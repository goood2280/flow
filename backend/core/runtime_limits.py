"""Runtime resource defaults for small Flow deployments.

The default resource profile is intentionally sized below a 4-core / 16GB test
host. Flow should stay inside roughly 3 CPU cores and 12GB process RSS unless an
operator explicitly opts into a larger profile.

These defaults should run before importing Polars, NumPy, or other native
compute libraries.
"""
from __future__ import annotations

import os
import re


_SMALL_PROFILES = {"", "small", "limited", "test", "default"}
_FULL_PROFILES = {"full", "prod-full", "unlimited"}


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def resource_profile() -> str:
    """Return the configured resource profile name.

    `small` is the default because this app is expected to be usable on a
    4-core / 16GB box without consuming the whole machine.
    """
    return os.environ.get("FLOW_RESOURCE_PROFILE", "small").strip().lower()


def is_small_profile() -> bool:
    return resource_profile() in _SMALL_PROFILES


def cpu_budget_cores() -> float:
    raw = os.environ.get("FLOW_CPU_BUDGET_CORES", "3.3" if is_small_profile() else "")
    try:
        value = float(raw)
    except Exception:
        value = 3.3 if is_small_profile() else float(os.cpu_count() or 1)
    return max(1.0, value)


def process_memory_limit_gb() -> float:
    raw = os.environ.get("FLOW_PROCESS_MEMORY_LIMIT_GB", "12" if is_small_profile() else "0")
    try:
        value = float(raw)
    except Exception:
        value = 12.0 if is_small_profile() else 0.0
    return max(0.0, value)


def heavy_background_jobs_enabled() -> bool:
    """Whether startup may run DB-scanning background jobs.

    Heavy jobs include dashboard chart recompute, Tracker ET cache scans, and
    tracker lot polling. SplitTable match-cache has its own paced scheduler so
    it can run conservatively on small hosts.
    """
    if "FLOW_ENABLE_HEAVY_BACKGROUND_JOBS" in os.environ:
        return _env_flag("FLOW_ENABLE_HEAVY_BACKGROUND_JOBS")
    return resource_profile() in _FULL_PROFILES


def splittable_match_cache_enabled() -> bool:
    """Whether the managed SplitTable FAB match-cache scheduler may run.

    Unlike broad dashboard/tracker scanners, this cache is paced product by
    product so SplitTable can keep its root_lot_id/fab_lot_id lookup warm on
    small servers.
    """
    if "FLOW_ENABLE_SPLITTABLE_MATCH_CACHE" in os.environ:
        return _env_flag("FLOW_ENABLE_SPLITTABLE_MATCH_CACHE")
    if "FLOW_DISABLE_SPLITTABLE_MATCH_CACHE" in os.environ:
        return not _env_flag("FLOW_DISABLE_SPLITTABLE_MATCH_CACHE")
    return True


def tracker_et_lot_cache_enabled() -> bool:
    """Whether Tracker Analysis ET lot-cache jobs may run.

    ET caches are intentionally opt-in for now because ET roots tend to be much
    larger than the FAB lineage data used by SplitTable.
    """
    if "FLOW_ENABLE_TRACKER_ET_LOT_CACHE" in os.environ:
        return _env_flag("FLOW_ENABLE_TRACKER_ET_LOT_CACHE")
    return False


def dashboard_scheduler_enabled() -> bool:
    if "FLOW_ENABLE_DASHBOARD_SCHEDULER" in os.environ:
        return _env_flag("FLOW_ENABLE_DASHBOARD_SCHEDULER")
    return heavy_background_jobs_enabled()


def manual_load_test_enabled() -> bool:
    return _env_flag("FLOW_ENABLE_MANUAL_LOAD_TEST", False)


def _read_proc_status_kb(field: str) -> int:
    try:
        text = open("/proc/self/status", "r", encoding="utf-8").read()
    except Exception:
        return 0
    m = re.search(rf"^{re.escape(field)}:\s+(\d+)\s+kB", text, flags=re.MULTILINE)
    if not m:
        return 0
    try:
        return int(m.group(1))
    except Exception:
        return 0


def process_memory_snapshot() -> dict:
    """Current process memory, with no psutil dependency."""
    rss_gb = 0.0
    vms_gb = 0.0
    try:
        import psutil  # type: ignore

        mi = psutil.Process(os.getpid()).memory_info()
        rss_gb = float(mi.rss) / (1024 ** 3)
        vms_gb = float(mi.vms) / (1024 ** 3)
    except Exception:
        rss_kb = _read_proc_status_kb("VmRSS")
        vms_kb = _read_proc_status_kb("VmSize")
        rss_gb = float(rss_kb) / (1024 ** 2) if rss_kb else 0.0
        vms_gb = float(vms_kb) / (1024 ** 2) if vms_kb else 0.0
    limit_gb = process_memory_limit_gb()
    pct = (rss_gb / limit_gb * 100.0) if limit_gb > 0 else 0.0
    return {
        "process_rss_gb": round(rss_gb, 3),
        "process_vms_gb": round(vms_gb, 3),
        "process_memory_limit_gb": round(limit_gb, 3),
        "process_memory_limit_percent": round(pct, 1),
        "process_memory_over_limit": bool(limit_gb > 0 and rss_gb >= limit_gb),
    }


def process_memory_high(reserve_gb: float = 1.0) -> bool:
    limit = process_memory_limit_gb()
    if limit <= 0:
        return False
    snap = process_memory_snapshot()
    rss = float(snap.get("process_rss_gb") or 0.0)
    return rss >= max(0.0, limit - max(0.0, reserve_gb))


def _default_polars_threads() -> str:
    raw = os.environ.get("FLOW_POLARS_MAX_THREADS", "").strip()
    if raw:
        return raw
    cores = os.cpu_count() or 2
    budget_threads = int(cpu_budget_cores())
    # Keep one core free for uvicorn/event loop/OS. On the default 3.3-core
    # budget this resolves to 3 Polars threads.
    return str(max(1, min(budget_threads, max(1, cores - 1))))


def apply_runtime_limits() -> None:
    """Apply CPU/memory-conscious defaults unless deploy set explicit values."""
    os.environ.setdefault("FLOW_RESOURCE_PROFILE", "small")
    os.environ.setdefault("FLOW_CPU_BUDGET_CORES", "3.3" if is_small_profile() else "")
    os.environ.setdefault("FLOW_PROCESS_MEMORY_LIMIT_GB", "12" if is_small_profile() else "0")
    os.environ.setdefault("POLARS_MAX_THREADS", _default_polars_threads())
    os.environ.setdefault("RAYON_NUM_THREADS", os.environ.get("POLARS_MAX_THREADS", "3"))
    os.environ.setdefault("PYARROW_NUM_THREADS", os.environ.get("POLARS_MAX_THREADS", "3"))
    os.environ.setdefault("WEB_CONCURRENCY", "1")
    os.environ.setdefault("MALLOC_ARENA_MAX", "2")
    for name in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    ):
        flow_name = f"FLOW_{name}"
        os.environ.setdefault(name, os.environ.get(flow_name, "1"))
