"""FileBrowser Unit AI — M2 PR #2.

Delegates to existing handlers in backend/routers/llm.py. No reimplementation.
Each delegated handler already self-checks the prompt and returns
{"handled": False} when the prompt isn't for it, so this class just chains
the checks and returns the first match.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from core.flowi_units import schema_columns as col
from core.flowi_units.base import (
    BaseUnitAI,
    CodeRef,
    DataSourceRef,
    SemanticBindings,
)

_BACKEND_CORE_DIR = Path(__file__).resolve().parent.parent


class FileBrowserUnitAI(BaseUnitAI):
    KEY = "filebrowser"
    TITLE = "파일 탐색기 AI (SQL / 스키마 / 캐시)"
    PROMPT_TEMPLATE_PATH = _BACKEND_CORE_DIR / "filebrowser_agent_prompts.default.json"
    DATA_SOURCES = (
        DataSourceRef(
            kind="parquet",
            path="data/Fab/cache/lot_progress_latest_lot_by_root_wafer.parquet",
            description="LOT 진행 최신 캐시. 각 root_lot + wafer의 가장 최근 step과 fab_lot_id를 한 행으로 모은 파일.",
            columns=(col.PRODUCT, col.ROOT_LOT, col.LOT, col.FAB_LOT, col.WAFER, col.STEP, col.FUNCTION_STEP),
        ),
        DataSourceRef(
            kind="fab_db",
            path="data/Fab/1.RAWDATA_DB_FAB/<product>/",
            description="FAB raw DB root. 제품별 디렉토리에 step별 parquet 파티션이 존재. read-only preview만 허용.",
            columns=(col.PRODUCT, col.ROOT_LOT, col.LOT, col.WAFER, col.STEP),
        ),
        DataSourceRef(
            kind="ml_table",
            path="data/Fab/ML_TABLE_*.parquet",
            description="제품별 ML_TABLE — root_lot/wafer 기준의 KNOB/feature 값 모음. lot_wf join의 right side.",
            columns=(col.ROOT_LOT, col.LOT, col.WAFER, col.LOT_WF),
        ),
        DataSourceRef(
            kind="runtime_data",
            path="data/flow-data/cache/filebrowser/",
            description="FileBrowser preview/query 결과 캐시 (hash 기반). UI 응답 가속용.",
        ),
    )
    SEMANTIC_BINDINGS = SemanticBindings(
        column_catalog_keys=(
            "product",
            "root_lot_id",
            "lot_id",
            "fab_lot_id",
            "wafer_id",
            "step_id",
            "function_step",
            "lot_wf",
        ),
        wiki_doc_ids=(
            "ml_table_proda.root_lot_id",
            "ml_table_proda.wafer_id",
            "ml_table_proda.step_id",
            "ml_table_proda.lot_wf",
        ),
    )
    HANDLER_ENTRY = CodeRef(
        module="backend.core.flowi_units.filebrowser",
        function="FileBrowserUnitAI.handle",
        description="현재 fab_lot 조회 / 현재 step 조회 narrow case 위임",
    )

    def handle(
        self,
        prompt: str,
        slots: dict[str, Any],
        ctx: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        # Lazy import: routers.llm imports core.* extensively at module load,
        # so importing it here avoids a circular dependency.
        from routers.llm import (
            _handle_current_fab_lot_lookup,
            _handle_current_step_from_progress_cache,
        )

        product = str(slots.get("product") or "")
        try:
            max_rows = int(slots.get("max_rows") or 12)
        except (TypeError, ValueError):
            max_rows = 12

        result = _handle_current_fab_lot_lookup(prompt, product, max_rows)
        if result.get("handled"):
            return result

        result = _handle_current_step_from_progress_cache(prompt, product, max_rows)
        if result.get("handled"):
            return result

        return None
