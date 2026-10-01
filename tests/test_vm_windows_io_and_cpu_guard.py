"""Windows VM regressions: file swaps under readers, CPU-guard sampling, dev-mode warning."""
import os
import threading
import time

import pytest

from core import file_transaction, runtime_limits
from app_v2.runtime import resource_guard


def test_replace_file_waits_out_a_transient_windows_sharing_violation(monkeypatch, tmp_path):
    src = tmp_path / "new.json"
    dst = tmp_path / "state.json"
    src.write_text("new", "utf-8")
    dst.write_text("old", "utf-8")
    real_replace = os.replace
    calls = []

    def flaky_replace(a, b):
        calls.append(1)
        if len(calls) < 3:  # a reader still holds dst (WinError 5)
            raise PermissionError(13, "Access is denied")
        real_replace(a, b)

    monkeypatch.setattr(file_transaction, "_IS_WINDOWS", True)
    monkeypatch.setattr(file_transaction.os, "replace", flaky_replace)
    file_transaction.replace_file(src, dst)

    assert len(calls) == 3
    assert dst.read_text("utf-8") == "new" and not src.exists()


def test_replace_file_gives_up_after_the_retry_window(monkeypatch, tmp_path):
    def always_locked(a, b):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(file_transaction, "_IS_WINDOWS", True)
    monkeypatch.setattr(file_transaction.os, "replace", always_locked)
    started = time.monotonic()
    with pytest.raises(PermissionError):
        file_transaction.replace_file(tmp_path / "a", tmp_path / "b", timeout=0.05)
    assert time.monotonic() - started < 1.0


def test_replace_file_does_not_retry_off_windows(monkeypatch, tmp_path):
    calls = []

    def locked(a, b):
        calls.append(1)
        raise PermissionError(13, "denied")

    monkeypatch.setattr(file_transaction, "_IS_WINDOWS", False)
    monkeypatch.setattr(file_transaction.os, "replace", locked)
    with pytest.raises(PermissionError):
        file_transaction.replace_file(tmp_path / "a", tmp_path / "b")
    assert calls == [1]


@pytest.mark.skipif(os.name != "nt", reason="real Windows sharing semantics")
def test_save_json_lands_while_a_reader_briefly_holds_the_file(tmp_path):
    from core.utils import load_json, save_json

    path = tmp_path / "state.json"
    save_json(path, {"v": 0})
    handle = open(path, "r", encoding="utf-8")  # Python readers block MoveFileEx on Windows
    releaser = threading.Timer(0.2, handle.close)
    releaser.start()
    try:
        save_json(path, {"v": 1})
    finally:
        releaser.join()
        handle.close()
    assert load_json(path) == {"v": 1}


def _fake_cpu(monkeypatch, clock, cpu):
    monkeypatch.setattr(runtime_limits, "_read_process_cpu_seconds", lambda: cpu[0])
    monkeypatch.setattr(runtime_limits.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runtime_limits, "_PROCESS_CPU_LAST", {"cpu_seconds": 0.0, "wall": 0.0, "percent": 0.0})
    monkeypatch.setattr(runtime_limits, "cpu_budget_cores", lambda: 8.0)
    monkeypatch.setattr(runtime_limits, "effective_cpu_count", lambda: 8.0)
    monkeypatch.delenv("FLOW_PROCESS_CPU_GUARD_CORES", raising=False)


