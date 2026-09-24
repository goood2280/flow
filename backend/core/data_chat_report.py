"""Selected-chart report proposals backed by server files, then explicit approval.

The browser only carries a proposal identifier. Creation and title edits are
deterministic; validation and saving use the Template Report canonical path.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re
import time
import uuid

from fastapi import HTTPException

from core.auth import require_admin
from core.file_transaction import file_transaction
from core.paths import PATHS
from core.utils import load_json, save_json

TTL_SECONDS = 1800
MAX_SELECTED_CHARTS = 24
MAX_CHARTS_PER_PAGE = 6
FEATURE = "report.template"
TAGGED_DECISION = re.compile(r"^(승인|취소)\s+([0-9a-f]{32})[.!\s]*$", re.I)
APPROVE = re.compile(r"^(?:승인|승인합니다|승인하겠습니다|승인하겠다|승인할게|승인해|승인해줘|저장해|저장해줘|저장하겠다|저장하겠습니다)(?:\s+진행하겠다)?[.!\s]*$")
CANCEL = re.compile(r"^(?:취소|취소해|취소해줘|취소합니다|승인하지마|저장하지마)[.!\s]*$")
REPORT = re.compile(r"보고서|리포트|report|템플릿|template", re.I)
CREATE = re.compile(r"만들|생성|저장|작성|등록|묶어|템플릿으로|template", re.I)
TITLE = re.compile(r"(?:제목|이름|title|name)\s*(?:을|를|은|는|=|:)?\s*[\"'“‘](.+?)[\"'”’]", re.I)


def _path(identifier):
    if not re.fullmatch(r"[0-9a-f]{32}", str(identifier or "")):
        raise ValueError("승인할 보고서 초안이 없습니다. 현재 차트로 템플릿을 먼저 만들어 주세요.")
    return PATHS.data_root / "chat_proposals" / f"report_{identifier}.json"


def _answer(message, context, *, template=None, approval=None, ok=True):
    from core.data_chat import reply
    tool = {"feature": FEATURE, "sources": ["현재 ChartBuilder 정의 · Template Report 검증·저장 경로"]}
    if context.get("definition_code"):
        tool["definition_code"] = context["definition_code"]
    if context.get("chart_result"):
        tool["chart_result"] = context["chart_result"]
    if template is not None:
        tool["report_template"] = template
        tool["template_code"] = json.dumps(template, ensure_ascii=False, indent=2)
    if approval:
        tool["approval"] = approval
    return reply(message, context=context, tool=tool, ok=ok,
                 interpretation={"summary": message, "source": "Template Report", "product": context.get("product", "")})


def _title(text):
    match = TITLE.search(text)
    if match:
        return match[1].strip()
    # Also accept an unquoted final title, without guessing other prose.
    match = re.search(r"(?:제목|이름)\s*(?:을|를|은|는|=|:)?\s+(.+?)\s*(?:으로|로)\s*(?:바꿔|변경해|해줘|설정해|만들어).*$", text)
    return match[1].strip() if match else ""


def _read_owned(path, user):
    proposal = load_json(path, {})
    if not proposal or proposal.get("user") != user.get("username"):
        raise ValueError("본인이 요청한 보고서 초안만 승인하거나 수정할 수 있습니다.")
    return proposal


def _pending(proposal):
    if proposal.get("status") != "pending" or time.time() - proposal.get("created", 0) > TTL_SECONDS:
        raise ValueError("취소되었거나 만료된 보고서 초안입니다. 현재 차트로 다시 만들어 주세요.")


def _selected_chart_snapshots(context):
    """Validate the browser's explicit chart selection and freeze canonical DSL."""
    from core.chart_builder_definition import parse_chart_builder_definition

    raw = context.get("selected_report_charts")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("보고서에 넣을 차트 목록이 올바르지 않습니다. 차트를 다시 선택해 주세요.")
    if len(raw) > MAX_SELECTED_CHARTS:
        raise ValueError(f"보고서에는 차트를 최대 {MAX_SELECTED_CHARTS}개까지 선택할 수 있습니다.")
    snapshots = []
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise ValueError(f"{index}번 차트 정보가 올바르지 않습니다. 차트를 다시 선택해 주세요.")
        definition = str(item.get("definition_code") or "")
        if not definition.strip():
            raise ValueError(f"{index}번 차트에는 재실행 가능한 ChartBuilder 정의가 없습니다.")
        parsed = parse_chart_builder_definition(definition)
        chart = parsed.get("chart") or {}
        snapshots.append({
            "id": str(item.get("id") or "")[:120],
            "name": str(item.get("name") or chart.get("title") or f"Chart {index}")[:120],
            "definition_code": parsed["canonical_code"],
        })
    return snapshots


