"""routers/dashboard.py v6.0.0 — Background chart scheduler + lazy reads + snapshots + SPC
Designed for 2-core / 6GB with 30-50GB parquet datasets.
Charts are pre-computed every 10 min by a daemon thread; frontend fetches snapshots.
v6: spec lines (USL/LSL/Target), SPC control limits (UCL/LCL/CL), OOS alerts.
"""
import datetime, threading, time, logging, statistics
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from typing import Optional
import polars as pl
from core.paths import PATHS
from core.utils import (
    _STR, cast_cats, read_source, read_one_file, find_all_sources,
    apply_time_window, lazy_read_source,
    load_json, save_json, serialize_rows,
)

logger = logging.getLogger("holweb.dashboard")
router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
# NOTE: DB_BASE 는 삭제됨. admin 이 런타임에 db_root 를 변경해도 반영되도록
# 각 함수 내부에서 PATHS.db_root 를 직접 참조(lazy resolve).
CHARTS_FILE = PATHS.data_root / "dashboard_charts.json"
SNAP_FILE = PATHS.data_root / "dashboard_snapshots.json"

MAX_POINTS = 5000


# ──────────────────────────────────────────────────────────────────
# Chart config model
# ──────────────────────────────────────────────────────────────────
class ChartConfig(BaseModel):
    id: str = ""
    title: str = ""
    source_type: str = ""
    root: str = ""
    product: str = ""
    file: str = ""
    x_col: str = ""
    y_expr: str = ""
    time_col: str = ""
    days: Optional[int] = None
    chart_type: str = "scatter"  # scatter/line/bar/pie/binning/box/area/pareto/donut/treemap/wafer_map/combo/heatmap
    filter_expr: str = ""
    agg_col: str = ""
    agg_method: str = ""  # mean/sum/count/min/max
    color_col: str = ""
    x_label: str = ""
    y_label: str = ""
    bin_count: Optional[int] = None
    bin_width: Optional[float] = None
    visible_to: str = "all"  # all / admin
    no_schedule: bool = False  # True = skip background refresh
    exclude_null: bool = True  # v8.1.5: filter null/"(null)"/empty from x_col and y_expr before plotting
    point_size: Optional[int] = None
    opacity: Optional[float] = None
    sort_x: bool = False
    limit_points: Optional[int] = None
    # v7.2: Cross-chart marking key — usually LOT_WF (wafer) or ROOT_LOT_ID (lot).
    # When set, each point carries this column's value; the frontend "global selection" state
    # highlights matching points across all charts sharing the same selection_key.
    selection_key: str = "LOT_WF"
    # v6: Spec lines (legacy single USL/LSL/Target)
    usl: Optional[float] = None       # Upper Specification Limit
    lsl: Optional[float] = None       # Lower Specification Limit
    target: Optional[float] = None    # Target / Center value
    # v7: Spec lines (multi). Each: {name, value, color, style: "solid"|"dashed", kind: "usl"|"lsl"|"target"|"custom"}
    spec_lines: list = []
    # v6: SPC
    enable_spc: bool = False          # Enable Statistical Process Control lines
    # v8.1.1: LEFT JOIN additional sources into main dataframe before chart compute.
    # Each join: {source_type, root, product, file, left_on:[...], right_on:[...], suffix:"_j1"}
    # Applied sequentially; missing right-side columns that collide with left are suffixed.
    joins: list = []
    # v8.4.8: Layout fields — group (filter chips 용) + grid span.
    # width 1..4 = 가로 열수, height 1..3 = 세로 행수. Legacy 차트는 (1,1).
    group: str = ""
    width: int = 1
    height: int = 1
    # v8.5.0: User group visibility. 비어있으면 public (모든 유저). 값이 있으면
    # 해당 그룹 멤버만 볼 수 있음. admin 은 항상 전체.
    group_ids: list = []


def _charts():
    return load_json(CHARTS_FILE, [])


def _new_id():
    # v8.4.8: microseconds 포함 (시드 스크립트에서 같은 초에 여러 개 POST 하면 충돌해서 덮어씌워지던 버그)
    return f"chart_{datetime.datetime.now().strftime('%Y%m%d%H%M%S%f')}"


