import { useMemo, useState } from "react";
import { postJson } from "../lib/api";

const boxStyle = {
  border: "1px solid var(--border)",
  borderRadius: 8,
  background: "var(--bg-secondary)",
  overflow: "hidden",
};

const inputStyle = {
  width: "100%",
  minHeight: 38,
  padding: "8px 10px",
  borderRadius: 6,
  border: "1px solid var(--border)",
  background: "var(--bg-primary)",
  color: "var(--text-primary)",
  fontSize: 14,
  lineHeight: 1.45,
  resize: "vertical",
  outline: "none",
  fontFamily: "inherit",
};

const buttonStyle = {
  minHeight: 38,
  padding: "0 14px",
  borderRadius: 6,
  border: "none",
  background: "var(--accent)",
  color: "#fff",
  fontSize: 14,
  fontWeight: 800,
  cursor: "pointer",
  whiteSpace: "nowrap",
};

const ghostButtonStyle = {
  ...buttonStyle,
  border: "1px solid var(--border)",
  background: "transparent",
  color: "var(--text-secondary)",
};

function compactValue(value) {
  if (value == null) return "";
  if (typeof value === "number") return Number.isFinite(value) ? String(Math.round(value * 10000) / 10000) : "";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function firstArray(...values) {
  return values.find((value) => Array.isArray(value) && value.length > 0) || [];
}

function columnKey(column) {
  if (typeof column === "string") return column;
  if (column && typeof column === "object") {
    return String(column.key || column.field || column.name || column.id || "").trim();
  }
  return String(column || "").trim();
}

function normalizeTable(tool) {
  const blocks = Array.isArray(tool?.blocks) ? tool.blocks : [];
  const tableBlock = blocks.find((block) => ["lot_table", "table"].includes(block?.kind));
  const source = tableBlock?.payload || tool?.table || tool?.split_view?.table || {};
  const rows = firstArray(source.rows, source.data, tableBlock?.rows, tool?.rows);
  if (!rows.length) return null;
  const columns = firstArray(source.columns, tableBlock?.columns);
  const inferred = Object.keys(rows.find((row) => row && typeof row === "object" && !Array.isArray(row)) || {});
  const normalizedColumns = (columns.length ? columns : inferred).map(columnKey).filter(Boolean);
  return {
    title: tableBlock?.title || source.title || tool?.table?.title || "표",
    columns: normalizedColumns.slice(0, 6),
    rows: rows.slice(0, 5),
    total: source.total || rows.length,
  };
}

function finiteNumber(value) {
  const direct = Number(value);
  if (Number.isFinite(direct)) return direct;
  if (typeof value === "string") {
    const parsed = Date.parse(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

function pointFromRow(row, index = 0) {
  if (!row || typeof row !== "object") return null;
  const preferred = [
    [row.x, row.y],
    [row.x_value, row.y_value],
    [row.date, row.value],
    [row.timestamp, row.value],
    [row.step_seq, row.value],
  ];
  for (const [x, y] of preferred) {
    const nx = finiteNumber(x);
    const ny = finiteNumber(y);
    if (Number.isFinite(ny)) return { x: Number.isFinite(nx) ? nx : index, y: ny };
  }
  const numeric = Object.values(row).map(finiteNumber).filter(Number.isFinite);
  return numeric.length >= 2 ? { x: numeric[0], y: numeric[1] } : null;
}

function normalizeChart(tool) {
  const blocks = Array.isArray(tool?.blocks) ? tool.blocks : [];
  const chartBlock = blocks.find((block) => String(block?.kind || "").startsWith("chart_"));
  const payload = chartBlock?.payload || tool?.chart_result || tool?.chart || {};
  const rawPoints = firstArray(payload.points, payload.rows, payload.data);
  const points = rawPoints.map(pointFromRow).filter(Boolean).slice(0, 80);
  if (points.length >= 2) {
    return {
      title: chartBlock?.title || payload.title || "차트",
      type: chartBlock?.kind || "chart",
      points,
      corr: payload.corr ?? payload.correlation ?? null,
    };
  }
  const series = firstArray(payload.series).flatMap((item) => firstArray(item?.points, item?.data).map(pointFromRow).filter(Boolean));
  if (series.length >= 2) {
    return {
      title: chartBlock?.title || payload.title || "차트",
      type: chartBlock?.kind || "chart_trend",
      points: series.slice(0, 80),
      corr: null,
    };
  }
  return null;
}

function FlowiMiniChart({ chart }) {
  if (!chart?.points?.length) return null;
  const width = 260;
  const height = 92;
  const pad = 10;
  const xs = chart.points.map((p) => p.x);
  const ys = chart.points.map((p) => p.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const sx = (value) => pad + ((value - minX) / (maxX - minX || 1)) * (width - pad * 2);
  const sy = (value) => height - pad - ((value - minY) / (maxY - minY || 1)) * (height - pad * 2);
  const line = chart.points.map((point) => `${sx(point.x)},${sy(point.y)}`).join(" ");
  const isTrend = String(chart.type || "").includes("trend");
  return (
    <div style={{ marginTop: 8, border: "1px solid var(--border)", borderRadius: 6, background: "var(--bg-primary)", padding: 8 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
        <span style={{ fontSize: 14, fontWeight: 800, color: "var(--text-primary)", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{chart.title}</span>
        <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--text-secondary)", fontFamily: "monospace" }}>
          {chart.points.length} pts{chart.corr != null ? ` · corr ${compactValue(chart.corr)}` : ""}
        </span>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={chart.title} style={{ width: "100%", height: 92, display: "block" }}>
        <rect x="0" y="0" width={width} height={height} fill="transparent" />
        {isTrend && <polyline points={line} fill="none" stroke="var(--accent)" strokeWidth="2" opacity="0.85" />}
        {chart.points.map((point, idx) => (
          <circle key={idx} cx={sx(point.x)} cy={sy(point.y)} r={isTrend ? 2.2 : 2.8} fill={isTrend ? "var(--accent)" : "var(--info, var(--accent))"} opacity="0.9" />
        ))}
      </svg>
    </div>
  );
}

function FlowiMiniTable({ table }) {
  if (!table?.rows?.length || !table?.columns?.length) return null;
  return (
    <div style={{ marginTop: 8, border: "1px solid var(--border)", borderRadius: 6, overflow: "hidden", background: "var(--bg-primary)" }}>
      <div style={{ padding: "6px 8px", borderBottom: "1px solid var(--border)", display: "flex", gap: 8, alignItems: "center" }}>
        <span style={{ fontSize: 14, fontWeight: 800, color: "var(--text-primary)" }}>{table.title}</span>
        <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--text-secondary)", fontFamily: "monospace" }}>{table.total} rows</span>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr>
              {table.columns.map((col) => (
                <th key={col} style={{ padding: "5px 7px", borderBottom: "1px solid var(--border)", textAlign: "left", color: "var(--text-secondary)", fontFamily: "monospace", whiteSpace: "nowrap" }}>{col}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((row, idx) => (
              <tr key={idx}>
                {table.columns.map((col) => (
                  <td key={col} title={compactValue(row?.[col])} style={{ padding: "5px 7px", borderTop: idx ? "1px solid var(--border)" : "none", color: "var(--text-primary)", fontFamily: "monospace", maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {compactValue(row?.[col])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function FlowiCompactResult({ result }) {
  const tool = result?.tool || {};
  const table = useMemo(() => normalizeTable(tool), [tool]);
  const chart = useMemo(() => normalizeChart(tool), [tool]);
  const blocks = Array.isArray(tool?.blocks) ? tool.blocks : [];
  const goHome = () => {
    window.dispatchEvent(new CustomEvent("flow:navigate", { detail: { tab: "home" } }));
  };
  return (
    <div style={{ padding: 10, borderTop: "1px solid var(--border)", background: "var(--bg-card, var(--bg-secondary))" }}>
      <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap", marginBottom: result?.answer ? 8 : 0 }}>
        <span style={{ fontSize: 13, fontWeight: 900, color: "var(--accent)" }}>{tool?.feature || tool?.intent || "flowi"}</span>
        {blocks.slice(0, 4).map((block, idx) => (
          <span key={`${block?.kind || "block"}-${idx}`} style={{ fontSize: 12, color: "var(--text-secondary)", border: "1px solid var(--border)", borderRadius: 999, padding: "2px 7px", fontFamily: "monospace" }}>
            {block?.kind || "block"}
          </span>
        ))}
        <button type="button" onClick={goHome} style={{ ...ghostButtonStyle, minHeight: 24, padding: "0 8px", marginLeft: "auto", fontSize: 12 }}>
          Home
        </button>
      </div>
      {result?.answer && (
        <div style={{ fontSize: 14, lineHeight: 1.55, whiteSpace: "pre-wrap", color: "var(--text-primary)" }}>
          {result.answer}
        </div>
      )}
      <FlowiMiniTable table={table} />
      <FlowiMiniChart chart={chart} />
      {result?.llm?.error && (
        <div style={{ marginTop: 8, fontSize: 12, color: "var(--text-secondary)" }}>LLM 확인 실패: {result.llm.error}</div>
      )}
    </div>
  );
}

export default function FlowiPromptBox({
  defaultScope = {},
  placeholder = "Flow-i 질문",
  product = "",
  maxRows = 8,
  compact = true,
  onResult,
}) {
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const disabled = busy || !prompt.trim();

  const submit = (event) => {
    event?.preventDefault?.();
    const q = prompt.trim();
    if (!q || busy) return;
    setBusy(true);
    setError("");
    postJson("/api/llm/flowi/chat", {
      prompt: q,
      product,
      max_rows: maxRows,
      context: {
        type: "page_flowi_prompt",
        default_scope: defaultScope,
        source_page: defaultScope?.kind || "",
      },
    })
      .then((data) => {
        setResult(data);
        if (onResult) onResult(data);
      })
      .catch((err) => setError(err?.message || String(err)))
      .finally(() => setBusy(false));
  };

  return (
    <section style={boxStyle}>
      <form onSubmit={submit} style={{ padding: 10, display: "grid", gridTemplateColumns: "minmax(180px, 1fr) auto", gap: 8, alignItems: "start" }}>
        <textarea
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          onKeyDown={(event) => {
            if ((event.ctrlKey || event.metaKey) && event.key === "Enter") submit(event);
          }}
          rows={1}
          spellCheck={false}
          placeholder={placeholder}
          style={inputStyle}
        />
        <button type="submit" disabled={disabled} style={{ ...buttonStyle, opacity: disabled ? 0.55 : 1, cursor: disabled ? "default" : "pointer" }}>
          {busy ? "처리 중" : "전송"}
        </button>
      </form>
      {error && <div style={{ padding: "0 10px 10px", fontSize: 13, color: "var(--danger)" }}>{error}</div>}
      {compact && result && <FlowiCompactResult result={result} />}
    </section>
  );
}
