import { useEffect, useMemo, useState } from "react";
import Loading from "../components/Loading";
import { uiLabel } from "../components/UXKit";
import { dl, sf, qs } from "../lib/api";

const API = "/api/ettime";

const ctl = {
  padding: "7px 10px",
  borderRadius: 8,
  border: "1px solid var(--border)",
  background: "var(--bg-card)",
  color: "var(--text-primary)",
  fontSize: 14,
  outline: "none",
};

const card = {
  border: "1px solid var(--border)",
  borderRadius: 8,
  background: "var(--bg-secondary)",
  minWidth: 0,
};

function fmt(v, digits = 4) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "-";
  if (Math.abs(n) >= 100) return n.toFixed(2);
  if (Math.abs(n) >= 1) return n.toFixed(digits);
  return n.toPrecision(4);
}

function tone(status) {
  const s = String(status || "").toLowerCase();
  if (s === "critical" || s === "abnormal") return "#dc2626";
  if (s === "warn" || s === "missing") return "#f97316";
  return "#0f766e";
}

function Mini({ label, value, color = "var(--accent)" }) {
  return (
    <div style={{ ...card, padding: "10px 12px", background: "var(--bg-card)" }}>
      <div style={{ fontSize: 14, color: "var(--text-secondary)" }}>{uiLabel(label)}</div>
      <div style={{ marginTop: 4, fontSize: 18, fontWeight: 800, color, fontFamily: "monospace" }}>{value ?? "-"}</div>
    </div>
  );
}

function Panel({ title, right, children, style }) {
  return (
    <div style={{ ...card, padding: 12, ...style }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, marginBottom: 10 }}>
        <div style={{ fontSize: 14, fontWeight: 800, color: "var(--accent)" }}>{uiLabel(title)}</div>
        {right}
      </div>
      {children}
    </div>
  );
}

