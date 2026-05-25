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


def test_runtime_wiki_cleanup_selector_preserves_schema_docs_and_chart_rules(tmp_path, monkeypatch):
    _isolate_knowledge(tmp_path, monkeypatch)
    kv.upsert_doc(KnowledgeDoc(
        doc_id="knowledge_vault_overview",
        kind="ontology",
        title="Knowledge Vault Overview",
        body="internal skeleton",
    ))
    kv.upsert_doc(KnowledgeDoc(
        doc_id="agent_deep_eval_semiconductor_terms",
        kind="agent_wiki",
        title="Deep Eval Terms",
        body="test-only eval doc",
    ))
    kv.upsert_doc(KnowledgeDoc(
        doc_id="flowi_live_aaa_0ec01543_anchor_registry",
        kind="agent_wiki",
        title="Anchor Registry",
        body="temporary anchor registry",
    ))
    kv.upsert_doc(KnowledgeDoc(
        doc_id="proda_operating_overview",
        kind="product",
        title="PRODA demo overview",
        body="demo product page",
        frontmatter={"schema_type": kv.DEMO_OPERATIONAL_KNOWLEDGE_SCHEMA},
    ))
    kv.upsert_doc(KnowledgeDoc(
        doc_id="ml_table_proda.step_id",
        kind="schema_doc",
        title="ML_TABLE_PRODA step_id",
        body="schema doc is execution evidence",
        frontmatter={
            "schema_type": kv.DEMO_OPERATIONAL_KNOWLEDGE_SCHEMA,
            "relation_id": "ML_TABLE_PRODA",
            "column_refs": ["ML_TABLE_PRODA.step_id"],
        },
    ))
    kv.upsert_doc(KnowledgeDoc(
        doc_id="dashboard_chart_generation_rules",
        kind="agent_wiki",
        title="Dashboard Chart Generation Rules",
        body="approved chart rule",
        frontmatter={"schema_type": "agent_llm_wiki_page_v1"},
    ))
    kv.upsert_doc(KnowledgeDoc(
        doc_id="default_agent_wiki_seed_framework",
        kind="agent_wiki",
        title="Default Agent Wiki Seed Framework",
        body="legacy seed",
    ))

    plan = kv.plan_runtime_wiki_cleanup()
    candidate_ids = {row["doc_id"] for row in plan["candidates"]}

    assert {
        "knowledge_vault_overview",
        "agent_deep_eval_semiconductor_terms",
        "flowi_live_aaa_0ec01543_anchor_registry",
        "proda_operating_overview",
        "default_agent_wiki_seed_framework",
    }.issubset(candidate_ids)
    assert "ml_table_proda.step_id" not in candidate_ids
    assert "dashboard_chart_generation_rules" not in candidate_ids

    result = kv.cleanup_runtime_wiki(apply=True, actor="test_cleanup")
    assert result["ok"] is True
    assert result["deleted_count"] == len(candidate_ids)
    assert Path(result["backup"]["backup_dir"]).is_dir()
    assert kv.get_doc("proda_operating_overview") is None
    assert kv.get_doc("ml_table_proda.step_id")
    assert kv.get_doc("dashboard_chart_generation_rules")


def test_runtime_wiki_clear_deletes_every_wiki_doc_after_backup(tmp_path, monkeypatch):
    _isolate_knowledge(tmp_path, monkeypatch)
    kv.upsert_doc(KnowledgeDoc(
        doc_id="operator_note",
        kind="agent_wiki",
        title="Operator Note",
        body="old note",
    ))
    kv.upsert_doc(KnowledgeDoc(
        doc_id="ml_table_proda.step_id",
        kind="schema_doc",
        title="ML_TABLE_PRODA step_id",
        body="schema doc",
    ))
    kv.upsert_doc(KnowledgeDoc(
        doc_id="knowledge_vault_overview",
        kind="ontology",
        title="Knowledge Vault Overview",
        body="internal skeleton",
    ))

    plan = kv.plan_runtime_wiki_clear()
    assert {row["doc_id"] for row in plan["candidates"]} == {
        "operator_note",
        "ml_table_proda.step_id",
        "knowledge_vault_overview",
    }

    result = kv.clear_runtime_wiki(apply=True, actor="test_clear")
    assert result["ok"] is True
    assert result["deleted_count"] == 3
    assert Path(result["backup"]["backup_dir"]).is_dir()
    assert kv.list_docs(limit=1000) == []
    assert not list(kv.WIKI_DIR.rglob("*.md"))


