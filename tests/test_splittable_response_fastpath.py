import json

import polars as pl
import pytest
from fastapi import HTTPException

from starlette.responses import Response


def test_offline_install_without_orjson_returns_direct_compact_json(monkeypatch):
    from routers import splittable

    monkeypatch.setattr(splittable, "_orjson", None)
    payload = {
        "root_lot_id": "ROOT", "cells_format": "v2",
        "wafer_keys": [str(i) for i in range(25)],
        "rows": [{"_param": f"KNOB_{i}", "_display": "공정", "a": [i] * 25,
                  "p": {"0": "계획"}, "m": [0], "can_plan": True} for i in range(2000)],
    }
    response, elapsed, size = splittable._view_orjson_response(payload)
    assert isinstance(response, Response)  # never FastAPI's recursive dict path
    assert json.loads(response.body) == payload
    assert size == len(response.body) and size > 0
    assert elapsed >= 0


def test_unsupported_nonfinite_payload_keeps_compatibility_fallback(monkeypatch):
    from routers import splittable

    monkeypatch.setattr(splittable, "_orjson", None)
    payload = {"value": float("nan")}
    result, _, size = splittable._view_orjson_response(payload)
    assert result is payload and size == 0


def test_meta_revision_reuses_checked_dependencies_and_tracks_colors(monkeypatch, tmp_path):
    from routers import splittable

    colors = tmp_path / "colors.json"
    monkeypatch.setattr(splittable, "CATEGORY_COLORS_CFG", colors)
    hard = (("product-input", 1), ("rulebook", 2))
    def unexpected(*args, **kwargs):
        raise AssertionError("warm view must not rescan metadata or dependencies")
    monkeypatch.setattr(splittable, "_split_view_cache_dep_signature", unexpected)
    monkeypatch.setattr(splittable, "_process_meta_snapshot", unexpected)
    first = splittable._matching_meta_revision("P", hard)
    assert splittable._matching_meta_revision("P", hard) == first
    colors.write_text('{"P":{"A":"#ffffff"}}', encoding="utf-8")
    second = splittable._matching_meta_revision("P", hard)
    assert second != first
    assert splittable._matching_meta_revision("P", ("changed-source",)) != second


def test_interactive_cold_lane_has_bounded_wait(monkeypatch):
    from routers import splittable

    waits = []
    class BusyLane:
        def acquire(self, *, timeout):
            waits.append(timeout)
            return False
    monkeypatch.setattr(splittable, "_VIEW_COLD_SEMAPHORE", BusyLane())
    monkeypatch.delenv("FLOW_SPLITTABLE_INTERACTIVE_QUEUE_WAIT_SEC", raising=False)
    monkeypatch.setattr(splittable, "_view_cold_lane_wait_sec", lambda: 90)
    assert not splittable._view_cold_lane_acquire({"is_user_search": True})
    assert waits == [0.15]


def test_cache_first_retry_consumes_ready_pivot_without_raw_scan(monkeypatch, tmp_path):
    from routers import splittable

    pivot = tmp_path / "pivot.parquet"
    pl.DataFrame({"root_lot_id": ["R"], "wafer_id": [1], "KNOB_A": ["X"]}).write_parquet(pivot)
    monkeypatch.setattr(splittable, "_product_path", lambda _product: pivot)
    monkeypatch.setattr(splittable, "_pivot_cache_path", lambda *_args: pivot)
    monkeypatch.setattr(splittable, "_split_view_cache_dep_signature", lambda *_a, **_k: ((), ()))
    monkeypatch.setattr(splittable, "_split_view_cache_get", lambda *_a: ("miss", None))
    monkeypatch.setattr(splittable, "_view_compute_begin", lambda *_a: (True, None))
    monkeypatch.setattr(splittable, "_view_compute_finish", lambda *_a: None)
    monkeypatch.setattr(splittable, "_view_cold_lane_acquire", lambda *_a: True)
    monkeypatch.setattr(splittable, "_knob_sidecar_usable", lambda *_a: False)
    monkeypatch.setattr(splittable, "_enqueue_pivot_cache_build", lambda *_a, **_k: None)
    def raw_forbidden(*args, **kwargs):
        pytest.fail("ready pivot retry must not reenter raw/lookup preparation")
    monkeypatch.setattr(splittable, "_split_view_large_root_cache_or_defer", raw_forbidden)
    def inspect_frame(_product, **kwargs):
        assert kwargs["base_lf"].collect()["KNOB_A"].to_list() == ["X"]
        raise HTTPException(418, "verified ready pivot")
    monkeypatch.setattr(splittable, "_scan_product", inspect_frame)
    with pytest.raises(HTTPException, match="verified ready pivot"):
        splittable.view_split_core(product="P", root_lot_id="R", wafer_ids="", prefix="KNOB",
            custom_name="", view_mode="all", history_mode="all", fab_lot_id="", custom_cols="",
            include_related=False, cache_first=True, request=None)


