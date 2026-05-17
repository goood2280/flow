#!/usr/bin/env python3
"""Live evaluation for FileBrowser natural-language SQL drafting.

This script does not query DB files. It only calls the configured LLM through
the same backend helper used by /api/filebrowser/sql/llm/draft, then validates
the returned read-only filter expression.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import polars as pl


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
for path in (str(ROOT), str(BACKEND)):
    if path not in sys.path:
        sys.path.insert(0, path)

from core import llm_adapter  # noqa: E402
from routers import filebrowser  # noqa: E402


COLUMN_SETS = {
    "fab": ["product", "lot_id", "root_lot_id", "wafer_id", "step_id", "function_step", "ppid", "tkout_time", "update_time"],
    "ml": ["product", "root_lot_id", "lot_id", "wafer_id", "feature_name", "knob_name", "knob_value", "value", "rank", "category"],
    "et": ["product", "lot_id", "wafer_id", "item_id", "item_desc", "value", "lsl", "usl", "measure_time", "step_id"],
    "inline": ["product", "lot_id", "wafer_id", "item_id", "subitem_id", "value", "shot_x", "shot_y", "measure_time"],
}


CASES = [
    {"kind": "fab", "prompt": "wafer 21만 보여줘", "sql": [r"wafer_id|CAST\(wafer_id AS BIGINT\)", r"21"], "selected": []},
    {"kind": "fab", "prompt": "lot_id, wafer_id, step_id만 보고 wafer 21 필터", "sql": [r"wafer_id|CAST\(wafer_id AS BIGINT\)", r"21"], "selected": ["lot_id", "wafer_id", "step_id"]},
    {"kind": "fab", "prompt": "루트랏 A1000이고 공정 step ETCH인 행", "sql": [r"root_lot_id", r"A1000", r"step_id", r"ETCH"], "selected": []},
    {"kind": "fab", "prompt": "2024년 4월 20일 이후 tkout_time만", "sql": [r"tkout_time", r">=", r"'2024-04-20'"], "selected": ["tkout_time"]},
    {"kind": "fab", "prompt": "step에 ETCH가 포함된 것", "sql": [r"step_id", r"LIKE", r"%ETCH%"], "selected": []},
    {"kind": "fab", "prompt": "root_lot_id가 A1000이고 step_id는 ETCH인 행", "sql": [r"root_lot_id\s*=\s*'A1000'", r"step_id\s*=\s*'ETCH'"], "selected": []},
    {"kind": "fab", "prompt": "ghost_col도 보여주고 wafer 21 필터", "sql": [r"wafer_id|CAST\(wafer_id AS BIGINT\)", r"21"], "selected": [], "warning": "ghost_col"},
    {"kind": "et", "prompt": "IOFF value 큰순서", "sql": [r"item_id\s*=\s*'IOFF'"], "selected": [], "sort": {"column": "value", "direction": "desc", "nulls": "last"}},
    {"kind": "et", "prompt": "IOFF value가 0.15보다 큰거", "sql": [r"item_id\s*=\s*'IOFF'", r"value\s*>\s*0\.15"], "selected": []},
]


SAMPLE_ROWS = {
    "fab": [
        {"product": "PRODA", "lot_id": "A1000.1", "root_lot_id": "A1000", "wafer_id": 21, "step_id": "ETCH", "function_step": "ETCH_MAIN", "ppid": "PPID_24_1", "tkout_time": "2024-04-21", "update_time": "2024-04-21T09:00:00"},
        {"product": "PRODA", "lot_id": "A1000.2", "root_lot_id": "A1000", "wafer_id": 7, "step_id": "CMP", "function_step": "CMP_MAIN", "ppid": "PPID_24_2", "tkout_time": "2024-04-19", "update_time": ""},
    ],
    "ml": [
        {"product": "PRODA", "root_lot_id": "A1001", "lot_id": "A1001.1", "wafer_id": 7, "feature_name": "24 SORT", "knob_name": "PPID", "knob_value": "ON", "value": 12.3, "rank": 3, "category": "PPID_05_1"},
    ],
    "et": [
        {"product": "PRODA", "lot_id": "A1000.1", "wafer_id": 21, "item_id": "IOFF", "item_desc": "leakage", "value": 0.2, "lsl": 0.1, "usl": 1.0, "measure_time": "2026-04-02", "step_id": "SORT"},
        {"product": "PRODA", "lot_id": "A1000.2", "wafer_id": 22, "item_id": "IOFF", "item_desc": "leakage", "value": 0.1, "lsl": 0.1, "usl": 1.0, "measure_time": "2026-04-02", "step_id": "SORT"},
    ],
    "inline": [
        {"product": "PRODA", "lot_id": "A1002.1", "wafer_id": 12, "item_id": "WIDTH", "subitem_id": 3, "value": 50.0, "shot_x": 10, "shot_y": -4, "measure_time": "2026-04-02"},
    ],
}


def classify(result: dict) -> str:
    if result.get("ok") and result.get("sql"):
        return "valid"
    warning = " ".join(str(x) for x in result.get("warnings") or []).lower()
    if any(token in warning for token in ("select", "drop", "statement", "semicolon", "ddl", "dml")):
        return "unsafe_statement"
    if "unknown column" in warning:
        return "wrong_column"
    if "parse" in warning or "sql error" in warning or "expression" in warning:
        return "parse_failed"
    return "invalid"


def matches_sql(sql: str, patterns: list[str]) -> bool:
    return all(re.search(pattern, sql, flags=re.I) for pattern in patterns)


def view_ok(kind: str, sql: str, selected: list[str], sort: dict | None = None) -> bool:
    if not sql and not selected:
        return True
    df = pl.DataFrame(SAMPLE_ROWS[kind])
    try:
        filebrowser._run_view(df, sql=sql, select_cols=",".join(selected), rows=20, sort_spec=sort or {})
        return True
    except Exception:
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Call the configured LLM.")
    parser.add_argument("--require-llm", action="store_true", help="Fail if any case falls back instead of using the configured LLM.")
    parser.add_argument("--cases", type=int, default=len(CASES))
    args = parser.parse_args()
    if not args.live:
        print("Use --live to call the configured LLM.")
        return 2
    print(json.dumps({
        "llm_available": llm_adapter.is_available(),
        "provider": llm_adapter.get_config(redact=True).get("provider"),
        "model": llm_adapter.get_config(redact=True).get("model"),
        "cases": min(args.cases, len(CASES)),
    }, ensure_ascii=False))
    counts: dict[str, int] = {}
    rows = []
    for idx, case in enumerate(CASES[: max(1, min(args.cases, len(CASES)))], start=1):
        kind = case["kind"]
        prompt = case["prompt"]
        result = filebrowser._draft_filebrowser_ai_sql(
            natural_language=prompt,
            columns=COLUMN_SETS[kind],
            sample_rows=SAMPLE_ROWS.get(kind, []),
            scope=kind,
        )
        selected = result.get("selected_columns") or []
        sort = result.get("sort") or {}
        warnings = result.get("warnings") or []
        status = classify(result)
        sql_match = matches_sql(result.get("sql") or "", case.get("sql") or [])
        selected_match = selected == case.get("selected", [])
        sort_match = True
        if case.get("sort"):
            sort_match = sort == case.get("sort")
        warning_match = True
        if case.get("warning"):
            warning_match = case["warning"].casefold() in " ".join(str(w) for w in warnings).casefold()
        executed = view_ok(kind, result.get("sql") or "", selected, sort)
        if status == "valid" and not (sql_match and selected_match and sort_match and warning_match and executed):
            status = "mismatch"
        counts[status] = counts.get(status, 0) + 1
        row = {
            "idx": idx,
            "kind": kind,
            "status": status,
            "llm_used": bool((result.get("llm") or {}).get("used")),
            "fallback": bool(result.get("fallback")),
            "prompt": prompt,
            "sql": result.get("sql") or "",
            "selected_columns": selected,
            "sort": sort,
            "sql_match": sql_match,
            "selected_match": selected_match,
            "sort_match": sort_match,
            "warning_match": warning_match,
            "view_ok": executed,
            "warnings": warnings[:2],
        }
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))
    total = len(rows)
    valid = counts.get("valid", 0)
    summary = {
        "total": total,
        "valid": valid,
        "valid_rate": round(valid / total, 3) if total else 0,
        "llm_used": sum(1 for row in rows if row.get("llm_used")),
        "fallback_used": sum(1 for row in rows if row.get("fallback")),
        "counts": counts,
    }
    print("SUMMARY " + json.dumps(summary, ensure_ascii=False))
    if valid != total or counts.get("unsafe_statement", 0) != 0:
        return 1
    if args.require_llm and summary["llm_used"] != total:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
