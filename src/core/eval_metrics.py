"""四维度 LLM-as-Judge 评测指标

基于 RAGAS 评估框架，从四个维度评估 RAG 系统回答质量：

1. 文档相关度 (Context Relevance)：检索到的文档与问题的相关程度
2. 回答忠实度 (Faithfulness)：回答是否忠实于检索结果（有无幻觉）
3. 回答帮助度 (Answer Helpfulness)：回答是否解决了用户问题
4. 回答正确度 (Answer Correctness)：回答与标准答案的事实一致性

每个维度用 LLM 作为裁判评分（0-1），最终按领域权重加权求总分。
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
        # 匹配 0~1 的数字（含 0.5、1、0.85 等）
        m = re.search(r'(\d+(?:\.\d+)?)', text)
        if not m:
            return 0.5
        try:
            score = float(m.group(1))
            # 如果是 1-5 分制，归一化到 0-1
            if score > 1:
                score = score / 5.0
            return max(0.0, min(1.0, score))
        except ValueError:
            return 0.5


# ---------------------------------------------------------------------------
# 四个维度的评测函数
# ---------------------------------------------------------------------------

def evaluate_context_relevance(question: str, sources: list[dict], judge: LLMJudge) -> float:
    """维度1：文档相关度 — 检索结果与问题的相关性

    把检索到的文档发给 LLM，让它评估其中有多少内容真正回答了问题。
    """
    if not sources:
        return 0.0  # 没有检索结果，相关度为 0

    context_text = "\n---\n".join(s.get("content", "")[:500] for s in sources[:5])
    system = """你是一个信息检索评估专家。评估给定的上下文对回答用户问题的相关程度。
只输出一个 0-1 之间的小数分数：
- 1.0 表示上下文完全回答了问题
- 0.5 表示上下文部分相关
- 0.0 表示上下文与问题完全无关"""

    user = f"""用户问题：{question}

检索到的上下文：
{context_text}

请评估这些上下文对回答该问题的相关程度，只输出分数。"""

    return judge._extract_score(judge._chat(system, user))


def evaluate_faithfulness(question: str, answer: str, sources: list[dict], judge: LLMJudge) -> float:
    """维度2：回答忠实度 — 回答是否基于检索结果（无幻觉）

    把回答拆解为事实性论断，逐条判断是否被上下文支持。
    """
    if not sources:
        return 0.0

    context_text = "\n---\n".join(s.get("content", "")[:500] for s in sources[:5])

    # 第一步：拆解回答为论断
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


def evaluate_answer_helpfulness(question: str, answer: str, judge: LLMJudge) -> float:
    """维度3：回答帮助度 — 回答是否解决了用户问题

    让 LLM 评估回答的具体性、完整性和冗余度。
    """
    if not answer:
        return 0.0

    system = """你是一个问答质量评估专家。评估回答对用户问题的帮助程度。
从以下维度打分：
- 是否直接回答了问题（有没有答非所问）
- 是否提供了具体、可操作的信息
- 是否有遗漏关键点
- 是否有冗余废话

输出 0-1 之间的小数分数（可带一位小数），只输出分数。"""
    user = f"""用户问题：{question}

系统回答：{answer}

请评估这个回答对用户问题的帮助程度，只输出分数。"""
    return judge._extract_score(judge._chat(system, user))


def evaluate_answer_correctness(question: str, answer: str, expected_answer: str, judge: LLMJudge) -> float:
    """维度4：回答正确度 — 回答与标准答案的事实一致性

    让 LLM 对比系统回答和标准答案，评估事实一致性和完整性。
    """
    if not answer or not expected_answer:
        return 0.0

    system = """你是一个答案评估专家。对比"系统回答"和"标准答案"，评估系统回答的事实正确性。
从以下维度打分：
- 事实一致性：系统回答中的事实是否与标准答案一致
- 完整性：系统回答是否覆盖了标准答案的关键信息
- 无误性：系统回答是否有错误或编造的内容

输出 0-1 之间的小数分数，只输出分数。"""
    user = f"""用户问题：{question}

标准答案：{expected_answer}

系统回答：{answer}

请评估系统回答的事实正确性，只输出分数。"""
    return judge._extract_score(judge._chat(system, user))


# ---------------------------------------------------------------------------
# 综合评测
# ---------------------------------------------------------------------------

def evaluate_all_dimensions(
    question: str,
    answer: str,
    sources: list[dict],
    expected_answer: str = "",
    weights: dict | None = None,
) -> dict:
    """对一次回答进行四维度评测

    Args:
        question: 用户问题
        answer: 系统生成的回答
        sources: 检索到的来源列表
        expected_answer: 标准答案（用于正确度评测）
        weights: 各维度权重 {"relevance", "faithfulness", "helpfulness", "correctness"}

    Returns:
        dict: {"relevance", "faithfulness", "helpfulness", "correctness", "total_score"}
    """
    judge = LLMJudge()

    # 默认权重（各维度均等，领域权重由外部覆盖）
    w = weights or {
        "relevance": 0.25,
        "faithfulness": 0.25,
        "helpfulness": 0.25,
        "correctness": 0.25,
    }

    relevance = evaluate_context_relevance(question, sources, judge)
    faithfulness = evaluate_faithfulness(question, answer, sources, judge)
    helpfulness = evaluate_answer_helpfulness(question, answer, judge)
    correctness = evaluate_answer_correctness(question, answer, expected_answer, judge)

    # 四维加权总分
    total = (
        w.get("relevance", 0) * relevance
        + w.get("faithfulness", 0) * faithfulness
        + w.get("helpfulness", 0) * helpfulness
        + w.get("correctness", 0) * correctness
    )

    return {
        "relevance": round(relevance, 4),
        "faithfulness": round(faithfulness, 4),
        "helpfulness": round(helpfulness, 4),
        "correctness": round(correctness, 4),
        "total_score": round(total, 4),
    }
