from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for p in (BACKEND, ROOT):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

from core import agent_semantic_service  # noqa: E402
from core import semantic_measure_catalog as catalog  # noqa: E402


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(catalog, "TERMS_FILE", tmp_path / "flow-data" / "semantic" / "measurement_terms.json")
    monkeypatch.setattr(catalog, "CHANGES_FILE", tmp_path / "flow-data" / "semantic" / "measurement_terms.changes.jsonl")
    monkeypatch.setattr(catalog, "CHANGE_MANAGEMENT_HISTORY", tmp_path / "flow-data" / "agent_unit_ai_sessions" / "change_management" / "history.jsonl")
    monkeypatch.setattr(catalog, "PATHS", SimpleNamespace(db_root=tmp_path / "DB"))


def test_semantic_measure_catalog_defaults_and_save_log(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    out = catalog.ensure_catalog(actor="tester")
    saved = catalog.save_term(
        {
            "id": "measure_inline_proda_ca_bcd",
            "term": "CA BCD",
            "aliases": ["CA BCD", "CA_BCD"],
            "source_type": "INLINE",
            "product": "PRODA",
            "step_id": "AA100001",
            "item_id": "CA_BCD",
            "default_agg": "avg",
            "target": 10,
            "spec_low": 8,
            "spec_high": 12,
            "evidence": [{"type": "meeting", "label": "Inline spec review", "source": "change-123"}],
        },
        actor="tester",
    )

    assert out["installed_defaults"] >= 2
    assert saved["target"] == 10
    assert catalog.CHANGES_FILE.exists()
    assert catalog.CHANGE_MANAGEMENT_HISTORY.exists()


def test_semantic_measure_catalog_delete_term(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    catalog.ensure_catalog(actor="tester")

    assert catalog.delete_term("measure_inline_proda_ca_bcd", actor="tester") is True
    out = catalog.load_catalog(ensure=False)

    assert all(row["id"] != "measure_inline_proda_ca_bcd" for row in out["terms"])
    assert catalog.delete_term("missing", actor="tester") is False


def test_semantic_measure_query_inline_avg_by_wafer(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    src_dir = tmp_path / "DB" / "1.RAWDATA_DB_INLINE" / "PRODA"
    src_dir.mkdir(parents=True)
    rows = []
    for wafer in range(1, 26):
        rows.append({"product": "PRODA", "root_lot_id": "A1001", "wafer_id": wafer, "step_id": "AA100001", "item_id": "CA_BCD", "value": float(wafer)})
        rows.append({"product": "PRODA", "root_lot_id": "A1001", "wafer_id": wafer, "step_id": "AA100001", "item_id": "CA_BCD", "value": float(wafer + 2)})
    pl.DataFrame(rows).write_parquet(src_dir / "part.parquet")
    catalog.save_term(
        {
            "id": "measure_inline_proda_ca_bcd",
            "term": "CA BCD",
            "aliases": ["CA BCD"],
            "source_type": "INLINE",
            "product": "PRODA",
            "step_id": "AA100001",
            "item_id": "CA_BCD",
            "default_agg": "avg",
            "target": 14.0,
            "spec_low": 1.0,
            "spec_high": 30.0,
        },
        actor="tester",
    )

    out = catalog.query_measurement("PRODA A1001 CA BCD 값 몇이야", max_rows=30)

    assert out and out["handled"] is True
    assert out["table"]["total"] == 25
    first = next(row for row in out["table"]["rows"] if row["wafer_id"] == "1")
    assert first["value_avg"] == 2.0
    assert first["target"] == 14.0
    assert out["term_resolution"][0]["updated_at"]


def test_flowi_semantic_measurement_handler_returns_table(monkeypatch, tmp_path):
    from routers import llm

    _isolate(monkeypatch, tmp_path)
    src_dir = tmp_path / "DB" / "1.RAWDATA_DB_INLINE" / "PRODA"
    src_dir.mkdir(parents=True)
    pl.DataFrame([
        {"product": "PRODA", "root_lot_id": "A1001", "wafer_id": 1, "step_id": "AA100001", "item_id": "CA_BCD", "value": 1.0},
        {"product": "PRODA", "root_lot_id": "A1001", "wafer_id": 1, "step_id": "AA100001", "item_id": "CA_BCD", "value": 3.0},
    ]).write_parquet(src_dir / "part.parquet")
    catalog.ensure_catalog(actor="tester")

    tool = llm._handle_semantic_measurement("PRODA A1001 CA BCD 값 몇이야", product="PRODA", max_rows=25)

    assert tool and tool["handled"] is True
    assert tool["intent"] == "semantic_measurement_lookup"
    assert tool["table"]["rows"][0]["value_avg"] == 2.0


def test_semantic_measurement_does_not_match_source_only_chart_prompt(monkeypatch, tmp_path):
    from routers import llm

    _isolate(monkeypatch, tmp_path)
    catalog.ensure_catalog(actor="tester")

    prompt = "Inline 15.0 M2 trend chart"

    assert catalog.match_terms(prompt, product="", limit=5) == []
    assert catalog.query_measurement(prompt, product="", max_rows=25) is None
    assert llm._handle_semantic_measurement(prompt, product="", max_rows=25) is None


def test_agent_semantic_service_includes_measurement_matches(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    catalog.ensure_catalog(actor="tester")

    frame = agent_semantic_service.resolve("PRODA A1001 CA BCD 값 몇이야", product="PRODA")

    assert frame["measurement_term_matches"]
    assert frame["measurement_term_matches"][0]["term"] == "CA BCD"
    assert any(row["source_id"] in {"inline_db", "split_base"} for row in frame["source_catalog_matches"])


def test_agent_semantic_measurements_endpoint(monkeypatch, tmp_path):
    from routers import agent

    _isolate(monkeypatch, tmp_path)

    class _Request:
        pass

    monkeypatch.setattr(agent, "current_user", lambda _request: {"username": "tester", "role": "admin"})

    out = agent.semantic_measurements(_Request())

    assert out["ok"] is True
    assert any(row["term"] == "CA BCD" for row in out["terms"])
    assert out["path"].endswith("measurement_terms.json")
