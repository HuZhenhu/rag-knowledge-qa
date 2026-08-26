"""T1.4 进程外缓存后端抽象 单元测试（红）
- CacheBackend 抽象：SQLiteBackend / RedisBackend
- SemanticCache / QueryCache 支持注入后端，key 含 ACL 指纹
"""
import fnmatch
import json

import pytest

from src.core.cache_backend import SQLiteBackend, RedisBackend
from src.core.query_cache import QueryCache
from src.core.semantic_cache import SemanticCache


# ---------- 工具：Fake Redis（不依赖真实 redis 服务/包） ----------

class FakeRedis:
    """最小 redis 客户端替身：get/set/delete/scan_iter/dbsize"""

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

    def dbsize(self):
        return len(self._d)


# ---------- SQLiteBackend（KV 后端） ----------

def test_sqlite_backend_kv_roundtrip(tmp_path):
    b = SQLiteBackend(str(tmp_path / "kv.db"))
    assert b.get("a") is None
    b.set("a", '{"x":1}')
    assert b.get("a") == '{"x":1}'
    b.delete("a")
    assert b.get("a") is None


def test_sqlite_backend_scan_prefix(tmp_path):
    b = SQLiteBackend(str(tmp_path / "kv.db"))
    b.set("fp1|||h1", "v1")
    b.set("fp1|||h2", "v2")
    b.set("fp2|||h1", "v3")
    rows = dict(b.scan("fp1|||"))
    assert rows == {"fp1|||h1": "v1", "fp1|||h2": "v2"}


def test_sqlite_backend_clear_and_size(tmp_path):
    b = SQLiteBackend(str(tmp_path / "kv.db"))
    b.set("k1", "1")
    b.set("k2", "2")
    assert b.size() == 2
    b.clear()
    assert b.size() == 0


# ---------- RedisBackend（进程外后端，fake client） ----------

def test_redis_backend_protocol():
    b = RedisBackend(client=FakeRedis())
    b.set("k1", "v1")
    assert b.get("k1") == "v1"
    assert b.size() == 1
    rows = dict(b.scan("rag:cache:k"))
    assert "rag:cache:k1" in rows
    b.delete("k1")
    assert b.get("k1") is None


def test_redis_backend_scan_prefix_only_namespace():
    b = RedisBackend(client=FakeRedis())
    b.set("k1", "v1")
    b.set("k2", "v2")
    rows = dict(b.scan("rag:cache:k1"))
    assert rows == {"rag:cache:k1": "v1"}


def test_redis_backend_clear_namespace():
    b = RedisBackend(client=FakeRedis())
    b.set("k1", "v1")
    b.set("k2", "v2")
    b.clear(namespace="rag:cache")
    assert b.size() == 0


# ---------- SemanticCache 注入后端 ----------

def _embed_vec(t):
    return [1.0, 0.0] if t else [0.0, 1.0]


@pytest.mark.parametrize("backend_factory", [
    lambda tmp: SQLiteBackend(str(tmp / "sc_kv.db")),
    lambda tmp: RedisBackend(client=FakeRedis()),
])
def test_semantic_cache_injected_backend_hit_miss(tmp_path, backend_factory):
    sc = SemanticCache(backend=backend_factory(tmp_path), threshold=0.85)
    sc.set("远程办公政策", "政策答案", [{"content": "c"}], "fp1", lambda t: [1.0, 0.0])
    hit = sc.get("远程办公政策具体内容", "fp1", lambda t: [0.95, 0.3])
    assert hit is not None
    assert hit[0] == "政策答案"
    miss = sc.get("今天天气怎么样", "fp1", lambda t: [0.0, 1.0])
    assert miss is None


@pytest.mark.parametrize("backend_factory", [
    lambda tmp: SQLiteBackend(str(tmp / "sc_kv2.db")),
    lambda tmp: RedisBackend(client=FakeRedis()),
])
def test_semantic_cache_injected_backend_acl_isolation(tmp_path, backend_factory):
    sc = SemanticCache(backend=backend_factory(tmp_path), threshold=0.85)
    sc.set("机密政策", "机密答案", [{"content": "c"}], "fp_admin", lambda t: [1.0, 0.0])
    assert sc.get("机密政策", "fp_user", lambda t: [1.0, 0.0]) is None
    assert sc.get("机密政策", "fp_admin", lambda t: [1.0, 0.0]) is not None


def test_semantic_cache_injected_backend_clear(tmp_path):
    sc = SemanticCache(backend=SQLiteBackend(str(tmp_path / "sc_kv3.db")), threshold=0.85)
    sc.set("q", "a", [{"content": "c"}], "fp1", lambda t: [1.0, 0.0])
    sc.clear()
    assert sc.get("q", "fp1", lambda t: [1.0, 0.0]) is None


# ---------- QueryCache 注入 Redis 后端（跨实例一致） ----------

def test_query_cache_redis_backend_cross_instance():
    """两个 QueryCache 共享同一 Redis 后端 → 跨进程/实例命中一致，key 含 ACL 指纹"""
    fake = FakeRedis()
    rdb = RedisBackend(client=fake)
    c1 = QueryCache(maxsize=16, ttl=300, backend=rdb)
    c2 = QueryCache(maxsize=16, ttl=300, backend=rdb)
    c1.set("q1", 5, {"answer": "A"}, "fp_a")
    assert c2.get("q1", 5, "fp_a") == {"answer": "A"}
    assert c2.get("q1", 5, "fp_b") is None  # ACL 指纹隔离


def test_query_cache_memory_backend_unchanged():
    c = QueryCache(maxsize=16, ttl=300)
    c.set("q", 5, {"answer": "A"}, "none")
    assert c.get("q", 5, "none") == {"answer": "A"}
    assert c.get("q", 6, "none") is None
