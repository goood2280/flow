"""Flow-i workflow templates — M5.

Declarative templates that map repeated prompt patterns to a sequence
of unit AI calls. Files live in `data/flow-data/flowi_workflow_templates/`
as one JSON per template. Stored shape:

    {
        "key": "<unique>",
        "title": "<human label>",
        "trigger": {
            "intent_in":        ["inform", ...],          # optional
            "prompt_contains":  ["GATE 모듈", ...],       # optional substrings (any-match)
            "slots_required":   ["product", "lot"]        # optional (informational for now)
        },
        "steps": [
            {"unit_ai": "filebrowser", "action": "query_current_fab_lot_from_fab_db",
             "bind_slots": ["product", "lot"]},
            {"unit_ai": "inform", "action": "create_draft",
             "fixed_slots": {"module": "GATE", "split": "test1"}}
        ],
        "owner": "<username>",
        "shared": false,
        "created_at": "...", "updated_at": "..."
    }

The dispatcher integration is intentionally light in this PR: `match_prompt`
returns the best-matching template (or None). Wire-up into the
_run_flowi_chat dispatcher arrives in a follow-up PR.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.paths import PATHS

_DIR = PATHS.data_root / "flowi_workflow_templates"


def _slug(key: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(key or "").strip())
    return safe[:80] or "template"


def _path_for(key: str) -> Path:
    return _DIR / f"{_slug(key)}.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_dir() -> None:
    _DIR.mkdir(parents=True, exist_ok=True)


def list_templates(username: str = "", *, include_shared: bool = True) -> list[dict[str, Any]]:
    """All templates visible to `username` — own + shared."""
    _ensure_dir()
    out: list[dict[str, Any]] = []
    for fp in sorted(_DIR.glob("*.json")):
        try:
            row = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(row, dict):
            continue
        owner = str(row.get("owner") or "")
        shared = bool(row.get("shared"))
        if username and owner == username:
            out.append(row)
        elif include_shared and shared:
            out.append(row)
        elif not username:
            out.append(row)
    return out


def get_template(key: str) -> dict[str, Any] | None:
    fp = _path_for(key)
    if not fp.exists():
        return None
    try:
        row = json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return row if isinstance(row, dict) else None


def save_template(row: dict[str, Any], *, by: str = "", is_admin: bool = False) -> dict[str, Any]:
    """Create or update. `shared=true` is admin-only."""
    if not isinstance(row, dict):
        raise ValueError("template payload must be a dict")
    key = str(row.get("key") or "").strip()
    if not key:
        raise ValueError("template requires key")
    title = str(row.get("title") or key)
    trigger = row.get("trigger") if isinstance(row.get("trigger"), dict) else {}
    steps = row.get("steps") if isinstance(row.get("steps"), list) else []
    shared = bool(row.get("shared"))
    if shared and not is_admin:
        raise PermissionError("shared=true requires admin")

    existing = get_template(key) or {}
    existing_owner = str(existing.get("owner") or "")
    if existing and not is_admin:
        if bool(existing.get("shared")):
            raise PermissionError("shared templates require admin to update")
        if existing_owner and existing_owner != str(by or ""):
            raise PermissionError("only owner or admin can update")
    out = {
        "key": _slug(key),
        "title": title,
        "trigger": {
            "intent_in": list(trigger.get("intent_in") or []),
            "prompt_contains": list(trigger.get("prompt_contains") or []),
            "slots_required": list(trigger.get("slots_required") or []),
        },
        "steps": [_clean_step(s) for s in steps if isinstance(s, dict)],
        "owner": str(existing_owner or by or ""),
        "shared": shared,
        "created_at": existing.get("created_at") or _now(),
        "updated_at": _now(),
    }
    _ensure_dir()
    _path_for(key).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def _clean_step(s: dict[str, Any]) -> dict[str, Any]:
    return {
        "unit_ai": str(s.get("unit_ai") or "").strip(),
        "action": str(s.get("action") or "").strip(),
        "bind_slots": list(s.get("bind_slots") or []),
        "fixed_slots": dict(s.get("fixed_slots") or {}),
    }


def delete_template(key: str, *, by: str = "", is_admin: bool = False) -> bool:
    row = get_template(key)
    if row is None:
        return False
    owner = str(row.get("owner") or "")
    shared = bool(row.get("shared"))
    if not is_admin and owner != by:
        raise PermissionError("only owner or admin can delete")
    if shared and not is_admin:
        raise PermissionError("shared templates require admin to delete")
    fp = _path_for(key)
    try:
        fp.unlink()
    except OSError:
        return False
    return True


def match_prompt(prompt: str, *, intent: str = "", username: str = "") -> dict[str, Any] | None:
    """Return the first template whose trigger matches the prompt.

    Matching rules: ALL listed `prompt_contains` substrings must be in
    the prompt (case-insensitive). If `intent_in` is set and `intent`
    is provided, `intent` must be one of `intent_in`. Templates with
    empty triggers never match (avoid catch-alls).
    """
    text = str(prompt or "")
    low = text.lower()
    intent_l = str(intent or "").lower()
    for row in list_templates(username, include_shared=True):
        trig = row.get("trigger") or {}
        contains = [str(s).lower() for s in (trig.get("prompt_contains") or [])]
        intent_in = [str(s).lower() for s in (trig.get("intent_in") or [])]
        if not contains and not intent_in:
            continue
        if contains and not all(token in low for token in contains):
            continue
        if intent_in and intent_l and intent_l not in intent_in:
            continue
        if intent_in and not intent_l:
            # intent gate set but caller didn't pass — be conservative, skip
            continue
        return row
    return None


# ── Workflow execution (P5) ─────────────────────────────────────────────
# Read-only step actions can be auto-executed via the dispatcher. Anything
# else returns `confirm_required: True` so the UI can ask the user to run
# the write step explicitly.
_WRITE_ACTION_HINTS = (
    "create", "save", "delete", "remove", "update", "send_mail", "register", "approve",
)


def _is_write_action(action: str) -> bool:
    action_l = str(action or "").lower()
    return any(hint in action_l for hint in _WRITE_ACTION_HINTS)


def _bind_step_slots(step: dict[str, Any], slots: dict[str, Any]) -> dict[str, Any]:
    """Resolve `bind_slots` references against the runtime `slots` dict.

    Returns a new dict merging `fixed_slots` with values pulled from `slots`
    for each `bind_slots` key. Missing slot references become None so callers
    can report `missing_slots` cleanly.
    """
    bound: dict[str, Any] = {}
    bound.update({k: v for k, v in (step.get("fixed_slots") or {}).items() if k})
    for slot_key in step.get("bind_slots") or []:
        if not slot_key:
            continue
        value = slots.get(slot_key) if isinstance(slots, dict) else None
        bound[slot_key] = value
    return bound


def execute_steps(
    template: dict[str, Any],
    *,
    slots: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Sequentially walk a template's steps.

    For each step:
    - Bind fixed_slots ∪ bind_slots from `slots`.
    - Mark missing slots so the UI can highlight what to fill in.
    - If `dry_run` OR the action is a write action, do NOT call the
      dispatcher — return `confirm_required: True` for write steps.
    - Otherwise call the Flow-i dispatcher with the bound slots.

    Returns a structured result with one row per step plus a summary.
    """
    slots = slots if isinstance(slots, dict) else {}
    out_steps: list[dict[str, Any]] = []
    confirm_required = False
    for index, raw_step in enumerate(template.get("steps") or []):
        if not isinstance(raw_step, dict):
            continue
        unit_ai = str(raw_step.get("unit_ai") or "").strip()
        action = str(raw_step.get("action") or "").strip()
        bound = _bind_step_slots(raw_step, slots)
        missing = [k for k, v in bound.items() if v in (None, "", [], {})]
        row: dict[str, Any] = {
            "index": index,
            "unit_ai": unit_ai,
            "action": action,
            "bound_slots": bound,
            "missing_slots": missing,
            "status": "pending",
            "result": None,
            "error": "",
        }
        if not unit_ai or not action:
            row["status"] = "skipped"
            row["error"] = "unit_ai or action missing"
            out_steps.append(row)
            continue
        if dry_run:
            row["status"] = "dry_run"
            out_steps.append(row)
            continue
        if _is_write_action(action):
            row["status"] = "confirm_required"
            confirm_required = True
            out_steps.append(row)
            continue
        if missing:
            row["status"] = "missing_slots"
            out_steps.append(row)
            continue
        try:
            from core.flowi_units.dispatcher import try_dispatch  # type: ignore
            product = ""
            if isinstance(bound.get("product"), str):
                product = bound["product"]
            elif isinstance(bound.get("products"), list) and bound["products"]:
                product = str(bound["products"][0])
            synthetic_prompt = " ".join([
                str(template.get("title") or ""),
                action,
                *[str(v) for v in bound.values() if isinstance(v, (str, int)) and str(v)],
            ]).strip()
            dispatcher_result = try_dispatch(
                synthetic_prompt,
                product=product,
                max_rows=12,
                agent_context={"workflow": str(template.get("key") or ""), "action": action, "slots": bound},
                only=(unit_ai,),
            )
            if dispatcher_result is None:
                row["status"] = "no_handler"
            else:
                row["status"] = "ok"
                row["result"] = {"keys": sorted(list((dispatcher_result or {}).keys()))[:24]}
        except Exception as exc:
            row["status"] = "error"
            row["error"] = str(exc)[:200]
        out_steps.append(row)
    return {
        "workflow": str(template.get("key") or ""),
        "title": template.get("title") or "",
        "steps": out_steps,
        "confirm_required": confirm_required,
        "dry_run": bool(dry_run),
    }
