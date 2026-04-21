"""core/backup.py v8.8.3 — 데이터 자동 백업 (사용자 기록 보호).

범위 (v8.7.4 재정의):
  - 가벼운 *사용자 기록* 만 백업. 대용량 DB parquet 는 **제외**.
  - 포함: data_root (holweb-data) 전체 + Base 루트(base CSV 등).
  - 제외: `*.parquet`, `*.pyc`, `__pycache__`, `_backups`, `cache`, `tmp`, `node_modules`.
  - logs/uploads 는 포함 (운영 기록 + 인폼 이미지 보존 필요).
  - 백업 경로: admin_settings.json `backup.path` (없으면 {data_root}/_backups).
  - 보관 정책: 최신 N 개 유지 (기본 5, 상한 5 — v8.8.3 부터 축소).
  - 주기: 서버 기동 시 1회 + 스케줄 스레드 (기본 24h). admin_settings.json
    `backup.interval_hours` 로 런타임 조절.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import threading
import time
import zipfile
from pathlib import Path
from typing import Optional, List, Tuple

from core.paths import PATHS

logger = logging.getLogger("fabcanvas.backup")

# 제외 규칙 — 큰 바이너리/휘발성/tmp/파이썬 캐시.  logs/uploads 는 **포함**.
_EXCLUDE_DIR_NAMES = {"_backups", "cache", "tmp", "__pycache__", "node_modules"}
_EXCLUDE_GLOBS = ("*.pyc", "*.parquet")

# v8.8.3: 기본 보관 개수 14 → 5, 상한도 5 로 축소 (디스크 보호).
_DEFAULT_KEEP = 5
_MAX_KEEP = 5
_DEFAULT_INTERVAL_HOURS = 24
_MIN_INTERVAL_HOURS = 1
_MAX_INTERVAL_HOURS = 24 * 7

_state_lock = threading.Lock()
_last_backup: dict = {"ok": False, "path": "", "bytes": 0, "at": "", "error": ""}


def _admin_settings_path() -> Path:
    """core/roots.py 와 같은 위치를 바라본다 (HOL_DATA_ROOT 호환)."""
    return PATHS.data_root / "admin_settings.json"


def _read_cfg() -> dict:
    p = _admin_settings_path()
    try:
        if p.is_file():
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f) or {}
    except Exception:
        return {}
    return {}


def _write_cfg(cfg: dict) -> None:
    p = _admin_settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def get_settings() -> dict:
    cfg = _read_cfg()
    bk = cfg.get("backup") or {}
    raw_keep = int(bk.get("keep") or _DEFAULT_KEEP)
    keep = max(1, min(_MAX_KEEP, raw_keep))  # v8.8.3: 상한 5 로 클램프
    return {
        "path": (bk.get("path") or "").strip(),
        "interval_hours": int(bk.get("interval_hours") or _DEFAULT_INTERVAL_HOURS),
        "keep": keep,
        "enabled": bool(bk.get("enabled", True)),
        # v8.8.14: 예약된 one-off 백업 시각 (ISO) + reason. 없으면 빈 문자열.
        "scheduled_at": (bk.get("scheduled_at") or "").strip(),
        "scheduled_reason": (bk.get("scheduled_reason") or "").strip(),
        "last": _last_backup,
    }


def set_settings(path: Optional[str] = None, interval_hours: Optional[int] = None,
                 keep: Optional[int] = None, enabled: Optional[bool] = None) -> dict:
    cfg = _read_cfg()
    bk = dict(cfg.get("backup") or {})
    if path is not None:
        bk["path"] = (path or "").strip()
    if interval_hours is not None:
        bk["interval_hours"] = max(_MIN_INTERVAL_HOURS, min(_MAX_INTERVAL_HOURS, int(interval_hours)))
    if keep is not None:
        # v8.8.3: 상한 5 강제.
        bk["keep"] = max(1, min(_MAX_KEEP, int(keep)))
    if enabled is not None:
        bk["enabled"] = bool(enabled)
    cfg["backup"] = bk
    _write_cfg(cfg)
    return get_settings()


def _resolve_backup_root() -> Path:
    cfg = get_settings()
    override = cfg["path"]
    if override:
        return Path(override)
    return PATHS.data_root / "_backups"


def _iter_files(src: Path):
    """src 이하 모든 파일 yield. 제외 규칙 적용."""
    for root, dirs, files in os.walk(src):
        # 제외 디렉토리 prune
        dirs[:] = [d for d in dirs if d not in _EXCLUDE_DIR_NAMES]
        rp = Path(root)
        # _backups 자기 자신은 항상 제외 (설사 src 내부여도)
        try:
            if "_backups" in rp.relative_to(src).parts:
                continue
        except ValueError:
            pass
        for name in files:
            if any(Path(name).match(g) for g in _EXCLUDE_GLOBS):
                continue
            yield rp / name


def _collect_sources() -> List[Tuple[Path, str]]:
    """백업할 (src, arc_prefix) 목록 반환. 중복/상/하위관계 제거."""
    srcs: List[Tuple[Path, str]] = []

    def _add(p: Path, prefix: str):
        try:
            rp = p.resolve()
        except Exception:
            return
        if not rp.is_dir():
            return
        for (existing, _) in srcs:
            try:
                ex = existing.resolve()
            except Exception:
                continue
            # 이미 포함된 경로의 하위이거나 동일하면 스킵
            if rp == ex:
                return
            try:
                rp.relative_to(ex)
                return  # rp 는 ex 의 하위 → 이미 포함됨
            except ValueError:
                pass
        srcs.append((rp, prefix))

    # 1) data_root (holweb-data) — 사용자 기록 메인
    _add(PATHS.data_root, "")

    # 2) Base 루트 — admin 이 편집하는 CSV 들. parquet 는 _EXCLUDE_GLOBS 로 자동 제외.
    #    resolve 는 core.roots 우선, 실패 시 env / PATHS default.
    base_path: Optional[Path] = None
    try:
        from core.roots import base_root as _br  # type: ignore
        p = Path(_br())
        if p.is_dir():
            base_path = p
    except Exception:
        pass
    if base_path is None:
        env_b = os.environ.get("FABCANVAS_BASE_ROOT") or os.environ.get("HOL_BASE_ROOT")
        if env_b and Path(env_b).is_dir():
            base_path = Path(env_b)
        else:
            cand = PATHS.app_root / "data" / "Base"
            if cand.is_dir():
                base_path = cand
    if base_path is not None:
        _add(base_path, "Base/")

    return srcs


def _cleanup_backups(dest_root: Path, keep: int) -> int:
    """v8.8.3: 백업 보관 정리 — 최신 keep 개만 남기고 나머지 삭제.
    run_backup / 스케줄러 / 수동 호출 어디서나 동일 로직 사용."""
    try:
        keep = max(1, min(_MAX_KEEP, int(keep or _DEFAULT_KEEP)))
    except Exception:
        keep = _DEFAULT_KEEP
    if not dest_root.is_dir():
        return 0
    files = sorted(dest_root.glob("flow_data_*.zip"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    removed = 0
    for old in files[keep:]:
        try:
            old.unlink()
            removed += 1
        except Exception as e:
            logger.warning(f"backup cleanup skip {old}: {e}")
    if removed:
        logger.info(f"backup cleanup: removed {removed} old zip(s), keeping {keep}")
    return removed


def run_backup(reason: str = "manual") -> dict:
    """설정된 소스들을 zip 으로 백업. 성공/실패 state 를 _last_backup 에 기록."""
    with _state_lock:
        try:
            sources = _collect_sources()
            if not sources:
                raise RuntimeError("no backup sources resolved")
            dest_root = _resolve_backup_root()
            dest_root.mkdir(parents=True, exist_ok=True)
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            zname = f"flow_data_{stamp}_{reason}.zip"
            zpath = dest_root / zname

            total = 0
            with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                for (src, prefix) in sources:
                    for fp in _iter_files(src):
                        try:
                            rel = fp.relative_to(src).as_posix()
                        except ValueError:
                            continue
                        arc = (prefix + rel) if prefix else rel
                        try:
                            zf.write(fp, arc)
                            total += fp.stat().st_size
                        except Exception as e:
                            logger.warning(f"backup skip {fp}: {e}")

            # v8.8.3: 보관 정리 — 공용 _cleanup_backups 훅 사용 (상한 5).
            keep = get_settings()["keep"]
            _cleanup_backups(dest_root, keep)

            info = {
                "ok": True,
                "path": str(zpath),
                "bytes": zpath.stat().st_size,
                "source_bytes": total,
                "at": datetime.datetime.now().isoformat(timespec="seconds"),
                "reason": reason,
                "error": "",
            }
            _last_backup.clear(); _last_backup.update(info)
            logger.info(f"backup ok: {zpath} ({zpath.stat().st_size:,} bytes)")
            return info
        except Exception as e:
            info = {
                "ok": False, "path": "", "bytes": 0, "source_bytes": 0,
                "at": datetime.datetime.now().isoformat(timespec="seconds"),
                "reason": reason, "error": str(e),
            }
            _last_backup.clear(); _last_backup.update(info)
            logger.warning(f"backup failed: {e}")
            return info


def list_backups() -> list:
    root = _resolve_backup_root()
    if not root.is_dir():
        return []
    # v8.8.3: 리스트 조회 시에도 기회적으로 cleanup (파일 개수가 초과하면 정리).
    try:
        _cleanup_backups(root, get_settings()["keep"])
    except Exception:
        pass
    out = []
    for p in sorted(root.glob("flow_data_*.zip"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            st = p.stat()
            out.append({
                "filename": p.name,
                "path": str(p),
                "size": st.st_size,
                "modified": datetime.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
            })
        except Exception:
            continue
    return out


_scheduler_started = False
_scheduler_stop = threading.Event()


def _check_and_run_one_off() -> bool:
    """v8.8.14: admin_settings.backup.scheduled_at 이 현재 시각 이전이면 1회 백업 실행하고
    필드를 비운다. 실행했으면 True.

    서버 점검 예정 시각 직전에 예약 백업을 돌릴 수 있게 해줌. 스케줄러 루프가 60초마다 폴링.
    """
    try:
        cfg = _read_cfg()
        bk = dict(cfg.get("backup") or {})
        at_s = (bk.get("scheduled_at") or "").strip()
        if not at_s:
            return False
        try:
            at_dt = datetime.datetime.fromisoformat(at_s.replace("Z", "+00:00"))
            if at_dt.tzinfo is not None:
                at_dt = at_dt.replace(tzinfo=None)
        except Exception:
            logger.warning(f"backup scheduler: invalid scheduled_at={at_s!r} — clearing")
            bk.pop("scheduled_at", None); bk.pop("scheduled_reason", None)
            cfg["backup"] = bk; _write_cfg(cfg)
            return False
        if at_dt > datetime.datetime.now():
            return False
        reason = (bk.get("scheduled_reason") or "pre-maintenance").strip() or "pre-maintenance"
        # 사용 즉시 필드를 비워 중복 실행 방지.
        bk.pop("scheduled_at", None); bk.pop("scheduled_reason", None)
        cfg["backup"] = bk; _write_cfg(cfg)
        run_backup(reason=reason)
        return True
    except Exception as e:
        logger.warning(f"backup scheduler: one-off check failed: {e}")
        return False


def _scheduler_loop():
    # 서버 기동 직후 짧은 지연 후 최초 1회.
    time.sleep(30)
    first = True
    while not _scheduler_stop.is_set():
        try:
            cfg = get_settings()
            if not cfg["enabled"]:
                # enabled=False 여도 one-off 예약은 존중.
                _check_and_run_one_off()
                time.sleep(600)
                continue
            if first:
                run_backup(reason="startup")
                first = False
            # 주기 대기 (60초 단위 폴링 — 설정 변경 + scheduled_at one-off 신속 반영)
            remain = cfg["interval_hours"] * 3600
            while remain > 0 and not _scheduler_stop.is_set():
                time.sleep(min(60, remain))
                remain -= 60
                # v8.8.14: 폴링 시마다 one-off 예약 체크.
                if _check_and_run_one_off():
                    # one-off 이 돌았으면 이번 주기는 생략 (이중 백업 방지) — remain 만료로 재진입.
                    remain = 0
                    break
                # 주기가 줄어들었으면 루프 재시작
                new_iv = get_settings()["interval_hours"] * 3600
                if new_iv < remain:
                    remain = new_iv
            if not _scheduler_stop.is_set() and get_settings()["enabled"]:
                run_backup(reason="scheduled")
        except Exception as e:
            logger.warning(f"backup scheduler loop error: {e}")
            time.sleep(120)


def start_scheduler() -> bool:
    """앱 기동 시 한 번 호출. 중복 호출 안전."""
    global _scheduler_started
    if _scheduler_started:
        return False
    if os.environ.get("FABCANVAS_DISABLE_BACKUP") == "1":
        logger.info("backup scheduler disabled via FABCANVAS_DISABLE_BACKUP=1")
        return False
    # v8.8.3: 기동 시 즉시 한 번 cleanup — 이전 설치에서 쌓인 >5개 파일 정리.
    try:
        root = _resolve_backup_root()
        _cleanup_backups(root, get_settings()["keep"])
    except Exception as e:
        logger.warning(f"initial cleanup skipped: {e}")
    t = threading.Thread(target=_scheduler_loop, name="backup-scheduler", daemon=True)
    t.start()
    _scheduler_started = True
    logger.info("backup scheduler started")
    return True
