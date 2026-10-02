"""SplitTable metadata/list HTTP contracts under synthetic server pressure."""

from pathlib import Path
import shutil
import subprocess

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient


@pytest.mark.parametrize("pressure", ["memory", "cpu"])
def test_screen_resources_survive_pressure_while_heavy_work_is_guarded(monkeypatch, pressure):
    from app_v2.runtime import resource_guard
    from core import json_fast
    from routers import splittable

    monkeypatch.setenv("FLOW_RESOURCE_GUARD_RECHECK_DELAY_SEC", "0")
    monkeypatch.delenv("FLOW_LIGHT_API_PATHS", raising=False)
    monkeypatch.delenv("FLOW_HEAVY_API_PREFIXES", raising=False)
    monkeypatch.setattr(resource_guard, "process_memory_high", lambda *_: pressure == "memory")
    monkeypatch.setattr(resource_guard, "process_memory_snapshot", lambda: {})
    monkeypatch.setattr(resource_guard, "process_cpu_snapshot", lambda: {"process_cpu_over_limit": pressure == "cpu"})
    monkeypatch.setattr(resource_guard, "_trigger_emergency_evict", lambda *_: None)
    monkeypatch.setattr(splittable, "_root_lot_pool", lambda _: {
        "values": ["R0001", "R0002"], "complete": True, "cached": "ram", "meta": {},
    })
    monkeypatch.setattr(splittable, "_matching_meta_revision", lambda _: "test-revision")
    monkeypatch.setattr(splittable, "_process_meta_snapshot", lambda _: {
        "knob": {"KNOB_TEST": {"step_ids": ["STEP1"]}},
    })

    # Use the real endpoints, Query defaults and production JSON wrapper without
    # changing the shared router or starting background services.
    router = APIRouter(prefix="/api/splittable")
    router.add_api_route("/lot-candidates", splittable.get_lot_candidates)
    router.add_api_route("/process-meta", splittable.process_meta_http)
    router.add_api_route("/heavy-test", lambda: {"ok": True})
    json_fast.wrap_router_endpoints(router)
    app = FastAPI()
    app.add_middleware(resource_guard.ResourceGuardMiddleware)
    app.include_router(router)
    client = TestClient(app)

    candidates = client.get("/api/splittable/lot-candidates", params={
        "product": "ML_TABLE_TEST", "col": "root_lot_id", "limit": 50000,
    })
    assert candidates.status_code == 200, candidates.text
    assert candidates.json()["candidates"] == ["R0001", "R0002"]
    metadata = client.get("/api/splittable/process-meta?product=ML_TABLE_TEST")
    assert metadata.status_code == 200, metadata.text
    assert metadata.json()["items"]["knob"]["KNOB_TEST"]["step_ids"] == ["STEP1"]
    assert client.get("/api/splittable/heavy-test").status_code == (503 if pressure == "memory" else 429)


def test_frontend_request_recovery():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for frontend request tests")
    script = Path(__file__).with_name("splittable_resource_recovery.mjs")
    result = subprocess.run([node, "--test", str(script)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
