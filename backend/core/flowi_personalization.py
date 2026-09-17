"""Owner-scoped, explicitly saved, parameterized chat recipes.

These records are advisory context. They contain no conversation text, result rows,
SQL, or concrete product/lot defaults, and confer no tool execution permission.
"""
from __future__ import annotations

import re
import sqlite3
import time
from contextlib import closing
from uuid import UUID, uuid4

from core.paths import PATHS
from core import chat_conversations


_FEATURES = {
    "dashboard": "대시보드 요약", "chart": "차트 분석", "tracker": "트래커 조회",
    "yield_map": "수율 지도 확인", "teg": "TEG 데이터 확인",
    "splittable": "SplitTable 검토", "location": "Lot 현위치 확인",
    "lot_progress": "Lot 진행 확인", "watchlist": "관심 Lot 확인",
    "informs": "인폼 확인", "lot_management": "Lot 관리 조회",
    "report": "리포트 준비", "table": "데이터 표 조회", "lot_requests": "Lot 요청 조회",
}
_ACTION_METHODS = {
    "lot_management.table": ("Lot 관리 표", "질문에서 제품과 필요하면 Lot을 확인한다. Lot 관리 표를 조회하고 현재 상태를 요약한다."),
    "lot_management.my_lots": ("내 관심 Lot", "현재 사용자의 관심 Lot을 조회한다. 상태와 우선 확인할 항목을 요약한다."),
    "lot_management.status": ("Lot 상태", "질문에서 제품과 Lot을 확인한다. 해당 Lot의 현재 공정, 설명, wafer 수량을 요약한다."),
    "dashboard.summary": ("대시보드 요약", "질문에서 제품 범위를 확인한다. 대시보드의 TAT, DPML, WIP 요약을 조회하고 변화와 주의 항목을 설명한다."),
    "dashboard.stuck_lots": ("장기 체류 Lot", "질문에서 제품, 조회 기간, 체류 시간 기준을 확인한다. 기준 이상 체류한 Lot을 조회하고 시간순으로 검토한다."),
    "dashboard.charts": ("대시보드 차트 목록", "현재 사용자에게 보이는 대시보드 차트 목록을 조회한다. 질문 목적에 맞는 차트를 선택하도록 안내한다."),
    "dashboard.chart_data": ("대시보드 차트 분석", "질문에서 차트를 확인한다. 저장된 차트 설정과 최신 데이터를 조회하고 축, 범위, 추세를 설명한다."),
    "lot_progress.lookup": ("Lot 진행 조회", "질문에서 제품 또는 Lot·wafer 식별자를 확인한다. 현재 WIP 행을 조회하고 공정 위치를 설명한다."),
    "lot_requests.list": ("Lot 요청 조회", "질문에서 상태와 제품 등 검색 조건을 확인한다. 보이는 Lot 요청을 조회하고 진행 상태를 요약한다."),
    "yield_map.map": ("수율 지도 확인", "질문에서 제품과 Lot 또는 wafer를 확인한다. 수율 지도와 BIN 분포를 조회하고 공간적 패턴을 설명한다."),
    "tracker.issues": ("ET 이슈 조회", "질문에서 상태·분류·제품 조건을 확인한다. ET 트래커 이슈를 조회하고 우선순위를 요약한다."),
    "tracker.issue": ("ET 이슈 상세", "질문에서 이슈를 확인한다. 해당 이슈의 ET 측정값과 상태를 조회하고 주의점을 설명한다."),
    "watchlist.lots": ("관심 Lot 조회", "현재 사용자의 관심 Lot을 조회한다. 진행 상태와 주의 항목을 요약한다."),
    "informs.recent": ("최근 인폼 조회", "질문에서 제품과 모듈 범위를 확인한다. 최근 공정 인폼을 조회하고 관련 항목을 요약한다."),
    "informs.by_lot": ("Lot 인폼 조회", "질문에서 Lot을 확인한다. 해당 Lot의 공정 인폼과 스레드를 조회하고 시간순으로 정리한다."),
    "teg.locations": ("TEG 위치 조회", "질문에서 제품과 TEG 종류를 확인한다. shot 내 기준 위치와 크기를 조회하고 단위를 설명한다."),
    "teg.coordinates": ("TEG 좌표 조회", "질문에서 제품과 TEG 종류를 확인한다. shot별 절대 좌표와 반경을 조회하고 단위를 설명한다."),
    "teg.mapfiles": ("TEG Mapfile 검증", "질문에서 제품을 확인한다. Mapfile 검증 상태와 이슈 수를 조회하고 재확인이 필요한 항목을 요약한다."),
}
_FEATURE_METHODS = {
    "splittable": ("SplitTable 계획 검토", "질문에서 제품, root Lot, wafer 범위와 S0/S1 배정 의도를 확인한다. 실제 기준과 현재 계획을 읽고 변경 미리보기를 제시한다. 반영은 사용자의 별도 승인을 받는다."),
    "report": ("리포트 준비", "질문에서 차트와 보고서 목적을 확인한다. 현재 차트 정의를 검증하고 리포트 초안을 만든 뒤 결과를 검토한다."),
    "chart": ("차트 분석", "질문에서 데이터 범위, 축, 집계 방식을 확인한다. 현재 데이터를 조회해 차트를 만들고 추세와 한계를 설명한다."),
    "location": ("Lot 현위치 확인", "질문에서 제품과 Lot을 확인한다. 현재 위치와 공정 단계를 조회하고 확인 시점을 설명한다."),
    "table": ("데이터 표 조회", "질문에서 데이터 범위와 조건을 확인한다. 표를 조회하고 행 수, 기준, 주요 결과를 설명한다."),
}
_CHART_TYPES = {"bar": "막대", "line": "꺾은선", "scatter": "산점", "heatmap": "히트맵", "histogram": "히스토그램"}
_AGGREGATIONS = {"avg": "평균", "mean": "평균", "sum": "합계", "count": "건수", "min": "최솟값", "max": "최댓값", "median": "중앙값"}
_SKILL_STOPWORDS = {
    "결과", "검토", "기준", "대상", "설명", "요약", "요청", "작업", "절차", "조회", "질문", "확인",
    "show", "check", "result", "review", "request", "query", "the", "and",
}
_FEATURE_HINTS = {
    "dashboard": ("대시보드", "dashboard", "tat", "dpml", "정체"),
    "chart": ("차트", "그래프", "chart", "분포", "추세"),
    "tracker": ("트래커", "tracker", "이슈"),
    "yield_map": ("수율", "yield", "웨이퍼맵", "wafermap"),
    "teg": ("teg", "맵파일", "mapfile", "좌표"),
    "splittable": ("스플릿", "split", "knob", "노브"),
    "location": ("위치", "어디", "현재공정"),
    "lot_progress": ("진행", "공정", "wip"),
    "watchlist": ("관심랏", "watchlist"),
    "informs": ("인폼", "inform"),
    "lot_management": ("랏관리", "lotmanage"),
    "report": ("리포트", "보고서", "report"),
    "lot_requests": ("랏요청", "lotrequest"),
}
_FORBIDDEN = re.compile(
    r"\b(?:select|insert|update|delete|drop|alter|create)\s+\S+|\b(?:where|join)\s+\S+"
    r"|```|\b(?:lot|wafer|product|제품|품목|로트)\s*(?:id|명|번호|코드)?\s*[:=]\s*\S+"
    r"|\b(?:LOT|WAFER|WF)[-_]?[A-Za-z0-9]{2,}\b",
    re.IGNORECASE,
)


