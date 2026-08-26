"""P1-3 检索/语义缓存接入默认引擎 单元测试"""
from types import SimpleNamespace

import pytest

from src.core.query_cache import QueryCache
from src.core.semantic_cache import SemanticCache
from src.core.langchain_rag import LangChainRAGEngine


# ---------- QueryCache（精确缓存） ----------

def test_query_cache_set_get():
    c = QueryCache(maxsize=16, ttl=300)
    assert c.get("q", 5, "none") is None
    c.set("q", 5, {"answer": "A"}, "none")
    assert c.get("q", 5, "none") == {"answer": "A"}


def test_query_cache_acl_isolation():
    """key 含 acl_fp：同 query 不同 ACL 指纹互不命中，防跨权限泄露"""
    c = QueryCache(maxsize=16, ttl=300)
    c.set("q", 5, {"answer": "A"}, "fp_a")
    assert c.get("q", 5, "fp_a") == {"answer": "A"}
    assert c.get("q", 5, "fp_b") is None
    assert c.get("q", 6, "fp_a") is None  # top_k 不同不命中


def test_query_cache_clear():
    c = QueryCache(maxsize=16, ttl=300)
    c.set("q", 5, {"answer": "A"}, "none")
    c.clear()
    assert c.get("q", 5, "none") is None


# ---------- SemanticCache（语义缓存） ----------

def _embed(v):
    return [1.0, 0.0, 0.0] if v else [0.0, 1.0, 0.0]


def test_semantic_cache_hit_and_miss(tmp_path):
    sc = SemanticCache(str(tmp_path / "sc.db"), threshold=0.85)
    sc.set("远程办公政策", "政策答案", [{"content": "c"}], "fp1", lambda t: [1.0, 0.0])
    hit = sc.get("远程办公政策具体内容", "fp1", lambda t: [0.95, 0.3])
    assert hit is not None
    assert hit[0] == "政策答案"
    # 不相关 → 未命中
    miss = sc.get("今天天气怎么样", "fp1", lambda t: [0.0, 1.0])
    assert miss is None


def test_semantic_cache_acl_isolation(tmp_path):
    """不同 acl_fp 语义缓存不互串"""
    sc = SemanticCache(str(tmp_path / "sc.db"), threshold=0.85)
    sc.set("机密政策", "机密答案", [{"content": "c"}], "fp_admin", lambda t: [1.0, 0.0])
    assert sc.get("机密政策", "fp_user", lambda t: [1.0, 0.0]) is None
    assert sc.get("机密政策", "fp_admin", lambda t: [1.0, 0.0]) is not None


def test_semantic_cache_clear(tmp_path):
    sc = SemanticCache(str(tmp_path / "sc.db"), threshold=0.85)
    sc.set("q", "a", [{"content": "c"}], "fp1", lambda t: [1.0, 0.0])
    sc.clear()
    assert sc.get("q", "fp1", lambda t: [1.0, 0.0]) is None


def test_clear_all_caches(monkeypatch):
    """clear_all_caches 同时清空检索缓存与语义缓存"""
    from src.core import semantic_cache as sc_mod
    from src.core import query_cache as qc_mod
    qc = QueryCache(maxsize=16, ttl=300)
    qc.set("q", 5, {"answer": "A"}, "none")
    qc2 = QueryCache(maxsize=16, ttl=300)
    qc2.set("q", 5, {"answer": "A"}, "none")
    monkeypatch.setattr(sc_mod, "get_query_cache", lambda: qc)
    monkeypatch.setattr(sc_mod, "get_semantic_cache", lambda: qc2)
    sc_mod.clear_all_caches()
    assert qc.get("q", 5, "none") is None
    assert qc2.get("q", 5, "none") is None


# ---------- 引擎接入（精确/语义缓存命中短路） ----------

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
    eng.cache_domain = None
    eng.embeddings = SimpleNamespace(embed_query=lambda t: [1.0, 0.0])
    eng._last_confidence = None
    eng._last_rerank_ms = 0.0
    for k, v in attrs.items():
        setattr(eng, k, v)
    return eng


def test_engine_query_cache_hit_short_circuit():
    c = QueryCache(maxsize=16, ttl=300)
    c.set("q1", 5, {"answer": "CACHED", "sources": [], "usage": {},
                    "timing": {"total_ms": 1}, "confidence": 0.9}, "none")
    eng = _make_engine(None, query_cache=c)
    resp = eng.query("q1", top_k=5)
    assert resp.answer == "CACHED"
    assert resp.timing.get("cache_hit") is True
    assert resp.confidence == 0.9


def test_engine_semantic_cache_hit_short_circuit(tmp_path):
    sc = SemanticCache(str(tmp_path / "sc.db"), threshold=0.85)
    sc.set("远程办公政策", "语义答案", [{"content": "c"}], "none", lambda t: [1.0, 0.0])
    eng = _make_engine(None, semantic_cache=sc)
    resp = eng.query("远程办公政策", top_k=5)
    assert resp.answer == "语义答案"
    assert resp.timing.get("semantic") is True
