from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core import runtime_limits  # noqa: E402


def test_small_profile_derives_budget_from_detected_host(monkeypatch):
    gb = 1024 ** 3
    monkeypatch.setenv("FLOW_RESOURCE_PROFILE", "small")
    monkeypatch.delenv("FLOW_CPU_BUDGET_CORES", raising=False)
    monkeypatch.delenv("FLOW_PROCESS_MEMORY_LIMIT_GB", raising=False)
    monkeypatch.delenv("FLOW_POLARS_MAX_THREADS", raising=False)
    monkeypatch.delenv("FLOW_SYSTEM_MEMORY_TOTAL_GB", raising=False)
    monkeypatch.delenv("FLOW_EFFECTIVE_MEMORY_TOTAL_GB", raising=False)
    monkeypatch.setattr(runtime_limits.os, "cpu_count", lambda: 5)
    monkeypatch.setattr(runtime_limits, "_cgroup_cpu_quota_cores", lambda: 0.0)
    monkeypatch.setattr(runtime_limits, "_cgroup_memory_snapshot_bytes", lambda: {})
    monkeypatch.setattr(
        runtime_limits,
        "_host_memory_snapshot_bytes",
        lambda: {
            "total_bytes": float(16 * gb),
            "available_bytes": float(12 * gb),
            "percent": 25.0,
            "source": "psutil",
        },
    )

    # 코어 5개 -> 예산 4 (1개는 OS/이벤트 루프 몫), 메모리 16GB -> 65% = 10.4GB.
    assert runtime_limits.effective_cpu_count() == 5
    assert runtime_limits.cpu_budget_cores() == 4.0
    assert runtime_limits.process_memory_limit_gb() == 10.4
    assert runtime_limits._default_polars_threads() == "4"


def test_small_profile_memory_falls_back_when_host_unknown(monkeypatch):
    monkeypatch.setenv("FLOW_RESOURCE_PROFILE", "small")
    monkeypatch.delenv("FLOW_PROCESS_MEMORY_LIMIT_GB", raising=False)
    monkeypatch.delenv("FLOW_SYSTEM_MEMORY_TOTAL_GB", raising=False)
    monkeypatch.delenv("FLOW_EFFECTIVE_MEMORY_TOTAL_GB", raising=False)
    monkeypatch.setattr(runtime_limits, "_cgroup_memory_snapshot_bytes", lambda: {})
    monkeypatch.setattr(runtime_limits, "_host_memory_snapshot_bytes", lambda: {"total_bytes": 0.0})

    assert runtime_limits.process_memory_limit_gb() == 10.0


def test_explicit_env_overrides_auto_budget(monkeypatch):
    monkeypatch.setenv("FLOW_RESOURCE_PROFILE", "small")
    monkeypatch.setenv("FLOW_CPU_BUDGET_CORES", "2")
    monkeypatch.setenv("FLOW_PROCESS_MEMORY_LIMIT_GB", "6")
    monkeypatch.setattr(runtime_limits.os, "cpu_count", lambda: 8)
    monkeypatch.setattr(runtime_limits, "_cgroup_cpu_quota_cores", lambda: 0.0)

    assert runtime_limits.cpu_budget_cores() == 2.0
    assert runtime_limits.process_memory_limit_gb() == 6.0


def test_system_memory_snapshot_prefers_lower_cgroup_limit(monkeypatch):
    gb = 1024 ** 3
    monkeypatch.delenv("FLOW_SYSTEM_MEMORY_TOTAL_GB", raising=False)
    monkeypatch.delenv("FLOW_EFFECTIVE_MEMORY_TOTAL_GB", raising=False)
    monkeypatch.setattr(
        runtime_limits,
        "_host_memory_snapshot_bytes",
        lambda: {
            "total_bytes": float(134 * gb),
            "available_bytes": float(120 * gb),
            "percent": 10.4,
            "source": "psutil",
        },
    )
    monkeypatch.setattr(
        runtime_limits,
        "_cgroup_memory_snapshot_bytes",
        lambda: {
            "total_bytes": float(16 * gb),
            "used_bytes": float(6 * gb),
            "source": "cgroup_v2",
        },
    )

    snap = runtime_limits.system_memory_snapshot()

    assert snap["system_memory_total_gb"] == 16
    assert snap["system_memory_available_gb"] == 10
    assert snap["system_memory_percent"] == 37.5
    assert snap["system_memory_raw_total_gb"] == 134
    assert snap["system_memory_source"] == "cgroup_v2"


def test_cgroup_used_excludes_reclaimable_file_cache(monkeypatch):
    gb = 1024 ** 3
    # cgroup v2: 한도 16GB, memory.current 15GB 이지만 그 중 13GB 는 회수 가능한
    # 파일 캐시(inactive_file). 실제 working set 은 2GB 여야 한다.
    int_files = {
        "/sys/fs/cgroup/memory.max": 16 * gb,
        "/sys/fs/cgroup/memory.current": 15 * gb,
    }
    monkeypatch.setattr(runtime_limits, "_read_int_file", lambda path: int(int_files.get(path, 0)))
    monkeypatch.setattr(
        runtime_limits,
        "_read_cgroup_stat_field",
        lambda path, field: 13 * gb if (path.endswith("memory.stat") and field == "inactive_file") else 0,
    )

    snap = runtime_limits._cgroup_memory_snapshot_bytes()

    assert snap["source"] == "cgroup_v2"
    assert snap["used_raw_bytes"] == float(15 * gb)
    assert snap["cache_reclaimable_bytes"] == float(13 * gb)
    # 캐시 제외 후 실제 사용 = 15 - 13 = 2GB.
    assert snap["used_bytes"] == float(2 * gb)


