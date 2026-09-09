"""Bounded, read-only TEG tools for the home data chat.

The dispatcher only claims prompts that explicitly mention TEG/Mapfile or are
follow-ups to an active TEG result.  This keeps ordinary lot/WIP ``location``
questions on the existing lot-progress path.

Coordinates always come from :mod:`core.teg_map`; this module only selects and
formats rows for the chat response.  It never derives missing coordinates.
"""
from __future__ import annotations

import re
from typing import Any

from core import teg_map


MAX_COORDINATE_ROWS = 2_000
MAX_MAPFILE_ROWS = 100
MAX_TEG_SELECTION = 30
MAX_NAME_LENGTH = 300

ACTION_SCHEMAS = {
    "teg.locations": {"product", "tegs"},
    "teg.coordinates": {"product", "tegs"},
    "teg.mapfiles": {"product"},
}

_TEG_MARKER_RE = re.compile(r"(?<![a-z])teg(?=$|[^a-z])|테그|맵파일|map\s*file", re.I)
_COORDINATE_RE = re.compile(r"좌표|coordinate|abs[_ -]?[xy]|radius|반경", re.I)
_MAPFILE_RE = re.compile(r"맵파일|map\s*file", re.I)
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]*")
_LOT_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]{2,}\d[A-Za-z0-9]*(?:\.\d+)?(?![A-Za-z0-9_])")
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
    names = [str(row["vehicle"]).strip() for row in _catalog(request)]
    by_folded: dict[str, list[str]] = {}
    for name in names:
        by_folded.setdefault(name.casefold(), []).append(name)

    if requested:
        matches = by_folded.get(requested.casefold(), [])
        return (matches[0], []) if len(matches) == 1 else ("", matches)

    mentioned = [name for name in names if _literal_in_text(name, prompt)]
    mentioned = list(dict.fromkeys(mentioned))
    if len(mentioned) == 1:
        return mentioned[0], []
    if len(mentioned) > 1:
        return "", mentioned
    unknown_products = [token for token in re.findall(r"\bPROD[A-Za-z0-9_]*\b", prompt, re.I)
                        if token.casefold() not in by_folded]
    if unknown_products:
        return "", []

    remembered = re.sub(r"^ML_TABLE_", "", str(context.get("product") or context.get("teg_product") or "").strip(), flags=re.I)
    remembered_matches = by_folded.get(remembered.casefold(), []) if remembered else []
    return (remembered_matches[0], []) if len(remembered_matches) == 1 else ("", remembered_matches)


def _payload_error(message: str, context: dict, *, missing: list[str] | None = None,
                   rows: list[dict] | None = None) -> dict:
    tool: dict[str, Any] = {"feature": "teg", "warnings": [message], "sources": []}
    if missing:
        tool["missing"] = missing
    if rows is not None:
        tool["table"] = {"rows": rows, "columns": list(rows[0]) if rows else [], "total": len(rows)}
    return _result(message, tool, context, ok=False)


def _result(message: str, tool: dict, context: dict, *, ok: bool = True) -> dict:
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
    if len(source_matches) == 1:
        return source_matches[0], []
    return "", source_matches or displayed


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
    inputs = requested or _prompt_teg_names(prompt, rows)
    if not inputs:
        inputs = _clean_tegs(context.get("teg_names"))

    selected: list[str] = []
    ambiguous: list[str] = []
    missing: list[str] = []
    for raw in inputs:
        resolved, choices = _resolve_one_teg(raw, rows)
        if resolved:
            if resolved.casefold() not in {name.casefold() for name in selected}:
                selected.append(resolved)
        elif choices:
            ambiguous.extend(choices)
        else:
            missing.append(raw)

    missing.extend(_unknown_teg_tokens(prompt, str(payload.get("vehicle") or ""), inputs))
    missing = list(dict.fromkeys(missing))
    if ambiguous:
        choices = list(dict.fromkeys(ambiguous))
        return [], _payload_error(
            "같은 원본 TEG 이름이 여러 위치에 있습니다. 번호가 붙은 TEG를 지정해 주세요: " + ", ".join(choices),
            context,
            missing=["teg"],
            rows=[{"teg": name} for name in choices],
        )
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
    return selected, None


