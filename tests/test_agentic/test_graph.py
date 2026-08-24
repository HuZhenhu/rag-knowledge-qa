"""端到端图执行与终止条件测试（设计文档 §7.1 test_graph.py）。

全部使用注入的伪造 LLM / 检索器 / 生成器，自带独立 fixture，
不触碰真实向量库与真实网络，不污染现有全局状态。
"""
import json
from unittest.mock import Mock

from src.core.retriever import RetrievalResult
from src.core.agentic.state import build_initial_state
from src.core.agentic.graph import build_graph
from src.core.agentic.supervisor import Supervisor
from src.core.agentic.planner import Planner
from src.core.agentic.retriever_agent import RetrieverAgent
from src.core.agentic.web_agent import WebAgent
from src.core.agentic.critic import Critic
from src.core.agentic.summarizer import Summarizer
from src.core.agentic.tools.kb_tools import KBTools


class FakeRetriever:
    """可切换行为的伪造检索器。"""
    def __init__(self):
        self.results = []
        self.calls = 0

    def retrieve(self, query, top_k=5):
        self.calls += 1
        return self.results[:top_k]


def _mk_result(content, source_file="技术栈.md", section="Web框架章节", score=0.95):
    return RetrievalResult(
        content=content,
        metadata={"source_file": source_file, "section": section},
        score=score,
    )


def _make_llm_client(route: str):
    resp = Mock()
    resp.choices = [Mock(message=Mock(content=json.dumps(
        {"route": route, "reason": "test"}, ensure_ascii=False)))]
    client = Mock()
    client.chat.completions.create.return_value = resp
    return client


class FakeGenerator:
    def __init__(self, answer="这是基于 2 条证据生成的答案（技术栈.md，Web框架章节）。"):
        self.answer = answer

    def generate(self, question, sources=None, history=None, summary=""):
        return {"answer": self.answer, "usage": {}}


def _build_app(retriever, llm_client=None, generator=None, max_retry=3):
    """组装注入 fake 组件的完整图。"""
    kb_tools = KBTools(retriever, reranker=None)
    gen = generator or FakeGenerator()
    return build_graph(
        supervisor=Supervisor(llm_client=llm_client),
        planner=Planner(),
        retriever_agent=RetrieverAgent(kb_tools),
        web_agent=WebAgent(enabled=False, api_key=""),
        critic=Critic(max_retry=max_retry),
        summarizer=Summarizer(generator=gen, llm_client=llm_client),
    )


def _invoke(app, question, **cfg):
    initial = build_initial_state(question)
    config = {"configurable": {"deadline": 9999999999, "top_k": 5, "history": []}}
    config["configurable"].update(cfg)
    return app.invoke(initial, config=config, recursion_limit=50)


# ---------------------------------------------------------------- 简单单跳闭环
def test_simple_question_closed_loop_with_citations():
    retriever = FakeRetriever()
    retriever.results = [
        _mk_result("FastAPI 是高性能 Web 框架", "技术栈.md", "Web框架章节", 0.95),
        _mk_result("FastAPI 支持异步", "技术栈.md", "异步支持章节", 0.90),
    ]
    app = _build_app(retriever, llm_client=_make_llm_client("retrieve"))
    result = _invoke(app, "FastAPI 有什么特点")

    assert result["route"] == "retrieve"
    assert len(result["evidences"]) == 2
    assert result["final_answer"] != ""
    assert result["final_answer"] != "知识库中未找到相关信息"
    # 引用必须非空且来源真实
    assert len(result["citations"]) >= 1
    assert all(c["file"] for c in result["citations"])
    # Critic 通过，无重试
    assert result["critic_decision"] == "pass"
    assert result["retry_count"] == 0


# ---------------------------------------------------------------- 反思重试
def test_retry_when_no_evidence_then_recover():
    retriever = FakeRetriever()
    # 第一次检索为空 → Critic retry；第二次有结果 → pass
    def fake_retrieve(query, top_k=5):
        retriever.calls += 1
        if retriever.calls == 1:
            return []
        return [_mk_result("FastAPI 特点：性能高", "技术栈.md", "Web框架章节", 0.9)]
    retriever.retrieve = fake_retrieve

    app = _build_app(retriever, llm_client=_make_llm_client("retrieve"))
    result = _invoke(app, "FastAPI 特点")

    assert retriever.calls == 2  # 补检了一次
    assert result["retry_count"] == 1
    assert result["critic_decision"] == "pass"
    assert len(result["evidences"]) >= 1
    assert result["final_answer"] != ""


# ---------------------------------------------------------------- 重试上限强制收敛
def test_force_converge_when_retry_exhausted():
    retriever = FakeRetriever()  # 始终返回空
    app = _build_app(retriever, llm_client=_make_llm_client("retrieve"), max_retry=3)
    result = _invoke(app, "不存在的主题")

    # 重试 3 次后强制收敛到 Summarizer
    assert result["retry_count"] == 3
    assert result["critic_decision"] == "pass"
    assert result["final_answer"] == "知识库中未找到相关信息"
    assert result["citations"] == []


# ---------------------------------------------------------------- 路由分支
def test_decompose_routes_through_planner():
    retriever = FakeRetriever()
    retriever.results = [_mk_result("A 与 B 差异内容", "对比.md", "差异章节", 0.9)]
    app = _build_app(retriever, llm_client=_make_llm_client("decompose"))
    result = _invoke(app, "对比 A 与 B 的区别")

    assert result["route"] == "decompose"
    # M2：对比类问题被拆解为两方信息 + 对比结论三个子问题
    assert result["sub_questions"] == [
        "A 的主要特点/相关信息",
        "B 的主要特点/相关信息",
        "A 与 B 的区别",
    ]
    assert len(result["plan"]) == 3
    assert result["plan"][2]["tools"] == ["kb_compare_documents"]
    assert result["plan"][2]["params"] == {"doc_a": "A", "doc_b": "B"}
    assert len(result["evidences"]) >= 1


def test_direct_answer_no_evidence():
    retriever = FakeRetriever()
    app = _build_app(retriever, llm_client=_make_llm_client("direct_answer"))
    result = _invoke(app, "你好")

    assert result["route"] == "direct_answer"
    assert result["evidences"] == []
    assert result["final_answer"] != ""


def test_web_route_skipped_when_disabled():
    """未启用联网时，即使路由到 web 也直接进 critic（无证据→重试→收敛）。"""
    retriever = FakeRetriever()
    app = _build_app(retriever, llm_client=_make_llm_client("web"), max_retry=1)
    result = _invoke(app, "最新新闻")

    assert result["route"] == "web"
    assert result["evidences"] == []
    # 无证据经 Critic 重试→超限收敛
    assert result["critic_decision"] == "pass"