# ──────────────────────────────────────────────────────────────────
# Chart computation (memory-efficient)
# ──────────────────────────────────────────────────────────────────
def _compute_binning(df, x_col, bin_count=None, bin_width=None):
    if x_col not in df.columns:
        return []
    col = df[x_col]
    try:
        vals = col.cast(pl.Float64, strict=False).drop_nulls().to_list()
        if not vals:
            raise ValueError("no numeric data")
        vmin, vmax = min(vals), max(vals)
        if bin_width and bin_width > 0:
            bins = []
            v = vmin
            while v <= vmax:
                bins.append(v)
                v += bin_width
            bins.append(vmax + bin_width)
        else:
            n = bin_count or 10
            step = (vmax - vmin) / n if vmax > vmin else 1
            bins = [vmin + i * step for i in range(n + 1)]
        counts = [0] * (len(bins) - 1)
        for v in vals:
            for i in range(len(bins) - 1):
                if bins[i] <= v < bins[i + 1] or (i == len(bins) - 2 and v == bins[i + 1]):
                    counts[i] += 1
                    break
        return [{"x": f"{bins[i]:.2g}~{bins[i+1]:.2g}", "y": counts[i],
                 "label": f"{bins[i]:.3g}"} for i in range(len(counts))]
    except Exception:
        pass
    vc = col.cast(_STR, strict=False).value_counts().sort("count", descending=True).head(30)
    return [{"x": str(d.get(x_col, "") or "(null)"),
             "y": int(d.get("count", d.get("counts", 0))),
             "label": str(d.get(x_col, ""))} for d in vc.to_dicts()]


