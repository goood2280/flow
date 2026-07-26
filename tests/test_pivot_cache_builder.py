from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app_v2.modules.splittable import cache_builder  # noqa: E402


def test_chunk_size_shrinks_under_memory_pressure(monkeypatch):
    monkeypatch.delenv("FLOW_PIVOT_CACHE_CHUNK_SIZE", raising=False)
    monkeypatch.delenv("FLOW_PIVOT_CACHE_CHUNK_SIZE_MIN", raising=False)

    # 메모리 여유가 있으면 기본(큰) 청크, 빠듯하면 작은 청크로 줄여 UI 여유를 확보한다.
    assert cache_builder._chunk_size(False) == cache_builder._CHUNK_SIZE_DEFAULT
    assert cache_builder._chunk_size(True) == cache_builder._CHUNK_SIZE_UNDER_MEMORY_PRESSURE
    assert cache_builder._chunk_size(True) < cache_builder._chunk_size(False)


def test_chunk_size_env_overrides(monkeypatch):
    monkeypatch.setenv("FLOW_PIVOT_CACHE_CHUNK_SIZE", "12")
    monkeypatch.setenv("FLOW_PIVOT_CACHE_CHUNK_SIZE_MIN", "3")

    assert cache_builder._chunk_size(False) == 12
    assert cache_builder._chunk_size(True) == 3


def test_chunk_size_clamps_bad_env(monkeypatch):
    monkeypatch.setenv("FLOW_PIVOT_CACHE_CHUNK_SIZE", "not-a-number")
    monkeypatch.setenv("FLOW_PIVOT_CACHE_CHUNK_SIZE_MIN", "0")  # below lower bound -> clamped to 1

    assert cache_builder._chunk_size(False) == cache_builder._CHUNK_SIZE_DEFAULT
    assert cache_builder._chunk_size(True) == 1
