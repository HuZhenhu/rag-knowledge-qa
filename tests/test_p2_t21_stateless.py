"""T2.1 无状态化 TDD 测试（红）

验收映射：
- SessionManager 后端抽象：进程内 fallback（memory）+ Redis 实现
- 会话数据存 Redis：API 任意副本可处理任意请求（跨实例一致）
- 任务状态也存 Redis：重启副本/多副本下任务状态不丢失
- 扩容/缩容副本后会话与任务不丢失
"""
import fnmatch
import json
import time

import pytest

from src.core.session import Message, Session, SessionManager
from src.core.task_manager import TaskManager


# ---------- 工具：FakeRedis（不依赖真实 redis 服务/包） ----------

class FakeRedis:
    """最小 redis 客户端替身：get/set/delete/scan_iter/expire"""

    def __init__(self):
        self._d = {}

    def get(self, key):
        return self._d.get(key)

    def set(self, key, value):
        self._d[key] = value

    def delete(self, *keys):
        for k in keys:
            self._d.pop(k, None)

    def scan_iter(self, match="*"):
        for k in list(self._d):
            if fnmatch.fnmatchcase(k, match):
                yield k

    def expire(self, key, seconds):
        self._d.setdefault("__expires__", {})[key] = time.time() + seconds
        return True

    def dbsize(self):
        return len(self._d)


# ======================================================================
# SessionBackend 抽象：Memory 与 Redis 实现
# ======================================================================

def test_session_backend_memory_crud():
    from src.core.session_backend import MemorySessionBackend
    b = MemorySessionBackend()
    assert b.get("s1") is None
    b.set("s1", {"session_id": "s1", "messages": []})
    assert b.get("s1") == {"session_id": "s1", "messages": []}
    assert b.size() == 1
    b.delete("s1")
    assert b.get("s1") is None


def test_session_backend_redis_crud_with_fake():
    from src.core.session_backend import RedisSessionBackend
    b = RedisSessionBackend(client=FakeRedis())
    b.set("s1", {"session_id": "s1", "messages": [{"role": "user", "content": "hi"}]})
    data = b.get("s1")
    assert data["session_id"] == "s1"
    assert data["messages"][0]["content"] == "hi"
    assert b.size() == 1
    b.delete("s1")
    assert b.get("s1") is None


def test_session_backend_redis_namespace_isolation():
    from src.core.session_backend import RedisSessionBackend
    b = RedisSessionBackend(client=FakeRedis())
    b.set("s1", {"session_id": "s1"})
    # scan 按完整 key 前缀（namespace）定位，返回 (session_id, data)
    rows = dict(b.scan("rag:session:"))
    assert rows == {"s1": {"session_id": "s1"}}
    # 其他 namespace 不干扰
    b.set("s2", {"session_id": "s2"})
    assert dict(b.scan("rag:session:s1")) == {"s1": {"session_id": "s1"}}


def test_session_backend_factory_memory_default():
    from src.core.session_backend import MemorySessionBackend, make_session_backend
    b = make_session_backend("memory")
    assert isinstance(b, MemorySessionBackend)


# ======================================================================
# SessionManager：会话序列化 + 后端持久化
# ======================================================================

def test_session_serialization_roundtrip():
    """Session（含 Message 列表 / summary / 时间戳）序列化后完整还原"""
    s = Session(session_id="s1", created_at=100.0, last_active=200.0, summary="摘要")
    s.messages = [
        Message(role="user", content="q1", timestamp=101.0),
        Message(role="assistant", content="a1", timestamp=102.0),
    ]
    d = SessionManager._session_to_dict(s)
    s2 = SessionManager._session_from_dict(d)
    assert s2.session_id == "s1"
    assert s2.created_at == 100.0
    assert s2.last_active == 200.0
    assert s2.summary == "摘要"
    assert len(s2.messages) == 2
    assert s2.messages[0].role == "user"
    assert s2.messages[0].content == "q1"
    assert s2.messages[0].timestamp == 101.0


