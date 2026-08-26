# AD-0004 — T3.4 架构收敛与 langchain-community 迁移

- **状态**：已接受（Accepted，2026-08-26）
- **日期**：2026-08-26
- **触发任务**：enterprise-rag-scale-plan.md · T3.4

## 背景

历史遗留三套引擎并存（langchain / agentic / original），HTTP 与 WebSocket 双实例，
双份缓存/双份索引、行为不一致；`langchain-community` 官方已停止维护（sunset），
依赖面风险持续累积。

## 决定

1. **统一引擎接口**：新增 `src/core/base_engine.py`（`BaseRAGEngine`），
   约定类属性 `engine_name` + 抽象方法 `query` / `query_stream`；
   LangChainRAGEngine（`langchain`）与 AgenticEngine（`agentic`）继承之。
2. **删除 original**：`src/core/engine_factory.py` 收敛为 `_get_engine_class`，
   仅支持 `langchain` / `agentic`；配置 `RAG_ENGINE=original` 或未知值直接抛 ValueError，
   禁止静默回退旧实现。向量库解析统一按 `engine_name` 属性路由。
3. **依赖迁移**：`langchain-community` 移除（requirements 换 `langchain-huggingface>=0.1`）：
   - `HuggingFaceEmbeddings` → `from langchain_huggingface import HuggingFaceEmbeddings`；
   - `BM25Retriever` 无官方独立替代 → 自研 `src/core/bm25_retriever.py`
     （基于 `rank_bm25`，混合中英分词，兼容 `BaseRetriever.from_texts/invoke`）。
4. **引擎切换只改配置**：`RAG_ENGINE` 一个环境变量完成全链路切换，无代码改动。

## 后果（影响）

**正**：引擎接口统一可测试；消除 community sunset 风险；配置化切换降低运维心智负担。
**负/风险**：`original` 引擎删除后无法再选（已并入 langchain 收敛）；自研 BM25 分词策略
  （jieba）与 community 版存在细小差异，以全量评测回归兜底。
**验收口径**：全部测试通过（维护后全量 **858 passed**）；引擎切换只改配置；无 sunset 依赖。