def _db():
    path = PATHS.data_root / "home_personalization.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("""CREATE TABLE IF NOT EXISTS recipes (
        id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL,
        procedure TEXT NOT NULL, feature TEXT NOT NULL, action TEXT NOT NULL DEFAULT '',
        shared INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL, updated_at REAL NOT NULL)""")
    cols = {row[1] for row in connection.execute("PRAGMA table_info(recipes)")}
    if "action" not in cols:
        connection.execute("ALTER TABLE recipes ADD COLUMN action TEXT NOT NULL DEFAULT ''")
    if "auto" not in cols:
        connection.execute("ALTER TABLE recipes ADD COLUMN auto INTEGER NOT NULL DEFAULT 0")
    connection.execute("CREATE INDEX IF NOT EXISTS recipes_owner ON recipes(owner)")
    return connection


def _owner(username):
    if not username or not str(username).strip():
        raise ValueError("사용자 정보가 없습니다.")
    return str(username)


def _public(row):
    d = {key: row[key] for key in ("id", "owner", "title", "procedure", "feature", "action", "created_at", "updated_at")}
    d["shared"] = bool(row["shared"])
    d["auto"] = bool(row["auto"]) if "auto" in row.keys() else False
    return d


def list_skills(username):
    username = _owner(username)
    with closing(_db()) as connection:
        rows = connection.execute("""SELECT * FROM recipes WHERE owner=? OR shared=1
            ORDER BY updated_at DESC""", (username,)).fetchall()
    return [_public(row) for row in rows]


def list_all_skills():
    with closing(_db()) as connection:
        rows = connection.execute("SELECT * FROM recipes ORDER BY updated_at DESC").fetchall()
    return [_public(row) for row in rows]


def get_skill(username, skill_id):
    username = _owner(username)
    skill_id = str(UUID(str(skill_id)))
    with closing(_db()) as connection:
        row = connection.execute("SELECT * FROM recipes WHERE id=? AND (owner=? OR shared=1)", (skill_id, username)).fetchone()
    if row is None:
        raise FileNotFoundError(skill_id)
    return _public(row)


def resolve_skill_context(username, skill_id):
    skill = get_skill(username, skill_id)
    return {key: skill[key] for key in ("id", "title", "procedure", "feature", "action")}


def _match_words(value):
    compact = re.sub(r"[\s_-]+", "", str(value or "").casefold())
    words = {
        word for word in re.findall(r"[a-z0-9]{3,}|[가-힣]{2,}", str(value or "").casefold())
        if word not in _SKILL_STOPWORDS
    }
    return compact, words


def resolve_skill_for_prompt(username, prompt):
    """Select one visible recipe as advisory context without exposing skill UI.

    Selection is intentionally conservative: a skill must match a domain hint or
    a meaningful title word. The recipe never grants permissions and concrete
    identifiers still come only from the current request/context.
    """
    compact_prompt, prompt_words = _match_words(prompt)
    if not compact_prompt:
        return None
    inferred = {
        feature for feature, hints in _FEATURE_HINTS.items()
        if any(re.sub(r"[\s_-]+", "", hint.casefold()) in compact_prompt for hint in hints)
    }
    ranked = []
    for skill in list_skills(username):
        _title_compact, title_words = _match_words(skill.get("title"))
        overlap = prompt_words & title_words
        feature_match = str(skill.get("feature") or "") in inferred
        if not feature_match and not overlap:
            continue
        score = (20 if feature_match else 0) + (5 * len(overlap))
        if skill.get("owner") == username:
            score += 1
        ranked.append((score, float(skill.get("updated_at") or 0), skill))
    if not ranked:
        return None
    skill = max(ranked, key=lambda item: (item[0], item[1]))[2]
    context = {key: skill[key] for key in ("id", "title", "procedure", "feature", "action")}
    context["selection"] = "automatic"
    return context


def _source_values(response):
    """Only identity-like source fields are checked; their values are never stored."""
    tool = response.get("tool") or {}
    values = []
    for section in (tool.get("context"), response.get("context")):
        if isinstance(section, dict):
            values.extend(str(section.get(key) or "").strip() for key in
                          ("product", "lot_id", "root_lot_id", "fab_lot_id", "wafer_id", "teg_product", "chart_id", "issue_id"))
    table = tool.get("table") or {}
    for row in (table.get("rows") or []) if isinstance(table, dict) else []:
        if isinstance(row, dict):
            values.extend(str(row.get(key) or "").strip() for key in
                          ("product", "lot_id", "root_lot_id", "wafer_id", "chart_id", "issue_id"))
    return {value.casefold() for value in values if len(value) >= 2}


def _result_message(username, conversation_id, message_id):
    state = chat_conversations.read(_owner(username), conversation_id)
    message = next((item for item in state["messages"] if item.get("id") == message_id), None)
    if not message or message.get("role") != "assistant" or message.get("error"):
        raise ValueError("성공한 응답을 선택하세요.")
    response = message.get("response") or {}
    tool = response.get("tool") or {}
    if (not isinstance(tool, dict) or response.get("ok") is False or response.get("error")
            or tool.get("ok") is False or tool.get("error")
            or (tool.get("approval") or {}).get("status") == "pending"):
        raise ValueError("성공한 작업 결과에서만 스킬을 만들 수 있습니다.")
    return response, tool


def draft_from_result(username, conversation_id, message_id):
    _response, tool = _result_message(username, conversation_id, message_id)
    feature = str(tool.get("feature") or "").split(".")[0]
    if not feature:
        action = str(tool.get("action") or "")
        feature = action.split(".")[0]
    if feature not in _FEATURES:
        raise ValueError("이 결과는 재사용 절차로 변환할 수 없습니다.")
    action = str(tool.get("action") or "")
    if action and action not in _ACTION_METHODS:
        raise ValueError("이 작업은 재사용 절차로 변환할 수 없습니다.")
    title, procedure = _ACTION_METHODS.get(action, _FEATURE_METHODS.get(feature, (_FEATURES[feature],
        f"질문에서 대상을 확인한다. {_FEATURES[feature]} 결과를 검토하고 근거와 범위를 설명한다.")))
    if action == "dashboard.chart_data":
        chart = tool.get("chart_result") or {}
        if isinstance(chart, dict):
            chart_type = _CHART_TYPES.get(str(chart.get("chart_type") or "").lower())
            aggregation = _AGGREGATIONS.get(str(chart.get("aggregation") or "").lower())
            if chart_type:
                procedure += f" {chart_type} 차트 형식을 사용한다."
            if aggregation:
                procedure += f" {aggregation} 집계를 사용한다."
    return {"title": title, "procedure": procedure, "feature": feature, "action": action}


def _validate_text(title, procedure, source_values=()):
    title, procedure = str(title or "").strip(), str(procedure or "").strip()
    if not (1 <= len(title) <= 80 and 10 <= len(procedure) <= 1000):
        raise ValueError("제목(1~80자)과 절차(10~1000자)를 입력하세요.")
    if _FORBIDDEN.search(title) or _FORBIDDEN.search(procedure):
        raise ValueError("SQL, 제품·Lot 식별값 대신 매개변수화된 절차만 저장할 수 있습니다.")
    from core.data_chat import available_product_names
    forbidden = {str(name).casefold() for name in available_product_names() if len(str(name)) >= 2}
    forbidden.update(source_values)
    combined = title.casefold() + "\n" + procedure.casefold()
    if any(re.search(r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])", combined) for name in forbidden):
        raise ValueError("실제 제품·Lot·결과 식별값은 스킬에 저장할 수 없습니다.")
    return title, procedure


def save_from_result(username, conversation_id, message_id, title, procedure, shared=False):
    draft = draft_from_result(username, conversation_id, message_id)
    response, _tool = _result_message(username, conversation_id, message_id)
    title, procedure = _validate_text(title, procedure, _source_values(response))
    now, skill_id = time.time(), str(uuid4())
    with closing(_db()) as connection:
        with connection:
            connection.execute("""INSERT INTO recipes
                (id, owner, title, procedure, feature, action, shared, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (skill_id, _owner(username), title, procedure, draft["feature"], draft["action"], int(bool(shared)), now, now))
    return get_skill(username, skill_id)


