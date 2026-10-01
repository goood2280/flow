import pytest

from core import duckdb_engine

duckdb = pytest.importorskip("duckdb")


def _snapshot(total, available):
    return lambda *a, **k: {"system_memory_total_gb": total, "system_memory_available_gb": available}


def test_large_host_caps_each_connection(monkeypatch):
    monkeypatch.delenv("FLOW_DUCKDB_MEMORY_LIMIT_GB", raising=False)
    monkeypatch.setattr("core.runtime_limits.system_memory_snapshot", _snapshot(128.0, 60.0))
    monkeypatch.setattr("core.cache_budget.large_host", lambda: True)
    assert duckdb_engine.memory_limit_gb() == 16.0
    # 캐시가 차서 여유가 적으면 여유의 절반까지만.
    monkeypatch.setattr("core.runtime_limits.system_memory_snapshot", _snapshot(128.0, 10.0))
    assert duckdb_engine.memory_limit_gb() == 5.0


def test_small_host_uses_quarter_of_ram_and_env_override(monkeypatch):
    monkeypatch.delenv("FLOW_DUCKDB_MEMORY_LIMIT_GB", raising=False)
    monkeypatch.setattr("core.runtime_limits.system_memory_snapshot", _snapshot(16.0, 12.0))
    monkeypatch.setattr("core.cache_budget.large_host", lambda: False)
    assert duckdb_engine.memory_limit_gb() == 4.0
    monkeypatch.setenv("FLOW_DUCKDB_MEMORY_LIMIT_GB", "2")
    assert duckdb_engine.memory_limit_gb() == 2.0
    monkeypatch.setenv("FLOW_DUCKDB_MEMORY_LIMIT_GB", "0")
    assert duckdb_engine.memory_limit_gb() == 0.0


def test_connection_gets_limit_and_temp_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOW_DUCKDB_MEMORY_LIMIT_GB", "3")
    monkeypatch.setenv("FLOW_DUCKDB_TEMP_DIR", str(tmp_path / "spill"))
    monkeypatch.setattr("core.runtime_limits.system_memory_snapshot", _snapshot(64.0, 40.0))
    con = duckdb_engine._connect()
    try:
        limit = con.execute("SELECT current_setting('memory_limit')").fetchone()[0]
        temp = con.execute("SELECT current_setting('temp_directory')").fetchone()[0]
    finally:
        con.close()
    assert limit.replace(" ", "").upper().startswith(("2.7GIB", "2.8GIB", "3.0GB", "3GB", "2.7GB", "2.8GB"))
    assert (tmp_path / "spill").is_dir()
    assert str(tmp_path / "spill") in temp
