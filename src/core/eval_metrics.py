"""RAGAS 标准评测指标 — LLM-as-Judge

基于 RAGAS 评估框架（arXiv:2309.15217），从检索和生成两个维度评估 RAG 系统：

1. 忠实度 (Faithfulness)：回答是否完全基于检索上下文（无幻觉）
2. 答案相关性 (Answer Relevancy)：回答是否切题，能否从回答反推回原问题
3. 上下文精准度 (Context Precision)：检索结果中相关 chunk 的占比（测噪声）
4. 上下文召回率 (Context Recall)：答案所需信息是否被完整检索到（测遗漏）

每个指标用 LLM 作为裁判评分（0-1）。
"""
import json
import logging
import re

logger = logging.getLogger(__name__)


class LLMJudge:
    """基于 LLM 的评测裁判"""

    def __init__(self):
        self.client = None

    def _init_client(self):
        from openai import OpenAI
        from src.config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL
        self.client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
        self.model = OPENAI_MODEL

    def _chat(self, system: str, user: str, max_tokens: int = 100,
              temperature: float = 0.0) -> str:
        """调用 LLM（惰性初始化客户端，后续调用复用连接）

        原实现每次调用都执行 _init_client() 新建 OpenAI client，
        评测大量用例时会反复重建连接。改为首次调用时初始化一次。
        """
        if self.client is None:
            self._init_client()
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return resp.choices[0].message.content or ""
        except Exception as e:
            logger.warning("LLM裁判调用失败: %s", e)
            return ""

    def _extract_score(self, text: str) -> float:
        """从 LLM 返回文本中提取 0-1 分数"""
        if not text:
            return 0.5  # 调用失败给中性分
        m = re.search(r'(\d+(?:\.\d+)?)', text)
        if not m:
            return 0.5
        try:
            score = float(m.group(1))
            if score > 1:
                score = score / 5.0  # 若是1-5分制，归一化
            return max(0.0, min(1.0, score))
        except ValueError:
            return 0.5

    def _extract_binary_list(self, text: str) -> list[bool]:
        """从 LLM 返回中提取支持/不支持列表"""
        results = []
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            results.append("支持" in line and "不" not in line[:5])
        return results


# ---------------------------------------------------------------------------
# RAGAS 指标1：忠实度 (Faithfulness)
# ---------------------------------------------------------------------------

def evaluate_faithfulness(question: str, answer: str, sources: list[dict], judge: LLMJudge) -> float:
    """忠实度 — 回答是否完全基于检索上下文（无幻觉）

    RAGAS 方法：将回答拆解为独立陈述 → 逐一判断每个陈述是否可由上下文支持
    """
    if not sources or not answer:
        return 0.0

    context_text = "\n---\n".join(s.get("content", "")[:600] for s in sources[:5])

    # 第一步：拆解回答为原子论断
    system_claim = """你是一个事实抽取专家。把用户的回答拆解为独立的事实性论断。
每行一条论断，只输出论断本身，不要编号和解释。"""
    claims = judge._chat(system_claim, f"回答：{answer}", max_tokens=300)
    if not claims.strip():
        return 0.5

    claim_list = [c.strip() for c in claims.split("\n") if c.strip() and len(c.strip()) > 3]
    if not claim_list:
        return 0.5

    # 第二步：逐条验证论断是否被上下文支持
    supported = 0
    for claim in claim_list[:10]:  # 最多验证10条，控制成本
        system_verify = """你是事实核查专家。判断以下论断是否能从给定的上下文中得到支持。
只回答"支持"或"不支持"。"""
        user_verify = f"""上下文：
{context_text[:800]}

论断：{claim}

该论断是否被上下文支持？"""
        verdict = judge._chat(system_verify, user_verify, max_tokens=10)
        if "支持" in verdict and "不" not in verdict[:5]:
            supported += 1

    return supported / len(claim_list)


# 模块级默认 embedder 惰性单例（避免每次评测重复加载模型）
_default_embedder = None


def _get_default_embedder():
    global _default_embedder
    if _default_embedder is None:
        from src.core.embedder import Embedder
        _default_embedder = Embedder()
    return _default_embedder


# ---------------------------------------------------------------------------
# RAGAS 指标2：答案相关性 (Answer Relevancy)
# ---------------------------------------------------------------------------

