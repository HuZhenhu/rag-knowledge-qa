"""T2.6 防滥用 — 分级限流(IP/用户/渠道) / 敏感查询审计。

- TieredRateLimiter: 滑动窗口按 IP / 用户 / 渠道 三级限流，任一级超限即拒绝
- SensitiveQueryAuditor: 敏感查询审计（内存缓冲，可检索）；命中敏感模式打 flagged
"""
from __future__ import annotations

import re
import threading
import time
from collections import defaultdict, deque
from typing import Any

from src.core.metrics_exporter import inc_rate_limited

# 敏感查询模式（命中即审计标记）
SENSITIVE_PATTERNS: tuple[str, ...] = (
    r"密码",
    r"口令",
    r"银行卡|信用卡|卡号",
    r"身份证",
    r"手机号|电话号码|联系方式",
    r"token|secret|api\s*key",
    r"薪水|工资|薪酬|月薪",
    r"医保|社保号",
    r"转账|汇款|余额",
)


class TieredRateLimiter:
    """分级限流：滑动窗口（秒级）。key = (维度, 标识)。

    allow() 返回 (ok: bool, reason: str)。任一级超限返回 False 并注明命中的维度。
    window_seconds <= 0 时视为立即重置（每次请求独立窗口，用于测试）。
    """

    def __init__(self, ip_limit: int = 60, user_limit: int = 600,
                 channel_limit: int = 1200, window_seconds: int = 60) -> None:
        self.ip_limit = ip_limit
        self.user_limit = user_limit
        self.channel_limit = channel_limit
        self.window_seconds = window_seconds
        self._hits: dict[tuple[str, str], deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: tuple[str, str], now: float) -> None:
        if self.window_seconds <= 0:
            self._hits[key].clear()
            return
        cutoff = now - self.window_seconds
        dq = self._hits[key]
        while dq and dq[0] <= cutoff:
            dq.popleft()

    def _check(self, dim: str, ident: str, limit: int, now: float) -> bool:
        key = (dim, ident)
        with self._lock:
            self._prune(key, now)
            dq = self._hits[key]
            if len(dq) >= limit:
                return False
            dq.append(now)
            return True

    def allow(self, channel: str, ip: str = "", user: str = "") -> tuple[bool, str]:
        now = time.time()
        if ip:
            if not self._check("ip", ip, self.ip_limit, now):
                inc_rate_limited("ip")  # T2.8 限流次数指标
                return False, f"ip rate limit exceeded: {ip}"
        if user:
            if not self._check("user", user, self.user_limit, now):
                inc_rate_limited("user")  # T2.8 限流次数指标
                return False, f"user rate limit exceeded: {user}"
        if channel:
            if not self._check("channel", channel, self.channel_limit, now):
                inc_rate_limited("channel")  # T2.8 限流次数指标
                return False, f"channel rate limit exceeded: {channel}"
        return True, ""

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


class SensitiveQueryAuditor:
    """敏感查询审计：记录查询事件到内存缓冲，命中敏感模式标记 flagged。

    生产可替换为落盘/审计日志后端（本实现聚焦可单测的内存缓冲）。
    """

    def __init__(self, max_events: int = 10000) -> None:
        self._events: list[dict[str, Any]] = []
        self._max_events = max_events
        self._lock = threading.Lock()
        self._patterns = [re.compile(p, re.IGNORECASE) for p in SENSITIVE_PATTERNS]

    def _flag(self, query: str) -> bool:
        return any(p.search(query) for p in self._patterns)

    def record(self, user: str, channel: str, query: str, ip: str = "",
               result: str = "") -> dict[str, Any]:
        event = {
            "ts": time.time(),
            "user": user,
            "channel": channel,
            "ip": ip,
            "query": query,
            "flagged": self._flag(query),
            "result": result,
        }
        with self._lock:
            self._events.append(event)
            if len(self._events) > self._max_events:
                self._events = self._events[-self._max_events:]
        return event

    def search(self, keyword: str) -> list[dict[str, Any]]:
        kw = keyword.lower()
        with self._lock:
            return [e for e in self._events if kw in e["query"].lower()]

    def all_events(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)

    def flagged_count(self) -> int:
        with self._lock:
            return sum(1 for e in self._events if e["flagged"])
