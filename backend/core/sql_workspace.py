"""core/sql_workspace.py — FileBrowser 멀티 SQL Workspace 엔진.

여러 SQL 셀을 단일 DuckDB 메모리 connection 에서 순차 실행한다.
각 cell 은 named TEMP VIEW 로 등록되거나 (`name` 지정), 또는 final SELECT
(`name=null`) 로 결과 fetch 한다. 마지막 cell 만 결과를 반환한다.

안전장치:
  - 위험 키워드 차단 (DROP/INSERT/DELETE/ATTACH/COPY/PRAGMA/UPDATE/LOAD/...)
  - read_parquet/read_csv_auto 경로 화이트리스트 (db_root/base_root 하위만 허용)
  - View name 영문/숫자/_ 만
  - Row 상한 (default 5000, 호출자 override 가능)
  - Cell 당 single statement (`;` 분리 multi 문장 거부)
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

from core.paths import PATHS

logger = logging.getLogger("flow.sql_workspace")

MAX_ROW_LIMIT = 50000
DEFAULT_ROW_LIMIT = 5000

# 매 cell 마다 한 statement 만 허용 — 끝의 ; 한 개는 허용, 그 이상은 거부.
_MULTI_STMT_RE = re.compile(r";\s*\S")

# 위험 키워드 — CREATE VIEW 는 허용해야 하므로 CREATE 는 명시적으로 빼고,
# 그 외 변경/외부 자원 접근/세션 변경을 모두 거부한다.
_FORBIDDEN_KEYWORDS = (
    "ATTACH", "DETACH", "INSTALL", "LOAD",
    "INSERT", "UPDATE", "DELETE", "MERGE",
    "DROP", "ALTER", "TRUNCATE", "VACUUM",
    "COPY", "EXPORT", "IMPORT",
    "PRAGMA", "SET", "RESET",
    "CALL",
)
_FORBIDDEN_RE = re.compile(
    r"\b(?:" + "|".join(_FORBIDDEN_KEYWORDS) + r")\b",
    re.I,
)

# 주석 제거용 (SQL 안전 검증 전 처리).
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)

# read_parquet/read_csv_auto/read_csv 의 문자열 인자 경로 추출.
_READ_FUNC_RE = re.compile(
    r"\bread_(?:parquet|csv|csv_auto|json|json_auto)\s*\(\s*('[^']+'|\"[^\"]+\"|\[[^\]]*\])",
    re.I,
)

_VIEW_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def _strip_comments(sql: str) -> str:
    s = _BLOCK_COMMENT_RE.sub(" ", sql or "")
    s = _LINE_COMMENT_RE.sub(" ", s)
    return s


def _safe_path(raw: str) -> Path:
    """경로 화이트리스트 검증.

    허용: db_root / base_root / cache_dir / data_root 하위.
    글로벌 패턴 (`*`, `?`) 은 그대로 두되 base 디렉토리는 화이트리스트 내.
    """
    raw = raw.strip().strip("'\"")
    if not raw:
        raise ValueError("빈 경로")
    p = Path(raw)
    try:
        resolved = p.resolve(strict=False)
    except Exception:
        resolved = p
    allowed_roots = [PATHS.db_root, PATHS.base_root, PATHS.data_root, PATHS.cache_dir]
    for root in allowed_roots:
        try:
            root_resolved = Path(root).resolve(strict=False)
        except Exception:
            continue
        try:
            resolved.relative_to(root_resolved)
            return resolved
        except ValueError:
            continue
        # glob 패턴 — base prefix 매치
        s = str(resolved)
        if s.startswith(str(root_resolved)):
            return resolved
    raise ValueError(f"경로가 허용된 root 외부: {raw}")


def _validate_read_paths(sql_clean: str) -> list[str]:
    """SQL 안의 read_*() 경로가 모두 화이트리스트 내인지 확인하고 경로 목록 반환."""
    seen: list[str] = []
    for m in _READ_FUNC_RE.finditer(sql_clean):
        arg = m.group(1).strip()
        if arg.startswith("["):
            # 배열 인자 — 각 문자열 리터럴 추출
            for s in re.findall(r"'([^']+)'|\"([^\"]+)\"", arg):
                token = s[0] or s[1]
                if token:
                    _safe_path(token)
                    seen.append(token)
        else:
            _safe_path(arg)
            seen.append(arg)
    return seen


def _validate_cell_sql(sql: str) -> str:
    if not sql or not sql.strip():
        raise ValueError("빈 SQL")
    cleaned = _strip_comments(sql).strip()
    # 끝 ; 한 개 허용
    trimmed = cleaned.rstrip().rstrip(";")
    if _MULTI_STMT_RE.search(trimmed):
        raise ValueError("한 cell 에 두 개 이상 statement 가 들어갈 수 없습니다.")
    if _FORBIDDEN_RE.search(trimmed):
        m = _FORBIDDEN_RE.search(trimmed)
        raise ValueError(f"허용되지 않는 키워드 사용: {m.group(0).upper()}")
    _validate_read_paths(trimmed)
    return trimmed


def _is_create_view(sql_trim: str) -> bool:
    head = sql_trim.lstrip().upper()
    return head.startswith("CREATE")


def run_workspace(cells: list[dict[str, Any]], *, row_limit: int = DEFAULT_ROW_LIMIT) -> dict[str, Any]:
    """cells 를 순차 실행. 반환:
        {
          "ok": bool,
          "cells": [{name, kind: "view"|"final", rowcount?, columns?, sql_preview, ms}],
          "result": {"columns": [...], "rows": [...], "rowcount": N, "truncated": bool},
          "elapsed_ms": N,
        }
    """
    from core import duckdb_engine

    if not isinstance(cells, list) or not cells:
        raise ValueError("cells 가 비어 있습니다")

    limit = max(1, min(int(row_limit or DEFAULT_ROW_LIMIT), MAX_ROW_LIMIT))

    duckdb = duckdb_engine._duckdb_module()
    if duckdb is None:
        raise RuntimeError("duckdb 가 설치되지 않았습니다")
    if not duckdb_engine.is_available():
        raise RuntimeError("duckdb 미사용")

    t_total = time.perf_counter()
    con = duckdb._duckdb_module() if False else None  # placeholder
    con = duckdb_engine._connect()
    cell_traces: list[dict[str, Any]] = []
    result: dict[str, Any] = {"columns": [], "rows": [], "rowcount": 0, "truncated": False}
    last_idx = len(cells) - 1

    try:
        for i, raw_cell in enumerate(cells):
            if not isinstance(raw_cell, dict):
                raise ValueError(f"cell {i} 가 dict 가 아닙니다")
            name = raw_cell.get("name")
            sql_raw = str(raw_cell.get("sql") or "")
            sql = _validate_cell_sql(sql_raw)
            is_final = (i == last_idx)
            is_view_cell = bool(name) and not is_final

            if is_view_cell:
                if not _VIEW_NAME_RE.match(str(name)):
                    raise ValueError(f"잘못된 view 이름: {name}")
                if _is_create_view(sql):
                    raise ValueError(f"cell {i}: view 셀은 SQL 만 적습니다. CREATE 구문 금지 (이름은 name 필드).")
                t0 = time.perf_counter()
                con.execute(f'CREATE OR REPLACE TEMP VIEW "{name}" AS {sql}')
                meta = con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()
                rowcount = int(meta[0]) if meta else 0
                ms = int((time.perf_counter() - t0) * 1000)
                cell_traces.append({
                    "name": name,
                    "kind": "view",
                    "rowcount": rowcount,
                    "sql_preview": sql[:200],
                    "ms": ms,
                })
            else:
                # final cell — fetch
                if _is_create_view(sql):
                    raise ValueError("마지막 cell 은 SELECT 결과여야 합니다 (CREATE 금지).")
                t0 = time.perf_counter()
                # LIMIT 자동 부여 — 사용자가 이미 LIMIT 쓰면 굳이 wrap 안 함
                limited_sql = sql
                if not re.search(r"\bLIMIT\b", sql, re.I):
                    limited_sql = f"SELECT * FROM ({sql}) AS __ws_final LIMIT {limit + 1}"
                cur = con.execute(limited_sql)
                desc = cur.description or []
                columns = [str(d[0]) for d in desc]
                rows_raw = cur.fetchall()
                truncated = len(rows_raw) > limit
                rows_raw = rows_raw[:limit]
                rows = [
                    {columns[j]: _coerce(rv) for j, rv in enumerate(r)}
                    for r in rows_raw
                ]
                ms = int((time.perf_counter() - t0) * 1000)
                result = {
                    "columns": columns,
                    "rows": rows,
                    "rowcount": len(rows),
                    "truncated": truncated,
                }
                cell_traces.append({
                    "name": name or "final",
                    "kind": "final",
                    "rowcount": len(rows),
                    "columns": columns,
                    "sql_preview": sql[:200],
                    "ms": ms,
                    "truncated": truncated,
                })
        return {
            "ok": True,
            "cells": cell_traces,
            "result": result,
            "elapsed_ms": int((time.perf_counter() - t_total) * 1000),
        }
    finally:
        try:
            con.close()
        except Exception:
            pass


def _coerce(v: Any) -> Any:
    """JSON 직렬화 가능한 타입으로 변환."""
    if v is None:
        return None
    if isinstance(v, (str, int, float, bool)):
        return v
    # date/datetime
    try:
        import datetime as _dt
        if isinstance(v, (_dt.date, _dt.datetime, _dt.time)):
            return v.isoformat()
    except Exception:
        pass
    try:
        return str(v)
    except Exception:
        return None
