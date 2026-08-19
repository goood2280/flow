"""Mechanically split a legacy router at top-level Python statement boundaries.

The generated ``*.part.py`` files are executed by
``app_v2.runtime.module_parts`` in the original router module namespace.  This
keeps endpoint paths, global singletons, import order, and monkeypatch targets
unchanged while reducing merge conflicts during the app_v2 migration.

This command is intentionally explicit and refuses to split an already converted
router.  It is a migration utility, not a build step.
"""

from __future__ import annotations

import argparse
import ast
import json
from dataclasses import dataclass
from pathlib import Path


LOADER_MARKER = "FLOW_SPLIT_ROUTER_LOADER_V1"


@dataclass(frozen=True)
class PartSpec:
    name: str
    near_line: int


SPECS: dict[str, tuple[PartSpec, ...]] = {
    "filebrowser": (
        PartSpec("00_bootstrap_and_single_file_cache", 1),
        PartSpec("10_settings_and_rule_drafts", 1349),
        PartSpec("20_validation_and_versioning", 3066),
        PartSpec("30_catalog_and_cache_routes", 4756),
        PartSpec("40_browse_and_preview", 5729),
        PartSpec("50_query_language", 6545),
        PartSpec("60_view_and_metadata_routes", 10982),
        PartSpec("70_chart_builder_and_downloads", 12358),
        PartSpec("80_settings_edit_and_version_routes", 13043),
    ),
    "splittable": (
        PartSpec("00_bootstrap_and_view_cache", 1),
        PartSpec("10_overlays_notes_and_products", 2513),
        PartSpec("20_sources_schema_and_rules", 3698),
        PartSpec("30_custom_columns_and_matching", 6587),
        PartSpec("40_product_and_match_caches", 8187),
        PartSpec("50_admin_scans_and_memory", 10122),
        PartSpec("60_lot_candidates_and_timing", 13562),
        PartSpec("70_pivot_fab_and_view", 15648),
        PartSpec("80_plan_history_and_exports", 18517),
    ),
    "llm": (
        PartSpec("00_bootstrap_models_and_shared_helpers", 1),
        PartSpec("10_chart_and_multisource_tools", 4500),
        PartSpec("20_semantic_and_cache_tools", 9000),
        PartSpec("30_workboard_and_app_writes", 12594),
        PartSpec("40_domain_lookup_tools", 14395),
        PartSpec("50_data_analysis_tools", 16037),
        PartSpec("60_splittable_filebrowser_inform_tools", 18118),
        PartSpec("70_orchestration_and_trace", 20481),
        PartSpec("80_chat_runtime", 22910),
        PartSpec("90_public_api", 25228),
    ),
}


def _top_level_starts(source: str) -> list[int]:
    tree = ast.parse(source)
    starts: set[int] = set()
    for node in tree.body:
        line = int(node.lineno)
        decorators = getattr(node, "decorator_list", ())
        if decorators:
            line = min(line, *(int(decorator.lineno) for decorator in decorators))
        starts.add(line)
    return sorted(starts)


def _restore_split_source(project_root: Path, feature: str) -> None:
    """Rejoin only files created by this utility so a split can be corrected."""

    source_path = project_root / "backend" / "routers" / f"{feature}.py"
    parts_dir = project_root / "backend" / "app_v2" / "modules" / feature / "router_parts"
    manifest_path = parts_dir / "manifest.json"
    if LOADER_MARKER not in source_path.read_text(encoding="utf-8"):
        raise ValueError(f"router is not a generated split loader: {source_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    names = [str(row["file"]) for row in manifest.get("parts", [])]
    if not names:
        raise ValueError(f"split manifest has no parts: {manifest_path}")
    unexpected = {path.name for path in parts_dir.iterdir()} - set(names) - {"manifest.json"}
    if unexpected:
        raise ValueError(f"refusing to remove unexpected split files: {sorted(unexpected)}")
    source_path.write_text(
        "".join((parts_dir / name).read_text(encoding="utf-8") for name in names),
        encoding="utf-8",
    )
    for name in names:
        (parts_dir / name).unlink()
    manifest_path.unlink()
    parts_dir.rmdir()


def _resolved_specs(source: str, specs: tuple[PartSpec, ...]) -> list[tuple[PartSpec, int]]:
    starts = _top_level_starts(source)
    resolved: list[tuple[PartSpec, int]] = []
    used: set[int] = set()
    for index, spec in enumerate(specs):
        if index == 0:
            line = 1
        else:
            line = next((candidate for candidate in starts if candidate >= spec.near_line), 0)
            if not line:
                raise ValueError(f"no top-level boundary at or after line {spec.near_line}")
        if line in used:
            raise ValueError(f"duplicate resolved boundary line {line} for {spec.name}")
        used.add(line)
        resolved.append((spec, line))
    return resolved


def _loader_text(feature: str, parts_relative: str) -> str:
    return f'''"""Compatibility entrypoint for the split {feature} router source."""

# {LOADER_MARKER}
from pathlib import Path as _FlowPartPath

from app_v2.runtime.module_parts import execute_module_parts as _execute_module_parts
from app_v2.runtime.module_parts import ordered_part_paths as _ordered_part_paths

_FLOW_PARTS_DIR = _FlowPartPath(__file__).resolve().parents[1] / {parts_relative!r}
_execute_module_parts(globals(), _ordered_part_paths(_FLOW_PARTS_DIR))
del _execute_module_parts, _ordered_part_paths, _FlowPartPath
'''


def split_router(project_root: Path, feature: str) -> dict:
    if feature not in SPECS:
        raise ValueError(f"unknown feature: {feature}")
    source_path = project_root / "backend" / "routers" / f"{feature}.py"
    source = source_path.read_text(encoding="utf-8")
    if LOADER_MARKER in source:
        raise ValueError(f"router is already split: {source_path}")

    parts_dir = project_root / "backend" / "app_v2" / "modules" / feature / "router_parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    if any(parts_dir.iterdir()):
        raise ValueError(f"parts directory is not empty: {parts_dir}")

    lines = source.splitlines(keepends=True)
    resolved = _resolved_specs(source, SPECS[feature])
    manifest: list[dict] = []
    for index, (spec, start_line) in enumerate(resolved):
        end_line = resolved[index + 1][1] - 1 if index + 1 < len(resolved) else len(lines)
        target = parts_dir / f"{spec.name}.part.py"
        target.write_text("".join(lines[start_line - 1 : end_line]), encoding="utf-8")
        manifest.append({"file": target.name, "start_line": start_line, "end_line": end_line})

    manifest_path = parts_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps({"feature": feature, "source": str(source_path.relative_to(project_root)), "parts": manifest}, indent=2),
        encoding="utf-8",
    )
    relative = str(parts_dir.relative_to(project_root / "backend")).replace("\\", "/")
    source_path.write_text(_loader_text(feature, relative), encoding="utf-8")
    return {"feature": feature, "source": str(source_path), "parts_dir": str(parts_dir), "parts": manifest}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("features", nargs="+", choices=sorted(SPECS))
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--resplit", action="store_true", help="rejoin a generated split before applying current boundaries")
    args = parser.parse_args()
    if args.resplit:
        for feature in args.features:
            _restore_split_source(args.project_root.resolve(), feature)
    results = [split_router(args.project_root.resolve(), feature) for feature in args.features]
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
