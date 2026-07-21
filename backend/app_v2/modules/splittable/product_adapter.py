import re
from pathlib import Path

def split_product_core(product: object) -> str:
    text = str(product or "").strip().replace("\\", "/")
    if not text:
        return ""
    text = Path(text).name
    m = re.search(r"ML_TABLE_", text, flags=re.I)
    if m:
        text = text[m.end():]
    ext = re.search(r"\.(?:parquet|csv)(?=$|[\s,，、()[\]{}])", text, flags=re.I)
    if ext:
        text = text[:ext.start()]
    return text.strip(" \t\r\n,，、()[]{}")

def product_aliases(product: str) -> set[str]:
    raw = str(product or "").strip()
    if not raw:
        return set()
    out = {raw.upper()}
    core = split_product_core(raw)
    if core:
        out.add(core.upper())
    return out

def product_alias_keys(product: str) -> set[str]:
    return {str(alias or "").strip().casefold() for alias in product_aliases(product) if str(alias or "").strip()}

def product_cell_tokens(row_product: object) -> list[str]:
    row_value = str(row_product or "").strip()
    if not row_value:
        return []
    return [part.strip() for part in re.split(r"[,，、]", row_value) if part.strip()]

def product_value_matches(product: str, row_product: object, *, allow_common: bool = True) -> bool:
    if not str(product or "").strip():
        return True
    row_values = product_cell_tokens(row_product)
    if not row_values:
        return allow_common
    product_keys = product_alias_keys(product)
    return any(row_value.casefold() in product_keys for row_value in row_values)

def step_matching_product_alias_keys(product: str) -> set[str]:
    raw = str(product or "").strip()
    if not raw:
        return set()
    core = split_product_core(raw)
    aliases = {raw, core}
    if core:
        aliases.add(f"ML_TABLE_{core}")
    up = core.upper()
    if up == "PRODA0":
        aliases.add("PRODA1")
    return {str(a).casefold() for a in aliases if str(a).strip()}

def step_matching_product_matches(product: str, row_product: object, *, allow_common: bool = True) -> bool:
    if not str(product or "").strip():
        return True
    row_values = product_cell_tokens(row_product)
    if not row_values:
        return allow_common
    product_keys = step_matching_product_alias_keys(product)
    for row_value in row_values:
        row_keys = step_matching_product_alias_keys(row_value)
        if row_keys and any(key in product_keys for key in row_keys):
            return True
    return False
