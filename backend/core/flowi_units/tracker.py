"""Tracker Unit AI — M2 PR #5.

Delegates to _handle_tracker_lot_purpose_lookup in backend/routers/llm.py.
The handler self-checks the prompt and returns {"handled": False} when
the prompt isn't a tracker LOT purpose lookup.
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


class TrackerUnitAI(BaseUnitAI):
    KEY = "tracker"
    TITLE = "Tracker AI (이슈 LOT 조회)"
    DATA_SOURCES = (
        DataSourceRef(
            kind="runtime_data",
            path="data/flow-data/tracker/",
            description="이슈 LOT 상태 캐시. 각 LOT의 issue category, purpose, owner를 추적.",
            columns=(
                ColumnDoc(name="lot_id", meaning="대상 LOT."),
                ColumnDoc(name="purpose", meaning="이 LOT을 왜 별도 관리하는지 (split 검증, retest 등)."),
                ColumnDoc(name="status", meaning="처리 상태."),
                ColumnDoc(name="owner", meaning="담당자."),
                ColumnDoc(name="updated_at", meaning="마지막 갱신 시각."),
            ),
        ),
    )
    SEMANTIC_BINDINGS = SemanticBindings()
    HANDLER_ENTRY = CodeRef(
        module="backend.routers.llm",
        function="_handle_tracker_lot_purpose_lookup",
        lineno=9169,
        description="Tracker 이슈 LOT의 purpose/issue 조회",
    )

    def handle(
        self,
        prompt: str,
        slots: dict[str, Any],
        ctx: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        from routers.llm import _handle_tracker_lot_purpose_lookup

        product = str(slots.get("product") or "")
        try:
            max_rows = int(slots.get("max_rows") or 12)
        except (TypeError, ValueError):
            max_rows = 12

        result = _handle_tracker_lot_purpose_lookup(prompt, product, max_rows)
        return result if result.get("handled") else None
