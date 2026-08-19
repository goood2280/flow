class ErrorExplainReq(BaseModel):
    status: int | None = None
    method: str = ""
    url: str = ""
    page: str = ""
    raw_error: str = ""
    body: Any = None
    context: str = ""


def _clip_error_text(value: Any, limit: int = 4000) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            text = str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    text = re.sub(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;\"']+", r"\1<redacted>", text)
    text = re.sub(r"(?i)(x-session-token\s*[:=]\s*)[^\s,;\"']+", r"\1<redacted>", text)
    text = re.sub(
        r"(?i)(admin_token|access_token|refresh_token|api_key|password|passwd|token)([\"'\s:=]+)([^\"'\s,;&]+)",
        r"\1\2<redacted>",
        text,
    )
    if len(text) > limit:
        return text[:limit].rstrip() + "\n...<truncated>"
    return text


def _first_error_line(text: str) -> str:
    for line in (text or "").splitlines():
        clean = line.strip()
        if clean:
            return clean[:220]
    return ""


def _fallback_error_explanation(req: ErrorExplainReq, raw_error: str) -> dict[str, Any]:
    method = (req.method or "").strip().upper()
    url = (req.url or "").strip()
    page = (req.page or "").strip()
    status = req.status if isinstance(req.status, int) else None
    where_parts = []
    if page:
        where_parts.append(f"화면 {page}")
    api = " ".join(part for part in [method, url] if part)
    if api:
        where_parts.append(f"API {api}")
    if status:
        where_parts.append(f"HTTP {status}")
    return {
        "summary": _first_error_line(raw_error) or (f"HTTP {status} 오류" if status else "앱 요청 처리 오류"),
        "where": " / ".join(where_parts) or "오류가 발생한 화면 또는 API를 확인해야 합니다.",
        "cause": "서버가 요청을 정상 처리하지 못했습니다. 자세한 원인은 원문 에러를 기준으로 확인해야 합니다.",
        "how_to_fix": [
            "같은 동작을 다시 시도해 재현되는지 확인하세요.",
            "입력값, 선택한 대상, 권한 또는 세션 상태가 맞는지 확인하세요.",
            "반복되면 발생 위치와 원문 에러를 관리자에게 전달하세요.",
        ],
        "raw_error": raw_error,
    }


def _clean_error_explanation(obj: dict[str, Any], fallback: dict[str, Any], raw_error: str) -> dict[str, Any]:
    def line(key: str, limit: int = 320) -> str:
        value = obj.get(key)
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        text = " ".join(part.strip() for part in text.splitlines() if part.strip())
        return text[:limit] or fallback.get(key, "")

    fixes = obj.get("how_to_fix")
    if isinstance(fixes, str):
        fixes = [part.strip(" -•\t") for part in fixes.splitlines() if part.strip(" -•\t")]
    if not isinstance(fixes, list):
        fixes = []
    clean_fixes = []
    for item in fixes:
        text = str(item or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        text = " ".join(part.strip() for part in text.splitlines() if part.strip())
        if text:
            clean_fixes.append(text[:220])
        if len(clean_fixes) >= 4:
            break
    if not clean_fixes:
        clean_fixes = list(fallback.get("how_to_fix") or [])[:4]
    return {
        "summary": line("summary", 220),
        "where": line("where", 320),
        "cause": line("cause", 360),
        "how_to_fix": clean_fixes,
        "raw_error": raw_error,
    }


def _format_error_explanation_message(exp: dict[str, Any]) -> str:
    fixes = [str(item).strip() for item in (exp.get("how_to_fix") or []) if str(item).strip()]
    parts = [
        "AI 오류 해석",
        f"문제: {exp.get('summary') or '앱 요청 처리 오류'}",
        f"발생 위치: {exp.get('where') or '확인 필요'}",
        f"가능한 원인: {exp.get('cause') or '확인 필요'}",
    ]
    if fixes:
        parts.append("해결 방법:\n" + "\n".join(f"- {item}" for item in fixes))
    raw_error = str(exp.get("raw_error") or "").strip()
    if raw_error:
        parts.append("원문 에러:\n" + raw_error)
    return "\n\n".join(parts)


def _build_error_explain_prompt(req: ErrorExplainReq, raw_error: str) -> str:
    payload = {
        "status": req.status,
        "method": (req.method or "").strip().upper(),
        "url": (req.url or "").strip(),
        "page": (req.page or "").strip(),
        "context": _clip_error_text(req.context, 1000),
        "body": _clip_error_text(req.body, 2500),
        "raw_error": raw_error,
    }
    return (
        "Flow 웹앱에서 발생한 API 오류를 한국어로 쉽게 설명해줘.\n"
        "반드시 사용자가 확인할 수 있는 발생 위치(API, 화면, HTTP status)를 적고, 해결 방법을 2~4개로 구체화해.\n"
        "근거가 없는 파일명, 코드 라인, 담당자를 추측하지 말고 확인 필요라고 말해.\n"
        "원문 에러는 응답 JSON에 다시 넣지 않아도 되지만, 해석은 아래 원문을 기준으로 해.\n\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )


@router.post("/error/explain")
def explain_error(req: ErrorExplainReq, request: Request):
    current_user(request)
    raw_error = _clip_error_text(req.raw_error or req.body or "", 4000)
    fallback = _fallback_error_explanation(req, raw_error)
    try:
        available = llm_adapter.is_available()
        if not available:
            return {
                "ok": True,
                "llm": {"available": False, "used": False},
                "explanation": fallback,
                "message": raw_error,
            }
        if not llm_adapter.should_attempt_llm():
            return {
                "ok": True,
                "llm": {"available": True, "used": False, "skipped": "circuit_breaker_open"},
                "explanation": fallback,
                "message": raw_error,
            }

        out = llm_adapter.complete_json(
            _build_error_explain_prompt(req, raw_error),
            system=(
                "You explain Flow app errors for Korean end users. "
                "Return only JSON with summary, where, cause, how_to_fix. "
                "Do not reveal secrets. Do not invent internal stack details."
            ),
            schema=ERROR_EXPLAIN_SCHEMA,
            timeout=8,
            max_retries=1,
        )
        if not out.get("ok"):
            return {
                "ok": True,
                "llm": {"available": True, "used": False, "error": str(out.get("error") or "")[:200]},
                "explanation": fallback,
                "message": raw_error,
            }
        explanation = _clean_error_explanation(out.get("obj") or {}, fallback, raw_error)
        return {
            "ok": True,
            "llm": {"available": True, "used": True},
            "explanation": explanation,
            "message": _format_error_explanation_message(explanation),
        }
    except Exception:
        logger.warning("explain_error unexpected error", exc_info=True)
        return {
            "ok": True,
            "llm": {"available": False, "used": False, "error": "internal_error"},
            "explanation": fallback,
            "message": raw_error,
        }


@router.get("/status")
def status(request: Request):
    me = current_user(request)
    is_admin = (me.get("role") or "user") == "admin"
    allowed_keys = _allowed_flowi_feature_keys(me)
    local_tools = ["unit_feature_router"] if allowed_keys else []
    if "splittable" in allowed_keys:
        local_tools.insert(0, "lot_knobs")
    if "dashboard" in allowed_keys:
        local_tools.append("dashboard_scatter_plan")
    try:
        cfg = llm_adapter.get_config(redact=True)
        llm_available = llm_adapter.is_available()
        has_token = llm_adapter.has_admin_token()
    except Exception:
        logger.warning("llm status config read failed", exc_info=True)
        cfg = {}
        llm_available = False
        has_token = False
    persona = _flowi_persona_config()
    flowi = {
        "requires_token": False,
        "allowed_features": sorted(allowed_keys),
        "entrypoints": [item for item in FLOWI_FEATURE_ENTRYPOINTS if item["key"] in allowed_keys],
        "persona": {
            "source": persona.get("source"),
            "enabled": persona.get("enabled"),
        },
        "agent_persona": FLOWI_AGENT_PERSONA,
        "naming_rules": FLOWI_NAMING_RULES,
    }
    if is_admin:
        flowi.update({
            "admin_token_configured": has_token,
            "local_tools": local_tools,
            "policy": FLOWI_READ_ONLY_POLICY,
            "workflow_guide": FLOWI_BASE_WORKFLOW_GUIDE,
            "unit_actions": {k: v for k, v in FLOWI_UNIT_ACTIONS.items() if k in allowed_keys},
            "persona": {
                "source": persona.get("source"),
                "enabled": persona.get("enabled"),
                "updated_by": persona.get("updated_by"),
                "updated_at": persona.get("updated_at"),
            },
        })
    return {
        "available": llm_available,
        "config": cfg,
        "native_capabilities": llm_adapter.native_capability_snapshot(),
        "flowi": flowi,
    }


class LLMTestReq(BaseModel):
    prompt: str
    system: str | None = None
    probe_capabilities: bool = True


class DcopSummaryReq(BaseModel):
    findings: list[dict[str, Any]] = Field(default_factory=list)


DCOP_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
    },
    "required": ["summary"],
    "additionalProperties": False,
}


