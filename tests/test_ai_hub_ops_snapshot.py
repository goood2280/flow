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

    assert out["status"] == "warn"
    assert out["headline"] == "Agent 운영 점검 필요"
    assert out["counts"]["backlog"] == 3
    assert len(out["top_actions"]) == 2
    assert out["top_actions"][0]["tone"] == "bad"
    assert out["recent_events"][0]["category"] == "wiki"
    cards = {row["key"]: row for row in out["summary_cards"]}
    assert cards["readiness"]["value"] == "74점"
    assert cards["deep_eval"]["value"] == "131/131"
    assert cards["wiki"]["detail"] == "sources 10 · lint 0 · graph 490/1347"
    assert cards["timeline"]["detail"] == "wiki 1"
    assert out["export_links"][0]["href"].startswith("/api/ai-hub/ops-export/download?format=obsidian&days=7")
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
