"""AI Hub deep-eval report reader.

The deep-eval script writes operator-owned runtime reports under data_root.
This module keeps AI Hub read-only: it never executes tests, it only summarizes
the latest JSON report already produced by the script.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.paths import PATHS


REPORT_RELATIVE_PATH = Path("reports") / "flowi_agent_deep_eval_latest.json"


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
