"""T1.6 提交-推送模式 TDD 测试（红）

验收映射：
- /query 立即返回 task_id（异步模式开关开启时），不等待 RAG 计算
- 任务完成后 /tasks/{task_id} 可查询到结果（status=done + result）
- 任务完成后经 WebSocket /ws/tasks 推送 task_done（前端无需轮询）
- 保留同步模式开关：QUERY_ASYNC_MODE=False 时 /query 保持同步响应
"""
import asyncio
import json
import sys
import time
import types
import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ── 先 mock sentence_transformers 重量级依赖（与 test_p1_t11 一致）──
_mock_st = types.ModuleType("sentence_transformers")
_mock_st.SentenceTransformer = lambda *a, **k: None
sys.modules.setdefault("sentence_transformers", _mock_st)

from src.core.task_manager import TaskManager  # noqa: E402


# ======================================================================
# TaskManager：任务状态机 + 订阅推送
# ======================================================================

def test_task_manager_create_returns_pending():
    tm = TaskManager()
    tid = tm.create_task()
    assert tid.startswith("task_")
    t = tm.get(tid)
    assert t["status"] == "pending"
    assert t["result"] is None


def test_task_manager_complete_then_get_done():
    tm = TaskManager()
    tid = tm.create_task()
    tm.complete(tid, {"answer": "A"})
    t = tm.get(tid)
    assert t["status"] == "done"
    assert t["result"]["answer"] == "A"


def test_task_manager_fail_records_error():
    tm = TaskManager()
    tid = tm.create_task()
    tm.fail(tid, "boom")
    t = tm.get(tid)
    assert t["status"] == "error"
    assert "boom" in t["error"]


def test_task_manager_unknown_task_returns_none():
    tm = TaskManager()
    assert tm.get("task_nope") is None


def test_task_manager_subscribe_pushes_payload():
    tm = TaskManager()
    tid = tm.create_task()
    q = asyncio.Queue()
    tm.subscribe(tid, q)
    tm.complete(tid, {"answer": "A"})
    payload = q.get_nowait()
    assert payload["type"] == "task_done"
    assert payload["task_id"] == tid
    assert payload["result"]["answer"] == "A"


def test_task_manager_subscribe_only_target_task():
    tm = TaskManager()
    t1 = tm.create_task()
    t2 = tm.create_task()
    q = asyncio.Queue()
    tm.subscribe(t2, q)
    tm.complete(t1, {"answer": "x"})  # 完成别的任务不应推送
    assert q.empty()


def test_task_manager_unsubscribe_stops_push():
    tm = TaskManager()
    tid = tm.create_task()
    q = asyncio.Queue()
    tm.subscribe(tid, q)
    tm.unsubscribe(tid, q)
    tm.complete(tid, {"answer": "A"})
    assert q.empty()


def test_task_manager_cleanup_expired():
    tm = TaskManager(ttl_seconds=10)
    tid = tm.create_task()
    tm.complete(tid, {"answer": "A"})
    n = tm.cleanup_expired(now=time.time() + 11)
    assert n == 1
    assert tm.get(tid) is None


# ======================================================================
# 公共 fixture：patch 引擎后加载 routes
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
    """以 patch 引擎方式加载 routes，切临时数据库，并重置任务单例。

    认证：FastAPI 依赖在路由注册时捕获的是 get_current_user 函数对象引用，
    直接 monkeypatch routes 模块属性无效（HTTP 流程 401）。因此改为在临时库
    创建真实用户 + 生成 JWT token，请求携带 Authorization header。
    """
    import src.storage.database as database
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    database.init_db()

    from src.core import engine_factory
    from src.core.task_manager import reset as reset_task_manager
    engine_factory.reset()
    reset_task_manager()
    with patch.object(engine_factory, "_build_engine", return_value=fake_engine):
        import src.api.routes as routes
        importlib.reload(routes)
        from src.api.jwt_auth import create_access_token, hash_password
        from src.storage.database import create_user
        create_user("u1", "tester", hash_password("x"))
        routes._test_token = create_access_token("u1", "admin")
    return routes


class _AuthClient:
    """包装 TestClient，自动为每个请求附加 JWT Bearer header。"""

    def __init__(self, base, token):
        self._base = base
        self._token = token

    def _headers(self, headers=None):
        h = {"Authorization": f"Bearer {self._token}"}
        if headers:
            h.update(headers)
        return h

    def post(self, url, **kw):
        kw["headers"] = self._headers(kw.get("headers"))
        return self._base.post(url, **kw)

    def get(self, url, **kw):
        kw["headers"] = self._headers(kw.get("headers"))
        return self._base.get(url, **kw)


