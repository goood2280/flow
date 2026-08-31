import csv
import json
from types import SimpleNamespace

from core import fab_matching_alerts as alerts
from core import valve_step_advisor


def test_unnecessary_decision_can_be_cancelled_back_to_active(tmp_path, monkeypatch):
    ack_path = tmp_path / "acks.json"
    decisions_path = tmp_path / "decisions.jsonl"
    source = {"id": "step-undo", "type": "unmatched_step", "product": "P1", "step_id": "S1"}
    monkeypatch.setattr(alerts, "ACK_PATH", ack_path)
    monkeypatch.setattr(alerts, "DECISIONS_PATH", decisions_path)
    monkeypatch.setattr(alerts, "_find_alert", lambda alert_id: source if alert_id == source["id"] else None)

    marked = alerts.hold_alert(source["id"], "반영불필요", username="first-user")
    assert marked["status"] == "반영불필요"
    assert json.loads(ack_path.read_text("utf-8"))[source["id"]]["status"] == "반영불필요"

    cancelled = alerts.hold_alert(source["id"], "active", username="second-user")

    assert cancelled["status"] == "active"
    assert json.loads(ack_path.read_text("utf-8")) == {}
    history = alerts.list_decisions()
    assert [row["action"] for row in history] == ["active", "반영불필요"]
    assert history[0]["by"] == "second-user"


def _write_csv(path, columns, rows):
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def test_batch_groups_versions_and_resolves_ppid_from_new_step(tmp_path, monkeypatch):
    vehicle_path = tmp_path / alerts.VEHICLE_MATCHING_FILE
    knob_path = tmp_path / alerts.PPID_KNOB_FILE
    _write_csv(vehicle_path, ["vehicle", "product", "step_id", "step_desc"], [])
    _write_csv(knob_path,
               ["feature_name", "function_step", "rule_order", "operator", "value", "category"],
               [{"feature_name": "KNOB_A", "function_step": "ETCH", "rule_order": "RO",
                 "operator": "", "value": "", "category": "RO"}])

    source_alerts = [
        {"id": "step-1", "type": "unmatched_step", "vehicle": "V1", "product": "P1", "step_id": "S1"},
        {"id": "step-2", "type": "unmatched_step", "vehicle": "V1", "product": "P1", "step_id": "S2"},
        {"id": "ppid-1", "type": "ro_ppid", "vehicle": "V1", "product": "P1", "step_id": "S1",
         "ppid": "PP_A", "feature_name": "KNOB_A", "step_desc": ""},
        {"id": "ppid-2", "type": "ro_ppid", "vehicle": "V1", "product": "P1", "step_id": "S1",
         "ppid": "PP_B", "feature_name": "KNOB_A", "step_desc": ""},
    ]
    version_calls = []
    decisions = []
    monkeypatch.setattr(alerts, "PATHS", SimpleNamespace(db_root=tmp_path))
    monkeypatch.setattr(alerts, "list_alerts", lambda: {"alerts": source_alerts})
    monkeypatch.setattr(alerts, "_post_write", lambda path, actor, note: version_calls.append((path.name, note)) or {
        "version_snapshot": True, "version_meta": {"version": "v1"}})
    monkeypatch.setattr(alerts, "_append_decision", decisions.append)
    monkeypatch.setattr(alerts, "request_scan", lambda: {"ok": True})

    result = alerts.apply_batch([
        {"type": "match_step", "id": "step-1", "step_desc": "ETCH"},
        {"type": "match_step", "id": "step-2", "step_desc": "CLEAN"},
        {"type": "classify_ppid", "id": "ppid-1", "category": "A"},
        {"type": "classify_ppid", "id": "ppid-2", "category": "B"},
    ], note="한 번에 승인", username="tester")

    assert result["count"] == 4
    assert result["step_count"] == 2
    assert result["ppid_count"] == 2
    assert [name for name, _ in version_calls] == [alerts.VEHICLE_MATCHING_FILE, alerts.PPID_KNOB_FILE]
    assert all(result["batch_id"] in note for _, note in version_calls)
    assert {row["batch_id"] for row in decisions} == {result["batch_id"]}

    _, vehicle_rows = alerts._read_csv(vehicle_path)
    _, knob_rows = alerts._read_csv(knob_path)
    assert [(row["step_id"], row["step_desc"]) for row in vehicle_rows] == [("S1", "ETCH"), ("S2", "CLEAN")]
    added = [row for row in knob_rows if row["rule_order"] != "RO"]
    assert [(row["rule_order"], row["value"], row["category"]) for row in added] == [
        ("R1", "PP_A", "A"), ("R2", "PP_B", "B")]


