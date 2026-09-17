"""Exact FAB lot history and reference-lot ETA for the LOT Tracker page."""
from __future__ import annotations

import datetime as dt
import re
from collections import Counter

import polars as pl

from core.long_pivot import FAB_ROOT, scan_long_fab
from core.lot_progress_cache import lookup_lot_progress
from core.lot_wip import describe_step
from core.paths import PATHS

_STEP_NUMBER = re.compile(r"^(?:FAB[_\s-]*)?(\d{1,3})\.(\d+)(?:\D|$)", re.I)
_DAY = 86400.0


def _key(value: object) -> str:
    return str(value or "").strip().upper()


def _datetime(value: object) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time())
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        return None


def step_number(step_desc: str) -> tuple[float | None, int | None]:
    match = _STEP_NUMBER.match(str(step_desc or "").strip())
    if not match:
        return None, None
    layer = int(match.group(1))
    return float(f"{layer}.{match.group(2)}"), layer


def step_label(step_desc: str) -> str:
    match = _STEP_NUMBER.match(str(step_desc or "").strip())
    return f"{int(match.group(1)):02d}.{match.group(2)}" if match else ""


def build_timeline(rows: list[dict], product: str) -> list[dict]:
    """One point per completed step; latest wafer TKOUT marks lot completion."""
    per_step: dict[str, dt.datetime] = {}
    for row in rows:
        sid = _key(row.get("step_id"))
        when = _datetime(row.get("tkout_time"))
        if sid and when and (sid not in per_step or when > per_step[sid]):
            per_step[sid] = when
    ordered = sorted(per_step.items(), key=lambda item: (item[1], item[0]))
    if not ordered:
        return []
    start = ordered[0][1]
    result = []
    for sid, when in ordered:
        desc = str(describe_step(sid, product).get("step_desc") or "").strip()
        number, layer = step_number(desc)
        result.append({
            "step_id": sid,
            "step_desc": desc,
            "step_number": number,
            "step_label": step_label(desc),
            "mask_layer": layer,
            "tkout_time": when.isoformat(timespec="seconds"),
            "elapsed_days": round((when - start).total_seconds() / _DAY, 3),
        })
    return result


def mask_layer_summary(points: list[dict]) -> dict:
    """DPML = total observed days / distinct completed mask layers.

    LITHO/PHOTO in Vehicle_matching step_desc is the primary mask marker.  The
    leading ``00.0`` integer part identifies a layer and folds repeated detail
    steps into one completion.  Products without either marker fall back to
    distinct numbered layers so legacy matching data still produces a value.
    """
    numbered = [p for p in points if p.get("mask_layer") is not None]
    marked = [p for p in points if re.search(r"\b(?:LITHO|PHOTO)\b", p.get("step_desc") or "", re.I)]
    eligible = marked or numbered
    layers = []
    for point in eligible:
        # A numbered PHOTO/LITHO sub-step uses the 00 integer part as its
        # layer identity.  Unnumbered matching rows still count as a completed
        # layer, keyed by step_id, rather than silently disappearing.
        layer = point.get("mask_layer")
        if layer is None:
            layer = point.get("step_id") or point.get("step_desc")
        if layer not in layers:
            layers.append(layer)
    if not points or not layers:
        return {"dpml": None, "mask_layer_count": 0, "mask_layers": [], "mask_basis": "unavailable"}
    elapsed = max(0.0, float(points[-1].get("elapsed_days") or 0.0))
    return {
        "dpml": round(elapsed / len(layers), 3),
        "mask_layer_count": len(layers),
        "mask_layers": layers,
        "mask_basis": "litho_photo" if marked else "numbered_step_fallback",
    }


def days_per_mask_layer(points: list[dict]) -> float | None:
    return mask_layer_summary(points)["dpml"]


def predict(lot_points: list[dict], ref_points: list[dict], target_step_id: str = "") -> dict:
    empty = {"target_step_id": _key(target_step_id), "target_step_desc": "", "eta": None,
             "remaining_days": None, "reference_days": None, "points": [], "basis": ""}
    if not lot_points or not ref_points:
        return empty
    current = lot_points[-1]
    target = _key(target_step_id) or ref_points[-1]["step_id"]
    by_id = {p["step_id"]: i for i, p in enumerate(ref_points)}
    empty["target_step_id"] = target
    if target in by_id:
        empty["target_step_desc"] = ref_points[by_id[target]]["step_desc"]
    actual = next((p for p in reversed(lot_points) if p["step_id"] == target), None)
    if actual:
        empty.update({"eta": actual["tkout_time"], "remaining_days": 0.0,
                      "reference_days": 0.0, "basis": "actual"})
        return empty
    start_idx = by_id.get(current["step_id"])
    end_idx = by_id.get(target)
    if start_idx is None or end_idx is None or end_idx <= start_idx:
        empty["basis"] = "참고 LOT에 현재 step과 이후 목표 step의 TKOUT 이력이 모두 필요합니다."
        return empty
    ref_start = _datetime(ref_points[start_idx]["tkout_time"])
    lot_start = _datetime(current["tkout_time"])
    projected = []
    for point in ref_points[start_idx + 1:end_idx + 1]:
        delta = _datetime(point["tkout_time"]) - ref_start
        projected.append({
            "step_id": point["step_id"], "step_desc": point["step_desc"],
            "step_number": point["step_number"],
            "step_label": point.get("step_label") or step_label(point.get("step_desc") or ""),
            "eta": (lot_start + delta).isoformat(timespec="seconds"),
            "elapsed_days": round(lot_points[-1]["elapsed_days"] + delta.total_seconds() / _DAY, 3),
        })
    delta_days = (_datetime(ref_points[end_idx]["tkout_time"]) - ref_start).total_seconds() / _DAY
    empty.update({"eta": projected[-1]["eta"], "remaining_days": round(delta_days, 3),
                  "reference_days": round(delta_days, 3), "points": projected,
                  "basis": "reference_lot_step_delta"})
    return empty


