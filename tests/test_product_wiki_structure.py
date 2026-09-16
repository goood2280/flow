import csv
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from core import product_wiki as wiki
from core import product_wiki_structure as structure
from routers import product_wiki as api


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    db = tmp_path / "Fab"
    db.mkdir()
    paths = SimpleNamespace(data_root=tmp_path / "data", db_root=db)
    monkeypatch.setattr(wiki, "PATHS", paths)
    return paths


def write_mapping(paths, rows, headers=None):
    headers = headers or ["vehicle", "product", "step_id", "step_desc", "module"]
    with (paths.db_root / "Vehicle_matching.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def row(module="GATE", path="Etch", step_ids=None, description=""):
    return {"module": module, "path": path, "step_ids": step_ids or [], "description": description}


def test_mapping_source_exact_scope_dotted_cells_and_priority(isolated):
    write_mapping(isolated, [
        {"vehicle": "VH_A", "product": "prodA.prodB", "step_id": "S1", "step_desc": "ETCH", "module": "GATE"},
        {"vehicle": "VH_A", "product": "prodA", "step_id": "S2", "step_desc": "A_ONLY", "module": "PHOTO"},
        {"vehicle": "VH_MASK", "product": "prodZ", "step_id": "S3", "step_desc": "MASK_ONLY", "module": "MASK"},
        {"vehicle": "VH_A", "product": "prodA", "step_id": "S1", "step_desc": "DUP", "module": "OTHER"},
    ])
    result = structure.mapping_source("PRODB")
    assert [r["step_id"] for r in result["rows"]] == ["S1"]
    assert result["rows"][0]["step_desc"] == "ETCH"
    # A populated product cell has priority over vehicle/mask fallback.
    assert structure.mapping_source("VH_A")["rows"] == []
    write_mapping(isolated, [
        {"vehicle": "VH_A", "product": "", "step_id": "S9", "step_desc": "COMMON", "module": "GATE"},
        {"vehicle": "", "mask": "MASK_A", "product": "", "step_id": "S8", "step_desc": "MASK", "module": "MASK"},
    ], ["vehicle", "mask", "product", "step_id", "step_desc", "module"])
    assert [r["step_id"] for r in structure.mapping_source("VH_A")["rows"]] == ["S9"]
    assert [r["step_id"] for r in structure.mapping_source("MASK_A")["rows"]] == ["S8"]


def test_mapping_case_insensitive_headers_and_missing_module_warning(isolated):
    write_mapping(isolated, [{"VEHICLE": "VH_A", "PRODUCT": "PRODA", "STEP_ID": "S1", "STEP_DESC": "ETCH"}],
                  ["VEHICLE", "PRODUCT", "STEP_ID", "STEP_DESC"])
    result = structure.mapping_source("proda")
    assert result["rows"] == [{"module": "", "step_id": "S1", "step_desc": "ETCH"}]
    assert "module" in result["warning"]


def test_structure_save_history_noop_and_unknown_orphan_preservation(isolated):
    write_mapping(isolated, [{"vehicle": "VH_A", "product": "PRODA", "step_id": "S1", "step_desc": "ETCH", "module": "GATE"}])
    saved = structure.save("PRODA", 0, [row(step_ids=["S1"])], "admin", manager=True)
    assert saved["revision"] == 1 and len(structure.history("PRODA")) == 1
    again = structure.save("PRODA", 1, [row(step_ids=["S1"])], "admin", manager=True)
    assert again["revision"] == 1 and len(structure.history("PRODA")) == 1
    edited = structure.save("PRODA", 1, [row(path="Etch2", step_ids=["S1"])], "admin", manager=True)
    assert edited["revision"] == 2
    assert [item["revision"] for item in structure.history("PRODA")] == [2, 1]
    # Existing links remain valid when the reference mapping later removes the step.
    write_mapping(isolated, [])
    preserved = structure.save("PRODA", 2, [row(path="Etch2", step_ids=["S1"])], "admin", manager=True)
    assert preserved["revision"] == 2
    assert preserved["rows"][0]["step_ids"] == ["S1"]
    assert preserved["mapping"]["rows"] == []


def test_structure_transitions_round_trip_history_and_noop(isolated):
    write_mapping(isolated, [
        {"vehicle": "VH_A", "product": "PRODA", "step_id": "S1", "step_desc": "CONTACT", "module": "MOL"},
        {"vehicle": "VH_A", "product": "PRODA", "step_id": "S2", "step_desc": "PLUG", "module": "MOL"},
    ])
    changes = [
        {"order": 1, "module": "MOL", "from_path": "", "to_path": "Contact", "relation": "생성", "description": "초기 구조 생성", "step_ids": ["S1"]},
        {"order": 2, "module": "MOL", "from_path": "Contact", "to_path": "Contact/Plug", "relation": "충전 후 생성", "description": "Split 영향과 함께 Plug가 채워짐", "step_ids": ["S2"]},
        {"order": 3, "module": "MOL", "from_path": "Contact/Plug", "to_path": "", "relation": "제거", "description": "다음 공정에서 소멸", "step_ids": []},
    ]
    saved = structure.save("PRODA", 0, [row(module="MOL", path="Contact", step_ids=["S1"]), row(module="MOL", path="Contact/Plug", step_ids=["S2"])], "admin", manager=True, transitions=changes)
    assert saved["revision"] == 1
    assert saved["transitions"] == changes
    assert structure.history("PRODA")[0]["transitions"] == changes
    again = structure.save("PRODA", 1, saved["rows"], "admin", manager=True, transitions=changes)
    assert again["revision"] == 1
    with pytest.raises(ValueError, match="이전 구조 또는 다음 구조"):
        structure.save("PRODA", 1, saved["rows"], "admin", manager=True, transitions=[{"module": "MOL", "from_path": "", "to_path": ""}])
    with pytest.raises(ValueError, match="순서는"):
        structure.save("PRODA", 1, saved["rows"], "admin", manager=True, transitions=[{"order": 0, "module": "MOL", "from_path": "Contact", "to_path": "Plug"}])
    with pytest.raises(ValueError, match="없는 Step ID"):
        structure.save("PRODA", 1, saved["rows"], "admin", manager=True, transitions=[{"module": "MOL", "from_path": "Contact", "to_path": "Plug", "step_ids": ["TYPO"]}])


def test_intake_context_includes_relevant_structure_transitions(isolated):
    write_mapping(isolated, [{"vehicle": "VH_A", "product": "PRODA", "step_id": "S1", "step_desc": "CONTACT", "module": "MOL"}])
    structure.save("PRODA", 0, [row(module="MOL", path="Contact", step_ids=["S1"])], "admin", manager=True,
                   transitions=[{"order": 1, "module": "MOL", "from_path": "", "to_path": "Contact", "relation": "생성", "description": "생성 순서"}])
    context = structure.intake_context("PRODA", "MOL Contact S1")
    assert len(context["transitions"]) == 1
    assert context["transitions"][0]["to_path"] == "Contact"
    assert structure.intake_context("PRODA", "unrelated text")["transitions"] == []


def test_duplicate_paths_and_unknown_step_ids_rejected(isolated):
    write_mapping(isolated, [{"vehicle": "VH_A", "product": "PRODA", "step_id": "S1", "step_desc": "ETCH", "module": "GATE"}])
    with pytest.raises(ValueError, match="중복"):
        structure.save("PRODA", 0, [row(step_ids=["S1"]), row(path="Etch", step_ids=["S1"])], "admin", manager=True)
    with pytest.raises(ValueError, match="없는 Step ID"):
        structure.save("PRODA", 0, [row(step_ids=["MISSING"])], "admin", manager=True)


def test_concurrent_and_stale_conflicts(isolated):
    write_mapping(isolated, [{"vehicle": "VH_A", "product": "PRODA", "step_id": "S1", "step_desc": "ETCH", "module": "GATE"}])
    def save(path):
        try:
            return structure.save("PRODA", 0, [row(path=path, step_ids=["S1"])], "admin", manager=True)
        except wiki.Conflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ["A", "B"]))
    assert sum(x is not None for x in results) == 1
    with pytest.raises(wiki.Conflict):
        structure.save("PRODA", 0, [row(step_ids=["S1"])], "admin", manager=True)


