"""INLINE spatial measurements via current lot membership and shared ChartBuilder."""
from collections import defaultdict
from copy import deepcopy
import math
import re
import statistics

from fastapi import HTTPException
from core import auth, inline_coordinates, teg_map
from core.chart_builder_definition import format_chart_builder_definition
from core.data_chat_inline_chart import _measures, _measure_options, _site_options, _q, _lit
from core.paths import PATHS

MAP = re.compile(r"wafer\s*map|wf\s*map|웨이퍼\s*(?:맵|지도)|와퍼\s*맵", re.I)
LOT = re.compile(r"(?<![\w.])[A-Za-z][A-Za-z0-9]{3,}\.[A-Za-z0-9]+(?![\w.])")


def _reply(message, state, query, **tool):
    from core.data_chat import reply
    return reply(message, context=state, ok=not bool(tool.get("missing") or tool.get("error")),
                 tool={"feature": "chart", "action": "inline.wafer_map", "query_scope": query, **tool})


def _ask(state, query, kind, options, title):
    state["pending_wafer_map"] = {"query": query, "kind": kind, "options": options}
    return _reply(title, state, query, missing=[kind], clarification={"kind": kind, "title": title,
        "options": options, "allow_other": kind == "lot"})


def percentile(values, fraction):
    values = sorted(values)
    pos = (len(values) - 1) * fraction
    lo, hi = math.floor(pos), math.ceil(pos)
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