def _compute_chart(cfg: dict) -> dict:
    """Compute one chart. Uses lazy reads for memory efficiency."""
    chart_id = cfg.get("id", "")
    result = {"chart_id": chart_id, "config": cfg, "points": [], "total": 0,
              "computed_at": datetime.datetime.now().isoformat(), "error": None}
    try:
        # v7.2: If reformatter rules exist for this product, we need FULL schema (not pushed-down),
        # because rules may reference raw columns (ITEM_ID, VALUE, A/B/C/D) that chart doesn't.
        product_name = cfg.get("product", "") or (cfg.get("file", "").rsplit("_", 1)[-1].split(".")[0] if cfg.get("file") else "")
        has_reformatter = False
        reformatter_rules = []
        try:
            from core.reformatter import load_rules as _rf_load
            from core.paths import PATHS as _PATHS
            reformatter_rules = _rf_load(_PATHS.data_root / "reformatter", product_name) if product_name else []
            has_reformatter = len(reformatter_rules) > 0
        except Exception:
            pass

        lf = lazy_read_source(
            cfg.get("source_type", ""), cfg.get("root", ""),
            cfg.get("product", ""), cfg.get("file", ""))

        if lf is not None and not has_reformatter:
            # Push down column selection (memory-efficient)
            x_col = cfg.get("x_col", "")
            y_expr = cfg.get("y_expr", "")
            cc = cfg.get("color_col") or cfg.get("agg_col", "")
            time_col = cfg.get("time_col", "")
            needed = set()
            for c in [x_col, y_expr, cc, time_col]:
                if c:
                    needed.add(c)
            # v8.1.1: keep join keys in the pushed-down projection
            for j in (cfg.get("joins") or []):
                for k in ((j or {}).get("left_on") or []):
                    if k:
                        needed.add(k)
            try:
                schema_cols = lf.columns
                needed = {c for c in needed if c in schema_cols}
                if needed:
                    lf = lf.select([pl.col(c) for c in needed])
            except Exception:
                pass
            try:
                df = cast_cats(lf.collect())
            except Exception:
                df = read_source(cfg.get("source_type", ""), cfg.get("root", ""),
                                 cfg.get("product", ""), cfg.get("file", ""))
        else:
            df = read_source(cfg.get("source_type", ""), cfg.get("root", ""),
                             cfg.get("product", ""), cfg.get("file", ""))

        # Apply reformatter rules so derived indices become valid x_col / y_expr / color_col
        if has_reformatter:
            try:
                from core.reformatter import apply_rules
                df = apply_rules(df, reformatter_rules, enabled_only=True)
            except Exception as e:
                logger.warning(f"Reformatter apply failed on chart {chart_id}: {e}")

        # Time window filter
        df = apply_time_window(df, cfg.get("time_col", ""), cfg.get("days"))

        # v8.1.1: LEFT JOIN additional sources (applied AFTER reformatter+time, BEFORE sql filter)
        joins = cfg.get("joins") or []
        for ji, j in enumerate(joins):
            try:
                jst = (j or {}).get("source_type", "")
                jroot = (j or {}).get("root", "")
                jprod = (j or {}).get("product", "")
                jfile = (j or {}).get("file", "")
                left_on = (j or {}).get("left_on") or []
                right_on = (j or {}).get("right_on") or []
                if isinstance(left_on, str): left_on = [c.strip() for c in left_on.split(",") if c.strip()]
                if isinstance(right_on, str): right_on = [c.strip() for c in right_on.split(",") if c.strip()]
                if not left_on or not right_on:
                    logger.warning(f"chart {chart_id} join {ji}: missing left_on/right_on")
                    continue
                if len(left_on) != len(right_on):
                    logger.warning(f"chart {chart_id} join {ji}: key length mismatch L={left_on} R={right_on}")
                    continue
                # Pull only the keys we need plus any right-side columns referenced downstream
                right_df = read_source(jst, jroot, jprod, jfile)
                # Cast join keys to string on both sides for safety (categorical / int mismatches)
                for lk, rk in zip(left_on, right_on):
                    if lk in df.columns:
                        df = df.with_columns(pl.col(lk).cast(_STR, strict=False))
                    if rk in right_df.columns:
                        right_df = right_df.with_columns(pl.col(rk).cast(_STR, strict=False))
                # Drop right-side rows with null keys to avoid explosion
                try:
                    right_df = right_df.drop_nulls(subset=right_on)
                except Exception:
                    pass
                # Deduplicate right side on keys (keep first) to guarantee LEFT JOIN 1:1-ish behavior
                try:
                    right_df = right_df.unique(subset=right_on, keep="first")
                except Exception:
                    pass
                suffix = (j or {}).get("suffix") or f"_j{ji+1}"
                df = df.join(right_df, left_on=left_on, right_on=right_on, how="left", suffix=suffix)
            except Exception as e:
                logger.warning(f"chart {chart_id} join {ji} failed: {e}")
                result["error"] = f"join {ji} failed: {e}"

        # SQL filter
        fe = (cfg.get("filter_expr") or "").strip()
        if fe:
            try:
                df = df.filter(pl.sql_expr(fe))
            except Exception:
                pass

        ct = cfg.get("chart_type", "scatter")
        x_col = cfg.get("x_col", "")
        y_expr = cfg.get("y_expr", "")

        # v8.1.5: exclude_null — filter rows where x_col / y_expr are null/empty/"(null)"/"NaN"
        # Default True. Applied to all chart types before compute (value_counts filter for categorical too).
        if cfg.get("exclude_null", True):
            NULL_STRS = ["(null)", "null", "NULL", "None", "NaN", "nan", ""]
            for _col in [x_col, y_expr]:
                if not _col or _col not in df.columns:
                    continue
                try:
                    dtype = str(df.schema.get(_col, ""))
                    # Numeric: drop_nulls + drop NaN
                    if any(nt in dtype for nt in ("Int", "Float", "Decimal")):
                        df = df.filter(pl.col(_col).is_not_null() & pl.col(_col).is_not_nan()) \
                            if "Float" in dtype else df.filter(pl.col(_col).is_not_null())
                    else:
                        # String/Categorical: drop nulls + literal null-like strings
                        df = df.filter(pl.col(_col).is_not_null())
                        df = df.filter(~pl.col(_col).cast(_STR, strict=False).is_in(NULL_STRS))
                except Exception as _ex:
                    logger.debug(f"chart {chart_id} exclude_null on {_col}: {_ex}")

        # Pie / Donut / Binning / Pareto / Treemap
        if ct in ("binning", "pie", "donut", "pareto", "treemap") and x_col:
            if ct == "binning":
                points = _compute_binning(df, x_col, cfg.get("bin_count"), cfg.get("bin_width"))
            else:
                col = df[x_col].cast(_STR, strict=False)
                vc = col.value_counts().sort("count", descending=True).head(30)
                _exn = cfg.get("exclude_null", True)
                _nullset = {"(null)", "null", "NULL", "None", "NaN", "nan", ""}
                points = []
                for d in vc.to_dicts():
                    xv = str(d.get(x_col, "") or "(null)")
                    if _exn and (xv in _nullset or d.get(x_col) is None):
                        continue
                    points.append({"x": xv,
                                   "y": int(d.get("count", d.get("counts", 0))),
                                   "label": str(d.get(x_col, ""))})
            # Pareto: add cumulative %
            if ct == "pareto":
                total = sum(p["y"] for p in points) or 1
                cum = 0
                for p in points:
                    cum += p["y"]
                    p["cum_pct"] = round(cum / total * 100, 1)
            result["points"] = points
            result["total"] = len(points)
            result["chart_type"] = ct
            return result

        # Box plot: compute Q1, median, Q3, min, max per group
        if ct == "box" and x_col and y_expr and y_expr in df.columns:
            try:
                grp = df.group_by(x_col).agg([
                    pl.col(y_expr).count().alias("count"),
                    pl.col(y_expr).mean().alias("mean"),
                    pl.col(y_expr).median().alias("median"),
                    pl.col(y_expr).std().alias("std"),
                    pl.col(y_expr).min().alias("min"),
                    pl.col(y_expr).quantile(0.10, interpolation="linear").alias("p10"),
                    pl.col(y_expr).quantile(0.25, interpolation="linear").alias("q1"),
                    pl.col(y_expr).quantile(0.75, interpolation="linear").alias("q3"),
                    pl.col(y_expr).quantile(0.90, interpolation="linear").alias("p90"),
                    pl.col(y_expr).max().alias("max"),
                ]).sort(x_col).head(30)
                points = []
                for d in grp.to_dicts():
                    points.append({k: (round(v, 4) if isinstance(v, float) else v)
                                   for k, v in d.items()})
                    points[-1]["x"] = str(d[x_col])
                result["points"] = points
                result["total"] = len(points)
                return result
            except Exception:
                pass

        # Wafer Map: x_col=die_x, y_expr=die_y, color_col=value column
        if ct == "wafer_map" and x_col and y_expr:
            try:
                needed = [x_col, y_expr]
                val_col = cc or "BIN"
                if val_col in df.columns:
                    needed.append(val_col)
                cols_avail = [c for c in needed if c in df.columns]
                wdf = df.select(cols_avail)
                if wdf.height > 10000:
                    wdf = wdf.sample(10000, seed=42)
                points = []
                for row in wdf.to_dicts():
                    try:
                        points.append({
                            "x": int(float(row.get(x_col, 0))),
                            "y": int(float(row.get(y_expr, 0))),
                            "val": row.get(val_col, ""),
                        })
                    except Exception:
                        pass
                result["points"] = points
                result["total"] = len(points)
                return result
            except Exception:
                pass

        # Combo (bar+line): x_col=x, y_expr=bar_values, agg_col=line_values
        if ct == "combo" and x_col and y_expr:
            try:
                sel_cols = [x_col]
                if y_expr in df.columns:
                    sel_cols.append(y_expr)
                line_col = cfg.get("agg_col", "")
                if line_col and line_col in df.columns:
                    sel_cols.append(line_col)
                cdf = df.select([c for c in sel_cols if c in df.columns])
                if cdf.height > 5000:
                    cdf = cdf.sample(5000, seed=42)
                points = []
                for row in cdf.to_dicts():
                    p = {"x": str(row.get(x_col, "")), "bar": None, "line": None}
                    try:
                        p["bar"] = float(row.get(y_expr, 0))
                    except Exception:
                        pass
                    if line_col and line_col in row:
                        try:
                            p["line"] = float(row.get(line_col, 0))
                        except Exception:
                            pass
                    points.append(p)
                result["points"] = points
                result["total"] = len(points)
                return result
            except Exception:
                pass

        # Table: just serialize rows (first N) as-is
        if ct == "table":
            try:
                sel_cols = []
                # x_col can be comma-separated for multi-column display
                if x_col:
                    for c in [c.strip() for c in x_col.split(",") if c.strip()]:
                        if c in df.columns:
                            sel_cols.append(c)
                if y_expr:
                    for c in [c.strip() for c in y_expr.split(",") if c.strip()]:
                        if c in df.columns and c not in sel_cols:
                            sel_cols.append(c)
                if not sel_cols:
                    sel_cols = list(df.columns)[:12]
                tdf = df.select(sel_cols).head(200)
                result["points"] = serialize_rows(tdf.to_dicts())
                result["total"] = df.height
                result["table_columns"] = sel_cols
                return result
            except Exception as e:
                result["error"] = f"Table error: {e}"
                return result

        # Cross Table (pivot): x_col = row dim, y_expr = col dim, agg_col = value, agg_method = aggregation
        if ct == "cross_table" and x_col and y_expr:
            try:
                row_col, col_col = x_col, y_expr
                val_col = cfg.get("agg_col") or ""
                method = (cfg.get("agg_method") or "count").lower()

                if row_col not in df.columns or col_col not in df.columns:
                    result["error"] = f"Row/Col column not found"
                    return result

                # Get unique row/col values (limited)
                row_vals = df[row_col].cast(_STR, strict=False).unique().sort().head(30).to_list()
                col_vals = df[col_col].cast(_STR, strict=False).unique().sort().head(20).to_list()

                # Build grouped aggregation
                agg_expr = None
                if method == "count" or not val_col or val_col not in df.columns:
                    agg_expr = pl.count().alias("val")
                elif method == "sum":
                    agg_expr = pl.col(val_col).cast(pl.Float64, strict=False).sum().alias("val")
                elif method == "mean":
                    agg_expr = pl.col(val_col).cast(pl.Float64, strict=False).mean().alias("val")
                elif method == "min":
                    agg_expr = pl.col(val_col).cast(pl.Float64, strict=False).min().alias("val")
                elif method == "max":
                    agg_expr = pl.col(val_col).cast(pl.Float64, strict=False).max().alias("val")
                else:
                    agg_expr = pl.count().alias("val")

                grp = df.select([
                    pl.col(row_col).cast(_STR, strict=False).alias("_r"),
                    pl.col(col_col).cast(_STR, strict=False).alias("_c"),
                    *([pl.col(val_col).cast(pl.Float64, strict=False).alias(val_col)] if (val_col and val_col in df.columns) else [])
                ]).group_by(["_r", "_c"]).agg(agg_expr)

                # Build pivot dict: {row: {col: val}}
                pivot = {}
                for d in grp.to_dicts():
                    r, c, v = str(d.get("_r", "")), str(d.get("_c", "")), d.get("val")
                    if r not in pivot:
                        pivot[r] = {}
                    if isinstance(v, float):
                        v = round(v, 4)
                    pivot[r][c] = v

                # Build rows in order
                rows_out = []
                for r in row_vals:
                    row = {"_row": r}
                    total = 0
                    for c in col_vals:
                        val = pivot.get(r, {}).get(c, None)
                        row[c] = val
                        if isinstance(val, (int, float)):
                            total += val
                    row["_total"] = round(total, 4) if isinstance(total, float) else total
                    rows_out.append(row)

                result["points"] = rows_out
                result["total"] = len(rows_out)
                result["cross_rows"] = row_vals
                result["cross_cols"] = col_vals
                result["cross_method"] = method
                result["cross_val_col"] = val_col
                return result
            except Exception as e:
                result["error"] = f"Cross table error: {e}"
                return result

        # Heatmap: 2D binned grid, x_col vs y_expr, color by count
        if ct == "heatmap" and x_col and y_expr:
            try:
                xc = df[x_col].cast(pl.Float64, strict=False).drop_nulls()
                yc = df[y_expr].cast(pl.Float64, strict=False).drop_nulls()
                if xc.len() > 0 and yc.len() > 0:
                    hdf = pl.DataFrame({"_hx": xc, "_hy": yc})
                    n_bins = cfg.get("bin_count") or 20
                    xmin, xmax = float(xc.min()), float(xc.max())
                    ymin, ymax = float(yc.min()), float(yc.max())
                    xstep = (xmax - xmin) / n_bins if xmax > xmin else 1
                    ystep = (ymax - ymin) / n_bins if ymax > ymin else 1
                    hdf = hdf.with_columns([
                        ((pl.col("_hx") - xmin) / xstep).floor().cast(pl.Int32).clip(0, n_bins - 1).alias("bx"),
                        ((pl.col("_hy") - ymin) / ystep).floor().cast(pl.Int32).clip(0, n_bins - 1).alias("by"),
                    ])
                    gc = hdf.group_by(["bx", "by"]).agg(pl.count().alias("cnt"))
                    points = []
                    for row in gc.to_dicts():
                        points.append({
                            "bx": int(row["bx"]), "by": int(row["by"]),
                            "cnt": int(row["cnt"]),
                            "x_lo": round(xmin + row["bx"] * xstep, 4),
                            "x_hi": round(xmin + (row["bx"] + 1) * xstep, 4),
                            "y_lo": round(ymin + row["by"] * ystep, 4),
                            "y_hi": round(ymin + (row["by"] + 1) * ystep, 4),
                        })
                    result["points"] = points
                    result["total"] = len(points)
                    result["heatmap_meta"] = {
                        "n_bins": n_bins, "x_min": round(xmin, 4), "x_max": round(xmax, 4),
                        "y_min": round(ymin, 4), "y_max": round(ymax, 4),
                    }
                    return result
            except Exception:
                pass

        # Scatter / Line / Bar
        sel = []
        if x_col and x_col in df.columns:
            sel.append(pl.col(x_col))
        if cc and cc in df.columns:
            sel.append(pl.col(cc).alias("color"))
        if y_expr in df.columns:
            sel.append(pl.col(y_expr))
        elif y_expr:
            try:
                sel.append(pl.sql_expr(y_expr).alias("y_val"))
                y_expr = "y_val"
            except Exception:
                pass
        # v7.2: Carry selection_key column for cross-chart marking
        sel_key = cfg.get("selection_key", "LOT_WF") or ""
        if sel_key and sel_key in df.columns and sel_key not in {x_col, cc, y_expr}:
            sel.append(pl.col(sel_key).alias("_mark"))
        if not sel:
            result["error"] = "No valid columns"
            return result
        df = df.select(sel)
        if df.height > MAX_POINTS * 2:
            df = df.sample(MAX_POINTS, seed=42)

        points = []
        for row in df.to_dicts():
            y = row.get(y_expr)
            if y is not None:
                try:
                    pt = {
                        "x": str(row.get(x_col, "")),
                        "y": float(y),
                        "color": str(row.get("color", "")) if cc else None,
                    }
                    # Selection key (for cross-chart marking) — only attach when available
                    m = row.get("_mark")
                    if m is not None:
                        pt["mark"] = str(m)
                    points.append(pt)
                except Exception:
                    pass
        result["points"] = points[:MAX_POINTS]
        result["total"] = len(points)
        result["selection_key"] = sel_key

        # v6: SPC computation
        if cfg.get("enable_spc") and points:
            yvals = [p["y"] for p in points if isinstance(p.get("y"), (int, float))]
            if len(yvals) >= 3:
                cl = statistics.mean(yvals)
                sigma = statistics.stdev(yvals)
                result["spc"] = {
                    "cl": round(cl, 6),
                    "ucl": round(cl + 3 * sigma, 6),
                    "lcl": round(cl - 3 * sigma, 6),
                    "sigma": round(sigma, 6),
                }

        # v6/v7: OOS (out-of-spec) count across legacy usl/lsl AND spec_lines[]
        usl_vals = []
        lsl_vals = []
        if cfg.get("usl") is not None:
            usl_vals.append(cfg.get("usl"))
        if cfg.get("lsl") is not None:
            lsl_vals.append(cfg.get("lsl"))
        for sl in (cfg.get("spec_lines") or []):
            try:
                v = float(sl.get("value"))
                k = (sl.get("kind") or "").lower()
                if k == "usl":
                    usl_vals.append(v)
                elif k == "lsl":
                    lsl_vals.append(v)
            except Exception:
                pass
        if usl_vals or lsl_vals:
            # tightest bounds
            tight_usl = min(usl_vals) if usl_vals else None
            tight_lsl = max(lsl_vals) if lsl_vals else None
            oos = 0
            # Per-spec-line breakdown (each USL / LSL individually)
            per_spec = []
            for v in usl_vals:
                per_spec.append({"kind": "usl", "value": v, "count": 0})
            for v in lsl_vals:
                per_spec.append({"kind": "lsl", "value": v, "count": 0})
            for p in points:
                y = p.get("y")
                if not isinstance(y, (int, float)):
                    continue
                if tight_usl is not None and y > tight_usl:
                    oos += 1
                if tight_lsl is not None and y < tight_lsl:
                    oos += 1
                for sp in per_spec:
                    if sp["kind"] == "usl" and y > sp["value"]:
                        sp["count"] += 1
                    elif sp["kind"] == "lsl" and y < sp["value"]:
                        sp["count"] += 1
            result["oos_count"] = oos
            result["oos_breakdown"] = per_spec

    except Exception as e:
        result["error"] = str(e)
        logger.warning(f"Chart compute error [{chart_id}]: {e}")
    return result


