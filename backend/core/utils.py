"""core/utils.py v4.0.0 - shared backend helpers
Extracted common patterns from routers to reduce duplication.
"""
import json, datetime, re, io, csv as csv_mod, time
from pathlib import Path
from fastapi import HTTPException
import polars as pl
from core.paths import PATHS


# ──────────────────────────────────────────────────────────────────────────
# Polars helpers
# ──────────────────────────────────────────────────────────────────────────
_STR = getattr(pl, "Utf8", None) or getattr(pl, "String", pl.Object)

DATA_EXTENSIONS = {".parquet", ".csv"}
MAX_WAFER_ID = 25
WAFER_COLUMN_CANDIDATES = ("wafer_id", "wf_id")
INLINE_COORD_COLUMNS = ("shot_x", "shot_y")
_DATA_FILE_COUNT_CACHE_TTL_SEC = 20.0
_DATA_FILE_COUNT_CACHE: dict[tuple[str, int], tuple[float, float, int]] = {}


def is_cat(d) -> bool:
    """Check if dtype is Categorical / Enum (needs cast for concat/filter)."""
    s = str(d)
    return d == pl.Categorical or "ategorical" in s or "Enum" in s


def cast_cats(df):
    """Cast all Categorical/Enum cols to Utf8 (strict=False)."""
    casts = [pl.col(n).cast(_STR, strict=False) for n, d in df.schema.items() if is_cat(d)]
    return df.with_columns(casts) if casts else df


def cast_all_str(df):
    """Force every column to Utf8 so concat always works."""
    return df.with_columns([pl.col(c).cast(_STR, strict=False) for c in df.columns])


def wafer_column(columns) -> str | None:
    """Return the wafer id column if a schema has one, case-insensitively."""
    by_lower = {str(c).lower(): str(c) for c in (columns or [])}
    for name in WAFER_COLUMN_CANDIDATES:
        hit = by_lower.get(name)
        if hit:
            return hit
    return None


def _wafer_number_expr(column: str) -> pl.Expr:
    text = (
        pl.col(column)
        .cast(_STR, strict=False)
        .str.strip_chars()
        .str.to_uppercase()
        .str.replace(r"^(?:#|WAFER|WF|W)\s*", "")
    )
    return text.cast(pl.Float64, strict=False)


def valid_wafer_expr(column: str) -> pl.Expr:
    """Return rows that have a positive integer wafer token.

    Source DBs can contain synthetic row ids such as 1000 in wafer_id. Those
    are normalized to physical wafer ids 1..25 instead of being dropped.
    Decimal, zero, null, and non-numeric wafer tokens are still invalid.
    """
    num = _wafer_number_expr(column)
    as_int = num.cast(pl.Int64, strict=False).cast(pl.Float64, strict=False)
    return ((num >= 1) & (num == as_int)).fill_null(False)


def physical_wafer_expr(column: str) -> pl.Expr:
    num = _wafer_number_expr(column).cast(pl.Int64, strict=False)
    return (((num - 1) % MAX_WAFER_ID) + 1).cast(_STR, strict=False)


def filter_valid_wafer_ids_df(df: pl.DataFrame) -> pl.DataFrame:
    """Normalize wafer ids to 1..25 when a wafer column exists."""
    if df is None:
        return df
    col = wafer_column(df.columns)
    if not col:
        return df
    try:
        return df.filter(valid_wafer_expr(col)).with_columns(physical_wafer_expr(col).alias(col))
    except Exception:
        return df


def filter_valid_wafer_ids_lazy(lf: pl.LazyFrame, columns: list[str] | None = None) -> pl.LazyFrame:
    """Normalize wafer ids to 1..25 when a wafer column exists."""
    if lf is None:
        return lf
    if columns is None:
        try:
            columns = list(lf.collect_schema().names())
        except Exception:
            try:
                columns = list(lf.schema.keys())
            except Exception:
                columns = []
    col = wafer_column(columns)
    if not col:
        return lf
    try:
        return lf.filter(valid_wafer_expr(col)).with_columns(physical_wafer_expr(col).alias(col))
    except Exception:
        return lf


