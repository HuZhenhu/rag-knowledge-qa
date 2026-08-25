"""T1.2 进程内引擎单例工厂。

消除 main.py 与 routes.py 各自初始化引擎造成的双实例问题：
HTTP 与 WebSocket 共享同一引擎实例，缓存 / BM25 索引只构建一次。
三引擎（langchain / agentic / original）统一在此初始化，按 RAG_ENGINE 配置切换。
"""
from __future__ import annotations

from typing import Any

from src.config import RAG_ENGINE, USE_QUERY_EXPANSION, USE_HYDE, USE_RERANKER

_engine: Any = None
_vector_store: Any = None


def _build_engine() -> Any:
    """根据 RAG_ENGINE 配置构建引擎实例（仅在首次调用时执行）。"""
    if RAG_ENGINE == "langchain":
        from src.core.langchain_rag import LangChainRAGEngine
        return LangChainRAGEngine()
    if RAG_ENGINE == "agentic":
        from src.core.agentic import AgenticEngine
        return AgenticEngine()
    from src.core.rag_engine import RAGEngine
    return RAGEngine(
        use_query_expansion=USE_QUERY_EXPANSION,
        use_hyde=USE_HYDE,
        use_reranker=USE_RERANKER,
    )


def _resolve_vector_store(engine: Any) -> Any:
    """从引擎实例解析向量库后端（三引擎属性名不同）。"""
    if RAG_ENGINE == "langchain":
        return engine.vectorstore
    return engine.vector_store


def get_engine() -> Any:
    """返回进程内唯一的 RAG 引擎实例（懒加载单例）。"""
    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def get_vector_store() -> Any:
    """返回与引擎关联的向量库实例（与 get_engine 绑定，只构建一次）。"""
    global _vector_store
    if _vector_store is None:
        _vector_store = _resolve_vector_store(get_engine())
    return _vector_store


def reset() -> None:
    """重置单例（主要用于测试隔离）。"""
    global _engine, _vector_store
    _engine = None
    _vector_store = None
