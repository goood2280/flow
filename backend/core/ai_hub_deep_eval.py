"""AI Hub deep-eval report reader/runner.

The deep-eval script writes operator-owned runtime reports under data_root.
AI Hub normally reads the latest report. Admin-only API actions can regenerate
the report by calling the deterministic deep-eval runner in-process.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.paths import PATHS


REPORT_RELATIVE_PATH = Path("reports") / "flowi_agent_deep_eval_latest.json"
_FLOW_ROOT = Path(__file__).resolve().parents[2]
_DEEP_EVAL_SCRIPT = _FLOW_ROOT / "scripts" / "flowi_agent_deep_eval.py"


def default_report_path() -> Path:
    return PATHS.data_root / REPORT_RELATIVE_PATH


def _utc_iso_from_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _age_seconds(path: Path) -> int:
    return max(0, int(datetime.now(timezone.utc).timestamp() - path.stat().st_mtime))


def _clean_count_dict(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {"passed": 0, "failed": 0, "total": 0}
    out: dict[str, int] = {}
    for key in ("passed", "failed", "total"):
        try:
            out[key] = int(value.get(key) or 0)
        except Exception:
            out[key] = 0
    return out


def load_latest_report() -> dict[str, Any]:
    path = default_report_path()
    relative = str(REPORT_RELATIVE_PATH).replace("\\", "/")
    if not path.exists():
        return {
            "ok": False,
            "exists": False,
            "status": "missing",
            "path": relative,
            "message": "latest deep-eval report not found",
        }

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "ok": False,
            "exists": True,
            "status": "invalid",
            "path": relative,
            "updated_at": _utc_iso_from_mtime(path),
            "age_seconds": _age_seconds(path),
            "message": f"invalid deep-eval report: {exc}",
        }

    summary = _clean_count_dict(payload.get("summary"))
    groups = {
        str(name): _clean_count_dict(value)
        for name, value in (payload.get("groups") or {}).items()
        if isinstance(name, str)
    }
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    failed_results = [
        {
            "name": str(row.get("name") or ""),
            "detail": str(row.get("detail") or ""),
        }
        for row in results
        if isinstance(row, dict) and not bool(row.get("ok"))
    ]
    status = "pass" if summary["failed"] == 0 else "fail"

    return {
        "ok": True,
        "exists": True,
        "status": status,
        "path": relative,
        "updated_at": _utc_iso_from_mtime(path),
        "generated_at": str(payload.get("generated_at") or _utc_iso_from_mtime(path)),
        "age_seconds": _age_seconds(path),
        "summary": summary,
        "groups": groups,
        "catalog": payload.get("catalog") if isinstance(payload.get("catalog"), dict) else {},
        "doc_id": str(payload.get("doc_id") or ""),
        "cleanup_knowledge": bool(payload.get("cleanup_knowledge")),
        "min_cases": int(payload.get("min_cases") or 0),
        "result_count": len(results),
        "failed_results": failed_results[:20],
    }


def run_latest_report(*, cleanup_knowledge: bool = False, min_cases: int = 80) -> dict[str, Any]:
    """Regenerate the latest deep-eval report and return its summarized state."""
    module = _load_deep_eval_module()
    runner = module.EvalRunner()
    min_cases = max(1, int(min_cases or 80))
    error = ""
    try:
        module.run_semantic_cases(runner)
        module.run_knowledge_cases(runner, cleanup=bool(cleanup_knowledge))
        module.run_sql_cases(runner)
        runner.check(
            "meta/min_case_count",
            len(runner.results) >= min_cases,
            f"cases={len(runner.results)} min={min_cases}",
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        runner.check("meta/unhandled_exception", False, error)

    payload = module._report_payload(
        runner,
        cleanup_knowledge=bool(cleanup_knowledge),
        min_cases=min_cases,
    )
    module._write_json_report("latest", payload)
    report = load_latest_report()
    summary = runner.summary()
    status = "pass" if summary["failed"] == 0 else "fail"
    return {
        "ok": status == "pass",
        "status": status,
        "summary": summary,
        "cleanup_knowledge": bool(cleanup_knowledge),
        "min_cases": min_cases,
        "path": str(REPORT_RELATIVE_PATH).replace("\\", "/"),
        "error": error,
        "report": report,
    }


def _load_deep_eval_module():
    name = "_flowi_agent_deep_eval_runtime"
    spec = importlib.util.spec_from_file_location(name, _DEEP_EVAL_SCRIPT)
    if not spec or not spec.loader:
        raise RuntimeError(f"cannot load deep-eval script: {_DEEP_EVAL_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
