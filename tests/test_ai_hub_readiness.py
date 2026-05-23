from __future__ import annotations

import sys
from pathlib import Path

_FLOW_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _FLOW_ROOT / "backend"
for p in (_BACKEND, _FLOW_ROOT):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)


def test_ai_hub_readiness_builds_score_and_backlog(monkeypatch):
    from core import ai_hub_board, ai_hub_readiness, ai_hub_workflow_map
    from routers import ai_hub

    def fake_board(username="", days=30, limit=12):
        return {
            "ok": True,
            "counts": {
                "tools_total": 4,
                "tools_enabled": 3,
                "tools_disabled": 1,
                "semantic_proposals_pending": 1,
                "skill_candidates": 1,
                "skills": 0,
                "workflows": 0,
            },
            "lanes": [
                {"id": "disabled_tools", "items": [{"id": "filebrowser", "title": "FileBrowser", "detail": "disabled"}]},
                {"id": "semantic_proposals", "items": [{"id": "p1", "title": "루트랏", "detail": "alias"}]},
                {"id": "skill_candidates", "items": [{"id": "sk1", "title": "Lot review", "meta": "freq 4"}]},
            ],
        }

    def fake_map(username="", days=30, limit=120, reference_limit=400, focus_tag=""):
        assert username == "alice"
        return {
            "ok": True,
            "counts": {
                "tools_total": 4,
                "tools_visible": 4,
                "tools_disabled_visible": 1,
                "tools_without_refs_visible": 2,
            },
            "warnings": [
                {"key": "missing_evidence", "items": ["tool_a", "tool_b"]},
                {"key": "workflow_empty_templates", "items": ["empty_workflow"]},
                {"key": "workflow_incomplete_steps", "items": ["incomplete_step#1"]},
                {"key": "workflow_missing_tools", "items": ["ghost_unit"]},
            ],
        }

    monkeypatch.setattr(ai_hub_board, "build_board", fake_board)
    monkeypatch.setattr(ai_hub_workflow_map, "build_workflow_map", fake_map)

    out = ai_hub_readiness.build_readiness(username="alice", days=30)

    assert out["ok"] is True
    assert 0 <= out["score"] <= 100
    assert out["level"] in {"operational", "managed", "needs_attention", "bootstrap"}
    assert {check["key"] for check in out["checks"]} == {
        "tool_catalog",
        "knowledge_grounding",
        "learning_queue",
        "workflow_assets",
        "workflow_validation",
    }
    backlog_ids = {item["id"] for item in out["backlog"]}
    assert "disabled_tool:filebrowser" in backlog_ids
    assert "missing_evidence:tool_a" in backlog_ids
    assert "semantic_proposal:p1" in backlog_ids
    assert "skill_candidate:sk1" in backlog_ids
    assert "workflow_empty_templates:empty_workflow" in backlog_ids
    assert "workflow_incomplete_steps:incomplete_step#1" in backlog_ids
    assert "workflow_missing_tools:ghost_unit" in backlog_ids
    assert "workflow_assets:none" in backlog_ids
    assert "skills:none" in backlog_ids
    by_id = {item["id"]: item for item in out["backlog"]}
    assert by_id["disabled_tool:filebrowser"]["actions"][0]["endpoint"] == "/api/ai-hub/tools/filebrowser/toggle"
    assert {action["id"] for action in by_id["semantic_proposal:p1"]["actions"]} == {"approve", "reject"}
    assert {action["id"] for action in by_id["skill_candidate:sk1"]["actions"]} == {"approve", "reject"}
    assert by_id["workflow_assets:none"]["actions"][0]["endpoint"] == "/api/ai-hub/readiness/bootstrap-workflows"

    api_out = ai_hub.readiness(_req(), days=30)
    assert api_out["counts"]["tools_total"] == 4
    assert api_out["counts"]["workflow_validation_total"] == 0
    assert api_out["is_admin"] is True


def test_ai_hub_readiness_tracks_workflow_validation_backlog(monkeypatch):
    from core import ai_hub_board, ai_hub_readiness, ai_hub_workflow_map

    def fake_board(username="", days=30, limit=12):
        return {
            "ok": True,
            "counts": {
                "tools_total": 2,
                "tools_enabled": 2,
                "tools_disabled": 0,
                "semantic_proposals_pending": 0,
                "skill_candidates": 0,
                "skills": 1,
                "workflows": 2,
            },
            "lanes": [],
        }

    def fake_map(username="", days=30, limit=120, reference_limit=400, focus_tag=""):
        return {
            "ok": True,
            "counts": {
                "tools_total": 2,
                "tools_visible": 2,
                "tools_without_refs_visible": 0,
                "workflow_runs_recent": 1,
                "workflow_run_warnings": 1,
            },
            "nodes": [
                {
                    "id": "workflow:unchecked",
                    "type": "workflow",
                    "label": "검증 안 된 workflow",
                    "workflow_key": "unchecked",
                    "metrics": {"run_count": 0, "warning_count": 0},
                },
                {
                    "id": "workflow:warned",
                    "type": "workflow",
                    "label": "경고 workflow",
                    "workflow_key": "warned",
                    "metrics": {"run_count": 2, "warning_count": 1},
                },
            ],
            "warnings": [],
        }

    monkeypatch.setattr(ai_hub_board, "build_board", fake_board)
    monkeypatch.setattr(ai_hub_workflow_map, "build_workflow_map", fake_map)

    out = ai_hub_readiness.build_readiness(username="alice", days=30)

    assert out["counts"]["workflow_validation_total"] == 2
    assert out["counts"]["workflow_validation_checked"] == 1
    assert out["counts"]["workflow_validation_unverified"] == 1
    assert out["counts"]["workflow_validation_warnings"] == 1
    backlog_ids = {item["id"] for item in out["backlog"]}
    assert "workflow_unverified:unchecked" in backlog_ids
    assert "workflow_validation_warning:warned" in backlog_ids


def test_ai_hub_readiness_bootstrap_workflows_is_idempotent(tmp_path, monkeypatch):
    from core import ai_hub_readiness, flowi_workflow_templates
    from routers import ai_hub

    monkeypatch.setattr(flowi_workflow_templates, "_DIR", tmp_path / "workflows")

    first = ai_hub_readiness.bootstrap_starter_workflows(by="alice")
    assert first["created_count"] == len(ai_hub_readiness.STARTER_WORKFLOWS)
    assert first["preserved_count"] == 0
    keys = {row["key"] for row in flowi_workflow_templates.list_templates("", include_shared=True)}
    assert {"ops_lot_step_review", "ops_knob_lotwf_review", "ops_inform_draft_review"} <= keys

    second = ai_hub_readiness.bootstrap_starter_workflows(by="alice")
    assert second["created_count"] == 0
    assert second["preserved_count"] == len(ai_hub_readiness.STARTER_WORKFLOWS)

    api_out = ai_hub.readiness_bootstrap_workflows(_req())
    assert api_out["created_count"] == 0
    assert api_out["preserved_count"] == len(ai_hub_readiness.STARTER_WORKFLOWS)


class _State:
    user = {"username": "alice", "role": "admin"}


class _Req:
    state = _State()
    headers = {}


def _req():
    return _Req()
