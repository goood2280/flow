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

    core_starters = (
        # 워커 오프로드 배선을 가장 먼저 — worker 역할이면 heartbeat/큐 소비를
        # 즉시 시작해 api 서버가 최대한 빨리 오프로드를 재개할 수 있게 한다.
        ("worker dispatch", "core.worker_dispatch", "start_services"),
        # OOM 방어 — 개발서버는 자동 재시작이 없어 죽기 전에 캐시를 비우는 게
        # 유일한 방어선. 모든 역할에서 켠다.
        ("memory watchdog", "core.memory_watchdog", "start_background"),
    )
    # 프로세스 로컬 캐시/재검증 서비스. worker는 공유 파일 산출만 담당하므로
    # 이런 캐시를 따로 보유하거나 api와 같은 revalidator를 중복 실행하지 않는다.
    local_service_starters = (
        ("filebrowser cache cleanup", "routers.filebrowser", "cleanup_legacy_cache_roots"),
        ("filebrowser preview prewarmer", "core.filebrowser_cache_prewarm", "start_prewarmer"),
        ("splittable search cache maintainer", "routers.splittable", "start_split_search_cache_maintainer"),
        ("splittable fab lot index revalidator", "routers.splittable", "start_fab_lot_index_revalidator"),
        # 우선 lot × KNOB view payload 프리워밍 — 가장 흔한 검색을 cold 계산 없이
        # 캐시 HIT 로 만든다(HIT 는 cold 레인에 줄서지 않아 동시 대기도 함께 준다).
        ("splittable KNOB prewarmer", "routers.splittable", "start_knob_prewarmer"),
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
        # v9.5.13: ET Tracker 일일 스캔 (하루 n회 지정 시각에 ET DB PGM(pt) 감지)
        ("et tracker scheduler", "core.et_tracker", "start_scheduler"),
    )
    starters = core_starters
    from core.worker_dispatch import external_services_enabled
    local_services_enabled = external_services_enabled()
    if local_services_enabled:
        starters = starters + local_service_starters + external_starters
    else:
        logger.info(
            "local cache/revalidator and external schedulers disabled on worker role "
            "(the api server owns RAM caches and schedules shared-file work)"
        )
    if local_services_enabled and splittable_match_cache_enabled():
        starters = starters + (
            ("splittable match cache scheduler", "routers.splittable", "start_match_cache_scheduler"),
        )
    else:
        logger.info(
            "SplitTable match cache scheduler disabled "
            "(set FLOW_ENABLE_SPLITTABLE_MATCH_CACHE=1 to enable)"
        )
    if local_services_enabled and splittable_product_ram_cache_scheduler_enabled():
        starters = starters + (
            ("splittable product RAM cache scheduler", "routers.splittable", "start_product_ram_cache_scheduler"),
        )
    else:
        logger.info(
            "SplitTable product RAM cache scheduler disabled "
            "(set FLOW_ENABLE_SPLITTABLE_PRODUCT_RAM_CACHE=1 to enable)"
        )
    if local_services_enabled and splittable_root_lot_ram_cache_scheduler_enabled():
        starters = starters + (
            ("splittable root lot RAM cache scheduler", "core.ml_table_lookup", "start_root_lot_ram_cache_scheduler"),
        )
    else:
        logger.info(
            "SplitTable root lot RAM cache scheduler disabled "
            "(set FLOW_ENABLE_SPLITTABLE_ROOT_LOT_RAM_CACHE=1 to enable)"
        )
    if local_services_enabled and heavy_background_jobs_enabled():
        starters = starters + heavy_starters
    else:
        logger.info(
            "heavy background DB scanners disabled "
            "(set FLOW_ENABLE_HEAVY_BACKGROUND_JOBS=1 to enable)"
        )
    if local_services_enabled and tracker_et_lot_cache_enabled():
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
