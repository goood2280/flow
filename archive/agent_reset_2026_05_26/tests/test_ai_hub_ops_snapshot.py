from __future__ import annotations

import sys
from pathlib import Path

_FLOW_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _FLOW_ROOT / "backend"
for p in (_BACKEND, _FLOW_ROOT):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)


def test_ai_hub_ops_snapshot_builds_daily_operator_view(monkeypatch):
    from core import ai_hub_ops_snapshot

    monkeypatch.setattr(ai_hub_ops_snapshot.ai_hub_readiness, "build_readiness", lambda username="", days=30: {
        "score": 74,
        "level": "attention",
        "backlog": [
            {"id": "deep_eval:failed", "severity": "high", "title": "Agent 검증 실패", "target": "semantic", "detail": "1 failed", "action": "리포트 재생성", "route": "/api/ai-hub/deep-eval-report"},
            {"id": "agent_wiki:missing_sources", "severity": "medium", "title": "Wiki source 보강", "target": "agent_terms", "detail": "missing source", "action": "source 연결", "route": "/api/ai-hub/wiki-health"},
            {"id": "skill_candidate:lot-step", "severity": "low", "title": "스킬 후보 검토", "target": "lot-step", "detail": "freq 3", "action": "승인/거부", "route": "/api/skills/candidates"},
        ],
    })
    monkeypatch.setattr(ai_hub_ops_snapshot.ai_hub_deep_eval, "load_latest_report", lambda: {
        "status": "pass",
        "age_seconds": 7200,
        "summary": {"passed": 131, "failed": 0, "total": 131},
    })
    monkeypatch.setattr(ai_hub_ops_snapshot.ai_hub_wiki_health, "build_wiki_health", lambda limit=12: {
        "status": "pass",
        "counts": {"docs": 15, "sources": 10, "lint_issues": 0, "graph_nodes": 490, "graph_edges": 1347},
    })
    monkeypatch.setattr(ai_hub_ops_snapshot.ai_hub_workflow_runbook, "build_runbook", lambda username="", days=30, limit=12: {
        "counts": {"workflows": 3, "ready": 1, "attention": 1, "blocked": 1, "unchecked": 1, "next_actions": 4},
        "next_action_queue": [{
            "key": "missing_tools",
            "title": "미등록 도구 연결",
            "detail": "ToolRegistry 등록 또는 workflow step unit_ai 값을 수정",
            "route": "/api/ai-hub/workflow-map",
            "tone": "bad",
            "count": 2,
            "workflow_keys": ["blocked_lot", "blocked_knob"],
            "workflows": [{"key": "blocked_lot", "title": "LOT step 확인", "status": "blocked", "tone": "bad"}],
        }],
    })
    monkeypatch.setattr(ai_hub_ops_snapshot.ai_hub_workflow_map, "build_workflow_map", lambda username="", days=30, limit=40, reference_limit=160, focus_tag="": {
        "counts": {
            "nodes": 18,
            "edges": 22,
            "workflow_templates_visible": 3,
            "tools_visible": 8,
            "tools_total": 12,
        },
        "warnings": [{"key": "missing_evidence", "tone": "warn", "message": "refs", "items": ["lot_wf", "knob"]}],
    })
    monkeypatch.setattr(ai_hub_ops_snapshot.ai_hub_timeline, "build_timeline", lambda days=30, limit=12, category="": {
        "items": [
            {
                "id": "event-1",
                "timestamp": "2099-01-01T00:00:00+00:00",
                "category": "wiki",
                "tone": "ok",
                "title": "Agent terms",
                "username": "alice",
                "meta": "ingest_commit",
                "detail": "Committed Agent terms",
                "action": "wiki:ingest_commit",
                "doc_id": "agent_terms",
            }
        ],
        "counts": {"wiki": 1},
    })

    out = ai_hub_ops_snapshot.build_snapshot(username="alice", days=7, limit=2)

    assert out["status"] == "bad"
    assert out["headline"] == "Agent 운영 문제 확인 필요"
    assert out["counts"]["backlog"] == 3
    assert out["counts"]["runbook_next_actions"] == 4
    assert len(out["top_actions"]) == 2
    assert len(out["runbook_action_queue"]) == 1
    assert out["runbook_action_queue"][0]["key"] == "missing_tools"
    assert out["runbook_action_queue"][0]["count"] == 2
    assert out["runbook_action_queue"][0]["workflows"][0]["key"] == "blocked_lot"
    assert len(out["workflow_map_warnings"]) == 1
    assert out["workflow_map_warnings"][0]["key"] == "missing_evidence"
    assert out["workflow_map_warnings"][0]["item_count"] == 2
    assert out["workflow_map_warnings"][0]["items"] == ["lot_wf", "knob"]
    assert "knowledge_refs" in out["workflow_map_warnings"][0]["action"]
    assert out["workflow_map_warnings"][0]["route"] == "/api/ai-hub/workflow-map"
    assert out["top_actions"][0]["tone"] == "bad"
    assert out["recent_events"][0]["category"] == "wiki"
    cards = {row["key"]: row for row in out["summary_cards"]}
    assert cards["readiness"]["value"] == "74점"
    assert cards["workflow_runbook"]["value"] == "1/3"
    assert cards["workflow_runbook"]["tone"] == "bad"
    assert cards["workflow_runbook"]["detail"] == "blocked 1 · attention 1 · unchecked 1 · actions 4"
    assert cards["workflow_map"]["value"] == "18/22"
    assert cards["workflow_map"]["tone"] == "warn"
    assert cards["workflow_map"]["detail"] == "workflows 3 · tools 8/12 · warnings 1"
    assert cards["deep_eval"]["value"] == "131/131"
    assert cards["wiki"]["detail"] == "sources 10 · lint 0 · graph 490/1347"
    assert cards["timeline"]["detail"] == "wiki 1"
    assert out["counts"]["runbook_blocked"] == 1
    assert out["counts"]["workflow_map_nodes"] == 18
    assert out["counts"]["workflow_map_warnings"] == 1
    assert out["export_links"][0]["href"].startswith("/api/ai-hub/ops-export/download?format=obsidian&days=7")
    assert out["sources"]["workflow_runbook"] == "/api/ai-hub/workflow-runbook"
    assert out["sources"]["workflow_map"] == "/api/ai-hub/workflow-map"
    assert out["sources"]["timeline"] == "/api/ai-hub/timeline"


def test_ai_hub_ops_snapshot_endpoint_passes_user_and_admin(monkeypatch):
    from routers import ai_hub

    def fake_build_snapshot(username="", days=30, limit=8):
        assert username == "alice"
        assert days == 7
        assert limit == 3
        return {
            "ok": True,
            "status": "ok",
            "summary_cards": [],
            "top_actions": [],
            "recent_events": [],
        }

    monkeypatch.setattr(ai_hub.ai_hub_ops_snapshot, "build_snapshot", fake_build_snapshot)

    out = ai_hub.ops_snapshot(_req(), days=7, limit=3)

    assert out["ok"] is True
    assert out["is_admin"] is True


class _State:
    user = {"username": "alice", "role": "admin"}


class _Req:
    state = _State()
    headers = {}


def _req():
    return _Req()
