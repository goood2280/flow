"""Build the active sample DB root under ``data/Fab``.

The app expects the demo DB to look close to the production datalake:

* root files at ``data/Fab`` such as ``ML_TABLE_*.parquet`` and matching CSVs
* long FAB/INLINE hive folders:
  ``1.RAWDATA_DB_FAB/<PROD>/date=YYYYMMDD/part_0.parquet``
* wafer-level VM hive folders:
  ``1.RAWDATA_DB_VM/<PROD>/date=YYYYMMDD/part_0.parquet``
* long ET flat folders:
  ``1.RAWDATA_DB_ET/<PROD>/<PROD>_YYYY-MM-DD.parquet``

Raw FAB/INLINE/ET folders are product-scoped by path.  Row payloads therefore
avoid duplicate ``product`` / ``fab_lot_id`` fields; ``lot_id`` is the current
FAB lot at that step, while ``root_lot_id`` + ``wafer_id`` stays the stable
wafer identity.  ``process_id`` carries the 4-letter process code where needed.

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

RAW_ROOTS = ("1.RAWDATA_DB_FAB", "1.RAWDATA_DB_INLINE", "1.RAWDATA_DB_VM", "1.RAWDATA_DB_ET")
SPLIT_TABLE_TARGET_WAFERS = 3000
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
    ("EA100010", "M0_DC", "ET DC M0 measure"),
    ("EA100020", "M1_DC", "ET DC M1 measure"),
    ("EA100030", "VIA_DC", "ET DC via/contact measure"),
]

PRODUCT_RENAMES = {
    "PRODUCT_A0": "PRODA",
    "PRODUCT_A1": "PRODA",
    "PRODUCT_A": "PRODA",
    "PRODA0": "PRODA",
    "PRODA1": "PRODA",
    "PRODUCT_B": "PRODB",
}

STEP_RENAMES = {
    "ETA100010": "EA100010",
    "ETA100020": "EA100020",
    "ETA100030": "EA100030",
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
    for old, new in STEP_RENAMES.items():
        out = out.replace(old, new)
    return out


def _normalize_seed_files() -> None:
    """Normalize copied seed files to operating product names.

    Source fixtures still use PRODUCT_A0/PRODUCT_A1/PRODUCT_B. Flow's active
    demo root should expose only the production-level products: PRODA/PRODB.
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
                        for old, new in STEP_RENAMES.items():
                            expr = expr.str.replace_all(old, new, literal=True)
                        exprs.append(expr.alias(col))
                if exprs:
                    df.with_columns(exprs).write_parquet(fp)
            except Exception as e:
                print(f"[warn] product parquet normalize failed {fp.name}: {e}")


def _dedupe_csv(fp: Path, key_fields: list[str]) -> None:
    rows = _read_csv_rows(fp)
    if not rows:
        return
    fields = list(rows[0].keys())
    seen: set[tuple[str, ...]] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        key = tuple(str(row.get(k) or "") for k in key_fields)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    if len(out) != len(rows):
        _write_csv_rows(fp, fields, out)
        print(f"[dedupe] {fp.name}: {len(rows)} -> {len(out)}")


def _process_id(product: str) -> str:
    prod = _family_root(product)
    return "PRDA" if prod == "PRODA" else "PRDB"


def _product_seq_token(product: str) -> str:
    return "PDA" if _family_root(product) == "PRODA" else "PDB"


