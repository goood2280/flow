from __future__ import annotations

import json
import sys
from pathlib import Path

_FLOW_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _FLOW_ROOT / "backend"
for p in (_BACKEND, _FLOW_ROOT):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)


def test_ai_hub_timeline_groups_management_events(tmp_path, monkeypatch):
    from core import ai_hub_timeline
    from routers import ai_hub

    activity_log = tmp_path / "activity.jsonl"
    rows = [
        {
            "timestamp": "2099-01-01T00:00:00+00:00",
            "username": "alice",
            "action": "ai_hub_run:workflow:lot_step_review",
            "tab": "ai_hub",
            "detail": json.dumps({
                "workflow": "lot_step_review",
                "title": "Lot step 확인",
                "dry_run": True,
                "steps": 2,
                "statuses": {"dry_run": 2},
            }, ensure_ascii=False),
        },
        {
            "timestamp": "2099-01-01T00:01:00+00:00",
            "username": "alice",
            "action": "semantic:proposal:approved:p1",
            "tab": "ai_hub",
            "detail": "term=루트랏;canonical=root_lot_id;upserted=True",
        },
        {
            "timestamp": "2099-01-01T00:02:00+00:00",
            "username": "alice",
            "action": "ai_hub_deep_eval_run",
            "tab": "ai_hub",
            "detail": "status=pass passed=131 failed=0",
        },
        {
            "timestamp": "2099-01-01T00:03:00+00:00",
            "username": "alice",
            "action": "ai_hub_toggle:filebrowser",
            "tab": "ai_hub",
            "detail": "enabled=True",
        },
        {
            "timestamp": "2099-01-01T00:04:00+00:00",
            "username": "bob",
            "action": "inform:create",
            "tab": "inform",
            "detail": "unrelated",
        },
    ]
    activity_log.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(ai_hub_timeline.audit, "ACTIVITY_LOG", activity_log)
    monkeypatch.setattr(ai_hub_timeline.kv, "list_wiki_log", lambda limit=300: [
        {
            "created_at": "2099-01-01T00:02:30+00:00",
            "action": "ingest_commit",
            "actor": "alice",
            "doc_id": "agent_terms",
            "source_ids": ["src_1"],
            "title": "Agent terms",
            "message": "Committed Agent Wiki page agent_terms",
            "log_id": "log_1",
        },
    ])

    out = ai_hub_timeline.build_timeline(days=365, limit=10)

    assert out["ok"] is True
    assert out["counts"] == {"tool": 1, "wiki": 1, "validation": 1, "semantic": 1, "workflow": 1}
    assert [row["category"] for row in out["items"]] == ["tool", "wiki", "validation", "semantic", "workflow"]
    assert out["items"][0]["title"] == "filebrowser"
    assert out["items"][0]["tool_name"] == "filebrowser"
    assert out["items"][1]["title"] == "Agent terms"
    assert out["items"][1]["action"] == "wiki:ingest_commit"
    assert out["items"][2]["tone"] == "ok"
    assert out["items"][3]["title"] == "p1"
    assert out["items"][4]["workflow_key"] == "lot_step_review"

    semantic = ai_hub_timeline.build_timeline(days=365, limit=10, category="semantic")
    assert len(semantic["items"]) == 1
    assert semantic["items"][0]["action"] == "semantic:proposal:approved:p1"

    wiki = ai_hub_timeline.build_timeline(days=365, limit=10, category="wiki")
    assert len(wiki["items"]) == 1
    assert wiki["items"][0]["doc_id"] == "agent_terms"

    api_out = ai_hub.timeline(_req(), days=365, limit=10, category="validation")
    assert api_out["is_admin"] is True
    assert api_out["items"][0]["category"] == "validation"


class _State:
    user = {"username": "alice", "role": "admin"}


class _Req:
    state = _State()
    headers = {}


def _req():
    return _Req()
