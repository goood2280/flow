from __future__ import annotations

import sys
from pathlib import Path

import polars as pl
import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core import utils  # noqa: E402
from routers import filebrowser  # noqa: E402


def test_sql_view_scans_all_partitions_and_filters_before_projection(monkeypatch):
    calls = []

    def fake_lazy_read_source(**kwargs):
        calls.append(kwargs)
        return pl.DataFrame({
            "value": ["hit", "miss"],
            "shown": [1, 2],
        }).lazy()

    monkeypatch.setattr(utils, "lazy_read_source", fake_lazy_read_source)

    result = filebrowser.view_product(
        root="ROOT",
        product="PROD",
        sql="value == 'hit'",
        rows=20,
        select_cols="shown",
        meta_only=False,
        all_partitions=False,
        page=0,
        page_size=20,
    )

    assert calls[-1]["recent_days"] is None
    assert calls[-1]["max_files"] is None
    assert result["columns"] == ["shown"]
    assert result["data"] == [{"shown": 1}]


def test_lazy_view_limits_default_wide_preview_but_filters_full_schema():
    data = {f"c{i:02d}": [i, i + 100] for i in range(30)}
    data["hidden_filter"] = ["hit", "miss"]
    lf = pl.DataFrame(data).lazy()

    result = filebrowser._run_view_lazy(
        lf,
        sql="hidden_filter == 'hit'",
        select_cols="",
        rows=20,
        page=0,
        page_size=20,
        preview_cols=5,
    )

    assert result["total_cols"] == 31
    assert result["preview_cols"] == 5
    assert result["truncated_cols"] is True
    assert result["columns"] == [f"c{i:02d}" for i in range(5)]
    assert result["data"] == [{"c00": 0, "c01": 1, "c02": 2, "c03": 3, "c04": 4}]


def test_download_lazy_csv_requires_selected_columns_for_wide_sources():
    data = {f"c{i:03d}": [i] for i in range(filebrowser.MAX_CSV_DOWNLOAD_AUTO_COLUMNS + 1)}

    with pytest.raises(HTTPException) as exc:
        filebrowser._download_lazy_csv(pl.DataFrame(data).lazy(), "", "", 10)

    assert exc.value.status_code == 400
    assert "컬럼" in str(exc.value.detail)


def test_download_lazy_csv_applies_sql_projection_and_row_cap():
    lf = pl.DataFrame({
        "flag": ["hit", "hit", "miss"],
        "shown": [1, 2, 3],
        "hidden": ["a", "b", "c"],
    }).lazy()

    df, csv_bytes = filebrowser._download_lazy_csv(lf, "flag == 'hit'", "shown", 10)

    assert df.to_dicts() == [{"shown": 1}, {"shown": 2}]
    assert b"shown" in csv_bytes
    assert b"hidden" not in csv_bytes

    with pytest.raises(HTTPException) as exc:
        filebrowser._download_lazy_csv(lf, "flag == 'hit'", "shown", 1)

    assert exc.value.status_code == 400
    assert "1" in str(exc.value.detail)
