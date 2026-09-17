"""Admin-reviewed question interpretations, stored only in the operator's DB.

Rules match whole questions, never fuzzy neighbors. They rewrite requests or
choose existing read-only actions; they cannot execute SQL, paths or code.
"""
from __future__ import annotations

from contextlib import closing
from contextlib import contextmanager
from contextvars import ContextVar
import json
import re
import sqlite3
import time
from uuid import UUID, uuid4

from core.paths import PATHS

MAX_QUESTIONS = 4
MAX_LLM_CALLS = 6
KINDS = {"product", "lot", "item", "source", "step", "intent"}
SLOTS = {"product", "lot", "item", "step"}
_SLOT = re.compile(r"\{([a-z_]+)\}")
_TRACE = ContextVar("flowi_route_events", default=None)


@contextmanager
def trace_scope():
    events = []
    token = _TRACE.set(events)
    try:
        yield events
    finally:
        _TRACE.reset(token)


def record(stage, **details):
    events = _TRACE.get()
    if events is not None:
        events.append({"stage": stage, **details})


def storage_path():
    return PATHS.db_root / "flowi" / "question_routes.sqlite3"


def _db():
    path = storage_path()
    if not PATHS.db_root.is_dir():
        raise ValueError("설정된 DB 폴더가 없습니다. 데이터 루트를 확인하세요.")
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=5000")
    db.execute("CREATE TABLE IF NOT EXISTS rules (id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at REAL NOT NULL)")
    return db


def actions():
    from core.data_chat_features import ACTIONS
    return [{"id": "auto", "description": "기존 분류기로 교정 질문 처리"},
            *[{"id": key, "description": value["description"]} for key, value in ACTIONS.items()]]


def list_rules():
    if not storage_path().exists():
        return []
    with closing(_db()) as db:
        return [json.loads(row[0]) for row in db.execute("SELECT payload FROM rules ORDER BY updated_at DESC LIMIT 500")]


def _clean(value, limit, label, required=False):
    text = str(value or "").strip()
    if len(text) > limit or "\x00" in text or (required and not text):
        raise ValueError(f"{label}: {'1~' if required else '최대 '}{limit}자로 입력하세요.")
    return text


def _pattern(question):
    slots = _SLOT.findall(question)
    if len(slots) != len(set(slots)) or set(slots) - SLOTS:
        raise ValueError("제품·랏·항목·공정 변수는 {product}, {lot}, {item}, {step}을 각각 한 번만 사용하세요.")
    literal = _SLOT.sub("", question)
    if "{" in literal or "}" in literal or len(literal.strip()) < 2:
        raise ValueError("질문 패턴에는 변수를 제외한 구체적인 문구가 2자 이상 필요합니다.")
    parts, end = [], 0
    for match in _SLOT.finditer(question):
        parts.append(re.escape(question[end:match.start()]))
        # Identifiers only: a template cannot swallow another sentence or task.
        parts.append(f"(?P<{match[1]}>[A-Za-z0-9가-힣_.-]{{1,100}})")
        end = match.end()
    parts.append(re.escape(question[end:]))
    return re.compile("".join(parts), re.I)


