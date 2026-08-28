"""Yield Map API — BIN-backed products and wafer trellis data."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from core import yield_map as _ym
from core import teg_map as _tm
from core.auth import canonical_tab_token, current_user, require_page_manager


router = APIRouter(prefix="/api/yield-map", tags=["yield-map"])
_require_manager = require_page_manager("yieldmap")


def _product_key(value: str) -> str:
    text = str(value or "").strip().casefold()
    text = re.sub(r"^product=", "", text)
    text = re.sub(r"^(?:ml|et|inline)_table_", "", text)
    return re.sub(r"[\s_-]+", "", text)


def _matching_geometry_vehicle(user: dict, product: str) -> str:
    catalog = _tm.visible_product_catalog(user)
    exact = next((row for row in catalog
                  if str(row.get("vehicle") or "").casefold() == str(product or "").casefold()), None)
    if exact:
        return str(exact.get("vehicle") or "")
    key = _product_key(product)
    matched = next((row for row in catalog if _product_key(row.get("vehicle")) == key), None)
    return str((matched or {}).get("vehicle") or "")


def _require_user(user=Depends(current_user)) -> dict:
    if user.get("role") == "admin" or "yieldmap" in (user.get("page_manager") or []):
        return user
    raw = user.get("tabs") or []
    values = raw if isinstance(raw, list) else str(raw).split(",")
    if any(canonical_tab_token(value) == "yieldmap" for value in values):
        return user
    raise HTTPException(403, "Yield Map page permission required")


class ProductConfigReq(BaseModel):
    source: str = ""
    vehicle: str = ""
    fields: dict = {}
    bin_map: list[dict] = []
    bin_colors: dict = {}
    shot_layout: dict = {}


class ShotFieldMappingReq(BaseModel):
    product: str
    kind: str
    fields: dict = {}
    value_columns: list[str] = []
    scope: str = "database"


class ShotScanReq(BaseModel):
    source: str = ""
    fields: dict = {}
    root_lot_id: str = ""
    lot_id: str = ""
    wafer_id: str = ""


class EtIndexMapReq(BaseModel):
    product: str
    vehicle: str
    root_lot_id: str
    wafer_id: str = ""
    step_id: str = ""
    step_seq: str = ""
    item_alias: str
    item_source: str = "et_download"
    addp_form: str = ""
    split_source: str = ""


class EtIndexCompareReq(EtIndexMapReq):
    yield_product: str
    bin_name: str = "yield"


class ShotSourcesCompareReq(BaseModel):
    yield_product: str
    et_product: str
    inline_product: str
    vehicle: str
    root_lot_id: str
    wafer_id: str = ""
    bin_name: str = "yield"
    et_item_id: str = ""
    inline_item_id: str = ""
    et_item_source: str = "raw"
    et_addp_form: str = ""
    step_id: str = ""
    step_seq: str = ""
    inline_step_id: str = ""
    inline_table: str = ""
    split_source: str = ""


class RelationMetricReq(BaseModel):
    id: str
    kind: str
    label: str = ""
    bin_name: str = "yield"
    item_id: str = ""
    step_id: str = ""
    step_seq: str = ""
    inline_table: str = ""


class MetricRelationsReq(BaseModel):
    product: str
    vehicle: str
    root_lot_id: str
    wafer_id: str = ""
    metrics: list[RelationMetricReq]
    color_source: str = "none"
    color_field: str = ""
    split_source: str = ""
    target_metric_id: str = ""
    tkout_from: str = ""
    tkout_to: str = ""


class RelationshipSaveReq(BaseModel):
    product: str
    left_metric: str
    right_metric: str
    status: str
    corr: float | None = None
    r2: float | None = None
    note: str = ""


def _et_index_map(req: EtIndexMapReq, user: dict) -> dict:
    """Run the ET Download REAL/ADDP engine and normalize it for WF MAP."""
    from routers import reformatize as _rf

    catalog = _rf.list_items(req.product, user)
    visible_items = catalog.get("items") or []
    item_specs = {str(row.get("alias") or ""): row for row in visible_items if row.get("alias")}
    alias = str(req.item_alias or "").strip()
    source = str(req.item_source or "et_download").strip().lower()
    filters = _rf.Filters(
        lot_filter=str(req.root_lot_id or "").strip(),
        wafer_filter=str(req.wafer_id or "").strip(),
        step_filter=str(req.step_id or "").strip(),
        step_seq_filter=str(req.step_seq or "").strip(),
    )
    formula = ""
    if source == "test_addp":
        if user.get("role") != "admin":
            raise HTTPException(403, "관리자만 Test ADDP를 WF MAP에서 실행할 수 있습니다")
        formula = str(req.addp_form or "").strip()
        if not alias or not formula:
            raise HTTPException(400, "Test ADDP alias와 ADDP Form을 입력해 주세요")
        out, _aliases, errors, vehicle_csv, notice = _rf._run_test(
            req.product, [_rf.TestItem(alias=alias, addp_form=formula)], filters,
            auto_trim=False,
        )
        item_specs = {**item_specs, alias: {
            "alias": alias, "category": "addp", "itemid": "", "addp_form": formula,
            "refs": _rf.formula_refs(formula), "test": True,
        }}
        item_names = [*item_specs.keys()]
    else:
        if source != "et_download":
            raise HTTPException(400, "ET ITEM source는 et_download 또는 test_addp여야 합니다")
        if alias not in item_specs:
            raise HTTPException(400, f"ET Download에 공개된 ITEM alias가 아닙니다: {alias}")
        out, _out_cols, errors, vehicle_csv, _table, _raw_rows, notice = _rf._compute(
            req.product, filters, selected_items=[alias], auto_trim=False,
        )
        formula = str(item_specs.get(alias, {}).get("addp_form") or "")
        item_names = list(item_specs)
    try:
        return _ym.et_index_map_data(
            req.product, req.vehicle, req.root_lot_id, out, alias,
            wafer_id=req.wafer_id, step_id=req.step_id, step_seq=req.step_seq,
            items=item_names,
            item_specs=item_specs, item_source=source, item_formula=formula,
            rule_errors=errors, notice=notice, vehicle_csv=vehicle_csv,
            split_source=req.split_source,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/bootstrap")
def bootstrap(user=Depends(_require_user)):
    sources = _ym.discover_sources()
    configs = _ym.load_config().get("products") or {}
    products = _ym.available_products(sources, configs)
    visible_geometry = _tm.visible_product_catalog(user)
    inline_tables = [
        {"table_name": row.get("table_name", ""), "vehicle": row.get("vehicle", ""),
         "shot_count": len(row.get("shots") or [])}
        for row in _tm.load_inline_map_settings().get("tables") or []
    ]
    return {
        "ok": True, "products": products, "sources": sources,
        "shot_sources": _ym.discover_shot_sources(),
        "split_sources": _ym.discover_split_sources(),
        "inline_matching": _ym.inline_matching_rules(),
        "geometry_products": visible_geometry,
        "inline_tables": inline_tables,
        "configs": configs,
        "can_edit": user.get("role") == "admin" or "yieldmap" in (user.get("page_manager") or []),
    }


@router.get("/preview")
def preview(source: str = Query(...), product: str = Query(""), _user=Depends(_require_user)):
    try:
        return {"ok": True, **_ym.preview(source, product)}
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/et-items")
def et_items(product: str = Query(...), user=Depends(_require_user)):
    """ET Download와 같은 visibility 규칙으로 REAL/ADDP alias를 반환한다."""
    from routers import reformatize as _rf
    try:
        return {"ok": True, **_rf.list_items(product, user)}
    except HTTPException as exc:
        # 원본 ET만 있고 vehicle reformatter가 없는 제품도 정상적인 WF MAP 제품이다.
        # 화면 bootstrap 단계에서 불필요한 400/에러 설명 요청을 만들지 않는다.
        if exc.status_code == 400:
            return {"ok": True, "product": product, "vehicle_csv": "", "items": [],
                    "notice": str(exc.detail)}
        raise


@router.post("/et-index-map")
def et_index_map(req: EtIndexMapReq, user=Depends(_require_user)):
    if not _tm.can_access_product(user, req.vehicle):
        raise HTTPException(403, "WF geometry product permission required")
    return {"ok": True, **_et_index_map(req, user)}


@router.post("/compare/et-index")
def compare_et_index(req: EtIndexCompareReq, user=Depends(_require_user)):
    if not _tm.can_access_product(user, req.vehicle):
        raise HTTPException(403, "WF geometry product permission required")
    et_map = _et_index_map(req, user)
    try:
        return {"ok": True, **_ym.compare_shot_metrics(
            req.yield_product, req.product, req.vehicle, req.root_lot_id,
            req.bin_name, req.item_alias, wafer_id=req.wafer_id,
            step_id=req.step_id, step_seq=req.step_seq, split_source=req.split_source,
            et_map_data=et_map,
        )}
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/compare/shot-sources")
def compare_shot_sources(req: ShotSourcesCompareReq, user=Depends(_require_user)):
    """Compare Yield, ET and Inline averages on Yield-overlapping shot coordinates."""
    if not _tm.can_access_product(user, req.vehicle):
        raise HTTPException(403, "WF geometry product permission required")
    et_map = None
    if req.et_item_source != "raw":
        et_map = _et_index_map(EtIndexMapReq(
            product=req.et_product, vehicle=req.vehicle, root_lot_id=req.root_lot_id,
            wafer_id=req.wafer_id, step_id=req.step_id, step_seq=req.step_seq,
            item_alias=req.et_item_id, item_source=req.et_item_source,
            addp_form=req.et_addp_form, split_source=req.split_source,
        ), user)
    try:
        return {"ok": True, **_ym.compare_shot_sources(
            req.yield_product, req.et_product, req.inline_product, req.vehicle,
            req.root_lot_id, req.bin_name, req.et_item_id, req.inline_item_id,
            wafer_id=req.wafer_id, step_id=req.step_id, step_seq=req.step_seq,
            inline_step_id=req.inline_step_id, inline_table=req.inline_table,
            split_source=req.split_source,
            et_map_data=et_map,
        )}
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/relation-options")
def relation_options(product: str = Query(...), _user=Depends(_require_user)):
    return {
        "ok": True,
        "fab_fields": _ym.fab_color_fields(product),
        "relationships": [row for row in _ym.load_relationships()
                          if str(row.get("product") or "").casefold() == product.casefold()],
    }


@router.get("/column-mapping")
def column_mapping(product: str = Query(""), kind: str = Query(...),
                   scope: str = Query("database"), _user=Depends(_require_user)):
    try:
        return {"ok": True, **_ym.shot_field_options(product, kind, scope)}
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/column-mapping")
def column_mapping_put(req: ShotFieldMappingReq, _user=Depends(_require_manager)):
    try:
        return {"ok": True, **_ym.save_shot_fields(
            req.product, req.kind, req.fields, req.value_columns, req.scope,
        )}
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/relations")
def metric_relations(req: MetricRelationsReq, user=Depends(_require_user)):
    if not _tm.can_access_product(user, req.vehicle):
        raise HTTPException(403, "WF geometry product permission required")
    try:
        return {"ok": True, **_ym.compare_metric_relations(
            req.product, req.vehicle, req.root_lot_id,
            [metric.model_dump() for metric in req.metrics], wafer_id=req.wafer_id,
            color_source=req.color_source, color_field=req.color_field,
            split_source=req.split_source, target_metric_id=req.target_metric_id,
            tkout_from=req.tkout_from, tkout_to=req.tkout_to,
        )}
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/relationships")
def relationship_save(req: RelationshipSaveReq, user=Depends(_require_user)):
    try:
        return {"ok": True, "relationship": _ym.save_relationship(
            req.model_dump(), user.get("username", ""),
        )}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/config/{product}")
def config_put(product: str, req: ProductConfigReq, user=Depends(_require_manager)):
    vehicle = _matching_geometry_vehicle(user, product)
    if not vehicle:
        raise HTTPException(400, "같은 제품명의 WF geometry가 없습니다")
    try:
        return {"ok": True, "product": product,
                "config": _ym.save_product_config(product, {**req.model_dump(), "vehicle": vehicle})}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/scan/{product}")
def scan(product: str, req: ShotScanReq, user=Depends(_require_manager)):
    """Match product chip coordinates to its Full Shot geometry and preview one wafer."""
    vehicle = _matching_geometry_vehicle(user, product)
    if not vehicle:
        raise HTTPException(400, "같은 제품명의 WF geometry가 없습니다")
    try:
        return {"ok": True, **_ym.scan_shot_layout(
            product, {**req.model_dump(), "vehicle": vehicle}, root_lot_id=req.root_lot_id,
            lot_id=req.lot_id, wafer_id=req.wafer_id,
        )}
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/map")
def get_map(product: str = Query(...), kind: str = Query("yield"), vehicle: str = Query(""),
            root_lot_id: str = Query(""), lot_id: str = Query(""), wafer_id: str = Query(""),
            item_id: str = Query(""), step_id: str = Query(""), step_seq: str = Query(""),
            inline_table: str = Query(""),
            split_source: str = Query(""), bin_name: str = Query(""),
            user=Depends(_require_user)):
    try:
        if str(kind or "yield").lower() in {"et", "inline"}:
            vehicle = _matching_geometry_vehicle(user, product)
            if not vehicle:
                raise HTTPException(400, "같은 제품명의 WF geometry가 없습니다")
            return {"ok": True, **_ym.shot_map_data(
                kind, product, vehicle, root_lot_id or lot_id, wafer_id=wafer_id,
                item_id=item_id, step_id=step_id, step_seq=step_seq, inline_table=inline_table,
                split_source=split_source,
            )}
        return {"ok": True, **_ym.map_data(
            product, root_lot_id=root_lot_id, lot_id=lot_id, wafer_id=wafer_id,
            selected_bin=bin_name,
        )}
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/compare")
def compare(yield_product: str = Query(...), et_product: str = Query(...),
            vehicle: str = Query(...), root_lot_id: str = Query(...),
            bin_name: str = Query("yield"), item_id: str = Query(""),
            wafer_id: str = Query(""), step_id: str = Query(""), step_seq: str = Query(""),
            split_source: str = Query(""), user=Depends(_require_user)):
    if not _tm.can_access_product(user, vehicle):
        raise HTTPException(403, "WF geometry product permission required")
    try:
        return {"ok": True, **_ym.compare_shot_metrics(
            yield_product, et_product, vehicle, root_lot_id, bin_name, item_id,
            wafer_id=wafer_id, step_id=step_id, step_seq=step_seq,
            split_source=split_source,
        )}
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
