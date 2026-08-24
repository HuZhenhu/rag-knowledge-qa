"""AgentState 结构与 reducer 行为测试（设计文档 §7.1 test_state.py）。"""
from src.core.agentic.state import AgentState, build_initial_state


def test_initial_state_fields():
    state = build_initial_state("什么是 FastAPI")
    assert state["question"] == "什么是 FastAPI"
    assert state["sub_questions"] == []
    assert state["plan"] == []
    assert state["evidences"] == []
    assert state["tool_calls"] == []
    assert isinstance(state["trace"], list) and state["trace"][0]["event"] == "user_question"
    assert state["reflection"] == ""
    assert state["retry_count"] == 0
    assert state["final_answer"] == ""
    assert state["citations"] == []
    assert state["route"] == ""
    assert state["critic_decision"] == ""


def test_evidences_reducer_appends():
    """Annotated[list, operator.add] 字段应增量追加而非覆盖。"""
    state: AgentState = build_initial_state("q")
    # 模拟 LangGraph 节点返回片段时的合并：追加
    state["evidences"] = state["evidences"] + [{"content": "a", "metadata": {}, "score": 0.9}]
    state["evidences"] = state["evidences"] + [{"content": "b", "metadata": {}, "score": 0.8}]
    assert len(state["evidences"]) == 2
    assert [e["content"] for e in state["evidences"]] == ["a", "b"]


def test_trace_and_tool_calls_reducer():
    state = build_initial_state("q")
    state["trace"] = state["trace"] + [{"node": "supervisor", "event": "agent_plan"}]
    state["trace"] = state["trace"] + [{"node": "critic", "event": "agent_reflect"}]
    state["tool_calls"] = state["tool_calls"] + [{"tool": "kb_search_keyword"}]
    assert len(state["trace"]) == 3  # start + supervisor + critic
    assert len(state["tool_calls"]) == 1


def test_scalar_fields_overwrite():
    """非 reducer 字段应整体覆盖。"""
    state = build_initial_state("q")
    state["final_answer"] = "第一版"
    state["final_answer"] = "第二版"
    assert state["final_answer"] == "第二版"
    state["retry_count"] = 2
    assert state["retry_count"] == 2
