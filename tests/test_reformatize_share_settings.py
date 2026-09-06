import pytest
from fastapi import HTTPException

from backend.routers import reformatize


def test_share_url_default_roundtrip_and_legacy_save(monkeypatch, tmp_path):
    monkeypatch.setattr(reformatize, "SETTINGS_FILE", tmp_path / "settings.json")
    assert reformatize._settings()["share_base_url"] == ""
    req = reformatize.SettingsReq(share_base_url=" https://flow.example.com:8443/flow/ ")
    result = reformatize.settings_save(req, user={"username": "tester"})
    assert result["share_base_url"] == "https://flow.example.com:8443/flow"
    result = reformatize.settings_save(reformatize.SettingsReq(page_rows=100), user={})
    assert result["share_base_url"] == "https://flow.example.com:8443/flow"
    result = reformatize.settings_save(reformatize.SettingsReq(share_base_url=""), user={})
    assert result["share_base_url"] == ""


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///tmp", "http://", "https://user:pass@host", "http://host:bad", "http://host/?a=b", "http://host/#hash", "http://bad host", "http://host\\path"])
def test_invalid_share_url_does_not_write(monkeypatch, tmp_path, url):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(reformatize, "SETTINGS_FILE", path)
    with pytest.raises(HTTPException) as exc:
        reformatize.settings_save(reformatize.SettingsReq(share_base_url=url), user={})
    assert exc.value.status_code == 400
    assert not path.exists()


def test_max_download_mb_settings_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(reformatize, "SETTINGS_FILE", tmp_path / "settings.json")
    defaults = reformatize._settings()
    assert defaults["max_download_mb"] == 500

    saved = reformatize.settings_save(reformatize.SettingsReq(max_download_mb=1200), user={"username": "tester"})
    assert saved["max_download_mb"] == 1200
    assert reformatize._settings()["max_download_mb"] == 1200


def test_download_request_allowed_without_filters(monkeypatch):
    monkeypatch.setattr(reformatize, "_find_csv", lambda prod: None)
    monkeypatch.setattr(reformatize, "_hidden_for", lambda prod, is_admin: set())
    # Should not raise HTTPException 400 even with completely empty filters
    wanted = reformatize._check_download_request("TEST_PROD", reformatize.Filters(), ["COL_A", "COL_B"], is_admin=False)
    assert wanted == ["COL_A", "COL_B"]


def test_ensure_size_within_limit_raises_capacity_exceeded():
    import polars as pl
    df = pl.DataFrame({"a": [1] * 1000})
    # Setting limit to 0 disables check
    reformatize._ensure_size_within_limit(df, max_mb=0)

    # When limit is exceeded (e.g. 0.000001 MB)
    with pytest.raises(HTTPException) as exc:
        # Pass a mock df or set max_mb lower than df estimated size
        est_mb = reformatize._df_est_bytes(df) / (1024 * 1024)
        # Even tiny limit
        reformatize._ensure_size_within_limit(df, max_mb=est_mb / 2, context="조회")
    assert exc.value.status_code == 400
    assert "용량초과" in exc.value.detail


def test_share_base_url_synchronization_across_modules(monkeypatch, tmp_path):
    import json
    from core.paths import PATHS
    from core.mail import get_share_base_url, set_share_base_url
    from backend.routers import admin, informs
    from backend.core import et_tracker

    monkeypatch.setattr(PATHS, "data_root", tmp_path)
    monkeypatch.setattr(reformatize, "SETTINGS_FILE", tmp_path / "reformatize_settings.json")
    monkeypatch.setattr(admin, "ADMIN_SETTINGS_FILE", tmp_path / "admin_settings.json")

    # 1. Saving via reformatize settings
    req = reformatize.SettingsReq(share_base_url="http://flow.corp.net:8080")
    res = reformatize.settings_save(req, user={"username": "admin"})
    assert res["share_base_url"] == "http://flow.corp.net:8080"
    assert get_share_base_url() == "http://flow.corp.net:8080"

    # admin_settings.json verified
    admin_cfg = json.loads((tmp_path / "admin_settings.json").read_text("utf-8"))
    assert admin_cfg["share_base_url"] == "http://flow.corp.net:8080"
    assert admin_cfg["mail"]["app_base_url"] == "http://flow.corp.net:8080"

    # 2. Inform email body verified
    mail_html = informs._build_html_body(
        root={"id": "INF-2026-001", "product": "PROD_X", "lot_id": "LOT123", "author": "tester", "created_at": "2026-09-06T12:00:00"},
        thread_html="",
        extra_prose="공정 변경 안내",
    )
    assert "http://flow.corp.net:8080/informs?id=INF-2026-001" in mail_html
    assert "Flow 인폼 열기 ↗" in mail_html
    assert "Flow 인폼 바로가기 ↗" in mail_html

    # 3. ET Tracker link verified
    issue_link = et_tracker._issue_link({}, "ISSUE-99")
    assert issue_link == "http://flow.corp.net:8080/tracker?issue_id=ISSUE-99"

    # 4. Admin endpoints verified
    admin_res = admin.admin_get_share_base_url()
    assert admin_res["share_base_url"] == "http://flow.corp.net:8080"

    admin_save_res = admin.admin_set_share_base_url(admin.ShareBaseUrlReq(share_base_url="https://newflow.example.com"))
    assert admin_save_res["share_base_url"] == "https://newflow.example.com"
    assert get_share_base_url() == "https://newflow.example.com"
    assert reformatize._settings()["share_base_url"] == "https://newflow.example.com"


