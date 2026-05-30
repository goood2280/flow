"""PPID Knob 분류 Unit AI — ppid_knob.csv 룰북 기반 결정적 분류.

홈 에이전트(Flow-i)가 "PPID_08_0는 어떤 knob으로 분류돼" 류 질문을 dispatcher 경로
(`try_dispatch(..., only=["ppid_knob"])`)에서 직접 처리한다. operator(eq) 규칙으로
value(ppid)를 category(knob/split 이름)로 분류. LLM 불필요.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from core.flowi_units.base import BaseUnitAI, CodeRef, DataSourceRef


def _ppid_tool_payload(result: dict[str, Any]) -> dict[str, Any]:
    matches = result.get("matches") or []
    columns = ["value", "category", "feature_name", "function_step", "rule_order"]
    rows = [{c: m.get(c, "") for c in columns} for m in matches]
    return {
        "handled": True,
        "type": "answer",
        "intent": "ppid_knob",
        "feature": "ppid_knob",
        "unit_ai": "ppid_knob",
        "action": "classify_ppid_knob",
        "answer": result.get("answer") or "",
        "table": (
            {"kind": "ppid_knob", "title": "PPID Knob 분류", "columns": columns, "rows": rows, "total": len(rows)}
            if rows
            else {}
        ),
    }


class PpidKnobUnitAI(BaseUnitAI):
    KEY = "ppid_knob"
    TITLE = "PPID Knob 분류"
    DESCRIPTION = (
        "ppid_knob.csv(feature_name, function_step, rule_order, operator, value, category) 룰북으로 "
        "ppid(value)를 operator(eq) 규칙에 따라 knob(split 이름 = category)으로 분류한다. "
        "예: 'PPID_08_0는 어떤 knob으로 분류돼'."
    )
    LLM_PROFILE = "deterministic rulebook lookup; no LLM"
    DATA_SOURCES = (
        DataSourceRef(
            kind="matching_csv",
            path="FLOW_DB_ROOT/ppid_knob.csv",
            description="feature_name, function_step, rule_order, operator, value, category 룰북 (matching_cache 경유).",
        ),
    )
    HANDLER_ENTRY = CodeRef(
        module="backend.core.fab_reference",
        function="classify_ppid_in_text",
        description="prompt 에서 ppid(value) 토큰을 찾아 operator(eq) 규칙으로 category 분류",
    )
    INPUT_SCHEMA = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string"},
            "product": {"type": "string"},
        },
        "required": ["prompt"],
    }
    OUTPUT_SCHEMA = {
        "type": "object",
        "properties": {
            "handled": {"type": "boolean"},
            "answer": {"type": "string"},
            "table": {"type": "object"},
        },
        "required": ["handled"],
    }
    EXAMPLES = (
        {"prompt": "PPID_08_0는 어떤 knob으로 분류돼"},
        {"prompt": "PPID_24_0 split 분류 알려줘"},
    )

    def feature_md_path(self) -> Path:
        return Path("docs/features/flowi-agent.md")

    def handle(
        self,
        prompt: str,
        slots: dict[str, Any],
        ctx: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        from core import fab_reference

        product = str((slots or {}).get("product") or "")
        result = fab_reference.classify_ppid_in_text(str(prompt or ""), product)
        if not result:
            return None
        return _ppid_tool_payload(result)
