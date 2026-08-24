"""LangGraph 图定义（Agentic RAG 设计文档 §3.8）。

```
START → supervisor ──(direct_answer)→ summarizer
                  ──(retrieve)→ retriever_agent ──→ critic
                  ──(decompose)→ planner ──→ retriever_agent
                  ──(web)→ web_agent ──→ critic
critic ──(pass)→ summarizer ──→ END
critic ──(retry, <上限)→ supervisor.route_retry 派回对应子 Agent（retriever_agent / web_agent）
critic ──(retry, 达上限/超时)→ summarizer（强制收敛）
```

终止条件：Critic 判定 pass、重试达上限、全局超时，四者之一（用户中断由上层处理）。
M3：retry 不再固定回 retriever_agent，而是经 Supervisor 依据 Critic 评审意见
（retry_target）派回对应子 Agent 补检；目标不可用时降级回检索 Agent。
"""
from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph

from src.core.agentic.state import AgentState

logger = logging.getLogger(__name__)


def _route_supervisor(state: dict) -> str:
    """Supervisor 节点后的条件路由。"""
    return state.get("route", "retrieve")


def _make_critic_router(supervisor):
    """构造 Critic 节点后的条件路由（path 函数，直接返回节点名）。

    - Critic 判定 ``pass``（含达上限/超时强制收敛）→ 进入 summarizer
    - Critic 判定 ``retry`` → 经 Supervisor 依据 retry_target 派回对应
      子 Agent（retriever_agent / web_agent），目标不可用时降级回检索 Agent。
    """
    def _route_critic(state: dict) -> str:
        if state.get("critic_decision", "pass") == "pass":
            return "summarizer"
        return supervisor.route_retry(state)
    return _route_critic


def build_graph(supervisor, planner, retriever_agent, web_agent, critic, summarizer):
    """构建 Agentic RAG 的 LangGraph 状态图。"""
    builder = StateGraph(AgentState)

    builder.add_node("supervisor", supervisor.run)
    builder.add_node("planner", planner.run)
    builder.add_node("retriever_agent", retriever_agent.run)
    builder.add_node("web_agent", web_agent.run)
    builder.add_node("critic", critic.run)
    builder.add_node("summarizer", summarizer.run)

    builder.add_edge(START, "supervisor")

    builder.add_conditional_edges(
        "supervisor",
        _route_supervisor,
        {
            "direct_answer": "summarizer",
            "retrieve": "retriever_agent",
            "decompose": "planner",
            "web": "web_agent",
        },
    )

    builder.add_edge("planner", "retriever_agent")
    builder.add_edge("retriever_agent", "critic")
    builder.add_edge("web_agent", "critic")

    # retry 目标动态：依据 Critic 评审的 retry_target，经 Supervisor 派回
    builder.add_conditional_edges(
        "critic",
        _make_critic_router(supervisor),
    )

    builder.add_edge("summarizer", END)

    return builder.compile()
