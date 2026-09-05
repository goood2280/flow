"""core/home_dismissed_alerts.py — 사용자가 홈 화면에서 확인(읽음) 처리한 알람 영구 저장소.

스플릿테이블 Plan 불일치 등 파케이/캐시 기반 알람은 사용자가 확인 버튼을 누르거나
스플릿테이블로 이동하여 확인했을 때 사용자별 확인 기록을 저장하여
홈 화면 알람 목록에서 제외되도록 합니다.
"""
from __future__ import annotations

import datetime
import threading
from typing import Any

from core.paths import PATHS
from core.utils import load_json, save_json

DISMISSED_FILE = PATHS.data_root / "home_dismissed_alerts.json"
_LOCK = threading.RLock()


def _normalize_user(username: str) -> str:
    return str(username or "").strip().lower()


def _load_data() -> dict[str, Any]:
    with _LOCK:
        if not DISMISSED_FILE.is_file():
            return {"version": 1, "users": {}}
        try:
            data = load_json(DISMISSED_FILE, {"version": 1, "users": {}})
            if not isinstance(data, dict):
                return {"version": 1, "users": {}}
            if "users" not in data or not isinstance(data["users"], dict):
                data["users"] = {}
            return data
        except Exception:
            return {"version": 1, "users": {}}


def _save_data(data: dict[str, Any]) -> None:
    with _LOCK:
        DISMISSED_FILE.parent.mkdir(parents=True, exist_ok=True)
        save_json(DISMISSED_FILE, data, indent=2)


def is_alert_dismissed(username: str, alert_id: str) -> bool:
    """특정 사용자가 해당 알람을 확인(읽음) 처리했는지 조회합니다."""
    user_key = _normalize_user(username)
    aid = str(alert_id or "").strip()
    if not user_key or not aid:
        return False
    data = _load_data()
    user_dismissed = (data.get("users") or {}).get(user_key) or {}
    if isinstance(user_dismissed, dict):
        return aid in user_dismissed
    if isinstance(user_dismissed, list):
        return aid in user_dismissed
    return False


def dismiss_alerts(username: str, alert_ids: list[str]) -> None:
    """사용자가 확인한 알람 ID들을 저장합니다."""
    user_key = _normalize_user(username)
    if not user_key or not alert_ids:
        return
    now = datetime.datetime.now().isoformat(timespec="seconds")
    with _LOCK:
        data = _load_data()
        users = data.setdefault("users", {})
        user_dict = users.setdefault(user_key, {})
        if not isinstance(user_dict, dict):
            user_dict = {}
            users[user_key] = user_dict
        for aid in alert_ids:
            clean = str(aid or "").strip()
            if clean:
                user_dict[clean] = now
        # 최대 1000개까지만 유지
        if len(user_dict) > 1000:
            keys = sorted(user_dict.keys(), key=lambda k: user_dict[k], reverse=True)[:800]
            users[user_key] = {k: user_dict[k] for k in keys}
        _save_data(data)
