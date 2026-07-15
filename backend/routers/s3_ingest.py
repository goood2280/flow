"""routers/s3_ingest.py v8.1.0 — S3 → local DB ingestion
Admin-configurable 'aws s3 cp|sync' commands per DB / root_parquet.
Scheduled (interval_min) + manual refresh. Whitelist-based arg validation.

v8.1.0 adds:
  - endpoint_url as a dedicated per-item field (auto-prepended as --endpoint-url)
  - GET/POST /aws-config to manage AWS credentials/config under flow-data (admin only)

Storage:
  data_root/s3_ingest/config.json       — item configs
  data_root/s3_ingest/status.json       — per-item last run status
  data_root/s3_ingest/history.jsonl     — recent run history (trimmed 500)
"""
from __future__ import annotations
import json, os, re, time, uuid, shlex, shutil, datetime, threading, subprocess, configparser
from pathlib import Path
from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from core.paths import PATHS
from core import llm_adapter
from core import aws_credentials as _aws_credentials
from core.utils import load_json, save_json, jsonl_append, jsonl_read, jsonl_trim
from core.auth import require_admin, require_page_manager

router = APIRouter(prefix="/api/s3ingest", tags=["s3ingest"])

# ── Paths ──
S3_DIR = PATHS.data_root / "s3_ingest"
S3_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = S3_DIR / "config.json"
STATUS_FILE = S3_DIR / "status.json"
HISTORY_FILE = S3_DIR / "history.jsonl"


def _db_root() -> Path:
    return PATHS.db_root

# ── AWS config paths (runtime data, not the backend user's home directory) ──
AWS_HOME = _aws_credentials.aws_home(PATHS.data_root)
AWS_CREDENTIALS = AWS_HOME / "credentials"
AWS_CONFIG = AWS_HOME / "config"

# ── Whitelists (security-critical) ──
ALLOWED_COMMANDS = {"sync", "cp"}
ALLOWED_FLAGS = {
    "--delete", "--exact-timestamps", "--dryrun", "--size-only",
    "--quiet", "--no-progress", "--recursive", "--no-follow-symlinks",
    "--only-show-errors", "--follow-symlinks", "--no-verify-ssl",
}
ALLOWED_FLAG_WITH_VALUE = {
    "--exclude", "--include", "--storage-class", "--sse",
    "--endpoint-url", "--profile", "--region", "--ca-bundle",
}
S3_URL_RE = re.compile(r"^s3://[\w\-\.]+(/[\w\-\.\/\*\?\=]*)?$")
ID_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")
# v8.7.9: allow multi-segment paths (DB/1.RAWDATA/제품명) + Korean/Unicode letter chars.
# Forbid absolute paths and traversal (leading '/', '..', backslashes).
TARGET_RE = re.compile(r"^[\w\-\.][\w\-\.\/]{0,254}$", re.UNICODE)
# Endpoint-url validation: http(s) scheme, no shell metachars
ENDPOINT_URL_RE = re.compile(r"^https?://[\w\-\.\:/]{1,256}$")
AWS_KEY_ID_RE = re.compile(r"^[A-Z0-9]{16,32}$")
AWS_PROFILE_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")
AWS_REGION_RE = re.compile(r"^[a-z0-9\-]{1,32}$")
MAX_RUNTIME_SEC = 1800  # 30 min

# ── Scheduler state ──
_RUNNING: Dict[str, Dict[str, Any]] = {}     # id -> {"thread": T, "start": ts}
_QUEUED: list[str] = []
_RUNNING_LOCK = threading.Lock()
_QUEUE_COND = threading.Condition(_RUNNING_LOCK)
_QUEUE_WORKER_STARTED = False
_SCHED_STARTED = False
_SCHED_LOCK = threading.Lock()
_LOCAL_INFO_CACHE_TTL_SEC = 300.0
_LOCAL_INFO_CACHE: Dict[tuple[str, str], Dict[str, Any]] = {}
_LOCAL_INFO_CACHE_LOCK = threading.Lock()
_LOCAL_INFO_REFRESHING: set[tuple[str, str]] = set()
_HIVE_DATE_DIR_RE = re.compile(r"date=(\d{4}-?\d{2}-?\d{2})")


# ═════════════════════ helpers ═════════════════════
def _load_cfg() -> Dict[str, Any]:
    return load_json(CONFIG_FILE, {"items": []})


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _auto_sync_settings(cfg: Dict[str, Any] | None = None) -> Dict[str, bool]:
    """Global scheduler switches. FLOW_DISABLE_S3_INGEST=1 hard-disables periodic
    runs on this server (dev vs prod); config toggles allow runtime on/off per
    direction without a restart. Manual /run and /push stay available."""
    cfg = cfg if cfg is not None else _load_cfg()
    disabled_env = _env_flag("FLOW_DISABLE_S3_INGEST")
    # v9.1.x: 관리자 전역 마스터 스위치(admin_settings.json) — 꺼지면 방향 무관 전체 중지.
    from core.s3_sync import master_enabled as _s3_master_enabled
    master = _s3_master_enabled()
    return {
        "auto_download_enabled": master and (not disabled_env) and bool(cfg.get("auto_download_enabled", True)),
        "auto_upload_enabled": master and (not disabled_env) and bool(cfg.get("auto_upload_enabled", True)),
        "disabled_by_env": disabled_env,
        "master_enabled": master,
    }


def _save_cfg(cfg):
    save_json(CONFIG_FILE, cfg, indent=2)


def _load_status() -> Dict[str, Any]:
    return load_json(STATUS_FILE, {})


def _save_status(st):
    save_json(STATUS_FILE, st, indent=2)


def _aws_cli_env() -> Dict[str, str]:
    env = os.environ.copy()
    env["AWS_SHARED_CREDENTIALS_FILE"] = str(AWS_CREDENTIALS)
    env["AWS_CONFIG_FILE"] = str(AWS_CONFIG)
    env.setdefault("AWS_EC2_METADATA_DISABLED", "true")
    return env


