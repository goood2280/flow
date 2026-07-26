from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core import semantic_source_catalog  # noqa: E402


def test_semantic_source_catalog_shape():
    sources = semantic_source_catalog.catalog_sources()

    assert list(sources) == ["rulebook", "step_matching", "split_base", "fab_db", "inline_db", "et_db"]
    assert sources["rulebook"]["path_patterns"] == ["FLOW_DB_ROOT/ppid_knob.csv"]
    assert sources["step_matching"]["path_patterns"] == ["FLOW_DB_ROOT/Vehicle_matching.csv"]
    assert sources["step_matching"]["fallback_path_patterns"] == ["FLOW_DB_ROOT/step_matching.csv"]
    assert sources["split_base"]["path_patterns"] == ["FLOW_DB_ROOT/ML_TABLE_<product>.parquet"]
    assert sources["split_base"]["related_question_ids"] == ["Q4"]
    assert sources["fab_db"]["docs_path"] == "docs/semantic/fab_db.md"
    assert sources["inline_db"]["docs_path"] == "docs/semantic/inline_db.md"
    assert sources["et_db"]["docs_path"] == "docs/semantic/et_db.md"

    roles = semantic_source_catalog.catalog_roles()
    assert "rulebook" in roles and "rulebook" in roles["rulebook"]
    assert "raw_export" in roles and "split_base" in roles["raw_export"]
    assert "current_location" in roles and "fab_db" in roles["current_location"]
    assert "measurement" in roles and {"inline_db", "et_db"}.issubset(set(roles["measurement"]))


def test_semantic_source_catalog_search_matches_terms_and_source_refs():
    prompt_ids = {
        row["source_id"]
        for row in semantic_source_catalog.source_catalog_matches("PPID knob step_id raw export 현재 위치")
    }
    assert {"rulebook", "step_matching", "split_base", "fab_db"}.issubset(prompt_ids)
    measure_ids = {
        row["source_id"]
        for row in semantic_source_catalog.source_catalog_matches("Inline CA BCD measurement target spec_low ET PCCB Chain")
    }
    assert {"inline_db", "et_db"}.issubset(measure_ids)

    split_ref = semantic_source_catalog.source_catalog_matches(
        "download raw rows",
        source_ref={"file": "ML_TABLE_PRODA.parquet"},
    )
    assert split_ref and split_ref[0]["source_id"] == "split_base"

    fab_priorities = semantic_source_catalog.search_priorities_for_term(
        "mystery term",
        source_ref={"root": "FAB", "product": "PRODA"},
    )
    assert any(row["source_id"] == "fab_db" for row in fab_priorities)


def test_semantic_source_catalog_disk_override_add_delete(monkeypatch, tmp_path):
    monkeypatch.setattr(semantic_source_catalog, "SOURCE_FILE", tmp_path / "semantic" / "source_catalog.json")
    monkeypatch.setattr(semantic_source_catalog, "CHANGES_FILE", tmp_path / "semantic" / "source_catalog.changes.jsonl")

    saved = semantic_source_catalog.save_source(
        {
            "id": "custom_inline",
            "title": "Custom Inline source",
            "role": "inline_db",
            "roles": ["inline_db", "custom_measurement"],
            "path_patterns": ["FLOW_DB_ROOT/custom_inline.parquet"],
            "search_terms": ["custom inline"],
        },
        actor="tester",
    )

    assert saved["id"] == "custom_inline"
    assert semantic_source_catalog.catalog_sources()["custom_inline"]["path_patterns"] == ["FLOW_DB_ROOT/custom_inline.parquet"]
    assert any(row["source_id"] == "custom_inline" for row in semantic_source_catalog.source_catalog_matches("custom inline trend"))

    deleted_default = semantic_source_catalog.delete_source("rulebook", actor="tester")

    assert deleted_default is True
    assert "rulebook" not in semantic_source_catalog.catalog_sources()
    assert "rulebook" not in {row["source_id"] for row in semantic_source_catalog.source_catalog_matches("ppid knob rulebook")}
