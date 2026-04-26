from __future__ import annotations

import datetime as dt
from pathlib import Path

from core.paths import PATHS
from core.utils import jsonl_append, load_json, save_json

TRACKER_DIR = PATHS.data_root / "tracker"
ISSUES_FILE = TRACKER_DIR / "issues.json"
MIGRATION_LOG = TRACKER_DIR / "schema_migrations.jsonl"

LOT_WF_SCHEMA_DEFAULTS = {
    "current_step": None,
    "current_function_step": None,
    "function_step": None,
    "func_step": None,
    "current_step_seq": None,
    "step_seq": None,
    "et_measured": None,
    "et_last_seq": None,
    "et_last_time": None,
    "et_last_step": None,
    "et_last_function_step": None,
    "et_step_summary": [],
    "et_step_seq_summary": "",
    "et_recent_formatted": "",
    "last_move_at": None,
    "last_checked_at": None,
    "last_scan_source": "",
    "last_scan_source_root": "",
    "last_scan_status": "",
}


def normalize_lot_row(row: dict | None) -> dict:
    src = dict(row or {})
    out = dict(src)
    legacy_seq = src.get("current_step_seq", src.get("step_seq"))
    legacy_et_seq = src.get("et_last_seq", src.get("step_seq"))
    legacy_et_time = src.get("et_last_time", src.get("last_move_at"))
    legacy_func = src.get("current_function_step") or src.get("function_step") or src.get("func_step")
    out.setdefault("current_step", src.get("current_step"))
    out.setdefault("current_function_step", legacy_func)
    out.setdefault("function_step", legacy_func)
    out.setdefault("func_step", legacy_func)
    out.setdefault("current_step_seq", legacy_seq)
    out.setdefault("step_seq", legacy_seq)
    out.setdefault("et_measured", src.get("et_measured"))
    out.setdefault("et_last_seq", legacy_et_seq)
    out.setdefault("et_last_time", legacy_et_time)
    out.setdefault("et_last_step", src.get("et_last_step"))
    out.setdefault("et_last_function_step", src.get("et_last_function_step"))
    out.setdefault("et_step_summary", src.get("et_step_summary") or [])
    out.setdefault("et_step_seq_summary", src.get("et_step_seq_summary") or "")
    out.setdefault("et_recent_formatted", src.get("et_recent_formatted") or "")
    out.setdefault("last_move_at", src.get("last_move_at"))
    out.setdefault("last_checked_at", src.get("last_checked_at"))
    out.setdefault("last_scan_source", src.get("last_scan_source") or "")
    out.setdefault("last_scan_source_root", src.get("last_scan_source_root") or "")
    out.setdefault("last_scan_status", src.get("last_scan_status") or "")
    for key, default in LOT_WF_SCHEMA_DEFAULTS.items():
        if key not in out:
            out[key] = list(default) if isinstance(default, list) else default
    return out


def normalize_issue(issue: dict | None) -> tuple[dict, int]:
    src = dict(issue or {})
    lots = list(src.get("lots") or [])
    next_lots = []
    changed = 0
    for lot in lots:
        normalized = normalize_lot_row(lot)
        if normalized != (lot or {}):
            changed += 1
        next_lots.append(normalized)
    out = dict(src)
    out["lots"] = next_lots
    return out, changed


def migrate_tracker_issues_file(
    *,
    path: Path | None = None,
    create_backup: bool = True,
    reason: str = "manual",
    actor: str = "system",
) -> dict:
    target = path or ISSUES_FILE
    TRACKER_DIR.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        result = {
            "ok": True,
            "path": str(target),
            "issues": 0,
            "lots_updated": 0,
            "changed": False,
            "backup_path": "",
            "log_path": str(MIGRATION_LOG),
        }
        jsonl_append(MIGRATION_LOG, {"event": "tracker_schema_migrate", "reason": reason, "actor": actor, **result})
        return result
    data = load_json(target, [])
    issues = data if isinstance(data, list) else []
    next_issues = []
    lots_updated = 0
    changed = False
    for issue in issues:
        normalized, issue_changes = normalize_issue(issue)
        next_issues.append(normalized)
        lots_updated += issue_changes
        if normalized != issue:
            changed = True
    backup_path = ""
    if changed:
        if create_backup:
            stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = target.with_name(f"{target.stem}.backup_{stamp}{target.suffix}")
            backup.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
            backup_path = str(backup)
        save_json(target, next_issues, indent=2)
    result = {
        "ok": True,
        "path": str(target),
        "issues": len(next_issues),
        "lots_updated": lots_updated,
        "changed": changed,
        "backup_path": backup_path,
        "log_path": str(MIGRATION_LOG),
    }
    jsonl_append(MIGRATION_LOG, {"event": "tracker_schema_migrate", "reason": reason, "actor": actor, **result})
    return result
