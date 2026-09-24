import json
from types import SimpleNamespace

import pytest
from core import product_semantics as sem, product_wiki as wiki, llm_adapter, data_chat


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    paths = SimpleNamespace(data_root=tmp_path / "data", db_root=tmp_path / "db")
    monkeypatch.setattr(wiki, "PATHS", paths)
    monkeypatch.setattr(sem, "PATHS", paths)
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)
    snap = {"product_names": ["AA", "BB"], "products": [{"product": "AA"}, {"product": "BB"}],
            "steps": [{"product": "AA", "module": "SD", "step_id": s, "step_desc": s} for s in ["A2", "A10", "A100"]],
            "measurements": [{"product": "AA", "module": m, "source_type": "INLINE", "step_id": s, "item_id": i}
                             for m,s,i in [("PC", "P10", "I1"), ("SD", "A10", "I2")]]}
    sem._snapshot_path().parent.mkdir(parents=True)
    sem._snapshot_path().write_text(json.dumps(snap), encoding="utf-8")
    monkeypatch.setattr(data_chat, "available_product_names", lambda: ["AA", "BB"])
    monkeypatch.setattr(data_chat, "available_split_product_names", lambda: ["AA", "BB"])
    return snap


def measurement(module="PC", term="PC CD1", alias="선폭"):
    return {"term": term, "module": module, "source_type": "INLINE", "step_id": "P10" if module == "PC" else "A10",
            "item_id": "I1" if module == "PC" else "I2", "aliases": [alias]}


def approve(row, actor="alice"):
    record = sem.propose("AA", "PC CD1 20nm로 하향", actor)
    return sem.confirm("AA", record["id"], {"measurements": [row]}, actor)


def test_raw_change_preserved_without_inventing_connection(catalog):
    record = sem.propose("AA", "PC CD1 20nm로 하향", "alice")
    assert record["source_text"] == "PC CD1 20nm로 하향"
    assert record["draft"]["measurements"][0]["step_id"] == ""
    assert sem.resolve_terms("AA", "PC CD1") == []
    with pytest.raises(ValueError, match="실제 DB"):
        sem.confirm("AA", record["id"], record["draft"], "alice")


def test_confirmation_requires_observed_tuple_not_individual_ids(catalog):
    record = sem.propose("AA", "PC CD1", "alice")
    row = measurement(); row["item_id"] = "I2"
    with pytest.raises(ValueError, match="실제 DB"):
        sem.confirm("AA", record["id"], {"measurements": [row]}, "alice")
    assert sem.records("AA")[0]["status"] == "pending"


def test_owner_and_single_confirmation(catalog):
    record = sem.propose("AA", "PC CD1", "alice")
    with pytest.raises(PermissionError):
        sem.confirm("AA", record["id"], {"measurements": [measurement()]}, "bob")
    saved = sem.confirm("AA", record["id"], {"measurements": [measurement()]}, "admin", manager=True)
    assert saved["created_by"] == "alice"
    assert saved["confirmed_by"] == "admin"
    with pytest.raises(wiki.Conflict):
        sem.confirm("AA", record["id"], saved["draft"], "admin", manager=True)


def test_structure_range_uses_actual_natural_step_order(catalog):
    record = sem.propose("AA", "SD 내 eSD A2~A100", "alice")
    draft = {"structures": [{"module": "SD", "path": "eSD", "step_start": "A2", "step_end": "A100", "aliases": ["에스디"]}]}
    saved = sem.confirm("AA", record["id"], draft, "alice")
    assert saved["draft"]["structures"][0]["step_ids"] == ["A2", "A10", "A100"]
    assert sem.resolve_terms("AA", "에스디 split")[0]["path"] == "eSD"
    assert not sem.resolve_terms("BB", "에스디 split")


def test_structure_rejects_unobserved_endpoint(catalog):
    record = sem.propose("AA", "SD eSD", "alice")
    with pytest.raises(ValueError, match="시작·끝"):
        sem.confirm("AA", record["id"], {"structures": [{"module": "SD", "path": "eSD", "step_start": "A2", "step_end": "A99"}]}, "alice")


def test_product_alias_conflict_never_selects_silently(catalog):
    sem.save_product_aliases("AA", ["테스트제품"], "admin")
    sem.save_product_aliases("BB", ["테스트제품"], "admin")
    assert sem.product_alias_candidates("테스트제품 위치", ["AA", "BB"]) == ["AA", "BB"]
    assert sem.product_alias_candidates("테스트제품 위치", ["AA"]) == ["AA"]
    with pytest.raises(ValueError):
        sem.save_product_aliases("PRODA", ["가짜"], "admin")


