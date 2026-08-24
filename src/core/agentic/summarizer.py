"""Summarizer 汇总节点（Agentic RAG 设计文档 §3.7）。

汇总 evidences 与子问题结论，复用现有 ``src/core/generator.Generator`` 的
引用格式与安全指令，生成带 [文件+章节] 引用的最终答案，并提取 citations。
"""
import logging

from langchain_core.runnables.config import RunnableConfig

from src.config import AGENT_MODEL

logger = logging.getLogger(__name__)


class Summarizer:
    """汇总生成最终答案。"""

    def __init__(self, generator=None, llm_client=None, model: str | None = None):
        # 优先复用现有 Generator（其 _build_prompt 已内置引用格式 + 安全指令）
        if generator is None:
            from src.core.generator import Generator
            generator = Generator()
        self.generator = generator
        self.llm_client = llm_client or getattr(generator, "client", None)
        self.model = model or AGENT_MODEL

    def _extract_citations(self, evidences: list[dict]) -> list[dict]:
        """从证据中提取引用列表（文件 + 章节 + 页码）。"""
        citations: list[dict] = []
        seen: set[tuple] = set()
        for e in evidences or []:
            meta = e.get("metadata", {}) or {}
            source_file = meta.get("source_file", "") or meta.get("source", "未知")
            section = meta.get("section", "")
            page = meta.get("page_number", "")
            key = (source_file, section, page)
            if key in seen:
                continue
            seen.add(key)
            citations.append({
                "file": source_file,
                "section": section,
                "page": page,
                "score": round(float(e.get("score", 0.0) or 0.0), 4),
            })
        return citations

    # ---------------------------------------------------------------- 图节点
    def run(self, state: dict, config: RunnableConfig | None = None) -> dict:
        question = state.get("question", "")
        evidences = state.get("evidences") or []
        route = state.get("route", "retrieve")
        history = (config or {}).get("configurable", {}).get("history") if config else None

        # 无证据：direct_answer（闲聊快速通道）直接由 LLM 回复；其余输出"未找到"
        if not evidences:
            if route == "direct_answer":
                try:
                    gen = self.generator.generate(question, sources=[])
                    answer = gen.get("answer", "") or "知识库中未找到相关信息"
                except Exception as e:  # noqa: BLE001
                    logger.error("direct_answer 生成失败: %s", e)
                    answer = "知识库中未找到相关信息"
            else:
                answer = "知识库中未找到相关信息"
            return {
                "final_answer": answer,
                "citations": [],
                "trace": [{
                    "node": "summarizer",
                    "event": "agent_final",
                    "answer": answer,
                    "citations": [],
                }],
            }

        # 复用 Generator 的引用格式与安全指令生成带引用答案
        try:
            gen_result = self.generator.generate(question, sources=evidences, history=history)
            answer = gen_result.get("answer", "")
            if not answer:
                answer = "知识库中未找到相关信息"
        except Exception as e:  # noqa: BLE001
            logger.error("Summarizer 生成失败，输出兜底答案: %s", e)
            answer = "知识库中未找到相关信息"

        citations = self._extract_citations(evidences)
        return {
            "final_answer": answer,
            "citations": citations,
            "trace": [{
                "node": "summarizer",
                "event": "agent_final",
                "answer": answer,
                "citations": citations,
            }],
        }
