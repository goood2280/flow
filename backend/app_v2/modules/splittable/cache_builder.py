import polars as pl
import os
import time
import logging
from pathlib import Path
from core.paths import PATHS
from core import request_priority

try:  # runtime_limits is optional in some minimal contexts (e.g. isolated tests)
    from core.runtime_limits import process_memory_high
except Exception:  # pragma: no cover - defensive import
    def process_memory_high(reserve_gb: float = 1.0) -> bool:  # type: ignore
        return False

logger = logging.getLogger(__name__)

CACHE_DIR = PATHS.db_cache_dir / "split_table" if hasattr(PATHS, "db_cache_dir") else Path("data/cache/split_table")

# Background builds run in chunks so an interactive read (SplitTable load, file
# view) always has spare RAM/CPU. When the process is already near its memory
# budget we shrink the chunk and wait longer between chunks so the reserved UI
# lane keeps working instead of tripping the memory guard.
_CHUNK_SIZE_DEFAULT = 5
_CHUNK_SIZE_UNDER_MEMORY_PRESSURE = 2


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(os.environ.get(name, "") or default)
    except Exception:
        value = default
    return max(lo, min(hi, value))


def _chunk_size(memory_pressured: bool) -> int:
    if memory_pressured:
        return _int_env(
            "FLOW_PIVOT_CACHE_CHUNK_SIZE_MIN", _CHUNK_SIZE_UNDER_MEMORY_PRESSURE, 1, 64
        )
    return _int_env("FLOW_PIVOT_CACHE_CHUNK_SIZE", _CHUNK_SIZE_DEFAULT, 1, 256)


def canonical_product_dir(product: str) -> str:
    """Single naming rule for the pivot cache directory: full upper-cased
    ML_TABLE_* name, whether callers pass "PRODA" or "ml_table_proda"."""
    raw = str(product or "").strip()
    if not raw:
        return ""
    if raw.casefold().startswith("ml_table_"):
        raw = raw[len("ML_TABLE_"):]
    return f"ML_TABLE_{raw}".upper() if raw else ""


def build_pivoted_cache_for_product(product: str, db_root: Path = None, product_path: Path = None):
    """
    Builds pre-pivoted Parquet caches for a specific product, partitioned by root_lot_id.
    This ensures instantaneous loading in SplitTable.
    """
    canonical = canonical_product_dir(product)
    if not canonical:
        return False

    if product_path is None:
        if db_root is None:
            db_root = PATHS.db_root if hasattr(PATHS, "db_root") else Path("data/db")
        product_path = db_root / f"{canonical}.parquet"
    if not product_path.exists():
        return False

    out_dir = CACHE_DIR / canonical
    out_dir.mkdir(parents=True, exist_ok=True)

    start_time = time.monotonic()
    try:
        lf = pl.scan_parquet(product_path)
        schema = lf.collect_schema()
        columns = schema.names()

        lot_col = next((c for c in columns if c.lower() == "lot_id"), None) or next((c for c in columns if "lot" in c.lower()), None)
        wf_col = next((c for c in columns if c.lower() == "wafer_id"), None) or next((c for c in columns if "wafer" in c.lower()), None)

        if not lot_col or not wf_col:
            return False

        if "root_lot_id" in columns:
            roots_df = lf.select("root_lot_id").unique().collect()
            unique_roots = roots_df["root_lot_id"].drop_nulls().to_list()
        else:
            roots_df = lf.select(pl.col(lot_col).cast(pl.Utf8).str.split(".").list.first().alias("root_lot_id")).unique().collect()
            unique_roots = roots_df["root_lot_id"].drop_nulls().to_list()

        import gc
        partitions_built = 0

        i = 0
        total_roots = len(unique_roots)
        while i < total_roots:
            # 매 청크 전에 현재 메모리 여유를 확인해 청크 크기를 조절한다. 백그라운드
            # 빌드가 메모리 보호 임계값을 건드려 기본 UI 작업을 막지 않도록 한다.
            memory_pressured = process_memory_high()
            chunk_size = _chunk_size(memory_pressured)
            chunk_roots = unique_roots[i:i + chunk_size]
            i += chunk_size

            if "root_lot_id" in columns:
                chunk_lf = lf.filter(pl.col("root_lot_id").is_in(chunk_roots))
            else:
                chunk_lf = lf.filter(pl.col(lot_col).cast(pl.Utf8).str.split(".").list.first().is_in(chunk_roots))
                chunk_lf = chunk_lf.with_columns(pl.col(lot_col).cast(pl.Utf8).str.split(".").list.first().alias("root_lot_id"))

            chunk_df = chunk_lf.collect()

            id_vars = ["root_lot_id", lot_col, wf_col]
            value_vars = [c for c in columns if c not in id_vars]
            if "root_lot_id" in value_vars:
                value_vars.remove("root_lot_id")

            melted = chunk_df.unpivot(index=id_vars, on=value_vars, variable_name="parameter", value_name="value").drop_nulls("value")

            partitions = melted.partition_by("root_lot_id", as_dict=True)
            for root_id_tuple, part_df in partitions.items():
                if not root_id_tuple: continue

                root_id_str = str(root_id_tuple[0] if isinstance(root_id_tuple, tuple) else root_id_tuple)
                if not root_id_str: continue

                pivoted = part_df.pivot(
                    values="value",
                    index=["parameter"],
                    on=wf_col,
                    aggregate_function="first"
                ).sort(["parameter"])

                safe_root = str(root_id_str).replace("/", "_").replace("\\", "_")
                tmp_path = out_dir / f"{safe_root}.tmp.parquet"
                final_path = out_dir / f"{safe_root}.parquet"

                pivoted.write_parquet(tmp_path)
                tmp_path.replace(final_path)
                partitions_built += 1

            del chunk_df
            del melted
            del partitions
            gc.collect()

            # API 우선 처리 — 사용자 요청이 진행 중이면 다음 청크를 미룬다.
            # 메모리가 빠듯하면 더 길게 쉬면서 RSS 가 내려갈 시간을 준다.
            if memory_pressured:
                time.sleep(0.5)
                request_priority.yield_to_users(max_wait_sec=60.0)
            else:
                time.sleep(0.1)
                request_priority.yield_to_users(max_wait_sec=20.0)

        logger.info("Built pivoted cache for %s (%d roots) in %.2fs", canonical, partitions_built, time.monotonic() - start_time)
        return True
    except Exception as e:
        logger.error("Failed to build pivot cache for %s: %s", canonical, e)
        return False

def get_pivoted_cache_path(product: str, root_lot_id: str) -> Path:
    safe_root = str(root_lot_id).replace("/", "_").replace("\\", "_")
    return CACHE_DIR / canonical_product_dir(product) / f"{safe_root}.parquet"
