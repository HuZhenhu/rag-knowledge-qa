"""知识库检索工具集（Agentic RAG 设计文档 §3.4）。

M2 里程碑：将工具集从 M1 的基础两工具拆细为完整五工具：
- kb_search_keyword: 关键词/语义混合检索（复用 retriever + reranker）
- kb_search_category: 按文档类别检索（类别来自 metadata category 或 source_file 目录）
- kb_filter_metadata: 按元数据字段过滤后检索（来源、标签、作者等）
- kb_list_documents: 列出知识库现有文档，供 Agent 判断检索方向
- kb_compare_documents: 多文档对比检索（针对"对比 X 与 Y 的差异"类问题）

每个检索工具返回统一结构化结果：
{"query": ..., "results": [{content, metadata, score}], "note": ...}
"""
from __future__ import annotations

import logging
import re

from src.config import AGENT_RETRIEVAL_TOP_K

logger = logging.getLogger(__name__)

# 工具统一返回 schema 字段
TOOL_SCHEMA_KEYS = ("query", "results")

# metadata 中可能存在的类别字段（优先级从高到低）
_CATEGORY_KEYS = ("category", "doc_category", "doc_type")


def _to_evidence(result) -> dict:
    """把 Retriever 的 RetrievalResult 转成统一证据 dict（与现有 sources 结构对齐）。"""
    meta = dict(result.metadata or {})
    source_file = meta.get("source_file", "") or meta.get("source", "")
    if "\\" in source_file or "/" in source_file:
        meta["source_file"] = source_file.replace("\\", "/").rsplit("/", 1)[-1]
    return {
        "content": result.content,
        "metadata": meta,
        "score": round(float(getattr(result, "score", 0.0) or 0.0), 4),
    }


