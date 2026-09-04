import pytest
from fastapi import HTTPException

from routers import filebrowser, s3_ingest


def test_named_rawdata_root_is_listed_before_data_files_arrive(tmp_path, monkeypatch):
    raw_root = tmp_path / "1.RAWDATA_DB_MSR"
    raw_root.mkdir()

    monkeypatch.setattr(filebrowser, "_db_root", lambda: tmp_path)
    monkeypatch.setattr(
        filebrowser,
        "_require_filebrowser_user",
        lambda request: {"username": "engineer"},
    )
    monkeypatch.setattr(
        filebrowser,
        "_load_filebrowser_settings",
        lambda: {"hidden_db_dirs": [], "db_name_aliases": {}},
    )
    filebrowser._LIST_CACHE.clear()

    payload = filebrowser.list_roots(request=object(), all=False, fast=False)

    match = next(root for root in payload["roots"] if root["name"] == raw_root.name)
    assert match["display_name"] == "MSR"
    assert match["parquet_count"] == 0


def test_teg_location_internal_folder_is_hidden_from_db_and_files(tmp_path, monkeypatch):
    internal = tmp_path / "teg_location"
    internal.mkdir()
    (internal / "teg_map.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(filebrowser, "_db_root", lambda: tmp_path)
    monkeypatch.setattr(filebrowser, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(
        filebrowser,
        "_require_filebrowser_user",
        lambda request: {"username": "engineer"},
    )
    monkeypatch.setattr(
        filebrowser,
        "_load_filebrowser_settings",
        lambda: {
            "hidden_db_dirs": ["teg_location"],
            "db_name_aliases": {},
            "versioned_single_file_dirs": [],
        },
    )
    filebrowser._LIST_CACHE.clear()

    roots = filebrowser.list_roots(request=object(), all=True, fast=True)

    assert filebrowser._is_filebrowser_hidden_dir_name("teg_location") is True
    assert "teg_location" not in {row["name"].casefold() for row in roots["roots"]}
    assert "teg_location" not in filebrowser._single_file_folder_names(
        {"hidden_db_dirs": ["teg_location"]}
    )


def test_s3_target_allows_folder_names_with_spaces():
    assert s3_ingest._validate_target("Auto report") is None
    assert s3_ingest._validate_target("Auto report/PRODUCT A") is None


def test_hidden_credential_folder_remains_available_to_s3_sync(tmp_path, monkeypatch):
    credential = tmp_path / "credential"
    credential.mkdir()
    monkeypatch.setattr(s3_ingest, "_db_root", lambda: tmp_path)

    available = s3_ingest.list_available(username="admin", _perm={"role": "admin"})

    assert {row["name"] for row in available["dbs"]} >= {"credential"}
    assert s3_ingest._validate_target("credential") is None


def test_s3_save_accepts_auto_report_folder(monkeypatch):
    saved = {}
    monkeypatch.setattr(s3_ingest, "_load_cfg", lambda: {"items": []})
    monkeypatch.setattr(s3_ingest, "_save_cfg", lambda cfg: saved.update(cfg))

    result = s3_ingest._save_item_checked(
        s3_ingest.SaveReq(
            username="admin",
            kind="db",
            target="Auto report",
            s3_url="s3://flow-reports/auto-report/",
            command="sync",
            direction="upload",
        )
    )

    assert result["ok"] is True
    assert saved["items"][0]["target"] == "Auto report"


@pytest.mark.parametrize(
    "target",
    ["", " Auto report", "Auto report ", "Auto report/../outside", "Auto\\report", "."],
)
def test_s3_target_with_spaces_still_rejects_unsafe_paths(target):
    with pytest.raises(HTTPException):
        s3_ingest._validate_target(target)
