import polars as pl

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
