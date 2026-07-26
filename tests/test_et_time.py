from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from routers.et_time import _fmt_duration, _parse_time, build_pgm_rows  # noqa: E402


def _pkg(wafer, step_id, step_seq, tkin, tkout, pt, flat="0", fab_lot="T1234.1", temp="25"):
    return {
        "fab_lot_id": fab_lot,
        "wafer_id": wafer,
        "step_id": step_id,
        "step_seq": step_seq,
        "flat_zone": flat,
        "temperature": temp,
        "tkin_time": tkin,
        "tkout_time": tkout,
        "pt_count": pt,
    }


def test_fmt_duration():
    assert _fmt_duration(35 * 60) == "35m 00s"
    assert _fmt_duration(3 * 3600 + 62) == "3h 01m 02s"
    assert _fmt_duration(9) == "9s"
    assert _fmt_duration(None) == ""


def test_parse_time_accepts_iso_and_space():
    assert _parse_time("2026-01-01T13:00:00").hour == 13
    assert _parse_time("2026-01-01 13:00:00").hour == 13
    assert _parse_time("") is None
    assert _parse_time(None) is None


def test_build_pgm_rows_groups_same_step_pgm_into_one_row():
    # wafer 2장 — 같은 step/seq/pt, 측정 시각은 다르지만 소요시간은 동일(35분).
    packages = [
        _pkg("1", "ET01", "1", "2026-01-01T13:00:00", "2026-01-01T13:35:00", 25),
        _pkg("2", "ET01", "1", "2026-01-01T13:06:00", "2026-01-01T13:41:00", 25),
    ]
    rows = build_pgm_rows(packages)

    assert len(rows) == 1
    row = rows[0]
    assert row["step_id"] == "ET01"
    assert row["pgm"] == "1(25pt)_1"
    assert row["wafer_count"] == 2
    assert row["duration_uniform"] is True
    assert row["duration_sec"] == 35 * 60
    assert row["duration_text"] == "35m 00s"
    assert row["tkin_min"] == "2026-01-01T13:00:00"
    assert row["tkout_max"] == "2026-01-01T13:41:00"


def test_build_pgm_rows_separates_pt_and_duplicate_measure():
    # 같은 step/seq 라도 pt 수가 다르면 다른 PGM(pt) 행.
    # 같은 wafer 가 같은 pt 로 두 번 측정되면 dup 차수(_1/_2)로 분리.
    packages = [
        _pkg("1", "ET01", "1", "2026-01-01T13:00:00", "2026-01-01T13:35:00", 25),
        _pkg("1", "ET01", "1", "2026-01-02T09:00:00", "2026-01-02T09:35:00", 25),  # 재측정
        _pkg("1", "ET01", "1", "2026-01-01T15:00:00", "2026-01-01T15:10:00", 10),  # pt 다름
    ]
    rows = build_pgm_rows(packages)

    labels = [r["pgm"] for r in rows]
    assert "1(10pt)_1" in labels
    assert "1(25pt)_1" in labels
    assert "1(25pt)_2" in labels
    assert len(rows) == 3


def test_build_pgm_rows_flags_duration_variance():
    # 같은 (step, PGM(pt)) 인데 wafer 간 소요시간이 다르면 uniform=False + 범위 표기.
    packages = [
        _pkg("1", "ET02", "2", "2026-01-01T10:00:00", "2026-01-01T10:30:00", 25),
        _pkg("2", "ET02", "2", "2026-01-01T11:00:00", "2026-01-01T11:40:00", 25),
    ]
    rows = build_pgm_rows(packages)

    assert len(rows) == 1
    row = rows[0]
    assert row["duration_uniform"] is False
    assert row["duration_min_sec"] == 30 * 60
    assert row["duration_max_sec"] == 40 * 60
    assert row["duration_text"] == "30m 00s ~ 40m 00s"


def test_build_pgm_rows_sorted_by_step_then_seq():
    packages = [
        _pkg("1", "ET02", "10", "2026-01-01T10:00:00", "2026-01-01T10:30:00", 5),
        _pkg("1", "ET02", "2", "2026-01-01T09:00:00", "2026-01-01T09:30:00", 5),
        _pkg("1", "ET01", "1", "2026-01-01T08:00:00", "2026-01-01T08:30:00", 5),
    ]
    rows = build_pgm_rows(packages)

    assert [(r["step_id"], r["step_seq"]) for r in rows] == [
        ("ET01", "1"), ("ET02", "2"), ("ET02", "10"),
    ]


def test_build_pgm_rows_dup_rank_is_per_fab_lot_wafer():
    # auto report Duplicate_Count 그룹에는 fab_lot_id 가 포함된다 — 같은 wafer 번호라도
    # fab lot 이 다르면 독립적으로 dup 차수를 매긴다 (둘 다 _1 이어야 함).
    packages = [
        _pkg("1", "ET01", "1", "2026-01-01T13:00:00", "2026-01-01T13:35:00", 25, fab_lot="A1000A.1"),
        _pkg("1", "ET01", "1", "2026-01-01T15:00:00", "2026-01-01T15:35:00", 25, fab_lot="A1000B.1"),
    ]
    rows = build_pgm_rows(packages)

    assert len(rows) == 1
    assert rows[0]["pgm"] == "1(25pt)_1"
    assert rows[0]["wafer_count"] == 2


def test_build_pgm_rows_temperature_rounded_to_5_like_auto_report():
    # temperature 24/26 은 25 로 정규화되어 같은 dup 그룹 — 같은 wafer 의 재측정이므로 _1/_2.
    packages = [
        _pkg("1", "ET01", "1", "2026-01-01T13:00:00", "2026-01-01T13:35:00", 25, temp="24"),
        _pkg("1", "ET01", "1", "2026-01-02T13:00:00", "2026-01-02T13:35:00", 25, temp="26"),
    ]
    rows = build_pgm_rows(packages)

    assert [r["pgm"] for r in rows] == ["1(25pt)_1", "1(25pt)_2"]


def test_build_pgm_rows_skips_blank_step_and_handles_missing_times():
    packages = [
        _pkg("1", "", "1", "2026-01-01T08:00:00", "2026-01-01T08:30:00", 5),
        _pkg("1", "ET03", "1", None, None, 7),
    ]
    rows = build_pgm_rows(packages)

    assert len(rows) == 1
    row = rows[0]
    assert row["step_id"] == "ET03"
    assert row["duration_sec"] is None
    assert row["duration_text"] == ""
