"""T2.8 监控指标导出：补齐企业级 Prometheus 指标。

本模块在既有 MetricsCollector（counter/histogram/告警）之上补齐：
- 队列深度 gauge（rag_queue_depth{topic=...}）
- 缓存命中率（rag_cache_hits_total / rag_cache_misses_total + ratio）
- 限流次数（rag_rate_limited_total{dimension=...}，由 abuse_guard 联动）
- 租户维度延迟（rag_tenant_latency_seconds{tenant=...}，直方图含 p95）
- LLM 成本桥接（转发到 MetricsCollector 的 llm_calls / llm_tokens_total / llm_cost_usd_total / llm_latency_ms）

设计要点：
- 模块级单例（线程安全），提供 reset() 便于单测隔离。
- prometheus_text() 输出 Prometheus 文本格式（与 /metrics 端点风格一致的子集）。
- 埋点方（abuse_guard / query_cache / worker）通过本模块函数接入，默认无副作用。
"""
from __future__ import annotations

import threading
from collections import defaultdict

from src.core.metrics import MetricsCollector


class MetricsExporter:
    """Prometheus 指标导出器（补齐 T2.8 缺口）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._gauges: dict[str, dict[str, float]] = defaultdict(dict)      # name -> {labels...: value}
        self._cache_hits = 0
        self._cache_misses = 0
        self._rate_limited: dict[str, int] = defaultdict(int)             # dimension -> count
        self._tenant_latency: dict[str, list[float]] = defaultdict(list)
        self._collector = MetricsCollector()

    # ------------------------------------------------------------------
    # 队列深度 gauge
    # ------------------------------------------------------------------

    def set_queue_depth(self, topic: str, depth: int) -> None:
        with self._lock:
            self._gauges["queue_depth"][str(topic)] = float(depth)

    def _queue_depth(self) -> dict[str, int]:
        with self._lock:
            return {k: int(v) for k, v in self._gauges.get("queue_depth", {}).items()}

    # ------------------------------------------------------------------
    # 缓存命中率
    # ------------------------------------------------------------------

    def record_cache_access(self, hit: bool) -> None:
        with self._lock:
            if hit:
                self._cache_hits += 1
            else:
                self._cache_misses += 1

    @staticmethod
    def _hit_rate(hits: int, misses: int) -> float:
        total = hits + misses
        return hits / total if total else 0.0

    # ------------------------------------------------------------------
    # 限流次数
    # ------------------------------------------------------------------

    def inc_rate_limited(self, dimension: str) -> None:
        with self._lock:
            self._rate_limited[dimension] += 1

    # ------------------------------------------------------------------
    # 租户维度延迟（直方图，p95 长尾可见）
    # ------------------------------------------------------------------

    def record_tenant_latency(self, tenant_id: str, seconds: float) -> None:
        with self._lock:
            self._tenant_latency[tenant_id].append(float(seconds))

    @staticmethod
    def _percentile(values: list[float], pct: float) -> float:
        if not values:
            return 0.0
        s = sorted(values)
        idx = min(int(len(s) * pct), len(s) - 1)
        return s[idx]

    def _tenant_stats(self) -> dict[str, dict]:
        with self._lock:
            out = {}
            for tenant, values in self._tenant_latency.items():
                out[tenant] = {
                    "count": len(values),
                    "avg": round(sum(values) / len(values), 4) if values else 0.0,
                    "p50": round(self._percentile(values, 0.50), 4),
                    "p95": round(self._percentile(values, 0.95), 4),
                    "p99": round(self._percentile(values, 0.99), 4),
                    "max": round(max(values), 4) if values else 0.0,
                }
            return out

    # ------------------------------------------------------------------
    # LLM 成本桥接（转发 MetricsCollector）
    # ------------------------------------------------------------------

    def _llm_snapshot(self) -> dict:
        hist = self._collector.get_histogram("llm_latency_ms")
        return {
            "calls": self._collector.get_counter("llm_calls"),
            "tokens_total": self._collector.get_counter("llm_tokens_total"),
            "cost_usd_total": round(self._collector.get_counter("llm_cost_usd_total"), 6),
            "p95_latency_ms": hist.get("p95", 0.0),
        }

    # ------------------------------------------------------------------
    # 快照 / Prometheus 导出
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            rate = dict(self._rate_limited)
            hits, misses = self._cache_hits, self._cache_misses
        return {
            "queue_depth": self._queue_depth(),
            "cache": {"hits": hits, "misses": misses, "hit_rate": self._hit_rate(hits, misses)},
            "rate_limited": {dim: rate.get(dim, 0) for dim in ("ip", "user", "channel")},
            "tenant_latency": self._tenant_stats(),
            "llm": self._llm_snapshot(),
        }

    def prometheus_text(self) -> str:
        lines: list[str] = []
        qd = self._queue_depth()
        for topic, depth in sorted(qd.items()):
            lines.append(f'rag_queue_depth{{topic="{topic}"}} {depth}')
        with self._lock:
            hits, misses = self._cache_hits, self._cache_misses
            rate = dict(self._rate_limited)
        lines.append(f"rag_cache_hits_total {hits}")
        lines.append(f"rag_cache_misses_total {misses}")
        lines.append(f"rag_cache_hit_rate {round(self._hit_rate(hits, misses), 6)}")
        for dim in sorted(rate):
            lines.append(f'rag_rate_limited_total{{dimension="{dim}"}} {rate[dim]}')
        for tenant, values in sorted(self._tenant_stats().items()):
            for k in ("count", "avg", "p50", "p95", "p99", "max"):
                if k == "count":
                    lines.append(f'rag_tenant_latency_seconds{{tenant="{tenant}",quantile="count"}} {values[k]}')
                else:
                    lines.append(f'rag_tenant_latency_seconds{{tenant="{tenant}",quantile="{k}"}} {values[k]}')
        # LLM 桥接（取自 MetricsCollector）
        lines.append(f"rag_llm_calls_total {self._collector.get_counter('llm_calls')}")
        lines.append(f"rag_llm_tokens_total {self._collector.get_counter('llm_tokens_total')}")
        lines.append(f"rag_llm_cost_usd_total {self._collector.get_counter('llm_cost_usd_total')}")
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------
    # 测试隔离
    # ------------------------------------------------------------------

    def reset(self) -> None:
        with self._lock:
            self._gauges.clear()
            self._cache_hits = 0
            self._cache_misses = 0
            self._rate_limited.clear()
            self._tenant_latency.clear()


# 模块级单例（线程安全），与 MetricsCollector 单例同构
_instance: MetricsExporter | None = None
_lock = threading.Lock()


def _get() -> MetricsExporter:
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = MetricsExporter()
    return _instance


def set_queue_depth(topic: str, depth: int) -> None:
    _get().set_queue_depth(topic, depth)


def record_cache_access(hit: bool) -> None:
    _get().record_cache_access(hit)


def inc_rate_limited(dimension: str) -> None:
    _get().inc_rate_limited(dimension)


def record_tenant_latency(tenant_id: str, seconds: float) -> None:
    _get().record_tenant_latency(tenant_id, seconds)


def snapshot() -> dict:
    return _get().snapshot()


def prometheus_text() -> str:
    return _get().prometheus_text()


def reset() -> None:
    _get().reset()
