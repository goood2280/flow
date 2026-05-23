from __future__ import annotations

import json
import sys
from pathlib import Path

_FLOW_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _FLOW_ROOT / "backend"
for p in (_BACKEND, _FLOW_ROOT):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)


def test_ai_hub_deep_eval_report_reads_latest_runtime_report(tmp_path, monkeypatch):
    from core import ai_hub_deep_eval
    from core.paths import PATHS
    from routers import ai_hub

    monkeypatch.setattr(PATHS, "data_root", tmp_path)
    report_path = ai_hub_deep_eval.default_report_path()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "generated_at": "2026-05-24T01:30:00+00:00",
                "summary": {"passed": 12, "failed": 1, "total": 13},
                "groups": {
                    "semantic": {"passed": 10, "failed": 0, "total": 10},
                    "sql": {"passed": 2, "failed": 1, "total": 3},
                },
                "catalog": {
                    "semantic_prompt_cases": 26,
                    "sql_answer_cases": 15,
                    "source_views": ["fab_db", "split_db"],
                },
                "doc_id": "agent_deep_eval_semiconductor_terms",
                "cleanup_knowledge": False,
                "min_cases": 80,
                "results": [
                    {"name": "semantic/a", "ok": True, "detail": "ok"},
                    {"name": "sql/b", "ok": False, "detail": "bad row"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    out = ai_hub_deep_eval.load_latest_report()

    assert out["ok"] is True
    assert out["exists"] is True
    assert out["status"] == "fail"
    assert out["summary"] == {"passed": 12, "failed": 1, "total": 13}
    assert out["groups"]["sql"] == {"passed": 2, "failed": 1, "total": 3}
    assert out["catalog"]["source_views"] == ["fab_db", "split_db"]
    assert out["doc_id"] == "agent_deep_eval_semiconductor_terms"
    assert out["failed_results"] == [{"name": "sql/b", "detail": "bad row"}]
    assert out["path"] == "reports/flowi_agent_deep_eval_latest.json"

    api_out = ai_hub.deep_eval_report(_req())
    assert api_out["is_admin"] is True
    assert api_out["status"] == "fail"


def test_ai_hub_deep_eval_report_missing_is_explicit(tmp_path, monkeypatch):
    from core import ai_hub_deep_eval
    from core.paths import PATHS

    monkeypatch.setattr(PATHS, "data_root", tmp_path)

    out = ai_hub_deep_eval.load_latest_report()

    assert out["ok"] is False
    assert out["exists"] is False
    assert out["status"] == "missing"
    assert out["path"] == "reports/flowi_agent_deep_eval_latest.json"


def test_ai_hub_deep_eval_run_endpoint_regenerates_report(tmp_path, monkeypatch):
    from core import ai_hub_deep_eval
    from routers import ai_hub

    monkeypatch.setattr(ai_hub.audit, "ACTIVITY_LOG", tmp_path / "activity.jsonl")

    def fake_run_latest_report(*, cleanup_knowledge=False, min_cases=80):
        assert cleanup_knowledge is True
        assert min_cases == 12
        return {
            "ok": True,
            "status": "pass",
            "summary": {"passed": 12, "failed": 0, "total": 12},
            "report": {
                "ok": True,
                "exists": True,
                "status": "pass",
                "summary": {"passed": 12, "failed": 0, "total": 12},
            },
        }

    monkeypatch.setattr(ai_hub_deep_eval, "run_latest_report", fake_run_latest_report)

    out = ai_hub.deep_eval_report_run(
        _req(),
        ai_hub.DeepEvalRunRequest(cleanup_knowledge=True, min_cases=12),
    )

    assert out["ok"] is True
    assert out["status"] == "pass"
    assert out["is_admin"] is True
    assert out["report"]["is_admin"] is True
    assert out["report"]["summary"] == {"passed": 12, "failed": 0, "total": 12}


class _State:
    user = {"username": "alice", "role": "admin"}


class _Req:
    state = _State()
    headers = {}


def _req():
    return _Req()
