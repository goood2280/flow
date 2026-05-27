from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core.flowi_units import inform_registration_runtime as runtime  # noqa: E402
from routers import agent, informs  # noqa: E402


class _State:
    def __init__(self, user: dict):
        self.user = user


class _Request:
    headers = {}
    method = "GET"
    query_params = {}

    def __init__(self, username: str = "tester", role: str = "admin"):
        self.state = _State({"username": username, "role": role})


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
        "slot_extract",
        "validate_missing",
        "snapshot_preview",
        "review",
        "register",
    ]
    assert graph_payload["state_design"]["slots"]["producer"] == "slot_extract"
    assert graph_payload["state_design"]["draft"]["producer"] == "review"
    for node in graph_payload["nodes"]:
        assert node["persona"]
        assert isinstance(node["state_io"]["reads"], list)
        assert isinstance(node["state_io"]["writes"], list)
        assert node["answer_attach_rule"]

    catalog = agent.unit_ai_catalog(_Request())
    assert [unit["key"] for unit in catalog["units"]] == ["filebrowser_ai_sql", "inform_registration"]

    graph = agent.unit_ai_runtime_graph("inform_registration", _Request())
    assert graph["ok"] is True
    assert graph["unit_ai"] == "inform_registration"
    assert graph["graph"]["nodes"][0]["id"] == "context_seed"


def test_agent_runtime_routes_are_before_archived_catchall():
    routes = [
        (getattr(route, "path", ""), getattr(route, "endpoint", None).__name__)
        for route in agent.router.routes
    ]
    catchall_idx = next(idx for idx, row in enumerate(routes) if row == ("/api/agent/{path:path}", "archived_agent_endpoint"))
    for path in (
        "/api/agent/unit-ai/inform_registration/runtime/graph",
        "/api/agent/unit-ai/{unit_key}/runtime/graph",
        "/api/agent/unit-ai/{unit_key}/runtime/run",
        "/api/agent/home-flowi/runtime/graph",
        "/api/agent/home-flowi/runtime/runs",
    ):
        idx = next(i for i, row in enumerate(routes) if row[0] == path)
        assert idx < catchall_idx


def test_archived_catchall_does_not_archive_active_inform_graph(monkeypatch):
    monkeypatch.setattr(agent, "current_user", lambda _request: {"username": "tester", "role": "admin"})

    graph = agent.archived_agent_endpoint("unit-ai/inform_registration/runtime/graph", _Request())
    assert graph["ok"] is True
    assert graph["unit_ai"] == "inform_registration"
    assert graph["graph"]["nodes"][0]["id"] == "context_seed"

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
    assert second["missing"] == []
    assert second["slots"]["product"] == "PRODA"
    assert second["slots"]["lot_id"] == "R1000"
    assert second["draft"]["inform"]["mail_draft"]["to_users"] == ["alice"]
    assert not informs_file.exists()

    history = agent.unit_ai_runtime_history("inform_registration", _Request())["history"]
    assert history[0]["session_id"] == first["session_id"]
    assert history[0]["status"] == "review"
    assert history[1]["status"] == "collecting"


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
