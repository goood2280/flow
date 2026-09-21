import datetime as dt
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from core import activity_index as index


@pytest.fixture(autouse=True)
def local_index(monkeypatch, tmp_path):
    monkeypatch.setattr(index, "_cache_path", lambda source: tmp_path / "index.sqlite3")


def row(user="alice", **kwargs):
    return {"username": user, "timestamp": dt.datetime.now().isoformat(), "action": "nav:home", **kwargs}


def write(path, rows, mode="w"):
    with path.open(mode, encoding="utf-8") as stream:
        stream.writelines(json.dumps(entry) + "\n" for entry in rows)


def test_only_new_lines_are_parsed_and_history_remains_intact(tmp_path, monkeypatch):
    path = tmp_path / "activity.jsonl"
    write(path, [row() for _ in range(100)])
    original = path.read_bytes()
    assert index.summary(path)["total"] == 100
    parsed = []
    insert = index._insert
    monkeypatch.setattr(index, "_insert", lambda db, batch: (parsed.extend(batch), insert(db, batch))[1])
    assert index.features(path)["features"][0]["count"] == 100
    assert parsed == []
    write(path, [row("bob")], "a")
    assert index.page(path)["total"] == 101
    assert len(parsed) == 1
    assert path.read_bytes().startswith(original)


def test_partial_lines_corruption_truncate_replace_and_delete(tmp_path):
    path = tmp_path / "activity.jsonl"
    write(path, [row()])
    with path.open("a", encoding="utf-8") as stream:
        stream.write('broken\nnull\n{"username":"bob"')
    assert index.page(path)["total"] == 1
    with path.open("a", encoding="utf-8") as stream:
        stream.write('}\n')
    assert index.page(path)["total"] == 2
    write(path, [row("c")])
    assert index.page(path)["total"] == 1
    replacement = tmp_path / "replacement"
    write(replacement, [row("d") for _ in range(5)])
    replacement.replace(path)
    assert index.page(path)["total"] == 5
    assert index.page(path)["logs"][0]["username"] == "d"
    path.unlink()
    assert index.summary(path)["total"] == 0


def test_concurrent_readers_do_not_duplicate_append(tmp_path):
    path = tmp_path / "activity.jsonl"
    write(path, [row() for _ in range(100)])
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(lambda _: index.page(path)["total"], range(8))) == [100] * 8
    write(path, [row("bob")], "a")
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(lambda _: index.page(path)["total"], range(8))) == [101] * 8


def test_filters_unicode_and_exact_owner_before_pagination(tmp_path):
    path = tmp_path / "activity.jsonl"
    write(path, [row("alice"), row("malice"), row("ALICE"), row("홍길동", action="DB:ÖPEN"),
                 row("alice", timestamp="2020-01-01")])
    assert index.page(path, username="alice", exact_user=True)["total"] == 2
    assert index.page(path, username="alice", days=1)["total"] == 3
    result = index.page(path, username="alice", limit=1, offset=1)
    assert result["total"] == 4 and result["logs"][0]["username"] == "ALICE"
    assert index.page(path, username="길동", action="öpen")["total"] == 1
