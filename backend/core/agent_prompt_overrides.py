"""Disk-backed Unit AI persona/prompt/cache overrides."""
from __future__ import annotations

import datetime as _dt
from copy import deepcopy
from typing import Any

from core.paths import PATHS
from core.utils import load_json, save_json


OVERRIDES_FILE = PATHS.data_root / "agent_unit_overrides.json"


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _clean_text(value: Any, limit: int = 12000) -> str:
    return str(value or "").replace("\x00", " ").strip()[: max(1, limit)]


def _clean_unit_key(value: Any) -> str:
    return _clean_text(value, 120).replace("/", "_")


def load_all() -> dict[str, Any]:
    data = load_json(OVERRIDES_FILE, {})
    return data if isinstance(data, dict) else {}


def load_unit(unit_key: str) -> dict[str, Any]:
    data = load_all()
    unit = data.get(_clean_unit_key(unit_key))
    return deepcopy(unit) if isinstance(unit, dict) else {"nodes": {}}


def save_unit(unit_key: str, payload: dict[str, Any], *, by: str = "") -> dict[str, Any]:
    key = _clean_unit_key(unit_key)
    if not key:
        raise ValueError("unit_key is required")
    nodes_in = payload.get("nodes") if isinstance(payload, dict) else {}
    if not isinstance(nodes_in, dict):
        nodes_in = {}
    nodes: dict[str, Any] = {}
    for node_id, raw in nodes_in.items():
        node_key = _clean_text(node_id, 120)
        if not node_key or not isinstance(raw, dict):
            continue
        clean = {
            "persona": _clean_text(raw.get("persona"), 4000),
            "prompt_system": _clean_text(raw.get("prompt_system"), 12000),
            "cache": _clean_text(raw.get("cache"), 12000),
        }
        nodes[node_key] = {k: v for k, v in clean.items() if v}
    data = load_all()
    unit = {
        "nodes": nodes,
        "updated_at": _now_iso(),
        "updated_by": _clean_text(by, 120),
    }
    data[key] = unit
    OVERRIDES_FILE.parent.mkdir(parents=True, exist_ok=True)
    save_json(OVERRIDES_FILE, data, indent=2)
    return deepcopy(unit)


def node_override(unit_key: str, node_id: str) -> dict[str, Any]:
    unit = load_unit(unit_key)
    nodes = unit.get("nodes") if isinstance(unit.get("nodes"), dict) else {}
    node = nodes.get(str(node_id or ""))
    return deepcopy(node) if isinstance(node, dict) else {}


def prompt_system(unit_key: str, node_id: str, default: str) -> str:
    override = node_override(unit_key, node_id)
    text = _clean_text(override.get("prompt_system"), 12000)
    return text or default


def persona(unit_key: str, node_id: str, default: str) -> str:
    override = node_override(unit_key, node_id)
    text = _clean_text(override.get("persona"), 4000)
    return text or default


def cache(unit_key: str, node_id: str) -> str:
    override = node_override(unit_key, node_id)
    return _clean_text(override.get("cache"), 12000)
