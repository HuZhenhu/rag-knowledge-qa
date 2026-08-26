"""T2.1 无状态化：会话存储后端抽象。

- SessionBackend: 会话存储抽象（dict 级接口，由上层负责序列化）
- MemorySessionBackend: 进程内 dict（本机 fallback，保持现行为）
- RedisSessionBackend: redis-py 实现（生产多副本共享，key 前缀 rag:session:）
- make_session_backend: 按配置（memory|redis）创建后端

任意 API 副本共享同一后端 → 会话不丢失、任意副本可处理任意请求。
"""
from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from typing import Any, Iterator


class SessionBackend(ABC):
    """会话存储后端抽象。value 为可 JSON 序列化的 dict。"""

    @abstractmethod
    def get(self, session_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def set(self, session_id: str, data: dict[str, Any], ttl: float | None = None) -> None: ...

    @abstractmethod
    def delete(self, session_id: str) -> None: ...

    @abstractmethod
    def scan(self, match: str = "*") -> Iterator[tuple[str, dict[str, Any]]]: ...

    @abstractmethod
    def size(self) -> int: ...


class MemorySessionBackend(SessionBackend):
    """进程内 dict 实现（单机 fallback）。"""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    def get(self, session_id: str) -> dict[str, Any] | None:
        return self._data.get(session_id)

    def set(self, session_id: str, data: dict[str, Any], ttl: float | None = None) -> None:
        self._data[session_id] = data

    def delete(self, session_id: str) -> None:
        self._data.pop(session_id, None)

    def scan(self, match: str = "*") -> Iterator[tuple[str, dict[str, Any]]]:
        import fnmatch
        pattern = match if any(c in match for c in "*?[") else match + "*"
        for sid, data in self._data.items():
            if fnmatch.fnmatchcase(sid, pattern):
                yield sid, data

    def size(self) -> int:
        return len(self._data)


class RedisSessionBackend(SessionBackend):
    """redis-py 实现：key = rag:session:{session_id}，value = JSON。

    支持注入 client（测试可传 FakeRedis）；ttl 用于会话过期回收。
    """

    PREFIX = "rag:session:"

    def __init__(self, client=None, prefix: str = PREFIX, ttl: float | None = 1800.0):
        if client is None:
            import redis
            from src.config import REDIS_URL
            client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        self._client = client
        self._prefix = prefix
        self._default_ttl = ttl

    def _key(self, session_id: str) -> str:
        return f"{self._prefix}{session_id}"

    def get(self, session_id: str) -> dict[str, Any] | None:
        raw = self._client.get(self._key(session_id))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)

    def set(self, session_id: str, data: dict[str, Any], ttl: float | None = None) -> None:
        ttl = self._default_ttl if ttl is None else ttl
        self._client.set(self._key(session_id), json.dumps(data, ensure_ascii=False))
        if ttl and ttl > 0 and hasattr(self._client, "expire"):
            self._client.expire(self._key(session_id), int(ttl))

    def delete(self, session_id: str) -> None:
        self._client.delete(self._key(session_id))

    def scan(self, match: str = "*") -> Iterator[tuple[str, dict[str, Any]]]:
        import fnmatch
        pattern = match if any(c in match for c in "*?[") else match + "*"
        for key in self._client.scan_iter(match=f"{self._prefix}*"):
            if isinstance(key, bytes):
                key = key.decode("utf-8")
            sid = key[len(self._prefix):]
            if not fnmatch.fnmatchcase(key, pattern):
                continue
            data = self.get(sid)
            if data is not None:
                yield sid, data

    def size(self) -> int:
        count = 0
        for _ in self._client.scan_iter(match=f"{self._prefix}*"):
            count += 1
        return count


def make_session_backend(kind: str) -> SessionBackend:
    """按配置创建会话后端：memory（默认）/ redis。"""
    kind = (kind or "memory").lower()
    if kind == "redis":
        return RedisSessionBackend()
    return MemorySessionBackend()
