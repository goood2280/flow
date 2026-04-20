"""core/notify.py v6.0.0 - Notification system with UUID + batch dismiss"""
import json, datetime, csv, uuid
from pathlib import Path
from core.paths import PATHS

NOTIFY_DIR = PATHS.data_root / "notifications"
NOTIFY_DIR.mkdir(parents=True, exist_ok=True)


def _read_all(username: str) -> list:
    fp = NOTIFY_DIR / f"{username}.jsonl"
    notifs = []
    needs_save = False
    if fp.exists():
        for line in fp.read_text(encoding="utf-8").strip().split("\n"):
            if not line:
                continue
            try:
                n = json.loads(line)
                if "id" not in n:
                    n["id"] = str(uuid.uuid4())[:8]
                    needs_save = True
                notifs.append(n)
            except:
                pass
    # Persist auto-generated IDs so they stay stable
    if needs_save and notifs:
        _write_all(username, notifs)
    return notifs


def _write_all(username: str, notifs: list):
    fp = NOTIFY_DIR / f"{username}.jsonl"
    with open(fp, "w", encoding="utf-8") as f:
        for n in notifs:
            f.write(json.dumps(n, ensure_ascii=False) + "\n")


def send_notify(to_user: str, title: str, body: str, type: str = "info"):
    fp = NOTIFY_DIR / f"{to_user}.jsonl"
    entry = {"id": str(uuid.uuid4())[:8], "title": title, "body": body,
             "type": type, "read": False,
             "timestamp": datetime.datetime.now().isoformat()}
    with open(fp, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def send_to_admins(title: str, body: str, type: str = "approval"):
    if PATHS.users_csv.exists():
        with open(PATHS.users_csv, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("role") == "admin":
                    send_notify(row["username"], title, body, type)


def get_notifications(username: str, unread_only: bool = False) -> list:
    notifs = _read_all(username)
    if unread_only:
        notifs = [n for n in notifs if not n.get("read")]
    return notifs[-50:]


def mark_all_read(username: str):
    notifs = _read_all(username)
    for n in notifs:
        n["read"] = True
    _write_all(username, notifs)


def mark_read_by_ids(username: str, ids: list):
    notifs = _read_all(username)
    id_set = set(ids)
    for n in notifs:
        if n.get("id") in id_set:
            n["read"] = True
    _write_all(username, notifs)


def dismiss_notification(username: str, index: int):
    notifs = _read_all(username)
    if 0 <= index < len(notifs):
        notifs.pop(index)
    _write_all(username, notifs)


def dismiss_by_ids(username: str, ids: list):
    notifs = _read_all(username)
    id_set = set(ids)
    notifs = [n for n in notifs if n.get("id") not in id_set]
    _write_all(username, notifs)
