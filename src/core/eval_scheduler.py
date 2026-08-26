"""M9: 评测定时任务 — APScheduler 定时运行评测 + 质量下降告警

T3.3 扩展：
- 每日评测（既有）：常规质量告警
- 每周全量评测（新增）：反馈回流 + 全量评测 + 质量门禁（阻断发布）
"""
import json
import logging
from datetime import datetime

from src.config import (
    EVAL_SCHEDULE_HOUR,
    EVAL_SCHEDULE_MINUTE,
    EVAL_ALERT_DROP_THRESHOLD,
    EVAL_FEEDBACK_ENABLED,
    EVAL_MIN_ACCURACY,
    EVAL_FEEDBACK_BAD_RATIO,
    EVAL_WEEKLY_HOUR,
    EVAL_WEEKLY_MINUTE,
)

logger = logging.getLogger(__name__)


def run_scheduled_evaluation() -> dict:
    """执行定时评测并检查是否需要告警

    Returns:
        评测汇总结果字典
    """
    from evaluate import run_evaluation, save_to_database

    logger.info("定时评测开始执行")

    version = datetime.now().strftime("scheduled_%Y%m%d_%H%M%S")
    summary = run_evaluation(version=version)

    if "error" in summary:
        logger.error("定时评测执行失败: %s", summary["error"])
        return summary

    # 保存到数据库
    save_to_database(summary)

    # 检查准确率是否下降超过阈值
    _check_quality_alert(summary)

    logger.info(
        "定时评测完成 — 准确率: %.1f%%, 语义相似度: %.3f",
        summary.get("answer_accuracy", 0) * 100,
        summary.get("avg_semantic_similarity", 0),
    )
    return summary


def _check_quality_alert(current_summary: dict) -> None:
    """检查准确率是否下降超过阈值，触发告警"""
    current_accuracy = current_summary.get("answer_accuracy", 0)

    # 从数据库获取上一次评测结果
    try:
        from src.storage.database import get_previous_evaluation
        prev = get_previous_evaluation()
    except Exception:
        prev = None

    if prev is None:
        logger.info("首次评测，无历史数据可对比")
        return

    prev_accuracy = prev.get("answer_accuracy", 0)
    drop = prev_accuracy - current_accuracy

    if drop > EVAL_ALERT_DROP_THRESHOLD:
        alert_msg = (
            f"[告警] 评测准确率下降超过阈值！"
            f" 上次: {prev_accuracy:.1%} → 本次: {current_accuracy:.1%}"
            f" (下降 {drop:.1%}，阈值: {EVAL_ALERT_DROP_THRESHOLD:.1%})"
        )
        logger.warning(alert_msg)
        _send_alert(alert_msg)
    else:
        logger.info(
            "准确率正常 — 上次: %.1%%, 本次: %.1%%, 变化: %+.1%%",
            prev_accuracy * 100,
            current_accuracy * 100,
            -drop * 100,
        )


def _send_alert(message: str) -> None:
    """发送告警通知

    当前实现：日志输出。可扩展为 webhook/email/钉钉/飞书通知。
    """
    logger.warning("ALERT: %s", message)

    # TODO: 可扩展为 webhook 通知
    # import requests
    # webhook_url = os.getenv("ALERT_WEBHOOK_URL", "")
    # if webhook_url:
    #     requests.post(webhook_url, json={"text": message})


