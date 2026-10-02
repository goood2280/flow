"""Windows 서버 기본 저장소(D:\\DB, D:\\flow-data)와 Linux /config 경로 차단."""
import sys
from pathlib import Path

import pytest

from core import root_profile


@pytest.fixture
def win_host(monkeypatch, tmp_path):
    """Windows 호스트 + 임시 '드라이브' + 프로필 없음."""
    drive = tmp_path / "drive"
    drive.mkdir()
    project = tmp_path / "Desktop" / "flow"
    project.mkdir(parents=True)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(root_profile, "PROJECT_ROOT", project)
    monkeypatch.setattr(root_profile, "PROFILE_FILE", project / "data" / "runtime_roots.json")
    # /config/... 가 있어도(이 개발 PC 처럼 D:\config\work\sharedworkspace) Windows 는 쓰지 않는다.
    shared = tmp_path / "config" / "work" / "sharedworkspace"
    (shared / "DB").mkdir(parents=True)
    (shared / "flow-data").mkdir(parents=True)
    monkeypatch.setattr(root_profile, "PROD_SHARED", shared)
    monkeypatch.setenv("FLOW_STORAGE_ROOT", str(drive))
    for name in ("FLOW_PROD", "FLOW_STORAGE_DEFAULT"):
        monkeypatch.delenv(name, raising=False)
    return drive, project, shared


def test_installed_copy_defaults_to_storage_drive_even_before_folders_exist(win_host):
    drive, _project, _shared = win_host
    profile = {"mode": "auto"}
    assert root_profile.default_db_root(profile) == drive / "DB"
    assert root_profile.default_data_root(profile) == drive / "flow-data"
    assert root_profile.use_shared_defaults(profile) is True


def test_git_checkout_defaults_to_storage_even_before_folders_exist(win_host, monkeypatch):
    drive, project, _shared = win_host
    (project / ".git").mkdir()
    profile = {"mode": "auto"}
    assert root_profile.default_data_root(profile) == drive / "flow-data"
    assert root_profile.default_db_root(profile) == drive / "DB"
    # 한쪽만 있어도 둘 다 D: 로 — 저장소가 드라이브와 프로젝트로 갈라지지 않는다.
    (drive / "DB").mkdir()
    assert root_profile.default_db_root(profile) == drive / "DB"
    assert root_profile.default_data_root(profile) == drive / "flow-data"


def test_git_checkout_with_flow_prod_uses_storage_drive(win_host, monkeypatch):
    drive, project, _shared = win_host
    (project / ".git").mkdir()
    monkeypatch.setenv("FLOW_PROD", "1")
    assert root_profile.default_db_root({"mode": "auto"}) == drive / "DB"


def test_storage_default_can_be_turned_off(win_host, monkeypatch):
    _drive, project, _shared = win_host
    monkeypatch.setenv("FLOW_STORAGE_DEFAULT", "0")
    assert root_profile.default_data_root({"mode": "auto"}) == project / "data" / "flow-data"
    assert root_profile.default_db_root({"mode": "auto"}) == project / "data" / "Fab"


def test_explicit_local_profile_keeps_project_storage(win_host):
    _drive, project, _shared = win_host
    assert root_profile.default_db_root({"mode": "local"}) == project / "data" / "Fab"
    assert root_profile.default_data_root({"mode": "local"}) == project / "data" / "flow-data"


def test_windows_never_uses_linux_shared_paths(win_host, monkeypatch):
    drive, _project, _shared = win_host
    monkeypatch.setenv("FLOW_PROD", "1")
    assert root_profile.prod_shared_available() is False
    assert root_profile.prod_app_candidates({"mode": "auto"}) == []
    # shared 모드도 Windows 에서는 D: 저장소를 뜻한다.
    assert root_profile.default_db_root({"mode": "shared"}) == drive / "DB"


def test_custom_profile_path_still_wins(win_host):
    _drive, project, _shared = win_host
    custom = project / "custom-db"
    custom.mkdir()
    assert root_profile.default_db_root({"mode": "custom", "db_root": str(custom)}) == custom


def test_db_resolver_defaults_and_storage_switches_are_immediate(win_host, monkeypatch):
    from core import roots
    drive, project, _shared = win_host
    monkeypatch.delenv("FLOW_DB_ROOT", raising=False)
    monkeypatch.setattr(roots, "_PROFILE", {"mode": "auto"})
    monkeypatch.setattr(roots, "_DB_ROOT_CACHE", {})
    monkeypatch.setattr(roots, "_read_admin_setting", lambda _key: None)
    assert roots.get_db_root() == drive / "DB"
    other = drive / "another-drive"
    monkeypatch.setenv("FLOW_STORAGE_ROOT", str(other))
    assert roots.get_db_root() == other / "DB"
    monkeypatch.setenv("FLOW_STORAGE_DEFAULT", "0")
    assert roots.get_db_root() == project / "data" / "Fab"


def test_explicit_db_environment_wins_over_default(win_host, monkeypatch):
    from core import roots
    drive, _project, _shared = win_host
    explicit = drive / "custom-db"
    monkeypatch.setenv("FLOW_DB_ROOT", str(explicit))
    monkeypatch.setattr(roots, "_DB_ROOT_CACHE", {})
    assert roots.get_db_root() == explicit


def test_windows_backup_default_is_next_to_flow_data(monkeypatch, tmp_path):
    from core import backup
    data_root = tmp_path / "flow-data"
    monkeypatch.setattr(backup.os, "name", "nt")
    monkeypatch.setattr(backup.PATHS, "data_root", data_root)
    assert backup._default_backup_root() == tmp_path / "flow-backups"
    assert "config" not in Path(backup._default_backup_root()).parts
