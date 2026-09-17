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
    knob_columns = ["feature_name", "function_step", "value", "product", "step_id", "step_desc"]
    knob_rows = [{"feature_name": "10.0 CONTACT", "function_step": "CONTACT", "value": "PP_A", "product": "", "step_id": "", "step_desc": ""}]
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
    mask_columns = ["reticle_id", "mask_version", "product", "step_id", "step_desc"]
    mask_rows = [{"reticle_id": "RET_A", "mask_version": "M3", "product": "", "step_id": "", "step_desc": ""}]
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


def test_mask_native_two_column_schema_fills_existing_mask_with_product(monkeypatch):
    from core import matching_fill as matching

    store = {"settings": {}, "proposals": {}}
    mask_columns = ["reticle_id", "mask"]
    mask_rows = [{"reticle_id": "RET_A", "mask": ""}]
    vehicle_columns = ["vehicle", "step_id", "step_desc"]
    vehicle_rows = [{"vehicle": "PRODA", "step_id": "S20", "step_desc": "PHOTO"}]
    monkeypatch.setattr(matching, "_read_csv", lambda target: (
        (vehicle_columns, vehicle_rows) if target == "vehicle" else (mask_columns, mask_rows)
    ))
    monkeypatch.setattr(matching, "list_products", lambda target: ["PRODA"])
    monkeypatch.setattr(matching, "_fab_reticle_step_index", lambda *args, **kwargs: {"ret_a": ["S20"]})
    monkeypatch.setattr(matching, "settings", lambda: {"prefix_rules": [], "module_rules": [], "max_files_per_product": 0})
    monkeypatch.setattr(matching, "_load_store", lambda: store)
    monkeypatch.setattr(matching, "_save_store", lambda data: None)

    proposal = matching.scan("mask", column="mask")
    assert proposal["rows"][0]["proposed"] == "PRODA"
    assert proposal["add_column"] is False


def test_vehicle_only_schema_fills_existing_vehicle_column(monkeypatch):
    from core import matching_fill as matching

    store = {"settings": {}, "proposals": {}}
    columns = ["vehicle", "step_id"]
    rows = [{"vehicle": "", "step_id": "S20"}]
    monkeypatch.setattr(matching, "_read_csv", lambda target: (columns, rows))
    monkeypatch.setattr(matching, "list_products", lambda target: ["PRODA"])
    monkeypatch.setattr(matching, "_product_key_index", lambda *args, **kwargs: {("S20",)})
    monkeypatch.setattr(matching, "settings", lambda: {"prefix_rules": [], "module_rules": [], "max_files_per_product": 0})
    monkeypatch.setattr(matching, "_load_store", lambda: store)
    monkeypatch.setattr(matching, "_save_store", lambda data: None)

    proposal = matching.scan("vehicle", column="vehicle")
    assert proposal["rows"][0]["proposed"] == "PRODA"
    assert matching.get_proposal("vehicle", "vehicle") is proposal
    assert proposal["column"] == "vehicle"


def test_inline_scan_matches_raw_item_id_by_item_desc_alias(monkeypatch):
    from core import matching_fill as matching

    store = {"settings": {}, "proposals": {}}
    columns = ["product", "step_id", "item_id", "item_desc", "module"]
    rows = [{
        "product": "", "step_id": "AA100500", "item_id": "ITEM_5",
        "item_desc": "5.0 PC", "module": "",
    }]
    captured = {}
    monkeypatch.setattr(matching, "_read_csv", lambda target: (columns, rows))
    monkeypatch.setattr(matching, "list_products", lambda target: ["PRODA"])

    def product_index(target, product, keys, limit, wanted):
        captured["wanted"] = wanted
        return {("AA100500", "5.0 PC")}

    monkeypatch.setattr(matching, "_product_key_index", product_index)
    monkeypatch.setattr(matching, "settings", lambda: {
        "prefix_rules": [], "module_rules": [], "max_files_per_product": 0,
    })
    monkeypatch.setattr(matching, "_load_store", lambda: store)
    monkeypatch.setattr(matching, "_save_store", lambda data: None)

    proposal = matching.scan("inline", column="product")

    assert ("AA100500", "ITEM_5") in captured["wanted"]
    assert ("AA100500", "5.0 PC") in captured["wanted"]
    assert proposal["rows"][0]["proposed"] == "PRODA"
    assert proposal["rows"][0]["keys"]["item_id"] == "5.0 PC / ITEM_5"