def update_skill(username, skill_id, title, procedure, shared):
    username = _owner(username)
    skill_id = str(UUID(str(skill_id)))
    title, procedure = _validate_text(title, procedure)
    with closing(_db()) as connection:
        with connection:
            cursor = connection.execute("""UPDATE recipes SET title=?, procedure=?, shared=?, updated_at=?
                WHERE id=? AND owner=?""", (title, procedure, int(bool(shared)), time.time(), skill_id, username))
            if not cursor.rowcount:
                raise FileNotFoundError(skill_id)
    return get_skill(username, skill_id)


def delete_skill(username, skill_id):
    with closing(_db()) as connection:
        with connection:
            cursor = connection.execute("DELETE FROM recipes WHERE id=? AND owner=?", (str(UUID(str(skill_id))), _owner(username)))
            if not cursor.rowcount:
                raise FileNotFoundError(skill_id)


def draft_from_any_conversation(conversation_id, message_id):
    decoded = chat_conversations.read_any(conversation_id)
    message = next((item for item in decoded.get("messages", []) if str(item.get("id")) == str(message_id)), None)
    if not message or message.get("role") != "assistant" or message.get("error"):
        raise ValueError("성공한 응답을 선택하세요.")
    response = message.get("response") or {}
    tool = response.get("tool") or {}
    if (not isinstance(tool, dict) or response.get("ok") is False or response.get("error")
            or tool.get("ok") is False or tool.get("error")
            or (tool.get("approval") or {}).get("status") == "pending"):
        raise ValueError("성공한 작업 결과에서만 스킬을 만들 수 있습니다.")
    feature = str(tool.get("feature") or "").split(".")[0]
    if not feature:
        action = str(tool.get("action") or "")
        feature = action.split(".")[0]
    if feature not in _FEATURES:
        feature = "table"
    action = str(tool.get("action") or "")
    title, procedure = _ACTION_METHODS.get(action, _FEATURE_METHODS.get(feature, (_FEATURES.get(feature, "작업"),
        f"질문에서 대상을 확인한다. {_FEATURES.get(feature, '결과')}를 검토하고 근거와 범위를 설명한다.")))
    return {
        "title": title,
        "procedure": procedure,
        "feature": feature,
        "action": action,
        "conversation_id": str(conversation_id),
        "message_id": str(message_id),
        "user": decoded.get("username") or "",
    }


