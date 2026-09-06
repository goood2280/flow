import datetime as dt

import polars as pl

from core.utils import download_content_disposition, download_filename
from routers import reformatize, splittable


def test_common_download_filename_keeps_identity_and_sanitizes_context():
    name = download_filename(
        "FileBrowser",
        username="홍 길동/PI",
        unique_id="abc:123",
        context=["ET/PROD:A", "where root_lot_id = 'L1'"],
        extension="csv",
        now=dt.datetime(2026, 9, 6, 13, 14, 15),
    )

    assert name.startswith("FileBrowser_20260906-131415_abc-123_홍-길동-PI_")
    assert "ET-PROD-A" in name
    assert "where-root_lot_id-=-'L1'" in name
    assert name.endswith(".csv")
    assert len(name) <= 220
    header = download_content_disposition(name)
    assert "filename*=UTF-8''" in header
    assert "%ED%99%8D" in header


def test_et_download_filename_contains_filter_and_user():
    filters = reformatize.Filters(lot_filter="ROOT-01", step_filter="S100")

    name = reformatize._download_name(
        "VEH-A", filters, "engineer", unique_id="JOB-123", agg="median",
    )

    assert name.startswith("ET-download_")
    assert "_JOB-123_engineer_" in name
    assert "VEH-A" in name
    assert "lot=ROOT-01" in name
    assert "agg-median" in name


def test_splittable_filename_uses_lot_key_and_custom_context():
    frame = pl.DataFrame({"lot_id": ["LOT-77"], "wafer_id": [1]})

    name = splittable._split_download_name(
        product="VEH-B", root_lot_id="", lot_col="lot_id", df=frame,
        username="owner", prefix="KNOB,FAB", custom_name="DOE set",
        custom_cols="", extension="xlsx", display_mode="split_check",
    )

    assert name.startswith("SplitTable_")
    assert "_LOT-77_owner_" in name
    assert "VEH-B_CUSTOM-DOE-set_split_check" in name
    assert name.endswith(".xlsx")


def test_splittable_filename_names_selected_knob_and_fab_scopes():
    frame = pl.DataFrame({"root_lot_id": ["ROOT-9"], "wafer_id": [1]})

    name = splittable._split_download_name(
        product="VEH-C", root_lot_id="ROOT-9", lot_col="root_lot_id", df=frame,
        username="owner", prefix="KNOB,FAB", custom_name="", custom_cols="",
        extension="csv",
    )

    assert "_ROOT-9_owner_" in name
    assert "VEH-C_KNOB+FAB" in name


def test_splittable_filename_uses_stable_multi_lot_summary_without_root_filter():
    frame = pl.DataFrame({
        "lot_id": ["LOT-C", "LOT-A", "LOT-B", "LOT-A"],
        "wafer_id": [1, 2, 3, 4],
    })

    name = splittable._split_download_name(
        product="VEH-D", root_lot_id="", lot_col="lot_id", df=frame,
        username="owner", prefix="FAB", custom_name="", custom_cols="",
        extension="csv",
    )

    assert "_LOT-A+LOT-B+more_owner_" in name
