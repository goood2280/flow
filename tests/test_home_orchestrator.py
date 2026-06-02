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
    llm._FLOWI_VERIFY_CACHE["result"] = None

    out = llm.flowi_verify(llm.FlowiVerifyReq(), _Request())

    assert out["ok"] is False
    assert out["status"] == "unavailable"
    assert out["unavailable"] is True
    assert out["error"] == "llm unavailable"
    assert out["message"] == "LLM 미설정"
    assert isinstance(out["elapsed_ms"], int)
    assert isinstance(out["meta"], dict)


def test_flowi_verify_returns_connected_status(monkeypatch):
    from routers import llm

    class _Request:
        pass

    monkeypatch.setattr(llm, "current_user", lambda _request: {"username": "tester", "role": "admin"})
    monkeypatch.setattr(llm.llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(llm.llm_adapter, "get_config", lambda redact=True: {"provider": "vertex_gemini", "model": "gemini", "auth_mode": "google_adc"})
    monkeypatch.setattr(llm.llm_adapter, "_google_adc_token_cache_status", lambda: {"cached": True, "status": "hit"})
    # Honest verify runs a real bounded probe; mock it as a healthy response.
    monkeypatch.setattr(
        llm.llm_adapter,
        "complete",
        lambda *_args, **_kwargs: {"ok": True, "text": "확인완료", "meta": {"latency_ms": 12}},
    )
    monkeypatch.setattr(llm.llm_adapter, "warm_google_adc_token_cache", lambda *_args, **_kwargs: False)
    llm._FLOWI_VERIFY_CACHE["result"] = None

    out = llm.flowi_verify(llm.FlowiVerifyReq(), _Request())

    assert out["ok"] is True
    assert out["status"] == "connected"
    assert out["message"] == "확인완료"
    assert out["meta"]["provider"] == "vertex_gemini"
    assert out["meta"]["token_cache"]["status"] == "hit"
    assert out["meta"]["verify_mode"] == "live_probe"
    assert out["meta"]["live_llm_call"] is True


def test_flowi_verify_starts_vertex_token_warmup_then_probes(monkeypatch):
    from routers import llm

    class _Request:
        pass

    warmups = []
    monkeypatch.setattr(llm, "current_user", lambda _request: {"username": "tester", "role": "admin"})
    monkeypatch.setattr(llm.llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(llm.llm_adapter, "get_config", lambda redact=True: {"provider": "vertex_gemini", "model": "gemini", "auth_mode": "google_adc"})
    monkeypatch.setattr(llm.llm_adapter, "_google_adc_token_cache_status", lambda: {"cached": False, "status": "empty"})
    monkeypatch.setattr(llm.llm_adapter, "warm_google_adc_token_cache", lambda **kwargs: warmups.append(kwargs) or True)
    monkeypatch.setattr(
        llm.llm_adapter,
        "complete",
        lambda *_args, **_kwargs: {"ok": True, "text": "확인완료", "meta": {}},
    )
    llm._FLOWI_VERIFY_CACHE["result"] = None

    out = llm.flowi_verify(llm.FlowiVerifyReq(), _Request())

    assert out["ok"] is True
    assert out["status"] == "connected"
    assert out["meta"]["warmup_started"] is True
    assert warmups == [{"timeout_s": 8}]


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


def test_dashboard_chart_knob_coloring_reuses_saved_chart_rows(monkeypatch, tmp_path):
    import polars as pl
    from routers import llm

    monkeypatch.setattr(llm.dashboard_charting, "CHART_SESSION_DIR", tmp_path / "sessions")
    sid = llm.dashboard_charting.save_chart_session({
        "username": "tester",
        "chart_type": "scatter",
        "config": {
            "chart_type": "scatter",
            "source_type": "INLINE",
            "metric": "15.0 M2",
            "item_id": "15.0 M2",
            "x_col": "tkout_time",
            "product": "PRODA",
        },
        "base_data_query": {
            "source_type": "INLINE",
            "db": "1.RAWDATA_DB_INLINE",
            "files": ["PRODA_inline.parquet"],
            "sql": "SELECT root_lot_id, wafer_id, tkout_time, AVG(value) AS y FROM INLINE GROUP BY 1,2,3",
        },
        "data": [
            {"x": 0, "y": 1.2, "tkout_time": "2026-01-01", "root_lot_id": "A1001", "wafer_id": "3", "lot_wf": "A1001_3"},
            {"x": 1, "y": 1.7, "tkout_time": "2026-01-02", "root_lot_id": "A1001", "wafer_id": "4", "lot_wf": "A1001_4"},
        ],
    })

    def _unexpected_requery(*_args, **_kwargs):
        raise AssertionError("base chart data must be reused instead of re-queried")

    monkeypatch.setattr(llm, "_handle_inline_trend_chart", _unexpected_requery)
    monkeypatch.setattr(llm, "_handle_et_trend_chart", _unexpected_requery)

    def _knob_lf(product, lots, prompt, xy_metrics):
        assert product == "PRODA"
        assert "A1001" in lots
        assert xy_metrics == ["15.0 M2"]
        return {
            "ok": True,
            "lf": pl.DataFrame([
                {"lot_wf": "A1001_3", "root_lot_id": "A1001", "wafer_id": "3", "color_value": "PPID_A", "color_n": 1},
                {"lot_wf": "A1001_4", "root_lot_id": "A1001", "wafer_id": "4", "color_value": "PPID_B", "color_n": 1},
            ]).lazy(),
            "group_cols": ["lot_wf"],
            "knob_col": "KNOB_1.0_STI",
            "display_name": "1.0 STI",
            "file_count": 1,
        }

    monkeypatch.setattr(llm, "_flowi_knob_lf", _knob_lf)

    out = llm._handle_dashboard_chart_context_followup(
        "방금 차트 1.0 STI Knob으로 컬러링해줘",
        "PRODA",
        12,
        {"chart_session_id": sid},
    )

    assert out["handled"] is True
    assert out["action"] == "refine_chart_session_knob_coloring"
    assert out["chart_session_id"] == sid
    assert [p["y"] for p in out["chart_result"]["points"]] == [1.2, 1.7]
    assert [p["color_value"] for p in out["chart_result"]["points"]] == ["PPID_A", "PPID_B"]
    assert out["chart_result"]["sources"]["base_chart_session_id"] == sid
    assert out["raw_data_download"]["url"].endswith(f"chart_session_id={sid}")

    saved = llm.dashboard_charting.load_chart_session(sid)
    assert [row["color_value"] for row in saved["data"]] == ["PPID_A", "PPID_B"]
    assert saved["base_data_query"]["knob_join"]["reuse_base_chart_raw_data"] is True

    raw_out = llm._handle_dashboard_chart_raw_data_followup(
        "방금 차트 raw data 줘",
        {"chart_session_id": sid},
        12,
        username="tester",
        role="admin",
    )
    assert raw_out["handled"] is True
    assert "color_value" in [col["key"] for col in raw_out["table"]["columns"]]
    assert raw_out["table"]["rows"][0]["color_value"] == "PPID_A"


def test_dashboard_chart_raw_data_provenance_uses_saved_session_query(monkeypatch, tmp_path):
    from routers import llm

    monkeypatch.setattr(llm.dashboard_charting, "CHART_SESSION_DIR", tmp_path / "sessions")
    sid = llm.dashboard_charting.save_chart_session({
        "username": "tester",
        "chart_type": "scatter",
        "config": {"chart_type": "scatter", "source_type": "INLINE", "metric": "15.0 M2", "product": "PRODA"},
        "base_data_query": {
            "source_type": "INLINE",
            "db": "1.RAWDATA_DB_INLINE",
            "files": ["PRODA_inline.parquet"],
            "sql": "SELECT root_lot_id, wafer_id, tkout_time, AVG(value) AS y FROM INLINE GROUP BY 1,2,3",
            "filters": {"product": "PRODA", "item_id": "15.0 M2"},
            "aggregation": {"INLINE": "avg"},
        },
        "data": [{"root_lot_id": "A1001", "wafer_id": "3", "tkout_time": "2026-01-01", "y": 1.2}],
    })

    out = llm._handle_dashboard_chart_raw_data_provenance_followup(
        "이 chart raw data 어떻게 뽑았어?",
        {"chart_session_id": sid},
        username="tester",
        role="admin",
    )

    assert out["handled"] is True
    assert out["action"] == "explain_chart_raw_data_query"
    assert "1.RAWDATA_DB_INLINE" in out["answer"]
    assert "SELECT root_lot_id" in out["answer"]
    rows = {row["field"]: row["value"] for row in out["table"]["rows"]}
    assert rows["source_type"] == "INLINE"
    assert "PRODA_inline.parquet" in rows["files"]
    assert "15.0 M2" in rows["filters"]


def test_flowi_step_id_token_is_not_classified_as_lot():
    from routers import llm

    step_summary = llm._slot_summary("AA100160 무슨 step이야?")
    assert step_summary["steps"] == ["AA100160"]
    assert step_summary["lots"] == []
    assert step_summary["root_lot_ids"] == []
    assert step_summary["fab_lot_ids"] == []

    suffix_step_summary = llm._slot_summary("AA100160EC 무슨 step이야?")
    assert suffix_step_summary["steps"] == ["AA100160EC"]
    assert suffix_step_summary["lots"] == []
    assert suffix_step_summary["root_lot_ids"] == []
    assert suffix_step_summary["fab_lot_ids"] == []

    lot_summary = llm._slot_summary("A1001 스플릿테이블 보여줘")
    assert lot_summary["steps"] == []
    assert lot_summary["root_lot_ids"] == ["A1001"]


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


def test_flowi_chat_skips_llm_polish_for_clarification(monkeypatch, tmp_path):
    from core import flowi_units, home_memory, home_orchestrator
    from routers import llm as llm_router

    monkeypatch.setattr(home_memory, "MEMORY_FILE", tmp_path / "home_memory.jsonl")
    monkeypatch.setattr(flowi_units, "try_dispatch", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(llm_router, "_append_user_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(llm_router, "_allowed_flowi_feature_keys", lambda _me: {"splittable"})
    monkeypatch.setattr(llm_router, "_handle_explicit_splittable_view_fast_path", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(llm_router.llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(
        llm_router.llm_adapter,
        "complete",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("LLM polish should be skipped")),
    )
    monkeypatch.setattr(
        home_orchestrator,
        "record_flowi_runtime_run",
        lambda *_args, **_kwargs: {"run_id": "pytest-clarify", "graph": {"nodes": [], "edges": []}, "status": "waiting"},
    )

    def fake_legacy_handler(*_args, **_kwargs):
        return {
            "handled": True,
            "intent": "splittable_context_followup",
            "action": "clarify_product",
            "feature": "splittable",
            "answer": "product가 없는 SplitTable 요청입니다. 어느 product 기준으로 볼지 선택해주세요.",
            "missing": ["product"],
            "pending_prompt": "A1001 스플릿테이블",
            "clarification": {"question": "어느 product 기준으로 볼까요?", "choices": []},
        }

    monkeypatch.setattr(llm_router, "_handle_flowi_query", fake_legacy_handler)

    result = llm_router._run_flowi_chat(
        prompt="A1001 스플릿테이블",
        product="",
        max_rows=12,
        me={"username": "alice", "role": "admin"},
        agent_context={},
    )

    assert result["answer"].startswith("product가 없는 SplitTable")
    assert result["llm"]["used"] is False
    assert result["llm"]["skipped"] == "deterministic_tool_result"


def test_flowi_splittable_single_product_candidate_auto_executes(monkeypatch):
    from routers import llm as llm_router

    preview = {
        "selected_function": {"name": "query_splittable_view"},
        "function_call": {"function": {"arguments": {"product": "", "root_lot_ids": ["A1001"], "wafer_ids": []}}},
        "validation": {"missing": ["product"]},
    }
    calls = []
    monkeypatch.setattr(llm_router, "_structure_flowi_function_call", lambda *_args, **_kwargs: preview)
    monkeypatch.setattr(
        llm_router,
        "_resolve_products_for_lots",
        lambda *_args, **_kwargs: [{"product": "PRODA", "sources": "ML_TABLE", "lots": "A1001", "row_count": 12}],
    )

    def fake_view(args, product_hint, prompt, max_rows):
        calls.append((dict(args), product_hint, prompt, max_rows))
        return {"handled": True, "intent": "splittable_view", "action": "query_splittable_view", "feature": "splittable", "answer": "ok"}

    monkeypatch.setattr(llm_router, "_flowi_query_splittable_view_tool", fake_view)

    out = llm_router._handle_wafer_split_at_step("A1001 스플릿테이블 보여줘", "", 12)

    assert out["action"] == "query_splittable_view"
    assert calls
    assert calls[0][0]["product"] == "PRODA"
    assert calls[0][1] == "PRODA"


def test_flowi_splittable_multiple_product_candidates_still_clarifies(monkeypatch):
    from routers import llm as llm_router

    preview = {
        "selected_function": {"name": "query_splittable_view"},
        "function_call": {"function": {"arguments": {"product": "", "root_lot_ids": ["A1001"], "wafer_ids": []}}},
        "validation": {"missing": ["product"]},
    }
    monkeypatch.setattr(llm_router, "_structure_flowi_function_call", lambda *_args, **_kwargs: preview)
    monkeypatch.setattr(
        llm_router,
        "_resolve_products_for_lots",
        lambda *_args, **_kwargs: [
            {"product": "PRODA", "sources": "ML_TABLE", "lots": "A1001", "row_count": 12},
            {"product": "PRODB", "sources": "ML_TABLE", "lots": "A1001", "row_count": 8},
        ],
    )
    monkeypatch.setattr(
        llm_router,
        "_flowi_query_splittable_view_tool",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("view should wait for product choice")),
    )

    out = llm_router._handle_wafer_split_at_step("A1001 스플릿테이블 보여줘", "", 12)

    assert out["action"] == "clarify_product"
    choices = out["clarification"]["choices"]
    assert [choice["title"] for choice in choices] == ["PRODA", "PRODB"]


def test_flowi_splittable_view_tool_attaches_runtime_metadata(monkeypatch):
    from routers import llm as llm_router
    from routers import splittable as splittable_router

    monkeypatch.setattr(
        splittable_router,
        "view_split",
        lambda **_kwargs: {
            "root_lot_id": "A1001",
            "runtime_profile": {"total_ms": 11},
            "view_cache": {"hit": True, "status": "fresh"},
        },
    )
    monkeypatch.setattr(
        llm_router,
        "_flowi_splittable_view_to_inline",
        lambda *_args, **_kwargs: (
            {"kind": "splittable_inline", "title": "SplitTable inline", "rows": [{"cells": []}], "total": 1},
            {"kind": "splittable_view_rows", "rows": [], "total": 0},
        ),
    )

    out = llm_router._flowi_query_splittable_view_tool(
        {"product": "PRODA", "root_lot_ids": ["A1001"], "wafer_ids": []},
        "PRODA",
        "A1001 스플릿테이블 보여줘",
        12,
    )

    assert out["handled"] is True
    assert out["split_api"]["path"] == "/api/splittable/view"
    assert out["runtime_profile"] == {"total_ms": 11}
    assert out["view_cache"] == {"hit": True, "status": "fresh"}
    assert isinstance(out["elapsed_ms"], int)


def test_flowi_explicit_splittable_view_prompt_accepts_korean():
    from routers import llm as llm_router

    assert llm_router._flowi_explicit_splittable_view_prompt("PRODA A1001 스플릿테이블 보여줘") is True
    assert llm_router._flowi_explicit_splittable_view_prompt("A1001 스플릿 테이블 보여줘") is True
    assert llm_router._flowi_explicit_splittable_view_prompt("A1001 SplitTable 보여줘") is True
    assert llm_router._flowi_explicit_splittable_view_prompt("A1001 1.0 STI Split(or Knob) 보여줘") is True


def test_flowi_splittable_view_prompt_accepts_bare_alpha_root_lot():
    from routers import llm as llm_router

    preview = llm_router._structure_flowi_function_call(
        "AZAAA Split table 보여줘\nproduct: PRODA",
        product="",
        max_rows=12,
    )
    selected = preview["selected_function"]
    args = preview["function_call"]["function"]["arguments"]

    assert selected["name"] == "query_splittable_view"
    assert selected["feature"] == "splittable"
    assert args["product"] == "PRODA"
    assert args["root_lot_ids"] == ["AZAAA"]
    assert preview["validation"]["missing"] == []


def test_flowi_splittable_knob_prompt_routes_to_custom_set_view():
    from routers import llm as llm_router

    preview = llm_router._structure_flowi_function_call(
        "A1001 1.0 STI Split(or Knob) 보여줘",
        product="",
        max_rows=12,
    )
    selected = preview["selected_function"]
    args = preview["function_call"]["function"]["arguments"]

    assert selected["name"] == "query_splittable_view"
    assert selected["intent"] == "splittable_view"
    assert args["root_lot_ids"] == ["A1001"]
    assert args["step"] == "1.0 STI"
    assert args["group"] == "KNOB"
    assert "product" in preview["validation"]["missing"]


def test_flowi_explicit_splittable_view_keeps_dotted_product_token(monkeypatch):
    from routers import llm as llm_router

    calls = []
    monkeypatch.setattr(llm_router, "_configured_product_names", lambda: {})
    monkeypatch.setattr(
        llm_router,
        "_flowi_query_splittable_view_tool",
        lambda args, product_hint, prompt, max_rows: calls.append((dict(args), product_hint, prompt, max_rows))
        or {"handled": True, "intent": "splittable_view", "action": "query_splittable_view", "feature": "splittable", "answer": "ok"},
    )

    out = llm_router._handle_explicit_splittable_view_fast_path(
        "AZAAA.1 A1001 스플릿테이블 보여줘",
        "",
        12,
        {"splittable"},
    )

    assert out and out["handled"] is True
    assert out["action"] == "query_splittable_view"
    assert calls
    assert calls[0][0]["product"] == "AZAAA.1"
    assert calls[0][0]["root_lot_ids"] == ["A1001"]
    assert calls[0][1] == "AZAAA.1"


def test_flowi_splittable_view_tool_passes_knob_custom_cols(monkeypatch):
    from routers import llm as llm_router
    from routers import splittable as splittable_router

    class FakeSchema:
        def names(self):
            return ["root_lot_id", "wafer_id", "KNOB_1.0 STI", "KNOB_1.0 STI_BIAS", "KNOB_2.0 STI"]

    class FakeLazyFrame:
        def collect_schema(self):
            return FakeSchema()

    captured = {}
    monkeypatch.setattr(splittable_router, "_scan_product_base", lambda _product: FakeLazyFrame())

    def fake_view_split(**kwargs):
        captured.update(kwargs)
        return {
            "product": kwargs["product"],
            "root_lot_id": kwargs["root_lot_id"],
            "headers": ["#1"],
            "header_groups": [{"label": "A1001.1", "span": 1}],
            "wafer_fab_list": ["A1001.1"],
            "rows": [
                {"_param": "KNOB_1.0 STI", "_display": "1.0 STI", "_cells": {"0": {"actual": "ON", "plan": ""}}},
                {"_param": "KNOB_1.0 STI_BIAS", "_display": "1.0 STI BIAS", "_cells": {"0": {"actual": "LOW", "plan": ""}}},
            ],
        }

    monkeypatch.setattr(splittable_router, "view_split", fake_view_split)

    out = llm_router._flowi_query_splittable_view_tool(
        {"product": "PRODA", "root_lot_ids": ["A1001"], "wafer_ids": [], "step": "1.0 STI", "group": "KNOB"},
        "PRODA",
        "PRODA A1001 1.0 STI Split(or Knob) 보여줘",
        12,
    )

    assert captured["custom_cols"] == "KNOB_1.0 STI,KNOB_1.0 STI_BIAS"
    assert out["handled"] is True
    assert out["action"] == "query_splittable_view"
    assert out["filters"]["custom_set_filter"] == "1.0 STI"
    assert out["split_api"]["custom_cols_count"] == 2
    assert [row["parameter"] for row in out["split_view"]["rows"]] == ["KNOB_1.0 STI", "KNOB_1.0 STI_BIAS"]


def test_flowi_splittable_inline_defaults_to_knob_and_keeps_lot_context():
    from routers import llm as llm_router

    split_view, table = llm_router._flowi_splittable_view_to_inline(
        {
            "product": "ML_TABLE_PRODA",
            "root_lot_id": "A1001",
            "headers": ["#1"],
            "header_groups": [{"label": "A1001.1", "span": 1}],
            "wafer_fab_list": ["A1001.1"],
            "row_labels": {"root_lot_id": "root_lot_id", "lot_id": "lot_id", "parameter": "항목"},
            "rows": [
                {"_param": "KNOB_GATE", "_display": "Gate", "_cells": {"0": {"actual": "A", "plan": ""}}},
                {"_param": "MASK_GATE", "_display": "Mask", "_cells": {"0": {"actual": "M", "plan": ""}}},
                {"_param": "FAB_STATE", "_display": "Fab", "_cells": {"0": {"actual": "RUN", "plan": ""}}},
            ],
        },
        max_rows=12,
    )

    assert [row["parameter"] for row in split_view["rows"]] == ["KNOB_GATE"]
    assert split_view["row_label"] == "KNOB"
    assert split_view["root_lot_id"] == "A1001"
    assert split_view["lot_id_label"] == "A1001.1"
    assert table["rows"][0]["lot_id"] == "A1001.1"


def test_flowi_splittable_inline_counts_blank_knob_cells_as_displayable():
    from routers import llm as llm_router

    split_view, table = llm_router._flowi_splittable_view_to_inline(
        {
            "product": "ML_TABLE_PRODA",
            "root_lot_id": "A1001",
            "headers": ["#1", "#2"],
            "header_groups": [{"label": "A1001.1", "span": 2}],
            "wafer_fab_list": ["A1001.1", "A1001.1"],
            "rows": [
                {
                    "_param": "KNOB_GATE",
                    "_display": "Gate",
                    "_cells": {
                        "0": {"actual": "", "plan": "", "key": "k0"},
                        "1": {"actual": "", "plan": "", "key": "k1"},
                    },
                },
            ],
        },
        max_rows=12,
    )

    assert split_view["rows"]
    assert split_view["total"] == 2
    assert table["total"] == 2
    assert table["rows"][0]["parameter"] == "KNOB_GATE"
    assert table["rows"][0]["cell_key"] == "k0"


def test_flowi_chat_explicit_splittable_view_uses_fast_path(monkeypatch, tmp_path):
    from core import flowi_units, home_memory, home_orchestrator
    from routers import llm as llm_router

    monkeypatch.setattr(home_memory, "MEMORY_FILE", tmp_path / "home_memory.jsonl")
    monkeypatch.setattr(llm_router, "_append_user_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(llm_router, "_allowed_flowi_feature_keys", lambda _me: {"splittable"})
    monkeypatch.setattr(llm_router.llm_adapter, "is_available", lambda: False)
    monkeypatch.setattr(flowi_units, "try_dispatch", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unit dispatcher should not run")))
    monkeypatch.setattr(llm_router, "_handle_flowi_query", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("generic router should not run")))
    monkeypatch.setattr(
        home_orchestrator,
        "record_flowi_runtime_run",
        lambda *_args, **_kwargs: {"run_id": "pytest-fast-split", "graph": {"nodes": [], "edges": []}, "status": "success"},
    )

    calls = []

    def fake_split_handler(prompt, product, max_rows):
        calls.append((prompt, product, max_rows))
        return {
            "handled": True,
            "intent": "splittable_view",
            "action": "query_splittable_view",
            "feature": "splittable",
            "answer": "SplitTable fast path ok",
            "filters": {"product": "ML_TABLE_PRODA", "root_lot_ids": ["A1001"]},
            "split_view": {"kind": "splittable_view", "rows": []},
        }

    monkeypatch.setattr(llm_router, "_handle_wafer_split_at_step", fake_split_handler)

    result = llm_router._run_flowi_chat(
        prompt="PRODA A1001 스플릿테이블 보여줘",
        product="",
        max_rows=12,
        me={"username": "alice", "role": "admin"},
        agent_context={},
    )

    assert result["answer"] == "SplitTable fast path ok"
    assert result["tool"]["action"] == "query_splittable_view"
    assert calls == [("PRODA A1001 스플릿테이블 보여줘", "PRODA", 12)]


def test_flowi_chat_explicit_splittable_view_asks_product_when_missing(monkeypatch, tmp_path):
    from core import flowi_units, home_memory, home_orchestrator
    from routers import llm as llm_router

    monkeypatch.setattr(home_memory, "MEMORY_FILE", tmp_path / "home_memory.jsonl")
    monkeypatch.setattr(llm_router, "_append_user_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(llm_router, "_allowed_flowi_feature_keys", lambda _me: {"splittable", "filebrowser", "dashboard"})
    monkeypatch.setattr(llm_router.llm_adapter, "is_available", lambda: False)
    monkeypatch.setattr(
        flowi_units,
        "try_dispatch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unit dispatcher should not run")),
    )
    monkeypatch.setattr(
        llm_router,
        "_handle_semantic_measurement",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("measurement lookup should not run")),
    )
    monkeypatch.setattr(
        llm_router,
        "_handle_wafer_split_at_step",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("split view should wait for product")),
    )
    monkeypatch.setattr(
        home_orchestrator,
        "record_flowi_runtime_run",
        lambda *_args, **_kwargs: {"run_id": "pytest-split-product-clarify", "graph": {"nodes": [], "edges": []}, "status": "waiting"},
    )

    result = llm_router._run_flowi_chat(
        prompt="A1001 스플릿테이블 보여줘",
        product="",
        max_rows=12,
        me={"username": "alice", "role": "admin"},
        agent_context={},
    )

    assert result["tool"]["action"] == "clarify_product"
    assert result["needs_input"] is True
    assert result["missing"] == ["product"]
    assert result["pending_prompt"] == "A1001 스플릿테이블 보여줘"
    assert "product" in result["question"].lower()


def test_flowi_chat_product_followup_resumes_pending_splittable_view(monkeypatch, tmp_path):
    from core import flowi_units, home_memory, home_orchestrator
    from routers import llm as llm_router

    pending_prompt = "A1002 1.0 STI Split(or Knob) show"
    calls = []

    monkeypatch.setattr(home_memory, "MEMORY_FILE", tmp_path / "home_memory.jsonl")
    monkeypatch.setattr(llm_router, "_append_user_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(llm_router, "_allowed_flowi_feature_keys", lambda _me: {"splittable", "filebrowser", "dashboard"})
    monkeypatch.setattr(llm_router.llm_adapter, "is_available", lambda: False)
    monkeypatch.setattr(
        flowi_units,
        "try_dispatch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unit dispatcher should not run")),
    )
    monkeypatch.setattr(
        llm_router,
        "_handle_semantic_measurement",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("measurement lookup should not run")),
    )
    monkeypatch.setattr(
        home_orchestrator,
        "record_flowi_runtime_run",
        lambda *_args, **_kwargs: {"run_id": "pytest-split-product-followup", "graph": {"nodes": [], "edges": []}, "status": "success"},
    )

    def fake_split_handler(prompt, product, max_rows):
        calls.append((prompt, product, max_rows))
        return {
            "handled": True,
            "intent": "splittable_view",
            "action": "query_splittable_view",
            "feature": "splittable",
            "answer": "SplitTable resumed with product",
            "filters": {"product": "ML_TABLE_PRODA", "root_lot_ids": ["A1002"]},
            "split_view": {"kind": "splittable_view", "rows": []},
        }

    monkeypatch.setattr(llm_router, "_handle_wafer_split_at_step", fake_split_handler)

    result = llm_router._run_flowi_chat(
        prompt="PRODA",
        product="",
        max_rows=12,
        me={"username": "alice", "role": "admin"},
        agent_context={
            "messages": [
                {"role": "user", "prompt": pending_prompt, "text": pending_prompt},
                {
                    "role": "assistant",
                    "prompt": pending_prompt,
                    "feature": "splittable",
                    "intent": "splittable_view",
                    "action": "clarify_product",
                    "missing": ["product"],
                    "pending_prompt": pending_prompt,
                },
            ],
        },
    )

    assert result["answer"] == "SplitTable resumed with product"
    assert result["tool"]["action"] == "query_splittable_view"
    assert calls == [(pending_prompt + "\nproduct: PRODA", "PRODA", 12)]


