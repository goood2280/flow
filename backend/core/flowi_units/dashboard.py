"""Dashboard Unit AI — M2 PR #6.

Delegates to two existing handlers chained in legacy _handle_flowi_query_core:
- _handle_dashboard_chart_raw_data_followup — raw CSV download follow-up
- _handle_dashboard_chart_context_followup → _augment_dashboard_tool — chart
  session follow-up (KNOB coloring, fit line, value exclude, etc.)
Both handlers self-check and return {"handled": False} when not theirs.
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


class DashboardUnitAI(BaseUnitAI):
    KEY = "dashboard"
    TITLE = "Dashboard AI"
    DATA_SOURCES = (
        DataSourceRef(
            kind="runtime_data",
            path="data/flow-data/dashboards/",
            description="대시보드 카드 정의와 chart session.",
            columns=(
                ColumnDoc(name="card_id", meaning="대시보드 카드 식별자."),
                ColumnDoc(name="chart_session_id", meaning="차트 한 번의 컨텍스트. 후속 요청에서 재사용."),
                ColumnDoc(name="metric", meaning="차트 metric 컬럼."),
                ColumnDoc(name="group_by", meaning="그룹 분할 컬럼 (예: lot_wf, KNOB 값)."),
            ),
        ),
    )
    SEMANTIC_BINDINGS = SemanticBindings()
    HANDLER_ENTRY = CodeRef(
        module="backend.routers.llm",
        function="_handle_dashboard_chart_raw_data_followup / _handle_dashboard_chart_context_followup",
        lineno=4655,
        description="raw CSV 다운로드 + chart session 후속 요청 라우팅",
    )

    def handle(
        self,
        prompt: str,
        slots: dict[str, Any],
        ctx: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        from routers.llm import (
            _augment_dashboard_tool,
            _handle_dashboard_chart_context_followup,
            _handle_dashboard_chart_raw_data_followup,
        )

        product = str(slots.get("product") or "")
        try:
            max_rows = int(slots.get("max_rows") or 12)
        except (TypeError, ValueError):
            max_rows = 12
        agent_context = ctx.get("agent_context") if isinstance(ctx.get("agent_context"), dict) else None
        me = ctx.get("me") or {}
        username = str(me.get("username") or "flowi")
        role = str(me.get("role") or "user")

        raw_data_out = _handle_dashboard_chart_raw_data_followup(
            prompt,
            agent_context,
            max_rows,
            username=username,
            role=role,
        )
        if raw_data_out.get("handled"):
            return raw_data_out

        chart_context_out = _handle_dashboard_chart_context_followup(prompt, product, max_rows, agent_context)
        if chart_context_out.get("handled"):
            return _augment_dashboard_tool(chart_context_out, prompt, product=product, username=username)

        return None
