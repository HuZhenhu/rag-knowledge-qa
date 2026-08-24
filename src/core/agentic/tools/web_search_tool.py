"""Web-Agent 联网搜索工具（Agentic RAG 设计文档 §3.5 / M4）。

封装联网搜索 API，优先国内可用服务商：
- Bocha（博查）：https://api.bochaai.com/v1/web-search
- Tavily：https://api.tavily.com/search

通过 ``AGENT_WEB_SEARCH_PROVIDER`` + ``AGENT_WEB_SEARCH_API_KEY`` 配置；
``AGENT_WEB_SEARCH=false`` 或未配置 Key 时 ``active`` 为 False，节点自动跳过。

安全约束（设计文档 §6）：联网结果必须经过现有内容防泄露（LEAK_PATTERNS）
+ Prompt 注入过滤（INJECTION_PATTERNS）后才进入证据集；命中任一模式的结果
被剔除，不进入证据。输出统一证据 schema（content/metadata/score），metadata
带 ``source_file=web:<provider>`` 与 ``source_url``，与知识库证据对齐。
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from src.api.validation import INJECTION_PATTERNS, LEAK_PATTERNS, sanitize_output
from src.config import AGENT_WEB_SEARCH_API_KEY, AGENT_WEB_SEARCH_PROVIDER

logger = logging.getLogger(__name__)

# Bocha 官方限制单次最多返回条数上限
_BOCHA_MAX_COUNT = 10
_TAVILY_MAX_RESULTS = 10

BOCHA_ENDPOINT = "https://api.bochaai.com/v1/web-search"
TAVILY_ENDPOINT = "https://api.tavily.com/search"


def _is_blocked(text: str) -> bool:
    """命中注入/泄露模式 → 返回 True（该结果应被剔除）。"""
    if not text:
        return False
    for pat in INJECTION_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            return True
    for pat in LEAK_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            return True
    return False


def _valid_url(url: str) -> bool:
    if not url or not url.strip():
        return False
    try:
        parts = urlparse(url.strip())
        return parts.scheme in ("http", "https") and bool(parts.netloc)
    except Exception:  # noqa: BLE001
        return False


class WebSearchTool:
    """联网搜索工具，provider 抽象（bocha / tavily）。"""

    PROVIDERS = ("bocha", "tavily")

    def __init__(self, provider: str | None = None, api_key: str | None = None,
                 timeout: int = 15, top_k: int = 5):
        self.provider = (provider or AGENT_WEB_SEARCH_PROVIDER or "bocha").lower()
        self.api_key = api_key if api_key is not None else AGENT_WEB_SEARCH_API_KEY
        self.timeout = timeout
        self.top_k = top_k
        if self.provider not in self.PROVIDERS:
            logger.warning("未知联网服务商 %r，将按 bocha 处理", self.provider)
            self.provider = "bocha"

    @property
    def active(self) -> bool:
        """是否可实际联网：配置了 Key 且 provider 合法。"""
        return bool(self.api_key) and self.provider in self.PROVIDERS

    # ---------------------------------------------------------------- 对外接口
    def search(self, query: str, top_k: int | None = None) -> dict:
        """执行联网搜索并返回统一结果 schema。

        {"query": ..., "results": [{content, metadata, score}], "note": ...}
        结果已过注入/泄露过滤；未启用时返回空结果集。
        """
        query = (query or "").strip()
        k = min(top_k or self.top_k, _BOCHA_MAX_COUNT)
        if not self.active:
            return {"query": query, "results": [], "note": "联网搜索未启用"}
        if not query:
            return {"query": query, "results": [], "note": "查询为空"}

        try:
            if self.provider == "tavily":
                raw_items = self._search_tavily(query, k)
            else:
                raw_items = self._search_bocha(query, k)
        except Exception as e:  # noqa: BLE001
            logger.warning("联网搜索失败 (%s): %s", self.provider, e)
            return {"query": query, "results": [], "note": f"联网搜索失败: {e}"}

        results = self._filter_and_normalize(raw_items)
        note = f"来源: {self.provider}（过滤后 {len(results)}/{len(raw_items)} 条）"
        return {"query": query, "results": results, "note": note}

    # ---------------------------------------------------------------- provider 实现
    def _search_bocha(self, query: str, count: int) -> list[dict]:
        """Bocha 博查搜索。返回原始条目列表。"""
        payload = {
            "query": query,
            "summary": True,          # 返回 AI 摘要作为 content
            "count": max(1, min(count, _BOCHA_MAX_COUNT)),
        }
        data = self._post_json(
            BOCHA_ENDPOINT, payload,
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        items = ((data or {}).get("data") or {}).get("web_results") or []
        parsed = []
        for it in items or []:
            if not isinstance(it, dict):
                continue
            parsed.append({
                "title": str(it.get("title") or "").strip(),
                "url": str(it.get("url") or "").strip(),
                "content": (str(it.get("summary") or it.get("snippet")
                                or it.get("content") or "")).strip(),
                "score": float(it.get("score") or 0.95),
            })
        return parsed

    def _search_tavily(self, query: str, max_results: int) -> list[dict]:
        """Tavily 搜索。返回原始条目列表。"""
        payload = {
            "query": query,
            "search_depth": "basic",
            "max_results": max(1, min(max_results, _TAVILY_MAX_RESULTS)),
        }
        data = self._post_json(
            TAVILY_ENDPOINT, payload,
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        items = ((data or {}).get("results")) or []
        parsed = []
        for it in items or []:
            if not isinstance(it, dict):
                continue
            parsed.append({
                "title": str(it.get("title") or "").strip(),
                "url": str(it.get("url") or "").strip(),
                "content": str(it.get("content") or "").strip(),
                "score": float(it.get("score") or 0.8),
            })
        return parsed

    def _post_json(self, url: str, payload: dict, headers: dict | None = None) -> dict:
        """POST JSON 并解析响应（可被测试替换）。"""
        import requests  # noqa: PLC0415
        resp = requests.post(
            url, json=payload, headers=headers or {},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    # ---------------------------------------------------------------- 过滤与归一化
    def _filter_and_normalize(self, raw_items: list[dict]) -> list[dict]:
        """注入/泄露过滤 + 清洗 + 统一证据 schema。"""
        results: list[dict] = []
        dropped = 0
        for it in raw_items:
            title = (it.get("title") or "").strip()
            content = (it.get("content") or "").strip()
            url = (it.get("url") or "").strip()
            # 安全过滤：命中注入/泄露模式的结果直接剔除
            if _is_blocked(f"{title}\n{content}"):
                dropped += 1
                logger.info("联网结果因命中注入/泄露模式被剔除: %s", title[:40])
                continue
            # URL 校验 + 清洗（防敏感信息泄露）
            if not _valid_url(url):
                dropped += 1
                continue
            cleaned = sanitize_output(f"{title}\n{content}").strip()
            if not cleaned:
                dropped += 1
                continue
            results.append({
                "content": cleaned,
                "metadata": {
                    "source_file": f"web:{self.provider}",
                    "source_type": "web",
                    "source_url": url,
                    "section": title,
                    "provider": self.provider,
                },
                "score": round(float(it.get("score") or 0.9), 4),
            })
        if dropped:
            logger.info("联网搜索过滤剔除 %d 条不安全/无效结果", dropped)
        return results
