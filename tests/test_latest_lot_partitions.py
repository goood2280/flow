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

from core import latest_lot_partitions  # noqa: E402
from core import lot_progress_cache  # noqa: E402
from routers import splittable  # noqa: E402


def _sample_df() -> pl.DataFrame:
    return pl.DataFrame({
        "product": ["ML_TABLE_P", "ML_TABLE_P", "ML_TABLE_P"],
        "root_lot_id": ["r100", "R100", "R200"],
        "wafer_id": ["1", "2", "1"],
        "lot_id": ["F100", "F100", "F200"],
        "step_id": ["", "", ""],
        "function_step": ["", "", ""],
        "tkout_time": ["2026-07-01T00:00:00"] * 3,
        "update_time": ["2026-07-01T01:00:00"] * 3,
    })


def _write_monolithic(fp: Path, df: pl.DataFrame) -> None:
    fp.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(fp)


def test_sync_partitions_layout_matches_reader(tmp_path, monkeypatch):
    fp = tmp_path / "cache" / "lot_progress_latest_lot_by_root_wafer.parquet"
    _write_monolithic(fp, _sample_df())

    assert latest_lot_partitions.sync_partitions(fp) is True

    idx_dir = latest_lot_partitions.partitions_dir(fp)
    # root key is trim+upper normalized — r100/R100 land in the same partition
    assert (idx_dir / "__latest_idx_root=R100").is_dir()
    assert (idx_dir / "__latest_idx_root=R200").is_dir()

    # the splittable reader accepts the layout as fresh and serves one root
    monkeypatch.setattr(splittable, "_db_base", lambda: tmp_path)
    with splittable._LATEST_IDX_FRESH_LOCK:
        splittable._LATEST_IDX_FRESH_CACHE.clear()
    part_lf = splittable._latest_lot_index_partition_lf("", "r100")
    assert part_lf is not None
    part = part_lf.collect()
    assert part.height == 2
    assert "__latest_idx_root" not in part.columns
    assert set(part["root_lot_id"].to_list()) == {"r100", "R100"}


def test_sync_partitions_short_circuits_on_same_content(tmp_path):
    fp = tmp_path / "cache" / "lot_progress_latest_lot_by_root_wafer.parquet"
    df = _sample_df()
    _write_monolithic(fp, df)
    assert latest_lot_partitions.sync_partitions(fp, df=df) is True

    part_file = next((latest_lot_partitions.partitions_dir(fp) / "__latest_idx_root=R100").glob("*.parquet"))
    first_mtime = part_file.stat().st_mtime_ns

    # identical content re-exported (mtime churn only)
    time.sleep(0.02)
    _write_monolithic(fp, df)
    assert latest_lot_partitions.sync_partitions(fp, df=df) is True

    assert part_file.stat().st_mtime_ns == first_mtime, \
        "partition files must not be rewritten when content is unchanged"
    meta = latest_lot_partitions.__dict__["load_json"](
        latest_lot_partitions.meta_path(fp), {})
    assert meta.get("source_sig") == latest_lot_partitions.source_signature(fp), \
        "meta staleness key must be refreshed to the new monolithic signature"

    # changed content → partitions rewritten
    changed = df.with_columns(pl.lit("F999").alias("lot_id"))
    _write_monolithic(fp, changed)
    assert latest_lot_partitions.sync_partitions(fp, df=changed) is True
    fresh = pl.read_parquet(next(
        (latest_lot_partitions.partitions_dir(fp) / "__latest_idx_root=R100").glob("*.parquet")))
    assert set(fresh["lot_id"].to_list()) == {"F999"}


