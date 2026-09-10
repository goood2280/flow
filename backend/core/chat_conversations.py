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
        state["title"] = content[:80]


def history(state):
    return [{"role": item["role"], "content": item["content"][:4000]}
            for item in state["messages"] if not item.get("error")][-20:]
