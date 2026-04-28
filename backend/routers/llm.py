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
from collections import Counter, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
import polars as pl

from core.paths import PATHS
from core.utils import _STR, load_json
from core.auth import current_user, require_admin
from core import llm_adapter
from core import semiconductor_knowledge as semi_knowledge
from routers.auth import read_users


router = APIRouter(prefix="/api/llm", tags=["llm"])
logger = logging.getLogger("flow.llm.router")
FLOWI_FEEDBACK_FILE = PATHS.data_root / "flowi_feedback.jsonl"
FLOWI_GOLDEN_FILE = PATHS.data_root / "flowi_golden_cases.jsonl"
FLOWI_ACTIVITY_FILE = PATHS.data_root / "flowi_activity.jsonl"
FLOWI_USER_DIR = PATHS.data_root / "flowi_users"
FLOWI_READ_ONLY_POLICY = {
    "read_only": True,
    "applies_to": ["user"],
    "blocked_targets": ["raw data DB", "Files", "DB root files", "product reformatter files"],
    "admin_controlled_file_ops": {
        "enabled": True,
        "format": "FLOWI_FILE_OP JSON with exact confirm text",
        "scope": "DB/Files root-level files only",
        "ops": ["delete", "rename", "replace_text"],
    },
}
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
        "key": "ml",
        "title": "ML 분석",
        "description": "Inline/ET 요약, 상관, 중요도, 공정 window 후보를 비교합니다.",
        "prompt": "ML 분석에서 내 제품의 원인 후보를 좁히기 위한 컬럼 선택을 추천해줘.",
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
    "ml": ["ml", "머신러닝", "상관", "correlation", "feature", "importance", "윈도우", "window", "knob", "노브", "coloring", "컬러링"],
    "tablemap": ["table map", "tablemap", "테이블맵", "관계", "relation", "join", "column map", "컬럼"],
    "devguide": ["devguide", "개발", "api", "문서", "가이드", "architecture"],
}
FLOWI_DEFAULT_TABS = {
    "filebrowser", "dashboard", "splittable", "ettime", "waferlayout",
    "inform", "meeting", "calendar", "diagnosis",
}
FLOWI_NEW_DEFAULT_TABS = {"inform", "meeting", "calendar", "ettime", "waferlayout", "diagnosis"}
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
    "ml": {
        "intent": "ml_guidance",
        "action": "open_ml",
        "needs": ["source table", "target metric", "candidate features"],
        "outputs": ["importance/correlation", "candidate process window"],
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
    "OVERLAY": ["OVERLAY", "OVL"],
    "THICKNESS": ["THICKNESS", "THK", "TICK"],
}
FLOWI_CHART_METRIC_STOP = {
    "INLINE", "IN-LINE", "ET", "ML", "ML_TABLE", "KNOB", "CORR", "CORRELATION",
    "SCATTER", "CHART", "DASHBOARD", "FITTING", "FIT", "LINE", "LINEAR", "COLOR",
    "COLORING", "FILTER", "LEFT", "JOIN", "INNER", "AVG", "AVERAGE", "MEDIAN",
    "EXCLUDE", "EXCEPT", "REMOVE", "WITHOUT", "BY", "BASIS",
}
FLOWI_CHART_POINT_LIMIT = 500

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
_FLOWI_FILE_EXTS = {".parquet", ".csv", ".json", ".md", ".txt", ".yaml", ".yml"}
_FLOWI_TEXT_FILE_EXTS = {".csv", ".json", ".md", ".txt", ".yaml", ".yml"}
_FLOWI_MAX_TEXT_EDIT_BYTES = 2 * 1024 * 1024
_FLOWI_FILE_TOKEN_RE = re.compile(
    r"(?<![\w./-])([A-Za-z0-9][A-Za-z0-9_.@+=-]{0,120}\.(?:parquet|csv|json|md|txt|yaml|yml))(?![\w.-])",
    re.I,
)

_STOP_TOKENS = {
    "A", "AN", "THE", "ET", "WF", "WAFER", "WAFERS", "BY", "PER", "ITEM", "LOT", "LOTS",
    "KNOB", "KNOBS", "MEDIAN", "MEAN", "AVG", "AVERAGE", "VALUE", "VALUES", "FLOWI",
    "값", "중앙값", "평균", "별로", "별", "랏", "로트", "노브", "아이템", "어떤",
    "어떻게", "몇이야", "처리", "데이터", "조회", "보여줘",
}