def _step_seq_token(product: str, root_num: int, et_step_idx: int, flat_angle: int) -> str:
    node = ((root_num + et_step_idx + max(0, flat_angle // 90)) % 9) + 1
    bundle = {0: "SF", 90: "MF", 180: "RF", 270: "EF"}.get(int(flat_angle), "PK")
    return f"N{node:02d}{_product_seq_token(product)}{bundle}"


def _stage_index_from_item(item_id: str, default: int = 1) -> int:
    head = str(item_id or "").strip().split(" ", 1)[0]
    try:
        return max(1, int(float(head)))
    except Exception:
        return default


def _shot_points(root_num: int, wafer_num: int, salt: int = 0) -> list[tuple[int, int]]:
    """Deterministic 10-30 point shot map per wafer.

    Most wafers get 10 points; a smaller share gets denser 15/20/30 point
    layouts so charting and split logic see realistic variety without making
    the demo DB excessively large.
    """
    bucket = (root_num * 31 + wafer_num * 17 + salt * 13) % 100
    if bucket < 72:
        count = 10
    elif bucket < 88:
        count = 15
    elif bucket < 96:
        count = 20
    else:
        count = 30
    candidates = [
        (x, y)
        for y in range(-5, 6)
        for x in range(-5, 6)
        if abs(x) + abs(y) <= 9
    ]
    start = (root_num * 13 + wafer_num * 7 + salt * 5) % len(candidates)
    step = 17
    coords: list[tuple[int, int]] = []
    for idx in range(len(candidates)):
        coord = candidates[(start + idx * step) % len(candidates)]
        coords.append(coord)
        if len(coords) >= count:
            break
    return coords


def _family_root(product: str) -> str:
    prod = _canonical_product(product)
    return "PRODA" if prod.startswith("PRODA") else "PRODB"


def _fab_lot_for(root_lot_id: str, wafer_id: Any) -> str:
    wid = _as_int(str(wafer_id).replace("W", ""), 1)
    letter = ["A", "B", "C"][(wid - 1) // 9 % 3]
    iteration = 1 if wid <= 17 else 2
    return f"{root_lot_id}{letter}.{iteration}"


def _flow_lot_id(root_lot_id: str, wafer_id: Any, step_idx: int, root_num: int = 0) -> str:
    """Current in-FAB lot id for a wafer at a process step.

    The wafer identity is root_lot_id + wafer_id. lot_id is operational state:
    a root starts together, splits into A/B/C lot branches, and later merges.
    A small subset re-splits late to keep the sample history realistic.
    """
    wid = _as_int(str(wafer_id).replace("W", ""), 1)
    idx = max(0, int(step_idx or 0))
    branch = ["A", "B", "C"][((wid - 1) // 9) % 3]
    if idx < 4:
        return f"{root_lot_id}A.1"
    if idx < 18:
        return f"{root_lot_id}{branch}.1"
    if idx < 34:
        return f"{root_lot_id}A.2"
    if idx < 46 and (root_num + wid) % 5 in (0, 1):
        re_branch = "B" if wid % 2 else "C"
        return f"{root_lot_id}{re_branch}.2"
    if idx < 52:
        return f"{root_lot_id}A.2"
    return f"{root_lot_id}A.3"


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


def _wide_split_value(col: str, value: Any, wafer_num: int, source_idx: int) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value + ((wafer_num + source_idx) % 3) - 1)
    if isinstance(value, float):
        return round(value + ((wafer_num % 37) - 18) * 0.001 + source_idx * 0.0002, 6)
    text = str(value)
    upper = str(col or "").upper()
    if upper.startswith("KNOB_"):
        return f"{text}_S{wafer_num % 4}"
    if upper.startswith("MASK_"):
        return f"{text}_M{wafer_num % 6}"
    return value


def _ensure_wide_split_tables(target_wafers: int = SPLIT_TABLE_TARGET_WAFERS) -> None:
    """Expand one representative root per product to ~3000 WF columns in SplitTable.

    SplitTable renders wafer_id values horizontally for a selected root_lot_id.
    The raw process DB still models current lot_id separately; this helper only
    widens the ML_TABLE root used by the SplitTable view.
    """
    for fp in sorted(FAB_DST.glob("ML_TABLE_*.parquet")):
        try:
            df = pl.read_parquet(fp)
        except Exception as e:
            print(f"[warn] split table widen read failed {fp.name}: {e}")
            continue
        if df.is_empty():
            continue
        cols = list(df.columns)
        product_col = _ci_col(cols, "product")
        root_col = _ci_col(cols, "root_lot_id")
        lot_col = _ci_col(cols, "lot_id")
        fab_col = _ci_col(cols, "fab_lot_id")
        wafer_col = _ci_col(cols, "wafer_id")
        if not root_col or not wafer_col:
            continue

        family = _family_root(fp.stem.replace("ML_TABLE_", "", 1))
        preferred_root = ("A" if family == "PRODA" else "B") + "1000"
        roots = (
            df.select(pl.col(root_col).cast(pl.Utf8, strict=False).alias("root"))
            .drop_nulls()
            .unique()
            .sort("root")
            .get_column("root")
            .to_list()
        )
        target_root = preferred_root if preferred_root in roots else (roots[0] if roots else "")
        if not target_root:
            continue
        root_df = df.filter(pl.col(root_col).cast(pl.Utf8, strict=False) == target_root)
        current = root_df.select(pl.col(wafer_col).cast(pl.Utf8, strict=False)).n_unique()
        if current >= target_wafers:
            continue

        template_df = root_df.sort(pl.col(wafer_col).cast(pl.Int64, strict=False))
        templates = template_df.to_dicts()
        if not templates:
            continue
        root_num = _as_int(target_root, 1000)
        id_cols = {c for c in (product_col, root_col, lot_col, fab_col, wafer_col) if c}
        rows: list[dict[str, Any]] = []
        for wafer_num in range(1, target_wafers + 1):
            source_idx = (wafer_num - 1) % len(templates)
            row = dict(templates[source_idx])
            if product_col:
                row[product_col] = family
            row[root_col] = target_root
            row[wafer_col] = wafer_num
            current_lot = _flow_lot_id(target_root, wafer_num, 59, root_num)
            if lot_col:
                row[lot_col] = current_lot
            if fab_col:
                row[fab_col] = current_lot
            for col in cols:
                if col in id_cols:
                    continue
                row[col] = _wide_split_value(col, row.get(col), wafer_num, source_idx)
            rows.append(row)

        other = df.filter(pl.col(root_col).cast(pl.Utf8, strict=False) != target_root)
        wide = pl.DataFrame(rows)
        out = pl.concat([other, wide], how="diagonal_relaxed").select(cols)
        cast_exprs = [pl.col(col).cast(dtype, strict=False).alias(col) for col, dtype in df.schema.items()]
        out = out.with_columns(cast_exprs)
        out.write_parquet(fp)
        print(f"[split_wide] {fp.name} root={target_root} wafers={target_wafers} rows={out.height} cols={out.width}")


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
    products = ["PRODA", "PRODB"]
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
            schema = pl.read_parquet(fp, n_rows=0).columns
            product_col = _ci_col(schema, "product")
            if not product_col:
                stem_product = fp.stem.replace("ML_TABLE_", "", 1).strip()
                vals = [stem_product] if stem_product else []
            else:
                vals = (
                    pl.read_parquet(fp, columns=[product_col])[product_col]
                    .drop_nulls()
                    .unique()
                    .sort()
                    .to_list()
                )
        except Exception:
            vals = []
        for val in vals:
            prod = _family_root(_canonical_product(val))
            if prod and prod not in found:
                found.append(prod)
    return found or ["PRODA", "PRODB"]


def _ci_col(columns: list[str], *names: str) -> str:
    lookup = {str(c).casefold(): str(c) for c in columns}
    for name in names:
        hit = lookup.get(str(name).casefold())
        if hit:
            return hit
    return ""


def _key_exprs(df: pl.DataFrame, fallback_product: str) -> list[Any]:
    cols = list(df.columns)
    product_col = _ci_col(cols, "product")
    root_col = _ci_col(cols, "root_lot_id")
    lot_col = _ci_col(cols, "lot_id")
    wafer_col = _ci_col(cols, "wafer_id")
    if not root_col or not wafer_col:
        return []
    fallback = _family_root(_canonical_product(fallback_product))
    product_expr = (
        pl.col(product_col).cast(pl.Utf8, strict=False)
        .map_elements(lambda v: _family_root(_canonical_product(v or fallback)), return_dtype=pl.Utf8)
        .alias("product")
        if product_col
        else pl.lit(fallback).alias("product")
    )
    root_expr = pl.col(root_col).cast(pl.Utf8, strict=False).alias("root_lot_id")
    lot_expr = (
        pl.col(lot_col).cast(pl.Utf8, strict=False).alias("lot_id")
        if lot_col
        else (pl.col(root_col).cast(pl.Utf8, strict=False) + pl.lit("A.1")).alias("lot_id")
    )
    return [
        product_expr,
        root_expr,
        lot_expr,
        pl.col(wafer_col).cast(pl.Utf8, strict=False).alias("wafer_id"),
    ]


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
    vm_cols: list[str] = []
    for fp in sorted(FAB_DST.glob("ML_TABLE_*.parquet")):
        try:
            cols = [c for c in pl.read_parquet(fp, n_rows=0).columns if c.startswith("VM_")]
        except Exception:
            cols = []
        for col in cols:
            feature = col.replace("VM_", "", 1)
            if feature not in vm_cols:
                vm_cols.append(feature)

    extra_features = [
        ("PREDICTED_VTH_N", "Predicted nVth", "EA100010", "M0_DC"),
        ("PREDICTED_VTH_P", "Predicted pVth", "EA100010", "M0_DC"),
        ("PREDICTED_ION_N", "Predicted nIon", "EA100020", "M1_DC"),
        ("PREDICTED_ION_P", "Predicted pIon", "EA100020", "M1_DC"),
        ("PREDICTED_RC", "Predicted contact resistance", "EA100030", "VIA_DC"),
        ("PREDICTED_RCH", "Predicted channel resistance", "EA100030", "VIA_DC"),
        ("VTH_N", "Virtual metrology nVth", "EA100010", "M0_DC"),
        ("VTH_P", "Virtual metrology pVth", "EA100010", "M0_DC"),
        ("ION_N", "Virtual metrology nIon", "EA100020", "M1_DC"),
        ("ION_P", "Virtual metrology pIon", "EA100020", "M1_DC"),
    ]
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for prod in products:
        fab_steps = _load_fab_steps(prod) or FALLBACK_FAB_STEPS
        _desc_map, func_map = _step_desc_maps()
        for idx, feature in enumerate(vm_cols):
            stage_idx = _stage_index_from_item(feature, idx + 1)
            step_id = fab_steps[min(len(fab_steps) - 1, max(0, stage_idx - 1))]
            func = func_map.get((prod, step_id)) or step_id
            key = (prod, feature)
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "product": prod,
                "feature_name": feature,
                "step_desc": f"VM {feature}",
                "step_id": step_id,
                "function_step": func,
            })
        for feature, desc, step_id, func in extra_features:
            key = (prod, feature)
            if key in seen:
                continue
            seen.add(key)
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
    et_step_ids = {step_id for step_id, _func, _note in ET_DC_STEPS}
    steps: list[str] = []
    if fp.is_file():
        with fp.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                step_id = (row.get("step_id") or "").strip()
                product = _canonical_product(row.get("product") or "")
                domain = (row.get("measure_domain") or "").strip().upper()
                module = (row.get("module") or "").strip().upper()
                if not step_id or step_id in et_step_ids or step_id.startswith("ET") or domain == "ET" or module.startswith("ET"):
                    continue
                if product and aliases and product not in aliases:
                    continue
                if step_id not in steps:
                    steps.append(step_id)
    steps = sorted(steps or FALLBACK_FAB_STEPS, key=_step_sort_key)
    return steps[:60]


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


def _key_rows(df: pl.DataFrame, fallback_product: str) -> list[dict[str, Any]]:
    exprs = _key_exprs(df, fallback_product)
    if not exprs:
        return []
    base = df.select(exprs).unique(subset=["product", "root_lot_id", "lot_id", "wafer_id"], keep="first")
    rows: list[dict[str, Any]] = []
    for row in base.sort(["root_lot_id", "lot_id", "wafer_id"]).iter_rows(named=True):
        lot_id = str(row.get("lot_id") or f"{row.get('root_lot_id')}A.1")
        rows.append({
            "product": _family_root(_canonical_product(row.get("product") or fallback_product)),
            "root_lot_id": str(row.get("root_lot_id") or "").strip(),
            "lot_id": lot_id,
            "wafer_id": str(row.get("wafer_id") or "").strip(),
        })
    return rows


def _build_raw_for_ml(ml: Path) -> None:
    folder_prod = ml.stem.replace("ML_TABLE_", "", 1).strip().upper()
    df = pl.read_parquet(ml)
    keys = _key_rows(df, folder_prod)
    if not keys:
        return

    default_fab_steps = _load_fab_steps(folder_prod)
    steps_by_product: dict[str, list[str]] = {}
    fab_parts: dict[str, list[dict[str, Any]]] = {OLD_DATE: [], NEW_DATE: []}
    inline_parts: dict[str, list[dict[str, Any]]] = {OLD_DATE: [], NEW_DATE: []}
    vm_parts: dict[str, list[dict[str, Any]]] = {OLD_DATE: [], NEW_DATE: []}
    et_by_product: dict[str, list[dict[str, Any]]] = {}

    inline_mean_cols = [
        c for c in df.columns
        if c.startswith("INLINE_") and not c.endswith("_STD") and c not in {"INLINE_OVL_X", "INLINE_OVL_Y"}
    ][:16]
    vm_cols = [c for c in df.columns if c.startswith("VM_")][:24]
    key_exprs = _key_exprs(df, folder_prod)
    inline_by_key = {}
    vm_by_key = {}
    if key_exprs:
        inline_by_key = {
            (
                str(r.get("product") or ""),
                str(r.get("root_lot_id") or ""),
                str(r.get("lot_id") or ""),
                str(r.get("wafer_id") or ""),
            ): r
            for r in df.select([*key_exprs, *[pl.col(c) for c in inline_mean_cols]]).iter_rows(named=True)
        }
        vm_by_key = {
            (
                str(r.get("product") or ""),
                str(r.get("root_lot_id") or ""),
                str(r.get("lot_id") or ""),
                str(r.get("wafer_id") or ""),
            ): r
            for r in df.select([*key_exprs, *[pl.col(c) for c in vm_cols]]).iter_rows(named=True)
        }
    et_items = ["VTH", "IDSAT", "IOFF", "RINGOSC"]

    for idx, key in enumerate(keys):
        product = _family_root(_canonical_product(key["product"] or _product_aliases(folder_prod)[0]))
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
        process_id = _process_id(product)
        shots = _shot_points(root_num, wafer_num, idx)

        for sidx, step_id in enumerate(fab_steps[:progress_count]):
            stamp = OLD_DAY + dt.timedelta(days=(root_num % 2), minutes=sidx * 90 + wafer_num * 3)
            step_times.append((step_id, stamp))
            part = OLD_DATE if sidx < max(2, progress_count - 3) else NEW_DATE
            current_lot_id = _flow_lot_id(root, wafer_num, sidx, root_num)
            eqp = f"EQP-{folder_prod[-1]}{(root_num + sidx) % 5 + 1:02d}"
            chamber = f"CH-{(wafer_num + sidx) % 4 + 1}"
            ppid = f"{folder_prod}_PPID_{(root_num + sidx) % 7 + 1:02d}"
            fab_parts[part].append({
                "root_lot_id": root,
                "lot_id": current_lot_id,
                "wafer_id": key["wafer_id"],
                "process_id": process_id,
                "step_id": step_id,
                "tkin_time": _iso(stamp - dt.timedelta(minutes=30)),
                "tkout_time": _iso(stamp),
                "eqp_id": eqp,
                "chamber_id": chamber,
                "ppid": ppid,
                "slot_id": f"{wafer_num:02d}",
            })

        raw_inline = inline_by_key.get((product, root, key["lot_id"], key["wafer_id"]), {})
        raw_vm = vm_by_key.get((product, root, key["lot_id"], key["wafer_id"]), {})
        inline_anchor_idx = max(0, min(len(step_times) - 1, progress_count - 3))
        inline_step, inline_time = step_times[inline_anchor_idx]
        inline_lot_id = _flow_lot_id(root, wafer_num, inline_anchor_idx, root_num)
        for cidx, col in enumerate(inline_mean_cols):
            item_id = col.replace("INLINE_", "").replace("_MEAN", "")
            base_value = _as_float(raw_inline.get(col), 10.0 + cidx)
            for shot_idx, (sx, sy) in enumerate(shots):
                stamp = inline_time + dt.timedelta(minutes=20 + cidx)
                inline_part = NEW_DATE if stamp.date() >= dt.date(2024, 4, 23) else OLD_DATE
                inline_parts[inline_part].append({
                    "root_lot_id": root,
                    "lot_id": inline_lot_id,
                    "wafer_id": key["wafer_id"],
                    "process_id": process_id,
                    "step_id": inline_step,
                    "item_id": item_id,
                    "subitem_id": f"SHOT{shot_idx + 1:02d}",
                    "value": round(base_value + (sx * 0.018) + (sy * 0.011) + (shot_idx % 5) * 0.007 + (wafer_num % 5) * 0.01, 6),
                    "tkin_time": _iso(stamp - dt.timedelta(minutes=10)),
                    "tkout_time": _iso(stamp),
                })

        for vidx, col in enumerate(vm_cols):
            item_id = col.replace("VM_", "", 1)
            stage_idx = _stage_index_from_item(item_id, vidx + 1)
            vm_step_idx = max(0, min(len(step_times) - 1, stage_idx - 1))
            vm_step, vm_time = step_times[vm_step_idx]
            vm_lot_id = _flow_lot_id(root, wafer_num, vm_step_idx, root_num)
            stamp = vm_time + dt.timedelta(minutes=35 + (vidx % 6) * 3)
            vm_part = NEW_DATE if stamp.date() >= dt.date(2024, 4, 23) else OLD_DATE
            base_value = _as_float(raw_vm.get(col), 0.5 + vidx * 0.03)
            vm_parts[vm_part].append({
                "root_lot_id": root,
                "lot_id": vm_lot_id,
                "wafer_id": key["wafer_id"],
                "process_id": process_id,
                "step_id": vm_step,
                "item_id": item_id,
                "value": round(base_value + (root_num % 17) * 0.002 + wafer_num * 0.0007, 6),
                "tkin_time": _iso(stamp - dt.timedelta(minutes=5)),
                "tkout_time": _iso(stamp),
                "model_id": f"{product}_VM_{stage_idx:02d}",
                "run_id": f"VM{root_num % 1000:03d}{wafer_num:02d}{vidx + 1:02d}",
            })

        for et_step_idx, (et_step, _func, _note) in enumerate(ET_DC_STEPS):
            base_step_idx = max(0, min(len(step_times) - 1, progress_count - 2 + et_step_idx))
            _, base_time = step_times[base_step_idx]
            et_lot_id = _flow_lot_id(root, wafer_num, base_step_idx, root_num)
            for flat_angle in (0, 90, 270):
                pkg_time = base_time + dt.timedelta(hours=2 + et_step_idx, minutes=(flat_angle // 90 + 1) * 8)
                for item_idx, item_id in enumerate(et_items):
                    nominal = 0.2 + et_step_idx * 0.15 + item_idx * 0.07 + (root_num % 9) * 0.005
                    for shot_idx, (sx, sy) in enumerate(shots):
                        et_by_product.setdefault(product, []).append({
                            "root_lot_id": root,
                            "lot_id": et_lot_id,
                            "wafer_id": key["wafer_id"],
                            "step_id": et_step,
                            "step_seq": _step_seq_token(product, root_num, et_step_idx, flat_angle),
                            "flat": flat_angle,
                            "tkin_time": _iso(pkg_time - dt.timedelta(minutes=12)),
                            "tkout_time": _iso(pkg_time),
                            "item_id": item_id,
                            "shot_x": sx,
                            "shot_y": sy,
                            "value": round(nominal + (sx * 0.0018) + (sy * 0.0011) + (flat_angle / 270.0) * 0.006 + wafer_num * 0.0004, 6),
                            "eqp_id": f"ET-{(root_num + et_step_idx) % 4 + 1:02d}",
                            "chamber_id": f"DC-{flat_angle or 0}",
                            "pgm": f"{product}_{et_step}_PGM",
                        })

    _write_hive("1.RAWDATA_DB_FAB", folder_prod, fab_parts)
    _write_hive("1.RAWDATA_DB_INLINE", folder_prod, inline_parts)
    _write_hive("1.RAWDATA_DB_VM", folder_prod, vm_parts)
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
    _normalize_seed_files()
    _ensure_ml_tables()
    _ensure_wide_split_tables()
    _ensure_step_matching_csv()
    _ensure_et_step_matching()
    _dedupe_csv(FAB_DST / "step_matching.csv", ["product", "step_id"])
    _dedupe_csv(FAB_DST / "matching_step.csv", ["product", "raw_step_id"])
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
