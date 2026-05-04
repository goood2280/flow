"""routers/dashboard.py v6.1.0 — Background chart scheduler + lazy reads + snapshots + SPC
Designed for 2-core / 6GB with 30-50GB parquet datasets.
Charts are pre-computed every 10 min by a daemon thread; frontend fetches snapshots.
v6: spec lines (USL/LSL/Target), SPC control limits (UCL/LCL/CL), OOS alerts.
"""
import datetime, threading, time, logging, statistics, re, math
from pathlib import Path
import sys

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_APP_ROOT = _BACKEND_ROOT.parent
for _path in (_APP_ROOT, _BACKEND_ROOT):
    _raw = str(_path)
    sys.path[:] = [p for p in sys.path if p != _raw]
    sys.path.insert(0, _raw)

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from typing import Any, Optional
import polars as pl
from core.paths import PATHS
from core.long_pivot import scan_long_fab
from core.utils import (
    _STR, cast_cats, read_source, read_one_file, find_all_sources,
    apply_time_window, lazy_read_source,
    load_json, save_json, serialize_rows, first_data_file,
)
from core.runtime_limits import dashboard_scheduler_enabled
from core.auth import require_admin
from app_v2.shared.source_adapter import resolve_column
from core.dashboard_join import (
    apply_chart_defaults,
    build_multi_db_chart,
    dashboard_items,
    load_chart_defaults,
    refine_chart_session,
    save_chart_default,
)

logger = logging.getLogger("flow.dashboard")
router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
# NOTE: DB_BASE 는 삭제됨. admin 이 런타임에 db_root 를 변경해도 반영되도록
# 각 함수 내부에서 PATHS.db_root 를 직접 참조(lazy resolve).
CHARTS_FILE = PATHS.data_root / "dashboard_charts.json"
SNAP_FILE = PATHS.data_root / "dashboard_snapshots.json"
SETTINGS_FILE = PATHS.data_root / "settings.json"

MAX_POINTS = 5000
FAB_PROGRESS_DEFAULT_SAMPLE_LOTS = 3
FAB_PROGRESS_DEFAULT_REFERENCE_STEP = "AA200000"
FAB_PROGRESS_DEFAULT_DAYS = 30
_STEP_SIMPLE_RE = re.compile(r"^([A-Z]{2})(?:(\d{6}))?(\d{6})$")
DASHBOARD_SECTION_DEFAULTS = {"charts": True, "progress": False, "alerts": False}
FAB_PROGRESS_SETTINGS_DEFAULTS = {
    "reference_step_id": FAB_PROGRESS_DEFAULT_REFERENCE_STEP,
    "sample_lots": FAB_PROGRESS_DEFAULT_SAMPLE_LOTS,
    "days": FAB_PROGRESS_DEFAULT_DAYS,
}


def _dashboard_sections_config() -> dict:
    data = load_json(SETTINGS_FILE, {})
    raw = data.get("dashboard_sections") if isinstance(data, dict) else {}
    if not isinstance(raw, dict):
        raw = {}
    return {
        **DASHBOARD_SECTION_DEFAULTS,
        **{k: bool(v) for k, v in raw.items() if k in DASHBOARD_SECTION_DEFAULTS},
    }


def _dashboard_fab_progress_config() -> dict:
    data = load_json(SETTINGS_FILE, {})
    raw = data.get("dashboard_fab_progress") if isinstance(data, dict) else {}
    if not isinstance(raw, dict):
        raw = {}
    out = dict(FAB_PROGRESS_SETTINGS_DEFAULTS)
    ref = str(raw.get("reference_step_id") or out["reference_step_id"]).strip().upper()
    out["reference_step_id"] = ref or FAB_PROGRESS_DEFAULT_REFERENCE_STEP
    try:
        out["sample_lots"] = max(1, min(50, int(raw.get("sample_lots", out["sample_lots"]))))
    except Exception:
        out["sample_lots"] = FAB_PROGRESS_DEFAULT_SAMPLE_LOTS
    try:
        out["days"] = max(1, min(365, int(raw.get("days", out["days"]))))
    except Exception:
        out["days"] = FAB_PROGRESS_DEFAULT_DAYS
    return out


def _require_dashboard_section(request: Request, section: str) -> dict:
    from core.auth import current_user
    me = current_user(request)
    if me.get("role") == "admin":
        return me
    if not _dashboard_sections_config().get(section, False):
        raise HTTPException(403, f"dashboard {section} is admin only")
    return me


def _visible_charts_for_user(me: dict) -> list[dict]:
    from routers.groups import filter_by_visibility
    role = me.get("role", "user")
    charts = _charts()
    if role != "admin":
        charts = [c for c in charts if (c.get("visible_to") or "all") != "admin"]
    return filter_by_visibility(charts, me["username"], role, key="group_ids")


def _step_sort_key(v: str):
    s = str(v or "").strip().upper()
    m = _STEP_SIMPLE_RE.match(s)
    if m:
        family = int(m.group(2)) if m.group(2) else -1
        return (m.group(1), family, int(m.group(3)), s)
    return ("ZZ", 999999, 999999, s)


def _dashboard_product_aliases(product: str) -> list[str]:
    raw = str(product or "").strip()
    if not raw:
        return []
    out = {raw.upper()}
    if raw.upper() == "PRODA":
        out.update({"PRODA0", "PRODA1", "PRODUCT_A0", "PRODUCT_A1"})
    elif raw.upper() == "PRODA0":
        out.update({"PRODA", "PRODUCT_A0"})
    elif raw.upper() == "PRODA1":
        out.update({"PRODA", "PRODUCT_A1"})
    elif raw.upper().startswith("PRODUCT_A"):
        if raw.upper().endswith("0"):
            out.update({"PRODA", "PRODA0", "PRODUCT_A0"})
        elif raw.upper().endswith("1"):
            out.update({"PRODA", "PRODA1", "PRODUCT_A1"})
        else:
            out.update({"PRODA", "PRODA0", "PRODA1", "PRODUCT_A0", "PRODUCT_A1"})
    elif raw.upper() == "PRODB":
        out.update({"PRODUCT_B"})
    elif raw.upper().startswith("PRODUCT_B"):
        out.update({"PRODB", "PRODUCT_B"})
    return sorted(out)


def _scan_dashboard_fab_long(product: str):
    aliases = _dashboard_product_aliases(product)
    for cand in aliases or [product]:
        lf = scan_long_fab(cand, PATHS.db_root)
        if lf is None:
            continue
        try:
            names = lf.collect_schema().names()
        except Exception:
            names = []
        name_set = set(names)
        has_time = bool({"time", "tkout_time", "tkin_time"} & name_set)
        if {"root_lot_id", "step_id"}.issubset(name_set) and has_time:
            return lf
    return None


def _dashboard_wafer_layout(product: str) -> dict:
    """Return WF Layout geometry for dashboard wafer-map rendering."""
    prod = str(product or "").strip()
    if not prod:
        return {}
    try:
        from routers.waferlayout import _build_cfg, _collect_shots, _load_product_wafer_layout
        wafer_layout = _load_product_wafer_layout(prod)
        cfg = _build_cfg(wafer_layout)
        shots = _collect_shots(cfg)
        return {
            "product": prod,
            "cfg": cfg,
            "shots": shots,
            "shot_count": len(shots),
        }
    except Exception as e:
        logger.warning("dashboard wafer layout load failed (%s): %s", prod, e)
        return {}


def _ml_table_file_for_product(product: str) -> str:
    p = str(product or "").upper()
    return "ML_TABLE_PRODB.parquet" if "PRODB" in p or "PRODUCT_B" in p else "ML_TABLE_PRODA.parquet"


def _load_root_knob_map(product: str, knob_col: str = "") -> dict:
    """Return ROOT_LOT_ID -> knob value plus top knob options for dashboard FAB progress."""
    ml_file = _ml_table_file_for_product(product)
    requested = str(knob_col or "").strip() or "KNOB_5.0 PC"
    try:
        ml_df = read_source("base_file", "", "", ml_file)
        names = list(ml_df.columns)
        root_col = _resolve_name(names, "ROOT_LOT_ID") or _resolve_name(names, "root_lot_id")
        knob_resolved = _resolve_name(names, requested)
        if not root_col or not knob_resolved:
            return {"ok": False, "ml_file": ml_file, "knob_col": requested, "map": {}, "options": [], "values": []}
        kdf = (
            ml_df.select([
                pl.col(root_col).cast(_STR, strict=False).alias("_root_lot_id"),
                pl.col(knob_resolved).cast(_STR, strict=False).alias("_knob"),
            ])
            .drop_nulls(subset=["_root_lot_id"])
            .group_by("_root_lot_id")
            .agg(pl.col("_knob").drop_nulls().first().alias("_knob"))
        )
        pairs = {
            str(r.get("_root_lot_id") or ""): str(r.get("_knob") or "")
            for r in kdf.to_dicts()
            if r.get("_root_lot_id")
        }
        vc = (
            kdf.select(pl.col("_knob").drop_nulls())
            .get_column("_knob")
            .value_counts()
            .sort("count", descending=True)
            .head(24)
        )
        values = [
            {"value": str(r.get("_knob") or ""), "count": int(r.get("count", r.get("counts", 0)) or 0)}
            for r in vc.to_dicts()
            if r.get("_knob")
        ]
        options = [c for c in names if str(c).upper().startswith("KNOB_")]
        return {
            "ok": True,
            "ml_file": ml_file,
            "knob_col": knob_resolved,
            "map": pairs,
            "options": options[:80],
            "values": values,
        }
    except Exception as e:
        logger.warning("load root knob map failed product=%s knob=%s: %s", product, requested, e)
        return {"ok": False, "ml_file": ml_file, "knob_col": requested, "map": {}, "options": [], "values": []}


