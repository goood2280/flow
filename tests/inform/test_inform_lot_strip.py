from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from backend.routers import informs  # noqa: E402


def test_recent_roots_include_root_lot_module_counts(monkeypatch):
    items = [
        {"id": "gate_1", "product": "PRODA", "root_lot_id": "R1000", "lot_id": "R1000", "module": "GATE", "created_at": "2099-01-10T09:00:00"},
        {"id": "gate_2", "product": "PRODA", "root_lot_id": "R1000", "lot_id": "R1000", "module": "GATE", "created_at": "2099-01-10T10:00:00"},
        {"id": "et_1", "product": "PRODA", "root_lot_id": "R1000", "lot_id": "R1000", "module": "ET", "created_at": "2099-01-10T11:00:00"},
        {"id": "child_ignored", "parent_id": "gate_1", "product": "PRODA", "root_lot_id": "R1000", "lot_id": "R1000", "module": "GATE", "created_at": "2099-01-10T12:00:00"},
        {"id": "other_lot", "product": "PRODA", "root_lot_id": "R2000", "lot_id": "R2000", "module": "PC", "created_at": "2099-01-10T13:00:00"},
    ]
    monkeypatch.setattr(informs, "_load_upgraded", lambda: items)
    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "admin", "username": "tester"})
    monkeypatch.setattr(informs, "_effective_modules", lambda _username, _role: {"__all__"})

    out = informs.recent_roots(object(), limit=10, include_deleted=False)
    rows = {row["id"]: row for row in out["informs"]}

    assert rows["gate_1"]["root_lot_module_counts"] == {"GATE": 2, "ET": 1}
    assert rows["gate_1"]["informed_modules"] == ["GATE", "ET"]
    assert rows["other_lot"]["root_lot_module_counts"] == {"PC": 1}
