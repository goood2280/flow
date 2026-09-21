import threading
import datetime as dt
import pytest

from core import sysmon


@pytest.fixture(autouse=True)
def isolate_paver(monkeypatch, tmp_path):
    monkeypatch.setattr(sysmon, "RESOURCE_LOG", tmp_path / "resources.jsonl")
    monkeypatch.setattr(sysmon, "SYSMON_STATE_FILE", tmp_path / "schedule.json")
    monkeypatch.setattr(sysmon, "_load_result", {})
    monkeypatch.setattr(sysmon, "_load_thread", None)
    monkeypatch.setattr(sysmon, "_load_stop", threading.Event())
    monkeypatch.setattr(sysmon, "_load_mode", "")
    monkeypatch.setattr(sysmon, "_load_error", "")
    monkeypatch.setattr(sysmon, "_load_release_reason", "")


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


class ScheduledThread:
    starts = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.alive = False

    def start(self):
        self.starts.append(self.kwargs)
        self.alive = True

    def is_alive(self):
        return self.alive


@pytest.fixture
def scheduled(monkeypatch):
    from core import worker_dispatch
    monkeypatch.setattr(worker_dispatch, "server_role", lambda: "api")
    monkeypatch.setattr(sysmon.PATHS, "is_prod", True)
    monkeypatch.setattr(sysmon.threading, "Thread", ScheduledThread)
    monkeypatch.setattr(ScheduledThread, "starts", [])
    sysmon.save_schedule(enabled=True, at="11:00")
    return ScheduledThread


def test_daily_schedule_catches_up_once_and_preserves_settings(scheduled, monkeypatch):
    assert not sysmon._maybe_start_scheduled_load(dt.datetime(2026, 9, 22, 10, 59))
    assert sysmon._maybe_start_scheduled_load(dt.datetime(2026, 9, 22, 16, 30))
    assert scheduled.starts[0]["args"] == (600, "scheduled", 85.0, True)
    assert sysmon._load_mode == "scheduled"
    monkeypatch.setattr(sysmon, "_load_thread", None)
    assert not sysmon._maybe_start_scheduled_load(dt.datetime(2026, 9, 22, 20, 0))
    assert sysmon._maybe_start_scheduled_load(dt.datetime(2026, 9, 23, 11, 0))
    assert len(scheduled.starts) == 2


def test_schedule_uses_korean_time(scheduled):
    assert sysmon._maybe_start_scheduled_load(dt.datetime(2026, 9, 22, 2, 1, tzinfo=dt.timezone.utc))
    assert sysmon.get_schedule()["last_run_at"].startswith("2026-09-22T11:01")


def test_disabled_and_worker_schedule_never_start(scheduled, monkeypatch):
    from core import worker_dispatch
    sysmon.save_schedule(enabled=False, at="11:00")
    assert not sysmon._maybe_start_scheduled_load(dt.datetime(2026, 9, 22, 12))
    sysmon.save_schedule(enabled=True, at="11:00")
    monkeypatch.setattr(worker_dispatch, "server_role", lambda: "worker")
    assert not sysmon._maybe_start_scheduled_load(dt.datetime(2026, 9, 22, 12))
    assert not scheduled.starts


def test_thread_start_failure_does_not_consume_daily_attempt(scheduled, monkeypatch):
    def fail(_self):
        raise RuntimeError("cannot start")
    monkeypatch.setattr(ScheduledThread, "start", fail)
    with pytest.raises(RuntimeError, match="cannot start"):
        sysmon._maybe_start_scheduled_load(dt.datetime(2026, 9, 22, 12))
    assert sysmon.get_schedule()["last_run_date"] == ""
    assert sysmon._load_thread is None


@pytest.mark.parametrize("cpu,ram,status", [(84, 81, "reached"), (85, 60, "incomplete"), (60, 85, "incomplete")])
def test_scheduled_result_requires_both_resources_and_survives_reload(monkeypatch, cpu, ram, status):
    def run(*_args):
        sysmon._record_paver_sample({"cpu_percent": cpu, "memory_percent": ram})
    monkeypatch.setattr(sysmon, "_run_load_worker", run)
    sysmon._load_worker(600, "scheduled", 85, True)
    result = sysmon.get_schedule()["last_result"]
    assert result["status"] == status
    assert result["cpu_peak_pct"] == cpu
    assert result["memory_peak_pct"] == ram


