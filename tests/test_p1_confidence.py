"""P1-4/P1-6 延迟收敛辅助逻辑 与 置信度门控 单元测试"""
from types import SimpleNamespace

from src.core.langchain_rag import LangChainRAGEngine
from src.api.schemas import QueryResponse


# ---------- P1-4 _should_skip_hyde（简单问题跳过 HyDE 条件化降级） ----------

def _eng(**attrs):
    eng = object.__new__(LangChainRAGEngine)
    eng.hyde_skip_simple = True
    for k, v in attrs.items():
        setattr(eng, k, v)
    return eng


def test_should_skip_hyde_simple_question():
    e = _eng()
    assert e._should_skip_hyde("RAG是什么") is True
    assert e._should_skip_hyde("RAG定义") is True


def test_should_skip_hyde_complex_question():
    e = _eng()
    # 多跳/对比/推理标记 → 不跳过（保召回）
    assert e._should_skip_hyde("RAG和LLM的区别是什么") is False
    assert e._should_skip_hyde("请详细介绍RAG的流程和步骤") is False
    assert e._should_skip_hyde("为什么需要重排序，以及如何实现") is False


def test_should_skip_hyde_disabled():
    e = _eng(hyde_skip_simple=False)
    assert e._should_skip_hyde("什么是RAG") is False


# ---------- P1-6 _compute_confidence（检索分数硬门控置信度） ----------

def test_compute_confidence_empty_sources():
    e = _eng(use_reranker=False, _reranker=None)
    assert e._compute_confidence([]) == 0.0


def test_compute_confidence_rerank_mode():
    """启用重排：bge-reranker sigmoid 分数直接取 Top-1"""
    e = _eng(use_reranker=True, _reranker=object())
    assert e._compute_confidence([{"score": 0.72}, {"score": 0.3}]) == 0.72
    assert e._compute_confidence([{"score": 1.05}]) == 1.0  # 封顶
    assert e._compute_confidence([{"score": -0.1}]) == 0.0  # 下限


def test_compute_confidence_rrf_mode():
    """未启用重排：RRF 分数量纲归一化（RRF_K=60 → 单路理论最大 1/61）"""
    e = _eng(use_reranker=False, _reranker=None)
    # 0.016 / (1/61) = 0.976 → 归一后 0.976
    assert e._compute_confidence([{"score": 0.016}]) == 0.976


# ---------- P1-3 _acl_fingerprint ----------

def test_acl_fingerprint_stable():
    e = _eng()
    # dict 键序不同 → 指纹一致（sort_keys）
    f1 = e._acl_fingerprint({"doc_id": {"$in": ["a", "b"]}, "kb_id": {"$eq": "kb1"}})
    f2 = e._acl_fingerprint({"kb_id": {"$eq": "kb1"}, "doc_id": {"$in": ["a", "b"]}})
    assert f1 == f2
    # 列表顺序不同（$in 语义有序）→ 指纹不同，属预期（不同授权集合）
    f3 = e._acl_fingerprint({"doc_id": {"$in": ["a", "b"]}})
    f4 = e._acl_fingerprint({"doc_id": {"$in": ["b", "a"]}})
    assert f3 != f4
    assert e._acl_fingerprint(None) == "none"


# ---------- P1-6 引擎门控：低置信拒答 ----------

def _make_engine(monkeypatch, **attrs):
    eng = object.__new__(LangChainRAGEngine)
    eng.top_k = 5
    eng.candidate_k = 20
    eng.use_query_correction = False
    eng.use_confidence_refuse = False
    eng.confidence_refuse_threshold = 0.35
    eng.acl_enforce = False
    eng.query_cache = None
    eng.semantic_cache = None
    eng.embeddings = SimpleNamespace(embed_query=lambda t: [1.0, 0.0])
    eng._last_confidence = None
    eng._last_rerank_ms = 0.0
    for k, v in attrs.items():
        setattr(eng, k, v)
    return eng


def test_engine_confidence_gate_refuses(monkeypatch):
    """低置信且有来源 → 拒答（不进入生成）"""
    eng = _make_engine(monkeypatch, use_confidence_refuse=True,
                       confidence_refuse_threshold=0.35)
    eng._last_confidence = 0.1
    monkeypatch.setattr(eng, "_retrieve_multi",
                        lambda q, ck, acl_filter=None: [("d", 0.01)])
    monkeypatch.setattr(eng, "_build_sources",
                        lambda q, docs, k: [{"content": "c",
                                             "metadata": {"source_file": "s.md"},
                                             "score": 0.1}])
    monkeypatch.setattr(eng, "_resolve_parent", lambda s: s)
    resp = eng.query("低置信问题", top_k=5)
    assert resp.answer == "知识库中未找到相关信息"
    assert resp.confidence == 0.1


def test_engine_confidence_gate_disabled_by_default():
    """开关默认关闭：低置信也不拒答（不构造检索，仅验证 default=False）"""
    e = _make_engine(None)
    assert e.use_confidence_refuse is False


# ---------- API 层透出 confidence ----------

def test_query_response_confidence_field():
    r = QueryResponse(request_id="r1", answer="a",
                      confidence=0.72)
    assert r.confidence == 0.72


def test_query_response_confidence_default_none():
    r = QueryResponse(request_id="r1", answer="a")
    assert r.confidence is None
