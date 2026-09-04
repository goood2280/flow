import polars as pl

from backend.core import vehicle_reformatter
from backend.routers import reformatize


def test_step_seq_filter_is_a_comma_separated_contains_filter():
    frame = pl.DataFrame({
        "root_lot_id": ["LOT1", "LOT1", "LOT2"],
        "step_id": ["ETCH", "ETCH", "CLEAN"],
        "step_seq": ["AAH1", "BBH2", "CCH3"],
        "wafer_id": ["1", "2", "3"],
    })

    filtered = reformatize._apply_filters(
        frame,
        reformatize.Filters(step_seq_filter="ah1, CCH"),
    )

    assert filtered["step_seq"].to_list() == ["AAH1", "CCH3"]


def test_step_seq_filter_counts_as_a_download_filter_and_cache_key():
    request = reformatize.Filters(step_seq_filter=" seq-2 ")

    assert reformatize._has_filter(request)
    assert "step_seq=seq-2" in reformatize._filter_desc(request)
    assert "SEQ-2" in reformatize._filters_key(request)


def test_aggregate_keeps_pgm_groups_for_formula_test_values():
    frame = pl.DataFrame({
        "root_lot_id": ["LOT1", "LOT1"],
        "wafer_id": ["1", "1"],
        "step_id": ["STEP", "STEP"],
        "step_seq": ["SEQ", "SEQ"],
        "tkout_time": ["2026-08-11 10:00:00", "2026-08-11 10:00:00"],
        "shot_x": [0, 1],
        "shot_y": [0, 1],
        "TEST_INDEX": [2.0, 4.0],
    })

    aggregated = reformatize._aggregate(frame, ["TEST_INDEX"], "avg")

    assert aggregated.height == 1
    assert aggregated["pgm"].to_list() == ["SEQ(2pt)"]
    assert aggregated["shot_count"].to_list() == [2]
    assert aggregated["TEST_INDEX"].to_list() == [3.0]


def test_formula_test_applies_requested_aggregation_after_formula(monkeypatch):
    wide = pl.DataFrame({
        "root_lot_id": ["LOT1", "LOT1"],
        "wafer_id": ["1", "1"],
        "step_id": ["STEP", "STEP"],
        "step_seq": ["SEQ", "SEQ"],
        "tkout_time": ["2026-08-11 10:00:00", "2026-08-11 10:00:00"],
        "shot_x": [0, 1],
        "shot_y": [0, 1],
        "BASE": [2.0, 4.0],
    })

    monkeypatch.setattr(
        reformatize,
        "_compute",
        lambda *args, **kwargs: (wide, list(wide.columns), [], "vehicle.csv", [], 2, ""),
    )
    monkeypatch.setattr(
        reformatize,
        "apply_addp_rows",
        lambda frame, rows: (frame.with_columns((pl.col("BASE") * 2).alias("TEST_INDEX")), []),
    )

    out, aliases, errors, _, _ = reformatize._run_test(
        "PRODUCT",
        [reformatize.TestItem(alias="TEST_INDEX", addp_form="{BASE} * 2")],
        reformatize.Filters(),
        agg="avg",
    )

    assert aliases == ["TEST_INDEX"]
    assert errors == []
    assert out.height == 1
    assert out["BASE"].to_list() == [3.0]
    assert out["TEST_INDEX"].to_list() == [6.0]


def _real_rule(alias="TEST_INDEX", itemid="ITEM_A"):
    return {
        "category": "real", "alias": alias, "itemid": itemid,
        "scale": 1.0, "absolute": False, "report_order": 1,
        "addp_form": "",
    }


