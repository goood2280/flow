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


def test_db_sql_key_reuse_is_shared_across_products_in_same_root(tmp_path, monkeypatch):
    history_path = tmp_path / "filebrowser_sql_execution_history.jsonl"
    monkeypatch.setattr(filebrowser, "_filebrowser_sql_execution_history_path", lambda: history_path)
    monkeypatch.setattr(filebrowser, "current_user", lambda request: {"username": "engineer"})
    filebrowser._record_filebrowser_sql_execution(
        object(),
        scope="db_product",
        root="FAB_DB",
        product="PRODUCT_A",
        sql="wafer_id = 1",
        ok=True,
    )
    original = filebrowser.jsonl_read(history_path, limit=0)[0]

    @filebrowser._track_filebrowser_sql_execution("db_product")
    def execute(*, root, product, sql, meta_only=True, page=0, reuse_history_id="", request=None):
        return {"data": [{"wafer_id": 1}], "showing": 1}

    execute(
        root="FAB_DB",
        product="PRODUCT_B",
        sql="wafer_id = 1",
        meta_only=False,
        page=0,
        reuse_history_id=original["history_id"],
        request=object(),
    )
    rows = filebrowser.jsonl_read(history_path, limit=0)

    assert len(rows) == 1
    assert rows[0]["product"] == "PRODUCT_A"
    assert rows[0]["reuse_count"] == 1

    with pytest.raises(HTTPException) as exc_info:
        execute(
            root="OTHER_DB",
            product="PRODUCT_B",
            sql="wafer_id = 1",
            meta_only=False,
            page=0,
            reuse_history_id=original["history_id"],
            request=object(),
        )
    assert exc_info.value.status_code == 404


def test_db_sql_history_lists_all_products_in_same_root(tmp_path, monkeypatch):
    history_path = tmp_path / "filebrowser_sql_execution_history.jsonl"
    monkeypatch.setattr(filebrowser, "_filebrowser_sql_execution_history_path", lambda: history_path)
    monkeypatch.setattr(filebrowser, "_require_filebrowser_user", lambda request: {"username": "engineer"})
    for root, product, sql in (
        ("FAB_DB", "PRODUCT_A", "value > 1"),
        ("FAB_DB", "PRODUCT_B", "value > 2"),
        ("OTHER_DB", "PRODUCT_C", "value > 3"),
    ):
        filebrowser._record_filebrowser_sql_execution(
            None,
            scope="db_product",
            root=root,
            product=product,
            sql=sql,
            ok=True,
        )

    payload = filebrowser.filebrowser_sql_execution_history(
        object(),
        scope="db_product",
        root="FAB_DB",
        product="PRODUCT_B",
        file="",
        history_id="",
        limit=500,
        access_scope="",
    )

    assert [row["product"] for row in payload["history"]] == ["PRODUCT_B", "PRODUCT_A"]
    assert [row["sql"] for row in payload["history"]] == ["value > 2", "value > 1"]


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


def test_sql_history_share_key_resolves_target_without_running_query(tmp_path, monkeypatch):
    history_path = tmp_path / "filebrowser_sql_execution_history.jsonl"
    monkeypatch.setattr(filebrowser, "_filebrowser_sql_execution_history_path", lambda: history_path)
    monkeypatch.setattr(filebrowser, "_require_filebrowser_user", lambda request: {"username": "engineer"})
    filebrowser.jsonl_append(history_path, {
        "event": "execution", "history_id": "fb_sql_exec_012345abcdef",
        "scope": "db_product", "root": "FAB_DB", "product": "PRODUCT_A",
        "sql": "value > 7",
    })

    payload = filebrowser.filebrowser_sql_execution_history(
        object(), scope="", root="", product="", file="",
        history_id="fb_sql_exec_012345abcdef", limit=1, access_scope="",
    )

    assert payload["history"][0]["root"] == "FAB_DB"
    assert payload["history"][0]["product"] == "PRODUCT_A"
    assert payload["history"][0]["sql"] == "value > 7"


def test_sql_history_share_key_rechecks_base_file_access(tmp_path, monkeypatch):
    history_path = tmp_path / "filebrowser_sql_execution_history.jsonl"
    monkeypatch.setattr(filebrowser, "_filebrowser_sql_execution_history_path", lambda: history_path)
    monkeypatch.setattr(filebrowser, "_require_filebrowser_user", lambda request: {"username": "engineer"})
    checked = []
    monkeypatch.setattr(filebrowser, "_require_base_file_access", lambda request, file, access_scope: checked.append((file, access_scope)))
    filebrowser.jsonl_append(history_path, {
        "event": "execution", "history_id": "fb_sql_exec_abcdef012345",
        "scope": "base", "file": "private/sample.parquet", "sql": "value > 2",
    })

    payload = filebrowser.filebrowser_sql_execution_history(
        object(), scope="", root="", product="", file="",
        history_id="fb_sql_exec_abcdef012345", limit=1, access_scope="",
    )

    assert payload["history"][0]["sql"] == "value > 2"
    assert checked == [("private/sample.parquet", "")]

    checked.clear()
    filebrowser.filebrowser_sql_execution_history(
        object(), scope="base", root="", product="", file="private/sample.parquet",
        history_id="", limit=500, access_scope="",
    )
    assert checked == [("private/sample.parquet", "")]