def test_batch_adds_mask_info_rows_and_versions_the_file(tmp_path, monkeypatch):
    mask_path = tmp_path / alerts.MASK_INFO_FILE
    _write_csv(mask_path, ["reticle_id", "mask"], [{"reticle_id": "RAA001", "mask": "MASK_A"}])

    source_alerts = [
        {"id": "fab-reticle|RAA002", "type": "missing_reticle", "vehicle": "V1", "product": "P1",
         "step_id": "S1", "reticle_id": "RAA002"},
        {"id": "fab-reticle|RAA003", "type": "missing_reticle", "vehicle": "V1", "product": "P2",
         "step_id": "S2", "reticle_id": "RAA003"},
    ]
    version_calls = []
    decisions = []
    monkeypatch.setattr(alerts, "PATHS", SimpleNamespace(db_root=tmp_path))
    monkeypatch.setattr(alerts, "list_alerts", lambda: {"alerts": source_alerts})
    monkeypatch.setattr(alerts, "_post_write", lambda path, actor, note: version_calls.append((path.name, note)) or {
        "version_snapshot": True, "version_meta": {"version": "v1"}})
    monkeypatch.setattr(alerts, "_append_decision", decisions.append)
    monkeypatch.setattr(alerts, "request_scan", lambda: {"ok": True})

    result = alerts.apply_batch([
        {"type": "add_mask", "id": "fab-reticle|RAA002", "mask": "MASK_B"},
        {"type": "add_mask", "id": "fab-reticle|RAA003", "mask": "MASK_C"},
    ], username="tester")

    assert result["mask_count"] == 2
    # 마스크만 바뀐 배치는 mask_info.csv 한 파일에 버전 1개만 남긴다.
    assert [name for name, _ in version_calls] == [alerts.MASK_INFO_FILE]
    columns, rows = alerts._read_csv(mask_path)
    assert columns == ["reticle_id", "mask"]
    assert [(row["reticle_id"], row["mask"]) for row in rows] == [
        ("RAA001", "MASK_A"), ("RAA002", "MASK_B"), ("RAA003", "MASK_C")]
    assert {row["action"] for row in decisions} == {"add_mask"}


def test_batch_rejects_reticle_already_in_mask_info(tmp_path, monkeypatch):
    mask_path = tmp_path / alerts.MASK_INFO_FILE
    _write_csv(mask_path, ["reticle_id", "mask"], [{"reticle_id": "raa001", "mask": "MASK_A"}])
    source = {"id": "fab-reticle|RAA001", "type": "missing_reticle", "product": "P1",
              "step_id": "S1", "reticle_id": "RAA001"}
    monkeypatch.setattr(alerts, "PATHS", SimpleNamespace(db_root=tmp_path))
    monkeypatch.setattr(alerts, "list_alerts", lambda: {"alerts": [source]})

    try:
        alerts.apply_batch([{"type": "add_mask", "id": "fab-reticle|RAA001", "mask": "MASK_X"}])
    except ValueError as exc:
        assert "이미 등록된 reticle" in str(exc)
    else:
        raise AssertionError("duplicate reticle must fail")
    _, rows = alerts._read_csv(mask_path)
    assert [row["mask"] for row in rows] == ["MASK_A"]


