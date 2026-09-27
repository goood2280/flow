from copy import deepcopy
from itertools import combinations

import pytest

from core import structure_model as model
from core import structure_topology


def test_sram_is_six_transistors_with_cross_coupled_storage_nodes():
    scene = model.build_scene(model._empty(), kind="sram", variant="HD")

    assert scene["topology"]["kind"] == "6T SRAM bitcell"
    assert len(scene["netlist"]) == 6
    by_id = {device["id"]: device for device in scene["netlist"]}
    assert {(device["type"], device["role"]) for device in scene["netlist"]} == {
        ("pmos", "pull_up"), ("nmos", "pull_down"), ("nmos", "access")}
    assert sum(device["type"] == "pmos" for device in scene["netlist"]) == 2
    assert sum(device["role"] == "pull_down" for device in scene["netlist"]) == 2
    assert sum(device["role"] == "access" for device in scene["netlist"]) == 2
    assert by_id["PU_Q"]["terminals"] == {"S": "VDD", "D": "Q", "G": "QB", "B": "VDD"}
    assert by_id["PD_Q"]["terminals"]["G"] == "QB"
    assert by_id["PU_QB"]["terminals"]["G"] == "Q"
    assert by_id["PD_QB"]["terminals"]["G"] == "Q"
    assert by_id["PG_Q"]["terminals"] == {"S": "BL", "D": "Q", "G": "WL", "B": "VSS"}
    assert by_id["PG_QB"]["terminals"] == {"S": "BLB", "D": "QB", "G": "WL", "B": "VSS"}

    physical_devices = {part.get("metadata", {}).get("device_id") for part in scene["parts"]
                        if part["role"] == "channel"}
    assert physical_devices == set(by_id)
    assert len([part for part in scene["parts"] if part["role"] == "source"]) == 6
    assert all({"device_id", "device_type", "device_role"} <= part["metadata"].keys()
               for part in scene["parts"] if part["role"] == "channel")


def test_sram_routing_uses_one_height_per_net_and_ignores_shape_overrides():
    document = deepcopy(model._empty())
    document["shape_profiles"]["sram/HD"] = {
        "beol": {"tcd_nm": 90, "mcd_nm": 70, "bcd_nm": 50, "primitive": "profile_box"}}
    scene = model.build_scene(document, kind="sram", variant="HD")
    nets = scene["topology"]["nets"]

    assert set(nets) == {"VDD", "VSS", "WL", "BL", "BLB", "Q", "QB"}
    assert len({net["routing_level"] for net in nets.values()}) == 7
    assert nets["WL"]["terminals"] == [
        {"device": "PG_Q", "terminal": "G"}, {"device": "PG_QB", "terminal": "G"}]
    assert nets["BL"]["terminals"] == [{"device": "PG_Q", "terminal": "S"}]
    assert nets["BLB"]["terminals"] == [{"device": "PG_QB", "terminal": "S"}]
    routes = [part for part in scene["parts"] if part.get("metadata", {}).get("routing_level")]
    assert routes
    assert all(all(size > 0 for size in part["size"]) for part in routes)
    assert all(part["size"][1] == pytest.approx(0.1) for part in routes
               if part["metadata"]["kind"] in {"trunk", "branch"})
    annotation_text = {annotation["text"] for annotation in scene["annotations"]}
    assert set(nets) <= annotation_text
    assert {"PU_Q", "PD_Q", "PG_Q", "PU_QB", "PD_QB", "PG_QB"} <= annotation_text
    assert all(annotation["role"] == "channel" for annotation in scene["annotations"]
               if annotation["text"].startswith(("PU_", "PD_", "PG_")))

    # Topology metal includes the cloned terminal metal plus all new escape,
    # via, branch and trunk geometry. Different nets must not occupy the same
    # three-dimensional volume.
    conductors = [part for part in scene["parts"]
                  if part.get("metadata", {}).get("net") and (
                      part["role"] == "mol" or part.get("metadata", {}).get("kind")
                      in {"escape", "via", "branch", "trunk"})]

    def intersects(left, right):
        return all(abs(left["center"][axis] - right["center"][axis])
                   < (left["size"][axis] + right["size"][axis]) / 2 - 1e-6
                   for axis in range(3))

    collisions = [(left["name"], right["name"])
                  for left, right in combinations(conductors, 2)
                  if left["metadata"]["net"] != right["metadata"]["net"]
                  and intersects(left, right)]
    assert collisions == []
    assert structure_topology.SRAM_REFERENCE in {reference["url"] for reference in scene["references"]}