def test_cpu_snapshot_ignores_sub_tick_windows(monkeypatch):
    clock, cpu = [100.0], [10.0]
    _fake_cpu(monkeypatch, clock, cpu)
    runtime_limits.process_cpu_snapshot()                     # baseline
    clock[0] += 1.0
    cpu[0] += 2.0                                             # 2 cores busy over 1 s
    first = runtime_limits.process_cpu_snapshot()
    assert first["process_cpu_cores"] == pytest.approx(2.0)
    # The next heavy request 2 ms later sees one 15.6 ms tick of 8 threads land
    # (0.125 CPU-seconds). Measured over 2 ms that used to read as 62 cores.
    clock[0] += 0.002
    cpu[0] += 0.125
    burst = runtime_limits.process_cpu_snapshot()
    assert burst["process_cpu_cores"] == pytest.approx(2.0)
    assert burst["process_cpu_over_limit"] is False
    # The baseline was kept, so the next full window includes that CPU time.
    clock[0] += 0.998
    cpu[0] += 1.875
    later = runtime_limits.process_cpu_snapshot()
    assert later["process_cpu_cores"] == pytest.approx(2.0)


def test_cpu_snapshot_still_flags_real_saturation(monkeypatch):
    clock, cpu = [50.0], [0.0]
    _fake_cpu(monkeypatch, clock, cpu)
    runtime_limits.process_cpu_snapshot()
    clock[0] += 1.0
    cpu[0] += 8.0
    assert runtime_limits.process_cpu_snapshot()["process_cpu_over_limit"] is True


def test_splittable_page_config_reads_bypass_the_heavy_lane():
    light = resource_guard._light_paths()
    for path in ("/api/splittable/display-settings", "/api/splittable/category-colors",
                 "/api/splittable/product-order", "/api/splittable/related-issues"):
        assert any(path.startswith(prefix) for prefix in light), path
    # The table read itself keeps its self-gated essential lane.
    assert "/api/splittable/view" in resource_guard.DEFAULT_SELF_GATED_PATHS


def test_host_diagnostics_warns_when_a_large_host_runs_in_dev_mode(monkeypatch):
    from core.paths import PATHS

    monkeypatch.setattr(runtime_limits, "system_memory_snapshot", lambda *a, **k: {"system_memory_total_gb": 128.0})
    monkeypatch.setattr(runtime_limits, "effective_cpu_count", lambda: 8.0)
    monkeypatch.setattr(runtime_limits, "resource_profile", lambda: "large")
    monkeypatch.setattr(PATHS, "is_prod", False)
    info, warnings = runtime_limits.host_diagnostics({})
    assert info["prod"] is False
    assert any("FLOW_PROD=1" in w for w in warnings)

    monkeypatch.setattr(PATHS, "is_prod", True)
    info, warnings = runtime_limits.host_diagnostics({})
    assert info["prod"] is True
    assert not any("FLOW_PROD=1" in w for w in warnings)


def test_lease_read_retries_a_windows_sharing_overlap(monkeypatch, tmp_path):
    from core import shared_lease

    path = tmp_path / "owner.lock.json"
    path.write_text('{"owner": "me"}', "utf-8")
    real_read = type(path).read_text
    calls = []

    def flaky_read(self, *args, **kwargs):
        calls.append(1)
        if len(calls) < 3:  # the renewal's os.replace is in progress
            raise PermissionError(13, "Access is denied")
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(type(path), "read_text", flaky_read)
    monkeypatch.setattr(shared_lease.time, "sleep", lambda _s: None)
    # Before: the first PermissionError read as "no owner" and dropped ownership.
    assert shared_lease._read(path) == {"owner": "me"}
    assert len(calls) == 3


def test_large_host_caps_background_yield_while_small_host_keeps_it(monkeypatch):
    from core import cache_budget, request_priority

    monkeypatch.delenv("FLOW_BACKGROUND_YIELD_MAX_WAIT_SEC", raising=False)
    monkeypatch.setattr(request_priority, "users_active", lambda quiet_for_sec=None: True)
    monkeypatch.setattr(request_priority.time, "sleep", lambda _s: None)

    monkeypatch.setattr(cache_budget, "large_host", lambda: True)
    assert request_priority.yield_to_users(max_wait_sec=60.0) == pytest.approx(3.0)
    assert request_priority.background_yield_cap(1.0) == 1.0

    monkeypatch.setattr(cache_budget, "large_host", lambda: False)
    assert request_priority.yield_to_users(max_wait_sec=60.0) == pytest.approx(60.0)

    monkeypatch.setenv("FLOW_BACKGROUND_YIELD_MAX_WAIT_SEC", "7")
    monkeypatch.setattr(cache_budget, "large_host", lambda: True)
    assert request_priority.yield_to_users(max_wait_sec=60.0) == pytest.approx(7.0)


