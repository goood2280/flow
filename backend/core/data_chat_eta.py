"""Bounded conversational adapter for the permission-checked LOT Tracker ETA.

The tracker predicts a target step's TKOUT (completion), not its arrival.
Conversation state keeps the target lot, reference lot, and target step separate.
"""
from __future__ import annotations

import re


ETA_KEYS = ("eta_query", "pending_eta")
_INTENT = re.compile(r"도착|언제쯤|언제|예정|예측|\beta\b|계산|산출|완료\s*시각", re.I)
_REFERENCE = re.compile(r"기준|참고|비교|레퍼런스|reference|어떤\s*(?:랏|lot)|무슨\s*(?:랏|lot)", re.I)
_LOT = re.compile(r"(?<![A-Za-z0-9_.])([A-Za-z][A-Za-z0-9_-]{3,}\.[A-Za-z0-9]+)(?![A-Za-z0-9_.])")
_DUMMY_TOKEN = re.compile(r"(?<![A-Za-z0-9_-])(DEMO[A-Za-z0-9_-]*|SAMPLE|TEST|MOCK)(?![A-Za-z0-9_-])", re.I)
_STEP = re.compile(r"(?<![A-Za-z0-9_.])([A-Za-z][A-Za-z0-9_-]{3,})\s*(?:에|까지|공정|step)(?![A-Za-z0-9_])", re.I)
_DUMMY = {"SAMPLE", "TEST", "MOCK"}


def _key(value):
    return str(value or "").strip().upper()


def _real_lot(value):
    value = _key(value)
    return bool(value and not value.startswith("DEMO") and value not in _DUMMY)


def _answer(message, context, *, tool=None, ok=True):
    from core.data_chat import reply

    details = {"feature": "eta", "sources": ["LOT Tracker FAB TKOUT 이력"]}
    details.update(tool or {})
    return reply(message, context=context, tool=details, ok=ok,
                 interpretation={"summary": message, "product": context.get("product", ""),
                                 "source": "LOT Tracker FAB TKOUT 이력"})


def _reference_candidates(product, target_lot):
    """Offer observed same-product WIP lots; history is checked after selection."""
    from core.lot_progress_cache import lookup_lot_progress

    rows = lookup_lot_progress(product=product, limit=500, refresh_if_missing=False)
    names = {_key(row.get("lot_id")) for row in rows
             if _key(row.get("product")) == _key(product) and _real_lot(row.get("lot_id"))}
    names.discard(_key(target_lot))
    return sorted(names)[:5]


def _ask_reference(context):
    query = context["eta_query"]
    options = [{"label": name, "value": name}
               for name in _reference_candidates(query["product"], query["lot_id"])]
    return _answer(
        f"{query['lot_id']}의 {query['target_step_id']} 완료 시각을 추정할 참고 LOT을 선택해 주세요. "
        "아래는 같은 제품의 현재 WIP 후보이며, 필요한 공정 이력은 선택 후 확인합니다.",
        context, tool={"missing": ["reference_lot_id"], "clarification": {
            "kind": "eta_reference", "title": "참고 LOT 선택", "options": options,
            "allow_other": True, "placeholder": "참고 LOT ID 입력"}})


def _tracker(request, query, reference=""):
    # Call the router, not core.track_lot: the router enforces lottracker access.
    from routers.lot_tracker import get_lot_tracker

    return get_lot_tracker(request=request, lot_id=query["lot_id"],
                           reference_lot_id=reference, target_step_id=query["target_step_id"],
                           product=query["product"], dummy=False)


