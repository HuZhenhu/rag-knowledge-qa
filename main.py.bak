"""FastAPI启动入口"""
import json
import asyncio
import time
import uuid
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from src.api.routes import router
from src.api.rate_limit import RateLimitMiddleware
from src.api.auth import API_KEYS
from src.api.jwt_auth import register_legacy_api_key
from src.api.logging_config import log_request, request_id_ctx, logger
from src.core.metrics import metrics
from src.core.alert_manager import alert_manager
from src.core.tracer import init_traces_table
from src.storage.database import init_db
from src.config import API_HOST, API_PORT, USE_QUERY_EXPANSION, USE_HYDE, USE_RERANKER
from src.core.session import SessionManager

# 初始化所有数据表（包括新增的 users / knowledge_bases 等）
init_db()
init_traces_table()


# ---------------------------------------------------------------------------
# M4: 请求日志中间件
# ---------------------------------------------------------------------------

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """为每个HTTP请求记录结构化日志"""

    async def dispatch(self, request: Request, call_next):
        req_id = f"req_{uuid.uuid4().hex[:12]}"
        token = request_id_ctx.set(req_id)

        start = time.time()
        try:
            response: Response = await call_next(request)
        except Exception:
            # 记录错误
            latency_ms = (time.time() - start) * 1000
            metrics.inc_counter("total_errors")
            log_request(request.method, request.url.path, 500, latency_ms)
            raise
        finally:
            request_id_ctx.reset(token)

        latency_ms = (time.time() - start) * 1000
        log_request(request.method, request.url.path,
                     response.status_code, latency_ms)

        # 将 request_id 注入响应头，方便前端追踪
        response.headers["X-Request-ID"] = req_id

        # 定期检查告警
        alert_manager.check_all()

        return response

# 预置开发用 API Key（旧体系，保持向后兼容）
API_KEYS["sk-rag-dev-key-12345"] = {
    "key": "sk-rag-dev-key-12345",
    "role": "admin",
    "created_at": "2026-01-01",
    "active": True,
}
# 注册到 JWT 模块的旧 Key 存储，供统一认证回退使用
register_legacy_api_key("sk-rag-dev-key-12345", API_KEYS["sk-rag-dev-key-12345"])

app = FastAPI(
    title="RAG智能问答API",
    description="基于个人知识库的智能问答服务",
    version="1.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# M4: 请求日志
app.add_middleware(RequestLoggingMiddleware)

# 限流
app.add_middleware(RateLimitMiddleware)

# 路由
app.include_router(router)

# RAG 引擎（WebSocket 使用）
from src.core.rag_engine import RAGEngine
rag_engine = RAGEngine(
    use_query_expansion=USE_QUERY_EXPANSION,
    use_hyde=USE_HYDE,
    use_reranker=USE_RERANKER,
)
session_manager = SessionManager()


@app.get("/")
async def root():
    return {
        "message": "RAG智能问答API",
        "docs": "/docs",
        "version": "1.1.0",
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, session_id: str = "default"):
    """WebSocket端点 - 前端聊天"""
    await websocket.accept()
    print(f"WebSocket连接: session={session_id}")

    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)

            if message.get("type") == "query":
                query = message.get("query", "")
                message_id = message.get("message_id", "")

                if not query:
                    await websocket.send_json({
                        "type": "error",
                        "message": "查询内容不能为空",
                    })
                    continue

                # 记录用户消息到会话
                session_manager.add_message(session_id, "user", query)

                # 获取对话历史和摘要
                history = session_manager.get_history(session_id)[:-1]
                summary = session_manager.get_summary(session_id)

                loop = asyncio.get_event_loop()
                try:
                    response = await loop.run_in_executor(
                        None,
                        lambda: rag_engine.query(
                            query,
                            top_k=3,
                            history=history,
                            summary=summary,
                            user_id=session_id,
                        ),
                    )

                    # 记录AI回复到会话
                    session_manager.add_message(session_id, "assistant", response.answer)

                    await websocket.send_json({
                        "type": "token",
                        "message_id": message_id,
                        "token": response.answer,
                    })

                    sources = []
                    for s in response.sources:
                        meta = s.get("metadata", {})
                        file_name = meta.get("source_file", "") or meta.get("source", "未知")
                        if "\\" in file_name or "/" in file_name:
                            file_name = file_name.replace("\\", "/").split("/")[-1]
                        sources.append({
                            "file": file_name,
                            "section": meta.get("section", ""),
                            "chunk": s["content"],
                            "score": s["score"],
                        })

                    await websocket.send_json({
                        "type": "sources",
                        "message_id": message_id,
                        "sources": sources,
                    })

                    await websocket.send_json({
                        "type": "done",
                        "message_id": message_id,
                        "timing": response.timing,
                    })

                except Exception as e:
                    await websocket.send_json({
                        "type": "error",
                        "message": f"查询失败: {str(e)}",
                    })

    except WebSocketDisconnect:
        print(f"WebSocket断开: session={session_id}")
    except Exception as e:
        print(f"WebSocket错误: {e}")


@app.websocket("/ws/data-monitor")
async def data_monitor_endpoint(websocket: WebSocket):
    """WebSocket端点 - 数据变化监控（文件新增/修改/索引进度）"""
    from src.core.data_monitor import get_data_monitor
    monitor = get_data_monitor()
    await monitor.connect(websocket)
    try:
        while True:
            # 保持连接，等待客户端消息（心跳或控制指令）
            data = await websocket.receive_text()
            msg = json.loads(data)
            if msg.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        await monitor.disconnect(websocket)
    except Exception:
        await monitor.disconnect(websocket)


# M9: 启动评测定时任务
try:
    from src.core.eval_scheduler import start_scheduler
    eval_scheduler = start_scheduler()
except Exception as e:
    logger.warning("评测调度器启动失败: %s", e)

# 文件监听器（可选自动启动）
from src.config import WATCHER_AUTO_START
if WATCHER_AUTO_START:
    try:
        from src.core.watcher import get_watcher
        get_watcher().start()
        logger.info("文件监听器已自动启动")
    except Exception as e:
        logger.warning("文件监听器启动失败: %s", e)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=API_HOST, port=API_PORT)
