import datetime
import json
from pathlib import Path

import polars as pl

from core import shared_lease
from routers import splittable


def test_f_step_catalog_maps_product_step_to_current_recipe(tmp_path, monkeypatch):
    db_root = tmp_path / "Fab"
    confidential = db_root / "confidential"
    confidential.mkdir(parents=True)
    source_path = confidential / "f_step.parquet"
    pl.DataFrame({
        "product_id": ["PRODA", "PRODA", "PRODA", "PRODB"],
        "step_id": ["AA100", "AA200", "AA100", "BB100"],
        "recipe_id": ["PP_OLD", "PP_TWO", "PP_CURRENT", "PP_B"],
    }).write_parquet(source_path)
    monkeypatch.setattr(splittable, "_db_base", lambda: db_root)
    monkeypatch.setattr(splittable, "_base_root", lambda: tmp_path / "Base")
    monkeypatch.setattr(splittable, "_S0_CATALOG_CACHE", None)

    catalog = splittable._s0_sop_catalog()

    assert catalog["proda"]["step_order"] == ["AA100", "AA200"]
    assert catalog["proda"]["rows"]["aa100"]["ppid"] == "PP_CURRENT"
    assert catalog["prodb"]["rows"]["bb100"]["ppid"] == "PP_B"


def test_f_step_without_product_column_is_a_global_step_recipe_map(tmp_path):
    path = tmp_path / "f_step.parquet"
    pl.DataFrame({"step_id": ["AA100"], "recipe_id": ["PP_STD"]}).write_parquet(path)

    catalog = splittable._s0_read_f_step_file(path)

    assert catalog["*"]["step_order"] == ["AA100"]
    assert splittable._s0_source_for_product(catalog, "ANY_PRODUCT")["rows"]["aa100"]["ppid"] == "PP_STD"


def test_daily_s0_snapshot_is_append_only_and_archives_product_parquet(tmp_path, monkeypatch):
    confidential = tmp_path / "Fab" / "confidential"
    confidential.mkdir(parents=True)
    source_path = confidential / "f_step.parquet"
    pl.DataFrame({"product": ["PRODA"], "step_id": ["AA100"], "recipe_id": ["PP_A"]}).write_parquet(source_path)
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
            "step_order": ["AA100"],
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
    archived = daily_dir / "source" / "PRODA" / "2026-09-03.parquet"
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
    assert (daily_dir / "source" / "PRODA" / "2026-09-04.parquet").is_file()


def test_legacy_csv_s0_registry_is_reset_for_f_step_source(tmp_path, monkeypatch):
    state_file = tmp_path / "knob_s0_registry.json"
    state_file.write_text(json.dumps({
        "schema_version": 1,
        "products": {"PRODA": {"KNOB_A": {"ppid": "CSV_OLD"}}},
    }), encoding="utf-8")
    monkeypatch.setattr(splittable, "_S0_STATE_FILE", state_file)

    state = splittable._s0_load_state()

    assert state["schema_version"] == 2
    assert state["products"] == {}


def test_split_exports_put_snapshot_por_in_s0_even_when_not_first_observed():
    value_maps = {"KNOB_A": ({0: "PP_X", 1: "PP_STD"}, {})}
    rows = splittable._build_split_check_export_rows(
        ["KNOB_A"], 2, value_maps, s0_by_param={"KNOB_A": "PP_STD"}
    )
    assert rows[0][:3] == ["KNOB_A", "PP_STD", "S0"]
    assert rows[1][:3] == ["KNOB_A", "PP_X", "S1"]


def test_credential_sop_catalog_loads_csv_and_falls_back_for_missing_product(tmp_path, monkeypatch):
    credential_dir = tmp_path / "credential"
    credential_dir.mkdir(parents=True, exist_ok=True)
    sop_file = credential_dir / "PRODX_sop.csv"
    sop_file.write_text(
        "step_id,current_por_ppid,is_por\n"
        "S10,PP_PRODX_S0,Y\n",
        encoding="utf-8-sig",
    )
    monkeypatch.setattr(splittable, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_db_base", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_S0_CATALOG_CACHE", None)

    catalog = splittable._s0_sop_catalog()
    assert "prodx" in catalog
    assert catalog["prodx"]["rows"]["s10"]["ppid"] == "PP_PRODX_S0"

    # Missing product returns empty source and empty s0
    assert splittable._s0_source_for_product(catalog, "UNKNOWN_PROD") == {}
    assert splittable._knob_s0_for_product("UNKNOWN_PROD") == {}


def test_step_order_context_ranks_feol_before_mol_and_test(monkeypatch, tmp_path):
    matching_csv = tmp_path / "step_matching.csv"
    matching_csv.write_text(
        "product,step_id,function_step\n"
        "PRODZ,AA100600,FINAL_INSPECTION\n"
        "PRODZ,AA100100,CONTACT_LITHO\n"
        "PRODZ,AA100210,STI_FORMATION\n"
        "PRODZ,AA100220,WELL_IMPLANT\n",
        encoding="utf-8-sig",
    )
    monkeypatch.setattr(splittable, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_db_base", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_load_knob_step_matching_rows", lambda *args: [
        {"product": "PRODZ", "step_id": "AA100600", "function_step": "FINAL_INSPECTION"},
        {"product": "PRODZ", "step_id": "AA100100", "function_step": "CONTACT_LITHO"},
        {"product": "PRODZ", "step_id": "AA100210", "function_step": "STI_FORMATION"},
        {"product": "PRODZ", "step_id": "AA100220", "function_step": "WELL_IMPLANT"},
    ])
    monkeypatch.setattr(splittable, "_s0_sop_catalog", lambda: {})

    ctx = splittable._split_step_order_context("PRODZ")
    # STI (stage 1) < WELL (stage 2) < CONTACT (stage 10) < FINAL_INSPECTION (stage 22)
    assert ctx["seq_rank"]["AA100210"] < ctx["seq_rank"]["AA100220"]
    assert ctx["seq_rank"]["AA100220"] < ctx["seq_rank"]["AA100100"]
    assert ctx["seq_rank"]["AA100100"] < ctx["seq_rank"]["AA100600"]

