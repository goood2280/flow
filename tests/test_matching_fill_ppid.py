import polars as pl
import pytest


def test_ppid_target_uses_fab_ppid_as_value_key(monkeypatch, tmp_path):
    from core import matching_fill as matching

    source = tmp_path / "fab.parquet"
    pl.DataFrame({"ppid": ["PP_A", "PP_B"], "step_id": ["S20", "S30"]}).write_parquet(source)
    monkeypatch.setattr(matching, "_product_files", lambda *args, **kwargs: [source])

    index = matching._product_key_index("ppid", "PRODA", ("value",))

    assert index == {("PP_A",), ("PP_B",)}


def test_mask_target_uses_fab_reticle_as_value_key(monkeypatch, tmp_path):
    from core import matching_fill as matching

    source = tmp_path / "fab.parquet"
    pl.DataFrame({
        "reticle_id": ["RET_A", "RET_B"],
        "step_id": ["S20", "S30"],
    }).write_parquet(source)
    monkeypatch.setattr(matching, "_product_files", lambda *args, **kwargs: [source])

    index = matching._fab_reticle_step_index("mask", "PRODA")

    assert index == {"ret_a": ["S20"], "ret_b": ["S30"]}


def test_ppid_scan_proposes_product_step_and_vehicle_desc_in_configured_order(monkeypatch):
    from core import matching_fill as matching

    store = {"settings": {}, "proposals": {}}
    knob_columns = ["feature_name", "function_step", "value"]
    knob_rows = [{"feature_name": "10.0 CONTACT", "function_step": "CONTACT", "value": "PP_A"}]
    vehicle_columns = ["product", "step_id", "step_desc"]
    vehicle_rows = [
        {"product": "PRODB", "step_id": "S10", "step_desc": "EARLY"},
        {"product": "PRODA", "step_id": "S20", "step_desc": "LATE"},
    ]
    monkeypatch.setattr(
        matching, "_read_csv",
        lambda target: (vehicle_columns, vehicle_rows) if target == "vehicle" else (knob_columns, knob_rows),
    )
    monkeypatch.setattr(matching, "list_products", lambda target: ["PRODA", "PRODB"])
    monkeypatch.setattr(
        matching, "_fab_ppid_step_index",
        lambda target, product, limit=0: {"pp_a": ["S20"]} if product == "PRODA" else {"pp_a": ["S10"]},
    )
    monkeypatch.setattr(matching, "settings", lambda: {
        "prefix_rules": [], "module_rules": [], "max_files_per_product": 0,
    })
    monkeypatch.setattr(matching, "_load_store", lambda: store)
    monkeypatch.setattr(matching, "_save_store", lambda data: None)

    product = matching.scan("ppid", column="product", username="tester")
    step_id = matching.scan("ppid", column="step_id", username="tester")
    step_desc = matching.scan("ppid", column="step_desc", username="tester")

    assert product["rows"][0]["proposed"] == "PRODB, PRODA"
    assert step_id["rows"][0]["proposed"] == "S10, S20"
    assert step_desc["rows"][0]["proposed"] == "EARLY, LATE"
    assert step_desc["rows"][0]["scoped"] == [
        "PRODB · S10 · EARLY", "PRODA · S20 · LATE",
    ]


def test_mask_scan_proposes_product_step_and_vehicle_desc(monkeypatch):
    from core import matching_fill as matching

    store = {"settings": {}, "proposals": {}}
    mask_columns = ["reticle_id", "mask_version"]
    mask_rows = [{"reticle_id": "RET_A", "mask_version": "M3"}]
    vehicle_columns = ["product", "step_id", "step_desc"]
    vehicle_rows = [{"product": "PRODA", "step_id": "S20", "step_desc": "PHOTO"}]
    monkeypatch.setattr(
        matching, "_read_csv",
        lambda target: (vehicle_columns, vehicle_rows) if target == "vehicle" else (mask_columns, mask_rows),
    )
    monkeypatch.setattr(matching, "list_products", lambda target: ["PRODA"])
    monkeypatch.setattr(
        matching, "_fab_reticle_step_index",
        lambda target, product, limit=0: {"ret_a": ["S20"]},
    )
    monkeypatch.setattr(matching, "settings", lambda: {
        "prefix_rules": [], "module_rules": [], "max_files_per_product": 0,
    })
    monkeypatch.setattr(matching, "_load_store", lambda: store)
    monkeypatch.setattr(matching, "_save_store", lambda data: None)

    product = matching.scan("mask", column="product", username="tester")
    step_id = matching.scan("mask", column="step_id", username="tester")
    step_desc = matching.scan("mask", column="step_desc", username="tester")

    assert product["rows"][0]["proposed"] == "PRODA"
    assert step_id["rows"][0]["proposed"] == "S20"
    assert step_desc["rows"][0]["proposed"] == "PHOTO"
    assert step_desc["rows"][0]["scoped"] == ["PRODA · S20 · PHOTO"]


