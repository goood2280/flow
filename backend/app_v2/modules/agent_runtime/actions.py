from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, Sequence

from core.flowi_units import UNIT_AIS

from .schemas import UnitAgentPlan, UnitAgentResult


ActionPolicy = Literal["read_only", "write_requires_approval", "blocked"]


@dataclass(frozen=True)
class UnitActionSpec:
    unit_ai: str
    action: str
    title: str
    policy: ActionPolicy = "read_only"
    endpoint: str = "core.flowi_units.dispatcher.try_dispatch"
    required_slots: tuple[str, ...] = ()
    description: str = ""

    @property
    def key(self) -> str:
        return f"{self.unit_ai}.{self.action}"


_RAW_WRITE_TARGETS = (
    "raw db",
    "raw data",
    "database",
    "db root",
    "source file",
    "원본",
    "원 data",
    "원데이터",
    "db",
    "데이터베이스",
)

_WRITE_HINTS = (
    "수정",
    "변경",
    "바꿔",
    "바꾸",
    "삭제",
    "지워",
    "저장",
    "생성",
    "등록",
    "추가",
    "업데이트",
    "편집",
    "delete",
    "remove",
    "update",
    "insert",
    "write",
    "save",
    "create",
    "edit",
    "modify",
)


_INTERNAL_RUNTIME_ACTIONS = {"resolve_semantic", "plan", "review_guardrail", "conclude"}


UNIT_ACTIONS: dict[str, UnitActionSpec] = {
    "filebrowser.current_step": UnitActionSpec(
        unit_ai="filebrowser",
        action="current_step",
        title="FileBrowser 현재 step 조회",
        required_slots=("product", "root_lot_ids"),
        description="LOT progress cache/FAB snapshot에서 현재 step을 read-only 조회",
    ),
    "filebrowser.query": UnitActionSpec(
        unit_ai="filebrowser",
        action="query",
        title="FileBrowser 데이터/스키마 조회",
        required_slots=(),
        description="FileBrowser 소유 read-only preview/query handler 사용",
    ),
    "tracker.lookup": UnitActionSpec(
        unit_ai="tracker",
        action="lookup",
        title="Tracker 이슈/LOT 조회",
        required_slots=(),
        description="Tracker runtime cache에서 이슈/목적을 read-only 조회",
    ),
    "tracker.create_issue": UnitActionSpec(
        unit_ai="tracker",
        action="create_issue",
        title="Tracker 이슈 등록 제안",
        policy="write_requires_approval",
        endpoint="/api/tracker/*",
        required_slots=("root_lot_ids",),
        description="저장성 Tracker 작업은 자동 실행하지 않고 승인 제안만 생성",
    ),
    "inform.summary": UnitActionSpec(
        unit_ai="inform",
        action="summary",
        title="Inform 로그 요약",
        required_slots=(),
        description="Inform runtime 로그를 read-only 조회/요약",
    ),
    "inform.create_draft": UnitActionSpec(
        unit_ai="inform",
        action="create_draft",
        title="Inform 작성 초안",
        policy="write_requires_approval",
        endpoint="/api/informs/*",
        required_slots=("root_lot_ids",),
        description="Inform 등록/수정 계열은 확인 전 자동 저장하지 않음",
    ),
    "meeting.recall": UnitActionSpec(
        unit_ai="meeting",
        action="recall",
        title="Meeting 회의록 회수",
        description="회의/아젠다/결정사항을 read-only 조회",
    ),
    "meeting.create_minutes": UnitActionSpec(
        unit_ai="meeting",
        action="create_minutes",
        title="Meeting 회의록 작성 제안",
        policy="write_requires_approval",
        endpoint="/api/meetings/*",
        description="회의록 저장은 사용자 확인 후 deterministic API에서 수행",
    ),
    "dashboard.chart": UnitActionSpec(
        unit_ai="dashboard",
        action="chart",
        title="Dashboard 차트 조회/초안",
        description="차트 payload와 source evidence를 read-only 생성",
    ),
    "dashboard.save_card": UnitActionSpec(
        unit_ai="dashboard",
        action="save_card",
        title="Dashboard 카드 저장 제안",
        policy="write_requires_approval",
        endpoint="/api/dashboard/*",
        description="대시보드 카드 저장은 자동 실행하지 않음",
    ),
    "splittable.knob_impact": UnitActionSpec(
        unit_ai="splittable",
        action="knob_impact",
        title="SplitTable KNOB 영향 조회",
        required_slots=("product", "root_lot_ids"),
        description="ML_TABLE/SplitTable 기준 KNOB 구성을 read-only 조회",
    ),
    "splittable.view": UnitActionSpec(
        unit_ai="splittable",
        action="view",
        title="SplitTable view 조회",
        required_slots=("root_lot_ids",),
        description="SplitTable plan/actual matrix를 read-only 조회",
    ),
    "splittable.plan_update": UnitActionSpec(
        unit_ai="splittable",
        action="plan_update",
        title="SplitTable plan 변경 제안",
        policy="write_requires_approval",
        endpoint="/api/splittable/*",
        required_slots=("root_lot_ids",),
        description="Plan/MGMT/TAG 변경은 확인 전 자동 저장하지 않음",
    ),
    "agent_runtime.inspect": UnitActionSpec(
        unit_ai="agent_runtime",
        action="inspect",
        title="Agent runtime trace 점검",
        endpoint="/api/agent/runtime/run",
        description="semantic/plan/guardrail trace 자체를 read-only 점검",
    ),
    "raw_data.write": UnitActionSpec(
        unit_ai="raw_data",
        action="write",
        title="Raw DB/File 직접 수정 차단",
        policy="blocked",
        endpoint="blocked",
        description="원본 DB/file 직접 write는 Flow-i/Agent runtime에서 허용하지 않음",
    ),
}


