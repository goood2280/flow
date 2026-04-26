#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import polars as pl

from app import app
from core.utils import find_all_sources, read_source
from routers.dashboard import fab_progress, trend_alerts
from routers.ettime import et_report, _product_aliases
from routers.ml import InlineETReq, MLSourceReq, inline_et_overview, knob_lineage_summary
from routers.splittable import _build_inline_meta, _build_knob_meta, _build_vm_meta, _load_operational_history


def _pick_ml_source() -> dict:
    sources = find_all_sources()
    for s in sources:
        label = str(s.get("label", "")).upper()
        root = str(s.get("root", "")).upper()
        product = str(s.get("product", "")).upper()
        file = str(s.get("file", "")).upper()
        if "ML" in label or "ML_TABLE" in root or "ML_TABLE" in product or "ML_TABLE" in file:
            return s
    raise RuntimeError("No ML source found for smoke test")


def main() -> int:
    report: dict[str, object] = {"checks": []}

    ml_src = _pick_ml_source()
    ml_df = read_source(
        source_type=ml_src.get("source_type") or "base_file",
        root=ml_src.get("root") or "",
        product=ml_src.get("product") or "",
        file=ml_src.get("file") or "ML_TABLE_PRODA.parquet",
        max_files=1,
    )
    ml_available = ml_df is not None and not ml_df.is_empty()
    id_cols = [c for c in ("product", "root_lot_id", "wafer_id") if ml_df is not None and c in ml_df.columns]
    ml_available = ml_available and bool(id_cols)
    if ml_available and id_cols:
        first_row = ml_df.select(id_cols).row(0, named=True)
    else:
        first_row = {
            "product": str(ml_src.get("product") or "PRODA").replace("ML_TABLE_", ""),
            "root_lot_id": "",
            "wafer_id": "",
        }
    product_code = str(first_row.get("product") or ml_src.get("product") or "").replace("ML_TABLE_", "")

    schema_paths = {route.path for route in app.routes}
    required_paths = {
        "/api/ml/inline_et_overview",
        "/api/ml/knob_lineage_summary",
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

    if ml_available:
        inline_res = inline_et_overview(InlineETReq(
            source_type=ml_src.get("source_type") or "flat",
            root=ml_src.get("root") or "ML_TABLE",
            product=ml_src.get("product") or product_code,
            file=ml_src.get("file") or "",
        ))
        report["checks"].append({
            "name": "inline_et_overview",
            "ok": inline_res.get("rows", 0) > 0 and len(inline_res.get("knob_cards", [])) > 0,
            "rows": inline_res.get("rows", 0),
            "target_et": inline_res.get("target_et"),
            "top_inline": len(inline_res.get("top_inline", [])),
        })

        lineage = knob_lineage_summary(MLSourceReq(
            source_type=ml_src.get("source_type") or "flat",
            root=ml_src.get("root") or "ML_TABLE",
            product=ml_src.get("product") or product_code,
            file=ml_src.get("file") or "",
            max_knobs=6,
        ))
        report["checks"].append({
            "name": "knob_lineage_summary",
            "ok": lineage.get("row_count", 0) > 0 and len(lineage.get("knobs", [])) > 0,
            "rows": lineage.get("row_count", 0),
            "knobs": len(lineage.get("knobs", [])),
            "first_earliest_step": (lineage.get("knobs") or [{}])[0].get("earliest_step_id", ""),
        })
    else:
        report["checks"].append({"name": "inline_et_overview", "ok": True, "skipped": "empty ML_TABLE source"})
        report["checks"].append({"name": "knob_lineage_summary", "ok": True, "skipped": "empty ML_TABLE source"})

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
