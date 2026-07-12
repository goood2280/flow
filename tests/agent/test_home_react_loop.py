"""tests/agent/test_home_react_loop.py — Flow-i 반복 ReAct 루프 검증.

Phase 1 (반복 루프) 단위 테스트. 실제 LLM 없이 헬퍼/제너레이터/배선을
monkeypatch 로 검증한다. 기본 off 상태에서 기존 단일 패스 경로가 그대로인지도 확인한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

import pytest  # noqa: E402

from core import agent_semantic_service, home_orchestrator, llm_adapter  # noqa: E402


def _tool(name, kind="function", enabled=True, **extra):
    return {"name": name, "kind": kind, "title": name, "enabled": enabled, "description": name, **extra}


def _call_tool_decision(tool, prompt_text, reason="r"):
    return {"action": "call_tool", "tool": tool, "tool_name": tool["name"],
            "input": {"prompt": prompt_text}, "reason": reason}


def _final_decision(answer, reason="done"):
    return {"action": "final", "answer": answer, "reason": reason}


def _scripted(decisions):
    seq = iter(decisions)

    def decide(**_kwargs):
        try:
            return next(seq)
        except StopIteration:
            return _final_decision("auto-final")

    return decide


def _ok_exec(tool, step_input, **_kwargs):
    return {"ok": True, "status": "success", "result_preview": "ok", "warnings": [], "result": {}}


# --- C1: react 게이트 / 상한 ------------------------------------------------

def test_react_loop_disabled_by_default(monkeypatch):
    monkeypatch.delenv(home_orchestrator._REACT_ENV_FLAG, raising=False)
    # 운영 설정과 무관하게 "플래그 off ⇒ 비활성" 계약만 검증 (hermetic).
    monkeypatch.setattr(home_orchestrator, "_agentic_settings_flag", lambda _name: False)
    assert home_orchestrator._react_loop_enabled() is False


def test_react_loop_requires_planner_even_with_flag(monkeypatch):
    # react flag 만 켜고 planner(=FLOW_LLM_TOOL_CALL + adapter) 가 꺼져 있으면 off.
    monkeypatch.setenv(home_orchestrator._REACT_ENV_FLAG, "1")
    monkeypatch.setattr(home_orchestrator, "_llm_planner_enabled", lambda: False)
    assert home_orchestrator._react_loop_enabled() is False


def test_react_loop_enabled_when_flag_and_planner(monkeypatch):
    monkeypatch.setenv(home_orchestrator._REACT_ENV_FLAG, "1")
    monkeypatch.setattr(home_orchestrator, "_llm_planner_enabled", lambda: True)
    assert home_orchestrator._react_loop_enabled() is True


def test_react_max_iters_default_and_clamp(monkeypatch):
    # 기본/상한 8 — 오케스트레이션 턴을 최대 8에서 끊는 운영 정책.
    monkeypatch.delenv(home_orchestrator._REACT_MAX_ITERS_ENV, raising=False)
    assert home_orchestrator._react_max_iters() == 8
    monkeypatch.setenv(home_orchestrator._REACT_MAX_ITERS_ENV, "3")
    assert home_orchestrator._react_max_iters() == 3
    monkeypatch.setenv(home_orchestrator._REACT_MAX_ITERS_ENV, "99")
    assert home_orchestrator._react_max_iters() == 8
    monkeypatch.setenv(home_orchestrator._REACT_MAX_ITERS_ENV, "0")
    assert home_orchestrator._react_max_iters() == 1
    monkeypatch.setenv(home_orchestrator._REACT_MAX_ITERS_ENV, "garbage")
    assert home_orchestrator._react_max_iters() == 8


def test_react_deadline_default_and_clamp(monkeypatch):
    monkeypatch.delenv(home_orchestrator._REACT_DEADLINE_ENV, raising=False)
    assert home_orchestrator._react_deadline_seconds() == home_orchestrator._REACT_DEFAULT_DEADLINE_S
    monkeypatch.setenv(home_orchestrator._REACT_DEADLINE_ENV, "12")
    assert home_orchestrator._react_deadline_seconds() == 15
    monkeypatch.setenv(home_orchestrator._REACT_DEADLINE_ENV, "999")
    assert home_orchestrator._react_deadline_seconds() == 110


# --- C1: semantic frame 헬퍼 -----------------------------------------------

def test_semantic_frame_for_prompt_swallows_errors(monkeypatch):
    def boom(_prompt):
        raise RuntimeError("resolve down")

    monkeypatch.setattr(agent_semantic_service, "resolve", boom)
    assert home_orchestrator._semantic_frame_for_prompt("아무 질문") == {}


def test_semantic_frame_for_prompt_returns_frame(monkeypatch):
    monkeypatch.setattr(agent_semantic_service, "resolve", lambda _prompt: {"alias_hits": []})
    assert home_orchestrator._semantic_frame_for_prompt("질문") == {"alias_hits": []}


def test_semantic_frame_summary_is_compact_and_safe():
    frame = {
        "natural_language": "PRODA lot A1000 IOFF 분포 보여줘",
        "resolved_columns": [f"col_{i}" for i in range(40)],
        "alias_hits": [
            {"canonical": "product", "alias": "PRODA"},
            {"canonical": "product", "alias": "제품"},  # dup canonical
            {"canonical": "lot_id", "alias": "lot"},
            "not-a-dict",
        ],
        "slot_hints": {"product": "PRODA", "lot_id": "A1000", "snapshot_custom_cols": ["KNOB_X", "KNOB_Y"]},
        "unknown_terms": [f"term_{i}" for i in range(30)],
        "value_terms": [f"val_{i}" for i in range(30)],
        "intent_matches": {"inform_registration": ["product", "lot_id"]},
        "warnings": ["Unmapped semantic terms: foo"],
    }
    summary = home_orchestrator._semantic_frame_summary(frame)

    # canonical dedupe + 캡
    assert summary["alias_hits"] == ["product", "lot_id"]
    # 캡 적용
    assert len(summary["resolved_columns"]) <= 20
    assert len(summary["unknown_terms"]) <= 12
    assert len(summary["value_terms"]) <= 12
    # slot list 값은 string list 로
    assert summary["slot_hints"]["snapshot_custom_cols"] == ["KNOB_X", "KNOB_Y"]
    assert summary["slot_hints"]["product"] == "PRODA"
    # intent 는 key 목록
    assert summary["intent_matches"] == ["inform_registration"]
    # 내부 추론/원문(natural_language, warnings)은 노출하지 않음
    assert "natural_language" not in summary
    assert "warnings" not in summary


def test_semantic_frame_summary_handles_non_dict():
    assert home_orchestrator._semantic_frame_summary(None) == {}
    assert home_orchestrator._semantic_frame_summary([1, 2, 3]) == {}


# --- C2: 카탈로그 포맷 / 관찰 요약 / 한 턴 결정 -----------------------------

def test_format_tool_catalog_format():
    tools = [
        _tool("filebrowser_ai_sql", kind="unit_ai", description="SQL 작업대", examples=[{"prompt": "PRODA 이상치"}]),
        _tool("send_mail", description="메일 발송"),
    ]
    text = home_orchestrator._format_tool_catalog(tools)
    assert "- filebrowser_ai_sql (unit_ai): SQL 작업대" in text
    assert '예: "PRODA 이상치"' in text
    assert "- send_mail (function): 메일 발송" in text


def test_format_tool_catalog_empty():
    assert home_orchestrator._format_tool_catalog([]) == "(empty)"


def test_observation_summary_compact():
    trace = [
        {"tool": "filebrowser_ai_sql", "ok": True, "status": "success",
         "result_preview": "x" * 999, "warnings": ["w1", "w2", "w3", "w4"]},
        {"tool": "dashboard_agent", "ok": False, "result_preview": None, "warnings": []},
        "not-a-row",
    ]
    obs = home_orchestrator._observation_summary(trace)
    assert len(obs) == 2
    assert obs[0]["tool"] == "filebrowser_ai_sql"
    assert obs[0]["status"] == "success"
    assert len(obs[0]["result_preview"]) <= 400
    assert obs[0]["warnings"] == ["w1", "w2", "w3"]  # capped at 3
    assert obs[1]["status"] == "failed"  # derived from ok=False


def test_decide_returns_call_tool_when_model_picks_tool(monkeypatch):
    tools = [_tool("send_mail", description="메일")]
    calls = []

    def fake_complete_json(*_args, **kwargs):
        calls.append(kwargs)
        return {
            "ok": True,
            "obj": {"action": "call_tool", "tool": "send_mail", "input": {"prompt": "보내줘"},
                    "reason": "메일 요청", "thought": "내부추론"},
        }

    monkeypatch.setattr(llm_adapter, "complete_json", fake_complete_json)
    decision = home_orchestrator._decide_next_action(
        prompt="메일 보내줘", tools=tools, semantic_summary={}, observations=[],
        step_index=0, max_steps=6,
    )
    assert decision["action"] == "call_tool"
    assert decision["tool"]["name"] == "send_mail"
    assert decision["input"] == {"prompt": "보내줘"}
    assert decision["reason"] == "메일 요청"
    assert "thought" not in decision  # 내부 추론은 공개하지 않음
    assert calls[0]["timeout"] == home_orchestrator._REACT_DECISION_TIMEOUT_S


def test_decide_coerces_to_final_when_tool_unknown(monkeypatch):
    tools = [_tool("send_mail")]
    monkeypatch.setattr(llm_adapter, "complete_json", lambda *a, **k: {
        "ok": True,
        "obj": {"action": "call_tool", "tool": "does_not_exist", "answer": "대신 답", "reason": "r"},
    })
    decision = home_orchestrator._decide_next_action(
        prompt="x", tools=tools, semantic_summary={}, observations=[], step_index=1, max_steps=6,
    )
    assert decision["action"] == "final"
    assert decision["answer"] == "대신 답"


def test_decide_returns_none_when_llm_fails(monkeypatch):
    monkeypatch.setattr(llm_adapter, "complete_json", lambda *a, **k: {"ok": False, "error": "down"})
    decision = home_orchestrator._decide_next_action(
        prompt="x", tools=[_tool("send_mail")], semantic_summary={}, observations=[],
        step_index=0, max_steps=6,
    )
    assert decision is None


def test_decide_defaults_to_final(monkeypatch):
    monkeypatch.setattr(llm_adapter, "complete_json", lambda *a, **k: {
        "ok": True, "obj": {"action": "final", "answer": "결론", "reason": "충분"},
    })
    decision = home_orchestrator._decide_next_action(
        prompt="x", tools=[_tool("send_mail")], semantic_summary={"alias_hits": ["product"]},
        observations=[{"tool": "send_mail", "status": "success"}], step_index=2, max_steps=6,
    )
    assert decision["action"] == "final"
    assert decision["answer"] == "결론"


# --- C3: 반복 루프 제너레이터 / 종료 가드 / compose -------------------------

def test_loop_iterates_multiple_steps_then_finalizes(monkeypatch):
    t1 = _tool("send_mail")
    t2 = _tool("dashboard_agent", kind="unit_ai")
    monkeypatch.setattr(home_orchestrator, "_decide_next_action", _scripted([
        _call_tool_decision(t1, "step0"),
        _call_tool_decision(t2, "step1"),
        _final_decision("최종결론"),
    ]))
    monkeypatch.setattr(home_orchestrator, "_execute_step", _ok_exec)
    events = list(home_orchestrator._run_react_loop(
        prompt="goal", tools=[t1, t2], semantic_summary={}, user=None, request=None, max_steps=6))
    step_ends = [e for e in events if e["kind"] == "step_end"]
    final = events[-1]
    assert len(step_ends) == 2
    assert final["kind"] == "final"
    assert len(final["trace"]) == 2
    assert final["reply"] == "최종결론"
    assert final["stop_reason"] == "model_final"


def test_loop_stops_on_model_final_first_turn(monkeypatch):
    monkeypatch.setattr(home_orchestrator, "_decide_next_action", _scripted([_final_decision("즉시답")]))
    monkeypatch.setattr(home_orchestrator, "_execute_step",
                        lambda *a, **k: pytest.fail("should not execute on first-turn final"))
    events = list(home_orchestrator._run_react_loop(
        prompt="g", tools=[_tool("x")], semantic_summary={}, user=None, request=None, max_steps=6))
    assert [e for e in events if e["kind"] == "step_end"] == []
    final = events[-1]
    assert final["trace"] == []
    assert final["reply"] == "즉시답"
    assert final["stop_reason"] == "model_final"


def test_loop_respects_max_steps(monkeypatch):
    t = _tool("loop_tool")
    counter = {"n": 0}

    def decide(**_kwargs):
        counter["n"] += 1
        return _call_tool_decision(t, f"step{counter['n']}")  # unique input → avoid repeated guard

    monkeypatch.setattr(home_orchestrator, "_decide_next_action", decide)
    monkeypatch.setattr(home_orchestrator, "_execute_step", _ok_exec)
    events = list(home_orchestrator._run_react_loop(
        prompt="g", tools=[t], semantic_summary={}, user=None, request=None, max_steps=3))
    assert len([e for e in events if e["kind"] == "step_end"]) == 3
    assert events[-1]["stop_reason"] == "max_steps"


def test_repeated_action_guard_stops(monkeypatch):
    t = _tool("loop_tool")
    monkeypatch.setattr(home_orchestrator, "_decide_next_action",
                        lambda **k: _call_tool_decision(t, "same"))  # identical input always
    monkeypatch.setattr(home_orchestrator, "_execute_step", _ok_exec)
    events = list(home_orchestrator._run_react_loop(
        prompt="g", tools=[t], semantic_summary={}, user=None, request=None, max_steps=6))
    assert len([e for e in events if e["kind"] == "step_end"]) == 1
    assert events[-1]["stop_reason"] == "repeated_action"


def test_no_progress_guard_stops(monkeypatch):
    t = _tool("loop_tool")
    counter = {"n": 0}

    def decide(**_kwargs):
        counter["n"] += 1
        return _call_tool_decision(t, f"diff{counter['n']}")  # unique → avoid repeated guard

    monkeypatch.setattr(home_orchestrator, "_decide_next_action", decide)
    monkeypatch.setattr(home_orchestrator, "_execute_step",
                        lambda tool, step_input, **k: {"ok": False, "status": "failed",
                                                       "result_preview": "", "warnings": ["fail"]})
    events = list(home_orchestrator._run_react_loop(
        prompt="g", tools=[t], semantic_summary={}, user=None, request=None, max_steps=6))
    assert len([e for e in events if e["kind"] == "step_end"]) == 2
    assert events[-1]["stop_reason"] == "no_progress"


def test_loop_stops_on_blocked(monkeypatch):
    t = _tool("loop_tool")
    monkeypatch.setattr(home_orchestrator, "_decide_next_action",
                        _scripted([_call_tool_decision(t, "s0"), _call_tool_decision(t, "s1")]))
    monkeypatch.setattr(home_orchestrator, "_execute_step",
                        lambda tool, step_input, **k: {"ok": False, "blocked": True, "status": "blocked",
                                                       "result_preview": "need input", "warnings": []})
    events = list(home_orchestrator._run_react_loop(
        prompt="g", tools=[t], semantic_summary={}, user=None, request=None, max_steps=6))
    assert len([e for e in events if e["kind"] == "step_end"]) == 1
    assert events[-1]["stop_reason"] == "blocked"


def test_loop_stops_on_llm_error(monkeypatch):
    monkeypatch.setattr(home_orchestrator, "_decide_next_action", lambda **k: None)
    monkeypatch.setattr(home_orchestrator, "_execute_step",
                        lambda *a, **k: pytest.fail("should not execute on llm error"))
    monkeypatch.setattr(home_orchestrator, "_react_loop_enabled", lambda: False)
    events = list(home_orchestrator._run_react_loop(
        prompt="g", tools=[_tool("x")], semantic_summary={}, user=None, request=None, max_steps=6))
    final = events[-1]
    assert [e for e in events if e["kind"] == "step_end"] == []
    assert final["trace"] == []
    assert final["stop_reason"] == "llm_error"
    assert isinstance(final["reply"], str)


def test_compose_prefers_model_answer(monkeypatch):
    monkeypatch.setattr(home_orchestrator, "_react_loop_enabled",
                        lambda: pytest.fail("should not consult flag when model answer present"))
    assert home_orchestrator._compose_final_reply("g", [], "직접답", {}) == "직접답"


def test_compose_uses_synthesize_reply_fallback(monkeypatch):
    monkeypatch.setattr(home_orchestrator, "_react_loop_enabled", lambda: False)
    trace = [{"title": "FB", "ok": True, "result_preview": "미리보기", "blocked": False}]
    assert home_orchestrator._compose_final_reply("g", trace, "", {}) == "[FB] 미리보기"


def test_action_signature_distinguishes_inputs():
    s1 = home_orchestrator._action_signature("t", {"prompt": "a", "max_rows": 12})
    s2 = home_orchestrator._action_signature("t", {"prompt": "b", "max_rows": 12})
    s3 = home_orchestrator._action_signature("t", {"prompt": "a", "max_rows": 12, "ignored": "x"})
    assert s1 != s2
    assert s1 == s3  # 의미 외 키는 서명에 영향 없음


# --- C4: orchestrate / orchestrate_stream 배선 -----------------------------

def _wire_react(monkeypatch, tool, decisions):
    from core import home_memory
    monkeypatch.setattr(home_memory, "is_memory_recall_prompt", lambda _p: False)
    monkeypatch.setattr(home_orchestrator.tool_registry, "list_tools", lambda include_stats=False: [tool])
    monkeypatch.setattr(home_orchestrator, "_react_loop_enabled", lambda: True)
    monkeypatch.setattr(home_orchestrator, "_react_max_iters", lambda: 6)
    monkeypatch.setattr(home_orchestrator, "_semantic_frame_for_prompt", lambda _p: {"alias_hits": []})
    monkeypatch.setattr(home_orchestrator, "_decide_next_action", _scripted(decisions))
    monkeypatch.setattr(home_orchestrator, "_execute_step", _ok_exec)
    monkeypatch.setattr(home_orchestrator, "_attach_runtime_result", lambda out, **k: out)


def test_orchestrate_uses_react_loop_when_enabled(monkeypatch):
    t = _tool("send_mail")
    _wire_react(monkeypatch, t, [_call_tool_decision(t, "보내"), _final_decision("메일 초안 작성됨")])
    out = home_orchestrator.orchestrate("메일 보내줘", user={"username": "t"})
    assert out["ok"] is True
    assert out["meta"]["planner"] == "react"
    assert out["meta"]["stop_reason"] == "model_final"
    assert out["reply"] == "메일 초안 작성됨"
    assert out["picked_count"] == 1
    assert [r["tool"] for r in out["trace"]] == ["send_mail"]
    assert {"ok", "prompt", "trace", "tool_calls", "meta", "reply", "picked_count"}.issubset(out.keys())


def test_attach_runtime_result_exposes_answer_alias(monkeypatch):
    monkeypatch.setattr(
        home_orchestrator,
        "build_home_runtime_snapshot",
        lambda **_kwargs: {"run_id": "pytest-run", "graph": {"nodes": [], "edges": []}, "status": "success", "action_log": {}},
    )
    monkeypatch.setattr(home_orchestrator.home_memory, "remember_turn", lambda **_kwargs: None)

    out = home_orchestrator._attach_runtime_result(
        {"ok": True, "prompt": "p", "trace": [], "reply": "결론"},
        prompt="p",
        user={"username": "t"},
    )

    assert out["answer"] == "결론"
    assert out["reply"] == "결론"


def test_orchestrate_falls_back_to_heuristic_when_llm_errors(monkeypatch):
    from core import home_memory
    t = _tool("send_mail")
    monkeypatch.setattr(home_memory, "is_memory_recall_prompt", lambda _p: False)
    monkeypatch.setattr(home_orchestrator.tool_registry, "list_tools", lambda include_stats=False: [t])
    monkeypatch.setattr(home_orchestrator, "_react_loop_enabled", lambda: True)
    monkeypatch.setattr(home_orchestrator, "_react_max_iters", lambda: 6)
    monkeypatch.setattr(home_orchestrator, "_semantic_frame_for_prompt", lambda _p: {})
    monkeypatch.setattr(home_orchestrator, "_decide_next_action", lambda **k: None)  # llm_error, empty trace
    monkeypatch.setattr(home_orchestrator, "_plan_from_alias", lambda _p, _t: None)
    fb_plan = [{"tool": t, "input": {"prompt": "x"}, "reason": "h", "source": "heuristic"}]
    monkeypatch.setattr(home_orchestrator, "_plan_from_heuristic", lambda _p, top_k=2: (fb_plan, {"signals": ["mail"]}))
    monkeypatch.setattr(home_orchestrator, "_execute_step", _ok_exec)
    monkeypatch.setattr(home_orchestrator, "_attach_runtime_result", lambda out, **k: out)
    out = home_orchestrator.orchestrate("메일 보내줘", user={"username": "t"})
    assert out["ok"] is True
    assert out["meta"]["planner"] == "heuristic"  # react llm_error → 기존 경로로 degrade
    assert [r["tool"] for r in out["trace"]] == ["send_mail"]


def test_orchestrate_stream_emits_plan_and_reply_with_react(monkeypatch):
    t = _tool("send_mail")
    _wire_react(monkeypatch, t, [_call_tool_decision(t, "보내"), _final_decision("끝")])
    events = list(home_orchestrator.orchestrate_stream("메일 보내줘", user={"username": "t"}))
    types = [e.get("type") for e in events]
    assert types.count("plan") == 1
    assert types.count("reply") == 1
    assert types.count("step_start") == 1
    assert types.count("step_end") == 1
    reply = next(e for e in events if e.get("type") == "reply")
    assert reply["picked_count"] == 1
    assert "trace" in reply and "meta" in reply
    assert reply["meta"]["planner"] == "react"


# --- C5: snapshot/graph 반복 노드 (additive) -------------------------------

def _react_result():
    return {
        "ok": True,
        "prompt": "goal",
        "trace": [
            {"tool": "filebrowser_ai_sql", "kind": "unit_ai", "title": "FB", "ok": True,
             "status": "success", "ms": 12, "result_preview": "rows=3", "warnings": [], "reason": "sql",
             "input": {"prompt": "x"}},
            {"tool": "dashboard_agent", "kind": "unit_ai", "title": "Dash", "ok": True,
             "status": "success", "ms": 8, "result_preview": "chart", "warnings": [], "reason": "chart",
             "input": {"prompt": "y"}},
        ],
        "tool_calls": [],
        "meta": {"planner": "react", "step_count": 2, "stop_reason": "model_final",
                 "semantic_summary": {"alias_hits": ["product"]}},
        "reply": "결론",
        "picked_count": 2,
    }


def test_graph_unchanged_without_iterations():
    graph = home_orchestrator.build_home_runtime_graph()
    node_ids = {n["id"] for n in graph["nodes"]}
    assert not any(nid.startswith("iter:") for nid in node_ids)
    assert {"prompt_input", "semantic_layer", "orchestrator", "result_renderer"}.issubset(node_ids)


def test_snapshot_includes_iteration_nodes_for_react_run():
    snap = home_orchestrator.build_home_runtime_snapshot(prompt="goal", result=_react_result(), save=False)
    node_ids = {n["id"] for n in snap["graph"]["nodes"]}
    assert "iter:0:filebrowser_ai_sql" in node_ids
    assert "iter:1:dashboard_agent" in node_ids
    edges = snap["graph"]["edges"]
    assert {"source": "orchestrator", "target": "iter:0:filebrowser_ai_sql"} in edges
    assert {"source": "iter:0:filebrowser_ai_sql", "target": "iter:1:dashboard_agent"} in edges
    assert {"source": "iter:1:dashboard_agent", "target": "result_renderer"} in edges
    # node_details 에 iter 항목과 result_preview
    assert snap["node_details"]["iter:0:filebrowser_ai_sql"]["output_summary"]["result_preview"] == "rows=3"
    # additive 최상위 필드
    assert len(snap["iterations"]) == 2
    assert snap["stop_reason"] == "model_final"
    assert snap["semantic_frame"] == {"alias_hits": ["product"]}
    # 고정 노드는 유지
    assert {"prompt_input", "semantic_layer", "orchestrator", "result_renderer"}.issubset(node_ids)


def test_snapshot_shape_unchanged_for_non_react_run():
    non_react = {
        "ok": True, "prompt": "g",
        "trace": [{"tool": "send_mail", "kind": "function", "title": "Mail", "ok": True,
                   "status": "success", "ms": 1, "result_preview": "ok", "warnings": []}],
        "meta": {"planner": "heuristic", "signals": ["mail"]},
        "reply": "done", "picked_count": 1,
    }
    snap = home_orchestrator.build_home_runtime_snapshot(prompt="g", result=non_react, save=False)
    node_ids = {n["id"] for n in snap["graph"]["nodes"]}
    assert not any(nid.startswith("iter:") for nid in node_ids)
    assert "iterations" not in snap
    assert "stop_reason" not in snap
    assert "semantic_frame" not in snap


# --- C6: semantic frame 노출 (stream + snapshot node detail) ----------------

def test_snapshot_semantic_node_shows_frame_for_react():
    snap = home_orchestrator.build_home_runtime_snapshot(prompt="goal", result=_react_result(), save=False)
    semantic = snap["node_details"]["semantic_layer"]["output_summary"]["semantic"]
    assert semantic == {"alias_hits": ["product"]}


def test_snapshot_semantic_node_empty_for_non_react():
    non_react = {
        "ok": True, "prompt": "g",
        "trace": [{"tool": "send_mail", "kind": "function", "title": "Mail", "ok": True,
                   "status": "success", "ms": 1, "result_preview": "ok", "warnings": []}],
        "meta": {"planner": "heuristic"},
        "reply": "done", "picked_count": 1,
    }
    snap = home_orchestrator.build_home_runtime_snapshot(prompt="g", result=non_react, save=False)
    assert snap["node_details"]["semantic_layer"]["output_summary"]["semantic"] == {}


def test_stream_semantic_event_carries_frame_when_react(monkeypatch):
    t = _tool("send_mail")
    _wire_react(monkeypatch, t, [_final_decision("끝")])
    monkeypatch.setattr(home_orchestrator, "_semantic_frame_for_prompt",
                        lambda _p: {"alias_hits": [{"canonical": "product", "alias": "PRODA"}],
                                    "resolved_columns": ["IOFF"]})
    events = list(home_orchestrator.orchestrate_stream("PRODA IOFF", user={"username": "t"}))
    sem_event = next(e for e in events if e.get("stage") == "semantic_layer")
    assert "semantic" in sem_event
    assert sem_event["semantic"]["alias_hits"] == ["product"]
    assert sem_event["semantic"]["resolved_columns"] == ["IOFF"]
