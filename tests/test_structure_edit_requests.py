"""Admin 3D structure edits written in plain Korean must reach the model."""
from copy import deepcopy

import pytest
from fastapi import HTTPException

from core import structure_edit_rules as rules
from core import structure_model as model
from routers import structure_model as api


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(model, "_path", lambda: tmp_path / "structure-model.sqlite3")
    rows = {"LOGIC_A": [{"module": "FEOL", "step_id": "0100", "item_id": "NS_TOP_M0", "item_desc": "ns"}]}
    monkeypatch.setattr(model.product_semantics, "load_inline_matching_rows", lambda product: rows.get(product, []))

    def no_llm(*args, **kwargs):
        raise AssertionError("rule-readable requests must not call the LLM")
    monkeypatch.setattr(api.llm_adapter, "complete_json", no_llm)
    return model.read()


def _suggest(doc, instruction, product=""):
    return api.suggest_edits(api.EditSuggestionRequest(
        variants=doc["variants"], products=doc["products"], dimensions=doc["dimensions"],
        shape_profiles=doc["shape_profiles"], material_stacks=doc["material_stacks"],
        product=product, type="logic", variant="6T", instruction=instruction), user={"role": "admin"})


def _apply(doc, result, product=""):
    return model.apply_edits(doc, product, "logic", "6T", result["edits"], result["material_stacks"])


def test_mol_levels_change_on_epi_side(isolated):
    for text, levels in (("epi MOL 쪽에 2단을 3단으로 해줘", 3), ("epi MOL 1단으로 바꿔줘", 1)):
        result = _suggest(isolated, text)
        assert result["source"] == "rule"
        scene = model.build_scene(_apply(isolated, result))
        assert scene["mol_level_count"] == levels
        source_lines = {part["metadata"]["mol_level"] for part in scene["parts"]
                        if part["role"] == "mol" and "소스" in part["name"] and part["metadata"]["mol_kind"] == "line"}
        assert source_lines == set(range(1, levels + 1))


def test_default_mol_is_direct_for_epi_and_gate(isolated):
    scene = model.build_scene(isolated)
    assert scene["mol_connection"] == "direct"
    mol = [part for part in scene["parts"] if part["role"] == "mol"]
    assert mol and all(part["metadata"].get("connection") == "direct" for part in mol)
    assert {name for name in ("소스", "게이트", "드레인") if any(name in part["name"] for part in mol)} == {
        "소스", "게이트", "드레인"}
    result = _suggest(isolated, "epi MOL 네모 패드 다시 살려줘")
    padded = model.build_scene(_apply(isolated, result))
    assert padded["mol_connection"] == "landing_pad"
    assert any(part["metadata"].get("mol_kind") == "via" for part in padded["parts"] if part["role"] == "mol")


def test_direct_mol_and_contact_fill_recipe(isolated):
    result = _suggest(isolated, "epi MOL 연결부위에 네모로 구분있는거 없애고 바로 연결해줘. "
                                "MOL 사이드에 물질A 얇게 넣어줘 B는 아래에 약간 넣어주고 그 위는 C로 채워줘")
    assert result["material_stacks"]["sd_contact"] == [
        {"material": "A", "mode": "liner", "size": "thin"},
        {"material": "B", "mode": "bottom", "size": "thin"},
        {"material": "C", "mode": "fill"}]
    scene = model.build_scene(_apply(isolated, result))
    assert scene["mol_connection"] == "direct"
    source_mol = [part for part in scene["parts"] if part["role"] == "mol" and "소스" in part["name"]]
    # No wide landing pads: every level keeps the contact footprint.
    assert source_mol and all(part["size"][0] == pytest.approx(0.32) for part in source_mol)
    layers = [part for part in scene["parts"] if part.get("metadata", {}).get("material_region") == "sd_contact"
              and "소스" in part["name"]]
    by_material = {}
    for part in layers:
        by_material.setdefault(part["metadata"]["material"], []).append(part)
    assert len(by_material["A"]) == 4  # sidewalls on all four sides
    assert min(p["center"][1] for p in by_material["B"]) < min(p["center"][1] for p in by_material["C"])
    assert [row["material"] for row in scene["material_layers"]["sd_contact"]] == ["A", "B", "C"]


