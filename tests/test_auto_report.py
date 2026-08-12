from pathlib import Path

import duckdb
import pytest

from core import auto_report


def test_parse_key_accepts_trigger_prefix_and_product_underscores():
    parsed = auto_report.parse_key("_TRIGGER_PRODUCT_ALPHA_A1000A.3_4500")
    assert parsed == {
        "key": "PRODUCT_ALPHA_A1000A.3_4500",
        "product": "PRODUCT_ALPHA",
        "lot_id": "A1000A.3",
        "step_id": "4500",
        "trigger": "_TRIGGER_PRODUCT_ALPHA_A1000A.3_4500",
    }


@pytest.mark.parametrize("value", ["", "PRODUCT_ONLY", "PRODUCT_LOT_BAD/STEP"])
def test_parse_key_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        auto_report.parse_key(value)


def test_product_keys_are_config_keys_when_config_exists(monkeypatch):
    monkeypatch.setattr(auto_report, "_config_data", lambda *args, **kwargs: {"PRODUCT_B": {}, "PRODUCT_A": {}})
    assert auto_report.product_keys() == ["PRODUCT_A", "PRODUCT_B"]


def test_preflight_resolves_vehicle_reformatter_from_product_config(tmp_path, monkeypatch):
    assets = tmp_path / "Auto report"
    assets.mkdir()
    for name in (*auto_report.REQUIRED_CODE, *auto_report.REQUIRED_ASSETS):
        (assets / name).write_text("placeholder", encoding="utf-8")
    (assets / "config.yaml").write_text("PRODUCT_KEY:\n  vehicle: ET_PRODUCT\n", encoding="utf-8")
    reformatter = tmp_path / "ET_PRODUCT_reformatter.csv"
    reformatter.write_text("CATEGORY,ITEMID,ALIAS\n", encoding="utf-8")
    monkeypatch.setattr(auto_report, "asset_dir", lambda: assets)
    monkeypatch.setattr(auto_report, "_find_reformatter", lambda value: reformatter if value == "ET_PRODUCT" else None)

    result = auto_report.preflight("PRODUCT_KEY")

    assert result["ok"] is True
    assert result["reformatter"] == str(reformatter)
    assert result["products"] == ["PRODUCT_KEY"]


def test_enqueue_persists_job_and_uses_async_queue_only(tmp_path, monkeypatch):
    from core import worker_dispatch

    submitted = []
    monkeypatch.setattr(auto_report, "_job_root", lambda: tmp_path / "auto_report")
    monkeypatch.setattr(auto_report, "preflight", lambda product="": {"ok": True, "missing": []})
    monkeypatch.setattr(
        worker_dispatch,
        "submit_async",
        lambda task_type, payload, **kwargs: submitted.append((task_type, payload, kwargs)) or {
            "ok": True,
            "task_id": "task-1",
            "worker_alive": False,
        },
    )

    job = auto_report.enqueue("PRODUCT_A1000A.3_4500", "engineer")

    assert job["state"] == "queued"
    assert job["task_id"] == "task-1"
    assert submitted[0][0] == auto_report.TASK_TYPE
    assert submitted[0][1] == {"job_id": job["id"]}
    assert auto_report.read_job(job["id"])["username"] == "engineer"


def test_worker_role_cannot_submit_an_async_job(monkeypatch):
    from core import worker_dispatch

    called = []
    monkeypatch.setattr(worker_dispatch, "server_role", lambda: "worker")
    monkeypatch.setattr(worker_dispatch, "_submit", lambda *args, **kwargs: called.append(True))

    result = worker_dispatch.submit_async("auto_report_generate", {"job_id": "job-1"})

    assert result["ok"] is False
    assert called == []


def test_auto_report_download_writes_shared_admin_history(tmp_path, monkeypatch):
    entries = []
    updates = []
    report = tmp_path / "report.pptx"
    report.write_bytes(b"pptx")
    monkeypatch.setattr(auto_report, "jsonl_append", lambda path, entry: entries.append((path, entry)))
    monkeypatch.setattr(auto_report, "update_job", lambda job_id, **fields: updates.append((job_id, fields)))

    auto_report.record_download(
        {"id": "job-1", "product": "PRODUCT", "key": "PRODUCT_LOT_STEP", "download_count": 0},
        report,
        "engineer",
    )

    assert entries[0][1]["source"] == "auto_report"
    assert entries[0][1]["username"] == "engineer"
    assert entries[0][1]["select_cols"] == "PPTX"
    assert updates[0][0] == "job-1"
    assert updates[0][1]["download_count"] == 1


