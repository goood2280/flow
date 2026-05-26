"""HTTP smoke for Home Flow-i chart regressions.

Runs against an already running server and first inspects local DB candidates.

Environment:
  FLOW_BASE=http://localhost:8080
  FLOW_USER=hol
  FLOW_PW=hol12345!
  FLOW_DB_ROOT=/optional/db/root
"""
from __future__ import annotations

import io
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    import polars as pl
except Exception:  # pragma: no cover - script readiness path
    pl = None  # type: ignore[assignment]

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
DB_ROOT = Path(os.environ.get("FLOW_DB_ROOT") or ROOT / "data" / "Fab")
BASE = os.environ.get("FLOW_BASE", "http://localhost:8080").rstrip("/")
USER = os.environ.get("FLOW_USER", "hol")
PW = os.environ.get("FLOW_PW", "hol12345!")

TOKEN = ""
PASS = 0
FAIL = 0


def _req(method: str, path: str, body: Any = None, token: str | None = None, timeout: int = 45) -> tuple[int, Any]:
    url = BASE + path
    data = None
    headers = {"Content-Type": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    if token:
        headers["X-Session-Token"] = token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if "json" in resp.headers.get("content-type", ""):
                return resp.status, json.loads(raw.decode("utf-8"))
            return resp.status, raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw) if raw.strip().startswith("{") else raw
        except Exception:
            return exc.code, raw
    except Exception as exc:
        return 0, f"{type(exc).__name__}: {exc}"