def evaluate_answer_relevancy(question: str, answer: str, judge: LLMJudge,
                              embedder=None) -> float:
    """答案相关性 — 回答是否切题

    RAGAS 方法：LLM 从回答逆向推导出多个问题变体 → 计算生成问题与原问题的
    embedding 平均余弦相似度。分数越高说明回答越紧扣问题。

    embedder 可复用外部传入的 embedding 实例（如评测引擎已加载的），
    避免每个用例都重新加载模型（同一模型进程内加载两次在 Windows 会段错误）。
    """
    if not answer:
        return 0.0

    # 第一步：从回答反推可能的问题
    system_gen = """你是一个问题生成专家。根据给定的回答，逆向推导出它可能回答的3个问题。
每个问题一行，只输出问题本身。"""
    gen_questions = judge._chat(system_gen, f"回答：{answer}", max_tokens=200)
    if not gen_questions.strip():
        return 0.5

    questions = [q.strip() for q in gen_questions.split("\n") if q.strip()][:3]
    if not questions:
        return 0.5

    # 第二步：计算原问题与生成问题的语义相似度
    try:
        if embedder is None:
            embedder = _get_default_embedder()
        all_texts = [question] + questions
        vecs = embedder.embed(all_texts)
        if len(vecs) < 2:
            return 0.5

        import numpy as np
        q_vec = np.array(vecs[0])
        similarities = []
        for g_vec in vecs[1:]:
            gv = np.array(g_vec)
            if np.linalg.norm(q_vec) == 0 or np.linalg.norm(gv) == 0:
                similarities.append(0.0)
            else:
                similarities.append(float(np.dot(q_vec, gv) / (np.linalg.norm(q_vec) * np.linalg.norm(gv))))

        return round(sum(similarities) / len(similarities), 4) if similarities else 0.5
    except Exception as e:
        logger.warning("答案相关性计算失败: %s", e)
        return 0.5


# ---------------------------------------------------------------------------
# RAGAS 指标3：上下文精准度 (Context Precision)
# ---------------------------------------------------------------------------

def evaluate_context_precision(question: str, sources: list[dict], judge: LLMJudge) -> float:
    """上下文精准度 — 检索结果中相关 chunk 的占比

    RAGAS 方法：让 LLM 判断每个检索到的 chunk 是否与问题相关，
    相关 chunk 数 / 总 chunk 数。低精准度 = 检索结果噪声多。
    """
    if not sources:
        return 0.0

    relevant = 0
    for s in sources[:5]:
        chunk_text = s.get("content", "")[:400]
        if not chunk_text:
            continue
        system = """你是一个信息检索评估专家。判断给定的文档片段是否与用户问题相关。
只回答"相关"或"不相关"。"""
        user = f"""用户问题：{question}

文档片段：
{chunk_text}

该片段与问题相关吗？"""
        verdict = judge._chat(system, user, max_tokens=10)
        if "相关" in verdict and "不" not in verdict[:5]:
            relevant += 1

    return relevant / len(sources) if sources else 0.0


# ---------------------------------------------------------------------------
# RAGAS 指标4：上下文召回率 (Context Recall)
# ---------------------------------------------------------------------------

def evaluate_context_recall(question: str, sources: list[dict], expected_answer: str, judge: LLMJudge) -> float:
    """上下文召回率 — 答案所需信息是否被完整检索到

    RAGAS 方法：将标准答案拆解为陈述 → 判断每个陈述是否能由检索上下文支持。
    被支持的陈述数 / 总陈述数。低召回率 = 检索遗漏关键信息。
    """
    if not sources or not expected_answer:
        return 0.0

    context_text = "\n---\n".join(s.get("content", "")[:600] for s in sources[:5])

    # 第一步：拆解标准答案为陈述
    system_claim = """你是一个事实抽取专家。把标准答案拆解为独立的事实性论断。
每行一条论断，只输出论断本身。"""
    claims = judge._chat(system_claim, f"标准答案：{expected_answer}", max_tokens=300)
    if not claims.strip():
        return 0.5

    claim_list = [c.strip() for c in claims.split("\n") if c.strip() and len(c.strip()) > 3]
    if not claim_list:
        return 0.5

    # 第二步：判断每个陈述是否由检索上下文支持
    supported = 0
    for claim in claim_list[:8]:  # 最多验证8条
        system_verify = """你是事实核查专家。判断以下论断是否能从给定的上下文中得到支持。
只回答"支持"或"不支持"。"""
        user_verify = f"""上下文：
{context_text[:800]}

论断：{claim}

该论断是否被上下文支持？"""
        verdict = judge._chat(system_verify, user_verify, max_tokens=10)
        if "支持" in verdict and "不" not in verdict[:5]:
            supported += 1

    return supported / len(claim_list)


# ---------------------------------------------------------------------------
# RAGAS 综合评测
# ---------------------------------------------------------------------------

