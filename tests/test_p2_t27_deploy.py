"""T2.7 部署平台化：健康检查端点（liveness/readiness）单元测试（红测先行）"""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import health
from src.api.health import (
    HealthChecker,
    health_router,
    liveness,
    readiness,
    get_health_checker,
)


def _client(checker: HealthChecker) -> TestClient:
    """构造挂载 health_router 的测试 app，并用 dependency override 注入指定 checker。"""
    app = FastAPI()
    app.include_router(health_router)

    def _override() -> HealthChecker:
        return checker

    app.dependency_overrides[get_health_checker] = _override
    return TestClient(app)


def _ok_checker() -> HealthChecker:
    """所有依赖健康。"""
    return HealthChecker(
        cache=lambda: {"backend": "sqlite"},
        queue=lambda: {"backlog": 0},
        vector_store=lambda: {"count": 12},
        llm=lambda: {"provider": "mock"},
    )


def _broken_llm_checker() -> HealthChecker:
    """llm 依赖故障，其余正常。"""
    return HealthChecker(
        cache=lambda: {"backend": "sqlite"},
        queue=lambda: {"backlog": 0},
        vector_store=lambda: {"count": 12},
        llm=lambda: (_ for _ in ()).throw(ConnectionError("llm unavailable")),
    )


# ---------------- T2.7-1 liveness 存活探针 ----------------

def test_liveness_returns_ok():
    client = _client(_ok_checker())
    resp = client.get("/health/live")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data


# ---------------- T2.7-2 readiness 就绪探针（全依赖健康） ----------------

def test_readiness_all_ok_returns_200():
    client = _client(_ok_checker())
    resp = client.get("/health/ready")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    # 必须覆盖 Redis/向量库/LLM 等核心依赖
    assert set(data["checks"].keys()) == {"cache", "queue", "vector_store", "llm"}
    assert all(c["status"] == "ok" for c in data["checks"].values())


# ---------------- T2.7-3 readiness 就绪探针（依赖故障） ----------------

def test_readiness_dependency_failed_returns_503():
    client = _client(_broken_llm_checker())
    resp = client.get("/health/ready")
    assert resp.status_code == 503
    data = resp.json()
    assert data["status"] == "unready"
    assert data["checks"]["llm"]["status"] == "error"
    assert "llm unavailable" in data["checks"]["llm"]["error"]
    # 其余依赖仍为 ok，便于定位故障点
    assert data["checks"]["cache"]["status"] == "ok"
    assert data["checks"]["vector_store"]["status"] == "ok"


# ---------------- T2.7-4 HealthChecker 探测逻辑 ----------------

def test_health_checker_uses_injected_probes():
    checker = _ok_checker()
    checks = checker.check_all()
    assert set(checks.keys()) == {"cache", "queue", "vector_store", "llm"}
    assert HealthChecker.is_ready(checks) is True


def test_health_checker_captures_exception_with_type():
    checker = _broken_llm_checker()
    checks = checker.check_all()
    assert checks["llm"]["status"] == "error"
    assert "ConnectionError" in checks["llm"]["error"]


def test_health_checker_none_probe_skipped():
    checker = HealthChecker(cache=None, queue=None, vector_store=None, llm=None)
    checks = checker.check_all()
    assert all(c["status"] == "skipped" for c in checks.values())
    assert HealthChecker.is_ready(checks) is False


# ---------------- T2.7-5 默认探测已配置（不触发外部依赖） ----------------

def test_default_probes_registered():
    """默认 HealthChecker 应注册 cache/queue/vector_store/llm 四个探针（不实际调用）。"""
    checker = health.health_checker
    assert set(checker._probes.keys()) == {"cache", "queue", "vector_store", "llm"}
    assert all(fn is not None for fn in checker._probes.values())


# ---------------- T2.7-6 main 应用注册健康路由 ----------------

def test_main_registers_health_router():
    """main.py 必须 include health_router，确保生产入口暴露 /health/* 探针。"""
    from pathlib import Path
    main_src = Path(health.__file__).parent.parent.parent / "main.py"
    text = main_src.read_text(encoding="utf-8")
    assert "health_router" in text
    assert "include_router(health_router)" in text


# ---------------- T2.7-7 探针返回字段可直接被 Prometheus/K8s 消费 ----------------

def test_readiness_payload_is_json_serializable():
    client = _client(_ok_checker())
    resp = client.get("/health/ready")
    body = resp.json()  # 反序列化成功即证明可 JSON 序列化
    assert isinstance(body["checks"], dict)


# ---------------- T2.7-8 worker 健康服务（K8s worker 探针） ----------------

def _start_worker_server(ready):
    import threading
    from http.server import ThreadingHTTPServer
    import urllib.request
    from worker import _make_health_handler

    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_health_handler(ready))
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


def _fetch(server, path):
    import urllib.request
    import json as _json
    with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}{path}", timeout=5) as r:
        return r.status, _json.loads(r.read().decode("utf-8"))


def test_worker_health_live_ok(monkeypatch):
    import threading
    ready = threading.Event()
    server = _start_worker_server(ready)
    try:
        code, body = _fetch(server, "/health/live")
        assert code == 200
        assert body["status"] == "ok"
        assert body["service"] == "worker"
    finally:
        server.shutdown()


def test_worker_health_ready_ok_when_ready(monkeypatch):
    import threading
    ready = threading.Event()
    ready.set()
    server = _start_worker_server(ready)
    try:
        code, body = _fetch(server, "/health/ready")
        assert code == 200
        assert body["status"] == "ok"
    finally:
        server.shutdown()


def test_worker_health_ready_503_before_ready(monkeypatch):
    import threading
    ready = threading.Event()  # 未 set → 未就绪
    server = _start_worker_server(ready)
    try:
        with pytest.raises(Exception) as exc:
            _fetch(server, "/health/ready")
        assert "HTTP Error 503" in str(exc.value)
    finally:
        server.shutdown()
