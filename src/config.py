"""配置管理"""
import os
from pathlib import Path
from dotenv import load_dotenv

# 加载.env文件
load_dotenv()

# 项目根目录
BASE_DIR = Path(__file__).parent.parent

# 知识库目录
DATA_DIR = BASE_DIR / "data"
CHROMA_DB_DIR = BASE_DIR / "chroma_db"
IMAGES_DIR = BASE_DIR / "images"

# LLM提供商配置
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "deepseek")  # deepseek/openai/anthropic

# OpenAI兼容API配置（DeepSeek/通义千问/Moonshot/本地Ollama等）
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", os.getenv("DEEPSEEK_API_KEY", ""))
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
OPENAI_MODEL = os.getenv("OPENAI_MODEL", os.getenv("DEEPSEEK_MODEL", "deepseek-chat"))

# Anthropic Claude配置
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")

# 兼容旧配置（deprecated，优先用上面的）
DEEPSEEK_API_KEY = OPENAI_API_KEY
DEEPSEEK_BASE_URL = OPENAI_BASE_URL
DEEPSEEK_MODEL = OPENAI_MODEL

# Embedding配置
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "local")  # local 或 api
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "models/BAAI/bge-m3")

# RAG引擎选择：langchain / original / agentic
RAG_ENGINE = os.getenv("RAG_ENGINE", "langchain")

# ===== Agentic RAG 配置（RAG_ENGINE=agentic 时生效）=====
AGENT_MAX_RETRY = int(os.getenv("AGENT_MAX_RETRY", "3"))  # Critic 反思重试上限
AGENT_TIMEOUT = int(os.getenv("AGENT_TIMEOUT", "60"))  # 全局执行超时（秒）
AGENT_WEB_SEARCH = os.getenv("AGENT_WEB_SEARCH", "false").lower() == "true"  # 是否启用联网搜索
AGENT_WEB_SEARCH_PROVIDER = os.getenv("AGENT_WEB_SEARCH_PROVIDER", "bocha")  # 联网服务商（bocha/tavily）
AGENT_WEB_SEARCH_API_KEY = os.getenv("AGENT_WEB_SEARCH_API_KEY", "")  # 联网服务 API Key
AGENT_MODEL = os.getenv("AGENT_MODEL", OPENAI_MODEL)  # Agent 节点使用的大模型（默认与 RAG 一致）
AGENT_RETRIEVAL_TOP_K = int(os.getenv("AGENT_RETRIEVAL_TOP_K", "5"))  # Agent 单次检索 top_k

# 切片配置
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "800"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "100"))

# 父子切片配置（P1-2，默认关闭；开启后需重建向量库索引）
USE_PARENT_CHILD = os.getenv("USE_PARENT_CHILD", "false").lower() == "true"
CHILD_TOKEN_SIZE = int(os.getenv("CHILD_TOKEN_SIZE", "150"))   # child 检索单元：100~200 tokens
PARENT_TOKEN_SIZE = int(os.getenv("PARENT_TOKEN_SIZE", "700"))  # parent 生成单元：600~800 tokens

# 检索配置
RETRIEVAL_TOP_K = int(os.getenv("RETRIEVAL_TOP_K", "10"))
RETRIEVAL_CANDIDATE_K = int(os.getenv("RETRIEVAL_CANDIDATE_K", "30"))  # 混合检索大召回候选数（Top-30 再精排）
RRF_K = int(os.getenv("RRF_K", "60"))  # RRF参数
USE_HYBRID_RETRIEVAL = os.getenv("USE_HYBRID_RETRIEVAL", "true").lower() == "true"
USE_QUERY_EXPANSION = os.getenv("USE_QUERY_EXPANSION", "false").lower() == "true"
USE_HYDE = os.getenv("USE_HYDE", "false").lower() == "true"
USE_RERANKER = os.getenv("USE_RERANKER", "false").lower() == "true"
USE_QUERY_CORRECTION = os.getenv("USE_QUERY_CORRECTION", "true").lower() == "true"  # 查询纠错

# M5: 检索优化配置
HYBRID_VECTOR_WEIGHT = float(os.getenv("HYBRID_VECTOR_WEIGHT", "1.0"))
HYBRID_BM25_WEIGHT = float(os.getenv("HYBRID_BM25_WEIGHT", "1.0"))
RELEVANCE_THRESHOLD = float(os.getenv("RELEVANCE_THRESHOLD", "0.01"))
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-base")

# LLM配置
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.7"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "2000"))

# API服务配置
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8080"))

# 多轮对话配置
MAX_HISTORY_ROUNDS = int(os.getenv("MAX_HISTORY_ROUNDS", "5"))
SESSION_TIMEOUT_MINUTES = int(os.getenv("SESSION_TIMEOUT_MINUTES", "30"))

# 安全配置
MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "5"))
ALLOWED_FILE_TYPES = os.getenv("ALLOWED_FILE_TYPES", ".md,.txt,.docx,.pdf,.xlsx,.png,.jpg").split(",")

