from routers import filebrowser


def test_file_display_name_is_saved_and_listed_without_changing_source_path(tmp_path, monkeypatch):
    source = tmp_path / "measurements.csv"
    source.write_text("lot,value\nL1,3\n", encoding="utf-8")
    settings_path = tmp_path / "filebrowser_settings.json"
    monkeypatch.setattr(filebrowser, "_filebrowser_settings_path", lambda: settings_path)
    monkeypatch.setattr(filebrowser, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(filebrowser, "_db_root", lambda: tmp_path)
    monkeypatch.setattr(filebrowser, "_require_filebrowser_user", lambda request: {"role": "user"})
    filebrowser._LIST_CACHE.clear()

    initial = filebrowser.base_files(request=object(), fast=True)
    assert next(item for item in initial["files"] if item["name"] == source.name)["display_name"] == source.name

    filebrowser._save_filebrowser_settings({"file_name_aliases": {source.name: "측정 파일"}})
    updated = filebrowser.base_files(request=object(), fast=True)
    item = next(item for item in updated["files"] if item["name"] == source.name)
    assert item["display_name"] == "측정 파일"
    assert item["path"] == source.name
    assert source.is_file()
    assert filebrowser._load_filebrowser_settings()["file_name_aliases"] == {source.name: "측정 파일"}


def test_written_file_description_is_flagged_for_hover_tooltip(tmp_path, monkeypatch):
    described = tmp_path / "measurements.csv"
    described.write_text("lot,value\nL1,3\n", encoding="utf-8")
    plain = tmp_path / "other.csv"
    plain.write_text("lot,value\nL1,3\n", encoding="utf-8")
    settings_path = tmp_path / "filebrowser_settings.json"
    monkeypatch.setattr(filebrowser, "_filebrowser_settings_path", lambda: settings_path)
    monkeypatch.setattr(filebrowser, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(filebrowser, "_db_root", lambda: tmp_path)
    monkeypatch.setattr(filebrowser, "_require_filebrowser_user", lambda request: {"role": "user"})
    filebrowser._LIST_CACHE.clear()

    filebrowser._save_filebrowser_settings({"file_descriptions": {described.name: "lot별 측정값 원본"}})
    files = {item["name"]: item for item in filebrowser.base_files(request=object(), fast=True)["files"]}
    assert files[described.name]["description"] == "lot별 측정값 원본"
    assert files[described.name]["description_custom"] is True
    # 설명을 적지 않은 파일은 기본 설명만 있고 툴팁 대상이 아니다.
    assert files[plain.name]["description_custom"] is False


def test_nested_file_display_name_uses_relative_path(tmp_path, monkeypatch):
    folder = tmp_path / "reports"
    folder.mkdir()
    (folder / "summary.csv").write_text("value\n1\n", encoding="utf-8")
    monkeypatch.setattr(filebrowser, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(filebrowser, "_db_root", lambda: tmp_path)
    monkeypatch.setattr(filebrowser, "_require_filebrowser_user", lambda request: {"role": "user"})
    monkeypatch.setattr(filebrowser, "_load_filebrowser_settings", lambda: {
        "hidden_db_dirs": ["reports"],
        "file_name_aliases": {"reports/summary.csv": "요약 보고서"},
    })

    item = filebrowser.base_dir_children(path="reports", request=object())["entries"][0]
    assert item["path"] == "reports/summary.csv"
    assert item["display_name"] == "요약 보고서"
