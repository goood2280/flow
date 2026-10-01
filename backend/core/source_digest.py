"""원천 파일 지문 — 캐시를 '원천이 바뀌었을 때만' 다시 만들기 위한 공용 도구.

캐시 스케줄러(제품 순환, WIP latest-lot, FAB 매칭 캐시)는 예전에 나이(30분)로 신선도를
판단해서, 원천이 그대로여도 주기마다 FAB 전체를 다시 훑었다. 이 모듈은 원천 파일 목록의
(상대경로, mtime_ns, 크기) 를 짧은 해시 하나로 접어 "바뀐 게 있나" 만 싸게 답한다.

Windows 에서 ``os.scandir`` 의 ``DirEntry.stat()`` 은 디렉터리 목록 조회 때 이미 받은
값이라 파일마다 stat 시스템 호출을 하지 않는다. FAB 파일이 수만 개여도 비용은 목록 조회
정도다(Path.rglob + 파일별 stat 의 수 분의 1).
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from pathlib import Path
from typing import Callable, Iterable

DATA_SUFFIXES = (".parquet", ".csv")


def _enc(text: str) -> bytes:
    return text.encode("utf-8", "surrogatepass")


def tree_digest(paths: Iterable, suffixes: tuple[str, ...] = DATA_SUFFIXES) -> dict:
    """paths(폴더 또는 파일) 아래 데이터 파일 전체의 지문.

    반환: ``{"digest": hex, "files": n}``. 없는 경로·읽을 수 없는 폴더도 지문에 남겨서,
    폴더가 사라지거나 다시 생기는 것도 '변경'으로 잡힌다.
    """
    h = hashlib.blake2b(digest_size=16)
    files = 0
    lowered = tuple(s.lower() for s in suffixes)
    for raw in paths:
        if raw is None or str(raw) == "":
            continue
        base = str(raw)
        h.update(b"\x00root\x00" + _enc(base.casefold()))
        try:
            st = os.stat(base)
        except OSError:
            h.update(b"|missing")
            continue
        if not os.path.isdir(base):
            h.update(_enc(f"|{st.st_mtime_ns}|{st.st_size}"))
            files += 1
            continue
        stack: list[tuple[str, str]] = [(base, "")]
        while stack:
            path, rel = stack.pop()
            try:
                with os.scandir(path) as it:
                    entries = sorted(it, key=lambda e: e.name)
            except OSError:
                h.update(b"\x00unreadable\x00" + _enc(rel))
                continue
            dirs: list[tuple[str, str]] = []
            for entry in entries:
                name = entry.name
                try:
                    if entry.is_dir():
                        dirs.append((entry.path, f"{rel}/{name}"))
                        continue
                    if not name.lower().endswith(lowered):
                        continue
                    est = entry.stat()
                except OSError:
                    continue
                h.update(_enc(f"\x00{rel}/{name}|{est.st_mtime_ns}|{est.st_size}"))
                files += 1
            stack.extend(reversed(dirs))
    return {"digest": h.hexdigest(), "files": files}


def files_digest(paths: Iterable) -> str:
    """개별 파일(설정 파일 등)의 지문. 없는 파일은 'missing' 으로 들어간다."""
    h = hashlib.blake2b(digest_size=16)
    for raw in paths:
        if raw is None or str(raw) == "":
            continue
        path = str(raw)
        h.update(b"\x00" + _enc(path.casefold()))
        try:
            st = os.stat(path)
            h.update(_enc(f"|{st.st_mtime_ns}|{st.st_size}"))
        except OSError:
            h.update(b"|missing")
    return h.hexdigest()


def combine(parts: dict) -> str:
    """이름 붙은 지문 조각들을 하나로 접는다(키 순서 무관)."""
    h = hashlib.blake2b(digest_size=16)
    for key in sorted(parts):
        h.update(_enc(f"\x00{key}={parts[key]}"))
    return h.hexdigest()


_MEMO_LOCK = threading.Lock()
_MEMO: dict = {}
_MEMO_MAX = 64


def memo(key, ttl_sec: float, compute: Callable[[], object]):
    """같은 원천 지문을 짧은 시간(ttl_sec) 안에 여러 제품이 물으면 한 번만 계산한다."""
    now = time.monotonic()
    with _MEMO_LOCK:
        hit = _MEMO.get(key)
        if hit is not None and now - hit[0] <= ttl_sec:
            return hit[1]
    value = compute()
    with _MEMO_LOCK:
        if len(_MEMO) >= _MEMO_MAX:
            oldest = min(_MEMO, key=lambda k: _MEMO[k][0])
            _MEMO.pop(oldest, None)
        _MEMO[key] = (time.monotonic(), value)
    return value


def clear_memo() -> None:
    with _MEMO_LOCK:
        _MEMO.clear()


def env_enabled(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def change_driven_enabled() -> bool:
    """원천 지문 기반 갱신(P2)은 명시적으로 켠다. 0/미설정이면 기존 나이 기준."""
    return env_enabled("FLOW_CACHE_CHANGE_DRIVEN", False)


def full_recheck_sec() -> float:
    """지문이 같아도 이 시간이 지나면 한 번은 끝까지 다시 확인한다(지문이 못 보는 변경 대비)."""
    try:
        hours = float(os.environ.get("FLOW_CACHE_FULL_RECHECK_HOURS", "") or 6.0)
    except Exception:
        hours = 6.0
    return max(0.25, min(168.0, hours)) * 3600.0


def app_version_path() -> Path | None:
    try:
        from core.paths import PATHS
        root = Path(getattr(PATHS, "app_root"))
    except Exception:
        root = Path(__file__).resolve().parents[2]
    for name in ("VERSION.json", "version.json"):
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None
