import json
from pathlib import Path

import polars as pl


def test_root_candidates_can_serve_stale_index_while_revalidating(monkeypatch, tmp_path):
    from core import ml_table_lookup as lookup

    source = tmp_path / "ML_TABLE_BIG.parquet"
    source.write_bytes(b"source")
    cache_dir = tmp_path / "lookup"
    cache_dir.mkdir()
    index = {
        "version": lookup.CANDIDATE_INDEX_VERSION,
        "root_lot_ids": ["A0001", "A0002"],
        "root_lot_id_count": 2,
    }

    monkeypatch.setattr(lookup, "cache_dir_for", lambda _fp: cache_dir)
    monkeypatch.setattr(lookup, "_read_meta", lambda _fp: {"root_lot_id_count": 2})
    monkeypatch.setattr(lookup, "_meta_source_stale", lambda _meta, _fp: True)
    monkeypatch.setattr(lookup, "_job_status_for", lambda _fp: "queued")
    monkeypatch.setattr(lookup, "read_candidate_index", lambda _fp: index)
    monkeypatch.setattr(lookup, "_candidate_index_source_stale", lambda _index, _fp: True)
    monkeypatch.setattr(lookup, "_candidate_index_summary", lambda _fp, _index: {"has_index": True})

    strict = lookup.root_lot_candidates_from_lookup_cache(source, limit=50_000)
    stale = lookup.root_lot_candidates_from_lookup_cache(
        source, limit=50_000, allow_stale=True)

    assert strict["candidates"] == []
    assert stale["candidates"] == ["A0001", "A0002"]
    assert stale["candidate_index"] is True
    assert stale["candidate_index_stale"] is True
    assert stale["source_stale"] is True


def test_lot_id_candidates_can_serve_stale_index_while_revalidating(monkeypatch, tmp_path):
    from core import ml_table_lookup as lookup

    source = tmp_path / "ML_TABLE_BIG.parquet"
    source.write_bytes(b"source")
    index = {
        "version": lookup.CANDIDATE_INDEX_VERSION,
        "root_lot_ids": ["A0001"],
        "identity_values": {"lot_id": ["A0001.1", "A0001.2"]},
        "truncated_columns": [],
    }
    monkeypatch.setattr(
        lookup,
        "cache_status",
        lambda _fp: {"status": "stale", "has_cache": True},
    )
    monkeypatch.setattr(lookup, "read_candidate_index", lambda _fp: index)
    monkeypatch.setattr(lookup, "_candidate_index_source_stale", lambda _index, _fp: True)

    strict = lookup.candidate_values_from_lookup_cache(source, "lot_id")
    stale = lookup.candidate_values_from_lookup_cache(
        source, "lot_id", allow_stale=True)

    assert strict["available"] is False
    assert stale["available"] is True
    assert stale["values"] == ["A0001.1", "A0001.2"]
    assert stale["source_stale"] is True


def test_fresh_partitions_without_candidate_index_are_rebuilt(monkeypatch, tmp_path):
    from core import ml_table_lookup as lookup

    source = tmp_path / "ML_TABLE_LEGACY.parquet"
    source.write_bytes(b"source")
    status = {"status": "fresh", "cache_dir": str(tmp_path / "lookup"), "meta": {}}
    monkeypatch.setattr(lookup, "cache_status", lambda _fp: status)
    monkeypatch.setattr(lookup, "read_candidate_index", lambda _fp: {})
    monkeypatch.setattr(lookup, "_try_acquire_build_lock", lambda _fp: (object(), tmp_path / "lock", {}))
    monkeypatch.setattr(lookup, "_release_build_lock", lambda *_args: None)
    rebuilt = []
    monkeypatch.setattr(
        lookup,
        "_build_lookup_cache",
        lambda fp: rebuilt.append(Path(fp)) or {"ok": True, "rebuilt": True},
    )

    result = lookup.build_lookup_cache(source)

    assert result["rebuilt"] is True
    assert rebuilt == [source]


def test_root_pool_at_limit_is_ready_not_permanently_preparing(monkeypatch):
    from routers import splittable

    candidates = [f"R{i:05d}" for i in range(splittable._ROOT_LOT_POOL_MAX)]
    monkeypatch.setattr(
        splittable,
        "_root_lot_lookup_cache_candidates",
        lambda *_args, **_kwargs: {
            "has_cache": True,
            "source_stale": False,
            "candidate_index": True,
            "root_lot_id_count": splittable._ROOT_LOT_POOL_MAX + 10,
            "candidates": candidates,
            "status": "fresh",
        },
    )
    queued = []
    monkeypatch.setattr(
        splittable._ml_table_lookup,
        "enqueue_build",
        lambda fp: queued.append(fp) or {"status": "queued"},
    )

    values, meta, ready = splittable._build_root_lot_pool("ML_TABLE_BIG")

    assert ready is True
    assert len(values) == splittable._ROOT_LOT_POOL_MAX
    assert meta["truncated"] is True
    assert meta["total_count"] == splittable._ROOT_LOT_POOL_MAX + 10
    assert meta["match_mode"] == "lookup_cache_roots"
    assert queued == []


