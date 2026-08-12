"""Development-worker scheduler for rolling Auto report ET history."""
from __future__ import annotations

import logging
import os
import threading
import time

logger = logging.getLogger("flow.auto_report_history")

_STARTED = threading.Event()
_THREAD: threading.Thread | None = None


def interval_sec() -> float:
    try:
        value = float(os.environ.get("FLOW_AUTO_REPORT_HISTORY_INTERVAL_SEC") or 6 * 3600)
    except Exception:
        value = 6 * 3600
    return max(300.0, min(7 * 24 * 3600.0, value))


def _due() -> bool:
    from core.auto_report import history_status

    state = history_status()
    finished = float(state.get("finished_ts") or 0.0)
    return not finished or (time.time() - finished) >= interval_sec()


def request_refresh() -> dict:
    from core import auto_report, scan_gate

    return scan_gate.submit(
        "auto_report_history",
        "Auto report ET history 갱신",
        auto_report.refresh_all_histories,
        source="auto_report_history",
        dedupe_key="auto-report-et-history",
    )


def _loop() -> None:
    while True:
        try:
            from core.worker_dispatch import server_role

            if server_role() == "worker" and _due():
                request_refresh()
        except Exception:
            logger.warning("Auto report history scheduler tick failed", exc_info=True)
        # Wake often enough to notice a live role change without spawning
        # duplicate work; scan_gate dedupe provides the second guard.
        time.sleep(60.0)


def start_scheduler() -> bool:
    global _THREAD
    from core.worker_dispatch import server_role

    if server_role() != "worker":
        logger.info("Auto report history scheduler not started: development worker role required")
        return False
    if _STARTED.is_set() and _THREAD is not None and _THREAD.is_alive():
        return False
    _THREAD = threading.Thread(target=_loop, name="auto-report-history", daemon=True)
    _THREAD.start()
    _STARTED.set()
    logger.info("Auto report ET history scheduler started (interval=%ss)", int(interval_sec()))
    return True
