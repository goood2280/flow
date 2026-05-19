"""Inform Log Unit AI — M2 PR #4.

Delegates to _handle_flowi_inform_summary in backend/routers/llm.py.
The handler self-checks the prompt and returns {"handled": False} when
the prompt isn't an inform-related request.
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


class InformUnitAI(BaseUnitAI):
    KEY = "inform"
    TITLE = "Inform Log AI"
    DATA_SOURCES = (
        DataSourceRef(
            kind="runtime_data",
            path="data/flow-data/flowi_inform_sessions/",
            description="Inform draft 세션 (TTL 3600s). 사용자가 인폼 작성 중인 임시 상태.",
            columns=(
                ColumnDoc(name="session_id", meaning="draft session 식별자."),
                ColumnDoc(name="product", meaning="제품명.", sample_values=("PRODA", "PRODB")),
                ColumnDoc(name="lot_id", meaning="대상 LOT.", sample_values=("A1000A.3",)),
                ColumnDoc(name="module", meaning="인폼 대상 모듈명.", sample_values=("GATE", "STI")),
                ColumnDoc(name="note", meaning="인폼 본문."),
                ColumnDoc(name="recipients", meaning="인폼 수신자 목록."),
            ),
        ),
        DataSourceRef(
            kind="runtime_data",
            path="data/flow-data/informs/",
            description="확정된 인폼 로그. 모듈/제품/LOT 기준 조회 가능.",
        ),
    )
    SEMANTIC_BINDINGS = SemanticBindings()
    HANDLER_ENTRY = CodeRef(
        module="backend.routers.llm",
        function="_handle_flowi_inform_summary",
        lineno=11897,
        description="Inform 조회/요약 + draft 생성 라우팅",
    )

    def handle(
        self,
        prompt: str,
        slots: dict[str, Any],
        ctx: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        from routers.llm import _handle_flowi_inform_summary

        me = ctx.get("me") or {}
        allowed_keys = ctx.get("allowed_keys")
        try:
            max_rows = int(slots.get("max_rows") or 12)
        except (TypeError, ValueError):
            max_rows = 12

        result = _handle_flowi_inform_summary(prompt, me, max_rows, allowed_keys)
        return result if result.get("handled") else None
