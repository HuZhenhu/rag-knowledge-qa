"""查询结果LRU缓存（P1-3：key 纳入 ACL 指纹，防跨权限缓存泄露）

- 最多缓存 1000 条，每条 1 小时后自动过期
- key = md5(query || top_k || acl_fp)，不同租户/权限指纹各自独立
- 存储可插拔（T1.4）：默认进程内 cachetools（memory），可注入 RedisBackend 实现跨 worker 精确共享
- 提供进程内单例 get_query_cache()，供默认引擎与索引失效钩子共享同一实例
"""
import hashlib
import json
from cachetools import TTLCache

from src.config import QUERY_CACHE_BACKEND, QUERY_CACHE_ENABLED


class QueryCache:
    """查询结果缓存，存储检索/回答结果。

    - backend=None：进程内 LRU（cachetools TTLCache，默认）
    - backend 注入：进程外共享存储（如 RedisBackend），value 以 JSON 字符串存储
    - 最多缓存 1000 条（内存）/ TTL 1 小时
    """

    _DEFAULT_MAX = 1000
    _DEFAULT_TTL = 3600  # 1小时

    def __init__(self, maxsize: int = _DEFAULT_MAX, ttl: int = _DEFAULT_TTL,
                 backend=None):
        self.cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl)
        self._backend = backend

    @staticmethod
    def _make_key(query: str, top_k: int, acl_fp: str | None = None) -> str:
        """用查询文本 + top_k + ACL 指纹生成缓存 key"""
        fp = acl_fp or "none"
        raw = f"{query}|||{top_k}|||{fp}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def get(self, query: str, top_k: int, acl_fp: str | None = None):
        """查询缓存，命中返回缓存值（list[dict] 或 dict），未命中返回 None"""
        key = self._make_key(query, top_k, acl_fp)
        if self._backend is not None:
            raw = self._backend.get(key)
            if raw is None:
                return None
            try:
                return json.loads(raw)
            except Exception:
                return None
        return self.cache.get(key)

    def set(self, query: str, top_k: int, value, acl_fp: str | None = None) -> None:
        """写入缓存"""
        key = self._make_key(query, top_k, acl_fp)
        if self._backend is not None:
            self._backend.set(key, json.dumps(value, ensure_ascii=False))
            return
        self.cache[key] = value

    def clear(self) -> None:
        """清空缓存"""
        if self._backend is not None:
            self._backend.clear()
            return
        self.cache.clear()

    @property
    def size(self) -> int:
        """当前缓存条目数"""
        if self._backend is not None:
            return self._backend.size()
        return len(self.cache)


_QUERY_CACHE: QueryCache | None = None


def get_query_cache() -> QueryCache | None:
    """进程内缓存单例（P1-3 / T1.4）。

    默认引擎与索引失效钩子（incremental_indexer.sync）必须操作同一实例，
    否则索引变更后无法真正清掉引擎已持有的缓存。开关关闭时返回 None。
    QUERY_CACHE_BACKEND=redis 时使用 RedisBackend 实现跨 worker 精确共享。
    """
    global _QUERY_CACHE
    if not QUERY_CACHE_ENABLED:
        return None
    if _QUERY_CACHE is None:
        backend = None
        if QUERY_CACHE_BACKEND == "redis":
            from src.core.cache_backend import RedisBackend
            backend = RedisBackend(namespace="rag:qc")
        _QUERY_CACHE = QueryCache(backend=backend)
    return _QUERY_CACHE

