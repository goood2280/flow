"""core/valve_watch.py — poll valve config/status alerts mirrored from S3.

Purpose:
  - Valve 가 S3 또는 shared workspace 에 남긴 config/status alert JSONL 을 flow 가 주기적으로 읽음
  - severity warn/error 이벤트를 admin bell notification 으로 전달
  - 중복 발송 방지를 위해 마지막 fingerprint 를 로컬 state 에 저장

Expected event line example:
  {
    "ts": 1776991200,
    "source": "valve.config_sync",
    "kind": "config_fallback_last_good",
    "severity": "warn",
    "title": "products.yaml — last_good 로 fallback 완료",
    "meta": {"name": "products.yaml"}
  }
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from pathlib import Path
from typing import Dict, List

from core.paths import PATHS
from core.notify import send_to_admins
from core.utils import load_json, save_json

logger = logging.getLogger("flow.valve_watch")

CFG_PATH = PATHS.data_root / "valve_watch.json"
STATE_PATH = PATHS.log_dir / "valve_watch_state.json"

DEFAULT_CFG = {
    "enabled": True,
    "poll_seconds": 300,
    "notify_min_severity": "warn",
    "paths": [
        str(PATHS.data_root / "valve" / "config_alerts.jsonl"),
        str(PATHS.data_root / "valve" / "status_alerts.jsonl"),
    ],
}

_thread: threading.Thread | None = None
_started = False
_lock = threading.Lock()


def _load_cfg() -> dict:
    cfg = load_json(CFG_PATH, DEFAULT_CFG)
    if not isinstance(cfg, dict):
        cfg = {}
    out = dict(DEFAULT_CFG)
    out.update(cfg)
    paths = out.get("paths")
    out["paths"] = [str(p) for p in paths] if isinstance(paths, list) else list(DEFAULT_CFG["paths"])
    return out


def _save_cfg_if_missing() -> None:
    if not CFG_PATH.exists():
        CFG_PATH.parent.mkdir(parents=True, exist_ok=True)
        save_json(CFG_PATH, DEFAULT_CFG)


def _load_state() -> dict:
    st = load_json(STATE_PATH, {})
    return st if isinstance(st, dict) else {}


def _save_state(st: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    save_json(STATE_PATH, st)


def _sev_rank(v: str) -> int:
    s = str(v or "").lower()
    return {"info": 0, "warn": 1, "warning": 1, "error": 2}.get(s, 0)


def _iter_new_events(path: Path, state: dict) -> List[dict]:
    key = str(path)
    if not path.exists() or not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return []
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    last_fp = (state.get("last_fp") or {}).get(key, "")
    out: List[dict] = []
    seen_last = not bool(last_fp)
    for ln in lines:
        fp = hashlib.sha1(ln.encode("utf-8")).hexdigest()[:16]
        if not seen_last:
            if fp == last_fp:
                seen_last = True
            continue
        try:
            evt = json.loads(ln)
        except Exception:
            continue
        evt["_fp"] = fp
        out.append(evt)
    return out


def _title(evt: dict) -> str:
    src = str(evt.get("source") or "valve")
    kind = str(evt.get("kind") or "status")
    ttl = str(evt.get("title") or f"{src} {kind}")
    return f"[Valve] {ttl}"


def _body(evt: dict) -> str:
    meta = evt.get("meta") or {}
    parts = [
        f"source={evt.get('source') or 'valve'}",
        f"kind={evt.get('kind') or '-'}",
        f"severity={evt.get('severity') or 'info'}",
    ]
    if isinstance(meta, dict):
        for k in ("name", "key", "error"):
            v = meta.get(k)
            if v not in (None, "", [], {}):
                parts.append(f"{k}={v}")
    return " | ".join(parts)


def poll_once() -> dict:
    cfg = _load_cfg()
    state = _load_state()
    if not cfg.get("enabled", True):
        return {"ok": True, "enabled": False, "events": 0}
    min_sev = _sev_rank(cfg.get("notify_min_severity", "warn"))
    total = 0
    notified = 0
    last_fp_map = dict(state.get("last_fp") or {})
    for raw in cfg.get("paths") or []:
        path = Path(str(raw))
        new_events = _iter_new_events(path, state)
        total += len(new_events)
        for evt in new_events:
            if _sev_rank(evt.get("severity")) < min_sev:
                continue
            send_to_admins(_title(evt), _body(evt), "warn" if _sev_rank(evt.get("severity")) < 2 else "approval")
            notified += 1
        if new_events:
            last_fp_map[str(path)] = new_events[-1]["_fp"]
    state["last_fp"] = last_fp_map
    _save_state(state)
    return {"ok": True, "enabled": True, "events": total, "notified": notified}


def _loop():
    logger.info("[valve_watch] background loop started")
    while True:
        try:
            res = poll_once()
            if res.get("events"):
                logger.info("[valve_watch] polled %s events, notified %s", res.get("events"), res.get("notified"))
        except Exception as e:
            logger.warning("[valve_watch] tick failed: %s", e)
        cfg = _load_cfg()
        time.sleep(max(30, int(cfg.get("poll_seconds", 300) or 300)))


def start_scheduler() -> None:
    global _thread, _started
    with _lock:
        if _started:
            return
        _save_cfg_if_missing()
        _thread = threading.Thread(target=_loop, name="valve-watch", daemon=True)
        _thread.start()
        _started = True
        logger.info("[valve_watch] scheduler started")
