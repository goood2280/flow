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
        "matching_table": "MAP_A", "vehicle": "PRODA", "rule_file": "inline_shot_matching.csv",
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


def test_matching_rules_prefer_confidential_over_credential(tmp_path):
    (tmp_path / "inline_matching.csv").write_text(
        "product,step_id,item_id,matching_table\n"
        "PRODA,S1,CD_A,MAP_CONF\n",
        encoding="utf-8",
    )
    # Legacy credential
    cred_settings = tmp_path / "credential" / "inline_map_settings.json"
    cred_settings.parent.mkdir()
    cred_settings.write_text(json.dumps({"tables": [{
        "table_name": "MAP_CONF", "vehicle": "VH_OLD",
        "shots": [{"name": "OLD_SITE", "shot_x": 0, "shot_y": 0}],
    }]}), encoding="utf-8")

    # New confidential
    conf_settings = tmp_path / "confidential" / "inline_map_settings.json"
    conf_settings.parent.mkdir()
    conf_settings.write_text(json.dumps({"tables": [{
        "table_name": "MAP_CONF", "vehicle": "VH_NEW",
        "shots": [{"name": "NEW_SITE", "shot_x": 1, "shot_y": 1}],
    }]}), encoding="utf-8")

    rules = load_matching_rules(tmp_path, products=["proda"])
    assert len(rules) == 1
    assert rules[0]["vehicle"] == "VH_NEW"
    assert rules[0]["available"] is True


def test_confidential_product_csv_with_normal_jpg_and_aliases(tmp_path):
    conf_dir = tmp_path / "confidential"
    conf_dir.mkdir()
    # 1) PRODA_cd,tox.csv with step_id, item_id, map (Normal.jpg)
    (conf_dir / "PRODA_cd,tox.csv").write_text(
        "step_id,item_id,map\n"
        "STEP10,CD_GATE,Normal.jpg\n"
        "STEP20,TOX_OX,Normal.jpg\n",
        encoding="utf-8",
    )
    # 2) PRODA_inline_shotmatching.csv with other items
    (conf_dir / "PRODA_inline_shotmatching.csv").write_text(
        "step_id,item_id,map_name\n"
        "STEP30,SPACER_W,Normal\n",
        encoding="utf-8",
    )
    # Settings has table_name="Normal"
    (conf_dir / "inline_map_settings.json").write_text(json.dumps({"tables": [{
        "table_name": "Normal", "vehicle": "PRODA_VH",
        "shots": [
            {"name": "1", "shot_x": 10.0, "shot_y": 20.0},
            {"name": "2", "shot_x": 15.0, "shot_y": 25.0},
            {"name": "AVG", "shot_x": 0.0, "shot_y": 0.0},
        ],
    }]}), encoding="utf-8")

    rules = load_matching_rules(tmp_path, products=["PRODA"])
    assert len(rules) == 3
    for r in rules:
        assert r["product"] == "PRODA"
        assert r["available"] is True
        assert r["shot_count"] == 2  # AVG ignored

    mapping = load_coordinate_mapping(tmp_path, products=["proda"], item_ids=["cd_gate"])
    assert mapping["configured"] is True
    assert len(mapping["rows"]) == 2
    assert mapping["rows"][0]["subitem_id"] == "1"
    assert mapping["rows"][0]["shot_x"] == 10.0
    assert mapping["rows"][0]["shot_y"] == 20.0




def test_saved_subitem_id_is_authoritative_and_nonfinite_is_rejected(tmp_path):
    root = tmp_path / "confidential"
    root.mkdir()
    (root / "PRODA_inline_shotmatching.csv").write_text("step_id,item_id,map_name\nS1,CD_A,Normal.jpg\n", encoding="utf-8")
    (root / "inline_map_settings.json").write_text(json.dumps({"tables": [{"table_name": "Normal", "vehicle": "VH_A", "shots": [
        {"subitem_id": "SITE1", "name": "OLD", "shot_x": 1, "shot_y": 2},
        {"subitem_id": "AVG", "name": "SITE2", "shot_x": 0, "shot_y": 0},
        {"subitem_id": "SITE3", "shot_x": "NaN", "shot_y": 1}
    ]}]}), encoding="utf-8")
    rows = load_coordinate_rows(tmp_path, products=["PRODA"])
    assert len(rows) == 1
    assert rows[0]["subitem_id"] == "site1"
    assert rows[0]["vehicle"] == "VH_A"
    assert rows[0]["matching_table"] == "Normal"
