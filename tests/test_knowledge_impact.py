from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app_v2.shared.contracts import KnowledgeDoc  # noqa: E402
from core import knowledge_impact, knowledge_vault as kv  # noqa: E402


def _isolate_knowledge(tmp_path, monkeypatch):
    root = tmp_path / "knowledge"
    monkeypatch.setattr(kv, "KNOWLEDGE_ROOT", root)
    monkeypatch.setattr(kv, "RAW_DIR", root / "raw")
    monkeypatch.setattr(kv, "EVENT_DIR", root / "raw" / "events")
    monkeypatch.setattr(kv, "SOURCE_DIR", root / "raw" / "sources")
    monkeypatch.setattr(kv, "WIKI_DIR", root / "wiki")
    monkeypatch.setattr(kv, "GRAPH_DIR", root / "graph")
    monkeypatch.setattr(kv, "INDEX_DIR", root / "index")
    monkeypatch.setattr(kv, "ONTOLOGY_DIR", root / "ontology")
    monkeypatch.setattr(kv, "EVENTS_JSONL", root / "raw" / "events" / "events.jsonl")
    monkeypatch.setattr(kv, "SOURCES_JSONL", root / "raw" / "sources" / "sources.jsonl")
    monkeypatch.setattr(kv, "WIKI_INDEX_FILE", root / "index" / "wiki_index.json")
    monkeypatch.setattr(kv, "WIKI_LOG_JSONL", root / "index" / "wiki_log.jsonl")
    monkeypatch.setattr(kv, "GRAPH_FILE", root / "graph" / "graph.json")
    monkeypatch.setattr(kv, "AI_ONTOLOGY_FILE", root / "ontology" / "ai_ontology.json")
    monkeypatch.setattr(kv, "SCHEMA_RELATION_FILE", tmp_path / "schema_relations.json")


def test_impact_payload_normalizes_required_fields_and_source_refs():
    payload = knowledge_impact.normalize_payload(
        "split_impact",
        {
            "product": "PRODA",
            "step_id": "24.0 SORT",
            "knob_name": "KNOB_A",
            "effect_confidence": "not-a-number",
            "source_refs": [{"type": "issue", "id": "ISS-1"}, {"type": "issue", "id": "ISS-1"}],
        },
        source_type="issue",
        source_id="ISS-1",
    )

    assert payload["event_type"] == "split_impact"
    assert set(knowledge_impact.PAYLOAD_FIELDS).issubset(payload.keys())
    assert payload["effect_confidence"] is None
    assert payload["confidence_parse_error"] is True
    assert payload["source_refs"] == [{"type": "issue", "id": "ISS-1"}]
    assert payload["impact_key"] == "PRODA||24.0 SORT|KNOB_A|"


def test_anchor_item_registry_closes_previous_open_version():
    events = [
        {
            "event_id": "evt_old",
            "event_type": "anchor_item_change",
            "payload": {"product": "PRODA", "step_id": "24.0 SORT", "item_id": "INLINE_OLD", "changed_at": "2026-01-01T00:00:00"},
        },
        {
            "event_id": "evt_new",
            "event_type": "anchor_item_change",
            "payload": {
                "product": "PRODA",
                "step_id": "24.0 SORT",
                "item_id": "INLINE_NEW",
                "previous_item_id": "INLINE_OLD",
                "changed_at": "2026-02-01T00:00:00",
                "reason": "main inline item changed",
            },
        },
    ]

    registry = knowledge_impact.build_anchor_item_registry(events)

    old = next(row for row in registry if row["item_id"] == "INLINE_OLD")
    new = next(row for row in registry if row["item_id"] == "INLINE_NEW")
    assert old["valid_to"] == "2026-02-01T00:00:00"
    assert old["replaced_by"] == "INLINE_NEW"
    assert new["valid_to"] == ""


def test_impact_context_combines_verified_wiki_and_conflicting_events(tmp_path, monkeypatch):
    _isolate_knowledge(tmp_path, monkeypatch)
    kv.upsert_doc(KnowledgeDoc(
        doc_id="split_rule_proda_sort_knob_a",
        kind="agent_wiki",
        title="PRODA SORT KNOB_A split impact rule",
        summary="Human-verified split impact rule.",
        body="PRODA 24.0 SORT KNOB_A split impact is reviewed by process owner.",
        frontmatter={"schema_type": "split_impact_rule", "product": "PRODA", "step_id": "24.0 SORT", "knob_name": "KNOB_A"},
    ))
    for direction in ("positive", "negative"):
        knowledge_impact.append_domain_event(
            event_type="split_impact",
            source_type="split_note",
            source_id=f"note-{direction}",
            title="Split impact candidate",
            summary=f"Candidate direction {direction}",
            actor="tester",
            payload={
                "product": "PRODA",
                "root_lot_id": "A1000",
                "step_id": "24.0 SORT",
                "knob_name": "KNOB_A",
                "effect_direction": direction,
                "status": "candidate",
            },
        )

    ctx = knowledge_impact.impact_context(product="PRODA", root_lot_id="A1000", step_id="24.0 SORT", knob="KNOB_A")

    assert ctx["confidence"] == "verified_wiki"
    assert ctx["wiki_refs"][0]["doc_id"] == "split_rule_proda_sort_knob_a"
    assert len(ctx["split_impacts"]) == 2
    assert ctx["conflicts"][0]["conflicting_evidence"] is True
