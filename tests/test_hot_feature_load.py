"""Server-load contracts for the three most used screens.

- ET 조회(/run): 캐시 미스 계산은 상주 계산 프로세스로 격리, 같은 조건은 1회 계산.
- TEG 위치 조회: 기준 파일 로더·지도 응답은 입력 지문이 같으면 다시 읽지/인코딩하지 않는다.
- SplitTable 공정 메타: 스냅샷이 그대로면 직렬화된 응답을 재사용한다.
"""
import importlib
import json
import os
import sys
import threading
import time
from pathlib import Path

import polars as pl
import pytest
from fastapi import HTTPException


BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core import et_run_service  # noqa: E402
from core import json_fast  # noqa: E402
from core import teg_map  # noqa: E402
from routers import reformatize as rf  # noqa: E402


# ── ET 조회: 부모 측 계약 ──────────────────────────────────────────────

WIDE = pl.DataFrame({
    "root_lot_id": ["L1", "L1"], "wafer_id": ["1", "2"],
    "shot_x": ["0", "0"], "shot_y": ["0", "1"], "IDX": [1.5, None],
})


@pytest.fixture
def et_env(monkeypatch, tmp_path):
    """_compute 가 실제 ET 파일 없이 캐시 키까지 도달하도록 입력만 고정한다."""
    csv = tmp_path / "V_reformatter.csv"
    csv.write_text("x", encoding="utf-8")
    monkeypatch.setattr(rf, "_find_csv", lambda product: csv)
    monkeypatch.setattr(rf, "_product_sig", lambda product: ((str(tmp_path / "d.parquet"), 1.0, 10),))
    monkeypatch.setattr(rf, "_settings", lambda: {"max_download_mb": 500, "page_rows": 100})
    monkeypatch.setenv("FLOW_REFORMATIZE_RUN_DIR", str(tmp_path / "run"))
    with rf._CACHE_LOCK:
        rf._CACHE.clear()
    yield tmp_path
    with rf._CACHE_LOCK:
        rf._CACHE.clear()


def test_isolated_compute_reads_child_result_caches_it_and_removes_file(et_env, monkeypatch):
    calls = []

    def fake_compute(job, progress=None):
        calls.append(job)
        progress("Index 계산 중")
        path = et_run_service.tmp_dir() / "job.arrow"
        WIDE.write_ipc(str(path), compression="uncompressed")
        return {"path": str(path), "out_cols": ["root_lot_id", "IDX"], "errors": ["e1"],
                "vehicle_csv": "V_reformatter.csv", "table": [{"alias": "IDX"}],
                "raw_rows": 7, "notice": "n", "rows": 2, "cols": 5}

    monkeypatch.setattr(et_run_service, "compute", fake_compute)
    monkeypatch.setattr(rf, "_compute_fresh", lambda *a, **k: pytest.fail("must not compute locally"))
    phases = []
    out = rf._compute("P", rf.Filters(lot_filter="L1"), auto_trim=True, isolate=True,
                      progress=lambda p, d=None, t=None: phases.append(p))

    assert out[0].equals(WIDE) and dict(out[0].schema) == dict(WIDE.schema)
    assert out[1:] == (["root_lot_id", "IDX"], ["e1"], "V_reformatter.csv", [{"alias": "IDX"}], 7, "n")
    assert calls[0]["filters"]["lot_filter"] == "L1" and calls[0]["auto_trim"] is True
    assert "Index 계산 중" in phases
    assert not list(et_run_service.tmp_dir().glob("*.arrow"))
    # 두 번째는 캐시 적중 — 자식에게 다시 가지 않는다.
    rf._compute("P", rf.Filters(lot_filter="L1"), auto_trim=True, isolate=True)
    assert len(calls) == 1


