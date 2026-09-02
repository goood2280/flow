import csv
import json
from types import SimpleNamespace

import pytest

from core import fab_matching_alerts as alerts


def _write_csv(path, columns, rows):
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _write_plan(path, plans, mismatch_alerts):
    path.write_text(json.dumps({
        "plans": plans,
        "history": [],
        "mismatch_alerts": mismatch_alerts,
    }, ensure_ascii=False), encoding="utf-8")


def _isolate(tmp_path, monkeypatch):
    plan_dir = tmp_path / "splittable"
    plan_dir.mkdir()
    monkeypatch.setattr(alerts, "PATHS", SimpleNamespace(db_root=tmp_path))
    monkeypatch.setattr(alerts, "PLAN_DIR", plan_dir)
    monkeypatch.setattr(alerts, "DECISIONS_PATH", tmp_path / "decisions.jsonl")
    return plan_dir


def test_plan_anomalies_group_duplicate_notifications_and_ignore_stale_plan(tmp_path, monkeypatch):
    plan_dir = _isolate(tmp_path, monkeypatch)
    _write_csv(
        tmp_path / alerts.PPID_KNOB_FILE,
        ["feature_name", "function_step", "rule_order", "operator", "value", "category"],
        [{"feature_name": "5.0 PC", "function_step": "PC_ETCH", "rule_order": "RO",
          "operator": "eq", "value": "PLAN_A", "category": "PLAN_A"}],
    )
    _write_csv(
        tmp_path / alerts.VEHICLE_MATCHING_FILE,
        ["product", "step_id", "step_desc"],
        [{"product": "PRODA", "step_id": "S20", "step_desc": "PC_ETCH"}],
    )
    plans = {
        "LOT1|1|KNOB_5.0 PC": {"value": "PLAN_A", "user": "owner", "updated": "2026-09-03T10:00:00"},
        "LOT1|2|KNOB_5.0 PC": {"value": "PLAN_A", "user": "owner", "updated": "2026-09-03T10:00:00"},
        "LOT1|3|KNOB_5.0 PC": {"value": "NEW_PLAN", "user": "owner", "updated": "2026-09-03T11:00:00"},
    }
    mismatches = {
        # The same cell is stored once per notification recipient; it must count once.
        "a": {"product": "ML_TABLE_PRODA", "cell": "LOT1|1|KNOB_5.0 PC", "column": "KNOB_5.0 PC",
              "plan": "PLAN_A", "actual": "PP_BAD", "root_lot_id": "LOT1", "wafer_id": "1"},
        "a-team": {"product": "ML_TABLE_PRODA", "cell": "LOT1|1|KNOB_5.0 PC", "column": "KNOB_5.0 PC",
                   "plan": "PLAN_A", "actual": "PP_BAD", "root_lot_id": "LOT1", "wafer_id": "1"},
        "b": {"product": "ML_TABLE_PRODA", "cell": "LOT1|2|KNOB_5.0 PC", "column": "KNOB_5.0 PC",
              "plan": "PLAN_A", "actual": "PP_BAD", "root_lot_id": "LOT1", "wafer_id": "2"},
        # This notification describes an old plan and must not remain actionable.
        "stale": {"product": "ML_TABLE_PRODA", "cell": "LOT1|3|KNOB_5.0 PC", "column": "KNOB_5.0 PC",
                  "plan": "OLD_PLAN", "actual": "PP_OLD"},
    }
    _write_plan(plan_dir / "ML_TABLE_PRODA.json", plans, mismatches)

    result = alerts.list_plan_knob_anomalies()

    assert result["total"] == 1
    item = result["items"][0]
    assert item["product_key"] == "PRODA"
    assert item["feature_name"] == "5.0 PC"
    assert item["plan"] == "PLAN_A" and item["actual_ppid"] == "PP_BAD"
    assert item["occurrences"] == 2
    assert item["step_desc"] == "PC_ETCH" and item["step_ids"] == ["S20"]
    assert item["mode"] == "add" and item["ready"] is True


