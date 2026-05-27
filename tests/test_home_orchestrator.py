"""tests/test_home_orchestrator.py — Step 4 휴리스틱 디스패처 검증.

LLM 의존성 없이 키워드 → 도구 매칭 + trace 구조만 검증.
실제 unit_ai handle() 은 본 검증 범위 밖.
"""
from __future__ import annotations

import sys
from pathlib import Path

_FLOW_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _FLOW_ROOT / "backend"
for p in (_BACKEND, _FLOW_ROOT):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)


def test_empty_prompt_returns_error():
    from core import home_orchestrator
    out = home_orchestrator.orchestrate("")
    assert out["ok"] is False
    assert out.get("error")


def test_sql_join_keyword_matches_sql_workspace_or_filebrowser():
    from core import home_orchestrator
    out = home_orchestrator.orchestrate("PROD_A SQL 로 lot_meta 와 et 결과 JOIN 해줘")
    assert out["ok"] is True
    names = [tr["tool"] for tr in out["trace"]]
    # filebrowser unit_ai 가 후보로 잡혀야 (tags=[filebrowser, sql_workspace])
    # 적어도 매칭된 도구 1개는 있어야 함
    assert len(names) >= 1
    # signals 에 sql_workspace 또는 filebrowser 가 있어야
    signals = out["meta"]["signals"]
    assert "sql_workspace" in signals or "filebrowser" in signals or "lot" in signals


def test_chart_keyword_matches_chart_tools():
    from core import home_orchestrator
    out = home_orchestrator.orchestrate("차트로 그려줘")
    assert out["ok"] is True
    assert len(out["trace"]) >= 1
    signals = out["meta"]["signals"]
    assert "chart" in signals or "dashboard" in signals


def test_no_match_returns_friendly_reply():
    from core import home_orchestrator
    out = home_orchestrator.orchestrate("ㅎㅎ")
    assert out["ok"] is False
    assert "도구" in out["reply"]
    assert out["trace"] == []


def test_trace_shape_well_formed():
    from core import home_orchestrator
    out = home_orchestrator.orchestrate("inform 메일 보내줘")
    if out["trace"]:
        tr = out["trace"][0]
        assert "tool" in tr and "kind" in tr and "score" in tr
        assert "confidence" in tr and "ok" in tr and "ms" in tr
        assert "result_preview" in tr
        assert 0.0 <= tr["confidence"] <= 1.0


def test_top_k_limits_picks():
    from core import home_orchestrator
    out = home_orchestrator.orchestrate("lot wafer ET SQL JOIN 차트 dashboard knob", top_k=2)
    assert len(out["trace"]) <= 2


def test_home_runtime_graph_basic_structure():
    from core import home_orchestrator

    graph = home_orchestrator.build_home_runtime_graph()
    node_ids = [node["id"] for node in graph["nodes"]]
    assert node_ids[:4] == ["prompt_input", "semantic_layer", "orchestrator", "result_renderer"]
    assert "unit_ai:filebrowser_ai_sql" in node_ids
    edges = {(edge["source"], edge["target"]) for edge in graph["edges"]}
    assert ("prompt_input", "semantic_layer") in edges
    assert ("semantic_layer", "orchestrator") in edges
    assert ("orchestrator", "unit_ai:filebrowser_ai_sql") in edges


def test_home_runtime_graph_selected_unit_status_and_edge():
    from core import home_orchestrator

    graph = home_orchestrator.build_home_runtime_graph(
        selected_units=["filebrowser_ai_sql"],
        statuses={
            "prompt_input": "success",
            "semantic_layer": "success",
            "orchestrator": "success",
            "unit_ai:filebrowser_ai_sql": "success",
            "result_renderer": "success",
        },
    )
    node = next(item for item in graph["nodes"] if item["id"] == "unit_ai:filebrowser_ai_sql")
    assert node["status"] == "success"
    edges = {(edge["source"], edge["target"]) for edge in graph["edges"]}
    assert ("unit_ai:filebrowser_ai_sql", "result_renderer") in edges


def test_home_orchestrator_marks_missing_filebrowser_source_blocked(monkeypatch, tmp_path):
    from core import home_orchestrator

    monkeypatch.setattr(home_orchestrator, "HOME_AGENT_RUNS_DIR", tmp_path / "runs")
    out = home_orchestrator.orchestrate("A1000 IOFF FileBrowser AI SQL로 보여줘", top_k=1)
    assert out["run_id"]
    assert out["graph"]
    fb_node = next(node for node in out["graph"]["nodes"] if node["id"] == "unit_ai:filebrowser_ai_sql")
    assert fb_node["status"] == "blocked"
    assert out["trace"][0]["blocked"] is True
    assert "대상" in out["reply"] or "source" in out["reply"]


