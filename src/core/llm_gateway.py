"""T2.4 LLM 网关：多 provider 路由 / 令牌桶限流 / 成本统计 / 故障转移 / 模型分级。

- BaseProvider / MockProvider / OpenAICompatProvider / AnthropicProvider
- CostTracker: 成本统计（provider / 租户 / 模型维度可查，线程安全）
- LLMGateway:
    * 多 provider 顺序路由（配置优先级，主 provider 故障自动切换备用）
    * 每 provider 令牌桶限流（超限降级，不抛错）
    * 模型分级：简单问题优先走小模型（低成本），复杂问题走大模型
    * 全 provider 失败返回降级文案
- 生产 provider 用 openai / anthropic SDK，支持注入 client（测试 mock）

灰度开关 LLM_GATEWAY_ENABLED 默认关，未启用时不替换现有 LLM 调用链。
"""
from __future__ import annotations

import re
import threading
import time
from abc import ABC, abstractmethod
from typing import Any

from src.core.llm_guard import TokenBucket


# ======================================================================
# Provider 抽象与实现
# ======================================================================

class BaseProvider(ABC):
    """LLM provider 抽象。generate 返回 (answer, usage)。"""

    name: str = "base"
    model: str = ""
    tier: str = "large"  # small（低成本小模型）/ large（大模型）

    @abstractmethod
    def generate(self, messages: list[dict], model: str | None = None,
                 temperature: float | None = None,
                 max_tokens: int | None = None) -> tuple[str, dict]:
        """返回 (回答文本, usage)。usage 含 prompt_tokens / completion_tokens / total_tokens。"""


class MockProvider(BaseProvider):
    """本地 mock provider（测试 / 无外部依赖降级）。fail=True 时模拟故障。"""

    def __init__(self, name: str = "mock", model: str = "mock-model",
                 response: str = "mock answer", tier: str = "large",
                 fail: bool = False, prompt_tokens: int = 10,
                 completion_tokens: int = 5, delay: float = 0.0) -> None:
        self.name = name
        self.model = model
        self.tier = tier
        self._response = response
        self._fail = fail
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.delay = delay

    def generate(self, messages, model=None, temperature=None, max_tokens=None):
        if self.delay:
            time.sleep(self.delay)
        if self._fail:
            raise ConnectionError(f"{self.name} provider unavailable")
        return (self._response, {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
        })


class OpenAICompatProvider(BaseProvider):
    """OpenAI 兼容 API（DeepSeek / 通义 / Moonshot / 本地 Ollama 等）。client 可注入。"""

    def __init__(self, name: str = "openai", api_key: str = "", base_url: str = "",
                 model: str = "deepseek-chat", tier: str = "large", client: Any = None) -> None:
        self.name = name
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.tier = tier
        self._client = client

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        return self._client

    def generate(self, messages, model=None, temperature=None, max_tokens=None):
        client = self._get_client()
        kwargs = {"model": model or self.model, "messages": messages}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        resp = client.chat.completions.create(**kwargs)
        answer = resp.choices[0].content
        usage = {
            "prompt_tokens": resp.usage.prompt_tokens,
            "completion_tokens": resp.usage.completion_tokens,
            "total_tokens": resp.usage.total_tokens,
        }
        return answer, usage


class AnthropicProvider(BaseProvider):
    """Anthropic Claude provider。client 可注入。"""

    def __init__(self, name: str = "anthropic", api_key: str = "",
                 model: str = "claude-sonnet-4-20250514", tier: str = "large",
                 client: Any = None) -> None:
        self.name = name
        self.api_key = api_key
        self.model = model
        self.tier = tier
        self._client = client

    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    def generate(self, messages, model=None, temperature=None, max_tokens=None):
        client = self._get_client()
        system = "\n".join(m["content"] for m in messages if m.get("role") == "system")
        user_messages = [m for m in messages if m.get("role") != "system"]
        kwargs = {"model": model or self.model, "max_tokens": max_tokens or 1024,
                  "messages": user_messages}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if system:
            kwargs["system"] = system
        resp = client.messages.create(**kwargs)
        answer = "".join(getattr(b, "text", "") for b in resp.content)
        usage = {
            "prompt_tokens": getattr(resp.usage, "input_tokens", 0),
            "completion_tokens": getattr(resp.usage, "output_tokens", 0),
            "total_tokens": getattr(resp.usage, "input_tokens", 0) + getattr(resp.usage, "output_tokens", 0),
        }
        return answer, usage


# ======================================================================
# 成本统计
# ======================================================================

