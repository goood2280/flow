"""routers/llm.py v8.7.7 — 선택적 사내 LLM 어댑터 노출.

- GET  /api/llm/status     is_available + redacted config (모든 유저 조회 가능 — UI 가시성용)
- POST /api/llm/test       admin 전용.  prompt 1건 실행해 연결 확인.
- POST /api/llm/flowi/chat 홈 Flowi 토큰 활성화 + fab 데이터 질의
- POST /api/llm/flowi/agent/chat 외부 AI client 가 같은 Flowi 기능을 API 로 호출

caller 주의: LLM 은 옵션. UI 는 status.available == false 면 관련 버튼을 숨겨야 함.
설정 편집은 /api/admin/settings/save 에서 llm 블록으로 수행.
"""
import json
import logging
import math
import re
import uuid
import csv
import io
from collections import Counter, deque
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
import polars as pl

from core import duckdb_engine
from core.paths import PATHS
from core.utils import _STR, load_json, save_json
from core.auth import current_user, require_admin, is_page_admin
from core import llm_adapter
from core import product_config
from core import semiconductor_knowledge as semi_knowledge
from routers.auth import read_users


router = APIRouter(prefix="/api/llm", tags=["llm"])
logger = logging.getLogger("flow.llm.router")
FLOWI_FEEDBACK_FILE = PATHS.data_root / "flowi_feedback.jsonl"
FLOWI_GOLDEN_FILE = PATHS.data_root / "flowi_golden_cases.jsonl"
FLOWI_ACTIVITY_FILE = PATHS.data_root / "flowi_activity.jsonl"
FLOWI_USER_DIR = PATHS.data_root / "flowi_users"
FLOWI_AGENT_GUIDE_FILE = PATHS.data_root / "flowi_agent_entrypoints.md"
FLOWI_AGENT_FEATURE_GUIDE_DIR = PATHS.data_root / "flowi_agent_features"
FLOWI_STAGED_DATA_DIR = PATHS.cache_dir / "flowi_data_register"
FLOWI_INFORM_SESSION_DIR = PATHS.data_root / "flowi_inform_sessions"
FLOWI_INFORM_SESSION_TTL_SECONDS = 3600
FLOWI_AGENT_GUIDE_FALLBACK = """# Flowi Agent Entrypoints

가벼운 라우팅 인덱스다. 질문에서 기능을 먼저 고르고, 고른 기능의 상세 가이드만 읽어 실행한다.

- dashboard: 차트, trend, 그래프, 그려줘, scatter, 상관, EQP/Chamber별
- tracker: 이슈, tracker, 모니터링, Analysis, 등록
- inform: 인폼, 인폼로그, 공지, 공유, 메일
- meeting: 회의, 미팅, 아젠다, 반복 회의
- calendar: 일정, 캘린더, 변경점, schedule
- splittable: SplitTable, plan, actual, KNOB, MASK, CUSTOM set
- ettime: ET median, elapsed, step/item/wf별 측정
- diagnosis: DIBL, VTH, SS, ION, IOFF, RCA, 원인 후보
- waferlayout: TEG, shot, die, wafer layout, edge
- tablemap: table map, relation, join path, 컬럼 관계
- filebrowser: parquet, csv, 파일, schema, 컬럼, row 조회
"""
FLOWI_READ_ONLY_POLICY = {
    "read_only": True,
    "applies_to": ["user"],
    "blocked_targets": ["raw data DB", "Files", "DB root files", "product reformatter files"],
    "admin_controlled_file_ops": {
        "enabled": True,
        "format": "FLOWI_FILE_OP JSON with exact confirm text",
        "scope": "Files root-level files only; DB root is read-only for everyone",
        "ops": ["delete", "rename", "replace_text", "register_data"],
    },
}
FLOWI_BASE_WORKFLOW_GUIDE = [
    {
        "key": "root_lot_id",
        "label": "root lot",
        "rule": "영문/숫자 혼합 5자 토큰은 기본적으로 root_lot_id로 해석합니다.",
        "examples": ["A0001", "B1234"],
    },
    {
        "key": "fab_lot_id",
        "label": "fab lot",
        "rule": "영문/숫자 혼합 6자 이상 토큰 또는 AZAAAB.1 같은 점 suffix lot은 fab_lot_id로 해석합니다.",
        "examples": ["A12345", "AZAAAB.1"],
    },
    {
        "key": "step_id",
        "label": "step",
        "rule": "step_id는 영문 2자 + 숫자 6자리만 step으로 해석합니다. 그 외에는 등록된 func_step 이름과 정확히 맞을 때만 step 후보로 봅니다.",
        "examples": ["AA200000", "GAA_CHANNEL_RELEASE"],
    },
    {
        "key": "wafer_id",
        "label": "wafer",
        "rule": "#6, WF6, WAFER 6은 wafer_id=6으로 해석합니다. 저장/표시는 DB 값에 맞춰 6 또는 06을 모두 매칭합니다.",
        "examples": ["#6", "WF6", "WAFER 06"],
    },
    {
        "key": "clarification",
        "label": "ambiguous",
        "rule": "lot/product/wafer/source가 애매하면 실행 전에 3개 이하의 선택지를 제시하고 사용자가 고르면 이어서 진행합니다.",
        "examples": ["root/fab/source 후보 선택"],
    },
]
FLOWI_AGENT_PERSONA = {
    "role": "semiconductor_process_data_analyst",
    "label": "반도체 공정 데이터 분석가",
    "principles": [
        "사내 naming rule을 먼저 적용해 자연어를 정형 파라미터로 변환한다.",
        "FAB/ET/INLINE/VM/EDS/QTIME의 grain과 join key 차이를 구분한다.",
        "원본 DB는 read-only로 다루고, 변경 요청은 전용 확인 workflow로만 진행한다.",
    ],
}
FLOWI_NAMING_RULES = [
    {
        "key": "product",
        "label": "product",
        "rule": "product_config/products.yaml, ML_TABLE_<product>, FAB product directory에서 product명을 동적으로 인식한다.",
        "examples": ["PRODA", "PRODB", "GAA2N"],
    },
    {
        "key": "root_lot_id",
        "label": "root lot",
        "rule": "영어/숫자 조합 5자리 토큰은 root_lot_id로 해석한다. product 토큰과 title 토큰은 lot에서 제외한다.",
        "examples": ["A1000", "R2001", "AB12C"],
    },
    {
        "key": "fab_lot_id",
        "label": "fab lot",
        "rule": "점(.)이 들어간 lot 조합이나 6자 이상 fab lot 후보는 fab_lot_id로 해석한다.",
        "examples": ["AZGASB.1", "ASDGA.1", "ASDAGFH.NJ"],
    },
    {
        "key": "wafer_id",
        "label": "wafer",
        "rule": "#6, WF6, wafer 6, slot 6, 6번 slot, 6번장, 6장 표현은 wafer_id=6으로 정규화한다. 유효 wafer slot은 1~25만 사용한다.",
        "examples": ["#6", "slot 6", "6번장"],
    },
    {
        "key": "step_id",
        "label": "step",
        "rule": "영문 2자 + 숫자 6자리 또는 등록된 func_step 이름만 step 후보로 확정한다.",
        "examples": ["AA200000", "GAA_CHANNEL_RELEASE"],
    },
    {
        "key": "func_step",
        "label": "function step",
        "rule": "`<숫자>.<숫자> <대문자모듈>` 형태를 func_step으로 그대로 캡처한다.",
        "examples": ["24.0 SORT", "16.0 VIA2", "8.0 SD_EPI"],
    },
    {
        "key": "module",
        "label": "inform module",
        "rule": "inform_user_modules의 모듈 union과 GATE/STI/PC/MOL/BEOL/ET/EDS/S-D Epi/Spacer/Well alias를 모듈로 해석한다.",
        "examples": ["GATE", "게이트", "S-D Epi", "스페이서"],
    },
    {
        "key": "metric",
        "label": "metric",
        "rule": "avg/평균/mean은 avg, median/중앙값은 median으로 정규화하고 ET/INLINE item alias를 metric 후보로 둔다.",
        "examples": ["CD", "LKG", "VIA2 Avg"],
    },
    {
        "key": "knob_value",
        "label": "KNOB value",
        "rule": "PPID_<digits>_<digits> 또는 KNOB/MASK 값처럼 쓰인 일반 토큰을 knob_value로 캡처한다.",
        "examples": ["PPID_24_3", "ABC_SPLIT"],
    },
    {
        "key": "split_set",
        "label": "split set",
        "rule": "`<token> 스플릿으로 선택`, `split=<token>` 표현을 split_set으로 캡처한다.",
        "examples": ["test1 스플릿으로 선택", "split=test2"],
    },
    {
        "key": "source_grain",
        "label": "source grain",
        "rule": "FAB은 route/progress 최신 이력, ET는 lot_wf median, INLINE raw는 lot_wf/subitem_id avg 기준으로 해석한다. raw INLINE에는 shot_x/shot_y가 없다.",
        "examples": ["FAB latest", "ET median", "INLINE subitem_id"],
    },
]
FLOWI_FUNCTION_FEW_SHOTS = [
    {
        "function": "query_current_fab_lot_from_fab_db",
        "prompt": "PRODA A1000 #6 현재 fab lot id가 뭐야?",
        "arguments": {"product": "PRODA", "root_lot_ids": ["A1000"], "wafer_ids": [6]},
    },
    {
        "function": "preview_splittable_plan_update",
        "prompt": "PRODA A1000 A KNOB #1~10은 ABC로 plan",
        "arguments": {"product": "PRODA", "root_lot_ids": ["A1000"], "plan_assignments": [{"knob": "KNOB_A", "wafer_ids": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10], "value": "ABC"}]},
    },
    {
        "function": "query_lot_knobs_from_ml_table",
        "prompt": "PRODA A1002 24.0 SORT KNOB 구성이 어떻게돼?",
        "arguments": {"product": "PRODA", "root_lot_ids": ["A1002"], "step": "24.0 SORT", "group": "KNOB"},
    },
    {
        "function": "build_dashboard_metric_chart",
        "prompt": "PRODA Inline CD scatter",
        "arguments": {"product": "PRODA", "source_types": ["INLINE"], "metrics_or_items": ["CD"], "chart_type": "scatter"},
    },
    {
        "function": "query_fab_progress",
        "prompt": "A1002A.1 어디에 있어?",
        "arguments": {"fab_lot_ids": ["A1002A.1"]},
    },
    {
        "function": "compose_inform_module_mail",
        "prompt": "GATE팀에 A1234 plan 적용 통보 메일",
        "arguments": {"root_lot_ids": ["A1234"], "module": "GATE", "lot_count": 1},
    },
    {
        "function": "register_inform_log",
        "prompt": "PRODA A1000A.3 GATE 모듈 인폼해줘 test1 스플릿으로 선택해줘 내용은 GATE 모듈인폼입니다.",
        "arguments": {"product": "PRODA", "fab_lot_ids": ["A1000A.3"], "module": "GATE", "split_set": "test1", "note": "GATE 모듈인폼입니다."},
    },
    {
        "function": "register_inform_log",
        "prompt": "A1003 GATE는 test1 STI는 test2 이런식으로 다 만들어줘",
        "arguments": {"root_lot_ids": ["A1003"], "entries": [{"module": "GATE", "split_set": "test1"}, {"module": "STI", "split_set": "test2"}]},
    },
    {
        "function": "preview_filebrowser_data",
        "prompt": "PRODA FAB 최근 100행 보여줘",
        "arguments": {"source_type": "FAB", "product": "PRODA", "limit": 100},
    },
    {
        "function": "search_filebrowser_schema",
        "prompt": "INLINE에 CD 컬럼 있는지 찾아줘",
        "arguments": {"source_type": "INLINE", "keyword": "CD"},
    },
    {
        "function": "query_wafer_split_at_step",
        "prompt": "PRODA A1002 #1 24.0 SORT Split이 뭐야",
        "arguments": {"product": "PRODA", "root_lot_ids": ["A1002"], "wafer_ids": [1], "step": "24.0 SORT"},
    },
    {
        "function": "find_lots_by_knob_value",
        "prompt": "24.0 SORT PPID_24_3인 자재 가장 빠른게 어디에 있어?",
        "arguments": {"step": "24.0 SORT", "knob_value": "PPID_24_3", "sort": "earliest_progress"},
    },
    {
        "function": "query_metric_at_step",
        "prompt": "A1000 #20 16.0 VIA2 Avg 몇이야?",
        "arguments": {"root_lot_ids": ["A1000"], "wafer_ids": [20], "step": "16.0 VIA2", "metric": "VIA2 Avg", "agg": "avg"},
    },
    {
        "function": "register_inform_walkthrough",
        "prompt": "A1004 인폼전체 작성해줘",
        "arguments": {"root_lot_ids": ["A1004"], "action": "start"},
    },
    {
        "function": "register_inform_walkthrough",
        "prompt": "test1로 해줘",
        "arguments": {"session_id": "<active>", "action": "set", "value": "test1"},
    },
    {
        "function": "register_inform_walkthrough",
        "prompt": "이건 일단 생략할게",
        "arguments": {"session_id": "<active>", "action": "skip"},
    },
    {
        "function": "register_inform_walkthrough",
        "prompt": "BEOL도 할게",
        "arguments": {"session_id": "<active>", "action": "jump", "target_module": "BEOL"},
    },
]
FLOWI_DEFAULT_SYSTEM_PROMPT = (
    "Flowi는 사내 Flow 홈 화면의 반도체 공정 데이터 분석가입니다. 답변은 짧고 실행 가능하게 작성합니다. "
    "사용자 Markdown 정보가 있으면 담당 제품, 관심 공정, 선호 출력 방식을 반영합니다. "
    "요청이 애매하면 바로 실행한다고 말하지 말고 1/2/3 형태의 선택지를 제시합니다. "
    "먼저 사내 naming rule을 적용해 자연어를 function arguments JSON으로 구조화한 뒤, 그 파라미터와 답변이 어긋나지 않게 합니다. "
    "product는 product_config/products.yaml, ML_TABLE_<product>, FAB product directory에서 동적으로 확인합니다. "
    "영어/숫자 조합 5자리 토큰은 root_lot_id, AZGASB.1/ASDAGFH.NJ처럼 점(.)이 들어간 lot 조합은 fab_lot_id로 해석합니다. "
    "#6, WF6, WAFER 6, slot 6, 6번 slot, 6번장, 6장은 wafer_id=6으로 해석하며 wafer_id는 1~25만 유효한 물리 slot으로 봅니다. "
    "step_id는 영문 2자 + 숫자 6자리만 step으로 해석하고, 그 외에는 등록된 func_step 이름과 정확히 맞을 때만 step 후보로 봅니다. "
    "FAB은 최신 route/progress 이력, ET는 기본 median, INLINE은 기본 avg이며 raw INLINE은 shot_x/shot_y가 아니라 subitem_id를 shot 구분자로 봅니다. "
    "일반 사용자의 원 data DB 또는 Files 수정/삭제/저장/업로드는 차단합니다. "
    "admin 파일 변경은 서버의 FLOWI_FILE_OP 단위기능 결과가 제공된 경우에만 그 결과를 설명합니다."
)
FLOWI_DEFAULT_MUST_NOT = (
    "- DB root/raw data 원본을 직접 수정, 삭제, 덮어쓰기, 이동하지 않는다.\n"
    "- 로컬 tool/cache/schema 결과에 없는 숫자, lot, product, step, item 값을 지어내지 않는다.\n"
    "- step_id는 영문 2자 + 숫자 6자리 또는 등록된 func_step 이름이 아니면 step으로 확정하지 않는다.\n"
    "- 기존 인폼/회의/이슈/일정 수정, 삭제, 상태 변경은 권한과 대상 내용을 확인하기 전 실행하지 않는다.\n"
    "- 파일 변경은 FLOWI_FILE_OP 또는 전용 단일파일 반영 플로우 없이 실행하지 않는다.\n"
    "- RAG/문서 내용은 flow-data 내부 저장소 밖으로 내보내지 않는다."
)
FLOWI_PROFILE_START = "<!-- FLOWI_USER_NOTES_START -->"
FLOWI_PROFILE_END = "<!-- FLOWI_USER_NOTES_END -->"
FLOWI_FEEDBACK_TAXONOMY = [
    {"key": "correct", "label": "정확함", "tone": "ok"},
    {"key": "explanation_gap", "label": "데이터는 맞는데 설명 부족", "tone": "warn"},
    {"key": "wrong_data_source", "label": "잘못된 DB/컬럼", "tone": "bad"},
    {"key": "wrong_workflow", "label": "원하는 workflow가 아님", "tone": "bad"},
    {"key": "missed_clarification", "label": "질문하고 진행했어야 함", "tone": "warn"},
    {"key": "too_slow", "label": "너무 느림", "tone": "warn"},
    {"key": "permission_risk", "label": "권한/보안 우려", "tone": "bad"},
    {"key": "output_issue", "label": "표/차트/출력 문제", "tone": "warn"},
    {"key": "hallucination", "label": "DB에 없는 값을 답변", "tone": "bad"},
    {"key": "key_matching_error", "label": "lot/wafer/step 매칭 오류", "tone": "bad"},
    {"key": "aggregation_error", "label": "avg/median/집계 오류", "tone": "bad"},
]
FLOWI_USER_FEEDBACK_TAGS = {"correct", "explanation_gap", "missed_clarification", "too_slow", "output_issue", "hallucination"}
FLOWI_FEATURE_ENTRYPOINTS = [
    {
        "key": "filebrowser",
        "title": "파일 탐색기",
        "description": "Parquet/CSV 원천 데이터를 선택하고 SQL-like 필터와 컬럼 선택으로 빠르게 샘플링합니다.",
        "prompt": "파일 탐색기에서 내가 가진 product/lot 조건으로 어떤 DB와 필터를 먼저 보면 좋을지 알려줘.",
    },
    {
        "key": "dashboard",
        "title": "대시보드",
        "description": "선택한 데이터 소스를 차트로 비교하고 기간, 컬럼, 필터 조건을 바꿔 추세를 봅니다.",
        "prompt": "대시보드에서 내 담당 제품의 이상 징후를 보기 위한 차트 구성을 추천해줘.",
    },
    {
        "key": "splittable",
        "title": "스플릿 테이블",
        "description": "Root lot/wafer 단위로 plan과 actual을 비교하고 변경 이력을 추적합니다.",
        "prompt": "스플릿 테이블에서 plan vs actual mismatch를 빨리 확인하는 흐름을 알려줘.",
    },
    {
        "key": "diagnosis",
        "title": "반도체 진단/RCA",
        "description": "ET/Inline/VM item 의미를 item_master로 해석하고 Knowledge Card, causal graph, similar case로 RCA 후보를 만듭니다.",
        "prompt": "GAA short Lg에서 DIBL과 SS가 증가했을 때 원인 후보와 확인 차트를 추천해줘.",
    },
    {
        "key": "tracker",
        "title": "이슈 추적",
        "description": "Lot/Wafer 범위를 포함한 이슈, 댓글, 이미지, Gantt 진행 상태를 관리합니다.",
        "prompt": "트래커에 lot/wafer 이슈를 남길 때 필요한 정보와 좋은 제목을 추천해줘.",
    },
    {
        "key": "inform",
        "title": "인폼 로그",
        "description": "제품/lot 인폼을 남기고 SplitTable 스냅샷, 댓글, 메일 공유까지 연결합니다.",
        "prompt": "인폼 로그에 공유할 내용을 내 상황에 맞게 정리해줘.",
    },
    {
        "key": "meeting",
        "title": "회의관리",
        "description": "회의 아젠다, 회의록, 결정사항, 액션아이템을 관리하고 메일로 공유합니다.",
        "prompt": "내 이슈를 회의 아젠다와 액션아이템으로 정리해줘.",
    },
    {
        "key": "calendar",
        "title": "변경점 관리",
        "description": "변경 일정과 상태를 달력에서 확인하고 회의 액션과 연결합니다.",
        "prompt": "이번 변경 건을 캘린더에 넣기 위한 제목, 기간, 상태를 추천해줘.",
    },
    {
        "key": "ettime",
        "title": "ET 레포트",
        "description": "fab_lot_id, step, item 기준 elapsed time과 wafer별 통계를 확인합니다.",
        "prompt": "ET 레포트에서 root_lot/step/item 조건으로 먼저 봐야 할 값을 알려줘.",
    },
    {
        "key": "waferlayout",
        "title": "WF Layout",
        "description": "제품별 wafer shot/chip/TEG 배치와 edge shot 후보를 검토합니다.",
        "prompt": "WF Layout에서 내 제품의 layout 검토 포인트를 체크리스트로 만들어줘.",
    },
    {
        "key": "tablemap",
        "title": "테이블 맵",
        "description": "DB 테이블과 컬럼 관계를 그래프로 보고 연결 맥락을 확인합니다.",
        "prompt": "테이블 맵에서 내가 찾는 lot/step/item 컬럼의 연결 경로를 어떻게 확인하면 좋을지 알려줘.",
    },
    {
        "key": "devguide",
        "title": "개발 가이드",
        "description": "Flow 구조, API, 운영 규칙을 확인하는 가벼운 문서 진입점입니다.",
        "prompt": "개발 가이드에서 이 기능을 이해하려면 어떤 문서와 API를 먼저 보면 좋을지 알려줘.",
    },
]
FLOWI_FEATURE_ALIASES = {
    "filebrowser": ["files", "file browser", "파일", "파일브라우저", "파일 탐색", "csv", "parquet", "db 조회", "데이터 조회"],
    "dashboard": ["dashboard", "대시보드", "차트", "trend", "추세", "그래프", "시각화", "scatter", "corr", "correlation", "상관", "피팅", "fitting"],
    "splittable": ["split", "split table", "splittable", "스플릿", "스플릿테이블", "plan", "actual", "mismatch", "매칭", "불일치"],
    "diagnosis": ["diagnosis", "rca", "root cause", "root-cause", "진단", "원인", "원인 후보", "반도체 지식", "knowledge card", "causal", "인과", "DIBL", "SS", "RSD", "ION", "IOFF", "IGATE", "VTH", "CA_RS", "SRAM"],
    "tracker": ["tracker", "트래커", "issue", "이슈", "gantt", "간트", "lot 이슈"],
    "inform": ["inform", "인폼", "공유", "메일", "공지", "보고"],
    "meeting": ["meeting", "회의", "아젠다", "회의록", "action item", "액션아이템"],
    "calendar": ["calendar", "캘린더", "일정", "변경점", "change", "schedule"],
    "ettime": ["et report", "ettime", "et 레포트", "et 리포트", "median", "wf별", "wafer별", "측정", "eta"],
    "waferlayout": ["wafer layout", "wf layout", "layout", "레이아웃", "shot", "die", "teg"],
    "tablemap": ["table map", "tablemap", "테이블맵", "관계", "relation", "join", "column map", "컬럼"],
    "devguide": ["devguide", "개발", "api", "문서", "가이드", "architecture"],
}
FLOWI_DEFAULT_TABS = {
    "filebrowser", "dashboard", "splittable", "ettime", "waferlayout",
    "tracker", "inform", "meeting", "calendar", "diagnosis",
}
FLOWI_NEW_DEFAULT_TABS = {"tracker", "inform", "meeting", "calendar", "ettime", "waferlayout", "diagnosis"}
FLOWI_ADMIN_ONLY_FEATURES = {"tablemap", "admin"}
FLOWI_RESTRICTED_FEATURES = {"devguide": "devguide_allowed"}
FLOWI_UNIT_ACTIONS = {
    "filebrowser": {
        "intent": "filebrowser_guidance",
        "action": "open_filebrowser",
        "needs": ["source/root", "product or file", "optional SQL/filter"],
        "outputs": ["table preview", "selected columns", "CSV download"],
    },
    "dashboard": {
        "intent": "dashboard_guidance",
        "action": "open_dashboard",
        "needs": ["source", "x/y column", "join key", "optional fit/color/filter"],
        "outputs": ["chart", "trend/alert summary", "query audit"],
    },
    "splittable": {
        "intent": "splittable_guidance",
        "action": "open_splittable",
        "needs": ["product", "root_lot_id", "wafer_id or all", "parameter prefix such as KNOB/MASK/FAB"],
        "outputs": ["plan vs actual matrix", "mismatch cells", "notes"],
    },
    "diagnosis": {
        "intent": "semiconductor_diagnosis",
        "action": "run_semiconductor_diagnosis",
        "needs": ["symptom metrics", "unit/source/test_structure if ambiguous", "product/lot when available"],
        "outputs": ["interpreted item meanings", "ranked RCA hypotheses", "causal paths", "similar cases", "chart specs", "missing data"],
    },
    "tracker": {
        "intent": "tracker_guidance",
        "action": "open_tracker",
        "needs": ["issue title", "product/lot/wafer", "owner/status"],
        "outputs": ["issue row", "comments", "Gantt status"],
    },
    "inform": {
        "intent": "inform_guidance",
        "action": "open_inform",
        "needs": ["product", "root_lot_id", "message/reason"],
        "outputs": ["inform thread", "split table snapshot", "mail preview"],
    },
    "meeting": {
        "intent": "meeting_guidance",
        "action": "open_meeting",
        "needs": ["meeting topic", "participants", "action items"],
        "outputs": ["agenda", "minutes", "action item list"],
    },
    "calendar": {
        "intent": "calendar_guidance",
        "action": "open_calendar",
        "needs": ["event title", "date/range", "status/category"],
        "outputs": ["change event", "linked action state"],
    },
    "ettime": {
        "intent": "et_wafer_median",
        "action": "query_et",
        "needs": ["product", "root_lot_id or lot_id", "step_id", "item_id"],
        "outputs": ["wafer별 median/mean/count table"],
    },
    "waferlayout": {
        "intent": "waferlayout_guidance",
        "action": "open_waferlayout",
        "needs": ["product", "layout name or shot/chip context"],
        "outputs": ["wafer map", "edge shot/layout checks"],
    },
    "tablemap": {
        "intent": "tablemap_guidance",
        "action": "open_tablemap",
        "needs": ["source table/column", "target table/column"],
        "outputs": ["relation path", "column match table"],
    },
    "devguide": {
        "intent": "devguide_guidance",
        "action": "open_devguide",
        "needs": ["feature/API/topic"],
        "outputs": ["doc entry", "API references"],
    },
}

FLOWI_CHART_TERMS = {
    "차트", "그래프", "scatter", "산점도", "corr", "correlation", "상관", "피팅", "fitting",
    "fit", "1차식", "선형", "linear", "컬러링", "color", "coloring", "filter", "필터", "제외",
    "그려", "그려줘", "plot", "bar", "막대", "trend", "추세", "시계열", "라인", "line",
}
FLOWI_JOIN_CHOICES = [
    {
        "id": "inline_left",
        "label": "1",
        "title": "INLINE 기준 left join",
        "recommended": True,
        "description": "INLINE metric을 기준으로 ET/ML_TABLE을 붙이고 누락 row 통계를 함께 표시합니다.",
        "prompt_suffix": "INLINE 기준 left join으로 진행",
    },
    {
        "id": "et_left",
        "label": "2",
        "title": "ET 기준 left join",
        "recommended": False,
        "description": "ET metric을 기준으로 INLINE/ML_TABLE을 붙입니다.",
        "prompt_suffix": "ET 기준 left join으로 진행",
    },
    {
        "id": "inner_join",
        "label": "3",
        "title": "inner join",
        "recommended": False,
        "description": "양쪽에 모두 있는 shot/wafer만 남겨 correlation을 계산합니다.",
        "prompt_suffix": "inner join으로 진행",
    },
]
FLOWI_DOMAIN_DICTIONARY = {
    "DIBL": ["DIBL", "drain induced barrier lowering"],
    "RCH": ["RCH", "R_CH", "channel resistance"],
    "DC": ["DC", "duty cycle", "direct current"],
    "RS": ["RS", "R_S", "source resistance"],
    "RC": ["RC", "R_C", "contact resistance"],
    "LKG": ["LKG", "LEAK", "LEAKAGE", "IOFF"],
    "SHORT": ["SHORT", "SHORT_FAIL"],
    "VTH": ["VTH", "VT", "VTLIN", "VTSAT"],
    "ION": ["ION", "IDSAT"],
    "IOFF": ["IOFF", "LEAKAGE"],
    "CD": ["CD", "CRITICAL_DIMENSION", "WIDTH"],
    "CD_GATE": ["CD_GATE", "GATE_CD", "GATE CD"],
    "CD_SPACER": ["CD_SPACER", "SPACER_CD", "SPACER CD"],
    "OVERLAY": ["OVERLAY", "OVL"],
    "THICKNESS": ["THICKNESS", "THK", "TICK"],
}
FLOWI_CHART_METRIC_STOP = {
    "INLINE", "IN-LINE", "ET", "ML", "ML_TABLE", "KNOB", "CORR", "CORRELATION",
    "SCATTER", "CHART", "DASHBOARD", "FITTING", "FIT", "LINE", "LINEAR", "COLOR",
    "COLORING", "FILTER", "LEFT", "JOIN", "INNER", "AVG", "AVERAGE", "MEDIAN",
    "EXCLUDE", "EXCEPT", "REMOVE", "WITHOUT", "BY", "BASIS", "TREND", "PLOT", "BAR", "GRAPH",
}
FLOWI_CHART_POINT_LIMIT = 500
FLOWI_CHART_DEFAULTS = {
    "surface": "home_flowi",
    "scatter": {"grain": "wafer_agg", "max_points": 500, "inline_agg": "avg", "et_agg": "median"},
    "line": {"grain": "wafer_agg", "max_points_per_series": 120},
    "bar": {"top_n": 12, "other_bucket": True},
    "pie": {"max_slices": 6, "other_bucket": True},
    "box": {"max_groups": 12, "min_n": 3},
}


def _merge_nested(base: dict[str, Any], override: Any) -> dict[str, Any]:
    out = {
        k: _merge_nested(v, {}) if isinstance(v, dict) else v
        for k, v in (base or {}).items()
    }
    if not isinstance(override, dict):
        return out
    for key, value in override.items():
        if isinstance(out.get(key), dict) and isinstance(value, dict):
            out[key] = _merge_nested(out[key], value)
        else:
            out[key] = value
    return out


def _flowi_chart_defaults() -> dict[str, Any]:
    cfg = (_admin_settings().get("flowi_defaults") or {}).get("chart_defaults") or {}
    defaults = _merge_nested(FLOWI_CHART_DEFAULTS, cfg)
    scatter = defaults.get("scatter") if isinstance(defaults.get("scatter"), dict) else {}
    if scatter.get("grain") not in {"wafer_agg", "shot", "die", "map"}:
        scatter["grain"] = "wafer_agg"
    for key, fallback, lo, hi in (
        ("max_points", 500, 50, 5000),
        ("max_points_per_series", 120, 20, 1000),
    ):
        if key in scatter:
            try:
                scatter[key] = max(lo, min(hi, int(scatter.get(key) or fallback)))
            except Exception:
                scatter[key] = fallback
    if scatter.get("inline_agg") not in {"avg", "median"}:
        scatter["inline_agg"] = "avg"
    if scatter.get("et_agg") not in {"avg", "median"}:
        scatter["et_agg"] = "median"
    defaults["scatter"] = scatter
    return defaults


def _flowi_engineer_knowledge_defaults() -> dict[str, Any]:
    raw = (_admin_settings().get("flowi_defaults") or {}).get("engineer_knowledge") or {}
    return {
        "rag_update_requires_marker": bool(raw.get("rag_update_requires_marker", True)),
        "admin_review_required": bool(raw.get("admin_review_required", True)),
        "custom_knowledge_append_only": bool(raw.get("custom_knowledge_append_only", True)),
    }

_WRITE_TERMS = (
    "수정", "변경", "바꿔", "바꾸", "저장", "삭제", "지워", "업로드", "올려",
    "덮어", "추가", "생성", "편집", "업데이트", "이동", "rename", "delete",
    "update", "insert", "drop", "write", "save", "modify", "edit", "upload",
    "create", "remove", "overwrite", "replace", "move",
)
_WRITE_TARGET_TERMS = (
    "db", "database", "data root", "raw data", "source file", "files", "file",
    "csv", "parquet", "json", "reformatter", "원 data", "원데이터", "원본",
    "데이터", "파일", "루트", "소스", "제품별 reformatter",
)
_FLOWI_FILE_OP_MARKER = "FLOWI_FILE_OP"
_FLOWI_DATA_REGISTER_MARKER = "FLOWI_DATA_REGISTER"
_FLOWI_SPLITTABLE_NOTE_MARKER = "FLOWI_SPLITTABLE_NOTE"
_FLOWI_SPLITTABLE_PLAN_MARKER = "FLOWI_SPLITTABLE_PLAN"
_FLOWI_INFORM_CONFIRM_MARKER = "FLOWI_INFORM_CONFIRM"
_FLOWI_INFORM_WALKTHROUGH_MARKER = "FLOWI_INFORM_WALKTHROUGH"
_FLOWI_FILE_EXTS = {".parquet", ".csv", ".json", ".md", ".txt", ".yaml", ".yml"}
_FLOWI_TEXT_FILE_EXTS = {".csv", ".json", ".md", ".txt", ".yaml", ".yml"}
_FLOWI_MAX_TEXT_EDIT_BYTES = 2 * 1024 * 1024
_FLOWI_MAX_REGISTER_ROWS = 300
_FLOWI_MAX_REGISTER_COLS = 80
FLOWI_MAX_WAFER_ID = 25
_FLOWI_FILE_TOKEN_RE = re.compile(
    r"(?<![\w./-])([A-Za-z0-9][A-Za-z0-9_.@+=-]{0,120}\.(?:parquet|csv|json|md|txt|yaml|yml))(?![\w.-])",
    re.I,
)
_FLOWI_APP_WRITE_TERMS = (
    "등록해줘", "등록해주세요", "만들어줘", "만들어주세요", "생성해줘", "생성해주세요",
    "추가해줘", "추가해주세요", "넣어줘", "넣어주세요", "남겨줘", "남겨주세요",
    "올려줘", "올려주세요", "기록 남겨", "기록해줘", "기록해주세요", "코멘트", "꼬리표",
)
_FLOWI_APP_CREATE_TERMS = (
    "등록", "만들", "생성", "추가", "넣어", "남겨", "기록", "올려",
    "create", "add", "new",
)
_FLOWI_APP_MODIFY_TERMS = (
    "수정", "삭제", "지워", "바꿔", "바꾸", "편집", "업데이트", "rename",
    "delete", "remove", "edit", "update", "modify", "replace", "archive",
)
_FLOWI_APP_WRITE_HINTS = {
    "inform": ("인폼", "inform"),
    "tracker": ("이슈추적", "이슈 추적", "이슈", "issue", "tracker", "트래커"),
    "meeting": ("회의", "아젠다", "회의록", "agenda", "meeting"),
    "calendar": ("일정", "캘린더", "변경점", "calendar"),
    "splittable": ("split table", "splittable", "스플릿", "스플릿테이블", "split table"),
    "annotation": ("꼬리표", "코멘트", "특이사항", "기록"),
}
_FLOWI_FAB_EQP_TERMS = ("eqp", "eqp_id", "equipment", "장비", "설비")
_FLOWI_STEP_WORDS = ("step", "step_id", "스텝", "공정")
_MODULE_ALIAS = {
    "GATE": ["게이트", "gate"],
    "STI": ["sti", "sti모듈"],
    "PC": ["pc", "photoresist"],
    "MOL": ["mol"],
    "BEOL": ["beol", "후공정"],
    "ET": ["et", "이티", "측정"],
    "EDS": ["eds"],
    "S-D Epi": ["sd", "sde", "sd epi", "s-d epi", "에피"],
    "Spacer": ["spacer", "스페이서"],
    "Well": ["well", "웰"],
    "기타": ["기타", "other"],
}
_FLOWI_DEFAULT_INFORM_MODULES = ["GATE", "STI", "PC", "MOL", "BEOL", "ET", "EDS", "S-D Epi", "Spacer", "Well", "기타"]

_STOP_TOKENS = {
    "A", "AN", "THE", "ET", "WF", "WAFER", "WAFERS", "BY", "PER", "ITEM", "LOT", "LOTS",
    "KNOB", "KNOBS", "MEDIAN", "MEAN", "AVG", "AVERAGE", "VALUE", "VALUES", "FLOWI",
    "값", "중앙값", "평균", "별로", "별", "랏", "로트", "노브", "아이템", "어떤",
    "어떻게", "몇이야", "처리", "데이터", "조회", "보여줘",
    "현재", "기준", "확인", "언제", "어디", "도착", "얼마나", "걸렸어",
}
_FLOWI_NON_LOT_TOKENS = {"SPLIT", "TEST", "PLAN", "ACTUAL"}


def _text(raw: Any) -> str:
    return str(raw or "").strip()


def _upper(raw: Any) -> str:
    return _text(raw).upper()


def _normalize_wafer_id(raw: Any, *, max_wafer: int = FLOWI_MAX_WAFER_ID) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    core = re.sub(r"^(?:#|WAFER|WF|W)\s*", "", text, flags=re.I).strip()
    if not re.fullmatch(r"\d+", core):
        return ""
    try:
        n = int(core)
    except Exception:
        return ""
    return str(n) if 1 <= n <= max_wafer else ""


def _all_valid_wafer_ids() -> list[str]:
    return [str(i) for i in range(1, FLOWI_MAX_WAFER_ID + 1)]


def _md_line(raw: Any, limit: int = 600) -> str:
    text = re.sub(r"\s+", " ", str(raw or "")).strip()
    return text[:limit]


def _safe_username(raw: Any) -> str:
    username = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(raw or "user").strip())
    username = username.strip("._-") or "user"
    return username[:80]


def _admin_settings() -> dict:
    data = load_json(PATHS.data_root / "admin_settings.json", {})
    return data if isinstance(data, dict) else {}


def _save_admin_settings(data: dict) -> None:
    save_json(PATHS.data_root / "admin_settings.json", data if isinstance(data, dict) else {}, indent=2)


def _flowi_persona_config() -> dict[str, Any]:
    raw = _admin_settings().get("flowi_persona")
    raw = raw if isinstance(raw, dict) else {}
    custom_prompt = str(raw.get("system_prompt") or "").strip()
    custom_must_not = str(raw.get("must_not") or "").strip()
    active_prompt = custom_prompt or FLOWI_DEFAULT_SYSTEM_PROMPT
    active_must_not = custom_must_not or FLOWI_DEFAULT_MUST_NOT
    active_system_prompt = active_prompt
    if active_must_not:
        active_system_prompt += "\n\n반드시 하지 말아야 할 것:\n" + active_must_not
    return {
        "enabled": True,
        "source": "saved" if custom_prompt else "default",
        "system_prompt": custom_prompt or FLOWI_DEFAULT_SYSTEM_PROMPT,
        "must_not": custom_must_not or FLOWI_DEFAULT_MUST_NOT,
        "active_system_prompt": active_system_prompt,
        "default_system_prompt": FLOWI_DEFAULT_SYSTEM_PROMPT,
        "default_must_not": FLOWI_DEFAULT_MUST_NOT,
        "notes": str(raw.get("notes") or "").strip(),
        "updated_by": str(raw.get("updated_by") or "").strip(),
        "updated_at": str(raw.get("updated_at") or "").strip(),
    }


def _flowi_few_shot_section(limit: int = 24) -> str:
    rows = []
    for item in FLOWI_FUNCTION_FEW_SHOTS[: max(1, int(limit or 24))]:
        rows.append(json.dumps(item, ensure_ascii=False, default=str))
    return "[Few-shot examples]\n" + "\n".join(rows)


def _flowi_system_prompt(include_few_shots: bool = True) -> str:
    prompt = _flowi_persona_config()["active_system_prompt"]
    if include_few_shots:
        prompt += "\n\n" + _flowi_few_shot_section()
    return prompt


def _tabs_for_user(username: str, role: str) -> set[str] | str:
    if role == "admin":
        return "__all__"
    raw = ""
    try:
        for row in read_users():
            if row.get("username") == username:
                raw = (row.get("tabs") or "").strip()
                role = row.get("role") or role
                if role == "admin":
                    return "__all__"
                break
    except Exception:
        raw = ""
    if raw == "__all__":
        return "__all__"
    if not raw:
        tabs = set(FLOWI_DEFAULT_TABS)
    else:
        tabs = {t.strip() for t in raw.split(",") if t.strip()}
        tabs.update(FLOWI_NEW_DEFAULT_TABS)
    return tabs


def _devguide_allowed(username: str, role: str, tabs: set[str] | str) -> bool:
    if role == "admin" or tabs == "__all__":
        return True
    if "devguide" not in tabs:
        return False
    devguide_users = (_admin_settings().get("devguide_user") or [])
    if not isinstance(devguide_users, list):
        return False
    return username in {str(u).strip() for u in devguide_users if str(u).strip()}


def _allowed_flowi_feature_keys(me: dict) -> set[str]:
    username = me.get("username") or "user"
    role = me.get("role") or "user"
    tabs = _tabs_for_user(username, role)
    out: set[str] = set()
    for item in FLOWI_FEATURE_ENTRYPOINTS:
        key = item.get("key") or ""
        if key in FLOWI_ADMIN_ONLY_FEATURES and role != "admin":
            continue
        if key in FLOWI_RESTRICTED_FEATURES and not _devguide_allowed(username, role, tabs):
            continue
        if tabs == "__all__" or key in tabs:
            out.add(key)
    return out


def _feature_title(key: str) -> str:
    for item in FLOWI_FEATURE_ENTRYPOINTS:
        if item.get("key") == key:
            return item.get("title") or key
    return key


def _flowi_permission_block(feature_key: str, me: dict) -> dict:
    title = _feature_title(feature_key)
    username = me.get("username") or "user"
    answer = (
        f"현재 계정({username})에는 {title} 기능 권한이 없어 Flowi가 접근할 수 없습니다.\n"
        "관리자에게 해당 탭 권한을 요청한 뒤 다시 실행하세요."
    )
    return {
        "handled": True,
        "intent": "permission_denied",
        "blocked": True,
        "feature": feature_key,
        "answer": answer,
        "missing_permission": feature_key,
    }


def _flowi_home_admin_function_block(prompt: str) -> dict[str, Any]:
    text = str(prompt or "")
    low = text.lower()
    admin_terms = (
        "매칭테이블", "매칭 테이블", "matching table", "match table",
        "룰북", "rulebook", "rule book",
        "knowledge ingest", "knowledge 등록", "지식 ingest", "지식 등록",
        "rag 반영", "rag 등록",
    )
    if not any(term in low or term in text for term in admin_terms):
        return {"handled": False}
    answer = "이 작업은 권한이 필요해요. 관리자에게 요청해 주세요."
    return {
        "handled": True,
        "intent": "home_admin_function_blocked",
        "action": "blocked_admin_only_function",
        "feature": "diagnosis",
        "blocked": True,
        "reject_reason": answer,
        "answer": answer,
    }


def _user_md_path(username: str) -> Path:
    return FLOWI_USER_DIR / f"{_safe_username(username)}.md"


def _new_user_md(username: str) -> str:
    now = datetime.now(timezone.utc).isoformat()
    return (
        f"# Flowi User Context: {_safe_username(username)}\n\n"
        f"- Created: {now}\n"
        f"- Updated: {now}\n\n"
        "## User Notes\n"
        f"{FLOWI_PROFILE_START}\n"
        "\n"
        f"{FLOWI_PROFILE_END}\n\n"
        "## Activity Log\n"
    )


def _read_user_md(username: str, *, create: bool = True) -> str:
    path = _user_md_path(username)
    try:
        FLOWI_USER_DIR.mkdir(parents=True, exist_ok=True)
        if not path.exists() and create:
            path.write_text(_new_user_md(username), encoding="utf-8")
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("flowi user md read failed: %s", e)
        return ""


def _notes_from_md(md: str) -> str:
    if not md:
        return ""
    m = re.search(
        re.escape(FLOWI_PROFILE_START) + r"\n?(.*?)\n?" + re.escape(FLOWI_PROFILE_END),
        md,
        flags=re.S,
    )
    return (m.group(1).strip() if m else "").strip()


def _replace_user_notes(md: str, username: str, notes: str) -> str:
    now = datetime.now(timezone.utc).isoformat()
    if not md:
        md = _new_user_md(username)
    notes_block = f"{FLOWI_PROFILE_START}\n{notes.strip()}\n{FLOWI_PROFILE_END}"
    pattern = re.escape(FLOWI_PROFILE_START) + r"\n?.*?\n?" + re.escape(FLOWI_PROFILE_END)
    if re.search(pattern, md, flags=re.S):
        out = re.sub(pattern, notes_block, md, flags=re.S)
    else:
        insert = "## User Notes\n" + notes_block + "\n\n"
        out = md.replace("## Activity Log\n", insert + "## Activity Log\n") if "## Activity Log\n" in md else md + "\n\n" + insert
    out = re.sub(r"- Updated: .+", f"- Updated: {now}", out, count=1)
    if "- Updated:" not in out.split("\n\n", 1)[0]:
        out = out.replace("\n\n", f"\n- Updated: {now}\n\n", 1)
    return out


def _write_user_notes(username: str, notes: str) -> str:
    path = _user_md_path(username)
    FLOWI_USER_DIR.mkdir(parents=True, exist_ok=True)
    md = _replace_user_notes(_read_user_md(username), username, notes)
    path.write_text(md, encoding="utf-8")
    return md


def _append_user_event(username: str, title: str, fields: dict[str, Any]) -> None:
    try:
        path = _user_md_path(username)
        md = _read_user_md(username)
        now = datetime.now(timezone.utc).isoformat()
        lines = [f"\n### {now} - {title}"]
        for key, val in fields.items():
            if val is None:
                continue
            lines.append(f"- {key}: {_md_line(val, 900)}")
        path.write_text(md.rstrip() + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
        FLOWI_ACTIVITY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with FLOWI_ACTIVITY_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "timestamp": now,
                "username": _safe_username(username),
                "event": title,
                "fields": fields,
            }, ensure_ascii=False, default=str) + "\n")
    except Exception as e:
        logger.warning("flowi user md append failed: %s", e)


def _taxonomy_keys() -> set[str]:
    return {str(item.get("key") or "") for item in FLOWI_FEEDBACK_TAXONOMY}


def _normalize_feedback_tags(tags: Any, rating: str = "") -> list[str]:
    allowed = _taxonomy_keys()
    raw = tags if isinstance(tags, list) else []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        key = str(item or "").strip()
        if not key or key not in allowed or key in seen:
            continue
        seen.add(key)
        out.append(key)
    if not out and str(rating or "").lower() == "up":
        out.append("correct")
    if not out and str(rating or "").lower() == "down":
        out.append("output_issue")
    return out[:8]


def _flowi_tool_summary(tool: Any) -> dict[str, Any]:
    if not isinstance(tool, dict):
        return {}
    table = tool.get("table") if isinstance(tool.get("table"), dict) else {}
    chart = tool.get("chart") if isinstance(tool.get("chart"), dict) else {}
    chart_result = tool.get("chart_result") if isinstance(tool.get("chart_result"), dict) else {}
    profile = tool.get("source_profile") if isinstance(tool.get("source_profile"), dict) else {}
    return {
        "intent": str(tool.get("intent") or "")[:100],
        "action": str(tool.get("action") or "")[:100],
        "feature": str(tool.get("feature") or "")[:80],
        "blocked": bool(tool.get("blocked")),
        "missing": [str(x)[:80] for x in (tool.get("missing") or [])[:8]] if isinstance(tool.get("missing"), list) else [],
        "table_kind": str(table.get("kind") or "")[:80],
        "table_total": table.get("total") if isinstance(table.get("total"), int) else None,
        "chart_status": str(chart.get("status") or "")[:80],
        "chart_kind": str(chart.get("kind") or chart_result.get("kind") or "")[:80],
        "source_type": str(profile.get("suggested_source_type") or "")[:40],
        "source_shape": str(profile.get("metric_shape") or "")[:40],
        "source_grain": str(profile.get("grain") or "")[:40],
    }


def _read_jsonl(path: Path, limit: int = 500) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: deque[str] = deque(maxlen=max(1, min(int(limit or 500), 10000)))
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rows.append(line)
    except Exception as e:
        logger.warning("flowi jsonl read failed (%s): %s", path, e)
        return []
    out: list[dict[str, Any]] = []
    for line in rows:
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _parse_ts(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _feedback_summary_from_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    sorted_records = sorted(
        records,
        key=lambda r: str(r.get("timestamp") or ""),
        reverse=True,
    )
    by_rating = Counter(str(r.get("rating") or "neutral") for r in sorted_records)
    by_user = Counter(str(r.get("username") or "-") for r in sorted_records)
    by_intent = Counter(str(r.get("intent") or "-") for r in sorted_records)
    by_workflow = Counter(str(r.get("expected_workflow") or r.get("workflow") or "-") for r in sorted_records)
    by_tag: Counter[str] = Counter()
    needs_review: list[dict[str, Any]] = []
    for rec in sorted_records:
        tags = _normalize_feedback_tags(rec.get("tags") or rec.get("failure_types") or [], rec.get("rating") or "")
        by_tag.update(tags)
        if rec.get("needs_review") or rec.get("golden_candidate") or str(rec.get("rating") or "") != "up" or any(t != "correct" for t in tags):
            needs_review.append(rec)
    return {
        "total": len(sorted_records),
        "by_rating": dict(by_rating),
        "by_user": dict(by_user.most_common(30)),
        "by_intent": dict(by_intent.most_common(30)),
        "by_workflow": dict(by_workflow.most_common(30)),
        "by_tag": dict(by_tag.most_common(30)),
        "recent": sorted_records,
        "review_queue": needs_review,
    }


def _feedback_to_golden_case(
    rec: dict[str, Any],
    *,
    created_by: str,
    expected_intent: str = "",
    expected_tool: str = "",
    expected_answer: str = "",
    notes: str = "",
) -> dict[str, Any]:
    tool_summary = rec.get("tool_summary") if isinstance(rec.get("tool_summary"), dict) else {}
    tags = _normalize_feedback_tags(rec.get("tags") or [], rec.get("rating") or "")
    forbidden = []
    if "hallucination" in tags:
        forbidden.append("DB/cache/tool 결과에 없는 값을 생성하지 않는다.")
    if "missed_clarification" in tags:
        forbidden.append("필수 slot이 불명확하면 실행 전에 선택지로 되묻는다.")
    if "permission_risk" in tags:
        forbidden.append("일반 user에게 DB/File 원본 수정 권한을 주지 않는다.")
    if "aggregation_error" in tags:
        forbidden.append("INLINE avg, ET median 기본 집계 원칙을 어기지 않는다.")
    if "key_matching_error" in tags:
        forbidden.append("root_lot_id, fab_lot_id, lot_wf, shot key를 명시적으로 확인한다.")
    return {
        "id": "golden_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:8],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "created_by": _safe_username(created_by),
        "source_feedback_id": rec.get("id") or "",
        "prompt": rec.get("prompt_excerpt") or "",
        "expected_intent": (expected_intent or rec.get("intent") or tool_summary.get("intent") or "").strip()[:120],
        "expected_tool": (expected_tool or rec.get("expected_workflow") or tool_summary.get("action") or "").strip()[:160],
        "expected_answer": (expected_answer or rec.get("expected_answer") or rec.get("correct_route") or "").strip()[:4000],
        "must_use_data_refs": (rec.get("data_refs") or "").strip()[:1000],
        "tags": tags,
        "forbidden": forbidden,
        "notes": (notes or rec.get("note") or "").strip()[:2000],
    }


def _profile_context(username: str) -> str:
    md = _read_user_md(username, create=False)
    notes = _notes_from_md(md)
    recent = md[-2500:] if md else ""
    workflow = "\n".join(f"- {item['key']}: {item['rule']}" for item in FLOWI_BASE_WORKFLOW_GUIDE)
    parts = ["기본 workflow/slot 해석 규칙:\n" + workflow]
    if notes:
        parts.append("사용자 메모:\n" + notes[:2500])
    if recent:
        parts.append("최근 Flowi 기록:\n" + recent)
    return "\n\n".join(parts).strip()


def _flowi_agent_guide_md() -> str:
    try:
        if FLOWI_AGENT_GUIDE_FILE.exists():
            text = FLOWI_AGENT_GUIDE_FILE.read_text(encoding="utf-8")
            if text.strip():
                return text.strip()
    except Exception as e:
        logger.warning("flowi agent guide read failed: %s", e)
    return FLOWI_AGENT_GUIDE_FALLBACK.strip()


def _flowi_feature_guide_md(key: str) -> str:
    safe = re.sub(r"[^a-z0-9_-]+", "", str(key or "").lower())
    if not safe:
        return ""
    try:
        fp = FLOWI_AGENT_FEATURE_GUIDE_DIR / f"{safe}.md"
        if fp.exists():
            text = fp.read_text(encoding="utf-8").strip()
            return text
    except Exception as e:
        logger.warning("flowi feature guide read failed key=%s: %s", key, e)
    return ""


def _matched_feature_entrypoints(
    prompt: str,
    limit: int = 4,
    allowed_keys: set[str] | None = None,
) -> list[dict[str, str]]:
    text = str(prompt or "")
    prompt_l = text.lower()
    prompt_u = _upper(text)
    toks = {_upper(t) for t in _tokens(prompt)}
    has_create = any(term in prompt_l or term in text for term in _FLOWI_APP_CREATE_TERMS)
    has_chart = _contains_chart_intent(text) or any(t in prompt_l or t in text for t in ("trend", "추세", "시계열", "그려", "그래프"))
    scored: list[tuple[int, dict[str, str]]] = []
    for item in FLOWI_FEATURE_ENTRYPOINTS:
        if allowed_keys is not None and item["key"] not in allowed_keys:
            continue
        hay = " ".join([item["key"], item["title"], item["description"], item["prompt"]]).lower()
        score = 0
        if item["key"].lower() in prompt_l or item["title"].lower() in prompt_l:
            score += 4
        key = item["key"]
        if key == "dashboard" and has_chart:
            score += 8
        if key == "tracker" and any(t in prompt_l or t in text for t in ("이슈", "issue", "tracker", "트래커", "모니터링", "analysis")):
            score += 7 + (2 if has_create else 0)
        if key == "inform" and any(t in prompt_l or t in text for t in ("인폼", "인폼로그", "inform", "공지", "공유")):
            score += 7 + (2 if has_create else 0)
        if key == "meeting" and any(t in prompt_l or t in text for t in ("회의", "미팅", "meeting", "아젠다", "매주", "매월")):
            score += 7 + (2 if has_create else 0)
        if key == "calendar" and any(t in prompt_l or t in text for t in ("일정", "캘린더", "calendar", "변경점", "schedule")):
            score += 7 + (2 if has_create else 0)
        if key == "splittable" and any(t in prompt_u for t in ("KNOB", "MASK", "PLAN", "ACTUAL", "CUSTOM", "SPLITTABLE", "ML_TABLE")):
            score += 6
        if key == "ettime" and (re.search(r"\bET\b", prompt_u) or any(t in prompt_l or t in text for t in ("elapsed", "median", "wf별", "wafer별", "측정시간"))):
            score += 5
        if key == "filebrowser" and any(t in prompt_l or t in text for t in ("parquet", "csv", "파일", "컬럼", "schema", "스키마")):
            score += 5
        for alias in FLOWI_FEATURE_ALIASES.get(item["key"], []):
            alias_l = alias.lower()
            if alias_l and alias_l in prompt_l:
                score += 3 if len(alias_l) > 2 else 1
        for tok in toks:
            if tok and tok.lower() in hay:
                score += 1
        if score:
            scored.append((score, item))
    if not scored:
        return []
    scored.sort(key=lambda x: x[0], reverse=True)
    return [dict(item) for _, item in scored[:limit]]


def _slot_summary(prompt: str, product: str = "") -> dict[str, Any]:
    classified_lots = _classified_lot_tokens(prompt)
    return {
        "product": _product_hint(prompt, product),
        "lots": _lot_tokens(prompt),
        "root_lot_ids": classified_lots.get("root_lot_ids") or [],
        "fab_lot_ids": classified_lots.get("fab_lot_ids") or [],
        "wafers": _wafer_tokens(prompt),
        "steps": _step_tokens(prompt),
        "terms": _query_tokens(prompt)[:12],
    }


def _flowi_product_resolution(prompt: str, explicit: str = "") -> dict[str, Any]:
    product = _product_hint(prompt, explicit)
    configured = _configured_product_names()
    source = "missing"
    if explicit:
        source = "explicit"
    elif product and _upper(product) in configured:
        source = "product_config_or_data"
    elif product:
        source = "prompt_token"
    return {
        "value": product,
        "source": source,
        "configured_products": sorted(set(configured.values()), key=lambda x: x.casefold())[:80],
    }


def _flowi_source_type_tokens(prompt: str) -> list[str]:
    text = str(prompt or "")
    up = _upper(text)
    out: list[str] = []
    for key, aliases in {
        "FAB": ["FAB", "ROUTE", "PROGRESS", "CURRENT", "현재", "진행", "공정"],
        "ET": ["ET", "WAT", "PARAMETRIC"],
        "INLINE": ["INLINE", "인라인", "METROLOGY"],
        "VM": ["VM", "VMIN", "SRAM"],
        "EDS": ["EDS", "SORT", "BIN", "YIELD"],
        "QTIME": ["QTIME", "QUEUE", "대기시간", "큐타임"],
        "ML_TABLE": ["ML_TABLE", "KNOB", "노브", "PLAN", "ACTUAL"],
    }.items():
        if any(alias in up or alias in text for alias in aliases):
            out.append(key)
    return out


def _flowi_inform_modules() -> list[str]:
    mods: list[str] = []
    seen: set[str] = set()

    def add(raw: Any) -> None:
        mod = str(raw or "").strip()
        if not mod:
            return
        key = mod.casefold()
        if key in seen:
            return
        seen.add(key)
        mods.append(mod)

    for mod in _FLOWI_DEFAULT_INFORM_MODULES:
        add(mod)
    try:
        raw = _admin_settings().get("inform_user_modules")
        if isinstance(raw, dict):
            for values in raw.values():
                if isinstance(values, list):
                    for mod in values:
                        add(mod)
    except Exception:
        pass
    try:
        from routers import informs as informs_router
        cfg = informs_router._load_config()
        for mod in cfg.get("modules") or []:
            add(mod)
    except Exception:
        pass
    return mods


def _flowi_module_alias_pairs() -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for module in _flowi_inform_modules():
        pairs.append((module, module))
    for module, aliases in _MODULE_ALIAS.items():
        pairs.append((module, module))
        for alias in aliases:
            pairs.append((module, alias))
    pairs.sort(key=lambda x: len(x[1]), reverse=True)
    return pairs


def _flowi_module_token(prompt: str) -> str:
    text = str(prompt or "")
    low = text.lower()
    up = _upper(text)
    explicit = _flowi_prompt_field(text, ("module", "모듈")) if "_flowi_prompt_field" in globals() else ""
    if explicit:
        explicit_clean = explicit.strip()
        for module, alias in _flowi_module_alias_pairs():
            if explicit_clean.lower() == str(alias or "").strip().lower():
                return module
        return explicit_clean
    for module, alias in _flowi_module_alias_pairs():
        alias_s = str(alias or "").strip()
        if not alias_s:
            continue
        if re.fullmatch(r"[A-Za-z0-9_. -]+", alias_s):
            if re.search(rf"(?<![A-Za-z0-9_.-]){re.escape(alias_s)}(?![A-Za-z0-9_.-])", text, flags=re.I):
                return module
        elif alias_s in text or alias_s.lower() in low or _upper(alias_s) in up:
            return module
    return ""


def _flowi_func_step_token(prompt: str) -> str:
    text = str(prompt or "")
    m = re.search(r"(\d+\.\d+)\s+([A-Z][A-Z0-9_/]*)", text, flags=re.I)
    if not m:
        return ""
    return f"{m.group(1)} {m.group(2).upper()}"


def _flowi_metric_agg(prompt: str) -> str:
    text = str(prompt or "")
    low = text.lower()
    if any(t in low or t in text for t in ("avg", "average", "mean", "평균")):
        return "avg"
    if any(t in low or t in text for t in ("median", "중앙값")):
        return "median"
    if re.search(r"(?<![A-Za-z])min(?![A-Za-z])", low):
        return "min"
    if re.search(r"(?<![A-Za-z])max(?![A-Za-z])", low):
        return "max"
    if re.search(r"(?<![A-Za-z])std(?![A-Za-z])", low):
        return "std"
    return "median"


def _flowi_metric_token(prompt: str) -> str:
    text = str(prompt or "")
    step = _flowi_func_step_token(text)
    if step:
        pos = _upper(text).find(_upper(step))
        tail = text[pos + len(step):] if pos >= 0 else ""
        m = re.search(r"\b([A-Za-z][A-Za-z0-9_/]*(?:\s+(?:Avg|AVG|Average|Mean|Median|Min|Max|Std))?)\b", tail)
        if m:
            metric = " ".join(m.group(1).split())
            if _upper(metric) not in _STOP_TOKENS and _upper(metric) not in {"SPLIT", "KNOB", "MASK"}:
                return metric
        step_mod = step.split(" ", 1)[1] if " " in step else ""
        if step_mod and any(t in text.lower() for t in ("avg", "average", "mean", "평균")):
            return f"{step_mod} Avg"
    hits = _metric_alias_hits(text)
    if hits:
        return str(hits[0].get("metric") or "")
    return ""


def _flowi_knob_value_token(prompt: str) -> str:
    text = str(prompt or "")
    for pat in (
        r"\b(PPID_\d+_\d+)\b",
        r"(?:knob_value|KNOB_VALUE|값|value)\s*[:=]\s*([A-Za-z0-9_.-]+)",
        r"\b([A-Za-z0-9_.-]+)\s*인\s*자재",
    ):
        m = re.search(pat, text, flags=re.I)
        if m:
            raw = (m.group(1) or "").strip(" .,;:()[]{}")
            if raw and _upper(raw) not in _STOP_TOKENS:
                return raw
    return ""


def _flowi_group_token(prompt: str) -> str:
    up = _upper(prompt)
    for group in ("KNOB", "MASK", "INLINE", "VM", "EDS", "FAB"):
        if re.search(rf"(?<![A-Z0-9_]){group}(?![A-Z0-9_])", up):
            return group
    if "노브" in str(prompt or ""):
        return "KNOB"
    return ""


def _flowi_split_set_token(prompt: str) -> str:
    text = str(prompt or "")
    patterns = [
        r"([^\s,;:/=]+)\s*스플릿(?:으로)?\s*선택",
        r"([^\s,;:/=]+)\s*split(?:으로)?\s*선택",
        r"(?:split|split_set|스플릿)\s*[:=]\s*([^\s,;]+)",
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.I)
        if m:
            return (m.group(1) or "").strip(" .,;:()[]{}")
    return ""


def _flowi_note_extract(prompt: str) -> str:
    text = str(prompt or "")
    for pat in (
        r"(?:내용은|내용\s*[:=])\s*[\"']?(.+?)[\"']?\s*$",
        r"(?:사유는|사유\s*[:=])\s*[\"']?(.+?)[\"']?\s*$",
    ):
        m = re.search(pat, text, flags=re.I | re.S)
        if m:
            return re.sub(r"\s+", " ", (m.group(1) or "").strip(" \t\r\n\"'")).strip()[:1000]
    return ""


def _flowi_parse_inform_batch_entries(prompt: str) -> list[dict[str, Any]]:
    text = str(prompt or "")
    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    aliases = []
    for module, alias in _flowi_module_alias_pairs():
        alias_s = str(alias or "").strip()
        if alias_s:
            aliases.append((module, alias_s))
    aliases.sort(key=lambda x: len(x[1]), reverse=True)
    if not aliases:
        return []
    mod_pat = "|".join(re.escape(alias) for _module, alias in aliases)
    pattern = re.compile(rf"(?P<module>{mod_pat})\s*(?:는|은|:|=)\s*(?P<split>[A-Za-z0-9_.-]+)", re.I)
    for m in pattern.finditer(text):
        raw_mod = m.group("module") or ""
        module = next((mod for mod, alias in aliases if alias.lower() == raw_mod.lower()), raw_mod)
        split = (m.group("split") or "").strip(" .,;:()[]{}")
        key = (module, split)
        if module and key not in seen:
            seen.add(key)
            entries.append({"module": module, "split_set": split})
    return entries


def _flowi_preview_limit(prompt: str, default: int = 100) -> int:
    text = str(prompt or "")
    m = re.search(r"(?:최근|top)?\s*(\d{1,4})\s*(?:행|row|rows)", text, flags=re.I)
    if not m:
        return default
    try:
        return max(1, min(500, int(m.group(1))))
    except Exception:
        return default


def _flowi_knob_tokens(prompt: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for tok in _tokens(prompt):
        key = _upper(tok)
        if "KNOB" in key and key not in seen:
            seen.add(key)
            out.append(key)
    if not out and re.search(r"\b[A-Z]\s+KNOB\b", _upper(prompt)):
        out.append("KNOB_" + re.search(r"\b([A-Z])\s+KNOB\b", _upper(prompt)).group(1))
    return out


def _flowi_invalid_wafer_mentions(prompt: str) -> list[str]:
    text = str(prompt or "")
    invalid: set[str] = set()
    patterns = [
        r"#\s*(\d{1,4})",
        r"\b(?:WF|WAFER|SLOT)\s*0?(\d{1,4})\b",
        r"(?:웨이퍼|슬롯)\s*0?(\d{1,4})",
        r"0?(\d{1,4})\s*번\s*(?:WF|WAFER|웨이퍼|SLOT|슬롯|장)",
        r"0?(\d{1,4})\s*번장",
        r"0?(\d{1,4})\s*장\b",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text, flags=re.I):
            try:
                n = int(m.group(1))
            except Exception:
                continue
            if n < 1 or n > FLOWI_MAX_WAFER_ID:
                invalid.add(str(n))
    return sorted(invalid, key=lambda x: int(x))


def _flowi_recent_lots(limit: int = 3) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []

    def add_from_text(text: str) -> None:
        for lot in _lot_tokens(text):
            if lot not in seen:
                seen.add(lot)
                out.append(lot)

    try:
        for rec in reversed(_read_jsonl(FLOWI_ACTIVITY_FILE, limit=300)):
            fields = rec.get("fields") if isinstance(rec.get("fields"), dict) else {}
            add_from_text(" ".join(str(v) for v in fields.values()))
            if len(out) >= limit:
                return out[:limit]
    except Exception:
        pass
    try:
        for fp in sorted(FLOWI_USER_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:8]:
            add_from_text(fp.read_text(encoding="utf-8")[-4000:])
            if len(out) >= limit:
                break
    except Exception:
        pass
    return out[:limit]


def _flowi_product_choice_values(limit: int = 3) -> list[str]:
    products = sorted(set(_configured_product_names().values()), key=lambda x: x.casefold())
    return products[:limit]


def _flowi_step_choice_values(product: str = "", limit: int = 3) -> list[str]:
    vals: list[str] = []
    seen: set[str] = set()
    for val in ["24.0 SORT", "16.0 VIA2", "1.0 STI", "8.0 SD_EPI"]:
        seen.add(_upper(val))
        vals.append(val)
    for val in _known_func_step_names():
        if _upper(val) not in seen:
            seen.add(_upper(val))
            vals.append(val)
    return vals[:limit]


def _flowi_split_set_choice_values(limit: int = 3) -> list[str]:
    vals: list[str] = []
    seen: set[str] = set()
    try:
        split_dir = PATHS.data_root / "splittable"
        for fp in sorted(split_dir.glob("*.json"))[:20]:
            data = load_json(fp, {})
            text = json.dumps(data, ensure_ascii=False)[:30000]
            for m in re.finditer(r"\b(test[A-Za-z0-9_.-]*)\b", text, flags=re.I):
                val = m.group(1)
                key = val.lower()
                if key not in seen:
                    seen.add(key)
                    vals.append(val)
    except Exception:
        pass
    for val in ("test1", "test2", "test3"):
        if val not in seen:
            seen.add(val)
            vals.append(val)
    return vals[:limit]


def _flowi_choice(field: str, idx: int, label: str, value: str, *, prompt_prefix: str = "") -> dict[str, Any]:
    title = label or value
    return {
        "id": str(idx),
        "label": str(idx),
        "title": title,
        "value": value,
        "recommended": idx == 1,
        "description": f"{field}={value} 로 이어서 진행",
        "prompt": (prompt_prefix + " " + value).strip() if prompt_prefix else value,
    }


def _flowi_arguments_choices(missing: list[str], prompt: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    args = arguments if isinstance(arguments, dict) else {}
    fields: list[dict[str, Any]] = []
    for field in missing:
        key = str(field or "")
        values: list[str] = []
        placeholder = ""
        if key == "product":
            values = _flowi_product_choice_values(3)
            placeholder = "다른 제품 입력"
        elif key in {"root_lot_ids", "root_lot_id", "lot_ids", "fab_lot_ids", "root_lot_id_or_fab_lot_id"}:
            values = _flowi_recent_lots(3)
            placeholder = "lot 직접 입력"
        elif key == "module":
            values = _flowi_inform_modules()[:3]
            placeholder = "다른 모듈 입력"
        elif key == "step":
            values = _flowi_step_choice_values(str(args.get("product") or ""), 3)
            placeholder = "step 직접 입력"
        elif key in {"metric", "metrics_or_items"}:
            values = ["DIBL", "SS", "CD", "VIA2 Avg", "LKG"][:3]
            placeholder = "다른 항목 입력"
        elif key == "knob_value":
            values = []
            placeholder = "값 직접 입력"
        elif key == "source_type":
            values = ["FAB", "ET", "INLINE"]
            placeholder = "source 직접 입력"
        elif key == "split_set":
            values = _flowi_split_set_choice_values(3)
            placeholder = "직접 입력"
        elif key == "note":
            values = []
            placeholder = "메모 직접 입력"
        elif key == "entries":
            values = []
            placeholder = "예: GATE는 test1 STI는 test2"
        else:
            placeholder = f"{key} 직접 입력"
        choices = [_flowi_choice(key, i + 1, f"{v}로 진행", v, prompt_prefix=prompt) for i, v in enumerate(values[:3])]
        if placeholder:
            choices.append({
                "id": "free",
                "label": "직접",
                "title": placeholder,
                "value": "",
                "recommended": not choices,
                "description": "자유 입력으로 값을 이어서 입력합니다.",
                "free_input": True,
                "prompt": prompt,
            })
        fields.append({"field": key, "choices": choices[:4], "free_input_label": placeholder})
    return {"message": "또는 직접 입력해 주세요", "fields": fields}


def _flowi_reason(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())[:80]


def _flowi_complete_json(messages: list[dict[str, Any]], schema_dict: dict[str, Any], *, max_retries: int = 1) -> dict[str, Any] | None:
    if not llm_adapter.is_available():
        return None
    keys = list((schema_dict or {}).get("properties", {}).keys()) or list((schema_dict or {}).get("keys", []))
    required = list((schema_dict or {}).get("required", []))
    system = (
        _flowi_system_prompt(include_few_shots=True)
        + "\n\nReturn only a single JSON object matching the schema. No prose, no code fences."
    )
    prompt = json.dumps({"messages": messages, "schema": schema_dict}, ensure_ascii=False, default=str)
    last_error = ""
    for attempt in range(max(0, int(max_retries or 0)) + 1):
        ask_prompt = prompt
        if attempt and last_error:
            ask_prompt = (
                f"이전 응답이 schema에 안 맞다. {last_error}. "
                f"정확히 다음 키만 있는 JSON 객체로 다시 응답해라: {keys}.\n"
                + prompt
            )
        try:
            out = llm_adapter.complete(ask_prompt, system=system, timeout=8)
        except Exception as e:
            last_error = str(e)
            continue
        if not out.get("ok") or not out.get("text"):
            last_error = str(out.get("error") or "empty")
            continue
        raw = str(out.get("text") or "").strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I | re.S).strip()
        try:
            obj = json.loads(raw)
        except Exception as e:
            last_error = f"json parse error: {e}"
            continue
        if not isinstance(obj, dict):
            last_error = "not object"
            continue
        missing = [k for k in required if k not in obj]
        if missing:
            last_error = "missing " + ", ".join(missing)
            continue
        if keys:
            obj = {k: obj.get(k) for k in keys if k in obj}
        return obj
    return None


def _flowi_infer_function_call(prompt: str, slots: dict[str, Any]) -> dict[str, Any]:
    text = str(prompt or "")
    up = _upper(text)
    entries = _matched_feature_entrypoints(text, limit=3)
    assignments, invalid_wafers = _flowi_parse_splittable_plan_assignments(text)
    lots = list(slots.get("lots") or [])
    root_lots = list(slots.get("root_lot_ids") or [])
    fab_lots = list(slots.get("fab_lot_ids") or [])
    wafers = list(slots.get("wafers") or [])
    step = _flowi_func_step_token(text) or (slots.get("steps") or [""])[0]
    metric = _flowi_metric_token(text)
    knob_value = _flowi_knob_value_token(text)
    module = _flowi_module_token(text)
    source_types = _flowi_source_type_tokens(text)
    mail_terms = ("메일", "통보", "알림", "공지", "보내", "발송", "mail", "notice", "notify")
    inform_terms = ("인폼", "inform", "등록", "기록", "남겨", "올려")
    batch_entries = _flowi_parse_inform_batch_entries(text)
    if _is_current_fab_lot_prompt(text):
        return {
            "name": "query_current_fab_lot_from_fab_db",
            "feature": "filebrowser",
            "intent": "current_fab_lot_lookup",
            "confidence": 0.94,
            "reason": _flowi_reason("현재 fab_lot_id 조회로 판단"),
            "requires_confirmation": False,
            "side_effect": "none",
        }
    if assignments and ("PLAN" in up or "계획" in text) and ("KNOB" in up or "노브" in text):
        return {
            "name": "preview_splittable_plan_update",
            "feature": "splittable",
            "intent": "splittable_plan_confirm",
            "confidence": 0.9,
            "reason": _flowi_reason("KNOB plan 변경 초안 확인"),
            "requires_confirmation": True,
            "side_effect": "confirm_before_write",
            "invalid_wafers": invalid_wafers,
        }
    if any(t in text or t in up for t in ("인폼전체", "전체 작성", "전부 작성", "다 작성", "통째로", "모든 모듈")):
        return {
            "name": "register_inform_walkthrough",
            "feature": "inform",
            "intent": "inform_walkthrough_start",
            "confidence": 0.9,
            "reason": _flowi_reason("모듈별 인폼 전체 작성 흐름 시작"),
            "requires_confirmation": True,
            "side_effect": "confirm_before_write",
        }
    if step and metric and (root_lots or fab_lots or lots) and any(t in text.lower() or t in text for t in ("avg", "median", "평균", "중앙값", "얼마", "몇이야", "측정값")):
        return {
            "name": "query_metric_at_step",
            "feature": "filebrowser",
            "intent": "metric_at_step_lookup",
            "confidence": 0.83,
            "reason": _flowi_reason("lot/step/metric 측정값 조회"),
            "requires_confirmation": False,
            "side_effect": "none",
        }
    if (wafers or invalid_wafers) and step and (root_lots or fab_lots or lots) and any(t in text.lower() or t in text for t in ("split", "스플릿", "진행", "뭘로", "뭐 했", "적용된")):
        return {
            "name": "query_wafer_split_at_step",
            "feature": "splittable",
            "intent": "wafer_split_at_step",
            "confidence": 0.85,
            "reason": _flowi_reason("wafer step split 조회"),
            "requires_confirmation": False,
            "side_effect": "none",
        }
    if step and knob_value and not (root_lots or fab_lots or lots) and any(t in text or t in text.lower() for t in ("인 자재", "가장 빠", "어디에 있", "어디", "진행 중", "받은 lot", "lot")):
        return {
            "name": "find_lots_by_knob_value",
            "feature": "splittable",
            "intent": "knob_value_lot_search",
            "confidence": 0.85,
            "reason": _flowi_reason("step/KNOB value 역검색"),
            "requires_confirmation": False,
            "side_effect": "none",
        }
    if "KNOB" in up or "노브" in text:
        return {
            "name": "query_lot_knobs_from_ml_table",
            "feature": "splittable",
            "intent": "lot_knobs",
            "confidence": 0.82,
            "reason": _flowi_reason("KNOB/MASK 구성 조회"),
            "requires_confirmation": False,
            "side_effect": "none",
        }
    if any(t in text.lower() or t in text for t in mail_terms):
        return {
            "name": "compose_inform_module_mail",
            "feature": "inform",
            "intent": "inform_module_mail_preview",
            "confidence": 0.88,
            "reason": _flowi_reason("모듈 인폼 메일 미리보기"),
            "requires_confirmation": True,
            "side_effect": "confirm_before_write",
        }
    has_inform_word = "인폼" in text or "inform" in text.lower()
    if any(t in text.lower() or t in text for t in inform_terms) and (has_inform_word or module or batch_entries):
        is_batch = len(batch_entries) >= 2 or any(t in text for t in ("다 만들어", "전부 등록", "각각", "다 등록"))
        return {
            "name": "register_inform_log",
            "feature": "inform",
            "intent": "inform_log_batch_draft" if is_batch else "inform_log_draft",
            "confidence": 0.84 if is_batch else 0.78,
            "reason": _flowi_reason("인폼 로그 등록 전 확인"),
            "requires_confirmation": True,
            "side_effect": "confirm_before_write",
        }
    if _contains_chart_intent(text):
        return {
            "name": "build_dashboard_metric_chart",
            "feature": "dashboard",
            "intent": "dashboard_chart_request",
            "confidence": 0.8,
            "reason": _flowi_reason("Dashboard 차트 요청"),
            "requires_confirmation": False,
            "side_effect": "none",
        }
    if (("FAB" in up) or root_lots or fab_lots or lots) and any(t in text or t in up for t in ("현재", "진행", "공정", "STEP", "어디")):
        return {
            "name": "query_fab_progress",
            "feature": "filebrowser",
            "intent": "fab_progress_lookup",
            "confidence": 0.74,
            "reason": _flowi_reason("FAB 진행 위치 조회"),
            "requires_confirmation": False,
            "side_effect": "none",
        }
    preview_show = (
        "보여줘" in text
        and any(s in {"FAB", "ET", "INLINE", "VM", "EDS"} for s in source_types)
        and not any(t in text.lower() or t in text for t in ("스플릿테이블", "split table", "splittable"))
    )
    if (preview_show or any(t in text.lower() or t in text for t in ("파일", "preview", "db", "row", "schema", "최근", "latest", "파일탐색기", "파일 탐색기"))) and source_types:
        return {
            "name": "preview_filebrowser_data",
            "feature": "filebrowser",
            "intent": "filebrowser_data_preview",
            "confidence": 0.75,
            "reason": _flowi_reason("파일/DB row preview"),
            "requires_confirmation": False,
            "side_effect": "none",
        }
    if any(t in text.lower() or t in text for t in ("컬럼", "찾아", "검색", "어떤 column", "있는지", "schema")):
        return {
            "name": "search_filebrowser_schema",
            "feature": "filebrowser",
            "intent": "filebrowser_schema_search",
            "confidence": 0.7,
            "reason": _flowi_reason("파일/DB schema 컬럼 검색"),
            "requires_confirmation": False,
            "side_effect": "none",
        }
    if entries:
        primary = entries[0]
        return {
            "name": "route_flowi_feature",
            "feature": primary.get("key") or "",
            "intent": f"{primary.get('key')}_guidance",
            "confidence": 0.62,
            "reason": f"{primary.get('title') or primary.get('key')} feature keyword match",
            "requires_confirmation": False,
            "side_effect": "none",
        }
    return {
        "name": "route_flowi_feature",
        "feature": "diagnosis",
        "intent": "semiconductor_analysis_request",
        "confidence": 0.45,
        "reason": "명확한 tool trigger가 없어 반도체 분석/RCA 기본 라우터로 전달",
        "requires_confirmation": False,
        "side_effect": "none",
    }


def _flowi_function_schema(name: str) -> dict[str, Any]:
    schemas = {
        "query_current_fab_lot_from_fab_db": {
            "description": "FAB DB에서 product/root_lot_id/fab_lot_id/wafer_id 조건으로 최신 fab_lot_id를 조회한다.",
            "required": ["product", "lot_ids"],
        },
        "preview_splittable_plan_update": {
            "description": "SplitTable plan 변경안을 저장 전 확인용 JSON으로 만든다. 원본 DB는 수정하지 않는다.",
            "required": ["product", "root_lot_ids", "plan_assignments"],
        },
        "query_lot_knobs_from_ml_table": {
            "description": "ML_TABLE/SplitTable에서 lot wafer별 KNOB/MASK 값을 조회한다. step/group 필터를 지원한다.",
            "required": ["product", "lot_ids"],
        },
        "compose_inform_module_mail": {
            "description": "모듈 담당자 인폼 메일을 저장/발송 전 미리보기로 구성한다.",
            "required": ["root_lot_ids 또는 fab_lot_ids", "module"],
        },
        "register_inform_log": {
            "description": "인폼 로그 단일 또는 batch 등록 초안을 만들고 확인 전에는 저장하지 않는다.",
            "required": ["root_lot_ids 또는 fab_lot_ids"],
        },
        "preview_filebrowser_data": {
            "description": "FileBrowser source_type/product 조건으로 최근 row를 read-only preview 한다.",
            "required": ["source_type", "product"],
        },
        "search_filebrowser_schema": {
            "description": "FileBrowser/FAB/ET/INLINE/VM/EDS schema 컬럼을 keyword로 검색한다.",
            "required": ["keyword"],
        },
        "query_wafer_split_at_step": {
            "description": "특정 wafer가 특정 function step에서 받은 KNOB/MASK 조합(split)을 조회한다.",
            "required": ["root_lot_ids 또는 fab_lot_ids", "wafer_ids", "step"],
        },
        "find_lots_by_knob_value": {
            "description": "특정 step에서 특정 KNOB value를 받은 lot/wafer를 찾아 FAB 진행 위치와 join한다.",
            "required": ["step", "knob_value"],
        },
        "query_metric_at_step": {
            "description": "lot/wafer/function step 조건에서 ET/INLINE 측정 metric을 집계한다.",
            "required": ["root_lot_ids 또는 fab_lot_ids", "step", "metric"],
        },
        "register_inform_walkthrough": {
            "description": "모듈별 인폼 전체 작성 multi-turn walkthrough를 시작/진행/확인한다.",
            "required": ["root_lot_ids"],
        },
        "build_dashboard_metric_chart": {
            "description": "ET/INLINE/VM/EDS/FAB 데이터를 읽어 Dashboard 차트용 query arguments를 만든다.",
            "required": ["product", "metrics_or_items"],
        },
        "query_fab_progress": {
            "description": "FAB route/progress DB에서 현재 step, fab lot, 시간 이력을 조회한다.",
            "required": ["lot_ids"],
        },
        "route_flowi_feature": {
            "description": "feature 후보와 slots를 바탕으로 Flow-i 기본 라우터에 전달한다.",
            "required": [],
        },
    }
    return schemas.get(name, schemas["route_flowi_feature"])


def _structure_flowi_function_call(prompt: str, product: str = "", max_rows: int = 12) -> dict[str, Any]:
    text = str(prompt or "").strip()
    product_info = _flowi_product_resolution(text, product)
    resolved_product = str(product_info.get("value") or "")
    slots = _slot_summary(text, resolved_product)
    classified = _classified_lot_tokens(text)
    wafers = [int(w) for w in _wafer_tokens(text)]
    assignments, invalid_wafers = _flowi_parse_splittable_plan_assignments(text)
    invalid_wafers = sorted(set(invalid_wafers + _flowi_invalid_wafer_mentions(text)), key=lambda x: int(x))
    metrics = _metric_alias_hits(text)
    selected = _flowi_infer_function_call(text, slots)
    selected_name = str(selected.get("name") or "")
    if float(selected.get("confidence") or 0) < 0.5:
        polished = _flowi_complete_json(
            [{"role": "user", "content": text}],
            {
                "keys": ["function", "arguments"],
                "required": ["function", "arguments"],
                "properties": {"function": {"type": "string"}, "arguments": {"type": "object"}},
            },
            max_retries=1,
        )
        if polished and isinstance(polished.get("arguments"), dict):
            name = str(polished.get("function") or "")
            if name:
                selected.update({
                    "name": name,
                    "intent": f"{name}_llm_polish",
                    "confidence": 0.65,
                    "reason": _flowi_reason((selected.get("reason") or "LLM 보조 구조화") + " (LLM polish)"),
                })
                selected_name = name
    if invalid_wafers:
        selected["invalid_wafers"] = invalid_wafers
    source_types = _flowi_source_type_tokens(text)
    if selected_name == "query_current_fab_lot_from_fab_db" and "FAB" not in source_types:
        source_types.insert(0, "FAB")
    if selected_name == "build_dashboard_metric_chart" and not source_types:
        source_types = ["ET", "INLINE"]
    metric_names = [m.get("metric") for m in metrics] if selected_name == "build_dashboard_metric_chart" else []
    plan_assignments = assignments if selected_name == "preview_splittable_plan_update" else []
    step = _flowi_func_step_token(text) or ((slots.get("steps") or [""])[0] if slots.get("steps") else "")
    group = _flowi_group_token(text)
    metric = _flowi_metric_token(text)
    agg = _flowi_metric_agg(text)
    module = _flowi_module_token(text)
    split_set = _flowi_split_set_token(text)
    note = _flowi_note_extract(text)
    knob_value = _flowi_knob_value_token(text)
    batch_entries = _flowi_parse_inform_batch_entries(text)
    source_type = next((s for s in source_types if s in {"FAB", "INLINE", "ET", "VM", "EDS"}), "")
    keyword = ""
    if selected_name == "search_filebrowser_schema":
        blocked = {"컬럼", "찾아", "검색", "어떤", "column", "있는지", "schema", "스키마"}
        for tok in _query_tokens(text):
            if tok.lower() not in blocked and _upper(tok) not in {"FAB", "INLINE", "ET", "VM", "EDS", "DB"}:
                keyword = tok
                break

    arguments = {
        "product": resolved_product,
        "product_source": product_info.get("source"),
        "root_lot_ids": classified.get("root_lot_ids") or [],
        "fab_lot_ids": classified.get("fab_lot_ids") or [],
        "lot_ids": slots.get("lots") or [],
        "wafer_ids": wafers,
        "step_ids": slots.get("steps") or [],
        "source_types": source_types,
        "metrics_or_items": metric_names,
        "knobs": _flowi_knob_tokens(text),
        "plan_assignments": plan_assignments,
        "aggregations": {"ET": "median", "INLINE": "avg", "FAB": "latest"},
        "join_keys": ["root_lot_id", "fab_lot_id", "wafer_id", "lot_wf"],
        "max_rows": max(1, min(int(max_rows or 12), 200)),
        "read_only": True,
        "side_effect": selected.get("side_effect") or "none",
    }
    if step:
        arguments["step"] = step
    if group:
        arguments["group"] = group
    if metric and selected_name == "query_metric_at_step":
        arguments["metric"] = metric
        arguments["agg"] = agg
    if knob_value:
        arguments["knob_value"] = knob_value
        arguments["sort"] = "earliest_progress" if any(t in text for t in ("가장 빠", "제일 빠", "빠른")) else "latest_progress"
    if module:
        arguments["module"] = module
    if split_set:
        arguments["split_set"] = split_set
    if note:
        arguments["note"] = note
    if selected_name == "register_inform_log" and "_flowi_prompt_field" in globals():
        reason_val = _flowi_prompt_field(text, ("reason", "사유"))
        if reason_val:
            arguments["reason"] = reason_val
    if batch_entries and (selected_name == "register_inform_log" or len(batch_entries) >= 2):
        arguments["entries"] = batch_entries
        arguments["mode"] = "batch"
    if selected_name == "preview_filebrowser_data":
        arguments["source_type"] = source_type
        arguments["limit"] = _flowi_preview_limit(text, 100)
    if selected_name == "search_filebrowser_schema":
        arguments["source_type"] = source_type or None
        arguments["keyword"] = keyword or metric or knob_value or ""
    if selected_name == "compose_inform_module_mail":
        arguments["lot_count"] = len(arguments["root_lot_ids"] or arguments["fab_lot_ids"] or arguments["lot_ids"])
        reason = _flowi_note_extract(text) or _flowi_prompt_field(text, ("reason", "사유")) if "_flowi_prompt_field" in globals() else ""
        if reason:
            arguments["reason"] = reason
    if selected_name == "register_inform_walkthrough":
        arguments["action"] = "start"
    schema = _flowi_function_schema(str(selected.get("name") or ""))
    missing: list[str] = []
    required = list(schema.get("required", []) or [])
    if "product" in required and not resolved_product:
        missing.append("product")
    if "lot_ids" in required and not arguments["lot_ids"]:
        missing.append("root_lot_id_or_fab_lot_id")
    if "root_lot_ids" in required and not arguments["root_lot_ids"]:
        missing.append("root_lot_ids")
    if "root_lot_ids 또는 fab_lot_ids" in required and not (arguments["root_lot_ids"] or arguments["fab_lot_ids"]):
        missing.append("root_lot_ids" if not arguments["fab_lot_ids"] else "fab_lot_ids")
    if "plan_assignments" in required and not arguments["plan_assignments"]:
        missing.append("plan_assignments")
    if "metrics_or_items" in required and not arguments["metrics_or_items"]:
        missing.append("metrics_or_items")
    if "module" in required and not arguments.get("module"):
        missing.append("module")
    if "wafer_ids" in required and not arguments["wafer_ids"]:
        missing.append("wafer_ids")
    if "step" in required and not arguments.get("step"):
        missing.append("step")
    if "metric" in required and not arguments.get("metric"):
        missing.append("metric")
    if "knob_value" in required and not arguments.get("knob_value"):
        missing.append("knob_value")
    if "source_type" in required and not arguments.get("source_type"):
        missing.append("source_type")
    if "keyword" in required and not arguments.get("keyword"):
        missing.append("keyword")
    if selected_name == "query_wafer_split_at_step" and not resolved_product:
        missing.insert(0, "product")
    if selected_name == "compose_inform_module_mail" and "module" in missing:
        missing = ["module"]
    missing = list(dict.fromkeys(missing))
    warnings: list[str] = []
    if invalid_wafers:
        warnings.append(", ".join(f"{w}번 wafer는 유효하지 않아요. 1~25만 처리 가능." for w in invalid_wafers))
    if "INLINE" in source_types:
        warnings.append("raw INLINE에는 shot_x/shot_y가 없으며 subitem_id 또는 explicit matching table이 필요합니다.")
    if not resolved_product:
        warnings.append("product를 찾지 못하면 YAML/product directory 기준 후보 선택이 필요합니다.")
    selected["reason"] = _flowi_reason(selected.get("reason") or "")
    arguments_choices = _flowi_arguments_choices(missing, text, arguments) if missing else {}
    validation = {
        "valid": not missing,
        "missing": missing,
        "warnings": warnings,
        "requires_confirmation": bool(selected.get("requires_confirmation")),
        "raw_db_policy": "read_only",
    }
    return {
        "ok": True,
        "mode": "dry_run",
        "prompt": text,
        "persona": FLOWI_AGENT_PERSONA,
        "naming_rules": FLOWI_NAMING_RULES,
        "selected_function": selected,
        "function_schema": schema,
        "function_call": {
            "type": "function_call",
            "function": {
                "name": selected.get("name"),
                "arguments": arguments,
            },
        },
        "validation": validation,
        "arguments_choices": arguments_choices,
        "free_input_hint": "또는 직접 입력해 주세요" if missing else "",
        "feature_candidates": _matched_feature_entrypoints(text, limit=3),
        "slot_summary": slots,
    }


def _flowi_preview_tool(preview: dict[str, Any], *, answer: str = "") -> dict[str, Any]:
    selected = preview.get("selected_function") if isinstance(preview.get("selected_function"), dict) else {}
    function = (preview.get("function_call") or {}).get("function") if isinstance(preview.get("function_call"), dict) else {}
    args = function.get("arguments") if isinstance(function, dict) else {}
    args = args if isinstance(args, dict) else {}
    validation = preview.get("validation") if isinstance(preview.get("validation"), dict) else {}
    missing = validation.get("missing") if isinstance(validation.get("missing"), list) else []
    rows = [{"field": k, "value": json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list)) else v} for k, v in args.items() if v not in (None, "", [], {})]
    tool = {
        "handled": True,
        "intent": selected.get("intent") or selected.get("name") or "flowi_function_preview",
        "action": selected.get("name") or "",
        "feature": selected.get("feature") or "",
        "answer": answer or ("필수값을 보완하면 바로 진행할 수 있습니다." if missing else "요청을 실행 전 구조화했습니다."),
        "requires_confirmation": bool(selected.get("requires_confirmation")),
        "side_effect": selected.get("side_effect") or "none",
        "missing": missing,
        "arguments": args,
        "arguments_choices": preview.get("arguments_choices") or {},
        "validation": validation,
        "slots": {
            "product": args.get("product") or "",
            "root_lot_ids": args.get("root_lot_ids") or [],
            "fab_lot_ids": args.get("fab_lot_ids") or [],
            "wafer_ids": args.get("wafer_ids") or [],
            "step": args.get("step") or "",
            "module": args.get("module") or "",
        },
        "table": {
            "kind": "flowi_function_arguments",
            "title": selected.get("name") or "Flowi function arguments",
            "placement": "below",
            "columns": _table_columns(["field", "value"]),
            "rows": rows,
            "total": len(rows),
        },
    }
    choices_fields = (preview.get("arguments_choices") or {}).get("fields") if isinstance(preview.get("arguments_choices"), dict) else []
    if choices_fields:
        first = choices_fields[0] if isinstance(choices_fields[0], dict) else {}
        choices = first.get("choices") if isinstance(first.get("choices"), list) else []
        tool["clarification"] = {
            "question": f"{first.get('field') or '필수값'} 값을 선택하거나 직접 입력해 주세요.",
            "choices": [c for c in choices if not c.get("free_input")][:3],
        }
    return tool


def _unit_feature_guidance(
    prompt: str,
    product: str = "",
    max_rows: int = 12,
    allowed_keys: set[str] | None = None,
) -> dict:
    entries = _matched_feature_entrypoints(prompt, limit=3, allowed_keys=allowed_keys)
    if not entries:
        fallback = [FLOWI_FEATURE_ENTRYPOINTS[2], FLOWI_FEATURE_ENTRYPOINTS[7], FLOWI_FEATURE_ENTRYPOINTS[1]]
        entries = [item for item in fallback if allowed_keys is None or item["key"] in allowed_keys]
    if not entries:
        return {
            "handled": True,
            "intent": "permission_denied",
            "blocked": True,
            "answer": "현재 계정으로 Flowi가 접근할 수 있는 단위기능이 없습니다. 관리자에게 탭 권한을 요청하세요.",
            "feature_entrypoints": [],
        }
    primary = entries[0]
    action = FLOWI_UNIT_ACTIONS.get(primary["key"], {})
    slots = _slot_summary(prompt, product)
    missing = []
    if primary["key"] in {"splittable", "ettime"}:
        if not slots.get("product"):
            missing.append("product")
        if not slots.get("lots"):
            missing.append("root_lot_id/lot_id")
    if primary["key"] == "ettime":
        if not slots.get("steps"):
            missing.append("step_id")
        if not slots.get("terms"):
            missing.append("item_id")
    rows = [
        {"field": "feature", "value": primary["title"]},
        {"field": "action", "value": action.get("action", primary["key"])},
        {"field": "detected_product", "value": slots.get("product") or ""},
        {"field": "detected_lot", "value": ", ".join(slots.get("lots") or [])},
        {"field": "detected_step", "value": ", ".join(slots.get("steps") or [])},
        {"field": "detected_terms", "value": ", ".join(slots.get("terms") or [])},
        {"field": "needs", "value": ", ".join(action.get("needs") or [])},
        {"field": "outputs", "value": ", ".join(action.get("outputs") or [])},
    ]
    if missing:
        rows.append({"field": "missing", "value": ", ".join(missing)})
    answer = (
        f"{primary['title']} 단위기능으로 처리하는 요청입니다.\n"
        f"- 실행 경로: {action.get('action', primary['key'])}\n"
        f"- 필요한 조건: {', '.join(action.get('needs') or [])}\n"
        f"- 현재 감지: product={slots.get('product') or '-'}, lot={', '.join(slots.get('lots') or []) or '-'}, step={', '.join(slots.get('steps') or []) or '-'}"
    )
    if missing:
        answer += f"\n- 추가로 필요: {', '.join(missing)}"
    answer += "\nFlowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다."
    return {
        "handled": True,
        "intent": action.get("intent", "unit_feature_guidance"),
        "answer": answer,
        "feature": primary["key"],
        "action": action.get("action", primary["key"]),
        "slots": slots,
        "missing": missing,
        "feature_entrypoints": entries,
        "table": {
            "kind": "flowi_action_plan",
            "title": "Flowi unit feature routing",
            "placement": "below",
            "columns": [{"key": "field", "label": "FIELD"}, {"key": "value", "label": "VALUE"}],
            "rows": rows[:max(1, max_rows)],
            "total": len(rows),
        },
    }


def _feature_context(prompt: str, allowed_keys: set[str] | None = None) -> str:
    matches = _matched_feature_entrypoints(prompt, allowed_keys=allowed_keys)
    items = matches or [item for item in FLOWI_FEATURE_ENTRYPOINTS[:6] if allowed_keys is None or item["key"] in allowed_keys]
    summary = "\n".join(
        f"- {it['title']}({it['key']}): {it['description']} 시작 질문 예시: {it['prompt']}"
        for it in items
    )
    parts = ["진입점 인덱스:\n" + _flowi_agent_guide_md()[:2200], "매칭된 기능 후보:\n" + summary]
    detail_parts = []
    for it in items[:3]:
        md = _flowi_feature_guide_md(it.get("key", ""))
        if md:
            detail_parts.append(md[:2600])
    if detail_parts:
        parts.append("선택 기능 상세 가이드:\n" + "\n\n".join(detail_parts))
    return "\n\n".join(parts)


def _flowi_write_target_detected(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    has_write = any(term in low or term in text for term in _WRITE_TERMS)
    has_target = any(term in low or term in text for term in _WRITE_TARGET_TERMS)
    return bool(has_write and has_target)


def _flowi_write_block_message(prompt: str) -> str:
    if not _flowi_write_target_detected(prompt):
        return ""
    return (
        "일반 사용자는 Flowi에서 원 data DB 또는 Files를 수정할 수 없습니다. "
        "조회/요약/표시는 가능하지만 파일 변경/데이터 등록은 admin 또는 파일탐색기 위임 admin의 확인된 단위기능으로만 실행됩니다."
    )


def _can_flowi_file_write(me: dict[str, Any]) -> bool:
    username = me.get("username") or ""
    if (me.get("role") or "") == "admin":
        return True
    return is_page_admin(username, "filebrowser")


def _flowi_file_roots() -> list[tuple[str, Path]]:
    roots: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for label, root in (("Files", PATHS.upload_dir),):
        try:
            root = Path(root)
            key = str(root.resolve()) if root.exists() else str(root)
        except Exception:
            key = str(root)
        if key in seen:
            continue
        seen.add(key)
        roots.append((label, Path(root)))
    return roots


def _flowi_rel_file_path(raw_path: Any) -> Path:
    text = str(raw_path or "").strip().strip("'\"")
    text = text.replace("\\", "/")
    if not text:
        raise ValueError("path가 비어 있습니다.")
    rel = Path(text)
    if rel.is_absolute():
        raise ValueError("절대 경로는 허용하지 않습니다.")
    parts = rel.parts
    if len(parts) != 1:
        raise ValueError("현재 Flow-i 파일 작업은 DB/Files 루트의 단일 파일만 허용합니다.")
    if any(part in {"", ".", ".."} or part.startswith(".") for part in parts):
        raise ValueError("숨김 파일, 상위 경로, 빈 경로는 허용하지 않습니다.")
    if rel.suffix.lower() not in _FLOWI_FILE_EXTS:
        raise ValueError(f"허용 확장자: {', '.join(sorted(_FLOWI_FILE_EXTS))}")
    return rel


def _is_relative_to(child: Path, root: Path) -> bool:
    try:
        child.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def _resolve_flowi_admin_file(raw_path: Any) -> tuple[str, Path, Path]:
    rel = _flowi_rel_file_path(raw_path)
    for label, root in _flowi_file_roots():
        try:
            fp = (root / rel).resolve()
            root_resolved = root.resolve()
        except Exception:
            continue
        if not _is_relative_to(fp, root_resolved):
            continue
        if fp.is_file():
            return label, root_resolved, fp
    raise FileNotFoundError(f"Files 루트에서 파일을 찾지 못했습니다: {rel.as_posix()}")


def _flowi_file_tokens(prompt: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for m in _FLOWI_FILE_TOKEN_RE.finditer(prompt or ""):
        name = m.group(1).strip()
        key = name.lower()
        if key in seen:
            continue
        try:
            _flowi_rel_file_path(name)
        except Exception:
            continue
        seen.add(key)
        out.append(name)
    return out


def _extract_flowi_file_op(prompt: str) -> dict[str, Any] | None:
    text = str(prompt or "")
    idx = text.upper().find(_FLOWI_FILE_OP_MARKER)
    if idx < 0:
        return None
    tail = text[idx + len(_FLOWI_FILE_OP_MARKER):].strip()
    if tail.startswith(":"):
        tail = tail[1:].strip()
    if not tail:
        return {}
    try:
        obj, _end = json.JSONDecoder().raw_decode(tail)
    except Exception as e:
        return {"_parse_error": str(e)}
    return obj if isinstance(obj, dict) else {"_parse_error": "JSON object가 필요합니다."}


def _guess_flowi_file_op(prompt: str) -> str:
    text = str(prompt or "")
    low = text.lower()
    if any(term in low or term in text for term in ("삭제", "지워", "delete", "remove")):
        return "delete"
    if any(term in low or term in text for term in ("rename", "이름", "이동", "move")):
        return "rename"
    if any(term in low or term in text for term in ("replace", "수정", "변경", "바꿔", "바꾸", "edit", "modify")):
        return "replace_text"
    return ""


def _flowi_confirm_text(op: str, rel: Path) -> str:
    op_u = {
        "delete": "DELETE",
        "rename": "RENAME",
        "replace_text": "REPLACE",
    }.get(op, op.upper())
    return f"{op_u} {rel.as_posix()}"


def _flowi_file_op_table(rows: list[dict[str, Any]], title: str = "Flowi admin file operation") -> dict:
    columns = [
        {"key": "field", "label": "FIELD"},
        {"key": "value", "label": "VALUE"},
    ]
    return {
        "kind": "flowi_admin_file_operation",
        "title": title,
        "placement": "below",
        "columns": columns,
        "rows": rows,
        "total": len(rows),
    }


def _flowi_admin_file_confirmation(prompt: str, parse_error: str = "") -> dict:
    files = _flowi_file_tokens(prompt)
    guessed_op = _guess_flowi_file_op(prompt) or "delete"
    rows = [
        {"field": "status", "value": "confirmation_required"},
        {"field": "scope", "value": "admin or filebrowser delegated admin; Files root-level files only"},
        {"field": "supported_ops", "value": "delete, rename, replace_text"},
        {"field": "safety", "value": "delete/replace_text는 .trash 백업 후 실행"},
    ]
    if parse_error:
        rows.append({"field": "parse_error", "value": parse_error})
    if files:
        rows.append({"field": "detected_file", "value": files[0]})
    else:
        rows.append({"field": "needs", "value": "대상 파일명"})

    choices: list[dict[str, Any]] = []
    if files:
        rel = _flowi_rel_file_path(files[0])
        if guessed_op == "delete":
            payload = {"op": "delete", "path": rel.as_posix(), "confirm": _flowi_confirm_text("delete", rel)}
            choices.append({
                "id": "delete_file",
                "label": "1",
                "title": f"{rel.as_posix()} 삭제",
                "recommended": True,
                "description": ".trash로 이동한 뒤 작업 기록을 남깁니다.",
                "prompt": f"{_FLOWI_FILE_OP_MARKER} {json.dumps(payload, ensure_ascii=False)}",
            })
        elif guessed_op == "rename":
            dst = files[1] if len(files) > 1 else f"{rel.stem}_renamed{rel.suffix}"
            payload = {
                "op": "rename",
                "path": rel.as_posix(),
                "new_path": dst,
                "confirm": _flowi_confirm_text("rename", rel),
            }
            choices.append({
                "id": "rename_file",
                "label": "1",
                "title": f"{rel.as_posix()} 이름 변경",
                "recommended": True,
                "description": "같은 DB/Files 루트에서 대상 파일명이 없을 때만 실행합니다.",
                "prompt": f"{_FLOWI_FILE_OP_MARKER} {json.dumps(payload, ensure_ascii=False)}",
            })
        else:
            payload = {
                "op": "replace_text",
                "path": rel.as_posix(),
                "old": "기존 문자열",
                "new": "새 문자열",
                "confirm": _flowi_confirm_text("replace_text", rel),
            }
            choices.append({
                "id": "replace_text",
                "label": "1",
                "title": f"{rel.as_posix()} 문자열 치환",
                "recommended": True,
                "description": "텍스트 계열 파일에서 old와 정확히 일치하는 문자열만 백업 후 치환합니다.",
                "prompt": f"{_FLOWI_FILE_OP_MARKER} {json.dumps(payload, ensure_ascii=False)}",
            })
    choices.append({
            "id": "open_filebrowser",
            "label": "2",
            "title": "파일 탐색기에서 먼저 확인",
            "recommended": not bool(files),
            "description": "Files 영역 대상 파일과 내용을 조회한 뒤 다시 실행합니다. DB는 수정하지 않습니다.",
            "prompt": "파일 탐색기에서 수정할 파일을 먼저 확인해줘",
        })
    return {
        "handled": True,
        "intent": "admin_file_operation",
        "action": "confirm_file_operation",
        "requires_confirmation": True,
            "answer": "Files 단일파일 작업은 구조화된 확인 명령이 필요합니다. DB 루트는 admin도 수정할 수 없습니다.",
        "clarification": {
            "question": "어떤 파일 작업을 실행할까요?",
            "choices": choices,
        },
        "table": _flowi_file_op_table(rows),
    }


def _execute_admin_file_operation(payload: dict[str, Any]) -> dict:
    op = str(payload.get("op") or "").strip().lower()
    if op not in {"delete", "rename", "replace_text"}:
        return _flowi_admin_file_confirmation("", f"지원하지 않는 op입니다: {op or '(empty)'}")
    try:
        rel = _flowi_rel_file_path(payload.get("path"))
        label, root, fp = _resolve_flowi_admin_file(rel.as_posix())
    except Exception as e:
        rows = [{"field": "status", "value": "error"}, {"field": "error", "value": str(e)}]
        return {
            "handled": True,
            "intent": "admin_file_operation",
            "action": op,
            "blocked": True,
            "answer": f"파일 작업을 실행하지 못했습니다: {e}",
            "table": _flowi_file_op_table(rows),
        }

    expected = _flowi_confirm_text(op, rel)
    confirm = str(payload.get("confirm") or "").strip()
    if confirm != expected:
        rows = [
            {"field": "status", "value": "confirmation_required"},
            {"field": "expected_confirm", "value": expected},
            {"field": "received_confirm", "value": confirm or "(empty)"},
        ]
        return {
            "handled": True,
            "intent": "admin_file_operation",
            "action": op,
            "requires_confirmation": True,
            "answer": f"확인 문구가 필요합니다: {expected}",
            "table": _flowi_file_op_table(rows),
            "clarification": {
                "question": "아래 확인 문구로 다시 실행할까요?",
                "choices": [{
                    "id": f"{op}_confirm",
                    "label": "1",
                    "title": expected,
                    "recommended": True,
                    "description": "정확한 확인 문구로 파일 작업을 실행합니다.",
                    "prompt": f"{_FLOWI_FILE_OP_MARKER} {json.dumps({**payload, 'confirm': expected}, ensure_ascii=False)}",
                }],
            },
        }

    trash = root / ".trash"
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    rows: list[dict[str, Any]] = [
        {"field": "status", "value": "executed"},
        {"field": "op", "value": op},
        {"field": "root", "value": label},
        {"field": "path", "value": rel.as_posix()},
    ]
    try:
        trash.mkdir(parents=True, exist_ok=True)
        if op == "delete":
            archived = trash / f"{ts}_{fp.name}"
            fp.rename(archived)
            rows.append({"field": "archived_to", "value": archived.relative_to(root).as_posix()})
            answer = f"{rel.as_posix()} 파일을 .trash로 이동했습니다."
        elif op == "rename":
            new_rel = _flowi_rel_file_path(payload.get("new_path"))
            target = (root / new_rel).resolve()
            if not _is_relative_to(target, root):
                raise ValueError("대상 경로가 DB/Files 루트를 벗어납니다.")
            if target.exists():
                raise FileExistsError(f"대상 파일이 이미 존재합니다: {new_rel.as_posix()}")
            fp.rename(target)
            rows.append({"field": "new_path", "value": new_rel.as_posix()})
            answer = f"{rel.as_posix()} 파일명을 {new_rel.as_posix()}로 변경했습니다."
        else:
            if fp.suffix.lower() not in _FLOWI_TEXT_FILE_EXTS:
                raise ValueError("replace_text는 csv/json/md/txt/yaml/yml 파일에서만 허용합니다.")
            if fp.stat().st_size > _FLOWI_MAX_TEXT_EDIT_BYTES:
                raise ValueError("replace_text는 2MB 이하 텍스트 파일만 허용합니다.")
            old = str(payload.get("old") or "")
            new = str(payload.get("new") or "")
            if not old:
                raise ValueError("old 문자열이 비어 있습니다.")
            text = fp.read_text(encoding="utf-8")
            count = text.count(old)
            if count <= 0:
                raise ValueError("old 문자열과 정확히 일치하는 내용이 없습니다.")
            replace_all = bool(payload.get("replace_all"))
            if count > 1 and not replace_all:
                raise ValueError(f"old 문자열이 {count}회 발견되었습니다. replace_all=true가 필요합니다.")
            backup = trash / f"{ts}_{fp.name}.bak"
            backup.write_text(text, encoding="utf-8")
            fp.write_text(text.replace(old, new), encoding="utf-8")
            rows.extend([
                {"field": "replaced_count", "value": count},
                {"field": "backup_to", "value": backup.relative_to(root).as_posix()},
            ])
            answer = f"{rel.as_posix()} 파일의 문자열 {count}건을 치환했습니다."
    except Exception as e:
        rows[0] = {"field": "status", "value": "error"}
        rows.append({"field": "error", "value": str(e)})
        return {
            "handled": True,
            "intent": "admin_file_operation",
            "action": op,
            "blocked": True,
            "answer": f"파일 작업을 실행하지 못했습니다: {e}",
            "table": _flowi_file_op_table(rows),
        }

    return {
        "handled": True,
        "intent": "admin_file_operation",
        "action": op,
        "answer": answer,
        "table": _flowi_file_op_table(rows),
        "file_operation": {
            "op": op,
            "path": rel.as_posix(),
            "root": label,
            "executed": True,
        },
    }


def _extract_flowi_data_register_payload(prompt: str) -> dict[str, Any] | None:
    text = str(prompt or "")
    idx = text.upper().find(_FLOWI_DATA_REGISTER_MARKER)
    if idx < 0:
        return None
    tail = text[idx + len(_FLOWI_DATA_REGISTER_MARKER):].strip()
    if tail.startswith(":"):
        tail = tail[1:].strip()
    if not tail:
        return {}
    try:
        obj, _end = json.JSONDecoder().raw_decode(tail)
    except Exception as e:
        return {"_parse_error": str(e)}
    return obj if isinstance(obj, dict) else {"_parse_error": "JSON object가 필요합니다."}


def _flowi_data_register_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    if _FLOWI_DATA_REGISTER_MARKER in text.upper():
        return True
    has_register = any(t in low or t in text for t in ("등록", "올려", "업로드", "저장", "추가", "register", "upload", "save", "import"))
    has_data = any(t in low or t in text for t in ("데이터", "표", "csv", "tsv", "json", "테이블", "data", "table"))
    looks_tabular = text.count("\n") >= 2 and ("\t" in text or "," in text or "|" in text)
    return bool(has_register and (has_data or looks_tabular))


def _flowi_fenced_block(prompt: str) -> str:
    m = re.search(r"```(?:csv|tsv|json|table|txt)?\s*\n(.*?)```", prompt or "", flags=re.I | re.S)
    return (m.group(1).strip() if m else "").strip()


def _flowi_register_filename(prompt: str, fmt: str) -> str:
    files = _flowi_file_tokens(prompt)
    ext = ".json" if fmt == "json" else ".csv"
    for name in files:
        if Path(name).suffix.lower() in {".csv", ".json", ".txt"}:
            return name
    product = _product_hint(prompt) or "flowi"
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", product).strip("._") or "flowi"
    return f"{safe}_registered_{ts}{ext}"


def _parse_flowi_data_block(prompt: str) -> dict[str, Any]:
    block = _flowi_fenced_block(prompt) or str(prompt or "").strip()
    block = block.strip()
    if len(block.encode("utf-8")) > 512 * 1024:
        raise ValueError("입력 데이터가 너무 큽니다. 512KB 이하로 나눠 등록해주세요.")
    json_candidate = block
    if not json_candidate.startswith(("[", "{")):
        m = re.search(r"(\[[\s\S]*\]|\{[\s\S]*\})", block)
        json_candidate = m.group(1).strip() if m else ""
    if json_candidate:
        try:
            parsed = json.loads(json_candidate)
            rows = parsed if isinstance(parsed, list) else [parsed]
            if not all(isinstance(r, dict) for r in rows):
                raise ValueError("JSON list는 object row 배열이어야 합니다.")
            columns: list[str] = []
            for row in rows:
                for key in row.keys():
                    k = str(key)
                    if k not in columns:
                        columns.append(k)
            return {
                "format": "json",
                "columns": columns[:_FLOWI_MAX_REGISTER_COLS],
                "rows": [{str(k): v for k, v in row.items()} for row in rows[:_FLOWI_MAX_REGISTER_ROWS]],
                "total_rows": len(rows),
            }
        except Exception:
            pass

    lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
    data_lines = [ln for ln in lines if ("\t" in ln or "," in ln or "|" in ln)]
    if len(data_lines) < 2:
        kv_pairs = []
        for ln in lines:
            m = re.match(r"^\s*([^:=]{1,80})\s*[:=]\s*(.+?)\s*$", ln)
            if m:
                kv_pairs.append((m.group(1).strip(), m.group(2).strip()))
        if kv_pairs:
            return {
                "format": "csv",
                "columns": [k for k, _v in kv_pairs],
                "rows": [{k: v for k, v in kv_pairs}],
                "total_rows": 1,
            }
        raise ValueError("등록할 표 데이터를 찾지 못했습니다. CSV/TSV/JSON 또는 key: value 형식으로 붙여주세요.")

    sample = "\n".join(data_lines[:20])
    delimiter = "\t" if "\t" in sample else ("|" if "|" in sample and sample.count("|") >= sample.count(",") else ",")
    reader = csv.reader(io.StringIO("\n".join(data_lines)), delimiter=delimiter)
    matrix = [row for row in reader if row]
    if len(matrix) < 2:
        raise ValueError("표 데이터는 header와 row가 필요합니다.")
    header = [str(c or "").strip() or f"col{i + 1}" for i, c in enumerate(matrix[0])]
    if len(header) > _FLOWI_MAX_REGISTER_COLS:
        raise ValueError(f"컬럼이 너무 많습니다. 최대 {_FLOWI_MAX_REGISTER_COLS}개까지 등록 가능합니다.")
    seen: dict[str, int] = {}
    columns = []
    for col in header:
        base = re.sub(r"\s+", "_", col.strip()) or "col"
        seen[base] = seen.get(base, 0) + 1
        columns.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
    rows: list[dict[str, Any]] = []
    for raw in matrix[1:]:
        if not any(str(v or "").strip() for v in raw):
            continue
        row = {}
        for i, col in enumerate(columns):
            row[col] = raw[i].strip() if i < len(raw) else ""
        rows.append(row)
    if not rows:
        raise ValueError("header 아래 데이터 row가 없습니다.")
    return {
        "format": "csv",
        "columns": columns,
        "rows": rows[:_FLOWI_MAX_REGISTER_ROWS],
        "total_rows": len(rows),
    }


def _flowi_data_register_confirm_text(path: str) -> str:
    return f"REGISTER {path}"


def _flowi_stage_data_register(draft: dict[str, Any]) -> str:
    FLOWI_STAGED_DATA_DIR.mkdir(parents=True, exist_ok=True)
    draft_id = "dr_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:8]
    fp = FLOWI_STAGED_DATA_DIR / f"{draft_id}.json"
    fp.write_text(json.dumps(draft, ensure_ascii=False, default=str), encoding="utf-8")
    return draft_id


def _flowi_load_staged_data_register(draft_id: str) -> dict[str, Any]:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "", str(draft_id or ""))
    if not safe:
        raise ValueError("draft_id가 비어 있습니다.")
    fp = FLOWI_STAGED_DATA_DIR / f"{safe}.json"
    if not fp.is_file():
        raise FileNotFoundError("등록 draft를 찾지 못했습니다. 다시 초안을 생성해주세요.")
    return json.loads(fp.read_text(encoding="utf-8"))


def _flowi_data_register_table(rows: list[dict[str, Any]], title: str = "Flowi data registration") -> dict:
    return {
        "kind": "flowi_data_register",
        "title": title,
        "placement": "below",
        "columns": [{"key": "field", "label": "FIELD"}, {"key": "value", "label": "VALUE"}],
        "rows": rows,
        "total": len(rows),
    }


def _write_flowi_registered_data(draft: dict[str, Any]) -> tuple[Path, int, int]:
    rel = _flowi_rel_file_path(draft.get("path"))
    if rel.suffix.lower() not in {".csv", ".json", ".txt"}:
        raise ValueError("데이터 등록은 csv/json/txt 파일만 허용합니다.")
    root = PATHS.upload_dir.resolve()
    target = (root / rel).resolve()
    if not _is_relative_to(target, root):
        raise ValueError("대상 경로가 Files 루트를 벗어납니다.")
    if target.exists() and not bool(draft.get("overwrite")):
        raise FileExistsError(f"대상 파일이 이미 존재합니다: {rel.as_posix()}")
    columns = [str(c) for c in (draft.get("columns") or [])]
    rows = draft.get("rows") if isinstance(draft.get("rows"), list) else []
    if len(rows) > _FLOWI_MAX_REGISTER_ROWS:
        raise ValueError(f"최대 {_FLOWI_MAX_REGISTER_ROWS}행까지 등록 가능합니다.")
    target.parent.mkdir(parents=True, exist_ok=True)
    if rel.suffix.lower() == ".json" or draft.get("format") == "json":
        target.write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    elif rel.suffix.lower() == ".txt":
        body = draft.get("text")
        if not isinstance(body, str):
            body = "\n".join("\t".join(str(row.get(c, "")) for c in columns) for row in rows)
        target.write_text(body, encoding="utf-8")
    else:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") if isinstance(row, dict) else "" for c in columns})
        target.write_text(buf.getvalue(), encoding="utf-8")
    return target, len(rows), len(columns)


def _handle_flowi_data_registration(prompt: str, me: dict[str, Any]) -> dict[str, Any]:
    if not _flowi_data_register_intent(prompt):
        return {"handled": False}
    if "db" in str(prompt or "").lower() or "DB" in str(prompt or "") or "원본" in str(prompt or "") or "raw data" in str(prompt or "").lower():
        return {
            "handled": True,
            "intent": "flowi_data_register",
            "action": "blocked_db_write",
            "blocked": True,
            "answer": "DB 루트 원본은 admin도 Flow-i에서 수정하거나 등록할 수 없습니다. 등록은 파일탐색기 수정 권한이 있는 사용자만 Files 영역 단일파일에 대해 확인 후 실행됩니다.",
            "table": _flowi_data_register_table([
                {"field": "status", "value": "blocked"},
                {"field": "reason", "value": "DB root is read-only for everyone"},
                {"field": "allowed_target", "value": "Files root-level file"},
            ]),
        }
    if not _can_flowi_file_write(me):
        return {
            "handled": True,
            "intent": "flowi_data_register",
            "action": "blocked",
            "blocked": True,
            "answer": "홈 Flow-i 데이터 등록은 admin 또는 파일탐색기 위임 admin만 실행할 수 있습니다.",
            "table": _flowi_data_register_table([
                {"field": "status", "value": "blocked"},
                {"field": "required_permission", "value": "admin or page_admin:filebrowser"},
            ]),
        }

    payload = _extract_flowi_data_register_payload(prompt)
    if payload is not None:
        if payload.get("_parse_error"):
            raise HTTPException(400, payload.get("_parse_error"))
        draft = _flowi_load_staged_data_register(str(payload.get("draft_id") or ""))
        expected = _flowi_data_register_confirm_text(str(draft.get("path") or ""))
        if str(payload.get("confirm") or "").strip() != expected:
            return {
                "handled": True,
                "intent": "flowi_data_register",
                "action": "confirm_data_register",
                "requires_confirmation": True,
                "answer": f"등록 전 확인 문구가 필요합니다: {expected}",
                "table": _flowi_data_register_table([
                    {"field": "status", "value": "confirmation_required"},
                    {"field": "target", "value": draft.get("path") or ""},
                    {"field": "rows", "value": len(draft.get("rows") or [])},
                    {"field": "columns", "value": ", ".join(draft.get("columns") or [])},
                ]),
                "clarification": {
                    "question": "이 형식으로 파일탐색기에 등록할까요?",
                    "choices": [{
                        "id": "confirm_register",
                        "label": "1",
                        "title": expected,
                        "recommended": True,
                        "description": "초안 데이터를 CSV/JSON 파일로 저장합니다.",
                        "prompt": f"{_FLOWI_DATA_REGISTER_MARKER} {json.dumps({'draft_id': payload.get('draft_id'), 'confirm': expected}, ensure_ascii=False)}",
                    }, {
                        "id": "cancel_register",
                        "label": "2",
                        "title": "취소",
                        "description": "등록하지 않고 초안만 폐기합니다.",
                        "prompt": "데이터 등록 취소",
                    }],
                },
            }
        target, n_rows, n_cols = _write_flowi_registered_data(draft)
        return {
            "handled": True,
            "intent": "flowi_data_register",
            "action": "registered",
            "answer": f"{target.name} 파일로 데이터 {n_rows}행/{n_cols}열을 등록했습니다. 파일탐색기에서 바로 확인할 수 있습니다.",
            "table": _flowi_data_register_table([
                {"field": "status", "value": "registered"},
                {"field": "path", "value": target.name},
                {"field": "rows", "value": n_rows},
                {"field": "columns", "value": n_cols},
            ]),
            "feature": "filebrowser",
        }

    parsed = _parse_flowi_data_block(prompt)
    fmt = "json" if parsed.get("format") == "json" else "csv"
    path = _flowi_register_filename(prompt, fmt)
    draft = {
        "path": path,
        "format": fmt,
        "columns": parsed.get("columns") or [],
        "rows": parsed.get("rows") or [],
        "created_by": me.get("username") or "",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    draft_id = _flowi_stage_data_register(draft)
    expected = _flowi_data_register_confirm_text(path)
    preview_rows = draft["rows"][:3]
    rows = [
        {"field": "status", "value": "draft_ready"},
        {"field": "target_file", "value": path},
        {"field": "format", "value": fmt},
        {"field": "rows", "value": f"{len(draft['rows'])}" + (f" / input {parsed.get('total_rows')}" if parsed.get("total_rows") != len(draft["rows"]) else "")},
        {"field": "columns", "value": ", ".join(draft["columns"])},
        {"field": "preview", "value": json.dumps(preview_rows, ensure_ascii=False)[:900]},
    ]
    return {
        "handled": True,
        "intent": "flowi_data_register",
        "action": "draft_data_register",
        "requires_confirmation": True,
        "answer": "입력 데이터를 파일탐색기에 등록 가능한 형식으로 정리했습니다. 등록 전 확인 선택지를 눌러야 실제 파일이 생성됩니다.",
        "table": _flowi_data_register_table(rows),
        "clarification": {
            "question": "정리된 데이터를 파일탐색기에 등록할까요?",
            "choices": [{
                "id": "register_data",
                "label": "1",
                "title": f"{path} 등록",
                "recommended": True,
                "description": f"Files 영역에 {len(draft['rows'])}행/{len(draft['columns'])}열을 {fmt.upper()}로 저장합니다. DB는 수정하지 않습니다.",
                "prompt": f"{_FLOWI_DATA_REGISTER_MARKER} {json.dumps({'draft_id': draft_id, 'confirm': expected}, ensure_ascii=False)}",
            }, {
                "id": "revise_data",
                "label": "2",
                "title": "수정해서 다시 등록",
                "description": "컬럼명/파일명/값을 고쳐 다시 붙여넣습니다.",
                "prompt": "데이터 등록 초안을 수정해서 다시 만들게",
            }, {
                "id": "open_filebrowser",
                "label": "3",
                "title": "파일탐색기에서 확인",
                "description": "등록 전 기존 파일과 root를 먼저 확인합니다.",
                "prompt": "파일탐색기에서 등록 위치를 먼저 확인해줘",
            }],
        },
        "feature": "filebrowser",
    }


def _handle_admin_file_operation(prompt: str) -> dict:
    payload = _extract_flowi_file_op(prompt)
    if payload is None:
        return _flowi_admin_file_confirmation(prompt)
    if payload.get("_parse_error"):
        return _flowi_admin_file_confirmation(prompt, str(payload.get("_parse_error")))
    return _execute_admin_file_operation(payload)


def _tokens(prompt: str) -> list[str]:
    return [m.group(0).upper() for m in re.finditer(r"[A-Za-z][A-Za-z0-9_.-]*|\d+(?:\.\d+)?", prompt or "")]


def _title_hint_tokens(prompt: str) -> set[str]:
    text = str(prompt or "")
    hints: set[str] = set()
    patterns = [
        r"([A-Za-z0-9_.-]{1,80})\s*(?:이름|제목|title)\s*으로",
        r"(?:이름|제목|title)\s*[:=]\s*([^\n,;/]+)",
        r"(?:이름|제목|title)\s*(?:은|는)\s*([^\n,;/]+)",
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.I)
        if not m:
            continue
        for tok in _tokens(m.group(1)):
            hints.add(tok)
    return hints


def _is_mixed_alnum_token(tok: str) -> bool:
    return bool(re.fullmatch(r"[A-Z0-9]+", tok or "") and re.search(r"[A-Z]", tok or "") and re.search(r"\d", tok or ""))


def _configured_product_names() -> dict[str, str]:
    products: dict[str, str] = {}
    try:
        for key, cfg in (product_config.load_all(PATHS.data_root) or {}).items():
            name = str((cfg or {}).get("product") or key or "").strip()
            if name:
                products[_upper(name)] = name
                products[_upper(f"ML_TABLE_{name}")] = name
    except Exception:
        pass
    for fp in _ml_files("") if "_ml_files" in globals() else []:
        try:
            stem = fp.stem
        except Exception:
            continue
        if stem.upper().startswith("ML_TABLE_"):
            name = stem[len("ML_TABLE_"):]
            products.setdefault(_upper(name), name)
            products.setdefault(_upper(stem), name)
    try:
        for root in _db_root_candidates("FAB"):
            for child in root.iterdir():
                if child.is_dir() and child.name:
                    products.setdefault(_upper(child.name), child.name)
    except Exception:
        pass
    return products


def _is_product_token(tok: str) -> bool:
    key = _upper(tok)
    if not key:
        return False
    if key.startswith(("ML_TABLE_", "PRODUCT_", "PROD")):
        return True
    return key in _configured_product_names()


def _is_root_lot_token(tok: str) -> bool:
    key = _upper(tok)
    return bool(re.fullmatch(r"[A-Z0-9]{5}", key or "") and re.search(r"[A-Z]", key or ""))


def _is_fab_lot_token(tok: str) -> bool:
    key = _upper(tok)
    return bool(re.fullmatch(r"[A-Z0-9]{5,24}\.[A-Z0-9][A-Z0-9_.-]{0,31}", key or ""))


def _product_aliases(product: str) -> set[str]:
    raw = _upper(product)
    if not raw:
        return set()
    out = {raw}
    if raw.startswith("ML_TABLE_"):
        raw = raw[len("ML_TABLE_"):]
        if raw:
            out.add(raw)
    if raw.startswith("PRODUCT_A0") or raw == "PRODA0":
        out.update({"PRODA", "PRODA0", "PRODUCT_A0", "ML_TABLE_PRODA", "ML_TABLE_PRODA0"})
    elif raw.startswith("PRODUCT_A1") or raw == "PRODA1":
        out.update({"PRODA", "PRODA1", "PRODUCT_A1", "ML_TABLE_PRODA", "ML_TABLE_PRODA1"})
    elif raw.startswith("PRODUCT_A") or raw == "PRODA":
        out.update({"PRODA", "PRODA0", "PRODA1", "PRODUCT_A", "PRODUCT_A0", "PRODUCT_A1", "ML_TABLE_PRODA"})
    elif raw.startswith("PRODUCT_B") or raw == "PRODB":
        out.update({"PRODB", "PRODUCT_B", "ML_TABLE_PRODB"})
    return {v for v in out if v}


def _product_hint(prompt: str, explicit: str = "") -> str:
    if explicit:
        return explicit
    configured = _configured_product_names()
    for tok in _tokens(prompt):
        if tok in configured:
            return configured[tok]
        if tok.startswith(("ML_TABLE_", "PRODUCT_", "PROD")):
            return tok
    return ""


def _lot_tokens(prompt: str) -> list[str]:
    out = []
    seen = set()
    title_tokens = _title_hint_tokens(prompt)
    for tok in _tokens(prompt):
        if _is_product_token(tok):
            continue
        if tok in _FLOWI_NON_LOT_TOKENS or re.fullmatch(r"TEST\d+", tok, flags=re.I):
            continue
        if tok in title_tokens:
            continue
        is_root_like = _is_root_lot_token(tok)
        is_fab_like = (
            _is_fab_lot_token(tok)
            or (len(tok) >= 6 and _is_mixed_alnum_token(tok) and not re.fullmatch(r"[A-Z]{2,5}\d{4,}", tok))
        )
        legacy_lot_like = bool(re.fullmatch(r"[A-Z]\d{4,}(?:[A-Z])?(?:\.\d+)?", tok))
        if (is_root_like or is_fab_like or legacy_lot_like) and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def _classified_lot_tokens(prompt: str) -> dict[str, list[str]]:
    root_ids: list[str] = []
    fab_ids: list[str] = []
    seen_root: set[str] = set()
    seen_fab: set[str] = set()
    for tok in _tokens(prompt):
        if _is_product_token(tok):
            continue
        if tok in _FLOWI_NON_LOT_TOKENS or re.fullmatch(r"TEST\d+", tok, flags=re.I):
            continue
        if _is_root_lot_token(tok):
            if tok not in seen_root:
                seen_root.add(tok)
                root_ids.append(tok)
            continue
        if _is_fab_lot_token(tok) or (len(tok) >= 6 and _is_mixed_alnum_token(tok) and not re.fullmatch(r"[A-Z]{2,5}\d{4,}", tok)):
            if tok not in seen_fab:
                seen_fab.add(tok)
                fab_ids.append(tok)
            continue
        if re.fullmatch(r"[A-Z]\d{4,}(?:[A-Z])?(?:\.\d+)?", tok):
            bucket = fab_ids if "." in tok or len(tok) >= 6 else root_ids
            seen = seen_fab if bucket is fab_ids else seen_root
            if tok not in seen:
                seen.add(tok)
                bucket.append(tok)
    return {"root_lot_ids": root_ids, "fab_lot_ids": fab_ids}


def _is_step_id_token(tok: str) -> bool:
    return bool(re.fullmatch(r"[A-Z]{2}\d{6}", _upper(tok)))


def _known_func_step_names() -> list[str]:
    names: list[str] = []
    try:
        for row in getattr(semi_knowledge, "PROCESS_MODULE_DICTIONARY", []) or []:
            module = _upper(row.get("module") if isinstance(row, dict) else "")
            if module:
                names.append(module)
    except Exception:
        pass
    try:
        for row in getattr(semi_knowledge, "FUNC_STEP_RULES", []) or []:
            if isinstance(row, (list, tuple)) and row:
                label = _upper(row[0])
                if label:
                    names.append(label)
    except Exception:
        pass
    return sorted(set(names), key=lambda x: (-len(x), x))


def _func_step_tokens(prompt: str) -> list[str]:
    norm_text = "_" + re.sub(r"[^A-Z0-9]+", "_", _upper(prompt)).strip("_") + "_"
    if norm_text == "__":
        return []
    out: list[str] = []
    for name in _known_func_step_names():
        needle = "_" + re.sub(r"[^A-Z0-9]+", "_", name).strip("_") + "_"
        if needle in norm_text:
            out.append(name)
    return out


def _step_tokens(prompt: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    func_step = _flowi_func_step_token(prompt)
    if func_step:
        seen.add(_upper(func_step))
        out.append(func_step)
    for tok in _tokens(prompt):
        key = _upper(tok)
        if _is_step_id_token(key) and key not in seen:
            seen.add(key)
            out.append(key)
    for func_step in _func_step_tokens(prompt):
        if func_step not in seen:
            seen.add(func_step)
            out.append(func_step)
    return out


def _query_tokens(prompt: str) -> list[str]:
    out = []
    for tok in _tokens(prompt):
        if tok in _STOP_TOKENS:
            continue
        if tok.startswith(("PROD", "ML_TABLE_", "PRODUCT_")):
            continue
        if re.fullmatch(r"\d+(?:\.\d+)?", tok):
            continue
        out.append(tok)
    return out


def _contains_chart_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    korean_terms = {
        "차트", "그래프", "산점도", "상관", "피팅", "1차식", "선형", "컬러링",
        "필터", "제외", "그려", "그려줘", "막대", "추세", "시계열", "라인",
    }
    if any(term in text for term in korean_terms):
        return True
    latin_terms = {
        "scatter", "corr", "correlation", "fitting", "fit", "linear", "color",
        "coloring", "filter", "plot", "bar", "trend", "line", "chart", "graph",
    }
    return any(re.search(rf"(?<![a-z0-9_]){re.escape(term)}(?![a-z0-9_])", low) for term in latin_terms)


def _source_terms(prompt: str) -> set[str]:
    up = _upper(prompt)
    out = set()
    if "INLINE" in up or "인라인" in prompt:
        out.add("INLINE")
    if re.search(r"\bET\b", up) or "ET" in up:
        out.add("ET")
    if "ML_TABLE" in up or "KNOB" in up or "노브" in prompt:
        out.add("ML_TABLE")
    return out


def _metric_alias_hits(prompt: str) -> list[dict[str, Any]]:
    up = _upper(prompt)
    hits: list[dict[str, Any]] = []
    seen = set()
    for metric, aliases in FLOWI_DOMAIN_DICTIONARY.items():
        for alias in aliases:
            alias_u = _upper(alias)
            if alias_u and alias_u in up and metric not in seen:
                seen.add(metric)
                hits.append({"metric": metric, "aliases": aliases[:6], "confidence": "dictionary_alias"})
                break
    for tok in _query_tokens(prompt):
        key = _upper(tok)
        if len(key) < 2 or key in seen or key in FLOWI_CHART_METRIC_STOP:
            continue
        if any(key == _upper(term) for term in FLOWI_CHART_TERMS):
            continue
        seen.add(key)
        hits.append({"metric": key, "aliases": [], "confidence": "prompt_token"})
    return hits[:12]


def _chart_operations(prompt: str) -> list[str]:
    text = str(prompt or "")
    low = text.lower()
    ops = []
    if any(t in low or t in text for t in ("corr", "correlation", "상관")):
        ops.append("correlation")
    if any(t in low or t in text for t in ("scatter", "산점도", "차트", "그래프")):
        ops.append("scatter")
    if any(t in low or t in text for t in ("1차식", "linear", "fit", "fitting", "피팅", "선형")):
        ops.append("linear_fit")
    if any(t in low or t in text for t in ("color", "coloring", "컬러링", "색")):
        ops.append("color_by_column")
    if any(t in low or t in text for t in ("filter", "필터", "제외", "빼줘")):
        ops.append("filter")
    return ops or ["scatter"]


def _chart_default_join_key(sources: set[str]) -> str:
    return "lot_wf"


def _inline_files(product: str) -> list[Path]:
    files: list[Path] = []
    for root in _db_root_candidates("INLINE"):
        files.extend(sorted(root.rglob("*.parquet")))
    return _filter_files_by_product(files, product)


def _metric_terms(metric: str) -> list[str]:
    key = _upper(metric)
    terms = [key]
    terms.extend(FLOWI_DOMAIN_DICTIONARY.get(key, []))
    out = []
    seen = set()
    for term in terms:
        t = _upper(term)
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _first_metric_in_text(text: str) -> str:
    up = _upper(text)
    for metric, aliases in FLOWI_DOMAIN_DICTIONARY.items():
        if any(_upper(alias) and _upper(alias) in up for alias in aliases):
            return metric
    for tok in _query_tokens(text):
        key = _upper(tok)
        if key and key not in FLOWI_CHART_METRIC_STOP:
            return key
    return ""


def _inline_et_metric_pair(prompt: str, metrics: list[dict[str, Any]]) -> tuple[str, str]:
    text = str(prompt or "")
    up = _upper(text)
    inline_metric = ""
    et_metric = ""
    inline_pos = up.find("INLINE")
    et_pos = up.find("ET")
    if inline_pos >= 0:
        inline_end = et_pos if et_pos > inline_pos else len(text)
        inline_metric = _first_metric_in_text(text[inline_pos:inline_end])
    if et_pos >= 0:
        et_end = inline_pos if inline_pos > et_pos else len(text)
        et_metric = _first_metric_in_text(text[et_pos:et_end])
    ordered = [str(m.get("metric") or "").strip() for m in metrics if str(m.get("metric") or "").strip()]
    if not inline_metric and ordered:
        inline_metric = ordered[0]
    if not et_metric:
        for item in ordered:
            if item != inline_metric:
                et_metric = item
                break
    return inline_metric, et_metric


def _root_key_expr(root_col: str):
    return (
        pl.col(root_col)
        .cast(_STR, strict=False)
        .str.strip_chars()
        .str.to_uppercase()
    )


def _wafer_key_expr(wafer_col: str):
    raw = pl.col(wafer_col).cast(_STR, strict=False).str.strip_chars()
    core = raw.str.replace(r"(?i)^(?:WAFER|WF|W)", "")
    numeric = core.cast(pl.Int64, strict=False)
    return (
        pl.when((numeric >= 1) & (numeric <= FLOWI_MAX_WAFER_ID))
        .then(numeric.cast(_STR, strict=False))
        .otherwise(None)
    )


def _lot_wf_expr(root_col: str, wafer_col: str):
    return (
        _root_key_expr(root_col)
        + pl.lit("_")
        + _wafer_key_expr(wafer_col)
    )


def _explicit_shot_grain(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    return any(term in low or term in text for term in (
        "shot", "die", "map", "좌표", "샷", "다이", "맵", "raw point", "raw-point",
    ))


def _flowi_metric_lf(
    kind: str,
    product: str,
    lots: list[str],
    metric: str,
    value_alias: str,
    *,
    include_shot: bool = False,
    agg_name: str | None = None,
) -> dict[str, Any]:
    kind_u = _upper(kind)
    files = _inline_files(product) if kind_u == "INLINE" else _et_files(product)
    if not files:
        return {"ok": False, "error": f"{kind_u} parquet 파일을 찾지 못했습니다.", "files": []}
    lf = _scan_parquet(files)
    cols = _schema_names(lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    lot_wf_col = _ci_col(cols, "lot_wf", "LOT_WF")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
    item_col = _ci_col(cols, "item_id", "ITEM_ID", "rawitem_id", "RAWITEM_ID", "item", "ITEM")
    value_col = _ci_col(cols, "value", "VALUE", "_value", "val", "VAL")
    shot_id_col = _ci_col(cols, "shot_id", "SHOT_ID")
    if kind_u == "INLINE":
        shot_id_col = _ci_col(cols, "subitem_id", "SUBITEM_ID") or shot_id_col
    shot_x_col = _ci_col(cols, "shot_x", "SHOT_X", "die_x", "DIE_X")
    shot_y_col = _ci_col(cols, "shot_y", "SHOT_Y", "die_y", "DIE_Y")
    if not value_col:
        return {"ok": False, "error": f"{kind_u} value 컬럼을 찾지 못했습니다.", "columns": cols[:80]}
    if not lot_wf_col and not (root_col and wafer_col):
        return {"ok": False, "error": f"{kind_u} lot_wf 또는 root_lot_id/wafer_id 컬럼이 필요합니다.", "columns": cols[:80]}
    if not item_col:
        return {"ok": False, "error": f"{kind_u} item_id 컬럼을 찾지 못했습니다.", "columns": cols[:80]}

    aliases = _product_aliases(product)
    filters = []
    if aliases and product_col:
        filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    if lots:
        lot_cols = [c for c in (root_col, lot_col, fab_col, lot_wf_col) if c]
        lot_expr = _or_contains(lot_cols, lots)
        if lot_expr is not None:
            filters.append(lot_expr)

    item_vals = _unique_strings(lf, item_col, limit=600)
    item_matches = _match_values(item_vals, _metric_terms(metric))
    if not item_matches:
        return {
            "ok": False,
            "error": f"{kind_u}에서 metric `{metric}`에 맞는 item 후보를 찾지 못했습니다.",
            "item_candidates": item_vals[:24],
            "metric": metric,
        }
    filters.append(pl.col(item_col).cast(_STR, strict=False).is_in(item_matches))
    for expr in filters:
        lf = lf.filter(expr)

    exprs = []
    group_cols = []
    if root_col:
        exprs.append(_root_key_expr(root_col).alias("root_lot_id"))
        group_cols.append("root_lot_id")
    if wafer_col:
        exprs.append(_wafer_key_expr(wafer_col).alias("wafer_id"))
        group_cols.append("wafer_id")
    if root_col and wafer_col:
        exprs.append(_lot_wf_expr(root_col, wafer_col).alias("lot_wf"))
    elif lot_wf_col:
        exprs.append(pl.col(lot_wf_col).cast(_STR, strict=False).alias("lot_wf"))
    if "lot_wf" not in group_cols:
        group_cols.append("lot_wf")
    if include_shot and shot_id_col:
        exprs.append(pl.col(shot_id_col).cast(_STR, strict=False).alias("shot_id"))
        group_cols.append("shot_id")
    elif include_shot and shot_x_col and shot_y_col:
        exprs.append(pl.col(shot_x_col).cast(_STR, strict=False).alias("shot_x"))
        exprs.append(pl.col(shot_y_col).cast(_STR, strict=False).alias("shot_y"))
        group_cols.extend(["shot_x", "shot_y"])
    exprs.append(pl.col(value_col).cast(pl.Float64, strict=False).alias("_metric_value"))
    scoped = lf.select(exprs).drop_nulls(subset=["_metric_value"])
    agg_name = agg_name if agg_name in {"avg", "median"} else ("avg" if kind_u == "INLINE" else "median")
    agg = pl.col("_metric_value").mean().alias(value_alias) if agg_name == "avg" else pl.col("_metric_value").median().alias(value_alias)
    grouped = scoped.group_by(group_cols).agg([
        agg,
        pl.len().alias(f"{value_alias}_n"),
    ])
    return {
        "ok": True,
        "lf": grouped,
        "group_cols": group_cols,
        "metric": metric,
        "item_matches": item_matches,
        "files": [str(p) for p in files[:12]],
        "file_count": len(files),
    }


def _flowi_join_cols(left_cols: list[str], right_cols: list[str]) -> list[str]:
    left = set(left_cols)
    right = set(right_cols)
    if {"root_lot_id", "wafer_id", "shot_id"}.issubset(left) and {"root_lot_id", "wafer_id", "shot_id"}.issubset(right):
        return ["root_lot_id", "wafer_id", "shot_id"]
    if {"root_lot_id", "wafer_id", "shot_x", "shot_y"}.issubset(left) and {"root_lot_id", "wafer_id", "shot_x", "shot_y"}.issubset(right):
        return ["root_lot_id", "wafer_id", "shot_x", "shot_y"]
    return ["lot_wf"]


def _explicit_knob_terms(prompt: str) -> list[str]:
    text = str(prompt or "")
    out: list[str] = []
    seen: set[str] = set()
    for pat in (
        r"\b([A-Za-z0-9_.-]{1,40})\s*(?:KNOB|노브)\b",
        r"\b(?:KNOB|노브)\s*([A-Za-z0-9_.-]{1,40})\b",
    ):
        for m in re.finditer(pat, text, flags=re.I):
            key = _upper(m.group(1))
            if not key or key in {"KNOB", "노브", "PLAN"} or key in seen:
                continue
            seen.add(key)
            out.append(key)
    return out[:6]


def _flowi_knob_query_terms(prompt: str, lots: list[str], xy_metrics: list[str]) -> list[str]:
    blocked = set(FLOWI_CHART_METRIC_STOP) | set(_STOP_TOKENS)
    blocked.update(_upper(v) for v in lots)
    metric_terms = set()
    for metric in xy_metrics:
        metric_terms.update(_metric_terms(metric))
    out = []
    seen = set()
    for key in _explicit_knob_terms(prompt):
        if key not in seen:
            seen.add(key)
            out.append(key)
    for tok in _query_tokens(prompt):
        key = _upper(tok)
        if len(key) < 2 or key in blocked or key in metric_terms:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out[:8]


def _pick_knob_by_values(lf: pl.LazyFrame, candidates: list[str]) -> str:
    limited = [c for c in candidates if c][:80]
    if not limited:
        return ""
    try:
        df = (
            lf.select([pl.col(c).cast(_STR, strict=False).alias(c) for c in limited])
            .limit(1000)
            .collect()
        )
    except Exception:
        return limited[0]
    fallback = ""
    for col in limited:
        try:
            vals = [_text(v) for v in df[col].drop_nulls().to_list() if _text(v)]
        except Exception:
            vals = []
        if vals and not fallback:
            fallback = col
        n_unique = len(set(vals))
        if 1 < n_unique <= 24:
            return col
    return fallback or limited[0]


def _select_knob_column(lf: pl.LazyFrame, knob_cols: list[str], prompt: str, lots: list[str], xy_metrics: list[str]) -> tuple[str, list[str]]:
    terms = _flowi_knob_query_terms(prompt, lots, xy_metrics)
    exact: list[str] = []
    contains: list[str] = []
    for col in knob_cols:
        body = _upper(col.replace("KNOB_", "", 1))
        col_u = _upper(col)
        for term in terms:
            if col_u == f"KNOB_{term}" or body == term:
                exact.append(col)
                break
            if term in body or term in col_u:
                contains.append(col)
                break
    candidates = exact or contains
    if candidates:
        return _pick_knob_by_values(lf, candidates), candidates
    return _pick_knob_by_values(lf, knob_cols), knob_cols[:80]


def _knob_filter_values(prompt: str, values: list[str]) -> list[str]:
    text = str(prompt or "")
    low = text.lower()
    if not any(term in low or term in text for term in ("filter", "exclude", "except", "without", "제외", "빼", "빼고", "빼줘", "제거")):
        return []
    up = _upper(text)
    toks = set(_tokens(text))
    out = []
    for value in values:
        raw = _text(value)
        val = _upper(raw)
        if not val:
            continue
        if len(val) <= 2:
            hit = val in toks
        else:
            hit = val in up
        if hit and raw not in out:
            out.append(raw)
    return out[:12]


def _flowi_knob_lf(product: str, lots: list[str], prompt: str, xy_metrics: list[str]) -> dict[str, Any]:
    files = _ml_files(product)
    if not files:
        return {"ok": False, "error": "ML_TABLE parquet 파일을 찾지 못했습니다.", "files": []}
    lf = _scan_parquet(files)
    cols = _schema_names(lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    lot_wf_col = _ci_col(cols, "lot_wf", "LOT_WF")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
    knob_cols = [c for c in cols if _upper(c).startswith("KNOB_")]
    if not knob_cols:
        return {"ok": False, "error": "ML_TABLE에서 KNOB_* 컬럼을 찾지 못했습니다.", "columns": cols[:80]}
    aliases = _product_aliases(product)
    filters = []
    if aliases and product_col:
        filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    if lots:
        lot_cols = [c for c in (root_col, lot_col, fab_col, lot_wf_col) if c]
        lot_expr = _or_contains(lot_cols, lots)
        if lot_expr is not None:
            filters.append(lot_expr)
    for expr in filters:
        lf = lf.filter(expr)

    knob_col, candidates = _select_knob_column(lf, knob_cols, prompt, lots, xy_metrics)
    if not knob_col:
        return {"ok": False, "error": "ML_TABLE에서 color/filter 기준 KNOB 컬럼을 정하지 못했습니다.", "knob_candidates": knob_cols[:24]}

    values = _unique_strings(lf, knob_col, limit=80)
    excluded_values = _knob_filter_values(prompt, values)
    exprs = []
    group_cols = []
    if root_col:
        exprs.append(_root_key_expr(root_col).alias("root_lot_id"))
        group_cols.append("root_lot_id")
    if wafer_col:
        exprs.append(_wafer_key_expr(wafer_col).alias("wafer_id"))
        group_cols.append("wafer_id")
    if root_col and wafer_col:
        exprs.append(_lot_wf_expr(root_col, wafer_col).alias("lot_wf"))
    elif lot_wf_col:
        exprs.append(pl.col(lot_wf_col).cast(_STR, strict=False).alias("lot_wf"))
    if "lot_wf" not in group_cols:
        group_cols.append("lot_wf")
    if not group_cols:
        return {"ok": False, "error": "ML_TABLE에 lot_wf 또는 root_lot_id/wafer_id 컬럼이 필요합니다.", "columns": cols[:80]}
    exprs.append(pl.col(knob_col).cast(_STR, strict=False).alias("color_value"))
    grouped = (
        lf.select(exprs)
        .drop_nulls(subset=["color_value"])
        .group_by(group_cols)
        .agg([
            pl.col("color_value").first().alias("color_value"),
            pl.len().alias("color_n"),
        ])
    )
    return {
        "ok": True,
        "lf": grouped,
        "group_cols": group_cols,
        "knob_col": knob_col,
        "display_name": knob_col.replace("KNOB_", "", 1),
        "candidate_count": len(candidates),
        "values": values[:24],
        "excluded_values": excluded_values,
        "file_count": len(files),
    }


def _flowi_knob_join_cols(scatter_cols: list[str], knob_cols: list[str]) -> list[str]:
    left = set(scatter_cols)
    right = set(knob_cols)
    if {"root_lot_id", "wafer_id"}.issubset(left) and {"root_lot_id", "wafer_id"}.issubset(right):
        return ["root_lot_id", "wafer_id"]
    if "lot_wf" in left and "lot_wf" in right:
        return ["lot_wf"]
    return []


def _duck_col(alias: str, col: str) -> str:
    return f"{alias}.{duckdb_engine.quote_ident(col)}"


def _duck_cast_str(alias: str, col: str) -> str:
    return f"CAST({_duck_col(alias, col)} AS VARCHAR)"


def _duck_root_key_expr(alias: str, col: str) -> str:
    return f"UPPER(TRIM({_duck_cast_str(alias, col)}))"


def _duck_wafer_key_expr(alias: str, col: str) -> str:
    raw = f"TRIM({_duck_cast_str(alias, col)})"
    core = f"REGEXP_REPLACE(UPPER({raw}), '^(WAFER|WF|W)', '')"
    numeric = f"TRY_CAST({core} AS BIGINT)"
    return f"CASE WHEN {numeric} BETWEEN 1 AND {FLOWI_MAX_WAFER_ID} THEN CAST({numeric} AS VARCHAR) ELSE NULL END"


def _duck_in(values: list[str] | set[str]) -> str:
    return ", ".join(duckdb_engine.sql_literal(v) for v in values if _text(v))


def _duck_alias_filter(alias: str, col: str, aliases: set[str]) -> str:
    vals = _duck_in(sorted(aliases))
    return f"UPPER({_duck_cast_str(alias, col)}) IN ({vals})" if col and vals else ""


def _duck_lot_filter(alias: str, cols: list[str], lots: list[str]) -> str:
    terms = [_upper(v) for v in lots if _upper(v)]
    if not terms:
        return ""
    parts: list[str] = []
    for col in cols:
        if not col:
            continue
        casted = f"UPPER({_duck_cast_str(alias, col)})"
        for term in terms:
            safe = term.replace("'", "''").replace("%", "").replace("_", "")
            if safe:
                parts.append(f"{casted} LIKE '%{safe}%'")
    return "(" + " OR ".join(parts) + ")" if parts else ""


def _duck_lot_wf_expr(alias: str, lot_wf_col: str, root_col: str, wafer_col: str) -> str:
    if root_col and wafer_col:
        return f"{_duck_root_key_expr(alias, root_col)} || '_' || {_duck_wafer_key_expr(alias, wafer_col)}"
    return _duck_cast_str(alias, lot_wf_col)


def _duck_metric_subquery(
    *,
    view: str,
    files: list[Path],
    kind: str,
    product: str,
    lots: list[str],
    metric: str,
    value_alias: str,
    include_shot: bool,
    agg_name: str,
) -> dict[str, Any]:
    kind_u = _upper(kind)
    if not files:
        return {"ok": False, "error": f"{kind_u} parquet 파일을 찾지 못했습니다.", "files": []}
    cols, _schema = duckdb_engine.inspect_files(files)
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    lot_wf_col = _ci_col(cols, "lot_wf", "LOT_WF")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
    item_col = _ci_col(cols, "item_id", "ITEM_ID", "rawitem_id", "RAWITEM_ID", "item", "ITEM")
    value_col = _ci_col(cols, "value", "VALUE", "_value", "val", "VAL")
    shot_id_col = _ci_col(cols, "shot_id", "SHOT_ID")
    if kind_u == "INLINE":
        shot_id_col = _ci_col(cols, "subitem_id", "SUBITEM_ID") or shot_id_col
    shot_x_col = _ci_col(cols, "shot_x", "SHOT_X", "die_x", "DIE_X")
    shot_y_col = _ci_col(cols, "shot_y", "SHOT_Y", "die_y", "DIE_Y")
    if not value_col:
        return {"ok": False, "error": f"{kind_u} value 컬럼을 찾지 못했습니다.", "columns": cols[:80]}
    if not lot_wf_col and not (root_col and wafer_col):
        return {"ok": False, "error": f"{kind_u} lot_wf 또는 root_lot_id/wafer_id 컬럼이 필요합니다.", "columns": cols[:80]}
    if not item_col:
        return {"ok": False, "error": f"{kind_u} item_id 컬럼을 찾지 못했습니다.", "columns": cols[:80]}

    item_vals = duckdb_engine.distinct_values(files, item_col, limit=1200)
    item_matches = _match_values(item_vals, _metric_terms(metric))
    if not item_matches:
        return {
            "ok": False,
            "error": f"{kind_u}에서 metric `{metric}`에 맞는 item 후보를 찾지 못했습니다.",
            "item_candidates": item_vals[:24],
            "metric": metric,
        }

    alias = "src"
    filters: list[str] = []
    product_filter = _duck_alias_filter(alias, product_col, _product_aliases(product))
    if product_filter:
        filters.append(product_filter)
    lot_filter = _duck_lot_filter(alias, [c for c in (root_col, lot_col, fab_col, lot_wf_col) if c], lots)
    if lot_filter:
        filters.append(lot_filter)
    filters.append(f"{_duck_cast_str(alias, item_col)} IN ({_duck_in(item_matches)})")
    where_sql = " AND ".join(filters) if filters else "TRUE"

    select_exprs: list[str] = []
    group_cols: list[str] = []
    if root_col:
        select_exprs.append(f"{_duck_root_key_expr(alias, root_col)} AS root_lot_id")
        group_cols.append("root_lot_id")
    if wafer_col:
        select_exprs.append(f"{_duck_wafer_key_expr(alias, wafer_col)} AS wafer_id")
        group_cols.append("wafer_id")
    select_exprs.append(f"{_duck_lot_wf_expr(alias, lot_wf_col, root_col, wafer_col)} AS lot_wf")
    if "lot_wf" not in group_cols:
        group_cols.append("lot_wf")
    if include_shot and shot_id_col:
        select_exprs.append(f"{_duck_cast_str(alias, shot_id_col)} AS shot_id")
        group_cols.append("shot_id")
    elif include_shot and shot_x_col and shot_y_col:
        select_exprs.append(f"{_duck_cast_str(alias, shot_x_col)} AS shot_x")
        select_exprs.append(f"{_duck_cast_str(alias, shot_y_col)} AS shot_y")
        group_cols.extend(["shot_x", "shot_y"])
    select_exprs.append(f"TRY_CAST({_duck_col(alias, value_col)} AS DOUBLE) AS _metric_value")
    agg_sql = "AVG(_metric_value)" if agg_name == "avg" else "MEDIAN(_metric_value)"
    group_sql = ", ".join(group_cols)
    sql = f"""
        SELECT {group_sql},
               {agg_sql} AS {value_alias},
               COUNT(*) AS {value_alias}_n
        FROM (
            SELECT {", ".join(select_exprs)}
            FROM {duckdb_engine.quote_ident(view)} {alias}
            WHERE {where_sql}
        ) scoped
        WHERE _metric_value IS NOT NULL
        GROUP BY {group_sql}
    """
    return {
        "ok": True,
        "sql": sql,
        "group_cols": group_cols,
        "metric": metric,
        "item_matches": item_matches,
        "files": [str(p) for p in files[:12]],
        "file_count": len(files),
    }


def _duck_select_knob_column(files: list[Path], knob_cols: list[str], prompt: str, lots: list[str], xy_metrics: list[str]) -> tuple[str, list[str], list[str]]:
    terms = _flowi_knob_query_terms(prompt, lots, xy_metrics)
    exact: list[str] = []
    contains: list[str] = []
    for col in knob_cols:
        body = _upper(col.replace("KNOB_", "", 1))
        col_u = _upper(col)
        for term in terms:
            if col_u == f"KNOB_{term}" or body == term:
                exact.append(col)
                break
            if term in body or term in col_u:
                contains.append(col)
                break
    candidates = exact or contains or knob_cols[:80]
    knob_col = candidates[0] if candidates else ""
    values = duckdb_engine.distinct_values(files, knob_col, limit=80) if knob_col else []
    return knob_col, candidates, values


def _duck_knob_subquery(product: str, lots: list[str], prompt: str, xy_metrics: list[str]) -> dict[str, Any]:
    files = _ml_files(product)
    if not files:
        return {"ok": False, "error": "ML_TABLE parquet 파일을 찾지 못했습니다.", "files": []}
    cols, _schema = duckdb_engine.inspect_files(files)
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    lot_wf_col = _ci_col(cols, "lot_wf", "LOT_WF")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
    knob_cols = [c for c in cols if _upper(c).startswith("KNOB_")]
    if not knob_cols:
        return {"ok": False, "error": "ML_TABLE에서 KNOB_* 컬럼을 찾지 못했습니다.", "columns": cols[:80]}
    if not lot_wf_col and not (root_col and wafer_col):
        return {"ok": False, "error": "ML_TABLE에 lot_wf 또는 root_lot_id/wafer_id 컬럼이 필요합니다.", "columns": cols[:80]}
    knob_col, candidates, values = _duck_select_knob_column(files, knob_cols, prompt, lots, xy_metrics)
    if not knob_col:
        return {"ok": False, "error": "ML_TABLE에서 color/filter 기준 KNOB 컬럼을 정하지 못했습니다.", "knob_candidates": knob_cols[:24]}

    alias = "src"
    filters: list[str] = []
    product_filter = _duck_alias_filter(alias, product_col, _product_aliases(product))
    if product_filter:
        filters.append(product_filter)
    lot_filter = _duck_lot_filter(alias, [c for c in (root_col, lot_col, fab_col, lot_wf_col) if c], lots)
    if lot_filter:
        filters.append(lot_filter)
    where_sql = " AND ".join(filters) if filters else "TRUE"
    exprs: list[str] = []
    group_cols: list[str] = []
    if root_col:
        exprs.append(f"{_duck_root_key_expr(alias, root_col)} AS root_lot_id")
        group_cols.append("root_lot_id")
    if wafer_col:
        exprs.append(f"{_duck_wafer_key_expr(alias, wafer_col)} AS wafer_id")
        group_cols.append("wafer_id")
    exprs.append(f"{_duck_lot_wf_expr(alias, lot_wf_col, root_col, wafer_col)} AS lot_wf")
    if "lot_wf" not in group_cols:
        group_cols.append("lot_wf")
    exprs.append(f"{_duck_cast_str(alias, knob_col)} AS color_value")
    group_sql = ", ".join(group_cols)
    sql = f"""
        SELECT {group_sql},
               MIN(color_value) AS color_value,
               COUNT(*) AS color_n
        FROM (
            SELECT {", ".join(exprs)}
            FROM {duckdb_engine.quote_ident("ml_src")} {alias}
            WHERE {where_sql}
        ) scoped
        WHERE color_value IS NOT NULL
        GROUP BY {group_sql}
    """
    return {
        "ok": True,
        "sql": sql,
        "group_cols": group_cols,
        "knob_col": knob_col,
        "display_name": knob_col.replace("KNOB_", "", 1),
        "candidate_count": len(candidates),
        "values": values[:24],
        "excluded_values": _knob_filter_values(prompt, values),
        "file_count": len(files),
        "files": files,
    }


def _try_metric_scatter_duckdb(prompt: str, product: str, metrics: list[dict[str, Any]], lots: list[str], operations: list[str]) -> dict[str, Any]:
    if not duckdb_engine.is_available():
        return {"ok": False, "error": "duckdb unavailable", "fallback": True}
    sources = _source_terms(prompt)
    if not {"INLINE", "ET"}.issubset(sources):
        return {"ok": False, "error": "현재 실제 scatter 실행은 INLINE + ET 조합부터 지원합니다.", "fallback": True}
    inline_metric, et_metric = _inline_et_metric_pair(prompt, metrics)
    if not inline_metric or not et_metric:
        return {"ok": False, "error": "INLINE/ET metric 2개가 필요합니다.", "fallback": True}
    chart_defaults = _flowi_chart_defaults()
    scatter_defaults = chart_defaults.get("scatter") or FLOWI_CHART_DEFAULTS["scatter"]
    inline_agg = scatter_defaults.get("inline_agg") if scatter_defaults.get("inline_agg") in {"avg", "median"} else "avg"
    et_agg = scatter_defaults.get("et_agg") if scatter_defaults.get("et_agg") in {"avg", "median"} else "median"
    try:
        point_limit = max(50, min(5000, int(scatter_defaults.get("max_points") or FLOWI_CHART_POINT_LIMIT)))
    except Exception:
        point_limit = FLOWI_CHART_POINT_LIMIT
    include_shot = _explicit_shot_grain(prompt)
    inline_files = _inline_files(product)
    et_files = _et_files(product)
    inline = _duck_metric_subquery(
        view="inline_src", files=inline_files, kind="INLINE", product=product, lots=lots,
        metric=inline_metric, value_alias="inline_value", include_shot=include_shot, agg_name=inline_agg,
    )
    if not inline.get("ok"):
        return inline
    et = _duck_metric_subquery(
        view="et_src", files=et_files, kind="ET", product=product, lots=lots,
        metric=et_metric, value_alias="et_value", include_shot=include_shot, agg_name=et_agg,
    )
    if not et.get("ok"):
        return et
    join_cols = _flowi_join_cols(inline.get("group_cols") or [], et.get("group_cols") or [])
    join_how = "INNER" if "inner join" in str(prompt).lower() or "inner" in str(prompt).lower() else "LEFT"
    needs_knob = (
        "color_by_column" in operations
        or "filter" in operations
        or "KNOB" in _upper(prompt)
        or "노브" in str(prompt or "")
    )
    source_files = {"inline_src": inline_files, "et_src": et_files}
    ctes = [f"inline_metric AS ({inline['sql']})", f"et_metric AS ({et['sql']})"]
    select_cols = [
        *(f"j.{c}" for c in join_cols),
        "j.lot_wf",
        "j.root_lot_id",
        "j.wafer_id",
        "j.inline_value",
        "j.et_value",
        "j.inline_value_n",
        "j.et_value_n",
    ]
    knob = None
    knob_join_cols: list[str] = []
    exclusion_sql = ""
    if needs_knob:
        knob = _duck_knob_subquery(product, lots, prompt, [inline_metric, et_metric])
        if not knob.get("ok"):
            return knob
        source_files["ml_src"] = knob["files"]
        ctes.append(f"knob_metric AS ({knob['sql']})")
        knob_join_cols = _flowi_knob_join_cols([*join_cols, "lot_wf", "root_lot_id", "wafer_id"], knob.get("group_cols") or [])
        if not knob_join_cols:
            return {"ok": False, "error": "INLINE/ET 결과와 ML_TABLE KNOB를 연결할 lot_wf/root_lot_id+wafer_id 키가 없습니다."}
        select_cols.extend(["j.color_value", "j.color_n"])
        excluded = knob.get("excluded_values") or []
        if excluded:
            exclusion_sql = f"AND (j.color_value IS NULL OR j.color_value NOT IN ({_duck_in(excluded)}))"

    using_cols = ", ".join(join_cols)
    joined_sql = (
        f"SELECT * FROM inline_metric i {join_how} JOIN et_metric e USING ({using_cols})"
    )
    if knob:
        knob_using = ", ".join(knob_join_cols)
        joined_sql = f"SELECT j.*, k.color_value, k.color_n FROM ({joined_sql}) j LEFT JOIN knob_metric k USING ({knob_using})"
    ctes.append(f"joined AS ({joined_sql})")
    final_sql = f"""
        WITH {", ".join(ctes)}
        SELECT {", ".join(dict.fromkeys(select_cols))}
        FROM joined j
        WHERE j.inline_value IS NOT NULL AND j.et_value IS NOT NULL
        {exclusion_sql}
        LIMIT {point_limit}
    """
    try:
        df = duckdb_engine.query_views(source_files, final_sql)
    except Exception as e:
        logger.warning("flowi duckdb metric scatter failed: %s", e)
        return {"ok": False, "error": f"DuckDB metric scatter query 실패: {e}", "fallback": True}

    rows = df.to_dicts()
    points = []
    xs: list[float] = []
    ys: list[float] = []
    for row in rows:
        try:
            x = float(row.get("inline_value"))
            y = float(row.get("et_value"))
        except Exception:
            continue
        xs.append(x)
        ys.append(y)
        label = row.get("lot_wf") or "_".join(str(row.get(c) or "") for c in join_cols)
        point = {
            "x": round(x, 6),
            "y": round(y, 6),
            "label": label,
            "root_lot_id": row.get("root_lot_id") or "",
            "wafer_id": row.get("wafer_id") or "",
            "join_key": "|".join(str(row.get(c) or "") for c in join_cols),
            "inline_n": int(row.get("inline_value_n") or 0),
            "et_n": int(row.get("et_value_n") or 0),
        }
        if knob and _text(row.get("color_value")):
            point["color_by"] = knob.get("display_name") or knob.get("knob_col") or "KNOB"
            point["color_value"] = _text(row.get("color_value"))
            point["color_n"] = int(row.get("color_n") or 0)
        points.append(point)
    corr = _pearson(xs, ys)
    fit = _linear_fit(xs, ys) if "linear_fit" in operations else {}
    color_counts: dict[str, int] = {}
    for point in points:
        cv = _text(point.get("color_value"))
        if cv:
            color_counts[cv] = color_counts.get(cv, 0) + 1
    source_meta = {
        "engine": "duckdb",
        "inline_items": inline.get("item_matches") or [],
        "et_items": et.get("item_matches") or [],
        "inline_file_count": inline.get("file_count") or 0,
        "et_file_count": et.get("file_count") or 0,
    }
    if knob:
        source_meta.update({
            "ml_table_file_count": knob.get("file_count") or 0,
            "knob_column": knob.get("knob_col") or "",
            "knob_join_cols": knob_join_cols,
        })
    return {
        "ok": True,
        "kind": "dashboard_scatter",
        "title": f"INLINE {inline_metric} vs ET {et_metric}",
        "points": points,
        "total": len(points),
        "x_label": f"INLINE {inline_metric} {inline_agg}",
        "y_label": f"ET {et_metric} {et_agg}",
        "join_cols": join_cols,
        "join_how": join_how.lower(),
        "corr": round(corr, 6) if corr is not None else None,
        "fit": fit,
        "color_by": (knob.get("display_name") if knob else "") or "",
        "color_values": [{"value": k, "count": v} for k, v in sorted(color_counts.items(), key=lambda kv: (-kv[1], kv[0]))],
        "filters": {"excluded_values": knob.get("excluded_values") or []} if knob else {},
        "sources": source_meta,
        "aggregations": {"INLINE": inline_agg, "ET": et_agg},
        "render_preset": {**scatter_defaults, "grain": "shot" if include_shot else "wafer_agg"},
    }


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = min(len(xs), len(ys))
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sx = sum((x - mx) ** 2 for x in xs)
    sy = sum((y - my) ** 2 for y in ys)
    if sx <= 0 or sy <= 0:
        return None
    cov = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    return cov / math.sqrt(sx * sy)


def _linear_fit(xs: list[float], ys: list[float]) -> dict[str, Any]:
    n = min(len(xs), len(ys))
    if n < 2:
        return {}
    mx = sum(xs) / n
    my = sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom <= 0:
        return {}
    slope = sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / denom
    intercept = my - slope * mx
    preds = [slope * x + intercept for x in xs]
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((ys[i] - preds[i]) ** 2 for i in range(n))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0
    return {"slope": round(slope, 8), "intercept": round(intercept, 8), "r2": round(r2, 6)}


def _try_metric_scatter(prompt: str, product: str, metrics: list[dict[str, Any]], lots: list[str], operations: list[str]) -> dict[str, Any]:
    sources = _source_terms(prompt)
    if not {"INLINE", "ET"}.issubset(sources):
        return {"ok": False, "error": "현재 실제 scatter 실행은 INLINE + ET 조합부터 지원합니다."}
    inline_metric, et_metric = _inline_et_metric_pair(prompt, metrics)
    if not inline_metric or not et_metric:
        return {"ok": False, "error": "INLINE/ET metric 2개가 필요합니다."}
    duckdb_result = _try_metric_scatter_duckdb(prompt, product, metrics, lots, operations)
    if duckdb_result.get("ok"):
        return duckdb_result
    if duckdb_result.get("fallback") and duckdb_result.get("error") != "duckdb unavailable":
        logger.warning("flowi duckdb scatter fallback: %s", duckdb_result.get("error"))
    chart_defaults = _flowi_chart_defaults()
    scatter_defaults = chart_defaults.get("scatter") or FLOWI_CHART_DEFAULTS["scatter"]
    inline_agg = scatter_defaults.get("inline_agg") if scatter_defaults.get("inline_agg") in {"avg", "median"} else "avg"
    et_agg = scatter_defaults.get("et_agg") if scatter_defaults.get("et_agg") in {"avg", "median"} else "median"
    try:
        point_limit = max(50, min(5000, int(scatter_defaults.get("max_points") or FLOWI_CHART_POINT_LIMIT)))
    except Exception:
        point_limit = FLOWI_CHART_POINT_LIMIT
    include_shot = _explicit_shot_grain(prompt)
    inline = _flowi_metric_lf("INLINE", product, lots, inline_metric, "inline_value", include_shot=include_shot, agg_name=inline_agg)
    if not inline.get("ok"):
        return inline
    et = _flowi_metric_lf("ET", product, lots, et_metric, "et_value", include_shot=include_shot, agg_name=et_agg)
    if not et.get("ok"):
        return et
    join_cols = _flowi_join_cols(inline.get("group_cols") or [], et.get("group_cols") or [])
    join_how = "inner" if "inner join" in str(prompt).lower() or "inner" in str(prompt).lower() else "left"
    needs_knob = (
        "color_by_column" in operations
        or "filter" in operations
        or "KNOB" in _upper(prompt)
        or "노브" in str(prompt or "")
    )
    knob = None
    knob_join_cols: list[str] = []
    if needs_knob:
        knob = _flowi_knob_lf(product, lots, prompt, [inline_metric, et_metric])
        if not knob.get("ok"):
            return knob
    try:
        joined = inline["lf"].join(et["lf"], on=join_cols, how=join_how)
        if knob:
            knob_join_cols = _flowi_knob_join_cols(joined.collect_schema().names(), knob.get("group_cols") or [])
            if not knob_join_cols:
                return {"ok": False, "error": "INLINE/ET 결과와 ML_TABLE KNOB를 연결할 lot_wf/root_lot_id+wafer_id 키가 없습니다."}
            joined = joined.join(knob["lf"], on=knob_join_cols, how="left")
            excluded = knob.get("excluded_values") or []
            if excluded:
                joined = joined.filter(
                    pl.col("color_value").is_null()
                    | (~pl.col("color_value").cast(_STR, strict=False).is_in(excluded))
                )
        keep = list(dict.fromkeys([
            *join_cols,
            "lot_wf",
            "root_lot_id",
            "wafer_id",
            "inline_value",
            "et_value",
            "inline_value_n",
            "et_value_n",
            "color_value",
            "color_n",
        ]))
        keep = [c for c in keep if c in joined.collect_schema().names()]
        df = (
            joined.select(keep)
            .drop_nulls(subset=["inline_value", "et_value"])
            .limit(point_limit)
            .collect()
        )
    except Exception as e:
        logger.warning("flowi metric scatter failed: %s", e)
        return {"ok": False, "error": f"metric scatter query 실패: {e}"}
    rows = df.to_dicts()
    points = []
    xs: list[float] = []
    ys: list[float] = []
    for row in rows:
        try:
            x = float(row.get("inline_value"))
            y = float(row.get("et_value"))
        except Exception:
            continue
        xs.append(x)
        ys.append(y)
        label = row.get("lot_wf") or "_".join(str(row.get(c) or "") for c in join_cols)
        point = {
            "x": round(x, 6),
            "y": round(y, 6),
            "label": label,
            "root_lot_id": row.get("root_lot_id") or "",
            "wafer_id": row.get("wafer_id") or "",
            "join_key": "|".join(str(row.get(c) or "") for c in join_cols),
            "inline_n": int(row.get("inline_value_n") or 0),
            "et_n": int(row.get("et_value_n") or 0),
        }
        if knob and _text(row.get("color_value")):
            point["color_by"] = knob.get("display_name") or knob.get("knob_col") or "KNOB"
            point["color_value"] = _text(row.get("color_value"))
            point["color_n"] = int(row.get("color_n") or 0)
        points.append(point)
    corr = _pearson(xs, ys)
    fit = _linear_fit(xs, ys) if "linear_fit" in operations else {}
    color_counts: dict[str, int] = {}
    for point in points:
        cv = _text(point.get("color_value"))
        if cv:
            color_counts[cv] = color_counts.get(cv, 0) + 1
    source_meta = {
        "inline_items": inline.get("item_matches") or [],
        "et_items": et.get("item_matches") or [],
        "inline_file_count": inline.get("file_count") or 0,
        "et_file_count": et.get("file_count") or 0,
    }
    if knob:
        source_meta.update({
            "ml_table_file_count": knob.get("file_count") or 0,
            "knob_column": knob.get("knob_col") or "",
            "knob_join_cols": knob_join_cols,
        })
    return {
        "ok": True,
        "kind": "dashboard_scatter",
        "title": f"INLINE {inline_metric} vs ET {et_metric}",
        "points": points,
        "total": len(points),
        "x_label": f"INLINE {inline_metric} {inline_agg}",
        "y_label": f"ET {et_metric} {et_agg}",
        "join_cols": join_cols,
        "join_how": join_how,
        "corr": round(corr, 6) if corr is not None else None,
        "fit": fit,
        "color_by": (knob.get("display_name") if knob else "") or "",
        "color_values": [{"value": k, "count": v} for k, v in sorted(color_counts.items(), key=lambda kv: (-kv[1], kv[0]))],
        "filters": {"excluded_values": knob.get("excluded_values") or []} if knob else {},
        "sources": source_meta,
        "aggregations": {"INLINE": inline_agg, "ET": et_agg},
        "render_preset": {**scatter_defaults, "grain": "shot" if include_shot else "wafer_agg"},
    }


def _group_chart_group_keys(prompt: str) -> list[str]:
    text = str(prompt or "")
    low = text.lower()
    has_eqp = any(t in low or t in text for t in ("eqp", "equipment", "장비", "설비"))
    has_chamber = any(t in low or t in text for t in ("chamber", "챔버"))
    if has_eqp and has_chamber:
        return ["eqp", "chamber"]
    if has_chamber:
        return ["chamber"]
    if has_eqp:
        return ["eqp"]
    return []


def _inline_metric_match_for_prompt(lf: pl.LazyFrame, item_col: str, prompt: str) -> tuple[str, list[str], list[str]]:
    item_vals = _unique_strings(lf, item_col, limit=1200)
    blocked = {"EQP", "EQUIPMENT", "CHAMBER", "장비", "설비", "챔버"}
    terms = []
    seen = set()
    for hit in _metric_alias_hits(prompt):
        key = _upper(hit.get("metric"))
        if key and key not in blocked and key not in seen:
            seen.add(key)
            terms.append(key)
    for tok in _query_tokens(prompt):
        key = _upper(tok)
        if key and key not in blocked and key not in seen:
            seen.add(key)
            terms.append(key)
    exact = []
    for term in terms:
        exact.extend([v for v in item_vals if _upper(v) == term])
    if exact:
        matches = sorted(set(exact), key=lambda x: (-len(str(x)), str(x)))
        return matches[0], matches, item_vals[:24]
    matches = _match_values(item_vals, terms)
    if matches:
        matches = sorted(set(matches), key=lambda x: (-len(str(x)), str(x)))
        return matches[0], matches, item_vals[:24]
    term_sets = {term: set(t for t in re.split(r"[_\W]+", _upper(term)) if t) for term in terms}
    reordered = []
    for value in item_vals:
        val_set = set(t for t in re.split(r"[_\W]+", _upper(value)) if t)
        if val_set and any(val_set == parts for parts in term_sets.values()):
            reordered.append(value)
    if reordered:
        reordered = sorted(set(reordered), key=lambda x: (-len(str(x)), str(x)))
        return reordered[0], reordered, item_vals[:24]
    return "", [], item_vals[:24]


def _is_trend_chart_request(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    if any(re.search(rf"(?<![a-z0-9_]){term}(?![a-z0-9_])", low) for term in ("scatter", "corr", "correlation")):
        return False
    if any(t in text for t in ("추세", "시계열", "라인")):
        return True
    return any(re.search(rf"(?<![a-z0-9_]){term}(?![a-z0-9_])", low) for term in ("trend", "line"))


def _is_box_chart_request(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    return _contains_chart_intent(text) and (
        any(t in text for t in ("박스", "분포", "분산"))
        or any(re.search(rf"(?<![a-z0-9_]){term}(?![a-z0-9_])", low) for term in ("box", "boxplot", "distribution"))
    )


def _percentile_sorted(vals: list[float], q: float) -> float | None:
    clean = sorted(float(v) for v in vals if v is not None and math.isfinite(float(v)))
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    pos = (len(clean) - 1) * max(0.0, min(1.0, float(q)))
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return clean[lo]
    frac = pos - lo
    return clean[lo] * (1 - frac) + clean[hi] * frac


def _is_wafer_map_chart_request(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    if any(t in low or t in text for t in ("tablemap", "table map", "테이블맵", "테이블 맵", "relation", "관계")):
        return False
    has_map = any(t in low or t in text for t in ("wf map", "wafer map", "웨이퍼맵", "맵", "map"))
    if not has_map or any(t in low or t in text for t in ("비슷", "similar", "유사", "닮")):
        return False
    return _contains_chart_intent(text) or any(t in low or t in text for t in ("보여", "표시", "view"))


def _metric_map_source_order(prompt: str, product: str = "") -> list[tuple[str, list[Path]]]:
    up = _upper(prompt)
    product_hint = _product_hint(prompt, product)
    explicit_inline = "INLINE" in up or "인라인" in str(prompt or "")
    explicit_et = "ET" in up
    if explicit_inline and not explicit_et:
        return [("INLINE", _inline_files(product_hint))]
    if explicit_et and not explicit_inline:
        return [("ET", _et_files(product_hint))]
    order: list[tuple[str, list[Path]]] = []
    if explicit_et:
        order.append(("ET", _et_files(product_hint)))
    if explicit_inline:
        order.append(("INLINE", _inline_files(product_hint)))
    for source, getter in (("ET", _et_files), ("INLINE", _inline_files)):
        if source not in {s for s, _ in order}:
            order.append((source, getter(product_hint)))
    return order


def _handle_inline_box_chart(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    text = str(prompt or "")
    if not _is_box_chart_request(text):
        return {"handled": False}
    product_hint = _product_hint(text, product)
    if not product_hint:
        return {
            "handled": True,
            "intent": "dashboard_box_needs_context",
            "action": "collect_required_fields",
            "answer": "Box plot을 그리려면 product가 필요합니다. 예: `PRODA CD_GATE box plot 그려줘`",
            "missing": ["product"],
            "feature": "dashboard",
        }
    inline_files = _inline_files(product_hint)
    if not inline_files:
        return {"handled": True, "intent": "dashboard_box", "answer": f"{product_hint} INLINE parquet을 찾지 못했습니다.", "feature": "dashboard"}
    inline_lf = _scan_parquet(inline_files)
    cols = _schema_names(inline_lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    item_col = _ci_col(cols, "item_id", "ITEM_ID", "rawitem_id", "RAWITEM_ID", "item", "ITEM")
    value_col = _ci_col(cols, "value", "VALUE", "_value", "val", "VAL")
    if not item_col or not value_col:
        return {"handled": True, "intent": "dashboard_box", "answer": "INLINE 데이터에서 item_id/value 컬럼을 찾지 못했습니다.", "feature": "dashboard"}
    metric, item_matches, item_candidates = _inline_metric_match_for_prompt(inline_lf, item_col, text)
    if not metric:
        return {
            "handled": True,
            "intent": "dashboard_box_needs_context",
            "action": "collect_required_fields",
            "answer": "Box plot으로 그릴 INLINE item을 찾지 못했습니다. item명을 더 정확히 알려주세요.",
            "missing": ["item_id"],
            "feature": "dashboard",
            "table": {"kind": "inline_item_candidates", "title": "INLINE item candidates", "placement": "below", "columns": _table_columns(["item_id"]), "rows": [{"item_id": x} for x in item_candidates], "total": len(item_candidates)},
        }
    aliases = _product_aliases(product_hint)
    lots = _lot_tokens(text)
    filters = []
    if aliases and product_col:
        filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    if lots:
        lot_expr = _or_contains([c for c in (root_col, lot_col, fab_col) if c], lots)
        if lot_expr is not None:
            filters.append(lot_expr)
    filters.append(pl.col(item_col).cast(_STR, strict=False).is_in(item_matches or [metric]))
    for expr in filters:
        inline_lf = inline_lf.filter(expr)
    group_expr = pl.col(root_col).cast(_STR, strict=False).alias("group") if root_col else pl.lit(product_hint).alias("group")
    try:
        df = (
            inline_lf.select([
                group_expr,
                pl.col(value_col).cast(pl.Float64, strict=False).alias("value"),
                pl.col(wafer_col).cast(_STR, strict=False).alias("wafer_id") if wafer_col else pl.lit("").alias("wafer_id"),
            ])
            .drop_nulls(subset=["group", "value"])
            .limit(200000)
            .collect()
        )
    except Exception as e:
        logger.warning("flowi inline box failed: %s", e)
        return {"handled": True, "intent": "dashboard_box", "answer": f"Box plot query 실패: {e}", "feature": "dashboard"}
    buckets: dict[str, list[float]] = {}
    wafer_counts: dict[str, set[str]] = {}
    for row in df.to_dicts():
        label = _text(row.get("group")) or product_hint
        try:
            val = float(row.get("value"))
        except Exception:
            continue
        if not math.isfinite(val):
            continue
        buckets.setdefault(label, []).append(val)
        wf = _text(row.get("wafer_id"))
        if wf:
            wafer_counts.setdefault(label, set()).add(wf)
    min_n = max(1, int((_flowi_chart_defaults().get("box") or FLOWI_CHART_DEFAULTS["box"]).get("min_n") or 3))
    max_groups = max(1, min(40, int((_flowi_chart_defaults().get("box") or FLOWI_CHART_DEFAULTS["box"]).get("max_groups") or 12)))
    boxes = []
    for label, vals in buckets.items():
        if len(vals) < min_n:
            continue
        vals_s = sorted(vals)
        boxes.append({
            "label": label,
            "min": _round4(vals_s[0]),
            "q1": _round4(_percentile_sorted(vals_s, 0.25)),
            "median": _round4(_percentile_sorted(vals_s, 0.5)),
            "q3": _round4(_percentile_sorted(vals_s, 0.75)),
            "max": _round4(vals_s[-1]),
            "mean": _round4(sum(vals_s) / len(vals_s)),
            "n": len(vals_s),
            "wafer_count": len(wafer_counts.get(label, set())),
        })
    boxes.sort(key=lambda r: (-int(r.get("n") or 0), str(r.get("label") or "")))
    boxes = boxes[:max_groups]
    rows = boxes
    answer = (
        f"{product_hint} {metric} INLINE 분포를 root_lot_id별 box plot으로 그렸습니다. "
        f"group={len(boxes)}, item match={', '.join(item_matches or [metric])}."
    ) if boxes else f"{product_hint} {metric} 조건으로 box plot을 만들 row가 부족합니다."
    cols_out = ["label", "min", "q1", "median", "q3", "max", "mean", "n", "wafer_count"]
    return {
        "handled": True,
        "intent": "dashboard_box_chart",
        "action": "query_inline_box_chart",
        "answer": answer,
        "feature": "dashboard",
        "slots": {"product": product_hint, "metric": metric, "lots": lots},
        "chart_result": {
            "ok": True,
            "kind": "dashboard_box",
            "title": f"{product_hint} {metric} Box Plot",
            "boxes": boxes,
            "total": len(boxes),
            "x_label": "root_lot_id",
            "y_label": metric,
            "metric": metric,
            "sources": {"inline_file_count": len(inline_files), "inline_items": item_matches or [metric]},
        },
        "table": {"kind": "dashboard_box", "title": f"{metric} box plot", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in rows], "total": len(rows)},
    }


def _handle_wafer_map_chart(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    text = str(prompt or "")
    if not _is_wafer_map_chart_request(text):
        return {"handled": False}
    product_hint = _product_hint(text, product)
    if not product_hint:
        return {
            "handled": True,
            "intent": "dashboard_wafer_map_needs_context",
            "action": "collect_required_fields",
            "answer": "WF map을 그리려면 product가 필요합니다. 예: `PRODA CD_GATE WF map 그려줘`",
            "missing": ["product"],
            "feature": "dashboard",
        }
    lots = _lot_tokens(text)
    aliases = _product_aliases(product_hint)
    item_candidates: list[str] = []
    inline_needs_coord_map = False
    for source, files in _metric_map_source_order(text, product_hint):
        if not files:
            continue
        try:
            lf = _scan_parquet(files)
            cols = _schema_names(lf)
            product_col = _ci_col(cols, "product", "PRODUCT")
            root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
            lot_col = _ci_col(cols, "lot_id", "LOT_ID")
            fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
            wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
            item_col = _ci_col(cols, "item_id", "ITEM_ID", "rawitem_id", "RAWITEM_ID", "item", "ITEM")
            value_col = _ci_col(cols, "value", "VALUE", "_value", "val", "VAL")
            shot_x_col = _ci_col(cols, "shot_x", "SHOT_X", "x", "X")
            shot_y_col = _ci_col(cols, "shot_y", "SHOT_Y", "y", "Y")
            if not (item_col and value_col and shot_x_col and shot_y_col):
                if source == "INLINE" and item_col and value_col and _ci_col(cols, "subitem_id", "SUBITEM_ID"):
                    inline_needs_coord_map = True
                    if not item_candidates:
                        item_candidates = _unique_strings(lf, item_col, limit=80)
                continue
            metric, item_matches, item_candidates = _inline_metric_match_for_prompt(lf, item_col, text)
            if not metric:
                continue
            filters = []
            if aliases and product_col:
                filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
            if lots:
                lot_expr = _or_contains([c for c in (root_col, lot_col, fab_col) if c], lots)
                if lot_expr is not None:
                    filters.append(lot_expr)
            filters.append(pl.col(item_col).cast(_STR, strict=False).is_in(item_matches or [metric]))
            for expr in filters:
                lf = lf.filter(expr)
            df = (
                lf.select([
                    pl.col(shot_x_col).cast(pl.Float64, strict=False).alias("shot_x"),
                    pl.col(shot_y_col).cast(pl.Float64, strict=False).alias("shot_y"),
                    pl.col(value_col).cast(pl.Float64, strict=False).alias("value"),
                    pl.col(root_col).cast(_STR, strict=False).alias("root_lot_id") if root_col else pl.lit("").alias("root_lot_id"),
                    pl.col(wafer_col).cast(_STR, strict=False).alias("wafer_id") if wafer_col else pl.lit("").alias("wafer_id"),
                ])
                .drop_nulls(subset=["shot_x", "shot_y", "value"])
                .group_by(["shot_x", "shot_y"])
                .agg([
                    pl.col("value").median().alias("value"),
                    pl.col("value").mean().alias("mean"),
                    pl.len().alias("n"),
                    pl.col("root_lot_id").n_unique().alias("lot_count"),
                    pl.col("wafer_id").n_unique().alias("wafer_count"),
                ])
                .sort(["shot_y", "shot_x"])
                .limit(800)
                .collect()
            )
        except Exception as e:
            logger.warning("flowi wafer map chart failed source=%s: %s", source, e)
            continue
        rows = df.to_dicts()
        points = []
        for row in rows:
            points.append({
                "x": _round4(row.get("shot_x")),
                "y": _round4(row.get("shot_y")),
                "value": _round4(row.get("value")),
                "mean": _round4(row.get("mean")),
                "n": int(row.get("n") or 0),
                "lot_count": int(row.get("lot_count") or 0),
                "wafer_count": int(row.get("wafer_count") or 0),
                "label": f"shot({row.get('shot_x')},{row.get('shot_y')})",
            })
        if not points:
            continue
        answer = (
            f"{product_hint} {source} {metric}을 shot_x/shot_y 기준 median으로 집계해 WF map을 그렸습니다. "
            f"points={len(points)}, item match={', '.join(item_matches or [metric])}."
        )
        cols_out = ["shot_x", "shot_y", "value", "mean", "n", "lot_count", "wafer_count"]
        return {
            "handled": True,
            "intent": "dashboard_wafer_map_chart",
            "action": "query_metric_wafer_map",
            "answer": answer,
            "feature": "dashboard",
            "slots": {"product": product_hint, "metric": metric, "source": source, "lots": lots},
            "chart_result": {
                "ok": True,
                "kind": "dashboard_wafer_map",
                "title": f"{product_hint} {source} {metric} WF Map",
                "points": points,
                "total": len(points),
                "x_label": "shot_x",
                "y_label": "shot_y",
                "value_label": f"{metric} median",
                "metric": metric,
                "source": source,
                "sources": {"file_count": len(files), "items": item_matches or [metric]},
            },
            "table": {"kind": "dashboard_wafer_map", "title": f"{metric} WF map", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in rows[:max(1, min(120, max_rows * 8))]], "total": len(rows)},
        }
    if inline_needs_coord_map:
        return {
            "handled": True,
            "intent": "dashboard_wafer_map_needs_inline_mapping",
            "action": "collect_inline_coordinate_mapping",
            "answer": "INLINE raw DB에는 shot_x/shot_y가 없고 subitem_id만 있습니다. WF map은 inline_item_map.csv와 inline_subitem_pos.csv로 ET shot 좌표에 매핑된 파생 데이터가 있을 때 그릴 수 있습니다.",
            "missing": ["inline_item_map", "inline_subitem_pos"],
            "feature": "dashboard",
            "table": {"kind": "wafer_map_item_candidates", "title": "INLINE item candidates", "placement": "below", "columns": _table_columns(["item_id"]), "rows": [{"item_id": x} for x in item_candidates[:40]], "total": len(item_candidates)},
        }
    return {
        "handled": True,
        "intent": "dashboard_wafer_map_needs_context",
        "action": "collect_required_fields",
        "answer": "WF map으로 그릴 item 또는 ET shot_x/shot_y/value 형태의 데이터를 찾지 못했습니다. INLINE raw는 subitem_id 기반이라 좌표 매핑이 먼저 필요합니다.",
        "missing": ["item_id"],
        "feature": "dashboard",
        "table": {"kind": "wafer_map_item_candidates", "title": "WF map item candidates", "placement": "below", "columns": _table_columns(["item_id"]), "rows": [{"item_id": x} for x in item_candidates[:40]], "total": len(item_candidates)},
    }


def _handle_inline_trend_chart(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    text = str(prompt or "")
    if not (_contains_chart_intent(text) and _is_trend_chart_request(text)):
        return {"handled": False}
    product_hint = _product_hint(text, product)
    if not product_hint:
        return {
            "handled": True,
            "intent": "dashboard_inline_trend_needs_context",
            "action": "collect_required_fields",
            "answer": "Trend 차트를 그리려면 product가 필요합니다. 예: `PRODA0 SPACER_CD Trend 그려줘`",
            "missing": ["product"],
            "feature": "dashboard",
        }
    inline_files = _inline_files(product_hint)
    if not inline_files:
        return {"handled": True, "intent": "dashboard_inline_trend", "answer": f"{product_hint} INLINE parquet을 찾지 못했습니다.", "feature": "dashboard"}
    inline_lf = _scan_parquet(inline_files)
    cols = _schema_names(inline_lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    lot_wf_col = _ci_col(cols, "lot_wf", "LOT_WF")
    item_col = _ci_col(cols, "item_id", "ITEM_ID", "rawitem_id", "RAWITEM_ID", "item", "ITEM")
    value_col = _ci_col(cols, "value", "VALUE", "_value", "val", "VAL")
    time_col = _ci_col(cols, "time", "TIME", "tkout_time", "TKOUT_TIME", "tkin_time", "TKIN_TIME", "date", "DATE")
    if not item_col or not value_col or not time_col:
        return {
            "handled": True,
            "intent": "dashboard_inline_trend",
            "answer": "INLINE 데이터에서 item_id/value/time 컬럼을 찾지 못했습니다.",
            "table": {"kind": "dashboard_inline_trend_error", "title": "Missing INLINE columns", "placement": "below", "columns": _table_columns(["message", "columns"]), "rows": [{"message": "missing item_id/value/time", "columns": ", ".join(cols[:80])}], "total": 1},
            "feature": "dashboard",
        }
    metric, item_matches, item_candidates = _inline_metric_match_for_prompt(inline_lf, item_col, text)
    if not metric:
        return {
            "handled": True,
            "intent": "dashboard_inline_trend_needs_context",
            "action": "collect_required_fields",
            "answer": "Trend로 그릴 INLINE item을 찾지 못했습니다. item명을 더 정확히 알려주세요.",
            "missing": ["item_id"],
            "feature": "dashboard",
            "table": {"kind": "inline_item_candidates", "title": "INLINE item candidates", "placement": "below", "columns": _table_columns(["item_id"]), "rows": [{"item_id": x} for x in item_candidates], "total": len(item_candidates)},
        }
    aliases = _product_aliases(product_hint)
    lots = _lot_tokens(text)
    filters = []
    if aliases and product_col:
        filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    if lots:
        lot_expr = _or_contains([c for c in (root_col, lot_col, fab_col, lot_wf_col) if c], lots)
        if lot_expr is not None:
            filters.append(lot_expr)
    filters.append(pl.col(item_col).cast(_STR, strict=False).is_in(item_matches or [metric]))
    for expr in filters:
        inline_lf = inline_lf.filter(expr)

    exprs = [
        pl.col(time_col).cast(_STR, strict=False).str.slice(0, 10).alias("bucket"),
        pl.col(value_col).cast(pl.Float64, strict=False).alias("metric_value"),
    ]
    if root_col:
        exprs.append(_root_key_expr(root_col).alias("root_lot_id"))
    else:
        exprs.append(pl.lit("").alias("root_lot_id"))
    if wafer_col:
        exprs.append(_wafer_key_expr(wafer_col).alias("wafer_id"))
    else:
        exprs.append(pl.lit("").alias("wafer_id"))
    if root_col and wafer_col:
        exprs.append(_lot_wf_expr(root_col, wafer_col).alias("lot_wf"))
    elif lot_wf_col:
        exprs.append(pl.col(lot_wf_col).cast(_STR, strict=False).alias("lot_wf"))
    else:
        exprs.append(pl.lit("").alias("lot_wf"))
    try:
        line_cfg = (_flowi_chart_defaults().get("line") or FLOWI_CHART_DEFAULTS["line"])
        point_limit = max(20, min(1000, int(line_cfg.get("max_points_per_series") or 120)))
    except Exception:
        point_limit = 120
    try:
        df = (
            inline_lf.select(exprs)
            .drop_nulls(subset=["bucket", "metric_value"])
            .group_by("bucket")
            .agg([
                pl.col("metric_value").median().alias("median"),
                pl.col("metric_value").mean().alias("mean"),
                pl.len().alias("n"),
                pl.col("root_lot_id").n_unique().alias("lot_count"),
                pl.col("lot_wf").n_unique().alias("wafer_groups"),
            ])
            .sort("bucket")
            .limit(point_limit)
            .collect()
        )
    except Exception as e:
        logger.warning("flowi inline trend failed: %s", e)
        return {"handled": True, "intent": "dashboard_inline_trend", "answer": f"INLINE trend query 실패: {e}", "feature": "dashboard"}
    rows = df.to_dicts()
    points = []
    for idx, row in enumerate(rows):
        y = _round4(row.get("median"))
        if y is None:
            continue
        points.append({
            "x": idx,
            "x_label": _text(row.get("bucket")),
            "y": y,
            "median": y,
            "mean": _round4(row.get("mean")),
            "n": int(row.get("n") or 0),
            "lot_count": int(row.get("lot_count") or 0),
            "wafer_groups": int(row.get("wafer_groups") or 0),
        })
    answer = (
        f"{product_hint} {metric} INLINE 값을 날짜별 median으로 집계해 Trend 차트를 그렸습니다. "
        f"표시 point={len(points)}, item match={', '.join(item_matches or [metric])}."
    )
    if not points:
        answer = f"{product_hint} {metric} 조건으로 Trend chart row를 찾지 못했습니다."
    cols_out = ["bucket", "median", "mean", "n", "lot_count", "wafer_groups"]
    return {
        "handled": True,
        "intent": "dashboard_inline_trend_chart",
        "action": "query_inline_trend_line_chart",
        "answer": answer,
        "feature": "dashboard",
        "slots": {"product": product_hint, "metric": metric, "lots": lots},
        "chart_result": {
            "ok": True,
            "kind": "dashboard_line",
            "title": f"{product_hint} {metric} Trend",
            "series": [{"name": metric, "points": points}],
            "total": len(points),
            "x_label": "date",
            "y_label": f"{metric} median",
            "metric": metric,
            "sources": {"inline_file_count": len(inline_files), "inline_items": item_matches or [metric]},
        },
        "table": {"kind": "dashboard_inline_trend", "title": f"{metric} Trend", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in rows[:max(1, min(120, max_rows * 8))]], "total": len(rows)},
    }


def _fab_context_files(product: str) -> list[Path]:
    files = [p for p in _fab_files(product) if "1.RAWDATA_DB_FAB" in str(p) and "_backups" not in str(p)]
    return files or [p for p in _fab_files(product) if "_backups" not in str(p)] or _fab_files(product)


def _handle_grouped_metric_chart(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    text = str(prompt or "")
    if not (_contains_chart_intent(text) or "별로" in text):
        return {"handled": False}
    group_keys = _group_chart_group_keys(text)
    if not group_keys:
        return {"handled": False}
    product_hint = _product_hint(text, product)
    if not product_hint:
        return {
            "handled": True,
            "intent": "dashboard_group_metric_needs_context",
            "action": "collect_required_fields",
            "answer": "EQP/Chamber별 차트를 그리려면 product가 필요합니다. 예: `PRODA CD_GATE EQP/Chamber별로 그려줘`",
            "missing": ["product"],
            "feature": "dashboard",
        }
    inline_files = _inline_files(product_hint)
    if not inline_files:
        return {"handled": True, "intent": "dashboard_group_metric", "answer": f"{product_hint} INLINE parquet을 찾지 못했습니다.", "feature": "dashboard"}
    inline_lf = _scan_parquet(inline_files)
    inline_cols = _schema_names(inline_lf)
    product_col = _ci_col(inline_cols, "product", "PRODUCT")
    root_col = _ci_col(inline_cols, "root_lot_id", "ROOT_LOT_ID")
    wafer_col = _ci_col(inline_cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    lot_wf_col = _ci_col(inline_cols, "lot_wf", "LOT_WF")
    item_col = _ci_col(inline_cols, "item_id", "ITEM_ID", "rawitem_id", "RAWITEM_ID", "item", "ITEM")
    value_col = _ci_col(inline_cols, "value", "VALUE", "_value", "val", "VAL")
    if not item_col or not value_col:
        return {
            "handled": True,
            "intent": "dashboard_group_metric",
            "answer": "INLINE 데이터에서 item_id/value 컬럼을 찾지 못했습니다.",
            "table": {"kind": "dashboard_group_metric_error", "title": "Missing INLINE columns", "placement": "below", "columns": _table_columns(["message", "columns"]), "rows": [{"message": "missing item_id/value", "columns": ", ".join(inline_cols[:80])}], "total": 1},
            "feature": "dashboard",
        }
    if not ((root_col and wafer_col) or lot_wf_col):
        return {"handled": True, "intent": "dashboard_group_metric", "answer": "INLINE 데이터에 root_lot_id+wafer_id 또는 lot_wf join key가 필요합니다.", "feature": "dashboard"}
    metric, item_matches, item_candidates = _inline_metric_match_for_prompt(inline_lf, item_col, text)
    if not metric:
        return {
            "handled": True,
            "intent": "dashboard_group_metric_needs_context",
            "action": "collect_required_fields",
            "answer": "차트로 그릴 INLINE item을 찾지 못했습니다. item명을 더 정확히 알려주세요.",
            "missing": ["item_id"],
            "feature": "dashboard",
            "table": {"kind": "inline_item_candidates", "title": "INLINE item candidates", "placement": "below", "columns": _table_columns(["item_id"]), "rows": [{"item_id": x} for x in item_candidates], "total": len(item_candidates)},
        }
    lots = _lot_tokens(text)
    aliases = _product_aliases(product_hint)
    filters = []
    if aliases and product_col:
        filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    if lots:
        lot_cols = [c for c in (root_col, lot_wf_col) if c]
        lot_expr = _or_contains(lot_cols, lots)
        if lot_expr is not None:
            filters.append(lot_expr)
    filters.append(pl.col(item_col).cast(_STR, strict=False).is_in(item_matches or [metric]))
    for expr in filters:
        inline_lf = inline_lf.filter(expr)
    inline_exprs = []
    join_cols = []
    if root_col and wafer_col:
        inline_exprs.append(_root_key_expr(root_col).alias("root_lot_id"))
        inline_exprs.append(_wafer_key_expr(wafer_col).alias("wafer_id"))
        join_cols = ["root_lot_id", "wafer_id"]
    if root_col and wafer_col:
        inline_exprs.append(_lot_wf_expr(root_col, wafer_col).alias("lot_wf"))
    elif lot_wf_col:
        inline_exprs.append(pl.col(lot_wf_col).cast(_STR, strict=False).alias("lot_wf"))
        if not join_cols:
            join_cols = ["lot_wf"]
    inline_exprs.append(pl.col(value_col).cast(pl.Float64, strict=False).alias("metric_value"))
    inline_group_cols = list(dict.fromkeys([*join_cols, "lot_wf"]))
    try:
        metric_lf = (
            inline_lf.select(inline_exprs)
            .drop_nulls(subset=["metric_value"])
            .group_by(inline_group_cols)
            .agg([
                pl.col("metric_value").mean().alias("metric_value"),
                pl.len().alias("metric_n"),
            ])
        )
    except Exception as e:
        return {"handled": True, "intent": "dashboard_group_metric", "answer": f"INLINE metric 집계 실패: {e}", "feature": "dashboard"}

    fab_files = _fab_context_files(product_hint)
    if not fab_files:
        return {"handled": True, "intent": "dashboard_group_metric", "answer": f"{product_hint} FAB parquet을 찾지 못해 EQP/Chamber를 붙일 수 없습니다.", "feature": "dashboard"}
    fab_lf = _scan_parquet(fab_files)
    fab_cols = _schema_names(fab_lf)
    f_product_col = _ci_col(fab_cols, "product", "PRODUCT")
    f_root_col = _ci_col(fab_cols, "root_lot_id", "ROOT_LOT_ID")
    f_wafer_col = _ci_col(fab_cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    f_lot_wf_col = _ci_col(fab_cols, "lot_wf", "LOT_WF")
    eqp_col = _ci_col(fab_cols, "eqp", "EQP", "eqp_id", "EQP_ID", "equipment_id", "EQUIPMENT_ID")
    chamber_col = _ci_col(fab_cols, "chamber", "CHAMBER", "chamber_id", "CHAMBER_ID")
    time_col = _ci_col(fab_cols, "tkout_time", "TKOUT_TIME", "time", "TIME", "timestamp", "TIMESTAMP")
    if ("eqp" in group_keys and not eqp_col) or ("chamber" in group_keys and not chamber_col):
        return {
            "handled": True,
            "intent": "dashboard_group_metric",
            "answer": "FAB 데이터에서 요청한 EQP/Chamber 컬럼을 찾지 못했습니다.",
            "table": {"kind": "dashboard_group_metric_error", "title": "Missing FAB columns", "placement": "below", "columns": _table_columns(["message", "columns"]), "rows": [{"message": "missing eqp/chamber", "columns": ", ".join(fab_cols[:80])}], "total": 1},
            "feature": "dashboard",
        }
    f_filters = []
    if aliases and f_product_col:
        f_filters.append(pl.col(f_product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    if lots:
        f_lot_cols = [c for c in (f_root_col, f_lot_wf_col) if c]
        lot_expr = _or_contains(f_lot_cols, lots)
        if lot_expr is not None:
            f_filters.append(lot_expr)
    for expr in f_filters:
        fab_lf = fab_lf.filter(expr)
    fab_exprs = []
    if f_root_col and f_wafer_col and "root_lot_id" in join_cols:
        fab_exprs.append(_root_key_expr(f_root_col).alias("root_lot_id"))
        fab_exprs.append(_wafer_key_expr(f_wafer_col).alias("wafer_id"))
        fab_join_cols = ["root_lot_id", "wafer_id"]
    elif f_root_col and f_wafer_col:
        fab_exprs.append(_lot_wf_expr(f_root_col, f_wafer_col).alias("lot_wf"))
        fab_join_cols = ["lot_wf"]
    elif f_lot_wf_col:
        fab_exprs.append(pl.col(f_lot_wf_col).cast(_STR, strict=False).alias("lot_wf"))
        fab_join_cols = ["lot_wf"]
    else:
        return {"handled": True, "intent": "dashboard_group_metric", "answer": "FAB 데이터에 metric과 연결할 root_lot_id+wafer_id 또는 lot_wf가 필요합니다.", "feature": "dashboard"}
    if eqp_col:
        fab_exprs.append(pl.col(eqp_col).cast(_STR, strict=False).alias("eqp"))
    else:
        fab_exprs.append(pl.lit("").alias("eqp"))
    if chamber_col:
        fab_exprs.append(pl.col(chamber_col).cast(_STR, strict=False).alias("chamber"))
    else:
        fab_exprs.append(pl.lit("").alias("chamber"))
    fab_exprs.append(pl.col(time_col).cast(_STR, strict=False).alias("latest_time") if time_col else pl.lit("").alias("latest_time"))
    try:
        fab_ctx = (
            fab_lf.select(fab_exprs)
            .drop_nulls(subset=[g for g in group_keys if g in {"eqp", "chamber"}])
            .group_by([*fab_join_cols, "eqp", "chamber"])
            .agg([
                pl.len().alias("fab_context_rows"),
                pl.col("latest_time").max().alias("latest_time"),
            ])
        )
        joined = metric_lf.join(fab_ctx, on=fab_join_cols, how="inner")
        group_exprs = [
            pl.col("metric_value").mean().alias("mean"),
            pl.col("metric_value").median().alias("median"),
            pl.len().alias("joined_rows"),
            pl.col("lot_wf").n_unique().alias("wafer_groups") if "lot_wf" in joined.collect_schema().names() else pl.len().alias("wafer_groups"),
            pl.col("metric_n").sum().alias("metric_n"),
            pl.col("fab_context_rows").sum().alias("fab_context_rows"),
        ]
        grouped = (
            joined.group_by(group_keys)
            .agg(group_exprs)
            .sort("median", descending=True)
            .limit(max(5, min(40, max_rows * 4)))
            .collect()
        )
    except Exception as e:
        logger.warning("flowi grouped metric chart failed: %s", e)
        return {"handled": True, "intent": "dashboard_group_metric", "answer": f"EQP/Chamber별 chart query 실패: {e}", "feature": "dashboard"}
    rows = grouped.to_dicts()
    groups = []
    for row in rows:
        label = " / ".join(_text(row.get(k)) or "-" for k in group_keys)
        groups.append({
            "label": label,
            "value": _round4(row.get("median")),
            "mean": _round4(row.get("mean")),
            "median": _round4(row.get("median")),
            "joined_rows": int(row.get("joined_rows") or 0),
            "wafer_groups": int(row.get("wafer_groups") or 0),
            "metric_n": int(row.get("metric_n") or 0),
            "fab_context_rows": int(row.get("fab_context_rows") or 0),
            **{k: row.get(k) or "" for k in group_keys},
        })
    cols_out = [*group_keys, "median", "mean", "joined_rows", "wafer_groups", "metric_n", "fab_context_rows"]
    answer = (
        f"{product_hint} {metric}을 실제 INLINE 값에 FAB EQP/Chamber context를 붙여 {len(groups)}개 그룹으로 그렸습니다. "
        "집계값은 그룹별 median 기준이며, join은 root_lot_id+wafer_id 우선입니다."
    )
    if not groups:
        answer = f"{product_hint} {metric} 조건으로 EQP/Chamber별 chart row를 찾지 못했습니다."
    return {
        "handled": True,
        "intent": "dashboard_group_metric_chart",
        "action": "query_group_metric_bar_chart",
        "answer": answer,
        "feature": "dashboard",
        "slots": {"product": product_hint, "metric": metric, "group_by": group_keys, "lots": lots},
        "chart_result": {
            "ok": True,
            "kind": "dashboard_group_bar",
            "title": f"{product_hint} {metric} by {'/'.join(group_keys).upper()}",
            "groups": groups,
            "total": len(groups),
            "x_label": " / ".join(group_keys),
            "y_label": f"{metric} median",
            "metric": metric,
            "group_by": group_keys,
            "join_cols": fab_join_cols,
            "sources": {"inline_file_count": len(inline_files), "fab_file_count": len(fab_files), "inline_items": item_matches or [metric]},
        },
        "table": {"kind": "dashboard_group_metric", "title": f"{metric} by {'/'.join(group_keys)}", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in rows[:max(1, min(120, max_rows * 8))]], "total": len(rows)},
    }


def _handle_chart_request(prompt: str, product: str, max_rows: int) -> dict:
    if not _contains_chart_intent(prompt):
        return {"handled": False}
    chart_defaults = _flowi_chart_defaults()
    scatter_defaults = chart_defaults.get("scatter") or FLOWI_CHART_DEFAULTS["scatter"]
    inline_agg = scatter_defaults.get("inline_agg") if scatter_defaults.get("inline_agg") in {"avg", "median"} else "avg"
    et_agg = scatter_defaults.get("et_agg") if scatter_defaults.get("et_agg") in {"avg", "median"} else "median"
    sources = _source_terms(prompt)
    metrics = _metric_alias_hits(prompt)
    operations = _chart_operations(prompt)
    lots = _lot_tokens(prompt)
    product_hint = _product_hint(prompt, product)
    join_key = _chart_default_join_key(sources)
    requires = []
    if len(sources) < 2 and "correlation" in operations:
        requires.append("x/y source")
    if len(metrics) < 2 and ("correlation" in operations or "scatter" in operations):
        requires.append("x/y metric")
    if not product_hint:
        requires.append("product")
    rows = [
        {"field": "unit_action", "value": "dashboard.metric_scatter"},
        {"field": "sources", "value": ", ".join(sorted(sources)) or "-"},
        {"field": "metrics", "value": ", ".join(m["metric"] for m in metrics) or "-"},
        {"field": "operations", "value": ", ".join(operations)},
        {"field": "join_key_priority", "value": "WF Agg(root_lot_id+wafer_id/lot_wf) 기본; shot/die는 명시 요청 시"},
        {"field": "INLINE aggregation", "value": f"{inline_agg} by wafer by default"},
        {"field": "ET aggregation", "value": f"{et_agg} by wafer by default"},
        {"field": "join_default", "value": "left join; ambiguous direction must be confirmed"},
        {"field": "anti_fabrication", "value": "schema catalog and DB rows only; no invented columns/data"},
    ]
    if product_hint:
        rows.append({"field": "product", "value": product_hint})
    if lots:
        rows.append({"field": "lot_filter", "value": ", ".join(lots)})
    if requires:
        rows.append({"field": "needs_clarification", "value": ", ".join(requires)})

    choices = []
    for choice in FLOWI_JOIN_CHOICES:
        next_prompt = f"{prompt.strip()} / {choice['prompt_suffix']}"
        choices.append({**choice, "prompt": next_prompt})
    if requires:
        choices.insert(0, {
            "id": "open_schema_search",
            "label": "0",
            "title": "schema 후보 먼저 찾기",
            "recommended": True,
            "description": "실제 DB schema catalog에서 INLINE/ET/ML_TABLE 컬럼 후보를 먼저 확인합니다.",
            "prompt": f"{prompt.strip()} / schema 후보 먼저 확인",
        })
        # Keep only one recommended marker in the rendered list.
        for item in choices[1:]:
            item["recommended"] = False

    chart = {
        "kind": "scatter",
        "status": "planned",
        "sources": sorted(sources),
        "metrics": metrics,
        "operations": operations,
        "join_key": join_key,
        "aggregations": {"INLINE": inline_agg, "ET": et_agg},
        "render_preset": scatter_defaults,
        "render_target": "dashboard",
        "requires": requires,
    }
    chart_result = None
    if not requires:
        actual = _try_metric_scatter(prompt, product_hint, metrics, lots, operations)
        if actual.get("ok"):
            chart_result = actual
            chart["status"] = "computed"
        else:
            chart["status"] = "planned"
            chart["execution_error"] = actual.get("error") or "chart execution failed"
    answer = (
        "차트/상관 분석 단위기능으로 처리할 요청입니다. "
        "Flowi는 metric 이름을 지어내지 않고 schema catalog와 실제 DB row로만 차트를 만듭니다.\n"
        f"- 감지 source: {', '.join(sorted(sources)) or '-'}\n"
        f"- 감지 metric 후보: {', '.join(m['metric'] for m in metrics) or '-'}\n"
        f"- 기본 집계: INLINE {inline_agg}, ET {et_agg}\n"
        "- 기본은 WF Agg입니다. shot/die/map을 명시한 경우에만 shot 단위 매칭을 시도합니다."
    )
    if requires:
        answer += "\n아래 선택지에서 먼저 확인할 범위를 골라주세요."
    elif chart_result:
        answer += (
            f"\n실제 DB 기준 scatter를 계산했습니다. n={chart_result.get('total', 0)}, "
            f"corr={chart_result.get('corr') if chart_result.get('corr') is not None else '-'}."
        )
    else:
        answer += "\n조건은 충분하지만 실제 차트 계산에 실패했습니다. 아래 계획과 오류를 확인해주세요."
    return {
        "handled": True,
        "intent": "dashboard_scatter_plan",
        "action": "build_metric_scatter",
        "answer": answer,
        "slots": {
            "product": product_hint,
            "lots": lots,
            "sources": sorted(sources),
            "metrics": [m["metric"] for m in metrics],
            "operations": operations,
        },
        "chart": chart,
        "chart_result": chart_result,
        "clarification": {
            "question": "어떤 기준으로 실제 DB query를 만들까요?",
            "choices": choices[:3],
        },
        "table": {
            "kind": "flowi_chart_plan",
            "title": "Flowi chart/query plan",
            "placement": "below",
            "columns": [{"key": "field", "label": "FIELD"}, {"key": "value", "label": "VALUE"}],
            "rows": rows[:max(1, max_rows)],
            "total": len(rows),
        },
    }


def _matches_any(value: str, needles: set[str]) -> bool:
    val = _upper(value)
    return any(n and (val == n or n in val) for n in needles)


def _filter_files_by_product(files: list[Path], product: str) -> list[Path]:
    aliases = _product_aliases(product)
    if not aliases:
        return files
    out = []
    for fp in files:
        parts = {_upper(fp.stem), _upper(fp.parent.name)}
        parts.update(_upper(p) for p in fp.parts[-6:])
        if any(_matches_any(p, aliases) or _matches_any(p.replace("ML_TABLE_", ""), aliases) for p in parts):
            out.append(fp)
    return out


def _scan_parquet(files: list[Path]) -> pl.LazyFrame:
    if not files:
        raise HTTPException(404, "읽을 parquet 파일이 없습니다")
    lf = pl.scan_parquet([str(p) for p in files])
    try:
        from core.utils import filter_valid_wafer_ids_lazy
        return filter_valid_wafer_ids_lazy(lf)
    except Exception:
        return lf


def _schema_names(lf: pl.LazyFrame) -> list[str]:
    try:
        return list(lf.collect_schema().names())
    except Exception:
        return list(lf.schema.keys())


def _ci_col(cols: list[str], *candidates: str) -> str:
    by_lower = {c.lower(): c for c in cols}
    for cand in candidates:
        hit = by_lower.get(str(cand).lower())
        if hit:
            return hit
    return ""


def _db_root_candidates(kind: str) -> list[Path]:
    base = PATHS.db_root
    kind_u = kind.upper()
    if not base.exists():
        return []
    roots = []
    if base.is_dir() and kind_u in base.name.upper():
        roots.append(base)
    try:
        for child in sorted(base.iterdir()):
            if child.is_dir() and kind_u in child.name.upper():
                roots.append(child)
    except Exception:
        pass
    return roots


def _et_files(product: str) -> list[Path]:
    files: list[Path] = []
    for root in _db_root_candidates("ET"):
        files.extend(sorted(root.rglob("*.parquet")))
    return _filter_files_by_product(files, product)


def _ml_files(product: str) -> list[Path]:
    roots = []
    for root in (PATHS.base_root, PATHS.db_root):
        try:
            if root.exists() and root not in roots:
                roots.append(root)
        except Exception:
            pass
    files: list[Path] = []
    for root in roots:
        try:
            files.extend(sorted(root.glob("ML_TABLE_*.parquet")))
        except Exception:
            pass
    dedup = []
    seen = set()
    for fp in files:
        key = str(fp.resolve()) if fp.exists() else str(fp)
        if key in seen:
            continue
        seen.add(key)
        dedup.append(fp)
    return _filter_files_by_product(dedup, product)


def _unique_strings(lf: pl.LazyFrame, col: str, limit: int = 200) -> list[str]:
    if not col:
        return []
    try:
        vals = (
            lf.select(pl.col(col).cast(_STR, strict=False).drop_nulls().unique().alias(col))
            .limit(limit)
            .collect()[col]
            .to_list()
        )
    except Exception:
        return []
    return [_text(v) for v in vals if _text(v)]


def _match_values(values: list[str], needles: list[str]) -> list[str]:
    clean = [_upper(n) for n in needles if _upper(n) and _upper(n) not in _STOP_TOKENS]
    if not clean:
        return []
    exact = [v for v in values if _upper(v) in clean]
    if exact:
        return sorted(set(exact))
    contains = [v for v in values if any(n in _upper(v) for n in clean)]
    return sorted(set(contains))


def _core_product_name(product: str) -> str:
    raw = _text(product)
    if raw.upper().startswith("ML_TABLE_"):
        return raw[len("ML_TABLE_"):].strip()
    return raw


def _column_matches(cols: list[str], terms: list[str], *, include_knob_when_named: bool = False) -> list[str]:
    clean = []
    seen_terms = set()
    for term in terms:
        key = _upper(term)
        if not key or key in _STOP_TOKENS or key in FLOWI_CHART_METRIC_STOP:
            continue
        if key in seen_terms:
            continue
        seen_terms.add(key)
        clean.append(key)
    out = []
    seen = set()
    for col in cols:
        col_u = _upper(col)
        body = col_u.replace("KNOB_", "", 1)
        if include_knob_when_named and "KNOB" in clean and col_u.startswith("KNOB_"):
            hit = True
        else:
            hit = any(t == col_u or t == body or t in col_u or t in body for t in clean)
        if hit and col not in seen:
            seen.add(col)
            out.append(col)
    return out


def _flowi_value_lookup_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    if ("스플릿테이블" in text or "split table" in low or "splittable" in low) and not any(t in low or t in text for t in ("값", "얼마", "sql", "select", "where", "db", "files", "파일탐색기", "조회", "검색", "찾")):
        return False
    return any(t in low or t in text for t in (
        "값", "얼마", "몇", "찾", "조회", "검색", "sql", "select", "where",
        "파일탐색기", "파일 탐색기", "files", "filebrowser", "db",
    ))


def _table_columns(keys: list[str]) -> list[dict[str, str]]:
    return [{"key": key, "label": key.upper()} for key in keys]


def _fab_files(product: str = "") -> list[Path]:
    files: list[Path] = []
    for root in _db_root_candidates("FAB"):
        files.extend(sorted(root.rglob("*.parquet")))
    return _filter_files_by_product(files, product)


def _wafer_tokens(prompt: str) -> list[str]:
    text = str(prompt or "")
    out: list[str] = []
    seen: set[str] = set()
    def add(raw: Any) -> None:
        val = _normalize_wafer_id(raw)
        if val and val not in seen:
            seen.add(val)
            out.append(val)
    def add_range(a: Any, b: Any) -> None:
        try:
            start, end = int(a), int(b)
        except Exception:
            return
        if start > end:
            start, end = end, start
        for n in range(max(1, start), min(FLOWI_MAX_WAFER_ID, end) + 1):
            add(n)
    range_patterns = [
        r"#\s*0?(\d{1,2})\s*(?:~|-|–|—|to)\s*#?\s*0?(\d{1,2})",
        r"\b(?:WF|WAFER)\s*0?(\d{1,2})\s*(?:~|-|–|—|to)\s*(?:WF|WAFER)?\s*0?(\d{1,2})\b",
        r"\b(?:SLOT|슬롯)\s*0?(\d{1,2})\s*(?:~|-|–|—|to)\s*(?:SLOT|슬롯)?\s*0?(\d{1,2})\b",
        r"웨이퍼\s*0?(\d{1,2})\s*(?:~|-|–|—|부터)\s*(?:웨이퍼\s*)?0?(\d{1,2})",
        r"0?(\d{1,2})\s*번\s*(?:~|-|–|—|부터)\s*0?(\d{1,2})\s*번",
        r"0?(\d{1,2})\s*장\s*(?:~|-|–|—|부터)\s*0?(\d{1,2})\s*장",
    ]
    for pat in range_patterns:
        for m in re.finditer(pat, text, flags=re.I):
            add_range(m.group(1), m.group(2))
    patterns = [
        r"#\s*(\d{1,2})(?=\D|$)",
        r"\bWF\s*0?(\d{1,2})\b",
        r"\bWAFER\s*0?(\d{1,2})\b",
        r"\bSLOT\s*0?(\d{1,2})\b",
        r"슬롯\s*0?(\d{1,2})",
        r"웨이퍼\s*0?(\d{1,2})",
        r"(\d{1,2})\s*번\s*(?:WF|WAFER|웨이퍼)",
        r"(\d{1,2})\s*번\s*(?:SLOT|슬롯)",
        r"(\d{1,2})\s*번\s*장",
        r"(\d{1,2})\s*번장",
        r"(\d{1,2})\s*장\b",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text, flags=re.I):
            add(m.group(1))
    return out


def _wafer_match_expr(col: str, wafers: list[str]):
    if not col or not wafers:
        return None
    vals: set[str] = set()
    for raw in wafers:
        val = _normalize_wafer_id(raw)
        if val:
            vals.add(val)
    if not vals:
        return pl.lit(False)
    return _wafer_key_expr(col).is_in(sorted(vals))


def _step_meta(product: str, step_id: Any) -> dict[str, Any]:
    try:
        from core.lot_step import lookup_step_meta
        meta = lookup_step_meta(product=product, step_id=step_id)
        return meta if isinstance(meta, dict) else {}
    except Exception:
        return {}


def _function_step_label(product: str, step_id: Any) -> str:
    meta = _step_meta(product, step_id)
    return _text(meta.get("func_step") or meta.get("function_step") or meta.get("step_desc"))


def _source_filter_lots(lf: pl.LazyFrame, cols: list[str], lots: list[str]) -> pl.LazyFrame:
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
    lot_wf_col = _ci_col(cols, "lot_wf", "LOT_WF")
    expr = _or_contains([c for c in (root_col, lot_col, fab_col, lot_wf_col) if c], lots)
    return lf.filter(expr) if expr is not None else lf


def _is_current_fab_lot_prompt(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    if not _lot_tokens(text):
        return False
    mentions_fab_lot = any(t in low for t in ("fab_lot", "fab lot", "fab-lot", "fablot"))
    mentions_lot_id_with_fab = "fab" in low and ("lot id" in low or "lot_id" in low)
    if not (mentions_fab_lot or mentions_lot_id_with_fab):
        return False
    return any(t in low or t in text for t in ("현재", "지금", "current", "now", "뭐야", "무엇", "알려", "찾", "조회", "확인"))


def _handle_current_fab_lot_lookup(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _is_current_fab_lot_prompt(prompt):
        return {"handled": False}
    lots = _lot_tokens(prompt)
    product_hint, candidate_tool = _product_or_candidate_tool(
        prompt, product, lots, kinds=("FAB",), intent="current_fab_lot_lookup"
    )
    if candidate_tool:
        return candidate_tool
    if not product_hint:
        return {
            "handled": True,
            "intent": "current_fab_lot_lookup",
            "action": "collect_required_fields",
            "answer": "현재 fab_lot_id를 FAB DB에서 찾으려면 product가 필요합니다. 예: `PRODA A1000 #6 현재 fab lot id가 뭐야?`",
            "missing": ["product"],
            "feature": "filebrowser",
        }
    files = _fab_files(product_hint)
    if not files:
        return {
            "handled": True,
            "intent": "current_fab_lot_lookup",
            "answer": f"{product_hint} FAB parquet을 찾지 못했습니다. DB root와 product명을 확인해주세요.",
            "table": {"kind": "current_fab_lot_lookup", "title": "Current FAB lot", "placement": "below", "columns": _table_columns(["message"]), "rows": [{"message": "FAB not found"}], "total": 0},
            "feature": "filebrowser",
        }
    wafers = _wafer_tokens(prompt)
    try:
        lf = _scan_parquet(files)
        cols = _schema_names(lf)
        product_col = _ci_col(cols, "product", "PRODUCT")
        root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
        lot_col = _ci_col(cols, "lot_id", "LOT_ID")
        fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID") or lot_col
        wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
        step_col = _ci_col(cols, "step_id", "STEP_ID")
        process_col = _ci_col(cols, "process_id", "PROCESS_ID")
        time_col = _ci_col(cols, "tkout_time", "TKOUT_TIME", "time", "TIME", "timestamp", "TIMESTAMP", "move_time", "MOVE_TIME", "updated_at", "UPDATED_AT")
        if not fab_col or not (root_col or lot_col):
            return {"handled": True, "intent": "current_fab_lot_lookup", "answer": "FAB 데이터에서 root_lot_id/lot_id/fab_lot_id 컬럼을 찾지 못했습니다.", "feature": "filebrowser"}
        aliases = _product_aliases(product_hint)
        if aliases and product_col:
            lf = lf.filter(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
        lf = _source_filter_lots(lf, cols, lots)
        if wafers and wafer_col:
            wf_expr = _wafer_match_expr(wafer_col, wafers)
            if wf_expr is not None:
                lf = lf.filter(wf_expr)
        exprs = [
            pl.col(product_col).cast(_STR, strict=False).alias("product") if product_col else pl.lit(_core_product_name(product_hint)).alias("product"),
            pl.col(root_col).cast(_STR, strict=False).alias("root_lot_id") if root_col else (
                pl.col(lot_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id") if lot_col else pl.lit("").alias("root_lot_id")
            ),
            pl.col(lot_col).cast(_STR, strict=False).alias("lot_id") if lot_col else pl.lit("").alias("lot_id"),
            pl.col(fab_col).cast(_STR, strict=False).alias("fab_lot_id"),
            _wafer_key_expr(wafer_col).alias("wafer_id") if wafer_col else pl.lit("").alias("wafer_id"),
            pl.col(step_col).cast(_STR, strict=False).alias("step_id") if step_col else pl.lit("").alias("step_id"),
            pl.col(process_col).cast(_STR, strict=False).alias("process_id") if process_col else pl.lit("").alias("process_id"),
            pl.col(time_col).cast(_STR, strict=False).alias("time") if time_col else pl.lit("").alias("time"),
        ]
        df = lf.select(exprs).drop_nulls(subset=["fab_lot_id"]).limit(50000).collect()
    except Exception as e:
        return {"handled": True, "intent": "current_fab_lot_lookup", "answer": f"FAB DB fab_lot_id 조회 실패: {e}", "feature": "filebrowser"}
    rows_all = [r for r in df.to_dicts() if _text(r.get("fab_lot_id"))]
    if not rows_all:
        wafer_text = f" wafer #{', #'.join(wafers)}" if wafers else ""
        return {
            "handled": True,
            "intent": "current_fab_lot_lookup",
            "answer": f"{product_hint} {', '.join(lots)}{wafer_text}에 해당하는 FAB row를 찾지 못했습니다.",
            "table": {"kind": "current_fab_lot_lookup", "title": "Current FAB lot", "placement": "below", "columns": _table_columns(["message"]), "rows": [{"message": "No FAB row matched"}], "total": 0},
            "filters": {"product": product_hint, "lots": lots, "wafers": wafers},
            "feature": "filebrowser",
        }
    def sort_key(row: dict[str, Any]):
        dt = _parse_flowi_datetime(row.get("time"))
        return (dt or datetime.min, _text(row.get("step_id")), _text(row.get("fab_lot_id")))
    rows_all.sort(key=sort_key, reverse=True)
    current = rows_all[0]
    cols_out = ["product", "root_lot_id", "wafer_id", "fab_lot_id", "lot_id", "step_id", "process_id", "time"]
    rows = [{k: r.get(k, "") for k in cols_out} for r in rows_all[:max(1, min(max_rows, 25))]]
    wafer_label = f" wafer #{current.get('wafer_id')}" if current.get("wafer_id") else ""
    answer = (
        f"{current.get('product') or product_hint} {current.get('root_lot_id') or lots[0]}{wafer_label}의 현재 fab_lot_id는 "
        f"`{current.get('fab_lot_id')}`입니다."
    )
    if current.get("time") or current.get("step_id"):
        answer += f" 기준 row: step_id={current.get('step_id') or '-'}, time={current.get('time') or '-'}."
    return {
        "handled": True,
        "intent": "current_fab_lot_lookup",
        "action": "query_current_fab_lot_from_fab_db",
        "answer": answer,
        "table": {"kind": "current_fab_lot_lookup", "title": "Current FAB lot", "placement": "below", "columns": _table_columns(cols_out), "rows": rows, "total": len(rows_all)},
        "filters": {"product": product_hint, "lots": lots, "wafers": wafers, "source": "FAB"},
        "feature": "filebrowser",
    }


def _resolve_products_for_lots(lots: list[str], *, kinds: tuple[str, ...] = ("FAB", "ET", "INLINE", "ML_TABLE"), limit: int = 12) -> list[dict[str, Any]]:
    clean_lots = [x for x in dict.fromkeys(_text(v) for v in lots) if x]
    if not clean_lots:
        return []
    out: dict[str, dict[str, Any]] = {}
    for kind in kinds:
        if kind == "FAB":
            files = _fab_files("")
        elif kind == "ET":
            files = _et_files("")
        elif kind == "INLINE":
            files = _inline_files("")
        elif kind == "ML_TABLE":
            files = _ml_files("")
        else:
            files = []
        if not files:
            continue
        try:
            lf = _scan_parquet(files[:240])
            cols = _schema_names(lf)
            product_col = _ci_col(cols, "product", "PRODUCT")
            root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
            lot_col = _ci_col(cols, "lot_id", "LOT_ID")
            fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
            if not (root_col or lot_col or fab_col):
                continue
            lf = _source_filter_lots(lf, cols, clean_lots)
            exprs = []
            if product_col:
                exprs.append(pl.col(product_col).cast(_STR, strict=False).alias("product"))
            else:
                exprs.append(pl.lit("").alias("product"))
            if root_col:
                exprs.append(pl.col(root_col).cast(_STR, strict=False).alias("root_lot_id"))
            elif lot_col:
                exprs.append(pl.col(lot_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id"))
            else:
                exprs.append(pl.col(fab_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id"))
            df = lf.select(exprs).drop_nulls(subset=["root_lot_id"]).limit(300).collect()
        except Exception:
            continue
        for row in df.to_dicts():
            product = _text(row.get("product"))
            root = _text(row.get("root_lot_id"))
            if not product:
                continue
            key = product.upper()
            cur = out.setdefault(key, {"product": product, "sources": set(), "lots": set(), "row_count": 0})
            cur["sources"].add(kind)
            if root:
                cur["lots"].add(root)
            cur["row_count"] += 1
    rows = []
    for rec in out.values():
        rows.append({
            "product": rec["product"],
            "sources": ",".join(sorted(rec["sources"])),
            "lots": ",".join(sorted(rec["lots"])[:8]),
            "row_count": rec["row_count"],
        })
    rows.sort(key=lambda r: (-int(r.get("row_count") or 0), r.get("product") or ""))
    return rows[:limit]


def _product_or_candidate_tool(prompt: str, product: str, lots: list[str], *, kinds: tuple[str, ...], intent: str) -> tuple[str, dict[str, Any] | None]:
    product_hint = _product_hint(prompt, product)
    if product_hint:
        return product_hint, None
    candidates = _resolve_products_for_lots(lots, kinds=kinds)
    if len(candidates) == 1:
        return candidates[0]["product"], None
    if len(candidates) > 1:
        rows = candidates
        choices = [
            {
                "id": f"product_{i}",
                "label": str(i + 1),
                "title": row["product"],
                "recommended": i == 0,
                "description": f"{row['sources']}에서 {row['row_count']} row 후보",
                "prompt": f"{row['product']} {prompt.strip()}",
            }
            for i, row in enumerate(rows[:4])
        ]
        return "", {
            "handled": True,
            "intent": intent,
            "action": "clarify_product",
            "answer": "같은 lot/root_lot_id가 여러 product에서 발견됐습니다. product를 선택한 뒤 다시 진행해주세요.",
            "clarification": {"question": "어느 product 기준으로 볼까요?", "choices": choices},
            "table": {
                "kind": "flowi_product_candidates",
                "title": "Product candidates by lot",
                "placement": "below",
                "columns": _table_columns(["product", "sources", "lots", "row_count"]),
                "rows": rows,
                "total": len(rows),
            },
        }
    return "", None


def _step_query_terms(prompt: str, lots: list[str], product: str = "") -> list[str]:
    blocked = set(_STOP_TOKENS) | {
        "EQP", "EQUIPMENT", "장비", "설비", "STEP", "STEP_ID", "PROCESS_ID", "PROCESS",
        "FAB", "DB", "PRODUCT", "PROD",
    }
    blocked.update(_upper(v) for v in lots)
    blocked.update(_product_aliases(product))
    out: list[str] = []
    seen: set[str] = set()
    for tok in _query_tokens(prompt):
        key = _upper(tok)
        if not key or key in blocked or key.startswith("PROD"):
            continue
        if re.fullmatch(r"[A-Z]\d{4,}(?:[A-Z])?(?:\.\d+)?", key):
            continue
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out[:8]


def _is_fab_eqp_prompt(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    return any(t in low or t in text for t in _FLOWI_FAB_EQP_TERMS) and any(t in low or t in text for t in _FLOWI_STEP_WORDS)


def _handle_fab_eqp_lookup(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _is_fab_eqp_prompt(prompt):
        return {"handled": False}
    lots = _lot_tokens(prompt)
    if not lots:
        return {"handled": False}
    product_hint, candidate_tool = _product_or_candidate_tool(prompt, product, lots, kinds=("FAB",), intent="fab_eqp_lookup")
    if candidate_tool:
        return candidate_tool
    files = _fab_files(product_hint)
    if not files:
        return {
            "handled": True,
            "intent": "fab_eqp_lookup",
            "answer": "FAB parquet을 찾지 못했습니다. product 또는 DB root를 확인해주세요.",
            "table": {"kind": "fab_eqp_lookup", "title": "FAB EQP lookup", "placement": "below", "columns": _table_columns(["message"]), "rows": [{"message": "FAB not found"}], "total": 0},
        }
    lf = _scan_parquet(files)
    cols = _schema_names(lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    step_col = _ci_col(cols, "step_id", "STEP_ID")
    eqp_col = _ci_col(cols, "eqp_id", "EQP_ID", "equipment_id", "EQUIPMENT_ID")
    chamber_col = _ci_col(cols, "chamber_id", "CHAMBER_ID")
    ppid_col = _ci_col(cols, "ppid", "PPID")
    reticle_col = _ci_col(cols, "reticle_id", "RETICLE_ID")
    tkin_col = _ci_col(cols, "tkin_time", "TKIN_TIME", "start_time", "START_TIME")
    tkout_col = _ci_col(cols, "tkout_time", "TKOUT_TIME", "end_time", "END_TIME", "time", "TIME", "timestamp", "TIMESTAMP")
    if not step_col or not eqp_col:
        return {
            "handled": True,
            "intent": "fab_eqp_lookup",
            "answer": "FAB 데이터에서 step_id 또는 eqp_id 컬럼을 찾지 못했습니다.",
            "table": {"kind": "fab_eqp_lookup", "title": "FAB EQP lookup", "placement": "below", "columns": _table_columns(["message", "columns"]), "rows": [{"message": "missing step_id/eqp_id", "columns": ", ".join(cols[:40])}], "total": 1},
        }
    aliases = _product_aliases(product_hint)
    filters = []
    if aliases and product_col:
        filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    if lots:
        expr = _or_contains([c for c in (root_col, lot_col, fab_col) if c], lots)
        if expr is not None:
            filters.append(expr)
    wafers = _wafer_tokens(prompt)
    wf_expr = _wafer_match_expr(wafer_col, wafers)
    if wf_expr is not None:
        filters.append(wf_expr)
    for expr in filters:
        lf = lf.filter(expr)
    select_exprs = []
    for src, alias in (
        (product_col, "product"), (root_col, "root_lot_id"), (lot_col, "lot_id"), (fab_col, "fab_lot_id"),
        (wafer_col, "wafer_id"), (step_col, "step_id"), (eqp_col, "eqp_id"), (chamber_col, "chamber_id"),
        (ppid_col, "ppid"), (reticle_col, "reticle_id"), (tkin_col, "tkin_time"), (tkout_col, "tkout_time"),
    ):
        select_exprs.append(pl.col(src).cast(_STR, strict=False).alias(alias) if src else pl.lit("").alias(alias))
    try:
        df = lf.select(select_exprs).limit(5000).collect()
    except Exception as e:
        return {"handled": True, "intent": "fab_eqp_lookup", "answer": f"FAB EQP 조회 실패: {e}"}
    rows_all = df.to_dicts()
    terms = _step_query_terms(prompt, lots, product_hint)
    lot_set = {_upper(v) for v in lots}
    step_ids = {s for s in _step_tokens(prompt) if _upper(s) not in lot_set}
    rows = []
    for row in rows_all:
        func = _function_step_label(row.get("product") or product_hint, row.get("step_id"))
        hay = _upper(" ".join([
            row.get("step_id") or "",
            func,
            row.get("eqp_id") or "",
            row.get("chamber_id") or "",
            row.get("ppid") or "",
            row.get("reticle_id") or "",
        ]))
        if step_ids and _upper(row.get("step_id")) not in step_ids:
            continue
        if terms and not any(term in hay for term in terms):
            continue
        if not func and terms:
            func = next((term for term in terms if term in hay), "")
        row["function_step"] = func
        rows.append(row)
    if terms and not rows and rows_all:
        candidates = []
        seen = set()
        for row in rows_all:
            sid = _text(row.get("step_id"))
            if not sid or sid in seen:
                continue
            seen.add(sid)
            candidates.append({"step_id": sid, "function_step": _function_step_label(row.get("product") or product_hint, sid)})
        return {
            "handled": True,
            "intent": "fab_eqp_lookup",
            "action": "clarify_function_step",
            "answer": f"{', '.join(terms)}와 매칭되는 function step을 찾지 못했습니다. 아래 step 후보 중에서 선택해주세요.",
            "table": {
                "kind": "fab_step_candidates",
                "title": "FAB step candidates for lot",
                "placement": "below",
                "columns": _table_columns(["step_id", "function_step"]),
                "rows": candidates[:max(1, max_rows * 2)],
                "total": len(candidates),
            },
        }
    rows = rows or rows_all
    rows.sort(key=lambda r: str(r.get("tkout_time") or r.get("tkin_time") or ""), reverse=True)
    display_cols = ["product", "root_lot_id", "wafer_id", "step_id", "function_step", "eqp_id", "chamber_id", "ppid", "reticle_id", "lot_id", "fab_lot_id", "tkin_time", "tkout_time"]
    shown = [{k: row.get(k, "") for k in display_cols} for row in rows[:max(1, min(80, max_rows * 6))]]
    top = shown[0] if shown else {}
    answer = (
        f"{top.get('product') or product_hint or '-'} {', '.join(lots)} 기준 FAB EQP를 조회했습니다. "
        f"대표 결과: {top.get('step_id') or '-'}{('(' + top.get('function_step') + ')') if top.get('function_step') else ''} "
        f"EQP={top.get('eqp_id') or '-'}."
    )
    return {
        "handled": True,
        "intent": "fab_eqp_lookup",
        "action": "query_fab_eqp_by_function_step",
        "answer": answer,
        "table": {
            "kind": "fab_eqp_lookup",
            "title": "FAB EQP by step/function step",
            "placement": "below",
            "columns": _table_columns(display_cols),
            "rows": shown,
            "total": len(rows),
        },
        "filters": {"product": product_hint, "lots": lots, "wafers": wafers, "step_terms": terms},
    }


def _is_process_id_prompt(prompt: str) -> bool:
    up = _upper(prompt)
    return "PROCESS_ID" in up or "PROCESS ID" in up or "공정ID" in up or "프로세스" in prompt


def _handle_product_process_id_lookup(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _is_process_id_prompt(prompt):
        return {"handled": False}
    product_hint = _product_hint(prompt, product)
    if not product_hint:
        return {"handled": False}
    files = _fab_files(product_hint) or _ml_files(product_hint)
    source = "FAB" if _fab_files(product_hint) else "ML_TABLE"
    if not files:
        return {"handled": True, "intent": "product_process_id_lookup", "answer": f"{product_hint} 관련 FAB/ML_TABLE parquet을 찾지 못했습니다."}
    lf = _scan_parquet(files)
    cols = _schema_names(lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    process_col = _ci_col(cols, "process_id", "PROCESS_ID")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    time_col = _ci_col(cols, "tkout_time", "TKOUT_TIME", "time", "TIME", "timestamp", "TIMESTAMP", "updated_at", "UPDATED_AT")
    if not process_col:
        return {
            "handled": True,
            "intent": "product_process_id_lookup",
            "answer": f"{source} 데이터에서 process_id 컬럼을 찾지 못했습니다.",
            "table": {"kind": "process_id_lookup", "title": "process_id lookup", "placement": "below", "columns": _table_columns(["message", "columns"]), "rows": [{"message": "process_id column not found", "columns": ", ".join(cols[:50])}], "total": 1},
        }
    aliases = _product_aliases(product_hint)
    if aliases and product_col:
        lf = lf.filter(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    exprs = [pl.col(process_col).cast(_STR, strict=False).alias("process_id")]
    exprs.append(pl.col(product_col).cast(_STR, strict=False).alias("product") if product_col else pl.lit(product_hint).alias("product"))
    exprs.append(pl.col(root_col).cast(_STR, strict=False).alias("latest_root_lot_id") if root_col else pl.lit("").alias("latest_root_lot_id"))
    exprs.append(pl.col(time_col).cast(_STR, strict=False).alias("latest_time") if time_col else pl.lit("").alias("latest_time"))
    try:
        scoped = lf.select(exprs).drop_nulls(subset=["process_id"])
        if time_col:
            scoped = scoped.sort("latest_time", descending=True)
        df = (
            scoped.group_by(["product", "process_id"])
            .agg([
                pl.len().alias("row_count"),
                pl.col("latest_time").first().alias("latest_time"),
                pl.col("latest_root_lot_id").first().alias("latest_root_lot_id"),
            ])
            .sort(["latest_time", "row_count"], descending=[True, True])
            .limit(max(1, min(50, max_rows * 4)))
            .collect()
        )
    except Exception as e:
        return {"handled": True, "intent": "product_process_id_lookup", "answer": f"process_id 조회 실패: {e}"}
    rows = df.to_dicts()
    top = rows[0] if rows else {}
    answer = f"{product_hint}의 최신 {source} 기준 process_id는 {top.get('process_id') or '-'} 입니다." if rows else f"{product_hint}에서 process_id row를 찾지 못했습니다."
    for row in rows:
        row["source"] = source
    cols_out = ["product", "process_id", "row_count", "latest_time", "latest_root_lot_id", "source"]
    return {
        "handled": True,
        "intent": "product_process_id_lookup",
        "action": "query_product_process_id",
        "answer": answer,
        "table": {"kind": "process_id_lookup", "title": "Product process_id", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in rows], "total": len(rows)},
    }


def _load_flowi_meetings() -> list[dict[str, Any]]:
    data = load_json(PATHS.data_root / "meetings" / "meetings.json", [])
    return data if isinstance(data, list) else []


def _load_flowi_calendar_events() -> list[dict[str, Any]]:
    data = load_json(PATHS.data_root / "calendar" / "events.json", [])
    return data if isinstance(data, list) else []


def _meeting_visible_to_flowi(meeting: dict[str, Any], me: dict[str, Any]) -> bool:
    username = me.get("username") or "user"
    role = me.get("role") or "user"
    try:
        from routers.meetings import _meeting_visible, _my_meeting_group_ids
        return bool(_meeting_visible(meeting, username, role, _my_meeting_group_ids(username, role)))
    except Exception:
        if role == "admin":
            return True
        gids = meeting.get("group_ids") or []
        return not gids or meeting.get("owner") == username or meeting.get("created_by") == username


def _meeting_search_terms(prompt: str) -> list[str]:
    text = str(prompt or "").strip()
    stop = {
        "회의", "회의록", "결정사항", "결정", "액션", "액션아이템", "날짜별로", "정리",
        "보여줘", "찾아줘", "이전", "지난", "했던", "일", "뭐", "어떤", "에서", "만",
        "날짜", "시간", "일시", "언제", "몇시", "몇", "아젠다", "agenda", "차", "회차",
    }
    text = re.sub(r"\b\d+\s*(?:차|회차|번째)\b", " ", text)
    parts = re.split(r"[\s,./]+", text)
    out: list[str] = []
    for part in parts:
        item = part.strip(" ?!~요은는이가을를과와:")
        if not item or item in stop:
            continue
        item = re.sub(r"(회의에서|회의만|회의|아젠다는|아젠다)$", "", item)
        if item and item not in stop and item not in out:
            out.append(item)
    return out[:8]


def _meeting_session_idx_from_prompt(prompt: str) -> int | None:
    text = str(prompt or "")
    m = re.search(r"(\d{1,3})\s*(?:차|회차|번째)", text)
    if m:
        try:
            return max(1, int(m.group(1)))
        except Exception:
            return None
    aliases = {
        "첫": 1, "첫번": 1, "첫번째": 1,
        "두": 2, "둘": 2, "두번": 2, "두번째": 2,
        "세": 3, "셋": 3, "세번": 3, "세번째": 3,
        "네": 4, "넷": 4, "네번째": 4,
        "다섯": 5, "다섯번째": 5,
    }
    for key, value in aliases.items():
        if key in text and ("차" in text or "번째" in text or "회의" in text):
            return value
    return None


def _meeting_context_from_agent(agent_context: dict[str, Any] | None) -> dict[str, Any]:
    for msg in reversed(_flowi_context_messages(agent_context)):
        intent = str(msg.get("intent") or "")
        feature = str(msg.get("feature") or "")
        slots = msg.get("slots") if isinstance(msg.get("slots"), dict) else {}
        workflow = msg.get("workflow_state") if isinstance(msg.get("workflow_state"), dict) else {}
        workflow_slots = workflow.get("slots") if isinstance(workflow.get("slots"), dict) else {}
        merged_slots = {**workflow_slots, **slots}
        if intent != "meeting_recall_summary" and feature != "meeting" and not merged_slots.get("meeting_id"):
            continue
        out = {
            "meeting_id": str(merged_slots.get("meeting_id") or ""),
            "meeting_title": str(merged_slots.get("meeting_title") or ""),
            "session_idx": merged_slots.get("session_idx"),
        }
        try:
            if out["session_idx"] not in (None, ""):
                out["session_idx"] = int(out["session_idx"])
        except Exception:
            out["session_idx"] = None
        if out.get("meeting_id") or out.get("meeting_title") or out.get("session_idx"):
            return out
    return {}


def _is_meeting_recall_prompt(prompt: str, agent_context: dict[str, Any] | None = None) -> bool:
    text = str(prompt or "")
    meeting_terms = ("결정", "회의록", "아젠다", "했던 일", "정리", "액션", "지난", "날짜", "시간", "일시", "언제", "몇시")
    if ("회의" in text or "회의록" in text) and any(term in text for term in meeting_terms):
        return True
    if _meeting_context_from_agent(agent_context) and any(term in text for term in meeting_terms + ("차", "번째")):
        return True
    return False


def _handle_meeting_recall(
    prompt: str,
    max_rows: int,
    me: dict[str, Any],
    agent_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not _is_meeting_recall_prompt(prompt, agent_context):
        return {"handled": False}
    meetings = [m for m in _load_flowi_meetings() if isinstance(m, dict) and _meeting_visible_to_flowi(m, me)]
    context = _meeting_context_from_agent(agent_context)
    requested_idx = _meeting_session_idx_from_prompt(prompt)
    context_idx = context.get("session_idx") if isinstance(context.get("session_idx"), int) else None
    session_idx = requested_idx or context_idx
    terms = _meeting_search_terms(prompt)
    if not terms and context.get("meeting_title"):
        terms = [str(context.get("meeting_title"))]
    if context.get("meeting_id"):
        meetings = [m for m in meetings if str(m.get("id") or "") == str(context.get("meeting_id"))] or meetings
    if terms:
        def score(meeting: dict[str, Any]) -> int:
            hay = _upper(" ".join([
                meeting.get("title") or "",
                meeting.get("category") or "",
                meeting.get("owner") or "",
            ]))
            return sum(1 for term in terms if _upper(term) in hay)
        scored = [(score(m), m) for m in meetings]
        meetings = [m for s, m in scored if s > 0] or meetings
    want_actions = "액션" in prompt or "했던 일" in prompt or "할 일" in prompt
    want_agenda = "아젠다" in prompt
    want_schedule = any(term in prompt for term in ("시간", "일시", "언제", "몇시")) or ("날짜" in prompt and "날짜별" not in prompt)
    want_minutes = "회의록" in prompt or "정리" in prompt
    rows: list[dict[str, Any]] = []
    matched_meetings: list[dict[str, Any]] = []
    matched_sessions: list[dict[str, Any]] = []
    for meeting in meetings:
        title = meeting.get("title") or ""
        for session in meeting.get("sessions") or []:
            try:
                idx_int = int(session.get("idx") or 0)
            except Exception:
                idx_int = 0
            if session_idx and idx_int != session_idx:
                continue
            if not any(m.get("id") == meeting.get("id") for m in matched_meetings):
                matched_meetings.append(meeting)
            matched_sessions.append(session)
            session_date = str(session.get("scheduled_at") or "")[:10]
            session_time = str(session.get("scheduled_at") or "")[11:16]
            scheduled_at = str(session.get("scheduled_at") or "")
            idx = session.get("idx") or ""
            minutes = session.get("minutes") or {}
            if want_schedule:
                agenda_count = len(session.get("agendas") or [])
                decision_count = len(minutes.get("decisions") or [])
                action_count = len(minutes.get("action_items") or [])
                rows.append({
                    "date": session_date,
                    "time": session_time,
                    "meeting_title": title,
                    "session_idx": idx,
                    "type": "session",
                    "text": f"{idx}차 회의 일시: {scheduled_at or '(미정)'} · agenda {agenda_count} · decision {decision_count} · action {action_count}",
                    "owner": meeting.get("owner") or "",
                    "status": session.get("status") or "",
                })
            if want_agenda:
                for ag in session.get("agendas") or []:
                    rows.append({
                        "date": session_date,
                        "time": session_time,
                        "meeting_title": title,
                        "session_idx": idx,
                        "type": "agenda",
                        "text": " - ".join(x for x in [ag.get("title") or "", ag.get("description") or ""] if x),
                        "owner": ag.get("owner") or "",
                        "status": session.get("status") or "",
                    })
            if want_minutes and _text(minutes.get("body")):
                rows.append({
                    "date": session_date,
                    "time": session_time,
                    "meeting_title": title,
                    "session_idx": idx,
                    "type": "minutes",
                    "text": minutes.get("body") or "",
                    "owner": minutes.get("author") or "",
                    "status": session.get("status") or "",
                })
            if not want_actions:
                for dec in minutes.get("decisions") or []:
                    obj = {"text": dec} if isinstance(dec, str) else (dec if isinstance(dec, dict) else {})
                    if not _text(obj.get("text")):
                        continue
                    rows.append({
                        "date": str(obj.get("due") or session_date)[:10],
                        "time": session_time,
                        "meeting_title": title,
                        "session_idx": idx,
                        "type": "decision",
                        "text": obj.get("text") or "",
                        "owner": "",
                        "status": "calendar_pushed" if obj.get("calendar_pushed") else "",
                    })
            if want_actions or "전체" in prompt or "정리" in prompt:
                for ai in minutes.get("action_items") or []:
                    if not isinstance(ai, dict) or not _text(ai.get("text")):
                        continue
                    rows.append({
                        "date": str(ai.get("due") or session_date)[:10],
                        "time": session_time,
                        "meeting_title": title,
                        "session_idx": idx,
                        "type": "action",
                        "text": ai.get("text") or "",
                        "owner": ai.get("owner") or "",
                        "status": ai.get("status") or "",
                    })
    if "변경점" in prompt or "캘린더" in prompt:
        meeting_ids = {m.get("id") for m in meetings}
        for ev in _load_flowi_calendar_events():
            if not isinstance(ev, dict):
                continue
            ref = ev.get("meeting_ref") or {}
            if ref.get("meeting_id") not in meeting_ids:
                continue
            rows.append({
                "date": ev.get("date") or "",
                "time": "",
                "meeting_title": ref.get("meeting_title") or "",
                "session_idx": "",
                "type": ev.get("source_type") or "calendar",
                "text": ev.get("title") or "",
                "owner": ev.get("author") or "",
                "status": ev.get("status") or "",
            })
    rows = [r for r in rows if r.get("text")]
    rows.sort(key=lambda r: (str(r.get("date") or ""), str(r.get("time") or ""), str(r.get("meeting_title") or ""), str(r.get("session_idx") or "")), reverse=True)
    cols = ["date", "time", "meeting_title", "session_idx", "type", "text", "owner", "status"]
    if not rows:
        return {
            "handled": True,
            "intent": "meeting_recall_summary",
            "answer": "조건에 맞는 회의 기록을 찾지 못했습니다. 회의명이나 기간을 조금 더 구체적으로 알려주세요.",
            "table": {"kind": "meeting_recall", "title": "Meeting recall", "placement": "below", "columns": _table_columns(["message"]), "rows": [{"message": "no meeting records"}], "total": 0},
        }
    scope = " / ".join(terms) if terms else "전체 회의"
    if session_idx:
        scope += f" · {session_idx}차"
    title = "Meeting minutes" if want_minutes else ("Meeting session details" if (want_schedule or want_agenda) else "Meeting decisions/actions by date")
    answer = f"{scope} 기준 회의 기록 {len(rows)}건을 정리했습니다. 회의관리/변경점 관리의 저장된 기록만 사용했습니다."
    primary_meeting = matched_meetings[0] if matched_meetings else {}
    primary_session = matched_sessions[0] if matched_sessions else {}
    return {
        "handled": True,
        "intent": "meeting_recall_summary",
        "action": "query_meeting_calendar_records",
        "answer": answer,
        "feature_entrypoints": [item for item in FLOWI_FEATURE_ENTRYPOINTS if item["key"] in {"meeting", "calendar"}],
        "table": {
            "kind": "meeting_recall",
            "title": title,
            "placement": "below",
            "columns": _table_columns(cols),
            "rows": [{k: row.get(k, "") for k in cols} for row in rows[:max(1, min(120, max_rows * 8))]],
            "total": len(rows),
        },
        "filters": {"terms": terms, "session_idx": session_idx or ""},
        "slots": {
            "meeting_id": primary_meeting.get("id") or context.get("meeting_id") or "",
            "meeting_title": primary_meeting.get("title") or context.get("meeting_title") or "",
            "session_id": primary_session.get("id") or "",
            "session_idx": primary_session.get("idx") or session_idx or "",
        },
    }


def _detect_app_write_feature(prompt: str) -> str:
    text = str(prompt or "")
    low = text.lower()
    has_write_intent = (
        any(term in low or term in text for term in _FLOWI_APP_WRITE_TERMS)
        or any(term in low or term in text for term in _FLOWI_APP_CREATE_TERMS)
        or any(term in low or term in text for term in _FLOWI_APP_MODIFY_TERMS)
        or ("변경" in text.replace("변경점", ""))
    )
    if not has_write_intent:
        return ""
    # "안올라왔는데" 같은 freshness 질문은 write가 아니다.
    if any(term in text for term in ("안올라", "안 올라", "최근업데이트", "업데이트 되었")):
        return ""
    for feature, hints in _FLOWI_APP_WRITE_HINTS.items():
        if any(h in low or h in text for h in hints):
            return feature
    return ""


def _flowi_app_write_mode(prompt: str) -> str:
    text = str(prompt or "")
    low = text.lower()
    create = any(term in low or term in text for term in _FLOWI_APP_CREATE_TERMS)
    modify = any(term in low or term in text for term in _FLOWI_APP_MODIFY_TERMS)
    # "변경점 등록"은 calendar create 의미라서 수정 요청으로 보지 않는다.
    change_text = text.replace("변경점", "")
    if "변경" in change_text and not create:
        modify = True
    if modify:
        return "modify"
    if create:
        return "create"
    return ""


def _flowi_prompt_title(prompt: str, feature: str) -> str:
    text = str(prompt or "").strip()
    quoted = re.findall(r"[\"'“”‘’「」『』](.+?)[\"'“”‘’「」『』]", text)
    if quoted:
        text = max(quoted, key=len)
    for pat in (
        r"(?:이름|제목|title)\s*[:=]\s*([^\n,;/]+)",
        r"(?:이름|제목|title)\s*(?:은|는)\s*([^\n,;/]+)",
        r"([A-Za-z0-9_.-]{1,80})\s*(?:이름|제목|title)\s*으로",
    ):
        m = re.search(pat, text, flags=re.I)
        if m:
            title = re.sub(r"\s+", " ", m.group(1)).strip(" \t\r\n-_:,.;")
            if title:
                return title[:120]
    feature_words = {
        "tracker": r"(?:이슈추적|이슈\s*추적|이슈|tracker|issue|트래커)",
        "meeting": r"(?:회의|미팅|meeting)",
        "inform": r"(?:인폼|inform)",
        "calendar": r"(?:일정|캘린더|calendar|변경점)",
    }.get(feature, "")
    if feature_words:
        m = re.search(rf"{feature_words}\s+(.{{1,80}}?)(?:이라고|라고|이라는|라는)", text, flags=re.I)
        if m:
            title = re.sub(r"\s+", " ", m.group(1)).strip(" \t\r\n-_:,.;")
            if title:
                return title[:120]
    if feature == "meeting":
        for pat in (
            r"^\s*(.{1,80}?)(?:이라고|라고|이라는|라는)\s*",
            r"^\s*(.{1,80}?)(?:\s+)?(?:회의|미팅)\s*(?:하나|한\s*개|1개)?\s*(?:등록|만들|생성|추가)",
        ):
            m = re.search(pat, text, flags=re.I)
            if m:
                title = re.sub(r"\s+", " ", m.group(1)).strip(" \t\r\n-_:,.;")
                if title:
                    return title[:120]
    remove_terms = [
        "등록해줘", "등록해주세요", "만들어줘", "만들어주세요", "생성해줘", "생성해주세요",
        "추가해줘", "추가해주세요", "넣어줘", "넣어주세요", "남겨줘", "남겨주세요",
        "기록해줘", "기록해주세요", "올려줘", "올려주세요",
        "등록", "만들어", "생성", "추가", "넣어", "남겨", "기록", "올려",
        "인폼", "inform", "이슈", "issue", "tracker", "트래커", "회의", "meeting",
        "일정", "캘린더", "calendar", "변경점", "아젠다", "회의록", "주세요", "해줘",
    ]
    for term in remove_terms:
        text = re.sub(re.escape(term), " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" \t\r\n-_:,.;")
    if not text:
        text = f"{_feature_title(feature)} 자동 등록"
    return text[:120]


_FLOWI_WEEKDAY_WORDS = (
    ("월요일", 0), ("화요일", 1), ("수요일", 2), ("목요일", 3),
    ("금요일", 4), ("토요일", 5), ("일요일", 6),
)


def _flowi_prompt_weekdays(prompt: str) -> list[int]:
    text = str(prompt or "")
    days = []
    for word, idx in _FLOWI_WEEKDAY_WORDS:
        if word in text and idx not in days:
            days.append(idx)
    return days


def _flowi_prompt_time(prompt: str) -> tuple[int, int] | None:
    text = str(prompt or "")
    m = re.search(r"(오전|오후|am|pm)?\s*(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분?)?", text, flags=re.I)
    if not m:
        m = re.search(r"(오전|오후|am|pm)?\s*(\d{1,2})\s*:\s*(\d{2})", text, flags=re.I)
    if not m:
        return None
    meridiem = (m.group(1) or "").lower()
    hour = int(m.group(2))
    minute = int(m.group(3) or 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    if meridiem in {"오후", "pm"} and hour < 12:
        hour += 12
    if meridiem in {"오전", "am"} and hour == 12:
        hour = 0
    return hour, minute


def _flowi_prompt_meeting_schedule(prompt: str) -> tuple[str, dict[str, Any]]:
    text = str(prompt or "")
    weekdays = _flowi_prompt_weekdays(text)
    time_pair = _flowi_prompt_time(text)
    recurrence = {"type": "none", "count_per_week": 0, "weekday": [], "note": ""}
    if any(term in text.lower() or term in text for term in ("매주", "매 주", "주마다", "weekly")):
        recurrence = {
            "type": "weekly",
            "count_per_week": len(weekdays) or 1,
            "weekday": weekdays,
            "note": text[:200],
        }
    date_s = _flowi_prompt_date(text)
    if not date_s and weekdays:
        today = datetime.now().date()
        target_wd = weekdays[0]
        days_ahead = (target_wd - today.weekday()) % 7
        candidate = today + timedelta(days=days_ahead)
        if time_pair:
            now = datetime.now()
            cand_dt = datetime(candidate.year, candidate.month, candidate.day, time_pair[0], time_pair[1])
            if cand_dt <= now:
                candidate = candidate + timedelta(days=7)
        date_s = candidate.isoformat()
    if date_s and time_pair:
        return f"{date_s}T{time_pair[0]:02d}:{time_pair[1]:02d}:00", recurrence
    if date_s:
        return f"{date_s}T00:00:00", recurrence
    return "", recurrence


def _flowi_prompt_field(prompt: str, names: tuple[str, ...], limit: int = 80) -> str:
    for name in names:
        m = re.search(rf"(?:{re.escape(name)})\s*[:=]\s*([^\n,;/]+)", str(prompt or ""), flags=re.I)
        if m:
            return m.group(1).strip()[:limit]
    return ""


def _flowi_prompt_content(prompt: str, limit: int = 4000) -> str:
    text = str(prompt or "")
    m = re.search(r"(?:내용|본문|description|desc)\s*(?:은|는|:|=)?\s*(.+?)(?:\s*(?:적어줘|작성해줘|등록해줘|넣어줘|남겨줘)\s*)?$", text, flags=re.I | re.S)
    if not m:
        return ""
    content = re.sub(r"\s+", " ", m.group(1)).strip(" \t\r\n-_:,.;")
    return content[:limit]


def _flowi_prompt_inform_text(prompt: str, limit: int = 4000) -> str:
    explicit = _flowi_prompt_content(prompt, limit=limit)
    if explicit:
        return explicit
    text = str(prompt or "")
    m = re.search(r"(?:인폼\s*로그|인폼로그|인폼|inform)\s+(.+?)(?:\s*으로)?\s*(?:등록|생성|추가|남겨|기록|올려)", text, flags=re.I | re.S)
    if not m:
        return ""
    body = re.sub(r"\s+", " ", m.group(1)).strip(" \t\r\n-_:,.;")
    return body[:limit]


def _extract_flowi_splittable_note_payload(prompt: str) -> dict[str, Any] | None:
    text = str(prompt or "")
    idx = text.upper().find(_FLOWI_SPLITTABLE_NOTE_MARKER)
    if idx < 0:
        return None
    tail = text[idx + len(_FLOWI_SPLITTABLE_NOTE_MARKER):].strip()
    if tail.startswith(":"):
        tail = tail[1:].strip()
    if not tail:
        return {}
    try:
        obj, _end = json.JSONDecoder().raw_decode(tail)
    except Exception as e:
        return {"_parse_error": str(e)}
    return obj if isinstance(obj, dict) else {"_parse_error": "JSON object가 필요합니다."}


def _flowi_splittable_note_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    if _FLOWI_SPLITTABLE_NOTE_MARKER in text.upper():
        return True
    has_split = any(t in low or t in text for t in ("split table", "splittable", "스플릿", "스플릿테이블", "스플릿 테이블"))
    has_note = any(t in low or t in text for t in ("꼬리표", "태그", "tag", "메모", "memo", "코멘트", "comment"))
    has_write = any(t in low or t in text for t in _FLOWI_APP_WRITE_TERMS + _FLOWI_APP_CREATE_TERMS)
    return bool(has_split and has_note and has_write)


def _clean_flowi_splittable_note_text(candidate: str, prompt: str) -> str:
    text = re.sub(r"\s+", " ", str(candidate or "")).strip(" \t\r\n-_:,.;'\"“”‘’")
    text = re.sub(r"(?:이라고|라고|이라는|라는)\s*$", "", text).strip(" \t\r\n-_:,.;'\"“”‘’")
    for term in (
        "스플릿 테이블", "스플릿테이블", "split table", "splittable",
        "꼬리표", "태그", "tag", "메모", "memo", "코멘트", "comment",
        "달아줘", "달아주세요", "붙여줘", "붙여주세요", "등록해줘", "등록해주세요",
        "추가해줘", "추가해주세요", "남겨줘", "남겨주세요", "기록해줘", "기록해주세요",
    ):
        text = re.sub(re.escape(term), " ", text, flags=re.I)
    for lot in _lot_tokens(prompt):
        text = re.sub(rf"\b{re.escape(lot)}\b\s*(?:에|에는|으로|로|를|을|은|는)?", " ", text, flags=re.I)
    product = _product_hint(prompt)
    if product:
        text = re.sub(rf"\b{re.escape(product)}\b\s*(?:에|에는|으로|로|를|을|은|는)?", " ", text, flags=re.I)
    text = re.sub(r"^(?:에|에는|으로|로|를|을|은|는)\s+", "", text)
    text = re.sub(r"\s+", " ", text).strip(" \t\r\n-_:,.;'\"“”‘’")
    return text[:2000]


def _flowi_prompt_splittable_note_text(prompt: str) -> str:
    text = str(prompt or "")
    marker = re.search(r"(꼬리표|태그|tag|메모|memo|코멘트|comment)", text, flags=re.I)
    if marker:
        after = text[marker.end():]
        after = re.sub(r"^\s*(?:로|를|을|은|는|:|=|-)?\s*", "", after)
        m = re.search(
            r"(.+?)(?:이라고|라고|이라는|라는)?\s*(?:달아|붙여|등록|추가|남겨|기록|저장|add|save|create)",
            after,
            flags=re.I | re.S,
        )
        body = m.group(1) if m else after
        cleaned = _clean_flowi_splittable_note_text(body, prompt)
        if cleaned:
            return cleaned
    for pat in (
        r"(.{1,200}?)(?:이라는|이라고|라는|라고)\s*(?:꼬리표|태그|tag|메모|memo|코멘트|comment)?\s*(?:달아|붙여|등록|추가|남겨|기록|저장)",
        r"(.{1,200}?)\s*(?:꼬리표|태그|tag|메모|memo|코멘트|comment)(?:를|을)?\s*(?:달아|붙여|등록|추가|남겨|기록|저장)",
    ):
        m = re.search(pat, text, flags=re.I | re.S)
        if m:
            cleaned = _clean_flowi_splittable_note_text(m.group(1), prompt)
            if cleaned:
                return cleaned
    return ""


def _flowi_splittable_product_id(product: str) -> str:
    raw = _upper(product)
    if not raw:
        return ""
    if raw.startswith("ML_TABLE_"):
        raw = raw[len("ML_TABLE_"):]
    if raw in {"PRODUCT_A", "PRODUCT_A0", "PRODUCT_A1", "PRODA0", "PRODA1"}:
        raw = "PRODA"
    elif raw == "PRODUCT_B":
        raw = "PRODB"
    if not raw:
        return ""
    return f"ML_TABLE_{raw}"


def _flowi_splittable_note_confirm_text(product: str, root_lot_id: str, text: str) -> str:
    basis = re.sub(r"\s+", " ", str(text or "")).strip()[:80]
    return f"SPLITTABLE_NOTE_CONFIRM::{product}::{root_lot_id}::{basis}"


def _flowi_splittable_note_table(rows: list[dict[str, Any]], title: str = "SplitTable lot note") -> dict[str, Any]:
    return {
        "kind": "splittable_lot_note",
        "title": title,
        "placement": "below",
        "columns": _table_columns(["field", "value"]),
        "rows": rows,
        "total": len(rows),
    }


def _flowi_splittable_note_product_choices(prompt: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    choices: list[dict[str, Any]] = []
    for row in candidates:
        product = _flowi_splittable_product_id(row.get("product") or "")
        if not product or product in seen:
            continue
        seen.add(product)
        choices.append({
            "id": f"product_{len(choices) + 1}",
            "label": str(len(choices) + 1),
            "title": product,
            "recommended": len(choices) == 0,
            "description": f"{row.get('sources') or 'data'} 기준 후보",
            "prompt": f"{product} {prompt.strip()}",
        })
    return choices[:4]


def _flowi_splittable_note_payload(prompt: str, me: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    classified = _classified_lot_tokens(prompt)
    root_lots = classified.get("root_lot_ids") or []
    fab_lots = classified.get("fab_lot_ids") or []
    root_lot_id = root_lots[0] if root_lots else ((fab_lots[0][:5] if fab_lots else "") or (_lot_tokens(prompt)[0] if _lot_tokens(prompt) else ""))
    note_text = _flowi_prompt_splittable_note_text(prompt)
    product = _flowi_splittable_product_id(_product_hint(prompt))
    missing: list[str] = []
    if not root_lot_id:
        missing.append("root_lot_id")
    if not note_text:
        missing.append("꼬리표 내용")
    if not product and root_lot_id:
        candidates = _resolve_products_for_lots([root_lot_id], kinds=("ML_TABLE", "FAB"), limit=8)
        choices = _flowi_splittable_note_product_choices(prompt, candidates)
        if len(choices) == 1:
            product = choices[0]["title"]
        elif len(choices) > 1:
            return None, {
                "handled": True,
                "intent": "splittable_lot_note_needs_product",
                "action": "clarify_product",
                "answer": "같은 lot 후보가 여러 product에서 발견됐습니다. 스플릿 테이블 꼬리표를 등록할 product를 선택해주세요.",
                "feature": "splittable",
                "missing": ["product"],
                "pending_prompt": prompt,
                "clarification": {"question": "어느 스플릿 테이블 product에 꼬리표를 등록할까요?", "choices": choices},
                "table": _flowi_splittable_note_table([
                    {"field": "status", "value": "needs_product"},
                    {"field": "root_lot_id", "value": root_lot_id},
                    {"field": "note", "value": note_text},
                ], title="SplitTable note needs product"),
            }
    if not product:
        missing.append("product")
    if missing:
        return None, _flowi_app_write_missing("splittable", missing, prompt, product, [root_lot_id] if root_lot_id else [], [])
    return {
        "scope": "lot",
        "product": product,
        "root_lot_id": root_lot_id,
        "text": note_text,
        "username": me.get("username") or "user",
    }, None


def _save_flowi_splittable_lot_note(payload: dict[str, Any]) -> dict[str, Any]:
    from routers import splittable as splittable_router
    product = _flowi_splittable_product_id(payload.get("product") or "")
    root_lot_id = _upper(payload.get("root_lot_id") or "")
    text = str(payload.get("text") or "").strip()
    username = _safe_username(payload.get("username") or "user")
    if not product:
        raise ValueError("product가 필요합니다.")
    if not root_lot_id:
        raise ValueError("root_lot_id가 필요합니다.")
    if not text:
        raise ValueError("꼬리표 내용이 비어 있습니다.")
    if len(text) > 2000:
        raise ValueError("꼬리표 내용은 2000자 이하로 입력해주세요.")
    entry = {
        "id": splittable_router._new_note_id(),
        "scope": "lot",
        "key": splittable_router._notes_key_lot(product, root_lot_id),
        "text": text,
        "username": username,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    entries = splittable_router._load_notes()
    entries.append(entry)
    splittable_router._save_notes(entries)
    return entry


def _extract_flowi_splittable_plan_payload(prompt: str) -> dict[str, Any] | None:
    text = str(prompt or "")
    idx = text.upper().find(_FLOWI_SPLITTABLE_PLAN_MARKER)
    if idx < 0:
        return None
    tail = text[idx + len(_FLOWI_SPLITTABLE_PLAN_MARKER):].strip()
    if tail.startswith(":"):
        tail = tail[1:].strip()
    if not tail:
        return {}
    try:
        obj, _end = json.JSONDecoder().raw_decode(tail)
    except Exception as e:
        return {"_parse_error": str(e)}
    return obj if isinstance(obj, dict) else {"_parse_error": "JSON object가 필요합니다."}


def _flowi_splittable_plan_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    if _FLOWI_SPLITTABLE_PLAN_MARKER in text.upper():
        return True
    has_plan = any(t in low or t in text for t in ("plan", "플랜", "계획"))
    has_knob_or_split = any(t in low or t in text for t in ("knob", "노브", "스플릿", "splittable", "split table"))
    has_write = any(t in low or t in text for t in ("넣어", "입력", "저장", "등록", "plan해", "plan 해", "set", "save"))
    return bool(has_plan and has_knob_or_split and has_write)


def _flowi_splittable_plan_confirm_text(product: str, root_lot_id: str, knob_col: str, plans: dict[str, Any]) -> str:
    basis = f"{product}|{root_lot_id}|{knob_col}|{len(plans or {})}"
    return "SPLITTABLE_PLAN_CONFIRM::" + basis[:160]


def _flowi_plan_table(rows: list[dict[str, Any]], title: str = "SplitTable plan draft") -> dict[str, Any]:
    return {
        "kind": "splittable_plan",
        "title": title,
        "placement": "below",
        "columns": _table_columns(["field", "value"]),
        "rows": rows,
        "total": len(rows),
    }


def _flowi_plan_value_from_tail(tail: str) -> str:
    text = re.sub(r"\s+", " ", str(tail or "")).strip()
    text = re.sub(r"^(?:은|는|에|으로|로|:|=|-)\s*", "", text)
    m = re.search(r"([A-Za-z0-9_.-]{1,60})", text)
    if not m:
        return ""
    val = m.group(1).strip(" .,:;")
    if _upper(val) in {"PLAN", "SAVE", "SET", "KNOB", "WF", "WAFER"}:
        return ""
    return val[:80]


def _flowi_wafer_ids_from_fragment(fragment: str) -> list[str]:
    text = str(fragment or "")
    out: list[str] = []
    seen: set[str] = set()
    def add(raw: Any) -> None:
        val = _normalize_wafer_id(raw)
        if val and val not in seen:
            seen.add(val)
            out.append(val)
    m = re.search(r"(\d{1,4})\s*(?:~|-|–|—|to)\s*#?\s*(\d{1,4})", text, flags=re.I)
    if m:
        try:
            start, end = int(m.group(1)), int(m.group(2))
        except Exception:
            start, end = 0, -1
        if start > end:
            start, end = end, start
        for n in range(max(1, start), min(FLOWI_MAX_WAFER_ID, end) + 1):
            add(n)
        return out
    for m in re.finditer(r"(?:#|WF|WAFER|W)?\s*0?(\d{1,4})", text, flags=re.I):
        add(m.group(1))
    return out


def _flowi_parse_splittable_plan_assignments(prompt: str) -> tuple[list[dict[str, Any]], list[str]]:
    text = str(prompt or "")
    assignments: list[dict[str, Any]] = []
    invalid_wafers: list[str] = []
    used: set[str] = set()
    range_pat = re.compile(
        r"(?P<wf>#\s*\d{1,4}\s*(?:~|-|–|—|to)\s*#?\s*\d{1,4}|#\s*\d{1,4}|\b(?:WF|WAFER)\s*\d{1,4}\b|웨이퍼\s*\d{1,4})"
        r"(?P<tail>.{0,80}?)(?=,|그리고|나머지|$)",
        flags=re.I | re.S,
    )
    for m in range_pat.finditer(text):
        frag = m.group("wf") or ""
        raw_nums = [int(x) for x in re.findall(r"\d{1,4}", frag)]
        for raw in raw_nums:
            if raw < 1 or raw > FLOWI_MAX_WAFER_ID:
                invalid_wafers.append(str(raw))
        wafers = _flowi_wafer_ids_from_fragment(frag)
        if not wafers:
            continue
        value = _flowi_plan_value_from_tail(m.group("tail") or "")
        if not value:
            continue
        for wf in wafers:
            used.add(wf)
        assignments.append({"wafers": wafers, "value": value, "label": frag.strip()})
    rest_pat = re.compile(r"나머지(?:는|은|에)?(?P<tail>.{0,80}?)(?=,|그리고|$)", flags=re.I | re.S)
    for m in rest_pat.finditer(text):
        value = _flowi_plan_value_from_tail(m.group("tail") or "")
        if not value:
            continue
        rest = [wf for wf in _all_valid_wafer_ids() if wf not in used]
        if rest:
            assignments.append({"wafers": rest, "value": value, "label": "나머지"})
            used.update(rest)
    return assignments, sorted(set(invalid_wafers), key=lambda x: int(x))


def _flowi_splittable_plan_product_choices(prompt: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    choices = []
    seen: set[str] = set()
    for row in candidates:
        product = _flowi_splittable_product_id(row.get("product") or "")
        if not product or product in seen:
            continue
        seen.add(product)
        choices.append({
            "id": f"product_{len(choices) + 1}",
            "label": str(len(choices) + 1),
            "title": product,
            "recommended": len(choices) == 0,
            "description": f"{row.get('sources') or 'ML_TABLE'} 기준 후보",
            "prompt": f"{product} {prompt.strip()}",
        })
    return choices[:3]


def _flowi_build_splittable_plan_payload(prompt: str, me: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    lots = _lot_tokens(prompt)
    classified = _classified_lot_tokens(prompt)
    root_lot_id = (classified.get("root_lot_ids") or lots or [""])[0]
    if "." in root_lot_id:
        root_lot_id = root_lot_id.split(".", 1)[0]
    product = _flowi_splittable_product_id(_product_hint(prompt))
    if not product and root_lot_id:
        candidates = _resolve_products_for_lots([root_lot_id], kinds=("ML_TABLE",), limit=8)
        choices = _flowi_splittable_plan_product_choices(prompt, candidates)
        if len(choices) == 1:
            product = choices[0]["title"]
        elif len(choices) > 1:
            return None, {
                "handled": True,
                "intent": "splittable_plan_needs_product",
                "action": "clarify_product",
                "answer": "같은 lot 후보가 여러 product에서 발견됐습니다. plan을 넣을 SplitTable product를 선택해주세요.",
                "feature": "splittable",
                "missing": ["product"],
                "pending_prompt": prompt,
                "clarification": {"question": "어느 SplitTable product에 plan을 넣을까요?", "choices": choices},
                "table": _flowi_plan_table([
                    {"field": "status", "value": "needs_product"},
                    {"field": "root_lot_id", "value": root_lot_id},
                ]),
            }
    assignments, invalid_wafers = _flowi_parse_splittable_plan_assignments(prompt)
    missing = []
    if not root_lot_id:
        missing.append("root_lot_id")
    if not product:
        missing.append("product")
    if not assignments:
        missing.append("wafer별 plan 값")
    if missing:
        return None, _flowi_app_write_missing("splittable", missing, prompt, product, [root_lot_id] if root_lot_id else [], _wafer_tokens(prompt))
    files = _ml_files(product)
    if not files:
        return None, {
            "handled": True,
            "intent": "splittable_plan_failed",
            "action": "prepare_splittable_plan",
            "blocked": True,
            "answer": f"{product} ML_TABLE parquet을 찾지 못해 plan cell을 만들 수 없습니다.",
            "feature": "splittable",
        }
    try:
        lf = _scan_parquet(files)
        cols = _schema_names(lf)
        product_col = _ci_col(cols, "product", "PRODUCT")
        root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
        lot_col = _ci_col(cols, "lot_id", "LOT_ID")
        knob_cols = [c for c in cols if _upper(c).startswith("KNOB_")]
        if product_col:
            aliases = _product_aliases(product)
            if aliases:
                lf = lf.filter(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
        lot_expr = _or_contains([c for c in (root_col, lot_col) if c], [root_lot_id])
        if lot_expr is not None:
            lf = lf.filter(lot_expr)
        knob_col, knob_candidates = _select_knob_column(lf, knob_cols, prompt, [root_lot_id], [])
    except Exception as e:
        return None, {
            "handled": True,
            "intent": "splittable_plan_failed",
            "action": "prepare_splittable_plan",
            "blocked": True,
            "answer": f"SplitTable plan 준비 중 ML_TABLE 조회에 실패했습니다: {e}",
            "feature": "splittable",
        }
    if not knob_col:
        return None, _flowi_app_write_missing("splittable", ["KNOB 컬럼"], prompt, product, [root_lot_id], _wafer_tokens(prompt))
    plans: dict[str, str] = {}
    summary_parts = []
    for item in assignments:
        value = str(item.get("value") or "").strip()
        wafers = [wf for wf in (item.get("wafers") or []) if _normalize_wafer_id(wf)]
        if not value or not wafers:
            continue
        for wf in wafers:
            plans[f"{root_lot_id}|{wf}|{knob_col}"] = value
        summary_parts.append(f"{item.get('label')}: {value} ({len(wafers)}wf)")
    if not plans:
        return None, _flowi_app_write_missing("splittable", ["유효 wafer_id 1~25 plan"], prompt, product, [root_lot_id], _wafer_tokens(prompt))
    return {
        "product": product,
        "root_lot_id": root_lot_id,
        "knob": knob_col,
        "plans": plans,
        "assignments": assignments,
        "summary": summary_parts,
        "invalid_wafers": invalid_wafers,
        "username": me.get("username") or "user",
        "knob_candidates": knob_candidates[:12],
    }, None


def _save_flowi_splittable_plan(payload: dict[str, Any]) -> dict[str, Any]:
    from routers import splittable as splittable_router
    product = _flowi_splittable_product_id(payload.get("product") or "")
    root_lot_id = _upper(payload.get("root_lot_id") or "")
    plans = payload.get("plans") if isinstance(payload.get("plans"), dict) else {}
    username = _safe_username(payload.get("username") or "user")
    clean_plans = {
        str(k): str(v)
        for k, v in plans.items()
        if str(k or "").startswith(f"{root_lot_id}|") and _normalize_wafer_id(str(k).split("|")[1] if "|" in str(k) else "")
    }
    if not product or not root_lot_id or not clean_plans:
        raise ValueError("product/root_lot_id/plans가 필요합니다.")
    req = splittable_router.PlanReq(product=product, plans=clean_plans, username=username, root_lot_id=root_lot_id)
    result = splittable_router.save_plan(req)
    return result if isinstance(result, dict) else {"ok": True, "saved": len(clean_plans)}


def _handle_splittable_plan_request(prompt: str, me: dict[str, Any], allowed_keys: set[str] | None = None) -> dict[str, Any]:
    payload = _extract_flowi_splittable_plan_payload(prompt)
    if payload is None and not _flowi_splittable_plan_intent(prompt):
        return {"handled": False}
    if allowed_keys is not None and "splittable" not in allowed_keys:
        return _flowi_permission_block("splittable", me)
    if payload is not None:
        if payload.get("_parse_error"):
            raise HTTPException(400, payload.get("_parse_error"))
        product = _flowi_splittable_product_id(payload.get("product") or "")
        root_lot_id = _upper(payload.get("root_lot_id") or "")
        plans = payload.get("plans") if isinstance(payload.get("plans"), dict) else {}
        expected = _flowi_splittable_plan_confirm_text(product, root_lot_id, str(payload.get("knob") or ""), plans)
        if str(payload.get("confirm") or "").strip() != expected:
            payload = {**payload, "product": product, "root_lot_id": root_lot_id, "confirm": expected, "username": me.get("username") or "user"}
            return _flowi_splittable_plan_confirmation(payload, "SplitTable plan 저장 전 확인이 필요합니다.")
        try:
            saved = _save_flowi_splittable_plan({**payload, "product": product, "root_lot_id": root_lot_id, "username": me.get("username") or payload.get("username") or "user"})
        except Exception as e:
            return {
                "handled": True,
                "intent": "splittable_plan_failed",
                "action": "save_splittable_plan",
                "blocked": True,
                "answer": f"SplitTable plan 저장 중 오류가 발생했습니다: {e}",
                "feature": "splittable",
            }
        rows = [
            {"field": "status", "value": "saved"},
            {"field": "product", "value": product},
            {"field": "root_lot_id", "value": root_lot_id},
            {"field": "knob", "value": str(payload.get("knob") or "")},
            {"field": "saved_cells", "value": str(saved.get("saved") or len(plans))},
            {"field": "wafer_policy", "value": f"wafer_id 1~{FLOWI_MAX_WAFER_ID}만 저장"},
        ]
        return {
            "handled": True,
            "intent": "splittable_plan_saved",
            "action": "save_splittable_plan",
            "answer": f"SplitTable plan을 저장했습니다.\n- product: {product}\n- lot: {root_lot_id}\n- KNOB: {payload.get('knob')}\n- 저장 cell: {saved.get('saved') or len(plans)}",
            "feature": "splittable",
            "created_record": {"id": f"{product}:{root_lot_id}:{payload.get('knob')}", "feature": "splittable", "title": "plan saved", "target": root_lot_id},
            "table": _flowi_plan_table(rows, title="SplitTable plan saved"),
        }
    draft, missing_tool = _flowi_build_splittable_plan_payload(prompt, me)
    if missing_tool:
        return missing_tool
    if not draft:
        return {"handled": False}
    expected = _flowi_splittable_plan_confirm_text(draft["product"], draft["root_lot_id"], draft["knob"], draft["plans"])
    return _flowi_splittable_plan_confirmation({**draft, "confirm": expected}, "SplitTable plan 저장 준비가 됐습니다. 확인 선택을 누르면 실제 plan 저장소에 반영합니다.")


def _flowi_splittable_plan_confirmation(payload: dict[str, Any], answer: str) -> dict[str, Any]:
    product = _flowi_splittable_product_id(payload.get("product") or "")
    root_lot_id = _upper(payload.get("root_lot_id") or "")
    plans = payload.get("plans") if isinstance(payload.get("plans"), dict) else {}
    rows = [
        {"field": "status", "value": "confirmation_required"},
        {"field": "product", "value": product},
        {"field": "root_lot_id", "value": root_lot_id},
        {"field": "knob", "value": str(payload.get("knob") or "")},
        {"field": "plan_cells", "value": str(len(plans))},
        {"field": "assignments", "value": "; ".join(payload.get("summary") or [])},
        {"field": "wafer_policy", "value": f"wafer_id 1~{FLOWI_MAX_WAFER_ID}만 반영"},
    ]
    if payload.get("invalid_wafers"):
        rows.append({"field": "ignored_wafers", "value": ", ".join(payload.get("invalid_wafers") or [])})
    return {
        "handled": True,
        "intent": "splittable_plan_confirm",
        "action": "confirm_splittable_plan",
        "requires_confirmation": True,
        "answer": answer,
        "feature": "splittable",
        "slots": {"product": product, "lots": [root_lot_id], "wafers": sorted({str(k).split("|")[1] for k in plans if "|" in str(k)}, key=lambda x: int(x))},
        "clarification": {
            "question": "이 SplitTable plan을 저장할까요?",
            "choices": [{
                "id": "confirm_splittable_plan",
                "label": "1",
                "title": "plan 저장",
                "recommended": True,
                "description": f"{product} / {root_lot_id} / {payload.get('knob')} {len(plans)} cells",
                "prompt": f"{_FLOWI_SPLITTABLE_PLAN_MARKER} {json.dumps(payload, ensure_ascii=False)}",
            }, {
                "id": "open_splittable",
                "label": "2",
                "title": "SplitTable에서 확인",
                "tab": "splittable",
                "description": "화면에서 lot과 KNOB row를 직접 확인합니다.",
                "prompt": "스플릿 테이블 열기",
            }, {
                "id": "cancel_splittable_plan",
                "label": "3",
                "title": "취소",
                "description": "plan을 저장하지 않습니다.",
                "prompt": "SplitTable plan 저장 취소",
            }],
        },
        "table": _flowi_plan_table(rows),
    }


def _handle_splittable_note_request(prompt: str, me: dict[str, Any], allowed_keys: set[str] | None = None) -> dict[str, Any]:
    payload = _extract_flowi_splittable_note_payload(prompt)
    if payload is None and not _flowi_splittable_note_intent(prompt):
        return {"handled": False}
    if allowed_keys is not None and "splittable" not in allowed_keys:
        return _flowi_permission_block("splittable", me)
    if payload is not None:
        if payload.get("_parse_error"):
            raise HTTPException(400, payload.get("_parse_error"))
        product = _flowi_splittable_product_id(payload.get("product") or "")
        root_lot_id = _upper(payload.get("root_lot_id") or "")
        note_text = str(payload.get("text") or "").strip()
        expected = _flowi_splittable_note_confirm_text(product, root_lot_id, note_text)
        if str(payload.get("confirm") or "").strip() != expected:
            return {
                "handled": True,
                "intent": "splittable_lot_note_confirm",
                "action": "confirm_splittable_lot_note",
                "requires_confirmation": True,
                "answer": "스플릿 테이블 꼬리표 등록 전 확인이 필요합니다.",
                "feature": "splittable",
                "clarification": {
                    "question": "이 꼬리표를 스플릿 테이블 lot에 등록할까요?",
                    "choices": [{
                        "id": "confirm_splittable_note",
                        "label": "1",
                        "title": "꼬리표 등록",
                        "recommended": True,
                        "description": f"{product} / {root_lot_id}에 `{note_text[:80]}` 등록",
                        "prompt": f"{_FLOWI_SPLITTABLE_NOTE_MARKER} {json.dumps({**payload, 'product': product, 'root_lot_id': root_lot_id, 'confirm': expected}, ensure_ascii=False)}",
                    }, {
                        "id": "cancel_splittable_note",
                        "label": "2",
                        "title": "취소",
                        "description": "꼬리표를 등록하지 않습니다.",
                        "prompt": "스플릿 테이블 꼬리표 등록 취소",
                    }],
                },
                "table": _flowi_splittable_note_table([
                    {"field": "status", "value": "confirmation_required"},
                    {"field": "product", "value": product},
                    {"field": "root_lot_id", "value": root_lot_id},
                    {"field": "note", "value": note_text},
                ]),
            }
        try:
            entry = _save_flowi_splittable_lot_note({**payload, "product": product, "root_lot_id": root_lot_id})
        except Exception as e:
            return {
                "handled": True,
                "intent": "splittable_lot_note_failed",
                "action": "create_splittable_lot_note",
                "blocked": True,
                "answer": f"스플릿 테이블 꼬리표 등록 중 오류가 발생했습니다: {e}",
                "feature": "splittable",
            }
        return {
            "handled": True,
            "intent": "splittable_lot_note_create",
            "action": "create_splittable_lot_note",
            "answer": f"스플릿 테이블 꼬리표를 등록했습니다.\n- product: {product}\n- lot: {root_lot_id}\n- 내용: {entry.get('text')}",
            "feature": "splittable",
            "created_record": {"id": entry.get("id") or "", "feature": "splittable", "title": entry.get("text") or "", "target": root_lot_id},
            "table": _flowi_splittable_note_table([
                {"field": "status", "value": "created"},
                {"field": "id", "value": entry.get("id") or ""},
                {"field": "product", "value": product},
                {"field": "root_lot_id", "value": root_lot_id},
                {"field": "note", "value": entry.get("text") or ""},
            ]),
        }

    draft, missing_tool = _flowi_splittable_note_payload(prompt, me)
    if missing_tool:
        return missing_tool
    if not draft:
        return {"handled": False}
    expected = _flowi_splittable_note_confirm_text(draft["product"], draft["root_lot_id"], draft["text"])
    confirm_payload = {**draft, "confirm": expected}
    return {
        "handled": True,
        "intent": "splittable_lot_note_create_draft",
        "action": "confirm_splittable_lot_note",
        "requires_confirmation": True,
        "answer": "스플릿 테이블 lot 꼬리표 등록 준비가 됐습니다. 확인 선택을 누르면 실제로 등록합니다.",
        "feature": "splittable",
        "slots": {"product": draft["product"], "lots": [draft["root_lot_id"]], "wafers": []},
        "clarification": {
            "question": "이 꼬리표를 스플릿 테이블 lot에 등록할까요?",
            "choices": [{
                "id": "confirm_splittable_note",
                "label": "1",
                "title": "꼬리표 등록",
                "recommended": True,
                "description": f"{draft['product']} / {draft['root_lot_id']}에 `{draft['text'][:80]}` 등록",
                "prompt": f"{_FLOWI_SPLITTABLE_NOTE_MARKER} {json.dumps(confirm_payload, ensure_ascii=False)}",
            }, {
                "id": "cancel_splittable_note",
                "label": "2",
                "title": "취소",
                "description": "꼬리표를 등록하지 않습니다.",
                "prompt": "스플릿 테이블 꼬리표 등록 취소",
            }],
        },
        "table": _flowi_splittable_note_table([
            {"field": "status", "value": "draft_ready"},
            {"field": "product", "value": draft["product"]},
            {"field": "root_lot_id", "value": draft["root_lot_id"]},
            {"field": "note", "value": draft["text"]},
            {"field": "policy", "value": "스플릿 테이블 권한이 있는 사용자는 lot 꼬리표를 확인 후 등록할 수 있습니다. DB/Files 원본은 수정하지 않습니다."},
        ]),
    }


def _flowi_prompt_tracker_category_match(prompt: str, cat_names: list[str]) -> tuple[str, bool]:
    names = [str(c or "").strip() for c in (cat_names or []) if str(c or "").strip()]
    if not names:
        return "General", False
    text = str(prompt or "")
    low = text.lower()
    for name in names:
        if name.lower() in low:
            return name, True

    def by_role(role: str) -> str:
        for name in names:
            if name.lower() == role:
                return name
        for name in names:
            if role in name.lower():
                return name
        return ""

    if any(term in low or term in text for term in ("analysis", "분석", "해석")):
        matched = by_role("analysis")
        if matched:
            return matched, True
    if any(term in low or term in text for term in ("monitor", "monitoring", "모니터", "모니터링", "감시")):
        matched = by_role("monitor")
        if matched:
            return matched, True
    return names[0], False


def _flowi_prompt_tracker_category(prompt: str, cat_names: list[str]) -> str:
    return _flowi_prompt_tracker_category_match(prompt, cat_names)[0]


def _flowi_prompt_date(prompt: str) -> str:
    text = str(prompt or "")
    today = datetime.now().date()
    m = re.search(r"\b(20\d{2})[-./](\d{1,2})[-./](\d{1,2})\b", text)
    if m:
        y, mo, d = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        try:
            return datetime(y, mo, d).date().isoformat()
        except Exception:
            return ""
    m = re.search(r"(?<!\d)(\d{1,2})[-./](\d{1,2})(?!\d)", text)
    if m:
        try:
            return datetime(today.year, int(m.group(1)), int(m.group(2))).date().isoformat()
        except Exception:
            return ""
    if "모레" in text:
        return (today + timedelta(days=2)).isoformat()
    if "내일" in text:
        return (today + timedelta(days=1)).isoformat()
    if "오늘" in text:
        return today.isoformat()
    return ""


def _flowi_app_write_missing(
    feature: str,
    missing: list[str],
    prompt: str,
    product: str,
    lots: list[str],
    wafers: list[str],
    *,
    choices: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    title = _feature_title(feature)
    choice_rows = choices if choices else [
        {
            "id": "provide_missing",
            "label": "1",
            "title": "필수값 이어서 입력",
            "recommended": True,
            "description": f"부족한 값({', '.join(missing)})을 추가해 같은 등록 요청을 이어갑니다.",
            "prompt": f"{title} 등록 필수값: ",
        }
    ]
    return {
        "handled": True,
        "intent": f"{feature}_create_needs_context",
        "action": "collect_required_fields",
        "answer": f"{title} 등록에 필요한 조건이 부족합니다. 추가로 필요한 값: {', '.join(missing)}",
        "feature": feature,
        "missing": missing,
        "pending_prompt": prompt,
        "slots": {"product": product, "lots": lots, "wafers": wafers},
        "clarification": {
            "question": f"{title} 등록을 계속하려면 {', '.join(missing)} 값을 알려주세요.",
            "choices": choice_rows[:3],
        },
        "table": {
            "kind": "flowi_app_write_missing",
            "title": "Registration needs more context",
            "placement": "below",
            "columns": _table_columns(["field", "value"]),
            "rows": [
                {"field": "requested_feature", "value": feature},
                {"field": "missing", "value": ", ".join(missing)},
                {"field": "prompt", "value": prompt[:500]},
            ],
            "total": 3,
        },
    }


def _flowi_app_create_missing(feature: str, prompt: str, product: str, lots: list[str], wafers: list[str]) -> tuple[list[str], list[dict[str, Any]]]:
    missing: list[str] = []
    choices: list[dict[str, Any]] = []
    if feature == "tracker":
        from routers import tracker as tracker_router
        cat_names = tracker_router._cat_names()
        _, category_explicit = _flowi_prompt_tracker_category_match(prompt, cat_names)
        if not category_explicit:
            missing.append("category")
            for i, name in enumerate(cat_names[:3], start=1):
                choices.append({
                    "id": f"category_{name}",
                    "label": str(i),
                    "title": name,
                    "recommended": i == 1,
                    "description": f"이슈 카테고리를 {name}(으)로 선택하고 등록을 이어갑니다.",
                    "prompt": f"category: {name}",
                })
        if not product:
            missing.append("product")
        if not lots and not wafers:
            missing.append("lot_id 또는 wafer_id")
    elif feature == "inform":
        if not lots and not wafers:
            missing.append("lot_id 또는 wafer_id")
        if not _flowi_prompt_inform_text(prompt) and not _flowi_prompt_content(prompt):
            missing.append("인폼 내용")
    elif feature == "meeting":
        title = _flowi_prompt_title(prompt, feature)
        if not title or title == f"{_feature_title(feature)} 자동 등록":
            missing.append("회의 제목")
        scheduled_at, recurrence = _flowi_prompt_meeting_schedule(prompt)
        if not scheduled_at and (recurrence or {}).get("type") == "none":
            missing.append("회의 일시 또는 반복 조건")
    elif feature == "calendar":
        if not _flowi_prompt_date(prompt):
            missing.append("date")
        title = _flowi_prompt_title(prompt, feature)
        if not title or title == f"{_feature_title(feature)} 자동 등록":
            missing.append("일정 제목")
    return missing, choices


def _flowi_create_app_record(feature: str, prompt: str, me: dict[str, Any], product: str, lots: list[str], wafers: list[str]) -> dict[str, Any]:
    username = me.get("username") or "user"
    title = _flowi_prompt_title(prompt, feature)
    now_s = datetime.now(timezone.utc).isoformat()
    if feature == "inform":
        if not (lots or wafers):
            return _flowi_app_write_missing(feature, ["lot_id 또는 wafer_id"], prompt, product, lots, wafers)
        from routers import informs as informs_router
        lot = lots[0] if lots else ""
        wafer = wafers[0] if wafers else lot
        module = _flowi_prompt_field(prompt, ("module", "모듈")) or ""
        inform_text = _flowi_prompt_inform_text(prompt) or str(prompt or "").strip()
        reason = _flowi_prompt_field(prompt, ("reason", "사유")) or inform_text[:80] or "Flow-i 등록"
        now = informs_router._now()
        root_lot = informs_router._root_lot_from_values(lot)
        fab_snapshot = informs_router._resolve_fab_lot_snapshot(product, lot, wafer)
        entry = {
            "id": informs_router._new_id(),
            "parent_id": None,
            "wafer_id": wafer,
            "lot_id": lot,
            "root_lot_id": root_lot,
            "product": product,
            "module": module,
            "reason": reason,
            "text": inform_text,
            "author": username,
            "created_at": now,
            "checked": False,
            "checked_by": "",
            "checked_at": "",
            "flow_status": "received",
            "status_history": [{"status": "received", "actor": username, "at": now, "note": "created by Flow-i"}],
            "splittable_change": None,
            "images": [],
            "embed_table": None,
            "auto_generated": False,
            "group_ids": [],
            "fab_lot_id_at_save": fab_snapshot,
        }
        items = informs_router._load_upgraded()
        items.append(entry)
        informs_router._save(items)
        record = {"id": entry["id"], "title": reason, "feature": "inform", "target": lot or wafer}
        answer = f"인폼을 바로 등록했습니다.\n- id: {entry['id']}\n- lot/wafer: {lot or '-'} / {wafer or '-'}\n- 내용: {inform_text[:80] or '-'}"
    elif feature == "tracker":
        from routers import tracker as tracker_router
        from core.tracker_schema import normalize_lot_row
        cat_names = tracker_router._cat_names()
        category = _flowi_prompt_tracker_category(prompt, cat_names)
        issue_id = f"ISS-{datetime.now().strftime('%y%m%d')}-{uuid.uuid4().hex[:4].upper()}"
        lot_rows = []
        for lot in lots[:20]:
            root_lot_id = lot if len(lot) == 5 and _is_mixed_alnum_token(lot) else ""
            lot_rows.append(normalize_lot_row({
                "product": product,
                "lot_id": "" if root_lot_id else lot,
                "root_lot_id": root_lot_id,
                "wafer_id": wafers[0] if wafers else "",
                "username": username,
                "added": now_s,
            }))
        result = tracker_router.TRACKER_SERVICE.create_legacy_issue(
            issue_id=issue_id,
            title=title,
            description=_flowi_prompt_content(prompt) or str(prompt or "").strip(),
            username=username,
            status="in_progress",
            priority="normal",
            category=category,
            links=[],
            images=[],
            lots=lot_rows,
            group_ids=[],
        )
        if not result.ok:
            raise RuntimeError(result.error)
        record = {"id": issue_id, "title": title, "feature": "tracker", "target": category}
        answer = f"이슈를 바로 등록했습니다.\n- id: {issue_id}\n- category: {category}\n- title: {title}"
    elif feature == "meeting":
        from routers import meetings as meetings_router
        now = meetings_router._now()
        scheduled_at, recurrence = _flowi_prompt_meeting_schedule(prompt)
        first_session = {
            "id": meetings_router._new_sid(),
            "idx": 1,
            "scheduled_at": scheduled_at,
            "status": "scheduled",
            "agendas": [],
            "minutes": None,
            "created_at": now,
            "updated_at": now,
        }
        items = meetings_router._load()
        used_colors = {m.get("color") for m in items if isinstance(m, dict) and m.get("color")}
        palette = getattr(meetings_router, "MEETING_PALETTE", ["#3b82f6"])
        color = ""
        for i in range(len(palette)):
            cand = palette[(len(items) + i) % len(palette)]
            if cand not in used_colors:
                color = cand
                break
        if not color:
            color = palette[len(items) % len(palette)]
        entry = {
            "id": meetings_router._new_mid(),
            "title": title,
            "owner": username,
            "recurrence": meetings_router._normalize_recurrence(recurrence),
            "status": "active",
            "color": color,
            "sessions": [first_session],
            "created_by": username,
            "created_at": now,
            "updated_at": now,
            "group_ids": [],
        }
        result = meetings_router.MEETING_SERVICE.create_meeting(entry)
        if not result.ok:
            raise RuntimeError(result.error)
        rec_summary = entry["recurrence"].get("type") or "none"
        if entry["recurrence"].get("weekday"):
            rec_summary += f" / weekday={','.join(str(x) for x in entry['recurrence']['weekday'])}"
        record = {
            "id": entry["id"],
            "title": title,
            "feature": "meeting",
            "target": username,
            "scheduled_at": scheduled_at,
            "recurrence": rec_summary,
        }
        answer = f"회의를 바로 등록했습니다.\n- id: {entry['id']}\n- title: {title}"
        if scheduled_at:
            answer += f"\n- 1차 일시: {scheduled_at}"
        if rec_summary != "none":
            answer += f"\n- 반복: {rec_summary}"
    elif feature == "calendar":
        date_s = _flowi_prompt_date(prompt)
        if not date_s:
            return _flowi_app_write_missing(feature, ["date"], prompt, product, lots, wafers)
        from routers import calendar as calendar_router
        now = calendar_router._now_iso()
        entry = {
            "id": calendar_router._new_id(),
            "version": 1,
            "date": date_s,
            "end_date": "",
            "title": title,
            "body": str(prompt or "").strip(),
            "category": "",
            "author": username,
            "source_type": "manual",
            "meeting_ref": None,
            "status": "pending",
            "created_at": now,
            "updated_at": now,
            "history": [],
            "group_ids": [],
        }
        items = calendar_router._load_events()
        items.append(entry)
        calendar_router._save_events(items)
        record = {"id": entry["id"], "title": title, "feature": "calendar", "target": date_s}
        answer = f"일정을 바로 등록했습니다.\n- id: {entry['id']}\n- date: {date_s}\n- title: {title}"
    else:
        return {"handled": False}
    table_rows = [
        {"field": "status", "value": "created"},
        {"field": "feature", "value": feature},
        {"field": "id", "value": record.get("id") or ""},
        {"field": "title", "value": record.get("title") or ""},
        {"field": "target", "value": record.get("target") or ""},
    ]
    for key in ("scheduled_at", "recurrence"):
        if record.get(key):
            table_rows.append({"field": key, "value": record.get(key) or ""})
    return {
        "handled": True,
        "intent": f"{feature}_create",
        "action": "create_app_record",
        "answer": answer,
        "feature": feature,
        "created_record": record,
        "feature_entrypoints": [item for item in FLOWI_FEATURE_ENTRYPOINTS if item["key"] == feature],
        "slots": {"product": product, "lots": lots, "wafers": wafers},
        "table": {
            "kind": "flowi_app_write_created",
            "title": "Created app record",
            "placement": "below",
            "columns": _table_columns(["field", "value"]),
            "rows": table_rows,
            "total": len(table_rows),
        },
    }


def _handle_app_write_draft(prompt: str, me: dict[str, Any], allowed_keys: set[str] | None = None) -> dict[str, Any]:
    feature = _detect_app_write_feature(prompt)
    if not feature:
        return {"handled": False}
    target_feature = "tracker" if feature == "annotation" else feature
    if allowed_keys is not None and target_feature not in allowed_keys and feature != "annotation":
        return _flowi_permission_block(target_feature, me)
    lots = _lot_tokens(prompt)
    wafers = _wafer_tokens(prompt)
    product = _product_hint(prompt)
    mode = _flowi_app_write_mode(prompt)
    if mode == "create" and feature != "annotation" and target_feature in {"inform", "tracker", "meeting", "calendar"}:
        try:
            missing, choices = _flowi_app_create_missing(target_feature, prompt, product, lots, wafers)
            if missing:
                return _flowi_app_write_missing(target_feature, missing, prompt, product, lots, wafers, choices=choices)
            return _flowi_create_app_record(target_feature, prompt, me, product, lots, wafers)
        except Exception as e:
            logger.warning("flowi app create failed: %s", e)
            return {
                "handled": True,
                "intent": f"{target_feature}_create_failed",
                "action": "create_app_record",
                "blocked": True,
                "answer": f"{_feature_title(target_feature)} 등록 중 오류가 발생했습니다. 관련 화면에서 직접 확인해주세요: {e}",
                "feature": target_feature,
                "slots": {"product": product, "lots": lots, "wafers": wafers},
            }
    action_by_feature = {
        "inform": "inform_create_draft",
        "tracker": "tracker_issue_create_draft",
        "meeting": "meeting_write_draft",
        "calendar": "calendar_event_create_draft",
        "splittable": "splittable_plan_update_draft",
        "annotation": "lot_wafer_annotation_draft",
    }
    rows = [
        {"field": "status", "value": "draft_confirmation_required"},
        {"field": "requested_feature", "value": feature},
        {"field": "detected_product", "value": product or ""},
        {"field": "detected_lot", "value": ", ".join(lots)},
        {"field": "detected_wafer", "value": ", ".join(wafers)},
        {"field": "prompt", "value": prompt[:500]},
        {"field": "policy", "value": "신규 등록은 확실하면 바로 실행합니다. 수정/삭제/상태 변경은 권한 확인과 사전 확인 후 실행해야 합니다."},
    ]
    answer = (
        "이 요청은 기존 기록의 수정/변경 또는 권한 확인이 필요한 작업입니다. "
        "변경 전에는 반드시 대상 화면에서 권한과 내용을 확인해야 합니다. "
        "원본 DB/Files는 수정하지 않습니다."
    )
    return {
        "handled": True,
        "intent": action_by_feature.get(feature, "app_write_draft"),
        "action": "draft_confirm_required",
        "requires_confirmation": True,
        "answer": answer,
        "feature": target_feature,
        "slots": {"product": product, "lots": lots, "wafers": wafers},
        "clarification": {
            "question": "이 작업은 실제 저장 전에 전용 초안 화면/확인 명령이 필요합니다.",
            "choices": [
                {
                    "id": "open_feature",
                    "label": "1",
                    "title": f"{_feature_title(target_feature)} 열기",
                    "tab": target_feature,
                    "recommended": True,
                    "description": "관련 화면에서 현재 조건을 확인한 뒤 수동 저장합니다.",
                    "prompt": f"{_feature_title(target_feature)}에서 이 요청을 처리할 화면을 열어줘",
                },
                {
                    "id": "cancel",
                    "label": "2",
                    "title": "취소",
                    "recommended": False,
                    "description": "저장 작업을 진행하지 않습니다.",
                    "prompt": "취소",
                },
            ],
        },
        "table": {
            "kind": "flowi_app_write_draft",
            "title": "Draft-confirm action required",
            "placement": "below",
            "columns": _table_columns(["field", "value"]),
            "rows": rows,
            "total": len(rows),
        },
    }


def _flowi_context_messages(agent_context: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(agent_context, dict):
        return []
    raw = agent_context.get("messages")
    return [m for m in (raw or []) if isinstance(m, dict)] if isinstance(raw, list) else []


def _flowi_pending_create_from_context(agent_context: dict[str, Any] | None) -> dict[str, Any]:
    messages = _flowi_context_messages(agent_context)
    for idx in range(len(messages) - 1, -1, -1):
        msg = messages[idx]
        feature = str(msg.get("feature") or "").strip()
        intent = str(msg.get("intent") or "")
        action = str(msg.get("action") or "")
        missing = msg.get("missing") if isinstance(msg.get("missing"), list) else []
        pending_prompt = str(msg.get("pending_prompt") or "").strip()
        if not feature or (not missing and not intent.endswith("_create_needs_context") and action != "collect_required_fields"):
            continue
        if not pending_prompt:
            for prev in range(idx - 1, -1, -1):
                if str(messages[prev].get("role") or "") == "user":
                    pending_prompt = str(messages[prev].get("prompt") or messages[prev].get("text") or "").strip()
                    break
        if pending_prompt:
            return {
                "feature": "tracker" if feature == "annotation" else feature,
                "pending_prompt": pending_prompt,
                "missing": missing,
            }
    return {}


def _handle_app_write_missing_followup(prompt: str, me: dict[str, Any], agent_context: dict[str, Any] | None, allowed_keys: set[str] | None = None) -> dict[str, Any]:
    if _is_app_write_status_followup(prompt):
        return {"handled": False}
    if _detect_app_write_feature(prompt) and _flowi_app_write_mode(prompt) == "create":
        return {"handled": False}
    pending = _flowi_pending_create_from_context(agent_context)
    feature = str(pending.get("feature") or "").strip()
    base = str(pending.get("pending_prompt") or "").strip()
    if not feature or not base:
        return {"handled": False}
    if allowed_keys is not None and feature not in allowed_keys:
        return _flowi_permission_block(feature, me)
    combined = (base + "\n" + str(prompt or "").strip()).strip()
    product = _product_hint(combined)
    lots = _lot_tokens(combined)
    wafers = _wafer_tokens(combined)
    missing, choices = _flowi_app_create_missing(feature, combined, product, lots, wafers)
    if missing:
        return _flowi_app_write_missing(feature, missing, combined, product, lots, wafers, choices=choices)
    try:
        created = _flowi_create_app_record(feature, combined, me, product, lots, wafers)
        created["intent"] = f"{feature}_create_from_missing_context"
        created["answer"] = "부족한 값을 반영해서 등록했습니다.\n" + str(created.get("answer") or "")
        return created
    except Exception as e:
        logger.warning("flowi app missing followup create failed: %s", e)
        return {
            "handled": True,
            "intent": f"{feature}_create_failed",
            "action": "create_app_record",
            "blocked": True,
            "answer": f"{_feature_title(feature)} 등록 중 오류가 발생했습니다. 관련 화면에서 직접 확인해주세요: {e}",
            "feature": feature,
            "slots": {"product": product, "lots": lots, "wafers": wafers},
        }


def _is_app_write_status_followup(prompt: str) -> bool:
    text = str(prompt or "")
    if not text.strip():
        return False
    status_terms = (
        "등록했", "등록됐", "등록 되었", "등록되어", "생성했", "생성됐", "생성 되었",
        "만들었", "만들어졌", "저장했", "저장됐", "저장 되었", "추가했", "추가됐",
        "되어있", "되어 있", "안되어", "안 되어", "안됐", "안 됐", "됐어", "되었어",
    )
    if not any(term in text for term in status_terms):
        return False
    return bool("?" in text or text.rstrip().endswith(("어", "니", "나", "요")))


def _flowi_feature_from_context(agent_context: dict[str, Any] | None) -> str:
    for msg in reversed(_flowi_context_messages(agent_context)):
        feature = str(msg.get("feature") or "").strip()
        if feature:
            return "tracker" if feature == "annotation" else feature
        intent = str(msg.get("intent") or "")
        action = str(msg.get("action") or "")
        text = " ".join([intent, action, str(msg.get("prompt") or ""), str(msg.get("text") or "")])
        if "tracker" in text or "이슈" in text:
            return "tracker"
        if "meeting" in text or "회의" in text:
            return "meeting"
        if "inform" in text or "인폼" in text:
            return "inform"
        if "calendar" in text or "일정" in text or "변경점" in text:
            return "calendar"
    return ""


def _flowi_last_create_prompt(agent_context: dict[str, Any] | None, feature: str) -> str:
    for msg in reversed(_flowi_context_messages(agent_context)):
        if str(msg.get("role") or "") != "user":
            continue
        text = str(msg.get("prompt") or msg.get("text") or "").strip()
        if not text or _is_app_write_status_followup(text):
            continue
        f = _detect_app_write_feature(text)
        f = "tracker" if f == "annotation" else f
        if f == feature and _flowi_app_write_mode(text) == "create":
            return text
    return ""


def _flowi_created_record_from_context(agent_context: dict[str, Any] | None, feature: str) -> dict[str, Any]:
    for msg in reversed(_flowi_context_messages(agent_context)):
        rec = msg.get("created_record")
        if isinstance(rec, dict):
            rec_feature = str(rec.get("feature") or msg.get("feature") or "").strip()
            if not rec_feature or rec_feature == feature:
                return rec
        text = str(msg.get("text") or "")
        if feature == "tracker":
            m = re.search(r"\bISS-\d{6}-[A-Z0-9]{4}\b", text)
            if m:
                return {"id": m.group(0), "feature": feature}
        if feature == "meeting":
            m = re.search(r"\bmt_\d{6}_[a-f0-9]{6}\b", text, flags=re.I)
            if m:
                return {"id": m.group(0), "feature": feature}
    return {}


def _flowi_find_app_record(feature: str, *, username: str, record_id: str = "", title: str = "", lots: list[str] | None = None) -> dict[str, Any]:
    lots_u = {_upper(x) for x in (lots or []) if str(x or "").strip()}
    title_s = str(title or "").strip()
    rid = str(record_id or "").strip()
    try:
        if feature == "tracker":
            from routers import tracker as tracker_router
            rows = tracker_router._load()
            def score(issue: dict[str, Any]) -> int:
                if rid and issue.get("id") == rid:
                    return 100
                s = 0
                if title_s:
                    if str(issue.get("title") or "").strip() == title_s:
                        s += 20
                    else:
                        return 0
                if username and str(issue.get("username") or issue.get("created_by") or "") == username:
                    s += 4
                issue_lots = set()
                for lot in issue.get("lots") or []:
                    if isinstance(lot, dict):
                        issue_lots.update(_upper(lot.get(k)) for k in ("lot_id", "root_lot_id", "wafer_id") if lot.get(k))
                if lots_u and issue_lots & lots_u:
                    s += 12
                elif lots_u and not title_s:
                    return 0
                return s
            best = max((r for r in rows if isinstance(r, dict)), key=score, default=None)
            if best and score(best) >= (100 if rid else 12):
                return {"id": best.get("id") or "", "title": best.get("title") or "", "feature": feature, "target": best.get("category") or "", "found": True}
        if feature == "meeting":
            from routers import meetings as meetings_router
            rows = meetings_router._load()
            def score(meeting: dict[str, Any]) -> int:
                if rid and meeting.get("id") == rid:
                    return 100
                s = 0
                if title_s and str(meeting.get("title") or "").strip() == title_s:
                    s += 20
                if username and username in {str(meeting.get("owner") or ""), str(meeting.get("created_by") or "")}:
                    s += 4
                return s
            best = max((r for r in rows if isinstance(r, dict)), key=score, default=None)
            if best and score(best) >= (100 if rid else 16):
                scheduled = ""
                sessions = best.get("sessions") or []
                if sessions and isinstance(sessions[0], dict):
                    scheduled = sessions[0].get("scheduled_at") or ""
                return {"id": best.get("id") or "", "title": best.get("title") or "", "feature": feature, "target": best.get("owner") or "", "scheduled_at": scheduled, "found": True}
        if feature == "inform":
            from routers import informs as informs_router
            rows = informs_router._load_upgraded()
            for row in reversed([r for r in rows if isinstance(r, dict)]):
                if rid and row.get("id") != rid:
                    continue
                if lots_u and not ({_upper(row.get("lot_id")), _upper(row.get("wafer_id")), _upper(row.get("root_lot_id"))} & lots_u):
                    continue
                if username and row.get("author") not in {"", username}:
                    continue
                return {"id": row.get("id") or "", "title": title_s or row.get("reason") or "인폼", "feature": feature, "target": row.get("lot_id") or row.get("wafer_id") or "", "found": True}
        if feature == "calendar":
            from routers import calendar as calendar_router
            rows = calendar_router._load_events()
            for row in reversed([r for r in rows if isinstance(r, dict)]):
                if rid and row.get("id") != rid:
                    continue
                if title_s and str(row.get("title") or "").strip() != title_s:
                    continue
                if username and row.get("author") not in {"", username}:
                    continue
                return {"id": row.get("id") or "", "title": row.get("title") or "", "feature": feature, "target": row.get("date") or "", "found": True}
    except Exception as e:
        logger.warning("flowi app record lookup failed: %s", e)
    return {}


def _handle_app_write_status_followup(prompt: str, me: dict[str, Any], agent_context: dict[str, Any] | None, allowed_keys: set[str] | None = None) -> dict[str, Any]:
    if not _is_app_write_status_followup(prompt):
        return {"handled": False}
    feature = _detect_app_write_feature(prompt) or _flowi_feature_from_context(agent_context)
    feature = "tracker" if feature == "annotation" else feature
    if not feature or feature not in {"tracker", "meeting", "inform", "calendar"}:
        return {"handled": False}
    if allowed_keys is not None and feature not in allowed_keys:
        return _flowi_permission_block(feature, me)
    username = me.get("username") or "user"
    rec_ctx = _flowi_created_record_from_context(agent_context, feature)
    prev_prompt = _flowi_last_create_prompt(agent_context, feature)
    basis_prompt = prev_prompt or prompt
    title = _flowi_prompt_title(basis_prompt, feature)
    lots = _lot_tokens(basis_prompt)
    found = _flowi_find_app_record(
        feature,
        username=username,
        record_id=str(rec_ctx.get("id") or ""),
        title=title,
        lots=lots,
    )
    if found:
        answer = f"네, 직전 {_feature_title(feature)} 등록 기록을 확인했습니다.\n- id: {found.get('id') or '-'}\n- title: {found.get('title') or '-'}"
        if found.get("target"):
            answer += f"\n- target: {found.get('target')}"
        if found.get("scheduled_at"):
            answer += f"\n- 1차 일시: {found.get('scheduled_at')}"
        rows = [{"field": k, "value": v} for k, v in found.items() if k not in {"found"} and v]
        return {
            "handled": True,
            "intent": f"{feature}_registration_status",
            "action": "check_app_record",
            "answer": answer,
            "feature": feature,
            "created_record": found,
            "feature_entrypoints": [item for item in FLOWI_FEATURE_ENTRYPOINTS if item["key"] == feature],
            "table": {"kind": "flowi_app_record_status", "title": "Registration status", "placement": "below", "columns": _table_columns(["field", "value"]), "rows": rows, "total": len(rows)},
        }
    if rec_ctx.get("id"):
        rid = str(rec_ctx.get("id") or "")
        return {
            "handled": True,
            "intent": f"{feature}_registration_status_missing",
            "action": "check_app_record",
            "blocked": True,
            "answer": (
                f"직전 응답에는 {_feature_title(feature)} 생성 id `{rid}`가 있었지만, 현재 저장소에서 같은 id를 확인하지 못했습니다. "
                "중복 등록을 피하려고 자동 재등록은 하지 않았습니다. 다시 등록하려면 원래 등록 요청을 그대로 보내주세요."
            ),
            "feature": feature,
            "created_record": rec_ctx,
            "feature_entrypoints": [item for item in FLOWI_FEATURE_ENTRYPOINTS if item["key"] == feature],
        }
    if prev_prompt:
        created = _flowi_create_app_record(feature, prev_prompt, me, _product_hint(prev_prompt), _lot_tokens(prev_prompt), _wafer_tokens(prev_prompt))
        created["intent"] = f"{feature}_registration_followup_create"
        created["answer"] = "직전 등록 요청이 저장 기록으로 확인되지 않아, 같은 요청을 지금 이어서 등록했습니다.\n" + str(created.get("answer") or "")
        return created
    return {
        "handled": True,
        "intent": f"{feature}_registration_status_unknown",
        "action": "check_app_record",
        "blocked": True,
        "answer": f"현재 대화에서 확인할 직전 {_feature_title(feature)} 등록 요청이나 생성 id를 찾지 못했습니다. 제목, lot, 회의명 중 하나를 같이 알려주면 실제 저장 기록을 확인하겠습니다.",
        "feature": feature,
        "feature_entrypoints": [item for item in FLOWI_FEATURE_ENTRYPOINTS if item["key"] == feature],
    }


def _flowi_inform_summary_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    if not any(term in low or term in text for term in ("inform", "인폼", "공지", "공유")):
        return False
    if _detect_app_write_feature(text) and _flowi_app_write_mode(text):
        return False
    return any(term in low or term in text for term in (
        "현황", "상태", "요약", "누락", "미등록", "미완료", "전체", "모듈", "관리",
        "status", "summary", "missing", "module",
    ))


def _handle_flowi_inform_summary(prompt: str, me: dict[str, Any], max_rows: int, allowed_keys: set[str] | None = None) -> dict[str, Any]:
    if not _flowi_inform_summary_intent(prompt):
        return {"handled": False}
    if allowed_keys is not None and "inform" not in allowed_keys:
        return _flowi_permission_block("inform", me)
    lots = _lot_tokens(prompt)
    if not lots:
        return {
            "handled": True,
            "intent": "inform_lot_module_summary_needs_context",
            "action": "collect_required_fields",
            "answer": "Lot별 인폼 모듈 현황을 보려면 lot_id 또는 root_lot_id가 필요합니다.",
            "feature": "inform",
            "missing": ["lot_id 또는 root_lot_id"],
            "clarification": {
                "question": "어떤 Lot의 인폼 현황을 볼까요?",
                "choices": [{
                    "id": "provide_lot",
                    "label": "1",
                    "title": "Lot 입력",
                    "recommended": True,
                    "description": "root_lot_id 또는 fab_lot_id를 이어서 입력합니다.",
                    "prompt": "lot_id: ",
                }],
            },
            "feature_entrypoints": [item for item in FLOWI_FEATURE_ENTRYPOINTS if item["key"] == "inform"],
        }
    from routers import informs as informs_router
    username = me.get("username") or "user"
    role = me.get("role") or "user"
    my_mods = informs_router._effective_modules(username, role)
    query = lots[0]
    root = informs_router._root_lot_from_values(query)
    root_prefix = root if len(root) <= 5 else ""
    items = informs_router._load_upgraded()
    hits = [x for x in items if (
        (x.get("root_lot_id") or informs_router._root_lot_from_values(x.get("lot_id") or "")) == root
        or (root_prefix and (x.get("root_lot_id") or "").startswith(root_prefix))
        or (query and (x.get("lot_id") or "") == query)
        or (query and (x.get("fab_lot_id_at_save") or "") == query)
    )]
    hits = [x for x in hits if informs_router._visible_to(x, username, role, my_mods)]
    hits.sort(key=lambda x: x.get("created_at", ""))
    summary = informs_router._module_progress_summary(hits)
    rows = []
    for row in summary.get("modules") or []:
        rows.append({
            "module": row.get("module") or "",
            "status": row.get("status") or "",
            "count": row.get("count") or 0,
            "mail_count": row.get("mail_count") or 0,
            "last_at": row.get("last_at") or "",
            "completed_at": row.get("completed_at") or "",
        })
    missing = summary.get("missing_modules") or []
    pending = summary.get("pending_modules") or []
    answer = (
        f"{root or query} 인폼 모듈 현황입니다.\n"
        f"- 등록 모듈: {summary.get('active_modules', 0)}/{summary.get('total_modules', 0)}\n"
        f"- 완료 모듈: {summary.get('completed_modules', 0)}\n"
        f"- 미완료 모듈: {len(pending)}\n"
        f"- 미등록 모듈: {len(missing)}"
    )
    if pending:
        answer += "\n- 미완료: " + ", ".join(pending[:8]) + ("..." if len(pending) > 8 else "")
    if missing:
        answer += "\n- 미등록: " + ", ".join(missing[:8]) + ("..." if len(missing) > 8 else "")
    return {
        "handled": True,
        "intent": "inform_lot_module_summary",
        "action": "summarize_inform_modules",
        "answer": answer,
        "feature": "inform",
        "slots": {"lots": [query], "root_lot_id": root, "product": _product_hint(prompt)},
        "summary": summary,
        "feature_entrypoints": [item for item in FLOWI_FEATURE_ENTRYPOINTS if item["key"] == "inform"],
        "table": {
            "kind": "inform_lot_module_summary",
            "title": f"Inform module summary: {root or query}",
            "placement": "below",
            "columns": _table_columns(["module", "status", "count", "mail_count", "last_at", "completed_at"]),
            "rows": rows,
            "total": len(rows),
        },
    }


def _handle_value_table_query(prompt: str, product: str, max_rows: int) -> dict:
    if not _flowi_value_lookup_intent(prompt):
        return {"handled": False}
    lots = _lot_tokens(prompt)
    terms = _query_tokens(prompt)
    # ET/INLINE requests are handled by their dedicated unit functions. This
    # generic table path focuses on ML_TABLE/Base data, matching FileBrowser's
    # read-only preview behavior.
    if ("ET" in _upper(prompt) or "INLINE" in _upper(prompt)) and not ("ML_TABLE" in _upper(prompt) or "KNOB" in _upper(prompt)):
        return {"handled": False}
    files = _ml_files(product)
    if not files:
        return {"handled": False}
    lf = _scan_parquet(files)
    cols = _schema_names(lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    lot_wf_col = _ci_col(cols, "lot_wf", "LOT_WF")
    id_cols = [c for c in (product_col, root_col, lot_col, fab_col, wafer_col, lot_wf_col) if c]
    matched_cols = [c for c in _column_matches(cols, terms, include_knob_when_named=True) if c not in id_cols]
    if not matched_cols and "KNOB" in _upper(prompt):
        matched_cols = [c for c in cols if _upper(c).startswith("KNOB_")][:8]
    if not lots and not matched_cols:
        return {"handled": False}

    aliases = _product_aliases(product)
    filters = []
    if aliases and product_col:
        filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    if lots:
        lot_cols = [c for c in (root_col, lot_col, fab_col, lot_wf_col) if c]
        lot_expr = _or_contains(lot_cols, lots)
        if lot_expr is not None:
            filters.append(lot_expr)
    for expr in filters:
        lf = lf.filter(expr)

    display_cols = list(dict.fromkeys([*id_cols, *matched_cols[:16]]))
    if not display_cols:
        display_cols = cols[: min(12, len(cols))]
    try:
        df = lf.select([pl.col(c).cast(_STR, strict=False).alias(c) for c in display_cols]).limit(max(1, min(120, max_rows * 8))).collect()
    except Exception as e:
        logger.warning("flowi table lookup failed: %s", e)
        return {
            "handled": True,
            "intent": "db_table_lookup",
            "answer": f"DB table 조회에 실패했습니다: {e}",
            "table": {
                "kind": "flowi_db_table",
                "title": "DB table lookup error",
                "placement": "below",
                "columns": _table_columns(["error"]),
                "rows": [{"error": str(e)}],
                "total": 1,
            },
        }
    rows = df.to_dicts()
    if not rows:
        return {
            "handled": True,
            "intent": "db_table_lookup",
            "answer": "실제 ML_TABLE parquet에서 조건에 맞는 row를 찾지 못했습니다. product/lot/컬럼명을 다시 확인해주세요.",
            "table": {
                "kind": "flowi_db_table",
                "title": "ML_TABLE lookup",
                "placement": "below",
                "columns": _table_columns(["message"]),
                "rows": [{"message": "no rows"}],
                "total": 0,
            },
            "filters": {"lot": lots, "product": sorted(aliases), "columns": matched_cols},
        }
    title_bits = []
    if product:
        title_bits.append(_core_product_name(product))
    if lots:
        title_bits.append(",".join(lots))
    if matched_cols:
        title_bits.append(",".join(matched_cols[:4]))
    title = " / ".join(title_bits) or "ML_TABLE"
    answer = (
        "실제 ML_TABLE parquet에서 조건을 적용해 표로 조회했습니다. "
        f"{len(rows)}개 row를 표시합니다."
    )
    if matched_cols:
        answer += f" 조회 컬럼: {', '.join(matched_cols[:8])}."
    return {
        "handled": True,
        "intent": "db_table_lookup",
        "action": "query_filebrowser_table",
        "answer": answer,
        "table": {
            "kind": "flowi_db_table",
            "title": title,
            "placement": "below",
            "columns": _table_columns(display_cols),
            "rows": rows,
            "total": len(rows),
            "source": "ML_TABLE",
        },
        "filters": {"lot": lots, "product": sorted(aliases), "columns": matched_cols},
    }


def _fastest_knob_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    has_knob = "KNOB" in _upper(text) or "노브" in text
    has_rank = any(t in low or t in text for t in (
        "가장 빠", "제일 빠", "어디", "앞선", "진행", "current", "latest", "fastest", "advanced",
    ))
    return has_knob and has_rank


def _mentioned_values(prompt: str, values: list[str]) -> list[str]:
    up = _upper(prompt)
    toks = set(_tokens(prompt))
    out = []
    for value in values:
        raw = _text(value)
        val = _upper(raw)
        if not val:
            continue
        hit = val in toks if len(val) <= 2 else val in up
        if hit and raw not in out:
            out.append(raw)
    return out


def _step_rank_key(step_id: Any) -> tuple[int, ...]:
    nums = [int(x) for x in re.findall(r"\d+", str(step_id or ""))]
    if not nums:
        return (-1,)
    return tuple(nums[-4:])


def _latest_fab_steps_for_roots(product: str, roots: list[str], limit: int = 200) -> dict[str, dict[str, Any]]:
    clean_roots = [r for r in dict.fromkeys(_text(r) for r in roots) if r]
    if not clean_roots:
        return {}
    files: list[Path] = []
    for root in _db_root_candidates("FAB"):
        files.extend(sorted(root.rglob("*.parquet")))
    files = _filter_files_by_product(files, product)
    if not files:
        return {}
    lf = _scan_parquet(files)
    cols = _schema_names(lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    step_col = _ci_col(cols, "step_id", "STEP_ID")
    time_col = _ci_col(cols, "tkout_time", "TKOUT_TIME", "time", "TIME", "timestamp", "TIMESTAMP", "move_time", "MOVE_TIME")
    if not step_col or not (root_col or lot_col or fab_col):
        return {}
    aliases = _product_aliases(product)
    filters = []
    if aliases and product_col:
        filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    if root_col:
        filters.append(pl.col(root_col).cast(_STR, strict=False).is_in(clean_roots))
    else:
        lot_expr = _or_contains([c for c in (lot_col, fab_col) if c], clean_roots)
        if lot_expr is not None:
            filters.append(lot_expr)
    for expr in filters:
        lf = lf.filter(expr)
    exprs = []
    if product_col:
        exprs.append(pl.col(product_col).cast(_STR, strict=False).alias("product"))
    else:
        exprs.append(pl.lit(_core_product_name(product)).alias("product"))
    if root_col:
        exprs.append(pl.col(root_col).cast(_STR, strict=False).alias("root_lot_id"))
    elif lot_col:
        exprs.append(pl.col(lot_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id"))
    else:
        exprs.append(pl.col(fab_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id"))
    for src, alias in ((lot_col, "lot_id"), (fab_col, "fab_lot_id"), (wafer_col, "wafer_id")):
        if src:
            exprs.append(pl.col(src).cast(_STR, strict=False).alias(alias))
        else:
            exprs.append(pl.lit("").alias(alias))
    exprs.append(pl.col(step_col).cast(_STR, strict=False).alias("step_id"))
    if time_col:
        exprs.append(pl.col(time_col).cast(_STR, strict=False).alias("time"))
    else:
        exprs.append(pl.lit("").alias("time"))
    try:
        scoped = lf.select(exprs).drop_nulls(subset=["step_id"])
        if time_col:
            scoped = scoped.sort("time", descending=True)
        df = (
            scoped.group_by("root_lot_id")
            .agg([
                pl.col("product").first(),
                pl.col("lot_id").first(),
                pl.col("fab_lot_id").first(),
                pl.col("wafer_id").first(),
                pl.col("step_id").first(),
                pl.col("time").first(),
            ])
            .limit(max(1, min(1000, limit)))
            .collect()
        )
    except Exception as e:
        logger.warning("flowi latest fab step scan failed: %s", e)
        return {}
    try:
        from core.lot_step import lookup_step_meta
    except Exception:
        lookup_step_meta = None
    out = {}
    for row in df.to_dicts():
        root = _text(row.get("root_lot_id"))
        if not root:
            continue
        meta = lookup_step_meta(product=row.get("product") or product, step_id=row.get("step_id")) if lookup_step_meta else {}
        out[root] = {
            **row,
            "func_step": meta.get("func_step") or meta.get("function_step") or meta.get("step_desc") or "",
            "step_rank": _step_rank_key(row.get("step_id")),
        }
    return out


def _handle_fastest_knob_query(prompt: str, product: str, max_rows: int) -> dict:
    if not _fastest_knob_intent(prompt):
        return {"handled": False}
    files = _ml_files(product)
    if not files:
        return {
            "handled": True,
            "intent": "knob_fastest_lot",
            "answer": "ML_TABLE parquet을 찾지 못했습니다. product 또는 DB root를 확인해주세요.",
            "table": {
                "kind": "knob_fastest_lot",
                "title": "KNOB fastest lot",
                "placement": "below",
                "columns": _table_columns(["message"]),
                "rows": [{"message": "ML_TABLE not found"}],
                "total": 0,
            },
        }
    lf = _scan_parquet(files)
    cols = _schema_names(lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    if not root_col:
        return {"handled": True, "intent": "knob_fastest_lot", "answer": "ML_TABLE에 root_lot_id 컬럼이 없어 FAB 진행 위치를 연결할 수 없습니다."}
    knob_cols = [c for c in cols if _upper(c).startswith("KNOB_")]
    if not knob_cols:
        return {"handled": True, "intent": "knob_fastest_lot", "answer": "ML_TABLE에서 KNOB_* 컬럼을 찾지 못했습니다.", "knobs": []}

    lots = _lot_tokens(prompt)
    aliases = _product_aliases(product)
    filters = []
    if aliases and product_col:
        filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    if lots:
        lot_expr = _or_contains([c for c in (root_col, lot_col, fab_col) if c], lots)
        if lot_expr is not None:
            filters.append(lot_expr)
    for expr in filters:
        lf = lf.filter(expr)

    terms = _flowi_knob_query_terms(prompt, lots, [])
    if not terms:
        candidates = knob_cols[:12]
        return {
            "handled": True,
            "intent": "knob_fastest_lot",
            "answer": "어떤 KNOB 기준으로 가장 앞선 lot을 찾을지 선택이 필요합니다. 아래 후보 중 하나를 골라주세요.",
            "clarification": {
                "question": "가장 빠른 lot을 찾을 KNOB 컬럼을 선택하세요.",
                "choices": [
                    {
                        "id": f"knob_{i}",
                        "label": str(i + 1),
                        "title": col.replace("KNOB_", "", 1),
                        "recommended": i == 0,
                        "description": f"{col} 값을 가진 lot 중 FAB 최신 step이 가장 앞선 lot을 찾습니다.",
                        "prompt": f"{prompt.strip()} {col}",
                    }
                    for i, col in enumerate(candidates[:4])
                ],
            },
            "table": {
                "kind": "knob_candidates",
                "title": "KNOB column candidates",
                "placement": "below",
                "columns": _table_columns(["knob"]),
                "rows": [{"knob": c} for c in candidates],
                "total": len(candidates),
            },
        }

    knob_col, knob_candidates = _select_knob_column(lf, knob_cols, prompt, lots, [])
    if not knob_col:
        return {"handled": True, "intent": "knob_fastest_lot", "answer": "요청과 맞는 KNOB 컬럼을 찾지 못했습니다.", "knobs": []}
    values = _unique_strings(lf, knob_col, limit=100)
    selected_values = _mentioned_values(prompt, values)
    value_filter = selected_values or []
    scoped = lf
    if value_filter:
        scoped = scoped.filter(pl.col(knob_col).cast(_STR, strict=False).is_in(value_filter))
    else:
        scoped = scoped.filter(
            pl.col(knob_col).is_not_null()
            & (pl.col(knob_col).cast(_STR, strict=False).str.strip_chars() != "")
            & (~pl.col(knob_col).cast(_STR, strict=False).is_in(["None", "null"]))
        )
    keep = [c for c in (product_col, root_col, lot_col, fab_col, wafer_col, knob_col) if c]
    try:
        df = scoped.select([pl.col(c).cast(_STR, strict=False).alias(c) for c in keep]).limit(5000).collect()
    except Exception as e:
        logger.warning("flowi fastest knob ML scan failed: %s", e)
        return {"handled": True, "intent": "knob_fastest_lot", "answer": f"ML_TABLE KNOB 조회 실패: {e}"}
    if df.height == 0:
        return {
            "handled": True,
            "intent": "knob_fastest_lot",
            "answer": f"{knob_col} 조건에 맞는 ML_TABLE row가 없습니다.",
            "table": {
                "kind": "knob_fastest_lot",
                "title": f"{knob_col} fastest lot",
                "placement": "below",
                "columns": _table_columns(["message"]),
                "rows": [{"message": "no ML_TABLE rows"}],
                "total": 0,
            },
        }
    grouped: dict[str, dict[str, Any]] = {}
    for row in df.to_dicts():
        root = _text(row.get(root_col))
        if not root:
            continue
        rec = grouped.setdefault(root, {
            "product": _text(row.get(product_col)) or _core_product_name(product),
            "root_lot_id": root,
            "lot_id": _text(row.get(lot_col)),
            "fab_lot_id": _text(row.get(fab_col)),
            "knob": knob_col,
            "knob_value": _text(row.get(knob_col)),
            "wafer_count": 0,
            "wafers": set(),
        })
        wafer = _text(row.get(wafer_col))
        if wafer:
            rec["wafers"].add(wafer)
        rec["wafer_count"] += 1
        if not rec.get("lot_id") and _text(row.get(lot_col)):
            rec["lot_id"] = _text(row.get(lot_col))
        if not rec.get("fab_lot_id") and _text(row.get(fab_col)):
            rec["fab_lot_id"] = _text(row.get(fab_col))
    roots = list(grouped.keys())[:200]
    fab_steps = _latest_fab_steps_for_roots(product or (next(iter(grouped.values())).get("product") or ""), roots, limit=300)
    rows = []
    for root, rec in grouped.items():
        fab = fab_steps.get(root) or {}
        wafers = sorted(rec.pop("wafers"), key=lambda x: (len(x), x))
        row = {
            **rec,
            "wafer_ids": ",".join(wafers[:12]),
            "current_step_id": fab.get("step_id") or "",
            "func_step": fab.get("func_step") or "",
            "fab_lot_current": fab.get("fab_lot_id") or rec.get("fab_lot_id") or "",
            "current_lot_id": fab.get("lot_id") or rec.get("lot_id") or "",
            "current_wafer_id": fab.get("wafer_id") or "",
            "tkout_time": fab.get("time") or "",
            "_rank": fab.get("step_rank") or (-1,),
        }
        rows.append(row)
    rows.sort(key=lambda r: (tuple(r.get("_rank") or (-1,)), str(r.get("tkout_time") or "")), reverse=True)
    for row in rows:
        row.pop("_rank", None)
    shown = rows[:max(1, min(40, max_rows))]
    cols_out = [
        "product", "root_lot_id", "knob", "knob_value", "wafer_count",
        "current_step_id", "func_step", "fab_lot_current", "current_lot_id", "tkout_time",
    ]
    top = shown[0] if shown else {}
    answer = (
        f"{knob_col} 값을 가진 lot 중 FAB 최신 step 기준으로 가장 앞선 후보를 계산했습니다. "
        f"Top: {top.get('root_lot_id') or '-'} / {top.get('current_step_id') or '-'}"
        f"{' (' + top.get('func_step') + ')' if top.get('func_step') else ''}."
    )
    if value_filter:
        answer += f" 값 필터: {', '.join(value_filter)}."
    return {
        "handled": True,
        "intent": "knob_fastest_lot",
        "action": "query_knob_fastest_fab_step",
        "answer": answer,
        "table": {
            "kind": "knob_fastest_lot",
            "title": f"{knob_col} fastest FAB step",
            "placement": "below",
            "columns": _table_columns(cols_out),
            "rows": [{k: row.get(k, "") for k in cols_out} for row in shown],
            "total": len(rows),
        },
        "filters": {"product": sorted(aliases), "lot": lots, "knob": knob_col, "values": value_filter, "knob_candidates": knob_candidates[:12]},
    }


def _sort_wafer_rows(rows: list[dict]) -> list[dict]:
    def key(row):
        raw = _text(row.get("wafer_id") or row.get("WAFER_ID"))
        m = re.search(r"\d+", raw)
        return (int(m.group(0)) if m else 9999, raw)
    return sorted(rows, key=key)


def _round4(value: Any) -> Any:
    if value is None:
        return None
    try:
        return round(float(value), 4)
    except Exception:
        return value


def _or_contains(cols: list[str], needles: list[str]) -> Any:
    expr = None
    for col in cols:
        for tok in needles:
            piece = pl.col(col).cast(_STR, strict=False).str.contains(tok, literal=True)
            expr = piece if expr is None else (expr | piece)
    return expr


def _parse_flowi_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            try:
                return value.astimezone(timezone.utc).replace(tzinfo=None)
            except Exception:
                return value.replace(tzinfo=None)
        return value
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "nan"}:
        return None
    text = text.replace("Z", "+00:00")
    if " " in text and "T" not in text:
        text = text.replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y%m%d%H%M%S", "%Y%m%d%H%M", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(str(value).strip(), fmt)
        except Exception:
            continue
    return None


def _fmt_flowi_datetime(value: datetime | None) -> str:
    return value.isoformat(timespec="seconds") if value else ""


def _flowi_hours_between(start: Any, end: Any) -> float | None:
    start_dt = _parse_flowi_datetime(start)
    end_dt = _parse_flowi_datetime(end)
    if not start_dt or not end_dt:
        return None
    return round((end_dt - start_dt).total_seconds() / 3600.0, 3)


def _flowi_percentile(values: list[float], q: float) -> float | None:
    clean = sorted(float(v) for v in values if v is not None)
    if not clean:
        return None
    if len(clean) == 1:
        return round(clean[0], 3)
    pos = (len(clean) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(clean) - 1)
    frac = pos - lo
    return round(clean[lo] * (1 - frac) + clean[hi] * frac, 3)


def _path_tail(fp: Path, depth: int = 4) -> str:
    parts = fp.parts[-depth:]
    return "/".join(parts)


def _flowi_report_terms(prompt: str, lots: list[str] | None = None, product: str = "") -> list[str]:
    blocked = set(_STOP_TOKENS) | {
        "ET", "REPORT", "REPORTED", "업데이트", "최근업데이트", "최근", "안올라왔는데",
        "안올라", "올라왔", "보여줘", "측정시간", "MEASURE", "MEASUREMENT", "DURATION",
        "얼마나", "걸렸어", "걸려", "언제", "도착", "ETA",
    }
    for lot in lots or []:
        blocked.add(_upper(lot))
    blocked.update(_product_aliases(product))
    out: list[str] = []
    seen: set[str] = set()
    for tok in _query_tokens(prompt):
        key = _upper(tok)
        if not key or key in blocked:
            continue
        if re.fullmatch(r"[A-Z]\d{4,}(?:[A-Z])?(?:\.\d+)?", key):
            continue
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out[:10]


def _step_id_terms_from_prompt(prompt: str, lots: list[str] | None = None, product: str = "") -> list[str]:
    blocked = set(_STOP_TOKENS)
    blocked.update(_upper(v) for v in lots or [])
    blocked.update(_product_aliases(product))
    out: list[str] = []
    seen: set[str] = set()
    for sid in _step_tokens(prompt):
        key = _upper(sid)
        if key and key not in seen and key not in blocked:
            seen.add(key)
            out.append(sid)
    text = str(prompt or "")
    for m in re.finditer(r"\bstep[_\s-]*id\s*(?:=|:|가|이|는|은|가\s*이건데|이\s*이건데)?\s*([A-Za-z0-9_.-]+)", text, flags=re.I):
        raw = (m.group(1) or "").strip(" .,;:()[]{}")
        key = _upper(raw)
        if not key or key in blocked or key.startswith(("PPID", "PROD", "KNOB")):
            continue
        if not _is_step_id_token(key) and key not in _known_func_step_names():
            continue
        if key not in seen:
            seen.add(key)
            out.append(raw)
    return out[:6]


def _ppid_tokens(prompt: str) -> list[str]:
    text = str(prompt or "")
    out: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"\b(PP(?:ID)?[A-Za-z0-9_.-]{1,80})\b", text, flags=re.I):
        raw = (m.group(1) or "").strip(" .,;:()[]{}")
        key = _upper(raw)
        if key and key not in {"PPID", "PP"} and key not in seen:
            seen.add(key)
            out.append(raw)
    toks = _tokens(text)
    for i, tok in enumerate(toks[:-1]):
        if _upper(tok) == "PPID":
            raw = toks[i + 1].strip(" .,;:()[]{}")
            key = _upper(raw)
            if key and key not in _STOP_TOKENS and key not in seen:
                seen.add(key)
                out.append(raw)
    return out[:6]


def _files_matching_prompt_terms(files: list[Path], prompt: str, lots: list[str], product: str = "") -> list[Path]:
    terms = _flowi_report_terms(prompt, lots, product)
    if not terms:
        return files
    filtered = []
    for fp in files:
        hay = _upper(str(fp))
        if any(term in hay for term in terms):
            filtered.append(fp)
    return filtered or files


def _flowi_lot_root_expr(cols: list[str], lots: list[str]):
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
    return _or_contains([c for c in (root_col, lot_col, fab_col) if c], lots)


def _is_fab_step_eta_prompt(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    if not _lot_tokens(prompt):
        return False
    if not any(t in low or t in text for t in ("도착", "언제쯤", "언제", "eta", "arrival", "arrive")):
        return False
    return bool(_step_tokens(prompt) or _step_query_terms(prompt, _lot_tokens(prompt)))


def _target_step_ids_from_fab_rows(prompt: str, rows: list[dict[str, Any]], lots: list[str], product: str) -> tuple[list[str], list[dict[str, Any]]]:
    lot_set = {_upper(v) for v in lots}
    exact = [s for s in _step_tokens(prompt) if _upper(s) not in lot_set]
    if exact:
        seen = set()
        out = []
        for sid in exact:
            key = _upper(sid)
            if key not in seen:
                seen.add(key)
                out.append(sid)
        return out, []
    terms = _step_query_terms(prompt, lots, product)
    if not terms:
        return [], []
    candidates: dict[str, dict[str, Any]] = {}
    for row in rows:
        sid = _text(row.get("step_id"))
        if not sid:
            continue
        func = _function_step_label(row.get("product") or product, sid)
        hay = _upper(" ".join([sid, func]))
        if not any(term in hay for term in terms):
            continue
        candidates.setdefault(sid, {"step_id": sid, "function_step": func, "row_count": 0})
        candidates[sid]["row_count"] += 1
    cand_rows = sorted(candidates.values(), key=lambda r: (-int(r.get("row_count") or 0), r.get("step_id") or ""))
    if len(cand_rows) == 1:
        return [cand_rows[0]["step_id"]], cand_rows
    return [], cand_rows


def _handle_fab_step_eta(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _is_fab_step_eta_prompt(prompt):
        return {"handled": False}
    lots = _lot_tokens(prompt)
    product_hint, candidate_tool = _product_or_candidate_tool(prompt, product, lots, kinds=("FAB",), intent="fab_step_eta")
    if candidate_tool:
        return candidate_tool
    files = _fab_files(product_hint)
    if not files:
        return {
            "handled": True,
            "intent": "fab_step_eta",
            "answer": "FAB parquet을 찾지 못했습니다. product 또는 DB root를 확인해주세요.",
            "table": {"kind": "fab_step_eta", "title": "FAB step ETA", "placement": "below", "columns": _table_columns(["message"]), "rows": [{"message": "FAB not found"}], "total": 0},
        }
    lf = _scan_parquet(files)
    cols = _schema_names(lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    step_col = _ci_col(cols, "step_id", "STEP_ID")
    time_col = _ci_col(cols, "tkout_time", "TKOUT_TIME", "time", "TIME", "timestamp", "TIMESTAMP", "move_time", "MOVE_TIME", "updated_at", "UPDATED_AT")
    if not step_col or not (root_col or lot_col or fab_col):
        return {"handled": True, "intent": "fab_step_eta", "answer": "FAB 데이터에서 root/lot 또는 step_id 컬럼을 찾지 못했습니다."}
    aliases = _product_aliases(product_hint)
    if aliases and product_col:
        lf = lf.filter(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    exprs = [
        pl.col(product_col).cast(_STR, strict=False).alias("product") if product_col else pl.lit(_core_product_name(product_hint)).alias("product"),
        pl.col(root_col).cast(_STR, strict=False).alias("root_lot_id") if root_col else (
            pl.col(lot_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id") if lot_col else pl.col(fab_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id")
        ),
        pl.col(lot_col).cast(_STR, strict=False).alias("lot_id") if lot_col else pl.lit("").alias("lot_id"),
        pl.col(fab_col).cast(_STR, strict=False).alias("fab_lot_id") if fab_col else pl.lit("").alias("fab_lot_id"),
        pl.col(wafer_col).cast(_STR, strict=False).alias("wafer_id") if wafer_col else pl.lit("").alias("wafer_id"),
        pl.col(step_col).cast(_STR, strict=False).alias("step_id"),
        pl.col(time_col).cast(_STR, strict=False).alias("time") if time_col else pl.lit("").alias("time"),
    ]
    try:
        df = lf.select(exprs).drop_nulls(subset=["step_id"]).limit(120000).collect()
    except Exception as e:
        return {"handled": True, "intent": "fab_step_eta", "answer": f"FAB ETA 조회 실패: {e}"}
    rows_all = df.to_dicts()
    target_steps, candidates = _target_step_ids_from_fab_rows(prompt, rows_all, lots, product_hint)
    if not target_steps:
        if candidates:
            return {
                "handled": True,
                "intent": "fab_step_eta",
                "action": "clarify_target_step",
                "answer": "도착 ETA를 계산할 target step이 여러 후보로 매칭됐습니다. step_id를 하나 선택해주세요.",
                "clarification": {
                    "question": "어느 step 도착 기준으로 볼까요?",
                    "choices": [
                        {
                            "id": f"step_{i}",
                            "label": str(i + 1),
                            "title": f"{row.get('step_id')} {row.get('function_step') or ''}".strip(),
                            "recommended": i == 0,
                            "description": f"FAB row {row.get('row_count')}건",
                            "prompt": f"{prompt.strip()} {row.get('step_id')}",
                        }
                        for i, row in enumerate(candidates[:4])
                    ],
                },
                "table": {"kind": "fab_step_candidates", "title": "FAB target step candidates", "placement": "below", "columns": _table_columns(["step_id", "function_step", "row_count"]), "rows": candidates[:max(1, max_rows)], "total": len(candidates)},
            }
        return {"handled": True, "intent": "fab_step_eta", "answer": "도착 기준 step_id를 찾지 못했습니다. 예: `A0001 AA230400에 언제쯤 도착해?`"}
    target_step = target_steps[0]
    wafers = _wafer_tokens(prompt)
    lot_expr_values = {_upper(v) for v in lots}
    def lot_hit(row: dict[str, Any]) -> bool:
        hay = _upper(" ".join([row.get("root_lot_id") or "", row.get("lot_id") or "", row.get("fab_lot_id") or ""]))
        return any(tok and tok in hay for tok in lot_expr_values)
    def wafer_hit(row: dict[str, Any]) -> bool:
        if not wafers:
            return True
        vals = set()
        for wf in wafers:
            vals.add(wf)
            try:
                vals.add(str(int(wf)))
                vals.add(f"{int(wf):02d}")
            except Exception:
                pass
        return _text(row.get("wafer_id")) in vals
    lot_rows = [row for row in rows_all if lot_hit(row) and wafer_hit(row)]
    if not lot_rows:
        return {"handled": True, "intent": "fab_step_eta", "answer": f"{', '.join(lots)}에 해당하는 FAB row를 찾지 못했습니다."}
    def row_sort_key(row: dict[str, Any]):
        dt = _parse_flowi_datetime(row.get("time"))
        return (dt or datetime.min, _step_rank_key(row.get("step_id")))
    lot_rows.sort(key=row_sort_key, reverse=True)
    current = lot_rows[0]
    current_step = _text(current.get("step_id"))
    current_time = _parse_flowi_datetime(current.get("time"))
    reached_rows = [row for row in lot_rows if _upper(row.get("step_id")) == _upper(target_step)]
    target_func = _function_step_label(current.get("product") or product_hint, target_step)
    current_func = _function_step_label(current.get("product") or product_hint, current_step)
    if reached_rows:
        reached_rows.sort(key=row_sort_key)
        first_reached = reached_rows[0]
        latest_reached = reached_rows[-1]
        row = {
            "product": current.get("product") or product_hint,
            "root_lot_id": current.get("root_lot_id") or lots[0],
            "current_step_id": current_step,
            "current_function_step": current_func,
            "target_step_id": target_step,
            "target_function_step": target_func,
            "status": "already_reached",
            "current_time": current.get("time") or "",
            "first_target_time": first_reached.get("time") or "",
            "latest_target_time": latest_reached.get("time") or "",
            "eta_median_hours": 0,
            "eta_p80_hours": 0,
            "eta_at_median": latest_reached.get("time") or "",
            "eta_at_p80": latest_reached.get("time") or "",
            "sample_lots": 0,
            "confidence": "actual",
        }
        cols_out = ["product", "root_lot_id", "current_step_id", "current_function_step", "target_step_id", "target_function_step", "status", "current_time", "first_target_time", "latest_target_time", "eta_median_hours", "eta_p80_hours", "eta_at_median", "eta_at_p80", "sample_lots", "confidence"]
        return {
            "handled": True,
            "intent": "fab_step_eta",
            "action": "query_fab_step_eta",
            "answer": f"{row['root_lot_id']}는 이미 {target_step}{('(' + target_func + ')') if target_func else ''}에 도착했습니다. 최초 도착: {row['first_target_time'] or '-'}, 최신 기록: {row['latest_target_time'] or '-'}.",
            "table": {"kind": "fab_step_eta", "title": "FAB step ETA", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: row.get(k, "") for k in cols_out}], "total": 1},
            "filters": {"product": product_hint, "lots": lots, "wafers": wafers, "target_step": target_step},
        }
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows_all:
        root = _text(row.get("root_lot_id"))
        if root:
            grouped.setdefault(root, []).append(row)
    sample_rows: list[dict[str, Any]] = []
    durations: list[float] = []
    current_key_roots = {_text(r.get("root_lot_id")) for r in lot_rows if _text(r.get("root_lot_id"))}
    for root, root_rows in grouped.items():
        if root in current_key_roots:
            continue
        starts = [r for r in root_rows if _upper(r.get("step_id")) == _upper(current_step) and _parse_flowi_datetime(r.get("time"))]
        targets = [r for r in root_rows if _upper(r.get("step_id")) == _upper(target_step) and _parse_flowi_datetime(r.get("time"))]
        if not starts or not targets:
            continue
        starts.sort(key=lambda r: _parse_flowi_datetime(r.get("time")) or datetime.min)
        targets.sort(key=lambda r: _parse_flowi_datetime(r.get("time")) or datetime.min)
        best = None
        for start in reversed(starts):
            start_dt = _parse_flowi_datetime(start.get("time"))
            target_after = next((t for t in targets if (_parse_flowi_datetime(t.get("time")) or datetime.min) >= start_dt), None)
            if target_after:
                best = (start, target_after)
                break
        if not best:
            continue
        hours = _flowi_hours_between(best[0].get("time"), best[1].get("time"))
        if hours is None or hours < 0:
            continue
        durations.append(hours)
        sample_rows.append({
            "root_lot_id": root,
            "from_time": best[0].get("time") or "",
            "target_time": best[1].get("time") or "",
            "duration_hours": hours,
        })
    median_h = _flowi_percentile(durations, 0.5)
    p80_h = _flowi_percentile(durations, 0.8)
    eta_median = current_time + timedelta(hours=median_h) if current_time and median_h is not None else None
    eta_p80 = current_time + timedelta(hours=p80_h) if current_time and p80_h is not None else None
    confidence = "high" if len(durations) >= 5 else ("medium" if len(durations) >= 2 else ("low" if durations else "no_sample"))
    row = {
        "product": current.get("product") or product_hint,
        "root_lot_id": current.get("root_lot_id") or lots[0],
        "current_step_id": current_step,
        "current_function_step": current_func,
        "target_step_id": target_step,
        "target_function_step": target_func,
        "status": "estimated" if durations else "no_historical_sample",
        "current_time": current.get("time") or "",
        "eta_median_hours": median_h if median_h is not None else "",
        "eta_p80_hours": p80_h if p80_h is not None else "",
        "eta_at_median": _fmt_flowi_datetime(eta_median),
        "eta_at_p80": _fmt_flowi_datetime(eta_p80),
        "sample_lots": len(durations),
        "confidence": confidence,
    }
    cols_out = ["product", "root_lot_id", "current_step_id", "current_function_step", "target_step_id", "target_function_step", "status", "current_time", "eta_median_hours", "eta_p80_hours", "eta_at_median", "eta_at_p80", "sample_lots", "confidence"]
    if durations:
        answer = (
            f"{row['root_lot_id']} 현재 위치는 {current_step}{('(' + current_func + ')') if current_func else ''}이고, "
            f"{target_step}{('(' + target_func + ')') if target_func else ''} 도착 예상은 median 기준 {row['eta_at_median'] or '-'} "
            f"(p80 {row['eta_at_p80'] or '-'})입니다. 과거 sample lot {len(durations)}개 기준입니다."
        )
    else:
        answer = (
            f"{row['root_lot_id']} 현재 위치는 {current_step}{('(' + current_func + ')') if current_func else ''}입니다. "
            f"{target_step}{('(' + target_func + ')') if target_func else ''}까지의 과거 duration sample을 찾지 못해 ETA는 계산하지 않았습니다."
        )
    return {
        "handled": True,
        "intent": "fab_step_eta",
        "action": "query_fab_step_eta",
        "answer": answer,
        "table": {"kind": "fab_step_eta", "title": "FAB step ETA", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: row.get(k, "") for k in cols_out}], "total": 1},
        "samples_table": {"kind": "fab_step_eta_samples", "title": "Historical FAB step durations", "placement": "below", "columns": _table_columns(["root_lot_id", "from_time", "target_time", "duration_hours"]), "rows": sample_rows[:max(1, max_rows)], "total": len(sample_rows)},
        "filters": {"product": product_hint, "lots": lots, "wafers": wafers, "target_step": target_step},
    }


def _is_et_report_freshness_prompt(prompt: str) -> bool:
    up = _upper(prompt)
    text = str(prompt or "")
    low = text.lower()
    if "ET" not in up or "REPORT" not in up:
        return False
    return any(t in low or t in text for t in ("최근업데이트", "최근 업데이트", "업데이트", "안올라", "안 올라", "latest", "fresh", "updated"))


def _is_et_report_lookup_prompt(prompt: str) -> bool:
    up = _upper(prompt)
    if "ET" not in up or "REPORT" not in up:
        return False
    return not _is_et_report_freshness_prompt(prompt)


def _et_product_or_candidate(prompt: str, product: str, lots: list[str], intent: str) -> tuple[str, dict[str, Any] | None]:
    product_hint = _product_hint(prompt, product)
    if product_hint:
        return product_hint, None
    if lots:
        product_hint, candidate_tool = _product_or_candidate_tool(prompt, product, lots, kinds=("ET", "FAB"), intent=intent)
        if product_hint or candidate_tool:
            return product_hint, candidate_tool
    return "", None


def _handle_et_report_freshness(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _is_et_report_freshness_prompt(prompt):
        return {"handled": False}
    lots = _lot_tokens(prompt)
    product_hint, candidate_tool = _et_product_or_candidate(prompt, product, lots, "et_report_freshness_lookup")
    if candidate_tool:
        return candidate_tool
    files = _files_matching_prompt_terms(_et_files(product_hint), prompt, lots, product_hint)
    if not files:
        return {
            "handled": True,
            "intent": "et_report_freshness_lookup",
            "answer": "ET Report 원천 parquet을 찾지 못했습니다. product 또는 DB root를 확인해주세요.",
            "table": {"kind": "et_report_freshness", "title": "ET Report freshness", "placement": "below", "columns": _table_columns(["message"]), "rows": [{"message": "ET not found"}], "total": 0},
        }
    rows: list[dict[str, Any]] = []
    aliases = _product_aliases(product_hint)
    for fp in files[:120]:
        rec = {
            "source": _path_tail(fp),
            "file_mtime": "",
            "latest_data_time": "",
            "row_count": 0,
            "status": "ok",
        }
        try:
            rec["file_mtime"] = _fmt_flowi_datetime(datetime.fromtimestamp(fp.stat().st_mtime))
        except Exception:
            pass
        try:
            lf = _scan_parquet([fp])
            cols = _schema_names(lf)
            product_col = _ci_col(cols, "product", "PRODUCT")
            time_col = _ci_col(cols, "tkout_time", "TKOUT_TIME", "time", "TIME", "timestamp", "TIMESTAMP", "updated_at", "UPDATED_AT", "measure_time", "MEASURE_TIME")
            filters = []
            if aliases and product_col:
                filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
            if lots:
                lot_expr = _flowi_lot_root_expr(cols, lots)
                if lot_expr is not None:
                    filters.append(lot_expr)
            for expr in filters:
                lf = lf.filter(expr)
            aggs = [pl.len().alias("row_count")]
            if time_col:
                aggs.append(pl.col(time_col).cast(_STR, strict=False).max().alias("latest_data_time"))
            else:
                aggs.append(pl.lit("").alias("latest_data_time"))
            got = lf.select(aggs).collect().to_dicts()[0]
            rec["row_count"] = int(got.get("row_count") or 0)
            rec["latest_data_time"] = _text(got.get("latest_data_time"))
            if not time_col:
                rec["status"] = "time_column_not_found"
        except Exception as e:
            rec["status"] = f"scan_failed: {e}"
        rows.append(rec)
    rows.sort(key=lambda r: (r.get("latest_data_time") or "", r.get("file_mtime") or ""), reverse=True)
    latest_file = rows[0] if rows else {}
    data_latest = next((r for r in rows if r.get("latest_data_time")), latest_file)
    answer = (
        f"ET Report 최근 업데이트를 확인했습니다. 파일 기준 최신은 {latest_file.get('file_mtime') or '-'} "
        f"({latest_file.get('source') or '-'}), 데이터 time 기준 최신은 {data_latest.get('latest_data_time') or '-'}입니다."
    )
    cols_out = ["source", "file_mtime", "latest_data_time", "row_count", "status"]
    return {
        "handled": True,
        "intent": "et_report_freshness_lookup",
        "action": "query_et_report_freshness",
        "answer": answer,
        "table": {"kind": "et_report_freshness", "title": "ET Report freshness", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in rows[:max(1, min(80, max_rows * 6))]], "total": len(rows)},
        "filters": {"product": product_hint, "lots": lots},
    }


def _handle_et_report_lookup(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _is_et_report_lookup_prompt(prompt):
        return {"handled": False}
    lots = _lot_tokens(prompt)
    product_hint, candidate_tool = _et_product_or_candidate(prompt, product, lots, "et_report_lookup")
    if candidate_tool:
        return candidate_tool
    files = _files_matching_prompt_terms(_et_files(product_hint), prompt, lots, product_hint)
    if not files:
        return {"handled": True, "intent": "et_report_lookup", "answer": "ET Report 원천 parquet을 찾지 못했습니다."}
    lf = _scan_parquet(files)
    cols = _schema_names(lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    step_col = _ci_col(cols, "step_id", "STEP_ID")
    item_col = _ci_col(cols, "item_id", "ITEM_ID")
    value_col = _ci_col(cols, "value", "VALUE")
    time_col = _ci_col(cols, "tkout_time", "TKOUT_TIME", "time", "TIME", "timestamp", "TIMESTAMP", "measure_time", "MEASURE_TIME", "updated_at", "UPDATED_AT")
    aliases = _product_aliases(product_hint)
    filters = []
    if aliases and product_col:
        filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    if lots:
        lot_expr = _flowi_lot_root_expr(cols, lots)
        if lot_expr is not None:
            filters.append(lot_expr)
    wafers = _wafer_tokens(prompt)
    wf_expr = _wafer_match_expr(wafer_col, wafers)
    if wf_expr is not None:
        filters.append(wf_expr)
    terms = _flowi_report_terms(prompt, lots, product_hint)
    if terms and item_col:
        matches = _match_values(_unique_strings(lf, item_col, limit=500), terms)
        if matches:
            filters.append(pl.col(item_col).cast(_STR, strict=False).is_in(matches))
    for expr in filters:
        lf = lf.filter(expr)
    exprs = [
        pl.col(product_col).cast(_STR, strict=False).alias("product") if product_col else pl.lit(_core_product_name(product_hint)).alias("product"),
        pl.col(root_col).cast(_STR, strict=False).alias("root_lot_id") if root_col else (
            pl.col(lot_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id") if lot_col else (pl.col(fab_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id") if fab_col else pl.lit("").alias("root_lot_id"))
        ),
        pl.col(step_col).cast(_STR, strict=False).alias("step_id") if step_col else pl.lit("").alias("step_id"),
        pl.col(item_col).cast(_STR, strict=False).alias("item_id") if item_col else pl.lit("").alias("item_id"),
        pl.col(wafer_col).cast(_STR, strict=False).alias("wafer_id") if wafer_col else pl.lit("").alias("wafer_id"),
        pl.col(time_col).cast(_STR, strict=False).alias("latest_time") if time_col else pl.lit("").alias("latest_time"),
    ]
    if value_col:
        exprs.append(pl.col(value_col).cast(pl.Float64, strict=False).alias("value"))
    try:
        scoped = lf.select(exprs)
        group_cols = ["product", "root_lot_id", "step_id", "item_id", "wafer_id"]
        aggs = [pl.len().alias("count"), pl.col("latest_time").max().alias("latest_time")]
        if value_col:
            aggs.extend([pl.col("value").median().alias("median"), pl.col("value").mean().alias("mean")])
        out = scoped.group_by(group_cols).agg(aggs)
        if "latest_time" in out.collect_schema().names():
            out = out.sort("latest_time", descending=True)
        df = out.limit(max(1, min(200, max_rows * 10))).collect()
    except Exception as e:
        return {"handled": True, "intent": "et_report_lookup", "answer": f"ET Report 조회 실패: {e}"}
    rows = df.to_dicts()
    for row in rows:
        row["function_step"] = _function_step_label(row.get("product") or product_hint, row.get("step_id"))
        row["median"] = _round4(row.get("median"))
        row["mean"] = _round4(row.get("mean"))
        row["count"] = int(row.get("count") or 0)
    cols_out = ["product", "root_lot_id", "step_id", "function_step", "item_id", "wafer_id", "median", "mean", "count", "latest_time"]
    answer = f"ET Report를 {len(rows)}개 그룹으로 조회했습니다."
    if lots:
        answer += f" lot/root 필터: {', '.join(lots)}."
    if not rows:
        answer = "조건에 맞는 ET Report row를 찾지 못했습니다."
    return {
        "handled": True,
        "intent": "et_report_lookup",
        "action": "query_et_report",
        "answer": answer,
        "table": {"kind": "et_report_lookup", "title": "ET Report", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in rows[:max(1, min(120, max_rows * 8))]], "total": len(rows)},
        "filters": {"product": product_hint, "lots": lots, "wafers": wafers, "terms": terms},
    }


def _is_measurement_duration_prompt(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    return bool(_lot_tokens(prompt)) and any(t in low or t in text for t in ("측정시간", "측정 시간", "얼마나 걸", "duration", "measure time", "measurement time"))


def _handle_measurement_duration_lookup(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _is_measurement_duration_prompt(prompt):
        return {"handled": False}
    lots = _lot_tokens(prompt)
    product_hint, candidate_tool = _et_product_or_candidate(prompt, product, lots, "measurement_duration_lookup")
    if candidate_tool:
        return candidate_tool
    files = _files_matching_prompt_terms(_et_files(product_hint), prompt, lots, product_hint)
    if not files:
        return {"handled": True, "intent": "measurement_duration_lookup", "answer": "측정시간을 계산할 ET parquet을 찾지 못했습니다."}
    lf = _scan_parquet(files)
    cols = _schema_names(lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    step_col = _ci_col(cols, "step_id", "STEP_ID")
    item_col = _ci_col(cols, "item_id", "ITEM_ID")
    start_col = _ci_col(cols, "tkin_time", "TKIN_TIME", "start_time", "START_TIME", "measure_start_time", "MEASURE_START_TIME", "measurement_start_time", "MEASUREMENT_START_TIME")
    end_col = _ci_col(cols, "tkout_time", "TKOUT_TIME", "end_time", "END_TIME", "measure_end_time", "MEASURE_END_TIME", "measurement_end_time", "MEASUREMENT_END_TIME")
    span_col = _ci_col(cols, "time", "TIME", "timestamp", "TIMESTAMP", "measure_time", "MEASURE_TIME")
    aliases = _product_aliases(product_hint)
    filters = []
    if aliases and product_col:
        filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    lot_expr = _flowi_lot_root_expr(cols, lots)
    if lot_expr is not None:
        filters.append(lot_expr)
    wafers = _wafer_tokens(prompt)
    wf_expr = _wafer_match_expr(wafer_col, wafers)
    if wf_expr is not None:
        filters.append(wf_expr)
    terms = _flowi_report_terms(prompt, lots, product_hint)
    item_matches = _match_values(_unique_strings(lf, item_col, limit=500), terms) if item_col else []
    step_matches = _match_values(_unique_strings(lf, step_col, limit=500), terms) if step_col else []
    if item_matches:
        filters.append(pl.col(item_col).cast(_STR, strict=False).is_in(item_matches))
    elif step_matches:
        filters.append(pl.col(step_col).cast(_STR, strict=False).is_in(step_matches))
    for expr in filters:
        lf = lf.filter(expr)
    start_src = start_col or span_col or end_col
    end_src = end_col or span_col or start_col
    if not (start_src and end_src):
        return {"handled": True, "intent": "measurement_duration_lookup", "answer": "측정 시작/종료/time 컬럼을 찾지 못했습니다."}
    exprs = [
        pl.col(product_col).cast(_STR, strict=False).alias("product") if product_col else pl.lit(_core_product_name(product_hint)).alias("product"),
        pl.col(root_col).cast(_STR, strict=False).alias("root_lot_id") if root_col else (
            pl.col(lot_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id") if lot_col else (pl.col(fab_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id") if fab_col else pl.lit("").alias("root_lot_id"))
        ),
        pl.col(step_col).cast(_STR, strict=False).alias("step_id") if step_col else pl.lit("").alias("step_id"),
        pl.col(item_col).cast(_STR, strict=False).alias("item_id") if item_col else pl.lit("").alias("item_id"),
        pl.col(wafer_col).cast(_STR, strict=False).alias("wafer_id") if wafer_col else pl.lit("").alias("wafer_id"),
        pl.col(start_src).cast(_STR, strict=False).alias("start_time"),
        pl.col(end_src).cast(_STR, strict=False).alias("end_time"),
    ]
    try:
        scoped = lf.select(exprs)
        group_cols = ["product", "root_lot_id", "step_id", "item_id", "wafer_id"]
        df = (
            scoped.group_by(group_cols)
            .agg([
                pl.col("start_time").min().alias("start_time"),
                pl.col("end_time").max().alias("end_time"),
                pl.len().alias("row_count"),
            ])
            .sort("end_time", descending=True)
            .limit(max(1, min(200, max_rows * 10)))
            .collect()
        )
    except Exception as e:
        return {"handled": True, "intent": "measurement_duration_lookup", "answer": f"측정시간 계산 실패: {e}"}
    rows = df.to_dicts()
    basis = "start_end_columns" if start_col and end_col else "span_of_time_column"
    for row in rows:
        row["function_step"] = _function_step_label(row.get("product") or product_hint, row.get("step_id"))
        hours = _flowi_hours_between(row.get("start_time"), row.get("end_time"))
        row["duration_min"] = round(hours * 60.0, 2) if hours is not None else ""
        row["duration_basis"] = basis
        row["row_count"] = int(row.get("row_count") or 0)
    cols_out = ["product", "root_lot_id", "step_id", "function_step", "item_id", "wafer_id", "start_time", "end_time", "duration_min", "duration_basis", "row_count"]
    answer = f"측정시간을 {len(rows)}개 그룹으로 계산했습니다."
    if rows:
        answer += f" 대표 duration: {rows[0].get('duration_min') or '-'}분."
    else:
        answer = "조건에 맞는 측정시간 row를 찾지 못했습니다."
    return {
        "handled": True,
        "intent": "measurement_duration_lookup",
        "action": "query_measurement_duration",
        "answer": answer,
        "table": {"kind": "measurement_duration", "title": "Measurement duration", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in rows[:max(1, min(120, max_rows * 8))]], "total": len(rows)},
        "filters": {"product": product_hint, "lots": lots, "wafers": wafers, "terms": terms, "item_matches": item_matches, "step_matches": step_matches},
    }


def _is_inline_item_lookup_prompt(prompt: str) -> bool:
    text = str(prompt or "")
    up = _upper(text)
    if "INLINE" not in up and "인라인" not in text:
        return False
    if "ITEM" not in up and "아이템" not in text and "항목" not in text:
        return False
    return bool(_step_id_terms_from_prompt(prompt))


def _handle_inline_item_lookup(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _is_inline_item_lookup_prompt(prompt):
        return {"handled": False}
    product_hint = _product_hint(prompt, product)
    step_terms = _step_id_terms_from_prompt(prompt, product=product_hint)
    files = _inline_files(product_hint)
    if not files:
        return {
            "handled": True,
            "intent": "inline_item_by_step_lookup",
            "answer": "INLINE parquet을 찾지 못했습니다. product 또는 DB root를 확인해주세요.",
            "table": {"kind": "inline_item_by_step", "title": "INLINE items by step", "placement": "below", "columns": _table_columns(["message"]), "rows": [{"message": "INLINE not found"}], "total": 0},
        }
    lf = _scan_parquet(files)
    cols = _schema_names(lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    step_col = _ci_col(cols, "step_id", "STEP_ID")
    item_col = _ci_col(cols, "item_id", "ITEM_ID", "inline_item", "INLINE_ITEM")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    time_col = _ci_col(cols, "time", "TIME", "timestamp", "TIMESTAMP", "measure_time", "MEASURE_TIME", "tkout_time", "TKOUT_TIME", "updated_at", "UPDATED_AT")
    value_col = _ci_col(cols, "value", "VALUE")
    if not step_col or not item_col:
        return {
            "handled": True,
            "intent": "inline_item_by_step_lookup",
            "answer": "INLINE 데이터에서 step_id 또는 item_id 컬럼을 찾지 못했습니다.",
            "table": {"kind": "inline_item_by_step", "title": "INLINE items by step", "placement": "below", "columns": _table_columns(["message", "columns"]), "rows": [{"message": "missing step_id/item_id", "columns": ", ".join(cols[:50])}], "total": 1},
        }
    aliases = _product_aliases(product_hint)
    filters = []
    if aliases and product_col:
        filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    step_matches = _match_values(_unique_strings(lf, step_col, limit=800), step_terms)
    if step_matches:
        filters.append(pl.col(step_col).cast(_STR, strict=False).is_in(step_matches))
    elif step_terms:
        expr = None
        for term in step_terms:
            piece = pl.col(step_col).cast(_STR, strict=False).str.to_uppercase().str.contains(_upper(term), literal=True)
            expr = piece if expr is None else (expr | piece)
        if expr is not None:
            filters.append(expr)
    for expr in filters:
        lf = lf.filter(expr)
    exprs = [
        pl.col(product_col).cast(_STR, strict=False).alias("product") if product_col else pl.lit(_core_product_name(product_hint)).alias("product"),
        pl.col(step_col).cast(_STR, strict=False).alias("step_id"),
        pl.col(item_col).cast(_STR, strict=False).alias("item_id"),
        pl.col(root_col).cast(_STR, strict=False).alias("root_lot_id") if root_col else pl.lit("").alias("root_lot_id"),
        pl.col(wafer_col).cast(_STR, strict=False).alias("wafer_id") if wafer_col else pl.lit("").alias("wafer_id"),
        pl.col(time_col).cast(_STR, strict=False).alias("latest_time") if time_col else pl.lit("").alias("latest_time"),
    ]
    if value_col:
        exprs.append(pl.col(value_col).cast(pl.Float64, strict=False).alias("value"))
    try:
        scoped = lf.select(exprs).drop_nulls(subset=["step_id", "item_id"])
        aggs = [
            pl.len().alias("row_count"),
            pl.col("root_lot_id").n_unique().alias("root_count"),
            pl.col("wafer_id").n_unique().alias("wafer_count"),
            pl.col("latest_time").max().alias("latest_time"),
        ]
        if value_col:
            aggs.append(pl.col("value").median().alias("median"))
        df = (
            scoped.group_by(["product", "step_id", "item_id"])
            .agg(aggs)
            .sort(["row_count", "latest_time"], descending=[True, True])
            .limit(max(1, min(120, max_rows * 8)))
            .collect()
        )
    except Exception as e:
        return {"handled": True, "intent": "inline_item_by_step_lookup", "answer": f"INLINE item 조회 실패: {e}"}
    rows = df.to_dicts()
    for row in rows:
        row["function_step"] = _function_step_label(row.get("product") or product_hint, row.get("step_id"))
        row["median"] = _round4(row.get("median"))
        row["row_count"] = int(row.get("row_count") or 0)
        row["root_count"] = int(row.get("root_count") or 0)
        row["wafer_count"] = int(row.get("wafer_count") or 0)
    cols_out = ["product", "step_id", "function_step", "item_id", "row_count", "root_count", "wafer_count", "median", "latest_time"]
    if rows:
        answer = f"{', '.join(step_terms)} 기준 INLINE item 후보 {len(rows)}개를 찾았습니다. 대표 item: {rows[0].get('item_id') or '-'}."
    else:
        answer = f"{', '.join(step_terms)} 기준 INLINE item을 찾지 못했습니다."
    return {
        "handled": True,
        "intent": "inline_item_by_step_lookup",
        "action": "query_inline_items_by_step",
        "answer": answer,
        "table": {"kind": "inline_item_by_step", "title": "INLINE items by step", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in rows], "total": len(rows)},
        "filters": {"product": product_hint, "step_terms": step_terms, "step_matches": step_matches},
    }


def _is_ppid_knob_lookup_prompt(prompt: str) -> bool:
    text = str(prompt or "")
    up = _upper(text)
    return ("KNOB" in up or "노브" in text) and "PPID" in up and bool(_step_id_terms_from_prompt(prompt) or _ppid_tokens(prompt))


def _handle_ppid_knob_lookup(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _is_ppid_knob_lookup_prompt(prompt):
        return {"handled": False}
    product_hint = _product_hint(prompt, product)
    step_terms = _step_id_terms_from_prompt(prompt, product=product_hint)
    ppids = _ppid_tokens(prompt)
    if not ppids:
        return {
            "handled": True,
            "intent": "ppid_knob_lookup",
            "action": "clarify_ppid",
            "answer": "PPID 값이 필요합니다. 예: `PRODA step_id AA230400 ppid PPID_STI 이거 무슨 knob이야?`",
            "clarification": {
                "question": "어떤 PPID 기준으로 KNOB를 볼까요?",
                "choices": [],
            },
        }
    fab_rows: list[dict[str, Any]] = []
    fab_files = _fab_files(product_hint)
    if fab_files:
        try:
            lf = _scan_parquet(fab_files)
            cols = _schema_names(lf)
            product_col = _ci_col(cols, "product", "PRODUCT")
            root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
            lot_col = _ci_col(cols, "lot_id", "LOT_ID")
            fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
            wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
            step_col = _ci_col(cols, "step_id", "STEP_ID")
            ppid_col = _ci_col(cols, "ppid", "PPID")
            time_col = _ci_col(cols, "tkout_time", "TKOUT_TIME", "time", "TIME", "timestamp", "TIMESTAMP")
            filters = []
            aliases = _product_aliases(product_hint)
            if aliases and product_col:
                filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
            if ppid_col:
                filters.append(pl.col(ppid_col).cast(_STR, strict=False).str.to_uppercase().is_in([_upper(v) for v in ppids]))
            if step_terms and step_col:
                step_matches = _match_values(_unique_strings(lf, step_col, limit=800), step_terms)
                if step_matches:
                    filters.append(pl.col(step_col).cast(_STR, strict=False).is_in(step_matches))
                else:
                    expr = None
                    for term in step_terms:
                        piece = pl.col(step_col).cast(_STR, strict=False).str.to_uppercase().str.contains(_upper(term), literal=True)
                        expr = piece if expr is None else (expr | piece)
                    if expr is not None:
                        filters.append(expr)
            for expr in filters:
                lf = lf.filter(expr)
            exprs = [
                pl.col(product_col).cast(_STR, strict=False).alias("product") if product_col else pl.lit(_core_product_name(product_hint)).alias("product"),
                pl.col(root_col).cast(_STR, strict=False).alias("root_lot_id") if root_col else (
                    pl.col(lot_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id") if lot_col else (pl.col(fab_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id") if fab_col else pl.lit("").alias("root_lot_id"))
                ),
                pl.col(wafer_col).cast(_STR, strict=False).alias("wafer_id") if wafer_col else pl.lit("").alias("wafer_id"),
                pl.col(step_col).cast(_STR, strict=False).alias("step_id") if step_col else pl.lit("").alias("step_id"),
                pl.col(ppid_col).cast(_STR, strict=False).alias("ppid") if ppid_col else pl.lit("").alias("ppid"),
                pl.col(time_col).cast(_STR, strict=False).alias("time") if time_col else pl.lit("").alias("time"),
            ]
            fab_rows = lf.select(exprs).limit(5000).collect().to_dicts()
        except Exception as e:
            logger.warning("flowi ppid knob FAB scan failed: %s", e)
            fab_rows = []
    roots = sorted({_text(r.get("root_lot_id")) for r in fab_rows if _text(r.get("root_lot_id"))})
    product_from_fab = next((_text(r.get("product")) for r in fab_rows if _text(r.get("product"))), "")
    ml_product = product_hint or product_from_fab
    ml_files = _ml_files(ml_product)
    if not ml_files:
        return {"handled": True, "intent": "ppid_knob_lookup", "answer": "KNOB를 확인할 ML_TABLE parquet을 찾지 못했습니다.", "filters": {"product": ml_product, "step_terms": step_terms, "ppid": ppids}}
    lf = _scan_parquet(ml_files)
    cols = _schema_names(lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    ppid_col = _ci_col(cols, "ppid", "PPID")
    step_col = _ci_col(cols, "step_id", "STEP_ID")
    knob_cols = [c for c in cols if _upper(c).startswith("KNOB_")]
    filters = []
    aliases = _product_aliases(ml_product)
    if aliases and product_col:
        filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    if roots and root_col:
        filters.append(pl.col(root_col).cast(_STR, strict=False).is_in(roots))
    elif ppid_col:
        filters.append(pl.col(ppid_col).cast(_STR, strict=False).str.to_uppercase().is_in([_upper(v) for v in ppids]))
    if step_terms and step_col:
        step_matches = _match_values(_unique_strings(lf, step_col, limit=800), step_terms)
        if step_matches:
            filters.append(pl.col(step_col).cast(_STR, strict=False).is_in(step_matches))
    for expr in filters:
        lf = lf.filter(expr)
    if not knob_cols:
        return {"handled": True, "intent": "ppid_knob_lookup", "answer": "ML_TABLE에서 KNOB_* 컬럼을 찾지 못했습니다."}
    keep = [c for c in (product_col, root_col, wafer_col, ppid_col, step_col) if c] + knob_cols[:120]
    try:
        df = lf.select([pl.col(c).cast(_STR, strict=False).alias(c) for c in keep]).limit(10000).collect()
    except Exception as e:
        return {"handled": True, "intent": "ppid_knob_lookup", "answer": f"PPID→KNOB 조회 실패: {e}"}
    raw_rows = df.to_dicts()
    rows = []
    for knob in knob_cols:
        values = {}
        lot_set = set()
        wafer_set = set()
        for row in raw_rows:
            val = _text(row.get(knob))
            if not val or val.lower() in {"none", "null", "nan"}:
                continue
            values[val] = values.get(val, 0) + 1
            if root_col and _text(row.get(root_col)):
                lot_set.add(_text(row.get(root_col)))
            if wafer_col and _text(row.get(wafer_col)):
                wafer_set.add(_text(row.get(wafer_col)))
        for val, count in sorted(values.items(), key=lambda kv: (-kv[1], kv[0]))[:6]:
            rows.append({
                "product": ml_product or product_from_fab,
                "step_id": ", ".join(step_terms),
                "ppid": ", ".join(ppids),
                "knob": knob,
                "knob_value": val,
                "row_count": count,
                "root_count": len(lot_set),
                "wafer_count": len(wafer_set),
                "example_lots": ", ".join(sorted(lot_set)[:8]),
            })
    rows.sort(key=lambda r: (-int(r.get("row_count") or 0), r.get("knob") or "", r.get("knob_value") or ""))
    cols_out = ["product", "step_id", "ppid", "knob", "knob_value", "row_count", "root_count", "wafer_count", "example_lots"]
    answer = f"{', '.join(ppids)} 기준 KNOB 후보 {len(rows)}개를 찾았습니다." if rows else f"{', '.join(ppids)} 조건에 맞는 KNOB 값을 찾지 못했습니다."
    if fab_rows:
        answer += f" FAB 매칭 lot {len(roots)}개를 ML_TABLE에 연결했습니다."
    return {
        "handled": True,
        "intent": "ppid_knob_lookup",
        "action": "query_knob_by_step_ppid",
        "answer": answer,
        "table": {"kind": "ppid_knob_lookup", "title": "PPID to KNOB lookup", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in rows[:max(1, min(100, max_rows * 8))]], "total": len(rows)},
        "filters": {"product": ml_product, "step_terms": step_terms, "ppid": ppids, "fab_root_count": len(roots)},
    }


def _is_index_form_prompt(prompt: str) -> bool:
    text = str(prompt or "")
    up = _upper(text)
    return ("INDEX" in up or "ADDP" in up or "인덱스" in text) and any(t in text or t in up for t in ("어떻게", "만들", "FORM", "폼", "식", "계산", "설명"))


def _handle_index_form_lookup(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _is_index_form_prompt(prompt):
        return {"handled": False}
    product_hint = _product_hint(prompt, product)
    terms = _flowi_report_terms(prompt, product=product_hint) or ["INDEX", "ADDP"]
    files = _ml_files(product_hint) + _et_files(product_hint) + _inline_files(product_hint)
    rows: list[dict[str, Any]] = []
    for source, source_files in (("ML_TABLE", _ml_files(product_hint)), ("ET", _et_files(product_hint)), ("INLINE", _inline_files(product_hint))):
        if not source_files:
            continue
        try:
            lf = _scan_parquet(source_files[:80])
            cols = _schema_names(lf)
            matches = _column_matches(cols, terms + ["INDEX", "ADDP"], include_knob_when_named=True)
            for col in matches[:12]:
                rec = {"source": source, "column": col, "non_null": "", "unique_count": "", "sample_values": "", "file_count": len(source_files)}
                try:
                    df = lf.select(pl.col(col).cast(_STR, strict=False).drop_nulls().alias(col)).limit(1000).collect()
                    vals = [_text(v) for v in df[col].to_list() if _text(v)]
                    rec["non_null"] = len(vals)
                    rec["unique_count"] = len(set(vals))
                    rec["sample_values"] = ", ".join(list(dict.fromkeys(vals))[:6])
                except Exception:
                    pass
                rows.append(rec)
        except Exception:
            continue
    templates = [
        {"source": "reformatter_template", "column": "scale_abs", "non_null": "", "unique_count": "", "sample_values": "source_col * scale + offset, optional abs"},
        {"source": "reformatter_template", "column": "python_expr", "non_null": "", "unique_count": "", "sample_values": "expr with named inputs, e.g. max({A}, {B})"},
        {"source": "reformatter_template", "column": "shot_formula", "non_null": "", "unique_count": "", "sample_values": "item_map + group_by shot/wafer keys + expr"},
        {"source": "reformatter_template", "column": "shot_agg", "non_null": "", "unique_count": "", "sample_values": "group_by shot keys + agg"},
        {"source": "reformatter_template", "column": "poly2_window", "non_null": "", "unique_count": "", "sample_values": "x_col/y_col + lsl/usl process window"},
    ]
    rows = rows[:max(1, min(80, max_rows * 6))] + templates
    cols_out = ["source", "column", "non_null", "unique_count", "sample_values", "file_count"]
    answer = (
        "INDEX/ADDP form은 실제 생성식 메타데이터가 있으면 reformatter 설정을 우선 확인해야 합니다. "
        "현재는 DB 컬럼 후보와 Flow reformatter에서 지원하는 form template을 함께 정리했습니다."
    )
    return {
        "handled": True,
        "intent": "index_form_lookup",
        "action": "explain_index_addp_form",
        "answer": answer,
        "table": {"kind": "index_form_lookup", "title": "INDEX/ADDP form lookup", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in rows], "total": len(rows)},
        "filters": {"product": product_hint, "terms": terms, "file_count": len(files)},
    }


def _teg_query_terms(prompt: str, product: str = "") -> list[str]:
    blocked = set(_STOP_TOKENS) | {
        "TEG", "SHOT", "WF", "WAFER", "MAP", "RADIUS", "POSITION", "LOCATION",
        "위치", "반경", "가장", "먼", "풀맵", "기준", "보여줘", "어디야",
    }
    blocked.update(_product_aliases(product))
    out: list[str] = []
    seen: set[str] = set()
    for tok in _query_tokens(prompt):
        key = _upper(tok)
        if not key or key in blocked:
            continue
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out[:8]


def _load_flowi_wafer_layout(product: str) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    from routers import waferlayout as waferlayout_router
    layout = waferlayout_router._load_product_wafer_layout(product)
    cfg = waferlayout_router._build_cfg(layout)
    tegs = waferlayout_router._normalize_tegs(layout.get("teg_definitions") or layout.get("tegs") or [])
    return layout, cfg, tegs


def _matching_tegs(tegs: list[dict[str, Any]], terms: list[str]) -> list[dict[str, Any]]:
    if not terms:
        return tegs
    out = []
    for teg in tegs:
        hay = _upper(" ".join([teg.get("id") or "", teg.get("label") or ""]))
        if any(term in hay for term in terms):
            out.append(teg)
    return out or tegs


def _is_teg_radius_prompt(prompt: str) -> bool:
    text = str(prompt or "")
    up = _upper(text)
    return "TEG" in up and any(t in text or t in up for t in ("RADIUS", "반경", "가장 먼", "먼게", "최외곽", "EDGE", "풀맵"))


def _handle_teg_radius_lookup(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _is_teg_radius_prompt(prompt):
        return {"handled": False}
    product_hint = _product_hint(prompt, product)
    if not product_hint:
        return {"handled": True, "intent": "teg_radius_lookup", "answer": "TEG 위치를 계산하려면 product가 필요합니다. 예: `PRODA AAA TEG radius 가장 먼 shot 보여줘`"}
    try:
        from routers import waferlayout as waferlayout_router
        _layout, cfg, tegs = _load_flowi_wafer_layout(product_hint)
        shots = waferlayout_router._collect_shots(cfg)
    except Exception as e:
        return {"handled": True, "intent": "teg_radius_lookup", "answer": f"Wafer layout 로드 실패: {e}"}
    terms = _teg_query_terms(prompt, product_hint)
    selected_tegs = _matching_tegs(tegs, terms)
    if not selected_tegs:
        return {"handled": True, "intent": "teg_radius_lookup", "answer": f"{product_hint} wafer layout에서 TEG 정의를 찾지 못했습니다."}
    rows: list[dict[str, Any]] = []
    for shot in shots:
        for teg in selected_tegs:
            x = float(shot.get("centerX") or 0) + float(teg.get("dx_mm") or 0)
            y = float(shot.get("centerY") or 0) + float(teg.get("dy_mm") or 0)
            try:
                inside = waferlayout_router._in_wafer(x, y, cfg)
            except Exception:
                inside = True
            if not inside:
                continue
            radius = math.hypot(x - float(cfg.get("wfCenterX") or 0), y - float(cfg.get("wfCenterY") or 0))
            rows.append({
                "product": product_hint,
                "teg_id": teg.get("id") or "",
                "teg_label": teg.get("label") or "",
                "shot_x": shot.get("gridShotX"),
                "shot_y": shot.get("gridShotY"),
                "raw_shot_x": shot.get("shotX"),
                "raw_shot_y": shot.get("shotY"),
                "teg_x_mm": round(x, 4),
                "teg_y_mm": round(y, 4),
                "radius_mm": round(radius, 4),
                "full_shot_inside": bool(shot.get("completely_inside")),
            })
    rows.sort(key=lambda r: float(r.get("radius_mm") or 0), reverse=True)
    shown = rows[:max(1, min(80, max_rows * 6))]
    cols_out = ["product", "teg_id", "teg_label", "shot_x", "shot_y", "raw_shot_x", "raw_shot_y", "teg_x_mm", "teg_y_mm", "radius_mm", "full_shot_inside"]
    top = shown[0] if shown else {}
    answer = (
        f"{product_hint} {top.get('teg_label') or 'TEG'} 기준 풀맵 내 가장 먼 위치는 "
        f"Shot({top.get('shot_x')},{top.get('shot_y')}) / radius {top.get('radius_mm')}mm 입니다."
    ) if shown else f"{product_hint}에서 wafer 안에 들어오는 TEG shot을 찾지 못했습니다."
    return {
        "handled": True,
        "intent": "teg_radius_lookup",
        "action": "query_teg_farthest_radius",
        "answer": answer,
        "table": {"kind": "teg_radius_lookup", "title": "TEG farthest radius", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in shown], "total": len(rows)},
        "filters": {"product": product_hint, "teg_terms": terms, "teg_count": len(selected_tegs), "shot_count": len(shots)},
        "feature": "waferlayout",
    }


def _is_teg_position_prompt(prompt: str) -> bool:
    text = str(prompt or "")
    up = _upper(text)
    return "TEG" in up and "SHOT" in up and any(t in text or t in up for t in ("위치", "POSITION", "LOCATION", "어디"))


def _handle_teg_position_lookup(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _is_teg_position_prompt(prompt):
        return {"handled": False}
    product_hint = _product_hint(prompt, product)
    if not product_hint:
        return {"handled": True, "intent": "teg_shot_position_lookup", "answer": "TEG Shot 내 위치를 보려면 product가 필요합니다. 예: `PRODA AAA TEG Shot내 위치 보여줘`"}
    try:
        layout, cfg, tegs = _load_flowi_wafer_layout(product_hint)
    except Exception as e:
        return {"handled": True, "intent": "teg_shot_position_lookup", "answer": f"Wafer layout 로드 실패: {e}"}
    terms = _teg_query_terms(prompt, product_hint)
    selected_tegs = _matching_tegs(tegs, terms)
    rows = []
    for teg in selected_tegs:
        rows.append({
            "product": product_hint,
            "teg_id": teg.get("id") or "",
            "teg_label": teg.get("label") or "",
            "shot_local_x_mm": round(float(teg.get("dx_mm") or 0), 4),
            "shot_local_y_mm": round(float(teg.get("dy_mm") or 0), 4),
            "teg_size_x_mm": round(float(cfg.get("tegSizeX") or layout.get("teg_size_w_mm") or 0), 4),
            "teg_size_y_mm": round(float(cfg.get("tegSizeY") or layout.get("teg_size_h_mm") or 0), 4),
            "shot_size_x_mm": round(float(cfg.get("shotSizeX") or 0), 4),
            "shot_size_y_mm": round(float(cfg.get("shotSizeY") or 0), 4),
            "origin": "shot_center + dx/dy",
        })
    cols_out = ["product", "teg_id", "teg_label", "shot_local_x_mm", "shot_local_y_mm", "teg_size_x_mm", "teg_size_y_mm", "shot_size_x_mm", "shot_size_y_mm", "origin"]
    answer = f"{product_hint} Shot 내 TEG 위치 {len(rows)}개를 정리했습니다." if rows else f"{product_hint} wafer layout에서 TEG 정의를 찾지 못했습니다."
    return {
        "handled": True,
        "intent": "teg_shot_position_lookup",
        "action": "query_teg_shot_position",
        "answer": answer,
        "table": {"kind": "teg_shot_position", "title": "TEG position inside shot", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in rows[:max(1, min(80, max_rows * 6))]], "total": len(rows)},
        "filters": {"product": product_hint, "teg_terms": terms},
        "feature": "waferlayout",
    }


def _is_wafer_map_similarity_prompt(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    return any(t in low or t in text for t in ("wf map", "wafer map", "웨이퍼맵", "맵", "map")) and any(t in low or t in text for t in ("비슷", "similar", "유사", "닮"))


def _pearson_corr(a: list[float], b: list[float]) -> float | None:
    if len(a) != len(b) or len(a) < 3:
        return None
    ma = sum(a) / len(a)
    mb = sum(b) / len(b)
    da = [x - ma for x in a]
    db = [y - mb for y in b]
    va = sum(x * x for x in da)
    vb = sum(y * y for y in db)
    if va <= 0 or vb <= 0:
        return None
    return sum(x * y for x, y in zip(da, db)) / math.sqrt(va * vb)


def _beol_hint(text: str) -> bool:
    up = _upper(text)
    return any(term in up for term in ("BEOL", "M0", "M1", "M2", "M3", "VIA", "CA", "CT", "METAL", "IMD", "ILD"))


def _handle_wafer_map_similarity(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _is_wafer_map_similarity_prompt(prompt):
        return {"handled": False}
    product_hint = _product_hint(prompt, product)
    terms = _flowi_report_terms(prompt, product=product_hint)
    beol_only = "BEOL" in _upper(prompt) or "beol" in str(prompt or "").lower()
    frames: list[dict[str, Any]] = []
    inline_needs_coord_map = False
    for source, files in (("ET", _et_files(product_hint)), ("INLINE", _inline_files(product_hint))):
        if not files:
            continue
        try:
            lf = _scan_parquet(files)
            cols = _schema_names(lf)
            product_col = _ci_col(cols, "product", "PRODUCT")
            item_col = _ci_col(cols, "item_id", "ITEM_ID", "subitem_id", "SUBITEM_ID")
            value_col = _ci_col(cols, "value", "VALUE")
            shot_x_col = _ci_col(cols, "shot_x", "SHOT_X", "x", "X")
            shot_y_col = _ci_col(cols, "shot_y", "SHOT_Y", "y", "Y")
            step_col = _ci_col(cols, "step_id", "STEP_ID")
            if not (item_col and value_col and shot_x_col and shot_y_col):
                if source == "INLINE" and item_col and value_col and _ci_col(cols, "subitem_id", "SUBITEM_ID"):
                    inline_needs_coord_map = True
                continue
            aliases = _product_aliases(product_hint)
            if aliases and product_col:
                lf = lf.filter(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
            df = lf.select([
                pl.lit(source).alias("source"),
                pl.col(product_col).cast(_STR, strict=False).alias("product") if product_col else pl.lit(_core_product_name(product_hint)).alias("product"),
                pl.col(item_col).cast(_STR, strict=False).alias("item_id"),
                pl.col(step_col).cast(_STR, strict=False).alias("step_id") if step_col else pl.lit("").alias("step_id"),
                pl.col(shot_x_col).cast(_STR, strict=False).alias("shot_x"),
                pl.col(shot_y_col).cast(_STR, strict=False).alias("shot_y"),
                pl.col(value_col).cast(pl.Float64, strict=False).alias("value"),
            ]).drop_nulls(subset=["item_id", "shot_x", "shot_y", "value"]).limit(200000).collect()
            frames.extend(df.to_dicts())
        except Exception as e:
            logger.warning("flowi wafer map scan failed source=%s: %s", source, e)
    if not frames:
        answer = "shot_x/shot_y/value/item_id 형태의 ET wafer map 데이터를 찾지 못했습니다."
        if inline_needs_coord_map:
            answer += " INLINE raw DB는 subitem_id만 있어서 inline_item_map.csv와 inline_subitem_pos.csv 매핑 후에 shot map similarity를 계산할 수 있습니다."
        return {"handled": True, "intent": "wafer_map_similarity", "answer": answer}
    item_counts: dict[tuple[str, str, str], int] = {}
    for row in frames:
        key = (_text(row.get("source")), _text(row.get("item_id")), _text(row.get("step_id")))
        item_counts[key] = item_counts.get(key, 0) + 1
    candidate_items = [
        {"source": src, "item_id": item, "step_id": step, "row_count": count, "beol_hint": _beol_hint(" ".join([item, step]))}
        for (src, item, step), count in item_counts.items()
    ]
    candidate_items.sort(key=lambda r: (not r["beol_hint"] if beol_only else False, -int(r["row_count"]), r["item_id"]))
    target_terms = [
        term for term in terms
        if term not in {"BEOL", "FEOL", "MOL", "MAP", "WF", "WAFER", "SIMILAR"}
        and not _beol_hint(term)
    ]
    target_matches = []
    for item in candidate_items:
        hay = _upper(" ".join([item.get("item_id") or "", item.get("step_id") or "", item.get("source") or ""]))
        if target_terms and any(term in hay for term in target_terms):
            target_matches.append(item)
    if not target_matches:
        rows = candidate_items[:max(1, min(40, max_rows * 4))]
        return {
            "handled": True,
            "intent": "wafer_map_similarity",
            "action": "clarify_target_map_item",
            "answer": "비교 기준이 될 target item을 특정하지 못했습니다. 아래 후보 중 item을 포함해서 다시 질문해주세요.",
            "clarification": {
                "question": "어떤 item의 WF map과 비교할까요?",
                "choices": [
                    {
                        "id": f"item_{i}",
                        "label": str(i + 1),
                        "title": f"{row['source']} {row['item_id']}",
                        "recommended": i == 0,
                        "description": f"step={row.get('step_id') or '-'}, rows={row.get('row_count')}",
                        "prompt": f"{product_hint} {row['item_id']} wf map이랑 가장 비슷한 map 찾아줘",
                    }
                    for i, row in enumerate(rows[:4])
                ],
            },
            "table": {"kind": "wafer_map_item_candidates", "title": "WF map item candidates", "placement": "below", "columns": _table_columns(["source", "item_id", "step_id", "row_count", "beol_hint"]), "rows": rows, "total": len(candidate_items)},
            "filters": {"product": product_hint, "terms": terms, "beol_only": beol_only},
        }
    target = target_matches[0]
    def map_for(src: str, item_id: str, step_id: str = "") -> dict[tuple[str, str], float]:
        vals: dict[tuple[str, str], list[float]] = {}
        for row in frames:
            if _text(row.get("source")) != src or _text(row.get("item_id")) != item_id:
                continue
            if step_id and _text(row.get("step_id")) != step_id:
                continue
            key = (_text(row.get("shot_x")), _text(row.get("shot_y")))
            vals.setdefault(key, []).append(float(row.get("value")))
        return {k: sum(v) / len(v) for k, v in vals.items() if v}
    target_map = map_for(target["source"], target["item_id"], target.get("step_id") or "")
    rows: list[dict[str, Any]] = []
    for cand in candidate_items:
        if cand["source"] == target["source"] and cand["item_id"] == target["item_id"] and cand.get("step_id") == target.get("step_id"):
            continue
        cand_map = map_for(cand["source"], cand["item_id"], cand.get("step_id") or "")
        common = sorted(set(target_map) & set(cand_map))
        if len(common) < 3:
            continue
        corr = _pearson_corr([target_map[k] for k in common], [cand_map[k] for k in common])
        if corr is None:
            continue
        rows.append({
            "target_source": target["source"],
            "target_item": target["item_id"],
            "candidate_source": cand["source"],
            "candidate_item": cand["item_id"],
            "candidate_step": cand.get("step_id") or "",
            "similarity": round(float(corr), 4),
            "abs_similarity": round(abs(float(corr)), 4),
            "common_shots": len(common),
            "beol_hint": bool(cand.get("beol_hint")),
        })
    rows.sort(key=lambda r: (not r["beol_hint"] if beol_only else False, -float(r.get("abs_similarity") or 0), -int(r.get("common_shots") or 0)))
    shown = rows[:max(1, min(80, max_rows * 6))]
    cols_out = ["target_source", "target_item", "candidate_source", "candidate_item", "candidate_step", "similarity", "abs_similarity", "common_shots", "beol_hint"]
    top = shown[0] if shown else {}
    answer = (
        f"{target['source']} {target['item_id']} WF map과 가장 유사한 후보는 "
        f"{top.get('candidate_source')} {top.get('candidate_item')}입니다. similarity={top.get('similarity')}, common_shots={top.get('common_shots')}."
    ) if shown else f"{target['item_id']}와 비교 가능한 common shot map 후보를 찾지 못했습니다."
    if beol_only:
        answer += " BEOL hint가 있는 후보를 우선 정렬했습니다."
    return {
        "handled": True,
        "intent": "wafer_map_similarity",
        "action": "query_similar_wafer_maps",
        "answer": answer,
        "table": {"kind": "wafer_map_similarity", "title": "Similar WF maps", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in shown], "total": len(rows)},
        "filters": {"product": product_hint, "terms": terms, "target": target, "beol_only": beol_only},
        "feature": "dashboard",
    }


def _is_split_fab_lot_basis_prompt(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    return ("fab_lot_id" in low or "fab lot" in low) and ("스플릿" in text or "split" in low) and any(t in text or t in low for t in ("언제", "업데이트", "기준", "fresh", "update"))


def _handle_split_fab_lot_basis(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _is_split_fab_lot_basis_prompt(prompt):
        return {"handled": False}
    product_hint = _product_hint(prompt, product)
    rows: list[dict[str, Any]] = []
    if product_hint:
        try:
            from routers import splittable as splittable_router
            current = splittable_router._match_cache_current(product_hint)
            interval = splittable_router._match_cache_refresh_minutes()
            if current:
                meta = current.get("meta") or {}
                rows.append({
                    "product": current.get("product") or product_hint,
                    "basis": "SplitTable match cache",
                    "built_at": meta.get("built_at") or "",
                    "interval_minutes": interval,
                    "fab_source": current.get("fab_source") or "",
                    "fab_col": meta.get("fab_col") or "fab_lot_id",
                    "ts_col": meta.get("ts_col") or "",
                    "join_keys": ", ".join(meta.get("join_keys") or []),
                    "row_count": int(meta.get("row_count") or 0),
                    "path": str(current.get("path") or ""),
                    "status": "cache_current",
                })
            else:
                meta = splittable_router._resolve_override_meta_light(product_hint)
                rows.append({
                    "product": product_hint,
                    "basis": "SplitTable FAB override metadata",
                    "built_at": "",
                    "interval_minutes": interval,
                    "fab_source": meta.get("fab_source") or "",
                    "fab_col": meta.get("fab_col") or "fab_lot_id",
                    "ts_col": meta.get("ts_col") or "",
                    "join_keys": ", ".join(meta.get("join_keys") or []),
                    "row_count": "",
                    "path": "",
                    "status": meta.get("error") or "cache_missing_or_stale",
                })
        except Exception as e:
            rows.append({"product": product_hint, "basis": "SplitTable", "built_at": "", "interval_minutes": "", "fab_source": "", "fab_col": "fab_lot_id", "ts_col": "", "join_keys": "", "row_count": "", "path": "", "status": f"lookup_failed: {e}"})
    else:
        rows.append({
            "product": "",
            "basis": "SplitTable match cache",
            "built_at": "",
            "interval_minutes": "",
            "fab_source": "product 필요",
            "fab_col": "fab_lot_id",
            "ts_col": "tkout_time/time 계열이 있으면 최신도 기준",
            "join_keys": "root_lot_id, wafer_id 등 product 설정",
            "row_count": "",
            "path": "",
            "status": "product_required_for_exact_cache_status",
        })
    cols_out = ["product", "basis", "built_at", "interval_minutes", "fab_source", "fab_col", "ts_col", "join_keys", "row_count", "path", "status"]
    row = rows[0]
    answer = (
        f"SplitTable fab_lot_id는 FAB source를 ML_TABLE join key에 맞춰 붙인 match cache 기준입니다. "
        f"{row.get('product') or 'product 미지정'} cache built_at={row.get('built_at') or '-'}, "
        f"fab_col={row.get('fab_col') or 'fab_lot_id'}, ts_col={row.get('ts_col') or 'last/원천 순서'}."
    )
    return {
        "handled": True,
        "intent": "splittable_fab_lot_basis",
        "action": "explain_splittable_fab_lot_basis",
        "answer": answer,
        "table": {"kind": "splittable_fab_lot_basis", "title": "SplitTable fab_lot_id basis", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in rows[:max(1, max_rows)]], "total": len(rows)},
        "feature": "splittable",
    }


def _is_fab_corun_prompt(prompt: str) -> bool:
    text = str(prompt or "")
    return bool(_lot_tokens(prompt)) and any(t in text for t in ("같이 진행", "같이진행", "동시", "같은 시기", "함께")) and any(t in text for t in ("기준", "step", "공정", "MOL", "FEOL", "BEOL"))


def _handle_fab_corun_lots(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _is_fab_corun_prompt(prompt):
        return {"handled": False}
    lots = _lot_tokens(prompt)
    product_hint, candidate_tool = _product_or_candidate_tool(prompt, product, lots, kinds=("FAB",), intent="fab_corun_lots")
    if candidate_tool:
        return candidate_tool
    files = _fab_files(product_hint)
    if not files:
        return {"handled": True, "intent": "fab_corun_lots", "answer": "FAB parquet을 찾지 못했습니다."}
    lf = _scan_parquet(files)
    cols = _schema_names(lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    step_col = _ci_col(cols, "step_id", "STEP_ID")
    time_col = _ci_col(cols, "tkout_time", "TKOUT_TIME", "time", "TIME", "timestamp", "TIMESTAMP")
    if not step_col or not time_col or not (root_col or lot_col or fab_col):
        return {"handled": True, "intent": "fab_corun_lots", "answer": "FAB 데이터에서 step/time/lot 컬럼을 찾지 못했습니다."}
    aliases = _product_aliases(product_hint)
    if aliases and product_col:
        lf = lf.filter(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    exprs = [
        pl.col(product_col).cast(_STR, strict=False).alias("product") if product_col else pl.lit(_core_product_name(product_hint)).alias("product"),
        pl.col(root_col).cast(_STR, strict=False).alias("root_lot_id") if root_col else (
            pl.col(lot_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id") if lot_col else pl.col(fab_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id")
        ),
        pl.col(lot_col).cast(_STR, strict=False).alias("lot_id") if lot_col else pl.lit("").alias("lot_id"),
        pl.col(fab_col).cast(_STR, strict=False).alias("fab_lot_id") if fab_col else pl.lit("").alias("fab_lot_id"),
        pl.col(wafer_col).cast(_STR, strict=False).alias("wafer_id") if wafer_col else pl.lit("").alias("wafer_id"),
        pl.col(step_col).cast(_STR, strict=False).alias("step_id"),
        pl.col(time_col).cast(_STR, strict=False).alias("time"),
    ]
    try:
        all_rows = lf.select(exprs).drop_nulls(subset=["step_id", "time"]).limit(150000).collect().to_dicts()
    except Exception as e:
        return {"handled": True, "intent": "fab_corun_lots", "answer": f"FAB 같이 진행 lot 조회 실패: {e}"}
    lot_set = {_upper(v) for v in lots}
    target_rows = [
        r for r in all_rows
        if any(tok in _upper(" ".join([r.get("root_lot_id") or "", r.get("lot_id") or "", r.get("fab_lot_id") or ""])) for tok in lot_set)
    ]
    terms = _step_query_terms(prompt, lots, product_hint)
    if terms:
        filtered = []
        for row in target_rows:
            func = _function_step_label(row.get("product") or product_hint, row.get("step_id"))
            hay = _upper(" ".join([row.get("step_id") or "", func]))
            if any(term in hay for term in terms):
                filtered.append(row)
        target_rows = filtered or target_rows
    if not target_rows:
        return {"handled": True, "intent": "fab_corun_lots", "answer": f"{', '.join(lots)} 기준 FAB step row를 찾지 못했습니다."}
    target_rows.sort(key=lambda r: _parse_flowi_datetime(r.get("time")) or datetime.min, reverse=True)
    target_steps = {_text(r.get("step_id")) for r in target_rows[:20] if _text(r.get("step_id"))}
    target_times = [(r, _parse_flowi_datetime(r.get("time"))) for r in target_rows if _text(r.get("step_id")) in target_steps and _parse_flowi_datetime(r.get("time"))]
    rows: list[dict[str, Any]] = []
    target_roots = {_text(r.get("root_lot_id")) for r in target_rows}
    for row in all_rows:
        root = _text(row.get("root_lot_id"))
        if not root or root in target_roots or _text(row.get("step_id")) not in target_steps:
            continue
        dt = _parse_flowi_datetime(row.get("time"))
        if not dt:
            continue
        best = None
        for target_row, target_dt in target_times:
            if _text(target_row.get("step_id")) != _text(row.get("step_id")) or not target_dt:
                continue
            delta = abs((dt - target_dt).total_seconds()) / 3600.0
            if best is None or delta < best[0]:
                best = (delta, target_row, target_dt)
        if best is None or best[0] > 72:
            continue
        rows.append({
            "product": row.get("product") or product_hint,
            "target_lot": ", ".join(lots),
            "peer_root_lot_id": root,
            "peer_lot_id": row.get("lot_id") or "",
            "peer_fab_lot_id": row.get("fab_lot_id") or "",
            "step_id": row.get("step_id") or "",
            "function_step": _function_step_label(row.get("product") or product_hint, row.get("step_id")),
            "target_time": best[1].get("time") or "",
            "peer_time": row.get("time") or "",
            "delta_hours": round(best[0], 3),
        })
    rows.sort(key=lambda r: (float(r.get("delta_hours") or 9999), r.get("peer_root_lot_id") or ""))
    cols_out = ["product", "target_lot", "peer_root_lot_id", "peer_lot_id", "peer_fab_lot_id", "step_id", "function_step", "target_time", "peer_time", "delta_hours"]
    answer = f"{', '.join(lots)}와 같은 step/function 기준 72시간 내 같이 진행한 후보 lot {len(rows)}개를 찾았습니다." if rows else "같은 step에서 72시간 내 같이 진행한 후보 lot을 찾지 못했습니다."
    return {
        "handled": True,
        "intent": "fab_corun_lots",
        "action": "query_fab_corun_lots",
        "answer": answer,
        "table": {"kind": "fab_corun_lots", "title": "FAB co-run lots", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in rows[:max(1, min(120, max_rows * 8))]], "total": len(rows)},
        "filters": {"product": product_hint, "lots": lots, "terms": terms, "target_steps": sorted(target_steps)},
    }


def _is_knob_clean_or_interference_prompt(prompt: str) -> bool:
    up = _upper(prompt)
    text = str(prompt or "")
    if "KNOB" not in up and "노브" not in text:
        return False
    return any(t in text for t in ("클린", "clean", "다른", "신경", "적용", "간섭", "같이"))


def _handle_knob_clean_interference(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _is_knob_clean_or_interference_prompt(prompt):
        return {"handled": False}
    product_hint = _product_hint(prompt, product)
    files = _ml_files(product_hint)
    if not files:
        return {"handled": True, "intent": "knob_clean_interference", "answer": "ML_TABLE parquet을 찾지 못했습니다."}
    lf = _scan_parquet(files)
    cols = _schema_names(lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    knob_cols = [c for c in cols if _upper(c).startswith("KNOB_")]
    if not root_col or not knob_cols:
        return {"handled": True, "intent": "knob_clean_interference", "answer": "ML_TABLE에서 root_lot_id 또는 KNOB_* 컬럼을 찾지 못했습니다."}
    aliases = _product_aliases(product_hint)
    if aliases and product_col:
        lf = lf.filter(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    selected_knob, knob_candidates = _select_knob_column(lf, knob_cols, prompt, _lot_tokens(prompt), [])
    if not selected_knob:
        return {"handled": True, "intent": "knob_clean_interference", "answer": "요청과 맞는 KNOB 컬럼을 찾지 못했습니다."}
    keep = [c for c in (product_col, root_col, wafer_col) if c] + knob_cols[:160]
    try:
        df = lf.select([pl.col(c).cast(_STR, strict=False).alias(c) for c in keep]).limit(20000).collect()
    except Exception as e:
        return {"handled": True, "intent": "knob_clean_interference", "answer": f"KNOB clean/interference 조회 실패: {e}"}
    want_clean = "클린" in prompt or "clean" in str(prompt or "").lower()
    grouped: dict[str, dict[str, Any]] = {}
    for row in df.to_dicts():
        selected_value = _text(row.get(selected_knob))
        if not selected_value or selected_value.lower() in {"none", "null", "nan"}:
            continue
        root = _text(row.get(root_col))
        if not root:
            continue
        rec = grouped.setdefault(root, {
            "product": _text(row.get(product_col)) or product_hint,
            "root_lot_id": root,
            "selected_knob": selected_knob,
            "selected_values": set(),
            "wafer_count": 0,
            "wafers": set(),
            "other_knobs": {},
        })
        rec["selected_values"].add(selected_value)
        rec["wafer_count"] += 1
        wafer = _text(row.get(wafer_col))
        if wafer:
            rec["wafers"].add(wafer)
        for knob in knob_cols:
            if knob == selected_knob:
                continue
            val = _text(row.get(knob))
            if val and val.lower() not in {"none", "null", "nan"}:
                rec["other_knobs"][f"{knob}={val}"] = rec["other_knobs"].get(f"{knob}={val}", 0) + 1
    rows = []
    for rec in grouped.values():
        other = sorted(rec["other_knobs"].items(), key=lambda kv: (-kv[1], kv[0]))
        is_clean = len(other) == 0
        if want_clean and not is_clean:
            continue
        if not want_clean and is_clean:
            continue
        rows.append({
            "product": rec["product"],
            "root_lot_id": rec["root_lot_id"],
            "selected_knob": rec["selected_knob"],
            "selected_values": ", ".join(sorted(rec["selected_values"])),
            "wafer_count": rec["wafer_count"],
            "wafers": ", ".join(sorted(rec["wafers"], key=lambda x: (len(x), x))[:12]),
            "clean_split": is_clean,
            "other_knob_count": len(other),
            "other_knobs": ", ".join(f"{k}({v})" for k, v in other[:8]),
        })
    rows.sort(key=lambda r: (int(r.get("other_knob_count") or 0), -int(r.get("wafer_count") or 0), r.get("root_lot_id") or "") if want_clean else (-int(r.get("other_knob_count") or 0), -int(r.get("wafer_count") or 0), r.get("root_lot_id") or ""))
    cols_out = ["product", "root_lot_id", "selected_knob", "selected_values", "wafer_count", "wafers", "clean_split", "other_knob_count", "other_knobs"]
    if want_clean:
        answer = f"{selected_knob} 기준 다른 KNOB가 같이 잡히지 않은 clean split lot {len(rows)}개를 찾았습니다."
        intent = "knob_clean_split"
        action = "query_knob_clean_split_lots"
    else:
        answer = f"{selected_knob} 분석 시 같이 적용된 다른 KNOB 후보가 있는 lot {len(rows)}개를 찾았습니다."
        intent = "knob_interference_lookup"
        action = "query_knob_interference"
    return {
        "handled": True,
        "intent": intent,
        "action": action,
        "answer": answer,
        "table": {"kind": intent, "title": "KNOB clean/interference", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in rows[:max(1, min(120, max_rows * 8))]], "total": len(rows)},
        "filters": {"product": product_hint, "selected_knob": selected_knob, "knob_candidates": knob_candidates[:12]},
        "feature": "splittable",
    }


def _is_lot_anomaly_prompt(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    return bool(_lot_tokens(prompt)) and any(t in low or t in text for t in ("특이사항", "outlier", "아웃라이어", "trend", "상하향", "상향", "하향", "이상"))


def _mean_std(values: list[float]) -> tuple[float | None, float | None]:
    clean = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not clean:
        return None, None
    mean = sum(clean) / len(clean)
    if len(clean) < 2:
        return mean, None
    var = sum((v - mean) ** 2 for v in clean) / (len(clean) - 1)
    return mean, math.sqrt(max(0.0, var))


def _handle_lot_anomaly_summary(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    if not _is_lot_anomaly_prompt(prompt):
        return {"handled": False}
    lots = _lot_tokens(prompt)
    product_hint, candidate_tool = _et_product_or_candidate(prompt, product, lots, "lot_anomaly_summary")
    if candidate_tool:
        return candidate_tool
    rows_raw: list[dict[str, Any]] = []
    for source, files in (("ET", _et_files(product_hint)), ("INLINE", _inline_files(product_hint))):
        if not files:
            continue
        try:
            lf = _scan_parquet(files)
            cols = _schema_names(lf)
            product_col = _ci_col(cols, "product", "PRODUCT")
            root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
            lot_col = _ci_col(cols, "lot_id", "LOT_ID")
            fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
            wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
            step_col = _ci_col(cols, "step_id", "STEP_ID")
            item_col = _ci_col(cols, "item_id", "ITEM_ID")
            value_col = _ci_col(cols, "value", "VALUE")
            time_col = _ci_col(cols, "tkout_time", "TKOUT_TIME", "time", "TIME", "timestamp", "TIMESTAMP", "measure_time", "MEASURE_TIME")
            if not (item_col and value_col and (root_col or lot_col or fab_col)):
                continue
            aliases = _product_aliases(product_hint)
            if aliases and product_col:
                lf = lf.filter(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
            df = lf.select([
                pl.lit(source).alias("source"),
                pl.col(product_col).cast(_STR, strict=False).alias("product") if product_col else pl.lit(_core_product_name(product_hint)).alias("product"),
                pl.col(root_col).cast(_STR, strict=False).alias("root_lot_id") if root_col else (
                    pl.col(lot_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id") if lot_col else pl.col(fab_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id")
                ),
                pl.col(wafer_col).cast(_STR, strict=False).alias("wafer_id") if wafer_col else pl.lit("").alias("wafer_id"),
                pl.col(step_col).cast(_STR, strict=False).alias("step_id") if step_col else pl.lit("").alias("step_id"),
                pl.col(item_col).cast(_STR, strict=False).alias("item_id"),
                pl.col(value_col).cast(pl.Float64, strict=False).alias("value"),
                pl.col(time_col).cast(_STR, strict=False).alias("time") if time_col else pl.lit("").alias("time"),
            ]).drop_nulls(subset=["root_lot_id", "item_id", "value"]).limit(250000).collect()
            rows_raw.extend(df.to_dicts())
        except Exception as e:
            logger.warning("flowi lot anomaly scan failed source=%s: %s", source, e)
    if not rows_raw:
        return {"handled": True, "intent": "lot_anomaly_summary", "answer": "ET/INLINE에서 lot anomaly를 계산할 item/value 데이터를 찾지 못했습니다."}
    lot_set = {_upper(v) for v in lots}
    target_rows = [r for r in rows_raw if _upper(r.get("root_lot_id")) in lot_set or any(tok in _upper(r.get("root_lot_id")) for tok in lot_set)]
    if not target_rows:
        return {"handled": True, "intent": "lot_anomaly_summary", "answer": f"{', '.join(lots)}에 해당하는 ET/INLINE row를 찾지 못했습니다."}
    target_groups: dict[tuple[str, str, str], list[float]] = {}
    baseline_groups: dict[tuple[str, str, str], list[float]] = {}
    latest_time: dict[tuple[str, str, str], str] = {}
    for row in rows_raw:
        key = (_text(row.get("source")), _text(row.get("step_id")), _text(row.get("item_id")))
        val = row.get("value")
        if val is None:
            continue
        is_target = _upper(row.get("root_lot_id")) in lot_set or any(tok in _upper(row.get("root_lot_id")) for tok in lot_set)
        if is_target:
            target_groups.setdefault(key, []).append(float(val))
            latest_time[key] = max(latest_time.get(key, ""), _text(row.get("time")))
        else:
            baseline_groups.setdefault(key, []).append(float(val))
    rows: list[dict[str, Any]] = []
    for key, vals in target_groups.items():
        src, step, item = key
        target_mean, _target_std = _mean_std(vals)
        base_mean, base_std = _mean_std(baseline_groups.get(key) or [])
        if target_mean is None:
            continue
        z = None
        if base_mean is not None and base_std and base_std > 0:
            z = (target_mean - base_mean) / base_std
        direction = "up" if base_mean is not None and target_mean > base_mean else ("down" if base_mean is not None and target_mean < base_mean else "")
        severity = "outlier" if z is not None and abs(z) >= 3 else ("shift" if z is not None and abs(z) >= 2 else ("watch" if z is not None and abs(z) >= 1 else "normal"))
        rows.append({
            "product": product_hint or _text(target_rows[0].get("product")),
            "root_lot_id": ", ".join(lots),
            "source": src,
            "step_id": step,
            "function_step": _function_step_label(product_hint or _text(target_rows[0].get("product")), step),
            "item_id": item,
            "target_mean": _round4(target_mean),
            "baseline_mean": _round4(base_mean),
            "baseline_std": _round4(base_std),
            "z_score": _round4(z),
            "direction": direction,
            "severity": severity,
            "target_n": len(vals),
            "baseline_n": len(baseline_groups.get(key) or []),
            "latest_time": latest_time.get(key, ""),
        })
    rows.sort(key=lambda r: ({"outlier": 0, "shift": 1, "watch": 2, "normal": 3}.get(r.get("severity"), 9), -abs(float(r.get("z_score") or 0)), r.get("item_id") or ""))
    cols_out = ["product", "root_lot_id", "source", "step_id", "function_step", "item_id", "target_mean", "baseline_mean", "baseline_std", "z_score", "direction", "severity", "target_n", "baseline_n", "latest_time"]
    top = rows[0] if rows else {}
    answer = (
        f"{', '.join(lots)} ET/INLINE trend 대비 특이 후보 {len(rows)}개를 계산했습니다. "
        f"Top: {top.get('source') or '-'} {top.get('item_id') or '-'} {top.get('direction') or ''} z={top.get('z_score') or '-'} ({top.get('severity') or '-'})."
    ) if rows else "baseline과 비교 가능한 특이 후보를 찾지 못했습니다."
    return {
        "handled": True,
        "intent": "lot_anomaly_summary",
        "action": "query_lot_anomaly_summary",
        "answer": answer,
        "table": {"kind": "lot_anomaly_summary", "title": "Lot anomaly summary", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in rows[:max(1, min(120, max_rows * 8))]], "total": len(rows)},
        "filters": {"product": product_hint, "lots": lots},
        "feature": "dashboard",
    }


def _handle_et_query(prompt: str, product: str, max_rows: int) -> dict:
    if "ET" not in _upper(prompt):
        return {"handled": False}
    files = _et_files(product)
    if not files:
        return {
            "handled": True,
            "intent": "et_wafer_median",
            "answer": "ET 원천 parquet을 찾지 못했습니다. DB root 아래 `*ET*` 폴더를 확인해주세요.",
            "rows": [],
        }
    lf = _scan_parquet(files)
    cols = _schema_names(lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    step_col = _ci_col(cols, "step_id", "STEP_ID")
    item_col = _ci_col(cols, "item_id", "ITEM_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID")
    value_col = _ci_col(cols, "value", "VALUE")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID", "fab_lot_id", "FAB_LOT_ID")
    if not (step_col and item_col and wafer_col and value_col):
        return {
            "handled": True,
            "intent": "et_wafer_median",
            "answer": "ET 데이터 컬럼(step_id/item_id/wafer_id/value)을 찾지 못했습니다.",
            "rows": [],
        }

    step_vals = _unique_strings(lf, step_col)
    item_vals = _unique_strings(lf, item_col)
    step_matches = _match_values(step_vals, _step_tokens(prompt))
    item_matches = _match_values(item_vals, _query_tokens(prompt))
    lot_matches = _lot_tokens(prompt)
    aliases = _product_aliases(product)

    filters = []
    if aliases and product_col:
        filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    if step_matches:
        filters.append(pl.col(step_col).cast(_STR, strict=False).is_in(step_matches))
    if item_matches:
        filters.append(pl.col(item_col).cast(_STR, strict=False).is_in(item_matches))
    if lot_matches:
        lot_cols = [c for c in (root_col, lot_col, _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")) if c]
        lot_expr = _or_contains(lot_cols, lot_matches)
        if lot_expr is not None:
            filters.append(lot_expr)
    for expr in filters:
        lf = lf.filter(expr)

    group_cols = [c for c in (product_col, step_col, item_col, wafer_col) if c]
    try:
        out = (
            lf.group_by(group_cols)
            .agg([
                pl.col(value_col).cast(pl.Float64, strict=False).median().alias("median"),
                pl.col(value_col).cast(pl.Float64, strict=False).mean().alias("mean"),
                pl.col(value_col).cast(pl.Float64, strict=False).count().alias("count"),
            ])
            .sort(group_cols)
            .limit(max(1, min(120, max_rows * 6)))
            .collect()
        )
    except Exception as e:
        logger.warning("flowi ET query failed: %s", e)
        raise HTTPException(400, f"ET 집계 실패: {e}")

    rows = out.rename({
        product_col: "product",
        step_col: "step_id",
        item_col: "item_id",
        wafer_col: "wafer_id",
    }).to_dicts() if out.height else []
    rows = _sort_wafer_rows(rows)
    for row in rows:
        row["median"] = _round4(row.get("median"))
        row["mean"] = _round4(row.get("mean"))
        row["count"] = int(row.get("count") or 0)

    if not rows:
        hints = []
        if _step_tokens(prompt) and not step_matches:
            hints.append(f"step 후보: {', '.join(step_vals[:8])}")
        if _query_tokens(prompt) and not item_matches:
            hints.append(f"item 후보: {', '.join(item_vals[:8])}")
        hint_txt = " / ".join(hints) if hints else "필터 조건에 맞는 ET row가 없습니다."
        return {
            "handled": True,
            "intent": "et_wafer_median",
            "answer": hint_txt,
            "rows": [],
            "filters": {"step": step_matches, "item": item_matches, "lot": lot_matches, "product": sorted(aliases)},
        }

    preview = rows[:max_rows]
    lines = [
        f"- WF {r.get('wafer_id')}: median {r.get('median')} (mean {r.get('mean')}, n={r.get('count')})"
        for r in preview
    ]
    scope = []
    if step_matches:
        scope.append("step=" + ",".join(step_matches))
    if item_matches:
        scope.append("item=" + ",".join(item_matches))
    if lot_matches:
        scope.append("lot~" + ",".join(lot_matches))
    if aliases:
        scope.append("product=" + ",".join(sorted(aliases)[:4]))
    answer = "ET value wafer별 median입니다"
    if scope:
        answer += " (" + " / ".join(scope) + ")"
    answer += f". 총 {len(rows)}개 그룹 중 상위 {len(preview)}개를 표시합니다.\n" + "\n".join(lines)
    table_cols = ["product", "step_id", "item_id", "wafer_id", "median", "mean", "count"]
    return {
        "handled": True,
        "intent": "et_wafer_median",
        "answer": answer,
        "rows": rows,
        "table": {
            "kind": "et_wafer_median",
            "title": "ET wafer median",
            "placement": "below",
            "columns": _table_columns(table_cols),
            "rows": [{k: r.get(k, "") for k in table_cols} for r in rows[: max(1, min(120, max_rows * 8))]],
            "total": len(rows),
        },
        "filters": {"step": step_matches, "item": item_matches, "lot": lot_matches, "product": sorted(aliases)},
    }


def _handle_knob_query(prompt: str, product: str, max_rows: int) -> dict:
    up = _upper(prompt)
    if "KNOB" not in up and "노브" not in prompt:
        return {"handled": False}
    lot_matches = _lot_tokens(prompt)
    files = _ml_files(product)
    if not files:
        return {
            "handled": True,
            "intent": "lot_knobs",
            "answer": "ML_TABLE parquet을 찾지 못했습니다. DB root의 `ML_TABLE_*.parquet` 파일을 확인해주세요.",
            "knobs": [],
        }
    lf = _scan_parquet(files)
    cols = _schema_names(lf)
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID")
    group = _flowi_group_token(prompt) or "KNOB"
    prefixes = (f"{group}_",) if group in {"KNOB", "MASK", "INLINE", "VM"} else ("KNOB_",)
    knob_cols = [c for c in cols if _upper(c).startswith(prefixes)]
    if not knob_cols:
        return {"handled": True, "intent": "lot_knobs", "answer": f"ML_TABLE에서 {group}_* 컬럼을 찾지 못했습니다.", "knobs": []}

    aliases = _product_aliases(product)
    filters = []
    if aliases and product_col:
        filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    if lot_matches:
        lot_cols = [c for c in (root_col, lot_col, fab_col) if c]
        lot_expr = _or_contains(lot_cols, lot_matches)
        if lot_expr is not None:
            filters.append(lot_expr)
    step = _flowi_func_step_token(prompt)
    step_expr = _flowi_step_filter_expr(cols, step)
    if step_expr is not None:
        filters.append(step_expr)
    for expr in filters:
        lf = lf.filter(expr)

    if not lot_matches:
        try:
            sample_cols = [c for c in (product_col, root_col, lot_col) if c]
            sample = lf.select(sample_cols).unique().limit(8).collect().to_dicts()
        except Exception:
            sample = []
        lots = ", ".join(sorted({_text(r.get(root_col) or r.get(lot_col)) for r in sample if _text(r.get(root_col) or r.get(lot_col))})[:8])
        suffix = f" 예: {lots}" if lots else ""
        return {
            "handled": True,
            "intent": "lot_knobs",
            "answer": "KNOB 조회는 lot/root lot 조건이 필요합니다." + suffix,
            "knobs": [],
            "lot_candidates": sample,
        }

    keep = [c for c in (product_col, root_col, lot_col, wafer_col) if c] + knob_cols
    try:
        df = lf.select(keep).collect()
    except Exception as e:
        logger.warning("flowi knob query failed: %s", e)
        raise HTTPException(400, f"KNOB 조회 실패: {e}")
    if wafer_col and wafer_col in df.columns:
        df = (
            df.with_columns(
                pl.col(wafer_col)
                .map_elements(lambda v: _normalize_wafer_id(v), return_dtype=_STR)
                .alias(wafer_col)
            )
            .filter(pl.col(wafer_col).is_not_null() & (pl.col(wafer_col) != ""))
        )
    if df.height == 0:
        return {
            "handled": True,
            "intent": "lot_knobs",
            "answer": f"{', '.join(lot_matches)} 조건에 맞는 ML_TABLE row가 없습니다.",
            "knobs": [],
        }

    q_tokens = set(_query_tokens(prompt)) - set(lot_matches)
    selected_knobs = []
    for col in knob_cols:
        body = _upper(col.replace("KNOB_", ""))
        if not q_tokens or any(tok in body for tok in q_tokens):
            selected_knobs.append(col)
    if not selected_knobs:
        selected_knobs = knob_cols

    table = None
    detail_requested = bool(q_tokens) or any(w in prompt for w in ("다", "전체", "테이블", "표", "보여"))
    if detail_requested and selected_knobs:
        table_knobs = selected_knobs[:8]
        table_cols = [c for c in (product_col, root_col, lot_col, fab_col, wafer_col) if c] + table_knobs
        rename = {}
        if product_col:
            rename[product_col] = "product"
        if root_col:
            rename[root_col] = "root_lot_id"
        if lot_col:
            rename[lot_col] = "lot_id"
        if fab_col:
            rename[fab_col] = "fab_lot_id"
        if wafer_col:
            rename[wafer_col] = "wafer_id"
        for col in table_knobs:
            rename[col] = col.replace("KNOB_", "", 1)
        try:
            tdf = df.select(table_cols).rename(rename)
            table_rows = _sort_wafer_rows(tdf.to_dicts())[:80]
        except Exception:
            table_rows = []
        table_columns = []
        for key, label in [
            ("product", "PRODUCT"),
            ("root_lot_id", "ROOT_LOT_ID"),
            ("lot_id", "LOT_ID"),
            ("fab_lot_id", "FAB_LOT_ID"),
            ("wafer_id", "WAFER_ID"),
        ]:
            if key in rename.values():
                table_columns.append({"key": key, "label": label})
        table_columns.extend({"key": col.replace("KNOB_", "", 1), "label": col.replace("KNOB_", "", 1)} for col in table_knobs)
        table = {
            "kind": "splittable_preview",
            "title": f"{', '.join(lot_matches)} KNOB table",
            "placement": "below",
            "columns": table_columns,
            "rows": table_rows,
            "total": int(df.height),
        }

    summaries = []
    for col in selected_knobs[: max(1, min(40, max_rows * 3))]:
        vc = (
            df.select(pl.col(col).cast(_STR, strict=False).alias("value"))
            .drop_nulls()
            .group_by("value")
            .len()
            .sort("len", descending=True)
        )
        values = vc.to_dicts()
        wafer_by_value = {}
        for rec in values[:5]:
            val = rec.get("value")
            try:
                wafers = (
                    df.filter(pl.col(col).cast(_STR, strict=False) == val)
                    .select(pl.col(wafer_col).cast(_STR, strict=False).alias("wafer_id"))
                    .unique()
                    .sort("wafer_id")
                    .limit(30)
                    .to_series()
                    .to_list()
                ) if wafer_col else []
            except Exception:
                wafers = []
            wafer_by_value[_text(val)] = wafers
        summaries.append({
            "knob": col,
            "display_name": col.replace("KNOB_", "", 1),
            "split": len(values) > 1,
            "values": [{"value": r.get("value"), "count": int(r.get("len") or 0), "wafers": wafer_by_value.get(_text(r.get("value")), [])} for r in values[:5]],
        })

    lot_label = ", ".join(lot_matches)
    preview = summaries[:max_rows]
    lines = []
    for item in preview:
        val_txt = "; ".join(
            f"{v.get('value')}({v.get('count')}wf" + (f": {','.join(v.get('wafers')[:8])}" if item.get("split") else "") + ")"
            for v in item.get("values", [])[:3]
        )
        lines.append(f"- {item.get('display_name')}: {val_txt}")
    answer = f"{lot_label} KNOB 요약입니다. {df.height} wafer row 기준, {len(summaries)}개 KNOB 중 {len(preview)}개를 표시합니다.\n" + "\n".join(lines)
    return {
        "handled": True,
        "intent": "lot_knobs",
        "answer": answer,
        "knobs": summaries,
        "table": table,
        "filters": {"lot": lot_matches, "product": sorted(aliases), "step": step, "group": group},
    }


def _is_rag_update_prompt(prompt: str) -> bool:
    return semi_knowledge.has_rag_update_marker(prompt)


def _handle_flowi_rag_update(prompt: str, me: dict[str, Any]) -> dict[str, Any]:
    username = me.get("username") or "user"
    role = me.get("role") or "user"
    try:
        knowledge_defaults = _flowi_engineer_knowledge_defaults()
        out = semi_knowledge.structure_rag_update_from_prompt(
            prompt,
            username=username,
            role=role,
            require_marker=(role != "admin") or bool(knowledge_defaults.get("rag_update_requires_marker", True)),
        )
    except ValueError as e:
        return {
            "handled": True,
            "intent": "semiconductor_rag_update",
            "action": "append_custom_knowledge",
            "blocked": True,
            "answer": f"RAG Update 본문이 비어 있습니다. [flow-i update] 또는 [flow-i RAG Update] 뒤에 구조화할 item/TEG/alias/판단 지식을 적어주세요. ({e})",
        }
    saved = out.get("saved") or {}
    structured = out.get("structured") or {}
    storage = out.get("storage") or {}
    rows = [
        {"field": "id", "value": saved.get("id") or ""},
        {"field": "kind", "value": saved.get("kind") or ""},
        {"field": "visibility", "value": saved.get("visibility") or ""},
        {"field": "schema_type", "value": structured.get("schema_type") or ""},
        {"field": "items", "value": ", ".join(structured.get("known_canonical_candidates") or [])},
        {"field": "raw_item_tokens", "value": ", ".join(structured.get("raw_item_tokens") or [])},
        {"field": "discriminators", "value": ", ".join(structured.get("discriminators") or [])},
        {"field": "storage", "value": storage.get("custom_knowledge") or ""},
    ]
    answer = (
        "Flow-i RAG Update를 append-only 지식으로 저장했습니다.\n"
        f"- 저장 위치: {storage.get('custom_knowledge') or '-'}\n"
        f"- visibility: {saved.get('visibility') or '-'}\n"
        f"- 구조 타입: {structured.get('schema_type') or '-'}\n"
        "기본 seed 코드는 프롬프트로 직접 수정하지 않고, 운영 지식은 flow-data에 누적합니다."
    )
    return {
        "handled": True,
        "intent": "semiconductor_rag_update",
        "action": "append_custom_knowledge",
        "answer": answer,
        "rag_update": out,
        "table": {"kind": "flowi_rag_update", "columns": ["field", "value"], "rows": rows},
        "feature": "diagnosis",
    }


def _is_reformatter_proposal_prompt(prompt: str) -> bool:
    low = str(prompt or "").lower()
    return (
        ("reformatter" in low or "alias" in low or "alias화" in low or "별칭" in low)
        and any(t in low for t in ["item", "teg", "chain", "pc-", "cb-", "m1", "raw"])
    )


def _is_teg_layout_prompt(prompt: str) -> bool:
    low = str(prompt or "").lower()
    return ("teg" in low or "좌표" in low or "coordinate" in low) and ("yaml" in low or "layout" in low or "정리" in low or "넣어" in low)


def _flowi_dataset_source_from_prompt(prompt: str, product: str, preferred_source: str = "") -> dict[str, Any]:
    files = _flowi_file_tokens(prompt)
    source: dict[str, Any] = {"product": product or ""}
    if files:
        source.update({"source_type": "base_file", "file": files[0]})
    explicit_source = re.search(r"(?:source_type|source|소스)\s*[:=]\s*(FAB|INLINE|ET|VM|QTIME|EDS)\b", prompt or "", re.I)
    if explicit_source:
        source["source_type_filter"] = explicit_source.group(1).upper()
        source["flowi_source_confirmed"] = True
    if preferred_source:
        source["source_type_filter"] = preferred_source.upper()
        source["flowi_source_confirmed"] = True
    return {k: v for k, v in source.items() if v}


def _compact_flowi_dataset_profile(profile: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(profile, dict):
        return {}
    if not profile.get("ok"):
        return {
            "ok": False,
            "reason": str(profile.get("reason") or "profile_failed")[:240],
            "warnings": [str(x)[:240] for x in (profile.get("warnings") or [])[:4]],
        }
    return {
        "ok": True,
        "source": profile.get("source") or {},
        "suggested_source_type": profile.get("suggested_source_type") or "",
        "metric_shape": profile.get("metric_shape") or "",
        "grain": profile.get("grain") or "",
        "join_keys": [str(x) for x in (profile.get("join_keys") or [])[:10]],
        "unique_items": [str(x) for x in (profile.get("unique_items") or [])[:12]],
        "metric_columns": [str(x) for x in (profile.get("metric_columns") or [])[:12]],
        "default_aggregation": profile.get("default_aggregation") or "",
        "warnings": [str(x)[:240] for x in (profile.get("warnings") or [])[:4]],
    }


def _flowi_dataset_profile_for_source(source: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(source, dict) or not source:
        return {}
    # Explicit file/root sources are cheap and explainable.  Product-only
    # discovery can scan many roots, so keep that out of the Home prompt path.
    if not (source.get("file") or (source.get("root") and source.get("product"))):
        return {}
    try:
        return _compact_flowi_dataset_profile(semi_knowledge.dataset_profile(source, limit=250))
    except Exception as e:
        return {"ok": False, "reason": str(e)[:240], "warnings": [str(e)[:240]]}


def _flowi_profile_label(profile: dict[str, Any]) -> str:
    if not profile:
        return "-"
    if not profile.get("ok"):
        return "profile_failed: " + str(profile.get("reason") or "-")
    bits = [
        str(profile.get("suggested_source_type") or "AUTO"),
        str(profile.get("metric_shape") or "?"),
        str(profile.get("grain") or "?"),
    ]
    keys = profile.get("join_keys") or []
    if keys:
        bits.append("join=" + ",".join(str(x) for x in keys[:4]))
    return " / ".join(bits)


def _flowi_source_profile_needs_clarification(source: dict[str, Any], profile: dict[str, Any]) -> bool:
    if not isinstance(source, dict) or not (source.get("file") or source.get("root")):
        return False
    if source.get("flowi_source_confirmed") or source.get("source_type_filter"):
        return False
    if not profile:
        return False
    if not profile.get("ok"):
        return True
    suggested = str(profile.get("suggested_source_type") or "").upper()
    shape = str(profile.get("metric_shape") or "").lower()
    grain = str(profile.get("grain") or "").lower()
    join_keys = profile.get("join_keys") or []
    if suggested in {"", "AUTO"}:
        return True
    if shape not in {"long", "wide"}:
        return True
    if grain in {"", "row"}:
        return True
    if not join_keys:
        return True
    severe = ("no clear item", "source type could not", "lot_wf cannot", "no readable")
    return any(any(term in str(w).lower() for term in severe) for w in (profile.get("warnings") or []))


def _flowi_source_type_choices(prompt: str, profile: dict[str, Any]) -> list[dict[str, Any]]:
    suggested = str((profile or {}).get("suggested_source_type") or "").upper()
    order = [suggested] if suggested in {"ET", "INLINE", "EDS", "VM", "QTIME", "FAB"} else []
    for item in ("ET", "INLINE", "EDS", "VM", "QTIME", "FAB"):
        if item not in order:
            order.append(item)
    meta = {
        "ET": ("ET/WAT parametric", "lot_wf 기준 median. DIBL/SS/Vth/Ion/Ioff/Rsd 같은 전기 특성에 적합합니다.", "grain=lot_wf aggregation=median"),
        "INLINE": ("INLINE metrology", "lot_wf 기준 avg, shot/position key가 있으면 shot 매칭을 우선합니다.", "grain=lot_wf aggregation=avg"),
        "EDS": ("EDS wafer sort", "die/bin 좌표와 fail/yield-rate를 보존합니다.", "grain=die aggregation=yield_rate"),
        "VM": ("VM/SRAM margin", "macro/condition/bin split을 유지하고 median 또는 fail-rate로 봅니다.", "grain=macro aggregation=median"),
        "QTIME": ("QTIME route window", "from_step/to_step 시간 구간을 route segment별 median/p95로 봅니다.", "grain=route_segment aggregation=p95"),
        "FAB": ("FAB route/progress", "step/time 최신 이력과 route sequence를 기준으로 봅니다.", "grain=lot_wf_step aggregation=latest"),
    }
    choices: list[dict[str, Any]] = []
    for idx, st in enumerate(order[:3]):
        title, desc, suffix = meta[st]
        choices.append({
            "id": f"source_{st.lower()}",
            "label": str(idx + 1),
            "title": title,
            "recommended": idx == 0,
            "description": desc,
            "prompt": f"{prompt.strip()} / source_type={st} {suffix} 으로 진행",
        })
    return choices


def _flowi_source_profile_clarification(
    prompt: str,
    product: str,
    source: dict[str, Any],
    profile: dict[str, Any],
    max_rows: int,
) -> dict[str, Any]:
    rows = [
        {"field": "source", "value": source.get("file") or (str(source.get("root") or "") + "/" + str(source.get("product") or product or "")).rstrip("/")},
        {"field": "profile", "value": _flowi_profile_label(profile)},
        {"field": "reason", "value": "source type/grain/join key가 확실하지 않아 실행 전 확인 필요"},
    ]
    if profile.get("ok"):
        rows.extend([
            {"field": "suggested_source_type", "value": profile.get("suggested_source_type") or "-"},
            {"field": "metric_shape", "value": profile.get("metric_shape") or "-"},
            {"field": "grain", "value": profile.get("grain") or "-"},
            {"field": "join_keys", "value": ", ".join(profile.get("join_keys") or []) or "-"},
            {"field": "unique_items", "value": ", ".join((profile.get("unique_items") or profile.get("metric_columns") or [])[:8]) or "-"},
        ])
    else:
        rows.append({"field": "profile_error", "value": profile.get("reason") or "profile_failed"})
    for i, warning in enumerate((profile.get("warnings") or [])[:3], start=1):
        rows.append({"field": f"warning_{i}", "value": warning})
    choices = _flowi_source_type_choices(prompt, profile)
    answer = (
        "파일/DB source의 schema가 애매해서 진단을 바로 실행하지 않았습니다.\n"
        "아래 1/2/3 중 어떤 데이터 성격으로 볼지 선택해주세요. "
        "선택 후에는 같은 파일을 whitelisted query로만 읽고, DB에 없는 값은 만들지 않습니다."
    )
    return {
        "handled": True,
        "intent": "semiconductor_source_clarification",
        "action": "confirm_semiconductor_source_profile",
        "answer": answer,
        "data_source": source,
        "source_profile": profile,
        "clarification": {
            "question": "이 source를 어떤 반도체 데이터 타입과 집계 기준으로 해석할까요?",
            "choices": choices,
        },
        "table": {
            "kind": "semiconductor_source_profile_review",
            "title": "Flow-i source profile review",
            "placement": "below",
            "columns": [{"key": "field", "label": "FIELD"}, {"key": "value", "label": "VALUE"}],
            "rows": rows[:max(1, max_rows)],
            "total": len(rows),
        },
        "feature": "diagnosis",
        "slots": {"product": product, "source": source},
    }


def _handle_flowi_admin_semiconductor_file_prep(prompt: str, product: str, me: dict[str, Any]) -> dict[str, Any]:
    if (me.get("role") or "user") != "admin":
        return {"handled": False}
    if _is_teg_layout_prompt(prompt):
        source = _flowi_dataset_source_from_prompt(prompt, product)
        source_profile = _flowi_dataset_profile_for_source(source)
        proposal = (
            semi_knowledge.teg_layout_proposal_from_dataset(product, source=source, prompt=prompt)
            if source.get("file") else
            semi_knowledge.teg_layout_proposal_from_rows(product, rows=[], prompt=prompt)
        )
        rows = [
            {"field": "target", "value": "product_config/products.yaml wafer_layout.teg_definitions"},
            {"field": "product", "value": product or "(필요)"},
            {"field": "source", "value": source.get("file") or "prompt/table rows"},
            {"field": "profile", "value": _flowi_profile_label(source_profile)},
            {"field": "detected_tegs", "value": str(len(proposal.get("teg_definitions") or []))},
            {"field": "required_columns", "value": ", ".join(proposal.get("required_columns") or [])},
        ]
        answer = (
            "TEG 좌표/YAML 반영은 admin 단위기능으로 처리해야 합니다.\n"
            "현재 프롬프트에서 추출된 TEG가 부족하면 `label/name/id`, `dx_mm/x`, `dy_mm/y` 컬럼을 가진 표를 먼저 넣어주세요. "
            "검토 후 `/api/semiconductor/teg/apply`가 product YAML에 반영합니다."
        )
        return {
            "handled": True,
            "intent": "semiconductor_teg_layout_proposal",
            "action": "propose_teg_yaml_update",
            "answer": answer,
            "proposal": proposal,
            "data_source": source,
            "source_profile": source_profile,
            "table": {"kind": "semiconductor_teg_yaml_proposal", "columns": ["field", "value"], "rows": rows},
            "feature": "diagnosis",
        }
    if _is_reformatter_proposal_prompt(prompt):
        source = _flowi_dataset_source_from_prompt(prompt, product)
        source_profile = _flowi_dataset_profile_for_source(source)
        proposal = (
            semi_knowledge.reformatter_alias_proposal_from_dataset(product, source=source, prompt=prompt)
            if source.get("file") else
            semi_knowledge.reformatter_alias_proposal_from_prompt(prompt, product=product)
        )
        rows = [
            {"field": "target", "value": "data/flow-data/reformatter/<product>.json"},
            {"field": "product", "value": product or "(필요)"},
            {"field": "source", "value": source.get("file") or "prompt text"},
            {"field": "profile", "value": _flowi_profile_label(source_profile)},
            {"field": "proposed_rules", "value": str(len(proposal.get("rules") or []))},
            {"field": "discriminators", "value": ", ".join(proposal.get("discriminators") or [])},
            {"field": "status", "value": "proposal_only; admin apply required"},
        ]
        answer = (
            "real item alias/reformatter 후보를 만들었습니다.\n"
            "PC-CB-M1처럼 비슷한 item은 14x14/13x13/12x12, pitch, cell height, coordinate 같은 discriminator를 유지한 뒤 admin apply 해야 합니다. "
            "반영은 `/api/semiconductor/reformatter/apply`에서 기존 rule과 중복/validation을 확인하고 저장합니다."
        )
        return {
            "handled": True,
            "intent": "semiconductor_reformatter_proposal",
            "action": "propose_reformatter_alias_rules",
            "answer": answer,
            "proposal": proposal,
            "data_source": source,
            "source_profile": source_profile,
            "table": {"kind": "semiconductor_reformatter_proposal", "columns": ["field", "value"], "rows": rows},
            "feature": "diagnosis",
        }
    return {"handled": False}


def _is_semiconductor_diagnosis_prompt(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    phrase_terms = [
        "rca", "root cause", "원인", "원인 후보", "진단", "mechanism", "causal", "knowledge card",
        "dibl", "vth", "rolloff", "roll-off", "ioff", "rsd", "igate",
        "gate leakage", "sram", "vmin", "ca_rs", "ca rc", "ca_cd", "short lg", "gaa",
    ]
    if any(t in low for t in phrase_terms):
        return True
    return bool(re.search(r"(?<![a-z0-9])(ss|ion)(?![a-z0-9])", low))


def _handle_semiconductor_diagnosis_query(prompt: str, product: str, max_rows: int = 12) -> dict[str, Any]:
    if _is_rag_update_prompt(prompt):
        return {"handled": False}
    if not _is_semiconductor_diagnosis_prompt(prompt):
        return {"handled": False}
    try:
        source_filter = _flowi_dataset_source_from_prompt(prompt, product)
        source_profile = _flowi_dataset_profile_for_source(source_filter)
        if _flowi_source_profile_needs_clarification(source_filter, source_profile):
            return _flowi_source_profile_clarification(prompt, product, source_filter, source_profile, max_rows)
        report = semi_knowledge.run_diagnosis(
            prompt,
            product=product,
            filters={"source": source_filter or "flowi", **source_filter, "max_rows": max_rows},
            save=True,
        )
    except Exception as e:
        return {
            "handled": True,
            "intent": "semiconductor_diagnosis",
            "action": "run_semiconductor_diagnosis",
            "answer": f"반도체 진단 실행 중 오류가 발생했습니다: {e}",
            "blocked": False,
        }
    hyps = report.get("ranked_hypotheses") or []
    rows = [
        {
            "rank": h.get("rank"),
            "hypothesis": h.get("hypothesis"),
            "confidence": h.get("confidence"),
            "mechanism": h.get("electrical_mechanism"),
            "card": h.get("knowledge_card_id"),
        }
        for h in hyps[:max_rows]
    ]
    item_rows = [
        {
            "raw": r.get("raw_item"),
            "status": r.get("status"),
            "canonical": r.get("canonical_item_id") or ", ".join(c.get("canonical_item_id", "") for c in r.get("candidates") or []),
            "meaning": (r.get("item") or {}).get("meaning") or r.get("ambiguity") or "",
        }
        for r in (report.get("interpreted_items") or {}).get("resolved", [])
    ]
    top = hyps[:3]
    source_line = ""
    if source_filter.get("file"):
        source_line = f"\n데이터 source: {source_filter.get('file')}"
    elif source_filter.get("root"):
        source_line = f"\n데이터 source: {source_filter.get('root')}/{source_filter.get('product') or product}"
    if source_profile:
        source_line += f" ({_flowi_profile_label(source_profile)})"
    if top:
        lines = [f"{h.get('rank')}. {h.get('hypothesis')} (confidence {h.get('confidence')})" for h in top]
        answer = (
            "반도체 진단/RCA 단위기능으로 처리했습니다.\n"
            + "\n".join(lines)
            + source_line
            + "\n확정 원인이 아니라 item 의미, Knowledge Card, causal graph, 유사 case 기반 후보입니다."
        )
    else:
        answer = "반도체 진단/RCA 단위기능으로 보았지만 인식된 지표가 부족합니다." + source_line + "\nitem명과 unit/test_structure를 더 알려주세요."
    return {
        "handled": True,
        "intent": "semiconductor_diagnosis",
        "action": "run_semiconductor_diagnosis",
        "answer": answer,
        "diagnosis": report,
        "data_source": source_filter,
        "source_profile": source_profile,
        "table": {"kind": "semiconductor_rca_hypotheses", "columns": ["rank", "hypothesis", "confidence", "mechanism", "card"], "rows": rows},
        "items_table": {"kind": "semiconductor_item_resolution", "columns": ["raw", "status", "canonical", "meaning"], "rows": item_rows},
        "feature": "diagnosis",
        "slots": {
            "product": product,
            "source": source_filter,
            "items": report.get("feature_extractor", {}).get("items") or [],
            "modules": report.get("feature_extractor", {}).get("modules") or [],
        },
    }


def _flowi_source_files(source_type: str, product: str = "") -> list[Path]:
    st = _upper(source_type)
    if st == "FAB":
        return _fab_files(product)
    if st == "ET":
        return _et_files(product)
    if st == "INLINE":
        return _inline_files(product)
    if st == "ML_TABLE":
        return _ml_files(product)
    roots = _db_root_candidates(st)
    files: list[Path] = []
    for root in roots:
        files.extend(sorted(root.rglob("*.parquet")))
    return _filter_files_by_product(files, product)


def _flowi_step_filter_expr(cols: list[str], step: str):
    target = _upper(step)
    if not target:
        return None
    candidates = [
        _ci_col(cols, "func_step", "function_step", "FUNCTION_STEP", "step_name", "STEP_NAME"),
        _ci_col(cols, "step_id", "STEP_ID"),
        _ci_col(cols, "process_id", "PROCESS_ID"),
        _ci_col(cols, "ppid", "PPID"),
    ]
    expr = None
    for col in [c for c in candidates if c]:
        piece = pl.col(col).cast(_STR, strict=False).str.to_uppercase().str.contains(target, literal=True)
        expr = piece if expr is None else (expr | piece)
    return expr


def _flowi_lot_filter_expr(cols: list[str], root_lots: list[str], fab_lots: list[str]):
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    lot_col = _ci_col(cols, "lot_id", "LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
    lot_wf_col = _ci_col(cols, "lot_wf", "LOT_WF")
    return _or_contains([c for c in (root_col, lot_col, fab_col, lot_wf_col) if c], [*(root_lots or []), *(fab_lots or [])])


def _handle_fab_progress_query(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    preview = _structure_flowi_function_call(prompt, product=product, max_rows=max_rows)
    selected = (preview.get("selected_function") or {}).get("name")
    if selected != "query_fab_progress":
        return {"handled": False}
    args = ((preview.get("function_call") or {}).get("function") or {}).get("arguments") or {}
    missing = (preview.get("validation") or {}).get("missing") or []
    if missing:
        return _flowi_preview_tool(preview, answer="FAB 진행 조회에 필요한 lot 조건을 보완해 주세요.")
    product_hint = str(args.get("product") or product or "")
    roots = [str(x) for x in args.get("root_lot_ids") or []]
    fabs = [str(x) for x in args.get("fab_lot_ids") or []]
    lots = roots + fabs + [str(x) for x in args.get("lot_ids") or []]
    files = _fab_files(product_hint)
    if not files:
        return {"handled": True, "intent": "fab_progress_lookup", "action": "query_fab_progress", "answer": "FAB parquet을 찾지 못했습니다.", "feature": "filebrowser"}
    try:
        lf = _scan_parquet(files)
        cols = _schema_names(lf)
        product_col = _ci_col(cols, "product", "PRODUCT")
        root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
        lot_col = _ci_col(cols, "lot_id", "LOT_ID")
        fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
        wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
        step_col = _ci_col(cols, "step_id", "STEP_ID")
        process_col = _ci_col(cols, "process_id", "PROCESS_ID")
        time_col = _ci_col(cols, "tkout_time", "TKOUT_TIME", "time", "TIME", "timestamp", "TIMESTAMP", "move_time", "MOVE_TIME", "updated_at", "UPDATED_AT")
        if product_hint and product_col:
            aliases = _product_aliases(product_hint)
            lf = lf.filter(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
        lot_expr = _flowi_lot_filter_expr(cols, roots, fabs or lots)
        if lot_expr is not None:
            lf = lf.filter(lot_expr)
        wf_expr = _wafer_match_expr(wafer_col, [str(w) for w in args.get("wafer_ids") or []])
        if wf_expr is not None:
            lf = lf.filter(wf_expr)
        exprs = [
            pl.col(product_col).cast(_STR, strict=False).alias("product") if product_col else pl.lit(product_hint).alias("product"),
            pl.col(root_col).cast(_STR, strict=False).alias("root_lot_id") if root_col else (
                pl.col(lot_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id") if lot_col else (pl.col(fab_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id") if fab_col else pl.lit("").alias("root_lot_id"))
            ),
            pl.col(lot_col).cast(_STR, strict=False).alias("lot_id") if lot_col else pl.lit("").alias("lot_id"),
            pl.col(fab_col).cast(_STR, strict=False).alias("fab_lot_id") if fab_col else pl.lit("").alias("fab_lot_id"),
            _wafer_key_expr(wafer_col).alias("wafer_id") if wafer_col else pl.lit("").alias("wafer_id"),
            pl.col(step_col).cast(_STR, strict=False).alias("step_id") if step_col else pl.lit("").alias("step_id"),
            pl.col(process_col).cast(_STR, strict=False).alias("process_id") if process_col else pl.lit("").alias("process_id"),
            pl.col(time_col).cast(_STR, strict=False).alias("tkout_time") if time_col else pl.lit("").alias("tkout_time"),
        ]
        df = lf.select(exprs).limit(50000).collect()
    except Exception as e:
        return {"handled": True, "intent": "fab_progress_lookup", "action": "query_fab_progress", "answer": f"FAB 진행 조회 실패: {e}", "feature": "filebrowser"}
    rows = df.to_dicts()
    if not rows:
        return {"handled": True, "intent": "fab_progress_lookup", "action": "query_fab_progress", "answer": "조건에 맞는 FAB 진행 row를 찾지 못했습니다.", "feature": "filebrowser"}
    rows.sort(key=lambda r: (_parse_flowi_datetime(r.get("tkout_time")) or datetime.min, _step_rank_key(r.get("step_id"))), reverse=True)
    cols_out = ["product", "root_lot_id", "fab_lot_id", "lot_id", "wafer_id", "step_id", "process_id", "tkout_time"]
    top = rows[0]
    answer = f"{top.get('fab_lot_id') or top.get('root_lot_id') or (lots[0] if lots else '')} 현재 위치는 step_id={top.get('step_id') or '-'} 입니다."
    if top.get("tkout_time"):
        answer += f" 최신 시간: {top.get('tkout_time')}."
    return {
        "handled": True,
        "intent": "fab_progress_lookup",
        "action": "query_fab_progress",
        "answer": answer,
        "feature": "filebrowser",
        "filters": {"product": product_hint, "root_lot_ids": roots, "fab_lot_ids": fabs},
        "table": {"kind": "fab_progress_lookup", "title": "FAB progress", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in rows[:max(1, min(80, max_rows * 4))]], "total": len(rows)},
    }


def _handle_wafer_split_at_step(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    preview = _structure_flowi_function_call(prompt, product=product, max_rows=max_rows)
    if ((preview.get("selected_function") or {}).get("name") != "query_wafer_split_at_step"):
        return {"handled": False}
    args = ((preview.get("function_call") or {}).get("function") or {}).get("arguments") or {}
    if (preview.get("validation") or {}).get("missing"):
        return _flowi_preview_tool(preview, answer="wafer split 조회에 필요한 값을 보완해 주세요.")
    product_hint = str(args.get("product") or product or "")
    files = _ml_files(product_hint)
    if not files:
        return {"handled": True, "intent": "wafer_split_at_step", "action": "query_wafer_split_at_step", "answer": "ML_TABLE parquet을 찾지 못했습니다.", "feature": "splittable"}
    try:
        lf = _scan_parquet(files)
        cols = _schema_names(lf)
        product_col = _ci_col(cols, "product", "PRODUCT")
        root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
        lot_col = _ci_col(cols, "lot_id", "LOT_ID")
        fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
        wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
        if product_hint and product_col:
            lf = lf.filter(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(_product_aliases(product_hint))))
        lot_expr = _flowi_lot_filter_expr(cols, args.get("root_lot_ids") or [], args.get("fab_lot_ids") or [])
        if lot_expr is not None:
            lf = lf.filter(lot_expr)
        step_expr = _flowi_step_filter_expr(cols, str(args.get("step") or ""))
        if step_expr is not None:
            lf = lf.filter(step_expr)
        wf_expr = _wafer_match_expr(wafer_col, [str(w) for w in args.get("wafer_ids") or []])
        if wf_expr is not None:
            lf = lf.filter(wf_expr)
        split_cols = [c for c in cols if _upper(c).startswith(("KNOB_", "MASK_"))]
        keep = [c for c in (product_col, root_col, lot_col, fab_col, wafer_col) if c] + split_cols[:40]
        df = lf.select([pl.col(c).cast(_STR, strict=False).alias(c) for c in keep]).limit(500).collect()
    except Exception as e:
        return {"handled": True, "intent": "wafer_split_at_step", "action": "query_wafer_split_at_step", "answer": f"wafer split 조회 실패: {e}", "feature": "splittable"}
    rows_raw = df.to_dicts()
    rows = []
    for row in rows_raw:
        for col in split_cols[:40]:
            val = _text(row.get(col))
            if not val:
                continue
            rows.append({
                "product": row.get(product_col) if product_col else product_hint,
                "root_lot_id": row.get(root_col) if root_col else "",
                "fab_lot_id": row.get(fab_col) if fab_col else "",
                "lot_id": row.get(lot_col) if lot_col else "",
                "wafer_id": row.get(wafer_col) if wafer_col else "",
                "step": args.get("step") or "",
                "parameter": col,
                "value": val,
            })
    cols_out = ["product", "root_lot_id", "fab_lot_id", "lot_id", "wafer_id", "step", "parameter", "value"]
    answer = f"{args.get('step')} 기준 wafer split 조합 {len(rows)}개를 찾았습니다." if rows else "조건에 맞는 wafer split 값을 찾지 못했습니다."
    return {
        "handled": True,
        "intent": "wafer_split_at_step",
        "action": "query_wafer_split_at_step",
        "answer": answer,
        "feature": "splittable",
        "table": {"kind": "wafer_split_at_step", "title": "Wafer split at step", "placement": "below", "columns": _table_columns(cols_out), "rows": rows[:max(1, min(120, max_rows * 8))], "total": len(rows)},
        "filters": {"product": product_hint, "step": args.get("step"), "root_lot_ids": args.get("root_lot_ids"), "fab_lot_ids": args.get("fab_lot_ids"), "wafer_ids": args.get("wafer_ids")},
    }


def _handle_find_lots_by_knob_value(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    preview = _structure_flowi_function_call(prompt, product=product, max_rows=max_rows)
    if ((preview.get("selected_function") or {}).get("name") != "find_lots_by_knob_value"):
        return {"handled": False}
    args = ((preview.get("function_call") or {}).get("function") or {}).get("arguments") or {}
    if (preview.get("validation") or {}).get("missing"):
        return _flowi_preview_tool(preview, answer="KNOB value 역검색에 필요한 값을 보완해 주세요.")
    product_hint = str(args.get("product") or product or "")
    knob_value = str(args.get("knob_value") or "")
    files = _ml_files(product_hint)
    if not files:
        return {"handled": True, "intent": "knob_value_lot_search", "action": "find_lots_by_knob_value", "answer": "ML_TABLE parquet을 찾지 못했습니다.", "feature": "splittable"}
    try:
        lf = _scan_parquet(files)
        cols = _schema_names(lf)
        product_col = _ci_col(cols, "product", "PRODUCT")
        root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
        lot_col = _ci_col(cols, "lot_id", "LOT_ID")
        fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
        wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
        if product_hint and product_col:
            lf = lf.filter(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(_product_aliases(product_hint))))
        step_expr = _flowi_step_filter_expr(cols, str(args.get("step") or ""))
        if step_expr is not None:
            lf = lf.filter(step_expr)
        knob_cols = [c for c in cols if _upper(c).startswith(("KNOB_", "MASK_"))]
        expr = None
        for col in knob_cols:
            piece = pl.col(col).cast(_STR, strict=False) == knob_value
            expr = piece if expr is None else (expr | piece)
        if expr is None:
            return {"handled": True, "intent": "knob_value_lot_search", "action": "find_lots_by_knob_value", "answer": "ML_TABLE에서 KNOB/MASK 컬럼을 찾지 못했습니다.", "feature": "splittable"}
        scoped = lf.filter(expr)
        keep = [c for c in (product_col, root_col, lot_col, fab_col, wafer_col) if c] + knob_cols
        df = scoped.select([pl.col(c).cast(_STR, strict=False).alias(c) for c in keep]).limit(10000).collect()
    except Exception as e:
        return {"handled": True, "intent": "knob_value_lot_search", "action": "find_lots_by_knob_value", "answer": f"KNOB value 역검색 실패: {e}", "feature": "splittable"}
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in df.to_dicts():
        for col in knob_cols:
            if _text(row.get(col)) != knob_value:
                continue
            root = _text(row.get(root_col)) if root_col else ""
            wafer = _text(row.get(wafer_col)) if wafer_col else ""
            key = (root, wafer, col)
            grouped[key] = {
                "product": _text(row.get(product_col)) if product_col else product_hint,
                "root_lot_id": root,
                "lot_id": _text(row.get(lot_col)) if lot_col else "",
                "fab_lot_id": _text(row.get(fab_col)) if fab_col else "",
                "wafer_id": wafer,
                "step": args.get("step") or "",
                "knob": col,
                "knob_value": knob_value,
            }
    rows = list(grouped.values())
    fab_steps = _latest_fab_steps_for_roots(product_hint, [r["root_lot_id"] for r in rows if r.get("root_lot_id")], limit=1000)
    for row in rows:
        fab = fab_steps.get(row.get("root_lot_id")) or {}
        row["current_step"] = fab.get("step_id") or ""
        row["current_func_step"] = fab.get("func_step") or ""
        row["tkout_time"] = fab.get("time") or ""
    rows.sort(key=lambda r: (_step_rank_key(r.get("current_step")), str(r.get("tkout_time") or "")), reverse=True)
    limit = max(1, min(100, int(args.get("limit") or max_rows or 10)))
    cols_out = ["product", "root_lot_id", "lot_id", "fab_lot_id", "wafer_id", "step", "knob", "knob_value", "current_step", "current_func_step", "tkout_time"]
    answer = f"{args.get('step')}에서 {knob_value} 값을 받은 lot/wafer {len(rows)}건을 FAB 진행 위치와 연결했습니다." if rows else f"{knob_value} 조건의 lot을 찾지 못했습니다."
    return {
        "handled": True,
        "intent": "knob_value_lot_search",
        "action": "find_lots_by_knob_value",
        "answer": answer,
        "feature": "splittable",
        "table": {"kind": "knob_value_lot_search", "title": "Lots by KNOB value", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in rows[:limit]], "total": len(rows)},
        "filters": {"product": product_hint, "step": args.get("step"), "knob_value": knob_value, "sort": args.get("sort") or "earliest_progress"},
    }


def _handle_metric_at_step(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    preview = _structure_flowi_function_call(prompt, product=product, max_rows=max_rows)
    if ((preview.get("selected_function") or {}).get("name") != "query_metric_at_step"):
        return {"handled": False}
    args = ((preview.get("function_call") or {}).get("function") or {}).get("arguments") or {}
    if (preview.get("validation") or {}).get("missing"):
        return _flowi_preview_tool(preview, answer="측정값 조회에 필요한 값을 보완해 주세요.")
    product_hint = str(args.get("product") or product or "")
    metric = str(args.get("metric") or "")
    agg = str(args.get("agg") or "median").lower()
    rows: list[dict[str, Any]] = []
    for source_type, files in (("ET", _et_files(product_hint)), ("INLINE", _inline_files(product_hint))):
        if not files:
            continue
        try:
            lf = _scan_parquet(files)
            cols = _schema_names(lf)
            product_col = _ci_col(cols, "product", "PRODUCT")
            root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
            lot_col = _ci_col(cols, "lot_id", "LOT_ID")
            fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID")
            wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
            step_col = _ci_col(cols, "step_id", "STEP_ID")
            item_col = _ci_col(cols, "item_id", "ITEM_ID", "metric", "METRIC", "subitem_id", "SUBITEM_ID")
            value_col = _ci_col(cols, "value", "VALUE", "result", "RESULT")
            if product_hint and product_col:
                lf = lf.filter(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(_product_aliases(product_hint))))
            lot_expr = _flowi_lot_filter_expr(cols, args.get("root_lot_ids") or [], args.get("fab_lot_ids") or [])
            if lot_expr is not None:
                lf = lf.filter(lot_expr)
            wf_expr = _wafer_match_expr(wafer_col, [str(w) for w in args.get("wafer_ids") or []])
            if wf_expr is not None:
                lf = lf.filter(wf_expr)
            step_expr = _flowi_step_filter_expr(cols, str(args.get("step") or ""))
            if step_expr is not None:
                lf = lf.filter(step_expr)
            metric_cols = _column_matches(cols, [metric], include_knob_when_named=False)
            metric_col = next((c for c in metric_cols if c not in {product_col, root_col, lot_col, fab_col, wafer_col, step_col, item_col}), "")
            if metric_col:
                value_expr = pl.col(metric_col).cast(pl.Float64, strict=False).alias("value")
                item_expr = pl.lit(metric).alias("metric")
            elif item_col and value_col:
                matches = _match_values(_unique_strings(lf, item_col, limit=1000), [metric])
                if matches:
                    lf = lf.filter(pl.col(item_col).cast(_STR, strict=False).is_in(matches))
                value_expr = pl.col(value_col).cast(pl.Float64, strict=False).alias("value")
                item_expr = pl.col(item_col).cast(_STR, strict=False).alias("metric")
            else:
                continue
            exprs = [
                pl.lit(source_type).alias("source_type"),
                pl.col(product_col).cast(_STR, strict=False).alias("product") if product_col else pl.lit(product_hint).alias("product"),
                pl.col(root_col).cast(_STR, strict=False).alias("root_lot_id") if root_col else (
                    pl.col(lot_col).cast(_STR, strict=False).str.slice(0, 5).alias("root_lot_id") if lot_col else pl.lit("").alias("root_lot_id")
                ),
                _wafer_key_expr(wafer_col).alias("wafer_id") if wafer_col else pl.lit("").alias("wafer_id"),
                pl.col(step_col).cast(_STR, strict=False).alias("step_id") if step_col else pl.lit(str(args.get("step") or "")).alias("step_id"),
                item_expr,
                value_expr,
            ]
            df = lf.select(exprs).drop_nulls(subset=["value"]).limit(100000).collect()
        except Exception as e:
            logger.warning("flowi metric at step failed source=%s: %s", source_type, e)
            continue
        if df.height == 0:
            continue
        group_cols = ["source_type", "product", "root_lot_id", "wafer_id", "step_id", "metric"]
        agg_expr = pl.col("value").mean().alias("value") if agg == "avg" else pl.col("value").median().alias("value")
        try:
            got = df.lazy().group_by(group_cols).agg([agg_expr, pl.len().alias("count")]).collect()
            rows.extend(got.to_dicts())
        except Exception:
            pass
    cols_out = ["source_type", "product", "root_lot_id", "wafer_id", "step_id", "metric", "value", "count"]
    answer = f"{args.get('step')} {metric} {agg} 집계 {len(rows)}건입니다." if rows else f"{args.get('step')} {metric} 측정값을 찾지 못했습니다."
    return {
        "handled": True,
        "intent": "metric_at_step_lookup",
        "action": "query_metric_at_step",
        "answer": answer,
        "feature": "filebrowser",
        "table": {"kind": "metric_at_step", "title": "Metric at step", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: r.get(k, "") for k in cols_out} for r in rows[:max(1, min(120, max_rows * 8))]], "total": len(rows)},
        "filters": {"product": product_hint, "step": args.get("step"), "metric": metric, "agg": agg, "root_lot_ids": args.get("root_lot_ids"), "fab_lot_ids": args.get("fab_lot_ids"), "wafer_ids": args.get("wafer_ids")},
    }


def _handle_filebrowser_data_preview(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    preview = _structure_flowi_function_call(prompt, product=product, max_rows=max_rows)
    if ((preview.get("selected_function") or {}).get("name") != "preview_filebrowser_data"):
        return {"handled": False}
    args = ((preview.get("function_call") or {}).get("function") or {}).get("arguments") or {}
    if (preview.get("validation") or {}).get("missing"):
        return _flowi_preview_tool(preview, answer="DB preview에 필요한 source/product를 보완해 주세요.")
    source_type = str(args.get("source_type") or "")
    product_hint = str(args.get("product") or product or "")
    limit = max(1, min(500, int(args.get("limit") or 100)))
    files = _flowi_source_files(source_type, product_hint)
    if not files:
        return {"handled": True, "intent": "filebrowser_data_preview", "action": "preview_filebrowser_data", "answer": f"{source_type} parquet을 찾지 못했습니다.", "feature": "filebrowser"}
    try:
        lf = _scan_parquet(files[:120])
        cols = _schema_names(lf)
        lot_expr = _flowi_lot_filter_expr(cols, args.get("root_lot_ids") or [], args.get("fab_lot_ids") or [])
        if lot_expr is not None:
            lf = lf.filter(lot_expr)
        wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
        wf_expr = _wafer_match_expr(wafer_col, [str(w) for w in args.get("wafer_ids") or []])
        if wf_expr is not None:
            lf = lf.filter(wf_expr)
        show_cols = cols[: min(18, len(cols))]
        df = lf.select([pl.col(c).cast(_STR, strict=False).alias(c) for c in show_cols]).limit(limit).collect()
    except Exception as e:
        return {"handled": True, "intent": "filebrowser_data_preview", "action": "preview_filebrowser_data", "answer": f"DB preview 실패: {e}", "feature": "filebrowser"}
    rows = df.to_dicts()
    return {
        "handled": True,
        "intent": "filebrowser_data_preview",
        "action": "preview_filebrowser_data",
        "answer": f"{source_type}/{product_hint} row {len(rows)}건을 read-only preview 했습니다.",
        "feature": "filebrowser",
        "table": {"kind": "filebrowser_data_preview", "title": f"{source_type} preview", "placement": "below", "columns": _table_columns(show_cols), "rows": rows, "total": len(rows), "source": source_type},
        "filters": {"source_type": source_type, "product": product_hint, "limit": limit},
    }


def _handle_filebrowser_schema_search(prompt: str, product: str, max_rows: int) -> dict[str, Any]:
    preview = _structure_flowi_function_call(prompt, product=product, max_rows=max_rows)
    if ((preview.get("selected_function") or {}).get("name") != "search_filebrowser_schema"):
        return {"handled": False}
    args = ((preview.get("function_call") or {}).get("function") or {}).get("arguments") or {}
    if (preview.get("validation") or {}).get("missing"):
        return _flowi_preview_tool(preview, answer="schema 검색 keyword를 보완해 주세요.")
    keyword = str(args.get("keyword") or "")
    source_types = [str(args.get("source_type") or "").upper()] if args.get("source_type") else ["FAB", "ET", "INLINE", "VM", "EDS", "ML_TABLE"]
    rows: list[dict[str, Any]] = []
    for st in source_types:
        files = _flowi_source_files(st, product)
        if not files:
            continue
        try:
            cols = _schema_names(_scan_parquet(files[:20]))
        except Exception:
            continue
        for col in cols:
            if _upper(keyword) in _upper(col):
                rows.append({"source_type": st, "column": col, "file_count": len(files)})
    rows.sort(key=lambda r: (r.get("source_type") or "", r.get("column") or ""))
    cols_out = ["source_type", "column", "file_count"]
    answer = f"`{keyword}` schema 컬럼 후보 {len(rows)}개를 찾았습니다." if rows else f"`{keyword}` 컬럼 후보를 찾지 못했습니다."
    return {
        "handled": True,
        "intent": "filebrowser_schema_search",
        "action": "search_filebrowser_schema",
        "answer": answer,
        "feature": "filebrowser",
        "table": {"kind": "filebrowser_schema_search", "title": "Schema column search", "placement": "below", "columns": _table_columns(cols_out), "rows": rows[:max(1, min(120, max_rows * 8))], "total": len(rows)},
        "filters": {"keyword": keyword, "source_types": source_types},
    }


def _flowi_module_recipients(module: str) -> list[dict[str, Any]]:
    try:
        from routers import informs as informs_router
        rows = informs_router._module_recipient_rows(module)
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


def _flowi_build_mail_preview_for_draft(entry: dict[str, Any], username: str = "") -> dict[str, Any]:
    try:
        from routers import informs as informs_router
        recipients = _flowi_module_recipients(str(entry.get("module") or ""))
        subject = informs_router._default_mail_subject(entry)
        body = informs_router._default_mail_prose(entry, sender_username=username)
        return {
            "subject": subject,
            "body_text": body,
            "resolved_recipients": [r.get("email") for r in recipients if isinstance(r, dict) and r.get("email")],
            "auto_module_recipients": recipients,
            "auto_module_used": bool(recipients),
        }
    except Exception:
        return {"subject": "", "body_text": "", "resolved_recipients": [], "auto_module_recipients": [], "auto_module_used": False}


def _handle_compose_inform_module_mail(prompt: str, product: str, max_rows: int, me: dict[str, Any] | None = None) -> dict[str, Any]:
    preview = _structure_flowi_function_call(prompt, product=product, max_rows=max_rows)
    if ((preview.get("selected_function") or {}).get("name") != "compose_inform_module_mail"):
        return {"handled": False}
    args = ((preview.get("function_call") or {}).get("function") or {}).get("arguments") or {}
    missing = list((preview.get("validation") or {}).get("missing") or [])
    if missing:
        return _flowi_preview_tool(preview, answer="메일 미리보기에 필요한 값을 선택해 주세요.")
    username = (me or {}).get("username") or "user"
    lot_id = (args.get("fab_lot_ids") or args.get("root_lot_ids") or args.get("lot_ids") or [""])[0]
    entry = {
        "id": "dry_run",
        "product": args.get("product") or product,
        "module": args.get("module") or "",
        "reason": args.get("reason") or "Flow-i 메일 미리보기",
        "text": args.get("reason") or "",
        "root_lot_id": (args.get("root_lot_ids") or [""])[0],
        "lot_id": lot_id,
        "wafer_id": lot_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "fab_lot_id_at_save": ", ".join(args.get("fab_lot_ids") or []),
    }
    mail_preview = _flowi_build_mail_preview_for_draft(entry, username=username)
    rows = [
        {"field": "product", "value": entry["product"]},
        {"field": "module", "value": entry["module"]},
        {"field": "lot", "value": lot_id},
        {"field": "recipients", "value": ", ".join(mail_preview.get("resolved_recipients") or [])},
        {"field": "subject", "value": mail_preview.get("subject") or ""},
        {"field": "policy", "value": "미리보기만 생성하며 발송은 별도 확인 후 진행"},
    ]
    return {
        "handled": True,
        "intent": "inform_module_mail_preview",
        "action": "compose_inform_module_mail",
        "answer": f"{entry['module']} 모듈 메일 미리보기입니다. 실제 발송은 하지 않았습니다.",
        "feature": "inform",
        "requires_confirmation": True,
        "side_effect": "confirm_before_write",
        "arguments": args,
        "mail_preview": mail_preview,
        "table": {"kind": "inform_mail_preview", "title": "Inform mail preview", "placement": "below", "columns": _table_columns(["field", "value"]), "rows": rows, "total": len(rows)},
    }


def _flowi_inform_session_path(session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(session_id or "")).strip("._-")
    if not safe:
        raise HTTPException(400, "session_id required")
    return FLOWI_INFORM_SESSION_DIR / f"{safe}.json"


def _flowi_cleanup_inform_sessions() -> None:
    try:
        FLOWI_INFORM_SESSION_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).timestamp()
        for fp in FLOWI_INFORM_SESSION_DIR.glob("*.json"):
            try:
                data = load_json(fp, {})
                ts = _parse_ts(data.get("last_active_at") or data.get("created_at"))
                if ts and now - ts.timestamp() > FLOWI_INFORM_SESSION_TTL_SECONDS:
                    fp.unlink(missing_ok=True)
            except Exception:
                continue
    except Exception:
        pass


def _flowi_save_inform_state(state: dict[str, Any]) -> dict[str, Any]:
    FLOWI_INFORM_SESSION_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    state = dict(state)
    state.setdefault("created_at", now)
    state["last_active_at"] = now
    save_json(_flowi_inform_session_path(str(state.get("session_id") or state.get("draft_id") or "")), state, indent=2)
    return state


def _flowi_load_inform_state(session_id: str) -> dict[str, Any]:
    _flowi_cleanup_inform_sessions()
    fp = _flowi_inform_session_path(session_id)
    data = load_json(fp, {})
    if not isinstance(data, dict) or not data:
        raise HTTPException(404, "inform session not found")
    return data


def _flowi_draft_id() -> str:
    return "draft_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:8]


def _flowi_inform_entry_preview(args: dict[str, Any], entry_args: dict[str, Any] | None = None) -> dict[str, Any]:
    entry_args = entry_args if isinstance(entry_args, dict) else {}
    lot = ""
    root_lots = [str(x) for x in (args.get("root_lot_ids") or []) if str(x or "").strip()]
    fab_lots = [str(x) for x in (args.get("fab_lot_ids") or []) if str(x or "").strip()]
    if fab_lots:
        lot = fab_lots[0]
    elif root_lots:
        lot = root_lots[0]
    wafer_ids = [str(x) for x in (args.get("wafer_ids") or []) if str(x or "").strip()]
    module = str(entry_args.get("module") or args.get("module") or "").strip()
    split_set = str(entry_args.get("split_set") or args.get("split_set") or "").strip()
    note = str(entry_args.get("note") or args.get("note") or "").strip()
    reason = str(entry_args.get("reason") or args.get("reason") or split_set or "Flow-i 인폼").strip()
    missing = []
    if not module:
        missing.append("module")
    return {
        "product": args.get("product") or "",
        "root_lot_id": root_lots[0] if root_lots else "",
        "fab_lot_id": fab_lots[0] if fab_lots else "",
        "lot_id": lot,
        "wafer_id": wafer_ids[0] if wafer_ids else lot,
        "module": module,
        "split_set": split_set,
        "reason": reason,
        "note": note,
        "missing": missing,
    }


def _flowi_save_inform_draft(args: dict[str, Any], entries: list[dict[str, Any]], username: str) -> dict[str, Any]:
    draft_id = _flowi_draft_id()
    state = {
        "kind": "inform_draft",
        "draft_id": draft_id,
        "session_id": draft_id,
        "username": username,
        "product": args.get("product") or "",
        "root_lot_ids": args.get("root_lot_ids") or [],
        "fab_lot_ids": args.get("fab_lot_ids") or [],
        "entries": entries,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return _flowi_save_inform_state(state)


def _flowi_create_inform_records_from_entries(state: dict[str, Any], me: dict[str, Any]) -> list[dict[str, Any]]:
    from routers import informs as informs_router
    username = me.get("username") or state.get("username") or "user"
    product = str(state.get("product") or "").strip()
    records = []
    items = informs_router._load_upgraded()
    now = informs_router._now()
    base_roots = [str(x) for x in (state.get("root_lot_ids") or []) if str(x or "").strip()]
    base_fabs = [str(x) for x in (state.get("fab_lot_ids") or []) if str(x or "").strip()]
    for entry in state.get("entries") or []:
        if not isinstance(entry, dict) or entry.get("missing"):
            continue
        lot = str(entry.get("lot_id") or entry.get("fab_lot_id") or entry.get("root_lot_id") or (base_fabs[0] if base_fabs else (base_roots[0] if base_roots else ""))).strip()
        wafer = str(entry.get("wafer_id") or lot).strip()
        root_lot = str(entry.get("root_lot_id") or (base_roots[0] if base_roots else "") or informs_router._root_lot_from_values(lot)).strip()
        fab_snapshot = (
            str(entry.get("fab_lot_id") or (base_fabs[0] if base_fabs else "")).strip()
            or informs_router._resolve_fab_lot_snapshot(product, lot or root_lot, wafer)
        )
        text = str(entry.get("note") or "").strip()
        if not text:
            bits = [str(entry.get("module") or "").strip()]
            if entry.get("split_set"):
                bits.append(f"split={entry.get('split_set')}")
            text = " ".join([b for b in bits if b]).strip() or "Flow-i 인폼"
        rec = {
            "id": informs_router._new_id(),
            "parent_id": None,
            "wafer_id": wafer,
            "lot_id": lot or root_lot,
            "root_lot_id": root_lot or informs_router._root_lot_from_values(lot),
            "product": product or str(entry.get("product") or ""),
            "module": str(entry.get("module") or "").strip(),
            "reason": str(entry.get("reason") or entry.get("split_set") or "Flow-i 인폼").strip(),
            "text": text,
            "author": username,
            "created_at": now,
            "checked": False,
            "checked_by": "",
            "checked_at": "",
            "flow_status": "received",
            "status_history": [{"status": "received", "actor": username, "at": now, "note": "created by Flow-i confirm"}],
            "splittable_change": None,
            "images": [],
            "embed_table": None,
            "auto_generated": False,
            "group_ids": [],
            "fab_lot_id_at_save": fab_snapshot,
        }
        items.append(rec)
        records.append({"id": rec["id"], "module": rec["module"], "lot_id": rec["lot_id"], "root_lot_id": rec["root_lot_id"]})
    if records:
        informs_router._save(items)
    return records


def _flowi_confirm_inform_draft(draft_id: str, confirm: bool, me: dict[str, Any]) -> dict[str, Any]:
    state = _flowi_load_inform_state(draft_id)
    if not confirm:
        return {
            "handled": True,
            "intent": "inform_log_cancelled",
            "action": "cancel_inform_draft",
            "answer": "인폼 등록을 취소했습니다. 저장된 인폼은 없습니다.",
            "feature": "inform",
            "draft_id": draft_id,
        }
    missing_entries = [e for e in (state.get("entries") or []) if isinstance(e, dict) and e.get("missing")]
    if missing_entries:
        return {
            "handled": True,
            "intent": "inform_log_confirm_blocked",
            "action": "confirm_inform_draft",
            "blocked": True,
            "answer": "누락 항목이 있어 등록하지 않았습니다. module/split/note 선택지를 먼저 보완해 주세요.",
            "feature": "inform",
            "draft_id": draft_id,
            "entries": state.get("entries") or [],
        }
    records = _flowi_create_inform_records_from_entries(state, me)
    cols_out = ["id", "module", "lot_id", "root_lot_id"]
    return {
        "handled": True,
        "intent": "inform_log_registered",
        "action": "confirm_inform_draft",
        "answer": f"인폼 {len(records)}건을 등록했습니다.",
        "feature": "inform",
        "created_records": records,
        "table": {"kind": "inform_log_registered", "title": "Registered inform logs", "placement": "below", "columns": _table_columns(cols_out), "rows": records, "total": len(records)},
    }


def _extract_flowi_inform_confirm(prompt: str) -> dict[str, Any] | None:
    text = str(prompt or "").strip()
    if not text.startswith(_FLOWI_INFORM_CONFIRM_MARKER):
        return None
    raw = text[len(_FLOWI_INFORM_CONFIRM_MARKER):].strip()
    try:
        data = json.loads(raw)
    except Exception:
        return {"_parse_error": "invalid JSON"}
    return data if isinstance(data, dict) else {"_parse_error": "invalid JSON"}


def _handle_flowi_register_inform_log(prompt: str, product: str, max_rows: int, me: dict[str, Any], allowed_keys: set[str] | None = None) -> dict[str, Any]:
    if _flowi_inform_summary_intent(prompt):
        return {"handled": False}
    payload = _extract_flowi_inform_confirm(prompt)
    if payload is not None:
        if payload.get("_parse_error"):
            return {"handled": True, "intent": "inform_log_confirm_failed", "blocked": True, "answer": "인폼 확인 payload를 읽지 못했습니다.", "feature": "inform"}
        return _flowi_confirm_inform_draft(str(payload.get("draft_id") or ""), bool(payload.get("confirm")), me)
    preview = _structure_flowi_function_call(prompt, product=product, max_rows=max_rows)
    if ((preview.get("selected_function") or {}).get("name") != "register_inform_log"):
        return {"handled": False}
    if allowed_keys is not None and "inform" not in allowed_keys:
        return _flowi_permission_block("inform", me)
    args = ((preview.get("function_call") or {}).get("function") or {}).get("arguments") or {}
    missing = list((preview.get("validation") or {}).get("missing") or [])
    if missing:
        return _flowi_preview_tool(preview, answer="인폼 등록 초안에 필요한 값을 선택해 주세요.")
    raw_entries = args.get("entries") if isinstance(args.get("entries"), list) else []
    if raw_entries:
        entries = [_flowi_inform_entry_preview(args, e if isinstance(e, dict) else {}) for e in raw_entries]
    else:
        entries = [_flowi_inform_entry_preview(args, {})]
    draft = _flowi_save_inform_draft(args, entries, me.get("username") or "user")
    first_entry = entries[0] if entries else {}
    mail_preview = _flowi_build_mail_preview_for_draft({
        "id": draft.get("draft_id"),
        "product": args.get("product") or product,
        "module": first_entry.get("module") or "",
        "reason": first_entry.get("reason") or "",
        "text": first_entry.get("note") or "",
        "root_lot_id": first_entry.get("root_lot_id") or "",
        "lot_id": first_entry.get("lot_id") or first_entry.get("root_lot_id") or "",
        "wafer_id": first_entry.get("lot_id") or first_entry.get("root_lot_id") or "",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "fab_lot_id_at_save": first_entry.get("fab_lot_id") or "",
    }, username=me.get("username") or "user") if first_entry.get("module") else {}
    cols_out = ["product", "root_lot_id", "fab_lot_id", "lot_id", "module", "split_set", "reason", "note", "missing"]
    confirm_payload = {"draft_id": draft.get("draft_id"), "confirm": True}
    cancel_payload = {"draft_id": draft.get("draft_id"), "confirm": False}
    missing_entry_count = sum(1 for e in entries if e.get("missing"))
    answer = f"인폼 {len(entries)}건을 등록 전 미리보기로 만들었습니다. 확인 전에는 저장하지 않습니다."
    if missing_entry_count:
        answer += f" 누락 항목 {missing_entry_count}건은 보완이 필요합니다."
    return {
        "handled": True,
        "intent": "inform_log_batch_draft" if len(entries) > 1 else "inform_log_draft",
        "action": "register_inform_log",
        "answer": answer,
        "feature": "inform",
        "requires_confirmation": True,
        "side_effect": "confirm_before_write",
        "draft_id": draft.get("draft_id"),
        "arguments": args,
        "inform_preview": entries,
        "mail_preview": mail_preview,
        "arguments_choices": _flowi_arguments_choices(["module"], prompt, args) if missing_entry_count else {},
        "clarification": {
            "question": "이대로 인폼을 등록할까요?",
            "choices": [
                {
                    "id": "confirm_inform",
                    "label": "1",
                    "title": "등록",
                    "recommended": True,
                    "description": f"{len(entries)}건을 실제 인폼 로그로 저장합니다.",
                    "prompt": f"{_FLOWI_INFORM_CONFIRM_MARKER} {json.dumps(confirm_payload, ensure_ascii=False)}",
                },
                {
                    "id": "cancel_inform",
                    "label": "2",
                    "title": "취소",
                    "description": "저장하지 않습니다.",
                    "prompt": f"{_FLOWI_INFORM_CONFIRM_MARKER} {json.dumps(cancel_payload, ensure_ascii=False)}",
                },
            ],
        } if not missing_entry_count else {},
        "table": {"kind": "inform_log_draft", "title": "Inform log draft", "placement": "below", "columns": _table_columns(cols_out), "rows": [{k: (", ".join(v) if isinstance(v, list) else v) for k, v in e.items() if k in cols_out} for e in entries], "total": len(entries)},
    }


def _flowi_active_walkthrough_session(agent_context: dict[str, Any] | None) -> str:
    for msg in reversed(_flowi_context_messages(agent_context)):
        slots = msg.get("slots") if isinstance(msg.get("slots"), dict) else {}
        sid = slots.get("session_id") or slots.get("inform_session_id")
        if sid:
            return str(sid)
        workflow = msg.get("workflow_state") if isinstance(msg.get("workflow_state"), dict) else {}
        wslots = workflow.get("slots") if isinstance(workflow.get("slots"), dict) else {}
        sid = wslots.get("session_id") or wslots.get("inform_session_id")
        if sid:
            return str(sid)
    return ""


def _flowi_walkthrough_response(state: dict[str, Any], answer: str = "") -> dict[str, Any]:
    current = str(state.get("current_module") or "")
    entries = state.get("entries") if isinstance(state.get("entries"), list) else []
    remaining = state.get("modules_remaining") if isinstance(state.get("modules_remaining"), list) else []
    choices = _flowi_split_set_choice_values(3)
    tool = {
        "handled": True,
        "intent": "inform_walkthrough",
        "action": "register_inform_walkthrough",
        "answer": answer or (f"{current}는 뭘로 할까요?" if current else f"현재 {len(entries)}개 entry가 있습니다. 이대로 등록할까요?"),
        "feature": "inform",
        "requires_confirmation": True,
        "side_effect": "confirm_before_write",
        "session_id": state.get("session_id"),
        "walkthrough": {
            "session_id": state.get("session_id"),
            "current_module": current,
            "entries": entries,
            "modules_remaining": remaining,
            "next_question": f"{current}는 뭘로 할까요?" if current else "이대로 등록할까요?",
        },
        "slots": {
            "session_id": state.get("session_id"),
            "product": state.get("product") or "",
            "root_lot_ids": state.get("root_lot_ids") or [],
            "current_module": current,
        },
        "arguments_choices": {
            "message": "또는 직접 입력해 주세요",
            "fields": [{
                "field": "split_set",
                "choices": [
                    _flowi_choice("split_set", i + 1, f"{v}로 진행", v, prompt_prefix="")
                    for i, v in enumerate(choices)
                ] + [{"id": "free", "label": "직접", "title": "직접 입력", "value": "", "free_input": True, "description": "split/note를 자유 입력합니다.", "prompt": ""}],
            }],
        } if current else {},
    }
    if not current and entries:
        payload = {"session_id": state.get("session_id"), "confirm": True}
        tool["clarification"] = {
            "question": f"현재 {len(entries)}개 entry를 등록할까요?",
            "choices": [{
                "id": "confirm_walkthrough",
                "label": "1",
                "title": "등록",
                "recommended": True,
                "description": "현재 entry를 일괄 등록합니다.",
                "prompt": f"{_FLOWI_INFORM_WALKTHROUGH_MARKER} {json.dumps(payload, ensure_ascii=False)}",
            }],
        }
    return tool


def _flowi_start_walkthrough(args: dict[str, Any], me: dict[str, Any]) -> dict[str, Any]:
    modules = _flowi_inform_modules()
    state = {
        "kind": "inform_walkthrough",
        "session_id": "walk_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:8],
        "username": me.get("username") or "user",
        "root_lot_ids": args.get("root_lot_ids") or [],
        "product": args.get("product") or "",
        "modules_remaining": modules[1:],
        "current_module": modules[0] if modules else "",
        "entries": [],
    }
    _flowi_save_inform_state(state)
    return _flowi_walkthrough_response(state, f"{state['current_module']}는 뭘로 할까요? (예: test1)")


def _flowi_walkthrough_next_module(state: dict[str, Any]) -> None:
    remaining = list(state.get("modules_remaining") or [])
    state["current_module"] = remaining.pop(0) if remaining else ""
    state["modules_remaining"] = remaining


def _flowi_resolve_walkthrough_state(state: dict[str, Any], prompt: str, me: dict[str, Any]) -> dict[str, Any]:
    text = str(prompt or "").strip()
    low = text.lower()
    if not state.get("current_module") and any(t in text for t in ("응", "등록", "확인", "이대로")):
        tmp_state = dict(state)
        tmp_state["kind"] = "inform_draft"
        tmp_state["draft_id"] = str(state.get("session_id") or "")
        return _flowi_confirm_inform_draft(str(state.get("session_id") or ""), True, me)
    if any(t in low or t in text for t in ("끝", "그만", "이대로 등록", "finalize")):
        state["current_module"] = ""
        state["modules_remaining"] = []
        _flowi_save_inform_state(state)
        return _flowi_walkthrough_response(state, f"현재 {len(state.get('entries') or [])}개 entry입니다. 이대로 등록할까요?")
    jump_module = ""
    for module, alias in _flowi_module_alias_pairs():
        if re.search(rf"{re.escape(alias)}\s*도", text, flags=re.I):
            jump_module = module
            break
    if jump_module:
        state["current_module"] = jump_module
    current = str(state.get("current_module") or "")
    if not current:
        return _flowi_walkthrough_response(state)
    note = _flowi_note_extract(text)
    split_values = []
    split = _flowi_split_set_token(text)
    if split:
        split_values.append(split)
    else:
        clean = re.sub(r"(로|으로)?\s*(해줘|해주세요|할게|진행|선택).*", "", text).strip()
        clean = re.sub(r"(그리고|,|/)", " ", clean)
        for tok in clean.split():
            if tok and tok not in {"이건", "일단", "생략할게", "넘어가", "안", "해"} and not _flowi_module_token(tok):
                split_values.append(tok.strip())
    if any(t in low or t in text for t in ("생략", "skip", "넘어가", "안 해", "안해")) and not split_values:
        _flowi_walkthrough_next_module(state)
        _flowi_save_inform_state(state)
        return _flowi_walkthrough_response(state)
    for split_val in split_values[:4]:
        state.setdefault("entries", []).append({
            "product": state.get("product") or "",
            "root_lot_id": (state.get("root_lot_ids") or [""])[0],
            "lot_id": (state.get("root_lot_ids") or [""])[0],
            "module": current,
            "split_set": split_val,
            "reason": split_val,
            "note": note,
            "missing": [],
        })
    if split_values:
        _flowi_walkthrough_next_module(state)
    _flowi_save_inform_state(state)
    return _flowi_walkthrough_response(state)


def _extract_flowi_walkthrough_payload(prompt: str) -> dict[str, Any] | None:
    text = str(prompt or "").strip()
    if not text.startswith(_FLOWI_INFORM_WALKTHROUGH_MARKER):
        return None
    try:
        data = json.loads(text[len(_FLOWI_INFORM_WALKTHROUGH_MARKER):].strip())
    except Exception:
        return {"_parse_error": "invalid JSON"}
    return data if isinstance(data, dict) else {"_parse_error": "invalid JSON"}


def _handle_flowi_inform_walkthrough_chat(prompt: str, product: str, max_rows: int, me: dict[str, Any], agent_context: dict[str, Any] | None = None, allowed_keys: set[str] | None = None) -> dict[str, Any]:
    if allowed_keys is not None and "inform" not in allowed_keys:
        preview = _structure_flowi_function_call(prompt, product=product, max_rows=max_rows)
        if ((preview.get("selected_function") or {}).get("name") == "register_inform_walkthrough"):
            return _flowi_permission_block("inform", me)
    payload = _extract_flowi_walkthrough_payload(prompt)
    if payload is not None:
        if payload.get("_parse_error"):
            return {"handled": True, "intent": "inform_walkthrough_failed", "blocked": True, "answer": "walkthrough 확인 payload를 읽지 못했습니다.", "feature": "inform"}
        sid = str(payload.get("session_id") or "")
        if payload.get("confirm"):
            return _flowi_confirm_inform_draft(sid, True, me)
        state = _flowi_load_inform_state(sid)
        return _flowi_resolve_walkthrough_state(state, str(payload.get("value") or ""), me)
    active_sid = _flowi_active_walkthrough_session(agent_context)
    if active_sid:
        state = _flowi_load_inform_state(active_sid)
        if state.get("kind") == "inform_walkthrough":
            return _flowi_resolve_walkthrough_state(state, prompt, me)
    preview = _structure_flowi_function_call(prompt, product=product, max_rows=max_rows)
    if ((preview.get("selected_function") or {}).get("name") != "register_inform_walkthrough"):
        return {"handled": False}
    args = ((preview.get("function_call") or {}).get("function") or {}).get("arguments") or {}
    if (preview.get("validation") or {}).get("missing"):
        return _flowi_preview_tool(preview, answer="인폼 전체 작성에 필요한 root lot을 알려주세요.")
    return _flowi_start_walkthrough(args, me)


def _handle_flowi_query(
    prompt: str,
    product: str = "",
    max_rows: int = 12,
    allowed_keys: set[str] | None = None,
) -> dict:
    product = _product_hint(prompt, product)
    if allowed_keys is None or {"filebrowser", "dashboard", "splittable", "ettime", "waferlayout"} & set(allowed_keys):
        fab_lot_out = _handle_current_fab_lot_lookup(prompt, product, max_rows)
        if fab_lot_out.get("handled"):
            return fab_lot_out
        metric_step_out = _handle_metric_at_step(prompt, product, max_rows)
        if metric_step_out.get("handled"):
            return metric_step_out
        wafer_split_out = _handle_wafer_split_at_step(prompt, product, max_rows)
        if wafer_split_out.get("handled"):
            return wafer_split_out
        knob_value_out = _handle_find_lots_by_knob_value(prompt, product, max_rows)
        if knob_value_out.get("handled"):
            return knob_value_out
        for handler in (_handle_teg_radius_lookup, _handle_teg_position_lookup, _handle_wafer_map_chart, _handle_wafer_map_similarity):
            wafer_out = handler(prompt, product, max_rows)
            if wafer_out.get("handled"):
                return wafer_out
        for handler in (_handle_split_fab_lot_basis, _handle_fab_corun_lots, _handle_knob_clean_interference, _handle_lot_anomaly_summary):
            ops_out = handler(prompt, product, max_rows)
            if ops_out.get("handled"):
                return ops_out
        eta_out = _handle_fab_step_eta(prompt, product, max_rows)
        if eta_out.get("handled"):
            return eta_out
        fab_eqp_out = _handle_fab_eqp_lookup(prompt, product, max_rows)
        if fab_eqp_out.get("handled"):
            return fab_eqp_out
        process_out = _handle_product_process_id_lookup(prompt, product, max_rows)
        if process_out.get("handled"):
            return process_out
        for handler in (_handle_inline_item_lookup, _handle_ppid_knob_lookup, _handle_index_form_lookup):
            meta_out = handler(prompt, product, max_rows)
            if meta_out.get("handled"):
                return meta_out
        fab_progress_out = _handle_fab_progress_query(prompt, product, max_rows)
        if fab_progress_out.get("handled"):
            return fab_progress_out
    if allowed_keys is None or "diagnosis" in allowed_keys:
        diag_out = _handle_semiconductor_diagnosis_query(prompt, product, max_rows)
        if diag_out.get("handled"):
            return diag_out
    if allowed_keys is None or "dashboard" in allowed_keys:
        box_chart_out = _handle_inline_box_chart(prompt, product, max_rows)
        if box_chart_out.get("handled"):
            return box_chart_out
        trend_chart_out = _handle_inline_trend_chart(prompt, product, max_rows)
        if trend_chart_out.get("handled"):
            return trend_chart_out
        grouped_chart_out = _handle_grouped_metric_chart(prompt, product, max_rows)
        if grouped_chart_out.get("handled"):
            return grouped_chart_out
        chart_out = _handle_chart_request(prompt, product, max_rows)
        if chart_out.get("handled"):
            return chart_out
    if allowed_keys is None or "splittable" in allowed_keys:
        fastest_out = _handle_fastest_knob_query(prompt, product, max_rows)
        if fastest_out.get("handled"):
            return fastest_out
    if allowed_keys is None or "ettime" in allowed_keys or "dashboard" in allowed_keys or "filebrowser" in allowed_keys:
        for handler in (_handle_et_report_freshness, _handle_et_report_lookup, _handle_measurement_duration_lookup):
            out = handler(prompt, product, max_rows)
            if out.get("handled"):
                return out
    if allowed_keys is None or "filebrowser" in allowed_keys:
        preview_out = _handle_filebrowser_data_preview(prompt, product, max_rows)
        if preview_out.get("handled"):
            return preview_out
        schema_out = _handle_filebrowser_schema_search(prompt, product, max_rows)
        if schema_out.get("handled"):
            return schema_out
    if allowed_keys is None or "filebrowser" in allowed_keys or "splittable" in allowed_keys:
        table_out = _handle_value_table_query(prompt, product, max_rows)
        if table_out.get("handled"):
            return table_out
    pre_matches = _matched_feature_entrypoints(prompt, limit=3, allowed_keys=allowed_keys)
    if pre_matches and pre_matches[0].get("key") not in {"ettime", "splittable"}:
        return _unit_feature_guidance(prompt, product, max_rows=max_rows, allowed_keys=allowed_keys)
    for handler in (_handle_et_query, _handle_knob_query):
        if handler is _handle_et_query and allowed_keys is not None and "ettime" not in allowed_keys:
            continue
        if handler is _handle_knob_query and allowed_keys is not None and "splittable" not in allowed_keys:
            continue
        out = handler(prompt, product, max_rows)
        if out.get("handled"):
            return out
    routed = _unit_feature_guidance(prompt, product, max_rows=max_rows, allowed_keys=allowed_keys)
    if routed.get("feature_entrypoints"):
        return routed
    return {
        "handled": False,
        "intent": "general",
        "answer": (
            "Flowi local tools는 현재 ET wafer별 median 조회와 lot KNOB 요약을 우선 지원합니다.\n"
            "예: `ET ETA100010 VTH median wf별`, `A1000 knob 어떻게돼`"
        ),
    }


def _clean_source_ai(raw: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(raw or "").strip())
    return text.strip("._:-")[:64] or "external"


def _json_excerpt(value: Any, limit: int = 4000) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)[:limit]
    except Exception:
        return str(value or "")[:limit]


def _flowi_agent_actions(tool: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    entries = tool.get("feature_entrypoints") or []
    if isinstance(entries, list):
        for item in entries[:3]:
            if not isinstance(item, dict) or not item.get("key"):
                continue
            actions.append({
                "type": "open_tab",
                "tab": item.get("key"),
                "title": item.get("title") or item.get("key"),
                "description": item.get("description") or "",
            })
    unit_action = tool.get("action")
    if unit_action:
        actions.append({
            "type": "flowi_unit_action",
            "action": unit_action,
            "intent": tool.get("intent") or "",
            "slots": tool.get("slots") or {},
            "filters": tool.get("filters") or {},
        })
    return actions


def _flowi_output_summary(tool: dict[str, Any]) -> dict[str, Any]:
    table = tool.get("table") if isinstance(tool.get("table"), dict) else {}
    chart = tool.get("chart_result") if isinstance(tool.get("chart_result"), dict) else (tool.get("chart") if isinstance(tool.get("chart"), dict) else {})
    aux_tables = []
    for key, value in tool.items():
        if key == "table" or not key.endswith("_table") or not isinstance(value, dict):
            continue
        aux_tables.append({
            "key": key,
            "kind": value.get("kind") or "",
            "total": value.get("total", len(value.get("rows") or [])),
        })
    return {
        "table": {
            "kind": table.get("kind") or "",
            "total": table.get("total", len(table.get("rows") or [])) if table else 0,
            "title": table.get("title") or "",
        } if table else {},
        "chart": {
            "kind": chart.get("kind") or chart.get("status") or "",
            "status": chart.get("status") or "",
            "title": chart.get("title") or "",
        } if chart else {},
        "aux_tables": aux_tables[:4],
        "has_rows": bool(tool.get("rows")),
        "has_knobs": bool(tool.get("knobs")),
    }


def _flowi_waiting_for(tool: dict[str, Any]) -> str:
    if tool.get("blocked"):
        return "permission_or_policy"
    if tool.get("requires_confirmation"):
        return "user_confirmation"
    clarification = tool.get("clarification") if isinstance(tool.get("clarification"), dict) else {}
    if clarification.get("choices"):
        return "user_choice"
    if not tool.get("handled"):
        return "more_context"
    return ""


def _flowi_workflow_status(tool: dict[str, Any]) -> str:
    if tool.get("blocked"):
        return "blocked"
    waiting = _flowi_waiting_for(tool)
    if waiting == "user_confirmation":
        return "awaiting_confirmation"
    if waiting == "user_choice":
        return "awaiting_choice"
    if waiting == "more_context":
        return "needs_more_context"
    return "ready"


def _flowi_next_actions(tool: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    clarification = tool.get("clarification") if isinstance(tool.get("clarification"), dict) else {}
    choices = clarification.get("choices") if isinstance(clarification.get("choices"), list) else []
    for i, choice in enumerate(choices[:3]):
        if not isinstance(choice, dict):
            continue
        actions.append({
            "type": "respond_with_prompt",
            "id": choice.get("id") or f"choice_{i + 1}",
            "label": choice.get("label") or str(i + 1),
            "title": choice.get("title") or choice.get("label") or f"선택 {i + 1}",
            "description": choice.get("description") or "",
            "prompt": choice.get("prompt") or choice.get("title") or "",
            "recommended": bool(choice.get("recommended")),
            "requires_user": True,
        })
    if tool.get("requires_confirmation") and not choices:
        actions.append({
            "type": "confirm_required",
            "id": "confirm",
            "title": "확인 필요",
            "description": "실제 저장/변경 전에 전용 확인 플로우가 필요합니다.",
            "requires_user": True,
        })
    entries = tool.get("feature_entrypoints") if isinstance(tool.get("feature_entrypoints"), list) else []
    for entry in entries[:3]:
        if not isinstance(entry, dict) or not entry.get("key"):
            continue
        actions.append({
            "type": "open_tab",
            "id": f"open_{entry.get('key')}",
            "tab": entry.get("key"),
            "title": f"{entry.get('title') or entry.get('key')} 열기",
            "description": entry.get("description") or "",
            "requires_user": False,
        })
    if isinstance(tool.get("table"), dict) and (tool.get("table") or {}).get("rows"):
        actions.append({
            "type": "inspect_table",
            "id": "inspect_table",
            "title": "표 확인",
            "description": f"{(tool.get('table') or {}).get('kind') or 'result'} 결과를 홈 화면에서 확인합니다.",
            "requires_user": False,
        })
    if isinstance(tool.get("samples_table"), dict) and (tool.get("samples_table") or {}).get("rows"):
        actions.append({
            "type": "inspect_aux_table",
            "id": "inspect_samples",
            "title": "근거 sample 확인",
            "description": "ETA/집계 계산에 사용된 sample table을 확인합니다.",
            "requires_user": False,
        })
    if isinstance(tool.get("chart_result"), dict) or isinstance(tool.get("chart"), dict):
        actions.append({
            "type": "render_chart",
            "id": "render_chart",
            "title": "차트 확인",
            "description": "홈 Flow-i 기본 차트 preset으로 렌더링합니다.",
            "requires_user": False,
        })
    if not actions and not tool.get("blocked"):
        actions.append({
            "type": "follow_up_prompt",
            "id": "follow_up",
            "title": "후속 조건 입력",
            "description": "product, lot, wafer, step, item 중 빠진 조건을 추가해 이어서 질문합니다.",
            "requires_user": True,
        })
    return actions[:8]


def _limit_flowi_choices(tool: dict[str, Any], limit: int = 3) -> dict[str, Any]:
    clarification = tool.get("clarification") if isinstance(tool.get("clarification"), dict) else {}
    choices = clarification.get("choices") if isinstance(clarification.get("choices"), list) else []
    if len(choices) <= limit:
        return tool
    trimmed = choices[:max(1, int(limit or 3))]
    clarification = dict(clarification)
    clarification["choices"] = trimmed
    tool["clarification"] = clarification
    return tool


def _flowi_workflow_state(
    tool: dict[str, Any],
    *,
    prompt: str,
    allowed_keys: set[str],
    agent_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    messages = []
    if isinstance(agent_context, dict) and isinstance(agent_context.get("messages"), list):
        messages = agent_context.get("messages") or []
    clarification = tool.get("clarification") if isinstance(tool.get("clarification"), dict) else {}
    choices = clarification.get("choices") if isinstance(clarification.get("choices"), list) else []
    return {
        "version": 1,
        "surface": "home_flowi",
        "status": _flowi_workflow_status(tool),
        "waiting_for": _flowi_waiting_for(tool),
        "intent": tool.get("intent") or "general",
        "action": tool.get("action") or "",
        "feature": tool.get("feature") or "",
        "requires_confirmation": bool(tool.get("requires_confirmation")),
        "blocked": bool(tool.get("blocked")),
        "last_prompt": str(prompt or "")[:500],
        "allowed_features": sorted(allowed_keys),
        "slots": tool.get("slots") if isinstance(tool.get("slots"), dict) else {},
        "filters": tool.get("filters") if isinstance(tool.get("filters"), dict) else {},
        "outputs": _flowi_output_summary(tool),
        "choice_count": len(choices),
        "context_message_count": len(messages),
    }


def _finalize_flowi_tool(
    tool: dict[str, Any],
    *,
    prompt: str,
    allowed_keys: set[str],
    agent_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(tool, dict):
        return tool
    _limit_flowi_choices(tool, 3)
    tool["workflow_state"] = _flowi_workflow_state(tool, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)
    tool["next_actions"] = _flowi_next_actions(tool)
    return tool


def _agent_api_meta(
    *,
    source: str,
    client_run_id: str,
    username: str,
    tool: dict[str, Any],
    agent_context: dict[str, Any],
) -> dict[str, Any]:
    return {
        "received": True,
        "source_ai": source,
        "client_run_id": client_run_id,
        "auth_user": username,
        "read_only": True,
        "actions": _flowi_agent_actions(tool),
        "workflow_state": tool.get("workflow_state") if isinstance(tool.get("workflow_state"), dict) else {},
        "next_actions": tool.get("next_actions") if isinstance(tool.get("next_actions"), list) else [],
        "requires_confirmation": bool(tool.get("requires_confirmation")),
        "clarification": tool.get("clarification") if isinstance(tool.get("clarification"), dict) else {},
        "context_keys": sorted(str(k) for k in agent_context.keys())[:20],
    }


def _event_fields(fields: dict[str, Any], *, source: str = "", client_run_id: str = "") -> dict[str, Any]:
    out = dict(fields)
    if source:
        out["source_ai"] = source
    if client_run_id:
        out["client_run_id"] = client_run_id
    return out


def _flowi_public_trace(
    *,
    prompt: str,
    allowed_keys: set[str],
    result: dict[str, Any],
    agent_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """User-visible execution trace. This is not model chain-of-thought."""
    tool = result.get("tool") if isinstance(result.get("tool"), dict) else {}
    llm = result.get("llm") if isinstance(result.get("llm"), dict) else {}
    context_messages = []
    if isinstance(agent_context, dict):
        raw_msgs = agent_context.get("messages")
        context_messages = raw_msgs if isinstance(raw_msgs, list) else []
    table = tool.get("table") if isinstance(tool.get("table"), dict) else {}
    chart = tool.get("chart_result") if isinstance(tool.get("chart_result"), dict) else (tool.get("chart") if isinstance(tool.get("chart"), dict) else {})
    data_source = tool.get("data_source") if isinstance(tool.get("data_source"), dict) else {}
    source_profile = tool.get("source_profile") if isinstance(tool.get("source_profile"), dict) else {}
    choices = []
    clarification = tool.get("clarification") if isinstance(tool.get("clarification"), dict) else {}
    if isinstance(clarification.get("choices"), list):
        choices = clarification.get("choices") or []

    intent = str(tool.get("intent") or "general")
    action = str(tool.get("action") or tool.get("feature") or "")
    output_bits = []
    if table:
        output_bits.append(f"table {table.get('kind') or ''} rows={table.get('total', len(table.get('rows') or []))}")
    if chart:
        output_bits.append(f"chart {chart.get('kind') or chart.get('status') or 'planned'}")
    if tool.get("rows"):
        output_bits.append(f"rows={len(tool.get('rows') or [])}")
    if tool.get("knobs"):
        output_bits.append(f"knobs={len(tool.get('knobs') or [])}")
    if data_source.get("file"):
        output_bits.append(f"source={data_source.get('file')}")
    elif data_source.get("root"):
        output_bits.append(f"source={data_source.get('root')}/{data_source.get('product') or ''}".rstrip("/"))
    if source_profile:
        output_bits.append("profile=" + _flowi_profile_label(source_profile))
    if choices:
        output_bits.append(f"choices={len(choices)}")
    if not output_bits:
        output_bits.append("answer text")

    guard_status = "blocked" if tool.get("blocked") else "done"
    guard_detail = "차단됨" if tool.get("blocked") else "허용된 단위기능 범위에서 진행"
    if tool.get("blocked") and tool.get("missing_permission"):
        guard_detail = f"권한 없음: {tool.get('missing_permission')}"
    elif tool.get("intent") == "admin_file_operation":
        guard_detail = "admin 파일 작업은 FLOWI_FILE_OP 확인 구조로 제한"
    elif tool.get("intent") == "blocked_write_request":
        guard_detail = "일반 user의 DB/File 원본 수정 요청 차단"

    llm_status = "done" if llm.get("used") else ("error" if llm.get("error") else "skipped")
    if llm.get("blocked"):
        llm_status = "blocked"
    llm_detail = "LLM이 로컬 결과를 짧게 정리" if llm.get("used") else "로컬 단위기능 결과를 그대로 사용"
    if llm.get("error"):
        llm_detail = f"LLM 오류: {llm.get('error')}"
    if llm.get("blocked"):
        llm_detail = "권한/보호 정책으로 LLM 보정 없이 종료"

    steps = [
        {
            "key": "receive",
            "label": "요청 접수",
            "status": "done",
            "detail": f"prompt {len(prompt or '')} chars, context {len(context_messages)} messages",
        },
        {
            "key": "auth",
            "label": "사용자/권한 확인",
            "status": "done",
            "detail": f"허용 기능 {len(allowed_keys)}개",
        },
        {
            "key": "route",
            "label": "의도/단위기능 선택",
            "status": "done",
            "detail": f"intent={intent}" + (f", action={action}" if action else ""),
        },
        {
            "key": "guardrail",
            "label": "권한/쓰기 보호",
            "status": guard_status,
            "detail": guard_detail,
        },
        {
            "key": "tool",
            "label": "DB/cache/tool 실행",
            "status": "skipped" if tool.get("blocked") else "done",
            "detail": ", ".join(output_bits),
        },
        {
            "key": "llm",
            "label": "LLM 답변 정리",
            "status": llm_status,
            "detail": llm_detail,
        },
        {
            "key": "render",
            "label": "화면 출력 준비",
            "status": "done",
            "detail": ", ".join(output_bits),
        },
    ]
    return {
        "kind": "public_execution_trace",
        "visible": True,
        "note": "사고과정 원문이 아니라 사용자가 검증할 수 있는 실행 흐름 요약입니다.",
        "steps": steps,
    }


def _attach_flowi_trace(
    result: dict[str, Any],
    *,
    prompt: str,
    allowed_keys: set[str],
    agent_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tool = result.get("tool") if isinstance(result.get("tool"), dict) else {}
    if tool:
        _finalize_flowi_tool(tool, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)
        result["workflow_state"] = tool.get("workflow_state")
        result["next_actions"] = tool.get("next_actions")
        if isinstance(result.get("agent_api"), dict):
            result["agent_api"]["workflow_state"] = tool.get("workflow_state") or {}
            result["agent_api"]["next_actions"] = tool.get("next_actions") or []
            result["agent_api"]["requires_confirmation"] = bool(tool.get("requires_confirmation"))
            if isinstance(tool.get("clarification"), dict):
                result["agent_api"]["clarification"] = tool.get("clarification")
    result["trace"] = _flowi_public_trace(
        prompt=prompt,
        allowed_keys=allowed_keys,
        result=result,
        agent_context=agent_context,
    )
    return result


_FLOWI_HOME_USER_TOOL_KEYS = {
    "answer",
    "clarification",
    "table",
    "rows",
    "knobs",
    "chart",
    "chart_result",
    "samples_table",
    "module_summary",
    "summary",
    "created_record",
    "created_records",
    "missing",
    "arguments_choices",
    "inform_preview",
    "mail_preview",
    "walkthrough",
    "draft_id",
    "session_id",
    "side_effect",
    "blocked",
    "reject_reason",
    "requires_confirmation",
}


def _flowi_home_response_for_role(result: dict[str, Any], me: dict[str, Any]) -> dict[str, Any]:
    if (me.get("role") or "user") == "admin":
        return result
    if not isinstance(result, dict):
        return result
    out: dict[str, Any] = {
        "ok": bool(result.get("ok", True)),
        "active": bool(result.get("active", True)),
        "answer": result.get("answer") or "",
    }
    if result.get("error"):
        out["error"] = result.get("error")
    tool = result.get("tool") if isinstance(result.get("tool"), dict) else {}
    public_tool = {key: deepcopy(tool[key]) for key in _FLOWI_HOME_USER_TOOL_KEYS if key in tool}
    clarification = public_tool.get("clarification") if isinstance(public_tool.get("clarification"), dict) else {}
    choices = clarification.get("choices") if isinstance(clarification.get("choices"), list) else []
    if choices:
        safe_choices = []
        for choice in choices[:3]:
            if not isinstance(choice, dict):
                continue
            safe = {
                key: choice.get(key)
                for key in ("id", "label", "title", "description", "prompt", "tab", "feature", "recommended")
                if key in choice
            }
            safe_choices.append(safe)
        public_tool["clarification"] = {
            "question": clarification.get("question") or "확인이 필요합니다.",
            "choices": safe_choices,
        }
    if public_tool:
        out["tool"] = public_tool
    return out


def _run_flowi_chat(
    *,
    prompt: str,
    product: str,
    max_rows: int,
    me: dict[str, Any],
    source_ai: str = "",
    client_run_id: str = "",
    agent_context: dict[str, Any] | None = None,
    allow_rag_update: bool = False,
) -> dict[str, Any]:
    username = me.get("username") or "user"
    prompt = (prompt or "").strip()
    if not prompt:
        raise HTTPException(400, "질문을 입력해주세요")

    source = _clean_source_ai(source_ai) if source_ai else ""
    client_run_id = str(client_run_id or "").strip()[:120]
    agent_context = agent_context if isinstance(agent_context, dict) else {}

    allowed_keys = _allowed_flowi_feature_keys(me)
    admin_block = _flowi_home_admin_function_block(prompt)
    if admin_block.get("handled"):
        answer = admin_block["answer"]
        result = {
            "ok": True,
            "active": True,
            "user": username,
            "answer": answer,
            "tool": admin_block,
            "llm": {"available": llm_adapter.is_available(), "used": False, "blocked": True},
            "allowed_features": sorted(allowed_keys),
        }
        return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)
    all_entries = _matched_feature_entrypoints(prompt)
    if all_entries and all_entries[0].get("key") not in allowed_keys:
        tool = _flowi_permission_block(all_entries[0].get("key") or "", me)
        answer = tool["answer"]
        _append_user_event(username, "blocked_permission_request", _event_fields(
            {"prompt": prompt, "feature": tool.get("feature"), "answer": answer},
            source=source,
            client_run_id=client_run_id,
        ))
        result = {
            "ok": True,
            "active": True,
            "user": username,
            "answer": answer,
            "tool": tool,
            "llm": {"available": llm_adapter.is_available(), "used": False, "blocked": True},
            "allowed_features": sorted(allowed_keys),
        }
        if source:
            result["agent_api"] = _agent_api_meta(
                source=source,
                client_run_id=client_run_id,
                username=username,
                tool=tool,
                agent_context=agent_context,
            )
        return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    if _is_rag_update_prompt(prompt):
        if "diagnosis" not in allowed_keys:
            tool = _flowi_permission_block("diagnosis", me)
            answer = tool["answer"]
        elif not allow_rag_update:
            answer = (
                "[flow-i update] 지식 등록은 홈 Flow-i 채팅에서 처리하지 않습니다.\n"
                "에이전트 페이지의 `RAG 반영` 화면에서 문서 타입 지식 등록, 빠른 RAG Update, 표 지식 반영 중 하나로 저장해주세요.\n"
                "홈에서는 일반 질의와 답변 피드백만 받습니다."
            )
            tool = {
                "handled": True,
                "intent": "semiconductor_rag_update",
                "action": "blocked_home_rag_update",
                "blocked": True,
                "answer": answer,
                "feature": "diagnosis",
                "feature_entrypoints": [
                    {"key": "diagnosis", "title": "에이전트", "description": "RAG 반영 화면에서 지식 등록"}
                ],
            }
        else:
            tool = _handle_flowi_rag_update(prompt, me)
            answer = tool.get("answer") or "Flow-i RAG Update를 처리했습니다."
        _append_user_event(username, "semiconductor_rag_update", _event_fields(
            {
                "prompt": prompt,
                "intent": tool.get("intent") or "",
                "action": tool.get("action") or "",
                "answer": answer,
            },
            source=source,
            client_run_id=client_run_id,
        ))
        result = {
            "ok": True,
            "active": True,
            "user": username,
            "answer": answer,
            "tool": tool,
            "llm": {"available": llm_adapter.is_available(), "used": False, "blocked": bool(tool.get("blocked"))},
            "allowed_features": sorted(allowed_keys),
        }
        if source:
            result["agent_api"] = _agent_api_meta(
                source=source,
                client_run_id=client_run_id,
                username=username,
                tool=tool,
                agent_context=agent_context,
            )
        return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    if "diagnosis" in allowed_keys:
        prep_tool = _handle_flowi_admin_semiconductor_file_prep(prompt, product, me)
        if prep_tool.get("handled"):
            answer = prep_tool.get("answer") or "반도체 지식/reformatter/YAML 준비 작업을 처리했습니다."
            _append_user_event(username, "semiconductor_admin_file_prep", _event_fields(
                {
                    "prompt": prompt,
                    "intent": prep_tool.get("intent") or "",
                    "action": prep_tool.get("action") or "",
                    "answer": answer,
                },
                source=source,
                client_run_id=client_run_id,
            ))
            result = {
                "ok": True,
                "active": True,
                "user": username,
                "answer": answer,
                "tool": prep_tool,
                "llm": {"available": llm_adapter.is_available(), "used": False, "blocked": False},
                "allowed_features": sorted(allowed_keys),
            }
            if source:
                result["agent_api"] = _agent_api_meta(
                    source=source,
                    client_run_id=client_run_id,
                    username=username,
                    tool=prep_tool,
                    agent_context=agent_context,
            )
            return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    walkthrough_tool = _handle_flowi_inform_walkthrough_chat(prompt, product, max_rows, me, agent_context=agent_context, allowed_keys=allowed_keys)
    if walkthrough_tool.get("handled"):
        answer = walkthrough_tool.get("answer") or "인폼 전체 작성 흐름을 진행합니다."
        _append_user_event(username, "inform_walkthrough", _event_fields(
            {"prompt": prompt, "intent": walkthrough_tool.get("intent") or "", "answer": answer},
            source=source,
            client_run_id=client_run_id,
        ))
        result = {
            "ok": True,
            "active": True,
            "user": username,
            "answer": answer,
            "tool": walkthrough_tool,
            "llm": {"available": llm_adapter.is_available(), "used": False, "blocked": bool(walkthrough_tool.get("blocked"))},
            "allowed_features": sorted(allowed_keys),
        }
        if source:
            result["agent_api"] = _agent_api_meta(
                source=source,
                client_run_id=client_run_id,
                username=username,
                tool=walkthrough_tool,
                agent_context=agent_context,
            )
        return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    mail_tool = _handle_compose_inform_module_mail(prompt, product, max_rows, me=me)
    if mail_tool.get("handled"):
        if "inform" not in allowed_keys:
            mail_tool = _flowi_permission_block("inform", me)
        answer = mail_tool.get("answer") or "모듈 인폼 메일 미리보기를 만들었습니다."
        _append_user_event(username, "inform_mail_preview", _event_fields(
            {"prompt": prompt, "intent": mail_tool.get("intent") or "", "answer": answer},
            source=source,
            client_run_id=client_run_id,
        ))
        result = {
            "ok": True,
            "active": True,
            "user": username,
            "answer": answer,
            "tool": mail_tool,
            "llm": {"available": llm_adapter.is_available(), "used": False, "blocked": bool(mail_tool.get("blocked"))},
            "allowed_features": sorted(allowed_keys),
        }
        if source:
            result["agent_api"] = _agent_api_meta(
                source=source,
                client_run_id=client_run_id,
                username=username,
                tool=mail_tool,
                agent_context=agent_context,
            )
        return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    inform_draft_tool = _handle_flowi_register_inform_log(prompt, product, max_rows, me, allowed_keys=allowed_keys)
    if inform_draft_tool.get("handled"):
        answer = inform_draft_tool.get("answer") or "인폼 등록 초안을 만들었습니다."
        _append_user_event(username, "inform_log_draft", _event_fields(
            {"prompt": prompt, "intent": inform_draft_tool.get("intent") or "", "answer": answer},
            source=source,
            client_run_id=client_run_id,
        ))
        result = {
            "ok": True,
            "active": True,
            "user": username,
            "answer": answer,
            "tool": inform_draft_tool,
            "llm": {"available": llm_adapter.is_available(), "used": False, "blocked": bool(inform_draft_tool.get("blocked"))},
            "allowed_features": sorted(allowed_keys),
        }
        if source:
            result["agent_api"] = _agent_api_meta(
                source=source,
                client_run_id=client_run_id,
                username=username,
                tool=inform_draft_tool,
                agent_context=agent_context,
            )
        return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    missing_followup_tool = _handle_app_write_missing_followup(prompt, me, agent_context, allowed_keys=allowed_keys)
    if missing_followup_tool.get("handled"):
        answer = missing_followup_tool.get("answer") or "부족한 값을 반영해 등록 요청을 처리했습니다."
        _append_user_event(username, "app_write_missing_followup", _event_fields(
            {
                "prompt": prompt,
                "intent": missing_followup_tool.get("intent") or "",
                "feature": missing_followup_tool.get("feature") or "",
                "answer": answer,
            },
            source=source,
            client_run_id=client_run_id,
        ))
        result = {
            "ok": True,
            "active": True,
            "user": username,
            "answer": answer,
            "tool": missing_followup_tool,
            "llm": {"available": llm_adapter.is_available(), "used": False, "blocked": bool(missing_followup_tool.get("blocked"))},
            "allowed_features": sorted(allowed_keys),
        }
        if source:
            result["agent_api"] = _agent_api_meta(
                source=source,
                client_run_id=client_run_id,
                username=username,
                tool=missing_followup_tool,
                agent_context=agent_context,
            )
        return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    status_tool = _handle_app_write_status_followup(prompt, me, agent_context, allowed_keys=allowed_keys)
    if status_tool.get("handled"):
        answer = status_tool.get("answer") or "직전 등록 상태를 확인했습니다."
        _append_user_event(username, "app_write_status_followup", _event_fields(
            {
                "prompt": prompt,
                "intent": status_tool.get("intent") or "",
                "feature": status_tool.get("feature") or "",
                "answer": answer,
            },
            source=source,
            client_run_id=client_run_id,
        ))
        result = {
            "ok": True,
            "active": True,
            "user": username,
            "answer": answer,
            "tool": status_tool,
            "llm": {"available": llm_adapter.is_available(), "used": False, "blocked": bool(status_tool.get("blocked"))},
            "allowed_features": sorted(allowed_keys),
        }
        if source:
            result["agent_api"] = _agent_api_meta(
                source=source,
                client_run_id=client_run_id,
                username=username,
                tool=status_tool,
                agent_context=agent_context,
            )
        return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    splittable_plan_tool = _handle_splittable_plan_request(prompt, me, allowed_keys=allowed_keys)
    if splittable_plan_tool.get("handled"):
        answer = splittable_plan_tool.get("answer") or "스플릿 테이블 plan 요청을 처리했습니다."
        _append_user_event(username, "splittable_plan", _event_fields(
            {
                "prompt": prompt,
                "intent": splittable_plan_tool.get("intent") or "",
                "feature": splittable_plan_tool.get("feature") or "",
                "answer": answer,
            },
            source=source,
            client_run_id=client_run_id,
        ))
        result = {
            "ok": True,
            "active": True,
            "user": username,
            "answer": answer,
            "tool": splittable_plan_tool,
            "llm": {"available": llm_adapter.is_available(), "used": False, "blocked": bool(splittable_plan_tool.get("blocked"))},
            "allowed_features": sorted(allowed_keys),
        }
        if source:
            result["agent_api"] = _agent_api_meta(
                source=source,
                client_run_id=client_run_id,
                username=username,
                tool=splittable_plan_tool,
                agent_context=agent_context,
            )
        return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    splittable_note_tool = _handle_splittable_note_request(prompt, me, allowed_keys=allowed_keys)
    if splittable_note_tool.get("handled"):
        answer = splittable_note_tool.get("answer") or "스플릿 테이블 꼬리표 요청을 처리했습니다."
        _append_user_event(username, "splittable_lot_note", _event_fields(
            {
                "prompt": prompt,
                "intent": splittable_note_tool.get("intent") or "",
                "feature": splittable_note_tool.get("feature") or "",
                "answer": answer,
            },
            source=source,
            client_run_id=client_run_id,
        ))
        result = {
            "ok": True,
            "active": True,
            "user": username,
            "answer": answer,
            "tool": splittable_note_tool,
            "llm": {"available": llm_adapter.is_available(), "used": False, "blocked": bool(splittable_note_tool.get("blocked"))},
            "allowed_features": sorted(allowed_keys),
        }
        if source:
            result["agent_api"] = _agent_api_meta(
                source=source,
                client_run_id=client_run_id,
                username=username,
                tool=splittable_note_tool,
                agent_context=agent_context,
            )
        return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    draft_tool = _handle_app_write_draft(prompt, me, allowed_keys=allowed_keys)
    if draft_tool.get("handled"):
        answer = draft_tool.get("answer") or "앱 내부 쓰기 작업은 초안 확인이 필요합니다."
        _append_user_event(username, "app_write_draft", _event_fields(
            {
                "prompt": prompt,
                "intent": draft_tool.get("intent") or "",
                "feature": draft_tool.get("feature") or "",
                "answer": answer,
            },
            source=source,
            client_run_id=client_run_id,
        ))
        result = {
            "ok": True,
            "active": True,
            "user": username,
            "answer": answer,
            "tool": draft_tool,
            "llm": {"available": llm_adapter.is_available(), "used": False, "blocked": False},
            "allowed_features": sorted(allowed_keys),
        }
        if source:
            result["agent_api"] = _agent_api_meta(
                source=source,
                client_run_id=client_run_id,
                username=username,
                tool=draft_tool,
                agent_context=agent_context,
            )
        return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    data_tool = _handle_flowi_data_registration(prompt, me)
    if data_tool.get("handled"):
        answer = data_tool.get("answer") or "데이터 등록 요청을 처리했습니다."
        _append_user_event(username, "flowi_data_register", _event_fields(
            {
                "prompt": prompt[:1000],
                "action": data_tool.get("action") or "",
                "requires_confirmation": data_tool.get("requires_confirmation") or False,
                "blocked": data_tool.get("blocked") or False,
                "answer": answer,
            },
            source=source,
            client_run_id=client_run_id,
        ))
        result = {
            "ok": True,
            "active": True,
            "user": username,
            "answer": answer,
            "tool": data_tool,
            "llm": {"available": llm_adapter.is_available(), "used": False, "blocked": bool(data_tool.get("blocked"))},
            "allowed_features": sorted(allowed_keys),
        }
        if source:
            result["agent_api"] = _agent_api_meta(
                source=source,
                client_run_id=client_run_id,
                username=username,
                tool=data_tool,
                agent_context=agent_context,
            )
        return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    if _flowi_write_target_detected(prompt):
        db_blocked = (
            "DB 루트 원본은 admin도 Flow-i에서 수정할 수 없습니다. "
            "수정/등록은 파일탐색기 수정 권한이 있는 사용자만 Files 영역 단일파일에 대해 확인 후 실행됩니다."
        )
        if "db" in str(prompt or "").lower() or "DB" in str(prompt or "") or "원본" in str(prompt or "") or "raw data" in str(prompt or "").lower():
            blocked_msg = db_blocked
            _append_user_event(username, "blocked_write_request", _event_fields(
                {"prompt": prompt, "answer": blocked_msg},
                source=source,
                client_run_id=client_run_id,
            ))
            tool = {
                "handled": True,
                "intent": "blocked_write_request",
                "blocked": True,
                "answer": blocked_msg,
                "policy": FLOWI_READ_ONLY_POLICY,
            }
            result = {
                "ok": True,
                "active": True,
                "user": username,
                "answer": blocked_msg,
                "tool": tool,
                "llm": {"available": llm_adapter.is_available(), "used": False, "blocked": True},
                "allowed_features": sorted(allowed_keys),
            }
            if source:
                result["agent_api"] = _agent_api_meta(
                    source=source,
                    client_run_id=client_run_id,
                    username=username,
                    tool=tool,
                    agent_context=agent_context,
                )
            return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

        if _can_flowi_file_write(me):
            tool = _handle_admin_file_operation(prompt)
            answer = tool.get("answer") or "파일탐색기 관리 작업 요청을 처리했습니다."
            _append_user_event(username, "admin_file_operation", _event_fields(
                {
                    "prompt": prompt,
                    "action": tool.get("action") or "",
                    "requires_confirmation": tool.get("requires_confirmation") or False,
                    "blocked": tool.get("blocked") or False,
                    "answer": answer,
                },
                source=source,
                client_run_id=client_run_id,
            ))
            result = {
                "ok": True,
                "active": True,
                "user": username,
                "answer": answer,
                "tool": tool,
                "llm": {"available": llm_adapter.is_available(), "used": False, "blocked": bool(tool.get("blocked"))},
                "allowed_features": sorted(allowed_keys),
            }
            if source:
                result["agent_api"] = _agent_api_meta(
                    source=source,
                    client_run_id=client_run_id,
                    username=username,
                    tool=tool,
                    agent_context=agent_context,
                )
            return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

        blocked_msg = _flowi_write_block_message(prompt)
        _append_user_event(username, "blocked_write_request", _event_fields(
            {"prompt": prompt, "answer": blocked_msg},
            source=source,
            client_run_id=client_run_id,
        ))
        tool = {
            "handled": True,
            "intent": "blocked_write_request",
            "blocked": True,
            "answer": blocked_msg,
            "policy": FLOWI_READ_ONLY_POLICY,
        }
        result = {
            "ok": True,
            "active": True,
            "user": username,
            "answer": blocked_msg,
            "tool": tool,
            "llm": {"available": llm_adapter.is_available(), "used": False, "blocked": True},
            "allowed_features": sorted(allowed_keys),
        }
        if source:
            result["agent_api"] = _agent_api_meta(
                source=source,
                client_run_id=client_run_id,
                username=username,
                tool=tool,
                agent_context=agent_context,
            )
        return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)

    max_rows = max(4, min(24, int(max_rows or 12)))
    inform_tool = _handle_flowi_inform_summary(prompt, me, max_rows=max_rows, allowed_keys=allowed_keys) if "inform" in allowed_keys else {"handled": False}
    if inform_tool.get("handled"):
        tool = inform_tool
    else:
        tool = {}
    meeting_tool = _handle_meeting_recall(prompt, max_rows=max_rows, me=me, agent_context=agent_context) if ("meeting" in allowed_keys or "calendar" in allowed_keys) else {"handled": False}
    if tool.get("handled"):
        pass
    elif meeting_tool.get("handled"):
        tool = meeting_tool
    else:
        tool = _handle_flowi_query(prompt, product, max_rows=max_rows, allowed_keys=allowed_keys)
    entries = _matched_feature_entrypoints(prompt, allowed_keys=allowed_keys)
    if entries:
        tool["feature_entrypoints"] = entries
    if not tool.get("handled") and entries:
        tool["answer"] = (
            "질문과 가장 가까운 기능 진입점입니다.\n"
            + "\n".join(f"- {e['title']}: {e['description']}" for e in entries[:3])
        )
    _finalize_flowi_tool(tool, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)
    answer = tool.get("answer") or ""
    llm_info: dict[str, Any] = {"available": llm_adapter.is_available(), "used": False}
    user_ctx = _profile_context(username)
    feature_ctx = _feature_context(prompt, allowed_keys=allowed_keys)
    agent_ctx = _json_excerpt(agent_context) if agent_context else ""

    skip_llm_polish = _flowi_should_skip_llm_polish(tool)
    if skip_llm_polish:
        llm_info["skipped"] = "deterministic_tool_result"
    if llm_adapter.is_available() and not tool.get("blocked") and not skip_llm_polish:
        source_line = f"외부 AI source: {source}\nclient_run_id: {client_run_id}\n" if source else ""
        context_line = f"외부 AI 입력 context JSON: {agent_ctx}\n\n" if agent_ctx else ""
        if tool.get("handled"):
            polish_prompt = (
                "사용자 질문과 Flow 서버가 선택한 로컬 단위기능 결과를 바탕으로 한국어로 간결하게 답하세요. "
                "숫자, 식별자, feature/action은 제공된 JSON에서만 사용하고 추측하지 마세요. "
                "로컬 결과 JSON의 intent/action/missing/table/clarification/chart를 우선합니다. "
                "clarification.choices가 있으면 1/2/3 선택을 권하고 recommended 선택지를 먼저 설명하세요.\n\n"
                f"{source_line}"
                f"{context_line}"
                f"사용자 정보 Markdown:\n{user_ctx or '(없음)'}\n\n"
                f"단위기능 진입점:\n{feature_ctx}\n\n"
                f"질문: {prompt}\n"
                f"로컬 결과 JSON: {json.dumps(tool, ensure_ascii=False)[:12000]}"
            )
        else:
            polish_prompt = (
                "당신은 반도체 fab 데이터 Flowi assistant입니다. "
                "사용자 정보와 단위기능 진입점 설명을 바탕으로 가장 좋은 화면/다음 행동을 먼저 추천하세요. "
                "Roo Code/OpenCode 계열 오픈소스 모델처럼 추론 성능이 제한적일 수 있으므로, "
                "복잡한 계획보다 필요한 조건과 다음 화면을 짧게 답하세요. "
                "지원 범위가 불확실하면 필요한 lot/step/item 조건을 3개 이하 선택지로 물어보세요.\n\n"
                f"{source_line}"
                f"{context_line}"
                f"사용자 정보 Markdown:\n{user_ctx or '(없음)'}\n\n"
                f"단위기능 진입점:\n{feature_ctx}\n\n"
                f"사용자: {prompt}"
            )
        out = llm_adapter.complete(
            polish_prompt,
            system=_flowi_system_prompt(),
            timeout=12,
        )
        llm_info.update({"used": bool(out.get("ok") and out.get("text"))})
        if out.get("ok") and out.get("text"):
            answer = out.get("text") or answer
        elif out.get("error"):
            llm_info["error"] = out.get("error")

    _append_user_event(username, "chat", _event_fields(
        {
            "prompt": prompt,
            "intent": tool.get("intent") or "",
            "llm_used": llm_info.get("used"),
            "answer": answer,
        },
        source=source,
        client_run_id=client_run_id,
    ))
    result = {
        "ok": True,
        "active": True,
        "user": username,
        "answer": answer,
        "tool": tool,
        "llm": llm_info,
        "allowed_features": sorted(allowed_keys),
    }
    if source:
        result["agent_api"] = _agent_api_meta(
            source=source,
            client_run_id=client_run_id,
            username=username,
            tool=tool,
            agent_context=agent_context,
        )
    return _attach_flowi_trace(result, prompt=prompt, allowed_keys=allowed_keys, agent_context=agent_context)


def _flowi_should_skip_llm_polish(tool: dict[str, Any]) -> bool:
    """Keep local chart/tablemap results fast and avoid delaying visible payloads."""
    intent = str(tool.get("intent") or "")
    if intent.startswith("dashboard_") or intent == "tablemap_guidance":
        return True
    if intent == "meeting_recall_summary":
        return True
    if intent == "inform_lot_module_summary":
        return True
    if intent.startswith("inform_"):
        return True
    if intent in {"wafer_split_at_step", "knob_value_lot_search", "metric_at_step_lookup", "fab_progress_lookup", "filebrowser_data_preview", "filebrowser_schema_search"}:
        return True
    if isinstance(tool.get("chart_result"), dict):
        return True
    return False


@router.get("/status")
def status(request: Request):
    me = current_user(request)
    is_admin = (me.get("role") or "user") == "admin"
    allowed_keys = _allowed_flowi_feature_keys(me)
    local_tools = ["unit_feature_router"] if allowed_keys else []
    if "ettime" in allowed_keys:
        local_tools.insert(0, "et_wafer_median")
    if "splittable" in allowed_keys:
        local_tools.insert(1 if local_tools and local_tools[0] == "et_wafer_median" else 0, "lot_knobs")
    if "dashboard" in allowed_keys:
        local_tools.append("dashboard_scatter_plan")
    cfg = llm_adapter.get_config(redact=True)
    persona = _flowi_persona_config()
    flowi = {
        "requires_token": False,
        "allowed_features": sorted(allowed_keys),
        "entrypoints": [item for item in FLOWI_FEATURE_ENTRYPOINTS if item["key"] in allowed_keys],
        "persona": {
            "source": persona.get("source"),
            "enabled": persona.get("enabled"),
        },
        "agent_persona": FLOWI_AGENT_PERSONA,
        "naming_rules": FLOWI_NAMING_RULES,
    }
    if is_admin:
        flowi.update({
            "admin_token_configured": llm_adapter.has_admin_token(),
            "local_tools": local_tools,
            "policy": FLOWI_READ_ONLY_POLICY,
            "workflow_guide": FLOWI_BASE_WORKFLOW_GUIDE,
            "unit_actions": {k: v for k, v in FLOWI_UNIT_ACTIONS.items() if k in allowed_keys},
            "persona": {
                "source": persona.get("source"),
                "enabled": persona.get("enabled"),
                "updated_by": persona.get("updated_by"),
                "updated_at": persona.get("updated_at"),
            },
        })
    return {
        "available": llm_adapter.is_available(),
        "config": cfg,
        "flowi": flowi,
    }


class LLMTestReq(BaseModel):
    prompt: str
    system: str | None = None


@router.post("/test")
def test(req: LLMTestReq, _admin=Depends(require_admin)):
    if not llm_adapter.is_available():
        raise HTTPException(400, "LLM 이 설정되어 있지 않거나 비활성화됨")
    out = llm_adapter.complete((req.prompt or "").strip(), system=req.system)
    return out


class FlowiChatReq(BaseModel):
    prompt: str
    token: str = ""
    product: str = ""
    max_rows: int = 12
    context: dict[str, Any] = Field(default_factory=dict)


class FlowiAgentChatReq(BaseModel):
    prompt: str
    source_ai: str = "external"
    client_run_id: str = ""
    product: str = ""
    max_rows: int = 12
    context: dict[str, Any] = Field(default_factory=dict)


class FlowiVerifyReq(BaseModel):
    token: str = ""


class FlowiFunctionCallPreviewReq(BaseModel):
    prompt: str
    product: str = ""
    max_rows: int = 12


class FlowiFeedbackReq(BaseModel):
    rating: str = ""
    prompt: str = ""
    answer: str = ""
    intent: str = ""
    note: str = ""
    tags: list[str] = Field(default_factory=list)
    expected_workflow: str = ""
    expected_answer: str = ""
    correct_route: str = ""
    data_refs: str = ""
    golden_candidate: bool = False
    tool: dict[str, Any] = Field(default_factory=dict)
    llm: dict[str, Any] = Field(default_factory=dict)
    elapsed_ms: int | None = None


class FlowiGoldenPromoteReq(BaseModel):
    feedback_id: str
    expected_intent: str = ""
    expected_tool: str = ""
    expected_answer: str = ""
    notes: str = ""


class FlowiAdminUpdateReq(BaseModel):
    mode: str = "both"
    prompt: str = ""
    expected_intent: str = ""
    expected_tool: str = ""
    expected_answer: str = ""
    notes: str = ""
    data_refs: str = ""


class FlowiProfileReq(BaseModel):
    notes: str = ""


class FlowiPersonaReq(BaseModel):
    enabled: bool = False
    system_prompt: str = ""
    must_not: str = ""
    notes: str = ""


class FlowiInformConfirmReq(BaseModel):
    draft_id: str
    confirm: bool = False


class FlowiInformWalkthroughStartReq(BaseModel):
    root_lot_ids: list[str] = Field(default_factory=list)
    product: str = ""


class FlowiInformWalkthroughResolveReq(BaseModel):
    session_id: str
    action: str = ""
    value: str = ""
    target_module: str = ""


class FlowiInformWalkthroughConfirmReq(BaseModel):
    session_id: str
    confirm: bool = False


@router.post("/flowi/verify")
def flowi_verify(req: FlowiVerifyReq, request: Request):
    _ = current_user(request)
    if not llm_adapter.is_available():
        return {"ok": False, "message": "LLM 설정이 비활성화되어 있습니다.", "error": "llm unavailable"}
    out = llm_adapter.complete(
        "연결 확인입니다. 정상 수신했다면 확인완료 라고만 답하세요.",
        system="Flowi 연결 확인 응답은 반드시 확인완료 한 단어로만 작성합니다.",
        timeout=8,
    )
    text = str(out.get("text") or "").strip()
    if out.get("ok") and "확인완료" in text:
        return {"ok": True, "message": "확인완료"}
    return {"ok": False, "message": "LLM 연결 확인 실패", "error": out.get("error") or text or "unknown"}


@router.post("/flowi/function-call/preview")
def flowi_function_call_preview(req: FlowiFunctionCallPreviewReq, _admin=Depends(require_admin)):
    prompt = (req.prompt or "").strip()
    if not prompt:
        raise HTTPException(400, "prompt is required")
    return _structure_flowi_function_call(
        prompt,
        product=(req.product or "").strip(),
        max_rows=req.max_rows,
    )


@router.get("/flowi/persona-card")
def flowi_persona_card(request: Request):
    current_user(request)
    cfg = _flowi_persona_config()
    dont = []
    for line in str(cfg.get("must_not") or FLOWI_DEFAULT_MUST_NOT).splitlines():
        clean = line.strip().lstrip("-").strip()
        if clean:
            dont.append(clean)
    do_list = [
        "lot 조회",
        "plan 등록/통보",
        "인폼 메일",
        "파일/DB preview",
        "KNOB/MASK",
        "FAB 진행",
        "ET 측정",
        "자연어 인폼 등록",
    ]
    return {
        "ok": True,
        "persona": FLOWI_AGENT_PERSONA,
        "do_list": do_list,
        "dont_list": dont[:5],
    }


@router.post("/flowi/inform/confirm")
def flowi_inform_confirm(req: FlowiInformConfirmReq, request: Request):
    me = current_user(request)
    return _flowi_confirm_inform_draft(req.draft_id, req.confirm, me)


@router.post("/flowi/inform/walkthrough/start")
def flowi_inform_walkthrough_start(req: FlowiInformWalkthroughStartReq, request: Request):
    me = current_user(request)
    roots = [str(x).strip() for x in (req.root_lot_ids or []) if str(x).strip()]
    if not roots:
        raise HTTPException(400, "root_lot_ids required")
    return _flowi_start_walkthrough({"root_lot_ids": roots, "product": (req.product or "").strip()}, me)


@router.post("/flowi/inform/walkthrough/resolve")
def flowi_inform_walkthrough_resolve(req: FlowiInformWalkthroughResolveReq, request: Request):
    me = current_user(request)
    state = _flowi_load_inform_state(req.session_id)
    if req.target_module:
        state["current_module"] = _flowi_module_token(req.target_module) or req.target_module
    prompt = req.value or req.action
    if req.action and req.action not in {"set", "skip", "jump", "add_split", "set_note", "finalize"}:
        prompt = req.action + " " + prompt
    if req.action == "skip":
        prompt = "생략"
    elif req.action == "finalize":
        prompt = "이대로 등록"
    return _flowi_resolve_walkthrough_state(state, prompt, me)


@router.post("/flowi/inform/walkthrough/confirm")
def flowi_inform_walkthrough_confirm(req: FlowiInformWalkthroughConfirmReq, request: Request):
    me = current_user(request)
    return _flowi_confirm_inform_draft(req.session_id, req.confirm, me)


@router.get("/flowi/persona")
def flowi_persona(_admin=Depends(require_admin)):
    cfg = _flowi_persona_config()
    return {"ok": True, **cfg}


@router.post("/flowi/persona")
def flowi_persona_save(req: FlowiPersonaReq, request: Request, _admin=Depends(require_admin)):
    system_prompt = (req.system_prompt or "").strip()
    must_not = (req.must_not or "").strip()
    notes = (req.notes or "").strip()
    if len(system_prompt) > 12000:
        raise HTTPException(400, "system_prompt는 12000자 이하로 입력해주세요")
    if len(must_not) > 8000:
        raise HTTPException(400, "must_not은 8000자 이하로 입력해주세요")
    if len(notes) > 2000:
        raise HTTPException(400, "notes는 2000자 이하로 입력해주세요")
    current = _admin_settings()
    me = current_user(request)
    current["flowi_persona"] = {
        "enabled": True,
        "system_prompt": system_prompt or FLOWI_DEFAULT_SYSTEM_PROMPT,
        "must_not": must_not or FLOWI_DEFAULT_MUST_NOT,
        "notes": notes,
        "updated_by": me.get("username") or "admin",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_admin_settings(current)
    cfg = _flowi_persona_config()
    return {"ok": True, **cfg}


@router.get("/flowi/profile")
def flowi_profile(request: Request):
    me = current_user(request)
    username = me.get("username") or "user"
    md = _read_user_md(username, create=False)
    return {
        "ok": True,
        "username": username,
        "notes": _notes_from_md(md),
        "markdown": md,
    }


@router.post("/flowi/profile")
def flowi_profile_save(req: FlowiProfileReq, request: Request):
    me = current_user(request)
    username = me.get("username") or "user"
    notes = (req.notes or "").strip()
    if len(notes) > 20000:
        raise HTTPException(400, "사용자 메모는 20000자 이하로 입력해주세요")
    md = _write_user_notes(username, notes)
    _append_user_event(username, "profile_update", {"notes": notes[:500]})
    return {
        "ok": True,
        "username": username,
        "notes": _notes_from_md(md),
    }


@router.post("/flowi/feedback")
def flowi_feedback(req: FlowiFeedbackReq, request: Request):
    me = current_user(request)
    is_admin = (me.get("role") or "user") == "admin"
    rating = (req.rating or "").strip().lower()
    if rating not in {"up", "down", "neutral"}:
        raise HTTPException(400, "rating must be up/down/neutral")
    tags = _normalize_feedback_tags(req.tags, rating)
    if not is_admin:
        tags = [tag for tag in tags if tag in FLOWI_USER_FEEDBACK_TAGS]
    tool_summary = _flowi_tool_summary(req.tool if is_admin else {})
    golden_candidate = bool(req.golden_candidate) if is_admin else False
    needs_review = rating != "up" or golden_candidate or any(tag != "correct" for tag in tags)
    rec = {
        "id": "ff_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:8],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "username": me.get("username") or "",
        "rating": rating,
        "intent": ((req.intent or "").strip()[:80] if is_admin else ""),
        "prompt_excerpt": (req.prompt or "").strip()[:500],
        "answer_excerpt": (req.answer or "").strip()[:800],
        "note": (req.note or "").strip()[:1000],
        "tags": tags,
        "expected_workflow": ((req.expected_workflow or "").strip()[:160] if is_admin else ""),
        "expected_answer": ((req.expected_answer or "").strip()[:2000] if is_admin else ""),
        "correct_route": ((req.correct_route or "").strip()[:2000] if is_admin else ""),
        "data_refs": ((req.data_refs or "").strip()[:1000] if is_admin else ""),
        "golden_candidate": golden_candidate,
        "needs_review": needs_review,
        "review_status": "golden_candidate" if golden_candidate else ("needs_review" if needs_review else "ok"),
        "tool_summary": tool_summary,
        "llm": {
            "used": bool(req.llm.get("used")) if isinstance(req.llm, dict) else False,
            "available": bool(req.llm.get("available")) if isinstance(req.llm, dict) else False,
            "provider": str(req.llm.get("provider") or "")[:80] if isinstance(req.llm, dict) else "",
            "model": str(req.llm.get("model") or "")[:120] if isinstance(req.llm, dict) else "",
        },
        "elapsed_ms": req.elapsed_ms if isinstance(req.elapsed_ms, int) and req.elapsed_ms >= 0 else None,
    }
    try:
        FLOWI_FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
        with FLOWI_FEEDBACK_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("flowi feedback save failed: %s", e)
        raise HTTPException(500, "피드백 저장 실패")
    _append_user_event(me.get("username") or "user", "feedback", {
        "rating": rating,
        "intent": rec["intent"],
        "tags": ", ".join(tags),
        "needs_review": needs_review,
        "golden_candidate": rec["golden_candidate"],
        "note": rec["note"],
        "prompt": rec["prompt_excerpt"],
    })
    return {"ok": True, "id": rec["id"], "needs_review": needs_review}


@router.get("/flowi/feedback/summary")
def flowi_feedback_summary(
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(300, ge=1, le=1000),
    _admin=Depends(require_admin),
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = _read_jsonl(FLOWI_FEEDBACK_FILE, limit=max(1000, limit * 5))
    records = []
    for rec in rows:
        ts = _parse_ts(rec.get("timestamp"))
        if ts and ts < cutoff:
            continue
        rec = dict(rec)
        rec["tags"] = _normalize_feedback_tags(rec.get("tags") or rec.get("failure_types") or [], rec.get("rating") or "")
        records.append(rec)
    summary = _feedback_summary_from_records(records)
    golden = _read_jsonl(FLOWI_GOLDEN_FILE, limit=200)
    return {
        "ok": True,
        "days": days,
        "taxonomy": FLOWI_FEEDBACK_TAXONOMY,
        "total": summary["total"],
        "by_rating": summary["by_rating"],
        "by_tag": summary["by_tag"],
        "by_user": summary["by_user"],
        "by_intent": summary["by_intent"],
        "by_workflow": summary["by_workflow"],
        "recent": summary["recent"][:limit],
        "review_queue": summary["review_queue"][:min(limit, 200)],
        "golden_cases": sorted(golden, key=lambda r: str(r.get("timestamp") or ""), reverse=True)[:100],
    }


@router.post("/flowi/feedback/promote")
def flowi_feedback_promote(req: FlowiGoldenPromoteReq, _admin=Depends(require_admin)):
    feedback_id = (req.feedback_id or "").strip()
    if not feedback_id:
        raise HTTPException(400, "feedback_id is required")
    records = _read_jsonl(FLOWI_FEEDBACK_FILE, limit=10000)
    rec = next((r for r in reversed(records) if str(r.get("id") or "") == feedback_id), None)
    if not rec:
        raise HTTPException(404, "feedback not found")
    case = _feedback_to_golden_case(
        rec,
        created_by=_admin.get("username") or "admin",
        expected_intent=req.expected_intent,
        expected_tool=req.expected_tool,
        expected_answer=req.expected_answer,
        notes=req.notes,
    )
    try:
        FLOWI_GOLDEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        with FLOWI_GOLDEN_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(case, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("flowi golden case save failed: %s", e)
        raise HTTPException(500, "golden case 저장 실패")
    _append_user_event(_admin.get("username") or "admin", "golden_case_promote", {
        "feedback_id": feedback_id,
        "golden_id": case["id"],
        "expected_intent": case["expected_intent"],
        "expected_tool": case["expected_tool"],
    })
    return {"ok": True, "case": case}


@router.post("/flowi/admin/update")
def flowi_admin_update(req: FlowiAdminUpdateReq, _admin=Depends(require_admin)):
    mode = (req.mode or "both").strip().lower()
    if mode not in {"knowledge", "workflow", "both"}:
        raise HTTPException(400, "mode must be knowledge/workflow/both")
    if mode == "knowledge":
        raise HTTPException(400, "사전지식 등록은 에이전트 페이지의 RAG 반영 화면에서만 가능합니다.")
    if mode == "both":
        mode = "workflow"
    prompt = (req.prompt or "").strip()
    expected_intent = (req.expected_intent or "").strip()
    expected_tool = (req.expected_tool or "").strip()
    expected_answer = (req.expected_answer or "").strip()
    notes = (req.notes or "").strip()
    data_refs = (req.data_refs or "").strip()
    if not any([prompt, expected_intent, expected_tool, expected_answer, notes, data_refs]):
        raise HTTPException(400, "업데이트할 사전지식 또는 workflow 내용을 입력해주세요")

    admin_user = _admin.get("username") or "admin"
    result: dict[str, Any] = {"ok": True, "mode": mode}

    wants_workflow = mode == "workflow" and any([prompt, expected_intent, expected_tool, expected_answer, notes, data_refs])
    if wants_workflow:
        rec = {
            "id": "admin_direct_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:8],
            "prompt_excerpt": (prompt or notes or expected_answer)[:500],
            "rating": "up",
            "intent": expected_intent[:120],
            "tags": ["correct"],
            "expected_answer": expected_answer[:4000],
            "correct_route": expected_answer[:4000],
            "data_refs": data_refs[:1000],
            "note": notes[:2000],
            "expected_workflow": expected_tool[:160],
            "tool_summary": {"action": expected_tool[:160], "intent": expected_intent[:120]},
        }
        case = _feedback_to_golden_case(
            rec,
            created_by=admin_user,
            expected_intent=expected_intent,
            expected_tool=expected_tool,
            expected_answer=expected_answer,
            notes=notes,
        )
        try:
            FLOWI_GOLDEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            with FLOWI_GOLDEN_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(case, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning("flowi admin workflow update failed: %s", e)
            raise HTTPException(500, "workflow 업데이트 저장 실패") from e
        result["workflow"] = case

    _append_user_event(admin_user, "admin_agent_update", {
        "mode": mode,
        "prompt": prompt[:500],
        "expected_intent": expected_intent[:120],
        "expected_tool": expected_tool[:160],
        "workflow_id": ((result.get("workflow") or {}).get("id") if isinstance(result.get("workflow"), dict) else ""),
    })
    return result


@router.post("/flowi/chat")
def flowi_chat(req: FlowiChatReq, request: Request):
    me = current_user(request)
    result = _run_flowi_chat(
        prompt=req.prompt,
        product=req.product,
        max_rows=req.max_rows,
        me=me,
        agent_context=req.context,
    )
    return _flowi_home_response_for_role(result, me)


@router.post("/flowi/agent/chat")
def flowi_agent_chat(req: FlowiAgentChatReq, request: Request):
    """External AI clients can call the same read-only Flowi web-app router.

    Authentication still uses the normal Flow session token; the body fields
    only identify the calling AI and correlate its run id for audit/debugging.
    """
    me = current_user(request)
    return _run_flowi_chat(
        prompt=req.prompt,
        product=req.product,
        max_rows=req.max_rows,
        me=me,
        source_ai=req.source_ai,
        client_run_id=req.client_run_id,
        agent_context=req.context,
    )
