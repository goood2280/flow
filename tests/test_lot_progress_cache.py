from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core import lot_progress_cache as cache  # noqa: E402


def test_tracker_lot_status_cache_keeps_requested_fields(monkeypatch, tmp_path):
    fp = tmp_path / "lot_status_cache.json"
    monkeypatch.setattr(cache, "lot_status_cache_file", lambda: fp)

    state = cache.upsert_tracker_lot_status_rows([
        {
            "product": "PRODA",
            "root_lot_id": "A1000",
            "lot_id": "A1000A.1",
            "wafer_id": "W01",
            "step_id": "STEP_010",
            "func_step": "PC_LITHO",
            "tkout_time": "2026-05-08T10:00:00",
            "eqp_id": "EQP_SHOULD_NOT_PERSIST",
        }
    ])

    assert state["items"] == [{
        "root_lot_id": "A1000",
        "wafer_id": "1",
        "lot_id": "A1000A.1",
        "step_id": "STEP_010",
        "func_step": "PC_LITHO",
        "update_time": "2026-05-08T10:00:00",
    }]


def test_lot_progress_summary_returns_wafers_and_steps(monkeypatch):
    monkeypatch.setattr(cache, "load_lot_progress_cache", lambda max_age_seconds=None: {
        "items": [
            {"product": "PRODA", "root_lot_id": "A1000", "lot_id": "A1000A.1", "wafer_id": "2", "step_id": "STEP_020", "func_step": "GATE", "time": "2026-05-08T11:00:00"},
            {"product": "PRODA", "root_lot_id": "A1000", "lot_id": "A1000A.1", "wafer_id": "1", "step_id": "STEP_010", "func_step": "STI", "time": "2026-05-08T10:00:00"},
        ]
    })

    summary = cache.lot_progress_summary(lot_id="A1000A.1")

    assert summary["wafer_count"] == 2
    assert summary["wafer_ids"] == ["1", "2"]
    assert summary["step_id"] == "STEP_020"
    assert summary["func_step"] == "GATE"
    assert [(r["wafer_id"], r["step_id"], r["func_step"]) for r in summary["rows"]] == [
        ("1", "STEP_010", "STI"),
        ("2", "STEP_020", "GATE"),
    ]