def _is_gpt_oss_120b_configured() -> bool:
    try:
        model = re.sub(r"[^a-z0-9]", "", str(llm_adapter.get_config(redact=True).get("model") or "").lower())
    except Exception:
        return False
    return "gptoss120b" in model


@router.post("/dcop/summary")
def dcop_summary(req: DcopSummaryReq, request: Request):
    _ = current_user(request)
    if not llm_adapter.is_available() or not _is_gpt_oss_120b_configured() or not llm_adapter.should_attempt_llm():
        return {"ok": True, "used": False, "summary": "", "reason": "gpt_oss_120b_not_connected"}

    findings = []
    for raw in req.findings[:100]:
        if not isinstance(raw, dict):
            continue
        severity = str(raw.get("severity") or "").strip().lower()
        if severity not in {"fail", "warning"}:
            continue
        findings.append({
            "rule_number": max(1, int(raw.get("rule_number") or 1)),
            "severity": severity,
            "count": max(0, int(raw.get("count") or 0)),
            "row_numbers": [max(1, int(value)) for value in (raw.get("row_numbers") or [])[:4]],
            "rows_over_limit": bool(raw.get("rows_over_limit")),
            "message": str(raw.get("message") or "")[:500],
        })
    if not findings:
        return {"ok": True, "used": False, "summary": "", "reason": "no_findings"}

    prompt = (
        "다음 DCOP 검사 FAIL/WARNING 결과를 한국어로 간결하게 요약하세요. "
        "FAIL을 먼저 쓰고 WARNING을 뒤에 쓰며, 규칙 번호·건수·핵심 문제를 빠뜨리지 마세요. "
        "제공된 사실만 사용하고 해결되지 않은 원인을 추측하지 마세요. 3문장 이내로 작성하세요.\n"
        + json.dumps(findings, ensure_ascii=False, separators=(",", ":"))
    )
    out = llm_adapter.complete_json(
        prompt,
        system="당신은 반도체 DCOP 데이터 품질 검사 결과를 정확하고 짧게 요약합니다.",
        schema=DCOP_SUMMARY_SCHEMA,
        timeout=12,
        max_retries=1,
    )
    summary = str((out.get("obj") or {}).get("summary") or "").strip()
    if not out.get("ok") or not summary:
        return {"ok": True, "used": False, "summary": "", "reason": "llm_call_failed"}
    return {"ok": True, "used": True, "summary": summary[:1200], "model": "gpt-oss-120b"}


@router.post("/test")
def test(req: LLMTestReq, _admin=Depends(require_admin)):
    if not llm_adapter.is_available():
        raise HTTPException(400, "LLM 이 설정되어 있지 않거나 비활성화됨")
    out = llm_adapter.complete((req.prompt or "").strip(), system=req.system)
    if req.probe_capabilities:
        # This only asks the model to select a harmless echo function. Flow-i
        # never executes the returned call, so the Admin probe has no side effect.
        llm_adapter.reset_native_capabilities()
        tool_probe = llm_adapter.complete_tool_call(
            "Call flowi_capability_echo once with value='ok'.",
            system="This is a capability test. Select the provided function exactly once.",
            tools=[{
                "type": "function",
                "function": {
                    "name": "flowi_capability_echo",
                    "description": "A no-op capability probe. It is never executed.",
                    "strict": True,
                    "parameters": {
                        "type": "object",
                        "properties": {"value": {"type": "string", "enum": ["ok"]}},
                        "required": ["value"],
                        "additionalProperties": False,
                    },
                },
            }],
        )
        structured_probe = llm_adapter.complete_json(
            "Return an object whose value is exactly 'ok'.",
            system="This is a structured-output capability test.",
            schema={
                "type": "object",
                "properties": {"value": {"type": "string", "enum": ["ok"]}},
                "required": ["value"],
                "additionalProperties": False,
            },
            max_retries=0,
        )
        out["capability_probe"] = {
            "tools": {
                "ok": bool(tool_probe.get("ok")),
                "unsupported": bool(tool_probe.get("unsupported")),
                "error": str(tool_probe.get("error") or ""),
            },
            "structured_output": {
                "ok": bool(structured_probe.get("ok")),
                "mode": str(structured_probe.get("structured_mode") or "prompt_json"),
                "error": str(structured_probe.get("error") or ""),
            },
            "detected": llm_adapter.native_capability_snapshot(),
        }
    return out


class FlowiChatReq(BaseModel):
    prompt: str
    token: str = ""
    product: str = ""
    max_rows: int = 12
    # 클라이언트가 만든 진행 표시용 id. GET /flowi/progress/{run_id} 로 같은 턴의
    # 공개 실행 단계를 읽는다. 비어 있으면 진행 표시 없이 기존과 동일하게 동작한다.
    run_id: str = ""
    context: dict[str, Any] = Field(default_factory=dict)


