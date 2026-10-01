"""File Browser SQL queue: several users' scans at once, per-user/key order kept."""
from __future__ import annotations

import threading
import time

import pytest

from core import duckdb_engine, filebrowser_query_queue as sql_queue, runtime_limits


@pytest.fixture(autouse=True)
def clean_queue(monkeypatch):
    monkeypatch.setattr(sql_queue, "stale_seconds", lambda: 10.0)
    monkeypatch.setattr(sql_queue, "_parallel_memory_ok_locked", lambda now: True)
    monkeypatch.setattr(duckdb_engine, "thread_budget", lambda: 8)
    monkeypatch.delenv("FLOW_FILEBROWSER_SQL_CONCURRENCY", raising=False)
    monkeypatch.delenv("FLOW_FILEBROWSER_SQL_PER_USER", raising=False)
    with sql_queue._COND:
        sql_queue._PENDING.clear()
        sql_queue._RUNNING.clear()
    yield
    with sql_queue._COND:
        sql_queue._PENDING.clear()
        sql_queue._RUNNING.clear()


class Scan:
    """Runs one queued scan in a thread and holds it until released."""

    def __init__(self, user, session, key=None, query_id="q"):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.error = None
        self.threads = None
        self.entered_at = None
        self._args = dict(username=user, session_id=session, query_id=query_id,
                          query_key=key or f"filebrowser:{user}:DB:{session}")
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        try:
            with sql_queue.execute(**self._args):
                self.threads = duckdb_engine._thread_count()
                self.entered_at = time.monotonic()
                self.entered.set()
                self.release.wait(10)
        except Exception as exc:  # recorded for assertions
            self.error = exc
            self.entered.set()

    def start(self):
        self.thread.start()
        return self

    def finish(self):
        self.release.set()
        self.thread.join(5)