def run_weekly_evaluation() -> dict:
    """每周全量评测 + 反馈回流 + 质量门禁（阻断发布）

    Returns:
        评测汇总（含 feedback_probe / quality_gate / blocked 字段）。
        blocked=True 表示质量回退被门禁拦截，发布流程应停止。
    """
    logger.info("每周全量评测开始执行")

    from evaluate import run_evaluation, save_to_database
    from src.core import eval_feedback

    # 1) 线上反馈回流为评测用例
    extra_cases: list[dict] = []
    if EVAL_FEEDBACK_ENABLED:
        try:
            extra_cases = eval_feedback.export_feedback_cases()
        except Exception as exc:
            logger.warning("反馈回流失败，继续执行基础评测: %s", exc)

    # 2) 全量评测（基础用例 + 反馈回流用例）
    from evaluate import load_test_cases
    test_cases = load_test_cases() + extra_cases
    version = datetime.now().strftime("weekly_%Y%m%d_%H%M%S")
    summary = run_evaluation(test_cases=test_cases, version=version)

    if "error" in summary:
        logger.error("每周评测执行失败: %s", summary["error"])
        summary["blocked"] = True
        return summary

    # 3) 反馈探针统计并入汇总
    summary["feedback_probe"] = eval_feedback.feedback_probe_summary(
        summary.get("results", [])
    )

    # 4) 保存本次评测 → 获取上一次评测作为基线
    try:
        save_to_database(summary)
    except Exception as exc:
        logger.warning("评测结果入库失败: %s", exc)

    try:
        from src.storage.database import get_previous_evaluation
        baseline = get_previous_evaluation()
    except Exception:
        baseline = None

    # 5) 质量门禁（阻断发布）
    gate = eval_feedback.quality_gate(
        summary,
        baseline=baseline,
        drop_threshold=EVAL_ALERT_DROP_THRESHOLD,
        min_accuracy=EVAL_MIN_ACCURACY,
        feedback_bad_ratio_limit=EVAL_FEEDBACK_BAD_RATIO,
    )
    summary["quality_gate"] = gate
    summary["blocked"] = not gate["ok"]

    if gate["ok"]:
        logger.info(
            "每周评测通过质量门禁 — 准确率 %.1f%%, 反馈兜底比例 %.1f%%",
            gate.get("current_accuracy", 0) * 100,
            gate.get("feedback_bad_ratio", 0.0) * 100,
        )
    else:
        alert_msg = (
            f"[阻断发布] 每周评测质量门禁未通过: {gate['reason']}"
            f"（version={version}）"
        )
        logger.error(alert_msg)
        _send_alert(alert_msg)

    return summary


def create_scheduler():
    """创建并配置 APScheduler 调度器

    Returns:
        APScheduler 的 BackgroundScheduler 实例
    """
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = BackgroundScheduler()

    trigger = CronTrigger(
        hour=EVAL_SCHEDULE_HOUR,
        minute=EVAL_SCHEDULE_MINUTE,
    )

    scheduler.add_job(
        run_scheduled_evaluation,
        trigger=trigger,
        id="daily_evaluation",
        name="每日自动评测",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # T3.3: 每周一全量评测 + 反馈回流 + 质量门禁
    weekly_trigger = CronTrigger(
        day_of_week="mon",
        hour=EVAL_WEEKLY_HOUR,
        minute=EVAL_WEEKLY_MINUTE,
    )
    scheduler.add_job(
        run_weekly_evaluation,
        trigger=weekly_trigger,
        id="weekly_feedback_evaluation",
        name="每周全量评测（反馈回流+质量门禁）",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    logger.info(
        "评测定时任务已配置 — 每天 %02d:%02d 执行；每周一 %02d:%02d 全量评测",
        EVAL_SCHEDULE_HOUR,
        EVAL_SCHEDULE_MINUTE,
        EVAL_WEEKLY_HOUR,
        EVAL_WEEKLY_MINUTE,
    )
    return scheduler


def start_scheduler():
    """启动调度器（非阻塞，后台线程运行）"""
    scheduler = create_scheduler()
    scheduler.start()
    logger.info("评测调度器已启动")
    return scheduler


if __name__ == "__main__":
    # 直接运行时手动执行一次评测
    logging.basicConfig(level=logging.INFO)
    result = run_scheduled_evaluation()
    print(json.dumps(
        {k: v for k, v in result.items() if k != "results"},
        ensure_ascii=False,
        indent=2,
    ))