function Table({ rows, columns, selected, rowKey, onRow }) {
  if (!rows?.length) {
    return <div style={{ padding: 24, textAlign: "center", fontSize: 14, color: "var(--text-secondary)" }}>데이터 없음</div>;
  }
  return (
    <div style={{ overflow: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} style={{ textAlign: c.align || "left", padding: "7px 8px", borderBottom: "1px solid var(--border)", color: "var(--text-secondary)", fontWeight: 700, whiteSpace: "nowrap" }}>
                {uiLabel(c.label)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, idx) => {
            const active = selected && rowKey && String(row[rowKey] || "") === String(selected);
            return (
              <tr
                key={idx}
                onClick={onRow ? () => onRow(row) : undefined}
                style={{ cursor: onRow ? "pointer" : "default", background: active ? "var(--accent-glow)" : "transparent", borderBottom: "1px solid var(--border)" }}
              >
                {columns.map((c) => (
                  <td key={c.key} style={{ padding: "7px 8px", textAlign: c.align || "left", whiteSpace: c.nowrap ? "nowrap" : "normal", fontFamily: c.mono ? "monospace" : "inherit", color: c.color ? c.color(row[c.key], row) : "var(--text-primary)" }}>
                    {c.render ? c.render(row[c.key], row) : row[c.key] ?? ""}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function extent(vals, pad = 0.08) {
  const nums = vals.map(Number).filter(Number.isFinite);
  if (!nums.length) return [0, 1];
  let lo = Math.min(...nums);
  let hi = Math.max(...nums);
  if (lo === hi) {
    lo -= 1;
    hi += 1;
  }
  const p = (hi - lo) * pad;
  return [lo - p, hi + p];
}

function SvgFrame({ title, children }) {
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 8, background: "var(--bg-card)", overflow: "hidden" }}>
      <div style={{ padding: "7px 9px", borderBottom: "1px solid var(--border)", fontSize: 14, fontWeight: 800, color: "var(--text-secondary)" }}>{uiLabel(title)}</div>
      {children}
    </div>
  );
}

function TrendPlot({ rows }) {
  const pts = (rows || []).filter((r) => Number.isFinite(Number(r.mean)));
  const [lo, hi] = extent(pts.map((r) => r.mean));
  const W = 360, H = 150, p = 22;
  const x = (i) => p + (pts.length <= 1 ? (W - p * 2) / 2 : (i / (pts.length - 1)) * (W - p * 2));
  const y = (v) => H - p - ((Number(v) - lo) / (hi - lo)) * (H - p * 2);
  const d = pts.map((r, i) => `${i ? "L" : "M"}${x(i)},${y(r.mean)}`).join(" ");
  return (
    <SvgFrame title="Trend">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height="150">
        <rect x="0" y="0" width={W} height={H} fill="transparent" />
        <line x1={p} y1={H - p} x2={W - p} y2={H - p} stroke="var(--border)" />
        <line x1={p} y1={p} x2={p} y2={H - p} stroke="var(--border)" />
        {d && <path d={d} fill="none" stroke="#0f766e" strokeWidth="2" />}
        {pts.map((r, i) => <circle key={i} cx={x(i)} cy={y(r.mean)} r="2.6" fill="#0f766e" />)}
        <text x={p + 2} y={p - 6} fontSize="9" fill="var(--text-secondary)" style={{ fontFamily: "monospace" }}>{fmt(hi, 3)}</text>
        <text x={p + 2} y={H - 6} fontSize="9" fill="var(--text-secondary)" style={{ fontFamily: "monospace" }}>{fmt(lo, 3)}</text>
      </svg>
    </SvgFrame>
  );
}

function ScatterPlot({ title, rows, xKey, yKey, valueKey = "value" }) {
  const pts = (rows || []).filter((r) => Number.isFinite(Number(r[xKey])) && Number.isFinite(Number(r[yKey])));
  const [xLo, xHi] = extent(pts.map((r) => r[xKey]), 0.12);
  const [yLo, yHi] = extent(pts.map((r) => r[yKey]), 0.12);
  const [vLo, vHi] = extent(pts.map((r) => r[valueKey]), 0.02);
  const W = 360, H = 150, p = 22;
  const sx = (v) => p + ((Number(v) - xLo) / (xHi - xLo)) * (W - p * 2);
  const sy = (v) => H - p - ((Number(v) - yLo) / (yHi - yLo)) * (H - p * 2);
  return (
    <SvgFrame title={title}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height="150">
        <line x1={p} y1={H - p} x2={W - p} y2={H - p} stroke="var(--border)" />
        <line x1={p} y1={p} x2={p} y2={H - p} stroke="var(--border)" />
        {pts.slice(0, 240).map((r, i) => {
          const t = (Number(r[valueKey]) - vLo) / Math.max(1e-9, vHi - vLo);
          const color = t > 0.66 ? "#f97316" : t > 0.33 ? "#2563eb" : "#0f766e";
          return <circle key={i} cx={sx(r[xKey])} cy={sy(r[yKey])} r="2.8" fill={color} opacity="0.82" />;
        })}
      </svg>
    </SvgFrame>
  );
}

function CdfPlot({ rows }) {
  const pts = (rows || []).filter((r) => Number.isFinite(Number(r.x)) && Number.isFinite(Number(r.p)));
  const [lo, hi] = extent(pts.map((r) => r.x));
  const W = 360, H = 150, p = 22;
  const sx = (v) => p + ((Number(v) - lo) / (hi - lo)) * (W - p * 2);
  const sy = (v) => H - p - Number(v) * (H - p * 2);
  const d = pts.map((r, i) => `${i ? "L" : "M"}${sx(r.x)},${sy(r.p)}`).join(" ");
  return (
    <SvgFrame title="Cumulative Plot">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height="150">
        <line x1={p} y1={H - p} x2={W - p} y2={H - p} stroke="var(--border)" />
        <line x1={p} y1={p} x2={p} y2={H - p} stroke="var(--border)" />
        {d && <path d={d} fill="none" stroke="#f97316" strokeWidth="2" />}
      </svg>
    </SvgFrame>
  );
}

function BoxSummary({ item }) {
  const s = item?.stats || {};
  const [lo, hi] = extent([s.min, s.q1, s.median, s.q3, s.max, item?.lsl, item?.usl]);
  const W = 360, H = 150, p = 28;
  const sx = (v) => p + ((Number(v) - lo) / (hi - lo)) * (W - p * 2);
  const y = H / 2;
  return (
    <SvgFrame title="Box Table">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height="150">
        <line x1={sx(s.min)} y1={y} x2={sx(s.max)} y2={y} stroke="#64748b" strokeWidth="2" />
        <rect x={sx(s.q1)} y={y - 20} width={Math.max(3, sx(s.q3) - sx(s.q1))} height="40" fill="rgba(37,99,235,0.14)" stroke="#2563eb" />
        <line x1={sx(s.median)} y1={y - 25} x2={sx(s.median)} y2={y + 25} stroke="#f97316" strokeWidth="3" />
        {Number.isFinite(Number(item?.lsl)) && <line x1={sx(item.lsl)} y1="22" x2={sx(item.lsl)} y2={H - 22} stroke="#dc2626" strokeDasharray="4,3" />}
        {Number.isFinite(Number(item?.usl)) && <line x1={sx(item.usl)} y1="22" x2={sx(item.usl)} y2={H - 22} stroke="#dc2626" strokeDasharray="4,3" />}
        <text x="18" y={H - 12} fontSize="9" fill="var(--text-secondary)" style={{ fontFamily: "monospace" }}>min {fmt(s.min)} / med {fmt(s.median)} / max {fmt(s.max)}</text>
      </svg>
    </SvgFrame>
  );
}

function StatsTable({ item }) {
  const s = item?.stats || {};
  const rows = [
    ["N", item?.n],
    ["Mean", fmt(s.mean)],
    ["Std", fmt(s.std)],
    ["Min", fmt(s.min)],
    ["Q1", fmt(s.q1)],
    ["Median", fmt(s.median)],
    ["Q3", fmt(s.q3)],
    ["Max", fmt(s.max)],
    ["P95", fmt(s.p95)],
  ];
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 8, overflow: "hidden", background: "var(--bg-card)" }}>
      <div style={{ padding: "7px 9px", borderBottom: "1px solid var(--border)", fontSize: 14, fontWeight: 800, color: "var(--text-secondary)" }}>{uiLabel("Statistical Table")}</div>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
        <tbody>
          {rows.map((r) => (
            <tr key={r[0]}>
              <td style={{ padding: "6px 9px", borderBottom: "1px solid var(--border)", color: "var(--text-secondary)", fontFamily: "monospace" }}>{r[0]}</td>
              <td style={{ padding: "6px 9px", borderBottom: "1px solid var(--border)", textAlign: "right", fontWeight: r[0] === "Mean" ? 800 : 500, fontFamily: "monospace" }}>{r[1]}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function My_ETTime() {
  const [products, setProducts] = useState([]);
  const [product, setProduct] = useState("");
  const [rootLotId, setRootLotId] = useState("");
  const [fabLotId, setFabLotId] = useState("");
  const [stepId, setStepId] = useState("");
  const [lotSearch, setLotSearch] = useState("");
  const [report, setReport] = useState(null);
  const [lots, setLots] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedPackage, setSelectedPackage] = useState("");
  const [err, setErr] = useState("");

  useEffect(() => {
    sf(API + "/products")
      .then((d) => {
        const arr = d.products || [];
        setProducts(arr);
        if (arr[0]) setProduct(arr[0]);
      })
      .finally(() => setLoading(false));
  }, []);

  const load = (overrides = {}) => {
    const params = {
      product,
      root_lot_id: rootLotId.trim(),
      fab_lot_id: fabLotId.trim(),
      step_id: stepId.trim(),
      limit: 500,
      ...overrides,
    };
    setErr("");
    setLoading(true);
    return Promise.all([
      sf(API + "/report" + qs(params)),
      sf(API + "/lots" + qs({ product: params.product, search: lotSearch.trim(), limit: 200 })),
    ])
      .then(([r, l]) => {
        setReport(r);
        setLots(l?.lots || []);
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    if (product) load({ product });
  }, [product]);

  useEffect(() => {
    const firstPkg = report?.recent_packages?.[0]?.package_key || "";
    setSelectedPackage(firstPkg);
  }, [report?.summary?.latest_package_time, report?.summary?.packages]);

  const summary = report?.summary || {};
  const activePackage = (report?.recent_packages || []).find((x) => String(x.package_key || "") === String(selectedPackage)) || null;
  const activeDetail = (report?.report_details || {})[selectedPackage] || null;

  const pptxUrl = (row = {}) => API + "/report/pptx" + qs({
    product: row.product || product,
    root_lot_id: row.root_lot_id || rootLotId.trim(),
    fab_lot_id: row.fab_lot_id || fabLotId.trim(),
    step_id: row.step_id || stepId.trim(),
    package_key: row.package_key || activePackage?.package_key || "",
    max_items: 30,
  });
  const downloadPptx = (row = {}) => {
    const target = row || {};
    const name = `ET_Report_${target.root_lot_id || rootLotId || product || "lot"}.pptx`;
    setErr("");
    return dl(pptxUrl(target), name).catch((e) => setErr(e.message || String(e) || "PPTX 다운로드 실패"));
  };

  if (loading && !report) {
    return <div style={{ padding: 40, textAlign: "center" }}><Loading text="ET 레포트 로딩 중..." /></div>;
  }

  return (
    <div style={{ padding: "14px 16px", background: "var(--bg-primary)", minHeight: "calc(100vh - 52px)", color: "var(--text-primary)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, marginBottom: 12, flexWrap: "wrap" }}>
        <div style={{ fontSize: 14, fontWeight: 700, color: "var(--text-secondary)" }}>ET 레포트</div>
        <div style={{ display: "flex", gap: 7, flexWrap: "wrap", alignItems: "center" }}>
          <select value={product} onChange={(e) => setProduct(e.target.value)} style={{ ...ctl, minWidth: 150 }}>
            {products.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
          <input value={rootLotId} onChange={(e) => setRootLotId(e.target.value)} placeholder="root_lot_id" style={ctl} />
          <input value={fabLotId} onChange={(e) => setFabLotId(e.target.value)} placeholder="fab_lot_id" style={ctl} />
          <input value={stepId} onChange={(e) => setStepId(e.target.value)} placeholder="step_id / M1DC" style={ctl} />
          <button onClick={() => load()} style={{ ...ctl, border: "none", background: "var(--accent)", color: "#fff", fontWeight: 800, cursor: "pointer" }}>갱신</button>
          <button
            onClick={() => {
              const sample = { product: "PRODA0", root_lot_id: "A0001", fab_lot_id: "", step_id: "ETA100010", metric: "VTH", limit: 500 };
              setProduct(sample.product);
              setRootLotId(sample.root_lot_id);
              setFabLotId(sample.fab_lot_id);
              setStepId(sample.step_id);
              setLotSearch(sample.root_lot_id);
              load(sample);
            }}
            style={{ ...ctl, border: "1px solid var(--accent)", color: "var(--accent)", fontWeight: 800, cursor: "pointer" }}
            title="PRODA0 / A0001 / ETA100010 예시"
          >
            A0001 예시
          </button>
          <button onClick={() => downloadPptx(activePackage || {})} style={{ ...ctl, border: "1px solid #7c3aed", color: "#6d28d9", fontWeight: 800, cursor: "pointer" }}>PPTX</button>
        </div>
      </div>
      {err && (
        <div style={{ marginBottom: 10, padding: "8px 10px", border: "1px solid #ef444466", background: "#ef444422", color: "#ef4444", fontSize: 14, borderRadius: 4 }}>
          {err}
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(120px, 1fr))", gap: 8, marginBottom: 10 }}>
        <Mini label="ET 측정이력" value={summary.packages || 0} />
        <Mini label="랏" value={summary.lots || 0} color="#2563eb" />
        <Mini label="최신 측정" value={summary.latest_package_time || "-"} color="#0f766e" />
        <Mini label="Step Seq" value={summary.seq_count || 0} color="#7c3aed" />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "320px minmax(0, 1fr)", gap: 10, alignItems: "start" }}>
        <Panel
          title="Lot Search"
          right={<button onClick={() => load()} style={{ ...ctl, padding: "5px 8px", cursor: "pointer" }}>검색</button>}
          style={{ position: "sticky", top: 8, maxHeight: "calc(100vh - 170px)", overflow: "hidden" }}
        >
          <input value={lotSearch} onChange={(e) => setLotSearch(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") load(); }} placeholder="PROD / root / fab lot" style={{ ...ctl, width: "100%", boxSizing: "border-box", marginBottom: 8 }} />
          <div style={{ maxHeight: "calc(100vh - 245px)", overflow: "auto" }}>
            <Table
              rows={lots}
              selected={rootLotId}
              rowKey="root_lot_id"
              onRow={(r) => {
                setRootLotId(r.root_lot_id || "");
                setFabLotId(r.fab_lot_id || "");
                setStepId((r.steps || [])[0] || r.step_range || "");
                load({ root_lot_id: r.root_lot_id || "", fab_lot_id: r.fab_lot_id || "", step_id: (r.steps || [])[0] || r.step_range || "" });
              }}
              columns={[
                { key: "root_lot_id", label: "Root", mono: true, nowrap: true },
                { key: "fab_lot_id", label: "Fab", mono: true, nowrap: true },
                { key: "last_measured_at", label: "Time", mono: true, nowrap: true },
              ]}
            />
          </div>
        </Panel>

        <div style={{ display: "grid", gap: 10, minWidth: 0 }}>
          <Panel title="ET 측정이력" right={<span style={{ fontSize: 14, color: "var(--text-secondary)", fontFamily: "monospace" }}>{activePackage?.package_time || ""}</span>}>
            <Table
              rows={report?.recent_packages || []}
              selected={selectedPackage}
              rowKey="package_key"
              onRow={(r) => {
                setSelectedPackage(r.package_key || "");
                setRootLotId(r.root_lot_id || "");
                setFabLotId(r.fab_lot_id || "");
                setStepId(r.step_id || "");
                setLotSearch(r.fab_lot_id || r.root_lot_id || "");
              }}
              columns={[
                { key: "product", label: "PROD", mono: true, nowrap: true },
                { key: "root_lot_id", label: "Root Lot", mono: true, nowrap: true },
                { key: "fab_lot_id", label: "Fab Lot", mono: true, nowrap: true },
                { key: "step_id", label: "ET Step", mono: true, nowrap: true },
                { key: "step_seq_points", label: "ET Measurement", mono: true },
                { key: "package_time", label: "Measured", mono: true, nowrap: true },
                { key: "wafer_count", label: "WF", mono: true, align: "right" },
                { key: "item_count", label: "Items", mono: true, align: "right" },
                { key: "probe_status", label: "Probe", mono: true, nowrap: true, render: (v) => String(v || "ok").toUpperCase(), color: (v) => tone(v || "ok") },
                { key: "download", label: "PPTX", render: (_, r) => (
                  <button onClick={(e) => { e.stopPropagation(); downloadPptx(r); }} style={{ ...ctl, padding: "4px 7px", border: "1px solid #7c3aed", color: "#6d28d9", fontWeight: 800, cursor: "pointer" }}>PPTX</button>
                ) },
              ]}
            />
          </Panel>

          <Panel
            title="선택한 ET 측정"
            right={activePackage && (
              <button onClick={() => downloadPptx(activePackage)}
                style={{ ...ctl, padding: "4px 8px", border: "1px solid #7c3aed", color: "#6d28d9", fontWeight: 800, cursor: "pointer" }}>
                PPTX
              </button>
            )}
          >
            {!activePackage ? (
              <div style={{ padding: 24, textAlign: "center", color: "var(--text-secondary)", fontSize: 14 }}>ET 측정이력을 선택하세요</div>
            ) : (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(120px, 1fr))", gap: 8 }}>
                <Mini label="제품" value={activePackage.product || "-"} color="#2563eb" />
                <Mini label="Lot" value={activePackage.fab_lot_id || activePackage.root_lot_id || "-"} color="#0f766e" />
                <Mini label="ET 측정시간" value={activePackage.package_time || "-"} color="#7c3aed" />
                <Mini label="Step Seq" value={activePackage.step_seq_points || activePackage.step_seq_combo || "-"} color="#f97316" />
              </div>
            )}
          </Panel>

          <Panel title="스코어보드" right={<span style={{ fontSize: 14, color: "var(--text-secondary)", fontFamily: "monospace" }}>{activePackage?.step_id || ""} {activePackage?.fab_lot_id || ""}</span>}>
            <Table
              rows={activeDetail?.scoreboard || []}
              columns={[
                { key: "alias", label: "Index", mono: true, nowrap: true },
                { key: "rawitem_id", label: "Raw", mono: true, nowrap: true },
                { key: "step_seq_points", label: "Step Seq PT", mono: true },
                { key: "pt_count", label: "PT", mono: true, align: "right" },
                { key: "mean_value", label: "Mean", mono: true, align: "right", render: (v) => fmt(v) },
                { key: "min_value", label: "Min", mono: true, align: "right", render: (v) => fmt(v) },
                { key: "max_value", label: "Max", mono: true, align: "right", render: (v) => fmt(v) },
                { key: "spec_out_points", label: "Out", mono: true, align: "right", color: (v) => Number(v) ? "#dc2626" : "var(--text-primary)" },
                { key: "status", label: "Status", mono: true, nowrap: true, render: (v) => String(v || "").toUpperCase(), color: (v) => tone(v) },
              ]}
            />
          </Panel>
        </div>
      </div>
    </div>
  );
}
