"""Lot/Split impact knowledge spine helpers.

This module keeps the domain-specific layer thin: operational pages append
standardized KnowledgeEvent rows, and readers build a read-only context from
verified Agent Wiki pages plus candidate raw events.
"""
from __future__ import annotations

import json
import re
from typing import Any

from app_v2.shared.contracts import FlowEntityKey, KnowledgeEvent
from core import knowledge_vault as kv

IMPACT_EVENT_TYPES = {"lot_anomaly", "split_impact", "mts_change", "anchor_item_change"}
IMPACT_SCHEMA_TYPES = {"lot_anomaly_summary", "split_impact_rule", "mts_change_record", "anchor_item_registry"}
SOURCE_TYPES = {"edm_version", "split_note", "issue", "meeting", "report", "agent_action", "agent_wiki_source", "agent_wiki_ingest", "manual", "system"}
PAYLOAD_FIELDS = [
    "product",
    "root_lot_id",
    "wafer_id",
    "step_id",
    "item_id",
    "knob_name",
    "split_value",
    "effect_direction",
    "effect_confidence",
    "status",
    "source_refs",
    "changed_at",
]

_CONFIDENCE_WORDS = {
    "confirmed": 1.0,
    "verified": 1.0,
    "high": 0.85,
    "medium": 0.55,
    "mid": 0.55,
    "low": 0.25,
    "candidate": 0.35,
    "needs_review": 0.2,
    "확정": 1.0,
    "높음": 0.85,
    "중간": 0.55,
    "낮음": 0.25,
    "후보": 0.35,
}