def _text(raw: Any) -> str:
    return str(raw or "").strip()


def _upper(raw: Any) -> str:
    return _text(raw).upper()


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
    parts = []
    if notes:
        parts.append("사용자 메모:\n" + notes[:2500])
    if recent:
        parts.append("최근 Flowi 기록:\n" + recent)
    return "\n\n".join(parts).strip()


def _matched_feature_entrypoints(
    prompt: str,
    limit: int = 4,
    allowed_keys: set[str] | None = None,
) -> list[dict[str, str]]:
    prompt_l = str(prompt or "").lower()
    toks = {_upper(t) for t in _tokens(prompt)}
    scored: list[tuple[int, dict[str, str]]] = []
    for item in FLOWI_FEATURE_ENTRYPOINTS:
        if allowed_keys is not None and item["key"] not in allowed_keys:
            continue
        hay = " ".join([item["key"], item["title"], item["description"], item["prompt"]]).lower()
        score = 0
        if item["key"].lower() in prompt_l or item["title"].lower() in prompt_l:
            score += 4
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
    return {
        "product": _product_hint(prompt, product),
        "lots": _lot_tokens(prompt),
        "steps": _step_tokens(prompt),
        "terms": _query_tokens(prompt)[:12],
    }


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
    return "\n".join(
        f"- {it['title']}({it['key']}): {it['description']} 시작 질문 예시: {it['prompt']}"
        for it in items
    )


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
        "조회/요약/표시는 가능하지만 파일 변경은 admin의 확인된 단위기능으로만 실행됩니다."
    )


