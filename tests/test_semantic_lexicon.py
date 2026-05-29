from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

import pytest

from app_v2.modules.semantic_lexicon import service, store


@pytest.fixture()
def lex_dir(tmp_path, monkeypatch):
    """Redirect lexicon storage to a tmp directory for each test."""
    base = tmp_path / "semantic"
    base.mkdir()
    monkeypatch.setattr(store, "LEXICON_DIR", base)
    monkeypatch.setattr(store, "ALIAS_FILE", base / "alias_groups.json")
    monkeypatch.setattr(store, "INTENT_FILE", base / "intent_hints.json")
    monkeypatch.setattr(store, "CHANGES_FILE", base / "changes.jsonl")
    return base


def test_load_returns_empty_when_file_missing(lex_dir):
    assert store.load_alias_groups() == {}
    assert store.load_intent_hints() == {}


def test_save_then_load_round_trip(lex_dir):
    store.save_alias_groups({"oxide": ["산화막", "GATE 산화막"]}, by="hol")
    store.save_intent_hints({"semantic_inspection": ["semantic_layer", "schema"]}, by="hol")

    assert store.load_alias_groups() == {"oxide": ["산화막", "GATE 산화막"]}
    assert store.load_intent_hints() == {"semantic_inspection": ["semantic_layer", "schema"]}


def test_effective_merges_seed_and_disk(lex_dir):
    seed = {
        "wafer_id": ["wafer", "웨이퍼"],
        "knob": ["knob", "split"],
    }
    # Seed-only when disk is empty
    assert service.effective_alias_groups(seed) == seed

    # Disk override wins for an existing key
    store.save_alias_groups({"wafer_id": ["wafer", "wf", "웨이퍼", "shot"]}, by="hol")
    out = service.effective_alias_groups(seed)
    assert out["wafer_id"] == ["wafer", "wf", "웨이퍼", "shot"]
    # Disk-only keys are added
    assert out["knob"] == seed["knob"]

    store.save_alias_groups({"wafer_id": ["wafer"], "oxide": ["산화막"]}, by="hol")
    merged = service.effective_alias_groups(seed)
    assert merged["wafer_id"] == ["wafer"]
    assert merged["oxide"] == ["산화막"]
    assert merged["knob"] == seed["knob"]


def test_upsert_alias_group_persists_and_audits(lex_dir):
    seed = {"knob": ["knob", "split"]}
    service.upsert_alias_group(
        "knob",
        ["knob", "split", "ppid", "분기"],
        by="hol",
        seed=seed,
        meta={"semantic_class": "rulebook", "normalization": {"case": "upper"}, "value_domain": ["ppid"]},
    )

    disk = store.load_alias_groups()
    assert disk["knob"] == ["knob", "split", "ppid", "분기"]
    entries = store.load_alias_group_entries()
    assert entries["knob"]["semantic_class"] == "rulebook"
    assert service.effective_alias_group_meta(seed)["knob"]["value_domain"] == ["ppid"]

    changes = store.list_changes()
    assert any(c.get("scope") == "alias_groups" and c.get("key") == "knob" for c in changes)


def test_delete_alias_group_only_removes_disk_override(lex_dir):
    seed = {"knob": ["knob", "split"]}
    # No disk override yet — delete returns False
    assert service.delete_alias_group("knob", by="hol") is False

    service.upsert_alias_group("knob", ["knob", "분기"], by="hol", seed=seed)
    assert service.delete_alias_group("knob", by="hol") is True
    assert "knob" not in store.load_alias_groups()
    # Effective view falls back to seed after delete
    assert service.effective_alias_groups(seed) == seed


def test_append_change_round_trip_with_list_changes(lex_dir):
    store.append_change(scope="alias_groups", key="oxide", before=[], after=["산화막"], by="hol")
    store.append_change(scope="intent_hints", key="semantic_inspection", before=[], after=["wiki"], by="hol")

    changes = store.list_changes(limit=10)
    assert len(changes) == 2
    scopes = {c.get("scope") for c in changes}
    assert scopes == {"alias_groups", "intent_hints"}
