import asyncio

from fastapi import Request
from fastapi.responses import JSONResponse

from app_v2.runtime import resource_guard
from core import upstream_proxy, worker_dispatch


def _request(path: str, method: str = "GET") -> Request:
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": [],
        "client": ("test", 1234),
        "server": ("test", 80),
    })


def test_browser_unit_ai_posts_are_owned_by_operating_api(monkeypatch):
    monkeypatch.setenv("FLOW_API_SERVER_URL", "http://operating-api:8080")
    monkeypatch.setattr(worker_dispatch, "server_role", lambda: "worker")

    assert upstream_proxy.requires_operating(
        "/api/filebrowser/sql/llm/draft", "POST", {}
    ) is True
    assert upstream_proxy.should_proxy(
        "/api/filebrowser/chart-builder/assistant", "POST", {}
    ) is True
    assert upstream_proxy.should_proxy(
        "/api/agent/unit-ai/filebrowser_ai_sql/runtime/run", "POST", {}
    ) is True
    assert upstream_proxy.should_proxy(
        "/api/filebrowser/chart-builder/run", "POST", {}
    ) is False


def test_operating_proxy_loop_guard_prevents_a_second_hop(monkeypatch):
    monkeypatch.setenv("FLOW_API_SERVER_URL", "http://operating-api:8080")
    monkeypatch.setattr(worker_dispatch, "server_role", lambda: "worker")
    headers = {upstream_proxy.LOOP_GUARD_HEADER: "1"}

    assert upstream_proxy.requires_operating(
        "/api/filebrowser/sql/llm/draft", "POST", headers
    ) is False
    assert upstream_proxy.should_proxy(
        "/api/filebrowser/sql/llm/draft", "POST", headers
    ) is False


def test_worker_splittable_read_prefers_proxy_but_allows_local_fallback(monkeypatch):
    monkeypatch.setenv("FLOW_API_SERVER_URL", "http://operating-api:8080")
    monkeypatch.setattr(worker_dispatch, "server_role", lambda: "worker")

    assert upstream_proxy.should_proxy(
        "/api/splittable/view", "GET", {}
    ) is True
    assert upstream_proxy.requires_operating(
        "/api/splittable/view", "GET", {}
    ) is False


def test_worker_splittable_read_runs_locally_without_operating_url(monkeypatch):
    monkeypatch.delenv("FLOW_API_SERVER_URL", raising=False)
    monkeypatch.setattr(worker_dispatch, "server_role", lambda: "worker")

    assert upstream_proxy.should_proxy(
        "/api/splittable/lot-candidates", "GET", {}
    ) is False
    assert upstream_proxy.requires_operating(
        "/api/splittable/lot-candidates", "GET", {}
    ) is False


def test_resource_guard_continues_to_local_splittable_after_proxy_failure(monkeypatch):
    monkeypatch.setenv("FLOW_API_SERVER_URL", "http://operating-api:8080")
    monkeypatch.setattr(worker_dispatch, "server_role", lambda: "worker")

    async def app(_scope, _receive, _send):
        return None

    async def proxy_unavailable(_request):
        return None

    called = []

    async def call_next(request):
        called.append(request.url.path)
        return JSONResponse({"ok": True, "source": "development_worker"})

    monkeypatch.setattr(resource_guard, "_try_upstream_proxy", proxy_unavailable)
    middleware = resource_guard.ResourceGuardMiddleware(app)
    response = asyncio.run(middleware.dispatch(
        _request("/api/splittable/view"), call_next))

    assert response.status_code == 200
    assert called == ["/api/splittable/view"]
