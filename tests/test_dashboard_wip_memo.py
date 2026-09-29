"""WIP 대시보드 결과 메모 — 입력 파일이 바뀌면 다시 계산, 워치독이 비울 수 있다."""
import os
from pathlib import Path

import polars as pl
import pytest

from routers import dashboard


@pytest.fixture(autouse=True)
def clean_memo(monkeypatch):
    monkeypatch.setenv("FLOW_DASHBOARD_MEMO_MB", "64")
    dashboard._WIP_MEMO.clear()
    yield
    dashboard._WIP_MEMO.clear()


def _install_latest(monkeypatch, tmp_path):
    fp = Path(tmp_path) / "latest.parquet"
    pl.DataFrame({"product": ["PRODA"], "root_lot_id": ["R1"], "wafer_id": ["1"]}).write_parquet(fp)
    from core import lot_progress_cache
    monkeypatch.setattr(lot_progress_cache, "filebrowser_cache_parquet_file", lambda: str(fp))
    calls = []

    def fresh(product=""):
        calls.append(product)
        return pl.read_parquet(fp), "PRODA", ["PRODA"], fp

    monkeypatch.setattr(dashboard, "_wip_split_latest_cache_fresh", fresh)
    return fp, calls


def test_latest_cache_is_reused_until_the_file_changes(monkeypatch, tmp_path):
    fp, calls = _install_latest(monkeypatch, tmp_path)
    first = dashboard._wip_split_latest_cache("PRODA")
    again = dashboard._wip_split_latest_cache("proda ")
    assert calls == ["PRODA"] and again[0] is first[0]

    pl.DataFrame({"product": ["PRODA"] * 2, "root_lot_id": ["R1", "R2"], "wafer_id": ["1", "1"]}).write_parquet(fp)
    st = fp.stat()
    os.utime(fp, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    changed = dashboard._wip_split_latest_cache("PRODA")
    assert len(calls) == 2 and changed[0].height == 2


def test_prepare_memo_keys_on_inputs_and_parameters(monkeypatch, tmp_path):
    fp, _ = _install_latest(monkeypatch, tmp_path)
    monkeypatch.setattr(dashboard, "_wip_split_ml_table_path", lambda product: None)
    monkeypatch.setattr(dashboard, "_wip_split_cache_path", lambda product: None)
    built = []

    def fresh(loaded, bin_size, split_col, axis, exclude, lot_type):
        built.append((split_col, exclude))
        return {"cur": loaded[0], "product": loaded[1]}

    monkeypatch.setattr(dashboard, "_wip_split_prepare_fresh", fresh)
    a = dashboard._wip_split_prepare("PRODA", 1000, "KNOB_A", "step_desc", "", "")
    b = dashboard._wip_split_prepare("PRODA", 1000, "KNOB_A", "step_desc", "", "")
    c = dashboard._wip_split_prepare("PRODA", 1000, "KNOB_A", "step_desc", "z", "")
    assert built == [("KNOB_A", ""), ("KNOB_A", "z")]
    assert a["_memo_key"] == b["_memo_key"] != c["_memo_key"]
    # 호출자가 받은 dict 를 고쳐도 메모는 오염되지 않는다.
    b["product"] = "tampered"
    assert dashboard._wip_split_prepare("PRODA", 1000, "KNOB_A", "step_desc", "", "")["product"] == "PRODA"


def test_non_file_inputs_are_not_memoized_and_evict_frees(monkeypatch, tmp_path):
    assert dashboard._wip_file_sig(object()) is None
    dashboard._wip_memo_put(("k",), {"cur": pl.DataFrame({"a": list(range(1000))})})
    assert dashboard._wip_memo_get(("k",)) is not None
    assert dashboard.emergency_evict(1) > 0
    assert dashboard._wip_memo_get(("k",)) is None
    monkeypatch.setenv("FLOW_DASHBOARD_MEMO_MB", "0")
    dashboard._wip_memo_put(("k",), {"x": 1})
    assert dashboard._wip_memo_get(("k",)) is None
