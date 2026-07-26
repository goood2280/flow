"""tests/test_sql_workspace.py — Step 3 SQL Workspace 엔진 검증.

DuckDB 내장 VALUES 만으로 view chain·안전장치·row 상한을 검증한다 (외부
parquet 의존성 없음).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_FLOW_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _FLOW_ROOT / "backend"
for p in (_BACKEND, _FLOW_ROOT):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)


def _engine_or_skip():
    try:
        from core import sql_workspace
        from core import duckdb_engine
    except Exception as e:
        pytest.skip(f"sql_workspace import 실패: {e}")
        return None
    if not duckdb_engine.is_available():
        pytest.skip("duckdb 미설치")
        return None
    return sql_workspace


def test_two_view_join_returns_expected_rows():
    engine = _engine_or_skip()
    cells = [
        {"name": "lot_meta", "sql": "SELECT * FROM (VALUES ('A1', 'R1'), ('A2', 'R2')) AS t(lot_id, root_lot_id)"},
        {"name": "et_results", "sql": "SELECT * FROM (VALUES ('A1', 10.0), ('A1', 20.0), ('A2', 30.0)) AS t(lot_id, value)"},
        {"name": None, "sql": "SELECT m.root_lot_id, AVG(e.value) AS avg_v FROM lot_meta m JOIN et_results e USING(lot_id) GROUP BY m.root_lot_id ORDER BY 1"},
    ]
    out = engine.run_workspace(cells)
    assert out["ok"] is True
    assert out["result"]["rowcount"] == 2
    assert out["result"]["columns"] == ["root_lot_id", "avg_v"]
    rows = out["result"]["rows"]
    assert rows[0]["root_lot_id"] == "R1"
    assert rows[1]["root_lot_id"] == "R2"
    assert rows[0]["avg_v"] == 15.0
    assert rows[1]["avg_v"] == 30.0
    assert len(out["cells"]) == 3
    assert out["cells"][0]["kind"] == "view"
    assert out["cells"][0]["rowcount"] == 2
    assert out["cells"][2]["kind"] == "final"


def test_forbidden_keyword_rejected():
    engine = _engine_or_skip()
    forbidden = [
        "DROP TABLE foo",
        "INSERT INTO foo VALUES (1)",
        "DELETE FROM foo",
        "ATTACH 'x' AS y",
        "COPY foo TO 'x.csv'",
        "PRAGMA threads=1",
        "UPDATE foo SET a=1",
        "INSTALL httpfs",
    ]
    for sql in forbidden:
        with pytest.raises(ValueError, match="허용되지 않는 키워드"):
            engine._validate_cell_sql(sql)


def test_multi_statement_rejected():
    engine = _engine_or_skip()
    with pytest.raises(ValueError, match="두 개 이상"):
        engine._validate_cell_sql("SELECT 1; SELECT 2")


def test_view_name_invalid_rejected():
    engine = _engine_or_skip()
    with pytest.raises(ValueError, match="잘못된 view"):
        engine.run_workspace([
            {"name": "1starts_digit", "sql": "SELECT 1 AS a"},
            {"name": None, "sql": "SELECT * FROM \"1starts_digit\""},
        ])


def test_final_cell_create_rejected():
    engine = _engine_or_skip()
    with pytest.raises(ValueError, match="SELECT 결과"):
        engine.run_workspace([
            {"name": None, "sql": "CREATE TABLE foo AS SELECT 1 AS a"},
        ])


def test_view_cell_create_rejected():
    engine = _engine_or_skip()
    with pytest.raises(ValueError, match="CREATE 구문 금지"):
        engine.run_workspace([
            {"name": "v", "sql": "CREATE VIEW v AS SELECT 1 AS a"},
            {"name": None, "sql": "SELECT * FROM v"},
        ])


def test_row_limit_truncates_and_flags():
    engine = _engine_or_skip()
    # 1000 행 생성 — limit 100 으로 자르면 truncated=True
    out = engine.run_workspace(
        [
            {"name": None, "sql": "SELECT * FROM range(1000) AS t(n)"},
        ],
        row_limit=100,
    )
    assert out["result"]["rowcount"] == 100
    assert out["result"]["truncated"] is True


def test_row_limit_no_truncate_when_below():
    engine = _engine_or_skip()
    out = engine.run_workspace(
        [
            {"name": None, "sql": "SELECT * FROM range(50) AS t(n)"},
        ],
        row_limit=100,
    )
    assert out["result"]["rowcount"] == 50
    assert out["result"]["truncated"] is False


def test_read_path_outside_root_rejected():
    engine = _engine_or_skip()
    # 정확한 외부 경로 — Windows 도 안전한 패턴
    with pytest.raises(ValueError, match="허용된 root 외부"):
        engine._validate_cell_sql("SELECT * FROM read_parquet('C:/Windows/System32/foo.parquet')")


def test_read_path_inside_root_passes_validation():
    engine = _engine_or_skip()
    from core.paths import PATHS
    p = PATHS.db_root / "_nonexistent.parquet"
    # 화이트리스트 검증만 통과 — 파일 존재 여부는 DuckDB 단계에서 체크.
    sql = f"SELECT * FROM read_parquet('{p}')"
    cleaned = engine._validate_cell_sql(sql)
    assert "read_parquet" in cleaned


def test_empty_cells_rejected():
    engine = _engine_or_skip()
    with pytest.raises(ValueError):
        engine.run_workspace([])


def test_comments_stripped_before_keyword_check():
    engine = _engine_or_skip()
    # 주석 안의 위험 키워드는 무시되어야 함
    sql = "SELECT 1 AS a -- DROP TABLE foo"
    cleaned = engine._validate_cell_sql(sql)
    assert "DROP" not in cleaned.upper()
