"""reformatize 필터 — tkout_time 기간·lot/step/wafer/site_cnt 필터와 무필터 다운로드 차단."""
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

from routers.reformatize import (  # noqa: E402
    Filters, _apply_filters, _filter_desc, _has_filter,
)


def _wide() -> pl.DataFrame:
    return pl.DataFrame({
        "root_lot_id": ["V3000", "V3000", "V3001", "W4000"],
        "lot_id": ["V3000A.1", "V3000A.1", "V3001A.2", "W4000A.1"],
        "wafer_id": ["1", "2", "1", "25"],
        "step_id": ["EV100010", "EV100020", "EV100010", "XX900010"],
        "tkout_time": ["2026-07-10T09:00:00", "2026-07-12T09:00:00",
                       "2026-07-15T09:00:00", "2026-07-17T09:00:00"],
        "total_site_cnt": ["17", "17", "9", "17"],
        "VTH_IDX": [1.0, 2.0, 3.0, 4.0],
    })


def test_has_filter_and_desc():
    assert not _has_filter(Filters())
    assert _has_filter(Filters(days=3))
    assert _has_filter(Filters(date_from="2026-07-01"))
    assert _has_filter(Filters(step_filter="EV1"))
    desc = _filter_desc(Filters(days=2, lot_filter="V30", site_cnt_filter="17"))
    assert "days=2" in desc and "lot=V30" in desc and "site_cnt=17" in desc


def test_no_filter_returns_all():
    assert _apply_filters(_wide(), Filters()).height == 4


def test_days_filter_anchored_to_max_tkout():
    # 최신 tkout = 07-17 → 최근 3일 = 07-14 이후 (07-15, 07-17 두 행)
    out = _apply_filters(_wide(), Filters(days=3))
    assert sorted(out["root_lot_id"].to_list()) == ["V3001", "W4000"]


def test_date_range_inclusive():
    out = _apply_filters(_wide(), Filters(date_from="2026-07-12", date_to="2026-07-15"))
    assert out["tkout_time"].to_list() == ["2026-07-12T09:00:00", "2026-07-15T09:00:00"]
    # 종료일 하루만 지정해도 그 날 전체 포함
    out2 = _apply_filters(_wide(), Filters(date_to="2026-07-10"))
    assert out2.height == 1


def test_days_takes_precedence_over_range():
    out = _apply_filters(_wide(), Filters(days=1, date_from="2026-07-01", date_to="2026-07-10"))
    assert out["root_lot_id"].to_list() == ["W4000"]


def test_bad_date_raises_400():
    with pytest.raises(HTTPException) as e:
        _apply_filters(_wide(), Filters(date_from="07/01/2026"))
    assert e.value.status_code == 400


def test_text_filters_contains_and_comma_or():
    assert _apply_filters(_wide(), Filters(lot_filter="v300")).height == 3   # 대소문자 무시 포함
    assert _apply_filters(_wide(), Filters(step_filter="EV100010")).height == 2
    assert _apply_filters(_wide(), Filters(step_filter="EV100020,XX9")).height == 2  # 쉼표 = OR
    assert _apply_filters(_wide(), Filters(wafer_filter="2")).height == 2   # "2", "25" 포함 매칭


def test_site_cnt_exact_match():
    assert _apply_filters(_wide(), Filters(site_cnt_filter="17")).height == 3
    assert _apply_filters(_wide(), Filters(site_cnt_filter="9,17")).height == 4
    with pytest.raises(HTTPException):
        _apply_filters(_wide(), Filters(site_cnt_filter="abc"))


def test_missing_column_raises_400():
    df = _wide().drop("total_site_cnt")
    with pytest.raises(HTTPException) as e:
        _apply_filters(df, Filters(site_cnt_filter="17"))
    assert e.value.status_code == 400
    with pytest.raises(HTTPException):
        _apply_filters(_wide().drop("tkout_time"), Filters(days=1))


def test_combined_filters():
    out = _apply_filters(_wide(), Filters(lot_filter="V300", step_filter="EV100010",
                                          site_cnt_filter="17"))
    assert out.height == 1 and out["root_lot_id"][0] == "V3000"
