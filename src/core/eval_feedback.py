"""T3.3 评测自动化闭环 — 线上反馈回流与质量门禁

功能：
1. export_feedback_cases：把线上用户反馈（赞/踩）转换为评测用例并落盘，
   回流到 evaluation/feedback_cases.json，供每周全量评测消费。
   - 好评问题：验证系统能给出实质回答（非兜底），作为高频有效问题探针。
   - 差评问题：用户明确不满，若系统仍返回"知识库中未找到"类兜底回答，
     视为探针失败，纳入质量门禁拦截。
2. feedback_probe_summary：对评测结果中的反馈用例做"兜底回答"探针统计。
3. quality_gate：发布质量门禁（可供 CI / eval_scheduler / 部署脚本共用）：
   - 准确率较上次评测下降超过阈值 → 阻断
   - 准确率低于最低要求 → 阻断
   - 反馈用例兜底回答比例超限 → 阻断
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 与 run_single_test 的拒答判定一致的兜底短语
_FALLBACK_PHRASES = ("未找到", "没有找到", "知识库中没有", "无法回答")


def export_feedback_cases(limit: int | None = None, out_path: str | None = None) -> list[dict]:
    """把线上反馈回流为评测用例并写入评测集文件

    Args:
        limit: 回流反馈条数上限（默认取 config.EVAL_FEEDBACK_LIMIT）
        out_path: 输出 JSON 路径（绝对路径或相对项目根；默认
                  config.EVAL_FEEDBACK_CASES_PATH）

    Returns:
        生成的评测用例列表（同时已落盘）
    """
    from src import config
    from src.storage.database import list_feedback

    limit = limit if limit is not None else config.EVAL_FEEDBACK_LIMIT
    out_path = out_path if out_path is not None else config.EVAL_FEEDBACK_CASES_PATH

    rows: list = []
    try:
        rows = list_feedback(limit=limit)
    except Exception as exc:  # 无数据库/未初始化时静默降级
        logger.warning("读取线上反馈失败，本周无反馈回流: %s", exc)
        return []

    cases: list[dict] = []
    for row in rows:
        r = dict(row) if not isinstance(row, dict) else row
        question = str(r.get("query", "") or "").strip()
        if not question:
            continue
        cases.append({
            "id": f"fb_{int(r.get('id', 0) or 0):06d}",
            "question": question,
            "category": "feedback",
            "expected_keywords": [],
            "expected_answer": "",
            "source_files": [],
            "rating": int(r.get("rating", 0) or 0),
            "request_id": str(r.get("request_id", "") or ""),
            "user_id": str(r.get("user_id", "") or ""),
            "source": "feedback",
        })

    if cases and out_path:
        target = Path(out_path)
        if not target.is_absolute():
            target = PROJECT_ROOT / target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info("已回流 %d 条线上反馈到评测集: %s", len(cases), target)

    return cases


def feedback_probe_summary(results: list[dict]) -> dict:
    """统计反馈用例中"兜底回答"探针结果

    Args:
        results: 评测结果列表（含 category / answer / status）

    Returns:
        {"total": 反馈用例数, "bad": 兜底/失败数, "bad_ratio": 兜底比例}
    """
    fb = [r for r in results if r.get("category") == "feedback"]
    if not fb:
        return {"total": 0, "bad": 0, "bad_ratio": 0.0}

    bad = 0
    for r in fb:
        if r.get("status") != "success":
            bad += 1
            continue
        answer = r.get("answer") or ""
        if not answer.strip() or any(p in answer for p in _FALLBACK_PHRASES):
            bad += 1

    return {"total": len(fb), "bad": bad, "bad_ratio": round(bad / len(fb), 4)}


def quality_gate(
    summary: dict,
    baseline: dict | None = None,
    drop_threshold: float | None = None,
    min_accuracy: float | None = None,
    feedback_bad_ratio_limit: float | None = None,
) -> dict:
    """发布质量门禁：任一条件超限即阻断发布

    Args:
        summary: 本次评测汇总（含 answer_accuracy / results 或 feedback_probe）
        baseline: 上一次评测汇总（无则跳过回退检查）
        drop_threshold: 准确率回退阈值（默认 config.EVAL_ALERT_DROP_THRESHOLD）
        min_accuracy: 准确率最低要求（默认 config.EVAL_MIN_ACCURACY）
        feedback_bad_ratio_limit: 反馈用例兜底比例上限（默认
                                  config.EVAL_FEEDBACK_BAD_RATIO；传 None 关闭）

    Returns:
        {"ok": bool, "reason": str, "current_accuracy": float,
         "baseline_accuracy": float|None, "drop": float|None,
         "feedback_bad_ratio": float}
    """
    from src import config

    drop_threshold = config.EVAL_ALERT_DROP_THRESHOLD if drop_threshold is None else drop_threshold
    min_accuracy = config.EVAL_MIN_ACCURACY if min_accuracy is None else min_accuracy
    if feedback_bad_ratio_limit is None:
        feedback_bad_ratio_limit = config.EVAL_FEEDBACK_BAD_RATIO

    current = float(summary.get("answer_accuracy", 0) or 0)
    baseline_acc = baseline.get("answer_accuracy") if baseline else None
    drop = round(baseline_acc - current, 4) if baseline_acc is not None else None

    reasons: list[str] = []

    if baseline_acc is not None and drop > drop_threshold:
        reasons.append(
            f"准确率较上次下降 {drop:.1%}（阈值 {drop_threshold:.1%}）"
        )

    if min_accuracy is not None and 0 <= min_accuracy <= 1 and current < min_accuracy:
        reasons.append(f"准确率 {current:.1%} 低于最低要求 {min_accuracy:.1%}")

    probe = summary.get("feedback_probe")
    if not isinstance(probe, dict):
        probe = feedback_probe_summary(summary.get("results", []))
    bad_ratio = float(probe.get("bad_ratio", 0) or 0)
    if feedback_bad_ratio_limit is not None and bad_ratio > feedback_bad_ratio_limit:
        reasons.append(
            f"反馈用例兜底回答比例 {bad_ratio:.1%} 超限（阈值 {feedback_bad_ratio_limit:.1%}）"
        )

    return {
        "ok": not reasons,
        "reason": "；".join(reasons),
        "current_accuracy": current,
        "baseline_accuracy": baseline_acc,
        "drop": drop,
        "feedback_bad_ratio": bad_ratio,
    }


def load_feedback_cases() -> list[dict]:
    """加载已回流的反馈评测用例（无文件或异常返回空列表）"""
    from src import config

    target = Path(config.EVAL_FEEDBACK_CASES_PATH)
    if not target.is_absolute():
        target = PROJECT_ROOT / target
    if not target.exists():
        return []
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def main() -> None:
    """CLI：python -m src.core.eval_feedback --export [--limit N] [--out PATH]"""
    import argparse

    parser = argparse.ArgumentParser(description="评测自动化闭环：反馈回流")
    parser.add_argument("--export", action="store_true", help="导出反馈回流评测用例")
    parser.add_argument("--limit", type=int, default=None, help="回流条数上限")
    parser.add_argument("--out", default=None, help="输出 JSON 路径")
    args = parser.parse_args()

    if args.export:
        cases = export_feedback_cases(limit=args.limit, out_path=args.out)
        print(json.dumps({"exported": len(cases)}, ensure_ascii=False))
    else:
        parser.print_help()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
