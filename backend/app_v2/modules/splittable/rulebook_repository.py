import csv
import logging
from pathlib import Path

from fastapi import HTTPException

from core.paths import PATHS
from app_v2.shared.source_adapter import resolve_existing_root
from core.utils import load_json, save_json

logger = logging.getLogger("flow.splittable.rulebook")


def repair_unquoted_product_list_row(row: dict, fieldnames: list[str] | None) -> dict:
    """Recover a product list whose commas were not CSV-quoted.

    A valid CSV stores a multi-product cell as ``"proda,prodb"``.  Some field
    files contain ``proda,prodb`` without quotes, so DictReader shifts every
    later field and places the final values under the ``None`` overflow key.
    When a product column is present, the overflow count tells us exactly how
    many leading product tokens must be joined back together.
    """
    if not isinstance(row, dict):
        return row
    extras = row.get(None)
    fields = list(fieldnames or [])
    if not isinstance(extras, list) or not extras or not fields:
        return row
    product_idx = next((
        idx for idx, name in enumerate(fields)
        if str(name or "").lstrip("\ufeff").strip().casefold() == "product"
    ), None)
    if product_idx is None:
        return row
    values = [row.get(name) for name in fields] + list(extras)
    shift = len(extras)
    repaired = {}
    for idx, name in enumerate(fields):
        if idx < product_idx:
            repaired[name] = values[idx]
        elif idx == product_idx:
            repaired[name] = ",".join(
                str(value or "").strip()
                for value in values[idx:idx + shift + 1]
                if str(value or "").strip()
            )
        else:
            repaired[name] = values[idx + shift]
    return repaired


def resolve_rulebook_file(root: Path, filename: str) -> Path:
    """Keep exact names authoritative; tolerate case variants on Linux too."""
    target = root / filename
    if target.exists() or not root.is_dir():
        return target
    matches = sorted(p for p in root.iterdir() if p.is_file() and p.name.casefold() == filename.casefold())
    return matches[0] if len(matches) == 1 else target

PLAN_DIR = PATHS.data_root / "splittable"
PLAN_DIR.mkdir(parents=True, exist_ok=True)
RULEBOOK_SCHEMA_FILE = PLAN_DIR / "rulebook_schema.json"

_DEFAULT_RULEBOOK_SCHEMA = {
    "knob_ppid": {
        "file_name":      "ppid_knob.csv",
        "feature_col":    "feature_name",
        "step_desc_col":  "step_desc",
        "func_step_col":  "function_step",
        "rule_order_col": "rule_order",
        "ppid_col":       "ppid",
        "value_col":      "value",
        "operator_col":   "operator",
        "category_col":   "category",
    },
    "step_matching": {
        "file_name":     "Vehicle_matching.csv",
        "step_id_col":   "step_id",
        "step_desc_col": "step_desc",
        "func_step_col": "function_step",
        "product_col":   "product",
        "module_col":    "module",
    },
    "inline_matching": {
        "file_name":     "inline_matching.csv",
        "step_id_col":   "step_id",
        "process_id_col": "process_id",
        "item_id_col":   "item_id",
        "item_desc_col": "item_desc",
        "step_desc_col": "step_desc",
        "product_col":   "product",
        "matching_table_col": "matching_table",
    },
    "vm_matching": {
        "file_name":     "vm_matching.csv",
        "step_desc_col": "step_desc",
        "item_id_col":   "item_id",
        "item_desc_col": "item_desc",
    },
    "fab_matching": {
        "file_name":        "fab.csv",
        "step_desc_col":    "step_desc",
        "feature_name_col": "feature_name",
    },
}

_RULEBOOK_FILES = {
    "knob_ppid": {
        "filename": "ppid_knob.csv",
        "legacy_filename": "knob_ppid.csv",
        "cols": ["feature_name", "rule_order", "step_desc", "operator", "value", "category"],
        "required": ["feature_name", "step_desc"],
    },
    "step_matching": {
        "filename": "Vehicle_matching.csv",
        "legacy_filename": "step_matching.csv",
        "cols": ["product", "step_id", "step_desc"],
        "required": ["product", "step_id", "step_desc"],
    },
    "inline_matching": {
        "filename": "inline_matching.csv",
        "legacy_filename": "inline_mathcing.csv",
        "cols": ["product", "step_id", "item_id", "item_desc", "step_desc", "matching_table"],
        "required": ["product", "step_id"],
    },
    "vm_matching": {
        "filename": "vm_matching.csv",
        "cols": ["step_desc", "item_id"],
        "required": ["step_desc", "item_id"],
    },
    "fab_matching": {
        "filename": "fab.csv",
        "cols": ["step_desc", "feature_name"],
        "required": ["step_desc", "feature_name"],
    },
}


def get_base_root() -> Path:
    return resolve_existing_root("base", PATHS.base_root)


class RulebookRepository:
    def load_schema(self) -> dict:
        try:
            data = load_json(RULEBOOK_SCHEMA_FILE, {})
        except Exception:
            data = {}
        out = {}
        for k, defmap in _DEFAULT_RULEBOOK_SCHEMA.items():
            cur = (data or {}).get(k) if isinstance(data, dict) else {}
            cur = cur if isinstance(cur, dict) else {}
            out[k] = {**defmap, **{kk: (vv or defmap.get(kk, "")) for kk, vv in cur.items() if isinstance(kk, str)}}
        return out

    def save_schema(self, schema: dict) -> None:
        save_json(RULEBOOK_SCHEMA_FILE, schema, indent=2)

    def get_sch(self, kind: str) -> dict:
        return self.load_schema().get(kind, _DEFAULT_RULEBOOK_SCHEMA.get(kind, {}))

    def get_meta(self, kind: str) -> dict | None:
        return _RULEBOOK_FILES.get(kind)

    def get_default_schema(self) -> dict:
        return _DEFAULT_RULEBOOK_SCHEMA

    def clean_rulebook_filename(self, value: object, default: str) -> str:
        name = Path(str(value or "").strip()).name
        if not name:
            return default
        if not name.lower().endswith(".csv"):
            name = f"{name}.csv"
        return name

    def get_rulebook_path(self, kind: str, base: Path | None = None) -> Path:
        meta = self.get_meta(kind)
        if not meta:
            raise HTTPException(400, f"unknown rulebook: {kind}")
        root = base or get_base_root()
        configured = self.clean_rulebook_filename(self.get_sch(kind).get("file_name"), meta["filename"])
        primary = resolve_rulebook_file(root, configured)
        if configured != meta["filename"] or primary.exists() or not meta.get("legacy_filename"):
            return primary
        legacy = resolve_rulebook_file(root, str(meta.get("legacy_filename") or ""))
        return legacy if legacy.exists() else primary

    def load_csv_rows(self, fp: Path) -> list[dict]:
        if not fp.exists():
            return []
        try:
            with open(fp, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                return [
                    repair_unquoted_product_list_row(row, reader.fieldnames)
                    for row in reader
                ]
        except Exception as e:
            logger.warning(f"Failed to read rulebook csv: {fp} - {e}")
            return []

    def save_csv_rows(self, fp: Path, rows: list[dict], cols: list[str]) -> None:
        fp.parent.mkdir(parents=True, exist_ok=True)
        import io as _io
        buf = _io.StringIO()
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
        fp.write_text(buf.getvalue(), encoding="utf-8", newline="")
