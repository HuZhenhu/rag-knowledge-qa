"""Pydantic请求/响应模型"""
from pydantic import BaseModel, Field
from typing import Optional


class Source(BaseModel):
    """引用来源"""
    file: str = Field(..., description="来源文件名")
    section: str = Field("", description="所属章节")
    content_type: str = Field("text", description="内容类型")
    chunk: str = Field(..., description="原文片段")
    score: float = Field(..., description="相关度分数")


class QueryResponse(BaseModel):
    """问答响应"""
    request_id: str = Field(..., description="请求ID")
    answer: str = Field(..., description="回答内容")
    sources: list[Source] = Field(default_factory=list, description="引用来源")
    usage: dict = Field(default_factory=dict, description="Token用量")
    timing: dict = Field(default_factory=dict, description="耗时统计")
    confidence: float | None = Field(None, description="置信度分数（P1-6，启用时输出）")
    citation_spans: list[dict] = Field(default_factory=list,
                                       description="引用 span 列表（P2-7：含起止位置/来源定位/有效性标记）")
    trace_id: str | None = Field(None, description="链路追踪 ID（P2-8：关联 trace 记录）")


class HealthResponse(BaseModel):
    """健康检查响应"""
    status: str = Field("ok", description="服务状态")
    version: str = Field("1.0.0", description="版本号")
    vector_store: dict = Field(default_factory=dict, description="向量库健康状态")


class StatsResponse(BaseModel):
    """统计信息响应"""
    total_documents: int = Field(0, description="文档数")
    total_chunks: int = Field(0, description="知识片段数")


class DocumentInfo(BaseModel):
    """文档信息"""
    id: str = Field(..., description="文档ID")
    filename: str = Field(..., description="文件名")
    chunks: int = Field(0, description="chunk数")
    indexed_at: str = Field("", description="索引时间")


class DocumentListResponse(BaseModel):
    """文档列表响应"""
    documents: list[DocumentInfo] = Field(default_factory=list)
    total: int = Field(0, description="总数")


class UploadResponse(BaseModel):
    """上传响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field("", description="消息")
    document_id: Optional[str] = Field(None, description="文档ID")


# ---- 认证相关 ----

class RegisterRequest(BaseModel):
    """注册请求"""
    username: str = Field(..., min_length=3, max_length=32, description="用户名")
    password: str = Field(..., min_length=6, max_length=128, description="密码")


class LoginRequest(BaseModel):
    """登录请求"""
    username: str = Field(..., description="用户名")
    password: str = Field(..., description="密码")


class UserInfo(BaseModel):
    """用户基本信息"""
    id: str = Field(..., description="用户ID")
    username: str = Field(..., description="用户名")
    role: str = Field(..., description="角色")


class TokenResponse(BaseModel):
    """JWT Token响应"""
    access_token: str = Field(..., description="访问Token")
    refresh_token: str = Field(..., description="刷新Token")
    token_type: str = Field("bearer", description="Token类型")
    expires_in: int = Field(..., description="过期时间（秒）")
    user: Optional[UserInfo] = Field(None, description="用户信息")


class RefreshRequest(BaseModel):
    """刷新Token请求"""
    refresh_token: str = Field(..., description="刷新Token")


# ---- 知识库相关 ----

class KnowledgeBaseCreateRequest(BaseModel):
    """创建知识库请求"""
    name: str = Field(..., min_length=1, max_length=100, description="知识库名称")
    description: str = Field("", description="知识库描述")


class KnowledgeBaseResponse(BaseModel):
    """知识库响应"""
    id: str = Field(..., description="知识库ID")
    name: str = Field(..., description="知识库名称")
    description: str = Field("", description="描述")
    owner_id: str = Field(..., description="创建者ID")
    created_at: str = Field(..., description="创建时间")
    document_count: int = Field(0, description="文档数")
    chunk_count: int = Field(0, description="chunk数")


# ---- 扩展 QueryRequest ----

class QueryRequest(BaseModel):
    """问答请求"""
    question: str = Field(..., description="用户问题")
    session_id: Optional[str] = Field(None, description="会话ID（多轮对话用）")
    top_k: Optional[int] = Field(5, description="返回前几条检索结果")
    use_hyde: Optional[bool] = Field(False, description="是否启用HyDE")
    kb_id: Optional[str] = Field(None, description="知识库ID（不指定则用默认）")


# ---- 反馈相关 ----

class FeedbackRequest(BaseModel):
    """用户反馈请求"""
    request_id: str = Field(..., description="关联的请求ID（trace_id）")
    rating: int = Field(..., description="评分: 1=赞, -1=踩")
    query: Optional[str] = Field(None, description="原始查询文本")


# ---- 文档 Chunk 相关 ----

class ChunkInfo(BaseModel):
    """文档 Chunk 信息"""
    chunk_id: str = Field(..., description="Chunk ID")
    content: str = Field(..., description="Chunk 内容")
    section: str = Field("", description="所属章节")
    page_number: Optional[int] = Field(None, description="所在页码")
    content_type: str = Field("text", description="内容类型")


class ChunkListResponse(BaseModel):
    """文档 Chunk 列表响应"""
    document_id: str = Field(..., description="文档ID")
    filename: str = Field(..., description="文件名")
    chunks: list[ChunkInfo] = Field(default_factory=list, description="Chunk列表")
    total: int = Field(0, description="Chunk总数")