# ──────────────────────────────────────────────────────────────────
# Background Scheduler
# ──────────────────────────────────────────────────────────────────
class ChartScheduler:
    def __init__(self, interval: int = 600):
        self._interval = interval
        self._snapshots: dict = {}
        self._lock = threading.Lock()
        self._thread = None
        self._load_cached()

    def _load_cached(self):
        cached = load_json(SNAP_FILE, {})
        if isinstance(cached, dict):
            self._snapshots = cached

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info(f"Chart scheduler started (interval={self._interval}s)")

    def _loop(self):
        # Initial computation after 5 seconds
        time.sleep(5)
        self._compute_all()
        while True:
            time.sleep(self._interval)
            self._compute_all()

    def _compute_all(self):
        charts = _charts()
        if not charts:
            return
        logger.info(f"Computing {len(charts)} charts...")
        t0 = time.time()
        for cfg in charts:
            if cfg.get("no_schedule"):
                continue
            try:
                snap = _compute_chart(cfg)
                # v6: OOS alert — notify admins if out-of-spec points detected
                oos = snap.get("oos_count", 0)
                old_oos = 0
                with self._lock:
                    old_snap = self._snapshots.get(cfg["id"])
                    if old_snap:
                        old_oos = old_snap.get("oos_count", 0)
                    self._snapshots[cfg["id"]] = snap
                if oos > 0 and oos != old_oos:
                    try:
                        from core.notify import send_to_admins
                        title = cfg.get("title", cfg["id"])
                        # Build spec summary including extra spec_lines
                        spec_parts = []
                        if cfg.get("usl") is not None:
                            spec_parts.append(f"USL={cfg.get('usl')}")
                        if cfg.get("lsl") is not None:
                            spec_parts.append(f"LSL={cfg.get('lsl')}")
                        for sl in (cfg.get("spec_lines") or []):
                            k = (sl.get("kind") or "").upper()
                            nm = sl.get("name") or k
                            v = sl.get("value")
                            if v is not None:
                                spec_parts.append(f"{nm}={v}")
                        spec_str = ", ".join(spec_parts) or "no specs"
                        send_to_admins(
                            f"OOS Alert: {title}",
                            f"{oos} points out of spec ({spec_str})",
                            "approval",
                        )
                    except Exception:
                        pass
            except Exception as e:
                logger.warning(f"Scheduler chart error: {e}")
        with self._lock:
            save_json(SNAP_FILE, self._snapshots)
        elapsed = time.time() - t0
        logger.info(f"Charts computed in {elapsed:.1f}s")

    def refresh(self):
        """Manual trigger (runs in background thread)."""
        threading.Thread(target=self._compute_all, daemon=True).start()

    def get_all(self) -> dict:
        with self._lock:
            return dict(self._snapshots)

    def get_one(self, chart_id: str):
        with self._lock:
            return self._snapshots.get(chart_id)