def is_inline_source(root: str = "", file: str = "") -> bool:
    text = f"{root or ''}/{file or ''}".upper()
    return "INLINE" in text


def _inline_coord_columns(columns) -> list[str]:
    blocked = {c.lower() for c in INLINE_COORD_COLUMNS}
    return [str(c) for c in (columns or []) if str(c).lower() in blocked]


def drop_inline_coord_columns_df(df: pl.DataFrame) -> pl.DataFrame:
    if df is None:
        return df
    cols = _inline_coord_columns(df.columns)
    if not cols:
        return df
    try:
        return df.drop(cols)
    except Exception:
        return df


def drop_inline_coord_columns_lazy(lf: pl.LazyFrame) -> pl.LazyFrame:
    if lf is None:
        return lf
    try:
        columns = lf.collect_schema().names()
    except Exception:
        try:
            columns = list(lf.schema.keys())
        except Exception:
            columns = []
    cols = _inline_coord_columns(columns)
    if not cols:
        return lf
    try:
        return lf.drop(cols)
    except Exception:
        return lf


def normalize_source_df(df: pl.DataFrame, *, root: str = "", file: str = "") -> pl.DataFrame:
    if is_inline_source(root, file):
        return drop_inline_coord_columns_df(df)
    return df


def normalize_source_lazy(lf: pl.LazyFrame, *, root: str = "", file: str = "") -> pl.LazyFrame:
    if is_inline_source(root, file):
        return drop_inline_coord_columns_lazy(lf)
    return lf


# ──────────────────────────────────────────────────────────────────────────
# Data source discovery & reading
# ──────────────────────────────────────────────────────────────────────────
def detect_structure(prod_path: Path) -> str:
    """Detect directory layout: hive (date=YYYY-MM-DD/*.parquet) / flat / unknown."""
    if not prod_path.is_dir():
        return "unknown"
    for item in prod_path.iterdir():
        if item.is_dir() and item.name.startswith("date="):
            return "hive"
        if item.is_file() and item.suffix in DATA_EXTENSIONS:
            return "flat"
    return "unknown"


def has_data_files(directory: Path) -> bool:
    """Fast existence check for data files without materializing large trees."""
    if not directory.is_dir():
        return False
    try:
        for fp in directory.rglob("*"):
            if fp.is_file() and fp.suffix.lower() in DATA_EXTENSIONS:
                return True
    except Exception:
        return False
    return False


def first_data_file(directory: Path) -> Path | None:
    """Return the first data file under a directory using a single bounded walk."""
    if not directory.is_dir():
        return None
    try:
        for fp in directory.rglob("*"):
            if fp.is_file() and fp.suffix.lower() in DATA_EXTENSIONS:
                return fp
    except Exception:
        return None
    return None


def data_files_limited(directory: Path, limit: int = 2000) -> list[Path]:
    """Return up to ``limit`` data files without exhausting huge partition trees."""
    if not directory.is_dir():
        return []
    try:
        limit = max(1, int(limit or 2000))
    except Exception:
        limit = 2000
    out: list[Path] = []
    try:
        for fp in directory.rglob("*"):
            if fp.is_file() and fp.suffix.lower() in DATA_EXTENSIONS:
                out.append(fp)
                if len(out) >= limit:
                    break
    except Exception:
        pass
    return sorted(out)


