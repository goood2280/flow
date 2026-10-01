import os
import types

import pytest

from core import runtime_limits


def _host(total_gb=128.0, available_gb=60.0):
    return lambda: {"total_bytes": total_gb * 2 ** 30, "available_bytes": available_gb * 2 ** 30,
                    "percent": 100 * (1 - available_gb / total_gb), "source": "test"}


@pytest.fixture(autouse=True)
def _plain_host(monkeypatch):
    monkeypatch.setattr(runtime_limits, "_host_memory_snapshot_bytes", _host())
    monkeypatch.setattr(runtime_limits, "_cgroup_memory_snapshot_bytes", lambda: {})
    monkeypatch.setattr(runtime_limits, "_memory_override_total_bytes", lambda: 0)
    monkeypatch.delenv("FLOW_SYSTEM_COMMIT_GUARD_PERCENT", raising=False)
    monkeypatch.delenv("FLOW_SYSTEM_MEMORY_MIN_AVAILABLE_GB", raising=False)


def test_commit_guard_is_opt_in(monkeypatch):
    monkeypatch.setattr(runtime_limits, "_windows_commit_bytes",
                        lambda: {"commit_total_bytes": 99 * 2 ** 30, "commit_limit_bytes": 100 * 2 ** 30})
    snap = runtime_limits.system_memory_snapshot()
    assert snap["system_commit_percent"] == 99.0
    # 시스템 관리 pagefile 은 스스로 늘어나므로 기본은 판정에 쓰지 않는다.
    assert snap["system_memory_low"] is False
    monkeypatch.setenv("FLOW_SYSTEM_COMMIT_GUARD_PERCENT", "95")
    assert runtime_limits.system_memory_snapshot()["system_memory_low"] is True


def test_small_commit_limit_is_warned_at_startup(monkeypatch):
    monkeypatch.setattr(runtime_limits, "_windows_commit_bytes",
                        lambda: {"commit_total_bytes": 40 * 2 ** 30, "commit_limit_bytes": 130 * 2 ** 30})
    _info, warnings = runtime_limits.host_diagnostics()
    assert any("커밋 한도" in w for w in warnings)
    monkeypatch.setattr(runtime_limits, "_windows_commit_bytes",
                        lambda: {"commit_total_bytes": 40 * 2 ** 30, "commit_limit_bytes": 200 * 2 ** 30})
    _info, warnings = runtime_limits.host_diagnostics()
    assert not any("커밋 한도" in w for w in warnings)


@pytest.mark.skipif(os.name != "nt", reason="Windows 전용 private bytes")
def test_windows_uses_private_bytes_not_working_set(monkeypatch):
    fake = types.SimpleNamespace(rss=40 * 2 ** 30, vms=12 * 2 ** 30, private=10 * 2 ** 30)
    import psutil

    class _Proc:
        def __init__(self, _pid):
            pass

        def memory_info(self):
            return fake

    monkeypatch.setattr(psutil, "Process", _Proc)
    monkeypatch.setattr(runtime_limits, "_read_smaps_rollup", lambda: {})
    snap = runtime_limits.process_memory_snapshot()
    assert snap["process_memory_effective_kind"] == "private_commit"
    assert snap["process_memory_effective_gb"] == 10.0
    assert snap["process_rss_gb"] == 40.0


@pytest.mark.skipif(os.name != "nt", reason="Windows 전용")
def test_real_commit_numbers_are_readable():
    runtime_limits._COMMIT_MEMO.update(ts=0.0, value=None)
    commit = runtime_limits._windows_commit_bytes()
    assert commit and commit["commit_limit_bytes"] >= commit["commit_total_bytes"] > 0
