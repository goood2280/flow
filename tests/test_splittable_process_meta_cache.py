import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest


BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


@pytest.fixture
def cache_class():
    from app_v2.modules.splittable.process_meta_cache import ProcessMetaCache

    return ProcessMetaCache


def test_reuses_ram_entry_without_rebuilding(cache_class, tmp_path):
    cache = cache_class()
    path = tmp_path / "meta.json"
    builds = []

    def build():
        builds.append(True)
        return {"lot": "A"}

    assert cache.get(path, "sig-1", build, lambda: "sig-1") == {"lot": "A"}
    assert cache.get(path, "sig-1", build, lambda: "sig-1") == {"lot": "A"}
    assert builds == [True]


def test_reuses_disk_snapshot_across_cache_instances(cache_class, tmp_path):
    path = tmp_path / "meta.json"
    first_builds = []
    first = cache_class()
    first.get(
        path,
        "sig-1",
        lambda: first_builds.append(True) or {"lot": "A"},
        lambda: "sig-1",
    )

    second_builds = []
    second = cache_class()
    result = second.get(
        path,
        "sig-1",
        lambda: second_builds.append(True) or {"lot": "A"},
        lambda: "sig-1",
    )

    assert result == {"lot": "A"}
    assert first_builds == [True]
    assert second_builds == []


def test_signature_change_rebuilds_and_replaces_disk_snapshot(cache_class, tmp_path):
    cache = cache_class()
    path = tmp_path / "meta.json"
    builds = []

    def build_v1():
        builds.append("v1")
        return {"version": 1}

    def build_v2():
        builds.append("v2")
        return {"version": 2}

    assert cache.get(path, "sig-1", build_v1, lambda: "sig-1") == {"version": 1}
    assert cache.get(path, "sig-2", build_v2, lambda: "sig-2") == {"version": 2}
    assert builds == ["v1", "v2"]
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "signature": "sig-2",
        "items": {"version": 2},
    }


def test_failed_build_does_not_persist_or_replace_previous_snapshot(cache_class, tmp_path):
    cache = cache_class()
    path = tmp_path / "meta.json"
    cache.get(path, "sig-1", lambda: {"version": 1}, lambda: "sig-1")
    before = path.read_bytes()

    def failed_build():
        raise OSError("build failed")

    with pytest.raises(OSError, match="build failed"):
        cache.get(path, "sig-2", failed_build, lambda: "sig-2")

    assert path.read_bytes() == before
    assert cache.get(path, "sig-1", lambda: pytest.fail("old RAM entry was lost"), lambda: "sig-1") == {"version": 1}


def test_mixed_build_does_not_persist_or_enter_ram(cache_class, tmp_path):
    cache = cache_class()
    path = tmp_path / "meta.json"
    current = ["sig-1"]

    cache.get(path, "sig-1", lambda: {"version": 1}, lambda: current[0])
    before = path.read_bytes()
    current[0] = "sig-2"

    def mixed_build():
        current[0] = "sig-3"
        return {"version": 2}

    with pytest.raises(RuntimeError, match="inputs changed"):
        cache.get(path, "sig-2", mixed_build, lambda: current[0])

    assert path.read_bytes() == before
    assert "sig-2" not in [entry[0] for entry in cache._entries.values()]


def test_concurrent_requests_single_flight_one_build(cache_class, tmp_path):
    cache = cache_class()
    path = tmp_path / "meta.json"
    started = threading.Event()
    release = threading.Event()
    calls = []
    results = []
    errors = []

    def build():
        calls.append(True)
        started.set()
        assert release.wait(2)
        return {"ready": True}

    def worker():
        try:
            results.append(cache.get(path, "sig-1", build, lambda: "sig-1"))
        except BaseException as exc:  # pragma: no cover - diagnostic propagation
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    assert started.wait(2)
    time.sleep(0.05)
    assert calls == [True]
    release.set()
    for thread in threads:
        thread.join(2)

    assert errors == []
    assert len(results) == len(threads)
    assert all(result == {"ready": True} for result in results)
    assert calls == [True]


