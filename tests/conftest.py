"""Shared pytest isolation for operator-owned Flow state."""

import pytest


@pytest.fixture(autouse=True)
def isolate_llm_usage(monkeypatch, tmp_path):
    from core import llm_usage
    monkeypatch.setattr(llm_usage, "_path", lambda: tmp_path / "llm_usage.json")


@pytest.fixture(autouse=True)
def isolate_activity_log(monkeypatch, tmp_path):
    """Never let route-level audit calls from tests touch the live activity log.

    Several route tests call endpoint functions directly with lightweight request
    doubles. Before this fixture those calls were written to flow-data as
    ``anonymous`` and later appeared in the admin activity dashboard.
    """
    from core import audit
    from core.paths import PATHS
    from routers import admin

    test_log = tmp_path / "activity.jsonl"
    monkeypatch.setattr(PATHS, "activity_log", test_log)
    monkeypatch.setattr(audit, "ACTIVITY_LOG", test_log)
    monkeypatch.setattr(admin, "ACTIVITY_LOG", test_log)


@pytest.fixture(autouse=True)
def isolate_download_log(monkeypatch, tmp_path):
    """Export tests must never append to operator-owned download history."""
    import sys
    from core.paths import PATHS

    test_log = tmp_path / "downloads.jsonl"
    monkeypatch.setattr(PATHS, "download_log", test_log)
    for name in ("routers.admin", "routers.filebrowser", "routers.reformatize"):
        module = sys.modules.get(name)
        if module is not None and hasattr(module, "DL_LOG"):
            monkeypatch.setattr(module, "DL_LOG", test_log)
