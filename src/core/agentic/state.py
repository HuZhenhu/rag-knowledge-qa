"""AgentState 定义与 reducer（Agentic RAG 升级设计文档 §3.2）。

所有节点通过返回 dict 增量更新状态；evidences / tool_calls / trace 使用
``operator.add`` reducer 追加，其余字段整体覆盖。
"""
from __future__ import annotations

import operator
from typing import Annotated, TypedDict


class AgentState(TypedDict):
    """LangGraph 中流转的 Agent 状态。

    字段与设计文档 §3.2 严格一致；``route`` / ``intent_reason`` 为内部实现
    字段（Supervisor 路由决策与理由，供前端 trace 可视化使用）。
    """
    question: str                       # 原始问题
    sub_questions: list[str]            # Planner 拆解出的子问题
    plan: list[dict]                    # 计划项（含目标、负责 agent、状态）
    evidences: Annotated[list[dict], operator.add]   # 证据集（reducer 追加）
    tool_calls: Annotated[list[dict], operator.add]  # 工具调用记录（reducer 追加）
    trace: Annotated[list[dict], operator.add]       # 推理日志（前端可视化，reducer 追加）
    reflection: str                     # Critic 的反馈意见
    retry_count: int                    # 反思重试计数
    final_answer: str                   # 最终答案
    citations: list[dict]               # 最终引用列表
    # --- M3 反思重试新增字段 ---
    draft_answer: str                   # 草稿答案（供 Critic 一致性评审，可注入）
    issues: list[dict]                  # Critic 结构化评审问题清单
    retry_target: str                   # retry 时派回的对应子 Agent
    refine_instructions: str            # 依据评审意见生成的补检指令
    # --- 内部字段 ---
    route: str                          # Supervisor 路由决策（direct_answer/retrieve/decompose/web）
    intent_reason: str                  # 路由决策理由
    critic_decision: str                # Critic 判定结果（pass/retry），供图条件边路由


def build_initial_state(question: str) -> AgentState:
    """构造图执行前的初始状态。"""
    return {
        "question": question,
        "sub_questions": [],
        "plan": [],
        "evidences": [],
        "tool_calls": [],
        "trace": [{
            "node": "start",
            "event": "user_question",
            "question": question,
        }],
        "reflection": "",
        "retry_count": 0,
        "final_answer": "",
        "citations": [],
        "draft_answer": "",
        "issues": [],
        "retry_target": "",
        "refine_instructions": "",
        "route": "",
        "intent_reason": "",
        "critic_decision": "",
    }
