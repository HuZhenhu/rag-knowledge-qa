"""P0-1 检索链路 ACL/租户隔离 单元测试"""
import pytest

from src.core.acl import (
    compute_doc_id,
    enrich_acl_metadata,
    build_acl_filter,
    allowed_doc_ids_from_filter,
    assert_sources_allowed,
)


def test_compute_doc_id_consistent():
    a = compute_doc_id("docs/a.md")
    b = compute_doc_id("docs/a.md")
    c = compute_doc_id("docs/b.md")
    assert a == b
    assert a != c
    assert len(a) == 32  # md5 hex


def test_compute_doc_id_empty():
    assert compute_doc_id("") == ""
    assert compute_doc_id(None) == ""


def test_enrich_acl_metadata_fills_defaults():
    meta = enrich_acl_metadata({"source_file": "docs/x.md"})
    assert meta["doc_id"] == compute_doc_id("docs/x.md")
    assert meta["owner_user_id"] == ""
    assert "reader" in meta["allowed_roles"]
    assert "admin" in meta["allowed_roles"]
    assert meta["kb_id"] == ""


def test_enrich_acl_metadata_preserves_existing():
    meta = enrich_acl_metadata(
        {"doc_id": "existing", "owner_user_id": "u1", "allowed_roles": "admin"},
        owner_user_id="should-not-override",
    )
    assert meta["doc_id"] == "existing"
    assert meta["owner_user_id"] == "u1"
    assert meta["allowed_roles"] == "admin"


def test_enrich_acl_metadata_supports_source_key():
    meta = enrich_acl_metadata({"source": "docs/y.md"})
    assert meta["doc_id"] == compute_doc_id("docs/y.md")


def test_build_acl_filter_admin_exempt():
    assert build_acl_filter(["d1"], role="admin", admin_roles=("admin",)) is None


def test_build_acl_filter_none_readable_exempt():
    assert build_acl_filter(None, role="reader", admin_roles=("admin",)) is None


def test_build_acl_filter_empty_never_match():
    f = build_acl_filter([], role="reader", admin_roles=("admin",))
    assert f == {"doc_id": {"$eq": "__no_access__"}}


def test_build_acl_filter_in_list():
    f = build_acl_filter(["d1", "d2"], role="reader", admin_roles=("admin",))
    assert f == {"doc_id": {"$in": ["d1", "d2"]}}


def test_allowed_doc_ids_from_filter_in():
    assert allowed_doc_ids_from_filter({"doc_id": {"$in": ["a", "b"]}}) == {"a", "b"}


def test_allowed_doc_ids_from_filter_eq():
    assert allowed_doc_ids_from_filter({"doc_id": {"$eq": "__no_access__"}}) == {"__no_access__"}


def test_allowed_doc_ids_from_filter_none():
    assert allowed_doc_ids_from_filter(None) is None
    assert allowed_doc_ids_from_filter({"kb_id": {"$eq": "kb1"}}) is None


def test_assert_sources_allowed_none_passthrough():
    sources = [{"metadata": {"doc_id": "d1"}}]
    kept, removed = assert_sources_allowed(sources, None)
    assert kept == sources
    assert removed == 0


def test_assert_sources_allowed_removes_violation():
    """越权来源（doc_id 不在允许集合内）必须被剔除"""
    sources = [
        {"metadata": {"doc_id": "d1"}},
        {"metadata": {"doc_id": "d2"}},  # 越权
        {"metadata": {"doc_id": "d1"}},
    ]
    kept, removed = assert_sources_allowed(sources, {"d1"})
    assert removed == 1
    assert all(s["metadata"]["doc_id"] == "d1" for s in kept)


def test_assert_sources_allowed_empty_allowed():
    sources = [{"metadata": {"doc_id": "d1"}}]
    kept, removed = assert_sources_allowed(sources, set())
    assert kept == []
    assert removed == 1