def _compute_fab_progress(product: str = "", days: int = 7, limit: int = 8,
                          target_step_id: str = "", lot_query: str = "",
                          sample_lots: int = FAB_PROGRESS_DEFAULT_SAMPLE_LOTS,
                          knob_col: str = "", knob_value: str = "",
                          reference_step_id: str = "") -> dict:
    fab_cfg = _dashboard_fab_progress_config()
    if not reference_step_id:
        reference_step_id = str(fab_cfg.get("reference_step_id") or FAB_PROGRESS_DEFAULT_REFERENCE_STEP)
    reference_step = str(reference_step_id or "").strip().upper()
    lf = _scan_dashboard_fab_long(product)
    sample_window = max(1, min(50, int(sample_lots or FAB_PROGRESS_DEFAULT_SAMPLE_LOTS)))
    if lf is None:
        return {
            "ok": False,
            "product": product,
            "source_mode": "fab_wide_only",
            "note": "FAB 공정이력(root_lot_id/wafer_id/step_id/tkin_time/tkout_time)을 찾지 못해 진행속도 TAT/ETA를 계산할 수 없습니다.",
            "wip_lots": [],
            "step_tat": [],
            "recent_paths": [],
            "target_benchmark": {},
            "search": {"lot_query": lot_query, "matched_lots": 0, "total_lots": 0},
            "summary": {"lots": 0, "transitions": 0, "days": days},
        }
    cols = lf.collect_schema().names()
    keep = [c for c in (
        "product", "root_lot_id", "lot_id", "fab_lot_id", "wafer_id", "step_id",
        "tkin_time", "tkout_time", "time", "eqp_id", "chamber_id", "reticle_id", "ppid",
    ) if c in cols]
    df = lf.select(keep).collect()
    if df.is_empty():
        return {
            "ok": False, "product": product, "source_mode": "fab_history", "note": "FAB 공정이력 데이터가 비어 있습니다.",
            "wip_lots": [], "step_tat": [], "recent_paths": [], "target_benchmark": {},
            "search": {"lot_query": lot_query, "matched_lots": 0, "total_lots": 0},
            "summary": {"lots": 0, "transitions": 0, "days": days},
        }
    time_col = "time" if "time" in df.columns else ("tkout_time" if "tkout_time" in df.columns else "tkin_time")
    if df.schema.get(time_col) != pl.Datetime:
        try:
            df = df.with_columns(pl.col(time_col).str.strptime(pl.Datetime, strict=False))
        except Exception:
            pass
    cutoff = datetime.datetime.now() - datetime.timedelta(days=max(1, int(days or 7)))
    if df.schema.get(time_col) == pl.Datetime:
        recent_df = df.filter(pl.col(time_col) >= cutoff)
    else:
        recent_df = df
    if recent_df.is_empty():
        recent_df = df
    data_now = None
    try:
        if recent_df.schema.get(time_col) == pl.Datetime:
            data_now = recent_df.select(pl.col(time_col).max()).item()
    except Exception:
        data_now = None
    root_key = "root_lot_id"
    lot_key = "fab_lot_id" if "fab_lot_id" in recent_df.columns else ("lot_id" if "lot_id" in recent_df.columns else "root_lot_id")
    lot_group_cols = list(dict.fromkeys([root_key, lot_key, "step_id"]))
    lot_aggs = [pl.col(time_col).min().alias("step_time")]
    if "product" in recent_df.columns:
        lot_aggs.append(pl.col("product").cast(_STR, strict=False).first().alias("product"))
    for meta_col in ("lot_id", "fab_lot_id", "wafer_id"):
        if meta_col in recent_df.columns and meta_col not in lot_group_cols:
            lot_aggs.append(pl.col(meta_col).cast(_STR, strict=False).first().alias(meta_col))
    lot_steps = (
        recent_df.group_by(lot_group_cols)
        .agg(lot_aggs)
        .sort([root_key, "step_time"])
    )
    rows = lot_steps.to_dicts()
    by_root = {}
    for r in rows:
        rk = str(r.get(root_key) or "")
        if not rk:
            continue
        by_root.setdefault(rk, []).append(r)
    transitions = []
    wip_lots = []
    recent_paths = []
    now = data_now if isinstance(data_now, datetime.datetime) else datetime.datetime.now()
    for rk, arr in by_root.items():
        arr = sorted(arr, key=lambda x: (x.get("step_time") or datetime.datetime.min, _step_sort_key(x.get("step_id") or "")))
        path = []
        for idx, cur in enumerate(arr):
            step = str(cur.get("step_id") or "")
            st = cur.get("step_time")
            path.append({
                "step_id": step,
                "time": st.isoformat() if hasattr(st, "isoformat") else str(st or ""),
                "lot_id": str(cur.get("lot_id") or cur.get(lot_key) or ""),
                "fab_lot_id": str(cur.get("fab_lot_id") or cur.get(lot_key) or ""),
                "wafer_id": str(cur.get("wafer_id") or ""),
            })
            if idx < len(arr) - 1:
                nxt = arr[idx + 1]
                if st and nxt.get("step_time"):
                    delta_h = (nxt["step_time"] - st).total_seconds() / 3600.0
                    if delta_h >= 0:
                        transitions.append({
                            "from_step": step,
                            "to_step": str(nxt.get("step_id") or ""),
                            "hours": delta_h,
                            "root_lot_id": rk,
                        })
        fab_lot_ids = []
        lot_ids = []
        wafer_ids = []
        for p in path:
            if p.get("fab_lot_id") and p.get("fab_lot_id") not in fab_lot_ids:
                fab_lot_ids.append(p.get("fab_lot_id"))
            if p.get("lot_id") and p.get("lot_id") not in lot_ids:
                lot_ids.append(p.get("lot_id"))
            if p.get("wafer_id") and p.get("wafer_id") not in wafer_ids:
                wafer_ids.append(p.get("wafer_id"))
        current_fab_lot_id = fab_lot_ids[-1] if fab_lot_ids else str(arr[-1].get(lot_key) or "")
        current_lot_id = lot_ids[-1] if lot_ids else str(arr[-1].get("lot_id") or "")
        recent_paths.append({
            "product": str(arr[-1].get("product") or product or ""),
            "root_lot_id": rk,
            "lot_id": current_lot_id,
            "fab_lot_id": current_fab_lot_id,
            "current_fab_lot_id": current_fab_lot_id,
            "fab_lot_ids": fab_lot_ids,
            "lot_ids": lot_ids,
            "wafer_ids": wafer_ids,
            "current_step_id": str(arr[-1].get("step_id") or ""),
            "current_time": arr[-1].get("step_time").isoformat() if hasattr(arr[-1].get("step_time"), "isoformat") else str(arr[-1].get("step_time") or ""),
            "path": path[-6:],
            "full_path": path,
        })
    tat_map = {}
    for tr in transitions:
        key = (tr["from_step"], tr["to_step"])
        tat_map.setdefault(key, []).append(float(tr["hours"]))
    step_tat = []
    for (frm, to), vals in tat_map.items():
        avg_h = sum(vals) / len(vals)
        med_h = statistics.median(vals)
        step_tat.append({
            "from_step": frm,
            "to_step": to,
            "avg_hours": round(avg_h, 2),
            "median_hours": round(med_h, 2),
            "samples": len(vals),
        })
    step_tat.sort(key=lambda x: (-x["samples"], x["avg_hours"]))
    next_by_from = {}
    for row in step_tat:
        if row["from_step"] not in next_by_from:
            next_by_from[row["from_step"]] = row

    def _target_eta_payload(cur_step: str, cur_iso: str, target_step_id: str) -> dict:
        tgt = str(target_step_id or "").strip().upper()
        if not tgt or not cur_step or not cur_iso:
            return {}
        try:
            base_dt = datetime.datetime.fromisoformat(cur_iso)
        except Exception:
            return {}
        if cur_step == tgt:
            return {
                "target_step_id": tgt,
                "avg_hours": 0.0,
                "eta_at": base_dt.isoformat(),
                "path": [cur_step],
            }
        seen = {cur_step}
        path = [cur_step]
        total_h = 0.0
        step = cur_step
        for _ in range(24):
            row = next_by_from.get(step)
            if not row:
                break
            nxt = str(row.get("to_step") or "")
            if not nxt or nxt in seen:
                break
            total_h += float(row.get("avg_hours") or 0.0)
            path.append(nxt)
            if nxt == tgt:
                eta_dt = base_dt + datetime.timedelta(hours=total_h)
                return {
                    "target_step_id": tgt,
                    "avg_hours": round(total_h, 2),
                    "eta_at": eta_dt.isoformat(),
                    "path": path,
                }
            seen.add(nxt)
            step = nxt
        return {}
    for row in recent_paths:
        cur = row["current_step_id"]
        cur_time = row["current_time"]
        eta = {}
        if cur in next_by_from and cur_time:
            try:
                base_dt = datetime.datetime.fromisoformat(cur_time)
                avg_h = float(next_by_from[cur]["avg_hours"])
                eta_dt = base_dt + datetime.timedelta(hours=avg_h)
                eta = {
                    "next_step_id": next_by_from[cur]["to_step"],
                    "avg_hours": avg_h,
                    "eta_at": eta_dt.isoformat(),
                    "elapsed_hours": round(max(0.0, (now - base_dt).total_seconds() / 3600.0), 2),
                }
            except Exception:
                eta = {"next_step_id": next_by_from[cur]["to_step"], "avg_hours": next_by_from[cur]["avg_hours"], "eta_at": ""}
        row["eta"] = eta
        wip_lots.append(row)
    max_path_len = max([len(r.get("path") or []) for r in recent_paths] or [1])
    baseline_units = []
    for row in wip_lots:
        path = row.get("path") or []
        started_at = ""
        if path:
            started_at = path[0].get("time") or row.get("current_time") or ""
        try:
            started_dt = datetime.datetime.fromisoformat(started_at) if started_at else now
            current_dt = datetime.datetime.fromisoformat(row.get("current_time") or started_at) if (row.get("current_time") or started_at) else now
        except Exception:
            started_dt = now
            current_dt = now
        elapsed_h = max(0.0, (current_dt - started_dt).total_seconds() / 3600.0)
        stuck_h = max(0.0, (now - current_dt).total_seconds() / 3600.0)
        progress_steps = max(1, len(path))
        unit_h = elapsed_h / progress_steps
        row["elapsed_hours"] = round(elapsed_h, 2)
        row["stuck_hours"] = round(stuck_h, 2)
        row["progress_steps"] = progress_steps
        row["progress_pct"] = round(100.0 * progress_steps / max_path_len, 1)
        row["speed_unit_hours"] = round(unit_h, 2)
        baseline_units.append(unit_h)
    avg_unit_h = sum(baseline_units) / len(baseline_units) if baseline_units else 0.0
    for row in wip_lots:
        unit_h = float(row.get("speed_unit_hours") or 0.0)
        stuck_h = float(row.get("stuck_hours") or 0.0)
        if stuck_h >= 24:
            state = "stuck"
            badge = "🔴 정체"
        elif avg_unit_h and unit_h <= avg_unit_h * 0.8:
            state = "fast"
            badge = "🟢 빠름"
        elif avg_unit_h and unit_h > avg_unit_h * 1.2:
            state = "slow"
            badge = "🟠 느림"
        else:
            state = "average"
            badge = "⚪ 평균"
        row["speed_state"] = state
        row["speed_badge"] = badge
    wip_lots.sort(key=lambda x: x.get("current_time") or "", reverse=True)
    recent_paths.sort(key=lambda x: x.get("current_time") or "", reverse=True)

    knob_payload = _load_root_knob_map(product, knob_col)
    knob_map = knob_payload.get("map") or {}
    for row in wip_lots:
        kval = str(knob_map.get(str(row.get("root_lot_id") or ""), "") or "")
        row["knob_col"] = knob_payload.get("knob_col") or (knob_col or "KNOB_5.0 PC")
        row["knob_value"] = kval
    if not knob_value and knob_payload.get("values"):
        knob_value = str((knob_payload["values"][0] or {}).get("value") or "")

    target_step = str(target_step_id or "").strip().upper()
    lot_q = str(lot_query or "").strip().upper()

    def _parse_dt(v):
        if isinstance(v, datetime.datetime):
            return v
        if isinstance(v, datetime.date):
            return datetime.datetime.combine(v, datetime.time.min)
        s = str(v or "").strip()
        if not s:
            return None
        try:
            return datetime.datetime.fromisoformat(s)
        except Exception:
            return None

    def _iso(v):
        dt = _parse_dt(v)
        return dt.isoformat() if dt else str(v or "")

    def _matches_lot(row: dict) -> bool:
        if not lot_q:
            return True
        hay = [
            row.get("root_lot_id"), row.get("fab_lot_id"), row.get("lot_id"),
            row.get("current_fab_lot_id"), row.get("current_step_id"), row.get("product"),
        ]
        hay.extend(row.get("fab_lot_ids") or [])
        hay.extend(row.get("lot_ids") or [])
        hay.extend(row.get("wafer_ids") or [])
        for p in row.get("full_path") or []:
            hay.extend([p.get("fab_lot_id"), p.get("lot_id"), p.get("wafer_id")])
        return any(lot_q in str(v or "").upper() for v in hay)

    display_lots = [row for row in wip_lots if _matches_lot(row)]
    display_paths = [row for row in recent_paths if _matches_lot(row)]

    def _pct(vals: list[float], q: float) -> float:
        if not vals:
            return 0.0
        vals = sorted(vals)
        if len(vals) == 1:
            return vals[0]
        pos = (len(vals) - 1) * q
        lo = math.floor(pos)
        hi = math.ceil(pos)
        if lo == hi:
            return vals[lo]
        return vals[lo] * (hi - pos) + vals[hi] * (pos - lo)

    def _norm(v) -> str:
        return str(v or "").strip().upper()

    def _lot_product(arr: list[dict]) -> str:
        for item in reversed(arr or []):
            p = _norm(item.get("product"))
            if p:
                return p
        return _norm(product)

    def _root_step_timeline(root_id: str) -> list[dict]:
        """Root lot 기준 step 최초 도착 timeline. FAB lot/wafer 중복 step은 접는다."""
        per_step = {}
        for item in by_root.get(str(root_id), []) or []:
            sid = _norm(item.get("step_id"))
            dt = _parse_dt(item.get("step_time"))
            if not sid or not dt:
                continue
            prev = per_step.get(sid)
            if prev and prev.get("_dt") <= dt:
                continue
            per_step[sid] = {
                "step_id": sid,
                "time": dt.isoformat(),
                "_dt": dt,
                "product": _norm(item.get("product")),
                "lot_id": str(item.get("lot_id") or item.get(lot_key) or ""),
                "fab_lot_id": str(item.get("fab_lot_id") or item.get(lot_key) or ""),
            }
        return sorted(per_step.values(), key=lambda r: (r.get("_dt") or datetime.datetime.min, _step_sort_key(r.get("step_id") or "")))

    def _timeline_transitions(timeline: list[dict]) -> list[dict]:
        out = []
        for idx in range(len(timeline) - 1):
            cur = timeline[idx]
            nxt = timeline[idx + 1]
            cur_dt = cur.get("_dt")
            nxt_dt = nxt.get("_dt")
            if not cur_dt or not nxt_dt:
                continue
            hours = (nxt_dt - cur_dt).total_seconds() / 3600.0
            if hours < 0:
                continue
            out.append({
                "from_step": cur.get("step_id") or "",
                "to_step": nxt.get("step_id") or "",
                "hours": round(hours, 2),
                "from_time": cur.get("time") or "",
                "to_time": nxt.get("time") or "",
            })
        return out

    def _recent_reference_samples(anchor_root: str, anchor_product: str) -> tuple[list[dict], int]:
        candidates = []
        for rk in by_root.keys():
            rk_s = str(rk or "")
            if not rk_s or rk_s == str(anchor_root or ""):
                continue
            timeline = _root_step_timeline(rk_s)
            if len(timeline) < 2:
                continue
            row_product = _lot_product(timeline)
            if anchor_product and row_product and row_product != anchor_product:
                continue
            if reference_step:
                hits = [row for row in timeline if _norm(row.get("step_id")) == reference_step]
                if not hits:
                    continue
                basis_row = hits[-1]
            else:
                basis_row = timeline[-1]
            basis_dt = basis_row.get("_dt")
            if not basis_dt:
                continue
            candidates.append({
                "root_lot_id": rk_s,
                "reference_step_id": reference_step,
                "reference_time": basis_dt.isoformat(),
                "_reference_dt": basis_dt,
                "current_step_id": timeline[-1].get("step_id") if timeline else "",
                "current_time": timeline[-1].get("time") if timeline else "",
                "timeline": timeline,
            })
        candidates.sort(key=lambda r: r.get("_reference_dt") or datetime.datetime.min, reverse=True)
        return candidates[:sample_window], len(candidates)

    def _progress_points(timeline: list[dict]) -> list[dict]:
        if not timeline:
            return []
        start_dt = timeline[0].get("_dt")
        out = []
        for idx, row in enumerate(timeline):
            dt = row.get("_dt")
            elapsed = ((dt - start_dt).total_seconds() / 3600.0) if start_dt and dt else 0.0
            out.append({
                "index": idx + 1,
                "step_id": row.get("step_id") or "",
                "elapsed_hours": round(max(0.0, elapsed), 2),
                "time": row.get("time") or "",
            })
        return out

    def _progress_chart_payload(anchor_timeline: list[dict], sample_specs: list[dict]) -> dict:
        anchor_points = _progress_points(anchor_timeline)
        average_points = []
        for point in anchor_points:
            step = _norm(point.get("step_id"))
            vals = []
            lots = []
            for spec in sample_specs:
                timeline = spec.get("timeline") or []
                if not timeline:
                    continue
                start_dt = timeline[0].get("_dt")
                hit = next((row for row in timeline if _norm(row.get("step_id")) == step), None)
                hit_dt = hit.get("_dt") if hit else None
                if not start_dt or not hit_dt:
                    continue
                elapsed = (hit_dt - start_dt).total_seconds() / 3600.0
                if elapsed < 0:
                    continue
                vals.append(elapsed)
                lots.append({
                    "root_lot_id": spec.get("root_lot_id") or "",
                    "elapsed_hours": round(elapsed, 2),
                    "time": hit.get("time") or "",
                })
            average_points.append({
                "index": point.get("index"),
                "step_id": point.get("step_id"),
                "avg_elapsed_hours": round(sum(vals) / len(vals), 2) if vals else None,
                "median_elapsed_hours": round(statistics.median(vals), 2) if vals else None,
                "sample_count": len(vals),
                "lots": lots[:8],
            })
        return {
            "kind": "lot_vs_reference_progress",
            "reference_step_id": reference_step,
            "sample_window": sample_window,
            "anchor": anchor_points,
            "average": average_points,
        }

    def _step_speed_compare_payload() -> dict:
        if not lot_q:
            return {
                "lot_query": lot_query,
                "sample_window": sample_window,
                "reference_step_id": reference_step,
                "rows": [],
                "progress_chart": {},
                "target_eta": {},
                "note": "root_lot_id 또는 fab_lot_id를 검색하면 최근 lots 평균과 비교합니다.",
            }
        if not display_lots:
            return {
                "lot_query": lot_query,
                "sample_window": sample_window,
                "reference_step_id": reference_step,
                "rows": [],
                "progress_chart": {},
                "target_eta": {},
                "note": "검색한 root_lot_id/fab_lot_id와 매칭되는 FAB 이력이 없습니다.",
            }
        anchor = display_lots[0]
        anchor_root = str(anchor.get("root_lot_id") or "")
        anchor_product = _norm(anchor.get("product"))
        anchor_timeline = _root_step_timeline(anchor_root)
        anchor_transitions = _timeline_transitions(anchor_timeline)

        sample_specs, sample_pool = _recent_reference_samples(anchor_root, anchor_product)
        sample_roots = [str(s.get("root_lot_id") or "") for s in sample_specs if s.get("root_lot_id")]

        sample_by_transition = {}
        sample_rows = []
        for spec in sample_specs:
            rk = str(spec.get("root_lot_id") or "")
            timeline = spec.get("timeline") or []
            sample_rows.append({
                "root_lot_id": rk,
                "reference_step_id": reference_step,
                "reference_time": spec.get("reference_time") or "",
                "current_step_id": timeline[-1].get("step_id") if timeline else "",
                "current_time": timeline[-1].get("time") if timeline else "",
            })
            for tr in _timeline_transitions(timeline):
                key = (tr.get("from_step"), tr.get("to_step"))
                sample_by_transition.setdefault(key, []).append({
                    "root_lot_id": rk,
                    "hours": float(tr.get("hours") or 0.0),
                    "from_time": tr.get("from_time") or "",
                    "to_time": tr.get("to_time") or "",
                })

        compare_rows = []
        for idx, tr in enumerate(anchor_transitions):
            key = (tr.get("from_step"), tr.get("to_step"))
            samples = sample_by_transition.get(key) or []
            vals = [float(s.get("hours") or 0.0) for s in samples if float(s.get("hours") or 0.0) >= 0]
            avg_h = round(sum(vals) / len(vals), 2) if vals else None
            searched_h = float(tr.get("hours") or 0.0)
            diff_h = round(searched_h - avg_h, 2) if avg_h is not None else None
            ratio = round(searched_h / avg_h, 3) if avg_h and avg_h > 0 else None
            compare_rows.append({
                "index": idx + 1,
                "from_step": tr.get("from_step"),
                "to_step": tr.get("to_step"),
                "label": f"{tr.get('from_step')} → {tr.get('to_step')}",
                "searched_hours": round(searched_h, 2),
                "avg_hours": avg_h,
                "median_hours": round(statistics.median(vals), 2) if vals else None,
                "sample_count": len(vals),
                "diff_hours": diff_h,
                "ratio": ratio,
                "searched_from_time": tr.get("from_time"),
                "searched_to_time": tr.get("to_time"),
            })

        target_eta = {}
        if target_step:
            target_idx = next((i for i, row in enumerate(anchor_timeline) if _norm(row.get("step_id")) == target_step), None)
            if target_idx is not None:
                actual = anchor_timeline[target_idx]
                target_eta = {
                    "status": "reached",
                    "target_step_id": target_step,
                    "actual_time": actual.get("time") or "",
                    "eta_at": actual.get("time") or "",
                    "days_from_current": 0,
                }
            elif anchor_timeline:
                current = anchor_timeline[-1]
                cur_step = _norm(current.get("step_id"))
                durations = []
                recent_lots = []
                for rk in sample_roots:
                    timeline = _root_step_timeline(rk)
                    cur_idx = next((i for i, row in enumerate(timeline) if _norm(row.get("step_id")) == cur_step), None)
                    tgt_idx = next((i for i, row in enumerate(timeline) if _norm(row.get("step_id")) == target_step), None)
                    if cur_idx is None or tgt_idx is None or tgt_idx <= cur_idx:
                        continue
                    cur_dt = timeline[cur_idx].get("_dt")
                    tgt_dt = timeline[tgt_idx].get("_dt")
                    if not cur_dt or not tgt_dt:
                        continue
                    hours = (tgt_dt - cur_dt).total_seconds() / 3600.0
                    if hours < 0:
                        continue
                    durations.append(hours)
                    recent_lots.append({
                        "root_lot_id": rk,
                        "from_step_id": cur_step,
                        "target_step_id": target_step,
                        "hours": round(hours, 2),
                        "from_time": timeline[cur_idx].get("time") or "",
                        "target_time": timeline[tgt_idx].get("time") or "",
                    })
                if durations:
                    avg_h = sum(durations) / len(durations)
                    base_dt = current.get("_dt")
                    eta_dt = base_dt + datetime.timedelta(hours=avg_h) if base_dt else None
                    target_eta = {
                        "status": "estimated",
                        "target_step_id": target_step,
                        "from_step_id": cur_step,
                        "avg_hours": round(avg_h, 2),
                        "avg_days": round(avg_h / 24.0, 2),
                        "eta_at": eta_dt.isoformat() if eta_dt else "",
                        "samples": len(durations),
                        "sample_window": sample_window,
                        "recent_lots": recent_lots,
                    }
                else:
                    target_eta = {
                        "status": "insufficient_history",
                        "target_step_id": target_step,
                        "from_step_id": cur_step,
                        "samples": 0,
                        "note": "최근 비교 lots에서 현재 step→target step 이력이 없습니다.",
                    }

        return {
            "lot_query": lot_query,
            "target_root_lot_id": anchor_root,
            "target_fab_lot_id": anchor.get("current_fab_lot_id") or anchor.get("fab_lot_id") or "",
            "target_product": anchor.get("product") or product or "",
            "current_step_id": anchor_timeline[-1].get("step_id") if anchor_timeline else anchor.get("current_step_id") or "",
            "current_time": anchor_timeline[-1].get("time") if anchor_timeline else anchor.get("current_time") or "",
            "speed_unit_hours": anchor.get("speed_unit_hours"),
            "speed_state": anchor.get("speed_state"),
            "speed_badge": anchor.get("speed_badge"),
            "elapsed_hours": anchor.get("elapsed_hours"),
            "progress_steps": anchor.get("progress_steps"),
            "progress_pct": anchor.get("progress_pct"),
            "reference_step_id": reference_step,
            "sample_window": sample_window,
            "sample_pool": sample_pool,
            "sample_lots": sample_rows,
            "progress_chart": _progress_chart_payload(anchor_timeline, sample_specs),
            "rows": compare_rows,
            "target_eta": target_eta,
        }

    step_speed_compare = _step_speed_compare_payload()

    def _recent_target_duration_payload(from_step: str, target_step_id: str,
                                        row_product: str = "",
                                        exclude_root: str = "") -> dict:
        frm = _norm(from_step)
        tgt = _norm(target_step_id)
        prod_norm = _norm(row_product)
        if not frm or not tgt:
            return {
                "samples": 0,
                "historical_samples": 0,
                "sample_window": sample_window,
                "recent_lots": [],
            }
        samples = []
        for rk, raw_arr in by_root.items():
            if exclude_root and str(rk) == str(exclude_root):
                continue
            arr = sorted(raw_arr, key=lambda x: (_parse_dt(x.get("step_time")) or datetime.datetime.min, _step_sort_key(x.get("step_id") or "")))
            arr_product = _lot_product(arr)
            if prod_norm and arr_product and arr_product != prod_norm:
                continue
            best = None
            for target_idx, target_row in enumerate(arr):
                if _norm(target_row.get("step_id")) != tgt:
                    continue
                from_candidates = [
                    idx for idx in range(0, target_idx + 1)
                    if _norm(arr[idx].get("step_id")) == frm
                ]
                if not from_candidates:
                    continue
                from_idx = from_candidates[-1]
                from_dt = _parse_dt(arr[from_idx].get("step_time"))
                target_dt = _parse_dt(target_row.get("step_time"))
                if not from_dt or not target_dt:
                    continue
                delta_h = (target_dt - from_dt).total_seconds() / 3600.0
                if delta_h < 0:
                    continue
                sample = {
                    "product": arr_product,
                    "root_lot_id": rk,
                    "fab_lot_id": str(target_row.get(lot_key) or ""),
                    "from_step_id": str(arr[from_idx].get("step_id") or ""),
                    "target_step_id": tgt,
                    "hours": round(delta_h, 2),
                    "from_time": _iso(arr[from_idx].get("step_time")),
                    "target_time": _iso(target_row.get("step_time")),
                }
                if best is None or (sample.get("target_time") or "") > (best.get("target_time") or ""):
                    best = sample
            if best:
                samples.append(best)
        samples.sort(key=lambda r: r.get("target_time") or "", reverse=True)
        recent = samples[:sample_window]
        vals = [float(s["hours"]) for s in recent]
        payload = {
            "target_step_id": tgt,
            "from_step_id": frm,
            "product": prod_norm,
            "basis": "recent_lots_average",
            "basis_label": f"최근 {sample_window} lots 평균",
            "sample_window": sample_window,
            "samples": len(vals),
            "historical_samples": len(samples),
            "recent_lots": recent,
        }
        if vals:
            payload.update({
                "avg_hours": round(sum(vals) / len(vals), 2),
                "median_hours": round(statistics.median(vals), 2),
                "p10_hours": round(_pct(vals, 0.10), 2),
                "p90_hours": round(_pct(vals, 0.90), 2),
                "min_hours": round(min(vals), 2),
                "max_hours": round(max(vals), 2),
            })
        else:
            payload["note"] = "같은 제품에서 현재 step→target step을 통과한 최근 랏 이력이 없습니다."
        return payload

    target_benchmark = {}
    if target_step:
        anchor = display_lots[0] if display_lots else (wip_lots[0] if wip_lots else {})
        from_step = _norm(anchor.get("current_step_id"))
        row_product = _norm(anchor.get("product"))
        target_benchmark = _recent_target_duration_payload(
            from_step,
            target_step,
            row_product=row_product,
            exclude_root=str(anchor.get("root_lot_id") or ""),
        )
        for row in wip_lots:
            full_path = row.get("full_path") or row.get("path") or []
            reached_rows = [p for p in full_path if _norm(p.get("step_id")) == target_step]
            if reached_rows:
                reached = reached_rows[-1]
                row["target_eta"] = {
                    "status": "reached",
                    "target_step_id": target_step,
                    "actual_time": reached.get("time") or "",
                    "eta_at": reached.get("time") or "",
                    "path": [p.get("step_id") for p in full_path],
                }
                continue
            cur_step = _norm(row.get("current_step_id"))
            duration = _recent_target_duration_payload(
                cur_step,
                target_step,
                row_product=_norm(row.get("product")),
                exclude_root=str(row.get("root_lot_id") or ""),
            )
            if duration.get("samples"):
                base_dt = _parse_dt(row.get("current_time"))
                avg_h = float(duration.get("avg_hours") or 0.0)
                row["target_eta"] = {
                    "status": "estimated",
                    "target_step_id": target_step,
                    "from_step_id": cur_step,
                    "avg_hours": duration.get("avg_hours"),
                    "median_hours": duration.get("median_hours"),
                    "samples": duration.get("samples"),
                    "historical_samples": duration.get("historical_samples"),
                    "basis": duration.get("basis"),
                    "basis_label": duration.get("basis_label"),
                    "recent_lots": duration.get("recent_lots"),
                    "eta_at": (base_dt + datetime.timedelta(hours=avg_h)).isoformat() if base_dt else "",
                }
            else:
                row["target_eta"] = {
                    "status": "insufficient_history",
                    "target_step_id": target_step,
                    "from_step_id": cur_step,
                    "samples": 0,
                    "note": duration.get("note", ""),
                }

    def _knob_progress_payload() -> dict:
        kcol = str(knob_payload.get("knob_col") or knob_col or "KNOB_5.0 PC")
        kval = str(knob_value or "").strip()
        rows_for_knob = [r for r in wip_lots if (not kval or str(r.get("knob_value") or "") == kval)]
        bins = {}
        for row in rows_for_knob:
            step = str(row.get("current_step_id") or "(no step)")
            cur = bins.setdefault(step, {
                "step_id": step,
                "lot_count": 0,
                "root_lot_ids": [],
                "fab_lot_ids": [],
                "avg_unit_hours": 0.0,
                "fast_lots": 0,
                "slow_lots": 0,
                "stuck_lots": 0,
                "_unit_sum": 0.0,
            })
            cur["lot_count"] += 1
            rk = str(row.get("root_lot_id") or "")
            fk = str(row.get("current_fab_lot_id") or row.get("fab_lot_id") or "")
            if rk and rk not in cur["root_lot_ids"]:
                cur["root_lot_ids"].append(rk)
            if fk and fk not in cur["fab_lot_ids"]:
                cur["fab_lot_ids"].append(fk)
            unit_h = float(row.get("speed_unit_hours") or 0.0)
            cur["_unit_sum"] += unit_h
            state = row.get("speed_state") or ""
            if state == "fast":
                cur["fast_lots"] += 1
            elif state == "slow":
                cur["slow_lots"] += 1
            elif state == "stuck":
                cur["stuck_lots"] += 1
        step_bins = []
        total = len(rows_for_knob)
        for step, row in bins.items():
            cnt = int(row.get("lot_count") or 0)
            avg_h = (float(row.pop("_unit_sum", 0.0)) / cnt) if cnt else 0.0
            row["avg_unit_hours"] = round(avg_h, 2)
            row["pct"] = round(cnt / max(1, total) * 100.0, 1)
            row["root_lot_ids"] = row["root_lot_ids"][:8]
            row["fab_lot_ids"] = row["fab_lot_ids"][:8]
            step_bins.append(row)
        step_bins.sort(key=lambda r: _step_sort_key(r.get("step_id") or ""))
        fastest_bins = sorted(
            [r for r in step_bins if r.get("lot_count")],
            key=lambda r: (float(r.get("avg_unit_hours") or 999999), -int(r.get("lot_count") or 0)),
        )[:5]
        lots = sorted(rows_for_knob, key=lambda r: (float(r.get("speed_unit_hours") or 999999), r.get("current_time") or ""))[:12]
        return {
            "ok": bool(knob_payload.get("ok")),
            "ml_file": knob_payload.get("ml_file"),
            "knob_col": kcol,
            "knob_value": kval,
            "knob_values": knob_payload.get("values") or [],
            "knob_options": knob_payload.get("options") or [],
            "total_lots": total,
            "step_bins": step_bins,
            "fastest_bins": fastest_bins,
            "lots": [{
                "root_lot_id": r.get("root_lot_id"),
                "fab_lot_id": r.get("current_fab_lot_id") or r.get("fab_lot_id"),
                "current_step_id": r.get("current_step_id"),
                "current_time": r.get("current_time"),
                "speed_state": r.get("speed_state"),
                "speed_unit_hours": r.get("speed_unit_hours"),
                "target_eta": r.get("target_eta") or {},
            } for r in lots],
        }

    return {
        "ok": True,
        "product": product,
        "source_mode": "fab_long",
        "note": f"최근 {days}일 기준 FAB step 통과시간으로 평균 TAT와 다음 step ETA를 계산합니다.",
        "summary": {
            "lots": len(recent_paths),
            "transitions": len(transitions),
            "days": days,
            "reference_step_id": reference_step,
            "avg_elapsed_hours": round(sum(float(r.get("elapsed_hours") or 0.0) for r in wip_lots) / max(1, len(wip_lots)), 2),
            "stuck_lots": sum(1 for r in wip_lots if r.get("speed_state") == "stuck"),
            "slow_lots": sum(1 for r in wip_lots if r.get("speed_state") == "slow"),
            "avg_unit_hours": round(avg_unit_h, 2),
            "matched_lots": len(display_lots),
            "eta_sample_lots": sample_window,
        },
        "wip_lots": display_lots[:limit],
        "step_tat": step_tat[:12],
        "recent_paths": display_paths[:limit],
        "target_benchmark": target_benchmark,
        "step_speed_compare": step_speed_compare,
        "knob_progress": _knob_progress_payload(),
        "search": {
            "lot_query": lot_query,
            "matched_lots": len(display_lots),
            "total_lots": len(wip_lots),
            "reference_step_id": reference_step,
            "sample_lots": sample_window,
        },
    }


