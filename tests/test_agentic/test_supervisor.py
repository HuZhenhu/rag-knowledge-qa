"""Supervisor 路由决策测试（设计文档 §7.1 test_supervisor.py）。"""
import json
from unittest.mock import Mock

from src.core.agentic.supervisor import Supervisor


def _make_llm_client(route: str):
    """构造返回指定 route 的伪造 LLM 客户端。"""
    resp = Mock()
    resp.choices = [Mock(message=Mock(content=json.dumps(
        {"route": route, "reason": "test reason"}, ensure_ascii=False)))]
    client = Mock()
    client.chat.completions.create.return_value = resp
    return client


def test_llm_route_retrieve():
    sup = Supervisor(llm_client=_make_llm_client("retrieve"))
    decision = sup.decide("FastAPI 有哪些特点")
    assert decision["route"] == "retrieve"
    assert decision["reason"] == "test reason"


def test_llm_route_decompose():
    sup = Supervisor(llm_client=_make_llm_client("decompose"))
    assert sup.decide("对比 A 与 B 的差异")["route"] == "decompose"


def test_llm_invalid_route_falls_back_to_heuristic():
    """LLM 返回非法路由时应回退启发式，保证总有决策。"""
    sup = Supervisor(llm_client=_make_llm_client("invalid_route"))
    decision = sup.decide("什么是 FastAPI")
    assert decision["route"] in ("direct_answer", "retrieve", "decompose", "web")


def test_heuristic_retrieve_default():
    sup = Supervisor()  # 无 LLM → 启发式
    assert sup.decide("什么是 FastAPI")["route"] == "retrieve"


def test_heuristic_chitchat():
    sup = Supervisor()
    assert sup.decide("你好呀")["route"] == "direct_answer"


def test_heuristic_decompose_marker():
    sup = Supervisor()
    assert sup.decide("对比 FastAPI 和 Flask 的区别")["route"] == "decompose"


def test_heuristic_web_only_when_enabled():
    """未启用联网时，即便含时效词也不路由到 web。"""
    sup = Supervisor(web_search=False, web_search_key="")
    assert sup.decide("2025 年最新 AI 新闻")["route"] != "web"
    # 启用且配 key 才路由 web
    sup2 = Supervisor(web_search=True, web_search_key="fake-key")
    assert sup2.decide("2025 年最新 AI 新闻")["route"] == "web"


def test_run_writes_trace():
    sup = Supervisor(llm_client=_make_llm_client("retrieve"))
    out = sup.run({"question": "FastAPI 特点"}, config={})
    assert out["route"] == "retrieve"
    assert out["intent_reason"] == "test reason"
    assert out["trace"][0]["node"] == "supervisor"
    assert out["trace"][0]["event"] == "agent_plan"
