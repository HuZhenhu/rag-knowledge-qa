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
from pathlib import Path

from src.config import (
    BASE_DIR,
    SEMANTIC_CACHE_BACKEND,
    SEMANTIC_CACHE_ENABLED,
    SEMANTIC_CACHE_THRESHOLD,
)

DB_PATH: Path = BASE_DIR / "data" / "semantic_cache.db"


def _cosine(a, b) -> float:
    """余弦相似度（向量为 list[float]）"""
    na = math.sqrt(sum(x * x for x in a)) or 1e-9
    nb = math.sqrt(sum(x * x for x in b)) or 1e-9
    return float(sum(x * y for x, y in zip(a, b)) / (na * nb))


class SemanticCache:
    """SQLite 语义缓存

    Args:
        db_path: sqlite 文件路径
        threshold: 余弦相似度命中阈值
    """

    def __init__(self, db_path: Path | str = DB_PATH, threshold: float = SEMANTIC_CACHE_THRESHOLD,
                 backend=None):
        self.db_path = Path(db_path)
        self.threshold = threshold
        if backend is None:
            from src.core.cache_backend import SQLiteBackend
            backend = SQLiteBackend(str(self.db_path))
        self._backend = backend

    # -- 对外接口 ---------------------------------------------------------
    def get(self, query: str, acl_fp: str | None = None, embed_query=None):
        """编码 query 并与同 acl_fp 历史条目比对。

        Returns:
            (answer, sources, sim) | None —— 命中返回三元组，未命中返回 None。
            embed_query 缺失或阈值 >= 1.0 时直接返回 None（无法命中）。
        """
        if embed_query is None or self.threshold >= 1.0:
            return None
        vec = embed_query(query)
        fp = acl_fp or "none"
        best = None
        for _key, raw in self._backend.scan(f"{fp}|||"):
            try:
                data = json.loads(raw)
                hist = data.get("query_vec") or []
                if not hist:
                    continue
                sim = _cosine(vec, hist)
                if sim >= self.threshold and (best is None or sim > best[2]):
                    try:
                        srcs = data.get("sources") or []
                    except Exception:
                        srcs = []
                    best = (data.get("answer", ""), srcs, round(sim, 4))
            except Exception:
                continue
        return best

    def set(self, query: str, answer: str, sources: list | None = None,
            acl_fp: str | None = None, embed_query=None) -> None:
        """写入缓存条目（query 向量 + answer + sources + acl_fp）"""
        if embed_query is None:
            return
        vec = embed_query(query)
        fp = acl_fp or "none"
        key = f"{fp}|||{hashlib.md5(query.encode('utf-8')).hexdigest()}"
        value = json.dumps({
            "query": query,
            "query_vec": vec,
            "answer": answer,
            "sources": sources or [],
        }, ensure_ascii=False)
        self._backend.set(key, value)

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
