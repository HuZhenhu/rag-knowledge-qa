"""T2.5 多租户与渠道隔离 — 租户注册 / 租户配额 / 渠道统计 / 综合守卫。

- TenantRegistry: 租户 → 可见 kb_ids 映射
- TenantQuota: 租户配额（文档数 / 会话数 / 请求量），超限自动拒绝（线程安全）
- ChannelStats: 渠道（web/wechat/app/ivr）独立统计
- MultiTenantEnforcer: 综合守卫（检索过滤 / 运行时来源断言 / 配额 / 请求放行）

隔离核心：检索前 build_tenant_filter 按租户可见 kb 过滤，检索后
assert_sources_tenant_allowed 运行时剔除跨租户来源（防绕过，配合渗透测试）。
"""
from __future__ import annotations

import threading
import time
from typing import Any

from src.core.acl import (
    assert_sources_tenant_allowed as _assert_sources_tenant_allowed,
    build_tenant_filter as _build_tenant_filter,
)

# 支持渠道（白名单）
CHANNELS = ("web", "wechat", "app", "ivr")

_DEFAULT_LIMITS = {
    "docs": 10000,          # 每租户文档数上限
    "sessions": 10000,      # 每租户会话数上限
    "requests_per_min": 600,  # 每租户每渠道每分钟请求量上限
}


class TenantRegistry:
    """租户注册表：tenant_id → 可见 kb_ids 集合。"""

    def __init__(self) -> None:
        self._map: dict[str, set[str]] = {}
        self._lock = threading.Lock()

    def register(self, tenant_id: str, kb_ids: list[str] | set[str]) -> None:
        with self._lock:
            self._map[tenant_id] = set(kb_ids)

    def kb_ids_for(self, tenant_id: str) -> set[str]:
        with self._lock:
            return set(self._map.get(tenant_id, set()))

    def has_tenant(self, tenant_id: str) -> bool:
        with self._lock:
            return tenant_id in self._map


class TenantQuota:
    """租户配额。所有配额方法返回 (ok: bool, reason: str)。

    - docs: 每租户文档数上限
    - sessions: 每租户会话数上限
    - requests_per_min: 每租户每渠道每分钟请求量上限（滑动窗口计数）
    """

    def __init__(self, default_limits: dict[str, int] | None = None,
                 tenant_overrides: dict[str, dict[str, int]] | None = None) -> None:
        self._limits = dict(_DEFAULT_LIMITS)
        if default_limits:
            self._limits.update(default_limits)
        self._overrides = dict(tenant_overrides or {})
        self._req_windows: dict[tuple[str, str], list[float]] = {}
        self._lock = threading.Lock()

    def _limit(self, tenant_id: str, key: str) -> int:
        override = self._overrides.get(tenant_id, {})
        return int(override.get(key, self._limits.get(key, 0)))

    def check_doc_quota(self, tenant_id: str, current: int, delta: int = 1) -> tuple[bool, str]:
        limit = self._limit(tenant_id, "docs")
        if current + delta > limit:
            return False, f"doc quota exceeded: {current + delta} > {limit}"
        return True, ""

    def check_session_quota(self, tenant_id: str, current: int) -> tuple[bool, str]:
        limit = self._limit(tenant_id, "sessions")
        if current >= limit:
            return False, f"session quota exceeded: {current} >= {limit}"
        return True, ""

    def consume_request(self, tenant_id: str, channel: str) -> tuple[bool, str]:
        """记录一次请求；超限返回 (False, reason)。滑动窗口：近 60s 内计数。"""
        if channel not in CHANNELS:
            return False, f"unknown channel: {channel}"
        now = time.time()
        key = (tenant_id, channel)
        limit = self._limit(tenant_id, "requests_per_min")
        with self._lock:
            win = self._req_windows.setdefault(key, [])
            # 清理 60s 前的记录
            cutoff = now - 60.0
            win[:] = [t for t in win if t > cutoff]
            if len(win) >= limit:
                return False, f"request quota exceeded for tenant {tenant_id} on {channel}"
            win.append(now)
            return True, ""

    def reset(self) -> None:
        with self._lock:
            self._req_windows.clear()


class ChannelStats:
    """渠道独立统计：requests / errors / avg_latency_ms。线程安全。"""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def record(self, channel: str, tenant_id: str = "default",
               latency_ms: float = 0.0, error: bool = False) -> None:
        with self._lock:
            d = self._data.setdefault(channel, {"requests": 0, "errors": 0,
                                                "latency_sum": 0.0, "tenants": set()})
            d["requests"] += 1
            if error:
                d["errors"] += 1
            d["latency_sum"] += latency_ms
            d["tenants"].add(tenant_id)

    def summary(self, channel: str | None = None) -> dict:
        """按渠道汇总；channel=None 时全渠道合计。"""
        with self._lock:
            channels = [channel] if channel else list(self._data.keys())
        total = {"requests": 0, "errors": 0, "avg_latency_ms": 0.0}
        latency_sum = 0.0
        for ch in channels:
            d = self._data.get(ch)
            if not d:
                continue
            total["requests"] += d["requests"]
            total["errors"] += d["errors"]
            latency_sum += d["latency_sum"]
        if total["requests"]:
            total["avg_latency_ms"] = round(latency_sum / total["requests"], 3)
        return total


class MultiTenantEnforcer:
    """综合守卫：检索过滤 + 运行时来源断言 + 配额 + 渠道统计。"""

    def __init__(self, registry: TenantRegistry | None = None,
                 quota: TenantQuota | None = None,
                 stats: ChannelStats | None = None) -> None:
        self.registry = registry or TenantRegistry()
        self.quota = quota or TenantQuota()
        self.stats = stats or ChannelStats()

    # ---- 检索隔离 ----
    def retrieval_filter(self, tenant_id: str | None, allowed_kb_ids: set[str] | list[str] | None,
                         role: str | None = None, admin_roles: tuple[str, ...] = ("admin",)) -> dict | None:
        """检索前过滤条件（按租户可见 kb）。"""
        return _build_tenant_filter(tenant_id, allowed_kb_ids, role=role, admin_roles=admin_roles)

    def assert_sources_tenant_allowed(self, sources: list[dict], tenant_id: str | None,
                                      allowed_kb_ids: set[str] | list[str] | None) -> tuple[list[dict], int]:
        """运行时剔除跨租户来源（防绕过）。"""
        return _assert_sources_tenant_allowed(sources, tenant_id, allowed_kb_ids)

    # ---- 配额 ----
    def check_doc_quota(self, tenant_id: str, current: int, delta: int = 1) -> tuple[bool, str]:
        return self.quota.check_doc_quota(tenant_id, current, delta)

    def check_session_quota(self, tenant_id: str, current: int) -> tuple[bool, str]:
        return self.quota.check_session_quota(tenant_id, current)

    def allow_request(self, tenant_id: str, channel: str) -> tuple[bool, str]:
        """请求放行：配额 + 渠道校验；放行后记录渠道统计。"""
        if channel not in CHANNELS:
            return False, f"unknown channel: {channel}"
        ok, reason = self.quota.consume_request(tenant_id, channel)
        if ok:
            self.stats.record(channel, tenant_id=tenant_id)
        return ok, reason
