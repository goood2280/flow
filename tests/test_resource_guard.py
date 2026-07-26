from __future__ import annotations

import threading
import time
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app_v2.runtime import resource_guard  # noqa: E402


def _cpu_ok() -> dict:
    return {
        "process_cpu_cores": 0.0,
        "process_cpu_guard_cores": 4.0,
        "process_cpu_over_limit": False,
    }


def test_flowi_verify_and_workflow_catalog_are_default_light_paths(monkeypatch):
    monkeypatch.delenv("FLOW_LIGHT_API_PATHS", raising=False)

    paths = resource_guard._light_paths()

    assert "/api/llm/flowi/verify" in paths
    assert "/api/llm/flowi/workflows" in paths
    assert resource_guard._matches("/api/llm/flowi/verify", paths)
    assert resource_guard._matches("/api/llm/flowi/workflows", paths)
    assert not resource_guard._matches("/api/llm/flowi/chat", paths)


def test_splittable_view_bypasses_memory_guard_via_essential_lane(monkeypatch):
    # 스플릿테이블 불러오기는 큰 백그라운드 작업으로 메모리가 높아도 항상 열려 있어야 한다.
    monkeypatch.delenv("FLOW_LIGHT_API_PATHS", raising=False)
    monkeypatch.delenv("FLOW_HEAVY_API_PREFIXES", raising=False)
    monkeypatch.delenv("FLOW_ESSENTIAL_API_PREFIXES", raising=False)
    monkeypatch.setenv("FLOW_RESOURCE_GUARD_RECHECK_DELAY_SEC", "0")
    monkeypatch.setattr(resource_guard, "process_memory_high", lambda _reserve_gb: True)
    monkeypatch.setattr(
        resource_guard,
        "process_memory_snapshot",
        lambda: {"process_rss_gb": 12.0, "process_memory_over_limit": True},
    )
    monkeypatch.setattr(
        resource_guard,
        "process_cpu_snapshot",
        lambda guard_cores=None: {
            "process_cpu_cores": 4.6,
            "process_cpu_guard_cores": 4.0,
            "process_cpu_over_limit": True,
        },
    )

    app = FastAPI()
    calls = {"plain": 0, "cache_first": 0}

    @app.get("/api/splittable/view")
    def splittable_view(cache_first: int = 0):
        if cache_first:
            calls["cache_first"] += 1
        else:
            calls["plain"] += 1
        return {"ok": True, "cache_first": bool(cache_first)}

    app.add_middleware(resource_guard.ResourceGuardMiddleware)
    client = TestClient(app)

    plain = client.get("/api/splittable/view?product=ML_TABLE_PRODA&root_lot_id=A1000")
    cache_first = client.get("/api/splittable/view?product=ML_TABLE_PRODA&root_lot_id=A1000&cache_first=1")

    # 메모리/CPU 가드가 켜져 있어도 essential 레인으로 처리된다.
    assert plain.status_code == 200
    assert cache_first.status_code == 200
    assert plain.headers["X-Flow-Heavy-Request-Group"] == "essential"
    assert cache_first.headers["X-Flow-Heavy-Request-Group"] == "essential"
    assert calls == {"plain": 1, "cache_first": 1}


def test_splittable_lot_candidate_search_bypasses_memory_guard(monkeypatch):
    monkeypatch.delenv("FLOW_LIGHT_API_PATHS", raising=False)
    monkeypatch.delenv("FLOW_HEAVY_API_PREFIXES", raising=False)
    monkeypatch.delenv("FLOW_ESSENTIAL_API_PREFIXES", raising=False)
    monkeypatch.setenv("FLOW_RESOURCE_GUARD_RECHECK_DELAY_SEC", "0")
    monkeypatch.setattr(resource_guard, "process_memory_high", lambda _reserve_gb: True)
    monkeypatch.setattr(
        resource_guard,
        "process_memory_snapshot",
        lambda: {"process_rss_gb": 9.2, "process_memory_over_limit": False},
    )
    monkeypatch.setattr(resource_guard, "process_cpu_snapshot", lambda guard_cores=None: _cpu_ok())

    assert "/api/splittable/lot-candidates" not in resource_guard._light_paths()
    assert "/api/splittable/lot-ids" not in resource_guard._light_paths()

    app = FastAPI()
    called = False

    @app.get("/api/splittable/lot-candidates")
    def lot_candidates():
        nonlocal called
        called = True
        return {"ok": True}

    app.add_middleware(resource_guard.ResourceGuardMiddleware)
    client = TestClient(app)

    response = client.get("/api/splittable/lot-candidates?product=ML_TABLE_PRODA&col=root_lot_id")

    assert response.status_code == 200
    assert response.headers["X-Flow-Heavy-Request-Group"] == "essential"
    assert called is True


