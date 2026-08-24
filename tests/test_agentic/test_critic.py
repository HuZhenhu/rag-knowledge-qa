"""Critic 结构化验证节点测试（设计文档 §3.6 / §7.1 test_critic.py）。

覆盖 M3 三维评审：
1. 证据充分性（coverage）：无证据 / 缺失子问题 → retry，全覆盖 → pass
2. 引用可靠性（citation）：来源缺失 / 来源不在知识库 / 内容不一致 → retry
3. 答案一致性（consistency）：引用越界 / LLM 判矛盾 → retry，一致 → pass
4. 决策收敛：retry 计数递增、达上限强制 pass、全局超时强制 pass
5. run 节点输出 schema 与 supervisor.route_retry 派回决策

全部注入伪造 LLM / 工具，不触碰真实向量库与网络。
"""
import json
import time
from unittest.mock import Mock

from src.core.agentic.critic import Critic
from src.core.agentic.supervisor import Supervisor

_SRC = "技术栈.md"


def _mk_evidence(content="FastAPI 是高性能 Web 框架", source=_SRC,
                 section="Web框架章节", score=0.95, sub_question=None):
    e = {
        "content": content,
        "metadata": {"source_file": source, "section": section},
        "score": score,
    }
    if sub_question:
        e["sub_question"] = sub_question
    return e


def _make_llm_client(content: str, route: str = "retrieve"):
    """构造返回固定 JSON 内容的 Mock LLM。"""
    resp = Mock()
    resp.choices = [Mock(message=Mock(content=content))]
    client = Mock()
    client.chat.completions.create.return_value = resp
    # 兼容 supervisor 解析
    route_resp = Mock()
    route_resp.choices = [Mock(message=Mock(content=json.dumps(
        {"route": route, "reason": "test"}, ensure_ascii=False)))]
    client2 = Mock()
    client2.chat.completions.create.side_effect = [route_resp]
    # 只关心 critic 场景时直接返回 client
    return client


def _fake_kb_tools(documents=("技术栈.md",), chunks=None):
    """模拟 KBTools，用于引用真实性校验。"""
    chunks = chunks or [
        {"metadata": {"source_file": "技术栈.md", "section": "Web框架章节"},
         "content": "FastAPI 是高性能 Web 框架，支持异步。"},
    ]
    tools = Mock()
    tools.kb_list_documents.return_value = {"documents": list(documents)}
    tools._all_chunks.return_value = chunks
    return tools


# ---------------------------------------------------------------- 证据充分性
def test_no_evidence_triggers_retry():
    c = Critic(max_retry=3)
    result = c.evaluate("问题", ["子问题1"], [], retry_count=0)

    assert result["decision"] == "retry"
    assert result["retry_target"] == "retriever_agent"
    assert result["refine_instructions"] != ""
    assert any(i["type"] == "coverage" and i["severity"] == "error"
               for i in result["issues"])


def test_missing_subquestion_coverage_triggers_retry():
    c = Critic(max_retry=3)
    evidences = [
        _mk_evidence(content="A 内容", source="A.md", sub_question="A 的主要特点/相关信息"),
    ]
    # 子问题 B 无证据覆盖 → retry
    result = c.evaluate(
        "对比 A 与 B", ["A 的主要特点/相关信息", "B 的主要特点/相关信息"],
        evidences, retry_count=0)

    assert result["decision"] == "retry"
    detail = next(i["detail"] for i in result["issues"]
                  if i["type"] == "coverage")
    assert "B 的主要特点/相关信息" in detail


def test_full_coverage_passes():
    c = Critic(max_retry=3)
    sub = ["A 的主要特点/相关信息", "B 的主要特点/相关信息"]
    evidences = [
        _mk_evidence(content="A 内容", source="A.md", sub_question=sub[0]),
        _mk_evidence(content="B 内容", source="B.md", sub_question=sub[1]),
    ]
    result = c.evaluate("对比 A 与 B", sub, evidences, retry_count=0)

    assert result["decision"] == "pass"
    assert result["reflection"].startswith("证据充分")


# ---------------------------------------------------------------- 引用可靠性
def test_citation_missing_source_triggers_retry():
    c = Critic(max_retry=3)
    e = _mk_evidence(content="无来源证据", source="")
    e["metadata"] = {}
    result = c.evaluate("问题", ["子问题1"], [e], retry_count=0)

    assert result["decision"] == "retry"
    assert any(i["type"] == "citation" and i["severity"] == "error"
               for i in result["issues"])


def test_citation_not_in_kb_triggers_retry():
    c = Critic(max_retry=3, tools=_fake_kb_tools(documents=("技术栈.md",)))
    e = _mk_evidence(content="内容", source="不存在的文档.md")
    result = c.evaluate("问题", ["子问题1"], [e], retry_count=0)

    assert result["decision"] == "retry"
    assert any("不在知识库" in i["detail"] for i in result["issues"])


def test_citation_content_mismatch_triggers_retry():
    tools = _fake_kb_tools(chunks=[{
        "metadata": {"source_file": "技术栈.md"},
        "content": "知识库中的真实片段内容",
    }])
    c = Critic(max_retry=3, tools=tools)
    e = _mk_evidence(content="与知识库片段完全不一致的伪造内容", source=_SRC)
    result = c.evaluate("问题", ["子问题1"], [e], retry_count=0)

    assert result["decision"] == "retry"
    assert any("不一致" in i["detail"] for i in result["issues"])


