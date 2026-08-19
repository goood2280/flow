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
