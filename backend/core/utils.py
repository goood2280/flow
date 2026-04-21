"""core/utils.py v4.0.0 - shared backend helpers
Extracted common patterns from routers to reduce duplication.
"""
import json, datetime, re, io, csv as csv_mod
from pathlib import Path
from fastapi import HTTPException
import polars as pl
from core.paths import PATHS


# ──────────────────────────────────────────────────────────────────────────
# Polars helpers
# ──────────────────────────────────────────────────────────────────────────
_STR = getattr(pl, "Utf8", None) or getattr(pl, "String", pl.Object)

DATA_EXTENSIONS = {".parquet", ".csv"}


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


def read_one_file(fp: Path):
    """Read single parquet or CSV with cats cast. Returns None on failure."""
    try:
        if fp.suffix == ".csv":
            return cast_cats(pl.read_csv(str(fp), infer_schema_length=5000, try_parse_dates=False))
        return cast_cats(pl.read_parquet(str(fp)))
    except Exception:
        return None


# Keep old name as alias for compatibility
read_one_parquet = read_one_file


def scan_one_file(fp: Path):
    """Lazy-scan a single file. Returns LazyFrame or None."""
    try:
        if fp.suffix == ".csv":
            return pl.scan_csv(str(fp), infer_schema_length=5000, try_parse_dates=False)
        return pl.scan_parquet(str(fp))
    except Exception:
        return None


def lazy_read_source(source_type: str = "", root: str = "", product: str = "",
                     file: str = "", max_files: int = 20):
    """Memory-efficient lazy reader. Returns LazyFrame (call .collect() when ready).
    For 30-50GB datasets on 2-core/6GB machines — push filters before collect.
    """
    DB_BASE = PATHS.db_root
    if source_type == "base_file":
        fp = PATHS.base_root / file
        if not fp.is_file():
            return None
        return scan_one_file(fp)
    if source_type == "root_parquet" or (file and not product):
        fp = DB_BASE / file
        if not fp.is_file():
            return None
        return scan_one_file(fp)

    prod_path = DB_BASE / root / product
    if not prod_path.is_dir():
        return None

    # Try hive partitioning first (most memory-efficient)
    pq_files = sorted(prod_path.rglob("*.parquet"))
    if pq_files:
        try:
            return pl.scan_parquet(
                [str(f) for f in pq_files[-max_files:]],
                hive_partitioning=True,
            )
        except Exception:
            pass
        # Fallback: scan individually and concat
        frames = []
        for f in pq_files[-max_files:]:
            lf = scan_one_file(f)
            if lf is not None:
                frames.append(lf)
        if frames:
            return pl.concat(frames, how="diagonal_relaxed")

    # CSV fallback
    csv_files = sorted(prod_path.rglob("*.csv"))
    if csv_files:
        frames = [scan_one_file(f) for f in csv_files[-max_files:]]
        frames = [f for f in frames if f is not None]
        if frames:
            return pl.concat(frames, how="diagonal_relaxed")

    return None


def _glob_data_files(directory: Path):
    """Glob both *.parquet and *.csv recursively, sorted."""
    files = []
    for ext in DATA_EXTENSIONS:
        files.extend(directory.rglob(f"*{ext}"))
    return sorted(files)


def read_source(source_type: str = "", root: str = "", product: str = "",
                file: str = "", max_files: int = 40):
    """Universal reader covering all DB types:
      - base_file:   BASE_ROOT/<file> (parquet or CSV) — Base 루트의 단일 파일
      - root_parquet: DB_BASE/<file> (parquet or CSV)
      - flat/hive/auto: DB_BASE/<root>/<product>/*.{parquet,csv} (recursive)
    """
    DB_BASE = PATHS.db_root
    if source_type == "base_file":
        fp = PATHS.base_root / file
        if not fp.is_file():
            raise HTTPException(404, f"Base file not found: {file}")
        df = read_one_file(fp)
        if df is None:
            raise HTTPException(400, f"Cannot read Base file: {file}")
        return df
    if source_type == "root_parquet" or (file and not product):
        fp = DB_BASE / file
        if not fp.is_file():
            raise HTTPException(404, f"File not found: {file}")
        df = read_one_file(fp)
        if df is None:
            raise HTTPException(400, f"Cannot read file: {file}")
        return df

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

    dfs = []
    for f in data_files[-max_files:] if max_files > 0 else data_files:
        d = read_one_file(f)
        if d is not None and d.height > 0:
            dfs.append(cast_all_str(d))

    if not dfs:
        raise HTTPException(404, f"No readable files with data (tried {len(data_files[-max_files:])})")

    if len(dfs) == 1:
        return dfs[0]

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
    return pl.concat(unified, how="vertical")


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
        for prod_dir in sorted(root_dir.iterdir()):
            if prod_dir.is_dir() and any(_glob_data_files(prod_dir)):
                st = detect_structure(prod_dir)
                canon = canonical_name(root_dir.name) if not is_rawdata else root_dir.name
                level = DB_REGISTRY.get(canon, {}).get("level", "")
                lvl_suffix = f" [{level}]" if level and level != "wide" else ""
                sources.append({
                    "source_type": st, "root": root_dir.name, "product": prod_dir.name,
                    "file": "", "canonical": canon, "level": level,
                    "label": f"{canon}/{prod_dir.name}{lvl_suffix}",
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
