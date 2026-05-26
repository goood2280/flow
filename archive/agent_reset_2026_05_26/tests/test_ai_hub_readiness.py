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
    from core import ai_hub_board, ai_hub_deep_eval, ai_hub_readiness, ai_hub_wiki_health, ai_hub_workflow_map
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
    monkeypatch.setattr(ai_hub_deep_eval, "load_latest_report", _passing_deep_eval)
    monkeypatch.setattr(ai_hub_wiki_health, "build_wiki_health", _passing_wiki_health)

    out = ai_hub_readiness.build_readiness(username="alice", days=30)

    assert out["ok"] is True
    assert 0 <= out["score"] <= 100
    assert out["level"] in {"operational", "managed", "needs_attention", "bootstrap"}
    assert {check["key"] for check in out["checks"]} == {
        "tool_catalog",
        "knowledge_grounding",
        "agent_wiki_health",
        "learning_queue",
        "workflow_assets",
        "workflow_validation",
        "agent_deep_eval",
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
    assert api_out["counts"]["wiki_lint_issues"] == 0
    assert api_out["counts"]["deep_eval_failed"] == 0
    assert api_out["is_admin"] is True


def test_ai_hub_readiness_tracks_workflow_validation_backlog(monkeypatch):
    from core import ai_hub_board, ai_hub_deep_eval, ai_hub_readiness, ai_hub_wiki_health, ai_hub_workflow_map

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
    monkeypatch.setattr(ai_hub_deep_eval, "load_latest_report", _passing_deep_eval)
    monkeypatch.setattr(ai_hub_wiki_health, "build_wiki_health", _passing_wiki_health)

    out = ai_hub_readiness.build_readiness(username="alice", days=30)

    assert out["counts"]["workflow_validation_total"] == 2
    assert out["counts"]["workflow_validation_checked"] == 1
    assert out["counts"]["workflow_validation_unverified"] == 1
    assert out["counts"]["workflow_validation_warnings"] == 1
    backlog_ids = {item["id"] for item in out["backlog"]}
    assert "workflow_unverified:unchecked" in backlog_ids
    assert "workflow_validation_warning:warned" in backlog_ids
    by_id = {item["id"]: item for item in out["backlog"]}
    assert by_id["workflow_unverified:unchecked"]["actions"][0]["endpoint"] == "/api/agent/workflows/execute"
    assert by_id["workflow_unverified:unchecked"]["actions"][0]["body"] == {
        "key": "unchecked",
        "slots": {},
        "dry_run": True,
    }
    assert by_id["workflow_validation_warning:warned"]["actions"][0]["body"]["key"] == "warned"


def test_ai_hub_readiness_tracks_deep_eval_backlog(monkeypatch):
    from core import ai_hub_board, ai_hub_deep_eval, ai_hub_readiness, ai_hub_wiki_health, ai_hub_workflow_map

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
                "workflows": 1,
            },
            "lanes": [],
        }

    def fake_map(username="", days=30, limit=120, reference_limit=400, focus_tag=""):
        return {
            "ok": True,
            "counts": {"tools_total": 2, "tools_visible": 2, "tools_without_refs_visible": 0},
            "nodes": [
                {
                    "id": "workflow:checked",
                    "type": "workflow",
                    "workflow_key": "checked",
                    "metrics": {"run_count": 1, "warning_count": 0},
                },
            ],
            "warnings": [],
        }

    def failing_deep_eval():
        return {
            "ok": True,
            "exists": True,
            "status": "fail",
            "summary": {"passed": 130, "failed": 2, "total": 132},
            "age_seconds": 99 * 86400,
            "generated_at": "2026-01-01T00:00:00+00:00",
            "failed_results": [{"name": "sql/raw join/rows", "detail": "bad rows"}],
        }

    monkeypatch.setattr(ai_hub_board, "build_board", fake_board)
    monkeypatch.setattr(ai_hub_workflow_map, "build_workflow_map", fake_map)
    monkeypatch.setattr(ai_hub_deep_eval, "load_latest_report", failing_deep_eval)
    monkeypatch.setattr(ai_hub_wiki_health, "build_wiki_health", _passing_wiki_health)

    out = ai_hub_readiness.build_readiness(username="alice", days=30)

    assert out["counts"]["deep_eval_total"] == 132
    assert out["counts"]["deep_eval_failed"] == 2
    assert next(check for check in out["checks"] if check["key"] == "agent_deep_eval")["score"] == 55
    backlog_ids = {item["id"] for item in out["backlog"]}
    assert "agent_deep_eval:failed" in backlog_ids
    assert "agent_deep_eval:stale" in backlog_ids
    by_id = {item["id"]: item for item in out["backlog"]}
    assert by_id["agent_deep_eval:failed"]["route"] == "/api/ai-hub/deep-eval-report"
    assert by_id["agent_deep_eval:failed"]["actions"][0]["endpoint"] == "/api/ai-hub/deep-eval-report/run"
    assert by_id["agent_deep_eval:failed"]["actions"][0]["body"] == {
        "cleanup_knowledge": False,
        "min_cases": 80,
    }
    assert "sql/raw join/rows" in by_id["agent_deep_eval:failed"]["detail"]


