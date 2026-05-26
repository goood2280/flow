"""Strangler-fig dispatcher for Flow-i Unit AIs.

In M1, all unit AI handle() returned None. M2 PRs progressively wire
real delegation per feature. This dispatcher is called from
backend/routers/llm.py inside _run_flowi_chat just before the legacy
_handle_flowi_query fallback so an early successful unit AI short-circuits
the legacy if/elif chain. Units that return None let the dispatcher try
the next, or fall through to legacy if none match.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from core.flowi_units.registry import UNIT_AIS


def try_dispatch(
    prompt: str,
    *,
    product: str = "",
    max_rows: int = 12,
    allowed_keys: set[str] | None = None,
    agent_context: dict[str, Any] | None = None,
    me: dict[str, Any] | None = None,
    only: Iterable[str] | None = None,
) -> Optional[dict[str, Any]]:
    """Try the listed unit AIs in order; return the first handled result.

    `only` restricts to a specific subset of unit AI keys. M2 PRs add
    keys here one at a time so unfinished units never accidentally
    activate. Returns None if no unit AI claims the prompt.

    Units that need user/auth context read `ctx["me"]` (e.g. meeting
    visibility). Units that need the raw agent_context read
    `ctx["agent_context"]`. Allowed keys are mirrored to
    `ctx["allowed_keys"]` for handlers that need to know what features
    the caller is permitted to use.
    """
    explicit_only = only is not None
    keys = list(only) if explicit_only else list(UNIT_AIS.keys())
    ctx: dict[str, Any] = {}
    if isinstance(agent_context, dict):
        ctx.update(agent_context)
        ctx["agent_context"] = agent_context
    ctx["allowed_keys"] = set(allowed_keys) if allowed_keys is not None else None
    if me is not None:
        ctx["me"] = me
    slots: dict[str, Any] = {"product": product, "max_rows": max_rows}

    for key in keys:
        unit = UNIT_AIS.get(key)
        if unit is None:
            continue
        # When `only` is explicit, the caller is responsible for permission
        # gating (e.g. mapping "calendar" → meeting AI). When `only` is the
        # default, fall back to the per-key allowed_keys check.
        if not explicit_only and allowed_keys is not None and key not in allowed_keys:
            continue
        try:
            result = unit.handle(prompt, slots, ctx)
        except Exception:
            # A failing unit AI must never crash _run_flowi_chat; fall
            # through to legacy. (Real errors should still surface in tests.)
            continue
        if result and result.get("handled"):
            result.setdefault("feature", key)
            return result
    return None
