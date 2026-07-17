from __future__ import annotations

import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from routers import dashboard  # noqa: E402
from core import lot_progress_cache  # noqa: E402


def _setup_cache(monkeypatch, tmp_path, rows, step_matching=None):
    fp = tmp_path / "lot_wf_current.parquet"
    pl.DataFrame(rows).write_parquet(fp)
    by_product, by_step = step_matching or ({}, {})
    monkeypatch.setattr(lot_progress_cache, "cache_parquet_file", lambda: fp)
    monkeypatch.setattr(lot_progress_cache, "cache_status", lambda: {"generated_at": "2026-07-13T09:00:00"})
    monkeypatch.setattr(lot_progress_cache, "load_step_matching", lambda: (dict(by_product), dict(by_step)))
    monkeypatch.setattr(dashboard, "_require_dashboard_section", lambda request, section: {"username": "tester"})
    monkeypatch.setattr(dashboard, "_wip_split_ml_table_path", lambda product: None)


_ROWS = [
    {"product": "PRODA", "root_lot_id": "A1", "wafer_id": "1", "lot_id": "A1.1",
     "step_id": "AA101000", "function_step": "FAB_1.0 STI", "tkout_time": "", "update_time": ""},
    {"product": "PRODA", "root_lot_id": "A1", "wafer_id": "2", "lot_id": "A1.1",
     "step_id": "AA102500", "function_step": "1.0 CMP", "tkout_time": "", "update_time": ""},
    {"product": "PRODA", "root_lot_id": "A2", "wafer_id": "1", "lot_id": "A2.1",
     "step_id": "AA205000", "function_step": "2.5 GATE", "tkout_time": "", "update_time": ""},
    {"product": "PRODA", "root_lot_id": "A3", "wafer_id": "1", "lot_id": "A3.1",
     "step_id": "AA309999", "function_step": None, "tkout_time": "", "update_time": ""},
    {"product": "PRODB", "root_lot_id": "B1", "wafer_id": "1", "lot_id": "B1.1",
     "step_id": "BB100000", "function_step": "1.0 STI", "tkout_time": "", "update_time": ""},
]


def test_wip_split_products_come_from_latest_cache(monkeypatch, tmp_path):
    _setup_cache(monkeypatch, tmp_path, _ROWS)
    out = dashboard.wip_split_summary(None, product="", bin_size=1000, split_col="", axis="step_id")
    # product(=vehicle) 목록은 latest cache 의 product 컬럼에서 나온다.
    assert out["products"] == ["PRODA", "PRODB"]
    assert out["product"] == "PRODA"
    assert out["total_wafers"] == 4


def test_wip_split_step_id_bins_1000(monkeypatch, tmp_path):
    _setup_cache(monkeypatch, tmp_path, _ROWS)
    out = dashboard.wip_split_summary(None, product="PRODA", bin_size=1000, split_col="", axis="step_id")
    assert out["axis"] == "step_id"
    assert out["bin_size"] == 1000
    labels = [b["label"] for b in out["bins"]]
    assert labels == ["101000~101999", "102000~102999", "205000~205999", "309000~309999"]


def test_wip_split_axis_step_desc_maps_step_id_via_vehicle_matching(monkeypatch, tmp_path):
    # 캐시의 function_step 이 비거나 낡아도, 조회 시점의 Vehicle_matching.csv
    # (step_id → step_desc) 매핑에서 앞머리 숫자를 뽑아 그룹핑한다.
    rows = [dict(r, function_step=None) for r in _ROWS]
    by_product = {
        ("PRODA", "AA101000"): "FAB_1.0 STI",
        ("PRODA", "AA102500"): "1.0 CMP",
        ("PRODA", "AA205000"): "2.5 GATE",
        # AA309999 는 CSV 에 없음 → 같은 prefix(AA) 중 step 번호가 가장
        # 가까운 AA205000("2.5 GATE") 로 근사 매칭 (미해석 아님).
    }
    _setup_cache(monkeypatch, tmp_path, rows, step_matching=(by_product, {}))
    out = dashboard.wip_split_summary(None, product="PRODA", bin_size=1000, split_col="", axis="step_desc")
    assert out["axis"] == "step_desc"
    bins = {b["label"]: b["total"] for b in out["bins"]}
    # "FAB_1.0 STI" 와 "1.0 CMP" 는 같은 1.0 그룹으로 묶이고, 존재하는 숫자가 전부 나온다.
    assert bins == {"1.0": 2, "2.5": 2}
    # 앞머리 숫자순 정렬.
    assert [b["label"] for b in out["bins"]] == ["1.0", "2.5"]


_NEAR_MATCHING = {
    ("PRODA", "AA101000"): "FAB_1.0 STI",
    ("PRODA", "AA102500"): "1.0 CMP",
    ("PRODA", "AA205000"): "2.5 GATE",
}


def _near_rows(step_id, function_step=None):
    return [
        {"product": "PRODA", "root_lot_id": "A1", "wafer_id": "1", "lot_id": "A1.1",
         "step_id": "AA101000", "function_step": None, "tkout_time": "", "update_time": ""},
        {"product": "PRODA", "root_lot_id": "A9", "wafer_id": "1", "lot_id": "A9.1",
         "step_id": step_id, "function_step": function_step, "tkout_time": "", "update_time": ""},
    ]


