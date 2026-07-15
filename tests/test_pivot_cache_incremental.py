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

from app_v2.modules.splittable import cache_builder  # noqa: E402


def _source_df(r2_knob: str = "OFF") -> pl.DataFrame:
    return pl.DataFrame({
        "ROOT_LOT_ID": ["R1", "R1", "R2", "R2"],
        "LOT_ID": ["R1A.1", "R1A.1", "R2A.1", "R2A.1"],
        "WAFER_ID": [1, 2, 1, 2],
        "KNOB_A": ["ON", "ON", r2_knob, r2_knob],
    })


def test_incremental_rebuild_skips_unchanged_roots(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_builder, "CACHE_DIR", tmp_path / "split_table")
    src = tmp_path / "ML_TABLE_INC.parquet"
    _source_df().write_parquet(src)

    assert cache_builder.build_pivoted_cache_for_product("ML_TABLE_INC", product_path=src)
    out_dir = tmp_path / "split_table" / "ML_TABLE_INC"
    r1, r2 = out_dir / "R1.parquet", out_dir / "R2.parquet"
    assert r1.is_file() and r2.is_file()
    m1, m2 = r1.stat().st_mtime_ns, r2.stat().st_mtime_ns

    # 소스 재기록(내용 동일) → 어떤 root 파일도 재작성하지 않는다
    time.sleep(0.02)
    _source_df().write_parquet(src)
    assert cache_builder.build_pivoted_cache_for_product("ML_TABLE_INC", product_path=src)
    assert r1.stat().st_mtime_ns == m1
    assert r2.stat().st_mtime_ns == m2

    # R2 내용 변경 → R2 만 재작성
    time.sleep(0.02)
    _source_df(r2_knob="ON").write_parquet(src)
    assert cache_builder.build_pivoted_cache_for_product("ML_TABLE_INC", product_path=src)
    assert r1.stat().st_mtime_ns == m1, "unchanged root must not be rewritten"
    assert r2.stat().st_mtime_ns != m2
    assert pl.read_parquet(r2)["KNOB_A"].to_list() == ["ON", "ON"]

    # 캐시 파일이 지워졌으면 지문이 같아도 다시 만든다
    r1.unlink()
    assert cache_builder.build_pivoted_cache_for_product("ML_TABLE_INC", product_path=src)
    assert r1.is_file()

    # 소스에서 root 가 사라지면 stale 파일 정리
    _source_df(r2_knob="ON").filter(pl.col("ROOT_LOT_ID") != "R2").write_parquet(src)
    assert cache_builder.build_pivoted_cache_for_product("ML_TABLE_INC", product_path=src)
    assert not r2.exists()
    assert r1.is_file()
