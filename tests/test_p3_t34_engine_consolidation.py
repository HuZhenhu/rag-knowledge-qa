"""T3.4 架构收敛 — TDD 测试

验收标准：
- 三引擎收敛为统一接口（BaseRAGEngine），保留 langchain + agentic，删除 original；
- langchain-community 依赖迁移（langchain-huggingface + rank_bm25 自研 retriever）；
- 引擎切换只改配置（RAG_ENGINE），全部测试通过，无 sunset 依赖。
"""
import re
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# 1) 统一引擎接口
# ---------------------------------------------------------------------------

def test_base_engine_interface_exists():
    """存在统一接口基类，且 query/query_stream 为抽象方法。"""
    from src.core.base_engine import BaseRAGEngine
    assert isinstance(BaseRAGEngine, type)
    assert "query" in BaseRAGEngine.__abstractmethods__
    assert "query_stream" in BaseRAGEngine.__abstractmethods__


def test_langchain_engine_conforms_to_interface():
    """LangChainRAGEngine 是可实例化实现，具备 engine_name 与统一方法。"""
    from src.core.langchain_rag import LangChainRAGEngine
    assert LangChainRAGEngine.engine_name == "langchain"
    assert hasattr(LangChainRAGEngine, "query")
    assert hasattr(LangChainRAGEngine, "query_stream")


def test_agentic_engine_conforms_to_interface():
    """AgenticEngine 是可实例化实现，具备 engine_name 与统一方法。"""
    from src.core.agentic import AgenticEngine
    assert AgenticEngine.engine_name == "agentic"
    assert hasattr(AgenticEngine, "query")
    assert hasattr(AgenticEngine, "query_stream")


# ---------------------------------------------------------------------------
# 2) 引擎工厂只支持 langchain / agentic（original 已删除）
# ---------------------------------------------------------------------------

def test_factory_supports_langchain_and_agentic():
    from src.core import engine_factory
    assert engine_factory._get_engine_class("langchain").__name__ == "LangChainRAGEngine"
    assert engine_factory._get_engine_class("agentic").__name__ == "AgenticEngine"


@pytest.mark.parametrize("bad", ["original", "foo", "", "hybrid"])
def test_factory_rejects_unknown_engine(bad):
    """original 及任何未知引擎名 → ValueError（出厂不再支持）。"""
    from src.core import engine_factory
    with pytest.raises(ValueError):
        engine_factory._get_engine_class(bad)


def test_factory_with_original_config_raises():
    """RAG_ENGINE=original 时 _build_engine 抛错，避免静默回退到旧实现。"""
    from src.core import engine_factory
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(engine_factory, "RAG_ENGINE", "original")
        with pytest.raises(ValueError):
            engine_factory._build_engine()


def test_resolve_vector_store_uses_engine_name_attr():
    """向量库解析基于 engine_name 属性，而非硬编码引擎名列表。"""
    from src.core import engine_factory
    class FakeL:
        engine_name = "langchain"
        vectorstore = object()
        vector_store = object()
    class FakeA:
        engine_name = "agentic"
        vector_store = object()
    assert engine_factory._resolve_vector_store(FakeL()) is FakeL.vectorstore
    assert engine_factory._resolve_vector_store(FakeA()) is FakeA.vector_store


# ---------------------------------------------------------------------------
# 3) langchain-community 依赖迁移
# ---------------------------------------------------------------------------
SRC = Path(__file__).resolve().parent.parent / "src"


def test_no_langchain_community_import_in_langchain_rag():
    """langchain_rag.py 不再 import langchain_community（sunset 依赖）。"""
    text = (SRC / "core" / "langchain_rag.py").read_text(encoding="utf-8")
    assert "langchain_community" not in text


def test_huggingface_embeddings_uses_independent_package():
    """HuggingFaceEmbeddings 改用 langchain-huggingface 独立包。"""
    text = (SRC / "core" / "langchain_rag.py").read_text(encoding="utf-8")
    assert "from langchain_huggingface import HuggingFaceEmbeddings" in text


def test_custom_bm25_retriever_exists_and_works():
    """自研 BM25Retriever（基于 rank_bm25）替代 community 实现。"""
    from langchain_core.documents import Document
    from src.core.bm25_retriever import BM25Retriever

    texts = ["什么是Transformer 注意力 机制", "Python 异常处理 try except", "什么是 注意力 机制"]
    metas = [{"doc_id": "a"}, {"doc_id": "b"}, {"doc_id": "c"}]
    r = BM25Retriever.from_texts(texts, metadatas=metas, k=2)
    docs = r.invoke("什么是注意力机制")
    assert isinstance(docs, list) and len(docs) <= 2
    assert all(isinstance(d, Document) for d in docs)
    # 与"注意力机制"最相关的应排最前
    assert "注意力" in docs[0].page_content


def test_requirements_dropped_community_added_huggingface():
    reqs = (Path(__file__).resolve().parent.parent / "requirements.txt").read_text(encoding="utf-8")
    assert "langchain-huggingface" in reqs
    assert "langchain-community" not in reqs
