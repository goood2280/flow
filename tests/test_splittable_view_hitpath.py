from __future__ import annotations

import sys
import time
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core import ml_table_lookup  # noqa: E402
from routers import splittable  # noqa: E402


def test_hit_path_skips_cache_status_and_audit_is_async(tmp_path, monkeypatch):
    product = "ML_TABLE_HITVERIFY"
    pl.DataFrame({
        "root_lot_id": ["RHV01", "RHV01"],
        "wafer_id": [1, 2],
        "KNOB_ALPHA": ["ON", "OFF"],
    }).write_parquet(tmp_path / f"{product}.parquet")
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    pl.DataFrame({
        "product": [product, product],
        "root_lot_id": ["RHV01", "RHV01"],
        "wafer_id": ["1", "2"],
        "lot_id": ["FHV01", "FHV01"],
        "step_id": ["", ""],
        "function_step": ["", ""],
        "tkout_time": ["2024-04-20T10:00:00", "2024-04-20T10:01:00"],
        "update_time": ["2024-04-20T10:02:00", "2024-04-20T10:02:00"],
    }).write_parquet(cache_root / "lot_progress_latest_lot_by_root_wafer.parquet")

    monkeypatch.setattr(splittable, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_db_base", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_enqueue_pivot_cache_build", lambda *a, **k: False)
    splittable._clear_split_view_cache()

    kw = dict(product=product, root_lot_id="RHV01", wafer_ids="", prefix="KNOB",
              custom_name="", view_mode="all", history_mode="all", fab_lot_id="", custom_cols="")

    # --- 1) async audit: 요청 경로가 _audit_user 를 동기 호출하지 않고 큐에 넣는지 ---
    # worker 를 정지시켜 큐가 비워지지 않게 한 뒤, enqueue 됐는지/동기 호출 안 됐는지 확인.
    import collections as _c
    fresh_q = _c.deque()
    monkeypatch.setattr(splittable, "_AUDIT_QUEUE", fresh_q)
    monkeypatch.setattr(splittable, "_ensure_audit_worker", lambda: None)  # worker 미기동
    audit_direct = []
    monkeypatch.setattr(splittable, "_audit_user", lambda *a, **k: audit_direct.append((a, k)))

    class _Req:
        headers = {"x-session-token": ""}
    monkeypatch.setattr(splittable, "_split_view_request_user", lambda r: ("tester", "user"))

    first = splittable.view_split(request=_Req(), **kw)  # miss → put
    assert first["view_cache"]["payload_cache_hit"] is False
    # 요청 스레드에서 _audit_user 를 동기 호출하지 않았고(파일 I/O 없음), 큐에만 적재.
    assert audit_direct == [], "audit must NOT be written synchronously on the request path"
    assert len(fresh_q) == 1, "audit must be enqueued to the async queue"

    # --- 2) HIT 경로가 무거운 cache_status(파티션 glob)를 호출하지 않는지 ---
    status_calls = {"n": 0}
    orig_status = ml_table_lookup.cache_status
    def _counting_status(fp):
        status_calls["n"] += 1
        return orig_status(fp)
    monkeypatch.setattr(ml_table_lookup, "cache_status", _counting_status)

    hit = splittable.view_split(request=_Req(), **kw)  # fresh hit
    assert hit["view_cache"]["payload_cache_hit"] is True
    assert hit["view_cache"]["stale"] is False
    assert status_calls["n"] == 0, "cache HIT must not recompute ml_table_lookup.cache_status"
    assert "lookup_cache" in hit  # 저장된 lookup_cache 를 그대로 서빙

    # --- 3) 전역 시그니처 stat 이 TTL 로 캐시되는지 (반복 HIT 시 재-stat 안 함) ---
    stat_paths = []
    orig_sig = splittable._path_cache_sig
    def _tracking_sig(p):
        stat_paths.append(str(p))
        return orig_sig(p)
    monkeypatch.setattr(splittable, "_path_cache_sig", _tracking_sig)
    stat_paths.clear()
    splittable.view_split(request=_Req(), **kw)
    global_files = {"source_config", "prefix_config", "precision_config", "rulebook",
                    "lot_wf_current", "lot_progress_latest", "match_cache_state"}
    n1 = sum(1 for p in stat_paths if any(g in p.lower() for g in global_files))
    stat_paths.clear()
    splittable.view_split(request=_Req(), **kw)  # TTL 창 안 두 번째 HIT
    n2 = sum(1 for p in stat_paths if any(g in p.lower() for g in global_files))
    assert n1 >= 0 and n2 == 0, f"global files must be TTL-cached on repeat HIT (n1={n1}, n2={n2})"
