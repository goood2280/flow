import polars as pl
import os
import time
import logging
from pathlib import Path
from core.paths import PATHS

logger = logging.getLogger(__name__)

CACHE_DIR = PATHS.db_cache_dir / "split_table" if hasattr(PATHS, "db_cache_dir") else Path("data/cache/split_table")

def build_pivoted_cache_for_product(product: str, db_root: Path = None):
    """
    Builds pre-pivoted Parquet caches for a specific product, partitioned by root_lot_id.
    This ensures instantaneous loading in SplitTable.
    """
    if db_root is None:
        db_root = PATHS.db_root if hasattr(PATHS, "db_root") else Path("data/db")
        
    product_path = db_root / f"ML_TABLE_{product}.parquet"
    if not product_path.exists():
        return False
        
    out_dir = CACHE_DIR / product
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
        CHUNK_SIZE = 5
        partitions_built = 0
        
        for i in range(0, len(unique_roots), CHUNK_SIZE):
            chunk_roots = unique_roots[i:i+CHUNK_SIZE]
            
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
            
            # API 우선 처리를 위한 CPU 제어권 반환 및 메모리 정리 시간 부여
            time.sleep(0.1)
            
        logger.info("Built pivoted cache for %s (%d roots) in %.2fs", product, partitions_built, time.monotonic() - start_time)
        return True
    except Exception as e:
        logger.error("Failed to build pivot cache for %s: %s", product, e)
        return False

def get_pivoted_cache_path(product: str, root_lot_id: str) -> Path:
    safe_root = str(root_lot_id).replace("/", "_").replace("\\", "_")
    return CACHE_DIR / product / f"{safe_root}.parquet"

