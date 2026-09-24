from copy import deepcopy

import pytest
from fastapi import HTTPException, Request

from core import structure_model as model
from routers import structure_model as api


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(model, "_path", lambda: tmp_path / "structure-model.sqlite3")
    rows = {
        "LOGIC_A": [{"module": "FEOL", "step_id": "0100", "item_id": "GATE_CD", "item_desc": "gate CD"}],
        "SRAM_B": [{"module": "FEOL", "step_id": "0220", "item_id": "GATE_CD", "item_desc": "gate CD"}],
    }
    monkeypatch.setattr(model.product_semantics, "load_inline_matching_rows", lambda product: rows.get(product, []))
    return model.read()


def test_scene_is_explicit_and_variants_change_geometry(isolated):
    reference = model.build_scene(isolated)
    assert {key: value for key, value in reference["nanosheet_dimensions_nm"].items() if key != "sheets"} == {
        "count_per_stack": 3, "thickness": 5.0, "vertical_gap": 10.0,
        "pair_gaps": [10.0, 10.0], "center_pitch": 15.0, "width": 40.0,
        "active_stack_height": 35.0,
    }
    assert [sheet["width"] for sheet in reference["nanosheet_dimensions_nm"]["sheets"]] == [40.0] * 3
    assert [part["size"][1] for part in reference["parts"] if part["role"] == "channel"] == [0.125] * 3
    assert sum(part["role"] == "inner_gate" for part in reference["parts"]) == 6
    assert any(part["name"] == "상부 게이트" for part in reference["parts"])
    assert {part["stage"] for part in reference["parts"]} == {"FEOL", "MOL", "BEOL"}
    assert any(part["name"] == "BEOL M2" for part in reference["parts"])
    small = model.build_scene(isolated, kind="logic", variant="5T")
    large = model.build_scene(isolated, kind="logic", variant="9T")
    assert small["schema"] == "flow.gaa.scene.v1"
    assert small["units"].startswith("illustrative")
    assert len([part for part in large["parts"] if part["role"] == "channel"]) == 4
    assert len([part for part in small["parts"] if part["role"] == "channel"]) == 3
    assert small["parameters"]["cell_height"] < large["parameters"]["cell_height"]
    hd = model.build_scene(isolated, kind="sram", variant="HD")
    hc = model.build_scene(isolated, kind="sram", variant="HC")
    assert sum(part["role"] == "channel" for part in hd["parts"]) == 4
    assert sum(part["role"] == "channel" for part in hc["parts"]) == 6
    assert any(part["role"] == "bitline" for part in hd["parts"])


def test_product_anchors_do_not_cross_product_boundary(isolated):
    document = deepcopy(isolated)
    document["products"] = {
        "LOGIC_A": {"profiles": {"logic/6T": {"parameters": {"sheet_count": 4},
                    "anchors": {"gate": {"module": "FEOL", "step_id": "0100", "item_id": "GATE_CD"},
                                "beol": {"module": "FEOL", "step_id": "0100", "item_id": "GATE_CD"}}}}},
        "SRAM_B": {"profiles": {"sram/HD": {"parameters": {},
                   "anchors": {"gate": {"module": "FEOL", "step_id": "0220", "item_id": "GATE_CD"}}}}},
    }
    logic = model.build_scene(document, "LOGIC_A")
    sram = model.build_scene(document, "SRAM_B", "sram", "HD")
    assert logic["anchors"]["gate"]["step_id"] == "0100"
    assert logic["anchors"]["gate"]["status"] == "matched"
    assert logic["anchors"]["beol"]["status"] == "matched"
    assert logic["anchor_candidate_count"] == 1
    assert logic["anchor_candidates"] == []
    assert model.build_scene(document, "LOGIC_A", include_candidates=True)["anchor_candidates"][0]["step_id"] == "0100"
    assert logic["parameters"]["sheet_count"] == 4
    assert sram["anchors"]["gate"]["step_id"] == "0220"
    assert sram["anchors"]["gate"]["status"] == "matched"
    assert sram["type"] == "sram"
    assert all(part["anchor"] == logic["anchors"]["gate"] for part in logic["parts"] if part["role"] == "gate")
    assert all(part["anchor"] == logic["anchors"]["beol"] for part in logic["parts"] if part["role"] == "beol")
    assert all(part["anchor"] == sram["anchors"]["gate"] for part in sram["parts"] if part["role"] == "gate")
    missing = model.build_scene({**document, "products": {"SRAM_B": document["products"]["LOGIC_A"]}}, "SRAM_B")
    assert missing["anchors"]["gate"]["status"] == "missing"
    assert missing["warnings"]