def test_entry_limit_evicts_oldest_entry(cache_class, tmp_path):
    cache = cache_class(max_entries=2, max_bytes=10_000)
    for index in range(3):
        path = tmp_path / f"entry-{index}.json"
        cache.get(path, f"sig-{index}", lambda index=index: {"value": index}, lambda index=index: f"sig-{index}")

    assert list(cache._entries) == [str(tmp_path / "entry-1.json"), str(tmp_path / "entry-2.json")]


def test_byte_limit_evicts_oldest_entry(cache_class, tmp_path):
    item = {"value": "x" * 20}
    raw_size = len(json.dumps({"signature": "sig", "items": item}, ensure_ascii=False).encode("utf-8"))
    cache = cache_class(max_entries=10, max_bytes=raw_size * 4)

    for index in range(2):
        path = tmp_path / f"entry-{index}.json"
        cache.get(path, "sig", lambda item=item: item, lambda: "sig")

    assert list(cache._entries) == [str(tmp_path / "entry-1.json")]


def _install_router_meta_cache(monkeypatch, tmp_path, cache_class):
    from routers import splittable

    cache = cache_class()
    monkeypatch.setattr(splittable, "_PROCESS_META_CACHE", cache)
    monkeypatch.setattr(splittable, "_process_meta_cache_path", lambda _product: tmp_path / "meta.json")
    return splittable, cache


def test_combined_legacy_and_export_metadata_paths_share_one_build(
    monkeypatch, tmp_path, cache_class
):
    splittable, _cache = _install_router_meta_cache(monkeypatch, tmp_path, cache_class)
    calls = {"knob": 0, "inline": 0, "vm": 0, "fab": 0, "mask": 0}

    def builder(kind, value):
        def build(_product):
            calls[kind] += 1
            return value

        return build

    monkeypatch.setattr(splittable, "_process_meta_signature", lambda _product: "stable")
    monkeypatch.setattr(splittable, "_build_knob_meta", builder("knob", {"KNOB_X": {}}))
    monkeypatch.setattr(
        splittable,
        "_build_inline_meta",
        builder("inline", {"INLINE_X": {"groups": [{"step_ids": ["S1"], "step_desc": "ETCH"}]}}),
    )
    monkeypatch.setattr(splittable, "_build_vm_meta", builder("vm", {"VM_X": {}}))
    monkeypatch.setattr(splittable, "_build_fab_meta", builder("fab", {"FAB_X": {}}))
    monkeypatch.setattr(splittable, "_build_mask_meta", builder("mask", {"MASK_X": {}}))

    assert splittable.process_meta("P")["items"]["inline"]["INLINE_X"]
    assert splittable.inline_meta("P")["items"]["INLINE_X"]
    assert splittable.vm_meta("P")["items"] == {"VM_X": {}}
    assert splittable.fab_meta("P")["items"] == {"FAB_X": {}}
    assert splittable.mask_meta("P")["items"] == {"MASK_X": {}}
    assert splittable._step_label_metas("P")["knob"] == {"KNOB_X": {}}
    assert calls == {"knob": 1, "inline": 1, "vm": 1, "fab": 1, "mask": 1}


