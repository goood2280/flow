"""Shared ColumnDoc instances used by multiple unit AIs.

Edit-here-once. Wiki linkage (`wiki_doc_id`) points to schema_doc kind
pages under data/flow-data/knowledge/wiki/schema_doc/<doc_id>.md.
"""
from __future__ import annotations

from core.flowi_units.base import ColumnDoc

PRODUCT = ColumnDoc(
    name="product",
    meaning="제품명. ML_TABLE 파일명, FAB product directory에서 확인되는 이름과 정규화 일치.",
    sample_values=("PRODA", "PRODB"),
)

ROOT_LOT = ColumnDoc(
    name="root_lot_id",
    meaning="LOT 머리 5글자 (영문 1 + 숫자 4). dot lot의 왼쪽 head. 예: A1001A.3 → A1001.",
    sample_values=("A1000", "A1001"),
    wiki_doc_id="ml_table_proda.root_lot_id",
)

LOT = ColumnDoc(
    name="lot_id",
    meaning="LOT 식별자. 영문/숫자 5~6자 이상과 '.' suffix 조합. 예: A1001A.1, A1000.XX.",
    sample_values=("A1000A.3", "A1001A.1"),
)

FAB_LOT = ColumnDoc(
    name="fab_lot_id",
    meaning="현재 fab에서 가공중인 lot id. lot_progress cache의 최신 행에서 조회.",
)

WAFER = ColumnDoc(
    name="wafer_id",
    meaning="물리 wafer 번호. '#21', 'WF21', '21번 wafer'는 모두 '21'로 정규화.",
    sample_values=("1", "21"),
    wiki_doc_id="ml_table_proda.wafer_id",
)

STEP = ColumnDoc(
    name="step_id",
    meaning="공정 step 식별자. 예: AA200000, CA100000. function_step과 step_matching.csv로 연결.",
    sample_values=("AA200000", "CA100000"),
    wiki_doc_id="ml_table_proda.step_id",
)

FUNCTION_STEP = ColumnDoc(
    name="function_step",
    meaning="step의 자연어 설명. 예: channel release etch, inner spacer recess.",
)

LOT_WF = ColumnDoc(
    name="lot_wf",
    meaning="root_lot_id + '_' + wafer_id. 예: A1000 #21 → A1000_21. multi-source join의 공통 key.",
    wiki_doc_id="ml_table_proda.lot_wf",
)