def test_one_product_keeps_logic_and_sram_profiles_independent(isolated):
    document = deepcopy(isolated)
    document["products"]["LOGIC_A"] = {"profiles": {
        "logic/6T": {"parameters": {"sheet_count": 4}, "anchors": {"gate": {"module": "FEOL", "step_id": "0100", "item_id": "GATE_CD"}}},
        "sram/HC": {"parameters": {"sheet_count": 2}, "anchors": {"channel": {"module": "FEOL", "step_id": "0100", "item_id": "GATE_CD"}}},
    }}
    logic = model.build_scene(document, "LOGIC_A", "logic", "6T")
    sram = model.build_scene(document, "LOGIC_A", "sram", "HC")
    assert logic["parameters"]["sheet_count"] == 4
    assert set(logic["anchors"]) == {"gate"}
    assert sram["parameters"]["sheet_count"] == 2
    assert set(sram["anchors"]) == {"channel"}


def test_save_conflict_and_invalid_geometry(isolated):
    payload = {"variants": isolated["variants"], "products": {}}
    saved = model.save(payload, 0, "admin")
    assert saved["version"] == 1
    with pytest.raises(model.Conflict):
        model.save(payload, 0, "second")
    assert model.read() == saved
    invalid = deepcopy(payload)
    invalid["variants"]["logic"]["5T"]["sheet_count"] = 2.5
    with pytest.raises(ValueError):
        model.validate(invalid)


def test_public_catalog_does_not_expose_product_profiles(isolated, monkeypatch):
    document = deepcopy(isolated)
    document["products"]["LOGIC_A"] = {"profiles": {"logic/6T": {"parameters": {}, "anchors": {}}}}
    monkeypatch.setattr(api.structure_model, "read", lambda: document)
    monkeypatch.setattr(api, "current_user", lambda request: {"role": "user", "tabs": ""})
    monkeypatch.setattr(api, "user_tab_tokens", lambda user: ([], []))
    monkeypatch.setattr(api, "is_page_manager", lambda user, page: False)
    request = Request({"type": "http", "method": "GET", "path": "/api/structure-model", "headers": []})
    assert api.read(request)["products"] == {}
    assert api.products(request) == {"products": []}
    with pytest.raises(HTTPException) as exc:
        api.scene(request, product="LOGIC_A", type="logic", variant="6T")
    assert exc.value.status_code == 403


def test_dimension_anchors_and_per_sheet_mts_are_product_specific(isolated):
    doc = deepcopy(isolated)
    doc["products"]["LOGIC_A"] = {"profiles": {"logic/6T": {
        "parameters": {"ns1_width_nm": 30, "ns2_width_nm": 34, "ns3_width_nm": 38,
                       "ns3_thickness_nm": 6, "mol_level_count": 3,
                       "mol_height_nm": 32, "sd_width_nm": 32,
                       "sd_protrusion_nm": 30, "gate_to_sd_gap_nm": 6,
                       "sd_doping_log10_cm3": 20},
        "anchors": {}, "dimension_anchors": {"ns_top_to_m0": {
            "module": "FEOL", "step_id": "0100", "item_id": "GATE_CD"}},
        "shape_profiles": {"source": {"tcd_nm": 18, "mcd_nm": 26, "bcd_nm": 31}},
    }}}
    a = model.build_scene(doc, "LOGIC_A")
    b = model.build_scene(doc, "SRAM_B")
    assert [sheet["width"] for sheet in a["nanosheet_dimensions_nm"]["sheets"]] == [30, 34, 38]
    assert a["nanosheet_dimensions_nm"]["active_stack_height"] == 35.5
    assert a["nanosheet_dimensions_nm"]["pair_gaps"] == [10.0, 9.5]
    assert a["mol_level_count"] == 3
    assert a["spacing_nm"]["gate_to_sd"] == 6
    assert a["measurements"]["ns_top_to_m0"]["anchor"]["status"] == "matched"
    assert a["measurements"]["ns_top_to_m0"]["height_nm"] != b["measurements"]["ns_top_to_m0"]["height_nm"]
    source = next(part for part in a["parts"] if part["role"] == "source")
    assert source["shape"] == "profile_box"
    assert source["metadata"]["sd_doping_log10_cm3"] == 20
    assert source["profile_widths"] == pytest.approx([1, 26 / 31, 18 / 31], abs=0.0001)
    assert b["shape_profiles"] == {}
    assert b["measurements"]["ns_top_to_m0"]["anchor"] is None


