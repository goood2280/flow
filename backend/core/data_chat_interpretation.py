"""User-facing account of the resolved plan, not model reasoning or guessed intent."""

LABELS = {
    "splittable": "SplitTable 조회", "location": "Lot 위치 조회", "chart": "차트 조회·수정",
    "et_time.measure": "ET 측정시간 조회", "et_time.trend": "ET 측정시간 추이 조회",
    "reformatize.download.start": "ET 데이터 파일 생성", "dashboard.summary": "대시보드 지표 요약",
    "dashboard.stuck_lots": "정체 Lot 조회", "inline.shot_trend": "원본 Inline 샷별 추이 조회",
    "inline.trend": "Inline 평균값 추이 조회", "inline.values": "Inline 측정값 조회",
    "por.current": "공정별 전산 POR·PPID 카테고리 조회", "ml_table.trend": "ML_TABLE 측정값·시간축·색상 열 선택 및 산점도 생성",
    "et.index_trend": "ET 항목 추출·추이 또는 Inline 상관분석", "inline.wafer_map": "현재 Lot의 Wafer별 Inline map 조회",
    "vm.trend": "VM 가상계측 저장값 추이 조회", "vm.values": "VM 가상계측 저장값 조회",
}
FIELDS = {"product": "제품", "root_lot_id": "Root Lot", "lot_id": "Lot", "step_id": "공정", "item_id": "측정 항목",
          "column": "측정 열", "tkout_time": "시간축", "months": "기간(개월)", "days": "기간(일)", "item": "추출 항목",
          "ml_measurement": "ML 측정 열", "ml_time": "시간축", "ml_color": "장비 원본 열", "color_mode": "장비 계층",
          "et_aggregation": "ET 집계", "mode": "분석 종류", "family": "데이터 종류", "aggregation": "집계", "split_col": "선택한 KNOB"}


def describe(question, result):
    tool = result.get("tool") or {}
    if isinstance(tool.get("interpretation"), dict):
        return tool["interpretation"]
    route = result.get("routing_trace") or {}
    status = route.get("status", "needs_input")
    action = tool.get("action") or tool.get("feature") or route.get("action") or ""
    state = result.get("context") or {}
    scope = {**{k: state[k] for k in ("product", "root_lot_id", "lot_id") if state.get(k)},
             **(tool.get("query_scope") or {}), **(tool.get("slots") or {}), **(tool.get("context") or {})}
    details = [{"label": label, "value": str(scope[key])} for key, label in FIELDS.items() if scope.get(key) not in (None, "", [], {})]
    missing = [str(v) for v in tool.get("missing") or []]
    label = LABELS.get(action, action.replace("_", " ") if action else "데이터 요청")
    product = str(scope.get("product") or "")
    summary = f"{product + '의 ' if product else ''}{label} 요청으로 이해했습니다."
    if status == "needs_input":
        summary += " 아직 조건을 확정하지 않았으며, 아래 선택 또는 추가 입력이 필요합니다."
    elif status == "failed":
        summary += " 요청을 처리하는 중 조건 또는 데이터 문제를 확인해 실행을 완료하지 못했습니다."
    else:
        summary += " 아래에 실제 사용한 대상과 조회 경로를 표시합니다."
    query = tool.get("query_scope") or {}
    if query.get("family") == "INLINE":
        summary += " ML_TABLE에 정제된 INLINE 평균값을 사용합니다. 개별 SITE/SHOT 원본은 INLINE DB에서 별도로 조회합니다."
    elif query.get("family") == "VM":
        summary += " VM·IM·가상계측으로 해석하여 ML_TABLE의 VM 저장값을 사용합니다."
    if action == "inline.shot_trend":
        summary = f"{product}의 원본 INLINE에서 측정 항목을 Semantic으로 찾고, SITE/SHOT 행만 tkout_time 기준 산점도로 보는 요청으로 이해했습니다."
        if query.get("measurement"):
            m = query["measurement"].get("measure", {})
            details.append({"label": "Semantic 연결", "value": f"{m.get('step_id', '')} / {m.get('item_id', '')}"})
        if "split_column" in missing:
            summary += " 현재 차트에 ML_TABLE의 Split을 Root Lot·Wafer 기준으로 왼쪽 결합하여 색상을 나누려 하지만, Split 열을 아직 확정하지 못했습니다."
        elif missing:
            summary += " 실제 후보 중 사용할 조건을 확인해야 합니다."
        elif status == "failed":
            summary += " 필요한 데이터 조건을 확정하지 못해 실행을 완료하지 않았습니다."
    title = (tool.get("clarification") or {}).get("title")
    unresolved = [title] if title else [FIELDS.get(v, v) + " 확인 필요" for v in missing]
    return {"summary": summary, "origin": "실행 계획과 검증된 조회 조건의 요약", "status": status,
            "details": details, "unresolved": unresolved}