def _normalize_direction(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if text in {"upload", "push"}:
        return "upload"
    return "download"


def _item_direction(item: Dict[str, Any] | None = None, direction: str | None = None) -> str:
    return _normalize_direction(direction or ((item or {}).get("direction") if item else "download"))


def _status_for_item(status: Dict[str, Any], item: Dict[str, Any]) -> Dict[str, Any]:
    base = status.get(item.get("id"), {}) or {}
    if not isinstance(base, dict):
        return {}
    direction = _item_direction(item)
    directions = base.get("directions") or {}
    if isinstance(directions, dict):
        slot = directions.get(direction) or {}
        if isinstance(slot, dict) and slot:
            merged = {k: v for k, v in base.items() if k != "directions"}
            merged.update(slot)
            return merged
    return base


def _update_status(item_id: str, direction: str | None = None, **patch):
    st = _load_status()
    cur = st.get(item_id, {})
    if not isinstance(cur, dict):
        cur = {}
    if direction:
        dir_key = _normalize_direction(direction)
        directions = cur.get("directions")
        if not isinstance(directions, dict):
            directions = {}
        slot = directions.get(dir_key)
        if not isinstance(slot, dict):
            slot = {}
        slot.update(patch)
        slot["direction"] = dir_key
        directions[dir_key] = slot
        cur.update(patch)
        cur["direction"] = dir_key
        cur["directions"] = directions
    else:
        cur.update(patch)
    st[item_id] = cur
    _save_status(st)


def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


S3_ERROR_EXPLAIN_SCHEMA = {
    "keys": ["summary", "cause", "how_to_fix"],
    "required": [],
    "properties": {"summary": {}, "cause": {}, "how_to_fix": {}},
}


def _clip_s3_text(value: Any, limit: int = 1200) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines:
        text = "\n".join(lines[-10:])
    return text[-limit:] if len(text) > limit else text


def _redact_s3_text(value: Any, limit: int = 1200) -> str:
    text = _clip_s3_text(value, limit)
    text = re.sub(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;\"']+", r"\1<redacted>", text)
    text = re.sub(r"(?i)(aws_secret_access_key|aws_session_token|secret_access_key)([\"'\s:=]+)([^\"'\s,;&]+)", r"\1\2<redacted>", text)
    text = re.sub(r"(?i)(x-amz-security-token|x-amz-credential)([\"'\s:=]+)([^\"'\s,;&]+)", r"\1\2<redacted>", text)
    return text


def _s3_failure_reason(tail: str, exit_code: int | None = None, fallback: str = "") -> str:
    reason = _clip_s3_text(tail) or _clip_s3_text(fallback)
    if reason:
        return reason
    if exit_code not in (None, 0):
        return f"aws CLI exited with code {exit_code} without output"
    return ""


def _clean_s3_ai_explanation(obj: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(obj, dict):
        return None

    def line(key: str, limit: int = 260) -> str:
        text = str(obj.get(key) or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        text = " ".join(part.strip() for part in text.splitlines() if part.strip())
        return text[:limit]

    fixes = obj.get("how_to_fix")
    if isinstance(fixes, str):
        fixes = [part.strip(" -\t") for part in fixes.splitlines() if part.strip(" -\t")]
    if not isinstance(fixes, list):
        fixes = []
    clean_fixes = []
    for item in fixes:
        text = str(item or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        text = " ".join(part.strip() for part in text.splitlines() if part.strip())
        if text:
            clean_fixes.append(text[:180])
        if len(clean_fixes) >= 3:
            break
    out = {
        "summary": line("summary", 180),
        "cause": line("cause", 300),
        "how_to_fix": clean_fixes,
    }
    if not out["summary"] and not out["cause"] and not out["how_to_fix"]:
        return None
    return out


def _explain_s3_failure(*, action: str, item_id: str, target: str, kind: str,
                        direction: str, reason: str, exit_code: int | None = None) -> dict[str, Any] | None:
    clean_reason = _redact_s3_text(reason, 1600)
    if not clean_reason:
        return None
    try:
        if not llm_adapter.is_available():
            return None
        if not llm_adapter.should_attempt_llm():
            return None
        prompt = {
            "surface": "FileBrowser S3",
            "action": action,
            "item_id": item_id,
            "target": target,
            "kind": kind,
            "direction": direction,
            "exit_code": exit_code,
            "raw_error": clean_reason,
        }
        out = llm_adapter.complete_json(
            "Flow FileBrowser S3 등록/동기화 실패를 한국어로 설명해줘. "
            "summary, cause, how_to_fix JSON만 반환하고 확인되지 않은 내부 원인은 추측하지 마.\n\n"
            + json.dumps(prompt, ensure_ascii=False),
            system="You explain Flow app S3 errors for Korean end users. Return concise JSON only.",
            schema=S3_ERROR_EXPLAIN_SCHEMA,
            timeout=8,
            max_retries=1,
        )
        if not out.get("ok"):
            return None
        return _clean_s3_ai_explanation(out.get("obj") or {})
    except Exception:
        return None


def _append_s3_error_history(*, action: str, item_id: str = "", target: str = "",
                             kind: str = "", direction: str = "download",
                             reason: str = "", exit_code: int | None = None,
                             duration_sec: int | None = None, cmd: str = "",
                             output_tail: str = "", actor: str = "") -> dict[str, Any] | None:
    safe_reason = _s3_failure_reason(output_tail, exit_code, reason)
    ai_explanation = _explain_s3_failure(
        action=action,
        item_id=item_id,
        target=target,
        kind=kind,
        direction=direction,
        reason=safe_reason,
        exit_code=exit_code,
    )
    entry: Dict[str, Any] = {
        "id": item_id or target or "s3_save",
        "target": target,
        "kind": kind,
        "direction": _normalize_direction(direction),
        "action": action,
        "status": "error",
        "reason": safe_reason,
        "error": safe_reason,
    }
    if actor:
        entry["actor"] = actor
    if exit_code is not None:
        entry["exit_code"] = exit_code
    if duration_sec is not None:
        entry["duration_sec"] = duration_sec
    if cmd:
        entry["cmd"] = cmd
    if output_tail:
        entry["output_tail"] = _clip_s3_text(output_tail, 2000)
    if ai_explanation:
        entry["ai_explanation"] = ai_explanation
    try:
        jsonl_append(HISTORY_FILE, entry)
        jsonl_trim(HISTORY_FILE, 500)
    except Exception:
        pass
    return ai_explanation


def _fmt_ts(ts: float | None) -> str | None:
    if not ts:
        return None
    try:
        return datetime.datetime.fromtimestamp(ts).isoformat(timespec="seconds")
    except Exception:
        return None


def _is_admin(username: str) -> bool:
    """Compatibility hook for older tests; route permissions use FastAPI dependencies."""
    if not username:
        return False
    try:
        from routers.auth import read_users
        for user in read_users():
            if user.get("username") == username and user.get("role") == "admin":
                return True
    except Exception:
        pass
    return False


def _latest_local_item_info(target: str) -> Dict[str, Any]:
    """Return the newest local file timestamp under db_root/target.

    This is intentionally lightweight metadata for UI freshness checks.
    We scan known data-like files recursively and return the newest one.
    """
    info = {
        "latest_item_at": None,
        "latest_item_relpath": None,
        "latest_item_age_hours": None,
        "latest_item_stale_6h": False,
        "latest_item_scan_error": None,
    }
    try:
        db_base = _db_root()
        local = (db_base / target).resolve()
        base = db_base.resolve()
        if base not in local.parents and local != base:
            info["latest_item_scan_error"] = "target_outside_db_root"
            return info
        if not local.exists():
            return info

        newest_ts = 0.0
        newest_path: Path | None = None
        exts = {".parquet", ".csv", ".json", ".jsonl", ".md", ".txt", ".xlsx", ".xls"}

        if local.is_file():
            newest_ts = local.stat().st_mtime
            newest_path = local
        else:
            # hive `date=YYYYMMDD` 형제 폴더는 최신 하나만 내려간다 — 대형 DB
            # 타깃에서 수천 개 과거 파티션 stat 을 생략해 신호등 freshness 를
            # 파티션 수와 무관하게 계산한다. (과거 파티션 파일만 늦게 갱신된
            # 경우는 놓칠 수 있으나, 신선도 표시 목적에는 충분하다.)
            for dirpath, dirnames, filenames in os.walk(local):
                dated: list[tuple[str, str]] = []
                plain: list[str] = []
                for name in dirnames:
                    m = _HIVE_DATE_DIR_RE.search(name)
                    if m:
                        dated.append((m.group(1).replace("-", ""), name))
                    else:
                        plain.append(name)
                if dated:
                    latest_key = max(k for k, _n in dated)
                    dirnames[:] = plain + [n for k, n in dated if k == latest_key]
                for name in filenames:
                    if Path(name).suffix.lower() not in exts:
                        continue
                    try:
                        mt = (Path(dirpath) / name).stat().st_mtime
                    except OSError:
                        continue
                    if mt > newest_ts:
                        newest_ts = mt
                        newest_path = Path(dirpath) / name

        if newest_ts > 0 and newest_path is not None:
            age_h = max(0.0, (time.time() - newest_ts) / 3600.0)
            info["latest_item_at"] = _fmt_ts(newest_ts)
            try:
                info["latest_item_relpath"] = str(newest_path.relative_to(local))
            except Exception:
                info["latest_item_relpath"] = newest_path.name
            info["latest_item_age_hours"] = round(age_h, 2)
            info["latest_item_stale_6h"] = age_h > 6.0
        return info
    except Exception as e:
        info["latest_item_scan_error"] = str(e)[:180]
        return info


def _refresh_local_item_info(key: tuple[str, str], target: str) -> None:
    try:
        info = _latest_local_item_info(target)
        with _LOCAL_INFO_CACHE_LOCK:
            _LOCAL_INFO_CACHE[key] = {"ts": time.time(), "info": dict(info)}
    except Exception:
        pass
    finally:
        with _LOCAL_INFO_CACHE_LOCK:
            _LOCAL_INFO_REFRESHING.discard(key)


def _cached_latest_local_item_info(target: str) -> Dict[str, Any]:
    """Stale-while-revalidate freshness lookup.

    TTL 이 지나도 이전 값을 즉시 반환하고 백그라운드 스레드로 재스캔한다 —
    status-by-target?include_local=1 응답이 로컬 트리 스캔을 기다리지 않는다.
    최초 1회(캐시 없음)만 동기 스캔한다.
    """
    try:
        root_key = str(_db_root().resolve())
    except Exception:
        root_key = str(_db_root())
    key = (root_key, str(target or ""))
    now = time.time()
    with _LOCAL_INFO_CACHE_LOCK:
        entry = _LOCAL_INFO_CACHE.get(key)
        if entry is not None:
            if (
                now - float(entry.get("ts") or 0) >= _LOCAL_INFO_CACHE_TTL_SEC
                and key not in _LOCAL_INFO_REFRESHING
            ):
                _LOCAL_INFO_REFRESHING.add(key)
                threading.Thread(
                    target=_refresh_local_item_info, args=(key, target), daemon=True,
                ).start()
            return dict(entry.get("info") or {})
    info = _latest_local_item_info(target)
    with _LOCAL_INFO_CACHE_LOCK:
        _LOCAL_INFO_CACHE[key] = {"ts": now, "info": dict(info)}
    return dict(info)


def _parse_iso_ts(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "")).timestamp()
    except Exception:
        return 0.0


def _fmt_iso_epoch(value: float) -> str | None:
    if not value:
        return None
    try:
        return datetime.datetime.fromtimestamp(value).isoformat(timespec="seconds")
    except Exception:
        return None


def _item_due_state(item: Dict[str, Any], status: Dict[str, Any] | None = None,
                    now: float | None = None) -> Dict[str, Any]:
    """Return per-item scheduler due state from interval and last_start/end."""
    status = status or {}
    now = time.time() if now is None else float(now)
    try:
        interval_min = int(item.get("interval_min", 0) or 0)
    except Exception:
        interval_min = 0
    last_raw = status.get("last_end") or status.get("last_start")
    last_ts = _parse_iso_ts(last_raw)
    if interval_min <= 0:
        return {"due": False, "next_due": None, "age_seconds": int(now - last_ts) if last_ts else None}
    if not last_ts:
        return {"due": True, "next_due": _fmt_iso_epoch(now), "age_seconds": None}
    age = max(0, now - last_ts)
    next_ts = last_ts + interval_min * 60
    return {
        "due": age >= interval_min * 60,
        "next_due": _fmt_iso_epoch(next_ts),
        "age_seconds": int(age),
    }


def _aggregate_child_statuses(by_target: Dict[str, Dict[str, Any]]) -> None:
    """Create synthetic parent lights when only child DB targets are configured.

    File Browser renders both a DB root row and child product rows. Operators may
    configure S3 sync on each child product instead of the parent folder; in that
    case the parent should reflect the child set instead of showing "unconfigured".
    """
    grouped: Dict[str, list[Dict[str, Any]]] = {}
    for target, info in list(by_target.items()):
        parts = [p for p in str(target or "").split("/") if p]
        for i in range(1, len(parts)):
            parent = "/".join(parts[:i])
            if parent and parent not in by_target:
                grouped.setdefault(parent, []).append(info)
    for parent, children in grouped.items():
        if not children:
            continue
        statuses = [str(c.get("last_status") or "never") for c in children]
        if any(c.get("is_running") for c in children):
            last_status = "running"
        elif any(c.get("is_queued") for c in children):
            last_status = "queued"
        elif any(s == "error" for s in statuses):
            last_status = "error"
        elif any(s == "never" for s in statuses):
            last_status = "never"
        elif all(s == "ok" for s in statuses):
            last_status = "ok"
        else:
            last_status = statuses[0] if statuses else "never"

        last_times = [
            _parse_iso_ts(c.get("last_end") or c.get("last_start"))
            for c in children
            if c.get("last_end") or c.get("last_start")
        ]
        latest_item_times = [
            _parse_iso_ts(c.get("latest_item_at"))
            for c in children
            if c.get("latest_item_at")
        ]
        next_times = [
            _parse_iso_ts(c.get("next_due"))
            for c in children
            if c.get("next_due")
        ]
        directions = sorted({str(c.get("direction") or "download").lower() for c in children})
        intervals = []
        for child in children:
            try:
                interval = int(child.get("interval_min") or 0)
            except Exception:
                interval = 0
            if interval > 0:
                intervals.append(interval)
        child_freshness = [str(c.get("freshness_state") or "").strip() for c in children]
        child_stale = any(state == "stale_item" for state in child_freshness)
        aggregate = {
            "kind": "aggregate",
            "direction": directions[0] if len(directions) == 1 else "mixed",
            "enabled": all(bool(c.get("enabled", True)) for c in children),
            "interval_min": min(intervals) if intervals else 0,
            "last_status": "ok" if last_status == "running" else last_status,
            "last_end": _fmt_iso_epoch(max(last_times)) if last_times else None,
            "last_start": None,
            "last_exit_code": None,
            "last_duration_sec": None,
            "is_running": any(c.get("is_running") for c in children),
            "is_queued": any(c.get("is_queued") for c in children),
            "latest_item_at": _fmt_iso_epoch(max(latest_item_times)) if latest_item_times else None,
            "latest_item_relpath": f"{len(children)} child targets",
            "latest_item_age_hours": None,
            "latest_item_stale_6h": child_stale,
            "latest_item_scan_error": None,
            "next_due": _fmt_iso_epoch(min(next_times)) if next_times else None,
            "due": any(bool(c.get("due")) for c in children),
            "age_seconds": max((int(c.get("age_seconds") or 0) for c in children if c.get("age_seconds") is not None), default=None),
            "aggregate": True,
            "child_targets": len(children),
        }
        if aggregate["is_running"]:
            aggregate["freshness_state"] = "running"
        elif aggregate["is_queued"]:
            aggregate["freshness_state"] = "queued"
        elif child_stale:
            aggregate["freshness_state"] = "stale_item"
        elif aggregate["last_status"] == "ok":
            aggregate["freshness_state"] = "ok"
        elif aggregate["last_status"] == "never":
            aggregate["freshness_state"] = "never"
        else:
            aggregate["freshness_state"] = "error"
        by_target[parent] = aggregate


def _validate_s3_url(url: str):
    if not url or not S3_URL_RE.match(url):
        raise HTTPException(400, f"invalid s3 url: {url!r}")


def _validate_endpoint_url(url: str):
    """endpoint_url is optional; empty = none."""
    if not url:
        return
    if not ENDPOINT_URL_RE.match(url):
        raise HTTPException(400, f"invalid endpoint_url: {url!r}")


def _validate_target(target: str):
    """v8.7.9: target may be multi-segment (DB/1.RAWDATA/제품명). Reject traversal."""
    if not target or not TARGET_RE.match(target):
        raise HTTPException(400, f"invalid target: {target!r}")
    if "\\" in target or ".." in target.split("/"):
        raise HTTPException(400, f"target must not contain '..' or backslash: {target!r}")


def _validate_profile(name: str):
    if not name:
        return
    if not AWS_PROFILE_RE.match(name):
        raise HTTPException(400, f"invalid profile: {name!r}")


def _validate_extra_args(extra: str) -> List[str]:
    if not extra or not extra.strip():
        return []
    try:
        tokens = shlex.split(extra)
    except Exception as e:
        raise HTTPException(400, f"failed to parse extra_args: {e}")
    out, i = [], 0
    while i < len(tokens):
        t = tokens[i]
        if t in ALLOWED_FLAGS:
            out.append(t); i += 1
        elif t in ALLOWED_FLAG_WITH_VALUE:
            if i + 1 >= len(tokens):
                raise HTTPException(400, f"{t} requires a value")
            v = tokens[i+1]
            if any(c in v for c in ('`', '$', ';', '|', '&', '\n', '\r', '<', '>')):
                raise HTTPException(400, f"disallowed character in {t} value")
            out.extend([t, v]); i += 2
        else:
            raise HTTPException(400, f"disallowed flag: {t!r} — allowed: {sorted(ALLOWED_FLAGS | ALLOWED_FLAG_WITH_VALUE)}")
    return out


def _build_cmd(item: Dict[str, Any]):
    cmd_sub = item.get("command", "sync")
    if cmd_sub not in ALLOWED_COMMANDS:
        raise HTTPException(400, f"invalid command: {cmd_sub}")
    _validate_s3_url(item.get("s3_url", ""))
    target = item.get("target", "")
    _validate_target(target)

    kind = item.get("kind", "db")
    if kind not in {"db", "root_parquet"}:
        raise HTTPException(400, f"invalid kind: {kind}")

    db_base = _db_root()
    local = db_base / target
    # Guard against resolved traversal out of db_root
    try:
        _real = local.resolve()
        _base = db_base.resolve()
        if _base not in _real.parents and _real != _base:
            raise HTTPException(400, f"target resolves outside db_root: {target!r}")
    except HTTPException:
        raise
    except Exception:
        pass
    if cmd_sub == "sync":
        if kind != "db":
            raise HTTPException(400, "'sync' is only valid for kind='db' (directory)")
        local.mkdir(parents=True, exist_ok=True)
    # 'cp' is allowed for both db (to copy a single object into a dir) and root_parquet

    # endpoint_url — dedicated field, auto-prepended as --endpoint-url
    endpoint_url = (item.get("endpoint_url") or "").strip()
    # v8.1.4: fallback to runtime AWS config default profile endpoint_url when item has none
    if not endpoint_url:
        try:
            _cfg = _read_config()
            endpoint_url = (_cfg.get("default", {}).get("endpoint_url", "") or "").strip()
        except Exception:
            endpoint_url = ""
    _validate_endpoint_url(endpoint_url)
    prefix: List[str] = []
    if endpoint_url:
        prefix = ["--endpoint-url", endpoint_url]

    # v8.7.9: per-item AWS profile — routes to the correct AWS key.
    profile = (item.get("profile") or "").strip()
    _validate_profile(profile)
    if profile:
        prefix += ["--profile", profile]

    extra = _validate_extra_args(item.get("extra_args", ""))
    # v8.8.0: direction. "download" = s3 → local (기본). "upload" = local → s3.
    direction = (item.get("direction") or "download").lower()
    if direction == "upload":
        if cmd_sub == "cp" and local.is_dir():
            extra = list(extra)
            if "--recursive" not in extra:
                extra.append("--recursive")
        args = ["aws", "s3", cmd_sub, str(local), item["s3_url"]] + prefix + extra
    else:
        args = ["aws", "s3", cmd_sub, item["s3_url"], str(local)] + prefix + extra
    return args, local


def _run_item_blocking(item_id: str):
    cfg = _load_cfg()
    item = next((x for x in cfg.get("items", []) if x.get("id") == item_id), None)
    if not item:
        return
    direction = _item_direction(item)
    _update_status(item_id, direction=direction, last_start=_now_iso(), last_status="running")

    try:
        args, _local = _build_cmd(item)
    except HTTPException as e:
        reason = f"validation failed: {e.detail}"
        ai_explanation = _append_s3_error_history(
            action="run",
            item_id=item_id,
            target=item.get("target", ""),
            kind=item.get("kind", ""),
            direction=direction,
            reason=reason,
        )
        _update_status(item_id,
                       direction=direction,
                       last_status="error",
                       last_output_tail=reason,
                       last_reason=reason,
                       last_ai_explanation=ai_explanation,
                       last_end=_now_iso(),
                       last_duration_sec=0)
        with _RUNNING_LOCK:
            _RUNNING.pop(item_id, None)
        return

    t0 = time.time()
    status = "error"; exit_code = -1; tail = ""
    try:
        # v9.1.x: Popen + 핸들 보관 — /stop 이 실행 중인 전송을 종료할 수 있게 한다.
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, env=_aws_cli_env())
        with _RUNNING_LOCK:
            slot = _RUNNING.get(item_id)
            if isinstance(slot, dict):
                slot["proc"] = proc
        try:
            out, _ = proc.communicate(timeout=MAX_RUNTIME_SEC)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate()
            out = (out or "") + f"\ntimeout after {MAX_RUNTIME_SEC}s"
        tail = out[-2000:] if out else ""
        exit_code = proc.returncode
        status = "ok" if proc.returncode == 0 else "error"
    except FileNotFoundError:
        tail = "aws CLI not installed on host (apt install awscli or pip install awscli)"
    except Exception as e:
        tail = f"exception: {str(e)[:1800]}"

    with _RUNNING_LOCK:
        slot = _RUNNING.get(item_id)
        cancel_requested = bool(isinstance(slot, dict) and slot.get("cancel"))
    if cancel_requested and status != "ok":
        status = "cancelled"
        tail = tail or "사용자 요청으로 중지됨"

    dur = int(time.time() - t0)
    reason = _s3_failure_reason(tail, exit_code) if status == "error" else ""
    ai_explanation = None
    if status == "error":
        ai_explanation = _explain_s3_failure(
            action="run",
            item_id=item_id,
            target=item.get("target", ""),
            kind=item.get("kind", ""),
            direction=direction,
            reason=reason,
            exit_code=exit_code,
        )
    _update_status(item_id,
                   direction=direction,
                   last_end=_now_iso(),
                   last_status=status,
                   last_exit_code=exit_code,
                   last_output_tail=tail,
                   last_reason=reason,
                   last_ai_explanation=ai_explanation,
                   last_duration_sec=dur)
    if status == "ok" and direction == "download":
        # 새 FAB 원천이 내려왔을 수 있다 — SplitTable fab_lot_index 스윕을 즉시
        # 깨워 다음 주기(기본 60s)를 기다리지 않고 재검증한다.
        try:
            from routers import splittable as _splittable
            _splittable.notify_fab_sources_changed(reason=f"s3_ingest:{item_id}")
        except Exception:
            pass
    entry = {
        "id": item_id, "target": item.get("target"), "kind": item.get("kind"),
        "direction": (item.get("direction") or "download").lower(),
        "status": status, "exit_code": exit_code, "duration_sec": dur,
        "cmd": " ".join(args),
    }
    if tail:
        entry["output_tail"] = tail
    if reason:
        entry["reason"] = reason
        entry["error"] = reason
    if ai_explanation:
        entry["ai_explanation"] = ai_explanation
    jsonl_append(HISTORY_FILE, entry)
    jsonl_trim(HISTORY_FILE, 500)
    with _RUNNING_LOCK:
        _RUNNING.pop(item_id, None)


def _queue_worker_loop():
    from core import request_priority
    while True:
        with _QUEUE_COND:
            while not _QUEUED:
                _QUEUE_COND.wait()
            item_id = _QUEUED.pop(0)
            if item_id in _RUNNING:
                continue
            _RUNNING[item_id] = {"thread": threading.current_thread(), "start": time.time()}
        # 사용자 요청이 진행 중이면 주기 동기화 시작을 잠시 미룬다 (최대 60초).
        request_priority.yield_to_users(max_wait_sec=60.0, quiet_for_sec=3.0)
        _run_item_blocking(item_id)


def _ensure_queue_worker():
    global _QUEUE_WORKER_STARTED
    with _QUEUE_COND:
        if _QUEUE_WORKER_STARTED:
            return
        threading.Thread(target=_queue_worker_loop, daemon=True, name="s3ingest-queue").start()
        _QUEUE_WORKER_STARTED = True


def _schedule_run(item_id: str) -> bool:
    _ensure_queue_worker()
    with _QUEUE_COND:
        if item_id in _RUNNING or item_id in _QUEUED:
            return False
        _QUEUED.append(item_id)
        _QUEUE_COND.notify()
    return True


def _scheduler_loop():
    while True:
        try:
            # v9.1.x: 공유 flow-data lease — 두 서버 중 lease 보유 서버만 주기 스케줄 실행.
            from core import shared_lease as _shared_lease
            if not _shared_lease.try_acquire("s3_ingest_scheduler", ttl_sec=90.0):
                time.sleep(30)
                continue
            cfg = _load_cfg()
            auto = _auto_sync_settings(cfg)
            status = _load_status()
            now = time.time()
            for item in cfg.get("items", []):
                if not item.get("enabled", True):
                    continue
                direction = _item_direction(item)
                if direction == "upload" and not auto["auto_upload_enabled"]:
                    continue
                if direction == "download" and not auto["auto_download_enabled"]:
                    continue
                st = _status_for_item(status, item)
                due = _item_due_state(item, st, now)
                if due.get("due"):
                    _schedule_run(item["id"])
        except Exception:
            pass
        time.sleep(30)


def _start_scheduler():
    global _SCHED_STARTED
    with _SCHED_LOCK:
        if _SCHED_STARTED:
            return
        threading.Thread(target=_scheduler_loop, daemon=True,
                         name="s3ingest-sched").start()
        _SCHED_STARTED = True


_start_scheduler()


# ═════════════════════ API ═════════════════════
@router.get("/items")
def list_items(username: str = Query(""), _perm=Depends(require_page_manager("filebrowser"))):
    cfg = _load_cfg()
    status = _load_status()
    out = []
    now = time.time()
    for it in cfg.get("items", []):
        merged = dict(it)
        st = _status_for_item(status, it)
        due = _item_due_state(it, st, now)
        merged["status"] = st
        merged["is_running"] = it["id"] in _RUNNING
        merged["is_queued"] = it["id"] in _QUEUED
        merged.update(due)
        # backward compat: ensure endpoint_url always present in response
        merged.setdefault("endpoint_url", "")
        out.append(merged)
    return {
        "items": out,
        "aws_available": shutil.which("aws") is not None,
        "db_base": str(_db_root()),
        "auto_sync": _auto_sync_settings(cfg),
    }


@router.get("/available")
def list_available(username: str = Query(""), _perm=Depends(require_page_manager("filebrowser"))):
    """List DBs and root parquets that can be configured."""
    dbs, files = [], []
    db_base = _db_root()
    if db_base.exists():
        for p in sorted(db_base.iterdir()):
            name = p.name
            if name.startswith("."):
                continue
            if p.is_dir():
                dbs.append({"name": name, "kind": "db"})
            elif p.suffix.lower() in {".parquet", ".csv"}:
                try:
                    sz = p.stat().st_size
                except Exception:
                    sz = 0
                files.append({"name": name, "kind": "root_parquet", "size": sz})
    return {"dbs": dbs, "root_parquets": files}


class SaveReq(BaseModel):
    username: str
    id: str = ""
    kind: str
    target: str
    s3_url: str
    command: str = "sync"
    extra_args: str = ""
    endpoint_url: str = ""
    # v8.7.9: per-item AWS profile (credentials/key) — fed as `--profile <name>`.
    profile: str = ""
    # v8.8.0: 동기화 방향. "download" = S3 → local (기본), "upload" = local → S3.
    direction: str = "download"
    interval_min: int = 0
    enabled: bool = True


def _find_existing_item_id(items: list[dict], *, kind: str, target: str, direction: str) -> str:
    target_key = str(target or "").strip().casefold()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("kind") or "").strip() != kind:
            continue
        if str(item.get("target") or "").strip().casefold() != target_key:
            continue
        if _item_direction(item) != direction:
            continue
        item_id = str(item.get("id") or "").strip()
        if item_id:
            return item_id
    return ""


@router.post("/save")
def save_item(req: SaveReq, _perm=Depends(require_admin)):
    try:
        return _save_item_checked(req)
    except HTTPException as exc:
        actor = ""
        if isinstance(_perm, dict):
            actor = str(_perm.get("username") or "")
        _append_s3_error_history(
            action="save",
            item_id=(req.id or "").strip(),
            target=req.target or "",
            kind=req.kind or "",
            direction=req.direction or "download",
            reason=str(exc.detail or ""),
            actor=actor or (req.username or ""),
        )
        raise


def _save_item_checked(req: SaveReq):
    if req.command not in ALLOWED_COMMANDS:
        raise HTTPException(400, f"invalid command: {req.command}")
    if req.kind not in {"db", "root_parquet"}:
        raise HTTPException(400, f"invalid kind: {req.kind}")
    _validate_target(req.target or "")
    _validate_s3_url(req.s3_url)
    _validate_endpoint_url((req.endpoint_url or "").strip())
    _validate_profile((req.profile or "").strip())
    _validate_extra_args(req.extra_args)
    if req.kind == "root_parquet" and req.command == "sync":
        raise HTTPException(400, "'sync' is only valid for kind='db'. Use 'cp' for root_parquet.")

    direction = (req.direction or "download").strip().lower()
    if direction not in {"download", "upload"}:
        raise HTTPException(400, f"invalid direction: {direction!r} (must be 'download' or 'upload')")
    cfg = _load_cfg()
    items = cfg.get("items", [])
    if not isinstance(items, list):
        items = []
    requested_id = (req.id or "").strip()
    item_id = requested_id or _find_existing_item_id(items, kind=req.kind, target=req.target, direction=direction)
    if not item_id:
        item_id = f"{req.kind}_{re.sub(r'[^a-zA-Z0-9]', '_', req.target)[:30]}_{uuid.uuid4().hex[:6]}"
    if not ID_RE.match(item_id):
        raise HTTPException(400, f"invalid id: {item_id}")
    new_item = {
        "id": item_id,
        "kind": req.kind, "target": req.target,
        "s3_url": req.s3_url, "command": req.command,
        "extra_args": req.extra_args,
        "endpoint_url": (req.endpoint_url or "").strip(),
        "profile": (req.profile or "").strip(),
        "direction": direction,
        "interval_min": max(0, int(req.interval_min)),
        "enabled": bool(req.enabled),
    }

    for i, it in enumerate(items):
        if it.get("id") == item_id:
            items[i] = new_item
            break
    else:
        items.append(new_item)
    cfg["items"] = items
    _save_cfg(cfg)
    return {"ok": True, "id": item_id}


class IdReq(BaseModel):
    username: str
    id: str


@router.post("/delete")
def delete_item(req: IdReq, _perm=Depends(require_admin)):
    cfg = _load_cfg()
    before = len(cfg.get("items", []))
    cfg["items"] = [x for x in cfg.get("items", []) if x.get("id") != req.id]
    if len(cfg["items"]) == before:
        raise HTTPException(404, "item not found")
    _save_cfg(cfg)
    st = _load_status()
    st.pop(req.id, None)
    _save_status(st)
    return {"ok": True}


@router.post("/run")
def run_manual(req: IdReq, _perm=Depends(require_page_manager("filebrowser"))):
    if not _auto_sync_settings().get("master_enabled", True):
        raise HTTPException(409, "관리자 설정에서 S3 전체가 꺼져 있습니다 (S3 master switch off)")
    cfg = _load_cfg()
    if not any(x.get("id") == req.id for x in cfg.get("items", [])):
        raise HTTPException(404, "item not found")
    if req.id in _RUNNING:
        return {"ok": True, "already_running": True}
    if req.id in _QUEUED:
        return {"ok": True, "already_queued": True}
    started = _schedule_run(req.id)
    return {"ok": True, "started": started, "queued": started}


@router.post("/stop")
def stop_item(req: IdReq, _perm=Depends(require_page_manager("filebrowser"))):
    """실행 중/대기 중인 항목을 개별 중지한다. 항목 설정은 그대로 유지된다."""
    cfg = _load_cfg()
    item = next((x for x in cfg.get("items", []) if x.get("id") == req.id), None)
    if not item:
        raise HTTPException(404, "item not found")
    dequeued = False
    terminating = False
    with _QUEUE_COND:
        if req.id in _QUEUED:
            _QUEUED.remove(req.id)
            dequeued = True
        slot = _RUNNING.get(req.id)
        proc = slot.get("proc") if isinstance(slot, dict) else None
        if isinstance(slot, dict):
            slot["cancel"] = True
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                terminating = True
            except Exception:
                pass
    if dequeued:
        direction = _item_direction(item)
        _update_status(req.id, direction=direction,
                       last_status="cancelled",
                       last_end=_now_iso(),
                       last_reason="실행 전 대기열에서 중지됨")
        jsonl_append(HISTORY_FILE, {
            "id": req.id, "target": item.get("target"), "kind": item.get("kind"),
            "direction": direction, "status": "cancelled",
            "reason": "실행 전 대기열에서 중지됨",
        })
        jsonl_trim(HISTORY_FILE, 500)
    return {"ok": True, "dequeued": dequeued, "terminating": terminating}


class SetEnabledReq(BaseModel):
    username: str
    id: str
    enabled: bool


@router.post("/set-enabled")
def set_item_enabled(req: SetEnabledReq, _perm=Depends(require_admin)):
    """삭제/재등록 없이 항목 주기 동기화를 일시정지(enabled=false)/재개한다."""
    cfg = _load_cfg()
    item = next((x for x in cfg.get("items", []) if x.get("id") == req.id), None)
    if not item:
        raise HTTPException(404, "item not found")
    item["enabled"] = bool(req.enabled)
    _save_cfg(cfg)
    return {"ok": True, "id": req.id, "enabled": bool(req.enabled)}


@router.get("/history")
def get_history(
    username: str = Query(""),
    id: str = Query(""),
    limit: int = Query(50),
    _perm=Depends(require_page_manager("filebrowser")),
):
    entries = jsonl_read(HISTORY_FILE, limit=max(1, min(500, limit)))
    if id:
        entries = [e for e in entries if e.get("id") == id]
    return {"entries": entries[::-1]}


# v8.4.4: lightweight schedule + push (local→S3) + history alias endpoints for FB gear
SCHEDULE_FILE = S3_DIR / "schedule.json"

@router.get("/schedule")
def get_schedule(username: str = Query(""), _perm=Depends(require_page_manager("filebrowser"))):
    d = load_json(SCHEDULE_FILE, {"enabled": False, "interval_minutes": 60})
    return d


# v8.6.4 — S3 status traffic light. 모든 유저 read-only. 미들웨어가 인증만 강제.
# 상태 산출:
#   green  = aws CLI 있고 + 최근 10건 모두 ok or 없음(설정 자체 없음)
#   yellow = aws CLI 없거나 / 최근 10건 중 1건 이상 실패 / 마지막 실행이 6h 초과
#   red    = 최근 5건 연속 실패
#   none   = 설정도 없음 + AWS 도 없음 (회색)
@router.get("/health")
def s3_health():
    history = jsonl_read(HISTORY_FILE, limit=40)
    cfg = _load_cfg()
    items = cfg.get("items", [])
    aws_ok = shutil.which("aws") is not None

    # v8.7.5: pull(다운로드) / push(업로드) 방향별 최근 상태 분리 계산.
    def _compute_light(entries):
        # 사용자 수동 중지(cancelled)는 동기화 실패로 집계하지 않는다.
        rec = [e for e in entries if (e.get("status") or "").lower() != "cancelled"][-10:]
        t = len(rec)
        f = sum(1 for e in rec if (e.get("status") or "").lower() not in ("ok", "success"))
        last5f = t >= 5 and all((e.get("status") or "").lower() not in ("ok", "success") for e in rec[-5:])
        last_ts_ = rec[-1].get("ts") if rec else ""
        stale = False
        if last_ts_:
            try:
                tm = datetime.datetime.fromisoformat(last_ts_.replace("Z", ""))
                stale = (datetime.datetime.now() - tm).total_seconds() > 6 * 3600
            except Exception:
                pass
        if not rec:
            return "none", t, f, last_ts_, stale
        if last5f:
            return "red", t, f, last_ts_, stale
        if f > 0 or stale:
            return "yellow", t, f, last_ts_, stale
        return "green", t, f, last_ts_, stale

    pull_entries = [e for e in history if _normalize_direction(e.get("direction")) == "download"]
    push_entries = [e for e in history if _normalize_direction(e.get("direction")) == "upload"]
    download_light, d_total, d_fails, d_last, d_stale = _compute_light(pull_entries)
    upload_light, u_total, u_fails, u_last, u_stale = _compute_light(push_entries)
    status = _load_status()
    status_times = {"download": [], "upload": []}
    for item in items:
        st = _status_for_item(status, item)
        direction = _item_direction(item)
        last = st.get("last_end") or st.get("last_start")
        ts = _parse_iso_ts(last)
        if ts:
            status_times.setdefault(direction, []).append((ts, last))
    if status_times.get("download"):
        d_last = max(status_times["download"], key=lambda x: x[0])[1]
    if status_times.get("upload"):
        u_last = max(status_times["upload"], key=lambda x: x[0])[1]

    # 기존 호환성: 전체 기준 light.
    recent = [e for e in history if (e.get("status") or "").lower() != "cancelled"][-10:]
    total = len(recent)
    fails = sum(1 for e in recent if (e.get("status") or "").lower() not in ("ok", "success"))
    last5_fail = total >= 5 and all(
        (e.get("status") or "").lower() not in ("ok", "success") for e in recent[-5:]
    )
    last_ts = recent[-1].get("ts") if recent else ""
    stale_6h = False
    if last_ts:
        try:
            t = datetime.datetime.fromisoformat(last_ts.replace("Z", ""))
            stale_6h = (datetime.datetime.now() - t).total_seconds() > 6 * 3600
        except Exception:
            pass
    # decision
    if not items and not aws_ok:
        light = "none"
    elif last5_fail:
        light = "red"
    elif (not aws_ok) or fails > 0 or stale_6h:
        light = "yellow"
    else:
        light = "green"
    msg_parts = []
    if not aws_ok:
        msg_parts.append("AWS CLI 미설치")
    if fails:
        msg_parts.append(f"최근 실패 {fails}/{total}")
    if stale_6h:
        msg_parts.append("최근 동기화 6h 경과")
    if light == "green":
        msg_parts.append("정상")
    if not items:
        msg_parts.append("설정 없음")
    return {
        "light": light,
        "download_light": download_light,
        "upload_light": upload_light,
        "download_last": d_last,
        "upload_last": u_last,
        "download_stale": d_stale,
        "upload_stale": u_stale,
        "download_failures": d_fails,
        "upload_failures": u_fails,
        "aws_available": aws_ok,
        "items_configured": len(items),
        "running_now": len(_RUNNING),
        "queued_now": len(_QUEUED),
        "recent_total": total,
        "recent_failures": fails,
        "last_synced_at": last_ts,
        "stale_6h": stale_6h,
        "message": " · ".join(msg_parts) or "—",
    }

@router.get("/auto-sync")
def get_auto_sync(username: str = Query(""), _perm=Depends(require_page_manager("filebrowser"))):
    return _auto_sync_settings()


class AutoSyncReq(BaseModel):
    auto_download_enabled: bool = True
    auto_upload_enabled: bool = True
    username: str = ""


@router.post("/auto-sync/save")
def save_auto_sync(req: AutoSyncReq, _perm=Depends(require_admin)):
    cfg = _load_cfg()
    cfg["auto_download_enabled"] = bool(req.auto_download_enabled)
    cfg["auto_upload_enabled"] = bool(req.auto_upload_enabled)
    _save_cfg(cfg)
    return {"ok": True, "auto_sync": _auto_sync_settings(cfg)}


class ScheduleReq(BaseModel):
    enabled: bool = False
    interval_minutes: int = 60
    username: str = ""

@router.post("/schedule/save")
def save_schedule(req: ScheduleReq, _perm=Depends(require_admin)):
    save_json(SCHEDULE_FILE, {"enabled": req.enabled, "interval_minutes": max(5, min(1440, req.interval_minutes))})
    return {"ok": True}


class PushReq(BaseModel):
    id: str
    username: str = ""

@router.post("/push")
def push_item(req: PushReq, _perm=Depends(require_page_manager("filebrowser"))):
    """v8.4.4 — 양방향 sync 의 local → S3 방향. 등록된 item 의 target s3 url 에
    local_path (db_root 기준) 를 업로드 (aws s3 cp/sync).
    """
    if not _auto_sync_settings().get("master_enabled", True):
        raise HTTPException(409, "관리자 설정에서 S3 전체가 꺼져 있습니다 (S3 master switch off)")
    cfg = _load_cfg()
    item = next((x for x in cfg.get("items", []) if x.get("id") == req.id), None)
    if not item: raise HTTPException(404, "item not found")
    if req.id in _RUNNING:
        return {"ok": True, "already_running": True}
    local_name = item.get("name") or item.get("target") or ""
    src = (_db_root() / local_name) if local_name else None
    dst = item.get("s3_url", "")
    if not dst or not dst.startswith("s3://"):
        raise HTTPException(400, "item has no valid s3_url")
    if not src or not src.exists():
        raise HTTPException(400, f"local source missing: {local_name}")
    cmd = item.get("command", "cp")
    if cmd not in ALLOWED_COMMANDS:
        cmd = "cp"
    args = ["aws", "s3", cmd, str(src), dst]
    endpoint = (item.get("endpoint_url") or "").strip()
    if endpoint: args += ["--endpoint-url", endpoint]
    profile = (item.get("profile") or "").strip()
    if profile:
        _validate_profile(profile)
        args += ["--profile", profile]
    if src.is_dir() and cmd == "cp": args += ["--recursive"]
    # Minimal sync execution — record in history
    entry = {"id": req.id, "direction": "push", "ts": datetime.datetime.now().isoformat(),
             "status": "starting", "cmd": " ".join(args)}
    jsonl_append(HISTORY_FILE, entry)
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=300, env=_aws_cli_env())
        ok = proc.returncode == 0
        done = {"id": req.id, "direction": "push", "ts": datetime.datetime.now().isoformat(),
                "status": "ok" if ok else "error",
                "stdout_tail": (proc.stdout or "")[-400:], "stderr_tail": (proc.stderr or "")[-400:]}
        jsonl_append(HISTORY_FILE, done); jsonl_trim(HISTORY_FILE, 500)
        return {"ok": ok, "entry": done}
    except Exception as e:
        jsonl_append(HISTORY_FILE, {"id": req.id, "direction": "push", "ts": datetime.datetime.now().isoformat(),
                                     "status": "error", "message": str(e)})
        raise HTTPException(500, f"push failed: {e}")


