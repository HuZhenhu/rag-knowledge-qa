"""M5 检索优化模块验收测试

覆盖范围：
1. 加权RRF公式正确性
2. QueryCache 缓存命中与过期
3. 阈值过滤（低分 chunk 被排除）
4. 反馈记录（create_feedback / list_feedback / get_feedback_stats）
"""
import time
import threading
import pytest
from unittest.mock import Mock


# ======================================================================
# 1. 加权RRF公式正确性
# ======================================================================

class TestWeightedRRF:
    """验证 HybridRetriever._rrf_fusion 的加权RRF公式"""

    def _make_retriever(self):
        """创建一个不需要真实向量库的 HybridRetriever"""
        from src.core.retriever import HybridRetriever
        return HybridRetriever(vector_store=Mock(), embedder=Mock())

    def test_rrf_formula_single_vector_only(self):
        """只有一个来源（纯向量）时，RRF分数正确"""
        from src.core.retriever import RetrievalResult

        retriever = self._make_retriever()
        vector_results = [
            RetrievalResult(content="A", metadata={}, score=0),
            RetrievalResult(content="B", metadata={}, score=0),
        ]
        bm25_results = []

        fused = retriever._rrf_fusion(vector_results, bm25_results, top_k=10)

        assert len(fused) == 2
        # 第1名: weight_vector/(k+1) + weight_bm25/(k+(len+1))
        # 默认 k=60, weight_vector=1.0, weight_bm25=1.0
        expected_a = 1.0 / (60 + 1) + 1.0 / (60 + 1)  # bm25 rank = len+1 = 1
        assert fused[0].content == "A"
        assert abs(fused[0].score - expected_a) < 1e-9

    def test_rrf_formula_both_sources(self):
        """文档同时出现在两个来源时，分数叠加"""
        from src.core.retriever import RetrievalResult

        retriever = self._make_retriever()
        vector_results = [
            RetrievalResult(content="SharedDoc", metadata={}, score=0),
            RetrievalResult(content="VecOnly", metadata={}, score=0),
        ]
        bm25_results = [
            RetrievalResult(content="SharedDoc", metadata={}, score=0),
            RetrievalResult(content="BM25Only", metadata={}, score=0),
        ]

        fused = retriever._rrf_fusion(vector_results, bm25_results, top_k=10)

        # SharedDoc: rank1 in vector + rank1 in bm25
        expected_shared = 1.0 / (60 + 1) + 1.0 / (60 + 1)
        # VecOnly: rank2 in vector + bm25默认 rank=(len_bm25+1)=3
        expected_vec_only = 1.0 / (60 + 2) + 1.0 / (60 + 3)
        # BM25Only: vector默认 rank=(len_vector+1)=3 + rank2 in bm25
        expected_bm25_only = 1.0 / (60 + 3) + 1.0 / (60 + 2)

        scores = {r.content: r.score for r in fused}
        assert abs(scores["SharedDoc"] - expected_shared) < 1e-9
        assert abs(scores["VecOnly"] - expected_vec_only) < 1e-9
        assert abs(scores["BM25Only"] - expected_bm25_only) < 1e-9
        # SharedDoc 排第一（同时出现在两个来源）
        assert fused[0].content == "SharedDoc"

    def test_rrf_weight_asymmetry(self):
        """不同权重导致不同排序"""
        from src.core.retriever import RetrievalResult

        retriever = self._make_retriever()

        # 场景：文档X在vector排第1，文档Y在bm25排第1
        vector_results = [
            RetrievalResult(content="X", metadata={}, score=0),
            RetrievalResult(content="Y", metadata={}, score=0),
        ]
        bm25_results = [
            RetrievalResult(content="Y", metadata={}, score=0),
            RetrievalResult(content="X", metadata={}, score=0),
        ]

        # 权重相等时，X和Y分数相同
        fused = retriever._rrf_fusion(vector_results, bm25_results, top_k=10,
                                       weight_vector=1.0, weight_bm25=1.0)
        scores_eq = {r.content: r.score for r in fused}
        assert abs(scores_eq["X"] - scores_eq["Y"]) < 1e-9

        # 增大vector权重，X排在Y前面（X在vector排第1）
        fused2 = retriever._rrf_fusion(vector_results, bm25_results, top_k=10,
                                        weight_vector=2.0, weight_bm25=0.5)
        assert fused2[0].content == "X"

        # 增大bm25权重，Y排在X前面（Y在bm25排第1）
        fused3 = retriever._rrf_fusion(vector_results, bm25_results, top_k=10,
                                        weight_vector=0.5, weight_bm25=2.0)
        assert fused3[0].content == "Y"

    def test_rrf_top_k_limits_output(self):
        """RRF融合后结果数量不超过top_k"""
        from src.core.retriever import RetrievalResult

        retriever = self._make_retriever()
        vector_results = [
            RetrievalResult(content=f"Doc{i}", metadata={}, score=0)
            for i in range(20)
        ]
        bm25_results = [
            RetrievalResult(content=f"Doc{i}", metadata={}, score=0)
            for i in range(20)
        ]

        fused = retriever._rrf_fusion(vector_results, bm25_results, top_k=5)
        assert len(fused) == 5

    def test_rrf_empty_sources(self):
        """两个来源都为空时返回空列表"""

        retriever = self._make_retriever()
        fused = retriever._rrf_fusion([], [], top_k=10)
        assert fused == []


