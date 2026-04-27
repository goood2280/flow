from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core.lot_step import (  # noqa: E402
    _source_roots,
    compare_to_watch,
    expand_lot_row_for_wafer_selection,
    lookup_step_meta,
    parse_wafer_selection,
    snapshot_row_fields,
    summarize_et_steps,
)


def test_lookup_step_meta_reads_func_step_from_step_matching():
    meta = lookup_step_meta(product="PRODUCT_A0", step_id="AA100010")

    assert meta["function_step"] == "1.0 STI_PAD_OX_GROW"
    assert meta["func_step"] == "1.0 STI_PAD_OX_GROW"


def test_snapshot_row_fields_exposes_current_function_step():
    fields = snapshot_row_fields({
        "fab": {
            "step_id": "AA100010",
            "function_step": "STI_FORM",
            "time": "2026-04-24T12:34:56",
        },
    })

    assert fields["current_step"] == "AA100010"
    assert fields["current_function_step"] == "STI_FORM"
    assert fields["function_step"] == "STI_FORM"


def test_summarize_et_steps_groups_step_seq_by_function_step():
    rows = summarize_et_steps([
        {"step_id": "ETA100020", "function_step": "M1_DC", "step_seq": 2, "pt_count": 10, "time": "2026-04-23T10:00:00"},
        {"step_id": "ETA100020", "function_step": "M1_DC", "step_seq": 1, "pt_count": 10, "time": "2026-04-23T09:00:00"},
    ])

    assert rows[0]["label"] == "ETA100020 > M1_DC"
    assert rows[0]["step_seq_combo"] == "1, 2"
    assert rows[0]["display_label"] == "M1_DC(ETA100020)"
    assert rows[0]["seq_pt_combo"] == "seq1(10pt),seq2(10pt)"
    assert rows[0]["pt_count"] == 20


def test_summarize_et_steps_omits_zero_point_seq():
    rows = summarize_et_steps([
        {"step_id": "ETA100020", "function_step": "M1_DC", "step_seq": 2, "pt_count": 0, "time": "2026-04-23T10:00:00"},
        {"step_id": "ETA100020", "function_step": "M1_DC", "step_seq": 1, "pt_count": 10, "time": "2026-04-23T09:00:00"},
    ])

    assert rows[0]["step_seq_combo"] == "1"
    assert rows[0]["seq_pt_combo"] == "seq1(10pt)"


def test_parse_wafer_selection_supports_csv_and_ranges():
    assert parse_wafer_selection("1,2~4,7") == ["1", "2", "3", "4", "7"]
    assert parse_wafer_selection("4~2") == ["4", "3", "2"]


def test_source_roots_use_tracker_db_source_config(monkeypatch, tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(
        '{"tracker_db_sources":{"monitor":"CUSTOM_FAB","analysis":"CUSTOM_ET"}}',
        encoding="utf-8",
    )
    import core.lot_step as lot_step

    monkeypatch.setattr(lot_step, "_settings_file", lambda: settings)

    assert _source_roots("fab") == ["CUSTOM_FAB"]
    assert _source_roots("et") == ["CUSTOM_ET"]
    assert _source_roots("both") == ["CUSTOM_ET", "CUSTOM_FAB"]


def test_expand_lot_row_for_wafer_selection_splits_rows_and_resets_watch_state():
    rows = expand_lot_row_for_wafer_selection(
        {
            "root_lot_id": "A0001",
            "wafer_id": "1~2",
            "watch": {
                "source": "et",
                "mail": True,
                "target_et_step_id": "VIA_DC",
                "et_step_states": {"old": {}},
                "last_fired_at": "2026-04-23T10:00:00",
            },
        },
        product="PRODUCT_A0",
        root_lot_id="A0001",
        wafer_id="1~2",
        source="et",
    )

    assert [r["wafer_id"] for r in rows] == ["1", "2"]
    assert rows[0]["watch"] == {"source": "et", "mail": True, "target_et_step_id": "VIA_DC"}


def test_compare_to_watch_et_waits_until_stable_delay():
    snap = {
        "et": [
            {"step_id": "ETA100020", "function_step": "M1_DC", "step_seq": 2, "pt_count": 10, "time": "2026-04-23T10:00:00"},
            {"step_id": "ETA100020", "function_step": "M1_DC", "step_seq": 1, "pt_count": 10, "time": "2026-04-23T09:00:00"},
        ]
    }

    first = compare_to_watch(
        snap,
        {"source": "et", "target_et_seqs": "1,2"},
        now_iso="2026-04-23T10:00:00",
        et_stable_delay_minutes=180,
    )
    assert first["fire"] is False
    assert first["watch_updates"]["et_watch_initialized"] is True

    stable = compare_to_watch(
        snap,
        {
            "source": "et",
            "target_et_seqs": "1,2",
            "et_watch_initialized": True,
            "et_step_states": first["watch_updates"]["et_step_states"],
        },
        now_iso="2026-04-23T13:01:00",
        et_stable_delay_minutes=180,
    )
    assert stable["fire"] is True
    assert "ET measurement stable 180m: M1_DC(ETA100020) seq1(10pt),seq2(10pt)" in stable["reason"]


def test_compare_to_watch_et_seq_expression_supports_percent_or():
    snap = {
        "et": [
            {"step_id": "ETA100020", "function_step": "M1_DC", "step_seq": 2, "pt_count": 10, "time": "2026-04-23T10:00:00"},
        ]
    }

    first = compare_to_watch(
        snap,
        {"source": "et", "target_et_seqs": "%seq1% OR %seq2%"},
        now_iso="2026-04-23T10:00:00",
        et_stable_delay_minutes=180,
    )
    states = first["watch_updates"]["et_step_states"]
    assert states["ETA100020"]["seq_key"] == "2:10"

    stable = compare_to_watch(
        snap,
        {
            "source": "et",
            "target_et_seqs": "%seq1% OR %seq2%",
            "et_watch_initialized": True,
            "et_step_states": states,
        },
        now_iso="2026-04-23T13:01:00",
        et_stable_delay_minutes=180,
    )
    assert stable["fire"] is True
    assert "seq2(10pt)" in stable["reason"]
