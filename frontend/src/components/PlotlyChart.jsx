import { useEffect, useMemo, useRef, useState } from "react";
import Plot from "react-plotly.js";
import { chartPalette, buildSeriesColors } from "./UXKit";

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
  const groups = Array.isArray(chart?.groups) ? chart.groups : Array.isArray(chart?.slices) ? chart.slices : [];
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

  const { traces, legendCounts, categorical } = useMemo(() => {
    if ((chartType === "pie" || chartType === "donut") && groups.length) {
      return {
        categorical: true,
        legendCounts: groups.map((row) => ({ name: text(row?.label || row?.group || row?.value), count: Number(row?.count ?? row?.value ?? 0) })),
        traces: [{
          type: "pie",
          hole: chartType === "donut" ? 0.46 : 0,
          labels: groups.map((row) => text(row?.label || row?.group || "(empty)")),
          values: groups.map((row) => Number(row?.count ?? row?.value ?? 0)),
          textinfo: "label+percent",
          hovertemplate: "%{label}<br>count=%{value}<br>%{percent}<extra></extra>",
          marker: { colors: groups.map((_, idx) => SERIES[idx % SERIES.length]) },
          sort: false,
        }],
      };
    }
    if (chartType === "bar" && groups.length && !points.length) {
      return {
        categorical: true,
        legendCounts: groups.map((row) => ({ name: text(row?.label || row?.group || row?.value), count: Number(row?.count ?? row?.value ?? 0) })),
        traces: [{
          type: "bar",
          name: yLabel || "count",
          x: groups.map((row) => text(row?.label || row?.group || "(empty)")),
          y: groups.map((row) => Number(row?.count ?? row?.value ?? 0)),
          text: groups.map((row) => row?.percent != null ? `${row.percent}%` : ""),
          textposition: "auto",
          hovertemplate: `${xLabel}: %{x}<br>${yLabel}: %{y}<extra></extra>`,
          marker: { color: groups.map((_, idx) => SERIES[idx % SERIES.length]), opacity: 0.86 },
        }],
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
    };
  }, [points, groups, colorBy, chartType, xLabel, yLabel, markerSize, dark, fitOk, fit, fitLabel]);

  if (!points.length && !groups.length) {
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
          xaxis: categorical && (chartType === "pie" || chartType === "donut") ? undefined : {
            title: { text: xLabel, font: { size: 13, color: fg } },
            gridcolor: grid,
            zerolinecolor: grid,
            color: fg,
            automargin: true,
          },
          yaxis: categorical && (chartType === "pie" || chartType === "donut") ? undefined : {
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

// 컨테이너 실제 폭 관측 — 레전드가 몇 줄로 접히는지 계산하려면 픽셀 폭이 필요하다.
function useBoxWidth() {
  const ref = useRef(null);
  const [w, setW] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const upd = () => setW(el.clientWidth || 0);
    upd();
    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", upd);
      return () => window.removeEventListener("resize", upd);
    }
    const ro = new ResizeObserver(upd);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
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
}) {
  // 표면/잉크는 앱 테마 토큰과 같은 값 — 차트가 카드 위에 얹힌 것처럼 보이게.
  const surface = dark ? "#262626" : "#ffffff";
  const ink = dark ? "#e5e5e5" : "#171717";
  const muted = dark ? "#a3a3a3" : "#737373";
  const hairline = dark ? "#333333" : "#ececec";
  const [boxRef, boxW] = useBoxWidth();

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
    // bin 이 50~80개여도 라벨은 눕히지 않는다 — 세로로 눕힌 90도 라벨은 높이를
    // 80px 씩 먹고 읽히지도 않는다. 대신 들어갈 수 있는 개수만 골라 찍는다.
    const labelPitch = maxTickLen * tickFont * GLYPH_W + 16;
    const maxLabels = Math.max(2, Math.floor(plotW0 / labelPitch));
    const tickStep = Math.max(1, Math.ceil(bins.length / maxLabels));
    const tickvals = bins.filter((_, i) => i % tickStep === 0).map((b) => b.label);
    const tickPx = Math.round(tickFont * 1.9);
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
    return { totalH, bottom, legendY, legendFont, entryPx, nameChars, barWidth, tickvals };
  }, [bins, splitValues, boxW, baseH, tickFont, legendMeasured]);

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
      const cd = bins.map((b, i) => [counts[i], b.total ? (counts[i] / b.total) * 100 : 0, String(b.full ?? b.label ?? "")]);
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
            tickangle: 0,
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
        useResizeHandler
        style={{ width: "100%", height: geo.totalH }}
      />
    </div>
  );
}
