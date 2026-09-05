import datetime as dt
import json

import polars as pl

from routers import splittable
from core import split_s0_history


def test_migration_preserves_registry_older_than_global_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(splittable, "_S0_DAILY_DIR", tmp_path)
    state = {"products": {"PRODA": {"KNOB_A": {
        "step_id": "STEP1", "ppid": "A", "sop_file": "f_step.csv",
        "captured_at": "2026-09-01T00:00:00",
    }}}}
    splittable._s0_update_source_history(state, {"*": {
        "file": "f_step.csv", "rows": {"step1": {"step_id": "STEP1", "ppid": "B"}},
    }}, dt.datetime(2026, 9, 2))
    events = state["sop_history"]["*"]["step1"]
    assert split_s0_history.resolve(events, "2026-08-01")["ppid"] == "A"
    assert split_s0_history.resolve(events, "2026-09-02")["ppid"] == "B"


def test_plan_save_persists_sop_basis_in_current_and_history(tmp_path, monkeypatch):
    from types import SimpleNamespace
    path = tmp_path / "plans.json"
    monkeypatch.setattr(splittable, "_plan_history_path", lambda product: path)
    monkeypatch.setattr(splittable, "_load_plan_data", lambda product: {"plans": {}, "history": []})
    monkeypatch.setattr(splittable, "_knob_current_s0_for_product", lambda product, cols: {
        "KNOB_A": {"ppid": "SOP_A", "step_id": "STEP1"},
    })
    monkeypatch.setattr(splittable, "_archive_plan_history", lambda *a: None)
    monkeypatch.setattr(splittable, "_invalidate_plan_risk_cache", lambda *a: None)
    monkeypatch.setattr(splittable, "_audit_user", lambda *a, **k: None)
    monkeypatch.setattr(splittable, "threading", SimpleNamespace(Thread=lambda **k: SimpleNamespace(start=lambda: None)))
    result = splittable.save_plan(splittable.PlanReq(
        product="PRODA", root_lot_id="R1", username="test", plans={"R1|1|KNOB_A": "SPLIT_B"},
    ))
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert result["ok"] is True
    basis = saved["plans"]["R1|1|KNOB_A"]["s0_basis"]
    assert basis["ppid"] == "SOP_A"
    assert saved["history"][0]["s0_basis"] == basis


def test_observe_and_resolve_as_of_with_prehistory_and_removal():
    history = {}
    split_s0_history.observe(history, {"S1": {"step_id": "S1", "ppid": "A"}}, "2026-09-01T01:00:00")
    split_s0_history.observe(history, {"S1": {"step_id": "S1", "ppid": "B"}}, "2026-09-02T01:00:00")
    split_s0_history.observe(history, {}, "2026-09-03T01:00:00")

    events = history["s1"]
    assert [event["ppid"] for event in events] == ["A", "B", ""]
    assert split_s0_history.resolve(events, "2026-08-31T23:00:00")["ppid"] == "A"
    assert split_s0_history.resolve(events, "2026-09-01T12:00:00")["ppid"] == "A"
    assert split_s0_history.resolve(events, "2026-09-02T12:00:00")["ppid"] == "B"
    assert split_s0_history.resolve(events, "2026-09-04T00:00:00")["ppid"] == ""
    assert split_s0_history.resolve(events, "2026-09-04T00:00:00")["basis"] == "as_of"
    assert split_s0_history.resolve(events, "not-a-time") is None


def test_timestamp_treats_naive_as_kst_and_utc_as_same_instant():
    assert split_s0_history.timestamp("2026-09-01T12:00:00") == split_s0_history.timestamp(
        "2026-09-01T03:00:00Z"
    )


