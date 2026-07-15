"""Per-root partitions of the latest-lot cache, synced at write time.

The monolithic ``lot_progress_latest_lot_by_root_wafer.parquet`` is the
canonical latest-lot table, but root-scoped readers (the SplitTable fab join,
fab-lot snapshots, history scope) need a single root's rows without paying a
full-file scan — the root filter needs cast+upper normalization, which defeats
parquet predicate pushdown on the monolithic file.

This module owns the normalized per-root layout
(``<cache>/lot_progress_latest_by_root/__latest_idx_root=<ROOT>/``) and is
called by BOTH monolithic writers (core.lot_progress_cache.export_lot_progress_parquet
and routers.splittable.export_latest_lot_step_cache) right after they replace
the monolithic file. Writing the partitions at the same moment as the data
itself means readers normally never see a stale partition set; the reader-side
signature check in routers.splittable remains only as self-heal for crashes
mid-write or files produced by older code.

Rewrites are incremental: a per-root content signature (volatile columns
excluded) is kept in the meta, and only roots whose content actually changed
are rewritten — a re-export with no lot movement (e.g. the filebrowser
/base-files background export) refreshes just the meta, and a normal refresh
touches only the handful of roots that moved.
"""

from __future__ import annotations

import datetime as dt
import logging
import shutil
from pathlib import Path

from core.utils import load_json, save_json

logger = logging.getLogger(__name__)

# Layout constants — shared with the readers in routers.splittable. The names
# predate this module (the layout was introduced as a reader-side cache) and
# must stay stable so existing deployments keep serving their partitions.
ROOT_KEY_COL = "__latest_idx_root"
PARTITION_DIR_NAME = "lot_progress_latest_by_root"
META_FILE = "_meta.json"
LEASE_NAME = "splittable_latest_lot_index"


def partitions_dir(monolithic_fp: Path) -> Path:
    return Path(monolithic_fp).parent / PARTITION_DIR_NAME


def meta_path(monolithic_fp: Path) -> Path:
    return partitions_dir(monolithic_fp) / META_FILE


def source_signature(monolithic_fp: Path) -> list:
    """(path, mtime, size) of the monolithic file — the partition staleness key."""
    fp = Path(monolithic_fp)
    try:
        st = fp.stat()
        return [str(fp.resolve()), st.st_mtime, st.st_size]
    except Exception:
        return [str(fp), 0.0, 0]


# 해시 합이 Int64 를 넘지 않도록 32bit 소수로 접는다 (root 당 수만 행이어도 여유).
_SIG_FOLD_PRIME = 4294967291
# per-root 지문에서 제외하는 휘발성 컬럼. update_time 은 export 마다
# generated_at 으로 통째로 바뀌지만 파티션 경로로는 어떤 소비자도 읽지 않는다
# (fab join override/history scope/snapshot 은 root/lot/wafer/step/tkout 만 사용;
# max update_time 배지는 monolithic 을 읽는다). 지문에 포함하면 모든 refresh 가
# "전 root 변경" 이 되어 증분이 무력화된다. 파티션에 update_time 소비자를
# 추가한다면 이 목록에서 빼야 한다.
_SIG_VOLATILE_COLS = ("update_time",)


def _keyed_lazy(df):
    import polars as pl

    return (
        df.lazy()
        .with_columns(_root_key_expr().alias(ROOT_KEY_COL))
        .filter(pl.col(ROOT_KEY_COL).is_not_null() & (pl.col(ROOT_KEY_COL) != ""))
    )


def _root_signatures(df) -> dict | None:
    """Per-root (row_count, folded row-hash sum), excluding volatile columns."""
    import polars as pl

    try:
        exclude = [ROOT_KEY_COL] + [c for c in _SIG_VOLATILE_COLS if c in df.columns]
        agg = (
            _keyed_lazy(df)
            .with_columns(
                (pl.struct(pl.all().exclude(exclude)).hash(seed=0)
                 % _SIG_FOLD_PRIME).cast(pl.Int64).alias("__h")
            )
            .group_by(ROOT_KEY_COL)
            .agg(pl.len().alias("n"), pl.col("__h").sum().alias("h"))
            .collect()
        )
        return {str(r): [int(n), int(h or 0)] for r, n, h in agg.iter_rows()}
    except Exception:
        logger.warning("latest_lot_partitions: root signature 계산 실패 — 전체 재작성으로 폴백",
                       exc_info=True)
        return None


def _root_key_expr():
    """Same normalization as routers.splittable._join_key_expr (trim + upper)."""
    import polars as pl

    return (
        pl.col("root_lot_id")
        .cast(pl.Utf8, strict=False)
        .str.strip_chars()
        .str.to_uppercase()
    )


