"""routers/s3_ingest.py v8.1.0 — S3 → local DB ingestion
Admin-configurable 'aws s3 cp|sync' commands per DB / root_parquet.
Scheduled (interval_min) + manual refresh. Whitelist-based arg validation.

v8.1.0 adds:
  - endpoint_url as a dedicated per-item field (auto-prepended as --endpoint-url)
  - GET/POST /aws-config to manage ~/.aws/credentials + ~/.aws/config (admin only)

Storage:
  data_root/s3_ingest/config.json       — item configs
  data_root/s3_ingest/status.json       — per-item last run status
  data_root/s3_ingest/history.jsonl     — recent run history (trimmed 500)
"""
from __future__ import annotations
import os, re, time, uuid, shlex, shutil, datetime, threading, subprocess, configparser
from pathlib import Path
from typing import List, Dict, Any
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from core.paths import PATHS
from core.utils import load_json, save_json, jsonl_append, jsonl_read, jsonl_trim

router = APIRouter(prefix="/api/s3ingest", tags=["s3ingest"])

# ── Paths ──
S3_DIR = PATHS.data_root / "s3_ingest"
S3_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = S3_DIR / "config.json"
STATUS_FILE = S3_DIR / "status.json"
HISTORY_FILE = S3_DIR / "history.jsonl"


def _db_root() -> Path:
    return PATHS.db_root

# ── AWS config paths (per the user running the backend) ──
AWS_HOME = Path(os.path.expanduser("~/.aws"))
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
_RUNNING_LOCK = threading.Lock()
_SCHED_STARTED = False
_SCHED_LOCK = threading.Lock()


# ═════════════════════ helpers ═════════════════════
def _load_cfg() -> Dict[str, Any]:
    return load_json(CONFIG_FILE, {"items": []})


def _save_cfg(cfg):
    save_json(CONFIG_FILE, cfg, indent=2)


def _load_status() -> Dict[str, Any]:
    return load_json(STATUS_FILE, {})


def _save_status(st):
    save_json(STATUS_FILE, st, indent=2)


def _update_status(item_id: str, **patch):
    st = _load_status()
    cur = st.get(item_id, {})
    cur.update(patch)
    st[item_id] = cur
    _save_status(st)


def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _fmt_ts(ts: float | None) -> str | None:
    if not ts:
        return None
    try:
        return datetime.datetime.fromtimestamp(ts).isoformat(timespec="seconds")
    except Exception:
        return None


def _is_admin(username: str) -> bool:
    if not username:
        return False
    try:
        from routers.auth import read_users
        for u in read_users():
            if u.get("username") == username and u.get("role") == "admin":
                return True
    except Exception:
        pass
    return False


def _require_admin(username: str):
    if not _is_admin(username):
        raise HTTPException(403, "admin only")


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
            for p in local.rglob("*"):
                try:
                    if not p.is_file():
                        continue
                    if p.suffix.lower() not in exts:
                        continue
                    mt = p.stat().st_mtime
                    if mt > newest_ts:
                        newest_ts = mt
                        newest_path = p
                except Exception:
                    continue

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
        aggregate = {
            "kind": "aggregate",
            "direction": directions[0] if len(directions) == 1 else "mixed",
            "enabled": all(bool(c.get("enabled", True)) for c in children),
            "interval_min": min(intervals) if intervals else 0,
            "last_status": "ok" if last_status == "running" else last_status,
            "last_end": _fmt_iso_epoch(min(last_times)) if last_times else None,
            "last_start": None,
            "last_exit_code": None,
            "last_duration_sec": None,
            "is_running": any(c.get("is_running") for c in children),
            "latest_item_at": _fmt_iso_epoch(max(latest_item_times)) if latest_item_times else None,
            "latest_item_relpath": f"{len(children)} child targets",
            "latest_item_age_hours": None,
            "latest_item_stale_6h": any(bool(c.get("latest_item_stale_6h")) for c in children),
            "latest_item_scan_error": None,
            "next_due": _fmt_iso_epoch(min(next_times)) if next_times else None,
            "aggregate": True,
            "child_targets": len(children),
        }
        if aggregate["is_running"]:
            aggregate["freshness_state"] = "running"
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
    # v8.1.4: fallback to ~/.aws/config default profile endpoint_url when item has none
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
    _update_status(item_id, last_start=_now_iso(), last_status="running")

    try:
        args, _local = _build_cmd(item)
    except HTTPException as e:
        _update_status(item_id,
                       last_status="error",
                       last_output_tail=f"validation failed: {e.detail}",
                       last_end=_now_iso(),
                       last_duration_sec=0)
        jsonl_append(HISTORY_FILE, {"id": item_id, "status": "error", "error": str(e.detail)})
        with _RUNNING_LOCK:
            _RUNNING.pop(item_id, None)
        return

    t0 = time.time()
    status = "error"; exit_code = -1; tail = ""
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=MAX_RUNTIME_SEC)
        out = (proc.stdout or "") + (proc.stderr or "")
        tail = out[-2000:] if out else ""
        exit_code = proc.returncode
        status = "ok" if proc.returncode == 0 else "error"
    except FileNotFoundError:
        tail = "aws CLI not installed on host (apt install awscli or pip install awscli)"
    except subprocess.TimeoutExpired:
        tail = f"timeout after {MAX_RUNTIME_SEC}s"
    except Exception as e:
        tail = f"exception: {str(e)[:1800]}"

    dur = int(time.time() - t0)
    _update_status(item_id,
                   last_end=_now_iso(),
                   last_status=status,
                   last_exit_code=exit_code,
                   last_output_tail=tail,
                   last_duration_sec=dur)
    jsonl_append(HISTORY_FILE, {
        "id": item_id, "target": item.get("target"), "kind": item.get("kind"),
        "direction": (item.get("direction") or "download").lower(),
        "status": status, "exit_code": exit_code, "duration_sec": dur,
        "cmd": " ".join(args),
    })
    jsonl_trim(HISTORY_FILE, 500)
    with _RUNNING_LOCK:
        _RUNNING.pop(item_id, None)