def dispatch(text, context, request=None):
    pending = context.get("pending_wafer_map") or {}
    fresh = bool(MAP.search(text))
    if not (fresh or pending):
        return None
    if fresh and re.search(r"\b(?:ET|VM|IM)\b|가상계측|수율|yield", text, re.I):
        return None
    if pending and not fresh and re.search(r"보여|조회|그려|컬러|추출", text):
        context.pop("pending_wafer_map", None)
        return None
    state = deepcopy(context)
    if re.fullmatch(r"취소(?:해|해줘)?[.!\s]*", text):
        state.pop("pending_wafer_map", None)
        return _reply("Wafer map 조건 선택을 취소했습니다.", state, {})
    product = state.get("confirmed_product")
    if not product:
        from core.data_chat import _product_question, available_product_names
        return _product_question(text, state, available_product_names())
    user = auth.current_user(request)
    tabs = auth.effective_permissions(user).get("tabs") or []
    if user.get("role") != "admin" and tabs != "*" and "*" not in tabs and "chartbuilder" not in tabs:
        return _reply("차트생성 권한이 필요합니다.", state, {}, error="permission_denied", blocked=True)
    query = {"product": product, "prompt": text} if fresh else deepcopy(pending["query"])
    if query.get("product") != product:
        state.pop("pending_wafer_map", None)
        return _reply("제품이 변경되었습니다. Lot과 측정 항목을 다시 요청해 주세요.", state, query, error="product_changed")
    if pending and not fresh:
        options = pending["options"]
        ordinal = re.fullmatch(r"\s*(\d+)\s*번?\s*", text)
        choice = options[int(ordinal[1])-1] if ordinal and 0 < int(ordinal[1]) <= len(options) else next(
            (o for o in options if text.strip().casefold() in {o["value"].casefold(), o["label"].casefold()}), None)
        if pending["kind"] == "lot" and LOT.fullmatch(text.strip()):
            choice = {"value": text.strip().upper(), "label": text.strip().upper()}
        if not choice:
            return _ask(state, query, pending["kind"], options, "표시된 후보를 선택해 주세요.")
        query[pending["kind"]] = choice
        state.pop("pending_wafer_map", None)
    from routers import filebrowser as fb, lot_progress
    try:
        measures = _measures(product, query["prompt"])
        selected = query.get("measurement", {}).get("measure")
        if selected and not any((m["step_id"], m["item_id"]) == (selected["step_id"], selected["item_id"]) for m in measures):
            raise ValueError("Semantic 연결이 변경되었습니다. 측정 항목을 다시 확인해 주세요.")
        if not selected:
            if not measures:
                raise ValueError("제품 Semantic에서 INLINE 측정 항목의 step_id·item_id를 찾지 못했습니다.")
            if len(measures) > 1:
                return _ask(state, query, "measurement", _measure_options(measures), "Wafer map에 표시할 측정 항목을 선택해 주세요.")
            selected = measures[0]
            query["measurement"] = _measure_options(measures)[0]
        lots = list(dict.fromkeys(v.upper() for v in LOT.findall(query["prompt"])))
        lot = query.get("lot", {}).get("value") or (lots[0] if len(lots) == 1 else "")
        if not lot:
            return _ask(state, query, "lot", [{"label": v, "value": v} for v in lots], "현재 소속 wafer를 확인할 Lot ID(예: AZBBB.1)를 입력해 주세요.")
        inventory = lot_progress.wafers(request, product=product, lot_id=lot, root_lot_id="", limit=200)
        if inventory.get("truncated"):
            raise ValueError("Lot의 wafer가 200개를 넘습니다. Lot 범위를 좁혀 주세요.")
        pairs = [{"root_lot_id": str(r["root_lot_id"]), "wafer_id": str(r["wafer_id"])} for r in inventory.get("items", [])
                 if r.get("root_lot_id") and str(r.get("wafer_id") or "")]
        if not pairs:
            raise ValueError("랏 현위치에서 해당 제품·Lot의 Root Lot / Wafer를 찾지 못했습니다. 형제 Lot 전체로 넓혀 조회하지 않았습니다.")
        query.update(lot_id=lot, step_id=selected["step_id"], item_id=selected["item_id"], wafer_count=len(pairs))
        rules = [r for r in inline_coordinates.load_matching_rules(PATHS.base_root, products=[product])
                 if str(r.get("step_id", "")).casefold() == selected["step_id"].casefold()
                 and str(r.get("item_id", "")).casefold() == selected["item_id"].casefold()]
        maps = {(str(r.get("matching_table") or ""), str(r.get("vehicle") or "")) for r in rules}
        if len(maps) != 1:
            raise ValueError("Inline_shot_matching의 제품·step_id·item_id에 유일한 map_name 연결이 필요합니다. Inline map setting 연결을 확인해 주세요.")
        map_name, vehicle = next(iter(maps))
        if not vehicle or not all(r.get("available") for r in rules):
            raise ValueError("연결된 Inline map setting 또는 제품 geometry가 없습니다.")
        geometry = teg_map.map_payload(vehicle)
        allowed = {(round(float(s["x"]), 6), round(float(s["y"]), 6)) for s in geometry.get("shots", [])}
        if not allowed or not geometry.get("geometry"):
            raise ValueError("TEG 위치조회에 제품별 wafer 외곽선과 shot 좌표가 없습니다.")
        root = fb._chart_builder_resolve_root_name("INLINE")
        files = fb.source_data_files(root=root, product=product)
        if not files:
            raise ValueError("제품의 원본 INLINE DB가 없습니다.")
        columns, _ = fb.duckdb_engine.inspect_files(files)
        names = {c.casefold(): c for c in columns}
        keys = ["root_lot_id", "wafer_id", "step_id", "item_id", "subitem_id"]
        if any(k not in names for k in keys):
            raise ValueError("원본 INLINE에 Root Lot·Wafer·step_id·item_id·subitem_id가 필요합니다.")
        value = next((names[c.casefold()] for c in [selected.get("value_column") or "", "value", "fab_value"] if c.casefold() in names), "")
        if not value:
            raise ValueError("측정값 열을 확인하지 못했습니다.")
        where = f"{_q(names['step_id'])} = {_lit(selected['step_id'])} AND {_q(names['item_id'])} = {_lit(selected['item_id'])}"
        sites = _site_options(fb, files, columns, where)
        if not sites:
            raise ValueError("SITE/SHOT 행을 구분할 실제 열과 값이 없습니다.")
        site = sites[0]
        where += f" AND {_q(site['column'])} IN ({', '.join(_lit(v) for v in site['values'])})"
        projected = list(dict.fromkeys([names[k] for k in keys] + [value, site["column"]] + ([names["tkout_time"]] if "tkout_time" in names else [])))
        source = {"id": "inline", "root": root, "product": product, "sql": f"SELECT {', '.join(_q(c) for c in projected)} WHERE {where}",
                  "runtime_lot_wafer_pairs": pairs}
        agg = query.get("aggregation", {}).get("value", "avg")
        chart = {"type": "wafer_map", "title": f"{product} {lot} {selected.get('term') or selected['item_id']} Wafer map",
                 "x": "shot_x", "map_y": "shot_y", "y": value, "map_scope": "trellis_root_wafer", "aggregation": agg,
                 "wafer_palette": "blue_gray_red", "wafer_mode": "value"}
        req = fb.ChartBuilderRunReq(sources=[source], max_rows=10000, chart=chart, chart_name=chart["title"], save_history=False)
        result = fb.chart_builder_run(req, request)
        if any(s.get("truncated") for s in result.get("sources", [])):
            raise ValueError("SITE 데이터가 차트 한도 10,000행을 넘습니다. 불완전한 wafer map을 만들지 않았습니다. 조회 범위를 줄여 주세요.")
        raw = result.get("joined", {}).get("rows") or []
        rows = []
        for r in raw:
            try:
                coord = (round(float(r["shot_x"]), 6), round(float(r["shot_y"]), 6))
                numeric = float(r[value])
            except (ValueError, TypeError, KeyError):
                continue
            if coord in allowed and math.isfinite(numeric) and str(r.get("inline_map_name", "")).casefold() == map_name.casefold():
                rows.append(r)
        if not rows:
            raise ValueError("subitem_id를 Inline map setting에 연결한 뒤 제품 geometry에 남는 유효한 측정점이 없습니다.")
        grouped = defaultdict(list)
        for row in rows:
            grouped[(str(row[names["root_lot_id"]]), str(row[names["wafer_id"]]), float(row["shot_x"]), float(row["shot_y"]))].append(float(row[value]))
        if any(len(v) > 1 for v in grouped.values()) and "aggregation" not in query:
            return _ask(state, query, "aggregation", [{"label": "평균", "value": "avg"}, {"label": "중앙값", "value": "median"}],
                        "같은 wafer·shot의 측정값이 여러 개입니다. 지도에 표시할 집계 방법을 선택해 주세요.")
        panels = {}
        for (root_lot, wafer, x, y), values in sorted(grouped.items()):
            key = root_lot + "|" + wafer
            panel = panels.setdefault(key, {"key": key, "label": f"{root_lot} · W{wafer}", "points": []})
            panel["points"].append({"x": x, "y": y, "value": statistics.median(values) if agg == "median" else statistics.mean(values), "n": len(values)})
        values = [float(r[value]) for r in rows]
        scale = {"wafer_low": percentile(values, .1), "wafer_center": percentile(values, .5), "wafer_high": percentile(values, .9)}
        chart.update(scale)
        req = req.model_copy(update={"chart": chart, "save_history": True})
        saved_result = fb.chart_builder_run(req, request)
        saved = saved_result.get("saved_chart") or {}
        query["history_id"] = saved.get("id", "")
        state.pop("pending_wafer_map", None)
        state.update(wafer_map_query=query, last_action="inline.wafer_map")
        excluded = len(raw) - len(rows) + sum(int(s.get("inline_coordinate_mapping", {}).get("unmatched_rows") or 0) for s in result.get("sources", []))
        summary = f"{product} {lot}의 현재 Root Lot·Wafer {len(pairs)}개를 확인한 뒤, INLINE {selected['step_id']} / {selected['item_id']}의 SITE/SHOT만 조회했습니다. {map_name}에서 subitem_id를 좌표로 LEFT JOIN하고 TEG 제품 geometry에 존재하는 shot만 wafer별로 표시합니다."
        return _reply(f"{len(panels)}개 wafer 지도에 {len(rows)}개 측정값을 표시했습니다. 좌표 미매칭·제품 밖·유효하지 않은 값 {excluded}개를 제외했습니다.", state, query,
            sources=["/api/lot-progress/wafers", root + "/" + product, "inline_shot_matching.csv", "Inline map setting", "TEG 위치조회", "/api/filebrowser/chart-builder/run"],
            table={"columns": list(rows[0]), "rows": rows, "total": len(rows)}, saved_chart=saved,
            definition_code=format_chart_builder_definition(sources=[source], joins=[], max_rows=10000, chart=chart),
            chart_result={**chart, "chart_type": "wafer_map", "product": vehicle, "panels": list(panels.values()), "points": [],
                          "wafer_geometry": geometry, "scale_values": values, "y_label": value},
            warnings=result.get("warnings") or [],
            interpretation={"summary": summary, "origin": "현위치 · Semantic · Inline map setting · TEG geometry", "status": "completed",
                "details": [{"label": "대상", "value": f"{lot} · 현재 wafer {len(pairs)}개"}, {"label": "행 필터", "value": site["label"]},
                            {"label": "좌표", "value": f"{map_name} · subitem_id → shot_x / shot_y"},
                            {"label": "공통 색상", "value": f"P10 {scale['wafer_low']:.5g} 파랑 · Median {scale['wafer_center']:.5g} 회색 · P90 {scale['wafer_high']:.5g} 빨강"}], "unresolved": []})
    except (HTTPException, ValueError, KeyError) as exc:
        state.pop("pending_wafer_map", None)
        return _reply(str(exc.detail) if isinstance(exc, HTTPException) else str(exc), state, query, error="wafer_map_failed")
