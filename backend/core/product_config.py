"""core/product_config.py v7.4 — Product YAML configuration.

Stores process_id, canonical KNOB list, target specs, owner, etc. — exactly the
metadata the remote compute system expects to see alongside reformatters.

All products are stored in one master YAML file:

    product_config/products.yaml

The TableMap UI edits a selected product block, while Wafer Layout reads and
writes the same block through this module so layout changes stay in YAML.
"""
from __future__ import annotations
import json
import logging
import re
from pathlib import Path
from typing import Dict, Any, List

logger = logging.getLogger("flow.product_config")

MASTER_FILE = "products.yaml"

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
    "probe_card_watch": "dict — ET step_id/step_seq probe-card health watch rules",
    "wafer_layout": "dict — wafer/shot/chip/TEG geometry preset used by Wafer Layout tab",
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
    "probe_card_watch": {
        "enabled": True,
        "notify_admin": True,
        "items": [
            {
                "item_id": "PC_RES",
                "alias": "Probe Resistance",
                "step_ids": ["EA100010"],
                "step_seqs": ["0"],
                "spec": "usl",
                "usl": 0.22,
                "severity": "critical",
            },
            {
                "item_id": "PC_OPEN",
                "alias": "Probe Open",
                "step_ids": ["EA100010"],
                "step_seqs": ["0"],
                "spec": "usl",
                "usl": 0.08,
                "severity": "warn",
            },
        ],
    },
    "wafer_layout": {
        "waferRadius": 150,
        "wfCenterX": 0,
        "wfCenterY": 0,
        "refShotX": 0,
        "refShotY": 0,
        "refShotCenterX": 0,
        "refShotCenterY": 0,
        "shotPitchX": 28,
        "shotPitchY": 30,
        "shotSizeX": 27.2,
        "shotSizeY": 29.2,
        "scribeLaneX": 0.8,
        "scribeLaneY": 0.8,
        "edgeExclusionMm": 3,
        "chipCols": 3,
        "chipRows": 2,
        "chipOrigin": "shot_lower_left",
        "tegs": [
            {"no": 101, "name": "TEG_TOP", "x": 13.6, "y": 29.6},
            {"no": 102, "name": "TEG_RIGHT", "x": 27.6, "y": 14.6},
        ],
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
    try:
        return parse_text(text)
    except Exception as e:
        logger.warning(f"yaml parse error: {e}")
        return {}


def parse_text(text: str) -> Dict[str, Any]:
    """Parse a product YAML text into a dict.

    This public wrapper is used by TableMap/FileBrowser APIs that edit the raw
    YAML body.  It raises on invalid non-dict content so callers can return a
    useful validation error instead of silently saving `{}`.
    """
    if _HAS_YAML:
        d = _yaml.safe_load(text) if text.strip() else {}
    else:
        d = json.loads(text) if text.strip() else {}
    if d is None:
        return {}
    if not isinstance(d, dict):
        raise ValueError("product config YAML must be a mapping/object")
    return d


# ─────────────────────────────────────────────────────────────
def config_dir(root: Path) -> Path:
    return root / "product_config"


def config_path(root: Path, product: str = "") -> Path:
    return config_dir(root) / MASTER_FILE


def _template_for(product: str) -> Dict[str, Any]:
    data = json.loads(json.dumps(TEMPLATE))
    data["product"] = product
    return data


def _normalize_product_key(key: Any, cfg: Dict[str, Any]) -> str:
    return str(cfg.get("product") or key or "").strip()


def _normalize_products_blob(blob: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    if not isinstance(blob, dict):
        return {}
    raw = blob.get("products")
    if not isinstance(raw, dict):
        raw = blob
    out: Dict[str, Dict[str, Any]] = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        product = _normalize_product_key(key, value)
        if not product:
            continue
        item = dict(value)
        item["product"] = product
        out[product] = item
    return out


def _legacy_split_products(root: Path) -> Dict[str, Dict[str, Any]]:
    d = config_dir(root)
    if not d.exists():
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for fp in sorted(d.glob("*.y*ml")):
        if fp.name == MASTER_FILE:
            continue
        cfg = _load_yaml(fp.read_text(encoding="utf-8"))
        if not isinstance(cfg, dict):
            continue
        product = _normalize_product_key(fp.stem, cfg) or fp.stem
        cfg = dict(cfg)
        cfg["product"] = product
        out[product] = cfg
    return out


def load_all(root: Path) -> Dict[str, Dict[str, Any]]:
    fp = config_path(root)
    if fp.exists():
        return _normalize_products_blob(_load_yaml(fp.read_text(encoding="utf-8")))
    return _legacy_split_products(root)


def save_all(root: Path, products: Dict[str, Dict[str, Any]]) -> None:
    fp = config_path(root)
    fp.parent.mkdir(parents=True, exist_ok=True)
    normalized: Dict[str, Dict[str, Any]] = {}
    for key in sorted(products.keys(), key=lambda x: str(x).casefold()):
        value = products.get(key) or {}
        if not isinstance(value, dict):
            continue
        product = _normalize_product_key(key, value)
        if not product:
            continue
        item = dict(value)
        item["product"] = product
        normalized[product] = item
    fp.write_text(_dump_yaml({"products": normalized}), encoding="utf-8")


def load(root: Path, product: str) -> Dict[str, Any]:
    product = (product or "").strip()
    if not product:
        return {}
    products = load_all(root)
    if product in products:
        return dict(products[product])
    for key, value in products.items():
        if key.casefold() == product.casefold():
            return dict(value)
    return {}


def load_raw(root: Path, product: str) -> str:
    data = load(root, product)
    if not data:
        data = _template_for(product)
    return _dump_yaml(data)


def save(root: Path, product: str, data: Dict[str, Any]) -> None:
    product = (product or "").strip()
    if not product:
        raise ValueError("product required")
    products = load_all(root)
    item = dict(data or {})
    item["product"] = product
    products[product] = item
    save_all(root, products)


def save_raw(root: Path, product: str, text: str) -> Dict[str, Any]:
    product = (product or "").strip()
    data = parse_text(text)
    if isinstance(data.get("products"), dict):
        incoming = _normalize_products_blob(data)
        if product not in incoming:
            raise ValueError(f"products.{product} block required")
        products = load_all(root)
        products.update(incoming)
        save_all(root, products)
        return load(root, product)
    save(root, product, data)
    return load(root, product)


def list_products(root: Path) -> List[Dict[str, Any]]:
    fp = config_path(root)
    products = load_all(root)
    out = []
    size = fp.stat().st_size if fp.exists() else 0
    for product in sorted(products.keys(), key=lambda x: x.casefold()):
        cfg = products.get(product) or {}
        out.append({
            "product": product,
            "process_id": cfg.get("process_id", ""),
            "owner": cfg.get("owner", ""),
            "knob_count": len(cfg.get("canonical_knobs", []) or []),
            "et_key_count": len(cfg.get("et_key_items", []) or []),
            "has_spec": bool(cfg.get("target_spec")),
            "size": size,
        })
    return out


def validate(data: Dict[str, Any]) -> List[str]:
    errs = []
    if not data.get("product"): errs.append("'product' required")
    if not data.get("process_id"): errs.append("'process_id' required")
    ts = data.get("target_spec")
    if ts and not isinstance(ts, dict): errs.append("'target_spec' must be a dict")
    return errs