def _record(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"PASS {name}" + (f" - {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"FAIL {name}" + (f" - {detail}" if detail else ""))


def _scan_parquet(files: list[Path]):
    if pl is None or not files:
        return None
    try:
        return pl.scan_parquet([str(p) for p in files], missing_columns="insert", extra_columns="ignore")
    except TypeError:
        return pl.scan_parquet([str(p) for p in files])


def _et_files() -> list[Path]:
    files: list[Path] = []
    for root in DB_ROOT.rglob("*"):
        try:
            if root.is_dir() and "ET" in root.name.upper():
                files.extend(sorted(root.rglob("*.parquet")))
        except Exception:
            pass
    return files[:200]


def _ml_files() -> list[Path]:
    try:
        return sorted(DB_ROOT.glob("ML_TABLE_*.parquet"))[:80]
    except Exception:
        return []


def _first_existing(cols: list[str], *names: str) -> str:
    lower = {c.lower(): c for c in cols}
    for name in names:
        hit = lower.get(name.lower())
        if hit:
            return hit
    return ""


def _discover_context() -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "product": "PRODA",
        "step_id": "EA100030",
        "metric": "IOFF",
        "knob_prompt": "24.0 SORT KNOB",
        "exclude_value": "",
        "ready": False,
        "readiness": [],
    }
    if pl is None:
        ctx["readiness"].append("polars unavailable")
        return ctx
    et_files = _et_files()
    if not et_files:
        ctx["readiness"].append(f"ET parquet 없음 under {DB_ROOT}")
        return ctx
    lf = _scan_parquet(et_files)
    if lf is None:
        ctx["readiness"].append("ET scan 실패")
        return ctx
    cols = list(lf.collect_schema().names())
    product_col = _first_existing(cols, "product", "PRODUCT")
    step_col = _first_existing(cols, "step_id", "STEP_ID")
    item_col = _first_existing(cols, "item_id", "ITEM_ID", "rawitem_id", "RAWITEM_ID", "item", "ITEM")
    value_col = _first_existing(cols, "value", "VALUE")
    time_col = _first_existing(cols, "tkout_time", "TKOUT_TIME", "time", "TIME")
    if not (item_col and value_col and time_col):
        ctx["readiness"].append("ET 필수 컬럼 부족")
        return ctx
    product_from_path = next((p.parent.name for p in et_files if p.parent.name), ctx["product"])
    try:
        scoped = lf
        if step_col:
            scoped = scoped.filter(pl.col(step_col).cast(pl.String, strict=False).str.to_uppercase() == "EA100030")
        scoped = scoped.filter(pl.col(item_col).cast(pl.String, strict=False).str.to_uppercase().is_in(["IOFF", "LEAKAGE", "LKG"]))
        group_cols = ([product_col] if product_col else []) + [item_col] + ([step_col] if step_col else [])
        candidates = (
            scoped.group_by(group_cols)
            .agg(pl.len().alias("rows"))
            .sort("rows", descending=True)
            .limit(8)
            .collect()
            .to_dicts()
        )
    except Exception as exc:
        ctx["readiness"].append(f"ET 후보 탐색 실패: {exc}")
        candidates = []
    if candidates:
        top = candidates[0]
        ctx["product"] = str((top.get(product_col) if product_col else product_from_path) or ctx["product"])
        ctx["metric"] = str(top.get(item_col) or ctx["metric"])
        if step_col:
            ctx["step_id"] = str(top.get(step_col) or ctx["step_id"])
    else:
        try:
            products = (
                lf.select(pl.col(product_col).cast(pl.String, strict=False).drop_nulls().unique().alias("product")).limit(5).collect()["product"].to_list()
                if product_col else [product_from_path]
            )
            items = lf.select(pl.col(item_col).cast(pl.String, strict=False).drop_nulls().unique().alias("item")).limit(20).collect()["item"].to_list()
            if products:
                ctx["product"] = str(products[0])
            if items:
                ctx["metric"] = str(items[0])
            ctx["readiness"].append("EA100030/IOFF 후보 없음, 첫 후보로 readiness smoke 실행")
        except Exception:
            ctx["readiness"].append("ET 후보 없음")
            return ctx
    for fp in _ml_files():
        if ctx["product"].upper() not in fp.stem.upper():
            continue
        try:
            ml_lf = _scan_parquet([fp])
            ml_cols = list(ml_lf.collect_schema().names()) if ml_lf is not None else []
            knob_cols = [c for c in ml_cols if c.upper().startswith("KNOB_")]
            if knob_cols:
                knob = next((c for c in knob_cols if "SORT" in c.upper()), knob_cols[0])
                ctx["knob_prompt"] = knob.replace("KNOB_", "") + " KNOB"
                vals = (
                    ml_lf.select(pl.col(knob).cast(pl.String, strict=False).drop_nulls().unique().alias("v"))
                    .limit(8)
                    .collect()["v"]
                    .to_list()
                )
                if vals:
                    ctx["exclude_value"] = str(vals[-1])
                break
        except Exception:
            continue
    ctx["ready"] = True
    return ctx


def _flowi(prompt: str, context: dict[str, Any] | None = None) -> tuple[int, Any]:
    body = {"prompt": prompt, "product": "", "max_rows": 12, "context": context or {"type": "flowi_chart_scenario"}}
    return _req("POST", "/api/llm/flowi/chat", body=body, token=TOKEN, timeout=60)


def _assert_chart(body: Any, source_type: str = "", color: bool = False) -> tuple[bool, str]:
    tool = body.get("tool") if isinstance(body, dict) else {}
    chart = tool.get("chart_result") if isinstance(tool, dict) else {}
    if not isinstance(chart, dict):
        return False, "chart_result missing"
    if chart.get("chart_type") != "scatter":
        return False, f"chart_type={chart.get('chart_type')}"
    if source_type and chart.get("source_type") != source_type:
        return False, f"source_type={chart.get('source_type')}"
    if chart.get("x_col") != "tkout_time":
        return False, f"x_col={chart.get('x_col')}"
    if int(chart.get("total") or 0) < 1:
        return False, "no points"
    if color and not chart.get("color_by"):
        return False, "color_by missing"
    return True, f"points={chart.get('total')} session={tool.get('chart_session_id') or ''}"


def main() -> int:
    global TOKEN
    ctx = _discover_context()
    print("Flow-i chart scenario context:", json.dumps(ctx, ensure_ascii=False))
    if not ctx.get("ready"):
        _record("real DB readiness", False, "; ".join(ctx.get("readiness") or ["unknown"]))
        return 1
    status, body = _req("POST", "/api/auth/login", {"username": USER, "password": PW}, timeout=20)
    TOKEN = body.get("token") if isinstance(body, dict) else ""
    _record("login", status == 200 and bool(TOKEN), f"status={status}")
    if not TOKEN:
        return 1

    prompt1 = f"{ctx['product']} {ctx['step_id']} {ctx['metric']} Trend 그려줘"
    status, body = _flowi(prompt1)
    ok, detail = _assert_chart(body, "ET")
    _record("ET trend single metric", status == 200 and ok, detail)
    session_id = ((body.get("tool") or {}).get("chart_session_id") if isinstance(body, dict) else "") or ""

    prompt2 = f"{ctx['product']} {ctx['metric']} Trend 그려줘"
    status, body2 = _flowi(prompt2)
    ok, detail = _assert_chart(body2, "ET")
    _record("ET product trend alias", status == 200 and ok, detail)

    follow_context = {"type": "flowi_chart_scenario", "chart_session_id": session_id, "messages": [{"chart_session_id": session_id}]}
    status, body3 = _flowi(f"{ctx['knob_prompt']}으로 컬러링해줘", follow_context)
    ok, detail = _assert_chart(body3, "ET", color=True)
    _record("ET trend + ML_TABLE KNOB color", status == 200 and ok, detail)

    session2 = ((body3.get("tool") or {}).get("chart_session_id") if isinstance(body3, dict) else "") or session_id
    if ctx.get("exclude_value"):
        follow_context2 = {"type": "flowi_chart_scenario", "chart_session_id": session2, "messages": [{"chart_session_id": session2}]}
        status, body4 = _flowi(f"{ctx['exclude_value']} 제외하고 보여줘", follow_context2)
        chart = ((body4.get("tool") or {}).get("chart_result") or {}) if isinstance(body4, dict) else {}
        excluded = (chart.get("filters") or {}).get("excluded_values") or []
        _record("ET trend color exclude value", status == 200 and ctx["exclude_value"] in excluded, f"excluded={excluded}")
    else:
        _record("ET trend color exclude value", False, "사용 가능한 KNOB value 없음")

    prompts = [
        (f"{ctx['product']} INLINE CD Trend 그려줘", "INLINE trend readiness"),
        (f"{ctx['product']} Inline CD와 ET {ctx['metric']} Corr scatter 그려줘", "INLINE/ET scatter readiness"),
        (f"{ctx['product']} ET와 ML_TABLE 조인해서 scatter 차트 그려줘", "confirmed relation multi-source readiness"),
    ]
    for prompt, name in prompts:
        status, bodyx = _flowi(prompt)
        handled = bool((bodyx.get("tool") or {}).get("handled")) if isinstance(bodyx, dict) else False
        _record(name, status == 200 and handled, (bodyx.get("answer") or "")[:120] if isinstance(bodyx, dict) else str(bodyx)[:120])

    total = PASS + FAIL
    print(f"RESULT {PASS}/{total} PASS")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
