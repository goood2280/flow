from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core import auth as auth_core  # noqa: E402
from routers import dashboard, llm as llm_router  # noqa: E402


class _State:
    def __init__(self, user: dict):
        self.user = user


class _Request:
    headers = {}

    def __init__(self, username: str = "alice", role: str = "user"):
        self.state = _State({"username": username, "role": role})


ADMIN_ONLY_WRITE_ENDPOINTS = [
    ("POST", "/api/dashboard/chart-defaults", "require_admin"),
    ("POST", "/api/dashboard/refresh", "require_admin"),
    ("POST", "/api/dashboard/charts/save", "require_admin"),
    ("POST", "/api/dashboard/charts/delete", "require_admin"),
    ("POST", "/api/dashboard/charts/copy", "require_admin"),
    ("POST", "/api/llm/flowi/admin/update", "require_admin"),
    ("POST", "/api/llm/flowi/feedback/promote", "require_admin"),
    ("POST", "/api/llm/flowi/persona", "require_admin"),
    ("POST", "/api/llm/flowi/inform/walkthrough/confirm", "require_admin"),
    ("POST", "/api/informs/config", "page_admin:informs"),
    ("POST", "/api/informs/modules/knob-map", "page_admin:informs"),
    ("POST", "/api/splittable/source-config/save", "page_admin:splittable"),
    ("POST", "/api/splittable/rulebook/save", "page_admin:splittable"),
    ("POST", "/api/splittable/rulebook/schema/save", "page_admin:splittable"),
    ("POST", "/api/splittable/prefixes/save", "page_admin:splittable"),
    ("POST", "/api/splittable/precision/save", "page_admin:splittable"),
    ("POST", "/api/agent/admin-tools/matching/suggest", "require_admin"),
    ("POST", "/api/agent/admin-tools/matching/apply", "require_admin"),
    ("POST", "/api/agent/admin-tools/rulebook/suggest", "require_admin"),
    ("POST", "/api/agent/admin-tools/rulebook/apply", "require_admin"),
    ("POST", "/api/agent/admin-tools/knowledge/ingest", "require_admin"),
]


def _assert_denied(dep, request: _Request) -> None:
    with pytest.raises(HTTPException) as exc:
        dep(request)
    assert exc.value.status_code in {401, 403}


def test_regular_user_denied_for_admin_only_write_matrix(monkeypatch):
    monkeypatch.setattr(auth_core, "get_page_admins", lambda: {})
    request = _Request("alice", "user")

    for _method, _path, gate in ADMIN_ONLY_WRITE_ENDPOINTS:
        dep = auth_core.require_admin if gate == "require_admin" else auth_core.require_page_admin(gate.split(":", 1)[1])
        _assert_denied(dep, request)

    with pytest.raises(HTTPException) as exc:
        dashboard.post_chart_defaults(
            dashboard.ChartDefaultReq(chart_type="scatter", config={"x": "$item1"}),
            request,
        )
    assert exc.value.status_code == 403


def test_page_admin_delegation_is_page_scoped(monkeypatch):
    monkeypatch.setattr(auth_core, "get_page_admins", lambda: {"tablemap": ["alice"]})
    request = _Request("alice", "user")

    assert auth_core.require_page_admin("tablemap")(request)["username"] == "alice"

    with pytest.raises(HTTPException) as exc:
        auth_core.require_page_admin("splittable")(request)
    assert exc.value.status_code == 403


def test_global_admin_passes_page_admin_dependency(monkeypatch):
    monkeypatch.setattr(auth_core, "get_page_admins", lambda: {})
    request = _Request("root", "admin")

    assert auth_core.require_page_admin("splittable")(request)["username"] == "root"


def test_flowi_chat_blocks_admin_function_prompts_for_regular_user(monkeypatch):
    monkeypatch.setattr(llm_router, "_append_user_event", lambda *_args, **_kwargs: None)

    result = llm_router._run_flowi_chat(
        prompt="매칭테이블을 변경하고 users 삭제해줘",
        product="",
        max_rows=12,
        me={"username": "alice", "role": "user"},
    )
    public = llm_router._flowi_home_response_for_role(result, {"username": "alice", "role": "user"})

    assert public["blocked"] is True
    assert public["reject_reason"] == "이 작업은 권한이 필요해요. 관리자에게 요청해 주세요."
    assert "missing" not in public
    assert "arguments_choices" not in public
    assert (public.get("tool") or {}).get("blocked") is True
    assert "missing" not in (public.get("tool") or {})
    assert "arguments_choices" not in (public.get("tool") or {})
