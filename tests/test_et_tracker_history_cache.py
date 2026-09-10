import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def _issue(lots):
    return {
        "id": "ISS-CACHE",
        "product": "PRODA",
        "lots": lots,
    }


def _lot(root_lot_id, wafer_id="1"):
    return {
        "product": "PRODA",
        "root_lot_id": root_lot_id,
        "wafer_id": wafer_id,
        "username": "tester",
    }


def test_tracker_measurement_scan_copies_from_et_history_cache(monkeypatch):
    from core import et_tracker, lot_step

    calls = []

    def history_lookup(**kwargs):
        calls.append(kwargs)
        kwargs["diag"].update({
            "cache": "et_history",
            "history_built_at": "2026-08-11T10:00:00",
            "max_file_date": "2026-08-11",
            "source_root": "1.RAWDATA_DB_ET",
        })
        return [{
            "wafer_id": "1",
            "step_id": "ET100",
            "step_seq": "H1",
            "flat": "A",
            "time": "2026-08-11T09:00:00",
            "pt_count": 20,
        }]

    monkeypatch.setattr(lot_step, "et_history_packages", history_lookup)
    monkeypatch.setattr(
        lot_step,
        "et_packages",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("raw ET DB fallback must not run")),
    )

    issue = _issue([_lot("ROOT-001")])
    new_items, changed, scanned = et_tracker._scan_issue_lots(
        issue,
        source_root="1.RAWDATA_DB_ET",
        now_iso="2026-08-11T10:01:00",
    )

    assert changed is True
    assert scanned == 1
    assert len(new_items) == 1
    assert calls[0]["root_lot_id"] == "ROOT-001"
    saved = issue["lots"][0]
    assert saved["last_scan_cache"] == "et_history"
    assert saved["last_scan_files"] == 0
    assert saved["et_history"][0]["step_id"] == "ET100"


def test_tracker_measurement_scan_never_falls_back_when_history_cache_is_missing(monkeypatch):
    from core import et_tracker, lot_step

    monkeypatch.setattr(lot_step, "et_history_packages", lambda **_kwargs: None)
    monkeypatch.setattr(
        lot_step,
        "et_packages",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("raw ET DB fallback must not run")),
    )

    issue = _issue([_lot("ROOT-404")])
    new_items, changed, scanned = et_tracker._scan_issue_lots(
        issue,
        source_root="1.RAWDATA_DB_ET",
        now_iso="2026-08-11T10:02:00",
    )

    assert new_items == []
    assert changed is True
    assert scanned == 1
    saved = issue["lots"][0]
    assert saved["last_scan_status"] == "error"
    assert saved["last_scan_error"] == "ET history scan 결과가 준비되지 않았습니다"


def test_tracker_scan_source_has_no_history_refresh_or_raw_db_fallback():
    source = (BACKEND / "core" / "et_tracker.py").read_text(encoding="utf-8")
    scan_source = source[source.index("def _scan_issue_lots"):source.index("# ─────────────────────────── mail body")]
    phase_source = source[source.index("def scan_phase"):source.index("def _notify_issue")]

    assert "et_packages(" not in scan_source
    assert "et_packages_multi(" not in scan_source
    assert "refresh_et_history_cache" not in phase_source


def test_tracker_detail_ui_keeps_title_author_time_on_one_line_and_hides_old_hint():
    source = (
        BACKEND.parent / "frontend" / "src" / "features" / "tracker" / "My_Tracker.jsx"
    ).read_text(encoding="utf-8")

    assert '작성자 <strong style={{ color: "var(--text-primary)" }}>{selected.username}</strong> ·' in source
    assert 'flexWrap: "nowrap"' in source
    assert "등록된 root_lot_id / wafer_id 기준으로 ET DB 에서" not in source


def test_scan_prefetch_shares_queries_across_issues_and_keeps_diff(monkeypatch):
    from core import et_tracker, lot_step

    calls = []
    package = {"wafer_id": "1", "step_id": "ET100", "step_seq": "H1",
               "flat": "A", "time": "2026-09-10T10:00:00", "pt_count": 2}

    def lookup(product, specs, **kwargs):
        calls.append((product, specs))
        kwargs["diag"].update(cache="et_history", max_file_date="2026-09-10")
        return [[dict(package)] for _ in specs]

    monkeypatch.setattr(lot_step, "et_history_packages_multi", lookup)
    monkeypatch.setattr(lot_step, "et_history_packages", lambda **kw: (_ for _ in ()).throw(AssertionError("extra read")))
    first, second = _issue([_lot("ROOT-1")]), _issue([_lot("ROOT-1")])
    for iteration in range(2):
        cache = et_tracker._prefetch_history([(first, "ET"), (second, "ET")], full=False)
        assert len(calls[-1][1]) == 1
        for issue in (first, second):
            new, _, count = et_tracker._scan_issue_lots(
                issue, source_root="ET", now_iso="2026-09-11T08:00:00", package_cache=cache)
            assert count == 1
            assert len(new) == (1 if iteration == 0 else 0)
            assert len(issue["lots"][0]["et_history"]) == 1
    assert len(calls) == 2