def test_flowi_chat_product_followup_keeps_bare_alpha_splittable_prompt(monkeypatch, tmp_path):
    from core import flowi_units, home_memory, home_orchestrator
    from routers import llm as llm_router

    pending_prompt = "AZAAA Split table 보여줘"
    calls = []

    monkeypatch.setattr(home_memory, "MEMORY_FILE", tmp_path / "home_memory.jsonl")
    monkeypatch.setattr(llm_router, "_append_user_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(llm_router, "_allowed_flowi_feature_keys", lambda _me: {"splittable", "filebrowser", "dashboard"})
    monkeypatch.setattr(llm_router.llm_adapter, "is_available", lambda: False)
    monkeypatch.setattr(
        flowi_units,
        "try_dispatch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unit dispatcher should not run")),
    )
    monkeypatch.setattr(
        llm_router,
        "_handle_semantic_measurement",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("measurement lookup should not run")),
    )
    monkeypatch.setattr(
        llm_router,
        "_handle_flowi_query",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("generic router should not run")),
    )
    monkeypatch.setattr(
        home_orchestrator,
        "record_flowi_runtime_run",
        lambda *_args, **_kwargs: {"run_id": "pytest-split-alpha-followup", "graph": {"nodes": [], "edges": []}, "status": "success"},
    )

    def fake_split_handler(prompt, product, max_rows):
        calls.append((prompt, product, max_rows))
        return {
            "handled": True,
            "intent": "splittable_view",
            "action": "query_splittable_view",
            "feature": "splittable",
            "answer": "SplitTable resumed with product",
            "filters": {"product": "ML_TABLE_PRODA", "root_lot_ids": ["AZAAA"]},
            "split_view": {"kind": "splittable_view", "rows": []},
        }

    monkeypatch.setattr(llm_router, "_handle_wafer_split_at_step", fake_split_handler)

    result = llm_router._run_flowi_chat(
        prompt="product: PRODA",
        product="",
        max_rows=12,
        me={"username": "alice", "role": "admin"},
        agent_context={
            "messages": [
                {"role": "user", "prompt": pending_prompt, "text": pending_prompt},
                {
                    "role": "assistant",
                    "prompt": pending_prompt,
                    "feature": "splittable",
                    "intent": "splittable_view",
                    "action": "clarify_product",
                    "missing": ["product"],
                    "pending_prompt": pending_prompt,
                },
            ],
        },
    )

    assert result["answer"] == "SplitTable resumed with product"
    assert result["tool"]["action"] == "query_splittable_view"
    assert calls == [(pending_prompt + "\nproduct: PRODA", "PRODA", 12)]


