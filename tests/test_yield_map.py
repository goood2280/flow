from pathlib import Path
from types import SimpleNamespace

import polars as pl

from core import yield_map


def _sample_source(tmp_path: Path) -> tuple[Path, str]:
    db_root = tmp_path / "DB"
    source_id = "1.RAWDATA_DB_EDS/WAFER_BIN"
    product_dir = db_root / source_id / "product=PROD_A"
    product_dir.mkdir(parents=True)
    (product_dir / "part.csv").write_text(
        "chip_x_pos,chip_y_pos,BIN,lot_id,wafer_id\n"
        "-3,10,1,L1,1\n"
        "-1,10,2,L1,1\n"
        "4,20,9,L2,2\n",
        encoding="utf-8",
    )
    return db_root, source_id


def test_yield_map_discovers_bin_table_and_filters_product_map(tmp_path, monkeypatch):
    db_root, source_id = _sample_source(tmp_path)
    monkeypatch.setattr(yield_map, "PATHS", SimpleNamespace(db_root=db_root, data_root=tmp_path))
    monkeypatch.setattr(yield_map, "CONFIG_PATH", tmp_path / "yield_map.json")

    sources = yield_map.discover_sources()
    assert [item["id"] for item in sources] == [source_id]
    assert sources[0]["products"] == ["PROD_A"]

    preview = yield_map.preview(source_id, "PROD_A")
    assert preview["detected_fields"]["x"] == "chip_x_pos"
    assert preview["detected_fields"]["y"] == "chip_y_pos"
    assert preview["detected_fields"]["bin"] == "BIN"

    yield_map.save_product_config("PROD_A", {
        "source": source_id,
        "fields": preview["detected_fields"],
        "bin_map": [
            {"bin": "2", "bin_color": "#ff0000"},
            {"bin": "1", "bin_color": "#00ff00"},
            {"bin": "bad", "bin_color": "invalid"},
        ],
    })
    result = yield_map.map_data("PROD_A", lot_id="L1", wafer_id="1")
    assert len(result["rows"]) == 2
    assert result["net_die"] == 2
    assert result["bin_colors"] == {"2": "#FF0000", "1": "#00FF00"}
    assert result["bins"] == [{"bin": "1", "count": 1}, {"bin": "2", "count": 1}]
    saved = yield_map.product_config("PROD_A")
    assert saved["bin_map"] == [
        {"bin": "2", "bin_color": "#FF0000"},
        {"bin": "1", "bin_color": "#00FF00"},
    ]


def test_yield_map_detects_bin_no_for_bin_sources_without_msr_mapping():
    fields = yield_map.detect_fields([
        "root_lot_id", "wafer_id", "chip_x_pos", "chip_y_pos", "bin_no", "msr",
    ])

    assert fields["bin"] == "bin_no"
    assert "msr" not in fields


def test_yield_map_does_not_discover_msr_only_sources(tmp_path, monkeypatch):
    db_root = tmp_path / "DB"
    msr_dir = db_root / "1.RAWDATA_DB_EDS" / "WAFER_MSR" / "product=PROD_A"
    msr_dir.mkdir(parents=True)
    (msr_dir / "part.csv").write_text("chip_x_pos,chip_y_pos,value\n0,0,1.2\n", encoding="utf-8")
    monkeypatch.setattr(yield_map, "PATHS", SimpleNamespace(db_root=db_root, data_root=tmp_path))

    assert yield_map.discover_sources() == []


def test_product_db_column_mapping_overrides_auto_detection(tmp_path, monkeypatch):
    config_path = tmp_path / "yield_map_shot_fields.json"
    monkeypatch.setattr(yield_map, "SHOT_FIELD_CONFIG_PATH", config_path)
    config_path.write_text(
        '{"version":1,"products":{"PROD_A":{"et":{"shot_x":"ET_X","shot_y":"ET_Y"}}}}',
        encoding="utf-8",
    )

    columns = [
        "root_lot_id", "wafer_id", "chip_x_pos", "chip_y_pos",
        "ET_X", "ET_Y", "item_id", "value",
    ]
    automatic = yield_map.detect_shot_fields(columns)
    configured = yield_map.detect_shot_fields(columns, "prod_a", "ET")

    assert automatic["shot_x"] == "chip_x_pos"
    assert automatic["shot_y"] == "chip_y_pos"
    assert configured["shot_x"] == "ET_X"
    assert configured["shot_y"] == "ET_Y"