def test_apply_plan_anomaly_adds_versioned_product_step_rule(tmp_path, monkeypatch):
    plan_dir = _isolate(tmp_path, monkeypatch)
    knob_path = tmp_path / alerts.PPID_KNOB_FILE
    _write_csv(
        knob_path,
        ["feature_name", "function_step", "rule_order", "operator", "value", "category"],
        [{"feature_name": "5.0 PC", "function_step": "PC_ETCH", "rule_order": "RO",
          "operator": "eq", "value": "PLAN_A", "category": "PLAN_A"}],
    )
    _write_csv(
        tmp_path / alerts.VEHICLE_MATCHING_FILE,
        ["product", "step_id", "step_desc"],
        [{"product": "PRODA", "step_id": "S20", "step_desc": "PC_ETCH"}],
    )
    cell = "LOT1|7|KNOB_5.0 PC"
    _write_plan(
        plan_dir / "ML_TABLE_PRODA.json",
        {cell: {"value": "PLAN_A", "user": "owner", "updated": "2026-09-03T10:00:00"}},
        {"a": {"product": "ML_TABLE_PRODA", "cell": cell, "column": "KNOB_5.0 PC",
               "plan": "PLAN_A", "actual": "PP_BAD", "root_lot_id": "LOT1", "wafer_id": "7"}},
    )
    versions = []
    decisions = []
    monkeypatch.setattr(alerts, "_post_write", lambda path, actor, note: versions.append((path.name, actor, note)) or {
        "version_snapshot": True, "version_meta": {"version": "v1"}})
    monkeypatch.setattr(alerts, "_append_decision", decisions.append)
    anomaly = alerts.list_plan_knob_anomalies()["items"][0]

    result = alerts.apply_plan_knob_anomalies([anomaly["id"]], "실제 진행을 plan으로 분류", "tester")

    assert result["count"] == 1
    assert result["results"][0]["change_mode"] == "add"
    columns, rows = alerts._read_csv(knob_path)
    assert columns[-3:] == ["product", "step_id", "step_desc"]
    added = next(row for row in rows if row["value"] == "PP_BAD")
    assert added == {
        "feature_name": "5.0 PC", "function_step": "PC_ETCH", "rule_order": "R1",
        "operator": "eq", "value": "PP_BAD", "category": "PLAN_A",
        "product": "PRODA", "step_id": "S20", "step_desc": "PC_ETCH",
    }
    assert len(versions) == 1 and "실제 진행을 plan으로 분류" in versions[0][2]
    assert decisions[0]["action"] == "plan_knob"
    assert decisions[0]["detail"] == "실제 진행을 plan으로 분류"
    assert alerts.list_plan_knob_anomalies()["total"] == 0


def test_apply_plan_anomaly_requires_comment_and_updates_existing_rule(tmp_path, monkeypatch):
    plan_dir = _isolate(tmp_path, monkeypatch)
    knob_path = tmp_path / alerts.PPID_KNOB_FILE
    _write_csv(
        knob_path,
        ["feature_name", "function_step", "rule_order", "operator", "value", "category"],
        [
            {"feature_name": "5.0 PC", "function_step": "PC_ETCH", "rule_order": "R1",
             "operator": "eq", "value": "PP_BAD", "category": "OLD_NAME"},
            {"feature_name": "5.0 PC", "function_step": "PC_ETCH", "rule_order": "RO",
             "operator": "eq", "value": "PLAN_A", "category": "PLAN_A"},
        ],
    )
    _write_csv(tmp_path / alerts.VEHICLE_MATCHING_FILE, ["product", "step_id", "step_desc"], [])
    cell = "LOT1|1|KNOB_5.0 PC"
    _write_plan(
        plan_dir / "ML_TABLE_PRODA.json",
        {cell: {"value": "PLAN_A", "user": "owner", "updated": "now"}},
        {"a": {"product": "ML_TABLE_PRODA", "cell": cell, "column": "KNOB_5.0 PC",
               "plan": "PLAN_A", "actual": "PP_BAD"}},
    )
    item = alerts.list_plan_knob_anomalies()["items"][0]
    assert item["mode"] == "update" and item["current_categories"] == ["OLD_NAME"]

    with pytest.raises(ValueError, match="코멘트"):
        alerts.apply_plan_knob_anomalies([item["id"]], "", "tester")

    monkeypatch.setattr(alerts, "_post_write", lambda *_args: {})
    monkeypatch.setattr(alerts, "_append_decision", lambda _row: None)
    result = alerts.apply_plan_knob_anomalies([item["id"]], "오분류 정정", "tester")

    assert result["results"][0]["change_mode"] == "update"
    _, rows = alerts._read_csv(knob_path)
    assert [row["category"] for row in rows if row["value"] == "PP_BAD"] == ["PLAN_A"]


def test_split_plan_mismatch_keeps_anomaly_ledger_when_notification_fails(monkeypatch):
    from core import notify
    from routers import splittable

    cell = "LOT1|1|KNOB_5.0 PC"
    saved = []
    monkeypatch.setattr(splittable, "_load_plan_data", lambda _product: {
        "plans": {cell: {"value": "PLAN_A", "user": "owner", "updated": "now"}},
        "history": [], "mismatch_alerts": {},
    })
    monkeypatch.setattr(splittable, "_product_mismatch_group_members", lambda _product: [])
    monkeypatch.setattr(splittable, "load_json", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(splittable, "save_json", lambda _path, payload: saved.append(payload))
    monkeypatch.setattr(notify, "emit_event", lambda *_args, **_kwargs: False)

    sent = splittable._notify_plan_actual_mismatches_once("ML_TABLE_PRODA", [{
        "key": cell, "plan": "PLAN_A", "actual": "PP_BAD",
        "plan_user": "owner", "plan_updated": "now",
    }])

    assert sent == 0
    assert len(saved) == 1
    ledgers = [row for row in saved[0]["mismatch_alerts"].values() if row.get("ledger") == "plan_knob"]
    assert len(ledgers) == 1
    assert ledgers[0]["cell"] == cell and ledgers[0]["actual"] == "PP_BAD"