def count_data_files(directory: Path, limit: int = 2000) -> int:
    """Bounded recursive data-file count for sidebar/source metadata.

    Large production DB roots can contain tens of thousands of partitions. UI
    badges only need a quick approximate count, so stop after ``limit``.
    Callers that need the full file list should keep using ``_glob_data_files``.
    """
    if not directory.is_dir():
        return 0
    try:
        limit = max(1, int(limit or 2000))
    except Exception:
        limit = 2000
    try:
        cache_key = (str(directory.resolve()), limit)
        dir_mtime = directory.stat().st_mtime
        cached = _DATA_FILE_COUNT_CACHE.get(cache_key)
        now = time.monotonic()
        if cached and cached[1] == dir_mtime and now - cached[0] < _DATA_FILE_COUNT_CACHE_TTL_SEC:
            return cached[2]
    except Exception:
        cache_key = None
        now = time.monotonic()
    count = 0
    try:
        for fp in directory.rglob("*"):
            if fp.is_file() and fp.suffix.lower() in DATA_EXTENSIONS:
                count += 1
                if count >= limit:
                    if cache_key is not None:
                        _DATA_FILE_COUNT_CACHE[cache_key] = (now, dir_mtime, count)
                    return count
    except Exception:
        return count
    if cache_key is not None:
        _DATA_FILE_COUNT_CACHE[cache_key] = (now, dir_mtime, count)
    return count


def iter_source_product_dirs(root_dir: Path):
    """Yield logical ``(product_name, directory, structure)`` under a DB root.

    Supports both legacy ``<root>/<product>/date=...`` layouts and hive table
    layouts such as ``<root>/<table>/product=PRODA/date=...`` without doing a
    full recursive file list for every candidate.
    """
    if not root_dir.is_dir():
        return
    seen: set[str] = set()

    def _emit(name: str, path: Path, structure: str):
        key = str(name or "").casefold()
        if not key or key in seen:
            return
        seen.add(key)
        yield (name, path, structure)

    try:
        children = [p for p in sorted(root_dir.iterdir(), key=lambda x: x.name.lower()) if p.is_dir()]
    except Exception:
        return

    for child in children:
        if child.name.startswith((".", "_", "__")):
            continue
        if child.name.startswith("product="):
            st = detect_structure(child)
            if st in ("hive", "flat") or has_data_files(child):
                yield from _emit(child.name[len("product="):], child, st if st in ("hive", "flat") else "hive")
            continue
        st = detect_structure(child)
        if st in ("hive", "flat"):
            yield from _emit(child.name, child, st)

    for table_dir in children:
        if table_dir.name.startswith((".", "_", "__", "product=")):
            continue
        try:
            parts = [p for p in sorted(table_dir.iterdir(), key=lambda x: x.name.lower()) if p.is_dir()]
        except Exception:
            continue
        for part in parts:
            if not part.name.startswith("product="):
                continue
            st = detect_structure(part)
            if st in ("hive", "flat") or has_data_files(part):
                yield from _emit(part.name[len("product="):], part, st if st in ("hive", "flat") else "hive")


def read_one_file(fp: Path):
    """Read single parquet or CSV with cats cast. Returns None on failure."""
    try:
        if fp.suffix == ".csv":
            return filter_valid_wafer_ids_df(cast_cats(pl.read_csv(str(fp), infer_schema_length=5000, try_parse_dates=False)))
        return filter_valid_wafer_ids_df(cast_cats(pl.read_parquet(str(fp))))
    except Exception:
        return None


# Keep old name as alias for compatibility
read_one_parquet = read_one_file


def scan_one_file(fp: Path):
    """Lazy-scan a single file. Returns LazyFrame or None."""
    try:
        if fp.suffix == ".csv":
            return filter_valid_wafer_ids_lazy(pl.scan_csv(str(fp), infer_schema_length=5000, try_parse_dates=False))
        return filter_valid_wafer_ids_lazy(pl.scan_parquet(str(fp)))
    except Exception:
        return None


