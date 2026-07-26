"""Lightweight daily product dedup scheduler."""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time

from core.paths import PATHS
from core.product_dedup import normalize_products
from core.utils import append_text_line, load_json, save_json
from app_v2.modules.splittable.cache_builder import build_pivoted_cache_for_product

logger = logging.getLogger("flow.product_dedup_sched")

_thread: threading.Thread | None = None
_started = False
_stop = threading.Event()
_LOG_FILE = PATHS.data_root / "logs" / "product_dedup_scheduler.log"
_CONFIG_FILE = PATHS.data_root / "informs" / "config.json"


def _append_log(message: str) -> None:
    append_text_line(_LOG_FILE, message.rstrip())


def run_product_dedup_once(now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now()
    cfg = load_json(_CONFIG_FILE, {})
    if not isinstance(cfg, dict):
        cfg = {}
        
    before = list(cfg.get("products") or [])
    after = normalize_products(before)
    changed = before != after
    
    if changed:
        cfg["products"] = after
        save_json(_CONFIG_FILE, cfg, indent=2)

    # v9.1: Build pre-pivoted SplitTable cache for fast instantaneous loading.
    # Base 루트(ML_TABLE 기본 위치)와 DB 루트를 모두 스캔하고, 캐시 디렉터리는
    # canonical ML_TABLE_* 이름으로 통일한다 (view fast path와 동일 규칙).
    scan_roots = []
    for attr in ("base_root", "db_root"):
        root = getattr(PATHS, attr, None)
        if root and root.exists() and root not in scan_roots:
            scan_roots.append(root)
    seen_products = set()
    for root in scan_roots:
        for fp in root.glob("ML_TABLE_*.parquet"):
            product_name = fp.stem
            if product_name.upper() in seen_products:
                continue
            seen_products.add(product_name.upper())
            logger.info("Scheduler: Building pivoted cache for %s", product_name)
            build_pivoted_cache_for_product(product_name, product_path=fp)
            # 다른 워커(API 응답)가 스플릿 테이블 검색에 즉각 반응할 수 있도록
            # 제품 단위 처리가 끝날 때마다 잠시 대기합니다.
            time.sleep(0.5)
            
    stamp = now.isoformat(timespec="seconds")
    message = f"{stamp} cron=03:00 before={len(before)} after={len(after)} changed={str(changed).lower()}"
    logger.info(message)
    _append_log(message)
    return {"before": len(before), "after": len(after), "changed": changed, "ran_at": stamp}


def _seconds_until_next_run(now: dt.datetime | None = None) -> float:
    now = now or dt.datetime.now()
    target = now.replace(hour=3, minute=0, second=0, microsecond=0)
    if target <= now:
      target += dt.timedelta(days=1)
    return max(1.0, (target - now).total_seconds())


def _loop() -> None:
    while not _stop.is_set():
        wait_s = _seconds_until_next_run()
        while wait_s > 0 and not _stop.is_set():
            step = min(wait_s, 60)
            _stop.wait(step)
            wait_s -= step
        if _stop.is_set():
            break
        try:
            run_product_dedup_once()
        except Exception as exc:
            logger.warning(f"product dedup scheduler tick failed: {exc}")


def start_scheduler() -> bool:
    global _thread, _started
    if _started:
        return False
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="product-dedup-scheduler", daemon=True)
    _thread.start()
    _started = True
    logger.info("product dedup scheduler started (03:00 daily)")
    return True
