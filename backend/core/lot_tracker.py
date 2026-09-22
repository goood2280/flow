"""Exact FAB lot history and reference-lot ETA for the LOT Tracker page."""
from __future__ import annotations

import datetime as dt
import json
import re
from collections import Counter

import polars as pl

from core.long_pivot import scan_long_fab
from core.lot_progress_cache import lookup_lot_progress
from core.lot_wip import describe_step
from core.paths import PATHS

_STEP_NUMBER = re.compile(r"^(?:FAB[_\s-]*)?(\d{1,3})(?:\.(\d+))?(?:\D|$)", re.I)
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
    minor = match.group(2) if match.group(2) is not None else "0"
    return float(f"{layer}.{minor}"), layer


def step_label(step_desc: str) -> str:
    match = _STEP_NUMBER.match(str(step_desc or "").strip())
    if not match:
        return ""
    # 공정 설명에 적힌 표기(예: 00.0, 02.0)를 UI 라벨에서도 그대로 보존한다.
    major = match.group(1)
    minor = match.group(2) if match.group(2) is not None else "0"
    return f"{major}.{minor}"


def build_timeline(rows: list[dict], product: str) -> list[dict]:
    """One point per completed step; latest wafer TKOUT marks lot completion."""
    per_step: dict[str, dt.datetime] = {}
    arrival_by_step: dict[str, dt.datetime] = {}
    for row in rows:
        sid = _key(row.get("step_id"))
        when = _datetime(row.get("tkout_time"))
        arrival = _datetime(row.get("tkin_time"))
        if sid and arrival and (sid not in arrival_by_step or arrival > arrival_by_step[sid]):
            arrival_by_step[sid] = arrival
        if sid and when and (sid not in per_step or when > per_step[sid]):
            per_step[sid] = when
    ordered = sorted(per_step.items(), key=lambda item: (item[1], item[0]))
    if not ordered:
        return []
    start = ordered[0][1]
    result = []
    current_layer_label = "0.0"
    current_mask_layer = 0
    for sid, when in ordered:
        desc = str(describe_step(sid, product).get("step_desc") or "").strip()
        number, layer = step_number(desc)
        lbl = step_label(desc)
        is_litho = bool(re.search(r"\b(?:LITHO|PHOTO|LITHOGRAPHY|PHOTOLITHO)\b", desc, re.I))

        # step_id에 매칭되지 않거나 번호가 없는 공정은 litho/photo 사이의 레이어 안에 넣어줌
        if lbl:
            current_layer_label = lbl
            if layer is not None:
                current_mask_layer = layer

        result.append({
            "step_id": sid,
            "step_desc": desc,
            "step_number": number,
            "step_label": lbl or sid,
            "layer_label": current_layer_label,
            "is_litho_photo": is_litho,
            "mask_layer": layer if layer is not None else current_mask_layer,
            "tkout_time": when.isoformat(timespec="seconds"),
            "tkin_time": arrival_by_step[sid].isoformat(timespec="seconds") if sid in arrival_by_step else None,
            "elapsed_days": round((when - start).total_seconds() / _DAY, 3),
        })
    return result


def mask_layer_summary(points: list[dict]) -> dict:
    """DPML = total observed days / distinct completed mask layers."""
    marked = [p for p in points if p.get("is_litho_photo") or re.search(r"\b(?:LITHO|PHOTO|LITHOGRAPHY|PHOTOLITHO)\b", p.get("step_desc") or "", re.I)]
    layers = []
    target_points = marked if marked else points
    for point in target_points:
        layer = point.get("layer_label") or point.get("mask_layer") or point.get("step_id")
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


def parse_lot_ids(raw: str) -> list[str]:
    if not raw:
        return []
    tokens = re.split(r"[,;\s/]+", str(raw).strip())
    seen = set()
    res = []
    for t in tokens:
        k = _key(t)
        if k and k not in seen:
            seen.add(k)
            res.append(k)
            if len(res) >= 5:
                break
    return res


