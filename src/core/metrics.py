"""指标收集器 — M4生产监控"""
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field

# P2-8: 延迟告警阈值与成本单价（可在 config 覆盖，这里提供默认避免循环依赖）
_LATENCY_ALERT_SECONDS_DEFAULT = 3.0
_COST_USD_PER_1K_DEFAULT = 0.5


@dataclass
class _HistogramBucket:
    """直方图数据：记录一段时间窗口内的值列表"""
    values: list[float] = field(default_factory=list)

    def record(self, value: float) -> None:
        self.values.append(value)

    def clear(self) -> None:
        self.values.clear()

    @property
    def count(self) -> int:
        return len(self.values)

    @property
    def avg(self) -> float:
        return sum(self.values) / len(self.values) if self.values else 0.0

    @property
    def p50(self) -> float:
        if not self.values:
            return 0.0
        s = sorted(self.values)
        mid = len(s) // 2
        return s[mid]

    @property
    def p95(self) -> float:
        if not self.values:
            return 0.0
        s = sorted(self.values)
        idx = int(len(s) * 0.95)
        return s[min(idx, len(s) - 1)]

    @property
    def p99(self) -> float:
        if not self.values:
            return 0.0
        s = sorted(self.values)
        idx = int(len(s) * 0.99)
        return s[min(idx, len(s) - 1)]

    @property
    def max(self) -> float:
        return max(self.values) if self.values else 0.0

    def to_dict(self) -> dict:
        return {
            "count": self.count,
            "avg": round(self.avg, 2),
            "p50": round(self.p50, 2),
            "p95": round(self.p95, 2),
            "p99": round(self.p99, 2),
            "max": round(self.max, 2),
        }


