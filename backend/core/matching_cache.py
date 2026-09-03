"""DuckDB-backed cache for matching CSV tables.

목표:
- 매칭 CSV(현재 운영 중점: step/knob/inline/vm/mask 계열)를 CSV 파싱 없이
  빠르게 재사용하기 위해 DuckDB 테이블로 캐싱.
- 쓰기 즉시 캐시를 갱신해서 다음 조회는 디스크 파싱을 피함.

사용 방식:
- write(저장) 경로에서 `refresh_matching_csv(path)` 호출.
- read 경로에서 `read_matching_csv(path)` 호출.
"""

from __future__ import annotations

from pathlib import Path
import hashlib
import logging
import re
import threading
from typing import Any

import duckdb
import polars as pl

from core.paths import PATHS

logger = logging.getLogger("flow.matching_cache")

_CACHE_DB = ".matching_cache.duckdb"
_CACHE_META = "__matching_cache_meta"
# RLock 필수 — read_matching_csv 가 _LOCK 을 잡은 채 캐시 미스 시
# refresh_matching_csv(동일 _LOCK 획득)를 호출한다. 일반 Lock 이면 self-deadlock.
_LOCK = threading.RLock()

# 운영에서 실제로 쓰이는 매칭 파일들 + 과거/레거시 혼재 파일
SUPPORTED_MATCHING_FILES = {
    "step_matching.csv",
    "matching_step.csv",
    "knob_ppid.csv",
    "ppid_knob.csv",
    "mask.csv",
    # reticle_id → mask 룰북 + 선택적 제품/공정 메타데이터
    "mask_info.csv",
    "inline_matching.csv",
    "inline_step_match.csv",
    "inline_item_map.csv",
    "inline_subitem_pos.csv",
    "yld_shot_agg.csv",
    "vm_matching.csv",
    "vehicle_matching.csv",
    # Optional legacy aliases requested in recent operator workflows.
    "fab.csv",
    "inline.csv",
    "vm.csv",
}


def _db_path() -> Path:
    return PATHS.base_root / _CACHE_DB


def _sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _ident(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def _to_table_name(name: str) -> str:
    base = str(name or "").strip().lower()
    base = Path(base).name
    base = re.sub(r"[^a-z0-9_]+", "_", Path(base).stem)
    if not base:
        base = "matching"
    if base[0].isdigit():
        base = f"t_{base}"
    return f"matching_{base}"


def is_matching_file(fp: Path) -> bool:
    p = Path(fp)
    return p.suffix.lower() == ".csv" and p.name.lower() in SUPPORTED_MATCHING_FILES


def _open_conn() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(_db_path()))