def _source_product_paths(db_base: Path, root: str, product: str) -> list[Path]:
    """Resolve product directories without a deep recursive walk.

    Covers both legacy `<root>/<product>` and table hive
    `<root>/<table>/product=<product>` layouts.  Returning all matching table
    partitions keeps lazy_read_source from falling back to eager reads.
    """
    root_path = db_base / root
    if not root_path.is_dir():
        return []
    target = str(product or "").casefold()
    hive_target = f"product={product}".casefold()
    out: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        try:
            key = str(path.resolve())
        except Exception:
            key = str(path)
        if key not in seen and path.is_dir():
            seen.add(key)
            out.append(path)

    direct = root_path / product
    if direct.is_dir():
        add(direct)

    try:
        children = [p for p in sorted(root_path.iterdir(), key=lambda x: x.name.lower()) if p.is_dir()]
    except Exception:
        return out

    for child in children:
        low = child.name.casefold()
        if low == target or low == hive_target:
            add(child)
        if child.name.startswith((".", "_", "__")):
            continue
        try:
            inner_dirs = [p for p in child.iterdir() if p.is_dir()]
        except Exception:
            continue
        for inner in inner_dirs:
            if inner.name.casefold() == hive_target:
                add(inner)
    return out


def lazy_read_source(source_type: str = "", root: str = "", product: str = "",
                     file: str = "", max_files: int | None = 20,
                     recent_days: int | None = 30,
                     latest_only: bool = False):
    """Memory-efficient lazy reader. Returns LazyFrame (call .collect() when ready).
    For 30-60GB datasets on 2-core/6GB machines — push filters before collect.

    v8.8.33: partition pruning (recent_days 기본 30) + hive_partitioning.
    latest_only=True 이면 최신 date 파티션/파일 날짜만 scan 해서 클릭 preview를 빠르게 반환.
    recent_days=None 이면 전체 파티션 스캔 (역사 조회용).
    """
    DB_BASE = PATHS.db_root
    if source_type == "base_file":
        # base_file is a compatibility name; files live at DB root level.
        fp = PATHS.base_root / file
        if not fp.is_file():
            return None
        return normalize_source_lazy(scan_one_file(fp), root=root, file=file)
    if source_type == "root_parquet" or (file and not product):
        fp = DB_BASE / file
        if not fp.is_file():
            return None
        return normalize_source_lazy(scan_one_file(fp), root=root, file=file)

    prod_paths = _source_product_paths(DB_BASE, root, product)
    if not prod_paths:
        return None

    # v8.8.33: parquet_perf.scan_parquet_perf 로 통합 — hive + partition pruning.
    pq_files = sorted(fp for prod_path in prod_paths for fp in prod_path.rglob("*.parquet"))
    if pq_files:
        try:
            from core.parquet_perf import scan_parquet_perf
            lf = scan_parquet_perf(pq_files, hive=True,
                                   recent_days=recent_days, max_files=max_files,
                                   latest_only=latest_only)
            if lf is not None:
                return normalize_source_lazy(filter_valid_wafer_ids_lazy(lf), root=root, file=file)
        except Exception:
            pass
        # Fallback: scan individually and concat
        frames = []
        fallback_files = pq_files if max_files is None else pq_files[-max_files:]
        for f in fallback_files:
            lf = scan_one_file(f)
            if lf is not None:
                frames.append(lf)
        if frames:
            return normalize_source_lazy(filter_valid_wafer_ids_lazy(pl.concat(frames, how="diagonal_relaxed")), root=root, file=file)

    # CSV fallback
    csv_files = sorted(fp for prod_path in prod_paths for fp in prod_path.rglob("*.csv"))
    if csv_files:
        fallback_files = csv_files if max_files is None else csv_files[-max_files:]
        frames = [scan_one_file(f) for f in fallback_files]
        frames = [f for f in frames if f is not None]
        if frames:
            return normalize_source_lazy(filter_valid_wafer_ids_lazy(pl.concat(frames, how="diagonal_relaxed")), root=root, file=file)

    return None


def _glob_data_files(directory: Path):
    """Glob both *.parquet and *.csv recursively, sorted."""
    files = []
    for ext in DATA_EXTENSIONS:
        files.extend(directory.rglob(f"*{ext}"))
    return sorted(files)


