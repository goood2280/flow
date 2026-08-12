"""core/sql_workspace.py — FileBrowser 멀티 SQL Workspace 엔진.

여러 SQL 셀을 단일 DuckDB 메모리 connection 에서 순차 실행한다.
각 cell 은 named TEMP VIEW 로 등록되거나 (`name` 지정), 또는 final SELECT
(`name=null`) 로 결과 fetch 한다. 마지막 cell 만 결과를 반환한다.

안전장치:
  - 위험 키워드 차단 (DROP/INSERT/DELETE/ATTACH/COPY/PRAGMA/UPDATE/LOAD/...)
  - 파일 접근 함수 **허용목록** (read_parquet/read_csv[_auto]/read_json[_auto]/
    parquet_scan 만) — read_text·read_blob·glob 등 나머지는 거부
  - 경로처럼 보이는 **모든 문자열 리터럴**을 화이트리스트 검증 (db_root/
    base_root/cache_dir 하위만 허용). `FROM '<경로>'` 직접 조회도 여기서 걸린다
  - View name 영문/숫자/_ 만
  - Row 상한 (default 5000, 호출자 override 가능)
  - Cell 당 single statement (`;` 분리 multi 문장 거부)

경로 검증이 read_*() 인자에만 걸려 있던 때에는 `read_text('<임의파일>')`,
`FROM '<임의경로>'`, `glob('C:/**')` 로 화이트리스트를 통째로 우회해 서버
파일을 읽을 수 있었다. 함수 인자가 아니라 **리터럴 자체**를 검사하는 지금
구조가 그 우회를 막는다 — 새 DuckDB 파일 함수가 생겨도 자동으로 막힌다.
data_root 는 users.csv·sessions/tokens.json 이 있는 앱 내부 저장소라
허용 root 에서 제외한다 (그 하위 cache_dir 만 남긴다).
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

# 파일을 읽을 수 있는 함수는 이 목록만 허용한다 (allowlist).
# read_text/read_blob/glob 처럼 화이트리스트를 우회하던 함수는 여기 없으므로 거부된다.
_ALLOWED_FILE_FUNCS = frozenset({
    "read_parquet", "read_csv", "read_csv_auto",
    "read_json", "read_json_auto", "parquet_scan",
})
# 파일 접근 성격의 함수 이름 패턴 — 허용목록에 없으면 거부.
_FILE_FUNC_RE = re.compile(r"\b(read_\w+|\w*_scan|glob|sniff_csv)\s*\(", re.I)

# SQL 안의 모든 문자열 리터럴.
_STR_LITERAL_RE = re.compile(r"'([^']*)'|\"([^\"]*)\"")

_VIEW_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def _strip_comments(sql: str) -> str:
    s = _BLOCK_COMMENT_RE.sub(" ", sql or "")
    s = _LINE_COMMENT_RE.sub(" ", s)
    return s


def _safe_path(raw: str) -> Path:
    """경로 화이트리스트 검증.

    허용: db_root / base_root / cache_dir 하위.
    **data_root 자체는 제외** — users.csv 와 sessions/tokens.json 이 있는 앱
    내부 저장소라 조회 대상이 아니다 (그 하위 cache_dir 만 허용).
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
    allowed_roots = [PATHS.db_root, PATHS.base_root, PATHS.cache_dir]
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
        # glob 패턴 — base prefix 매치. 구분자 경계까지 봐야 형제 디렉터리
        # (`flow-data` 검사에 `flow-data-backup` 통과)를 막는다.
        s, rs = str(resolved), str(root_resolved).rstrip("/\\")
        if s == rs or s.startswith(rs + "/") or s.startswith(rs + "\\"):
            return resolved
    raise ValueError(f"경로가 허용된 root 외부: {raw}")


def _looks_like_path(text: str) -> bool:
    """파일 경로로 쓰일 수 있는 리터럴인가.

    경로 구분자가 있으면 경로로 본다. LIKE 패턴(`%`)은 파일 참조가 아니므로 제외 —
    `WHERE p LIKE '%/foo/%'` 같은 정상 쿼리를 막지 않기 위함이다.
    """
    if not text or "%" in text:
        return False
    return "/" in text or "\\" in text


def _validate_read_paths(sql_clean: str) -> list[str]:
    """파일 접근 함수와 경로 리터럴을 검증하고 확인된 경로 목록을 반환한다.

    1) 파일을 읽는 함수는 허용목록만 통과 — `read_text`·`glob` 등은 여기서 거부.
    2) 경로처럼 보이는 **모든** 문자열 리터럴을 화이트리스트 검증 — 함수 인자만
       보던 예전 방식이 놓치던 `FROM '<경로>'` 직접 조회까지 여기서 걸린다.
    """
    for m in _FILE_FUNC_RE.finditer(sql_clean):
        name = m.group(1).lower()
        if name not in _ALLOWED_FILE_FUNCS:
            raise ValueError(f"허용되지 않는 파일 접근 함수: {m.group(1)}")
    seen: list[str] = []
    for m in _STR_LITERAL_RE.finditer(sql_clean):
        token = m.group(1) if m.group(1) is not None else m.group(2)
        if _looks_like_path(token):
            _safe_path(token)
            seen.append(token)
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
