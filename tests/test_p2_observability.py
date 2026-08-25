"""P2-8 可观测性增强：token/成本指标、延迟告警阈值、采集接入点 单元测试"""
from collections import defaultdict

import pytest

from src.core.metrics import MetricsCollector, _HistogramBucket


@pytest.fixture(autouse=True)
def _reset_metrics(monkeypatch):
    """metrics 为全局单例，每个用例前重置内部容器保证隔离"""
    m = MetricsCollector()
    monkeypatch.setattr(m, "_counters", defaultdict(int))
    monkeypatch.setattr(m, "_histograms", defaultdict(_HistogramBucket))
    monkeypatch.setattr(m, "_alerts", [])
    monkeypatch.setattr(m, "_window_start", {})
    return m


def test_record_llm_usage_tokens_and_cost():
    m = MetricsCollector()
    m.record_llm_usage(latency_ms=120.0, prompt_tokens=500, completion_tokens=300)
    snap = m.snapshot()
    assert snap["counters"]["llm_calls"] == 1
    assert snap["counters"]["llm_tokens_total"] == 800
    # 成本 = 800/1000 * 0.5 = 0.4（按 config METRICS_COST_USD_PER_1K）
    assert abs(snap["counters"]["llm_cost_usd_total"] - 0.4) < 1e-6
    assert snap["histograms"]["llm_latency_ms"]["count"] == 1
    assert snap["histograms"]["llm_tokens"]["count"] == 1


def test_latency_alert_threshold():
    m = MetricsCollector()
    # 4s 超过阈值（config METRICS_LATENCY_ALERT_SECONDS=3.0）→ 触发 latency_high 告警
    m.record_llm_usage(latency_ms=4000.0, prompt_tokens=100, completion_tokens=50)
    alerts = m.get_recent_alerts()
    assert any(a["type"] == "latency_high" for a in alerts)
    # 短延迟不告警
    m.record_llm_usage(latency_ms=100.0, prompt_tokens=10, completion_tokens=5)
    alerts2 = m.get_recent_alerts()
    latency_alerts = [a for a in alerts2 if a["type"] == "latency_high"]
    assert len(latency_alerts) == 1


def test_snapshot_contains_error_rate():
    m = MetricsCollector()
    m.inc_counter("total_queries", 10)
    m.inc_counter("total_errors", 1)
    snap = m.snapshot()
    assert snap["error_rate_pct"] == 10.0


def test_prometheus_export_contains_cost_metrics():
    m = MetricsCollector()
    m.record_llm_usage(latency_ms=50.0, prompt_tokens=1000, completion_tokens=2000)
    text = m.to_prometheus()
    assert "rag_llm_calls 1" in text
    assert "rag_llm_tokens_total 3000" in text
    assert "rag_llm_cost_usd" in text
    assert "rag_llm_latency_ms_count" in text


def test_config_thresholds_readable():
    """config 中延迟告警与成本配置存在且为数值"""
    import src.config as cfg
    assert float(cfg.METRICS_LATENCY_ALERT_SECONDS) > 0
    assert float(cfg.METRICS_COST_USD_PER_1K) > 0
