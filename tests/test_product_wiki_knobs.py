import csv
from types import SimpleNamespace

import pytest

from app_v2.modules.splittable import rulebook_repository as rb
from core import product_wiki as wiki
from core import product_wiki_structure as structure


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    db = tmp_path / "Fab"
    db.mkdir()
    monkeypatch.setattr(wiki, "PATHS", SimpleNamespace(data_root=tmp_path / "data", db_root=db, base_root=db))
    monkeypatch.setattr(rb, "get_base_root", lambda: db)
    monkeypatch.setattr(rb.RulebookRepository, "load_schema", lambda self: rb._DEFAULT_RULEBOOK_SCHEMA)
    monkeypatch.setattr(structure, "_lot_purposes_from_lot_management", lambda product: {"a1001": "랏관리 목적"})
    _write(db / "Vehicle_matching.csv", ["product", "step_id", "function_step"], [
        {"product": "PRODA", "step_id": "AA100", "function_step": "CA_ETCH"},
        {"product": "PRODA", "step_id": "AA110", "function_step": "CA_FILL"},
        {"product": "PRODB", "step_id": "BB100", "function_step": "OTHER"},
    ])
    _write(db / "ppid_knob.csv", ["feature_name", "function_step", "rule_order", "operator", "value", "category"], [
        {"feature_name": "CA_DEEP", "function_step": "CA_ETCH", "rule_order": "R1", "operator": "eq", "value": "PP_ETCH_2", "category": ""},
        {"feature_name": "CA_FILL_NEW", "function_step": "CA_FILL", "rule_order": "R1", "operator": "in", "value": "PP_FILL_1,PP_FILL_2", "category": ""},
    ])
    return db


