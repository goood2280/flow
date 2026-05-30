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


def test_flowi_verify_marks_unavailable_without_llm_config(monkeypatch):
    from routers import llm

    class _Request:
        pass

    monkeypatch.setattr(llm, "current_user", lambda _request: {"username": "tester", "role": "admin"})
    monkeypatch.setattr(llm.llm_adapter, "is_available", lambda: False)

    out = llm.flowi_verify(llm.FlowiVerifyReq(), _Request())

    assert out["ok"] is False
    assert out["unavailable"] is True
    assert out["error"] == "llm unavailable"
    assert out["message"] == "LLM 미설정"


def test_flowi_workflows_api_lists_defaults(monkeypatch, tmp_path):
    from routers import llm
    from core import flowi_workflow_catalog as catalog

    class _Request:
        pass

    monkeypatch.setattr(catalog, "RUNTIME_CATALOG_FILE", tmp_path / "flowi_workflows.json")
    monkeypatch.setattr(catalog, "CHANGE_LOG_FILE", tmp_path / "flowi_workflows.changes.jsonl")
    monkeypatch.setattr(llm, "_append_user_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(llm, "current_user", lambda _request: {"username": "tester", "role": "admin"})

    out = llm.flowi_workflows(_Request())

    assert out["ok"] is True
    assert out["can_edit"] is True
    assert len(out["workflows"]) == 50
    assert out["default_target_count"] == 50
    assert any(row["id"].startswith("wf_auto_") for row in out["workflows"])
    assert len(catalog.workflow_few_shots(limit=50)) == 50


def test_flowi_workflows_draft_and_save(monkeypatch, tmp_path):
    from routers import llm
    from core import flowi_workflow_catalog as catalog

    monkeypatch.setattr(catalog, "RUNTIME_CATALOG_FILE", tmp_path / "flowi_workflows.json")
    monkeypatch.setattr(catalog, "CHANGE_LOG_FILE", tmp_path / "flowi_workflows.changes.jsonl")
    monkeypatch.setattr(llm, "_append_user_event", lambda *_args, **_kwargs: None)

    draft = llm.flowi_workflows_draft(
        llm.FlowiWorkflowDraftReq(prompt="Inline 특정 item trend를 knob coloring까지 제공"),
        _admin={"username": "admin", "role": "admin"},
    )
    saved = llm.flowi_workflows_save(
        llm.FlowiWorkflowSaveReq(workflow=draft["workflow"]),
        _admin={"username": "admin", "role": "admin"},
    )

    assert draft["ok"] is True
    assert draft["workflow"]["source_roles"]
    assert saved["ok"] is True
    assert saved["workflow"]["id"] == draft["workflow"]["id"]


def test_flowi_unit_dispatch_allows_step_lookup_from_filebrowser_permission(monkeypatch):
    from core import fab_reference
    from core.flowi_units import try_dispatch

    monkeypatch.setattr(
        fab_reference,
        "lookup_step_in_text",
        lambda prompt, product: {
            "found": True,
            "direction": "id_to_step",
            "answer": "AA100090는 SD_EPI step입니다.",
            "matches": [{"product": product or "PRODA", "step_id": "AA100090", "function_step": "SD_EPI"}],
        },
    )

    out = try_dispatch(
        "AA100090는 무슨 step이야",
        product="PRODA",
        allowed_keys={"filebrowser"},
        only=("step_lookup",),
    )

    assert out is not None
    assert out["unit_ai"] == "step_lookup"
    assert out["table"]["rows"][0]["function_step"] == "SD_EPI"


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


def test_home_orchestrator_runs_inform_registration_runtime_with_public_result(monkeypatch):
    from core import home_orchestrator
    from core.flowi_units import inform_registration_runtime as runtime

    class DummyRequest:
        pass

    request = DummyRequest()
    captured = {}

    def fake_run(payload, *, username="", request=None):
        captured["payload"] = payload
        captured["username"] = username
        captured["request"] = request
        return {
            "ok": True,
            "session_id": "inform_reg_pytest",
            "status": "review",
            "missing": [],
            "requires_confirmation": True,
            "draft": {"inform": {"lot_id": "R1000"}},
            "created_inform": {},
            "warnings": ["confirm required"],
            "slots": {"hidden": "not public"},
            "trace": [{"node_id": "slot_extract"}],
            "answer": "Inform draft가 준비되었습니다.",
        }

    monkeypatch.setattr(runtime, "run_inform_registration_runtime", fake_run)
    tool = {"name": "inform_registration", "kind": "unit_ai", "title": "Inform 등록 도우미"}
    step_input = {
        "prompt": "인폼 등록 준비",
        "session_id": "inform_reg_pytest",
        "action": "confirm",
        "slot_overrides": {"product": "PRODA"},
        "ignored": "not forwarded",
    }

    exec_out = home_orchestrator._execute_step(
        tool,
        step_input,
        request=request,
        user={"username": "alice"},
    )
    assert captured == {
        "payload": {
            "prompt": "인폼 등록 준비",
            "session_id": "inform_reg_pytest",
            "action": "confirm",
            "slot_overrides": {"product": "PRODA"},
        },
        "username": "alice",
        "request": request,
    }
    assert exec_out["ok"] is True
    assert exec_out["status"] == "review"

    row = home_orchestrator._make_trace_row(
        {"tool": tool, "input": step_input, "reason": "pytest", "source": "pytest"},
        exec_out,
    )
    assert set(row["result"]) == {
        "session_id",
        "status",
        "missing",
        "requires_confirmation",
        "draft",
        "created_inform",
        "warnings",
    }
    assert "slots" not in row["result"]
    assert "trace" not in row["result"]


def test_home_agent_orchestrate_and_run_tool_pass_request_context(monkeypatch):
    from routers import home_agent

    class DummyRequest:
        pass

    request = DummyRequest()
    me = {"username": "alice", "role": "admin"}
    captured = {}
    tool = {"name": "inform_registration", "kind": "unit_ai", "title": "Inform 등록 도우미", "enabled": True}

    monkeypatch.setattr(home_agent, "current_user", lambda _request: me)
    monkeypatch.setattr(home_agent.audit, "record", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(home_agent.tool_registry, "get_tool", lambda name: tool if name == "inform_registration" else None)

    def fake_orchestrate(prompt, user=None, top_k=2, *, request=None):
        captured["orchestrate"] = {"prompt": prompt, "user": user, "top_k": top_k, "request": request}
        return {"ok": True, "trace": [], "meta": {}, "picked_count": 0}

    def fake_execute_step(tool_arg, step_input, *, request=None, user=None):
        captured["run_tool"] = {"tool": tool_arg, "input": step_input, "request": request, "user": user}
        return {"ok": True, "ms": 1, "result_preview": "ok", "result": {"status": "review"}}

    monkeypatch.setattr(home_agent.home_orchestrator, "orchestrate", fake_orchestrate)
    monkeypatch.setattr(home_agent.home_orchestrator, "_execute_step", fake_execute_step)

    home_agent.orchestrate(request, home_agent.OrchestrateRequest(prompt="인폼 등록", top_k=3))
    assert captured["orchestrate"] == {
        "prompt": "인폼 등록",
        "user": me,
        "top_k": 3,
        "request": request,
    }

    out = home_agent.run_tool(
        request,
        home_agent.RunToolRequest(tool="inform_registration", input={"prompt": "인폼 등록"}),
    )
    assert out["ok"] is True
    assert captured["run_tool"] == {
        "tool": tool,
        "input": {"prompt": "인폼 등록"},
        "request": request,
        "user": me,
    }
    home_agent_src = (_FLOW_ROOT / "backend" / "routers" / "home_agent.py").read_text(encoding="utf-8")
    assert "orchestrate_stream(prompt, user=me, top_k=top_k, request=request)" in home_agent_src


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


def test_flowi_chat_merges_server_memory_into_agent_context(monkeypatch, tmp_path):
    from core import home_memory, home_orchestrator
    from routers import llm as llm_router

    monkeypatch.setattr(home_memory, "MEMORY_FILE", tmp_path / "home_memory.jsonl")
    home_memory.remember_turn(
        username="alice",
        prompt="A1000 IOFF 보여줘",
        answer="A1000 IOFF preview 3 rows",
        tool={"feature": "filebrowser", "intent": "filebrowser_data_preview", "action": "preview_filebrowser_data"},
    )
    monkeypatch.setattr(llm_router, "_append_user_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(llm_router, "_allowed_flowi_feature_keys", lambda _me: {"filebrowser"})
    monkeypatch.setattr(llm_router.llm_adapter, "is_available", lambda: False)
    monkeypatch.setattr(
        home_orchestrator,
        "record_flowi_runtime_run",
        lambda *_args, **_kwargs: {"run_id": "pytest-memory-run", "graph": {"nodes": [], "edges": []}, "status": "success"},
    )

    captured: dict[str, object] = {}

    def fake_legacy_handler(
        prompt,
        product,
        max_rows=12,
        allowed_keys=None,
        username="flowi",
        role="user",
        agent_context=None,
    ):
        captured["messages"] = list((agent_context or {}).get("messages") or [])
        return {
            "handled": True,
            "intent": "pytest_memory_context",
            "action": "pytest.memory",
            "feature": "filebrowser",
            "answer": "current answer",
        }

    monkeypatch.setattr(llm_router, "_handle_flowi_query", fake_legacy_handler)

    result = llm_router._run_flowi_chat(
        prompt="현재 상태 알려줘",
        product="",
        max_rows=12,
        me={"username": "alice", "role": "admin"},
        agent_context={},
    )

    assert result["answer"] == "current answer"
    messages = captured["messages"]
    assert any(m.get("role") == "user" and "A1000 IOFF" in m.get("prompt", "") for m in messages)
    assert any(m.get("role") == "assistant" and "preview 3 rows" in m.get("text", "") for m in messages)


def test_flowi_chat_answers_memory_recall_without_client_context(monkeypatch, tmp_path):
    from core import home_memory, home_orchestrator
    from routers import llm as llm_router

    monkeypatch.setattr(home_memory, "MEMORY_FILE", tmp_path / "home_memory.jsonl")
    home_memory.remember_turn(
        username="alice",
        prompt="변경점 관리 회의 결정사항 알려줘",
        answer="결정사항은 device owner 확인입니다.",
        tool={"feature": "meeting", "intent": "meeting_recall_summary"},
    )
    monkeypatch.setattr(llm_router, "_append_user_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(llm_router, "_allowed_flowi_feature_keys", lambda _me: {"meeting", "calendar"})
    monkeypatch.setattr(llm_router.llm_adapter, "is_available", lambda: False)
    monkeypatch.setattr(
        home_orchestrator,
        "record_flowi_runtime_run",
        lambda *_args, **_kwargs: {"run_id": "pytest-memory-recall", "graph": {"nodes": [], "edges": []}, "status": "success"},
    )

    result = llm_router._run_flowi_chat(
        prompt="아까 내가 뭐 물어봤지?",
        product="",
        max_rows=12,
        me={"username": "alice", "role": "admin"},
        agent_context={},
    )

    assert result["tool"]["intent"] == "home_memory_recall"
    assert "변경점 관리 회의" in result["answer"]
    assert "device owner" in result["answer"]


def test_home_orchestrator_answers_memory_recall(monkeypatch, tmp_path):
    from core import home_memory, home_orchestrator

    monkeypatch.setattr(home_memory, "MEMORY_FILE", tmp_path / "home_memory.jsonl")
    monkeypatch.setattr(home_orchestrator, "HOME_AGENT_RUNS_DIR", tmp_path / "runs")
    home_memory.remember_turn(
        username="alice",
        prompt="인폼 등록할 때 필수값 뭐야?",
        answer="product, lot_id, module, note, mail target이 필요합니다.",
        tool={"feature": "inform", "intent": "inform_registration_help"},
    )

    out = home_orchestrator.orchestrate("이전 질문과 답변 기억해?", user={"username": "alice"})

    assert out["ok"] is True
    assert out["meta"]["planner"] == "home_memory"
    assert "인폼 등록" in out["reply"]
    assert "product" in out["reply"]


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


def test_run_flowi_chat_falls_back_after_unhandled_unit_dispatch(monkeypatch, tmp_path):
    from core import home_memory, home_orchestrator
    from routers import llm as llm_router

    monkeypatch.setattr(home_memory, "MEMORY_FILE", tmp_path / "home_memory.jsonl")
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
    assert called["agent_context"]["source"] == "pytest"
    assert called["agent_context"]["server_memory"]["enabled"] is True
    assert called["agent_context"]["messages"] == []


def _run_step_mapping_flowi_chat(monkeypatch, tmp_path, prompt):
    from core import flowi_units, home_memory, home_orchestrator
    from routers import llm as llm_router

    class DummyPaths:
        def __init__(self, root):
            self.base_root = root
            self.db_root = root
            self.data_root = root / "flow-data"
            self.cache_dir = self.data_root / "cache"

    paths = DummyPaths(tmp_path)
    paths.data_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(llm_router, "PATHS", paths)
    monkeypatch.setattr(home_memory, "MEMORY_FILE", tmp_path / "home_memory.jsonl")
    monkeypatch.setattr(flowi_units, "try_dispatch", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(llm_router, "_append_user_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(llm_router, "_allowed_flowi_feature_keys", lambda _me: {"filebrowser", "splittable"})
    monkeypatch.setattr(llm_router.llm_adapter, "is_available", lambda: False)
    monkeypatch.setattr(
        home_orchestrator,
        "record_flowi_runtime_run",
        lambda *_args, **_kwargs: {"run_id": "pytest-step-mapping", "graph": {"nodes": [], "edges": []}, "status": "success"},
    )

    return llm_router._run_flowi_chat(
        prompt=prompt,
        product="",
        max_rows=12,
        me={"username": "alice", "role": "admin"},
        agent_context={},
    )


def test_flowi_chat_answers_step_id_from_step_matching_csv(monkeypatch, tmp_path):
    (tmp_path / "step_matching.csv").write_text(
        "product,step_id,function_step\n"
        "PRODA,AA100090,SD_EPI\n",
        encoding="utf-8",
    )

    result = _run_step_mapping_flowi_chat(monkeypatch, tmp_path, "AA100090은 어떤 스텝이야")

    assert result["tool"]["intent"] == "step_mapping_lookup"
    assert result["tool"]["action"] == "query_step_mapping_lookup"
    assert "SD_EPI" in result["answer"]
    assert "step_matching.csv" in result["answer"]
    assert result["tool"]["source_ids"] == ["step_matching.csv"]


def test_flowi_chat_expands_ppid_knob_feature_to_step_ids(monkeypatch, tmp_path):
    (tmp_path / "ppid_knob.csv").write_text(
        "feature_name,function_step,rule_order,operator,category\n"
        "3.0 VTN,VT_ADJUST,RO,eq,PPID_03_0\n",
        encoding="utf-8",
    )
    (tmp_path / "step_matching.csv").write_text(
        "product,step_id,function_step\n"
        "PRODA,AA300100,VT_ADJUST\n"
        "PRODB,BB300100,VT_ADJUST\n",
        encoding="utf-8",
    )

    result = _run_step_mapping_flowi_chat(monkeypatch, tmp_path, "3.0 VTN이 어떤 step_id에 영향을 받냐")

    assert result["tool"]["intent"] == "step_mapping_lookup"
    assert result["tool"]["source_ids"] == ["ppid_knob.csv", "step_matching.csv"]
    assert "ppid_knob.csv" in result["answer"]
    assert "VT_ADJUST" in result["answer"]
    assert "PRODA: AA300100" in result["answer"]
    assert "PRODB: BB300100" in result["answer"]
    assert any(item.get("token") == "3.0 VTN" for item in result["tool"]["term_resolution"])


def test_flowi_chat_uses_vehicle_matching_step_desc_as_function_step(monkeypatch, tmp_path):
    (tmp_path / "Vehicle_matching.csv").write_text(
        "product,step_id,step_desc\n"
        "PRODA,AA100090,SD_EPI\n",
        encoding="utf-8",
    )

    result = _run_step_mapping_flowi_chat(monkeypatch, tmp_path, "AA100090은 어떤 스텝이야")

    assert "SD_EPI" in result["answer"]
    assert "Vehicle_matching.csv" in result["answer"]
    assert result["tool"]["source_ids"] == ["Vehicle_matching.csv"]
