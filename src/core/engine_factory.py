"""T1.2 进程内引擎单例工厂（T3.4 架构收敛）。

消除 main.py 与 routes.py 各自初始化引擎造成的双实例问题：
HTTP 与 WebSocket 共享同一引擎实例，缓存 / BM25 索引只构建一次。

T3.4 收敛：三引擎（langchain / agentic / original）收敛为两引擎（langchain / agentic），
统一接口 BaseRAGEngine，original 已删除 —— RAG_ENGINE 仅支持 langchain / agentic，
传入 original 或未知值抛 ValueError（禁止静默回退到旧实现）。
引擎切换只改配置 src.config.RAG_ENGINE。
"""
from __future__ import annotations

from typing import Any

from src.config import RAG_ENGINE

_engine: Any = None
_vector_store: Any = None


def _get_engine_class(name: str):
    """按配置返回引擎类（langchain / agentic）。

    Raises:
        ValueError: 引擎名不在支持集合内（含已删除的 original）。
    """
    if name == "langchain":
        from src.core.langchain_rag import LangChainRAGEngine
        return LangChainRAGEngine
    if name == "agentic":
        from src.core.agentic import AgenticEngine
        return AgenticEngine
    raise ValueError(
        f"未知 RAG 引擎: {name!r}；仅支持 langchain / agentic（original 已并入 langchain 收敛）"
    )


def _build_engine() -> Any:
    """根据 RAG_ENGINE 配置构建引擎实例（仅在首次调用时执行）。"""
    return _get_engine_class(RAG_ENGINE)()


def _resolve_vector_store(engine: Any) -> Any:
    """从引擎实例解析向量库后端（按统一 engine_name 属性路由）。

    仅接受合法引擎名（langchain/agentic）；MagicMock 或未实现
    engine_name 的对象回退到配置 RAG_ENGINE，保证单测兼容。
    """
    name = getattr(engine, "engine_name", None)
    if name not in ("langchain", "agentic"):
        name = RAG_ENGINE
    if name == "langchain":
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
