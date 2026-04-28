from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from routers import llm as llm_router  # noqa: E402
from routers.llm import _handle_flowi_query, _matched_feature_entrypoints, _run_flowi_chat  # noqa: E402


def test_flowi_feature_router_matches_korean_splittable_alias():
    matches = _matched_feature_entrypoints("A10001 1.0 STI 스플릿테이블에서 plan actual 보여줘")

    assert matches
    assert matches[0]["key"] == "splittable"


def test_flowi_general_query_returns_deterministic_unit_action():
    out = _handle_flowi_query("A10001 1.0 STI 스플릿테이블에서 plan actual 보여줘", "", 12)

    assert out["handled"] is True
    assert out["intent"] == "splittable_guidance"
    assert out["action"] == "open_splittable"
    assert out["table"]["kind"] == "flowi_action_plan"
    assert out["slots"]["lots"] == ["A10001"]


def test_flowi_feature_router_prefers_tablemap_relation_terms():
    out = _handle_flowi_query("테이블맵 relation에서 inline item과 knob 연결 보여줘", "", 12)

    assert out["intent"] == "tablemap_guidance"
    assert out["action"] == "open_tablemap"


def test_flowi_feature_router_filters_by_allowed_tabs():
    matches = _matched_feature_entrypoints(
        "테이블맵 relation에서 inline item과 knob 연결 보여줘",
        allowed_keys={"splittable"},
    )

    assert not matches or matches[0]["key"] != "tablemap"


def test_flowi_agent_chat_accepts_codex_source_and_returns_web_actions(monkeypatch):
    seen = {}

    def fake_complete(prompt, **_kwargs):
        seen["prompt"] = prompt
        return {"ok": True, "text": "Codex 입력 기준으로 스플릿 테이블을 열고 plan/actual을 확인하세요."}

    monkeypatch.setattr(llm_router, "_append_user_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(llm_router, "_profile_context", lambda _username: "")
    monkeypatch.setattr(llm_router.llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(llm_router.llm_adapter, "complete", fake_complete)

    out = _run_flowi_chat(
        prompt="A10001 1.0 STI 스플릿테이블에서 plan actual 보여줘",
        product="",
        max_rows=12,
        me={"username": "codex_tester", "role": "admin"},
        source_ai="codex",
        client_run_id="codex-smoke-1",
        agent_context={"origin": "codex-test", "surface": "api"},
    )

    assert out["ok"] is True
    assert out["llm"]["used"] is True
    assert out["answer"].startswith("Codex 입력 기준")
    assert out["agent_api"]["received"] is True
    assert out["agent_api"]["source_ai"] == "codex"
    assert out["agent_api"]["auth_user"] == "codex_tester"
    assert any(a["type"] == "open_tab" and a["tab"] == "splittable" for a in out["agent_api"]["actions"])
    assert any(a["type"] == "flowi_unit_action" and a["action"] == "open_splittable" for a in out["agent_api"]["actions"])
    assert "외부 AI source: codex" in seen["prompt"]
    assert "codex-smoke-1" in seen["prompt"]
    assert "codex-test" in seen["prompt"]
