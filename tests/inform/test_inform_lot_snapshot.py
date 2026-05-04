from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

import polars as pl

from backend.routers import informs  # noqa: E402
from routers import splittable  # noqa: E402


def test_create_inform_preserves_split_table_root_and_fab_lots(tmp_path, monkeypatch):
    informs_file = tmp_path / "informs.json"
    monkeypatch.setattr(informs, "INFORMS_FILE", informs_file)
    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "admin", "username": "tester"})
    monkeypatch.setattr(informs, "_resolve_fab_lot_snapshot", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(informs, "_audit", lambda *_args, **_kwargs: None)

    req = informs.InformCreate(**{
        "lot_id": "LOT029AA",
        "product": "PRODA",
        "module": "ET",
        "reason": "PEMS",
        "text": "check split lot",
        "embed_table": {
            "source": "SplitTable/PRODA @ LOT029AA",
            "columns": ["parameter"],
            "rows": [],
            "st_view": {
                "root_lot_id": "LOT029AA",
                "header_groups": [
                    {"label": "LOT029AA.1", "span": 2},
                    {"label": "LOT029AA.2", "span": 1},
                ],
                "wafer_fab_list": ["LOT029AA.1", "LOT029AA.2"],
            },
            "st_scope": {},
        },
    })
    resp = informs.create_inform(req, object())

    created = resp["inform"]
    assert created["root_lot_id"] == "LOT029AA"
    assert created["fab_lot_id_at_save"] == "LOT029AA.1, LOT029AA.2"

    by_lot = informs.by_lot(object(), lot_id="LOT029AA.1")
    assert by_lot["count"] == 1


def test_inform_wafer_queries_normalize_saved_wafer_forms(monkeypatch):
    items = [
        {"id": "a", "wafer_id": "01", "lot_id": "A1000", "product": "PRODA", "created_at": "2026-04-27T10:00:00"},
        {"id": "b", "wafer_id": "W1", "lot_id": "A1000", "product": "PRODA", "created_at": "2026-04-27T11:00:00"},
        {"id": "c", "wafer_id": "2, 3", "wafer_ids_at_save": ["2", "3"], "lot_id": "A1001", "product": "PRODA", "created_at": "2026-04-27T12:00:00"},
    ]
    monkeypatch.setattr(informs, "_load_upgraded", lambda: items)

    by_wf = informs.list_by_wafer("1")
    sidebar = informs._sidebar_payload(items, {"username": "admin", "role": "admin"}, {"__all__"})

    assert [row["id"] for row in by_wf["informs"]] == ["a", "b"]
    assert [row["id"] for row in informs.list_by_wafer("3")["informs"]] == ["c"]
    wafers = {row["wafer_key"]: row for row in sidebar["wafers"]}
    assert wafers["1"]["count"] == 2
    assert set(wafers) >= {"1", "2", "3"}


def test_by_lot_returns_module_progress_summary(monkeypatch):
    items = [
        {"id": "a", "root_lot_id": "A1000", "lot_id": "A1000", "wafer_id": "1", "module": "ET", "flow_status": "completed", "created_at": "2026-04-27T10:00:00", "status_history": [{"status": "completed", "at": "2026-04-27T11:00:00"}]},
        {"id": "b", "root_lot_id": "A1000", "lot_id": "A1000", "wafer_id": "2", "module": "FAB", "flow_status": "received", "created_at": "2026-04-27T12:00:00"},
    ]
    monkeypatch.setattr(informs, "_load_upgraded", lambda: items)
    monkeypatch.setattr(informs, "_load_config", lambda: {"modules": ["ET", "FAB", "PC"]})
    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "admin", "username": "tester"})

    out = informs.by_lot(object(), lot_id="A1000")
    summary = out["module_summary"]

    assert out["count"] == 2
    assert summary["active_modules"] == 2
    assert summary["completed_modules"] == 1
    assert summary["pending_modules"] == ["FAB"]
    assert summary["missing_modules"] == ["PC"]
    assert {row["module"]: row["status"] for row in summary["modules"]} == {
        "ET": "apply_confirmed",
        "FAB": "registered",
        "PC": "missing",
    }