class FlowiAgentChatReq(BaseModel):
    prompt: str
    source_ai: str = "external"
    client_run_id: str = ""
    product: str = ""
    max_rows: int = 12
    context: dict[str, Any] = Field(default_factory=dict)


class FlowiVerifyReq(BaseModel):
    token: str = ""


class FlowiFunctionCallPreviewReq(BaseModel):
    prompt: str
    product: str = ""
    max_rows: int = 12


class FlowiActivationPreviewReq(BaseModel):
    prompts: list[str] = Field(default_factory=list)
    product: str = ""
    max_rows: int = 12
    context: dict[str, Any] = Field(default_factory=dict)


class FlowiFeedbackReq(BaseModel):
    rating: str = ""
    prompt: str = ""
    answer: str = ""
    run_id: str = ""
    intent: str = ""
    note: str = ""
    tags: list[str] = Field(default_factory=list)
    expected_workflow: str = ""
    expected_answer: str = ""
    correct_route: str = ""
    data_refs: str = ""
    golden_candidate: bool = False
    tool: dict[str, Any] = Field(default_factory=dict)
    llm: dict[str, Any] = Field(default_factory=dict)
    elapsed_ms: int | None = None


class FlowiGoldenPromoteReq(BaseModel):
    feedback_id: str
    expected_intent: str = ""
    expected_tool: str = ""
    expected_answer: str = ""
    notes: str = ""


class FlowiAdminUpdateReq(BaseModel):
    mode: str = "both"
    prompt: str = ""
    expected_intent: str = ""
    expected_tool: str = ""
    expected_answer: str = ""
    notes: str = ""
    data_refs: str = ""


class FlowiWorkflowDraftReq(BaseModel):
    prompt: str = ""
    existing_id: str = ""
    workflow: dict[str, Any] = Field(default_factory=dict)


class FlowiWorkflowSaveReq(BaseModel):
    workflow: dict[str, Any] = Field(default_factory=dict)


class FlowiWorkflowDeleteReq(BaseModel):
    workflow_id: str = ""


class FlowiProfileReq(BaseModel):
    notes: str = ""


class FlowiPersonaReq(BaseModel):
    enabled: bool = False
    system_prompt: str = ""
    must_not: str = ""
    notes: str = ""


class FlowiInformConfirmReq(BaseModel):
    draft_id: str
    confirm: bool = False


class FlowiInformWalkthroughStartReq(BaseModel):
    root_lot_ids: list[str] = Field(default_factory=list)
    product: str = ""


class FlowiInformWalkthroughResolveReq(BaseModel):
    session_id: str
    action: str = ""
    value: str = ""
    target_module: str = ""


class FlowiInformWalkthroughConfirmReq(BaseModel):
    session_id: str
    confirm: bool = False


