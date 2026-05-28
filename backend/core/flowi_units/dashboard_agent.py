"""Dashboard Agent Unit AI metadata."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from core.flowi_units.base import BaseUnitAI, CodeRef, DataSourceRef


class DashboardAgentUnitAI(BaseUnitAI):
    KEY = "dashboard_agent"
    TITLE = "Dashboard Agent"
    DESCRIPTION = (
        "source와 무관한 natural_language, columns, sample_rows 입력을 받아 "
        "공유 semantic layer와 Dashboard chart defaults 기반 Plotly chart_result를 만든다."
    )
    LLM_PROFILE = "chart_type_select + params_fill 두 LLM 노드, LLM 미설정 시 deterministic fallback"
    DATA_SOURCES = (
        DataSourceRef(
            kind="request_payload",
            path="{natural_language, columns, sample_rows}",
            description="호출자가 제공한 schema와 샘플 행. 원본 source는 직접 읽지 않는다.",
        ),
        DataSourceRef(
            kind="dashboard_defaults",
            path="FLOW_DATA_ROOT/dashboard_chart_defaults.json",
            description="Dashboard chart type별 기본 config override.",
        ),
        DataSourceRef(
            kind="prompt_overrides",
            path="FLOW_DATA_ROOT/agent_unit_overrides.json",
            description="Unit AI card에서 저장한 persona/prompt/cache override.",
        ),
    )
    HANDLER_ENTRY = CodeRef(
        module="backend.core.flowi_units.dashboard_agent_runtime",
        function="run_dashboard_agent_runtime",
        description="semantic_layer -> chart_intent -> chart_type_select -> params_fill -> spec_validate -> render_spec",
    )
    INPUT_SCHEMA = {
        "type": "object",
        "properties": {
            "natural_language": {"type": "string"},
            "columns": {"type": "array", "items": {"type": "string"}},
            "sample_rows": {"type": "array", "items": {"type": "object"}},
            "product": {"type": "string"},
            "dtypes": {"type": "object"},
        },
        "required": ["natural_language", "columns", "sample_rows"],
    }
    OUTPUT_SCHEMA = {
        "type": "object",
        "properties": {
            "chart_result": {"type": "object"},
            "config": {"type": "object"},
            "trace": {"type": "array"},
            "semantic_frame": {"type": "object"},
        },
        "required": ["chart_result", "trace"],
    }
    EXAMPLES = (
        {
            "natural_language": "wafer별 IOFF 분포 scatter로 그려줘",
            "columns": ["wafer_id", "IOFF", "lot_id"],
            "sample_rows": [{"wafer_id": 1, "IOFF": 0.12, "lot_id": "A1000"}],
        },
    )

    def feature_md_path(self) -> Path:
        return Path("docs/features/flowi-agent.md")

    def handle(
        self,
        prompt: str,
        slots: dict[str, Any],
        ctx: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        return None
