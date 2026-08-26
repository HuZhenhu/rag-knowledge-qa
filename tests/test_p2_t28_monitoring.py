"""T2.8 监控告警 — Prometheus 指标补齐 + 告警规则文件单测。

覆盖：
- 队列深度 gauge（set 后 Prometheus 文本可导出、覆盖更新）
- 缓存命中率（hit/miss counter + ratio）
- 限流次数 counter（abuse_guard 超限自动联动）
- 租户维度延迟 histogram（p95 分位）
- LLM 成本指标桥接（MetricsCollector.record_llm_usage → cost/token/latency）
- 埋点联动：QueryCache 命中统计 / TieredRateLimiter 超限统计
- 告警规则文件：k8s/monitoring/prometheus-alerts.yaml 可解析且覆盖 P95>3s / 错误率>2% / 队列积压阈值
"""
import threading

import pytest

# metrics_exporter 允许通过环境变量关闭？不——保持默认开但提供 reset 以便断言
pytestmark = pytest.mark.usefixtures("t28_reset_exporter")


@pytest.fixture()
def t28_reset_exporter():
    from src.core import metrics_exporter
    metrics_exporter.reset()
    yield
    metrics_exporter.reset()


# ---------------- 队列深度 gauge ----------------

def test_set_queue_depth_updates_gauge():
    from src.core import metrics_exporter
    metrics_exporter.set_queue_depth("tasks", 5)
    metrics_exporter.set_queue_depth("tasks", 42)  # 覆盖更新
    snapshot = metrics_exporter.snapshot()
    assert snapshot["queue_depth"]["tasks"] == 42


def test_queue_depth_in_prometheus_text():
    from src.core import metrics_exporter
    metrics_exporter.set_queue_depth("tasks", 42)
    text = metrics_exporter.prometheus_text()
    assert "rag_queue_depth{topic=\"tasks\"} 42" in text


# ---------------- 缓存命中率 ----------------

def test_cache_hit_rate_computed():
    from src.core import metrics_exporter

    def emit(hits):
        # 用 RecordWriter 语义：内部 hit/miss 计数 + 记录
        pass

    metrics_exporter.record_cache_access(hit=True)
    metrics_exporter.record_cache_access(hit=True)
    metrics_exporter.record_cache_access(hit=False)
    snapshot = metrics_exporter.snapshot()
    assert snapshot["cache"]["hits"] == 2
    assert snapshot["cache"]["misses"] == 1
    assert abs(snapshot["cache"]["hit_rate"] - 2 / 3) < 1e-6


def test_cache_metrics_in_prometheus_text():
    from src.core import metrics_exporter
    metrics_exporter.record_cache_access(hit=True)
    metrics_exporter.record_cache_access(hit=False)
    text = metrics_exporter.prometheus_text()
    assert "rag_cache_hits_total 1" in text
    assert "rag_cache_misses_total 1" in text


# ---------------- 限流次数 ----------------

def test_rate_limited_counters():
    from src.core import metrics_exporter
    metrics_exporter.inc_rate_limited("ip")
    metrics_exporter.inc_rate_limited("ip")
    metrics_exporter.inc_rate_limited("user")
    snapshot = metrics_exporter.snapshot()
    assert snapshot["rate_limited"] == {"ip": 2, "user": 1, "channel": 0}


def test_rate_limited_in_prometheus_text():
    from src.core import metrics_exporter
    metrics_exporter.inc_rate_limited("channel")
    text = metrics_exporter.prometheus_text()
    assert 'rag_rate_limited_total{dimension="channel"} 1' in text


# ---------------- 租户维度延迟 ----------------

def test_tenant_latency_histogram_p95():
    from src.core import metrics_exporter
    # 0.1~5.0s 拉出明显长尾
    for v in [0.1, 0.2, 0.3, 0.4, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0]:
        metrics_exporter.record_tenant_latency("tenantA", v)
    snapshot = metrics_exporter.snapshot()
    bucket = snapshot["tenant_latency"]["tenantA"]
    assert bucket["count"] == 11
    assert bucket["p95"] >= 4.0  # 长尾被 p95 捕捉


def test_tenant_latency_in_prometheus_text():
    from src.core import metrics_exporter
    metrics_exporter.record_tenant_latency("tenantA", 0.5)
    text = metrics_exporter.prometheus_text()
    assert 'rag_tenant_latency_seconds{tenant="tenantA",' in text
    assert 'quantile="p95"' in text


