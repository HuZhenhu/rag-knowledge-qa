"""Planner 拆解测试（设计文档 §7.1 test_planner.py）。

M2：验证复杂问题拆解为有序子问题清单，且每个子问题携带
检索方向（intent）与建议工具（tools / params）；简单问题保持透传。
使用注入的伪造 LLM，无真实网络调用。
"""
import json
from unittest.mock import Mock

from src.core.agentic.planner import Planner


def _make_llm_client(sub_questions, plan=None):
    payload = {"sub_questions": sub_questions, "plan": plan or []}
    resp = Mock()
    resp.choices = [Mock(message=Mock(content=json.dumps(payload, ensure_ascii=False)))]
    client = Mock()
    client.chat.completions.create.return_value = resp
    return client


# ---------------------------------------------------------------- LLM 拆解
def test_llm_decompose_returns_structured_plan():
    subs = ["FastAPI 的核心特性", "Flask 的核心特性", "FastAPI 与 Flask 的差异"]
    plan = [
        {"question": subs[0], "intent": "检索 FastAPI 特性", "tools": ["kb_search_keyword"]},
        {"question": subs[1], "intent": "检索 Flask 特性", "tools": ["kb_search_keyword"]},
        {"question": subs[2], "intent": "对比两者差异",
         "tools": ["kb_compare_documents"], "params": {"doc_a": "FastAPI", "doc_b": "Flask"}},
    ]
    planner = Planner(llm_client=_make_llm_client(subs, plan))
    out_subs, out_plan = planner.decompose("FastAPI 和 Flask 有什么区别")

    assert out_subs == subs
    assert len(out_plan) == 3
    assert out_plan[0]["intent"] == "检索 FastAPI 特性"
    assert out_plan[0]["tools"] == ["kb_search_keyword"]
    assert out_plan[2]["tools"] == ["kb_compare_documents"]
    assert out_plan[2]["params"] == {"doc_a": "FastAPI", "doc_b": "Flask"}


def test_llm_invalid_output_falls_back_to_heuristic():
    """LLM 返回空/非法 JSON 时应回退启发式，保证总有子问题。"""
    resp = Mock()
    resp.choices = [Mock(message=Mock(content="not json at all"))]
    client = Mock()
    client.chat.completions.create.return_value = resp
    planner = Planner(llm_client=client)
    subs, plan = planner.decompose("对比 A 与 B 的区别")
    assert len(subs) >= 1
    assert plan and plan[0]["tools"]


def test_llm_filters_unknown_tools():
    """LLM 给出的非法工具名应被过滤，回退默认工具。"""
    subs = ["子问题1"]
    plan = [{"question": "子问题1", "intent": "i", "tools": ["not_a_tool"]}]
    planner = Planner(llm_client=_make_llm_client(subs, plan))
    out_subs, out_plan = planner.decompose("复杂问题")
    assert out_subs == ["子问题1"]
    assert out_plan[0]["tools"] == ["kb_search_keyword"]


# ---------------------------------------------------------------- 启发式拆解
def test_heuristic_compare_decompose():
    planner = Planner()
    subs, plan = planner.decompose("对比 FastAPI 和 Flask 的区别")
    assert subs == [
        "FastAPI 的主要特点/相关信息",
        "Flask 的主要特点/相关信息",
        "FastAPI 与 Flask 的区别",
    ]
    assert len(plan) == 3
    # 前两步用关键词检索，第三步用对比检索
    assert plan[0]["tools"] == ["kb_search_keyword"]
    assert plan[2]["tools"] == ["kb_compare_documents"]
    assert plan[2]["params"] == {"doc_a": "FastAPI", "doc_b": "Flask"}
    assert plan[0]["intent"] != ""
    assert plan[0]["status"] == "planned"


def test_heuristic_compare_with_alternative_wording():
    planner = Planner()
    subs, _ = planner.decompose("A 与 B 有什么不同")
    assert len(subs) == 3
    assert subs[2] == "A 与 B 有什么不同"


def test_heuristic_simple_question_passthrough():
    """简单单跳问题应透传，不拆解。"""
    planner = Planner()
    subs, plan = planner.decompose("什么是 FastAPI")
    assert subs == ["什么是 FastAPI"]
    assert len(plan) == 1
    assert plan[0]["tools"] == ["kb_search_keyword"]


# ---------------------------------------------------------------- 图节点
def test_run_writes_plan_and_trace():
    planner = Planner()
    out = planner.run({"question": "对比 A 与 B 的区别"}, config={})
    assert len(out["sub_questions"]) == 3
    assert len(out["plan"]) == 3
    assert out["trace"][0]["node"] == "planner"
    assert out["trace"][0]["event"] == "agent_plan"
    assert "plan" in out["trace"][0]


def test_run_simple_passthrough_trace():
    planner = Planner()
    out = planner.run({"question": "什么是 FastAPI"}, config={})
    assert out["sub_questions"] == ["什么是 FastAPI"]
    assert out["plan"][0]["tools"] == ["kb_search_keyword"]
