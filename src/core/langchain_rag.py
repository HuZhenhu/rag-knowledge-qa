"""LangChain版RAG引擎 — 使用LangChain组件重构RAG链路
检索质量优化：默认引擎接通 混合检索(BM25+向量+RRF) + 查询增强(纠错/扩展/HyDE双路) + 重排(Top-50→Top-5)
"""
import logging
import time
from dataclasses import dataclass, field

from langchain_openai import ChatOpenAI
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_chroma import Chroma
from langchain_community.retrievers import BM25Retriever
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough
from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    CHROMA_DB_DIR,
    EMBEDDING_MODEL,
    RETRIEVAL_TOP_K,
    RETRIEVAL_CANDIDATE_K,
    RRF_K,
    HYBRID_VECTOR_WEIGHT,
    HYBRID_BM25_WEIGHT,
    USE_HYBRID_RETRIEVAL,
    USE_RERANKER,
    USE_QUERY_EXPANSION,
    USE_QUERY_CORRECTION,
    USE_HYDE,
    RELEVANCE_THRESHOLD,
    CHUNK_SIZE,
    CHUNK_OVERLAP,
    USE_PARENT_CHILD,
    CHILD_TOKEN_SIZE,
    PARENT_TOKEN_SIZE,
    ACL_ENFORCE,
    USE_PII_REDACTION,
    PII_REDACT_MODE,
    PII_PLACEHOLDER,
)
from src.core.tracer import Trace
from src.core.acl import enrich_acl_metadata, assert_sources_allowed, allowed_doc_ids_from_filter
from src.core.pii_redactor import redact_texts, mask_text

logger = logging.getLogger(__name__)


@dataclass
class LangChainRAGResponse:
    """LangChain RAG响应"""
    answer: str
    sources: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    timing: dict = field(default_factory=dict)
    trace_id: str = ""