def _clean(value: Any, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _norm_key(value: Any) -> str:
    return _clean(value).casefold()


def _same(value: Any, query: str) -> bool:
    if not query:
        return True
    return _norm_key(value) == _norm_key(query)


def _contains(hay: Any, needle: str) -> bool:
    if not needle:
        return True
    return _norm_key(needle) in _norm_key(hay)


def normalize_confidence(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _CONFIDENCE_WORDS:
            return _CONFIDENCE_WORDS[text]
    try:
        parsed = float(value)
    except Exception:
        return None
    if parsed > 1 and parsed <= 100:
        parsed = parsed / 100
    if parsed < 0 or parsed > 1:
        return None
    return round(parsed, 4)


def normalize_source_refs(refs: Any, *, source_type: str = "", source_id: str = "") -> list[dict[str, Any]]:
    values = refs if isinstance(refs, list) else ([refs] if refs not in (None, "", {}) else [])
    if source_id:
        values.insert(0, {"type": source_type or "manual", "id": source_id})
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in values:
        if isinstance(raw, dict):
            item = {
                "type": _clean(raw.get("type") or raw.get("source_type") or source_type or "manual", 80),
                "id": _clean(raw.get("id") or raw.get("source_id") or raw.get("ref") or "", 160),
                "label": _clean(raw.get("label") or raw.get("title") or "", 240),
                "url": _clean(raw.get("url") or raw.get("path") or "", 500),
            }
        else:
            item = {"type": source_type or "manual", "id": _clean(raw, 160), "label": "", "url": ""}
        if not item["id"] and not item["label"] and not item["url"]:
            continue
        key = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        out.append({k: v for k, v in item.items() if v})
        if len(out) >= 16:
            break
    return out


def normalize_payload(
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    source_type: str = "",
    source_id: str = "",
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if event_type not in IMPACT_EVENT_TYPES:
        raise ValueError(f"unsupported impact event_type: {event_type}")
    raw = {**(defaults or {}), **(payload or {})}
    out = dict(raw)
    out["event_type"] = event_type
    for key in PAYLOAD_FIELDS:
        out.setdefault(key, "")
    for key in ("product", "root_lot_id", "wafer_id", "step_id", "item_id", "knob_name", "split_value", "effect_direction", "status", "changed_at"):
        out[key] = _clean(out.get(key), 240)
    out["effect_confidence"] = normalize_confidence(out.get("effect_confidence"))
    if raw.get("effect_confidence") not in (None, "") and out["effect_confidence"] is None:
        out["confidence_parse_error"] = True
    out["source_refs"] = normalize_source_refs(raw.get("source_refs"), source_type=source_type, source_id=source_id)
    if not out["changed_at"]:
        out["changed_at"] = kv.now_iso()
    if not out["status"]:
        out["status"] = "candidate"
    if event_type == "split_impact":
        basis = [
            out.get("product"),
            out.get("root_lot_id"),
            out.get("step_id"),
            out.get("knob_name") or out.get("item_id"),
            out.get("split_value"),
        ]
        out["impact_key"] = _clean(raw.get("impact_key") or "|".join(str(x or "") for x in basis), 500)
    if event_type == "anchor_item_change":
        out["anchor_key"] = _clean(raw.get("anchor_key") or "|".join(str(out.get(k) or "") for k in ("product", "step_id", "item_id")), 500)
        out.setdefault("valid_from", out.get("changed_at") or kv.now_iso())
        out.setdefault("valid_to", "")
        out.setdefault("replaced_by", "")
        out.setdefault("reason", "")
    return out


def append_domain_event(
    *,
    event_type: str,
    source_type: str,
    source_id: str,
    title: str,
    summary: str,
    actor: str = "",
    payload: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    source = source_type if source_type in SOURCE_TYPES else "manual"
    norm = normalize_payload(event_type, payload, source_type=source, source_id=source_id)
    entity = FlowEntityKey(
        product=norm.get("product") or "",
        root_lot_id=norm.get("root_lot_id") or "",
        wafer_id=norm.get("wafer_id") or "",
    )
    event = KnowledgeEvent(
        event_type=event_type,
        source_type=source,
        source_id=source_id,
        title=_clean(title, 240) or event_type,
        summary=_clean(summary, 1000),
        actor=actor or "",
        entity=entity,
        tags=list(dict.fromkeys([event_type, *(tags or [])])),
        payload=norm,
    )
    return kv.append_event(event)


def safe_append_domain_event(**kwargs: Any) -> dict[str, Any] | None:
    try:
        return append_domain_event(**kwargs)
    except Exception:
        return None


def _extract_step(text: str) -> str:
    for pattern in (r"\b(?:step_id|step|공정)\s*[:=]?\s*([A-Za-z0-9_.-]{2,80})", r"\b(\d+(?:\.\d+){0,3})\s*(?:SORT|STI|METAL|ETCH|CMP)?"):
        m = re.search(pattern, text, re.I)
        if m:
            return _clean(m.group(1), 80)
    return ""


def _extract_item(text: str) -> str:
    m = re.search(r"\b((?:INLINE|ET|VM|MASK|KNOB|FAB)_[A-Za-z0-9_.\-]+)", text, re.I)
    if m:
        return _clean(m.group(1), 120)
    m = re.search(r"\b(?:item_id|item|항목)\s*[:=]?\s*([A-Za-z0-9_.\-]+)", text, re.I)
    return _clean(m.group(1), 120) if m else ""


def _extract_knob(text: str, item_id: str = "") -> str:
    m = re.search(r"\b(KNOB_[A-Za-z0-9_.\-]+)", text, re.I)
    if m:
        return _clean(m.group(1), 120)
    if item_id.upper().startswith(("KNOB_", "MASK_")):
        return item_id
    m = re.search(r"\b(?:knob|노브)\s*[:=]?\s*([A-Za-z0-9_.\-]+)", text, re.I)
    return _clean(m.group(1), 120) if m else ""


def _extract_direction(text: str) -> str:
    low = text.lower()
    if any(t in low or t in text for t in ("갈림", "conflict", "mixed", "상반", "충돌")):
        return "mixed"
    if any(t in low or t in text for t in ("증가", "상승", "악화", "높아", "increase", "worse", "positive")):
        return "positive"
    if any(t in low or t in text for t in ("감소", "하락", "개선", "낮아", "decrease", "improve", "negative")):
        return "negative"
    return "unknown"


def extract_candidate_events(
    text: str,
    *,
    context: dict[str, Any] | None = None,
    allowed_event_types: set[str] | None = None,
    status: str = "candidate",
) -> list[dict[str, Any]]:
    body = str(text or "")
    if not body.strip():
        return []
    ctx = dict(context or {})
    item = _clean(ctx.get("item_id") or _extract_item(body), 120)
    knob = _clean(ctx.get("knob_name") or _extract_knob(body, item), 120)
    base = {
        "product": _clean(ctx.get("product"), 120),
        "root_lot_id": _clean(ctx.get("root_lot_id"), 120),
        "wafer_id": _clean(ctx.get("wafer_id"), 80),
        "step_id": _clean(ctx.get("step_id") or _extract_step(body), 120),
        "item_id": item,
        "knob_name": knob,
        "split_value": _clean(ctx.get("split_value"), 120),
        "effect_direction": _extract_direction(body),
        "effect_confidence": ctx.get("effect_confidence"),
        "status": status,
        "source_refs": ctx.get("source_refs") or [],
        "changed_at": ctx.get("changed_at") or kv.now_iso(),
    }
    low = body.lower()
    out: list[dict[str, Any]] = []

    def allowed(kind: str) -> bool:
        return allowed_event_types is None or kind in allowed_event_types

    if allowed("lot_anomaly") and ("lot" in low or base["root_lot_id"]) and any(t in low or t in body for t in ("이상", "불량", "anomaly", "abnormal", "문제", "issue")):
        out.append({"event_type": "lot_anomaly", "payload": {**base}, "summary": _clean(body, 800)})
    if allowed("split_impact") and any(t in low or t in body for t in ("split", "스플릿", "knob", "노브", "mask", "plan", "actual")) and any(t in low or t in body for t in ("영향", "impact", "effect", "평가", "drift", "갈림", "충돌", "mismatch", "불일치", "달라")):
        out.append({"event_type": "split_impact", "payload": {**base}, "summary": _clean(body, 800)})
    if allowed("mts_change") and any(t in low or t in body for t in ("mts", "recipe", "레시피", "공정 변경", "recipe 변경", "조건 변경")):
        out.append({"event_type": "mts_change", "payload": {**base, "status": status or "needs_review"}, "summary": _clean(body, 800)})
    if allowed("anchor_item_change") and any(t in low or t in body for t in ("anchor", "앵커", "대표 item", "메인 item")) and any(t in low or t in body for t in ("변경", "교체", "등록", "change", "replace")):
        out.append({"event_type": "anchor_item_change", "payload": {**base, "status": status or "needs_review"}, "summary": _clean(body, 800)})
    return out


def append_candidates_from_text(
    text: str,
    *,
    source_type: str,
    source_id: str,
    actor: str,
    context: dict[str, Any] | None = None,
    allowed_event_types: set[str] | None = None,
    status: str = "candidate",
    title_prefix: str = "",
) -> list[dict[str, Any]]:
    rows = extract_candidate_events(text, context=context, allowed_event_types=allowed_event_types, status=status)
    saved: list[dict[str, Any]] = []
    for row in rows:
        event_type = row["event_type"]
        saved_row = safe_append_domain_event(
            event_type=event_type,
            source_type=source_type,
            source_id=source_id,
            title=f"{title_prefix} {event_type}".strip(),
            summary=row.get("summary") or "",
            actor=actor,
            payload=row.get("payload") or {},
        )
        if saved_row:
            saved.append(saved_row)
    return saved


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    return row.get("payload") if isinstance(row.get("payload"), dict) else {}


def _event_value(row: dict[str, Any], key: str) -> Any:
    payload = _payload(row)
    if key in payload:
        return payload.get(key)
    if key in {"product", "root_lot_id", "wafer_id"}:
        return (row.get("entity") or {}).get(key)
    return row.get(key)


def _matches_event(row: dict[str, Any], *, product: str = "", root_lot_id: str = "", step_id: str = "", item_id: str = "", knob: str = "") -> bool:
    if str(row.get("event_type") or "generic") not in IMPACT_EVENT_TYPES:
        return False
    if product and not _same(_event_value(row, "product"), product):
        return False
    if root_lot_id and not _same(_event_value(row, "root_lot_id"), root_lot_id):
        return False
    if step_id and not _same(_event_value(row, "step_id"), step_id):
        return False
    if item_id and not (_same(_event_value(row, "item_id"), item_id) or _same(_event_value(row, "knob_name"), item_id)):
        return False
    if knob and not (_same(_event_value(row, "knob_name"), knob) or _same(_event_value(row, "item_id"), knob)):
        return False
    return True


def _matches_anchor_scope_history(row: dict[str, Any], *, product: str = "", step_id: str = "", item_id: str = "", knob: str = "") -> bool:
    if str(row.get("event_type") or "") != "anchor_item_change":
        return False
    target_item = item_id or knob
    if not target_item:
        return False
    if product and not _same(_event_value(row, "product"), product):
        return False
    if step_id and not _same(_event_value(row, "step_id"), step_id):
        return False
    return True


def _event_ref(row: dict[str, Any]) -> dict[str, Any]:
    payload = _payload(row)
    ref = {
        "event_id": row.get("event_id") or "",
        "event_type": row.get("event_type") or "generic",
        "title": row.get("title") or "",
        "status": payload.get("status") or "",
        "source_type": row.get("source_type") or "",
        "source_id": row.get("source_id") or "",
        "source_refs": payload.get("source_refs") or [],
        "changed_at": payload.get("changed_at") or row.get("created_at") or "",
        "summary": row.get("summary") or "",
    }
    detail_keys = (
        "product",
        "root_lot_id",
        "wafer_id",
        "step_id",
        "item_id",
        "knob_name",
        "split_value",
        "previous_value",
        "old_value",
        "from_value",
        "new_value",
        "current_value",
        "to_value",
        "previous_threshold",
        "old_threshold",
        "from_threshold",
        "new_threshold",
        "current_threshold",
        "to_threshold",
        "baseline",
        "baseline_value",
        "criteria",
        "criterion",
        "reason",
    )
    for key in detail_keys:
        value = payload.get(key)
        if value not in (None, "", [], {}):
            ref[key] = value
    return ref


def _doc_matches(doc: dict[str, Any], *, product: str = "", root_lot_id: str = "", step_id: str = "", item_id: str = "", knob: str = "") -> bool:
    fm = doc.get("frontmatter") if isinstance(doc.get("frontmatter"), dict) else {}
    schema_type = str(fm.get("schema_type") or doc.get("schema_type") or "")
    if schema_type not in IMPACT_SCHEMA_TYPES:
        return False
    hay = " ".join([
        str(doc.get("doc_id") or ""),
        str(doc.get("title") or ""),
        str(doc.get("summary") or ""),
        str(doc.get("body") or ""),
        " ".join(map(str, doc.get("tags") or [])),
        json.dumps(fm, ensure_ascii=False, default=str),
    ])
    if product and not (_same((doc.get("entity") or {}).get("product"), product) or _same(fm.get("product"), product) or _contains(hay, product)):
        return False
    doc_root = (doc.get("entity") or {}).get("root_lot_id") or fm.get("root_lot_id")
    if root_lot_id and doc_root and not _same(doc_root, root_lot_id):
        return False
    if step_id and not (_same(fm.get("step_id"), step_id) or _contains(hay, step_id)):
        return False
    if item_id and not (_same(fm.get("item_id"), item_id) or _contains(hay, item_id)):
        return False
    if knob and not (_same(fm.get("knob_name"), knob) or _contains(hay, knob)):
        return False
    return True


def _wiki_refs(product: str = "", root_lot_id: str = "", step_id: str = "", item_id: str = "", knob: str = "", limit: int = 40) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in kv.list_docs(limit=1000):
        doc_id = str(row.get("doc_id") or "").strip()
        if not doc_id or doc_id in seen:
            continue
        doc = kv.get_doc(doc_id) or {}
        if not _doc_matches(doc, product=product, root_lot_id=root_lot_id, step_id=step_id, item_id=item_id, knob=knob):
            continue
        fm = doc.get("frontmatter") if isinstance(doc.get("frontmatter"), dict) else {}
        seen.add(doc_id)
        refs.append({
            "doc_id": doc_id,
            "id": doc_id,
            "title": doc.get("title") or doc_id,
            "summary": doc.get("summary") or "",
            "schema_type": fm.get("schema_type") or "",
            "source_event_ids": doc.get("source_event_ids") or fm.get("source_event_ids") or [],
            "source_ids": fm.get("source_ids") or [],
            "path": doc.get("path") or "",
            "status": "verified",
        })
        if len(refs) >= limit:
            break
    return refs


def build_anchor_item_registry(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [row for row in events if str(row.get("event_type") or "") == "anchor_item_change"]
    rows.sort(key=lambda row: str(_payload(row).get("changed_at") or row.get("created_at") or ""))
    versions: list[dict[str, Any]] = []
    open_by_scope: dict[tuple[str, str], dict[str, Any]] = {}
    open_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        p = _payload(row)
        product = _clean(p.get("product"))
        step = _clean(p.get("step_id"))
        item = _clean(p.get("item_id") or p.get("knob_name"))
        if not product or not step or not item:
            continue
        valid_from = _clean(p.get("valid_from") or p.get("changed_at") or row.get("created_at"))
        scope = (product.casefold(), step.casefold())
        key = (*scope, item.casefold())
        previous_item = _clean(p.get("previous_item_id") or p.get("replaces_item_id"))
        if previous_item:
            prev = open_by_key.get((*scope, previous_item.casefold()))
            if prev and not prev.get("valid_to"):
                prev["valid_to"] = valid_from
                prev["replaced_by"] = item
                prev["reason"] = prev.get("reason") or _clean(p.get("reason"))
        current = open_by_scope.get(scope)
        if current and current.get("item_id") != item and not current.get("valid_to"):
            current["valid_to"] = valid_from
            current["replaced_by"] = item
            current["reason"] = current.get("reason") or _clean(p.get("reason"))
        version = {
            "product": product,
            "step_id": step,
            "item_id": item,
            "valid_from": valid_from,
            "valid_to": _clean(p.get("valid_to")),
            "replaced_by": _clean(p.get("replaced_by")),
            "reason": _clean(p.get("reason")),
            "status": _clean(p.get("status")),
            "event_id": row.get("event_id") or "",
        }
        versions.append(version)
        if not version.get("valid_to"):
            open_by_scope[scope] = version
            open_by_key[key] = version
    return versions


def _conflicts(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in events:
        if str(row.get("event_type") or "") != "split_impact":
            continue
        p = _payload(row)
        key = str(p.get("impact_key") or "").strip()
        if not key:
            key = "|".join(str(p.get(k) or "") for k in ("product", "root_lot_id", "step_id", "knob_name", "item_id", "split_value"))
        groups.setdefault(key, []).append(row)
    out: list[dict[str, Any]] = []
    for key, rows in groups.items():
        directions = sorted({str(_payload(r).get("effect_direction") or "") for r in rows if _payload(r).get("effect_direction")})
        values = sorted({str(_payload(r).get("split_value") or "") for r in rows if _payload(r).get("split_value")})
        flagged = any(bool(_payload(r).get("conflicting_evidence")) for r in rows)
        if flagged or len(directions) > 1 or len(values) > 1:
            out.append({
                "impact_key": key,
                "conflicting_evidence": True,
                "directions": directions,
                "split_values": values,
                "event_ids": [r.get("event_id") for r in rows if r.get("event_id")],
                "count": len(rows),
            })
    return out


def impact_context(
    *,
    product: str = "",
    root_lot_id: str = "",
    step_id: str = "",
    item_id: str = "",
    knob: str = "",
    limit: int = 200,
) -> dict[str, Any]:
    product = _clean(product, 120)
    root_lot_id = _clean(root_lot_id, 120)
    step_id = _clean(step_id, 120)
    item_id = _clean(item_id, 120)
    knob = _clean(knob, 120)
    events = [
        row for row in kv.list_events(limit=1000)
        if (
            _matches_event(row, product=product, root_lot_id=root_lot_id, step_id=step_id, item_id=item_id, knob=knob)
            or _matches_anchor_scope_history(row, product=product, step_id=step_id, item_id=item_id, knob=knob)
        )
    ][: max(1, min(int(limit or 200), 1000))]
    wiki_refs = _wiki_refs(product=product, root_lot_id=root_lot_id, step_id=step_id, item_id=item_id, knob=knob)
    by_type = {kind: [] for kind in IMPACT_EVENT_TYPES}
    for row in events:
        by_type.setdefault(str(row.get("event_type") or "generic"), []).append(_event_ref(row))
    conflicts = _conflicts(events)
    anchor_items = build_anchor_item_registry(events)
    for doc in wiki_refs:
        if doc.get("schema_type") == "anchor_item_registry":
            anchor_items.append({
                "product": product or "",
                "step_id": step_id or "",
                "item_id": item_id or knob or "",
                "status": "verified_wiki",
                "wiki_doc_id": doc.get("doc_id"),
                "title": doc.get("title"),
            })
    if wiki_refs:
        confidence = "verified_wiki"
    elif any(((_payload(row).get("status") or "") in {"confirmed", "verified", "validated"}) for row in events):
        confidence = "event_confirmed"
    elif events:
        confidence = "candidate_only"
    else:
        confidence = "none"
    return {
        "query": {
            "product": product,
            "root_lot_id": root_lot_id,
            "step_id": step_id,
            "item_id": item_id,
            "knob": knob,
        },
        "anchor_items": anchor_items,
        "lot_anomalies": by_type.get("lot_anomaly", []),
        "split_impacts": by_type.get("split_impact", []),
        "mts_changes": by_type.get("mts_change", []),
        "conflicts": conflicts,
        "wiki_refs": wiki_refs,
        "event_refs": [_event_ref(row) for row in events],
        "confidence": confidence,
    }
