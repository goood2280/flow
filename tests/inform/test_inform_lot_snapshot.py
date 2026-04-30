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

    by_lot = informs.by_lot(object(), lot_id="LOT029AA")
    assert by_lot["count"] == 1


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
    assert created["wafer_id"] == "R9600"
    assert created["fab_lot_id_at_save"] == "F9600_NEW"