class KBTools:
    """知识库检索工具集，包装现有混合检索器与重排序器。"""

    def __init__(self, retriever, reranker=None, top_k: int = AGENT_RETRIEVAL_TOP_K,
                 embedder=None):
        self.retriever = retriever
        self.reranker = reranker
        self.top_k = top_k
        self.embedder = embedder or getattr(retriever, "embedder", None)

    # ---------------------------------------------------------------- 工具注册表
    @property
    def tools(self) -> list[dict]:
        """工具清单（name/description），供 Supervisor/前端展示。"""
        return [
            {
                "name": "kb_search_keyword",
                "description": "在知识库中进行关键词/语义检索，返回相关文本片段、来源文件、章节与相关度。",
            },
            {
                "name": "kb_search_category",
                "description": "按文档类别检索（如法律法规/技术文档/操作手册），返回该类别的相关文本片段。",
            },
            {
                "name": "kb_filter_metadata",
                "description": "按元数据过滤检索（来源 source、标签、作者等），先过滤再检索。",
            },
            {
                "name": "kb_list_documents",
                "description": "列出知识库现有文档清单（可选按类别过滤），用于判断检索方向。",
            },
            {
                "name": "kb_compare_documents",
                "description": "多文档对比检索，针对'对比 X 与 Y 的差异'类问题，分别取两文档相关片段。",
            },
        ]

    # ---------------------------------------------------------------- 工具实现
    def kb_search_keyword(self, query: str, top_k: int | None = None) -> dict:
        """关键词/语义混合检索（复用 retriever + reranker）。"""
        k = top_k or self.top_k
        results = self.retriever.retrieve(query, top_k=k)
        if not results:
            return {"query": query, "results": [], "note": "无检索结果"}

        # 可选 reranker 精排
        if self.reranker is not None:
            try:
                docs = [{"content": r.content, "metadata": dict(r.metadata or {})} for r in results]
                reranked = self.reranker.rerank(query, docs, top_k=len(results))
                results = reranked
            except Exception as e:  # noqa: BLE001
                logger.warning("Agent 检索 reranker 失败，使用原始排序: %s", e)

        return {
            "query": query,
            "results": [_to_evidence(r) for r in results[:k]],
        }

    def kb_search_category(self, query: str, category: str,
                           top_k: int | None = None) -> dict:
        """按文档类别检索。

        类别匹配：优先 metadata 中的 category/doc_category/doc_type 字段；
        否则以 source_file 的父目录名作为类别。先大召回再按类别过滤 + rerank。
        """
        k = top_k or self.top_k
        category = (category or "").strip()
        if not category:
            return self.kb_search_keyword(query, top_k=k)

        # 大召回，避免类别过滤后数量不足
        recall = max(k * 5, 20)
        try:
            results = self.retriever.retrieve(query, top_k=recall)
        except Exception as e:  # noqa: BLE001
            logger.warning("按类别检索失败 %r: %s", category, e)
            return {"query": query, "results": [], "note": f"检索失败: {e}"}

        filtered = [r for r in results if self._category_matches(r.metadata or {}, category)]
        if not filtered:
            return {
                "query": query, "results": [],
                "note": f"类别「{category}」下无匹配片段",
            }
        if self.reranker is not None:
            try:
                docs = [{"content": r.content, "metadata": dict(r.metadata or {})} for r in filtered]
                filtered = self.reranker.rerank(query, docs, top_k=len(filtered))
            except Exception as e:  # noqa: BLE001
                logger.warning("按类别检索 reranker 失败，使用原始排序: %s", e)

        return {"query": query, "results": [_to_evidence(r) for r in filtered[:k]]}

    def kb_filter_metadata(self, query: str, top_k: int | None = None,
                           **filters) -> dict:
        """按元数据字段过滤后检索。

        用法：kb_filter_metadata("预算", source="xxx.md", author="张三")
        filters 的每个 key 取 metadata 同名字段做包含匹配（忽略大小写）。
        """
        k = top_k or self.top_k
        filters = {kk: vv for kk, vv in filters.items() if vv not in (None, "")}
        if not filters:
            return self.kb_search_keyword(query, top_k=k)

        chunks = self._all_chunks()
        if not chunks:
            return {"query": query, "results": [], "note": "知识库为空或无法读取"}
        matched = [c for c in chunks if self._metadata_match(c["metadata"], filters)]
        if not matched:
            return {
                "query": query, "results": [],
                "note": f"无满足元数据过滤条件 {filters} 的片段",
            }
        ranked = self._sort_by_query(query, matched, k)
        return {"query": query, "results": ranked}

    def kb_list_documents(self, category: str | None = None) -> dict:
        """列出知识库现有文档（可选按类别过滤），基于检索器底层向量库尽力而为。"""
        names = []
        try:
            vs = getattr(self.retriever, "vector_store", None)
            if vs is not None:
                data = vs.get_all() if hasattr(vs, "get_all") else {}
                metas = data.get("metadatas") or []
                seen = set()
                for m in metas:
                    m = m or {}
                    if category and not self._category_matches(m, category):
                        continue
                    sf = m.get("source_file", "") or m.get("source", "")
                    if sf and sf not in seen:
                        seen.add(sf)
                        names.append(sf.replace("\\", "/").rsplit("/", 1)[-1])
        except Exception as e:  # noqa: BLE001
            logger.warning("列出知识库文档失败: %s", e)
        return {"documents": sorted(names)}

    def kb_compare_documents(self, doc_a: str, doc_b: str,
                             query: str | None = None,
                             top_k: int | None = None) -> dict:
        """多文档对比检索：分别从 doc_a / doc_b 中取与 query 最相关的片段。

        doc_a / doc_b 可为完整文件名或关键词（对 metadata.source_file 做包含匹配）。
        """
        k = top_k or self.top_k
        chunks = self._all_chunks()
        if not chunks:
            return {"query": query or "", "results": [], "note": "知识库为空或无法读取"}
        q = query or f"{doc_a} 与 {doc_b} 的对比"
        a_chunks = [c for c in chunks if self._match_source(c["metadata"], doc_a)]
        b_chunks = [c for c in chunks if self._match_source(c["metadata"], doc_b)]
        results: list[dict] = []
        notes = []
        for doc_name, doc_chunks in ((doc_a, a_chunks), (doc_b, b_chunks)):
            if not doc_chunks:
                notes.append(f"未找到文档「{doc_name}」")
                continue
            results.extend(self._sort_by_query(q, doc_chunks, k))
        return {
            "query": q,
            "results": results,
            "note": "；".join(notes) if notes else "",
        }

    # ---------------------------------------------------------------- 统一调度入口
    def call_tool(self, name: str, **kwargs) -> dict:
        """按工具名分发调用，供 Retriever-Agent 使用。"""
        if name == "kb_search_keyword":
            return self.kb_search_keyword(**kwargs)
        if name == "kb_search_category":
            return self.kb_search_category(**kwargs)
        if name == "kb_filter_metadata":
            return self.kb_filter_metadata(**kwargs)
        if name == "kb_list_documents":
            return self.kb_list_documents()
        if name == "kb_compare_documents":
            return self.kb_compare_documents(**kwargs)
        raise ValueError(f"未知知识库工具: {name}")

    # ---------------------------------------------------------------- 内部辅助
    def _all_chunks(self) -> list[dict]:
        """从底层向量库读取全部片段：返回 [{"content":..., "metadata": {...}}]。"""
        try:
            vs = getattr(self.retriever, "vector_store", None)
            if vs is None or not hasattr(vs, "get_all"):
                return []
            data = vs.get_all()
            if not data or not data.get("documents"):
                return []
            docs = data["documents"]
            metas = data.get("metadatas") or [{}] * len(docs)
            return [
                {"content": d, "metadata": dict(m or {})}
                for d, m in zip(docs, metas)
            ]
        except Exception as e:  # noqa: BLE001
            logger.warning("读取知识库全量片段失败: %s", e)
            return []

    @staticmethod
    def _category_of(metadata: dict) -> str:
        for key in _CATEGORY_KEYS:
            val = metadata.get(key)
            if val:
                return str(val)
        sf = metadata.get("source_file", "") or metadata.get("source", "")
        if sf:
            parts = sf.replace("\\", "/").split("/")
            if len(parts) >= 2:
                return parts[-2]
        return ""

    def _category_matches(self, metadata: dict, category: str) -> bool:
        cat = self._category_of(metadata)
        category = (category or "").strip().lower()
        if not category:
            return True
        cat_l = cat.lower()
        return category in cat_l or cat_l in category

    @staticmethod
    def _match_source(metadata: dict, doc_name: str) -> bool:
        sf = metadata.get("source_file", "") or metadata.get("source", "")
        doc_name = (doc_name or "").strip()
        if not doc_name or not sf:
            return False
        base = sf.replace("\\", "/").rsplit("/", 1)[-1]
        return doc_name == base or doc_name in sf or doc_name in base

    @staticmethod
    def _metadata_match(metadata: dict, filters: dict) -> bool:
        for key, val in filters.items():
            mv = metadata.get(key)
            # "source" 是 "source_file" 的别名，兼容用户/LLM 传入
            if mv is None and key == "source":
                mv = metadata.get("source_file")
            if mv is None:
                return False
            if not isinstance(val, str) or not isinstance(mv, str):
                if mv != val:
                    return False
                continue
            if val.lower() not in mv.lower():
                return False
        return True

    def _sort_by_query(self, query: str, chunks: list[dict], top_k: int) -> list[dict]:
        """按与 query 的相关度对 chunks（content/metadata）排序取 top_k。"""
        if self.embedder is not None:
            try:
                qv = self.embedder.embed_single(query)
                scored = []
                for c in chunks:
                    cv = self.embedder.embed_single(c["content"])
                    scored.append((self._cosine(qv, cv), c))
                scored.sort(key=lambda t: t[0], reverse=True)
                return [self._to_evidence_from_chunk(c, s)
                        for s, c in scored[:top_k]]
            except Exception as e:  # noqa: BLE001
                logger.warning("元数据/对比检索 embedder 失败，回退关键词排序: %s", e)
        # 兜底：query 关键词命中计数排序
        words = [w for w in re.split(r"[\s，。、？！,.;：:]+", query) if w]

        def _hits(c: dict) -> int:
            return sum(1 for w in words if w in c["content"])

        ranked = sorted(chunks, key=_hits, reverse=True)
        return [self._to_evidence_from_chunk(c, 0.0) for c in ranked[:top_k]]

    @staticmethod
    def _to_evidence_from_chunk(chunk: dict, score: float) -> dict:
        meta = dict(chunk.get("metadata") or {})
        source_file = meta.get("source_file", "") or meta.get("source", "")
        if "\\" in source_file or "/" in source_file:
            meta["source_file"] = source_file.replace("\\", "/").rsplit("/", 1)[-1]
        return {
            "content": chunk.get("content", ""),
            "metadata": meta,
            "score": round(float(score or 0.0), 4),
        }

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)