def test_gate_fill_order_bottom_u_and_remainder(isolated):
    result = _suggest(isolated, "GATE는 밑에서부터 D,C,E로 채워줘 G,H는 U자로 채워넣고 I는 나머지에 구간에 채워줘")
    assert [(layer["material"], layer["mode"]) for layer in result["material_stacks"]["gate"]] == [
        ("D", "bottom"), ("C", "bottom"), ("E", "bottom"), ("G", "u_liner"), ("H", "u_liner"), ("I", "fill")]
    scene = model.build_scene(_apply(isolated, result))
    gate = [part for part in scene["parts"] if part.get("metadata", {}).get("material_region") == "gate"]
    bottoms = {part["metadata"]["material"]: part["center"][1] for part in gate if part["metadata"]["material_mode"] == "bottom"}
    assert bottoms["D"] < bottoms["C"] < bottoms["E"]
    assert sum(part["metadata"]["material"] == "G" for part in gate) == 3  # U = floor + two walls
    fill = next(part for part in gate if part["metadata"]["material"] == "I")
    walls = [part for part in gate if part["metadata"]["material"] == "H" and part["size"][0] < 0.1]
    assert all(abs(wall["center"][0]) > fill["size"][0] / 2 for wall in walls)
    assert fill["center"][1] - fill["size"][1] / 2 >= max(bottoms.values())


def test_epi_recess_moves_contact_bottom(isolated):
    result = _suggest(isolated, "epi와 연결된 MOL은 epi 위를 얼마 깎아줘")
    assert result["notes"] and "5 nm" in result["notes"][0]
    exact = _suggest(isolated, "epi와 연결된 MOL은 epi 위를 4nm 깎아줘")
    scene = model.build_scene(_apply(isolated, exact))
    assert scene["landmarks"]["sd_epi_top"] - scene["landmarks"]["sd_contact_bottom"] == pytest.approx(0.1, abs=0.001)
    contact = next(part for part in scene["parts"] if part["role"] == "contact")
    assert contact["center"][1] - contact["size"][1] / 2 == pytest.approx(scene["landmarks"]["sd_contact_bottom"], abs=0.002)


def test_inner_gate_levels_width_and_height(isolated):
    placeholder = rules.parse("inner gate 1,2,3측 각각 width, height 어떻게 해줘")
    assert placeholder["needs_values"] and not placeholder["edits"]
    with pytest.raises(HTTPException) as exc:
        _suggest(isolated, "inner gate 1,2,3측 각각 width, height 어떻게 해줘")
    assert exc.value.status_code == 422
    result = _suggest(isolated, "inner gate 1,2,3측 각각 width 30,28,26nm height 12,10,8nm로 해줘")
    scene = model.build_scene(_apply(isolated, result))
    gates = scene["nanosheet_dimensions_nm"]["inner_gates"]
    assert [(row["width_nm"], row["height_nm"]) for row in gates] == [(30, 12), (28, 10), (26, 8)]
    parts = [part for part in scene["parts"] if part["role"] == "inner_gate"]
    assert [part["size"][0] for part in parts] == [0.75, 0.7, 0.65]
    assert scene["nanosheet_dimensions_nm"]["pair_gaps"] == [10, 8]
    sheets = [part["center"][1] for part in scene["parts"] if part["role"] == "channel"]
    assert sheets[0] == pytest.approx((12 + 2.5) / 40, abs=0.001)
    # Narrower inner gates leave inner spacers between them and the gate edge.
    assert sum(part["name"].startswith("Inner spacer") for part in scene["parts"]) == 6