@pytest.mark.parametrize("kind", ["pivot", "fab"])
def test_remote_submission_is_not_cache_completion_and_allows_offline_fallback(monkeypatch, tmp_path, kind):
    from routers import splittable
    from core import worker_dispatch

    options = []
    class InlineThread:
        def __init__(self, *, target, **kwargs):
            self.target = target
        def start(self):
            self.target()
    monkeypatch.setattr(splittable.threading, "Thread", InlineThread)
    monkeypatch.setattr(splittable, "_canonical_mltable_product_name", lambda *_a, **_k: "P")
    monkeypatch.setattr(splittable, "_product_path", lambda *_a: tmp_path / "source.parquet")
    monkeypatch.setattr(splittable, "_cache_build_emit", lambda *_a, **_k: None)
    for name in ("_PIVOT_BUILD_INPROGRESS", "_FAB_IDX_BUILD_INPROGRESS"):
        monkeypatch.setattr(splittable, name, set())
    for name in ("_PIVOT_BUILD_LAST", "_FAB_IDX_BUILD_LAST"):
        monkeypatch.setattr(splittable, name, {})
    monkeypatch.setattr(splittable, "_fab_lot_index_enabled", lambda: True)
    monkeypatch.setattr(splittable, "_is_fab_lot_index_staging_product", lambda *_a: False)
    def queued(*args, **kwargs):
        options.append(kwargs)
        return {"ok": True, "queued": True}
    monkeypatch.setattr(worker_dispatch, "run_heavy", queued)
    monkeypatch.setattr(splittable, "_clear_split_view_cache_product",
                        lambda *_a: pytest.fail("queued job must retain ready view cache"))
    enqueue = splittable._enqueue_pivot_cache_build if kind == "pivot" else splittable._enqueue_fab_lot_index_build
    assert enqueue("P", reason="cache_miss")
    assert len(options) == 1 and options[0]["local_fallback"] is True


@pytest.fixture
def isolated_view_cache(monkeypatch, tmp_path):
    from collections import OrderedDict
    from routers import splittable

    monkeypatch.setattr(splittable, "_VIEW_CACHE", OrderedDict())
    monkeypatch.setattr(splittable, "_VIEW_CACHE_BYTES", 0)
    monkeypatch.setattr(splittable, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_view_cache_max_bytes", lambda: 200_000)
    monkeypatch.setattr(splittable, "_view_disk_cache_enabled", lambda: True)
    return splittable


def test_stale_disk_payload_remains_stale_after_ram_promotion(isolated_view_cache):
    st = isolated_view_cache
    key, hard, old_soft, new_soft = ("P", "R"), ("input",), ("old",), ("new",)
    st._view_disk_cache_write(key, hard, old_soft, {"rows_compact": [{"a": ["S1"]}]})

    assert st._split_view_cache_get(key, hard, new_soft)[0] == "stale"
    assert st._split_view_cache_get(key, hard, new_soft)[0] == "stale"
    st._split_view_cache_put(key, hard, new_soft, {"rows_compact": [{"a": ["S2"]}]})
    freshness, payload = st._split_view_cache_get(key, hard, new_soft)
    assert freshness == "fresh" and payload["rows_compact"][0]["a"] == ["S2"]
    assert st._split_view_cache_get(key, ("edited",), new_soft) == ("miss", None)


def test_oversized_response_uses_disk_without_evicting_hot_lots(isolated_view_cache):
    st = isolated_view_cache
    small = {"rows_compact": [{"a": ["S1"]}]}
    large = {"rows_compact": [{"a": ["X" * 250_000]}]}
    st._split_view_cache_put(("P", "hot"), (), (), small)
    st._split_view_cache_put(("P", "large"), (), (), large)

    assert ("P", "hot") in st._VIEW_CACHE
    assert ("P", "large") not in st._VIEW_CACHE
    assert st._VIEW_CACHE_BYTES <= st._view_cache_max_bytes()
    assert st._split_view_cache_get(("P", "large"), (), ()) == ("fresh", large)
    assert ("P", "large") not in st._VIEW_CACHE


def test_view_cache_accounts_for_long_values_plans_and_metadata(isolated_view_cache):
    st = isolated_view_cache
    payload = {"rows_compact": [{"a": ["actual"], "p": {"0": "P" * 50_000}}],
               "all_columns": ["COLUMN_" + "X" * 100_000]}
    assert st._estimate_view_payload_bytes(payload) > 150_000
    # The second large entry must evict the first instead of accumulating.
    st._split_view_cache_put_memory(("P", "R1"), (), (), payload)
    st._split_view_cache_put_memory(("P", "R2"), (), (), payload)
    assert list(st._VIEW_CACHE) == [("P", "R2")]
    assert st._VIEW_CACHE_BYTES <= st._view_cache_max_bytes()


def test_ready_view_reuses_diagnostics_but_refreshes_s0(monkeypatch):
    from routers import splittable as st

    monkeypatch.setattr(st, "_knob_s0_for_root", lambda *_: {"KNOB_A": {"value": "history"}})
    monkeypatch.setattr(st, "_knob_current_s0_for_product", lambda *_: {"KNOB_A": {"value": "current"}})
    def unexpected(*args, **kwargs):
        pytest.fail("ready response must not reaggregate the entire root RAM cache")
    monkeypatch.setattr(st, "_product_ram_cache_response_meta", unexpected)
    payload = {"product": "P", "root_lot_id": "R", "rows_compact": [{"_param": "KNOB_A"}],
               "lookup_cache": {}, "product_cache": {"hit": True}}
    result = st._attach_split_view_runtime_fields(payload, None, payload_cache_hit=True)
    assert result["product_cache"] == {"hit": True}
    assert result["s0_by_knob"]["KNOB_A"]["value"] == "history"
    assert result["s0_edit_by_knob"]["KNOB_A"]["value"] == "current"
    assert "s0_by_knob" not in payload