def _compute_dashboard_summary(product: str = "") -> dict:
    week = _compute_fab_progress(product=product, days=7, limit=200)
    month = _compute_fab_progress(product=product, days=30, limit=200)
    week_lots = week.get("wip_lots") or []
    month_lots = month.get("wip_lots") or []

    def _dpml(rows: list[dict]) -> float:
        total = len(rows)
        if total <= 0:
            return 0.0
        issues = sum(1 for row in rows if row.get("speed_state") in {"slow", "stuck"})
        return round((issues / total) * 1_000_000, 1)

    return {
        "ok": True,
        "product": product,
        "tat_7d_hours": week.get("summary", {}).get("avg_elapsed_hours", 0),
        "tat_30d_hours": month.get("summary", {}).get("avg_elapsed_hours", 0),
        "dpml_7d": _dpml(week_lots),
        "dpml_30d": _dpml(month_lots),
        "wip_lots": len(month_lots),
        "stuck_lots": sum(1 for row in month_lots if row.get("speed_state") == "stuck"),
        "slow_lots": sum(1 for row in month_lots if row.get("speed_state") == "slow"),
        "source_mode": month.get("source_mode"),
        "note": month.get("note", ""),
    }


def _detect_trend_alert(points: list, chart_type: str = "", title: str = "") -> dict:
    ct = str(chart_type or "").lower()
    if ct not in {"scatter", "line", "area", "combo"}:
        return {"count": 0, "latest": [], "method": "off"}
    ys = []
    for idx, p in enumerate(points or []):
        try:
            yv = float(p.get("y"))
        except Exception:
            continue
        if math.isnan(yv) or math.isinf(yv):
            continue
        ys.append((idx, yv, p))
    if len(ys) < 8:
        return {"count": 0, "latest": [], "method": "insufficient_points"}
    vals = [y for _, y, _ in ys]
    try:
        q = pl.DataFrame({"y": vals}).select([
            pl.col("y").quantile(0.25).alias("q1"),
            pl.col("y").quantile(0.75).alias("q3"),
        ]).to_dicts()[0]
        q1 = float(q.get("q1"))
        q3 = float(q.get("q3"))
    except Exception:
        q1, q3 = statistics.quantiles(vals, n=4)[0], statistics.quantiles(vals, n=4)[2]
    iqr = max(1e-9, q3 - q1)
    lo = q1 - 1.5 * iqr
    hi = q3 + 1.5 * iqr
    flagged = []
    for idx, yv, p in ys:
        if yv < lo or yv > hi:
            flagged.append({
                "index": idx,
                "x": p.get("x"),
                "y": yv,
                "color": p.get("color") or p.get("series") or "",
            })
    flagged.sort(key=lambda x: x["index"], reverse=True)
    return {
        "count": len(flagged),
        "latest": flagged[:5],
        "method": "iqr_1.5",
        "bounds": {"low": round(lo, 6), "high": round(hi, 6)},
        "title": title,
    }


