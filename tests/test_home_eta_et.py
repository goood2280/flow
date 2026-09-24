"""홈 ETA / ET DATA / ET 측정시간 fastpath 검증 (실측 DB + 격리)."""
import pytest

from core import home_orchestrator


@pytest.fixture
def no_side_effects(monkeypatch):
    monkeypatch.setattr(home_orchestrator, "_react_loop_enabled", lambda: False)
    monkeypatch.setattr(home_orchestrator, "_llm_planner_enabled", lambda: False)
    monkeypatch.setattr(
        home_orchestrator, "build_home_runtime_snapshot",
        lambda **kwargs: {"run_id": "test", "graph": {}, "action_log": {}, "status": "ok"},
    )
    monkeypatch.setattr(home_orchestrator.home_memory, "remember_turn", lambda **kwargs: None)


def _orch(prompt, **kwargs):
    return home_orchestrator.orchestrate(
        prompt, user={"username": "t", "role": "admin"}, **kwargs)


def test_eta_step_desc_proposes_confirmation(no_side_effects):
    out = _orch("PRODA A1000A.3 FINAL INSPECTION 언제 도착해?")
    assert out["meta"]["planner"] == "fastpath:eta_forecast_ask"
    clar = out["tool"]["clarification"]
    assert clar["kind"] == "eta_step"
    assert clar["options"], "step 후보 선택지가 비어 있음"
    assert "AA100600" in clar["options"][0]["label"]
    assert "AA100600" in clar["options"][0]["value"]
    assert out["tool"]["missing"] == ["target_step_id"]


def test_eta_step_id_goes_to_tracker(no_side_effects):
    out = _orch("PRODA A1000A.3 AA100600에 언제도착해?")
    assert out["meta"]["planner"] == "fastpath:eta_forecast"
    assert out["tool"]["feature"] == "eta"
    # 실측 FAB 이력 없음 → lot_history 안내 (정상 분기)
    assert out["tool"].get("missing") == ["lot_history"]
    assert out["context"]["eta_query"]["lot_id"] == "A1000A.3"


def test_et_download_scope_hitl(no_side_effects):
    out = _orch("ET DATA PRODA 뽑아줘")
    assert out["meta"]["planner"] == "fastpath:et_download_ask"
    clar = out["tool"]["clarification"]
    assert clar["kind"] == "et_scope"
    assert len(clar["options"]) == 4
    followup = clar["options"][1]["value"]
    assert "__ET_DAYS=5" in followup


def test_et_download_product_hitl(no_side_effects):
    out = _orch("ET DATA 뽑고싶어")
    assert out["meta"]["planner"] == "fastpath:et_download_ask"
    assert out["tool"]["clarification"]["kind"] == "product"
    assert out["tool"]["clarification"]["options"]


def test_et_download_registers_job(no_side_effects, monkeypatch):
    from routers import reformatize as rf
    calls = {}

    def fake_start(req, user):
        calls["req"] = req
        return {"job_id": "job-1", "status": "queued"}
    monkeypatch.setattr(rf, "download_start", fake_start)
    out = _orch("ET DATA PRODA VTH_IDX 최근 5일치 뽑아줘")
    assert out["meta"]["planner"] == "fastpath:et_download"
    assert out["tool"]["download_job"]["job_id"] == "job-1"
    assert calls["req"].days == 5
    assert calls["req"].items == ["VTH_IDX"]


def test_et_time_measure(no_side_effects):
    out = _orch("PRODA A1020 ET측정시간 보여줘")
    assert out["meta"]["planner"] == "fastpath:et_time"
    assert out["tool"]["feature"] == "ettime"
    assert out["tool"]["table"]["total"] > 0
    assert "step_id" in out["tool"]["table"]["columns"]


def test_et_time_trend_chart(no_side_effects):
    out = _orch("PRODA ET측정시간 추이 보여줘")
    assert out["meta"]["planner"] == "fastpath:et_time"
    chart = out["tool"].get("chart_result") or {}
    assert chart.get("points"), "추이 포인트가 비어 있음"
    assert chart.get("color_by") == "series"
    assert out["tool"]["table"]["total"] > 0
