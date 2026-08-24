"""Web-Agent 节点（Agentic RAG 设计文档 §3.5 / M4）。

M4 里程碑：实现真实联网搜索。封装 ``WebSearchTool``（Bocha/Tavily），
联网结果经内容防泄露 + Prompt 注入过滤后写入 ``evidences``。

- ``AGENT_WEB_SEARCH=false`` 或未配置 API Key 时：节点自动跳过（不产生证据），
  Supervisor 的启发式路由也不会选择 web（见 supervisor.py WEB_MARKERS 判定）。
- 搜索结果证据带 ``source_file=web:<provider>`` 与 ``source_url``，供前端展示与
  下游 Critic 做引用可靠性评审（web 来源不做知识库清单校验）。
"""
import logging

from langchain_core.runnables.config import RunnableConfig

from src.config import AGENT_WEB_SEARCH, AGENT_WEB_SEARCH_API_KEY, AGENT_WEB_SEARCH_PROVIDER
from src.core.agentic.tools.web_search_tool import WebSearchTool

logger = logging.getLogger(__name__)


class WebAgent:
    """联网搜索 Agent。"""

    def __init__(self, enabled: bool | None = None, api_key: str | None = None,
                 provider: str | None = None, tool: WebSearchTool | None = None):
        self.enabled = AGENT_WEB_SEARCH if enabled is None else enabled
        self.api_key = AGENT_WEB_SEARCH_API_KEY if api_key is None else api_key
        self.provider = AGENT_WEB_SEARCH_PROVIDER if provider is None else provider
        self.tool = tool or WebSearchTool(provider=self.provider, api_key=self.api_key)

    @property
    def active(self) -> bool:
        """是否可实际联网：开关开启 且 配置了 Key 且 工具可用。"""
        return bool(self.enabled and self.api_key) and self.tool.active

    def run(self, state: dict, config: RunnableConfig | None = None) -> dict:
        """联网检索并将过滤后的证据追加到 evidences（带 sub_question 标签）。"""
        if not self.active:
            return {
                "trace": [{
                    "node": "web_agent",
                    "event": "agent_plan",
                    "note": "联网搜索未启用（AGENT_WEB_SEARCH=false 或未配置 API Key），跳过",
                }],
            }

        query = state.get("question", "")
        cfg = (config or {}).get("configurable") or {}
        top_k = cfg.get("top_k") or self.tool.top_k

        result = self.tool.search(query, top_k=top_k)
        hits = result.get("results") or []
        evidences = [
            dict(h, sub_question=query)  # 打子问题标签，与知识库证据对齐
            for h in hits
        ]
        tool_calls = [{
            "tool": "web_search",
            "params": {"query": query, "top_k": top_k, "provider": self.provider},
            "hits": len(hits),
            "note": result.get("note", ""),
        }]
        trace = [{
            "node": "web_agent",
            "event": "agent_tool_call",
            "tool": "web_search",
            "query": query,
            "hits": len(hits),
            "note": result.get("note", ""),
        }]
        if not hits:
            trace[0]["event"] = "agent_plan"
            trace[0]["note"] = f"联网搜索未返回有效结果（{result.get('note', '')}）"
        logger.info("Web-Agent 联网搜索 %r 命中 %d 条（%s）",
                    query, len(hits), self.provider)

        return {"evidences": evidences, "tool_calls": tool_calls, "trace": trace}
