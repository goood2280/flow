from __future__ import annotations

import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core import semiconductor_knowledge as semi  # noqa: E402


def test_item_resolution_uses_item_master_metadata():
    out = semi.resolve_item_semantics(["DIBL"])

    assert out["resolved"][0]["canonical_item_id"] == "DIBL"
    item = out["resolved"][0]["item"]
    assert item["unit"] == "mV/V"
    assert item["source_type"] == "ET"
    assert "Id-Vg" in item["measurement_method"]


def test_ca_rs_without_context_is_ambiguous():
    out = semi.resolve_item_semantics(["CA_RS"])

    row = out["resolved"][0]
    assert row["status"] == "ambiguous"
    assert row["canonical_item_id"] == ""
    assert {c["canonical_item_id"] for c in row["candidates"]} >= {"CA_RS", "CA_RC_KELVIN", "CA_CHAIN_R"}
    assert "raw name alone" in row["ambiguity"]


def test_ca_rs_contact_structure_maps_to_contact_candidate():
    out = semi.resolve_item_semantics([
        {"raw_item": "CA_RS", "unit": "ohm", "test_structure": "Kelvin"}
    ])

    row = out["resolved"][0]
    assert row["status"] == "resolved_with_context"
    assert row["canonical_item_id"] == "CA_RC_KELVIN"
    assert "contact resistance" in row["item"]["meaning"]


def test_ca_rs_sheet_context_maps_to_sheet_resistance():
    out = semi.resolve_item_semantics([
        {"raw_item": "CA_RS", "unit": "ohm/sq", "measurement_method": "Rsheet"}
    ])

    row = out["resolved"][0]
    assert row["status"] == "resolved"
    assert row["canonical_item_id"] == "CA_RS"
    assert row["item"]["test_structure"] == "sheet_resistance"


def test_diagnosis_output_schema_is_deterministic_and_guarded():
    report = semi.run_diagnosis(
        "GAA nFET short Lg에서 DIBL과 SS가 증가했고 CA_RS도 올랐어. 원인 후보와 확인 차트 보여줘.",
        product="PRODA",
        save=False,
    )

    for key in [
        "diagnosis_summary",
        "observed_symptoms",
        "ranked_hypotheses",
        "recommended_action_plan",
        "charts",
        "evidence",
        "missing_data",
        "do_not_conclude",
    ]:
        assert key in report
    assert report["mode"] == "mock_llm_deterministic"
    assert report["ranked_hypotheses"]
    assert report["eval"]["passed"] is True
    assert any("CA_RS" in item for item in report["do_not_conclude"])
    assert any(step["stage"] == "graph_causal_db" for step in report["pipeline"])


def test_semiconductor_storage_manifest_documents_runtime_and_seed_paths():
    manifest = semi.storage_manifest()

    assert manifest["code_seed"]["python_module"] == "backend/core/semiconductor_knowledge.py"
    assert manifest["code_seed"]["default_rca_seed"].endswith("semiconductor_rca_seed_knowledge.json")
    assert manifest["default_seed_pack"]["card_count"] >= 8
    assert "flow-data" in manifest["runtime_data"]["description"]
    assert "custom_knowledge" in manifest["runtime_data"]


def test_default_seed_knowledge_pack_extends_rca_cards_and_cases():
    cards = semi.seed_knowledge_cards()
    cases = semi.seed_historical_cases()
    edges = semi.seed_causal_edges()

    assert any(card["id"] == "SEED_KC_GAA_DIBL_SS_SHORT_LG" for card in cards)
    assert any("GAA_CHANNEL_RELEASE" in card.get("module_tags", []) for card in cards)
    assert any(case["case_id"] == "SEED_CASE_GAA_DIBL_SS" for case in cases)
    assert any(edge["source"] == "GAA_CHANNEL_RELEASE" and edge["target"] == "DIBL" for edge in edges)


def test_seed_knowledge_is_used_by_search_graph_and_cases():
    cards = semi.search_knowledge_cards("GAA short Lg DIBL SS 증가", limit=5)["cards"]
    graph = semi.traverse_causal_graph(["GAA_CHANNEL_RELEASE"], max_depth=1)
    cases = semi.find_similar_cases({"items": ["DIBL", "SS"], "terms": ["short_lg"], "modules": ["GAA_CHANNEL_RELEASE"]})

    assert any(card["id"] == "SEED_KC_GAA_DIBL_SS_SHORT_LG" for card in cards)
    assert any(path["edge"]["target"] == "DIBL" for path in graph["paths"])
    assert any(case["case_id"] == "SEED_CASE_GAA_DIBL_SS" for case in cases["cases"])


def test_rag_update_requires_marker_for_non_admin(tmp_path, monkeypatch):
    monkeypatch.setattr(semi, "SEMICONDUCTOR_DIR", tmp_path)
    monkeypatch.setattr(semi, "CUSTOM_KNOWLEDGE_FILE", tmp_path / "custom_knowledge.jsonl")

    try:
        semi.structure_rag_update_from_prompt(
            "DIBL SS RCA 지식 저장",
            username="u1",
            role="user",
            require_marker=True,
        )
    except ValueError as e:
        assert "[flow-i update]" in str(e)
    else:
        raise AssertionError("non-admin RAG update without marker should fail")

    out = semi.structure_rag_update_from_prompt(
        "[flow-i update] DIBL SS는 GAA short Lg electrostatic RCA 후보",
        username="u1",
        role="user",
        require_marker=True,
    )
    assert out["ok"] is True
    assert out["saved"]["visibility"] == "private"
    assert (tmp_path / "custom_knowledge.jsonl").exists()


