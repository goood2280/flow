from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core import filebrowser_cache as fbcache  # noqa: E402
from core.paths import PATHS  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_cache(monkeypatch, tmp_path):
    cache_root = tmp_path / "cache"
    data_root = tmp_path / "data"
    cache_root.mkdir(parents=True, exist_ok=True)
    (data_root / "logs").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(PATHS, "cache_dir", cache_root, raising=False)
    monkeypatch.setattr(PATHS, "data_root", data_root, raising=False)
    fbcache._INFLIGHT.clear()
    yield
    fbcache._INFLIGHT.clear()


def _make_source_file(tmp_path: Path, name: str = "src.parquet", content: bytes = b"abc") -> tuple[Path, dict]:
    fp = tmp_path / name
    fp.write_bytes(content)
    source = fbcache.stat_for_file(fp)
    assert source is not None
    return fp, source


def _basic_payload() -> dict:
    return {"sql_norm": "", "select_cols_norm": "", "meta_only": True, "page": 0, "page_size": 100, "preview_cols": 20}


def test_cache_hit_when_file_unchanged(tmp_path):
    fp, source = _make_source_file(tmp_path)
    calls = []

    def compute():
        calls.append(1)
        return {"data": [], "showing": 0}

    out1 = fbcache.get_or_compute(endpoint="view", source=source, key_payload=_basic_payload(), compute=compute)
    out2 = fbcache.get_or_compute(endpoint="view", source=source, key_payload=_basic_payload(), compute=compute)

    assert len(calls) == 1
    assert out1.get("preview_cache_hit") is not True
    assert out2.get("preview_cache_hit") is True


def test_cache_miss_when_mtime_changes(tmp_path):
    fp, source = _make_source_file(tmp_path)
    calls = []

    def compute():
        calls.append(1)
        return {"data": [], "showing": 0}

    fbcache.get_or_compute(endpoint="view", source=source, key_payload=_basic_payload(), compute=compute)
    # Touch with future mtime.
    new_ns = source["mtime_ns"] + 10**9
    os.utime(fp, ns=(new_ns, new_ns))
    new_source = fbcache.stat_for_file(fp)
    assert new_source is not None
    assert new_source["mtime_ns"] != source["mtime_ns"]

    fbcache.get_or_compute(endpoint="view", source=new_source, key_payload=_basic_payload(), compute=compute)
    assert len(calls) == 2


def test_cache_miss_when_size_changes(tmp_path):
    fp, source = _make_source_file(tmp_path, content=b"abc")
    calls = []

    def compute():
        calls.append(1)
        return {"data": [], "showing": 0}

    fbcache.get_or_compute(endpoint="view", source=source, key_payload=_basic_payload(), compute=compute)
    fp.write_bytes(b"abcdefgh")
    new_source = fbcache.stat_for_file(fp)
    assert new_source["size_bytes"] != source["size_bytes"]

    fbcache.get_or_compute(endpoint="view", source=new_source, key_payload=_basic_payload(), compute=compute)
    assert len(calls) == 2


def test_cache_corrupted_file_recovers(tmp_path):
    fp, source = _make_source_file(tmp_path)
    calls = []

    def compute():
        calls.append(1)
        return {"data": [], "showing": 0}

    fbcache.get_or_compute(endpoint="view", source=source, key_payload=_basic_payload(), compute=compute)
    # Corrupt the cache file.
    for cache_file in fbcache.cache_dir().glob("*.json"):
        cache_file.write_text("{not valid json", encoding="utf-8")

    fbcache.get_or_compute(endpoint="view", source=source, key_payload=_basic_payload(), compute=compute)
    assert len(calls) == 2


def test_key_payload_distinguishes_sql_and_select_cols(tmp_path):
    fp, source = _make_source_file(tmp_path)
    calls: list[str] = []

    def make_compute(tag: str):
        def _c():
            calls.append(tag)
            return {"tag": tag}
        return _c

    fbcache.get_or_compute(endpoint="view", source=source, key_payload={**_basic_payload(), "sql_norm": ""}, compute=make_compute("a"))
    fbcache.get_or_compute(endpoint="view", source=source, key_payload={**_basic_payload(), "sql_norm": "x = 1"}, compute=make_compute("b"))
    fbcache.get_or_compute(endpoint="view", source=source, key_payload={**_basic_payload(), "select_cols_norm": "x,y"}, compute=make_compute("c"))
    # Same as first — should hit.
    fbcache.get_or_compute(endpoint="view", source=source, key_payload={**_basic_payload(), "sql_norm": ""}, compute=make_compute("a"))

    assert calls == ["a", "b", "c"]