def _trend_alert_candidates(charts: list, snapshots: dict, limit: int = 8) -> list:
    out = []
    for c in charts or []:
        snap = (snapshots or {}).get(c.get("id")) or {}
        if snap.get("error"):
            continue
        trend = _detect_trend_alert(snap.get("points") or [], c.get("chart_type") or "", c.get("title") or "")
        oos = int(snap.get("oos_count") or 0)
        if oos <= 0 and trend.get("count", 0) <= 0:
            continue
        out.append({
            "chart_id": c.get("id"),
            "title": c.get("title") or c.get("id") or "",
            "group": c.get("group") or "기타",
            "chart_type": c.get("chart_type") or "",
            "oos_count": oos,
            "trend_outliers": trend.get("count", 0),
            "latest_points": trend.get("latest", []),
            "computed_at": snap.get("computed_at") or "",
            "source": c.get("file") or f'{c.get("root") or ""}/{c.get("product") or ""}',
        })
    out.sort(key=lambda x: (-(x.get("oos_count") or 0), -(x.get("trend_outliers") or 0), x.get("title") or ""))
    return out[:max(1, int(limit or 8))]


def _notify_dashboard_watchers(cfg: dict, title: str, body: str, alert_key: str = ""):
    recipients = set()
    try:
        from core.notify import send_notify, send_to_admins
        gids = [str(g).strip() for g in (cfg.get("group_ids") or []) if str(g).strip()]
        if gids:
            from routers.groups import _load as _load_groups  # type: ignore
            for g in _load_groups():
                gid = str(g.get("id") or "").strip()
                if gid in gids:
                    owner = str(g.get("owner") or "").strip()
                    if owner:
                        recipients.add(owner)
                    for m in (g.get("members") or []):
                        sm = str(m).strip()
                        if sm:
                            recipients.add(sm)
        if recipients:
            body2 = body + (f"\nalert={alert_key}" if alert_key else "")
            for un in sorted(recipients):
                send_notify(un, title, body2, "warn")
        else:
            send_to_admins(title, body, "warn")
    except Exception:
        pass


def _resolve_name(pool, name: str) -> str:
    if not name:
        return ""
    hit = resolve_column(list(pool or []), name)
    return (hit.matched if hit else "") or ""


def _resolve_name_csv(pool, raw: str) -> str:
    if not raw:
        return ""
    parts = [s.strip() for s in str(raw).split(",") if s.strip()]
    if not parts:
        return ""
    return ",".join([_resolve_name(pool, p) or p for p in parts])