def test_inline_module_scan_accepts_spaced_case_variant_headers_without_db_scan(monkeypatch):
    from core import matching_fill as matching

    store = {"settings": {}, "proposals": {}}
    columns = ["Product", "Step ID", "Item ID", "Module"]
    rows = [{"Product": "PRODA", "Step ID": "AA100500", "Item ID": "ITEM_5", "Module": ""}]
    monkeypatch.setattr(matching, "_read_csv", lambda target: (columns, rows))
    monkeypatch.setattr(matching, "settings", lambda: {
        "prefix_rules": [],
        "module_rules": [{"prefix": "AA", "breaks": [{"from": 100000, "module": "PC"}]}],
        "max_files_per_product": 0,
    })
    monkeypatch.setattr(matching, "list_products", lambda target: pytest.fail("module scan must not read DB"))
    monkeypatch.setattr(matching, "_load_store", lambda: store)
    monkeypatch.setattr(matching, "_save_store", lambda data: None)

    proposal = matching.scan("inline", column="module")

    assert proposal["counts"] == {"fill": 1, "change": 0, "same": 0, "miss": 0}
    assert proposal["rows"][0]["proposed"] == "PC"


def test_inline_product_index_falls_back_across_mixed_parquet_schemas(monkeypatch, tmp_path):
    from core import matching_fill as matching

    step_only = tmp_path / "step_only.parquet"
    full = tmp_path / "full.parquet"
    pl.DataFrame({"step_id": ["AA100500"]}).write_parquet(step_only)
    pl.DataFrame({"step_id": ["AA100500"], "item_id": ["5.0 PC"]}).write_parquet(full)
    monkeypatch.setattr(matching, "_product_files", lambda *args, **kwargs: [step_only, full])

    index = matching._product_key_index(
        "inline", "PRODA", ("step_id", "item_id"), wanted={("AA100500", "5.0 PC")},
    )

    assert ("AA100500", "*") in index
    assert ("AA100500", "5.0 PC") in index


@pytest.mark.parametrize("target,column,filename,source", [
    ("mask", "mask", "mask_info.csv", "reticle_id,mask\nRET_A,\n"),
    ("vehicle", "vehicle", "Vehicle_matching.csv", "vehicle,step_id,step_desc\n,S20,PHOTO\n"),
])
def test_native_product_column_scan_apply_preserves_schema(monkeypatch, tmp_path, target, column, filename, source):
    from core import matching_fill as matching
    from core import valve_alerts

    path = tmp_path / filename
    path.write_text(source, encoding="utf-8")
    store = {"settings": {}, "proposals": {}}
    monkeypatch.setattr(matching, "_db_root", lambda: tmp_path)
    monkeypatch.setattr(matching, "list_products", lambda _target: ["PRODA", "PRODB"])
    monkeypatch.setattr(matching, "_fab_reticle_step_index", lambda *args: {"ret_a": ["S20"]})
    monkeypatch.setattr(matching, "_product_key_index", lambda *args: {("S20",)})
    monkeypatch.setattr(matching, "settings", lambda: {"prefix_rules": [], "module_rules": [], "max_files_per_product": 0})
    monkeypatch.setattr(matching, "_load_store", lambda: store)
    monkeypatch.setattr(matching, "_save_store", lambda _data: None)
    monkeypatch.setattr(valve_alerts, "_after_write", lambda *args: {})
    original_columns, _ = matching._read_csv(target)
    proposal = matching.scan(target, column=column)
    result = matching.apply_proposal(target, column=column, expected_scanned_at=proposal["scanned_at"])
    columns, rows = matching._read_csv(target)
    assert columns == original_columns
    assert rows[0][column] == "PRODA, PRODB"
    assert result["added_column"] is False
    assert matching.get_proposal(target, column)["applied"] is True


