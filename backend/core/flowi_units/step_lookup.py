"""Step ID 매칭 Unit AI — step_matching.csv 기반 결정적 조회.

홈 에이전트(Flow-i)가 "SD_EPI의 step_id가 뭐야" / "AA100090는 무슨 step이야" 류 질문을
dispatcher 경로(`try_dispatch(..., only=["step_lookup"])`)에서 직접 처리한다. LLM 불필요.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from core.flowi_units.base import BaseUnitAI, CodeRef, DataSourceRef


def _step_tool_payload(result: dict[str, Any]) -> dict[str, Any]:
    matches = result.get("matches") or []
    columns = ["product", "step_id", "function_step"]
    rows = [{c: m.get(c, "") for c in columns} for m in matches]
    return {
        "handled": True,
        "type": "answer",
        "intent": "step_lookup",
        "feature": "step_lookup",
        "unit_ai": "step_lookup",
        "action": result.get("direction") or "lookup_step",
        "answer": result.get("answer") or "",
        "table": (
            {"kind": "step_matching", "title": "Step 매칭", "columns": columns, "rows": rows, "total": len(rows)}
            if rows
            else {}
        ),
    }


class StepLookupUnitAI(BaseUnitAI):
    KEY = "step_lookup"
    TITLE = "Step ID 매칭"
    DESCRIPTION = (
        "step_matching.csv(product, step_id, function_step) 단일 파일로 step_id ↔ function_step 을 "
        "양방향 조회한다. 예: 'SD_EPI의 step_id가 뭐야', 'AA100090는 무슨 step이야'."
    )
    LLM_PROFILE = "deterministic single-file lookup; no LLM"
    DATA_SOURCES = (
        DataSourceRef(
            kind="matching_csv",
            path="FLOW_DB_ROOT/step_matching.csv",
            description="product, step_id, function_step 매칭표 (matching_cache DuckDB 캐시 경유).",
        ),
    )
    HANDLER_ENTRY = CodeRef(
        module="backend.core.fab_reference",
        function="lookup_step_in_text",
        description="prompt 에서 step_id/function_step 토큰을 찾아 양방향 매칭",
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
        {"prompt": "SD_EPI의 step_id가 뭐야"},
        {"prompt": "AA100090는 무슨 step이야"},
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
        result = fab_reference.lookup_step_in_text(str(prompt or ""), product)
        if not result:
            return None
        return _step_tool_payload(result)
