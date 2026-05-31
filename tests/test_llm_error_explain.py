from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from routers import llm  # noqa: E402


def test_error_explain_route_exists():
    routes = {getattr(route, "path", ""): set(getattr(route, "methods", set()) or set()) for route in llm.router.routes}

    assert "POST" in routes.get("/api/llm/error/explain", set())


def test_error_explain_falls_back_to_raw_when_llm_unavailable(monkeypatch):
    monkeypatch.setattr(llm, "current_user", lambda _request: {"username": "viewer", "role": "user"})
    monkeypatch.setattr(llm.llm_adapter, "is_available", lambda: False)

    req = llm.ErrorExplainReq(
        status=500,
        method="GET",
        url="/api/informs/recent",
        page="/inform",
        raw_error="RuntimeError: DB root missing",
    )
    out = llm.explain_error(req, object())

    assert out["llm"] == {"available": False, "used": False}
    assert out["message"] == "RuntimeError: DB root missing"
    assert out["explanation"]["raw_error"] == "RuntimeError: DB root missing"
    assert "/api/informs/recent" in out["explanation"]["where"]


def test_error_explain_uses_llm_and_keeps_raw_error(monkeypatch):
    monkeypatch.setattr(llm, "current_user", lambda _request: {"username": "viewer", "role": "user"})
    monkeypatch.setattr(llm.llm_adapter, "is_available", lambda: True)

    def fake_complete_json(prompt, **kwargs):
        assert "/api/filebrowser/preview" in prompt
        assert "HTTP 403" in prompt
        return {
            "ok": True,
            "obj": {
                "summary": "권한이 없어 파일 미리보기를 열 수 없습니다.",
                "where": "FileBrowser 화면의 GET /api/filebrowser/preview 호출",
                "cause": "현재 사용자에게 FileBrowser read 권한이 없습니다.",
                "how_to_fix": ["로그인 계정을 확인하세요.", "관리자에게 FileBrowser 권한 부여를 요청하세요."],
            },
        }

    monkeypatch.setattr(llm.llm_adapter, "complete_json", fake_complete_json)

    req = llm.ErrorExplainReq(
        status=403,
        method="GET",
        url="/api/filebrowser/preview",
        page="/filebrowser",
        raw_error="HTTP 403: forbidden",
    )
    out = llm.explain_error(req, object())

    assert out["llm"]["used"] is True
    assert out["explanation"]["raw_error"] == "HTTP 403: forbidden"
    assert "AI 오류 해석" in out["message"]
    assert "발생 위치:" in out["message"]
    assert "해결 방법:" in out["message"]
    assert "원문 에러:\nHTTP 403: forbidden" in out["message"]


def test_error_explain_redacts_sensitive_tokens_before_prompt(monkeypatch):
    monkeypatch.setattr(llm, "current_user", lambda _request: {"username": "viewer", "role": "user"})
    monkeypatch.setattr(llm.llm_adapter, "is_available", lambda: True)
    seen = {}

    def fake_complete_json(prompt, **kwargs):
        seen["prompt"] = prompt
        return {
            "ok": True,
            "obj": {
                "summary": "인증 오류입니다.",
                "where": "API",
                "cause": "토큰이 거부되었습니다.",
                "how_to_fix": ["다시 로그인하세요."],
            },
        }

    monkeypatch.setattr(llm.llm_adapter, "complete_json", fake_complete_json)

    req = llm.ErrorExplainReq(
        status=401,
        method="POST",
        url="/api/admin/settings/save",
        page="/admin",
        raw_error="Authorization: Bearer secret-token\npassword: super-secret",
    )
    llm.explain_error(req, object())

    assert "secret-token" not in seen["prompt"]
    assert "super-secret" not in seen["prompt"]
    assert "<redacted>" in seen["prompt"]


def test_frontend_api_helper_requests_ai_explanation_and_preserves_raw_error():
    api = (ROOT / "frontend" / "src" / "lib" / "api.js").read_text(encoding="utf-8")

    assert 'fetch("/api/llm/error/explain"' in api
    assert "err.rawMessage = rawMessage" in api
    assert "data.llm.used" in api
    assert "if (body && body.error)" in api


def test_frontend_api_helper_skips_ai_explanation_for_resource_guard_errors():
    api = (ROOT / "frontend" / "src" / "lib" / "api.js").read_text(encoding="utf-8")

    assert "_RESOURCE_GUARD_ERROR_EXPLAIN_EXEMPT" in api
    assert '"resource_queue_timeout"' in api
    assert '"resource_memory_guard"' in api
    assert 'if (_RESOURCE_GUARD_ERROR_EXPLAIN_EXEMPT.has(String(body?.error_code || ""))) return null;' in api
