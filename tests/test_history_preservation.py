import datetime as dt
import json

from core import audit, utils
from core.paths import PATHS
from routers import admin, filebrowser


def write_rows(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_audit_and_download_logs_ignore_retention_caps(monkeypatch, tmp_path):
    monkeypatch.setattr(PATHS, "download_log", tmp_path / "downloads.jsonl")
    monkeypatch.setattr(utils, "_JSONL_TRIM_CHECK_EVERY", 1)
    for path in (PATHS.activity_log, PATHS.download_log):
        for index in range(5):
            utils.jsonl_append(path, {"index": index}, max_lines=1, max_bytes=1)
        assert [r["index"] for r in utils.jsonl_read(path, limit=0)] == list(range(5))
    assert audit.ACTIVITY_LOG_MAX_BYTES is None
    operational = tmp_path / "operational.jsonl"
    for index in range(5):
        utils.jsonl_append(operational, {"index": index}, max_lines=1)
    assert len(utils.jsonl_read(operational, limit=0)) == 1


def test_pages_reach_all_records_and_filter_before_slicing(tmp_path):
    path = tmp_path / "history.jsonl"
    write_rows(path, [{"index": i, "username": "alice" if i % 2 == 0 else "bob"} for i in range(6002)])
    with path.open("a", encoding="utf-8") as stream:
        stream.write('broken\nnull\n{"partial":')
    result = []
    for offset in range(0, 3001, 100):
        page = utils.jsonl_page(path, 100, offset, lambda row: row["username"] == "alice")
        assert page["total"] == 3001
        assert len(page["logs"]) <= 100
        result.extend(row["index"] for row in page["logs"])
    assert result == list(range(6000, -1, -2))
    assert page["has_more"] is False
    assert utils.jsonl_page(path, 100, 7000)["logs"] == []
    assert utils.jsonl_page(tmp_path / "absent")["total"] == 0


def test_activity_history_authorization_old_users_and_days(monkeypatch, tmp_path):
    path = tmp_path / "activity.jsonl"
    today = dt.date.today().isoformat()
    write_rows(path, [{"actor": "old-user", "timestamp": "2020-01-01"}] + [
        {"username": "alice", "timestamp": today, "action": "nav:home"} for _ in range(5001)
    ] + [{"username": "bob", "timestamp": today}])
    monkeypatch.setattr(admin, "ACTIVITY_LOG", path)
    monkeypatch.setattr(admin, "current_user", lambda request: {"username": "alice", "role": "user"})
    page = admin.get_logs(None, username="bob", offset=5000)
    assert page["total"] == 5001
    assert [row["username"] for row in page["logs"]] == ["alice"]
    monkeypatch.setattr(admin, "current_user", lambda request: {"username": "root", "role": "admin"})
    assert admin.get_logs(None, username="old-user")["total"] == 1
    assert admin.get_logs(None, username="old-user", days=1)["total"] == 0
    assert {row["username"] for row in admin.get_log_users(_admin={})["users"]} == {"old-user", "alice", "bob"}


def test_download_history_filters_all_records_and_enforces_owner(monkeypatch, tmp_path):
    from core import auth
    path = tmp_path / "downloads.jsonl"
    write_rows(path, [{"username": "alice", "product": "old-chip"}] + [
        {"username": "bob", "source": "splittable", "product": "new-chip"} for _ in range(501)
    ])
    monkeypatch.setattr(filebrowser, "DL_LOG", path)
    monkeypatch.setattr(auth, "current_user", lambda request: {"username": "alice", "role": "user"})
    assert filebrowser.download_history(None, username="bob")["total"] == 1
    monkeypatch.setattr(auth, "current_user", lambda request: {"username": "root", "role": "admin"})
    page = filebrowser.download_history(None, q="old-chip", source="filebrowser")
    assert page["total"] == 1
    assert page["logs"][0]["username"] == "alice"
    assert filebrowser.download_history(None, source="splittable", offset=500)["total"] == 501


def test_repeated_et_downloads_record_each_actual_user(monkeypatch, tmp_path):
    import asyncio
    from core import download_queue
    from routers import reformatize

    path = tmp_path / "result.csv"
    path.write_bytes(b"value\n1\n")
    job = {"username": "owner", "state": "done", "result": {"path": str(path), "meta": {}}}
    monkeypatch.setattr(reformatize, "_job_for_user", lambda *args: job)
    monkeypatch.setattr(download_queue, "mark_fetched", lambda *args: None)
    monkeypatch.setattr(download_queue, "fetched_once", lambda *args: True)
    recorded = []
    monkeypatch.setattr(reformatize, "_record_download", lambda *args: recorded.append(args))

    async def fetch():
        response = reformatize.download_file("job", user={"username": "admin", "role": "admin"})
        return b"".join([part async for part in response.body_iterator])

    assert asyncio.run(fetch()) == path.read_bytes()
    assert asyncio.run(fetch()) == path.read_bytes()
    assert len(recorded) == 2
    assert all(row[0] == "admin" and row[-1] == path.stat().st_size for row in recorded)