def _flowi_verify_meta(call_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        cfg = llm_adapter.get_config(redact=True)
    except Exception:
        cfg = {}
    token_cache = {}
    try:
        token_cache = llm_adapter._google_adc_token_cache_status()
    except Exception:
        token_cache = {}
    out = {
        "provider": str(cfg.get("provider") or ""),
        "model": str(cfg.get("model") or ""),
        "auth_mode": str(cfg.get("auth_mode") or ""),
        "source": str(cfg.get("source") or ""),
        "token_cache": token_cache,
    }
    if isinstance(call_meta, dict):
        out["call"] = {
            key: call_meta.get(key)
            for key in ("provider", "profile", "model", "latency_ms", "error")
            if call_meta.get(key) not in (None, "", [], {})
        }
    return out


# Cache the live verify result briefly so opening the console isn't slow and we
# don't probe the LLM on every poll.  TTL is short so a recovered/broken endpoint
# is reflected quickly.
_FLOWI_VERIFY_CACHE: dict[str, Any] = {"at": 0.0, "result": None}


def _flowi_verify_ttl_s() -> float:
    raw = str(os.environ.get("FLOW_FLOWI_VERIFY_TTL_S", "") or "").strip()
    try:
        value = float(raw) if raw else 45.0
    except (TypeError, ValueError):
        value = 45.0
    return max(5.0, min(300.0, value))


def _flowi_run_verify_probe() -> dict[str, Any]:
    """Real, bounded LLM liveness probe.  Unlike a config-only check it surfaces
    the true error (token / 403 / model) so the user can fix the connection, and
    it updates the adapter health breaker that gates the chat."""
    started = time.monotonic()
    if not llm_adapter.is_available():
        return {
            "ok": False, "status": "unavailable", "message": "LLM 미설정",
            "error": "llm unavailable", "unavailable": True,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "meta": _flowi_verify_meta(),
        }
    warmup_started = False
    meta0 = _flowi_verify_meta()
    if str(meta0.get("auth_mode") or "").strip().lower() == "google_adc":
        token_cache = meta0.get("token_cache") if isinstance(meta0.get("token_cache"), dict) else {}
        if not token_cache.get("cached"):
            try:
                warmup_started = bool(llm_adapter.warm_google_adc_token_cache(timeout_s=8))
            except Exception:
                warmup_started = False
    out = llm_adapter.complete(
        "연결 확인입니다. 정상 수신했다면 확인완료 라고만 답하세요.",
        system="Flowi 연결 확인 응답은 반드시 확인완료 한 단어로만 작성합니다.",
        timeout=8,
        probe=True,
    )
    elapsed_ms = int((time.monotonic() - started) * 1000)
    meta = _flowi_verify_meta(out.get("meta") if isinstance(out.get("meta"), dict) else None)
    meta["verify_mode"] = "live_probe"
    meta["live_llm_call"] = True
    meta["warmup_started"] = warmup_started
    if out.get("ok") and str(out.get("text") or "").strip():
        return {"ok": True, "status": "connected", "message": "확인완료",
                "elapsed_ms": elapsed_ms, "meta": meta}
    err = str(out.get("error") or "").strip() or "unknown"
    low = err.lower()
    status = "delayed" if ("timed out" in low or "timeout" in low) else "verify_failed"
    return {"ok": False, "status": status, "message": "LLM 연결 확인 실패",
            "error": err, "elapsed_ms": elapsed_ms, "meta": meta}


@router.post("/flowi/verify")
def flowi_verify(req: FlowiVerifyReq, request: Request):
    _ = current_user(request)
    now = time.monotonic()
    force = bool(str(getattr(req, "token", "") or "").strip())
    cached = _FLOWI_VERIFY_CACHE.get("result")
    cached_at = float(_FLOWI_VERIFY_CACHE.get("at") or 0.0)
    if cached and not force and (now - cached_at) < _flowi_verify_ttl_s():
        return {**cached, "cached": True}
    try:
        result = _flowi_run_verify_probe()
    except Exception:
        logger.warning("flowi_verify unexpected error", exc_info=True)
        result = {
            "ok": False, "status": "error", "message": "연결 확인 중 내부 오류 발생",
            "error": "internal_error", "elapsed_ms": int((time.monotonic() - now) * 1000),
        }
    _FLOWI_VERIFY_CACHE["result"] = result
    _FLOWI_VERIFY_CACHE["at"] = time.monotonic()
    return {**result, "cached": False}


@router.get("/flowi/chart-session/raw-data.csv")
def flowi_chart_session_raw_data(request: Request, chart_session_id: str = Query(...)):
    me = current_user(request)
    if "dashboard" not in _allowed_flowi_feature_keys(me):
        raise HTTPException(403, "Dashboard access denied")
    meta, csv_bytes = _flowi_chart_raw_download_payload(
        chart_session_id,
        username=me.get("username") or "",
        role=str(me.get("role") or "user"),
    )
    return csv_response(csv_bytes, meta.get("filename") or "flowi_chart_raw.csv")


@router.post("/flowi/function-call/preview")
def flowi_function_call_preview(req: FlowiFunctionCallPreviewReq, _admin=Depends(require_admin)):
    prompt = (req.prompt or "").strip()
    if not prompt:
        raise HTTPException(400, "prompt is required")
    return _structure_flowi_function_call(
        prompt,
        product=(req.product or "").strip(),
        max_rows=req.max_rows,
    )


@router.post("/flowi/orchestrator/preview")
def flowi_orchestrator_preview(req: FlowiActivationPreviewReq, request: Request):
    current_user(request)
    prompts = [str(p or "").strip() for p in (req.prompts or []) if str(p or "").strip()]
    if not prompts:
        raise HTTPException(400, "prompts are required")
    rows = _flowi_orchestrator_activation_previews(
        prompts,
        product=(req.product or "").strip(),
        max_rows=req.max_rows,
    )
    if isinstance(req.context, dict) and req.context.get("ask_llm_to_guess_missing"):
        for row in rows:
            guessed = _flowi_guess_missing_for_preview(str(row.get("prompt") or ""), row)
            if guessed:
                row["guessed"] = guessed
    features = Counter(str(row.get("feature") or "general") for row in rows)
    return {
        "ok": True,
        "mode": "dry_run",
        "count": len(rows),
        "features": [{"feature": key, "count": count} for key, count in features.most_common()],
        "rows": rows,
    }


@router.get("/flowi/persona-card")
def flowi_persona_card(request: Request):
    current_user(request)
    cfg = _flowi_persona_config()
    dont = []
    for line in str(cfg.get("must_not") or FLOWI_DEFAULT_MUST_NOT).splitlines():
        clean = line.strip().lstrip("-").strip()
        if clean:
            dont.append(clean)
    do_list = [
        "lot 조회",
        "plan 등록/통보",
        "인폼 메일",
        "파일/DB preview",
        "KNOB/MASK",
        "FAB 진행",
        "ET 측정",
        "자연어 인폼 등록",
    ]
    return {
        "ok": True,
        "persona": FLOWI_AGENT_PERSONA,
        "do_list": do_list,
        "dont_list": dont[:5],
    }


@router.get("/flowi/workflows")
def flowi_workflows(request: Request):
    me = current_user(request)
    catalog = flowi_workflow_catalog.load_catalog(ensure=True)
    matches_preview = []
    try:
        matches_preview = flowi_workflow_catalog.match_workflows("split knob fab trend corr raw data step", limit=8)
    except Exception:
        matches_preview = []
    return {
        "ok": True,
        "can_edit": (me.get("role") or "user") == "admin",
        "schema_version": catalog.get("version"),
        "default_target_count": catalog.get("default_target_count"),
        "description": catalog.get("description") or "",
        "path": catalog.get("path") or "",
        "default_path": catalog.get("default_path") or "",
        "workflows": catalog.get("workflows") or [],
        "matches_preview": matches_preview,
    }


FLOWI_WORKFLOW_FORMAT_SCHEMA = {
    "type": "object",
    "required": ["title"],
    "properties": {
        "title": {"type": "string"},
        "enabled": {"type": "boolean"},
        "priority": {"type": "integer"},
        "category": {"type": "string"},
        "unit_ai": {"type": "string"},
        "action": {"type": "string"},
        "examples": {"type": "array", "items": {"type": "string"}},
        "question_template": {"type": "string"},
        "trigger_terms": {"type": "array", "items": {"type": "string"}},
        "slots": {"type": "array", "items": {"type": "object"}},
        "source_roles": {"type": "array", "items": {"type": "string"}},
        "steps": {"type": "array", "items": {"type": "string"}},
        "orchestration": {"type": "array", "items": {"type": "string"}},
        "result_contract": {"type": "object"},
    },
}


def _flowi_format_workflow_with_connected_llm(
    prompt: str,
    fallback: dict[str, Any],
    *,
    actor: str,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    if not llm_adapter.is_available():
        return fallback, {"available": False, "used": False}, "로컬 규칙으로 workflow schema에 맞게 형식화했습니다."
    payload = {
        "instruction": (
            "사용자 입력과 현재 workflow 초안을 Flow-i workflow JSON 형식에 맞게 정리한다. "
            "도구를 실행하거나 결과를 추측하지 말고, 저장 가능한 공개 필드만 반환한다."
        ),
        "allowed_unit_ai": sorted(flowi_workflow_catalog.KNOWN_UNIT_AIS),
        "allowed_source_roles": sorted(flowi_workflow_catalog.KNOWN_SOURCE_ROLES),
        "user_input": prompt,
        "current_workflow": fallback,
    }
    out = llm_adapter.complete_json(
        json.dumps(payload, ensure_ascii=False, default=str),
        system=(
            "You format Flow-i workflow templates. Return only JSON fields that match the schema. "
            "Do not include hidden reasoning, markdown, prose, execution results, or database content."
        ),
        schema=FLOWI_WORKFLOW_FORMAT_SCHEMA,
        timeout=10,
        max_retries=1,
    )
    if not out.get("ok") or not isinstance(out.get("obj"), dict):
        err = str(out.get("error") or "")[:200]
        return fallback, {"available": True, "used": False, "error": err}, "LLM 형식화 실패로 로컬 규칙 결과를 사용했습니다."
    allowed = set((FLOWI_WORKFLOW_FORMAT_SCHEMA.get("properties") or {}).keys())
    shaped = {key: value for key, value in (out.get("obj") or {}).items() if key in allowed}
    merged = {**fallback, **shaped}
    if "examples" in shaped and "question_template" not in shaped:
        merged["question_template"] = ""
    workflow = flowi_workflow_catalog.normalize_workflow(merged, actor=actor, base=fallback)
    return workflow, {"available": True, "used": True}, "연결된 LLM으로 workflow schema 형식만 맞췄습니다."


@router.post("/flowi/workflows/draft")
def flowi_workflows_draft(req: FlowiWorkflowDraftReq, _admin=Depends(require_admin)):
    prompt = (req.prompt or "").strip()
    existing = None
    if req.existing_id:
        for row in flowi_workflow_catalog.load_catalog(ensure=True).get("workflows") or []:
            if str(row.get("id") or "") == req.existing_id:
                existing = row
                break
    if isinstance(req.workflow, dict) and req.workflow:
        existing = {**(existing or {}), **req.workflow}
    if not prompt and not existing:
        raise HTTPException(400, "prompt 또는 workflow가 필요합니다.")
    actor = _admin.get("username") or "admin"
    fallback = flowi_workflow_catalog.draft_workflow(
        prompt or str((existing or {}).get("title") or ""),
        base=existing,
        actor=actor,
    )
    workflow, llm_meta, note = _flowi_format_workflow_with_connected_llm(prompt, fallback, actor=actor)
    return {
        "ok": True,
        "workflow": workflow,
        "llm": llm_meta,
        "note": note,
    }


@router.post("/flowi/workflows")
def flowi_workflows_save(req: FlowiWorkflowSaveReq, _admin=Depends(require_admin)):
    if not isinstance(req.workflow, dict) or not req.workflow:
        raise HTTPException(400, "workflow가 필요합니다.")
    actor = _admin.get("username") or "admin"
    workflow = flowi_workflow_catalog.save_workflow(req.workflow, actor=actor)
    _append_user_event(actor, "flowi_workflow_save", {
        "workflow_id": workflow.get("id") or "",
        "title": workflow.get("title") or "",
        "unit_ai": workflow.get("unit_ai") or "",
        "action": workflow.get("action") or "",
    })
    return {"ok": True, "workflow": workflow}


@router.post("/flowi/workflows/delete")
def flowi_workflows_delete(req: FlowiWorkflowDeleteReq, _admin=Depends(require_admin)):
    workflow_id = (req.workflow_id or "").strip()
    if not workflow_id:
        raise HTTPException(400, "workflow_id가 필요합니다.")
    actor = _admin.get("username") or "admin"
    workflow = flowi_workflow_catalog.disable_workflow(workflow_id, actor=actor)
    if not workflow:
        raise HTTPException(404, "workflow를 찾을 수 없습니다.")
    _append_user_event(actor, "flowi_workflow_disable", {
        "workflow_id": workflow.get("id") or "",
        "title": workflow.get("title") or "",
    })
    return {"ok": True, "deleted": True, "workflow": workflow}


@router.post("/flowi/workflows/merge-defaults")
def flowi_workflows_merge_defaults(_admin=Depends(require_admin)):
    actor = _admin.get("username") or "admin"
    catalog = flowi_workflow_catalog.ensure_runtime_catalog(actor=actor)
    _append_user_event(actor, "flowi_workflow_merge_defaults", {
        "installed_defaults": catalog.get("installed_defaults"),
        "preserved": catalog.get("preserved"),
    })
    return {"ok": True, **catalog}


@router.post("/flowi/inform/confirm")
def flowi_inform_confirm(req: FlowiInformConfirmReq, request: Request):
    me = current_user(request)
    return _flowi_confirm_inform_draft(req.draft_id, req.confirm, me)


@router.post("/flowi/inform/walkthrough/start")
def flowi_inform_walkthrough_start(req: FlowiInformWalkthroughStartReq, request: Request):
    me = current_user(request)
    roots = [str(x).strip() for x in (req.root_lot_ids or []) if str(x).strip()]
    if not roots:
        raise HTTPException(400, "root_lot_ids required")
    return _flowi_start_walkthrough({"root_lot_ids": roots, "product": (req.product or "").strip()}, me)


@router.post("/flowi/inform/walkthrough/resolve")
def flowi_inform_walkthrough_resolve(req: FlowiInformWalkthroughResolveReq, request: Request):
    me = current_user(request)
    state = _flowi_load_inform_state(req.session_id)
    if req.target_module:
        state["current_module"] = _flowi_module_token(req.target_module) or req.target_module
    prompt = req.value or req.action
    if req.action and req.action not in {"set", "skip", "jump", "add_split", "set_note", "finalize"}:
        prompt = req.action + " " + prompt
    if req.action == "skip":
        prompt = "생략"
    elif req.action == "finalize":
        prompt = "이대로 등록"
    return _flowi_resolve_walkthrough_state(state, prompt, me)


@router.post("/flowi/inform/walkthrough/confirm")
def flowi_inform_walkthrough_confirm(
    req: FlowiInformWalkthroughConfirmReq,
    request: Request,
    _admin=Depends(require_admin),
):
    me = current_user(request)
    return _flowi_confirm_inform_draft(req.session_id, req.confirm, me)


@router.get("/flowi/persona")
def flowi_persona(_admin=Depends(require_admin)):
    cfg = _flowi_persona_config()
    return {"ok": True, **cfg}


@router.post("/flowi/persona")
def flowi_persona_save(req: FlowiPersonaReq, request: Request, _admin=Depends(require_admin)):
    system_prompt = (req.system_prompt or "").strip()
    must_not = (req.must_not or "").strip()
    notes = (req.notes or "").strip()
    if len(system_prompt) > 12000:
        raise HTTPException(400, "system_prompt는 12000자 이하로 입력해주세요")
    if len(must_not) > 8000:
        raise HTTPException(400, "must_not은 8000자 이하로 입력해주세요")
    if len(notes) > 2000:
        raise HTTPException(400, "notes는 2000자 이하로 입력해주세요")
    current = _admin_settings()
    me = current_user(request)
    current["flowi_persona"] = {
        "enabled": True,
        "system_prompt": system_prompt or FLOWI_DEFAULT_SYSTEM_PROMPT,
        "must_not": must_not or FLOWI_DEFAULT_MUST_NOT,
        "notes": notes,
        "updated_by": me.get("username") or "admin",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_admin_settings(current)
    cfg = _flowi_persona_config()
    return {"ok": True, **cfg}


@router.get("/flowi/profile")
def flowi_profile(request: Request):
    me = current_user(request)
    username = me.get("username") or "user"
    md = _read_user_md(username, create=False)
    return {
        "ok": True,
        "username": username,
        "notes": _notes_from_md(md),
        "markdown": md,
    }


@router.post("/flowi/profile")
def flowi_profile_save(req: FlowiProfileReq, request: Request):
    me = current_user(request)
    username = me.get("username") or "user"
    notes = (req.notes or "").strip()
    if len(notes) > 20000:
        raise HTTPException(400, "사용자 메모는 20000자 이하로 입력해주세요")
    md = _write_user_notes(username, notes)
    _append_user_event(username, "profile_update", {"notes": notes[:500]})
    return {
        "ok": True,
        "username": username,
        "notes": _notes_from_md(md),
    }


@router.post("/flowi/feedback")
def flowi_feedback(req: FlowiFeedbackReq, request: Request):
    me = current_user(request)
    is_admin = (me.get("role") or "user") == "admin"
    rating = (req.rating or "").strip().lower()
    if rating not in {"up", "down", "neutral"}:
        raise HTTPException(400, "rating must be up/down/neutral")
    tags = _normalize_feedback_tags(req.tags, rating)
    if not is_admin:
        tags = [tag for tag in tags if tag in FLOWI_USER_FEEDBACK_TAGS]
    tool_summary = _flowi_tool_summary(req.tool if is_admin else {})
    golden_candidate = bool(req.golden_candidate) if is_admin else False
    needs_review = rating != "up" or golden_candidate or any(tag != "correct" for tag in tags)
    rec = {
        "id": "ff_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:8],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "username": me.get("username") or "",
        "rating": rating,
        "run_id": (req.run_id or "").strip()[:160],
        "intent": ((req.intent or "").strip()[:80] if is_admin else ""),
        "prompt_excerpt": (req.prompt or "").strip()[:500],
        "answer_excerpt": (req.answer or "").strip()[:800],
        "note": (req.note or "").strip()[:1000],
        "tags": tags,
        "expected_workflow": ((req.expected_workflow or "").strip()[:160] if is_admin else ""),
        "expected_answer": ((req.expected_answer or "").strip()[:2000] if is_admin else ""),
        "correct_route": ((req.correct_route or "").strip()[:2000] if is_admin else ""),
        "data_refs": ((req.data_refs or "").strip()[:1000] if is_admin else ""),
        "golden_candidate": golden_candidate,
        "needs_review": needs_review,
        "review_status": "golden_candidate" if golden_candidate else ("needs_review" if needs_review else "ok"),
        "tool_summary": tool_summary,
        "llm": {
            "used": bool(req.llm.get("used")) if isinstance(req.llm, dict) else False,
            "available": bool(req.llm.get("available")) if isinstance(req.llm, dict) else False,
            "provider": str(req.llm.get("provider") or "")[:80] if isinstance(req.llm, dict) else "",
            "model": str(req.llm.get("model") or "")[:120] if isinstance(req.llm, dict) else "",
        },
        "elapsed_ms": req.elapsed_ms if isinstance(req.elapsed_ms, int) and req.elapsed_ms >= 0 else None,
    }
    try:
        FLOWI_FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
        with FLOWI_FEEDBACK_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("flowi feedback save failed: %s", e)
        raise HTTPException(500, "피드백 저장 실패")
    _append_user_event(me.get("username") or "user", "feedback", {
        "rating": rating,
        "intent": rec["intent"],
        "tags": ", ".join(tags),
        "needs_review": needs_review,
        "golden_candidate": rec["golden_candidate"],
        "note": rec["note"],
        "prompt": rec["prompt_excerpt"],
    })
    penalty_profile = None
    if rating in {"up", "down"}:
        unit_key = agent_feedback_penalties.home_feedback_unit_key(req.tool)
        feedback_tool = unit_key or str((req.tool or {}).get("feature") or (req.tool or {}).get("intent") or req.intent or "").strip()
        reason = rec["note"] or ", ".join(tags) or rec["intent"]
        try:
            agent_feedback_penalties.record_home_feedback(
                rating=rating,
                planner=str((req.tool or {}).get("action") or rec["intent"] or ""),
                tool=feedback_tool,
                source="llm_flowi_chat",
                reason=reason,
                actor=me.get("username") or "",
            )
            if unit_key:
                penalty_profile = agent_feedback_penalties.record_feedback(
                    unit_key,
                    rating,
                    run_id=rec["run_id"],
                    reason=reason,
                    actor=me.get("username") or "",
                )
        except Exception:
            logger.debug("flowi feedback penalty update failed", exc_info=True)
    if rating == "down" and isinstance(req.tool, dict):
        semantic_resolution = req.tool.get("semantic_resolution") if isinstance(req.tool.get("semantic_resolution"), dict) else {}
        if semantic_resolution.get("auto_applied"):
            try:
                semantic_hitl.record_rejection(
                    username=me.get("username") or "user",
                    term=str(semantic_resolution.get("term") or ""),
                    source_type=str(semantic_resolution.get("source_type") or "ET"),
                    item_id=str(semantic_resolution.get("item_id") or ""),
                    product=str(semantic_resolution.get("product") or ""),
                )
            except (TypeError, ValueError):
                logger.debug("semantic auto-resolution rejection save failed", exc_info=True)
    return {"ok": True, "id": rec["id"], "needs_review": needs_review, "penalty_profile": penalty_profile}


@router.get("/flowi/feedback/summary")
def flowi_feedback_summary(
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(300, ge=1, le=1000),
    _admin=Depends(require_admin),
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = _read_jsonl(FLOWI_FEEDBACK_FILE, limit=max(1000, limit * 5))
    records = []
    for rec in rows:
        ts = _parse_ts(rec.get("timestamp"))
        if ts and ts < cutoff:
            continue
        rec = dict(rec)
        rec["tags"] = _normalize_feedback_tags(rec.get("tags") or rec.get("failure_types") or [], rec.get("rating") or "")
        records.append(rec)
    summary = _feedback_summary_from_records(records)
    golden = _read_jsonl(FLOWI_GOLDEN_FILE, limit=200)
    return {
        "ok": True,
        "days": days,
        "taxonomy": FLOWI_FEEDBACK_TAXONOMY,
        "total": summary["total"],
        "by_rating": summary["by_rating"],
        "by_tag": summary["by_tag"],
        "by_user": summary["by_user"],
        "by_intent": summary["by_intent"],
        "by_workflow": summary["by_workflow"],
        "recent": summary["recent"][:limit],
        "review_queue": summary["review_queue"][:min(limit, 200)],
        "golden_cases": sorted(golden, key=lambda r: str(r.get("timestamp") or ""), reverse=True)[:100],
    }


@router.post("/flowi/feedback/promote")
def flowi_feedback_promote(req: FlowiGoldenPromoteReq, _admin=Depends(require_admin)):
    feedback_id = (req.feedback_id or "").strip()
    if not feedback_id:
        raise HTTPException(400, "feedback_id is required")
    records = _read_jsonl(FLOWI_FEEDBACK_FILE, limit=10000)
    rec = next((r for r in reversed(records) if str(r.get("id") or "") == feedback_id), None)
    if not rec:
        raise HTTPException(404, "feedback not found")
    case = _feedback_to_golden_case(
        rec,
        created_by=_admin.get("username") or "admin",
        expected_intent=req.expected_intent,
        expected_tool=req.expected_tool,
        expected_answer=req.expected_answer,
        notes=req.notes,
    )
    try:
        FLOWI_GOLDEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        with FLOWI_GOLDEN_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(case, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("flowi golden case save failed: %s", e)
        raise HTTPException(500, "golden case 저장 실패")
    _append_user_event(_admin.get("username") or "admin", "golden_case_promote", {
        "feedback_id": feedback_id,
        "golden_id": case["id"],
        "expected_intent": case["expected_intent"],
        "expected_tool": case["expected_tool"],
    })
    return {"ok": True, "case": case}


@router.post("/flowi/admin/update")
def flowi_admin_update(req: FlowiAdminUpdateReq, _admin=Depends(require_admin)):
    mode = (req.mode or "both").strip().lower()
    if mode not in {"knowledge", "workflow", "both"}:
        raise HTTPException(400, "mode must be knowledge/workflow/both")
    if mode == "knowledge":
        raise HTTPException(400, "사전지식 등록은 에이전트 페이지의 RAG 반영 화면에서만 가능합니다.")
    if mode == "both":
        mode = "workflow"
    prompt = (req.prompt or "").strip()
    expected_intent = (req.expected_intent or "").strip()
    expected_tool = (req.expected_tool or "").strip()
    expected_answer = (req.expected_answer or "").strip()
    notes = (req.notes or "").strip()
    data_refs = (req.data_refs or "").strip()
    if not any([prompt, expected_intent, expected_tool, expected_answer, notes, data_refs]):
        raise HTTPException(400, "업데이트할 사전지식 또는 workflow 내용을 입력해주세요")

    admin_user = _admin.get("username") or "admin"
    result: dict[str, Any] = {"ok": True, "mode": mode}

    wants_workflow = mode == "workflow" and any([prompt, expected_intent, expected_tool, expected_answer, notes, data_refs])
    if wants_workflow:
        rec = {
            "id": "admin_direct_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:8],
            "prompt_excerpt": (prompt or notes or expected_answer)[:500],
            "rating": "up",
            "intent": expected_intent[:120],
            "tags": ["correct"],
            "expected_answer": expected_answer[:4000],
            "correct_route": expected_answer[:4000],
            "data_refs": data_refs[:1000],
            "note": notes[:2000],
            "expected_workflow": expected_tool[:160],
            "tool_summary": {"action": expected_tool[:160], "intent": expected_intent[:120]},
        }
        case = _feedback_to_golden_case(
            rec,
            created_by=admin_user,
            expected_intent=expected_intent,
            expected_tool=expected_tool,
            expected_answer=expected_answer,
            notes=notes,
        )
        try:
            FLOWI_GOLDEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            with FLOWI_GOLDEN_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(case, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning("flowi admin workflow update failed: %s", e)
            raise HTTPException(500, "workflow 업데이트 저장 실패") from e
        result["workflow"] = case

    _append_user_event(admin_user, "admin_agent_update", {
        "mode": mode,
        "prompt": prompt[:500],
        "expected_intent": expected_intent[:120],
        "expected_tool": expected_tool[:160],
        "workflow_id": ((result.get("workflow") or {}).get("id") if isinstance(result.get("workflow"), dict) else ""),
    })
    return result


@router.post("/flowi/chat")
def flowi_chat(req: FlowiChatReq, request: Request):
    me = current_user(request)
    if not flowi_gate.access_allowed(me):
        return flowi_gate.denied_payload(me)
    # 진행 표시 채널은 요청 처리보다 먼저 연다 — 폴링이 첫 이벤트를 놓치지 않게.
    run_id = flowi_progress.begin(req.run_id) if str(req.run_id or "").strip() else ""
    done = {"status": "success", "label": "답변 정리"}
    try:
        with flowi_gate.slot(username=me.get("username") or "", role=me.get("role") or "user"):
            result = _run_flowi_chat_maybe_offloaded(
                prompt=req.prompt,
                product=req.product,
                max_rows=req.max_rows,
                me=me,
                client_run_id=run_id,
                agent_context=req.context,
            )
        payload = _flowi_home_response_for_role(result, me)
        if run_id:
            payload = {**payload, "run_id": run_id}
        return payload
    except flowi_gate.FlowiBusy as busy:
        done = {"status": "blocked", "label": "동시 실행 한도"}
        return flowi_gate.busy_payload(busy)
    except HTTPException:
        done = {"status": "failed", "label": "요청 실패"}
        raise
    except Exception:
        logger.warning("flowi_chat unexpected error", exc_info=True)
        done = {"status": "failed", "label": "내부 오류"}
        return {
            "ok": True,
            "type": "answer",
            "intent": "error_fallback",
            "answer": "내부 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
            "llm": {"available": False, "used": False, "error": "internal_error"},
        }
    finally:
        # 어떤 경로로 끝나든 딱 한 번 마감한다 — 폴링이 done 을 보고 멈춘다.
        flowi_progress.end(run_id, status=done["status"], label=done["label"])
        # 이 컨텍스트에 run id 를 남기지 않는다 — 뒤따르는 비-Flow-i 호출이
        # 끝난 턴의 진행 파일에 이벤트를 덧붙이지 않도록.
        flowi_progress.bind("")


@router.get("/flowi/progress/{run_id}")
def flowi_progress_read(run_id: str, request: Request, after: int = 0):
    """진행 중인 Flow-i 턴의 **공개 실행 단계**를 읽는다 (폴링).

    담기는 것은 어떤 단위기능/오케스트레이터/모델 호출이 떴고 어떻게 끝났는지와
    소요시간뿐이다. 프롬프트 원문·모델 추론·SQL·행 데이터는 담지 않는다
    (`core.flowi_progress` 가 공개 키 화이트리스트로 자른다).
    """
    current_user(request)
    return flowi_progress.read(run_id, after=after)


@router.post("/flowi/agent/chat")
def flowi_agent_chat(req: FlowiAgentChatReq, request: Request):
    """External AI clients can call the same read-only Flowi web-app router.

    Authentication still uses the normal Flow session token; the body fields
    only identify the calling AI and correlate its run id for audit/debugging.
    """
    me = current_user(request)
    if not flowi_gate.access_allowed(me):
        return flowi_gate.denied_payload(me)
    try:
        with flowi_gate.slot(username=me.get("username") or "", role=me.get("role") or "user"):
            return _run_flowi_chat_maybe_offloaded(
                prompt=req.prompt,
                product=req.product,
                max_rows=req.max_rows,
                me=me,
                source_ai=req.source_ai,
                client_run_id=req.client_run_id,
                agent_context=req.context,
            )
    except flowi_gate.FlowiBusy as busy:
        return flowi_gate.busy_payload(busy)
    except HTTPException:
        raise
    except Exception:
        logger.warning("flowi_agent_chat unexpected error", exc_info=True)
        return {
            "ok": True,
            "type": "answer",
            "intent": "error_fallback",
            "answer": "내부 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
            "llm": {"available": False, "used": False, "error": "internal_error"},
        }


# --- Flow-i EDM proposals -------------------------------------------------
# Home Agent write bridge for FileBrowser EDM actions.  These endpoints keep
# write operations behind an explicit proposal + confirmation boundary; the
# actual file/schema mutations remain owned by FileBrowser's deterministic APIs.
FLOWI_EDM_PROPOSAL_DIR = PATHS.cache_dir / "flowi_edm_proposals"


class FlowiEdmProposalReq(BaseModel):
    action_type: str = "rollback_file"  # rollback_file | edit_file | save_schema_snapshot
    file: str = ""
    version: str = ""
    text: str = ""
    note: str = ""
    schema: dict[str, Any] = Field(default_factory=dict)
    target: dict[str, Any] = Field(default_factory=dict)


class FlowiEdmExecuteReq(BaseModel):
    proposal_id: str
    confirm: str = ""


def _flowi_edm_proposal_path(proposal_id: str) -> Path:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(proposal_id or "")).strip("._-")
    if not clean:
        raise HTTPException(400, "proposal_id is required")
    FLOWI_EDM_PROPOSAL_DIR.mkdir(parents=True, exist_ok=True)
    return FLOWI_EDM_PROPOSAL_DIR / f"{clean}.json"


def _flowi_edm_confirm_text(action_type: str, file: str, version: str = "") -> str:
    import hashlib as _hashlib
    raw = f"{action_type}|{file}|{version}".encode("utf-8")
    return "CONFIRM_FLOWI_EDM_" + _hashlib.sha256(raw).hexdigest()[:12].upper()


def _flowi_edm_can_write(me: dict[str, Any]) -> bool:
    username = me.get("username") or ""
    return (me.get("role") == "admin") or is_page_admin(username, "filebrowser")


@router.post("/flowi/edm/propose")
def flowi_edm_propose(req: FlowiEdmProposalReq, request: Request):
    me = current_user(request)
    action_type = str(req.action_type or "").strip()
    if action_type not in {"rollback_file", "edit_file", "save_schema_snapshot"}:
        raise HTTPException(400, f"unsupported EDM action: {action_type}")
    if action_type in {"rollback_file", "edit_file"} and not str(req.file or "").strip():
        raise HTTPException(400, "file is required")
    if action_type == "rollback_file" and not str(req.version or "").strip():
        raise HTTPException(400, "version is required")
    if action_type == "edit_file" and req.text == "":
        raise HTTPException(400, "text is required")
    if action_type == "save_schema_snapshot" and not (req.schema.get("columns") or []):
        raise HTTPException(400, "schema.columns is required")

    from app_v2.shared.contracts import AgentActionProposal, FlowEntityKey

    proposal_id = "edm_" + datetime.now().strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:8]
    confirm = _flowi_edm_confirm_text(action_type, req.file, req.version)
    risk = "admin_change" if action_type in {"rollback_file", "edit_file"} else "write"
    summary = {
        "rollback_file": f"Rollback EDM file {req.file} to {req.version}",
        "edit_file": f"Save EDM text file {req.file}",
        "save_schema_snapshot": "Save FileBrowser schema snapshot",
    }[action_type]
    proposal = AgentActionProposal(
        action_id=proposal_id,
        action_type=action_type,
        target=FlowEntityKey(**(req.target or {})),
        file=req.file,
        summary=summary,
        payload={
            "file": req.file,
            "version": req.version,
            "text": req.text,
            "note": req.note,
            "schema": req.schema,
        },
        requires_confirmation=True,
        risk_level=risk,
    ).dict()
    stored = {
        "proposal": proposal,
        "confirm": confirm,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": me.get("username") or "user",
        "executed": False,
    }
    save_json(_flowi_edm_proposal_path(proposal_id), stored, indent=2)
    return {
        "ok": True,
        "proposal": proposal,
        "requires_confirmation": True,
        "confirm": confirm,
        "choices": [{
            "id": "confirm_flowi_edm",
            "title": "확인 후 실행",
            "description": "이 값을 그대로 confirm으로 보내면 FileBrowser EDM deterministic API가 실행됩니다.",
            "prompt": confirm,
        }],
    }


@router.post("/flowi/edm/execute")
def flowi_edm_execute(req: FlowiEdmExecuteReq, request: Request):
    me = current_user(request)
    fp = _flowi_edm_proposal_path(req.proposal_id)
    stored = load_json(fp, None)
    if not isinstance(stored, dict):
        raise HTTPException(404, "proposal not found")
    if stored.get("executed"):
        raise HTTPException(409, "proposal already executed")
    expected = str(stored.get("confirm") or "")
    if str(req.confirm or "").strip() != expected:
        return {
            "ok": False,
            "requires_confirmation": True,
            "expected_confirm": expected,
            "received_confirm": req.confirm,
        }
    proposal = stored.get("proposal") if isinstance(stored.get("proposal"), dict) else {}
    action_type = str(proposal.get("action_type") or "")
    payload = proposal.get("payload") if isinstance(proposal.get("payload"), dict) else {}
    if action_type in {"rollback_file", "edit_file"} and not _flowi_edm_can_write(me):
        raise HTTPException(403, "Admin or delegated filebrowser admin only")

    from routers import filebrowser as fb

    if action_type == "rollback_file":
        result = fb.rollback_base_file(
            fb.BaseFileRollbackReq(
                file=str(payload.get("file") or ""),
                version=str(payload.get("version") or ""),
                username=me.get("username") or "user",
                note=str(payload.get("note") or "Flow-i EDM rollback"),
            ),
            request,
        )
    elif action_type == "edit_file":
        result = fb.save_base_text_file(
            fb.BaseTextFileSaveReq(
                file=str(payload.get("file") or ""),
                text=str(payload.get("text") or ""),
                username=me.get("username") or "user",
                note=str(payload.get("note") or "Flow-i EDM text edit"),
            ),
            request,
        )
    elif action_type == "save_schema_snapshot":
        schema = payload.get("schema") if isinstance(payload.get("schema"), dict) else {}
        result = fb.save_schema_snapshot(
            fb.SchemaSnapshotReq(
                source_type=str(schema.get("source_type") or ""),
                root=str(schema.get("root") or ""),
                product=str(schema.get("product") or ""),
                file=str(schema.get("file") or ""),
                columns=list(schema.get("columns") or []),
                total_rows=schema.get("total_rows"),
                username=me.get("username") or "user",
                note=str(payload.get("note") or "Flow-i schema snapshot"),
            ),
            request,
        )
    else:
        raise HTTPException(400, f"unsupported EDM action: {action_type}")

    stored["executed"] = True
    stored["executed_at"] = datetime.now(timezone.utc).isoformat()
    stored["executed_by"] = me.get("username") or "user"
    stored["result"] = result
    save_json(fp, stored, indent=2)
    return {"ok": True, "proposal_id": req.proposal_id, "action_type": action_type, "result": result}
