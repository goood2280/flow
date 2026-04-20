"""core/product_config.py v7.3 — Per-product YAML configuration.

Stores process_id, canonical KNOB list, target specs, owner, etc. — exactly the
metadata the remote compute system expects to see alongside reformatters.

YAML because the team already maintains YAMLs for other systems. We parse with
a tiny hand-written reader (no dependency) that covers flat dict + list syntax.
If PyYAML is installed we use it for nested structures.
"""
from __future__ import annotations
import json
import logging
import re
from pathlib import Path
from typing import Dict, Any, List

logger = logging.getLogger("holweb.product_config")

try:
    import yaml as _yaml
    _HAS_YAML = True
except Exception:
    _yaml = None
    _HAS_YAML = False


SCHEMA = {
    "product": "str — PRODUCT_A / PRODUCT_B",
    "process_id": "str — canonical process generation id",
    "description": "str — free text",
    "owner": "str — engineer in charge",
    "canonical_knobs": "list[str] — KNOB names this product tracks",
    "canonical_inline_items": "list[str] — INLINE canonical items in scope",
    "et_key_items": "list[str] — ET item_ids considered performance-critical",
    "yld_metric": "str — primary YLD column to optimize (default YIELD)",
    "perf_metric": "str — primary performance metric (e.g. ET item_id of interest)",
    "target_spec": "dict[str, [lsl, usl, target]] — per-item spec limits",
    "measured_shots": "list[[x,y]] — ET/INLINE covered shot grid (for edge detection)",
}

TEMPLATE = {
    "product": "PRODUCT_X",
    "process_id": "1Z_MAIN",
    "description": "Template — replace with real values",
    "owner": "",
    "canonical_knobs": ["KNOB_RECIPE", "KNOB_TOOL", "KNOB_DOSE"],
    "canonical_inline_items": ["CD_POLY", "THK_OX", "OCD_SLOPE"],
    "et_key_items": ["VTH", "IDSAT", "LEAKAGE"],
    "yld_metric": "YIELD",
    "perf_metric": "VTH",
    "target_spec": {
        "VTH": [0.3, 0.8, 0.55],
        "IDSAT": [40.0, 80.0, 60.0],
    },
    "measured_shots": [[-2, -2], [-2, -1], [-2, 0], [-2, 1], [-2, 2],
                        [-1, -2], [-1, -1], [-1, 0], [-1, 1], [-1, 2],
                        [0, -2], [0, -1], [0, 0], [0, 1], [0, 2],
                        [1, -2], [1, -1], [1, 0], [1, 1], [1, 2],
                        [2, -2], [2, -1], [2, 0], [2, 1], [2, 2]],
}


def _dump_yaml(obj: Any) -> str:
    if _HAS_YAML:
        return _yaml.safe_dump(obj, allow_unicode=True, sort_keys=False)
    # Fallback: emit JSON-compatible YAML-ish (YAML is a superset of JSON for dicts/lists)
    return json.dumps(obj, indent=2, ensure_ascii=False)


def _load_yaml(text: str) -> Dict[str, Any]:
    if _HAS_YAML:
        try:
            d = _yaml.safe_load(text)
            return d if isinstance(d, dict) else {}
        except Exception as e:
            logger.warning(f"yaml parse error: {e}")
            return {}
    # Fallback: only JSON subset
    try:
        d = json.loads(text)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────
def config_path(root: Path, product: str) -> Path:
    safe = "".join(c for c in product if c.isalnum() or c in "_-") or "UNKNOWN"
    return root / "product_config" / f"{safe}.yaml"


def load(root: Path, product: str) -> Dict[str, Any]:
    fp = config_path(root, product)
    if not fp.exists():
        return {}
    return _load_yaml(fp.read_text(encoding="utf-8"))


def save(root: Path, product: str, data: Dict[str, Any]) -> None:
    fp = config_path(root, product)
    fp.parent.mkdir(parents=True, exist_ok=True)
    # Keep product key in sync with filename
    data = dict(data); data["product"] = product
    fp.write_text(_dump_yaml(data), encoding="utf-8")


def list_products(root: Path) -> List[Dict[str, Any]]:
    d = root / "product_config"
    if not d.exists():
        return []
    out = []
    for fp in sorted(d.glob("*.yaml")):
        cfg = _load_yaml(fp.read_text(encoding="utf-8"))
        out.append({
            "product": fp.stem,
            "process_id": cfg.get("process_id", ""),
            "owner": cfg.get("owner", ""),
            "knob_count": len(cfg.get("canonical_knobs", []) or []),
            "et_key_count": len(cfg.get("et_key_items", []) or []),
            "has_spec": bool(cfg.get("target_spec")),
            "size": fp.stat().st_size,
        })
    return out


def validate(data: Dict[str, Any]) -> List[str]:
    errs = []
    if not data.get("product"): errs.append("'product' required")
    if not data.get("process_id"): errs.append("'process_id' required")
    ts = data.get("target_spec")
    if ts and not isinstance(ts, dict): errs.append("'target_spec' must be a dict")
    return errs