def test_yield_map_rejects_source_outside_db_root(tmp_path, monkeypatch):
    db_root = tmp_path / "DB"
    db_root.mkdir()
    monkeypatch.setattr(yield_map, "PATHS", SimpleNamespace(db_root=db_root, data_root=tmp_path))
    try:
        yield_map.resolve_source("../outside_BIN.csv")
    except ValueError as exc:
        assert "벗어납니다" in str(exc)
    else:
        raise AssertionError("path traversal source must be rejected")


def test_yield_map_does_not_fall_back_to_another_product_partition(tmp_path, monkeypatch):
    db_root, source_id = _sample_source(tmp_path)
    monkeypatch.setattr(yield_map, "PATHS", SimpleNamespace(db_root=db_root, data_root=tmp_path))
    assert yield_map.source_files(source_id, "PROD_B") == []


def test_yield_map_lists_only_products_backed_by_bin_sources(tmp_path, monkeypatch):
    db_root, source_id = _sample_source(tmp_path)
    monkeypatch.setattr(yield_map, "PATHS", SimpleNamespace(db_root=db_root, data_root=tmp_path))
    monkeypatch.setattr(yield_map, "CONFIG_PATH", tmp_path / "yield_map.json")
    sources = yield_map.discover_sources()

    assert yield_map.available_products(sources, {}) == ["PROD_A"]
    assert yield_map.available_products(sources, {
        "PROD_A": {"source": source_id},
        "PROD_WITHOUT_BIN": {"source": "OTHER_TABLE"},
    }) == ["PROD_A"]


