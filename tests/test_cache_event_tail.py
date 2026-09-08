import json


def test_shared_tail_reuses_parse_and_invalidates_on_file_change(monkeypatch, tmp_path):
    from core import cache_event_log as log

    path = tmp_path / "events.jsonl"
    path.write_text('{"eid":"one","detail":{"n":1}}\n', encoding="utf-8")
    monkeypatch.setattr(log, "_shared_log_path", lambda: path)
    monkeypatch.setattr(log, "_TAIL_READ_CACHE", {})
    loads = json.loads
    parsed = []

    def counted(raw):
        parsed.append(raw)
        return loads(raw)

    monkeypatch.setattr(log.json, "loads", counted)
    first = log._read_shared_tail(10)
    assert first == [{"eid": "one", "detail": {"n": 1}}]
    assert log._read_shared_tail(10)[0]["detail"]["n"] == 1
    assert len(parsed) == 1
    with path.open("a", encoding="utf-8") as stream:
        stream.write('{"eid":"two"}\n')
    assert [r["eid"] for r in log._read_shared_tail(10)] == ["one", "two"]
    path.write_text('{"eid":"replacement"}\n', encoding="utf-8")
    assert log._read_shared_tail(10) == [{"eid": "replacement"}]
    path.unlink()
    assert log._read_shared_tail(10) == []


def test_shared_tail_bounds_rows_and_ignores_invalid_records(monkeypatch, tmp_path):
    from core import cache_event_log as log

    path = tmp_path / "events.jsonl"
    path.write_text('{"eid":"old"}\nnull\nbroken\n{"eid":"new"}\n', encoding="utf-8")
    monkeypatch.setattr(log, "_shared_log_path", lambda: path)
    monkeypatch.setattr(log, "_TAIL_READ_CACHE", {})
    assert log._read_shared_tail(3) == [{"eid": "new"}]


def test_public_events_do_not_mutate_shared_snapshot(monkeypatch, tmp_path):
    from core import cache_event_log as log

    path = tmp_path / "events.jsonl"
    path.write_text('{"eid":"one","detail":{"n":1}}\n', encoding="utf-8")
    monkeypatch.setattr(log, "_shared_log_path", lambda: path)
    monkeypatch.setattr(log, "_TAIL_READ_CACHE", {})
    monkeypatch.setattr(log, "_EVENTS", [])
    log.get_events()[0]["detail"]["n"] = 999
    assert log.get_events()[0]["detail"]["n"] == 1
