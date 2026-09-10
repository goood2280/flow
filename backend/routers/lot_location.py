"""LOT Current Location query and CSV export using canonical WIP parquet cache."""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from core import fab_reference
from core import lot_progress_cache
from core.latest_lot_cache_format import normalize_product
from core.lot_wip import step_desc_index

router = APIRouter(prefix="/api/lot-location", tags=["lot-location"])


class LotLocationQueryRequest(BaseModel):
    lot_ids: list[str] = Field(default_factory=list)
    raw_text: str = Field(default="")
    match_root: bool = Field(default=True)


async def _extract_query_payload(request: Request) -> LotLocationQueryRequest:
    body_bytes = await request.body()
    if not body_bytes:
        return LotLocationQueryRequest()
    try:
        data = json.loads(body_bytes.decode("utf-8-sig"))
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                data = {"raw_text": data}
        if isinstance(data, dict):
            return LotLocationQueryRequest(**data)
        elif isinstance(data, list):
            return LotLocationQueryRequest(lot_ids=[str(x) for x in data])
    except Exception:
        text_content = body_bytes.decode("utf-8-sig", errors="replace")
        return LotLocationQueryRequest(raw_text=text_content)
    return LotLocationQueryRequest()


def parse_lot_ids(lot_ids: list[str] | None, raw_text: str = "") -> list[str]:
    """Parse and normalize LOT IDs preserving original appearance order."""
    tokens: list[str] = []
    if lot_ids:
        for item in lot_ids:
            if isinstance(item, str):
                for part in re.split(r"[\r\n\t,;\s]+", item):
                    clean = part.strip()
                    if clean:
                        tokens.append(clean)
    if raw_text:
        for part in re.split(r"[\r\n\t,;\s]+", raw_text):
            clean = part.strip()
            if clean:
                tokens.append(clean)

    out: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        key = token.upper()
        # Filter out common header names if pasted by mistake
        if key in {"LOT_ID", "LOTID", "LOT ID", "ROOT_LOT_ID", "ROOT_LOT"}:
            continue
        if key not in seen:
            seen.add(key)
            out.append(token)
    return out


def _natural_wafer_sort_key(wafer_id: str) -> tuple[int, str]:
    cleaned = str(wafer_id or "").strip()
    digits = re.findall(r"\d+", cleaned)
    if digits:
        try:
            return (int(digits[0]), cleaned)
        except Exception:
            pass
    return (999999, cleaned)


