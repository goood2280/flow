"""MCP-style public tool-call contract for Home Agent orchestration."""
from __future__ import annotations

from typing import Any, Literal, TypedDict


class ToolCall(TypedDict, total=False):
    tool: str
    input: dict[str, Any]
    output: dict[str, Any]
    status: Literal["success", "warning", "failed", "blocked"]
    sub_trace: list[dict[str, Any]]
    warnings: list[str]