def test_reformatize_preserves_pgm_before_p10_aggregation():
    """Same wafer/step/coordinate in another PGM must not be averaged first."""
    frame = pl.DataFrame({
        "product": ["P"] * 4,
        "root_lot_id": ["LOT1"] * 4,
        "wafer_id": ["1"] * 4,
        "step_id": ["STEP"] * 4,
        "step_seq": ["SEQ"] * 4,
        "pgm": ["PGM_A", "PGM_A", "PGM_B", "PGM_B"],
        "tkout_time": ["2026-09-01 10:00:00"] * 2 + ["2026-09-01 11:00:00"] * 2,
        "chip_x_pos": [0, 1, 0, 1],
        "chip_y_pos": [0, 1, 0, 1],
        "item_id": ["ITEM_A"] * 4,
        "value": [1.0, 3.0, 100.0, 200.0],
    })

    wide, _, errors = vehicle_reformatter.reformatize(frame, [_real_rule()])
    aggregated = reformatize._aggregate(wide, ["TEST_INDEX"], "p10")

    assert errors == []
    assert wide.height == 4
    assert aggregated["pgm"].to_list() == ["PGM_A", "PGM_B"]
    assert aggregated["shot_count"].to_list() == [2, 2]
    assert aggregated["TEST_INDEX"].to_list() == [1.2, 110.0]


def test_reformatize_uses_tkout_as_package_key_when_pgm_is_missing():
    frame = pl.DataFrame({
        "root_lot_id": ["LOT1"] * 4,
        "wafer_id": ["1"] * 4,
        "step_id": ["STEP"] * 4,
        "step_seq": ["SEQ"] * 4,
        "tkout_time": ["2026-09-01 10:00:00"] * 2 + ["2026-09-01 11:00:00"] * 2,
        "chip_x_pos": [0, 1, 0, 1],
        "chip_y_pos": [0, 1, 0, 1],
        "item_id": ["ITEM_A"] * 4,
        "value": [1.0, 3.0, 100.0, 200.0],
    })

    wide, _, _ = vehicle_reformatter.reformatize(frame, [_real_rule()])
    aggregated = reformatize._aggregate(wide, ["TEST_INDEX"], "p10")

    assert wide.height == 4
    assert aggregated["pgm"].to_list() == ["SEQ(2pt)_1", "SEQ(2pt)_2"]
    assert aggregated["TEST_INDEX"].to_list() == [1.2, 110.0]


def test_p10_matches_spotfire_and_excel_inclusive_linear_percentile():
    frame = pl.DataFrame({
        "root_lot_id": ["LOT1"] * 4,
        "wafer_id": ["1"] * 4,
        "step_id": ["STEP"] * 4,
        "pgm": ["PGM_A"] * 4,
        "TEST_INDEX": [1.0, 2.0, 3.0, 4.0],
    })

    aggregated = reformatize._aggregate(frame, ["TEST_INDEX"], "p10")

    assert aggregated["TEST_INDEX"].to_list() == [1.3]


def test_pgm_point_count_filter_keeps_whole_matching_packages():
    frame = pl.DataFrame({
        "root_lot_id": ["LOT1"] * 5,
        "wafer_id": ["1"] * 5,
        "step_id": ["STEP"] * 5,
        "pgm": ["PGM_2PT"] * 2 + ["PGM_3PT"] * 3,
        "TEST_INDEX": [1.0, 3.0, 10.0, 20.0, 30.0],
    })

    filtered = reformatize._point_cnt_filter(frame, "2")
    aggregated = reformatize._aggregate(filtered, ["TEST_INDEX"], "p10")

    assert filtered.height == 2
    assert filtered["pgm"].unique().to_list() == ["PGM_2PT"]
    assert aggregated["shot_count"].to_list() == [2]
    assert aggregated["TEST_INDEX"].to_list() == [1.2]


def test_pgm_point_count_is_a_download_filter_and_job_field():
    request = reformatize.Filters(point_cnt_filter=" 25,49 ")

    assert reformatize._has_filter(request)
    assert "pgm_pt=25,49" in reformatize._filter_desc(request)
    assert reformatize._filters_dump(request)["point_cnt_filter"] == " 25,49 "
