"""LangChain版RAG引擎 — 使用LangChain组件重构RAG链路
检索质量优化：默认引擎接通 混合检索(BM25+向量+RRF) + 查询增强(纠错/扩展/HyDE双路) + 重排(Top-50→Top-5)
P1-3: 精确缓存(QueryCache) + 语义缓存(SemanticCache) 接入默认引擎，key 含 ACL 指纹
P1-4: 多路检索并行化(ThreadPoolExecutor) + 简单事实问题条件化跳过 HyDE
P1-6: 置信度硬门控/低置信拒答（默认关）
"""
import concurrent.futures
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field

from langchain_openai import ChatOpenAI
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough
from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.core.base_engine import BaseRAGEngine
from src.core.bm25_retriever import BM25Retriever

from src.core.tracer import Trace
from src.core.acl import enrich_acl_metadata, assert_sources_allowed, allowed_doc_ids_from_filter
from src.core.pii_redactor import redact_texts, mask_text
from src.core.query_cache import get_query_cache
from src.core.semantic_cache import get_semantic_cache
from src.core.metrics import metrics
from src.core.citation import (
    build_context_with_citations,
    parse_citations,
    validate_citations,
    build_citation_spans,
)
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
    QUERY_CACHE_ENABLED,
    SEMANTIC_CACHE_ENABLED,
    SEMANTIC_CACHE_THRESHOLD,
    SEMANTIC_CACHE_DOMAIN,
    SEMANTIC_CACHE_HOT_QUESTIONS,
    PARALLEL_RETRIEVAL_WORKERS,
    HYDE_SKIP_SIMPLE,
    ENABLE_CONFIDENCE_REFUSE,
    CONFIDENCE_REFUSE_THRESHOLD,
    USE_CITATION_VERIFY,
    LLM_GUARD_ENABLED,
    MODEL_ROUTER_ENABLED,
)
from src.core.llm_guard import guarded_llm_invoke, get_llm_guard

logger = logging.getLogger(__name__)


@dataclass
class LangChainRAGResponse:
    """LangChain RAG响应"""
    answer: str
    sources: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    timing: dict = field(default_factory=dict)
    trace_id: str = ""
    confidence: float | None = None  # P1-6: 置信度（启用门控时透出）
    citation_spans: list = field(default_factory=list)  # P2-7: 引用 span 列表


