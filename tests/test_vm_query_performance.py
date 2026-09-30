"""VM query throughput contracts, using synthetic data and no live services."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import threading

import httpx
import polars as pl
import pytest
from fastapi import FastAPI, HTTPException, Request


@pytest.mark.parametrize("path", ["query", "export-csv"])
def test_lot_location_work_does_not_block_health(monkeypatch, path):
    from routers import lot_location

    started = threading.Event()
    release = threading.Event()
    worker_ids = []
    payload = {"items": [{"lot_id": "DEMO", "wafer_id": "1"}], "stats": {"requested_count": 1}}

    def slow_query(lots, match_root=True):
        worker_ids.append(threading.get_ident())
        started.set()
        release.wait(3)
        return payload

    monkeypatch.setattr(lot_location, "query_lot_locations", slow_query)
    app = FastAPI()
    app.include_router(lot_location.router)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    async def exercise():
        event_loop_id = threading.get_ident()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            pending = asyncio.create_task(client.post(f"/api/lot-location/{path}", json={"lot_ids": ["DEMO"]}))
            try:
                assert await asyncio.to_thread(started.wait, 2)
                assert not pending.done(), "query completed before the event loop could serve another request"
                response = await asyncio.wait_for(client.get("/health"), 0.5)
                assert response.json() == {"status": "ok"}
                assert len(worker_ids) == 1 and worker_ids[0] != event_loop_id
            finally:
                release.set()
                result = await pending
            assert result.status_code == 200
            if path == "query":
                assert result.json() == payload
            else:
                assert result.content.startswith(b"\xef\xbb\xbf")
                assert "attachment" in result.headers["content-disposition"]

    asyncio.run(exercise())


@pytest.mark.parametrize("endpoint", ["base-file-view", "root-parquet-view", "view"])
def test_filebrowser_preview_serializes_in_worker_and_preserves_data(monkeypatch, tmp_path, endpoint):
    from core import json_fast, paths
    from routers import filebrowser

    product = tmp_path / "TEST_DB" / "DEMO"
    product.mkdir(parents=True)
    df = pl.DataFrame({"wafer_id": [1, 2], "value": [1.5, None], "label": ["한글", "test"]})
    df.write_parquet(tmp_path / "sample.parquet")
    df.write_parquet(product / "sample.parquet")
    monkeypatch.setattr(filebrowser, "_db_root", lambda: tmp_path)
    monkeypatch.setattr(filebrowser, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(paths, "_get_db_root", lambda: tmp_path)
    monkeypatch.setattr(paths, "_get_base_root", lambda: tmp_path)
    monkeypatch.setattr(filebrowser, "_require_filebrowser_user", lambda request: {"username": "demo", "role": "admin"})
    monkeypatch.setattr(filebrowser, "current_user", lambda request: {"username": "demo", "role": "admin"})
    monkeypatch.setattr(filebrowser, "_load_filebrowser_settings", lambda: {"preview_cache_enabled": False})
    recorded = []
    encoded = []
    real_dumps = json_fast.dumps_bytes

    def encode(payload):
        encoded.append((threading.get_ident(), payload))
        return real_dumps(payload)

    monkeypatch.setattr(json_fast, "dumps_bytes", encode)
    monkeypatch.setattr(filebrowser, "_record_filebrowser_sql_execution", lambda *args, **kwargs: recorded.append(kwargs))
    app = FastAPI()
    app.include_router(filebrowser.router)

    async def exercise():
        event_loop_id = threading.get_ident()
        params = {"meta_only": "false", "sql": "wafer_id >= 1"}
        params.update({"root": "TEST_DB", "product": "DEMO"} if endpoint == "view" else {"file": "sample.parquet"})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            result = await client.get(f"/api/filebrowser/{endpoint}", params=params)
        assert result.status_code == 200, result.text
        assert len(encoded) == 1 and encoded[0][0] != event_loop_id
        assert result.json() == json.loads(real_dumps(encoded[0][1]))
        assert result.json()["showing"] == 2
        assert {row["label"] for row in result.json()["data"]} == {"한글", "test"}
        assert len(recorded) == 1 and isinstance(recorded[0]["result"], dict)
        assert recorded[0]["result"]["showing"] == 2

    asyncio.run(exercise())


def test_preview_encoder_preserves_internal_calls_and_legacy_fallback(monkeypatch):
    from core import json_fast
    from routers import filebrowser

    payload = {"data": [{"date": dt.date(2026, 9, 30), "value": None}], "showing": 1}

    @filebrowser._preview_http_response
    def preview(request=None):
        return payload

    assert preview() is payload
    assert preview(request=object()) is payload
    request = Request({"type": "http"})
    assert json.loads(preview(request=request).body)["data"][0]["date"] == "2026-09-30"

    def unavailable(_payload):
        raise TypeError("legacy value")

    monkeypatch.setattr(json_fast, "dumps_bytes", unavailable)
    assert preview(request=request) is payload


def test_preview_optimization_preserves_access_failures():
    from routers import filebrowser

    @filebrowser._preview_http_response
    def denied(request=None):
        raise HTTPException(403, "denied")

    with pytest.raises(HTTPException) as exc:
        denied(request=object())
    assert exc.value.status_code == 403


@pytest.mark.parametrize("profile", ["auto", "large"])
def test_8_core_128_gib_capacity_defaults(monkeypatch, profile):
    from core import cache_budget, cache_settings, duckdb_engine, runtime_limits
    from core.paths import PATHS
    from app_v2.runtime import resource_guard

    for key in ("FLOW_CPU_BUDGET_CORES", "FLOW_PROCESS_MEMORY_LIMIT_GB", "FLOW_MEMORY_CEILING_GB",
                "FLOW_MEMORY_CEILING_FRACTION", "POLARS_MAX_THREADS", "FLOW_DUCKDB_THREADS",
                "FLOW_CACHE_TOTAL_BUDGET_FRACTION", "FLOW_CACHE_MEMORY_TARGET_RATIO",
                "FLOW_HEAVY_REQUEST_CONCURRENCY", "FLOW_ESSENTIAL_REQUEST_CONCURRENCY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("FLOW_RESOURCE_PROFILE", profile)
    monkeypatch.setattr(runtime_limits, "effective_cpu_count", lambda: 8.0)
    monkeypatch.setattr(runtime_limits, "system_memory_snapshot", lambda *a, **kw: {"system_memory_total_gb": 128.0})
    monkeypatch.setattr(resource_guard, "effective_cpu_count", lambda: 8.0)
    monkeypatch.setattr(PATHS, "is_prod", True)
    monkeypatch.setattr(cache_budget, "_is_dev", lambda: False)
    monkeypatch.setattr(cache_budget, "worker_budget_factor", lambda: 1.0)
    monkeypatch.setattr(cache_settings, "get_float_role", lambda *a, **kw: None)
    cache_budget.invalidate()
    try:
        assert runtime_limits.resource_profile() == "large"
        assert runtime_limits.cpu_budget_cores() == 8
        assert runtime_limits._default_polars_threads() == "8"
        assert duckdb_engine._thread_count() == 8
        assert runtime_limits.process_memory_limit_gb() == pytest.approx(99.8)
        assert cache_budget.pool_bytes() / 1024**3 == pytest.approx(61.44)
        guard = resource_guard.ResourceGuardMiddleware(lambda *args: None)
        assert guard._concurrency == 4 and guard._essential_concurrency == 8
    finally:
        cache_budget.invalidate()


def test_large_host_diagnostics_flags_old_limits_without_changing_them(monkeypatch):
    from core import runtime_limits

    monkeypatch.setenv("FLOW_RESOURCE_PROFILE", "large")
    monkeypatch.setenv("FLOW_CPU_BUDGET_CORES", "3")
    monkeypatch.setenv("POLARS_MAX_THREADS", "1")
    monkeypatch.setenv("FLOW_DUCKDB_THREADS", "2")
    monkeypatch.setenv("FLOW_PROCESS_MEMORY_LIMIT_GB", "8")
    monkeypatch.setattr(runtime_limits, "effective_cpu_count", lambda: 8.0)
    monkeypatch.setattr(runtime_limits, "system_memory_snapshot", lambda *a, **kw: {"system_memory_total_gb": 128.0})
    info, warnings = runtime_limits.host_diagnostics()
    assert info["cpu_budget"] == 3 and info["process_memory_limit_gb"] == 8
    for key in ("FLOW_CPU_BUDGET_CORES", "POLARS_MAX_THREADS", "FLOW_DUCKDB_THREADS", "FLOW_PROCESS_MEMORY_LIMIT_GB"):
        assert any(key in warning for warning in warnings)