def _flowi_file_roots() -> list[tuple[str, Path]]:
    roots: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for label, root in (("Files", PATHS.base_root), ("DB", PATHS.db_root)):
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
    raise FileNotFoundError(f"DB/Files 루트에서 파일을 찾지 못했습니다: {rel.as_posix()}")


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
        {"field": "scope", "value": "admin only; DB/Files root-level files"},
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
        "description": "대상 파일과 컬럼/내용을 조회한 뒤 다시 실행합니다.",
        "prompt": "파일 탐색기에서 수정할 파일을 먼저 확인해줘",
    })
    return {
        "handled": True,
        "intent": "admin_file_operation",
        "action": "confirm_file_operation",
        "requires_confirmation": True,
        "answer": "Admin 파일 작업은 구조화된 확인 명령이 필요합니다. 추천 선택지를 눌러 확인 명령을 다시 보내거나 JSON을 직접 입력하세요.",
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


def _handle_admin_file_operation(prompt: str) -> dict:
    payload = _extract_flowi_file_op(prompt)
    if payload is None:
        return _flowi_admin_file_confirmation(prompt)
    if payload.get("_parse_error"):
        return _flowi_admin_file_confirmation(prompt, str(payload.get("_parse_error")))
    return _execute_admin_file_operation(payload)


def _tokens(prompt: str) -> list[str]:
    return [m.group(0).upper() for m in re.finditer(r"[A-Za-z][A-Za-z0-9_.-]*|\d+(?:\.\d+)?", prompt or "")]


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
    for tok in _tokens(prompt):
        if tok.startswith(("ML_TABLE_", "PRODUCT_", "PROD")):
            return tok
    return ""


def _lot_tokens(prompt: str) -> list[str]:
    out = []
    for tok in _tokens(prompt):
        if re.fullmatch(r"[A-Z]\d{4,}(?:[A-Z])?(?:\.\d+)?", tok):
            out.append(tok)
    return out


def _step_tokens(prompt: str) -> list[str]:
    out = []
    for tok in _tokens(prompt):
        if re.fullmatch(r"[A-Z]{1,5}\d{4,}", tok):
            out.append(tok)
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
    return any(term in low or term in text for term in FLOWI_CHART_TERMS)


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
    if {"INLINE", "ET"} & sources:
        return "shot_or_die_key if present, else lot_wf"
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


def _lot_wf_expr(root_col: str, wafer_col: str):
    return (
        pl.col(root_col).cast(_STR, strict=False)
        + pl.lit("_")
        + pl.col(wafer_col).cast(_STR, strict=False)
    )


def _flowi_metric_lf(kind: str, product: str, lots: list[str], metric: str, value_alias: str) -> dict[str, Any]:
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
        exprs.append(pl.col(root_col).cast(_STR, strict=False).alias("root_lot_id"))
        group_cols.append("root_lot_id")
    if wafer_col:
        exprs.append(pl.col(wafer_col).cast(_STR, strict=False).alias("wafer_id"))
        group_cols.append("wafer_id")
    if lot_wf_col:
        exprs.append(pl.col(lot_wf_col).cast(_STR, strict=False).alias("lot_wf"))
    elif root_col and wafer_col:
        exprs.append(_lot_wf_expr(root_col, wafer_col).alias("lot_wf"))
    if "lot_wf" not in group_cols:
        group_cols.append("lot_wf")
    if shot_id_col:
        exprs.append(pl.col(shot_id_col).cast(_STR, strict=False).alias("shot_id"))
        group_cols.append("shot_id")
    if shot_x_col and shot_y_col:
        exprs.append(pl.col(shot_x_col).cast(_STR, strict=False).alias("shot_x"))
        exprs.append(pl.col(shot_y_col).cast(_STR, strict=False).alias("shot_y"))
        group_cols.extend(["shot_x", "shot_y"])
    exprs.append(pl.col(value_col).cast(pl.Float64, strict=False).alias("_metric_value"))
    scoped = lf.select(exprs).drop_nulls(subset=["_metric_value"])
    agg = (
        pl.col("_metric_value").mean().alias(value_alias)
        if kind_u == "INLINE"
        else pl.col("_metric_value").median().alias(value_alias)
    )
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


def _flowi_knob_query_terms(prompt: str, lots: list[str], xy_metrics: list[str]) -> list[str]:
    blocked = set(FLOWI_CHART_METRIC_STOP) | set(_STOP_TOKENS)
    blocked.update(_upper(v) for v in lots)
    metric_terms = set()
    for metric in xy_metrics:
        metric_terms.update(_metric_terms(metric))
    out = []
    seen = set()
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
        exprs.append(pl.col(root_col).cast(_STR, strict=False).alias("root_lot_id"))
        group_cols.append("root_lot_id")
    if wafer_col:
        exprs.append(pl.col(wafer_col).cast(_STR, strict=False).alias("wafer_id"))
        group_cols.append("wafer_id")
    if lot_wf_col:
        exprs.append(pl.col(lot_wf_col).cast(_STR, strict=False).alias("lot_wf"))
    elif root_col and wafer_col:
        exprs.append(_lot_wf_expr(root_col, wafer_col).alias("lot_wf"))
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
    inline = _flowi_metric_lf("INLINE", product, lots, inline_metric, "inline_value")
    if not inline.get("ok"):
        return inline
    et = _flowi_metric_lf("ET", product, lots, et_metric, "et_value")
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
            .limit(FLOWI_CHART_POINT_LIMIT)
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
        "x_label": f"INLINE {inline_metric} avg",
        "y_label": f"ET {et_metric} median",
        "join_cols": join_cols,
        "join_how": join_how,
        "corr": round(corr, 6) if corr is not None else None,
        "fit": fit,
        "color_by": (knob.get("display_name") if knob else "") or "",
        "color_values": [{"value": k, "count": v} for k, v in sorted(color_counts.items(), key=lambda kv: (-kv[1], kv[0]))],
        "filters": {"excluded_values": knob.get("excluded_values") or []} if knob else {},
        "sources": source_meta,
        "aggregations": {"INLINE": "avg", "ET": "median"},
    }


def _handle_chart_request(prompt: str, product: str, max_rows: int) -> dict:
    if not _contains_chart_intent(prompt):
        return {"handled": False}
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
    if not lots:
        requires.append("root_lot_id/fab_lot_id filter")
    rows = [
        {"field": "unit_action", "value": "dashboard.metric_scatter"},
        {"field": "sources", "value": ", ".join(sorted(sources)) or "-"},
        {"field": "metrics", "value": ", ".join(m["metric"] for m in metrics) or "-"},
        {"field": "operations", "value": ", ".join(operations)},
        {"field": "join_key_priority", "value": "shot/die key > lot_wf"},
        {"field": "INLINE aggregation", "value": "avg by lot_wf unless shot-level match exists"},
        {"field": "ET aggregation", "value": "median by lot_wf unless shot-level match exists"},
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
        "aggregations": {"INLINE": "avg", "ET": "median"},
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
        f"- 기본 집계: INLINE avg, ET median\n"
        "- shot/die key가 양쪽에 있으면 shot 단위로 먼저 매칭하고, 없을 때 lot_wf로 내려갑니다."
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
            "choices": choices[:4],
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
    return pl.scan_parquet([str(p) for p in files])


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
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID")
    knob_cols = [c for c in cols if _upper(c).startswith("KNOB_")]
    if not knob_cols:
        return {"handled": True, "intent": "lot_knobs", "answer": "ML_TABLE에서 KNOB_* 컬럼을 찾지 못했습니다.", "knobs": []}

    aliases = _product_aliases(product)
    filters = []
    if aliases and product_col:
        filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    if lot_matches:
        lot_cols = [c for c in (root_col, lot_col) if c]
        lot_expr = _or_contains(lot_cols, lot_matches)
        if lot_expr is not None:
            filters.append(lot_expr)
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
        table_cols = [c for c in (product_col, root_col, lot_col, wafer_col) if c] + table_knobs
        rename = {}
        if product_col:
            rename[product_col] = "product"
        if root_col:
            rename[root_col] = "root_lot_id"
        if lot_col:
            rename[lot_col] = "lot_id"
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
        "filters": {"lot": lot_matches, "product": sorted(aliases)},
    }


