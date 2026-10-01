"""Conservative priority normalization for ``ppid_knob.csv`` rows.

Rows that share a ``rule_order`` are one AND group.  This module therefore
never moves an individual row out of its group: it only swaps existing R# or
numeric labels between whole groups when a same-PPID exact rule would otherwise run
after a broader ``~~tkout_time >= ...`` rule.
"""

from __future__ import annotations

import re
from typing import Any


_EQ_OPERATORS = {"", "eq", "=", "==", "equal", "equals"}
_GTE_OPERATORS = {
    ">=", "ge", "gte", "at_least", "at least",
    "greater_equal", "greater than or equal", "greater_than_or_equal",
    "greater_than_or_equals", "greater_or_equal", "greater than or equal to",
    "larger than or equal", "larger_than_or_equal",
    "bigger than or equal", "bigger_than_or_equal",
}
_RULE_ORDER_RE = re.compile(r"^(?:R)?(\d+)$", re.IGNORECASE)
_TKOUT_MARKER_RE = re.compile(r"~~\s*tkout_time", re.IGNORECASE)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _field(row: dict, configured: str, *fallbacks: str) -> str:
    keys = [configured, *fallbacks]
    folded = {str(key).strip().casefold(): value for key, value in row.items()}
    for key in keys:
        value = folded.get(str(key or "").strip().casefold())
        if _text(value):
            return _text(value)
    return ""


def _operator_tokens(value: str) -> set[str]:
    text = _text(value).casefold().replace("-", "_")
    tokens = {text}
    tokens.update(part.strip(" ()[]{}:,;") for part in re.split(r"~~|\s*\|\s*", text))
    for alias in _GTE_OPERATORS:
        if re.search(r"(?<![a-z0-9_])" + re.escape(alias) + r"(?![a-z0-9_])", text):
            tokens.add(alias)
    return {token for token in tokens if token}


def _is_tkout_gte_rule(operator: str, value: str) -> bool:
    combined = f"{operator} {value}"
    if not _TKOUT_MARKER_RE.search(combined):
        return False
    return bool(_operator_tokens(combined) & _GTE_OPERATORS)


def _base_ppid(operator: str, value: str) -> str:
    """Return a PPID only when the marker layout makes it unambiguous."""
    value_text = _text(value)
    marker = _TKOUT_MARKER_RE.search(value_text)
    if marker:
        return value_text[:marker.start()].strip(" ~|,;")
    if _TKOUT_MARKER_RE.search(_text(operator)):
        return value_text
    return ""


def _numeric_order(value: str) -> int | None:
    match = _RULE_ORDER_RE.fullmatch(_text(value))
    return int(match.group(1)) if match else None


def _canonical_order(value: str) -> str:
    number = _numeric_order(value)
    return f"R{number}" if number is not None else _text(value).upper()


def _order_in_original_style(original: str, canonical: str) -> str:
    """Render a remapped group using each row's original numeric/R style."""
    number = _numeric_order(canonical)
    if number is None:
        return canonical
    return f"R{number}" if _text(original).upper().startswith("R") else str(number)


