"""Agentic RAG 引擎入口（Agentic RAG 升级设计文档 §2.2 / §3.1）。

``AgenticEngine`` 复用现有 retriever / reranker / generator / vector_store 模块，
通过 LangGraph 编排 Supervisor → Planner → Retriever-Agent → Critic → Summarizer，
输出格式与现有引擎对齐（answer + [文件+章节] 引用 + trace），保证前端与评测兼容。
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field

from src.core.embedder import Embedder
from src.core.vector_store import VectorStore
from src.core.retriever import Retriever, HybridRetriever
from src.core.generator import Generator
from src.core.reranker import Reranker

from src.core.base_engine import BaseRAGEngine

from src.core.agentic.state import build_initial_state
from src.core.agentic.supervisor import Supervisor
from src.core.agentic.planner import Planner
from src.core.agentic.retriever_agent import RetrieverAgent
from src.core.agentic.web_agent import WebAgent
from src.core.agentic.critic import Critic
from src.core.agentic.summarizer import Summarizer
from src.core.agentic.tools.kb_tools import KBTools
from src.core.agentic.graph import build_graph

from src.config import (
    USE_HYBRID_RETRIEVAL,
    AGENT_TIMEOUT,
    AGENT_RETRIEVAL_TOP_K,
)

logger = logging.getLogger(__name__)


@dataclass
class AgenticRAGResponse:
    """Agentic 引擎响应，字段对齐现有引擎（LangChainRAGResponse / RAGResponse）。"""
    answer: str
    sources: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    timing: dict = field(default_factory=dict)
    trace_id: str = ""
    agent_trace: list[dict] = field(default_factory=list)  # 推理过程（前端可视化，M5 使用）


class AgenticEngine(BaseRAGEngine):
    """基于 LangGraph 的 Agentic RAG 引擎。"""

    engine_name = "agentic"

    def __init__(self, use_hybrid: bool = USE_HYBRID_RETRIEVAL,
                 use_reranker: bool = True,
                 llm_client=None, model: str | None = None):
        # 最近一次查询的 Agent 推理过程（前端可视化，M5：由 main.py 逐条推送 WebSocket agent 事件）
        self._last_agent_trace: list[dict] = []

        # ---- 复用现有基础组件 ----
        self.embedder = Embedder()
        self.vector_store = VectorStore()
        self.generator = Generator()
        self.reranker = Reranker() if use_reranker else None

        if use_hybrid:
            self.retriever = HybridRetriever(self.vector_store, self.embedder)
            self._build_bm25_index()
        else:
            self.retriever = Retriever(self.vector_store, self.embedder)

        # ---- Agentic 编排组件 ----
        self.kb_tools = KBTools(self.retriever, self.reranker)
        self.supervisor = Supervisor(llm_client=llm_client, model=model)
        self.planner = Planner(llm_client=llm_client, model=model)
        self.retriever_agent = RetrieverAgent(self.kb_tools)
        self.web_agent = WebAgent()
        self.critic = Critic()
        self.summarizer = Summarizer(generator=self.generator, llm_client=llm_client, model=model)

        self.app = build_graph(
            self.supervisor,
            self.planner,
            self.retriever_agent,
            self.web_agent,
            self.critic,
            self.summarizer,
        )
        logger.info("AgenticEngine 初始化完成（LangGraph 图已编译）")

    def _build_bm25_index(self):
        """从向量库加载文档构建 BM25 索引（复用原版 RAGEngine 逻辑）。"""
        try:
            all_data = self.vector_store.get_all()
            if all_data and all_data.get("documents"):
                self.retriever.build_bm25_index(all_data["documents"])
                logger.info("Agent BM25 索引构建完成，共 %d 个文档", len(all_data["documents"]))
        except Exception as e:  # noqa: BLE001
            logger.warning("Agent BM25 索引构建失败，回退纯向量检索: %s", e)
            self.retriever = Retriever(self.vector_store, self.embedder)

    # ---------------------------------------------------------------- 核心查询
    def query(self, question: str, top_k: int | None = None,
              history: list[dict] | None = None,
              summary: str = "",
              user_id: str = "") -> AgenticRAGResponse:
        """执行 Agentic RAG 问答（同步）。"""
        start = time.time()
        initial = build_initial_state(question)
        deadline = time.time() + AGENT_TIMEOUT
        graph_config = {
            "configurable": {
                "deadline": deadline,
                "top_k": top_k or AGENT_RETRIEVAL_TOP_K,
                "history": history or [],
                "summary": summary,
                "user_id": user_id,
            }
        }
        try:
            result = self.app.invoke(initial, config=graph_config, recursion_limit=50)
        except Exception as e:  # noqa: BLE001
            logger.error("Agentic 图执行失败: %s", e)
            result = {**initial, "final_answer": f"Agentic 执行失败: {e}"}

        final_answer = result.get("final_answer") or "知识库中未找到相关信息"
        evidences = result.get("evidences") or []
        citations = result.get("citations") or []
        trace = result.get("trace") or []

        timing = {
            "total_ms": round((time.time() - start) * 1000, 2),
            "retry_count": int(result.get("retry_count", 0)),
            "route": result.get("route", ""),
        }

        return AgenticRAGResponse(
            answer=final_answer,
            sources=evidences,
            usage={},
            timing=timing,
            trace_id=f"agent_{uuid.uuid4().hex[:12]}",
            agent_trace=trace,
        )

    def query_stream(self, question: str, top_k: int | None = None,
                     history: list[dict] | None = None,
                     summary: str = "",
                     user_id: str = ""):
        """流式接口兼容（M1：一次性产出完整答案；后续可按证据分片流式化）。

        M5：执行后把完整推理过程写入 ``self._last_agent_trace``，
        供 main.py WebSocket 端点逐条推送 ``agent_plan / agent_tool_call /
        agent_evidence / agent_reflect / agent_final`` 事件。
        """
        resp = self.query(question, top_k=top_k, history=history,
                          summary=summary, user_id=user_id)
        # 暴露推理过程供 WebSocket 端点推送（前端可视化，M5）
        self._last_agent_trace = list(resp.agent_trace or [])
        if resp.answer:
            yield resp.answer, True, resp.sources, resp.timing
        else:
            yield "知识库中未找到相关信息", True, resp.sources, resp.timing