def test_yield_map_root_lot_query_returns_all_wafers_for_trellis(tmp_path, monkeypatch):
    db_root = tmp_path / "DB"
    source_id = "1.RAWDATA_DB_EDS/WAFER_BIN"
    product_dir = db_root / source_id / "product=PROD_A"
    product_dir.mkdir(parents=True)
    (product_dir / "part.csv").write_text(
        "chip_x_pos,chip_y_pos,BIN,root_lot_id,wafer_id\n"
        "0,0,1,ROOT_A,1\n1,0,2,ROOT_A,1\n"
        "0,0,1,ROOT_A,2\n1,0,1,ROOT_A,2\n"
        "0,0,9,ROOT_B,3\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(yield_map, "PATHS", SimpleNamespace(db_root=db_root, data_root=tmp_path))
    monkeypatch.setattr(yield_map, "CONFIG_PATH", tmp_path / "yield_map.json")
    yield_map.save_product_config("PROD_A", {
        "source": source_id,
        "fields": {
            "x": "chip_x_pos", "y": "chip_y_pos", "bin": "BIN",
            "lot": "root_lot_id", "wafer": "wafer_id",
        },
    })

    result = yield_map.map_data("PROD_A", root_lot_id="ROOT_A")

    assert result["root_lot_id"] == "ROOT_A"
    assert result["wafer_ids"] == ["1", "2"]
    assert result["wafer_count"] == 2
    assert len(result["rows"]) == 4
    assert {row["lot"] for row in result["rows"]} == {"ROOT_A"}


def test_yield_map_scans_full_shots_and_exports_chart_builder_grain(tmp_path, monkeypatch):
    db_root = tmp_path / "DB"
    source_id = "1.RAWDATA_DB_EDS/WAFER_BIN"
    product_dir = db_root / source_id / "product=PROD_A"
    product_dir.mkdir(parents=True)
    (product_dir / "part.csv").write_text(
        "chip_x_pos,chip_y_pos,BIN,lot_id,wafer_id\n"
        "0,0,1,L1,1\n1,0,1,L1,1\n0,1,1,L1,1\n1,1,2,L1,1\n"
        "2,0,1,L1,1\n3,0,1,L1,1\n2,1,1,L1,1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(yield_map, "PATHS", SimpleNamespace(db_root=db_root, data_root=tmp_path))
    monkeypatch.setattr(yield_map, "CONFIG_PATH", tmp_path / "yield_map.json")
    monkeypatch.setattr(yield_map, "_wf_geometry", lambda vehicle: {
        "vehicle": vehicle,
        "display": {"cols": 2, "rows": 2},
        "shots": [{"x": 0, "y": 0}, {"x": 1, "y": 0}],
    })
    config = {
        "source": source_id,
        "fields": {"x": "chip_x_pos", "y": "chip_y_pos", "bin": "BIN", "lot": "lot_id", "wafer": "wafer_id"},
        "bin_map": [{"bin": "1", "bin_color": "#00FF00"}, {"bin": "2", "bin_color": "#FF0000"}],
        "shot_layout": {"enabled": True, "cols": 2, "rows": 2, "origin_x": 0, "origin_y": 0, "good_bins": ["1"]},
    }
    yield_map.save_product_config("PROD_A", config)

    scan = yield_map.scan_shot_layout("PROD_A", {**config, "vehicle": "PROD_A"}, lot_id="L1", wafer_id="1")
    assert scan["full_shot_count"] == 1
    assert scan["partial_shot_count"] == 1
    assert scan["layout"]["expected_die"] == 4
    assert scan["layout"]["origin_x"] == 0
    assert scan["layout"]["origin_y"] == 0
    assert scan["bins"] == [{"bin": "1", "count": 6}, {"bin": "2", "count": 1}]
    assert scan["preview_wafer_id"] == "1"
    assert len(scan["preview_rows"]) == 7

    result = yield_map.map_data("PROD_A", lot_id="L1", wafer_id="1")
    assert result["full_shot_count"] == 1
    assert result["partial_shot_count"] == 1
    assert result["shot_rows"][0]["shot_yield"] == 75.0
    assert result["rows"][3]["die_x_in_shot"] == 1
    assert result["rows"][3]["die_y_in_shot"] == 1
    metrics = yield_map.map_data("PROD_A", lot_id="L1", wafer_id="1", selected_bin="2")["shot_metrics"]
    assert metrics[0]["selected_bin_count"] == 1
    assert metrics[0]["selected_bin_ratio"] == 25.0

    chart = yield_map.shot_yield_frame("PROD_A")
    assert chart.height == 1
    assert chart.to_dicts()[0] == {
        "product": "PROD_A", "root_lot_id": "L1", "lot_id": "L1", "wafer_id": "1",
        "shot_x": 0, "shot_y": 0, "shot_yield": 75.0, "good_die": 3,
        "total_die": 4, "expected_die": 4, "is_full_shot": True,
    }


def test_et_wf_map_uses_native_shot_coordinates_without_subitem(tmp_path, monkeypatch):
    db_root = tmp_path / "DB"
    product_dir = db_root / "1.RAWDATA_DB_ET" / "PROD_A"
    product_dir.mkdir(parents=True)
    (product_dir / "part.csv").write_text(
        "root_lot_id,wafer_id,step_id,step_seq,item_id,shot_x,shot_y,value\n"
        "R1,1,ET100,10,VTH,-2,3,0.2\nR1,1,ET100,10,VTH,-2,3,0.4\n"
        "R1,1,ET100,20,VTH,-2,3,9.9\nR1,2,ET100,10,VTH,1,-1,0.8\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(yield_map, "PATHS", SimpleNamespace(db_root=db_root, data_root=tmp_path))
    monkeypatch.setattr(yield_map, "_wf_geometry", lambda vehicle: {"vehicle": vehicle, "shots": [{"x": -2, "y": 3}]})

    result = yield_map.shot_map_data("et", "PROD_A", "VEH_A", "R1", wafer_id="1", item_id="VTH")

    assert result["rows"] == [{
        "wafer": "1", "shot_x": -2.0, "shot_y": 3.0,
        "value": 0.30000000000000004, "sample_count": 2,
    }]
    assert result["selected_step"] == "ET100"
    assert result["selected_step_seq"] == "10"
    assert result["step_seqs"] == ["10", "20"]
    assert "subitem" not in result["fields"]


def test_et_wide_metric_columns_are_configurable_and_queryable(tmp_path, monkeypatch):
    db_root = tmp_path / "DB"
    product_dir = db_root / "1.RAWDATA_DB_ET" / "WIDE_A"
    product_dir.mkdir(parents=True)
    (product_dir / "part.csv").write_text(
        "root_lot_id,wafer_id,chip_x_pos,chip_y_pos,step_id,VTH,IDSAT\n"
        "R1,1,-2,3,ET100,0.5,600\nR1,1,-2,3,ET100,0.7,620\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(yield_map, "PATHS", SimpleNamespace(db_root=db_root, data_root=tmp_path))
    monkeypatch.setattr(yield_map, "SHOT_FIELD_CONFIG_PATH", tmp_path / "shot_fields.json")
    monkeypatch.setattr(yield_map, "_wf_geometry", lambda vehicle: {
        "vehicle": vehicle, "shots": [{"x": -2, "y": 3}],
    })

    options = yield_map.shot_field_options("WIDE_A", "et")
    assert options["fields"]["shot_x"] == "chip_x_pos"
    assert options["auto_value_columns"] == ["VTH", "IDSAT"]
    saved = yield_map.save_shot_fields(
        "WIDE_A", "et", options["fields"], ["VTH", "IDSAT"],
    )
    assert saved["scope"] == "database"
    assert saved["value_columns"] == ["VTH", "IDSAT"]
    assert yield_map.detect_shot_fields(
        ["root_lot_id", "wafer_id", "chip_x_pos", "chip_y_pos"], "OTHER_PRODUCT", "et",
    )["shot_x"] == "chip_x_pos"
    assert "et" in yield_map._shot_field_config_doc()["databases"]

    result = yield_map.shot_map_data("et", "WIDE_A", "VEH_A", "R1", item_id="IDSAT")
    assert result["selected_item"] == "IDSAT"
    assert result["items"] == ["VTH", "IDSAT"]
    assert result["rows"][0]["value"] == 610.0


def test_et_download_addp_frame_maps_alias_to_shots_and_filters_exact_lot(monkeypatch):
    monkeypatch.setattr(yield_map, "_wf_geometry", lambda vehicle: {
        "vehicle": vehicle, "shots": [{"x": -2, "y": 3}, {"x": 1, "y": -1}],
    })
    frame = pl.DataFrame({
        "root_lot_id": ["R1", "R1", "R1", "R10"],
        "wafer_id": ["1", "1", "1", "9"],
        "step_id": ["ET100", "ET100", "ET100", "ET100"],
        "step_seq": ["10", "10", "20", "10"],
        "shot_x": [-2, -2, -2, 1], "shot_y": [3, 3, 3, -1],
        "PERF_INDEX": [10.0, 14.0, 100.0, 999.0],
    })

    result = yield_map.et_index_map_data(
        "PROD_A", "VEH_A", "R1", frame, "PERF_INDEX",
        items=["VTH_IDX", "PERF_INDEX"],
        item_specs={"PERF_INDEX": {"category": "addp", "addp_form": "{A} * {B}"}},
        item_source="et_download", item_formula="{A} * {B}",
    )

    assert result["rows"] == [{
        "wafer": "1", "shot_x": -2.0, "shot_y": 3.0,
        "value": 12.0, "sample_count": 2,
    }]
    assert result["wafer_ids"] == ["1"]
    assert result["selected_item"] == "PERF_INDEX"
    assert result["selected_step"] == "ET100"
    assert result["selected_step_seq"] == "10"
    assert result["step_seqs"] == ["10", "20"]
    assert result["item_source"] == "et_download"
    assert result["item_spec"]["category"] == "addp"


def test_inline_wf_map_converts_subitem_to_saved_shot_coordinate(tmp_path, monkeypatch):
    db_root = tmp_path / "DB"
    product_dir = db_root / "1.RAWDATA_DB_INLINE" / "PROD_A"
    product_dir.mkdir(parents=True)
    (product_dir / "part.csv").write_text(
        "root_lot_id,wafer_id,step_id,item_id,subitem_id,value\n"
        "R1,1,IN100,THK,SITE_A,10\nR1,1,IN100,THK,SITE_B,20\nR1,1,IN100,THK,UNMAPPED,999\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(yield_map, "PATHS", SimpleNamespace(db_root=db_root, data_root=tmp_path))
    monkeypatch.setattr(yield_map, "_wf_geometry", lambda vehicle: {"vehicle": vehicle, "shots": [{"x": 4, "y": -5}, {"x": 0, "y": 2}]})
    monkeypatch.setattr(yield_map, "_inline_table", lambda vehicle, table_name="": {
        "table_name": "INLINE_A", "vehicle": vehicle,
        "shots": [
            {"shot_x": 4, "shot_y": -5, "subitem_id": "SITE_A"},
            {"shot_x": 0, "shot_y": 2, "name": "SITE_B"},
        ],
    })
    monkeypatch.setattr(yield_map, "inline_matching_rules", lambda product="": [{
        "product": "PROD_A", "step_id": "IN100", "item_id": "THK",
        "matching_table": "INLINE_A", "available": True,
        "vehicle": "VEH_A", "shot_count": 2,
    }])

    result = yield_map.shot_map_data("inline", "PROD_A", "VEH_A", "R1", item_id="THK")

    assert [(row["shot_x"], row["shot_y"], row["value"]) for row in result["rows"]] == [
        (4.0, -5.0, 10.0), (0.0, 2.0, 20.0),
    ]
    assert result["inline_table"] == "INLINE_A"
    assert result["selected_step"] == "IN100"


def test_compare_shot_metrics_joins_yield_and_et_by_wafer_and_shot(monkeypatch):
    monkeypatch.setattr(yield_map, "map_data", lambda *args, **kwargs: {
        "shot_metrics": [
            {"wafer": "1", "shot_x": 2, "shot_y": 3, "value": 97.5,
             "shot_yield": 97.5, "selected_bin_ratio": 2.5},
            {"wafer": "2", "shot_x": 2, "shot_y": 3, "value": 96.0},
        ],
    })
    monkeypatch.setattr(yield_map, "shot_map_data", lambda *args, **kwargs: {
        "selected_item": "VTH", "items": ["VTH"], "steps": ["ET100"],
        "rows": [
            {"wafer": "1", "shot_x": 2.0, "shot_y": 3.0, "value": 0.21, "split": "SPLIT_A", "sample_count": 4},
            {"wafer": "1", "shot_x": 9.0, "shot_y": 9.0, "value": 0.99, "split": "SPLIT_B", "sample_count": 1},
        ],
    })

    result = yield_map.compare_shot_metrics(
        "YIELD_PROD", "ET_PROD", "VEH", "R1", "yield", "VTH", split_source="ET_TABLE_PROD",
    )

    assert result["point_count"] == 1
    assert result["points"][0] == {
        "wafer": "1", "shot_x": 2.0, "shot_y": 3.0,
        "yield_value": 97.5, "shot_yield": 97.5,
        "selected_bin_ratio": 2.5,
        "et_value": 0.21, "split": "SPLIT_A", "et_sample_count": 4,
    }


def test_compare_shot_sources_uses_yield_overlap_and_pairwise_similarity(monkeypatch):
    monkeypatch.setattr(yield_map, "map_data", lambda *args, **kwargs: {
        "shot_metrics": [
            {"wafer": "1", "shot_x": 0, "shot_y": 0, "value": 90.0},
            {"wafer": "1", "shot_x": 1, "shot_y": 0, "value": 95.0},
            {"wafer": "1", "shot_x": 2, "shot_y": 0, "value": 100.0},
        ],
    })

    def fake_shot_map(kind, *args, **kwargs):
        if kind == "et":
            return {
                "selected_item": "ET_A", "items": ["ET_A"], "steps": ["S1"],
                "step_seqs": ["10"], "selected_step": "S1", "selected_step_seq": "10",
                "rows": [
                    {"wafer": "1", "shot_x": 0, "shot_y": 0, "value": 1.0, "sample_count": 2},
                    {"wafer": "1", "shot_x": 1, "shot_y": 0, "value": 2.0, "sample_count": 2},
                    {"wafer": "1", "shot_x": 2, "shot_y": 0, "value": 3.0, "sample_count": 2},
                    {"wafer": "1", "shot_x": 9, "shot_y": 9, "value": 99.0, "sample_count": 1},
                ],
            }
        return {
            "selected_item": "IN_A", "items": ["IN_A"], "inline_table": "MAP_A",
            "rows": [
                {"wafer": "1", "shot_x": 0, "shot_y": 0, "value": 30.0, "sample_count": 4},
                {"wafer": "1", "shot_x": 1, "shot_y": 0, "value": 20.0, "sample_count": 4},
                {"wafer": "1", "shot_x": 2, "shot_y": 0, "value": 10.0, "sample_count": 4},
            ],
        }

    monkeypatch.setattr(yield_map, "shot_map_data", fake_shot_map)
    result = yield_map.compare_shot_sources(
        "YIELD_PROD", "ET_PROD", "INLINE_PROD", "VEH", "R1", "yield", "ET_A", "IN_A",
    )

    assert result["point_count"] == 3
    assert result["triple_count"] == 3
    assert result["points"][0]["root_lot_id"] == "R1"
    assert result["points"][0]["wafer_id"] == "1"
    assert result["points"][1]["inline_value"] == 20.0
    assert result["similarity"]["yield_et"]["pearson_r"] == 1.0
    assert result["similarity"]["yield_inline"]["pearson_r"] == -1.0
    assert result["similarity"]["et_inline"]["pearson_r"] == -1.0


def test_compare_metric_relations_supports_two_or_more_mixed_metrics(monkeypatch):
    monkeypatch.setattr(yield_map, "map_data", lambda *args, **kwargs: {
        "shot_metrics": [
            {"wafer": "1", "shot_x": 0, "shot_y": 0, "value": 90.0},
            {"wafer": "1", "shot_x": 1, "shot_y": 0, "value": 95.0},
            {"wafer": "1", "shot_x": 2, "shot_y": 0, "value": 100.0},
        ],
    })

    def fake_shot_map(kind, *args, **kwargs):
        item = kwargs.get("item_id")
        values = [1.0, 2.0, 3.0] if item == "ET_A" else [3.0, 2.0, 1.0]
        return {"selected_item": item, "rows": [
            {"wafer": "1", "shot_x": index, "shot_y": 0, "value": value}
            for index, value in enumerate(values)
        ]}

    monkeypatch.setattr(yield_map, "shot_map_data", fake_shot_map)
    monkeypatch.setattr(yield_map, "load_relationships", lambda: [])
    result = yield_map.compare_metric_relations("PROD", "VEH", "R1", [
        {"id": "y", "kind": "yield", "bin_name": "yield"},
        {"id": "a", "kind": "et", "item_id": "ET_A"},
        {"id": "b", "kind": "et", "item_id": "ET_B"},
    ])

    assert len(result["pairs"]) == 3
    assert result["pairs"][0]["sample_count"] == 3
    assert abs(result["pairs"][0]["pearson_r"]) == 1.0
    assert result["points"][0]["values"] == {"y": 90.0, "a": 1.0, "b": 3.0}

    targeted = yield_map.compare_metric_relations("PROD", "VEH", "R1", [
        {"id": "y", "kind": "yield", "bin_name": "yield"},
        {"id": "a", "kind": "et", "item_id": "ET_A"},
        {"id": "b", "kind": "et", "item_id": "ET_B"},
    ], target_metric_id="y")
    assert len(targeted["pairs"]) == 2
    assert all("y" in {pair["left_id"], pair["right_id"]} for pair in targeted["pairs"])


def test_threshold_fit_detects_level_change():
    rows = [
        {"x": float(index), "y": float(index) if index < 6 else float(index + 30)}
        for index in range(12)
    ]

    result = yield_map._threshold_fit(rows, "x", "y")

    assert result is not None
    assert result["is_candidate"] is True
    assert 5.0 < result["threshold"] < 6.0
    assert result["improvement"] > 0.9


def test_saved_relationship_classification_is_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(yield_map, "RELATIONSHIPS_PATH", tmp_path / "relationships.json")

    saved = yield_map.save_relationship({
        "product": "PROD", "left_metric": "ET · A", "right_metric": "INLINE · B",
        "status": "significant", "pearson_r": 0.72,
    }, "tester")

    assert saved["status"] == "significant"
    assert yield_map.load_relationships()[0]["updated_by"] == "tester"


def test_tkout_filter_uses_inclusive_date_range():
    source = pl.DataFrame({
        "tkout_time": ["2026-08-22T23:59:59", "2026-08-23T10:00:00", "2026-08-24T00:00:00"],
        "value": [1, 2, 3],
    }).lazy()

    result = yield_map._tkout_filter(source, "tkout_time", "2026-08-23", "2026-08-23").collect()

    assert result["value"].to_list() == [2]
