# AD-0001 — T3.1 模型分级路由（Model Router）

- **状态**：已接受（Accepted，2026-08-26）
- **日期**：2026-08-26
- **触发任务**：enterprise-rag-scale-plan.md · T3.1

## 背景

简单事实问题与复杂推理问题共用同一模型，造成大模型 token 成本与延迟的双重浪费。
客服场景高频重复问答，latency 与成本是核心制约（P95 目标 < 3s、成本下降 ≥ 30%）。

## 决定

新增 `src/core/model_router.py`（ModelRouter）：
- `classify_intent`：意图分类（客服高频/事实型 vs 复杂推理）；
- `assess_complexity`：复杂度评估（关键词/多跳特征/长度信号）；
- `decide`：输出 `RouterDecision{tier, temperature, max_tokens, requires_llm, enabled}`，
  tier ∈ {small, large, cache};
- 缓存优先：命中路由层判定可走缓存则优先缓存；
- `langchain_rag.py` 注入 model_router，`_select_llm()` 按决策选 LLM 接入 query/query_stream。

总开关 `MODEL_ROUTER_ENABLED`（默认 `false`），关闭时保持原链路，保证灰度、可回滚。

## 后果（影响）

**正**：简单问题走小模型/低温度（方差低、省 token），复杂问题走大模型；平均延迟与成本可降。
**负/风险**：多一路路由逻辑，需完整单测（9 用例）；评测准确率不降由评估闭环兜底。
**验收口径**：平均延迟下降 ≥ 20%、成本下降 ≥ 30%、评测准确率不降（压测与评测实测项）。