def _auto_chart_layout(count, index):
    """Match Template Report's default layouts for one through six charts."""
    if count <= 1:
        return {"x": 4, "y": 10, "chart_width": 1760, "chart_height": 850}
    if count == 2:
        return {"x": 52 if index else 4, "y": 12, "chart_width": 870, "chart_height": 810}
    if count == 3:
        if index == 0:
            return {"x": 4, "y": 10, "chart_width": 1760, "chart_height": 360}
        return {"x": 4 if index == 1 else 52, "y": 48, "chart_width": 870, "chart_height": 430}
    if count == 4:
        return {"x": 52 if index % 2 else 4, "y": 12 if index < 2 else 53,
                "chart_width": 870, "chart_height": 390}
    if count == 5:
        if index == 0:
            return {"x": 4, "y": 9, "chart_width": 1760, "chart_height": 300}
        cell = index - 1
        return {"x": 52 if cell % 2 else 4, "y": 39 if cell < 2 else 68,
                "chart_width": 870, "chart_height": 280}
    return {"x": (4, 35.5, 67)[index % 3], "y": 12 if index < 3 else 53,
            "chart_width": 560, "chart_height": 390}


def _unchanged_chart(proposal, context):
    selected = proposal.get("selected_charts")
    if selected is not None:
        try:
            current = _selected_chart_snapshots(context)
        except (ValueError, TypeError, KeyError):
            current = []
        if current != selected:
            raise ValueError("보고서 초안 이후 선택한 차트가 변경됐습니다. 현재 차트로 보고서 템플릿 초안을 다시 만든 뒤 승인해 주세요.")
        return
    definition = proposal["template"]["pages"][0]["slots"][0]["definition_code"]
    if str(context.get("definition_code") or "") != definition:
        raise ValueError("보고서 초안 이후 차트가 변경됐습니다. 현재 차트로 보고서 템플릿 초안을 다시 만든 뒤 승인해 주세요.")


def _preview(text, context, user, *, rename=False):
    from routers import template_report
    title = _title(text)
    if rename:
        if not title:
            raise ValueError('예: 보고서 제목을 "주간 ET 추이"로 바꿔줘. 초안을 수정한 뒤 다시 승인할 수 있습니다.')
        with file_transaction(_path(context.get("pending_report_id"))):
            old = _read_owned(_path(context.get("pending_report_id")), user)
            _pending(old)
            _unchanged_chart(old, context)
            draft = deepcopy(old["template"])
        draft.update(id="", name=title)
        draft["pages"][0]["title"] = title
    else:
        selected = _selected_chart_snapshots(context)
        if not selected and re.search(r"선택한|여러|차트들|모든\s*차트|모두|전부", text):
            raise ValueError("리포트 차트 선택에서 담을 차트를 먼저 선택해 주세요. 선택한 순서대로 리포트를 만듭니다.")
        if selected:
            title = title or (selected[0]["name"] if len(selected) == 1 else "선택한 차트 보고서")
            pages = []
            for offset in range(0, len(selected), MAX_CHARTS_PER_PAGE):
                page_charts = selected[offset:offset + MAX_CHARTS_PER_PAGE]
                slots = []
                for index, chart in enumerate(page_charts):
                    slots.append({
                        "position": index + 1,
                        "kind": "chart",
                        "chart_id": chart["id"],
                        "chart_name": chart["name"],
                        "definition_code": chart["definition_code"],
                        **_auto_chart_layout(len(page_charts), index),
                    })
                page_number = len(pages) + 1
                pages.append({"id": f"page_{page_number}",
                              "title": title if page_number == 1 else f"{title} {page_number}",
                              "slots": slots})
            draft = {"name": title, "pages": pages}
        else:
            definition = str(context.get("definition_code") or "")
            if not definition.strip():
                raise ValueError("현재 차트에는 재실행 가능한 ChartBuilder 정의가 없습니다. ChartBuilder에서 데이터 원천·SQL이 포함된 차트 생성식을 만든 뒤 이 화면에서 ‘이 차트로 보고서 템플릿 만들어줘’를 요청해 주세요.")
            from core.chart_builder_definition import parse_chart_builder_definition
            settings = parse_chart_builder_definition(definition).get("chart") or {}
            title = title or str(settings.get("title") or "현재 차트 보고서")
            draft = {"name": title, "pages": [{"id": "page_1", "title": title,
                     "slots": [{"position": 1, "kind": "chart", "chart_name": str(settings.get("title") or "현재 차트"),
                                "definition_code": definition}]}]}
    normalized = template_report._normalize_code_template(template_report.TemplateSaveReq(**draft), user)
    identifier = uuid.uuid4().hex
    proposal = {"id": identifier, "user": user["username"], "created": time.time(), "status": "pending", "template": normalized}
    if rename and "selected_charts" in old:
        proposal["selected_charts"] = deepcopy(old["selected_charts"])
    elif not rename and selected:
        proposal["selected_charts"] = selected
    with file_transaction(_path(identifier)):
        save_json(_path(identifier), proposal)
    old_id = context.get("pending_report_id")
    if old_id and old_id != identifier:
        with file_transaction(_path(old_id)):
            old = load_json(_path(old_id), {})
            if old.get("user") == user["username"] and old.get("status") == "pending":
                old["status"] = "superseded"
                save_json(_path(old_id), old)
    context.pop("pending_split_id", None)
    context.update(pending_report_id=identifier, last_action=FEATURE)
    chart_count = sum(len(page["slots"]) for page in normalized["pages"])
    return _answer(f'“{normalized["name"]}” 보고서 템플릿 초안입니다. 차트 {chart_count}개를 {len(normalized["pages"])}페이지에 데이터 조건과 함께 담았으며 아직 저장하지 않았습니다. 제목을 수정하거나 아래 승인 버튼 또는 ‘승인 {identifier}’로 저장하세요.',
                   context, template=normalized,
                   approval={"id": identifier, "kind": "report", "status": "pending", "expires_in": TTL_SECONDS})


