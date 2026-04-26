"""Product catalog dedup helpers."""

from __future__ import annotations


def canonical_product(value: str) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text:
        return ""
    if text.lower().startswith("ml_table_"):
        text = text[len("ML_TABLE_"):]
    return text


def normalize_products(products: list[str] | None) -> list[str]:
    seen: dict[str, str] = {}
    for raw in products or []:
        canon = canonical_product(raw)
        if not canon:
            continue
        key = canon.casefold()
        if key not in seen:
            seen[key] = canon
    return list(seen.values())


def find_duplicate_product(products: list[str] | None, candidate: str) -> str:
    canon = canonical_product(candidate)
    if not canon:
        return ""
    key = canon.casefold()
    for existing in normalize_products(products):
        if existing.casefold() == key:
            return existing
    return ""