def test_mask_target_reads_and_applies_mask_info_csv(monkeypatch, tmp_path):
    from core import matching_fill as matching
    from core import valve_alerts

    mask_info = tmp_path / "mask_info.csv"
    legacy_mask = tmp_path / "mask.csv"
    mask_info.write_text("reticle_id,mask,product\nRET_A,MASK_A,\n", encoding="utf-8")
    legacy_mask.write_text("reticle_id,mask\nLEGACY,OLD\n", encoding="utf-8")
    monkeypatch.setattr(matching, "_db_root", lambda: tmp_path)

    columns, rows = matching._read_csv("mask")
    assert matching.TARGETS["mask"]["file"] == "mask_info.csv"
    assert columns == ["reticle_id", "mask", "product"]
    assert rows[0]["reticle_id"] == "RET_A"

    proposal = {
        "target": "mask",
        "column": "product",
        "file": "mask_info.csv",
        "scanned_at": "2026-09-03T09:00:00",
        "applied": False,
        "add_column": False,
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
    assert columns == ["reticle_id", "mask", "product"]
    assert rows[0]["product"] == "PRODA"
    assert legacy_mask.read_text(encoding="utf-8") == "reticle_id,mask\nLEGACY,OLD\n"


def test_vehicle_lookup_treats_dotted_product_cell_as_two_products(monkeypatch):
    from core import matching_fill as matching

    monkeypatch.setattr(matching, "_read_csv", lambda target: (
        ["product", "step_id", "step_desc"],
        [{"product": "PRODA.PRODB", "step_id": "S10", "step_desc": "CONTACT"}],
    ))
    exact, fallback = matching._vehicle_step_desc_lookup()

    assert exact[("proda", "s10")] == ("CONTACT", 0)
    assert exact[("prodb", "s10")] == ("CONTACT", 0)
    assert fallback == {}


def test_apply_rejects_stale_proposal_for_missing_column(monkeypatch, tmp_path):
    from core import matching_fill as matching

    source = tmp_path / "mask_info.csv"
    source.write_text("reticle_id,mask\nRET_A,MASK_A\n", encoding="utf-8")
    proposal = {
        "target": "mask", "column": "product", "file": source.name,
        "scanned_at": "2026-09-03T09:00:00", "applied": False,
        "rows": [{"i": 0, "status": "fill", "current": "", "proposed": "PRODA"}],
    }
    monkeypatch.setattr(matching, "get_proposal", lambda *args, **kwargs: proposal)
    monkeypatch.setattr(matching, "_csv_path", lambda *args, **kwargs: source)

    with pytest.raises(ValueError, match="열이 없어"):
        matching.apply_proposal("mask", column="product", expected_scanned_at=proposal["scanned_at"])
    assert source.read_text(encoding="utf-8") == "reticle_id,mask\nRET_A,MASK_A\n"


def test_apply_preserves_mixed_case_header_and_marks_canonical_proposal(monkeypatch, tmp_path):
    from core import matching_fill as matching
    from core import valve_alerts

    source = tmp_path / "Vehicle_matching.csv"
    source.write_text("Product,step_id\n, S20\n", encoding="utf-8")
    proposal = {
        "target": "vehicle", "column": "product", "file": source.name,
        "scanned_at": "2026-09-03T09:00:00", "applied": False,
        "rows": [{"i": 0, "status": "fill", "current": "", "proposed": "PRODA"}],
    }
    store = {"proposals": {"vehicle:product": proposal}}
    monkeypatch.setattr(matching, "get_proposal", lambda *args, **kwargs: proposal)
    monkeypatch.setattr(matching, "_csv_path", lambda *args, **kwargs: source)
    monkeypatch.setattr(matching, "_load_store", lambda: store)
    monkeypatch.setattr(valve_alerts, "_after_write", lambda *args, **kwargs: {})
    monkeypatch.setattr(matching, "_save_store", lambda data: None)

    matching.apply_proposal("vehicle", column="product", expected_scanned_at=proposal["scanned_at"])
    assert source.read_text(encoding="utf-8") == "Product,step_id\nPRODA, S20\n"
    assert store["proposals"]["vehicle:product"]["applied"] is True


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
    monkeypatch.setattr(splittable, "_product_step_map_by_desc", lambda *args, **kwargs: {
        "etch, clean": [{"step_id": "S10", "step_desc": "ETCH, CLEAN", "module": ""}],
        "s10": [{"step_id": "S10", "step_desc": "ETCH, CLEAN", "module": ""}],
    })
    monkeypatch.setattr(splittable, "_inferred_stage_meta", lambda *args, **kwargs: {})

    meta = splittable._build_knob_meta("ML_TABLE_PRODA")
    group = meta["KNOB_A"]["groups"][0]

    assert group["step_desc"] == "ETCH, CLEAN"
    assert group["step_ids"] == ["S10"]


def test_split_knob_meta_filters_legacy_direct_steps_by_vehicle_product(monkeypatch, tmp_path):
    from routers import splittable

    knob_file = tmp_path / "ppid_knob.csv"
    knob_file.write_text("placeholder", encoding="utf-8")
    monkeypatch.setattr(splittable, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_load_csv_rows", lambda path: [{
        "feature_name": "10.0 CONTACT",
        "function_step": "CONTACT",
        "value": "PP_A",
        "operator": "eq",
        "rule_order": "R1",
        "step_id": "A100, B100, A100",
    }])
    monkeypatch.setattr(splittable, "_sch", lambda name: {
        "feature_col": "feature_name", "step_desc_col": "function_step",
        "value_col": "value", "operator_col": "operator", "rule_order_col": "rule_order",
        "category_col": "category",
    } if name == "knob_ppid" else {})
    monkeypatch.setattr(splittable, "_product_step_map_by_desc", lambda *args, **kwargs: {
        "contact": [{"step_id": "A100", "step_desc": "CONTACT", "module": "M1"}],
        "a100": [{"step_id": "A100", "step_desc": "CONTACT", "module": "M1"}],
    })
    monkeypatch.setattr(splittable, "_inferred_stage_meta", lambda *args, **kwargs: {})

    meta = splittable._build_knob_meta("ML_TABLE_PRODA")
    group = meta["KNOB_10.0 CONTACT"]["groups"][0]

    assert group["step_ids"] == ["A100"]


