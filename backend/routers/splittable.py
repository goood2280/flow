"""Compatibility entrypoint for the split splittable router source."""

# FLOW_SPLIT_ROUTER_LOADER_V1
from pathlib import Path as _FlowPartPath

from app_v2.runtime.module_parts import execute_module_parts as _execute_module_parts
from app_v2.runtime.module_parts import ordered_part_paths as _ordered_part_paths

_FLOW_PARTS_DIR = _FlowPartPath(__file__).resolve().parents[1] / 'app_v2/modules/splittable/router_parts'
_execute_module_parts(globals(), _ordered_part_paths(_FLOW_PARTS_DIR))
del _execute_module_parts, _ordered_part_paths, _FlowPartPath