def _resolve_simple_y_expr(pool, raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    safe_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_, ")
    if any(ch not in safe_chars for ch in text):
        return text
    return _resolve_name_csv(pool, text)


# ──────────────────────────────────────────────────────────────────
# Chart config model
# ──────────────────────────────────────────────────────────────────
class ChartConfig(BaseModel):
    id: str = ""
    title: str = ""
    source: str = ""  # inform = use /api/informs/dashboard-data adapter
    metric: str = ""
    groupby: str = ""
    period: str = "all"
    inform_product: str = ""
    inform_module: str = ""
    x_groupby: str = ""
    y_groupby: str = ""
    series_groupby: str = ""
    top_n: Optional[int] = None
    source_type: str = ""
    root: str = ""
    product: str = ""
    file: str = ""
    x_col: str = ""
    y_expr: str = ""
    time_col: str = ""
    days: Optional[int] = None
    chart_type: str = "scatter"  # scatter/line/bar/pie/binning/box/area/pareto/donut/treemap/wafer_map/combo/heatmap/step_knob_binning
    filter_expr: str = ""
    agg_col: str = ""
    agg_method: str = ""  # mean/sum/count/min/max
    color_col: str = ""
    layout_product: str = ""  # wafer_map 배경 WF Layout product override
    x_label: str = ""
    y_label: str = ""
    bin_count: Optional[int] = None
    bin_width: Optional[float] = None
    visible_to: str = "all"  # all / admin
    no_schedule: bool = False  # True = skip background refresh
    exclude_null: bool = True  # v8.1.5: filter null/"(null)"/empty from x_col and y_expr before plotting
    point_size: Optional[int] = None
    opacity: Optional[float] = None
    sort_x: bool = False
    limit_points: Optional[int] = None
    # v7.2: Cross-chart marking key — usually LOT_WF (wafer) or ROOT_LOT_ID (lot).
    # When set, each point carries this column's value; the frontend "global selection" state
    # highlights matching points across all charts sharing the same selection_key.
    selection_key: str = "LOT_WF"
    # v6: Spec lines (legacy single USL/LSL/Target)
    usl: Optional[float] = None       # Upper Specification Limit
    lsl: Optional[float] = None       # Lower Specification Limit
    target: Optional[float] = None    # Target / Center value
    # v7: Spec lines (multi). Each: {name, value, color, style: "solid"|"dashed", kind: "usl"|"lsl"|"target"|"custom"}
    spec_lines: list = []
    # v6: SPC
    enable_spc: bool = False          # Enable Statistical Process Control lines
    # v8.1.1: LEFT JOIN additional sources into main dataframe before chart compute.
    # Each join: {source_type, root, product, file, left_on:[...], right_on:[...], suffix:"_j1"}
    # Applied sequentially; missing right-side columns that collide with left are suffixed.
    joins: list = []
    # Dashboard analysis helpers.
    # derive_eqp_chamber builds a stable categorical column from eqp/chamber after optional joins.
    derive_eqp_chamber: bool = False
    eqp_col: str = "eqp"
    chamber_col: str = "chamber"
    # step_knob_binning overlays current FAB step lot counts with a selected ML_TABLE knob ratio.
    ml_file: str = ""
    knob_col: str = ""
    knob_value: str = ""
    # v8.4.8: Layout fields — group/phase sections + grid span.
    # width 1..4 = 가로 열수, height 1..4 = 세로 크기. Legacy 차트는 (1,1).
    group: str = ""
    width: int = 1
    height: int = 1
    # v8.5.0: User group visibility. 비어있으면 public (모든 유저). 값이 있으면
    # 해당 그룹 멤버만 볼 수 있음. admin 은 항상 전체.
    group_ids: list = []


class ChartDefaultReq(BaseModel):
    chart_type: str
    config: dict[str, Any]


class MultiDbChartReq(BaseModel):
    primary_source: str = "ET"
    secondary_source: str = ""
    x_item: str = ""
    y_item: str = ""
    items: list = []
    chart_type: str = "scatter"
    product: str = ""
    root_lot_ids: list = []
    color_by: str = "none"
    group_by: str = ""
    fit: str = "none"
    stats: Any = None
    font_size: int = 14


class ChartRefineReq(BaseModel):
    chart_session_id: str
    action: str
    value: Any = None


class DashboardLayoutReq(BaseModel):
    layout: Any = None


def _charts():
    return load_json(CHARTS_FILE, [])


def _new_id():
    # v8.4.8: microseconds 포함 (시드 스크립트에서 같은 초에 여러 개 POST 하면 충돌해서 덮어씌워지던 버그)
    return f"chart_{datetime.datetime.now().strftime('%Y%m%d%H%M%S%f')}"


# ──────────────────────────────────────────────────────────────────
# Chart computation (memory-efficient)
# ──────────────────────────────────────────────────────────────────
def _compute_binning(df, x_col, bin_count=None, bin_width=None):
    if x_col not in df.columns:
        return []
    col = df[x_col]
    try:
        vals = col.cast(pl.Float64, strict=False).drop_nulls().to_list()
        if not vals:
            raise ValueError("no numeric data")
        vmin, vmax = min(vals), max(vals)
        if bin_width and bin_width > 0:
            bins = []
            v = vmin
            while v <= vmax:
                bins.append(v)
                v += bin_width
            bins.append(vmax + bin_width)
        else:
            n = bin_count or 10
            step = (vmax - vmin) / n if vmax > vmin else 1
            bins = [vmin + i * step for i in range(n + 1)]
        counts = [0] * (len(bins) - 1)
        for v in vals:
            for i in range(len(bins) - 1):
                if bins[i] <= v < bins[i + 1] or (i == len(bins) - 2 and v == bins[i + 1]):
                    counts[i] += 1
                    break
        return [{"x": f"{bins[i]:.2g}~{bins[i+1]:.2g}", "y": counts[i],
                 "label": f"{bins[i]:.3g}"} for i in range(len(counts))]
    except Exception:
        pass
    vc = col.cast(_STR, strict=False).value_counts().sort("count", descending=True).head(30)
    return [{"x": str(d.get(x_col, "") or "(null)"),
             "y": int(d.get("count", d.get("counts", 0))),
             "label": str(d.get(x_col, ""))} for d in vc.to_dicts()]


def _compute_step_knob_binning(df: pl.DataFrame, cfg: dict, names: list[str], x_col: str, time_col: str) -> dict:
    """Current FAB step distribution with ML_TABLE knob loading ratio per step."""
    root_col = _resolve_name(names, cfg.get("root_col", "") or "root_lot_id")
    step_col = _resolve_name(names, x_col or cfg.get("x_col", "") or "step_id")
    sort_time_col = _resolve_name(names, time_col or cfg.get("time_col", "") or "") \
        or _resolve_name(names, "tkout_time") or _resolve_name(names, "time") or _resolve_name(names, "tkin_time")
    if not root_col or not step_col:
        return {"points": [], "total": 0, "error": "step_knob_binning requires root_lot_id and step_id"}

    keep_cols = [root_col, step_col]
    if sort_time_col:
        keep_cols.append(sort_time_col)
    for meta_col in ("fab_lot_id", "lot_id", "wafer_id", "product"):
        mc = _resolve_name(names, meta_col)
        if mc and mc not in keep_cols:
            keep_cols.append(mc)
    ldf = df.select([c for c in keep_cols if c in df.columns]).drop_nulls(subset=[root_col, step_col])
    if ldf.is_empty():
        return {"points": [], "total": 0, "error": None}

    sort_col = sort_time_col if sort_time_col in ldf.columns else ""
    if sort_col:
        try:
            ldf = ldf.with_columns(
                pl.col(sort_col).cast(_STR, strict=False).str.strptime(pl.Datetime, strict=False).alias("_sort_time")
            ).sort(["_sort_time", step_col])
        except Exception:
            ldf = ldf.sort([sort_col, step_col])
    else:
        ldf = ldf.sort(step_col)
    latest = ldf.unique(subset=[root_col], keep="last")

    ml_file = str(cfg.get("ml_file") or "").strip()
    if not ml_file:
        product = str(cfg.get("product") or "").upper()
        ml_file = "ML_TABLE_PRODB.parquet" if "PRODB" in product else "ML_TABLE_PRODA.parquet"
    knob_col_raw = str(cfg.get("knob_col") or "").strip()
    if not knob_col_raw:
        knob_col_raw = "KNOB_5.0 PC"
    try:
        ml_df = read_source("base_file", "", "", ml_file)
        ml_names = list(ml_df.columns)
        ml_root_col = _resolve_name(ml_names, "ROOT_LOT_ID") or _resolve_name(ml_names, "root_lot_id")
        knob_col = _resolve_name(ml_names, knob_col_raw)
        if not ml_root_col or not knob_col:
            raise ValueError(f"ML_TABLE columns not found: {ml_file} / {knob_col_raw}")
        ml_root = (
            ml_df.select([
                pl.col(ml_root_col).cast(_STR, strict=False).alias("_ml_root_lot_id"),
                pl.col(knob_col).cast(_STR, strict=False).alias("_knob"),
            ])
            .drop_nulls(subset=["_ml_root_lot_id"])
            .group_by("_ml_root_lot_id")
            .agg(pl.col("_knob").drop_nulls().first().alias("_knob"))
        )
        joined = latest.with_columns(pl.col(root_col).cast(_STR, strict=False)).join(
            ml_root, left_on=root_col, right_on="_ml_root_lot_id", how="left"
        )
        knob_value = str(cfg.get("knob_value") or "").strip()
        if not knob_value:
            vc = (
                joined.select(pl.col("_knob").drop_nulls())
                .get_column("_knob")
                .value_counts()
                .sort("count", descending=True)
            )
            if vc.height:
                knob_value = str(vc.row(0)[0])
        joined = joined.with_columns(
            (pl.col("_knob").cast(_STR, strict=False) == knob_value).cast(pl.Int64).alias("_knob_hit")
        )
        grouped = joined.group_by(step_col).agg([
            pl.len().alias("_lot_count"),
            pl.col("_knob_hit").sum().alias("_knob_count"),
            pl.col("_knob").drop_nulls().n_unique().alias("_knob_types"),
        ])
        rows = []
        for row in grouped.to_dicts():
            total = int(row.get("_lot_count") or 0)
            hit = int(row.get("_knob_count") or 0)
            pct = (hit / total * 100.0) if total else 0.0
            step = str(row.get(step_col) or "")
            rows.append({
                "x": step,
                "label": step,
                "bar": total,
                "line": round(pct, 2),
                "y": total,
                "lot_count": total,
                "knob_pct": round(pct, 2),
                "knob_count": hit,
                "knob_value": knob_value,
                "knob_col": knob_col,
                "knob_types": int(row.get("_knob_types") or 0),
            })
        rows.sort(key=lambda r: _step_sort_key(r.get("x") or ""))
        return {
            "points": rows[:80],
            "total": len(rows),
            "error": None,
            "step_knob_meta": {
                "ml_file": ml_file,
                "knob_col": knob_col,
                "knob_value": knob_value,
                "root_lots": int(latest.height),
            },
        }
    except Exception as e:
        return {"points": [], "total": 0, "error": f"step_knob_binning error: {e}"}


def _pushdown_dashboard_filters(lf: pl.LazyFrame, cfg: dict, schema_cols: set[str] | None = None) -> tuple[pl.LazyFrame, dict]:
    """Apply chart filters before collect when Polars can push them into parquet scan."""
    schema_cols = schema_cols or set()
    pushed = {"filter_expr": False, "time_window": False}
    fe = str(cfg.get("filter_expr") or "").strip()
    if fe:
        try:
            lf = lf.filter(pl.sql_expr(fe))
            pushed["filter_expr"] = True
        except Exception:
            pushed["filter_expr"] = False

    time_col = str(cfg.get("time_col") or "").strip()
    days = cfg.get("days")
    if time_col and days and (not schema_cols or time_col in schema_cols):
        try:
            d_int = int(days)
            cutoff = (datetime.datetime.now() - datetime.timedelta(days=d_int)).isoformat()
            lf = lf.filter(pl.col(time_col).cast(_STR, strict=False) >= cutoff)
            pushed["time_window"] = True
        except Exception:
            pushed["time_window"] = False
    return lf, pushed


_DASHBOARD_EXPR_STOP = {
    "and", "or", "not", "null", "true", "false", "is", "in", "like", "as", "case", "when", "then", "else",
    "pl", "col", "lit", "str", "dt", "cast", "alias", "mean", "sum", "min", "max", "count", "len",
}


def _dashboard_expr_tokens(raw: Any) -> set[str]:
    out: set[str] = set()
    for token in re.findall(r"[A-Za-z_][A-Za-z0-9_. ]*", str(raw or "")):
        tok = token.strip()
        if not tok or tok.lower() in _DASHBOARD_EXPR_STOP:
            continue
        out.add(tok)
    return out


def _dashboard_referenced_columns(cfg: dict) -> set[str]:
    refs: set[str] = set()
    for key in ("x_col", "y_expr", "color_col", "agg_col", "time_col", "selection_key", "eqp_col", "chamber_col", "filter_expr"):
        refs.update(_dashboard_expr_tokens(cfg.get(key) or ""))
    for key in ("table_columns", "cross_cols", "cross_rows"):
        vals = cfg.get(key) or []
        if isinstance(vals, str):
            vals = [v.strip() for v in vals.split(",") if v.strip()]
        if isinstance(vals, list):
            for val in vals:
                refs.update(_dashboard_expr_tokens(val))
    return refs


def _join_projection_columns(schema_cols: list[str], right_on: list[str], cfg: dict, suffix: str) -> list[str]:
    include: list[str] = []

    def add(raw: str):
        hit = _resolve_name(schema_cols, raw) or raw
        if hit in schema_cols and hit not in include:
            include.append(hit)

    for key in right_on:
        add(key)
    refs = _dashboard_referenced_columns(cfg)
    for ref in refs:
        candidates = [ref]
        if suffix and ref.endswith(suffix):
            candidates.append(ref[:-len(suffix)])
        for cand in candidates:
            hit = _resolve_name(schema_cols, cand)
            if hit:
                add(hit)
    if str(cfg.get("chart_type") or "").lower() == "table" and len(include) <= len(right_on):
        for col in schema_cols[:12]:
            add(col)
    return include or schema_cols


def _load_dashboard_join_right_source(jst: str, jroot: str, jprod: str, jfile: str,
                                      right_on: list[str], cfg: dict, suffix: str) -> tuple[pl.DataFrame, list[str]]:
    lf = lazy_read_source(jst, jroot, jprod, jfile)
    if lf is not None:
        try:
            schema_cols = list(lf.collect_schema().names())
            resolved_right_on = [_resolve_name(schema_cols, c) or c for c in right_on]
            keep = _join_projection_columns(schema_cols, resolved_right_on, cfg, suffix)
            lf = lf.select([pl.col(c) for c in keep if c in schema_cols])
            try:
                from core.parquet_perf import collect_streaming
                return cast_cats(collect_streaming(lf)), resolved_right_on
            except Exception:
                return cast_cats(lf.collect()), resolved_right_on
        except Exception:
            pass
    right_df = read_source(jst, jroot, jprod, jfile)
    resolved_right_on = [_resolve_name(right_df.columns, c) or c for c in right_on]
    return right_df, resolved_right_on


def _compute_chart(cfg: dict) -> dict:
    """Compute one chart. Uses lazy reads for memory efficiency."""
    chart_id = cfg.get("id", "")
    result = {"chart_id": chart_id, "config": cfg, "points": [], "total": 0,
              "computed_at": datetime.datetime.now().isoformat(), "error": None}
    try:
        if str(cfg.get("source") or "").strip().lower() == "inform":
            from routers.informs import build_inform_dashboard_data
            payload = build_inform_dashboard_data(
                metric=cfg.get("metric") or "count",
                groupby=cfg.get("groupby") or "module",
                period=cfg.get("period") or "all",
                product=cfg.get("inform_product") or "",
                module=cfg.get("inform_module") or "",
                x_groupby=cfg.get("x_groupby") or "",
                y_groupby=cfg.get("y_groupby") or "",
                series_groupby=cfg.get("series_groupby") or "",
                top_n=cfg.get("top_n"),
                chart_type=cfg.get("chart_type") or "",
            )
            result["points"] = payload.get("points") or []
            result["total"] = len(result["points"])
            result["chart_type"] = cfg.get("chart_type") or ""
            result["series_order"] = payload.get("series_order") or []
            result["meta"] = payload.get("meta") or {}
            heatmap_meta = (payload.get("meta") or {}).get("heatmap_meta") or {}
            if heatmap_meta:
                result["heatmap_meta"] = heatmap_meta
            table_columns = (payload.get("meta") or {}).get("table_columns") or []
            if table_columns:
                result["table_columns"] = table_columns
            return result

        # v7.2: If reformatter rules exist for this product, we need FULL schema (not pushed-down),
        # because rules may reference raw columns (ITEM_ID, VALUE, A/B/C/D) that chart doesn't.
        product_name = cfg.get("product", "") or (cfg.get("file", "").rsplit("_", 1)[-1].split(".")[0] if cfg.get("file") else "")
        has_reformatter = False
        reformatter_rules = []
        try:
            from core.reformatter import load_rules as _rf_load
            from core.paths import PATHS as _PATHS
            reformatter_rules = _rf_load(_PATHS.data_root / "reformatter", product_name) if product_name else []
            has_reformatter = len(reformatter_rules) > 0
        except Exception:
            pass

        lf = lazy_read_source(
            cfg.get("source_type", ""), cfg.get("root", ""),
            cfg.get("product", ""), cfg.get("file", ""))

        if lf is not None and not has_reformatter:
            # Push down column selection (memory-efficient)
            x_col = cfg.get("x_col", "")
            y_expr = cfg.get("y_expr", "")
            cc = cfg.get("color_col") or cfg.get("agg_col", "")
            time_col = cfg.get("time_col", "")
            chart_type_for_projection = str(cfg.get("chart_type") or "").lower()
            needed = set()
            for c in [x_col, cc, time_col]:
                if c:
                    needed.add(c)
            if y_expr:
                for c in [s.strip() for s in str(y_expr or "").split(",") if s.strip()]:
                    needed.add(c)
            filter_expr = str(cfg.get("filter_expr") or "")
            if filter_expr:
                for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", filter_expr):
                    if token.lower() not in {"and", "or", "not", "null", "true", "false", "is", "in", "like"}:
                        needed.add(token)
            if chart_type_for_projection == "table":
                for c in (cfg.get("table_columns") or []):
                    if c:
                        needed.add(c)
                for raw in [x_col, y_expr]:
                    for c in [s.strip() for s in str(raw or "").split(",") if s.strip()]:
                        needed.add(c)
            if chart_type_for_projection == "wafer_map":
                needed.update({"product", "wafer_id", "lot_id", "root_lot_id"})
            if chart_type_for_projection == "step_knob_binning":
                needed.update({"product", "root_lot_id", "lot_id", "fab_lot_id", "wafer_id", "step_id", "time", "tkin_time", "tkout_time"})
            if cfg.get("derive_eqp_chamber"):
                needed.update({
                    cfg.get("eqp_col") or "eqp",
                    cfg.get("chamber_col") or "chamber",
                    "eqp", "chamber", "eqp_id", "chamber_id",
                })
            # v8.1.1: keep join keys in the pushed-down projection
            for j in (cfg.get("joins") or []):
                left_keys = (j or {}).get("left_on") or []
                if isinstance(left_keys, str):
                    left_keys = [c.strip() for c in left_keys.split(",") if c.strip()]
                for k in left_keys:
                    if k:
                        needed.add(k)
            pushed = {"filter_expr": False, "time_window": False}
            try:
                schema_cols = lf.collect_schema().names()
                needed = {c for c in needed if c in schema_cols}
                if needed:
                    lf = lf.select([pl.col(c) for c in needed])
                lf, pushed = _pushdown_dashboard_filters(lf, cfg, set(schema_cols))
            except Exception:
                pass
            try:
                try:
                    from core.parquet_perf import collect_streaming
                    df = cast_cats(collect_streaming(lf))
                except Exception:
                    df = cast_cats(lf.collect())
            except Exception:
                pushed = {"filter_expr": False, "time_window": False}
                df = read_source(cfg.get("source_type", ""), cfg.get("root", ""),
                                 cfg.get("product", ""), cfg.get("file", ""))
        else:
            pushed = {"filter_expr": False, "time_window": False}
            df = read_source(cfg.get("source_type", ""), cfg.get("root", ""),
                             cfg.get("product", ""), cfg.get("file", ""))

        # Apply reformatter rules so derived indices become valid x_col / y_expr / color_col
        if has_reformatter:
            try:
                from core.reformatter import apply_rules
                df = apply_rules(df, reformatter_rules, enabled_only=True)
            except Exception as e:
                logger.warning(f"Reformatter apply failed on chart {chart_id}: {e}")

        names = list(df.columns)
        x_col = _resolve_name(names, cfg.get("x_col", ""))
        y_expr = _resolve_simple_y_expr(names, cfg.get("y_expr", ""))
        cc = _resolve_name(names, cfg.get("color_col") or cfg.get("agg_col", ""))
        time_col = _resolve_name(names, cfg.get("time_col", ""))
        sel_key = _resolve_name(names, cfg.get("selection_key", "LOT_WF") or "")

        # Time window filter
        if not pushed.get("time_window"):
            df = apply_time_window(df, time_col, cfg.get("days"))
        names = list(df.columns)

        # v8.1.1: LEFT JOIN additional sources (applied AFTER reformatter+time, BEFORE sql filter)
        joins = cfg.get("joins") or []
        for ji, j in enumerate(joins):
            try:
                jst = (j or {}).get("source_type", "")
                jroot = (j or {}).get("root", "")
                jprod = (j or {}).get("product", "")
                jfile = (j or {}).get("file", "")
                left_on = (j or {}).get("left_on") or []
                right_on = (j or {}).get("right_on") or []
                if isinstance(left_on, str): left_on = [c.strip() for c in left_on.split(",") if c.strip()]
                if isinstance(right_on, str): right_on = [c.strip() for c in right_on.split(",") if c.strip()]
                left_on = [_resolve_name(df.columns, c) or c for c in left_on]
                if not left_on or not right_on:
                    logger.warning(f"chart {chart_id} join {ji}: missing left_on/right_on")
                    continue
                if len(left_on) != len(right_on):
                    logger.warning(f"chart {chart_id} join {ji}: key length mismatch L={left_on} R={right_on}")
                    continue
                suffix = (j or {}).get("suffix") or f"_j{ji+1}"
                # Pull only right keys plus columns referenced downstream.
                right_df, right_on = _load_dashboard_join_right_source(jst, jroot, jprod, jfile, right_on, cfg, suffix)
                # Cast join keys to string on both sides for safety (categorical / int mismatches)
                for lk, rk in zip(left_on, right_on):
                    if lk in df.columns:
                        df = df.with_columns(pl.col(lk).cast(_STR, strict=False))
                    if rk in right_df.columns:
                        right_df = right_df.with_columns(pl.col(rk).cast(_STR, strict=False))
                # Drop right-side rows with null keys to avoid explosion
                try:
                    right_df = right_df.drop_nulls(subset=right_on)
                except Exception:
                    pass
                # Deduplicate right side on keys (keep first) to guarantee LEFT JOIN 1:1-ish behavior
                try:
                    right_df = right_df.unique(subset=right_on, keep="first")
                except Exception:
                    pass
                df = df.join(right_df, left_on=left_on, right_on=right_on, how="left", suffix=suffix)
            except Exception as e:
                logger.warning(f"chart {chart_id} join {ji} failed: {e}")
                result["error"] = f"join {ji} failed: {e}"

        if cfg.get("derive_eqp_chamber"):
            try:
                eqp_col = (
                    _resolve_name(df.columns, cfg.get("eqp_col") or "")
                    or _resolve_name(df.columns, "eqp")
                    or _resolve_name(df.columns, "eqp_id")
                )
                chamber_col = (
                    _resolve_name(df.columns, cfg.get("chamber_col") or "")
                    or _resolve_name(df.columns, "chamber")
                    or _resolve_name(df.columns, "chamber_id")
                )
                if eqp_col and chamber_col:
                    df = df.with_columns(
                        (
                            pl.col(eqp_col).cast(_STR, strict=False)
                            + pl.lit(" / ")
                            + pl.col(chamber_col).cast(_STR, strict=False)
                        ).alias("eqp_chamber")
                    )
            except Exception as e:
                logger.warning(f"chart {chart_id} eqp_chamber derive failed: {e}")
        names = list(df.columns)
        x_col = _resolve_name(names, x_col or cfg.get("x_col", ""))
        y_expr = _resolve_simple_y_expr(names, y_expr or cfg.get("y_expr", ""))
        cc = _resolve_name(names, cc or (cfg.get("color_col") or cfg.get("agg_col", "")))
        time_col = _resolve_name(names, time_col or cfg.get("time_col", ""))
        sel_key = _resolve_name(names, sel_key or (cfg.get("selection_key", "LOT_WF") or ""))

        # SQL filter
        fe = (cfg.get("filter_expr") or "").strip()
        if fe and not pushed.get("filter_expr"):
            try:
                df = df.filter(pl.sql_expr(fe))
            except Exception:
                pass

        ct = cfg.get("chart_type", "scatter")

        # v8.1.5: exclude_null — filter rows where x_col / y_expr are null/empty/"(null)"/"NaN"
        # Default True. Applied to all chart types before compute (value_counts filter for categorical too).
        if cfg.get("exclude_null", True):
            NULL_STRS = ["(null)", "null", "NULL", "None", "NaN", "nan", ""]
            for _col in [x_col, y_expr]:
                if not _col or _col not in df.columns:
                    continue
                try:
                    dtype = str(df.schema.get(_col, ""))
                    # Numeric: drop_nulls + drop NaN
                    if any(nt in dtype for nt in ("Int", "Float", "Decimal")):
                        df = df.filter(pl.col(_col).is_not_null() & pl.col(_col).is_not_nan()) \
                            if "Float" in dtype else df.filter(pl.col(_col).is_not_null())
                    else:
                        # String/Categorical: drop nulls + literal null-like strings
                        df = df.filter(pl.col(_col).is_not_null())
                        df = df.filter(~pl.col(_col).cast(_STR, strict=False).is_in(NULL_STRS))
                except Exception as _ex:
                    logger.debug(f"chart {chart_id} exclude_null on {_col}: {_ex}")

        if ct == "step_knob_binning":
            payload = _compute_step_knob_binning(df, cfg, list(df.columns), x_col, time_col)
            result["points"] = payload.get("points") or []
            result["total"] = payload.get("total") or 0
            result["chart_type"] = ct
            result["step_knob_meta"] = payload.get("step_knob_meta") or {}
            if payload.get("error"):
                result["error"] = payload.get("error")
            return result

        if ct in ("scatter", "line", "area"):
            sort_col = time_col if time_col in df.columns else ""
            if not sort_col and x_col in df.columns and "time" in str(x_col).lower():
                sort_col = x_col
            if sort_col:
                try:
                    df = df.with_columns(
                        pl.col(sort_col).cast(_STR, strict=False).str.strptime(pl.Datetime, strict=False).alias("_chart_sort_time")
                    ).sort("_chart_sort_time")
                except Exception:
                    try:
                        df = df.sort(sort_col)
                    except Exception:
                        pass

        # Pie / Donut / Binning / Pareto / Treemap
        if ct in ("binning", "pie", "donut", "pareto", "treemap") and x_col:
            if ct == "binning":
                points = _compute_binning(df, x_col, cfg.get("bin_count"), cfg.get("bin_width"))
            else:
                col = df[x_col].cast(_STR, strict=False)
                vc = col.value_counts().sort("count", descending=True).head(30)
                _exn = cfg.get("exclude_null", True)
                _nullset = {"(null)", "null", "NULL", "None", "NaN", "nan", ""}
                points = []
                for d in vc.to_dicts():
                    xv = str(d.get(x_col, "") or "(null)")
                    if _exn and (xv in _nullset or d.get(x_col) is None):
                        continue
                    points.append({"x": xv,
                                   "y": int(d.get("count", d.get("counts", 0))),
                                   "label": str(d.get(x_col, ""))})
            # Pareto: add cumulative %
            if ct == "pareto":
                total = sum(p["y"] for p in points) or 1
                cum = 0
                for p in points:
                    cum += p["y"]
                    p["cum_pct"] = round(cum / total * 100, 1)
            result["points"] = points
            result["total"] = len(points)
            result["chart_type"] = ct
            return result

        # Box plot: compute Q1, median, Q3, min, max per group
        if ct == "box" and x_col and y_expr and y_expr in df.columns:
            try:
                grp = df.group_by(x_col).agg([
                    pl.col(y_expr).count().alias("count"),
                    pl.col(y_expr).mean().alias("mean"),
                    pl.col(y_expr).median().alias("median"),
                    pl.col(y_expr).std().alias("std"),
                    pl.col(y_expr).min().alias("min"),
                    pl.col(y_expr).quantile(0.10, interpolation="linear").alias("p10"),
                    pl.col(y_expr).quantile(0.25, interpolation="linear").alias("q1"),
                    pl.col(y_expr).quantile(0.75, interpolation="linear").alias("q3"),
                    pl.col(y_expr).quantile(0.90, interpolation="linear").alias("p90"),
                    pl.col(y_expr).max().alias("max"),
                ]).sort(x_col).head(30)
                points = []
                for d in grp.to_dicts():
                    points.append({k: (round(v, 4) if isinstance(v, float) else v)
                                   for k, v in d.items()})
                    points[-1]["x"] = str(d[x_col])
                result["points"] = points
                result["total"] = len(points)
                return result
            except Exception:
                pass

        # Wafer Map: x_col=shot_x, y_expr=shot_y, color_col/agg_col=value.
        # v9.0.2: product WF Layout geometry is included so FE can draw the wafer/shot
        # background exactly as configured in WF Layout, then color only measured shots.
        if ct == "wafer_map" and x_col and y_expr:
            try:
                val_col = cc or _resolve_name(names, cfg.get("agg_col", "") or "") or _resolve_name(names, "value") or ""
                needed = [x_col, y_expr]
                if val_col in df.columns:
                    needed.append(val_col)
                if "product" in df.columns:
                    needed.append("product")
                if "wafer_id" in df.columns:
                    needed.append("wafer_id")
                if "lot_id" in df.columns:
                    needed.append("lot_id")
                if "root_lot_id" in df.columns:
                    needed.append("root_lot_id")
                cols_avail = list(dict.fromkeys(c for c in needed if c in df.columns))
                wdf = df.select(cols_avail)
                method = (cfg.get("agg_method") or "mean").lower().strip()
                is_numeric_val = False
                if val_col and val_col in wdf.columns:
                    try:
                        is_numeric_val = wdf.select(pl.col(val_col).cast(pl.Float64, strict=False).is_not_null().any()).item()
                    except Exception:
                        is_numeric_val = False
                if val_col and val_col in wdf.columns:
                    if is_numeric_val:
                        val_expr = pl.col(val_col).cast(pl.Float64, strict=False)
                        if method == "sum":
                            agg_expr = val_expr.sum().alias("_val")
                        elif method == "min":
                            agg_expr = val_expr.min().alias("_val")
                        elif method == "max":
                            agg_expr = val_expr.max().alias("_val")
                        elif method == "count":
                            agg_expr = pl.len().alias("_val")
                        else:
                            agg_expr = val_expr.mean().alias("_val")
                    else:
                        agg_expr = pl.col(val_col).cast(_STR, strict=False).first().alias("_val")
                else:
                    agg_expr = pl.len().alias("_val")
                    method = "count"
                group_cols = [x_col, y_expr]
                meta_aggs = [agg_expr, pl.len().alias("_count")]
                for meta_col in ("product", "wafer_id", "lot_id", "root_lot_id"):
                    if meta_col in wdf.columns and meta_col not in group_cols:
                        meta_aggs.append(pl.col(meta_col).cast(_STR, strict=False).first().alias(meta_col))
                wdf = wdf.group_by(group_cols).agg(meta_aggs)
                if wdf.height > 5000:
                    wdf = wdf.sample(5000, seed=42)
                layout_product = (
                    str(cfg.get("layout_product") or "").strip()
                    or str(cfg.get("product") or "").strip()
                )
                if not layout_product and "product" in wdf.columns and wdf.height:
                    try:
                        layout_product = str(wdf.select(pl.col("product").drop_nulls().first()).item() or "").strip()
                    except Exception:
                        layout_product = ""
                layout = _dashboard_wafer_layout(layout_product)
                shots = layout.get("shots") or []
                by_grid = {}
                by_raw = {}
                for shot in shots:
                    try:
                        by_grid[(int(shot.get("gridShotX")), int(shot.get("gridShotY")))] = shot
                    except Exception:
                        pass
                    try:
                        by_raw[(int(shot.get("shotX")), int(shot.get("shotY")))] = shot
                    except Exception:
                        pass
                points = []
                for row in wdf.to_dicts():
                    try:
                        sx = int(float(row.get(x_col, 0)))
                        sy = int(float(row.get(y_expr, 0)))
                        matched = by_grid.get((sx, sy)) or by_raw.get((sx, sy)) or {}
                        val = row.get("_val")
                        if isinstance(val, float):
                            val = round(val, 6)
                        p = {
                            "x": sx,
                            "y": sy,
                            "val": val,
                            "count": int(row.get("_count") or 0),
                            "product": row.get("product") or layout_product,
                            "wafer_id": row.get("wafer_id", ""),
                            "lot_id": row.get("lot_id", ""),
                            "root_lot_id": row.get("root_lot_id", ""),
                            "matched_layout": bool(matched),
                        }
                        if matched:
                            p.update({
                                "gridShotX": matched.get("gridShotX"),
                                "gridShotY": matched.get("gridShotY"),
                                "raw_shot_x": matched.get("shotX"),
                                "raw_shot_y": matched.get("shotY"),
                                "shotBody": matched.get("shotBody"),
                                "centerX": matched.get("centerX"),
                                "centerY": matched.get("centerY"),
                                "completely_inside": matched.get("completely_inside"),
                            })
                        points.append({
                            **p,
                        })
                    except Exception:
                        pass
                result["points"] = points
                result["total"] = len(points)
                result["wafer_layout"] = layout
                result["wafer_map_meta"] = {
                    "product": layout_product,
                    "x_col": x_col,
                    "y_col": y_expr,
                    "value_col": val_col or "count",
                    "agg_method": method,
                    "matched_points": sum(1 for p in points if p.get("matched_layout")),
                }
                return result
            except Exception:
                pass

        # Combo (bar+line): x_col=x, y_expr=bar_values, agg_col=line_values
        if ct == "combo" and x_col and y_expr:
            try:
                sel_cols = [x_col]
                if y_expr in df.columns:
                    sel_cols.append(y_expr)
                line_col = cfg.get("agg_col", "")
                if line_col and line_col in df.columns:
                    sel_cols.append(line_col)
                cdf = df.select([c for c in sel_cols if c in df.columns])
                if cdf.height > 5000:
                    cdf = cdf.sample(5000, seed=42)
                points = []
                for row in cdf.to_dicts():
                    p = {"x": str(row.get(x_col, "")), "bar": None, "line": None}
                    try:
                        p["bar"] = float(row.get(y_expr, 0))
                    except Exception:
                        pass
                    if line_col and line_col in row:
                        try:
                            p["line"] = float(row.get(line_col, 0))
                        except Exception:
                            pass
                    points.append(p)
                result["points"] = points
                result["total"] = len(points)
                return result
            except Exception:
                pass

        # Table: just serialize rows (first N) as-is
        if ct == "table":
            try:
                sel_cols = []
                for c in (cfg.get("table_columns") or []):
                    if c in df.columns and c not in sel_cols:
                        sel_cols.append(c)
                # x_col can be comma-separated for multi-column display
                if x_col:
                    for c in [c.strip() for c in x_col.split(",") if c.strip()]:
                        if c in df.columns and c not in sel_cols:
                            sel_cols.append(c)
                if y_expr:
                    for c in [c.strip() for c in y_expr.split(",") if c.strip()]:
                        if c in df.columns and c not in sel_cols:
                            sel_cols.append(c)
                if not sel_cols:
                    sel_cols = list(df.columns)[:12]
                tdf = df.select(sel_cols).head(200)
                result["points"] = serialize_rows(tdf.to_dicts())
                result["total"] = df.height
                result["table_columns"] = sel_cols
                return result
            except Exception as e:
                result["error"] = f"Table error: {e}"
                return result

        # Cross Table (pivot): x_col = row dim, y_expr = col dim, agg_col = value, agg_method = aggregation
        if ct == "cross_table" and x_col and y_expr:
            try:
                row_col, col_col = x_col, y_expr
                val_col = cfg.get("agg_col") or ""
                method = (cfg.get("agg_method") or "count").lower()

                if row_col not in df.columns or col_col not in df.columns:
                    result["error"] = f"Row/Col column not found"
                    return result

                # Get unique row/col values (limited)
                row_vals = df[row_col].cast(_STR, strict=False).unique().sort().head(30).to_list()
                col_vals = df[col_col].cast(_STR, strict=False).unique().sort().head(20).to_list()

                # Build grouped aggregation
                agg_expr = None
                if method == "count" or not val_col or val_col not in df.columns:
                    agg_expr = pl.count().alias("val")
                elif method == "sum":
                    agg_expr = pl.col(val_col).cast(pl.Float64, strict=False).sum().alias("val")
                elif method == "mean":
                    agg_expr = pl.col(val_col).cast(pl.Float64, strict=False).mean().alias("val")
                elif method == "min":
                    agg_expr = pl.col(val_col).cast(pl.Float64, strict=False).min().alias("val")
                elif method == "max":
                    agg_expr = pl.col(val_col).cast(pl.Float64, strict=False).max().alias("val")
                else:
                    agg_expr = pl.count().alias("val")

                grp = df.select([
                    pl.col(row_col).cast(_STR, strict=False).alias("_r"),
                    pl.col(col_col).cast(_STR, strict=False).alias("_c"),
                    *([pl.col(val_col).cast(pl.Float64, strict=False).alias(val_col)] if (val_col and val_col in df.columns) else [])
                ]).group_by(["_r", "_c"]).agg(agg_expr)

                # Build pivot dict: {row: {col: val}}
                pivot = {}
                for d in grp.to_dicts():
                    r, c, v = str(d.get("_r", "")), str(d.get("_c", "")), d.get("val")
                    if r not in pivot:
                        pivot[r] = {}
                    if isinstance(v, float):
                        v = round(v, 4)
                    pivot[r][c] = v

                # Build rows in order
                rows_out = []
                for r in row_vals:
                    row = {"_row": r}
                    total = 0
                    for c in col_vals:
                        val = pivot.get(r, {}).get(c, None)
                        row[c] = val
                        if isinstance(val, (int, float)):
                            total += val
                    row["_total"] = round(total, 4) if isinstance(total, float) else total
                    rows_out.append(row)

                result["points"] = rows_out
                result["total"] = len(rows_out)
                result["cross_rows"] = row_vals
                result["cross_cols"] = col_vals
                result["cross_method"] = method
                result["cross_val_col"] = val_col
                return result
            except Exception as e:
                result["error"] = f"Cross table error: {e}"
                return result

        # Heatmap: 2D binned grid, x_col vs y_expr, color by count
        if ct == "heatmap" and x_col and y_expr:
            try:
                xc = df[x_col].cast(pl.Float64, strict=False).drop_nulls()
                yc = df[y_expr].cast(pl.Float64, strict=False).drop_nulls()
                if xc.len() > 0 and yc.len() > 0:
                    hdf = pl.DataFrame({"_hx": xc, "_hy": yc})
                    n_bins = cfg.get("bin_count") or 20
                    xmin, xmax = float(xc.min()), float(xc.max())
                    ymin, ymax = float(yc.min()), float(yc.max())
                    xstep = (xmax - xmin) / n_bins if xmax > xmin else 1
                    ystep = (ymax - ymin) / n_bins if ymax > ymin else 1
                    hdf = hdf.with_columns([
                        ((pl.col("_hx") - xmin) / xstep).floor().cast(pl.Int32).clip(0, n_bins - 1).alias("bx"),
                        ((pl.col("_hy") - ymin) / ystep).floor().cast(pl.Int32).clip(0, n_bins - 1).alias("by"),
                    ])
                    gc = hdf.group_by(["bx", "by"]).agg(pl.count().alias("cnt"))
                    points = []
                    for row in gc.to_dicts():
                        points.append({
                            "bx": int(row["bx"]), "by": int(row["by"]),
                            "cnt": int(row["cnt"]),
                            "x_lo": round(xmin + row["bx"] * xstep, 4),
                            "x_hi": round(xmin + (row["bx"] + 1) * xstep, 4),
                            "y_lo": round(ymin + row["by"] * ystep, 4),
                            "y_hi": round(ymin + (row["by"] + 1) * ystep, 4),
                        })
                    result["points"] = points
                    result["total"] = len(points)
                    result["heatmap_meta"] = {
                        "n_bins": n_bins, "x_min": round(xmin, 4), "x_max": round(xmax, 4),
                        "y_min": round(ymin, 4), "y_max": round(ymax, 4),
                    }
                    return result
            except Exception:
                pass

        # Bar with aggregation: x_col categories + agg_method/agg_col.
        if ct == "bar" and x_col and x_col in df.columns and not y_expr:
            try:
                method = (cfg.get("agg_method") or "count").lower()
                agg_col = _resolve_name(list(df.columns), cfg.get("agg_col", "") or "")
                if method == "sum" and agg_col and agg_col in df.columns:
                    agg_expr = pl.col(agg_col).cast(pl.Float64, strict=False).sum().alias("_value")
                elif method == "mean" and agg_col and agg_col in df.columns:
                    agg_expr = pl.col(agg_col).cast(pl.Float64, strict=False).mean().alias("_value")
                elif method == "min" and agg_col and agg_col in df.columns:
                    agg_expr = pl.col(agg_col).cast(pl.Float64, strict=False).min().alias("_value")
                elif method == "max" and agg_col and agg_col in df.columns:
                    agg_expr = pl.col(agg_col).cast(pl.Float64, strict=False).max().alias("_value")
                else:
                    agg_expr = pl.len().alias("_value")
                grp = df.group_by(x_col).agg(agg_expr)
                grp = grp.sort(x_col) if cfg.get("sort_x", False) else grp.sort("_value", descending=True)
                limit = cfg.get("limit_points") or 30
                try:
                    grp = grp.head(int(limit))
                except Exception:
                    grp = grp.head(30)
                points = []
                for d in grp.to_dicts():
                    yv = d.get("_value", 0)
                    points.append({
                        "x": str(d.get(x_col, "")),
                        "y": round(float(yv), 4) if isinstance(yv, float) else int(yv or 0),
                        "label": str(d.get(x_col, "")),
                    })
                result["points"] = points
                result["total"] = len(points)
                return result
            except Exception as e:
                result["error"] = f"Bar aggregation error: {e}"
                return result

        # Scatter / Line / Bar
        # v8.8.13: y_expr 에 콤마 여러 컬럼 지원 → 각 Y 를 series 로 emit, pt.series 필드 부여.
        y_cols_raw = [s.strip() for s in (y_expr or "").split(",") if s.strip()]
        y_cols = [c for c in y_cols_raw if c in df.columns]
        multi_y = len(y_cols) >= 2
        sel = []
        if x_col and x_col in df.columns:
            sel.append(pl.col(x_col))
        if cc and cc in df.columns:
            sel.append(pl.col(cc).alias("color"))
        if multi_y:
            for yc in y_cols:
                sel.append(pl.col(yc))
        elif len(y_cols) == 1:
            sel.append(pl.col(y_cols[0]))
            y_expr = y_cols[0]
        elif y_expr:
            try:
                sel.append(pl.sql_expr(y_expr).alias("y_val"))
                y_expr = "y_val"
            except Exception:
                pass
        # v7.2: Carry selection_key column for cross-chart marking
        if sel_key and sel_key in df.columns and sel_key not in set([x_col, cc, *y_cols, y_expr]):
            sel.append(pl.col(sel_key).alias("_mark"))
        if not sel:
            result["error"] = "No valid columns"
            return result
        df = df.select(sel)
        if df.height > MAX_POINTS * 2:
            df = df.sample(MAX_POINTS, seed=42)

        points = []
        for row in df.to_dicts():
            if multi_y:
                for yc in y_cols:
                    yv = row.get(yc)
                    if yv is None:
                        continue
                    try:
                        pt = {
                            "x": str(row.get(x_col, "")),
                            "y": float(yv),
                            "series": yc,  # 시리즈명 = Y 컬럼명
                            "color": str(row.get("color", "")) if cc else None,
                        }
                        m = row.get("_mark")
                        if m is not None:
                            pt["mark"] = str(m)
                        points.append(pt)
                    except Exception:
                        pass
            else:
                y = row.get(y_expr)
                if y is not None:
                    try:
                        pt = {
                            "x": str(row.get(x_col, "")),
                            "y": float(y),
                            "color": str(row.get("color", "")) if cc else None,
                        }
                        m = row.get("_mark")
                        if m is not None:
                            pt["mark"] = str(m)
                        points.append(pt)
                    except Exception:
                        pass
        result["points"] = points[:MAX_POINTS]
        result["total"] = len(points)
        result["selection_key"] = sel_key
        if multi_y:
            result["series"] = y_cols

        # v6: SPC computation
        if cfg.get("enable_spc") and points:
            yvals = [p["y"] for p in points if isinstance(p.get("y"), (int, float))]
            if len(yvals) >= 3:
                cl = statistics.mean(yvals)
                sigma = statistics.stdev(yvals)
                result["spc"] = {
                    "cl": round(cl, 6),
                    "ucl": round(cl + 3 * sigma, 6),
                    "lcl": round(cl - 3 * sigma, 6),
                    "sigma": round(sigma, 6),
                }

        # v6/v7: OOS (out-of-spec) count across legacy usl/lsl AND spec_lines[]
        usl_vals = []
        lsl_vals = []
        if cfg.get("usl") is not None:
            usl_vals.append(cfg.get("usl"))
        if cfg.get("lsl") is not None:
            lsl_vals.append(cfg.get("lsl"))
        for sl in (cfg.get("spec_lines") or []):
            try:
                v = float(sl.get("value"))
                k = (sl.get("kind") or "").lower()
                if k == "usl":
                    usl_vals.append(v)
                elif k == "lsl":
                    lsl_vals.append(v)
            except Exception:
                pass
        if usl_vals or lsl_vals:
            # tightest bounds
            tight_usl = min(usl_vals) if usl_vals else None
            tight_lsl = max(lsl_vals) if lsl_vals else None
            oos = 0
            # Per-spec-line breakdown (each USL / LSL individually)
            per_spec = []
            for v in usl_vals:
                per_spec.append({"kind": "usl", "value": v, "count": 0})
            for v in lsl_vals:
                per_spec.append({"kind": "lsl", "value": v, "count": 0})
            for p in points:
                y = p.get("y")
                if not isinstance(y, (int, float)):
                    continue
                if tight_usl is not None and y > tight_usl:
                    oos += 1
                if tight_lsl is not None and y < tight_lsl:
                    oos += 1
                for sp in per_spec:
                    if sp["kind"] == "usl" and y > sp["value"]:
                        sp["count"] += 1
                    elif sp["kind"] == "lsl" and y < sp["value"]:
                        sp["count"] += 1
            result["oos_count"] = oos
            result["oos_breakdown"] = per_spec

    except Exception as e:
        result["error"] = str(e)
        logger.warning(f"Chart compute error [{chart_id}]: {e}")
    return result


# ──────────────────────────────────────────────────────────────────
# Background Scheduler
# ──────────────────────────────────────────────────────────────────
class ChartScheduler:
    def __init__(self, interval: int = 600):
        self._interval = interval
        self._snapshots: dict = {}
        self._lock = threading.Lock()
        self._thread = None
        self._load_cached()

    def _load_cached(self):
        cached = load_json(SNAP_FILE, {})
        if isinstance(cached, dict):
            self._snapshots = cached

    def start(self):
        if self._thread is not None:
            return
        if not dashboard_scheduler_enabled():
            logger.info(
                "Chart scheduler disabled "
                "(set FLOW_ENABLE_DASHBOARD_SCHEDULER=1 to enable)"
            )
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info(f"Chart scheduler started (interval={self._interval}s)")

    def _loop(self):
        # Initial computation after 5 seconds
        time.sleep(5)
        self._compute_all()
        while True:
            time.sleep(self._interval)
            self._compute_all()

    def _compute_all(self):
        charts = _charts()
        if not charts:
            return
        logger.info(f"Computing {len(charts)} charts...")
        t0 = time.time()
        for cfg in charts:
            if cfg.get("no_schedule"):
                continue
            try:
                snap = _compute_chart(cfg)
                # v6: OOS alert — notify admins if out-of-spec points detected
                oos = snap.get("oos_count", 0)
                old_oos = 0
                with self._lock:
                    old_snap = self._snapshots.get(cfg["id"])
                    if old_snap:
                        old_oos = old_snap.get("oos_count", 0)
                    self._snapshots[cfg["id"]] = snap
                if oos > 0 and oos != old_oos:
                    try:
                        title = cfg.get("title", cfg["id"])
                        # Build spec summary including extra spec_lines
                        spec_parts = []
                        if cfg.get("usl") is not None:
                            spec_parts.append(f"USL={cfg.get('usl')}")
                        if cfg.get("lsl") is not None:
                            spec_parts.append(f"LSL={cfg.get('lsl')}")
                        for sl in (cfg.get("spec_lines") or []):
                            k = (sl.get("kind") or "").upper()
                            nm = sl.get("name") or k
                            v = sl.get("value")
                            if v is not None:
                                spec_parts.append(f"{nm}={v}")
                        spec_str = ", ".join(spec_parts) or "no specs"
                        _notify_dashboard_watchers(
                            cfg,
                            f"OOS Alert: {title}",
                            f"{oos} points out of spec ({spec_str})",
                            alert_key=f"oos:{cfg.get('id')}:{oos}",
                        )
                    except Exception:
                        pass
                trend = _detect_trend_alert(snap.get("points") or [], cfg.get("chart_type") or "", cfg.get("title") or "")
                snap["trend_alert"] = trend
                old_trend = 0
                if old_snap:
                    old_trend = int(((old_snap.get("trend_alert") or {}).get("count")) or 0)
                if trend.get("count", 0) > 0 and trend.get("count", 0) != old_trend:
                    try:
                        title = cfg.get("title", cfg["id"])
                        latest = (trend.get("latest") or [{}])[0]
                        body = (
                            f"{trend.get('count', 0)} outlier candidates detected"
                            f" · latest x={latest.get('x') or '-'} y={latest.get('y') or '-'}"
                        )
                        _notify_dashboard_watchers(
                            cfg,
                            f"Trend Alert: {title}",
                            body,
                            alert_key=f"trend:{cfg.get('id')}:{trend.get('count', 0)}",
                        )
                    except Exception:
                        pass
            except Exception as e:
                logger.warning(f"Scheduler chart error: {e}")
        with self._lock:
            active_ids = {str(c.get("id") or "") for c in charts if c.get("id")}
            for stale_id in list(self._snapshots.keys()):
                if stale_id not in active_ids:
                    self._snapshots.pop(stale_id, None)
            save_json(SNAP_FILE, self._snapshots)
        elapsed = time.time() - t0
        logger.info(f"Charts computed in {elapsed:.1f}s")

    def refresh(self):
        """Manual trigger (runs in background thread)."""
        threading.Thread(target=self._compute_all, daemon=True).start()

    def get_all(self) -> dict:
        with self._lock:
            return dict(self._snapshots)

    def get_one(self, chart_id: str):
        with self._lock:
            return self._snapshots.get(chart_id)


_scheduler = ChartScheduler(interval=600)
_scheduler.start()


# ──────────────────────────────────────────────────────────────────
# API Endpoints
# ──────────────────────────────────────────────────────────────────
@router.get("/items")
def get_dashboard_items(
    request: Request,
    group: str = Query("ET"),
    product: str = Query(""),
    limit: int = Query(2000),
):
    _require_dashboard_section(request, "charts")
    return {"items": dashboard_items(group=group, product=product, limit=limit)}


@router.get("/chart-defaults")
def get_chart_defaults(request: Request):
    _require_dashboard_section(request, "charts")
    return {"defaults": load_chart_defaults()}


@router.post("/chart-defaults")
def post_chart_defaults(req: ChartDefaultReq, request: Request, _admin=Depends(require_admin)):
    from core.auth import current_user
    me = current_user(request)
    if me.get("role") != "admin":
        raise HTTPException(403, "Admin only")
    updated = save_chart_default(req.chart_type, req.config)
    return {"ok": True, "chart_type": req.chart_type, "config": updated, "defaults": load_chart_defaults()}


@router.post("/multi-db-chart")
def multi_db_chart(req: MultiDbChartReq, request: Request):
    me = _require_dashboard_section(request, "charts")
    payload = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    result = build_multi_db_chart(payload, username=me.get("username") or "user")
    return result


@router.post("/chart-refine")
def chart_refine(req: ChartRefineReq, request: Request):
    from core.auth import current_user
    me = current_user(request)
    try:
        return refine_chart_session(
            req.chart_session_id,
            req.action,
            req.value,
            username=me.get("username") or "user",
        )
    except FileNotFoundError:
        raise HTTPException(404, "chart session not found")


@router.post("/layout")
def save_dashboard_layout(req: DashboardLayoutReq, request: Request):
    from core.auth import current_user
    me = current_user(request)
    username = me.get("username") or "user"
    with _scheduler._lock:
        snaps = load_json(SNAP_FILE, {})
        if not isinstance(snaps, dict):
            snaps = {}
        layouts = snaps.get("_last_layout")
        if not isinstance(layouts, dict):
            layouts = {}
        layouts[username] = {"layout": req.layout, "updated_at": datetime.datetime.now().isoformat(timespec="seconds")}
        snaps["_last_layout"] = layouts
        save_json(SNAP_FILE, snaps, indent=2)
    return {"ok": True}


@router.post("/apply-default")
def apply_dashboard_default(req: MultiDbChartReq, request: Request):
    _require_dashboard_section(request, "charts")
    selected = [req.x_item, req.y_item, *list(req.items or [])]
    return {"ok": True, "config": apply_chart_defaults(req.chart_type, selected)}


@router.get("/charts")
def get_charts(request: Request):
    """v8.5.0: group_ids visibility 필터. admin 은 전체, 일반 유저는 자기 그룹 매칭만.
    v8.8.0: visible_to == "admin" 차트는 admin 외 차단. visible_to == "groups" 는 group_ids 와 동일하게 group 교집합 필요."""
    me = _require_dashboard_section(request, "charts")
    return {"charts": _visible_charts_for_user(me)}


@router.get("/products")
def list_products(request: Request):
    # Chart creation uses this source picker. Gate it with the charts section,
    # not the progress panel, so delegated chart users do not see an empty
    # source list just because FAB progress is disabled.
    _require_dashboard_section(request, "charts")
    return {"products": find_all_sources()}


@router.get("/fab-progress")
def fab_progress(
    request: Request,
    product: str = Query(""),
    days: int = Query(0),
    limit: int = Query(8),
    target_step_id: str = Query(""),
    lot_query: str = Query(""),
    sample_lots: int = Query(0),
    reference_step_id: str = Query(""),
    knob_col: str = Query(""),
    knob_value: str = Query(""),
):
    _require_dashboard_section(request, "progress")
    cfg = _dashboard_fab_progress_config()
    return _compute_fab_progress(
        product=product,
        days=days or int(cfg.get("days") or FAB_PROGRESS_DEFAULT_DAYS),
        limit=limit,
        target_step_id=target_step_id,
        lot_query=lot_query,
        sample_lots=sample_lots or int(cfg.get("sample_lots") or FAB_PROGRESS_DEFAULT_SAMPLE_LOTS),
        knob_col=knob_col,
        knob_value=knob_value,
        reference_step_id=reference_step_id or str(cfg.get("reference_step_id") or FAB_PROGRESS_DEFAULT_REFERENCE_STEP),
    )


@router.get("/summary")
def dashboard_summary(request: Request, product: str = Query("")):
    _require_dashboard_section(request, "progress")
    return _compute_dashboard_summary(product=product)


@router.get("/stuck-lots")
def stuck_lots(request: Request, product: str = Query(""), days: int = Query(30), limit: int = Query(50), hours: int = Query(24)):
    _require_dashboard_section(request, "progress")
    data = _compute_fab_progress(product=product, days=days, limit=max(limit, 100))
    rows = [
        row for row in (data.get("wip_lots") or [])
        if float(row.get("stuck_hours") or 0.0) >= float(hours)
    ]
    rows.sort(key=lambda row: float(row.get("stuck_hours") or 0.0), reverse=True)
    return {"ok": True, "product": product, "hours": hours, "lots": rows[:limit], "count": len(rows)}


@router.get("/snapshots")
def get_snapshots(request: Request):
    """Return all pre-computed chart data."""
    me = _require_dashboard_section(request, "charts")
    allowed_ids = {str(c.get("id") or "") for c in _visible_charts_for_user(me) if c.get("id")}
    snaps = _scheduler.get_all()
    return {"snapshots": {cid: snap for cid, snap in snaps.items() if cid in allowed_ids}}


@router.post("/refresh")
def refresh_charts(_admin=Depends(require_admin)):
    """Admin: manually trigger re-computation."""
    _scheduler.refresh()
    return {"ok": True, "message": "Refresh started in background"}


@router.get("/trend-alerts")
def trend_alerts(request: Request, limit: int = Query(8)):
    me = _require_dashboard_section(request, "alerts")
    charts = _visible_charts_for_user(me)
    snaps = _scheduler.get_all()
    return {"alerts": _trend_alert_candidates(charts, snaps, limit=limit)}


@router.post("/charts/save")
def save_chart(cfg: ChartConfig, _admin=Depends(require_admin)):
    charts = _charts()
    if not cfg.id:
        cfg.id = _new_id()
    d = cfg.dict()
    for i, c in enumerate(charts):
        if c.get("id") == cfg.id:
            charts[i] = d
            break
    else:
        charts.append(d)
    save_json(CHARTS_FILE, charts, indent=2)
    # Compute this chart immediately
    snap = _compute_chart(d)
    with _scheduler._lock:
        _scheduler._snapshots[cfg.id] = snap
        save_json(SNAP_FILE, _scheduler._snapshots)
    return {"ok": True, "id": cfg.id}


@router.post("/charts/delete")
def delete_chart(chart_id: str = Query(...), _admin=Depends(require_admin)):
    charts = [c for c in _charts() if c.get("id") != chart_id]
    save_json(CHARTS_FILE, charts, indent=2)
    with _scheduler._lock:
        _scheduler._snapshots.pop(chart_id, None)
        save_json(SNAP_FILE, _scheduler._snapshots)
    return {"ok": True}


@router.post("/charts/copy")
def copy_chart(chart_id: str = Query(...), _admin=Depends(require_admin)):
    import copy as cp
    charts = _charts()
    src = next((c for c in charts if c.get("id") == chart_id), None)
    if not src:
        raise HTTPException(404)
    new = cp.deepcopy(src)
    new["id"] = _new_id()
    new["title"] = src.get("title", "") + " (copy)"
    charts.append(new)
    save_json(CHARTS_FILE, charts, indent=2)
    return {"ok": True, "id": new["id"]}


@router.get("/columns")
def get_columns(root: str = Query(""), product: str = Query(""), file: str = Query(""),
                source_type: str = Query("")):
    # v8.8.7: PATHS 는 함수 진입부에서 임포트 — 이전에 base_file 분기에서만 임포트 되어
    #   hive DB 분기에서 UnboundLocalError 발생 ("cannot access local variable 'PATHS'") 하던 버그 수정.
    from core.paths import PATHS
    if source_type == "base_file" and file:
        fp = PATHS.base_root / file
        if not fp.is_file():
            raise HTTPException(404, f"Base file not found: {file}")
        df = read_one_file(fp)
        if df is None:
            raise HTTPException(400, "Cannot read base file")
        df = df.head(1)
        return {"columns": list(df.columns), "dtypes": {n: str(d) for n, d in df.schema.items()}}
    # v8.8.3: lazy resolve — PATHS.db_root 를 매 호출마다 읽어 admin 런타임 변경 반영.
    db_base = PATHS.db_root
    if file:
        fp = db_base / file
        if not fp.is_file():
            raise HTTPException(404, f"File not found: {file} (db_root={db_base})")
        df = read_one_file(fp)
        if df is None:
            raise HTTPException(400, f"Cannot read file: {file}")
        df = df.head(1)
    else:
        prod_path = db_base / root / product
        if not prod_path.is_dir():
            raise HTTPException(
                404,
                f"Product directory not found: {root}/{product} (db_root={db_base})"
            )
        first_file = first_data_file(prod_path)
        if first_file is None:
            raise HTTPException(
                404,
                f"No data files found in: {root}/{product} (db_root={db_base})"
            )
        df = read_one_file(first_file)
        if df is None:
            raise HTTPException(400, f"Cannot read data file: {first_file.name}")
        df = df.head(1)
    return {"columns": list(df.columns), "dtypes": {n: str(d) for n, d in df.schema.items()}}


@router.get("/preview")
def preview_data(root: str = Query(""), product: str = Query(""),
                 file: str = Query(""), source_type: str = Query(""),
                 x_col: str = Query(""), y_expr: str = Query(""),
                 filter_expr: str = Query(""), time_col: str = Query(""),
                 days: str = Query(""), limit: int = Query(10)):
    try:
        df = read_source(source_type, root, product, file, max_files=5)
        names = list(df.columns)
        x_col = _resolve_name(names, x_col)
        y_expr = _resolve_simple_y_expr(names, y_expr)
        time_col = _resolve_name(names, time_col)
        df = apply_time_window(df, time_col, days) if days.strip() else df
        if filter_expr.strip():
            try:
                df = df.filter(pl.sql_expr(filter_expr))
            except Exception as e:
                raise HTTPException(400, f"Filter error: {e}")
        sel = []
        if x_col and x_col in df.columns:
            sel.append(x_col)
        if y_expr and y_expr in df.columns:
            sel.append(y_expr)
        if not sel:
            sel = list(df.columns)[:5]
        show = df.select([c for c in sel if c in df.columns]).head(limit)
        data = serialize_rows(show.to_dicts())
        return {"rows": data, "total": df.height, "columns": list(show.columns)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Preview error: {str(e)}")


@router.get("/data")
def get_chart_data(request: Request, chart_id: str = Query(...)):
    """Returns snapshot if available, otherwise computes on-demand."""
    me = _require_dashboard_section(request, "charts")
    allowed_ids = {str(c.get("id") or "") for c in _visible_charts_for_user(me) if c.get("id")}
    if chart_id not in allowed_ids:
        raise HTTPException(404, "Chart not found")
    snap = _scheduler.get_one(chart_id)
    if snap:
        return snap
    # Fallback: compute on-demand
    charts = _charts()
    cfg = next((c for c in charts if c.get("id") == chart_id), None)
    if not cfg:
        raise HTTPException(404, "Chart not found")
    return _compute_chart(cfg)
