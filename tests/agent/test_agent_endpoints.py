from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
import polars as pl

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


def req(role: str = "user", username: str = "alice", page_admins: list[str] | None = None):
    return DummyRequest({"username": username, "role": role, "page_admins": page_admins or []})


def _install_agent_wiki_tmp(monkeypatch, tmp_path):
    root = tmp_path / "knowledge"
    raw = root / "raw"
    event = raw / "events"
    source = raw / "sources"
    wiki = root / "wiki"
    graph = root / "graph"
    index = root / "index"
    monkeypatch.setattr(agent.kv, "KNOWLEDGE_ROOT", root)
    monkeypatch.setattr(agent.kv, "RAW_DIR", raw)
    monkeypatch.setattr(agent.kv, "EVENT_DIR", event)
    monkeypatch.setattr(agent.kv, "SOURCE_DIR", source)
    monkeypatch.setattr(agent.kv, "WIKI_DIR", wiki)
    monkeypatch.setattr(agent.kv, "GRAPH_DIR", graph)
    monkeypatch.setattr(agent.kv, "INDEX_DIR", index)
    monkeypatch.setattr(agent.kv, "EVENTS_JSONL", event / "events.jsonl")
    monkeypatch.setattr(agent.kv, "SOURCES_JSONL", source / "sources.jsonl")
    monkeypatch.setattr(agent.kv, "WIKI_INDEX_FILE", index / "wiki_index.json")
    monkeypatch.setattr(agent.kv, "WIKI_LOG_JSONL", index / "wiki_log.jsonl")
    monkeypatch.setattr(agent.kv, "GRAPH_FILE", graph / "graph.json")
    return root


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


