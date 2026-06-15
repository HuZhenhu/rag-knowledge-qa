"""LangChain版RAG引擎 — 使用LangChain组件重构RAG链路"""
import logging
import time
from dataclasses import dataclass, field

from langchain_openai import ChatOpenAI
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_chroma import Chroma
from langchain_community.retrievers import BM25Retriever
from langchain_classic.retrievers import EnsembleRetriever
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
    USE_HYBRID_RETRIEVAL,
    RELEVANCE_THRESHOLD,
    CHUNK_SIZE,
    CHUNK_OVERLAP,
)
from src.core.tracer import Trace

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
    """基于LangChain的RAG引擎"""

    def __init__(
        self,
        use_hybrid: bool = USE_HYBRID_RETRIEVAL,
        top_k: int = RETRIEVAL_TOP_K,
    ):
        self.top_k = top_k
        self.use_hybrid = use_hybrid

        # 初始化LLM
        self.llm = ChatOpenAI(
            model=DEEPSEEK_MODEL,
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
            temperature=0.3,
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
        self._ensemble_retriever = None

        # 如果启用混合检索，构建EnsembleRetriever
        if self.use_hybrid:
            self._setup_hybrid_retriever()

        # 构建RAG Chain
        self._build_chain()

    def _setup_hybrid_retriever(self):
        """设置混合检索器（向量 + BM25）"""
        try:
            # 从Chroma获取所有文档用于构建BM25索引
            all_docs = self.vectorstore.get()
            if all_docs and all_docs.get("documents"):
                texts = all_docs["documents"]
                self._bm25_retriever = BM25Retriever.from_texts(
                    texts,
                    k=self.top_k,
                )
                # Ensemble: 向量检索权重0.6，BM25权重0.4
                self._ensemble_retriever = EnsembleRetriever(
                    retrievers=[self.vector_retriever, self._bm25_retriever],
                    weights=[0.6, 0.4],
                )
                logger.info("混合检索器初始化完成，共 %d 个文档", len(texts))
            else:
                logger.info("向量库为空，使用纯向量检索")
                self.use_hybrid = False
        except Exception as e:
            logger.warning("混合检索器初始化失败: %s，回退到纯向量检索", e)
            self.use_hybrid = False

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

        # 获取当前使用的检索器
        retriever = self._ensemble_retriever if self.use_hybrid and self._ensemble_retriever else self.vector_retriever

        # 构建RAG Chain
        def format_docs(docs):
            formatted = []
            for i, doc in enumerate(docs):
                source = doc.metadata.get("source", "未知来源")
                formatted.append(f"[来源{i+1}: {source}]\n{doc.page_content}")
            return "\n\n".join(formatted)

        self.chain = (
            {"context": retriever | format_docs, "question": RunnablePassthrough()}
            | self.prompt
            | self.llm
            | StrOutputParser()
        )

        # 保存检索器引用用于获取来源
        self._retriever = retriever

    def query(
        self,
        question: str,
        top_k: int | None = None,
        history: list[dict] | None = None,
        summary: str = "",
        user_id: str = "",
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
            # 1. 检索
            retrieval_start = time.time()
            retriever = self._ensemble_retriever if self.use_hybrid and self._ensemble_retriever else self.vector_retriever

            # 获取相关文档
            docs = retriever.invoke(question)
            timing["retrieval_ms"] = round((time.time() - retrieval_start) * 1000, 2)

            # 2. 构建sources
            sources = []
            for i, doc in enumerate(docs):
                sources.append({
                    "content": doc.page_content,
                    "metadata": doc.metadata,
                    "score": 1.0 - (i * 0.1),  # 简单评分：按排名递减
                })

            # 3. 生成回答
            generation_start = time.time()
            if not sources:
                answer = "知识库中未找到相关信息"
                usage = {}
            else:
                answer = self.chain.invoke(question)
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
                     user_id: str = ""):
        """流式RAG问答

        Yields:
            tuple: (token_str, is_last, sources, timing)
        """
        trace = Trace(question, user_id=user_id)
        start_time = time.time()
        timing = {}

        try:
            # 1. 检索
            trace.start_span("retrieval")
            retrieval_start = time.time()
            retriever = self._ensemble_retriever if self.use_hybrid and self._ensemble_retriever else self.vector_retriever
            docs = retriever.invoke(question)
            timing["retrieval_ms"] = round((time.time() - retrieval_start) * 1000, 2)
            trace.end_span({"docs_count": len(docs)})

            # 2. 构建sources
            sources = []
            for i, doc in enumerate(docs):
                sources.append({
                    "content": doc.page_content,
                    "metadata": doc.metadata,
                    "score": 1.0 - (i * 0.1),
                })

            # 3. 流式生成
            trace.start_span("generation")
            generation_start = time.time()
            if not sources:
                yield "知识库中未找到相关信息", True, sources, timing
                trace.end_span({"status": "no_sources"})
                return

            full_answer = ""
            for chunk in self.chain.stream(question):
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
            self._setup_hybrid_retriever()
            self._build_chain()

    def get_retriever(self):
        """获取当前使用的检索器"""
        return self._ensemble_retriever if self.use_hybrid and self._ensemble_retriever else self.vector_retriever

    def query_by_source(self, source_filename: str) -> list[dict]:
        """按源文件查询chunks（兼容原版VectorStore接口）

        Args:
            source_filename: 源文件名

        Returns:
            list[dict]: chunk列表，每个包含content, metadata等
        """
        results = self.vectorstore.get(
            where={"source_file": source_filename} if source_filename else None,
        )
        chunks = []
        if results and results.get("documents"):
            for i, doc in enumerate(results["documents"]):
                meta = results["metadatas"][i] if results.get("metadatas") else {}
                chunks.append({
                    "content": doc,
                    "metadata": meta,
                })
        return chunks
