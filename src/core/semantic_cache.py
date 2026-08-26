"""语义缓存 — 余弦相似度召回历史查询的答案（P1-3）

- 基于 embed 函数（默认 langchain 引擎的 HuggingFaceEmbeddings.embed_query，本地 bge-m3）
  对 query 编码，与缓存库中同 ACL 指纹的历史 query 向量算余弦相似度，
  最高相似度 ≥ SEMANTIC_CACHE_THRESHOLD(默认0.92) 视为命中，返回对应 answer + sources。
- 存储用可插拔后端（T1.4）：默认 SQLiteBackend（跨 worker 共享文件），可切换 RedisBackend（进程外共享）。
- 缓存条目按 acl_fp 隔离：不同租户/权限指纹互不可见，防跨权限缓存泄露。
- 提供进程内单例 get_semantic_cache() 与 clear_all_caches()（索引变更失效钩子）。
"""
import hashlib
import json
import math
import time
from pathlib import Path

from src.config import (
    BASE_DIR,
    SEMANTIC_CACHE_BACKEND,
    SEMANTIC_CACHE_DOMAIN_THRESHOLDS,
    SEMANTIC_CACHE_MAX_ENTRIES,
    SEMANTIC_CACHE_PREFIX_ENABLED,
    SEMANTIC_CACHE_PREFIX_FALLBACK_THRESHOLD,
    SEMANTIC_CACHE_PREFIX_WIDTH,
    SEMANTIC_CACHE_TTL_SECONDS,
    SEMANTIC_CACHE_ENABLED,
    SEMANTIC_CACHE_THRESHOLD,
)
from src.core.cache_policy import (
    expired_entry,
    normalize_prefix,
    pick_lru_keys,
    prefix_matches,
    resolve_threshold,
    warmup_parse,
)

DB_PATH: Path = BASE_DIR / "data" / "semantic_cache.db"


def _cosine(a, b) -> float:
    """余弦相似度（向量为 list[float]）"""
    na = math.sqrt(sum(x * x for x in a)) or 1e-9
    nb = math.sqrt(sum(x * x for x in b)) or 1e-9
    return float(sum(x * y for x, y in zip(a, b)) / (na * nb))


