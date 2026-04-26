from __future__ import annotations

import datetime
import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from routers import dashboard  # noqa: E402


def test_fab_progress_target_eta_uses_last_three_same_product_lots(monkeypatch):
    base = datetime.datetime(2026, 4, 20, 8, 0, 0)
    rows = []
    for i, hours in enumerate([10, 20, 30, 40, 50], start=1):
        lot = f"L{i:03d}"
        start = base + datetime.timedelta(days=i)
        rows.extend([
            {"product": "PRODX", "root_lot_id": lot, "fab_lot_id": lot, "step_id": "S10", "time": start.isoformat()},
            {"product": "PRODX", "root_lot_id": lot, "fab_lot_id": lot, "step_id": "S20", "time": (start + datetime.timedelta(hours=hours)).isoformat()},
        ])
    rows.append({
        "product": "PRODX",
        "root_lot_id": "L999",
        "fab_lot_id": "L999",
        "step_id": "S10",
        "time": (base + datetime.timedelta(days=9)).isoformat(),
    })
    monkeypatch.setattr(dashboard, "_scan_dashboard_fab_long", lambda _product: pl.DataFrame(rows).lazy())

    data = dashboard._compute_fab_progress(
        product="PRODX",
        days=999,
        limit=10,
        target_step_id="S20",
        lot_query="L999",
        sample_lots=3,
    )

    bench = data["target_benchmark"]
    assert bench["samples"] == 3
    assert bench["historical_samples"] == 5
    assert bench["avg_hours"] == 40
    assert [row["root_lot_id"] for row in bench["recent_lots"]] == ["L005", "L004", "L003"]

    lot = data["wip_lots"][0]
    assert lot["root_lot_id"] == "L999"
    assert lot["target_eta"]["status"] == "estimated"
    assert lot["target_eta"]["avg_hours"] == 40
