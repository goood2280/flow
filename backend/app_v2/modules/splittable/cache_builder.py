import polars as pl
import json
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

# per-root 파일의 저장 포맷 세대. 포맷이 바뀌면(legacy 전치형 등) 시작 시 일괄
# 제거가 필요하지만, 같은 포맷의 재빌드는 기존 파일을 그대로 서빙하면서 root
# 단위로 원자 교체한다 — 재빌드 중 캐시 미스(전체 스캔 폴백) 구간이 없다.
_CACHE_FORMAT_VERSION = 2
_CACHE_FORMAT_MARKER = ".cache_format.json"

# 증분 재빌드용 per-root 내용 지문. 소스 ML_TABLE 이 갱신돼도 대부분의 root 는
# 내용이 그대로다 — 지문이 같은 root 는 collect/write 를 통째로 건너뛰어
# "root 몇 개 갱신 = 전체 재빌드(수 분)" 를 "변경 root 만(수 초)" 로 줄인다.
# 지문은 한 번의 스트리밍 스캔으로 계산하며, 파일이 없거나 스키마/해시 계산이
# 실패하면 None 을 반환해 기존 전체 재빌드로 폴백한다.
_ROOT_FINGERPRINT_FILE = ".root_fingerprints.json"
# 해시 합이 Int64 를 넘지 않도록 32bit 소수로 접는다 (root 당 수만 행이어도 여유).
_FINGERPRINT_FOLD_PRIME = 4294967291


def _cache_format_matches(out_dir: Path) -> bool:
    try:
        meta = json.loads((out_dir / _CACHE_FORMAT_MARKER).read_text("utf-8"))
        return int(meta.get("format") or 0) == _CACHE_FORMAT_VERSION
    except Exception:
        return False


def _write_cache_format_marker(out_dir: Path) -> None:
    try:
        (out_dir / _CACHE_FORMAT_MARKER).write_text(
            json.dumps({"format": _CACHE_FORMAT_VERSION}), "utf-8"
        )
    except Exception:
        pass


def _safe_root_filename(root_id: str) -> str:
    return f"{str(root_id).replace('/', '_').replace(chr(92), '_')}.parquet"


def _compute_root_fingerprints(lf, key_expr) -> dict | None:
    """Per-root (row_count, folded row-hash sum) in one streaming pass."""
    try:
        df = (
            lf.with_columns(key_expr.alias("__root"))
            .with_columns(
                (pl.struct(pl.all().exclude("__root")).hash(seed=0)
                 % _FINGERPRINT_FOLD_PRIME).cast(pl.Int64).alias("__h")
            )
            .group_by("__root")
            .agg(pl.len().alias("n"), pl.col("__h").sum().alias("h"))
            .collect()
        )
        return {
            str(root): [int(n), int(h or 0)]
            for root, n, h in df.iter_rows()
            if root is not None
        }
    except Exception as e:
        logger.warning("root fingerprint 계산 실패 — 전체 재빌드로 폴백: %s", e)
        return None


def _load_root_fingerprints(out_dir: Path) -> dict | None:
    try:
        data = json.loads((out_dir / _ROOT_FINGERPRINT_FILE).read_text("utf-8"))
        if int(data.get("format") or 0) != _CACHE_FORMAT_VERSION:
            return None
        roots = data.get("roots")
        return roots if isinstance(roots, dict) else None
    except Exception:
        return None


def _save_root_fingerprints(out_dir: Path, fingerprints: dict) -> None:
    try:
        (out_dir / _ROOT_FINGERPRINT_FILE).write_text(
            json.dumps({"format": _CACHE_FORMAT_VERSION, "roots": fingerprints}),
            "utf-8",
        )
    except Exception:
        pass


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


def _detect_col(columns, exact: str, contains: str = "") -> str | None:
    """Case-insensitive column detection (source ML_TABLE uses UPPER_CASE names)."""
    hit = next((c for c in columns if c.lower() == exact.lower()), None)
    if hit is None and contains:
        hit = next((c for c in columns if contains.lower() in c.lower()), None)
    return hit


