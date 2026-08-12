"""Dispatcher for the small active Unit AI registry."""
from __future__ import annotations

from typing import Any, Iterable


_UNIT_ALIASES = {
    "filebrowser": "filebrowser_ai_sql",
}

_FEATURE_ALLOWED_UNIT_KEYS = {
    "step_lookup": {"filebrowser", "splittable", "dashboard"},
    "lot_wip": {"filebrowser", "splittable", "dashboard", "tracker"},
    "ppid_knob": {"filebrowser", "splittable"},
    "split_nav": {"splittable"},
}


def _candidate_keys(only: Iterable[str] | None, registered: set[str]) -> list[str]:
    raw_keys = [str(v).strip() for v in (only or []) if str(v).strip()]
    if not raw_keys:
        return sorted(registered)
    out: list[str] = []
    for key in raw_keys:
        mapped = _UNIT_ALIASES.get(key, key)
        if mapped in registered and mapped not in out:
            out.append(mapped)
    return out


def _allowed(key: str, allowed_keys: Iterable[str] | None) -> bool:
    if allowed_keys is None:
        return True
    allowed = {str(v).strip() for v in allowed_keys if str(v).strip()}
    if not allowed:
        return False
    aliases = {key}
    aliases.update(alias for alias, target in _UNIT_ALIASES.items() if target == key)
    aliases.update(_FEATURE_ALLOWED_UNIT_KEYS.get(key, set()))
    return bool(aliases & allowed)


def try_dispatch(
    prompt: str,
    product: str = "",
    max_rows: int = 12,
    only: Iterable[str] | None = None,
    allowed_keys: Iterable[str] | None = None,
    agent_context: dict[str, Any] | None = None,
    me: dict[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any] | None:
    from core.flowi_units import registry

    unit_ais = registry.UNIT_AIS
    slots = {
        "product": product,
        "max_rows": max_rows,
        **kwargs,
    }
    ctx = {
        "agent_context": agent_context or {},
        "me": me or {},
        "allowed_keys": sorted({str(v).strip() for v in (allowed_keys or []) if str(v).strip()}),
    }
    from core import flowi_progress

    for key in _candidate_keys(only, set(unit_ais)):
        if not _allowed(key, allowed_keys):
            continue
        unit = unit_ais[key]
        # 단위기능이 뜨는 지점은 여기 하나뿐이다 — 홈 챗 진행 표시가 붙는 자리.
        token = flowi_progress.step_start("단위기능", getattr(unit, "TITLE", "") or key, group=key)
        try:
            result = unit.handle(prompt, slots, ctx)
        except BaseException:
            flowi_progress.step_end(token, status="failed")
            raise
        if result is not None:
            flowi_progress.step_end(token, status="success")
            return result
        flowi_progress.step_end(token, status="skipped", detail="처리 대상 아님")
    return None