class LangChainRAGEngine(BaseRAGEngine):
    """基于LangChain的RAG引擎

    检索链路（优化后）：
        查询纠错 → 查询扩展(多子查询) → 混合检索(向量+BM25 加权RRF, 大召回 Top-candidate_k)
        → [可选] HyDE 双路融合(原始query + HyDE向量路) → [可选] bge-reranker 精排 → Top-5 进 LLM
    说明：RELEVANCE_THRESHOLD 由硬过滤降级为参考阈值（仅记录，不截断候选）。
    """
    engine_name = "langchain"

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
        parallel_workers: int = PARALLEL_RETRIEVAL_WORKERS,
        hyde_skip_simple: bool = HYDE_SKIP_SIMPLE,
        use_confidence_refuse: bool = ENABLE_CONFIDENCE_REFUSE,
        confidence_refuse_threshold: float = CONFIDENCE_REFUSE_THRESHOLD,
        use_citation_verify: bool = USE_CITATION_VERIFY,
        model_router=None,
        enable_model_router: bool | None = None,
        cache_domain: str | None = None,
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
        # P1-4: 多路检索并行线程数 / 简单事实问题跳过 HyDE
        self.parallel_workers = max(1, int(parallel_workers))
        self.hyde_skip_simple = hyde_skip_simple
        # P1-6: 置信度门控开关与阈值（默认关，灰度开启）
        self.use_confidence_refuse = use_confidence_refuse
        self.confidence_refuse_threshold = float(confidence_refuse_threshold)
        # P2-7: 引用真实性校验开关（默认关）
        self.use_citation_verify = use_citation_verify
        self._last_confidence: float | None = None
        self._last_rerank_ms = 0.0
        # P1-3: 缓存（进程内单例，索引失效钩子可清同一实例）
        self.query_cache = get_query_cache()
        self.semantic_cache = get_semantic_cache()
        # T3.2: 语义缓存按部署领域调优阈值（SEMANTIC_CACHE_DOMAIN_THRESHOLDS 中配置对应领域）
        self.cache_domain = (cache_domain or SEMANTIC_CACHE_DOMAIN) or None
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
        # T3.1 模型分级：路由开关（默认关时为 None，保持 legacy 单模型链路）
        self.model_router = model_router
        self.enable_model_router = (
            MODEL_ROUTER_ENABLED if enable_model_router is None else enable_model_router
        )
        self.llm_factory = None  # 注入用（测试/自定义）；None 时按配置创建 ChatOpenAI

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

        # T1.5: LLM 限流/重试/熔断防护（LLM_GUARD_ENABLED 时启用，默认 None 保持现行为）
        self.llm_guard = get_llm_guard(LLM_GUARD_ENABLED)

        # T3.2: 热门问题预热（配置 SEMANTIC_CACHE_HOT_QUESTIONS 时写入语义缓存，提升客服高频命中率）
        self.warmup_semantic_cache()

    def warmup_semantic_cache(self) -> int:
        """热门问题预热：将 SEMANTIC_CACHE_HOT_QUESTIONS 批量写入语义缓存（幂等）。

        返回写入条数；语义缓存关闭、无配置或热问为空时返回 0。
        """
        if self.semantic_cache is None or not SEMANTIC_CACHE_HOT_QUESTIONS:
            return 0
        embed_query = getattr(self, "embeddings", None)
        if embed_query is None:
            return 0
        return self.semantic_cache.warmup(
            SEMANTIC_CACHE_HOT_QUESTIONS, embed_query.embed_query,
            acl_fp=None, domain=self.cache_domain,
        )

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

    @staticmethod
    def _acl_fingerprint(acl_filter: dict | None) -> str:
        """P1-3: 将 acl_filter 序列化为稳定指纹（md5），None 返回 'none'"""
        if not acl_filter:
            return "none"
        try:
            raw = json.dumps(acl_filter, sort_keys=True, ensure_ascii=False)
            return hashlib.md5(raw.encode("utf-8")).hexdigest()
        except Exception:
            return "unknown"

    def _should_skip_hyde(self, question: str) -> bool:
        """P1-4: 简单事实问题跳过 HyDE（条件化降级，HYDE_SKIP_SIMPLE 开关）

        启发式：问题较短且不含多跳/对比/推理标记时视为简单事实问题，
        跳过 HyDE 多路 LLM 调用以降延迟；模糊用例不跳过以保召回。
        """
        if not self.hyde_skip_simple:
            return False
        q = question.strip()
        if len(q) > 40:
            return False
        multi_hop_markers = (
            "以及", "并且", "以及", "对比", "区别", "为什么", "如何", "哪些",
            "几个", "分别", "流程", "步骤", "总结", "分析", "相比", "介绍",
            "和", "与", "及", "、", "，", "？", "?", "比较", "关系",
        )
        return not any(m in q for m in multi_hop_markers)

    def _compute_confidence(self, sources: list[dict]) -> float:
        """P1-6: 取 Top-1 归一化分数作为置信度（0~1）

        - 启用重排：bge-reranker sigmoid 分数本身 0~1，直接取 Top-1；
        - 未启用重排：RRF 分数量纲极小，用理论最大单路得分(1/(RRF_K+1))归一化，封顶 1.0。
        """
        if not sources:
            return 0.0
        top1 = float(sources[0].get("score", 0.0) or 0.0)
        if self.use_reranker and self._reranker is not None:
            return round(max(0.0, min(1.0, top1)), 4)
        denom = 1.0 / (RRF_K + 1)
        if denom <= 0:
            return 0.0
        return round(max(0.0, min(1.0, top1 / denom)), 4)

    def _build_citation_spans(self, answer: str, cit_index: dict, sources: list[dict]) -> list[dict]:
        """P2-7: 解析答案中的 [cit:N] 引用并做真实性校验，产出结构化 span。

        - 真实检索 ID 集 = sources 中 metadata.doc_id（入库时 enrich_acl_metadata 写入）；
        - 引用编号不在 cit_index（上下文未提供该来源）或对应 doc_id 不在真实 ID 集 → 幻觉引用（valid=False）。
        """
        if not answer:
            return []
        source_ids = {
            str(s.get("metadata", {}).get("doc_id"))
            for s in sources
            if s.get("metadata", {}).get("doc_id")
        }
        cited = parse_citations(answer)
        if not cited:
            return []
        valid = validate_citations(cited, cit_index, source_ids)
        return build_citation_spans(answer, cit_index, valid)

    def _build_chain(self):
        """构建RAG Chain"""
        # Prompt模板
        self.prompt = ChatPromptTemplate.from_messages([
            ("system", """你是一个专业的知识库问答助手。请根据以下上下文信息回答用户的问题。

规则：
1. 只基于提供的上下文回答，不要编造信息
2. 如果上下文中没有相关信息，请明确说明"知识库中未找到相关信息"
3. 回答要准确、简洁、专业
4. 引用标注：如需引用来源，在对应句子后标注编号 [cit:N]（N 为上下文来源编号），严禁编造不存在的编号

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
        P1-4: 各子查询检索路与 HyDE 路经 ThreadPoolExecutor 并行执行（PARALLEL_RETRIEVAL_WORKERS）；
              简单事实问题（_should_skip_hyde）条件化跳过 HyDE 多路 LLM 调用。
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

        def _retrieve_one(q: str) -> list[tuple[object, float]]:
            try:
                return list(self._hybrid_retrieve(q, k, acl_filter=acl_filter))
            except Exception as e:
                logger.warning("子查询检索失败(%s): %s", q[:30], e)
                return []

        def _hyde_retrieve() -> list[tuple[object, float]]:
            out: list[tuple[object, float]] = []
            try:
                hyde_text = self._get_query_understander().generate_hyde(question)
                hyde_emb = self.embeddings.embed_query(hyde_text)
                hyde_raw = self.vectorstore.similarity_search_by_vector_with_relevance_scores(
                    hyde_emb, k=k, filter=acl_filter,
                )
                for rank, (doc, _dist) in enumerate(hyde_raw):
                    out.append((doc, 1.0 / (RRF_K + rank + 1)))
            except Exception as e:
                logger.warning("HyDE检索失败: %s", e)
            return out

        tasks = [lambda q=q: _retrieve_one(q) for q in queries]
        if self.use_hyde and not self._should_skip_hyde(question):
            tasks.append(_hyde_retrieve)

        def _merge(docs: list[tuple[object, float]]) -> None:
            for doc, score in docs:
                key = doc.page_content[:200]
                if key not in pool or score > pool[key][1]:
                    pool[key] = (doc, score)

        if self.parallel_workers > 1 and len(tasks) > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.parallel_workers) as ex:
                futures = [ex.submit(t) for t in tasks]
                for fut in concurrent.futures.as_completed(futures):
                    _merge(fut.result())
        else:
            for t in tasks:
                _merge(t())

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
        # P1-6: 记录本次 Top-1 归一化置信度（_compute_confidence 基于最终排序分数）
        self._last_confidence = self._compute_confidence(sources)
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

    # T3.1 模型分级：按决策选择生成用 LLM
    def _select_llm(self, question: str) -> tuple:
        """按模型分级决策返回 (llm, decision)。

        - 开关关 / 决策为 legacy → 返回默认 self.llm（保持原行为）
        - 决策 small / large → 返回对应模型 LLM（小模型低温度）
        - 缓存优先：决策 tier=cache 时返回 (None, decision)，调用方跳过生成
        """
        from src.core.model_router import ModelRouter

        if self.model_router is None:
            self.model_router = ModelRouter(enabled=self.enable_model_router)
        decision = self.model_router.decide(question)
        if decision.tier == "cache":
            return None, decision
        if not decision.enabled or decision.model is None:
            return self.llm, decision
        factory = self.llm_factory
        if factory is None:
            def factory(model: str, temperature: float, max_tokens: int):
                return ChatOpenAI(
                    model=model, api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL,
                    temperature=temperature, max_tokens=max_tokens,
                )
            self.llm_factory = factory
        return factory(decision.model, decision.temperature, decision.max_tokens), decision

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
            top_k = top_k or self.top_k
            acl_fp = self._acl_fingerprint(acl_filter)

            # P1-3 精确缓存命中（原始 question + top_k + acl_fp，跨权限隔离）
            if self.query_cache is not None:
                cached = self.query_cache.get(question, top_k, acl_fp)
                if cached is not None:
                    logger.info("查询精确缓存命中: %s", mask_text(question[:40]))
                    hit_timing = dict(cached.get("timing") or {})
                    hit_timing["cache_hit"] = True
                    hit_timing["total_ms"] = 1
                    metrics.inc_counter("total_queries", 1)
                    metrics.inc_counter("cache_hits", 1)
                    metrics.record_histogram("latency_ms", 1.0)
                    return LangChainRAGResponse(
                        answer=cached.get("answer", ""),
                        sources=cached.get("sources", []),
                        usage=cached.get("usage", {}),
                        timing=hit_timing,
                        confidence=cached.get("confidence"),
                        trace_id=cached.get("trace_id", ""),
                        citation_spans=cached.get("citation_spans", []),
                    )

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

            # P1-3 语义缓存命中（纠错后 query + acl_fp，余弦相似度召回）
            if self.semantic_cache is not None:
                hit = self.semantic_cache.get(effective_q, acl_fp, self.embeddings.embed_query, domain=self.cache_domain)
                if hit is not None:
                    answer, srcs, sim = hit
                    logger.info("语义缓存命中: %s (sim=%.3f)", mask_text(effective_q[:40]), sim)
                    # P2-7: 缓存命中按 sources 顺序重建编号索引，构建引用 span
                    cit_index = {str(i + 1): s for i, s in enumerate(srcs)}
                    citation_spans = self._build_citation_spans(answer, cit_index, srcs)
                    metrics.inc_counter("total_queries", 1)
                    metrics.inc_counter("cache_hits", 1)
                    metrics.record_histogram("latency_ms", 1.0)
                    return LangChainRAGResponse(
                        answer=answer, sources=srcs, usage={},
                        timing={"cache_hit": True, "semantic": True, "total_ms": 1},
                        confidence=round(float(sim), 4),
                        citation_spans=citation_spans,
                    )

            # 1. 混合检索（大召回 Top-candidate_k；P0-1: 透传 acl_filter 检索前过滤）
            retrieval_start = time.time()
            scored_docs = self._retrieve_multi(effective_q, self.candidate_k, acl_filter=acl_filter)
            timing["retrieval_ms"] = round((time.time() - retrieval_start) * 1000, 2)

            # 2. 构建 sources + 重排精排 → Top-K（内部记录 _last_confidence，P1-6）
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

            confidence = self._last_confidence if self._last_confidence is not None else 0.0

            # 2.7 P1-6 置信度硬门控：低置信且有来源时拒答（默认关，灰度开启）
            refuse = False
            if self.use_confidence_refuse and sources and confidence < self.confidence_refuse_threshold:
                refuse = True
                logger.info("置信度门控触发: confidence=%.4f < %.2f，拒答", confidence, self.confidence_refuse_threshold)

            # 3. 生成回答（用与 sources 一致的文档，绕过 chain 内部重复检索）
            generation_start = time.time()
            if not sources or refuse:
                answer = "知识库中未找到相关信息"
                usage = {}
                citation_spans = []
            else:
                # P2-7: 上下文带 [cit:N] 编号，构建 编号->来源 索引
                context, cit_index = build_context_with_citations(sources)
                # T3.1 模型分级：按决策选择生成 LLM（简单→小模型低温度，复杂→大模型）
                gen_llm, _decision = self._select_llm(question)
                chain_out = (
                    {"context": lambda _: context, "question": RunnablePassthrough()}
                    | self.prompt | (gen_llm or self.llm) | StrOutputParser()
                )
                # T1.5: LLM 调用受限流/重试/熔断保护；限流排队或熔断降级时返回降级文案
                answer = guarded_llm_invoke(self.llm_guard, lambda: chain_out.invoke(question))
                usage = {}  # LangChain不直接暴露token使用量
                # P2-7: 解析引用编号 → 校验真实性（仅真实检索 ID 集合内的来源有效）→ 构建 span
                citation_spans = self._build_citation_spans(answer, cit_index, sources)
                if self.use_citation_verify:
                    cited = [sp["citation_id"] for sp in citation_spans]
                    valid_cnt = sum(1 for sp in citation_spans if sp["valid"])
                    if cited and valid_cnt == 0:
                        logger.warning("引用校验：全部 %d 个引用均为幻觉引用，按拒答处理", len(cited))
                        answer = "知识库中未找到相关信息"
            timing["generation_ms"] = round((time.time() - generation_start) * 1000, 2)

            total_time = (time.time() - start_time) * 1000
            timing["total_ms"] = round(total_time, 2)

            # P1-3 未命中 → 写缓存（key 含 acl_fp；语义缓存仅写有真实答案的条目）
            if self.query_cache is not None:
                self.query_cache.set(question, top_k, {
                    "answer": answer, "sources": sources, "usage": usage,
                    "timing": timing, "confidence": confidence, "trace_id": "",
                    "citation_spans": citation_spans,
                }, acl_fp)
            if self.semantic_cache is not None and answer and answer != "知识库中未找到相关信息":
                self.semantic_cache.set(effective_q, answer, sources, acl_fp, self.embeddings.embed_query, domain=self.cache_domain)

            # P2-8: 采集查询总量/延迟/token与成本（token 用字符/4 估算，成本按 config 单价）
            metrics.inc_counter("total_queries", 1)
            metrics.record_histogram("latency_ms", total_time)
            metrics.record_llm_usage(
                latency_ms=float(timing.get("generation_ms", 0) or 0),
                prompt_tokens=len(context) // 4 if "context" in dir() else 0,
                completion_tokens=len(answer) // 4,
            )

            return LangChainRAGResponse(
                answer=answer,
                sources=sources,
                usage=usage,
                timing=timing,
                confidence=confidence,
                citation_spans=citation_spans,
            )

        except Exception as e:
            logger.error("LangChain RAG查询失败: %s", e)
            total_time = (time.time() - start_time) * 1000
            metrics.inc_counter("total_queries", 1)
            metrics.inc_counter("total_errors", 1)
            metrics.record_histogram("latency_ms", total_time)
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
            top_k = top_k or self.top_k
            acl_fp = self._acl_fingerprint(acl_filter)

            # P1-3 精确缓存命中（原始 question + top_k + acl_fp，跨权限隔离）
            if self.query_cache is not None:
                cached = self.query_cache.get(question, top_k, acl_fp)
                if cached is not None:
                    hit_timing = dict(cached.get("timing") or {})
                    hit_timing["cache_hit"] = True
                    hit_timing["total_ms"] = 1
                    trace.start_span("cache", {"kind": "exact"})
                    trace.end_span({"hit": True})
                    metrics.inc_counter("total_queries", 1)
                    metrics.inc_counter("cache_hits", 1)
                    metrics.record_histogram("latency_ms", 1.0)
                    yield cached.get("answer", ""), True, cached.get("sources", []), hit_timing
                    return

            # 0. 查询纠错
            effective_q = question
            if self.use_query_correction:
                try:
                    corrected = self._get_query_understander().correct_query(question)
                    if corrected and corrected.strip() and corrected.strip() != question:
                        effective_q = corrected.strip()
                except Exception:
                    pass

            # P1-3 语义缓存命中（纠错后 query + acl_fp，余弦相似度召回）
            if self.semantic_cache is not None:
                hit = self.semantic_cache.get(effective_q, acl_fp, self.embeddings.embed_query, domain=self.cache_domain)
                if hit is not None:
                    answer, srcs, sim = hit
                    logger.info("语义缓存命中(stream): %s (sim=%.3f)", mask_text(effective_q[:40]), sim)
                    trace.start_span("cache", {"kind": "semantic"})
                    trace.end_span({"hit": True, "sim": round(float(sim), 4)})
                    metrics.inc_counter("total_queries", 1)
                    metrics.inc_counter("cache_hits", 1)
                    metrics.record_histogram("latency_ms", 1.0)
                    yield answer, True, srcs, {"cache_hit": True, "semantic": True, "total_ms": 1}
                    return

            # 1. 混合检索
            trace.start_span("retrieval")
            retrieval_start = time.time()
            top_k = top_k or self.top_k
            scored_docs = self._retrieve_multi(effective_q, self.candidate_k, acl_filter=acl_filter)
            timing["retrieval_ms"] = round((time.time() - retrieval_start) * 1000, 2)
            trace.end_span({"docs_count": len(scored_docs)})

            # 2. 构建sources + 重排
            trace.start_span("rerank")
            sources = self._build_sources(effective_q, scored_docs, top_k)
            timing["rerank_ms"] = round(getattr(self, "_last_rerank_ms", 0), 2)
            trace.end_span({"sources_count": len(sources), "rerank_ms": timing["rerank_ms"]})

            # 2.5 父子切片回取：child → parent（P1-2）
            sources = self._resolve_parent(sources)

            # 2.6 P0-1 运行时归属断言（防检索前过滤被绕过）
            if self.acl_enforce and acl_filter:
                allowed_ids = allowed_doc_ids_from_filter(acl_filter)
                if allowed_ids is not None:
                    sources, removed = assert_sources_allowed(sources, allowed_ids)
                    if removed:
                        logger.warning("ACL运行时断言：剔除 %d 条越权来源", removed)

            confidence = self._last_confidence if self._last_confidence is not None else 0.0

            # 2.7 P1-6 置信度硬门控（默认关，灰度开启）
            refuse = False
            if self.use_confidence_refuse and sources and confidence < self.confidence_refuse_threshold:
                refuse = True
                logger.info("置信度门控触发(stream): confidence=%.4f < %.2f，拒答", confidence, self.confidence_refuse_threshold)

            # 3. 流式生成（注入 context，避免 chain 内部重复检索）
            trace.start_span("generation")
            generation_start = time.time()
            if not sources or refuse:
                metrics.inc_counter("total_queries", 1)
                metrics.record_histogram("latency_ms", (time.time() - start_time) * 1000)
                metrics.record_llm_usage(latency_ms=0.0, prompt_tokens=0, completion_tokens=0)
                yield "知识库中未找到相关信息", True, sources, timing
                trace.end_span({"status": "refused" if refuse else "no_sources"})
                return

            # P2-7: 上下文带 [cit:N] 编号，构建 编号->来源 索引
            context, cit_index = build_context_with_citations(sources)
            # T3.1 模型分级：按决策选择生成 LLM（简单→小模型低温度，复杂→大模型）
            gen_llm, _decision = self._select_llm(question)
            chain_out = (
                {"context": lambda _: context, "question": RunnablePassthrough()}
                | self.prompt | (gen_llm or self.llm) | StrOutputParser()
            )

            full_answer = ""
            for chunk in chain_out.stream(question):
                if chunk:
                    full_answer += chunk
                    yield chunk, False, sources, timing

            timing["generation_ms"] = round((time.time() - generation_start) * 1000, 2)
            timing["total_ms"] = round((time.time() - start_time) * 1000, 2)
            # P2-7: 引用校验与 span（随最后一个 token 的 timing 透出）
            citation_spans = self._build_citation_spans(full_answer, cit_index, sources)
            timing["citation_spans"] = citation_spans
            if self.use_citation_verify:
                cited = [sp["citation_id"] for sp in citation_spans]
                valid_cnt = sum(1 for sp in citation_spans if sp["valid"])
                if cited and valid_cnt == 0:
                    logger.warning("引用校验(stream)：全部 %d 个引用均为幻觉引用，按拒答处理", len(cited))
                    timing["refused_reason"] = "hallucinated_citations"
            trace.end_span({"answer_length": len(full_answer), "citations": len(citation_spans)})

            # P1-3 未命中 → 写缓存（key 含 acl_fp；语义缓存仅写有真实答案的条目）
            if self.query_cache is not None:
                self.query_cache.set(question, top_k, {
                    "answer": full_answer, "sources": sources, "usage": {},
                    "timing": timing, "confidence": confidence, "trace_id": "",
                    "citation_spans": citation_spans,
                }, acl_fp)
            if self.semantic_cache is not None and full_answer and full_answer != "知识库中未找到相关信息":
                self.semantic_cache.set(effective_q, full_answer, sources, acl_fp, self.embeddings.embed_query, domain=self.cache_domain)

            # P2-8: 流式链路采集总量/延迟/token与成本
            metrics.inc_counter("total_queries", 1)
            total_ms = float(timing.get("total_ms", 0) or 0)
            metrics.record_histogram("latency_ms", total_ms)
            metrics.record_llm_usage(
                latency_ms=float(timing.get("generation_ms", 0) or 0),
                prompt_tokens=len(context) // 4 if "context" in dir() else 0,
                completion_tokens=len(full_answer) // 4,
            )

            yield "", True, sources, timing

        except Exception as e:
            logger.error("LangChain RAG流式查询失败: %s", e)
            trace.status = "error"
            metrics.inc_counter("total_queries", 1)
            metrics.inc_counter("total_errors", 1)
            metrics.record_histogram("latency_ms", (time.time() - start_time) * 1000)
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