def test_inline_parameter_positions_are_shown_on_request(isolated):
    doc = deepcopy(isolated)
    doc["products"]["LOGIC_A"] = {"profiles": {"logic/6T": {"parameters": {}, "anchors": {
        "gate": {"module": "FEOL", "step_id": "0100", "item_id": "NS_TOP_M0"}},
        "dimension_anchors": {"ns_top_to_m0": {"module": "FEOL", "step_id": "0100", "item_id": "NS_TOP_M0"}}}}}
    result = _suggest(doc, "각 연결된 Inline parameter 위치 표시해줘", product="LOGIC_A")
    assert result["display"] == {"inline_parameters": True} and result["edits"] == []
    scene = model.build_scene(doc, "LOGIC_A")
    markers = {marker["id"]: marker for marker in scene["inline_markers"]}
    ns_top = markers["ns_top_to_m0"]
    assert ns_top["status"] == "matched" and "0100 / NS_TOP_M0" in ns_top["text"]
    assert ns_top["from"][1] == pytest.approx(scene["landmarks"]["ns_stack_top"], abs=0.001)
    assert ns_top["to"][1] == pytest.approx(scene["landmarks"]["mol_m0_bottom"], abs=0.001)
    assert markers["gate"]["kind"] == "role" and markers["gate"]["status"] == "matched"
    assert markers["ns_stack_height"]["status"] == "unlinked"


def test_all_six_requests_in_one_message(isolated):
    text = ("1.epi MOL 쪽에 2단을 3단으로 해줘\n"
            "2.epi MOL 연결부위에 네모로 구분있는거 없애고 바로 연결해줘. MOL 사이드에 물질A 얇게 넣어줘 "
            "B는 아래에 약간 넣어주고 그 위는 C로 채워줘\n"
            "3,GATE는 밑에서부터 D,C,E로 채워줘 G,H는 U자로 채워넣고 I는 나머지에 구간에 채워줘\n"
            "4.epi와 연결된 MOL은 epi 위를 3nm 깎아줘\n"
            "5.inner gate 1,2,3측 각각 width 30,28,26 height 12,10,8 해줘\n"
            "6.각 연결된 Inline parameter 위치 표시해줘")
    result = _suggest(isolated, text)
    assert result["source"] == "rule"
    names = {edit["name"]: edit["value"] for edit in result["edits"]}
    assert names["mol_level_count"] == 3 and names["mol_sd_landing_pad"] == 0
    assert names["contact_epi_recess_nm"] == 3 and names["inner_gate3_height_nm"] == 8
    assert set(result["material_stacks"]) == {"sd_contact", "gate"}
    assert result["display"]["inline_parameters"] is True
    saved = model.save(_apply(isolated, result), 0, "admin")
    assert model.build_scene(saved)["material_layers"]["gate"][-1]["material"] == "I"


def test_unreadable_request_goes_to_llm_and_is_merged(isolated, monkeypatch):
    calls = []

    def fake(prompt, **kwargs):
        calls.append(prompt)
        return {"ok": True, "obj": {"summary": "시트 폭", "edits": [
            {"kind": "parameter", "name": "ns2_width_nm", "role": "", "value": 34}]}}
    monkeypatch.setattr(api.llm_adapter, "complete_json", fake)
    result = _suggest(isolated, "epi MOL 3단으로 해줘, NS 두 번째 시트는 조금 좁혀줘")
    assert result["source"] == "llm+rule" and len(calls) == 1
    assert {edit["name"] for edit in result["edits"]} == {"ns2_width_nm", "mol_level_count"}
    assert "sentences_left_for_you" in calls[0]


def test_material_recipe_validation(isolated):
    doc = {key: deepcopy(isolated[key]) for key in model.DOCUMENT_KEYS}
    doc["material_stacks"] = {"logic/6T": {"gate": [{"material": "I", "mode": "fill"}, {"material": "D", "mode": "bottom"}]}}
    with pytest.raises(ValueError, match="마지막"):
        model.validate(doc)
    doc["material_stacks"] = {"logic/6T": {"gate": [{"material": "D", "mode": "bottom", "thickness_nm": 40}]}}
    with pytest.raises(ValueError, match="두께 합"):
        model.build_scene(doc)
    doc["material_stacks"] = {"logic/6T": {"via": [{"material": "D", "mode": "bottom"}]}}
    with pytest.raises(ValueError):
        model.validate(doc)