def test_latchup_has_buried_wells_full_complementary_devices_and_parasitic_path():
    document = model._empty()
    base = model.build_scene(document, kind="logic", variant="6T")
    scene = model.build_scene(document, kind="logic", variant="6T", view="latchup")

    for role in ("nwell", "pwell"):
        well = next(part for part in scene["parts"] if part["role"] == role)
        assert well["center"][1] + well["size"][1] / 2 <= 0
    assert {part.get("metadata", {}).get("device_id") for part in scene["parts"]
            if part["role"] == "channel"} == {"PMOS", "NMOS"}
    assert len([part for part in scene["parts"] if part["role"] == "channel"]) == 2 * base["parameters"]["sheet_count"]
    assert {part["shape"] for part in scene["parts"] if part["role"] in {"source", "drain"}} == {"faceted_epi"}
    assert {part["shape"] for part in scene["parts"] if part["role"] == "gate"} == {"gate_shell"}
    assert any(part["role"] == "spacer" and part["shape"] == "gate_shell" for part in scene["parts"])
    assert any(part["role"] == "contact" and part["shape"] == "tapered_cylinder" for part in scene["parts"])
    assert any(part["role"] == "field" and part["metadata"]["isolation"] == "STI" for part in scene["parts"])
    assert any(part["role"] == "guard_ring" for part in scene["parts"])

    base_gate = next(part for part in base["parts"] if part["role"] == "gate")
    scaled_gate = next(part for part in scene["parts"]
                       if part["role"] == "gate" and part["metadata"]["device_id"] == "PMOS")
    # The gate cross-section is a notched outline ([z, y] local points) around the sheet column.
    base_point = base_gate["metadata"]["outlines"][0][1]
    scaled_point = scaled_gate["metadata"]["outlines"][0][1]
    assert scaled_point == pytest.approx([base_point[0] * 0.86, base_point[1] * 0.86], abs=0.0001)
    base_spacer = next(part for part in base["parts"] if part["role"] == "spacer")
    scaled_spacer = next(part for part in scene["parts"]
                         if part["role"] == "spacer" and part["metadata"]["device_id"] == "PMOS")
    assert scaled_spacer["metadata"]["sheet_holes"][0]["y"] == pytest.approx(
        base_spacer["metadata"]["sheet_holes"][0]["y"] * 0.86, abs=0.0001)
    base_epi = next(part for part in base["parts"] if part["role"] == "source")
    scaled_epi = next(part for part in scene["parts"]
                       if part["role"] == "source" and part["metadata"]["device_id"] == "PMOS")
    assert scaled_epi["metadata"]["cap_height"] == pytest.approx(
        base_epi["metadata"]["cap_height"] * 0.86, abs=0.0001)
    assert scaled_epi["metadata"]["facet_angle_deg"] == base_epi["metadata"]["facet_angle_deg"]
    nmos_epi = next(part for part in scene["parts"]
                     if part["role"] == "source" and part["metadata"]["device_id"] == "NMOS")
    assert nmos_epi["metadata"]["material"] == "Si:P"

    wells = {role: next(part for part in scene["parts"] if part["role"] == role)
             for role in ("nwell", "pwell")}
    taps = [part for part in scene["parts"] if part["role"] == "well_tap"]
    assert wells["nwell"]["center"][0] - wells["nwell"]["size"][0] / 2 < taps[0]["center"][0] < -0.1
    assert 0.1 < taps[1]["center"][0] < wells["pwell"]["center"][0] + wells["pwell"]["size"][0] / 2
    assert all(part["center"][1] - part["size"][1] / 2 < 0 for part in taps)
    assert {part["metadata"]["net"] for part in scene["parts"]
            if part.get("metadata", {}).get("kind") == "tap_branch"} == {"VDD", "VSS"}
    guard_parts = [part for part in scene["parts"] if part["role"] == "guard_ring"]
    assert {part["metadata"].get("side") for part in guard_parts if part["metadata"].get("side")} == {
        "left", "right", "front", "back"}
    assert {part["metadata"]["net"] for part in guard_parts
            if part["metadata"].get("kind") == "contact"} == {"VDD", "VSS"}

    assert {part["metadata"]["parasitic"] for part in scene["parts"] if part["role"] == "latch_path"} == {"PNP", "NPN"}
    path = sorted((part for part in scene["parts"] if part["role"] == "latch_path"),
                  key=lambda part: part["metadata"]["sequence"])
    assert all(left["metadata"]["to"] == right["metadata"]["from"]
               for left, right in zip(path, path[1:]))
    assert path[0]["metadata"]["from"][0] == pytest.approx(
        next(part for part in scene["parts"] if part["role"] == "drain"
             and part["metadata"]["device_id"] == "PMOS")["center"][0])
    assert path[-1]["metadata"]["to"][0] == pytest.approx(nmos_epi["center"][0])
    assert all("direction" in part["metadata"] for part in path)
    assert {row["id"] for row in scene["latchup_path"]["hotspots"]} == {"injection", "well_boundary", "tap"}
    assert {annotation["text"] for annotation in scene["annotations"]} == {
        "P+", "N+", "N-Well", "P-Well", "PNP", "NPN", "VDD", "VSS"}
    assert set(structure_topology.LATCHUP_REFERENCES) <= {reference["url"] for reference in scene["references"]}


def test_latchup_guard_ring_can_be_omitted_without_removing_taps():
    template = model.build_scene(model._empty())["parts"]
    scene = structure_topology.build_latchup(template, include_guard_ring=False)

    assert not any(part["role"] == "guard_ring" for part in scene["parts"])
    assert len([part for part in scene["parts"] if part["role"] == "well_tap"]) == 2
    assert scene["topology"]["guard_ring"]["enabled"] is False


def test_logic_annotation_uses_actual_facet_metadata_and_custom_shape():
    scene = model.build_scene(model._empty())
    source = next(part for part in scene["parts"] if part["role"] == "source")
    source["metadata"]["facet_angle_deg"] = 48.25
    labels = structure_topology.gaa_annotations(scene["parts"])
    assert len(labels) == 4
    assert "48.25°" in labels[0]["text"]
    assert labels[0]["anchor"] == source["center"]

    source["shape"] = "cylinder"
    labels = structure_topology.gaa_annotations(scene["parts"])
    assert "48.25°" not in labels[0]["text"]
    assert "cylinder" in labels[0]["text"]