def _decide(identifier, cancel, context, user):
    from routers import template_report
    path = _path(identifier)
    with file_transaction(path):
        proposal = _read_owned(path, user)
        if proposal.get("status") == "applied":
            saved = proposal["saved_template"]
            message = "이미 저장된 보고서 템플릿입니다. 중복 저장하지 않았습니다."
        else:
            # Recover a committed operation even when its proposal TTL elapsed
            # after a crash. Expiry only forbids a new write, not its receipt.
            with file_transaction(template_report.STORE_FILE):
                committed = next((row for row in template_report._load_templates()
                                  if row.get("chat_operation_id") == identifier), None)
            if committed:
                if committed.get("created_by") != user.get("username"):
                    raise ValueError("본인이 승인한 보고서 템플릿만 확인할 수 있습니다.")
                saved = template_report._public_template(committed)
                message = "이미 저장된 보고서 템플릿입니다. 중복 저장하지 않았습니다."
            else:
                _pending(proposal)
                if cancel:
                    proposal["status"] = "cancelled"
                    save_json(path, proposal)
                    if context.get("pending_report_id") == identifier:
                        context.pop("pending_report_id", None)
                    return _answer("보고서 초안을 취소했습니다. 템플릿은 저장하지 않았습니다.", context)
                _unchanged_chart(proposal, context)
                # The operation marker and template share one canonical write.
                result = template_report._save_template(template_report.TemplateSaveReq(**proposal["template"]),
                                                        user, operation_id=identifier)
                saved = result["template"]
                message = f'“{saved["name"]}” 보고서 템플릿을 저장했습니다. Template Report에서 열고 실행할 수 있습니다.'
            proposal.update(status="applied", saved_template=saved)
            save_json(path, proposal)
    if context.get("pending_report_id") == identifier:
        context.pop("pending_report_id", None)
    context.update(report_template_id=saved["id"], last_action=FEATURE)
    return _answer(message, context, template=saved,
                   approval={"id": identifier, "kind": "report", "status": "applied"})


def handle(text, context, request):
    """Return None when unrelated. Every proposal/write requires admin rights."""
    text = str(text or "").strip()
    tagged = TAGGED_DECISION.fullmatch(text)
    decision = bool(APPROVE.fullmatch(text) or CANCEL.fullmatch(text))
    current = context.get("last_action") == FEATURE
    if tagged:
        identifier = tagged[2].lower()
        if identifier != context.get("pending_report_id") and not _path(identifier).exists():
            return None  # The same explicit decision grammar is used by SplitTable.
    elif decision:
        if not current:
            return None
        identifier = context.get("pending_report_id")
        if context.get("pending_split_id") and identifier:
            return _answer("보고서와 SplitTable 초안이 함께 있습니다. 승인 버튼이나 ‘승인 초안ID’로 대상을 지정해 주세요.", context, ok=False)
    else:
        rename = bool(current and context.get("pending_report_id") and re.search(r"제목|이름|title|name", text, re.I))
        intent_text = re.sub(r'''["'“‘].*?["'”’]''', "", text)
        if not rename and not (REPORT.search(intent_text) and CREATE.search(intent_text)):
            return None
    user = require_admin(request)
    try:
        if tagged and identifier != context.get("pending_report_id"):
            old = _read_owned(_path(identifier), user)
            if not (old.get("status") == "applied" and context.get("report_template_id")
                    and context["report_template_id"] == old.get("saved_template", {}).get("id")):
                raise ValueError("현재 보고서 초안과 승인 ID가 다릅니다. 현재 차트로 초안을 다시 만들고 승인해 주세요.")
        if tagged or decision:
            return _decide(identifier, tagged[1] == "취소" if tagged else bool(CANCEL.fullmatch(text)), context, user)
        return _preview(text, context, user, rename=rename)
    except (ValueError, HTTPException) as exc:
        return _answer(str(exc.detail) if isinstance(exc, HTTPException) else str(exc), context, ok=False)