@router.get("/status-by-target")
def status_by_target(include_local: bool = True):
    """PUBLIC minimal status map — keyed by target name.
    Does NOT require admin; only exposes sync freshness for UI indicators.
    Does not leak s3_url, extra_args, endpoint_url, or full output.
    """
    cfg = _load_cfg()
    status = _load_status()
    by_target: Dict[str, Dict[str, Any]] = {}
    now = time.time()
    for it in cfg.get("items", []):
        tgt = it.get("target", "")
        if not tgt:
            continue
        st = _status_for_item(status, it)
        due = _item_due_state(it, st, now)
        is_running = it["id"] in _RUNNING or st.get("last_status") == "running"
        is_queued = it["id"] in _QUEUED
        info = {
            "kind": it.get("kind", "db"),
            "direction": (it.get("direction") or "download").lower(),
            "enabled": bool(it.get("enabled", True)),
            "interval_min": int(it.get("interval_min", 0) or 0),
            "last_status": st.get("last_status") or "never",
            "last_end": st.get("last_end") or None,
            "last_start": st.get("last_start") or None,
            "last_exit_code": st.get("last_exit_code"),
            "last_duration_sec": st.get("last_duration_sec"),
            "is_running": bool(is_running),
            "is_queued": bool(is_queued),
        }
        info.update(due)
        if include_local:
            info.update(_cached_latest_local_item_info(tgt))
        last_end = info["last_end"] or info["last_start"]
        last_ts = _parse_iso_ts(last_end)
        sync_recent_6h = bool(last_ts and (now - last_ts) <= 6 * 3600)
        stale_item = bool(info.get("latest_item_stale_6h"))
        # Downloaded files can legitimately keep the source object's older
        # mtime.  A successful recent sync is fresh even when the newest local
        # file timestamp itself is older than six hours.
        if info["is_queued"]:
            info["freshness_state"] = "queued"
        elif stale_item and not sync_recent_6h and not info["is_running"] and info["last_status"] == "ok":
            info["freshness_state"] = "stale_item"
        elif info["is_running"]:
            info["freshness_state"] = "running"
        elif info["last_status"] == "ok":
            info["freshness_state"] = "ok"
        elif info["last_status"] == "never":
            info["freshness_state"] = "never"
        else:
            info["freshness_state"] = "error"
        cur = by_target.get(tgt)
        if cur:
            def _rank(x):
                if x.get("is_running"): return 3
                if x.get("freshness_state") == "stale_item": return 2.5
                return 2 if x.get("last_status") == "ok" else 1
            if _rank(info) >= _rank(cur):
                by_target[tgt] = info
        else:
            by_target[tgt] = info
    _aggregate_child_statuses(by_target)
    return {"by_target": by_target, "local_freshness_included": bool(include_local)}