def test_same_size_rulebook_rewrite_with_new_nanosecond_invalidates_snapshot(
    monkeypatch, tmp_path, cache_class
):
    splittable, _cache = _install_router_meta_cache(monkeypatch, tmp_path, cache_class)
    source_cfg = tmp_path / "source_config.json"
    schema = tmp_path / "rulebook_schema.json"
    rulebook = tmp_path / "ppid_knob.csv"
    source_cfg.write_text("{}", encoding="utf-8")
    schema.write_text("{}", encoding="utf-8")
    rulebook.write_text("x", encoding="utf-8")
    monkeypatch.setattr(splittable, "SOURCE_CFG", source_cfg)
    monkeypatch.setattr(splittable, "RULEBOOK_SCHEMA_FILE", schema)
    monkeypatch.setattr(splittable, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_sch", lambda _kind: {"file_name": "ppid_knob.csv"})
    monkeypatch.setattr(splittable, "_rulebook_path", lambda _kind: rulebook)
    monkeypatch.setattr(splittable, "_mltable_schema_columns", lambda _product: [])
    builds = []
    monkeypatch.setattr(splittable, "_build_knob_meta", lambda _product: builds.append(True) or {"v": len(builds)})
    for name in ("_build_inline_meta", "_build_vm_meta", "_build_fab_meta", "_build_mask_meta"):
        monkeypatch.setattr(splittable, name, lambda _product: {})

    timestamp = 1_700_000_000_000_000_000
    os.utime(rulebook, ns=(timestamp, timestamp))
    first = splittable._process_meta_snapshot("P")
    before = rulebook.stat()
    rulebook.write_text("y", encoding="utf-8")
    os.utime(rulebook, ns=(timestamp + 100, timestamp + 100))
    assert rulebook.stat().st_size == 1
    assert rulebook.stat().st_mtime_ns != before.st_mtime_ns
    assert rulebook.stat().st_mtime == before.st_mtime
    second = splittable._process_meta_snapshot("P")

    assert first["knob"] == {"v": 1}
    assert second["knob"] == {"v": 2}
    assert len(builds) == 2


def test_ml_schema_column_change_invalidates_snapshot(monkeypatch, tmp_path, cache_class):
    splittable, _cache = _install_router_meta_cache(monkeypatch, tmp_path, cache_class)
    columns = [["KNOB_A"]]
    monkeypatch.setattr(splittable, "_mltable_schema_columns", lambda _product: list(columns[0]))
    builds = []
    monkeypatch.setattr(splittable, "_build_knob_meta", lambda _product: builds.append(True) or {"v": len(builds)})
    for name in ("_build_inline_meta", "_build_vm_meta", "_build_fab_meta", "_build_mask_meta"):
        monkeypatch.setattr(splittable, name, lambda _product: {})

    first = splittable._process_meta_snapshot("P")
    columns[0] = ["KNOB_A", "INLINE_B"]
    second = splittable._process_meta_snapshot("P")

    assert first["knob"] == {"v": 1}
    assert second["knob"] == {"v": 2}
    assert len(builds) == 2


def test_scratch_eviction_registry_includes_process_metadata(monkeypatch, tmp_path, cache_class):
    from routers import splittable

    cache = cache_class()
    monkeypatch.setattr(splittable, "_PROCESS_META_CACHE", cache)
    registered = [entry for entry in splittable._scratch_cache_registry() if entry[0] == "process_meta"]
    assert registered == [("process_meta", cache._entries, cache._lock)]
    monkeypatch.setattr(
        splittable,
        "_scratch_cache_registry",
        lambda: registered,
    )
    cache._entries["P"] = ("sig", {"knob": {"X": "value"}}, 512)

    sizes = splittable.scratch_cache_sizes()
    assert sizes["process_meta"]["entries"] == 1
    assert splittable.emergency_evict_scratch_caches(1) > 0
    assert not cache._entries


def test_refresh_dashboard_reports_process_metadata_error(monkeypatch):
    from routers import splittable

    monkeypatch.setattr(splittable, "_refresh_match_cache_products", lambda *_a, **_k: {
        "products": [{"product": "P", "ok": True}],
    })
    monkeypatch.setattr(splittable, "_match_cache_products", lambda _product: ["P"])
    monkeypatch.setattr(splittable, "_match_cache_products_cover_all", lambda _products: False)
    monkeypatch.setattr(splittable, "export_latest_lot_step_cache", lambda **_kwargs: {
        "ok": True,
        "products": ["P"],
        "row_count": 1,
        "process_meta_errors": [{"product": "P", "error": "failed"}],
    })

    result = splittable._refresh_dashboard_latest_v4(["P"], force=True)

    assert result["ok"] is False
    assert result["error"] == "process_meta_incomplete"
