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


def test_paver_cpu_workers_scale_past_old_eight_worker_ceiling(monkeypatch):
    monkeypatch.setattr(sysmon, "effective_cpu_count", lambda: 32.0)

    assert sysmon._paver_cpu_worker_count() == 32


def test_paver_cpu_load_uses_full_workers_and_one_fractional_worker():
    assert sysmon._paver_cpu_duties(8, 3.25) == [1.0, 1.0, 1.0, 0.25, 0.0, 0.0, 0.0, 0.0]


def test_paver_cpu_equivalents_move_gradually_toward_target():
    assert sysmon._next_paver_cpu_equivalents(4.0, cpu_pct=60, target_pct=85, worker_count=12) == 4.75
    assert sysmon._next_paver_cpu_equivalents(8.0, cpu_pct=88, target_pct=85, worker_count=12) < 8.0
    assert sysmon._next_paver_cpu_equivalents(8.0, cpu_pct=85.5, target_pct=85, worker_count=12) == 8.0


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


def test_manual_paver_is_not_released_by_user_activity(monkeypatch):
    stop_event = threading.Event()
    monkeypatch.setattr(sysmon, "_load_stop", stop_event)
    monkeypatch.setattr(sysmon, "_load_mode", "manual")
    monkeypatch.setattr(sysmon, "_last_user_activity", 0.0)
    monkeypatch.setattr(sysmon, "_paused_until", 0.0)
    monkeypatch.setattr(sysmon, "_now", lambda: 1_000.0)

    sysmon.mark_user_activity()

    assert not stop_event.is_set()
    assert sysmon._last_user_activity == 1_000.0
    assert sysmon._paused_until == 1_000.0 + sysmon.PAUSE_AFTER_USER_SEC


def test_automatic_idle_load_is_released_by_user_activity(monkeypatch):
    stop_event = threading.Event()
    monkeypatch.setattr(sysmon, "_load_stop", stop_event)
    monkeypatch.setattr(sysmon, "_load_mode", "auto")

    sysmon.mark_user_activity()

    assert stop_event.is_set()


def test_combined_worker_stops_and_records_cpu_ceiling(monkeypatch):
    monkeypatch.setattr(sysmon, "_collect_stats", lambda: {"cpu_percent": 90.2, "memory_percent": 84})
    monkeypatch.setattr(sysmon, "_burn_cpu", lambda *args, **kwargs: None)
    monkeypatch.setattr(sysmon, "_hold_memory_until", lambda *args, **kwargs: None)
    monkeypatch.setattr(sysmon, "_load_stop", threading.Event())

    sysmon._load_worker(duration_sec=5, mode="manual", target_pct=85, memory=True)

    assert sysmon._load_stop.is_set()
    assert sysmon._load_release_reason.startswith("CPU 90.2%")
    assert sysmon._mem_allocated_mb == 0
