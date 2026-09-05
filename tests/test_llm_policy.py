import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from core import llm_adapter
from routers import llm, s3_ingest, template_report


def test_shared_daily_budget_never_exceeds_30_concurrent_attempts(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from core import llm_usage
    monkeypatch.delenv("FLOW_LLM_DAILY_CALL_LIMIT", raising=False)
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda _: llm_usage.reserve_attempt(), range(50)))
    assert outcomes.count("") == 30
    assert llm_usage.snapshot()["daily_calls_used"] == 30
    assert llm_usage.snapshot()["daily_calls_remaining"] == 0
    assert "limit reached" in llm_usage.reserve_attempt()
    # A new process reads the same state; there is no in-memory quota reset.
    assert llm_usage._read(llm_usage._path(), llm_usage._today())["used"] == 30


def test_daily_budget_resets_next_korean_day_and_fails_closed_if_corrupt(monkeypatch):
    from core import llm_usage
    monkeypatch.setattr(llm_usage, "_today", lambda: "2026-09-05")
    assert llm_usage.reserve_attempt() == ""
    monkeypatch.setattr(llm_usage, "_today", lambda: "2026-09-06")
    assert llm_usage.snapshot()["daily_calls_used"] == 0
    assert llm_usage.reserve_attempt() == ""
    assert llm_usage.snapshot()["daily_calls_used"] == 1
    llm_usage._path().write_text("{broken", encoding="utf-8")
    assert "unavailable" in llm_usage.reserve_attempt()
    assert llm_usage.snapshot()["daily_calls_remaining"] == 0


def test_exhausted_budget_blocks_admin_network_call(monkeypatch):
    from core import llm_usage
    monkeypatch.setattr(llm_adapter, "_raw_config", _connected_config)
    monkeypatch.setattr(llm_adapter, "should_attempt_llm", lambda: True)
    monkeypatch.setenv("FLOW_LLM_DAILY_CALL_LIMIT", "0")
    monkeypatch.setattr(llm_adapter.urllib.request, "urlopen", lambda *a, **k: pytest.fail("quota bypass"))
    with llm_adapter.request_execution_scope({"username": "admin", "role": "admin"}):
        result = llm_adapter.complete("chart")
    assert not result["ok"] and not result["meta"]["invoked"]
    assert "daily call limit" in result["error"]
    assert llm_usage.snapshot()["daily_calls_used"] == 0


def test_auth_middleware_binds_admin_scope_for_sync_endpoints(monkeypatch):
    import asyncio
    import anyio
    from starlette.responses import JSONResponse
    from app_v2.runtime import security
    monkeypatch.setattr(security, "validate_token", lambda token: {"username": token, "role": token} if token else None)
    middleware = security.AuthMiddleware(app=lambda *args: None)

    async def call_next(request):
        # Starlette sync routes run through AnyIO's worker-thread context copy.
        denied = await anyio.to_thread.run_sync(lambda: bool(llm_adapter._execution_denial()))
        return JSONResponse({"denied": denied})

    async def run():
        for role in ("admin", "user", ""):
            request = Request({"type": "http", "method": "GET", "path": "/api/policy-probe",
                               "headers": [(b"x-session-token", role.encode())]})
            response = await middleware.dispatch(request, call_next)
            if role:
                assert json.loads(response.body) == {"denied": role != "admin"}
            else:
                assert response.status_code == 401
            assert llm_adapter._execution_denial()
    asyncio.run(run())


def _request(user: dict, path: str = "/api/llm/error/explain") -> Request:
    request = Request({"type": "http", "method": "POST", "path": path, "headers": []})
    request.state.user = user
    return request


def _connected_config() -> dict:
    return {
        "enabled": True,
        "api_url": "http://internal-llm.example/v1/chat/completions",
        "model": "internal-test-model",
        "provider": "local",
        "auth_mode": "none",
        "format": "openai",
        "headers": {},
        "extra_body": {},
        "timeout_s": 5,
    }


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int) -> bytes:
        return json.dumps({"choices": [{"message": {"content": "allowed"}}]}).encode()


def test_llm_adapter_denies_non_admin_before_network(monkeypatch):
    calls = []
    monkeypatch.setattr(llm_adapter, "_raw_config", _connected_config)
    monkeypatch.setattr(llm_adapter.urllib.request, "urlopen", lambda *_args, **_kwargs: calls.append(True))

    with llm_adapter.request_execution_scope({"username": "engineer", "role": "user"}):
        result = llm_adapter.complete("make a chart")

    assert result["ok"] is False
    assert result["meta"]["invoked"] is False
    assert "admin-only" in result["error"]
    assert calls == []


def test_llm_adapter_allows_connected_provider_for_admin_request(monkeypatch):
    calls = []
    monkeypatch.setattr(llm_adapter, "_raw_config", _connected_config)

    def urlopen(*_args, **_kwargs):
        calls.append(True)
        return _Response()

    monkeypatch.setattr(llm_adapter.urllib.request, "urlopen", urlopen)
    llm_adapter.reset_llm_health()
    with llm_adapter.request_execution_scope({"username": "poc-admin", "role": "admin"}):
        result = llm_adapter.complete("make a chart")

    assert result["ok"] is True
    assert result["text"] == "allowed"
    assert calls == [True]


def test_error_explain_endpoint_never_calls_llm(monkeypatch):
    monkeypatch.setattr(
        llm_adapter,
        "complete_json",
        lambda *_args, **_kwargs: pytest.fail("runtime error explanation called the LLM"),
    )
    result = llm.explain_error(
        llm.ErrorExplainReq(status=500, method="GET", url="/api/example", raw_error="database unavailable"),
        _request({"username": "poc-admin", "role": "admin"}),
    )

    assert result["disabled"] is True
    assert result["reason"] == "error_explanation_disabled"
    assert result["llm"] == {"available": False, "used": False}
    assert result["message"] == "database unavailable"


def test_s3_runtime_failure_never_calls_llm():
    assert s3_ingest._explain_s3_failure(
        action="sync",
        item_id="fab",
        target="FAB",
        kind="db",
        direction="download",
        reason="aws command failed",
        exit_code=1,
    ) is None


def test_template_assistant_rejects_non_admin_before_llm(monkeypatch):
    monkeypatch.setattr(
        llm_adapter,
        "complete_json",
        lambda *_args, **_kwargs: pytest.fail("non-admin template request called the LLM"),
    )

    with pytest.raises(HTTPException) as exc_info:
        template_report.template_assistant(
            template_report.TemplateAssistantReq(instruction="make a report", template_code=""),
            {"username": "engineer", "role": "user"},
        )

    assert exc_info.value.status_code == 403


def test_frontend_api_has_no_automatic_error_llm_request():
    source = (llm_adapter.PATHS.app_root / "frontend" / "src" / "lib" / "api.js").read_text("utf-8")

    assert "/api/llm/error/explain" not in source
    assert "explainPromise" not in source
