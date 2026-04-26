"""core/notify.py v7.0.0 (v8.8.33) — 이벤트 허브 + 구독 룰.

v7.0: emit_event(event_type, actor, target_user, payload) 단일 진입점 +
      admin_settings.notify_rules[username] 로 유저별 on/off.
      기본 활성 이벤트: my_plan_changed / my_meeting_minutes_added /
                       my_tracker_comment / my_tracker_status_changed /
                       tracker_step_reached / my_inform_comment.
"""
import json, datetime, csv, uuid
from pathlib import Path
from core.paths import PATHS

NOTIFY_DIR = PATHS.data_root / "notifications"
NOTIFY_DIR.mkdir(parents=True, exist_ok=True)

# 이벤트 → 기본 메시지 템플릿 (actor/payload 치환).
# admin_settings.notify_rules.{username}.disabled = [] 로 유저별 off 가능.
_DEFAULT_RULES = {
    "my_plan_changed": True,
    "my_meeting_minutes_added": True,
    "my_meeting_action_changed": True,
    "my_tracker_comment": True,
    "my_tracker_status_changed": True,
    "tracker_step_reached": True,
    "my_inform_comment": True,
    "my_calendar_event_invited": True,
}

_EVENT_META = {
    "my_plan_changed": ("plan 변경", "info"),
    "my_meeting_minutes_added": ("회의록 갱신", "info"),
    "my_meeting_action_changed": ("회의 액션 갱신", "info"),
    "my_tracker_comment": ("이슈 댓글", "info"),
    "my_tracker_status_changed": ("이슈 상태 변경", "warn"),
    "tracker_step_reached": ("Lot step 도달", "warn"),
    "my_inform_comment": ("인폼 댓글", "info"),
    "my_calendar_event_invited": ("캘린더 초대", "info"),
}


def _admin_settings_path() -> Path:
    return PATHS.data_root / "admin_settings.json"


def _read_admin_settings_safe() -> dict:
    fp = _admin_settings_path()
    if fp.is_file():
        try:
            return json.loads(fp.read_text("utf-8")) or {}
        except Exception:
            return {}
    return {}


def _write_admin_settings_safe(cfg: dict):
    fp = _admin_settings_path()
    try:
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")
    except Exception:
        pass


def _load_user_rules(username: str) -> dict:
    """admin_settings.notify_rules[username] 조회. 없으면 _DEFAULT_RULES."""
    cfg = _read_admin_settings_safe()
    rules = (cfg.get("notify_rules") or {}).get(username) or {}
    out = dict(_DEFAULT_RULES)
    out.update({k: bool(v) for k, v in rules.items() if k in _DEFAULT_RULES})
    return out


def _is_event_enabled(target_user: str, event_type: str) -> bool:
    rules = _load_user_rules(target_user)
    return bool(rules.get(event_type, True))


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


# ─────────────────────────────────────────────────────────
# v7.0: 이벤트 허브
# ─────────────────────────────────────────────────────────
def emit_event(event_type: str, actor: str = "", target_user: str = "",
               title: str = "", body: str = "", payload: dict | None = None) -> bool:
    """이벤트 단일 진입점.
    - target_user 가 비어있거나 actor 와 같으면 no-op (자신이 한 행동은 알림 X)
    - 유저 구독 룰에서 off 면 no-op
    - 성공 시 bell 알림 생성, return True
    """
    if not target_user or not event_type:
        return False
    if actor and actor == target_user:
        return False
    if not _is_event_enabled(target_user, event_type):
        return False
    meta_title, tone = _EVENT_META.get(event_type, (event_type, "info"))
    final_title = title or f"[{meta_title}]"
    if not body:
        if actor and payload:
            body = f"{actor} · {_format_payload(payload)}"
        elif actor:
            body = f"by {actor}"
        else:
            body = meta_title
    # payload 는 참조용 — 별도 필드로 저장해 FE 에서 라우팅 판단 가능
    entry = {
        "id": str(uuid.uuid4())[:8],
        "title": final_title,
        "body": body,
        "type": tone,
        "event": event_type,
        "actor": actor,
        "payload": payload or {},
        "read": False,
        "timestamp": datetime.datetime.now().isoformat(),
    }
    fp = NOTIFY_DIR / f"{target_user}.jsonl"
    with open(fp, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return True


def _format_payload(payload: dict) -> str:
    """작은 payload 를 읽기 좋은 한 줄로."""
    if not payload:
        return ""
    parts = []
    for k in ("product", "root_lot_id", "lot_id", "wafer_id", "step_id",
              "issue_id", "meeting_id", "cell", "column", "field",
              "old_status", "new_status", "text"):
        v = payload.get(k)
        if v not in (None, "", [], {}):
            sv = str(v)
            if len(sv) > 60:
                sv = sv[:57] + "..."
            parts.append(f"{k}={sv}")
    return " · ".join(parts[:5])


def list_rules(username: str) -> dict:
    """유저의 현재 구독 룰. default 와 override 합쳐서 반환."""
    return _load_user_rules(username)


def save_rules(username: str, rules: dict):
    """유저 구독 룰 업데이트. True/False 만 허용."""
    cfg = _read_admin_settings_safe()
    all_rules = dict(cfg.get("notify_rules") or {})
    cleaned = {k: bool(v) for k, v in (rules or {}).items() if k in _DEFAULT_RULES}
    all_rules[username] = cleaned
    cfg["notify_rules"] = all_rules
    _write_admin_settings_safe(cfg)


def event_catalog() -> list:
    """FE 알림 설정 UI 용 — (key, label, default) 목록."""
    return [
        {"key": k, "label": _EVENT_META.get(k, (k, ""))[0], "default": bool(v)}
        for k, v in _DEFAULT_RULES.items()
    ]
