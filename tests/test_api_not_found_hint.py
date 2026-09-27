"""미등록 /api 경로의 404 응답 — 서버 기동 뒤에 설치된 라우터면 재시작을 안내한다."""
import os
import sys

import app  # noqa: F401 — top-level shim 이 backend/app.py 를 _flow_backend_app 으로 로드한다

app_module = sys.modules["_flow_backend_app"]


def test_router_changed_after_start_asks_for_restart(monkeypatch):
    monkeypatch.setattr(app_module, "_ROUTERS_LOADED_AT", 0.0)
    body = app_module._stale_router_body("analysis_requests", "/api/analysis-requests/x")
    assert body["error_code"] == "server_restart_required"
    assert "재시작" in body["detail"]
    assert body["router"] == "analysis_requests"


def test_router_older_than_start_keeps_plain_not_found(monkeypatch):
    path = app_module.ROUTERS_DIR / "analysis_requests.py"
    monkeypatch.setattr(app_module, "_ROUTERS_LOADED_AT", os.path.getmtime(path) + 1)
    assert app_module._stale_router_body("analysis_requests", "/api/analysis-requests/x") is None


def test_unknown_or_unsafe_router_key_is_ignored(monkeypatch):
    monkeypatch.setattr(app_module, "_ROUTERS_LOADED_AT", 0.0)
    assert app_module._stale_router_body("no_such_router_xyz", "/api/no-such") is None
    assert app_module._stale_router_body("..", "/api/..") is None
    assert app_module._stale_router_body("", "/api/") is None