_scheduler = ChartScheduler(interval=600)
_scheduler.start()


# ──────────────────────────────────────────────────────────────────
# API Endpoints
# ──────────────────────────────────────────────────────────────────
@router.get("/charts")
def get_charts(request: Request):
    """v8.5.0: group_ids visibility 필터. admin 은 전체, 일반 유저는 자기 그룹 매칭만.
    v8.8.0: visible_to == "admin" 차트는 admin 외 차단. visible_to == "groups" 는 group_ids 와 동일하게 group 교집합 필요."""
    from core.auth import current_user
    from routers.groups import filter_by_visibility
    me = current_user(request)
    role = me.get("role", "user")
    charts = _charts()
    if role != "admin":
        charts = [c for c in charts if (c.get("visible_to") or "all") != "admin"]
    filtered = filter_by_visibility(
        charts, me["username"], role, key="group_ids"
    )
    return {"charts": filtered}


@router.get("/products")
def list_products():
    return {"products": find_all_sources()}


@router.get("/snapshots")
def get_snapshots():
    """Return all pre-computed chart data."""
    return {"snapshots": _scheduler.get_all()}


@router.post("/refresh")
def refresh_charts():
    """Admin: manually trigger re-computation."""
    _scheduler.refresh()
    return {"ok": True, "message": "Refresh started in background"}


