import { useMemo } from "react";
import Plot from "react-plotly.js";
import { chartPalette } from "./UXKit";

const SERIES = chartPalette.series || ["#2563eb", "#dc2626", "#16a34a", "#9333ea", "#f59e0b", "#0891b2"];
const MISSING_COLOR = "#9ca3af";

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

function pointX(point) {
  return point?.x_label || point?.tkout_time || point?.time || point?.x;
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
  for (const key of ["label", "lot_wf", "root_lot_id", "lot_id", "wafer_id", "step_id", "n"]) {
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
  height = 360,
  dark = false,
  onPointClick = null,
  selectionKey = "",
}) {
  const points = Array.isArray(chart?.points) ? chart.points : [];
  const xLabel = cfg.x_label || chart?.x_label || cfg.x_col || chart?.x_col || "X";
  const yLabel = cfg.y_label || chart?.y_label || cfg.y_expr || chart?.y_col || "Y";
  const colorBy = cfg.color_by || chart?.color_by || "";
  const chartType = String(cfg.chart_type || chart?.chart_type || chart?.kind || "scatter").replace("dashboard_", "");
  const title = cfg.title || chart?.title || "";
  const markerSize = Number(cfg.point_size || chart?.render_preset?.point_size || 7);
  const fit = chart?.fit || chart?.fit_params || cfg.fit_params || null;
  const fitOk = fit && Number.isFinite(Number(fit.slope)) && Number.isFinite(Number(fit.intercept));
  const fitLabel = fitOk && Number.isFinite(Number(fit.r2)) ? `R²=${Number(fit.r2).toFixed(4)}` : "";
  const bg = dark ? "#111111" : "#ffffff";
  const fg = dark ? "#e5e7eb" : "#111827";
  const grid = dark ? "rgba(148,163,184,0.22)" : "rgba(15,23,42,0.12)";

  const { traces, legendCounts } = useMemo(() => {
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
      return {
        type: "scattergl",
        mode,
        name: colorBy ? `${name} (${rows.length})` : "data",
        x: rows.map((point) => numberOrValue(pointX(point))),
        y: rows.map((point) => numberOrValue(pointY(point))),
        text: rows.map((point) => hoverLines(point, xLabel, yLabel, colorBy)),
        hoverinfo: "text",
        customdata: rows,
        marker: {
          size: markerSize,
          color: missing ? MISSING_COLOR : SERIES[idx % SERIES.length],
          opacity: missing ? 0.58 : 0.82,
          line: { color: dark ? "#111111" : "#ffffff", width: 0.6 },
        },
        line: { color: missing ? MISSING_COLOR : SERIES[idx % SERIES.length], width: 1.6 },
      };
    });
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
    return {
      traces: plotTraces,
      legendCounts: entries.map(([name, rows]) => ({ name, count: rows.length })),
    };
  }, [points, colorBy, chartType, xLabel, yLabel, markerSize, dark, fitOk, fit, fitLabel]);

  if (!points.length) {
    return <div style={{ minHeight: Math.max(180, height), display: "flex", alignItems: "center", justifyContent: "center", color: dark ? "#9ca3af" : "#64748b", fontSize: 14 }}>차트로 표시할 point가 없습니다.</div>;
  }

  return (
    <div style={{ width: "100%", minHeight: Math.max(220, height), position: "relative" }}>
      <Plot
        data={traces}
        layout={{
          title: title ? { text: title, font: { size: 18, color: fg } } : undefined,
          autosize: true,
          height: Math.max(240, height),
          paper_bgcolor: bg,
          plot_bgcolor: bg,
          margin: { l: 64, r: 22, t: title ? 48 : 20, b: 56 },
          hovermode: "closest",
          dragmode: "pan",
          xaxis: {
            title: { text: xLabel, font: { size: 13, color: fg } },
            gridcolor: grid,
            zerolinecolor: grid,
            color: fg,
            automargin: true,
          },
          yaxis: {
            title: { text: yLabel, font: { size: 13, color: fg } },
            gridcolor: grid,
            zerolinecolor: grid,
            color: fg,
            automargin: true,
          },
          legend: {
            orientation: "h",
            y: -0.22,
            x: 0,
            font: { size: 11, color: fg },
          },
          showlegend: colorBy ? true : legendCounts.length > 1,
          annotations: fitLabel ? [{
            xref: "paper",
            yref: "paper",
            x: 1,
            y: 1.08,
            xanchor: "right",
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
          scrollZoom: true,
          modeBarButtonsToRemove: ["lasso2d", "select2d"],
        }}
        useResizeHandler
        style={{ width: "100%", height: Math.max(240, height) }}
        onClick={(event) => {
          const point = event?.points?.[0]?.customdata;
          if (point && onPointClick) onPointClick(point, selectionKey);
        }}
      />
    </div>
  );
}