def source_data_files(source_type: str = "", root: str = "", product: str = "",
                      file: str = "", max_files: int | None = None) -> list[Path]:
    """Resolve physical data files for a Flow DB source without reading them."""
    DB_BASE = PATHS.db_root
    files: list[Path] = []
    if source_type == "base_file":
        fp = PATHS.base_root / file
        files = [fp] if fp.is_file() and fp.suffix.lower() in DATA_EXTENSIONS else []
    elif source_type == "root_parquet" or (file and not product):
        fp = DB_BASE / file
        files = [fp] if fp.is_file() and fp.suffix.lower() in DATA_EXTENSIONS else []
    else:
        prod_paths = _source_product_paths(DB_BASE, root, product)
        files = sorted(
            fp
            for prod_path in prod_paths
            for fp in _glob_data_files(prod_path)
            if fp.suffix.lower() in DATA_EXTENSIONS
        )
    if max_files is not None and max_files > 0 and len(files) > max_files:
        return files[-max_files:]
    return files


def read_source(source_type: str = "", root: str = "", product: str = "",
                file: str = "", max_files: int | None = 40):
    """Universal reader covering all DB types:
      - base_file:   DB_ROOT/<file> (parquet or CSV) — DB 루트 최상단 단일 파일
      - root_parquet: DB_BASE/<file> (parquet or CSV)
      - flat/hive/auto: DB_BASE/<root>/<product>/*.{parquet,csv} (recursive)
    """
    DB_BASE = PATHS.db_root
    if source_type == "base_file":
        fp = PATHS.base_root / file
        if not fp.is_file():
            raise HTTPException(404, f"DB root file not found: {file}")
        df = read_one_file(fp)
        if df is None:
            raise HTTPException(400, f"Cannot read DB root file: {file}")
        return normalize_source_df(df, root=root, file=file)
    if source_type == "root_parquet" or (file and not product):
        fp = DB_BASE / file
        if not fp.is_file():
            raise HTTPException(404, f"File not found: {file}")
        df = read_one_file(fp)
        if df is None:
            raise HTTPException(400, f"Cannot read file: {file}")
        return normalize_source_df(df, root=root, file=file)

    prod_path = DB_BASE / root / product
    data_files: list
    if prod_path.is_dir():
        # Legacy layout: <root>/<product>/*.{parquet,csv}
        data_files = _glob_data_files(prod_path)
    else:
        # Hive-partitioned layout: <root>/<table>/product=<P>/*.{parquet,csv}.
        # Collect every partition directory that matches `product=<product>`
        # across all tables hosted by this root.  This lets `/products`
        # surface the actual product names while keeping one logical view
        # per product (multi-table union if >1 table lives under the root).
        root_path = DB_BASE / root
        if not root_path.is_dir():
            raise HTTPException(404, f"Not found: {root}")
        data_files = []
        for hive_dir in sorted(root_path.rglob(f"product={product}")):
            if hive_dir.is_dir():
                data_files.extend(_glob_data_files(hive_dir))
        data_files = sorted(data_files)
    if not data_files:
        raise HTTPException(404, f"No data files in {root}/{product}")

    selected_files = data_files if max_files is None or max_files <= 0 else data_files[-max_files:]
    dfs = []
    for f in selected_files:
        d = read_one_file(f)
        if d is not None and d.height > 0:
            dfs.append(cast_all_str(d))

    if not dfs:
        raise HTTPException(404, f"No readable files with data (tried {len(selected_files)})")

    if len(dfs) == 1:
        return normalize_source_df(filter_valid_wafer_ids_df(dfs[0]), root=root, file=file)

    # Unify columns across partitions
    all_cols, seen = [], set()
    for d in dfs:
        for c in d.columns:
            if c not in seen:
                all_cols.append(c)
                seen.add(c)
    unified = []
    for d in dfs:
        missing = [c for c in all_cols if c not in d.columns]
        if missing:
            d = d.with_columns([pl.lit(None).cast(_STR).alias(c) for c in missing])
        unified.append(d.select(all_cols))
    return normalize_source_df(filter_valid_wafer_ids_df(pl.concat(unified, how="vertical")), root=root, file=file)


