"""Background daily scheduler for Mapfile traffic light verification.

Runs twice a day (e.g. at 06:00 and 18:00) to scan mapfiles in roots.get_db_root() / 'mapfile',
verifying any newly added or modified files for registered product codes.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading

import core.mapfile_traffic as _mt
import core.mapfile_alerts as _ma
import core.teg_map as _tm

logger = logging.getLogger("flow.mapfile_traffic_sched")

_thread: threading.Thread | None = None
_started = False
_stop = threading.Event()


def run_mapfile_traffic_once(now: dt.datetime | None = None) -> dict:
    """Run verification on any changed or new mapfiles across all products."""
    now = now or dt.datetime.now()
    products = _tm.product_catalog()
    scanned_products = 0
    verified_files = 0
    total_files = 0
    alert_published = 0
    alert_duplicates = 0
    alert_suppressed = 0
    alert_failed = 0
    alert_warnings: list[str] = []

    for prod in products:
        vehicle = str(prod.get("vehicle") or "").strip()
        code = str(prod.get("product_code") or "").strip()
        if not vehicle or not code:
            continue
        scanned_products += 1
        try:
            res = _mt.inspect_mapfiles_for_product(vehicle, force=False)
            files = res.get("files") or []
            total_files += len(files)
            # count files that were newly verified (not cached)
            verified_files += sum(1 for f in files if not f.get("is_cached"))
        except Exception as exc:
            logger.warning("Mapfile traffic check failed for vehicle %s (%s): %s", vehicle, code, exc)
            continue
        try:
            alert_result = _ma.publish_mapfile_alerts(res)
            alert_published += int(alert_result.get("published") or 0)
            alert_duplicates += int(alert_result.get("duplicates") or 0)
            alert_suppressed += int(alert_result.get("suppressed") or 0)
            alert_failed += int(alert_result.get("failed") or 0)
            warning = str(alert_result.get("warning") or "").strip()
            if warning and warning not in alert_warnings:
                alert_warnings.append(warning)
        except Exception as exc:
            alert_failed += 1
            warning = f"{vehicle} TEG_CHECK 알림 연결 실패. 다음 검사에서 재시도합니다."
            if warning not in alert_warnings:
                alert_warnings.append(warning)
            logger.warning("Mapfile alert publish failed for vehicle %s (%s): %s", vehicle, code, exc)

    stamp = now.isoformat(timespec="seconds")
    msg = (f"[mapfile_traffic_sched] ran_at={stamp} products={scanned_products} "
           f"total_files={total_files} newly_verified={verified_files}")
    logger.info(msg)
    return {
        "ran_at": stamp,
        "scanned_products": scanned_products,
        "total_files": total_files,
        "newly_verified": verified_files,
        "alert_delivery": {
            "published": alert_published,
            "duplicates": alert_duplicates,
            "suppressed": alert_suppressed,
            "failed": alert_failed,
            "retry_required": bool(alert_warnings or alert_failed),
            "warnings": alert_warnings,
        },
    }


def _seconds_until_next_run(now: dt.datetime | None = None) -> float:
    """Calculate seconds until next 06:00 or 18:00 slot (twice daily)."""
    now = now or dt.datetime.now()
    today_6am = now.replace(hour=6, minute=0, second=0, microsecond=0)
    today_6pm = now.replace(hour=18, minute=0, second=0, microsecond=0)
    tomorrow_6am = today_6am + dt.timedelta(days=1)

    if now < today_6am:
        target = today_6am
    elif now < today_6pm:
        target = today_6pm
    else:
        target = tomorrow_6am

    return max(1.0, (target - now).total_seconds())


def _loop() -> None:
    while not _stop.is_set():
        wait_s = _seconds_until_next_run()
        while wait_s > 0 and not _stop.is_set():
            step = min(wait_s, 60.0)
            _stop.wait(step)
            wait_s -= step
        if _stop.is_set():
            break
        from core.background_owner import is_owner
        if not is_owner():
            continue
        try:
            run_mapfile_traffic_once()
        except Exception as exc:
            logger.warning("Mapfile traffic scheduler tick failed: %s", exc)


def start_scheduler() -> bool:
    global _thread, _started
    if _started:
        return False
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="mapfile-traffic-scheduler", daemon=True)
    _thread.start()
    _started = True
    logger.info("mapfile traffic scheduler started (06:00, 18:00 daily; file changes only)")
    return True