def test_independent_sheet_thickness_cannot_overlap(isolated):
    doc = {"variants": deepcopy(isolated["variants"]), "products": {},
           "dimensions": deepcopy(isolated["dimensions"]), "shape_profiles": {}}
    doc["variants"]["logic"]["6T"]["ns1_thickness_nm"] = 15
    doc["variants"]["logic"]["6T"]["ns2_thickness_nm"] = 15
    with pytest.raises(ValueError, match="NS 층간"):
        model.validate(doc)


def test_shape_llm_result_is_preview_only(isolated, monkeypatch):
    monkeypatch.setattr(api.llm_adapter, "complete_json", lambda *args, **kwargs: {
        "ok": True, "obj": {"tcd_nm": 13, "mcd_nm": 18, "bcd_nm": 24}})
    result = api.suggest_shape(api.ShapeSuggestionRequest(
        role="source", instruction="위가 좁고 아래가 넓게", current={"tcd_nm": 20}), user={"role": "admin"})
    assert result == {"role": "source", "shape_profile": {
        "tcd_nm": 13, "mcd_nm": 18, "bcd_nm": 24}, "applied": False}
    assert model.read()["shape_profiles"] == {}
    with pytest.raises(HTTPException) as exc:
        api.suggest_shape(api.ShapeSuggestionRequest(
            role="channel", instruction="폭 조정", current={}), user={"role": "admin"})
    assert exc.value.status_code == 400


def test_gemma_numeric_patch_is_scoped_validated_and_unsaved(isolated, monkeypatch):
    doc = {"variants": deepcopy(isolated["variants"]), "products": {},
           "dimensions": deepcopy(isolated["dimensions"]), "shape_profiles": {}}
    doc["products"]["LOGIC_A"] = {"profiles": {"logic/6T": {
        "parameters": {"ns1_width_nm": 30}, "anchors": {}, "shape_profiles": {}, "dimension_anchors": {}}}}
    doc["products"]["SRAM_B"] = {"profiles": {"logic/6T": {
        "parameters": {"ns1_width_nm": 50}, "anchors": {}, "shape_profiles": {}, "dimension_anchors": {}}}}
    edits = [{"kind": "parameter", "name": "ns2_width_nm", "role": "", "value": 34},
             {"kind": "shape", "name": "tcd_nm", "role": "source", "value": 18}]
    changed = model.apply_numeric_edits(doc, "LOGIC_A", "logic", "6T", edits)
    assert model.build_scene(changed, "LOGIC_A")["nanosheet_dimensions_nm"]["sheets"][1]["width"] == 34
    assert changed["products"]["SRAM_B"] == doc["products"]["SRAM_B"]
    assert "ns2_width_nm" not in doc["products"]["LOGIC_A"]["profiles"]["logic/6T"]["parameters"]
    with pytest.raises(ValueError):
        model.apply_numeric_edits(doc, "LOGIC_A", "logic", "6T",
                                  [{"kind": "parameter", "name": "sheet_count", "role": "", "value": 99}])
    with pytest.raises(ValueError):
        model.apply_numeric_edits(doc, "LOGIC_A", "logic", "6T",
                                  [{"kind": "parameter", "name": "anchor", "role": "", "value": 1}])

    from core import domain_knowledge, product_wiki
    monkeypatch.setattr(domain_knowledge, "prompt_context", lambda *args, **kwargs: {"body": "공통 GAA"})
    monkeypatch.setattr(product_wiki, "document", lambda product: {"entries": [
        {"title": "선택 제품", "source_text": "NS 폭 34 nm"}]})
    prompts = []
    def fake_llm(prompt, **kwargs):
        prompts.append(prompt)
        return {"ok": True, "obj": {"summary": "NS2 폭 제안", "edits": edits}}
    monkeypatch.setattr(api.llm_adapter, "complete_json", fake_llm)
    result = api.suggest_edits(api.EditSuggestionRequest(
        **doc, product="LOGIC_A", type="logic", variant="6T", instruction="NS 2층과 소스 CD 조정"),
        user={"role": "admin"})
    assert result["saved"] is False and result["applied"] is False
    assert result["after"]["sheet_dimensions_nm"]["sheets"][1]["width"] == 34
    assert "SRAM_B" not in prompts[0]
    assert model.read()["products"] == {}


