from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core import root_profile  # noqa: E402


def _sandbox_roots(monkeypatch, tmp_path: Path, platform: str = "linux"):
    project = tmp_path / "project"
    shared = tmp_path / "sharedworkspace"
    (project / "data" / "flow-data").mkdir(parents=True)
    (project / "data" / "Fab").mkdir(parents=True)
    shared.mkdir()

    monkeypatch.setattr(root_profile, "PROJECT_ROOT", project)
    monkeypatch.setattr(root_profile, "PROFILE_FILE", project / "data" / "runtime_roots.json")
    monkeypatch.setattr(root_profile, "PROD_SHARED", shared)
    monkeypatch.setattr(root_profile.sys, "platform", platform)
    monkeypatch.delenv("FLOW_PROD", raising=False)
    return project, shared


def test_auto_mode_uses_existing_linux_shared_roots(monkeypatch, tmp_path):
    _, shared = _sandbox_roots(monkeypatch, tmp_path)
    (shared / "DB").mkdir()
    (shared / "flow-data").mkdir()

    assert root_profile.default_db_root({"mode": "auto"}) == shared / "DB"
    assert root_profile.default_data_root({"mode": "auto"}) == shared / "flow-data"
    assert root_profile.use_shared_defaults({"mode": "auto"}) is True


def test_auto_mode_keeps_local_flow_data_when_shared_flow_data_missing(monkeypatch, tmp_path):
    project, shared = _sandbox_roots(monkeypatch, tmp_path)
    (shared / "DB").mkdir()

    assert root_profile.default_db_root({"mode": "auto"}) == shared / "DB"
    assert root_profile.default_data_root({"mode": "auto"}) == project / "data" / "flow-data"


def test_auto_mode_uses_linux_shared_db_root_even_when_db_dir_missing(monkeypatch, tmp_path):
    project, shared = _sandbox_roots(monkeypatch, tmp_path)

    assert root_profile.default_db_root({"mode": "auto"}) == shared / "DB"
    assert root_profile.default_data_root({"mode": "auto"}) == project / "data" / "flow-data"


def test_local_mode_ignores_linux_shared_roots(monkeypatch, tmp_path):
    project, shared = _sandbox_roots(monkeypatch, tmp_path)
    (shared / "DB").mkdir()
    (shared / "flow-data").mkdir()

    assert root_profile.default_db_root({"mode": "local"}) == project / "data" / "Fab"
    assert root_profile.default_data_root({"mode": "local"}) == project / "data" / "flow-data"
    assert root_profile.use_shared_defaults({"mode": "local"}) is False