def list_action_specs() -> list[UnitActionSpec]:
    return sorted(UNIT_ACTIONS.values(), key=lambda spec: (spec.unit_ai, spec.action))


def action_policy_for(unit_ai: str, action: str) -> ActionPolicy:
    spec = _spec_for(unit_ai, action)
    if spec:
        return spec.policy
    lowered = str(action or "").lower()
    if any(hint in lowered for hint in ("delete", "remove", "update", "save", "create", "write", "register", "send_mail")):
        return "write_requires_approval"
    return "read_only"


def build_action_blueprint() -> dict[str, Any]:
    return {
        "actions": [spec_to_dict(spec) for spec in list_action_specs()],
        "policies": {
            "read_only": "기존 Flow deterministic handler 또는 unit AI dispatcher로 즉시 실행 가능",
            "write_requires_approval": "자동 실행하지 않고 approval proposal/확인 필요 상태만 반환",
            "blocked": "Raw DB/file 직접 write처럼 runtime에서 절대 실행하지 않는 작업",
        },
        "workflow_enabled": True,
    }


def spec_to_dict(spec: UnitActionSpec) -> dict[str, Any]:
    return {
        "key": spec.key,
        "unit_ai": spec.unit_ai,
        "action": spec.action,
        "title": spec.title,
        "policy": spec.policy,
        "approval_required": spec.policy == "write_requires_approval",
        "endpoint": spec.endpoint,
        "required_slots": list(spec.required_slots),
        "description": spec.description,
    }


