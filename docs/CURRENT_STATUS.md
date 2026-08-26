---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 12c4ab0be885f937bdaeebb9f27bfe85_a6724a2ca15511f1a238525400e6dd8f
    ReservedCode1: O6qQhxalLYrWDfSb139uR0Szadksh1rplWWDOp3Tqjyz9jhbqMnqi0cUi1T1pnFEHQtNy1GL63CJnzAzEqG6YPpCI4ct5eXa+s+bkBFy6HLIdWkEwoc09FSgN/cWe2m8a7xeVYKbJB4UGEgp0mprUnIoSC00jZO+icRRU5X6ta2jS6vp/nquOYNH3AY=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 12c4ab0be885f937bdaeebb9f27bfe85_a6724a2ca15511f1a238525400e6dd8f
    ReservedCode2: O6qQhxalLYrWDfSb139uR0Szadksh1rplWWDOp3Tqjyz9jhbqMnqi0cUi1T1pnFEHQtNy1GL63CJnzAzEqG6YPpCI4ct5eXa+s+bkBFy6HLIdWkEwoc09FSgN/cWe2m8a7xeVYKbJB4UGEgp0mprUnIoSC00jZO+icRRU5X6ta2jS6vp/nquOYNH3AY=
---

# 当前状态（Current Status）

> 维护基线文档：**本文件为当前唯一权威状态文档**，与代码保持同步。
> 历史计划/审计文档已归档至 [docs/archive/](archive/README.md)，决策记录见 [docs/adr/](adr/)。
> 任务书：《enterprise-rag-scale-plan.md》。
> 最后更新：2026-08-26（Phase 3 全部完成并收尾校对，全量测试 869 passed）

---

## 1. 架构总览

```
浏览器/Vue 前端
   │  HTTP + WebSocket（提交-推送模式）
   ▼
FastAPI 网关（多 worker，事件循环只做 I/O；run_in_executor / 任务队列）
   ▼
无状态 RAG 引擎（进程内单例，engine_factory）
   ├── 引擎：langchain（默认） / agentic（LangGraph，可配置切换，仅改 RAG_ENGINE）
   ├── 检索：向量（ChromaDB）+ BM25（自研 rank_bm25）混合 + [可选 reranker]
   ├── 模型分级：model_router（简单→小模型/低温度，复杂→大模型，缓存优先；默认关）
   ├── 语义缓存：SQLite/Redis 后端 + 前缀缓存 + 热问题预热 + TTL/LRU 淘汰
   └── LLM：DeepSeek 网关（限流/成本统计/故障转移）
   │
   ▼
SQLite（业务数据/评测）   ChromaDB（向量索引）   本地 Embedding（bge-m3）
```

## 2. 引擎

| 项 | 值 |
|----|----|
| 统一接口 | `BaseRAGEngine`（`src/core/base_engine.py`），约定 `engine_name` / `query` / `query_stream` |
| 可用引擎 | `langchain`（默认）、`agentic` |
| 已删除 | `original`（`src/core/rag_engine.py` 不再经 engine_factory 使用） |
| 切换方式 | 仅改配置 `RAG_ENGINE=langchain|agentic`（环境变量），无需改代码 |
| 单例 | `engine_factory.get_engine()` 进程内单例，HTTP/WebSocket 共享；向量库 `get_vector_store()` 同源 |

**依赖收敛（T3.4）**：`langchain-community`（已 sunset）已移除；
`HuggingFaceEmbeddings` 改用独立包 `langchain-huggingface`；
BM25Retriever 改用自研 `src/core/bm25_retriever.py`（基于 `rank-bm25`）。

## 3. 关键配置开关（src/config.py，环境变量可覆盖）

