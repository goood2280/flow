from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app_v2.modules.agent_runtime import build_runtime_blueprint, resolve_semantic_frame  # noqa: E402
from app_v2.runtime.security import QUERY_TOKEN_PREFIXES  # noqa: E402


def test_agent_runtime_semantic_layer_resolves_core_terms():
    frame = resolve_semantic_frame("PRODA A1000 #21 현재 step과 KNOB 영향을 LangSmith trace로 보여줘")

    assert frame.intent in {"traceable_orchestration", "knob_analysis"}
    assert frame.slots["products"] == ["PRODA"]
    assert frame.slots["root_lot_ids"] == ["A1000"]
    assert frame.slots["wafer_ids"] == [21]
    assert any(c.canonical_alias in {"knob", "step_id", "wafer_id", "langsmith"} or c.normalized == "knob" for c in frame.candidates)
    assert frame.polars_profile["catalog_rows"] >= 1


def test_agent_runtime_blueprint_exposes_langgraph_langsmith_sse_contract():
    blueprint = build_runtime_blueprint()

    assert blueprint["ok"] is True
    assert blueprint["graph"]["stream_mode"] == "updates"
    assert ["semantic_layer", "task_planner"] in blueprint["graph"]["edges"]
    assert "stream" in blueprint["endpoints"]
    assert blueprint["endpoints"]["stream"].startswith("GET /api/agent/runtime/stream")
    assert any(agent["agent_id"] == "semantic_interpreter" for agent in blueprint["unit_agents"])
    assert "project" in blueprint["langsmith"]


def test_agent_runtime_sse_allows_query_token_auth_for_eventsource():
    assert "/api/agent/runtime/stream" in QUERY_TOKEN_PREFIXES


def test_semantic_frame_includes_thought_trace_with_activation_intent_slots():
    frame = resolve_semantic_frame("PRODA A1000 #21 KNOB 변화 LangSmith 추적")

    assert frame.thought is not None
    # Intent decision must include the chosen intent and per-intent score breakdown
    assert frame.thought.intent_decision.chosen == frame.intent
    assert isinstance(frame.thought.intent_decision.scores, list)
    assert len(frame.thought.intent_decision.scores) >= 1
    # Activation table — at least one row should exist when token catalog hits
    assert isinstance(frame.thought.activation_table, list)
    if frame.candidates:
        assert len(frame.thought.activation_table) >= 1
        first = frame.thought.activation_table[0]
        assert hasattr(first, "token")
        assert hasattr(first, "score")
    # Slot extraction summary mirrors slots and reports token_count
    assert frame.thought.slot_extraction.get("token_count") == len(frame.tokens)
    if frame.slots.get("products"):
        assert frame.thought.slot_extraction.get("products") == frame.slots["products"]


def test_semantic_resolver_respects_disk_lexicon_override(tmp_path, monkeypatch):
    """When admin adds a new alias on disk, resolver picks it up immediately."""
    from app_v2.modules.semantic_lexicon import store as _lex_store
    base = tmp_path / "semantic"
    base.mkdir()
    monkeypatch.setattr(_lex_store, "LEXICON_DIR", base)
    monkeypatch.setattr(_lex_store, "ALIAS_FILE", base / "alias_groups.json")
    monkeypatch.setattr(_lex_store, "INTENT_FILE", base / "intent_hints.json")
    monkeypatch.setattr(_lex_store, "CHANGES_FILE", base / "changes.jsonl")

    # Seed-only behaviour: a custom company word "산화막" should NOT normalize anywhere
    frame_before = resolve_semantic_frame("GATE 산화막 두께 측정")
    assert frame_before.normalized_terms.get("산화막") in (None, "")

    # Admin adds "산화막" as an alias for the existing "oxide" canonical via disk override
    _lex_store.save_alias_groups({"oxide": ["산화막", "GATE 산화막"]}, by="hol")

    frame_after = resolve_semantic_frame("GATE 산화막 두께 측정")
    assert frame_after.normalized_terms.get("산화막") == "oxide"