# 限流配置
RATE_LIMIT_DAILY = int(os.getenv("RATE_LIMIT_DAILY", "100"))
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "10"))
RATE_LIMIT_DB = os.getenv("RATE_LIMIT_DB", str(BASE_DIR / "rate_limit.db"))

# JWT认证配置
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "rag-knowledge-qa-dev-secret-key-change-in-production")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))  # 24小时
JWT_REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("JWT_REFRESH_TOKEN_EXPIRE_DAYS", "30"))

# 注册开关（admin可关闭开放注册）
ALLOW_REGISTRATION = os.getenv("ALLOW_REGISTRATION", "true").lower() == "true"

# M6: 大规模数据支撑配置
VECTOR_STORE_BACKEND = os.getenv("VECTOR_STORE_BACKEND", "chroma")  # chroma / milvus / faiss
BATCH_EMBEDDING_SIZE = int(os.getenv("BATCH_EMBEDDING_SIZE", "100"))  # 批量Embedding大小
PARALLEL_LOAD_WORKERS = int(os.getenv("PARALLEL_LOAD_WORKERS", "4"))  # 并行加载线程数
DEDUP_SIMILARITY_THRESHOLD = float(os.getenv("DEDUP_SIMILARITY_THRESHOLD", "0.95"))  # 相似度去重阈值

# 文件监听器配置
WATCHER_AUTO_START = os.getenv("WATCHER_AUTO_START", "true").lower() == "true"  # 服务启动时自动启动监听
WATCHER_DEBOUNCE_SECONDS = float(os.getenv("WATCHER_DEBOUNCE_SECONDS", "5.0"))  # 文件事件防抖窗口（亚分钟级事件驱动同步）

# M7: 多模态能力配置（默认关闭）
MULTIMODAL_ENABLED = os.getenv("MULTIMODAL_ENABLED", "false").lower() == "true"
IMAGE_LLM_DESCRIPTION = os.getenv("IMAGE_LLM_DESCRIPTION", "false").lower() == "true"  # 用LLM生成图片描述
TABLE_NL_DESCRIPTION = os.getenv("TABLE_NL_DESCRIPTION", "false").lower() == "true"  # 表格生成自然语言描述
CHART_ANALYSIS_ENABLED = os.getenv("CHART_ANALYSIS_ENABLED", "false").lower() == "true"  # 图表分析
MULTIMODAL_EMBEDDING = os.getenv("MULTIMODAL_EMBEDDING", "false").lower() == "true"  # CLIP多模态Embedding
OCR_LANGUAGES = os.getenv("OCR_LANGUAGES", "chi_sim+eng")  # OCR支持语言
TESSERACT_CMD = os.getenv("TESSERACT_CMD", "tesseract")  # Tesseract可执行文件路径

# M8: 对话能力增强配置
USE_CONVERSATION_SUMMARY = os.getenv("USE_CONVERSATION_SUMMARY", "false").lower() == "true"
SUMMARY_THRESHOLD_ROUNDS = int(os.getenv("SUMMARY_THRESHOLD_ROUNDS", "5"))  # 超过多少轮触发摘要
SUMMARY_KEEP_RECENT_ROUNDS = int(os.getenv("SUMMARY_KEEP_RECENT_ROUNDS", "3"))  # 保留最近几轮完整对话
FOLLOWUP_SCORE_THRESHOLD = float(os.getenv("FOLLOWUP_SCORE_THRESHOLD", "0.3"))  # 追问分数阈值
USE_INTENT_CLASSIFICATION = os.getenv("USE_INTENT_CLASSIFICATION", "false").lower() == "true"

# M9: 评测配置
EVAL_TEST_CASES_PATH = os.getenv("EVAL_TEST_CASES_PATH", "evaluation/test_cases.json")
EVAL_RESULTS_DIR = os.getenv("EVAL_RESULTS_DIR", "evaluation")
EVAL_SIMILARITY_THRESHOLD = float(os.getenv("EVAL_SIMILARITY_THRESHOLD", "0.6"))
EVAL_ALERT_DROP_THRESHOLD = float(os.getenv("EVAL_ALERT_DROP_THRESHOLD", "0.05"))  # 准确率下降5%告警
EVAL_SCHEDULE_HOUR = int(os.getenv("EVAL_SCHEDULE_HOUR", "2"))  # 每天凌晨2点
EVAL_SCHEDULE_MINUTE = int(os.getenv("EVAL_SCHEDULE_MINUTE", "0"))

# M4: 监控告警配置
ALERT_ERROR_RATE_THRESHOLD = float(os.getenv("ALERT_ERROR_RATE_THRESHOLD", "0.05"))  # 5%
ALERT_LATENCY_THRESHOLD_MS = int(os.getenv("ALERT_LATENCY_THRESHOLD_MS", "3000"))  # 3000ms
ALERT_CHECK_WINDOW_SECONDS = int(os.getenv("ALERT_CHECK_WINDOW_SECONDS", "60"))  # 1分钟
ALERT_LATENCY_WINDOW_SECONDS = int(os.getenv("ALERT_LATENCY_WINDOW_SECONDS", "300"))  # 5分钟
# ===== FAISS 后端配置（VECTOR_STORE_BACKEND=faiss 时生效）=====
FAISS_INDEX_DIR = Path(os.getenv("FAISS_INDEX_DIR", str(BASE_DIR / "faiss_index")))
FAISS_INDEX_TYPE = os.getenv("FAISS_INDEX_TYPE", "hnsw-sq8")  # hnsw-sq8（SQ8量化）/ flat（精确）
FAISS_HNSW_M = int(os.getenv("FAISS_HNSW_M", "16"))
FAISS_HNSW_EF_CONSTRUCTION = int(os.getenv("FAISS_HNSW_EF_CONSTRUCTION", "100"))
FAISS_HNSW_EF_SEARCH = int(os.getenv("FAISS_HNSW_EF_SEARCH", "64"))

