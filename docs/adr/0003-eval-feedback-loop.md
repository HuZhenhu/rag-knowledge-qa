# AD-0003 — T3.3 评测自动化闭环（反馈回流 + 每周评测 + 质量门禁）

- **状态**：已接受（Accepted，2026-08-26）
- **日期**：2026-08-26
- **触发任务**：enterprise-rag-scale-plan.md · T3.3

## 背景

评测集静态、无线上真实反馈回流，发布流程无法自动拦截质量回退；每周全量评测长期缺失，
"评测驱动"原则落空，发布回归形同虚设。

## 决定

1. **线上反馈回流**：新增 `src/core/eval_feedback.py`：
   - 赞/踩反馈写库（`database.list_feedback` 等既有接口）→ `export_feedback_cases` 导出
     为 `evaluation/feedback_cases.json`（`EVAL_FEEDBACK_CASES_PATH`），纳入评测集（上限 `EVAL_FEEDBACK_LIMIT`）；
   - `feedback_probe_summary` 探针摘要：统计兜底回答占比，作为质量劣化信号；
   - 开关 `EVAL_FEEDBACK_ENABLED`（默认关）。
2. **每周自动全量评测**：`eval_scheduler.run_weekly_evaluation`（复用既有每日调度器，
   周一 03:00 `EVAL_WEEKLY_HOUR/MINUTE`）；CI `.github/workflows/eval-regression.yml`
   增加 `schedule: cron "0 3 * * 1"` 的 weekly-full-regression job。
3. **质量下降告警 + 阻断发布**：新增 `evaluation/check_quality_gate.py`：
   - 准确率 < `EVAL_MIN_ACCURACY`（0.6）→ 阻断（非零退出）；
   - 兜底回答占比 > `EVAL_FEEDBACK_BAD_RATIO`（0.5）→ 阻断；
   - 相对基线下降 ≥ `EVAL_ALERT_DROP_THRESHOLD`（5%）→ 告警并阻断。
   CI/发布管道把该 CLI 作为门禁步骤。

## 后果（影响）

**正**：评测集随线上反馈持续演化；发布流程自动拦截质量回退；每周自动回归形成闭环。
**负/风险**：反馈用例存在噪音（单次误踩），用比例阈值而非逐条删除缓解。
**验收口径**：反馈驱动评测集持续更新；发布流程自动拦截质量回退。
