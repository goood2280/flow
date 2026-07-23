import { useState, useEffect, useMemo } from "react";
import Loading from "../components/Loading";
import { Pill, Button, statusPalette } from "../components/UXKit";
import { WipStackedBar } from "../components/PlotlyChart";
import { sf as apiSf } from "../lib/api";

// v9.2 (2026-07-12): 대시보드 = WIP × Split 현황 단일 화면.
// 기존 차트 보드(저장 차트 그리드/에디터/스케줄러 UI)는 퇴역 —
// 원본 전체는 archive/dashboard_chartboard_2026_07_12/My_Dashboard.full.jsx 참조.
// 차트 보드용 백엔드 API(/api/dashboard/charts 등)는 아직 남아 있다.
const API = "/api/dashboard";
const sf = (url, o) => apiSf(url, o);
const BAD = statusPalette.bad;

// 차트 높이를 뷰포트에 맞춰 산출 — 고정 430px 대신 전체화면 기준으로 한눈에 들어오게.
function useChartHeight(ratio = 0.5, min = 320, max = 900) {
  const calc = () => {
    const h = typeof window !== "undefined" ? window.innerHeight : 800;
    return Math.max(min, Math.min(max, Math.round(h * ratio)));
  };
  const [h, setH] = useState(calc);
  useEffect(() => {
    const on = () => setH(calc());
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, []);
  return h;
}

/* ═══ WIP × Split 현황 (latest cache 기반) ═══ */
const WIP_BIN_CHOICES = [1000, 10000, 100000];
// X축 기준 — step_id 숫자 구간 / step_desc 앞머리 숫자(예: FAB_1.0 STI → 1.0).
const AXIS_CHOICES = [
  { value: "step_id", label: "step_id 구간" },
  { value: "step_desc", label: "step_desc (앞 숫자)" },
];

function WipSplitPanel() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [product, setProduct] = useState("");
  const [binSize, setBinSize] = useState(1000);
  const [binInput, setBinInput] = useState("1000");
  const [splitCol, setSplitCol] = useState("");
  const [axis, setAxis] = useState("step_id");
  const dark = typeof document !== "undefined" && (document.documentElement.classList.contains("dark") || localStorage.getItem("hol_dark") === "true");
  const chartH = useChartHeight();

  const fetchData = (p, b, s, a) => {
    setLoading(true);
    setErr("");
    const q = new URLSearchParams();
    if (p) q.set("product", p);
    q.set("bin_size", String(b || 1000));
    if (s) q.set("split_col", s);
    q.set("axis", a || "step_id");
    sf(`${API}/wip-split?${q.toString()}`)
      .then((d) => {
        setData(d);
        setProduct(d.product || "");
        setSplitCol(d.split_col || "");
        if (d.axis) setAxis(d.axis);
      })
      .catch((e) => setErr(e.message || String(e)))
      .finally(() => setLoading(false));
  };
  useEffect(() => { fetchData("", binSize, "", axis); }, []);

  // 자유 입력 bin 간격 적용 — 1~100000 정수로 보정, 같은 값 재입력은 무시.
  const applyBin = (val) => {
    const b = Math.max(1, Math.min(100000, Math.round(Number(val) || 0)));
    if (!b) { setBinInput(String(binSize)); return; }
    setBinInput(String(b));
    if (b === binSize) return;
    setBinSize(b);
    fetchData(product, b, splitCol, axis);
  };

  const bins = data?.bins || [];
  const splitValues = data?.split_values || [];
  const unassigned = data?.unassigned_label || "(미지정)";
  const totalBySplit = useMemo(() => {
    const acc = {};
    for (const b of bins) for (const [k, v] of Object.entries(b.splits || {})) acc[k] = (acc[k] || 0) + Number(v || 0);
    return acc;
  }, [bins]);
  const grandTotal = data?.total_wafers || 0;
  const groupedSplitCols = useMemo(() => {
    const groups = { KNOB: [], MASK: [], FAB: [], 기타: [] };
    for (const c of data?.split_cols || []) {
      const u = String(c).toUpperCase();
      if (u.startsWith("KNOB_")) groups.KNOB.push(c);
      else if (u.startsWith("MASK_")) groups.MASK.push(c);
      else if (u.startsWith("FAB_")) groups.FAB.push(c);
      else groups["기타"].push(c);
    }
    return groups;
  }, [data]);

  const selStyle = { padding: "6px 9px", border: "1px solid var(--border)", borderRadius: 6, background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14 };
  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr)", gap: 12, paddingBottom: 14 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "end", flexWrap: "wrap", padding: "10px 12px", border: "1px solid var(--border)", borderRadius: 8, background: "var(--bg-secondary)" }}>
        <label style={{ display: "grid", gap: 4, fontSize: 12, color: "var(--text-secondary)" }}>
          PRODUCT
          <select style={selStyle} value={product} onChange={(e) => fetchData(e.target.value, binSize, "", axis)}>
            {(data?.products || (product ? [product] : [])).map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </label>
        <label style={{ display: "grid", gap: 4, fontSize: 12, color: "var(--text-secondary)" }}>
          X축 기준
          <select style={selStyle} value={axis} onChange={(e) => { setAxis(e.target.value); fetchData(product, binSize, splitCol, e.target.value); }}>
            {AXIS_CHOICES.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
          </select>
        </label>
        <label style={{ display: "grid", gap: 4, fontSize: 12, color: "var(--text-secondary)", opacity: axis === "step_id" ? 1 : 0.45 }}>
          STEP BIN 간격 — 직접 입력 (step_id 숫자 6자리 기준)
          <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
            <input
              type="number"
              min={1}
              max={100000}
              step={5}
              value={binInput}
              disabled={axis !== "step_id"}
              onChange={(e) => setBinInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") {if(e.nativeEvent?.isComposing||e.keyCode===229)return; applyBin(binInput);} }}
              onBlur={() => applyBin(binInput)}
              style={{ ...selStyle, width: 92 }}
            />
            <div style={{ display: "flex", gap: 3 }}>
              {WIP_BIN_CHOICES.map((b) => (
                <button
                  key={b}
                  type="button"
                  disabled={axis !== "step_id"}
                  onClick={() => applyBin(b)}
                  style={{
                    padding: "4px 8px",
                    fontSize: 12,
                    border: `1px solid ${binSize === b ? "var(--accent, var(--text-primary))" : "var(--border)"}`,
                    borderRadius: 5,
                    background: binSize === b ? "var(--bg-tertiary)" : "var(--bg-primary)",
                    color: "var(--text-primary)",
                    fontWeight: binSize === b ? 800 : 400,
                    cursor: "pointer",
                  }}
                >{b}</button>
              ))}
            </div>
          </div>
        </label>
        <label style={{ display: "grid", gap: 4, fontSize: 12, color: "var(--text-secondary)" }}>
          SPLIT 기준 열 (SplitTable)
          <select style={selStyle} value={splitCol} onChange={(e) => fetchData(product, binSize, e.target.value, axis)}>
            {Object.entries(groupedSplitCols).map(([g, cols]) => cols.length ? (
              <optgroup key={g} label={g}>
                {cols.map((c) => <option key={c} value={c}>{c}</option>)}
              </optgroup>
            ) : null)}
          </select>
        </label>
        <Button variant="subtle" onClick={() => fetchData(product, binSize, splitCol, axis)} disabled={loading}>{loading ? "조회 중..." : "새로고침"}</Button>
        <div style={{ marginLeft: "auto", display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap", fontSize: 12, color: "var(--text-secondary)" }}>
          <Pill tone="ok">wafer {grandTotal}</Pill>
          <Pill tone={data?.matched_wafers ? "ok" : "neutral"}>split 매칭 {data?.matched_wafers ?? 0}</Pill>
          {data?.generated_at ? <span>latest cache: {data.generated_at}</span> : null}
        </div>
      </div>
      {err && <div style={{ padding: 12, border: "1px solid var(--border)", borderRadius: 8, color: BAD.fg, fontSize: 13 }}>{err}</div>}
      <div style={{ border: "1px solid var(--border)", borderRadius: 8, background: "var(--bg-secondary)", padding: 10 }}>
        <div style={{ fontSize: 13, fontWeight: 800, marginBottom: 6 }}>
          현재 STEP 구간별 WAFER 물량 <span style={{ fontWeight: 400, color: "var(--text-secondary)" }}>— {splitCol || "split 없음"} 비중 스택</span>
        </div>
        {loading && !data ? <Loading /> : (
          <WipStackedBar bins={bins} splitValues={splitValues} dark={dark} unassignedLabel={unassigned} height={chartH}
            xLabel={axis === "step_desc" ? "STEP_DESC 앞 숫자" : `STEP BIN (간격 ${data?.bin_size ?? binSize})`} yLabel="WAFERS" />
        )}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 2fr) minmax(260px, 1fr)", gap: 12, alignItems: "start" }}>
        <div style={{ border: "1px solid var(--border)", borderRadius: 8, background: "var(--bg-secondary)", padding: 10, overflowX: "auto" }}>
          <div style={{ fontSize: 13, fontWeight: 800, marginBottom: 6 }}>BIN별 상세</div>
          <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 12 }}>
            <thead>
              <tr>
                <th style={{ textAlign: "left", padding: "4px 8px", borderBottom: "1px solid var(--border)" }}>{axis === "step_desc" ? "STEP_DESC 앞 숫자" : "STEP BIN"}</th>
                <th style={{ textAlign: "right", padding: "4px 8px", borderBottom: "1px solid var(--border)" }}>WAFERS</th>
                {splitValues.map((sv) => (
                  <th key={sv} style={{ textAlign: "right", padding: "4px 8px", borderBottom: "1px solid var(--border)", whiteSpace: "nowrap" }}>{sv}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {bins.map((b) => (
                <tr key={b.bin}>
                  <td style={{ padding: "4px 8px", borderBottom: "1px solid var(--border)", fontFamily: "monospace" }}>{b.label}</td>
                  <td style={{ padding: "4px 8px", borderBottom: "1px solid var(--border)", textAlign: "right", fontWeight: 700 }}>{b.total}</td>
                  {splitValues.map((sv) => {
                    const n = Number(b.splits?.[sv] || 0);
                    const pct = b.total ? Math.round((n / b.total) * 100) : 0;
                    return (
                      <td key={sv} style={{ padding: "4px 8px", borderBottom: "1px solid var(--border)", textAlign: "right", color: n ? "var(--text-primary)" : "var(--text-secondary)" }}>
                        {n ? `${n} (${pct}%)` : "-"}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div style={{ border: "1px solid var(--border)", borderRadius: 8, background: "var(--bg-secondary)", padding: 10 }}>
          <div style={{ fontSize: 13, fontWeight: 800, marginBottom: 6 }}>SPLIT 전체 비중</div>
          <div style={{ display: "grid", gap: 4 }}>
            {splitValues.map((sv) => {
              const n = totalBySplit[sv] || 0;
              const pct = grandTotal ? ((n / grandTotal) * 100) : 0;
              return (
                <div key={sv} style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) auto", gap: 8, alignItems: "center", fontSize: 12 }}>
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{sv}</span>
                  <span style={{ fontFamily: "monospace" }}>{n} · {pct.toFixed(1)}%</span>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

export default function My_Dashboard() {
  return (
    <div style={{ padding: "16px 18px", background: "var(--bg-primary)", color: "var(--text-primary)", maxWidth: "none", margin: 0, height: "100%", minHeight: 0, overflow: "auto", boxSizing: "border-box" }}>
      <WipSplitPanel />
    </div>
  );
}
