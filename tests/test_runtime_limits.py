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

    # 코어 5개 -> 예산 4 (1개는 OS/이벤트 루프 몫), 메모리 16GB -> 80% = 12.8GB.
    assert runtime_limits.effective_cpu_count() == 5
    assert runtime_limits.cpu_budget_cores() == 4.0
    assert runtime_limits.process_memory_limit_gb() == 12.8
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
    # 파일 캐시(inactive_file 10GB + active_file 3GB). 실제 anonymous 메모리는 2GB.
    int_files = {
        "/sys/fs/cgroup/memory.max": 16 * gb,
        "/sys/fs/cgroup/memory.current": 15 * gb,
    }
    stat_fields = {
        ("inactive_file", "/sys/fs/cgroup/memory.stat"): 10 * gb,
        ("active_file", "/sys/fs/cgroup/memory.stat"): 3 * gb,
    }
    monkeypatch.setattr(runtime_limits, "_read_int_file", lambda path: int(int_files.get(path, 0)))
    monkeypatch.setattr(
        runtime_limits,
        "_read_cgroup_stat_field",
        lambda path, field: stat_fields.get((field, path), 0),
    )

    snap = runtime_limits._cgroup_memory_snapshot_bytes()

    assert snap["source"] == "cgroup_v2"
    assert snap["used_raw_bytes"] == float(15 * gb)
    # active_file(3GB) + inactive_file(10GB) = 13GB 전체가 회수 가능.
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
    # 한도 16GB, current 15.5GB(거의 꽉 참)인데 14GB 가 파일 캐시(active 6 + inactive 8).
    int_files = {
        "/sys/fs/cgroup/memory.max": 16 * gb,
        "/sys/fs/cgroup/memory.current": int(15.5 * gb),
    }
    stat_fields = {
        ("inactive_file", "/sys/fs/cgroup/memory.stat"): 8 * gb,
        ("active_file", "/sys/fs/cgroup/memory.stat"): 6 * gb,
    }
    monkeypatch.setattr(runtime_limits, "_read_int_file", lambda path: int(int_files.get(path, 0)))
    monkeypatch.setattr(
        runtime_limits,
        "_read_cgroup_stat_field",
        lambda path, field: stat_fields.get((field, path), 0),
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


def test_over_limit_does_not_block_when_host_memory_is_free(monkeypatch):
    """RSS가 하드 한도를 넘어도 호스트 여유가 충분하면 거절하지 않는다 — 핵심 수정.

    Python/Polars 는 회수 가능한 arena/mmap 페이지로 RSS 가 한도 위에 상주하므로
    RSS 단독은 신뢰할 수 없는 OOM 신호다. 실제 호스트/컨테이너 압박(system_memory_low)
    만 본다. 예전엔 rss>=limit 에서 무조건 True 라 캐시 빌드/조회가 영구 차단(503)됐다.
    """
    monkeypatch.delenv("FLOW_PROCESS_MEMORY_LIMIT_STRICT", raising=False)
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

    assert runtime_limits.process_memory_high(reserve_gb=1.0) is False


def test_over_limit_blocks_when_no_host_memory_signal(monkeypatch):
    """호스트 메모리 신호가 없으면 하드 RSS 한도로 안전하게 폴백한다."""
    monkeypatch.delenv("FLOW_PROCESS_MEMORY_LIMIT_STRICT", raising=False)
    monkeypatch.setenv("FLOW_PROCESS_MEMORY_LIMIT_GB", "10")
    monkeypatch.setattr(
        runtime_limits,
        "process_memory_snapshot",
        lambda: {
            "process_rss_gb": 10.01,
            "system_memory_total_gb": 0.0,
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


def test_read_smaps_rollup_returns_pss_and_uss(monkeypatch, tmp_path):
    """PSS/USS 를 /proc/self/smaps_rollup 에서 파싱한다."""
    content = """00400000-7fffffff ---p 00000000 00:00 0  [rollup]
Rss:              512000 kB
Pss:              256000 kB
Shared_Clean:     100000 kB
Shared_Dirty:      50000 kB
Private_Clean:    150000 kB
Private_Dirty:     62000 kB
"""
    fake_path = tmp_path / "smaps_rollup"
    fake_path.write_text(content)
    orig_open = open

    def fake_open(path, *a, **kw):
        if str(path) == "/proc/self/smaps_rollup":
            return orig_open(str(fake_path), *a, **kw)
        return orig_open(path, *a, **kw)

    monkeypatch.setattr("builtins.open", fake_open)

    result = runtime_limits._read_smaps_rollup()

    assert result["pss_kb"] == 256000
    # USS = Private_Clean + Private_Dirty = 150000 + 62000 = 212000
    assert result["uss_kb"] == 212000


def test_process_memory_snapshot_includes_pss_uss(monkeypatch):
    """process_memory_snapshot 이 PSS/USS 를 포함하고 effective 를 PSS 로 정한다."""
    monkeypatch.setattr(
        runtime_limits,
        "_read_smaps_rollup",
        lambda: {"pss_kb": 2048 * 1024, "uss_kb": 1536 * 1024},  # PSS 2GB, USS 1.5GB
    )
    monkeypatch.setattr(runtime_limits, "_read_proc_status_kb", lambda field: {
        "VmRSS": 3 * 1024 * 1024,  # RSS 3GB
        "VmSize": 8 * 1024 * 1024,
    }.get(field, 0))
    monkeypatch.setattr(runtime_limits, "system_memory_snapshot", lambda **kw: {
        "system_memory_total_gb": 16.0,
        "system_memory_available_gb": 10.0,
        "system_memory_percent": 37.5,
        "system_memory_low": False,
        "system_memory_source": "test",
        "system_memory_raw_total_gb": 16.0,
        "system_memory_cache_reclaimable_gb": 0.0,
        "system_memory_min_available_gb": 2.0,
        "system_memory_guard_percent": 95.0,
    })
    monkeypatch.setattr(runtime_limits, "process_memory_limit_gb", lambda: 10.0)
    # psutil import 실패 시 /proc/self/status 폴백 사용
    monkeypatch.setitem(__import__("sys").modules, "psutil", None)

    snap = runtime_limits.process_memory_snapshot()

    assert snap["process_rss_gb"] == 3.0
    assert snap["process_pss_gb"] == 2.0
    assert snap["process_uss_gb"] == 1.5
    # effective = PSS (가용하므로)
    assert snap["process_memory_effective_gb"] == 2.0
    # limit_percent 는 effective(2GB) / limit(10GB) = 20%
    assert snap["process_memory_limit_percent"] == 20.0


def test_process_memory_high_prefers_effective_over_rss(monkeypatch):
    """process_memory_high 가 RSS 대신 PSS 기반 effective 를 사용한다."""
    monkeypatch.delenv("FLOW_PROCESS_MEMORY_LIMIT_STRICT", raising=False)
    monkeypatch.setenv("FLOW_PROCESS_MEMORY_LIMIT_GB", "10")
    # RSS 가 한도를 넘지만 PSS(effective)는 한도 아래 — 부풀려진 RSS 로 차단하면 안 된다.
    monkeypatch.setattr(
        runtime_limits,
        "process_memory_snapshot",
        lambda: {
            "process_rss_gb": 10.5,
            "process_memory_effective_gb": 6.0,
            "system_memory_total_gb": 128.0,
            "system_memory_low": False,
        },
    )

    assert runtime_limits.process_memory_high(reserve_gb=1.0) is False


def test_cgroup_active_file_also_subtracted(monkeypatch):
    """active_file 도 캐시로 차감되어야 한다 — parquet 스캔 후 과대 표시 방지."""
    gb = 1024 ** 3
    int_files = {
        "/sys/fs/cgroup/memory.max": 16 * gb,
        "/sys/fs/cgroup/memory.current": 14 * gb,
    }
    stat_fields = {
        ("inactive_file", "/sys/fs/cgroup/memory.stat"): 4 * gb,
        ("active_file", "/sys/fs/cgroup/memory.stat"): 6 * gb,
    }
    monkeypatch.setattr(runtime_limits, "_read_int_file", lambda path: int(int_files.get(path, 0)))
    monkeypatch.setattr(
        runtime_limits,
        "_read_cgroup_stat_field",
        lambda path, field: stat_fields.get((field, path), 0),
    )

    snap = runtime_limits._cgroup_memory_snapshot_bytes()

    # 전체 파일 캐시 10GB = inactive 4 + active 6
    assert snap["cache_reclaimable_bytes"] == float(10 * gb)
    # anonymous 메모리 = 14 - 10 = 4GB
    assert snap["used_bytes"] == float(4 * gb)

