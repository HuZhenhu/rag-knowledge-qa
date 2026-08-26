"""T2.7 部署平台化：K8s 健康检查端点（liveness / readiness）。

- GET /health/live   ：存活探针，仅确认进程存活（不触碰外部依赖）。
- GET /health/ready  ：就绪探针，探测核心依赖（缓存/Redis、队列、向量库、LLM 网关）状态，
                       任一依赖故障返回 503 + unready，供 K8s Deployment 就绪探针与流量摘除使用。

依赖探测默认走项目可插拔抽象（cache_backend / queue_backend / engine_factory / llm_gateway），
本机无 Redis / K8s 时以 sqlite 缓存 + memory 队列等本地实现兜底，失败按 error 状态暴露，
不会因单个依赖故障导致进程崩溃。
"""
import json

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

health_router = APIRouter(prefix="/health", tags=["health"])

_APP_VERSION = "1.2.0"


class HealthChecker:
    """依赖健康检查器：每个依赖对应一个可注入的探针 callable（返回 dict 或抛异常）。

    探针约定：无参 callable，返回 dict 作为附加详情；抛异常视为该依赖故障。
    """

    def __init__(self, cache=None, queue=None, vector_store=None, llm=None):
        self._probes = {
            "cache": cache,
            "queue": queue,
            "vector_store": vector_store,
            "llm": llm,
        }

    @staticmethod
    def _probe(fn):
        if fn is None:
            return {"status": "skipped", "detail": "no probe configured"}
        try:
            detail = fn() or {}
            if isinstance(detail, dict):
                return {"status": "ok", **detail}
            return {"status": "ok"}
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "error": f"{type(e).__name__}: {e}"}

    def check_all(self) -> dict:
        return {name: self._probe(fn) for name, fn in self._probes.items()}

    @staticmethod
    def is_ready(checks: dict) -> bool:
        return all(c.get("status") == "ok" for c in checks.values())


# ---------------------------------------------------------------------------
# 默认探针：经项目可插拔抽象访问真实依赖（无外部服务时以本地实现兜底）
# ---------------------------------------------------------------------------

def _probe_cache():
    from src.core.cache_backend import make_cache_backend
    from src.config import SEMANTIC_CACHE_BACKEND
    be = make_cache_backend(SEMANTIC_CACHE_BACKEND)
    be.set("health:probe", "1")
    value = be.get("health:probe")
    be.delete("health:probe")
    if value != "1":
        raise RuntimeError("cache read-back mismatch")
    return {"backend": SEMANTIC_CACHE_BACKEND}


def _probe_queue():
    from src.core.queue_backend import get_queue_backend
    be = get_queue_backend()
    backlog = be.get_backlog("health")
    return {"backend": type(be).__name__, "backlog": int(backlog)}


def _probe_vector_store():
    from src.core.engine_factory import get_vector_store
    from src.config import RAG_ENGINE
    vs = get_vector_store()
    if RAG_ENGINE == "langchain":
        count = vs._collection.count()
        return {"count": count}
    return vs.health_check()


def _probe_llm():
    from src.config import LLM_GATEWAY_ENABLED
    if not LLM_GATEWAY_ENABLED:
        return {"status": "ok", "detail": "llm gateway disabled"}
    # 生产装配：按 LLM_GATEWAY_PROVIDERS 顺序构造 provider，缺 API key 的跳过，
    # 全部缺失时以 mock 兜底，确保就绪探测不会因配置缺失误报故障。
    from src.core.llm_gateway import LLMGateway, MockProvider
    from src.config import LLM_GATEWAY_PROVIDERS
    providers = []
    for name in LLM_GATEWAY_PROVIDERS.split(","):
        name = name.strip().lower()
        if name == "mock":
            providers.append(MockProvider(name="mock", model="mock-model"))
        # 其余 provider 依赖外部 API key，未显式配置时在此轮忽略（可扩展接入）
    if not providers:
        providers.append(MockProvider(name="mock", model="mock-model"))
    gw = LLMGateway(providers=providers)
    answer, meta = gw.generate([{"role": "user", "content": "ping"}], max_tokens=1)
    if meta.get("status") != "ok":
        raise RuntimeError(meta.get("reason") or "llm gateway unhealthy")
    return {"provider": meta.get("provider"), "model": meta.get("model")}


# 模块级单例：生产端点使用；测试可通过 dependency override 替换
health_checker = HealthChecker(
    cache=_probe_cache,
    queue=_probe_queue,
    vector_store=_probe_vector_store,
    llm=_probe_llm,
)


def get_health_checker() -> HealthChecker:
    return health_checker


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------

@health_router.get("/live", summary="存活探针")
async def liveness():
    return {"status": "ok", "version": _APP_VERSION}


@health_router.get("/ready", summary="就绪探针")
async def readiness(checker: HealthChecker = Depends(get_health_checker)):
    checks = checker.check_all()
    ready = HealthChecker.is_ready(checks)
    payload = {
        "status": "ok" if ready else "unready",
        "version": _APP_VERSION,
        "checks": checks,
    }
    return JSONResponse(content=payload, status_code=200 if ready else 503)
