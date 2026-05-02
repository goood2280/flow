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


def _install_files(tmp_path, monkeypatch, username="alice", role="admin"):
    monkeypatch.setattr(informs, "INFORMS_FILE", tmp_path / "informs.json")
    monkeypatch.setattr(informs, "INFORM_AUDIT_FILE", tmp_path / "audit_log.json")
    monkeypatch.setattr(informs, "_INFORMS_CACHE_SIG", None)
    monkeypatch.setattr(informs, "_INFORMS_CACHE_ITEMS", None)
    monkeypatch.setattr(informs, "_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(informs, "current_user", lambda _request: {"username": username, "role": role})
    monkeypatch.setattr(informs, "_effective_modules", lambda _username, _role: {"__all__"})


def test_audit_log_filters_and_desc_order(tmp_path, monkeypatch):
    _install_files(tmp_path, monkeypatch)
    items = [
        {
            "id": "inf_a",
            "product": "ML_TABLE_PRODA",
            "root_lot_id": "R1000",
            "lot_id": "R1000",
            "module": "GATE",
            "author": "alice",
            "created_at": "2099-01-10T09:00:00",
        },
        {
            "id": "inf_b",
            "product": "PRODB",
            "root_lot_id": "R2000",
            "lot_id": "R2000",
            "module": "STI",
            "author": "bob",
            "created_at": "2099-01-10T09:00:00",
        },
    ]
    informs._save(items)
    informs._audit_record("alice", "create", items[0], {"field": "x"}, "created a", at="2099-01-10T10:00:00")
    informs._audit_record("alice", "edit", items[0], {"field": "text"}, "edited a", at="2099-01-10T12:00:00")
    informs._audit_record("bob", "mail", items[1], {"subject": "hello"}, "mailed b", at="2099-01-10T11:00:00")

    out = informs.audit_log(
        _request(),
        products=["PRODA"],
        products_bracket=[],
        modules=["GATE"],
        modules_bracket=[],
        lot_search="100",
        days=3650,
        types=["edit", "create"],
        types_bracket=[],
        start="",
        end="",
    )

    assert [row["type"] for row in out["audit"]] == ["edit", "create"]
    assert [row["summary"] for row in out["audit"]] == ["edited a", "created a"]
    assert all(row["inform_id"] == "inf_a" for row in out["audit"])


def test_mutations_append_audit_rows(tmp_path, monkeypatch):
    _install_files(tmp_path, monkeypatch, username="alice", role="admin")

    created = informs.create_inform(
        informs.InformCreate(lot_id="R1000", product="PRODA", module="GATE", reason="PEMS", text="root"),
        _request(),
    )["inform"]
    informs.edit_inform(informs.InformEditReq(text="updated"), _request(), id=created["id"])
    informs.set_status(informs.StatusReq(status="completed", note="done"), _request(), id=created["id"])
    informs.create_inform(
        informs.InformCreate(wafer_id=created["wafer_id"], parent_id=created["id"], text="reply"),
        _request(),
    )
    informs.delete_inform(_request(), id=created["id"])

    out = informs.audit_log(
        _request(),
        products=[],
        products_bracket=[],
        modules=[],
        modules_bracket=[],
        lot_search="",
        days=3650,
        types=[],
        types_bracket=[],
        start="",
        end="",
    )

    types = [row["type"] for row in out["audit"]]
    assert "create" in types
    assert "edit" in types
    assert "status_change" in types
    assert "comment" in types
    assert "delete" in types
    assert out["audit"][0]["type"] == "delete"
