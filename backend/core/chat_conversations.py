"""Private, durable home chats. One SQLite file per owner/conversation.

Only the selected conversation is supplied as LLM history; archives are not
global instructions or automatically approved knowledge. Data lives outside
the source bundle, under the operator-owned data root.
"""
from contextlib import closing, contextmanager
import hashlib
import json
import sqlite3
import time
from uuid import UUID, uuid4

from core.paths import PATHS


def _directory(username):
    if not username:
        raise ValueError("사용자 정보가 없습니다.")
    return PATHS.data_root / "home_conversations" / hashlib.sha256(username.encode()).hexdigest()


def _path(username, conversation_id):
    return _directory(username) / (str(UUID(str(conversation_id))) + ".sqlite3")


def _decode(connection, conversation_id):
    meta = dict(connection.execute("SELECT key, value FROM metadata"))
    messages = [json.loads(row[0]) for row in connection.execute("SELECT payload FROM messages ORDER BY seq")]
    return {"id": conversation_id, "title": meta.get("title", "새 대화"),
            "updated_at": float(meta.get("updated_at", 0)),
            "context": json.loads(meta.get("context", "{}")), "messages": messages}


def read(username, conversation_id):
    path = _path(username, conversation_id)
    if not path.is_file():
        raise FileNotFoundError(conversation_id)
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as connection:
        return _decode(connection, str(UUID(str(conversation_id))))


def list_conversations(username):
    rows = []
    for path in _directory(username).glob("*.sqlite3"):
        with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as connection:
            meta = dict(connection.execute("SELECT key, value FROM metadata"))
        if "title" in meta:
            rows.append({"id": path.stem, "title": meta["title"], "updated_at": float(meta.get("updated_at", 0))})
    return sorted(rows, key=lambda row: row["updated_at"], reverse=True)


_RESULT_KINDS = (
    ("report_template", "report"), ("chart_result", "chart"), ("chart_panels", "chart"),
    ("split_view", "table"), ("table", "table"), ("download_job", "download"),
)


def _result_kind(tool: dict) -> str:
    for key, kind in _RESULT_KINDS:
        if tool.get(key):
            return kind
    if tool.get("rows") or tool.get("teg") or tool.get("related_tegs"):
        return "table"
    return ""


def _awaiting_input(tool: dict) -> bool:
    approval = tool.get("approval") or {}
    if tool.get("feature") == "report.template" and tool.get("report_template"):
        return False
    return bool(tool.get("needs_input") or (tool.get("clarification") or {}).get("kind")
                or tool.get("missing") or approval.get("status") == "pending")


