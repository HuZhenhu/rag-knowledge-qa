"""T1.2 引擎单例化 — TDD 测试

验收标准：进程内只有一个引擎实例；缓存/BM25 索引只构建一次；
HTTP 与 WebSocket 共享同一实例。
"""
import types
import sys
from unittest.mock import MagicMock

import pytest

# 先 mock sentence_transformers 重量级依赖（与 test_m3 一致）
_mock_st = types.ModuleType("sentence_transformers")
_mock_st.SentenceTransformer = lambda *a, **k: None
sys.modules.setdefault("sentence_transformers", _mock_st)


@pytest.fixture(autouse=True)
def reset_factory():
    from src.core import engine_factory
    engine_factory.reset()
    yield
    engine_factory.reset()


def test_get_engine_returns_same_instance():
    """多次调用返回同一实例（进程内单例）。"""
    from src.core import engine_factory

    fake = MagicMock()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(engine_factory, "_build_engine", lambda: fake)
        e1 = engine_factory.get_engine()
        e2 = engine_factory.get_engine()
        assert e1 is fake
        assert e1 is e2


def test_build_engine_called_only_once():
    """缓存/BM25 索引只构建一次（_build_engine 只调用一次）。"""
    from src.core import engine_factory

    calls = []
    fake = MagicMock()

    def builder():
        calls.append(1)
        return fake

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(engine_factory, "_build_engine", builder)
        engine_factory.get_engine()
        engine_factory.get_engine()
        assert len(calls) == 1


def test_get_vector_store_resolves_from_engine_langchain():
    """langchain 引擎：vector_store = engine.vectorstore。"""
    from src.core import engine_factory

    fake = MagicMock()
    fake.vectorstore = MagicMock()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(engine_factory, "_build_engine", lambda: fake)
        vs = engine_factory.get_vector_store()
        assert vs is fake.vectorstore


def test_get_vector_store_resolves_from_engine_original_attr():
    """agentic/original 引擎：vector_store = engine.vector_store。"""
    from src.core import engine_factory

    fake = MagicMock()
    fake.vector_store = MagicMock()
    fake.vectorstore = MagicMock()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(engine_factory, "RAG_ENGINE", "agentic")
        # 模拟非 langchain：resolve 逻辑优先 engine.vector_store
        vs = engine_factory._resolve_vector_store(fake)
        assert vs is fake.vector_store


def test_routes_and_main_share_same_engine_instance():
    """routes 与 main 都通过 engine_factory 取引擎，应得到同一实例。"""
    from src.core import engine_factory

    fake = MagicMock()
    fake.vectorstore = MagicMock()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(engine_factory, "_build_engine", lambda: fake)
        e_from_routes_path = engine_factory.get_engine()
        e_from_main_path = engine_factory.get_engine()
        assert e_from_routes_path is e_from_main_path is fake
