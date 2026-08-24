"""Agentic 各节点共用的 LLM 客户端与辅助调用。

统一使用 OpenAI 兼容协议（DeepSeek 等），与 ``src/core/generator.py`` 同源配置。
测试场景下可向节点注入伪造 client，避免真实网络调用。
"""
from __future__ import annotations

import json
import logging

from src.config import (
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
    AGENT_MODEL,
)

logger = logging.getLogger(__name__)


def create_client():
    """创建 OpenAI 兼容客户端（DeepSeek 等）。"""
    from openai import OpenAI
    return OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)


def default_model() -> str:
    """Agent 节点默认模型。"""
    return AGENT_MODEL or OPENAI_MODEL


def chat_text(
    client,
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 1024,
) -> str:
    """非流式文本生成。"""
    model = model or default_model()
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content or ""


def chat_json(
    client,
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 1024,
) -> dict:
    """要求 LLM 返回 JSON 对象并解析；失败返回空 dict（调用方自行兜底）。"""
    text = chat_text(client, messages, model=model, temperature=temperature, max_tokens=max_tokens)
    return _parse_json(text)


def _parse_json(text: str) -> dict:
    """从 LLM 输出中稳健提取 JSON 对象。"""
    text = (text or "").strip()
    # 去掉 ```json ... ``` 围栏
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().lstrip("#").strip().lower().startswith("json"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 尝试截取第一个 { ... } 块
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
        logger.warning("LLM 返回内容无法解析为 JSON，返回空 dict")
        return {}
