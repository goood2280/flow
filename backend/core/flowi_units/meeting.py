"""Meeting Unit AI — M2 PR #3.

Delegates to existing _handle_meeting_recall in backend/routers/llm.py.
The handler already self-checks the prompt and returns
{"handled": False} when the prompt isn't a meeting recall.
"""
from __future__ import annotations

from typing import Any, Optional

from core.flowi_units.base import (
    BaseUnitAI,
    CodeRef,
    ColumnDoc,
    DataSourceRef,
    SemanticBindings,
)


class MeetingUnitAI(BaseUnitAI):
    KEY = "meeting"
    TITLE = "회의 관리 AI"
    DESCRIPTION = (
        "회의 차수/참석자/회의록/액션아이템/결정사항 recall. "
        "'지난주 회의 결정', '아젠다 어땠어' 같은 회의 검색 질의에 적합."
    )
    EXAMPLES = (
        {"prompt": "지난주 회의 결정사항", "max_rows": 8},
        {"prompt": "PROD A 관련 회의 아젠다 보여줘"},
    )
    DATA_SOURCES = (
        DataSourceRef(
            kind="runtime_data",
            path="data/flow-data/meetings/",
            description="회의 세션 메타 + 회의록 본문 (minutes). title/date/owner/attendees와 본문 markdown.",
            columns=(
                ColumnDoc(name="meeting_id", meaning="회의 식별자."),
                ColumnDoc(name="title", meaning="회의 제목."),
                ColumnDoc(name="session", meaning="회의 차수 (1차, 2차 ...)."),
                ColumnDoc(name="attendees", meaning="참석자 username 목록."),
                ColumnDoc(name="minutes", meaning="회의록 본문 markdown."),
            ),
        ),
        DataSourceRef(
            kind="runtime_data",
            path="data/flow-data/knowledge/",
            description="회의 답변 보강에 사용하는 wiki/schema_doc/agent_wiki knowledge 인덱스.",
        ),
    )
    SEMANTIC_BINDINGS = SemanticBindings(
        wiki_doc_ids=(),  # meeting 자체는 schema_doc 의존성이 적음
    )
    HANDLER_ENTRY = CodeRef(
        module="backend.routers.llm",
        function="_handle_meeting_recall",
        lineno=9804,
        description="회의 recall — 차수/시간/액션/결정/아젠다/이슈 등 질의 처리",
    )

    def handle(
        self,
        prompt: str,
        slots: dict[str, Any],
        ctx: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        from routers.llm import _handle_meeting_recall

        me = ctx.get("me") or {}
        try:
            max_rows = int(slots.get("max_rows") or 12)
        except (TypeError, ValueError):
            max_rows = 12
        agent_context = ctx.get("agent_context") if isinstance(ctx.get("agent_context"), dict) else None

        result = _handle_meeting_recall(prompt, max_rows, me, agent_context)
        return result if result.get("handled") else None