def test_missing_reticle_alerts_skip_known_reticles(tmp_path, monkeypatch):
    _write_csv(tmp_path / alerts.MASK_INFO_FILE, ["reticle_id", "mask"],
               [{"reticle_id": "RAA001", "mask": "MASK_A"}])
    _write_csv(tmp_path / alerts.VEHICLE_MATCHING_FILE, ["vehicle", "product", "step_id", "step_desc"],
               [{"vehicle": "V1", "product": "P1", "step_id": "S1", "step_desc": "ETCH"}])
    _write_csv(tmp_path / alerts.PPID_KNOB_FILE,
               ["feature_name", "function_step", "rule_order", "operator", "value", "category"], [])
    monkeypatch.setattr(alerts, "PATHS", SimpleNamespace(db_root=tmp_path))
    monkeypatch.setattr(alerts, "load_cfg", lambda: {"step_exceptions": []})

    observations = [
        {"step_id": "S1", "ppid": "PP_A", "rows": 4, "n_lots": 2, "root_lot_id": "L1",
         "lot_id": "L1.1", "wafer_id": "W1", "eqp_id": "E1", "eqp_model": "M1",
         "latest_event_time": "2026-01-01"},
    ]
    reticle_observations = [
        {"reticle_id": "RAA001", "rows": 4, "n_lots": 2, "lot_id": "L1.1", "root_lot_id": "L1", "wafer_id": "W1",
         "eqp_id": "E1", "eqp_model": "M1", "latest_event_time": "2026-01-01",
         "step_ids": ["S1"], "ppids": ["PP_A"]},
        {"reticle_id": "RAA002", "rows": 6, "n_lots": 3, "lot_id": "L2.7", "root_lot_id": "L2", "wafer_id": "W2",
         "eqp_id": "E1", "eqp_model": "M1", "latest_event_time": "2026-01-02",
         "step_ids": ["S1", "S2"], "ppids": ["PP_A", "PP_B"]},
    ]
    produced = alerts._alerts_for_product(
        {"product": "P1", "path": str(tmp_path), "root": "FAB"},
        observations, reticle_observations, [])
    reticle_alerts = [a for a in produced if a["type"] == "missing_reticle"]

    # mask_info.csv 에 이미 있는 RAA001 은 빠지고 RAA002 만 알람이 된다.
    assert [a["reticle_id"] for a in reticle_alerts] == ["RAA002"]
    only = reticle_alerts[0]
    assert only["id"] == "fab-reticle|RAA002"
    assert only["rows"] == 6 and only["n_lots"] == 3
    assert only["step_ids"] == ["S1", "S2"] and only["ppids"] == ["PP_A", "PP_B"]
    assert only["examples"] == [{"lot_id": "L2.7", "root_lot_id": "L2", "wafer_id": "W2"}]


def test_unmatched_step_alert_includes_lot_and_wafer_example(tmp_path, monkeypatch):
    _write_csv(tmp_path / alerts.MASK_INFO_FILE, ["reticle_id", "mask"], [])
    _write_csv(tmp_path / alerts.VEHICLE_MATCHING_FILE,
               ["vehicle", "product", "step_id", "step_desc"], [])
    _write_csv(tmp_path / alerts.PPID_KNOB_FILE,
               ["feature_name", "function_step", "rule_order", "operator", "value", "category"], [])
    monkeypatch.setattr(alerts, "PATHS", SimpleNamespace(db_root=tmp_path))
    monkeypatch.setattr(alerts, "load_cfg", lambda: {"step_exceptions": []})

    produced = alerts._alerts_for_product(
        {"product": "P1", "path": str(tmp_path), "root": "FAB"},
        [{"step_id": "S_NEW", "ppid": "PP_A", "rows": 2, "n_lots": 1,
          "lot_id": "LOT.3", "root_lot_id": "LOT", "wafer_id": "17",
          "eqp_ids": ["E1"], "eqp_models": ["M1"], "areas": ["ETCH"]}],
        [], [],
    )

    step_alert = next(row for row in produced if row["type"] == "unmatched_step")
    assert step_alert["examples"] == [
        {"lot_id": "LOT.3", "root_lot_id": "LOT", "wafer_id": "17"}]


