from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from routers import tracker  # noqa: E402


def test_category_source_uses_saved_category_mapping_case_insensitively(monkeypatch):
    monkeypatch.setattr(
        tracker,
        "_load_cats",
        lambda: [{"name": "Inline Analysis", "color": "#3b82f6", "source": "et"}],
    )

    assert tracker._category_source(" inline analysis ", "fab") == "et"


def test_empty_categories_file_falls_back_to_default_categories(monkeypatch):
    monkeypatch.setattr(tracker, "load_json", lambda *_args, **_kwargs: [])

    cats = tracker._load_cats()

    assert [c["name"] for c in cats[:2]] == ["Analysis", "Monitor"]


def test_monitor_lot_rows_expand_from_lot_progress_cache(monkeypatch):
    from core import lot_progress_cache

    monkeypatch.setattr(
        lot_progress_cache,
        "lot_progress_summary",
        lambda **_kwargs: {
            "rows": [
                {"product": "PRODA", "root_lot_id": "A1000", "lot_id": "A1000A.1", "wafer_id": "1", "step_id": "STEP_010", "func_step": "STI", "update_time": "2026-05-08T10:00:00"},
                {"product": "PRODA", "root_lot_id": "A1000", "lot_id": "A1000A.1", "wafer_id": "2", "step_id": "STEP_020", "func_step": "GATE", "update_time": "2026-05-08T11:00:00"},
            ]
        },
    )

    rows = tracker._expand_monitor_lot_rows_from_cache([{"lot_id": "A1000A.1"}], category="Monitor")

    assert [(r["wafer_id"], r["current_step"], r["func_step"]) for r in rows] == [
        ("1", "STEP_010", "STI"),
        ("2", "STEP_020", "GATE"),
    ]


def test_tracker_scheduler_fab_scan_prefers_lot_progress_cache(monkeypatch, tmp_path):
    from core import lot_progress_cache, lot_step, tracker_scheduler
    from core import paths as core_paths

    class DummyPaths:
        data_root = tmp_path

    tracker_dir = tmp_path / "tracker"
    tracker_dir.mkdir()
    issues_fp = tracker_dir / "issues.json"
    issues_fp.write_text(json.dumps([{
        "id": "ISS-1",
        "status": "open",
        "category": "Monitor",
        "product": "PRODA",
        "username": "owner",
        "lots": [{"root_lot_id": "A1000", "wafer_id": "1", "product": "PRODA"}],
    }]), encoding="utf-8")

    progress_calls = []
    fallback_calls = []
    monkeypatch.setattr(core_paths, "PATHS", DummyPaths())
    monkeypatch.setattr(lot_step, "expand_lot_row_for_wafer_selection", lambda lot, **_kwargs: [lot])
    monkeypatch.setattr(
        lot_progress_cache,
        "lot_progress_snapshot",
        lambda **kwargs: progress_calls.append(kwargs) or {
            "fab": {
                "product": "PRODA",
                "root_lot_id": "A1000",
                "wafer_id": "1",
                "lot_id": "A1000A.1",
                "step_id": "STEP_CACHE",
                "function_step": "CACHE_FUNC",
                "time": "2026-05-10T02:00:00",
            },
            "et": [],
            "cache": {"hit": True},
        },
    )
    monkeypatch.setattr(lot_step, "lot_step_snapshot", lambda **kwargs: fallback_calls.append(kwargs) or {})

    out = tracker_scheduler._scan_once()
    saved = json.loads(issues_fp.read_text("utf-8"))
    lot = saved[0]["lots"][0]

    assert out["ok"] is True
    assert progress_calls and progress_calls[0]["root_lot_id"] == "A1000"
    assert fallback_calls == []
    assert lot["current_step"] == "STEP_CACHE"
    assert lot["current_function_step"] == "CACHE_FUNC"
