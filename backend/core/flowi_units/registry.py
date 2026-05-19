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
from core.flowi_units.filebrowser import FileBrowserUnitAI

_BACKEND_CORE_DIR = Path(__file__).resolve().parent.parent


class MeetingUnitAI(BaseUnitAI):
    KEY = "meeting"
    TITLE = "회의 관리 AI"
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
    HANDLER_ENTRY = CodeRef(
        module="backend.routers.meetings",
        function="_meeting_ask_llm_answer",
        lineno=1959,
        description="회의 prompt → LLM 답변 + knowledge attach",
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
    HANDLER_ENTRY = CodeRef(
        module="backend.routers.llm",
        function="_flowi_save_inform_draft",
        lineno=16718,
        description="Inform draft 생성 / 저장",
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
    HANDLER_ENTRY = CodeRef(
        module="backend.routers.llm",
        function="(M2 위임 예정)",
        description="Tracker 이슈 LOT purpose 조회",
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
    HANDLER_ENTRY = CodeRef(
        module="backend.routers.llm",
        function="(M2 위임 예정)",
        description="Dashboard 카드 / chart session",
    )


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
