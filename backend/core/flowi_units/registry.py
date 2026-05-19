"""Declarative registry of 11 Flow-i Unit AIs.

Order matches the 11 feature markdown files in
`data/flow-data/flowi_agent_features/`. Each unit AI's key matches the
corresponding md filename (`filebrowser.md` -> key `filebrowser`).

M1 registers metadata only — `handle()` returns None and the existing
`_run_flowi_chat` if/elif chain runs unchanged.
"""
from __future__ import annotations

from pathlib import Path

from core.flowi_units import schema_columns as col
from core.flowi_units.base import (
    BaseUnitAI,
    CodeRef,
    ColumnDoc,
    DataSourceRef,
    SemanticBindings,
    UnitAI,
)
from core.flowi_units.dashboard import DashboardUnitAI
from core.flowi_units.filebrowser import FileBrowserUnitAI
from core.flowi_units.inform import InformUnitAI
from core.flowi_units.meeting import MeetingUnitAI
from core.flowi_units.tracker import TrackerUnitAI

_BACKEND_CORE_DIR = Path(__file__).resolve().parent.parent


class SplitTableUnitAI(BaseUnitAI):
    KEY = "splittable"
    TITLE = "SplitTable AI"
    DATA_SOURCES = (
        DataSourceRef(
            kind="ml_table",
            path="data/Fab/ML_TABLE_<product>.parquet",
            description="제품별 ML_TABLE — SplitTable view의 base. KNOB_*, MGMT_*, TAG_* 컬럼을 보유.",
            columns=(col.PRODUCT, col.ROOT_LOT, col.LOT, col.WAFER, col.LOT_WF),
        ),
        DataSourceRef(
            kind="runtime_data",
            path="data/flow-data/splittable/management_rows.json",
            description="SplitTable의 MGMT_* 관리 행 overlay (runtime-only). 원본 ML_TABLE 미변경.",
        ),
        DataSourceRef(
            kind="runtime_data",
            path="data/flow-data/splittable/custom_tags.json",
            description="TAG_* 사용자 꼬리표 overlay (runtime-only).",
        ),
    )
    HANDLER_ENTRY = CodeRef(
        module="backend.routers.llm",
        function="(M2 위임 예정)",
        description="SplitTable view 조회 / KNOB prefix 필터",
    )


class TableMapUnitAI(BaseUnitAI):
    KEY = "tablemap"
    TITLE = "TableMap AI"
    HANDLER_ENTRY = CodeRef(
        module="backend.routers.llm",
        function="(M2 위임 예정)",
        description="TableMap 검색 / 매핑 조회",
    )


class DiagnosisUnitAI(BaseUnitAI):
    KEY = "diagnosis"
    TITLE = "Diagnosis AI (RAG)"
    HANDLER_ENTRY = CodeRef(
        module="backend.routers.llm",
        function="(M2 위임 예정)",
        description="RAG 지식 검색 / RCA",
    )


class CalendarUnitAI(BaseUnitAI):
    KEY = "calendar"
    TITLE = "Calendar AI"
    HANDLER_ENTRY = CodeRef(
        module="backend.routers.llm",
        function="(M2 위임 예정)",
        description="일정 / 회의 캘린더 조회",
    )


class EttimeUnitAI(BaseUnitAI):
    KEY = "ettime"
    TITLE = "ET Time AI"
    HANDLER_ENTRY = CodeRef(
        module="backend.routers.llm",
        function="(M2 위임 예정)",
        description="ET TIME 조회",
    )


class WaferLayoutUnitAI(BaseUnitAI):
    KEY = "waferlayout"
    TITLE = "Wafer Layout AI"
    HANDLER_ENTRY = CodeRef(
        module="backend.routers.llm",
        function="(M2 위임 예정)",
        description="Wafer Layout / wafer map 조회",
    )


_UNIT_AI_CLASSES = (
    FileBrowserUnitAI,
    MeetingUnitAI,
    InformUnitAI,
    TrackerUnitAI,
    DashboardUnitAI,
    SplitTableUnitAI,
    TableMapUnitAI,
    DiagnosisUnitAI,
    CalendarUnitAI,
    EttimeUnitAI,
    WaferLayoutUnitAI,
)


UNIT_AIS: dict[str, UnitAI] = {cls.KEY: cls() for cls in _UNIT_AI_CLASSES}


def get_unit_ai(key: str) -> UnitAI | None:
    return UNIT_AIS.get(key)


def all_unit_ais() -> list[UnitAI]:
    return list(UNIT_AIS.values())
