from __future__ import annotations

import sys
from pathlib import Path

_FLOW_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _FLOW_ROOT / "backend"
for p in (_BACKEND, _FLOW_ROOT):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)


def test_ai_hub_wiki_health_summarizes_agent_wiki_state(monkeypatch):
    from core import ai_hub_wiki_health

    monkeypatch.setattr(ai_hub_wiki_health.kv, "list_docs", lambda limit=1000: [
        {"doc_id": "agent_terms", "kind": "agent_wiki", "title": "Agent terms", "summary": "terms", "updated_at": "2099-01-01T00:00:00+00:00", "tags": ["agent"]},
        {"doc_id": "ml_step_id", "kind": "schema_doc", "title": "step_id", "summary": "schema", "updated_at": "2099-01-01T00:00:00+00:00"},
    ])
    monkeypatch.setattr(ai_hub_wiki_health.kv, "list_agent_wiki_pages", lambda limit=100: [
        {"doc_id": "agent_terms", "kind": "agent_wiki", "title": "Agent terms", "summary": "terms", "updated_at": "2099-01-01T00:00:00+00:00", "tags": ["agent"]},
    ])
    monkeypatch.setattr(ai_hub_wiki_health.kv, "list_agent_wiki_sources", lambda limit=1000: [
        {"source_id": "src_1", "source_type": "markdown", "title": "Source", "actor": "alice", "created_at": "2099-01-01T00:00:00+00:00", "content_chars": 120},
    ])
    monkeypatch.setattr(ai_hub_wiki_health.kv, "list_wiki_log", lambda limit=100: [
        {"log_id": "log_1", "action": "ingest_commit", "doc_id": "agent_terms", "title": "Agent terms", "actor": "alice", "created_at": "2099-01-01T00:01:00+00:00"},
    ])
    monkeypatch.setattr(ai_hub_wiki_health.kv, "lint_agent_wiki", lambda: {
        "ok": True,
        "checked_at": "2099-01-01T00:02:00+00:00",
        "broken_links": [{"doc_id": "agent_terms", "target": "missing"}],
        "missing_sources": [],
        "orphan_pages": [{"doc_id": "agent_terms", "inbound_links": 0}],
        "stale_summaries": [],
        "contradiction_candidates": [],
        "counts": {"pages": 1, "broken_links": 1, "missing_sources": 0, "orphan_pages": 1, "stale_summaries": 0, "contradiction_candidates": 0},
    })
    monkeypatch.setattr(ai_hub_wiki_health.kv, "get_graph", lambda rebuild_if_missing=False, rebuild_if_stale=False: {
        "updated_at": "2099-01-01T00:03:00+00:00",
        "counts": {"nodes": 4, "edges": 3, "docs": 2, "events": 1},
    })

    out = ai_hub_wiki_health.build_wiki_health(limit=5)

    assert out["ok"] is True
    assert out["status"] == "warn"
    assert out["counts"]["docs"] == 2
    assert out["counts"]["agent_wiki_pages"] == 1
    assert out["counts"]["schema_docs"] == 1
    assert out["counts"]["sources"] == 1
    assert out["counts"]["graph_nodes"] == 4
    assert out["counts"]["lint_issues"] == 1
    assert out["recent_pages"][0]["doc_id"] == "agent_terms"
    assert out["recent_sources"][0]["source_id"] == "src_1"
    assert out["recent_log"][0]["action"] == "ingest_commit"
    assert out["sources"]["wiki_lint"] == "/api/agent/wiki/lint"


def test_ai_hub_wiki_health_endpoint_adds_admin_flag(monkeypatch):
    from routers import ai_hub

    monkeypatch.setattr(ai_hub.ai_hub_wiki_health, "build_wiki_health", lambda limit=12: {
        "ok": True,
        "status": "pass",
        "counts": {"docs": 1, "sources": 1, "lint_issues": 0},
    })

    out = ai_hub.wiki_health(_req(), limit=12)

    assert out["is_admin"] is True
    assert out["status"] == "pass"


class _State:
    user = {"username": "alice", "role": "admin"}


class _Req:
    state = _State()
    headers = {}


def _req():
    return _Req()