def test_session_manager_redis_backend_cross_instance():
    """两个 SessionManager 共享同一 Redis 后端 → 跨实例会话一致（无状态副本）"""
    from src.core.session_backend import RedisSessionBackend
    fake = FakeRedis()
    backend = RedisSessionBackend(client=fake)
    sm1 = SessionManager(backend=backend)
    sm2 = SessionManager(backend=backend)

    sm1.add_message("sess_abc", "user", "你好")
    sm1.add_message("sess_abc", "assistant", "你好，有什么可以帮你")

    history = sm2.get_history("sess_abc")
    assert len(history) == 2
    assert history[0] == {"role": "user", "content": "你好"}
    assert history[1] == {"role": "assistant", "content": "你好，有什么可以帮你"}


def test_session_manager_reload_preserves_data():
    """backend 保留时重建 SessionManager（模拟副本重启）→ 会话不丢失"""
    from src.core.session_backend import MemorySessionBackend
    backend = MemorySessionBackend()
    sm1 = SessionManager(backend=backend)
    sm1.add_message("sess_keep", "user", "问题1")
    sm1.add_message("sess_keep", "assistant", "答案1")

    sm2 = SessionManager(backend=backend)  # 重启：新实例同一后端
    assert sm2.get_summary("sess_keep") == ""
    history = sm2.get_history("sess_keep")
    assert history[-1] == {"role": "assistant", "content": "答案1"}


def test_session_manager_summary_persisted_across_instance():
    """summary 经后端持久化，跨实例可读"""
    from src.core.session_backend import MemorySessionBackend
    backend = MemorySessionBackend()
    sm = SessionManager(backend=backend)
    s = sm.get_or_create_session("sess_s")
    s.summary = "历史摘要"
    sm.save_session(s)

    sm2 = SessionManager(backend=backend)
    assert sm2.get_summary("sess_s") == "历史摘要"


def test_session_manager_cleanup_expired_via_backend():
    """过期清理对后端生效：过期会话从存储移除"""
    from src.core.session_backend import MemorySessionBackend
    backend = MemorySessionBackend()
    sm = SessionManager(backend=backend, timeout_minutes=1)
    sm.add_message("sess_old", "user", "hi")
    s = sm.get_or_create_session("sess_old")
    s.last_active = time.time() - 120  # 过期 2 分钟
    sm.save_session(s)

    sm.cleanup_expired()
    # 清理后旧会话数据从后端移除
    assert backend.get("sess_old") is None


def test_session_manager_default_backend_memory():
    """默认配置（SESSION_BACKEND=memory）保持进程内行为，现测试兼容"""
    sm = SessionManager()
    sm.add_message("sess_d", "user", "q")
    assert sm.get_history("sess_d") == [{"role": "user", "content": "q"}]


# ======================================================================
# TaskManager：任务状态存后端（Redis / 共享存储）→ 多副本可查
# ======================================================================

def test_task_manager_redis_backend_cross_instance():
    """任务状态存 Redis：A 实例完成任务，B 实例可查询到 done（任意副本处理任意请求）"""
    from src.core.task_state_backend import RedisTaskStateBackend
    fake = FakeRedis()
    backend = RedisTaskStateBackend(client=fake)
    tm1 = TaskManager(backend=backend)
    tm2 = TaskManager(backend=backend)

    tid = tm1.create_task()
    tm1.complete(tid, {"answer": "跨实例答案"})

    t = tm2.get(tid)
    assert t is not None
    assert t["status"] == "done"
    assert t["result"]["answer"] == "跨实例答案"


def test_task_manager_memory_backend_unchanged():
    """默认（memory 后端）行为保持现测试兼容"""
    tm = TaskManager()
    tid = tm.create_task()
    tm.complete(tid, {"answer": "A"})
    assert tm.get(tid)["status"] == "done"


def test_task_manager_redis_cleanup_expired():
    """过期任务经后端清理"""
    from src.core.task_state_backend import RedisTaskStateBackend
    fake = FakeRedis()
    backend = RedisTaskStateBackend(client=fake)
    tm = TaskManager(backend=backend, ttl_seconds=10)
    tid = tm.create_task()
    tm.complete(tid, {"answer": "A"})
    n = tm.cleanup_expired(now=time.time() + 11)
    assert n == 1
    assert tm.get(tid) is None
