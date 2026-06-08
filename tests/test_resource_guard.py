from __future__ import annotations

import threading
import time
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app_v2.runtime import resource_guard  # noqa: E402


def _cpu_ok() -> dict:
    return {
        "process_cpu_cores": 0.0,
        "process_cpu_guard_cores": 4.5,
        "process_cpu_over_limit": False,
    }


def test_flowi_verify_and_workflow_catalog_are_default_light_paths(monkeypatch):
    monkeypatch.delenv("FLOW_LIGHT_API_PATHS", raising=False)

    paths = resource_guard._light_paths()

    assert "/api/llm/flowi/verify" in paths
    assert "/api/llm/flowi/workflows" in paths
    assert resource_guard._matches("/api/llm/flowi/verify", paths)
    assert resource_guard._matches("/api/llm/flowi/workflows", paths)
    assert not resource_guard._matches("/api/llm/flowi/chat", paths)


def test_splittable_view_cache_first_uses_heavy_middleware(monkeypatch):
    monkeypatch.delenv("FLOW_LIGHT_API_PATHS", raising=False)
    monkeypatch.delenv("FLOW_HEAVY_API_PREFIXES", raising=False)
    monkeypatch.setenv("FLOW_RESOURCE_GUARD_RECHECK_DELAY_SEC", "0")
    monkeypatch.setattr(resource_guard, "process_memory_high", lambda _reserve_gb: True)
    monkeypatch.setattr(
        resource_guard,
        "process_memory_snapshot",
        lambda: {"process_rss_gb": 12.0, "process_memory_over_limit": True},
    )
    monkeypatch.setattr(resource_guard, "process_cpu_snapshot", lambda guard_cores=None: _cpu_ok())

    app = FastAPI()
    calls = {"plain": 0, "cache_first": 0}

    @app.get("/api/splittable/view")
    def splittable_view(cache_first: int = 0):
        if cache_first:
            calls["cache_first"] += 1
        else:
            calls["plain"] += 1
        return {"ok": True, "cache_first": bool(cache_first)}

    app.add_middleware(resource_guard.ResourceGuardMiddleware)
    client = TestClient(app)

    plain = client.get("/api/splittable/view?product=ML_TABLE_PRODA&root_lot_id=A1000")
    cache_first = client.get("/api/splittable/view?product=ML_TABLE_PRODA&root_lot_id=A1000&cache_first=1")

    assert plain.status_code == 503
    assert plain.json()["error_code"] == "resource_memory_guard"
    assert cache_first.status_code == 503
    assert cache_first.json()["error_code"] == "resource_memory_guard"
    assert calls == {"plain": 0, "cache_first": 0}


def test_flowi_verify_and_workflow_catalog_bypass_heavy_middleware(monkeypatch):
    monkeypatch.delenv("FLOW_LIGHT_API_PATHS", raising=False)
    monkeypatch.delenv("FLOW_HEAVY_API_PREFIXES", raising=False)
    monkeypatch.setattr(resource_guard, "process_memory_high", lambda _reserve_gb: False)
    monkeypatch.setattr(resource_guard, "process_cpu_snapshot", lambda guard_cores=None: _cpu_ok())

    app = FastAPI()

    @app.post("/api/llm/flowi/verify")
    def verify():
        return {"ok": True}

    @app.get("/api/llm/flowi/workflows")
    def workflows():
        return {"ok": True}

    @app.post("/api/llm/flowi/chat")
    def chat():
        return {"ok": True}

    app.add_middleware(resource_guard.ResourceGuardMiddleware)
    client = TestClient(app)

    verify_response = client.post("/api/llm/flowi/verify", json={})
    workflows_response = client.get("/api/llm/flowi/workflows")
    chat_response = client.post("/api/llm/flowi/chat", json={})

    assert verify_response.status_code == 200
    assert workflows_response.status_code == 200
    assert chat_response.status_code == 200
    assert "X-Flow-Heavy-Request-Concurrency" not in verify_response.headers
    assert "X-Flow-Heavy-Request-Concurrency" not in workflows_response.headers
    assert "X-Flow-Heavy-Request-Concurrency" in chat_response.headers


