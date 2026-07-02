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


def test_small_profile_memory_guard_blocks_before_ten_gb_by_default(monkeypatch):
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

    assert runtime_limits.process_memory_high(reserve_gb=1.0) is True
