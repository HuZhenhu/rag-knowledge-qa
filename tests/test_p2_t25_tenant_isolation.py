"""Phase 2 T2.5 多租户与渠道隔离 — 单测（含租户隔离渗透测试）

覆盖：
- 租户级 chunk 元数据注入（tenant_id / kb_id）
- build_tenant_filter：租户可见 kb 过滤条件；无权限租户恒空
- 【渗透测试】租户 A 无法检索租户 B 数据：检索结果跨租户来源被剔除
- 管理角色豁免
- 租户配额：文档数 / 会话数 / 请求量超限自动拒绝
- 渠道（web/wechat/app）独立配置与统计
- Session 会话按租户隔离（归属可回读）

外部依赖均用内存实现，无 Redis/Kafka/Milvus 依赖。
"""
from __future__ import annotations


# ======================================================================
# 1. 租户级元数据与检索过滤（acl 扩展）
# ======================================================================

def test_enrich_acl_metadata_injects_tenant_and_kb():
    from src.core.acl import enrich_acl_metadata
    meta = enrich_acl_metadata({"source_file": "kbA/1.txt"}, owner_user_id="u1",
                               kb_id="kbA", tenant_id="tenantA")
    assert meta["tenant_id"] == "tenantA"
    assert meta["kb_id"] == "kbA"
    assert meta["owner_user_id"] == "u1"


def test_build_tenant_filter_visible_kbs():
    from src.core.acl import build_tenant_filter
    f = build_tenant_filter("tenantA", kb_ids={"kbA1", "kbA2"})
    assert f == {"kb_id": {"$in": ["kbA1", "kbA2"]}}


def test_build_tenant_filter_no_access_is_empty():
    from src.core.acl import build_tenant_filter
    f = build_tenant_filter("tenantA", kb_ids=set())
    assert f == {"kb_id": {"$eq": "__no_access__"}}


def test_build_tenant_filter_admin_exempt():
    from src.core.acl import build_tenant_filter
    f = build_tenant_filter("tenantA", kb_ids=set(), role="admin", admin_roles=("admin",))
    assert f is None  # 管理角色豁免过滤


# ======================================================================
# 2. 【渗透测试】租户 A 无法检索租户 B 数据
# ======================================================================

def test_tenant_isolation_penetration_a_cannot_read_b():
    """租户 A 的检索结果中，租户 B 的文档来源必须全部被剔除。"""
    from src.core.tenant_guard import MultiTenantEnforcer
    enforcer = MultiTenantEnforcer()

    sources = [
        {"metadata": {"doc_id": "a1", "kb_id": "kbA", "tenant_id": "tenantA"}},
        {"metadata": {"doc_id": "a2", "kb_id": "kbA", "tenant_id": "tenantA"}},
        {"metadata": {"doc_id": "b1", "kb_id": "kbB", "tenant_id": "tenantB"}},
        {"metadata": {"doc_id": "b2", "kb_id": "kbB", "tenant_id": "tenantB"}},
    ]
    kept, removed = enforcer.assert_sources_tenant_allowed(
        sources, tenant_id="tenantA", allowed_kb_ids={"kbA"}
    )
    assert removed == 2  # 两条租户 B 数据被剔除
    assert {s["metadata"]["doc_id"] for s in kept} == {"a1", "a2"}


def test_tenant_isolation_retrieval_filter_excludes_other_tenant():
    """检索前过滤：租户 A 的过滤条件不匹配租户 B 的任何 doc/kb。"""
    from src.core.acl import build_tenant_filter
    from src.core.tenant_guard import MultiTenantEnforcer

    enforcer = MultiTenantEnforcer()
    acl_filter = enforcer.retrieval_filter("tenantA", allowed_kb_ids={"kbA"})
    assert acl_filter == build_tenant_filter("tenantA", kb_ids={"kbA"})
    # 租户 B 的 kb 不在过滤条件内
    assert "kbB" not in str(acl_filter)


def test_tenant_isolation_quota_rejects_cross_tenant_overflow():
    """租户配额：超限租户自动拒绝（不影响其他租户）。"""
    from src.core.tenant_guard import MultiTenantEnforcer, TenantQuota
    quota = TenantQuota(default_limits={"docs": 2})
    enforcer = MultiTenantEnforcer(quota=quota)
    # 租户 A 占满配额
    ok, _ = enforcer.check_doc_quota("tenantA", current=1)
    assert ok
    ok, _ = enforcer.check_doc_quota("tenantA", current=1, delta=1)
    assert ok
    ok, reason = enforcer.check_doc_quota("tenantA", current=2, delta=1)
    assert ok is False and "quota" in reason
    # 租户 B 不受影响
    ok, _ = enforcer.check_doc_quota("tenantB", current=0)
    assert ok


# ======================================================================
# 3. 租户配额
# ======================================================================

def test_tenant_quota_requests_per_minute():
    from src.core.tenant_guard import TenantQuota
    quota = TenantQuota(default_limits={"requests_per_min": 2})
    ok, _ = quota.consume_request("tenantA", "web")
    assert ok
    ok, _ = quota.consume_request("tenantA", "web")
    assert ok
    ok, reason = quota.consume_request("tenantA", "web")
    assert ok is False and "request" in reason


def test_tenant_quota_session_limit():
    from src.core.tenant_guard import TenantQuota
    quota = TenantQuota(default_limits={"sessions": 3})
    ok, _ = quota.check_session_quota("tenantA", current=2)
    assert ok
    ok, reason = quota.check_session_quota("tenantA", current=3)
    assert ok is False


# ======================================================================
# 4. 渠道独立配置与统计
# ======================================================================

def test_channel_stats_isolated_per_channel():
    from src.core.tenant_guard import ChannelStats
    stats = ChannelStats()
    stats.record("web", tenant_id="t1", latency_ms=120)
    stats.record("web", tenant_id="t1", latency_ms=180)
    stats.record("wechat", tenant_id="t1", latency_ms=90)
    stats.record("app", tenant_id="t1", error=True)

    web = stats.summary("web")
    assert web["requests"] == 2
    assert web["errors"] == 0
    assert web["avg_latency_ms"] == 150

    wechat = stats.summary("wechat")
    assert wechat["requests"] == 1

    app = stats.summary("app")
    assert app["errors"] == 1

    all_ = stats.summary()
    assert all_["requests"] == 4


def test_channel_config_supports_known_channels():
    """渠道独立配置：web/wechat/app 均在白名单内。"""
    from src.core.tenant_guard import CHANNELS
    assert {"web", "wechat", "app"}.issubset(set(CHANNELS))


# ======================================================================
# 5. Session 会话按租户隔离
# ======================================================================

def test_session_tenant_ownership_roundtrip():
    """会话归属租户可回读；不同租户会话互不可见（后端按 tenant 隔离查询）。"""
    from src.core.session import SessionManager
    sm = SessionManager(backend=None, timeout_minutes=60)
    s = sm.get_or_create_session("sess-a", tenant_id="tenantA", channel="web")
    assert s.tenant_id == "tenantA" and s.channel == "web"
    sm.add_message("sess-a", "user", "hello", tenant_id="tenantA")
    loaded = sm.get_or_create_session("sess-a", tenant_id="tenantA", channel="web")
    assert loaded.tenant_id == "tenantA"
    assert loaded.messages[-1].content == "hello"