def test_singleflight_only_invokes_compute_once(tmp_path):
    fp, source = _make_source_file(tmp_path)
    counter = {"n": 0}
    proceed = threading.Event()

    def compute():
        proceed.wait(timeout=5.0)
        counter["n"] += 1
        return {"data": [], "showing": 0}

    results = []

    def worker():
        results.append(fbcache.get_or_compute(
            endpoint="view", source=source,
            key_payload=_basic_payload(), compute=compute,
        ))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    # Give threads time to enter singleflight.
    time.sleep(0.1)
    proceed.set()
    for t in threads:
        t.join(timeout=10.0)

    assert counter["n"] == 1
    assert len(results) == 4


def test_directory_stat_detects_new_parquet(tmp_path):
    prod = tmp_path / "PRODA" / "date=20260101"
    prod.mkdir(parents=True)
    (prod / "part_0.parquet").write_bytes(b"abc")
    source1 = fbcache.stat_for_db_product(tmp_path / "PRODA")
    assert source1 is not None

    calls = []

    def compute():
        calls.append(1)
        return {"data": [], "showing": 0}

    fbcache.get_or_compute(endpoint="view", source=source1, key_payload=_basic_payload(), compute=compute)

    new_part = prod / "part_1.parquet"
    new_part.write_bytes(b"deadbeef")
    # Force a later mtime in case filesystem timestamp granularity loses the change.
    future_ns = source1["latest_child_mtime_ns"] + 10**9
    os.utime(new_part, ns=(future_ns, future_ns))
    source2 = fbcache.stat_for_db_product(tmp_path / "PRODA")
    assert source2 is not None
    assert source2["latest_child_mtime_ns"] != source1["latest_child_mtime_ns"]

    fbcache.get_or_compute(endpoint="view", source=source2, key_payload=_basic_payload(), compute=compute)
    assert len(calls) == 2


def test_preview_cache_disabled_setting_bypasses_cache(tmp_path):
    fp, source = _make_source_file(tmp_path)
    assert fbcache.is_enabled({"preview_cache_enabled": True}) is True
    assert fbcache.is_enabled({"preview_cache_enabled": False}) is False
    # Disabled flag means routers won't call get_or_compute at all; the cache
    # store itself never sees the request, so no file is created.
    assert not any(fbcache.cache_dir().glob("*.json"))


def test_static_kind_cache_distinguishes_from_tabular(tmp_path):
    """JSON/MD/YAML wraps use a different key payload (static_kind) than the
    tabular parquet/csv wrap. Make sure the two never collide for the same fp."""
    fp, source = _make_source_file(tmp_path, name="config.json", content=b'{"a":1}')
    calls = []

    def compute_static():
        calls.append("static")
        return {"kind": "json", "preview": '{"a":1}'}

    def compute_tabular():
        calls.append("tabular")
        return {"kind": "table", "data": []}

    fbcache.get_or_compute(
        endpoint="base-file-view", source=source,
        key_payload={"static_kind": "json"}, compute=compute_static,
    )
    fbcache.get_or_compute(
        endpoint="base-file-view", source=source,
        key_payload={"sql_norm": "", "select_cols_norm": "", "meta_only": True,
                     "page": 0, "page_size": 100, "preview_cols": 20},
        compute=compute_tabular,
    )
    # Each variant cached once.
    fbcache.get_or_compute(
        endpoint="base-file-view", source=source,
        key_payload={"static_kind": "json"}, compute=compute_static,
    )
    fbcache.get_or_compute(
        endpoint="base-file-view", source=source,
        key_payload={"sql_norm": "", "select_cols_norm": "", "meta_only": True,
                     "page": 0, "page_size": 100, "preview_cols": 20},
        compute=compute_tabular,
    )
    assert calls == ["static", "tabular"]