def test_unmatched_step_alert_carries_same_area_matching_step_signatures(tmp_path, monkeypatch):
    _write_csv(tmp_path / alerts.MASK_INFO_FILE, ["reticle_id", "mask"], [])
    _write_csv(tmp_path / alerts.VEHICLE_MATCHING_FILE,
               ["vehicle", "product", "step_id", "step_desc"], [
                   {"vehicle": "V1", "product": "P1", "step_id": "S_MATCH",
                    "step_desc": "ETCH_FUNC"},
                   {"vehicle": "V1", "product": "P1", "step_id": "S_OTHER_AREA",
                    "step_desc": "PHOTO_FUNC"},
               ])
    _write_csv(tmp_path / alerts.PPID_KNOB_FILE,
               ["feature_name", "function_step", "rule_order", "operator", "value", "category"], [])
    monkeypatch.setattr(alerts, "PATHS", SimpleNamespace(db_root=tmp_path))
    monkeypatch.setattr(alerts, "load_cfg", lambda: {"step_exceptions": []})

    def observed(step_id, ppid, eqp_id, area):
        return {"step_id": step_id, "ppid": ppid, "rows": 2, "n_lots": 1,
                "eqp_ids": [eqp_id], "eqp_models": ["MODEL_A"], "areas": [area],
                "eqp_id": eqp_id, "eqp_model": "MODEL_A", "area": area}

    produced = alerts._alerts_for_product(
        {"product": "P1", "path": str(tmp_path), "root": "FAB"},
        [
            observed("S_NEW", "PP_SHARED", "EQP_A", "ETCH"),
            observed("S_MATCH", "PP_SHARED", "EQP_A", "ETCH"),
            observed("S_OTHER_AREA", "PP_SHARED", "EQP_A", "PHOTO"),
        ],
        [], [],
    )

    step_alert = next(row for row in produced if row["type"] == "unmatched_step")
    assert step_alert["match_hint"]["values"]["area"] == ["ETCH"]
    assert [row["step_id"] for row in step_alert["match_hint"]["neighbors"]] == ["S_MATCH"]
    assert step_alert["match_hint"]["neighbors"][0]["values"]["ppid"] == ["PP_SHARED"]


def test_list_alerts_merges_same_reticle_across_products(monkeypatch):
    def _alert(product, rows, n_lots, step_ids, seen):
        return {"id": "fab-reticle|RAA002", "type": "missing_reticle", "reticle_id": "RAA002",
                "product": product, "vehicle": product, "step_id": step_ids[0],
                "step_ids": step_ids, "ppids": ["PP_A"], "rows": rows, "n_lots": n_lots,
                "first_seen_ts": seen, "last_seen_ts": seen, "latest_event_time": ""}

    monkeypatch.setattr(alerts, "_load_state", lambda: {"alerts_by_product": {
        "P1": [_alert("P1", 6, 3, ["S1"], 100.0)],
        "P2": [_alert("P2", 4, 2, ["S2"], 200.0)],
    }, "products": []})
    monkeypatch.setattr(alerts, "_acks", dict)
    monkeypatch.setattr(alerts, "_decided_ids", dict)
    monkeypatch.setattr(alerts, "_recommendations", dict)

    rows = alerts.list_alerts()["alerts"]

    assert len(rows) == 1
    merged = rows[0]
    assert merged["products"] == ["P1", "P2"]
    assert [item["product"] for item in merged["product_evidence"]] == ["P1", "P2"]
    assert [item["rows"] for item in merged["product_evidence"]] == [6, 4]
    assert [item["n_lots"] for item in merged["product_evidence"]] == [3, 2]
    assert merged["rows"] == 10 and merged["n_lots"] == 5
    assert merged["step_ids"] == ["S1", "S2"]
    assert merged["first_seen_ts"] == 100.0 and merged["last_seen_ts"] == 200.0


