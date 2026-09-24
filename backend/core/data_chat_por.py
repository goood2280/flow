"""Bounded current-POR lookup for the active Home data chat.

The live reference is the same f_step source used by SplitTable: an available
``credential/f_step.csv`` is authoritative, otherwise
``confidential/f_step.parquet`` is used.  Only exact product and step matches
are accepted; ambiguous or missing mappings remain a human choice in
``pending_por``.
"""
from __future__ import annotations

import csv
from copy import deepcopy
from pathlib import Path
import re
from typing import Any

from core import auth, fab_reference
from core.paths import PATHS


_INTENT = re.compile(r"(?:현재|지금|전산)?\s*(?:POR|피오알|레시피|recipe)", re.I)
_QUESTION = re.compile(r"뭐|무엇|알려|조회|확인|어떤|what|show", re.I)
_STEP = re.compile(r"(?<![A-Za-z0-9_])([A-Z]{1,3}\d{5,6}[A-Z0-9]{0,4})(?![A-Za-z0-9_])", re.I)
_CANCEL = re.compile(r"^\s*취소(?:해|해줘)?[.!\s]*$", re.I)
_OTHER = re.compile(r"스플릿|split|대시보드|dashboard|위치|어디|인폼|inform|inline|인라인|ET\s*(?:시간|데이터)|수율|yield", re.I)
_HEADER_CLEAN = re.compile(r"[^0-9a-z가-힣]+", re.I)
_STEP_ALIASES = ("stepid", "step", "operationid", "operation", "oper", "operno", "공정id", "공정", "스텝id", "스텝")
_RECIPE_ALIASES = ("currentporppid", "porppid", "standardppid", "currentppid", "ppid", "recipeid", "recipe", "currentpor", "por")
_PRODUCT_ALIASES = ("product", "productid", "productcode", "prodid", "device", "deviceid", "vehicle")


def _key(value: Any) -> str:
    return _HEADER_CLEAN.sub("", str(value or "").strip().casefold())


def _column(columns: list[str], aliases: tuple[str, ...]) -> str:
    by_key = {_key(name): name for name in columns if str(name or "").strip()}
    return next((by_key.get(_key(alias), "") for alias in aliases if by_key.get(_key(alias))), "")


def _ci_child(root: Path, name: str) -> Path | None:
    try:
        return next((item for item in root.iterdir() if item.name.casefold() == name.casefold()), None)
    except OSError:
        return None


def _roots() -> list[Path]:
    output: list[Path] = []
    seen: set[str] = set()
    for raw in (PATHS.db_root, PATHS.base_root):
        try:
            root = Path(raw).resolve()
        except OSError:
            root = Path(raw)
        marker = str(root).casefold()
        if marker not in seen:
            seen.add(marker)
            output.append(root)
    return output


def _active_paths() -> list[Path]:
    csv_paths: list[Path] = []
    for root in _roots():
        folder = _ci_child(root, "credential")
        candidate = _ci_child(folder, "f_step.csv") if folder and folder.is_dir() else None
        if candidate and candidate.is_file():
            csv_paths.append(candidate)
    if csv_paths:
        return csv_paths
    parquet_paths: list[Path] = []
    for root in _roots():
        folder = _ci_child(root, "confidential")
        candidate = _ci_child(folder, "f_step.parquet") if folder and folder.is_dir() else None
        if candidate and candidate.is_file():
            parquet_paths.append(candidate)
    return parquet_paths


