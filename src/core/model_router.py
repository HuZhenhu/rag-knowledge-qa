"""T3.1 模型分级 — 意图分类 + 复杂度评估 → 小模型/低温度 / 大模型/缓存优先。

设计目标（验收）：
- 简单事实问题走小模型 + 低温度（latency 低、单价低）
- 复杂问题（多跳 / 对比 / 推理）走大模型，保证准确率不降
- 缓存优先：cache_hit=True 时决策为 cache，跳过生成
- 闲聊 / 反馈意图不触发 RAG 生成
- 灰度开关 MODEL_ROUTER_ENABLED 默认关，关闭时保持 legacy 行为（单模型 + 默认温度）

实现说明：
- 意图分类：本地关键词快路径（复用 intent_classifier 语义）；可选注入 LLM 分类器增强
- 复杂度评估：启发式规则（长度 + 复杂关键词），全本地、无外部依赖
"""
from __future__ import annotations

from dataclasses import dataclass

from src.config import (
    MODEL_ROUTER_SMALL_MODEL,
    MODEL_ROUTER_LARGE_MODEL,
    MODEL_ROUTER_SMALL_TEMPERATURE,
    MODEL_ROUTER_MAX_TOKENS_SMALL,
    MODEL_ROUTER_MAX_TOKENS_LARGE,
    MODEL_ROUTER_ENABLED,
)


@dataclass
class RouterDecision:
    """模型分级决策结果。"""
    intent: str                      # query / followup / chitchat / feedback
    complexity: str                  # simple / complex / ""（cache / disabled）
    tier: str                        # small / large / cache
    model: str | None                # 命中缓存或 disabled 时为 None
    temperature: float | None = None
    max_tokens: int | None = None
    requires_llm: bool = True        # 缓存命中 / 闲聊反馈等场景为 False
    enabled: bool = True
    reason: str = ""


class ModelRouter:
    """模型分级路由器：意图分类 + 复杂度评估 → 模型 / 温度 / 是否生成。"""

    _COMPLEX_KEYWORDS = (
        "对比", "比较", "异同", "区别", "论证", "分析", "为什么", "如何",
        "总结", "详细", "展开", "多个", "维度", "结合", "设计", "方案",
        "规划", "流程", "步骤", "哪些", "分别", "关系", "影响", "建议",
    )
    _CHITCHAT_KEYWORDS = {
        "你好", "你是谁", "谢谢", "好的", "明白了", "再见",
        "请问你", "帮我", "讲个笑话",
    }
    _FEEDBACK_KEYWORDS = {
        "不对", "错误", "不准确", "没用", "这个回答", "你说的不对",
        "回答错了", "不太对", "不是这样",
    }

    def __init__(
        self,
        classifier=None,
        small_model: str | None = None,
        large_model: str | None = None,
        small_temperature: float | None = None,
        default_temperature: float | None = None,
        max_tokens_small: int | None = None,
        max_tokens_large: int | None = None,
        enabled: bool | None = None,
    ) -> None:
        self.classifier = classifier  # 可选：IntentClassifier（LLM 增强意图识别）
        self.small_model = small_model or MODEL_ROUTER_SMALL_MODEL
        self.large_model = large_model or MODEL_ROUTER_LARGE_MODEL
        self.small_temperature = (
            small_temperature if small_temperature is not None else MODEL_ROUTER_SMALL_TEMPERATURE
        )
        self.default_temperature = default_temperature if default_temperature is not None else 0.0
        self.max_tokens_small = int(max_tokens_small or MODEL_ROUTER_MAX_TOKENS_SMALL)
        self.max_tokens_large = int(max_tokens_large or MODEL_ROUTER_MAX_TOKENS_LARGE)
        self.enabled = MODEL_ROUTER_ENABLED if enabled is None else enabled

    # ---------------- 意图分类 ----------------
    def classify_intent(self, question: str) -> str:
        """本地关键词快路径 + 可选 LLM 分类器。返回 intent 名。"""
        q = question.strip()
        if any(kw in q for kw in self._FEEDBACK_KEYWORDS):
            return "feedback"
        if any(kw in q for kw in self._CHITCHAT_KEYWORDS):
            return "chitchat"
        if self.classifier is not None:
            try:
                return self.classifier.classify(q).intent.value
            except Exception:
                pass
        return "query"

    # ---------------- 复杂度评估 ----------------
    def assess_complexity(self, question: str) -> str:
        """启发式：短文本且不含复杂关键词 → simple；否则 complex。"""
        text = question.strip()
        if len(text) <= 60 and not any(kw in text for kw in self._COMPLEX_KEYWORDS):
            return "simple"
        return "complex"

    # ---------------- 决策 ----------------
    def decide(self, question: str, cache_hit: bool = False) -> RouterDecision:
        """给出本问题的模型 / 温度 / 是否生成决策。"""
        if not self.enabled:
            return RouterDecision(
                intent="query", complexity="", tier="large",
                model=self.large_model, temperature=self.default_temperature,
                max_tokens=self.max_tokens_large, requires_llm=True,
                enabled=False, reason="disabled",
            )
        if cache_hit:
            return RouterDecision(
                intent="query", complexity="", tier="cache",
                model=None, temperature=None, max_tokens=None,
                requires_llm=False, enabled=True, reason="cache_first",
            )
        intent = self.classify_intent(question)
        complexity = self.assess_complexity(question)
        if intent in ("chitchat", "feedback"):
            # 不触发 RAG 生成（requires_llm=False）；若需轻量对话也用小模型
            return RouterDecision(
                intent=intent, complexity=complexity, tier="small",
                model=self.small_model, temperature=self.small_temperature,
                max_tokens=self.max_tokens_small, requires_llm=False,
                enabled=True, reason=f"intent:{intent}",
            )
        if complexity == "simple":
            return RouterDecision(
                intent=intent, complexity="simple", tier="small",
                model=self.small_model, temperature=self.small_temperature,
                max_tokens=self.max_tokens_small, requires_llm=True,
                enabled=True, reason="simple_factual",
            )
        return RouterDecision(
            intent=intent, complexity="complex", tier="large",
            model=self.large_model, temperature=self.default_temperature,
            max_tokens=self.max_tokens_large, requires_llm=True,
            enabled=True, reason="complex_reasoning",
        )
