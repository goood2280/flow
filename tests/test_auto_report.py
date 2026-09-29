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


def _job_env(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_report, "_job_root", lambda: tmp_path / "auto_report")
    monkeypatch.setattr(auto_report, "preflight", lambda product="": {"ok": True, "missing": []})


def test_enqueue_persists_a_queued_job_without_running_it(tmp_path, monkeypatch):
    _job_env(tmp_path, monkeypatch)
    ran = []
    monkeypatch.setattr(auto_report, "generate_job", lambda job_id: ran.append(job_id))

    job = auto_report.enqueue("PRODUCT_A1000A.3_4500", "engineer")

    assert job["state"] == "queued"
    assert auto_report.read_job(job["id"])["username"] == "engineer"
    assert auto_report.queue_depth() == 1
    assert ran == []  # the HTTP request only writes the job file


def test_runner_takes_oldest_queued_job_through_heavy_jobs(tmp_path, monkeypatch):
    from core import heavy_jobs

    _job_env(tmp_path, monkeypatch)
    first = auto_report.enqueue("PRODUCT_A1000A.3_4500", "a")
    auto_report.update_job(first["id"], created_at="2026-01-01T00:00:00")
    second = auto_report.enqueue("PRODUCT_A1000B.3_4500", "b")
    auto_report.update_job(second["id"], created_at="2026-01-02T00:00:00")
    kinds = []

    def fake_run_heavy(kind, fn, **kwargs):
        kinds.append(kind)
        return fn()

    def fake_generate(job_id):
        auto_report.update_job(job_id, state="completed")
        return {"ok": True}

    monkeypatch.setattr(heavy_jobs, "run_heavy", fake_run_heavy)
    monkeypatch.setattr(auto_report, "generate_job", fake_generate)

    assert auto_report.run_next_job()["job_id"] == first["id"]
    assert auto_report.run_next_job()["job_id"] == second["id"]
    assert auto_report.run_next_job() is None
    assert kinds == [auto_report.TASK_TYPE, auto_report.TASK_TYPE]


def test_admission_refusal_keeps_the_job_queued(tmp_path, monkeypatch):
    from core import heavy_jobs

    _job_env(tmp_path, monkeypatch)
    job = auto_report.enqueue("PRODUCT_A1000A.3_4500", "a")
    monkeypatch.setattr(heavy_jobs, "run_heavy", lambda kind, fn, **kw: {
        "ok": False, "error": "local_heavy_memory_guard", "reason": "low_host_memory"})

    result = auto_report.run_next_job()

    assert result["deferred"] is True
    row = auto_report.read_job(job["id"])
    assert row["state"] == "queued" and "low_host_memory" in row["phase"]


def test_interrupted_job_is_requeued_once_then_failed(tmp_path, monkeypatch):
    _job_env(tmp_path, monkeypatch)
    job = auto_report.enqueue("PRODUCT_A1000A.3_4500", "a")
    auto_report.update_job(job["id"], state="running")

    assert auto_report.recover_interrupted_jobs() == [job["id"]]
    assert auto_report.read_job(job["id"])["state"] == "queued"

    auto_report.update_job(job["id"], state="running")
    auto_report.recover_interrupted_jobs()
    assert auto_report.read_job(job["id"])["state"] == "failed"


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


def test_auto_report_matching_and_et_tracker_share_serial_heavy_gate():
    from core import heavy_jobs

    assert {
        "auto_report_generate",
        "auto_report_history_refresh",
        "fab_matching_alert_scan",
        "et_tracker_scan",
    } <= heavy_jobs.CACHE_BUILD_KINDS


def test_matching_alert_scheduler_runs_only_in_the_background_owner(monkeypatch):
    from core import background_owner, fab_matching_alerts

    monkeypatch.setattr(background_owner, "is_owner", lambda: False)
    assert fab_matching_alerts._scheduler_owner_enabled() is False
    monkeypatch.setattr(background_owner, "is_owner", lambda: True)
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