def evaluate_all_dimensions(
    question: str,
    answer: str,
    sources: list[dict],
    expected_answer: str = "",
    weights: dict | None = None,
    embedder=None,
) -> dict:
    """对一次回答进行 RAGAS 四指标评测

    Args:
        question: 用户问题
        answer: 系统生成的回答
        sources: 检索到的来源列表
        expected_answer: 标准答案（用于上下文召回率）
        weights: 各指标权重 {"faithfulness", "answer_relevancy", "context_precision", "context_recall"}
        embedder: 可复用的 embedding 实例（默认内部惰性加载）

    Returns:
        dict: {"faithfulness", "answer_relevancy", "context_precision", "context_recall", "total_score"}
    """
    judge = LLMJudge()

    w = weights or {
        "faithfulness": 0.25,
        "answer_relevancy": 0.25,
        "context_precision": 0.25,
        "context_recall": 0.25,
    }

    faithfulness = evaluate_faithfulness(question, answer, sources, judge)
    answer_relevancy = evaluate_answer_relevancy(question, answer, judge, embedder=embedder)
    context_precision = evaluate_context_precision(question, sources, judge)
    context_recall = evaluate_context_recall(question, sources, expected_answer, judge)

    total = (
        w.get("faithfulness", 0) * faithfulness
        + w.get("answer_relevancy", 0) * answer_relevancy
        + w.get("context_precision", 0) * context_precision
        + w.get("context_recall", 0) * context_recall
    )

    return {
        "faithfulness": round(faithfulness, 4),
        "answer_relevancy": round(answer_relevancy, 4),
        "context_precision": round(context_precision, 4),
        "context_recall": round(context_recall, 4),
        "total_score": round(total, 4),
    }


# ---------------------------------------------------------------------------
# Agentic 专项评测维度（M6：Agentic RAG 升级设计文档 §7.2）
#
# 四个维度均基于 AgenticEngine 输出的 agent_trace 计算：
#   1. 规划正确率 (planning_accuracy)   —— Planner 子问题拆解是否合理覆盖原问题
#   2. 工具选择合理性 (tool_choice_score)—— Retriever/Web Agent 的工具调用是否恰当
#   3. 平均反思次数 (retry_count)        —— Critic 反思重试次数
#   4. 最终引用正确率 (final_citation_accuracy) —— 最终引用的 [文件+章节] 是否真实对应检索证据
# ---------------------------------------------------------------------------


def _extract_agentic_trace(agent_trace: list[dict]) -> dict:
    """从 agent_trace 中提取各节点关键字段（不存在时返回空默认值）。"""
    out = {
        "sub_questions": [],
        "plan": [],
        "tool_calls": [],
        "retry_count": 0,
        "citations": [],
        "route": "",
    }
    if not agent_trace:
        return out
    for t in agent_trace:
        if not isinstance(t, dict):
            continue
        node = t.get("node", "")
        event = t.get("event", "")
        if node == "planner" and event == "agent_plan":
            subs = t.get("sub_questions") or []
            plan = t.get("plan") or []
            if subs:
                out["sub_questions"] = [str(s) for s in subs]
            if plan:
                out["plan"] = plan
        elif node == "retriever_agent" and event == "agent_tool_call":
            calls = t.get("tool_calls") or []
            out["tool_calls"].extend(calls)
        elif node == "critic" and event == "agent_reflect":
            # retry_count 为 Critic 每次评审后的累计值，取最后一条即最终反思次数
            out["retry_count"] = int(t.get("retry_count", 0) or 0)
        elif node == "summarizer" and event == "agent_final":
            cites = t.get("citations") or []
            if cites:
                out["citations"] = cites
        elif node == "supervisor" and event == "agent_plan":
            out["route"] = t.get("route", "")
    return out


def _normalize_citation_file(cite: dict) -> str:
    """规范化引用来源文件名（去路径、去扩展名、统一小写）。"""
    import re
    f = str(cite.get("file", "") or "")
    f = f.replace("\\", "/").split("/")[-1]
    return re.sub(r"\.(pdf|docx|doc|md|txt|png|jpg)$", "", f).lower()


def evaluate_planning_accuracy(question: str, sub_questions: list[str],
                               judge: LLMJudge) -> float:
    """规划正确率 — Planner 子问题拆解是否合理覆盖原问题。

    单跳问题（子问题为空或仅含原问题本身）视为拆解正确得 1.0；
    多跳问题由 LLM 裁判判断子问题能否覆盖原问题的回答需求。
    """
    if not question:
        return 0.0
    subs = [s for s in (sub_questions or []) if str(s).strip()]
    if not subs:
        return 1.0  # 未拆解：单跳透传，视为正确
    if len(subs) == 1 and str(subs[0]).strip() == question.strip():
        return 1.0  # 透传原问题
    sub_list = "\n".join(f"- {s}" for s in subs)
    system = """你是一个 RAG 规划质量评估专家。判断给定的子问题拆解方案是否合理：
1. 子问题集合应能覆盖回答原问题所需的全部关键信息
2. 子问题应相互独立、无冗余
3. 对简单单跳问题，不拆解（仅保留原问题）也是合理的
只输出一个 0~1 之间的分数（可含两位小数）。"""
    user = f"""原问题：{question}

子问题拆解：
{sub_list}

请给出该拆解的合理性评分（0~1）。"""
    text = judge._chat(system, user, max_tokens=10)
    return judge._extract_score(text)