def test_reformatter_alias_proposal_keeps_teg_discriminators():
    out = semi.reformatter_alias_proposal_from_prompt(
        "PC-CB-M1 Chain item은 14x14, 13x13, 12x12 DOE TEG가 다르고 gate pitch와 Cell height를 구분해야 해",
        product="PRODA",
    )

    assert out["ok"] is True
    assert out["rules"]
    assert "geometry_dimension" in out["discriminators"]
    assert "pitch" in out["discriminators"]
    assert "cell_height" in out["discriminators"]
    assert any(r["item_id"] == "PC-CB-M1" for r in out["rules"])


def test_teg_layout_proposal_from_rows_normalizes_coordinates():
    out = semi.teg_layout_proposal_from_rows(
        "PRODA",
        rows=[
            {"name": "TEG_TOP", "x": 13.6, "y": 29.6, "width": 1.2, "height": 0.6},
            {"name": "TEG_RIGHT", "x": 27.6, "y": 14.6},
        ],
    )

    assert out["ok"] is True
    assert len(out["teg_definitions"]) == 2
    assert out["teg_definitions"][0]["id"] == "TEG_TOP"
    assert out["teg_definitions"][0]["dx_mm"] == 13.6


def test_query_measurements_prefers_actual_dataset_when_available(monkeypatch):
    df = pl.DataFrame([
        {"product": "PRODA", "root_lot_id": "A10001", "wafer_id": "01", "item_id": "DIBL", "value": 91.2, "date": "2026-04-01"},
        {"product": "PRODA", "root_lot_id": "A10001", "wafer_id": "02", "item_id": "SS", "value": 77.1, "date": "2026-04-01"},
    ])
    monkeypatch.setattr(semi, "_actual_source_candidates", lambda source_type, filters: [{"source_type": "base_file", "file": "ET_PRODA.parquet"}])
    monkeypatch.setattr(semi, "_read_dataset_sample", lambda source, max_files=8, limit=5000: df)

    out = semi.query_measurements("ET", {"product": "PRODA", "canonical_item_ids": ["DIBL"]})

    assert out["mode"] == "actual_parquet_sample"
    assert out["rows"][0]["canonical_item_id"] == "DIBL"
    assert out["rows"][0]["value"] == 91.2
    assert out["sources"][0]["file"] == "ET_PRODA.parquet"


def test_reformatter_proposal_can_use_dataset_item_column(monkeypatch):
    monkeypatch.setattr(semi, "dataset_sample", lambda source, limit=500: {
        "ok": True,
        "columns": ["product", "item_id", "value"],
        "rows": [
            {"product": "PRODA", "item_id": "PC-CB-M1-14x14-CHAIN", "value": 1.0},
            {"product": "PRODA", "item_id": "PC-CB-M1-13x13-CHAIN", "value": 2.0},
        ],
        "source": {"source_type": "base_file", "file": "EDS_PRODA.parquet"},
        "mode": "actual_dataset_sample",
    })

    out = semi.reformatter_alias_proposal_from_dataset(
        "PRODA",
        {"file": "EDS_PRODA.parquet"},
        "gate pitch와 cell height 구분",
    )

    assert out["ok"] is True
    assert len(out["rules"]) == 2
    assert out["dataset"]["source"]["file"] == "EDS_PRODA.parquet"
    assert {r["item_id"] for r in out["rules"]} == {"PC-CB-M1-14x14-CHAIN", "PC-CB-M1-13x13-CHAIN"}


def test_dataset_profile_detects_long_et_schema(monkeypatch):
    monkeypatch.setattr(semi, "dataset_sample", lambda source, limit=300: {
        "ok": True,
        "columns": ["product", "root_lot_id", "wafer_id", "item_id", "value", "step_id"],
        "rows": [
            {"product": "PRODA", "root_lot_id": "A10001", "wafer_id": "01", "item_id": "DIBL", "value": 91.2, "step_id": "ET01"},
            {"product": "PRODA", "root_lot_id": "A10001", "wafer_id": "02", "item_id": "SS", "value": 77.1, "step_id": "ET01"},
        ],
        "source": {"source_type": "base_file", "file": "ET_PRODA.parquet", "product": "PRODA"},
        "mode": "actual_dataset_sample",
    })

    out = semi.dataset_profile({"file": "ET_PRODA.parquet", "product": "PRODA"})

    assert out["ok"] is True
    assert out["suggested_source_type"] == "ET"
    assert out["metric_shape"] == "long"
    assert out["grain"] == "lot_wf"
    assert "root_lot_id" in out["join_keys"]
    assert out["unique_items"] == ["DIBL", "SS"]
    assert out["column_roles"]["item"] == "item_id"


def test_dataset_profile_detects_wide_eds_schema(monkeypatch):
    monkeypatch.setattr(semi, "dataset_sample", lambda source, limit=300: {
        "ok": True,
        "columns": ["root_lot_id", "wafer_id", "die_x", "die_y", "bin", "ION", "IOFF"],
        "rows": [
            {"root_lot_id": "A10001", "wafer_id": "01", "die_x": 10, "die_y": 20, "bin": 1, "ION": 1.3, "IOFF": 0.02},
        ],
        "source": {"source_type": "base_file", "file": "EDS_PRODA.parquet"},
        "mode": "actual_dataset_sample",
    })

    out = semi.dataset_profile({"file": "EDS_PRODA.parquet"})

    assert out["ok"] is True
    assert out["suggested_source_type"] == "EDS"
    assert out["metric_shape"] == "wide"
    assert out["grain"] == "die"
    assert {"die_x", "die_y", "bin"} <= set(out["join_keys"])
    assert {"ION", "IOFF"} <= set(out["metric_columns"])