def test_home_shared_alias_asks_and_number_resumes_split_scope(catalog):
    approve(measurement())
    approve(measurement("SD", "SD CD1"))
    first = data_chat.execute("AA 선폭 split 해줘", {}, None)
    assert first["tool"]["missing"] == ["semantic_target"]
    second = data_chat.execute("1번", first["context"], None)
    assert second["tool"]["missing"] == ["split_conditions"]
    assert len(second["context"]["semantic_scope"]) == 1
    assert "pending_semantic_selection" not in second["context"]


def test_semantic_draft_is_not_used_as_confirmed_knowledge(catalog):
    sem.propose("AA", "PC CD1 20nm", "alice")
    reference = sem.prompt_context("AA", "PC CD1")
    assert reference["pending_terms"]
    assert reference["confirmed_semantics"] == []
    approve(measurement())
    reference = sem.prompt_context("AA", "선폭")
    assert reference["matched_terms"][0]["item_id"] == "I1"


def test_revised_term_supersedes_mapping_but_history_remains(catalog):
    approve(measurement(alias="oldalias"))
    approve(measurement(alias="newalias"))
    assert sem.resolve_terms("AA", "oldalias") == []
    assert len(sem.resolve_terms("AA", "newalias")) == 1
    assert len(sem.records("AA")) == 2
    assert "oldalias" not in str(sem.prompt_context("AA")["confirmed_semantics"])


def test_llm_draft_never_bypasses_db_validation(catalog, monkeypatch):
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(llm_adapter, "complete_json", lambda *a, **k: {"ok": True, "obj": {"measurements": [{**measurement(), "step_id": "MADE_UP"}], "structures": []}})
    record = sem.propose("AA", "PC CD1", "alice")
    assert record["status"] == "pending"
    with pytest.raises(ValueError):
        sem.confirm("AA", record["id"], record["draft"], "alice")


def test_bootstrap_reads_only_identifiers_and_writes_confidential(catalog, monkeypatch):
    from core import product_wiki_structure as structure
    root = sem.PATHS.db_root
    source = root / "INLINE" / "AA" / "sample.csv"
    source.parent.mkdir(parents=True)
    source.write_text("product,module,step_id,item_id,measurement_value\nAA,PC,P10,I1,999SECRET\n", encoding="utf-8")
    monkeypatch.setattr(structure, "matching_products", lambda: [])
    monkeypatch.setattr(structure, "mapping_source", lambda p: {"rows": [{"module": "PC", "step_id": "P10", "step_desc": "공정"}]})
    saved = sem.bootstrap("admin")
    assert saved["measurements"][0]["item_id"] == "I1"
    assert "999SECRET" not in sem._snapshot_path().read_text(encoding="utf-8")
    assert sem._snapshot_path().parent.name == "confidential"