def test_router_get_post_and_server_manager_permission(isolated):
    write_mapping(isolated, [{"vehicle": "VH_A", "product": "PRODA", "step_id": "S1", "step_desc": "ETCH", "module": "GATE"}])
    request = api.StructureRequest(product="PRODA", expected_revision=0, rows=[api.StructureRow(module="GATE", path="Etch", step_ids=["S1"])], transitions=[api.StructureTransition(order=1, module="GATE", to_path="Etch", relation="생성")])
    with pytest.raises(HTTPException) as exc:
        api.save_product_structure(request, {"username": "user", "role": "user"})
    assert exc.value.status_code == 403
    out = api.save_product_structure(request, {"username": "root", "role": "admin"})
    assert out["revision"] == 1
    assert out["transitions"][0]["relation"] == "생성"
    assert api.product_structure("PRODA")["mapping"]["rows"][0]["step_id"] == "S1"


def test_router_http_dependency_enforces_permission(isolated):
    write_mapping(isolated, [{"vehicle": "VH_A", "product": "PRODA", "step_id": "S1", "step_desc": "ETCH", "module": "GATE"}])
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.require_access] = lambda: {"username": "user", "role": "user"}
    payload = {"product": "PRODA", "expected_revision": 0, "rows": [{"module": "GATE", "path": "Etch", "step_ids": ["S1"]}]}
    response = TestClient(app).post("/api/product-wiki/structure", json=payload)
    assert response.status_code == 403
    app.dependency_overrides[api.require_access] = lambda: {"username": "root", "role": "admin"}
    response = TestClient(app).post("/api/product-wiki/structure", json=payload)
    assert response.status_code == 200


