# -*- coding: utf-8 -*-
"""routers/et_time.py — ET 측정시간 API.

ET raw DB(`1.RAWDATA_DB_ET/<PRODUCT>/**/*.parquet`)에서 root lot 별로
auto report 의 PGM(pt) 단위(step_seq(Npt)_중복차수) 측정 소요시간을 집계한다.
측정시간 = tkout_time - tkin_time. 같은 (step_id, PGM(pt)) 조합이면 wafer 가
달라도 측정시간은 동일하다는 업무 규칙을 전제로, 그룹당 한 행으로 요약한다.

ET 데이터 구조 기준 — auto report(f_et_test) 구조를 따른다:
  컬럼: fab_lot_id, lot_id, root_lot_id, wafer_id, process_id, part_id,
        step_id, step_seq, tkout_time, item_id, flat_zone, eqp_id,
        probe_card_id, chip_x_pos, chip_y_pos, subitem_id, et_value,
        temperature, total_site_cnt
  (flow 내부 샘플 DB 의 shot_x/shot_y·flat·value 명칭은 fallback 으로 흡수.
   tkin_time 은 auto report 소스에는 없지만 flow ET DB 에 있으면 측정시간 계산.)

PGM(pt) 라벨 규칙 — auto report Main.py 와 동일:
  - Point(pt) = 같은 (fab_lot_id, wafer_id, tkout_time) 측정 패키지의
    pivot 행 수 = 고유 (chip_x_pos, chip_y_pos, subitem_id) 조합 수
  - Duplicate_Count = (DC_Split, temperature, flat_zone, fab_lot_id,
    wafer_id, step_seq, Point) 그룹 안에서 tkout_time dense rank
    (DC_Split 은 step_id→DC layer 매핑이라 flow 에서는 step_id 로 대체,
     temperature 는 auto report 처럼 5 단위 반올림)
  - 라벨 = f"{step_seq}({Point}pt)_{Duplicate_Count}"

  GET /api/et-time/products?prefix=            ET DB 의 product 후보
  GET /api/et-time/lots?product=&prefix=       root_lot_id 후보
  GET /api/et-time/measure?product=&root_lot_id=  step_id × PGM(pt) 측정시간 표
"""
from __future__ import annotations

import datetime as dt
import logging

from fastapi import APIRouter, HTTPException, Query, Request

from core.auth import current_user

logger = logging.getLogger("flow.et_time")

router = APIRouter(prefix="/api/et-time", tags=["et-time"])

ET_SCAN_FILE_LIMIT = 60


def _parse_time(value) -> dt.datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day)
    text = str(value).strip()
    if not text:
        return None
    try:
        return dt.datetime.fromisoformat(text.replace(" ", "T"))
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M%S", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(text, fmt)
        except Exception:
            continue
    return None


