"""Local, rebuildable index of the retained audit JSONL (never modifies the log).

Each process/host shares the local SQLite cache through transactions. Only complete
new lines are ingested; a replaced/truncated source rebuilds the derived index.
The cache lives off shared data roots so SQLite locking never depends on SMB/NFS.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from contextlib import contextmanager


def _cache_path(source: Path) -> Path:
    root = Path(tempfile.gettempdir()) / f"flow-activity-{getattr(os, 'getuid', lambda: 'local')()}"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = hashlib.sha256(str(source.resolve()).encode()).hexdigest()
    return root / f"{key}.sqlite3"


def _anchor(stream, offset):
    stream.seek(max(0, offset - 256))
    return hashlib.sha256(stream.read(min(offset, 256))).hexdigest()


def _ingest(db, source):
    meta_row = db.execute("SELECT value FROM metadata WHERE key='source'").fetchone()
    meta = json.loads(meta_row[0]) if meta_row else {}
    try:
        stream = source.open("rb")
    except FileNotFoundError:
        db.execute("DELETE FROM events")
        db.execute("DELETE FROM metadata")
        return
    with stream:
        stat = os.fstat(stream.fileno())
        identity = [stat.st_dev, stat.st_ino]
        offset = meta.get("offset", 0)
        unchanged = (meta.get("identity") == identity and meta.get("size") == stat.st_size
                     and meta.get("mtime") == stat.st_mtime_ns)
        if unchanged:
            return
        if (meta.get("identity") != identity or stat.st_size < offset
                or (meta.get("size") == stat.st_size and meta.get("mtime") != stat.st_mtime_ns)
                or (offset and _anchor(stream, offset) != meta.get("anchor"))):
            db.execute("DELETE FROM events")
            offset = 0
        stream.seek(offset)
        batch = []
        while stream.tell() < stat.st_size:
            line = stream.readline(stat.st_size - stream.tell())
            if not line.endswith(b"\n"):
                break  # Retry an in-flight writer's partial line on the next request.
            offset = stream.tell()
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    continue
            except (ValueError, UnicodeError):
                continue
            timestamp = str(row.get("timestamp") or row.get("time") or "").strip()
            try:
                date = dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00")).date().isoformat()
            except ValueError:
                date = None
            username = str(row.get("username") or row.get("actor") or "")
            user = username.strip()
            action = str(row.get("action") or "")
            tab = str(row.get("tab") or "")
            batch.append((timestamp, date, username, user, int(bool(user and user.lower() != "anonymous")),
                          action, tab, action.strip().split(":", 1)[0], username.lower(),
                          action.lower(), tab.lower(), json.dumps(row, ensure_ascii=False)))
            if len(batch) >= 2000:
                _insert(db, batch)
                batch.clear()
        _insert(db, batch)
        meta = dict(identity=identity, offset=offset, size=stat.st_size,
                    mtime=stat.st_mtime_ns, anchor=_anchor(stream, offset))
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('source', ?)", (json.dumps(meta),))


def _insert(db, batch):
    db.executemany("""INSERT INTO events
        (timestamp, day, username, user, authenticated, action, tab, feature,
         username_lower, action_lower, tab_lower, payload) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", batch)


@contextmanager
def _snapshot(source):
    db = sqlite3.connect(_cache_path(Path(source)), timeout=60)
    db.row_factory = sqlite3.Row
    try:
        db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
        db.execute("""CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY, timestamp TEXT, day TEXT, username TEXT, user TEXT,
            authenticated INTEGER, action TEXT, tab TEXT, feature TEXT,
            username_lower TEXT, action_lower TEXT, tab_lower TEXT, payload TEXT)""")
        db.execute("CREATE INDEX IF NOT EXISTS events_day ON events(day)")
        db.execute("BEGIN IMMEDIATE")
        _ingest(db, Path(source))
        db.commit()
        db.execute("BEGIN")
        yield db
    finally:
        db.close()


def _days(value):
    try:
        return max(0, min(3650, int(value)))
    except (ValueError, TypeError):
        return 0


def _window(days):
    return (dt.date.today() - dt.timedelta(days=days - 1)).isoformat() if days else ""