def save_rule(data, actor):
    row = {key: _clean(data.get(key), limit, key, required) for key, limit, required in (
        ("title", 100, True), ("question", 2000, True), ("normalized_question", 2000, True), ("notes", 1000, False))}
    _pattern(row["question"])
    if set(_SLOT.findall(row["normalized_question"])) - set(_SLOT.findall(row["question"])):
        raise ValueError("교정 질문의 변수는 원문 질문에 있는 변수만 사용하세요.")
    if set(_SLOT.findall(row["question"])) - set(_SLOT.findall(row["normalized_question"])):
        raise ValueError("교정 질문에도 원문의 모든 변수를 유지하세요. 과거 제품·랏 값으로 고정할 수 없습니다.")
    if re.search(r"[{}]", _SLOT.sub("", row["normalized_question"])):
        raise ValueError("교정 질문의 변수 형식을 확인하세요.")
    row["route"] = str(data.get("route") or "auto")
    if row["route"] not in {a["id"] for a in actions()}:
        raise ValueError("지원되는 읽기 전용 처리 경로를 선택하세요.")
    segments = data.get("segments") or []
    if not isinstance(segments, list) or len(segments) > 20:
        raise ValueError("해석 부분은 최대 20개입니다.")
    row["segments"] = []
    for segment in segments:
        if not isinstance(segment, dict) or segment.get("kind") not in KINDS:
            raise ValueError("해석 종류를 제품·랏·항목·DB·공정·의도 중 선택하세요.")
        item = {"kind": segment["kind"], "text": _clean(segment.get("text"), 200, "원문 부분", True),
                "value": _clean(segment.get("value"), 300, "해석 값", True)}
        if item["text"].casefold() not in row["question"].casefold():
            raise ValueError("각 원문 부분은 질문 패턴에 실제로 포함되어야 합니다.")
        if set(_SLOT.findall(item["value"])) - set(_SLOT.findall(row["question"])):
            raise ValueError("해석 값에 원문에 없는 변수를 사용할 수 없습니다.")
        if _SLOT.search(item["text"]) and item["value"] != item["text"]:
            raise ValueError("변수로 받은 제품·랏·항목·공정은 고정 값으로 교정할 수 없습니다. text와 value에 같은 변수를 유지하세요.")
        if any(s["text"].casefold() == item["text"].casefold() and s["value"] != item["value"] for s in row["segments"]):
            raise ValueError("같은 질문 표현에 서로 다른 교정 값을 지정할 수 없습니다.")
        row["segments"].append(item)
    row.update(id=str(UUID(data["id"])) if data.get("id") else str(uuid4()),
               enabled=data.get("enabled") is True, updated_at=time.time(), actor=str(actor),
               conversation_id=str(UUID(data["conversation_id"])) if data.get("conversation_id") else "",
               message_id=str(UUID(data["message_id"])) if data.get("message_id") else "")
    with closing(_db()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        if data.get("id") and not db.execute("SELECT 1 FROM rules WHERE id=?", (row["id"],)).fetchone():
            raise FileNotFoundError(row["id"])
        if not data.get("id") and db.execute("SELECT COUNT(*) FROM rules").fetchone()[0] >= 500:
            raise ValueError("규칙은 최대 500개입니다. 사용하지 않는 규칙을 정리하세요.")
        db.execute("INSERT OR REPLACE INTO rules VALUES (?, ?, ?)",
                   (row["id"], json.dumps(row, ensure_ascii=False), row["updated_at"]))
    return row


def delete_rule(rule_id):
    with closing(_db()) as db, db:
        if not db.execute("DELETE FROM rules WHERE id=?", (str(UUID(rule_id)),)).rowcount:
            raise FileNotFoundError(rule_id)


def _sub(text, bindings):
    return _SLOT.sub(lambda m: bindings.get(m[1], m[0]), text)


def resolve(question):
    matches = []
    for row in list_rules():
        if not row.get("enabled"):
            continue
        match = _pattern(row["question"]).fullmatch(question.strip())
        if match:
            bindings = match.groupdict()
            segments = [{**s, "text": _sub(s["text"], bindings), "value": _sub(s["value"], bindings)} for s in row["segments"]]
            normalized = _sub(row["normalized_question"], bindings)
            # Simultaneous literal replacements: never re-rewrite an inserted value.
            replacements = {s["text"].casefold(): s["value"] for s in segments if s["kind"] != "intent" and s["text"] != s["value"]}
            if replacements:
                pattern = re.compile(r"(?<![A-Za-z0-9_])(?:" + "|".join(re.escape(s) for s in sorted(replacements, key=len, reverse=True)) + r")(?![A-Za-z0-9_])", re.I)
                normalized = pattern.sub(lambda m: replacements[m[0].casefold()], normalized)
            matches.append({"rule": row, "bindings": bindings,
                            "segments": segments, "normalized_question": normalized})
    if len(matches) > 1:
        return {"matched": False, "ambiguous": True, "candidates": [{"id": m["rule"]["id"], "title": m["rule"]["title"]} for m in matches]}
    return {"matched": bool(matches), "ambiguous": False, **(matches[0] if matches else {})}


def overview():
    return {"nodes": [
        {"id": "input", "label": "질문 입력", "detail": "물음표·줄바꿈·명시적 연결어로 복수 질문 분리 (최대 4개)"},
        {"id": "learning", "label": "관리자 해석", "detail": "활성화된 전체 문장/변수 패턴 매칭. 중복 일치 시 확인 요청"},
        {"id": "scope", "label": "제품·대상 확인", "detail": "실제 DB 제품 확인 및 용어 후보 확인"},
        {"id": "handlers", "label": "전용 처리", "detail": "Report → Split → Split 선두 → TEG → 기존 차트 편집"},
        {"id": "rules", "label": "규칙 분류", "detail": "위치·랏관리·트래커·관심랏·인폼·대시보드. 관리자 지정 경로는 읽기 전용 기능으로 전달"},
        {"id": "llm", "label": "LLM 분류", "detail": "규칙으로 분류하지 못할 때 허용 기능 중 하나 선택. 전체 요청 최대 6회 (재시도 포함)"},
        {"id": "execute", "label": "검증·실제 조회", "detail": "제품·매개변수·기존 권한 검증. 변경 작업은 기존 미리보기와 승인 절차 유지"},
        {"id": "result", "label": "결과·기록", "detail": "질문별 응답·실제 경로·호출 차감·출처 저장. 확인 필요 시 후속 질문 보류"},
    ], "edges": [["input", "learning"], ["learning", "scope"], ["scope", "handlers"],
                 ["scope", "rules"], ["handlers", "rules"], ["rules", "llm"], ["rules", "execute"],
                 ["llm", "execute"], ["handlers", "result"], ["execute", "result"]],
        "actions": actions(), "rules": list_rules(), "storage_path": str(storage_path()),
        "max_questions": MAX_QUESTIONS, "max_llm_calls": MAX_LLM_CALLS}
