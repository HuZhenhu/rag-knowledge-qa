"""FastAPI启动入口"""
import json
import asyncio
import time
import uuid
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from src.api.routes import router, ws_router
from src.api.rate_limit import RateLimitMiddleware
from src.api.jwt_auth import register_legacy_key_from_env
from src.api.logging_config import log_request, request_id_ctx, logger
from src.core.metrics import metrics
from src.core.alert_manager import alert_manager
from src.core.tracer import init_traces_table
from src.storage.database import init_db
from src.config import (
    API_HOST, API_PORT, UVICORN_WORKERS,
    CORS_ALLOW_ORIGINS, CORS_ALLOW_CREDENTIALS, validate_security_config,
)
from src.core.session import SessionManager

# T0.2: 启动安全校验——生产环境（APP_ENV=production）使用默认 JWT 密钥时直接报错退出
validate_security_config()

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

# T0.3: 旧体系 API Key 可选注入——默认不注册任何 key（消除默认 admin 后门）。
# 需启用时配置环境变量 LEGACY_API_KEY（可选 LEGACY_API_KEY_ROLE=viewer/editor/admin）。
register_legacy_key_from_env()


# ---------------------------------------------------------------------------
# T1.3: 启动预热（消除首请求 4 秒冷启动）
# ---------------------------------------------------------------------------

def warmup_models() -> None:
    """预热 embedding 与 reranker 模型，仅初始化一次。"""
    from src.core.embedder import Embedder
    from src.core.reranker import Reranker
    from src.config import WARMUP_ON_START

    if not WARMUP_ON_START:
        return
    try:
        Embedder().warmup()
        logger.info("Embedding 模型预热完成")
    except Exception as e:  # noqa: BLE001
        logger.warning("Embedding 预热失败（不影响启动）: %s", e)
    try:
        Reranker().warmup()
        logger.info("ReRanker 模型预热完成")
    except Exception as e:  # noqa: BLE001
        logger.warning("ReRanker 预热失败（不影响启动）: %s", e)


from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app):
    """应用生命周期：启动时预热模型（T1.3）"""
    import asyncio
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, warmup_models)
    yield


app = FastAPI(
    lifespan=lifespan,
    title="RAG智能问答API",
    description="基于个人知识库的智能问答服务",
    version="1.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# T0.1: CORS 白名单化——allow_origins 从 env 读取（默认仅 localhost）；
# 白名单含 * 时 allow_credentials 自动为 False，杜绝非法组合。
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS,
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# M4: 请求日志
app.add_middleware(RequestLoggingMiddleware)

# 限流
app.add_middleware(RateLimitMiddleware)

# 路由
app.include_router(router)

# T1.6: 提交-推送 WebSocket 通道（/ws/tasks）
app.include_router(ws_router)

# T1.2: 引擎单例化——HTTP 与 WebSocket 经 engine_factory 共享同一实例（缓存/BM25 只建一次）
from src.core.engine_factory import get_engine
rag_engine = get_engine()
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
                    full_answer = ""
                    final_sources = []
                    final_timing = {}

                    # 流式生成（T1.1：同步生成器放入线程池，避免阻塞事件循环）
                    from src.core.async_util import aiter_in_thread
                    async for token, is_last, sources, timing in aiter_in_thread(
                        rag_engine.query_stream,
                        query,
                        top_k=3,
                        history=history,
                        summary=summary,
                        user_id=session_id,
                    ):
                        if is_last:
                            final_sources = sources
                            final_timing = timing
                        elif token:
                            full_answer += token
                            await websocket.send_json({
                                "type": "token",
                                "message_id": message_id,
                                "token": token,
                            })

                    # 记录AI回复到会话
                    session_manager.add_message(session_id, "assistant", full_answer)

                    # Agent 推理过程事件推送（Agentic RAG 前端可视化，M5，设计文档 §4.1）
                    # 仅 agentic 引擎产生 _last_agent_trace；普通引擎无该属性，不推送任何 agent 事件
                    agent_trace = getattr(rag_engine, "_last_agent_trace", None) or []
                    for ev in agent_trace:
                        ev_type = ev.get("event", "")
                        if ev_type not in (
                            "agent_plan",
                            "agent_tool_call",
                            "agent_evidence",
                            "agent_reflect",
                            "agent_final",
                            "user_question",
                        ):
                            continue
                        await websocket.send_json({
                            "type": ev_type,
                            "message_id": message_id,
                            "data": ev,
                        })

                    # 发送来源
                    source_list = []
                    for s in final_sources:
                        meta = s.get("metadata", {})
                        file_name = meta.get("source_file", "") or meta.get("source", "未知")
                        if "\\" in file_name or "/" in file_name:
                            file_name = file_name.replace("\\", "/").split("/")[-1]
                        source_list.append({
                            "file": file_name,
                            "section": meta.get("section", ""),
                            "chunk": s["content"],
                            "score": s["score"],
                        })

                    await websocket.send_json({
                        "type": "sources",
                        "message_id": message_id,
                        "sources": source_list,
                    })

                    await websocket.send_json({
                        "type": "done",
                        "message_id": message_id,
                        "timing": final_timing,
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
    # T1.3: 多 worker（默认 UVICORN_WORKERS=1 保持现行为；生产设 4 利用多核）。
    # 多 worker 模式下必须以 "main:app" 字符串形式传入，由 uvicorn 各 worker 独立 import。
    uvicorn.run("main:app", host=API_HOST, port=API_PORT, workers=UVICORN_WORKERS)
