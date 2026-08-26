"""T3.1 模型分级 — 意图分类 + 复杂度评估 → 小模型/低温度 / 大模型/缓存优先。

覆盖：
- 简单事实问题 → small tier + 低温度（低成本小模型）
- 复杂问题（多跳/对比/推理）→ large tier（大模型）
- 闲聊/反馈意图 → 不触发 RAG 生成（requires_llm=False），仍走小模型
- 缓存优先：cache_hit=True 时决策为 cache，无需生成
- 开关关闭（enabled=False）→ 保持原行为（large/default 温度）
- 延迟 / 成本收益估算（模拟批次）：小模型便宜且快，验收延迟降 >=20%、成本降 >=30%
- 引擎集成：LangChainRAGEngine 按决策选择模型/温度（mock 底层 LLM/Embedding/Chroma）
"""
from unittest.mock import MagicMock, PropertyMock, patch

import pytest


# ---------------- 基础路由 ----------------

def test_simple_factual_question_routes_to_small_model():
    from src.core.model_router import ModelRouter
    router = ModelRouter(small_model="deepseek-chat", large_model="deepseek-reasoner",
                         enabled=True)
    d = router.decide("什么是RAG？")
    assert d.complexity == "simple"
    assert d.tier == "small"
    assert d.model == "deepseek-chat"
    assert d.requires_llm is True
    assert d.temperature is not None and d.temperature <= 0.1


def test_complex_question_routes_to_large_model():
    from src.core.model_router import ModelRouter
    router = ModelRouter(small_model="deepseek-chat", large_model="deepseek-reasoner",
                         enabled=True)
    d = router.decide("对比RAG与微调的区别，并分析在企业客服场景各自的适用流程和成本")
    assert d.complexity == "complex"
    assert d.tier == "large"
    assert d.model == "deepseek-reasoner"


def test_chitchat_intent_does_not_require_rag_llm():
    from src.core.model_router import ModelRouter
    router = ModelRouter(enabled=True)
    d = router.decide("你好")
    assert d.intent == "chitchat" or d.requires_llm is False


def test_cache_hit_short_circuits_to_cache_tier():
    from src.core.model_router import ModelRouter
    router = ModelRouter(enabled=True)
    d = router.decide("什么是RAG？", cache_hit=True)
    assert d.tier == "cache"
    assert d.requires_llm is False


def test_disabled_router_preserves_legacy_behavior():
    from src.core.model_router import ModelRouter
    router = ModelRouter(enabled=False, default_temperature=0.0)
    d = router.decide("解释一下什么是缓存淘汰策略")
    # 关闭时不做分级，保持 legacy：单一模型、默认温度
    assert d.enabled is False
    assert d.model == router.large_model
    assert d.temperature == 0.0


# ---------------- 延迟 / 成本收益估算 ----------------

def test_latency_and_cost_reduction_meet_targets():
    """模拟一批客服问题（70% 简单 / 30% 复杂）：
    简单走小模型（latency 100ms, cost 0.2），复杂走大模型（latency 500ms, cost 1.0）。
    基线全走大模型。断言延迟降 >=20%、成本降 >=30%。
    """
    from src.core.model_router import ModelRouter

    router = ModelRouter(small_model="s", large_model="l", enabled=True)
    questions = [
        "怎么申请报销", "休假政策是什么", "如何修改密码", "打印机怎么连",
        "出差审批流程", "年假有几天", "工资条怎么看", "会议室怎么预定",
        "对比两种方案的差异并分析落地步骤", "设计一套多租户隔离方案并评估成本",
    ]  # 7 简单 / 3 复杂（按复杂度评估）

    simple = 0
    complex_ = 0
    for q in questions:
        d = router.decide(q)
        simple += d.complexity == "simple"
        complex_ += d.complexity == "complex"

    n = len(questions)
    assert simple >= 6, f"简单问题应占多数，got simple={simple}"
    assert complex_ >= 2

    LARGE_LAT, LARGE_COST = 500, 1.0
    SMALL_LAT, SMALL_COST = 100, 0.2

    baseline_lat = n * LARGE_LAT
    baseline_cost = n * LARGE_COST
    routed_lat = simple * SMALL_LAT + complex_ * LARGE_LAT
    routed_cost = simple * SMALL_COST + complex_ * LARGE_COST

    lat_reduction = (baseline_lat - routed_lat) / baseline_lat
    cost_reduction = (baseline_cost - routed_cost) / baseline_cost
    assert lat_reduction >= 0.20, f"延迟降幅 {lat_reduction:.1%} < 20%"
    assert cost_reduction >= 0.30, f"成本降幅 {cost_reduction:.1%} < 30%"


