"""Dashboard Agent Unit AI metadata."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from core.flowi_units.base import BaseUnitAI, CodeRef, DataSourceRef


class DashboardAgentUnitAI(BaseUnitAI):
    KEY = "dashboard_agent"
    TITLE = "Dashboard Agent"
    DESCRIPTION = (
        "natural_language, columns, sample_rows 입력을 받아 Plotly chart_result를 만들고, "
        "Home Agent가 source/chart 요청으로 선택하면 FileBrowser AI SQL과 confirmed JOIN "
        "source orchestration을 내부적으로 사용한다."
    )
    LLM_PROFILE = "chart_type_select + params_fill 두 LLM 노드, LLM 미설정 시 deterministic fallback"
    DATA_SOURCES = (
        DataSourceRef(
            kind="request_payload",
            path="{natural_language, columns, sample_rows, root, product, file}",
            description="호출자가 제공한 schema/샘플 행 또는 Home Agent가 넘긴 source 힌트.",
        ),
        DataSourceRef(
            kind="filebrowser_source",
            path="FLOW_DB_ROOT/FLOW_BASE_ROOT read-only source via internal FileBrowser AI SQL",
            description="Home Agent source/chart 요청에서만 내부 source resolver와 JOIN runtime이 읽기 전용으로 사용한다.",
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
            "root": {"type": "string"},
            "product": {"type": "string"},
            "file": {"type": "string"},
            "max_rows": {"type": "integer", "minimum": 1, "maximum": 100},
            "dtypes": {"type": "object"},
        },
        "required": ["natural_language"],
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
