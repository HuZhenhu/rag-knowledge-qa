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
        """调用 LLM"""
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


# ---------------------------------------------------------------------------
# RAGAS 指标2：答案相关性 (Answer Relevancy)
# ---------------------------------------------------------------------------

def evaluate_answer_relevancy(question: str, answer: str, judge: LLMJudge) -> float:
    """答案相关性 — 回答是否切题

    RAGAS 方法：LLM 从回答逆向推导出多个问题变体 → 计算生成问题与原问题的
    embedding 平均余弦相似度。分数越高说明回答越紧扣问题。
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
        from src.core.embedder import Embedder
        embedder = Embedder()
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
) -> dict:
    """对一次回答进行 RAGAS 四指标评测

    Args:
        question: 用户问题
        answer: 系统生成的回答
        sources: 检索到的来源列表
        expected_answer: 标准答案（用于上下文召回率）
        weights: 各指标权重 {"faithfulness", "answer_relevancy", "context_precision", "context_recall"}

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
    answer_relevancy = evaluate_answer_relevancy(question, answer, judge)
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