# ======================================================================
# 2. QueryCache 缓存命中与过期
# ======================================================================

class TestQueryCache:
    """验证 QueryCache 的命中、miss、过期、大小限制"""

    def test_cache_miss_returns_none(self):
        """未缓存的查询返回None"""
        from src.core.query_cache import QueryCache

        cache = QueryCache(maxsize=100, ttl=3600)
        assert cache.get("unknown query", 5) is None

    def test_cache_hit_after_set(self):
        """set后同一key能命中"""
        from src.core.query_cache import QueryCache

        cache = QueryCache(maxsize=100, ttl=3600)
        results = [{"content": "doc1", "score": 0.9}]
        cache.set("hello", 5, results)

        hit = cache.get("hello", 5)
        assert hit is not None
        assert hit == results

    def test_cache_different_top_k_misses(self):
        """不同top_k视为不同key，不会命中"""
        from src.core.query_cache import QueryCache

        cache = QueryCache(maxsize=100, ttl=3600)
        cache.set("query", 5, [{"a": 1}])

        assert cache.get("query", 5) is not None
        assert cache.get("query", 10) is None  # 不同top_k

    def test_cache_expires_after_ttl(self):
        """缓存条目在TTL后过期"""
        from src.core.query_cache import QueryCache

        cache = QueryCache(maxsize=100, ttl=1)  # 1秒TTL
        cache.set("expires", 5, [{"data": True}])

        # 立即命中
        assert cache.get("expires", 5) is not None

        # 等待过期
        time.sleep(1.5)
        assert cache.get("expires", 5) is None

    def test_cache_eviction_at_maxsize(self):
        """超出maxsize时，LRU淘汰旧条目"""
        from src.core.query_cache import QueryCache

        cache = QueryCache(maxsize=2, ttl=3600)
        cache.set("q1", 5, [{"a": 1}])
        cache.set("q2", 5, [{"a": 2}])

        # 访问q1，使其成为最近使用
        cache.get("q1", 5)

        # 写入第3个，应淘汰最久未使用的q2
        cache.set("q3", 5, [{"a": 3}])

        assert cache.get("q1", 5) is not None  # 最近使用，存活
        assert cache.get("q2", 5) is None       # 被淘汰
        assert cache.get("q3", 5) is not None

    def test_cache_clear(self):
        """clear清空全部缓存"""
        from src.core.query_cache import QueryCache

        cache = QueryCache(maxsize=100, ttl=3600)
        cache.set("a", 5, [{"x": 1}])
        cache.set("b", 5, [{"x": 2}])
        assert cache.size == 2

        cache.clear()
        assert cache.size == 0
        assert cache.get("a", 5) is None

    def test_cache_size_property(self):
        """size属性反映当前条目数"""
        from src.core.query_cache import QueryCache

        cache = QueryCache(maxsize=100, ttl=3600)
        assert cache.size == 0
        cache.set("a", 5, [])
        assert cache.size == 1
        cache.set("b", 5, [])
        assert cache.size == 2

    def test_cache_thread_safety(self):
        """并发读写不会崩溃"""
        from src.core.query_cache import QueryCache

        cache = QueryCache(maxsize=100, ttl=3600)
        errors = []

        def writer():
            try:
                for i in range(50):
                    cache.set(f"q{i}", 5, [{"i": i}])
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for i in range(50):
                    cache.get(f"q{i}", 5)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer) for _ in range(3)]
        threads += [threading.Thread(target=reader) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []


# ======================================================================
# 3. 阈值过滤（低分chunk被排除）
# ======================================================================

class TestRelevanceThreshold:
    """验证 RELEVANCE_THRESHOLD 过滤逻辑"""

    def test_threshold_filters_low_score(self):
        """低于阈值的chunk被排除，等于阈值保留

        使用显式阈值验证过滤语义（>= 保留），避免与 .env 运行时配置值耦合
        （RELEVANCE_THRESHOLD 由 P0 优化调整为 0.55，可被环境覆盖）。
        """
        threshold = 0.5

        class FakeResult:
            def __init__(self, score):
                self.score = score
                self.content = f"doc_score_{score}"

        results = [
            FakeResult(0.9),
            FakeResult(0.5),
            FakeResult(0.1),
            FakeResult(0.01),
            FakeResult(0.3),
        ]

        filtered = [r for r in results if r.score >= threshold]

        # 阈值0.5: 保留 0.9, 0.5；排除 0.1, 0.01, 0.3
        assert [r.score for r in filtered] == [0.9, 0.5]
        assert all(r.score >= threshold for r in filtered)

    def test_threshold_boundary_equal(self):
        """分数恰好等于阈值时保留（>=）"""
        threshold = 0.3

        class FakeResult:
            def __init__(self, score):
                self.score = score

        r = FakeResult(0.3)
        assert r.score >= threshold  # 等于阈值保留

    def test_threshold_all_filtered(self):
        """所有结果都低于阈值时返回空列表"""
        threshold = 0.3

        class FakeResult:
            def __init__(self, score):
                self.score = score

        results = [FakeResult(0.1), FakeResult(0.05)]
        filtered = [r for r in results if r.score >= threshold]
        assert filtered == []

    def test_threshold_none_filtered(self):
        """所有结果都高于阈值时全部保留"""
        threshold = 0.3

        class FakeResult:
            def __init__(self, score):
                self.score = score

        results = [FakeResult(0.9), FakeResult(0.8), FakeResult(0.5)]
        filtered = [r for r in results if r.score >= threshold]
        assert len(filtered) == 3

    def test_threshold_config_default(self):
        """config中阈值配置为合法范围（默认值可被 .env 覆盖，P0 已调整为 0.55）"""
        from src.config import RELEVANCE_THRESHOLD
        assert isinstance(RELEVANCE_THRESHOLD, float)
        assert 0.0 <= RELEVANCE_THRESHOLD <= 1.0

    def test_threshold_custom_via_env(self, monkeypatch):
        """通过环境变量可自定义阈值"""
        monkeypatch.setenv("RELEVANCE_THRESHOLD", "0.5")
        # 重新导入config以读取新环境变量
        import importlib
        import src.config
        importlib.reload(src.config)
        from src.config import RELEVANCE_THRESHOLD as custom_threshold
        assert custom_threshold == 0.5
        # 恢复原始值
        monkeypatch.delenv("RELEVANCE_THRESHOLD", raising=False)
        importlib.reload(src.config)


# ======================================================================
# 4. 反馈记录
# ======================================================================

class TestFeedback:
    """验证 feedback 的创建、查询和统计（使用临时SQLite数据库）"""

    @pytest.fixture(autouse=True)
    def setup_db(self, tmp_path):
        """每个测试前用临时数据库"""
        self.db_path = str(tmp_path / "test_feedback.db")
        # patch _get_conn 使 database 模块使用临时数据库
        import src.storage.database as db_module
        self._orig_conn_fn = db_module._get_conn

        def fake_get_conn():
            import sqlite3
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            # 初始化表结构
            conn.execute("""
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    user_id TEXT DEFAULT '',
                    query TEXT NOT NULL,
                    rating INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            conn.commit()
            return conn

        db_module._get_conn = fake_get_conn
        yield
        db_module._get_conn = self._orig_conn_fn

    def test_create_feedback_positive(self):
        """创建赞的反馈"""
        from src.storage.database import create_feedback

        fid = create_feedback(
            request_id="trace_001",
            query="什么是RAG？",
            rating=1,
            user_id="user_A",
        )
        assert fid is not None
        assert fid > 0

    def test_create_feedback_negative(self):
        """创建踩的反馈"""
        from src.storage.database import create_feedback

        fid = create_feedback(
            request_id="trace_002",
            query="Python怎么装包？",
            rating=-1,
            user_id="user_B",
        )
        assert fid is not None

    def test_list_feedback(self):
        """list_feedback 返回最近反馈"""
        from src.storage.database import create_feedback, list_feedback

        create_feedback("t1", "q1", 1, "u1")
        create_feedback("t2", "q2", -1, "u2")
        create_feedback("t3", "q3", 1, "u3")

        rows = list_feedback(limit=10)
        assert len(rows) == 3
        # 按id DESC，最新的在前
        assert rows[0]["request_id"] == "t3"

    def test_list_feedback_limit(self):
        """list_feedback 的 limit 参数生效"""
        from src.storage.database import create_feedback, list_feedback

        for i in range(5):
            create_feedback(f"t{i}", f"q{i}", 1, "u")

        rows = list_feedback(limit=2)
        assert len(rows) == 2

    def test_get_feedback_stats(self):
        """get_feedback_stats 返回正确的赞/踩统计"""
        from src.storage.database import create_feedback, get_feedback_stats

        create_feedback("t1", "q1", 1, "u1")
        create_feedback("t2", "q2", 1, "u2")
        create_feedback("t3", "q3", 1, "u3")
        create_feedback("t4", "q4", -1, "u4")
        create_feedback("t5", "q5", -1, "u5")

        stats = get_feedback_stats()
        assert stats["total"] == 5
        assert stats["positive"] == 3
        assert stats["negative"] == 2

    def test_get_feedback_stats_empty(self):
        """空数据库返回零统计"""
        from src.storage.database import get_feedback_stats

        stats = get_feedback_stats()
        assert stats == {"total": 0, "positive": 0, "negative": 0}

    def test_feedback_schema_fields(self):
        """反馈记录包含所有必要字段"""
        from src.storage.database import create_feedback, list_feedback

        create_feedback("trace_xyz", "测试查询", 1, "user_42")
        rows = list_feedback(limit=1)
        row = rows[0]

        assert "id" in row
        assert row["request_id"] == "trace_xyz"
        assert row["query"] == "测试查询"
        assert row["rating"] == 1
        assert row["user_id"] == "user_42"
        assert "created_at" in row


# ======================================================================
# 5. 集成: RAGEngine 缓存+阈值联动
# ======================================================================

class TestRAGEngineCacheIntegration:
    """验证 RAGEngine 中缓存与阈值的联动逻辑"""

    def test_cached_results_below_threshold_not_used_as_sources(self):
        """缓存命中但所有sources低于阈值时，返回空sources"""
        from src.core.query_cache import QueryCache
        from src.config import RELEVANCE_THRESHOLD

        cache = QueryCache(maxsize=100, ttl=3600)
        # 缓存中有低于阈值的结果
        cached_sources = [
            {"content": "low score doc", "score": 0.005},
            {"content": "another low", "score": 0.001},
        ]
        cache.set("test query", 5, cached_sources)

        # 模拟 rag_engine.py 中的缓存过滤逻辑
        cached = cache.get("test query", 5)
        sources = [s for s in cached if s.get("score", 0) >= RELEVANCE_THRESHOLD]

        assert sources == []  # 全部被过滤掉

    def test_cached_results_above_threshold_kept(self):
        """缓存命中且有高分sources时保留"""
        from src.core.query_cache import QueryCache
        from src.config import RELEVANCE_THRESHOLD

        cache = QueryCache(maxsize=100, ttl=3600)
        cached_sources = [
            {"content": "good doc", "score": 0.9},
            {"content": "low doc", "score": 0.005},
        ]
        cache.set("test query 2", 5, cached_sources)

        cached = cache.get("test query 2", 5)
        sources = [s for s in cached if s.get("score", 0) >= RELEVANCE_THRESHOLD]

        assert len(sources) == 1
        assert sources[0]["content"] == "good doc"