def test_create_inform_resolves_root_only_fab_lot_from_splittable_cache(tmp_path, monkeypatch):
    informs_file = tmp_path / "informs.json"
    monkeypatch.setattr(informs, "INFORMS_FILE", informs_file)
    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "admin", "username": "tester"})
    monkeypatch.setattr(informs, "_audit", lambda *_args, **_kwargs: None)

    pl.DataFrame({
        "root_lot_id": ["R9600"],
        "wafer_id": [1],
        "KNOB_ALPHA": ["ON"],
    }).write_parquet(tmp_path / "ML_TABLE_INF.parquet")
    fab_root = tmp_path / "1.RAWDATA_DB_FAB" / "INF" / "date=20240423"
    fab_root.mkdir(parents=True)
    pl.DataFrame({
        "root_lot_id": ["R9600", "R9600"],
        "lot_id": ["F9600_OLD", "F9600_NEW"],
        "wafer_id": [1, 1],
        "tkout_time": ["2024-04-23T08:00:00", "2024-04-23T10:00:00"],
    }).write_parquet(fab_root / "part_0.parquet")

    cache_dir = tmp_path / "flow-data" / "splittable" / "match_cache"
    source_cfg = tmp_path / "flow-data" / "splittable" / "source_config.json"
    monkeypatch.setattr(splittable, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_db_base", lambda: tmp_path)
    monkeypatch.setattr(splittable, "MATCH_CACHE_DIR", cache_dir)
    monkeypatch.setattr(splittable, "SOURCE_CFG", source_cfg)
    splittable._LOT_LOOKUP_CACHE.clear()
    splittable._RGLOB_CACHE.clear()
    splittable._DB_ROOTS_CACHE.clear()
    splittable.refresh_match_cache(product="ML_TABLE_INF", force=True)

    req = informs.InformCreate(**{
        "lot_id": "R9600",
        "product": "INF",
        "module": "ET",
        "reason": "PEMS",
        "text": "cache snapshot",
    })
    created = informs.create_inform(req, object())["inform"]

    assert created["root_lot_id"] == "R9600"
    assert created["wafer_id"] == "1"
    assert created["wafer_ids_at_save"] == ["1"]
    assert created["lot_identity_snapshot"]["fab_lot_id"] == "F9600_NEW"
    assert created["lot_identity_snapshot"]["root_lot_id"] == "R9600"
    assert created["fab_lot_id_at_save"] == "F9600_NEW"


def test_create_inform_snapshots_current_fab_lot_root_and_wafers(tmp_path, monkeypatch):
    informs_file = tmp_path / "informs.json"
    monkeypatch.setattr(informs, "INFORMS_FILE", informs_file)
    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "admin", "username": "tester"})
    monkeypatch.setattr(informs, "_audit", lambda *_args, **_kwargs: None)

    snapshots = iter([
        {"root_lot_id": "R9700", "root_lot_ids": ["R9700"], "wafer_id": "1", "wafer_ids": ["1"], "fab_lot_id": "F9700A.1", "source": "test"},
        {"root_lot_id": "R9700", "root_lot_ids": ["R9700"], "wafer_id": "2", "wafer_ids": ["2"], "fab_lot_id": "F9700A.1", "source": "test"},
    ])

    def fake_snapshot(product, lot_id, requested_wafer_id, embed, explicit_fab_lot_id=""):
        return dict(next(snapshots))

    monkeypatch.setattr(informs, "_resolve_lot_identity_snapshot", fake_snapshot)

    gate = informs.create_inform(informs.InformCreate(
        lot_id="F9700A.1",
        product="PRODA",
        module="GATE",
        text="gate snapshot",
        fab_lot_id_at_save="F9700A.1",
    ), object())["inform"]
    sti = informs.create_inform(informs.InformCreate(
        lot_id="F9700A.1",
        product="PRODA",
        module="STI",
        text="sti snapshot",
        fab_lot_id_at_save="F9700A.1",
    ), object())["inform"]

    assert gate["fab_lot_id_at_save"] == sti["fab_lot_id_at_save"] == "F9700A.1"
    assert gate["wafer_id"] == "1"
    assert sti["wafer_id"] == "2"

    rows = informs.by_lot(object(), lot_id="F9700A.1")["informs"]
    assert [row["module"] for row in rows] == ["GATE", "STI"]

    matrix = informs.lot_matrix(object(), product="PRODA", days=3650, search="F9700A.1")
    lot = matrix["products"][0]["lots"][0]
    assert lot["fab_lot_id"] == "F9700A.1"
    assert set(lot["modules"]) >= {"GATE", "STI"}
