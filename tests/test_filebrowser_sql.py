from __future__ import annotations

import sys
from pathlib import Path

import polars as pl

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
