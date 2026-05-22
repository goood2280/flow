from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app_v2.modules.agent_runtime import build_runtime_blueprint, resolve_semantic_frame  # noqa: E402
from app_v2.runtime.security import QUERY_TOKEN_PREFIXES  # noqa: E402


def test_agent_runtime_semantic_layer_resolves_core_terms():
    frame = resolve_semantic_frame("PRODA A1000 #21 현재 step과 KNOB 영향을 LangSmith trace로 보여줘")

    assert frame.intent in {"traceable_orchestration", "knob_analysis"}
    assert frame.slots["products"] == ["PRODA"]
    assert frame.slots["root_lot_ids"] == ["A1000"]
    assert frame.slots["wafer_ids"] == [21]
    assert any(c.canonical_alias in {"knob", "step_id", "wafer_id", "langsmith"} or c.normalized == "knob" for c in frame.candidates)
    assert frame.polars_profile["catalog_rows"] >= 1


def test_agent_runtime_blueprint_exposes_langgraph_langsmith_sse_contract():
    blueprint = build_runtime_blueprint()

    assert blueprint["ok"] is True
    assert blueprint["graph"]["stream_mode"] == "updates"
    assert ["semantic_layer", "task_planner"] in blueprint["graph"]["edges"]
    assert "stream" in blueprint["endpoints"]
    assert blueprint["endpoints"]["stream"].startswith("GET /api/agent/runtime/stream")
    assert any(agent["agent_id"] == "semantic_interpreter" for agent in blueprint["unit_agents"])
    assert "project" in blueprint["langsmith"]


def test_agent_runtime_sse_allows_query_token_auth_for_eventsource():
    assert "/api/agent/runtime/stream" in QUERY_TOKEN_PREFIXES
