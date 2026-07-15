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

PRODUCT = "ML_TABLE_FABINC"
SOURCE = "1.RAWDATA_DB_FAB/FABINC"


def _fab_rows(root: str, lot: str, ts: str, wafers=(1, 2)) -> pl.DataFrame:
    return pl.DataFrame({
        "root_lot_id": [root] * len(wafers),
        "lot_id": [lot] * len(wafers),
        "wafer_id": list(wafers),
        "tkout_time": [ts] * len(wafers),
    })


def _clear_discovery_caches():
    splittable._RGLOB_CACHE.clear()
    splittable._DB_ROOTS_CACHE.clear()
    splittable._FIRST_DATA_FILE_CACHE.clear()


def _read_root(idx_dir: Path, root: str) -> pl.DataFrame:
    part = idx_dir / f"__fab_idx_root={root}"
    files = sorted(part.glob("*.parquet"))
    df = pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")
    return df.sort(["wafer_id"])


def test_added_file_merges_incrementally_and_matches_full_rebuild(tmp_path, monkeypatch):
    src_dir = tmp_path / "1.RAWDATA_DB_FAB" / "FABINC"
    (src_dir / "date=20240101").mkdir(parents=True)
    _fab_rows("R1", "F1A.1", "2024-01-01T10:00:00").write_parquet(
        src_dir / "date=20240101" / "part0.parquet")

    monkeypatch.setattr(splittable, "_db_base", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_base_root", lambda: tmp_path)
    _clear_discovery_caches()

    assert splittable._build_fab_lot_index(PRODUCT, SOURCE, include_all=False)
    idx_dir = splittable._fab_lot_index_dir(PRODUCT)
    assert (idx_dir / "__fab_idx_root=R1").is_dir()
    r1_before = _read_root(idx_dir, "R1")
    assert set(r1_before["lot_id"].to_list()) == {"F1A.1"}

    # 새 date 파일 추가: R1 은 더 최신 lot 으로 이동, R2 는 신규
    (src_dir / "date=20240102").mkdir()
    pl.concat([
        _fab_rows("R1", "F1A.2", "2024-01-02T09:00:00"),
        _fab_rows("R2", "F2A.1", "2024-01-02T10:00:00"),
    ]).write_parquet(src_dir / "date=20240102" / "part1.parquet")
    _clear_discovery_caches()

    calls = {"full": 0}
    orig_full = splittable._build_fab_lot_index_full
    def _counting_full(*a, **k):
        calls["full"] += 1
        return orig_full(*a, **k)
    monkeypatch.setattr(splittable, "_build_fab_lot_index_full", _counting_full)

    assert splittable._build_fab_lot_index(PRODUCT, SOURCE, include_all=False)
    assert calls["full"] == 0, "append-only 갱신은 전체 재빌드 없이 처리돼야 한다"

    r1 = _read_root(idx_dir, "R1")
    assert set(r1["lot_id"].to_list()) == {"F1A.2"}, "R1 은 최신 lot 으로 갱신"
    r2 = _read_root(idx_dir, "R2")
    assert set(r2["lot_id"].to_list()) == {"F2A.1"}, "신규 root R2 파티션 생성"

    # 증분 결과가 from-scratch 전체 재빌드와 동일한지 대조
    import shutil
    shutil.rmtree(idx_dir)
    _clear_discovery_caches()
    assert orig_full(PRODUCT, SOURCE, include_all=False)
    for root in ("R1", "R2"):
        full_df = _read_root(idx_dir, root)
        inc_df = {"R1": r1, "R2": r2}[root]
        assert full_df.select(sorted(full_df.columns)).equals(
            inc_df.select(sorted(inc_df.columns))), f"{root}: incremental != full"

    # 파일 변화가 없으면 no-op
    _clear_discovery_caches()
    monkeypatch.setattr(splittable, "_build_fab_lot_index_full", _counting_full)
    calls["full"] = 0
    assert splittable._build_fab_lot_index(PRODUCT, SOURCE, include_all=False)
    assert calls["full"] == 0


def test_removed_file_falls_back_to_full_rebuild(tmp_path, monkeypatch):
    src_dir = tmp_path / "1.RAWDATA_DB_FAB" / "FABINC"
    for date, root, lot in (("20240101", "R1", "F1A.1"), ("20240102", "R2", "F2A.1")):
        (src_dir / f"date={date}").mkdir(parents=True)
        _fab_rows(root, lot, f"2024-01-01T00:00:00").write_parquet(
            src_dir / f"date={date}" / "part.parquet")

    monkeypatch.setattr(splittable, "_db_base", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_base_root", lambda: tmp_path)
    _clear_discovery_caches()
    assert splittable._build_fab_lot_index(PRODUCT, SOURCE, include_all=False)
    idx_dir = splittable._fab_lot_index_dir(PRODUCT)
    assert (idx_dir / "__fab_idx_root=R2").is_dir()

    (src_dir / "date=20240102" / "part.parquet").unlink()
    (src_dir / "date=20240102").rmdir()
    _clear_discovery_caches()

    assert splittable._build_fab_lot_index(PRODUCT, SOURCE, include_all=False)
    assert not (idx_dir / "__fab_idx_root=R2").exists(), \
        "삭제된 파일의 root 는 전체 재빌드로 제거돼야 한다"
    assert (idx_dir / "__fab_idx_root=R1").is_dir()
