"""Internal source orchestration metadata for Dashboard Agent."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from core.flowi_units.base import BaseUnitAI, CodeRef, DataSourceRef


class HomeSqlJoinDashboardUnitAI(BaseUnitAI):
    KEY = "home_sql_join_dashboard"
    TITLE = "Dashboard Agent Source Orchestration (internal)"
    DESCRIPTION = (
        "Dashboard Agent가 Home Agent에서 source/chart 요청으로 선택됐을 때 "
        "FileBrowser AI SQL로 기준 소스의 WHERE/SELECT를 만들고, "
        "schema_relations에 등록된 confirmed relation으로 다른 파일/DB와 JOIN한 뒤, "
        "사용자 의도에 따라 raw 결과 또는 Dashboard 차트 초안을 제공하는 내부 runtime."
    )
    LLM_PROFILE = "filebrowser_sql_draft(서브그래프) + output_route + dashboard_draft(delegate)"
    DATA_SOURCES = (
        DataSourceRef(
            kind="db_product",
            path="FLOW_DB_ROOT/<root>/<product>/**/*.parquet",
            description="FileBrowser DB root/product 기준 소스(읽기 전용).",
        ),
        DataSourceRef(
            kind="base_file",
            path="FLOW_DB_ROOT 또는 FLOW_DATA_ROOT 단일 parquet/csv",
            description="FileBrowser 단일 파일 기준 소스(읽기 전용).",
        ),
        DataSourceRef(
            kind="schema_relations",
            path="FLOW_DATA_ROOT/schema_relations.json",
            description="JOIN 후보 산출과 confirmed relation 평가에 사용.",
        ),
        DataSourceRef(
            kind="db_root_files",
            path="FLOW_DB_ROOT/**/*.{parquet,csv}",
            description="JOIN 대상이 될 수 있는 db_root 내 다른 파일/디렉터리.",
        ),
        DataSourceRef(
            kind="base_root_files",
            path="FLOW_BASE_ROOT/**/*.{parquet,csv}",
            description="JOIN 대상이 될 수 있는 base_root 내 파일.",
        ),
    )
    HANDLER_ENTRY = CodeRef(
        module="backend.core.flowi_units.home_sql_join_dashboard_runtime",
        function="run_home_sql_join_dashboard_runtime",
        description=(
            "semantic_layer -> source_resolve -> filebrowser_sql_draft -> "
            "data_need_decision -> join_candidate_select -> join_plan_validate -> "
            "data_execute -> output_route -> dashboard_draft"
        ),
    )
    INPUT_SCHEMA = {
        "type": "object",
        "properties": {
            "natural_language": {"type": "string"},
            "root": {"type": "string"},
            "product": {"type": "string"},
            "file": {"type": "string"},
            "max_rows": {"type": "integer", "minimum": 1, "maximum": 100},
            "preferred_selected_columns": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["natural_language"],
    }
    OUTPUT_SCHEMA = {
        "type": "object",
        "properties": {
            "trace": {"type": "array"},
            "semantic_frame": {"type": "object"},
            "source_resolution": {"type": "object"},
            "ai_sql": {"type": "object"},
            "data_need": {"type": "object"},
            "join_plan": {"type": "object"},
            "joined": {"type": "object"},
            "output_route": {"type": "object"},
            "dashboard": {"type": "object"},
        },
        "required": ["trace", "joined", "output_route"],
    }
    EXAMPLES = (
        {
            "natural_language": "ML_TABLE 과 INLINE 조인해서 PRODA #3 의 CD 산점도 그려줘",
            "root": "ML_TABLE",
            "product": "PRODA",
        },
        {
            "natural_language": "ET 와 EDS 조인 결과를 표로 보여줘",
            "root": "ET",
            "product": "PRODB",
        },
    )

    def feature_md_path(self) -> Path:
        return Path("docs/features/home_sql_join_dashboard.md")

    def handle(
        self,
        prompt: str,
        slots: dict[str, Any],
        ctx: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        return None
