from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from backend.routers import informs  # noqa: E402


def test_module_summary_shape_counts_and_days_filter(monkeypatch):
    items = [
        {
            "id": "recent_received",
            "module": "GATE",
            "flow_status": "received",
            "created_at": "2099-01-10T09:00:00",
        },
        {
            "id": "recent_progress",
            "module": "GATE",
            "flow_status": "in_progress",
            "created_at": "2099-01-10T10:00:00",
        },
        {
            "id": "recent_completed",
            "module": "PC",
            "flow_status": "completed",
            "created_at": "2099-01-10T11:00:00",
        },
        {
            "id": "old_completed",
            "module": "GATE",
            "flow_status": "completed",
            "created_at": "1999-01-10T11:00:00",
        },
        {
            "id": "child_ignored",
            "parent_id": "recent_received",
            "module": "GATE",
            "flow_status": "",
            "created_at": "2099-01-10T12:00:00",
        },
    ]
    monkeypatch.setattr(informs, "_load_upgraded", lambda: items)
    monkeypatch.setattr(informs, "_load_config", lambda: {"modules": ["GATE", "PC", "ET"]})
    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "admin", "username": "tester"})

    rows = informs.module_summary(object(), days=3650)

    assert rows == [
        {"module": "GATE", "registered": 1, "mail_completed": 1, "apply_confirmed": 0, "pending": 2, "received": 1, "in_progress": 1, "completed": 0},
        {"module": "PC", "registered": 0, "mail_completed": 0, "apply_confirmed": 1, "pending": 0, "received": 0, "in_progress": 0, "completed": 1},
        {"module": "ET", "registered": 0, "mail_completed": 0, "apply_confirmed": 0, "pending": 0, "received": 0, "in_progress": 0, "completed": 0},
    ]
