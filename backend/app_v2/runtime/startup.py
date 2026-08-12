from __future__ import annotations

import datetime
import os

from core.auth import hash_password
from core.runtime_limits import (
    heavy_background_jobs_enabled,
    splittable_match_cache_enabled,
    splittable_product_ram_cache_scheduler_enabled,
    splittable_root_lot_ram_cache_scheduler_enabled,
    tracker_et_lot_cache_enabled,
)

# Startup failures are exposed only through the admin-protected deploy-info.
SCHEDULER_ERRORS: list[dict] = []


def _start_many(starters, logger) -> None:
    for label, module_name, attr_name in starters:
        try:
            module = __import__(module_name, fromlist=[attr_name])
            getattr(module, attr_name)()
        except Exception as exc:
            logger.error(
                "%s init failed: %s: %s; the service remains stopped",
                label, type(exc).__name__, exc,
            )
            SCHEDULER_ERRORS.append(
                {"service": label, "error": f"{type(exc).__name__}: {exc}"}
            )


def start_background_services(logger) -> None:
    """Start local guards and one cross-process owner for shared schedulers."""
    SCHEDULER_ERRORS.clear()

    # These services protect or serve one process and must exist in every
    # process.  They do not own recurring shared-data jobs.
    process_starters = [
        ("worker dispatch", "core.worker_dispatch", "start_services"),
        ("memory watchdog", "core.memory_watchdog", "start_background"),
        # Both starters are role-gated internally and run only on the
        # development worker. Their heavy work shares core.scan_gate with
        # cache builds received through worker_dispatch.
        ("FAB matching alert scanner", "core.fab_matching_alerts", "start_scheduler"),
        ("Auto report history scheduler", "core.auto_report_history", "start_scheduler"),
    ]

    from core.worker_dispatch import (
        external_services_enabled,
        marker_path,
        marker_role,
        role_source,
        server_role,
    )

    try:
        marker = marker_role()
        logger.info(
            "server role at startup: %s (source=%s, %s)",
            server_role(), role_source(),
            str(marker[1]) if marker else f"default api role; worker marker: {marker_path('worker')}",
        )
    except Exception:
        logger.debug("server role log skipped", exc_info=True)

    local_services_enabled = external_services_enabled()
    if local_services_enabled:
        process_starters.extend([
            ("filebrowser cache cleanup", "routers.filebrowser", "cleanup_legacy_cache_roots"),
            ("download queue orphan sweep", "core.download_queue", "start_orphan_sweeper"),
            # These warm process RAM and therefore intentionally run per API
            # process; shared cache creation is owned below.
            ("splittable candidate list prewarmer", "routers.splittable", "start_candidate_list_prewarmer"),
            ("splittable KNOB prewarmer", "routers.splittable", "start_knob_prewarmer"),
        ])
    else:
        logger.info("API-local RAM warmers disabled on worker role")

    if local_services_enabled and splittable_product_ram_cache_scheduler_enabled():
        process_starters.append(
            ("splittable product RAM cache scheduler", "routers.splittable", "start_product_ram_cache_scheduler")
        )
    else:
        logger.info("SplitTable product RAM warmup retired (always disabled)")

    if splittable_root_lot_ram_cache_scheduler_enabled():
        process_starters.append(
            ("splittable root lot RAM cache scheduler", "core.ml_table_lookup", "start_root_lot_ram_cache_scheduler")
        )
    else:
        logger.info("SplitTable root lot RAM warmup retired (always disabled)")

    if local_services_enabled and tracker_et_lot_cache_enabled():
        process_starters.append(
            ("tracker ET lot cache scheduler", "core.lot_step", "start_et_lot_cache_scheduler")
        )
    else:
        logger.info("Tracker ET lot RAM cache scheduler disabled")

    _start_many(process_starters, logger)

    # All recurring work that reads/writes shared files, sends notifications,
    # or builds shared caches is started by exactly one elected process.
    owner_starters = [
        ("filebrowser preview prewarmer", "core.filebrowser_cache_prewarm", "start_prewarmer"),
        ("Inform registration postprocessor", "routers.informs", "start_inform_postprocess_worker"),
        ("splittable product cache rotation", "routers.splittable", "start_split_search_cache_maintainer"),
        ("backup scheduler", "core.backup", "start_scheduler"),
        ("valve watch scheduler", "core.valve_watch", "start_scheduler"),
        ("product dedup scheduler", "scheduler", "start_scheduler"),
        ("S3 ingest scheduler", "routers.s3_ingest", "start_scheduler"),
        ("dashboard chart scheduler", "routers.dashboard", "start_chart_scheduler"),
    ]
    if splittable_match_cache_enabled():
        owner_starters.append(
            ("splittable match cache scheduler", "routers.splittable", "start_match_cache_scheduler")
        )
    else:
        logger.info("SplitTable match cache scheduler disabled")
    if heavy_background_jobs_enabled():
        owner_starters.append(("et tracker scheduler", "core.et_tracker", "start_scheduler"))
    else:
        logger.info("heavy background DB scanners disabled")

    def _start_owned() -> None:
        _start_many(owner_starters, logger)
        try:
            from core.scheduler_health import record_startup, start_monitor
            record_startup(SCHEDULER_ERRORS, logger)
            start_monitor(logger)
        except Exception:
            logger.warning("scheduler health monitor wiring failed", exc_info=True)

    if local_services_enabled:
        try:
            from core.background_owner import start as start_owner_election
            start_owner_election(_start_owned, logger)
        except Exception as exc:
            logger.error("background scheduler owner init failed: %s: %s", type(exc).__name__, exc)
            SCHEDULER_ERRORS.append(
                {"service": "background scheduler owner", "error": f"{type(exc).__name__}: {exc}"}
            )
    else:
        logger.info("shared background owner election disabled on worker role")


def ensure_seed_admin(logger) -> None:
    """Create the local seed admin when it does not exist."""
    from routers.auth import read_users, write_users

    users = read_users()
    if any(user["username"] == "hol" for user in users):
        return

    seed_pw = str(os.environ.get("FLOW_ADMIN_PW") or "")
    if len(seed_pw) < 10 or seed_pw.strip().casefold() in {
        "1111", "1234", "password", "password1", "hol12345!", "change_me",
    }:
        logger.error(
            "Seed admin was not created: set FLOW_ADMIN_PW to an explicit, "
            "non-default password of at least 10 characters"
        )
        return
    users.append({
        "username": "hol",
        "password_hash": hash_password(seed_pw),
        "role": "admin",
        "status": "approved",
        "created": datetime.datetime.now().isoformat(),
        "tabs": "__all__",
    })
    write_users(users)
    logger.info("Admin user 'hol' created from explicit FLOW_ADMIN_PW.")