def _valid_forecast(result, query):
    """Reject the tracker's fallback ETA when an exact target TKOUT is absent."""
    if not result.get("ok") or result.get("is_dummy"):
        return None
    lot = result.get("lot") or {}
    if (_key(lot.get("lot_id")) != query["lot_id"] or
            _key(lot.get("product")) != _key(query["product"])):
        return None
    points = lot.get("points") or []
    if not points:
        return None
    anchor = _key(points[-1].get("step_id"))
    target = query["target_step_id"]
    ref_id = query["reference_lot_id"]
    references = [ref for ref in result.get("references") or []
                  if _key(ref.get("lot_id")) == ref_id and _key(ref.get("product")) == _key(query["product"])]
    if len(references) != 1:
        return None
    ref_points = references[0].get("points") or []
    anchors = [i for i, point in enumerate(ref_points) if _key(point.get("step_id")) == anchor]
    targets = [i for i, point in enumerate(ref_points) if _key(point.get("step_id")) == target]
    if not anchors or not targets or targets[-1] <= anchors[-1]:
        return None
    forecast = result.get("forecast") or {}
    if (_key(forecast.get("target_step_id")) != target or not forecast.get("eta") or
            not any(_key(item.get("lot_id")) == ref_id and item.get("eta")
                    for item in forecast.get("ref_summaries") or []) or
            not any(_key(point.get("step_id")) == target and not point.get("is_anchor")
                    for point in forecast.get("points") or [])):
        return None
    return forecast