# ──────────────────────────────────────────────────────────────────────────
# SQL / filter helpers
# ──────────────────────────────────────────────────────────────────────────
def apply_sql_like(df, sql_str: str):
    """Filter df with pseudo-SQL.

    v8.8.3 fix: LIKE / NOT LIKE 는 pl.sql_expr 에 그대로 전달 (네이티브 지원).
    이전 버전은 LIKE → .str.contains() 로 변환 후 sql_expr 에 넘겼으나,
    pl.sql_expr 은 Python Polars 점(.) 메서드 문법을 해석하지 못해 빈 결과 또는
    SQLInterfaceError 가 발생했음.

    지원 구문:
      - SQL 네이티브 (pl.sql_expr): ==, !=, >, <, >=, <=, AND(&), OR(|),
        LIKE, NOT LIKE, IN (...), IS NULL, IS NOT NULL, BETWEEN
      - Polars 점-메서드 fallback (eval): col.is_in([...]), col.is_not_null(),
        col.is_null(), col.str.contains(...) — sql_expr 실패 시 시도.
    """
    s = (sql_str or "").strip()
    if not s:
        return df
    # 1차: pl.sql_expr — LIKE/NOT LIKE/IN/IS NULL 등 SQL 구문 직접 처리.
    try:
        return df.filter(pl.sql_expr(s))
    except Exception as sql_err:
        pass
    # 2차 fallback: Polars 점-메서드 표현식 (is_in, is_not_null 등).
    # 안전한 네임스페이스에 컬럼명 → pl.col(name) 매핑만 노출.
    try:
        ns = {c: pl.col(c) for c in df.columns}
        expr = eval(s, {"__builtins__": {}, "pl": pl}, ns)  # noqa: S307
        return df.filter(expr)
    except Exception as eval_err:
        raise HTTPException(400, f"SQL error: {sql_err} | expr error: {eval_err}")


def apply_time_window(df, time_col: str, days):
    """Filter df to rows within the last `days` days based on `time_col` (ISO string)."""
    if not time_col or time_col not in df.columns or not days:
        return df
    try:
        d_int = int(days)
        cutoff = (datetime.datetime.now() - datetime.timedelta(days=d_int)).isoformat()
        return df.filter(pl.col(time_col).cast(_STR, strict=False) >= cutoff)
    except Exception:
        return df


# ──────────────────────────────────────────────────────────────────────────
# Schema detection for domain cols (lot / wafer)
# ──────────────────────────────────────────────────────────────────────────
def find_lot_wafer_cols(schema_keys):
    """Auto-detect (lot_col, wafer_col) from a schema. Falls back to schema_keys[0] for lot."""
    lot_col = wf_col = None
    keys = list(schema_keys)
    for n in keys:
        nl = n.lower()
        if not lot_col and "root" in nl and "lot" in nl:
            lot_col = n
        elif not lot_col and "lot" in nl and "id" in nl:
            lot_col = n
        if not wf_col and "wafer" in nl and "id" in nl:
            wf_col = n
    if not lot_col and keys:
        lot_col = keys[0]
    return lot_col, wf_col


# ──────────────────────────────────────────────────────────────────────────
# Row / value serialization
# ──────────────────────────────────────────────────────────────────────────
def serialize_rows(data):
    """Make rows JSON-safe: convert non-primitive values to str."""
    for row in data:
        for k, v in row.items():
            if v is not None and not isinstance(v, (str, int, float, bool)):
                try:
                    row[k] = str(v)
                except Exception:
                    row[k] = repr(v)
    return data


# ──────────────────────────────────────────────────────────────────────────
# Filename / id safety
# ──────────────────────────────────────────────────────────────────────────
def safe_id(s: str, max_len: int = 50) -> str:
    """Sanitize a string so it's safe as a filename or id fragment."""
    return "".join(c for c in (s or "") if c.isalnum() or c in "_- ")[:max_len]


def safe_filename(s: str) -> str:
    """Sanitize for CSV/file downloads."""
    return re.sub(r'[^\w\-.]', '_', s or "download")


