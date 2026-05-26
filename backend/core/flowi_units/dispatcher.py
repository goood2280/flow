"""Fail-closed dispatcher for archived Unit AIs."""
from __future__ import annotations

from typing import Any, Iterable


def try_dispatch(
    prompt: str,
    product: str = "",
    max_rows: int = 12,
    only: Iterable[str] | None = None,
    agent_context: dict[str, Any] | None = None,
    me: dict[str, Any] | None = None,
) -> dict[str, Any]:
    selected = [str(v) for v in (only or []) if str(v).strip()]
    return {
        "handled": False,
        "feature": selected[0] if selected else "",
        "reason": "unit_ai_archived",
        "text": "Unit AI implementations are archived for rebuild.",
    }
