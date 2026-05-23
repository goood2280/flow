"""core/home_orchestrator.py — 홈 에이전트 오케스트레이터.

자연어 prompt 를 받아 ToolRegistry 의 도구 중 적합한 것을 1~N개 골라
순차 실행하고, 트레이스(어떤 도구를 왜 골랐는지)를 응답에 포함한다.

선택 전략 (우선순위):
  1. LLM function-calling — llm_adapter.complete_with_tools 가 있으면 사용
     (현재 어댑터 미보유 시 자동 건너뜀)
  2. 휴리스틱 dispatcher — prompt 의 한국어/영어 키워드를 태그/이름에 매칭
     해 도구 후보 가중치 산정 → 상위 1~2개 선택

실행:
  - sql_workspace 계열은 직접 실행 가능 (현재 미파라미터)
  - flowi_units 의 unit_ai 는 try_dispatch 위임 (기존 동작)
  - function-call 단위는 _flowi_function_schema 기반 stub trace 만 제공
    (실제 실행은 기존 flowi/chat 경로로 우회)

트레이스 포맷:
  [{"tool": name, "kind": "unit_ai"|"function"|"sql_workspace",
    "matched_terms": [...], "confidence": 0.0~1.0,
    "ok": bool, "ms": int, "result_preview": "..."}]
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from core import tool_registry

logger = logging.getLogger("flow.home_orchestrator")


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


def orchestrate(prompt: str, user: dict[str, Any] | None = None, top_k: int = 2) -> dict[str, Any]:
    """홈 에이전트 메인 엔트리포인트.

    prompt → 도구 후보 산정 → 상위 top_k 실행 → trace + 최종 응답.
    """
    prompt = str(prompt or "").strip()
    if not prompt:
        return {"ok": False, "error": "빈 prompt", "trace": []}

    picks, meta = _pick_tools(prompt, top_k=top_k)
    trace: list[dict[str, Any]] = []

    if not picks:
        return {
            "ok": False,
            "prompt": prompt,
            "trace": [],
            "meta": meta,
            "reply": "키워드 매칭으로 적합한 도구를 찾지 못했습니다. AI 허브에서 도구를 활성화하거나 더 구체적인 단어를 사용해 주세요.",
        }

    for p in picks:
        tool = p["tool"]
        exec_out = _execute_tool(tool, prompt)
        trace.append({
            "tool": tool["name"],
            "kind": tool["kind"],
            "title": tool["title"],
            "score": round(p["score"], 2),
            "confidence": round(min(1.0, p["score"] / 5.0), 2),
            "ok": bool(exec_out.get("ok")),
            "ms": exec_out.get("ms", 0),
            "result_preview": exec_out.get("result_preview", ""),
        })

    # 응답 합성 — 가장 confident 한 도구의 result_preview 우선.
    succ = [tr for tr in trace if tr["ok"]]
    if succ:
        head = succ[0]
        reply = f"[{head['title']}] {head['result_preview']}"
    else:
        reply = "도구를 선택했지만 prompt 가 자세하지 않아 결과를 만들지 못했습니다. 트레이스를 참고해 인자를 보완해 주세요."

    return {
        "ok": True,
        "prompt": prompt,
        "trace": trace,
        "meta": meta,
        "reply": reply,
        "picked_count": len(trace),
    }
