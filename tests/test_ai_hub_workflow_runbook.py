from __future__ import annotations

import sys
from pathlib import Path

_FLOW_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _FLOW_ROOT / "backend"
for p in (_BACKEND, _FLOW_ROOT):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)


def test_ai_hub_workflow_runbook_builds_operator_rows(monkeypatch):
    from core import ai_hub_workflow_runbook

    monkeypatch.setattr(ai_hub_workflow_runbook.ai_hub_workflow_map, "build_workflow_map", lambda **kwargs: {
        "ok": True,
        "top_tags": [{"tag": "knob", "count": 2}],
        "warnings": [],
        "nodes": [
            {
                "id": "workflow:ready_knob",
                "type": "workflow",
                "label": "KNOB lot_wf 확인",
                "workflow_key": "ready_knob",
                "owner": "alice",
                "shared": True,
                "detail": "trigger intent=knob_analysis\ncontains=knob\nslots=product,knobs\nsteps:\n1. splittable.knob_impact",
                "metrics": {"steps": 1, "run_count": 2, "warning_count": 0, "last_run": "2099-01-01T00:00:00+00:00", "last_status": "dry_run:1"},
                "steps": [{"index": 0, "unit_ai": "splittable", "action": "knob_impact", "bind_slots": ["product", "knobs"]}],
            },
            {
                "id": "tool:splittable",
                "type": "tool",
                "enabled": True,
                "tags": ["splittable", "knob"],
            },
            {
                "id": "workflow:blocked_lot",
                "type": "workflow",
                "label": "LOT step 확인",
                "workflow_key": "blocked_lot",
                "shared": False,
                "detail": "trigger intent=filebrowser_ai_sql\ncontains=step\nslots=product,root_lot_ids\nsteps:\n1. ghost.lookup",
                "metrics": {"steps": 1, "run_count": 0, "warning_count": 0},
                "steps": [{"index": 0, "unit_ai": "ghost", "action": "lookup", "bind_slots": ["product"]}],
            },
            {
                "id": "tool:ghost",
                "type": "tool",
                "enabled": False,
                "tags": ["workflow", "missing_tool"],
            },
        ],
        "edges": [
            {"from": "tool:splittable", "to": "wiki:knob_rule", "kind": "evidence", "label": "wiki"},
            {"from": "workflow:ready_knob", "to": "tool:splittable", "kind": "workflow_step", "label": "knob_impact"},
            {"from": "workflow:blocked_lot", "to": "tool:ghost", "kind": "workflow_step", "label": "lookup"},
        ],
    })

    out = ai_hub_workflow_runbook.build_runbook(username="alice", days=7, limit=10, focus_tag="knob")

    assert out["ok"] is True
    assert out["counts"]["workflows"] == 2
    assert out["counts"]["workflow_templates_total"] == 2
    assert out["counts"]["ready"] == 1
    assert out["counts"]["blocked"] == 1
    assert out["actions"] == []
    rows = {row["key"]: row for row in out["items"]}
    assert rows["ready_knob"]["status"] == "ready"
    assert rows["ready_knob"]["trigger_summary"]["intent"] == "knob_analysis"
    assert rows["ready_knob"]["evidence_node_ids"] == ["wiki:knob_rule"]
    assert rows["ready_knob"]["actions"][0]["endpoint"] == "/api/agent/workflows/execute"
    assert rows["blocked_lot"]["status"] == "blocked"
    assert rows["blocked_lot"]["missing_tools"] == ["ghost"]
    assert {issue["key"] for issue in rows["blocked_lot"]["issues"]} >= {"missing_tools", "not_checked", "no_evidence"}


def test_ai_hub_workflow_runbook_exposes_bootstrap_action_when_empty(monkeypatch):
    from core import ai_hub_workflow_runbook

    monkeypatch.setattr(ai_hub_workflow_runbook.ai_hub_workflow_map, "build_workflow_map", lambda **kwargs: {
        "ok": True,
        "counts": {"workflow_templates_total": 0},
        "top_tags": [],
        "warnings": [],
        "nodes": [],
        "edges": [],
    })

    out = ai_hub_workflow_runbook.build_runbook(username="alice", days=7, limit=10)

    assert out["counts"]["workflows"] == 0
    assert out["counts"]["workflow_templates_total"] == 0
    assert out["actions"][0]["id"] == "bootstrap_workflows"
    assert out["actions"][0]["endpoint"] == "/api/ai-hub/readiness/bootstrap-workflows"


def test_ai_hub_workflow_runbook_endpoint_passes_user(monkeypatch):
    from routers import ai_hub

    def fake_build_runbook(username="", days=30, limit=40, focus_tag=""):
        assert username == "alice"
        assert days == 7
        assert limit == 5
        assert focus_tag == "knob"
        return {"ok": True, "items": [], "counts": {"workflows": 0}}

    monkeypatch.setattr(ai_hub.ai_hub_workflow_runbook, "build_runbook", fake_build_runbook)

    out = ai_hub.workflow_runbook(_req(), days=7, limit=5, focus_tag="knob")

    assert out["ok"] is True
    assert out["is_admin"] is True


class _State:
    user = {"username": "alice", "role": "admin"}


class _Req:
    state = _State()
    headers = {}


def _req():
    return _Req()
