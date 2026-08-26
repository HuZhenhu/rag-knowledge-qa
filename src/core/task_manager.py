"""T1.6 提交-推送模式：进程内任务管理器（内存实现）。

- /query 异步模式下立即返回 task_id，后台执行查询（asyncio.create_task）
- 任务完成后经 WebSocket 订阅通道推送结果（复用 /ws/tasks 端点）
- 保留同步模式开关：QUERY_ASYNC_MODE=False 时 /query 保持同步响应

设计约束（本机无 Redis）：
- 任务状态与订阅队列均为进程内内存实现，单 worker 内有效；
- T2.1 无状态化阶段将迁移到 Redis 等外部存储。
"""
from __future__ import annotations

import asyncio
import threading
import time
import uuid

from typing import Any


class TaskManager:
    """内存任务表 + 订阅推送。

    线程安全：dict/订阅集合由锁保护；complete/fail 在事件循环线程
    （后台 asyncio 任务）中调用，asyncio.Queue.put_nowait 安全。
    """

    def __init__(self, ttl_seconds: float = 300.0):
        self._tasks: dict[str, dict[str, Any]] = {}
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds

    # ---------------- 任务状态机 ----------------

    def create_task(self) -> str:
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        with self._lock:
            self._tasks[task_id] = {
                "task_id": task_id,
                "status": "pending",
                "created_at": time.time(),
                "result": None,
                "error": None,
            }
        return task_id

    def set_running(self, task_id: str) -> None:
        with self._lock:
            t = self._tasks.get(task_id)
            if t is not None:
                t["status"] = "running"

    def complete(self, task_id: str, result: Any) -> None:
        self._finish(task_id, status="done", result=result, error=None)

    def fail(self, task_id: str, error: Any) -> None:
        self._finish(task_id, status="error", result=None, error=str(error))

    def _finish(self, task_id: str, *, status: str, result: Any, error: str | None) -> None:
        with self._lock:
            t = self._tasks.get(task_id)
            if t is None:
                return
            t["status"] = status
            t["result"] = result
            t["error"] = error
            t["finished_at"] = time.time()
        payload = {
            "type": "task_done",
            "task_id": task_id,
            "status": status,
            "result": result,
            "error": error,
        }
        self._publish(task_id, payload)

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            t = self._tasks.get(task_id)
            if t is None:
                return None
            return dict(t)

    # ---------------- 订阅推送 ----------------

    def subscribe(self, task_id: str, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.setdefault(task_id, set()).add(queue)

    def unsubscribe(self, task_id: str, queue: asyncio.Queue) -> None:
        with self._lock:
            subs = self._subscribers.get(task_id)
            if subs:
                subs.discard(queue)
                if not subs:
                    self._subscribers.pop(task_id, None)

    def _publish(self, task_id: str, payload: dict[str, Any]) -> None:
        with self._lock:
            subs = list(self._subscribers.pop(task_id, ()))
        for q in subs:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:  # pragma: no cover
                pass

    # ---------------- 清理 ----------------

    def cleanup_expired(self, now: float | None = None) -> int:
        """清理超过 TTL 的已完成/失败任务；保留 pending/running 任务。"""
        now = now if now is not None else time.time()
        with self._lock:
            expired = [
                tid for tid, t in self._tasks.items()
                if t["status"] in ("done", "error")
                and now - t.get("finished_at", now) > self._ttl
            ]
            for tid in expired:
                self._tasks.pop(tid, None)
        return len(expired)


# 进程级默认单例
_DEFAULT: TaskManager | None = None


def get_task_manager() -> TaskManager:
    global _DEFAULT
    if _DEFAULT is None:
        from src.config import TASK_RESULT_TTL_SECONDS
        _DEFAULT = TaskManager(ttl_seconds=TASK_RESULT_TTL_SECONDS)
    return _DEFAULT


def reset() -> None:
    """重置单例（主要用于测试隔离）。"""
    global _DEFAULT
    _DEFAULT = None