def sync_partitions(monolithic_fp: Path, df=None, reason: str = "") -> bool:
    """Bring the per-root partition set in line with the monolithic file.

    ``df`` is the frame that was just written to ``monolithic_fp`` (writers
    pass it to avoid a read-back); ``None`` reads the file from disk (reader
    self-heal path). Guarded by a shared lease so dev/prod servers writing the
    same cache directory do not race; returns False when another server holds
    the lease (its build serves both).
    """
    import polars as pl

    fp = Path(monolithic_fp)
    if df is None:
        if not fp.is_file():
            return False
        try:
            df = pl.read_parquet(fp)
        except Exception as e:
            logger.warning("latest_lot_partitions: monolithic read failed (%s) %s: %s",
                           fp, type(e).__name__, e)
            return False
    if "root_lot_id" not in df.columns:
        return False

    lease_held = False
    try:
        from core import shared_lease
        lease_held = shared_lease.try_acquire(LEASE_NAME, ttl_sec=600.0)
        if not lease_held:
            logger.info("latest_lot_partitions: sync skipped — 다른 서버가 빌드 중")
            return False
    except Exception:
        lease_held = False  # lease infra optional; proceed without it
    try:
        return _sync_partitions_locked(fp, df, reason)
    finally:
        if lease_held:
            try:
                from core import shared_lease
                shared_lease.release(LEASE_NAME)
            except Exception:
                pass


def _sync_partitions_locked(fp: Path, df, reason: str) -> bool:
    idx_dir = partitions_dir(fp)
    meta_fp = meta_path(fp)
    old_meta = load_json(meta_fp, {}) or {}
    root_sigs = _root_signatures(df)
    meta = {
        "built_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source": str(fp),
        "source_sig": source_signature(fp),
        "root_sigs": root_sigs,
        "reason": reason,
    }

    old_sigs = old_meta.get("root_sigs")
    if root_sigs is not None and isinstance(old_sigs, dict) and idx_dir.is_dir():
        applied = _apply_incremental(idx_dir, df, old_sigs, root_sigs)
        if applied:
            save_json(meta_fp, meta)
            return True

    _rewrite_all_partitions(idx_dir, df)
    save_json(meta_fp, meta)
    return True


def _partition_dir_name(root: str) -> str:
    return f"{ROOT_KEY_COL}={root}"


def _apply_incremental(idx_dir: Path, df, old_sigs: dict, new_sigs: dict) -> bool:
    """Rewrite only roots whose content changed; False → caller full-rewrites.

    Removal is by literal directory name — hive-encoded names of special-char
    roots cannot be mapped back safely, so any unmappable removal falls back to
    the full rewrite instead of leaving a stale partition behind.
    """
    import polars as pl

    changed = [
        r for r in new_sigs
        if old_sigs.get(r) != new_sigs[r]
        or not (idx_dir / _partition_dir_name(r)).is_dir()
    ]
    removed = [r for r in old_sigs if r not in new_sigs]
    for root in removed:
        victim = idx_dir / _partition_dir_name(root)
        if not victim.is_dir():
            return False
    for root in removed:
        shutil.rmtree(idx_dir / _partition_dir_name(root), ignore_errors=True)
    if not changed:
        return True

    staging = idx_dir.with_name(idx_dir.name + ".delta")
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    lf = _keyed_lazy(df).filter(pl.col(ROOT_KEY_COL).is_in(changed))
    try:
        sink_target = pl.PartitionBy(
            staging, key=ROOT_KEY_COL, include_key=True,
            approximate_bytes_per_file="auto",
        )
        lf.sink_parquet(sink_target, mkdir=True, maintain_order=False)
    except Exception:
        part_df = lf.collect()
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        if part_df.height:
            part_df.write_parquet(staging, partition_by=ROOT_KEY_COL)
    try:
        written = {p.name for p in staging.iterdir() if p.is_dir()}
        expected = {_partition_dir_name(r) for r in changed}
        if written != expected:
            # hive 인코딩으로 디렉터리명이 리터럴과 다르면 root 단위 교체가
            # 어긋난다 — 전체 재작성으로 폴백.
            return False
        for child in sorted(staging.iterdir()):
            if not child.is_dir():
                continue
            target = idx_dir / child.name
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            child.replace(target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return True


def _rewrite_all_partitions(idx_dir: Path, df) -> None:
    import polars as pl

    lf = _keyed_lazy(df)
    tmp_dir = idx_dir.with_name(idx_dir.name + ".tmp")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        sink_target = pl.PartitionBy(
            tmp_dir, key=ROOT_KEY_COL, include_key=True,
            approximate_bytes_per_file="auto",
        )
        lf.sink_parquet(sink_target, mkdir=True, maintain_order=False)
    except Exception:
        # Older polars / sink edge cases — fall back to an eager partitioned write.
        part_df = lf.collect()
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
        tmp_dir.mkdir(parents=True, exist_ok=True)
        if part_df.height:
            part_df.write_parquet(tmp_dir, partition_by=ROOT_KEY_COL)
    if idx_dir.exists():
        shutil.rmtree(idx_dir, ignore_errors=True)
    tmp_dir.replace(idx_dir)
