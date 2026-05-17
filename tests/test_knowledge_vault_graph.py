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


def test_default_agent_wiki_seed_installs_only_missing_docs(tmp_path, monkeypatch):
    _isolate_knowledge(tmp_path, monkeypatch)
    seed_dir = tmp_path / "seed"
    seed_page = seed_dir / "agent_wiki" / "seed_doc.md"
    seed_page.parent.mkdir(parents=True)
    seed_page.write_text(
        """---
doc_id: default_seed_test_doc
kind: agent_wiki
title: Default Seed Test Doc
summary: 기본 seed 테스트 문서
actor: system_seed
tags: ["seed", "test"]
schema_type: default_agent_wiki_seed_v1
---

## Notes

처음 설치되는 기본 지식입니다.
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(kv, "DEFAULT_AGENT_WIKI_SEED_DIR", seed_dir)

    first = kv.ensure_default_agent_wiki_seed(actor="test")
    assert first["installed"] == 1
    doc = kv.get_doc("default_seed_test_doc")
    assert doc and doc["title"] == "Default Seed Test Doc"

    runtime_path = kv.WIKI_DIR / "agent_wiki" / "default_seed_test_doc.md"
    text = runtime_path.read_text(encoding="utf-8")
    runtime_path.write_text(text.replace("처음 설치되는 기본 지식입니다.", "사용자가 수정한 운영 지식입니다."), encoding="utf-8")

    second = kv.ensure_default_agent_wiki_seed(actor="test")
    assert second["installed"] == 0
    assert second["preserved"] == 1
    assert "사용자가 수정한 운영 지식입니다." in runtime_path.read_text(encoding="utf-8")


def test_actual_default_agent_wiki_seed_contains_gaa_docs(tmp_path, monkeypatch):
    _isolate_knowledge(tmp_path, monkeypatch)

    out = kv.ensure_default_agent_wiki_seed(actor="test")
    doc_ids = {row["doc_id"] for row in out["docs"]}

    assert "default_agent_wiki_seed_framework" in doc_ids
    assert "gaa_device_evolution_and_purpose" in doc_ids
    assert "semiconductor_eight_major_processes_for_gaa" in doc_ids
    assert "gaa_nanosheet_process_flow_and_failure_modes" in doc_ids
    assert "gaa_device_geometry_and_multi_vt_design" in doc_ids
    assert "gaa_beol_bspdn_power_delivery_basics" in doc_ids
