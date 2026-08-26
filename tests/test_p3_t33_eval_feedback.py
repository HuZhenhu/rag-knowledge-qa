"""T3.3 评测自动化闭环验收测试

覆盖：
1. export_feedback_cases：线上反馈（赞/踩）回流为评测用例并落盘
2. feedback_probe_summary：反馈用例"兜底回答"探针统计
3. quality_gate：质量回退 / 低于门槛 / 反馈探针超限 → 阻断发布
4. eval_scheduler 注册每周评测 job
5. run_weekly_evaluation：每周全量评测 + 反馈回流 + 质量门禁
6. 每周自动评测 workflow（schedule cron）交付
"""
import json
from pathlib import Path

import pytest

from src.core import eval_feedback


# ======================================================================
# 1. 线上反馈回流为评测用例
# ======================================================================

class TestExportFeedbackCases:
    def test_export_builds_cases_from_feedback(self, tmp_path, monkeypatch):
        rows = [
            {
                "id": 1, "request_id": "t1", "user_id": "u1",
                "query": "如何申请年假？", "rating": 1,
                "created_at": "2026-08-20T10:00:00",
            },
            {
                "id": 2, "request_id": "t2", "user_id": "u2",
                "query": "加班补贴标准是什么？", "rating": -1,
                "created_at": "2026-08-21T10:00:00",
            },
        ]
        monkeypatch.setattr("src.storage.database.list_feedback", lambda limit=100: rows)
        out = Path(tmp_path) / "feedback_cases.json"

        cases = eval_feedback.export_feedback_cases(out_path=str(out))

        assert len(cases) == 2
        assert {c["rating"] for c in cases} == {1, -1}
        for c in cases:
            assert c["question"]
            assert c["category"] == "feedback"
            assert c["source"] == "feedback"
            assert c["request_id"]
            assert set(c) >= {"id", "question", "expected_keywords", "expected_answer",
                              "source_files", "category", "rating"}
        assert out.exists()
        disk = json.loads(out.read_text(encoding="utf-8"))
        assert len(disk) == 2

    def test_export_filters_empty_query(self, tmp_path, monkeypatch):
        rows = [
            {"id": 9, "request_id": "x", "user_id": "u", "query": "   ", "rating": -1,
             "created_at": "t"},
            {"id": 10, "request_id": "y", "user_id": "u", "query": "培训报名入口", "rating": 1,
             "created_at": "t"},
        ]
        monkeypatch.setattr("src.storage.database.list_feedback", lambda limit=100: rows)
        out = Path(tmp_path) / "fb.json"
        cases = eval_feedback.export_feedback_cases(out_path=str(out), limit=100)
        assert [c["id"] for c in cases] == ["fb_000010"]

    def test_export_empty_feedback(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.storage.database.list_feedback", lambda limit=100: [])
        cases = eval_feedback.export_feedback_cases(out_path=str(tmp_path / "e.json"))
        assert cases == []

    def test_feedback_cases_path_config(self):
        from src.config import EVAL_FEEDBACK_CASES_PATH
        assert EVAL_FEEDBACK_CASES_PATH.endswith(".json")


# ======================================================================
# 2. 兜底回答探针
# ======================================================================

class TestFeedbackProbeSummary:
    def test_probe_counts_fallback_answers(self):
        results = [
            {"category": "feedback", "answer": "知识库中未找到相关信息", "status": "success"},
            {"category": "feedback", "answer": "根据[1]规定，加班补贴按1.5倍计算。", "status": "success"},
            {"category": "feedback", "answer": "", "status": "success"},
            {"category": "simple_fact", "answer": "xxx", "status": "success"},
        ]
        probe = eval_feedback.feedback_probe_summary(results)
        assert probe["total"] == 3
        assert probe["bad"] == 2
        assert probe["bad_ratio"] == pytest.approx(2 / 3, abs=1e-3)

    def test_probe_empty_no_feedback_cases(self):
        results = [{"category": "simple_fact", "answer": "x", "status": "success"}]
        probe = eval_feedback.feedback_probe_summary(results)
        assert probe["total"] == 0
        assert probe["bad_ratio"] == 0.0

    def test_probe_error_status_counted_bad(self):
        results = [{"category": "feedback", "status": "error"}]
        probe = eval_feedback.feedback_probe_summary(results)
        assert probe["bad"] == 1


# ======================================================================
# 3. 质量门禁（阻断发布）
# ======================================================================

class TestQualityGate:
    def test_gate_blocks_on_drop_over_threshold(self):
        summary = {"answer_accuracy": 0.80}
        gate = eval_feedback.quality_gate(
            summary, baseline={"answer_accuracy": 0.90},
            drop_threshold=0.05, min_accuracy=0.5, feedback_bad_ratio_limit=0.5,
        )
        assert gate["ok"] is False
        assert "下降" in gate["reason"]

    def test_gate_ok_within_drop_threshold(self):
        summary = {"answer_accuracy": 0.86}
        gate = eval_feedback.quality_gate(
            summary, baseline={"answer_accuracy": 0.90},
            drop_threshold=0.05, min_accuracy=0.5, feedback_bad_ratio_limit=0.5,
        )
        assert gate["ok"] is True

    def test_gate_blocks_below_min_accuracy(self):
        summary = {"answer_accuracy": 0.40}
        gate = eval_feedback.quality_gate(
            summary, baseline={"answer_accuracy": 0.90},
            drop_threshold=0.05, min_accuracy=0.6, feedback_bad_ratio_limit=0.5,
        )
        assert gate["ok"] is False
        assert "低于" in gate["reason"]

    def test_gate_blocks_on_feedback_bad_ratio_over_limit(self):
        summary = {
            "answer_accuracy": 0.85,
            "feedback_probe": {"total": 10, "bad": 8, "bad_ratio": 0.8},
        }
        gate = eval_feedback.quality_gate(
            summary, baseline=None, drop_threshold=0.05,
            min_accuracy=0.6, feedback_bad_ratio_limit=0.5,
        )
        assert gate["ok"] is False
        assert "反馈" in gate["reason"]

    def test_gate_ok_with_no_baseline(self):
        summary = {"answer_accuracy": 0.80, "feedback_probe": {"bad_ratio": 0.0}}
        gate = eval_feedback.quality_gate(
            summary, baseline=None, drop_threshold=0.05,
            min_accuracy=0.6, feedback_bad_ratio_limit=0.5,
        )
        assert gate["ok"] is True

    def test_gate_skips_feedback_check_when_limit_disabled(self):
        summary = {"answer_accuracy": 0.85,
                   "feedback_probe": {"total": 1, "bad": 1, "bad_ratio": 1.0}}
        gate = eval_feedback.quality_gate(
            summary, baseline=None, drop_threshold=0.05,
            min_accuracy=0.6, feedback_bad_ratio_limit=1.0,
        )
        assert gate["ok"] is True


# ======================================================================
# 4. 每周评测任务注册
# ======================================================================

class TestWeeklyScheduler:
    def test_scheduler_registers_daily_and_weekly_jobs(self, monkeypatch):
        # 用假调度器记录 add_job 调用，避免真实后台线程
        calls = []

        class FakeScheduler:
            def __init__(self):
                pass

            def add_job(self, fn, trigger, **kw):
                calls.append({"fn": fn.__name__, "trigger": trigger, "kw": kw})

            def start(self):
                pass

        import apscheduler.schedulers.background as bg
        monkeypatch.setattr(bg, "BackgroundScheduler", FakeScheduler)

        from src.core import eval_scheduler
        eval_scheduler.create_scheduler()

        ids = {c["kw"].get("id") for c in calls}
        assert "daily_evaluation" in ids
        assert "weekly_feedback_evaluation" in ids
        weekly = next(c for c in calls if c["kw"].get("id") == "weekly_feedback_evaluation")
        # 周任务触发器应为周一
        assert "mon" in str(weekly["trigger"]).lower() or "1" == str(weekly["trigger"]).split(", ")[0].split("=")[-1].strip()

    def test_weekly_config_defaults(self):
        from src.config import EVAL_WEEKLY_HOUR, EVAL_WEEKLY_MINUTE, EVAL_FEEDBACK_ENABLED
        assert 0 <= EVAL_WEEKLY_HOUR <= 23
        assert 0 <= EVAL_WEEKLY_MINUTE <= 59
        assert isinstance(EVAL_FEEDBACK_ENABLED, bool)


# ======================================================================
# 5. 每周全量评测 + 门禁
# ======================================================================

class TestRunWeeklyEvaluation:
    def _good_summary(self):
        return {
            "version": "weekly_1", "total_cases": 4, "success_cases": 4,
            "retrieval_hit_rate": 0.9, "answer_accuracy": 0.85,
            "citation_accuracy": 0.9, "avg_semantic_similarity": 0.8,
            "avg_latency_ms": 900.0, "category_accuracy": {},
            "results": [
                {"category": "feedback", "answer": "根据[1]说明如下。", "status": "success"},
                {"category": "simple_fact", "answer": "x", "status": "success"},
            ],
        }

    def test_weekly_runs_eval_with_feedback_export(self, monkeypatch):
        monkeypatch.setattr("src.core.eval_feedback.export_feedback_cases", lambda: [
            {"id": "fb_1", "question": "q", "category": "feedback"},
            {"id": "fb_2", "question": "q2", "category": "feedback"},
        ])
        monkeypatch.setattr("evaluate.load_test_cases", lambda *a, **k: [
            {"id": "b1", "question": "q", "category": "simple_fact"},
            {"id": "b2", "question": "q", "category": "simple_fact"},
        ])
        monkeypatch.setattr("src.core.eval_scheduler.EVAL_FEEDBACK_ENABLED", True)
        captured = {}

        def fake_run_evaluation(**kw):
            captured.update(kw)
            return self._good_summary()

        monkeypatch.setattr("evaluate.run_evaluation", fake_run_evaluation)
        monkeypatch.setattr("evaluate.save_to_database", lambda s: 1)
        monkeypatch.setattr("src.core.eval_feedback.quality_gate",
                            lambda *a, **k: {"ok": True, "reason": "", "current_accuracy": 0.85})

        from src.core import eval_scheduler
        result = eval_scheduler.run_weekly_evaluation()

        assert captured.get("version", "").startswith("weekly_")
        assert isinstance(captured.get("test_cases"), list)
        assert len(captured["test_cases"]) == 4  # 2 基础 + 2 反馈
        assert result["feedback_probe"]["total"] == 1
        assert result["blocked"] is False
        assert "quality_gate" in result and result["quality_gate"]["ok"] is True

    def test_weekly_blocks_when_gate_fails(self, monkeypatch):
        def fake_run_evaluation(**kw):
            summary = self._good_summary()
            summary["results"] = [
                {"category": "feedback", "answer": "知识库中未找到相关信息", "status": "success"},
            ]
            return summary

        monkeypatch.setattr("evaluate.run_evaluation", fake_run_evaluation)
        monkeypatch.setattr("evaluate.save_to_database", lambda s: 1)
        monkeypatch.setattr("src.core.eval_feedback.export_feedback_cases",
                            lambda: [{"id": "fb_1", "question": "q", "category": "feedback"}])

        from src.core import eval_scheduler
        with monkeypatch.context() as m:
            m.setattr("src.core.eval_scheduler.EVAL_FEEDBACK_ENABLED", True)
            m.setattr("src.core.eval_scheduler.EVAL_FEEDBACK_BAD_RATIO", 0.6)
            result = eval_scheduler.run_weekly_evaluation()

        assert result["blocked"] is True
        assert "gate" in str(result.get("quality_gate", {})).lower() or not result["quality_gate"]["ok"]


# ======================================================================
# 6. 每周自动化 workflow 交付
# ======================================================================

class TestWeeklyWorkflow:
    def test_workflow_has_schedule_and_weekly_job(self):
        wf = Path(__file__).parent.parent / ".github" / "workflows" / "eval-regression.yml"
        assert wf.exists(), "缺少 eval-regression.yml"
        text = wf.read_text(encoding="utf-8")
        assert "schedule:" in text
        assert "* * 1" in text or "* * 1" in text.replace(" ", "")  # 周一 cron
        assert "weekly" in text

    def test_quality_gate_script_exists(self):
        p = Path(__file__).parent.parent / "evaluation" / "check_quality_gate.py"
        assert p.exists()
