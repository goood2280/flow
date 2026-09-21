"""Bounded, read-only TEG tools for the home data chat.

The dispatcher only claims prompts that explicitly mention TEG/Mapfile or are
follow-ups to an active TEG result.  This keeps ordinary lot/WIP ``location``
questions on the existing lot-progress path.

Coordinates always come from :mod:`core.teg_map`; this module only selects and
formats rows for the chat response.  It never derives missing coordinates.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from core import teg_map


MAX_COORDINATE_ROWS = 2_000
MAX_MAPFILE_ROWS = 100
MAX_TEG_SELECTION = 30
MAX_NAME_LENGTH = 300
MAX_VISUAL_SHOTS = 2_000
MAX_VISUAL_TEG_POSITIONS = 10_000

ACTION_SCHEMAS = {
    "teg.locations": {"product", "tegs"},
    "teg.coordinates": {"product", "tegs"},
    "teg.mapfiles": {"product"},
}

_TEG_MARKER_RE = re.compile(r"(?<![a-z])teg(?=$|[^a-z])|테그|맵파일|map\s*file", re.I)
_COORDINATE_RE = re.compile(r"좌표|coordinate|abs[_ -]?[xy]|radius|반경", re.I)
_MAPFILE_RE = re.compile(r"맵파일|map\s*file", re.I)
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]*")
_LOT_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9]{3,}(?:\.[A-Za-z0-9]+)?(?![A-Za-z0-9_])")
_TEG_FOLLOWUP_RE = re.compile(r"좌표|coordinate|abs[_ -]?[xy]|radius|반경|위치|어디|맵파일|map\s*file", re.I)


def _clean_text(value: Any, name: str, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{name} is required")
    if len(value) > MAX_NAME_LENGTH:
        raise ValueError(f"{name} is too long")
    return value


def _clean_tegs(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple)):
        values = list(value)
    else:
        raise ValueError("tegs must be a list of strings")
    out: list[str] = []
    for raw in values:
        name = _clean_text(raw, "teg")
        if name and name.casefold() not in {item.casefold() for item in out}:
            out.append(name)
    return out


def _request_user(request: Any) -> dict | None:
    if request is None:
        return None
    state = getattr(request, "state", None)
    user = getattr(state, "user", None) if state is not None else None
    if isinstance(user, dict):
        return user
    # The home data-chat route is already authenticated, but keeping the same
    # resolver here makes direct callers obey the TEG product visibility rules.
    from core.auth import current_user

    resolved = current_user(request)
    return resolved if isinstance(resolved, dict) else None


def _catalog(request: Any) -> list[dict]:
    user = _request_user(request)
    rows = teg_map.visible_product_catalog(user) if user is not None else teg_map.product_catalog()
    return [row for row in rows if isinstance(row, dict) and str(row.get("vehicle") or "").strip()]


def _literal_in_text(name: str, text: str) -> bool:
    return bool(re.search(
        rf"(?<![A-Za-z0-9_-]){re.escape(name)}(?![A-Za-z0-9_-])",
        text,
        re.I,
    ))


def _resolve_product(prompt: str, requested: str, context: dict, request: Any) -> tuple[str, list[str]]:
    from core.data_chat import available_product_names, product_candidates

    actual_products = available_product_names()
    search_text = requested or prompt
    matches = product_candidates(search_text, actual_products) if search_text else []
    if len(matches) > 1:
        return "", matches
    actual_product = matches[0] if len(matches) == 1 else ""
    explicit_unknown = bool(requested) or bool(re.search(
        r"\b(?:ML_TABLE_|VH_)?PROD[A-Za-z0-9_-]*\b", str(prompt or ""), re.I,
    ))
    if not actual_product and explicit_unknown:
        return "", []
    if not actual_product:
        remembered = str(context.get("product") or context.get("db_product") or "").strip()
        inherited = product_candidates(remembered, actual_products) if remembered else []
        if len(inherited) == 1:
            actual_product = inherited[0]
        elif len(inherited) > 1:
            return "", inherited
    if not actual_product:
        return "", []

    # Product selection comes from the real DB.  Only after that selection do
    # we resolve the product's TEG vehicle/source identifier.
    candidates: list[str] = []
    wanted = actual_product.casefold()
    for row in _catalog(request):
        vehicle = str(row.get("vehicle") or "").strip()
        aliases = {
            vehicle.casefold(),
            re.sub(r"^VH_", "", vehicle, flags=re.I).casefold(),
            str(row.get("product_code") or "").strip().casefold(),
        }
        if wanted in aliases and vehicle and vehicle not in candidates:
            candidates.append(vehicle)
    if len(candidates) == 1:
        context["db_product"] = actual_product
        return candidates[0], []
    return "", candidates


def _payload_error(message: str, context: dict, *, missing: list[str] | None = None,
                   rows: list[dict] | None = None) -> dict:
    tool: dict[str, Any] = {"feature": "teg", "warnings": [message], "sources": []}
    if missing:
        tool["missing"] = missing
    if rows is not None:
        tool["table"] = {"rows": rows, "columns": list(rows[0]) if rows else [], "total": len(rows)}
    return _result(message, tool, context, ok=False)


def _result(message: str, tool: dict, context: dict, *, ok: bool = True) -> dict:
    tool.setdefault("context", {key: context[key] for key in ("product", "teg_product", "teg_names") if key in context})
    product = str(context.get("product") or "")
    names = [str(name) for name in context.get("teg_names") or []]
    source = " · ".join(str(item) for item in tool.get("sources") or [])
    return {
        "ok": ok,
        "reply": message,
        "tool": tool,
        "context": context,
        "interpretation": {
            "summary": f"{product or '제품 미지정'}의 TEG {', '.join(names) if names else '미지정'} 위치 데이터를 조회합니다.",
            "product": product,
            "tegs": names,
            "source": source,
            "data_kind": "TEG geometry",
        },
        "meta": {"planner": "bounded_teg_read", "step_count": 1 if tool.get("action") else 0},
    }


def _load_payload(product: str, context: dict) -> tuple[dict | None, dict | None]:
    try:
        return teg_map.map_payload(product), None
    except (FileNotFoundError, LookupError, ValueError) as exc:
        return None, _payload_error(
            f"{product}의 TEG 위치 기준 데이터를 읽지 못했습니다: {exc}",
            context,
            missing=["teg_source"],
        )


def _resolve_one_teg(name: str, tegs: list[dict]) -> tuple[str, list[str]]:
    folded = name.casefold()
    displayed = [str(row.get("teg") or "") for row in tegs if str(row.get("teg") or "").casefold() == folded]
    displayed = list(dict.fromkeys(item for item in displayed if item))
    if len(displayed) == 1:
        return displayed[0], []

    source_matches = [str(row.get("teg") or "") for row in tegs
                      if str(row.get("teg_src") or "").casefold() == folded]
    source_matches = list(dict.fromkeys(item for item in source_matches if item))
    # A unique exact source alias is authoritative, including display names
    # carrying an orientation prefix. Multiple H/V matches still need a choice.
    if len(source_matches) == 1:
        return source_matches[0], []

    prefix_matches = [str(row.get("teg") or "") for row in tegs
                      if re.sub(r"^(?:TEG_|VH_)", "", str(row.get("teg") or ""), flags=re.I).casefold() == folded]
    prefix_matches = list(dict.fromkeys(item for item in prefix_matches if item))
    if len(prefix_matches) == 1:
        return prefix_matches[0], []
    choices = prefix_matches or source_matches or displayed
    if choices:
        return "", choices
    def normalized(value):
        value = re.sub(r"^(?:(?:TEG|VH|H|V)[_ -])+", "", value, flags=re.I)
        return re.sub(r"[^a-z0-9]", "", value.casefold())
    target = normalized(name)
    ranked = []
    for row in tegs:
        label = str(row.get("teg") or "").strip()
        if not label or not target:
            continue
        score = max(SequenceMatcher(None, target, normalized(str(row.get(key) or ""))).ratio()
                    for key in ("teg", "teg_src"))
        if score >= 0.72:
            ranked.append((score, label))
    return "", list(dict.fromkeys(label for _, label in sorted(ranked, key=lambda x: (-x[0], x[1]))))[:8]


def _prompt_teg_names(prompt: str, rows: list[dict]) -> list[str]:
    aliases: list[str] = []
    for row in rows:
        for key in ("teg", "teg_src"):
            value = str(row.get(key) or "").strip()
            if value and value.casefold() not in {item.casefold() for item in aliases}:
                aliases.append(value)
    # Longest-first prevents a shorter alias from consuming a numbered name.
    return [name for name in sorted(aliases, key=len, reverse=True) if _literal_in_text(name, prompt)]


def _unknown_teg_tokens(prompt: str, product: str, resolved_inputs: list[str]) -> list[str]:
    known = {name.casefold() for name in resolved_inputs}
    unknown: list[str] = []
    for token in _TOKEN_RE.findall(prompt):
        folded = token.casefold()
        if folded == product.casefold() or folded in known or folded in {"teg", "mapfile"}:
            continue
        if "teg" in folded and folded not in {item.casefold() for item in unknown}:
            unknown.append(token)
    return unknown


def _select_tegs(prompt: str, requested: list[str], context: dict, payload: dict) -> tuple[list[str], dict | None]:
    rows = [row for row in payload.get("tegs") or [] if isinstance(row, dict)]
    prompt_candidates = re.findall(r"\b([A-Za-z0-9_]+)\s+TEG\b", prompt, re.I)
    tokens = [token for token in _TOKEN_RE.findall(prompt)
              if (re.search(r"\d", token) or (len(token) >= 3 and any(_resolve_one_teg(token, rows)))) and token.casefold() not in {
                  str(context.get(key) or "").casefold() for key in ("product", "db_product", "teg_product")}
              and not re.fullmatch(r"\d+(?:\.\d+)?", token)]
    known_inputs = _prompt_teg_names(prompt, rows)
    similar_inputs = [token for token in tokens if any(_resolve_one_teg(token, rows))]
    inputs = requested or list(dict.fromkeys([*known_inputs, *similar_inputs])) or prompt_candidates or tokens
    if not inputs:
        inputs = _clean_tegs(context.get("teg_names"))

    selected: list[str] = []
    ambiguous: list[str] = []
    missing: list[str] = []
    suggestions = []
    for raw in inputs:
        resolved, choices = _resolve_one_teg(raw, rows)
        if resolved:
            if resolved.casefold() not in {name.casefold() for name in selected}:
                selected.append(resolved)
        elif choices:
            ambiguous.extend(choices)
            suggestions.append({"requested": raw, "candidates": choices})
        else:
            missing.append(raw)

    missing.extend(_unknown_teg_tokens(prompt, str(payload.get("vehicle") or ""), inputs))
    missing = list(dict.fromkeys(missing))
    if ambiguous:
        choices = list(dict.fromkeys(ambiguous))
        context["pending_teg_selection"] = {"inputs": inputs, "suggestions": suggestions,
            "action": context.get("last_action", "teg.locations"), "product": context.get("product")}
        result = _payload_error(
            "TEG 정답지에서 비슷한 이름을 찾았습니다. 조회할 TEG가 맞는지 선택해 주세요: " + ", ".join(choices),
            context,
            missing=["teg"],
            rows=[{"teg": name} for name in choices],
        )
        result["tool"]["teg_candidates"] = suggestions
        return [], result
    if missing:
        return [], _payload_error(
            "제품의 TEG 목록에서 찾지 못했습니다: " + ", ".join(missing),
            context,
            missing=["teg"],
        )
    if not selected:
        available = [str(row.get("teg") or "") for row in rows[:30] if row.get("teg")]
        return [], _payload_error(
            "조회할 TEG 이름을 지정해 주세요.",
            context,
            missing=["teg"],
            rows=[{"teg": name} for name in available],
        )
    if len(selected) > MAX_TEG_SELECTION:
        return [], _payload_error(
            f"한 번에 조회할 수 있는 TEG는 {MAX_TEG_SELECTION}개까지입니다.",
            context,
            missing=["teg_selection"],
        )
    context.pop("pending_teg_selection", None)
    return selected, None


def _unavailable_teg_maps(product: str, reason: str) -> dict:
    return {
        "version": 1,
        "available": False,
        "product": str(product or ""),
        "coordinate_unit": "mm",
        "unavailable_reason": reason,
    }


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _teg_maps_payload(product: str, names: list[str], payload: dict) -> dict:
    """Return bounded, source-backed geometry for the home TEG diagrams.

    The frontend receives Cartesian millimetres only.  It does not fit shot
    geometry or substitute default TEG sizes when a reference file is missing.
    """
    geometry = payload.get("geometry") if isinstance(payload, dict) else None
    if not isinstance(geometry, dict) or geometry.get("fit") != "radius":
        return _unavailable_teg_maps(product, "Chip_Radius 기반 wafer geometry가 없어 위치 그림을 표시할 수 없습니다.")

    wafer_radius = _finite_number(geometry.get("wafer_radius_mm"))
    wafer_edge = _finite_number(geometry.get("wafer_edge_mm"))
    shot_width = _finite_number(geometry.get("shot_w_mm"))
    shot_height = _finite_number(geometry.get("shot_h_mm"))
    if not wafer_radius or wafer_radius <= 0:
        return _unavailable_teg_maps(product, "wafer 반경 기준값이 없어 위치 그림을 표시할 수 없습니다.")
    if not shot_width or shot_width <= 0 or not shot_height or shot_height <= 0:
        return _unavailable_teg_maps(product, "실제 shot 크기가 없어 위치 그림을 표시할 수 없습니다.")

    source_shots = payload.get("shots") or []
    if len(source_shots) > MAX_VISUAL_SHOTS:
        return _unavailable_teg_maps(
            product,
            f"실제 shot이 {len(source_shots):,}개여서 위치 그림 표시 한도 {MAX_VISUAL_SHOTS:,}개를 넘었습니다.",
        )
    shots: list[dict] = []
    for raw in source_shots:
        if not isinstance(raw, dict):
            continue
        center_x = _finite_number(raw.get("mm_x"))
        source_y = _finite_number(raw.get("mm_y"))
        shot_x = _finite_number(raw.get("x"))
        shot_y = _finite_number(raw.get("y"))
        if None in (center_x, source_y, shot_x, shot_y):
            continue
        shots.append({
            "shot_x": shot_x,
            "shot_y": shot_y,
            "center_x_mm": center_x,
            # chip_y_adj/WF-map y is down-positive; diagrams use Cartesian y.
            "center_y_mm": -source_y,
        })
    if not shots:
        return _unavailable_teg_maps(product, "실제 shot 중심 좌표가 없어 위치 그림을 표시할 수 없습니다.")

    wanted = {str(name).casefold() for name in names}
    selected: list[dict] = []
    for raw in payload.get("tegs") or []:
        if not isinstance(raw, dict) or str(raw.get("teg") or "").casefold() not in wanted:
            continue
        x_mm = _finite_number(raw.get("ebeam_x"))
        y_mm = _finite_number(raw.get("ebeam_y"))
        if x_mm is None or y_mm is None:
            continue
        chip_width = _finite_number(raw.get("chip_w"))
        chip_height = _finite_number(raw.get("chip_h"))
        teg_width = _finite_number(raw.get("teg_w"))
        teg_height = _finite_number(raw.get("teg_h"))
        if chip_width and chip_width > 0 and chip_height and chip_height > 0:
            width, height, size_source = chip_width, chip_height, "Main_chip_info"
        elif teg_width and teg_width > 0 and teg_height and teg_height > 0:
            width, height, size_source = teg_width, teg_height, "Teg_location"
        else:
            width = height = None
            size_source = ""
        selected.append({
            "teg": str(raw.get("teg") or ""),
            "x_mm": x_mm,
            "y_mm": y_mm,
            "width_mm": width,
            "height_mm": height,
            "direction": str(raw.get("flat_zone") or ""),
            "size_source": size_source,
            "geometry_available": width is not None and height is not None,
            "unavailable_reason": "" if width is not None and height is not None
            else "TEG 실제 크기 기준값이 없어 위치만 표시합니다.",
        })
    if not selected:
        return _unavailable_teg_maps(product, "선택한 TEG의 실제 shot 상대좌표가 없어 위치 그림을 표시할 수 없습니다.")
    visual_positions = len(shots) * len(selected)
    if visual_positions > MAX_VISUAL_TEG_POSITIONS:
        return _unavailable_teg_maps(
            product,
            f"TEG 위치가 {visual_positions:,}개여서 그림 표시 한도 {MAX_VISUAL_TEG_POSITIONS:,}개를 넘었습니다.",
        )

    return {
        "version": 1,
        "available": True,
        "product": str(product or ""),
        "coordinate_unit": "mm",
        "wafer": {
            "radius_mm": wafer_radius,
            "edge_mm": wafer_edge if wafer_edge and wafer_edge > 0 else None,
            "shot_width_mm": shot_width,
            "shot_height_mm": shot_height,
            "shots": shots,
        },
        "within_shot": {
            "x_min_mm": -shot_width / 2,
            "x_max_mm": shot_width / 2,
            "y_min_mm": -shot_height / 2,
            "y_max_mm": shot_height / 2,
            "tegs": selected,
        },
    }


def _native_teg_view(product, names, payload):
    """Bound the native read-only map payload; geometry remains source-owned."""
    shots = payload.get("shots") or []
    if len(shots) > MAX_VISUAL_SHOTS or len(shots) * len(names) > MAX_VISUAL_TEG_POSITIONS:
        return None
    wanted = {name.casefold() for name in names}
    return {"product": product, "geometry": payload.get("geometry"),
            "shots": [{k: s.get(k) for k in ("x", "y", "mm_x", "mm_y", "radius", "synthetic")} for s in shots],
            "tegs": [{k: t.get(k) for k in ("teg", "ebeam_x", "ebeam_y", "teg_w", "teg_h", "chip_w", "chip_h", "flat_zone")}
                     for t in payload.get("tegs", []) if str(t.get("teg") or "").casefold() in wanted],
            "selected_tegs": names}


def _locations(product: str, names: list[str], payload: dict, context: dict) -> dict:
    display_product = str(context.get("db_product") or product)
    context.update(product=display_product, teg_product=product, teg_names=names, last_action="teg.locations")
    wanted = {name.casefold() for name in names}
    rows = []
    for raw in payload.get("tegs") or []:
        if not isinstance(raw, dict) or str(raw.get("teg") or "").casefold() not in wanted:
            continue
        rows.append({
            "product": display_product,
            "teg": raw.get("teg"),
            "ebeam_x": raw.get("ebeam_x"),
            "ebeam_y": raw.get("ebeam_y"),
            "teg_w": raw.get("teg_w"),
            "teg_h": raw.get("teg_h"),
            "direction": raw.get("flat_zone"),
        })
    all_tegs = [str(r.get("teg") or "").strip() for r in (payload.get("tegs") or []) if r.get("teg")]
    related = [t for t in dict.fromkeys(all_tegs) if t not in names][:12]

    tool = {
        "feature": "teg",
        "action": "teg.locations",
        "table": {
            "rows": rows,
            "columns": ["product", "teg", "ebeam_x", "ebeam_y", "teg_w", "teg_h", "direction"],
            "total": len(rows),
        },
        "teg_maps": _teg_maps_payload(product, names, payload),
        "teg_view": _native_teg_view(product, names, payload),
        "related_tegs": related,
        "sources": ["TEG 위치조회 · Teg_location"],
        "warnings": [],
    }
    return _result(f"{display_product}에서 {', '.join(names)}의 shot 내부 기준 위치를 찾았습니다. 좌표와 크기의 단위는 mm입니다.", tool, context)


def _coordinates(product: str, names: list[str], context: dict) -> dict:
    display_product = str(context.get("db_product") or product)
    context.update(product=display_product, teg_product=product, teg_names=names, last_action="teg.coordinates")
    shown: list[dict] = []
    total = 0
    try:
        for name in names:
            table = teg_map.teg_radius_table(product, name)
            source_rows = [raw for raw in table.get("rows") or [] if isinstance(raw, dict)]
            total += len(source_rows)
            room = MAX_COORDINATE_ROWS - len(shown)
            for raw in source_rows[:max(0, room)]:
                if isinstance(raw, dict):
                    shown.append({"product": display_product, "teg": str(table.get("teg") or name), **raw})
    except (FileNotFoundError, LookupError, ValueError) as exc:
        return _payload_error(
            f"{product} {name}의 실제 좌표를 계산할 기준 데이터가 없습니다: {exc}",
            context,
            missing=["coordinate_source"],
        )

    warnings = []
    if total > len(shown):
        warnings.append(f"좌표 {total:,}행 중 앞의 {len(shown):,}행만 표시했습니다.")
    try:
        native_payload = teg_map.map_payload(product)
        teg_maps = _teg_maps_payload(product, names, native_payload)
        teg_view = _native_teg_view(product, names, native_payload)
    except (FileNotFoundError, LookupError, ValueError) as exc:
        teg_maps = _unavailable_teg_maps(product, f"TEG 위치 그림 기준 데이터를 읽지 못했습니다: {exc}")
        teg_view = None
    tool = {
        "feature": "teg",
        "action": "teg.coordinates",
        "table": {
            "rows": shown,
            "columns": ["product", "teg", "shot_x", "shot_y", "abs_x", "abs_y", "radius"],
            "total": total,
        },
        "teg_maps": teg_maps,
        "teg_view": teg_view,
        "sources": ["TEG 위치조회 · Chip_Radius + Teg_location"],
        "warnings": warnings,
    }
    return _result(f"{display_product} {', '.join(names)}의 shot별 실제 좌표 {total:,}행입니다. abs_x·abs_y·radius의 단위는 mm입니다.", tool, context)


def _mapfiles(product: str, context: dict) -> dict:
    from core import mapfile_traffic
    display_product = str(context.get("db_product") or product)

    try:
        data = mapfile_traffic.inspect_mapfiles_for_product(product, force=False)
    except (FileNotFoundError, LookupError, ValueError, OSError) as exc:
        return _payload_error(f"{product}의 Mapfile 검증 결과를 읽지 못했습니다: {exc}", context)
    rows = []
    for raw in data.get("files") or []:
        if not isinstance(raw, dict):
            continue
        rows.append({
            "product": display_product,
            "filename": raw.get("filename"),
            "status": raw.get("status"),
            "traffic_light": raw.get("traffic_light"),
            "sl_light": (raw.get("sl") or {}).get("light"),
            "main_light": (raw.get("main") or {}).get("light"),
            "issue_count": len(raw.get("issues") or []),
            "verified_at": raw.get("verified_at"),
            "cached": bool(raw.get("is_cached")),
        })
    total = len(rows)
    shown = rows[:MAX_MAPFILE_ROWS]
    warnings = []
    if total > len(shown):
        warnings.append(f"Mapfile {total:,}개 중 앞의 {len(shown):,}개만 표시했습니다.")
    if not rows:
        warnings.append("제품 코드와 일치하는 Mapfile 검증 결과가 없습니다.")
    context.update(product=display_product, teg_product=product, last_action="teg.mapfiles")
    tool = {
        "feature": "teg",
        "action": "teg.mapfiles",
        "table": {
            "rows": shown,
            "columns": ["product", "filename", "status", "traffic_light", "sl_light", "main_light", "issue_count", "verified_at", "cached"],
            "total": total,
        },
        "sources": ["TEG Mapfile 검증 결과"],
        "warnings": warnings,
        "summary": data.get("summary") or {},
    }
    return _result(f"{display_product} Mapfile 검증 결과 {total:,}개입니다.", tool, context)


def execute(action: str, params: dict | None, context: dict | None = None, request: Any = None) -> dict:
    """Execute one allowlisted TEG read action with exact product/TEG names."""
    action = str(action or "").strip()
    if action not in ACTION_SCHEMAS:
        raise ValueError(f"unsupported TEG action: {action}")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ValueError("TEG params must be an object")
    unexpected = sorted(set(params) - ACTION_SCHEMAS[action])
    if unexpected:
        raise ValueError(f"unsupported parameters for {action}: {', '.join(unexpected)}")

    context = dict(context or {})
    requested_product = _clean_text(params.get("product"), "product")
    product, ambiguous_products = _resolve_product("", requested_product, context, request)
    if ambiguous_products:
        return _payload_error(
            "제품명이 여러 등록 제품과 일치합니다. 정확한 제품을 지정해 주세요: " + ", ".join(ambiguous_products),
            context,
            missing=["product"],
            rows=[{"product": name} for name in ambiguous_products],
        )
    if not product:
        return _payload_error("등록되고 접근 가능한 정확한 제품명을 지정해 주세요.", context, missing=["product"])
    context.update(product=str(context.get("db_product") or product), teg_product=product, last_action=action)
    if action == "teg.mapfiles":
        return _mapfiles(product, context)

    payload, error = _load_payload(product, context)
    if error:
        return error
    names, error = _select_tegs("", _clean_tegs(params.get("tegs")), context, payload or {})
    if error:
        return error
    return _coordinates(product, names, context) if action == "teg.coordinates" else _locations(product, names, payload or {}, context)


def dispatch(prompt: str, context: dict | None = None, request: Any = None) -> dict | None:
    """Claim and run a deterministic TEG prompt, or return ``None`` for WIP/chat."""
    text = str(prompt or "").strip()
    if not text:
        return None
    context = dict(context or {})
    last_action = str(context.get("last_action") or "")
    active_teg = last_action in ACTION_SCHEMAS
    explicit_teg = bool(_TEG_MARKER_RE.search(text))
    pending = context.get("pending_teg_selection") or {}
    chosen_inputs = None
    if pending and pending.get("product") == context.get("product"):
        if re.fullmatch(r"취소(?:해|해줘)?[.!\s]*", text):
            context.pop("pending_teg_selection", None)
            return _result("TEG 선택을 취소했습니다.", {"feature": "teg"}, context)
        suggestions = pending.get("suggestions") or []
        if suggestions:
            first = suggestions[0]
            candidates = first.get("candidates") or []
            chosen = [name for name in candidates if _literal_in_text(name, text)]
            if re.fullmatch(r"(?:네|예|응|맞아|맞아요|확인|yes)[.!\s]*", text, re.I) and len(candidates) == 1:
                chosen = candidates
            if len(chosen) == 1:
                chosen_inputs = [chosen[0] if raw == first["requested"] else raw for raw in pending.get("inputs", [])]
                explicit_teg = True
            elif re.fullmatch(r"(?:네|예|응|맞아|맞아요|확인|yes)[.!\s]*", text, re.I):
                chosen_inputs = pending.get("inputs") or []
                explicit_teg = True
    if not explicit_teg:
        if not active_teg:
            return None
        # Check real answer-sheet names before mistaking HOL10 for a Lot ID.
        candidate_payload, _ = _load_payload(str(context.get("teg_product") or context.get("product") or ""), context)
        candidate_rows = (candidate_payload or {}).get("tegs") or []
        name_followup = any(any(_resolve_one_teg(token, candidate_rows)) for token in _TOKEN_RE.findall(text))
        if not _TEG_FOLLOWUP_RE.search(text) and not name_followup and not pending:
            return None
        # A new lot identifier is a strong signal that this is a WIP follow-up,
        # even if the previous result happened to be a TEG table.
        if _LOT_TOKEN_RE.search(text) and not name_followup and not pending:
            return None

    action = "teg.mapfiles" if _MAPFILE_RE.search(text) else (
        "teg.coordinates" if _COORDINATE_RE.search(text) else "teg.locations"
    )
    if chosen_inputs:
        action = pending.get("action") if pending.get("action") in ACTION_SCHEMAS else action
    product, ambiguous_products = _resolve_product(text, "", context, request)
    if ambiguous_products:
        return _payload_error(
            "제품이 여러 개 언급됐습니다. 하나를 지정해 주세요: " + ", ".join(ambiguous_products),
            context,
            missing=["product"],
            rows=[{"product": name} for name in ambiguous_products],
        )
    if not product:
        return _payload_error("등록되고 접근 가능한 제품명을 지정해 주세요.", context, missing=["product"])
    context.update(product=str(context.get("db_product") or product), teg_product=product, last_action=action)
    if action == "teg.mapfiles":
        return _mapfiles(product, context)

    payload, error = _load_payload(product, context)
    if error:
        return error
    names, error = _select_tegs("" if chosen_inputs else text, chosen_inputs or [], context, payload or {})
    if error:
        return error
    return _coordinates(product, names, context) if action == "teg.coordinates" else _locations(product, names, payload or {}, context)
