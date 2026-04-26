from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl

from core.domain import classify_process_area
from core.paths import PATHS
from core.utils import load_json


CONTRACTS_FILE = PATHS.data_root / "api_contracts" / "standard_contracts.json"
MATCHING_STEP_FILE = PATHS.db_root / "matching_step.csv"
INLINE_STEP_MATCH_FILE = PATHS.db_root / "inline_step_match.csv"
VM_MATCH_FILE = PATHS.db_root / "vm_matching.csv"


DEFAULT_CONTRACTS: dict[str, Any] = {
    "FAB": {
        "required_columns": [
            "root_lot_id", "lot_id", "wafer_id", "line_id", "process_id", "step_id",
            "tkin_time", "tkout_time", "eqp_id", "chamber_id", "reticle_id", "ppid",
        ],
        "optional_columns": ["product", "fab_lot_id", "time", "eqp", "chamber"],
        "join_keys": ["root_lot_id", "wafer_id", "step_id"],
        "time_columns": ["tkin_time", "tkout_time"],
        "step_column": "step_id",
    },
    "ET": {
        "required_columns": [
            "root_lot_id", "lot_id", "wafer_id", "process_id", "step_id", "step_seq",
            "eqp_id", "probe_card", "tkin_time", "tkout_time", "flat_zone",
            "item_id", "shot_x", "shot_y", "value",
        ],
        "optional_columns": ["product", "fab_lot_id", "time", "flat", "request_id", "measure_group_id"],
        "join_keys": ["root_lot_id", "wafer_id", "step_id"],
        "time_columns": ["tkin_time", "tkout_time"],
        "step_column": "step_id",
    },
    "INLINE": {
        "required_columns": [
            "root_lot_id", "lot_id", "wafer_id", "process_id", "tkin_time", "tkout_time",
            "eqp_id", "subitem_id", "item_id", "value", "speclow", "target", "spechigh",
        ],
        "optional_columns": ["product", "fab_lot_id", "time", "shot_x", "shot_y", "step_id"],
        "join_keys": ["root_lot_id", "wafer_id", "item_id", "subitem_id"],
        "time_columns": ["tkin_time", "tkout_time"],
        "step_column": "",
    },
    "VM": {
        "required_columns": ["root_lot_id", "wafer_id", "step_id", "item_id", "value"],
        "optional_columns": ["fab_lot_id", "lot_id", "tkin_time", "tkout_time", "eqp_id"],
        "join_keys": ["root_lot_id", "wafer_id", "step_id"],
        "time_columns": ["tkin_time", "tkout_time"],
        "step_column": "step_id",
    },
    "EDS": {
        "required_columns": ["root_lot_id", "wafer_id", "shot_id", "chip_id", "value"],
        "optional_columns": ["fab_lot_id", "step_id", "bin", "x", "y"],
        "join_keys": ["root_lot_id", "wafer_id", "shot_id", "chip_id"],
        "time_columns": [],
        "step_column": "step_id",
    },
}


def load_contracts() -> dict[str, Any]:
    data = load_json(CONTRACTS_FILE, DEFAULT_CONTRACTS)
    return data if isinstance(data, dict) else dict(DEFAULT_CONTRACTS)


def contract_for(dataset: str) -> dict[str, Any]:
    key = str(dataset or "").upper()
    return dict(load_contracts().get(key) or DEFAULT_CONTRACTS.get(key) or {})


def validate_dataset_contract(columns: list[str], dataset: str) -> dict[str, Any]:
    cols = [str(c) for c in (columns or [])]
    present = set(cols)
    spec = contract_for(dataset)
    required = [str(c) for c in spec.get("required_columns") or []]
    optional = [str(c) for c in spec.get("optional_columns") or []]
    missing = [c for c in required if c not in present]
    join_keys = [c for c in (spec.get("join_keys") or []) if c in present]
    time_columns = [c for c in (spec.get("time_columns") or []) if c in present]
    return {
        "dataset": str(dataset or "").upper(),
        "ok": len(missing) == 0,
        "required_columns": required,
        "optional_columns": optional,
        "missing_required": missing,
        "present_join_keys": join_keys,
        "present_time_columns": time_columns,
        "coverage_ratio": round((len(required) - len(missing)) / max(1, len(required)), 4),
    }


def _load_step_mapping_table(dataset: str) -> pl.DataFrame | None:
    ds = str(dataset or "").upper()
    fp = {
        "FAB": MATCHING_STEP_FILE,
        "ET": MATCHING_STEP_FILE,
        "INLINE": INLINE_STEP_MATCH_FILE,
        "VM": VM_MATCH_FILE,
    }.get(ds)
    if not fp or not Path(fp).exists():
        return None
    try:
        return pl.read_csv(fp, infer_schema_length=500)
    except Exception:
        return None


def suggest_step_classification(dataset: str, product: str, step_ids: list[str]) -> dict[str, Any]:
    ds = str(dataset or "").upper()
    product_key = str(product or "")
    unique_steps = []
    seen = set()
    for raw in step_ids or []:
        step = str(raw or "").strip()
        if step and step not in seen:
            unique_steps.append(step)
            seen.add(step)

    table = _load_step_mapping_table(ds)
    mapped: list[dict[str, Any]] = []
    unresolved: list[str] = []
    if table is not None and not table.is_empty():
        prod_col = "product" if "product" in table.columns else None
        if prod_col and product_key:
            table = table.filter(pl.col(prod_col).cast(pl.Utf8, strict=False) == product_key)
        raw_col = "raw_step_id" if "raw_step_id" in table.columns else ("step_id" if "step_id" in table.columns else None)
        func_col = "canonical_step" if "canonical_step" in table.columns else ("func_step" if "func_step" in table.columns else None)
        module_col = "area" if "area" in table.columns else ("module" if "module" in table.columns else None)
        if raw_col and func_col:
            rows = table.select([
                pl.col(raw_col).cast(pl.Utf8, strict=False).alias("raw_step_id"),
                pl.col(func_col).cast(pl.Utf8, strict=False).alias("function_step"),
                (pl.col(module_col).cast(pl.Utf8, strict=False) if module_col else pl.lit(None, dtype=pl.Utf8)).alias("module"),
            ]).to_dicts()
            by_raw = {str(r["raw_step_id"]): r for r in rows if r.get("raw_step_id")}
            for step in unique_steps:
                hit = by_raw.get(step)
                if hit:
                    func = str(hit.get("function_step") or "")
                    mod = str(hit.get("module") or "") or (classify_process_area(func) or "")
                    mapped.append({
                        "step_id": step,
                        "function_step": func,
                        "module": mod,
                        "confidence": 1.0,
                        "strategy": "matching_table",
                    })
                else:
                    unresolved.append(step)
        else:
            unresolved = list(unique_steps)
    else:
        unresolved = list(unique_steps)

    heuristic_rows = []
    for step in unresolved:
        area = classify_process_area(step) or ""
        heuristic_rows.append({
            "step_id": step,
            "function_step": step,
            "module": area,
            "confidence": 0.35 if area else 0.1,
            "strategy": "heuristic_fallback",
        })
    mapped.extend(heuristic_rows)
    still_unresolved = [r["step_id"] for r in heuristic_rows if not r.get("module")]
    return {
        "dataset": ds,
        "product": product_key,
        "mapped": mapped,
        "unresolved": still_unresolved,
        "mapping_coverage": round((len(mapped) - len(still_unresolved)) / max(1, len(unique_steps)), 4),
    }