def test_identical_concurrent_requests_compute_once(et_env, monkeypatch):
    builds = []
    started = threading.Event()

    def slow_fresh(*args, **kwargs):
        builds.append(1)
        started.set()
        time.sleep(0.3)
        return WIDE, list(WIDE.columns), [], [], 2, ""

    monkeypatch.setattr(rf, "_compute_fresh", slow_fresh)
    results = []
    threads = [threading.Thread(target=lambda: results.append(
        rf._compute("P", rf.Filters(), auto_trim=True)[0].height)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert builds == [1]
    assert results == [2, 2, 2, 2]
    assert not rf._INFLIGHT


def test_follower_computes_itself_when_leader_fails(et_env, monkeypatch):
    attempts = []

    def flaky(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            time.sleep(0.2)
            raise HTTPException(400, "first fails")
        return WIDE, list(WIDE.columns), [], [], 2, ""

    monkeypatch.setattr(rf, "_compute_fresh", flaky)
    errors, oks = [], []

    def call():
        try:
            oks.append(rf._compute("P", rf.Filters(), auto_trim=True)[0].height)
        except HTTPException as exc:
            errors.append(exc.status_code)

    first = threading.Thread(target=call)
    first.start()
    time.sleep(0.05)
    second = threading.Thread(target=call)
    second.start()
    first.join(10)
    second.join(10)
    assert errors == [400] and oks == [2]
    assert not rf._INFLIGHT


def test_child_error_is_relayed_with_its_status(et_env, monkeypatch):
    def fail(job, progress=None):
        raise et_run_service.RunError(404, "조건에 맞는 ET 데이터가 없습니다")

    monkeypatch.setattr(et_run_service, "compute", fail)
    with pytest.raises(HTTPException) as info:
        rf._compute("P", rf.Filters(), auto_trim=True, isolate=True)
    assert info.value.status_code == 404
    assert "ET 데이터가 없습니다" in info.value.detail


def test_unavailable_service_falls_back_to_local_compute(et_env, monkeypatch):
    def unavailable(job, progress=None):
        raise et_run_service.ServiceUnavailable("spawn blocked")

    local = []
    monkeypatch.setattr(et_run_service, "compute", unavailable)
    monkeypatch.setattr(rf, "_compute_fresh",
                        lambda *a, **k: local.append(1) or (WIDE, list(WIDE.columns), [], [], 2, ""))
    out = rf._compute("P", rf.Filters(), auto_trim=True, isolate=True)
    assert local == [1] and out[0].height == 2


def test_isolation_can_be_turned_off(et_env, monkeypatch):
    monkeypatch.setenv("FLOW_REFORMATIZE_RUN_ISOLATION", "0")
    monkeypatch.setattr(et_run_service, "compute", lambda *a, **k: pytest.fail("disabled"))
    monkeypatch.setattr(rf, "_compute_fresh",
                        lambda *a, **k: (WIDE, list(WIDE.columns), [], [], 2, ""))
    assert rf._compute("P", rf.Filters(), auto_trim=True, isolate=True)[0].height == 2


def test_child_round_trip_relays_errors_from_a_real_process(monkeypatch, tmp_path):
    """spawn → import → 요청 → 오류 응답 왕복 (데이터 없이: 없는 제품)."""
    monkeypatch.setenv("FLOW_REFORMATIZE_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("FLOW_REFORMATIZE_RUN_PROCS", "1")
    et_run_service.shutdown()
    try:
        with pytest.raises(et_run_service.RunError) as info:
            et_run_service.compute({"product": "__NO_SUCH_PRODUCT__", "filters": {},
                                    "selected_items": None, "max_mb": 500, "auto_trim": True})
        assert info.value.status in (400, 404)
        st = et_run_service.status()
        assert any(slot["alive"] for slot in st["slots"])   # 오류 뒤에도 자식은 재사용된다
    finally:
        et_run_service.shutdown()


def test_child_modules_do_not_rewrite_parent_thread_env(monkeypatch):
    """부모(API)는 polars 가 이미 있으므로 자식용 스레드 고정이 env 를 덮지 않는다."""
    monkeypatch.setenv("POLARS_MAX_THREADS", "7")
    from core import reformatize_child, et_run_child
    importlib.reload(reformatize_child)
    importlib.reload(et_run_child)
    assert os.environ["POLARS_MAX_THREADS"] == "7"
    assert os.environ.get("FLOW_REFORMATIZE_IN_CHILD") != "1"
    assert et_run_service.enabled() or os.environ.get("FLOW_REFORMATIZE_RUN_ISOLATION") == "0"


# ── TEG 위치 조회 ─────────────────────────────────────────────────────

def test_teg_loader_is_read_once_per_file_fingerprint(monkeypatch, tmp_path):
    path = tmp_path / "Teg_location.csv"
    path.write_text("vehicle,teg,ebeam_x,ebeam_y\nP,T1,1,2\n", encoding="utf-8")
    monkeypatch.setattr(teg_map, "load_cfg", lambda: {"teg_file": str(path)})
    monkeypatch.setattr(teg_map, "resolve_path", lambda value: Path(value))
    reads = []
    real_read = teg_map._read_table
    monkeypatch.setattr(teg_map, "_read_table", lambda p: reads.append(p) or real_read(p))
    teg_map._invalidate_loader_cache()
    try:
        first, _ = teg_map.load_tegs()
        first.loc[:, "teg"] = "MUTATED"
        second, _ = teg_map.load_tegs()
        assert len(reads) == 1
        assert second["teg"].tolist() == ["T1"]

        path.write_text("vehicle,teg,ebeam_x,ebeam_y\nP,T1,1,2\nP,T2,3,4\n", encoding="utf-8")
        third, _ = teg_map.load_tegs()
        assert len(reads) == 2 and third["teg"].tolist() == ["T1", "T2"]

        teg_map._invalidate_loader_cache()
        teg_map.load_tegs()
        assert len(reads) == 3
    finally:
        teg_map._invalidate_loader_cache()


def test_teg_map_json_is_encoded_once_per_source_and_selection_limit(monkeypatch):
    builds = []
    signature = ["v1"]
    monkeypatch.setattr(teg_map, "load_cfg", lambda: {})
    monkeypatch.setattr(teg_map, "_teg_source_signature", lambda cfg: signature[0])
    monkeypatch.setattr(teg_map, "map_payload",
                        lambda veh: builds.append(veh) or {"vehicle": veh, "tegs": [{"teg": "T", "x": 1.5}]})
    teg_map._TEG_MAP_JSON_CACHE.clear()
    try:
        user_body = teg_map.map_payload_json("P", 20)
        assert teg_map.map_payload_json("P", 20) is user_body
        assert json.loads(user_body) == {"vehicle": "P", "tegs": [{"teg": "T", "x": 1.5}],
                                         "max_selection": 20}
        assert json.loads(teg_map.map_payload_json("P", None))["max_selection"] is None
        assert builds == ["P", "P"]
        signature[0] = "v2"
        teg_map.map_payload_json("P", 20)
        assert builds == ["P", "P", "P"]
    finally:
        teg_map._TEG_MAP_JSON_CACHE.clear()


def test_json_fast_matches_fastapi_encoding_and_refuses_unknown_types():
    import datetime as dt
    import numpy as np
    from fastapi.encoders import jsonable_encoder

    payload = {"a": [1, 2.5, None, "한글"], "t": dt.datetime(2026, 9, 25, 10, 0, 1),
               "n": np.int64(3), "f": np.float64(0.25), 1: "int-key"}
    decoded = json.loads(json_fast.dumps_bytes(payload))
    expected = jsonable_encoder({k: v for k, v in payload.items() if k not in ("n", "f")})
    assert decoded["a"] == expected["a"] and decoded["t"] == expected["t"]
    assert decoded["n"] == 3 and decoded["f"] == 0.25 and decoded["1"] == "int-key"
    with pytest.raises(TypeError):
        json_fast.dumps_bytes({"x": object()})


# ── SplitTable 공정 메타 ──────────────────────────────────────────────

def test_process_meta_http_reuses_bytes_until_snapshot_changes(monkeypatch):
    from routers import splittable

    snapshot = [{"knob": {"KNOB_A": {"groups": []}}}]
    monkeypatch.setattr(splittable, "_matching_meta_revision", lambda product: "rev1")
    monkeypatch.setattr(splittable, "_process_meta_snapshot", lambda product: snapshot[0])
    encodes = []
    real = json_fast.dumps_bytes
    monkeypatch.setattr(json_fast, "dumps_bytes", lambda payload: encodes.append(1) or real(payload))
    splittable._PROCESS_META_JSON_CACHE.clear()
    try:
        first = splittable.process_meta_http("P")
        second = splittable.process_meta_http("P")
        assert first.body == second.body and len(encodes) == 1
        assert json.loads(first.body) == splittable.process_meta("P")

        snapshot[0] = {"knob": {"KNOB_B": {"groups": []}}}      # 새 스냅샷 객체
        third = splittable.process_meta_http("P")
        assert len(encodes) == 2 and "KNOB_B" in json.loads(third.body)["items"]["knob"]
    finally:
        splittable._PROCESS_META_JSON_CACHE.clear()
