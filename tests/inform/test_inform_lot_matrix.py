from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from backend.routers import informs  # noqa: E402


def _install_matrix_fixtures(monkeypatch):
    items = [
        {
            "id": "gate_completed",
            "product": "ML_TABLE_PRODA",
            "root_lot_id": "R1000",
            "lot_id": "R1000",
            "module": "GATE",
            "reason": "PEMS",
            "author": "alice",
            "flow_status": "completed",
            "created_at": "2099-01-10T09:00:00",
            "status_history": [{"status": "completed", "actor": "alice", "at": "2099-01-10T09:30:00"}],
        },
        {
            "id": "gate_received_newer",
            "product": "PRODA",
            "root_lot_id": "R1000",
            "lot_id": "R1000",
            "module": "GATE",
            "reason": "PEMS",
            "author": "bob",
            "flow_status": "received",
            "created_at": "2099-01-11T09:00:00",
        },
        {
            "id": "sti_received",
            "product": "PRODA",
            "root_lot_id": "R1000",
            "lot_id": "R1000",
            "module": "STI",
            "reason": "PEMS",
            "author": "carol",
            "flow_status": "received",
            "created_at": "2099-01-11T10:00:00",
        },
        {
            "id": "pc_progress",
            "product": "PRODA",
            "root_lot_id": "R1000",
            "lot_id": "R1000",
            "module": "PC",
            "reason": "PEMS",
            "author": "dave",
            "flow_status": "reviewing",
            "created_at": "2099-01-11T11:00:00",
        },
        {
            "id": "other_lot",
            "product": "PRODA",
            "root_lot_id": "R1010",
            "lot_id": "R1010",
            "module": "GATE",
            "reason": "PEMS",
            "author": "erin",
            "flow_status": "received",
            "created_at": "2099-01-12T09:00:00",
        },
        {
            "id": "old_excluded",
            "product": "PRODA",
            "root_lot_id": "R9999",
            "lot_id": "R9999",
            "module": "GATE",
            "reason": "PEMS",
            "author": "frank",
            "flow_status": "completed",
            "created_at": "1999-01-12T09:00:00",
        },
    ]
    monkeypatch.setattr(informs, "_load_upgraded", lambda: items)
    monkeypatch.setattr(informs, "_load_config", lambda: {"modules": []})
    monkeypatch.setattr(informs, "_get_inform_user_mods", lambda: {"viewer": ["GATE", "STI", "PC"]})
    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "admin", "username": "tester"})


def test_lot_matrix_shape_state_priority_and_progress(monkeypatch):
    _install_matrix_fixtures(monkeypatch)

    out = informs.lot_matrix(object(), product="", days=3650, search="")

    assert out["module_order"] == ["GATE", "STI", "PC"]
    assert out["products"][0]["product"] == "PRODA"
    lots = {row["root_lot_id"]: row for row in out["products"][0]["lots"]}
    assert set(lots) == {"R1000", "R1010"}

    r1000 = lots["R1000"]
    assert r1000["progress"] == {"done": 1, "total": 3}
    assert r1000["modules"]["GATE"]["state"] == "completed"
    assert r1000["modules"]["GATE"]["inform_id"] == "gate_completed"
    assert r1000["modules"]["STI"]["state"] == "received"
    assert r1000["modules"]["PC"]["state"] == "in_progress"
    assert r1000["last_update"] == "2099-01-11T11:00:00"


def test_lot_matrix_product_days_and_search_filters(monkeypatch):
    _install_matrix_fixtures(monkeypatch)

    searched = informs.lot_matrix(object(), product="PRODA", days=3650, search="101")
    assert [row["root_lot_id"] for row in searched["products"][0]["lots"]] == ["R1010"]

    missing_product = informs.lot_matrix(object(), product="PRODB", days=3650, search="")
    assert missing_product["products"] == []

    windowed = informs.lot_matrix(object(), product="", days=3650, search="999")
    assert windowed["products"] == []


def test_lot_matrix_is_readable_by_non_admin_user(monkeypatch):
    _install_matrix_fixtures(monkeypatch)
    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "user", "username": "viewer"})
    monkeypatch.setattr(informs, "_effective_modules", lambda _username, _role: {"__all__"})

    out = informs.lot_matrix(object(), product="PRODA", days=3650, search="100")

    assert out["products"][0]["lots"][0]["root_lot_id"] == "R1000"
