from __future__ import annotations

import asyncio
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
from app_v2.modules.semantic_learning import inbox as semantic_inbox  # noqa: E402


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
    ontology = root / "ontology"
    monkeypatch.setattr(agent.kv, "KNOWLEDGE_ROOT", root)
    monkeypatch.setattr(agent.kv, "RAW_DIR", raw)
    monkeypatch.setattr(agent.kv, "EVENT_DIR", event)
    monkeypatch.setattr(agent.kv, "SOURCE_DIR", source)
    monkeypatch.setattr(agent.kv, "WIKI_DIR", wiki)
    monkeypatch.setattr(agent.kv, "GRAPH_DIR", graph)
    monkeypatch.setattr(agent.kv, "INDEX_DIR", index)
    monkeypatch.setattr(agent.kv, "ONTOLOGY_DIR", ontology)
    monkeypatch.setattr(agent.kv, "EVENTS_JSONL", event / "events.jsonl")
    monkeypatch.setattr(agent.kv, "SOURCES_JSONL", source / "sources.jsonl")
    monkeypatch.setattr(agent.kv, "WIKI_INDEX_FILE", index / "wiki_index.json")
    monkeypatch.setattr(agent.kv, "WIKI_LOG_JSONL", index / "wiki_log.jsonl")
    monkeypatch.setattr(agent.kv, "GRAPH_FILE", graph / "graph.json")
    monkeypatch.setattr(agent.kv, "AI_ONTOLOGY_FILE", ontology / "ai_ontology.json")
    monkeypatch.setattr(agent.kv, "SCHEMA_RELATION_FILE", tmp_path / "schema_relations.json")
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