| 配置 | 默认 | 说明 |
|------|------|------|
| `RAG_ENGINE` | `langchain` | 引擎选择：langchain / agentic |
| `MODEL_ROUTER_ENABLED` | `false` | T3.1 模型分级总开关（默认关，保持原链路） |
| `MODEL_ROUTER_SMALL_MODEL` / `LARGE_MODEL` | 网关小/大模型 | 分级模型 |
| `MODEL_ROUTER_SMALL_TEMPERATURE` | `0.1` | 小模型低温度 |
| `MODEL_ROUTER_MAX_TOKENS_SMALL/LARGE` | `1024/2048` | 分级 token 上限 |
| `SEMANTIC_CACHE_ENABLED` | `true` | 语义缓存开关 |
| `SEMANTIC_CACHE_THRESHOLD` | `0.92` | 兜底余弦命中阈值 |
| `SEMANTIC_CACHE_DOMAIN_THRESHOLDS` | `{}` | 按领域阈值，如 `{"faq":0.88,"tech":0.94}` |
| `SEMANTIC_CACHE_PREFIX_ENABLED` | `true` | 前缀缓存（宽度 16） |
| `SEMANTIC_CACHE_HOT_QUESTIONS` | `[]` | 热门问题预热 |
| `SEMANTIC_CACHE_TTL_SECONDS` / `MAX_ENTRIES` | `604800` / `50000` | 淘汰策略（TTL 过期 + LRU 上限） |
| `SEMANTIC_CACHE_BACKEND` | `sqlite` | 跨 worker 共享后端（sqlite/redis） |
| `EVAL_FEEDBACK_ENABLED` | `false` | T3.3 线上反馈回流开关 |
| `EVAL_MIN_ACCURACY` | `0.6` | 发布质量门禁：准确率最低要求 |
| `EVAL_FEEDBACK_BAD_RATIO` | `0.5` | 兜底回复占比上限（判定质量下降） |
| `EVAL_WEEKLY_HOUR/MINUTE` | `3:00` | 每周一全量评测时间 |

## 4. 评测与质量闭环

- 评测命令：`python -m pytest tests/`（本机全量 **869 passed**）；
  检索/生成链路回归：`evaluation/eval_baseline.py`。
- **线上反馈回流（T3.3）**：赞/踩反馈写库 → `eval_feedback.export_feedback_cases` 汇入
  `evaluation/feedback_cases.json` → 纳入评测集（上限 `EVAL_FEEDBACK_LIMIT`）。
- **每周评测（T3.3）**：`eval_scheduler.run_weekly_evaluation`（周一 03:00）跑全量评测；
  CI `.github/workflows/eval-regression.yml` 含 `schedule: cron "0 3 * * 1"` 每周 job。
- **发布阻断（T3.3）**：`evaluation/check_quality_gate.py` 质量门禁，准确率 < `EVAL_MIN_ACCURACY`
  或兜底比例 > `EVAL_FEEDBACK_BAD_RATIO` 时非零退出，CI/发布流程自动拦截；低于基线 5% 触发告警。
- 评测口径：out_of_scope 问题按人工判定计分（P1 已修复引用虚高问题）。

## 5. Phase 0-3 完成情况

| 阶段 | 主题 | 状态 |
|------|------|------|
| Phase 0 | 安全与正确性（CORS/密钥/后门/吊销/CI/测试环境） | 完成 |
| Phase 1 | A 档并发（异步化/单例/多 worker/语义缓存/队列限流熔断/提交-推送/压测） | 完成 |
| Phase 2 | B 档智能客服（无状态/向量集群/消息队列/LLM 网关/多租户/防滥用/部署/监控） | 完成 |
| Phase 3 | 平台化与成本治理（模型分级/缓存深化/评测闭环/架构收敛/文档收敛） | 完成（2026-08-26） |

## 6. 测试基线

- 全量：`pytest --basetemp .pytest_tmp tests/` → **869 passed, 0 failed**（2026-08-26，Phase 3 全部完成后实测）。
- Phase 2 末 797 → T3.1 +9=806 → T3.2 +19=825 → T3.3 +19=844 → T3.4 +14=858 → T3.5 +11=869。
- 说明：K8s manifests、Prometheus 告警规则、Milvus/Redis 接入为**交付配置**（本机无对应服务，
  已用可插拔抽象 + mock 单测验证），压测/线上数据为验收实测项。

## 7. 已知边界

- 本机 8080 端口被 `H:\ai-dev-platform` 的 uvicorn 占用，联调前先确认端口。
- C 盘空间紧张（约 2.6GB），构建产物/模型缓存建议指向 D 盘或 H 盘。
- 语义缓存命中等性能指标需在真实客服流量下压测验收（Phase 3 验收项）。
*（内容由AI生成，仅供参考）*
