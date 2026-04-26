"""scripts/seed_power_dashboard.py — 파워유저용 데모 대시보드 차트 시드.

v8.4.8: 5개 그룹 (KNOB 영향 / INLINE 분포 / VM vs 실측 / FAB 장비 / 종합)
        × 다양한 차트 타입 (scatter / line / bar / pie / box / histogram /
        pareto / heatmap / treemap / cross_table / table).
        모든 차트의 selection_key="LOT_WF" 로 설정해 차트 간 마킹·하이라이트 연동.
        width/height 로 그리드 스팬 구성 — 가장 중요한 종합 차트는 L/XL.

사용:
  python scripts/seed_power_dashboard.py                    # 기본: localhost:8080, hol 계정
  python scripts/seed_power_dashboard.py --host http://127.0.0.1:8091 --username hol --password hol12345!
  python scripts/seed_power_dashboard.py --wipe             # 기존 차트 전부 삭제 후 시드
"""
import argparse
import json
import sys
import urllib.request
import urllib.parse

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ML_SRC = {"source_type": "base_file", "root": "", "product": "", "file": "ML_TABLE_PRODA.parquet"}
ET_SRC = {"source_type": "", "root": "1.RAWDATA_DB_ET", "product": "PRODUCT_A0", "file": ""}


def mk(title, *, chart_type, x_col="", y_expr="", color_col="", agg_col="", agg_method="",
       group="", width=1, height=1, filter_expr="", visible_to="all", sort_x=False,
       selection_key="LOT_WF", source=None, extra=None):
    c = {
        "title": title, "chart_type": chart_type,
        **(source or ML_SRC),
        "x_col": x_col, "y_expr": y_expr, "color_col": color_col,
        "agg_col": agg_col, "agg_method": agg_method,
        "group": group, "width": width, "height": height,
        "filter_expr": filter_expr, "visible_to": visible_to, "sort_x": sort_x,
        "selection_key": selection_key,
        "exclude_null": True,
    }
    if extra:
        c.update(extra)
    return c


