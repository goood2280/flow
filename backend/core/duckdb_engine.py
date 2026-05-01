"""Optional DuckDB read/query helpers for large Flow DB sources.

DuckDB is used as an in-memory query engine over parquet/csv files. The helper
never opens a writable DuckDB database file and never writes back to source DB
files; callers receive Polars DataFrames for the existing response pipeline.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import polars as pl

MAX_WAFER_ID = 25
WAFER_COLUMN_CANDIDATES = ("wafer_id", "wf_id")


_FORBIDDEN_FILTER_RE = re.compile(
    r";|--|/\*|\*/|\b("
    r"ATTACH|CALL|COPY|CREATE|DELETE|DETACH|DROP|EXPORT|IMPORT|INSERT|INSTALL|"
    r"LOAD|PRAGMA|SET|UPDATE|VACUUM"
    r")\b",
    re.I,
)


def _duckdb_module():
    try:
        import duckdb  # type: ignore
        return duckdb
    except Exception:
        return None


def is_available() -> bool:
    return _duckdb_module() is not None


def min_auto_bytes() -> int:
    raw = os.environ.get("FLOW_DUCKDB_MIN_BYTES", "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except Exception:
            pass
    return 256 * 1024 * 1024


def _thread_count() -> int:
    raw = os.environ.get("FLOW_DUCKDB_THREADS", "").strip()
    if raw:
        try:
            return max(1, min(16, int(raw)))
        except Exception:
            pass
    try:
        return max(1, min(4, (os.cpu_count() or 2) - 1))
    except Exception:
        return 2


def total_size(files: list[Path]) -> int:
    size = 0
    for fp in files or []:
        try:
            size += fp.stat().st_size
        except Exception:
            pass
    return size


def should_use_duckdb(
    files: list[Path],
    *,
    engine: str = "auto",
    sql: str = "",
    select_cols: str = "",
    threshold_bytes: int | None = None,
) -> bool:
    mode = str(engine or "auto").strip().lower()
    if mode in {"polars", "off", "false", "0"}:
        return False
    if not files or not is_available():
        return False
    if mode in {"duckdb", "on", "true", "1"}:
        return True
    if mode != "auto":
        return False
    threshold = min_auto_bytes() if threshold_bytes is None else max(0, int(threshold_bytes))
    if total_size(files) >= threshold:
        return True
    return len(files) > 1 and bool((sql or "").strip() or (select_cols or "").strip())


def quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def normalize_filter_expr(expr: str) -> str:
    """Translate the lightweight Polars-style filter syntax Flow exposes.

    Only a WHERE expression is accepted. Full SQL statements, DDL/DML, comments,
    and semicolon chaining are rejected before the string reaches DuckDB.
    """
    s = str(expr or "").strip()
    if not s:
        return ""
    if _FORBIDDEN_FILTER_RE.search(s):
        raise ValueError("DuckDB filter accepts a single read-only WHERE expression")
    s = re.sub(r"(?<![<>=!])==(?![=])", "=", s)
    s = re.sub(r"\s*&\s*", " AND ", s)
    s = re.sub(r"\s*\|\s*", " OR ", s)
    return s


def _connect():
    duckdb = _duckdb_module()
    if duckdb is None:
        raise RuntimeError("duckdb is not installed")
    con = duckdb.connect(database=":memory:")
    threads = _thread_count()
    try:
        con.execute(f"SET threads={threads}")
    except Exception:
        pass
    try:
        con.execute("SET preserve_insertion_order=false")
    except Exception:
        pass
    return con


def _source_kind(files: list[Path]) -> str:
    exts = {fp.suffix.lower() for fp in files or []}
    if not exts:
        raise ValueError("no files")
    if exts <= {".parquet"}:
        return "parquet"
    if exts <= {".csv"}:
        return "csv"
    raise ValueError("DuckDB source must not mix parquet and csv files")


def _file_args(files: list[Path]) -> list[str]:
    return [str(Path(fp).resolve()) for fp in files]


def _file_list_sql(files: list[Path]) -> str:
    paths = _file_args(files)
    return "[" + ", ".join(sql_literal(p) for p in paths) + "]"


def wafer_column(columns: list[str] | tuple[str, ...] | None) -> str | None:
    by_lower = {str(c).lower(): str(c) for c in (columns or [])}
    for name in WAFER_COLUMN_CANDIDATES:
        hit = by_lower.get(name)
        if hit:
            return hit
    return None


def valid_wafer_where(columns: list[str] | tuple[str, ...] | None) -> str:
    col = wafer_column(columns)
    if not col:
        return ""
    num = _wafer_number_sql(col)
    return f"({num} >= 1 AND {num} = FLOOR({num}))"


def _wafer_number_sql(col: str) -> str:
    raw = f"UPPER(TRIM(CAST({quote_ident(col)} AS VARCHAR)))"
    core = raw
    for pattern in ("^#\\s*", "^WAFER\\s*", "^WF\\s*", "^W\\s*"):
        core = f"REGEXP_REPLACE({core}, '{pattern}', '')"
    return f"TRY_CAST({core} AS DOUBLE)"


def physical_wafer_sql(col: str) -> str:
    num = _wafer_number_sql(col)
    return f"CAST((((CAST({num} AS BIGINT) - 1) % {MAX_WAFER_ID}) + 1) AS VARCHAR)"


def _select_with_normalized_wafer(columns: list[str]) -> str:
    wafer_col = wafer_column(columns)
    parts = []
    for col in columns:
        if wafer_col and col == wafer_col:
            parts.append(f"{physical_wafer_sql(col)} AS {quote_ident(col)}")
        else:
            parts.append(quote_ident(col))
    return ", ".join(parts) if parts else "*"


def _raw_view_name(view_name: str) -> str:
    return "__flow_raw_" + re.sub(r"[^A-Za-z0-9_]", "_", str(view_name or "source"))


def _register_view(con, view_name: str, files: list[Path]) -> None:
    kind = _source_kind(files)
    paths_sql = _file_list_sql(files)
    view = quote_ident(view_name)
    raw_view_name = _raw_view_name(view_name)
    raw_view = quote_ident(raw_view_name)
    if kind == "parquet":
        con.execute(
            f"CREATE TEMP VIEW {raw_view} AS "
            f"SELECT * FROM read_parquet({paths_sql}, union_by_name=true, hive_partitioning=true)"
        )
    else:
        con.execute(
            f"CREATE TEMP VIEW {raw_view} AS SELECT * FROM read_csv_auto({paths_sql}, union_by_name=true)",
        )
    columns, _schema = _schema_for_view(con, raw_view_name)
    wafer_where = valid_wafer_where(columns)
    select_sql = _select_with_normalized_wafer(columns)
    if wafer_where:
        con.execute(f"CREATE TEMP VIEW {view} AS SELECT {select_sql} FROM {raw_view} WHERE {wafer_where}")
    else:
        con.execute(f"CREATE TEMP VIEW {view} AS SELECT * FROM {raw_view}")


def _fetch_polars(cursor) -> pl.DataFrame:
    try:
        return cursor.pl()
    except Exception:
        return pl.from_arrow(cursor.fetch_arrow_table())


def _schema_for_view(con, view_name: str) -> tuple[list[str], dict[str, str]]:
    cursor = con.execute(f"SELECT * FROM {quote_ident(view_name)} LIMIT 0")
    desc = cursor.description or []
    names = [str(item[0]) for item in desc]
    schema = {str(item[0]): str(item[1]) for item in desc}
    return names, schema


def query_files(
    files: list[Path],
    *,
    where: str = "",
    select_cols: list[str] | None = None,
    limit: int = 200,
    offset: int = 0,
    order_by: str = "",
    descending: bool = False,
) -> tuple[pl.DataFrame, list[str], dict[str, str]]:
    con = _connect()
    try:
        _register_view(con, "_source", files)
        all_columns, schema = _schema_for_view(con, "_source")
        selected = [c for c in (select_cols or []) if c in all_columns]
        select_sql = ", ".join(quote_ident(c) for c in selected) if selected else "*"
        parts = [f"SELECT {select_sql} FROM {quote_ident('_source')}"]
        if where and where.strip():
            parts.append(f"WHERE ({normalize_filter_expr(where)})")
        if order_by and order_by in all_columns:
            direction = "DESC" if descending else "ASC"
            parts.append(f"ORDER BY {quote_ident(order_by)} {direction} NULLS LAST")
        parts.append(f"LIMIT {max(0, int(limit))}")
        parts.append(f"OFFSET {max(0, int(offset))}")
        df = _fetch_polars(con.execute(" ".join(parts)))
        return df, all_columns, schema
    finally:
        try:
            con.close()
        except Exception:
            pass


def inspect_files(files: list[Path]) -> tuple[list[str], dict[str, str]]:
    con = _connect()
    try:
        _register_view(con, "_source", files)
        return _schema_for_view(con, "_source")
    finally:
        try:
            con.close()
        except Exception:
            pass


def distinct_values(
    files: list[Path],
    column: str,
    *,
    where: str = "",
    limit: int = 200,
) -> list[str]:
    if not column:
        return []
    con = _connect()
    try:
        _register_view(con, "_source", files)
        all_columns, _schema = _schema_for_view(con, "_source")
        if column not in all_columns:
            return []
        parts = [
            f"SELECT DISTINCT CAST({quote_ident(column)} AS VARCHAR) AS value "
            f"FROM {quote_ident('_source')}"
        ]
        not_null = f"CAST({quote_ident(column)} AS VARCHAR) IS NOT NULL"
        if where and where.strip():
            parts.append(f"WHERE ({normalize_filter_expr(where)})")
            parts.append(f"AND {not_null}")
        else:
            parts.append(f"WHERE {not_null}")
        parts.append(f"LIMIT {max(1, int(limit))}")
        df = _fetch_polars(con.execute(" ".join(parts)))
        return [str(v) for v in df.get_column("value").to_list() if str(v or "")]
    finally:
        try:
            con.close()
        except Exception:
            pass


def query_views(source_files: dict[str, list[Path]], sql: str) -> pl.DataFrame:
    con = _connect()
    try:
        for view_name, files in source_files.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", view_name or ""):
                raise ValueError(f"invalid DuckDB view name: {view_name}")
            _register_view(con, view_name, files)
        return _fetch_polars(con.execute(sql))
    finally:
        try:
            con.close()
        except Exception:
            pass