# ═════════════════════ AWS configure API (admin only) ═════════════════════
def _mask_secret(v: str) -> str:
    """Return last-4 masked for display. Keep empty if not set."""
    if not v:
        return ""
    if len(v) <= 4:
        return "••••"
    return "•" * (len(v) - 4) + v[-4:]


def _read_credentials() -> Dict[str, Dict[str, str]]:
    """Return {profile: {field: value}} from flow-data AWS credentials."""
    p = configparser.ConfigParser()
    if AWS_CREDENTIALS.exists():
        try:
            p.read(AWS_CREDENTIALS, encoding="utf-8")
        except Exception:
            pass
    return {s: dict(p.items(s)) for s in p.sections()}


def _read_config() -> Dict[str, Dict[str, str]]:
    """Return {section: {field: value}} from flow-data AWS config.

    Note: aws config uses 'profile NAME' section headers except for [default].
    We return with normalized key = profile name (stripped 'profile ' prefix).
    """
    p = configparser.ConfigParser()
    if AWS_CONFIG.exists():
        try:
            p.read(AWS_CONFIG, encoding="utf-8")
        except Exception:
            pass
    out: Dict[str, Dict[str, str]] = {}
    for s in p.sections():
        name = s[8:].strip() if s.lower().startswith("profile ") else s
        out[name] = dict(p.items(s))
    return out


