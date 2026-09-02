import { useEffect, useMemo, useRef, useState } from "react";
import createPlotlyComponent from "react-plotly.js/factory";
import Plotly from "../lib/plotlyCustom";
import { chartBoxFor, chartLayoutKind, useElementWidth } from "../lib/chartLayout";
import { sortCategoryValues } from "../lib/boxStats";
import { chartPalette, buildSeriesColors } from "./UXKit";

// partial bundle 로 컴포넌트를 만든다 (lib/plotlyCustom.js 참조).
const Plot = createPlotlyComponent(Plotly);

const SERIES = chartPalette.series || ["#2563eb", "#dc2626", "#16a34a", "#9333ea", "#f59e0b", "#0891b2"];
const MISSING_COLOR = "#9ca3af";

function lightenHex(hex, amount = 0.52) {
  const value = String(hex || "").replace("#", "");
  if (!/^[0-9a-f]{6}$/i.test(value)) return hex;
  const rgb = [0, 2, 4].map((offset) => parseInt(value.slice(offset, offset + 2), 16));
  return `rgb(${rgb.map((channel) => Math.round(channel + (255 - channel) * amount)).join(",")})`;
}

function text(value) {
  return value == null ? "" : String(value);
}

function numberOrValue(value) {
  const n = Number(value);
  return Number.isFinite(n) && text(value).trim() !== "" ? n : value;
}

function numberOrNull(value) {
  const n = Number(value);
  return Number.isFinite(n) && text(value).trim() !== "" ? n : null;
}

function cubicRegression(rows) {
  const clean = rows
    .map((point) => ({ x: numberOrNull(point?.x), y: numberOrNull(pointY(point)) }))
    .filter((point) => point.x != null && point.y != null);
  const uniqueX = new Set(clean.map((point) => point.x));
  if (clean.length < 4 || uniqueX.size < 4) return null;
  const xs = clean.map((point) => point.x);
  const center = (Math.min(...xs) + Math.max(...xs)) / 2;
  const scale = Math.max((Math.max(...xs) - Math.min(...xs)) / 2, 1e-9);
  const matrix = Array.from({ length: 4 }, () => Array(5).fill(0));
  for (const point of clean) {
    const t = (point.x - center) / scale;
    const powers = [1, t, t * t, t * t * t];
    for (let row = 0; row < 4; row += 1) {
      for (let col = 0; col < 4; col += 1) matrix[row][col] += powers[row] * powers[col];
      matrix[row][4] += powers[row] * point.y;
    }
  }
  for (let pivot = 0; pivot < 4; pivot += 1) {
    let best = pivot;
    for (let row = pivot + 1; row < 4; row += 1) {
      if (Math.abs(matrix[row][pivot]) > Math.abs(matrix[best][pivot])) best = row;
    }
    if (Math.abs(matrix[best][pivot]) < 1e-12) return null;
    [matrix[pivot], matrix[best]] = [matrix[best], matrix[pivot]];
    const divisor = matrix[pivot][pivot];
    for (let col = pivot; col < 5; col += 1) matrix[pivot][col] /= divisor;
    for (let row = 0; row < 4; row += 1) {
      if (row === pivot) continue;
      const factor = matrix[row][pivot];
      for (let col = pivot; col < 5; col += 1) matrix[row][col] -= factor * matrix[pivot][col];
    }
  }
  const coefficients = matrix.map((row) => row[4]);
  const predict = (x) => {
    const t = (x - center) / scale;
    return coefficients[0] + coefficients[1] * t + coefficients[2] * t * t + coefficients[3] * t * t * t;
  };
  const mean = clean.reduce((sum, point) => sum + point.y, 0) / clean.length;
  const residual = clean.reduce((sum, point) => sum + (point.y - predict(point.x)) ** 2, 0);
  const total = clean.reduce((sum, point) => sum + (point.y - mean) ** 2, 0);
  return { minX: Math.min(...xs), maxX: Math.max(...xs), predict, r2: total > 0 ? 1 - residual / total : 1 };
}

function pointX(point) {
  return point?.x_label ?? point?.tkout_time ?? point?.time ?? point?.x;
}

function pointY(point) {
  return point?.y ?? point?.median ?? point?.avg ?? point?.value;
}

function colorValue(point) {
  return text(point?.color_value || point?.color || point?.series || "").trim();
}

function hoverLines(point, xLabel, yLabel, colorBy) {
  const rows = [
    `${xLabel || "X"}: ${text(pointX(point))}`,
    `${yLabel || "Y"}: ${text(pointY(point))}`,
  ];
  for (const key of ["label", "lot_wf", "root_lot_id", "lot_id", "wafer_id", "step_id", "radius_shot", "source_shot", "n"]) {
    if (point?.[key] !== undefined && text(point[key])) rows.push(`${key}: ${text(point[key])}`);
  }
  const cv = colorValue(point);
  if (colorBy && cv) rows.push(`${colorBy}: ${cv}`);
  if (colorBy && !cv) rows.push(`${colorBy}: missing`);
  return rows.join("<br>");
}

