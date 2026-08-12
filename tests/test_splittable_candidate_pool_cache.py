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
