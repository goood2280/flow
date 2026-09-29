"""전용 대형 서버(128GB·8코어) SplitTable·다운로드 기본값.

단일 운영서버에서 첫 검색이 캐시 빌드를 기다리지 않도록 자동 제품 캐싱·응답 예열을
기본으로 켜고, 예열한 응답이 개수 상한에 밀려나지 않게 한다.
"""
import os
import time

import pytest

from routers import splittable


@pytest.fixture
def large(monkeypatch):
    monkeypatch.setattr(splittable, "_split_large_host", lambda: True)
    for name in ("FLOW_SPLITTABLE_VIEW_CACHE_MAX_ENTRIES", "FLOW_SPLITTABLE_VIEW_DISK_MAX_PER_PRODUCT",
                 "FLOW_SPLITTABLE_KNOB_PREWARM_MAX_LOTS", "FLOW_SPLITTABLE_AUTO_PRODUCT_CACHE_ENABLED"):
        monkeypatch.delenv(name, raising=False)


def test_small_host_keeps_previous_limits(monkeypatch):
    monkeypatch.setattr(splittable, "_split_large_host", lambda: False)
    monkeypatch.delenv("FLOW_SPLITTABLE_VIEW_CACHE_MAX_ENTRIES", raising=False)
    monkeypatch.delenv("FLOW_SPLITTABLE_VIEW_DISK_MAX_PER_PRODUCT", raising=False)
    monkeypatch.delenv("FLOW_SPLITTABLE_KNOB_PREWARM_MAX_LOTS", raising=False)
    assert splittable._view_cache_max_entries() == 512
    assert splittable._view_disk_cache_max_per_product() == 64
    assert splittable._knob_prewarm_max_lots() == 50


def test_large_host_raises_limits(large):
    assert splittable._view_cache_max_entries() == 20000
    assert splittable._view_disk_cache_max_per_product() == 4000
    assert splittable._knob_prewarm_max_lots() == 3000


def test_env_still_wins_on_large_host(large, monkeypatch):
    monkeypatch.setenv("FLOW_SPLITTABLE_VIEW_CACHE_MAX_ENTRIES", "300")
    monkeypatch.setenv("FLOW_SPLITTABLE_AUTO_PRODUCT_CACHE_ENABLED", "0")
    assert splittable._view_cache_max_entries() == 300
    assert splittable._auto_product_cache_enabled() is False


def test_auto_product_cache_defaults_on_only_for_large_host(monkeypatch):
    from core import cache_settings
    monkeypatch.delenv("FLOW_SPLITTABLE_AUTO_PRODUCT_CACHE_ENABLED", raising=False)
    monkeypatch.setattr(cache_settings, "_role_value", lambda key, is_dev: None)
    monkeypatch.setattr(splittable, "_split_large_host", lambda: True)
    assert splittable._auto_product_cache_enabled() is True
    monkeypatch.setattr(splittable, "_split_large_host", lambda: False)
    assert splittable._auto_product_cache_enabled() is False
    # 관리자가 저장한 '꺼짐'은 대형 서버에서도 그대로다.
    monkeypatch.setattr(splittable, "_split_large_host", lambda: True)
    monkeypatch.setattr(cache_settings, "_role_value",
                        lambda key, is_dev: False if key == "auto_product_cache_enabled" else None)
    assert splittable._auto_product_cache_enabled() is False


def test_recent_search_targets_rank_by_user_searches(monkeypatch):
    now = time.time()
    rows = [
        {"actor_type": "user_search", "product": "ML_TABLE_A", "root_lot_id": "R1", "ts": now - 50},
        {"actor_type": "user_search", "product": "ML_TABLE_A", "root_lot_id": "R2", "ts": now - 40},
        {"actor_type": "user_search", "product": "ML_TABLE_A", "root_lot_id": "R2", "ts": now - 30},
        # 예열·재검증 기록은 세지 않는다.
        {"actor_type": "prewarm", "product": "ML_TABLE_A", "root_lot_id": "R9", "ts": now - 1},
        {"actor_type": "user_search", "product": "", "root_lot_id": "R3", "ts": now},
    ]
    monkeypatch.setattr(splittable._search_timing_log, "_read_shared", lambda since: rows)
    assert splittable._knob_prewarm_recent_search_targets(10) == [("ML_TABLE_A", "R2"), ("ML_TABLE_A", "R1")]
    assert splittable._knob_prewarm_recent_search_targets(1) == [("ML_TABLE_A", "R2")]


def test_prewarm_requests_add_recent_roots_only_on_large_host(monkeypatch, tmp_path):
    monkeypatch.setattr(splittable.PATHS, "data_root", tmp_path)
    monkeypatch.setattr(splittable, "_knob_prewarm_targets", lambda: [("ML_TABLE_A", "P1")])
    monkeypatch.setattr(splittable, "_knob_prewarm_recent_search_targets",
                        lambda limit: [("ML_TABLE_A", "P1"), ("ML_TABLE_A", "S1")])
    monkeypatch.setattr(splittable, "_knob_prewarm_active_wip_targets", lambda limit: [("ML_TABLE_B", "W1")])
    monkeypatch.setattr(splittable, "_split_large_host", lambda: False)
    monkeypatch.delenv("FLOW_SPLITTABLE_KNOB_PREWARM_MAX_LOTS", raising=False)
    assert splittable._knob_prewarm_requests() == [("ML_TABLE_A", "P1", "")]
    monkeypatch.setattr(splittable, "_split_large_host", lambda: True)
    assert splittable._knob_prewarm_requests() == [
        ("ML_TABLE_A", "P1", ""), ("ML_TABLE_A", "S1", ""), ("ML_TABLE_B", "W1", "")]


def test_disk_prune_is_batched_and_keeps_newest(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOW_SPLITTABLE_VIEW_DISK_MAX_PER_PRODUCT", "10")
    splittable._VIEW_DISK_PRUNE_STATE.clear()
    folder = tmp_path / "PRODA"
    folder.mkdir()
    base = time.time() - 1000
    for i in range(30):
        fp = folder / f"{i:03d}.json.z"
        fp.write_bytes(b"x")
        os.utime(fp, (base + i, base + i))
    # 첫 호출은 정리(마지막 정리 시각이 0) → 최신 10개만 남는다.
    splittable._view_disk_cache_prune(folder)
    left = sorted(p.name for p in folder.glob("*.json.z"))
    assert left == [f"{i:03d}.json.z" for i in range(20, 30)]
    # 곧바로 다시 쓰면 폴더 전체 stat 을 건너뛴다(쓰기 몇 번은 한도를 잠깐 넘어도 된다).
    (folder / "extra.json.z").write_bytes(b"x")
    splittable._view_disk_cache_prune(folder)
    assert len(list(folder.glob("*.json.z"))) == 11


def test_download_queue_large_host_defaults(monkeypatch):
    from core import cache_budget, download_queue
    monkeypatch.delenv("FLOW_DOWNLOAD_JOB_WORKERS", raising=False)
    monkeypatch.delenv("FLOW_DOWNLOAD_JOB_MAX_PENDING", raising=False)
    monkeypatch.setattr(cache_budget, "large_host", lambda: True)
    assert download_queue.worker_count() == 2
    assert download_queue.max_pending() == 48
    monkeypatch.setattr(cache_budget, "large_host", lambda: False)
    assert download_queue.worker_count() == 1
    assert download_queue.max_pending() == download_queue.MAX_PENDING_DEFAULT