def evaluate_tool_choice(question: str, tool_calls: list[dict],
                         judge: LLMJudge) -> float:
    """工具选择合理性 — 针对问题使用的检索/联网工具是否恰当。

    由 LLM 裁判结合问题语义与工具调用记录判断：
    知识库可答问题不应过度联网，时效/外部信息问题应补充联网工具。
    """
    if not tool_calls:
        return 0.5  # 无工具调用（如 direct_answer 闲聊）：中性偏合理
    tools_used = []
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        name = tc.get("tool", "")
        query = ""
        params = tc.get("params") or {}
        if isinstance(params, dict):
            query = params.get("query", "")
        tools_used.append(f"- 工具: {name}  查询: {query}")
    tools_text = "\n".join(tools_used)
    system = """你是一个 Agent 工具调用评估专家。判断给定的工具调用是否合理：
1. 知识库类问题使用 kb_search 类工具合理
2. 时效性/外部信息问题使用 web_search 合理
3. 工具查询应与问题相关，不应明显无关
只输出一个 0~1 之间的分数（可含两位小数）。"""
    user = f"""用户问题：{question}

工具调用记录：
{tools_text}

请给出工具选择合理性评分（0~1）。"""
    text = judge._chat(system, user, max_tokens=10)
    return judge._extract_score(text)


def evaluate_final_citation_accuracy(citations: list[dict],
                                     evidences: list[dict]) -> float:
    """最终引用正确率 — 最终答案引用的 [文件+章节] 是否真实对应检索证据。

    启发式：对每条 citation，判断 evidences 中是否存在同文件来源
    （章节相同时要求章节一致；无章节信息时仅比对文件）。纯规则计算，无 LLM 调用。
    """
    if not citations:
        return 0.0  # 无引用：视为 0（答案未标注可溯源来源）
    if not evidences:
        return 0.0

    # 收集证据来源集合（file -> set(section 集合)）
    file_sections: dict[str, set] = {}
    for e in evidences:
        if not isinstance(e, dict):
            continue
        meta = e.get("metadata") or {}
        f = str(meta.get("source_file", "") or meta.get("source", ""))
        if not f:
            continue
        nf = _normalize_citation_file({"file": f})
        sec = str(meta.get("section", "") or "")
        file_sections.setdefault(nf, set()).add(sec)

    if not file_sections:
        return 0.0

    hit = 0
    for c in citations:
        if not isinstance(c, dict):
            continue
        nf = _normalize_citation_file(c)
        sec = str(c.get("section", "") or "")
        if nf not in file_sections:
            continue
        if sec and sec not in file_sections[nf]:
            continue  # 章节不符视为错误引用
        hit += 1

    return round(hit / len(citations), 4)


def evaluate_agentic_dimensions(
    question: str,
    agent_trace: list[dict],
    answer: str = "",
    evidences: list[dict] | None = None,
    use_llm: bool = True,
) -> dict:
    """Agentic 专项四维度评测（设计文档 §7.2）。

    Args:
        question: 用户问题
        agent_trace: AgenticEngine 输出的推理过程（trace 列表）
        answer: 最终答案（预留，暂未使用）
        evidences: 检索证据列表（用于引用正确率）
        use_llm: 是否调用 LLM 裁判评估规划/工具维度（False 时返回占位 0.5）

    Returns:
        dict: {"planning_accuracy", "tool_choice_score", "retry_count",
               "final_citation_accuracy"}
    """
    trace_data = _extract_agentic_trace(agent_trace)

    if use_llm:
        judge = LLMJudge()
        planning_accuracy = evaluate_planning_accuracy(
            question, trace_data["sub_questions"], judge
        )
        tool_choice_score = evaluate_tool_choice(
            question, trace_data["tool_calls"], judge
        )
    else:
        planning_accuracy = 0.5
        tool_choice_score = 0.5

    return {
        "planning_accuracy": round(planning_accuracy, 4),
        "tool_choice_score": round(tool_choice_score, 4),
        "retry_count": trace_data["retry_count"],
        "final_citation_accuracy": evaluate_final_citation_accuracy(
            trace_data["citations"], evidences or []
        ),
    }
