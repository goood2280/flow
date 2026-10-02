"""Boot-time root profile for flow.

This module is intentionally independent from core.paths. It lets the Admin UI
persist local/shared/custom root preferences in a project-local file that is
read before PATHS is built.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PROFILE_FILE = PROJECT_ROOT / "data" / "runtime_roots.json"
PROD_SHARED = Path("/config/work/sharedworkspace")
DEFAULT_PROD_APP_CANDIDATES = [
    Path("/config/work/flow-fast-api"),
]
VALID_MODES = {"auto", "local", "shared", "custom"}
# Windows 전용 서버의 기본 저장소 — 이 드라이브 아래 DB/ 와 flow-data/ 를 쓴다
# (Linux 운영의 /config/work/sharedworkspace/{DB,flow-data} 와 같은 구조).
WINDOWS_STORAGE_DEFAULT = "D:\\"


def windows_storage_root() -> Path | None:
    """Windows 기본 저장소 루트(FLOW_STORAGE_ROOT, 기본 D: 드라이브). Windows 가 아니면 None."""
    if not sys.platform.startswith("win"):
        return None
    raw = str(os.environ.get("FLOW_STORAGE_ROOT", "") or "").strip() or WINDOWS_STORAGE_DEFAULT
    return Path(raw)


def is_source_checkout() -> bool:
    """개발용 git 체크아웃인가. setup.py 로 푼 설치본에는 .git 이 없다."""
    return (PROJECT_ROOT / ".git").exists()


def windows_storage_default_active() -> bool:
    """Windows 에서 D:\\DB·D:\\flow-data 를 기본 저장소로 쓸지.

    - 설치본(바탕화면 flow 폴더 등에 setup.py 로 푼 폴더): 폴더가 아직 없어도 D: 가 기본.
      첫 기동에 flow-data 는 앱이 만들고, DB 는 운영자가 채운다(비어 있으면 화면이 비어 보여
      설정 누락을 바로 알 수 있다 — 프로젝트 안 샘플 DB 로 조용히 떨어지지 않는다).
    - 개발 체크아웃/VM에서 git init 한 설치본도 같은 D: 기본값을 쓴다.
    - FLOW_STORAGE_DEFAULT=0 이면 끈다(프로젝트 안 data/ 사용).
    """
    if windows_storage_root() is None:
        return False
    flag = str(os.environ.get("FLOW_STORAGE_DEFAULT", "") or "").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    return True


def _same_path(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return a.absolute() == b.absolute()


def _clean_mode(value: Any) -> str:
    mode = str(value or "auto").strip().lower()
    return mode if mode in VALID_MODES else "auto"


def read_profile() -> dict:
    try:
        if not PROFILE_FILE.is_file():
            return {"mode": "auto"}
        with open(PROFILE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"mode": "auto"}
    except Exception:
        return {"mode": "auto"}
    data["mode"] = _clean_mode(data.get("mode"))
    roots = []
    for raw in data.get("prod_app_roots") or []:
        s = str(raw or "").strip()
        if s:
            roots.append(s)
    data["prod_app_roots"] = roots
    return data


def write_profile(update: dict) -> dict:
    current = read_profile()
    next_profile = dict(current)
    for key in ("data_root", "db_root"):
        if key in update:
            val = update.get(key)
            if isinstance(val, str) and val.strip():
                next_profile[key] = val.strip()
            else:
                next_profile.pop(key, None)
    if "mode" in update:
        next_profile["mode"] = _clean_mode(update.get("mode"))
    if "prod_app_roots" in update:
        roots = []
        for raw in update.get("prod_app_roots") or []:
            s = str(raw or "").strip()
            if s and s not in roots:
                roots.append(s)
        if roots:
            next_profile["prod_app_roots"] = roots
        else:
            next_profile.pop("prod_app_roots", None)
    next_profile["updated_at"] = datetime.now(timezone.utc).isoformat()
    PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROFILE_FILE.with_suffix(PROFILE_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(next_profile, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PROFILE_FILE)
    return next_profile


def prod_app_candidates(profile: dict | None = None) -> list[Path]:
    profile = profile or read_profile()
    # Windows 에서 /config/... 는 현재 드라이브 기준으로 풀린다 — Linux 운영 경로는 쓰지 않는다.
    out = [] if sys.platform.startswith("win") else list(DEFAULT_PROD_APP_CANDIDATES)
    for raw in profile.get("prod_app_roots") or []:
        p = Path(str(raw)).expanduser()
        if p not in out:
            out.append(p)
    return out


def project_is_prod_app(profile: dict | None = None) -> bool:
    profile = profile or read_profile()
    return any(_same_path(PROJECT_ROOT, p) for p in prod_app_candidates(profile))


def _is_linux_host() -> bool:
    return sys.platform.startswith("linux")


def prod_shared_available() -> bool:
    """Linux 운영의 /config/work/sharedworkspace 를 쓸 수 있는가.

    Windows 에서 "/config/..." 는 현재 드라이브 기준 경로(D:/config/work/...)로 풀려,
    예전에는 그런 폴더가 우연히 있으면 DB·백업이 그쪽으로 샜다. Windows 는 D: 저장소만 쓴다.
    """
    if sys.platform.startswith("win"):
        return False
    return PROD_SHARED.exists()


def _shared_default_root(profile: dict | None, child: str) -> Path | None:
    profile = profile or read_profile()
    mode = _clean_mode(profile.get("mode"))
    if mode in {"local", "custom"}:
        return None
    win = windows_storage_root()
    if win is not None:
        # Windows 서버: D:\DB, D:\flow-data (드라이브는 FLOW_STORAGE_ROOT). 두 폴더는 한 쌍으로
        # 움직인다 — 한쪽만 D: 로 가고 다른 쪽이 프로젝트 안 data/ 로 남는 조합을 만들지 않는다.
        if mode == "shared" or windows_storage_default_active():
            return win / child
        return None
    if mode == "shared":
        return PROD_SHARED / child if prod_shared_available() else None
    if not prod_shared_available():
        return None
    shared_child = PROD_SHARED / child
    if project_is_prod_app(profile) or os.environ.get("FLOW_PROD") == "1":
        return shared_child
    if _is_linux_host():
        # Generic Linux/dev hosts can have /config/work/sharedworkspace present
        # without the Flow DB mounted. Only adopt the shared default implicitly
        # when that child exists; prod app root/FLOW_PROD above still keeps a
        # stable operator-owned path even if the mount arrives later.
        if shared_child.exists():
            return shared_child
    return None


def use_shared_defaults(profile: dict | None = None) -> bool:
    profile = profile or read_profile()
    return (
        _shared_default_root(profile, "DB") is not None
        or _shared_default_root(profile, "flow-data") is not None
    )


def local_data_root() -> Path:
    for cand in (PROJECT_ROOT / "flow-data", PROJECT_ROOT / "data" / "flow-data"):
        if cand.exists():
            return cand
    return PROJECT_ROOT / "data" / "flow-data"


def default_data_root(profile: dict | None = None) -> Path:
    profile = profile or read_profile()
    custom = str(profile.get("data_root") or "").strip()
    if custom:
        p = Path(custom).expanduser()
        if p.exists():
            return p
    shared = _shared_default_root(profile, "flow-data")
    if shared is not None:
        return shared
    return local_data_root()


def default_db_root(profile: dict | None = None) -> Path:
    profile = profile or read_profile()
    custom = str(profile.get("db_root") or "").strip()
    if custom:
        p = Path(custom).expanduser()
        if p.exists():
            return p
    shared = _shared_default_root(profile, "DB")
    if shared is not None:
        return shared
    for cand in (PROJECT_ROOT / "data" / "Fab", PROJECT_ROOT / "Fab", PROJECT_ROOT / "data" / "DB", PROJECT_ROOT / "DB"):
        if cand.exists():
            return cand
    return PROJECT_ROOT / "data" / "Fab"


def snapshot() -> dict:
    profile = read_profile()
    return {
        "file": str(PROFILE_FILE),
        "mode": _clean_mode(profile.get("mode")),
        "data_root": str(profile.get("data_root") or ""),
        "db_root": str(profile.get("db_root") or ""),
        "prod_app_roots": list(profile.get("prod_app_roots") or []),
        "shared_exists": prod_shared_available(),
        "windows_storage_root": str(windows_storage_root() or ""),
        "windows_storage_default": windows_storage_default_active(),
        "project_is_prod_app": project_is_prod_app(profile),
        "shared_defaults": use_shared_defaults(profile),
    }