def test_inline_stage_filters_by_discovered_root_lot(tmp_path, monkeypatch):
    source = tmp_path / "inline.parquet"
    con = duckdb.connect()
    con.execute(
        """COPY (SELECT * FROM (VALUES
          ('ROOT_A', 'A.1', '1', 'STEP', 'CD1', 1.2, current_timestamp),
          ('ROOT_B', 'B.1', '1', 'STEP', 'CD1', 2.3, current_timestamp)
        ) t(root_lot_id, lot_id, wafer_id, step_id, item_id, fab_value, tkout_time))
        TO ? (FORMAT PARQUET)""",
        [str(source)],
    )
    monkeypatch.setattr(auto_report, "_parquet_files", lambda kind, product: [source])

    output = auto_report._stage_inline(con, tmp_path / "runtime", "PRODUCT", ["A.1", "ROOT_A"])
    rows = con.execute("SELECT DISTINCT root_lot_id FROM read_csv_auto(?)", [str(output)]).fetchall()
    con.close()

    assert rows == [("ROOT_A",)]


def test_history_refresh_publishes_under_db_auto_report_run(tmp_path, monkeypatch):
    source = tmp_path / "et.parquet"
    assets = tmp_path / "DB" / "Auto report"
    assets.mkdir(parents=True)
    con = duckdb.connect()
    con.execute(
        """COPY (SELECT * FROM (VALUES
          ('LOT.1', 'LOT', '1', 'STEP', current_timestamp, 'ITEM', 1.5)
        ) t(fab_lot_id, root_lot_id, wafer_id, step_id, tkout_time, item_id, et_value))
        TO ? (FORMAT PARQUET)""",
        [str(source)],
    )
    con.close()
    monkeypatch.setattr(auto_report, "asset_dir", lambda: assets)
    monkeypatch.setattr(auto_report.PATHS, "data_root", tmp_path / "flow-data")
    monkeypatch.setattr(auto_report, "_parquet_files", lambda kind, product: [source])

    result = auto_report.refresh_history_product("PRODUCT", 30)

    expected = assets / "RUN" / "ET_HISTORY" / "PRODUCT" / "history.parquet"
    assert result["ok"] is True
    assert result["rows"] == 1
    assert expected.is_file()
    assert (expected.parent / "et_log.csv").is_file()


def test_durable_maintenance_is_queued_without_local_fallback(monkeypatch):
    from core import worker_dispatch

    local_calls = []
    monkeypatch.setattr(worker_dispatch, "server_role", lambda: "api")
    monkeypatch.setattr(worker_dispatch, "_find_deduped_task", lambda key: None)
    monkeypatch.setattr(worker_dispatch, "_queue_depth", lambda: 0)
    monkeypatch.setattr(worker_dispatch, "max_queue_depth", lambda: 10)
    monkeypatch.setattr(worker_dispatch, "worker_alive", lambda **kwargs: False)
    monkeypatch.setattr(worker_dispatch, "_bump", lambda key: None)
    monkeypatch.setattr(
        worker_dispatch,
        "_submit",
        lambda *args, **kwargs: ("task-queued", Path("task.json"), False),
    )

    result = worker_dispatch.run_heavy(
        "splittable_match_cache_refresh",
        {"product": "PRODUCT"},
        lambda: local_calls.append(True) or {"ok": True},
        durable=True,
        local_fallback=False,
        priority="maintenance",
    )

    assert result["ok"] is True
    assert result["queued"] is True
    assert result["task_id"] == "task-queued"
    assert local_calls == []


def test_auto_report_matching_and_et_tracker_share_serial_heavy_gate():
    from core import worker_dispatch

    assert {
        "auto_report_generate",
        "auto_report_history_refresh",
        "fab_matching_alert_scan",
        "et_tracker_scan",
    } <= worker_dispatch._CACHE_BUILD_TYPES


def test_auto_report_generation_is_rejected_on_api_role(monkeypatch):
    from core import worker_dispatch

    monkeypatch.setattr(worker_dispatch, "server_role", lambda: "api")

    result = auto_report.generate_job("job-that-must-not-run")

    assert result == {
        "ok": False,
        "error": "auto_report_generation_requires_development_worker",
    }


def test_matching_alert_scheduler_is_development_worker_only(monkeypatch):
    from core import fab_matching_alerts, worker_dispatch

    monkeypatch.setattr(worker_dispatch, "server_role", lambda: "api")
    assert fab_matching_alerts._scheduler_owner_enabled() is False
    monkeypatch.setattr(worker_dispatch, "server_role", lambda: "worker")
    assert fab_matching_alerts._scheduler_owner_enabled() is True


def test_auto_report_compat_api_routes_are_registered():
    from routers import auto_report as router_module

    paths = {route.path for route in router_module.match_router.routes}
    assert "/api/autoreport/config" in paths
    assert "/api/autoreport/jobs" in paths
    assert "/api/autoreport/jobs/{job_id}/download" in paths


def test_flow_adapter_has_no_bigdataquery_dependency():
    backend = Path(auto_report.__file__).resolve().parent
    combined = (backend / "auto_report.py").read_text("utf-8") + (backend / "auto_report_child.py").read_text("utf-8")
    assert "bigdataquery" not in combined.casefold()
