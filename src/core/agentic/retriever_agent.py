"""Retriever-Agent 节点（Agentic RAG 设计文档 §3.4）。

对 Planner 产出的每个子问题调用知识库检索工具，结果统一写入
evidences / tool_calls / trace。

M2 里程碑：按 Supervisor/Planner 的指示选用合适工具——
- 若 state 携带 plan（每项含 question / intent / tools / params），
  则按各项的 tools 列表调用对应工具（kb_search_keyword / kb_search_category /
  kb_filter_metadata / kb_compare_documents / kb_list_documents），
  params 透传给工具（如 category、doc_a/doc_b、filters 等）；
- 若 plan 为空（简单问题透传 / 兜底），回退 kb_search_keyword，保持 M1 行为。
"""
import logging

from langchain_core.runnables.config import RunnableConfig

from src.config import AGENT_RETRIEVAL_TOP_K

logger = logging.getLogger(__name__)

# 需携带检索词的工具：调用时把子问题作为 query 透传
_QUERY_TOOLS = ("kb_search_keyword", "kb_search_category", "kb_filter_metadata")


class RetrieverAgent:
    """知识库检索 Agent。"""

    def __init__(self, tools, top_k: int = AGENT_RETRIEVAL_TOP_K):
        """tools: 知识库工具集实例（提供 call_tool 分发与 kb_search_* 等方法）。"""
        self.tools = tools
        self.top_k = top_k

    def _build_work_items(self, question: str, plan: list[dict]) -> list[dict]:
        """把 plan 归一化为工作项 [{query, tools, params}]；plan 为空回退 keyword。"""
        if plan:
            items = []
            for item in plan:
                if not isinstance(item, dict):
                    continue
                query = item.get("question") or question
                tools = item.get("tools") or ["kb_search_keyword"]
                if not isinstance(tools, list) or not tools:
                    tools = ["kb_search_keyword"]
                params = item.get("params") if isinstance(item.get("params"), dict) else {}
                items.append({"query": query, "tools": tools, "params": params})
            if items:
                return items
        return [{"query": question, "tools": ["kb_search_keyword"], "params": {}}]

    def _call_one(self, tool: str, query: str, params: dict, top_k: int) -> dict:
        """按工具名构造调用参数并分发到工具集。"""
        call_params = dict(params)
        if tool in _QUERY_TOOLS:
            call_params["query"] = query
        call_params["top_k"] = top_k
        if tool == "kb_search_category":
            call_params.setdefault("category", query)  # 类别缺省用子问题本身
        elif tool == "kb_filter_metadata":
            # 展开内嵌的 filters（若 plan 提供了结构化过滤条件）
            flt = call_params.get("filters")
            if isinstance(flt, dict):
                call_params.pop("filters")
                call_params.update(flt)
        elif tool == "kb_compare_documents":
            call_params.setdefault("query", query)
        return self.tools.call_tool(tool, **call_params)

    def run(self, state: dict, config: RunnableConfig | None = None) -> dict:
        question = state.get("question", "")
        plan = state.get("plan") or []
        cfg = (config or {}).get("configurable", {}) or {}
        top_k = cfg.get("top_k") or self.top_k

        evidences: list[dict] = []
        tool_calls: list[dict] = []
        for item in self._build_work_items(question, plan):
            for tool in item["tools"]:
                try:
                    result = self._call_one(tool, item["query"], item["params"], top_k)
                    hits = result.get("results") or []
                    tagged = []
                    for h in hits:
                        if not isinstance(h, dict):
                            continue
                        # 浅拷贝 + 打子问题归属标签，供 Critic 覆盖度评审；不污染上游数据
                        h = dict(h)
                        h.setdefault("sub_question", item["query"])
                        tagged.append(h)
                    evidences.extend(tagged)
                    tool_calls.append({
                        "tool": tool,
                        "params": {"query": item["query"], "top_k": top_k,
                                   **{k: v for k, v in item["params"].items()}},
                        "hits": len(tagged),
                    })
                    logger.info("Retriever-Agent 工具 %s 检索 %r 命中 %d 条",
                                tool, item["query"], len(tagged))
                except Exception as e:  # noqa: BLE001
                    logger.warning("Retriever-Agent 工具 %s 调用失败 %r: %s",
                                   tool, item["query"], e)
                    tool_calls.append({
                        "tool": tool,
                        "params": {"query": item["query"], "top_k": top_k,
                                   **{k: v for k, v in item["params"].items()}},
                        "error": str(e),
                    })

        trace = [{
            "node": "retriever_agent",
            "event": "agent_tool_call",
            "tool_calls": tool_calls,
        }]
        if evidences:
            # 证据卡片（设计文档 §4.2）：携带来源 + 章节，供前端时间线渲染
            src_map: dict[tuple[str, str], dict] = {}
            for e in evidences:
                meta = e.get("metadata") or {}
                f = str(meta.get("source_file", "") or meta.get("source", "未知"))
                sec = str(meta.get("section", "") or "")
                src_map.setdefault((f, sec), {"file": f, "section": sec})
            trace.append({
                "node": "retriever_agent",
                "event": "agent_evidence",
                "evidence_count": len(evidences),
                "sources": sorted(src_map.values(), key=lambda s: s["file"]),
            })

        return {
            "evidences": evidences,
            "tool_calls": tool_calls,
            "trace": trace,
        }