def _write(path, headers, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def knob(kid, name, steps, **extra):
    return {"id": kid, "name": name, "structure": "MOL/Contact/CA",
            "steps": [{"step_id": s, "ppid": p, "change": ""} for s, p in steps], **extra}


def test_knob_step_set_ppid_check_and_compensation_chain(isolated):
    knobs = [
        knob("k1", "CA deep etch", [("AA100", "PP_ETCH_2"), ("AA110", "PP_FILL_2")],
             side_effects="nRch 저하", por_status="por", por_ref="ECN-1",
             impacts={"yield": {"effect": "up", "note": "open 감소"}, "performance": {"effect": "down", "note": "nRch"}}),
        knob("k2", "nRch 보상 knob", [("AA100", "PP_ETCH_9")], compensates=["k1"], por_status="experiment"),
        knob("k3", "미매칭", [("ZZ999", "PP_X")]),
    ]
    doc = structure.save_knobs("PRODA", 0, knobs, "hol")

    assert doc["revision"] == 1
    checks = doc["ppid_checks"]
    assert [c["status"] for c in checks["k1"]] == ["registered", "registered"]
    assert checks["k1"][0]["features"] == ["CA_DEEP"]
    assert checks["k2"][0]["status"] == "step_only"
    assert checks["k3"][0]["status"] == "step_unmapped"
    assert doc["ppid_summary"] == {"steps": 4, "registered": 2, "missing": 2}

    # 코드 없는 knob 은 step 별로 따로 인식하고, 여러 step 이면 코드 안내 대상이다.
    sets = doc["groups"][0]["step_sets"]
    assert all(s["kind"] == "step" and len(s["step_ids"]) <= 1 for s in sets)
    assert {s["key"] for s in sets} >= {"AA100", "AA110", "ZZ999"}
    assert doc["code_hints"] == ["k1"]

    assert [[c["id"] for c in chain] for chain in doc["chains"]] == [["k1", "k2"]]
    assert doc["chains"][0][0]["side_effects"] == "nRch 저하"
    assert doc["por_counts"]["por"] == 1


def test_knob_validation_and_conflict(isolated):
    with pytest.raises(ValueError, match="보상 대상"):
        structure.save_knobs("PRODA", 0, [knob("k1", "A", [], compensates=["nope"])], "hol")
    with pytest.raises(ValueError, match="POR"):
        structure.save_knobs("PRODA", 0, [knob("k1", "A", [], por_status="done?")], "hol")
    structure.save_knobs("PRODA", 0, [knob("k1", "A", [])], "hol")
    with pytest.raises(wiki.Conflict):
        structure.save_knobs("PRODA", 0, [knob("k1", "B", [])], "kim")


def test_lot_table_links_knobs_by_name_and_shows_lot_management_purpose(isolated):
    structure.save_knobs("PRODA", 0, [knob("k1", "CA deep etch", [], lot_ids=["A1002"])], "hol")
    doc = structure.save_lots("PRODA", 0, [
        {"lot_id": "A1001", "purpose": "CA knob 평가", "knobs": "CA deep etch", "status": "진행"},
        {"lot_id": "", "purpose": ""},
    ], "hol")

    lot = doc["lots"][0]
    assert lot["knob_ids"] == ["k1"] and lot["knob_names"] == ["CA deep etch"]
    assert lot["status"] == "running"
    assert lot["lot_management_purpose"] == "랏관리 목적"
    assert doc["suggested"] == [{"lot_id": "A1002", "knob_names": ["CA deep etch"]}]
    with pytest.raises(ValueError, match="knob을 찾을 수 없습니다"):
        structure.save_lots("PRODA", 1, [{"lot_id": "A1003", "knobs": "없는 knob"}], "hol")
    with pytest.raises(ValueError, match="중복"):
        structure.save_lots("PRODA", 1, [{"lot_id": "A1003"}, {"lot_id": "a1003"}], "hol")
    assert structure.registry_history("PRODA", "lots")[0]["revision"] == 1


def test_same_code_knobs_merge_into_one_code_set(isolated):
    doc = structure.save_knobs("PRODA", 0, [
        knob("k1", "CA etch part", [("AA100", "PP_ETCH_2")], code="CA_K1"),
        knob("k2", "CA fill part", [("AA110", "PP_FILL_2")], code="ca_k1"),
        knob("k3", "solo", [("AA100", "PP_X")]),
    ], "hol")
    sets = doc["groups"][0]["step_sets"]
    code_set = next(s for s in sets if s["kind"] == "code")
    assert code_set["step_ids"] == ["AA100", "AA110"] and code_set["knob_ids"] == ["k1", "k2"]
    assert code_set["multi_step"] and code_set["key"].startswith("CA_K1")
    assert doc["code_hints"] == []


def test_feature_name_matching_by_name_alias_ppid_and_suggestions(isolated):
    doc = structure.save_knobs("PRODA", 0, [
        knob("k1", "10.0 ca deep", []),                               # 순번·공백·대소문자 무시
        knob("k2", "새 이름", [], aliases=["KNOB_CA_FILL_NEW"]),       # 별칭
        knob("k3", "다른 이름", [("AA100", "PP_ETCH_2")]),            # PPID 로만 등록
        knob("k4", "CA_DEPP", []),                                    # 미등록 → 추천
        knob("k5", "중단된 것", [], por_status="dropped"),
    ], "hol")
    m = doc["feature_matches"]
    assert (m["k1"]["status"], m["k1"]["feature"]) == ("name", "CA_DEEP")
    assert (m["k2"]["status"], m["k2"]["feature"]) == ("alias", "CA_FILL_NEW")
    assert (m["k3"]["status"], m["k3"]["feature"]) == ("ppid", "CA_DEEP")
    assert m["k4"]["status"] == "none" and m["k4"]["suggestions"][0] == "CA_DEEP"

    report = structure.unregistered_knob_report()
    ids = [k["knob_id"] for p in report["products"] for k in p["knobs"]]
    assert "k4" in ids and "k5" not in ids and "k1" not in ids

    assert structure.add_knob_aliases("PRODA", [{"knob_id": "k4", "alias": "CA_DEEP"}], "hol")["added"] == 1
    assert structure.knobs_document("PRODA")["feature_matches"]["k4"]["status"] == "alias"
    assert structure.add_knob_aliases("PRODA", [{"knob_id": "k4", "alias": "ca_deep"}], "hol")["added"] == 0