def test_wiki_original_survives_semantic_service_failure(catalog, monkeypatch):
    monkeypatch.setattr(llm_adapter, "complete", lambda *a, **k: {"ok": False})
    monkeypatch.setattr(sem, "propose", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    saved = wiki.intake_entry("AA", 0, "PC CD1 20nm로 하향", "alice")
    assert saved["entries"][0]["source_text"] == "PC CD1 20nm로 하향"
    assert saved["semantic_proposal"] is None
    assert "원문은 저장" in saved["intake_warning"]


def test_admin_management_routes_and_author_confirmation(catalog):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import product_semantics as api
    app = FastAPI(); app.include_router(api.router)
    client = TestClient(app)
    assert client.post("/api/product-semantics/bootstrap").status_code in (401, 403)
    app.dependency_overrides[api.require_productwiki_manager] = lambda: {"username": "alice", "role": "admin"}
    app.dependency_overrides[api.require_access] = lambda: {"username": "bob", "role": "user"}
    saved = client.post("/api/product-semantics/propose", json={"product": "AA", "text": "PC CD1"})
    assert saved.status_code == 200
    confirm = client.post("/api/product-semantics/confirm", json={"product": "AA", "id": saved.json()["id"], "draft": {"measurements": [measurement()]}})
    assert confirm.status_code == 403


def test_inline_matching_scope_headers_and_fab_separation(catalog):
    sem.PATHS.db_root.joinpath("Inline_matching.csv").write_text(
        ' PRODUCT , STEP_ID , ITEM_ID ,MODULE,ITEM_DESC\n"AA,BB",P10,I1,PC,Gate CD\nBB,X1,J1,SD,Other\n', encoding="utf-8")
    catalog["measurements"].append({"product": "AA", "module": "FAB_MOD", "source_type": "FAB", "step_id": "P10", "item_id": "I1"})
    sem._snapshot_path().write_text(json.dumps(catalog), encoding="utf-8")
    sem.save_item_alias("AA", "P10", "I1", ["선폭"], "admin", module="PC")
    rows = sem.overview("AA")["measurements"]
    assert not any(r["step_id"] == "X1" for r in rows)
    inline = next(r for r in rows if r["source_type"] == "INLINE" and r["step_id"] == "P10")
    assert inline["module"] == "PC" and inline["aliases"] == ["선폭"]
    assert next(r for r in rows if r["source_type"] == "FAB")["aliases"] == []


def test_alias_optimistic_concurrency_and_validation(catalog):
    first = sem.save_product_aliases("AA", ["alias", "ALIAS"], "admin", "")
    assert first["aliases"] == ["alias"]
    with pytest.raises(wiki.Conflict):
        sem.save_product_aliases("AA", [], "other", "")
    assert sem.save_product_aliases("AA", [], "admin", first["updated_at"])["aliases"] == []
    item = sem.save_item_alias("AA", "P10", "I1", ["선폭"], "admin", expected_updated_at="")
    with pytest.raises(wiki.Conflict):
        sem.save_item_alias("AA", "P10", "I1", [], "other", expected_updated_at="")
    assert sem.save_item_alias("AA", "P10", "I1", [], "admin", expected_updated_at=item["updated_at"])["aliases"] == []
    with pytest.raises(ValueError):
        sem.save_item_alias("AA", "", "", [], "admin")
    with pytest.raises(ValueError):
        sem.save_item_alias("UNKNOWN", "P10", "I1", [], "admin")


def test_alias_context_is_product_scoped_and_identifier_safe(catalog):
    sem.save_item_alias("AA", "P10", "I1", ["Gate CD", "선폭"], "admin", module="PC")
    assert sem.resolve_terms("AA", "GateCD 변경")[0]["source_type"] == "INLINE"
    assert sem.resolve_terms("AA", "I10") == []
    assert sem.resolve_terms("BB", "Gate CD") == []
    context = sem.intake_reference("AA", "선폭")
    assert context["measurements"][0]["item_id"] == "I1"
    assert context["measurements"][0]["aliases"] == ["Gate CD", "선폭"]


def test_inline_live_mapping_retains_unambiguous_observed_module(catalog):
    sem.PATHS.db_root.joinpath("Inline_matching.csv").write_text(
        "product,step_id,item_id,item_desc\nAA,P10,I1,Gate CD\n", encoding="utf-8")
    row = next(r for r in sem.overview("AA")["measurements"] if r["item_id"] == "I1")
    assert row["module"] == "PC"
    sem.save_item_alias("AA", "P10", "I1", ["선폭"], "admin")
    assert sem.resolve_terms("AA", "선폭")[0]["module"] == "PC"


def test_alias_ambiguity_remains_explicit(catalog):
    for step, item in [("P10", "I1"), ("A10", "I2")]:
        sem.save_item_alias("AA", step, item, ["선폭"], "admin")
    assert len(sem.resolve_terms("AA", "선폭")) == 2
    assert sem.overview("AA")["diagnostics"]


def test_edited_issue_cannot_confirm_old_semantics(catalog, monkeypatch):
    monkeypatch.setattr(llm_adapter, "complete", lambda *a, **k: {"ok": False})
    first = wiki.intake_entry("AA", 0, "PC CD1 original", "alice", title="원제목")
    proposal = first["semantic_proposal"]
    second = wiki.intake_entry("AA", first["revision"], "PC CD1 revised", "alice", first["saved_entry_id"], title="수정제목")
    assert second["revision"] == 2
    assert next(r for r in sem.records("AA") if r["id"] == proposal["id"])["is_current"] is False
    with pytest.raises(wiki.Conflict, match="원문"):
        sem.confirm("AA", proposal["id"], {"measurements": [measurement()]}, "alice")