class LangChainRAGEngine:
    """基于LangChain的RAG引擎

    检索链路（优化后）：
        查询纠错 → 查询扩展(多子查询) → 混合检索(向量+BM25 加权RRF, 大召回 Top-candidate_k)
        → [可选] HyDE 双路融合(原始query + HyDE向量路) → [可选] bge-reranker 精排 → Top-5 进 LLM
    说明：RELEVANCE_THRESHOLD 由硬过滤降级为参考阈值（仅记录，不截断候选）。
    """

    def __init__(
        self,
        use_hybrid: bool = USE_HYBRID_RETRIEVAL,
        top_k: int = RETRIEVAL_TOP_K,
        relevance_threshold: float | None = None,
        temperature: float | None = None,
        use_query_expansion: bool = USE_QUERY_EXPANSION,
        use_reranker: bool = USE_RERANKER,
        use_query_correction: bool = USE_QUERY_CORRECTION,
        use_hyde: bool = USE_HYDE,
        candidate_k: int = RETRIEVAL_CANDIDATE_K,
        use_parent_child: bool = USE_PARENT_CHILD,
        acl_enforce: bool = ACL_ENFORCE,
    ):
        self.top_k = top_k
        self.use_hybrid = use_hybrid
        self.relevance_threshold = relevance_threshold if relevance_threshold is not None else RELEVANCE_THRESHOLD
        self.temperature = temperature if temperature is not None else 0.0
        self.use_query_expansion = use_query_expansion
        self.use_reranker = use_reranker
        self.use_query_correction = use_query_correction
        self.use_hyde = use_hyde
        self.candidate_k = candidate_k
        self.use_parent_child = use_parent_child
        self.acl_enforce = acl_enforce  # P0-1: 检索链路 ACL 开关（默认关）
        self._reranker = None
        self._query_understander = None
        self._bm25_metadatas = None  # BM25 索引对应的 chunk 元数据（ACL 过滤用）

        # 初始化LLM
        self.llm = ChatOpenAI(
            model=DEEPSEEK_MODEL,
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
            temperature=self.temperature,
            max_tokens=2048,
        )

        # 初始化Embeddings（使用本地HuggingFace模型）
        self.embeddings = HuggingFaceEmbeddings(
            model_name=EMBEDDING_MODEL,
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )

        # 初始化Chroma向量存储（使用与原版相同的集合名）
        self.vectorstore = Chroma(
            collection_name="knowledge_base",
            embedding_function=self.embeddings,
            persist_directory=str(CHROMA_DB_DIR),
        )

        # 初始化检索器
        self.vector_retriever = self.vectorstore.as_retriever(
            search_type="similarity",
            search_kwargs={"k": self.top_k},
        )

        # BM25检索器（懒加载）
        self._bm25_retriever = None

        # 如果启用混合检索，构建 BM25 索引（大召回 candidate_k）
        if self.use_hybrid:
            self._setup_bm25_index()

        # 构建RAG Chain
        self._build_chain()

    def _setup_bm25_index(self):
        """构建 BM25 检索索引（大召回 candidate_k，供 RRF 融合）"""
        try:
            # 从Chroma获取所有文档用于构建BM25索引
            all_docs = self.vectorstore.get()
            if all_docs and all_docs.get("documents"):
                texts = all_docs["documents"]
                metas = all_docs.get("metadatas") or [{}] * len(texts)
                self._bm25_metadatas = list(metas)
                self._bm25_retriever = BM25Retriever.from_texts(
                    texts,
                    metadatas=self._bm25_metadatas,
                    k=self.candidate_k,
                )
                logger.info("BM25索引初始化完成，共 %d 个文档，候选数 k=%d", len(texts), self.candidate_k)
            else:
                logger.info("向量库为空，使用纯向量检索")
                self.use_hybrid = False
        except Exception as e:
            logger.warning("BM25索引初始化失败: %s，回退到纯向量检索", e)
            self.use_hybrid = False

    def _get_query_understander(self):
        """懒加载 QueryUnderstander"""
        if self._query_understander is None:
            from src.core.query_understander import QueryUnderstander
            self._query_understander = QueryUnderstander()
        return self._query_understander

    def _build_chain(self):
        """构建RAG Chain"""
        # Prompt模板
        self.prompt = ChatPromptTemplate.from_messages([
            ("system", """你是一个专业的知识库问答助手。请根据以下上下文信息回答用户的问题。

规则：
1. 只基于提供的上下文回答，不要编造信息
2. 如果上下文中没有相关信息，请明确说明"知识库中未找到相关信息"
3. 回答要准确、简洁、专业
4. 在回答末尾标注引用来源，格式为 [来源X]

上下文信息：
{context}"""),
            ("human", "{question}"),
        ])

        # 检索在 query/query_stream 中显式执行并注入 context（绕过 chain 内部重复检索）
        self.chain = (
            {"context": lambda _: "", "question": RunnablePassthrough()}
            | self.prompt
            | self.llm
            | StrOutputParser()
        )
        self._retriever = self.vector_retriever

    def _hybrid_retrieve(self, query: str, candidate_k: int | None = None, acl_filter: dict | None = None) -> list[tuple[object, float]]:
        """混合检索：向量 + BM25 加权 RRF 融合，返回 [(doc, rrf_score)] 按分数降序

        RRF: score = w_v/(k+rank_v) + w_b/(k+rank_b)，k=RRF_K 默认 60。
        向量用完整 query，BM25 用精炼关键词，双路各自召回 candidate_k 再融合。
        P0-1: acl_filter 非空时，向量路走 Chroma where（检索前过滤），BM25 路按 doc_id 二次过滤。
        """
        k = candidate_k or self.candidate_k
        # 向量路（P0-1: 检索前过滤）
        vector_raw = self.vectorstore.similarity_search_with_score(query, k=k, filter=acl_filter)
        nv = len(vector_raw)
        # BM25 路（按 BM25 得分降序；P0-1: 按 doc_id 过滤越权 chunk）
        bm25_docs = []
        if self.use_hybrid and self._bm25_retriever is not None:
            try:
                raw_bm25 = self._bm25_retriever.invoke(query)
                if acl_filter:
                    allowed = allowed_doc_ids_from_filter(acl_filter)
                    if allowed is not None:
                        raw_bm25 = [
                            d for d in raw_bm25
                            if (d.metadata or {}).get("doc_id") in allowed
                        ]
                bm25_docs = raw_bm25
            except Exception as e:
                logger.warning("BM25检索失败: %s", e)
                bm25_docs = []
        nb = len(bm25_docs)

        entries = {}
        for rank, (doc, _dist) in enumerate(vector_raw):
            key = doc.page_content[:200]
            e = entries.setdefault(key, {"doc": doc, "v": nv + 1, "b": nb + 1})
            e["v"] = rank + 1
        for rank, doc in enumerate(bm25_docs):
            key = doc.page_content[:200]
            e = entries.setdefault(key, {"doc": doc, "v": nv + 1, "b": nb + 1})
            e["b"] = rank + 1

        fused = []
        for _key, e in entries.items():
            rrf = (HYBRID_VECTOR_WEIGHT / (RRF_K + e["v"])) + \
                  (HYBRID_BM25_WEIGHT / (RRF_K + e["b"]))
            fused.append((e["doc"], rrf))
        fused.sort(key=lambda x: x[1], reverse=True)
        return fused

    def _retrieve_multi(self, question: str, candidate_k: int | None = None, acl_filter: dict | None = None) -> list[tuple[object, float]]:
        """多路检索：多子查询(扩展) + HyDE 双路融合，返回候选池 [(doc, score)] 按分数降序

        所有路的结果合并到一个 pool，取最高分去重；返回 Top-candidate_k。
        HyDE 路分数用 RRF 量纲(1/(RRF_K+rank))，与 query 路 RRF 分数同量纲可比较。
        P0-1: acl_filter 透传给每条检索路。
        """
        k = candidate_k or self.candidate_k
        queries = [question]
        if self.use_query_expansion:
            try:
                expansion = self._get_query_understander().expand_query(question)
                if expansion.expanded_queries:
                    queries = expansion.expanded_queries[:3]
            except Exception as e:
                logger.warning("查询扩展失败: %s", e)

        pool: dict[str, tuple[object, float]] = {}
        for q in queries:
            try:
                for doc, score in self._hybrid_retrieve(q, k, acl_filter=acl_filter):
                    key = doc.page_content[:200]
                    if key not in pool or score > pool[key][1]:
                        pool[key] = (doc, score)
            except Exception as e:
                logger.warning("子查询检索失败(%s): %s", q[:30], e)

        # HyDE 双路：原始 query 路已在上面；这里补 HyDE 向量路，命中后取更高分
        if self.use_hyde:
            try:
                hyde_text = self._get_query_understander().generate_hyde(question)
                hyde_emb = self.embeddings.embed_query(hyde_text)
                hyde_raw = self.vectorstore.similarity_search_by_vector_with_relevance_scores(
                    hyde_emb, k=k, filter=acl_filter,
                )
                for rank, (doc, _dist) in enumerate(hyde_raw):
                    key = doc.page_content[:200]
                    hyde_score = 1.0 / (RRF_K + rank + 1)
                    if key not in pool or hyde_score > pool[key][1]:
                        pool[key] = (doc, hyde_score)
            except Exception as e:
                logger.warning("HyDE检索失败: %s", e)

        ranked = sorted(pool.values(), key=lambda x: x[1], reverse=True)
        return ranked[:k]

    def _build_sources(self, question: str, scored_docs: list[tuple[object, float]], top_k: int) -> list[dict]:
        """构建 sources 并执行重排（可选）

        阈值 RELEVANCE_THRESHOLD 已从硬过滤降级为参考阈值：不再据此丢弃候选，
        由重排(或 RRF 排序)决定 Top-K。启用重排时：混合召回 → bge-reranker 精排 → Top-K。
        """
        sources = []
        for doc, score in scored_docs:
            meta = dict(doc.metadata)
            source_file = meta.get("source_file", "") or meta.get("source", "未知")
            meta["source_file"] = source_file
            sources.append({
                "content": doc.page_content,
                "metadata": meta,
                "score": round(float(score), 4),
            })

        # 重排：对全量候选精排后取 Top-K
        if self.use_reranker and sources:
            rerank_start = time.time()
            try:
                if self._reranker is None:
                    from src.core.reranker import Reranker
                    self._reranker = Reranker()
                reranked = self._reranker.rerank(
                    question,
                    [{"content": s["content"], "metadata": s["metadata"]} for s in sources],
                    top_k=top_k,
                )
                rerank_map = {r.content: r.score for r in reranked}
                new_sources = []
                for r in reranked:
                    matched = next((s for s in sources if s["content"] == r.content), None)
                    if matched is None:
                        continue
                    matched = dict(matched)
                    matched["score"] = round(float(r.score), 4)
                    new_sources.append(matched)
                if new_sources:
                    sources = new_sources
                logger.info("重排完成：候选 %d → Top-%d", len(sources), len(new_sources))
                self._last_rerank_ms = round((time.time() - rerank_start) * 1000, 2)
            except Exception as e:
                logger.warning("ReRanker失败，跳过: %s", e)
                sources = sources[:top_k]
        else:
            sources = sources[:top_k]
        return sources

    def _resolve_parent(self, sources: list[dict]) -> list[dict]:
        """父子切片回取（P1-2）：命中 child 时，将其替换为所属 parent 整块，并按 parent 去重

        检索以 child（100~200 tokens）为单元保证精度；生成阶段用 parent（600~800 tokens）
        提供完整上下文。多个 child 命中同一 parent 时只保留一块，避免上下文冗余。
        """
        if not self.use_parent_child:
            return sources
        resolved: list[dict] = []
        seen: set[str] = set()
        for s in sources:
            parent = (s.get("metadata") or {}).get("parent_content")
            if parent and parent not in seen:
                seen.add(parent)
                s = dict(s)
                s["content"] = parent
                resolved.append(s)
            elif not parent:
                resolved.append(s)
        return resolved

    def query(
        self,
        question: str,
        top_k: int | None = None,
        history: list[dict] | None = None,
        summary: str = "",
        user_id: str = "",
        acl_filter: dict | None = None,
    ) -> LangChainRAGResponse:
        """执行RAG问答

        Args:
            question: 用户问题
            top_k: 检索结果数量（覆盖默认值）
            history: 对话历史（暂未使用，预留接口）

        Returns:
            LangChainRAGResponse: RAG响应
        """
        start_time = time.time()
        timing = {}

        try:
            # 0. 查询纠错（P1-1）
            effective_q = question
            if self.use_query_correction:
                try:
                    corrected = self._get_query_understander().correct_query(question)
                    if corrected and corrected.strip() and corrected.strip() != question:
                        effective_q = corrected.strip()
                        logger.info("查询纠错: %s -> %s", mask_text(question), mask_text(effective_q))
                except Exception as e:
                    logger.warning("查询纠错失败: %s", e)

            # 1. 混合检索（大召回 Top-candidate_k；P0-1: 透传 acl_filter 检索前过滤）
            retrieval_start = time.time()
            top_k = top_k or self.top_k
            scored_docs = self._retrieve_multi(effective_q, self.candidate_k, acl_filter=acl_filter)
            timing["retrieval_ms"] = round((time.time() - retrieval_start) * 1000, 2)

            # 2. 构建 sources + 重排精排 → Top-K
            sources = self._build_sources(effective_q, scored_docs, top_k)
            timing["rerank_ms"] = round(getattr(self, "_last_rerank_ms", 0), 2)

            # 2.5 父子切片回取：child → parent（P1-2）
            sources = self._resolve_parent(sources)

            # 2.6 P0-1 运行时归属断言（防检索前过滤被绕过/重排引入越权项）
            if self.acl_enforce and acl_filter:
                allowed_ids = allowed_doc_ids_from_filter(acl_filter)
                if allowed_ids is not None:
                    sources, removed = assert_sources_allowed(sources, allowed_ids)
                    if removed:
                        logger.warning("ACL运行时断言：剔除 %d 条越权来源", removed)

            # 3. 生成回答（用与 sources 一致的文档，绕过 chain 内部重复检索）
            generation_start = time.time()
            if not sources:
                answer = "知识库中未找到相关信息"
                usage = {}
            else:
                context = "\n\n".join(
                    f"[来源{i+1}: {s['metadata'].get('source_file','未知来源')}]\n{s['content']}"
                    for i, s in enumerate(sources)
                )
                chain_out = (
                    {"context": lambda _: context, "question": RunnablePassthrough()}
                    | self.prompt | self.llm | StrOutputParser()
                )
                answer = chain_out.invoke(question)
                usage = {}  # LangChain不直接暴露token使用量
            timing["generation_ms"] = round((time.time() - generation_start) * 1000, 2)

            total_time = (time.time() - start_time) * 1000
            timing["total_ms"] = round(total_time, 2)

            return LangChainRAGResponse(
                answer=answer,
                sources=sources,
                usage=usage,
                timing=timing,
            )

        except Exception as e:
            logger.error("LangChain RAG查询失败: %s", e)
            total_time = (time.time() - start_time) * 1000
            return LangChainRAGResponse(
                answer=f"查询失败: {str(e)}",
                sources=[],
                usage={},
                timing={"total_ms": round(total_time, 2)},
            )

    def query_stream(self, question: str, top_k: int | None = None,
                     history: list[dict] | None = None,
                     summary: str = "",
                     user_id: str = "",
                     acl_filter: dict | None = None):
        """流式RAG问答

        Yields:
            tuple: (token_str, is_last, sources, timing)
        """
        trace = Trace(question, user_id=user_id)
        start_time = time.time()
        timing = {}

        try:
            # 0. 查询纠错
            effective_q = question
            if self.use_query_correction:
                try:
                    corrected = self._get_query_understander().correct_query(question)
                    if corrected and corrected.strip() and corrected.strip() != question:
                        effective_q = corrected.strip()
                except Exception:
                    pass

            # 1. 混合检索
            trace.start_span("retrieval")
            retrieval_start = time.time()
            top_k = top_k or self.top_k
            scored_docs = self._retrieve_multi(effective_q, self.candidate_k, acl_filter=acl_filter)
            timing["retrieval_ms"] = round((time.time() - retrieval_start) * 1000, 2)
            trace.end_span({"docs_count": len(scored_docs)})

            # 2. 构建sources + 重排
            sources = self._build_sources(effective_q, scored_docs, top_k)
            timing["rerank_ms"] = round(getattr(self, "_last_rerank_ms", 0), 2)

            # 2.5 父子切片回取：child → parent（P1-2）
            sources = self._resolve_parent(sources)

            # 2.6 P0-1 运行时归属断言（防检索前过滤被绕过）
            if self.acl_enforce and acl_filter:
                allowed_ids = allowed_doc_ids_from_filter(acl_filter)
                if allowed_ids is not None:
                    sources, removed = assert_sources_allowed(sources, allowed_ids)
                    if removed:
                        logger.warning("ACL运行时断言：剔除 %d 条越权来源", removed)

            # 3. 流式生成（注入 context，避免 chain 内部重复检索）
            trace.start_span("generation")
            generation_start = time.time()
            if not sources:
                yield "知识库中未找到相关信息", True, sources, timing
                trace.end_span({"status": "no_sources"})
                return

            context = "\n\n".join(
                f"[来源{i+1}: {s['metadata'].get('source_file','未知来源')}]\n{s['content']}"
                for i, s in enumerate(sources)
            )
            chain_out = (
                {"context": lambda _: context, "question": RunnablePassthrough()}
                | self.prompt | self.llm | StrOutputParser()
            )

            full_answer = ""
            for chunk in chain_out.stream(question):
                if chunk:
                    full_answer += chunk
                    yield chunk, False, sources, timing

            timing["generation_ms"] = round((time.time() - generation_start) * 1000, 2)
            timing["total_ms"] = round((time.time() - start_time) * 1000, 2)
            trace.end_span({"answer_length": len(full_answer)})

            yield "", True, sources, timing

        except Exception as e:
            logger.error("LangChain RAG流式查询失败: %s", e)
            trace.status = "error"
            yield f"查询失败: {str(e)}", True, [], {"total_ms": round((time.time() - start_time) * 1000, 2)}
        finally:
            trace.finish()

    def add_documents(self, texts: list[str], metadatas: list[dict] | None = None):
        """添加文档到向量存储

        Args:
            texts: 文档文本列表
            metadatas: 元数据列表（可选）
        """
        # P0-2 PII 脱敏（入库前，开关默认关）
        if USE_PII_REDACTION:
            texts = redact_texts(texts, mode=PII_REDACT_MODE, placeholder=PII_PLACEHOLDER)
            logger.info("PII脱敏已启用：%d 个文档入库前脱敏", len(texts))
        # P0-1 chunk 级 ACL 元数据注入（doc_id 等，供检索前过滤/断言）
        if metadatas:
            metadatas = [enrich_acl_metadata(m) for m in metadatas]

        # 父子切片模式（P1-2）：child 入库（检索单元），metadata 携带 parent_content 供回取
        if self.use_parent_child:
            self._add_documents_parent_child(texts, metadatas)
            logger.info("父子切片模式：已添加文档并重建索引")
            return

        # 文本切分
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
            length_function=len,
            separators=["\n\n", "\n", "。", "！", "？", "，", " "],
        )

        split_texts = []
        split_metadatas = []

        for i, text in enumerate(texts):
            chunks = text_splitter.split_text(text)
            split_texts.extend(chunks)
            if metadatas and i < len(metadatas):
                split_metadatas.extend([metadatas[i]] * len(chunks))

        # 添加到Chroma
        if split_metadatas:
            self.vectorstore.add_texts(split_texts, metadatas=split_metadatas)
        else:
            self.vectorstore.add_texts(split_texts)

        logger.info("添加 %d 个文档块到向量存储", len(split_texts))

        # 如果启用了混合检索，需要重建BM25索引
        if self.use_hybrid:
            self._setup_bm25_index()

    def _add_documents_parent_child(self, texts: list[str], metadatas: list[dict] | None = None):
        """父子切片入库：child 进向量库，parent 全文存于 child 的 metadata"""
        # P0-2 PII 脱敏（入库前，开关默认关）
        if USE_PII_REDACTION:
            texts = redact_texts(texts, mode=PII_REDACT_MODE, placeholder=PII_PLACEHOLDER)
            logger.info("PII脱敏已启用：%d 个文档父子切片前脱敏", len(texts))
        # P0-1 chunk 级 ACL 元数据注入
        if metadatas:
            metadatas = [enrich_acl_metadata(m) for m in metadatas]

        from src.core.parent_child_splitter import ParentChildSplitter
        splitter = ParentChildSplitter(
            child_token_size=CHILD_TOKEN_SIZE,
            parent_token_size=PARENT_TOKEN_SIZE,
        )
        split_texts: list[str] = []
        split_metadatas: list[dict] = []
        for i, text in enumerate(texts):
            meta = dict(metadatas[i]) if metadatas and i < len(metadatas) else {}
            meta["content_type"] = meta.get("content_type", "parent_child_child")
            for chunk in splitter.split(text, meta):
                split_texts.append(chunk.content)
                split_metadatas.append(chunk.metadata)
        if split_texts:
            self.vectorstore.add_texts(split_texts, metadatas=split_metadatas)
            logger.info("父子切片模式：添加 %d 个 child 块到向量存储", len(split_texts))
        # 重建 BM25 索引（索引对象是 child 文本）
        if self.use_hybrid:
            self._setup_bm25_index()

    def get_retriever(self):
        """获取当前使用的检索器"""
        return self.vector_retriever

    def query_by_source(self, source_filename: str) -> list[dict]:
        """按源文件查询chunks（兼容原版VectorStore接口）

        Args:
            source_filename: 源文件名

        Returns:
            list[dict]: chunk列表，每个包含chunk_id, content, metadata等
        """
        # metadata中source_file是完整路径，需要模糊匹配
        # 注意：ids 默认返回，不能放在 include 里
        all_results = self.vectorstore.get(include=["metadatas", "documents"])
        chunks = []
        if all_results and all_results.get("documents"):
            for i, doc in enumerate(all_results["documents"]):
                meta = all_results["metadatas"][i] if all_results.get("metadatas") else {}
                chunk_id = all_results["ids"][i] if all_results.get("ids") else f"chunk_{i}"
                sf = meta.get("source_file", "")
                # 匹配：完整路径包含文件名，或文件名包含路径
                if source_filename and (source_filename in sf or sf.endswith(source_filename)):
                    chunks.append({
                        "chunk_id": chunk_id,
                        "content": doc,
                        "section": meta.get("section", ""),
                        "page_number": meta.get("page_number"),
                        "content_type": meta.get("content_type", "text"),
                        "metadata": meta,
                    })
        return chunks