def query_lot_locations(lot_ids: list[str], match_root: bool = True) -> dict[str, Any]:
    clean_lots = parse_lot_ids(lot_ids)
    if not clean_lots:
        return {
            "items": [],
            "stats": {
                "requested_count": 0,
                "matched_lot_count": 0,
                "matched_wafer_count": 0,
                "unmatched_lots": [],
            },
        }

    parquet_file = Path(lot_progress_cache.filebrowser_cache_parquet_file())
    if not parquet_file.is_file():
        return {
            "items": [],
            "stats": {
                "requested_count": len(clean_lots),
                "matched_lot_count": 0,
                "matched_wafer_count": 0,
                "unmatched_lots": clean_lots,
            },
            "warning": "WIP 진행 캐시 파일이 존재하지 않습니다. 먼저 캐시를 갱신해 주세요.",
        }

    import polars as pl

    requested_upper = [lot.upper() for lot in clean_lots]
    requested_set = set(requested_upper)
    lot_order_map = {lot: index for index, lot in enumerate(requested_upper)}

    try:
        lf = pl.scan_parquet(parquet_file)
        schema_cols = lf.collect_schema().names()
        needed = ["product", "root_lot_id", "wafer_id", "lot_id", "step_id", "function_step", "lot_type", "tkout_time"]
        available_cols = [c for c in needed if c in schema_cols]

        lot_id_col = (
            pl.col("lot_id").cast(pl.Utf8, strict=False).fill_null("")
            .str.strip_chars().str.to_uppercase()
        )
        predicate = lot_id_col.is_in(requested_upper)

        # Base lot matching (e.g. A1000A matches A1000A.3 or A1000A-01)
        lot_base_col = (
            lot_id_col.str.split(".").list.get(0).str.split("-").list.get(0)
        )
        predicate |= lot_base_col.is_in(requested_upper)

        if match_root and "root_lot_id" in schema_cols:
            root_id_col = (
                pl.col("root_lot_id").cast(pl.Utf8, strict=False).fill_null("")
                .str.strip_chars().str.to_uppercase()
            )
            predicate |= root_id_col.is_in(requested_upper)

        df = lf.filter(predicate).select(available_cols).collect()
        raw_rows = df.to_dicts()
    except Exception as exc:
        raise HTTPException(500, f"WIP Parquet 캐시 조회 실패: {exc}")

    # Build Vehicle Matching step_desc index
    try:
        vm_rows = fab_reference.vehicle_matching_rows()
        by_product_step, by_step = step_desc_index(rows=vm_rows)
    except Exception:
        by_product_step, by_step = {}, {}

    matched_lots_found: set[str] = set()
    items: list[dict[str, Any]] = []

    for row in raw_rows:
        lot_id = str(row.get("lot_id") or "").strip()
        root_lot_id = str(row.get("root_lot_id") or "").strip()
        wafer_id = str(row.get("wafer_id") or "").strip()
        step_id = str(row.get("step_id") or "").strip()
        product = str(row.get("product") or "").strip()
        func_step = str(row.get("function_step") or "").strip()
        lot_type = str(row.get("lot_type") or "").strip()
        tkout_time = str(row.get("tkout_time") or "").strip()

        # Resolve step_desc: product+step_id exact > global step_id > function_step
        product_key = normalize_product(product)
        step_key = step_id.upper()
        vm_entry = (
            by_product_step.get((product_key, step_key), {})
            if product_key
            else by_step.get(step_key, {})
        )
        if not vm_entry:
            vm_entry = by_step.get(step_key, {})

        step_desc = str(vm_entry.get("step_desc") or "").strip() or func_step

        # Record which requested token matched this row
        lot_upper = lot_id.upper()
        root_upper = root_lot_id.upper()
        base_upper = lot_upper.split(".")[0].split("-")[0]
        matched_token = ""
        matched_rank = 999999
        if lot_upper in lot_order_map:
            matched_token = clean_lots[lot_order_map[lot_upper]]
            matched_rank = lot_order_map[lot_upper]
            matched_lots_found.add(lot_upper)
        elif root_upper in lot_order_map:
            matched_token = clean_lots[lot_order_map[root_upper]]
            matched_rank = lot_order_map[root_upper]
            matched_lots_found.add(root_upper)
        elif base_upper in lot_order_map:
            matched_token = clean_lots[lot_order_map[base_upper]]
            matched_rank = lot_order_map[base_upper]
            matched_lots_found.add(base_upper)

        items.append({
            "lot_id": lot_id,
            "root_lot_id": root_lot_id,
            "wafer_id": wafer_id,
            "current_step_id": step_id,
            "step_desc": step_desc,
            "product": product,
            "lot_type": lot_type,
            "tkout_time": tkout_time,
            "_sort_rank": matched_rank,
            "_wafer_key": _natural_wafer_sort_key(wafer_id),
            "matched_input": matched_token,
        })

    # Sort rows by user's input lot order, then wafer_id naturally
    items.sort(key=lambda item: (item["_sort_rank"], item["lot_id"], item["_wafer_key"]))

    # Clean internal sort helper keys
    for item in items:
        item.pop("_sort_rank", None)
        item.pop("_wafer_key", None)

    unmatched = [
        lot for lot in clean_lots
        if lot.upper() not in matched_lots_found
        and lot.upper().split(".")[0].split("-")[0] not in matched_lots_found
    ]

    return {
        "items": items,
        "stats": {
            "requested_count": len(clean_lots),
            "matched_lot_count": len(matched_lots_found),
            "matched_wafer_count": len(items),
            "unmatched_lots": unmatched,
        },
    }


@router.post("/query")
async def query_lot_location_endpoint(request: Request):
    req = await _extract_query_payload(request)
    lot_ids = req.lot_ids
    if req.raw_text:
        lot_ids = parse_lot_ids(lot_ids, req.raw_text)
    return query_lot_locations(lot_ids, match_root=req.match_root)


@router.post("/export-csv")
async def export_lot_location_csv(request: Request):
    req = await _extract_query_payload(request)
    lot_ids = req.lot_ids
    if req.raw_text:
        lot_ids = parse_lot_ids(lot_ids, req.raw_text)
    result = query_lot_locations(lot_ids, match_root=req.match_root)
    items = result.get("items") or []

    output = io.StringIO()
    # Write UTF-8 BOM for Excel compatibility
    output.write("\ufeff")
    fieldnames = [
        "lot_id",
        "root_lot_id",
        "wafer_id",
        "current_step_id",
        "step_desc",
        "product",
        "lot_type",
        "tkout_time",
    ]
    header_labels = {
        "lot_id": "lot_id",
        "root_lot_id": "root_lot_id",
        "wafer_id": "wafer_id",
        "current_step_id": "현step_id",
        "step_desc": "현 step_desc",
        "product": "제품",
        "lot_type": "lot_type",
        "tkout_time": "최근 이동 시간",
    }

    writer = csv.DictWriter(
        output,
        fieldnames=fieldnames,
        extrasaction="ignore",
        lineterminator="\r\n",
    )
    writer.writerow(header_labels)
    for row in items:
        writer.writerow(row)

    csv_bytes = output.getvalue().encode("utf-8")
    now_str = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"lot_locations_{now_str}.csv"

    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )
