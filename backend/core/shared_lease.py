"""core/shared_lease.py — 공유 data_root 기반 서버간 lease (v9.1.x).

개발/운영 2개 서버가 같은 flow-data 를 공유할 때 백그라운드 작업(스플릿테이블
pivot 캐시 재빌드, S3 주기 스케줄)이 이중 실행되지 않도록 하는 파일 lease.

- 락 파일: {data_root}/locks/<name>.lock.json
- 내용: {"owner": "<hostname>:<pid>", "acquired_at": ts, "ttl_sec": n}
- 만료(stale): acquired_at + ttl_sec 초과 시 다른 서버가 탈취(takeover).
- release 는 삭제 대신 owner 를 비워 기록 — 삭제 권한이 제한된 mount 에서도 동작.
- 획득은 tmp 파일 + os.replace(원자적 rename) 후 재확인으로 경쟁을 좁힌다.
  (최악의 경쟁 케이스는 중복 실행 1회 — 기존 무락 동작과 동일한 안전도)
"""
from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path

from core.paths import PATHS


def _locks_dir() -> Path:
    d = PATHS.data_root / "locks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _owner_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _lock_path(name: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in str(name or "lease"))
    return _locks_dir() / f"{safe}.lock.json"


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text("utf-8")) or {}
    except Exception:
        return {}


def _write_atomic(path: Path, payload: dict) -> bool:
    tmp = path.with_suffix(f".tmp.{os.getpid()}")
    try:
        tmp.write_text(json.dumps(payload), "utf-8")
        os.replace(tmp, path)
        return True
    except Exception:
        try:
            tmp.unlink()
        except Exception:
            pass
        return False


def try_acquire(name: str, ttl_sec: float) -> bool:
    """lease 획득 시 True. 다른 서버가 유효하게 보유 중이면 False.

    자기 소유 재획득은 renew 로 처리된다."""
    path = _lock_path(name)
    now = time.time()
    meta = _read(path)
    owner = str(meta.get("owner") or "")
    acquired = float(meta.get("acquired_at") or 0)
    old_ttl = float(meta.get("ttl_sec") or 0)
    if owner and owner != _owner_id() and acquired and old_ttl and (now - acquired) < old_ttl:
        return False
    payload = {"owner": _owner_id(), "acquired_at": now, "ttl_sec": float(ttl_sec)}
    if not _write_atomic(path, payload):
        return False
    # 동시 탈취 경쟁 확인 — 마지막 writer 가 이긴다.
    time.sleep(0.05)
    return str(_read(path).get("owner") or "") == _owner_id()


def renew(name: str, ttl_sec: float) -> bool:
    path = _lock_path(name)
    if str(_read(path).get("owner") or "") != _owner_id():
        return False
    return _write_atomic(path, {"owner": _owner_id(), "acquired_at": time.time(), "ttl_sec": float(ttl_sec)})


def release(name: str) -> None:
    path = _lock_path(name)
    if str(_read(path).get("owner") or "") != _owner_id():
        return
    _write_atomic(path, {"owner": "", "acquired_at": 0, "ttl_sec": 0})


def holder(name: str) -> str:
    """현재 유효한 lease 소유자. 없거나 만료면 ""."""
    meta = _read(_lock_path(name))
    acquired = float(meta.get("acquired_at") or 0)
    ttl = float(meta.get("ttl_sec") or 0)
    if not acquired or not ttl or (time.time() - acquired) >= ttl:
        return ""
    return str(meta.get("owner") or "")
