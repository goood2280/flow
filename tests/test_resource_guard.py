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


def test_flowi_verify_and_workflow_catalog_are_default_light_paths(monkeypatch):
    monkeypatch.delenv("FLOW_LIGHT_API_PATHS", raising=False)

    paths = resource_guard._light_paths()

    assert "/api/llm/flowi/verify" in paths
    assert "/api/llm/flowi/workflows" in paths
    assert resource_guard._matches("/api/llm/flowi/verify", paths)
    assert resource_guard._matches("/api/llm/flowi/workflows", paths)
    assert not resource_guard._matches("/api/llm/flowi/chat", paths)


def test_flowi_verify_and_workflow_catalog_bypass_heavy_middleware(monkeypatch):
    monkeypatch.delenv("FLOW_LIGHT_API_PATHS", raising=False)
    monkeypatch.delenv("FLOW_HEAVY_API_PREFIXES", raising=False)
    monkeypatch.setattr(resource_guard, "process_memory_high", lambda _reserve_gb: False)

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
