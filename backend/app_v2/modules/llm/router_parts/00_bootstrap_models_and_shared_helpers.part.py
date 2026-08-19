"""routers/llm.py v8.7.8 — 선택적 사내 LLM 어댑터 노출.

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
import os
import re
import time
import uuid
import csv
import io
from difflib import SequenceMatcher
from collections import Counter, deque
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
import polars as pl

from core import duckdb_engine
from core import ml_table_lookup
from core.paths import PATHS
from core.utils import _STR, csv_response, load_json, save_json
from core.auth import current_user, require_admin, is_page_admin
from core import llm_adapter
from core import product_config
from core import semiconductor_knowledge as semi_knowledge
from core import dashboard_join as dashboard_charting
from core import knowledge_impact
from core import knowledge_vault as kv
from core import flowi_gate
from core import flowi_multisource
from core import flowi_progress
from core import home_memory
from core import agent_feedback_penalties
from core import flowi_workflow_catalog
from core import semantic_measure_catalog
from core import semantic_hitl
from core import inline_coordinates
from core.parquet_perf import prune_recent_partitions
from app_v2.modules.agent_runtime.actions import (
    build_action_plans as _agent_runtime_build_action_plans,
    compact_plan_rows as _agent_runtime_compact_plan_rows,
    guardrail_summary_from_plans as _agent_runtime_guardrail_summary_from_plans,
)
from app_v2.modules.agent_runtime.semantic import resolve_semantic_frame as _agent_runtime_resolve_semantic_frame
from routers.auth import read_users


router = APIRouter(prefix="/api/llm", tags=["llm"])
logger = logging.getLogger("flow.llm.router")
FLOWI_FEEDBACK_FILE = PATHS.data_root / "flowi_feedback.jsonl"
FLOWI_GOLDEN_FILE = PATHS.data_root / "flowi_golden_cases.jsonl"
FLOWI_ACTIVITY_FILE = PATHS.data_root / "flowi_activity.jsonl"
FLOWI_USER_DIR = PATHS.data_root / "flowi_users"
FLOWI_AGENT_GUIDE_FILE = PATHS.data_root / "flowi_agent_entrypoints.md"
FLOWI_AGENT_FEATURE_GUIDE_DIR = PATHS.data_root / "flowi_agent_features"
FLOWI_PROMOTED_KNOWLEDGE_FILE = PATHS.data_root / "promoted_knowledge.json"
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
- diagnosis: DIBL, VTH, SS, ION, IOFF, RCA, 원인 후보
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
        "rule": "영문/숫자 혼합 5자 토큰은 기본적으로 root_lot_id로 해석합니다. 영문 2자+숫자 6자리와 EC 같은 영문 suffix가 붙은 step_id 패턴은 lot으로 보지 않습니다.",
        "examples": ["A0001", "B1234"],
    },
    {
        "key": "fab_lot_id",
        "label": "fab lot",
        "rule": "점 suffix lot 또는 6자 이상 혼합 토큰 중 step_id 패턴이 아닌 값은 fab_lot_id로 해석합니다.",
        "examples": ["A12345", "AZAAAB.1"],
    },
    {
        "key": "step_id",
        "label": "step",
        "rule": "step_id는 영문 2자 + 숫자 6자리 또는 그 뒤에 EC 같은 영문 suffix가 붙은 토큰을 step으로 해석합니다. 그 외에는 등록된 func_step 이름과 정확히 맞을 때만 step 후보로 봅니다.",
        "examples": ["AA200000", "AA200000EC", "GAA_CHANNEL_RELEASE"],
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
        "rule": "영어/숫자 조합 5자리 토큰은 root_lot_id로 해석한다. product 토큰, title 토큰, 영문 2자+숫자 6자리와 EC 같은 영문 suffix가 붙은 step_id 패턴은 lot에서 제외한다.",
        "examples": ["A1000", "R2001", "AB12C"],
    },
    {
        "key": "fab_lot_id",
        "label": "fab lot",
        "rule": "점(.)이 들어간 lot 조합이나 6자 이상 후보 중 step_id 패턴이 아닌 값은 fab_lot_id로 해석한다.",
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
        "rule": "영문 2자 + 숫자 6자리 또는 그 뒤에 EC 같은 영문 suffix가 붙은 토큰, 또는 등록된 func_step 이름만 step 후보로 확정한다.",
        "examples": ["AA200000", "AA200000EC", "GAA_CHANNEL_RELEASE"],
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
FLOWI_PLAIN_TEXT_OUTPUT_RULE = (
    "출력은 마크다운 강조 없이 plain text로 작성합니다. "
    "금지: **굵게**, ### 제목, 장식적 bullet 남발. "
    "섹션명은 요약, 결정사항, 액션아이템, 변경점 일정, 관련 이슈, 근거처럼 일반 텍스트 한 줄로 씁니다."
)
FLOWI_DEFAULT_SYSTEM_PROMPT = (
    "Flowi는 사내 Flow 앱을 이해하고 사용자 권한 안에서 작업하는 반도체 공정 데이터 운영 에이전트입니다. 답변은 짧고 실행 가능하게 작성합니다. "
    "사용자 Markdown 정보가 있으면 담당 제품, 관심 공정, 선호 출력 방식을 반영합니다. "
    "요청이 애매하면 바로 실행한다고 말하지 말고 1/2/3 형태의 선택지를 제시합니다. "
    "먼저 사내 naming rule을 적용해 자연어를 function arguments JSON으로 구조화한 뒤, 그 파라미터와 답변이 어긋나지 않게 합니다. "
    "product는 product_config/products.yaml, ML_TABLE_<product>, FAB product directory에서 동적으로 확인합니다. "
    "영어/숫자 조합 5자리 토큰은 root_lot_id, AZGASB.1/ASDAGFH.NJ처럼 점(.)이 들어간 lot 조합은 fab_lot_id로 해석합니다. 단 영문 2자+숫자 6자리와 EC 같은 영문 suffix가 붙은 step_id 패턴은 lot으로 보지 않습니다. "
    "#6, WF6, WAFER 6, slot 6, 6번 slot, 6번장, 6장은 wafer_id=6으로 해석하며 wafer_id는 1~25만 유효한 물리 slot으로 봅니다. "
    "step_id는 영문 2자 + 숫자 6자리 또는 그 뒤에 EC 같은 영문 suffix가 붙은 토큰을 step으로 해석하고, 그 외에는 등록된 func_step 이름과 정확히 맞을 때만 step 후보로 봅니다. "
    "FAB은 최신 route/progress 이력, ET는 기본 median, INLINE은 기본 avg이며 raw INLINE은 shot_x/shot_y가 아니라 subitem_id를 shot 구분자로 봅니다. "
    "SplitTable을 보여줄 때는 홈에서 따로 재구성하지 않고 SplitTable 화면 API의 headers, header_groups, rows, cell key, actual/plan/mismatch 결과를 기준으로 표시합니다. "
    "SplitTable/Inform 같은 앱 데이터 쓰기는 draft와 확인 선택지를 먼저 만들고, 확인 후에만 저장합니다. "
    "일반 사용자의 원 data DB 또는 Files 수정/삭제/저장/업로드는 차단합니다. "
    "admin 파일 변경은 서버의 FLOWI_FILE_OP 단위기능 결과가 제공된 경우에만 그 결과를 설명합니다. "
    + FLOWI_PLAIN_TEXT_OUTPUT_RULE
)
FLOWI_DEFAULT_MUST_NOT = (
    "- DB root/raw data 원본을 직접 수정, 삭제, 덮어쓰기, 이동하지 않는다.\n"
    "- 로컬 tool/cache/schema 결과에 없는 숫자, lot, product, step, item 값을 지어내지 않는다.\n"
    "- step_id는 영문 2자 + 숫자 6자리 또는 그 뒤에 EC 같은 영문 suffix가 붙은 토큰, 또는 등록된 func_step 이름이 아니면 step으로 확정하지 않는다.\n"
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
        "title": "ET 추적",
        "description": "Lot/Wafer 범위를 포함한 이슈 게시판과 ET 항목 추적(일일 스캔·trend)을 관리합니다.",
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
        "key": "teg",
        "title": "TEG 위치 조회",
        "description": "제품/Mask의 chip layout으로 wafer map을 그리고 TEG(module) 위치와 shot 격자좌표를 확인합니다.",
        "prompt": "TEG 위치 조회에서 특정 TEG가 wafer 어디에 있는지 확인하는 방법을 알려줘.",
    },
    {
        "key": "ettime",
        "title": "ET 측정시간",
        "description": "Product/Root lot 단위 ET 측정시간과 월별 평균 추이를 확인합니다.",
        "prompt": "ET 측정시간에서 내 제품의 측정시간 추이를 확인하는 흐름을 알려줘.",
    },
    {
        "key": "reformatize",
        "title": "ET 다운로드",
        "description": "vehicle 매칭으로 ET index(REAL/ADDP 수식)를 추출하고 기간·lot·step 필터로 CSV 다운로드합니다.",
        "prompt": "ET 다운로드에서 내 제품 ET index를 추출해 CSV로 받는 절차를 알려줘.",
    },
    {
        "key": "valve",
        "title": "매칭알람",
        "description": "Valve 파이프라인이 발행한 매칭 알람(RO ppid·미매칭 step)을 확인하고 엔지니어 판정을 남깁니다.",
        "prompt": "매칭알람에서 새 알람을 확인하고 판정하는 방법을 알려줘.",
    },
    {
        "key": "ramcache",
        "title": "캐시 관리",
        "description": "SplitTable RAM 캐시 현황·예산·수동 스캔·이벤트 로그를 확인하고 관리합니다.",
        "prompt": "캐시 관리에서 현재 캐시 사용량과 예산을 확인하는 방법을 알려줘.",
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
FLOWI_REGISTERED_UNIT_ACTIONS = {
    "filebrowser.scopes",
    "filebrowser.list",
    "filebrowser.preview",
    "filebrowser.lot_progress.latest",
    "filebrowser.csv.rules.read",
    "filebrowser.csv.rules.draft",
    "filebrowser.sql.llm.draft",
    "filebrowser.ml_table.lookup",
    "filebrowser.cache.lot_progress.refresh",
    "filebrowser.cache.lot_progress.status",
    "filebrowser.cache.llm.refresh",
    "dashboard.chart.llm.draft",
    "meeting.ask.llm",
    "splittable.view",
    "splittable.knob.summary",
    "splittable.plan.compare",
    "inform.draft.start",
    "inform.draft.resolve",
    "inform.thread.list",
    "tracker.lot.purpose",
    "diagnosis.rca.read",
    "tablemap.query",
    "flowi.feature.guidance",
    "flowi.general",
}
FLOWI_FEATURE_ALIASES = {
    "filebrowser": ["files", "file browser", "파일", "파일브라우저", "파일 탐색", "csv", "parquet", "db 조회", "데이터 조회"],
    "dashboard": ["dashboard", "대시보드", "차트", "trend", "추세", "그래프", "시각화", "scatter", "corr", "correlation", "상관", "피팅", "fitting"],
    "splittable": ["split", "split table", "splittable", "스플릿", "스플릿테이블", "plan", "actual", "mismatch", "매칭", "불일치"],
    "diagnosis": ["diagnosis", "rca", "root cause", "root-cause", "진단", "원인", "원인 후보", "반도체 지식", "knowledge card", "causal", "인과", "DIBL", "SS", "RSD", "ION", "IOFF", "IGATE", "VTH", "CA_RS", "SRAM"],
    "tracker": ["tracker", "트래커", "issue", "이슈", "gantt", "간트", "lot 이슈", "et 추적"],
    "teg": ["teg", "teg 위치", "teg map", "테그", "mapfile", "맵파일", "top_cell", "wf map"],
    "ettime": ["측정시간", "측정 시간", "et time", "et 측정시간", "tkout"],
    "reformatize": ["reformatize", "et 다운로드", "et index", "et 인덱스", "reformatter", "addp", "vehicle"],
    "valve": ["valve", "매칭알람", "매칭 알람", "알람 판정", "ro ppid", "미매칭"],
    "ramcache": ["ram cache", "ramcache", "캐시 관리", "캐시관리", "램 캐시", "캐시 예산", "수동 스캔"],
    "inform": ["inform", "인폼", "공유", "메일", "공지", "보고"],
    "meeting": ["meeting", "회의", "아젠다", "회의록", "action item", "액션아이템"],
    "calendar": ["calendar", "캘린더", "일정", "변경점", "change", "schedule"],
    "tablemap": ["table map", "tablemap", "테이블맵", "관계", "relation", "join", "column map", "컬럼"],
    "devguide": ["devguide", "개발", "api", "문서", "가이드", "architecture"],
}
FLOWI_CORE_AGENT_FEATURES = ("filebrowser", "splittable", "inform", "dashboard")
FLOWI_CORE_FEATURE_TERMS = {
    "filebrowser": (
        "파일탐색기", "파일 탐색기", "filebrowser", "file browser", "files", "parquet", "csv",
        "스키마", "schema", "컬럼", "column", "row preview", "preview row", "db preview",
    ),
    "splittable": (
        "스플릿테이블", "스플릿 테이블", "splittable", "split table", "knob", "mask",
        "plan", "actual", "custom set", "custom", "mismatch", "plan vs actual",
    ),
    "inform": (
        "인폼로그", "인폼 로그", "inform log", "inform", "인폼", "모듈 인폼",
        "메일", "mail preview", "통보 메일",
    ),
}
# v9.5.x: ettime 은 "ET 측정시간" 탭으로 부활 — archived 목록에서 제거.
FLOWI_ARCHIVED_TABS = {"waferlayout"}
FLOWI_ADMIN_ONLY_FEATURES = {"tablemap", "admin"}
FLOWI_RESTRICTED_FEATURES = {"devguide": "devguide_allowed"}
FLOWI_UNIT_ACTIONS = {
    "filebrowser": {
        "intent": "filebrowser_guidance",
        "action": "open_filebrowser",
        "agent_driver_actions": [
            "filebrowser.scopes",
            "filebrowser.list",
            "filebrowser.preview",
            "filebrowser.ml_table.lookup",
            "filebrowser.lot_progress.latest",
            "filebrowser.csv.rules.read",
            "filebrowser.cache.lot_progress.refresh",
            "filebrowser.cache.lot_progress.status",
        ],
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
        "agent_driver_actions": [
            "splittable.view",
            "splittable.knob.summary",
            "splittable.plan.compare",
            "splittable.notes.list",
            "splittable.notes.save",
            "splittable.export.snapshot",
        ],
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
        "agent_driver_actions": [
            "inform.draft.start",
            "inform.draft.resolve",
            "inform.draft.confirm",
            "inform.thread.list",
            "inform.thread.read",
            "inform.search",
            "inform.embed.splittable",
        ],
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
    "teg": {
        "intent": "teg_guidance",
        "action": "open_teg",
        "needs": ["product/mask", "TEG(module) name"],
        "outputs": ["wafer map TEG positions", "shot grid coordinates", "radius table"],
    },
    "ettime": {
        "intent": "ettime_guidance",
        "action": "open_ettime",
        "needs": ["product", "root_lot_id"],
        "outputs": ["measurement time table", "monthly average trend"],
    },
    "reformatize": {
        "intent": "reformatize_guidance",
        "action": "open_reformatize",
        "needs": ["product/vehicle", "ET index items or ADDP form", "period/lot/step filter"],
        "outputs": ["ET index table", "CSV download"],
    },
    "valve": {
        "intent": "valve_guidance",
        "action": "open_valve",
        "needs": ["alert kind (RO ppid / unmatched step)", "판정 결과"],
        "outputs": ["alert list", "ack decision history"],
    },
    "ramcache": {
        "intent": "ramcache_guidance",
        "action": "open_ramcache",
        "needs": ["cache scope (FAB/제품 원본/root lot)"],
        "outputs": ["cache status", "budget usage", "scan progress", "event log"],
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


def _flowi_core_feature_hint(prompt: str) -> str:
    text = str(prompt or "")
    low = text.lower()
    scores: dict[str, int] = {}
    for key, terms in FLOWI_CORE_FEATURE_TERMS.items():
        score = 0
        for term in terms:
            needle = term.lower()
            if needle and (needle in low or term in text):
                score += 4 if len(needle) > 3 else 2
        if score:
            scores[key] = score
    if not scores:
        return ""
    return sorted(scores.items(), key=lambda item: (-item[1], FLOWI_CORE_AGENT_FEATURES.index(item[0])))[0][0]

FLOWI_CHART_TERMS = {
    "차트", "그래프", "scatter", "산점도", "corr", "correlation", "상관", "피팅", "fitting",
    "fit", "1차식", "선형", "linear", "컬러링", "color", "coloring", "filter", "필터", "제외",
    "그려", "그려줘", "plot", "bar", "막대", "trend", "추세", "시계열", "라인", "line",
    "pie", "파이", "원형", "donut", "도넛", "table", "테이블", "cross table", "교차",
    "area", "heatmap", "히트맵", "treemap", "트리맵", "pareto", "파레토", "histogram", "binning",
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
FLOWI_ET_TREND_DEFAULT_METRICS = {
    "DIBL", "LKG", "LEAK", "LEAKAGE", "IOFF", "VTH", "VT", "ION", "IDSAT",
    "SS", "RSD", "IGATE", "RINGOSC", "RCH", "RS", "RC",
}
FLOWI_CHART_METRIC_STOP = {
    "INLINE", "IN-LINE", "ET", "FAB", "VM", "EDS", "ML", "ML_TABLE", "KNOB", "MASK", "CORR", "CORRELATION",
    "SCATTER", "CHART", "DASHBOARD", "FITTING", "FIT", "LINE", "LINEAR", "COLOR",
    "COLORING", "FILTER", "LEFT", "JOIN", "INNER", "AVG", "AVERAGE", "MEDIAN",
    "EXCLUDE", "EXCEPT", "REMOVE", "WITHOUT", "BY", "BASIS", "TREND", "PLOT", "BAR", "GRAPH",
    "BOX", "BOXPLOT", "WAFER", "MAP", "CLASSIFICATION", "R2", "PIE", "DONUT", "TABLE",
    "CROSS", "PIVOT", "AREA", "HEATMAP", "TREEMAP", "PARETO", "HISTOGRAM", "BINNING",
}
FLOWI_CHART_POINT_LIMIT = 500
FLOWI_CHART_DEFAULTS = {
    "surface": "home_flowi",
    "scatter": {"grain": "wafer_agg", "max_points": 500, "inline_agg": "avg", "et_agg": "median"},
    "line": {"grain": "wafer_agg", "max_points_per_series": 120},
    "bar": {"top_n": 12, "other_bucket": True},
    "pie": {"max_slices": 6, "other_bucket": True},
    "box": {"max_groups": 12, "min_n": 3},
    "boxplot": {"x": "step_id", "y": "$item1", "color": "product"},
    "trend": {"x": "tkout_time", "y": "$item1", "group": "lot_id", "agg": "median"},
    "correlation_matrix": {"items": "$selected", "method": "pearson"},
    "wafer_map": {"value": "$item1", "agg": "median"},
    "classification": {"x": "step_id", "y": "$item1", "group": "product"},
    "stacked_bar": {"x": "step_id", "y": "count", "group": "defect_type"},
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
    try:
        file_defaults = dashboard_charting.load_chart_defaults()
    except Exception:
        file_defaults = {}
    defaults = _merge_nested(_merge_nested(FLOWI_CHART_DEFAULTS, file_defaults), cfg)
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
    if scatter.get("inline_agg") not in _CHART_AGG_VALUES:
        scatter["inline_agg"] = "avg"
    if scatter.get("et_agg") not in _CHART_AGG_VALUES:
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
_FLOWI_INFORM_MAIL_MARKER = "FLOWI_INFORM_MAIL"
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
    "inform": ("인폼", "inform", "모듈 전달", "모듈전달", "module transfer", "module handoff"),
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
_FLOWI_NON_LOT_TOKENS = {"SPLIT", "TABLE", "TEST", "PLAN", "ACTUAL", "VIEW", "SHOW", "CUSTOM", "SET"}


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
    workflow_rows = []
    try:
        workflow_rows = flowi_workflow_catalog.workflow_few_shots(limit=max(1, int(limit or 24)))
    except Exception:
        workflow_rows = []
    for item in workflow_rows:
        rows.append(json.dumps(item, ensure_ascii=False, default=str))
    remaining = max(0, int(limit or 24) - len(rows))
    for item in FLOWI_FUNCTION_FEW_SHOTS[: remaining]:
        rows.append(json.dumps(item, ensure_ascii=False, default=str))
    return "[Workflow/Few-shot examples]\n" + "\n".join(rows)


def _flowi_promoted_knowledge_items(limit: int = 12) -> list[dict[str, Any]]:
    data = load_json(FLOWI_PROMOTED_KNOWLEDGE_FILE, {})
    raw = data.get("items") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict) or item.get("promoted") is False:
            continue
        title = str(item.get("title") or item.get("display_title") or "").strip()
        summary = re.sub(r"\s+", " ", str(item.get("summary") or item.get("content") or item.get("body") or "")).strip()
        if not title and not summary:
            continue
        out.append({
            "id": str(item.get("id") or item.get("source_id") or title or uuid.uuid4().hex[:8])[:140],
            "kind": str(item.get("kind") or "promoted_docs")[:80],
            "title": title[:180] or "Promoted knowledge",
            "summary": summary[:220],
            "tags": item.get("tags") if isinstance(item.get("tags"), list) else [],
            "updated_at": str(item.get("updated_at") or item.get("created_at") or "")[:80],
        })
        if len(out) >= max(1, int(limit or 12)):
            break
    return out


def _flowi_promoted_knowledge_section(limit: int = 12) -> str:
    rows = _flowi_promoted_knowledge_items(limit)
    if not rows:
        return ""
    lines = ["[사내 지식]"]
    for row in rows:
        title = row.get("title") or row.get("id") or "Promoted knowledge"
        summary = row.get("summary") or ""
        lines.append(f"- {title}: {summary[:200]}")
    return "\n".join(lines)


def _flowi_system_prompt(include_few_shots: bool = True) -> str:
    prompt = _flowi_persona_config()["active_system_prompt"]
    if FLOWI_PLAIN_TEXT_OUTPUT_RULE not in prompt:
        prompt += "\n\n" + FLOWI_PLAIN_TEXT_OUTPUT_RULE
    if include_few_shots:
        prompt += "\n\n" + _flowi_few_shot_section()
    promoted = _flowi_promoted_knowledge_section()
    if promoted:
        prompt += "\n\n" + promoted
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
    tabs = {t.strip() for t in raw.split(",") if t.strip()}
    tabs.difference_update(FLOWI_ARCHIVED_TABS)
    return tabs


def _devguide_allowed(username: str, role: str, tabs: set[str] | str) -> bool:
    # v9.3.x: DevGuide 는 global admin 전용 (devguide_user 위임 목록 폐기).
    return role == "admin"


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


def _flowi_home_admin_function_block(prompt: str, me: dict[str, Any] | None = None) -> dict[str, Any]:
    if ((me or {}).get("role") or "user") == "admin":
        return {"handled": False}
    text = str(prompt or "")
    low = text.lower()
    admin_terms = (
        "매칭테이블", "매칭 테이블", "matching table", "match table",
        "룰북", "rulebook", "rule book",
        "knowledge ingest", "knowledge 등록", "knowledge promote", "promote knowledge",
        "지식 ingest", "지식 등록", "지식 promote", "지식 프로모트", "지식 승격",
        "rag 반영", "rag 등록", "rag promote",
        "user 삭제", "users 삭제", "사용자 삭제", "계정 삭제", "delete user", "delete users",
        "db 직접 변경", "db 변경", "db 수정", "database update", "direct db",
        "admin update", "관리자 설정", "관리자 변경",
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
        event = {
            "timestamp": now,
            "username": _safe_username(username),
            "event": title,
            "fields": fields,
        }
        for key in ("selected_function", "retrieved_ids", "system_knowledge_ids", "retrieval_score", "result_status", "elapsed_ms"):
            if key in fields:
                event[key] = fields.get(key)
        with FLOWI_ACTIVITY_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
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
    if _is_current_fab_lot_prompt(text):
        if allowed_keys is not None and "filebrowser" not in allowed_keys:
            return []
        return [dict(item) for item in FLOWI_FEATURE_ENTRYPOINTS if item.get("key") == "filebrowser"][:1]
    prompt_l = text.lower()
    prompt_u = _upper(text)
    toks = {_upper(t) for t in _tokens(prompt)}
    has_create = any(term in prompt_l or term in text for term in _FLOWI_APP_CREATE_TERMS)
    has_chart = _contains_chart_intent(text) or any(t in prompt_l or t in text for t in ("trend", "추세", "시계열", "그려", "그래프"))
    core_hint = _flowi_core_feature_hint(text)
    scored: list[tuple[int, dict[str, str]]] = []
    for item in FLOWI_FEATURE_ENTRYPOINTS:
        if allowed_keys is not None and item["key"] not in allowed_keys:
            continue
        hay = " ".join([item["key"], item["title"], item["description"], item["prompt"]]).lower()
        score = 0
        if item["key"].lower() in prompt_l or item["title"].lower() in prompt_l:
            score += 4
        key = item["key"]
        if core_hint:
            if key == core_hint:
                score += 20
            elif key not in FLOWI_CORE_AGENT_FEATURES:
                score -= 4
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
    product = _flowi_explicit_splittable_product_hint(prompt, explicit)
    configured = {} if explicit else _configured_product_names()
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
    m = re.search(r"(?<![A-Z0-9_.#-])(\d{1,3})(?:\.(\d+))?(?:\s+|\s*\.\s*)([A-Z][A-Z0-9_/]*)(?![A-Z0-9_/])", text, flags=re.I)
    if not m:
        return ""
    decimal = m.group(2) if m.group(2) is not None else "0"
    return f"{m.group(1)}.{decimal} {m.group(3).upper()}"


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
        r"(?<![A-Za-z0-9_])(PPID_\d+_\d+)(?![A-Za-z0-9_])",
        r"(?:knob_value|KNOB_VALUE|값|value)\s*[:=]\s*([A-Za-z0-9_.-]+)",
        r"(?:KNOB|knob|노브|값|value)\s*(?:이|가|은|는)?\s*([A-Za-z0-9_.-]+)\s*인\s*(?:LOT_WF|lot_wf|WF|WAFER|웨이퍼|LOT|자재)",
        r"\b([A-Za-z0-9_.-]+)\s*인\s*(?:LOT_WF|lot_wf|WF|WAFER|웨이퍼|LOT|자재)",
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
        r"([A-Za-z0-9_.-]+)\s+(?:CUSTOM\s*SET|custom\s*set)",
        r"(?:CUSTOM\s*SET|custom\s*set)\s*[:=]?\s*([A-Za-z0-9_.-]+)",
        r"([^\s,;:/=]+)\s*스플릿(?:으로)?\s*선택",
        r"([^\s,;:/=]+)\s*split(?:으로)?\s*선택",
        r"([^\s,;:/=]+)\s*(?:커스텀\s*세트|custom\s*set)(?:으로|로)?",
        r"(?:split|split_set|스플릿)\s*[:=]\s*([^\s,;]+)",
        r"(?:custom_set|custom\s*set|커스텀\s*세트)\s*[:=]\s*([^\s,;]+)",
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.I)
        if m:
            return (m.group(1) or "").strip(" .,;:()[]{}")
    return ""


def _flowi_note_extract(prompt: str) -> str:
    text = str(prompt or "")
    for pat in (
        r"[A-Za-z0-9_.-]+\s*module\s*(?:\uc5d0|\uc5d0\ub294)?\s*(.+?)\s*(?:\uc801\uc5b4\uc11c|\ub0b4\uc6a9\uc73c\ub85c)\s*(?:[A-Za-z0-9_.@+-]+\s*(?:\uc218\uc2e0\ucc98|\uc218\uc2e0\uc790)(?:\ub85c|\uc5d0\uac8c)?\s*)?(?:\uc778\ud3fc|inform)",
        r"(.+?)\s*(?:\uc801\uc5b4\uc11c|\ub0b4\uc6a9\uc73c\ub85c)\s*(?:[A-Za-z0-9_.@+-]+\s*(?:\uc218\uc2e0\ucc98|\uc218\uc2e0\uc790)(?:\ub85c|\uc5d0\uac8c)?\s*)?(?:\uc778\ud3fc|inform)",
        r"(?:내용은|내용\s*[:=])\s*[\"']?(.+?)[\"']?\s*$",
        r"(?:사유는|사유\s*[:=])\s*[\"']?(.+?)[\"']?\s*$",
        r"(?:인폼로그|인폼|inform(?:\s+log)?)\s*(?:으로|로)?\s*[\"']?(.+?)[\"']?\s*(?:등록|생성|추가|남겨|기록|올려)(?:해줘|해주세요|합니다|해)?\s*$",
    ):
        m = re.search(pat, text, flags=re.I | re.S)
        if m:
            return re.sub(r"\s+", " ", (m.group(1) or "").strip(" \t\r\n\"'")).strip()[:1000]
    return ""


def _flowi_recipient_tokens(prompt: str) -> list[str]:
    text = str(prompt or "")
    out: list[str] = []
    seen: set[str] = set()
    for email in re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text):
        key = email.casefold()
        if key not in seen:
            seen.add(key)
            out.append(email)
    for pat in (
        r"([A-Za-z0-9_.@+-]+)\s*(?:\uc218\uc2e0\ucc98|\uc218\uc2e0\uc790)(?:\ub85c|\uc5d0\uac8c)?",
        r"(?:\uc218\uc2e0\ucc98|\uc218\uc2e0\uc790)\s*[:=]?\s*([A-Za-z0-9_.@+-]+)",
        r"(?:수신처|받는\s*사람|recipient|recipients|to)\s*[:=]\s*([^\n;]+)",
        r"([A-Za-z0-9_.@+-]+)\s*(?:에게|한테)\s*(?:인폼|inform|메일|mail)",
    ):
        for m in re.finditer(pat, text, flags=re.I):
            raw = (m.group(1) or "").strip()
            for piece in re.split(r"[,/]\s*|\s+", raw):
                val = piece.strip(" .,;:()[]{}")
                if not val or _upper(val) in _STOP_TOKENS:
                    continue
                key = val.casefold()
                if key not in seen:
                    seen.add(key)
                    out.append(val)
    return out[:12]


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
        if m.start("module") > 0 and re.match(r"[A-Za-z0-9_]", text[m.start("module") - 1]):
            continue
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


_FLOWI_FREETEXT_MISSING_KEYS = {
    "note",
    "reason",
    "사유",
    "내용",
    "memo",
    "메모",
    "comment",
    "코멘트",
    "knob_value",
    "keyword",
    "title",
    "description",
    "entries",
}

_FLOWI_FREETEXT_KEY_ALIASES = {
    "인폼 내용": "note",
    "내용": "note",
    "메모": "memo",
    "사유": "reason",
    "코멘트": "comment",
    "comment": "comment",
    "memo": "memo",
    "description": "description",
    "desc": "description",
}


def _flowi_missing_key(field: str) -> str:
    raw = str(field or "").strip()
    if not raw:
        return ""
    lowered = raw.lower()
    return _FLOWI_FREETEXT_KEY_ALIASES.get(raw) or _FLOWI_FREETEXT_KEY_ALIASES.get(lowered) or lowered


def _flowi_is_freetext_missing(field: str) -> bool:
    raw = str(field or "").strip()
    key = _flowi_missing_key(raw)
    if key in _FLOWI_FREETEXT_MISSING_KEYS:
        return True
    return any(token in raw for token in ("내용", "사유", "메모", "코멘트"))


def _flowi_freetext_label(field: str) -> str:
    key = _flowi_missing_key(field)
    labels = {
        "note": "인폼 내용",
        "reason": "사유",
        "memo": "메모",
        "comment": "코멘트",
        "knob_value": "KNOB 값",
        "keyword": "검색 키워드",
        "title": "제목",
        "description": "설명",
        "entries": "모듈별 내용",
    }
    return labels.get(key) or str(field or "").strip() or "내용"


def _flowi_freetext_placeholder(field: str) -> str:
    key = _flowi_missing_key(field)
    placeholders = {
        "note": "메모를 적어주세요(예: GATE 모듈 인폼)",
        "reason": "사유를 적어주세요(예: split 변경 공유)",
        "memo": "메모를 적어주세요",
        "comment": "코멘트를 적어주세요",
        "knob_value": "KNOB 값을 입력해 주세요(예: PPID_24_3)",
        "keyword": "찾을 키워드를 입력해 주세요",
        "title": "제목을 입력해 주세요",
        "description": "설명을 입력해 주세요",
        "entries": "예: GATE는 test1, STI는 test2",
    }
    return placeholders.get(key) or f"{_flowi_freetext_label(field)}을 입력해 주세요"


def _flowi_missing_freetext(missing: list[str]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for field in missing or []:
        if not _flowi_is_freetext_missing(field):
            continue
        key = _flowi_missing_key(field) or str(field or "").strip()
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "key": key,
            "label": _flowi_freetext_label(field),
            "placeholder": _flowi_freetext_placeholder(field),
        })
    return out


def _flowi_arguments_choices(missing: list[str], prompt: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    args = arguments if isinstance(arguments, dict) else {}
    fields: list[dict[str, Any]] = []
    for field in missing:
        key = str(field or "")
        if _flowi_is_freetext_missing(key):
            continue
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
        elif key == "recipients":
            module_recipients = _flowi_module_recipients(str(args.get("module") or "")) if "_flowi_module_recipients" in globals() else []
            values = [str(r.get("email") or r.get("name") or "").strip() for r in module_recipients if isinstance(r, dict) and str(r.get("email") or r.get("name") or "").strip()][:3]
            placeholder = "수신처 직접 입력"
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
        fields.append({"field": key, "question": _flowi_field_question(key), "choices": choices[:4], "free_input_label": placeholder})
    return {"message": "또는 직접 입력해 주세요", "fields": fields} if fields else {}


def _flowi_reason(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())[:80]


def _flowi_complete_json(messages: list[dict[str, Any]], schema_dict: dict[str, Any], *, max_retries: int = 1) -> dict[str, Any] | None:
    if not llm_adapter.is_available():
        return None
    keys = list((schema_dict or {}).get("properties", {}).keys()) or list((schema_dict or {}).get("keys", []))
    system = (
        _flowi_system_prompt(include_few_shots=True)
        + "\n\nReturn only a single JSON object matching the schema. No prose, no code fences."
    )
    prompt = json.dumps({"messages": messages, "schema": schema_dict}, ensure_ascii=False, default=str)
    out = llm_adapter.complete_json(prompt, system=system, schema=schema_dict, timeout=8, max_retries=max_retries)
    if out.get("ok") and isinstance(out.get("obj"), dict):
        obj = out.get("obj") or {}
        if keys:
            obj = {k: obj.get(k) for k in keys if k in obj}
        return obj
    return None


def _flowi_explicit_splittable_view_prompt(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    compact = re.sub(r"\s+", "", text)
    # Keep the native Korean spellings explicit. Some old fallback literals in
    # this module came from a legacy encoded source and do not match real user
    # input, which allowed the navigation-only Unit AI to intercept the turn.
    if "스플릿테이블" in compact:
        return True
    korean_show = any(term in text for term in ("보여", "조회", "열어", "띄워", "확인"))
    if korean_show and any(term in text for term in ("스플릿", "노브")):
        return True
    if any(
        term in low or term in text
        for term in ("split table", "splittable", "스플릿테이블", "스플릿 테이블")
    ):
        return True
    has_show = any(term in low or term in text for term in ("show", "display", "보여", "조회", "열어"))
    has_split_or_knob = bool(re.search(r"(?<![A-Za-z0-9_])(split|knob)(?![A-Za-z0-9_])", text, flags=re.I)) or any(
        term in text for term in ("스플릿", "노브")
    )
    return bool(has_show and has_split_or_knob and _flowi_func_step_token(text) and _lot_tokens(text))


def _flowi_explicit_splittable_root_hints(prompt: str) -> list[str]:
    text = str(prompt or "")
    if not _flowi_explicit_splittable_view_prompt(text):
        return []
    match = re.search(r"^(.*?)(?:split\s*table|splittable|스플릿\s*테이블|스플릿테이블)", text, flags=re.I | re.S)
    prefix = match.group(1) if match else text
    blocked = {
        "SPLIT",
        "TABLE",
        "SPLITTABLE",
        "SHOW",
        "DISPLAY",
        "VIEW",
        "QUERY",
        "PRODUCT",
        "PRODUCTS",
        "PROD",
        "KNOB",
        "MASK",
        "CUSTOM",
        "SET",
        "ML_TABLE",
        "FILE",
        "BROWSER",
        "SQL",
        "FAB",
        "ET",
        "INLINE",
        "VM",
        "EDS",
    } | set(_FLOWI_NON_LOT_TOKENS)
    for tok in reversed(_tokens(prefix)):
        if tok in blocked or re.fullmatch(r"TEST\d+", tok, flags=re.I):
            continue
        if _is_step_id_token(tok) or _is_product_token(tok):
            continue
        if re.fullmatch(r"[A-Z]{5}", tok):
            return [tok]
    return []


def _flowi_current_step_prompt(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    if not _lot_tokens(text):
        return False
    classified = _classified_lot_tokens(text)
    if _flowi_knob_value_token(text) and not (classified.get("root_lot_ids") or classified.get("fab_lot_ids")):
        return False
    if any(t in low for t in ("lot_id", "lot id", "fab_lot", "fab lot", "fab-lot", "fablot")):
        return False
    has_current = any(t in low or t in text for t in ("현재", "지금", "current", "now", "어디"))
    has_step = any(t in low or t in text for t in ("step", "function_step", "func_step", "스텝", "공정"))
    return has_current and has_step


def _flowi_tracker_lot_purpose_prompt(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    if not _lot_tokens(text):
        return False
    return any(t in low or t in text for t in ("무슨랏", "무슨 랏", "무슨 lot", "어떤랏", "어떤 랏", "목적", "purpose", "뭐하는", "무엇하는"))


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
    inform_transfer_terms = ("모듈 전달", "모듈전달", "module transfer", "module handoff")
    batch_entries = _flowi_parse_inform_batch_entries(text)
    if _step_mapping_lookup_intent(text, str(slots.get("product") or "")):
        return {
            "name": "query_step_mapping_lookup",
            "feature": "filebrowser",
            "intent": "step_mapping_lookup",
            "confidence": 0.92,
            "reason": _flowi_reason("Step ID/function_step matching lookup"),
            "requires_confirmation": False,
            "side_effect": "none",
        }
    if entries and (entries[0].get("key") or "") == "tablemap":
        primary = entries[0]
        return {
            "name": "route_flowi_feature",
            "feature": "tablemap",
            "intent": "tablemap_guidance",
            "confidence": 0.75,
            "reason": f"{primary.get('title') or primary.get('key')} feature keyword match",
            "requires_confirmation": False,
            "side_effect": "none",
        }
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
    if _is_fab_current_location_prompt(text):
        return {
            "name": "query_current_location",
            "feature": "filebrowser",
            "intent": "fab_current_location_lookup",
            "confidence": 0.92,
            "reason": _flowi_reason("FAB DB latest tkout_time current location lookup"),
            "requires_confirmation": False,
            "side_effect": "none",
        }
    if _flowi_current_step_prompt(text):
        return {
            "name": "query_lot_current_step_from_progress_cache",
            "feature": "filebrowser",
            "intent": "lot_current_step_lookup",
            "confidence": 0.92,
            "reason": _flowi_reason("FileBrowser latest progress cache에서 현재 step 조회"),
            "requires_confirmation": False,
            "side_effect": "none",
        }
    if _flowi_tracker_lot_purpose_prompt(text):
        return {
            "name": "query_tracker_lot_purpose",
            "feature": "tracker",
            "intent": "tracker_lot_purpose_lookup",
            "confidence": 0.86,
            "reason": _flowi_reason("이슈추적 lot 목적 조회"),
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
    wants_inform_walkthrough = any(
        t in text or t in up
        for t in ("인폼전체", "인폼 전체", "전체 작성", "전부 작성", "다 작성", "통째로", "모든 모듈")
    )
    if wants_inform_walkthrough:
        return {
            "name": "register_inform_walkthrough",
            "feature": "inform",
            "intent": "inform_walkthrough_start",
            "confidence": 0.9,
            "reason": _flowi_reason("모듈별 인폼 전체 작성 흐름 시작"),
            "requires_confirmation": True,
            "side_effect": "confirm_before_write",
        }
    clean_inform_write = bool(
        ("\uc778\ud3fc" in text or "inform" in text.lower())
        and re.search(r"(?:\ub4f1\ub85d|\uc791\uc131|\ub9cc\ub4e4|\ucd94\uac00|create|register)", text, flags=re.I)
    )
    if clean_inform_write:
        return {
            "name": "register_inform_log",
            "feature": "inform",
            "intent": "inform_log_draft",
            "confidence": 0.94,
            "reason": _flowi_reason("inform registration draft with confirmation"),
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
    if _flowi_explicit_splittable_view_prompt(text) and (root_lots or fab_lots or lots) and not clean_inform_write:
        return {
            "name": "query_splittable_view",
            "feature": "splittable",
            "intent": "splittable_view",
            "confidence": 0.9,
            "reason": _flowi_reason("SplitTable 화면 조회"),
            "requires_confirmation": False,
            "side_effect": "none",
        }
    has_split_lookup = (
        bool(re.search(r"(?<![A-Za-z0-9_])split(?!\s*table|[A-Za-z0-9_])", text, flags=re.I))
        or any(t in text for t in ("스플릿이", "스플릿 어떻게", "뭘로", "뭐 했", "적용된", "진행했", "진행했어"))
    )
    if step and (root_lots or fab_lots or lots) and has_split_lookup:
        return {
            "name": "query_wafer_split_at_step",
            "feature": "splittable",
            "intent": "wafer_split_at_step",
            "confidence": 0.85,
            "reason": _flowi_reason("wafer step split 조회"),
            "requires_confirmation": False,
            "side_effect": "none",
        }
    if step and knob_value and not (root_lots or fab_lots or lots) and any(t in text or t in text.lower() or t in up for t in ("인 자재", "가장 빠", "어디에 있", "어디", "진행 중", "받은 lot", "lot", "WF", "WAFER", "웨이퍼")):
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
    has_inform_word = "인폼" in text or "inform" in text.lower() or any(t in text.lower() or t in text for t in inform_transfer_terms)
    has_inform_create = any(t in text.lower() or t in text for t in _FLOWI_APP_CREATE_TERMS + _FLOWI_APP_WRITE_TERMS)
    has_inform_request = has_inform_create or "남기" in text or "전달" in text or "해줘" in text or "해주세요" in text
    if has_inform_word and has_inform_request and (has_inform_word or module or batch_entries):
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
    schema_requested = (
        any(t in text.lower() or t in text for t in ("컬럼", "어떤 column", "있는지", "schema", "스키마"))
        or (
            _flowi_core_feature_hint(text) == "filebrowser"
            and any(t in text.lower() or t in text for t in ("찾아", "검색"))
        )
    )
    if schema_requested:
        return {
            "name": "search_filebrowser_schema",
            "feature": "filebrowser",
            "intent": "filebrowser_schema_search",
            "confidence": 0.7,
            "reason": _flowi_reason("파일/DB schema 컬럼 검색"),
            "requires_confirmation": False,
            "side_effect": "none",
        }
    preview_show = (
        "보여줘" in text
        and any(s in {"FAB", "ET", "INLINE", "VM", "EDS"} for s in source_types)
        and not any(t in text.lower() or t in text for t in ("스플릿테이블", "split table", "splittable"))
    )
    preview_requested = preview_show or any(t in text.lower() or t in text for t in ("파일", "preview", "db", "row", "최근", "latest", "파일탐색기", "파일 탐색기"))
    if preview_requested:
        return {
            "name": "preview_filebrowser_data",
            "feature": "filebrowser",
            "intent": "filebrowser_data_preview",
            "confidence": 0.75,
            "reason": _flowi_reason("파일/DB row preview"),
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
        "query_current_location": {
            "description": "FAB DB latest tkout_time row by lot and wafer, returning the current step_id.",
            "required": ["lot_ids", "wafer_ids"],
        },
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
            "description": "특정 lot 또는 wafer가 특정 function step에서 받은 KNOB/MASK/FAB 조합(split)을 SplitTable 화면 기준으로 조회한다.",
            "required": ["root_lot_ids 또는 fab_lot_ids", "step"],
        },
        "query_lot_current_step_from_progress_cache": {
            "description": "FileBrowser latest progress cache에서 root_lot_id/lot_id/wafer_id 기준 현재 step_id와 function_step을 조회한다.",
            "required": ["root_lot_ids 또는 fab_lot_ids"],
        },
        "query_splittable_view": {
            "description": "SplitTable 화면 API 기준으로 lot/fab lot의 KNOB/MASK/FAB matrix를 조회한다.",
            "required": ["product", "root_lot_ids 또는 fab_lot_ids"],
        },
        "find_lots_by_knob_value": {
            "description": "특정 step에서 특정 KNOB value를 받은 lot/wafer를 찾아 FAB 진행 위치와 join한다.",
            "required": ["product", "step", "knob_value"],
        },
        "query_metric_at_step": {
            "description": "lot/wafer/function step 조건에서 ET/INLINE 측정 metric을 집계한다.",
            "required": ["root_lot_ids 또는 fab_lot_ids", "step", "metric"],
        },
        "register_inform_walkthrough": {
            "description": "모듈별 인폼 전체 작성 multi-turn walkthrough를 시작/진행/확인한다.",
            "required": ["root_lot_ids 또는 fab_lot_ids"],
        },
        "build_dashboard_metric_chart": {
            "description": "ET/INLINE/VM/EDS/FAB 데이터를 읽어 Dashboard 차트용 query arguments를 만든다.",
            "required": ["product", "metrics_or_items"],
        },
        "query_fab_progress": {
            "description": "FAB route/progress DB에서 현재 step, fab lot, 시간 이력을 조회한다.",
            "required": ["lot_ids"],
        },
        "query_tracker_lot_purpose": {
            "description": "Tracker 이슈의 lot 목적/purpose 행을 lot_id 기준으로 조회한다.",
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
    forced_match = re.search(r"__FLOWI_FORCE_FUNCTION__=([A-Za-z0-9_.-]{1,100})", text)
    forced_function = str(forced_match.group(1) or "") if forced_match else ""
    if forced_match:
        text = (text[:forced_match.start()] + text[forced_match.end():]).strip()
    product_info = _flowi_product_resolution(text, product)
    resolved_product = str(product_info.get("value") or "")
    classified = _classified_lot_tokens(text)
    if resolved_product:
        classified = _flowi_prune_product_lot_tokens(classified, resolved_product)
    if not (classified.get("root_lot_ids") or classified.get("fab_lot_ids")):
        root_hints = _flowi_explicit_splittable_root_hints(text)
        if root_hints:
            classified = {**classified, "root_lot_ids": root_hints}
    slots = _slot_summary(text, resolved_product)
    if classified.get("root_lot_ids") or classified.get("fab_lot_ids"):
        slots["root_lot_ids"] = classified.get("root_lot_ids") or []
        slots["fab_lot_ids"] = classified.get("fab_lot_ids") or []
        slots["lots"] = _flowi_lot_scope_terms(slots["root_lot_ids"], slots["fab_lot_ids"])
    wafers = [int(w) for w in _wafer_tokens(text)]
    assignments, invalid_wafers = _flowi_parse_splittable_plan_assignments(text)
    invalid_wafers = sorted(set(invalid_wafers + _flowi_invalid_wafer_mentions(text)), key=lambda x: int(x))
    metrics = _metric_alias_hits(text)
    source_types = _flowi_source_type_tokens(text)
    selected = _flowi_infer_function_call(text, slots)
    if forced_function:
        forced_schema = _flowi_function_schema(forced_function)
        selected = {
            "name": forced_function,
            "feature": "",
            "intent": f"{forced_function}_native",
            "confidence": 1.0,
            "reason": _flowi_reason(f"native tool selection: {forced_function}"),
            "requires_confirmation": forced_function.startswith(("register_", "preview_")),
            "side_effect": "confirm_before_write" if forced_function.startswith(("register_", "preview_")) else "none",
            "description": forced_schema.get("description") or "",
        }
    selected_name = str(selected.get("name") or "")
    structure_signal = bool(
        _matched_feature_entrypoints(text, limit=1)
        or resolved_product
        or any(classified.get(k) for k in ("root_lot_ids", "fab_lot_ids"))
        or wafers
        or assignments
        or invalid_wafers
        or metrics
        or source_types
        or any(slots.get(k) for k in ("lots", "root_lot_ids", "fab_lot_ids", "steps", "terms"))
        or _contains_chart_intent(text)
    )
    if float(selected.get("confidence") or 0) < 0.5 and structure_signal:
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
    if selected_name == "query_current_fab_lot_from_fab_db" and "FAB" not in source_types:
        source_types.insert(0, "FAB")
    if selected_name == "build_dashboard_metric_chart" and not source_types:
        source_types = ["ET", "INLINE"]
    metric_names = [m.get("metric") for m in metrics] if selected_name == "build_dashboard_metric_chart" else []
    plan_assignments = assignments if selected_name == "preview_splittable_plan_update" else []
    step = _flowi_func_step_token(text) or ((slots.get("steps") or [""])[0] if slots.get("steps") else "")
    group = _flowi_group_token(text)
    if selected_name == "query_splittable_view" and step and not group:
        group = "KNOB"
    metric = _flowi_metric_token(text)
    agg = _flowi_metric_agg(text)
    module = _flowi_module_token(text)
    split_set = _flowi_split_set_token(text)
    note = _flowi_note_extract(text)
    if note and resolved_product:
        note = re.sub(
            rf"(?:\s+(?:product|제품)\s*[:=]\s*)?{re.escape(resolved_product)}\s*$",
            "",
            note,
            flags=re.I,
        ).strip()
    knob_value = _flowi_knob_value_token(text)
    recipients = _flowi_recipient_tokens(text)
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
        "lot_wf_ids": _flowi_lot_wf_ids(classified.get("root_lot_ids") or [], classified.get("fab_lot_ids") or [], wafers),
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
    if selected_name in {"register_inform_log", "register_inform_walkthrough", "compose_inform_module_mail"} and not resolved_product:
        lots_for_product = list(dict.fromkeys(
            [str(x) for x in (arguments["root_lot_ids"] or []) if str(x).strip()]
            + [str(x) for x in (arguments["fab_lot_ids"] or []) if str(x).strip()]
            + [str(x) for x in (arguments["lot_ids"] or []) if str(x).strip()]
        ))
        candidates = _resolve_products_for_lots(lots_for_product, kinds=("FAB", "ML_TABLE"), limit=4) if lots_for_product else []
        products = []
        seen_products: set[str] = set()
        for row in candidates:
            prod = str(row.get("product") or "").strip()
            if prod and prod not in seen_products:
                seen_products.add(prod)
                products.append(prod)
        if len(products) == 1:
            resolved_product = products[0]
            arguments["product"] = resolved_product
            arguments["product_source"] = "lot_candidate"
    if step:
        arguments["step"] = step
    if group:
        arguments["group"] = group
    if metric and selected_name == "query_metric_at_step":
        arguments["metric"] = metric
        arguments["agg"] = agg
    if knob_value:
        arguments["knob_value"] = knob_value
        arguments["sort"] = "earliest_progress" if any(t in text for t in ("가장 빠", "가장 빨", "제일 빠", "제일 빨", "빠른", "빨리")) else "latest_progress"
    if module:
        arguments["module"] = module
    if split_set:
        arguments["split_set"] = split_set
    if note:
        arguments["note"] = note
    if recipients:
        arguments["recipients"] = recipients
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
    if selected_name == "build_dashboard_metric_chart":
        arguments["chart_type"] = _flowi_chart_type_from_prompt(text, metrics)
        arguments["color_by"] = dashboard_charting.parse_color_by(text)
        arguments["group_by"] = dashboard_charting.parse_group_by(text)
        arguments["fit"] = dashboard_charting.parse_fit(text)
        arguments["stats_columns"] = dashboard_charting.parse_stats_columns(text)
        arguments["config"] = _flowi_dashboard_default_config(
            text,
            arguments["chart_type"],
            metrics,
            product=resolved_product,
        )
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
    if selected_name == "register_inform_log" and not batch_entries:
        if not arguments.get("module"):
            missing.append("module")
        if not arguments.get("split_set"):
            missing.append("split_set")
        if not arguments.get("note") and not arguments.get("reason"):
            missing.append("note")
        if not arguments.get("recipients") and not arguments.get("module"):
            missing.append("recipients")
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
    missing_freetext = _flowi_missing_freetext(missing) if missing else []
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
        "missing_freetext": missing_freetext,
        "last_partial_prompt": text,
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
    last_partial_prompt = preview.get("last_partial_prompt") or preview.get("prompt") or ""
    slots = {
        "product": args.get("product") or "",
        "root_lot_ids": args.get("root_lot_ids") or [],
        "fab_lot_ids": args.get("fab_lot_ids") or [],
        "wafer_ids": args.get("wafer_ids") or [],
        "step": args.get("step") or "",
        "module": args.get("module") or "",
    }
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
        "arguments_partial": args,
        "arguments_choices": preview.get("arguments_choices") or {},
        "missing_freetext": preview.get("missing_freetext") or [],
        "last_partial_prompt": last_partial_prompt,
        "pending_prompt": last_partial_prompt if missing else "",
        "validation": validation,
        "slots": slots,
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
    return _flowi_set_inline_type(tool, "table")


def _unit_feature_guidance(
    prompt: str,
    product: str = "",
    max_rows: int = 12,
    allowed_keys: set[str] | None = None,
) -> dict:
    entries = _matched_feature_entrypoints(prompt, limit=3, allowed_keys=allowed_keys)
    if not entries:
        fallback = [item for key in FLOWI_CORE_AGENT_FEATURES for item in FLOWI_FEATURE_ENTRYPOINTS if item["key"] == key]
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
    return _flowi_set_inline_type({
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
    }, "table", prompt=prompt)


def _feature_context(prompt: str, allowed_keys: set[str] | None = None) -> str:
    matches = _matched_feature_entrypoints(prompt, allowed_keys=allowed_keys)
    core_items = [item for key in FLOWI_CORE_AGENT_FEATURES for item in FLOWI_FEATURE_ENTRYPOINTS if item["key"] == key and (allowed_keys is None or key in allowed_keys)]
    items = matches or core_items or [item for item in FLOWI_FEATURE_ENTRYPOINTS[:6] if allowed_keys is None or item["key"] in allowed_keys]
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
    for label, root in (("Files", PATHS.db_root),):
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
    root = PATHS.db_root.resolve()
    target = (root / rel).resolve()
    if not _is_relative_to(target, root):
        raise ValueError("대상 경로가 DB/Fab 루트를 벗어납니다.")
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


def _is_flowi_product_choice_name(name: str) -> bool:
    raw = str(name or "").strip()
    key = _upper(raw)
    if not raw or raw.startswith("."):
        return False
    if key in {"CACHE", "HISTORY", "LOGS", "REPORTS", "TEMP", "TMP"}:
        return False
    if key.startswith(("_", ".")) or key.endswith(("_CACHE", "_BACKUP", "_BACKUPS")):
        return False
    return True


def _configured_product_names() -> dict[str, str]:
    products: dict[str, str] = {}
    try:
        for key, cfg in (product_config.load_all(PATHS.data_root) or {}).items():
            name = str((cfg or {}).get("product") or key or "").strip()
            if name and _is_flowi_product_choice_name(name):
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
            if _is_flowi_product_choice_name(name):
                products.setdefault(_upper(name), name)
                products.setdefault(_upper(stem), name)
    try:
        for root in _db_root_candidates("FAB"):
            for child in root.iterdir():
                child_name = str(child.name or "").strip()
                child_u = _upper(child_name)
                if (
                    child.is_dir()
                    and child_name
                    and _is_flowi_product_choice_name(child_name)
                    and "RAWDATA_DB" not in child_u
                    and child_u not in {"FAB", "ET", "INLINE", "VM", "EDS", "MATCHING", "_BACKUPS"}
                ):
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
    if "_" in key or "." in key:
        return False
    if len(key) < 4:
        return False
    if key in {"GATE", "STI", "MOL", "BEOL", "FEOL", "SORT", "MODULE", "SPLIT", "CUSTOM", "INFORM"}:
        return False
    return key in _configured_product_names()


def _is_root_lot_token(tok: str) -> bool:
    key = _upper(tok)
    return bool(
        re.fullmatch(r"[A-Z0-9]{5}", key or "")
        and re.search(r"[A-Z]", key or "")
        and re.search(r"\d", key or "")
    )


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
    field_match = re.search(r"(?:product|제품)\s*[:=]\s*([A-Za-z0-9_][A-Za-z0-9_.-]*)", str(prompt or ""), flags=re.I)
    if field_match:
        candidate = _upper(field_match.group(1))
        if candidate and candidate not in {"PRODUCT", "PRODUCTS", "PROD"}:
            return candidate
    toks = _tokens(prompt)
    for tok in toks:
        if tok in {"PRODUCT", "PRODUCTS", "PROD"}:
            continue
        if tok.startswith(("ML_TABLE_", "PRODUCT_", "PROD")):
            return tok
    configured = _configured_product_names()
    for tok in toks:
        if tok in configured:
            return configured[tok]
    return ""


def _flowi_explicit_splittable_product_hint(prompt: str, explicit: str = "") -> str:
    product = _product_hint(prompt, explicit)
    if product or not _flowi_explicit_splittable_view_prompt(prompt):
        return product
    text = str(prompt or "")
    match = re.search(r"^(.*?)(?:split\s*table|splittable|스플릿\s*테이블|스플릿테이블)", text, flags=re.I | re.S)
    prefix = match.group(1) if match else text
    blocked = {
        "SPLIT",
        "TABLE",
        "SPLITTABLE",
        "SHOW",
        "DISPLAY",
        "VIEW",
        "QUERY",
        "PRODUCT",
        "PRODUCTS",
        "PROD",
        "KNOB",
        "MASK",
        "CUSTOM",
        "SET",
        "ML_TABLE",
    } | set(_FLOWI_NON_LOT_TOKENS)
    toks = [tok for tok in _tokens(prefix) if tok and tok not in blocked and not _is_step_id_token(tok)]
    if len(toks) < 2:
        return ""
    first = toks[0]
    if _is_root_lot_token(first) or re.fullmatch(r"TEST\d+", first, flags=re.I):
        return ""
    rest_has_lot = any(
        _is_root_lot_token(tok)
        or _is_fab_lot_token(tok)
        or re.fullmatch(r"[A-Z]\d{4,}(?:[A-Z])?(?:\.\d+)?", tok)
        for tok in toks[1:]
    )
    return first if rest_has_lot else ""


def _lot_tokens(prompt: str) -> list[str]:
    out = []
    seen = set()
    title_tokens = _title_hint_tokens(prompt)
    for tok in _tokens(prompt):
        if tok in _FLOWI_NON_LOT_TOKENS or re.fullmatch(r"TEST\d+", tok, flags=re.I):
            continue
        if tok in title_tokens:
            continue
        if _is_step_id_token(tok):
            continue
        is_root_like = _is_root_lot_token(tok)
        is_fab_like = (
            _is_fab_lot_token(tok)
            or (
                len(tok) >= 6
                and _is_mixed_alnum_token(tok)
                and not _is_step_id_token(tok)
                and not re.fullmatch(r"[A-Z]{2,5}\d{4,}", tok)
            )
        )
        legacy_lot_like = bool(re.fullmatch(r"[A-Z]\d{4,}(?:[A-Z])?(?:\.\d+)?", tok))
        if (is_root_like or is_fab_like or legacy_lot_like) and tok not in seen:
            if _is_product_token(tok):
                continue
            seen.add(tok)
            out.append(tok)
    return out


def _classified_lot_tokens(prompt: str) -> dict[str, list[str]]:
    root_ids: list[str] = []
    fab_ids: list[str] = []
    seen_root: set[str] = set()
    seen_fab: set[str] = set()

    def add_root(raw: Any) -> None:
        root = _upper(raw)
        if root and root not in seen_root:
            seen_root.add(root)
            root_ids.append(root)

    for tok in _tokens(prompt):
        if tok in _FLOWI_NON_LOT_TOKENS or re.fullmatch(r"TEST\d+", tok, flags=re.I):
            continue
        if _is_step_id_token(tok):
            continue
        if _is_root_lot_token(tok):
            if _is_product_token(tok):
                continue
            add_root(tok)
            continue
        if _is_fab_lot_token(tok) or (
            len(tok) >= 6
            and _is_mixed_alnum_token(tok)
            and not _is_step_id_token(tok)
            and not re.fullmatch(r"[A-Z]{2,5}\d{4,}", tok)
        ):
            if _is_product_token(tok):
                continue
            if tok not in seen_fab:
                seen_fab.add(tok)
                fab_ids.append(tok)
            continue
        if re.fullmatch(r"[A-Z]\d{4,}(?:[A-Z])?(?:\.\d+)?", tok):
            if _is_product_token(tok):
                continue
            bucket = fab_ids if "." in tok or len(tok) >= 6 else root_ids
            seen = seen_fab if bucket is fab_ids else seen_root
            if tok not in seen:
                seen.add(tok)
                bucket.append(tok)
            continue
    for fab in fab_ids:
        add_root(_flowi_root_from_fab_lot(fab))
    return {"root_lot_ids": root_ids, "fab_lot_ids": fab_ids}


def _flowi_prune_product_lot_tokens(classified: dict[str, list[str]], product: str) -> dict[str, list[str]]:
    product_key = _upper(product)
    if not product_key:
        return classified
    blocked = {product_key}
    if product_key.startswith("ML_TABLE_"):
        blocked.add(product_key[len("ML_TABLE_"):])
    product_root = _flowi_root_from_fab_lot(product_key)
    if product_root:
        blocked.add(product_root)
    return {
        "root_lot_ids": [x for x in (classified.get("root_lot_ids") or []) if _upper(x) not in blocked],
        "fab_lot_ids": [x for x in (classified.get("fab_lot_ids") or []) if _upper(x) not in blocked],
    }


def _flowi_root_from_fab_lot(value: str) -> str:
    text = _upper(value).strip()
    if not text or "." not in text:
        return ""
    head = text.split(".", 1)[0]
    return head[:5] if len(head) >= 5 else ""


def _flowi_lot_scope_terms(*groups: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if not text:
            return
        key = _upper(text)
        if key not in seen:
            seen.add(key)
            out.append(text)
        root = _flowi_root_from_fab_lot(text)
        if root and root not in seen:
            seen.add(root)
            out.append(root)

    for group in groups:
        if isinstance(group, (list, tuple, set)):
            for item in group:
                add(item)
        else:
            add(group)
    return out


def _flowi_lot_wf_id(root_lot_id: Any, wafer_id: Any) -> str:
    root = _upper(root_lot_id)
    wafer = _normalize_wafer_id(wafer_id)
    return f"{root}_{wafer}" if root and wafer else ""


def _flowi_lot_wf_ids(root_lot_ids: list[Any], fab_lot_ids: list[Any], wafer_ids: list[Any]) -> list[str]:
    roots: list[str] = []
    seen_roots: set[str] = set()
    for raw in [*(root_lot_ids or []), *(_flowi_root_from_fab_lot(str(f)) for f in (fab_lot_ids or []))]:
        root = _upper(raw)
        if root and root not in seen_roots:
            seen_roots.add(root)
            roots.append(root)
    out: list[str] = []
    seen: set[str] = set()
    for root in roots:
        for wafer in wafer_ids or []:
            lot_wf = _flowi_lot_wf_id(root, wafer)
            if lot_wf and lot_wf not in seen:
                seen.add(lot_wf)
                out.append(lot_wf)
    return out


def _is_step_id_token(tok: str) -> bool:
    return bool(re.fullmatch(r"[A-Z]{2}\d{6}(?:[A-Z]{1,4})?", _upper(tok)))


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
        "박스", "분포", "웨이퍼맵", "분류", "통계표", "분리", "별",
        "파이", "원형", "도넛", "테이블", "교차", "히트맵", "트리맵",
        "파레토", "히스토그램", "면적",
    }
    if any(term in text for term in korean_terms):
        return True
    latin_terms = {
        "scatter", "corr", "correlation", "fitting", "fit", "linear", "color",
        "coloring", "filter", "plot", "bar", "trend", "line", "chart", "graph",
        "boxplot", "box", "wafer map", "classification", "stacked", "pie",
        "donut", "doughnut", "table", "cross table", "crosstable", "pivot",
        "area", "heatmap", "heat map", "treemap", "tree map", "pareto",
        "histogram", "binning",
    }
    return any(re.search(rf"(?<![a-z0-9_]){re.escape(term)}(?![a-z0-9_])", low) for term in latin_terms)


def _flowi_knob_table_lookup_intent(prompt: str) -> bool:
    text = str(prompt or "")
    low = text.lower()
    up = _upper(text)
    if "KNOB" not in up and "노브" not in text:
        return False
    if not _lot_tokens(text):
        return False
    explicit_table = any(t in up or t in text for t in ("TABLE", "테이블", "표"))
    show_terms = ("보여", "조회", "확인", "show", "list")
    has_show = any(t in low or t in text for t in show_terms)
    chart_terms = (
        "차트", "그래프", "산점도", "상관", "피팅", "그려", "막대", "추세",
        "시계열", "라인", "박스", "분포", "웨이퍼맵", "파이", "도넛",
        "scatter", "corr", "chart", "graph", "plot", "trend", "box", "pie",
        "donut", "heatmap", "treemap", "pareto", "histogram",
    )
    wants_chart = any(t in low or t in text for t in chart_terms)
    return explicit_table or (has_show and not wants_chart)


def _source_terms(prompt: str) -> set[str]:
    up = _upper(prompt)
    out = set()
    if "INLINE" in up or "인라인" in prompt:
        out.add("INLINE")
    if re.search(r"\bET\b", up) or "ET" in up:
        out.add("ET")
    if re.search(r"\bFAB\b", up) or "공정" in prompt:
        out.add("FAB")
    if re.search(r"\bVM\b", up):
        out.add("VM")
    if re.search(r"\bEDS\b", up):
        out.add("EDS")
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
    explicit_visual = {
        "pie": ("pie", "파이", "원형"),
        "donut": ("donut", "doughnut", "도넛"),
        "bar": ("bar", "막대"),
        "line": ("line", "trend", "라인", "추세", "시계열"),
        "area": ("area", "면적"),
        "table": ("table", "테이블"),
        "cross_table": ("cross table", "crosstable", "pivot", "교차", "피벗"),
        "heatmap": ("heatmap", "heat map", "히트맵"),
    }
    for op, terms in explicit_visual.items():
        if any(t in low or t in text for t in terms):
            ops.append(op)
    if any(t in low or t in text for t in ("scatter", "산점도")) or (not ops and any(t in low or t in text for t in ("차트", "그래프"))):
        ops.append("scatter")
    if any(t in low or t in text for t in ("1차식", "linear", "fit", "fitting", "피팅", "선형")):
        ops.append("linear_fit")
    if any(t in low or t in text for t in ("color", "coloring", "컬러링", "색")):
        ops.append("color_by_column")
    if any(t in low or t in text for t in ("filter", "필터", "제외", "빼줘")):
        ops.append("filter")
    return ops or ["scatter"]


def _flowi_chart_type_from_prompt(prompt: str, metrics: list[dict[str, Any]] | None = None) -> str:
    selected = [str(m.get("metric") or "") for m in (metrics or []) if isinstance(m, dict) and m.get("metric")]
    return dashboard_charting.infer_chart_type(prompt, selected)


def _flowi_dashboard_default_config(
    prompt: str,
    chart_type: str,
    metrics: list[dict[str, Any]] | list[str] | None = None,
    *,
    product: str = "",
) -> dict[str, Any]:
    selected: list[str] = []
    for item in metrics or []:
        if isinstance(item, dict):
            val = str(item.get("metric") or "").strip()
        else:
            val = str(item or "").strip()
        if val:
            selected.append(val)
    color_by = dashboard_charting.parse_color_by(prompt)
    group_by = dashboard_charting.parse_group_by(prompt)
    fit = dashboard_charting.parse_fit(prompt)
    stats_cols = dashboard_charting.parse_stats_columns(prompt)
    overrides = {
        "product": product,
        "color_by": color_by,
        "group_by": group_by,
        "fit": fit,
        "stats_columns": stats_cols,
        "font_size": 14,
        "axis_label_size": 12,
        "legend": True,
        "theme": "dark",
    }
    return dashboard_charting.apply_chart_defaults(chart_type, selected, overrides=overrides)


def _fit_with_equation(fit: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(fit, dict) or not fit:
        return {}
    out = dict(fit)
    if "equation" not in out and "slope" in out and "intercept" in out:
        try:
            slope = float(out.get("slope"))
            intercept = float(out.get("intercept"))
            out["equation"] = f"y = {slope:.6g}*x + {intercept:.6g}"
        except Exception:
            pass
    out.setdefault("residual_std", None)
    return out


def _dashboard_chart_data_for_stats(chart_result: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(chart_result, dict):
        return []
    if isinstance(chart_result.get("points"), list):
        return [p for p in chart_result.get("points") or [] if isinstance(p, dict)]
    if isinstance(chart_result.get("groups"), list):
        rows = []
        for row in chart_result.get("groups") or []:
            if isinstance(row, dict):
                rows.append({"group": row.get("label") or row.get("group") or row.get("eqp") or "", "y": row.get("value") or row.get("median") or row.get("mean")})
        return rows
    if isinstance(chart_result.get("boxes"), list):
        rows = []
        for row in chart_result.get("boxes") or []:
            if isinstance(row, dict):
                rows.append({"group": row.get("label") or "", "y": row.get("median"), **row})
        return rows
    if isinstance(chart_result.get("series"), list):
        rows = []
        for series in chart_result.get("series") or []:
            name = series.get("name") if isinstance(series, dict) else ""
            for point in (series.get("points") if isinstance(series, dict) else []) or []:
                if isinstance(point, dict):
                    rows.append({"group": name, "y": point.get("y"), **point})
        return rows
    return []


def _chart_fit_intent(prompt: str) -> bool:
    return dashboard_charting.parse_fit(prompt) == "linear"


def _chart_fit_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    xs: list[float] = []
    ys: list[float] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            x = float(row.get("x"))
            y_raw = row.get("y")
            if y_raw is None:
                y_raw = row.get("median")
            if y_raw is None:
                y_raw = row.get("avg")
            if y_raw is None:
                y_raw = row.get("value")
            y = float(y_raw)
        except Exception:
            continue
        xs.append(x)
        ys.append(y)
    return _fit_with_equation(_linear_fit(xs, ys))


def _dashboard_agent_wiki_knowledge(
    prompt: str,
    *,
    product: str = "",
    chart_type: str = "",
    metrics: list[dict[str, Any]] | None = None,
    limit: int = 6,
) -> list[dict[str, Any]]:
    selected = [str(m.get("metric") or "").strip() for m in (metrics or []) if isinstance(m, dict) and m.get("metric")]
    query = " ".join(
        x for x in [
            "dashboard chart generation rules trend scatter INLINE ET lot_wf tkout_time aggregation",
            product,
            chart_type,
            " ".join(selected),
            prompt,
        ] if str(x or "").strip()
    )
    try:
        rows = kv.search_agent_wiki(query, limit=max(limit, 12))
    except Exception as exc:
        logger.debug("dashboard agent wiki search failed: %s", exc)
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        doc_id = str(row.get("doc_id") or row.get("id") or "").strip()
        if not doc_id or doc_id in seen:
            continue
        seen.add(doc_id)
        out.append({
            "id": doc_id,
            "doc_id": doc_id,
            "kind": row.get("kind") or "",
            "title": row.get("title") or doc_id,
            "summary": row.get("summary") or "",
            "snippet": row.get("snippet") or "",
            "score": row.get("score"),
            "tags": row.get("tags") or [],
            "source": "agent_wiki",
        })
        if len(out) >= max(1, min(limit, 20)):
            break
    return out


def _merge_retrieved_knowledge(existing: Any, additions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(row: Any) -> None:
        if isinstance(row, dict):
            doc_id = str(row.get("id") or row.get("doc_id") or row.get("knowledge_id") or "").strip()
            item = dict(row)
            if doc_id:
                item.setdefault("id", doc_id)
                item.setdefault("doc_id", doc_id)
        else:
            doc_id = str(row or "").strip()
            item = {"id": doc_id, "doc_id": doc_id}
        if not doc_id or doc_id in seen:
            return
        seen.add(doc_id)
        out.append(item)

    for row in existing or []:
        add(row)
    for row in additions or []:
        add(row)
    return out[:30]


def _flowi_knowledge_terms(prompt: str, tool: dict[str, Any], limit: int = 5) -> list[str]:
    terms: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if not text:
            return
        for part in re.findall(r"[A-Za-z][A-Za-z0-9_.-]*|[가-힣][가-힣0-9_]*", text):
            key = part.strip("._-")
            if len(key) < 2 and key.upper() not in {"B", "C", "D", "E"}:
                continue
            if _upper(key) in FLOWI_CHART_TERMS or _upper(key) in _STOP_TOKENS:
                continue
            terms.append(key)

    add(prompt)
    for src_key in ("slots", "filters", "arguments"):
        src = tool.get(src_key) if isinstance(tool.get(src_key), dict) else {}
        for value in src.values():
            if isinstance(value, list):
                for item in value[:8]:
                    add(item)
            else:
                add(value)
    for key in ("knob", "metric", "item_id", "source_type", "chart_type"):
        add(tool.get(key))
    out: list[str] = []
    seen: set[str] = set()
    for term in terms:
        normalized = term.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        out.append(term)
        if len(out) >= max(1, min(limit, 10)):
            break
    return out


def attach_term_knowledge(prompt: str, tool: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(tool, dict):
        return tool
    additions: list[dict[str, Any]] = []
    for term in _flowi_knowledge_terms(prompt, tool, limit=5):
        try:
            found = kv.lookup_term(term, limit=6)
        except Exception as exc:
            logger.debug("flowi term knowledge lookup failed for %s: %s", term, exc)
            continue
        for row in (found.get("columns") or [])[:3]:
            if not isinstance(row, dict):
                continue
            relation = str(row.get("relation_id") or "").strip()
            column = str(row.get("column") or "").strip()
            if not column:
                continue
            item_id = f"column:{relation}.{column}" if relation else f"column:{column}"
            additions.append({
                "id": item_id,
                "doc_id": str(row.get("wiki_doc_id") or item_id),
                "kind": "column_catalog",
                "title": f"{relation}.{column}" if relation else column,
                "summary": row.get("canonical_alias") or row.get("dtype") or "",
                "term": term,
                "relation_id": relation,
                "column": column,
                "source": "column_catalog",
            })
        for doc in (found.get("docs") or [])[:2]:
            if not isinstance(doc, dict):
                continue
            doc_id = str(doc.get("doc_id") or "").strip()
            if not doc_id:
                continue
            additions.append({
                "id": doc_id,
                "doc_id": doc_id,
                "kind": doc.get("kind") or "wiki_doc",
                "title": doc.get("title") or doc_id,
                "summary": doc.get("summary") or "",
                "term": term,
                "source": "knowledge_term",
            })
    # 단일 지식 레이어(knowledge_cards) — 시드/생성/로컬/어댑터 카드를 같은
    # retrieved_knowledge 로 합치고, 답변 출처(knowledge_sources)를 tool 에 남긴다.
    try:
        from core import knowledge_cards as _knowledge_cards
        matched_cards = _knowledge_cards.resolve(prompt)
        for card in matched_cards:
            body_line = re.sub(r"\s+", " ", str(card.get("body") or "")).strip()
            additions.append({
                "id": f"card:{card.get('term')}",
                "doc_id": f"card:{card.get('path') or card.get('term')}",
                "kind": f"knowledge_card_{card.get('origin') or 'seed'}",
                "title": str(card.get("term") or ""),
                "summary": body_line[:200],
                "term": str(card.get("matched") or card.get("term") or ""),
                "source": "knowledge_cards",
            })
        card_sources = _knowledge_cards.knowledge_sources(matched_cards)
        if card_sources and not tool.get("knowledge_sources"):
            tool["knowledge_sources"] = card_sources[:6]
    except Exception as exc:
        logger.debug("knowledge_cards attach failed: %s", exc)
    if additions:
        tool["retrieved_knowledge"] = _merge_retrieved_knowledge(tool.get("retrieved_knowledge"), additions)
    return tool


def _flowi_wiki_candidate_terms(prompt: str, limit: int = 12) -> list[str]:
    text = str(prompt or "")
    terms: list[str] = []
    seen: set[str] = set()

    def add(raw: Any) -> None:
        term = re.sub(r"\s+", " ", str(raw or "").strip(" .,;:()[]{}\"'"))
        if not term:
            return
        key = term.casefold()
        if key in seen:
            return
        term_u = _upper(term)
        if term_u in _STOP_TOKENS or term_u in _FLOWI_NON_LOT_TOKENS:
            return
        if re.fullmatch(r"\d+(?:\.\d+)?", term):
            return
        if _is_root_lot_token(term_u) or _is_fab_lot_token(term_u) or _is_product_token(term_u):
            return
        if len(term) < 2 and not re.fullmatch(r"[가-힣]", term):
            return
        seen.add(key)
        terms.append(term)

    for tok in _tokens(text):
        add(tok)
    for word in re.findall(r"[가-힣][가-힣0-9_]*", text):
        add(word)
    step = _flowi_func_step_token(text)
    if step:
        add(step)

    parts = [
        m.group(0)
        for m in re.finditer(r"[A-Za-z][A-Za-z0-9_.-]*|[가-힣][가-힣0-9_]*", text)
        if _upper(m.group(0)) not in _STOP_TOKENS
    ]
    for size in (3, 2):
        for idx in range(0, max(0, len(parts) - size + 1)):
            phrase = " ".join(parts[idx:idx + size])
            if len(phrase) <= 80:
                add(phrase)

    return terms[:max(1, min(limit, 24))]


def _flowi_wiki_relation_product(relation_id: str) -> str:
    relation = str(relation_id or "").strip()
    if _upper(relation).startswith("ML_TABLE_"):
        return relation[len("ML_TABLE_"):]
    return ""


def _flowi_wiki_column_hints(row: dict[str, Any]) -> list[str]:
    hints: list[str] = []

    def add(raw: Any) -> None:
        value = str(raw or "").strip()
        if value and value not in hints:
            hints.append(value[:120])

    relation = str(row.get("relation_id") or "").strip()
    column = str(row.get("column") or "").strip()
    alias = str(row.get("canonical_alias") or "").strip()
    relation_u = _upper(relation)
    column_u = _upper(column)
    product = _flowi_wiki_relation_product(relation)
    if product:
        add(product)
        add("ML_TABLE")
    for group in ("KNOB", "MASK", "INLINE", "VM", "FAB", "ET", "EDS", "QTIME"):
        if column_u.startswith(f"{group}_") or relation_u == group or relation_u.startswith(f"{group}_"):
            add(group)
    add(column)
    if alias and _upper(alias) != column_u:
        add(alias)
    for key in ("raw_names", "sample_values"):
        values = row.get(key) if isinstance(row.get(key), list) else []
        for value in values[:3]:
            add(value)
    return hints[:10]


def _flowi_wiki_prompt_interpretation(prompt: str) -> dict[str, Any]:
    """Resolve company slang/domain terms before deterministic routing."""
    prompt_text = str(prompt or "")
    if not prompt_text.strip():
        return {"pre_route": False}
    additions: list[dict[str, Any]] = []
    term_rows: list[dict[str, Any]] = []
    hint_tokens: list[str] = []
    lookup_terms: list[str] = []

    def add_hint(raw: Any) -> None:
        value = str(raw or "").strip()
        if not value:
            return
        if value not in hint_tokens:
            hint_tokens.append(value[:120])

    for term in _flowi_wiki_candidate_terms(prompt_text, limit=12):
        try:
            found = kv.lookup_term(term, limit=6)
        except Exception as exc:
            logger.debug("flowi pre-route wiki lookup failed for %s: %s", term, exc)
            continue
        columns = [row for row in (found.get("columns") or []) if isinstance(row, dict)]
        docs = [row for row in (found.get("docs") or []) if isinstance(row, dict)]
        if not columns and not docs:
            continue
        lookup_terms.append(term)
        for row in columns[:4]:
            relation = str(row.get("relation_id") or "").strip()
            column = str(row.get("column") or "").strip()
            if not column:
                continue
            item_id = f"column:{relation}.{column}" if relation else f"column:{column}"
            doc_id = str(row.get("wiki_doc_id") or item_id)
            title = f"{relation}.{column}" if relation else column
            additions.append({
                "id": item_id,
                "doc_id": doc_id,
                "kind": "column_catalog",
                "title": title,
                "summary": row.get("canonical_alias") or row.get("dtype") or "",
                "term": term,
                "relation_id": relation,
                "column": column,
                "source": "column_catalog_pre_route",
            })
            for hint in _flowi_wiki_column_hints(row):
                add_hint(hint)
            refs = [item_id]
            if doc_id and doc_id != item_id:
                refs.append(doc_id)
            term_rows.append({
                "token": term,
                "meaning": row.get("canonical_alias") or title,
                "wiki_refs": refs,
                "query_filter": title,
                "status": "wiki_pre_route",
            })
        for doc in docs[:3]:
            doc_id = str(doc.get("doc_id") or "").strip()
            if not doc_id:
                continue
            additions.append({
                "id": doc_id,
                "doc_id": doc_id,
                "kind": doc.get("kind") or "wiki_doc",
                "title": doc.get("title") or doc_id,
                "summary": doc.get("summary") or "",
                "term": term,
                "source": "agent_wiki_pre_route",
                "relation_id": doc.get("relation_id") or "",
            })
            relation = str(doc.get("relation_id") or "").strip()
            if relation:
                add_hint(_flowi_wiki_relation_product(relation))
                add_hint(relation)
            for ref in (doc.get("column_refs") or [])[:6]:
                ref_text = str(ref or "").strip()
                if not ref_text:
                    continue
                add_hint(ref_text.split(".", 1)[1] if "." in ref_text else ref_text)
            tags = doc.get("tags") if isinstance(doc.get("tags"), list) else []
            for tag in tags[:6]:
                tag_u = _upper(tag)
                if tag_u in {"KNOB", "MASK", "FAB", "INLINE", "VM", "ET", "EDS", "ML_TABLE"}:
                    add_hint(tag_u)
            term_rows.append({
                "token": term,
                "meaning": doc.get("summary") or doc.get("title") or doc_id,
                "wiki_refs": [doc_id],
                "query_filter": str(doc.get("relation_id") or ", ".join(doc.get("column_refs") or []))[:300],
                "status": "wiki_pre_route",
            })

    hints = [h for h in hint_tokens if h]
    augmented = prompt_text
    if hints:
        augmented = prompt_text + "\n" + " ".join(hints[:24])
    return {
        "pre_route": bool(additions or term_rows or hints),
        "terms": lookup_terms[:12],
        "prompt_hints": hints[:24],
        "retrieved_knowledge": _merge_retrieved_knowledge([], additions),
        "term_resolution": term_rows[:16],
        "augmented_prompt": augmented,
    }
