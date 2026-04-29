"""core/tracker_scheduler.py v1.0.0 (v9.0.0)
30분 주기로 Tracker 의 Lot watch 를 전체 폴링 — FAB step 도달 / ET 신규 측정 감지 시
작성자·lot 추가자에게 bell 알림 + (issue.mail_watch.enabled=True 시) 메일 발송.

철학:
  - 실시간은 부하 크니 30분 cadence 로 타협.
  - 카테고리-driven source (Monitor→fab, Analysis→et) 에 따라 감지 동작이 다름.
  - 모든 wafer 가 auto_close_step 넘으면 자동 이슈 완료 처리 (메일 X, bell 만).
"""
from __future__ import annotations

import logging
import os
import threading
import time

logger = logging.getLogger("flow.tracker_sched")

_scheduler_thread: threading.Thread | None = None
_scheduler_started = False
_scan_lock = threading.Lock()

_DEFAULT_INTERVAL_MINUTES = 30
_MIN_INTERVAL_MINUTES = 1
_MAX_INTERVAL_MINUTES = 24 * 60
_DEFAULT_ET_STABLE_DELAY_MINUTES = 180
_MIN_ET_STABLE_DELAY_MINUTES = 1
_MAX_ET_STABLE_DELAY_MINUTES = 24 * 60


def _now_iso() -> str:
    import datetime as _dt
    return _dt.datetime.now().isoformat(timespec="seconds")


def _tracker_dir():
    from core.paths import PATHS
    return PATHS.data_root / "tracker"


def _settings_file():
    from core.paths import PATHS
    return PATHS.data_root / "settings.json"


def _status_file():
    return _tracker_dir() / "scheduler_status.json"


