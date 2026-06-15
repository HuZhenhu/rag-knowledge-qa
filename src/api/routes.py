"""API路由 — JWT认证 + 多知识库 + 审计日志 + M4监控"""
import uuid
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile, File
from fastapi.responses import PlainTextResponse

from src.api.schemas import (
    QueryRequest, QueryResponse, Source,
    HealthResponse, StatsResponse,
    DocumentListResponse, DocumentInfo,
    UploadResponse,
    RegisterRequest, LoginRequest, TokenResponse, RefreshRequest,
    KnowledgeBaseCreateRequest, KnowledgeBaseResponse,
    FeedbackRequest,
    ChunkInfo, ChunkListResponse,
)
from src.api.jwt_auth import (
    get_current_user, require_role, log_audit,
    hash_password, verify_password,
    create_access_token, create_refresh_token, decode_token,
    _get_client_ip,
)
from src.storage.database import (
    create_user, get_user_by_username, update_user_login,
    create_knowledge_base, get_knowledge_base, list_knowledge_bases,
)
from src.config import USE_QUERY_EXPANSION, USE_HYDE, USE_RERANKER, ALLOW_REGISTRATION, USE_CONVERSATION_SUMMARY, RAG_ENGINE
from src.core.session import SessionManager
from src.core.metrics import metrics
from src.core.tracer import get_trace, list_recent_traces
from src.core.alert_manager import alert_manager

router = APIRouter(prefix="/api/v1")

# 初始化组件（根据配置选择引擎）
if RAG_ENGINE == "langchain":
    from src.core.langchain_rag import LangChainRAGEngine
    rag_engine = LangChainRAGEngine()
    vector_store = rag_engine.vectorstore  # LangChain Chroma实例
else:
    from src.core.rag_engine import RAGEngine
    from src.core.vector_store import VectorStore
    rag_engine = RAGEngine(
        use_query_expansion=USE_QUERY_EXPANSION,
        use_hyde=USE_HYDE,
        use_reranker=USE_RERANKER,
    )
    vector_store = VectorStore()

session_manager = SessionManager()


# ===================================================================
# 认证接口
# ===================================================================

@router.post("/auth/register", response_model=TokenResponse)
async def register(req: RegisterRequest, request: Request):
    """注册新用户"""
    if not ALLOW_REGISTRATION:
        raise HTTPException(status_code=403, detail="注册已关闭，请联系管理员")

    user_id = uuid.uuid4().hex
    password_hash = hash_password(req.password)
    user = create_user(user_id, req.username, password_hash)

    if user is None:
        raise HTTPException(status_code=409, detail="用户名已存在")

    access_token = create_access_token(user["id"], user["role"])
    refresh_token = create_refresh_token(user["id"])

    from src.config import JWT_ACCESS_TOKEN_EXPIRE_MINUTES
    log_audit(user_id, "register", "user", user_id,
              ip_address=_get_client_ip(request))

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/auth/login", response_model=TokenResponse)
async def login(req: LoginRequest, request: Request):
    """登录获取JWT Token"""
    user = get_user_by_username(req.username)
    if user is None or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not user.get("is_active", True):
        raise HTTPException(status_code=403, detail="用户已被禁用")

    update_user_login(user["id"])
    access_token = create_access_token(user["id"], user["role"])
    refresh_token = create_refresh_token(user["id"])

    from src.config import JWT_ACCESS_TOKEN_EXPIRE_MINUTES
    log_audit(user["id"], "login", "user", user["id"],
              ip_address=_get_client_ip(request))

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user={"id": user["id"], "username": user["username"], "role": user["role"]},
    )


@router.post("/auth/refresh", response_model=TokenResponse)
async def refresh_token(req: RefreshRequest):
    """刷新Token"""
    payload = decode_token(req.refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="无效的刷新Token")

    user_id = payload["sub"]
    from src.storage.database import get_user_by_id
    user = get_user_by_id(user_id)
    if user is None or not user.get("is_active", True):
        raise HTTPException(status_code=401, detail="用户不存在或已被禁用")

    access_token = create_access_token(user["id"], user["role"])
    new_refresh_token = create_refresh_token(user["id"])

    from src.config import JWT_ACCESS_TOKEN_EXPIRE_MINUTES
    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh_token,
        expires_in=JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


