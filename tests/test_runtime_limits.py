from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core import runtime_limits  # noqa: E402


def test_small_profile_defaults_to_two_polars_threads_on_five_core_host(monkeypatch):
    monkeypatch.setenv("FLOW_RESOURCE_PROFILE", "small")
    monkeypatch.delenv("FLOW_CPU_BUDGET_CORES", raising=False)
    monkeypatch.delenv("FLOW_POLARS_MAX_THREADS", raising=False)
    monkeypatch.setattr(runtime_limits.os, "cpu_count", lambda: 5)
    monkeypatch.setattr(runtime_limits, "_cgroup_cpu_quota_cores", lambda: 0.0)

    assert runtime_limits.effective_cpu_count() == 5
    assert runtime_limits.cpu_budget_cores() == 2.0
    assert runtime_limits._default_polars_threads() == "2"


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
