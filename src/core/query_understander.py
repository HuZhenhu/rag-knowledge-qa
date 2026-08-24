"""查询理解"""
import json
import logging
import re
from dataclasses import dataclass, field

from openai import OpenAI

from src.config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    LLM_TEMPERATURE,
)

logger = logging.getLogger(__name__)


@dataclass
class QueryExpansion:
    """查询扩展结果"""
    original_query: str
    expanded_queries: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    intent: str = ""


class QueryUnderstander:
    """查询理解器"""

    def __init__(self):
        self.client = None

    def _init_client(self):
        """初始化DeepSeek客户端"""
        if self.client is None:
            self.client = OpenAI(
                api_key=DEEPSEEK_API_KEY,
                base_url=DEEPSEEK_BASE_URL
            )

    @staticmethod
    def _content_to_text(content) -> str:
        """兼容 OpenAI 兼容接口可能返回的 content 数组（如 [{"type":"text","text":"..."}]）。"""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    parts.append(item.get("text", ""))
                elif isinstance(item, str):
                    parts.append(item)
            return "".join(parts)
        return str(content) if content is not None else ""

    def expand_query(self, query: str) -> QueryExpansion:
        """查询扩展：把问题拆成多个子查询"""
        self._init_client()

        prompt = f"""请将以下用户问题拆分成3-5个不同角度的子查询，用于检索知识库。

用户问题：{query}

请返回JSON格式：
{{
    "expanded_queries": ["子查询1", "子查询2", "子查询3"],
    "entities": ["实体1", "实体2"],
    "intent": "用户意图描述"
}}

要求：
1. 子查询要覆盖问题的不同方面
2. 实体包括人名、地名、专业术语等
3. 意图要简洁明了"""

        try:
            response = self.client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[
                    {"role": "system", "content": "你是一个查询分析专家。"},
                    {"role": "user", "content": prompt}
                ],
                temperature=LLM_TEMPERATURE,
                max_tokens=500
            )

            result_text = self._content_to_text(response.choices[0].message.content)

            json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
            if json_match:
                try:
                    result = json.loads(json_match.group())
                    return QueryExpansion(
                        original_query=query,
                        expanded_queries=result.get("expanded_queries", [query]),
                        entities=result.get("entities", []),
                        intent=result.get("intent", "")
                    )
                except json.JSONDecodeError:
                    pass

        except Exception as e:
            logger.warning("查询扩展失败: %s，返回原始查询", e)

        return QueryExpansion(
            original_query=query,
            expanded_queries=[query],
            entities=[],
            intent=""
        )

    def correct_query(self, query: str) -> str:
        """纠错：修正用户输入的错别字

        用 LLM 判断查询中是否有错别字，如果有则返回修正后的版本。
        没有错别字时返回原始查询。
        """
        self._init_client()

        prompt = f"""请检查以下用户查询中是否有错别字或谐音字，如果有请修正，如果没有请原样返回。

用户查询：{query}

要求：
1. 只修正明显的错别字和同音错字（如"心法"→"新法"、"新发"→"新法"）
2. 不要改变用户的意思
3. 如果没有错别字，原样返回
4. 只返回修正后的查询文本，不要加任何解释"""

        try:
            response = self.client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[
                    {"role": "system", "content": "你是一个中文纠错专家，擅长识别同音字、形近字错误。"},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=200
            )
            corrected = self._content_to_text(response.choices[0].message.content).strip()
            if corrected and corrected != query:
                logger.info("查询纠错: '%s' → '%s'", query, corrected)
            return corrected if corrected else query
        except Exception as e:
            logger.warning("查询纠错失败: %s，返回原始查询", e)
            return query

    def generate_hyde(self, query: str) -> str:
        """HyDE：生成假设性文档"""
        self._init_client()

        prompt = f"""请基于以下问题，生成一段假设性的回答（像知识库中会有的内容）。

问题：{query}

要求：
1. 假设性回答应该像文档中的内容，而不是对话式的回答
2. 包含可能相关的关键词和信息
3. 长度约100-200字"""

        try:
            response = self.client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[
                    {"role": "system", "content": "你是一个文档生成专家。"},
                    {"role": "user", "content": prompt}
                ],
                temperature=LLM_TEMPERATURE,
                max_tokens=300
            )
            content = self._content_to_text(response.choices[0].message.content)
            if content and content.strip():
                return content
            return query

        except Exception as e:
            logger.warning("HyDE生成失败: %s，返回原始查询", e)
            return query
