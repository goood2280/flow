from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core import flowi_multisource  # noqa: E402
from core import knowledge_vault as kv  # noqa: E402


def _install_multisource_fixture(tmp_path, monkeypatch, *, status: str = "confirmed"):
    db_root = tmp_path / "db"
    et_dir = db_root / "1.RAWDATA_DB_ET" / "PRODA"
    et_dir.mkdir(parents=True)
    pl.DataFrame({
        "product": ["PRODA", "PRODA"],
        "root_lot_id": ["A1000", "A1001"],
        "wafer_id": [1, 2],
        "LKG": [0.5, 0.9],
    }).write_parquet(et_dir / "part.parquet")
    pl.DataFrame({
        "ROOT_LOT_ID": ["A1000", "A1002"],
        "WAFER_ID": [1, 3],
        "KNOB_A": ["K1", "K3"],
    }).write_parquet(db_root / "ML_TABLE_PRODA.parquet")
    relation_file = tmp_path / "flow-data" / "schema_relations.json"
    relation_file.parent.mkdir(parents=True)
    relation_file.write_text(json.dumps({
        "relations": [
            {
                "left_source_id": "db_1.RAWDATA_DB_ET_PRODA",
                "left_label": "ET PRODA",
                "left_source_type": "db",
                "left_column": "root_lot_id",
                "right_source_id": "file_base_root_ML_TABLE_PRODA.parquet",
                "right_label": "ML_TABLE_PRODA",
                "right_source_type": "file",
                "right_column": "ROOT_LOT_ID",
                "canonical_key": "root_lot_id",
                "relation_type": "join_key",
                "relation_id": "rel_root",
                "status": status,
            },
            {
                "left_source_id": "db_1.RAWDATA_DB_ET_PRODA",
                "left_label": "ET PRODA",
                "left_source_type": "db",
                "left_column": "wafer_id",
                "right_source_id": "file_base_root_ML_TABLE_PRODA.parquet",
                "right_label": "ML_TABLE_PRODA",
                "right_source_type": "file",
                "right_column": "WAFER_ID",
                "canonical_key": "wafer_id",
                "relation_type": "join_key",
                "relation_id": "rel_wafer",
                "status": status,
            },
        ],
        "column_catalog": [
            {"relation_id": "ET_PRODA", "column": "LKG", "canonical_alias": "LKG", "raw_names": ["LKG"], "dtype": "float"},
            {"relation_id": "ET_PRODA", "column": "root_lot_id", "canonical_alias": "root_lot_id", "raw_names": ["root_lot_id"], "dtype": "string"},
            {"relation_id": "ET_PRODA", "column": "wafer_id", "canonical_alias": "wafer_id", "raw_names": ["wafer_id"], "dtype": "int"},
            {"relation_id": "ML_TABLE_PRODA", "column": "KNOB_A", "canonical_alias": "KNOB_A", "raw_names": ["KNOB_A"], "dtype": "string"},
            {"relation_id": "ML_TABLE_PRODA", "column": "root_lot_id", "canonical_alias": "root_lot_id", "raw_names": ["ROOT_LOT_ID"], "dtype": "string"},
            {"relation_id": "ML_TABLE_PRODA", "column": "wafer_id", "canonical_alias": "wafer_id", "raw_names": ["WAFER_ID"], "dtype": "int"},
        ],
    }), encoding="utf-8")
    monkeypatch.setattr(flowi_multisource, "SCHEMA_RELATION_FILE", relation_file)
    monkeypatch.setattr(kv, "SCHEMA_RELATION_FILE", relation_file)
    monkeypatch.setattr(flowi_multisource, "PATHS", SimpleNamespace(db_root=db_root, base_root=db_root, data_root=tmp_path / "flow-data"))
    return relation_file


def test_flowi_multisource_executes_confirmed_relation_join(tmp_path, monkeypatch):
    _install_multisource_fixture(tmp_path, monkeypatch)

    out = flowi_multisource.execute_multisource_request(
        "ET PRODA와 ML_TABLE_PRODA 조인해서 A1000 #1 LKG KNOB_A 보여줘",
        product="PRODA",
        max_rows=10,
    )

    assert out["handled"] is True
    assert out["ok"] is True
    assert out["source_ids"] == ["db_1.RAWDATA_DB_ET_PRODA", "file_base_root_ML_TABLE_PRODA.parquet"]
    assert out["relation_ids"] == ["rel_root", "rel_wafer"]
    assert out["join_keys"] == ["root_lot_id", "wafer_id"]
    assert out["row_count"] == 1
    row = out["sample_rows"][0]
    assert row["ET PRODA.LKG"] == 0.5
    assert row["ML_TABLE_PRODA.KNOB_A"] == "K1"


def test_flowi_multisource_blocks_unconfirmed_relation(tmp_path, monkeypatch):
    _install_multisource_fixture(tmp_path, monkeypatch, status="preview")

    out = flowi_multisource.execute_multisource_request(
        "ET PRODA와 ML_TABLE_PRODA 조인해서 A1000 #1 LKG KNOB_A 보여줘",
        product="PRODA",
        max_rows=10,
    )

    assert out["handled"] is True
    assert out["blocked"] is True
    assert out["row_count"] == 0
    assert any("관계 확인 필요" in w for w in out["warnings"])


def test_flowi_multisource_single_file_preview_uses_real_source(tmp_path, monkeypatch):
    _install_multisource_fixture(tmp_path, monkeypatch)

    out = flowi_multisource.execute_multisource_request(
        "ML_TABLE_PRODA 파일에서 KNOB_A 보여줘",
        product="PRODA",
        max_rows=10,
    )

    assert out["handled"] is True
    assert out["ok"] is True
    assert out["relation_ids"] == []
    assert out["row_count"] == 2
    assert out["sample_rows"][0]["ML_TABLE_PRODA.KNOB_A"] == "K1"


def test_flowi_multisource_chart_preserves_source_evidence(tmp_path, monkeypatch):
    _install_multisource_fixture(tmp_path, monkeypatch)

    out = flowi_multisource.execute_multisource_request(
        "ET PRODA와 ML_TABLE_PRODA confirmed relation으로 scatter 차트 그려줘",
        product="PRODA",
        max_rows=10,
    )

    assert out["handled"] is True
    assert out["chart_config"]["editable"] is True
    evidence = out["chart_config"]["source_evidence"]
    assert evidence["source_ids"]
    assert evidence["relation_ids"] == ["rel_root", "rel_wafer"]
    assert out["chart_result"]["points"]
