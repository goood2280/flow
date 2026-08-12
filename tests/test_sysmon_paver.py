import threading

from core import sysmon


def test_paver_target_is_kept_between_80_and_89_percent():
    assert sysmon._normalize_paver_target(12) == 80.0
    assert sysmon._normalize_paver_target(85) == 85.0
    assert sysmon._normalize_paver_target(99) == 89.0


def test_paver_ceiling_applies_to_cpu_or_ram():
    assert sysmon._paver_ceiling_breach({"cpu_percent": 90, "memory_percent": 85}).startswith("CPU")
    assert sysmon._paver_ceiling_breach({"cpu_percent": 85, "memory_percent": 90}).startswith("RAM")
    assert sysmon._paver_ceiling_breach({"cpu_percent": 89.9, "memory_percent": 89.9}) == ""


def test_manual_paver_starts_combined_cpu_and_ram_load(monkeypatch):
    started = {}

    class FakeThread:
        def __init__(self, *, target, args, name, daemon):
            started.update(target=target, args=args, name=name, daemon=daemon)
            self.alive = False

        def start(self):
            self.alive = True

        def is_alive(self):
            return self.alive

    monkeypatch.setattr(sysmon, "_collect_stats", lambda: {"cpu_percent": 20, "memory_percent": 30})
    monkeypatch.setattr(sysmon.threading, "Thread", FakeThread)
    monkeypatch.setattr(sysmon, "_load_thread", None)
    monkeypatch.setattr(sysmon, "_load_stop", threading.Event())
    monkeypatch.setattr(sysmon, "_last_user_activity", 0.0)

    result = sysmon.start_manual_load(duration_sec=180, target_pct=85, memory=False)

    assert result["ok"] is True
    assert result["cpu"] is True
    assert result["memory"] is True
    assert started["args"] == (180, "manual", 85.0, True)
    assert result["state"]["paver_cpu_active"] is True
    assert result["state"]["paver_memory_active"] is True


def test_manual_paver_does_not_start_at_safety_ceiling(monkeypatch):
    monkeypatch.setattr(sysmon, "_collect_stats", lambda: {"cpu_percent": 91, "memory_percent": 40})
    monkeypatch.setattr(sysmon, "_load_thread", None)
    monkeypatch.setattr(sysmon, "_load_stop", threading.Event())
    monkeypatch.setattr(sysmon, "_manual_paver_allocated_mb", 64)
    monkeypatch.setattr(sysmon, "_manual_paver_hold", [bytearray(1)])

    result = sysmon.start_manual_load()

    assert result["ok"] is False
    assert result["released"] is True
    assert result["released_mb"] == 64
    assert result["state"]["load_release_reason"].startswith("CPU 91.0%")


def test_combined_worker_stops_and_records_cpu_ceiling(monkeypatch):
    monkeypatch.setattr(sysmon, "_collect_stats", lambda: {"cpu_percent": 90.2, "memory_percent": 84})
    monkeypatch.setattr(sysmon, "_burn_cpu", lambda *args, **kwargs: None)
    monkeypatch.setattr(sysmon, "_hold_memory_until", lambda *args, **kwargs: None)
    monkeypatch.setattr(sysmon, "_load_stop", threading.Event())

    sysmon._load_worker(duration_sec=5, mode="manual", target_pct=85, memory=True)

    assert sysmon._load_stop.is_set()
    assert sysmon._load_release_reason.startswith("CPU 90.2%")
    assert sysmon._mem_allocated_mb == 0
