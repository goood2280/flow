import math

import polars as pl
import pytest

from backend.routers import reformatize


def _frame(values=(1.0, 2.0, 3.0, 4.0, 5.0)):
    n = len(values)
    return pl.DataFrame({
        "root_lot_id": ["LOT1"] * n,
        "wafer_id": ["1"] * n,
        "step_id": ["STEP"] * n,
        "pgm": ["PGM_A"] * n,
        "X": list(values),
    })


def test_percentile_param_matches_linear_interpolation():
    out = reformatize._aggregate(_frame(), ["X"], "pct:20")

    assert out.height == 1
    assert out["X"].to_list() == [pytest.approx(1.8)]


def test_legacy_p90_p10_still_work():
    assert reformatize._aggregate(_frame(), ["X"], "p90")["X"].to_list() == [pytest.approx(4.6)]
    assert reformatize._aggregate(_frame(), ["X"], "p10")["X"].to_list() == [pytest.approx(1.4)]


def test_below_above_spec_pct_are_inclusive():
    below = reformatize._aggregate(_frame(), ["X"], "below:3")["X"].to_list()
    above = reformatize._aggregate(_frame(), ["X"], "above:3")["X"].to_list()

    assert below == [pytest.approx(60.0)]
    assert above == [pytest.approx(60.0)]


def test_cp_cpk_pp_ppk_formulas():
    sd = math.sqrt(2.5)  # sample std of [1..5]
    expected_cp = 6.0 / (6.0 * sd)
    expected_cpk = 3.0 / (3.0 * sd)

    assert reformatize._aggregate(_frame(), ["X"], "cp:0,6")["X"].to_list() == [pytest.approx(expected_cp)]
    assert reformatize._aggregate(_frame(), ["X"], "cpk:0,6")["X"].to_list() == [pytest.approx(expected_cpk)]
    # Pp/Ppk share the package-grain std with Cp/Cpk.
    assert reformatize._aggregate(_frame(), ["X"], "pp:0,6")["X"].to_list() == [pytest.approx(expected_cp)]
    assert reformatize._aggregate(_frame(), ["X"], "ppk:0,6")["X"].to_list() == [pytest.approx(expected_cpk)]


def test_agg_param_validation_errors():
    for bad in ("pct:120", "pct:abc", "below:", "above:xyz",
                "cp:0.8,0.2", "cp:0.5", "cpk:1,1", "ppk:a,b", "bogus"):
        with pytest.raises(Exception) as exc_info:
            reformatize._aggregate(_frame(), ["X"], bad)
        assert getattr(exc_info.value, "status_code", None) == 400
