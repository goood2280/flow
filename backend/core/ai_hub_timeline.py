"""AI Hub operational timeline.

Read-only view over the existing activity log. It keeps AI Hub management
events visible without creating another runtime store.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from core import audit


def build_timeline(*, days: int = 30, limit: int = 30, category: str = "") -> dict[str, Any]:
    days = max(1, min(365, int(days or 30)))
    limit = max(1, min(120, int(limit or 30)))
    category = str(category or "").strip()
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400

    items: list[dict[str, Any]] = []
    scanned = 0
    for rec in _read_recent_activity(max_lines=4000):
        scanned += 1
        ts = _parse_ts(str(rec.get("timestamp") or ""))
        if ts and ts.timestamp() < cutoff:
            continue
        item = _timeline_item(rec)
        if not item:
            continue
        if category and item.get("category") != category:
            continue
        items.append(item)
        if len(items) >= limit:
            break

    counts = Counter(str(row.get("category") or "") for row in items)
    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days": days,
        "limit": limit,
        "category": category,
        "scanned": scanned,
        "counts": dict(counts),
        "items": items,
    }


def _read_recent_activity(*, max_lines: int) -> list[dict[str, Any]]:
    log = audit.ACTIVITY_LOG
    if not log.exists():
        return []
    try:
        lines = log.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in reversed(lines[-max_lines:]):
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if isinstance(rec, dict):
            rows.append(rec)
    return rows


def _timeline_item(rec: dict[str, Any]) -> dict[str, Any] | None:
    action = str(rec.get("action") or "")
    tab = str(rec.get("tab") or "")
    category = _category(action, tab)
    if not category:
        return None
    detail_text = str(rec.get("detail") or "")
    detail = _json_detail(detail_text)
    timestamp = str(rec.get("timestamp") or "")
    title, meta = _title_meta(action, detail, detail_text)
    return {
        "id": f"{timestamp}:{action}",
        "timestamp": timestamp,
        "username": str(rec.get("username") or ""),
        "action": action,
        "tab": tab,
        "category": category,
        "title": title,
        "meta": meta,
        "detail": _detail_summary(detail, detail_text),
        "tone": _tone(category, action, detail, detail_text),
        "workflow_key": _workflow_key(action, detail),
    }


def _category(action: str, tab: str) -> str:
    if action.startswith("ai_hub_run:workflow:") or action == "ai_hub_readiness_bootstrap_workflows":
        return "workflow"
    if action.startswith("ai_hub_toggle:"):
        return "tool"
    if action == "ai_hub_deep_eval_run":
        return "validation"
    if action.startswith("skill:"):
        return "skill"
    if action.startswith("semantic:"):
        return "semantic"
    if tab == "ai_hub" and action:
        return "ai_hub"
    return ""


def _title_meta(action: str, detail: dict[str, Any], detail_text: str) -> tuple[str, str]:
    if action.startswith("ai_hub_run:workflow:"):
        key = action.split("ai_hub_run:workflow:", 1)[-1]
        title = str(detail.get("title") or key)
        status = _status_text(detail)
        return title, status or ("dry-run" if detail.get("dry_run") else "executed")
    if action.startswith("ai_hub_toggle:"):
        return action.split("ai_hub_toggle:", 1)[-1], _extract_kv(detail_text, "enabled")
    if action == "ai_hub_deep_eval_run":
        return "Agent deep-eval 재검증", _extract_counts(detail_text)
    if action == "ai_hub_readiness_bootstrap_workflows":
        return "시작 workflow 템플릿 생성", _extract_counts(detail_text)
    if action.startswith("skill:approve:"):
        return action.split("skill:approve:", 1)[-1], "skill approved"
    if action.startswith("skill:reject:"):
        return action.split("skill:reject:", 1)[-1], "skill rejected"
    if action == "skill:mine":
        return "스킬 후보 마이닝", _extract_counts(detail_text)
    if action.startswith("semantic:"):
        parts = action.split(":")
        title = parts[-1] if len(parts) > 2 else action
        meta = ":".join(parts[1:-1]) if len(parts) > 2 else "semantic"
        return title, meta
    return action, detail_text[:80]


def _detail_summary(detail: dict[str, Any], detail_text: str) -> str:
    if detail:
        if isinstance(detail.get("statuses"), dict):
            statuses = ", ".join(f"{k}:{v}" for k, v in detail["statuses"].items())
            if statuses:
                return statuses
        if detail.get("workflow"):
            return f"workflow={detail.get('workflow')} steps={detail.get('steps') or 0}"
    return detail_text[:220]


def _tone(category: str, action: str, detail: dict[str, Any], detail_text: str) -> str:
    if category == "tool":
        return "ok" if "enabled=true" in str(detail_text).lower() else "warn"
    if category == "validation":
        return "ok" if "failed=0" in str(detail_text).lower() else "bad"
    if category == "workflow":
        statuses = detail.get("statuses") if isinstance(detail.get("statuses"), dict) else {}
        bad = sum(int(statuses.get(k) or 0) for k in ("error", "blocked", "missing_slots", "confirm_required", "no_handler"))
        return "warn" if bad else "info"
    if category == "semantic":
        return "ok" if ":approved:" in action or ":upsert:" in action else ("bad" if ":rejected:" in action else "info")
    if category == "skill":
        return "ok" if ":approve:" in action else ("bad" if ":reject:" in action else "info")
    return "neutral"


def _workflow_key(action: str, detail: dict[str, Any]) -> str:
    if action.startswith("ai_hub_run:workflow:"):
        return action.split("ai_hub_run:workflow:", 1)[-1]
    return str(detail.get("workflow") or "")


def _json_detail(value: str) -> dict[str, Any]:
    try:
        out = json.loads(str(value or "{}"))
    except Exception:
        return {}
    return out if isinstance(out, dict) else {}


def _parse_ts(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _status_text(detail: dict[str, Any]) -> str:
    statuses = detail.get("statuses") if isinstance(detail.get("statuses"), dict) else {}
    return ", ".join(f"{k}:{v}" for k, v in statuses.items())


def _extract_kv(text: str, key: str) -> str:
    marker = f"{key}="
    for part in str(text or "").replace(";", " ").split():
        if part.startswith(marker):
            return part
    return ""


def _extract_counts(text: str) -> str:
    parts = [
        part for part in str(text or "").replace(";", " ").split()
        if "=" in part and any(ch.isdigit() for ch in part)
    ]
    return " ".join(parts[:4])