# ===================================================================
# 知识库管理
# ===================================================================

@router.post("/knowledge_bases", response_model=KnowledgeBaseResponse)
async def create_kb(
    req: KnowledgeBaseCreateRequest,
    user: dict = Depends(require_role("editor")),
    request: Request = None,
):
    """创建知识库"""
    kb_id = uuid.uuid4().hex
    kb = create_knowledge_base(kb_id, req.name, req.description, user["id"])
    log_audit(user["id"], "create_kb", "knowledge_base", kb_id,
              details=json.dumps({"name": req.name}, ensure_ascii=False),
              ip_address=_get_client_ip(request))
    return KnowledgeBaseResponse(**kb)


@router.get("/knowledge_bases", response_model=list[KnowledgeBaseResponse])
async def list_kbs(user: dict = Depends(get_current_user)):
    """列出知识库（admin看全部，普通用户看自己的）"""
    if user["role"] == "admin":
        kbs = list_knowledge_bases()
    else:
        kbs = list_knowledge_bases(owner_id=user["id"])
    return [KnowledgeBaseResponse(**kb) for kb in kbs]


@router.get("/knowledge_bases/{kb_id}", response_model=KnowledgeBaseResponse)
async def get_kb(kb_id: str, user: dict = Depends(get_current_user)):
    """获取知识库详情"""
    kb = get_knowledge_base(kb_id)
    if kb is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    return KnowledgeBaseResponse(**kb)


# ===================================================================
# 核心接口
# ===================================================================

@router.post("/query", response_model=QueryResponse)
async def query(
    req: QueryRequest,
    user: dict = Depends(get_current_user),
    request: Request = None,
):
    """知识库问答（核心接口）"""
    request_id = f"req_{uuid.uuid4().hex[:12]}"

    # 如果指定了 kb_id，切换向量库 collection
    if req.kb_id:
        kb = get_knowledge_base(req.kb_id)
        if kb is None:
            raise HTTPException(status_code=404, detail="知识库不存在")
        vector_store.set_kb(req.kb_id)

    # 多轮会话
    session_id = req.session_id or f"sess_{uuid.uuid4().hex[:8]}"
    session_manager.add_message(session_id, "user", req.question)
    history = session_manager.get_history(session_id)[:-1]

    # M8: 获取对话摘要
    summary = ""
    if USE_CONVERSATION_SUMMARY:
        summary = session_manager.get_summary(session_id)

    response = rag_engine.query(req.question, top_k=req.top_k, history=history,
                                  summary=summary, user_id=user["id"])
    session_manager.add_message(session_id, "assistant", response.answer)

    sources = [
        Source(
            file=s["metadata"].get("source", "未知"),
            section=s["metadata"].get("section", ""),
            content_type=s["metadata"].get("content_type", "text"),
            chunk=s["content"],
            score=s["score"],
        )
        for s in response.sources
    ]

    # 审计日志
    log_audit(
        user["id"], "query", "knowledge_base",
        req.kb_id or "default",
        details=json.dumps({
            "question": req.question,
            "intent": response.intent,
            "is_followup": response.is_followup,
        }, ensure_ascii=False),
        ip_address=_get_client_ip(request) if request else "",
    )

    return QueryResponse(
        request_id=response.trace_id or request_id,
        answer=response.answer,
        sources=sources,
        usage=response.usage,
        timing=response.timing,
    )


# ===================================================================
# M5: 反馈接口
# ===================================================================

@router.post("/feedback")
async def submit_feedback(
    req: FeedbackRequest,
    user: dict = Depends(get_current_user),
    request: Request = None,
):
    """用户反馈（赞/踩）"""
    if req.rating not in (1, -1):
        raise HTTPException(status_code=400, detail="rating 必须为 1（赞）或 -1（踩）")
    from src.storage.database import create_feedback
    feedback_id = create_feedback(
        request_id=req.request_id,
        query=req.query or "",
        rating=req.rating,
        user_id=user["id"],
    )
    log_audit(
        user["id"], "feedback", "query",
        req.request_id,
        details=json.dumps({"rating": req.rating}, ensure_ascii=False),
        ip_address=_get_client_ip(request) if request else "",
    )
    return {"id": feedback_id, "message": "反馈已记录"}


