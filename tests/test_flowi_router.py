from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from routers.llm import _handle_flowi_query, _matched_feature_entrypoints  # noqa: E402


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
