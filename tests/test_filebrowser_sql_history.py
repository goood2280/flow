import pytest
from fastapi import HTTPException

from routers import filebrowser


def test_filebrowser_sql_key_reuse_increments_original_without_duplicate(tmp_path, monkeypatch):
    history_path = tmp_path / "filebrowser_sql_execution_history.jsonl"
    monkeypatch.setattr(filebrowser, "_filebrowser_sql_execution_history_path", lambda: history_path)
    monkeypatch.setattr(filebrowser, "current_user", lambda request: {"username": "engineer"})
    filebrowser._record_filebrowser_sql_execution(
        object(),
        scope="rootpq",
        file="sample.parquet",
        sql="value > 10",
        ok=True,
        result={"data": [{"value": 11}], "showing": 1},
    )
    original = filebrowser.jsonl_read(history_path, limit=0)[0]

    @filebrowser._track_filebrowser_sql_execution("rootpq")
    def execute(*, file, sql, meta_only=True, page=0, reuse_history_id="", request=None):
        return {"data": [{"value": 11}], "showing": 1}

    result = execute(
        file="sample.parquet",
        sql="value > 10",
        meta_only=False,
        page=0,
        reuse_history_id=original["history_id"],
        request=object(),
    )
    rows = filebrowser.jsonl_read(history_path, limit=0)

    assert result["showing"] == 1
    assert len(rows) == 1
    assert rows[0]["history_id"] == original["history_id"]
    assert rows[0]["timestamp"] == original["timestamp"]
    assert rows[0]["reuse_count"] == 1
    assert rows[0]["last_reused_by"] == "engineer"

    execute(
        file="sample.parquet",
        sql="value > 10",
        meta_only=False,
        page=0,
        request=object(),
    )
    assert len(filebrowser.jsonl_read(history_path, limit=0)) == 2


def test_filebrowser_sql_key_reuse_rejects_a_different_target(tmp_path, monkeypatch):
    history_path = tmp_path / "filebrowser_sql_execution_history.jsonl"
    monkeypatch.setattr(filebrowser, "_filebrowser_sql_execution_history_path", lambda: history_path)
    monkeypatch.setattr(filebrowser, "current_user", lambda request: {"username": "engineer"})
    filebrowser._record_filebrowser_sql_execution(
        object(), scope="rootpq", file="one.parquet", sql="value > 10", ok=True,
    )
    history_id = filebrowser.jsonl_read(history_path, limit=0)[0]["history_id"]

    @filebrowser._track_filebrowser_sql_execution("rootpq")
    def execute(*, file, sql, meta_only=True, page=0, reuse_history_id="", request=None):
        return {"data": []}

    with pytest.raises(HTTPException) as exc_info:
        execute(
            file="two.parquet",
            sql="value > 10",
            meta_only=False,
            page=0,
            reuse_history_id=history_id,
            request=object(),
        )

    assert exc_info.value.status_code == 404
    assert len(filebrowser.jsonl_read(history_path, limit=0)) == 1


def test_filebrowser_sql_history_returns_newest_500_rows(tmp_path, monkeypatch):
    history_path = tmp_path / "filebrowser_sql_execution_history.jsonl"
    monkeypatch.setattr(filebrowser, "_filebrowser_sql_execution_history_path", lambda: history_path)
    monkeypatch.setattr(filebrowser, "_require_filebrowser_user", lambda request: {"username": "engineer"})
    for index in range(505):
        filebrowser.jsonl_append(history_path, {
            "event": "execution",
            "history_id": f"fb_sql_exec_{index:012x}",
            "scope": "rootpq",
            "file": "sample.parquet",
            "sql": f"value > {index}",
            "timestamp": f"2026-09-02T00:{index // 60:02d}:{index % 60:02d}+00:00",
        }, max_lines=None)

    payload = filebrowser.filebrowser_sql_execution_history(
        object(),
        scope="rootpq",
        root="",
        product="",
        file="sample.parquet",
        history_id="",
        limit=500,
        access_scope="",
    )

    assert payload["limit"] == 500
    assert len(payload["history"]) == 500
    assert payload["history"][0]["sql"] == "value > 504"
    assert payload["history"][-1]["sql"] == "value > 5"
