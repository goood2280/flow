"""core/session.py - 유저별 세션 저장/복원"""
import json
from pathlib import Path
from core.paths import PATHS

SESSION_DIR = PATHS.data_root / "sessions"
SESSION_DIR.mkdir(parents=True, exist_ok=True)


def _session_file(username: str) -> Path:
    from core.auth import validate_username
    safe = validate_username(username)
    if not safe:
        raise ValueError("Username required")
    return SESSION_DIR / f"{safe}.json"

def save_session(username: str, data: dict):
    fp = _session_file(username)
    existing = load_session(username)
    existing.update(data)
    fp.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")

def load_session(username: str) -> dict:
    fp = _session_file(username)
    if fp.exists():
        try: return json.loads(fp.read_text(encoding="utf-8"))
        except: pass
    return {}