# ---------------- LLM 成本桥接 ----------------

def test_llm_cost_bridge_from_metrics_collector():
    from src.core import metrics
    from src.core import metrics_exporter
    metrics.MetricsCollector().reset() if hasattr(metrics.MetricsCollector(), "reset") else None
    metrics.MetricsCollector().record_llm_usage(
        latency_ms=1200.0, prompt_tokens=100, completion_tokens=200)
    snapshot = metrics_exporter.snapshot()
    assert snapshot["llm"]["calls"] >= 1
    assert snapshot["llm"]["tokens_total"] >= 300
    assert snapshot["llm"]["cost_usd_total"] > 0
    assert snapshot["llm"]["p95_latency_ms"] >= 1200.0


def test_llm_metrics_in_prometheus_text():
    from src.core import metrics
    from src.core import metrics_exporter
    metrics.MetricsCollector().record_llm_usage(
        latency_ms=800.0, prompt_tokens=50, completion_tokens=50)
    text = metrics_exporter.prometheus_text()
    assert "rag_llm_calls_total" in text
    assert "rag_llm_tokens_total" in text
    assert "rag_llm_cost_usd_total" in text


# ---------------- 埋点联动：QueryCache ----------------

def test_query_cache_records_hit_and_miss():
    from src.core import metrics_exporter
    from src.core.query_cache import QueryCache
    c = QueryCache()
    before = metrics_exporter.snapshot()["cache"]
    c.set("你好", 5, [{"content": "x"}])
    assert c.get("你好", 5) is not None          # hit
    assert c.get("不存在的查询", 5) is None       # miss
    c.get("不存在的查询2", 5)
    after = metrics_exporter.snapshot()["cache"]
    assert after["hits"] - before["hits"] == 1
    assert after["misses"] - before["misses"] == 2


# ---------------- 埋点联动：TieredRateLimiter ----------------

def test_rate_limiter_records_limited_dimension():
    from src.core import metrics_exporter
    from src.core.abuse_guard import TieredRateLimiter
    limiter = TieredRateLimiter(ip_limit=1, user_limit=5, channel_limit=5,
                                window_seconds=60)
    ok1, _ = limiter.allow("ch", ip="1.2.3.4")
    assert ok1 is True
    ok2, _ = limiter.allow("ch", ip="1.2.3.4")  # ip 超限（同窗口内第二次）
    assert ok2 is False
    snapshot = metrics_exporter.snapshot()
    assert snapshot["rate_limited"]["ip"] >= 1


# ---------------- 告警规则文件 ----------------

def test_alert_rules_file_exists_and_parses():
    import yaml
    from pathlib import Path
    rule_file = Path("k8s/monitoring/prometheus-alerts.yaml")
    assert rule_file.exists(), "告警规则文件缺失"
    data = yaml.safe_load(rule_file.read_text(encoding="utf-8"))
    groups = data["groups"]
    assert len(groups) >= 1
    rules = [r for g in groups for r in g["rules"]]
    names = {r["alert"] for r in rules}
    assert "HighP95Latency" in names
    assert "HighErrorRate" in names
    assert "QueueBacklogHigh" in names


def test_alert_rules_cover_required_thresholds():
    import yaml
    from pathlib import Path
    rule_file = Path("k8s/monitoring/prometheus-alerts.yaml")
    data = yaml.safe_load(rule_file.read_text(encoding="utf-8"))
    exprs = " ".join(r["expr"] for g in data["groups"] for r in g["rules"])
    # 阈值语义：P95 > 3s（3000ms）、错误率 > 2%（0.02）、队列积压阈值
    assert "3000" in exprs or "3s" in exprs or "3.0" in exprs
    assert "0.02" in exprs or "0.02 " in exprs or "0.02}" in exprs
    assert "rag_queue_depth" in exprs


def test_worker_queue_depth_gauge_writable():
    """worker 入口里 set_queue_depth 写入的 gauge 可从 snapshot 读取（供探针/采集）。"""
    from src.core import metrics_exporter
    metrics_exporter.set_queue_depth("tasks", 3)
    assert metrics_exporter.snapshot()["queue_depth"]["tasks"] == 3
