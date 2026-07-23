"""Runtime resource defaults for small Flow deployments.

The default resource profile is intentionally bounded for a shared Flow host.
Budgets are derived from the detected host at startup — CPU budget is
(cores - 1) and the process memory limit is ~80% of total memory (cgroup limits
and FLOW_SYSTEM_MEMORY_TOTAL_GB overrides honored) — so the same build adapts
when the machine changes. Operators can still pin exact values with
FLOW_CPU_BUDGET_CORES / FLOW_PROCESS_MEMORY_LIMIT_GB, retune the fraction with
FLOW_PROCESS_MEMORY_LIMIT_FRACTION, or opt into the `full` profile.

Note: above the limit the guard consults real host/container memory pressure
(not RSS alone) unless FLOW_PROCESS_MEMORY_LIMIT_STRICT=1, because Python/Polars
keep reclaimable arenas and mmap'd file pages resident in RSS.

v9.2.1: process memory now reports PSS (Proportional Set Size) and USS
(Unique Set Size) alongside RSS via /proc/self/smaps_rollup. The memory
guard uses the most accurate available metric (PSS > USS > RSS) to avoid
false OOM signals from inflated RSS. Cgroup file cache subtraction now
includes both active_file and inactive_file for more accurate reporting.

These defaults should run before importing Polars, NumPy, or other native
compute libraries.
"""
from __future__ import annotations

import os
import re
import threading
import time


_SMALL_PROFILES = {"", "small", "limited", "test", "default"}
_FULL_PROFILES = {"full", "prod-full", "unlimited"}
_SMALL_PROCESS_MEMORY_LIMIT_GB_DEFAULT = 10.0
# RSS 상한 = 총 메모리 × 이 비율. Python/Polars 는 큰 스캔 후에도 allocator arena·
# mmap 파일 캐시로 RSS 가 높게 유지돼(특히 Windows/비-cgroup 환경에선 회수 가능한
# 파일 캐시까지 RSS 로 계상됨) 0.65 는 10GB 급 호스트에서 상시 상한 초과를 유발했다.
# 0.80 으로 올려 10GB→8.0 / 12GB→9.6 / 16GB→12.8 처럼 환경별로 여유 있게 잡는다.
_PROCESS_MEMORY_LIMIT_FRACTION_DEFAULT = 0.80
# 개발(small) 프로파일 프로세스 메모리 상한 하한(GB). FAB 매칭캐시 등 빌드가 순간
# 수 GB(예: 6GB)를 쓰는데 분수 상한(총량×0.80, 10GB→8GB)이 너무 낮으면 가드가
# 빌드를 조기 throttle 한다. 최소 이 값까지는 상한을 보장한다(총량-예약으로 클램프).
# env FLOW_DEV_PROCESS_MEMORY_LIMIT_GB 로 조절.
_DEV_PROCESS_MEMORY_LIMIT_GB_DEFAULT = 9.5
_CGROUP_MEMORY_UNLIMITED_BYTES = 1 << 60
_PROCESS_CPU_LOCK = threading.Lock()
_PROCESS_CPU_LAST: dict[str, float] = {"cpu_seconds": 0.0, "wall": 0.0}


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _env_float(name: str, default: float, lo: float = 0.0, hi: float = 10_000.0) -> float:
    raw = os.environ.get(name)
    try:
        value = float(raw if raw not in (None, "") else default)
    except Exception:
        value = default
    return max(lo, min(hi, value))


def resource_profile() -> str:
    """Return the configured resource profile name.

    `small` is the default because this app is expected to be usable on a
    shared workstation without consuming the whole machine.
    """
    return os.environ.get("FLOW_RESOURCE_PROFILE", "small").strip().lower()


def is_small_profile() -> bool:
    return resource_profile() in _SMALL_PROFILES


def _read_float_file(path: str) -> float:
    try:
        raw = open(path, "r", encoding="utf-8").read().strip()
    except Exception:
        return 0.0
    try:
        return float(raw)
    except Exception:
        return 0.0


