"""Prompt override loader for Agent unit runtimes."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from core import agent_prompt_overrides


class PromptLoader:
    """Small wrapper around ``agent_prompt_overrides.json``.

    Unit runtimes can keep static node metadata in code and ask this loader for
    the effective persona/system/cache text when they are ready to consume
    runtime overrides.
    """

    def __init__(self, unit_key: str):
        self.unit_key = str(unit_key or "")

    def load(self) -> dict[str, Any]:
        return agent_prompt_overrides.load_unit(self.unit_key)

    def node(self, node_id: str, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
        base = deepcopy(fallback or {})
        overrides = self.load().get("nodes") or {}
        node_override = overrides.get(str(node_id or "")) if isinstance(overrides, dict) else None
        if isinstance(node_override, dict):
            base.update({k: v for k, v in node_override.items() if v is not None})
        return base
