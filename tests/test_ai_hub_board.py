from __future__ import annotations

import sys
from pathlib import Path

_FLOW_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _FLOW_ROOT / "backend"
for p in (_BACKEND, _FLOW_ROOT):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)


def test_ai_hub_board_combines_operations_queues(tmp_path, monkeypatch):
    from app_v2.modules.semantic_learning import inbox as semantic_inbox
    from core import ai_hub_board, flowi_workflow_templates, skills_repo, tool_registry
    from routers import ai_hub

    monkeypatch.setattr(tool_registry, "STATE_FILE", tmp_path / "tool_registry_state.json")
    monkeypatch.setattr(skills_repo, "SKILLS_DIR", tmp_path / "skills")
    monkeypatch.setattr(skills_repo, "CANDIDATES_DIR", tmp_path / "skills" / "_candidates")
    monkeypatch.setattr(flowi_workflow_templates, "_DIR", tmp_path / "workflows")
    monkeypatch.setattr(semantic_inbox, "INBOX_DIR", tmp_path / "semantic" / "proposals")

    tool_registry.set_enabled("filebrowser", False, by="pytest")
    skills_repo.save_candidate({
        "key": "sk_parallel_review",
        "title": "병렬 리뷰",
        "description": "filebrowser_sql -> home_agent",
        "kind": "chain",
        "steps": [{"tool_name": "filebrowser_sql"}],
        "freq": 4,
        "users": ["alice", "bob"],
    })
    flowi_workflow_templates.save_template({
        "key": "lot_step_review",
        "title": "Lot step 확인",
        "trigger": {"prompt_contains": ["lot", "step"]},
        "steps": [{"unit_ai": "filebrowser", "action": "current_step", "bind_slots": ["product"]}],
        "shared": False,
    }, by="alice")
    semantic_inbox.enqueue_proposal({
        "term": "루트랏",
        "category": "mapping",
        "canonical_match": "root_lot_id",
        "confidence": 0.9,
        "rationale": "운영 질문에서 반복 등장",
        "origin": {"kind": "test", "ref": "ai-hub-board"},
    })

    out = ai_hub_board.build_board(username="alice", limit=5)

    assert out["ok"] is True
    assert out["counts"]["tools_disabled"] >= 1
    assert out["counts"]["skill_candidates"] == 1
    assert out["counts"]["workflows"] == 1
    assert out["counts"]["semantic_proposals_pending"] == 1
    lanes = {lane["id"]: lane for lane in out["lanes"]}
    assert set(lanes) == {"semantic_proposals", "skill_candidates", "workflow_templates", "disabled_tools"}
    assert lanes["semantic_proposals"]["items"][0]["title"] == "루트랏"
    assert lanes["skill_candidates"]["items"][0]["id"] == "sk_parallel_review"
    assert lanes["workflow_templates"]["items"][0]["id"] == "lot_step_review"
    assert any(item["id"] == "filebrowser" for item in lanes["disabled_tools"]["items"])

    api_out = ai_hub.operations_board(_req(), days=30, limit=5)
    assert api_out["is_admin"] is True
    assert api_out["counts"]["skill_candidates"] == 1


class _State:
    user = {"username": "alice", "role": "admin"}


class _Req:
    state = _State()
    headers = {}


def _req():
    return _Req()