def _schedule_run(item_id: str) -> bool:
    with _RUNNING_LOCK:
        if item_id in _RUNNING:
            return False
        t = threading.Thread(target=_run_item_blocking, args=(item_id,),
                             daemon=True, name=f"s3ingest-{item_id}")
        _RUNNING[item_id] = {"thread": t, "start": time.time()}
    t.start()
    return True


def _scheduler_loop():
    while True:
        try:
            cfg = _load_cfg()
            status = _load_status()
            now = time.time()
            for item in cfg.get("items", []):
                if not item.get("enabled", True):
                    continue
                iv = int(item.get("interval_min", 0) or 0)
                if iv <= 0:
                    continue
                st = status.get(item["id"], {})
                last_end = st.get("last_end") or st.get("last_start")
                last_ts = 0.0
                if last_end:
                    try:
                        last_ts = datetime.datetime.fromisoformat(last_end).timestamp()
                    except Exception:
                        last_ts = 0.0
                if now - last_ts >= iv * 60:
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
def list_items(username: str = Query("")):
    _require_admin(username)
    cfg = _load_cfg()
    status = _load_status()
    out = []
    for it in cfg.get("items", []):
        merged = dict(it)
        merged["status"] = status.get(it["id"], {})
        merged["is_running"] = it["id"] in _RUNNING
        # backward compat: ensure endpoint_url always present in response
        merged.setdefault("endpoint_url", "")
        out.append(merged)
    return {
        "items": out,
        "aws_available": shutil.which("aws") is not None,
        "db_base": str(_db_root()),
    }


@router.get("/available")
def list_available(username: str = Query("")):
    """List DBs and root parquets that can be configured."""
    _require_admin(username)
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


@router.post("/save")
def save_item(req: SaveReq):
    _require_admin(req.username)
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

    item_id = (req.id or "").strip() or f"{req.kind}_{re.sub(r'[^a-zA-Z0-9]', '_', req.target)[:30]}_{uuid.uuid4().hex[:6]}"
    if not ID_RE.match(item_id):
        raise HTTPException(400, f"invalid id: {item_id}")

    direction = (req.direction or "download").strip().lower()
    if direction not in {"download", "upload"}:
        raise HTTPException(400, f"invalid direction: {direction!r} (must be 'download' or 'upload')")
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

    cfg = _load_cfg()
    items = cfg.get("items", [])
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
def delete_item(req: IdReq):
    _require_admin(req.username)
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
def run_manual(req: IdReq):
    _require_admin(req.username)
    cfg = _load_cfg()
    if not any(x.get("id") == req.id for x in cfg.get("items", [])):
        raise HTTPException(404, "item not found")
    if req.id in _RUNNING:
        return {"ok": True, "already_running": True}
    started = _schedule_run(req.id)
    return {"ok": True, "started": started}


@router.get("/history")
def get_history(username: str = Query(""), id: str = Query(""), limit: int = Query(50)):
    _require_admin(username)
    entries = jsonl_read(HISTORY_FILE, limit=max(1, min(500, limit)))
    if id:
        entries = [e for e in entries if e.get("id") == id]
    return {"entries": entries[::-1]}


