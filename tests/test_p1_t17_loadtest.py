"""T1.7 压测脚本 TDD 测试（红）

验收映射（enterprise-rag-scale-plan.md T1.7）：
- scripts/loadtest.py：pytest+httpx 并发脚本，20 并发提问（混合简单/多跳/超纲），
  默认 15 分钟，输出压测报告存档
- 验收指标：P95 < 3s，错误率 < 1%，内存稳定
本测试验证脚本的纯逻辑（不依赖真实服务/网络）：
- 场景问题集：混合简单/多跳/超纲三类，比例可配置
- 统计聚合：P95/平均/错误率计算正确
- 验收判定：阈值逻辑（P95<3s、错误率<1%）与报告渲染
"""
import time

import pytest


def _import_loadtest():
    import importlib
    mod = importlib.import_module("scripts.loadtest")
    importlib.reload(mod)
    return mod


# ======================================================================
# 场景构建：混合简单/多跳/超纲
# ======================================================================

def test_build_scenarios_returns_mixed_types():
    mod = _import_loadtest()
    scenarios = mod.build_scenarios()
    assert isinstance(scenarios, list) and len(scenarios) >= 10
    kinds = {s["kind"] for s in scenarios}
    assert "simple" in kinds
    assert "multi_hop" in kinds
    assert "out_of_scope" in kinds
    # 每个场景必须携带问题文本
    for s in scenarios:
        assert s["question"].strip()


def test_build_scenarios_respects_ratio():
    mod = _import_loadtest()
    scenarios = mod.build_scenarios(total=200, simple_ratio=0.5, multi_hop_ratio=0.3,
                                    out_of_scope_ratio=0.2)
    assert len(scenarios) == 200
    n_simple = sum(1 for s in scenarios if s["kind"] == "simple")
    n_multi = sum(1 for s in scenarios if s["kind"] == "multi_hop")
    n_ooo = sum(1 for s in scenarios if s["kind"] == "out_of_scope")
    assert n_simple == 100
    assert n_multi == 60
    assert n_ooo == 40


# ======================================================================
# 统计聚合：P95 / 平均 / 错误率
# ======================================================================

def test_compute_stats_p95_and_error_rate():
    mod = _import_loadtest()
    # 100 条延迟：前 95 条 1s，后 5 条 4s -> P95 应为 4s（95% 分位）
    latencies = [1.0] * 95 + [4.0] * 5
    errors = [False] * 99 + [True]  # 1% 错误
    stats = mod.compute_stats(latencies, errors, wall_seconds=60.0)
    assert stats["count"] == 100
    assert stats["p95"] == pytest.approx(4.0, abs=0.001)
    assert stats["error_rate"] == pytest.approx(0.01, abs=1e-6)
    assert stats["qps"] == pytest.approx(round(100 / 60.0, 4), abs=1e-6)
    assert stats["mean"] == pytest.approx(1.15, abs=1e-6)


def test_compute_stats_empty_inputs():
    mod = _import_loadtest()
    stats = mod.compute_stats([], [], wall_seconds=1.0)
    assert stats["count"] == 0
    assert stats["error_rate"] == 0.0
    assert stats["p95"] == 0.0


# ======================================================================
# 验收判定：P95 < 3s，错误率 < 1%
# ======================================================================

def test_check_acceptance_pass():
    mod = _import_loadtest()
    ok, problems = mod.check_acceptance({"count": 100, "p95": 2.0, "error_rate": 0.003})
    assert ok is True
    assert problems == []


def test_check_acceptance_fail_on_p95():
    mod = _import_loadtest()
    ok, problems = mod.check_acceptance({"count": 100, "p95": 3.5, "error_rate": 0.003})
    assert ok is False
    assert any("P95" in p for p in problems)


def test_check_acceptance_fail_on_error_rate():
    mod = _import_loadtest()
    ok, problems = mod.check_acceptance({"count": 100, "p95": 1.0, "error_rate": 0.02})
    assert ok is False
    assert any("错误率" in p for p in problems)


# ======================================================================
# 报告渲染：输出含关键指标
# ======================================================================

def test_render_report_contains_metrics(tmp_path):
    mod = _import_loadtest()
    stats = {
        "count": 200, "p95": 1.2, "mean": 0.8, "error_rate": 0.002,
        "qps": 10.0, "wall_seconds": 20.0, "concurrency": 20,
    }
    md = mod.render_report(stats, report_path=str(tmp_path / "report.md"))
    assert "P95" in md
    assert "1.2" in md
    assert "错误率" in md
    assert "20" in md


def test_report_file_written(tmp_path):
    mod = _import_loadtest()
    stats = {"count": 5, "p95": 0.5, "mean": 0.3, "error_rate": 0.0,
             "qps": 1.0, "wall_seconds": 5.0, "concurrency": 5}
    out = tmp_path / "report.md"
    mod.render_report(stats, report_path=str(out))
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "压测报告" in content