def test_home_runtime_snapshot_save_list_load_shape(monkeypatch, tmp_path):
    from core import home_orchestrator

    monkeypatch.setattr(home_orchestrator, "HOME_AGENT_RUNS_DIR", tmp_path / "runs")
    result = {
        "ok": True,
        "answer": "완료",
        "tool": {
            "feature": "filebrowser_ai_sql",
            "sql_draft": {
                "sql": "SELECT lot_id WHERE value > 1",
                "selected_columns": ["lot_id", "value"],
                "warnings": [],
            },
            "table": {
                "kind": "preview",
                "columns": ["lot_id", "value"],
                "rows": [{"lot_id": "A1000", "value": 1.2, "hidden": "trim"}],
                "total": 1,
            },
        },
        "trace": {
            "semantic": {"intent": "filebrowser_ai_sql"},
            "unit_ai_selection": [{"key": "filebrowser_ai_sql", "status": "delegated"}],
            "guardrail": {"status": "done", "selected_feature": "filebrowser_ai_sql"},
            "interpretation": {},
            "validation": {"warnings": []},
            "steps": [],
        },
        "action_log": {"summary": ["공개 실행 요약"], "timeline": [], "final_answer": "완료"},
    }
    snapshot = home_orchestrator.build_home_runtime_snapshot(
        prompt="A1000 value만",
        result=result,
        user={"username": "tester"},
        source="pytest",
        save=True,
    )
    assert snapshot["run_id"]
    assert snapshot["graph"]["nodes"]
    assert snapshot["node_details"]["result_renderer"]["preview"]["rows"] == [
        {"lot_id": "A1000", "value": 1.2, "hidden": "trim"}
    ]
    rows = home_orchestrator.list_home_runtime_runs(limit=5)
    assert rows and rows[0]["run_id"] == snapshot["run_id"]
    loaded = home_orchestrator.load_home_runtime_run(snapshot["run_id"])
    assert loaded and loaded["run_id"] == snapshot["run_id"]


def test_pick_tools_internal_excludes_disabled(monkeypatch, tmp_path):
    from core import home_orchestrator, tool_registry

    # filebrowser unit_ai 를 비활성 → pick 후보에서 제외
    fake_state = tmp_path / "tool_registry_state.json"
    monkeypatch.setattr(tool_registry, "STATE_FILE", fake_state)
    tool_registry.set_enabled("filebrowser", False, by="pytest")

    picks, _ = home_orchestrator._pick_tools("파일 schema 검색")
    names = [p["tool"]["name"] for p in picks]
    assert "filebrowser" not in names

    # 복구
    tool_registry.set_enabled("filebrowser", True, by="pytest")


def test_flowi_unit_dispatcher_accepts_home_context_kwargs():
    from core.flowi_units.dispatcher import try_dispatch

    result = try_dispatch(
        "일반 Home Flow-i 질문",
        product="PROD_A",
        max_rows=8,
        only=["filebrowser"],
        allowed_keys={"filebrowser"},
        agent_context={"source": "pytest"},
        me={"username": "alice", "role": "user"},
        future_flag=True,
    )

    assert result is None


def test_flowi_unit_dispatcher_returns_registered_handle_result(monkeypatch):
    from core.flowi_units import dispatcher, registry

    expected = {"handled": True, "answer": "unit handled"}
    captured: dict[str, object] = {}

    class FakeUnitAI:
        def handle(self, prompt, slots, ctx):
            captured["prompt"] = prompt
            captured["slots"] = slots
            captured["ctx"] = ctx
            return expected

    monkeypatch.setattr(registry, "UNIT_AIS", {"fake_unit": FakeUnitAI()})

    result = dispatcher.try_dispatch(
        "fake prompt",
        product="PROD_A",
        max_rows=5,
        only=["fake_unit"],
        allowed_keys={"fake_unit"},
        agent_context={"client_run_id": "pytest"},
        me={"username": "alice"},
        future_flag=True,
    )

    assert result is expected
    assert captured["prompt"] == "fake prompt"
    assert captured["slots"]["product"] == "PROD_A"
    assert captured["slots"]["max_rows"] == 5
    assert captured["slots"]["future_flag"] is True
    assert captured["ctx"]["allowed_keys"] == ["fake_unit"]
    assert captured["ctx"]["agent_context"] == {"client_run_id": "pytest"}
    assert captured["ctx"]["me"] == {"username": "alice"}


def test_run_flowi_chat_falls_back_after_unhandled_unit_dispatch(monkeypatch):
    from core import home_orchestrator
    from routers import llm as llm_router

    monkeypatch.setattr(llm_router, "_append_user_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(llm_router, "_allowed_flowi_feature_keys", lambda _me: {"filebrowser"})
    monkeypatch.setattr(llm_router.llm_adapter, "is_available", lambda: False)
    monkeypatch.setattr(
        home_orchestrator,
        "record_flowi_runtime_run",
        lambda *_args, **_kwargs: {
            "run_id": "pytest-run",
            "graph": {"nodes": [], "edges": []},
            "status": "success",
        },
    )

    called: dict[str, object] = {}

    def fake_legacy_handler(
        prompt,
        product,
        max_rows=12,
        allowed_keys=None,
        username="flowi",
        role="user",
        agent_context=None,
    ):
        called["allowed_keys"] = set(allowed_keys or [])
        called["agent_context"] = agent_context
        return {
            "handled": True,
            "intent": "pytest_legacy",
            "action": "pytest.legacy",
            "feature": "filebrowser",
            "answer": "legacy fallback",
        }

    monkeypatch.setattr(llm_router, "_handle_flowi_query", fake_legacy_handler)

    result = llm_router._run_flowi_chat(
        prompt="일반 상태 확인",
        product="",
        max_rows=12,
        me={"username": "alice", "role": "user"},
        agent_context={"source": "pytest"},
    )

    assert result["answer"] == "legacy fallback"
    assert result["run_id"] == "pytest-run"
    assert called["allowed_keys"] == {"filebrowser"}
    assert called["agent_context"] == {"source": "pytest"}