def test_filebrowser_view_bypasses_memory_guard(monkeypatch):
    monkeypatch.delenv("FLOW_ESSENTIAL_API_PREFIXES", raising=False)
    monkeypatch.setenv("FLOW_RESOURCE_GUARD_RECHECK_DELAY_SEC", "0")
    monkeypatch.setattr(resource_guard, "process_memory_high", lambda _reserve_gb: True)
    monkeypatch.setattr(
        resource_guard,
        "process_memory_snapshot",
        lambda: {"process_rss_gb": 12.0, "process_memory_over_limit": True},
    )
    monkeypatch.setattr(resource_guard, "process_cpu_snapshot", lambda guard_cores=None: _cpu_ok())

    app = FastAPI()

    @app.get("/api/filebrowser/view")
    def filebrowser_view():
        return {"ok": True}

    app.add_middleware(resource_guard.ResourceGuardMiddleware)
    client = TestClient(app)

    response = client.get("/api/filebrowser/view?path=/data/x.parquet")

    assert response.status_code == 200
    assert response.headers["X-Flow-Heavy-Request-Group"] == "essential"


def test_base_file_save_paths_bypass_memory_guard(monkeypatch):
    """단일 관리 파일(설정 CSV 등) 저장/검증/롤백은 메모리 가드와 무관하게 항상 처리된다.

    heavy 목록에 없어서 통과하는 암묵 동작이 아니라 light 계약으로 고정한다.
    """
    monkeypatch.delenv("FLOW_LIGHT_API_PATHS", raising=False)
    monkeypatch.delenv("FLOW_HEAVY_API_PREFIXES", raising=False)
    monkeypatch.setenv("FLOW_RESOURCE_GUARD_RECHECK_DELAY_SEC", "0")
    monkeypatch.setattr(resource_guard, "process_memory_high", lambda _reserve_gb: True)
    monkeypatch.setattr(
        resource_guard,
        "process_memory_snapshot",
        lambda: {"process_rss_gb": 12.0, "process_memory_over_limit": True},
    )
    monkeypatch.setattr(resource_guard, "process_cpu_snapshot", lambda guard_cores=None: _cpu_ok())

    for path in (
        "/api/filebrowser/base-file/save",
        "/api/filebrowser/base-file/text-save",
        "/api/filebrowser/base-file/validate",
        "/api/filebrowser/base-file/rollback",
    ):
        assert resource_guard._matches(path, resource_guard._light_paths()), path

    app = FastAPI()

    @app.post("/api/filebrowser/base-file/save")
    def base_file_save():
        return {"ok": True}

    app.add_middleware(resource_guard.ResourceGuardMiddleware)
    client = TestClient(app)

    response = client.post("/api/filebrowser/base-file/save", json={"file": "ppid_knob.csv"})

    assert response.status_code == 200
    # light 통과 — heavy/essential 레인 헤더가 붙지 않는다.
    assert "X-Flow-Heavy-Request-Group" not in response.headers


def test_root_scoped_splittable_download_bypasses_memory_guard(monkeypatch):
    """root lot 단위 다운로드(≤25행)는 메모리 가드와 무관하게 항상 동작해야 한다.

    root_lot_id 가 없는 제품 전체 다운로드는 기존 heavy 가드가 유지된다.
    """
    monkeypatch.delenv("FLOW_ESSENTIAL_API_PREFIXES", raising=False)
    monkeypatch.delenv("FLOW_HEAVY_API_PREFIXES", raising=False)
    monkeypatch.setenv("FLOW_RESOURCE_GUARD_RECHECK_DELAY_SEC", "0")
    monkeypatch.setattr(resource_guard, "process_memory_high", lambda _reserve_gb: True)
    monkeypatch.setattr(
        resource_guard,
        "process_memory_snapshot",
        lambda: {"process_rss_gb": 12.0, "process_memory_over_limit": True},
    )
    monkeypatch.setattr(resource_guard, "process_cpu_snapshot", lambda guard_cores=None: _cpu_ok())

    app = FastAPI()

    @app.get("/api/splittable/download-csv")
    def splittable_download_csv():
        return {"ok": True}

    @app.get("/api/splittable/download-xlsx")
    def splittable_download_xlsx():
        return {"ok": True}

    app.add_middleware(resource_guard.ResourceGuardMiddleware)
    client = TestClient(app)

    scoped_csv = client.get(
        "/api/splittable/download-csv?product=ML_TABLE_PRODA&root_lot_id=A1000&prefix=KNOB"
    )
    scoped_xlsx = client.get(
        "/api/splittable/download-xlsx?product=ML_TABLE_PRODA&root_lot_id=A1000&prefix=KNOB"
    )
    unscoped = client.get("/api/splittable/download-csv?product=ML_TABLE_PRODA&prefix=KNOB")

    assert scoped_csv.status_code == 200
    assert scoped_csv.headers["X-Flow-Heavy-Request-Group"] == "essential"
    assert scoped_xlsx.status_code == 200
    assert scoped_xlsx.headers["X-Flow-Heavy-Request-Group"] == "essential"
    # 전체 다운로드는 메모리 부족 상황에서 기존대로 거절된다.
    assert unscoped.status_code == 503
    assert unscoped.json()["error_code"] == "resource_memory_guard"