def recent_results(username, limit: int = 8, scan_conversations: int = 12):
    """Latest completed results (charts, tables, reports) across a user's chats.

    The home landing lists them so work done through Flow-i can be reopened
    without scrolling a conversation. Only summaries leave this function; the
    full result is loaded again with the conversation."""
    out = []
    for summary in list_conversations(username)[:max(1, int(scan_conversations))]:
        try:
            state = read(username, summary["id"])
        except (FileNotFoundError, ValueError, sqlite3.Error):
            continue
        question = ""
        for message in state["messages"]:
            if message.get("role") == "user":
                content = str(message.get("content") or "").strip()
                # "5"·"avg" 같은 선택지 답은 질문이 아니다 — 원래 요청을 유지한다.
                if len(content) > 6 or not question:
                    question = content
                continue
            response = message.get("response") if isinstance(message.get("response"), dict) else {}
            tool = response.get("tool") if isinstance(response.get("tool"), dict) else {}
            if message.get("error") or not tool or tool.get("error") or tool.get("blocked") or _awaiting_input(tool):
                continue
            kind = _result_kind(tool)
            if not kind:
                continue
            chart = tool.get("chart_result") if isinstance(tool.get("chart_result"), dict) else {}
            report = tool.get("report_template") if isinstance(tool.get("report_template"), dict) else {}
            scope = tool.get("context") if isinstance(tool.get("context"), dict) else {}
            query_scope = tool.get("query_scope") if isinstance(tool.get("query_scope"), dict) else {}
            out.append({
                "conversation_id": state["id"],
                "message_id": message.get("id") or "",
                "conversation_title": state.get("title") or "",
                "question": question[:160],
                "answer": str(message.get("content") or "")[:200],
                "feature": str(tool.get("feature") or tool.get("action") or kind),
                "action": str(tool.get("action") or ""),
                "kind": kind,
                "title": str(report.get("name") or chart.get("title") or "")[:120],
                "chart_type": str(chart.get("chart_type") or ""),
                "product": str(scope.get("product") or query_scope.get("product") or chart.get("product") or ""),
                "lot": str(scope.get("root_lot_id") or scope.get("lot_id") or query_scope.get("root_lot_id") or ""),
                "saved_chart_id": str((tool.get("saved_chart") or {}).get("id") or ""),
                "created_at": float(message.get("created_at") or state.get("updated_at") or 0),
            })
    out.sort(key=lambda row: row["created_at"], reverse=True)
    unique, seen = [], set()
    for row in out:
        key = (row["title"], row["question"], row["kind"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique[:max(1, int(limit))]


def list_all_conversations(limit: int = 100):
    rows = []
    base_dir = PATHS.data_root / "home_conversations"
    if not base_dir.is_dir():
        return []
    for path in base_dir.glob("*/*.sqlite3"):
        try:
            with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as connection:
                meta = dict(connection.execute("SELECT key, value FROM metadata"))
                msg_count = connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            if "title" in meta:
                rows.append({
                    "id": path.stem,
                    "title": meta.get("title", "대화"),
                    "username": meta.get("username", ""),
                    "updated_at": float(meta.get("updated_at", 0)),
                    "message_count": msg_count,
                })
        except Exception:
            continue
    rows.sort(key=lambda r: r["updated_at"], reverse=True)
    return rows[:limit]


def read_any(conversation_id):
    cid = str(UUID(str(conversation_id)))
    base_dir = PATHS.data_root / "home_conversations"
    matching = list(base_dir.glob(f"*/{cid}.sqlite3"))
    if not matching:
        raise FileNotFoundError(conversation_id)
    path = matching[0]
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as connection:
        decoded = _decode(connection, cid)
        meta = dict(connection.execute("SELECT key, value FROM metadata"))
        decoded["username"] = meta.get("username", "")
        return decoded


@contextmanager
def turn(username, conversation_id=None):
    conversation_id = str(UUID(str(conversation_id))) if conversation_id else str(uuid4())
    path = _path(username, conversation_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=0.2)
    try:
        connection.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("CREATE TABLE IF NOT EXISTS messages (seq INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
        connection.commit()
        # Cross-process serialization prevents two tabs from overwriting a turn.
        connection.execute("BEGIN IMMEDIATE")
        state = _decode(connection, conversation_id)
        yield state
        for key in ("title", "context", "updated_at"):
            value = json.dumps(state[key], ensure_ascii=False) if key == "context" else str(state[key])
            connection.execute("INSERT OR REPLACE INTO metadata VALUES (?, ?)", (key, value))
        connection.execute("INSERT OR REPLACE INTO metadata VALUES (?, ?)", ("username", str(username)))
        existing = connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        connection.executemany("INSERT INTO messages(payload) VALUES (?)", [
            (json.dumps(message, ensure_ascii=False, default=str),) for message in state["messages"][existing:]
        ])
        connection.commit()
    finally:
        connection.close()


def append(state, role, content, **extra):
    state["messages"].append({"id": str(uuid4()), "role": role, "content": content,
                              "created_at": time.time(), **extra})
    state["updated_at"] = time.time()
    if role == "user" and len(state["messages"]) == 1:
        # A pasted Excel range starts with tabs and line breaks.
        state["title"] = " ".join(str(content).split())[:80]


def history(state):
    return [{"role": item["role"], "content": item["content"][:4000]}
            for item in state["messages"] if not item.get("error")][-20:]