def test_ai_hub_readiness_tracks_wiki_health_backlog(monkeypatch):
    from core import ai_hub_board, ai_hub_deep_eval, ai_hub_readiness, ai_hub_wiki_health, ai_hub_workflow_map

    monkeypatch.setattr(ai_hub_board, "build_board", lambda username="", days=30, limit=12: {
        "ok": True,
        "counts": {
            "tools_total": 2,
            "tools_enabled": 2,
            "tools_disabled": 0,
            "semantic_proposals_pending": 0,
            "skill_candidates": 0,
            "skills": 1,
            "workflows": 1,
        },
        "lanes": [],
    })
    monkeypatch.setattr(ai_hub_workflow_map, "build_workflow_map", lambda username="", days=30, limit=120, reference_limit=400, focus_tag="": {
        "ok": True,
        "counts": {"tools_total": 2, "tools_visible": 2, "tools_without_refs_visible": 0},
        "nodes": [
            {
                "id": "workflow:checked",
                "type": "workflow",
                "workflow_key": "checked",
                "metrics": {"run_count": 1, "warning_count": 0},
            },
        ],
        "warnings": [],
    })
    monkeypatch.setattr(ai_hub_deep_eval, "load_latest_report", _passing_deep_eval)
    monkeypatch.setattr(ai_hub_wiki_health, "build_wiki_health", lambda limit=12: {
        "ok": True,
        "status": "warn",
        "counts": {"docs": 2, "sources": 1, "lint_issues": 2, "graph_nodes": 0, "graph_edges": 0},
        "lint": {
            "broken_links": [{"doc_id": "agent_terms", "target": "missing_page"}],
            "missing_sources": [{"doc_id": "agent_terms", "source_id": "src_missing"}],
            "stale_summaries": [],
            "contradiction_candidates": [],
        },
    })

    out = ai_hub_readiness.build_readiness(username="alice", days=30)

    wiki_check = next(check for check in out["checks"] if check["key"] == "agent_wiki_health")
    assert wiki_check["score"] == 59
    assert out["counts"]["wiki_docs"] == 2
    assert out["counts"]["wiki_lint_issues"] == 2
    backlog_ids = {item["id"] for item in out["backlog"]}
    assert "agent_wiki:graph_missing" in backlog_ids
    assert "agent_wiki:broken_links:agent_terms" in backlog_ids
    assert "agent_wiki:missing_sources:agent_terms" in backlog_ids
    by_id = {item["id"]: item for item in out["backlog"]}
    assert by_id["agent_wiki:broken_links:agent_terms"]["route"] == "/api/ai-hub/wiki-health"


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


def _passing_deep_eval():
    return {
        "ok": True,
        "exists": True,
        "status": "pass",
        "summary": {"passed": 131, "failed": 0, "total": 131},
        "age_seconds": 60,
        "generated_at": "2026-05-24T01:30:00+00:00",
        "groups": {"semantic": {"passed": 108, "failed": 0, "total": 108}},
        "failed_results": [],
    }


def _passing_wiki_health(limit=12):
    return {
        "ok": True,
        "status": "pass",
        "counts": {
            "docs": 8,
            "agent_wiki_pages": 3,
            "schema_docs": 2,
            "sources": 5,
            "wiki_log": 6,
            "lint_issues": 0,
            "graph_nodes": 20,
            "graph_edges": 30,
        },
        "lint": {"counts": {"broken_links": 0, "missing_sources": 0, "stale_summaries": 0, "contradiction_candidates": 0}},
    }