def _raw_rows(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    if path.suffix.casefold() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            return list(reader.fieldnames or []), list(reader)
    import polars as pl

    frame = pl.read_parquet(path)
    return frame.columns, frame.to_dicts()


def _catalog() -> dict[str, dict[str, Any]]:
    """Return product-keyed current mappings without exposing source contents."""
    catalog: dict[str, dict[str, Any]] = {}
    for path in _active_paths():
        try:
            columns, rows = _raw_rows(path)
        except Exception:
            continue
        step_col = _column(columns, _STEP_ALIASES)
        recipe_col = _column(columns, _RECIPE_ALIASES)
        product_col = _column(columns, _PRODUCT_ALIASES)
        if not step_col or not recipe_col:
            continue
        path_catalog: dict[str, dict[str, Any]] = {}
        for raw in rows:
            product = str(raw.get(product_col) or "").strip() if product_col else ""
            if product_col and not product:
                continue
            step_id = str(raw.get(step_col) or "").strip()
            ppid = str(raw.get(recipe_col) or "").strip()
            if not step_id:
                continue
            product_key = product.casefold() if product else "*"
            source = path_catalog.setdefault(product_key, {"product": product, "rows": {}})
            # The active f_step convention is final non-empty recipe wins.
            if ppid:
                source["rows"][step_id.casefold()] = {"product": product, "step_id": step_id, "ppid": ppid}
        # Root priority is deterministic: the first source for a product wins.
        for product_key, source in path_catalog.items():
            catalog.setdefault(product_key, source)
    return catalog


def _request_user(request: Any) -> dict:
    if request is None:
        return {}
    state = getattr(request, "state", None)
    user = getattr(state, "user", None) if state is not None else None
    return user if isinstance(user, dict) else auth.current_user(request)


def _allowed(request: Any) -> bool:
    user = _request_user(request)
    permissions = auth.effective_permissions(user)
    tabs = permissions.get("tabs") or []
    all_tabs = isinstance(tabs, str) and tabs in {"*", "__all__"}
    return user.get("role") == "admin" or all_tabs or "*" in tabs or "splittable" in tabs


def _reply(message: str, context: dict, **tool: Any) -> dict:
    from core.data_chat import reply

    return reply(message, context=context, ok=not bool(tool.get("missing") or tool.get("error")),
                 tool={"feature": "por", "action": "por.current", **tool})


def _options_for(catalog: dict[str, dict[str, Any]], step_id: str, product: str = "") -> list[dict[str, str]]:
    key = step_id.casefold()
    if product:
        source = catalog.get(product.casefold()) or catalog.get("*") or {}
        row = (source.get("rows") or {}).get(key)
        return [dict(row)] if row else []
    if set(catalog) == {"*"}:
        row = (catalog["*"].get("rows") or {}).get(key)
        return [dict(row)] if row else []
    return [dict(source["rows"][key]) for source in catalog.values() if key in (source.get("rows") or {})]


def _product_in(text: str, catalog: dict[str, dict[str, Any]]) -> str:
    products = [source["product"] for key, source in catalog.items() if key != "*" and source.get("product")]
    hits = [product for product in products if re.search(
        r"(?<![A-Za-z0-9_-])" + re.escape(product) + r"(?![A-Za-z0-9_-])", text, re.I)]
    return hits[0] if len({hit.casefold() for hit in hits}) == 1 else ""


def _ask(state: dict, step_id: str, options: list[dict[str, str]], message: str) -> dict:
    choices = [{"product": item.get("product", ""), "step_id": item["step_id"], "ppid": item["ppid"]} for item in options]
    state["pending_por"] = {"step_id": step_id, "options": choices}
    labels = [f"{item.get('product') or '공통'} · {item['step_id']} · {item['ppid']}" for item in choices]
    return _reply(message, state, missing=["por_mapping"], needs_input=True,
                  clarification={"kind": "por_mapping", "title": message,
                                 "options": [{"label": label, "value": str(index + 1)} for index, label in enumerate(labels)],
                                 "allow_other": True, "placeholder": "후보 번호 또는 정확한 제품명/PPID"},
                  table={"columns": ["번호", "후보"], "rows": [{"번호": i + 1, "후보": label} for i, label in enumerate(labels)]})


def _selected(text: str, options: list[dict[str, str]]) -> dict[str, str] | None:
    ordinal = re.fullmatch(r"\s*(\d+)\s*번?\s*", text)
    if ordinal and 0 < int(ordinal.group(1)) <= len(options):
        return options[int(ordinal.group(1)) - 1]
    hits = []
    folded = text.strip().casefold()
    for option in options:
        values = {str(option.get(key) or "").casefold() for key in ("product", "ppid")}
        if folded and folded in values:
            hits.append(option)
    return hits[0] if len(hits) == 1 else None


def _answer(state: dict, mapping: dict[str, str]) -> dict:
    classification = fab_reference.classify_ppid_knob(mapping["ppid"], product=mapping.get("product", ""))
    categories = list(dict.fromkeys(str(item.get("category") or "").strip()
                                    for item in classification.get("matches") or [] if item.get("category")))
    state.pop("pending_por", None)
    state["product"] = mapping.get("product") or state.get("product", "")
    state["step_id"] = mapping["step_id"]
    category_text = ", ".join(categories) if categories else "ppid_knob 분류 규칙 없음"
    product_text = f"{mapping['product']} · " if mapping.get("product") else ""
    return _reply(f"{product_text}{mapping['step_id']}의 현재 전산 POR은 {mapping['ppid']}입니다. 분류: {category_text}.",
                  state, ppid=mapping["ppid"], categories=categories,
                  scope={"product": mapping.get("product", ""), "step_id": mapping["step_id"]},
                  sources=["현재 f_step POR 기준", "ppid_knob 분류 규칙"], warnings=[],
                  table={"columns": ["제품", "공정", "전산 POR PPID", "카테고리"], "rows": [{
                      "제품": mapping.get("product") or "공통", "공정": mapping["step_id"],
                      "전산 POR PPID": mapping["ppid"], "카테고리": category_text}], "total": 1},
                  interpretation={"summary": f"{mapping['step_id']}를 공정 ID로 해석했습니다. 현재 f_step의 전산 POR PPID를 찾은 뒤, 동일 PPID에 적용되는 ppid_knob 카테고리를 조회했습니다.",
                    "status": "completed", "origin": "f_step + ppid_knob 기준정보",
                    "details": [{"label": "공정", "value": mapping["step_id"]}, {"label": "전산 POR", "value": mapping["ppid"]}, {"label": "분류", "value": category_text}], "unresolved": []})


def dispatch(text: str, context: dict, request: Any = None) -> dict | None:
    """Handle an explicit current-POR question or a pending POR choice."""
    pending = context.get("pending_por") or {}
    explicit = bool(_INTENT.search(text or "") and (_QUESTION.search(text or "") or _STEP.search(text or "")))
    if not explicit and not pending:
        return None
    if pending and not explicit and _OTHER.search(text or ""):
        context.pop("pending_por", None)
        return None
    state = deepcopy(context)
    if pending and _CANCEL.fullmatch(text or ""):
        state.pop("pending_por", None)
        return _reply("POR 조회 조건 확인을 취소했습니다.", state)
    if not _allowed(request):
        state.pop("pending_por", None)
        return _reply("현재 계정에는 SplitTable 기준정보 조회 권한이 없습니다.", state,
                      error="permission_denied", blocked=True)

    catalog = _catalog()
    if pending and not explicit:
        options = pending.get("options") or []
        choice = _selected(text, options)
        if choice:
            # Re-resolve against the active source so a stale pending value cannot be used.
            current = _options_for(catalog, choice["step_id"], choice.get("product", ""))
            exact = [item for item in current if item["ppid"].casefold() == choice["ppid"].casefold()]
            if len(exact) == 1:
                return _answer(state, exact[0])
        if not options:
            product = _product_in(text, catalog)
            current = _options_for(catalog, str(pending.get("step_id") or ""), product) if product else []
            if len(current) == 1:
                return _answer(state, current[0])
        return _ask(state, str(pending.get("step_id") or ""), options,
                    "저장된 후보 중 하나를 번호, 정확한 제품명 또는 PPID로 선택해 주세요.")

    steps = list(dict.fromkeys(match.group(1).upper() for match in _STEP.finditer(text or "")))
    if len(steps) != 1:
        return _ask(state, steps[0] if steps else "", [], "조회할 f_step의 정확한 step_id 하나를 알려 주세요.")
    step_id = steps[0]
    product = _product_in(text, catalog)
    if not product:
        inherited = str(context.get("confirmed_product") or context.get("product") or "").strip()
        if inherited.casefold() in catalog:
            product = catalog[inherited.casefold()]["product"]
    options = _options_for(catalog, step_id, product)
    if len(options) == 1:
        return _answer(state, options[0])
    if not options:
        return _ask(state, step_id, [], f"{step_id}의 현재 POR을 활성 f_step 범위에서 하나로 확인하지 못했습니다. 정확한 제품명을 알려 주세요.")
    return _ask(state, step_id, options, f"{step_id}의 현재 POR 후보가 여러 개입니다. 제품 또는 PPID를 선택해 주세요.")