def test_flowi_chat_explicit_splittable_view_preempts_measurement_lookup(monkeypatch, tmp_path):
    from core import flowi_units, home_memory, home_orchestrator
    from routers import llm as llm_router

    monkeypatch.setattr(home_memory, "MEMORY_FILE", tmp_path / "home_memory.jsonl")
    monkeypatch.setattr(llm_router, "_append_user_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(llm_router, "_allowed_flowi_feature_keys", lambda _me: {"splittable", "filebrowser", "dashboard"})
    monkeypatch.setattr(llm_router.llm_adapter, "is_available", lambda: False)
    monkeypatch.setattr(
        flowi_units,
        "try_dispatch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unit dispatcher should not run")),
    )
    monkeypatch.setattr(
        llm_router,
        "_handle_semantic_measurement",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("measurement lookup should not run")),
    )
    monkeypatch.setattr(
        llm_router,
        "_handle_flowi_query",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("generic router should not run")),
    )
    monkeypatch.setattr(
        home_orchestrator,
        "record_flowi_runtime_run",
        lambda *_args, **_kwargs: {"run_id": "pytest-fast-split-korean", "graph": {"nodes": [], "edges": []}, "status": "success"},
    )

    calls = []

    def fake_split_handler(prompt, product, max_rows):
        calls.append((prompt, product, max_rows))
        return {
            "handled": True,
            "intent": "splittable_view",
            "action": "query_splittable_view",
            "feature": "splittable",
            "answer": "SplitTable fast path ok",
            "filters": {"product": "ML_TABLE_PRODA", "root_lot_ids": ["A1001"]},
            "split_view": {"kind": "splittable_view", "rows": []},
        }

    monkeypatch.setattr(llm_router, "_handle_wafer_split_at_step", fake_split_handler)

    result = llm_router._run_flowi_chat(
        prompt="PRODA A1001 스플릿테이블 보여줘",
        product="",
        max_rows=12,
        me={"username": "alice", "role": "admin"},
        agent_context={},
    )

    assert result["answer"] == "SplitTable fast path ok"
    assert result["tool"]["action"] == "query_splittable_view"
    assert calls == [("PRODA A1001 스플릿테이블 보여줘", "PRODA", 12)]


