#!/usr/bin/env python3
"""Flow-i Agent semantic, knowledge, and multi-DB deep evaluation.

This is intentionally deterministic and self-contained:
- semantic cases call the Agent runtime resolver directly
- knowledge cases upsert one stable Agent Wiki page and verify retrieval
- multi-DB cases use in-memory SQL Workspace views, not production files
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app_v2.modules.agent_runtime.semantic import resolve_semantic_frame  # noqa: E402
from app_v2.shared.contracts import KnowledgeDoc  # noqa: E402
from core import knowledge_vault as kv  # noqa: E402
from core.sql_workspace import run_workspace  # noqa: E402


DOC_ID = "agent_deep_eval_semiconductor_terms"


@dataclass
class CaseResult:
    name: str
    ok: bool
    detail: str = ""


class EvalRunner:
    def __init__(self) -> None:
        self.results: list[CaseResult] = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.results.append(CaseResult(name=name, ok=bool(ok), detail=detail))

    def equal(self, name: str, actual: Any, expected: Any) -> None:
        self.check(name, actual == expected, f"actual={actual!r} expected={expected!r}")

    def contains(self, name: str, values: Any, expected: Any) -> None:
        haystack = values if isinstance(values, (list, set, tuple)) else []
        self.check(name, expected in haystack, f"expected={expected!r} in {list(haystack)!r}")

    def summary(self) -> dict[str, Any]:
        passed = sum(1 for r in self.results if r.ok)
        failed = len(self.results) - passed
        return {"passed": passed, "failed": failed, "total": len(self.results)}

    def print_report(self, *, show_pass: bool = False) -> None:
        for result in self.results:
            if result.ok and not show_pass:
                continue
            mark = "PASS" if result.ok else "FAIL"
            suffix = f" - {result.detail}" if result.detail else ""
            print(f"[{mark}] {result.name}{suffix}")
        summary = self.summary()
        print(f"\nsummary: passed={summary['passed']} failed={summary['failed']} total={summary['total']}")


def _semantic_surface(frame: Any) -> set[str]:
    terms = set((frame.normalized_terms or {}).values())
    for candidate in frame.candidates or []:
        if candidate.normalized:
            terms.add(str(candidate.normalized))
        if candidate.column:
            terms.add(str(candidate.column))
        if candidate.canonical_alias:
            terms.add(str(candidate.canonical_alias))
    return terms


SEMANTIC_CASES: list[dict[str, Any]] = [
    {
        "name": "step_id simple question",
        "prompt": "step_id AA100570 무슨 스텝이야",
        "terms": ["step_id"],
        "slots": {"steps": ["AA100570"]},
    },
    {
        "name": "step_id to function_step",
        "prompt": "AA100570 function step 뭐야",
        "terms": ["step_id", "function_step"],
        "slots": {"steps": ["AA100570"]},
    },
    {
        "name": "korean function step",
        "prompt": "step AA200010의 기능공정 알려줘",
        "terms": ["step_id", "function_step"],
        "slots": {"steps": ["AA200010"]},
    },
    {
        "name": "korean module process",
        "prompt": "공정 AA220000 모듈공정이 뭐야",
        "terms": ["step_id", "function_step"],
        "slots": {"steps": ["AA220000"]},
    },
    {
        "name": "product root wafer step",
        "prompt": "제품 PRODA root lot A1000 wafer #21 현재 step",
        "terms": ["product", "root_lot_id", "wafer_id", "step_id"],
        "slots": {"products": ["PRODA"], "root_lot_ids": ["A1000"], "wafer_ids": [21]},
    },
    {
        "name": "korean root wafer process",
        "prompt": "루트랏 A1000 웨이퍼 7 현재 공정",
        "terms": ["root_lot_id", "wafer_id", "step_id"],
        "slots": {"root_lot_ids": ["A1000"]},
    },
    {
        "name": "fab lot step",
        "prompt": "fab lot A1000.2 step 확인",
        "terms": ["fab_lot_id", "step_id"],
        "slots": {"fab_lot_ids": ["A1000.2"], "root_lot_ids": ["A1000"]},
    },
    {
        "name": "lot id raw data",
        "prompt": "lot_id A1004인 raw data 보여줘",
        "terms": ["fab_lot_id", "filebrowser"],
        "slots": {"root_lot_ids": ["A1004"]},
    },
    {
        "name": "lot_wf underscore",
        "prompt": "lot_wf A1000_W21 raw row",
        "terms": ["lot_wf", "filebrowser"],
        "slots": {"lot_wfs": ["A1000_W21"]},
    },
    {
        "name": "lot wafer korean",
        "prompt": "랏웨이퍼 A1002_W5 기준으로 ET raw",
        "terms": ["lot_wf", "filebrowser"],
        "slots": {"lot_wfs": ["A1002_W5"]},
    },
    {
        "name": "knob lot_wf list",
        "prompt": "Split table에서 특정 KNOB_A 가진 lot_wf 리스트 달라",
        "terms": ["knob", "lot_wf"],
        "slots": {"knobs": ["KNOB_A"]},
        "intent": "knob_analysis",
    },
    {
        "name": "ppid split wafer",
        "prompt": "PPID_04_2 분기 wafer 리스트",
        "terms": ["knob", "wafer_id"],
        "slots": {"knobs": ["PPID_04_2"]},
        "intent": "knob_analysis",
    },
    {
        "name": "numeric sort step knob",
        "prompt": "24.0 SORT knob 별 lot_wf",
        "terms": ["step_id", "knob", "lot_wf"],
        "slots": {"steps": ["24.0 SORT"], "knobs": ["KNOB"]},
        "intent": "knob_analysis",
    },
    {
        "name": "korean split knob",
        "prompt": "스플릿 놉 영향 분석",
        "terms": ["knob"],
        "intent": "knob_analysis",
    },
    {
        "name": "raw DB sql join",
        "prompt": "여러 DB SQL join raw data 달라",
        "terms": ["filebrowser", "ai_sql"],
        "intent": "filebrowser_ai_sql",
    },
    {
        "name": "ai sql filter sort",
        "prompt": "AI SQL 필터 정렬 조건 만들어줘",
        "terms": ["ai_sql"],
        "intent": "filebrowser_ai_sql",
    },
    {
        "name": "chart trend scatter",
        "prompt": "chart trend scatter 분석",
        "terms": ["chart"],
        "intent": "chart_analysis",
    },
    {
        "name": "korean chart trend",
        "prompt": "차트 트렌드 그래프로 보여줘",
        "terms": ["chart"],
        "intent": "chart_analysis",
    },
    {
        "name": "inform mail",
        "prompt": "인폼 메일 초안",
        "terms": ["inform"],
        "intent": "inform_draft",
    },
    {
        "name": "meeting recall",
        "prompt": "meeting 회의록 recall",
        "terms": ["meeting"],
        "intent": "meeting_recall",
    },
    {
        "name": "tracker issue",
        "prompt": "tracker issue 이슈 찾아줘",
        "terms": ["tracker"],
        "intent": "tracker_lookup",
    },
    {
        "name": "semantic wiki schema",
        "prompt": "semantic wiki schema 단어 의미 확인",
        "terms": ["semantic_layer"],
        "intent": "semantic_inspection",
    },
    {
        "name": "langgraph langsmith trace",
        "prompt": "LangGraph trace LangSmith 추적 상태",
        "terms": ["langgraph", "langsmith"],
        "intent": "traceable_orchestration",
    },
    {
        "name": "product code",
        "prompt": "제품명 PRODZ 현재 lot",
        "terms": ["product", "fab_lot_id"],
        "slots": {"products": ["PRODZ"]},
    },
    {
        "name": "wf shorthand",
        "prompt": "A1007 WF12 현재 공정",
        "terms": ["wafer_id", "step_id"],
        "slots": {"root_lot_ids": ["A1007"], "wafer_ids": [12]},
    },
    {
        "name": "rootlot and fab lot",
        "prompt": "rootlot A9999 lot id A9999.3",
        "terms": ["root_lot_id", "fab_lot_id"],
        "slots": {"root_lot_ids": ["A9999"], "fab_lot_ids": ["A9999.3"]},
    },
]


BASE_CELLS: list[dict[str, str | None]] = [
    {
        "name": "fab_db",
        "sql": (
            "SELECT * FROM (VALUES "
            "('PRODA','A1000','A1000.1',21,'A1000_W21','AA100570','2026-05-21T10:00:00'),"
            "('PRODA','A1000','A1000.2',22,'A1000_W22','AA200010','2026-05-21T11:00:00'),"
            "('PRODA','A1001','A1001.1',7,'A1001_W07','AA100570','2026-05-22T09:30:00'),"
            "('PRODB','A1002','A1002.1',5,'A1002_W05','24.0 SORT','2026-05-22T10:30:00'),"
            "('PRODB','A1003','A1003.1',12,'A1003_W12','AA220000','2026-05-22T12:00:00')"
            ") AS t(product, root_lot_id, lot_id, wafer_id, lot_wf, step_id, tkout_time)"
        ),
    },
    {
        "name": "step_map_db",
        "sql": (
            "SELECT * FROM (VALUES "
            "('AA100570','CONTACT'),"
            "('AA200010','PHOTO'),"
            "('AA220000','ETCH'),"
            "('24.0 SORT','SORT')"
            ") AS t(step_id, function_step)"
        ),
    },
    {
        "name": "split_db",
        "sql": (
            "SELECT * FROM (VALUES "
            "('A1000_W21','PPID_A','HIGH'),"
            "('A1000_W22','PPID_B','LOW'),"
            "('A1001_W07','PPID_A','HIGH'),"
            "('A1002_W05','PPID_SORT_A','SORT_A'),"
            "('A1003_W12','KNOB_SAFE','BASE')"
            ") AS t(lot_wf, knob, knob_value)"
        ),
    },
    {
        "name": "et_db",
        "sql": (
            "SELECT * FROM (VALUES "
            "('A1000_W21','AA100570','CD',10.2),"
            "('A1000_W22','AA200010','CD',11.4),"
            "('A1001_W07','AA100570','CD',9.8),"
            "('A1002_W05','24.0 SORT','CD',12.0),"
            "('A1003_W12','AA220000','CD',13.1)"
            ") AS t(lot_wf, step_id, metric, value)"
        ),
    },
    {
        "name": "tracker_db",
        "sql": (
            "SELECT * FROM (VALUES "
            "('A1000_W22','OPEN','PHOTO hold'),"
            "('A1003_W12','CLOSED','ETCH review')"
            ") AS t(lot_wf, issue_status, issue_summary)"
        ),
    },
    {
        "name": "inform_db",
        "sql": (
            "SELECT * FROM (VALUES "
            "('A1000_W21','INF-1','split high'),"
            "('A1002_W05','INF-2','sort split')"
            ") AS t(lot_wf, inform_id, inform_summary)"
        ),
    },
]


SQL_CASES: list[dict[str, Any]] = [
    {
        "name": "step_id AA100570 function_step",
        "sql": "SELECT function_step FROM step_map_db WHERE step_id='AA100570'",
        "expect_rows": [{"function_step": "CONTACT"}],
    },
    {
        "name": "step_id AA200010 function_step",
        "sql": "SELECT function_step FROM step_map_db WHERE step_id='AA200010'",
        "expect_rows": [{"function_step": "PHOTO"}],
    },
    {
        "name": "lot_wf list for PPID_A",
        "sql": "SELECT lot_wf FROM split_db WHERE knob='PPID_A' ORDER BY lot_wf",
        "expect_rows": [{"lot_wf": "A1000_W21"}, {"lot_wf": "A1001_W07"}],
    },
    {
        "name": "lot_wf list for HIGH knob value",
        "sql": "SELECT lot_wf FROM split_db WHERE knob_value='HIGH' ORDER BY lot_wf",
        "expect_rows": [{"lot_wf": "A1000_W21"}, {"lot_wf": "A1001_W07"}],
    },
    {
        "name": "24.0 SORT split lot_wf",
        "sql": (
            "SELECT f.lot_wf, s.knob FROM fab_db f "
            "JOIN split_db s USING(lot_wf) "
            "WHERE f.step_id='24.0 SORT'"
        ),
        "expect_rows": [{"lot_wf": "A1002_W05", "knob": "PPID_SORT_A"}],
    },
    {
        "name": "five-source raw join columns",
        "sql": (
            "SELECT f.product, f.root_lot_id, f.lot_wf, f.step_id, m.function_step, "
            "s.knob, e.metric, e.value, tr.issue_status, i.inform_id "
            "FROM fab_db f "
            "JOIN step_map_db m USING(step_id) "
            "JOIN split_db s USING(lot_wf) "
            "JOIN et_db e USING(lot_wf, step_id) "
            "LEFT JOIN tracker_db tr USING(lot_wf) "
            "LEFT JOIN inform_db i USING(lot_wf) "
            "ORDER BY f.lot_wf"
        ),
        "expect_rowcount": 5,
        "expect_columns": [
            "product",
            "root_lot_id",
            "lot_wf",
            "step_id",
            "function_step",
            "knob",
            "metric",
            "value",
            "issue_status",
            "inform_id",
        ],
    },
    {
        "name": "exact cross-db row A1000_W21",
        "sql": (
            "SELECT f.lot_wf, m.function_step, s.knob, e.value, i.inform_id "
            "FROM fab_db f "
            "JOIN step_map_db m USING(step_id) "
            "JOIN split_db s USING(lot_wf) "
            "JOIN et_db e USING(lot_wf, step_id) "
            "LEFT JOIN inform_db i USING(lot_wf) "
            "WHERE f.lot_wf='A1000_W21'"
        ),
        "expect_rows": [{"lot_wf": "A1000_W21", "function_step": "CONTACT", "knob": "PPID_A", "value": 10.2, "inform_id": "INF-1"}],
    },
    {
        "name": "root_lot CD average",
        "sql": (
            "SELECT f.root_lot_id, ROUND(AVG(e.value), 2) AS avg_cd "
            "FROM fab_db f JOIN et_db e USING(lot_wf, step_id) "
            "GROUP BY f.root_lot_id ORDER BY f.root_lot_id"
        ),
        "expect_rows": [
            {"root_lot_id": "A1000", "avg_cd": 10.8},
            {"root_lot_id": "A1001", "avg_cd": 9.8},
            {"root_lot_id": "A1002", "avg_cd": 12.0},
            {"root_lot_id": "A1003", "avg_cd": 13.1},
        ],
    },
    {
        "name": "CONTACT wafer list",
        "sql": (
            "SELECT f.root_lot_id, f.wafer_id, f.lot_wf "
            "FROM fab_db f JOIN step_map_db m USING(step_id) "
            "WHERE m.function_step='CONTACT' ORDER BY f.root_lot_id, f.wafer_id"
        ),
        "expect_rows": [
            {"root_lot_id": "A1000", "wafer_id": 21, "lot_wf": "A1000_W21"},
            {"root_lot_id": "A1001", "wafer_id": 7, "lot_wf": "A1001_W07"},
        ],
    },
    {
        "name": "tracker issue left join",
        "sql": (
            "SELECT f.lot_wf, COALESCE(tr.issue_status, 'NONE') AS issue_status "
            "FROM fab_db f LEFT JOIN tracker_db tr USING(lot_wf) "
            "WHERE f.lot_wf IN ('A1000_W21','A1000_W22') ORDER BY f.lot_wf"
        ),
        "expect_rows": [
            {"lot_wf": "A1000_W21", "issue_status": "NONE"},
            {"lot_wf": "A1000_W22", "issue_status": "OPEN"},
        ],
    },
    {
        "name": "inform evidence for sort split",
        "sql": (
            "SELECT f.lot_wf, i.inform_id, i.inform_summary "
            "FROM fab_db f JOIN inform_db i USING(lot_wf) "
            "WHERE f.step_id='24.0 SORT'"
        ),
        "expect_rows": [{"lot_wf": "A1002_W05", "inform_id": "INF-2", "inform_summary": "sort split"}],
    },
    {
        "name": "PPID raw rows across fab split et",
        "sql": (
            "SELECT f.product, f.lot_wf, s.knob, e.value "
            "FROM fab_db f JOIN split_db s USING(lot_wf) JOIN et_db e USING(lot_wf, step_id) "
            "WHERE s.knob LIKE 'PPID%' ORDER BY f.lot_wf"
        ),
        "expect_rowcount": 4,
    },
    {
        "name": "knob high max CD",
        "sql": (
            "SELECT MAX(e.value) AS max_cd "
            "FROM split_db s JOIN et_db e USING(lot_wf) "
            "WHERE s.knob_value='HIGH'"
        ),
        "expect_rows": [{"max_cd": 10.2}],
    },
    {
        "name": "function_step by knob",
        "sql": (
            "SELECT DISTINCT m.function_step "
            "FROM split_db s JOIN fab_db f USING(lot_wf) JOIN step_map_db m USING(step_id) "
            "WHERE s.knob='PPID_A' ORDER BY m.function_step"
        ),
        "expect_rows": [{"function_step": "CONTACT"}],
    },
    {
        "name": "product raw row count",
        "sql": "SELECT product, COUNT(*) AS rows FROM fab_db GROUP BY product ORDER BY product",
        "expect_rows": [{"product": "PRODA", "rows": 3}, {"product": "PRODB", "rows": 2}],
    },
]


def _rows_for(sql: str, *, row_limit: int = 100) -> tuple[list[str], list[dict[str, Any]]]:
    out = run_workspace([*BASE_CELLS, {"name": None, "sql": sql}], row_limit=row_limit)
    result = out.get("result") or {}
    return list(result.get("columns") or []), list(result.get("rows") or [])


def run_semantic_cases(runner: EvalRunner) -> None:
    for case in SEMANTIC_CASES:
        frame = resolve_semantic_frame(case["prompt"], max_terms=48)
        surface = _semantic_surface(frame)
        for term in case.get("terms", []):
            runner.contains(f"semantic/{case['name']}/term/{term}", surface, term)
        if case.get("intent"):
            runner.equal(f"semantic/{case['name']}/intent", frame.intent, case["intent"])
        for slot_name, expected_values in (case.get("slots") or {}).items():
            actual_values = (frame.slots or {}).get(slot_name) or []
            for expected in expected_values:
                runner.contains(f"semantic/{case['name']}/slot/{slot_name}/{expected}", actual_values, expected)
        runner.check(f"semantic/{case['name']}/coverage", frame.coverage >= 0.2, f"coverage={frame.coverage}")


def run_knowledge_cases(runner: EvalRunner, *, cleanup: bool) -> None:
    body = (
        "Flow-i Agent deep eval operational terms.\n\n"
        "- step_id is the raw process step key. Map it through step_map_db to function_step.\n"
        "- function_step is the user-facing process family such as CONTACT, PHOTO, ETCH, SORT.\n"
        "- lot_wf is the wafer-level key used to join fab_db, split_db, et_db, tracker_db, and inform_db.\n"
        "- KNOB/PPID terms come from SplitTable and should filter split_db before returning lot_wf lists.\n"
        "- Multi-DB raw answers must preserve source columns, join keys, and row counts."
    )
    doc = KnowledgeDoc(
        doc_id=DOC_ID,
        kind="agent_wiki",
        title="[deep-eval] Agent semiconductor term and join rules",
        summary="step_id/function_step/lot_wf/KNOB multi-source join 검증용 운영 지식",
        body=body,
        actor="codex_deep_eval",
        tags=["agent", "deep-eval", "semantic", "lot_wf", "knob", "multi-db"],
        frontmatter={
            "relation_id": "flow_agent_deep_eval",
            "column_refs": [
                "step_map_db.step_id",
                "step_map_db.function_step",
                "fab_db.lot_wf",
                "split_db.knob",
                "et_db.value",
            ],
            "join_keys": ["step_id", "lot_wf"],
            "source": "scripts/flowi_agent_deep_eval.py",
        },
    )
    saved = kv.upsert_doc(doc)
    runner.equal("knowledge/upsert/doc_id", saved.get("doc_id"), DOC_ID)

    fetched = kv.get_doc(DOC_ID)
    runner.check("knowledge/get_doc", isinstance(fetched, dict), f"doc={type(fetched).__name__}")
    if isinstance(fetched, dict):
        runner.contains("knowledge/tags/lot_wf", fetched.get("tags") or [], "lot_wf")
        runner.check("knowledge/body/function_step", "function_step" in str(fetched.get("body") or ""))

    results = kv.search_agent_wiki("lot_wf function_step KNOB multi DB join", limit=20)
    ids = [str(row.get("id") or row.get("doc_id") or "") for row in results if isinstance(row, dict)]
    runner.contains("knowledge/search/finds_deep_eval_doc", ids, DOC_ID)

    if cleanup:
        deleted = kv.delete_doc(DOC_ID, actor="codex_deep_eval")
        runner.check("knowledge/cleanup/delete_doc", bool(deleted.get("deleted")), str(deleted))


def _normalize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        normalized: dict[str, Any] = {}
        for key, value in row.items():
            if isinstance(value, float):
                normalized[key] = round(value, 6)
            elif isinstance(value, str) and re.fullmatch(r"-?\d+\.\d+", value):
                normalized[key] = round(float(value), 6)
            elif isinstance(value, str) and re.fullmatch(r"-?\d+", value):
                normalized[key] = int(value)
            else:
                normalized[key] = value
        out.append(normalized)
    return out


def run_sql_cases(runner: EvalRunner) -> None:
    for case in SQL_CASES:
        columns, rows = _rows_for(case["sql"])
        rows = _normalize_rows(rows)
        if "expect_rows" in case:
            expected = _normalize_rows(case["expect_rows"])
            runner.equal(f"sql/{case['name']}/rows", rows, expected)
        if "expect_rowcount" in case:
            runner.equal(f"sql/{case['name']}/rowcount", len(rows), case["expect_rowcount"])
        if "expect_columns" in case:
            runner.equal(f"sql/{case['name']}/columns", columns, case["expect_columns"])

    try:
        run_workspace([{"name": None, "sql": "DROP TABLE fab_db"}])
        runner.check("sql/safety/drop_rejected", False, "DROP unexpectedly succeeded")
    except Exception as exc:
        runner.check("sql/safety/drop_rejected", "허용되지 않는 키워드" in str(exc), str(exc))


def _group_summary(results: list[CaseResult]) -> dict[str, dict[str, int]]:
    groups: dict[str, dict[str, int]] = {}
    for result in results:
        group = result.name.split("/", 1)[0] if "/" in result.name else "misc"
        bucket = groups.setdefault(group, {"passed": 0, "failed": 0, "total": 0})
        bucket["total"] += 1
        if result.ok:
            bucket["passed"] += 1
        else:
            bucket["failed"] += 1
    return groups


def _report_payload(runner: EvalRunner, *, cleanup_knowledge: bool, min_cases: int) -> dict[str, Any]:
    return {
        "summary": runner.summary(),
        "groups": _group_summary(runner.results),
        "doc_id": DOC_ID,
        "cleanup_knowledge": cleanup_knowledge,
        "min_cases": min_cases,
        "catalog": {
            "semantic_prompt_cases": len(SEMANTIC_CASES),
            "sql_answer_cases": len(SQL_CASES),
            "source_views": [str(cell.get("name")) for cell in BASE_CELLS if cell.get("name")],
        },
        "results": [
            {"name": result.name, "ok": result.ok, "detail": result.detail}
            for result in runner.results
        ],
    }


def _write_json_report(path: str | Path, payload: dict[str, Any]) -> Path:
    target = Path(path).expanduser()
    if not target.is_absolute():
        target = ROOT / target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Flow-i Agent deep eval cases.")
    parser.add_argument("--cleanup-knowledge", action="store_true", help="delete the eval Wiki doc after verification")
    parser.add_argument("--show-pass", action="store_true", help="print all passing cases")
    parser.add_argument("--json", action="store_true", help="print machine-readable summary after the text report")
    parser.add_argument("--report-json", default="", help="write detailed case results to this JSON path")
    parser.add_argument("--min-cases", type=int, default=80, help="minimum assertion count expected")
    args = parser.parse_args(argv)

    runner = EvalRunner()
    try:
        run_semantic_cases(runner)
        run_knowledge_cases(runner, cleanup=bool(args.cleanup_knowledge))
        run_sql_cases(runner)
        runner.check("meta/min_case_count", len(runner.results) >= args.min_cases, f"cases={len(runner.results)} min={args.min_cases}")
    except Exception as exc:  # keep failures visible instead of stopping at first exception.
        runner.check("meta/unhandled_exception", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    runner.print_report(show_pass=bool(args.show_pass))
    summary = runner.summary()
    payload = _report_payload(
        runner,
        cleanup_knowledge=bool(args.cleanup_knowledge),
        min_cases=int(args.min_cases),
    )
    if args.report_json:
        report_path = _write_json_report(args.report_json, payload)
        print(f"report_json: {report_path}")
    if args.json:
        print(
            json.dumps(
                {
                    "summary": summary,
                    "groups": payload["groups"],
                    "catalog": payload["catalog"],
                    "doc_id": DOC_ID,
                    "cleanup_knowledge": bool(args.cleanup_knowledge),
                },
                ensure_ascii=False,
            )
        )
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
