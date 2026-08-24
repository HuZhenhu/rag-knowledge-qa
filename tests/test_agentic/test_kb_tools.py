"""知识库检索工具 schema 测试（设计文档 §7.1 test_kb_tools.py）。

使用注入的伪造检索器，避免真实向量库依赖，自带独立 fixture 不污染全局状态。
M2 新增：按类别检索 / 元数据过滤 / 多文档对比 三类工具的 schema 与行为测试。
"""
from src.core.retriever import RetrievalResult
from src.core.agentic.tools.kb_tools import KBTools


class FakeVectorStore:
    """伪造底层向量库：get_all 返回全量片段。"""
    def __init__(self, chunks):
        # chunks: [{"content":..., "metadata":{...}}]
        self._chunks = chunks or []

    def get_all(self):
        return {
            "documents": [c["content"] for c in self._chunks],
            "metadatas": [c["metadata"] for c in self._chunks],
        }


class FakeRetriever:
    def __init__(self, results, vector_store=None):
        self._results = results
        self.vector_store = vector_store

    def retrieve(self, query, top_k=5):
        return self._results[:top_k]


class FakeEmbedder:
    """确定性伪 embedding：按内容首字映射到固定向量。"""
    def embed_single(self, text):
        text = text or ""
        first = text[0] if text else "?"
        return [ord(first) % 7 + 1.0, 1.0]


def _mk_result(content, source_file="", section="", score=0.9, **extra_meta):
    meta = {"source_file": source_file, "section": section, **extra_meta}
    return RetrievalResult(content=content, metadata=meta, score=score)


def test_kb_search_keyword_schema():
    retriever = FakeRetriever([
        _mk_result("FastAPI 是一个现代 Web 框架", "技术栈.md", "Web框架章节", 0.95),
        _mk_result("Flask 是轻量级框架", "技术栈.md", "Web框架章节", 0.88),
    ])
    tools = KBTools(retriever, reranker=None)
    out = tools.kb_search_keyword("FastAPI 特点", top_k=5)

    assert "query" in out and "results" in out
    assert len(out["results"]) == 2
    for ev in out["results"]:
        assert "content" in ev
        assert "metadata" in ev
        assert "score" in ev
        assert ev["metadata"].get("source_file") == "技术栈.md"


def test_kb_search_top_k():
    retriever = FakeRetriever([
        _mk_result("doc1", "a.md", "s1", 0.9),
        _mk_result("doc2", "a.md", "s2", 0.8),
        _mk_result("doc3", "b.md", "s3", 0.7),
    ])
    tools = KBTools(retriever, reranker=None, top_k=2)
    out = tools.kb_search_keyword("查询", top_k=2)
    assert len(out["results"]) == 2


def test_kb_search_no_result():
    tools = KBTools(FakeRetriever([]), reranker=None)
    out = tools.kb_search_keyword("不存在的内容")
    assert out["results"] == []


def test_kb_list_documents_fallback():
    """vector_store 缺失时不应抛异常，返回空列表。"""
    tools = KBTools(FakeRetriever([]), reranker=None)
    out = tools.kb_list_documents()
    assert "documents" in out


def test_call_tool_dispatch_and_unknown():
    retriever = FakeRetriever([_mk_result("内容", "a.md", "s", 0.9)])
    tools = KBTools(retriever, reranker=None)
    assert "results" in tools.call_tool("kb_search_keyword", query="q")
    assert "documents" in tools.call_tool("kb_list_documents")
    try:
        tools.call_tool("unknown_tool")
        assert False, "应抛出 ValueError"
    except ValueError:
        pass


def test_tools_registry():
    retriever = FakeRetriever([])
    tools = KBTools(retriever, reranker=None)
    names = {t["name"] for t in tools.tools}
    assert "kb_search_keyword" in names
    assert "kb_list_documents" in names


# ---------------------------------------------------------------- M2 新工具
def test_kb_search_category_filters_by_category():
    retriever = FakeRetriever([
        _mk_result("刑法条文内容", "刑法.pdf", "总则", 0.9, category="法律法规"),
        _mk_result("民法典内容", "民法典.pdf", "总则", 0.85, category="法律法规"),
        _mk_result("FastAPI 技术细节", "技术栈.md", "Web", 0.8, category="技术文档"),
    ])
    tools = KBTools(retriever, reranker=None)
    out = tools.kb_search_category("条文", category="法律法规")
    assert len(out["results"]) == 2
    assert all(e["metadata"].get("category") == "法律法规" for e in out["results"])
    assert "query" in out and "results" in out


def test_kb_search_category_no_match():
    retriever = FakeRetriever([
        _mk_result("FastAPI 技术细节", "技术栈.md", "Web", 0.8, category="技术文档"),
    ])
    tools = KBTools(retriever, reranker=None)
    out = tools.kb_search_category("条文", category="法律法规")
    assert out["results"] == []
    assert "note" in out


def test_kb_search_category_defaults_to_keyword():
    """category 为空时应退化为关键词检索。"""
    retriever = FakeRetriever([_mk_result("内容", "a.md", "s", 0.9)])
    tools = KBTools(retriever, reranker=None)
    out = tools.kb_search_category("查询", category="")
    assert len(out["results"]) == 1