@pytest.fixture()
def client(routes_module):
    """构造只含本模块路由的 TestClient（不 import main，避免启动副作用）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(routes_module.router)
    app.include_router(routes_module.ws_router)
    return _AuthClient(TestClient(app, raise_server_exceptions=False), routes_module._test_token)


# ======================================================================
# /query 异步模式：立即返回 task_id
# ======================================================================

def test_query_async_mode_returns_task_id_immediately(client, routes_module,
                                                      monkeypatch, fake_engine):
    """异步模式下 /query 返回 task_id + pending，而非同步答案。

    注意：TestClient 的同步 portal 会把 asyncio.create_task 的后台任务带到完成，
    因此无法在单测层直接断言"耗时 < 引擎耗时"（真实 uvicorn 下 create_task 立即
    返回、不阻塞响应）。此处验证语义契约：响应携带 task_id、status=pending、
    answer 为空，且引擎查询确实被提交到后台执行。
    """
    monkeypatch.setattr(routes_module, "QUERY_ASYNC_MODE", True)

    def slow_query(*a, **k):
        time.sleep(0.5)
        return SimpleNamespace(
            answer="slow", sources=[], usage={}, timing={},
            trace_id="trace_s", confidence=1.0, citation_spans=[],
            intent="", is_followup=False,
        )

    fake_engine.query.side_effect = slow_query
    resp = client.post("/api/v1/query", json={"question": "什么是RAG", "top_k": 5})
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("task_id")  # 拿到 task_id（而非同步答案）
    assert data.get("status") == "pending"
    assert data.get("answer") == ""  # 答案走后端任务，不经响应体返回
    assert fake_engine.query.called  # 引擎查询已在后台被调用


def test_query_async_mode_task_reaches_done(client, routes_module, monkeypatch):
    """异步提交后，/tasks/{task_id} 可轮询到 done + result。"""
    monkeypatch.setattr(routes_module, "QUERY_ASYNC_MODE", True)
    resp = client.post("/api/v1/query", json={"question": "q", "top_k": 5})
    tid = resp.json()["task_id"]

    status = None
    for _ in range(100):
        r = client.get(f"/api/v1/tasks/{tid}")
        assert r.status_code == 200
        status = r.json()["status"]
        if status in ("done", "error"):
            break
        time.sleep(0.05)
    assert status == "done"
    assert r.json()["result"]["answer"] == "mock answer"


def test_query_async_mode_task_error_records_error(client, routes_module,
                                                   monkeypatch, fake_engine):
    """后台查询抛错 -> 任务状态为 error 并记录错误信息。"""
    monkeypatch.setattr(routes_module, "QUERY_ASYNC_MODE", True)
    fake_engine.query.side_effect = RuntimeError("engine down")
    resp = client.post("/api/v1/query", json={"question": "q", "top_k": 5})
    tid = resp.json()["task_id"]

    status = None
    for _ in range(100):
        r = client.get(f"/api/v1/tasks/{tid}")
        status = r.json()["status"]
        if status in ("done", "error"):
            break
        time.sleep(0.05)
    assert status == "error"
    assert "engine down" in r.json()["error"]


def test_query_async_mode_task_missing_404(client, routes_module, monkeypatch):
    monkeypatch.setattr(routes_module, "QUERY_ASYNC_MODE", True)
    resp = client.get("/api/v1/tasks/task_does_not_exist")
    assert resp.status_code == 404


# ======================================================================
# 同步模式开关：QUERY_ASYNC_MODE=False 保持现行为
# ======================================================================

def test_query_sync_mode_returns_answer_directly(client, routes_module):
    """默认（开关关闭）下 /query 直接返回答案，保持同步响应。"""
    resp = client.post("/api/v1/query", json={"question": "q", "top_k": 5})
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"] == "mock answer"
    assert data.get("task_id") is None


# ======================================================================
# WebSocket /ws/tasks：任务完成推送（复用 WS 通道）
# ======================================================================

class FakeWebSocket:
    """最小 WebSocket 替身：可预设 receive_text 序列，记录 send_json 输出。"""

    def __init__(self, msgs):
        self._msgs = list(msgs)
        self.sent = []
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def receive_text(self):
        if self._msgs:
            return self._msgs.pop(0)
        await asyncio.sleep(3600)  # 无更多输入时挂起，等待被取消

    async def send_json(self, obj):
        self.sent.append(obj)

    async def close(self, code=1000):
        pass


@pytest.mark.anyio
async def test_ws_tasks_push_on_complete(routes_module):
    """订阅 task_id 后，任务完成经 WebSocket 推送 task_done。"""
    from src.core.task_manager import get_task_manager
    tm = get_task_manager()
    tid = tm.create_task()
    fake_ws = FakeWebSocket([json.dumps({"type": "subscribe", "task_id": tid})])

    ws_task = asyncio.create_task(routes_module.ws_tasks(fake_ws))
    await asyncio.sleep(0.1)  # 让端点 accept + 完成订阅
    assert fake_ws.accepted is True

    tm.complete(tid, {"answer": "push answer"})
    await asyncio.sleep(0.1)  # 让 pump 消费并推送

    ws_task.cancel()
    try:
        await ws_task
    except asyncio.CancelledError:
        pass

    done = [m for m in fake_ws.sent if m.get("type") == "task_done"]
    assert done, f"未收到 task_done，实际发送: {fake_ws.sent}"
    assert done[0]["task_id"] == tid
    assert done[0]["result"]["answer"] == "push answer"