def test_update_source_history_migrates_daily_and_same_day_revisions(tmp_path, monkeypatch):
    daily = tmp_path / "daily"
    source_dir = daily / "source" / "PRODA"
    source_dir.mkdir(parents=True)
    schema = {
        "snapshot_date": pl.Utf8, "snapshot_at": pl.Utf8, "product": pl.Utf8,
        "step_id": pl.Utf8, "por_ppid": pl.Utf8, "source_file": pl.Utf8,
    }
    def write(path, at, ppid):
        pl.DataFrame({
            "snapshot_date": ["2026-09-01"], "snapshot_at": [at], "product": ["PRODA"],
            "step_id": ["S1"], "por_ppid": [ppid], "source_file": ["f_step.csv"],
        }, schema=schema).write_parquet(path)
    write(source_dir / "2026-09-01.parquet", "2026-09-01T01:00:00+09:00", "A")
    revisions = source_dir / "revisions"
    revisions.mkdir()
    write(revisions / "2026-09-01_a.parquet", "2026-09-01T01:00:00+09:00", "A")
    write(revisions / "2026-09-01_b.parquet", "2026-09-01T02:00:00+09:00", "B")
    monkeypatch.setattr(splittable, "_S0_DAILY_DIR", daily)

    state = {}
    catalog = {"proda": {"file": "f_step.csv", "rows": {"s1": {"step_id": "S1", "ppid": "B"}}}}
    splittable._s0_update_source_history(state, catalog, dt.datetime(2026, 9, 2, 1))

    assert [event["ppid"] for event in state["sop_history"]["proda"]["s1"]] == ["A", "B"]
    assert state["sop_history_migrated"] is True


def test_knob_s0_as_of_uses_product_history(monkeypatch):
    state = {"sop_history": {"proda": {"step1": [
        {"step_id": "STEP1", "ppid": "A", "effective_at": "2026-09-01T01:00:00+09:00"},
        {"step_id": "STEP1", "ppid": "B", "effective_at": "2026-09-02T01:00:00+09:00"},
    ]}}}
    monkeypatch.setattr(splittable, "_s0_load_state", lambda readonly=True: state)
    monkeypatch.setattr(splittable, "_canonical_product_name", lambda value: str(value).removeprefix("ML_TABLE_"))
    monkeypatch.setattr(splittable, "_s0_resolution_context", lambda product: ({}, {}, {}))
    monkeypatch.setattr(splittable, "_s0_step_candidates", lambda product, knob, context=None: ["STEP1"])

    result = splittable._knob_s0_as_of("ML_TABLE_PRODA", {"KNOB_A": "2026-09-01T12:00:00+09:00", "KNOB_B": "2026-09-02T12:00:00Z"})
    assert result["KNOB_A"]["ppid"] == "A"
    assert result["KNOB_B"]["ppid"] == "B"


def test_knob_s0_for_root_uses_latest_plan_as_of_and_saved_basis(monkeypatch):
    history = {"sop_history": {"proda": {"step1": [
        {"step_id": "STEP1", "ppid": "A", "effective_at": "2026-09-01T01:00:00+09:00"},
        {"step_id": "STEP1", "ppid": "B", "effective_at": "2026-09-02T01:00:00+09:00"},
    ]}}}
    monkeypatch.setattr(splittable, "_s0_load_state", lambda readonly=True: history)
    monkeypatch.setattr(splittable, "_canonical_product_name", lambda value: str(value).removeprefix("ML_TABLE_"))
    monkeypatch.setattr(splittable, "_s0_resolution_context", lambda product: ({}, {}, {}))
    monkeypatch.setattr(splittable, "_s0_step_candidates", lambda product, knob, context=None: ["STEP1"])
    monkeypatch.setattr(splittable, "_knob_s0_for_product", lambda product, columns: {})
    monkeypatch.setattr(splittable, "_split_plan_cell_key", lambda cell: ("R1", "1", str(cell).split("|")[-1]))
    plans = {
        "R1|1|KNOB_A": {"value": "planned", "updated": "2026-09-02T12:00:00+09:00"},
        "R1|1|KNOB_B": {"value": "planned", "updated": "2026-09-03T12:00:00+09:00", "s0_basis": {"step_id": "STEP1", "ppid": "A"}},
    }
    monkeypatch.setattr(splittable, "_plan_entries_for_root", lambda product, root: plans)

    result = splittable._knob_s0_for_root("ML_TABLE_PRODA", "R1", ["KNOB_A", "KNOB_B"])
    assert result["KNOB_A"]["ppid"] == "B"
    assert result["KNOB_A"]["basis"] == "as_of"
    assert result["KNOB_B"]["ppid"] == "A"
    assert result["KNOB_B"]["basis"] == "saved_plan"


def test_knob_s0_for_root_without_plan_keeps_legacy_earliest_fallback(monkeypatch):
    monkeypatch.setattr(splittable, "_knob_s0_for_product", lambda product, columns: {"KNOB_A": {"ppid": "A", "basis": "first_received"}})
    monkeypatch.setattr(splittable, "_plan_entries_for_root", lambda product, root: {})
    assert splittable._knob_s0_for_root("PRODA", "R1", ["KNOB_A"]) == {
        "KNOB_A": {"ppid": "A", "basis": "first_received"}
    }