def _product_candidates(lot_id: str, product: str) -> list[str]:
    if product:
        return [product.strip()]
    cached = lookup_lot_progress(lot_id=lot_id, limit=100, refresh_if_missing=False)
    known = {_key(row.get("product")) for row in cached if row.get("product")}
    if known:
        return sorted(known)
    root = PATHS.db_root / FAB_ROOT
    if root.is_dir():
        known.update(p.name for p in root.iterdir() if p.is_dir())
    return sorted(known)


def _history(lot_id: str, candidates: list[str]) -> tuple[str, list[dict]]:
    matches = []
    for product in candidates:
        lf = scan_long_fab(product, PATHS.db_root)
        if lf is None:
            continue
        names = set(lf.collect_schema().names())
        if not {"lot_id", "step_id", "tkout_time"}.issubset(names):
            continue
        rows = (lf.filter(pl.col("lot_id").cast(pl.Utf8, strict=False)
                          .str.strip_chars().str.to_uppercase() == _key(lot_id))
                  .select("lot_id", "step_id", "tkout_time").collect().to_dicts())
        if rows:
            matches.append((product, rows))
    if len(matches) > 1:
        raise ValueError("같은 lot_id가 여러 제품에 있습니다. product를 지정하세요: " + ", ".join(p for p, _ in matches))
    return matches[0] if matches else ("", [])


def track_lot(lot_id: str, reference_lot_id: str = "", target_step_id: str = "", product: str = "") -> dict:
    lot_id, reference_lot_id = _key(lot_id), _key(reference_lot_id)
    if not lot_id:
        raise ValueError("lot_id를 입력하세요.")
    product, rows = _history(lot_id, _product_candidates(lot_id, product))
    if not rows:
        return {"ok": False, "note": "FAB DB에서 해당 lot_id의 TKOUT 이력을 찾지 못했습니다.",
                "lot": None, "reference": None, "forecast": None}
    points = build_timeline(rows, product)
    if not points:
        return {"ok": False, "note": "해당 LOT의 유효한 step_id·tkout_time 이력이 없습니다.",
                "lot": None, "reference": None, "forecast": None}
    latest = points[-1]
    cache_rows = lookup_lot_progress(product=product, lot_id=lot_id, limit=500, refresh_if_missing=False)
    current_counts = Counter(_key(row.get("step_id")) for row in cache_rows if row.get("step_id"))
    current_steps = [{"step_id": sid, "step_desc": describe_step(sid, product).get("step_desc") or "",
                      "wafer_count": count} for sid, count in current_counts.most_common()]
    current = current_steps[0] if current_steps else {"step_id": latest["step_id"],
                                                       "step_desc": latest["step_desc"]}
    mask_summary = mask_layer_summary(points)
    lot = {"lot_id": lot_id, "product": product, "current_step_id": current["step_id"],
           "current_step_desc": current["step_desc"], "current_time": latest["tkout_time"],
           "current_source": "latest_cache" if current_steps else "last_tkout",
           "current_steps": current_steps, "points": points, **mask_summary}
    reference = None
    forecast = None
    if reference_lot_id:
        ref_product, ref_rows = _history(reference_lot_id, [product])
        ref_points = build_timeline(ref_rows, ref_product) if ref_rows else []
        reference = {"lot_id": reference_lot_id, "product": product, "points": ref_points,
                     **mask_layer_summary(ref_points)}
        forecast = predict(points, ref_points, target_step_id)
    return {"ok": True, "lot": lot, "reference": reference, "forecast": forecast,
            "note": "BigQuery 적재와 FAB DB 반영 시차가 있어 설비 실시간 위치와 다를 수 있습니다. "
                    "TKOUT은 해당 step 완료 시각이며 예측은 참고 LOT의 실제 step 간 소요시간을 적용합니다."}