def compact_plan_rows(plans: list[UnitAgentPlan] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for plan in plans or []:
        row = plan.model_dump(mode="json") if hasattr(plan, "model_dump") else dict(plan or {})
        if not row.get("unit_ai") and not row.get("action"):
            continue
        if row.get("unit_ai") == "agent_runtime" and row.get("action") in _INTERNAL_RUNTIME_ACTIONS:
            continue
        rows.append({
            "agent_id": row.get("agent_id") or "",
            "unit_ai": row.get("unit_ai") or "",
            "action": row.get("action") or "",
            "policy": row.get("policy") or "read_only",
            "approval_required": bool(row.get("approval_required")),
            "endpoint": row.get("endpoint") or "",
            "missing_slots": row.get("missing_slots") or [],
            "evidence_refs": row.get("evidence_refs") or [],
        })
    return rows


def guardrail_summary_from_plans(plans: list[UnitAgentPlan] | list[dict[str, Any]]) -> dict[str, Any]:
    rows = compact_plan_rows(plans)
    approvals = [r for r in rows if r.get("approval_required")]
    blocked = [r for r in rows if r.get("policy") == "blocked"]
    missing: list[str] = []
    for row in rows:
        for slot in row.get("missing_slots") or []:
            if slot not in missing:
                missing.append(slot)
    return {
        "status": "blocked" if blocked else ("approval_required" if approvals else ("missing_slots" if missing else "allowed")),
        "read_only_actions": len([r for r in rows if r.get("policy") == "read_only"]),
        "approval_required": len(approvals),
        "blocked": len(blocked),
        "missing_slots": missing,
        "policies": sorted({str(r.get("policy") or "read_only") for r in rows}),
    }


def build_action_plans(
    *,
    goal: str,
    semantic: dict[str, Any],
    username: str = "",
    unit_ai_scope: str | Sequence[str] | None = None,
) -> tuple[list[UnitAgentPlan], dict[str, Any]]:
    slots = _normalized_slots(semantic.get("slots") if isinstance(semantic.get("slots"), dict) else {})
    intent = str(semantic.get("intent") or "general_orchestration")
    workflow = _match_workflow(goal, intent=intent, username=username)
    evidence_refs = _evidence_refs(semantic)
    specs: list[UnitActionSpec] = []
    workflow_key = ""
    scope = _normalize_scope(unit_ai_scope)
    if _is_raw_write_request(goal):
        specs = [UNIT_ACTIONS["raw_data.write"]]
    elif workflow:
        workflow_key = str(workflow.get("key") or "")
        for step in workflow.get("steps") or []:
            if not isinstance(step, dict):
                continue
            spec = _spec_for(str(step.get("unit_ai") or ""), str(step.get("action") or ""))
            if spec is None:
                spec = _ad_hoc_spec(str(step.get("unit_ai") or ""), str(step.get("action") or ""))
            specs.append(spec)
    else:
        specs = _infer_specs(goal, intent, semantic)
    specs = _dedupe_specs(specs)
    scoped_out_specs: list[UnitActionSpec] = []
    if scope:
        scoped_specs = [spec for spec in specs if spec.unit_ai in scope]
        scoped_out_specs = [spec for spec in specs if spec.unit_ai not in scope]
        specs = scoped_specs or [_scoped_inspect_spec(scope[0])]
    plans: list[UnitAgentPlan] = []
    for idx, spec in enumerate(specs, start=1):
        missing = _missing_slots(spec, slots)
        inputs = {
            "intent": intent,
            "slots": slots,
            "workflow": workflow_key,
            "unit_ai_scope": scope,
        }
        plans.append(UnitAgentPlan(
            agent_id=spec.key,
            title=spec.title,
            inputs=inputs,
            outputs=["tool", "guardrail", "table", "chart_result", "warnings"],
            depends_on=["task_planner"] if idx == 1 else [specs[idx - 2].key],
            unit_ai=spec.unit_ai,
            action=spec.action,
            policy=spec.policy,
            approval_required=spec.policy == "write_requires_approval",
            endpoint=spec.endpoint,
            missing_slots=missing,
            evidence_refs=evidence_refs,
        ))
    meta = {
        "workflow": workflow,
        "guardrail": guardrail_summary_from_plans(plans),
        "actions": compact_plan_rows(plans),
        "unit_ai_scope": scope,
        "scoped_out_actions": [spec_to_dict(spec) for spec in scoped_out_specs],
    }
    return plans, meta


def execute_action_plan(
    plan: UnitAgentPlan | dict[str, Any],
    *,
    goal: str,
    semantic: dict[str, Any],
    username: str = "",
    context: dict[str, Any] | None = None,
) -> UnitAgentResult:
    row = plan.model_dump(mode="json") if hasattr(plan, "model_dump") else dict(plan or {})
    unit_ai = str(row.get("unit_ai") or "")
    action = str(row.get("action") or "")
    policy = str(row.get("policy") or action_policy_for(unit_ai, action))
    missing = [str(x) for x in (row.get("missing_slots") or []) if str(x).strip()]
    guardrail = {
        "policy": policy,
        "approval_required": bool(row.get("approval_required") or policy == "write_requires_approval"),
        "missing_slots": missing,
        "endpoint": row.get("endpoint") or "",
        "status": "allowed",
    }
    if policy == "blocked":
        guardrail["status"] = "blocked"
        return UnitAgentResult(
            agent_id=str(row.get("agent_id") or f"{unit_ai}.{action}"),
            status="skipped",
            summary="Raw DB/file 직접 write 요청은 차단했습니다.",
            artifacts=[{"type": "guardrail", **guardrail}],
            handled=False,
            guardrail=guardrail,
            warnings=["blocked_by_policy"],
        )
    if policy == "write_requires_approval":
        guardrail["status"] = "approval_required"
        return UnitAgentResult(
            agent_id=str(row.get("agent_id") or f"{unit_ai}.{action}"),
            status="skipped",
            summary="저장성 작업은 자동 실행하지 않고 승인 제안으로 멈췄습니다.",
            artifacts=[{
                "type": "approval_proposal",
                "unit_ai": unit_ai,
                "action": action,
                "slots": _normalized_slots(semantic.get("slots") if isinstance(semantic.get("slots"), dict) else {}),
                "confirm_required": True,
            }],
            handled=False,
            guardrail=guardrail,
            warnings=["approval_required"],
        )
    if missing:
        guardrail["status"] = "missing_slots"
        return UnitAgentResult(
            agent_id=str(row.get("agent_id") or f"{unit_ai}.{action}"),
            status="skipped",
            summary="필수 slot이 부족해 read-only 실행을 건너뛰었습니다.",
            artifacts=[{"type": "missing_slots", "missing_slots": missing}],
            handled=False,
            guardrail=guardrail,
            warnings=["missing_slots"],
        )
    if unit_ai == "agent_runtime":
        guardrail["status"] = "handled"
        return UnitAgentResult(
            agent_id=str(row.get("agent_id") or f"{unit_ai}.{action}"),
            summary="semantic/plan/guardrail trace를 read-only로 점검했습니다.",
            artifacts=[{"type": "runtime_trace", "semantic_intent": semantic.get("intent") or ""}],
            handled=True,
            guardrail=guardrail,
        )
    if unit_ai not in UNIT_AIS:
        guardrail["status"] = "no_handler"
        return UnitAgentResult(
            agent_id=str(row.get("agent_id") or f"{unit_ai}.{action}"),
            status="skipped",
            summary=f"{unit_ai} unit AI handler가 registry에 없습니다.",
            handled=False,
            guardrail=guardrail,
            warnings=["no_handler"],
        )
    try:
        from core.flowi_units.dispatcher import try_dispatch

        slots = _normalized_slots(semantic.get("slots") if isinstance(semantic.get("slots"), dict) else {})
        product = _first_value(slots.get("product"), slots.get("products"))
        tool = try_dispatch(
            goal,
            product=str(product or ""),
            max_rows=12,
            agent_context={"runtime": "agent_runtime", "action": action, "slots": slots, **(context or {})},
            me={"username": username or "agent_runtime", "role": "admin"},
            only=(unit_ai,),
        )
    except Exception as exc:
        guardrail["status"] = "error"
        return UnitAgentResult(
            agent_id=str(row.get("agent_id") or f"{unit_ai}.{action}"),
            status="failed",
            summary="read-only unit AI 실행 중 오류가 발생했습니다.",
            handled=False,
            guardrail=guardrail,
            warnings=[str(exc)[:240]],
        )
    if not isinstance(tool, dict):
        guardrail["status"] = "no_handler"
        return UnitAgentResult(
            agent_id=str(row.get("agent_id") or f"{unit_ai}.{action}"),
            status="skipped",
            summary="read-only dispatcher가 처리 가능한 handler를 찾지 못했습니다.",
            handled=False,
            guardrail=guardrail,
            warnings=["no_handler"],
        )
    guardrail["status"] = "handled" if tool.get("handled") else "no_handler"
    return UnitAgentResult(
        agent_id=str(row.get("agent_id") or f"{unit_ai}.{action}"),
        status="completed" if tool.get("handled") else "skipped",
        summary=str(tool.get("answer") or tool.get("summary") or "read-only unit AI 실행 결과를 받았습니다.")[:500],
        artifacts=[{"type": "tool_keys", "keys": sorted(tool.keys())[:32]}],
        metrics={"row_count": _tool_row_count(tool)},
        handled=bool(tool.get("handled")),
        guardrail=guardrail,
        tool=tool,
        table=tool.get("table") if isinstance(tool.get("table"), dict) else {},
        chart_result=(tool.get("chart_result") if isinstance(tool.get("chart_result"), dict) else (tool.get("chart") if isinstance(tool.get("chart"), dict) else {})),
        warnings=[str(w) for w in (tool.get("warnings") or []) if str(w).strip()][:12],
    )


def _spec_for(unit_ai: str, action: str) -> UnitActionSpec | None:
    unit_ai = str(unit_ai or "").strip()
    action = str(action or "").strip()
    if not unit_ai:
        return None
    if f"{unit_ai}.{action}" in UNIT_ACTIONS:
        return UNIT_ACTIONS[f"{unit_ai}.{action}"]
    action_l = action.lower()
    for spec in UNIT_ACTIONS.values():
        if spec.unit_ai == unit_ai and (spec.action == action_l or spec.action in action_l or action_l in spec.action):
            return spec
    return None


def _ad_hoc_spec(unit_ai: str, action: str) -> UnitActionSpec:
    policy = action_policy_for(unit_ai, action)
    return UnitActionSpec(
        unit_ai=unit_ai or "flowi",
        action=action or "run",
        title=f"{unit_ai or 'Flow-i'} {action or 'run'}",
        policy=policy,
        endpoint="core.flowi_units.dispatcher.try_dispatch" if policy == "read_only" else "approval_required",
    )


def _normalize_scope(unit_ai_scope: str | Sequence[str] | None) -> list[str]:
    if unit_ai_scope in (None, "", [], ()):
        return []
    if isinstance(unit_ai_scope, str):
        raw = [unit_ai_scope]
    else:
        raw = list(unit_ai_scope)
    out: list[str] = []
    for item in raw:
        key = str(item or "").strip()
        if key and key not in out:
            out.append(key)
    return out


def _scoped_inspect_spec(unit_ai: str) -> UnitActionSpec:
    unit = UNIT_AIS.get(unit_ai)
    title = f"{unit.title()} 라우팅 진단" if unit is not None else f"{unit_ai} 라우팅 진단"
    return UnitActionSpec(
        unit_ai=unit_ai or "agent_runtime",
        action="inspect",
        title=title,
        policy="read_only",
        endpoint="core.flowi_units.dispatcher.try_dispatch",
        description="선택한 unit AI에 고정해 dispatcher 처리 가능 여부를 read-only로 점검",
    )


def _infer_specs(goal: str, intent: str, semantic: dict[str, Any]) -> list[UnitActionSpec]:
    terms = set(str(v) for v in (semantic.get("normalized_terms") or {}).values())
    text = str(goal or "")
    lowered = text.lower()
    specs: list[UnitActionSpec] = []
    if "step_id" in terms or "현재 step" in text or "current step" in lowered:
        specs.append(UNIT_ACTIONS["filebrowser.current_step"])
    if intent == "filebrowser_ai_sql" or "filebrowser" in terms or "ai_sql" in terms:
        specs.append(UNIT_ACTIONS["filebrowser.query"])
    if intent == "tracker_lookup" or "tracker" in terms:
        specs.append(UNIT_ACTIONS["tracker.lookup"])
    if intent == "inform_draft" or "inform" in terms:
        if _has_write_hint(goal):
            specs.append(UNIT_ACTIONS["inform.create_draft"])
        else:
            specs.append(UNIT_ACTIONS["inform.summary"])
    if intent == "meeting_recall" or "meeting" in terms:
        specs.append(UNIT_ACTIONS["meeting.recall"])
    if intent == "chart_analysis" or "chart" in terms:
        specs.append(UNIT_ACTIONS["dashboard.chart"])
    if intent == "knob_analysis" or "knob" in terms:
        specs.append(UNIT_ACTIONS["splittable.knob_impact"])
    if "splittable" in lowered or "split table" in lowered or "스플릿테이블" in text:
        specs.append(UNIT_ACTIONS["splittable.view"])
    if intent in {"semantic_inspection", "traceable_orchestration"}:
        specs.append(UNIT_ACTIONS["agent_runtime.inspect"])
    if not specs:
        specs.append(UNIT_ACTIONS["agent_runtime.inspect"])
    return specs


def _match_workflow(goal: str, *, intent: str, username: str) -> dict[str, Any] | None:
    try:
        from core import flowi_workflow_templates as wf_templates

        return wf_templates.match_prompt(goal, intent=intent, username=username)
    except Exception:
        return None


def _dedupe_specs(specs: list[UnitActionSpec]) -> list[UnitActionSpec]:
    out: list[UnitActionSpec] = []
    seen: set[str] = set()
    for spec in specs:
        if spec.key in seen:
            continue
        seen.add(spec.key)
        out.append(spec)
    return out


def _missing_slots(spec: UnitActionSpec, slots: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for key in spec.required_slots:
        if _slot_present(slots, key):
            continue
        missing.append(key)
    return missing


def _slot_present(slots: dict[str, Any], key: str) -> bool:
    value = slots.get(key)
    if value not in (None, "", [], {}):
        return True
    aliases = {
        "product": ("products",),
        "root_lot_ids": ("root_lot_id", "lot", "lot_id", "lot_ids", "fab_lot_ids"),
        "wafer_ids": ("wafer_id",),
    }.get(key, ())
    return any(slots.get(alias) not in (None, "", [], {}) for alias in aliases)


def _normalized_slots(slots: dict[str, Any]) -> dict[str, Any]:
    out = dict(slots or {})
    if "product" not in out:
        product = _first_value(out.get("products"))
        if product:
            out["product"] = product
    if "root_lot_ids" not in out and out.get("lot"):
        out["root_lot_ids"] = [out.get("lot")]
    return out


def _first_value(*values: Any) -> Any:
    for value in values:
        if isinstance(value, list) and value:
            return value[0]
        if value not in (None, "", [], {}):
            return value
    return ""


def _evidence_refs(semantic: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for cand in semantic.get("candidates") or []:
        if not isinstance(cand, dict):
            continue
        ref = {
            "token": cand.get("token") or "",
            "relation_id": cand.get("relation_id") or "",
            "column": cand.get("column") or "",
            "source": cand.get("source") or "",
        }
        if any(ref.values()) and ref not in refs:
            refs.append(ref)
        if len(refs) >= 8:
            break
    return refs


def _is_raw_write_request(goal: str) -> bool:
    lowered = str(goal or "").lower()
    return _has_write_hint(goal) and any(target in lowered for target in _RAW_WRITE_TARGETS)


def _has_write_hint(goal: str) -> bool:
    lowered = str(goal or "").lower()
    return any(hint in lowered for hint in _WRITE_HINTS)


def _tool_row_count(tool: dict[str, Any]) -> int:
    table = tool.get("table") if isinstance(tool.get("table"), dict) else {}
    if table:
        try:
            return int(table.get("total") or len(table.get("rows") or []))
        except Exception:
            return 0
    rows = tool.get("rows")
    return len(rows) if isinstance(rows, list) else 0
