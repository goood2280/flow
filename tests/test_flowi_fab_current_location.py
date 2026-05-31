from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import polars as pl

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
for p in (BACKEND, ROOT):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)


def test_flowi_current_location_returns_latest_fab_step_without_lot_cards(monkeypatch, tmp_path):
    from routers import llm

    paths = SimpleNamespace(
        base_root=tmp_path,
        db_root=tmp_path / "DB",
        data_root=tmp_path / "flow-data",
        cache_dir=tmp_path / "flow-data" / "cache",
    )
    monkeypatch.setattr(llm, "PATHS", paths)

    src_dir = paths.db_root / "1.RAWDATA_DB_FAB" / "PRODA" / "date=20240424"
    src_dir.mkdir(parents=True)
    pl.DataFrame(
        [
            {
                "product": "PRODA",
                "root_lot_id": "A1001",
                "lot_id": "A1001A.1",
                "fab_lot_id": "A1001A.1",
                "wafer_id": 3,
                "step_id": "AA100140",
                "process_id": "P1",
                "tkout_time": "2024-04-24T03:09:00",
            },
            {
                "product": "PRODA",
                "root_lot_id": "A1001",
                "lot_id": "A1001A.1",
                "fab_lot_id": "A1001A.1",
                "wafer_id": 3,
                "step_id": "AA100150",
                "process_id": "P1",
                "tkout_time": "2024-04-24T10:09:00",
            },
            {
                "product": "PRODA",
                "root_lot_id": "A1001",
                "lot_id": "A1001A.1",
                "fab_lot_id": "A1001A.1",
                "wafer_id": 4,
                "step_id": "AA999999",
                "process_id": "P1",
                "tkout_time": "2024-04-25T00:00:00",
            },
        ]
    ).write_parquet(src_dir / "part.parquet")

    prompt = "A1001 #3 " + "\uc9c0\uae08 \uc5b4\ub514\uc5d0 \uc788\uc5b4?"
    out = llm._handle_flowi_query(
        prompt,
        product="PRODA",
        max_rows=12,
        allowed_keys={"filebrowser", "dashboard", "splittable"},
        username="tester",
        role="admin",
        agent_context={},
    )

    assert out["handled"] is True
    assert out["action"] == "query_current_location"
    assert out["intent"] == "fab_current_location_lookup"
    assert "AA100150" in out["answer"]
    assert "lot_list" not in out
    assert out["table"]["total"] == 1
    assert out["table"]["rows"][0]["step_id"] == "AA100150"
    assert out["table"]["rows"][0]["wafer_id"] == "3"