class CostTracker:
    """LLM 成本统计：按 provider / 租户 / 模型维度可查，线程安全。

    默认单价 0.5 USD / 1K tokens（可经 PRICING[provider] 覆盖）。
    """

    DEFAULT_PROMPT_PRICE = 0.5
    DEFAULT_COMPLETION_PRICE = 0.5
    PRICING: dict[str, tuple[float, float]] = {}

    def __init__(self) -> None:
        self._rows: list[dict] = []
        self._lock = threading.Lock()

    def record(self, provider: str, tenant_id: str, model: str,
               prompt_tokens: int, completion_tokens: int,
               prompt_price: float | None = None,
               completion_price: float | None = None) -> None:
        pp, cp = self.PRICING.get(provider, (self.DEFAULT_PROMPT_PRICE, self.DEFAULT_COMPLETION_PRICE))
        pp = self.DEFAULT_PROMPT_PRICE if prompt_price is None else prompt_price
        cp = self.DEFAULT_COMPLETION_PRICE if completion_price is None else completion_price
        cost = prompt_tokens / 1000 * pp + completion_tokens / 1000 * cp
        with self._lock:
            self._rows.append({
                "provider": provider, "tenant_id": tenant_id, "model": model,
                "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                "cost_usd": cost, "ts": time.time(),
            })

    def summary(self, provider: str | None = None, tenant_id: str | None = None,
                model: str | None = None) -> dict:
        """按维度过滤汇总：requests / prompt_tokens / completion_tokens / cost_usd。"""
        with self._lock:
            rows = self._rows
        agg = {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
               "cost_usd": 0.0}
        for r in rows:
            if provider is not None and r["provider"] != provider:
                continue
            if tenant_id is not None and r["tenant_id"] != tenant_id:
                continue
            if model is not None and r["model"] != model:
                continue
            agg["requests"] += 1
            agg["prompt_tokens"] += r["prompt_tokens"]
            agg["completion_tokens"] += r["completion_tokens"]
            agg["total_tokens"] += r["prompt_tokens"] + r["completion_tokens"]
            agg["cost_usd"] += r["cost_usd"]
        return agg

    def clear(self) -> None:
        with self._lock:
            self._rows.clear()


# ======================================================================
# LLM 网关
# ======================================================================

class LLMGateway:
    """LLM 网关：路由 + 限流 + 故障转移 + 模型分级 + 成本统计。

    generate() 返回 (answer, meta)：
        meta = {provider, model, status, complexity, failed, reason, prompt_tokens, completion_tokens}
    status: ok / degraded（全 provider 失败或限流）
    """

    PROMPT_PRICE = 0.5  # USD / 1K tokens（简单/小模型也可按 provider 单独定价）
    COMPLETION_PRICE = 0.5

    _COMPLEX_KEYWORDS = (
        "对比", "比较", "异同", "区别", "论证", "分析", "为什么", "如何",
        "总结", "详细", "展开", "多个", "维度", "结合", "设计", "方案", "规划",
    )

    def __init__(self, providers: list[BaseProvider], cost_tracker: CostTracker | None = None,
                 rate_rpm: float | None = None, fallback_text: str = "知识库中未找到相关信息") -> None:
        if not providers:
            raise ValueError("至少需要一个 provider")
        self.providers = providers
        self._cost = cost_tracker if cost_tracker is not None else CostTracker()
        self._buckets: dict[str, TokenBucket] = {}
        self._rate_rpm = rate_rpm
        if rate_rpm:
            for p in providers:
                self._buckets[p.name] = TokenBucket(capacity=float(rate_rpm),
                                                    refill_rate=float(rate_rpm) / 60.0)
        self.fallback_text = fallback_text

    # ---- 模型分级 ----
    def classify_complexity(self, text: str) -> str:
        """简单规则分级：文本短且不含复杂关键词 -> simple；否则 complex。"""
        if len(text) < 80 and not any(kw in text for kw in self._COMPLEX_KEYWORDS):
            return "simple"
        return "complex"

    def _candidates(self, complexity: str) -> list[BaseProvider]:
        """按复杂度排序候选 provider：simple 优先 small，complex 优先 large。"""
        small = [p for p in self.providers if p.tier == "small"]
        large = [p for p in self.providers if p.tier != "small"]
        if complexity == "simple":
            return small + large
        return large + small

    # ---- 核心调用 ----
    def generate(self, messages: list[dict], tenant_id: str = "default",
                 complexity: str | None = None, temperature: float | None = None,
                 max_tokens: int | None = None) -> tuple[str, dict]:
        user_text = " ".join(m.get("content", "") for m in messages if m.get("role") == "user")
        if complexity is None:
            complexity = self.classify_complexity(user_text)
        candidates = self._candidates(complexity)
        failed: list[str] = []
        for p in candidates:
            bucket = self._buckets.get(p.name)
            if bucket is not None and not bucket.acquire(1.0):
                failed.append(p.name)
                continue
            try:
                answer, usage = p.generate(messages, temperature=temperature,
                                           max_tokens=max_tokens)
            except Exception:
                failed.append(p.name)
                continue
            self._cost.record(
                provider=p.name, tenant_id=tenant_id, model=p.model,
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                prompt_price=self.PROMPT_PRICE, completion_price=self.COMPLETION_PRICE,
            )
            meta = {
                "provider": p.name, "model": p.model, "status": "ok",
                "complexity": complexity, "failed": failed, "reason": None,
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
            }
            return answer, meta

        # 全部失败：可能因限流（failed 全是被限流的）或故障
        reason = "rate_limited" if failed and all(
            self._buckets.get(n) is not None for n in failed) else "all_providers_failed"
        meta = {
            "provider": None, "model": None, "status": "degraded",
            "complexity": complexity, "failed": failed, "reason": reason,
            "prompt_tokens": 0, "completion_tokens": 0,
        }
        return self.fallback_text, meta

    def cost_summary(self, provider: str | None = None, tenant_id: str | None = None,
                     model: str | None = None) -> dict:
        return self._cost.summary(provider=provider, tenant_id=tenant_id, model=model)
