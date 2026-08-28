"""Shared product selector order for SplitTable, Lot Management, and Dashboard."""
from __future__ import annotations

import re
import threading
from collections.abc import Callable, Iterable
from typing import Any, TypeVar

from core.paths import PATHS
from core.utils import load_json, save_json


_PREFIX_RE = re.compile(r"^ML_TABLE_", re.IGNORECASE)
_LOCK = threading.RLock()
_T = TypeVar("_T")


def canonical_product_name(value: Any) -> str:
    return _PREFIX_RE.sub("", str(value or "").strip())


def clean_product_order(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        name = canonical_product_name(value)
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        out.append(name[:200])
    return out[:2000]


def _settings_file():
    return PATHS.data_root / "product_order.json"


def load_product_order() -> list[str]:
    raw = load_json(_settings_file(), {})
    values = raw.get("product_order") if isinstance(raw, dict) else raw
    return clean_product_order(values)


def save_product_order(values: Any) -> list[str]:
    order = clean_product_order(values)
    with _LOCK:
        save_json(_settings_file(), {"product_order": order}, indent=2)
    return order


def order_products(
    values: Iterable[_T],
    *,
    name: Callable[[_T], Any] = lambda value: value,
    product_order: list[str] | None = None,
) -> list[_T]:
    """Apply explicit order and append unlisted products alphabetically."""
    items = list(values)
    order = clean_product_order(product_order if product_order is not None else load_product_order())
    ranks = {value.casefold(): index for index, value in enumerate(order)}

    def sort_key(item: _T):
        label = canonical_product_name(name(item))
        key = label.casefold()
        return (0, ranks[key]) if key in ranks else (1, key)

    return sorted(items, key=sort_key)