def test_worker_failure_is_recorded_as_incomplete(monkeypatch):
    def run(*_args):
        raise RuntimeError("sample failed")
    monkeypatch.setattr(sysmon, "_run_load_worker", run)
    sysmon._load_worker(600, "scheduled", 85, True)
    result = sysmon.get_schedule()["last_result"]
    assert result["status"] == "incomplete"
    assert "sample failed" in result["error"]


def test_cpu_percent_uses_five_core_cgroup_not_host(monkeypatch):
    monkeypatch.setattr(sysmon, "effective_cpu_count", lambda: 5.0)
    monkeypatch.setattr(sysmon, "_cgroup_cpu_quota_cores", lambda: 5.0)
    monkeypatch.setattr(sysmon, "_CGROUP_CPU_LAST", {})
    readings = iter([(100.0, "cgroup_v2"), (104.0, "cgroup_v2")])
    ticks = iter([1.0, 2.0])
    monkeypatch.setattr(sysmon, "_cgroup_cpu_seconds", lambda: next(readings))
    monkeypatch.setattr(sysmon.time, "monotonic", lambda: next(ticks))
    sysmon._apply_effective_cpu({"cpu_percent": 10})
    sample = sysmon._apply_effective_cpu({"cpu_percent": 10})
    assert sample["cpu_percent"] == 80.0
    assert sample["cpu_host_percent"] == 10
    assert sample["cpu_source"] == "cgroup_v2"



def test_sampling_error_stops_all_auxiliary_threads(monkeypatch):
    samples = iter([{"cpu_percent": 20, "memory_percent": 30}])
    monkeypatch.setattr(sysmon, "_collect_stats", lambda: next(samples))
    monkeypatch.setattr(sysmon, "_burn_cpu", lambda stop, *_args: stop.wait(2))
    monkeypatch.setattr(sysmon, "_hold_memory_until", lambda stop, *_args: stop.wait(2))
    sysmon._load_worker(10, "scheduled", 85, True)
    assert sysmon._load_stop.is_set()
    assert sysmon.get_schedule()["last_result"]["status"] == "incomplete"
    assert sysmon._load_mode == ""
    assert sysmon._mem_allocated_mb == 0


def test_monitor_is_registered_only_with_elected_scheduler_owner(monkeypatch):
    import logging
    from app_v2.runtime import startup
    from core import background_owner, worker_dispatch, scheduler_health
    batches = []
    callbacks = []
    monkeypatch.setattr(startup, "_start_many", lambda entries, _logger: batches.append(entries))
    monkeypatch.setattr(worker_dispatch, "external_services_enabled", lambda: True)
    monkeypatch.setattr(background_owner, "start", lambda callback, _logger: callbacks.append(callback))
    monkeypatch.setattr(scheduler_health, "record_startup", lambda *_args: None)
    monkeypatch.setattr(scheduler_health, "start_monitor", lambda *_args: None)
    startup.start_background_services(logging.getLogger("test"))
    assert not any(module == "core.sysmon" for batch in batches for _, module, _ in batch)
    assert len(callbacks) == 1
    callbacks[0]()
    assert sum(module == "core.sysmon" for batch in batches for _, module, _ in batch) == 1



def test_nonproduction_api_never_starts_scheduled_load(scheduled, monkeypatch):
    monkeypatch.setattr(sysmon.PATHS, "is_prod", False)
    assert not sysmon._maybe_start_scheduled_load(dt.datetime(2026, 9, 22, 12))
    assert not scheduled.starts


def test_initial_sample_error_does_not_leave_active_mode(monkeypatch):
    def fail():
        raise RuntimeError("initial sample failed")
    monkeypatch.setattr(sysmon, "_collect_stats", fail)
    sysmon._load_worker(600, "scheduled", 85, True)
    assert sysmon._load_stop.is_set()
    assert sysmon._load_mode == ""
    assert "initial sample failed" in sysmon.get_schedule()["last_result"]["error"]


def test_cpu_only_idle_result_does_not_require_ram(monkeypatch):
    def run(*_args):
        sysmon._record_paver_sample({"cpu_percent": 84, "memory_percent": 20})
    monkeypatch.setattr(sysmon, "_run_load_worker", run)
    sysmon._load_worker(600, "auto", 85, False)
    assert sysmon._load_result["status"] == "reached"
    assert sysmon._load_result["reached_80"] is False