def test_prompt_history_reads_flow_data_activity(tmp_path, monkeypatch):
    activity = tmp_path / "flowi_activity.jsonl"
    rows = [
        {
            "timestamp": "2026-05-01T00:01:00+00:00",
            "username": "alice",
            "event": "chat",
            "fields": {
                "prompt": "A1001 24.SORT KNOB 스플릿테이블로 보여줘",
                "feature": "splittable",
                "intent": "wafer_split_at_step",
                "selected_function": "query_wafer_split_at_step",
                "result_status": "success",
                "elapsed_ms": 22,
                "answer": "snapshot ready",
                "source_ai": "agent_page",
                "client_run_id": "agent_page_1",
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

    out = agent.prompt_history(req(username="alice"), limit=20, user="bob")

    assert out["ok"] is True
    assert out["user"] == "alice"
    assert len(out["rows"]) == 1
    assert out["rows"][0]["prompt"].startswith("A1001")
    assert out["rows"][0]["feature"] == "splittable"
    assert out["rows"][0]["source_ai"] == "agent_page"


def test_prompt_review_uses_missing_slot_fallback(monkeypatch):
    monkeypatch.setattr(agent.llm_adapter, "is_available", lambda: False)

    out = agent.prompt_review(
        agent.PromptReviewReq(
            prompt="인폼 남겨줘",
            preview_row={
                "prompt": "인폼 남겨줘",
                "feature": "inform",
                "action": "register_inform_log",
                "status": "needs_input",
                "missing": ["root_lot_ids", "module", "note"],
            },
        ),
        req(username="alice"),
    )

    assert out["ok"] is True
    assert out["source"] == "fallback"
    assert out["llm"]["used"] is False
    assert out["deterministic_status"] == "needs_input"
    assert out["review"]["missing"] == ["root_lot_ids", "module", "note"]
    assert any("Inform" in q for q in out["review"]["ambiguous_questions"])


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


def test_agent_wiki_source_ingest_search_log_lint(tmp_path, monkeypatch):
    root = _install_agent_wiki_tmp(monkeypatch, tmp_path)

    with pytest.raises(HTTPException) as denied:
        agent.agent_wiki_create_source(
            agent.AgentWikiSourceReq(title="DIBL memo", content="DIBL source"),
            req(role="user"),
        )
    assert denied.value.status_code == 403

    created = agent.agent_wiki_create_source(
        agent.AgentWikiSourceReq(
            source_type="markdown",
            title="DIBL memo",
            content="# DIBL memo\n\nDIBL increases when CA resistance and short channel effects worsen.",
            tags=["DIBL", "CA"],
        ),
        req(role="admin", username="root"),
    )
    source = created["source"]
    assert (root / source["raw_path"]).is_file()

    listed_sources = agent.agent_wiki_sources(req(), q="DIBL", source_type="", limit=20)
    assert listed_sources["sources"][0]["source_id"] == source["source_id"]

    preview = agent.agent_wiki_ingest_preview(
        agent.AgentWikiIngestReq(source_ids=[source["source_id"]], title="DIBL maintained wiki", tags=["RCA"]),
        req(role="user"),
    )
    assert preview["ok"] is True
    assert preview["preview"]["kind"] == "agent_wiki"
    assert "DIBL" in preview["preview"]["summary"]

    with pytest.raises(HTTPException) as commit_denied:
        agent.agent_wiki_ingest_commit(
            agent.AgentWikiIngestReq(source_ids=[source["source_id"]], title="DIBL maintained wiki"),
            req(role="user"),
        )
    assert commit_denied.value.status_code == 403

    committed = agent.agent_wiki_ingest_commit(
        agent.AgentWikiIngestReq(
            source_ids=[source["source_id"]],
            doc_id=preview["preview"]["doc_id"],
            title=preview["preview"]["title"],
            summary=preview["preview"]["summary"],
            body=preview["preview"]["body"],
            tags=preview["preview"]["tags"],
        ),
        req(role="admin", username="root"),
    )
    doc = committed["doc"]
    assert doc["kind"] == "agent_wiki"
    assert (root / doc["path"]).is_file()
    assert (root / "index" / "wiki_index.json").is_file()

    search = agent.agent_wiki_search(req(), q="DIBL", limit=20)
    assert search["results"]
    assert search["results"][0]["doc_id"] == doc["doc_id"]

    page = agent.agent_wiki_page(req(), doc_id=doc["doc_id"])
    assert page["page"]["frontmatter"]["source_ids"] == [source["source_id"]]

    saved = agent.agent_wiki_page_save(
        agent.AgentWikiPageSaveReq(
            doc_id=doc["doc_id"],
            kind="agent_wiki",
            title="DIBL updated wiki",
            summary="updated summary",
            body=page["page"]["body"] + "\n## Operator Edit\n\nverified",
            tags=["DIBL", "updated"],
        ),
        req(role="admin", username="root"),
    )
    assert saved["page"]["title"] == "DIBL updated wiki"
    saved_page = agent.agent_wiki_page(req(), doc_id=doc["doc_id"])
    assert saved_page["page"]["frontmatter"]["source_ids"] == [source["source_id"]]
    assert saved_page["page"]["body"].count("# DIBL updated wiki") == 1
    assert "updated" in saved_page["page"]["tags"]

    logs = agent.agent_wiki_log(req(), limit=20, action="")
    assert {row["action"] for row in logs["logs"]} >= {"source_register", "ingest_commit", "page_save"}

    with pytest.raises(HTTPException) as lint_denied:
        agent.agent_wiki_lint(req(role="user"))
    assert lint_denied.value.status_code == 403

    lint = agent.agent_wiki_lint(req(role="admin", username="root"))
    assert lint["ok"] is True
    assert lint["counts"]["pages"] == 1

    with pytest.raises(HTTPException) as delete_denied:
        agent.agent_wiki_page_delete(req(role="user"), agent.AgentWikiPageDeleteReq(doc_id=doc["doc_id"]))
    assert delete_denied.value.status_code == 403

    deleted = agent.agent_wiki_page_delete(req(role="admin", username="root"), agent.AgentWikiPageDeleteReq(doc_id=doc["doc_id"]))
    assert deleted["deleted"] is True
    assert not (root / doc["path"]).exists()
    assert agent.agent_wiki_search(req(), q="DIBL updated", limit=20)["results"] == []
    delete_logs = agent.agent_wiki_log(req(), limit=20, action="page_delete")
    assert delete_logs["logs"][0]["doc_id"] == doc["doc_id"]


def test_schema_relation_preview_and_admin_save_do_not_touch_sources(tmp_path, monkeypatch):
    db_root = tmp_path / "db"
    product_dir = db_root / "PRODA"
    product_dir.mkdir(parents=True)
    fab_file = product_dir / "part_0.parquet"
    ml_file = db_root / "ML_TABLE_PRODA.parquet"
    pl.DataFrame({
        "root_lot_id": ["A1000", "A1001"],
        "wafer_id": [1, 2],
        "step_id": ["AA", "BB"],
    }).write_parquet(fab_file)
    pl.DataFrame({
        "root_lot_id": ["A1000", "A1002"],
        "wafer_id": [1, 3],
        "knob": ["K1", "K2"],
    }).write_parquet(ml_file)
    before = {fab_file: fab_file.stat().st_mtime_ns, ml_file: ml_file.stat().st_mtime_ns}
    relation_file = tmp_path / "flow-data" / "schema_relations.json"
    monkeypatch.setattr(agent, "SCHEMA_RELATION_FILE", relation_file)
    monkeypatch.setattr(agent, "_relation_resolve_root", lambda _raw, default=None: db_root)

    preview = agent.schema_relation_preview(
        agent.SchemaRelationPreviewReq(sources=[
            agent.SchemaRelationSource(source_type="db", root="db_root", product="PRODA", label="FAB PRODA"),
            agent.SchemaRelationSource(source_type="file", root="db_root", file="ML_TABLE_PRODA.parquet", label="ML_TABLE PRODA"),
        ]),
        req(role="user"),
    )

    assert preview["ok"] is True
    assert preview["preview_only"] is True
    assert preview["saved"] is False
    assert not relation_file.exists()
    assert any(row["left_column"] == "root_lot_id" and row["right_column"] == "root_lot_id" for row in preview["candidates"])

    with pytest.raises(HTTPException) as denied:
        agent.schema_relation_save(agent.SchemaRelationSaveReq(candidates=preview["candidates"]), req(role="user"))
    assert denied.value.status_code == 403
    monkeypatch.setattr(agent, "is_page_admin", lambda username, page: username == "engineer" and page in {"diagnosis", "knowledge"})

    saved = agent.schema_relation_save(
        agent.SchemaRelationSaveReq(candidates=preview["candidates"][:2], note="checked by engineer"),
        req(role="user", username="engineer"),
    )

    assert saved["ok"] is True
    assert saved["raw_sources_mutated"] is False
    assert saved["relations"][0]["confirmed_by"] == "engineer"
    assert relation_file.exists()
    assert all(path.stat().st_mtime_ns == mtime for path, mtime in before.items())

    graph = agent.schema_relation_graph(req(role="user"))
    assert graph["relations"]
    assert graph["graph"]["edges"]

    deleted = agent.schema_relation_delete(
        agent.SchemaRelationDeleteReq(relation_ids=[graph["relations"][0]["relation_id"]], note="wrong edge"),
        req(role="user", username="engineer"),
    )
    assert deleted["ok"] is True
    assert deleted["raw_sources_mutated"] is False
    assert len(deleted["relations"]) == len(graph["relations"]) - 1
    assert all(path.stat().st_mtime_ns == mtime for path, mtime in before.items())


def test_schema_relation_scan_discovers_db_and_single_files(tmp_path, monkeypatch):
    db_root = tmp_path / "db"
    fab_dir = db_root / "1.RAWDATA_DB_FAB" / "PRODA"
    fab_dir.mkdir(parents=True)
    pl.DataFrame({"root_lot_id": ["A1000"], "wafer_id": [1], "step_id": ["AA"]}).write_parquet(fab_dir / "part.parquet")
    pl.DataFrame({"root_lot_id": ["A1000"], "wafer_id": [1], "KNOB_A": ["ON"]}).write_parquet(db_root / "ML_TABLE_PRODA.parquet")
    relation_file = tmp_path / "flow-data" / "schema_relations.json"
    monkeypatch.setattr(agent, "SCHEMA_RELATION_FILE", relation_file)

    monkeypatch.setattr(agent, "PATHS", SimpleNamespace(data_root=tmp_path / "flow-data", db_root=db_root, base_root=db_root))

    out = agent.schema_relation_scan(agent.SchemaRelationScanReq(max_sources=10, max_candidates=20), req(role="user"))

    assert out["ok"] is True
    assert out["preview_only"] is True
    assert out["discovered_count"] >= 2
    assert any({c["left_source_type"], c["right_source_type"]} == {"db", "file"} for c in out["candidates"])
    assert not relation_file.exists()