def test_filebrowser_cached_sql_view_does_not_wait_behind_another_users_scan(monkeypatch, tmp_path):
    import asyncio
    import httpx
    import polars as pl
    from fastapi import FastAPI
    from core import filebrowser_query_queue as sql_queue, paths
    from routers import filebrowser

    product = tmp_path / "TEST_DB" / "DEMO"
    product.mkdir(parents=True)
    pl.DataFrame({"wafer_id": [1, 2, 3], "value": [1.5, 2.5, 3.5]}).write_parquet(product / "part.parquet")
    monkeypatch.setattr(filebrowser, "_db_root", lambda: tmp_path)
    monkeypatch.setattr(filebrowser, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(paths, "_get_db_root", lambda: tmp_path)
    monkeypatch.setattr(paths, "_get_base_root", lambda: tmp_path)
    monkeypatch.setattr(filebrowser, "_require_filebrowser_user", lambda request: {"username": "demo", "role": "admin"})
    monkeypatch.setattr(filebrowser, "current_user", lambda request: {"username": "demo", "role": "admin"})
    monkeypatch.setattr(filebrowser, "_load_filebrowser_settings", lambda: {"preview_cache_enabled": True})
    monkeypatch.setattr(filebrowser, "_record_filebrowser_sql_execution", lambda *a, **k: None)
    monkeypatch.setattr(sql_queue, "stale_seconds", lambda: 10.0)
    app = FastAPI()
    app.include_router(filebrowser.router)
    params = {"root": "TEST_DB", "product": "DEMO", "meta_only": "false", "sql": "wafer_id >= 2",
              "query_session": "page-1"}

    held = threading.Event()
    release = threading.Event()

    def other_users_long_scan():
        with sql_queue.execute(username="other", session_id="s-other", query_id="q", query_key="k"):
            held.set()
            release.wait(10)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            first = await client.get("/api/filebrowser/view", params=params)
            assert first.status_code == 200, first.text
            assert not first.json().get("preview_cache_hit")
            blocker = threading.Thread(target=other_users_long_scan, daemon=True)
            blocker.start()
            assert await asyncio.to_thread(held.wait, 2)
            try:
                started = time.monotonic()
                again = await asyncio.wait_for(client.get("/api/filebrowser/view", params=params), 3)
                waited = time.monotonic() - started
            finally:
                release.set()
            assert again.status_code == 200, again.text
            assert again.json().get("preview_cache_hit") is True
            assert again.json()["showing"] == first.json()["showing"] == 2
            assert waited < 1.0

    asyncio.run(exercise())


def test_host_diagnostics_names_missing_fast_path_packages(monkeypatch):
    monkeypatch.setattr(runtime_limits, "_module_available", lambda name: name != "orjson")
    info, warnings = runtime_limits.host_diagnostics({})
    assert info["duckdb"] is True and info["orjson"] is False
    assert any("pip install orjson" in w for w in warnings)
    assert not any("pip install duckdb" in w for w in warnings)


def test_wip_cache_freshness_matches_ml_table_names_to_fab_products():
    from core import lot_progress_cache as lpc

    state = {"items": [{"product": "PRODA"}, {"product": "prodb"}]}
    # The product rotation asks with ML_TABLE_* names; the cache rows carry FAB names.
    assert lpc._state_has_products(state, ["ML_TABLE_PRODA"]) is True
    assert lpc._state_has_products(state, ["ml_table_prodb", "PRODA"]) is True
    assert lpc._state_has_products(state, ["ML_TABLE_PRODC"]) is False
    assert lpc._state_has_products(state, []) is True
