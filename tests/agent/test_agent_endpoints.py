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

from routers import agent  # noqa: E402


class DummyState:
    def __init__(self, user):
        self.user = user


class DummyRequest:
    def __init__(self, user):
        self.state = DummyState(user)
        self.headers = {}


def req(role: str = "user", username: str = "alice"):
    return DummyRequest({"username": username, "role": role})


def test_agent_workflow_shape():
    out = agent.agent_workflow(req())

    assert out["ok"] is True
    assert out["stage_count"] == 8
    assert out["stages"][0]["key"] == "input_prompt"
    assert any("register_inform_walkthrough" in stage["modules"] for stage in out["stages"])


def test_agent_persona_uses_current_user_activity(tmp_path, monkeypatch):
    activity = tmp_path / "flowi_activity.jsonl"
    activity.write_text(
        json.dumps({
            "timestamp": "2026-05-01T00:00:00+00:00",
            "username": "alice",
            "event": "chat",
            "fields": {"prompt": "PRODA A1000", "selected_function": "query_fab_progress", "result_status": "success"},
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(agent.flowi_llm, "FLOWI_ACTIVITY_FILE", activity)
    monkeypatch.setattr(agent.flowi_llm, "_read_user_md", lambda *_args, **_kwargs: "")

    out = agent.agent_persona(req(username="alice"))

    assert out["ok"] is True
    assert out["username"] == "alice"
    assert out["frequent_products"][0]["product"] == "PRODA"
    assert out["last_actions"][0]["selected_function"] == "query_fab_progress"


def test_agent_inventory_and_item_rules_shape(monkeypatch, tmp_path):
    promoted = tmp_path / "promoted_knowledge.json"
    promoted.write_text(json.dumps({"items": []}), encoding="utf-8")
    monkeypatch.setattr(agent.flowi_llm, "FLOWI_PROMOTED_KNOWLEDGE_FILE", promoted)

    inv = agent.knowledge_inventory(req(), q="DIBL", tag="", kind="knowledge_cards")
    rules = agent.item_rules(req(), source_type="ET", product="PRODA")

    assert inv["ok"] is True
    assert "knowledge_cards" in inv["kinds"]
    assert isinstance(inv["items"], list)
    assert rules["ok"] is True
    assert isinstance(rules["rules"], list)
    if rules["rules"]:
        assert {"item", "matching_step_id", "matching_knob", "matching_mask"} <= set(rules["rules"][0])


def test_recent_rag_shape_and_user_scope(tmp_path, monkeypatch):
    activity = tmp_path / "flowi_activity.jsonl"
    rows = [
        {
            "timestamp": "2026-05-01T00:01:00+00:00",
            "username": "alice",
            "event": "chat",
            "fields": {
                "prompt": "DIBL RCA",
                "selected_function": "run_semiconductor_diagnosis",
                "retrieved_ids": ["KC1"],
                "retrieval_score": 0.8,
                "elapsed_ms": 12,
                "result_status": "success",
            },
        },
        {
            "timestamp": "2026-05-01T00:02:00+00:00",
            "username": "bob",
            "event": "chat",
            "fields": {"prompt": "hidden", "selected_function": "x"},
        },
    ]
    activity.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(agent.flowi_llm, "FLOWI_ACTIVITY_FILE", activity)

    out = agent.recent_rag(req(username="alice"), limit=50, user="bob")

    assert out["ok"] is True
    assert out["user"] == "alice"
    assert len(out["traces"]) == 1
    assert out["traces"][0]["retrieved_ids"] == ["KC1"]


def test_admin_tools_require_admin_and_ingest_to_temp(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "AGENT_ADMIN_STATE_FILE", tmp_path / "agent_admin_tools.json")
    monkeypatch.setattr(agent, "AGENT_BACKUP_DIR", tmp_path / "agent_backups")
    monkeypatch.setattr(agent, "AGENT_KNOWLEDGE_RAW_DIR", tmp_path / "knowledge" / "raw")
    monkeypatch.setattr(agent.semi, "SEMICONDUCTOR_DIR", tmp_path / "semiconductor")
    monkeypatch.setattr(agent.semi, "CUSTOM_KNOWLEDGE_FILE", tmp_path / "semiconductor" / "custom_knowledge.jsonl")

    with pytest.raises(HTTPException) as denied:
        agent.matching_suggest(agent.MatchingSuggestReq(product="PRODA", source_table="ML_TABLE"), req(role="user"))
    assert denied.value.status_code == 403

    suggest = agent.rulebook_suggest(agent.RulebookSuggestReq(product="PRODA", knob="KNOB_A", mask="", change_summary="CA"), req(role="admin", username="root"))
    assert suggest["ok"] is True
    assert suggest["candidates"]

    ingested = agent.knowledge_ingest(
        agent.KnowledgeIngestReq(title="테스트 지식", tags=["DIBL"], doc_type="internal_knowledge", content="DIBL 원인 후보 " * 200),
        req(role="admin", username="root"),
    )
    listed = agent.knowledge_list(req(role="admin", username="root"))

    assert ingested["ok"] is True
    assert ingested["structured"]["chunk_count"] >= 1
    assert listed["rows"][0]["title"] == "테스트 지식"