def test_fab_lot_bounded_pool_is_cached_when_identity_index_is_truncated(monkeypatch, tmp_path):
    from routers import splittable

    source = tmp_path / "ML_TABLE_BIG.parquet"
    source.write_bytes(b"source")
    monkeypatch.setattr(splittable, "_product_path", lambda _product: source)
    monkeypatch.setattr(
        splittable._ml_table_lookup,
        "cache_status",
        lambda _fp: {
            "status": "fresh",
            "meta": {"schema": {"lot_id": "String"}},
        },
    )
    monkeypatch.setattr(
        splittable._ml_table_lookup,
        "candidate_values_from_lookup_cache",
        lambda *_args, **_kwargs: {
            "available": True,
            "complete": False,
            "values": ["LOT_A", "LOT_B"],
            "source_column": "lot_id",
        },
    )
    monkeypatch.setattr(
        splittable,
        "_latest_lot_step_cache_lf",
        lambda _product: pl.DataFrame({"lot_id": ["LOT_B", "LOT_C"]}).lazy(),
    )
    monkeypatch.setattr(
        splittable,
        "_latest_lot_step_cache_source",
        lambda _product: "latest_cache",
    )

    values, meta, ready = splittable._build_fab_lot_pool("ML_TABLE_BIG")

    assert ready is True
    assert values == ["LOT_A", "LOT_B", "LOT_C"]
    assert meta["truncated"] is True
    assert meta["exhaustive"] is False


class _EmptyPoolCache:
    def __init__(self):
        self.value = None
        self.put_calls = 0

    def get(self, *_args, **_kwargs):
        return self.value

    def put(self, _product, _sig, values, **kwargs):
        self.put_calls += 1
        self.value = {
            "values": list(values),
            "meta": kwargs.get("meta") or {},
            "complete": bool(kwargs.get("complete")),
            "cached": "memory",
        }
        return self.value


def test_complete_empty_root_pool_is_cached_and_not_rebuilt(monkeypatch):
    from routers import splittable

    cache = _EmptyPoolCache()
    builds = []
    monkeypatch.setattr(splittable, "_lot_list_cache", cache)
    monkeypatch.setattr(splittable, "_root_lot_pool_sig", lambda _product: "sig")
    monkeypatch.setattr(splittable, "_root_lot_provisional_get", lambda _product: None)
    monkeypatch.setattr(splittable, "_root_lot_provisional_drop", lambda _product="": None)
    monkeypatch.setattr(
        splittable,
        "_build_root_lot_pool",
        lambda product: builds.append(product) or ([], {"match_mode": "lookup_cache_roots"}, True),
    )

    first = splittable._root_lot_pool("ML_TABLE_EMPTY")
    second = splittable._root_lot_pool("ML_TABLE_EMPTY")

    assert first["complete"] is True
    assert first["values"] == []
    assert second == first
    assert builds == ["ML_TABLE_EMPTY"]
    assert cache.put_calls == 1


def test_complete_empty_lot_list_survives_disk_round_trip(monkeypatch, tmp_path):
    from core import lot_list_cache

    monkeypatch.setattr(lot_list_cache, "_cache_dir", lambda: tmp_path)
    lot_list_cache.clear()
    try:
        written = lot_list_cache.put(
            "ML_TABLE_EMPTY", "sig-empty", [], complete=True,
            meta={"match_mode": "lookup_cache_roots"},
        )
        assert written["complete"] is True
        assert written["values"] == []

        # RAM을 비워 디스크 계층을 실제로 통과시킨다.
        with lot_list_cache._LOCK:
            lot_list_cache._RAM.clear()
            lot_list_cache._RAM_BYTES = 0
        loaded = lot_list_cache.get("ML_TABLE_EMPTY", "sig-empty")
        assert loaded is not None
        assert loaded["complete"] is True
        assert loaded["values"] == []
        assert loaded["cached"] == "disk"
    finally:
        lot_list_cache.clear()


def test_complete_empty_root_lookup_response_does_not_report_preparing(monkeypatch):
    from routers import splittable

    monkeypatch.setattr(
        splittable,
        "_root_lot_pool",
        lambda _product: {
            "values": [],
            "complete": True,
            "cached": "disk",
            "meta": {"match_mode": "lookup_cache_roots", "source": "mltable_lookup"},
        },
    )

    result = splittable.get_lot_candidates(
        product="ML_TABLE_EMPTY",
        col="root_lot_id",
        prefix="",
        limit=30,
        source="auto",
        root_lot_id="",
    )

    assert result["candidates"] == []
    assert result["complete"] is True
    assert result["match_mode"] == "lookup_cache_roots"


def test_pivot_readiness_metadata_is_written_atomically(tmp_path):
    from app_v2.modules.splittable import cache_builder

    target = tmp_path / ".root_fingerprints.json"
    cache_builder._write_json_atomic(target, {"format": 2, "roots": {"A": [1, 2]}})

    assert json.loads(target.read_text(encoding="utf-8")) == {
        "format": 2,
        "roots": {"A": [1, 2]},
    }
    assert list(tmp_path.glob("*.tmp")) == []
