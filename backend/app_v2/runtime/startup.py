from __future__ import annotations

import datetime
import os

from core.auth import hash_password
from core.runtime_limits import (
    heavy_background_jobs_enabled,
    splittable_match_cache_enabled,
    tracker_et_lot_cache_enabled,
)


def start_background_services(logger) -> None:
    """Start optional background schedulers without blocking app startup."""

    light_starters = (
        ("backup scheduler", "core.backup", "start_scheduler"),
        ("valve watch scheduler", "core.valve_watch", "start_scheduler"),
        ("product dedup scheduler", "scheduler", "start_scheduler"),
    )
    heavy_starters = (
        ("tracker scheduler", "core.tracker_scheduler", "start_scheduler"),
    )
    starters = light_starters
    if splittable_match_cache_enabled():
        starters = starters + (
            ("splittable match cache scheduler", "routers.splittable", "start_match_cache_scheduler"),
        )
    else:
        logger.info(
            "SplitTable match cache scheduler disabled "
            "(set FLOW_ENABLE_SPLITTABLE_MATCH_CACHE=1 to enable)"
        )
    if heavy_background_jobs_enabled():
        starters = starters + heavy_starters
    else:
        logger.info(
            "heavy background DB scanners disabled "
            "(set FLOW_ENABLE_HEAVY_BACKGROUND_JOBS=1 to enable)"
        )
    if tracker_et_lot_cache_enabled():
        starters = starters + (
            ("tracker ET lot cache scheduler", "core.lot_step", "start_et_lot_cache_scheduler"),
        )
    else:
        logger.info(
            "Tracker ET lot cache scheduler disabled "
            "(set FLOW_ENABLE_TRACKER_ET_LOT_CACHE=1 to enable)"
        )
    for label, module_name, attr_name in starters:
        try:
            module = __import__(module_name, fromlist=[attr_name])
            getattr(module, attr_name)()
        except Exception as exc:
            logger.warning(f"{label} init failed: {exc}")


def ensure_seed_admin(logger) -> None:
    """Create the local default admin account when no admin exists yet."""

    from routers.auth import read_users, write_users

    users = read_users()
    if any(user["username"] == "hol" for user in users):
        return

    seed_pw = os.environ.get("FLOW_ADMIN_PW")
    if not seed_pw:
        seed_pw = "hol12345!"
        logger.warning(
            "Seed admin password using local default. "
            "Set FLOW_ADMIN_PW env var for production to rotate this."
        )
    users.append(
        {
            "username": "hol",
            "password_hash": hash_password(seed_pw),
            "role": "admin",
            "status": "approved",
            "created": datetime.datetime.now().isoformat(),
            "tabs": "__all__",
        }
    )
    write_users(users)
    logger.info("Admin user 'hol' created (password via env or local default).")
