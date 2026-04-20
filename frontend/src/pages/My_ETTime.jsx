import { useState, useEffect, useRef } from "react";
import Loading from "../components/Loading";
const API = "/api/ettime";
const sf = (url, o) => fetch(url, o).then(r => { if (!r.ok) return r.json().then(d => { throw new Error(d.detail || "Error"); }); return r.json(); });
const COLORS = ["#6366f1","#f59e0b","#ec4899","#10b981","#3b82f6","#ef4444","#8b5cf6","#06b6d4","#f97316","#84cc16","#a855f7","#14b8a6","#e11d48","#0ea5e9","#d946ef"];

/* ─── Admin Settings Panel ─── */
function SettingsPanel({ config, onSave, onClose }) {
  const [cfg, setCfg] = useState(config);
  const u = (k, v) => setCfg({ ...cfg, [k]: v });
  const toggleGroup = (col) => {
    const cur = cfg.groupby_cols || [];
    u("groupby_cols", cur.includes(col) ? cur.filter(c => c !== col) : [...cur, col]);
  };
  const S = { width: "100%", padding: "6px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12, outline: "none" };
  return (
    <div style={{ position: "fixed", bottom: 0, left: 0, width: 340, maxHeight: "80vh", overflow: "auto", background: "var(--bg-secondary)", border: "1px solid var(--border)", borderRadius: "0 12px 0 0", padding: 20, zIndex: 100, boxShadow: "4px -4px 20px rgba(0,0,0,0.3)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <span style={{ fontSize: 14, fontWeight: 700, fontFamily: "monospace", color: "var(--accent)" }}>ET Time 설정</span>
        <span onClick={onClose} style={{ cursor: "pointer", fontSize: 16, color: "var(--text-secondary)" }}>×</span>
      </div>
      {[["source_root","Source Root (소스 루트)"],["source_product","Source Product (소스 제품)"],["lot_col","Lot 컬럼"],["step_col","Step 컬럼"],["tkin_col","TK-In 컬럼"],["tkout_col","TK-Out 컬럼"]].map(([k,l])=>(
        <div key={k} style={{ marginBottom: 10 }}>
          <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 3 }}>{l}</div>
          <input value={cfg[k] || ""} onChange={e => u(k, e.target.value)} style={S} />
        </div>
      ))}
      <div style={{ marginBottom: 14 }}>
        <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 6 }}>그룹 컬럼 (CAT 설비 카테고리)</div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {(config.available_cats || []).map(col => (
            <label key={col} style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11, cursor: "pointer", padding: "3px 8px", borderRadius: 4, border: "1px solid var(--border)", background: (cfg.groupby_cols || []).includes(col) ? "var(--accent-glow)" : "transparent", color: (cfg.groupby_cols || []).includes(col) ? "var(--accent)" : "var(--text-secondary)" }}>
              <input type="checkbox" checked={(cfg.groupby_cols || []).includes(col)} onChange={() => toggleGroup(col)} style={{ width: 12, height: 12, accentColor: "var(--accent)" }} />
              {col}
            </label>
          ))}
        </div>
      </div>
      <button onClick={() => onSave(cfg)} style={{ width: "100%", padding: 10, borderRadius: 6, border: "none", background: "var(--accent)", color: "#fff", fontWeight: 600, cursor: "pointer" }}>설정 저장</button>
    </div>
  );
}