def test_wip_split_step_desc_nearest_same_prefix(monkeypatch, tmp_path):
    # CSV 에 없는 step_id 는 같은 영문 prefix 중 step 번호가 가장 가까운 항목으로.
    # AA103000 → |103000-102500| < |103000-101000| → "1.0 CMP" 그룹.
    _setup_cache(monkeypatch, tmp_path, _near_rows("AA103000"), step_matching=(_NEAR_MATCHING, {}))
    out = dashboard.wip_split_summary(None, product="PRODA", bin_size=1000, split_col="", axis="step_desc")
    bins = {b["label"]: b["total"] for b in out["bins"]}
    assert bins == {"1.0": 2}


def test_wip_split_step_desc_nearest_requires_same_two_letter_prefix(monkeypatch, tmp_path):
    # prefix 가 두 글자면 같은 prefix 하고만 매칭 — ZZ 후보가 없으면 미해석 유지.
    _setup_cache(monkeypatch, tmp_path, _near_rows("ZZ103000"), step_matching=(_NEAR_MATCHING, {}))
    out = dashboard.wip_split_summary(None, product="PRODA", bin_size=1000, split_col="", axis="step_desc")
    bins = {b["label"]: b["total"] for b in out["bins"]}
    assert bins == {"1.0": 1, "미해석": 1}


def test_wip_split_step_desc_non_two_letter_prefix_goes_to_max_group(monkeypatch, tmp_path):
    # prefix 가 두 글자가 아니면(1글자/3글자 등) 앞머리 숫자가 가장 큰 그룹으로.
    _setup_cache(monkeypatch, tmp_path, _near_rows("X103000"), step_matching=(_NEAR_MATCHING, {}))
    out = dashboard.wip_split_summary(None, product="PRODA", bin_size=1000, split_col="", axis="step_desc")
    bins = {b["label"]: b["total"] for b in out["bins"]}
    assert bins == {"1.0": 1, "2.5": 1}


def test_wip_split_step_desc_function_step_beats_nearest(monkeypatch, tmp_path):
    # 캐시 function_step 이 살아 있으면 근사 매칭보다 우선한다.
    _setup_cache(monkeypatch, tmp_path, _near_rows("AA103000", function_step="7.7 METAL"),
                 step_matching=(_NEAR_MATCHING, {}))
    out = dashboard.wip_split_summary(None, product="PRODA", bin_size=1000, split_col="", axis="step_desc")
    bins = {b["label"]: b["total"] for b in out["bins"]}
    assert bins == {"1.0": 1, "7.7": 1}


def test_wip_split_axis_step_desc_falls_back_to_cached_function_step(monkeypatch, tmp_path):
    # Vehicle_matching 에 없는 step 은 캐시에 저장된 function_step 으로 폴백.
    _setup_cache(monkeypatch, tmp_path, _ROWS, step_matching=({}, {}))
    out = dashboard.wip_split_summary(None, product="PRODA", bin_size=1000, split_col="", axis="step_desc")
    bins = {b["label"]: b["total"] for b in out["bins"]}
    assert bins == {"1.0": 2, "2.5": 1, "미해석": 1}


def test_wip_split_axis_step_desc_numeric_sort(monkeypatch, tmp_path):
    rows = list(_ROWS) + [
        {"product": "PRODA", "root_lot_id": "A4", "wafer_id": "1", "lot_id": "A4.1",
         "step_id": "AA400000", "function_step": "FAB_10.0 MET", "tkout_time": "", "update_time": ""},
    ]
    _setup_cache(monkeypatch, tmp_path, rows)
    out = dashboard.wip_split_summary(None, product="PRODA", bin_size=1000, split_col="", axis="step_desc")
    # "10.0" 은 문자열이 아니라 숫자 기준으로 "2.5" 뒤에 온다.
    assert [b["label"] for b in out["bins"]] == ["1.0", "2.5", "10.0", "미해석"]


def test_wip_split_falls_back_to_filebrowser_parquet(monkeypatch, tmp_path):
    fb_fp = tmp_path / "lot_progress_latest_lot_by_root_wafer.parquet"
    pl.DataFrame(_ROWS).write_parquet(fb_fp)
    missing = tmp_path / "missing" / "lot_wf_current.parquet"
    monkeypatch.setattr(lot_progress_cache, "cache_parquet_file", lambda: missing)
    monkeypatch.setattr(lot_progress_cache, "filebrowser_cache_parquet_file", lambda: fb_fp)
    monkeypatch.setattr(lot_progress_cache, "load_lot_progress_cache", lambda *a, **k: {})
    monkeypatch.setattr(lot_progress_cache, "cache_status", lambda: {})
    monkeypatch.setattr(dashboard, "_require_dashboard_section", lambda request, section: {"username": "tester"})
    monkeypatch.setattr(dashboard, "_wip_split_ml_table_path", lambda product: None)
    out = dashboard.wip_split_summary(None, product="PRODB", bin_size=1000, split_col="", axis="step_id")
    assert out["ok"] is True
    assert out["total_wafers"] == 1