@router.get("/aws-config")
def aws_config_get(username: str = Query(""), _perm=Depends(require_admin)):
    """Return profiles with masked secrets."""
    creds = _read_credentials()
    conf = _read_config()
    # Merge profile names from both files
    names = sorted(set(list(creds.keys()) + list(conf.keys())))
    profiles = []
    for name in names:
        c = creds.get(name, {})
        f = conf.get(name, {})
        profiles.append({
            "profile": name,
            "aws_access_key_id": c.get("aws_access_key_id", ""),
            "aws_secret_access_key_masked": _mask_secret(c.get("aws_secret_access_key", "")),
            "has_secret": bool(c.get("aws_secret_access_key", "")),
            "region": f.get("region", ""),
            "output": f.get("output", ""),
            "endpoint_url": f.get("endpoint_url", ""),  # saved via `aws configure set endpoint_url ...`
        })
    if not profiles:
        profiles = [{"profile": "default", "aws_access_key_id": "",
                     "aws_secret_access_key_masked": "", "has_secret": False,
                     "region": "", "output": "", "endpoint_url": ""}]
    return {
        "profiles": profiles,
        "credentials_path": str(AWS_CREDENTIALS),
        "config_path": str(AWS_CONFIG),
        "home": str(AWS_HOME),
        "aws_available": shutil.which("aws") is not None,
    }