class SemanticCache:
    """SQLite 语义缓存（T3.2 深化：前缀缓存 / 按领域阈值 / 预热 / 淘汰）

    Args:
        db_path: sqlite 文件路径
        threshold: 余弦相似度命中阈值（领域未配置时的全局默认）
        backend: 可插拔后端（默认 SQLiteBackend）
        prefix_enabled: 前缀缓存开关（相同规范化前缀的相近问法放宽阈值）
        prefix_width: 前缀规范化宽度（字符）
        prefix_fallback_threshold: 前缀命中放宽阈值（默认 0.86 < 全局 0.92）
        ttl_seconds: 条目 TTL（秒，默认 7 天；<=0 永不过期）
        max_entries: 条目上限，超限按 LRU 淘汰（默认 5 万）
        domain_thresholds: 领域 -> 阈值映射（可配置），未配置领域回退 threshold
    """

    def __init__(self, db_path: Path | str = DB_PATH, threshold: float = SEMANTIC_CACHE_THRESHOLD,
                 backend=None, prefix_enabled: bool = SEMANTIC_CACHE_PREFIX_ENABLED,
                 prefix_width: int = SEMANTIC_CACHE_PREFIX_WIDTH,
                 prefix_fallback_threshold: float = SEMANTIC_CACHE_PREFIX_FALLBACK_THRESHOLD,
                 ttl_seconds: int = SEMANTIC_CACHE_TTL_SECONDS,
                 max_entries: int = SEMANTIC_CACHE_MAX_ENTRIES,
                 domain_thresholds: dict | None = SEMANTIC_CACHE_DOMAIN_THRESHOLDS):
        self.db_path = Path(db_path)
        self.threshold = threshold
        self.prefix_enabled = prefix_enabled
        self.prefix_width = int(prefix_width)
        self.prefix_fallback_threshold = float(prefix_fallback_threshold)
        self.ttl_seconds = int(ttl_seconds)
        self.max_entries = int(max_entries)
        self.domain_thresholds = domain_thresholds or {}
        if backend is None:
            from src.core.cache_backend import SQLiteBackend
            backend = SQLiteBackend(str(self.db_path))
        self._backend = backend

    # -- 对外接口 ---------------------------------------------------------
    def get(self, query: str, acl_fp: str | None = None, embed_query=None, domain: str | None = None):
        """编码 query 并与同 acl_fp 历史条目比对（T3.2：支持按领域阈值 + 前缀缓存放宽）。

        Args:
            query: 本次查询
            acl_fp: ACL 指纹（租户/权限隔离）
            embed_query: query -> 向量 编码函数
            domain: 可选领域名；命中 SEMANTIC_CACHE_DOMAIN_THRESHOLDS[domain] 阈值，
                    否则回退全局 threshold。前缀相同的相近问法用 PREFIX_FALLBACK_THRESHOLD 放宽。

        Returns:
            (answer, sources, sim) | None —— 命中返回三元组，未命中返回 None。
            embed_query 缺失或阈值 >= 1.0 时直接返回 None（无法命中）。
        """
        if embed_query is None:
            return None
        threshold_base = resolve_threshold(domain, self.threshold, self.domain_thresholds)
        if threshold_base >= 1.0:
            return None
        vec = embed_query(query)
        fp = acl_fp or "none"
        prefix = normalize_prefix(query, self.prefix_width) if self.prefix_enabled else ""
        best = None
        for _key, raw in self._backend.scan(f"{fp}|||"):
            try:
                data = json.loads(raw)
                hist = data.get("query_vec") or []
                if not hist:
                    continue
                sim = _cosine(vec, hist)
                thr = threshold_base
                # 前缀缓存：同族问法（规范化前缀互为前缀且非自身）放宽阈值
                if prefix:
                    hist_q = data.get("query") or ""
                    if hist_q != query and prefix_matches(hist_q, query, self.prefix_width):
                        thr = min(thr, self.prefix_fallback_threshold)
                if sim >= thr and (best is None or sim > best[2]):
                    try:
                        srcs = data.get("sources") or []
                    except Exception:
                        srcs = []
                    best = (data.get("answer", ""), srcs, round(sim, 4), _key)
            except Exception:
                continue
        if best is not None:
            self._touch(best[3])  # 命中回写访问时间/计数（LRU 依据 + 命中率观测）
            return best[0], best[1], best[2]
        return None

    def set(self, query: str, answer: str, sources: list | None = None,
            acl_fp: str | None = None, embed_query=None, domain: str | None = None) -> None:
        """写入缓存条目（query 向量 + answer + sources + acl_fp + domain + 时间戳）"""
        if embed_query is None:
            return
        vec = embed_query(query)
        fp = acl_fp or "none"
        now = time.time()
        key = f"{fp}|||{hashlib.md5(query.encode('utf-8')).hexdigest()}"
        value = json.dumps({
            "query": query,
            "query_vec": vec,
            "answer": answer,
            "sources": sources or [],
            "domain": domain or "",
            "created_at": now,
            "last_access_ts": now,
            "access_count": 0,
        }, ensure_ascii=False)
        self._backend.set(key, value)
        self._maybe_evict()

    def _touch(self, key: str) -> None:
        """命中回写：更新 last_access_ts 与 access_count（LRU + 命中率观测依据）"""
        raw = self._backend.get(key)
        if raw is None:
            return
        try:
            data = json.loads(raw)
        except Exception:
            return
        data["access_count"] = int(data.get("access_count", 0)) + 1
        data["last_access_ts"] = time.time()
        self._backend.set(key, json.dumps(data, ensure_ascii=False))

    # -- T3.2 淘汰 / 预热 / 统计 -------------------------------------------
    def _maybe_evict(self) -> None:
        """set 后按需触发淘汰（超上限 LRU 或 TTL 过期），控制缓存规模"""
        if self.max_entries > 0 and self.size > self.max_entries:
            self.evict()

    def evict(self, max_entries: int | None = None, ttl_seconds: int | None = None,
              now: float | None = None) -> int:
        """淘汰策略：TTL 过期清理 + 超上限 LRU 淘汰。

        Returns: 被淘汰条目数。
        """
        max_entries = self.max_entries if max_entries is None else int(max_entries)
        ttl = self.ttl_seconds if ttl_seconds is None else int(ttl_seconds)
        now = time.time() if now is None else now
        entries: dict[str, dict] = {}
        for key, raw in self._backend.scan(""):
            try:
                entries[key] = json.loads(raw)
            except Exception:
                continue
        removed = 0
        for key, data in list(entries.items()):
            if expired_entry(data, now, ttl):
                self._backend.delete(key)
                removed += 1
                del entries[key]
        if max_entries > 0 and len(entries) > max_entries:
            excess = len(entries) - max_entries
            for key in pick_lru_keys(entries, excess):
                self._backend.delete(key)
                removed += 1
        return removed

    def warmup(self, hot: list, embed_query=None, acl_fp: str | None = None,
               domain: str | None = None) -> int:
        """热门问题预热：批量写入语义缓存（客服高频场景提升命中率）。

        hot 支持纯字符串或 {q,a,sources} dict（见 warmup_parse）。返回写入条数。
        """
        items = warmup_parse(hot)
        n = 0
        if embed_query is None:
            return 0
        for q, answer, srcs in items:
            if not q:
                continue
            try:
                self.set(q, answer, srcs, acl_fp, embed_query, domain=domain)
                n += 1
            except Exception:
                continue
        return n

    def stats(self, acl_fp: str | None = None) -> dict:
        """缓存条目统计（命中/访问次数，供命中率观测与运维）。"""
        fp = acl_fp or "none"
        total = 0
        hits = 0
        for _key, raw in self._backend.scan(f"{fp}|||"):
            total += 1
            try:
                hits += int(json.loads(raw).get("access_count", 0))
            except Exception:
                continue
        return {"entries": total, "hits": hits, "access_count": hits}

    def clear(self) -> None:
        """清空全部缓存条目"""
        self._backend.clear()

    @property
    def size(self) -> int:
        return self._backend.size()


_SEMANTIC_CACHE: SemanticCache | None = None


def get_semantic_cache() -> SemanticCache | None:
    """进程内语义缓存单例（P1-3）。开关关闭时返回 None。"""
    global _SEMANTIC_CACHE
    if not SEMANTIC_CACHE_ENABLED:
        return None
    if _SEMANTIC_CACHE is None:
        from src.core.cache_backend import make_cache_backend
        backend = make_cache_backend(SEMANTIC_CACHE_BACKEND, db_path=DB_PATH,
                                     namespace="rag:sem")
        _SEMANTIC_CACHE = SemanticCache(db_path=DB_PATH, backend=backend)
    return _SEMANTIC_CACHE


def clear_all_caches() -> None:
    """索引变更后清空检索/语义缓存（P1-3 失效钩子）"""
    qc = get_query_cache()
    if qc is not None:
        qc.clear()
    sc = get_semantic_cache()
    if sc is not None:
        sc.clear()


# 延迟 import，避免循环依赖（query_cache 不依赖 semantic_cache）
from src.core.query_cache import get_query_cache  # noqa: E402
