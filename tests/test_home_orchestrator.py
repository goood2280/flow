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
    out = home_orchestrator.orchestrate("ET 차트로 그려줘")
    assert out["ok"] is True
    assert len(out["trace"]) >= 1
    signals = out["meta"]["signals"]
    assert "chart" in signals or "dashboard" in signals or "ettime" in signals


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