def test_split_knob_meta_rejects_step_shaped_desc_outside_vehicle_product(monkeypatch, tmp_path):
    from routers import splittable

    knob_file = tmp_path / "ppid_knob.csv"
    knob_file.write_text("placeholder", encoding="utf-8")
    monkeypatch.setattr(splittable, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_load_csv_rows", lambda path: [{
        "feature_name": "FOREIGN_STEP",
        "function_step": "BB2000",
        "value": "PP_B",
        "operator": "eq",
        "rule_order": "R1",
    }])
    monkeypatch.setattr(splittable, "_sch", lambda name: {
        "feature_col": "feature_name", "step_desc_col": "function_step",
        "value_col": "value", "operator_col": "operator", "rule_order_col": "rule_order",
        "category_col": "category",
    } if name == "knob_ppid" else {})
    monkeypatch.setattr(splittable, "_product_step_map_by_desc", lambda *args, **kwargs: {
        "aa1000": [{"step_id": "AA1000", "step_desc": "OWN_STEP", "module": "M1"}],
    })
    monkeypatch.setattr(splittable, "_inferred_stage_meta", lambda *args, **kwargs: {})

    meta = splittable._build_knob_meta("ML_TABLE_PRODA")

    assert meta["KNOB_FOREIGN_STEP"]["groups"][0]["step_ids"] == []


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
