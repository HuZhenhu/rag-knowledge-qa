"""T2.1 无状态化：任务状态存储后端抽象。

- TaskStateBackend: 任务状态存取抽象
- MemoryTaskStateBackend: 进程内 dict（本机 fallback，保持现行为）
- RedisTaskStateBackend: redis-py 实现（生产多副本共享，key 前缀 rag:task:）

任务状态存后端后，任意 API 副本可查询/处理任意请求（无状态化关键）。
订阅推送（WebSocket）仍为进程内（连接所在副本负责推送）。
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, Iterator


class TaskStateBackend(ABC):
    """任务状态后端抽象。value 为可 JSON 序列化的 dict。"""

    @abstractmethod
    def get(self, task_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def set(self, task_id: str, data: dict[str, Any], ttl: float | None = None) -> None: ...

    @abstractmethod
    def delete(self, task_id: str) -> None: ...

    @abstractmethod
    def scan(self, match: str = "*") -> Iterator[tuple[str, dict[str, Any]]]: ...


class MemoryTaskStateBackend(TaskStateBackend):
    """进程内 dict 实现（单机 fallback）。"""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    def get(self, task_id: str) -> dict[str, Any] | None:
        return self._data.get(task_id)

    def set(self, task_id: str, data: dict[str, Any], ttl: float | None = None) -> None:
        self._data[task_id] = data

    def delete(self, task_id: str) -> None:
        self._data.pop(task_id, None)

    def scan(self, match: str = "*") -> Iterator[tuple[str, dict[str, Any]]]:
        import fnmatch
        pattern = match if any(c in match for c in "*?[") else match + "*"
        for tid, data in self._data.items():
            if fnmatch.fnmatchcase(tid, pattern):
                yield tid, data


class RedisTaskStateBackend(TaskStateBackend):
    """redis-py 实现：key = rag:task:{task_id}，value = JSON。

    支持注入 client（测试可传 FakeRedis）。
    """

    PREFIX = "rag:task:"

    def __init__(self, client=None, prefix: str = PREFIX, ttl: float | None = 300.0):
        if client is None:
            import redis
            from src.config import REDIS_URL
            client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        self._client = client
        self._prefix = prefix
        self._default_ttl = ttl

    def _key(self, task_id: str) -> str:
        return f"{self._prefix}{task_id}"

    def get(self, task_id: str) -> dict[str, Any] | None:
        raw = self._client.get(self._key(task_id))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)

    def set(self, task_id: str, data: dict[str, Any], ttl: float | None = None) -> None:
        ttl = self._default_ttl if ttl is None else ttl
        self._client.set(self._key(task_id), json.dumps(data, ensure_ascii=False))
        if ttl and ttl > 0 and hasattr(self._client, "expire"):
            self._client.expire(self._key(task_id), int(ttl))

    def delete(self, task_id: str) -> None:
        self._client.delete(self._key(task_id))

    def scan(self, match: str = "*") -> Iterator[tuple[str, dict[str, Any]]]:
        import fnmatch
        pattern = match if any(c in match for c in "*?[") else match + "*"
        for key in self._client.scan_iter(match=f"{self._prefix}*"):
            if isinstance(key, bytes):
                key = key.decode("utf-8")
            tid = key[len(self._prefix):]
            if not fnmatch.fnmatchcase(key, pattern):
                continue
            data = self.get(tid)
            if data is not None:
                yield tid, data


def make_task_state_backend(kind: str) -> TaskStateBackend:
    """按配置创建任务状态后端：memory（默认）/ redis。"""
    kind = (kind or "memory").lower()
    if kind == "redis":
        return RedisTaskStateBackend()
    return MemoryTaskStateBackend()