def test_sync_partitions_incremental_rewrites_only_changed_roots(tmp_path):
    fp = tmp_path / "cache" / "lot_progress_latest_lot_by_root_wafer.parquet"
    df = _sample_df()
    _write_monolithic(fp, df)
    assert latest_lot_partitions.sync_partitions(fp, df=df) is True
    idx = latest_lot_partitions.partitions_dir(fp)
    r100_file = next((idx / "__latest_idx_root=R100").glob("*.parquet"))
    r200_file = next((idx / "__latest_idx_root=R200").glob("*.parquet"))
    r100_mtime, r200_mtime = r100_file.stat().st_mtime_ns, r200_file.stat().st_mtime_ns

    # update_time 만 바뀐 재-export → 어떤 파티션도 재작성하지 않는다
    time.sleep(0.02)
    bumped = df.with_columns(pl.lit("2026-07-02T00:00:00").alias("update_time"))
    _write_monolithic(fp, bumped)
    assert latest_lot_partitions.sync_partitions(fp, df=bumped) is True
    assert r100_file.stat().st_mtime_ns == r100_mtime
    assert r200_file.stat().st_mtime_ns == r200_mtime

    # R200 의 lot 만 이동 → R200 파티션만 재작성
    time.sleep(0.02)
    moved = bumped.with_columns(
        pl.when(pl.col("root_lot_id") == "R200")
        .then(pl.lit("F201")).otherwise(pl.col("lot_id")).alias("lot_id"))
    _write_monolithic(fp, moved)
    assert latest_lot_partitions.sync_partitions(fp, df=moved) is True
    assert r100_file.stat().st_mtime_ns == r100_mtime, "unchanged root must not be rewritten"
    new_r200 = pl.read_parquet(next((idx / "__latest_idx_root=R200").glob("*.parquet")))
    assert new_r200["lot_id"].to_list() == ["F201"]

    # root 가 사라지면 해당 파티션 디렉터리 제거
    dropped = moved.filter(pl.col("root_lot_id").str.to_uppercase() != "R200")
    _write_monolithic(fp, dropped)
    assert latest_lot_partitions.sync_partitions(fp, df=dropped) is True
    assert not (idx / "__latest_idx_root=R200").exists()
    assert (idx / "__latest_idx_root=R100").is_dir()


def test_export_lot_progress_parquet_writes_partitions(tmp_path, monkeypatch):
    mono_fp = tmp_path / "Fab" / "cache" / "lot_progress_latest_lot_by_root_wafer.parquet"
    copy_fp = tmp_path / "flow-data" / "cache" / "lot_progress" / "lot_wf_current.parquet"
    monkeypatch.setattr(lot_progress_cache, "filebrowser_cache_parquet_file", lambda: mono_fp)
    monkeypatch.setattr(lot_progress_cache, "cache_parquet_file", lambda: copy_fp)

    state = {
        "generated_at": "2026-07-15T00:00:00",
        "items": [
            {"product": "ML_TABLE_P", "root_lot_id": "R300", "wafer_id": "1",
             "lot_id": "F300", "step_id": "S1", "tkout_time": "2026-07-14T00:00:00"},
        ],
    }
    result = lot_progress_cache.export_lot_progress_parquet(state)
    assert result["ok"] is True and result["rows"] == 1
    assert mono_fp.is_file() and copy_fp.is_file()
    # per-root partitions written at the same moment as the monolithic file
    part_dir = latest_lot_partitions.partitions_dir(mono_fp) / "__latest_idx_root=R300"
    assert part_dir.is_dir()
    part = pl.read_parquet(next(part_dir.glob("*.parquet")))
    assert part["lot_id"].to_list() == ["F300"]


def test_fab_lot_index_sweep_enqueues_only_on_drift(tmp_path, monkeypatch):
    base = tmp_path / "cache" / "fab_lot_index"
    (base / "ML_TABLE_P").mkdir(parents=True)
    monkeypatch.setattr(splittable, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_current_fab_override",
                        lambda product: (product, {}, "FABSRC"))
    monkeypatch.setattr(splittable, "_foreground_global_fab_scan_enabled", lambda: False)
    live_sig = [["f.parquet", 1, 10]]
    monkeypatch.setattr(splittable, "_fab_source_signature",
                        lambda fab_source, include_all: list(live_sig))

    enqueued = []
    monkeypatch.setattr(splittable, "_enqueue_fab_lot_index_build",
                        lambda product, fab_source="", include_all=False, reason="":
                        enqueued.append((product, reason)) or True)

    # missing meta → rebuild
    splittable._fab_lot_index_sweep_once()
    assert enqueued == [("ML_TABLE_P", "sweep_missing_meta")]

    # fresh meta → no rebuild
    enqueued.clear()
    monkeypatch.setattr(splittable, "_fab_lot_index_read_meta",
                        lambda product: {"source_sig": list(live_sig)})
    splittable._fab_lot_index_sweep_once()
    assert enqueued == []

    # drifted signature → rebuild
    monkeypatch.setattr(splittable, "_fab_lot_index_read_meta",
                        lambda product: {"source_sig": [["f.parquet", 1, 99]]})
    splittable._fab_lot_index_sweep_once()
    assert enqueued == [("ML_TABLE_P", "sweep_stale")]