def build_pivoted_cache_for_product(product: str, db_root: Path = None, product_path: Path = None):
    """
    Builds per-root Parquet caches for a specific product, one file per real
    root_lot_id, for instantaneous loading in SplitTable.

    v9.2: Stored in **native wide orientation** (wafer rows × parameter columns),
    exactly like the source ML_TABLE, instead of transposed (parameter rows ×
    wafer columns).  This lets the view fast-path use parquet **column
    projection** to read only the requested prefix (KNOB_/FAB_/…) or the custom
    columns — an index-speed read (~1-2ms) instead of loading a 3000-wide pivot.
    It also preserves LOT_ID so the "latest lot" label resolves instead of "-".

    The partition key is the **real** ROOT_LOT_ID (case-insensitive detection);
    previously a lowercase-only check silently missed UPPERCASE ROOT_LOT_ID and
    keyed files by a LOT_ID-prefix derivation, so fast-path lookups never hit.
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

        # Case-insensitive detection — source columns are UPPER_CASE.
        root_col = _detect_col(columns, "root_lot_id")
        lot_col = _detect_col(columns, "lot_id", contains="lot")
        wf_col = _detect_col(columns, "wafer_id", contains="wafer")
        if not wf_col:
            return False

        # Root key expression: prefer the authoritative ROOT_LOT_ID; only fall
        # back to a LOT_ID-prefix derivation when no root column exists at all.
        if root_col:
            key_expr = pl.col(root_col).cast(pl.Utf8)
        elif lot_col:
            key_expr = pl.col(lot_col).cast(pl.Utf8).str.split(".").list.first()
        else:
            return False

        # 같은 포맷의 재빌드는 기존 per-root 파일을 지우지 않는다 — 빌드가 도는
        # 동안 SplitTable 조회는 이전 파일을 계속 서빙하고, root 단위 tmp→replace
        # 로만 새 데이터로 교체된다. 포맷 세대가 다른 legacy 캐시(전치형/잘못된
        # 키)만 시작 시 일괄 제거해 신·구 포맷이 섞이지 않게 한다.
        if not _cache_format_matches(out_dir):
            for stale in out_dir.glob("*.parquet"):
                try:
                    stale.unlink()
                except Exception:
                    pass
            _write_cache_format_marker(out_dir)

        # 증분 재빌드: per-root 지문이 이전 빌드와 같고 파일도 살아 있으면 그
        # root 는 건너뛴다. 지문 계산 실패(None)면 전체 재빌드.
        fingerprints = _compute_root_fingerprints(lf, key_expr)
        if fingerprints is not None:
            unique_roots = list(fingerprints.keys())
            previous = _load_root_fingerprints(out_dir) or {}
            build_roots = [
                r for r in unique_roots
                if previous.get(r) != fingerprints[r]
                or not (out_dir / _safe_root_filename(r)).exists()
            ]
        else:
            unique_roots = (
                lf.select(key_expr.alias("__root")).unique().collect()["__root"]
                .drop_nulls().to_list()
            )
            build_roots = list(unique_roots)

        import gc
        partitions_built = 0

        i = 0
        total_roots = len(build_roots)
        while i < total_roots:
            # 매 청크 전에 현재 메모리 여유를 확인해 청크 크기를 조절한다. 백그라운드
            # 빌드가 메모리 보호 임계값을 건드려 기본 UI 작업을 막지 않도록 한다.
            memory_pressured = process_memory_high()
            chunk_size = _chunk_size(memory_pressured)
            chunk_roots = build_roots[i:i + chunk_size]
            i += chunk_size

            chunk_lf = lf.filter(key_expr.is_in(chunk_roots))
            # Guarantee a partition key column named "__root" even when the
            # source has no root column (LOT_ID-derived fallback).
            chunk_df = chunk_lf.with_columns(key_expr.alias("__root")).collect()

            partitions = chunk_df.partition_by("__root", as_dict=True)
            for root_id_tuple, part_df in partitions.items():
                if not root_id_tuple:
                    continue
                root_id_str = str(root_id_tuple[0] if isinstance(root_id_tuple, tuple) else root_id_tuple)
                if not root_id_str:
                    continue

                # Store native wide form; drop the helper key column. No melt,
                # no pivot — column projection at read time is the fast path.
                out_df = part_df.drop("__root")

                safe_root = str(root_id_str).replace("/", "_").replace("\\", "_")
                tmp_path = out_dir / f"{safe_root}.tmp.parquet"
                final_path = out_dir / f"{safe_root}.parquet"

                out_df.write_parquet(tmp_path)
                tmp_path.replace(final_path)
                partitions_built += 1

            del chunk_df
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

        # 소스에서 사라진 root 의 stale 파일은 전체 빌드가 끝난 뒤에만 정리한다.
        # 빌드 실패 시에는 이 지점에 도달하지 않으므로 이전 파일이 그대로 남아
        # 다음 성공 빌드까지 계속 서빙된다.
        expected = {_safe_root_filename(r) for r in unique_roots}
        for stale in out_dir.glob("*.parquet"):
            if stale.name not in expected:
                try:
                    stale.unlink()
                except Exception:
                    pass

        # 성공적으로 끝난 빌드만 지문을 기록한다 — 중간 실패 시 이전 지문이
        # 남아 다음 빌드가 변경 root 를 다시 잡는다.
        if fingerprints is not None:
            _save_root_fingerprints(out_dir, fingerprints)

        logger.info("Built pivoted cache for %s (%d/%d roots, %d unchanged skipped) in %.2fs",
                    canonical, partitions_built, len(unique_roots),
                    len(unique_roots) - len(build_roots), time.monotonic() - start_time)
        return True
    except Exception as e:
        logger.error("Failed to build pivot cache for %s: %s", canonical, e)
        return False

def get_pivoted_cache_path(product: str, root_lot_id: str) -> Path:
    safe_root = str(root_lot_id).replace("/", "_").replace("\\", "_")
    return CACHE_DIR / canonical_product_dir(product) / f"{safe_root}.parquet"
