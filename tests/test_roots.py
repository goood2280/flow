from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core import root_profile, roots  # noqa: E402


def _sandbox_roots(monkeypatch, tmp_path: Path) -> Path:
    project = tmp_path / "flow"
    (project / "data" / "flow-data").mkdir(parents=True)
    (project / "data" / "Fab").mkdir(parents=True)

    monkeypatch.setattr(root_profile, "PROJECT_ROOT", project)
    monkeypatch.setattr(root_profile, "PROFILE_FILE", project / "data" / "runtime_roots.json")
    monkeypatch.setattr(root_profile, "PROD_SHARED", tmp_path / "sharedworkspace")
    monkeypatch.setattr(root_profile.sys, "platform", "win32")
    monkeypatch.setattr(roots, "_PROJECT_ROOT", project)
    monkeypatch.setattr(roots, "_PROFILE", {"mode": "local"})
    monkeypatch.delenv("FLOW_DATA_ROOT", raising=False)
    monkeypatch.delenv("FLOW_DB_ROOT", raising=False)
    monkeypatch.delenv("FLOW_PROD", raising=False)
    monkeypatch.chdir(tmp_path)
    return project


def _write_admin_db(project: Path, value: str) -> None:
    fp = project / "data" / "flow-data" / "admin_settings.json"
    fp.write_text(json.dumps({"data_roots": {"db": value}}), encoding="utf-8")


def test_admin_db_root_relative_path_is_project_relative(monkeypatch, tmp_path):
    project = _sandbox_roots(monkeypatch, tmp_path)
    _write_admin_db(project, "data/Fab")

    assert roots.get_db_root() == project / "data" / "Fab"


def test_env_db_root_relative_path_is_project_relative(monkeypatch, tmp_path):
    project = _sandbox_roots(monkeypatch, tmp_path)
    monkeypatch.setenv("FLOW_DB_ROOT", "data/Fab")

    assert roots.get_db_root() == project / "data" / "Fab"


def test_admin_db_root_stale_checkout_path_uses_current_project(monkeypatch, tmp_path, caplog):
    project = _sandbox_roots(monkeypatch, tmp_path)
    stale = tmp_path / "old" / "flow" / "data" / "Fab"
    _write_admin_db(project, str(stale))

    caplog.set_level(logging.WARNING, logger="flow.roots")

    assert roots.get_db_root() == project / "data" / "Fab"
    assert "admin_settings data_roots.db ignored" not in caplog.text