def _is_rag_update_prompt(prompt: str) -> bool:
    return bool(re.match(r"^\s*\[?\s*flow-i\s+rag\s+update\s*\]?", str(prompt or ""), flags=re.I))


def _handle_flowi_rag_update(prompt: str, me: dict[str, Any]) -> dict[str, Any]:
    username = me.get("username") or "user"
    role = me.get("role") or "user"
    try:
        out = semi_knowledge.structure_rag_update_from_prompt(prompt, username=username, role=role)
    except ValueError as e:
        return {
            "handled": True,
            "intent": "semiconductor_rag_update",
            "action": "append_custom_knowledge",
            "blocked": True,
            "answer": f"RAG Update 본문이 비어 있습니다. [flow-i RAG Update] 뒤에 구조화할 item/TEG/alias/판단 지식을 적어주세요. ({e})",
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


def _handle_flowi_query(
    prompt: str,
    product: str = "",
    max_rows: int = 12,
    allowed_keys: set[str] | None = None,
) -> dict:
    product = _product_hint(prompt, product)
    if allowed_keys is None or "diagnosis" in allowed_keys:
        diag_out = _handle_semiconductor_diagnosis_query(prompt, product, max_rows)
        if diag_out.get("handled"):
            return diag_out
    if allowed_keys is None or "dashboard" in allowed_keys or "ml" in allowed_keys:
        chart_out = _handle_chart_request(prompt, product, max_rows)
        if chart_out.get("handled"):
            return chart_out
    if allowed_keys is None or "splittable" in allowed_keys or "ml" in allowed_keys:
        fastest_out = _handle_fastest_knob_query(prompt, product, max_rows)
        if fastest_out.get("handled"):
            return fastest_out
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
    result["trace"] = _flowi_public_trace(
        prompt=prompt,
        allowed_keys=allowed_keys,
        result=result,
        agent_context=agent_context,
    )
    return result


def _run_flowi_chat(
    *,
    prompt: str,
    product: str,
    max_rows: int,
    me: dict[str, Any],
    source_ai: str = "",
    client_run_id: str = "",
    agent_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    username = me.get("username") or "user"
    prompt = (prompt or "").strip()
    if not prompt:
        raise HTTPException(400, "질문을 입력해주세요")

    source = _clean_source_ai(source_ai) if source_ai else ""
    client_run_id = str(client_run_id or "").strip()[:120]
    agent_context = agent_context if isinstance(agent_context, dict) else {}

    allowed_keys = _allowed_flowi_feature_keys(me)
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

    if _flowi_write_target_detected(prompt):
        if (me.get("role") or "user") == "admin":
            tool = _handle_admin_file_operation(prompt)
            answer = tool.get("answer") or "Admin 파일 작업 요청을 처리했습니다."
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
    tool = _handle_flowi_query(prompt, product, max_rows=max_rows, allowed_keys=allowed_keys)
    entries = _matched_feature_entrypoints(prompt, allowed_keys=allowed_keys)
    if entries:
        tool["feature_entrypoints"] = entries
    if not tool.get("handled") and entries:
        tool["answer"] = (
            "질문과 가장 가까운 기능 진입점입니다.\n"
            + "\n".join(f"- {e['title']}: {e['description']}" for e in entries[:3])
        )
    answer = tool.get("answer") or ""
    llm_info: dict[str, Any] = {"available": llm_adapter.is_available(), "used": False}
    user_ctx = _profile_context(username)
    feature_ctx = _feature_context(prompt, allowed_keys=allowed_keys)
    agent_ctx = _json_excerpt(agent_context) if agent_context else ""

    if llm_adapter.is_available() and not tool.get("blocked"):
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
                "지원 범위가 불확실하면 필요한 lot/step/item 조건을 물어보세요.\n\n"
                f"{source_line}"
                f"{context_line}"
                f"사용자 정보 Markdown:\n{user_ctx or '(없음)'}\n\n"
                f"단위기능 진입점:\n{feature_ctx}\n\n"
                f"사용자: {prompt}"
            )
        out = llm_adapter.complete(
            polish_prompt,
            system=(
                "Flowi는 사내 Flow 홈 화면의 fab 데이터 assistant입니다. 답변은 짧고 실행 가능하게 작성합니다. "
                "사용자 Markdown 정보가 있으면 담당 제품, 관심 공정, 선호 출력 방식을 반영합니다. "
                "요청이 애매하면 바로 실행한다고 말하지 말고 1/2/3 형태의 선택지를 제시합니다. "
                "INLINE은 기본 avg, ET는 기본 median이며 shot/die key가 있으면 lot_wf보다 우선합니다. "
                "일반 사용자의 원 data DB 또는 Files 수정/삭제/저장/업로드는 차단합니다. "
                "admin 파일 변경은 서버의 FLOWI_FILE_OP 단위기능 결과가 제공된 경우에만 그 결과를 설명합니다."
            ),
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


@router.get("/status")
def status(request: Request):
    me = current_user(request)
    allowed_keys = _allowed_flowi_feature_keys(me)
    local_tools = ["unit_feature_router"] if allowed_keys else []
    if "ettime" in allowed_keys:
        local_tools.insert(0, "et_wafer_median")
    if "splittable" in allowed_keys:
        local_tools.insert(1 if local_tools and local_tools[0] == "et_wafer_median" else 0, "lot_knobs")
    if "dashboard" in allowed_keys or "ml" in allowed_keys:
        local_tools.append("dashboard_scatter_plan")
    cfg = llm_adapter.get_config(redact=True)
    return {
        "available": llm_adapter.is_available(),
        "config": cfg,
        "flowi": {
            "requires_token": False,
            "admin_token_configured": llm_adapter.has_admin_token(),
            "local_tools": local_tools,
            "policy": FLOWI_READ_ONLY_POLICY,
            "allowed_features": sorted(allowed_keys),
            "entrypoints": [item for item in FLOWI_FEATURE_ENTRYPOINTS if item["key"] in allowed_keys],
            "unit_actions": {k: v for k, v in FLOWI_UNIT_ACTIONS.items() if k in allowed_keys},
        },
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


class FlowiProfileReq(BaseModel):
    notes: str = ""


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
    rating = (req.rating or "").strip().lower()
    if rating not in {"up", "down", "neutral"}:
        raise HTTPException(400, "rating must be up/down/neutral")
    tags = _normalize_feedback_tags(req.tags, rating)
    tool_summary = _flowi_tool_summary(req.tool)
    needs_review = rating != "up" or bool(req.golden_candidate) or any(tag != "correct" for tag in tags)
    rec = {
        "id": "ff_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:8],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "username": me.get("username") or "",
        "rating": rating,
        "intent": (req.intent or "").strip()[:80],
        "prompt_excerpt": (req.prompt or "").strip()[:500],
        "answer_excerpt": (req.answer or "").strip()[:800],
        "note": (req.note or "").strip()[:1000],
        "tags": tags,
        "expected_workflow": (req.expected_workflow or "").strip()[:160],
        "expected_answer": (req.expected_answer or "").strip()[:2000],
        "correct_route": (req.correct_route or "").strip()[:2000],
        "data_refs": (req.data_refs or "").strip()[:1000],
        "golden_candidate": bool(req.golden_candidate),
        "needs_review": needs_review,
        "review_status": "golden_candidate" if req.golden_candidate else ("needs_review" if needs_review else "ok"),
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


@router.post("/flowi/chat")
def flowi_chat(req: FlowiChatReq, request: Request):
    me = current_user(request)
    return _run_flowi_chat(
        prompt=req.prompt,
        product=req.product,
        max_rows=req.max_rows,
        me=me,
        agent_context=req.context,
    )


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
