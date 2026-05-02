from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from backend.routers import informs  # noqa: E402


def _install_directness_fixtures(monkeypatch):
    items = []
    for i in range(7):
        items.append({
            "id": f"gate_{i}",
            "product": "PRODA",
            "root_lot_id": "R1000",
            "lot_id": "R1000",
            "module": "GATE",
            "reason": f"R{i}",
            "text": f"body {i}",
            "author": f"user{i}",
            "flow_status": "completed" if i == 0 else "received",
            "created_at": f"2099-01-10T0{i}:00:00",
            "status_history": [{"status": "completed", "actor": "user0", "at": "2099-01-10T00:30:00"}] if i == 0 else [],
        })
    items.extend([
        {
            "id": "sti_0",
            "product": "PRODA",
            "root_lot_id": "R1000",
            "lot_id": "R1000",
            "module": "STI",
            "reason": "S",
            "text": "sti",
            "author": "sti",
            "flow_status": "received",
            "created_at": "2099-01-10T08:00:00",
        },
        {
            "id": "pc_hidden",
            "product": "PRODA",
            "root_lot_id": "R1000",
            "lot_id": "R1000",
            "module": "PC",
            "reason": "P",
            "text": "pc",
            "author": "pc",
            "flow_status": "received",
            "created_at": "2099-01-10T09:00:00",
        },
        {
            "id": "other_lot",
            "product": "PRODA",
            "root_lot_id": "R2000",
            "lot_id": "R2000",
            "module": "GATE",
            "reason": "O",
            "text": "other",
            "author": "other",
            "flow_status": "received",
            "created_at": "2099-01-10T10:00:00",
        },
    ])
    monkeypatch.setattr(informs, "_load_upgraded", lambda: items)
    monkeypatch.setattr(informs, "_load_config", lambda: {"modules": ["GATE", "STI", "PC"]})
    monkeypatch.setattr(informs, "_get_inform_user_mods", lambda: {"viewer": ["GATE", "STI"]})
    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "user", "username": "viewer"})
    monkeypatch.setattr(informs, "_effective_modules", lambda _username, _role: {"GATE", "STI"})


def test_lot_matrix_cell_count_recent_totals_and_visibility(monkeypatch):
    _install_directness_fixtures(monkeypatch)

    out = informs.lot_matrix(object(), product="PRODA", days=3650, search="100")

    product = out["products"][0]
    lot = product["lots"][0]
    gate = lot["modules"]["GATE"]
    assert gate["inform_count"] == 7
    assert [row["inform_id"] for row in gate["recent"]] == ["gate_6", "gate_5", "gate_4", "gate_3", "gate_2"]
    assert len(gate["recent"]) == 5
    assert product["module_totals"]["GATE"] == 7
    assert product["module_totals"]["STI"] == 1
    assert "PC" not in lot["modules"]
    assert gate["recent"][0]["body_preview"] == "body 6"


def test_by_lot_module_counts_and_informed_modules_frequency(monkeypatch):
    _install_directness_fixtures(monkeypatch)

    out = informs.by_lot(object(), lot_id="R1000", include_deleted=False)

    assert out["module_counts"] == {"GATE": 7, "STI": 1}
    assert out["informed_modules"] == ["GATE", "STI"]
    assert out["available_modules"][:2] == ["GATE", "STI"]
    assert "PC" not in out["module_counts"]