export function FlowPlotlyChart({
  chart,
  cfg = {},
  // height 를 주면 그 높이로 고정한다. 비워 두면 컨테이너 폭을 재서 차트 종류에
  // 맞는 비율로 스스로 정한다(lib/chartLayout.js) — 이쪽이 기본이다.
  height = 0,
  layoutKind = "",
  dark = false,
  onPointClick = null,
  selectionKey = "",
  enableHighlight = false,
  // 그려진 뒤 실제 그림 영역 좌표를 올려 준다 — 아래에 붙는 표를 눈금과 같은
  // 자리에 맞추려면 plotly 가 automargin 으로 정한 최종 여백을 알아야 한다.
  onGeometry = null,
}) {
  const points = Array.isArray(chart?.points) ? chart.points : [];
  // 사전 계산된 5수 요약(min/q1/median/q3/max). flow-i 는 원 측정값 대신 이 형태로
  // box 를 내려준다 — 원 표본이 없어도 plotly box trace 로 그릴 수 있다.
  const boxStats = Array.isArray(chart?.boxes) ? chart.boxes : [];
  const groups = Array.isArray(chart?.groups) ? chart.groups : Array.isArray(chart?.slices) ? chart.slices : [];
  const xLabel = cfg.x_label || chart?.x_label || cfg.x_col || chart?.x_col || "X";
  const yLabel = cfg.y_label || chart?.y_label || cfg.y_expr || chart?.y_col || "Y";
  const colorBy = cfg.color_by || chart?.color_by || "";
  const colorMap = cfg.color_map || chart?.color_map || {};
  const chartType = String(cfg.chart_type || chart?.chart_type || chart?.kind || "scatter").replace("dashboard_", "");
  const radial = chartType === "pie" || chartType === "donut";
  const title = cfg.title || chart?.title || "";
  const axisXLabel = chartType === "bar_horizontal" ? yLabel : xLabel;
  const axisYLabel = chartType === "bar_horizontal" ? xLabel : yLabel;
  const markerSize = Number(cfg.point_size || chart?.render_preset?.point_size || 9);
  const markerOpacity = Math.max(0.05, Math.min(1, Number(cfg.marker_opacity ?? chart?.marker_opacity ?? 0.82)));
  const lineWidth = Math.max(0.5, Math.min(8, Number(cfg.line_width ?? chart?.line_width ?? 2.3)));
  const isTrendScatter = Boolean(cfg.trend_grain || chart?.trend_grain);
  const emphasizeMarkers = Boolean(isTrendScatter || cfg.emphasize_markers || chart?.emphasize_markers);
  const useSvg = Boolean(cfg.use_svg || chart?.use_svg);
  const compact = Boolean(cfg.compact || chart?.compact);
  const hideTitle = Boolean(cfg.hide_title || chart?.hide_title);
  const emphasizeAxes = Boolean(cfg.emphasize_axes || chart?.emphasize_axes);
  const axisTitleSize = Number(cfg.axis_title_size || chart?.axis_title_size || (isTrendScatter ? 18 : 16));
  const axisLineWidth = Number(cfg.axis_line_width || chart?.axis_line_width || (emphasizeAxes ? 2 : 1));
  const tickFontSize = Number(cfg.tick_font_size || chart?.tick_font_size || 11);
  // 아래에 눈금과 정렬된 표가 붙을 때는 x 눈금 글자를 끈다 — 표 머리글이 같은
  // 이름을 다시 쓰므로 그대로 두면 카테고리 이름이 두 줄로 겹쳐 보인다.
  const hideXTicks = Boolean(cfg.hide_x_ticks || chart?.hide_x_ticks);
  // 저장 차트의 SHOW_LEGEND=false 계약. Template Report에서 공통 범례 블록을
  // 따로 둘 때 각 차트 내부의 중복 범례만 감춘다.
  const showLegend = cfg.show_legend !== false && chart?.show_legend !== false;
  const showGrid = cfg.show_grid !== false && chart?.show_grid !== false;
  const legendPosition = String(cfg.legend_position || chart?.legend_position || "bottom");
  const yMin = numberOrNull(cfg.y_min ?? chart?.y_min);
  const yMax = numberOrNull(cfg.y_max ?? chart?.y_max);
  const yRange = yMin != null && yMax != null ? [yMin, yMax] : undefined;
  const yScale = String(cfg.y_scale || chart?.y_scale || "linear") === "log" ? "log" : "linear";
  const valueAxisIsX = chartType === "bar_horizontal";
  const scaledRange = yRange?.map(value => yScale === "log" ? Math.log10(Math.max(value, 1e-300)) : value);
  const boxPoints = String(cfg.box_points || chart?.box_points || "outliers");
  const fit = chart?.fit || chart?.fit_params || cfg.fit_params || null;
  const fitOk = fit && Number.isFinite(Number(fit.slope)) && Number.isFinite(Number(fit.intercept));
  const fitLabel = fitOk && Number.isFinite(Number(fit.r2)) ? `R²=${Number(fit.r2).toFixed(4)}` : "";
  const cubicFit = Boolean(cfg.cubic_fit || chart?.cubic_fit);
  const bg = dark ? "#111111" : "#ffffff";
  const fg = dark ? "#e5e7eb" : "#111827";
  const grid = dark ? "rgba(148,163,184,0.22)" : "rgba(15,23,42,0.12)";

  const { traces, legendCounts, categorical, columnCount, rowCount, categoryArray } = useMemo(() => {
    if ((chartType === "pie" || chartType === "donut") && groups.length) {
      return {
        categorical: true,
        columnCount: 0,
        rowCount: 0,
        legendCounts: groups.map((row) => ({ name: text(row?.label || row?.group || row?.value), count: Number(row?.count ?? row?.value ?? 0) })),
        traces: [{
          type: "pie",
          hole: chartType === "donut" ? 0.46 : 0,
          labels: groups.map((row) => text(row?.label || row?.group || "(empty)")),
          values: groups.map((row) => Number(row?.count ?? row?.value ?? 0)),
          // 글자는 조각 안에만 쓴다 — 밖으로 내보내면 지시선이 사방으로 뻗고
          // 위아래 라벨이 카드에 잘린다. 안 들어가는 라벨은 plotly 가 감추고,
          // 이름은 아래 레전드가, 값은 hover 가 대신 말해 준다.
          textinfo: "label+percent",
          textposition: "inside",
          insidetextorientation: "horizontal",
          hovertemplate: `%{label}<br>${yLabel || "count"}=%{value}<br>%{percent}<extra></extra>`,
          marker: { colors: groups.map((_, idx) => SERIES[idx % SERIES.length]) },
          sort: false,
        }],
      };
    }
    if ((chartType === "bar" || chartType === "bar_horizontal") && groups.length && !points.length) {
      const horizontal = chartType === "bar_horizontal";
      const labels = groups.map((row) => text(row?.label || row?.group || "(empty)"));
      const values = groups.map((row) => Number(row?.value ?? row?.count ?? 0));
      return {
        categorical: true,
        columnCount: groups.length,
        rowCount: horizontal ? groups.length : 0,
        legendCounts: groups.map((row) => ({ name: text(row?.label || row?.group || row?.value), count: Number(row?.count ?? 0) })),
        traces: [{
          type: "bar",
          orientation: horizontal ? "h" : "v",
          name: yLabel || "count",
          x: horizontal ? values : labels,
          y: horizontal ? labels : values,
          customdata: groups.map((row) => Number(row?.count || 0)),
          text: groups.map((row) => row?.percent != null ? `${row.percent}%` : ""),
          textposition: "auto",
          hovertemplate: horizontal ? `${xLabel}: %{y}<br>${yLabel}: %{x}<br>n=%{customdata}<extra></extra>` : `${xLabel}: %{x}<br>${yLabel}: %{y}<br>n=%{customdata}<extra></extra>`,
          // 값이 한 종류인 막대는 한 가지 색으로 그린다 — 막대마다 색을 바꾸면
          // 이름표가 이미 하는 구분을 색이 또 하면서 크기 비교를 방해한다.
          marker: { color: SERIES[0], opacity: markerOpacity },
        }],
      };
    }
    if (chartType === "box" && boxStats.length) {
      // q1/median/q3 signature — 표본 배열 대신 상자 하나당 값 하나씩을 넘긴다.
      const rows = boxStats
        .filter((row) => ["q1", "median", "q3"].every((key) => Number.isFinite(Number(row?.[key]))))
        .slice(0, 60);
      const pick = (key) => rows.map((row) => numberOrNull(row?.[key]));
      const means = pick("mean");
      const seriesColor = SERIES[0];
      return {
        categorical: true,
        columnCount: rows.length,
        rowCount: 0,
        categoryArray: rows.map((row) => text(row?.label || "(empty)")),
        // 상자 하나가 곧 x 눈금이라 레전드로 다시 늘어놓을 이유가 없다.
        legendCounts: [],
        traces: [{
          type: "box",
          name: yLabel,
          showlegend: false,
          x: rows.map((row) => text(row?.label || "(empty)")),
          q1: pick("q1"),
          median: pick("median"),
          q3: pick("q3"),
          lowerfence: pick("min"),
          upperfence: pick("max"),
          ...(rows.length && means.every((value) => value != null) ? { mean: means, boxmean: true } : {}),
          customdata: rows.map((row) => Number(row?.n || 0)),
          hovertemplate: `%{x}<br>${yLabel}<br>max %{upperfence}<br>q3 %{q3}<br>median %{median}<br>q1 %{q1}<br>min %{lowerfence}<br>n=%{customdata}<extra></extra>`,
          marker: { color: seriesColor, opacity: markerOpacity, size: markerSize },
          line: { color: seriesColor, width: Math.max(0.5,lineWidth*0.7) },
          fillcolor: lightenHex(seriesColor, 0.62),
        }],
      };
    }
    if (chartType === "box" && points.length) {
      const buckets = new Map();
      for (const point of points) {
        const raw = colorBy ? colorValue(point) : "";
        const key = colorBy ? (raw || "missing") : "data";
        if (!buckets.has(key)) buckets.set(key, []);
        buckets.get(key).push(point);
      }
      const entries = Array.from(buckets.entries());
      // 상자는 본디 범주다 — x 를 숫자축에 두면 간격이 값 차이만큼 벌어져 상자
      // 폭도 자리도 들쭉날쭉해지고, 아래 통계표를 눈금에 맞출 수도 없다.
      const categoryArray = sortCategoryValues(points.map((point) => pointX(point)));
      return {
        categorical: true,
        columnCount: categoryArray.length,
        rowCount: 0,
        categoryArray,
        legendCounts: entries.map(([name, rows]) => ({ name, count: rows.length })),
        traces: entries.map(([name, rows], idx) => ({
          type: "box",
          name: colorBy ? `${name} (${rows.length})` : yLabel,
          x: rows.map((point) => text(pointX(point))),
          y: rows.map((point) => numberOrValue(pointY(point))),
          text: rows.map((point) => hoverLines(point, xLabel, yLabel, colorBy)),
          hoverinfo: "text",
          customdata: rows,
          boxpoints: boxPoints === "none" ? false : boxPoints,
          jitter: 0.2,
          pointpos: 0,
          marker: { color: name === "missing" ? MISSING_COLOR : colorMap[name] || SERIES[idx % SERIES.length], opacity: markerOpacity, size: markerSize },
          line: { color: name === "missing" ? MISSING_COLOR : colorMap[name] || SERIES[idx % SERIES.length], width: Math.max(0.5,lineWidth*0.7) },
          fillcolor: lightenHex(name === "missing" ? MISSING_COLOR : colorMap[name] || SERIES[idx % SERIES.length], 0.62),
        })),
      };
    }
    const buckets = new Map();
    for (const point of points) {
      const raw = colorBy ? colorValue(point) : "";
      const key = colorBy ? (raw || "missing") : "data";
      if (!buckets.has(key)) buckets.set(key, []);
      buckets.get(key).push(point);
    }
    const entries = Array.from(buckets.entries());
    const plotTraces = entries.map(([name, rows], idx) => {
      const missing = name === "missing";
      const mode = chartType === "line" ? "lines+markers" : "markers";
      const seriesColor = missing ? MISSING_COLOR : colorMap[name] || SERIES[idx % SERIES.length];
      return {
        type: useSvg ? "scatter" : "scattergl",
        mode,
        name: colorBy ? `${name} (${rows.length})` : "data",
        x: rows.map((point) => numberOrValue(pointX(point))),
        y: rows.map((point) => numberOrValue(pointY(point))),
        text: rows.map((point) => hoverLines(point, xLabel, yLabel, colorBy)),
        hoverinfo: "text",
        customdata: rows,
        marker: {
          size: markerSize,
          color: emphasizeMarkers ? lightenHex(seriesColor) : seriesColor,
          opacity: emphasizeMarkers ? Math.max(markerOpacity,0.92) : missing ? Math.min(markerOpacity,0.58) : markerOpacity,
          line: emphasizeMarkers
            ? { color: dark ? "#6b7280" : "#374151", width: 1.3 }
            : { color: dark ? "#111111" : "#ffffff", width: 0.6 },
        },
        line: { color: seriesColor, width: chartType === "line" ? lineWidth : Math.max(0.5,lineWidth*0.78) },
      };
    });
    if (cubicFit) {
      entries.forEach(([name, rows], idx) => {
        const regression = cubicRegression(rows);
        if (!regression) return;
        const seriesColor = name === "missing" ? MISSING_COLOR : colorMap[name] || SERIES[idx % SERIES.length];
        const x = Array.from({ length: 81 }, (_, pointIndex) => regression.minX + ((regression.maxX - regression.minX) * pointIndex) / 80);
        plotTraces.push({
          type: "scatter",
          mode: "lines",
          name: colorBy ? `${name} · 3차 fit` : "3차 fit",
          showlegend: false,
          x,
          y: x.map(regression.predict),
          hovertemplate: `${colorBy ? `${colorBy}: ${name}<br>` : ""}3차 fit · R²=${regression.r2.toFixed(4)}<extra></extra>`,
          line: { color: seriesColor, width: 2.5 },
        });
      });
    }
    if (fitOk) {
      const fitRows = points.map((point, idx) => {
        const x = numberOrNull(point?.x);
        const y = numberOrNull(pointY(point));
        return { x: x == null ? idx : x, displayX: numberOrValue(pointX(point)), y };
      }).filter((row) => row.y != null);
      if (fitRows.length >= 2) {
        fitRows.sort((a, b) => a.x - b.x);
        const first = fitRows[0];
        const last = fitRows[fitRows.length - 1];
        plotTraces.push({
          type: "scatter",
          mode: "lines",
          name: fitLabel ? `1차 fit (${fitLabel})` : "1차 fit",
          x: [first.displayX, last.displayX],
          y: [Number(fit.slope) * first.x + Number(fit.intercept), Number(fit.slope) * last.x + Number(fit.intercept)],
          hoverinfo: "skip",
          line: { color: "#ef4444", width: 2.4, dash: "dash" },
        });
      }
    }
    for (const [specKey, specName] of [["spec_high", "spec high"], ["spec_low", "spec low"]]) {
      const specRows = points
        .map((point) => ({ x: numberOrValue(pointX(point)), y: numberOrNull(point?.[specKey]) }))
        .filter((row) => row.y != null);
      if (specRows.length) {
        plotTraces.push({
          type: "scatter",
          mode: "lines",
          name: specName,
          x: specRows.map((row) => row.x),
          y: specRows.map((row) => row.y),
          hovertemplate: `${specName}: %{y}<extra></extra>`,
          line: { color: "#ef4444", width: 1.6, shape: "hv", dash: "dot" },
        });
      }
    }
    return {
      traces: plotTraces,
      legendCounts: entries.map(([name, rows]) => ({ name, count: rows.length })),
      categorical: false,
      columnCount: 0,
      rowCount: 0,
    };
  }, [points, groups, boxStats, colorBy, colorMap, chartType, xLabel, yLabel, markerSize, markerOpacity, lineWidth, boxPoints, dark, fitOk, fit, fitLabel, cubicFit, isTrendScatter, emphasizeMarkers, useSvg]);

  const [highlightSelection, setHighlightSelection] = useState(null);
  useEffect(() => { setHighlightSelection(null); }, [points, chartType, colorBy, enableHighlight]);
  const selectedCount = useMemo(() => highlightSelection
    ? Object.values(highlightSelection).reduce((sum, values) => sum + values.length, 0)
    : 0, [highlightSelection]);
  const renderedTraces = useMemo(() => traces.map((trace, traceIndex) => {
    if (!enableHighlight || !Array.isArray(trace.customdata)) return trace;
    const selectedpoints = highlightSelection ? (highlightSelection[traceIndex] || []) : null;
    return {
      ...trace,
      selectedpoints,
      selected: { marker: { opacity: 1, size: markerSize + 3, line: { color: "#111827", width: 2 } } },
      unselected: { marker: { opacity: highlightSelection ? 0.16 : undefined } },
    };
  }), [traces, enableHighlight, highlightSelection, markerSize]);

  // 폭은 바깥 div 에서 재고, 높이는 차트 종류의 비율에서 나온다.
  const [boxRef, boxWidth] = useElementWidth();
  const layoutKey = layoutKind || chartLayoutKind(chartType, { trendGrain: cfg.trend_grain || chart?.trend_grain });
  const geo = chartBoxFor(layoutKey, boxWidth, { columns: columnCount, rows: rowCount });
  const configuredWidth = Number(cfg.width || chart?.width || 0);
  const configuredHeight = Number(height || cfg.height || chart?.height || 0);
  const requestedWidth = Number.isFinite(configuredWidth) && configuredWidth > 0 ? Math.max(320, Math.min(2400, configuredWidth)) : 0;
  const plotWidth = requestedWidth ? Math.min(requestedWidth, boxWidth || requestedWidth) : geo.width;
  const plotHeight = Number.isFinite(configuredHeight) && configuredHeight > 0 ? Math.max(240, Math.min(1600, configuredHeight)) : geo.height;

  const geometryRef = useRef("");
  const reportGeometry = (_figure, gd) => {
    const size = gd?._fullLayout?._size;
    if (!onGeometry || !size) return;
    const next = { left: Math.round(size.l), right: Math.round(size.r), plotWidth: Math.round(size.w), width: plotWidth };
    const signature = `${next.left}|${next.right}|${next.plotWidth}|${next.width}`;
    if (signature === geometryRef.current) return; // 같은 값이면 다시 올리지 않는다(렌더 루프 방지)
    geometryRef.current = signature;
    onGeometry(next);
  };

  if (!points.length && !groups.length && !boxStats.length) {
    return <div style={{ minHeight: Math.max(180, plotHeight), display: "flex", alignItems: "center", justifyContent: "center", color: dark ? "#9ca3af" : "#64748b", fontSize: 14 }}>차트로 표시할 point가 없습니다.</div>;
  }

  return (
    <div ref={boxRef} style={{ width: requestedWidth ? `${requestedWidth}px` : "100%", maxWidth: "100%", margin: "0 auto", position: "relative" }}>
      {enableHighlight&&selectedCount>0&&<button type="button" onClick={()=>setHighlightSelection(null)} style={{position:"absolute",right:10,top:8,zIndex:4,border:"1px solid #cbd5e1",borderRadius:6,background:"rgba(255,255,255,.94)",color:"#334155",padding:"5px 8px",fontSize:11,fontWeight:800,cursor:"pointer"}}>선택 {selectedCount.toLocaleString()}개 · 하이라이트 해제</button>}
      <Plot
        data={renderedTraces}
        layout={{
          title: title && !hideTitle ? { text: title, font: { size: compact ? 15 : 18, color: fg } } : undefined,
          autosize: true,
          height: plotHeight,
          paper_bgcolor: bg,
          plot_bgcolor: bg,
          // 축 라벨이 없는 원 차트는 좌우 여백이 필요 없다 — 원을 키운다.
          margin: radial
            ? { l: 20, r: 20, t: title && !hideTitle ? 44 : 16, b: 16 }
            : { l: 64, r: 22, t: title && !hideTitle ? 48 : 20, b: 56 },
          hovermode: "closest",
          dragmode: "pan",
          clickmode: enableHighlight ? "event+select" : "event",
          // 원 차트에는 축이 없다. 이때 xaxis/yaxis 키를 undefined 로 "넣어" 두면
          // plotly 의 cleanLayout 이 layout.xaxis.anchor 를 읽다가 터진다(빈 차트).
          // 키 자체를 빼야 한다.
          ...(radial ? {} : {
            xaxis: {
              title: { text: axisXLabel, font: { size: axisTitleSize, color: fg, family: emphasizeAxes ? "Arial Black, Malgun Gothic, sans-serif" : undefined } },
              tickfont: { size: tickFontSize, color: fg },
              ...(valueAxisIsX ? { type: yScale, ...(scaledRange ? { range: scaledRange } : {}) } : {}),
              showticklabels: !hideXTicks,
              // 상자는 범주축에 균등 배치 — 그래야 아래 통계표 열과 자리가 맞는다.
              ...(chartType === "box" && categoryArray?.length
                ? { type: "category", categoryorder: "array", categoryarray: categoryArray }
                : {}),
              showgrid: showGrid,
              gridcolor: grid,
              zerolinecolor: grid,
              color: fg,
              showline: emphasizeAxes,
              linecolor: emphasizeAxes ? "#000000" : fg,
              linewidth: axisLineWidth,
              mirror: false,
              automargin: true,
            },
            yaxis: {
              title: { text: axisYLabel, font: { size: axisTitleSize, color: fg, family: emphasizeAxes ? "Arial Black, Malgun Gothic, sans-serif" : undefined } },
              tickfont: { size: tickFontSize, color: fg },
              ...(!valueAxisIsX ? { type: yScale, ...(scaledRange ? { range: scaledRange } : {}) } : {}),
              showgrid: showGrid,
              gridcolor: grid,
              zerolinecolor: grid,
              color: fg,
              showline: emphasizeAxes,
              linecolor: emphasizeAxes ? "#000000" : fg,
              linewidth: axisLineWidth,
              mirror: false,
              automargin: true,
            },
          }),
          legend: legendPosition==="right"
            ? {orientation:"v",x:1.02,y:1,xanchor:"left",yanchor:"top",font:{size:11,color:fg}}
            : legendPosition==="top"
              ? {orientation:"h",x:0,y:1.08,xanchor:"left",yanchor:"bottom",font:{size:11,color:fg}}
              : legendPosition==="inside"
                ? {orientation:"v",x:0.99,y:0.99,xanchor:"right",yanchor:"top",bgcolor:dark?"rgba(17,17,17,.75)":"rgba(255,255,255,.8)",font:{size:11,color:fg}}
                : {orientation:"h",y:-0.22,x:0,font:{size:11,color:fg}},
          showlegend: showLegend && (colorBy ? true : legendCounts.length > 1),
          // 회귀 라벨은 그림 "안쪽" 오른쪽 위에 둔다 — 예전처럼 y=1.08 로 밖에
          // 내보내면 위 여백이 좁을 때 글자 윗줄이 카드에 잘려 나갔다.
          annotations: fitLabel ? [{
            xref: "paper",
            yref: "paper",
            x: 0.99,
            y: 0.98,
            xanchor: "right",
            yanchor: "top",
            showarrow: false,
            text: fit?.equation ? `${fit.equation}<br>${fitLabel}` : fitLabel,
            font: { size: 12, color: "#ef4444" },
            bgcolor: dark ? "rgba(17,17,17,0.78)" : "rgba(255,255,255,0.9)",
            bordercolor: "rgba(239,68,68,0.35)",
            borderwidth: 1,
          }] : [],
        }}
        config={{
          responsive: true,
          displaylogo: false,
          displayModeBar: enableHighlight || !compact,
          scrollZoom: true,
          toImageButtonOptions: { format: "png", filename: String(title || "flowi-chart").replace(/[^a-zA-Z0-9가-힣_-]+/g, "_"), scale: 2 },
          modeBarButtonsToRemove: enableHighlight ? [] : ["lasso2d", "select2d"],
        }}
        useResizeHandler
        onInitialized={reportGeometry}
        onUpdate={reportGeometry}
        // react-plotly 는 렌더 실패를 onError 로만 알린다 — 안 받으면 promise
        // catch 가 삼켜서 "빈 차트"만 남고 원인이 사라진다.
        onError={(error) => console.error("[FlowPlotlyChart] plotly render failed", chartType, error)}
        // 폭 상한은 여기서 건다 — 관측용 바깥 div 를 좁히면 다음 측정이 그 값을
        // 다시 읽어 폭이 돌아오지 못한다.
        style={{ width: plotWidth, maxWidth: "100%", height: plotHeight, margin: "0 auto" }}
        onClick={(event) => {
          const point = event?.points?.[0]?.customdata;
          if (point && onPointClick) onPointClick(point, selectionKey);
        }}
        onSelected={(event) => {
          if (!enableHighlight) return;
          const next = {};
          for (const point of event?.points || []) {
            const traceIndex = Number(point.curveNumber);
            const pointIndex = Number(point.pointNumber ?? point.pointIndex);
            if (!Number.isInteger(traceIndex) || !Number.isInteger(pointIndex)) continue;
            if (!next[traceIndex]) next[traceIndex] = [];
            next[traceIndex].push(pointIndex);
          }
          setHighlightSelection(Object.keys(next).length ? next : null);
        }}
        onDeselect={()=>{if(enableHighlight)setHighlightSelection(null);}}
        onDoubleClick={()=>{if(enableHighlight)setHighlightSelection(null);}}
      />
    </div>
  );
}

