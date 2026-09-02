import importlib
import json
from pathlib import Path

import pytest

from app_v2.runtime.module_parts import ordered_part_paths
from scripts.split_router_source import _top_level_starts


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROUTER_CONTRACTS = {
    "filebrowser": {"part_count": 9, "route_count": 52},
    "splittable": {"part_count": 9, "route_count": 92},
}


def test_parked_flowi_keeps_only_feature_neutral_llm_routes():
    module = importlib.import_module("routers.llm")
    paths = {route.path for route in module.router.routes}

    assert paths == {
        "/api/llm/error/explain",
        "/api/llm/status",
        "/api/llm/test",
        "/api/llm/dcop/summary",
    }
    assert not any("/flowi/" in path for path in paths)


@pytest.mark.parametrize(("feature", "contract"), ROUTER_CONTRACTS.items())
def test_split_router_keeps_public_entrypoint_and_route_contract(feature, contract):
    module = importlib.import_module(f"routers.{feature}")
    manifest_path = (
        PROJECT_ROOT / "backend" / "app_v2" / "modules" / feature / "router_parts" / "manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_names = tuple(part["file"] for part in manifest["parts"])

    assert module.__flow_source_parts__ == manifest_names
    assert len(manifest_names) == contract["part_count"]
    assert len(module.router.routes) == contract["route_count"]

    entrypoint = PROJECT_ROOT / "backend" / "routers" / f"{feature}.py"
    entry_source = entrypoint.read_text(encoding="utf-8")
    assert "FLOW_SPLIT_ROUTER_LOADER_V1" in entry_source
    assert len(entry_source.splitlines()) < 20


@pytest.mark.parametrize("feature", ROUTER_CONTRACTS)
def test_split_router_manifest_is_ordered_and_complete(feature):
    parts_dir = PROJECT_ROOT / "backend" / "app_v2" / "modules" / feature / "router_parts"
    manifest = json.loads((parts_dir / "manifest.json").read_text(encoding="utf-8"))
    rows = manifest["parts"]

    assert [path.name for path in ordered_part_paths(parts_dir)] == [row["file"] for row in rows]
    assert rows[0]["start_line"] == 1
    for previous, current in zip(rows, rows[1:]):
        assert previous["end_line"] + 1 == current["start_line"]
    for row in rows:
        source = (parts_dir / row["file"]).read_text(encoding="utf-8")
        compile(source, row["file"], "exec")


def test_router_split_boundary_includes_decorators_with_their_function():
    source = "value = 1\n\n@decorator\nasync def endpoint():\n    return value\n"

    assert _top_level_starts(source) == [1, 3]