/* ─── Horizontal Stacked Bar Chart ─── */
function ETBarChart({ waferBars, byCat, steps, selCat, onSelectWf }) {
  const [tip, setTip] = useState(null);
  const svgRef = useRef(null);
  if (!waferBars || !waferBars.length) return null;

  const maxTotal = Math.max(...waferBars.map(w => w.total));
  const barH = 22, gap = 4, labelW = 60, rightPad = 80;
  const chartW = 700;
  const H = waferBars.length * (barH + gap) + 40;
  const W = labelW + chartW + rightPad;
  const toX = (v) => labelW + (v / (maxTotal || 1)) * chartW;

  // Color map: steps or tools
  const useToolColors = selCat && byCat?.[selCat];
  let colorMap = {};
  if (useToolColors) {
    const allTools = [...new Set(byCat[selCat].flatMap(w => w.tools.map(t => t.tool)))].sort();
    allTools.forEach((t, i) => { colorMap[t] = COLORS[i % COLORS.length]; });
  } else {
    (steps || []).forEach((s, i) => { colorMap[s] = COLORS[i % COLORS.length]; });
  }

  const fmtMin = (v) => { if (v < 60) return v.toFixed(0) + "m"; return (v / 60).toFixed(1) + "h"; };

  // X-axis ticks
  const xTicks = [0, 0.25, 0.5, 0.75, 1].map(f => Math.round(maxTotal * f));

  return (
    <div style={{ position: "relative" }}>
      {tip && <div style={{ position: "absolute", left: tip.x + 12, top: tip.y - 10, background: "#111", border: "1px solid var(--border)", borderRadius: 6, padding: "6px 10px", fontSize: 11, color: "#e5e5e5", pointerEvents: "none", zIndex: 10, whiteSpace: "pre-wrap", lineHeight: 1.5, boxShadow: "0 4px 12px rgba(0,0,0,0.5)" }}>
        {tip.lines.map((l, i) => <div key={i}>{l}</div>)}
      </div>}
      <svg width={W} height={H} ref={svgRef} onMouseLeave={() => setTip(null)}>
        {/* X-axis gridlines */}
        {xTicks.map((v, i) => (
          <g key={i}>
            <line x1={toX(v)} y1={4} x2={toX(v)} y2={H - 20} stroke="var(--border)" strokeDasharray="2,3" opacity={0.4} />
            <text x={toX(v)} y={H - 6} textAnchor="middle" fill="var(--text-secondary)" fontSize={9}>{fmtMin(v)}</text>
          </g>
        ))}

        {/* Bars */}
        {waferBars.map((wf, wi) => {
          const y = wi * (barH + gap) + 4;
          const segments = useToolColors
            ? (byCat[selCat].find(w => w.wf === wf.wf)?.tools || []).map(t => ({ key: t.tool, elapsed: t.elapsed }))
            : wf.steps;

          let cx = labelW;
          return (
            <g key={wf.wf} onClick={() => onSelectWf?.(wf.wf)} style={{ cursor: "pointer" }}>
              {/* WF label */}
              <text x={labelW - 8} y={y + barH / 2 + 4} textAnchor="end" fill="var(--text-primary)" fontSize={11} fontWeight={600} fontFamily="monospace">{wf.wf}</text>
              {/* Stacked segments */}
              {segments.map((seg, si) => {
                const segKey = useToolColors ? seg.key : seg.step;
                const w = (seg.elapsed / (maxTotal || 1)) * chartW;
                const x = cx; cx += w;
                return (
                  <rect key={si} x={x} y={y} width={Math.max(0.5, w)} height={barH} rx={si === 0 ? 3 : 0}
                    fill={colorMap[segKey] || "#888"} opacity={0.85}
                    onMouseMove={e => { const r = svgRef.current.getBoundingClientRect(); setTip({ x: e.clientX - r.left, y: e.clientY - r.top, lines: [`${wf.wf}`, `${segKey}: ${fmtMin(seg.elapsed)}`, `합계: ${fmtMin(wf.total)}`] }); }}
                  />
                );
              })}
              {/* Total label */}
              <text x={toX(wf.total) + 6} y={y + barH / 2 + 4} fill="var(--accent)" fontSize={10} fontWeight={700} fontFamily="monospace">{fmtMin(wf.total)}</text>
            </g>
          );
        })}
      </svg>

      {/* Legend */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8, paddingLeft: labelW }}>
        {Object.entries(colorMap).map(([k, c]) => (
          <span key={k} style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 10 }}>
            <span style={{ width: 10, height: 10, borderRadius: 2, background: c, flexShrink: 0 }} />
            <span style={{ color: "var(--text-secondary)" }}>{k}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

/* ─── Main ET Time ─── */
export default function My_ETTime({ user }) {
  const [config, setConfig] = useState(null);
  const [sources, setSources] = useState([]);
  const [selRoot, setSelRoot] = useState("");
  const [selProduct, setSelProduct] = useState("");
  const [lotQuery, setLotQuery] = useState("");
  const [lotSuggestions, setLotSuggestions] = useState([]);
  const [showLotDrop, setShowLotDrop] = useState(false);
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [viewMode, setViewMode] = useState("chart"); // chart | detail
  const [selCat, setSelCat] = useState(""); // Selected CAT for drill-down
  const [selWf, setSelWf] = useState(null); // Selected wafer for detail
  const isAdmin = user?.role === "admin";
  const lotRef = useRef(null);

  useEffect(() => {
    sf(API + "/config").then(setConfig).catch(() => {});
    sf(API + "/sources").then(d => {
      const s = (d.products || []).filter(p => p.root);
      setSources(s);
      if (s.length) { setSelRoot(s[0].root); setSelProduct(s[0].product); }
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (selRoot && selProduct)
      sf(API + "/lot-ids?root=" + encodeURIComponent(selRoot) + "&product=" + encodeURIComponent(selProduct))
        .then(d => setLotSuggestions(d.lot_ids || [])).catch(() => {});
  }, [selRoot, selProduct]);

  useEffect(() => {
    const h = e => { if (lotRef.current && !lotRef.current.contains(e.target)) setShowLotDrop(false); };
    document.addEventListener("mousedown", h); return () => document.removeEventListener("mousedown", h);
  }, []);

  const filteredLots = lotQuery ? lotSuggestions.filter(l => l.toLowerCase().includes(lotQuery.toLowerCase())) : lotSuggestions;

  const search = () => {
    if (!lotQuery.trim()) return;
    setLoading(true); setSelWf(null); setSelCat("");
    const params = `lot=${encodeURIComponent(lotQuery.trim())}&root=${encodeURIComponent(selRoot)}&product=${encodeURIComponent(selProduct)}`;
    sf(API + "/search?" + params)
      .then(d => { setResults(d); setLoading(false); })
      .catch(e => { alert(e.message); setLoading(false); });
  };

  const saveSettings = (cfg) => {
    sf(API + "/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(cfg) })
      .then(() => { setShowSettings(false); sf(API + "/config").then(setConfig); })
      .catch(e => alert(e.message));
  };

  const fmtMin = (v) => { if (v == null || isNaN(v)) return "-"; const n = parseFloat(v); if (n < 60) return n.toFixed(1) + "m"; return (n / 60).toFixed(1) + "h"; };
  const cellS = { padding: "6px 10px", borderBottom: "1px solid var(--border)", fontSize: 12 };
  const headS = { ...cellS, background: "var(--bg-tertiary)", fontSize: 10, color: "var(--text-secondary)", fontWeight: 600, textAlign: "left", position: "sticky", top: 0, zIndex: 1 };

  if (!config) return <div style={{ padding: 40, textAlign: "center" }}><Loading text="설정 불러오는 중..." /></div>;

  const waferBars = results?.wafer_bars || [];
  const byCat = results?.by_cat || {};
  const catKeys = Object.keys(byCat);
  const summary = results?.summary || [];

  // Detail rows for selected wafer
  const detailRows = selWf && results?.results
    ? results.results.filter(r => String(r[config.wafer_col || "WAFER_ID"]) === selWf)
    : [];

  return (
    <div style={{ display: "flex", height: "calc(100vh - 48px)", background: "var(--bg-primary)", color: "var(--text-primary)" }}>
      <div style={{ flex: 1, overflow: "auto", padding: "24px 32px" }}>
        {/* Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
          <div style={{ fontSize: 16, fontWeight: 700, fontFamily: "monospace", color: "var(--accent)" }}>{">"} et_time</div>
          <div style={{ display: "flex", gap: 6 }}>
            {["chart", "summary", "detail"].map(m => (
              <span key={m} onClick={() => setViewMode(m)} style={{ padding: "4px 12px", borderRadius: 4, fontSize: 11, cursor: "pointer", fontWeight: viewMode === m ? 600 : 400, background: viewMode === m ? "var(--accent-glow)" : "transparent", color: viewMode === m ? "var(--accent)" : "var(--text-secondary)", fontFamily: "monospace" }}>
                {m === "chart" ? "막대 차트" : m === "summary" ? "요약" : "상세"}
              </span>
            ))}
          </div>
        </div>

        {/* Source + Search */}
        <div style={{ display: "flex", gap: 8, marginBottom: 12, alignItems: "flex-end" }}>
          <div>
            <div style={{ fontSize: 10, color: "var(--text-secondary)", marginBottom: 3 }}>소스</div>
            <select value={`${selRoot}/${selProduct}`} onChange={e => { const [r, p] = e.target.value.split("/"); setSelRoot(r); setSelProduct(p); setLotQuery(""); setResults(null); }}
              style={{ padding: "9px 12px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg-card)", color: "var(--text-primary)", fontSize: 12, outline: "none", fontFamily: "monospace", minWidth: 180 }}>
              {sources.map(s => <option key={s.label} value={`${s.root}/${s.product}`}>{s.root}/{s.product}</option>)}
            </select>
          </div>
          <div style={{ flex: 1, position: "relative" }} ref={lotRef}>
            <div style={{ fontSize: 10, color: "var(--text-secondary)", marginBottom: 3 }}>ROOT_LOT_ID</div>
            <input value={lotQuery} onChange={e => { setLotQuery(e.target.value); setShowLotDrop(true); }}
              onFocus={() => setShowLotDrop(true)}
              placeholder="Lot 입력 또는 선택..."
              onKeyDown={e => { if (e.key === "Enter") { setShowLotDrop(false); search(); } }}
              style={{ width: "100%", padding: "9px 14px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg-card)", color: "var(--text-primary)", fontSize: 13, outline: "none", fontFamily: "'JetBrains Mono',monospace" }} />
            {showLotDrop && filteredLots.length > 0 && <div style={{ position: "absolute", top: "100%", left: 0, right: 0, zIndex: 20, maxHeight: 200, overflow: "auto", background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: 6, marginTop: 2, boxShadow: "0 4px 12px rgba(0,0,0,0.3)" }}>
              {filteredLots.slice(0, 50).map(l => <div key={l} onClick={() => { setLotQuery(l); setShowLotDrop(false); }}
                onMouseDown={e => e.preventDefault()}
                style={{ padding: "6px 12px", fontSize: 12, cursor: "pointer", borderBottom: "1px solid var(--border)", fontFamily: "monospace" }}
                onMouseEnter={e => e.currentTarget.style.background = "var(--bg-hover)"} onMouseLeave={e => e.currentTarget.style.background = "transparent"}>{l}</div>)}
            </div>}
          </div>
          <button onClick={search} disabled={loading}
            style={{ padding: "9px 24px", borderRadius: 6, border: "none", background: "var(--accent)", color: "#fff", fontSize: 13, fontWeight: 600, cursor: loading ? "wait" : "pointer", fontFamily: "monospace" }}>
            {loading ? "..." : "검색"}
          </button>
        </div>

        {/* CAT selector chips */}
        {results && catKeys.length > 0 && (
          <div style={{ display: "flex", gap: 6, marginBottom: 12, alignItems: "center", flexWrap: "wrap" }}>
            <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>그룹 기준:</span>
            <span onClick={() => setSelCat("")}
              style={{ padding: "3px 10px", borderRadius: 4, fontSize: 10, fontWeight: 600, cursor: "pointer", background: !selCat ? "var(--accent)" : "var(--bg-hover)", color: !selCat ? "#fff" : "var(--text-secondary)", fontFamily: "monospace" }}>STEP</span>
            {catKeys.map(cat => (
              <span key={cat} onClick={() => setSelCat(cat)}
                style={{ padding: "3px 10px", borderRadius: 4, fontSize: 10, fontWeight: 600, cursor: "pointer", background: selCat === cat ? "var(--accent)" : "var(--bg-hover)", color: selCat === cat ? "#fff" : "var(--text-secondary)", fontFamily: "monospace" }}>{cat}</span>
            ))}
          </div>
        )}

        {/* Result info */}
        {results && !loading && (
          <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 8 }}>
            {results.lot_query} | {results.total} 건 | {waferBars.length} Wafer
            {selWf && <span style={{ marginLeft: 12, color: "var(--accent)", fontWeight: 600 }}>선택됨: {selWf}</span>}
          </div>
        )}

        {/* Bar Chart View */}
        {results && !loading && viewMode === "chart" && (
          <div style={{ background: "var(--bg-card)", borderRadius: 8, border: "1px solid var(--border)", padding: "16px 20px", overflow: "auto" }}>
            <ETBarChart waferBars={waferBars} byCat={byCat} steps={results.steps} selCat={selCat}
              onSelectWf={(wf) => setSelWf(selWf === wf ? null : wf)} />
          </div>
        )}

        {/* Selected WF step detail */}
        {results && !loading && viewMode === "chart" && selWf && (
          <div style={{ marginTop: 12, background: "var(--bg-card)", borderRadius: 8, border: "1px solid var(--border)", padding: "12px 16px" }}>
            <div style={{ fontSize: 12, fontWeight: 700, fontFamily: "monospace", color: "var(--accent)", marginBottom: 8 }}>{selWf} Step 상세</div>
            <div style={{ maxHeight: 240, overflow: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead><tr>
                  {results.columns?.map(h => <th key={h} style={headS}>{h}</th>)}
                </tr></thead>
                <tbody>
                  {detailRows.map((row, i) => (
                    <tr key={i} style={{ background: i % 2 ? "var(--bg-secondary)" : "transparent" }}>
                      {results.columns?.map(c => (
                        <td key={c} style={{ ...cellS, fontFamily: c.includes("TIME") || c === "ELAPSED_MIN" ? "monospace" : "inherit", fontSize: c.includes("TIME") ? 10 : 12, color: c === "ELAPSED_MIN" ? "var(--accent)" : "var(--text-primary)" }}>
                          {c === "ELAPSED_MIN" ? fmtMin(row[c]) : (row[c] ?? "")}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Summary table view */}
        {results && !loading && viewMode === "summary" && summary.length > 0 && (
          <div style={{ border: "1px solid var(--border)", borderRadius: 8, overflow: "auto", maxHeight: "calc(100vh - 260px)" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead><tr>{Object.keys(summary[0]).map(h => <th key={h} style={headS}>{h}</th>)}</tr></thead>
              <tbody>{summary.map((row, i) => (
                <tr key={i} style={{ background: i % 2 ? "var(--bg-card)" : "transparent" }}>
                  {Object.entries(row).map(([k, v]) => (
                    <td key={k} style={{ ...cellS, fontFamily: k.includes("MIN") || k === "STEP_COUNT" ? "monospace" : "inherit", fontWeight: k === "TOTAL_MIN" ? 700 : 400, color: k === "TOTAL_MIN" ? "var(--accent)" : "var(--text-primary)" }}>
                      {k.includes("MIN") ? fmtMin(v) : v}
                    </td>
                  ))}
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}

        {/* Detail table view */}
        {results && !loading && viewMode === "detail" && results.results?.length > 0 && (
          <div style={{ border: "1px solid var(--border)", borderRadius: 8, overflow: "auto", maxHeight: "calc(100vh - 260px)" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead><tr>{results.columns?.map(h => <th key={h} style={headS}>{h}</th>)}</tr></thead>
              <tbody>{results.results.map((row, i) => (
                <tr key={i} style={{ background: i % 2 ? "var(--bg-card)" : "transparent" }}>
                  {results.columns?.map(c => (
                    <td key={c} style={{ ...cellS, fontFamily: c.includes("TIME") || c === "ELAPSED_MIN" ? "monospace" : "inherit", fontSize: c.includes("TIME") ? 10 : 12, color: c === "ELAPSED_MIN" ? "var(--accent)" : "var(--text-primary)" }}>
                      {c === "ELAPSED_MIN" ? fmtMin(row[c]) : (row[c] ?? "")}
                    </td>
                  ))}
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}

        {!results && !loading && <div style={{ textAlign: "center", padding: 60, color: "var(--text-secondary)", fontSize: 13 }}>ROOT_LOT_ID 를 입력하여 설비 시간을 검색하세요</div>}
      </div>

      {/* Admin gear */}
      {isAdmin && <div onClick={() => setShowSettings(!showSettings)}
        style={{ position: "fixed", bottom: 16, left: 16, width: 40, height: 40, borderRadius: "50%", background: "var(--bg-secondary)", border: "1px solid var(--border)", display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer", zIndex: 99, boxShadow: "0 2px 8px rgba(0,0,0,0.3)", fontSize: 18 }}>
        ⚙️
      </div>}
      {showSettings && isAdmin && config && <SettingsPanel config={config} onSave={saveSettings} onClose={() => setShowSettings(false)} />}
    </div>
  );
}
