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
DB_BASE = PATHS.db_root

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
TARGET_RE = re.compile(r"^[a-zA-Z0-9_\-\.]{1,128}$")
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


def _validate_s3_url(url: str):
    if not url or not S3_URL_RE.match(url):
        raise HTTPException(400, f"invalid s3 url: {url!r}")


def _validate_endpoint_url(url: str):
    """endpoint_url is optional; empty = none."""
    if not url:
        return
    if not ENDPOINT_URL_RE.match(url):
        raise HTTPException(400, f"invalid endpoint_url: {url!r}")


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
    if not TARGET_RE.match(target):
        raise HTTPException(400, f"invalid target: {target!r}")

    kind = item.get("kind", "db")
    if kind not in {"db", "root_parquet"}:
        raise HTTPException(400, f"invalid kind: {kind}")

    local = DB_BASE / target
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

    extra = _validate_extra_args(item.get("extra_args", ""))
    # Build: aws s3 <cmd> <src> <dst> --endpoint-url URL <extra>
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
        "db_base": str(DB_BASE),
    }


@router.get("/available")
def list_available(username: str = Query("")):
    """List DBs and root parquets that can be configured."""
    _require_admin(username)
    dbs, files = [], []
    if DB_BASE.exists():
        for p in sorted(DB_BASE.iterdir()):
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
    interval_min: int = 0
    enabled: bool = True


@router.post("/save")
def save_item(req: SaveReq):
    _require_admin(req.username)
    if req.command not in ALLOWED_COMMANDS:
        raise HTTPException(400, f"invalid command: {req.command}")
    if req.kind not in {"db", "root_parquet"}:
        raise HTTPException(400, f"invalid kind: {req.kind}")
    if not TARGET_RE.match(req.target or ""):
        raise HTTPException(400, f"invalid target: {req.target!r}")
    _validate_s3_url(req.s3_url)
    _validate_endpoint_url((req.endpoint_url or "").strip())
    _validate_extra_args(req.extra_args)
    if req.kind == "root_parquet" and req.command == "sync":
        raise HTTPException(400, "'sync' is only valid for kind='db'. Use 'cp' for root_parquet.")

    item_id = (req.id or "").strip() or f"{req.kind}_{re.sub(r'[^a-zA-Z0-9]', '_', req.target)[:30]}_{uuid.uuid4().hex[:6]}"
    if not ID_RE.match(item_id):
        raise HTTPException(400, f"invalid id: {item_id}")

    new_item = {
        "id": item_id,
        "kind": req.kind, "target": req.target,
        "s3_url": req.s3_url, "command": req.command,
        "extra_args": req.extra_args,
        "endpoint_url": (req.endpoint_url or "").strip(),
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
    history = jsonl_read(HISTORY_FILE, limit=20)
    cfg = _load_cfg()
    items = cfg.get("items", [])
    aws_ok = shutil.which("aws") is not None
    # 가장 최근 10건만 분석 (jsonl_read 는 오래된 것부터)
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
    src = (DB_BASE / local_name) if local_name else None
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
            "enabled": bool(it.get("enabled", True)),
            "interval_min": int(it.get("interval_min", 0) or 0),
            "last_status": st.get("last_status") or "never",
            "last_end": st.get("last_end") or None,
            "last_start": st.get("last_start") or None,
            "last_exit_code": st.get("last_exit_code"),
            "last_duration_sec": st.get("last_duration_sec"),
            "is_running": bool(is_running),
        }
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
        cur = by_target.get(tgt)
        if cur:
            def _rank(x):
                if x.get("is_running"): return 3
                return 2 if x.get("last_status") == "ok" else 1
            if _rank(info) >= _rank(cur):
                by_target[tgt] = info
        else:
            by_target[tgt] = info
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