def _wait_queued(count, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with sql_queue._COND:
            if len(sql_queue._PENDING) >= count:
                return True
        time.sleep(0.01)
    return False


def test_different_users_scan_at_the_same_time(monkeypatch):
    monkeypatch.setenv("FLOW_FILEBROWSER_SQL_CONCURRENCY", "2")
    a = Scan("alice", "s1").start()
    assert a.entered.wait(2)
    b = Scan("bob", "s2").start()
    assert b.entered.wait(2), "second user's scan must not wait behind the first"
    assert a.error is None and b.error is None
    assert sql_queue.snapshot()["running_count"] == 2
    # The scan that started next to another gets a share of the DuckDB threads.
    assert (a.threads, b.threads) == (8, 4)
    a.finish()
    b.finish()
    assert sql_queue.snapshot()["running_count"] == 0


def test_capacity_is_respected_in_fifo_order(monkeypatch):
    monkeypatch.setenv("FLOW_FILEBROWSER_SQL_CONCURRENCY", "2")
    a = Scan("alice", "s1").start()
    b = Scan("bob", "s2").start()
    assert a.entered.wait(2) and b.entered.wait(2)
    c = Scan("carol", "s3").start()
    assert _wait_queued(1)
    d = Scan("dave", "s4").start()
    assert _wait_queued(2)
    assert not c.entered.wait(0.3)
    a.finish()
    assert c.entered.wait(2)
    assert not d.entered.is_set()
    b.finish()
    assert d.entered.wait(2)
    c.finish()
    d.finish()
    assert all(s.error is None for s in (a, b, c, d))


def test_same_user_second_tab_waits_but_other_users_pass(monkeypatch):
    monkeypatch.setenv("FLOW_FILEBROWSER_SQL_CONCURRENCY", "3")
    a1 = Scan("alice", "tab1").start()
    assert a1.entered.wait(2)
    a2 = Scan("alice", "tab2").start()
    assert _wait_queued(1)
    b = Scan("bob", "s").start()
    assert b.entered.wait(2), "another user's scan passes alice's queued one"
    assert not a2.entered.is_set()
    a1.finish()
    assert a2.entered.wait(2)
    assert a2.error is None
    a2.finish()
    b.finish()


def test_same_query_key_never_overlaps_even_with_higher_per_user_limit(monkeypatch):
    monkeypatch.setenv("FLOW_FILEBROWSER_SQL_CONCURRENCY", "3")
    monkeypatch.setenv("FLOW_FILEBROWSER_SQL_PER_USER", "3")
    key = "filebrowser:alice:DB:PROD"
    a1 = Scan("alice", "tab1", key=key).start()
    assert a1.entered.wait(2)
    other = Scan("alice", "tab3", key="filebrowser:alice:DB:OTHER").start()
    assert other.entered.wait(2)
    a2 = Scan("alice", "tab2", key=key).start()
    assert _wait_queued(1)
    assert not a2.entered.wait(0.3)
    a1.finish()
    assert a2.entered.wait(2) and a2.error is None
    a2.finish()
    other.finish()


def test_new_request_of_same_session_replaces_queued_one(monkeypatch):
    monkeypatch.setenv("FLOW_FILEBROWSER_SQL_CONCURRENCY", "1")
    blocker = Scan("bob", "s").start()
    assert blocker.entered.wait(2)
    old = Scan("alice", "page", query_id="q1").start()
    assert _wait_queued(1)
    new = Scan("alice", "page", query_id="q2").start()
    assert old.entered.wait(2)
    assert isinstance(old.error, sql_queue.QueryQueueCanceled)
    blocker.finish()
    assert new.entered.wait(2) and new.error is None
    new.finish()


def test_cancel_interrupts_only_the_matching_running_scan(monkeypatch):
    monkeypatch.setenv("FLOW_FILEBROWSER_SQL_CONCURRENCY", "2")
    interrupted = []
    monkeypatch.setattr(duckdb_engine, "interrupt_query", lambda key: interrupted.append(key) or True)
    a = Scan("alice", "s1").start()
    b = Scan("bob", "s2").start()
    assert a.entered.wait(2) and b.entered.wait(2)
    result = sql_queue.cancel(username="alice", session_id="s1")
    assert result["interrupted"] is True
    assert interrupted == ["filebrowser:alice:DB:s1"]
    a.finish()
    b.finish()
    assert isinstance(a.error, sql_queue.QueryQueueCanceled)
    assert b.error is None


def test_no_parallel_start_while_memory_is_tight(monkeypatch):
    monkeypatch.setenv("FLOW_FILEBROWSER_SQL_CONCURRENCY", "3")
    tight = {"value": True}
    monkeypatch.setattr(sql_queue, "_parallel_memory_ok_locked", lambda now: not tight["value"])
    a = Scan("alice", "s1").start()
    assert a.entered.wait(2), "the first scan always starts"
    b = Scan("bob", "s2").start()
    assert _wait_queued(1)
    assert not b.entered.wait(0.4)
    tight["value"] = False
    assert b.entered.wait(2)
    a.finish()
    b.finish()


def test_default_concurrency_follows_resource_profile(monkeypatch):
    monkeypatch.setattr(runtime_limits, "is_large_profile", lambda: True)
    assert sql_queue.max_concurrent() == 3
    monkeypatch.setattr(runtime_limits, "is_large_profile", lambda: False)
    assert sql_queue.max_concurrent() == 2
    monkeypatch.setenv("FLOW_FILEBROWSER_SQL_CONCURRENCY", "1")
    assert sql_queue.max_concurrent() == 1
    monkeypatch.setenv("FLOW_FILEBROWSER_SQL_CONCURRENCY", "99")
    assert sql_queue.max_concurrent() == 8
    assert sql_queue.per_user_limit() == 1


def test_duckdb_thread_limit_is_scoped_to_the_block(monkeypatch):
    assert duckdb_engine._thread_count() == 8
    with duckdb_engine.thread_limit(3):
        assert duckdb_engine._thread_count() == 3
        with duckdb_engine.thread_limit(16):
            assert duckdb_engine._thread_count() == 8
        assert duckdb_engine._thread_count() == 3
    assert duckdb_engine._thread_count() == 8