def test_text_context_is_small_and_product_scoped(isolated, monkeypatch):
    doc = deepcopy(isolated)
    doc["products"] = {"LOGIC_A": {"profiles": {"logic/6T": {
        "parameters": {"ns1_width_nm": 30}, "anchors": {}, "shape_profiles": {}, "dimension_anchors": {}}}},
        "SRAM_B": {"profiles": {"logic/6T": {
            "parameters": {"ns1_width_nm": 50}, "anchors": {}, "shape_profiles": {}, "dimension_anchors": {}}}}}
    monkeypatch.setattr(model, "read", lambda: doc)
    assert model.prompt_context("LOGIC_A", "오늘 날씨") == {}
    scoped = model.prompt_context("LOGIC_A", "NS 폭", max_chars=3000)
    assert scoped["sheet_dimensions_nm"]["sheets"][0]["width"] == 30
    assert "SRAM_B" not in str(scoped)
    assert "Inner gate" in str(scoped["topology"])
    doc["products"]["LOGIC_A"]["profiles"]["sram/HD"] = {
        "parameters": {}, "anchors": {}, "shape_profiles": {}, "dimension_anchors": {}}
    ambiguous = model.prompt_context("LOGIC_A", "GAA 구조")
    assert ambiguous["available_profiles"] == ["logic/6T", "sram/HD"]
    assert "sheet_dimensions_nm" not in ambiguous
    assert model.prompt_context("LOGIC_A", "6T NS 폭")["variant"] == "6T"


def test_latchup_view_has_adjacent_wells_and_no_fake_prediction(isolated):
    scene = model.build_scene(isolated, view="latchup")
    assert {"nwell", "pwell", "well_tap", "latch_path"} <= {part["role"] for part in scene["parts"]}
    assert scene["measurements"] == {}
    assert "trigger/holding" in scene["latchup_path"]["meaning"]
    with pytest.raises(ValueError):
        model.build_scene(isolated, view="unknown")


def test_shape_and_measurement_validation(isolated):
    doc = deepcopy(isolated)
    doc["shape_profiles"] = {"logic/6T": {"gate": {"tcd_nm": 12, "mcd_nm": 14, "bcd_nm": 16}}}
    assert model.build_scene(doc)["shape_profiles"]["gate"]["tcd_nm"] == 12
    bad = deepcopy(doc)
    bad["shape_profiles"]["logic/6T"]["gate"]["tcd_nm"] = -1
    with pytest.raises(ValueError):
        model.validate(bad)
    bad = deepcopy(doc)
    bad["dimensions"]["invalid"] = {"label": "bad", "from": "ns_stack_top", "to": "ns_stack_top"}
    with pytest.raises(ValueError):
        model.validate(bad)
