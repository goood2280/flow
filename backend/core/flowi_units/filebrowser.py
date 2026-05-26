"""FileBrowser AI SQL Unit AI metadata for the Agent rebuild slice."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from core.flowi_units.base import BaseUnitAI, CodeRef, DataSourceRef

_BACKEND_CORE_DIR = Path(__file__).resolve().parent.parent


class FileBrowserAiSqlUnitAI(BaseUnitAI):
    KEY = "filebrowser_ai_sql"
    TITLE = "FileBrowser AI SQL"
    DESCRIPTION = (
        "FileBrowser가 소유한 자연어 SQL/filter draft를 Agent 탭에서 "
        "LangGraph 실행 흐름으로 테스트하고 가시화한다."
    )
    PROMPT_TEMPLATE_PATH = _BACKEND_CORE_DIR / "filebrowser_agent_prompts.default.json"
    LLM_PROFILE = "filter_draft와 column_draft를 별도 LLM 호출로 실행"
    DATA_SOURCES = (
        DataSourceRef(
            kind="db_product",
            path="FLOW_DB_ROOT/<root>/<product>/**/*.parquet",
            description="FileBrowser DB root/product read-only preview source.",
        ),
        DataSourceRef(
            kind="root_parquet",
            path="FLOW_DB_ROOT/<file>",
            description="DB root 단일 parquet/csv read-only preview source.",
        ),
        DataSourceRef(
            kind="base_file",
            path="FLOW_DATA_ROOT 또는 FLOW_DB_ROOT 단일 파일",
            description="FileBrowser Base file read-only preview source.",
        ),
    )
    HANDLER_ENTRY = CodeRef(
        module="backend.core.flowi_units.filebrowser_ai_sql_runtime",
        function="run_filebrowser_ai_sql_runtime",
        description="context_sample -> semantic_layer -> filter_draft -> column_draft -> merge -> preview_apply",
    )
    INPUT_SCHEMA = {
        "type": "object",
        "properties": {
            "natural_language": {"type": "string"},
            "scope": {"type": "string", "enum": ["db_product", "rootpq", "base"]},
            "root": {"type": "string"},
            "product": {"type": "string"},
            "file": {"type": "string"},
        },
        "required": ["natural_language"],
    }
    OUTPUT_SCHEMA = {
        "type": "object",
        "properties": {
            "trace": {"type": "array"},
            "semantic_frame": {"type": "object"},
            "filter": {"type": "object"},
            "columns": {"type": "object"},
            "preview": {"type": "object"},
        },
        "required": ["trace", "preview"],
    }
    EXAMPLES = (
        {
            "natural_language": "A1000 #3 IOFF만 보고싶어",
            "scope": "db_product",
            "root": "ML_TABLE",
            "product": "PRODA",
        },
        {
            "natural_language": "value가 0.15보다 큰 행에서 lot, wafer, value만 보여줘",
            "scope": "base",
            "file": "ML_TABLE_PRODA.parquet",
        },
    )

    def feature_md_path(self) -> Path:
        return Path("docs/features/filebrowser.md")

    def handle(
        self,
        prompt: str,
        slots: dict[str, Any],
        ctx: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        return None
