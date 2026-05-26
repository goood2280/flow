"""tests/test_tool_registry.py — v9.1 Step 1 회귀 검증.

목적: core/tool_registry.py 가 Unit AI 11개 + Function-call 16개 모두를
손실 없이 통합 카탈로그로 반환하는지, enabled toggle 이 다른 도구에
영향을 주지 않고 idempotent 한지 검증한다.

기존 flowi_units/registry, FLOWI_FUNCTION_FEW_SHOTS, _flowi_function_schema
는 손대지 않으므로 dispatch 회귀는 본 검증 범위 밖이다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# backend/ 를 sys.path 에 추가 — 다른 tests/* 가 따르는 패턴.
_FLOW_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _FLOW_ROOT / "backend"
for p in (_BACKEND, _FLOW_ROOT):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)


EXPECTED_UNIT_AI_KEYS = {
    "filebrowser", "meeting", "inform", "tracker", "dashboard",
    "splittable", "tablemap", "diagnosis", "calendar", "ettime", "waferlayout",
}

EXPECTED_FUNCTION_NAMES_MIN = {
    "query_current_fab_lot_from_fab_db",
    "preview_splittable_plan_update",
    "query_lot_knobs_from_ml_table",
    "compose_inform_module_mail",
    "register_inform_log",
    "preview_filebrowser_data",
    "search_filebrowser_schema",
    "query_wafer_split_at_step",
    "query_lot_current_step_from_progress_cache",
    "query_splittable_view",
    "find_lots_by_knob_value",
    "query_metric_at_step",
    "register_inform_walkthrough",
    "build_dashboard_metric_chart",
    "query_fab_progress",
    "query_tracker_lot_purpose",
}


def test_list_unit_ai_keys_match_registry():
    from core import tool_registry
    items = tool_registry.list_unit_ai_tools()
    keys = {it["name"] for it in items}
    missing = EXPECTED_UNIT_AI_KEYS - keys
    assert not missing, f"unit_ai 누락: {missing}"
    for it in items:
        assert it["kind"] == "unit_ai"
        assert it.get("title"), f"unit_ai {it['name']} title 비어 있음"
        assert "tags" in it
        assert isinstance(it.get("data_sources"), list)


def test_list_function_tools_cover_known_schema():
    from core import tool_registry
    items = tool_registry.list_function_tools()
    names = {it["name"] for it in items}
    missing = EXPECTED_FUNCTION_NAMES_MIN - names
    assert not missing, f"function schema 누락: {missing}"
    for it in items:
        assert it["kind"] == "function"
        assert it.get("title"), f"function {it['name']} title 비어 있음"
        assert isinstance(it.get("required_args"), list)
        assert isinstance(it.get("input_schema"), dict)
        assert isinstance(it.get("tags"), list)


def test_combined_catalog_has_at_least_27_items():
    from core import tool_registry
    items = tool_registry.list_tools(include_stats=False)
    assert len(items) >= 27, f"통합 카탈로그 항목 수 {len(items)} < 27"
    kinds = {it["kind"] for it in items}
    assert kinds == {"unit_ai", "function"}, f"예상 외 kind: {kinds}"


def test_get_tool_returns_full_detail_for_unit_ai():
    from core import tool_registry
    tool = tool_registry.get_tool("filebrowser")
    assert tool is not None
    assert tool["kind"] == "unit_ai"
    assert isinstance(tool.get("data_sources"), list)
    assert "semantic_bindings" in tool
    assert tool["management_flow"]["nodes"][0]["id"] == "trigger"
    assert isinstance(tool["knowledge_refs"]["wiki_doc_ids"], list)
    assert "count_30d" in tool
    assert "user_count_30d" in tool


def test_get_tool_returns_full_detail_for_function():
    from core import tool_registry
    tool = tool_registry.get_tool("query_current_fab_lot_from_fab_db")
    assert tool is not None
    assert tool["kind"] == "function"
    assert tool.get("description")
    assert isinstance(tool.get("input_schema"), dict)
    assert tool["management_flow"]["nodes"][2]["id"] == "execute"
    assert "required_args" in tool["knowledge_refs"]


def test_management_flow_is_stable_for_catalog_items():
    from core import tool_registry
    items = tool_registry.list_tools(include_stats=False)
    for item in items:
        flow = item.get("management_flow") or {}
        node_ids = [node.get("id") for node in flow.get("nodes") or []]
        assert node_ids == ["trigger", "guardrail", "execute", "evidence", "improve"]
        edges = flow.get("edges") or []
        assert edges[0] == {"from": "trigger", "to": "guardrail"}
        assert edges[-1] == {"from": "evidence", "to": "improve"}


def test_get_tool_missing_returns_none():
    from core import tool_registry
    assert tool_registry.get_tool("__does_not_exist__") is None


def test_enabled_toggle_isolation(tmp_path, monkeypatch):
    """enabled toggle 이 state 파일에만 영향을 주고, 다른 도구는 default True 유지."""
    from core import tool_registry
    from core.paths import PATHS

    # state 파일을 tmp 로 격리.
    fake_state = tmp_path / "tool_registry_state.json"
    monkeypatch.setattr(tool_registry, "STATE_FILE", fake_state)

    assert tool_registry.get_enabled("filebrowser") is True  # default
    tool_registry.set_enabled("filebrowser", False, by="pytest")
    assert tool_registry.get_enabled("filebrowser") is False
    assert tool_registry.get_enabled("meeting") is True  # 다른 도구 영향 없음

    data = json.loads(fake_state.read_text(encoding="utf-8"))
    assert data["tools"]["filebrowser"]["enabled"] is False
    assert data["tools"]["filebrowser"]["by"] == "pytest"


def test_tags_are_inferred_from_name():
    from core import tool_registry
    items = tool_registry.list_function_tools()
    by_name = {it["name"]: it for it in items}
    assert "read" in by_name["query_current_fab_lot_from_fab_db"]["tags"]
    assert "write" in by_name["register_inform_log"]["tags"]
    assert "chart" in by_name["build_dashboard_metric_chart"]["tags"]
    assert "search" in by_name["search_filebrowser_schema"]["tags"]


def test_all_tags_non_empty():
    from core import tool_registry
    tags = tool_registry.all_tags()
    assert len(tags) > 0