def test_essential_lane_serializes_within_reserved_concurrency(monkeypatch):
    monkeypatch.delenv("FLOW_ESSENTIAL_API_PREFIXES", raising=False)
    monkeypatch.setenv("FLOW_ESSENTIAL_REQUEST_CONCURRENCY", "1")
    monkeypatch.setenv("FLOW_ESSENTIAL_REQUEST_QUEUE_TIMEOUT_SEC", "3")
    monkeypatch.setattr(resource_guard, "process_memory_high", lambda _reserve_gb: False)
    monkeypatch.setattr(resource_guard, "process_cpu_snapshot", lambda guard_cores=None: _cpu_ok())

    app = FastAPI()
    lock = threading.Lock()
    active = 0
    max_active = 0

    @app.get("/api/splittable/view")
    def splittable_view():
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.2)
        with lock:
            active -= 1
        return {"ok": True}

    app.add_middleware(resource_guard.ResourceGuardMiddleware)

    # 단일 이벤트 루프 공유(프로덕션과 동일 조건)로 예약 레인의 직렬화를 결정적으로 검증한다.
    with TestClient(app) as client:
        threads = [
            threading.Thread(target=lambda: client.get("/api/splittable/view"), daemon=True)
            for _ in range(3)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5.0)

    assert max_active == 1


def test_essential_concurrency_scales_with_cores(monkeypatch):
    monkeypatch.delenv("FLOW_ESSENTIAL_REQUEST_CONCURRENCY", raising=False)
    monkeypatch.setattr(resource_guard, "effective_cpu_count", lambda: 2)
    assert resource_guard._auto_essential_concurrency() == 1
    monkeypatch.setattr(resource_guard, "effective_cpu_count", lambda: 5)
    assert resource_guard._auto_essential_concurrency() == 2
    monkeypatch.setattr(resource_guard, "effective_cpu_count", lambda: 12)
    assert resource_guard._auto_essential_concurrency() == 3


def test_flowi_verify_and_workflow_catalog_bypass_heavy_middleware(monkeypatch):
    monkeypatch.delenv("FLOW_LIGHT_API_PATHS", raising=False)
    monkeypatch.delenv("FLOW_HEAVY_API_PREFIXES", raising=False)
    monkeypatch.setattr(resource_guard, "process_memory_high", lambda _reserve_gb: False)
    monkeypatch.setattr(resource_guard, "process_cpu_snapshot", lambda guard_cores=None: _cpu_ok())

    app = FastAPI()

    @app.post("/api/llm/flowi/verify")
    def verify():
        return {"ok": True}

    @app.get("/api/llm/flowi/workflows")
    def workflows():
        return {"ok": True}

    @app.post("/api/llm/flowi/chat")
    def chat():
        return {"ok": True}

    app.add_middleware(resource_guard.ResourceGuardMiddleware)
    client = TestClient(app)

    verify_response = client.post("/api/llm/flowi/verify", json={})
    workflows_response = client.get("/api/llm/flowi/workflows")
    chat_response = client.post("/api/llm/flowi/chat", json={})

    assert verify_response.status_code == 200
    assert workflows_response.status_code == 200
    assert chat_response.status_code == 200
    assert "X-Flow-Heavy-Request-Concurrency" not in verify_response.headers
    assert "X-Flow-Heavy-Request-Concurrency" not in workflows_response.headers
    assert "X-Flow-Heavy-Request-Concurrency" in chat_response.headers