def test_intake_context_boundaries_ambiguity_and_mapping_reread(isolated, monkeypatch):
    write_mapping(isolated, [
        {"vehicle": "VH_A", "product": "PRODA", "step_id": "S1", "step_desc": "ETCH", "module": "GATE"},
        {"vehicle": "VH_A", "product": "PRODA", "step_id": "S2", "step_desc": "ETCH", "module": "PHOTO"},
    ])
    structure.save("PRODA", 0, [row(module="GATE", path="Cell", step_ids=["S1"]), row(module="PHOTO", path="Cell", step_ids=["S2"])], "admin", manager=True)
    context = structure.intake_context("PRODA", "ETCHX S1")
    assert context["step_ids"] == ["S1"]
    assert structure.intake_context("PRODA", "S10")["step_ids"] == []
    ambiguous = structure.intake_context("PRODA", "ETCH")
    assert ambiguous["step_ids"] == []
    assert structure.intake_context("PRODA", "Cell")["paths"] == []
    assert structure.intake_context("PRODA", "GATE/Cell")["paths"] == ["GATE/Cell"]
    monkeypatch.setattr("core.llm_adapter.complete", lambda *a, **k: {"ok": True, "text": json.dumps({"kind": "fact", "title": "S1", "body": "S1", "evidence": "S1", "status": "open"})})
    saved = wiki.intake_entry("PRODA", 0, "S1 observed", "alice")
    assert saved["entries"][0]["source_text"] == "S1 observed"
    snapshot = saved["entries"][0]["reference_snapshot"]
    assert snapshot["mapping_rows"][0]["step_desc"] == "ETCH"
    write_mapping(isolated, [{"vehicle": "VH_A", "product": "PRODA", "step_id": "S1", "step_desc": "CHANGED", "module": "GATE"}])
    current = wiki.document("PRODA")["entries"][0]["reference_snapshot"]
    assert current == snapshot
    assert structure.document("PRODA")["mapping"]["rows"][0]["step_desc"] == "CHANGED"


def test_intake_context_caps_large_reference(isolated):
    write_mapping(isolated, [{"vehicle": "VH_A", "product": "PRODA", "step_id": f"S{i}", "step_desc": f"STEP{i}", "module": "GATE"} for i in range(200)])
    rows = [row(path=f"Cell{i}", step_ids=[f"S{i}"]) for i in range(200)]
    structure.save("PRODA", 0, rows, "admin", manager=True)
    context = structure.intake_context("PRODA", " ".join(f"S{i}" for i in range(200)))
    assert len(json.dumps(context, ensure_ascii=False)) <= 16000 or context["truncated"]