def normalize_ppid_knob_rule_order(
    rows: list[dict], *, schema: dict | None = None,
) -> dict[str, Any]:
    """Move broad tkout threshold groups behind same-PPID exact groups.

    The function is deliberately narrow.  It only acts when all of these are
    explicit in the rows: same feature, same PPID, an exact eq rule with a
    non-empty category, a different-category rule containing the literal
    ``~~tkout_time`` marker and a >= operator alias, and distinct numeric/R#
    groups.  Existing involved labels are permuted; unrelated labels and row
    order are left intact.  Ambiguous same-group cases are reported unchanged.
    """
    schema = schema or {}
    feature_col = _text(schema.get("feature_col")) or "feature_name"
    order_col = _text(schema.get("rule_order_col")) or "rule_order"
    operator_col = _text(schema.get("operator_col")) or "operator"
    value_col = _text(schema.get("value_col")) or _text(schema.get("ppid_col")) or "value"
    category_col = _text(schema.get("category_col")) or "category"

    result_rows = [dict(row) for row in (rows or []) if isinstance(row, dict)]
    candidates: dict[tuple[str, str], dict[str, set[str]]] = {}

    for row in result_rows:
        feature = _field(row, feature_col, "feature_name").casefold()
        order = _canonical_order(_field(row, order_col, "rule_order"))
        operator = _field(row, operator_col, "operator")
        value = _field(row, value_col, "value", "ppid")
        category = _field(row, category_col, "category")
        if not feature or _numeric_order(order) is None:
            continue
        if _is_tkout_gte_rule(operator, value):
            ppid = _base_ppid(operator, value).casefold()
            if ppid:
                slot = candidates.setdefault((feature, ppid), {
                    "exact": set(), "broad": set(),
                    "exact_categories": {}, "broad_categories": {},
                })
                slot["broad"].add(order)
                if category:
                    slot["broad_categories"].setdefault(order, set()).add(category.casefold())
        elif _text(operator).casefold().replace("-", "_") in _EQ_OPERATORS and value and category:
            slot = candidates.setdefault((feature, value.casefold()), {
                "exact": set(), "broad": set(),
                "exact_categories": {}, "broad_categories": {},
            })
            slot["exact"].add(order)
            slot["exact_categories"].setdefault(order, set()).add(category.casefold())

    changes: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    edges_by_feature: dict[str, set[tuple[str, str]]] = {}
    reasons_by_feature: dict[str, list[tuple[str, str]]] = {}
    for (feature, ppid), slot in candidates.items():
        exact = set(slot["exact"])
        broad = set(slot["broad"])
        if not exact or not broad:
            continue
        pair_edges = {
            (exact_order, broad_order)
            for exact_order in exact for broad_order in broad
            if any(
                exact_category != broad_category
                for exact_category in slot["exact_categories"].get(exact_order, set())
                for broad_category in slot["broad_categories"].get(broad_order, set())
            )
        }
        if not pair_edges:
            continue
        overlap = {before for before, after in pair_edges if before == after}
        if overlap:
            warnings.append({
                "feature_name": feature, "ppid": ppid,
                "rule_order": ",".join(sorted(overlap)),
                "reason": "exact and tkout threshold rules share one AND group",
            })
            continue
        edges = edges_by_feature.setdefault(feature, set())
        edges.update(pair_edges)
        reasons_by_feature.setdefault(feature, []).append((ppid, "same-PPID exact category before tkout_time threshold"))

    remap_by_feature: dict[str, dict[str, str]] = {}
    for feature, edges in edges_by_feature.items():
        labels = sorted({label for edge in edges for label in edge}, key=lambda label: _numeric_order(label) or 0)
        incoming = {label: 0 for label in labels}
        outgoing = {label: set() for label in labels}
        for before, after in edges:
            if after not in outgoing[before]:
                outgoing[before].add(after)
                incoming[after] += 1
        ready = [label for label in labels if incoming[label] == 0]
        desired_groups: list[str] = []
        while ready:
            ready.sort(key=lambda label: _numeric_order(label) or 0)
            current = ready.pop(0)
            desired_groups.append(current)
            for after in sorted(outgoing[current], key=lambda label: _numeric_order(label) or 0):
                incoming[after] -= 1
                if incoming[after] == 0:
                    ready.append(after)
        if len(desired_groups) != len(labels):
            warnings.append({
                "feature_name": feature, "ppid": "", "rule_order": "",
                "reason": "same-PPID priority constraints form a cycle",
            })
            continue
        mapping = {old: new for old, new in zip(desired_groups, labels) if old != new}
        if not mapping:
            continue
        remap_by_feature[feature] = mapping
        ppid_summary = ", ".join(ppid for ppid, _reason in reasons_by_feature.get(feature, []))
        for old, new in mapping.items():
            changes.append({
                "feature_name": feature, "ppid": ppid_summary,
                "from_rule_order": old, "to_rule_order": new,
                "reason": "same-PPID exact category before tkout_time threshold",
            })

    if remap_by_feature:
        for row in result_rows:
            feature = _field(row, feature_col, "feature_name").casefold()
            raw_order = _field(row, order_col, "rule_order")
            order = _canonical_order(raw_order)
            new_order = remap_by_feature.get(feature, {}).get(order)
            if new_order:
                accepted_order_cols = {order_col.casefold(), "rule_order"}
                actual_col = next(
                    (key for key in row if str(key).strip().casefold() in accepted_order_cols),
                    order_col,
                )
                row[actual_col] = _order_in_original_style(raw_order, new_order)

    return {"rows": result_rows, "changes": changes, "warnings": warnings}
