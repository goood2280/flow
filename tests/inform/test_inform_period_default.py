from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from backend.routers import informs  # noqa: E402


def _request():
    return object()


def test_lot_matrix_defaults_to_all_history(monkeypatch):
    items = [
        {
            "id": "old_gate",
            "product": "PRODA",
            "root_lot_id": "R1999",
            "lot_id": "R1999",
            "module": "GATE",
            "reason": "PEMS",
            "author": "alice",
            "flow_status": "received",
            "created_at": "1999-01-10T09:00:00",
        },
    ]
    monkeypatch.setattr(informs, "_load_upgraded", lambda: items)
    monkeypatch.setattr(informs, "_load_config", lambda: {"modules": ["GATE"]})
    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "admin", "username": "tester"})

    default_out = informs.lot_matrix(_request(), product="PRODA", search="")
    zero_out = informs.lot_matrix(_request(), product="PRODA", days=0, search="")

    assert default_out["products"][0]["lots"][0]["root_lot_id"] == "R1999"
    assert zero_out["products"][0]["lots"][0]["root_lot_id"] == "R1999"


def test_audit_log_defaults_to_all_history(tmp_path, monkeypatch):
    monkeypatch.setattr(informs, "INFORMS_FILE", tmp_path / "informs.json")
    monkeypatch.setattr(informs, "INFORM_AUDIT_FILE", tmp_path / "audit_log.json")
    monkeypatch.setattr(informs, "_INFORMS_CACHE_SIG", None)
    monkeypatch.setattr(informs, "_INFORMS_CACHE_ITEMS", None)
    monkeypatch.setattr(informs, "_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(informs, "current_user", lambda _request: {"username": "tester", "role": "admin"})
    monkeypatch.setattr(informs, "_effective_modules", lambda _username, _role: {"__all__"})

    item = {
        "id": "old_inform",
        "product": "PRODA",
        "root_lot_id": "R1999",
        "lot_id": "R1999",
        "module": "GATE",
        "author": "alice",
        "created_at": "1999-01-10T09:00:00",
    }
    informs._save([item])
    informs._audit_record("alice", "create", item, {}, "created old", at="1999-01-10T10:00:00")

    default_out = informs.audit_log(
        _request(),
        products=[],
        products_bracket=[],
        modules=[],
        modules_bracket=[],
        lot_search="",
        types=[],
        types_bracket=[],
        start="",
        end="",
    )
    zero_out = informs.audit_log(
        _request(),
        products=[],
        products_bracket=[],
        modules=[],
        modules_bracket=[],
        lot_search="",
        days=0,
        types=[],
        types_bracket=[],
        start="",
        end="",
    )

    assert [row["inform_id"] for row in default_out["audit"]] == ["old_inform"]
    assert [row["inform_id"] for row in zero_out["audit"]] == ["old_inform"]