def dispatch(prompt: str, context: dict, request) -> dict | None:
    """Handle an ETA request or active ETA follow-up; otherwise return None.

    The caller has already resolved/confirmed the product. Only ETA_KEYS need
    to be whitelisted across HTTP turns, in addition to confirmed_product.
    """
    text = str(prompt or "").strip()
    if not text:
        return None
    context = dict(context or {})
    prior = context.get("eta_query") if isinstance(context.get("eta_query"), dict) else {}
    prior = {key: _key(prior.get(key)) for key in
             ("product", "lot_id", "target_step_id", "reference_lot_id")}
    if context.get("pending_eta") and re.fullmatch(r"취소(?:해|해줘)?[.!\s]*", text):
        context.pop("pending_eta", None)
        context.pop("eta_query", None)
        return _answer("예상 시각 계산을 취소했습니다.", context)
    fresh_intent = bool(re.search(r"도착|언제쯤|예정|예측|\beta\b|완료\s*시각", text, re.I))
    followup = bool(prior.get("lot_id") and (_INTENT.search(text) or _REFERENCE.search(text) or _LOT.fullmatch(text)))
    if not (fresh_intent or followup or (context.get("pending_eta") and _STEP.search(text))):
        return None

    product = _key(context.get("confirmed_product") or context.get("product"))
    if not product:
        return None  # The main data-chat product resolver asks for confirmation.
    if prior.get("product") and prior["product"] != product:
        prior = {}  # A confirmed product switch cannot retain an old basis.

    lots = [_key(match.group(1)) for match in _LOT.finditer(text)]
    if not lots:
        dummy_match = _DUMMY_TOKEN.search(text)
        if dummy_match:
            lots = [_key(dummy_match.group(1))]
    explicit_reference = bool(_REFERENCE.search(text))
    bare_reference = bool(context.get("pending_eta") and _LOT.fullmatch(text) and prior.get("lot_id") and prior.get("target_step_id"))
    new_target = bool(lots and (not prior.get("lot_id") or
                                (lots[0] != prior["lot_id"] and not explicit_reference and not bare_reference)))
    query = {"product": product, "lot_id": prior.get("lot_id", ""),
             "target_step_id": prior.get("target_step_id", ""),
             "reference_lot_id": prior.get("reference_lot_id", "")}
    if new_target:
        query.update(lot_id=lots[0], target_step_id="", reference_lot_id="")
        lots = lots[1:]
    step_match = _STEP.search(text)
    if step_match:
        query["target_step_id"] = _key(step_match.group(1))
        if prior.get("target_step_id") != query["target_step_id"]:
            query["reference_lot_id"] = ""
    if explicit_reference and lots:
        query["reference_lot_id"] = lots[0]
    # A bare lot supplied after a reference question is the reference, not a new target.
    if not new_target and lots and context.get("pending_eta") and not query["reference_lot_id"]:
        query["reference_lot_id"] = lots[0]

    context["eta_query"] = query
    context["pending_eta"] = True
    context["last_action"] = "eta"
    context["product"] = product
    if query["lot_id"]:
        context["lot_id"] = query["lot_id"]
        context["fab_lot_id"] = query["lot_id"]
        context["root_lot_id"] = query["lot_id"].split(".")[0][:5]
    if not query["lot_id"]:
        return _answer("완료 시각을 추정할 FAB LOT ID를 알려 주세요.", context,
                       tool={"missing": ["lot_id"]})
    if not _real_lot(query["lot_id"]):
        return _answer("실제 FAB LOT ID를 알려 주세요. 예시/더미 LOT으로는 계산하지 않습니다.",
                       context, tool={"missing": ["lot_id"]}, ok=False)
    if not query["target_step_id"]:
        return _answer("목표 공정 STEP ID를 알려 주세요.", context,
                       tool={"missing": ["target_step_id"]})
    if not query["reference_lot_id"]:
        result = _tracker(request, query)  # Permission and real target history first.
        if result.get("is_dummy") or not result.get("ok") or not result.get("lot"):
            return _answer(result.get("note") or "대상 LOT의 실제 TKOUT 이력이 없습니다.",
                           context, tool={"missing": ["lot_history"]}, ok=False)
        return _ask_reference(context)
    if not _real_lot(query["reference_lot_id"]) or query["reference_lot_id"] == query["lot_id"]:
        query["reference_lot_id"] = ""
        return _answer("대상과 다른 실제 참고 LOT ID를 알려 주세요.", context,
                       tool={"missing": ["reference_lot_id"]}, ok=False)

    result = _tracker(request, query, query["reference_lot_id"])
    forecast = _valid_forecast(result, query)
    if not forecast:
        query["reference_lot_id"] = ""
        return _answer(
            "선택한 참고 LOT에서 대상 LOT의 마지막 완료 공정과 목표 공정의 실제 TKOUT 이력을 "
            "모두 확인할 수 없어 예상 시각을 계산할 수 없습니다. 다른 참고 LOT을 지정해 주세요.",
            context, tool={"missing": ["reference_lot_id"]}, ok=False)
    context["pending_eta"] = False
    # TKIN is the arrival/entry basis when the source actually has it. Do not
    # relabel the tracker's TKOUT estimate as arrival when TKIN is absent.
    from core.lot_tracker import _datetime
    current = result["lot"]["points"][-1]
    ref_points = next(ref["points"] for ref in result["references"] if _key(ref["lot_id"]) == query["reference_lot_id"])
    anchor = next(p for p in ref_points if _key(p["step_id"]) == _key(current["step_id"]))
    target = next(p for p in ref_points if _key(p["step_id"]) == query["target_step_id"])
    entry = _datetime(target.get("tkin_time"))
    baseline = _datetime(anchor.get("tkout_time"))
    current_time = _datetime(current.get("tkout_time"))
    eta, kind = forecast["eta"], "완료(TKOUT)"
    if entry and baseline and current_time and entry >= baseline:
        eta = (current_time + (entry - baseline)).isoformat(timespec="seconds")
        kind = "도착·진입(TKIN)"
    rows = [{"대상 FAB Lot": query["lot_id"], "목표 Step": query["target_step_id"],
             "기준 FAB Lot": query["reference_lot_id"], "예상 종류": kind, "예상 시각": eta,
             "계산 시작 시각": current["tkout_time"]}]
    return _answer(
        f"{product} {query['lot_id']}의 {query['target_step_id']} 예상 {kind} 시각은 "
        f"{eta}입니다. 참고 LOT {query['reference_lot_id']}의 실제 공정 간 소요시간을 "
        "대상 LOT의 마지막 TKOUT 시각에 적용했습니다. "
        + ("공정 도착 시각은 이 데이터로 구분해 산출할 수 없습니다. " if kind == "완료(TKOUT)" else "")
        + "기준랏의 과거 소요시간에 따른 추정이며 FAB 데이터 적재 시차가 있습니다.",
        context, tool={"eta": eta, "estimate_kind": kind, "table": {"rows": rows, "total": 1},
                       "remaining_days": round((_datetime(eta) - current_time).total_seconds()/86400, 2) if current_time else None,
                       "lot_id": query["lot_id"], "reference_lot_id": query["reference_lot_id"],
                       "target_step_id": query["target_step_id"], "forecast": forecast})
