"""Remaining 6 Unit AIs bundled — M2 PR #7.

splittable / tablemap / ettime / diagnosis / calendar / waferlayout.
Lower-frequency or UI-dominant features that share registration with the
common metadata pattern. splittable and waferlayout get narrow handle()
delegations; the rest expose metadata only and rely on the legacy
_handle_flowi_query_core fallback (cleaned up in M6).
"""
from __future__ import annotations

from typing import Any, Optional

from core.flowi_units import schema_columns as col
from core.flowi_units.base import (
    BaseUnitAI,
    CodeRef,
    ColumnDoc,
    DataSourceRef,
    SemanticBindings,
)


class SplitTableUnitAI(BaseUnitAI):
    KEY = "splittable"
    TITLE = "SplitTable AI"
    DESCRIPTION = (
        "ML_TABLE 기반 KNOB / MASK / split 정보 조회. "
        "'A1000 LOT의 KNOB_GATE 값', 'X1234 wafer split' 같은 질의에 적합."
    )
    EXAMPLES = (
        {"prompt": "A1000A.3 KNOB_GATE_CD 값", "product": "PRODA"},
        {"prompt": "split 검증 LOT KNOB 비교"},
    )
    DATA_SOURCES = (
        DataSourceRef(
            kind="ml_table",
            path="data/Fab/ML_TABLE_<product>.parquet",
            description="제품별 ML_TABLE — SplitTable view의 base. KNOB_*, MGMT_*, TAG_* 컬럼 보유.",
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
    SEMANTIC_BINDINGS = SemanticBindings(
        column_catalog_keys=("product", "root_lot_id", "lot_id", "wafer_id", "lot_wf"),
    )
    HANDLER_ENTRY = CodeRef(
        module="backend.routers.llm",
        function="_handle_knob_query",
        lineno=14732,
        description="KNOB 조회 — split table에서 KNOB 값과 적용공정 정보 라우팅",
    )

    def handle(
        self,
        prompt: str,
        slots: dict[str, Any],
        ctx: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        from routers.llm import (
            _flowi_explicit_splittable_view_prompt,
            _flowi_knob_table_lookup_intent,
            _handle_knob_query,
        )

        product = str(slots.get("product") or "")
        try:
            max_rows = int(slots.get("max_rows") or 12)
        except (TypeError, ValueError):
            max_rows = 12

        if not _flowi_knob_table_lookup_intent(prompt):
            return None
        if _flowi_explicit_splittable_view_prompt(prompt):
            return None

        result = _handle_knob_query(prompt, product, max_rows)
        return result if result.get("handled") else None


class TableMapUnitAI(BaseUnitAI):
    KEY = "tablemap"
    TITLE = "TableMap AI"
    DESCRIPTION = (
        "DB 테이블 관계 그래프 / join path 안내. "
        "'tablemap 보여줘', 'ET와 ML_TABLE 어떻게 join해' 같은 질의에 적합."
    )
    EXAMPLES = (
        {"prompt": "tablemap 보여줘"},
        {"prompt": "ET PRODA와 ML_TABLE_PRODA join 경로"},
    )
    DATA_SOURCES = (
        DataSourceRef(
            kind="duckdb",
            path="/api/dbmap/tables",
            description="DB 테이블 메타 매핑. 테이블↔파일 연결 정보를 보유.",
            columns=(
                ColumnDoc(name="table_id", meaning="테이블 식별자."),
                ColumnDoc(name="source_path", meaning="실제 데이터 파일 경로."),
                ColumnDoc(name="schema_label", meaning="schema 라벨 (ET PRODA, ML_TABLE_PRODA 등)."),
            ),
        ),
    )
    SEMANTIC_BINDINGS = SemanticBindings()
    HANDLER_ENTRY = CodeRef(
        module="backend.routers.llm",
        function="_unit_feature_guidance",
        lineno=2586,
        description="TableMap 진입/가이드 — prompt에 'tablemap/테이블맵' 포함 시 라우팅",
    )

    def handle(
        self,
        prompt: str,
        slots: dict[str, Any],
        ctx: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        from routers.llm import _unit_feature_guidance

        text = str(prompt or "")
        low = text.lower()
        if not any(t in low or t in text for t in ("테이블맵", "테이블 맵", "tablemap", "table map")):
            return None

        product = str(slots.get("product") or "")
        try:
            max_rows = int(slots.get("max_rows") or 12)
        except (TypeError, ValueError):
            max_rows = 12
        allowed_keys = ctx.get("allowed_keys")
        scoped = {"tablemap"} if (allowed_keys is None or "tablemap" in allowed_keys) else set()

        result = _unit_feature_guidance(prompt, product, max_rows=max_rows, allowed_keys=scoped)
        return result if result.get("handled") else None


class EttimeUnitAI(BaseUnitAI):
    KEY = "ettime"
    TITLE = "ET Time AI"
    DESCRIPTION = (
        "ET 단계 tkout_time / elapsed / median 집계. "
        "'PRODA ET 평균 시간', 'A1000 ET 추이' 같은 시간 metric 질의에 적합."
    )
    EXAMPLES = (
        {"prompt": "PRODA root_lot 별 ET 평균", "product": "PRODA"},
        {"prompt": "A1000A.3 ET elapsed"},
    )
    DATA_SOURCES = (
        DataSourceRef(
            kind="parquet",
            path="data/Fab/1.RAWDATA_DB_ET_*/",
            description="ET 단계 raw DB. tkout_time / process_id / step별 metric.",
            columns=(
                col.PRODUCT, col.ROOT_LOT, col.LOT, col.WAFER, col.STEP,
                ColumnDoc(name="tkout_time", meaning="ET 단계 처리 완료 시각 (timestamp).", unit="datetime"),
                ColumnDoc(name="process_id", meaning="공정 process id."),
            ),
        ),
    )
    SEMANTIC_BINDINGS = SemanticBindings(
        relation_ids=(),
        column_catalog_keys=("product", "root_lot_id", "wafer_id", "tkout_time", "process_id"),
    )
    HANDLER_ENTRY = CodeRef(
        module="backend.routers.llm",
        function="_handle_et_query",
        lineno=14544,
        description="ET 단계 metric/trend (tkout_time/elapsed/median) 라우팅",
    )

    def handle(
        self,
        prompt: str,
        slots: dict[str, Any],
        ctx: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        from routers.llm import _handle_et_query

        product = str(slots.get("product") or "")
        try:
            max_rows = int(slots.get("max_rows") or 12)
        except (TypeError, ValueError):
            max_rows = 12

        result = _handle_et_query(prompt, product, max_rows)
        return result if result.get("handled") else None


class DiagnosisUnitAI(BaseUnitAI):
    KEY = "diagnosis"
    TITLE = "Diagnosis AI (RAG / RCA)"
    DESCRIPTION = (
        "Wiki/Knowledge Vault 기반 RAG 분석 + RCA. lot 이상, split 영향, MTS 변경, "
        "anchor item, DIBL/VTH/ION 같은 분석 질의에 적합."
    )
    EXAMPLES = (
        {"prompt": "A1000A.3 lot 이상 원인", "product": "PRODA"},
        {"prompt": "MTS_001 변경 영향"},
    )
    DATA_SOURCES = (
        DataSourceRef(
            kind="runtime_data",
            path="data/flow-data/knowledge/raw/sources/",
            description="RAG raw source 저장소 (append-only). 원본 DB/Fab 파일은 수정하지 않음.",
        ),
        DataSourceRef(
            kind="runtime_data",
            path="data/flow-data/knowledge/wiki/agent_wiki/",
            description="Maintained agent_wiki markdown 페이지.",
            columns=(
                ColumnDoc(name="doc_id", meaning="문서 식별자."),
                ColumnDoc(name="kind", meaning="문서 종류 (agent_wiki / schema_doc / ontology 등)."),
                ColumnDoc(name="source_ids", meaning="이 문서가 참조한 raw source id 목록."),
                ColumnDoc(name="tags", meaning="검색 태그."),
            ),
        ),
        DataSourceRef(
            kind="json",
            path="data/flow-data/knowledge/index/wiki_index.json",
            description="Wiki 검색 인덱스. doc_id/kind/tags/entity 메타.",
        ),
        DataSourceRef(
            kind="json",
            path="data/flow-data/knowledge/graph/graph.json",
            description="개념(concept) 그래프 — kind: identity|process|split|work 분류.",
        ),
    )
    SEMANTIC_BINDINGS = SemanticBindings(
        wiki_doc_ids=(
            "ml_table_proda.root_lot_id",
            "ml_table_proda.wafer_id",
            "ml_table_proda.step_id",
            "ml_table_proda.lot_wf",
        ),
    )
    HANDLER_ENTRY = CodeRef(
        module="backend.routers.llm",
        function="_handle_knowledge_impact_context",
        description="RAG 지식 / RCA — lot 이상, split 영향, MTS 변경, anchor item 질문 라우팅",
    )

    def handle(
        self,
        prompt: str,
        slots: dict[str, Any],
        ctx: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        from routers.llm import _handle_knowledge_impact_context

        product = str(slots.get("product") or "")
        try:
            max_rows = int(slots.get("max_rows") or 12)
        except (TypeError, ValueError):
            max_rows = 12

        result = _handle_knowledge_impact_context(prompt, product, max_rows)
        return result if result.get("handled") else None


class CalendarUnitAI(BaseUnitAI):
    KEY = "calendar"
    TITLE = "Calendar AI"
    DESCRIPTION = (
        "일정/캘린더 이벤트 조회. 회의 일정은 meeting AI가 함께 처리. "
        "'이번주 일정', '변경점 일정' 같은 질의에 적합."
    )
    EXAMPLES = (
        {"prompt": "이번주 일정"},
        {"prompt": "PRODA 변경점 일정"},
    )
    DATA_SOURCES = (
        DataSourceRef(
            kind="runtime_data",
            path="data/flow-data/calendar/",
            description="일정 / 회의 캘린더. /api/calendar/events에서 노출.",
            columns=(
                ColumnDoc(name="event_id", meaning="일정 식별자."),
                ColumnDoc(name="title", meaning="일정 제목."),
                ColumnDoc(name="start", meaning="시작 시각."),
                ColumnDoc(name="end", meaning="종료 시각."),
                ColumnDoc(name="owner", meaning="등록자."),
            ),
        ),
    )
    SEMANTIC_BINDINGS = SemanticBindings()
    HANDLER_ENTRY = CodeRef(
        module="backend.routers.llm",
        function="_unit_feature_guidance",
        lineno=2592,
        description="일정/캘린더 가이드 — prompt에 '일정/캘린더/calendar' 포함 시 라우팅",
    )

    def handle(
        self,
        prompt: str,
        slots: dict[str, Any],
        ctx: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        from routers.llm import _unit_feature_guidance

        text = str(prompt or "")
        low = text.lower()
        if not any(t in low or t in text for t in ("일정", "캘린더", "calendar", "변경점")):
            return None

        product = str(slots.get("product") or "")
        try:
            max_rows = int(slots.get("max_rows") or 12)
        except (TypeError, ValueError):
            max_rows = 12
        allowed_keys = ctx.get("allowed_keys")
        scoped = {"calendar"} if (allowed_keys is None or "calendar" in allowed_keys) else set()

        result = _unit_feature_guidance(prompt, product, max_rows=max_rows, allowed_keys=scoped)
        return result if result.get("handled") else None


class WaferLayoutUnitAI(BaseUnitAI):
    KEY = "waferlayout"
    TITLE = "Wafer Layout AI"
    DESCRIPTION = (
        "Wafer/TEG/shot/die layout 시각화 + TEG radius/position 조회. "
        "'wafer map', 'TEG radius', 'TEG 위치' 같은 layout 질의에 적합."
    )
    EXAMPLES = (
        {"prompt": "PRODA wafer map", "product": "PRODA"},
        {"prompt": "TEG_001 radius"},
    )
    DATA_SOURCES = (
        DataSourceRef(
            kind="json",
            path="data/Fab/wafer_maps/",
            description="제품별 wafer map JSON. die 위치, TEG 위치, radius 메타.",
            columns=(
                col.PRODUCT, col.WAFER,
                ColumnDoc(name="die_x", meaning="die의 X 좌표."),
                ColumnDoc(name="die_y", meaning="die의 Y 좌표."),
                ColumnDoc(name="teg_id", meaning="TEG (Test Element Group) 식별자."),
                ColumnDoc(name="radius", meaning="wafer center로부터의 거리."),
            ),
        ),
    )
    SEMANTIC_BINDINGS = SemanticBindings(
        column_catalog_keys=("product", "wafer_id"),
    )
    HANDLER_ENTRY = CodeRef(
        module="backend.routers.llm",
        function="_handle_wafer_map_chart / _handle_teg_radius_lookup / _handle_teg_position_lookup",
        lineno=7234,
        description="Wafer map chart / TEG radius / position 조회 라우팅",
    )

    def handle(
        self,
        prompt: str,
        slots: dict[str, Any],
        ctx: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        from routers.llm import (
            _handle_teg_position_lookup,
            _handle_teg_radius_lookup,
            _handle_wafer_map_chart,
            _handle_wafer_map_similarity,
        )

        product = str(slots.get("product") or "")
        try:
            max_rows = int(slots.get("max_rows") or 12)
        except (TypeError, ValueError):
            max_rows = 12

        for handler in (
            _handle_teg_radius_lookup,
            _handle_teg_position_lookup,
            _handle_wafer_map_chart,
            _handle_wafer_map_similarity,
        ):
            result = handler(prompt, product, max_rows)
            if result.get("handled"):
                return result
        return None
