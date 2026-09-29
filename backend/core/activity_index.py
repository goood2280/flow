"""Local, rebuildable index of the retained audit JSONL (never modifies the log).

Each process/host shares the local SQLite cache through transactions. Only complete
new lines are ingested; a replaced/truncated source rebuilds the derived index.
The cache lives off shared data roots so SQLite locking never depends on SMB/NFS.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
from contextlib import contextmanager


# 관리자 활동 통계(summary/features/users)는 매 요청마다 인덱스 쓰기 락을 잡고
# 전체 이벤트를 여러 번 집계했다. activity.jsonl 은 모든 API 요청마다 한 줄씩
# 늘어나므로 mtime 키로는 캐시가 거의 맞지 않는다 — 대신 짧은 TTL 로 같은 화면을
# 다시 열거나 기간 버튼을 오갈 때의 재집계를 없앤다. 로그 목록(page)은 캐시하지 않는다.
try:
    _RESULT_TTL_SEC = max(0.0, float(os.environ.get("FLOW_ACTIVITY_SUMMARY_TTL_SEC", "") or 30.0))
except ValueError:
    _RESULT_TTL_SEC = 30.0
_RESULT_MEMO: dict = {}
_RESULT_MEMO_LOCK = threading.Lock()


def _memoized(name, source, args, compute):
    if _RESULT_TTL_SEC <= 0:
        return compute()
    key = (name, str(source), args, dt.date.today().isoformat())
    now = time.monotonic()
    with _RESULT_MEMO_LOCK:
        hit = _RESULT_MEMO.get(key)
        if hit is not None and now - hit[0] < _RESULT_TTL_SEC:
            return copy.deepcopy(hit[1])
    value = compute()
    with _RESULT_MEMO_LOCK:
        for stale in [k for k, v in _RESULT_MEMO.items() if now - v[0] >= _RESULT_TTL_SEC]:
            _RESULT_MEMO.pop(stale, None)
        _RESULT_MEMO[key] = (now, value)
    return copy.deepcopy(value)


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


def _excluded(names):
    """Normalized, order-stable usernames to leave out of the dashboard aggregates."""
    return tuple(sorted({str(name).strip().lower() for name in (names or ()) if str(name).strip()}))


def _exclude_sql(excluded):
    # `user` is the stripped username; SQLite lower() is enough for ASCII account ids.
    if not excluded:
        return "", ()
    return f" AND lower(user) NOT IN ({','.join('?' * len(excluded))})", tuple(excluded)


def _week_start(value):
    day = value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value))
    return day - dt.timedelta(days=day.weekday())


def summary(source, days=0, include_recent=True, exclude_users=()):
    excluded = _excluded(exclude_users)
    return _memoized("summary", source, (_days(days), bool(include_recent), excluded),
                     lambda: _summary(source, days, include_recent, excluded))


def _summary(source, days=0, include_recent=True, excluded=()):
    days = _days(days)
    cutoff = _window(days)
    skip_sql, skip_args = _exclude_sql(excluded)
    with _snapshot(source) as db:
        where = "day IS NOT NULL AND day >= ? AND authenticated=1" + skip_sql
        args = (cutoff, *skip_args)
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
        daily = dict(db.execute("SELECT day, COUNT(DISTINCT user) FROM events WHERE authenticated=1 AND day BETWEEN ? AND ?" + skip_sql + " GROUP BY day",
                               (start_day.isoformat(), end_day.isoformat(), *skip_args)).fetchall())
        month_start = f"{start_month // 12:04d}-{start_month % 12 + 1:02d}"
        month_end = f"{end_month // 12:04d}-{end_month % 12 + 1:02d}"
        monthly = dict(db.execute("SELECT substr(day,1,7) m, COUNT(DISTINCT user) FROM events WHERE authenticated=1 AND substr(day,1,7) BETWEEN ? AND ?" + skip_sql + " GROUP BY m",
                                  (month_start, month_end, *skip_args)).fetchall())
        end_week = _week_start(end_day) if not days and last else _week_start(today)
        start_week = _week_start(start_day) if not days and first else end_week - dt.timedelta(weeks=25)
        week_sql = "date(day, '-' || ((CAST(strftime('%w', day) AS INTEGER) + 6) % 7) || ' days')"
        weekly = dict(db.execute(
            f"SELECT {week_sql} w, COUNT(DISTINCT user) FROM events "
            "WHERE authenticated=1 AND day BETWEEN ? AND ?" + skip_sql + " GROUP BY w",
            (start_week.isoformat(), end_day.isoformat() if not days and last else today.isoformat(), *skip_args),
        ).fetchall())
        by_week = dict(db.execute(
            f"SELECT {week_sql} w, COUNT(*) FROM events WHERE {where} GROUP BY w ORDER BY w",
            args,
        ).fetchall())
        by_month = dict(db.execute(
            f"SELECT substr(day,1,7) m, COUNT(*) FROM events WHERE {where} GROUP BY m ORDER BY m",
            args,
        ).fetchall())
        recent = [json.loads(row[0]) for row in db.execute(f"SELECT payload FROM events WHERE {where} ORDER BY timestamp DESC, id LIMIT 3000", args)] if include_recent else []
        result = {
            "window_days": days, "activity_start": first, "activity_end": last,
            "excluded_users": list(excluded),
            "excluded_count": db.execute(
                "SELECT COUNT(*) FROM events WHERE day IS NOT NULL AND day >= ? AND authenticated=1"
                f" AND lower(user) IN ({','.join('?' * len(excluded))})", (cutoff, *excluded)).fetchone()[0] if excluded else 0,
            "total": db.execute(f"SELECT COUNT(*) FROM events WHERE {where}", args).fetchone()[0],
            "unattributed_count": db.execute("SELECT COUNT(*) FROM events WHERE day IS NOT NULL AND day >= ? AND authenticated=0", (cutoff,)).fetchone()[0],
            "by_user": counts("user", 20), "by_action": counts("action", 30),
            "by_tab": counts("tab"), "by_day": counts("day"),
            "by_week": by_week, "by_month": by_month, "recent": recent,
            "active_users_by_day": {(start_day + dt.timedelta(days=i)).isoformat(): daily.get((start_day + dt.timedelta(days=i)).isoformat(), 0)
                                    for i in range((end_day - start_day).days + 1)},
            "active_users_by_week": {(start_week + dt.timedelta(weeks=i)).isoformat(): weekly.get((start_week + dt.timedelta(weeks=i)).isoformat(), 0)
                                      for i in range(((end_week - start_week).days // 7) + 1)},
            "active_users_by_month": {f"{i // 12:04d}-{i % 12 + 1:02d}": monthly.get(f"{i // 12:04d}-{i % 12 + 1:02d}", 0)
                                      for i in range(start_month, end_month + 1)},
        }
    return result


def features(source, days=0, exclude_users=()):
    excluded = _excluded(exclude_users)
    return _memoized("features", source, (_days(days), excluded), lambda: _features(source, days, excluded))


def _features(source, days=0, excluded=()):
    days = _days(days)
    skip_sql, skip_args = _exclude_sql(excluded)
    with _snapshot(source) as db:
        args = (_window(days), *skip_args)
        where = "day IS NOT NULL AND day >= ? AND authenticated=1 AND feature!=''" + skip_sql
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
        unattributed = db.execute("SELECT COUNT(*) FROM events WHERE day IS NOT NULL AND day >= ? AND authenticated=0", args[:1]).fetchone()[0]
        return dict(window_days=days, features=out, feature_count=len(out), unattributed_count=unattributed,
                    excluded_users=list(excluded))


def page(source, limit=100, offset=0, username="", action="", tab="", days=0, exact_user=False, exclude_users=()):
    limit, offset = max(1, min(500, int(limit))), max(0, int(offset))
    clauses, args = [], []
    excluded = _excluded(exclude_users)
    if excluded:
        clauses.append(f"lower(trim(username)) NOT IN ({','.join('?' * len(excluded))})")
        args.extend(excluded)
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
    return _memoized("users", source, (), lambda: _users(source))


def _users(source):
    with _snapshot(source) as db:
        return {"users": [dict(username=row[0], count=row[1], last=row[2]) for row in db.execute(
            "SELECT username, COUNT(*), MAX(timestamp) last FROM events WHERE username!='' GROUP BY username ORDER BY last DESC, MIN(id)")]}
