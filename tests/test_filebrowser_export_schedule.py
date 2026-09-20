import os
import threading
import time

from app_v2.modules.filebrowser.export_schedule import schedule_if_stale


def test_export_runs_once_for_stale_source_and_again_after_update(tmp_path):
    source = tmp_path / "lot_wf_current.json"
    target = tmp_path / "lot_wf_current.parquet"
    source.write_text("source", encoding="utf-8")
    os.utime(source, ns=(2_000_000_000, 2_000_000_000))
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    restarted = threading.Event()
    calls = []

    def export():
        calls.append(1)
        if len(calls) == 2:
            restarted.set()
        started.set()
        release.wait(timeout=5)
        target.write_text("export", encoding="utf-8")
        os.utime(target, ns=(3_000_000_000, 3_000_000_000))
        finished.set()

    assert schedule_if_stale(source, target, export)
    assert started.wait(timeout=5)
    assert not schedule_if_stale(source, target, export)
    release.set()
    assert finished.wait(timeout=5)
    assert not schedule_if_stale(source, target, export)
    assert calls == [1]

    os.utime(source, ns=(4_000_000_000, 4_000_000_000))
    deadline = time.monotonic() + 5
    while not schedule_if_stale(source, target, export):
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert restarted.wait(timeout=5)


def test_settings_change_and_failed_result_require_retry(tmp_path):
    source = tmp_path / "state.json"
    target = tmp_path / "state.parquet"
    settings = tmp_path / "settings.json"
    source.write_text("state", encoding="utf-8")
    settings.write_text("settings", encoding="utf-8")
    target.write_text("old", encoding="utf-8")
    os.utime(source, ns=(1_000_000_000, 1_000_000_000))
    os.utime(settings, ns=(1_000_000_000, 1_000_000_000))
    os.utime(target, ns=(2_000_000_000, 2_000_000_000))
    assert not schedule_if_stale(source, target, lambda: None, settings=settings)

    os.utime(settings, ns=(3_000_000_000, 3_000_000_000))
    failed = threading.Event()

    def failing_export():
        target.write_text("incomplete", encoding="utf-8")
        os.utime(target, ns=(4_000_000_000, 4_000_000_000))
        failed.set()
        return {"ok": False}

    assert schedule_if_stale(source, target, failing_export, settings=settings)
    assert failed.wait(timeout=5)
    retried = threading.Event()

    def successful_export():
        retried.set()
        return {"ok": True}

    deadline = time.monotonic() + 5
    while not schedule_if_stale(source, target, successful_export, settings=settings):
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert retried.wait(timeout=5)


def test_missing_source_triggers_regeneration_even_with_existing_target(tmp_path):
    target = tmp_path / "state.parquet"
    target.write_text("old", encoding="utf-8")
    regenerated = threading.Event()
    assert schedule_if_stale(tmp_path / "missing.json", target, regenerated.set)
    assert regenerated.wait(timeout=5)
