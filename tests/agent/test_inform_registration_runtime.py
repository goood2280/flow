from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.routing import Match

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core.flowi_units import inform_registration_runtime as runtime  # noqa: E402
from core import fab_reference  # noqa: E402
from routers import agent, informs  # noqa: E402


def _first_matching_endpoint(routes, path: str, method: str = "GET") -> str:
    scope = {
        "type": "http",
        "path": path,
        "method": method,
        "root_path": "",
        "headers": [],
        "query_string": b"",
    }
    for route in routes:
        try:
            match, _child_scope = route.matches(scope)
        except Exception:
            continue
        if match is Match.FULL:
            return getattr(getattr(route, "endpoint", None), "__name__", "")
    return ""


class _State:
    def __init__(self, user: dict):
        self.user = user


class _Request:
    headers = {}
    query_params = {}

    def __init__(self, username: str = "tester", role: str = "admin", method: str = "GET", json_body: dict | None = None):
        self.state = _State({"username": username, "role": role})
        self.method = method
        self._json = json_body or {}


class _DummyPaths:
    def __init__(self, root: Path):
        self.data_root = root / "flow-data"
        self.data_root.mkdir(parents=True, exist_ok=True)


def _install_inform_fixture(monkeypatch, tmp_path: Path) -> Path:
    paths = _DummyPaths(tmp_path)
    informs_file = paths.data_root / "informs" / "informs.json"
    monkeypatch.setattr(runtime, "PATHS", paths)
    monkeypatch.setattr(informs, "INFORMS_FILE", informs_file)
    monkeypatch.setattr(informs, "_INFORMS_CACHE_SIG", None)
    monkeypatch.setattr(informs, "_INFORMS_CACHE_ITEMS", None)
    monkeypatch.setattr(agent, "current_user", lambda _request: {"username": "tester", "role": "admin"})
    monkeypatch.setattr(informs, "current_user", lambda _request: {"username": "tester", "role": "admin"})
    monkeypatch.setattr(informs, "_audit_record", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(informs, "_resolve_fab_lot_snapshot", lambda *_args, **_kwargs: "")
    return informs_file


def _run(payload: dict, request: _Request | None = None) -> dict:
    return agent.unit_ai_runtime_run(
        "inform_registration",
        agent.UnitAiRuntimeRunReq(**payload),
        request or _Request(),
    )


def test_inform_registration_graph_shape_and_catalog(monkeypatch):
    monkeypatch.setattr(agent, "current_user", lambda _request: {"username": "tester", "role": "admin"})

    graph_payload = runtime.inform_registration_graph()
    assert [node["id"] for node in graph_payload["nodes"]] == [
        "context_seed",
        "semantic_layer",
        "slot_extract",
        "validate_missing",
        "snapshot_preview",
        "review",
        "human_review",
        "register",
    ]
    assert graph_payload["state_design"]["semantic_frame"]["producer"] == "semantic_layer"
    assert graph_payload["state_design"]["slots"]["producer"] == "slot_extract"
    assert graph_payload["state_design"]["draft"]["producer"] == "review"
    assert graph_payload["state_design"]["human_review"]["producer"] == "human_review"
    for node in graph_payload["nodes"]:
        assert node["persona"]
        assert isinstance(node["state_io"]["reads"], list)
        assert isinstance(node["state_io"]["writes"], list)
        assert node["answer_attach_rule"]

    catalog = agent.unit_ai_catalog(_Request())
    assert [unit["key"] for unit in catalog["units"]] == [
        "filebrowser_ai_sql",
        "inform_registration",
        "change_management",
        "dashboard_agent",
        "step_lookup",
        "ppid_knob",
    ]

    status = agent.agent_reset_status()
    assert status["ok"] is True
    assert status["status"] == "active_unit_ai"
    assert status["legacy_agent_studio"]["status"] == "archived_for_rebuild"
    assert status["unit_ai_endpoint"] == "/api/agent/unit-ai/catalog"
    assert status["unit_endpoint"] == "/api/agent/catalog"
    assert status["active_unit_endpoints"]["inform_registration"]["graph"] == "/api/agent/unit-ai/inform_registration/runtime/graph"
    assert status["active_unit_endpoints_v2"]["inform_registration"]["graph"] == "/api/agent/unit/inform_registration/graph"
    assert status["active_unit_endpoints"]["change_management"]["graph"] == "/api/agent/unit-ai/change_management/runtime/graph"
    assert status["active_unit_endpoints"]["dashboard_agent"]["graph"] == "/api/agent/unit-ai/dashboard_agent/runtime/graph"
    assert status["active_unit_endpoints"]["dashboard_agent"]["history"] == "/api/agent/unit-ai/dashboard_agent/runtime/history"
    assert status["active_unit_endpoints"]["step_lookup"]["graph"] == "/api/agent/unit-ai/step_lookup/runtime/graph"
    assert status["active_unit_endpoints"]["ppid_knob"]["run"] == "/api/agent/unit-ai/ppid_knob/runtime/run"
    assert "backend_version" in status
    assert "backend_commit" in status

    graph = agent.unit_ai_runtime_graph("inform_registration", _Request())
    assert graph["ok"] is True
    assert graph["unit_ai"] == "inform_registration"
    assert graph["graph"]["nodes"][0]["id"] == "context_seed"
    assert graph["graph"]["nodes"][1]["id"] == "semantic_layer"
    graph_v2 = agent.unit_runtime_graph("inform_registration", _Request())
    assert graph_v2["graph"]["nodes"][1]["id"] == "semantic_layer"


def test_agent_runtime_routes_are_before_archived_catchall():
    routes = [
        (getattr(route, "path", ""), getattr(route, "endpoint", None).__name__)
        for route in agent.router.routes
    ]
    catchall_idx = next(idx for idx, row in enumerate(routes) if row == ("/api/agent/{path:path}", "archived_agent_endpoint"))
    for path in (
        "/api/agent/unit-ai/inform_registration/runtime/graph",
        "/api/agent/unit-ai/change_management/runtime/graph",
        "/api/agent/unit-ai/{unit_key}/runtime/graph",
        "/api/agent/unit-ai/{unit_key}/runtime/run",
        "/api/agent/unit-ai/{unit_key}/runtime/history",
        "/api/agent/unit-ai/{unit_key}/feedback-profile",
        "/api/agent/unit-ai/{unit_key}/feedback",
        "/api/agent/catalog",
        "/api/agent/unit/{unit_key}/graph",
        "/api/agent/unit/{unit_key}/run",
        "/api/agent/unit/{unit_key}/history",
        "/api/agent/home-flowi/runtime/graph",
        "/api/agent/home-flowi/runtime/runs",
        "/api/agent/semantic/lexicon",
        "/api/agent/semantic/sources",
        "/api/agent/semantic/proposals",
    ):
        idx = next(i for i, row in enumerate(routes) if row[0] == path)
        assert idx < catchall_idx


def test_mounted_app_dispatches_active_agent_get_routes_before_archived_catchall():
    flow_app = importlib.import_module("app").app

    expected = {
        "/api/agent/unit-ai/inform_registration/runtime/graph": "inform_registration_runtime_graph",
        "/api/agent/unit-ai/inform_registration/runtime/history": "inform_registration_runtime_history",
        "/api/agent/unit-ai/change_management/runtime/graph": "change_management_runtime_graph",
        "/api/agent/unit-ai/change_management/runtime/history": "change_management_runtime_history",
        "/api/agent/unit-ai/dashboard_agent/runtime/graph": "unit_ai_runtime_graph",
        "/api/agent/unit-ai/dashboard_agent/runtime/history": "unit_ai_runtime_history",
        "/api/agent/unit-ai/inform_registration/feedback-profile": "unit_ai_feedback_profile",
        "/api/agent/catalog": "agent_unit_catalog",
        "/api/agent/unit/inform_registration/graph": "unit_runtime_graph",
        "/api/agent/unit/inform_registration/history": "unit_runtime_history",
        "/api/agent/unit/dashboard_agent/graph": "unit_runtime_graph",
        "/api/agent/unit/dashboard_agent/history": "unit_runtime_history",
        "/api/agent/unit/step_lookup/graph": "unit_runtime_graph",
        "/api/agent/unit/step_lookup/history": "unit_runtime_history",
        "/api/agent/unit/ppid_knob/graph": "unit_runtime_graph",
        "/api/agent/unit/ppid_knob/history": "unit_runtime_history",
        "/api/agent/home-flowi/runtime/graph": "home_flowi_runtime_graph",
        "/api/agent/semantic/lexicon": "semantic_lexicon",
        "/api/agent/semantic/sources": "semantic_sources",
    }
    for path, endpoint in expected.items():
        assert _first_matching_endpoint(flow_app.routes, path) == endpoint
    expected_post = {
        "/api/agent/unit-ai/dashboard_agent/runtime/run": "unit_ai_runtime_run",
        "/api/agent/unit/dashboard_agent/run": "unit_runtime_run",
        "/api/agent/unit/step_lookup/run": "unit_runtime_run",
        "/api/agent/unit-ai/ppid_knob/runtime/run": "unit_ai_runtime_run",
    }
    for path, endpoint in expected_post.items():
        assert _first_matching_endpoint(flow_app.routes, path, method="POST") == endpoint


def test_archived_catchall_does_not_archive_active_unit_graph_and_history(monkeypatch):
    monkeypatch.setattr(agent, "current_user", lambda _request: {"username": "tester", "role": "admin"})
    monkeypatch.setattr(fab_reference, "_read_rows", lambda _filename: [
        {"product": "PRODA", "step_id": "AA100090", "function_step": "SD_EPI"},
    ])

    graph = agent.archived_agent_endpoint("unit-ai/inform_registration/runtime/graph", _Request())
    assert graph["ok"] is True
    assert graph["unit_ai"] == "inform_registration"
    assert graph["graph"]["nodes"][0]["id"] == "context_seed"
    graph_v2 = agent.archived_agent_endpoint("unit/inform_registration/graph", _Request())
    assert graph_v2["graph"]["nodes"][0]["id"] == "context_seed"
    dashboard_graph = agent.archived_agent_endpoint("unit-ai/dashboard_agent/runtime/graph", _Request())
    assert dashboard_graph["unit_ai"] == "dashboard_agent"
    assert dashboard_graph["graph"]["nodes"][0]["id"] == "data_context"
    dashboard_history = agent.archived_agent_endpoint("unit-ai/dashboard_agent/runtime/history", _Request())
    assert dashboard_history["ok"] is True
    assert dashboard_history["unit_ai"] == "dashboard_agent"
    dashboard_graph_v2 = agent.archived_agent_endpoint("unit/dashboard_agent/graph", _Request())
    assert dashboard_graph_v2["graph"]["nodes"][0]["id"] == "data_context"
    dashboard_history_v2 = agent.archived_agent_endpoint("unit/dashboard_agent/history", _Request())
    assert dashboard_history_v2["unit_ai"] == "dashboard_agent"
    step_graph = agent.archived_agent_endpoint("unit/step_lookup/graph", _Request())
    assert step_graph["unit_ai"] == "step_lookup"
    assert [node["id"] for node in step_graph["graph"]["nodes"]] == [
        "prompt_input",
        "semantic_parse",
        "lookup_execute",
        "answer_render",
    ]
    ppid_graph = agent.archived_agent_endpoint("unit-ai/ppid_knob/runtime/graph", _Request())
    assert ppid_graph["unit_ai"] == "ppid_knob"

    post_out = agent.archived_agent_endpoint(
        "unit/step_lookup/run",
        _Request(method="POST", json_body={"prompt": "AA100090는 무슨 step이야"}),
    )
    assert post_out["unit_ai"] == "step_lookup"
    assert post_out["trace"][0]["node_id"] == "prompt_input"

    with pytest.raises(HTTPException) as excinfo:
        agent.archived_agent_endpoint("runtime", _Request())
    assert excinfo.value.status_code == 410


def test_inform_registration_missing_slots_followup_and_history(monkeypatch, tmp_path):
    informs_file = _install_inform_fixture(monkeypatch, tmp_path)

    first = _run({"prompt": "product PRODA lot R1000"})
    assert first["status"] == "collecting"
    assert first["session_id"]
    assert {"module", "note", "mail_target"}.issubset(set(first["missing"]))
    assert first["question"]
    assert not informs_file.exists()

    second = _run({
        "session_id": first["session_id"],
        "prompt": "",
        "slot_overrides": {
            "module": "GATE",
            "note": "IOFF drift review",
            "mail_draft": {"to_users": ["alice"], "groups": ["Process Owners"]},
        },
    })
    assert second["session_id"] == first["session_id"]
    assert second["status"] == "review"
    assert second["requires_confirmation"] is True
    assert second["human_review"]["approval_status"] == "pending"
    assert second["human_review"]["action_required"] is True
    assert second["human_review"]["can_confirm"] is True
    assert "human_review" in [row["node_id"] for row in second["trace"]]
    assert second["missing"] == []
    assert second["slots"]["product"] == "PRODA"
    assert second["slots"]["lot_id"] == "R1000"
    assert second["draft"]["inform"]["mail_draft"]["to_users"] == ["alice"]
    assert not informs_file.exists()

    history = agent.unit_ai_runtime_history("inform_registration", _Request())["history"]
    assert history[0]["session_id"] == first["session_id"]
    assert history[0]["status"] == "review"
    assert history[0]["human_review"]["approval_status"] == "pending"
    assert history[1]["status"] == "collecting"


def test_inform_registration_semantic_layer_precedes_slot_extract_and_overrides_win(monkeypatch, tmp_path):
    _install_inform_fixture(monkeypatch, tmp_path)

    out = _run({
        "prompt": "product PRODA lot R1000 module GATE note IOFF drift to alice@example.test",
        "slot_overrides": {"product": "PRODB"},
    })

    trace_ids = [row["node_id"] for row in out["trace"]]
    assert trace_ids[:3] == ["context_seed", "semantic_layer", "slot_extract"]
    assert out["semantic_frame"]["alias_hits"]
    assert out["trace"][1]["output"]["slot_hints"]["product"] == "PRODA"
    assert out["slots"]["product"] == "PRODB"
    assert out["slots"]["lot_id"] == "R1000"


def test_inform_registration_confirm_writes_inform_and_mail_draft(monkeypatch, tmp_path):
    informs_file = _install_inform_fixture(monkeypatch, tmp_path)

    ready = _run({
        "prompt": "",
        "slot_overrides": {
            "product": "PRODA",
            "lot_id": "R1000",
            "module": "GATE",
            "note": "Gate split check",
            "to": ["direct@example.test"],
            "to_users": ["alice"],
            "groups": ["Process Owners"],
            "extra_emails": ["vendor@example.test"],
        },
    })
    assert ready["status"] == "review"
    assert ready["requires_confirmation"] is True
    assert not informs_file.exists()

    confirmed = _run({
        "session_id": ready["session_id"],
        "action": "confirm",
        "prompt": "",
    })
    assert confirmed["ok"] is True
    assert confirmed["status"] == "registered"
    assert confirmed["requires_confirmation"] is False
    assert confirmed["human_review"]["approval_status"] == "approved"
    assert confirmed["human_review"]["approved_by"] == "tester"
    assert confirmed["graph"]["nodes"][6]["id"] == "human_review"
    created = confirmed["created_inform"]
    assert created["lot_id"] == "R1000"
    assert created["product"] == "PRODA"
    assert created["mail_draft"]["to"] == ["direct@example.test"]
    assert created["mail_draft"]["to_users"] == ["alice"]
    assert created["mail_draft"]["groups"] == ["Process Owners"]
    assert created["mail_draft"]["extra_emails"] == ["vendor@example.test"]

    saved = json.loads(informs_file.read_text("utf-8"))
    assert len(saved) == 1
    assert saved[0]["id"] == created["id"]
    assert saved[0]["mail_draft"]["to_users"] == ["alice"]


def test_inform_registration_snapshot_required_only_when_requested(monkeypatch, tmp_path):
    informs_file = _install_inform_fixture(monkeypatch, tmp_path)
    calls = []

    def fake_snapshot(req):
        calls.append(req)
        return {
            "source": f"SplitTable/{req.product}",
            "columns": ["parameter", "#1"],
            "rows": [["KNOB_GATE", "A"]],
            "st_view": {"headers": ["#1"], "rows": [{"_param": "KNOB_GATE", "_cells": {"0": {"actual": "A"}}}]},
            "st_scope": {"custom_cols": req.custom_cols},
        }

    monkeypatch.setattr(informs, "_build_splittable_snapshot_embed", fake_snapshot)

    waiting = _run({
        "prompt": "knob snapshot도 붙여줘",
        "slot_overrides": {
            "product": "PRODA",
            "lot_id": "R1000",
            "module": "GATE",
            "note": "Need knob evidence",
            "to_users": ["alice"],
        },
    })
    assert waiting["status"] == "collecting"
    assert "snapshot_custom_cols" in waiting["missing"]
    assert calls == []
    assert not informs_file.exists()

    ready = _run({
        "session_id": waiting["session_id"],
        "prompt": "",
        "slot_overrides": {"snapshot_custom_cols": ["KNOB_GATE"]},
    })
    assert ready["status"] == "review"
    assert ready["draft"]["embed_table"]["source"] == "SplitTable/PRODA"
    assert calls[-1].custom_cols == ["KNOB_GATE"]
    assert not informs_file.exists()

    confirmed = _run({
        "session_id": ready["session_id"],
        "action": "confirm",
        "prompt": "",
    })
    assert confirmed["status"] == "registered"
    saved = json.loads(informs_file.read_text("utf-8"))
    assert saved[0]["embed_table"]["source"] == "SplitTable/PRODA"
    assert saved[0]["embed_table"]["st_scope"]["custom_cols"] == ["KNOB_GATE"]


def test_inform_registration_without_snapshot_request_skips_embed(monkeypatch, tmp_path):
    _install_inform_fixture(monkeypatch, tmp_path)

    out = _run({
        "prompt": "",
        "slot_overrides": {
            "product": "PRODA",
            "lot_id": "R1000",
            "module": "GATE",
            "note": "Plain inform",
            "to_users": ["alice"],
        },
    })
    assert out["status"] == "review"
    assert out["missing"] == []
    assert out["draft"]["snapshot"]["requested"] is False
    assert out["draft"]["embed_table"] is None


def test_inform_registration_cancel_records_human_review_cancel(monkeypatch, tmp_path):
    _install_inform_fixture(monkeypatch, tmp_path)

    ready = _run({
        "prompt": "",
        "slot_overrides": {
            "product": "PRODA",
            "lot_id": "R1000",
            "module": "GATE",
            "note": "Cancel this draft",
            "to_users": ["alice"],
        },
    })
    cancelled = _run({
        "session_id": ready["session_id"],
        "action": "cancel",
        "prompt": "",
    })

    assert cancelled["status"] == "cancelled"
    assert cancelled["human_review"]["approval_status"] == "cancelled"
    assert cancelled["graph"]["nodes"][6]["status"] == "cancelled"
    assert cancelled["graph"]["nodes"][7]["status"] == "skipped"


def test_inform_registration_register_failure_keeps_human_approval(monkeypatch, tmp_path):
    _install_inform_fixture(monkeypatch, tmp_path)
    ready = _run({
        "prompt": "",
        "slot_overrides": {
            "product": "PRODA",
            "lot_id": "R1000",
            "module": "GATE",
            "note": "Will fail",
            "to_users": ["alice"],
        },
    })

    def boom(_draft, _request):
        raise RuntimeError("write failed")

    monkeypatch.setattr(runtime, "_register_inform", boom)
    failed = _run({
        "session_id": ready["session_id"],
        "action": "confirm",
        "prompt": "",
    })

    assert failed["ok"] is False
    assert failed["status"] == "blocked"
    assert failed["human_review"]["approval_status"] == "approved"
    assert failed["graph"]["nodes"][6]["status"] == "success"
    assert failed["graph"]["nodes"][7]["status"] == "failed"
    assert "write failed" in " ".join(failed["warnings"])