class MetricsCollector:
    """全局指标收集器（线程安全单例）

    提供计数器（counter）和直方图（histogram）两类指标。
    支持按时间窗口（1min / 5min / 1h）自动聚合。
    """

    _instance: "MetricsCollector | None" = None
    _lock_cls = threading.Lock()

    def __new__(cls) -> "MetricsCollector":
        if cls._instance is None:
            with cls._lock_cls:
                if cls._instance is None:
                    inst = super().__new__(cls)
                    inst._init_fields()
                    cls._instance = inst
        return cls._instance

    def _init_fields(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = defaultdict(int)
        self._histograms: dict[str, _HistogramBucket] = defaultdict(_HistogramBucket)
        # 用于时间窗口重置
        self._window_start: dict[str, float] = {}
        # 告警历史
        self._alerts: list[dict] = []

    # ------------------------------------------------------------------
    # 计数器
    # ------------------------------------------------------------------

    def inc_counter(self, name: str, value: int = 1) -> None:
        """增加计数器"""
        with self._lock:
            self._counters[name] += value

    def get_counter(self, name: str) -> int:
        with self._lock:
            return self._counters.get(name, 0)

    # ------------------------------------------------------------------
    # P2-8: 延迟告警阈值配置（可在 config 覆盖）
    # ------------------------------------------------------------------

    def _latency_alert_seconds(self) -> float:
        try:
            from src.config import METRICS_LATENCY_ALERT_SECONDS
            return float(METRICS_LATENCY_ALERT_SECONDS)
        except Exception:
            return _LATENCY_ALERT_SECONDS_DEFAULT

    # ------------------------------------------------------------------
    # P2-8: token / 成本指标
    # ------------------------------------------------------------------

    def record_llm_usage(self, latency_ms: float, prompt_tokens: int = 0,
                         completion_tokens: int = 0) -> None:
        """记录一次生成调用的延迟、token 与估算成本。

        - 延迟超过阈值自动产生 latency 告警（对齐企业级 P95<3s 目标）。
        - token 与成本按 1min 窗口直方图聚合，供 /metrics 快照与 Prometheus 导出。
        """
        self.inc_counter("llm_calls", 1)
        self.record_histogram("llm_latency_ms", latency_ms)
        total_tokens = int(prompt_tokens or 0) + int(completion_tokens or 0)
        if total_tokens:
            self.inc_counter("llm_tokens_total", total_tokens)
            self.record_histogram("llm_tokens", float(total_tokens))
            cost = total_tokens / 1000.0 * self._cost_per_1k()
            self.inc_counter("llm_cost_usd_total", round(cost, 6))
            self.record_histogram("llm_cost_usd", cost)

        if latency_ms / 1000.0 > self._latency_alert_seconds():
            self.record_alert(
                "latency_high",
                f"生成延迟 {latency_ms:.0f}ms 超过阈值 {self._latency_alert_seconds() * 1000:.0f}ms",
                {"latency_ms": latency_ms, "tokens": total_tokens},
            )

    def _cost_per_1k(self) -> float:
        try:
            from src.config import METRICS_COST_USD_PER_1K
            return float(METRICS_COST_USD_PER_1K)
        except Exception:
            return _COST_USD_PER_1K_DEFAULT

    # ------------------------------------------------------------------
    # 直方图
    # ------------------------------------------------------------------

    def record_histogram(self, name: str, value: float) -> None:
        """记录直方图值"""
        with self._lock:
            self._histograms[name].record(value)

    def get_histogram(self, name: str) -> dict:
        with self._lock:
            bucket = self._histograms.get(name)
            return bucket.to_dict() if bucket else {}

    # ------------------------------------------------------------------
    # 告警
    # ------------------------------------------------------------------

    def record_alert(self, alert_type: str, message: str,
                     details: dict | None = None) -> None:
        """记录告警事件"""
        alert = {
            "type": alert_type,
            "message": message,
            "timestamp": time.time(),
            "details": details or {},
        }
        with self._lock:
            self._alerts.append(alert)
            # 只保留最近1000条告警
            if len(self._alerts) > 1000:
                self._alerts = self._alerts[-1000:]

    def get_recent_alerts(self, limit: int = 50) -> list[dict]:
        with self._lock:
            return list(reversed(self._alerts[-limit:]))

    # ------------------------------------------------------------------
    # 窗口重置
    # ------------------------------------------------------------------

    def maybe_reset_window(self, name: str, window_seconds: float) -> bool:
        """如果当前窗口已过期则重置并返回 True"""
        now = time.time()
        with self._lock:
            start = self._window_start.get(name, 0.0)
            if now - start >= window_seconds:
                self._window_start[name] = now
                if name in self._histograms:
                    self._histograms[name].clear()
                return True
            return False

    # ------------------------------------------------------------------
    # Prometheus 导出
    # ------------------------------------------------------------------

    def to_prometheus(self) -> str:
        """将当前指标导出为 Prometheus exposition format 文本。

        支持 counter（只增不减的计数器）和 gauge（直方图摘要值）两类指标。
        """
        lines: list[str] = []

        with self._lock:
            # --- counters ---
            for name, value in sorted(self._counters.items()):
                lines.append(f"rag_{name} {value}")
            # --- histogram summaries as gauges ---
            for name, bucket in sorted(self._histograms.items()):
                if not bucket.values:
                    continue
                base = f"rag_{name}"
                lines.append(f"{base}_count {bucket.count}")
                lines.append(f"{base}_avg {bucket.avg:.2f}")
                lines.append(f"{base}_p50 {bucket.p50:.2f}")
                lines.append(f"{base}_p95 {bucket.p95:.2f}")
                lines.append(f"{base}_p99 {bucket.p99:.2f}")
                lines.append(f"{base}_max {bucket.max:.2f}")

        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------
    # 全量快照
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        """返回当前所有指标的快照"""
        with self._lock:
            counters = dict(self._counters)
            histograms = {k: v.to_dict() for k, v in self._histograms.items()}
            total_queries = counters.get("total_queries", 0)
            total_errors = counters.get("total_errors", 0)
            error_rate = (total_errors / total_queries * 100
                          if total_queries > 0 else 0.0)
            return {
                "counters": counters,
                "histograms": histograms,
                "error_rate_pct": round(error_rate, 2),
            }


# 全局单例
metrics = MetricsCollector()