def test_citation_real_and_matching_passes():
    tools = _fake_kb_tools(documents=("技术栈.md",))
    c = Critic(max_retry=3, tools=tools)
    e = _mk_evidence(content="FastAPI 是高性能 Web 框架，支持异步。", source=_SRC)
    result = c.evaluate("问题", ["子问题1"], [e], retry_count=0)

    assert result["decision"] == "pass"


# ---------------------------------------------------------------- 答案一致性
def test_consistency_draft_ref_overflow_triggers_retry():
    c = Critic(max_retry=3)
    e = _mk_evidence(content="证据一", source=_SRC)
    # 草稿引用了 [5]，但只有 1 条证据 → 越界
    result = c.evaluate("问题", ["子问题1"], [e], retry_count=0,
                        draft_answer="结论[5]")

    assert result["decision"] == "retry"
    assert any(i["type"] == "consistency" and i["severity"] == "error"
               for i in result["issues"])


def test_consistency_llm_detects_conflict():
    client = _make_llm_client(json.dumps({"consistent": False,
                                          "reason": "草稿与证据相矛盾"}))
    c = Critic(max_retry=3, llm_client=client, model="test-model")
    e = _mk_evidence(content="FastAPI 性能高", source=_SRC)
    result = c.evaluate("问题", ["子问题1"], [e], retry_count=0,
                        draft_answer="FastAPI 性能很低")

    assert result["decision"] == "retry"
    assert any("草稿与证据相矛盾" in i["detail"] for i in result["issues"])


def test_consistency_llm_consistent_passes():
    client = _make_llm_client(json.dumps({"consistent": True, "reason": "一致"}))
    c = Critic(max_retry=3, llm_client=client, model="test-model")
    e = _mk_evidence(content="FastAPI 性能高", source=_SRC)
    result = c.evaluate("问题", ["子问题1"], [e], retry_count=0,
                        draft_answer="FastAPI 性能高[1]")

    assert result["decision"] == "pass"


def test_consistency_without_draft_skipped():
    """无草稿时一致性评审跳过，不阻断。"""
    c = Critic(max_retry=3)
    e = _mk_evidence(content="证据", source=_SRC)
    result = c.evaluate("问题", ["子问题1"], [e], retry_count=0, draft_answer="")
    assert result["decision"] == "pass"
    assert not any(i["type"] == "consistency" and i["severity"] == "error"
                   for i in result["issues"])


# ---------------------------------------------------------------- 决策收敛
def test_retry_count_increments_until_limit():
    c = Critic(max_retry=3)
    # 始终无证据
    r1 = c.evaluate("q", ["s"], [], retry_count=0)
    assert r1["decision"] == "retry"
    r2 = c.evaluate("q", ["s"], [], retry_count=1)
    assert r2["decision"] == "retry"
    r3 = c.evaluate("q", ["s"], [], retry_count=2)
    assert r3["decision"] == "retry"
    # 达上限 → 强制收敛 pass
    r4 = c.evaluate("q", ["s"], [], retry_count=3)
    assert r4["decision"] == "pass"
    assert "强制收敛" in r4["reflection"]
    assert r4["retry_target"] == "retriever_agent"
    assert r4["refine_instructions"] == ""


def test_warning_only_passes():
    """仅 warning 级 issue 不阻断。"""
    c = Critic(max_retry=3)
    e = _mk_evidence(content="证据", source=_SRC)
    result = c.evaluate("q", ["s"], [e], retry_count=0)
    # 无 warning 时也 pass；若引入 warning 级也应为 pass
    assert result["decision"] == "pass"


# ---------------------------------------------------------------- run 节点与派回
def test_run_node_output_schema():
    c = Critic(max_retry=3)
    state = {"question": "q", "sub_questions": ["s"], "evidences": [],
             "retry_count": 0, "route": "retrieve", "draft_answer": ""}
    out = c.run(state)

    assert out["critic_decision"] == "retry"
    assert out["retry_count"] == 1
    assert isinstance(out["issues"], list)
    assert out["retry_target"] == "retriever_agent"
    assert out["refine_instructions"] != ""
    assert isinstance(out["reflection"], str)
    assert out["trace"][0]["event"] == "agent_reflect"


def test_run_node_deadline_force_converge():
    c = Critic(max_retry=3)
    state = {"question": "q", "sub_questions": ["s"], "evidences": [],
             "retry_count": 0, "route": "retrieve", "draft_answer": ""}
    config = {"configurable": {"deadline": time.time() - 1}}  # 已超时
    out = c.run(state, config=config)

    assert out["critic_decision"] == "pass"
    assert "超时" in out["reflection"]
    assert out["retry_count"] == 0


def test_route_retry_web_enabled():
    sup = Supervisor(llm_client=None, web_search=True, web_search_key="key")
    target = sup.route_retry({"retry_target": "web_agent", "route": "web"})
    assert target == "web_agent"


def test_route_retry_web_disabled_fallback():
    sup = Supervisor(llm_client=None, web_search=False, web_search_key="")
    target = sup.route_retry({"retry_target": "web_agent", "route": "web"})
    assert target == "retriever_agent"


def test_route_retry_retriever_default():
    sup = Supervisor(llm_client=None)
    target = sup.route_retry({"retry_target": "retriever_agent", "route": "retrieve"})
    assert target == "retriever_agent"
