"""E2E 测试 — 完整 RAG 链路 mock 化"""
import sys
from unittest.mock import Mock, patch

# 在导入项目模块前，mock 掉重型依赖以避免 torch 导入问题
_MOCK_MODULES = [
    "sentence_transformers",
    "sentence_transformers.SentenceTransformer",
    "sentence_transformers.cross_encoder",
    "sentence_transformers.cross_encoder.CrossEncoder",
    "rank_bm25",
]
_original_modules = {}
for _mod_name in _MOCK_MODULES:
    _original_modules[_mod_name] = sys.modules.get(_mod_name)
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = Mock()

from src.core.rag_engine import RAGEngine  # noqa: E402

# 项目模块导入完成后，恢复被 mock 的 rank_bm25 真实模块：
# retriever 在运行时延迟 `from rank_bm25 import BM25Okapi`，若不恢复会
# 污染同进程后续测试（test_retriever::test_bm25_search 会拿到 Mock 报错）。
if _original_modules.get("rank_bm25") is not None:
    sys.modules["rank_bm25"] = _original_modules["rank_bm25"]
else:
    sys.modules.pop("rank_bm25", None)


def _patch_engine_deps():
    """返回一组 patcher，用于 mock RAGEngine 的所有外部依赖。"""
    return {
        "embedder": patch("src.core.rag_engine.Embedder"),
        "vector_store": patch("src.core.rag_engine.VectorStore"),
        "retriever": patch("src.core.rag_engine.Retriever"),
        "generator": patch("src.core.rag_engine.Generator"),
        "query_understander": patch("src.core.rag_engine.QueryUnderstander"),
        "reranker": patch("src.core.rag_engine.Reranker"),
        "query_cache": patch("src.core.rag_engine.QueryCache"),
    }


def _setup_engine(overrides: dict | None = None):
    """构建一个所有外部依赖都被 mock 的 RAGEngine，并返回 (engine, mock_objects, patches)。"""
    patches = _patch_engine_deps()
    mocks = {}
    for name, p in patches.items():
        mocks[name] = p.start()

    # 让 QueryUnderstander.expand_query 返回原始查询（不触发 LLM）
    mocks["query_understander"].return_value.expand_query.return_value = Mock(
        original_query="test",
        expanded_queries=["test"],
        entities=[],
        intent="",
    )
    # correct_query 默认返回原始查询（不纠错）
    mocks["query_understander"].return_value.correct_query.side_effect = lambda q: q

    # Retriever.retrieve 返回一条结果（走普通 Retriever 路径，mock vector_store.query）
    mocks["vector_store"].return_value.query.return_value = {
        "documents": [["参考内容"]],
        "metadatas": [[{"source": "test.md"}]],
        "distances": [[0.2]],
    }
    mocks["embedder"].return_value.embed_single.return_value = [0.1] * 384

    # Generator.generate 返回固定回答
    mocks["generator"].return_value.generate.return_value = {
        "answer": "这是基于知识库的测试回答[1]。",
        "usage": {"prompt_tokens": 50, "completion_tokens": 30, "total_tokens": 80},
    }

    # QueryCache 默认返回 None（缓存未命中），确保走完整链路
    mocks["query_cache"].return_value.get.return_value = None

    # 应用测试级覆盖
    if overrides:
        for key, value in overrides.items():
            if key in mocks:
                mocks[key].return_value.configure_mock(**value)

    engine = RAGEngine(use_query_expansion=False, use_hyde=False, use_reranker=False)
    # 强制使用普通 Retriever，避免 BM25 mock 问题
    from src.core.retriever import Retriever
    engine.retriever = Retriever(mocks["vector_store"].return_value, mocks["embedder"].return_value)
    return engine, mocks, patches


def _stop_all(patches):
    for p in patches.values():
        p.stop()


class TestFullQueryReturnsAnswer:
    """完整 RAG 链路：查询 -> 检索 -> 生成 -> 带引用的回答"""

    def test_full_query_returns_answer(self):
        engine, mocks, patches = _setup_engine()
        try:
            response = engine.query("什么是RAG？")

            assert response.answer, "回答不应为空"
            assert "测试回答" in response.answer
            assert response.sources is not None
            assert response.timing is not None
        finally:
            _stop_all(patches)


class TestEmptyQueryReturnsNotFound:
    """空查询应优雅处理，不崩溃"""

    def test_empty_query_returns_not_found(self):
        engine, mocks, patches = _setup_engine()
        try:
            # 检索器对空查询返回空结果
            mocks["vector_store"].return_value.query.return_value = {
                "documents": [[]],
                "metadatas": [[]],
                "distances": [[]],
            }

            response = engine.query("")

            assert response.answer
            assert "未找到" in response.answer or "抱歉" in response.answer
        finally:
            _stop_all(patches)


