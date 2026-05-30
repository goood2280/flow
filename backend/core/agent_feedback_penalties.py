"""Runtime feedback penalty profile for Agent Unit AIs.

Ratings update a data-root JSON profile. They do not rewrite prompts, code, or
stored runtime overrides.
"""
from __future__ import annotations

import datetime as _dt
import re
from copy import deepcopy
from typing import Any

from app_v2.shared.json_store import JsonFileStore
from core.paths import PATHS


PENALTIES_FILE = PATHS.data_root / "agent_feedback_penalties.json"
_VALID_RATINGS = {"up", "down"}
_UNIT_KEYS = {"filebrowser_ai_sql", "inform_registration", "change_management", "dashboard_agent"}
_HOME_FEATURE_UNIT_MAP = {
    "filebrowser": "filebrowser_ai_sql",
    "filebrowser_ai_sql": "filebrowser_ai_sql",
    "inform": "inform_registration",
    "inform_registration": "inform_registration",
    "meeting": "change_management",
    "calendar": "change_management",
    "change": "change_management",
    "change_management": "change_management",
    "dashboard": "dashboard_agent",
    "chart": "dashboard_agent",
    "dashboard_agent": "dashboard_agent",
}


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _clean_text(value: Any, limit: int = 240) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text[: max(1, limit)]


def _clean_key(value: Any, limit: int = 120) -> str:
    text = _clean_text(value, limit)
    return re.sub(r"[^A-Za-z0-9_.:-]+", "_", text).strip("._-")[:limit]


def home_feedback_unit_key(tool: Any = None, *, suggested_tool: str = "") -> str:
    """Map a Home answer/tool payload to the unit feedback profile key."""
    candidates: list[Any] = [suggested_tool]
    if isinstance(tool, dict):
        candidates.extend([
            tool.get("unit_ai"),
            tool.get("unit_key"),
            tool.get("tool"),
            tool.get("name"),
            tool.get("feature"),
            tool.get("action"),
            tool.get("intent"),
        ])
    elif tool is not None:
        candidates.append(tool)
    for candidate in candidates:
        key = _clean_key(candidate).lower()
        if not key:
            continue
        if key in _UNIT_KEYS:
            return key
        mapped = _HOME_FEATURE_UNIT_MAP.get(key)
        if mapped:
            return mapped
    return ""


def _empty_store() -> dict[str, Any]:
    return {
        "version": 1,
        "units": {},
        "nodes": {},
        "home": {},
    }


def _store() -> JsonFileStore:
    return JsonFileStore(PENALTIES_FILE, default=_empty_store)


