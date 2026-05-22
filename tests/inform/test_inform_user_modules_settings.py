from __future__ import annotations

import logging
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
from routers import auth as auth_router  # noqa: E402


def _request():
    return object()


def test_user_modules_list_reports_broken_admin_settings(tmp_path, monkeypatch, caplog):
    admin_settings = tmp_path / "admin_settings.json"
    admin_settings.write_text("{bad json", encoding="utf-8")
    monkeypatch.setattr(informs, "ADMIN_SETTINGS_FILE", admin_settings)
    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "admin", "username": "admin"})

    with caplog.at_level(logging.WARNING, logger=informs.logger.name):
        with pytest.raises(HTTPException) as excinfo:
            informs.list_user_modules(_request())

    assert excinfo.value.status_code == 500
    assert "admin_settings.json 읽기 실패" in excinfo.value.detail
    assert "inform admin settings read failed" in caplog.text


def test_user_modules_save_does_not_overwrite_broken_admin_settings(tmp_path, monkeypatch):
    admin_settings = tmp_path / "admin_settings.json"
    original = "{bad json"
    admin_settings.write_text(original, encoding="utf-8")
    monkeypatch.setattr(informs, "ADMIN_SETTINGS_FILE", admin_settings)
    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "admin", "username": "admin"})

    with pytest.raises(HTTPException) as excinfo:
        informs.save_user_modules(
            informs.UserModulesSaveReq(username="alice", modules=["GATE"]),
            _request(),
        )

    assert excinfo.value.status_code == 500
    assert admin_settings.read_text(encoding="utf-8") == original


def test_inform_page_access_sees_all_modules_even_with_legacy_module_map(monkeypatch):
    monkeypatch.setattr(auth_router, "read_users", lambda: [
        {"username": "viewer", "role": "user", "status": "approved", "tabs": "inform"},
        {"username": "other", "role": "user", "status": "approved", "tabs": "dashboard"},
    ])
    monkeypatch.setattr(informs, "_get_inform_user_mods", lambda *_, **__: {"viewer": []})

    assert informs._effective_modules("viewer", "user") == {"__all__"}
    assert informs._effective_modules("other", "user") == set()


def test_audit_record_logs_best_effort_write_failure(monkeypatch, caplog):
    monkeypatch.setattr(informs, "_load_inform_audit", lambda: [])

    def fail_save(_rows):
        raise OSError("disk full")

    monkeypatch.setattr(informs, "_save_inform_audit", fail_save)
    monkeypatch.setattr(informs, "_audit", lambda *_args, **_kwargs: None)

    with caplog.at_level(logging.WARNING, logger=informs.logger.name):
        row = informs._audit_record("alice", "create", {"id": "inf_a"}, {}, "created")

    assert row["inform_id"] == "inf_a"
    assert "inform audit log write failed" in caplog.text


def test_audit_record_logs_global_mirror_failure(monkeypatch, caplog):
    monkeypatch.setattr(informs, "_load_inform_audit", lambda: [])
    monkeypatch.setattr(informs, "_save_inform_audit", lambda _rows: None)

    def fail_global_audit(*_args, **_kwargs):
        raise OSError("audit unavailable")

    monkeypatch.setattr(informs, "_audit", fail_global_audit)

    with caplog.at_level(logging.WARNING, logger=informs.logger.name):
        row = informs._audit_record("alice", "create", {"id": "inf_a"}, {}, "created")

    assert row["inform_id"] == "inf_a"
    assert "inform global audit mirror failed" in caplog.text
