from __future__ import annotations

import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from routers import splittable  # noqa: E402


def _expand_like_frontend(payload: dict) -> list[dict]:
    """My_SplitTable.jsx expandViewRows 와 동일한 복원 규칙 (계약 고정용)."""
    wf_keys = payload.get("wafer_keys") or []
    root = payload.get("root_lot_id") or ""
    out = []
    for r in payload["rows_compact"]:
        vals = r.get("a") or []
        plans = r.get("p") or {}
        mism = set(r.get("m") or [])
        can_plan = bool(r.get("can_plan"))
        is_tag = bool(r.get("tag"))
        is_mgmt = bool(r.get("mgmt"))
        cells = {}
        for ci in range(len(vals)):
            key = str(ci)
            cells[key] = {
                "actual": vals[ci],
                "plan": plans.get(key),
                "key": f"{root}|{wf_keys[ci] if ci < len(wf_keys) else ci}|{r['_param']}",
                "can_plan": can_plan,
                "mismatch": ci in mism,
                "is_custom_tag": is_tag,
                "can_tag": is_tag,
                "is_management_row": is_mgmt,
                "can_management_edit": is_mgmt,
            }
        out.append({"_param": r["_param"], "_display": r["_display"], "_cells": cells})
    return out


def test_compact_view_rows_roundtrip_matches_legacy(tmp_path, monkeypatch):
    product = "ML_TABLE_COMPACT"
    pl.DataFrame({
        "root_lot_id": ["RCP01"] * 3,
        "wafer_id": [1, 2, 3],
        "KNOB_ALPHA": ["ON", "OFF", None],
        "KNOB_BETA": ["1.0", "1.0", "2.0"],
    }).write_parquet(tmp_path / f"{product}.parquet")

    monkeypatch.setattr(splittable, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_db_base", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_enqueue_pivot_cache_build", lambda *a, **k: False)
    # plan 1건 저장 → sparse plan/mismatch 경로 검증
    monkeypatch.setattr(splittable, "_load_plan_data", lambda p: {
        "plans": {"RCP01|2|KNOB_ALPHA": {"value": "ON", "user": "t", "updated": ""}},
    })
    splittable._clear_split_view_cache()

    payload = splittable.view_split(
        product=product, root_lot_id="RCP01", wafer_ids="", prefix="KNOB",
        custom_name="", view_mode="all", history_mode="all", fab_lot_id="",
        custom_cols="", request=None,
    )
    assert "rows_compact" in payload and "wafer_keys" in payload
    assert payload["rows"], "legacy rows must still be present for internal consumers"

    expanded = _expand_like_frontend(payload)
    assert expanded == payload["rows"], "FE 복원 결과가 레거시 rows 와 정확히 일치해야 한다"

    # plan/mismatch가 실제로 반영됐는지 (wafer 2 KNOB_ALPHA: plan ON vs actual OFF)
    alpha = next(r for r in payload["rows"] if r["_param"] == "KNOB_ALPHA")
    plan_cells = [c for c in alpha["_cells"].values() if c["plan"] is not None]
    assert plan_cells and plan_cells[0]["mismatch"] is True


def test_http_wrapper_swaps_rows_with_compact(tmp_path, monkeypatch):
    product = "ML_TABLE_COMPACT2"
    pl.DataFrame({
        "root_lot_id": ["RCP02", "RCP02"],
        "wafer_id": [1, 2],
        "KNOB_ALPHA": ["ON", "OFF"],
    }).write_parquet(tmp_path / f"{product}.parquet")
    monkeypatch.setattr(splittable, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_db_base", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_enqueue_pivot_cache_build", lambda *a, **k: False)
    splittable._clear_split_view_cache()

    import orjson
    resp = splittable.view_split_http(
        product=product, root_lot_id="RCP02", wafer_ids="", prefix="KNOB",
        custom_name="", view_mode="all", history_mode="all", fab_lot_id="",
        custom_cols="", request=None,
    )
    body = orjson.loads(resp.body)
    assert body.get("cells_format") == "v2"
    assert "rows_compact" not in body
    assert all("_cells" not in r for r in body["rows"]), "HTTP rows 는 슬림 포맷이어야 한다"
    assert isinstance(body.get("wafer_keys"), list)

    # 캐시 HIT 경로에서도 동일 (rows_compact 는 view cache 에 저장됨)
    resp2 = splittable.view_split_http(
        product=product, root_lot_id="RCP02", wafer_ids="", prefix="KNOB",
        custom_name="", view_mode="all", history_mode="all", fab_lot_id="",
        custom_cols="", request=None,
    )
    body2 = orjson.loads(resp2.body)
    assert body2.get("cells_format") == "v2"
    assert body2["rows"] == body["rows"]
