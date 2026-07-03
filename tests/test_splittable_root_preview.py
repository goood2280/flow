from __future__ import annotations

import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from routers import splittable  # noqa: E402


def _make_lf(n_rows: int = 5000, distinct_roots: int = 400) -> pl.LazyFrame:
    # 앞부분 행에는 소수의 root 만, 뒤쪽에 나머지 root 들이 몰려 있는 원천을 흉내낸다.
    roots = [f"R{(i % distinct_roots):04d}" for i in range(n_rows)]
    return pl.LazyFrame({"root_lot_id": roots, "wafer_id": [str(i % 25) for i in range(n_rows)]})


def test_limited_unique_values_empty_prefix_preview_is_bounded():
    lf = _make_lf()
    # 빈 prefix + preview_only=True → 앞부분만 샘플링해 상한 개수만 반환한다(즉시 응답).
    preview = splittable._limited_unique_values(lf, "root_lot_id", prefix="", limit=50, preview_only=True)
    assert 0 < len(preview) <= 50
    assert preview == sorted(preview, key=lambda s: str(s).upper())


def test_limited_unique_values_prefix_searches_full_source():
    lf = _make_lf()
    # 사용자가 입력하면 preview_only 와 무관하게 원천 전체에서 prefix 를 필터링한다.
    # R0399 는 뒤쪽 행에만 등장하지만 검색으로 찾아져야 한다.
    hits = splittable._limited_unique_values(lf, "root_lot_id", prefix="R0399", limit=50, preview_only=True)
    assert hits == ["R0399"]


def test_main_table_candidates_empty_prefix_root_lot_uses_preview(monkeypatch):
    captured = {}

    def _fake_limited_unique_values(lf, col, prefix="", limit=500, preview_only=True):
        captured["preview_only"] = preview_only
        captured["prefix"] = prefix
        return ["R0000", "R0001", "R0002"]

    # raw-scan 분기를 강제: 캐시류는 모두 miss 로 만든다.
    monkeypatch.setattr(splittable, "_lot_lookup_cache_get", lambda key: None)
    monkeypatch.setattr(splittable, "_lot_lookup_cache_set", lambda key, payload: payload)
    monkeypatch.setattr(splittable, "_root_lot_lookup_cache_candidates", lambda *a, **k: None)
    monkeypatch.setattr(splittable, "_scan_product_base", lambda product: _make_lf())
    monkeypatch.setattr(splittable, "_detect_lot_wafer", lambda lf, product=None: ("root_lot_id", "wafer_id"))
    monkeypatch.setattr(splittable, "_limited_unique_values", _fake_limited_unique_values)

    out = splittable._main_table_candidates(
        "ZZ_NOCACHE_TESTPROD_%s" % id(captured), col="root_lot_id", prefix="", limit=50
    )

    assert out.get("candidates") == ["R0000", "R0001", "R0002"]
    # 핵심: 빈 prefix root_lot_id 초기 목록은 전체 스캔이 아니라 미리보기 경로를 탄다.
    assert captured["preview_only"] is True


def test_main_table_candidates_prefix_root_lot_full_filter(monkeypatch):
    captured = {}

    def _fake_limited_unique_values(lf, col, prefix="", limit=500, preview_only=True):
        captured["preview_only"] = preview_only
        return ["R0399"]

    monkeypatch.setattr(splittable, "_lot_lookup_cache_get", lambda key: None)
    monkeypatch.setattr(splittable, "_lot_lookup_cache_set", lambda key, payload: payload)
    monkeypatch.setattr(splittable, "_root_lot_lookup_cache_candidates", lambda *a, **k: None)
    monkeypatch.setattr(splittable, "_scan_product_base", lambda product: _make_lf())
    monkeypatch.setattr(splittable, "_detect_lot_wafer", lambda lf, product=None: ("root_lot_id", "wafer_id"))
    monkeypatch.setattr(splittable, "_limited_unique_values", _fake_limited_unique_values)

    out = splittable._main_table_candidates(
        "ZZ_NOCACHE_TESTPROD2_%s" % id(captured), col="root_lot_id", prefix="R0399", limit=50
    )

    assert out.get("candidates") == ["R0399"]
    # prefix 검색은 _limited_unique_values 의 prefix 분기가 원천 전체를 필터링한다
    # (preview_only 값과 무관). 미리보기 밖 값도 검색으로 찾을 수 있음을 보장.