def predict_multi_lots(lot_points: list[dict], ref_lots: list[dict], target_step_id: str = "") -> dict:
    """Predict future step arrival times and ETA averaged over up to 5 reference lots.

    Includes current lot's latest point as anchor so frontend can draw a continuous
    dashed blue line from current status to future projected layers.
    """
    empty = {
        "target_step_id": _key(target_step_id),
        "target_step_desc": "",
        "eta": None,
        "remaining_days": None,
        "ref_lot_count": 0,
        "ref_summaries": [],
        "points": [],
        "basis": "",
    }
    if not lot_points or not ref_lots:
        return empty

    current = lot_points[-1]
    curr_sid = current["step_id"]
    curr_time = _datetime(current["tkout_time"])
    if not curr_time:
        return empty

    target = _key(target_step_id)
    if not target:
        all_last_steps = [r["points"][-1]["step_id"] for r in ref_lots if r.get("points")]
        target = all_last_steps[0] if all_last_steps else curr_sid

    empty["target_step_id"] = target

    valid_refs = []
    step_deltas: dict[str, list[float]] = {}
    step_info: dict[str, dict] = {}

    for ref in ref_lots:
        r_points = ref.get("points") or []
        by_id = {p["step_id"]: i for i, p in enumerate(r_points)}
        start_idx = by_id.get(curr_sid)
        end_idx = by_id.get(target)

        if start_idx is None:
            continue

        ref_start_time = _datetime(r_points[start_idx]["tkout_time"])
        if not ref_start_time:
            continue

        r_rem_days = None
        r_eta = None
        if end_idx is not None and end_idx >= start_idx:
            r_end_time = _datetime(r_points[end_idx]["tkout_time"])
            if r_end_time:
                r_sec = (r_end_time - ref_start_time).total_seconds()
                r_rem_days = round(r_sec / _DAY, 2)
                r_eta = (curr_time + dt.timedelta(seconds=r_sec)).isoformat(timespec="seconds")
                empty["target_step_desc"] = r_points[end_idx].get("step_desc", "")

        valid_refs.append({
            "lot_id": ref.get("lot_id", ""),
            "remaining_days": r_rem_days,
            "eta": r_eta,
            "points_count": len(r_points),
        })

        limit_idx = end_idx + 1 if (end_idx is not None and end_idx >= start_idx) else len(r_points)
        for p in r_points[start_idx + 1:limit_idx]:
            sid = p["step_id"]
            p_time = _datetime(p["tkout_time"])
            if p_time and p_time >= ref_start_time:
                d_sec = (p_time - ref_start_time).total_seconds()
                step_deltas.setdefault(sid, []).append(d_sec)
                if sid not in step_info:
                    step_info[sid] = p

    if not valid_refs:
        empty["basis"] = "선택된 참고 LOT들에 현재 완료 공정(STEP) 이력이 없어 예측을 계산할 수 없습니다."
        return empty

    # Anchor point: 현재 LOT의 마지막 완료 지점을 첫 번째 점으로 포함하여 파란색 점선이 끊기지 않게 연결
    projected = [
        {
            "step_id": current["step_id"],
            "step_desc": current["step_desc"],
            "step_number": current.get("step_number"),
            "step_label": current.get("step_label") or current.get("layer_label"),
            "layer_label": current.get("layer_label"),
            "is_litho_photo": current.get("is_litho_photo", False),
            "mask_layer": current.get("mask_layer"),
            "tkout_time": current["tkout_time"],
            "eta": current["tkout_time"],
            "elapsed_days": current.get("elapsed_days", 0.0),
            "is_anchor": True,
        }
    ]

    for sid, deltas in step_deltas.items():
        mean_sec = sum(deltas) / len(deltas)
        info = step_info[sid]
        eta_time = curr_time + dt.timedelta(seconds=mean_sec)
        projected.append({
            "step_id": sid,
            "step_desc": info.get("step_desc", ""),
            "step_number": info.get("step_number"),
            "step_label": info.get("step_label") or step_label(info.get("step_desc") or ""),
            "layer_label": info.get("layer_label") or step_label(info.get("step_desc") or ""),
            "is_litho_photo": info.get("is_litho_photo", False),
            "mask_layer": info.get("mask_layer"),
            "tkout_time": eta_time.isoformat(timespec="seconds"),
            "eta": eta_time.isoformat(timespec="seconds"),
            "elapsed_days": round(current.get("elapsed_days", 0.0) + mean_sec / _DAY, 3),
            "is_anchor": False,
        })

    projected.sort(key=lambda p: (p.get("elapsed_days", 0), p.get("step_id", "")))

    rem_days_list = [r["remaining_days"] for r in valid_refs if r["remaining_days"] is not None]
    avg_rem_days = round(sum(rem_days_list) / len(rem_days_list), 2) if rem_days_list else None
    final_eta = None
    if avg_rem_days is not None:
        final_eta = (curr_time + dt.timedelta(days=avg_rem_days)).isoformat(timespec="seconds")
    elif len(projected) > 1:
        final_eta = projected[-1]["eta"]
        avg_rem_days = round((_datetime(final_eta) - curr_time).total_seconds() / _DAY, 2)

    basis_text = f"참고 LOT {len(valid_refs)}개 평균 소요시간 적용" if len(valid_refs) > 1 else "참고 LOT 1개 소요시간 적용"

    empty.update({
        "eta": final_eta,
        "remaining_days": avg_rem_days,
        "ref_lot_count": len(valid_refs),
        "ref_summaries": valid_refs,
        "points": projected,
        "basis": basis_text,
    })
    return empty