def test_flowi_chat_does_not_wait_behind_generic_heavy_request(monkeypatch):
    monkeypatch.delenv("FLOW_LIGHT_API_PATHS", raising=False)
    monkeypatch.delenv("FLOW_HEAVY_API_PREFIXES", raising=False)
    monkeypatch.setenv("FLOW_HEAVY_REQUEST_CONCURRENCY", "1")
    monkeypatch.setenv("FLOW_HEAVY_REQUEST_QUEUE_TIMEOUT_SEC", "1")
    monkeypatch.setenv("FLOW_FLOWI_CHAT_CONCURRENCY", "1")
    monkeypatch.setenv("FLOW_FLOWI_CHAT_QUEUE_TIMEOUT_SEC", "1")
    monkeypatch.setattr(resource_guard, "process_memory_high", lambda _reserve_gb: False)
    monkeypatch.setattr(resource_guard, "process_cpu_snapshot", lambda guard_cores=None: _cpu_ok())

    app = FastAPI()
    blocker_started = threading.Event()

    @app.get("/api/dashboard/block")
    def dashboard_block():
        blocker_started.set()
        time.sleep(1.5)
        return {"ok": True}

    @app.post("/api/llm/flowi/chat")
    def flowi_chat():
        return {"ok": True}

    app.add_middleware(resource_guard.ResourceGuardMiddleware)
    client = TestClient(app)

    thread = threading.Thread(target=lambda: client.get("/api/dashboard/block"), daemon=True)
    thread.start()
    assert blocker_started.wait(timeout=1.0)

    response = client.post("/api/llm/flowi/chat", json={})
    thread.join(timeout=3.0)

    assert response.status_code == 200
    assert response.headers["X-Flow-Heavy-Request-Group"] == "flowi_chat"


def test_small_profile_heavy_requests_are_sequential_by_default(monkeypatch):
    monkeypatch.setenv("FLOW_RESOURCE_PROFILE", "small")
    monkeypatch.delenv("FLOW_HEAVY_REQUEST_CONCURRENCY", raising=False)
    monkeypatch.delenv("FLOW_HEAVY_API_PREFIXES", raising=False)
    monkeypatch.setenv("FLOW_HEAVY_REQUEST_QUEUE_TIMEOUT_SEC", "2")
    monkeypatch.setattr(resource_guard, "process_memory_high", lambda _reserve_gb: False)
    monkeypatch.setattr(resource_guard, "process_cpu_snapshot", lambda guard_cores=None: _cpu_ok())

    app = FastAPI()
    lock = threading.Lock()
    active = 0
    max_active = 0

    @app.get("/api/dashboard/slow")
    def dashboard_slow():
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.2)
        with lock:
            active -= 1
        return {"ok": True}

    app.add_middleware(resource_guard.ResourceGuardMiddleware)
    client = TestClient(app)

    threads = [threading.Thread(target=lambda: client.get("/api/dashboard/slow"), daemon=True) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3.0)

    assert max_active == 1


def test_heavy_request_delays_then_blocks_when_cpu_stays_high(monkeypatch):
    monkeypatch.setenv("FLOW_RESOURCE_PROFILE", "small")
    monkeypatch.setenv("FLOW_RESOURCE_GUARD_RECHECK_DELAY_SEC", "0.01")
    monkeypatch.setattr(resource_guard, "process_memory_high", lambda _reserve_gb: False)
    monkeypatch.setattr(
        resource_guard,
        "process_cpu_snapshot",
        lambda guard_cores=None: {
            "process_cpu_cores": 4.6,
            "process_cpu_guard_cores": 4.5,
            "process_cpu_over_limit": True,
        },
    )

    app = FastAPI()
    called = False

    @app.get("/api/dashboard/heavy")
    def dashboard_heavy():
        nonlocal called
        called = True
        return {"ok": True}

    app.add_middleware(resource_guard.ResourceGuardMiddleware)
    client = TestClient(app)

    response = client.get("/api/dashboard/heavy")

    assert response.status_code == 429
    assert response.json()["error_code"] == "resource_cpu_guard"
    assert response.headers["Retry-After"] == "15"
    assert called is False


def test_heavy_request_proceeds_when_memory_recovers_after_delay(monkeypatch):
    monkeypatch.setenv("FLOW_RESOURCE_PROFILE", "small")
    monkeypatch.setenv("FLOW_RESOURCE_GUARD_RECHECK_DELAY_SEC", "0.01")
    memory_checks = iter([True, False, False])
    monkeypatch.setattr(resource_guard, "process_memory_high", lambda _reserve_gb: next(memory_checks))
    monkeypatch.setattr(resource_guard, "process_cpu_snapshot", lambda guard_cores=None: _cpu_ok())

    app = FastAPI()

    @app.get("/api/dashboard/heavy")
    def dashboard_heavy():
        return {"ok": True}

    app.add_middleware(resource_guard.ResourceGuardMiddleware)
    client = TestClient(app)

    response = client.get("/api/dashboard/heavy")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
