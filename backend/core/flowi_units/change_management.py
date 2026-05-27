"""Change management Flow-i Unit AI metadata for the Agent unit tab."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from core.flowi_units.base import BaseUnitAI, CodeRef, DataSourceRef


class ChangeManagementUnitAI(BaseUnitAI):
    KEY = "change_management"
    TITLE = "변경점 관리 Flow-i"
    DESCRIPTION = (
        "회의/변경점 질문에서 대상 회의를 해석하고, visible 회의의 아젠다·회의록·결정사항·"
        "액션아이템·캘린더 이벤트만 근거로 plain text 답변을 만든다."
    )
    LLM_PROFILE = "visible meeting/calendar context + deterministic fallback; no markdown decoration"
    DATA_SOURCES = (
        DataSourceRef(
            kind="meetings",
            path="FLOW_DATA_ROOT/meetings/*.json",
            description="Visible meeting sessions, agendas, minutes, decisions, and action items.",
        ),
        DataSourceRef(
            kind="calendar",
            path="FLOW_DATA_ROOT/calendar/events.json",
            description="Visible change-management calendar events linked to meetings or manual entries.",
        ),
        DataSourceRef(
            kind="history",
            path="FLOW_DATA_ROOT/agent_unit_ai_sessions/change_management/history.jsonl",
            description="Agent unit run history with prompt, answer, sources, and public warnings.",
        ),
    )
    HANDLER_ENTRY = CodeRef(
        module="backend.core.flowi_units.change_management_runtime",
        function="run_change_management_runtime",
        description="context_scope -> meeting_reference -> evidence_pack -> answer_compose",
    )
    INPUT_SCHEMA = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string"},
            "meeting_id": {"type": "string"},
            "session_id": {"type": "string"},
        },
        "required": ["prompt"],
    }
    OUTPUT_SCHEMA = {
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
            "needs_clarification": {"type": "boolean"},
            "meeting": {"type": "object"},
            "meetings": {"type": "array"},
            "calendar_events": {"type": "array"},
            "sources": {"type": "array"},
            "trace": {"type": "array"},
        },
        "required": ["answer", "needs_clarification", "sources", "trace"],
    }
    EXAMPLES = (
        {"prompt": "Device Change Sync 회의 액션아이템과 결정사항 정리해줘"},
        {"prompt": "이번 변경점 관리에 연결된 회의 아젠다, 회의록, 액션아이템을 요약해줘"},
    )

    def feature_md_path(self) -> Path:
        return Path("docs/features/meeting-calendar.md")

    def prompt_template_path(self) -> Optional[Path]:
        return None

    def handle(
        self,
        prompt: str,
        slots: dict[str, Any],
        ctx: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        return None