def _fmt_duration(seconds) -> str:
    if seconds is None:
        return ""
    total = int(round(float(seconds)))
    sign = "-" if total < 0 else ""
    total = abs(total)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{sign}{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{sign}{m}m {s:02d}s"
    return f"{sign}{s}s"


def _seq_sort_key(v) -> tuple[int, str]:
    try:
        return (0, f"{int(str(v)):09d}")
    except Exception:
        return (1, str(v or ""))


def _norm_temperature(value) -> str:
    """auto report 와 동일하게 temperature 를 5 단위 반올림한 라벨로 정규화."""
    if value is None or value == "":
        return ""
    try:
        return str(int(round(float(value) / 5.0) * 5))
    except Exception:
        return str(value).strip()


def build_pgm_rows(packages: list[dict]) -> list[dict]:
    """wafer 단위 측정 패키지 목록 → (step_id, PGM(pt)) 행 목록.

    packages 행 스키마(키 없으면 빈 값으로 처리):
      {fab_lot_id, wafer_id, step_id, step_seq, flat_zone, temperature,
       tkin_time, tkout_time, pt_count}

    auto report 와 같은 방식으로 Duplicate_Count(dup)를 매긴 뒤,
    같은 (step_id, step_seq, pt, dup) = PGM(pt) 조합을 한 행으로 묶는다.
    """
    prepared = []
    for pkg in packages or []:
        step_id = str(pkg.get("step_id") or "").strip()
        if not step_id:
            continue
        tkin = _parse_time(pkg.get("tkin_time"))
        tkout = _parse_time(pkg.get("tkout_time"))
        prepared.append({
            "fab_lot_id": str(pkg.get("fab_lot_id") or pkg.get("lot_id") or "").strip(),
            "wafer_id": str(pkg.get("wafer_id") or "").strip(),
            "step_id": step_id,
            "step_seq": str(pkg.get("step_seq") or "").strip(),
            "flat_zone": str(pkg.get("flat_zone") if pkg.get("flat_zone") is not None else "").strip(),
            "temperature": _norm_temperature(pkg.get("temperature")),
            "pt": int(pkg.get("pt_count") or 0),
            "tkin": tkin,
            "tkout": tkout,
        })

    # Duplicate_Count — auto report groupby ['DC_Split','temperature','flat_zone',
    # 'fab_lot_id','wafer_id','step_seq','Point'] 의 tkout_time dense rank 와 동일.
    # DC_Split(step_id→DC layer 매핑)은 flow 에 매핑이 없어 step_id 로 대체.
    dup_groups: dict[tuple, list] = {}
    for row in prepared:
        key = (row["step_id"], row["temperature"], row["flat_zone"],
               row["fab_lot_id"], row["wafer_id"], row["step_seq"], row["pt"])
        dup_groups.setdefault(key, []).append(row)
    for rows in dup_groups.values():
        times = sorted({(r["tkout"] or dt.datetime.min) for r in rows})
        rank = {t: i + 1 for i, t in enumerate(times)}
        for r in rows:
            r["dup"] = rank[r["tkout"] or dt.datetime.min]

    grouped: dict[tuple, dict] = {}
    for row in prepared:
        seq = row["step_seq"]
        pgm = f"{seq}({row['pt']}pt)_{row['dup']}" if seq else f"({row['pt']}pt)_{row['dup']}"
        key = (row["step_id"], pgm)
        g = grouped.setdefault(key, {
            "step_id": row["step_id"],
            "pgm": pgm,
            "step_seq": seq,
            "pt": row["pt"],
            "dup": row["dup"],
            "wafers": set(),
            "package_count": 0,
            "tkin_min": None,
            "tkout_max": None,
            "durations": [],
        })
        g["package_count"] += 1
        if row["wafer_id"]:
            g["wafers"].add((row["fab_lot_id"], row["wafer_id"]))
        if row["tkin"] is not None and (g["tkin_min"] is None or row["tkin"] < g["tkin_min"]):
            g["tkin_min"] = row["tkin"]
        if row["tkout"] is not None and (g["tkout_max"] is None or row["tkout"] > g["tkout_max"]):
            g["tkout_max"] = row["tkout"]
        if row["tkin"] is not None and row["tkout"] is not None:
            g["durations"].append((row["tkout"] - row["tkin"]).total_seconds())

    out = []
    for g in grouped.values():
        durations = g.pop("durations")
        wafers = g.pop("wafers")
        dur = durations[0] if durations else None
        dur_min = min(durations) if durations else None
        dur_max = max(durations) if durations else None
        uniform = bool(durations) and abs(dur_max - dur_min) < 1.0
        out.append({
            **g,
            "wafer_count": len(wafers),
            "tkin_min": g["tkin_min"].isoformat(timespec="seconds") if g["tkin_min"] else "",
            "tkout_max": g["tkout_max"].isoformat(timespec="seconds") if g["tkout_max"] else "",
            "duration_sec": dur,
            "duration_min_sec": dur_min,
            "duration_max_sec": dur_max,
            "duration_uniform": uniform,
            "duration_text": (
                _fmt_duration(dur_min) if uniform
                else (f"{_fmt_duration(dur_min)} ~ {_fmt_duration(dur_max)}" if durations else "")
            ),
        })
    out.sort(key=lambda r: (r["step_id"], _seq_sort_key(r["step_seq"]), r["pt"], r["dup"]))
    return out


def _scan_et_packages(product: str, root_lot_id: str, lot_id: str = "") -> list[dict]:
    """ET raw parquet → wafer 단위 측정 패키지 목록.

    패키지 = 같은 (fab_lot, wafer_id, step_id, step_seq, flat_zone,
    temperature, tkin_time, tkout_time). pt_count 는 auto report Point 정의대로
    고유 측정 point(chip_x_pos, chip_y_pos, subitem_id) 수 — 컬럼이 없으면
    flow 내부 명칭(shot_x/shot_y), 그것도 없으면 행 수 fallback.
    """
    try:
        import polars as pl
    except Exception:
        raise HTTPException(500, "polars 미설치 — ET 측정시간 집계를 사용할 수 없습니다")
    from core.lot_step import (
        ET_ROOT, _data_product_values, _filter_valid_wafers, _parquet_files,
    )

    files = _parquet_files(ET_ROOT, product, source="et")
    if not files:
        raise HTTPException(404, f"ET DB 에서 product '{product}' 파일을 찾지 못했습니다")
    try:
        lf = pl.scan_parquet([str(f) for f in files[-ET_SCAN_FILE_LIMIT:]], hive_partitioning=True)
    except Exception:
        lf = pl.scan_parquet([str(f) for f in files[-ET_SCAN_FILE_LIMIT:]])
    lf = _filter_valid_wafers(lf)
    schema = lf.collect_schema().names()
    if "tkout_time" not in schema:
        raise HTTPException(422, "ET DB 스키마에 tkout_time 컬럼이 없어 측정시간을 계산할 수 없습니다")
    if "step_id" not in schema:
        raise HTTPException(422, "ET DB 스키마에 step_id 컬럼이 없습니다")

    filters = []
    prod_values = _data_product_values(product)
    if prod_values and "product" in schema:
        filters.append(pl.col("product").cast(pl.Utf8, strict=False).str.to_uppercase().is_in(sorted(prod_values)))
    root_text = str(root_lot_id or "").strip()
    lot_text = str(lot_id or "").strip()
    if root_text and "root_lot_id" in schema:
        filters.append(pl.col("root_lot_id").cast(pl.Utf8, strict=False) == root_text)
    elif lot_text:
        lot_filters = [
            pl.col(c).cast(pl.Utf8, strict=False) == lot_text
            for c in ("lot_id", "fab_lot_id")
            if c in schema
        ]
        if lot_filters:
            expr = lot_filters[0]
            for e in lot_filters[1:]:
                expr = expr | e
            filters.append(expr)
    if filters:
        expr = filters[0]
        for e in filters[1:]:
            expr = expr & e
        lf = lf.filter(expr)

    # 컬럼 명칭 해석 — auto report 명칭 우선, flow 내부 명칭 fallback.
    fab_lot_col = "fab_lot_id" if "fab_lot_id" in schema else ("lot_id" if "lot_id" in schema else "")
    flat_col = "flat_zone" if "flat_zone" in schema else ("flat" if "flat" in schema else "")
    temp_col = "temperature" if "temperature" in schema else ""
    tkin_col = "tkin_time" if "tkin_time" in schema else ""
    if {"chip_x_pos", "chip_y_pos"} <= set(schema):
        point_cols = ["chip_x_pos", "chip_y_pos"]
        if "subitem_id" in schema:
            point_cols.append("subitem_id")
    elif {"shot_x", "shot_y"} <= set(schema):
        point_cols = ["shot_x", "shot_y"]
    else:
        point_cols = []
    pt_expr = (pl.struct(point_cols).n_unique() if point_cols else pl.len()).alias("pt_count")

    group_cols = [c for c in ("wafer_id", "step_id", "step_seq", fab_lot_col, flat_col,
                              temp_col, tkin_col, "tkout_time") if c and c in schema]
    try:
        df = lf.group_by(group_cols).agg(pt_expr).collect()
    except Exception as e:
        logger.warning(f"et-time scan failed product={product} root={root_lot_id}: {e}")
        raise HTTPException(500, f"ET DB 조회 실패: {e}")
    rows = df.to_dicts()
    # 표준 키로 정규화 — build_pgm_rows 는 auto report 명칭 기준으로 동작.
    for r in rows:
        if fab_lot_col and fab_lot_col != "fab_lot_id":
            r["fab_lot_id"] = r.pop(fab_lot_col, None)
        if flat_col and flat_col != "flat_zone":
            r["flat_zone"] = r.pop(flat_col, None)
    return rows


@router.get("/products")
def et_time_products(request: Request, prefix: str = Query(""), limit: int = Query(500)):
    from core.lot_step import ET_ROOT, db_product_candidates
    current_user(request)
    products = db_product_candidates(
        source_root=ET_ROOT,
        source="et",
        prefix=prefix,
        limit=max(1, min(1000, int(limit or 500))),
    )
    return {"products": products}


@router.get("/lots")
def et_time_lots(request: Request, product: str = Query(""), prefix: str = Query(""),
                 limit: int = Query(200)):
    from core.lot_step import ET_ROOT, lot_id_candidates
    current_user(request)
    if not str(product or "").strip():
        return {"requires_product": True, "candidates": []}
    candidates = lot_id_candidates(
        product=product,
        source_root=ET_ROOT,
        source="et",
        prefix=prefix,
        limit=max(1, min(500, int(limit or 200))),
    )
    return {"candidates": candidates or []}


@router.get("/measure")
def et_time_measure(request: Request, product: str = Query(""),
                    root_lot_id: str = Query(""), lot_id: str = Query("")):
    from core.lot_step import lookup_step_meta
    current_user(request)
    product_text = str(product or "").strip()
    root_text = str(root_lot_id or "").strip()
    lot_text = str(lot_id or "").strip()
    if not product_text:
        raise HTTPException(400, "product 를 입력하세요")
    if not root_text and not lot_text:
        raise HTTPException(400, "root_lot_id 를 입력하세요")

    packages = _scan_et_packages(product_text, root_text, lot_text)
    if not packages and root_text and not lot_text:
        # 입력값이 root 가 아니라 fab lot / lot_id 인 경우 fallback 재조회.
        packages = _scan_et_packages(product_text, "", root_text)
    rows = build_pgm_rows(packages)
    for row in rows:
        meta = lookup_step_meta(product=product_text, step_id=row["step_id"])
        func = str(meta.get("function_step") or meta.get("func_step") or "").strip()
        if func:
            row["function_step"] = func

    total_sec = sum(
        (r["duration_min_sec"] if r.get("duration_uniform") else (r.get("duration_max_sec") or 0)) or 0
        for r in rows
        if r.get("duration_min_sec") is not None
    )
    has_tkin = any(r.get("duration_sec") is not None for r in rows)
    return {
        "product": product_text,
        "root_lot_id": root_text or lot_text,
        "rows": rows,
        "step_count": len({r["step_id"] for r in rows}),
        "pgm_count": len(rows),
        "total_duration_sec": total_sec,
        "total_duration_text": _fmt_duration(total_sec) if rows else "",
        "tkin_available": has_tkin,
    }