def test_mask_target_reads_and_applies_mask_info_csv(monkeypatch, tmp_path):
    from core import matching_fill as matching
    from core import valve_alerts

    mask_info = tmp_path / "mask_info.csv"
    legacy_mask = tmp_path / "mask.csv"
    mask_info.write_text("reticle_id,mask\nRET_A,MASK_A\n", encoding="utf-8")
    legacy_mask.write_text("reticle_id,mask\nLEGACY,OLD\n", encoding="utf-8")
    monkeypatch.setattr(matching, "_db_root", lambda: tmp_path)

    columns, rows = matching._read_csv("mask")
    assert matching.TARGETS["mask"]["file"] == "mask_info.csv"
    assert columns == ["reticle_id", "mask"]
    assert rows[0]["reticle_id"] == "RET_A"

    proposal = {
        "target": "mask",
        "column": "product",
        "file": "mask_info.csv",
        "scanned_at": "2026-09-03T09:00:00",
        "applied": False,
        "add_column": True,
        "rows": [{
            "i": 0, "status": "fill", "current": "", "proposed": "PRODA",
        }],
    }
    monkeypatch.setattr(matching, "get_proposal", lambda *args, **kwargs: proposal)
    monkeypatch.setattr(valve_alerts, "_after_write", lambda *args, **kwargs: {})

    result = matching.apply_proposal(
        "mask", column="product", expected_scanned_at=proposal["scanned_at"],
    )

    columns, rows = matching._read_csv("mask")
    assert result["file"] == "mask_info.csv"
    assert columns == ["product", "reticle_id", "mask"]
    assert rows[0]["product"] == "PRODA"
    assert legacy_mask.read_text(encoding="utf-8") == "reticle_id,mask\nLEGACY,OLD\n"


def test_stale_mask_csv_proposal_is_hidden(monkeypatch):
    from core import matching_fill as matching

    monkeypatch.setattr(matching, "_load_store", lambda: {
        "proposals": {
            "mask:product": {"target": "mask", "column": "product", "file": "mask.csv"},
        },
    })

    assert matching.get_proposal("mask", "product") is None


def test_split_knob_meta_prefers_product_scoped_fab_step_columns(monkeypatch, tmp_path):
    from routers import splittable

    knob_file = tmp_path / "ppid_knob.csv"
    knob_file.write_text("placeholder", encoding="utf-8")
    monkeypatch.setattr(splittable, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_load_csv_rows", lambda path: [{
        "feature_name": "10.0 CONTACT",
        "function_step": "LEGACY_DESC",
        "value": "PP_A",
        "operator": "eq",
        "rule_order": "R1",
        "product": "PRODA, PRODB",
        "step_id": "S10, S20",
        "step_desc": "EARLY, LATE",
    }])
    monkeypatch.setattr(splittable, "_sch", lambda name: {
        "feature_col": "feature_name", "step_desc_col": "function_step",
        "value_col": "value", "operator_col": "operator", "rule_order_col": "rule_order",
        "category_col": "category",
    } if name == "knob_ppid" else {})
    monkeypatch.setattr(splittable, "_product_step_map_by_desc", lambda *args, **kwargs: {
        "early": [{"step_id": "S10", "step_desc": "EARLY", "module": "M1"}],
        "late": [{"step_id": "S20", "step_desc": "LATE", "module": "M2"}],
    })
    monkeypatch.setattr(splittable, "_inferred_stage_meta", lambda *args, **kwargs: {})

    meta = splittable._build_knob_meta("ML_TABLE_PRODB")
    group = meta["KNOB_10.0 CONTACT"]["groups"][0]

    assert group["step_ids"] == ["S20"]
    assert group["step_desc"] == "LATE"
    assert group["module"] == "M2"


def test_split_knob_meta_preserves_comma_inside_single_product_step_desc(monkeypatch, tmp_path):
    from routers import splittable

    knob_file = tmp_path / "ppid_knob.csv"
    knob_file.write_text(
        'feature_name,rule_order,step_desc,operator,value,category,product,step_id\n'
        'KNOB_A,R1,"ETCH, CLEAN",eq,PP_A,S1,PRODA,S10\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(splittable, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_product_step_map_by_desc", lambda *args, **kwargs: {})
    monkeypatch.setattr(splittable, "_inferred_stage_meta", lambda *args, **kwargs: {})

    meta = splittable._build_knob_meta("ML_TABLE_PRODA")
    group = meta["KNOB_A"]["groups"][0]

    assert group["step_desc"] == "ETCH, CLEAN"
    assert group["step_ids"] == ["S10"]


def test_apply_requires_the_exact_preview_revision(monkeypatch):
    from core import matching_fill as matching

    proposal = {
        "target": "vehicle", "column": "product", "file": "Vehicle_matching.csv",
        "scanned_at": "2026-09-03T09:00:00", "applied": False, "rows": [],
    }
    monkeypatch.setattr(matching, "get_proposal", lambda *args, **kwargs: proposal)

    with pytest.raises(ValueError, match="Before/After"):
        matching.apply_proposal(
            "vehicle", column="product", expected_scanned_at="2026-09-03T08:59:59",
        )


def test_apply_rejects_csv_changed_after_preview(monkeypatch, tmp_path):
    from core import matching_fill as matching

    source = tmp_path / "Vehicle_matching.csv"
    source.write_text("product,step_id\nOTHER,S20\n", encoding="utf-8")
    proposal = {
        "target": "vehicle", "column": "product", "file": source.name,
        "scanned_at": "2026-09-03T09:00:00", "applied": False,
        "rows": [{
            "i": 0, "status": "change", "current": "BEFORE", "proposed": "AFTER",
        }],
    }
    monkeypatch.setattr(matching, "get_proposal", lambda *args, **kwargs: proposal)
    monkeypatch.setattr(matching, "_csv_path", lambda *args, **kwargs: source)

    with pytest.raises(ValueError, match="검사 후 변경"):
        matching.apply_proposal(
            "vehicle", column="product", expected_scanned_at=proposal["scanned_at"],
        )

    assert source.read_text(encoding="utf-8") == "product,step_id\nOTHER,S20\n"