# ---------------- 引擎集成（mock 底层） ----------------

from langchain_core.runnables import Runnable


class _FakeLLM(Runnable):
    """模拟 ChatOpenAI：记录 (model, temperature) 供断言，返回固定回答。

    继承 Runnable 以保证 `| self.llm` 链式组合可用。
    """

    def __init__(self, model="deepseek-chat", temperature=0.0, **kwargs):
        self.model = model
        self.temperature = temperature
        self.calls = []

    def bind(self, **kwargs):
        return self

    def invoke(self, prompt, config=None):
        self.calls.append(prompt)
        return "基于知识库的回答[1]"

    def stream(self, prompt, config=None):
        self.calls.append(prompt)
        yield "基于知识库的回答[1]"

    def batch(self, inputs):
        return [self.invoke(x) for x in inputs]

    def ainvoke(self, *args, **kwargs):
        from unittest.mock import AsyncMock
        return AsyncMock(return_value=self.invoke(*args, **kwargs))()

    def abatch(self, inputs):
        from unittest.mock import AsyncMock
        return AsyncMock(return_value=self.batch(inputs))()

    def astream(self, *args, **kwargs):
        async def _gen():
            for c in self.stream(*args, **kwargs):
                yield c
        return _gen()


def _build_engine_with_router(enable_router=True):
    """构造 LangChainRAGEngine，mock 掉 LLM / Embedding / Chroma / BM25。"""
    with patch("src.core.langchain_rag.ChatOpenAI", side_effect=_FakeLLM) as m_chat, \
         patch("src.core.langchain_rag.HuggingFaceEmbeddings") as m_emb, \
         patch("src.core.langchain_rag.Chroma") as m_chroma:
        m_emb.return_value = MagicMock(embed_query=lambda x: [0.1] * 8,
                                       embed_documents=lambda x: [[0.1] * 8] * len(x))
        store = MagicMock()
        store.get.return_value = {"documents": ["d1", "d2"], "metadatas": [{"doc_id": "1"}, {"doc_id": "2"}]}
        store.add_texts.return_value = None
        store.similarity_search_with_score.return_value = []
        m_chroma.return_value = store

        from src.core.model_router import ModelRouter
        from src.core import langchain_rag
        router = ModelRouter(small_model="deepseek-chat", large_model="deepseek-reasoner",
                             enabled=enable_router, default_temperature=0.0)
        engine = langchain_rag.LangChainRAGEngine(use_hybrid=False)
        engine.model_router = router
        engine.enable_model_router = enable_router
        # 替换引擎 LLM 为可记录调用的假 LLM
        engine.llm = _FakeLLM(model="deepseek-reasoner", temperature=0.0)
        return engine


def test_engine_selects_small_llm_for_simple_question():
    engine = _build_engine_with_router(enable_router=True)
    llm, decision = engine._select_llm("什么是RAG？")
    assert decision.complexity == "simple"
    assert decision.tier == "small"
    assert llm.model == "deepseek-chat"
    assert llm.temperature <= 0.1


def test_engine_selects_large_llm_for_complex_question():
    engine = _build_engine_with_router(enable_router=True)
    llm, decision = engine._select_llm("对比RAG与微调的区别并分析落地成本")
    assert decision.complexity == "complex"
    assert decision.tier == "large"
    assert llm.model == "deepseek-reasoner"


def test_engine_disabled_router_keeps_default_llm():
    engine = _build_engine_with_router(enable_router=False)
    llm, decision = engine._select_llm("什么是RAG？")
    assert decision.enabled is False
    assert llm is engine.llm
