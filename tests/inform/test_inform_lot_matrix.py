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
            "lot_id": "R1000A.1",
            "fab_lot_id_at_save": "R1000A.1",
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
            "lot_id": "R1000A.1",
            "fab_lot_id_at_save": "R1000A.1",
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
            "lot_id": "R1000A.1",
            "fab_lot_id_at_save": "R1000A.1",
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
            "lot_id": "R1000A.1",
            "fab_lot_id_at_save": "R1000A.1",
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
            "lot_id": "R1010A.1",
            "fab_lot_id_at_save": "R1010A.1",
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
            "lot_id": "R9999A.1",
            "fab_lot_id_at_save": "R9999A.1",
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
    lots = {row["fab_lot_id"]: row for row in out["products"][0]["lots"]}
    assert set(lots) == {"R1000A.1", "R1010A.1"}

    r1000 = lots["R1000A.1"]
    assert r1000["progress"] == {"done": 1, "total": 3}
    assert r1000["modules"]["GATE"]["state"] == "apply_confirmed"
    assert r1000["modules"]["GATE"]["inform_id"] == "gate_completed"
    assert r1000["modules"]["STI"]["state"] == "registered"
    assert r1000["modules"]["PC"]["state"] == "mail_completed"
    assert r1000["last_update"] == "2099-01-11T11:00:00"


def test_lot_matrix_product_days_and_search_filters(monkeypatch):
    _install_matrix_fixtures(monkeypatch)

    searched = informs.lot_matrix(object(), product="PRODA", days=3650, search="101")
    assert [row["fab_lot_id"] for row in searched["products"][0]["lots"]] == ["R1010A.1"]

    missing_product = informs.lot_matrix(object(), product="PRODB", days=3650, search="")
    assert missing_product["products"] == []

    windowed = informs.lot_matrix(object(), product="", days=3650, search="999")
    assert windowed["products"] == []


def test_lot_matrix_is_readable_by_non_admin_user(monkeypatch):
    _install_matrix_fixtures(monkeypatch)
    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "user", "username": "viewer"})
    monkeypatch.setattr(informs, "_effective_modules", lambda _username, _role: {"__all__"})

    out = informs.lot_matrix(object(), product="PRODA", days=3650, search="100")

    assert out["products"][0]["lots"][0]["fab_lot_id"] == "R1000A.1"


def test_lot_matrix_expands_legacy_multi_fab_snapshot(monkeypatch):
    items = [
        {
            "id": "multi",
            "product": "PRODA",
            "root_lot_id": "R3000",
            "lot_id": "R3000",
            "fab_lot_id_at_save": "R3000A.1, R3000A.2",
            "module": "GATE",
            "reason": "PEMS",
            "author": "alice",
            "flow_status": "received",
            "created_at": "2099-01-12T09:00:00",
        },
    ]
    monkeypatch.setattr(informs, "_load_upgraded", lambda: items)
    monkeypatch.setattr(informs, "_load_config", lambda: {"modules": ["GATE"]})
    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "admin", "username": "tester"})

    out = informs.lot_matrix(object(), product="PRODA", days=3650, search="R3000A")

    lots = {row["fab_lot_id"]: row for row in out["products"][0]["lots"]}
    assert set(lots) == {"R3000A.1", "R3000A.2"}


def test_lot_matrix_prefers_saved_fab_lot_over_embed_lot_labels(monkeypatch):
    items = [
        {
            "id": "split_one",
            "product": "PRODA",
            "root_lot_id": "R4000",
            "lot_id": "R4000A.1",
            "fab_lot_id_at_save": "R4000A.1",
            "module": "GATE",
            "reason": "PEMS",
            "author": "alice",
            "flow_status": "received",
            "created_at": "2099-01-12T09:00:00",
            "embed_table": {
                "st_view": {
                    "header_groups": [
                        {"label": "R4000A.1", "span": 1},
                        {"label": "R4000A.2", "span": 1},
                    ],
                    "wafer_fab_list": ["R4000A.1", "R4000A.2"],
                },
            },
        },
    ]
    monkeypatch.setattr(informs, "_load_upgraded", lambda: items)
    monkeypatch.setattr(informs, "_load_config", lambda: {"modules": ["GATE"]})
    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "admin", "username": "tester"})

    out = informs.lot_matrix(object(), product="PRODA", days=3650, search="R4000")

    assert [row["fab_lot_id"] for row in out["products"][0]["lots"]] == ["R4000A.1"]


def test_lot_matrix_search_matches_root_when_fab_lot_label_differs(monkeypatch):
    items = [
        {
            "id": "root_search",
            "product": "PRODA",
            "root_lot_id": "ROOT777",
            "lot_id": "OPER_A.1",
            "fab_lot_id_at_save": "OPER_A.1",
            "module": "GATE",
            "reason": "PEMS",
            "author": "alice",
            "flow_status": "received",
            "created_at": "2099-01-12T09:00:00",
        },
    ]
    monkeypatch.setattr(informs, "_load_upgraded", lambda: items)
    monkeypatch.setattr(informs, "_load_config", lambda: {"modules": ["GATE"]})
    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "admin", "username": "tester"})

    out = informs.lot_matrix(object(), product="PRODA", days=3650, search="ROOT777")

    assert out["products"][0]["lots"][0]["fab_lot_id"] == "OPER_A.1"
