"""Shared Agent unit runtime execution helpers.

The unit runtimes keep their domain-specific node functions and output shapes.
This module owns the common node dispatch wrapper: timing, public trace row,
warning status, exception wrapping, and state-diff reduction.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable


NodeBody = Callable[[dict[str, Any], list[str]], dict[str, Any] | None]
InputSummary = Callable[[dict[str, Any]], dict[str, Any]]
TraceOutput = Callable[[str, dict[str, Any], dict[str, Any]], Any]


def node_status(warnings: list[str], failed: bool = False) -> str:
    if failed:
        return "failed"
    return "warning" if warnings else "success"


@dataclass
class TraceRecorder:
    label_for: Callable[[str], str] | None = None

    def row(
        self,
        *,
        node_id: str,
        status: str,
        input_summary: dict[str, Any],
        output: Any,
        warnings: list[str],
        started: float,
    ) -> dict[str, Any]:
        label = self.label_for(node_id) if self.label_for else node_id
        return {
            "node_id": node_id,
            "label": label,
            "status": status,
            "input_summary": input_summary,
            "output": output,
            "warnings": warnings,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }


class StateReducer:
    @staticmethod
    def merge_diff(state: dict[str, Any], diff: dict[str, Any]) -> dict[str, Any]:
        for key, value in (diff or {}).items():
            if key in {"trace", "runtime_warnings"}:
                existing = state.get(key) or []
                state[key] = list(existing) + list(value or [])
            else:
                state[key] = value
        return state


@dataclass
class NodeExecutor:
    trace_output: TraceOutput
    trace_recorder: TraceRecorder

    def execute(
        self,
        state: dict[str, Any],
        node_id: str,
        body: NodeBody,
        input_summary: InputSummary,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        warnings: list[str] = []
        failed = False
        result: dict[str, Any] = {}
        diff: dict[str, Any] = {}
        try:
            result = body(state, warnings) or {}
            diff.update(result)
        except Exception as exc:
            failed = True
            warnings.append(f"{type(exc).__name__}: {exc}")
            diff["node_errors"] = {**(state.get("node_errors") or {}), node_id: warnings[-1]}
        status = node_status(warnings, failed)
        merged_view = {**state, **diff}
        diff["trace"] = [
            self.trace_recorder.row(
                node_id=node_id,
                status=status,
                input_summary=input_summary(merged_view),
                output=self.trace_output(node_id, merged_view, result),
                warnings=warnings,
                started=started,
            )
        ]
        return diff


def run_sequential(
    state: dict[str, Any],
    node_runners: list[tuple[str, NodeBody, InputSummary]] | tuple[tuple[str, NodeBody, InputSummary], ...],
    executor: NodeExecutor,
    *,
    fallback_warning: str = "LangGraph is not installed; sequential fallback runner used.",
) -> dict[str, Any]:
    state.setdefault("runtime_warnings", []).append(fallback_warning)
    for node_id, body, input_summary in node_runners:
        diff = executor.execute(state, node_id, body, input_summary)
        StateReducer.merge_diff(state, diff)
    return state
