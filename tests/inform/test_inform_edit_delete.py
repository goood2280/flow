from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from backend.routers import informs  # noqa: E402


def _request():
    return object()


def _install_files(tmp_path, monkeypatch, current):
    monkeypatch.setattr(informs, "INFORMS_FILE", tmp_path / "informs.json")
    monkeypatch.setattr(informs, "INFORM_AUDIT_FILE", tmp_path / "audit_log.json")
    monkeypatch.setattr(informs, "_INFORMS_CACHE_SIG", None)
    monkeypatch.setattr(informs, "_INFORMS_CACHE_ITEMS", None)
    monkeypatch.setattr(informs, "_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(informs, "current_user", lambda _request: dict(current))
    monkeypatch.setattr(informs, "_effective_modules", lambda _username, _role: {"__all__"})


def test_edit_permission_patch_and_audit(tmp_path, monkeypatch):
    current = {"username": "alice", "role": "user"}
    _install_files(tmp_path, monkeypatch, current)
    informs._save([{
        "id": "inf_a",
        "product": "PRODA",
        "root_lot_id": "R1000",
        "lot_id": "R1000",
        "module": "GATE",
        "reason": "PEMS",
        "text": "before",
        "author": "alice",
        "created_at": "2099-01-10T09:00:00",
    }])

    current.update(username="bob", role="user")
    with pytest.raises(HTTPException) as denied:
        informs.edit_inform(informs.InformEditReq(text="bad"), _request(), id="inf_a")
    assert denied.value.status_code == 403

    current.update(username="admin", role="admin")
    res = informs.edit_inform(informs.InformEditReq(text="after", module="STI"), _request(), id="inf_a")

    assert res["changed"] == ["text", "module"]
    saved = informs._load_upgraded()[0]
    assert saved["text"] == "after"
    assert saved["module"] == "STI"
    logs = informs._load_inform_audit()
    assert logs[-1]["type"] == "edit"
    assert logs[-1]["payload"]["fields"] == ["text", "module"]


def test_delete_soft_delete_permission_default_exclusion_and_audit(tmp_path, monkeypatch):
    current = {"username": "module_owner", "role": "user"}
    _install_files(tmp_path, monkeypatch, current)
    monkeypatch.setattr(informs, "user_modules", lambda _username, _role: {"GATE"})
    informs._save([{
        "id": "inf_a",
        "product": "PRODA",
        "root_lot_id": "R1000",
        "lot_id": "R1000",
        "wafer_id": "R1000",
        "module": "GATE",
        "reason": "PEMS",
        "text": "before",
        "author": "alice",
        "created_at": "2099-01-10T09:00:00",
    }])

    with pytest.raises(HTTPException) as denied:
        informs.delete_inform(_request(), id="inf_a")
    assert denied.value.status_code == 403

    current.update(username="alice", role="user")
    res = informs.delete_inform(_request(), id="inf_a")

    assert res["ok"] is True
    saved = informs._load_upgraded()[0]
    assert saved["deleted"] is True
    assert saved["deleted_by"] == "alice"
    assert saved["deleted_at"]
    assert informs.recent_roots(_request(), limit=50, include_deleted=False)["informs"] == []
    assert informs.by_lot(_request(), lot_id="R1000", include_deleted=False)["informs"] == []
    assert informs.by_lot(_request(), lot_id="R1000", include_deleted=True)["informs"][0]["id"] == "inf_a"
    assert informs._load_inform_audit()[-1]["type"] == "delete"
