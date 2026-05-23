from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


_FLOW_ROOT = Path(__file__).resolve().parent.parent


def _load_deep_eval_module():
    name = "flowi_agent_deep_eval_for_test"
    spec = importlib.util.spec_from_file_location(name, _FLOW_ROOT / "scripts" / "flowi_agent_deep_eval.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_deep_eval_report_payload_groups_and_writes_json(tmp_path):
    deep_eval = _load_deep_eval_module()
    runner = deep_eval.EvalRunner()
    runner.check("semantic/case one/coverage", True, "coverage=1.0")
    runner.check("sql/case two/rows", False, "bad rows")
    runner.check("meta/min_case_count", True, "cases=3 min=2")

    payload = deep_eval._report_payload(runner, cleanup_knowledge=True, min_cases=2)

    assert payload["summary"] == {"passed": 2, "failed": 1, "total": 3}
    assert payload["groups"]["semantic"] == {"passed": 1, "failed": 0, "total": 1}
    assert payload["groups"]["sql"] == {"passed": 0, "failed": 1, "total": 1}
    assert payload["groups"]["meta"] == {"passed": 1, "failed": 0, "total": 1}
    assert payload["catalog"]["semantic_prompt_cases"] == len(deep_eval.SEMANTIC_CASES)
    assert payload["catalog"]["sql_answer_cases"] == len(deep_eval.SQL_CASES)

    report_path = deep_eval._write_json_report(tmp_path / "deep-eval-report.json", payload)
    saved = json.loads(report_path.read_text(encoding="utf-8"))

    assert saved["doc_id"] == deep_eval.DOC_ID
    assert saved["results"][1] == {"name": "sql/case two/rows", "ok": False, "detail": "bad rows"}
