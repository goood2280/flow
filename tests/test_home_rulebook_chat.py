"""Home matching CSV changes require a reviewed, same-user approval turn."""
from __future__ import annotations

import csv
import json
from types import SimpleNamespace

import pytest

from core import data_chat_rulebook as rulebook


def _write(path, columns, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _rows(path):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    paths = SimpleNamespace(db_root=tmp_path / "db", data_root=tmp_path / "data")
    paths.db_root.mkdir()
    paths.data_root.mkdir()
    monkeypatch.setattr(rulebook, "PATHS", paths)
    from core import audit, matching_cache
    monkeypatch.setattr(audit, "record_user", lambda *args, **kwargs: None)
    monkeypatch.setattr(matching_cache, "refresh_matching_csv", lambda path: {"ok": True, "rows": len(_rows(path))})
    admin = SimpleNamespace(state=SimpleNamespace(user={"username": "boss", "role": "admin"}), headers={})
    viewer = SimpleNamespace(state=SimpleNamespace(user={"username": "viewer", "role": "user"}), headers={})
    return paths, admin, viewer


def test_query_preview_then_explicit_approval_uses_csv_line_numbers(isolated):
    paths, admin, _viewer = isolated
    path = paths.db_root / "ppid_knob.csv"
    _write(path, ["feature_name", "rule_order", "step_desc", "operator", "value", "category"], [
        {"feature_name": "K", "rule_order": "R1", "step_desc": "ETCH", "operator": "eq", "value": "P1", "category": "OLD"},
    ])
    context = {}
    query = rulebook.handle("ppid_knob.csv 조회", context, admin)
    assert query["tool"]["table"]["rows"][0]["row_number"] == 2
    assert context["pending_rulebook_file"] == "ppid_knob.csv"

    preview = rulebook.handle("2행 category를 NEW로 수정해줘", context, admin)
    assert preview["tool"]["approval"]["status"] == "pending"
    assert preview["tool"]["table"]["rows"][0]["이전.category"] == "OLD"
    assert preview["tool"]["table"]["rows"][0]["변경.category"] == "NEW"
    assert _rows(path)[0]["category"] == "OLD"

    identifier = preview["tool"]["approval"]["id"]
    applied = rulebook.handle(f"승인 {identifier}", context, admin)
    assert applied["tool"]["approval"]["status"] == "applied"
    assert _rows(path)[0]["category"] == "NEW"
    assert list((paths.data_root / "rulebook_backups" / path.name).glob("*.csv"))
    assert not (paths.data_root / "matching").exists()


def test_stale_preview_and_other_user_cannot_apply(isolated):
    paths, admin, viewer = isolated
    path = paths.db_root / "mask_info.csv"
    _write(path, ["reticle_id", "category", "product"], [
        {"reticle_id": "M1", "category": "OLD", "product": "P"},
    ])
    context = {}
    preview = rulebook.handle("mask_info.csv 2행 category를 NEW로 수정", context, admin)
    assert preview["tool"]["approval"]["status"] == "pending"
    identifier = preview["tool"]["approval"]["id"]
    denied = rulebook.handle(f"승인 {identifier}", context, viewer)
    assert denied["ok"] is False
    assert _rows(path)[0]["category"] == "OLD"

    context["pending_rulebook_update"] = identifier
    _write(path, ["reticle_id", "category", "product"], [
        {"reticle_id": "M1", "category": "OTHER", "product": "P"},
    ])
    stale = rulebook.handle(f"승인 {identifier}", context, admin)
    assert stale["ok"] is False
    assert "변경" in stale["reply"]
    assert _rows(path)[0]["category"] == "OTHER"


def test_mask_csv_is_an_explicit_db_single_file(isolated):
    paths, admin, _viewer = isolated
    path = paths.db_root / "mask.csv"
    _write(path, ["product", "reticle_id", "mask_version"], [
        {"product": "P", "reticle_id": "R1", "mask_version": "OLD"},
    ])

    context = {}
    preview = rulebook.handle("mask 파일 2행 mask_version을 NEW로 수정", context, admin)
    assert preview["tool"]["file"] == "mask.csv"
    assert preview["tool"]["approval"]["status"] == "pending"
    assert _rows(path)[0]["mask_version"] == "OLD"
    rulebook.handle(f"승인 {preview['tool']['approval']['id']}", context, admin)
    assert _rows(path)[0]["mask_version"] == "NEW"


def test_tsv_add_and_ppid_priority_are_in_preview(isolated):
    paths, admin, _viewer = isolated
    vehicle = paths.db_root / "Vehicle_matching.csv"
    _write(vehicle, ["product", "step_id", "step_desc", "module"], [
        {"product": "P", "step_id": "S1", "step_desc": "ETCH", "module": "BEOL"},
    ])
    context = {}
    preview = rulebook.handle("Vehicle_matching.csv 추가\nproduct\tstep_id\tstep_desc\tmodule\nP\tS2\tCLEAN\tBEOL", context, admin)
    assert preview["tool"]["approval"]["status"] == "pending"
    assert _rows(vehicle) == [{"product": "P", "step_id": "S1", "step_desc": "ETCH", "module": "BEOL"}]
    rulebook.handle(f"승인 {preview['tool']['approval']['id']}", context, admin)
    assert [row["step_id"] for row in _rows(vehicle)] == ["S1", "S2"]

    knob = paths.db_root / "ppid_knob.csv"
    _write(knob, ["feature_name", "rule_order", "step_desc", "operator", "value", "category"], [
        {"feature_name": "K", "rule_order": "R1", "step_desc": "ETCH", "operator": "gte~~tkout_time", "value": "P1", "category": "OLD"},
        {"feature_name": "K", "rule_order": "R2", "step_desc": "ETCH", "operator": "eq", "value": "P1", "category": "NEW"},
    ])
    preview = rulebook.handle("ppid_knob.csv 2행 category를 OLD2로 수정", context, admin)
    shown = preview["tool"]["table"]["rows"]
    assert len(shown) == 2
    assert {(row["이전.rule_order"], row["변경.rule_order"]) for row in shown} == {("R1", "R2"), ("R2", "R1")}
    assert [row["rule_order"] for row in _rows(knob)] == ["R1", "R2"]
    rulebook.handle(f"승인 {preview['tool']['approval']['id']}", context, admin)
    assert [row["rule_order"] for row in _rows(knob)] == ["R2", "R1"]


def test_unrelated_alias_table_is_not_intercepted(isolated):
    _paths, admin, _viewer = isolated
    assert rulebook.handle("PRODA Inline 별칭 추가\nstep_id\titem_id\talias\nS1\tI1\tfoo", {}, admin) is None


def test_ambiguous_mask_filename_does_not_fall_back_to_previous_selection(isolated):
    _paths, admin, _viewer = isolated
    context = {"pending_rulebook_file": "mask.csv"}
    result = rulebook.handle("mask.csv와 mask_info.csv 2행 category를 NEW로 수정", context, admin)
    assert result["ok"] is False
    assert result["tool"]["missing"] == ["one_file"]


def test_configured_vm_file_in_db_is_the_preview_and_write_target(isolated):
    paths, admin, _viewer = isolated
    schema = paths.data_root / "splittable" / "rulebook_schema.json"
    schema.parent.mkdir(parents=True)
    schema.write_text(json.dumps({"vm_matching": {"file_name": "vm_active.csv"}}), encoding="utf-8")
    active = paths.db_root / "vm_active.csv"
    _write(active, ["step_desc", "item_id"], [{"step_desc": "ETCH", "item_id": "I1"}])
    stale = paths.db_root / "vm_matching.csv"
    _write(stale, ["step_desc", "item_id"], [{"step_desc": "ETCH", "item_id": "OLD"}])

    context = {}
    preview = rulebook.handle("vm_matching.csv 2행 item_id를 I2로 수정", context, admin)
    assert preview["tool"]["sources"] == ["원천 DB/vm_active.csv"]
    assert _rows(active)[0]["item_id"] == "I1"
    rulebook.handle(f"승인 {preview['tool']['approval']['id']}", context, admin)
    assert _rows(active)[0]["item_id"] == "I2"
    assert _rows(stale)[0]["item_id"] == "OLD"
