from core import memory_watchdog as wd
from core import heavy_jobs


def test_dev_server_evicts_earlier_than_production(monkeypatch):
    for name in ("FLOW_MEMORY_WARN_PCT", "FLOW_MEMORY_CRITICAL_PCT", "FLOW_MEMORY_SAFE_PCT", "FLOW_MEMORY_IDLE_TRIM_PCT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(wd, "_is_prod_server", lambda: False)
    assert (wd.warn_pct(), wd.critical_pct(), wd.safe_pct(), wd.idle_trim_pct()) == (65.0, 80.0, 45.0, 35.0)
    monkeypatch.setattr(wd, "_is_prod_server", lambda: True)
    assert (wd.warn_pct(), wd.critical_pct(), wd.safe_pct(), wd.idle_trim_pct()) == (80.0, 95.0, 60.0, 50.0)


def test_idle_trim_runs_below_critical_at_most_once_per_interval(monkeypatch):
    calls = []
    monkeypatch.setattr(wd, "_is_prod_server", lambda: False)
    monkeypatch.setattr(wd, "_memory_pct", lambda: (40.0, 6.4, 16.0))
    monkeypatch.setattr(wd, "_LAST_IDLE_TRIM_TS", 0.0)
    monkeypatch.setattr("core.memory_trim.trim", lambda reason="", **k: calls.append(reason) or {"released_bytes": 2 << 20})
    monkeypatch.setattr("core.cache_event_log.record_ram_peak_sample", lambda **k: None)

    wd.check_once()
    wd.check_once()
    assert calls == ["idle"]
    assert wd.status()["last_idle_trim"]["released_mb"] == 2.0

    # 보통 사용량(35% 미만)에서는 건드리지 않는다.
    monkeypatch.setattr(wd, "_memory_pct", lambda: (20.0, 3.2, 16.0))
    monkeypatch.setattr(wd, "_LAST_IDLE_TRIM_TS", 0.0)
    wd.check_once()
    assert calls == ["idle"]


def test_heavy_job_releases_memory_when_done(monkeypatch):
    calls = []
    monkeypatch.setattr("core.memory_trim.trim", lambda reason="", **k: calls.append(reason) or {})
    monkeypatch.setattr(heavy_jobs, "_memory_block_reason", lambda: "")
    assert heavy_jobs.run_heavy("unit_test_task", lambda: {"ok": True}) == {"ok": True}
    assert calls and calls[-1].startswith("task_done:unit_test_task")