def predict(lot_points: list[dict], ref_points: list[dict], target_step_id: str = "") -> dict:
    if not ref_points:
        return predict_multi_lots(lot_points, [], target_step_id)
    return predict_multi_lots(lot_points, [{"lot_id": "REF", "points": ref_points}], target_step_id)


def _fab_source_roots() -> list[str]:
    """Resolve the physical FAB DB using FileBrowser's configured display name.

    An explicit ``db_name_aliases`` entry displayed as ``FAB`` wins. When the
    operator has not configured one, LOT Tracker uses the legacy
    ``1.RAWDATA_DB`` folder.
    """
    db_root = PATHS.db_root
    actual_by_name: dict[str, str] = {}
    if db_root.is_dir():
        try:
            actual_by_name = {
                child.name.casefold(): child.name
                for child in db_root.iterdir()
                if child.is_dir()
            }
        except OSError:
            actual_by_name = {}

    aliases: dict = {}
    settings_path = PATHS.data_root / "filebrowser_settings.json"
    try:
        raw = json.loads(settings_path.read_text(encoding="utf-8"))
        configured = raw.get("db_name_aliases") if isinstance(raw, dict) else {}
        if isinstance(configured, dict):
            aliases = configured
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass

    matched: list[str] = []
    for source, display in aliases.items():
        if str(display or "").strip().casefold() != "fab":
            continue
        source_key = str(source or "").strip().casefold()
        if source_key == "1.rawdata_db":
            continue
        actual = actual_by_name.get(source_key)
        if actual and actual not in matched:
            matched.append(actual)
    if matched:
        return matched

    return [actual_by_name.get("1.rawdata_db", "1.RAWDATA_DB")]


def _product_candidates(lot_id: str, product: str) -> list[str]:
    if product:
        return [product.strip()]
    cached = lookup_lot_progress(lot_id=lot_id, limit=100, refresh_if_missing=False)
    known = {_key(row.get("product")) for row in cached if row.get("product")}
    for folder in _fab_source_roots():
        root = PATHS.db_root / folder
        if root.is_dir():
            try:
                known.update(p.name for p in root.iterdir() if p.is_dir())
            except OSError:
                continue
    return sorted(known)


def _history(lot_id: str, candidates: list[str]) -> tuple[str, list[dict]]:
    matches = []
    for folder in _fab_source_roots():
        for product in candidates:
            lf = scan_long_fab(product, PATHS.db_root, folder)
            if lf is None:
                continue
            names = set(lf.collect_schema().names())
            if not {"lot_id", "step_id", "tkout_time"}.issubset(names):
                continue
            selected = ["lot_id", "step_id", "tkout_time"] + (["tkin_time"] if "tkin_time" in names else [])
            rows = (lf.filter(pl.col("lot_id").cast(pl.Utf8, strict=False)
                              .str.strip_chars().str.to_uppercase() == _key(lot_id))
                      .select(selected).collect().to_dicts())
            if rows:
                matches.append((product, rows))
    if len(matches) > 1:
        raise ValueError("같은 lot_id가 여러 제품에 있습니다. product를 지정하세요: " + ", ".join(p for p, _ in matches))
    return matches[0] if matches else ("", [])


def track_lot(lot_id: str, reference_lot_id: str = "", target_step_id: str = "", product: str = "") -> dict:
    lot_id = _key(lot_id)
    if not lot_id:
        raise ValueError("lot_id를 입력하세요.")
    product, rows = _history(lot_id, _product_candidates(lot_id, product))
    if not rows:
        return {"ok": False, "note": f"FAB DB에서 lot_id '{lot_id}'의 TKOUT 이력을 찾지 못했습니다.",
                "lot": None, "references": [], "reference": None, "forecast": None}
    points = build_timeline(rows, product)
    if not points:
        return {"ok": False, "note": "해당 LOT의 유효한 step_id·tkout_time 이력이 없습니다.",
                "lot": None, "references": [], "reference": None, "forecast": None}
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

    ref_lots = []
    parsed_refs = parse_lot_ids(reference_lot_id)
    for r_id in parsed_refs:
        ref_product, ref_rows = _history(r_id, [product])
        if ref_rows:
            r_points = build_timeline(ref_rows, ref_product)
            if r_points:
                ref_lots.append({
                    "lot_id": r_id,
                    "product": product,
                    "points": r_points,
                    **mask_layer_summary(r_points),
                })
    forecast = predict_multi_lots(points, ref_lots, target_step_id) if ref_lots else None

    return {
        "ok": True,
        "lot": lot,
        "references": ref_lots,
        "reference": ref_lots[0] if ref_lots else None,
        "forecast": forecast,
        "note": "BigQuery 적재와 FAB DB 반영 시차가 있어 설비 실시간 위치와 다를 수 있습니다. "
                "TKOUT은 해당 step 완료 시각이며 예측은 참고 LOT의 실제 step 간 소요시간을 적용합니다."
    }
