"""core/split_lead_tracker.py — Split 조건별 선두랏(가장 앞선 랏) 조회 및 Split 분포 집계.

1. ML_TABLE_<product> 에서 해당 split 열을 필터링한 뒤 최신 WIP 캐시(lot_progress_cache)를
   조회하여 step_id 기준으로 가장 공정 진행이 앞선 선두 랏을 찾는다.
2. split 조건/값이 모호할 경우 Human-in-the-loop 방식으로 후보 선택지(candidates)를 제공한다.
3. split 분포/점유율 요청 시 해당 split의 split별 점유율을 파이차트 그룹으로 집계한다.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import polars as pl

from core.lot_progress_cache import read_lot_progress_cache, compress_wafer_ids
from core.lot_wip import describe_step, step_label
from core.lot_tracker import step_number
from core.ml_table_lookup import discover_ml_table_files


def find_ml_table_path(product: str) -> Path | None:
    prod = str(product or "").strip().upper()
    if not prod:
        return None
    for file_path in discover_ml_table_files():
        stem = file_path.stem.upper()
        if stem == f"ML_TABLE_{prod}" or stem == prod or stem.replace("ML_TABLE_", "") == prod:
            return file_path
    return None


def get_product_split_columns(product: str) -> list[str]:
    path = find_ml_table_path(product)
    if not path or not path.is_file():
        return []
    try:
        schema = pl.read_parquet_schema(str(path))
        cols = list(schema.keys())
        knob_cols = [c for c in cols if "KNOB" in c.upper()]
        other_split = [c for c in cols if "SPLIT" in c.upper() and c not in knob_cols]
        mask_cols = [c for c in cols if "MASK" in c.upper() and c not in knob_cols]
        return knob_cols + other_split + mask_cols
    except Exception:
        return []


def resolve_split_column(product: str, text: str) -> tuple[str | None, list[str]]:
    """텍스트에서 언급된 split 컬럼을 매칭한다."""
    cols = get_product_split_columns(product)
    if not cols:
        return None, []

    text_clean = re.sub(r"[\s_]+", " ", text).lower()

    # 1. 완전 일치 또는 컬럼명이 텍스트에 포함
    exact = []
    for c in cols:
        c_clean = re.sub(r"[\s_]+", " ", c).lower()
        core_c = re.sub(r"^(knob|mask|fab)\s*", "", c_clean).strip()
        if c_clean in text_clean or (len(core_c) >= 3 and core_c in text_clean):
            exact.append(c)
    if len(exact) == 1:
        return exact[0], exact
    if len(exact) > 1:
        knobs = [c for c in exact if c.upper().startswith("KNOB_")]
        if len(knobs) == 1:
            return knobs[0], exact
        return None, exact

    # 2. 숫자.숫자 또는 키워드 매칭 (예: 5.0, PC, STI)
    step_match = re.search(r"(\d+(?:\.\d+)?)\s*([a-zA-Z가-힣_]+)?", text)
    if step_match:
        num = step_match.group(1)
        name = (step_match.group(2) or "").lower()
        matched = []
        for c in cols:
            if num in c:
                if not name or name in c.lower():
                    matched.append(c)
        if len(matched) == 1:
            return matched[0], matched
        if len(matched) > 1:
            knobs = [c for c in matched if c.upper().startswith("KNOB_")]
            if len(knobs) == 1:
                return knobs[0], matched
            return None, matched

    # 3. 영문 단어 매칭
    words = [w.lower() for w in re.findall(r"[a-zA-Z0-9_]+", text) if len(w) >= 2 and w.upper() not in {product.upper(), "LOT", "WIP", "STEP", "SPLIT"}]
    for word in words:
        cand = [c for c in cols if word in c.lower()]
        if len(cand) == 1:
            return cand[0], cand
        if len(cand) > 1:
            knobs = [c for c in cand if c.upper().startswith("KNOB_")]
            if len(knobs) == 1:
                return knobs[0], cand
            return None, cand

    return None, cols[:8]


def find_column_for_value(product: str, text: str) -> tuple[str | None, str | None]:
    """텍스트 내 단어가 특정 split 컬럼의 값(예: PPID_05_1_S0)과 일치하는지 전수 검사."""
    cols = get_product_split_columns(product)
    for col in cols:
        val_counts = get_split_value_counts(product, col)
        for vc in val_counts:
            val = vc["value"]
            if len(val) >= 2 and (val.lower() in text.lower() or val.upper() in text.upper()):
                return col, val
    return None, None


def get_split_value_counts(product: str, split_col: str) -> list[dict[str, Any]]:
    path = find_ml_table_path(product)
    if not path or not path.is_file():
        return []
    try:
        df = pl.read_parquet(str(path), columns=[split_col])
        series = df[split_col].drop_nulls()
        counts = series.value_counts().sort("count", descending=True)
        total = series.len() or 1
        results = []
        for row in counts.iter_rows(named=True):
            val = str(row[split_col])
            cnt = int(row["count"])
            results.append({
                "value": val,
                "count": cnt,
                "percent": round((cnt / total) * 100, 1),
            })
        return results
    except Exception:
        return []


def resolve_split_value(product: str, split_col: str, text: str) -> tuple[str | None, list[dict[str, Any]]]:
    """해당 split 컬럼의 고유값 중에서 텍스트에 언급된 값을 찾는다."""
    val_counts = get_split_value_counts(product, split_col)
    if not val_counts:
        return None, []

    text_clean = text.lower()
    matched = []
    for vc in val_counts:
        val = vc["value"]
        val_lower = val.lower()
        # S0, S1, S2, S3 or PPID_05_1_S0
        if val_lower in text_clean:
            matched.append(vc)
        elif len(val_lower) >= 2 and re.search(r"(?<![a-zA-Z0-9])" + re.escape(val_lower) + r"(?![a-zA-Z0-9])", text_clean):
            matched.append(vc)
        else:
            # S0, S1 단독 언급 검사
            s_match = re.search(r"\b(s[0-9]+)\b", val_lower)
            if s_match and s_match.group(1) in text_clean:
                matched.append(vc)

    if len(matched) == 1:
        return matched[0]["value"], val_counts
    return None, val_counts


def score_step_progress(step_id: str, step_desc: str = "", update_time: str = "") -> tuple[float, str, str]:
    """공정 진행도 점수 계산 (선두랏 판별용).

    1. step_desc 안의 공정 번호 (예: 94.0 > 10.0)
    2. alphanumeric step_id (예: AA100600 > AA100050)
    3. update_time
    """
    num, _ = step_number(step_desc)
    num_val = float(num) if num is not None else -1.0
    return (num_val, str(step_id or ""), str(update_time or ""))


def find_split_leading_lot(product: str, split_col: str, split_val: str) -> dict[str, Any]:
    """ML_TABLE_<product> 에서 해당 split 값의 wafer/lot을 필터링하고 최신 WIP 기준 선두랏 산출."""
    path = find_ml_table_path(product)
    if not path or not path.is_file():
        return {
            "ok": False,
            "error": f"{product}의 ML_TABLE 파일을 찾을 수 없습니다.",
        }

    try:
        df = pl.read_parquet(str(path), columns=["ROOT_LOT_ID", "LOT_ID", "WAFER_ID", split_col])
        filtered = df.filter(pl.col(split_col).cast(pl.Utf8).str.to_lowercase() == split_val.lower())
        if filtered.is_empty():
            filtered = df.filter(pl.col(split_col).cast(pl.Utf8).str.to_lowercase().str.contains(split_val.lower()))
    except Exception as exc:
        return {
            "ok": False,
            "error": f"ML_TABLE 조회 중 오류: {exc}",
        }

    if filtered.is_empty():
        return {
            "ok": False,
            "error": f"{product}의 {split_col} 열에서 '{split_val}' 조건으로 진행된 랏을 찾지 못했습니다.",
        }

    target_wafers_by_root: dict[str, set[str]] = {}
    lot_id_map: dict[str, str] = {}
    for row in filtered.iter_rows(named=True):
        root = str(row.get("ROOT_LOT_ID") or "").strip().upper()
        wf = str(row.get("WAFER_ID") or "").strip()
        lot = str(row.get("LOT_ID") or "").strip()
        if root:
            target_wafers_by_root.setdefault(root, set()).add(wf)
            if lot and root not in lot_id_map:
                lot_id_map[root] = lot

    matching_roots = set(target_wafers_by_root.keys())
    state = read_lot_progress_cache(allow_stale=True)
    all_items = state.get("items") or []

    # 현재 WIP 캐시에서 일치하는 root_lot_id / wafer_id 추출
    wip_items = []
    for item in all_items:
        if str(item.get("product") or "").strip().upper() != product.upper():
            continue
        root = str(item.get("root_lot_id") or "").strip().upper()
        if root in matching_roots:
            wf = str(item.get("wafer_id") or "").strip()
            if not target_wafers_by_root.get(root) or wf in target_wafers_by_root[root]:
                wip_items.append(dict(item))

    if not wip_items:
        return {
            "ok": True,
            "product": product,
            "split_col": split_col,
            "split_val": split_val,
            "total_matched_wafers": len(filtered),
            "total_matched_roots": len(matching_roots),
            "wip_count": 0,
            "message": f"{product}에서 {split_col}='{split_val}' 조건으로 배정된 {len(matching_roots)}개 랏({len(filtered)} wafer)은 현재 FAB WIP 캐시에 남아있지 않습니다 (공정 완료 out 되었거나 최신 캐시 미적재).",
            "table": {"rows": [], "columns": []},
        }

    # 각 항목별 step 정보 및 진행 점수 계산
    for it in wip_items:
        sid = str(it.get("step_id") or "")
        desc_info = describe_step(sid, product)
        it["step_desc"] = desc_info.get("step_desc", "")
        it["vehicle"] = desc_info.get("vehicle", "")
        it["func_step"] = str(it.get("function_step") or it.get("func_step") or "")
        it["score"] = score_step_progress(sid, it["step_desc"], str(it.get("update_time") or ""))

    # 점수 내림차순 정렬 (가장 선행하는 항목이 맨 앞)
    wip_items.sort(key=lambda x: x["score"], reverse=True)

    # 랏 단위로 집계
    lots_summary: dict[str, dict[str, Any]] = {}
    for it in wip_items:
        root = str(it.get("root_lot_id") or "").strip().upper()
        if root not in lots_summary:
            lots_summary[root] = {
                "root_lot_id": root,
                "lot_id": it.get("lot_id") or lot_id_map.get(root) or root,
                "step_id": it.get("step_id"),
                "step_desc": it.get("step_desc"),
                "func_step": it.get("func_step"),
                "vehicle": it.get("vehicle"),
                "wafers": [],
                "latest_move": str(it.get("update_time") or ""),
                "score": it["score"],
            }
        lots_summary[root]["wafers"].append(str(it.get("wafer_id") or ""))
        if str(it.get("update_time") or "") > lots_summary[root]["latest_move"]:
            lots_summary[root]["latest_move"] = str(it.get("update_time") or "")

    sorted_lots = sorted(lots_summary.values(), key=lambda x: x["score"], reverse=True)
    lead_lot = sorted_lots[0]
    lead_wafers = sorted(lead_lot["wafers"], key=lambda w: int(w) if w.isdigit() else 999)

    table_rows = []
    for rank, lot_info in enumerate(sorted_lots[:15], 1):
        step_str = step_label(lot_info["step_id"], lot_info["func_step"], lot_info["step_desc"])
        w_label = compress_wafer_ids(lot_info["wafers"])
        table_rows.append({
            "순위": f"{rank}위" + (" (선두랏)" if rank == 1 else ""),
            "Root Lot": lot_info["root_lot_id"],
            "FAB Lot": lot_info["lot_id"],
            "현재 Step": step_str,
            "Step ID": lot_info["step_id"],
            "공정명": lot_info["step_desc"] or lot_info["func_step"] or "-",
            "웨이퍼 수량": f"{len(lot_info['wafers'])}장",
            "Wafer 번호": w_label,
            "최종 이동 시각": lot_info["latest_move"] or "-",
        })

    step_display = step_label(lead_lot["step_id"], lead_lot["func_step"], lead_lot["step_desc"])
    lead_msg = (
        f"**{product}** 제품에서 **{split_col} = {split_val}** 조건으로 진행 중인 물량 중\n"
        f"가장 앞에 있는 **선두 랏은 `{lead_lot['root_lot_id']}` (`{lead_lot['lot_id']}`)** 입니다.\n\n"
        f"• **현재 위치**: `{step_display}`\n"
        f"• **진행 웨이퍼**: {len(lead_wafers)}장 ({compress_wafer_ids(lead_wafers)})\n"
        f"• **최종 이동 시각**: {lead_lot['latest_move'] or '-'}\n"
        f"• 해당 split 조건의 WIP 물량: 총 {len(sorted_lots)}개 랏, {len(wip_items)}장 웨이퍼"
    )

    return {
        "ok": True,
        "product": product,
        "split_col": split_col,
        "split_val": split_val,
        "lead_lot": lead_lot,
        "total_lots": len(sorted_lots),
        "total_wafers": len(wip_items),
        "message": lead_msg,
        "table": {
            "rows": table_rows,
            "columns": ["순위", "Root Lot", "FAB Lot", "현재 Step", "Step ID", "공정명", "웨이퍼 수량", "Wafer 번호", "최종 이동 시각"],
            "total": len(table_rows),
        },
    }


def get_split_distribution_chart(product: str, split_col: str) -> dict[str, Any]:
    """split 열의 점유율 분포를 Plotly 파이차트 및 데이터 테이블 형태로 생성."""
    counts = get_split_value_counts(product, split_col)
    if not counts:
        return {
            "ok": False,
            "error": f"{product}의 {split_col} 데이터를 찾을 수 없습니다.",
        }

    # 색상 팔레트
    palette = [
        "#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6",
        "#ec4899", "#06b6d4", "#84cc16", "#f97316", "#64748b",
        "#14b8a6", "#e11d48", "#6366f1", "#d97706", "#0ea5e9",
    ]
    pie_groups = []
    total_wafers = sum(c["count"] for c in counts)
    table_rows = []

    for idx, item in enumerate(counts):
        color = palette[idx % len(palette)]
        pie_groups.append({
            "label": item["value"],
            "value": item["count"],
            "count": item["count"],
            "percent": item["percent"],
            "color": color,
        })
        table_rows.append({
            "순위": f"{idx + 1}위",
            "Split 값": item["value"],
            "Wafer 수량": f"{item['count']:,}장",
            "점유율": f"{item['percent']}%",
        })

    chart = {
        "type": "pie",
        "chart_type": "pie",
        "title": f"{product} {split_col} 점유율 분포",
        "groups": pie_groups,
        "y_label": "WAFER",
        "hide_title": False,
    }

    summary_text = (
        f"**{product}** 의 **{split_col}** Split 점유율 분포입니다 (총 {total_wafers:,} wafer).\n"
        f"1위: **{counts[0]['value']}** ({counts[0]['count']:,}장, {counts[0]['percent']}%)"
        + (f", 2위: **{counts[1]['value']}** ({counts[1]['count']:,}장, {counts[1]['percent']}%)" if len(counts) > 1 else "")
    )

    return {
        "ok": True,
        "product": product,
        "split_col": split_col,
        "total_wafers": total_wafers,
        "chart_result": chart,
        "summary": summary_text,
        "table": {
            "rows": table_rows,
            "columns": ["순위", "Split 값", "Wafer 수량", "점유율"],
            "total": len(table_rows),
        },
    }


def handle_split_chat_query(prompt: str, context: dict | None = None, request=None) -> dict[str, Any] | None:
    context = context or {}
    text = (prompt or "").strip()
    folded = text.lower()

    # 1. 의도 감지 (선두랏/가장 앞선 랏 또는 Split 점유율/분포)
    is_lead_lot = bool(
        re.search(r"(?:선두\s*랏|선행\s*랏|가장\s*앞|제일\s*앞|앞선\s*랏|앞에\s*있는|선두랏|선행랏|선행하|선행한|가장\s*선행|선행중)", folded)
        or (re.search(r"(?:가장|제일|어떤|무슨|가장\s*먼저|앞|선행).*?(?:앞|선행)", folded) and any(w in folded for w in ("랏", "lot", "것", "거", "뭐야", "뭐")))
        or ("선두" in folded)
        or ("선행" in folded and any(w in folded for w in ("랏", "lot", "것", "거", "뭐야", "위치", "어디")))
    )

    is_split_dist = bool(
        (any(w in folded for w in ("분포", "점유율", "비중", "파이차트", "pie"))
         and any(w in folded for w in ("split", "스플릿", "knob", "mask", "ppid")))
        or re.search(r"split.*(?:분포|점유율|비중)", folded)
        or re.search(r"(?:분포|점유율).*split", folded)
    )

    if not is_lead_lot and not is_split_dist:
        return None

    from core import data_chat
    products = data_chat.available_product_names()
    matches = data_chat.product_candidates(text, products)
    product = matches[0] if len(matches) == 1 else str(context.get("product") or context.get("confirmed_product") or "")

    if not product:
        if context.get("product"):
            product = context["product"]
        elif products:
            return {
                "ok": False,
                "reply": f"조회할 제품명을 알려주세요 (예: {', '.join(products[:3])}).",
                "tool": {"missing": ["product"]},
                "context": context,
            }

    # 2. 선두 랏 조회 (Human-in-the-loop 지원)
    if is_lead_lot:
        found_col, found_val = find_column_for_value(product, text)
        split_col, cand_cols = resolve_split_column(product, text) if not found_col else (found_col, [found_col])
        split_val = found_val

        # Split 컬럼이 모호하거나 미지정인 경우
        if not split_col:
            if cand_cols:
                candidate_buttons = [
                    {"label": c, "prompt": f"{product} {c} 선두랏 조회"}
                    for c in cand_cols[:6]
                ]
                return {
                    "ok": True,
                    "reply": f"**{product}** 에서 어떤 Split 항목의 선두 랏을 조회할까요? 아래 후보 중 선택해 주세요.",
                    "tool": {
                        "feature": "splittable",
                        "split_candidates": [
                            {"title": f"{product} Split 항목 선택", "candidates": candidate_buttons}
                        ],
                        "sources": ["ML_TABLE", "WIP 캐시"],
                    },
                    "context": {**context, "product": product},
                }
            return {
                "ok": False,
                "reply": f"{product}의 Split 열을 확인하지 못했습니다. 정확한 KNOB 또는 Split 항목명을 알려주세요.",
                "tool": {"missing": ["split_col"]},
                "context": context,
            }

        # Split 값이 모호하거나 미지정인 경우 -> HITL 후보 제공
        if not split_val:
            split_val, cand_vals = resolve_split_value(product, split_col, text)

        if not split_val:
            candidate_buttons = [
                {
                    "label": f"{vc['value']} ({vc['count']:,}장)",
                    "prompt": f"{product} {split_col} {vc['value']} 선두랏 조회",
                    "value": vc["value"],
                }
                for vc in cand_vals[:6]
            ]
            return {
                "ok": True,
                "reply": f"**{product}** 의 **{split_col}** 에서 어떤 Split 조건의 선두 랏을 조회할까요? 아래 조건 중 선택해 주세요.",
                "tool": {
                    "feature": "splittable",
                    "split_candidates": [
                        {"title": f"{split_col} Split 조건 선택", "candidates": candidate_buttons}
                    ],
                    "sources": ["ML_TABLE", "WIP 캐시"],
                },
                "context": {**context, "product": product, "split_col": split_col},
            }

        # 컬럼과 값 모두 확정 -> 선두랏 산출
        result = find_split_leading_lot(product, split_col, split_val)
        if not result.get("ok"):
            return {
                "ok": False,
                "reply": result.get("error", "선두 랏을 조회하지 못했습니다."),
                "tool": {"feature": "splittable"},
                "context": context,
            }

        return {
            "ok": True,
            "reply": result["message"],
            "tool": {
                "feature": "splittable",
                "table": result["table"],
                "sources": [f"ML_TABLE_{product}", "WIP 최신 캐시"],
                "lead_lot": result.get("lead_lot"),
                "context": {
                    "product": product,
                    "split_col": split_col,
                    "split_val": split_val,
                },
            },
            "context": {
                **context,
                "product": product,
                "split_col": split_col,
                "split_val": split_val,
                "table": result["table"],
            },
        }

    # 3. Split 분포 / 점유율 파이차트 조회
    if is_split_dist:
        split_col, cand_cols = resolve_split_column(product, text)
        if not split_col:
            candidate_buttons = [
                {"label": f"{c} 분포", "prompt": f"{product} {c} 분포 보여줘"}
                for c in (cand_cols or get_product_split_columns(product))[:6]
            ]
            return {
                "ok": True,
                "reply": f"**{product}** 의 어떤 Split 분포를 볼까요? 아래 항목 중 선택해 주세요.",
                "tool": {
                    "feature": "dashboard",
                    "split_candidates": [
                        {"title": f"{product} Split 분포 선택", "candidates": candidate_buttons}
                    ],
                    "sources": ["ML_TABLE"],
                },
                "context": {**context, "product": product},
            }

        result = get_split_distribution_chart(product, split_col)
        if not result.get("ok"):
            return {
                "ok": False,
                "reply": result.get("error", "분포 데이터를 조회하지 못했습니다."),
                "tool": {"feature": "dashboard"},
                "context": context,
            }

        return {
            "ok": True,
            "reply": result["summary"],
            "tool": {
                "feature": "dashboard",
                "action": "dashboard.chart_data",
                "chart_result": result["chart_result"],
                "table": result["table"],
                "sources": [f"ML_TABLE_{product}"],
                "context": {
                    "product": product,
                    "split_col": split_col,
                },
            },
            "context": {
                **context,
                "product": product,
                "split_col": split_col,
                "chart_result": result["chart_result"],
                "table": result["table"],
            },
        }

    return None