def _coerce_bool(value, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    return default


def _coerce_interval_minutes(value, default: int = _DEFAULT_INTERVAL_MINUTES) -> int:
    try:
        minutes = int(value)
    except Exception:
        minutes = default
    return max(_MIN_INTERVAL_MINUTES, min(_MAX_INTERVAL_MINUTES, minutes))


def _coerce_et_stable_delay_minutes(value, default: int = _DEFAULT_ET_STABLE_DELAY_MINUTES) -> int:
    try:
        minutes = int(value)
    except Exception:
        minutes = default
    return max(_MIN_ET_STABLE_DELAY_MINUTES, min(_MAX_ET_STABLE_DELAY_MINUTES, minutes))


def _settings_interval_default() -> int:
    return _coerce_interval_minutes(os.environ.get("FLOW_TRACKER_POLL_MIN", _DEFAULT_INTERVAL_MINUTES))


def _read_settings() -> dict:
    from core.utils import load_json
    raw = load_json(_settings_file(), {})
    return raw if isinstance(raw, dict) else {}


def scheduler_config() -> dict:
    """Return Admin-configurable tracker scheduler settings."""
    settings = _read_settings()
    tracker = settings.get("tracker") if isinstance(settings.get("tracker"), dict) else {}
    minutes = settings.get("tracker_poll_minutes", tracker.get("interval_minutes", _settings_interval_default()))
    enabled = settings.get("tracker_poll_enabled", tracker.get("enabled", True))
    et_delay = settings.get(
        "tracker_et_stable_delay_minutes",
        tracker.get("et_stable_delay_minutes", _DEFAULT_ET_STABLE_DELAY_MINUTES),
    )
    return {
        "enabled": _coerce_bool(enabled, True),
        "interval_minutes": _coerce_interval_minutes(minutes, _settings_interval_default()),
        "et_stable_delay_minutes": _coerce_et_stable_delay_minutes(et_delay),
        "min_interval_minutes": _MIN_INTERVAL_MINUTES,
        "max_interval_minutes": _MAX_INTERVAL_MINUTES,
        "min_et_stable_delay_minutes": _MIN_ET_STABLE_DELAY_MINUTES,
        "max_et_stable_delay_minutes": _MAX_ET_STABLE_DELAY_MINUTES,
    }


def save_scheduler_config(*, enabled: bool, interval_minutes: int, et_stable_delay_minutes: int | None = None) -> dict:
    from core.utils import save_json
    settings = _read_settings()
    interval = _coerce_interval_minutes(interval_minutes, _settings_interval_default())
    et_delay = _coerce_et_stable_delay_minutes(
        et_stable_delay_minutes
        if et_stable_delay_minutes is not None
        else settings.get("tracker_et_stable_delay_minutes", _DEFAULT_ET_STABLE_DELAY_MINUTES)
    )
    settings["tracker_poll_enabled"] = bool(enabled)
    settings["tracker_poll_minutes"] = interval
    settings["tracker_et_stable_delay_minutes"] = et_delay
    tracker = settings.get("tracker") if isinstance(settings.get("tracker"), dict) else {}
    settings["tracker"] = {
        **tracker,
        "enabled": bool(enabled),
        "interval_minutes": interval,
        "et_stable_delay_minutes": et_delay,
    }
    save_json(_settings_file(), settings)
    return scheduler_config()


def _write_status(status: dict) -> dict:
    try:
        from core.utils import save_json
        payload = dict(status or {})
        payload["running"] = _scan_lock.locked() if "running" not in payload else bool(payload["running"])
        save_json(_status_file(), payload, indent=2)
        return payload
    except Exception as e:
        logger.warning(f"tracker scheduler status save failed: {e}")
        return status or {}


def scheduler_status() -> dict:
    from core.utils import load_json
    status = load_json(_status_file(), {})
    if not isinstance(status, dict):
        status = {}
    return {**scheduler_config(), "status": {**status, "running": _scan_lock.locked()}}


def _interval_seconds() -> int:
    return scheduler_config()["interval_minutes"] * 60


def _category_source(category: str, cat_meta: dict, default: str = "auto") -> str:
    name = (category or "").strip()
    low = name.lower()
    try:
        from core.lot_step import tracker_role_names_config
        roles = tracker_role_names_config()
    except Exception:
        roles = {"monitor": "Monitor", "analysis": "Analysis"}
    if low == str(roles.get("monitor") or "Monitor").strip().lower():
        return "fab"
    if low == str(roles.get("analysis") or "Analysis").strip().lower():
        return "et"
    src = (cat_meta.get("source") or default or "auto").lower().strip()
    return src if src in ("fab", "et", "both", "auto") else "auto"


def _row_source_for_watch(category_source: str, watch: dict) -> str:
    src = (category_source or "auto").lower().strip()
    if src == "et":
        return "et"
    if src == "fab":
        return "fab"
    watch_source = ((watch or {}).get("source") or "fab").lower().strip()
    return watch_source if watch_source in ("fab", "et") else "fab"


def _unique_list(values) -> list:
    out = []
    seen = set()
    for value in values or []:
        s = str(value or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _recipient_payload(base_users, group_ids: list) -> dict:
    """Expand issue/watch recipients into app users plus external group emails."""
    users = _unique_list(base_users)
    extra_emails = []
    gids = {str(g or "") for g in (group_ids or []) if str(g or "").strip()}
    if not gids:
        return {"users": users, "extra_emails": extra_emails}
    try:
        from routers.groups import _load as _grp_load
        for group in _grp_load():
            if str(group.get("id") or "") not in gids:
                continue
            for member in group.get("members") or []:
                if member and member not in users:
                    users.append(member)
            for email in group.get("extra_emails") or []:
                if email and email not in extra_emails:
                    extra_emails.append(email)
    except Exception:
        pass
    return {"users": users, "extra_emails": extra_emails}


def _issue_mail_watch(issue: dict) -> dict:
    current = issue.get("mail_watch") if isinstance(issue.get("mail_watch"), dict) else None
    if current is not None:
        return {
            "enabled": bool(current.get("enabled")),
            "mail_group_ids": [str(x) for x in (current.get("mail_group_ids") or []) if str(x).strip()],
        }
    groups = []
    enabled = False
    for lot in issue.get("lots") or []:
        watch = lot.get("watch") if isinstance(lot, dict) else {}
        if not isinstance(watch, dict) or not watch.get("mail"):
            continue
        enabled = True
        groups.extend(watch.get("mail_group_ids") or [])
    return {"enabled": enabled, "mail_group_ids": _unique_list(groups)}


def _mark_watch_fired(watch: dict, target_step: str, fired_step: str) -> dict:
    if not target_step:
        return watch
    fired = _unique_list(watch.get("fired_target_step_ids") or [])
    if target_step not in fired:
        fired.append(target_step)
    watch["fired_target_step_ids"] = fired
    watch["last_fired_at"] = _now_iso()
    watch["last_fired_step_id"] = fired_step or ""
    return watch


def _scan_once() -> dict:
    """Open Tracker issues를 1회 스캔하고 lot 행의 현재 진행 정보를 저장한다."""
    started_at = _now_iso()
    config = scheduler_config()
    summary = {
        "ok": True,
        "enabled": config["enabled"],
        "interval_minutes": config["interval_minutes"],
        "et_stable_delay_minutes": config["et_stable_delay_minutes"],
        "started_at": started_at,
        "finished_at": "",
        "issues_scanned": 0,
        "lots_scanned": 0,
        "lots_updated": 0,
        "watches_checked": 0,
        "fire_count": 0,
        "notify_count": 0,
        "mail_count": 0,
        "last_error": "",
    }
    try:
        from core.paths import PATHS
        from core.utils import load_json, save_json
        from core.lot_step import (
            _fab_step_reached,
            _is_root_lot_id,
            compare_to_watch,
            expand_lot_row_for_wafer_selection,
            lot_step_snapshot,
            source_root_for_context,
            snapshot_row_fields,
        )
        from core.notify import emit_event
        from core.tracker_schema import normalize_lot_row
        from core.tracker_templates import render_tracker_mail, tracker_mail_context
    except Exception as e:
        logger.warning(f"scheduler imports failed: {e}")
        summary.update({"ok": False, "finished_at": _now_iso(), "last_error": f"import failed: {e}"})
        return _write_status(summary)
    TRACKER_DIR = PATHS.data_root / "tracker"
    issues_fp = TRACKER_DIR / "issues.json"
    if not issues_fp.is_file():
        summary["finished_at"] = _now_iso()
        return _write_status(summary)
    try:
        issues = load_json(issues_fp, [])
        if not isinstance(issues, list):
            summary.update({"ok": False, "finished_at": _now_iso(), "last_error": "issues.json is not a list"})
            return _write_status(summary)
    except Exception as e:
        summary.update({"ok": False, "finished_at": _now_iso(), "last_error": f"issue load failed: {e}"})
        return _write_status(summary)
    changed = False
    cats_cache = None

    def _get_cat_meta(name: str) -> dict:
        nonlocal cats_cache
        if cats_cache is None:
            try:
                raw = load_json(TRACKER_DIR / "categories.json", [])
                cats_cache = {}
                for c in raw if isinstance(raw, list) else []:
                    if isinstance(c, dict) and c.get("name"):
                        cats_cache[c["name"]] = c
            except Exception:
                cats_cache = {}
        return cats_cache.get(name) or {}

    for iss in issues:
        if not isinstance(iss, dict):
            continue
        if iss.get("status") == "closed":
            continue
        lots = iss.get("lots") or []
        if not lots:
            continue
        summary["issues_scanned"] += 1
        cat_meta = _get_cat_meta(iss.get("category") or "")
        source = _category_source(iss.get("category") or "", cat_meta, "auto")
        source_root = source_root_for_context(source, iss.get("category") or "")
        issue_changed = False
        expanded_lots = []
        for raw_lot in lots:
            lot = normalize_lot_row(raw_lot)
            root = (lot.get("root_lot_id") or "").strip()
            lid = (lot.get("lot_id") or "").strip()
            wid = str(lot.get("wafer_id") or "").strip()
            row_root = root
            row_lot = lid
            if row_root and not _is_root_lot_id(row_root):
                if not row_lot:
                    row_lot = row_root
                row_root = ""
            product = (lot.get("product") or lot.get("monitor_prod") or iss.get("product") or "").strip()
            expanded_lots.extend(
                normalize_lot_row(row)
                for row in expand_lot_row_for_wafer_selection(
                    lot,
                    product=product,
                    root_lot_id=row_root,
                    lot_id=row_lot,
                    wafer_id=wid,
                    source=source,
                    source_root=source_root,
                )
            )
        if expanded_lots != lots:
            lots = expanded_lots
            issue_changed = True
        auto_close_step = (cat_meta.get("auto_close_step_id") or "").strip()
        category_mail_group_ids = cat_meta.get("mail_group_ids") or []
        issue_group_ids = iss.get("group_ids") or []
        all_reached = bool(auto_close_step and lots)  # all lots must reach
        next_lots = []
        for i, lot in enumerate(lots):
            original_lot = lot if isinstance(lot, dict) else {}
            lot = normalize_lot_row(original_lot)
            root = (lot.get("root_lot_id") or "").strip()
            lid = (lot.get("lot_id") or "").strip()
            wid = str(lot.get("wafer_id") or "").strip()
            row_root = root
            row_lot = lid
            if row_root and not _is_root_lot_id(row_root):
                if not row_lot:
                    row_lot = row_root
                row_root = ""
            product = (lot.get("product") or lot.get("monitor_prod") or iss.get("product") or "").strip()
            checked_at = _now_iso()
            try:
                snap = lot_step_snapshot(
                    product=product,
                    root_lot_id=row_root,
                    lot_id=row_lot,
                    wafer_id=wid,
                    source=source,
                    source_root=source_root,
                )
            except Exception as e:
                snap = {}
                logger.warning(f"lot_step_snapshot failed issue={iss.get('id')} lot={root or lid}: {e}")
            row_fields = snapshot_row_fields(snap)
            fab = (snap.get("fab") or {})
            row_product = product or str(fab.get("product") or "").strip()
            current_step = row_fields.get("current_step") or ""
            current_function_step = row_fields.get("current_function_step") or ""
            current_step_seq = row_fields.get("current_step_seq")
            if source == "fab":
                et_fields = {
                    "et_measured": None,
                    "et_last_seq": None,
                    "et_last_time": "",
                    "et_last_step": "",
                    "et_last_function_step": "",
                    "et_step_summary": [],
                    "et_step_seq_summary": "",
                    "et_recent_formatted": "",
                }
            else:
                et_fields = {
                    "et_measured": row_fields.get("et_measured"),
                    "et_last_seq": row_fields.get("et_last_seq"),
                    "et_last_time": row_fields.get("et_last_time"),
                    "et_last_step": row_fields.get("et_last_step"),
                    "et_last_function_step": row_fields.get("et_last_function_step"),
                    "et_step_summary": row_fields.get("et_step_summary") or [],
                    "et_step_seq_summary": row_fields.get("et_step_seq_summary") or "",
                    "et_recent_formatted": row_fields.get("et_recent_formatted") or "",
                }
            updated_lot = normalize_lot_row({
                **lot,
                "product": row_product,
                "monitor_prod": row_product,
                "current_step": current_step or None,
                "current_function_step": current_function_step or None,
                "function_step": current_function_step or None,
                "func_step": current_function_step or None,
                "current_step_seq": current_step_seq,
                "step_seq": current_step_seq,
                "last_move_at": row_fields.get("last_move_at") or "",
                "last_checked_at": checked_at,
                "last_scan_source": source,
                "last_scan_source_root": source_root,
                "last_scan_status": "ok" if snap else "no_match",
                **et_fields,
            })
            summary["lots_scanned"] += 1

            fab_step = fab.get("step_id") or ""
            if auto_close_step:
                if not _fab_step_reached(fab_step, auto_close_step):
                    all_reached = False
            else:
                all_reached = False

            watch = updated_lot.get("watch") or {}
            if not watch:
                if updated_lot != original_lot:
                    issue_changed = True
                    summary["lots_updated"] += 1
                next_lots.append(updated_lot)
                continue
            summary["watches_checked"] += 1
            watch_source = _row_source_for_watch(source, watch)
            effective_watch = {**watch, "source": watch_source}
            cmp = compare_to_watch(
                snap,
                effective_watch,
                now_iso=checked_at,
                et_stable_delay_minutes=config["et_stable_delay_minutes"],
            )
            et_count = int(cmp.get("et_count") or 0)
            watch_updates = cmp.get("watch_updates") if isinstance(cmp.get("watch_updates"), dict) else {}
            # last_observed 업데이트
            if fab_step and fab_step != watch.get("last_observed_step"):
                watch["last_observed_step"] = fab_step
                issue_changed = True
            if et_count != int(watch.get("last_observed_et_count") or 0):
                watch["last_observed_et_count"] = et_count
                issue_changed = True
            if watch.get("source") != watch_source:
                watch["source"] = watch_source
                issue_changed = True
            if watch_updates:
                next_watch = {**watch, **watch_updates}
                if next_watch != watch:
                    watch = next_watch
                    issue_changed = True
            updated_lot["watch"] = watch
            if not cmp.get("fire"):
                if updated_lot != original_lot:
                    issue_changed = True
                    summary["lots_updated"] += 1
                next_lots.append(updated_lot)
                continue
            base_targets = set()
            if iss.get("username"):
                base_targets.add(iss["username"])
            if updated_lot.get("username"):
                base_targets.add(updated_lot["username"])
            mail_watch = _issue_mail_watch(iss)
            recipient_groups = _unique_list(list((mail_watch.get("mail_group_ids") or [])))
            notify_recipients = _recipient_payload(base_targets, [])
            targets = notify_recipients.get("users") or []
            body_text = cmp.get("reason") or "lot progress"
            for tgt in targets:
                try:
                    if emit_event(
                        "tracker_step_reached",
                        actor="scheduler",
                        target_user=tgt,
                        title=f"[Lot 진행] {iss.get('title') or iss['id']}",
                        body=f"{body_text} · lot={row_root or row_lot} wf={wid}",
                        payload={
                            "issue_id": iss["id"], "product": row_product,
                            "lot_id": row_lot,
                            "root_lot_id": row_root, "wafer_id": wid,
                            "step_id": cmp.get("new_step_id"),
                            "et_count": et_count, "reason": body_text,
                        },
                    ):
                        summary["notify_count"] += 1
                except Exception as e:
                    logger.warning(f"emit_event failed: {e}")
            if mail_watch.get("enabled"):
                try:
                    from core.mail import send_mail
                    mail_recipients = _recipient_payload(base_targets, recipient_groups)
                    mail_targets = mail_recipients.get("users") or []
                    kind = "analysis" if watch_source == "et" else "monitor"
                    context = tracker_mail_context(
                        kind,
                        iss,
                        product=row_product,
                        lot=row_root or row_lot,
                        root_lot_id=row_root,
                        lot_id=row_lot,
                        wafer_id=wid,
                        step_id=cmp.get("new_step_id") or fab_step or "",
                        target_step_id=watch.get("target_step_id") or "",
                        recent_et=cmp.get("et_recent_formatted") or "-",
                        et_count=et_count,
                        recipient_groups=", ".join(recipient_groups) or "User only",
                        source=source,
                        source_root=source_root,
                        checked_at=checked_at,
                    )
                    rendered = render_tracker_mail(
                        kind,
                        context,
                    )
                    mail_result = send_mail(
                        sender_username="flow-scheduler",
                        receiver_usernames=mail_targets,
                        extra_emails=mail_recipients.get("extra_emails") or [],
                        title=rendered["subject"],
                        content=rendered["body"],
                    )
                    if mail_result.get("ok"):
                        summary["mail_count"] += len(mail_result.get("to") or [])
                except Exception as e:
                    logger.warning(f"send_mail failed: {e}")
            summary["fire_count"] += 1
            issue_changed = True
            if watch_source == "fab" and watch.get("target_step_id"):
                updated_lot["watch"] = _mark_watch_fired(
                    watch,
                    str(watch.get("target_step_id") or "").strip().upper(),
                    str(cmp.get("new_step_id") or fab_step or ""),
                )
            if updated_lot != original_lot:
                summary["lots_updated"] += 1
            next_lots.append(updated_lot)
        if issue_changed:
            iss["lots"] = next_lots
            iss["updated_at"] = _now_iso()
            iss["updated_by"] = "scheduler"
            iss["revision"] = int(iss.get("revision") or 0) + 1
            changed = True
        # auto-close
        if auto_close_step and all_reached and iss.get("status") != "closed":
            iss["status"] = "closed"
            iss["closed_at"] = _now_iso()
            iss["updated_at"] = iss["closed_at"]
            iss["updated_by"] = "scheduler"
            iss["revision"] = int(iss.get("revision") or 0) + 1
            changed = True
            # bell 알림만 (메일 X)
            try:
                tgt = iss.get("username")
                if tgt:
                    emit_event(
                        "tracker_step_reached",
                        actor="scheduler",
                        target_user=tgt,
                        title=f"[이슈 자동 완료] {iss.get('title') or iss['id']}",
                        body=f"모든 wafer 가 {auto_close_step} 이상 도달 → 자동 완료 처리",
                        payload={"issue_id": iss["id"], "reason": f"all wafers passed {auto_close_step}"},
                    )
            except Exception:
                pass
    if changed:
        try:
            save_json(issues_fp, issues, indent=2)
        except Exception as e:
            logger.warning(f"save issues failed: {e}")
            summary["ok"] = False
            summary["last_error"] = f"save issues failed: {e}"
    summary["finished_at"] = _now_iso()
    if summary["fire_count"]:
        logger.info(f"tracker_scheduler: fired={summary['fire_count']}")
    return _write_status(summary)


def run_once(*, force: bool = False) -> dict:
    """Run one tracker scan. Scheduled runs honor the enabled flag; manual runs can force."""
    config = scheduler_config()
    if not force and not config["enabled"]:
        return _write_status({
            "ok": True,
            "enabled": False,
            "interval_minutes": config["interval_minutes"],
            "et_stable_delay_minutes": config["et_stable_delay_minutes"],
            "started_at": "",
            "finished_at": _now_iso(),
            "issues_scanned": 0,
            "lots_scanned": 0,
            "lots_updated": 0,
            "watches_checked": 0,
            "fire_count": 0,
            "notify_count": 0,
            "mail_count": 0,
            "last_error": "",
        })
    if not _scan_lock.acquire(blocking=False):
        return {**scheduler_status()["status"], "ok": False, "running": True, "last_error": "scan already running"}
    result = None
    try:
        result = _scan_once()
    except Exception as e:
        logger.warning(f"tracker scheduler scan failed: {e}")
        result = _write_status({
            "ok": False,
            "enabled": config["enabled"],
            "interval_minutes": config["interval_minutes"],
            "et_stable_delay_minutes": config["et_stable_delay_minutes"],
            "started_at": _now_iso(),
            "finished_at": _now_iso(),
            "issues_scanned": 0,
            "lots_scanned": 0,
            "lots_updated": 0,
            "watches_checked": 0,
            "fire_count": 0,
            "notify_count": 0,
            "mail_count": 0,
            "last_error": str(e),
        })
    finally:
        _scan_lock.release()
    return _write_status({**(result or {}), "running": False})


def _ensure_seed_categories():
    """v9.0.0: 기본 카테고리 Monitor / Analysis seed. 없으면 추가, 있으면 건드리지 않음."""
    try:
        from core.paths import PATHS
        from core.utils import load_json, save_json
    except Exception:
        return
    cats_fp = PATHS.data_root / "tracker" / "categories.json"
    cats_fp.parent.mkdir(parents=True, exist_ok=True)
    cats = load_json(cats_fp, [])
    if not isinstance(cats, list):
        cats = []
    names = {c.get("name") for c in cats if isinstance(c, dict)}
    try:
        from core.lot_step import tracker_role_names_config
        roles = tracker_role_names_config()
    except Exception:
        roles = {"monitor": "Monitor", "analysis": "Analysis"}
    seeds = [
        {"name": roles.get("monitor") or "Monitor", "color": "hsl(210, 58%, 58%)", "source": "fab",
         "max_issues_per_user": 15, "mail_group_ids": [], "auto_close_step_id": ""},
        {"name": roles.get("analysis") or "Analysis", "color": "hsl(330, 58%, 58%)", "source": "et",
         "max_issues_per_user": 15, "mail_group_ids": [], "auto_close_step_id": ""},
    ]
    added = False
    for s in seeds:
        if s["name"] not in names:
            cats.append(s); added = True
    if added:
        save_json(cats_fp, cats)
        logger.info("tracker_scheduler: seeded Monitor/Analysis categories")


def _scheduler_loop():
    # 기동 직후 30초 딜레이 (서버 안정화 시간 확보)
    time.sleep(30)
    while True:
        try:
            run_once()
        except Exception as e:
            logger.warning(f"tracker scheduler tick failed: {e}")
        time.sleep(_interval_seconds())


def start_scheduler() -> bool:
    global _scheduler_thread, _scheduler_started
    if _scheduler_started:
        return False
    try:
        from core.runtime_limits import heavy_background_jobs_enabled
        if not heavy_background_jobs_enabled():
            logger.info("tracker scheduler disabled by resource profile")
            return False
    except Exception:
        pass
    if os.environ.get("FLOW_DISABLE_TRACKER_SCHED") == "1":
        logger.info("tracker scheduler disabled via FLOW_DISABLE_TRACKER_SCHED=1")
        return False
    try:
        _ensure_seed_categories()
    except Exception as e:
        logger.warning(f"category seed failed: {e}")
    t = threading.Thread(target=_scheduler_loop, name="tracker-lot-scheduler", daemon=True)
    t.start()
    _scheduler_thread = t
    _scheduler_started = True
    logger.info(f"tracker scheduler started (interval {_interval_seconds()//60} min)")
    return True
