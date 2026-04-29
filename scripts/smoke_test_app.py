#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app import app
from routers.dashboard import fab_progress, trend_alerts
from routers.ettime import et_report, _product_aliases
from routers.splittable import _build_inline_meta, _build_knob_meta, _build_vm_meta, _load_operational_history


def main() -> int:
    report: dict[str, object] = {"checks": []}

    product_code = "PRODA"
    first_row = {"product": product_code, "root_lot_id": "", "wafer_id": ""}

    schema_paths = {route.path for route in app.routes}
    required_paths = {
        "/api/ettime/report",
        "/api/dashboard/fab-progress",
        "/api/dashboard/trend-alerts",
        "/api/splittable/operational-history",
        "/api/tracker/issue",
    }
    missing = sorted(required_paths - schema_paths)
    if missing:
        raise RuntimeError(f"Missing routes: {missing}")
    report["checks"].append({"name": "routes_registered", "ok": True, "count": len(required_paths)})

    knob_meta = _build_knob_meta(product_code)
    inline_meta = _build_inline_meta(product_code)
    vm_meta = _build_vm_meta(product_code)
    if not knob_meta:
        raise RuntimeError("knob meta is empty")
    report["checks"].append({
        "name": "mapping_meta",
        "ok": True,
        "knob": len(knob_meta),
        "inline": len(inline_meta),
        "vm": len(vm_meta),
    })

    hist = _load_operational_history(
        product=product_code,
        root_lot_id=str(first_row.get("root_lot_id") or ""),
        wafer_ids=str(first_row.get("wafer_id") or ""),
        username="hol",
        role="admin",
    )
    report["checks"].append({"name": "operational_history", "ok": isinstance(hist, list), "items": len(hist)})

    et_res = {"summary": {"packages": 0, "metric_name": "Rc"}}
    for cand in [product_code, *_product_aliases(product_code)]:
        cur = et_report(
            product=cand,
            root_lot_id="",
            fab_lot_id="",
            wafer_id="",
            step_id="",
            metric="Rc",
            limit=80,
        )
        if (cur.get("summary") or {}).get("packages", 0) > 0:
            et_res = cur
            break
    report["checks"].append({
        "name": "et_report",
        "ok": (et_res.get("summary") or {}).get("packages", 0) >= 1,
        "packages": (et_res.get("summary") or {}).get("packages", 0),
        "metric": (et_res.get("summary") or {}).get("metric_name", ""),
    })

    class _State:
        user = {"username": "hol", "role": "admin"}
    class _DummyReq:
        headers = {}
        state = _State()
    dummy = _DummyReq()

    fab_res = fab_progress(
        dummy,
        product="PRODA",
        days=7,
        limit=5,
        target_step_id="AA200030",
        lot_query="",
        sample_lots=5,
        knob_col="",
        knob_value="",
    )
    report["checks"].append({
        "name": "dashboard_fab_progress",
        "ok": "summary" in fab_res and "wip_lots" in fab_res,
        "source_mode": fab_res.get("source_mode", ""),
        "lots": (fab_res.get("summary") or {}).get("lots", 0),
    })

    trend_res = trend_alerts(dummy, limit=5)
    report["checks"].append({
        "name": "dashboard_trend_alerts",
        "ok": isinstance(trend_res.get("alerts"), list),
        "alerts": len(trend_res.get("alerts") or []),
    })

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