def save_curated_skill(owner, title, procedure, feature, action="", shared=True, auto=False):
    title = str(title or "").strip()
    procedure = str(procedure or "").strip()
    if not (1 <= len(title) <= 80 and 10 <= len(procedure) <= 1000):
        raise ValueError("제목(1~80자)과 절차(10~1000자)를 입력하세요.")
    now, skill_id = time.time(), str(uuid4())
    with closing(_db()) as connection:
        with connection:
            connection.execute("""INSERT INTO recipes
                (id, owner, title, procedure, feature, action, shared, auto, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (skill_id, _owner(owner), title, procedure, feature, action, int(bool(shared)), int(bool(auto)), now, now))
        row = connection.execute("SELECT * FROM recipes WHERE id=?", (skill_id,)).fetchone()
    return _public(row)


def update_skill_admin(skill_id, title, procedure, shared=True):
    skill_id = str(UUID(str(skill_id)))
    title = str(title or "").strip()
    procedure = str(procedure or "").strip()
    if not (1 <= len(title) <= 80 and 10 <= len(procedure) <= 1000):
        raise ValueError("제목(1~80자)과 절차(10~1000자)를 입력하세요.")
    with closing(_db()) as connection:
        with connection:
            cursor = connection.execute("""UPDATE recipes SET title=?, procedure=?, shared=?, updated_at=?
                WHERE id=?""", (title, procedure, int(bool(shared)), time.time(), skill_id))
            if not cursor.rowcount:
                raise FileNotFoundError(skill_id)
            row = connection.execute("SELECT * FROM recipes WHERE id=?", (skill_id,)).fetchone()
    return _public(row)


def delete_skill_admin(skill_id):
    skill_id = str(UUID(str(skill_id)))
    with closing(_db()) as connection:
        with connection:
            cursor = connection.execute("DELETE FROM recipes WHERE id=?", (skill_id,))
            if not cursor.rowcount:
                raise FileNotFoundError(skill_id)


def auto_create_skill_from_prompt(prompt: str, category: str = ""):
    clean_prompt = str(prompt or "").strip()
    if len(clean_prompt) < 2:
        return None
    cat = (category or "").lower().strip()
    feature = cat if cat in _FEATURES else "dashboard" if any(w in clean_prompt for w in ("분포", "점유율", "대시보드")) else "location" if any(w in clean_prompt for w in ("위치", "어디", "선두")) else "splittable" if any(w in clean_prompt for w in ("split", "스플릿")) else "table"
    action = f"{feature}.summary" if feature == "dashboard" else ""
    title_val, proc_val = _ACTION_METHODS.get(action, _FEATURE_METHODS.get(feature, (_FEATURES.get(feature, "조회"), f"질문에서 대상을 확인한다. {_FEATURES.get(feature, '결과')}를 검토하고 근거와 범위를 설명한다.")))
    # Only a generic operation template may cross the private chat boundary.
    title = f"[자동 스킬] {title_val}"
    with closing(_db()) as connection:
        if connection.execute("SELECT id FROM recipes WHERE title=? AND owner='system'", (title,)).fetchone():
            return None
    procedure = proc_val
    return save_curated_skill(
        owner="system",
        title=title,
        procedure=procedure[:1000],
        feature=feature,
        action=action,
        shared=True,
        auto=True,
    )
