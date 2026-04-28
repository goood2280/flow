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
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
import polars as pl

from core.paths import PATHS
from core.utils import _STR, load_json
from core.auth import current_user, require_admin
from core import llm_adapter
from routers.auth import read_users


router = APIRouter(prefix="/api/llm", tags=["llm"])
logger = logging.getLogger("flow.llm.router")
FLOWI_FEEDBACK_FILE = PATHS.data_root / "flowi_feedback.jsonl"
FLOWI_USER_DIR = PATHS.data_root / "flowi_users"
FLOWI_READ_ONLY_POLICY = {
    "read_only": True,
    "applies_to": ["user", "admin"],
    "blocked_targets": ["raw data DB", "Files", "DB root files", "product reformatter files"],
}
FLOWI_PROFILE_START = "<!-- FLOWI_USER_NOTES_START -->"
FLOWI_PROFILE_END = "<!-- FLOWI_USER_NOTES_END -->"
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
    "dashboard": ["dashboard", "대시보드", "차트", "trend", "추세", "그래프", "시각화"],
    "splittable": ["split", "split table", "splittable", "스플릿", "스플릿테이블", "plan", "actual", "mismatch", "매칭", "불일치"],
    "tracker": ["tracker", "트래커", "issue", "이슈", "gantt", "간트", "lot 이슈"],
    "inform": ["inform", "인폼", "공유", "메일", "공지", "보고"],
    "meeting": ["meeting", "회의", "아젠다", "회의록", "action item", "액션아이템"],
    "calendar": ["calendar", "캘린더", "일정", "변경점", "change", "schedule"],
    "ettime": ["et report", "ettime", "et 레포트", "et 리포트", "median", "wf별", "wafer별", "측정", "eta"],
    "waferlayout": ["wafer layout", "wf layout", "layout", "레이아웃", "shot", "die", "teg"],
    "ml": ["ml", "머신러닝", "상관", "correlation", "feature", "importance", "윈도우", "window"],
    "tablemap": ["table map", "tablemap", "테이블맵", "관계", "relation", "join", "column map", "컬럼"],
    "devguide": ["devguide", "개발", "api", "문서", "가이드", "architecture"],
}
FLOWI_DEFAULT_TABS = {
    "filebrowser", "dashboard", "splittable", "ettime", "waferlayout",
    "inform", "meeting", "calendar",
}
FLOWI_NEW_DEFAULT_TABS = {"inform", "meeting", "calendar", "ettime", "waferlayout"}
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
        "needs": ["source", "x/y column", "optional time/filter"],
        "outputs": ["chart", "trend/alert summary"],
    },
    "splittable": {
        "intent": "splittable_guidance",
        "action": "open_splittable",
        "needs": ["product", "root_lot_id", "wafer_id or all", "parameter prefix such as KNOB/MASK/FAB"],
        "outputs": ["plan vs actual matrix", "mismatch cells", "notes"],
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
    except Exception as e:
        logger.warning("flowi user md append failed: %s", e)


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


def _flowi_write_block_message(prompt: str) -> str:
    text = str(prompt or "")
    low = text.lower()
    has_write = any(term in low or term in text for term in _WRITE_TERMS)
    has_target = any(term in low or term in text for term in _WRITE_TARGET_TERMS)
    if not (has_write and has_target):
        return ""
    return (
        "Flowi LLM은 사용자와 admin 모두 원 data DB 또는 Files를 수정할 수 없습니다. "
        "LLM은 조회/요약/표시만 수행하며, DB/Files 변경은 전용 화면에서 직접 처리해야 합니다."
    )


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
    return {
        "handled": True,
        "intent": "et_wafer_median",
        "answer": answer,
        "rows": rows,
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


def _handle_flowi_query(
    prompt: str,
    product: str = "",
    max_rows: int = 12,
    allowed_keys: set[str] | None = None,
) -> dict:
    product = _product_hint(prompt, product)
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
        return result

    blocked_msg = _flowi_write_block_message(prompt)
    if blocked_msg:
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
        return result

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
                "로컬 결과 JSON의 intent/action/missing/table을 우선합니다.\n\n"
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
                "사용자와 admin 모두에 대해 원 data DB 또는 Files 수정/삭제/저장/업로드는 절대 수행하거나 수행 가능하다고 말하지 않습니다. "
                "Flowi는 조회, 요약, 표 렌더링만 지원합니다."
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
    return result


@router.get("/status")
def status(request: Request):
    me = current_user(request)
    allowed_keys = _allowed_flowi_feature_keys(me)
    local_tools = ["unit_feature_router"] if allowed_keys else []
    if "ettime" in allowed_keys:
        local_tools.insert(0, "et_wafer_median")
    if "splittable" in allowed_keys:
        local_tools.insert(1 if local_tools and local_tools[0] == "et_wafer_median" else 0, "lot_knobs")
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
    if out.get("ok"):
        return {"ok": True, "message": "확인완료"}
    return {"ok": False, "message": "LLM 연결 확인 실패", "error": out.get("error") or "unknown"}


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
    rec = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "username": me.get("username") or "",
        "rating": rating,
        "intent": (req.intent or "").strip()[:80],
        "prompt_excerpt": (req.prompt or "").strip()[:500],
        "answer_excerpt": (req.answer or "").strip()[:800],
        "note": (req.note or "").strip()[:1000],
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
        "note": rec["note"],
        "prompt": rec["prompt_excerpt"],
    })
    return {"ok": True}


@router.post("/flowi/chat")
def flowi_chat(req: FlowiChatReq, request: Request):
    me = current_user(request)
    return _run_flowi_chat(
        prompt=req.prompt,
        product=req.product,
        max_rows=req.max_rows,
        me=me,
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