@router.post("/charts/save")
def save_chart(cfg: ChartConfig):
    charts = _charts()
    if not cfg.id:
        cfg.id = _new_id()
    d = cfg.dict()
    for i, c in enumerate(charts):
        if c.get("id") == cfg.id:
            charts[i] = d
            break
    else:
        charts.append(d)
    save_json(CHARTS_FILE, charts, indent=2)
    # Compute this chart immediately
    snap = _compute_chart(d)
    with _scheduler._lock:
        _scheduler._snapshots[cfg.id] = snap
        save_json(SNAP_FILE, _scheduler._snapshots)
    return {"ok": True, "id": cfg.id}


@router.post("/charts/delete")
def delete_chart(chart_id: str = Query(...)):
    charts = [c for c in _charts() if c.get("id") != chart_id]
    save_json(CHARTS_FILE, charts, indent=2)
    with _scheduler._lock:
        _scheduler._snapshots.pop(chart_id, None)
        save_json(SNAP_FILE, _scheduler._snapshots)
    return {"ok": True}


@router.post("/charts/copy")
def copy_chart(chart_id: str = Query(...)):
    import copy as cp
    charts = _charts()
    src = next((c for c in charts if c.get("id") == chart_id), None)
    if not src:
        raise HTTPException(404)
    new = cp.deepcopy(src)
    new["id"] = _new_id()
    new["title"] = src.get("title", "") + " (copy)"
    charts.append(new)
    save_json(CHARTS_FILE, charts, indent=2)
    return {"ok": True, "id": new["id"]}


