"""T1.4 进程外缓存后端抽象

- CacheBackend: 抽象基类（get/set/delete/scan/clear/size）
- SQLiteBackend: SQLite KV 实现（默认 fallback，无外部依赖）
- RedisBackend: redis-py 实现（需配置 REDIS_URL；未安装 redis 包时延迟报错）

语义缓存与精确查询缓存均通过该抽象获得进程外共享能力：
跨 worker 命中一致（SQLite 共享文件 / Redis 共享服务）。
"""
import json
import sqlite3
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterator

from src.config import BASE_DIR, REDIS_URL

DEFAULT_KV_DB: Path = BASE_DIR / "data" / "cache_kv.db"


class CacheBackend(ABC):
    """进程外缓存后端统一接口（value 均为 JSON 字符串）"""

    @abstractmethod
    def get(self, key: str) -> str | None:
        """按 key 取值，未命中返回 None"""

    @abstractmethod
    def set(self, key: str, value: str) -> None:
        """写入 key-value"""

    @abstractmethod
    def delete(self, key: str) -> None:
        """删除指定 key"""

    @abstractmethod
    def scan(self, prefix: str) -> Iterator[tuple[str, str]]:
        """按 key 前缀遍历 (key, value) 迭代器"""

    @abstractmethod
    def clear(self, namespace: str | None = None) -> None:
        """清空指定 namespace（None 清空全部）"""

    @abstractmethod
    def size(self, namespace: str | None = None) -> int:
        """条目数（可按 namespace 统计）"""


# ======================================================================
# SQLite 实现（默认 fallback）
# ======================================================================

class SQLiteBackend(CacheBackend):
    """SQLite 键值后端，线程安全，跨进程共享同一文件即跨 worker 一致"""

    def __init__(self, db_path: Path | str = DEFAULT_KV_DB):
        self.db_path = Path(db_path)
        self._lock = threading.Lock()
        self._init_db()

    # -- internal ------------------------------------------------------
    def _conn(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _exec(self, sql: str, params: tuple = (), fetchone: bool = False,
              fetchall: bool = False):
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(sql, params)
                conn.commit()
                if fetchall:
                    return cur.fetchall()
                if fetchone:
                    return cur.fetchone()
                return None
            finally:
                conn.close()

    def _init_db(self) -> None:
        self._exec(
            """
            CREATE TABLE IF NOT EXISTS kv_cache (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                created_at REAL DEFAULT 0
            )
            """
        )

    # -- CacheBackend ---------------------------------------------------
    def get(self, key: str) -> str | None:
        row = self._exec("SELECT value FROM kv_cache WHERE key = ?", (key,), fetchone=True)
        return row["value"] if row else None

    def set(self, key: str, value: str) -> None:
        self._exec(
            "INSERT OR REPLACE INTO kv_cache(key, value, created_at) VALUES (?,?,?)",
            (key, value, time.time()),
        )

    def delete(self, key: str) -> None:
        self._exec("DELETE FROM kv_cache WHERE key = ?", (key,))

    def scan(self, prefix: str) -> Iterator[tuple[str, str]]:
        rows = self._exec(
            "SELECT key, value FROM kv_cache WHERE key LIKE ? ORDER BY key",
            (prefix + "%",),
            fetchall=True,
        )
        for row in rows:
            yield row["key"], row["value"]

    def clear(self, namespace: str | None = None) -> None:
        if namespace:
            self._exec("DELETE FROM kv_cache WHERE key LIKE ?", (namespace + "%",))
        else:
            self._exec("DELETE FROM kv_cache")

    def size(self, namespace: str | None = None) -> int:
        if namespace:
            row = self._exec(
                "SELECT COUNT(*) AS c FROM kv_cache WHERE key LIKE ?",
                (namespace + "%",),
                fetchone=True,
            )
        else:
            row = self._exec("SELECT COUNT(*) AS c FROM kv_cache", fetchone=True)
        return int(row["c"]) if row else 0


# ======================================================================
# Redis 实现（进程外共享）
# ======================================================================

class RedisBackend(CacheBackend):
    """基于 redis-py 的共享缓存后端。

    - key 统一加 namespace 前缀（如 "rag:cache:"）避免与其他业务 key 冲突
    - 未安装 redis 包且未注入 client 时，构造报错（由调用方配置开关控制是否启用）
    """

    DEFAULT_NAMESPACE = "rag:cache"

    def __init__(self, url: str = REDIS_URL, namespace: str = DEFAULT_NAMESPACE,
                 client=None):
        if client is not None:
            self._r = client
        else:
            import redis  # 延迟 import：本机未安装时仅当启用 Redis 后端才报错

            self._r = redis.Redis.from_url(url, decode_responses=True)
        self.namespace = namespace

    def _key(self, key: str) -> str:
        return f"{self.namespace}:{key}"

    def _pattern(self, prefix: str) -> str:
        """生成匹配模式：prefix 若已带 namespace 则直接使用，否则自动拼接"""
        if not prefix:
            return f"{self.namespace}:"
        if prefix == self.namespace or prefix.startswith(self.namespace + ":"):
            return prefix
        return f"{self.namespace}:{prefix}"

    # -- CacheBackend ---------------------------------------------------
    def get(self, key: str) -> str | None:
        return self._r.get(self._key(key))

    def set(self, key: str, value: str) -> None:
        self._r.set(self._key(key), value)

    def delete(self, key: str) -> None:
        self._r.delete(self._key(key))

    def scan(self, prefix: str) -> Iterator[tuple[str, str]]:
        pattern = self._pattern(prefix) + "*"
        for raw_key in self._r.scan_iter(match=pattern):
            value = self._r.get(raw_key)
            if value is not None:
                yield raw_key, value

    def clear(self, namespace: str | None = None) -> None:
        pattern = self._pattern(namespace or "") + "*"
        keys = list(self._r.scan_iter(match=pattern))
        if keys:
            self._r.delete(*keys)

    def size(self, namespace: str | None = None) -> int:
        pattern = self._pattern(namespace or "") + "*"
        return sum(1 for _ in self._r.scan_iter(match=pattern))


# ======================================================================
# 工厂：按配置创建后端
# ======================================================================

def make_cache_backend(kind: str, db_path: Path | str = DEFAULT_KV_DB,
                       namespace: str = RedisBackend.DEFAULT_NAMESPACE) -> CacheBackend:
    """按 kind 创建缓存后端（'sqlite' | 'redis'）"""
    kind = (kind or "sqlite").lower()
    if kind == "redis":
        return RedisBackend(namespace=namespace)
    return SQLiteBackend(db_path)