def test_flowi_chat_does_not_wait_behind_generic_heavy_request(monkeypatch):
    monkeypatch.delenv("FLOW_LIGHT_API_PATHS", raising=False)
    monkeypatch.delenv("FLOW_HEAVY_API_PREFIXES", raising=False)
    monkeypatch.setenv("FLOW_HEAVY_REQUEST_CONCURRENCY", "1")
    monkeypatch.setenv("FLOW_HEAVY_REQUEST_QUEUE_TIMEOUT_SEC", "1")
    monkeypatch.setenv("FLOW_FLOWI_CHAT_CONCURRENCY", "1")
    monkeypatch.setenv("FLOW_FLOWI_CHAT_QUEUE_TIMEOUT_SEC", "1")
    monkeypatch.setattr(resource_guard, "process_memory_high", lambda _reserve_gb: False)
    monkeypatch.setattr(resource_guard, "process_cpu_snapshot", lambda guard_cores=None: _cpu_ok())

    app = FastAPI()
    blocker_started = threading.Event()

    @app.get("/api/dashboard/block")
    def dashboard_block():
        blocker_started.set()
        time.sleep(1.5)
        return {"ok": True}

    @app.post("/api/llm/flowi/chat")
    def flowi_chat():
        return {"ok": True}

    app.add_middleware(resource_guard.ResourceGuardMiddleware)
    client = TestClient(app)

    thread = threading.Thread(target=lambda: client.get("/api/dashboard/block"), daemon=True)
    thread.start()
    assert blocker_started.wait(timeout=1.0)

    response = client.post("/api/llm/flowi/chat", json={})
    thread.join(timeout=3.0)

    assert response.status_code == 200
    assert response.headers["X-Flow-Heavy-Request-Group"] == "flowi_chat"


def test_small_profile_heavy_requests_are_sequential_by_default(monkeypatch):
    monkeypatch.setenv("FLOW_RESOURCE_PROFILE", "small")
    monkeypatch.delenv("FLOW_HEAVY_REQUEST_CONCURRENCY", raising=False)
    monkeypatch.delenv("FLOW_HEAVY_API_PREFIXES", raising=False)
    monkeypatch.setenv("FLOW_HEAVY_REQUEST_QUEUE_TIMEOUT_SEC", "2")
    monkeypatch.setattr(resource_guard, "process_memory_high", lambda _reserve_gb: False)
    monkeypatch.setattr(resource_guard, "process_cpu_snapshot", lambda guard_cores=None: _cpu_ok())

    app = FastAPI()
    lock = threading.Lock()
    active = 0
    max_active = 0

    @app.get("/api/dashboard/slow")
    def dashboard_slow():
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.2)
        with lock:
            active -= 1
        return {"ok": True}

    app.add_middleware(resource_guard.ResourceGuardMiddleware)

    # 하나의 이벤트 루프를 공유하도록 컨텍스트 매니저로 TestClient 를 연다. 컨텍스트
    # 없이 스레드마다 TestClient 를 호출하면 요청마다 별도 이벤트 루프/스레드가 떠서
    # asyncio.Semaphore(단일 루프 전제) 의 _value 를 경합하게 되어, 프로덕션(단일 uvicorn
    # 루프)과 무관하게 결과가 타이밍에 따라 흔들린다.
    with TestClient(app) as client:
        threads = [threading.Thread(target=lambda: client.get("/api/dashboard/slow"), daemon=True) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3.0)

    assert max_active == 1


def test_heavy_request_delays_then_blocks_when_cpu_stays_high(monkeypatch):
    monkeypatch.setenv("FLOW_RESOURCE_PROFILE", "small")
    monkeypatch.setenv("FLOW_RESOURCE_GUARD_RECHECK_DELAY_SEC", "0.01")
    monkeypatch.setattr(resource_guard, "process_memory_high", lambda _reserve_gb: False)
    monkeypatch.setattr(
        resource_guard,
        "process_cpu_snapshot",
        lambda guard_cores=None: {
            "process_cpu_cores": 4.6,
            "process_cpu_guard_cores": 4.0,
            "process_cpu_over_limit": True,
        },
    )

    app = FastAPI()
    called = False

    @app.get("/api/dashboard/heavy")
    def dashboard_heavy():
        nonlocal called
        called = True
        return {"ok": True}

    app.add_middleware(resource_guard.ResourceGuardMiddleware)
    client = TestClient(app)

    response = client.get("/api/dashboard/heavy")

    assert response.status_code == 429
    assert response.json()["error_code"] == "resource_cpu_guard"
    assert response.headers["Retry-After"] == "15"
    assert called is False


def test_heavy_request_proceeds_when_memory_recovers_after_delay(monkeypatch):
    monkeypatch.setenv("FLOW_RESOURCE_PROFILE", "small")
    monkeypatch.setenv("FLOW_RESOURCE_GUARD_RECHECK_DELAY_SEC", "0.01")
    memory_checks = iter([True, False, False])
    monkeypatch.setattr(resource_guard, "process_memory_high", lambda _reserve_gb: next(memory_checks))
    monkeypatch.setattr(resource_guard, "process_cpu_snapshot", lambda guard_cores=None: _cpu_ok())

    app = FastAPI()

    @app.get("/api/dashboard/heavy")
    def dashboard_heavy():
        return {"ok": True}

    app.add_middleware(resource_guard.ResourceGuardMiddleware)
    client = TestClient(app)

    response = client.get("/api/dashboard/heavy")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
