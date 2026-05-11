#!/usr/bin/env python3
"""Live evaluation for FileBrowser natural-language SQL drafting.

This script does not query DB files. It only calls the configured LLM through
the same backend helper used by /api/filebrowser/sql/llm/draft, then validates
the returned read-only filter expression.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


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
    ("fab", "lot id가 A1000.1인 행"),
    ("fab", "root lot이 A1000이고 wafer 21"),
    ("fab", "step_id가 ETCH인 것만"),
    ("fab", "function step에 CLEAN이 포함된 행"),
    ("fab", "ppid가 PPID_24_1 또는 PPID_24_2"),
    ("fab", "tkout_time이 2026-05-01 이후"),
    ("fab", "update_time이 비어있지 않은 행"),
    ("fab", "product가 PRODA이고 step_id는 CMP가 아닌 행"),
    ("fab", "wafer id가 1보다 크고 25 이하"),
    ("fab", "lot_id에 A1000이 들어가는 행"),
    ("ml", "product가 PRODA인 행"),
    ("ml", "feature name에 SORT가 들어가는 행"),
    ("ml", "knob_name이 PPID_24_1인 행"),
    ("ml", "knob_value가 ON 또는 HIGH"),
    ("ml", "value가 10보다 큰 행"),
    ("ml", "rank가 3 이하"),
    ("ml", "category가 PPID_05_1인 행"),
    ("ml", "root lot이 A1001이고 wafer 7"),
    ("ml", "feature_name이 비어있지 않은 행"),
    ("ml", "product가 PRODA 또는 PRODB"),
    ("et", "item_id가 VTH인 행"),
    ("et", "item_desc에 leakage가 포함된 행"),
    ("et", "value가 lsl보다 작은 행"),
    ("et", "value가 usl보다 큰 행"),
    ("et", "measure_time이 2026-04-01 이후"),
    ("et", "wafer 21이고 step_id가 SORT"),
    ("et", "lot_id가 A1000으로 시작하는 행"),
    ("et", "lsl과 usl이 모두 null이 아닌 행"),
    ("et", "product는 PRODA이고 item_id는 CD"),
    ("et", "value가 0 이상 1 이하"),
    ("inline", "shot_x가 10 이상인 행"),
    ("inline", "shot_y가 -5보다 큰 행"),
    ("inline", "subitem_id가 3인 행"),
    ("inline", "item_id가 THK 또는 CD"),
    ("inline", "measure_time이 비어있지 않은 행"),
    ("inline", "product가 PRODA이고 wafer_id가 12"),
    ("inline", "value가 100보다 작고 0보다 큰 행"),
    ("inline", "lot_id에 A1002가 포함된 행"),
    ("inline", "shot_x와 shot_y가 모두 null이 아닌 행"),
    ("inline", "item_id에 WIDTH가 들어가는 행"),
]


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Call the configured LLM.")
    parser.add_argument("--cases", type=int, default=40)
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
    for idx, (kind, prompt) in enumerate(CASES[: max(1, min(args.cases, len(CASES)))], start=1):
        result = filebrowser._draft_filebrowser_ai_sql(
            natural_language=prompt,
            columns=COLUMN_SETS[kind],
            scope=kind,
        )
        status = classify(result)
        counts[status] = counts.get(status, 0) + 1
        row = {
            "idx": idx,
            "kind": kind,
            "status": status,
            "llm_used": bool((result.get("llm") or {}).get("used")),
            "fallback": bool(result.get("fallback")),
            "prompt": prompt,
            "sql": result.get("sql") or "",
            "warnings": (result.get("warnings") or [])[:2],
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
    return 0 if valid == total and counts.get("unsafe_statement", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