def _ensure_meta(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_ident(_CACHE_META)} (
            table_name VARCHAR PRIMARY KEY,
            source_path VARCHAR,
            source_mtime_ns BIGINT,
            source_size BIGINT,
            source_rows BIGINT,
            refreshed_at TIMESTAMP
        )
        """
    )


def _meta_record(con: duckdb.DuckDBPyConnection, table_name: str):
    return con.execute(
        f"SELECT source_mtime_ns, source_size FROM {_ident(_CACHE_META)} WHERE table_name=?",
        [table_name],
    ).fetchone()


def _needs_refresh(con: duckdb.DuckDBPyConnection, fp: Path, table_name: str) -> bool:
    st = fp.stat()
    rec = _meta_record(con, table_name)
    if not rec:
        return True
    db_mtime, db_size = rec
    return int(st.st_mtime_ns) != int(db_mtime or -1) or int(st.st_size) != int(db_size or -1)


def _file_hash(fp: Path, size: int) -> str:
    # Keep lightweight: size + mtime 기반 식별자(헤더만 바뀐 경우도 size 변화로 추적됨).
    return hashlib.sha1(f"{fp}:{size}:{fp.stat().st_mtime_ns}".encode("utf-8")).hexdigest()[:12]


def refresh_matching_csv(fp: Path) -> dict[str, Any]:
    """Rebuild one matching table cache entry from CSV.

    Returns:
      - ok: bool
      - rows: int (refresh success)
      - table: table_name
      - file_hash: lightweight fingerprint
    """
    p = Path(fp)
    if not is_matching_file(p):
        return {"ok": True, "skipped": True}
    if not p.exists() or not p.is_file():
        return {"ok": False, "error": f"file not found: {p}"}

    st = p.stat()
    table_name = _to_table_name(p.name)
    db_rows = 0
    try:
        with _LOCK:
            con = _open_conn()
            try:
                _ensure_meta(con)
                path_str = str(p.resolve())
                con.execute(
                    f"CREATE OR REPLACE TABLE {_ident(table_name)} AS\n"
                    f"SELECT * FROM read_csv_auto({_sql_literal(path_str)},\n"
                    f"  HEADER=TRUE, ALL_VARCHAR=TRUE)"
                )
                db_rows = int(con.execute(f"SELECT COUNT(*) FROM {_ident(table_name)}").fetchone()[0])
                con.execute(f"DELETE FROM {_ident(_CACHE_META)} WHERE table_name=?", [table_name])
                con.execute(
                    f"INSERT INTO {_ident(_CACHE_META)} VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                    [table_name, path_str, int(st.st_mtime_ns), int(st.st_size), int(db_rows)],
                )
                return {
                    "ok": True,
                    "table": table_name,
                    "rows": db_rows,
                    "file_hash": _file_hash(p, int(st.st_size)),
                }
            finally:
                con.close()
    except Exception as e:
        logger.warning("matching_cache.refresh failed file=%s: %s", p, e)
        return {"ok": False, "error": str(e), "table": table_name}


def read_matching_csv(fp: Path) -> pl.DataFrame | None:
    """Read matching CSV via DuckDB cache if supported/ready. None on miss/failure."""
    p = Path(fp)
    if not is_matching_file(p) or not p.exists():
        return None
    table_name = _to_table_name(p.name)
    try:
        with _LOCK:
            con = _open_conn()
            try:
                _ensure_meta(con)
                if _needs_refresh(con, p, table_name):
                    # Fallback path: refresh and continue.
                    refresh = refresh_matching_csv(p)
                    if not refresh.get("ok", False):
                        return None
                return con.execute(f"SELECT * FROM {_ident(table_name)}").pl()
            finally:
                con.close()
    except Exception as e:
        logger.warning("matching_cache.read failed file=%s: %s", p, e)
        return None


def dedupe_rows(rows: list[dict], key_cols: list[str], required_cols: list[str] | None = None,
                *, strict_required: bool = False) -> tuple[list[dict], int]:
    """Normalize values, optionally validate required cols, and deduplicate.

    - required column missing/empty with strict_required=True raises ValueError.
    - duplicates are deduplicated by the last row wins.
    """
    cleaned: list[dict] = []
    idx_by_key: dict[tuple[str, ...], int] = {}
    dropped = 0

    key_cols = [str(c or "").strip() for c in (key_cols or []) if str(c or "").strip()]
    required_cols = [str(c or "").strip() for c in (required_cols or []) if str(c or "").strip()]

    for row in rows or []:
        if not isinstance(row, dict):
            continue
        norm = {str(k): ("" if v is None else str(v).strip()) for k, v in row.items()}
        if strict_required and required_cols:
            missing = [c for c in required_cols if not norm.get(c)]
            if missing:
                raise ValueError(f"required columns missing: {', '.join(missing)}")
        if not any(norm.values()):
            continue
        if key_cols:
            key = tuple(norm.get(c, "") for c in key_cols)
            if any(v for v in key):
                pos = idx_by_key.get(key)
                if pos is None:
                    idx_by_key[key] = len(cleaned)
                    cleaned.append(norm)
                else:
                    cleaned[pos] = norm
                    dropped += 1
                continue
        cleaned.append(norm)
    return cleaned, dropped
