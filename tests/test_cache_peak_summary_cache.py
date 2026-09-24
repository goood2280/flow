import json
import os
from concurrent.futures import ThreadPoolExecutor


def _row(ts, rss, *, kind="pss", effective=None, origin="prod", host="flow"):
    return {
        "ts": ts,
        "rss_gb": rss,
        "effective_gb": rss if effective is None else effective,
        "effective_kind": kind,
        "origin": origin,
        "host": host,
    }


def _write(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _isolate(monkeypatch, log, path, *, wall=10_000.0, monotonic=100.0):
    clock = {"wall": wall, "mono": monotonic}
    monkeypatch.setattr(log, "_ram_peak_log_path", lambda: path)
    monkeypatch.setattr(log, "_origin", lambda: ("prod", "flow"))
    monkeypatch.setattr(log, "_RAM_PEAK_READ_CACHE", {})
    monkeypatch.setattr(log.time, "time", lambda: clock["wall"])
    monkeypatch.setattr(log.time, "monotonic", lambda: clock["mono"])
    return clock


def test_recent_peak_reuses_summary_for_append_until_ttl(monkeypatch, tmp_path):
    from core import cache_event_log as log

    path = tmp_path / "ram.jsonl"
    _write(path, [_row(9_990.0, 2.0)])
    clock = _isolate(monkeypatch, log, path)
    real_loads = json.loads
    parsed = []

    def counted(raw):
        parsed.append(raw)
        return real_loads(raw)

    monkeypatch.setattr(log.json, "loads", counted)
    assert log.recent_peak_rss(1.0, effective_kind="pss")["peak_rss_gb"] == 2.0
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(_row(9_999.0, 4.0)) + "\n")

    # Poll-time appends are allowed bounded staleness and avoid a 30k-row parse.
    assert log.recent_peak_rss(1.0, effective_kind="pss")["peak_rss_gb"] == 2.0
    assert len(parsed) == 1

    clock["mono"] += log._RAM_PEAK_CACHE_TTL_SEC + 0.01
    assert log.recent_peak_rss(1.0, effective_kind="pss")["peak_rss_gb"] == 4.0
    assert len(parsed) == 3


def test_recent_peak_invalidates_replaced_file_before_ttl(monkeypatch, tmp_path):
    from core import cache_event_log as log

    path = tmp_path / "ram.jsonl"
    _write(path, [_row(9_990.0, 2.0)])
    _isolate(monkeypatch, log, path)
    assert log.recent_peak_rss(1.0)["peak_rss_gb"] == 2.0

    replacement = tmp_path / "replacement.jsonl"
    _write(replacement, [_row(9_999.0, 7.0)])
    os.replace(replacement, path)
    assert log.recent_peak_rss(1.0)["peak_rss_gb"] == 7.0


def test_recent_peak_cache_separates_kind_identity_and_window(monkeypatch, tmp_path):
    from core import cache_event_log as log

    path = tmp_path / "ram.jsonl"
    _write(path, [
        _row(9_500.0, 5.0, kind="pss", effective=2.0),
        _row(9_995.0, 6.0, kind="rss_fallback", effective=6.0),
        _row(9_995.0, 9.0, origin="worker"),
    ])
    _isolate(monkeypatch, log, path)

    pss = log.recent_peak_rss(1.0, effective_kind="pss")
    fallback = log.recent_peak_rss(1.0, effective_kind="rss_fallback")
    short = log.recent_peak_rss(0.1, effective_kind="pss")
    assert (pss["peak_rss_gb"], pss["peak_effective_gb"]) == (6.0, 2.0)
    assert fallback["peak_effective_gb"] == 6.0
    assert short["sample_count"] == 1


def test_recent_peak_ignores_malformed_numeric_values(monkeypatch, tmp_path):
    from core import cache_event_log as log

    path = tmp_path / "ram.jsonl"
    bad_rss = _row(9_998.0, "not-a-number")
    bad_effective = _row(9_999.0, 8.0, effective="also-not-a-number")
    _write(path, [bad_rss, bad_effective, _row(9_999.0, 3.0, effective=1.5)])
    _isolate(monkeypatch, log, path)

    peak = log.recent_peak_rss(1.0, effective_kind="pss")
    assert peak["sample_count"] == 1
    assert peak["peak_rss_gb"] == 3.0
    assert peak["peak_effective_gb"] == 1.5


def test_recent_peak_ttl_refreshes_unchanged_file_for_rolling_window(monkeypatch, tmp_path):
    from core import cache_event_log as log

    path = tmp_path / "ram.jsonl"
    _write(path, [_row(9_999.0, 3.0)])
    clock = _isolate(monkeypatch, log, path)
    assert log.recent_peak_rss(0.1)["sample_count"] == 1

    clock["wall"] += 400.0
    clock["mono"] += log._RAM_PEAK_CACHE_TTL_SEC + 0.01
    refreshed = log.recent_peak_rss(0.1)
    assert refreshed["sample_count"] == 0
    assert refreshed["peak_rss_gb"] == 0.0


def test_recent_peak_concurrent_callers_share_one_parse(monkeypatch, tmp_path):
    from core import cache_event_log as log

    path = tmp_path / "ram.jsonl"
    _write(path, [_row(9_999.0, float(value)) for value in range(1, 101)])
    _isolate(monkeypatch, log, path)
    real_loads = json.loads
    parsed = []

    def counted(raw):
        parsed.append(raw)
        return real_loads(raw)

    monkeypatch.setattr(log.json, "loads", counted)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _n: log.recent_peak_rss(1.0), range(8)))

    assert {result["peak_rss_gb"] for result in results} == {100.0}
    assert len(parsed) == 100