def test_shared_history_read_preserves_per_wafer_window_and_limit(monkeypatch):
    import polars as pl
    from core import lot_step

    frame = pl.DataFrame([
        {"root_lot_id": "ROOT-1", "lot_id": "LOT-1", "wafer_id": wafer,
         "step_id": "ET100", "step_seq": "H1", "flat": "A", "pt_count": 1,
         "time": day + "T10:00:00"}
        for wafer in ("1", "2") for day in ("2026-09-01", "2026-09-10")
    ])
    monkeypatch.setattr(lot_step, "_et_history_cache_current", lambda *a: {
        "lf": frame.lazy(), "meta": {"max_file_date": "2026-09-10"}, "source_root": "ET"})
    meta_calls = []
    monkeypatch.setattr(lot_step, "lookup_step_meta", lambda **kw: meta_calls.append(kw) or {})
    rows = lot_step.et_history_packages_multi("PRODA", [
        {"root_lot_id": "ROOT-1", "wafer_id": "1", "since_date": "", "limit": 5},
        {"root_lot_id": "ROOT-1", "wafer_id": "2", "since_date": "2026-09-09", "limit": 5},
        {"root_lot_id": "ROOT-1", "wafer_id": "1", "since_date": "", "limit": 1},
    ])
    assert [len(row) for row in rows] == [2, 1, 1]
    assert rows[1][0]["wafer_id"] == "2"
    assert rows[2][0]["time"].startswith("2026-09-10")
    assert len(meta_calls) == 1


def test_schedule_defaults_and_legacy_slots_are_twice_daily(monkeypatch, tmp_path):
    from core import et_tracker
    from core.utils import save_json

    settings = tmp_path / "settings.json"
    monkeypatch.setattr(et_tracker, "_settings_file", lambda: settings)
    assert et_tracker.et_tracker_config()["scan_times"] == ["08:00", "20:00"]
    save_json(settings, {"tracker_et_scan": {"scan_times": ["08:00", "12:00", "20:00"]}})
    assert et_tracker.et_tracker_config()["scan_times"] == ["08:00", "20:00"]
    save_json(settings, {"tracker_et_scan": {"scan_times": []}})
    assert et_tracker.et_tracker_config()["scan_times"] == []


def test_failed_prefetch_keeps_existing_history_and_watermark(monkeypatch):
    from core import et_tracker, lot_step

    lot = _lot("ROOT-1")
    lot.update(et_history=[{"step_id": "OLD"}], et_scan_watermark="2026-09-01")
    issue = _issue([lot])
    calls = []
    monkeypatch.setattr(lot_step, "et_history_packages_multi", lambda *a, **kw: calls.append(a) or None)
    cache = et_tracker._prefetch_history([(issue, "ET")], full=False)
    new, _, _ = et_tracker._scan_issue_lots(issue, source_root="ET", now_iso="now", package_cache=cache)
    assert len(calls) == 1
    assert new == []
    saved = issue["lots"][0]
    assert saved["et_history"] == [{"step_id": "OLD"}]
    assert saved["et_scan_watermark"] == "2026-09-01"
    assert saved["last_scan_status"] == "error"


def test_apply_scan_keeps_newer_user_edit(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from core import et_tracker, paths, utils

    monkeypatch.setattr(paths, "PATHS", SimpleNamespace(data_root=tmp_path))
    path = tmp_path / "tracker" / "issues.json"
    current = {"id": "ISS-1", "revision": 2, "lots": [_lot("USER-EDIT")]}
    utils.save_json(path, [current])
    result = et_tracker._apply_scan_result({"ok": True, "issues": [{
        "issue_id": "ISS-1", "revision": 1, "changed": True, "lots": [_lot("OLD")],
    }]}, {"pgm_filters": []}, notify=False, actor="test")
    assert result["skipped_stale"] == 1
    assert utils.load_json(path, []) == [current]