def test_kb_filter_metadata_by_source():
    vs = FakeVectorStore([
        {"content": "预算说明：100万", "metadata": {"source_file": "预算.md", "section": "s1"}},
        {"content": "预算说明：200万", "metadata": {"source_file": "预算.md", "section": "s2"}},
        {"content": "技术方案", "metadata": {"source_file": "技术栈.md", "section": "s3"}},
    ])
    retriever = FakeRetriever([], vector_store=vs)
    tools = KBTools(retriever, reranker=None, embedder=FakeEmbedder())
    out = tools.kb_filter_metadata("预算", source="预算.md")
    assert len(out["results"]) == 2
    assert all(e["metadata"].get("source_file") == "预算.md" for e in out["results"])
    assert all("score" in e for e in out["results"])


def test_kb_filter_metadata_no_match():
    vs = FakeVectorStore([
        {"content": "技术方案", "metadata": {"source_file": "技术栈.md", "section": "s3"}},
    ])
    retriever = FakeRetriever([], vector_store=vs)
    tools = KBTools(retriever, reranker=None)
    out = tools.kb_filter_metadata("查询", source="不存在的.md")
    assert out["results"] == []
    assert "note" in out


def test_kb_filter_metadata_fallback_keyword_sorting():
    """embedder 缺失时应回退关键词命中排序，不抛异常。"""
    vs = FakeVectorStore([
        {"content": "合同金额 100 万元", "metadata": {"source_file": "合同.md", "author": "张三"}},
        {"content": "无关内容", "metadata": {"source_file": "合同.md", "author": "张三"}},
    ])
    retriever = FakeRetriever([], vector_store=vs)
    tools = KBTools(retriever, reranker=None)
    out = tools.kb_filter_metadata("合同金额", author="张三")
    assert len(out["results"]) == 2
    # 关键词命中更多者排前
    assert out["results"][0]["content"] == "合同金额 100 万元"


def test_kb_compare_documents_returns_both_sides():
    vs = FakeVectorStore([
        {"content": "FastAPI 高性能异步框架", "metadata": {"source_file": "fastapi.md"}},
        {"content": "FastAPI 自动文档", "metadata": {"source_file": "fastapi.md"}},
        {"content": "Flask 轻量灵活", "metadata": {"source_file": "flask.md"}},
        {"content": "Flask 生态简单", "metadata": {"source_file": "flask.md"}},
    ])
    retriever = FakeRetriever([], vector_store=vs)
    tools = KBTools(retriever, reranker=None, embedder=FakeEmbedder())
    out = tools.kb_compare_documents("fastapi.md", "flask.md", query="框架特点", top_k=2)
    assert len(out["results"]) == 4  # 每文档各 2 条
    sources = {e["metadata"].get("source_file") for e in out["results"]}
    assert sources == {"fastapi.md", "flask.md"}


def test_kb_compare_documents_missing_side_note():
    vs = FakeVectorStore([
        {"content": "FastAPI 高性能", "metadata": {"source_file": "fastapi.md"}},
    ])
    retriever = FakeRetriever([], vector_store=vs)
    tools = KBTools(retriever, reranker=None)
    out = tools.kb_compare_documents("fastapi.md", "flask.md")
    assert len(out["results"]) == 1
    assert "flask" in (out.get("note") or "").lower()


def test_kb_list_documents_with_category_filter():
    vs = FakeVectorStore([
        {"content": "刑法条文", "metadata": {"source_file": "刑法.pdf", "category": "法律法规"}},
        {"content": "FastAPI", "metadata": {"source_file": "技术栈.md", "category": "技术文档"}},
    ])
    retriever = FakeRetriever([], vector_store=vs)
    tools = KBTools(retriever, reranker=None)
    out = tools.kb_list_documents(category="法律法规")
    assert out["documents"] == ["刑法.pdf"]


def test_call_tool_dispatch_new_tools():
    vs = FakeVectorStore([
        {"content": "刑法条文", "metadata": {"source_file": "刑法.pdf", "category": "法律法规"}},
        {"content": "FastAPI", "metadata": {"source_file": "技术栈.md"}},
    ])
    retriever = FakeRetriever([_mk_result("刑法内容", "刑法.pdf", "总则", 0.9, category="法律法规")],
                              vector_store=vs)
    tools = KBTools(retriever, reranker=None)
    assert "results" in tools.call_tool("kb_search_category", query="条文", category="法律法规")
    assert "results" in tools.call_tool("kb_filter_metadata", query="条文", source="刑法.pdf")
    assert "results" in tools.call_tool("kb_compare_documents", doc_a="刑法.pdf", doc_b="技术栈.md")


def test_tools_registry_has_five_tools():
    retriever = FakeRetriever([])
    tools = KBTools(retriever, reranker=None)
    names = {t["name"] for t in tools.tools}
    assert names == {
        "kb_search_keyword", "kb_search_category", "kb_filter_metadata",
        "kb_list_documents", "kb_compare_documents",
    }
