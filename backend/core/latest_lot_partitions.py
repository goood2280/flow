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

Repeated exports of identical content (e.g. the filebrowser /base-files
background export) short-circuit on a content signature: only the meta's
source signature is refreshed, the ~per-root files are not rewritten.
"""

from __future__ import annotations

import datetime as dt
import hashlib
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


def _content_signature(df) -> str:
    """Order-independent row-content hash of the frame (schema + values)."""
    try:
        hashes = sorted(df.hash_rows(seed=0).to_list())
        digest = hashlib.sha1()
        for value in hashes:
            digest.update(int(value).to_bytes(8, "little"))
        return f"{len(hashes)}:{','.join(df.columns)}:{digest.hexdigest()}"
    except Exception:
        return ""


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
    import polars as pl

    idx_dir = partitions_dir(fp)
    source_sig = source_signature(fp)
    content_sig = _content_signature(df)
    meta_fp = meta_path(fp)
    old_meta = load_json(meta_fp, {}) or {}
    meta = {
        "built_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source": str(fp),
        "source_sig": source_sig,
        "content_sig": content_sig,
        "reason": reason,
    }

    # Same content re-exported (mtime churn only) → the partition files are
    # already correct; refresh the staleness key without rewriting them.
    if (
        content_sig
        and old_meta.get("content_sig") == content_sig
        and idx_dir.is_dir()
    ):
        save_json(meta_fp, meta)
        return True

    lf = (
        df.lazy()
        .with_columns(_root_key_expr().alias(ROOT_KEY_COL))
        .filter(pl.col(ROOT_KEY_COL).is_not_null() & (pl.col(ROOT_KEY_COL) != ""))
    )
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
    save_json(meta_fp, meta)
    return True