def _normalize_store(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        data = {}
    normalized = _empty_store()
    for key in ("units", "nodes", "home"):
        value = data.get(key)
        normalized[key] = value if isinstance(value, dict) else {}
    normalized["version"] = int(data.get("version") or 1) if isinstance(data, dict) else 1
    return normalized


def _count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _penalty(up_count: int, down_count: int) -> float:
    return round(min(5.0, max(0, down_count - up_count) * 1.0), 2)


def _boost(up_count: int, down_count: int) -> float:
    return round(min(2.0, max(0, up_count - down_count) * 0.4), 2)


def _unit_record(unit_key: str, raw: Any | None = None) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    up_count = _count(raw.get("up_count"))
    down_count = _count(raw.get("down_count"))
    return {
        "unit_key": unit_key,
        "up_count": up_count,
        "down_count": down_count,
        "penalty": _penalty(up_count, down_count),
        "boost": _boost(up_count, down_count),
        "last_rating": _clean_text(raw.get("last_rating"), 12),
        "updated_at": _clean_text(raw.get("updated_at"), 80),
        "updated_by": _clean_text(raw.get("updated_by"), 120),
    }


def _node_record(unit_key: str, node_id: str, raw: Any | None = None) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    up_count = _count(raw.get("up_count"))
    down_count = _count(raw.get("down_count"))
    return {
        "unit_key": unit_key,
        "node_id": node_id,
        "up_count": up_count,
        "down_count": down_count,
        "penalty": _penalty(up_count, down_count),
        "last_rating": _clean_text(raw.get("last_rating"), 12),
        "updated_at": _clean_text(raw.get("updated_at"), 80),
        "updated_by": _clean_text(raw.get("updated_by"), 120),
    }


def _home_record(key: str, raw: Any | None = None) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    up_count = _count(raw.get("up_count"))
    down_count = _count(raw.get("down_count"))
    return {
        "key": key,
        "planner": _clean_text(raw.get("planner"), 80),
        "tool": _clean_key(raw.get("tool"), 120),
        "source": _clean_text(raw.get("source"), 80),
        "reason_signature": _clean_text(raw.get("reason_signature"), 160),
        "up_count": up_count,
        "down_count": down_count,
        "penalty": _penalty(up_count, down_count),
        "boost": _boost(up_count, down_count),
        "last_rating": _clean_text(raw.get("last_rating"), 12),
        "updated_at": _clean_text(raw.get("updated_at"), 80),
        "updated_by": _clean_text(raw.get("updated_by"), 120),
    }


def _apply_rating(record: dict[str, Any], rating: str, *, actor: str = "") -> dict[str, Any]:
    if rating not in _VALID_RATINGS:
        raise ValueError("rating must be up or down")
    record = dict(record or {})
    if rating == "up":
        record["up_count"] = _count(record.get("up_count")) + 1
    else:
        record["down_count"] = _count(record.get("down_count")) + 1
    record["last_rating"] = rating
    record["updated_at"] = _now_iso()
    record["updated_by"] = _clean_text(actor, 120)
    return record


def load_all() -> dict[str, Any]:
    return _normalize_store(_store().load())


def feedback_profile(unit_key: str) -> dict[str, Any]:
    key = _clean_key(unit_key)
    data = load_all()
    nodes_raw = data.get("nodes", {}).get(key)
    nodes = {
        node_id: _node_record(key, node_id, raw)
        for node_id, raw in (nodes_raw.items() if isinstance(nodes_raw, dict) else [])
    }
    home_methods = [
        _home_record(method_key, raw)
        for method_key, raw in (data.get("home") or {}).items()
        if isinstance(raw, dict) and _clean_key(raw.get("tool")) == key
    ]
    home_methods.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return {
        "unit": _unit_record(key, data.get("units", {}).get(key)),
        "nodes": nodes,
        "home_methods": home_methods,
    }


def record_feedback(
    unit_key: str,
    rating: str,
    *,
    node_id: str = "",
    run_id: str = "",
    reason: str = "",
    actor: str = "",
) -> dict[str, Any]:
    key = _clean_key(unit_key)
    node_key = _clean_key(node_id)
    rating_key = _clean_text(rating, 12).lower()
    if not key:
        raise ValueError("unit_key is required")
    if rating_key not in _VALID_RATINGS:
        raise ValueError("rating must be up or down")

    def updater(current: Any) -> dict[str, Any]:
        data = _normalize_store(current)
        units = data.setdefault("units", {})
        unit = _unit_record(key, units.get(key))
        unit = _unit_record(key, _apply_rating(unit, rating_key, actor=actor))
        unit["last_run_id"] = _clean_text(run_id, 160)
        unit["last_reason"] = _clean_text(reason, 500)
        units[key] = unit
        if node_key:
            nodes_by_unit = data.setdefault("nodes", {}).setdefault(key, {})
            node = _node_record(key, node_key, nodes_by_unit.get(node_key))
            node = _node_record(key, node_key, _apply_rating(node, rating_key, actor=actor))
            node["last_run_id"] = _clean_text(run_id, 160)
            node["last_reason"] = _clean_text(reason, 500)
            nodes_by_unit[node_key] = node
        data["updated_at"] = _now_iso()
        return data

    _store().load_and_update(updater)
    return feedback_profile(key)


def _reason_signature(reason: str) -> str:
    text = _clean_text(reason, 240).lower()
    return text[:160]


def record_home_feedback(
    *,
    rating: str,
    planner: str = "",
    tool: str = "",
    source: str = "",
    reason: str = "",
    actor: str = "",
) -> dict[str, Any]:
    rating_key = _clean_text(rating, 12).lower()
    if rating_key not in _VALID_RATINGS:
        raise ValueError("rating must be up or down")
    planner_key = _clean_text(planner, 80)
    tool_key = _clean_key(tool)
    source_key = _clean_text(source, 80)
    signature = _reason_signature(reason)
    method_key = "|".join([planner_key or "-", tool_key or "-", source_key or "-", signature or "-"])[:360]

    def updater(current: Any) -> dict[str, Any]:
        data = _normalize_store(current)
        home = data.setdefault("home", {})
        rec = _home_record(method_key, home.get(method_key))
        rec.update({
            "planner": planner_key,
            "tool": tool_key,
            "source": source_key,
            "reason_signature": signature,
        })
        home[method_key] = _home_record(method_key, _apply_rating(rec, rating_key, actor=actor))
        data["updated_at"] = _now_iso()
        return data

    updated = _store().load_and_update(updater)
    return _home_record(method_key, (updated.get("home") or {}).get(method_key))


def unit_adjustment(unit_key: str) -> dict[str, Any]:
    profile = feedback_profile(unit_key)
    unit = profile.get("unit") if isinstance(profile.get("unit"), dict) else {}
    penalty = float(unit.get("penalty") or 0.0)
    boost = float(unit.get("boost") or 0.0)
    return {
        "unit_key": _clean_key(unit_key),
        "penalty": penalty,
        "boost": boost,
        "score_adjustment": round(boost - penalty, 2),
        "up_count": int(unit.get("up_count") or 0),
        "down_count": int(unit.get("down_count") or 0),
        "last_rating": unit.get("last_rating") or "",
        "updated_at": unit.get("updated_at") or "",
    }


def tool_score_adjustment(tool: dict[str, Any] | str) -> dict[str, Any]:
    name = tool if isinstance(tool, str) else (tool or {}).get("name")
    return unit_adjustment(str(name or ""))


def node_penalty(unit_key: str, node_id: str) -> dict[str, Any]:
    key = _clean_key(unit_key)
    node_key = _clean_key(node_id)
    profile = feedback_profile(key)
    node = (profile.get("nodes") or {}).get(node_key)
    if not isinstance(node, dict):
        node = _node_record(key, node_key)
    return {
        "unit_key": key,
        "node_id": node_key,
        "penalty": float(node.get("penalty") or 0.0),
        "up_count": int(node.get("up_count") or 0),
        "down_count": int(node.get("down_count") or 0),
        "last_rating": node.get("last_rating") or "",
        "updated_at": node.get("updated_at") or "",
    }


def annotate_trace(unit_key: str, trace: Any) -> list[dict[str, Any]]:
    rows = trace if isinstance(trace, list) else []
    annotated: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        copied = deepcopy(row)
        copied["feedback_penalty"] = node_penalty(unit_key, copied.get("node_id") or "")
        annotated.append(copied)
    return annotated


def annotate_graph(unit_key: str, graph: Any) -> dict[str, Any]:
    copied = deepcopy(graph) if isinstance(graph, dict) else {}
    nodes = copied.get("nodes")
    if isinstance(nodes, list):
        copied["nodes"] = [
            {**node, "feedback_penalty": node_penalty(unit_key, node.get("id") or "")}
            if isinstance(node, dict)
            else node
            for node in nodes
        ]
    copied["feedback_profile"] = feedback_profile(unit_key)
    return copied


def annotate_result(unit_key: str, result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict):
        return result
    copied = dict(result)
    copied["trace"] = annotate_trace(unit_key, copied.get("trace"))
    copied["graph"] = annotate_graph(unit_key, copied.get("graph") or {})
    copied["feedback_profile"] = feedback_profile(unit_key)
    return copied
