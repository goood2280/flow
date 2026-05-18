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
from routers import knowledge as knowledge_router  # noqa: E402


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


def test_term_endpoint_returns_docs_and_columns(tmp_path, monkeypatch):
    _isolate_knowledge(tmp_path, monkeypatch)
    doc_id = "ml_table_prodx.wafer_id"
    kv.upsert_doc(KnowledgeDoc(
        doc_id=doc_id,
        kind="schema_doc",
        title="ML_TABLE_PRODX wafer_id",
        summary="Wafer identifier",
        body="wafer_id identifies a wafer.",
        frontmatter={"relation_id": "ML_TABLE_PRODX", "column_refs": ["ML_TABLE_PRODX.wafer_id"]},
    ))
    kv.merge_schema_column_catalog([{
        "relation_id": "ML_TABLE_PRODX",
        "column": "wafer_id",
        "canonical_alias": "wafer_id",
        "raw_names": ["WaferID"],
        "dtype": "string",
    }], wiki_doc_id=doc_id, actor="test")

    out = knowledge_router.term_lookup("wafer_id", limit=30)

    assert out["term"] == "wafer_id"
    assert len(out["columns"]) >= 1
    assert len(out["docs"]) >= 1
    assert out["columns"][0]["column"] == "wafer_id"


def test_term_lookup_resolves_root_lot_id_english_and_korean_aliases(tmp_path, monkeypatch):
    _isolate_knowledge(tmp_path, monkeypatch)
    doc_id = "ml_table_prodx.root_lot_id"
    kv.upsert_doc(KnowledgeDoc(
        doc_id=doc_id,
        kind="schema_doc",
        title="ML_TABLE_PRODX root_lot_id",
        summary="RootLotID identifies the root lot key.",
        body="Dot lot의 앞 5자리를 저장하는 컬럼입니다.",
        frontmatter={"relation_id": "ML_TABLE_PRODX", "column_refs": ["ML_TABLE_PRODX.root_lot_id"]},
    ))
    kv.merge_schema_column_catalog([{
        "relation_id": "ML_TABLE_PRODX",
        "column": "root_lot_id",
        "canonical_alias": "root_lot_id",
        "raw_names": ["RootLotID", "ROOT_LOT_ID"],
        "dtype": "string",
    }], wiki_doc_id=doc_id, actor="test")

    for term in ("root lot id", "RootLotID", "루트랏아이디", "루트랏 id", "루트랏아이디가"):
        out = knowledge_router.term_lookup(term, limit=30)
        assert any(row["column"] == "root_lot_id" for row in out["columns"]), term
        assert out["columns"][0]["column"] == "root_lot_id", term
        assert any(row["doc_id"] == doc_id for row in out["docs"]), term


def test_term_lookup_reads_new_issue_and_meeting_wiki_body_tokens(tmp_path, monkeypatch):
    _isolate_knowledge(tmp_path, monkeypatch)
    kv.upsert_doc(KnowledgeDoc(
        doc_id="issue_knob_beta_rule",
        kind="issue",
        title="Issue note",
        summary="New issue knowledge",
        body="MILKY_KNOB_BETA는 SORT hold 이슈에서 임시로 쓰는 knob 판정값입니다.",
        tags=["issue", "KNOB"],
    ))
    kv.upsert_doc(KnowledgeDoc(
        doc_id="meeting_vehicle_review",
        kind="meeting",
        title="Vehicle review meeting",
        summary="Meeting notes",
        body="회의내용: VEHICLE_FN_SORT는 Vehicle_matching.csv의 function_id 검증 항목입니다.",
        tags=["meeting", "Vehicle_matching"],
    ))

    issue_out = knowledge_router.term_lookup("MILKY_KNOB_BETA", limit=30)
    meeting_out = knowledge_router.term_lookup("VEHICLE_FN_SORT", limit=30)

    assert any(row["doc_id"] == "issue_knob_beta_rule" for row in issue_out["docs"])
    assert any(row["doc_id"] == "meeting_vehicle_review" for row in meeting_out["docs"])
