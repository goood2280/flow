"""KNOB prewarm in a separate process + view caches that survive no-op cache rebuilds."""
from __future__ import annotations

import types

import polars as pl
import pytest


def test_process_mode_defaults_to_large_production_hosts_only(monkeypatch):
    from core import cache_budget, splittable_prewarm_process as spp

    for name in ("FLOW_SPLITTABLE_PREWARM_PROCESS", "FLOW_PREWARM_CHILD", "FLOW_REFORMATIZE_IN_CHILD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cache_budget, "large_host", lambda: True)
    assert spp.enabled() is True
    monkeypatch.setattr(cache_budget, "large_host", lambda: False)
    assert spp.enabled() is False
    monkeypatch.setenv("FLOW_SPLITTABLE_PREWARM_PROCESS", "1")
    assert spp.enabled() is True
    monkeypatch.setattr(cache_budget, "large_host", lambda: True)
    monkeypatch.setenv("FLOW_SPLITTABLE_PREWARM_PROCESS", "0")
    assert spp.enabled() is False
    # the child itself never spawns another child
    monkeypatch.setenv("FLOW_SPLITTABLE_PREWARM_PROCESS", "1")
    monkeypatch.setenv("FLOW_PREWARM_CHILD", "1")
    assert spp.enabled() is False


def test_child_threads_default_to_half_the_cores(monkeypatch):
    from core import runtime_limits, splittable_prewarm_process as spp

    monkeypatch.delenv("FLOW_SPLITTABLE_PREWARM_THREADS", raising=False)
    monkeypatch.setattr(runtime_limits, "effective_cpu_count", lambda: 8.0)
    assert spp.child_threads() == 4
    monkeypatch.setenv("FLOW_SPLITTABLE_PREWARM_THREADS", "2")
    assert spp.child_threads() == 2


def test_child_computes_but_never_queues_cache_builds(monkeypatch):
    from core import ml_table_lookup, splittable_prewarm_process as spp

    fake = types.SimpleNamespace(
        _enqueue_pivot_cache_build=lambda *a, **k: True,
        _enqueue_fab_lot_index_build=lambda *a, **k: True,
        _enqueue_latest_lot_index_build=lambda *a, **k: True,
    )
    monkeypatch.setattr(ml_table_lookup, "enqueue_build", ml_table_lookup.enqueue_build)
    spp._compute_only(fake)
    assert fake._enqueue_pivot_cache_build("ML_TABLE_X", immediate=True) is False
    assert fake._enqueue_fab_lot_index_build("ML_TABLE_X") is False
    assert fake._enqueue_latest_lot_index_build() is False
    assert ml_table_lookup.enqueue_build("x.parquet")["queued"] is False


def test_supervisor_falls_back_to_thread_when_spawn_fails(monkeypatch):
    import multiprocessing
    from core import splittable_prewarm_process as spp

    class BrokenCtx:
        def Process(self, *a, **k):
            raise OSError("spawn blocked")

    monkeypatch.setattr(multiprocessing, "get_context", lambda method: BrokenCtx())
    started = []
    assert spp.start(fallback=lambda: started.append("thread")) is False
    assert started == ["thread"]
    assert spp.status()["mode"] == "thread"


def test_unchanged_pivot_rebuild_reports_zero_changes(monkeypatch, tmp_path):
    from app_v2.modules.splittable import cache_builder as pivot

    monkeypatch.setattr(pivot, "CACHE_DIR", tmp_path / "split_table")
    monkeypatch.setattr(pivot, "_throttled_yield", lambda *a, **k: False)
    source = tmp_path / "ML_TABLE_PRODZ.parquet"
    pl.DataFrame({
        "root_lot_id": ["Z1", "Z1", "Z2"],
        "wafer_id": [1, 2, 1],
        "KNOB_A": ["x", "y", "z"],
    }).write_parquet(source)

    assert pivot.build_pivoted_cache_for_product("ML_TABLE_PRODZ", product_path=source) is True
    assert pivot.last_build_changes("ML_TABLE_PRODZ") == 2
    # Same source again: every root is unchanged -> 0, so the view cache is kept.
    assert pivot.build_pivoted_cache_for_product("ML_TABLE_PRODZ", product_path=source) is True
    assert pivot.last_build_changes("prodz") == 0
    # A root disappearing is a change even though nothing new is written.
    pl.DataFrame({"root_lot_id": ["Z1", "Z1"], "wafer_id": [1, 2], "KNOB_A": ["x", "y"]}).write_parquet(source)
    assert pivot.build_pivoted_cache_for_product("ML_TABLE_PRODZ", product_path=source) is True
    assert pivot.last_build_changes("ML_TABLE_PRODZ") >= 1