# v8.4.4: lightweight schedule + push (local→S3) + history alias endpoints for FB gear
SCHEDULE_FILE = S3_DIR / "schedule.json"

@router.get("/schedule")
def get_schedule(username: str = Query("")):
    _require_admin(username)
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
        rec = entries[-10:]
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

    pull_entries = [e for e in history if (e.get("direction") or "pull") == "pull"]
    push_entries = [e for e in history if e.get("direction") == "push"]
    download_light, d_total, d_fails, d_last, d_stale = _compute_light(pull_entries)
    upload_light, u_total, u_fails, u_last, u_stale = _compute_light(push_entries)

    # 기존 호환성: 전체 기준 light.
    recent = history[-10:]
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
        "recent_total": total,
        "recent_failures": fails,
        "last_synced_at": last_ts,
        "stale_6h": stale_6h,
        "message": " · ".join(msg_parts) or "—",
    }

class ScheduleReq(BaseModel):
    enabled: bool = False
    interval_minutes: int = 60
    username: str = ""

@router.post("/schedule/save")
def save_schedule(req: ScheduleReq):
    _require_admin(req.username)
    save_json(SCHEDULE_FILE, {"enabled": req.enabled, "interval_minutes": max(5, min(1440, req.interval_minutes))})
    return {"ok": True}


class PushReq(BaseModel):
    id: str
    username: str = ""

@router.post("/push")
def push_item(req: PushReq):
    """v8.4.4 — 양방향 sync 의 local → S3 방향. 등록된 item 의 target s3 url 에
    local_path (db_root 기준) 를 업로드 (aws s3 cp/sync).
    """
    _require_admin(req.username)
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
        proc = subprocess.run(args, capture_output=True, text=True, timeout=300)
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
def status_by_target():
    """PUBLIC minimal status map — keyed by target name.
    Does NOT require admin; only exposes sync freshness for UI indicators.
    Does not leak s3_url, extra_args, endpoint_url, or full output.
    """
    cfg = _load_cfg()
    status = _load_status()
    by_target: Dict[str, Dict[str, Any]] = {}
    for it in cfg.get("items", []):
        tgt = it.get("target", "")
        if not tgt:
            continue
        st = status.get(it["id"], {}) or {}
        is_running = it["id"] in _RUNNING or st.get("last_status") == "running"
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
        }
        info.update(_latest_local_item_info(tgt))
        iv = info["interval_min"]
        last_end = info["last_end"] or info["last_start"]
        if iv > 0 and last_end:
            try:
                last_ts = datetime.datetime.fromisoformat(last_end)
                info["next_due"] = (last_ts + datetime.timedelta(minutes=iv)).isoformat(timespec="seconds")
            except Exception:
                info["next_due"] = None
        else:
            info["next_due"] = None
        last_ts = _parse_iso_ts(last_end)
        sync_recent_6h = bool(last_ts and (time.time() - last_ts) <= 6 * 3600)
        stale_item = bool(info.get("latest_item_stale_6h"))
        # Downloaded files can legitimately keep the source object's older
        # mtime.  A successful recent sync is fresh even when the newest local
        # file timestamp itself is older than six hours.
        if stale_item and not sync_recent_6h and not info["is_running"] and info["last_status"] == "ok":
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
    return {"by_target": by_target}


# ═════════════════════ AWS configure API (admin only) ═════════════════════
def _mask_secret(v: str) -> str:
    """Return last-4 masked for display. Keep empty if not set."""
    if not v:
        return ""
    if len(v) <= 4:
        return "••••"
    return "•" * (len(v) - 4) + v[-4:]


def _read_credentials() -> Dict[str, Dict[str, str]]:
    """Return {profile: {field: value}} from ~/.aws/credentials (empty dict if none)."""
    p = configparser.ConfigParser()
    if AWS_CREDENTIALS.exists():
        try:
            p.read(AWS_CREDENTIALS, encoding="utf-8")
        except Exception:
            pass
    return {s: dict(p.items(s)) for s in p.sections()}


def _read_config() -> Dict[str, Dict[str, str]]:
    """Return {section: {field: value}} from ~/.aws/config.

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
def aws_config_get(username: str = Query("")):
    """Return profiles with masked secrets."""
    _require_admin(username)
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
def aws_config_save(req: AwsConfigReq):
    """Save one profile. Secret: empty string means 'keep current', mask-string also means keep."""
    _require_admin(req.username)
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

    # Ensure ~/.aws exists with mode 700
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
def aws_config_delete(req: AwsProfileReq):
    _require_admin(req.username)
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