class AwsConfigReq(BaseModel):
    username: str
    profile: str = "default"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""   # empty = keep current; non-empty = replace
    region: str = ""
    output: str = ""
    endpoint_url: str = ""


@router.post("/aws-config/save")
def aws_config_save(req: AwsConfigReq, _perm=Depends(require_admin)):
    """Save one profile. Secret: empty string means 'keep current', mask-string also means keep."""
    profile = (req.profile or "default").strip() or "default"
    if not AWS_PROFILE_RE.match(profile):
        raise HTTPException(400, f"invalid profile name: {profile!r}")

    akid = (req.aws_access_key_id or "").strip()
    if akid and not AWS_KEY_ID_RE.match(akid):
        raise HTTPException(400, "invalid aws_access_key_id (expect ALLCAPS/digits, 16-32 chars)")

    region = (req.region or "").strip()
    if region and not AWS_REGION_RE.match(region):
        raise HTTPException(400, "invalid region (expect lowercase/digits/dashes)")

    output = (req.output or "").strip()
    if output and output not in {"json", "text", "table", "yaml", "yaml-stream"}:
        raise HTTPException(400, "invalid output format (json|text|table|yaml|yaml-stream)")

    endpoint_url = (req.endpoint_url or "").strip()
    _validate_endpoint_url(endpoint_url)

    # Ensure flow-data AWS credential directory exists with mode 700
    try:
        AWS_HOME.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(AWS_HOME, 0o700)
        except Exception:
            pass
    except Exception as e:
        raise HTTPException(500, f"cannot create {AWS_HOME}: {e}")

    # Load existing secret to preserve if the incoming one is empty or masked
    incoming_secret = (req.aws_secret_access_key or "").strip()
    existing = _read_credentials()
    cur_secret = existing.get(profile, {}).get("aws_secret_access_key", "")
    # Treat dot/bullet-only strings as "keep current"
    is_mask = incoming_secret and all(c in "•*·.xX " for c in incoming_secret)
    if (not incoming_secret) or is_mask:
        new_secret = cur_secret
    else:
        # Basic sanity — AWS secret keys are 40 base64-ish chars, but allow 16-128
        if not re.match(r"^[A-Za-z0-9/+=]{16,128}$", incoming_secret):
            raise HTTPException(400, "invalid aws_secret_access_key format")
        new_secret = incoming_secret

    # --- Write credentials file ---
    cp = configparser.ConfigParser()
    if AWS_CREDENTIALS.exists():
        try:
            cp.read(AWS_CREDENTIALS, encoding="utf-8")
        except Exception:
            pass
    if not cp.has_section(profile):
        cp.add_section(profile)
    if akid:
        cp.set(profile, "aws_access_key_id", akid)
    elif cp.has_option(profile, "aws_access_key_id") and not akid:
        # empty akid + no existing => remove; but keep existing if akid field blank
        pass
    if new_secret:
        cp.set(profile, "aws_secret_access_key", new_secret)
    # Write with restricted permissions
    try:
        tmp = AWS_CREDENTIALS.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            cp.write(f)
        try:
            os.chmod(tmp, 0o600)
        except Exception:
            pass
        os.replace(tmp, AWS_CREDENTIALS)
    except Exception as e:
        raise HTTPException(500, f"failed to write {AWS_CREDENTIALS}: {e}")

    # --- Write config file (region/output/endpoint_url) ---
    section_name = profile if profile == "default" else f"profile {profile}"
    conf = configparser.ConfigParser()
    if AWS_CONFIG.exists():
        try:
            conf.read(AWS_CONFIG, encoding="utf-8")
        except Exception:
            pass
    if not conf.has_section(section_name):
        conf.add_section(section_name)
    if region:
        conf.set(section_name, "region", region)
    if output:
        conf.set(section_name, "output", output)
    if endpoint_url:
        conf.set(section_name, "endpoint_url", endpoint_url)
    else:
        # If explicitly blanked, remove the option
        if conf.has_option(section_name, "endpoint_url"):
            conf.remove_option(section_name, "endpoint_url")
    try:
        tmp = AWS_CONFIG.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            conf.write(f)
        try:
            os.chmod(tmp, 0o600)
        except Exception:
            pass
        os.replace(tmp, AWS_CONFIG)
    except Exception as e:
        raise HTTPException(500, f"failed to write {AWS_CONFIG}: {e}")

    return {"ok": True, "profile": profile}


class AwsProfileReq(BaseModel):
    username: str
    profile: str


@router.post("/aws-config/delete")
def aws_config_delete(req: AwsProfileReq, _perm=Depends(require_admin)):
    profile = (req.profile or "").strip()
    if not AWS_PROFILE_RE.match(profile):
        raise HTTPException(400, f"invalid profile name: {profile!r}")
    if profile == "default":
        raise HTTPException(400, "refuse to delete 'default' profile")

    # credentials
    cp = configparser.ConfigParser()
    if AWS_CREDENTIALS.exists():
        cp.read(AWS_CREDENTIALS, encoding="utf-8")
        if cp.has_section(profile):
            cp.remove_section(profile)
            with open(AWS_CREDENTIALS, "w", encoding="utf-8") as f:
                cp.write(f)

    # config
    conf = configparser.ConfigParser()
    if AWS_CONFIG.exists():
        conf.read(AWS_CONFIG, encoding="utf-8")
        sec = f"profile {profile}"
        if conf.has_section(sec):
            conf.remove_section(sec)
            with open(AWS_CONFIG, "w", encoding="utf-8") as f:
                conf.write(f)

    return {"ok": True}
