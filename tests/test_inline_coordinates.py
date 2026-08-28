import csv
import json

from core.inline_coordinates import (
    is_summary_subitem, load_coordinate_mapping, load_coordinate_rows, load_matching_rules,
)


def test_summary_subitems_are_excluded():
    for value in ("avg", "MED", "std_dev", "MIN", "max", "Q1", "q-3"):
        assert is_summary_subitem(value)
    assert not is_summary_subitem("01")
    assert not is_summary_subitem("A03")


def test_matching_table_flattens_only_named_map_positions(tmp_path):
    with (tmp_path / "inline_shot_matching.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["product", "step_id", "item_id", "map_name"])
        writer.writeheader()
        writer.writerow({"product": "PRODA", "step_id": "AA100001", "item_id": "CD1", "map_name": "MAP_A"})
    settings = tmp_path / "credential" / "inline_map_settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"tables": [{
        "table_name": "MAP_A", "vehicle": "PRODA",
        "shots": [
            {"name": "1", "shot_x": -2, "shot_y": 3},
            {"name": "AVG", "shot_x": 0, "shot_y": 0},
        ],
    }]}), encoding="utf-8")

    rows = load_coordinate_rows(tmp_path, products=["proda"], item_ids=["cd1"])

    assert rows == [{
        "product": "proda", "step_id": "aa100001", "item_id": "cd1",
        "subitem_id": "1", "shot_x": -2.0, "shot_y": 3.0,
        "matching_table": "MAP_A",
    }]


def test_unknown_table_does_not_guess_coordinates(tmp_path):
    (tmp_path / "inline_shot_matching.csv").write_text(
        "product,step_id,item_id,map_name\nPRODA,AA100001,CD1,MISSING\n",
        encoding="utf-8",
    )
    mapping = load_coordinate_mapping(tmp_path, products=["PRODA"], item_ids=["CD1"])
    assert mapping["configured"] is True
    assert mapping["missing_tables"] == ["MISSING"]
    assert mapping["rows"] == []


def test_matching_rules_report_item_specific_table_availability(tmp_path):
    # 기존 배포 파일도 계속 읽어 무중단으로 신규 파일로 이관할 수 있다.
    (tmp_path / "inline_matching.csv").write_text(
        "product,step_id,item_id,matching_table\n"
        "PRODA,S1,CD_A,MAP_A\nPRODA,S1,CD_B,MISSING\n",
        encoding="utf-8",
    )
    settings = tmp_path / "credential" / "inline_map_settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"tables": [{
        "table_name": "MAP_A", "vehicle": "VH_A",
        "shots": [{"name": "SITE_1", "shot_x": 0, "shot_y": 1}],
    }]}), encoding="utf-8")

    rules = load_matching_rules(tmp_path, products=["proda"])

    assert rules == [
        {"product": "PRODA", "step_id": "S1", "item_id": "CD_A",
         "matching_table": "MAP_A", "available": True, "vehicle": "VH_A", "shot_count": 1},
        {"product": "PRODA", "step_id": "S1", "item_id": "CD_B",
         "matching_table": "MISSING", "available": False, "vehicle": "", "shot_count": 0},
    ]
