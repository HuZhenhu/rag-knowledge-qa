"""T2.7 worker 入口：消费消息队列任务并处理。

设计要点：
- 复用 T2.3 ConsumerWorker（memory/file/kafka/rabbitmq 可插拔后端），按 QUEUE_BACKEND 配置建后端。
- 内置轻量 HTTP 健康端口（默认 8081，可用 HEALTH_PORT 覆盖），供 K8s Deployment 存活/就绪探针。
  * GET /health/live  ：进程存活即返回 200。
  * GET /health/ready ：worker 就绪（队列后端已初始化）返回 200，否则 503。
- 默认任务处理器为可扩展骨架：可按 payload 的 task_type 分派到具体业务逻辑。

生产部署：
  uvicorn 由 API Deployment 承载；本进程由 worker Deployment 以
  `python -m worker` 或 `python worker.py` 启动。
"""
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from src.config import TASK_QUEUE_TOPIC, HEALTH_PORT, QUEUE_BACKEND
from src.core.queue_backend import get_queue_backend, ConsumerWorker
from src.core.metrics_exporter import set_queue_depth


# ---------------------------------------------------------------------------
# 默认任务处理器（可扩展骨架）
# ---------------------------------------------------------------------------

def default_task_handler(payload: dict) -> None:
    """按 task_type 分派处理。当前为 logging 占位，生产可在此接入索引/生成等任务。"""
    task_type = payload.get("type", payload.get("task_type", "unknown"))
    task_id = payload.get("task_id", payload.get("id", "-"))
    # TODO: 按 task_type 分发到实际业务处理器（前端 / 异步查询 / 索引构建等）
    print(f"[worker] handled task_type={task_type} task_id={task_id}")


# ---------------------------------------------------------------------------
# worker 健康检查服务
# ---------------------------------------------------------------------------

def _make_health_handler(ready: threading.Event):
    """构造健康检查 HTTP handler（返回类是原生的，便于单测与复用）。"""

    class _HealthHandler(BaseHTTPRequestHandler):
        def _reply(self, code: int, obj: dict) -> None:
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            if self.path in ("/health/live", "/health/live/"):
                self._reply(200, {"status": "ok", "service": "worker"})
                return
            if self.path in ("/health/ready", "/health/ready/"):
                if ready.is_set():
                    self._reply(200, {"status": "ok", "service": "worker"})
                else:
                    self._reply(503, {"status": "starting", "service": "worker"})
                return
            self._reply(404, {"error": "not found"})

        def log_message(self, fmt, *args):  # 静默请求日志，避免刷屏
            pass

    return _HealthHandler


def serve_health(port: int, ready: threading.Event) -> None:
    """启动 worker 健康检查服务（阻塞）。"""
    server = ThreadingHTTPServer(("0.0.0.0", port), _make_health_handler(ready))
    server.serve_forever()


# ---------------------------------------------------------------------------
# worker 主逻辑
# ---------------------------------------------------------------------------

def run_worker(topic: str = TASK_QUEUE_TOPIC, health_port: int = HEALTH_PORT,
               max_delivery: int = 3, poll_interval: float = 0.05) -> None:
    """启动 worker：健康服务（线程）+ 队列消费主循环（阻塞）。"""
    backend = get_queue_backend()
    if backend is None:
        raise RuntimeError("unable to init queue backend, check QUEUE_BACKEND config")
    worker = ConsumerWorker(
        backend=backend,
        topic=topic,
        handler=default_task_handler,
        max_delivery=max_delivery,
    )

    ready = threading.Event()
    health_thread = threading.Thread(
        target=serve_health, args=(health_port, ready), daemon=True,
        name="worker-health",
    )
    health_thread.start()

    # 队列后端就绪即视为可接受任务
    ready.set()

    while True:
        try:
            worker.process_one()
            set_queue_depth(topic, backend.get_backlog(topic))  # T2.8 队列深度上报
        except KeyboardInterrupt:
            break
        except Exception:  # noqa: BLE001
            # 单条消息处理异常已被 ConsumerWorker 捕获并重投；此处兜底避免主循环崩溃
            time.sleep(1.0)
        time.sleep(poll_interval)


def main() -> None:
    run_worker(topic=TASK_QUEUE_TOPIC, health_port=HEALTH_PORT)


if __name__ == "__main__":
    main()