def _cgroup_cpu_quota_cores() -> float:
    try:
        raw = open("/sys/fs/cgroup/cpu.max", "r", encoding="utf-8").read().strip()
        quota_raw, period_raw, *_ = raw.split()
        if quota_raw != "max":
            quota = float(quota_raw)
            period = float(period_raw)
            if quota > 0 and period > 0:
                return quota / period
    except Exception:
        pass
    quota = _read_float_file("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
    period = _read_float_file("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
    if quota > 0 and period > 0:
        return quota / period
    return 0.0


def effective_cpu_count() -> float:
    detected = float(os.cpu_count() or 1)
    quota = _cgroup_cpu_quota_cores()
    if quota > 0:
        detected = min(detected, quota)
    return max(1.0, detected)


def auto_cpu_budget_cores() -> float:
    """Host-proportional CPU budget: leave one core for the OS/event loop.

    Detected at runtime so the same build adapts when the host changes
    (e.g. 4 cores -> budget 3, 8 cores -> budget 7, 2 cores -> budget 1)."""
    return max(1.0, effective_cpu_count() - 1.0)


def process_memory_limit_fraction() -> float:
    """Fraction of total memory used as the process RSS soft limit.

    Default 0.80 (env FLOW_PROCESS_MEMORY_LIMIT_FRACTION). Clamped to a sane
    band so a mis-set value cannot starve the host or disable the guard."""
    return _env_float(
        "FLOW_PROCESS_MEMORY_LIMIT_FRACTION",
        _PROCESS_MEMORY_LIMIT_FRACTION_DEFAULT,
        0.3,
        0.95,
    )


def _dev_process_memory_floor_gb() -> float:
    """개발(small) 프로파일 프로세스 메모리 상한 하한(GB). 0 이면 하한 없음."""
    return _env_float(
        "FLOW_DEV_PROCESS_MEMORY_LIMIT_GB",
        _DEV_PROCESS_MEMORY_LIMIT_GB_DEFAULT,
        0.0,
        512.0,
    )


def auto_process_memory_limit_gb() -> float:
    """Host-proportional RSS limit: ~80% of detected total memory.

    Falls back to the fixed small-profile default when total memory cannot
    be detected. Detection honors cgroup limits and env overrides. The
    fraction is tunable via FLOW_PROCESS_MEMORY_LIMIT_FRACTION.

    개발 하한(_dev_process_memory_floor_gb)을 최소 보장하되, 호스트 총량-0.5GB
    (OS 여유)를 넘지 않도록 클램프한다 — FAB 캐시 등 순간 수 GB 빌드가 분수 상한에
    걸려 조기 throttle 되는 것을 막는다."""
    total_bytes = 0.0
    override = _memory_override_total_bytes()
    if override:
        total_bytes = float(override)
    if not total_bytes:
        limit = _cgroup_memory_snapshot_bytes()
        total_bytes = float(limit.get("total_bytes") or 0.0) if limit else 0.0
    if not total_bytes:
        total_bytes = float(_host_memory_snapshot_bytes().get("total_bytes") or 0.0)
    if total_bytes <= 0:
        return _SMALL_PROCESS_MEMORY_LIMIT_GB_DEFAULT
    total_gb = total_bytes / (1024 ** 3)
    frac_limit = max(2.0, round(total_gb * process_memory_limit_fraction(), 1))
    floor = _dev_process_memory_floor_gb()
    if floor > 0:
        floor = min(floor, round(max(2.0, total_gb - 0.5), 1))   # OS 여유 0.5GB 확보
        return max(frac_limit, floor)
    return frac_limit


def cpu_budget_cores() -> float:
    raw = os.environ.get("FLOW_CPU_BUDGET_CORES", "")
    try:
        value = float(raw)
    except Exception:
        value = auto_cpu_budget_cores() if is_small_profile() else effective_cpu_count()
    if value <= 0:
        value = auto_cpu_budget_cores() if is_small_profile() else effective_cpu_count()
    return max(1.0, min(value, effective_cpu_count()))


def process_memory_limit_gb() -> float:
    raw = os.environ.get("FLOW_PROCESS_MEMORY_LIMIT_GB", "")
    if raw.strip() == "":
        return auto_process_memory_limit_gb() if is_small_profile() else 0.0
    try:
        value = float(raw)
    except Exception:
        value = auto_process_memory_limit_gb() if is_small_profile() else 0.0
    return max(0.0, value)


def heavy_background_jobs_enabled() -> bool:
    """Whether startup may run DB-scanning background jobs.

    Heavy jobs include dashboard chart recompute, Tracker ET cache scans, and
    tracker lot polling. SplitTable match-cache has its own paced scheduler so
    it can run conservatively on small hosts.
    """
    if "FLOW_ENABLE_HEAVY_BACKGROUND_JOBS" in os.environ:
        return _env_flag("FLOW_ENABLE_HEAVY_BACKGROUND_JOBS")
    # worker는 api가 제출한 작업을 소비하는 실행 노드이지 스케줄 소유자가 아니다.
    # 여기서 자체 scheduler까지 켜면 api 스케줄과 중복 실행되고, lookup 빌드와
    # Tracker/ET 스캔이 같은 개발 서버에서 겹쳐 OOM을 유발한다. 명시 env는
    # 비표준 단독-worker 배포를 위해 위에서 계속 우선한다.
    try:
        from core.worker_dispatch import server_role as _server_role
        if _server_role() == "worker":
            return False
    except Exception:
        if os.environ.get("FLOW_SERVER_ROLE", "").strip().lower() == "worker":
            return False
    return resource_profile() in _FULL_PROFILES


def splittable_match_cache_enabled() -> bool:
    """Whether the managed SplitTable FAB match-cache scheduler may run.

    Disabled by default. SplitTable now uses the shared LOT progress latest
    cache for root_lot_id/wafer_id -> lot_id, and the old product match-cache
    builder is kept only as an explicit compatibility path.
    """
    if "FLOW_ENABLE_SPLITTABLE_MATCH_CACHE" in os.environ:
        return _env_flag("FLOW_ENABLE_SPLITTABLE_MATCH_CACHE")
    if "FLOW_DISABLE_SPLITTABLE_MATCH_CACHE" in os.environ:
        return not _env_flag("FLOW_DISABLE_SPLITTABLE_MATCH_CACHE")
    return False


def splittable_product_ram_cache_scheduler_enabled() -> bool:
    """Whether startup should warm SplitTable ML_TABLE product files in RAM."""
    if "FLOW_DISABLE_SPLITTABLE_PRODUCT_RAM_CACHE" in os.environ:
        return not _env_flag("FLOW_DISABLE_SPLITTABLE_PRODUCT_RAM_CACHE")
    if "FLOW_ENABLE_SPLITTABLE_PRODUCT_RAM_CACHE" in os.environ:
        return _env_flag("FLOW_ENABLE_SPLITTABLE_PRODUCT_RAM_CACHE")
    try:
        from core.worker_dispatch import server_role as _server_role
        if _server_role() == "worker":
            return False
    except Exception:
        if os.environ.get("FLOW_SERVER_ROLE", "").strip().lower() == "worker":
            return False
    return resource_profile() in (_FULL_PROFILES | {"prod", "production"})


def splittable_root_lot_ram_cache_scheduler_enabled() -> bool:
    """Whether startup should warm root_lot_id-scoped ML_TABLE RAM partitions."""
    if "FLOW_DISABLE_SPLITTABLE_ROOT_LOT_RAM_CACHE" in os.environ:
        return not _env_flag("FLOW_DISABLE_SPLITTABLE_ROOT_LOT_RAM_CACHE")
    if "FLOW_ENABLE_SPLITTABLE_ROOT_LOT_RAM_CACHE" in os.environ:
        return _env_flag("FLOW_ENABLE_SPLITTABLE_ROOT_LOT_RAM_CACHE")
    try:
        from core.worker_dispatch import server_role as _server_role
        if _server_role() == "worker":
            return False
    except Exception:
        if os.environ.get("FLOW_SERVER_ROLE", "").strip().lower() == "worker":
            return False
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


def _read_meminfo_kb(field: str) -> int:
    try:
        text = open("/proc/meminfo", "r", encoding="utf-8").read()
    except Exception:
        return 0
    m = re.search(rf"^{re.escape(field)}:\s+(\d+)\s+kB", text, flags=re.MULTILINE)
    if not m:
        return 0
    try:
        return int(m.group(1))
    except Exception:
        return 0


def _read_int_file(path: str) -> int:
    try:
        raw = open(path, "r", encoding="utf-8").read().strip()
    except Exception:
        return 0
    if raw.lower() in {"", "max"}:
        return 0
    try:
        value = int(raw)
    except Exception:
        return 0
    if value <= 0 or value >= _CGROUP_MEMORY_UNLIMITED_BYTES:
        return 0
    return value


def _read_cgroup_stat_field(path: str, field: str) -> int:
    """Read one `field value` line from a cgroup memory.stat file."""
    try:
        text = open(path, "r", encoding="utf-8").read()
    except Exception:
        return 0
    m = re.search(rf"^{re.escape(field)}\s+(\d+)\s*$", text, flags=re.MULTILINE)
    if not m:
        return 0
    try:
        return int(m.group(1))
    except Exception:
        return 0


def _cgroup_reclaimable_cache_bytes(source: str) -> int:
    """Reclaimable page cache inside the cgroup.

    cgroup `memory.current` / `memory.usage_in_bytes` count the file page cache,
    which is reclaimable under pressure and does NOT cause OOM. Reading big
    parquet files fills this cache up to the limit, so counting it as "used"
    makes the app report ~100% memory even when real (anonymous) usage is low.
    Both active_file and inactive_file are reclaimable under memory pressure.
    Previous versions only subtracted inactive_file (matching Kubernetes
    working_set), but this still over-reported on parquet-heavy workloads
    where active_file grew large.  We now subtract the full file cache
    (active + inactive) for the display value, while keeping the raw value
    available for comparison with Kubernetes metrics.
    """
    if source == "cgroup_v2":
        inactive = _read_cgroup_stat_field("/sys/fs/cgroup/memory.stat", "inactive_file")
        active = _read_cgroup_stat_field("/sys/fs/cgroup/memory.stat", "active_file")
        return inactive + active
    if source == "cgroup_v1":
        inactive = _read_cgroup_stat_field("/sys/fs/cgroup/memory/memory.stat", "total_inactive_file")
        active = _read_cgroup_stat_field("/sys/fs/cgroup/memory/memory.stat", "total_active_file")
        return inactive + active
    return 0


def _cgroup_memory_snapshot_bytes() -> dict[str, float | str]:
    total = _read_int_file("/sys/fs/cgroup/memory.max")
    used = _read_int_file("/sys/fs/cgroup/memory.current")
    source = "cgroup_v2" if total else ""
    if not total:
        total = _read_int_file("/sys/fs/cgroup/memory/memory.limit_in_bytes")
        used = _read_int_file("/sys/fs/cgroup/memory/memory.usage_in_bytes")
        source = "cgroup_v1" if total else ""
    if not total:
        return {}
    used_raw = max(0, min(int(total), int(used or 0)))
    # 회수 가능한 전체 파일 캐시(active + inactive)를 빼서 실제 anonymous 메모리만 남긴다.
    # parquet 스캔 후 active_file 이 크게 잡혀 memory_used 가 부풀려지는 문제 해결.
    reclaimable = min(used_raw, max(0, _cgroup_reclaimable_cache_bytes(source)))
    used_working = max(0, used_raw - reclaimable)
    return {
        "total_bytes": float(total),
        "used_bytes": float(used_working),
        "used_raw_bytes": float(used_raw),
        "cache_reclaimable_bytes": float(reclaimable),
        "source": source,
    }


def _memory_override_total_bytes() -> int:
    raw = (
        os.environ.get("FLOW_SYSTEM_MEMORY_TOTAL_GB")
        or os.environ.get("FLOW_EFFECTIVE_MEMORY_TOTAL_GB")
        or ""
    )
    try:
        gb = float(raw)
    except Exception:
        return 0
    if gb <= 0:
        return 0
    return int(gb * 1024 * 1024 * 1024)


def _host_memory_snapshot_bytes() -> dict[str, float | str]:
    try:
        import psutil  # type: ignore

        vm = psutil.virtual_memory()
        return {
            "total_bytes": float(vm.total),
            "available_bytes": float(vm.available),
            "percent": float(vm.percent),
            "source": "psutil",
        }
    except Exception:
        total_kb = _read_meminfo_kb("MemTotal")
        avail_kb = _read_meminfo_kb("MemAvailable")
        total_bytes = float(total_kb) * 1024.0 if total_kb else 0.0
        available_bytes = float(avail_kb) * 1024.0 if avail_kb else 0.0
        percent = 0.0
        if total_bytes > 0:
            percent = max(0.0, min(100.0, (1.0 - available_bytes / total_bytes) * 100.0))
        return {
            "total_bytes": total_bytes,
            "available_bytes": available_bytes,
            "percent": percent,
            "source": "proc_meminfo",
        }


def system_memory_snapshot(reserve_gb: float = 1.0) -> dict:
    """Host/container memory state used by the soft guard."""
    host = _host_memory_snapshot_bytes()
    total_bytes = float(host.get("total_bytes") or 0.0)
    available_bytes = float(host.get("available_bytes") or 0.0)
    percent = float(host.get("percent") or 0.0)
    source = str(host.get("source") or "")
    raw_total_bytes = total_bytes

    limit = _cgroup_memory_snapshot_bytes()
    cache_reclaimable_gb = (
        float(limit.get("cache_reclaimable_bytes") or 0.0) / (1024 ** 3) if limit else 0.0
    )
    override_total = _memory_override_total_bytes()
    if override_total:
        limit = {
            "total_bytes": float(override_total),
            "source": "env_override",
        }
        cache_reclaimable_gb = 0.0
    if limit:
        limit_total = float(limit.get("total_bytes") or 0.0)
        if limit_total > 0 and (not total_bytes or limit_total < total_bytes):
            used_bytes = float(limit.get("used_bytes") or 0.0)
            if used_bytes > 0:
                total_bytes = limit_total
                available_bytes = max(0.0, total_bytes - min(total_bytes, used_bytes))
                percent = max(0.0, min(100.0, 100.0 * used_bytes / total_bytes))
            else:
                total_bytes = limit_total
                available_bytes = min(available_bytes, total_bytes) if available_bytes else 0.0
                if total_bytes > 0:
                    percent = max(0.0, min(100.0, (1.0 - available_bytes / total_bytes) * 100.0))
            source = str(limit.get("source") or source)

    total_gb = total_bytes / (1024 ** 3) if total_bytes else 0.0
    available_gb = available_bytes / (1024 ** 3) if available_bytes else 0.0
    min_available = _env_float(
        "FLOW_SYSTEM_MEMORY_MIN_AVAILABLE_GB",
        max(2.0, float(reserve_gb or 0.0)),
        0.0,
        1024.0,
    )
    high_percent = _env_float("FLOW_SYSTEM_MEMORY_GUARD_PERCENT", 95.0, 50.0, 100.0)
    low = bool(
        total_gb > 0
        and (
            available_gb <= min_available
            or (percent > 0 and percent >= high_percent)
        )
    )
    return {
        "system_memory_total_gb": round(total_gb, 3),
        "system_memory_available_gb": round(available_gb, 3),
        "system_memory_percent": round(percent, 1),
        "system_memory_min_available_gb": round(min_available, 3),
        "system_memory_guard_percent": round(high_percent, 1),
        "system_memory_low": low,
        "system_memory_source": source,
        "system_memory_raw_total_gb": round(raw_total_bytes / (1024 ** 3), 3) if raw_total_bytes else 0,
        "system_memory_cache_reclaimable_gb": round(cache_reclaimable_gb, 3),
    }


def _read_smaps_rollup() -> dict[str, float]:
    """Read PSS and Private (USS) from /proc/self/smaps_rollup.

    PSS (Proportional Set Size) divides shared pages proportionally among
    all processes mapping them, giving a fairer picture than RSS.
    USS (Unique Set Size / Private) counts only pages exclusive to this
    process — the memory that would be freed if the process exited.

    Returns {"pss_kb": ..., "uss_kb": ...}. Both 0 on non-Linux / failure.
    """
    pss_kb = 0
    uss_kb = 0
    try:
        text = open("/proc/self/smaps_rollup", "r", encoding="utf-8").read()
        m_pss = re.search(r"^Pss:\s+(\d+)\s+kB", text, flags=re.MULTILINE)
        if m_pss:
            pss_kb = int(m_pss.group(1))
        # Private = Private_Clean + Private_Dirty (= USS).
        priv_clean = 0
        priv_dirty = 0
        m_pc = re.search(r"^Private_Clean:\s+(\d+)\s+kB", text, flags=re.MULTILINE)
        m_pd = re.search(r"^Private_Dirty:\s+(\d+)\s+kB", text, flags=re.MULTILINE)
        if m_pc:
            priv_clean = int(m_pc.group(1))
        if m_pd:
            priv_dirty = int(m_pd.group(1))
        uss_kb = priv_clean + priv_dirty
    except Exception:
        pass
    return {"pss_kb": pss_kb, "uss_kb": uss_kb}


def process_memory_snapshot() -> dict:
    """Current process memory with RSS, PSS, and USS.

    RSS (Resident Set Size) includes shared libraries and memory-mapped files
    and is typically inflated.  PSS divides shared pages proportionally; USS
    counts only private pages.  On Linux ≥4.14 PSS/USS are read from
    /proc/self/smaps_rollup; on other platforms they fall back to 0.
    """
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

    # PSS / USS — more accurate than RSS for process-level memory.
    smaps = _read_smaps_rollup()
    pss_gb = float(smaps.get("pss_kb") or 0) / (1024 ** 2) if smaps.get("pss_kb") else 0.0
    uss_gb = float(smaps.get("uss_kb") or 0) / (1024 ** 2) if smaps.get("uss_kb") else 0.0

    # 대표 프로세스 메모리 = PSS (가용하면) > USS > RSS 순으로 정확도가 높다.
    effective_gb = pss_gb if pss_gb > 0 else (uss_gb if uss_gb > 0 else rss_gb)

    limit_gb = process_memory_limit_gb()
    pct = (effective_gb / limit_gb * 100.0) if limit_gb > 0 else 0.0
    out = {
        "process_rss_gb": round(rss_gb, 3),
        "process_pss_gb": round(pss_gb, 3),
        "process_uss_gb": round(uss_gb, 3),
        "process_memory_effective_gb": round(effective_gb, 3),
        "process_vms_gb": round(vms_gb, 3),
        "process_memory_limit_gb": round(limit_gb, 3),
        "process_memory_limit_percent": round(pct, 1),
        "process_memory_over_limit": bool(limit_gb > 0 and effective_gb >= limit_gb),
    }
    out.update(system_memory_snapshot())
    return out


def _read_process_cpu_seconds() -> float:
    try:
        import psutil  # type: ignore

        times = psutil.Process(os.getpid()).cpu_times()
        return float(getattr(times, "user", 0.0)) + float(getattr(times, "system", 0.0))
    except Exception:
        try:
            times = os.times()
            return float(times.user) + float(times.system)
        except Exception:
            return 0.0


def process_cpu_snapshot(sample_seconds: float = 0.0, guard_cores: float | None = None) -> dict:
    """Current process CPU pressure measured in full-core equivalents."""
    if sample_seconds and sample_seconds > 0:
        time.sleep(max(0.0, min(float(sample_seconds), 1.0)))
    now = time.time()
    cpu_seconds = _read_process_cpu_seconds()
    with _PROCESS_CPU_LOCK:
        prev_cpu = float(_PROCESS_CPU_LAST.get("cpu_seconds") or 0.0)
        prev_wall = float(_PROCESS_CPU_LAST.get("wall") or 0.0)
        _PROCESS_CPU_LAST["cpu_seconds"] = cpu_seconds
        _PROCESS_CPU_LAST["wall"] = now
    percent = 0.0
    if prev_wall > 0 and now > prev_wall and cpu_seconds >= prev_cpu:
        percent = max(0.0, 100.0 * (cpu_seconds - prev_cpu) / (now - prev_wall))
    budget = cpu_budget_cores()
    guard = guard_cores if guard_cores is not None else _env_float("FLOW_PROCESS_CPU_GUARD_CORES", budget, 0.1, 1024.0)
    try:
        guard = float(guard)
    except Exception:
        guard = budget
    cores_used = percent / 100.0
    return {
        "process_cpu_percent": round(percent, 1),
        "process_cpu_cores": round(cores_used, 3),
        "process_cpu_budget_cores": round(budget, 3),
        "process_cpu_guard_cores": round(max(0.1, guard), 3),
        "process_cpu_over_limit": bool(cores_used >= max(0.1, guard)),
        "system_cpu_count": round(effective_cpu_count(), 3),
    }


def process_cpu_high(guard_cores: float | None = None, sample_seconds: float = 0.0) -> bool:
    return bool(process_cpu_snapshot(sample_seconds=sample_seconds, guard_cores=guard_cores).get("process_cpu_over_limit"))


def process_memory_high(reserve_gb: float = 1.0) -> bool:
    limit = process_memory_limit_gb()
    if limit <= 0:
        return False
    snap = process_memory_snapshot()
    # PSS/USS 가 가용하면 더 정확한 effective 값 사용, 없으면 RSS 폴백.
    effective = float(snap.get("process_memory_effective_gb") or 0.0)
    rss = effective if effective > 0 else float(snap.get("process_rss_gb") or 0.0)

    # Below the soft floor (limit - reserve): always plenty of headroom.
    soft_floor = max(0.0, limit - max(0.0, reserve_gb))
    if rss < soft_floor:
        return False

    if _env_flag("FLOW_PROCESS_MEMORY_LIMIT_STRICT", False):
        # Legacy behavior: RSS alone gates — any RSS in or above the soft band
        # (rss >= limit - reserve, already true here) blocks new work.
        return True

    # Non-strict default (every profile): once RSS is near/over the limit it is
    # an UNRELIABLE OOM signal — Python/Polars keep allocator arenas and mmap'd
    # parquet pages resident (on Windows / non-cgroup hosts the reclaimable file
    # cache is counted in RSS too), so a high-but-reclaimable RSS would otherwise
    # trip the guard *permanently*. That death spiral is what starved the
    # ML_TABLE / lookup / RAM cache builders and made every heavy read 503.
    # Gate on the real host/container memory pressure — the true OOM predictor —
    # instead of RSS. Refuse new work only when memory is genuinely tight.
    # Restore the old RSS-only cap with FLOW_PROCESS_MEMORY_LIMIT_STRICT=1.
    if float(snap.get("system_memory_total_gb") or 0.0) > 0:
        return bool(snap.get("system_memory_low"))
    # No host memory signal available: fall back to the hard RSS cap.
    return bool(rss >= limit)


def _polars_threads_for_role() -> int:
    """역할 기반 Polars/rayon 스레드 수 — 프로세스 시작 시 1회 결정.

    Polars 풀은 프로세스 수명 동안 최초 1회만 크기가 정해지고(런타임 변경 불가)
    모든 동시 검색이 이 단일 풀을 공유한다. 그래서 무거운 빌드를 개발서버로
    오프로드하는 api 는 검색 병렬도를 더 주고(4), 수 GB FAB/lookup 빌드를 직접
    도는 worker 는 peak memory를 줄이기 위해 2로 제한한다. standalone/기타는 기존 호스트 비례 자동값. 모두 실제 코어
    수로 상한 클램프한다.

    연결 상태(개발서버 alive 여부)는 런타임에 변하지만 풀은 재크기 불가이므로,
    풀 크기는 '연결 상태' 가 아니라 '역할' 로만 결정한다(사용자 확정 방향)."""
    cores = int(effective_cpu_count())
    try:
        from core import worker_dispatch
        role = worker_dispatch.server_role()
    except Exception:
        role = "standalone"
    if role == "api":
        want = 4
    elif role == "worker":
        want = 2
    else:
        # standalone: 기존 호스트 비례 자동값 (한 코어는 uvicorn/event loop 용으로 남김).
        budget_threads = int(cpu_budget_cores())
        want = max(1, min(budget_threads, max(1, cores - 1)))
    return max(1, min(want, cores))


def _default_polars_threads() -> str:
    raw = os.environ.get("FLOW_POLARS_MAX_THREADS", "").strip()
    if raw:
        # 운영자 명시 핀 — 역할 기반 산출을 건너뛴다 (.env 분리 설정 경로).
        return raw
    return str(_polars_threads_for_role())


def apply_runtime_limits() -> None:
    """Apply CPU/memory-conscious defaults unless deploy set explicit values."""
    os.environ.setdefault("FLOW_RESOURCE_PROFILE", "small")
    # 호스트 크기를 읽어 비례 산출 — 환경이 바뀌어도 (코어/메모리 증감) 재배포 없이 맞춰진다.
    os.environ.setdefault("FLOW_CPU_BUDGET_CORES", str(auto_cpu_budget_cores()) if is_small_profile() else "")
    os.environ.setdefault(
        "FLOW_PROCESS_MEMORY_LIMIT_GB",
        str(auto_process_memory_limit_gb()) if is_small_profile() else "0",
    )
    # STRICT 기본값을 강제로 심지 않는다 — 소프트밴드는 기본적으로 실제 호스트
    # 여유 메모리를 확인한다(위 process_memory_high). 명시적 env "1"만 엄격 모드.
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