# 차트 타입별 1개 이상 포함 — scatter/line/bar/area/combo/pie/donut/binning/
# pareto/box/treemap/heatmap/wafer_map/table/cross_table.
CHARTS = [
    mk("제품별 로트 수", chart_type="pie",
       x_col="product", agg_col="wafer_id", agg_method="count",
       group="종합", width=2, height=1),
    mk("제품별 wafer 비중", chart_type="donut",
       x_col="product", agg_col="wafer_id", agg_method="count",
       group="종합", width=2, height=1),
    mk("KNOB_ETCH_PPID 별 wafer 수", chart_type="bar",
       x_col="KNOB_ETCH_PPID", agg_col="wafer_id", agg_method="count",
       group="KNOB 영향", width=2, height=1),
    mk("KNOB_CVD_PPID Pareto", chart_type="pareto",
       x_col="KNOB_CVD_PPID", agg_col="wafer_id", agg_method="count",
       group="KNOB 영향", width=2, height=1, sort_x=True),
    mk("KNOB_GATE_PPID 별 INLINE_CD_GATE_MEAN 박스플롯",
       chart_type="box", x_col="KNOB_GATE_PPID", y_expr="INLINE_CD_GATE_MEAN",
       group="KNOB 영향", width=2, height=1),
    mk("KNOB_ETCH_PPID vs Predicted Vth_N",
       chart_type="scatter", x_col="KNOB_ETCH_PPID", y_expr="VM_PREDICTED_VTH_N",
       color_col="KNOB_GATE_PPID",
       group="KNOB 영향", width=2, height=1),
    mk("INLINE_CD_GATE_MEAN 히스토그램",
       chart_type="binning", x_col="INLINE_CD_GATE_MEAN",
       group="INLINE 분포", width=2, height=1,
       extra={"bin_count": 30}),
    mk("INLINE_OVL_X vs INLINE_OVL_Y",
       chart_type="scatter", x_col="INLINE_OVL_X", y_expr="INLINE_OVL_Y",
       color_col="product",
       group="INLINE 분포", width=2, height=1),
    mk("VM_PREDICTED_VTH_N 추이", chart_type="line",
       x_col="root_lot_id", y_expr="VM_PREDICTED_VTH_N",
       color_col="product", group="VM vs 실측", width=2, height=1, sort_x=True),
    mk("INLINE_CD_GATE_MEAN 면적 추이", chart_type="area",
       x_col="root_lot_id", y_expr="INLINE_CD_GATE_MEAN",
       color_col="product", group="INLINE 분포", width=2, height=1, sort_x=True),
    mk("Vth_N bar + INLINE_CD line", chart_type="combo",
       x_col="root_lot_id", y_expr="VM_PREDICTED_VTH_N",
       agg_col="INLINE_CD_GATE_MEAN", group="VM vs 실측", width=2, height=1, sort_x=True),
    mk("VM_PREDICTED_VTH_N vs INLINE_CD_GATE_MEAN",
       chart_type="scatter", x_col="VM_PREDICTED_VTH_N", y_expr="INLINE_CD_GATE_MEAN",
       color_col="FAB_EQP_GATE",
       group="VM vs 실측", width=3, height=1),
    mk("FAB_EQP_ETCH wafer 수 Treemap",
       chart_type="treemap", x_col="FAB_EQP_ETCH",
       agg_col="wafer_id", agg_method="count",
       group="FAB 장비", width=2, height=1),
    mk("FAB_EQP_GATE × FAB_EQP_ETCH",
       chart_type="cross_table", x_col="FAB_EQP_GATE", y_expr="FAB_EQP_ETCH",
       agg_col="VM_PREDICTED_VTH_N", agg_method="mean",
       group="FAB 장비", width=2, height=1,
       extra={"cross_val_col": "VM_PREDICTED_VTH_N", "cross_method": "mean"}),
    mk("root_lot_id × wafer_id heatmap", chart_type="heatmap",
       x_col="root_lot_id", y_expr="wafer_id",
       agg_col="VM_PREDICTED_VTH_N", agg_method="mean",
       group="종합", width=2, height=1, sort_x=True),
    mk("Wafer Map · ET value by WF Layout", chart_type="wafer_map",
       x_col="shot_x", y_expr="shot_y", color_col="value", agg_col="value", agg_method="mean",
       filter_expr="item_id == 'PC_ALIGN'", source=ET_SRC,
       selection_key="root_lot_id", group="Wafer Map", width=2, height=2,
       extra={"layout_product": "PRODUCT_A0"}),
    mk("상세 테이블 — root_lot / wafer / KNOB / INLINE / VM",
       chart_type="table", x_col="root_lot_id",
       group="종합", width=4, height=1,
       extra={"table_columns": [
           "root_lot_id", "wafer_id", "product",
           "KNOB_GATE_PPID", "KNOB_ETCH_PPID",
           "INLINE_CD_GATE_MEAN", "INLINE_OVL_X",
           "VM_PREDICTED_VTH_N", "VM_CHAMBER_DRIFT_GATE",
           "FAB_EQP_GATE", "FAB_EQP_ETCH",
       ]}),
]


def http(method, url, token=None, body=None):
    data = None
    headers = {"content-type": "application/json"}
    if token:
        headers["X-Session-Token"] = token
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        try:
            return json.loads(raw)
        except Exception:
            return {"_raw": raw.decode("utf-8", "replace")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="http://127.0.0.1:8080")
    ap.add_argument("--username", default="hol")
    ap.add_argument("--password", default="hol12345!")
    ap.add_argument("--wipe", action="store_true")
    args = ap.parse_args()

    # login
    r = http("POST", f"{args.host}/api/auth/login",
             body={"username": args.username, "password": args.password})
    token = r.get("token")
    if not token:
        raise SystemExit(f"login failed: {r}")

    if args.wipe:
        cur = http("GET", f"{args.host}/api/dashboard/charts", token=token)
        for c in cur.get("charts", []):
            http("POST", f"{args.host}/api/dashboard/charts/delete?chart_id={c['id']}", token=token)
        print(f"wiped {len(cur.get('charts', []))} charts")

    for cfg in CHARTS:
        r = http("POST", f"{args.host}/api/dashboard/charts/save", token=token, body=cfg)
        print(f"saved: {cfg['title']}  →  id={r.get('id') or r.get('chart_id') or '?'}")

    # trigger refresh
    http("POST", f"{args.host}/api/dashboard/refresh", token=token)
    print("\nrefresh triggered — 열어서 확인:", args.host + "/  (dashboard 탭)")


if __name__ == "__main__":
    main()