def _locations(product: str, names: list[str], payload: dict, context: dict) -> dict:
    context.update(product=product, teg_product=product, teg_names=names, last_action="teg.locations")
    wanted = {name.casefold() for name in names}
    rows = []
    for raw in payload.get("tegs") or []:
        if not isinstance(raw, dict) or str(raw.get("teg") or "").casefold() not in wanted:
            continue
        rows.append({
            "product": product,
            "teg": raw.get("teg"),
            "ebeam_x": raw.get("ebeam_x"),
            "ebeam_y": raw.get("ebeam_y"),
            "teg_w": raw.get("teg_w"),
            "teg_h": raw.get("teg_h"),
            "direction": raw.get("flat_zone"),
        })
    tool = {
        "feature": "teg",
        "action": "teg.locations",
        "table": {
            "rows": rows,
            "columns": ["product", "teg", "ebeam_x", "ebeam_y", "teg_w", "teg_h", "direction"],
            "total": len(rows),
        },
        "sources": ["TEG 위치조회 · Teg_location"],
        "warnings": [],
    }
    return _result(f"{product}에서 {', '.join(names)}의 shot 내부 기준 위치를 찾았습니다. 좌표와 크기의 단위는 mm입니다.", tool, context)


def _coordinates(product: str, names: list[str], context: dict) -> dict:
    context.update(product=product, teg_product=product, teg_names=names, last_action="teg.coordinates")
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
                    shown.append({"product": product, "teg": str(table.get("teg") or name), **raw})
    except (FileNotFoundError, LookupError, ValueError) as exc:
        return _payload_error(
            f"{product} {name}의 실제 좌표를 계산할 기준 데이터가 없습니다: {exc}",
            context,
            missing=["coordinate_source"],
        )

    warnings = []
    if total > len(shown):
        warnings.append(f"좌표 {total:,}행 중 앞의 {len(shown):,}행만 표시했습니다.")
    tool = {
        "feature": "teg",
        "action": "teg.coordinates",
        "table": {
            "rows": shown,
            "columns": ["product", "teg", "shot_x", "shot_y", "abs_x", "abs_y", "radius"],
            "total": total,
        },
        "sources": ["TEG 위치조회 · Chip_Radius + Teg_location"],
        "warnings": warnings,
    }
    return _result(f"{product} {', '.join(names)}의 shot별 실제 좌표 {total:,}행입니다. abs_x·abs_y·radius의 단위는 mm입니다.", tool, context)


def _mapfiles(product: str, context: dict) -> dict:
    from core import mapfile_traffic

    try:
        data = mapfile_traffic.inspect_mapfiles_for_product(product, force=False)
    except (FileNotFoundError, LookupError, ValueError, OSError) as exc:
        return _payload_error(f"{product}의 Mapfile 검증 결과를 읽지 못했습니다: {exc}", context)
    rows = []
    for raw in data.get("files") or []:
        if not isinstance(raw, dict):
            continue
        rows.append({
            "product": product,
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
    context.update(product=product, teg_product=product, last_action="teg.mapfiles")
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
    return _result(f"{product} Mapfile 검증 결과 {total:,}개입니다.", tool, context)


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
    context.update(product=product, teg_product=product, last_action=action)
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
    if not explicit_teg:
        if not active_teg or not _TEG_FOLLOWUP_RE.search(text):
            return None
        # A new lot identifier is a strong signal that this is a WIP follow-up,
        # even if the previous result happened to be a TEG table.
        if _LOT_TOKEN_RE.search(text):
            return None

    action = "teg.mapfiles" if _MAPFILE_RE.search(text) else (
        "teg.coordinates" if _COORDINATE_RE.search(text) else "teg.locations"
    )
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
    context.update(product=product, teg_product=product, last_action=action)
    if action == "teg.mapfiles":
        return _mapfiles(product, context)

    payload, error = _load_payload(product, context)
    if error:
        return error
    names, error = _select_tegs(text, [], context, payload or {})
    if error:
        return error
    return _coordinates(product, names, context) if action == "teg.coordinates" else _locations(product, names, payload or {}, context)
