from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app_v2.modules.semantic_learning import inbox  # noqa: E402
from app_v2.modules.semantic_lexicon import store  # noqa: E402
from routers import agent  # noqa: E402


class _State:
    def __init__(self, user: dict):
        self.user = user


class _Request:
    headers = {}
    method = "GET"
    query_params = {}

    def __init__(self, username: str = "tester", role: str = "admin"):
        self.state = _State({"username": username, "role": role})


@pytest.fixture()
def semantic_store(tmp_path, monkeypatch):
    semantic_dir = tmp_path / "semantic"
    proposals_dir = semantic_dir / "proposals"
    semantic_dir.mkdir()
    proposals_dir.mkdir()
    monkeypatch.setattr(store, "LEXICON_DIR", semantic_dir)
    monkeypatch.setattr(store, "ALIAS_FILE", semantic_dir / "alias_groups.json")
    monkeypatch.setattr(store, "INTENT_FILE", semantic_dir / "intent_hints.json")
    monkeypatch.setattr(store, "CHANGES_FILE", semantic_dir / "changes.jsonl")
    monkeypatch.setattr(inbox, "INBOX_DIR", proposals_dir)
    monkeypatch.setattr(agent, "current_user", lambda request: request.state.user)
    return semantic_dir


def test_semantic_lexicon_alias_intent_roundtrip(semantic_store):
    req = _Request(role="admin")

    agent.semantic_alias_group_upsert("ioff", agent.SemanticAliasGroupReq(aliases=["IOFF", "누설전류"]), req)
    agent.semantic_intent_hint_upsert("inform_registration", agent.SemanticIntentHintReq(required_canonicals=["product", "lot_id"]), req)

    payload = agent.semantic_lexicon(req)
    assert payload["ok"] is True
    assert payload["alias_groups"]["disk"]["ioff"] == ["IOFF", "누설전류"]
    assert payload["intent_hints"]["disk"]["inform_registration"] == ["product", "lot_id"]
    assert payload["changes"]

    deleted = agent.semantic_alias_group_delete("ioff", req)
    assert deleted["deleted"] is True
    assert "ioff" not in deleted["alias_groups"]["disk"]


def test_semantic_write_requires_admin_or_page_manager(semantic_store, monkeypatch):
    req = _Request(username="viewer", role="user")
    monkeypatch.setattr(agent, "is_page_manager", lambda _user, _page: False)

    with pytest.raises(HTTPException) as excinfo:
        agent.semantic_alias_group_upsert("ioff", agent.SemanticAliasGroupReq(aliases=["IOFF"]), req)
    assert excinfo.value.status_code == 403

    monkeypatch.setattr(agent, "is_page_manager", lambda _user, page: page == "diagnosis")
    out = agent.semantic_alias_group_upsert("ioff", agent.SemanticAliasGroupReq(aliases=["IOFF"]), req)
    assert out["ok"] is True


def test_semantic_proposal_approve_adds_alias_and_marks_decided(semantic_store):
    req = _Request(role="admin")
    agent.semantic_alias_group_upsert("wafer_id", agent.SemanticAliasGroupReq(aliases=["wafer"]), req)
    proposal = inbox.enqueue_proposal({
        "term": "웨이퍼",
        "category": "mapping",
        "canonical_match": "wafer_id",
        "confidence": 0.8,
        "rationale": "test",
        "origin": {"kind": "inform", "ref": "if-001"},
    })

    decided = agent.semantic_proposal_decision(
        proposal["id"],
        agent.SemanticProposalDecisionReq(decision="approve"),
        req,
    )

    assert decided["proposal"]["status"] == "approved"
    assert "웨이퍼" in decided["alias_groups"]["disk"]["wafer_id"]
    pending = agent.semantic_proposals(req, status="pending")["proposals"]
    assert all(row["id"] != proposal["id"] for row in pending)


def test_semantic_draft_from_json_and_text_is_read_only(semantic_store):
    req = _Request(role="user")

    json_out = agent.semantic_draft(agent.SemanticDraftReq(text='{"alias_groups":{"oxide":["산화막"]}}'), req)
    assert json_out["draft"]["alias_groups"] == {"oxide": ["산화막"]}
    assert store.load_alias_groups() == {}

    text_out = agent.semantic_draft(agent.SemanticDraftReq(text="산화막 두께 intent:inform_registration -> product,lot_id"), req)
    assert text_out["ok"] is True
    assert text_out["draft"]["alias_groups"]
    assert text_out["draft"]["intent_hints"]["inform_registration"] == ["product", "lot_id"]
    assert store.load_alias_groups() == {}