def test_system_memory_percent_reflects_working_set_not_cache(monkeypatch):
    gb = 1024 ** 3
    monkeypatch.delenv("FLOW_SYSTEM_MEMORY_TOTAL_GB", raising=False)
    monkeypatch.delenv("FLOW_EFFECTIVE_MEMORY_TOTAL_GB", raising=False)
    monkeypatch.setattr(
        runtime_limits,
        "_host_memory_snapshot_bytes",
        lambda: {"total_bytes": float(64 * gb), "available_bytes": float(40 * gb), "percent": 37.5, "source": "psutil"},
    )
    # 한도 16GB, current 15.5GB(거의 꽉 참)인데 14GB 가 파일 캐시.
    int_files = {
        "/sys/fs/cgroup/memory.max": 16 * gb,
        "/sys/fs/cgroup/memory.current": int(15.5 * gb),
    }
    monkeypatch.setattr(runtime_limits, "_read_int_file", lambda path: int(int_files.get(path, 0)))
    monkeypatch.setattr(
        runtime_limits,
        "_read_cgroup_stat_field",
        lambda path, field: 14 * gb if field == "inactive_file" else 0,
    )

    snap = runtime_limits.system_memory_snapshot()

    # working set = 1.5GB / 16GB ≈ 9.4% — 캐시 팽창(≈97%)로 오표시되지 않는다.
    assert snap["system_memory_source"] == "cgroup_v2"
    assert snap["system_memory_percent"] < 15.0
    assert snap["system_memory_cache_reclaimable_gb"] == 14.0
    assert snap["system_memory_low"] is False


def test_process_cpu_snapshot_flags_core_budget_overage(monkeypatch):
    with runtime_limits._PROCESS_CPU_LOCK:
        runtime_limits._PROCESS_CPU_LAST.update({"cpu_seconds": 0.0, "wall": 0.0})
    cpu_samples = iter([0.0, 2.5])
    wall_samples = iter([100.0, 101.0])
    monkeypatch.setattr(runtime_limits, "_read_process_cpu_seconds", lambda: next(cpu_samples))
    monkeypatch.setattr(runtime_limits.time, "time", lambda: next(wall_samples))
    monkeypatch.setenv("FLOW_CPU_BUDGET_CORES", "2")

    runtime_limits.process_cpu_snapshot(guard_cores=2.0)
    snap = runtime_limits.process_cpu_snapshot(guard_cores=2.0)

    assert snap["process_cpu_cores"] == 2.5
    assert snap["process_cpu_over_limit"] is True


def test_process_memory_high_blocks_at_hard_process_limit(monkeypatch):
    monkeypatch.setenv("FLOW_PROCESS_MEMORY_LIMIT_GB", "10")
    monkeypatch.setattr(
        runtime_limits,
        "process_memory_snapshot",
        lambda: {
            "process_rss_gb": 10.01,
            "system_memory_total_gb": 128.0,
            "system_memory_low": False,
        },
    )

    assert runtime_limits.process_memory_high(reserve_gb=1.0) is True


def test_soft_band_passes_when_host_memory_is_actually_free(monkeypatch):
    """소프트밴드(RSS 잔류)에서는 실제 호스트 여유 메모리를 본다 — 기본 정책.

    Polars 스캔 후 RSS가 limit 근처에 남아 있어도 호스트 여유가 충분하면
    사용자 조회/다운로드와 백그라운드 빌드를 거절하지 않는다.
    """
    monkeypatch.setenv("FLOW_RESOURCE_PROFILE", "small")
    monkeypatch.setenv("FLOW_PROCESS_MEMORY_LIMIT_GB", "10")
    monkeypatch.delenv("FLOW_PROCESS_MEMORY_LIMIT_STRICT", raising=False)
    monkeypatch.setattr(
        runtime_limits,
        "process_memory_snapshot",
        lambda: {
            "process_rss_gb": 9.1,
            "system_memory_total_gb": 128.0,
            "system_memory_low": False,
        },
    )

    assert runtime_limits.process_memory_high(reserve_gb=1.0) is False


def test_soft_band_blocks_when_host_memory_is_genuinely_low(monkeypatch):
    monkeypatch.setenv("FLOW_RESOURCE_PROFILE", "small")
    monkeypatch.setenv("FLOW_PROCESS_MEMORY_LIMIT_GB", "10")
    monkeypatch.delenv("FLOW_PROCESS_MEMORY_LIMIT_STRICT", raising=False)
    monkeypatch.setattr(
        runtime_limits,
        "process_memory_snapshot",
        lambda: {
            "process_rss_gb": 9.1,
            "system_memory_total_gb": 16.0,
            "system_memory_low": True,
        },
    )

    assert runtime_limits.process_memory_high(reserve_gb=1.0) is True


def test_soft_band_blocks_when_strict_env_explicitly_set(monkeypatch):
    monkeypatch.setenv("FLOW_RESOURCE_PROFILE", "small")
    monkeypatch.setenv("FLOW_PROCESS_MEMORY_LIMIT_GB", "10")
    monkeypatch.setenv("FLOW_PROCESS_MEMORY_LIMIT_STRICT", "1")
    monkeypatch.setattr(
        runtime_limits,
        "process_memory_snapshot",
        lambda: {
            "process_rss_gb": 9.1,
            "system_memory_total_gb": 128.0,
            "system_memory_low": False,
        },
    )

    assert runtime_limits.process_memory_high(reserve_gb=1.0) is True