def test_agent_knowledge_overview_combines_runtime_sources(tmp_path, monkeypatch):
    root = _install_agent_wiki_tmp(monkeypatch, tmp_path)
    monkeypatch.setattr(semantic_inbox, "INBOX_DIR", tmp_path / "semantic" / "proposals")
    activity = tmp_path / "flowi_activity.jsonl"
    activity.write_text(
        json.dumps({
            "timestamp": "2026-05-01T00:03:00+00:00",
            "username": "alice",
            "event": "chat",
            "fields": {
                "prompt": "DIBL split 영향 정리해줘",
                "feature": "knowledge",
                "intent": "knowledge_impact_context",
                "selected_function": "knowledge.impact_context.lookup",
                "result_status": "success",
                "answer": "DIBL split impact",
            },
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(agent.flowi_llm, "FLOWI_ACTIVITY_FILE", activity)
    monkeypatch.setattr(agent.semi, "rag_knowledge_view", lambda *_args, **_kwargs: {
        "knowledge_cards": [],
        "causal_edges": [],
        "runtime_knowledge": [],
    })
    monkeypatch.setattr(agent.semi, "all_historical_cases", lambda: [])
    monkeypatch.setattr(agent.flowi_llm, "_flowi_promoted_knowledge_items", lambda limit=200: [])

    agent.kv.upsert_doc(agent.KnowledgeDoc(
        doc_id="dibl_runtime_note",
        kind="agent_wiki",
        title="DIBL runtime note",
        summary="DIBL split impact maintained page",
        body="DIBL split impact maintained page",
        actor="root",
        tags=["DIBL"],
    ))
    agent.kv.register_agent_wiki_source({
        "source_type": "markdown",
        "title": "DIBL source memo",
        "content": "DIBL source memo from meeting",
        "tags": ["DIBL"],
        "actor": "root",
    })
    agent.kv.append_event({
        "event_type": "split_impact",
        "source_type": "meeting",
        "source_id": "meeting-1",
        "title": "DIBL split impact",
        "summary": "DIBL impact discussed in meeting",
        "actor": "alice",
        "entity": {"product": "PRODA"},
        "tags": ["DIBL"],
    })
    semantic_inbox.enqueue_proposal({
        "term": "DIBL drift",
        "category": "new_canonical",
        "confidence": 0.8,
        "rationale": "DIBL drift was mentioned repeatedly",
        "origin": {"kind": "meeting", "ref": "meeting-1"},
    })

    out = agent.agent_knowledge_overview(req(username="alice"), q="DIBL", kind="", limit=20)

    assert out["ok"] is True
    assert out["counts"]["pending_semantic_proposals"] == 1
    assert out["pending_semantic_proposals"][0]["term"] == "DIBL drift"
    assert out["recent_wiki_pages"][0]["doc_id"] == "dibl_runtime_note"
    assert out["recent_wiki_sources"][0]["title"] == "DIBL source memo"
    assert out["recent_knowledge_events"][0]["event_type"] == "split_impact"
    assert out["recent_prompt_history"][0]["prompt"].startswith("DIBL split")
    assert {row["kind"] for row in out["recent_items"]} >= {
        "semantic_proposal",
        "wiki_page",
        "wiki_source",
        "knowledge_event",
        "prompt_history",
    }
    assert root.exists()


def test_workflow_shared_templates_require_admin(tmp_path, monkeypatch):
    monkeypatch.setattr(agent.wf_templates, "_DIR", tmp_path / "workflows")
    payload = {
        "key": "personal_dibl_review",
        "title": "DIBL 개인 질문 설계",
        "trigger": {"prompt_contains": ["DIBL"], "intent_in": ["knowledge_impact_context"], "slots_required": ["product"]},
        "steps": [{"unit_ai": "tracker", "action": "lookup", "bind_slots": ["product"]}],
        "shared": False,
    }

    saved = agent.workflows_save(agent.WorkflowSaveReq(**payload), req(username="alice"))
    assert saved["template"]["owner"] == "alice"
    assert saved["template"]["shared"] is False

    with pytest.raises(HTTPException) as update_denied:
        agent.workflows_save(
            agent.WorkflowSaveReq(**{**payload, "title": "Bob overwrite"}),
            req(username="bob"),
        )
    assert update_denied.value.status_code == 403

    with pytest.raises(HTTPException) as denied:
        agent.workflows_save(
            agent.WorkflowSaveReq(**{**payload, "key": "shared_dibl_review", "shared": True}),
            req(username="alice"),
        )
    assert denied.value.status_code == 403

    admin_saved = agent.workflows_save(
        agent.WorkflowSaveReq(**{**payload, "key": "shared_dibl_review", "shared": True}),
        req(role="admin", username="root"),
    )
    assert admin_saved["template"]["owner"] == "root"
    assert admin_saved["template"]["shared"] is True


def test_workflow_test_returns_runtime_plan_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(agent.wf_templates, "_DIR", tmp_path / "workflows")
    agent.workflows_save(
        agent.WorkflowSaveReq(
            key="knob_read",
            title="KNOB read",
            trigger={"prompt_contains": ["KNOB"], "intent_in": ["knob_analysis"]},
            steps=[{"unit_ai": "splittable", "action": "knob_impact", "bind_slots": ["product", "root_lot_ids"]}],
            shared=False,
        ),
        req(username="alice"),
    )

    out = agent.workflows_test(
        agent.WorkflowTestReq(prompt="PRODA A1000 #21 KNOB 확인", intent="knob_analysis"),
        req(username="alice"),
    )

    assert out["ok"] is True
    assert out["matched"]["key"] == "knob_read"
    assert out["runtime_plan"][0]["unit_ai"] == "splittable"
    assert out["runtime_plan"][0]["policy"] == "read_only"
    assert out["guardrail"]["status"] == "allowed"


def test_unit_ai_runtime_endpoints_validate_unknown_key():
    with pytest.raises(HTTPException) as exc:
        agent.unit_ai_runtime_blueprint("missing_unit", req())
    assert exc.value.status_code == 404


def test_unit_ai_runtime_run_passes_selected_scope(monkeypatch):
    captured = {}

    class DummyRun:
        def model_dump(self, mode="json"):
            return {"goal": "hello", "plan": [], "results": [], "events": [], "conclusion": {}}

    async def fake_run(req_obj, username):
        captured["unit_ai_scope"] = req_obj.unit_ai_scope
        captured["context_scope"] = req_obj.context.get("unit_ai_scope")
        captured["username"] = username
        return DummyRun()

    monkeypatch.setattr(agent, "run_agent_runtime_once", fake_run)

    out = asyncio.run(agent.unit_ai_runtime_run(
        "filebrowser",
        agent.AgentRuntimeRequest(goal="hello", max_terms=8),
        req(username="alice"),
    ))

    assert out["ok"] is True
    assert out["unit_ai_scope"] == "filebrowser"
    assert captured == {
        "unit_ai_scope": "filebrowser",
        "context_scope": "filebrowser",
        "username": "alice",
    }


def test_unit_ai_runtime_improvement_proposals_are_readable_but_apply_gated():
    payload = agent.UnitAIRuntimeImprovementReq(run={
        "goal": "mystery_runtime_term filebrowser로 조회",
        "semantic": {
            "goal": "mystery_runtime_term filebrowser로 조회",
            "tokens": ["mystery_runtime_term", "filebrowser"],
            "normalized_terms": {"filebrowser": "filebrowser"},
            "intent": "general_orchestration",
            "coverage": 0.1,
            "candidates": [],
        },
        "plan": [{
            "agent_id": "filebrowser.inspect",
            "unit_ai": "filebrowser",
            "action": "inspect",
            "missing_slots": [],
        }],
        "results": [{
            "agent_id": "filebrowser.inspect",
            "status": "skipped",
            "handled": False,
            "guardrail": {"status": "no_handler"},
            "warnings": ["no_handler"],
        }],
        "conclusion": {"warnings": []},
        "events": [],
    })

    user_out = agent.unit_ai_runtime_improvement_proposals("filebrowser", payload, req(username="alice"))
    admin_out = agent.unit_ai_runtime_improvement_proposals("filebrowser", payload, req(role="admin", username="root"))

    assert user_out["ok"] is True
    assert user_out["can_apply"] is False
    assert admin_out["can_apply"] is True
    assert {row["target"] for row in user_out["proposals"]} >= {"semantic_alias", "semantic_intent", "feature_md", "workflow_template"}
    assert all(row["approval_required"] is True for row in user_out["proposals"])
    assert all(row["can_apply"] is False for row in user_out["proposals"])
    assert all(row["can_apply"] is True for row in admin_out["proposals"])


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
    assert saved["graph_counts"]["docs"] == 1
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


def test_schema_single_file_registers_vehicle_matching_catalog(tmp_path, monkeypatch):
    _install_agent_wiki_tmp(monkeypatch, tmp_path)
    db_root = tmp_path / "db"
    db_root.mkdir(parents=True)
    vehicle_file = db_root / "Vehicle_matching.csv"
    vehicle_file.write_text(
        "step_id,function_id,vehicle_name\n"
        "AA100200,FN_SORT,Sort vehicle\n",
        encoding="utf-8",
    )
    flow_data = tmp_path / "flow-data"
    relation_file = tmp_path / "schema_relations.json"
    monkeypatch.setattr(agent, "PATHS", SimpleNamespace(data_root=flow_data, db_root=db_root, base_root=db_root))
    monkeypatch.setattr(agent, "SCHEMA_RELATION_FILE", relation_file)
    monkeypatch.setattr(agent.kv, "SCHEMA_RELATION_FILE", relation_file)

    preview = agent.schema_doc_single_file_preview(
        agent.SchemaSingleFilePreviewReq(
            source=agent.SchemaRelationSource(source_type="file", root="db_root", file="Vehicle_matching.csv", label="Vehicle_matching"),
            sample_rows=5,
        ),
        req(role="user"),
    )

    assert preview["ok"] is True
    assert preview["preview_only"] is True
    assert preview["source"]["row_count"] == 1
    assert "function_id" in preview["source"]["columns"]

    registered = agent.schema_doc_single_file_register(
        agent.SchemaSingleFileRegisterReq(
            source=agent.SchemaRelationSource(source_type="file", root="db_root", file="Vehicle_matching.csv", label="Vehicle_matching"),
            purpose="matching",
            key_columns=["step_id"],
            output_columns=["function_id"],
            title="Vehicle matching",
        ),
        req(role="admin", username="engineer"),
    )

    assert registered["ok"] is True
    assert registered["raw_sources_mutated"] is False
    lookup = agent.kv.lookup_term("function_id")
    assert any(row.get("relation_id") == "Vehicle_matching" and row.get("column") == "function_id" for row in lookup["columns"])
    assert relation_file.exists()