def _isolate_state(tmp_path, monkeypatch):
    monkeypatch.setattr(alerts, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(alerts, "SCANNER_PATH", tmp_path / "scanner.json")
    monkeypatch.setattr(alerts, "_scanner_local", {})
    # 이 테스트들은 worker 역할을 흉내내므로, 진짜 검사 스레드가 뜨지 않게 막는다.
    monkeypatch.setattr(alerts, "_ensure_scheduler_running", lambda: False)


def test_recommendations_hide_records_from_old_algorithm(monkeypatch):
    monkeypatch.setattr(valve_step_advisor, "load_records", lambda: {
        "V1|OLD": {"algorithm_version": valve_step_advisor.ALGORITHM_VERSION - 1,
                    "step_desc": "OLD_DESC"},
        "V1|NEW": {"algorithm_version": valve_step_advisor.ALGORITHM_VERSION,
                    "step_desc": "NEW_DESC"},
    })

    records = alerts._recommendations()

    assert list(records) == ["V1|NEW"]


def test_recommendation_batch_uses_current_fab_alerts_and_saves_status(tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    source = {"id": "fab-step|P1|S1", "type": "unmatched_step", "vehicle": "P1",
              "product": "P1", "step_id": "S1", "status": "active"}
    received = []
    monkeypatch.setattr(alerts, "list_alerts", lambda: {"alerts": [source]})
    monkeypatch.setattr(
        valve_step_advisor,
        "recommend_pending",
        lambda rows: received.extend(rows) or {
            "ok": True, "enabled": True, "pending": 11,
            "checked": 10, "skipped": 1, "records": [],
        },
    )

    result = alerts._run_recommendation_batch()

    assert received == [source]
    assert result["checked"] == 10 and result["skipped"] == 1
    status = alerts._load_state()["recommendation_status"]
    assert status["ok"] is True and status["pending"] == 11
    assert status["checked"] == 10 and status["remaining"] == 1
    assert status["finished_ts"] >= status["last_run_ts"]


def test_recommendation_batch_records_error_without_raising(tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    monkeypatch.setattr(alerts, "list_alerts", lambda: {"alerts": []})

    def _fail(_rows):
        raise RuntimeError("advisor unavailable")

    monkeypatch.setattr(valve_step_advisor, "recommend_pending", _fail)

    result = alerts._run_recommendation_batch()

    assert result["ok"] is False
    assert "advisor unavailable" in alerts._load_state()["recommendation_status"]["error"]


def test_scan_request_is_cleared_even_when_no_products_are_found(tmp_path, monkeypatch):
    """예전에는 이 경로가 요청 플래그를 남겨 화면이 영원히 '검사 요청 대기 중'이었다."""
    _isolate_state(tmp_path, monkeypatch)
    monkeypatch.setattr(alerts, "_development_worker_enabled", lambda: True)
    monkeypatch.setattr(alerts, "discover_products", list)
    monkeypatch.setattr(alerts, "load_cfg", lambda: {"enabled": True, "scan_interval_seconds": 300})
    alerts.request_scan()
    assert alerts._load_state()["scan_requested"] is True

    result = alerts.scan_next_product(force=True)

    assert result["ok"] is False and result["products"] == 0
    state = alerts._load_state()
    assert state["scan_requested"] is False
    assert "FAB 제품 폴더" in state["last_error"]


def test_scan_request_is_cleared_before_the_product_scan_runs(tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    monkeypatch.setattr(alerts, "_development_worker_enabled", lambda: True)
    monkeypatch.setattr(alerts, "discover_products",
                        lambda: [{"product": "P1", "path": str(tmp_path), "root": "FAB"}])
    monkeypatch.setattr(alerts, "load_cfg", lambda: {"enabled": True, "scan_interval_seconds": 300})
    monkeypatch.setattr(alerts, "scan_product", lambda product: {"ok": True, "product": "P1"})
    alerts.request_scan()

    alerts.scan_next_product(force=True)

    assert alerts._load_state()["scan_requested"] is False


def test_list_alerts_explains_a_pending_request_with_no_running_scanner(tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    monkeypatch.setattr(alerts, "_development_worker_enabled", lambda: False)
    monkeypatch.setattr(alerts, "_acks", dict)
    monkeypatch.setattr(alerts, "_decided_ids", dict)
    monkeypatch.setattr(alerts, "_recommendations", dict)
    alerts.request_scan()

    scanner = alerts.list_alerts()["scanner"]

    assert scanner["scan_requested"] is True
    assert scanner["scanner_alive"] is False and scanner["scanner_state"] == "down"
    assert "개발 worker 검사기가 없습니다" in scanner["scan_request_hint"]


def test_list_alerts_reports_the_product_being_scanned_right_now(tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    monkeypatch.setattr(alerts, "_development_worker_enabled", lambda: True)
    monkeypatch.setattr(alerts, "_acks", dict)
    monkeypatch.setattr(alerts, "_decided_ids", dict)
    monkeypatch.setattr(alerts, "_recommendations", dict)
    alerts._scanner_beat(state="scanning", product="PRODA",
                         product_started_ts=alerts.time.time() - 120,
                         files_done=7, files_total=40)
    alerts.request_scan()

    scanner = alerts.list_alerts()["scanner"]

    assert scanner["scanner_alive"] is True and scanner["scanner_state"] == "scanning"
    assert scanner["scanning"]["product"] == "PRODA"
    assert scanner["scanning"]["files_done"] == 7 and scanner["scanning"]["files_total"] == 40
    assert scanner["scanning"]["elapsed_seconds"] >= 119
    assert "PRODA 검사 중" in scanner["scan_request_hint"]


def test_step_exception_without_product_applies_to_every_product(tmp_path, monkeypatch):
    _write_csv(tmp_path / alerts.MASK_INFO_FILE, ["reticle_id", "mask"], [])
    _write_csv(tmp_path / alerts.VEHICLE_MATCHING_FILE,
               ["vehicle", "product", "step_id", "step_desc"], [])
    _write_csv(tmp_path / alerts.PPID_KNOB_FILE,
               ["feature_name", "function_step", "rule_order", "operator", "value", "category"], [])
    monkeypatch.setattr(alerts, "PATHS", SimpleNamespace(db_root=tmp_path))
    monkeypatch.setattr(alerts, "load_cfg", lambda: {"step_exceptions": [
        # 제품 칸이 비어 있으면 전 제품에 적용된다.
        {"id": "r1", "enabled": True, "product": "", "column": "area",
         "operator": "eq", "value": "photo"},
    ]})

    def _observation(step_id, area):
        return {"step_id": step_id, "ppid": "PP_A", "rows": 1, "n_lots": 1,
                "root_lot_id": "L1", "wafer_id": "W1", "eqp_id": "E1", "eqp_model": "M1",
                "area": area, "eqp_ids": ["E1"], "eqp_models": ["M1"], "areas": [area],
                "latest_event_time": "2026-01-01"}

    for product in ("P1", "P2"):
        produced = alerts._alerts_for_product(
            {"product": product, "path": str(tmp_path), "root": "FAB"},
            [_observation("S1", "PHOTO"), _observation("S2", "ETCH")], [], [])
        steps = [a for a in produced if a["type"] == "unmatched_step"]
        # area=PHOTO 인 S1 은 두 제품 모두에서 제외되고 S2 만 알람으로 남는다.
        assert [a["step_id"] for a in steps] == ["S2"]
        assert steps[0]["areas"] == ["ETCH"] and steps[0]["area"] == "ETCH"


def test_step_exception_scoped_to_one_product_leaves_others_alerting(tmp_path, monkeypatch):
    _write_csv(tmp_path / alerts.MASK_INFO_FILE, ["reticle_id", "mask"], [])
    _write_csv(tmp_path / alerts.VEHICLE_MATCHING_FILE,
               ["vehicle", "product", "step_id", "step_desc"], [])
    _write_csv(tmp_path / alerts.PPID_KNOB_FILE,
               ["feature_name", "function_step", "rule_order", "operator", "value", "category"], [])
    monkeypatch.setattr(alerts, "PATHS", SimpleNamespace(db_root=tmp_path))
    monkeypatch.setattr(alerts, "load_cfg", lambda: {"step_exceptions": [
        {"id": "r1", "enabled": True, "product": "P1", "column": "eqp_model",
         "operator": "starts_with", "value": "TEST"},
    ]})
    observation = {"step_id": "S1", "ppid": "PP_A", "rows": 1, "n_lots": 1,
                   "root_lot_id": "L1", "wafer_id": "W1", "eqp_id": "E1",
                   "eqp_model": "TEST_M1", "area": "PHOTO", "eqp_ids": ["E1"],
                   "eqp_models": ["TEST_M1"], "areas": ["PHOTO"], "latest_event_time": ""}

    excluded = alerts._alerts_for_product(
        {"product": "P1", "path": str(tmp_path), "root": "FAB"}, [observation], [], [])
    kept = alerts._alerts_for_product(
        {"product": "P2", "path": str(tmp_path), "root": "FAB"}, [observation], [], [])

    assert [a["type"] for a in excluded] == []
    assert [a["step_id"] for a in kept if a["type"] == "unmatched_step"] == ["S1"]


def test_saved_exception_hides_matching_steps_before_the_next_fab_scan(tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    monkeypatch.setattr(alerts, "_development_worker_enabled", lambda: True)
    monkeypatch.setattr(alerts, "_acks", dict)
    monkeypatch.setattr(alerts, "_decided_ids", dict)
    monkeypatch.setattr(alerts, "_recommendations", dict)

    def _stored(step_id, area):
        return {"id": f"fab-step|P1|{step_id}", "type": "unmatched_step", "product": "P1",
                "vehicle": "P1", "step_id": step_id, "area": area, "areas": [area],
                "eqp_ids": ["E1"], "eqp_models": ["M1"], "ppids": ["PP_A"],
                "first_seen_ts": 1.0, "last_seen_ts": 1.0}

    monkeypatch.setattr(alerts, "_load_state", lambda: {
        "alerts_by_product": {"P1": [_stored("S1", "PHOTO"), _stored("S2", "ETCH")]},
        "products": [],
    })
    monkeypatch.setattr(alerts, "load_cfg", lambda: {"step_exceptions": [
        {"id": "r1", "enabled": True, "product": "", "column": "area",
         "operator": "eq", "value": "PHOTO"},
    ]})

    rows = alerts.list_alerts()["alerts"]

    assert [row["step_id"] for row in rows] == ["S2"]


def test_normalize_step_exceptions_keeps_the_four_supported_columns():
    raw = [{"id": f"r{i}", "column": column, "operator": "eq", "value": "V"}
           for i, column in enumerate(alerts.EXCEPTION_COLUMNS + ("chamber_id",))]

    kept = alerts._normalize_step_exceptions(raw)

    assert [rule["column"] for rule in kept] == list(alerts.EXCEPTION_COLUMNS)
    assert all(rule["product"] == "" for rule in kept)


def test_batch_rejects_duplicate_alert_without_writing(tmp_path, monkeypatch):
    source = {"id": "step-1", "type": "unmatched_step", "vehicle": "V1", "product": "P1", "step_id": "S1"}
    monkeypatch.setattr(alerts, "PATHS", SimpleNamespace(db_root=tmp_path))
    monkeypatch.setattr(alerts, "list_alerts", lambda: {"alerts": [source]})

    try:
        alerts.apply_batch([
            {"type": "match_step", "id": "step-1", "step_desc": "ETCH"},
            {"type": "match_step", "id": "step-1", "step_desc": "ETCH"},
        ])
    except ValueError as exc:
        assert "중복" in str(exc)
    else:
        raise AssertionError("duplicate alert id must fail")
    assert not (tmp_path / alerts.VEHICLE_MATCHING_FILE).exists()
