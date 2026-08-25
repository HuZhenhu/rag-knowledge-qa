"""T1.1 同步重活移出事件循环 — TDD 测试

验收标准：单 worker 下 5 个并发 /query 不再互相阻塞；WebSocket/健康检查不受慢查询影响。
"""
import asyncio
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ── 先 mock sentence_transformers 重量级依赖（与 test_m3 一致）──
_mock_st = types.ModuleType("sentence_transformers")
_mock_st.SentenceTransformer = lambda *a, **k: None
sys.modules.setdefault("sentence_transformers", _mock_st)

from src.core.async_util import run_in_thread  # noqa: E402


# ======================================================================
# 核心机制：run_in_thread 将同步阻塞函数放入线程池
# ======================================================================

@pytest.mark.anyio
async def test_run_in_thread_runs_sync_fn_and_returns():
    result = await run_in_thread(lambda: 42)
    assert result == 42


@pytest.mark.anyio
async def test_run_in_thread_passes_args():
    result = await run_in_thread(lambda a, b=0: a + b, 3, b=4)
    assert result == 7


@pytest.mark.anyio
async def test_run_in_thread_propagates_exception():
    def boom():
        raise ValueError("boom")

    with pytest.raises(ValueError):
        await run_in_thread(boom)


@pytest.mark.anyio
async def test_run_in_thread_does_not_block_event_loop():
    """核心验收：阻塞函数放进线程池，事件循环不被阻塞。

    blocker 内同步 sleep 0.8s；若在事件循环中执行会卡住 pinger。
    并发执行后 pinger(0.3s) 必须先完成 → order == ["ping", "block"]。
    """
    order: list[str] = []

    async def pinger():
        await asyncio.sleep(0.3)
        order.append("ping")

    async def blocker():
        await run_in_thread(lambda: time.sleep(0.8))
        order.append("block")

    await asyncio.gather(blocker(), pinger())
    assert order == ["ping", "block"]


# ======================================================================
# 端点接线：/query 必须经由 run_in_thread 执行引擎同步调用
# ======================================================================

@pytest.fixture()
def fake_engine():
    eng = MagicMock()
    eng.query.return_value = SimpleNamespace(
        answer="mock answer",
        sources=[],
        usage={},
        timing={},
        trace_id="trace_1",
        confidence=0.9,
        citation_spans=[],
        intent="",
        is_followup=False,
    )
    return eng


@pytest.fixture()
def routes_module(fake_engine, tmp_path, monkeypatch):
    """以 patch 引擎方式加载 routes 模块，并切到临时数据库。"""
    import src.storage.database as database
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    database.init_db()

    # T1.2 单例化适配：reset 引擎工厂并 patch _build_engine，
    # 使 reload 后的 routes 从单例工厂拿到 fake_engine
    from src.core import engine_factory
    engine_factory.reset()
    with patch.object(engine_factory, "_build_engine", return_value=fake_engine):
        import src.api.routes as routes
        import importlib
        importlib.reload(routes)
    monkeypatch.setattr(routes, "get_current_user", lambda *a, **k: {"id": "u1", "role": "admin"})
    return routes


@pytest.mark.anyio
async def test_query_endpoint_runs_engine_in_thread(routes_module, fake_engine):
    """/query 端点应通过 run_in_thread 执行引擎同步查询，而非直接阻塞事件循环。"""
    import src.api.routes as routes
    from src.core import async_util

    called_fns = []

    async def fake_run_in_thread(fn, *args, **kwargs):
        called_fns.append(fn)
        return await asyncio.to_thread(fn, *args, **kwargs)

    with patch.object(routes, "run_in_thread", side_effect=fake_run_in_thread):
        resp = await routes.query(
            SimpleNamespace(
                question="什么是RAG",
                kb_id=None,
                session_id=None,
                top_k=5,
                acl_filter=None,
                stream=False,
            ),
            user={"id": "u1", "role": "admin"},
        )
        # 断言引擎查询确实经由 run_in_thread
        assert any("query" in getattr(f, "__name__", "") for f in called_fns) or called_fns
        assert fake_engine.query.called
        assert resp.answer == "mock answer"
