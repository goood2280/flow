"""Build the active sample DB root under ``data/Fab``.

The app expects the demo DB to look close to the production datalake:

* root files at ``data/Fab`` such as ``ML_TABLE_*.parquet`` and matching CSVs
* long FAB/INLINE hive folders:
  ``1.RAWDATA_DB_FAB/<PROD>/date=YYYYMMDD/part_0.parquet``
* long ET flat folders:
  ``1.RAWDATA_DB_ET/<PROD>/<PROD>_YYYY-MM-DD.parquet``

This script preserves the top-level files and regenerates only raw roots. It
also ensures a few ET/DC step ids exist in ``step_matching.csv`` so Tracker
Analysis can show ``step_id > function_step`` instead of a bare ET flag.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import shutil
from pathlib import Path
from typing import Any

import polars as pl


HERE = Path(__file__).resolve().parent
PROJ = HERE.parent
BASE_SRC = HERE / "Base"
FAB_DST = HERE / "Fab"
ADMIN_SETTINGS = HERE / "flow-data" / "admin_settings.json"

RAW_ROOTS = ("1.RAWDATA_DB_FAB", "1.RAWDATA_DB_INLINE", "1.RAWDATA_DB_ET")
# Keep sample timestamps safely historical. ET flows in particular use latest
# package time for alert/report logic, so future-ish demo data is misleading.
OLD_DATE = "20240418"
NEW_DATE = "20240423"
OLD_DAY = dt.datetime(2024, 4, 18, 8, 0, 0)

FALLBACK_FAB_STEPS = [
    "AA100010",
    "AA100020",
    "AA100030",
    "AA100100",
    "AA100120",
    "AA100140",
    "AA200100",
    "AA200110",
    "AA300010",
    "AA300030",
    "AA600010",
    "AA600020",
    "AA900010",
    "AB100010",
    "AB100020",
]

ET_DC_STEPS = [
    ("ETA100010", "M0_DC", "ET DC M0 measure"),
    ("ETA100020", "M1_DC", "ET DC M1 measure"),
    ("ETA100030", "VIA_DC", "ET DC via/contact measure"),
]

PRODUCT_RENAMES = {
    "PRODUCT_A0": "PRODA0",
    "PRODUCT_A1": "PRODA1",
    "PRODUCT_B": "PRODB",
}


def _canonical_product(product: str) -> str:
    prod = str(product or "").strip().upper()
    if not prod:
        return ""
    if prod in PRODUCT_RENAMES:
        return PRODUCT_RENAMES[prod]
    return prod.replace("_", "")


def _reset_or_copy_root() -> None:
    base_files = [
        src for src in sorted(BASE_SRC.iterdir())
        if src.is_file() and src.suffix.lower() in (".parquet", ".csv", ".json", ".md", ".txt")
    ] if BASE_SRC.is_dir() else []
    if base_files:
        if FAB_DST.exists():
            for child in list(FAB_DST.iterdir()):
                if child.is_dir() and child.name.startswith("1.RAWDATA_DB"):
                    shutil.rmtree(child)
                elif child.is_file():
                    child.unlink(missing_ok=True)
        else:
            FAB_DST.mkdir(parents=True)
        copied: list[str] = []
        for src in base_files:
            shutil.copy2(src, FAB_DST / src.name)
            copied.append(src.name)
        print(f"[copy Base->Fab] {len(copied)} files")
        _normalize_seed_files()
        return

    if not FAB_DST.is_dir():
        raise SystemExit(f"Neither Base nor Fab exists: {BASE_SRC}, {FAB_DST}")
    for child in list(FAB_DST.iterdir()):
        if child.is_dir() and child.name.startswith("1.RAWDATA_DB"):
            shutil.rmtree(child)
    print("[note] Fab root reused; raw folders regenerated")


def _replace_product_tokens(text: str) -> str:
    out = text
    for old, new in PRODUCT_RENAMES.items():
        out = out.replace(old, new)
    return out


def _normalize_seed_files() -> None:
    """Normalize copied seed files to operating product names.

    Source fixtures still use PRODUCT_A0/PRODUCT_A1/PRODUCT_B. Flow's active
    demo root should look like the production naming style: PRODA0/PRODA1/PRODB.
    """
    for fp in sorted(FAB_DST.iterdir()):
        if not fp.is_file():
            continue
        suffix = fp.suffix.lower()
        if suffix in (".csv", ".json", ".md", ".txt"):
            try:
                raw = fp.read_text(encoding="utf-8")
                fixed = _replace_product_tokens(raw)
                if fixed != raw:
                    fp.write_text(fixed, encoding="utf-8")
            except Exception as e:
                print(f"[warn] product text normalize failed {fp.name}: {e}")
            continue
        if suffix == ".parquet":
            try:
                df = pl.read_parquet(fp)
                exprs = []
                for col, dtype in df.schema.items():
                    if dtype == pl.Utf8 or dtype == getattr(pl, "String", None):
                        expr = pl.col(col)
                        for old, new in PRODUCT_RENAMES.items():
                            expr = expr.str.replace_all(old, new, literal=True)
                        exprs.append(expr.alias(col))
                if exprs:
                    df.with_columns(exprs).write_parquet(fp)
            except Exception as e:
                print(f"[warn] product parquet normalize failed {fp.name}: {e}")


def _family_root(product: str) -> str:
    prod = _canonical_product(product)
    return "PRODA" if prod.startswith("PRODA") else "PRODB"


def _fab_lot_for(root_lot_id: str, wafer_id: Any) -> str:
    wid = _as_int(str(wafer_id).replace("W", ""), 1)
    letter = ["A", "B", "C"][(wid - 1) // 9 % 3]
    iteration = 1 if wid <= 17 else 2
    return f"{root_lot_id}{letter}.{iteration}"


def _ensure_ml_tables() -> None:
    """Create ML_TABLE_PRODA/B when the Base seed only has feature stores."""
    if list(FAB_DST.glob("ML_TABLE_*.parquet")):
        return
    et_fp = FAB_DST / "features_et_wafer.parquet"
    if not et_fp.is_file():
        return
    et = pl.read_parquet(et_fp)
    if et.is_empty():
        return
    inline_fp = FAB_DST / "features_inline_agg.parquet"
    if inline_fp.is_file():
        inline = pl.read_parquet(inline_fp)
        join_cols = [c for c in ("product", "lot_id", "wafer_id") if c in inline.columns and c in et.columns]
        if join_cols:
            et = et.join(inline, on=join_cols, how="left", suffix="_inline")

    et = et.with_columns(
        pl.col("product").cast(pl.Utf8, strict=False)
        .map_elements(_canonical_product, return_dtype=pl.Utf8)
        .alias("product")
    )

    roots = (
        et.select(["product", "root_lot_id"]).unique()
        .sort(["product", "root_lot_id"])
        .iter_rows(named=True)
    )
    counters = {"PRODA": 0, "PRODB": 0}
    root_map: dict[tuple[str, str], str] = {}
    for row in roots:
        family = _family_root(row["product"])
        counters[family] += 1
        prefix = "A" if family == "PRODA" else "B"
        root_map[(str(row["product"]), str(row["root_lot_id"]))] = f"{prefix}{counters[family]:04d}"

    rows: list[dict[str, Any]] = []
    for idx, row in enumerate(et.iter_rows(named=True)):
        product = _canonical_product(row.get("product") or "")
        old_root = str(row.get("root_lot_id") or "")
        new_root = root_map.get((product, old_root), old_root[:5])
        wafer_raw = str(row.get("wafer_id") or "")
        wafer_num = _as_int(wafer_raw.replace("W", ""), idx % 25 + 1)
        out = {
            "product": product,
            "root_lot_id": new_root,
            "lot_id": _fab_lot_for(new_root, wafer_num),
            "fab_lot_id": _fab_lot_for(new_root, wafer_num),
            "wafer_id": wafer_num,
            "KNOB_GATE_PPID": row.get("GATE_PROFILE_Split") or row.get("CHANNEL_RELEASE_TIME_Split") or "",
            "KNOB_ETCH_PPID": row.get("M1_CU_RECIPE_Split") or "",
            "KNOB_CVD_PPID": row.get("HKMG_WF_METAL_Split") or "",
            "KNOB_LITHO_PPID": row.get("SPACER_LOWK_K_Split") or "",
            "KNOB_SPACER_PPID": row.get("SPACER_LOWK_K_Split") or "",
            "KNOB_ANNEAL_RECIPE": row.get("CHANNEL_RELEASE_TIME_Split") or "",
            "KNOB_SD_EPI_RECIPE": row.get("SD_EPI_BORON_Split") or "",
            "FAB_EQP_GATE": row.get("eqp") or "",
            "FAB_EQP_ETCH": row.get("eqp") or "",
            "FAB_EQP_CVD": f"CVD-{(idx % 5) + 1:02d}",
            "FAB_EQP_CMP": f"CMP-{(idx % 4) + 1:02d}",
            "FAB_CHAMBER": row.get("chamber") or "",
            "FAB_SLOT": f"{wafer_num:02d}",
            "FAB_PPID_EXPERIMENT": row.get("pgm") or "",
            "INLINE_CD_GATE_MEAN": row.get("CD_3.3M_PC_FIN_CD_MEAS_mean"),
            "INLINE_CD_GATE_STD": row.get("CD_3.3M_PC_FIN_CD_MEAS_std"),
            "INLINE_CD_SPACER_MEAN": row.get("CD_3.3M_PC_FIN_CD_MEAS_p90"),
            "INLINE_CD_SPACER_STD": row.get("CD_3.3M_PC_FIN_CD_MEAS_p10"),
            "INLINE_TOX_M1_MEAN": row.get("THK_9.3M_M1_THK_MEAS_mean"),
            "INLINE_TOX_M1_STD": row.get("THK_9.3M_M1_THK_MEAS_std"),
            "INLINE_METAL_RES_M1": row.get("RS_8.4M_CT_RS_MEAS_mean"),
            "INLINE_METAL_RES_M2": row.get("RS_8.4M_CT_RS_MEAS_p90"),
            "INLINE_OVL_X": row.get("OVL_10.2M_M2_OVL_M1_mean"),
            "INLINE_OVL_Y": row.get("OVL_12.1M_M4_OVL_M3_mean"),
            "ET_RC_MEAN": row.get("Rc"),
            "ET_RCH_MEAN": row.get("Rch"),
            "ET_VTH_N": row.get("Vth_n"),
            "ET_VTH_P": row.get("Vth_p"),
            "ET_ION_N": row.get("Ion_n"),
            "ET_ION_P": row.get("Ion_p"),
            "YIELD_SCORE": max(0.0, min(100.0, 92.0 + _as_float(row.get("Ion_n_zscore"), 0.0) * 2.0)),
        }
        rows.append(out)

    all_df = pl.DataFrame(rows)
    for family in ("PRODA", "PRODB"):
        sub = all_df.filter(pl.col("product").map_elements(lambda p: _family_root(p) == family, return_dtype=pl.Boolean))
        if sub.is_empty():
            continue
        fp = FAB_DST / f"ML_TABLE_{family}.parquet"
        sub.write_parquet(fp)
        print(f"[ml_table] {fp.name} rows={sub.height} roots={sub['root_lot_id'].n_unique()}")


def _ensure_step_matching_csv() -> None:
    fp = FAB_DST / "step_matching.csv"
    if fp.is_file():
        return
    src = FAB_DST / "matching_step.csv"
    if not src.is_file():
        return
    with src.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        product = (row.get("product") or "").strip()
        step_id = (row.get("raw_step_id") or row.get("step_id") or "").strip()
        func_step = (row.get("function_step") or row.get("canonical_step") or "").strip()
        if not product or not step_id or not func_step:
            continue
        key = (product, step_id)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "step_id": step_id,
            "func_step": func_step,
            "product": product,
            "module": row.get("area") or row.get("module") or "",
            "step_class": row.get("step_type") or row.get("step_class") or "",
            "measure_domain": row.get("measure_domain") or "",
            "main_function_step": row.get("main_function_step") or func_step,
            "is_manual": row.get("is_manual") or "N",
            "is_active": row.get("is_active") or "Y",
            "valid_from": row.get("valid_from") or "2025-01-01",
            "valid_to": row.get("valid_to") or "",
            "priority": row.get("priority") or "100",
            "note": row.get("note") or "",
        })
    if out:
        fields = ["step_id", "func_step", "product", "module", "step_class", "measure_domain",
                  "main_function_step", "is_manual", "is_active", "valid_from", "valid_to", "priority", "note"]
        with fp.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(out)
        print(f"[step_matching] generated rows={len(out)}")


def _product_aliases(folder_prod: str) -> list[str]:
    prod = _canonical_product(folder_prod)
    if prod == "PRODA":
        return ["PRODA", "PRODA0", "PRODA1", "PRODUCT_A0", "PRODUCT_A1"]
    if prod == "PRODA0":
        return ["PRODA0", "PRODUCT_A0", "PRODA"]
    if prod == "PRODA1":
        return ["PRODA1", "PRODUCT_A1", "PRODA"]
    if prod == "PRODB":
        return ["PRODB", "PRODUCT_B"]
    return [prod] if prod else []


def _step_sort_key(step_id: str) -> tuple[str, int]:
    s = str(step_id or "")
    prefix = "".join(ch for ch in s if not ch.isdigit())
    digits = "".join(ch for ch in s if ch.isdigit())
    return (prefix, int(digits or 0))


def _ensure_et_step_matching() -> None:
    fp = FAB_DST / "step_matching.csv"
    if not fp.is_file():
        return
    with fp.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    for required in ("step_id", "func_step", "product", "module", "step_class"):
        if required not in fields:
            fields.append(required)
    existing = {(r.get("product") or "", r.get("step_id") or "") for r in rows}
    products = ["PRODA", "PRODA0", "PRODA1", "PRODB"]
    added = 0
    for step_id, func_step, note in ET_DC_STEPS:
        for product in products:
            key = (product, step_id)
            if key in existing:
                continue
            row = {field: "" for field in fields}
            row.update({
                "step_id": step_id,
                "func_step": func_step,
                "product": product,
                "module": "ET-DC",
                "step_class": "measure",
                "measure_domain": "ET",
                "main_function_step": func_step,
                "is_manual": "N",
                "is_active": "Y",
                "valid_from": "2025-01-01",
                "priority": "90",
                "note": note,
            })
            rows.append(row)
            existing.add(key)
            added += 1
    if added:
        with fp.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        print(f"[step_matching] added ET/DC rows={added}")

    fp2 = FAB_DST / "matching_step.csv"
    if not fp2.is_file():
        return
    with fp2.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    for required in ("product", "raw_step_id", "canonical_step", "function_step", "step_type",
                     "area", "step_class", "measure_domain", "main_function_step", "is_manual",
                     "is_active", "valid_from", "valid_to", "priority", "note"):
        if required not in fields:
            fields.append(required)
    existing = {(r.get("product") or "", r.get("raw_step_id") or "") for r in rows}
    added = 0
    for step_id, func_step, note in ET_DC_STEPS:
        for product in products:
            key = (product, step_id)
            if key in existing:
                continue
            row = {field: "" for field in fields}
            row.update({
                "product": product,
                "raw_step_id": step_id,
                "canonical_step": note,
                "function_step": func_step,
                "step_type": "measure",
                "area": "ET-DC",
                "step_class": "measure",
                "measure_domain": "ET",
                "main_function_step": func_step,
                "is_manual": "N",
                "is_active": "Y",
                "valid_from": "2025-01-01",
                "priority": "90",
                "note": "Tracker Analysis DC step sample",
            })
            rows.append(row)
            existing.add(key)
            added += 1
    if added:
        with fp2.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        print(f"[matching_step] added ET/DC rows={added}")


def _read_csv_rows(fp: Path) -> list[dict[str, str]]:
    if not fp.is_file():
        return []
    with fp.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _write_csv_rows(fp: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    fp.parent.mkdir(parents=True, exist_ok=True)
    with fp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows([{field: row.get(field, "") for field in fields} for row in rows])


def _step_desc_maps() -> tuple[dict[tuple[str, str], str], dict[tuple[str, str], str]]:
    desc: dict[tuple[str, str], str] = {}
    func: dict[tuple[str, str], str] = {}
    for row in _read_csv_rows(FAB_DST / "matching_step.csv"):
        prod = _canonical_product(row.get("product") or "")
        step = (row.get("raw_step_id") or row.get("step_id") or "").strip()
        if not prod or not step:
            continue
        desc[(prod, step)] = (row.get("canonical_step") or row.get("function_step") or step).strip()
        func[(prod, step)] = (row.get("function_step") or row.get("canonical_step") or step).strip()
    for row in _read_csv_rows(FAB_DST / "step_matching.csv"):
        prod = _canonical_product(row.get("product") or "")
        step = (row.get("step_id") or "").strip()
        if not prod or not step:
            continue
        func[(prod, step)] = (row.get("func_step") or func.get((prod, step)) or step).strip()
    return desc, func


def _products_for_rulebooks() -> list[str]:
    found: list[str] = []
    for fp in sorted(FAB_DST.glob("ML_TABLE_*.parquet")):
        try:
            vals = (
                pl.read_parquet(fp, columns=["product"])["product"]
                .drop_nulls()
                .unique()
                .sort()
                .to_list()
            )
        except Exception:
            vals = []
        for val in vals:
            prod = _canonical_product(val)
            if prod and prod not in found:
                found.append(prod)
    return found or ["PRODA0", "PRODA1", "PRODB"]


def _inline_step_for_item(product: str, item_name: str) -> tuple[str, str, str]:
    rows = _read_csv_rows(FAB_DST / "inline_item_map.csv")
    prod = _canonical_product(product)
    base = str(item_name or "").upper()
    base = base.removeprefix("INLINE_")
    for suffix in ("_MEAN", "_STD", "_P90", "_P10"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    keyword_sets = [
        [base],
        ["CD_FIN"] if "CD_GATE" in base else [],
        ["THK_SPACER"] if "CD_SPACER" in base else [],
        ["THK_M1"] if "TOX_M1" in base or "M1" in base and "RES" not in base and "OVL" not in base else [],
        ["RS_CT"] if "METAL_RES" in base else [],
        ["OVL_M2_M1"] if "OVL_X" in base else [],
        ["OVL_M4_M3"] if "OVL_Y" in base else [],
    ]
    for keys in keyword_sets:
        keys = [k for k in keys if k]
        if not keys:
            continue
        for row in rows:
            if _canonical_product(row.get("product") or "") != prod:
                continue
            hay = " ".join(str(row.get(k) or "").upper() for k in ("item_id", "canonical_item", "step_id"))
            if all(k in hay for k in keys):
                step = (row.get("step_id") or "").strip()
                item = (row.get("canonical_item") or row.get("item_id") or item_name).strip()
                return step, item, item
    return "", item_name, item_name


def _ensure_inline_matching_csv() -> None:
    desc_map, func_map = _step_desc_maps()
    products = _products_for_rulebooks()
    inline_cols: list[str] = []
    for fp in sorted(FAB_DST.glob("ML_TABLE_*.parquet")):
        try:
            cols = [c for c in pl.read_parquet(fp, n_rows=0).columns if c.startswith("INLINE_")]
        except Exception:
            cols = []
        for col in cols:
            item = col.replace("INLINE_", "", 1)
            if item not in inline_cols:
                inline_cols.append(item)

    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for prod in products:
        for item_id in inline_cols:
            step, mapped_item, fallback_desc = _inline_step_for_item(prod, item_id)
            step_desc = desc_map.get((prod, step)) or fallback_desc
            func = func_map.get((prod, step)) or step_desc
            key = (prod, step, item_id)
            if not step or key in seen:
                continue
            seen.add(key)
            rows.append({
                "product": prod,
                "step_id": step,
                "item_id": item_id,
                "item_desc": f"{func} / {mapped_item}".strip(" /"),
                "function_step": func,
            })
    _write_csv_rows(
        FAB_DST / "inline_matching.csv",
        ["product", "step_id", "item_id", "item_desc", "function_step"],
        rows,
    )
    print(f"[inline_matching] generated rows={len(rows)}")


def _ensure_vm_matching_csv() -> None:
    products = _products_for_rulebooks()
    features = [
        ("PREDICTED_VTH_N", "Predicted nVth", "ETA100010", "M0_DC"),
        ("PREDICTED_VTH_P", "Predicted pVth", "ETA100010", "M0_DC"),
        ("PREDICTED_ION_N", "Predicted nIon", "ETA100020", "M1_DC"),
        ("PREDICTED_ION_P", "Predicted pIon", "ETA100020", "M1_DC"),
        ("PREDICTED_RC", "Predicted contact resistance", "ETA100030", "VIA_DC"),
        ("PREDICTED_RCH", "Predicted channel resistance", "ETA100030", "VIA_DC"),
        ("VTH_N", "Virtual metrology nVth", "ETA100010", "M0_DC"),
        ("VTH_P", "Virtual metrology pVth", "ETA100010", "M0_DC"),
        ("ION_N", "Virtual metrology nIon", "ETA100020", "M1_DC"),
        ("ION_P", "Virtual metrology pIon", "ETA100020", "M1_DC"),
    ]
    rows: list[dict[str, Any]] = []
    for prod in products:
        for feature, desc, step_id, func in features:
            rows.append({
                "product": prod,
                "feature_name": feature,
                "step_desc": desc,
                "step_id": step_id,
                "function_step": func,
            })
    _write_csv_rows(
        FAB_DST / "vm_matching.csv",
        ["product", "feature_name", "step_desc", "step_id", "function_step"],
        rows,
    )
    print(f"[vm_matching] generated rows={len(rows)}")


def _load_fab_steps(folder_prod: str) -> list[str]:
    fp = FAB_DST / "step_matching.csv"
    aliases = set(_product_aliases(folder_prod))
    steps: list[str] = []
    if fp.is_file():
        with fp.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                step_id = (row.get("step_id") or "").strip()
                product = _canonical_product(row.get("product") or "")
                if not step_id or step_id.startswith("ET"):
                    continue
                if product and aliases and product not in aliases:
                    continue
                if step_id not in steps:
                    steps.append(step_id)
    steps = sorted(steps or FALLBACK_FAB_STEPS, key=_step_sort_key)
    return steps[:15]


def _as_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        digits = "".join(ch for ch in str(v or "") if ch.isdigit())
        return int(digits or default)


def _as_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _iso(t: dt.datetime) -> str:
    return t.isoformat(timespec="seconds")


def _write_hive(root_name: str, prod: str, parts: dict[str, list[dict[str, Any]]]) -> None:
    for date_key, rows in parts.items():
        if not rows:
            continue
        out_dir = FAB_DST / root_name / prod / f"date={date_key}"
        out_dir.mkdir(parents=True, exist_ok=True)
        pl.DataFrame(rows).write_parquet(out_dir / "part_0.parquet")
        print(f"[hive] {out_dir.relative_to(PROJ)} rows={len(rows)}")


def _write_flat(root_name: str, prod: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    out_dir = FAB_DST / root_name / prod
    out_dir.mkdir(parents=True, exist_ok=True)
    iso = f"{NEW_DATE[0:4]}-{NEW_DATE[4:6]}-{NEW_DATE[6:8]}"
    fp = out_dir / f"{prod}_{iso}.parquet"
    pl.DataFrame(rows).write_parquet(fp)
    print(f"[flat] {fp.relative_to(PROJ)} rows={len(rows)}")


def _key_rows(df: pl.DataFrame) -> list[dict[str, Any]]:
    keep = [c for c in ("product", "root_lot_id", "lot_id", "wafer_id", "fab_lot_id") if c in df.columns]
    if "lot_id" not in keep:
        df = df.with_columns((pl.col("root_lot_id").cast(pl.Utf8) + pl.lit("A.1")).alias("lot_id"))
        keep.append("lot_id")
    base = df.select(keep).unique(subset=["product", "root_lot_id", "lot_id", "wafer_id"], keep="first")
    rows: list[dict[str, Any]] = []
    for row in base.sort(["root_lot_id", "lot_id", "wafer_id"]).iter_rows(named=True):
        lot_id = str(row.get("lot_id") or f"{row.get('root_lot_id')}A.1")
        rows.append({
            "product": _canonical_product(row.get("product") or ""),
            "root_lot_id": str(row.get("root_lot_id") or "").strip(),
            "lot_id": lot_id,
            "fab_lot_id": str(row.get("fab_lot_id") or lot_id),
            "wafer_id": str(row.get("wafer_id") or "").strip(),
        })
    return rows


def _build_raw_for_ml(ml: Path) -> None:
    folder_prod = ml.stem.replace("ML_TABLE_", "", 1).strip().upper()
    df = pl.read_parquet(ml)
    keys = _key_rows(df)
    if not keys:
        return

    default_fab_steps = _load_fab_steps(folder_prod)
    steps_by_product: dict[str, list[str]] = {}
    fab_parts: dict[str, list[dict[str, Any]]] = {OLD_DATE: [], NEW_DATE: []}
    inline_parts: dict[str, list[dict[str, Any]]] = {OLD_DATE: [], NEW_DATE: []}
    et_by_product: dict[str, list[dict[str, Any]]] = {}

    inline_mean_cols = [
        c for c in df.columns
        if c.startswith("INLINE_") and not c.endswith("_STD") and c not in {"INLINE_OVL_X", "INLINE_OVL_Y"}
    ][:8]
    inline_by_key = {
        (
            str(r.get("product") or ""),
            str(r.get("root_lot_id") or ""),
            str(r.get("lot_id") or ""),
            str(r.get("wafer_id") or ""),
        ): r
        for r in df.select(["product", "root_lot_id", "lot_id", "wafer_id", *inline_mean_cols]).iter_rows(named=True)
    }
    shots = [(-1, -1), (0, 0), (1, 1), (-1, 1), (1, -1)]
    et_items = ["VTH", "IDSAT", "IOFF", "RINGOSC"]

    for idx, key in enumerate(keys):
        product = _canonical_product(key["product"] or _product_aliases(folder_prod)[0])
        key = {**key, "product": product}
        if product not in steps_by_product:
            steps_by_product[product] = _load_fab_steps(product) or default_fab_steps
        fab_steps = steps_by_product[product]
        root = key["root_lot_id"]
        wafer_num = _as_int(key["wafer_id"], 1)
        root_num = _as_int(root, idx + 1)
        lot_phase = (root_num + wafer_num + idx) % max(1, len(fab_steps) - 4)
        progress_count = min(len(fab_steps), 5 + lot_phase)
        step_times: list[tuple[str, dt.datetime]] = []

        for sidx, step_id in enumerate(fab_steps[:progress_count]):
            stamp = OLD_DAY + dt.timedelta(days=(root_num % 5), hours=sidx * 7, minutes=wafer_num * 3)
            step_times.append((step_id, stamp))
            part = OLD_DATE if sidx < max(2, progress_count - 3) else NEW_DATE
            eqp = f"EQP-{folder_prod[-1]}{(root_num + sidx) % 5 + 1:02d}"
            chamber = f"CH-{(wafer_num + sidx) % 4 + 1}"
            ppid = f"{folder_prod}_PPID_{(root_num + sidx) % 7 + 1:02d}"
            for item_id, subitem_id, value in (
                ("EQP_ID", "", eqp),
                ("CHAMBER", "", chamber),
                ("PPID", "", ppid),
                ("SLOT", "", f"{wafer_num:02d}"),
            ):
                fab_parts[part].append({
                    **key,
                    "step_id": step_id,
                    "item_id": item_id,
                    "subitem_id": subitem_id,
                    "value": value,
                    "time": _iso(stamp),
                    "tkin_time": _iso(stamp - dt.timedelta(minutes=30)),
                    "tkout_time": _iso(stamp),
                    "eqp": eqp,
                    "chamber": chamber,
                    "ppid": ppid,
                })

        raw_inline = inline_by_key.get((product, root, key["lot_id"], key["wafer_id"]), {})
        inline_anchor_idx = max(0, min(len(step_times) - 1, progress_count - 3))
        inline_step, inline_time = step_times[inline_anchor_idx]
        for cidx, col in enumerate(inline_mean_cols):
            item_id = col.replace("INLINE_", "").replace("_MEAN", "")
            base_value = _as_float(raw_inline.get(col), 10.0 + cidx)
            for shot_idx, (sx, sy) in enumerate(shots):
                stamp = inline_time + dt.timedelta(minutes=20 + cidx)
                inline_parts[OLD_DATE if stamp.date() <= dt.date(2026, 4, 20) else NEW_DATE].append({
                    **key,
                    "step_id": inline_step,
                    "item_id": item_id,
                    "subitem_id": f"SHOT{shot_idx + 1}",
                    "shot_x": sx,
                    "shot_y": sy,
                    "value": round(base_value + (shot_idx - 2) * 0.03 + (wafer_num % 5) * 0.01, 6),
                    "time": _iso(stamp),
                    "tkin_time": _iso(stamp - dt.timedelta(minutes=10)),
                    "tkout_time": _iso(stamp),
                })

        for et_step_idx, (et_step, _func, _note) in enumerate(ET_DC_STEPS):
            base_step_idx = max(0, min(len(step_times) - 1, progress_count - 2 + et_step_idx))
            _, base_time = step_times[base_step_idx]
            for seq in (1, 2):
                pkg_time = base_time + dt.timedelta(hours=2 + et_step_idx, minutes=seq * 8)
                for item_idx, item_id in enumerate(et_items):
                    nominal = 0.2 + et_step_idx * 0.15 + item_idx * 0.07 + (root_num % 9) * 0.005
                    for shot_idx, (sx, sy) in enumerate(shots):
                        et_by_product.setdefault(product, []).append({
                            **key,
                            "product": product,
                            "step_id": et_step,
                            "step_seq": seq,
                            "flat": f"F{seq}",
                            "date": pkg_time.date().isoformat(),
                            "time": _iso(pkg_time),
                            "tkin_time": _iso(pkg_time - dt.timedelta(minutes=12)),
                            "tkout_time": _iso(pkg_time),
                            "item_id": item_id,
                            "shot_x": sx,
                            "shot_y": sy,
                            "value": round(nominal + (shot_idx - 2) * 0.003 + wafer_num * 0.0004, 6),
                            "request_id": f"REQ-{root}-{wafer_num:02d}-{et_step_idx + 1}",
                            "measure_group_id": f"{et_step}-SEQ{seq}",
                            "eqp": f"ET-{(root_num + et_step_idx) % 4 + 1:02d}",
                            "chamber": f"DC-{seq}",
                            "pgm": f"{product}_{et_step}_PGM",
                        })

    _write_hive("1.RAWDATA_DB_FAB", folder_prod, fab_parts)
    _write_hive("1.RAWDATA_DB_INLINE", folder_prod, inline_parts)
    for prod, rows in sorted(et_by_product.items()):
        _write_flat("1.RAWDATA_DB_ET", prod, rows)


def _update_admin_settings() -> None:
    ADMIN_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    try:
        cfg = json.loads(ADMIN_SETTINGS.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    cfg.setdefault("data_roots", {})
    abs_fab = str(FAB_DST.resolve()).replace("\\", "/")
    cfg["data_roots"]["db"] = abs_fab
    cfg["data_roots"]["base"] = abs_fab
    ADMIN_SETTINGS.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    print(f"[settings] db=base={abs_fab}")


def main() -> None:
    _reset_or_copy_root()
    _ensure_ml_tables()
    _ensure_step_matching_csv()
    _ensure_et_step_matching()
    _ensure_inline_matching_csv()
    _ensure_vm_matching_csv()
    for ml in sorted(FAB_DST.glob("ML_TABLE_*.parquet")):
        _build_raw_for_ml(ml)
    _update_admin_settings()

    print("\nFab raw summary")
    for root_name in RAW_ROOTS:
        files = sorted((FAB_DST / root_name).rglob("*.parquet")) if (FAB_DST / root_name).exists() else []
        rows = 0
        for fp in files:
            try:
                rows += pl.read_parquet(fp, n_rows=0).height
            except Exception:
                pass
        print(f"  {root_name}: files={len(files)}")


if __name__ == "__main__":
    main()