def test_wiki_graph_curated_view_hides_automatic_edges_and_keeps_approved_edges(tmp_path, monkeypatch):
    _isolate_knowledge(tmp_path, monkeypatch)
    kv.upsert_doc(KnowledgeDoc(
        doc_id="schema_rel_step_id",
        kind="schema_doc",
        title="REL step_id",
        body="step_id describes process step.",
        frontmatter={"relation_id": "REL", "column_refs": ["REL.step_id"]},
    ))
    kv.upsert_doc(KnowledgeDoc(
        doc_id="operator_rule",
        kind="agent_wiki",
        title="Operator Rule",
        body="Use schema relation for routing.",
        entity={"product": "PRODA", "root_lot_id": "A1001", "wafer_id": "W07"},
        frontmatter={
            "related_doc_ids": ["schema_rel_step_id"],
            "relations": {"schema_rel_step_id": "uses_schema"},
        },
    ))
    kv.merge_schema_column_catalog([{
        "relation_id": "REL",
        "column": "step_id",
        "canonical_alias": "step_id",
        "raw_names": ["STEP_ID"],
        "dtype": "string",
    }], wiki_doc_id="schema_rel_step_id", actor="test")
    kv.append_event({
        "source_type": "manual",
        "source_id": "event-source",
        "title": "PRODA event",
        "summary": "runtime event",
        "entity": {"product": "PRODA", "root_lot_id": "A1001", "wafer_id": "W07"},
    })

    full = kv.rebuild_graph()
    curated = kv.wiki_graph_view(full)
    full_node_kinds = {row["kind"] for row in full["nodes"]}
    curated_node_kinds = {row["kind"] for row in curated["nodes"]}
    curated_edges = {(row["source"], row["target"], row["relation"], row.get("evidence") or "") for row in curated["edges"]}

    assert {"event", "product", "lot", "wafer"}.intersection(full_node_kinds)
    assert {"event", "product", "lot", "wafer"}.isdisjoint(curated_node_kinds)
    assert curated_node_kinds.issubset({"wiki_doc", "schema_column"})
    assert all((row.get("evidence") or "") in kv.CURATED_WIKI_GRAPH_EVIDENCE for row in curated["edges"])
    assert ("doc:operator_rule", "doc:schema_rel_step_id", "uses_schema", "frontmatter:related_doc_ids") in curated_edges
    assert ("doc:schema_rel_step_id", "concept:step_id", "describes", "schema_relations:column_catalog") in curated_edges
    assert kv.wiki_graph_view(full, view="full")["counts"] == full["counts"]


def test_agent_wiki_ingest_uses_only_explicit_related_docs(tmp_path, monkeypatch):
    _isolate_knowledge(tmp_path, monkeypatch)
    kv.upsert_doc(KnowledgeDoc(
        doc_id="existing_related_doc",
        kind="agent_wiki",
        title="Existing Related Doc",
        summary="same keyword",
        body="same keyword body",
        tags=["same"],
    ))

    preview = kv.preview_agent_wiki_ingest({
        "title": "New same keyword note",
        "content": "same keyword operational note",
        "tags": ["same"],
    })
    assert preview["related_doc_ids"] == []
    assert "## Related Pages" not in preview["body"]

    explicit = kv.preview_agent_wiki_ingest({
        "title": "Explicit relation note",
        "content": "approved relation note",
        "related_doc_ids": ["existing_related_doc"],
        "relations": {"existing_related_doc": "supports"},
    })
    assert explicit["related_doc_ids"] == ["existing_related_doc"]
    assert explicit["relations"] == {"existing_related_doc": "supports"}
    assert "## Related Pages" in explicit["body"]


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


def test_actual_default_agent_wiki_seed_ships_no_background_docs(tmp_path, monkeypatch):
    _isolate_knowledge(tmp_path, monkeypatch)

    out = kv.ensure_default_agent_wiki_seed(actor="test")

    assert out["installed"] == 0
    assert out["docs"] == []
    assert kv.list_docs(limit=1000) == []


def test_default_agent_wiki_seed_graph_hub_is_absent_when_no_seed_docs(tmp_path, monkeypatch):
    _isolate_knowledge(tmp_path, monkeypatch)

    kv.ensure_default_agent_wiki_seed(actor="test")
    graph = kv.rebuild_graph()
    nodes = {row["id"]: row for row in graph["nodes"]}

    assert graph["schema_version"] == kv.GRAPH_SCHEMA_VERSION
    assert "concept:default_agent_wiki_seed" not in nodes
    assert all(not str(node_id).startswith("doc:gaa_") for node_id in nodes)


def test_get_graph_rebuilds_stale_cached_wiki_docs(tmp_path, monkeypatch):
    _isolate_knowledge(tmp_path, monkeypatch)
    kv.upsert_doc(KnowledgeDoc(
        doc_id="first_doc",
        kind="agent_wiki",
        title="First Doc",
        summary="first",
        body="first body",
    ))
    first = kv.rebuild_graph()
    assert first["counts"]["docs"] == 1

    kv.upsert_doc(KnowledgeDoc(
        doc_id="second_doc",
        kind="agent_wiki",
        title="Second Doc",
        summary="second",
        body="second body",
    ))
    refreshed = kv.get_graph(rebuild_if_missing=True, rebuild_if_stale=True)

    assert refreshed["counts"]["docs"] == 2
    assert any(row["id"] == "doc:second_doc" for row in refreshed["nodes"])
