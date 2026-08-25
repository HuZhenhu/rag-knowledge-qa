"""P2-7 引用 span / 真实性校验 单元测试"""
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.citation import (  # noqa: E402
    build_citation_spans,
    build_context_with_citations,
    parse_citations,
    validate_citations,
)

_SOURCES = [
    {"content": "甲文档内容", "metadata": {"source_file": "a.md", "section": "1", "page_number": 2, "doc_id": "d1"}},
    {"content": "乙文档内容", "metadata": {"source_file": "b.md", "doc_id": "d2"}},
]


class TestBuildContext:
    def test_context_contains_cit_labels(self):
        ctx, idx = build_context_with_citations(_SOURCES)
        assert "[cit:1:" in ctx and "[cit:2:" in ctx
        assert "a.md" in ctx and "b.md" in ctx

    def test_cit_index_maps_number_to_source(self):
        _ctx, idx = build_context_with_citations(_SOURCES)
        assert idx["1"]["content"] == "甲文档内容"
        assert idx["2"]["metadata"]["doc_id"] == "d2"

    def test_locale_appended(self):
        ctx, _ = build_context_with_citations(_SOURCES)
        assert "第1节" in ctx and "第2页" in ctx


class TestParse:
    def test_parse_single(self):
        ans = "结论[cit:1]结束"
        cited = parse_citations(ans)
        assert cited == [("1", 2, 9)]

    def test_parse_multi_expands(self):
        ans = "见[cit:1,2]"
        cited = parse_citations(ans)
        ids = [c for c, _, _ in cited]
        assert ids == ["1", "2"]

    def test_parse_none(self):
        assert parse_citations("没有引用") == []


class TestValidate:
    def test_valid_citation(self):
        ans = "甲[cit:1]乙[cit:2]"
        vm = validate_citations(parse_citations(ans), build_context_with_citations(_SOURCES)[1])
        assert vm == {"1": True, "2": True}

    def test_hallucinated_id_invalid(self):
        ans = "不存在[cit:9]"
        vm = validate_citations(parse_citations(ans), build_context_with_citations(_SOURCES)[1])
        assert vm["9"] is False

    def test_source_ids_enhancement(self):
        _, idx = build_context_with_citations(_SOURCES)
        ans = "甲[cit:1]"
        vm = validate_citations(parse_citations(ans), idx, source_ids={"d1"})
        assert vm["1"] is True
        # cit:2 对应 doc_id=d2 不在 source_ids 中 → 增强判定无效
        ans2 = "乙[cit:2]"
        vm2 = validate_citations(parse_citations(ans2), idx, source_ids={"d1"})
        assert vm2["2"] is False

    def test_source_ids_empty_falls_back_to_index(self):
        _, idx = build_context_with_citations(_SOURCES)
        ans = "甲[cit:1]"
        vm = validate_citations(parse_citations(ans), idx, source_ids=set())
        assert vm["1"] is True


class TestBuildSpans:
    def test_spans_have_offsets_and_source(self):
        ans = "甲[cit:1]乙[cit:9]"
        _, idx = build_context_with_citations(_SOURCES)
        vm = validate_citations(parse_citations(ans), idx)
        spans = build_citation_spans(ans, idx, vm)
        assert len(spans) == 2
        s1 = spans[0]
        assert s1["citation_id"] == "1"
        assert s1["start"] == 1 and s1["end"] == 8
        assert s1["valid"] is True
        assert s1["source_file"] == "a.md"
        assert s1["section"] == "1"
        assert s1["page_number"] == 2
        assert spans[1]["valid"] is False
