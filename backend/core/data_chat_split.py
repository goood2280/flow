"""Server-owned SplitTable proposals: preview first, explicit approval, one commit.

The model and browser never supply executable cell edits on approval. S0 remains
the existing SplitTable SOP basis; assigning recipes does not change the SOP.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid

from fastapi import HTTPException
from core.auth import require_admin
from core.paths import PATHS
from core.utils import load_json, save_json

TTL_SECONDS = 1800
APPROVE = re.compile(r"^(?:승인(?:합니다|하겠습니다|하겠다|할게|해|하고)?|진행(?:합니다|하겠습니다|하겠다|해|해줘)?|반영(?:해|해줘)?|네|예|[\s,.!])+[.!]?$", re.I)
CANCEL = re.compile(r"^(?:취소|취소해|취소해줘|취소하겠다|취소합니다|반영하지마|승인하지마)[.!\s]*$")
EDIT = re.compile(r"(?:스플릿|split).*(?:깔아|배정|수정|적용|설정|나눠|넣어)|S0.*S1", re.I)
ASSIGNMENT = re.compile(
    r"(?P<column>[\w.\-]+)\s*(?:Split|스플릿)\s*"
    r"(?P<wafers>#[\d\s,#~\-]+?)\s*(?:은|는|을|를)?\s*S0\s*(?:으로|로)?\s*"
    r"(?P<s0>[A-Za-z0-9_.:/\-]+)\s*(?:으로|로)?\s*[,;]?\s*"
    r"나머지(?:는|은)?\s*S1\s*(?:으로|로)?\s*(?P<s1>[A-Za-z0-9_.:/\-]+)", re.I)


def _answer(message, context, *, table=None, approval=None, ok=True):
    tool = {"feature": "splittable.plan", "sources": ["SplitTable 실제 wafer 목록·계획·S0 기준"]}
    if table is not None:
        tool["table"] = {"rows": table, "total": len(table)}
    if approval:
        tool["approval"] = approval
    return {"ok": ok, "reply": message, "tool": tool, "context": context}


def _proposal_path(identifier):
    if not re.fullmatch(r"[0-9a-f]{32}", str(identifier or "")):
        raise ValueError("승인할 미리보기가 없습니다. 수정 내용을 먼저 요청해 주세요.")
    return PATHS.data_root / "chat_proposals" / f"{identifier}.json"


def _wafer_number(value):
    value = re.sub(r"^(?:#|WAFER|WF|W)\s*", "", str(value).strip(), flags=re.I)
    if not value.isdigit():
        raise ValueError("wafer 번호를 명확히 확인하지 못했습니다. root lot으로 다시 조회해 주세요.")
    return int(value)


def _numbers(text):
    numbers = []
    for part in text.replace("#", "").split(","):
        match = re.fullmatch(r"\s*(\d+)\s*(?:[-~]\s*(\d+))?\s*", part)
        if not match:
            raise ValueError("wafer 목록은 #1,2,3 또는 #1~6 형식으로 알려 주세요.")
        start, end = int(match[1]), int(match[2] or match[1])
        if start < 1 or end < start or end > 1000:
            raise ValueError("wafer 범위를 확인해 주세요.")
        numbers.extend(range(start, end + 1))
    if len(numbers) != len(set(numbers)):
        raise ValueError("wafer 번호가 중복되었습니다.")
    return set(numbers)


def _view(product, root, request):
    from routers import splittable
    result = splittable.view_split(product=product, root_lot_id=root, wafer_ids="", prefix="KNOB",
        custom_name="", view_mode="all", history_mode="all", fab_lot_id="", custom_cols="",
        include_related=False, cache_first=False, request=request)
    if str(result.get("root_lot_id") or "").upper() != str(root).upper():
        raise ValueError("조회한 root lot이 요청과 일치하지 않습니다. 제품과 root lot을 확인해 주세요.")
    return result


def _row(view, name):
    def aliases(raw):
        display = re.sub(r"^(?:KNOB|MASK|FAB)_", "", raw, flags=re.I)
        display = re.sub(r"_Split$", "", display, flags=re.I)
        return {raw.casefold(), display.casefold()}
    rows = [row for row in view.get("rows", []) if name.casefold() in aliases(str(row.get("_param") or ""))]
    if len(rows) != 1:
        raise ValueError(f"{name} 항목을 하나로 확인하지 못했습니다. 정확한 KNOB 항목명을 알려 주세요.")
    if not str(rows[0]["_param"]).upper().startswith("KNOB_"):
        raise ValueError("현재 챗 수정은 KNOB Split 계획만 지원합니다.")
    return rows[0]


def _basis(view, row):
    param = row["_param"]
    progress = view.get("step_progress") or {}
    by_wafer = progress.get("by_wafer") or {}
    if by_wafer:
        by_number = {_wafer_number(k): v for k, v in by_wafer.items()}
        not_reached = bool(view.get("wafer_keys")) and all(
            param in (by_number.get(_wafer_number(k), {}).get("not_reached") or []) for k in view["wafer_keys"])
    else:
        not_reached = param in (progress.get("not_reached") or [])
    has_actual = any(c.get("actual") not in (None, "") for c in row.get("_cells", {}).values())
    mapping = view.get("s0_by_knob" if has_actual and not not_reached else "s0_edit_by_knob") or {}
    return str((mapping.get(param) or {}).get("ppid") or "").strip()


def _source_version(view, row):
    document = {"wafers": view.get("wafer_keys"), "s0": _basis(view, row),
        "actual": {i: {"key": c.get("key"), "actual": c.get("actual")} for i, c in row.get("_cells", {}).items()}}
    return hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _resolve_scope(text, context):
    from core.data_chat import product_candidates
    from routers import splittable
    products = [p["name"] for p in splittable.list_products().get("products", []) if p.get("name")]
    matched = product_candidates(text, products)
    if len(matched) > 1:
        raise ValueError("제품이 여러 개입니다. 하나의 제품을 지정해 주세요.")
    product = matched[0] if matched else context.get("product", "")
    inherited = product_candidates(str(product), products)
    if len(inherited) == 1:
        product = inherited[0]
    if product not in products:
        raise ValueError("수정할 제품과 root lot을 알려 주세요. 예: PRODA LOT01")
    # Do not interpret recipe names or the M1 column as lot IDs.
    prefix = ASSIGNMENT.split(text)[0] if ASSIGNMENT.search(text) else text
    unknown_products = [p for p in re.findall(r"\b(?:ML_TABLE_)?PROD[A-Za-z0-9_]*\b", prefix, re.I)
                        if not product_candidates(p, products)]
    if unknown_products:
        raise ValueError("등록되지 않은 제품입니다: " + ", ".join(unknown_products))
    lots = [s for s in re.findall(r"(?<![\w])([A-Za-z]{2,}\d[A-Za-z0-9]*(?:\.\d+)?)(?![\w])", prefix)
            if not product_candidates(s, products)]
    if len(lots) > 1:
        raise ValueError("수정할 root lot을 하나만 지정해 주세요.")
    root = lots[0].upper() if lots else context.get("root_lot_id", "")
    if not root or "." in root or (matched and product != context.get("product") and not lots):
        raise ValueError("수정할 root lot을 알려 주세요. ‘나머지’는 해당 root lot의 실제 wafer에만 적용됩니다.")
    return product, root


def _preview(text, context, request, user):
    from routers import splittable
    from core.file_transaction import file_transaction
    match = ASSIGNMENT.search(text)
    if not match:
        raise ValueError("예: M1 Split #1,2,3,4,5,6은 S0로 ABC, 나머지는 S1로 ABB로 스플릿 깔아줘. 한 항목씩 수정할 수 있습니다.")
    # Reject extra groups instead of silently applying the first part of a request.
    tail = text[match.end():]
    if re.search(r"S\d|#\d|(?:그리고|추가로).*Split", tail, re.I):
        raise ValueError("한 번에 하나의 항목과 S0/S1 두 그룹만 미리보기할 수 있습니다.")
    selected = _numbers(match["wafers"])
    product, root = _resolve_scope(text, context)
    with file_transaction(splittable._plan_history_path(product)):
        view = _view(product, root, request)
        row = _row(view, match["column"])
        keys = view.get("wafer_keys") or []
        numbers = [_wafer_number(k) for k in keys]
        if not keys or len(set(numbers)) != len(numbers):
            raise ValueError("실제 wafer 목록이 없거나 중복됩니다. 조회를 다시 확인해 주세요.")
        missing = selected - set(numbers)
        if missing:
            raise ValueError("해당 root lot에 없는 wafer: " + ", ".join(map(str, sorted(missing))))
        s0, s1 = match["s0"], match["s1"]
        basis = _basis(view, row)
        if not basis or s0 != basis:
            raise ValueError(f"현재 {row['_param']}의 S0 기준은 {basis or '확인 불가'}입니다. 요청한 {s0}를 S0로 바꾸지 않았습니다. 기준과 맞는 recipe로 다시 요청해 주세요.")
        if s0 == s1:
            raise ValueError("S0와 S1의 recipe가 같습니다. 서로 다른 조건을 지정해 주세요.")
        if not set(numbers) - selected:
            raise ValueError("‘나머지’에 해당하는 wafer가 없습니다. 배정 범위를 확인해 주세요.")
        plans, table = {}, []
        existing = splittable._load_plan_data(product)["plans"]
        for i, (key, number) in enumerate(zip(keys, numbers)):
            cell = row.get("_cells", {}).get(str(i), {})
            cell_key = f"{root}|{key}|{row['_param']}"
            if cell.get("key") and cell["key"] != cell_key:
                raise ValueError("셀의 root lot과 조회 대상이 일치하지 않습니다.")
            value = s0 if number in selected else s1
            plans[cell_key] = value
            table.append({"제품": product, "Root lot": root, "항목": row["_param"], "Wafer": number,
                "현재 실제값": cell.get("actual"), "변경 전 계획": (existing.get(cell_key) or {}).get("value"),
                "변경 후 Split": "S0" if number in selected else "S1", "변경 후 계획": value})
        identifier = uuid.uuid4().hex
        proposal = {"id": identifier, "user": user["username"], "product": product, "root": root,
            "column": row["_param"], "plans": plans, "expected_plans": {k: existing.get(k) for k in plans},
            "source_version": _source_version(view, row), "rows": table, "created": time.time(),
            "status": "pending", "reason": text}
        save_json(_proposal_path(identifier), proposal)
    context.update(product=product, root_lot_id=root, last_action="splittable.plan", pending_split_id=identifier)
    context.pop("split_instruction", None)
    return _answer(f"{product} / {root}의 {row['_param']}에서 #{','.join(map(str, sorted(selected)))}은 S0={s0}, 나머지 실제 wafer #{','.join(map(str, sorted(set(numbers)-selected)))}은 S1={s1}로 이해했습니다. 아래는 변경 전·후 미리보기이며 아직 저장하지 않았습니다. 확인 후 ‘승인하겠다 진행하겠다’ 또는 승인 버튼으로 반영하세요.",
        context, table=table, approval={"id": identifier, "status": "pending", "expires_in": TTL_SECONDS})


def _decide(text, context, request, user):
    from routers import splittable
    from core.file_transaction import file_transaction
    path = _proposal_path(context.get("pending_split_id"))
    with file_transaction(path):
        proposal = load_json(path, {})
        if not proposal or proposal.get("user") != user["username"]:
            raise ValueError("본인이 요청한 미리보기만 승인할 수 있습니다.")
        if proposal["status"] == "applied":
            context.pop("pending_split_id", None)
            return _answer("이미 반영된 요청입니다. 중복 저장하지 않았습니다.", context, table=proposal["rows"])
        if proposal["status"] != "pending" or time.time() - proposal["created"] > TTL_SECONDS:
            context.pop("pending_split_id", None)
            raise ValueError("취소되었거나 만료된 미리보기입니다. 다시 요청해 주세요.")
        if CANCEL.fullmatch(text):
            proposal["status"] = "cancelled"
            save_json(path, proposal)
            context.pop("pending_split_id", None)
            return _answer("수정을 취소했습니다. 계획은 변경하지 않았습니다.", context)
        with file_transaction(splittable._plan_history_path(proposal["product"])):
            # Journal and plans are committed in the same atomic JSON write.
            existing = splittable._load_plan_data(proposal["product"])
            applied = proposal["id"] in existing.get("operations", {})
            if not applied:
                view = _view(proposal["product"], proposal["root"], request)
                row = _row(view, proposal["column"])
                if _source_version(view, row) != proposal["source_version"]:
                    raise ValueError("미리보기 이후 wafer·실제값·S0 기준이 변경됐습니다. 다시 조회하고 승인해 주세요.")
            result = splittable.save_plan(splittable.PlanReq(product=proposal["product"],
                root_lot_id=proposal["root"], plans=proposal["plans"], expected_plans=proposal["expected_plans"],
                username=user["username"], operation_id=proposal["id"], reason="[채팅 승인] " + proposal["reason"]), request=request)
        proposal["status"] = "applied"
        save_json(path, proposal)
    context.pop("pending_split_id", None)
    context.update(product=proposal["product"], root_lot_id=proposal["root"], last_action="splittable.plan")
    return _answer(f"승인한 {result['saved']}개 셀의 계획을 반영했습니다. SplitTable과 변경 이력에서도 확인할 수 있습니다.", context, table=proposal["rows"])


def handle(text, context, request):
    """Return None when unrelated; all writes require authenticated admin approval."""
    decision = APPROVE.fullmatch(text) or CANCEL.fullmatch(text)
    editing = EDIT.search(text)
    if context.get("split_instruction") and re.search(r"TEG|좌표|위치|대시보드|차트|취소", text, re.I) and not editing:
        context.pop("split_instruction", None)
    continuation = bool(context.get("split_instruction")) and not decision
    if not editing and not decision and not continuation:
        return None
    if decision and not context.get("pending_split_id"):
        if context.get("last_action") != "splittable.plan":
            return None
        return _answer("승인할 미리보기가 없습니다. 수정 내용을 먼저 요청해 주세요.", context, ok=False)
    user = require_admin(request)
    try:
        if decision:
            if context.get("last_action") != "splittable.plan":
                return _answer("다른 조회 이후에는 수정 내용을 다시 미리보기하고 승인해 주세요.", context, ok=False)
            return _decide(text, context, request, user)
        instruction = text if editing else text + " " + context["split_instruction"]
        if editing:
            context.pop("pending_split_id", None)
        context["split_instruction"] = instruction
        return _preview(instruction, context, request, user)
    except (ValueError, HTTPException) as exc:
        return _answer(str(exc.detail) if isinstance(exc, HTTPException) else str(exc), context, ok=False)
