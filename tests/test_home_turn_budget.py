"""홈 요청 스코프 LLM 예산 + 응답 usage/evidence 첨부 검증."""
import pytest

from core import home_orchestrator, llm_usage


@pytest.fixture
def no_llm_no_tools(monkeypatch):
    monkeypatch.setattr(home_orchestrator, "_react_loop_enabled", lambda: False)
    monkeypatch.setattr(home_orchestrator, "_llm_planner_enabled", lambda: False)
    monkeypatch.setattr(home_orchestrator, "_semantic_frame_for_prompt", lambda prompt: {})
    monkeypatch.setattr(home_orchestrator.tool_registry, "list_tools", lambda **kwargs: [])
    monkeypatch.setattr(
        home_orchestrator, "build_home_runtime_snapshot",
        lambda **kwargs: {"run_id": "test", "graph": {}, "action_log": {}, "status": "ok"},
    )
    monkeypatch.setattr(home_orchestrator.home_memory, "remember_turn", lambda **kwargs: None)


def test_home_turn_limit_defaults_and_env(monkeypatch):
    monkeypatch.delenv("FLOW_HOME_TURN_LLM_LIMIT", raising=False)
    monkeypatch.delenv("FLOW_HOME_TURN_LLM_ADMIN_LIMIT", raising=False)
    assert home_orchestrator.home_turn_limit("user") == 20
    assert home_orchestrator.home_turn_limit("admin") == 24
    monkeypatch.setenv("FLOW_HOME_TURN_LLM_LIMIT", "7")
    assert home_orchestrator.home_turn_limit("user") == 7
    monkeypatch.setenv("FLOW_HOME_TURN_LLM_LIMIT", "999")
    assert home_orchestrator.home_turn_limit("user") == 25
    monkeypatch.setenv("FLOW_HOME_TURN_LLM_LIMIT", "bogus")
    assert home_orchestrator.home_turn_limit("user") == 20


def test_turn_budget_blocks_over_limit():
    with llm_usage.turn_budget(1):
        assert llm_usage.reserve_attempt() == ""
        blocked = llm_usage.reserve_attempt()
        assert "call limit" in blocked


def test_orchestrate_attaches_usage_evidence_semantic(no_llm_no_tools):
    out = home_orchestrator.orchestrate("스플릿 보여줘", user={"username": "t", "role": "user"})
    assert out["usage"]["llm_calls_used"] == 0
    assert out["usage"]["llm_call_limit"] == 20
    assert out["usage"]["minute_call_limit"] == 25
    assert out["usage"]["turn_exhausted"] is False
    assert set(out["meta"]["semantic_summary"]) == {
        "resolved_columns", "alias_hits", "slot_hints", "unknown_terms",
        "value_catalog_matches", "value_terms", "intent_matches",
    }
    assert out["evidence"]["steps"] == []
    assert out["evidence"]["semantic"] == out["meta"]["semantic_summary"]


def test_orchestrate_respects_env_limit(no_llm_no_tools, monkeypatch):
    monkeypatch.setenv("FLOW_HOME_TURN_LLM_LIMIT", "5")
    out = home_orchestrator.orchestrate("스플릿 보여줘", user={"username": "t", "role": "user"})
    assert out["usage"]["llm_call_limit"] == 5


def test_orchestrate_counts_provider_calls(no_llm_no_tools):
    with llm_usage.turn_budget(home_orchestrator.home_turn_limit("user")):
        before = home_orchestrator.home_usage_block()["llm_calls_used"]
        assert llm_usage.reserve_attempt() == ""
        assert llm_usage.reserve_attempt() == ""
        usage = home_orchestrator.home_usage_block()
    assert usage["llm_calls_used"] == before + 2
    assert usage["minute_calls_used"] == 2


def test_stream_final_has_usage(no_llm_no_tools):
    events = list(home_orchestrator.orchestrate_stream("스플릿 보여줘", user={"username": "t"}))
    final = events[-1]
    assert final["usage"]["llm_call_limit"] == 20
    assert final["evidence"]["steps"] == []