# ===== Milvus 后端配置（VECTOR_STORE_BACKEND=milvus 时生效）=====
# 本地文件用 milvus-lite：MILVUS_URI=./milvus_lite.db；远程：MILVUS_URI=http://localhost:19530
MILVUS_URI = os.getenv("MILVUS_URI", str(BASE_DIR / "milvus_lite.db"))
MILVUS_INDEX_TYPE = os.getenv("MILVUS_INDEX_TYPE", "HNSW")  # HNSW / IVF_FLAT
MILVUS_METRIC_TYPE = os.getenv("MILVUS_METRIC_TYPE", "COSINE")
MILVUS_INDEX_NLIST = int(os.getenv("MILVUS_INDEX_NLIST", "128"))
MILVUS_INDEX_NPROBE = int(os.getenv("MILVUS_INDEX_NPROBE", "10"))
MILVUS_QUANTIZER = os.getenv("MILVUS_QUANTIZER", "")  # 空 / SCALAR（SQ8风格）/ QUANTIZE_BIT（2.4+）

# ===== P0 安全合规配置 =====
# P0-1 检索链路 ACL/租户隔离（默认关，灰度开启）
# 开启后需重建索引（chunk 元数据需携带 doc_id/allowed_roles 等 ACL 字段），
# 并在数据库 document_permissions 中配置各用户可读文档，否则无权限记录的用户检索结果为空。
ACL_ENFORCE = os.getenv("ACL_ENFORCE", "false").lower() == "true"
ACL_ADMIN_ROLES = tuple(
    r.strip() for r in os.getenv("ACL_ADMIN_ROLES", "admin").split(",") if r.strip()
)  # 命中这些角色的用户免 ACL 过滤（默认 admin）

# P0-2 PII 脱敏（默认关，避免过度脱敏影响检索语义）
# 开启后新入库文档在写入向量库前对文本脱敏，日志输出做掩码；存量数据需重建索引才生效。
USE_PII_REDACTION = os.getenv("USE_PII_REDACTION", "false").lower() == "true"
PII_REDACT_MODE = os.getenv("PII_REDACT_MODE", "mask")  # mask 掩码 / remove 删除 / replace 占位
PII_PLACEHOLDER = os.getenv("PII_PLACEHOLDER", "[PII]")

# ===== P1 性能与可靠性配置 =====
# P1-3 缓存（默认开；进程内精确缓存 + SQLite 语义缓存，key 均含 ACL 指纹防跨权限泄露）
QUERY_CACHE_ENABLED = os.getenv("QUERY_CACHE_ENABLED", "true").lower() == "true"
SEMANTIC_CACHE_ENABLED = os.getenv("SEMANTIC_CACHE_ENABLED", "true").lower() == "true"
SEMANTIC_CACHE_THRESHOLD = float(os.getenv("SEMANTIC_CACHE_THRESHOLD", "0.92"))  # 语义缓存余弦命中阈值

# P1-4 延迟收敛
PARALLEL_RETRIEVAL_WORKERS = int(os.getenv("PARALLEL_RETRIEVAL_WORKERS", "3"))  # 多路检索并行线程数
HYDE_SKIP_SIMPLE = os.getenv("HYDE_SKIP_SIMPLE", "true").lower() == "true"  # 简单事实问题跳过 HyDE（条件化降级）

# P1-6 置信度门控/拒答硬化（默认关，灰度开启）
ENABLE_CONFIDENCE_REFUSE = os.getenv("ENABLE_CONFIDENCE_REFUSE", "false").lower() == "true"
CONFIDENCE_REFUSE_THRESHOLD = float(os.getenv("CONFIDENCE_REFUSE_THRESHOLD", "0.35"))

# ===== P2 企业级增强配置 =====
# P2-7 引用真实性校验（默认关；开启时若模型答案中所有引用均为幻觉引用则拒答）
USE_CITATION_VERIFY = os.getenv("USE_CITATION_VERIFY", "false").lower() == "true"

# P2-8 可观测性：延迟告警阈值（秒），响应超过该值记录 alert（默认 3s，对齐企业级 P95<3s 目标）
METRICS_LATENCY_ALERT_SECONDS = float(os.getenv("METRICS_LATENCY_ALERT_SECONDS", "3.0"))
METRICS_COST_USD_PER_1K = float(os.getenv("METRICS_COST_USD_PER_1K", "0.5"))  # 生成 token 估算单价（USD / 1K tokens，用于成本监控近似）