// 대략적인 글자 폭(= font size × 비율). Plotly 기본 sans-serif 기준 근사치.
const GLYPH_W = 0.62;
// 스택 세그먼트 사이를 갈라놓는 표면색 간격(px). 테두리를 그리는 대신 배경색
// 선을 얹어 인접 색이 서로 번지지 않게 한다.
const SEGMENT_GAP = 1;
// 막대 두께 상한 — 굵은 색 블록이 되지 않도록 잘라내고 남는 폭은 여백으로 둔다.
const BAR_MAX_PX = 24;
// bin 이 한두 개뿐이면(예: 간격 100000) 24px 막대 하나가 빈 캔버스에 떠 있는
// 꼴이 된다 — 이때만 두께를 풀어 "전체 대비 구성" 막대처럼 읽히게 한다.
const BAR_MAX_PX_SPARSE = 96;

export function WipStackedBar({
  bins = [],
  splitValues = [],
  height = 420,
  dark = false,
  unassignedLabel = "(미지정)",
  otherLabel = "",
  norm = "count",   // "count" | "percent" — percent 면 각 구간을 100% 로 정규화
  axis = "step_id", // "step_id" | "step_desc" — step_desc 는 눈금을 생략하지 않는다
  onSegmentClick = null, // (bin, label, split) — 세그먼트 클릭 드릴다운
}) {
  // 표면/잉크는 앱 테마 토큰과 같은 값 — 차트가 카드 위에 얹힌 것처럼 보이게.
  const surface = dark ? "#262626" : "#ffffff";
  const ink = dark ? "#e5e5e5" : "#171717";
  const muted = dark ? "#a3a3a3" : "#737373";
  const hairline = dark ? "#333333" : "#ececec";
  const [boxRef, boxW] = useElementWidth();

  const baseH = Math.max(240, Math.round(height));
  const tickFont = 10;

  // 첫 렌더는 추정식으로 그리고, 실제 그려진 레전드 높이를 재서 한 번 보정한다
  // (plotly 의 줄바꿈 개수는 폰트 실측폭에 달려 있어 추정이 어긋날 수 있다).
  const [legendMeasured, setLegendMeasured] = useState(0);
  const legendMeasuredRef = useRef(0);
  const legendSig = `${splitValues.length}|${splitValues.join("|").length}|${boxW}`;
  useEffect(() => { legendMeasuredRef.current = 0; setLegendMeasured(0); }, [legendSig]);
  const onPlotRendered = (_fig, gd) => {
    const eff = gd?._fullLayout?.legend?._effHeight;
    if (!eff) return;
    const px = Math.round(eff) + 10;
    if (Math.abs(px - legendMeasuredRef.current) > 4) {
      legendMeasuredRef.current = px;
      setLegendMeasured(px);
    }
  };

  // ── 아래 여백 배분: x 눈금 라벨 → 레전드. 축 제목은 카드 헤더가 대신하므로
  // 그리지 않는다(세로 공간 절약). 레전드를 plotly 자동 배치에 맡기면 계열이
  // 스무 개쯤 될 때 x축 글자 위로 겹치므로 필요한 픽셀을 직접 계산한다.
  const geo = useMemo(() => {
    const maxTickLen = bins.reduce((m, b) => Math.max(m, String(b?.label ?? "").length), 0);
    const plotW0 = Math.max(320, (boxW || 1200) - 56 - 18);
    const labelW = maxTickLen * tickFont * GLYPH_W;
    let tickvals;
    let tickangle = 0;
    let tickPx = Math.round(tickFont * 1.9);
    if (axis === "step_desc") {
      // step_desc 축은 눈금 하나하나가 공정 번호("00.0")라 생략하면 읽을 수 없다.
      // 전부 찍되, 수평으로 안 들어가면 기울인다 — 라벨이 짧아 기울여도 높이 부담이 작다.
      tickvals = bins.map((b) => b.label);
      const pitch = plotW0 / Math.max(1, bins.length);
      if (pitch < labelW + 6) {
        const diagonal = pitch >= tickFont * 1.4; // 45도조차 안 들어가는 밀도면 세로로 세운다
        tickangle = diagonal ? -45 : -90;
        tickPx = Math.round(labelW * (diagonal ? 0.75 : 1) + tickFont + 6);
      }
    } else {
      // step_id 구간축 라벨은 "100000" 처럼 길어 90도로 눕히면 높이를 80px 씩 먹고
      // 읽히지도 않는다 — 구간 시작값이라 생략해도 읽히므로 들어갈 개수만 골라 찍는다.
      const labelPitch = labelW + 16;
      const maxLabels = Math.max(2, Math.floor(plotW0 / labelPitch));
      const tickStep = Math.max(1, Math.ceil(bins.length / maxLabels));
      tickvals = bins.filter((_, i) => i % tickStep === 0).map((b) => b.label);
    }
    const tickToLegend = 14;

    const n = splitValues.length;
    const legendFont = 11;
    const maxNameLen = splitValues.reduce((m, s) => Math.max(m, String(s).length), 0);
    // 이름이 다 들어가는 폭을 우선하고, 220px 를 넘길 때만 잘라 쓴다.
    const wantedEntry = Math.round(maxNameLen * legendFont * GLYPH_W) + 30;
    const entryPx = Math.max(60, Math.min(220, wantedEntry));
    const plotW = Math.max(320, (boxW || 1200) - 56 - 18);
    // 실측: 한 항목의 가로 피치 = entrywidth + itemwidth(30) + 여백(≈15).
    const perRow = Math.max(1, Math.floor(plotW / (entryPx + 45)));
    const legendRows = n ? Math.ceil(n / perRow) : 0;
    const legendEst = n ? legendRows * (legendFont + 8) + 8 : 0;
    const legendPx = legendMeasured > 0 ? Math.max(legendEst, legendMeasured) : legendEst;

    // 주어진 높이를 절대 넘기지 않는다 — 전체화면에서 한 화면에 들어와야 하므로
    // 공간이 부족하면 그림 영역을 줄이고(하한 120px) 레전드를 우선 살린다.
    const want = tickPx + tickToLegend + legendPx + 6;
    const totalH = baseH;
    const bottom = Math.round(Math.max(32, Math.min(want, totalH - 12 - 120)));
    const plotAreaH = Math.max(80, totalH - 12 - bottom);
    const legendY = -Math.min(0.98, (tickPx + tickToLegend) / plotAreaH);
    const nameChars = Math.max(8, Math.floor((entryPx - 26) / (legendFont * GLYPH_W)));

    // 막대 두께: 카테고리 한 칸의 픽셀 폭에서 상한을 넘지 않게 데이터 단위로 환산.
    const perCat = plotW / Math.max(1, bins.length);
    const capPx = bins.length <= 3 ? BAR_MAX_PX_SPARSE : BAR_MAX_PX;
    const barWidth = Math.min(0.72, capPx / Math.max(1, perCat));
    return { totalH, bottom, legendY, legendFont, entryPx, nameChars, barWidth, tickvals, tickangle };
  }, [bins, splitValues, boxW, baseH, tickFont, legendMeasured, axis]);

  const colorMap = useMemo(
    () => buildSeriesColors(splitValues, { dark, missingLabel: unassignedLabel, otherLabel }),
    [splitValues, dark, unassignedLabel, otherLabel],
  );

  const traces = useMemo(() => {
    const labels = bins.map((b) => b.label);
    return splitValues.map((sv) => {
      const full = String(sv);
      // 레전드 항목 폭을 넘는 긴 값은 잘라 표기 — 원값은 hover 와 표에 그대로 있다.
      const shown = full.length > geo.nameChars ? `${full.slice(0, geo.nameChars - 1)}…` : full;
      const counts = bins.map((b) => Number(b?.splits?.[sv] || 0));
      // 축이 %로 바뀌어도 hover 는 늘 원 수량과 구간 내 비중을 같이 보여준다.
      // [3]=split 원값, [4]=bin 키 — 클릭 드릴다운이 표시명(잘린 레전드)이 아니라 원값을 쓰기 위함.
      const cd = bins.map((b, i) => [counts[i], b.total ? (counts[i] / b.total) * 100 : 0, String(b.full ?? b.label ?? ""), full, String(b.bin ?? b.label ?? "")]);
      return {
        type: "bar",
        name: shown,
        x: labels,
        y: counts,
        customdata: cd,
        width: geo.barWidth,
        marker: {
          color: colorMap[sv] || MISSING_COLOR,
          // 테두리가 아니라 "표면색 간격" — 맞닿은 세그먼트를 흰 선이 가른다.
          line: { color: surface, width: SEGMENT_GAP },
        },
        hovertemplate: `<b>${full}</b><br>%{customdata[2]}<br>%{customdata[0]:,} wafer · 구간의 %{customdata[1]:.1f}%<extra></extra>`,
      };
    });
  }, [bins, splitValues, geo.nameChars, geo.barWidth, colorMap, surface]);

  if (!bins.length) {
    return (
      <div ref={boxRef} style={{ minHeight: Math.max(180, height), display: "flex", alignItems: "center", justifyContent: "center", color: muted, fontSize: 13 }}>
        표시할 WIP 데이터가 없습니다.
      </div>
    );
  }
  return (
    <div ref={boxRef} style={{ width: "100%", height: geo.totalH }}>
      <Plot
        data={traces}
        layout={{
          autosize: true,
          height: geo.totalH,
          barmode: "stack",
          barnorm: norm === "percent" ? "percent" : "",
          bargap: 0.12,
          paper_bgcolor: surface,
          plot_bgcolor: surface,
          font: { family: "system-ui, -apple-system, 'Segoe UI', sans-serif", color: muted },
          // autoexpand 를 끄면 plotly 가 레전드에 맞춰 여백을 다시 부풀리지 않는다
          // — 아래 여백/레전드 y 를 여기서 계산한 값 그대로 쓰기 위함.
          margin: { l: 56, r: 18, t: 12, b: geo.bottom, autoexpand: false },
          hovermode: "closest",
          hoverlabel: {
            bgcolor: surface,
            bordercolor: hairline,
            font: { size: 12, color: ink, family: "system-ui, -apple-system, 'Segoe UI', sans-serif" },
          },
          xaxis: {
            type: "category",
            showgrid: false,
            showline: true,
            linecolor: hairline,
            linewidth: 1,
            ticks: "",
            tickangle: geo.tickangle,
            tickmode: "array",
            tickvals: geo.tickvals,
            tickfont: { size: tickFont, color: muted },
            automargin: false,
          },
          yaxis: {
            // 값은 눈금이 읽어준다 — 축 제목/눈금선은 카드 헤더와 hairline 이 대신.
            gridcolor: hairline,
            gridwidth: 1,
            griddash: "solid",
            zeroline: false,
            showline: false,
            ticks: "",
            nticks: 5,
            separatethousands: true,
            tickfont: { size: 11, color: muted },
            automargin: false,
            rangemode: "tozero",
            ...(norm === "percent"
              ? { range: [0, 100], tickmode: "array", tickvals: [0, 25, 50, 75, 100], ticktext: ["0", "25", "50", "75", "100%"] }
              : {}),
          },
          legend: {
            orientation: "h",
            x: 0,
            xanchor: "left",
            y: geo.legendY,
            yanchor: "top",
            yref: "paper",
            font: { size: geo.legendFont, color: muted },
            entrywidth: geo.entryPx,
            entrywidthmode: "pixels",
            itemwidth: 30,
            bgcolor: "rgba(0,0,0,0)",
          },
          showlegend: splitValues.length > 1,
        }}
        config={{
          responsive: true,
          displaylogo: false,
          // 모드바는 hover 시에만, PNG 저장 하나만 남긴다 — 확대/올가미는 이 차트에 불필요.
          modeBarButtonsToRemove: ["lasso2d", "select2d", "zoom2d", "pan2d", "zoomIn2d", "zoomOut2d", "autoScale2d", "resetScale2d"],
        }}
        onInitialized={onPlotRendered}
        onUpdate={onPlotRendered}
        onClick={(event) => {
          if (!onSegmentClick) return;
          const cd = event?.points?.[0]?.customdata;
          if (!cd) return;
          const [, , binFull, sv, binKey] = cd;
          // 접힌 "기타"는 여러 원값의 합이라 특정할 수 없다 — 드릴다운 제외.
          if (otherLabel && sv === otherLabel) return;
          onSegmentClick({ bin: binKey, label: binFull, split: sv });
        }}
        useResizeHandler
        style={{ width: "100%", height: geo.totalH }}
      />
    </div>
  );
}