@router.get("/columns")
def get_columns(root: str = Query(""), product: str = Query(""), file: str = Query(""),
                source_type: str = Query("")):
    # v8.8.7: PATHS 는 함수 진입부에서 임포트 — 이전에 base_file 분기에서만 임포트 되어
    #   hive DB 분기에서 UnboundLocalError 발생 ("cannot access local variable 'PATHS'") 하던 버그 수정.
    from core.paths import PATHS
    if source_type == "base_file" and file:
        fp = PATHS.base_root / file
        if not fp.is_file():
            raise HTTPException(404, f"Base file not found: {file}")
        df = read_one_file(fp)
        if df is None:
            raise HTTPException(400, "Cannot read base file")
        df = df.head(1)
        return {"columns": list(df.columns), "dtypes": {n: str(d) for n, d in df.schema.items()}}
    # v8.8.3: lazy resolve — PATHS.db_root 를 매 호출마다 읽어 admin 런타임 변경 반영.
    db_base = PATHS.db_root
    if file:
        fp = db_base / file
        if not fp.is_file():
            raise HTTPException(404, f"File not found: {file} (db_root={db_base})")
        df = read_one_file(fp)
        if df is None:
            raise HTTPException(400, f"Cannot read file: {file}")
        df = df.head(1)
    else:
        prod_path = db_base / root / product
        if not prod_path.is_dir():
            raise HTTPException(
                404,
                f"Product directory not found: {root}/{product} (db_root={db_base})"
            )
        from core.utils import _glob_data_files
        files = _glob_data_files(prod_path)
        if not files:
            raise HTTPException(
                404,
                f"No data files found in: {root}/{product} (db_root={db_base})"
            )
        df = read_one_file(files[0])
        if df is None:
            raise HTTPException(400, f"Cannot read data file: {files[0].name}")
        df = df.head(1)
    return {"columns": list(df.columns), "dtypes": {n: str(d) for n, d in df.schema.items()}}


