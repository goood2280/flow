import json

from routers import filebrowser


def test_save_version_reuses_diff_for_summary_and_preview(tmp_path, monkeypatch):
    previous = tmp_path / "previous.csv"
    target = tmp_path / "current.csv"
    previous.write_text("id,value\n1,old\n2,same\n", encoding="utf-8")
    target.write_text("id,value\n1,new\n2,same\n", encoding="utf-8")
    monkeypatch.setattr(filebrowser, "_version_dir", lambda file: tmp_path / "versions")
    monkeypatch.setattr(filebrowser, "_base_file_versioned", lambda file, target: True)

    original_diff = filebrowser._diff_table_between
    calls = []

    def count_diff(*args, **kwargs):
        calls.append((args, kwargs))
        return original_diff(*args, **kwargs)

    monkeypatch.setattr(filebrowser, "_diff_table_between", count_diff)
    meta = filebrowser._snapshot_base_file_version(
        target, "current.csv", diff_previous=previous,
    )

    assert len(calls) == 1
    assert meta["save_diff_table"]["counts"] == {
        "added": 0, "deleted": 0, "modified": 1, "unchanged": 1,
    }
    assert meta["change_summary"]["modified_rows"] == 1
    assert meta["change_summary"]["changed_cells"] == 1


def test_version_list_does_not_recompute_complete_zero_change_summary(tmp_path, monkeypatch):
    target = tmp_path / "current.csv"
    target.write_text("id,value\n1,same\n", encoding="utf-8")
    version_dir = tmp_path / "versions"
    monkeypatch.setattr(filebrowser, "_version_dir", lambda file: version_dir)
    monkeypatch.setattr(filebrowser, "_base_file_versioned", lambda file, target: True)
    monkeypatch.setattr(filebrowser, "_resolve_base_file_for_version", lambda file: target)
    filebrowser._snapshot_base_file_version(target, "current.csv")
    filebrowser._snapshot_base_file_version(target, "current.csv")

    original_diff = filebrowser._diff_table_between
    calls = []

    def count_diff(*args, **kwargs):
        calls.append((args, kwargs))
        return original_diff(*args, **kwargs)

    monkeypatch.setattr(filebrowser, "_diff_table_between", count_diff)
    versions = filebrowser._list_base_file_versions("current.csv")
    assert len(versions) == 2
    assert calls == []

    # Older records without summary fields still get their change counts restored.
    meta_path = version_dir / "v2.meta.json"
    old_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    old_meta["change_summary"] = {"label": "내용 수정"}
    meta_path.write_text(json.dumps(old_meta), encoding="utf-8")
    filebrowser._list_base_file_versions("current.csv")
    assert len(calls) == 1