# ===================================================================
# M8: 对话导出
# ===================================================================

@router.get("/sessions/{session_id}/export")
async def export_session(
    session_id: str,
    user: dict = Depends(get_current_user),
):
    """导出对话记录为Markdown格式"""
    session = session_manager.get_or_create_session(session_id)
    if not session.messages:
        raise HTTPException(status_code=404, detail="会话不存在或无消息")

    markdown = session_manager.export_session_markdown(session_id)
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="session_{session_id}.md"',
        },
    )


# ===================================================================
# 其他接口
# ===================================================================

@router.get("/health", response_model=HealthResponse)
async def health():
    """健康检查（含向量库状态）"""
    if RAG_ENGINE == "langchain":
        # LangChain模式：直接查询Chroma状态
        try:
            count = vector_store._collection.count()
            vs_health = {"status": "ok", "count": count}
        except Exception as e:
            vs_health = {"status": "error", "error": str(e)}
    else:
        vs_health = vector_store.health_check()
    overall_status = "ok" if vs_health["status"] == "ok" else "degraded"
    return HealthResponse(
        status=overall_status,
        version="1.2.0",
        vector_store=vs_health,
    )


@router.get("/stats", response_model=StatsResponse)
async def stats(user: dict = Depends(get_current_user)):
    """知识库统计"""
    from src.config import DATA_DIR
    total_documents = len(list(DATA_DIR.rglob("*.md")))
    if RAG_ENGINE == "langchain":
        total_chunks = vector_store._collection.count()
    else:
        total_chunks = vector_store.count()
    return StatsResponse(total_documents=total_documents, total_chunks=total_chunks)


@router.get("/documents", response_model=DocumentListResponse)
async def list_documents(user: dict = Depends(get_current_user)):
    """查看文档列表"""
    from src.storage.database import list_documents as db_list_documents
    records = db_list_documents()
    documents = [
        DocumentInfo(
            id=r.id,
            filename=r.filename,
            chunks=r.chunk_count,
            indexed_at=r.indexed_at,
        )
        for r in records
    ]
    return DocumentListResponse(documents=documents, total=len(documents))


@router.get("/documents/{document_id}/chunks", response_model=ChunkListResponse)
async def get_document_chunks(document_id: str, user: dict = Depends(get_current_user)):
    """获取文档的所有 chunk 列表"""
    from src.storage.database import get_document, get_user_readable_doc_ids

    doc = get_document(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    # 权限检查
    readable_ids = get_user_readable_doc_ids(user["id"])
    if user["role"] != "admin" and readable_ids and document_id not in readable_ids:
        raise HTTPException(status_code=403, detail="无权访问该文档")

    # 从向量库查询 chunks
    if RAG_ENGINE == "langchain":
        chunks = rag_engine.query_by_source(doc.filename)
    else:
        chunks = rag_engine.vector_store.query_by_source(doc.filename)

    return ChunkListResponse(
        document_id=document_id,
        filename=doc.filename,
        chunks=[ChunkInfo(**c) for c in chunks],
        total=len(chunks),
    )


@router.post("/documents/upload", response_model=UploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    user: dict = Depends(require_role("editor")),
):
    """上传文档并触发增量索引"""
    from src.config import DATA_DIR, MAX_UPLOAD_SIZE_MB, ALLOWED_FILE_TYPES
    import hashlib

    # 校验文件类型
    suffix = Path(file.filename).suffix.lower() if file.filename else ""
    if suffix not in ALLOWED_FILE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型: {suffix}，允许: {', '.join(ALLOWED_FILE_TYPES)}",
        )

    # 读取并校验大小
    content = await file.read()
    max_bytes = MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"文件过大，最大允许 {MAX_UPLOAD_SIZE_MB}MB",
        )

    # 保存到 data/
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    save_path = DATA_DIR / file.filename
    save_path.write_bytes(content)

    # 计算 hash 并注册文档
    file_hash = hashlib.md5(content).hexdigest()
    doc_id = uuid.uuid4().hex
    from src.storage.database import init_db, upsert_document
    from src.storage.models import DocumentRecord

    init_db()
    doc = DocumentRecord(
        id=doc_id,
        filename=file.filename,
        file_path=str(save_path),
        file_hash=file_hash,
        file_type=suffix,
        file_size=len(content),
        status="pending",
    )
    upsert_document(doc)

    # 触发增量索引（仅索引刚上传的文件）
    try:
        from src.core.incremental_indexer import IncrementalIndexer
        indexer = IncrementalIndexer()
        indexer._add_file(save_path)
        doc.status = "indexed"
        doc.indexed_at = DocumentRecord.now()
        doc.updated_at = doc.indexed_at
        upsert_document(doc)
    except Exception as e:
        doc.status = "error"
        doc.error_message = str(e)[:500]
        upsert_document(doc)
        return UploadResponse(
            success=False,
            message=f"文件已保存但索引失败: {e}",
            document_id=doc_id,
        )

    log_audit(user["id"], "upload_document", "document", doc_id,
              details=json.dumps({"filename": file.filename}, ensure_ascii=False))

    return UploadResponse(
        success=True,
        message=f"上传成功，已索引为 {doc.chunk_count} 个chunk",
        document_id=doc_id,
    )


