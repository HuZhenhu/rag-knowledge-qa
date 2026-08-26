"""T3.4 自研 BM25Retriever（基于 rank_bm25）。

替代 langchain_community.retrievers.BM25Retriever（community 已 sunset）。
接口对齐原用法：
    BM25Retriever.from_texts(texts, metadatas=None, k=4)
    retriever.invoke(query) -> list[Document]

分词策略：
- 中文字符串（含 CJK）走 jieba 关键词切分；
- 英文/数字 token 保留原词；
兼容混合中英文文本。
"""
from __future__ import annotations

from typing import Any, Iterable, List, Optional

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from rank_bm25 import BM25Okapi

import jieba

_CJK_RE = __import__("re").compile(r"[\u4e00-\u9fff]")


def _tokenize(text: str) -> List[str]:
    """混合中英文分词：英文/数字按空白切分，中文走 jieba。"""
    tokens: List[str] = []
    for seg in _CJK_RE.split(text):
        if not seg:
            continue
        if _CJK_RE.search(seg):
            tokens.extend(t for t in jieba.lcut(seg) if t.strip())
        else:
            tokens.extend(t for t in seg.split() if t.strip())
    return [t for t in tokens if t.strip()]


class BM25Retriever(BaseRetriever):
    """基于 rank_bm25 的 BM25 检索器，兼容 LangChain BaseRetriever 接口。"""

    bm25: Any
    documents: List[Document]
    k: int

    def __init__(self, bm25: Any, documents: List[Document], k: int = 4, **kwargs: Any) -> None:
        super().__init__(bm25=bm25, documents=documents, k=k, **kwargs)

    @classmethod
    def from_texts(
        cls,
        texts: Iterable[str],
        metadatas: Optional[List[dict]] = None,
        k: int = 4,
        **kwargs: Any,
    ) -> "BM25Retriever":
        texts = list(texts)
        metas = list(metadatas) if metadatas is not None else [{}] * len(texts)
        if len(metas) < len(texts):
            metas = metas + [{}] * (len(texts) - len(metas))
        tokenized = [_tokenize(t) for t in texts]
        bm25 = BM25Okapi(tokenized)
        documents = [
            Document(page_content=t, metadata=dict(m or {}))
            for t, m in zip(texts, metas)
        ]
        return cls(bm25=bm25, documents=documents, k=k)

    def _get_relevant_documents(self, query: str, **kwargs: Any) -> List[Document]:
        scores = self.bm25.get_scores(_tokenize(query))
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[: self.k]
        return [self.documents[i] for i in ranked]
