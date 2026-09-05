"""core/watchlist.py — 사용자별 관심랏(Watchlist) 관리 모듈.

스플릿테이블 및 랏관리에서 사용자가 지정한 관심랏 목록을 저장하고,
해당 랏에 스플릿 변경, 메모 등록, 랏관리 정보 갱신 등의 활동이 있을 때
구독자 목록을 조회할 수 있도록 지원합니다.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from core.paths import PATHS
from core.utils import load_json, save_json

WATCHLIST_FILE = PATHS.data_root / "user_watchlist.json"
_LOCK = threading.RLock()


def _normalize_lot(lot_id: str) -> str:
    return str(lot_id or "").strip().upper()


def _normalize_user(username: str) -> str:
    return str(username or "").strip().lower()


def _load_data() -> dict[str, Any]:
    with _LOCK:
        if not WATCHLIST_FILE.is_file():
            return {"version": 1, "users": {}}
        try:
            data = load_json(WATCHLIST_FILE, {"version": 1, "users": {}})
            if not isinstance(data, dict):
                return {"version": 1, "users": {}}
            if "users" not in data or not isinstance(data["users"], dict):
                data["users"] = {}
            return data
        except Exception:
            return {"version": 1, "users": {}}


def _save_data(data: dict[str, Any]) -> None:
    with _LOCK:
        WATCHLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
        save_json(WATCHLIST_FILE, data, indent=2)


def get_user_watchlist(username: str) -> list[str]:
    """특정 사용자가 등록한 관심랏 목록을 반환합니다 (대문자 정규화)."""
    user_key = _normalize_user(username)
    if not user_key:
        return []
    data = _load_data()
    raw_lots = (data.get("users") or {}).get(user_key) or []
    out = []
    seen = set()
    for item in raw_lots:
        lot = _normalize_lot(item)
        if lot and lot not in seen:
            seen.add(lot)
            out.append(lot)
    return out


def is_lot_watched(username: str, lot_id: str) -> bool:
    """특정 사용자가 해당 랏을 관심 등록했는지 확인합니다."""
    lot = _normalize_lot(lot_id)
    if not lot:
        return False
    lots = get_user_watchlist(username)
    return lot in lots


def toggle_watchlist_lot(username: str, lot_id: str, watched: bool | None = None) -> bool:
    """관심랏 추가/제거를 토글하거나 지정된 상태로 설정합니다.

    Args:
        username: 사용자 아이디
        lot_id: 랏 번호
        watched: True(추가), False(제거), None(현재 상태 반전 토글)

    Returns:
        최종 관심 등록 여부 (bool)
    """
    user_key = _normalize_user(username)
    clean_lot = _normalize_lot(lot_id)
    if not user_key or not clean_lot:
        return False

    with _LOCK:
        data = _load_data()
        users = data.setdefault("users", {})
        current_lots = [_normalize_lot(x) for x in (users.get(user_key) or []) if _normalize_lot(x)]
        currently_watched = clean_lot in current_lots

        if watched is None:
            target_state = not currently_watched
        else:
            target_state = bool(watched)

        if target_state:
            if not currently_watched:
                current_lots.append(clean_lot)
        else:
            if currently_watched:
                current_lots = [x for x in current_lots if x != clean_lot]

        users[user_key] = current_lots
        _save_data(data)
        return target_state


def get_users_watching_lot(lot_id: str) -> list[str]:
    """해당 랏을 관심 등록한 모든 사용자 아이디 목록을 반환합니다."""
    clean_lot = _normalize_lot(lot_id)
    if not clean_lot:
        return []

    data = _load_data()
    users = data.get("users") or {}
    watchers: list[str] = []
    for user_key, lots in users.items():
        if isinstance(lots, list):
            norm_lots = {_normalize_lot(x) for x in lots}
            if clean_lot in norm_lots:
                watchers.append(user_key)
    return watchers
