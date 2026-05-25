"""core/home_orchestrator.py — 홈 에이전트 오케스트레이터.

자연어 prompt 를 받아 ToolRegistry 의 도구 중 적합한 것을 1~N개 골라
순차 실행하고, 트레이스(어떤 도구를 왜 골랐는지)를 응답에 포함한다.

선택 전략 (우선순위):
  1. LLM planner — env `FLOW_LLM_TOOL_CALL=1` 이고 llm_adapter 가 enabled 면
     LLM 에 tool catalog 와 prompt 를 보내 step sequence(JSON) 를 받아 실행.
  2. 휴리스틱 dispatcher — prompt 의 한국어/영어 키워드를 태그/이름에 매칭
     해 도구 후보 가중치 산정 → 상위 top_k 선택.

실행:
  - flowi_units 의 unit_ai 는 try_dispatch 위임 (slots 으로 LLM 결정 인자 전달).
  - function-call 단위는 trace stub (실제 실행은 기존 flowi/chat 경로).

트레이스 포맷:
  [{"tool": name, "kind": "unit_ai"|"function",
    "input": {...}, "output_preview": "...",
    "ok": bool, "ms": int, "matched_terms"|"reason": "..."}]

orchestrate_stream(prompt, user) → generator: SSE event chunk dict 시퀀스.
  {"type": "plan", "steps": [...]}
  {"type": "step_start", "step": {...}}
  {"type": "step_end", "step": {..., "ok": bool, "output_preview": "..."}}
  {"type": "reply", "text": "...", "trace": [...]}
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Iterator

from core import tool_registry

logger = logging.getLogger("flow.home_orchestrator")

_MAX_STEPS = 10
_LLM_ENV_FLAG = "FLOW_LLM_TOOL_CALL"


# 한국어/영어 키워드 → 태그 가중치. 매칭되면 해당 태그 보유 도구가 점수 획득.
KEYWORD_WEIGHTS: list[tuple[re.Pattern[str], dict[str, float]]] = [
    (re.compile(r"\b(sql|join|쿼리|조인)\b", re.I), {"sql_workspace": 3.0, "filebrowser": 1.0}),
    (re.compile(r"(차트|그래프|시각화|chart|plot|trend)", re.I), {"chart": 2.5, "dashboard": 2.0}),
    (re.compile(r"(평균|중앙값|avg|median|집계|aggregate)", re.I), {"chart": 1.2, "dashboard": 1.2}),
    (re.compile(r"(lot|wafer|fab|로트|웨이퍼)", re.I), {"lot": 2.0, "fab": 1.5}),
    (re.compile(r"(knob|mask|스플릿|split|splittable)", re.I), {"splittable": 2.5, "knob": 2.0}),
    (re.compile(r"(인폼|inform|메일|mail)", re.I), {"inform": 2.5}),
    (re.compile(r"(회의|meeting|아젠다)", re.I), {"meeting": 2.5}),
    (re.compile(r"(이슈|tracker|추적)", re.I), {"tracker": 2.5}),
    (re.compile(r"(파일|file|parquet|csv|디렉토리)", re.I), {"filebrowser": 2.0}),
    (re.compile(r"(스키마|schema|컬럼|column)", re.I), {"filebrowser": 1.5, "search": 1.0}),
    (re.compile(r"(step|단계)", re.I), {"step": 1.5}),
    (re.compile(r"(et|elapsed)", re.I), {"ettime": 2.0}),
    (re.compile(r"(diagnosis|진단|rca|dibl|vth|ion|ioff)", re.I), {"diagnosis": 2.5}),
    (re.compile(r"(일정|캘린더|calendar|변경점)", re.I), {"calendar": 2.5}),
    (re.compile(r"(layout|tablemap|관계|join)", re.I), {"tablemap": 1.5}),
]


def _keyword_signals(prompt: str) -> tuple[dict[str, float], list[str]]:
    signals: dict[str, float] = {}
    matched: list[str] = []
    for rx, weights in KEYWORD_WEIGHTS:
        m = rx.search(prompt or "")
        if not m:
            continue
        matched.append(m.group(0))
        for tag, w in weights.items():
            signals[tag] = signals.get(tag, 0.0) + w
    return signals, matched


def _score_tool(tool: dict[str, Any], signals: dict[str, float], prompt_lower: str) -> float:
    if not tool.get("enabled"):
        return 0.0
    score = 0.0
    for tag in tool.get("tags") or []:
        if tag in signals:
            score += signals[tag]
    # 이름/제목 키워드 매칭 bonus
    nm = (tool.get("name") or "").lower()
    title = (tool.get("title") or "").lower()
    for token in re.findall(r"[A-Za-z가-힣]{3,}", prompt_lower):
        if token in nm:
            score += 0.8
        elif token in title:
            score += 0.5
    return score


def _pick_tools(prompt: str, top_k: int = 2) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    items = tool_registry.list_tools(include_stats=False)
    signals, matched_terms = _keyword_signals(prompt)
    plower = (prompt or "").lower()
    scored = [(t, _score_tool(t, signals, plower)) for t in items]
    scored = [(t, s) for t, s in scored if s > 0.0]
    scored.sort(key=lambda x: -x[1])
    picked = scored[:top_k]
    meta = {"signals": signals, "matched_terms": matched_terms, "candidate_count": len(scored)}
    return [{"tool": t, "score": s} for t, s in picked], meta


# 실제 실행 가능한 도구 매핑 — 회귀 위험 최소화 위해 좁게 시작.
def _execute_tool(tool: dict[str, Any], prompt: str) -> dict[str, Any]:
    name = tool.get("name") or ""
    kind = tool.get("kind") or ""
    t0 = time.perf_counter()
    out: dict[str, Any] = {"ok": False, "kind": kind, "name": name}
    try:
        # Unit AI: try_dispatch 위임 (only 매칭 단일 키)
        if kind == "unit_ai":
            from core.flowi_units.dispatcher import try_dispatch
            res = try_dispatch(prompt or "", only=[name])
            if res and res.get("handled"):
                out["ok"] = True
                out["result_preview"] = _summarize_result(res)
                out["raw_keys"] = sorted(res.keys())
            else:
                out["ok"] = False
                out["result_preview"] = "unit_ai 가 prompt 를 처리하지 않음 (handled=False)"
        elif kind == "function":
            # Function-call 직접 실행은 _run_flowi_chat 의 거대 디스패치 영역.
            # 회귀 위험을 피해 trace 만 남기고 stub 응답.
            out["ok"] = False
            out["result_preview"] = "function-call 은 /api/llm/flowi/chat 경로에서 호출됩니다 (현재는 trace stub)."
        else:
            out["ok"] = False
            out["result_preview"] = f"unknown kind: {kind}"
    except Exception as e:
        out["ok"] = False
        out["result_preview"] = f"{type(e).__name__}: {e}"
        logger.exception("tool exec failed: %s", name)
    out["ms"] = int((time.perf_counter() - t0) * 1000)
    return out


def _summarize_result(res: dict[str, Any]) -> str:
    if not isinstance(res, dict):
        return str(res)[:200]
    for key in ("text", "answer", "message", "summary"):
        v = res.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()[:300]
    keys = [k for k in res.keys() if k != "handled"]
    return f"키: {', '.join(sorted(keys))[:200]}"


def _llm_planner_enabled() -> bool:
    """LLM planner 사용 여부.

    환경변수 `FLOW_LLM_TOOL_CALL=1` 이고 llm_adapter 가 enabled 이어야 활성.
    어느 쪽이든 비활성이면 휴리스틱 fallback. 회귀 위험 0 유지.
    """
    if str(os.environ.get(_LLM_ENV_FLAG, "")).strip() not in ("1", "true", "yes", "on"):
        return False
    try:
        from core import llm_adapter
        return bool(llm_adapter.is_available())
    except Exception:
        return False


def _plan_with_llm(prompt: str, tools: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """LLM 에게 prompt + tool catalog 을 주고 step list(JSON) 를 받아 반환.

    실패하면 None — 호출자는 휴리스틱 fallback 으로 떨어진다.
    """
    try:
        from core import llm_adapter
    except Exception:
        return None
    enabled_tools = [t for t in tools if t.get("enabled")]
    # 도구 카탈로그를 LLM 이 한 번에 보기 좋은 짧은 list 로.
    tool_lines = []
    for t in enabled_tools[:40]:
        ex = ""
        if t.get("examples"):
            first = t["examples"][0]
            ex_prompt = first.get("prompt") if isinstance(first, dict) else None
            if ex_prompt:
                ex = f' | 예: "{str(ex_prompt)[:50]}"'
        tool_lines.append(f"- {t['name']} ({t['kind']}): {t.get('description','')[:120]}{ex}")
    tools_text = "\n".join(tool_lines) or "(empty)"

    system = (
        "You are Flow-i's home agent planner. Pick the minimum set of tools from "
        "the catalog to satisfy the user's request. Respond with strict JSON only."
    )
    user_prompt = (
        f"# 사용자 요청\n{prompt}\n\n"
        f"# 사용 가능한 도구 카탈로그\n{tools_text}\n\n"
        "# 출력 형식 (JSON, 다른 텍스트 금지)\n"
        "{\n"
        '  "steps": [\n'
        '    {"tool": "<카탈로그 name>", "kind": "unit_ai|function", '
        '"input": {"prompt": "<단일 도구로 풀어쓴 자연어>", "product": "<선택>", "max_rows": 12}, '
        '"reason": "<왜 골랐는지 한 줄>"}\n'
        "  ]\n"
        "}\n"
        f"steps 는 최대 {_MAX_STEPS}개. 필요 없으면 빈 배열."
    )
    out = llm_adapter.complete_json(user_prompt, system=system, schema={"keys": ["steps"]})
    if not out.get("ok"):
        logger.info("llm planner failed: %s", out.get("error"))
        return None
    obj = out.get("obj") or {}
    steps = obj.get("steps")
    if not isinstance(steps, list):
        return None
    # 카탈로그에 실제 존재하는 도구만 남김.
    tool_by_name = {t["name"]: t for t in enabled_tools}
    plan: list[dict[str, Any]] = []
    for s in steps[:_MAX_STEPS]:
        if not isinstance(s, dict):
            continue
        name = str(s.get("tool") or "").strip()
        tool = tool_by_name.get(name)
        if not tool:
            continue
        plan.append({
            "tool": tool,
            "input": s.get("input") if isinstance(s.get("input"), dict) else {"prompt": prompt},
            "reason": str(s.get("reason") or "")[:200],
            "source": "llm",
        })
    return plan or None


def _execute_step(tool: dict[str, Any], step_input: dict[str, Any]) -> dict[str, Any]:
    """단일 step 실행. step_input 은 input_schema 기준 dict.

    unit_ai 는 try_dispatch 위임. function-call 은 stub.
    """
    name = tool.get("name") or ""
    kind = tool.get("kind") or ""
    t0 = time.perf_counter()
    out: dict[str, Any] = {"ok": False, "kind": kind, "name": name, "input": step_input}
    try:
        if kind == "unit_ai":
            from core.flowi_units.dispatcher import try_dispatch
            prompt = str(step_input.get("prompt") or "").strip()
            product = str(step_input.get("product") or "")
            try:
                max_rows = int(step_input.get("max_rows") or 12)
            except (TypeError, ValueError):
                max_rows = 12
            res = try_dispatch(prompt, product=product, max_rows=max_rows, only=[name])
            if res and res.get("handled"):
                out["ok"] = True
                out["result"] = res
                out["result_preview"] = _summarize_result(res)
                out["raw_keys"] = sorted(res.keys())
            else:
                out["ok"] = False
                out["result_preview"] = "unit_ai 가 prompt 를 처리하지 않음 (handled=False)"
        elif kind == "function":
            out["ok"] = False
            out["result_preview"] = "function-call 은 /api/llm/flowi/chat 경로에서 호출됩니다 (현재는 trace stub)."
        else:
            out["ok"] = False
            out["result_preview"] = f"unknown kind: {kind}"
    except Exception as e:
        out["ok"] = False
        out["result_preview"] = f"{type(e).__name__}: {e}"
        logger.exception("step exec failed: %s", name)
    out["ms"] = int((time.perf_counter() - t0) * 1000)
    return out


def _plan_from_heuristic(prompt: str, top_k: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """휴리스틱 keyword 매칭 → step list. orchestrate fallback 용."""
    picks, meta = _pick_tools(prompt, top_k=top_k)
    plan: list[dict[str, Any]] = []
    for p in picks:
        plan.append({
            "tool": p["tool"],
            "input": {"prompt": prompt, "product": "", "max_rows": 12},
            "reason": f"휴리스틱 score={round(p['score'], 2)}",
            "source": "heuristic",
            "score": p["score"],
        })
    return plan, meta


def _make_trace_row(step: dict[str, Any], exec_out: dict[str, Any]) -> dict[str, Any]:
    tool = step["tool"]
    score = step.get("score")
    row = {
        "tool": tool["name"],
        "kind": tool["kind"],
        "title": tool["title"],
        "ok": bool(exec_out.get("ok")),
        "ms": exec_out.get("ms", 0),
        "input": step.get("input") or {},
        "result_preview": exec_out.get("result_preview", ""),
        "reason": step.get("reason", ""),
        "source": step.get("source", ""),
    }
    if score is not None:
        row["score"] = round(score, 2)
        row["confidence"] = round(min(1.0, score / 5.0), 2)
    return row


def _synthesize_reply(trace: list[dict[str, Any]]) -> str:
    succ = [tr for tr in trace if tr.get("ok")]
    if succ:
        head = succ[0]
        return f"[{head['title']}] {head['result_preview']}"
    return "도구를 선택했지만 prompt 가 자세하지 않아 결과를 만들지 못했습니다. 트레이스를 참고해 인자를 보완해 주세요."


def _plan_from_alias(prompt: str, tools: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """사용자가 등록한 alias 가 매칭되면 단일 step plan 으로 변환."""
    try:
        from core import agent_feedback
    except Exception:
        return None
    matched = agent_feedback.match_alias(prompt)
    if not matched:
        return None
    name = str(matched.get("tool") or "").strip()
    tool = next((t for t in tools if t.get("name") == name and t.get("enabled")), None)
    if not tool:
        return None
    return [{
        "tool": tool,
        "input": {"prompt": prompt, "product": "", "max_rows": 12},
        "reason": f"사용자 alias 매칭 (pattern='{matched.get('pattern')}')",
        "source": "alias",
    }]


def orchestrate(prompt: str, user: dict[str, Any] | None = None, top_k: int = 2) -> dict[str, Any]:
    """홈 에이전트 메인 엔트리포인트.

    prompt → alias 매칭 → LLM planner (선택) → 휴리스틱 → step 실행 → trace + 응답.
    """
    prompt = str(prompt or "").strip()
    if not prompt:
        return {"ok": False, "error": "빈 prompt", "trace": []}

    tools = tool_registry.list_tools(include_stats=False)
    plan: list[dict[str, Any]] | None = None
    meta: dict[str, Any] = {"planner": "heuristic"}

    plan = _plan_from_alias(prompt, tools)
    if plan:
        meta = {"planner": "alias", "step_count": len(plan)}
    if not plan and _llm_planner_enabled():
        plan = _plan_with_llm(prompt, tools)
        if plan:
            meta = {"planner": "llm", "step_count": len(plan)}
    if not plan:
        plan, hmeta = _plan_from_heuristic(prompt, top_k=top_k)
        meta.update(hmeta)
        meta["planner"] = "heuristic"

    if not plan:
        return {
            "ok": False,
            "prompt": prompt,
            "trace": [],
            "meta": meta,
            "reply": "키워드 매칭으로 적합한 도구를 찾지 못했습니다. AI 허브에서 도구를 활성화하거나 더 구체적인 단어를 사용해 주세요.",
        }

    trace: list[dict[str, Any]] = []
    accumulated: dict[str, Any] = {}
    for step in plan[:_MAX_STEPS]:
        merged_input = {**step.get("input", {}), **{k: v for k, v in accumulated.items() if k not in (step.get("input") or {})}}
        if "prompt" not in merged_input:
            merged_input["prompt"] = prompt
        step["input"] = merged_input
        exec_out = _execute_step(step["tool"], merged_input)
        trace.append(_make_trace_row(step, exec_out))
        # 다음 step input 에 사용할 수 있도록 product 같은 단순 값 누적.
        if exec_out.get("ok") and isinstance(exec_out.get("result"), dict):
            res = exec_out["result"]
            for key in ("product", "lot_id", "root_lot_id", "wafer_id"):
                v = res.get(key)
                if isinstance(v, str) and v and key not in accumulated:
                    accumulated[key] = v

    return {
        "ok": True,
        "prompt": prompt,
        "trace": trace,
        "meta": meta,
        "reply": _synthesize_reply(trace),
        "picked_count": len(trace),
    }


def orchestrate_stream(
    prompt: str,
    user: dict[str, Any] | None = None,
    top_k: int = 2,
) -> Iterator[dict[str, Any]]:
    """SSE 용 generator. orchestrate 와 동일하지만 step 별로 event 를 yield 한다.

    소비 측 (FastAPI StreamingResponse)은 각 dict 를 `event: <type>\\ndata: <json>\\n\\n`
    으로 직렬화한다.
    """
    prompt = str(prompt or "").strip()
    if not prompt:
        yield {"type": "reply", "ok": False, "error": "빈 prompt", "trace": []}
        return

    tools = tool_registry.list_tools(include_stats=False)
    plan: list[dict[str, Any]] | None = None
    meta: dict[str, Any] = {"planner": "heuristic"}
    plan = _plan_from_alias(prompt, tools)
    if plan:
        meta = {"planner": "alias", "step_count": len(plan)}
    if not plan and _llm_planner_enabled():
        plan = _plan_with_llm(prompt, tools)
        if plan:
            meta = {"planner": "llm", "step_count": len(plan)}
    if not plan:
        plan, hmeta = _plan_from_heuristic(prompt, top_k=top_k)
        meta.update(hmeta)
        meta["planner"] = "heuristic"

    yield {
        "type": "plan",
        "meta": meta,
        "steps": [
            {
                "tool": s["tool"]["name"],
                "kind": s["tool"]["kind"],
                "title": s["tool"]["title"],
                "input": s.get("input") or {},
                "reason": s.get("reason", ""),
            }
            for s in (plan or [])
        ],
    }

    if not plan:
        yield {
            "type": "reply",
            "ok": False,
            "trace": [],
            "reply": "키워드 매칭으로 적합한 도구를 찾지 못했습니다.",
        }
        return

    trace: list[dict[str, Any]] = []
    accumulated: dict[str, Any] = {}
    for idx, step in enumerate(plan[:_MAX_STEPS]):
        merged_input = {**step.get("input", {}), **{k: v for k, v in accumulated.items() if k not in (step.get("input") or {})}}
        if "prompt" not in merged_input:
            merged_input["prompt"] = prompt
        step["input"] = merged_input
        yield {
            "type": "step_start",
            "index": idx,
            "tool": step["tool"]["name"],
            "kind": step["tool"]["kind"],
            "input": merged_input,
        }
        exec_out = _execute_step(step["tool"], merged_input)
        row = _make_trace_row(step, exec_out)
        trace.append(row)
        yield {
            "type": "step_end",
            "index": idx,
            "tool": step["tool"]["name"],
            "ok": row["ok"],
            "ms": row["ms"],
            "result_preview": row["result_preview"],
        }
        if exec_out.get("ok") and isinstance(exec_out.get("result"), dict):
            res = exec_out["result"]
            for key in ("product", "lot_id", "root_lot_id", "wafer_id"):
                v = res.get(key)
                if isinstance(v, str) and v and key not in accumulated:
                    accumulated[key] = v

    yield {
        "type": "reply",
        "ok": True,
        "trace": trace,
        "reply": _synthesize_reply(trace),
        "picked_count": len(trace),
    }
