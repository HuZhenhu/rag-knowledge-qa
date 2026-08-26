# AD-0002 — T3.2 语义缓存策略深化

- **状态**：已接受（Accepted，2026-08-26）
- **日期**：2026-08-26
- **触发任务**：enterprise-rag-scale-plan.md · T3.2

## 背景

客服场景高频重复提问，语义缓存命中率直接决定成本与延迟。单一余弦阈值无法适配
"FAQ 口语化问法差异大、技术问题需严格匹配" 等跨领域差异；进程内缓存扩容即失效。

## 决定

在既有语义缓存（QUERY_CACHE_* / SEMANTIC_CACHE_*）基础上升级四项策略，全部可配置：
1. **按领域阈值调优**：`SEMANTIC_CACHE_DOMAIN_THRESHOLDS`（JSON，如 `{"faq":0.88,"tech":0.94}`），
   请求标注领域 `SEMANTIC_CACHE_DOMAIN` 后用领域阈值，命中宽松领域（faq）提升命中率；
2. **前缀缓存**：`SEMANTIC_CACHE_PREFIX_ENABLED`（宽度 16），对相同规范化前缀（去空白标点）的近义问法
   放宽阈值 `SEMANTIC_CACHE_PREFIX_FALLBACK_THRESHOLD`（0.86），提升"同一问题换说法"的命中；
3. **热门问题预热**：`SEMANTIC_CACHE_HOT_QUESTIONS`（JSON），部署时批量写入语义缓存，
   避免冷启动高频问题直连 LLM；
4. **淘汰策略**：TTL 过期（`SEMANTIC_CACHE_TTL_SECONDS` 默认 7 天）+ 超上限 LRU
   （`SEMANTIC_CACHE_MAX_ENTRIES` 默认 5 万），并可换后端 sqlite/redis（跨 worker 共享）。

## 后果（影响）

**正**：客服高频场景命中率目标 ≥ 40%；扩容不丢缓存（跨 worker 后端）。
**负/风险**：前缀放宽阈值引入轻微误命中（阈值 0.86 高于精确语义标准），以评测回归兜底。
**验收口径**：客服高频场景缓存命中率 ≥ 40%（压测实测项）。