def test_flowi_chat_product_name_fab_lot_splittable_prompt_uses_view(monkeypatch, tmp_path):
    from core import flowi_units, home_memory, home_orchestrator
    from routers import llm as llm_router

    prompt = "제품명 AZAAA.1 스플릿테이블 보여줘"
    calls = []
    preview = {
        "selected_function": {"name": "query_splittable_view", "feature": "splittable", "intent": "splittable_view"},
        "function_call": {"function": {"arguments": {"product": "", "root_lot_ids": ["AZAAA"], "fab_lot_ids": ["AZAAA.1"], "wafer_ids": []}}},
        "validation": {"missing": ["product"]},
    }

    monkeypatch.setattr(home_memory, "MEMORY_FILE", tmp_path / "home_memory.jsonl")
    monkeypatch.setattr(llm_router, "_append_user_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(llm_router, "_allowed_flowi_feature_keys", lambda _me: {"splittable", "filebrowser", "dashboard"})
    monkeypatch.setattr(llm_router.llm_adapter, "is_available", lambda: False)
    monkeypatch.setattr(
        flowi_units,
        "try_dispatch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unit dispatcher should not run")),
    )
    monkeypatch.setattr(
        llm_router,
        "_handle_semantic_measurement",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("measurement lookup should not run")),
    )
    monkeypatch.setattr(
        llm_router,
        "_handle_flowi_query",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("generic router should not run")),
    )
    monkeypatch.setattr(
        home_orchestrator,
        "record_flowi_runtime_run",
        lambda *_args, **_kwargs: {"run_id": "pytest-fast-split-product-name", "graph": {"nodes": [], "edges": []}, "status": "success"},
    )
    monkeypatch.setattr(llm_router, "_structure_flowi_function_call", lambda *_args, **_kwargs: preview)
    monkeypatch.setattr(
        llm_router,
        "_resolve_products_for_lots",
        lambda *_args, **_kwargs: [{"product": "PRODA", "sources": "ML_TABLE", "lots": "AZAAA.1", "row_count": 10}],
    )

    def fake_view(args, product_hint, prompt_arg, max_rows_arg):
        calls.append((dict(args), product_hint, prompt_arg, max_rows_arg))
        return {
            "handled": True,
            "intent": "splittable_view",
            "action": "query_splittable_view",
            "feature": "splittable",
            "answer": "SplitTable product-name prompt ok",
            "filters": {"product": "ML_TABLE_PRODA", "root_lot_ids": ["AZAAA"]},
            "split_view": {"kind": "splittable_view", "rows": []},
        }

    monkeypatch.setattr(llm_router, "_flowi_query_splittable_view_tool", fake_view)

    result = llm_router._run_flowi_chat(
        prompt=prompt,
        product="",
        max_rows=12,
        me={"username": "alice", "role": "admin"},
        agent_context={},
    )

    assert result["answer"] == "SplitTable product-name prompt ok"
    assert result["tool"]["action"] == "query_splittable_view"
    assert calls == [({"product": "PRODA", "root_lot_ids": ["AZAAA"], "fab_lot_ids": ["AZAAA.1"], "wafer_ids": []}, "PRODA", prompt, 12)]


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


def test_flowi_chat_knob_rulebook_filters_exact_feature_name(monkeypatch, tmp_path):
    (tmp_path / "ppid_knob.csv").write_text(
        "feature_name,function_step,rule_order,operator,category,ppid\n"
        "1.6.0 LDD,LDD_RULE,10,eq,CAT_LDD,PPID_LDD\n"
        "11.6.0 LDD,OTHER_RULE,20,eq,CAT_OTHER,PPID_OTHER\n"
        "6.0 LDD,PARTIAL_RULE,30,eq,CAT_PARTIAL,PPID_PARTIAL\n",
        encoding="utf-8",
    )

    result = _run_step_mapping_flowi_chat(monkeypatch, tmp_path, "1.6.0 LDD Knob 어떻게 룰 구성되어있어?")

    assert result["tool"]["intent"] == "knob_rulebook_lookup"
    assert result["tool"]["action"] == "query_knob_rulebook_rows"
    rows = result["tool"]["table"]["rows"]
    assert [row["feature_name"] for row in rows] == ["1.6.0 LDD"]
    assert rows[0]["function_step"] == "LDD_RULE"
    assert result["tool"]["filters"]["search_conditions"]["feature_name"] == ["1.6.0 LDD"]


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
