"""Planner 节点（Agentic RAG 设计文档 §3.1 / §3.8）。

M2 里程碑：从 M1 的"透传"升级为真正的复杂问题拆解：
- 对比 / 多跳类问题拆成有序子问题清单（sub_questions）；
- 每个子问题携带检索方向（intent）与建议工具（tools / params），
  供 Retriever-Agent 按 Supervisor/Planner 的指示选用合适工具；
- LLM 可用时优先结构化拆解；失败或不可用时回退确定性启发式规则；
- 简单单跳问题保持透传（sub_questions=[question]），不破坏 M1 闭环。
"""
import logging
import re

from langchain_core.runnables.config import RunnableConfig

from src.core.agentic import llm as llm_util

logger = logging.getLogger(__name__)

# 工具白名单（与 kb_tools.tools 注册表一致）
_ALLOWED_TOOLS = (
    "kb_search_keyword",
    "kb_search_category",
    "kb_filter_metadata",
    "kb_compare_documents",
    "kb_list_documents",
)

# 对比实体提取：<X> (和|与|跟|及|vs) <Y> [的] (区别|差异|...)
_COMPARE_RE = re.compile(
    r"(?:对比|比较|区别|差异)?\s*(?P<x>[^，。？！,.;\s]{1,20})\s*"
    r"(?:和|与|跟|及|vs|VS|vs\.)\s*"
    r"(?P<y>[^，。？！,.;\s]{1,20})\s*(?:的)?"
    r"(?:区别|差异|对比|比较|有什么不同|有何不同)?"
)

_SYSTEM_PROMPT = """你是 RAG 知识库问答系统的规划器（Planner）。
你的任务：把用户的复杂问题拆解为**有序的、可独立检索的子问题清单**。

规则：
1. 只有真正需要多步检索的复杂问题（多跳 / 对比 / 分析 / 多步骤）才拆解；
   简单单跳问题保持原样，sub_questions 只含原问题本身。
2. 每个子问题应当独立可检索，并为它标注检索方向（intent）与建议使用的工具。
3. 可用工具（tools 取值）：
   - kb_search_keyword: 通用关键词/语义混合检索
   - kb_search_category: 按文档类别检索（如法律法规/技术文档/操作手册）
   - kb_filter_metadata: 按元数据过滤检索（来源、标签、作者等）
   - kb_compare_documents: 多文档对比检索
   - kb_list_documents: 列出知识库文档清单
4. 对比类问题通常拆为：先分别检索各方信息，最后再对比总结。

只输出严格 JSON（不要输出其他内容）：
{"sub_questions": ["子问题1", "子问题2"], "plan": [
  {"question": "子问题1", "intent": "检索方向说明", "tools": ["kb_search_keyword"]}
]}

用户问题：{question}"""


class Planner:
    """复杂问题拆解节点。"""

    def __init__(self, llm_client=None, model: str | None = None):
        self.llm_client = llm_client
        self.model = model or llm_util.default_model()

    # ---------------------------------------------------------------- 拆解
    def decompose(self, question: str) -> tuple[list[str], list[dict]]:
        """返回 (sub_questions, plan)。LLM 失败或不可用时回退启发式。"""
        if self.llm_client is not None:
            try:
                messages = [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": f"用户问题：{question}"},
                ]
                data = llm_util.chat_json(self.llm_client, messages, model=self.model)
                subs, plan = self._normalize_llm_plan(question, data)
                if subs:
                    return subs, plan
                logger.warning("Planner LLM 未产出有效子问题，回退启发式")
            except Exception as e:  # noqa: BLE001
                logger.warning("Planner LLM 调用失败，回退启发式: %s", e)
        return self._heuristic_decompose(question)

    def _normalize_llm_plan(self, question: str, data: dict) -> tuple[list[str], list[dict]]:
        """把 LLM 返回的 JSON 归一化为 (sub_questions, plan)，非法则返回空。"""
        if not isinstance(data, dict):
            return [], []
        subs = data.get("sub_questions") or []
        raw_plan = data.get("plan") or []
        if not isinstance(subs, list) or not subs:
            return [], []
        subs = [str(s).strip() for s in subs if str(s).strip()]
        if not subs:
            return [], []
        plan: list[dict] = []
        for i, sq in enumerate(subs):
            item = raw_plan[i] if i < len(raw_plan) and isinstance(raw_plan[i], dict) else {}
            tools = self._validate_tools(item.get("tools"))
            params = item.get("params") if isinstance(item.get("params"), dict) else {}
            plan.append({
                "question": sq,
                "intent": str(item.get("intent", "") or "").strip() or f"检索：{sq}",
                "tools": tools,
                "params": params,
                "status": "planned",
            })
        return subs, plan

    @staticmethod
    def _validate_tools(tools) -> list[str]:
        if not isinstance(tools, list):
            return ["kb_search_keyword"]
        clean = [t for t in tools if isinstance(t, str) and t in _ALLOWED_TOOLS]
        return clean or ["kb_search_keyword"]

    # ---------------------------------------------------------------- 启发式拆解
    def _heuristic_decompose(self, question: str) -> tuple[list[str], list[dict]]:
        """确定性拆解：识别对比类问题拆成"两方信息 + 对比结论"；否则透传。"""
        q = question.strip()
        m = _COMPARE_RE.search(q)
        if m:
            x, y = m.group("x").strip(), m.group("y").strip()
            verb = self._compare_verb(q)
            # "有什么不同/有何不同" 前不加 "的"（如"A 与 B 有什么不同"）
            sep = "" if verb.startswith("有") else "的"
            subs = [
                f"{x} 的主要特点/相关信息",
                f"{y} 的主要特点/相关信息",
                f"{x} 与 {y} {sep}{verb}".replace("  ", " "),
            ]
            plan = [
                {
                    "question": subs[0], "intent": f"检索实体 {x} 的信息",
                    "tools": ["kb_search_keyword"], "params": {}, "status": "planned",
                },
                {
                    "question": subs[1], "intent": f"检索实体 {y} 的信息",
                    "tools": ["kb_search_keyword"], "params": {}, "status": "planned",
                },
                {
                    "question": subs[2], "intent": f"对比 {x} 与 {y} 的差异",
                    "tools": ["kb_compare_documents"],
                    "params": {"doc_a": x, "doc_b": y}, "status": "planned",
                },
            ]
            return subs, plan
        # 无法可靠拆解的复杂问题：保持透传（避免幻觉拆解）
        return [q], [{
            "question": q,
            "intent": f"检索：{q}",
            "tools": ["kb_search_keyword"],
            "params": {},
            "status": "planned",
        }]

    @staticmethod
    def _compare_verb(q: str) -> str:
        for w in ("有什么不同", "有何不同", "差异", "区别", "对比", "比较"):
            if w in q:
                return w
        return "区别"

    # ---------------------------------------------------------------- 图节点
    def run(self, state: dict, config: RunnableConfig | None = None) -> dict:
        """LangGraph 节点：产出 sub_questions 与 plan（携带检索方向/工具）。"""
        question = state.get("question", "")
        sub_questions, plan = self.decompose(question)
        return {
            "sub_questions": sub_questions,
            "plan": plan,
            "trace": [{
                "node": "planner",
                "event": "agent_plan",
                "sub_questions": sub_questions,
                "plan": plan,
                "note": "M2 复杂问题拆解（LLM 或启发式）",
            }],
        }
