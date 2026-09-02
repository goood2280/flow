import datetime
from pathlib import Path

import polars as pl

from core import shared_lease
from routers import splittable


def test_sop_reader_prefers_explicit_por_row(tmp_path):
    path = tmp_path / "PRODA_sop.csv"
    path.write_text(
        "step_id,ppid,status\nAA100,PP_SPLIT,split\nAA100,PP_STD,POR\nAA200,PP_TWO,\n",
        encoding="utf-8",
    )
    rows = splittable._s0_read_sop_file(path)
    assert rows["aa100"]["ppid"] == "PP_STD"
    assert rows["aa200"]["ppid"] == "PP_TWO"


def test_daily_s0_snapshot_is_append_only_and_archives_product_parquet(tmp_path, monkeypatch):
    credential = tmp_path / "Fab" / "credential"
    credential.mkdir(parents=True)
    source_path = credential / "PRODA_sop.csv"
    source_path.write_text("step_id,ppid\nAA100,PP_A\n", encoding="utf-8")
    state_file = tmp_path / "state" / "knob_s0_registry.json"
    daily_dir = tmp_path / "state" / "daily"
    mutable = {
        "signature": "first",
        "knobs": ["KNOB_ALPHA"],
        "por": {"KNOB_ALPHA": {"step_id": "AA100", "ppid": "PP_A"}},
    }

    monkeypatch.setattr(splittable, "_S0_STATE_FILE", state_file)
    monkeypatch.setattr(splittable, "_S0_DAILY_DIR", daily_dir)
    monkeypatch.setattr(splittable, "_s0_ml_table_products", lambda: ["PRODA"])
    monkeypatch.setattr(splittable, "_mltable_schema_columns", lambda product, prefix="": list(mutable["knobs"]))
    monkeypatch.setattr(splittable, "_s0_current_candidate", lambda product, knob, rows: dict(mutable["por"].get(knob) or {}))
    monkeypatch.setattr(splittable, "_s0_source_signature", lambda catalog: mutable["signature"])
    monkeypatch.setattr(splittable, "_s0_sop_catalog", lambda: {
        "proda": {
            "product": "PRODA",
            "file": source_path.name,
            "path": source_path,
            "modified_at": "2026-09-03T01:00:00",
            "rows": {"aa100": {"step_id": "AA100", "ppid": mutable["por"]["KNOB_ALPHA"]["ppid"]}},
        }
    })
    monkeypatch.setattr(shared_lease, "try_acquire", lambda name, ttl: True)
    monkeypatch.setattr(shared_lease, "release", lambda name: None)

    first = splittable.refresh_knob_s0_snapshots(
        force=True, now=datetime.datetime(2026, 9, 3, 3, 0, 0)
    )
    assert first["knobs_captured"] == 1
    state = splittable._s0_load_state()
    assert state["products"]["PRODA"]["KNOB_ALPHA"]["ppid"] == "PP_A"
    archived = credential / "PRODA" / "2026-09-03.parquet"
    assert archived.is_file()
    frame = pl.read_parquet(archived)
    assert frame.filter(pl.col("step_id") == "AA100")["por_ppid"].item() == "PP_A"

    mutable["signature"] = "second"
    mutable["knobs"].append("KNOB_BETA")
    mutable["por"] = {
        "KNOB_ALPHA": {"step_id": "AA100", "ppid": "PP_B"},
        "KNOB_BETA": {"step_id": "AA100", "ppid": "PP_B"},
    }
    second = splittable.refresh_knob_s0_snapshots(
        force=True, now=datetime.datetime(2026, 9, 4, 3, 0, 0)
    )
    assert second["knobs_captured"] == 1
    state = splittable._s0_load_state()
    assert state["products"]["PRODA"]["KNOB_ALPHA"]["ppid"] == "PP_A"
    assert state["products"]["PRODA"]["KNOB_BETA"]["ppid"] == "PP_B"
    assert (credential / "PRODA" / "2026-09-04.parquet").is_file()


def test_split_exports_put_snapshot_por_in_s0_even_when_not_first_observed():
    value_maps = {"KNOB_A": ({0: "PP_X", 1: "PP_STD"}, {})}
    rows = splittable._build_split_check_export_rows(
        ["KNOB_A"], 2, value_maps, s0_by_param={"KNOB_A": "PP_STD"}
    )
    assert rows[0][:3] == ["KNOB_A", "PP_STD", "S0"]
    assert rows[1][:3] == ["KNOB_A", "PP_X", "S1"]