def summary(source, days=0, include_recent=True):
    days = _days(days)
    cutoff = _window(days)
    with _snapshot(source) as db:
        where = "day IS NOT NULL AND day >= ? AND authenticated=1"
        args = (cutoff,)
        first, last = db.execute("SELECT MIN(day), MAX(day) FROM events").fetchone()
        def counts(column, limit=None):
            expression = {"action": "COALESCE(NULLIF(action,''),'(unknown)')",
                          "tab": "COALESCE(NULLIF(tab,''),'(none)')"}.get(column, column)
            order = "k" if column == "day" else "n DESC, MIN(id)"
            sql = f"SELECT {expression} k, COUNT(*) n FROM events WHERE {where} GROUP BY k ORDER BY {order}"
            if limit:
                sql += f" LIMIT {int(limit)}"
            return {row[0]: row[1] for row in db.execute(sql, args)}
        today = dt.date.today()
        start_day = dt.date.fromisoformat(first) if not days and first else today - dt.timedelta(days=29)
        end_day = dt.date.fromisoformat(last) if not days and last else today
        start_month = start_day.year * 12 + start_day.month - 1 if not days and first else today.year * 12 + today.month - 12
        end_month = end_day.year * 12 + end_day.month - 1
        daily = dict(db.execute("SELECT day, COUNT(DISTINCT user) FROM events WHERE authenticated=1 AND day BETWEEN ? AND ? GROUP BY day",
                               (start_day.isoformat(), end_day.isoformat())).fetchall())
        month_start = f"{start_month // 12:04d}-{start_month % 12 + 1:02d}"
        month_end = f"{end_month // 12:04d}-{end_month % 12 + 1:02d}"
        monthly = dict(db.execute("SELECT substr(day,1,7) m, COUNT(DISTINCT user) FROM events WHERE authenticated=1 AND substr(day,1,7) BETWEEN ? AND ? GROUP BY m",
                                 (month_start, month_end)).fetchall())
        recent = [json.loads(row[0]) for row in db.execute(f"SELECT payload FROM events WHERE {where} ORDER BY timestamp DESC, id LIMIT 3000", args)] if include_recent else []
        result = {
            "window_days": days, "activity_start": first, "activity_end": last,
            "total": db.execute(f"SELECT COUNT(*) FROM events WHERE {where}", args).fetchone()[0],
            "unattributed_count": db.execute("SELECT COUNT(*) FROM events WHERE day IS NOT NULL AND day >= ? AND authenticated=0", args).fetchone()[0],
            "by_user": counts("user", 20), "by_action": counts("action", 30),
            "by_tab": counts("tab"), "by_day": counts("day"), "recent": recent,
            "active_users_by_day": {(start_day + dt.timedelta(days=i)).isoformat(): daily.get((start_day + dt.timedelta(days=i)).isoformat(), 0)
                                    for i in range((end_day - start_day).days + 1)},
            "active_users_by_month": {f"{i // 12:04d}-{i % 12 + 1:02d}": monthly.get(f"{i // 12:04d}-{i % 12 + 1:02d}", 0)
                                      for i in range(start_month, end_month + 1)},
        }
    return result


def features(source, days=0):
    days = _days(days)
    with _snapshot(source) as db:
        args = (_window(days),)
        where = "day IS NOT NULL AND day >= ? AND authenticated=1 AND feature!=''"
        out = []
        # Group once for all features, rather than rescanning events for each one.
        users, actions = {}, {}
        for row in db.execute(f"SELECT feature, user FROM events WHERE {where} GROUP BY feature, user ORDER BY user", args):
            users.setdefault(row[0], []).append(row[1])
        for row in db.execute(f"SELECT feature, trim(action) a, COUNT(*) n FROM events WHERE {where} GROUP BY feature, a ORDER BY n DESC, MIN(id)", args):
            bucket = actions.setdefault(row[0], {})
            if len(bucket) < 5:
                bucket[row[1]] = row[2]
        for row in db.execute(f"SELECT feature, COUNT(*), MIN(timestamp), MAX(timestamp) FROM events WHERE {where} GROUP BY feature ORDER BY COUNT(*) DESC, MIN(id)", args):
            people = users[row[0]]
            out.append(dict(feature=row[0], count=row[1], first_seen=row[2], last_seen=row[3],
                            user_count=len(people), users=people[:20], top_actions=actions[row[0]]))
        unattributed = db.execute("SELECT COUNT(*) FROM events WHERE day IS NOT NULL AND day >= ? AND authenticated=0", args).fetchone()[0]
        return dict(window_days=days, features=out, feature_count=len(out), unattributed_count=unattributed)


def page(source, limit=100, offset=0, username="", action="", tab="", days=0, exact_user=False):
    limit, offset = max(1, min(500, int(limit))), max(0, int(offset))
    clauses, args = [], []
    if days > 0:
        clauses.append("substr(timestamp,1,10) >= ?")
        args.append(_window(_days(days)))
    if exact_user:
        clauses.append("username = ?")
        args.append(username)
    elif username.strip():
        clauses.append("instr(username_lower, ?) > 0")
        args.append(username.strip().lower())
    for column, value in (("action_lower", action), ("tab_lower", tab)):
        if value.strip():
            clauses.append(f"instr({column}, ?) > 0")
            args.append(value.strip().lower())
    where = " AND ".join(clauses) or "1"
    with _snapshot(source) as db:
        total = db.execute(f"SELECT COUNT(*) FROM events WHERE {where}", args).fetchone()[0]
        logs = [json.loads(row[0]) for row in db.execute(f"SELECT payload FROM events WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?", [*args, limit, offset])]
        return dict(logs=logs, total=total, limit=limit, offset=offset, has_more=offset + len(logs) < total)


def users(source):
    with _snapshot(source) as db:
        return {"users": [dict(username=row[0], count=row[1], last=row[2]) for row in db.execute(
            "SELECT username, COUNT(*), MAX(timestamp) last FROM events WHERE username!='' GROUP BY username ORDER BY last DESC, MIN(id)")]}