# ──────────────────────────────────────────────────────────────────────────
# JSON / JSONL persistence
# ──────────────────────────────────────────────────────────────────────────
def load_json(path: Path, default=None):
    """Read JSON file with default fallback."""
    if path.exists():
        try:
            return json.loads(path.read_text("utf-8"))
        except Exception:
            pass
    return default if default is not None else {}


def _json_default(o):
    """v8.8.2: datetime/Decimal/UUID/Path/set 등 비-JSON 타입을 안전하게 문자열로.
    polars import 결과 rows 에 섞여 들어오는 타입들을 커버."""
    try:
        import datetime as _dt
        if isinstance(o, (_dt.datetime, _dt.date, _dt.time)):
            return o.isoformat()
    except Exception:
        pass
    try:
        from decimal import Decimal as _D
        if isinstance(o, _D):
            return str(o)
    except Exception:
        pass
    try:
        import uuid as _uuid
        if isinstance(o, _uuid.UUID):
            return str(o)
    except Exception:
        pass
    if isinstance(o, (bytes, bytearray)):
        try:
            return o.decode("utf-8", errors="replace")
        except Exception:
            return o.hex()
    if isinstance(o, set):
        return list(o)
    # Path, numpy scalars, polars types, etc.
    return str(o)


def save_json(path: Path, data, indent: int = None):
    """Write JSON file (utf-8, ensure_ascii=False).
    v8.8.2: datetime/Decimal 등 비-JSON 타입도 자동 string 변환 (TableMap import 500 방지)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=indent, default=_json_default),
        "utf-8",
    )


def jsonl_append(path: Path, entry: dict, add_timestamp: bool = True):
    """Append one JSON entry as a line."""
    if add_timestamp and "timestamp" not in entry:
        entry = {**entry, "timestamp": datetime.datetime.now().isoformat()}
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def jsonl_read(path: Path, limit: int = 200, filter_fn=None):
    """Read JSONL, return last `limit` entries that pass `filter_fn`."""
    logs = []
    if not path.exists():
        return logs
    for line in path.read_text("utf-8").strip().split("\n"):
        if not line:
            continue
        try:
            e = json.loads(line)
            if filter_fn and not filter_fn(e):
                continue
            logs.append(e)
        except Exception:
            pass
    return logs[-limit:] if limit > 0 else logs


def jsonl_trim(path: Path, max_lines: int):
    """Keep only last max_lines."""
    if not path.exists():
        return
    lines = path.read_text("utf-8").strip().split("\n")
    if len(lines) > max_lines:
        path.write_text("\n".join(lines[-max_lines:]) + "\n", "utf-8")


# ──────────────────────────────────────────────────────────────────────────
# CSV streaming response helper
# ──────────────────────────────────────────────────────────────────────────
def csv_response(csv_bytes: bytes, filename: str):
    """Build a StreamingResponse for CSV download. v8.4.4: ensures UTF-8 BOM
    prefix so Excel (Korean Windows) opens the file with correct encoding and
    한글/emoji 가 깨지지 않음. 이미 BOM 이 있으면 중복 prefix 하지 않음.
    """
    from fastapi.responses import StreamingResponse
    fname = safe_filename(filename)
    if not fname.endswith(".csv"):
        fname += ".csv"
    BOM = b"\xef\xbb\xbf"
    if not csv_bytes.startswith(BOM):
        csv_bytes = BOM + csv_bytes
    return StreamingResponse(
        iter([csv_bytes]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


def csv_writer_bytes(header, rows):
    """Build CSV bytes from header + row iterable."""
    buf = io.StringIO()
    w = csv_mod.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode("utf-8")


# ──────────────────────────────────────────────────────────────────────────
# DB source enumeration (Dashboard, Table Map both need this)
# ──────────────────────────────────────────────────────────────────────────
def find_all_sources(apply_whitelist: bool = True):
    """Return [{source_type, root, product, file, label}, ...] for every reachable source.

    v7.1: When apply_whitelist=True (default), restrict to the canonical 8 DBs defined
    in core.domain.VISIBLE_CANONICAL. Physical folder names are mapped via
    PHYSICAL_TO_CANONICAL (e.g. CP → YLD). Set apply_whitelist=False to bypass (e.g.
    for admin table-map which needs to see everything).
    """
    DB_BASE = PATHS.db_root
    BASE = PATHS.base_root
    sources = []
    if not DB_BASE.exists():
        return sources
    # v8.8.5: 사내 실환경 = base_root == db_root. 동일 경로면 루트 레벨 파일을 한 번만 스캔.
    same_root = False
    try:
        same_root = (BASE.exists() and DB_BASE.resolve() == BASE.resolve())
    except Exception:
        same_root = False
    # Lazy import to avoid cycle
    try:
        from core.domain import is_visible_root, is_visible_file, canonical_name, DB_REGISTRY
    except Exception:
        is_visible_root = lambda n: True
        is_visible_file = lambda n: True
        canonical_name = lambda n: n
        DB_REGISTRY = {}
    # Root-level files (parquet + CSV) — base/db 공통.
    # v8.8.5: same_root 면 `source_type=base_file` 하나로 통일 (파일탐색기 표시 일관).
    for f in sorted(DB_BASE.iterdir()):
        if f.is_file() and f.suffix in DATA_EXTENSIONS:
            if apply_whitelist and not is_visible_file(f.name):
                continue
            if f.name.startswith("_") or f.name.startswith("."):
                continue
            if same_root:
                sources.append({
                    "source_type": "base_file", "root": "", "product": "",
                    "file": f.name, "canonical": "BASE", "level": "base",
                    "label": f.name,
                })
            else:
                sources.append({
                    "source_type": "root_parquet", "root": "", "product": "",
                    "file": f.name, "label": f.name,
                })
    # Base root files — base 가 db 와 다른 경우에만 별도 스캔.
    if not same_root:
        try:
            if BASE.exists():
                for f in sorted(BASE.iterdir()):
                    if f.is_file() and f.suffix in DATA_EXTENSIONS:
                        if f.name.startswith("_") or f.name.startswith("."):
                            continue
                        sources.append({
                            "source_type": "base_file", "root": "", "product": "",
                            "file": f.name, "canonical": "BASE", "level": "base",
                            "label": f"Base/{f.name}",
                        })
        except Exception:
            pass
    # Nested product directories — v8.8.5: `1.RAWDATA_DB*` prefix 도 whitelist 우회.
    for root_dir in sorted(DB_BASE.iterdir()):
        if not root_dir.is_dir():
            continue
        is_rawdata = root_dir.name.startswith("1.RAWDATA_DB")
        if apply_whitelist and not is_rawdata and not is_visible_root(root_dir.name):
            continue
        for product_name, _prod_dir, st in iter_source_product_dirs(root_dir):
            canon = canonical_name(root_dir.name) if not is_rawdata else root_dir.name
            level = DB_REGISTRY.get(canon, {}).get("level", "")
            lvl_suffix = f" [{level}]" if level and level != "wide" else ""
            sources.append({
                "source_type": st, "root": root_dir.name, "product": product_name,
                "file": "", "canonical": canon, "level": level,
                "label": f"{canon}/{product_name}{lvl_suffix}",
            })
    # dedup — (source_type, root, product, file, label) 완전 일치만 제거.
    # v8.8.5: 추가로 (file, same_root) 기반 strict dedup — ML_TABLE_*.parquet 같은 게 base_file + root_parquet 로 동시 들어오던 문제 원천 차단.
    seen = set()
    dedup = []
    seen_files = set()   # file-only dedup
    for s in sources:
        key = (s.get("source_type"), s.get("root") or "", s.get("product") or "", s.get("file") or "", s.get("label") or "")
        if key in seen:
            continue
        fn = s.get("file") or ""
        # 파일명만 있고 root/product 없는 루트 단일 파일은 중복 안 되도록 file-only 로 체크.
        if fn and not s.get("root") and not s.get("product"):
            if fn in seen_files:
                continue
            seen_files.add(fn)
        seen.add(key)
        dedup.append(s)
    return dedup