@router.get("/preview")
def preview_data(root: str = Query(""), product: str = Query(""),
                 file: str = Query(""), source_type: str = Query(""),
                 x_col: str = Query(""), y_expr: str = Query(""),
                 filter_expr: str = Query(""), time_col: str = Query(""),
                 days: str = Query(""), limit: int = Query(10)):
    try:
        df = read_source(source_type, root, product, file, max_files=5)
        df = apply_time_window(df, time_col, days) if days.strip() else df
        if filter_expr.strip():
            try:
                df = df.filter(pl.sql_expr(filter_expr))
            except Exception as e:
                raise HTTPException(400, f"Filter error: {e}")
        sel = []
        if x_col and x_col in df.columns:
            sel.append(x_col)
        if y_expr and y_expr in df.columns:
            sel.append(y_expr)
        if not sel:
            sel = list(df.columns)[:5]
        show = df.select([c for c in sel if c in df.columns]).head(limit)
        data = serialize_rows(show.to_dicts())
        return {"rows": data, "total": df.height, "columns": list(show.columns)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Preview error: {str(e)}")


@router.get("/data")
def get_chart_data(chart_id: str = Query(...)):
    """Returns snapshot if available, otherwise computes on-demand."""
    snap = _scheduler.get_one(chart_id)
    if snap:
        return snap
    # Fallback: compute on-demand
    charts = _charts()
    cfg = next((c for c in charts if c.get("id") == chart_id), None)
    if not cfg:
        raise HTTPException(404, "Chart not found")
    return _compute_chart(cfg)
