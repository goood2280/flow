from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app_v2.shared.contracts import KnowledgeDoc  # noqa: E402
from core import knowledge_vault as kv  # noqa: E402


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


def test_graph_links_docs_to_terms(tmp_path, monkeypatch):
    _isolate_knowledge(tmp_path, monkeypatch)
    doc_id = "ml_table_prodx.root_lot_id"
    kv.upsert_doc(KnowledgeDoc(
        doc_id=doc_id,
        kind="schema_doc",
        title="ML_TABLE_PRODX root_lot_id",
        summary="Root lot identifier",
        body="root_lot_id identifies the root lot.",
        frontmatter={"relation_id": "ML_TABLE_PRODX", "column_refs": ["ML_TABLE_PRODX.root_lot_id"]},
    ))
    kv.merge_schema_column_catalog([{
        "relation_id": "ML_TABLE_PRODX",
        "column": "root_lot_id",
        "canonical_alias": "root_lot_id",
        "raw_names": ["RootLotID"],
        "dtype": "string",
    }], wiki_doc_id=doc_id, actor="test")

    graph = kv.rebuild_graph()
    edges = {(e["source"], e["target"], e["relation"]) for e in graph["edges"]}

    assert ("doc:" + doc_id, "concept:root_lot_id", "describes") in edges
    assert ("concept:root_lot_id", "doc:" + doc_id, "described_by") in edges