@router.delete("/documents/{document_id}")
async def delete_document(document_id: str, user: dict = Depends(require_role("editor"))):
    """删除文档（向量库 + 磁盘文件 + 数据库记录）"""
    from src.storage.database import get_document as db_get_document, delete_document as db_delete_document

    doc = db_get_document(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")

    # 从向量库删除该文件的所有 chunk
    try:
        all_data = vector_store.get_all()
        ids_to_delete = []
        for i, metadata in enumerate(all_data.get("metadatas", [])):
            if metadata.get("source_file", "") == doc.filename:
                ids_to_delete.append(all_data["ids"][i])
        if ids_to_delete:
            vector_store.delete(ids=ids_to_delete)
    except Exception:
        pass  # 向量库中可能没有数据，忽略

    # 删除磁盘文件
    file_path = Path(doc.file_path)
    if file_path.exists():
        file_path.unlink()

    # 删除数据库记录
    db_delete_document(document_id)

    log_audit(user["id"], "delete_document", "document", document_id,
              details=json.dumps({"filename": doc.filename}, ensure_ascii=False))

    return {"success": True, "message": f"已删除文档: {doc.filename}"}


@router.post("/index/scan")
async def index_scan(user: dict = Depends(require_role("admin")), request: Request = None):
    """扫描文件变化"""
    from src.core.document_scanner import scan_data_directory
    scan_result = scan_data_directory()
    log_audit(user["id"], "index_scan", "system", "",
              ip_address=_get_client_ip(request) if request else "")
    return {
        "added": [str(p) for p in scan_result.added],
        "modified": [str(p) for p in scan_result.modified],
        "deleted": scan_result.deleted,
        "summary": scan_result.summary(),
    }


@router.post("/index/sync")
async def index_sync(user: dict = Depends(require_role("admin")), request: Request = None):
    """执行增量同步"""
    from src.core.incremental_indexer import IncrementalIndexer
    indexer = IncrementalIndexer()
    stats = indexer.sync()
    log_audit(user["id"], "index_sync", "system", "",
              details=json.dumps(stats, ensure_ascii=False),
              ip_address=_get_client_ip(request) if request else "")
    return {
        "added": stats["added"],
        "updated": stats["updated"],
        "deleted": stats["deleted"],
        "errors": stats["errors"],
    }


@router.get("/index/status")
async def index_status(user: dict = Depends(get_current_user)):
    """查看索引状态"""
    from src.storage.database import get_stats
    return get_stats()


# ===================================================================
# 文件监听器控制（仅 admin）
# ===================================================================

@router.post("/index/watcher/start")
async def watcher_start(user: dict = Depends(require_role("admin")), request: Request = None):
    """启动文件监听器"""
    from src.core.watcher import get_watcher
    watcher = get_watcher()
    if watcher.is_running:
        return {"status": "already_running", "message": "文件监听器已在运行"}
    watcher.start()
    log_audit(user["id"], "watcher_start", "system", "",
              ip_address=_get_client_ip(request) if request else "")
    return {"status": "started", "message": "文件监听器已启动"}


@router.post("/index/watcher/stop")
async def watcher_stop(user: dict = Depends(require_role("admin")), request: Request = None):
    """停止文件监听器"""
    from src.core.watcher import get_watcher
    watcher = get_watcher()
    if not watcher.is_running:
        return {"status": "not_running", "message": "文件监听器未在运行"}
    watcher.stop()
    log_audit(user["id"], "watcher_stop", "system", "",
              ip_address=_get_client_ip(request) if request else "")
    return {"status": "stopped", "message": "文件监听器已停止"}


@router.get("/index/watcher/status")
async def watcher_status(user: dict = Depends(get_current_user)):
    """查看文件监听器状态"""
    from src.core.watcher import get_watcher
    watcher = get_watcher()
    return {"running": watcher.is_running}


# ===================================================================
# 审计日志查询（仅 admin）
# ===================================================================

@router.get("/audit_logs")
async def get_audit_logs(
    user: dict = Depends(require_role("admin")),
    action: str | None = None,
    limit: int = 100,
):
    """查询审计日志"""
    from src.storage.database import list_audit_logs
    return list_audit_logs(action=action, limit=limit)


# ===================================================================
# 旧 API Key 管理（保留兼容）
# ===================================================================

@router.post("/keys")
async def create_key(role: str = "reader", user: dict = Depends(require_role("admin"))):
    """创建API Key（仅管理员，旧接口保留）"""
    from src.api.auth import create_api_key
    new_key = create_api_key(role)
    return {"key": new_key["key"], "role": new_key["role"]}


# ===================================================================
# M4: 监控接口
# ===================================================================

@router.get("/metrics")
async def get_metrics(user: dict = Depends(require_role("admin"))):
    """返回当前系统指标（计数器 + 直方图）"""
    return metrics.snapshot()


@router.get("/metrics/prometheus")
async def get_prometheus_metrics(user: dict = Depends(require_role("admin"))):
    """返回 Prometheus exposition format 的指标文本"""
    return PlainTextResponse(content=metrics.to_prometheus())


@router.get("/traces/{trace_id}")
async def get_trace_detail(trace_id: str, user: dict = Depends(get_current_user)):
    """查看完整调用链路"""
    trace = get_trace(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="trace不存在")
    return trace


@router.get("/traces")
async def list_traces(
    user: dict = Depends(require_role("admin")),
    limit: int = 20,
):
    """列出最近的trace"""
    return list_recent_traces(limit=limit)


@router.get("/alerts")
async def get_alerts(
    user: dict = Depends(require_role("admin")),
    limit: int = 50,
):
    """返回最近的告警记录"""
    # 先触发一次检查
    alert_manager.check_all()
    return metrics.get_recent_alerts(limit=limit)


# ===================================================================
# M9: 评测接口
# ===================================================================

@router.get("/evaluations")
async def list_evaluations(
    user: dict = Depends(require_role("admin")),
    limit: int = 20,
):
    """列出历史评测记录"""
    from src.storage.database import list_evaluations
    return list_evaluations(limit=limit)


@router.get("/evaluations/latest")
async def get_latest_evaluation(
    user: dict = Depends(require_role("admin")),
):
    """获取最近一次评测结果"""
    from src.storage.database import get_latest_evaluation
    result = get_latest_evaluation()
    if result is None:
        raise HTTPException(status_code=404, detail="暂无评测记录")
    return result


@router.post("/evaluations/run")
async def run_evaluation_now(
    user: dict = Depends(require_role("admin")),
    version: str = "",
):
    """手动触发一次评测"""
    from evaluate import run_evaluation, save_to_database
    summary = run_evaluation(version=version, engine=rag_engine)
    if "error" in summary:
        raise HTTPException(status_code=500, detail=summary["error"])
    save_to_database(summary)
    return {k: v for k, v in summary.items() if k != "results"}


@router.get("/evaluations/compare")
async def compare_evaluations(
    ver_a: str,
    ver_b: str,
    user: dict = Depends(require_role("admin")),
):
    """对比两个版本的评测结果"""
    from evaluate import compare_evaluations
    try:
        return compare_evaluations(ver_a, ver_b)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
