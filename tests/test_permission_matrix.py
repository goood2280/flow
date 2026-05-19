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
from scripts import check_permission_matrix  # noqa: E402


class _State:
    def __init__(self, user: dict):
        self.user = user


class _Request:
    headers = {}

    def __init__(self, username: str = "alice", role: str = "user"):
        self.state = _State({"username": username, "role": role})


ADMIN_ONLY_WRITE_ENDPOINTS = [
    ("POST", "/api/dashboard/chart-defaults", "page_manager:dashboard"),
    ("POST", "/api/dashboard/refresh", "page_manager:dashboard"),
    ("POST", "/api/dashboard/charts/save", "page_manager:dashboard"),
    ("POST", "/api/dashboard/charts/delete", "page_manager:dashboard"),
    ("POST", "/api/dashboard/charts/copy", "page_manager:dashboard"),
    ("POST", "/api/llm/flowi/admin/update", "require_admin"),
    ("POST", "/api/llm/flowi/feedback/promote", "require_admin"),
    ("POST", "/api/llm/flowi/persona", "require_admin"),
    ("POST", "/api/llm/flowi/inform/walkthrough/confirm", "require_admin"),
    ("POST", "/api/informs/config", "page_manager:inform"),
    ("POST", "/api/informs/modules/knob-map", "page_manager:inform"),
    ("POST", "/api/splittable/source-config/save", "page_manager:splittable"),
    ("POST", "/api/splittable/rulebook/save", "page_manager:splittable"),
    ("POST", "/api/splittable/rulebook/schema/save", "page_manager:splittable"),
    ("POST", "/api/splittable/prefixes/save", "page_manager:splittable"),
    ("POST", "/api/splittable/precision/save", "page_manager:splittable"),
    ("POST", "/api/s3ingest/save", "require_admin"),
    ("POST", "/api/s3ingest/aws-config/save", "require_admin"),
    ("POST", "/api/catalog/matching/save", "page_manager:splittable"),
    ("POST", "/api/catalog/s3/config/save", "page_manager:filebrowser"),
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
        dep = auth_core.require_admin if gate == "require_admin" else auth_core.require_page_manager(gate.split(":", 1)[1])
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

    assert auth_core.require_page_manager("tablemap")(request)["username"] == "alice"
    assert auth_core.require_page_manager("dbmap")(request)["username"] == "alice"

    with pytest.raises(HTTPException) as exc:
        auth_core.require_page_manager("splittable")(request)
    assert exc.value.status_code == 403


def test_global_admin_passes_page_admin_dependency(monkeypatch):
    monkeypatch.setattr(auth_core, "get_page_admins", lambda: {})
    request = _Request("root", "admin")

    assert auth_core.require_page_manager("splittable")(request)["username"] == "root"


def test_page_admin_aliases_canonicalize(monkeypatch, tmp_path):
    settings = tmp_path / "admin_settings.json"
    settings.write_text(
        '{"page_admins":{"informs":["alice"],"meetings":["bob"],"wafer_map":["carol"],"dbmap":["dana"]}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(auth_core.PATHS, "data_root", tmp_path)

    assert auth_core.canonical_page_id("informs") == "inform"
    assert auth_core.get_page_admins() == {
        "inform": ["alice"],
        "meeting": ["bob"],
        "tablemap": ["dana"],
        "waferlayout": ["carol"],
    }


def test_shared_write_routes_are_not_session_middleware_only():
    rows = check_permission_matrix.backend_rows()
    sensitive_prefixes = (
        "/api/s3ingest/",
        "/api/catalog/",
        "/api/agent/wiki/",
        "/api/agent/schema",
        "/api/knowledge/",
    )
    sensitive_exact = {
        "/api/dashboard/chart-defaults",
        "/api/dashboard/refresh",
        "/api/dashboard/charts/save",
        "/api/dashboard/charts/delete",
        "/api/dashboard/charts/copy",
        "/api/calendar/categories/save",
        "/api/calendar/settings/save",
        "/api/tracker/categories/save",
        "/api/tracker/scheduler/save",
        "/api/tracker/scheduler/run-now",
        "/api/tracker/db-sources/save",
        "/api/tracker/et-lot-cache/refresh",
        "/api/splittable/source-config/save",
        "/api/splittable/rulebook/save",
        "/api/splittable/rulebook/schema/save",
        "/api/splittable/prefixes/save",
        "/api/splittable/precision/save",
        "/api/splittable/paste-sets/save",
        "/api/splittable/paste-sets/delete",
        "/api/splittable/paste-sets/to-custom",
        "/api/splittable/customs/save",
        "/api/splittable/customs/delete",
        "/api/informs/config",
        "/api/informs/settings",
        "/api/informs/modules/knob-map",
        "/api/informs/product-contacts",
        "/api/informs/product-contacts/update",
        "/api/informs/product-contacts/delete",
        "/api/informs/product-contacts/bulk-add",
    }
    leaks = []
    for row in rows:
        if row["method"] not in {"POST", "PUT", "PATCH", "DELETE"}:
            continue
        endpoint = row["endpoint"]
        sensitive = endpoint in sensitive_exact or any(endpoint.startswith(prefix) for prefix in sensitive_prefixes)
        if sensitive and row["backend_gate"] == "session_middleware":
            leaks.append(f"{row['method']} {endpoint}")
    assert leaks == []


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
