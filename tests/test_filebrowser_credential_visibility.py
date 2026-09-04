from pathlib import Path

import pytest
from fastapi import HTTPException

from routers import filebrowser


def _fixture_roots(tmp_path: Path):
    root = tmp_path / "Fab"
    credential = root / "credential"
    credential.mkdir(parents=True)
    (credential / "PRODA_sop.csv").write_text("step_id,ppid\nAA100,PP_STD\n", encoding="utf-8")
    return root


def test_credential_folder_is_hidden_from_users_and_global_admin(tmp_path, monkeypatch):
    root = _fixture_roots(tmp_path)
    monkeypatch.setattr(filebrowser, "_base_root", lambda: root)
    monkeypatch.setattr(filebrowser, "_db_root", lambda: root)
    monkeypatch.setattr(filebrowser, "_load_filebrowser_settings", lambda: {})
    filebrowser._LIST_CACHE.clear()

    monkeypatch.setattr(filebrowser, "_require_filebrowser_user", lambda request: {"username": "user", "role": "user"})
    user_payload = filebrowser.base_files(request=object(), fast=True)
    assert "credential" not in {item["name"].casefold() for item in user_payload["files"]}

    monkeypatch.setattr(filebrowser, "_require_filebrowser_user", lambda request: {"username": "admin", "role": "admin"})
    admin_payload = filebrowser.base_files(request=object(), fast=True)
    assert "credential" not in {item["name"].casefold() for item in admin_payload["files"]}

    db_payload = filebrowser.list_roots(request=object(), all=True, fast=True)
    assert "credential" not in {item["name"].casefold() for item in db_payload["roots"]}

    children = filebrowser.base_dir_children(path="credential", request=object())
    assert children["entries"] == []


def test_credential_direct_file_access_is_blocked_for_every_role(monkeypatch):
    monkeypatch.setattr(filebrowser, "_require_filebrowser_user", lambda request: {"username": "delegate", "role": "user"})
    with pytest.raises(HTTPException) as exc:
        filebrowser._require_base_file_access(object(), "credential/PRODA_sop.csv")
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as alias_exc:
        filebrowser._require_base_file_access(object(), "credential./PRODA_sop.csv")
    assert alias_exc.value.status_code == 403

    monkeypatch.setattr(filebrowser, "_require_filebrowser_user", lambda request: {"username": "admin", "role": "admin"})
    with pytest.raises(HTTPException) as admin_exc:
        filebrowser._require_base_file_access(object(), "credential/PRODA_sop.csv")
    assert admin_exc.value.status_code == 403
