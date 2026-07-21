from typing import Any
from fastapi import HTTPException
from core import matching_cache as _matching_cache
from core import s3_sync as _s3
from core.paths import PATHS
from core.audit import record_user as _audit_user
from app_v2.modules.splittable.rulebook_repository import RulebookRepository
from app_v2.modules.splittable.product_adapter import step_matching_product_matches, product_value_matches

import logging
logger = logging.getLogger("flow.splittable.rulebook")

class RulebookService:
    def __init__(self, repository: RulebookRepository):
        self.repo = repository

    def _row_step_desc(self, row: dict, schema: dict) -> str:
        for col in [schema.get("step_desc_col", "step_desc"), schema.get("func_step_col", "function_step"), "step_desc", "function_step", "func_step"]:
            if not col: continue
            val = str((row or {}).get(col) or "").strip()
            if val: return val
        return ""

    def _first_row_value(self, row: dict, *cols: str) -> str:
        for col in cols:
            if not col: continue
            val = str((row or {}).get(col) or "").strip()
            if val: return val
        return ""

    def normalize_rows(self, kind: str, rows: list[dict]) -> list[dict]:
        out = []
        sch = self.repo.get_sch(kind)
        for row in rows or []:
            if not isinstance(row, dict): continue
            r = dict(row)
            if kind in {"knob_ppid", "step_matching", "vm_matching"} and not str(r.get("step_desc") or "").strip():
                r["step_desc"] = self._row_step_desc(r, sch)
            if kind == "knob_ppid" and not str(r.get("value") or "").strip():
                r["value"] = self._first_row_value(r, sch.get("value_col", "value"), "ppid", "category")
            if kind == "vm_matching" and not str(r.get("item_id") or "").strip():
                r["item_id"] = self._first_row_value(r, sch.get("item_id_col", "item_id"), "feature_name")
            out.append(r)
        return out

    def row_matches_product(self, kind: str, row: dict, product: str, *, allow_common: bool = True) -> bool:
        p_col = self.repo.get_sch(kind).get("product_col", "product")
        row_product = (row or {}).get(p_col)
        if row_product is None and p_col != "product":
            row_product = (row or {}).get("product")
        if kind in {"step_matching", "inline_matching"}:
            return step_matching_product_matches(product, row_product, allow_common=allow_common)
        return product_value_matches(product, row_product, allow_common=allow_common)

    def get_rulebook(self, kind: str, product: str = "") -> dict:
        meta = self.repo.get_meta(kind)
        if not meta:
            raise HTTPException(400, f"unknown rulebook: {kind}")
        
        path = self.repo.get_rulebook_path(kind)
        raw_rows = self.repo.load_csv_rows(path)
        rows = self.normalize_rows(kind, raw_rows)
        
        if product and kind not in {"knob_ppid", "vm_matching"}:
            allow_common = True
            if kind in {"step_matching", "inline_matching"}:
                p_col = self.repo.get_sch(kind).get("product_col", "product")
                allow_common = not any(p_col in r or "product" in r for r in rows)
            rows = [r for r in rows if self.row_matches_product(kind, r, product, allow_common=allow_common)]
            
        return {
            "kind": kind, "file": path.name,
            "columns": meta["cols"], "rows": rows, "count": len(rows),
        }

    def save_rulebook(self, kind: str, rows: list[dict], product: str, username: str) -> dict:
        meta = self.repo.get_meta(kind)
        if not meta:
            raise HTTPException(400, f"unknown rulebook: {kind}")
            
        fp = self.repo.get_rulebook_path(kind)
        cols = meta["cols"]
        req_cols = meta.get("required", [])
        
        try:
            cleaned, dedupe_rows = _matching_cache.dedupe_rows(
                self.normalize_rows(kind, rows),
                key_cols=cols,
                required_cols=req_cols,
                strict_required=True,
            )
        except ValueError as e:
            raise HTTPException(400, f"validation failed: {e}")

        product_scope = str(product or "").strip()
        if kind in {"knob_ppid", "vm_matching"}:
            product_scope = ""

        if product_scope:
            existing = self.normalize_rows(kind, self.repo.load_csv_rows(fp))
            kept = [r for r in existing if not self.row_matches_product(kind, r, product_scope, allow_common=False)]
            for c in cleaned:
                c["product"] = product_scope
            final = kept + cleaned
        else:
            final = cleaned

        try:
            final, dedupe_rows_after = _matching_cache.dedupe_rows(
                final,
                key_cols=cols,
                required_cols=req_cols,
                strict_required=True,
            )
        except ValueError as e:
            raise HTTPException(400, f"validation failed: {e}")

        self.repo.save_csv_rows(fp, final, cols)
        
        cache_result = _matching_cache.refresh_matching_csv(fp)
        if not cache_result.get("ok", False):
            logger.warning("rulebook save cache refresh failed: %s", cache_result)
            
        _audit_user(username, "splittable:rulebook_save", detail=f"kind={kind} product={product_scope} rows={len(final)}")
        sync_result = _s3.sync_saved_path(PATHS.data_root, PATHS.db_root, fp)
        
        return {
            "ok": True,
            "kind": kind,
            "product": product_scope,
            "saved_rows": len(final),
            "deduped_rows": dedupe_rows + dedupe_rows_after,
            "cache_rows": cache_result.get("rows"),
            "s3_sync": sync_result,
        }
