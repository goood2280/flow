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


def start_background_services(logger) -> None:
    """Start optional background schedulers without blocking app startup."""

    light_starters = (
        # 워커 오프로드 배선을 가장 먼저 — worker 역할이면 heartbeat/큐 소비를
        # 즉시 시작해 api 서버가 최대한 빨리 오프로드를 재개할 수 있게 한다.
        ("worker dispatch", "core.worker_dispatch", "start_services"),
        # OOM 방어 — 개발서버는 자동 재시작이 없어 죽기 전에 캐시를 비우는 게
        # 유일한 방어선. 모든 역할에서 켠다.
        ("memory watchdog", "core.memory_watchdog", "start_background"),
        ("filebrowser cache cleanup", "routers.filebrowser", "cleanup_legacy_cache_roots"),
        ("filebrowser preview prewarmer", "core.filebrowser_cache_prewarm", "start_prewarmer"),
        ("splittable fab lot index revalidator", "routers.splittable", "start_fab_lot_index_revalidator"),
    )
    # 외부 서비스 연동/운영성 스케줄러 — 운영(api)·standalone 전용.
    # worker(개발서버) 역할은 로드 분산(SplitTable·데이터 처리)만 담당하므로
    # 백업 zip, valve 알람 폴링(메일/알림 발송), dedup 스케줄을 띄우지 않는다.
    # 역할 판정은 startup 1회 — 관리자 탭에서 역할을 바꾸면 재시작 후 완전 적용
    # (worker_dispatch 루프들과 달리 이 스케줄러들은 시작 후 역할을 재확인하지
    # 않는 기존 모듈들이라 시작 자체를 막는다).
    external_starters = (
        ("backup scheduler", "core.backup", "start_scheduler"),
        ("valve watch scheduler", "core.valve_watch", "start_scheduler"),
        ("valve alerts scheduler", "core.valve_alerts", "start_scheduler"),
        ("product dedup scheduler", "scheduler", "start_scheduler"),
    )
    heavy_starters = (
        ("tracker scheduler", "core.tracker_scheduler", "start_scheduler"),
    )
    starters = light_starters
    from core.worker_dispatch import external_services_enabled
    if external_services_enabled():
        starters = starters + external_starters
    else:
        logger.info(
            "external-service schedulers disabled on worker role "
            "(backup/valve watch/valve alerts/dedup run on the api server)"
        )
    if splittable_match_cache_enabled():
        starters = starters + (
            ("splittable match cache scheduler", "routers.splittable", "start_match_cache_scheduler"),
        )
    else:
        logger.info(
            "SplitTable match cache scheduler disabled "
            "(set FLOW_ENABLE_SPLITTABLE_MATCH_CACHE=1 to enable)"
        )
    if splittable_product_ram_cache_scheduler_enabled():
        starters = starters + (
            ("splittable product RAM cache scheduler", "routers.splittable", "start_product_ram_cache_scheduler"),
        )
    else:
        logger.info(
            "SplitTable product RAM cache scheduler disabled "
            "(set FLOW_ENABLE_SPLITTABLE_PRODUCT_RAM_CACHE=1 to enable)"
        )
    if splittable_root_lot_ram_cache_scheduler_enabled():
        starters = starters + (
            ("splittable root lot RAM cache scheduler", "core.ml_table_lookup", "start_root_lot_ram_cache_scheduler"),
        )
    else:
        logger.info(
            "SplitTable root lot RAM cache scheduler disabled "
            "(set FLOW_ENABLE_SPLITTABLE_ROOT_LOT_RAM_CACHE=1 to enable)"
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
