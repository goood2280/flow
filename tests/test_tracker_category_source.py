from __future__ import annotations

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
