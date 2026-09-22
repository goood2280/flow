import datetime as dt
import json
from types import SimpleNamespace

import polars as pl

from core import lot_tracker
from routers import lot_tracker as lot_tracker_router


def test_lot_tracker_exact_lot_history_dpml_and_reference_eta(monkeypatch):
    rows = [
        {"lot_id": "A.1", "step_id": "S0", "tkout_time": dt.datetime(2026, 9, 1, 0)},
        {"lot_id": "A.1", "step_id": "S0", "tkout_time": dt.datetime(2026, 9, 1, 12)},
        {"lot_id": "A.1", "step_id": "S1", "tkout_time": dt.datetime(2026, 9, 3, 12)},
        {"lot_id": "A.10", "step_id": "S2", "tkout_time": dt.datetime(2026, 9, 10)},
        {"lot_id": "R.1", "step_id": "S0", "tkout_time": dt.datetime(2026, 8, 1)},
        {"lot_id": "R.1", "step_id": "S1", "tkout_time": dt.datetime(2026, 8, 3)},
        {"lot_id": "R.1", "step_id": "S2", "tkout_time": dt.datetime(2026, 8, 6)},
    ]
    desc = {"S0": "00.0 PHOTO START", "S1": "01.0 LITHO MASK", "S2": "02.0 ET"}
    monkeypatch.setattr(lot_tracker, "_product_candidates", lambda lot_id, product: ["P"])
    monkeypatch.setattr(lot_tracker, "scan_long_fab", lambda *args: pl.DataFrame(rows).lazy())
    monkeypatch.setattr(lot_tracker, "lookup_lot_progress", lambda **kw: [])
    monkeypatch.setattr(lot_tracker, "describe_step", lambda sid, product: {"step_desc": desc[sid]})

    result = lot_tracker.track_lot("a.1", "r.1", "s2")
    assert result["ok"] is True
    assert [p["step_id"] for p in result["lot"]["points"]] == ["S0", "S1"]
    assert result["lot"]["points"][0]["tkout_time"] == "2026-09-01T12:00:00"
    assert result["lot"]["points"][0]["step_label"] == "00.0"
    assert result["lot"]["dpml"] == 1.0
    assert result["lot"]["mask_layer_count"] == 2
    assert result["lot"]["mask_basis"] == "litho_photo"
    assert result["forecast"]["eta"] == "2026-09-06T12:00:00"
    assert result["forecast"]["remaining_days"] == 3.0


def test_lot_tracker_unmatched_reference_step_has_no_eta(monkeypatch):
    current = [{"step_id": "S1", "tkout_time": "2026-09-01T00:00:00", "elapsed_days": 0}]
    reference = [{"step_id": "S0", "step_desc": "00.0", "tkout_time": "2026-08-01T00:00:00"},
                 {"step_id": "S2", "step_desc": "02.0", "tkout_time": "2026-08-04T00:00:00"}]
    forecast = lot_tracker.predict(current, reference, "S2")
    assert forecast["eta"] is None
    assert forecast["basis"]


def test_history_retains_real_tkin_for_arrival(monkeypatch):
    rows = [{"lot_id": "AZCVC.1", "step_id": "CS100000", "tkout_time": dt.datetime(2026, 9, 13), "tkin_time": dt.datetime(2026, 9, 12)}]
    monkeypatch.setattr(lot_tracker, "scan_long_fab", lambda *a: pl.DataFrame(rows).lazy())
    monkeypatch.setattr(lot_tracker, "describe_step", lambda *a: {})
    product, history = lot_tracker._history("AZCVC.1", ["PRODC1"])
    assert lot_tracker.build_timeline(history, product)[0]["tkin_time"] == "2026-09-12T00:00:00"


def test_fab_source_prefers_filebrowser_display_name(tmp_path, monkeypatch):
    db_root = tmp_path / "db"
    data_root = tmp_path / "data"
    (db_root / "mounted_fab_history").mkdir(parents=True)
    (db_root / "1.RAWDATA_DB").mkdir()
    data_root.mkdir()
    (data_root / "filebrowser_settings.json").write_text(json.dumps({
        "db_name_aliases": {
            "mounted_fab_history": "FAB",
            "1.RAWDATA_DB": "Legacy FAB",
        }
    }), encoding="utf-8")
    monkeypatch.setattr(lot_tracker, "PATHS", SimpleNamespace(db_root=db_root, data_root=data_root))

    assert lot_tracker._fab_source_roots() == ["mounted_fab_history"]


def test_fab_source_falls_back_to_rawdata_db(tmp_path, monkeypatch):
    db_root = tmp_path / "db"
    data_root = tmp_path / "data"
    (db_root / "1.RAWDATA_DB").mkdir(parents=True)
    data_root.mkdir()
    monkeypatch.setattr(lot_tracker, "PATHS", SimpleNamespace(db_root=db_root, data_root=data_root))

    assert lot_tracker._fab_source_roots() == ["1.RAWDATA_DB"]


def test_demo_named_lot_is_searched_in_fab_instead_of_generated(monkeypatch):
    monkeypatch.setattr(lot_tracker, "_product_candidates", lambda lot_id, product: ["P"])
    monkeypatch.setattr(lot_tracker, "_history", lambda lot_id, candidates: ("", []))

    result = lot_tracker.track_lot("DEMO-LOT-01", product="PRODA")

    assert result["ok"] is False
    assert result["lot"] is None


def test_dpml_counts_distinct_photo_layers_not_numeric_gap():
    points = [
        {"step_desc": "02.0 PHOTO", "mask_layer": 2, "elapsed_days": 0},
        {"step_desc": "02.5 PHOTO REWORK", "mask_layer": 2, "elapsed_days": 1},
        {"step_desc": "09.0 LITHO", "mask_layer": 9, "elapsed_days": 6},
        {"step_desc": "10.0 ET", "mask_layer": 10, "elapsed_days": 8},
    ]
    summary = lot_tracker.mask_layer_summary(points)
    assert summary["mask_layer_count"] == 2
    assert summary["mask_layers"] == [2, 9]
    assert summary["dpml"] == 4.0


def test_dpml_counts_unnumbered_photo_step_by_step_id():
    points = [
        {"step_id": "P1", "step_desc": "PHOTO COAT", "mask_layer": None, "elapsed_days": 0},
        {"step_id": "P2", "step_desc": "LITHO EXPOSURE", "mask_layer": None, "elapsed_days": 5},
    ]
    summary = lot_tracker.mask_layer_summary(points)
    assert summary["mask_layers"] == ["P1", "P2"]
    assert summary["dpml"] == 2.5


def test_lot_tracker_endpoint_enforces_tab_access(monkeypatch):
    monkeypatch.setattr(lot_tracker_router, "current_user", lambda request: {"username": "u", "role": "user", "tabs": "dashboard"})
    monkeypatch.setattr(lot_tracker_router, "is_page_manager", lambda user, page: False)
    try:
        lot_tracker_router.get_lot_tracker(object(), "A.1", "", "", "")
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 403
    else:
        raise AssertionError("missing LOT Tracker permission must be rejected")
