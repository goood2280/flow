import { useState, useEffect, useRef, useMemo, createContext, useContext } from "react";
import Loading from "../components/Loading";
import PageGear from "../components/PageGear";
import { PageHeader, TabStrip, Pill, Button, statusPalette, uxColors, chartPalette } from "../components/UXKit";
import { sf as apiSf } from "../lib/api";
// Inject chart hover styles once
if(typeof document!=="undefined"&&!document.getElementById("dash-styles")){
  const s=document.createElement("style");s.id="dash-styles";
  s.textContent=`
    .chart-card .chart-actions{opacity:0;transition:opacity 0.15s}
    .chart-card:hover .chart-actions{opacity:1}
    .chart-card{box-shadow:0 8px 22px rgba(15,23,42,0.06)}
    .chart-card:hover{box-shadow:0 12px 30px rgba(15,23,42,0.10)}
  `;
  document.head.appendChild(s);
}
const API = "/api/dashboard";
const sf = (url, o) => apiSf(url, o);
const DASHBOARD_SECTIONS_DEFAULT = { charts: true, progress: false, alerts: false };
const SERIES = chartPalette.series;
const PASTELS = chartPalette.pastel;
const BAD = statusPalette.bad;
const WARN = statusPalette.warn;
const OK = statusPalette.ok;
const INFO = statusPalette.info;
const NEUTRAL = statusPalette.neutral;
const INDIGO = { fg: chartPalette.series[0], bg: `${chartPalette.series[0]}22`, soft: `${chartPalette.series[0]}11` };
const PURPLE = { fg: chartPalette.series[6], bg: `${chartPalette.series[6]}22`, soft: `${chartPalette.series[6]}11`, border: `${chartPalette.series[6]}33` };
const GREEN = { fg: chartPalette.series[3], bg: `${chartPalette.series[3]}22`, soft: `${chartPalette.series[3]}11`, border: `${chartPalette.series[3]}33` };
const BLUE = { fg: chartPalette.series[4], bg: `${chartPalette.series[4]}22`, soft: `${chartPalette.series[4]}11`, border: `${chartPalette.series[4]}55` };
const TEAL = { fg: chartPalette.series[11], bg: `${chartPalette.series[11]}22` };
const WHITE = "var(--bg-secondary)";
const MUTED_DARK = "rgba(17,24,39,0.92)";
const SOFT_TEXT = "rgba(229,231,235,0.94)";
const DIM_TEXT = "rgba(156,163,175,0.95)";
const DIVIDER_DARK = "rgba(68,68,68,0.85)";
const MARK_STROKE = "rgba(251,191,36,0.95)";

function chartTypeLabel(type) {
  return ({
    scatter: "산점도",
    line: "선형 추이",
    bar: "막대 비교",
    area: "면적 추이",
    combo: "복합 차트",
    pie: "구성 비율",
    donut: "도넛 차트",
    binning: "분포 히스토그램",
    pareto: "파레토",
    box: "분포 박스플롯",
    treemap: "트리맵",
    heatmap: "히트맵",
    wafer_map: "웨이퍼 맵",
    step_knob_binning: "Step/KNOB 비율",
    table: "테이블",
    cross_table: "교차 테이블",
  }[type] || "차트");
}

function timeAgo(iso) {
  if (!iso) return "";
  const d = (Date.now() - new Date(iso).getTime()) / 60000;
  if (d < 1) return "방금"; if (d < 60) return `${Math.floor(d)}분 전`;
  if (d < 1440) return `${Math.floor(d / 60)}시간 전`; return `${Math.floor(d / 1440)}일 전`;
}
function num(v, fallback = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}
function maybeNum(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}
function fmt(v) {
  if (v == null || v === "") return "";
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  return Math.abs(n) >= 1000 ? (n/1000).toFixed(1)+"k" : n % 1 === 0 ? String(n) : n.toFixed(2);
}
function fixed(v, digits = 1, fallback = "-") {
  const n = Number(v);
  if (!Number.isFinite(n)) return v == null || v === "" ? fallback : String(v);
  return n.toFixed(digits);
}
function pct(v, digits = 1) {
  return fixed(num(v) * 100, digits, "0.0");
}

function MiniStat({ label, value, tone = "var(--accent)" }) {
  return (
    <div style={{ padding: "10px 12px", borderRadius: 12, border: "1px solid var(--border)", background: "rgba(255,255,255,0.62)" }}>
      <div style={{ fontSize: 10, color: "var(--text-secondary)", fontFamily: "monospace" }}>{label}</div>
      <div style={{ marginTop: 4, fontSize: 18, fontWeight: 800, color: tone, fontFamily: "monospace" }}>{value}</div>
    </div>
  );
}

function ProgressSparkline({ path }) {
  const pts = Array.isArray(path) ? path.slice(-6) : [];
  if (pts.length < 2) return <div style={{ fontSize: 10, color: "var(--text-secondary)" }}>경로 데이터 없음</div>;
  const W = 180, H = 54, pad = 8;
  const x = (i) => pad + ((W - pad * 2) * i) / Math.max(1, pts.length - 1);
  const y = (i) => H - pad - ((H - pad * 2) * i) / Math.max(1, pts.length - 1);
  const d = pts.map((_, i) => `${i === 0 ? "M" : "L"} ${x(i)} ${y(i)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} style={{ display: "block" }}>
      <path d={d} fill="none" stroke={BLUE.fg} strokeWidth={2.5} />
      {pts.map((p, i) => (
        <g key={i}>
          <circle cx={x(i)} cy={y(i)} r={3.5} fill={i === pts.length - 1 ? WARN.fg : BLUE.fg} />
          <text x={x(i)} y={H - 2} textAnchor="middle" fontSize={7.5} fill="var(--text-secondary)" fontFamily="monospace">{String(p.step_id || "").slice(-4)}</text>
        </g>
      ))}
    </svg>
  );
}

function StepSpeedLineChart({ rows }) {
  const data = (Array.isArray(rows) ? rows : []).slice(0, 36);
  if (!data.length) return <div style={{ fontSize: 11, color: "var(--text-secondary)", fontFamily: "monospace" }}>비교 가능한 step transition이 없습니다.</div>;
  const W = 720, H = 250;
  const pad = { t: 18, r: 28, b: 58, l: 56 };
  const cw = W - pad.l - pad.r, ch = H - pad.t - pad.b;
  const vals = data.flatMap(r => [Number(r.searched_hours), Number(r.avg_hours)]).filter(Number.isFinite);
  const maxY = Math.max(1, ...vals) * 1.08;
  const toX = (i) => pad.l + (data.length <= 1 ? cw / 2 : (i / (data.length - 1)) * cw);
  const toY = (v) => pad.t + ch - (Number(v || 0) / maxY) * ch;
  const pathFor = (key) => data
    .map((r, i) => ({ r, i }))
    .filter(({ r }) => r[key] != null && Number.isFinite(Number(r[key])))
    .map(({ r, i }, pi) => `${pi === 0 ? "M" : "L"} ${toX(i)} ${toY(r[key])}`)
    .join(" ");
  const labelEvery = Math.max(1, Math.ceil(data.length / 9));
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} style={{ display: "block" }}>
      {[0, 0.25, 0.5, 0.75, 1].map((f, i) => {
        const y = pad.t + ch * (1 - f);
        return (
          <g key={i}>
            <line x1={pad.l} y1={y} x2={W - pad.r} y2={y} stroke="var(--border)" strokeDasharray="3,4" />
            <text x={pad.l - 8} y={y + 4} textAnchor="end" fill="var(--text-secondary)" fontSize={10}>{fmt(maxY * f)}h</text>
          </g>
        );
      })}
      <line x1={pad.l} y1={pad.t} x2={pad.l} y2={pad.t + ch} stroke="rgba(15,23,42,0.28)" />
      <line x1={pad.l} y1={pad.t + ch} x2={W - pad.r} y2={pad.t + ch} stroke="rgba(15,23,42,0.28)" />
      <path d={pathFor("searched_hours")} fill="none" stroke={BLUE.fg} strokeWidth={2.4} />
      <path d={pathFor("avg_hours")} fill="none" stroke="rgba(15,23,42,0.58)" strokeWidth={2.2} strokeDasharray="6,4" />
      {data.map((r, i) => (
        <g key={i}>
          <circle cx={toX(i)} cy={toY(r.searched_hours)} r={3.3} fill={BLUE.fg}>
            <title>{`${r.from_step} -> ${r.to_step}\n검색 랏 ${fmt(r.searched_hours)}h\n최근 평균 ${fmt(r.avg_hours)}h`}</title>
          </circle>
          {r.avg_hours != null && <circle cx={toX(i)} cy={toY(r.avg_hours)} r={2.8} fill="rgba(15,23,42,0.58)">
            <title>{`${r.from_step} -> ${r.to_step}\n최근 평균 ${fmt(r.avg_hours)}h\nsamples ${r.sample_count || 0}`}</title>
          </circle>}
          {i % labelEvery === 0 && (
            <text x={toX(i)} y={H - pad.b + 16} textAnchor="end" fill="var(--text-secondary)" fontSize={8} fontFamily="monospace" transform={`rotate(-35,${toX(i)},${H - pad.b + 16})`}>
              {String(r.to_step || r.from_step || "").slice(-6)}
            </text>
          )}
        </g>
      ))}
      <g transform={`translate(${pad.l},${H - 18})`}>
        <rect width="9" height="9" rx="2" fill={BLUE.fg} />
        <text x="14" y="9" fill="var(--text-secondary)" fontSize="10" fontFamily="monospace">searched lot</text>
        <line x1="106" y1="5" x2="128" y2="5" stroke="rgba(15,23,42,0.58)" strokeWidth="2.2" strokeDasharray="6,4" />
        <text x="134" y="9" fill="var(--text-secondary)" fontSize="10" fontFamily="monospace">recent average</text>
      </g>
    </svg>
  );
}

function StepSpeedComparePanel({ compare, targetStepId, sampleLots }) {
  const rows = Array.isArray(compare?.rows) ? compare.rows : [];
  const eta = compare?.target_eta || {};
  const hasTarget = !!compare?.target_root_lot_id;
  const dt = (v) => v ? new Date(v).toLocaleString() : "-";
  const etaLabel = (() => {
    if (!targetStepId) return "";
    if (eta.status === "reached") return `도착 완료 · ${dt(eta.actual_time || eta.eta_at)}`;
    if (eta.status === "estimated") return `${dt(eta.eta_at)} · 약 ${fmt(eta.avg_days)}일 (${fmt(eta.avg_hours)}h)`;
    if (eta.status === "insufficient_history") return eta.note || "최근 lots 이력 부족";
    return "target step 입력 시 도착 예상 표시";
  })();
  if (!hasTarget) {
    return (
      <div style={{ marginBottom: 12, padding: 12, borderRadius: 12, border: "1px dashed rgba(37,99,235,0.28)", background: "rgba(255,255,255,0.62)", fontSize: 11, color: "var(--text-secondary)", fontFamily: "monospace" }}>
        {compare?.note || "root_lot_id 또는 fab_lot_id를 검색하면 최근 lots 평균과 비교합니다."}
      </div>
    );
  }
  return (
    <div style={{ marginBottom: 12, border: "1px solid rgba(37,99,235,0.16)", borderRadius: 12, overflow: "hidden", background: "rgba(255,255,255,0.72)" }}>
      <div style={{ padding: "10px 12px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
        <div style={{ fontSize: 11, fontWeight: 800, color: INDIGO.fg, fontFamily: "monospace" }}>
          {compare.target_root_lot_id} 속도 비교
          <span style={{ marginLeft: 8, color: "var(--text-secondary)", fontWeight: 600 }}>
            {compare.target_fab_lot_id || "-"} · 최근 {compare.sample_window || sampleLots || 3} lots 평균
          </span>
        </div>
        {targetStepId && <div style={{ fontSize: 11, color: eta.status === "estimated" ? TEAL.fg : eta.status === "reached" ? OK.fg : "var(--text-secondary)", fontFamily: "monospace", fontWeight: 700 }}>{targetStepId}: {etaLabel}</div>}
      </div>
      <div style={{ padding: 12, display: "grid", gap: 10 }}>
        {rows.length === 0 ? (
          <div style={{ fontSize: 11, color: "var(--text-secondary)", fontFamily: "monospace" }}>비교 가능한 step transition이 없습니다.</div>
        ) : (
          <>
            <StepSpeedLineChart rows={rows} />
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 6 }}>
              {rows.slice(0, 8).map((row) => {
                const ratio = Number(row.ratio || 0);
                const tone = ratio > 1.15 ? WARN.fg : ratio > 0 && ratio < 0.85 ? OK.fg : BLUE.fg;
                return (
                  <div key={`${row.index}-${row.from_step}-${row.to_step}`} style={{ padding: "7px 9px", border: "1px solid var(--border)", borderRadius: 8, background: "rgba(15,23,42,0.025)", fontFamily: "monospace" }}>
                    <div style={{ fontSize: 10, color: "var(--text-primary)", fontWeight: 700, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{String(row.from_step || "").slice(-6)} → {String(row.to_step || "").slice(-6)}</div>
                    <div style={{ marginTop: 3, display: "flex", justifyContent: "space-between", gap: 8, fontSize: 10, color: "var(--text-secondary)" }}>
                      <span>lot {fmt(row.searched_hours)}h</span>
                      <span>avg {row.avg_hours == null ? "-" : `${fmt(row.avg_hours)}h`}</span>
                      <span style={{ color: tone, fontWeight: 800 }}>{ratio ? `${fmt(ratio)}x` : "-"}</span>
                    </div>
                  </div>
                );
              })}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function KnobProgressPanel({ data, targetStepId }) {
  const kp = data?.knob_progress || {};
  const bins = Array.isArray(kp.step_bins) ? kp.step_bins : [];
  const fastest = Array.isArray(kp.fastest_bins) ? kp.fastest_bins : [];
  if (!kp.ok || !kp.knob_col) return null;
  const maxCount = Math.max(1, ...bins.map(b => Number(b.lot_count || 0)));
  return (
    <div style={{ marginBottom: 12, border: "1px solid rgba(15,23,42,0.12)", borderRadius: 12, overflow: "hidden", background: "rgba(255,255,255,0.72)" }}>
      <div style={{ padding: "10px 12px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", gap: 10, flexWrap: "wrap", alignItems: "baseline" }}>
        <div style={{ fontSize: 11, fontWeight: 800, color: INDIGO.fg, fontFamily: "monospace" }}>
          KNOB 위치/속도
          <span style={{ marginLeft: 8, color: "var(--text-secondary)", fontWeight: 600 }}>{kp.knob_col} = {kp.knob_value || "(전체)"} · {kp.total_lots || 0} lots</span>
        </div>
        <div style={{ fontSize: 10, color: "var(--text-secondary)", fontFamily: "monospace" }}>
          {targetStepId ? `target ${targetStepId} ETA와 함께 비교` : "step별 현재 위치와 평균 h/step"}
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.4fr) minmax(240px, 0.6fr)", gap: 12, padding: 12 }}>
        <div style={{ display: "grid", gap: 6, maxHeight: 280, overflow: "auto" }}>
          {bins.length === 0 ? <div style={{ fontSize: 11, color: "var(--text-secondary)", fontFamily: "monospace" }}>해당 KNOB lot이 없습니다.</div> : bins.map((b) => {
            const w = Math.max(4, Math.min(100, (Number(b.lot_count || 0) / maxCount) * 100));
            const tone = b.stuck_lots > 0 ? BAD.fg : b.slow_lots > b.fast_lots ? WARN.fg : BLUE.fg;
            return (
              <div key={b.step_id} style={{ display: "grid", gridTemplateColumns: "118px minmax(160px,1fr) 88px", gap: 9, alignItems: "center", fontFamily: "monospace" }}>
                <div title={b.step_id} style={{ minWidth: 0, fontSize: 10, color: "var(--text-primary)", fontWeight: 700, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{b.step_id}</div>
                <div title={(b.root_lot_ids || []).join(", ")} style={{ height: 14, borderRadius: 4, background: "rgba(15,23,42,0.07)", overflow: "hidden", position: "relative" }}>
                  <div style={{ width: `${w}%`, height: "100%", background: tone, borderRadius: 4 }} />
                  <span style={{ position: "absolute", left: 6, top: 1, fontSize: 9, color: "rgba(255,255,255,0.96)", fontWeight: 800 }}>{b.lot_count} lots · {b.pct}%</span>
                </div>
                <div style={{ textAlign: "right", fontSize: 10, color: "var(--text-secondary)" }}>{fmt(b.avg_unit_hours)} h/step</div>
              </div>
            );
          })}
        </div>
        <div style={{ display: "grid", gap: 8 }}>
          <div style={{ fontSize: 10, fontWeight: 800, color: "var(--text-secondary)", fontFamily: "monospace" }}>빠른 bucket</div>
          {fastest.length === 0 ? <div style={{ fontSize: 10, color: "var(--text-secondary)" }}>비교 대상 없음</div> : fastest.map((b, idx) => (
            <div key={b.step_id} style={{ padding: "8px 9px", borderRadius: 8, border: "1px solid var(--border)", background: idx === 0 ? "rgba(37,99,235,0.07)" : "rgba(15,23,42,0.025)", fontFamily: "monospace" }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8, fontSize: 10, fontWeight: 800 }}>
                <span>{b.step_id}</span>
                <span style={{ color: BLUE.fg }}>{fmt(b.avg_unit_hours)}h</span>
              </div>
              <div style={{ marginTop: 3, fontSize: 9, color: "var(--text-secondary)" }}>{b.lot_count} lots · fast {b.fast_lots} / slow {b.slow_lots} / stuck {b.stuck_lots}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function FabProgressPanel({ loading, data, summary, speedFilter, setSpeedFilter, product, setProduct, products, targetStepId, setTargetStepId, lotQuery, setLotQuery, progressDays, setProgressDays, sampleLots, setSampleLots, knobCol, setKnobCol, knobValue, setKnobValue }) {
  const ok = !!data?.ok;
  const bench = data?.target_benchmark || {};
  const search = data?.search || {};
  const compare = data?.step_speed_compare || {};
  const knobProgress = data?.knob_progress || {};
  const hasCompare = !!compare?.target_root_lot_id;
  const rows = (data?.wip_lots || []).filter((row) => {
    if (speedFilter === "stuck") return row.speed_state === "stuck";
    if (speedFilter === "slow_stuck") return row.speed_state === "slow" || row.speed_state === "stuck";
    if (speedFilter === "fast") return row.speed_state === "fast";
    return true;
  });
  return (
    <div style={{ marginBottom: 14, background: "linear-gradient(135deg, rgba(14,116,144,0.04), rgba(37,99,235,0.05))", border: "1px solid rgba(37,99,235,0.15)", borderRadius: 14, padding: 16, boxShadow: "0 14px 36px rgba(15,23,42,0.05)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexWrap: "wrap", marginBottom: 12 }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 800, color: INDIGO.fg, fontFamily: "monospace" }}>FAB Progress / TAT / ETA</div>
          <div style={{ marginTop: 4, fontSize: 10, color: "var(--text-secondary)", fontFamily: "monospace" }}>
            특정 랏이 지금 어디까지 왔는지, 최근 평균 TAT 기준으로 다음 step 또는 target step 도착시점을 봅니다.
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <select value={product} onChange={(e) => setProduct(e.target.value)} style={{ padding: "7px 10px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg-card)", color: "var(--text-primary)", fontSize: 11 }}>
            {products.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
          <input value={lotQuery} onChange={(e) => setLotQuery(e.target.value)} placeholder="root_lot / fab_lot" style={{ padding: "7px 10px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg-card)", color: "var(--text-primary)", fontSize: 11, width: 150, fontFamily: "monospace" }} />
          <input value={targetStepId} onChange={(e) => setTargetStepId(e.target.value.toUpperCase())} placeholder="target step_id" style={{ padding: "7px 10px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg-card)", color: "var(--text-primary)", fontSize: 11, width: 132, fontFamily: "monospace" }} />
          <input value={knobCol} onChange={(e) => { setKnobCol(e.target.value); setKnobValue(""); }} placeholder="KNOB_5.0 PC" list="dashboard-knob-cols" style={{ padding: "7px 10px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg-card)", color: "var(--text-primary)", fontSize: 11, width: 140, fontFamily: "monospace" }} />
          <datalist id="dashboard-knob-cols">{(knobProgress.knob_options || []).map(k => <option key={k} value={k} />)}</datalist>
          <select value={knobValue} onChange={(e) => setKnobValue(e.target.value)} style={{ padding: "7px 10px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg-card)", color: "var(--text-primary)", fontSize: 11, maxWidth: 150 }}>
            <option value="">KNOB value auto</option>
            {(knobProgress.knob_values || []).map(v => <option key={v.value} value={v.value}>{v.value} ({v.count})</option>)}
          </select>
          <label style={{ display: "inline-flex", alignItems: "center", gap: 5, padding: "6px 8px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg-card)", color: "var(--text-secondary)", fontSize: 10, fontFamily: "monospace" }}>
            최근
            <input type="number" min={1} max={20} value={sampleLots}
              onChange={(e) => setSampleLots(Math.max(1, Math.min(20, Number(e.target.value) || 3)))}
              style={{ width: 42, border: "none", outline: "none", background: "transparent", color: "var(--text-primary)", fontSize: 11, fontFamily: "monospace" }} />
            lots
          </label>
          <select value={progressDays} onChange={(e) => setProgressDays(Number(e.target.value) || 30)} style={{ padding: "7px 10px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg-card)", color: "var(--text-primary)", fontSize: 11 }}>
            {[7, 14, 30, 60].map((d) => <option key={d} value={d}>{d}d</option>)}
          </select>
          <div style={{ display: "inline-flex", border: "1px solid var(--border)", borderRadius: 10, overflow: "hidden" }}>
            {[["all","전체"],["stuck","정체만"],["slow_stuck","느림+정체"],["fast","빠름만"]].map(([key,label])=>(
              <button key={key} onClick={()=>setSpeedFilter(key)} style={{ padding:"7px 10px", border:"none", background:speedFilter===key?"var(--accent)":"var(--bg-card)", color:speedFilter===key?"#fff":"var(--text-primary)", fontSize:11, cursor:"pointer", fontWeight:700 }}>{label}</button>
            ))}
          </div>
        </div>
      </div>
      {loading ? (
        <div style={{ fontSize: 11, color: "var(--text-secondary)", fontFamily: "monospace" }}>progress loading...</div>
      ) : !ok ? (
        <div style={{ padding: 14, borderRadius: 12, background: "rgba(255,255,255,0.65)", border: "1px dashed rgba(37,99,235,0.28)", fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.7 }}>
          {data?.note || "FAB long-format 데이터가 아직 없어 진행속도/TAT를 계산할 수 없습니다."}
        </div>
      ) : (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 10, marginBottom: 12 }}>
            <MiniStat label="TAT 7d(h)" value={fmt(summary?.tat_7d_hours ?? 0)} tone={INDIGO.fg} />
            <MiniStat label="TAT 30d(h)" value={fmt(summary?.tat_30d_hours ?? 0)} tone={TEAL.fg} />
            <MiniStat label="DPML 7d" value={fmt(summary?.dpml_7d ?? 0)} tone={WARN.fg} />
            <MiniStat label="DPML 30d" value={fmt(summary?.dpml_30d ?? 0)} tone={PURPLE.fg} />
            <MiniStat label="WIP Lots" value={summary?.wip_lots ?? 0} tone={BLUE.fg} />
            <MiniStat label="Stuck Lots" value={summary?.stuck_lots ?? 0} tone={BAD.fg} />
          </div>
          {lotQuery && <StepSpeedComparePanel compare={compare} targetStepId={targetStepId} sampleLots={sampleLots} />}
          <KnobProgressPanel data={data} targetStepId={targetStepId} />
          {targetStepId && !hasCompare && (
            <div style={{ marginBottom: 12, padding: "10px 12px", borderRadius: 12, border: "1px solid rgba(15,118,110,0.22)", background: "rgba(15,118,110,0.06)", display: "flex", gap: 14, flexWrap: "wrap", alignItems: "center", fontSize: 11, fontFamily: "monospace" }}>
              <b style={{ color: TEAL.fg }}>Target {bench.target_step_id || targetStepId}</b>
              <span>from {bench.from_step_id || "현재 step"}</span>
              <span>{bench.basis_label || `최근 ${sampleLots || 3} lots 평균`}</span>
              <span>samples {bench.samples ?? 0}/{bench.historical_samples ?? 0}</span>
              <span>avg {bench.avg_hours != null ? `${bench.avg_hours}h` : "-"}</span>
              <span>median {bench.median_hours != null ? `${bench.median_hours}h` : "-"}</span>
              <span>p10/p90 {bench.p10_hours != null ? `${bench.p10_hours}h / ${bench.p90_hours}h` : "-"}</span>
              {bench.note && <span style={{ color: "var(--text-secondary)" }}>{bench.note}</span>}
            </div>
          )}
          <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.55fr) minmax(340px, 0.85fr)", gap: 12 }}>
            <div style={{ border: "1px solid var(--border)", borderRadius: 12, overflow: "hidden", background: "rgba(255,255,255,0.7)" }}>
              <div style={{ padding: "10px 12px", fontSize: 11, fontWeight: 700, color: INDIGO.fg, borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", gap: 8 }}>
                <span>현재 진행 랏</span>
                <span style={{ color: "var(--text-secondary)", fontFamily: "monospace", fontWeight: 500 }}>검색 {search.matched_lots ?? rows.length}/{search.total_lots ?? rows.length}</span>
              </div>
              <div style={{ maxHeight: 520, overflow: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse" }}>
                  <thead>
                    <tr>
                      {["root_lot", "current", "speed", "tat", "ETA", "stuck", "path"].map((h) => (
                        <th key={h} style={{ textAlign: "left", padding: "8px 10px", fontSize: 10, fontFamily: "monospace", color: "var(--text-secondary)", background: "rgba(15,23,42,0.03)", position: "sticky", top: 0 }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row, idx) => (
                      <tr key={idx} style={{ borderTop: "1px solid var(--border)" }}>
                        <td style={{ padding: "8px 10px", fontSize: 11, fontFamily: "monospace", verticalAlign: "top" }}>
                          <div style={{ fontWeight: 700, color: "var(--text-primary)" }}>{row.root_lot_id}</div>
                          <div style={{ fontSize: 9, color: "var(--text-secondary)" }}>{row.current_fab_lot_id || row.fab_lot_id || "-"}</div>
                          {Array.isArray(row.fab_lot_ids) && row.fab_lot_ids.length > 1 && (
                            <div style={{ fontSize: 9, color: "var(--text-secondary)" }}>{row.fab_lot_ids.length} fab lots</div>
                          )}
                        </td>
                        <td style={{ padding: "8px 10px", fontSize: 11, fontFamily: "monospace", verticalAlign: "top" }}>
                          <div>{row.current_step_id || "-"}</div>
                          <div style={{ fontSize: 9, color: "var(--text-secondary)" }}>{timeAgo(row.current_time)} · {fmt(row.progress_pct)}%</div>
                        </td>
                        <td style={{ padding: "8px 10px", fontSize: 11, fontFamily: "monospace", verticalAlign: "top" }}>
                          <div style={{ fontWeight: 700, color: row.speed_state === "stuck" ? BAD.fg : row.speed_state === "slow" ? WARN.fg : row.speed_state === "fast" ? OK.fg : "var(--text-primary)" }}>{row.speed_badge || "-"}</div>
                          <div style={{ fontSize: 9, color: "var(--text-secondary)" }}>{fmt(row.speed_unit_hours)} h/step</div>
                        </td>
                        <td style={{ padding: "8px 10px", fontSize: 11, fontFamily: "monospace", verticalAlign: "top" }}>
                          <div>{fmt(row.elapsed_hours)} h</div>
                          <div style={{ fontSize: 9, color: "var(--text-secondary)" }}>{row.progress_steps} steps</div>
                        </td>
                        <td style={{ padding: "8px 10px", fontSize: 11, fontFamily: "monospace", verticalAlign: "top" }}>
                          <div>{row.target_eta?.target_step_id ? `target ${row.target_eta.target_step_id}` : (row.eta?.next_step_id || "-")}</div>
                          <div style={{ fontSize: 9, color: (row.target_eta?.eta_at || row.eta?.eta_at) ? TEAL.fg : "var(--text-secondary)" }}>
                            {row.target_eta?.status === "reached"
                              ? `도착 완료 · ${new Date(row.target_eta.actual_time || row.target_eta.eta_at).toLocaleString()}`
                              : row.target_eta?.eta_at
                                ? `${new Date(row.target_eta.eta_at).toLocaleString()} · avg ${fmt(row.target_eta.avg_hours)}h · n=${row.target_eta.samples ?? 0}`
                                : row.target_eta?.status === "insufficient_history"
                                  ? `최근 ${sampleLots || 3} lots 이력 부족`
                                  : row.eta?.eta_at ? new Date(row.eta.eta_at).toLocaleString() : "-"}
                          </div>
                        </td>
                        <td style={{ padding: "8px 10px", fontSize: 11, fontFamily: "monospace", verticalAlign: "top" }}>
                          <div>{fmt(row.stuck_hours)} h</div>
                          <div style={{ fontSize: 9, color: row.stuck_hours >= 24 ? BAD.fg : "var(--text-secondary)" }}>{row.target_eta?.target_step_id || targetStepId || "-"}</div>
                        </td>
                        <td style={{ padding: "8px 10px", minWidth: 180 }}><ProgressSparkline path={row.path} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            <div style={{ display: "grid", gap: 12 }}>
              <div style={{ border: "1px solid var(--border)", borderRadius: 12, overflow: "hidden", background: "rgba(255,255,255,0.7)" }}>
                <div style={{ padding: "10px 12px", fontSize: 11, fontWeight: 700, color: INDIGO.fg, borderBottom: "1px solid var(--border)" }}>주요 Step TAT</div>
                <div style={{ maxHeight: 152, overflow: "auto" }}>
                  <table style={{ width: "100%", borderCollapse: "collapse" }}>
                    <thead>
                      <tr>
                        {["from", "to", "avg(h)", "samples"].map((h) => (
                          <th key={h} style={{ textAlign: "left", padding: "8px 10px", fontSize: 10, fontFamily: "monospace", color: "var(--text-secondary)", background: "rgba(15,23,42,0.03)", position: "sticky", top: 0 }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {(data?.step_tat || []).slice(0, 8).map((row, idx) => (
                        <tr key={idx} style={{ borderTop: "1px solid var(--border)" }}>
                          <td style={{ padding: "8px 10px", fontSize: 10, fontFamily: "monospace" }}>{row.from_step}</td>
                          <td style={{ padding: "8px 10px", fontSize: 10, fontFamily: "monospace" }}>{row.to_step}</td>
                          <td style={{ padding: "8px 10px", fontSize: 10, fontFamily: "monospace" }}>{row.avg_hours}</td>
                          <td style={{ padding: "8px 10px", fontSize: 10, fontFamily: "monospace" }}>{row.samples}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
              <div style={{ border: "1px solid var(--border)", borderRadius: 12, overflow: "hidden", background: "rgba(255,255,255,0.7)" }}>
                <div style={{ padding: "10px 12px", fontSize: 11, fontWeight: 700, color: INDIGO.fg, borderBottom: "1px solid var(--border)" }}>최근 진행 Path</div>
                <div style={{ maxHeight: 156, overflow: "auto", padding: 10, display: "grid", gap: 8 }}>
                  {(data?.recent_paths || []).slice(0, 4).map((row, idx) => (
                    <div key={idx} style={{ padding: 10, borderRadius: 10, border: "1px solid var(--border)", background: "rgba(15,23,42,0.02)" }}>
                      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, fontSize: 10, fontFamily: "monospace" }}>
                        <span style={{ color: "var(--text-primary)", fontWeight: 700 }}>{row.root_lot_id}</span>
                        <span style={{ color: "var(--text-secondary)" }}>{row.current_step_id}</span>
                      </div>
                      <div style={{ marginTop: 6, fontSize: 9, color: "var(--text-secondary)", lineHeight: 1.6 }}>
                        {(row.path || []).map((p) => p.step_id).join(" → ")}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function TrendAlertPanel({ loading, alerts }) {
  const bad = statusPalette.bad;
  return (
    <div style={{ marginBottom: 14, background: "linear-gradient(135deg, rgba(220,38,38,0.03), rgba(249,115,22,0.05))", border: "1px solid rgba(239,68,68,0.14)", borderRadius: 14, padding: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, flexWrap: "wrap", marginBottom: 12 }}>
        <div>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <div style={{ fontSize: 13, fontWeight: 800, color: bad.fg, fontFamily: "monospace" }}>Trend Alert Watch</div>
          </div>
          <div style={{ marginTop: 4, fontSize: 10, color: "var(--text-secondary)", fontFamily: "monospace" }}>
            scatter/line/area/combo 차트의 OOS/IQR 후보를 기준으로 주의가 필요한 trend를 보여줍니다.
          </div>
        </div>
      </div>
      {loading ? (
        <div style={{ fontSize: 11, color: "var(--text-secondary)", fontFamily: "monospace" }}>trend alerts loading...</div>
      ) : !alerts?.length ? (
        <div style={{ fontSize: 11, color: "var(--text-secondary)", fontFamily: "monospace" }}>현재 눈에 띄는 trend 이상치 후보가 없습니다.</div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 10 }}>
          {alerts.map((row) => (
            <div key={row.chart_id} style={{ padding: 12, borderRadius: 12, border: "1px solid rgba(239,68,68,0.16)", background: "rgba(255,255,255,0.72)" }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline" }}>
                <div style={{ fontSize: 11, fontWeight: 800, color: bad.fg }}>{row.title}</div>
                <div style={{ fontSize: 9, color: "var(--text-secondary)", fontFamily: "monospace" }}>{timeAgo(row.computed_at)}</div>
              </div>
              <div style={{ marginTop: 6, display: "flex", gap: 6, flexWrap: "wrap" }}>
                <Pill tone="info" size="sm">{row.group}</Pill>
                <Pill tone="bad" size="sm">OOS {row.oos_count}</Pill>
                <Pill tone="warn" size="sm">Outlier {row.trend_outliers}</Pill>
              </div>
              {!!row.latest_points?.length && (
                <div style={{ marginTop: 8, display: "grid", gap: 4 }}>
                  {row.latest_points.slice(0, 3).map((p, idx) => (
                    <div key={idx} style={{ display: "flex", justifyContent: "space-between", gap: 8, fontSize: 9, fontFamily: "monospace", color: "var(--text-secondary)" }}>
                      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>x={String(p.x ?? "-")}</span>
                      <span>y={fmt(p.y)}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function DashboardSectionNav({ view, setView, counts, sections }) {
  const items = [
    { key: "charts", label: "차트", hint: `${counts.charts}개 차트` },
    { key: "progress", label: "FAB 진행", hint: `${counts.products}개 제품` },
    { key: "alerts", label: "알림 감시", hint: `${counts.alerts}개 후보` },
  ].filter(item => sections?.[item.key] !== false);
  if (!items.length) return null;
  return (
    <div style={{ marginBottom: 14 }}>
      <TabStrip
        active={view}
        onChange={setView}
        items={items.map((item) => ({ k: item.key, l: item.label, badge: item.hint }))}
      />
    </div>
  );
}

/* ═══ Interactive SVG Chart ═══ */
function ChartCanvas({ cfg, points, computedAt }) {
  // v7.2: cross-chart marks
  const { marks, toggle: toggleMark } = useContext(SelectionContext);
  const hasAnyMark = marks && marks.size > 0;
  const [tip, setTip] = useState(null);
  const svgRef = useRef(null);
  const rawType = cfg.chart_type || "scatter";
  const type = rawType === "step_knob_binning" ? "combo" : rawType;
  points = Array.isArray(points) ? points : [];
  if (!points.length && type !== "wafer_map") return <div style={{ padding: 40, textAlign: "center", color: "var(--text-secondary)", fontSize: 12 }}>데이터 없음</div>;
  const title = cfg.title; const xL = cfg.x_label || cfg.x_col; const yL = cfg.y_label || cfg.y_expr;
  const ptSize = cfg.point_size || 3; const ptOpacity = cfg.opacity || 0.7;

  const Tip = tip ? <div style={{ position: "absolute", left: tip.x + 12, top: tip.y - 10, background: MUTED_DARK, border: "1px solid var(--border)", borderRadius: 6, padding: "6px 10px", fontSize: 11, color: SOFT_TEXT, pointerEvents: "none", zIndex: 10, maxWidth: 280, whiteSpace: "pre-wrap", lineHeight: 1.5, boxShadow: "0 4px 12px rgba(0,0,0,0.5)" }}>
    {tip.lines.map((l, i) => <div key={i}>{l}</div>)}
  </div> : null;

  const Header = <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px 12px", margin: "-12px -14px 8px", borderRadius: "8px 8px 0 0", background: "rgba(15,23,42,0.035)", borderBottom: "1px solid var(--border)" }}>
    {title && <div style={{ fontSize: 12, fontWeight: 800, fontFamily: "monospace", color: "var(--text-primary)" }}>{title}</div>}
    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
      {cfg._oos > 0 && <span style={{ fontSize: 9, fontWeight: 700, color: WHITE, background: BAD.fg, borderRadius: 4, padding: "1px 5px" }}>OOS: {cfg._oos}</span>}
      <span style={{ fontSize: 9, color: "var(--text-secondary)" }}>{points.length.toLocaleString()} 개 점</span>
      {computedAt && <span style={{ fontSize: 8, color: "var(--text-secondary)" }}>{timeAgo(computedAt)}</span>}
    </div>
  </div>;

  /* ── Table (simple row viewer) ── */
  if (type === "table") {
    const cols = cfg.table_columns || (points[0] ? Object.keys(points[0]) : []);
    const tS = { padding: "4px 8px", fontSize: 10, fontFamily: "monospace", borderBottom: "1px solid var(--border)", textAlign: "left", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: 160 };
    return (<div style={{ overflow: "hidden", background: "var(--bg-card)", borderRadius: 8, border: "1px solid var(--border)", padding: "12px 14px", position: "relative" }}>
      {Header}
      <div style={{ overflow: "auto", maxHeight: 280 }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead><tr>{cols.map(c => <th key={c} style={{ ...tS, background: "var(--bg-tertiary)", color: "var(--accent)", fontWeight: 700, position: "sticky", top: 0 }}>{c}</th>)}</tr></thead>
          <tbody>{points.map((r, i) => (
            <tr key={i} style={{ background: i % 2 ? "var(--bg-primary)" : "transparent" }}>
              {cols.map(c => <td key={c} style={{ ...tS, color: "var(--text-primary)" }} title={String(r[c] ?? "")}>{r[c] == null ? "" : String(r[c])}</td>)}
            </tr>
          ))}</tbody>
        </table>
      </div>
    </div>);
  }

  /* ── Cross Table (pivot) ── */
  if (type === "cross_table") {
    const cols = cfg.cross_cols || [];
    const rowCol = cfg.x_col, colCol = cfg.y_expr;
    const valCol = cfg.agg_col || "";
    const method = cfg.cross_method || "count";
    // Color scale: find max value for heat coloring
    const allVals = points.flatMap(r => cols.map(c => r[c])).filter(v => typeof v === "number");
    const maxV = allVals.length ? Math.max(...allVals) : 1;
    const minV = allVals.length ? Math.min(...allVals) : 0;
    const heatColor = (v) => {
      if (typeof v !== "number") return "transparent";
      const t = (v - minV) / (maxV - minV || 1);
      const r = Math.round(50 + 200 * t), g = Math.round(100 - 40 * t), b = Math.round(220 - 180 * t);
      return `rgba(${r},${g},${b},0.25)`;
    };
    const tS = { padding: "4px 8px", fontSize: 11, fontFamily: "monospace", borderBottom: "1px solid var(--border)", borderRight: "1px solid var(--border)", textAlign: "center", whiteSpace: "nowrap" };
    return (<div style={{ overflow: "hidden", background: "var(--bg-card)", borderRadius: 8, border: "1px solid var(--border)", padding: "12px 14px", position: "relative" }}>
      {Header}
      <div style={{ fontSize: 9, color: "var(--text-secondary)", marginBottom: 6, fontFamily: "monospace" }}>
        {method}{valCol ? "(" + valCol + ")" : ""} | rows: {rowCol} | cols: {colCol}
      </div>
      <div style={{ overflow: "auto", maxHeight: 280 }}>
        <table style={{ borderCollapse: "collapse" }}>
          <thead><tr>
            <th style={{ ...tS, background: "var(--bg-tertiary)", color: "var(--accent)", fontWeight: 700, textAlign: "left", position: "sticky", top: 0, left: 0, zIndex: 3 }}>{rowCol} \ {colCol}</th>
            {cols.map(c => <th key={c} style={{ ...tS, background: "var(--bg-tertiary)", color: "var(--text-primary)", fontWeight: 700, position: "sticky", top: 0 }}>{c}</th>)}
            <th style={{ ...tS, background: "var(--bg-tertiary)", color: "var(--accent)", fontWeight: 700, position: "sticky", top: 0 }}>합계</th>
          </tr></thead>
          <tbody>{points.map((r, i) => (
            <tr key={i}>
              <td style={{ ...tS, background: "var(--bg-secondary)", color: "var(--accent)", fontWeight: 600, textAlign: "left", position: "sticky", left: 0, zIndex: 2 }}>{r._row}</td>
              {cols.map(c => {
                const v = r[c];
                return <td key={c} style={{ ...tS, background: heatColor(v), color: "var(--text-primary)" }}>{v == null ? "-" : (typeof v === "number" ? (Number.isInteger(v) ? v : v.toFixed(2)) : v)}</td>;
              })}
              <td style={{ ...tS, background: "var(--bg-tertiary)", color: "var(--accent)", fontWeight: 700 }}>{r._total == null ? "-" : (typeof r._total === "number" ? (Number.isInteger(r._total) ? r._total : r._total.toFixed(2)) : r._total)}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </div>);
  }

  /* ── Pie ── */
  if (type === "pie") {
    const chartPoints = points.map(p => ({ ...p, y: num(p.y) }));
    const total = chartPoints.reduce((s, p) => s + p.y, 0) || 1;
    const R = 110, cx = 140, cy = 140; let acc = 0;
    const slices = chartPoints.map((p, i) => {
      const frac = p.y / total; const a0 = acc * 2 * Math.PI; acc += frac; const a1 = acc * 2 * Math.PI;
      const mid = (a0 + a1) / 2;
      return { ...p, frac, a0, a1, lx: cx + R * 0.65 * Math.sin(mid), ly: cy - R * 0.65 * Math.cos(mid),
        x0: cx + R * Math.sin(a0), y0: cy - R * Math.cos(a0), x1: cx + R * Math.sin(a1), y1: cy - R * Math.cos(a1),
        color: PASTELS[i % PASTELS.length], i };
    });
    return (<div style={{ background: "var(--bg-card)", borderRadius: 8, border: "1px solid var(--border)", padding: "14px 16px", position: "relative" }}>
      {Header}{Tip}
      <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
        <svg width={280} height={280} viewBox="0 0 280 280" ref={svgRef}
          onMouseLeave={() => setTip(null)}>
          {slices.map(s => (<g key={s.i}>
            <path d={`M ${cx},${cy} L ${s.x0},${s.y0} A ${R},${R} 0 ${s.frac > 0.5 ? 1 : 0} 1 ${s.x1},${s.y1} Z`}
              fill={s.color} stroke="var(--bg-card)" strokeWidth={2} opacity={0.9}
              onMouseMove={e => { const r = svgRef.current.getBoundingClientRect(); setTip({ x: e.clientX - r.left, y: e.clientY - r.top, lines: [`${s.label}`, `개수: ${s.y.toLocaleString()}`, `비율: ${pct(s.frac)}%`] }); }}
              style={{ cursor: "pointer" }} />
            {s.frac > 0.04 && <text x={s.lx} y={s.ly} textAnchor="middle" dominantBaseline="middle" fill={WHITE} fontSize={s.frac > 0.1 ? 12 : 10} fontWeight={700} style={{ textShadow: "0 1px 3px rgba(0,0,0,0.6)", pointerEvents: "none" }}>{pct(s.frac)}%</text>}
          </g>))}
        </svg>
        <div style={{ flex: 1, overflow: "auto", maxHeight: 260 }}>
          {slices.map(s => (<div key={s.i} style={{ display: "flex", alignItems: "center", gap: 8, padding: "3px 0", borderBottom: "1px solid var(--border)" }}>
            <span style={{ width: 10, height: 10, borderRadius: 2, background: s.color, flexShrink: 0 }} />
            <span style={{ flex: 1, fontSize: 11, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={s.label}>{s.label}</span>
            <span style={{ fontSize: 10, color: "var(--text-secondary)", fontFamily: "monospace", flexShrink: 0 }}>{s.y.toLocaleString()}</span>
            <span style={{ fontSize: 10, fontWeight: 600, color: s.color, flexShrink: 0, minWidth: 42, textAlign: "right" }}>{pct(s.frac)}%</span>
          </div>))}
          <div style={{ fontSize: 10, color: "var(--text-secondary)", padding: "6px 0", fontWeight: 600 }}>합계: {total.toLocaleString()}</div>
        </div>
      </div>
    </div>);
  }

  /* ── Binning / Histogram ── */
  if (type === "binning") {
    const chartPoints = points.map(p => ({ ...p, y: num(p.y) }));
    const maxY = Math.max(...chartPoints.map(p => p.y));
    const total = chartPoints.reduce((s, p) => s + p.y, 0) || 1;
    const W = Math.max(400, Math.min(600, points.length * 48)), H = 300, pad = { t: 24, r: 16, b: 56, l: 56 };
    const cw = W - pad.l - pad.r, ch = H - pad.t - pad.b, bw = Math.max(4, cw / points.length - 2);
    return (<div style={{ overflow: "hidden", background: "var(--bg-card)", borderRadius: 8, border: "1px solid var(--border)", padding: "14px 16px", position: "relative" }}>
      {Header}{Tip}
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" ref={svgRef} onMouseLeave={() => setTip(null)} style={{ display: "block" }}>
        {[0, 0.25, 0.5, 0.75, 1].map((f, i) => { const y = pad.t + ch * (1 - f); return (<g key={i}><line x1={pad.l} y1={y} x2={W - pad.r} y2={y} stroke="var(--border)" strokeDasharray="2,3" /><text x={pad.l - 6} y={y + 3} textAnchor="end" fill="var(--text-secondary)" fontSize={9}>{Math.round(maxY * f)}</text></g>); })}
        {chartPoints.map((p, i) => {
          const x = pad.l + i * (cw / points.length) + (cw / points.length - bw) / 2;
          const barH = maxY > 0 ? (p.y / maxY) * ch : 0; const pctText = fixed((p.y / total) * 100, 1, "0.0");
          return (<g key={i}>
            <rect x={x} y={pad.t + ch - barH} width={bw} height={Math.max(1, barH)} fill={SERIES[i % SERIES.length]} rx={2} opacity={0.85}
              onMouseMove={e => { const r = svgRef.current.getBoundingClientRect(); setTip({ x: e.clientX - r.left, y: e.clientY - r.top, lines: [`${p.label || p.x}`, `개수: ${p.y.toLocaleString()}`, `전체의 ${pctText}%`] }); }}
              style={{ cursor: "pointer" }} />
            {barH > 16 && <text x={x + bw / 2} y={pad.t + ch - barH + 13} textAnchor="middle" fill={WHITE} fontSize={9} fontWeight={600} style={{ pointerEvents: "none" }}>{p.y}</text>}
            {barH > 30 && <text x={x + bw / 2} y={pad.t + ch - barH + 25} textAnchor="middle" fill="rgba(255,255,255,0.6)" fontSize={8} style={{ pointerEvents: "none" }}>{pctText}%</text>}
            <text x={x + bw / 2} y={H - pad.b + 6} textAnchor="end" fill="var(--text-secondary)" fontSize={8} transform={`rotate(-90,${x + bw / 2},${H - pad.b + 6})`}>{(p.x || "").slice(0, 12)}</text>
          </g>);
        })}
        {xL && <text x={pad.l + cw / 2} y={H - 4} textAnchor="middle" fill="var(--text-secondary)" fontSize={10}>{xL}</text>}
      </svg>
    </div>);
  }

  /* ── Donut ── */
  if (type === "donut") {
    const chartPoints = points.map(p => ({ ...p, y: num(p.y) }));
    const total = chartPoints.reduce((s, p) => s + p.y, 0) || 1;
    const R = 90, IR = 50, cx = 120, cy = 120; let acc = 0;
    const slices = chartPoints.map((p, i) => {
      const frac = p.y / total; const a0 = acc * 2 * Math.PI; acc += frac; const a1 = acc * 2 * Math.PI;
      const mid = (a0 + a1) / 2;
      return { ...p, frac, color: PASTELS[i % PASTELS.length], i,
        outerX0: cx + R * Math.sin(a0), outerY0: cy - R * Math.cos(a0), outerX1: cx + R * Math.sin(a1), outerY1: cy - R * Math.cos(a1),
        innerX0: cx + IR * Math.sin(a0), innerY0: cy - IR * Math.cos(a0), innerX1: cx + IR * Math.sin(a1), innerY1: cy - IR * Math.cos(a1),
        lx: cx + (R + IR) / 2 * 0.9 * Math.sin(mid), ly: cy - (R + IR) / 2 * 0.9 * Math.cos(mid) };
    });
    return (<div style={{ background: "var(--bg-card)", borderRadius: 8, border: "1px solid var(--border)", padding: "12px 14px", position: "relative" }}>
      {Header}{Tip}
      <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
        <svg width={240} height={240} viewBox="0 0 240 240" ref={svgRef} onMouseLeave={() => setTip(null)}>
          {slices.map(s => <path key={s.i}
            d={`M ${s.outerX0},${s.outerY0} A ${R},${R} 0 ${s.frac > 0.5 ? 1 : 0} 1 ${s.outerX1},${s.outerY1} L ${s.innerX1},${s.innerY1} A ${IR},${IR} 0 ${s.frac > 0.5 ? 1 : 0} 0 ${s.innerX0},${s.innerY0} Z`}
            fill={s.color} stroke="var(--bg-card)" strokeWidth={1.5} opacity={0.9}
            onMouseMove={e => { const r = svgRef.current.getBoundingClientRect(); setTip({ x: e.clientX - r.left, y: e.clientY - r.top, lines: [s.label, `${s.y.toLocaleString()} (${pct(s.frac)}%)`] }); }}
            style={{ cursor: "pointer" }} />)}
          <text x={cx} y={cy - 4} textAnchor="middle" fill="var(--text-secondary)" fontSize={10}>합계</text>
          <text x={cx} y={cy + 12} textAnchor="middle" fill="var(--text-primary)" fontSize={14} fontWeight={700}>{total.toLocaleString()}</text>
        </svg>
        <div style={{ flex: 1, overflow: "auto", maxHeight: 220, fontSize: 11 }}>
          {slices.map(s => (<div key={s.i} style={{ display: "flex", alignItems: "center", gap: 6, padding: "2px 0" }}>
            <span style={{ width: 8, height: 8, borderRadius: 2, background: s.color, flexShrink: 0 }} />
            <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{s.label}</span>
            <span style={{ color: "var(--text-secondary)", fontFamily: "monospace", fontSize: 10 }}>{s.y}</span>
            <span style={{ color: s.color, fontWeight: 600, fontSize: 10, minWidth: 36, textAlign: "right" }}>{pct(s.frac)}%</span>
          </div>))}
        </div>
      </div>
    </div>);
  }

  /* ── Pareto ── */
  if (type === "pareto") {
    const chartPoints = points.map(p => ({ ...p, y: num(p.y) }));
    const maxY = Math.max(...chartPoints.map(p => p.y));
    const total = chartPoints.reduce((s, p) => s + p.y, 0) || 1;
    const W = Math.max(400, Math.min(600, points.length * 44)), H = 260, pad = { t: 20, r: 40, b: 50, l: 50 };
    const cw = W - pad.l - pad.r, ch = H - pad.t - pad.b, bw = Math.max(4, cw / points.length - 2);
    let cum = 0;
    return (<div style={{ overflow: "hidden", background: "var(--bg-card)", borderRadius: 8, border: "1px solid var(--border)", padding: "12px 14px", position: "relative" }}>
      {Header}{Tip}
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" ref={svgRef} onMouseLeave={() => setTip(null)} style={{ display: "block" }}>
        {[0, 0.5, 1].map((f, i) => { const y = pad.t + ch * (1 - f); return <g key={i}><line x1={pad.l} y1={y} x2={W - pad.r} y2={y} stroke="var(--border)" strokeDasharray="2,3" /><text x={pad.l - 4} y={y + 3} textAnchor="end" fill="var(--text-secondary)" fontSize={8}>{Math.round(maxY * f)}</text></g>; })}
        {chartPoints.map((p, i) => {
          const x = pad.l + i * (cw / points.length) + (cw / points.length - bw) / 2;
          const barH = maxY > 0 ? (p.y / maxY) * ch : 0;
          cum += p.y; const cumPct = cum / total * 100;
          const lineY = pad.t + ch * (1 - cumPct / 100);
          return (<g key={i}>
            <rect x={x} y={pad.t + ch - barH} width={bw} height={Math.max(1, barH)} fill={SERIES[i % SERIES.length]} rx={2} opacity={0.8}
              onMouseMove={e => { const r = svgRef.current.getBoundingClientRect(); setTip({ x: e.clientX - r.left, y: e.clientY - r.top, lines: [p.label || p.x, `개수: ${p.y}`, `누적: ${fixed(cumPct, 1, "0.0")}%`] }); }} style={{ cursor: "pointer" }} />
            <circle cx={x + bw / 2} cy={lineY} r={3} fill={BAD.fg} />
            {i > 0 && <line x1={pad.l + (i - 1) * (cw / points.length) + (cw / points.length) / 2} y1={pad.t + ch * (1 - (cum - p.y) / total)} x2={x + bw / 2} y2={lineY} stroke={BAD.fg} strokeWidth={1.5} />}
            <text x={x + bw / 2} y={H - pad.b + 6} textAnchor="end" fill="var(--text-secondary)" fontSize={7} transform={`rotate(-90,${x + bw / 2},${H - pad.b + 6})`}>{(p.x || "").slice(0, 10)}</text>
          </g>);
        })}
        {/* Right axis: cum% */}
        {[0, 50, 80, 100].map(pct => <text key={pct} x={W - pad.r + 4} y={pad.t + ch * (1 - pct / 100) + 3} fill={BAD.fg} fontSize={8}>{pct}%</text>)}
      </svg>
    </div>);
  }

  /* ── Box Plot with Statistics Table (chart above, stats aligned below) ── */
  if (type === "box") {
    const allVals = points.flatMap(p => [p.min, p.q1, p.median, p.q3, p.max].map(maybeNum).filter(v => v != null));
    if (!allVals.length) return <div style={{ padding: 40, textAlign: "center", color: "var(--text-secondary)", fontSize: 12 }}>숫자 통계 데이터 없음</div>;
    const minV = Math.min(...allVals), maxV = Math.max(...allVals), rangeV = maxV - minV || 1;
    const colW = Math.max(80, Math.min(120, 500 / points.length));
    const padL = 54, padR = 16;
    const W = padL + points.length * colW + padR, H = 220;
    const pad = { t: 20, r: padR, b: 24, l: padL };
    const ch = H - pad.t - pad.b;
    const toY = v => pad.t + ch - (num(v, minV) - minV) / rangeV * ch;
    const bw = Math.min(44, colW * 0.55);
    const statRows = [
      { label: "N", key: "count", fmt: v => v },
      { label: "Mean", key: "mean", fmt: v => fixed(v, 4) },
      { label: "Median", key: "median", fmt: v => fixed(v, 4) },
      { label: "Std Dev", key: "std", fmt: v => fixed(v, 4) },
      { label: "Min", key: "min", fmt: v => fixed(v, 4) },
      { label: "P10", key: "p10", fmt: v => fixed(v, 4) },
      { label: "Q1", key: "q1", fmt: v => fixed(v, 4) },
      { label: "Q3", key: "q3", fmt: v => fixed(v, 4) },
      { label: "P90", key: "p90", fmt: v => fixed(v, 4) },
      { label: "Max", key: "max", fmt: v => fixed(v, 4) },
    ];
    const tS = { padding: "2px 4px", borderBottom: "1px solid var(--border)", fontSize: 10, textAlign: "center", fontFamily: "monospace", overflow: "hidden" };
    return (<div style={{ overflow: "hidden", background: "var(--bg-card)", borderRadius: 8, border: "1px solid var(--border)", padding: "12px 14px", position: "relative" }}>
      {Header}{Tip}
      {/* Wrapper: SVG + table share identical pixel width */}
      <div style={{ width: W, maxWidth: "100%", margin: "0 auto" }}>
      <svg width={W} height={H} ref={svgRef} onMouseLeave={() => setTip(null)} style={{ display: "block" }}>
        <line x1={pad.l} y1={pad.t} x2={pad.l} y2={pad.t + ch} stroke="var(--text-secondary)" strokeWidth={0.5} opacity={0.4} />
        <line x1={pad.l} y1={pad.t + ch} x2={W - pad.r} y2={pad.t + ch} stroke="var(--text-secondary)" strokeWidth={0.5} opacity={0.4} />
        {[0, 0.25, 0.5, 0.75, 1].map((f, i) => { const y = pad.t + ch * (1 - f); const v = minV + rangeV * f; return <g key={i}><line x1={pad.l} y1={y} x2={W - pad.r} y2={y} stroke="var(--border)" strokeDasharray="2,3" opacity={0.4} /><text x={pad.l - 6} y={y + 3} textAnchor="end" fill="var(--text-secondary)" fontSize={9}>{fmt(v)}</text></g>; })}
        {points.map((p, i) => {
          const cx = pad.l + (i + 0.5) * colW;
          return (<g key={i}
            onMouseMove={e => { const r = svgRef.current.getBoundingClientRect(); setTip({ x: e.clientX - r.left, y: e.clientY - r.top, lines: [`${p.x}`, `N: ${p.count}`, `Mean: ${fmt(p.mean)}`, `Med: ${fmt(p.median)}`, `Std: ${fmt(p.std)}`, `P10: ${fmt(p.p10)}`, `P90: ${fmt(p.p90)}`] }); }}
            style={{ cursor: "pointer" }}>
            <line x1={cx} y1={toY(p.min)} x2={cx} y2={toY(p.q1)} stroke={SERIES[i % SERIES.length]} strokeWidth={1} />
            <line x1={cx} y1={toY(p.q3)} x2={cx} y2={toY(p.max)} stroke={SERIES[i % SERIES.length]} strokeWidth={1} />
            <line x1={cx - bw / 3} y1={toY(p.min)} x2={cx + bw / 3} y2={toY(p.min)} stroke={SERIES[i % SERIES.length]} strokeWidth={1.5} />
            <line x1={cx - bw / 3} y1={toY(p.max)} x2={cx + bw / 3} y2={toY(p.max)} stroke={SERIES[i % SERIES.length]} strokeWidth={1.5} />
            <rect x={cx - bw / 2} y={toY(p.q3)} width={bw} height={Math.max(1, toY(p.q1) - toY(p.q3))} fill={SERIES[i % SERIES.length] + "44"} stroke={SERIES[i % SERIES.length]} strokeWidth={1.5} rx={2} />
            <line x1={cx - bw / 2} y1={toY(p.median)} x2={cx + bw / 2} y2={toY(p.median)} stroke={WHITE} strokeWidth={2.5} />
            <circle cx={cx} cy={toY(p.mean)} r={3} fill={WHITE} stroke={SERIES[i % SERIES.length]} strokeWidth={1} />
            <text x={cx} y={H - 4} textAnchor="middle" fill={SERIES[i % SERIES.length]} fontSize={10} fontWeight={600}>{(p.x || "").slice(0, 10)}</text>
          </g>);
        })}
        {yL && <text x={10} y={pad.t + ch / 2} transform={`rotate(-90,10,${pad.t + ch / 2})`} fill="var(--accent)" fontSize={12} fontWeight={700} textAnchor="middle" style={{ fontFamily: "monospace" }}>{yL}</text>}
      </svg>
      <table style={{ borderCollapse: "collapse", tableLayout: "fixed", width: W, border: "1px solid var(--border)" }}>
        <colgroup>
          <col style={{ width: padL }} />
          {points.map((_, i) => <col key={i} style={{ width: colW }} />)}
          <col style={{ width: padR }} />
        </colgroup>
        <thead><tr>
          <th style={{ ...tS, textAlign: "left", background: "var(--bg-tertiary)", fontWeight: 700, fontSize: 9, color: "var(--text-secondary)" }}>통계</th>
          {points.map((p, i) => <th key={i} style={{ ...tS, background: "var(--bg-tertiary)", fontWeight: 700, color: SERIES[i % SERIES.length], fontSize: 10 }}>{p.x}</th>)}
          <th style={{ ...tS, background: "var(--bg-tertiary)" }}></th>
        </tr></thead>
        <tbody>{statRows.map(sr => (
          <tr key={sr.label}>
            <td style={{ ...tS, textAlign: "left", fontWeight: 600, color: "var(--text-secondary)", fontSize: 9, background: "var(--bg-primary)" }}>{sr.label}</td>
            {points.map((p, i) => <td key={i} style={{ ...tS, color: sr.label === "Mean" || sr.label === "Median" ? "var(--accent)" : "var(--text-primary)" }}>{p[sr.key] != null ? sr.fmt(p[sr.key]) : "-"}</td>)}
            <td style={{ ...tS }}></td>
          </tr>
        ))}</tbody>
      </table>
      </div>{/* end wrapper */}
    </div>);
  }

  /* ── Treemap (div-based, no overflow) ── */
  if (type === "treemap") {
    const chartPoints = points.map(p => ({ ...p, y: num(p.y) }));
    const total = chartPoints.reduce((s, p) => s + p.y, 0) || 1;
    const H = 220;
    const sorted = [...chartPoints].sort((a, b) => b.y - a.y).map((p, i) => ({ ...p, pct: p.y / total * 100, color: PASTELS[i % PASTELS.length], i }));
    return (<div style={{ background: "var(--bg-card)", borderRadius: 8, border: "1px solid var(--border)", padding: "12px 14px", position: "relative", overflow: "hidden" }}>
      {Header}{Tip}
      <div ref={svgRef} onMouseLeave={() => setTip(null)} style={{ display: "flex", height: H, borderRadius: 6, overflow: "hidden", gap: 2 }}>
        {sorted.map((r, i) => (
          <div key={i} style={{ flex: `${r.pct} 0 0%`, minWidth: 2, height: "100%", background: r.color, borderRadius: 4, opacity: 0.88, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", cursor: "pointer", overflow: "hidden", position: "relative" }}
            onMouseMove={e => { const b = svgRef.current.getBoundingClientRect(); setTip({ x: e.clientX - b.left, y: e.clientY - b.top, lines: [r.label, `${r.y.toLocaleString()} (${fixed(r.pct, 1, "0.0")}%)`] }); }}>
            {r.pct > 8 && <div style={{ color: WHITE, fontSize: Math.min(13, Math.max(9, r.pct / 3)), fontWeight: 700, textShadow: "0 1px 3px rgba(0,0,0,0.6)", pointerEvents: "none", textAlign: "center", padding: "0 4px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: "100%" }}>{(r.label || "").slice(0, 12)}</div>}
            {r.pct > 6 && <div style={{ color: "rgba(255,255,255,0.8)", fontSize: 9, fontWeight: 600, pointerEvents: "none" }}>{fixed(r.pct, 1, "0.0")}%</div>}
          </div>
        ))}
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 6 }}>
        {sorted.map((r, i) => <span key={i} style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 10 }}>
          <span style={{ width: 8, height: 8, borderRadius: 2, background: r.color, flexShrink: 0 }} />
          <span style={{ color: "var(--text-secondary)" }}>{r.label} ({fixed(r.pct, 1, "0.0")}%)</span>
        </span>)}
      </div>
    </div>);
  }

  /* ── Heatmap (2D binned grid) ── */
  if (type === "heatmap") {
    const meta = cfg._heatmap_meta || {};
    const maxCnt = Math.max(1, ...points.map(p => num(p.cnt)));
    const nBins = meta.n_bins || 20;
    const W = 420, H = 380;
    const pad = { t: 16, r: 60, b: 44, l: 58 };
    const cw = W - pad.l - pad.r, ch = H - pad.t - pad.b;
    const cellW = cw / nBins, cellH = ch / nBins;
    // Viridis-like color scale
    const heatColor = (cnt) => {
      if (!cnt) return "transparent";
      const t = Math.pow(num(cnt) / maxCnt, 0.6);
      const r = Math.round(68 + 187 * t);
      const g = Math.round(1 + 150 * Math.min(t * 1.5, 1) - 80 * Math.max(t - 0.6, 0));
      const b = Math.round(84 + 100 * (1 - t));
      return `rgb(${r},${g},${b})`;
    };
    return (<div style={{ overflow: "hidden", background: "var(--bg-card)", borderRadius: 8, border: "1px solid var(--border)", padding: "12px 14px", position: "relative" }}>
      {Header}{Tip}
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" ref={svgRef} onMouseLeave={() => setTip(null)} style={{ display: "block" }}>
        {/* Grid cells */}
        {points.map((p, i) => (
          <rect key={i} x={pad.l + p.bx * cellW} y={pad.t + (nBins - 1 - p.by) * cellH}
            width={cellW - 0.5} height={cellH - 0.5} fill={heatColor(p.cnt)} rx={1}
            onMouseMove={e => { const r = svgRef.current.getBoundingClientRect(); setTip({ x: e.clientX - r.left, y: e.clientY - r.top, lines: [`X: ${p.x_lo} ~ ${p.x_hi}`, `Y: ${p.y_lo} ~ ${p.y_hi}`, `개수: ${p.cnt}`] }); }}
            style={{ cursor: "pointer" }} />
        ))}
        {/* Axes */}
        <line x1={pad.l} y1={pad.t} x2={pad.l} y2={pad.t + ch} stroke="var(--text-secondary)" strokeWidth={0.5} opacity={0.5} />
        <line x1={pad.l} y1={pad.t + ch} x2={W - pad.r} y2={pad.t + ch} stroke="var(--text-secondary)" strokeWidth={0.5} opacity={0.5} />
        {/* X tick labels */}
        {[0, 0.25, 0.5, 0.75, 1].map((f, i) => {
          const v = (meta.x_min || 0) + ((meta.x_max || 1) - (meta.x_min || 0)) * f;
          return <text key={i} x={pad.l + cw * f} y={H - pad.b + 14} textAnchor="middle" fill="var(--text-secondary)" fontSize={9}>{fmt(v)}</text>;
        })}
        {/* Y tick labels */}
        {[0, 0.25, 0.5, 0.75, 1].map((f, i) => {
          const v = (meta.y_min || 0) + ((meta.y_max || 1) - (meta.y_min || 0)) * f;
          return <text key={i} x={pad.l - 6} y={pad.t + ch * (1 - f) + 3} textAnchor="end" fill="var(--text-secondary)" fontSize={9}>{fmt(v)}</text>;
        })}
        {xL && <text x={pad.l + cw / 2} y={H - 2} textAnchor="middle" fill="var(--accent)" fontSize={12} fontWeight={700} style={{ fontFamily: "monospace" }}>{xL}</text>}
        {yL && <text x={10} y={pad.t + ch / 2} transform={`rotate(-90,10,${pad.t + ch / 2})`} fill="var(--accent)" fontSize={12} fontWeight={700} textAnchor="middle" style={{ fontFamily: "monospace" }}>{yL}</text>}
        {/* Color bar */}
        <defs><linearGradient id="heatGrad" x1="0" y1="1" x2="0" y2="0">
          <stop offset="0%" stopColor="rgb(68,1,84)" /><stop offset="30%" stopColor="rgb(120,80,120)" />
          <stop offset="60%" stopColor="rgb(200,100,60)" /><stop offset="100%" stopColor="rgb(255,150,4)" />
        </linearGradient></defs>
        <rect x={W - pad.r + 16} y={pad.t} width={14} height={ch} rx={3} fill="url(#heatGrad)" />
        <text x={W - pad.r + 36} y={pad.t + 4} fill="var(--text-secondary)" fontSize={8}>{maxCnt}</text>
        <text x={W - pad.r + 36} y={pad.t + ch} fill="var(--text-secondary)" fontSize={8}>0</text>
        <text x={W - pad.r + 24} y={pad.t - 6} fill="var(--text-secondary)" fontSize={8} textAnchor="middle">개수</text>
      </svg>
    </div>);
  }

  /* ── Wafer Map: WF Layout background + measured-shot coloring ── */
  if (type === "wafer_map") {
    const layout = cfg._wafer_layout || {};
    const layoutShots = Array.isArray(layout.shots) ? layout.shots : [];
    const lcfg = layout.cfg || {};
    const vals = points.map(p => p.val).filter(v => v != null);
    const numericVals = vals.map(Number).filter(Number.isFinite);
    const isNumeric = vals.length > 0 && numericVals.length === vals.length;
    let colorFn;
    if (isNumeric) {
      const vMin = Math.min(...numericVals), vMax = Math.max(...numericVals), vR = vMax - vMin || 1;
      // Blue=low, Red=high (semiconductor standard)
      colorFn = (v) => { const t = (Number(v) - vMin) / vR; return `rgb(${Math.round(50 + 200 * t)},${Math.round(100 - 40 * t)},${Math.round(220 - 180 * t)})`; };
    } else {
      const uniq = [...new Set(vals)];
      colorFn = (v) => SERIES[Math.max(0, uniq.indexOf(v)) % SERIES.length];
    }

    if (layoutShots.length) {
      const W = 520, H = 520, pad = 28;
      const wrMm = Number(lcfg.waferRadius || 150);
      const cx0 = Number(lcfg.wfCenterX || 0);
      const cy0 = Number(lcfg.wfCenterY || 0);
      const edgeMm = Math.max(0, Number(lcfg.edgeExclusionMm || 0));
      const scale = (Math.min(W, H) - pad * 2) / Math.max(1, 2 * wrMm);
      const sx = (x) => pad + (Number(x) - (cx0 - wrMm)) * scale;
      const sy = (y) => pad + ((cy0 + wrMm) - Number(y)) * scale;
      const rectSvg = (rect = {}) => ({
        x: sx(rect.x),
        y: sy(Number(rect.y || 0) + Number(rect.h || 0)),
        w: Number(rect.w || 0) * scale,
        h: Number(rect.h || 0) * scale,
      });
      const shotByGrid = new Map(layoutShots.map(s => [`${s.gridShotX}|${s.gridShotY}`, s]));
      const shotByRaw = new Map(layoutShots.map(s => [`${s.shotX}|${s.shotY}`, s]));
      const clipId = "waferClip-" + (cfg.id || "0");
      const gradId = "wBg-" + (cfg.id || "0");
      const waferCx = sx(cx0), waferCy = sy(cy0), waferR = wrMm * scale;
      return (<div style={{ overflow: "hidden", background: "var(--bg-card)", borderRadius: 8, border: "1px solid var(--border)", padding: "12px 14px", position: "relative" }}>
        {Header}{Tip}
        <svg viewBox={`0 0 ${W} ${H}`} width="100%" ref={svgRef} onMouseLeave={() => setTip(null)} style={{ display: "block" }}>
          <defs>
            <clipPath id={clipId}><circle cx={waferCx} cy={waferCy} r={waferR} /></clipPath>
            <radialGradient id={gradId} cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor="rgba(37,99,235,0.08)" />
              <stop offset="100%" stopColor="rgba(15,23,42,0.03)" />
            </radialGradient>
          </defs>
          <circle cx={waferCx} cy={waferCy} r={waferR} fill={`url(#${gradId})`} stroke="rgba(37,99,235,0.35)" strokeWidth="1.5" />
          {edgeMm > 0 && <circle cx={waferCx} cy={waferCy} r={Math.max(0, (wrMm - edgeMm) * scale)} fill="none" stroke="rgba(249,115,22,0.55)" strokeDasharray="6,4" strokeWidth="1.1" />}
          <polygon points={`${waferCx - 7},${waferCy - waferR - 1} ${waferCx + 7},${waferCy - waferR - 1} ${waferCx},${waferCy - waferR + 9}`} fill="rgba(15,23,42,0.35)" />
          <g clipPath={`url(#${clipId})`}>
            {layoutShots.map((shot, i) => {
              const r = rectSvg(shot.shotBody);
              return <rect key={`bg-${i}`} x={r.x} y={r.y} width={Math.max(0.5, r.w)} height={Math.max(0.5, r.h)} rx="1.2" fill="rgba(148,163,184,0.08)" stroke="rgba(100,116,139,0.22)" strokeWidth="0.55" />;
            })}
            {points.map((p, i) => {
              const matched = p.shotBody ? p : (shotByGrid.get(`${p.x}|${p.y}`) || shotByRaw.get(`${p.x}|${p.y}`) || {});
              const body = p.shotBody || matched.shotBody;
              if (!body) return null;
              const r = rectSvg(body);
              const gridX = p.gridShotX ?? matched.gridShotX ?? p.x;
              const gridY = p.gridShotY ?? matched.gridShotY ?? p.y;
              return <rect key={`pt-${i}`} x={r.x} y={r.y} width={Math.max(1, r.w)} height={Math.max(1, r.h)} rx="1.4" fill={colorFn(p.val)} stroke="rgba(15,23,42,0.5)" strokeWidth="0.7" opacity={0.9}
                onMouseMove={e => { const b = svgRef.current.getBoundingClientRect(); setTip({ x: e.clientX - b.left, y: e.clientY - b.top, lines: [
                  `Shot (${gridY}, ${gridX})`,
                  `${cfg.color_col || cfg.agg_col || "value"}: ${fmt(p.val)}`,
                  `n: ${p.count || 1}`,
                  p.root_lot_id ? `root_lot: ${p.root_lot_id}` : "",
                  p.wafer_id ? `wafer: ${p.wafer_id}` : "",
                ].filter(Boolean) }); }}
                style={{ cursor: "pointer" }} />;
            })}
          </g>
          <line x1={waferCx - 14} y1={waferCy} x2={waferCx + 14} y2={waferCy} stroke="rgba(15,23,42,0.25)" strokeDasharray="4,4" />
          <line x1={waferCx} y1={waferCy - 14} x2={waferCx} y2={waferCy + 14} stroke="rgba(15,23,42,0.25)" strokeDasharray="4,4" />
          <text x={18} y={H - 16} fontSize="9" fill="var(--text-secondary)" style={{ fontFamily: "monospace" }}>
            layout: {layout.product || cfg.layout_product || cfg.product || "-"} · measured {points.length} / shots {layoutShots.length}
          </text>
        </svg>
        <div style={{ display: "flex", alignItems: "center", gap: 4, marginTop: 6, fontSize: 9, color: "var(--text-secondary)" }}>
          {isNumeric ? <>
            <span>{Math.min(...numericVals).toFixed(2)}</span>
            <div style={{ flex: 1, height: 10, borderRadius: 3, background: "linear-gradient(to right, rgb(50,100,220), rgb(150,70,120), rgb(250,60,40))" }} />
            <span>{Math.max(...numericVals).toFixed(2)}</span>
          </> : <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {[...new Set(vals)].slice(0, 12).map((v, i) => <span key={i} style={{ display: "flex", alignItems: "center", gap: 3 }}>
              <span style={{ width: 8, height: 8, borderRadius: 2, background: SERIES[i % SERIES.length] }} />{String(v)}
            </span>)}
          </div>}
        </div>
      </div>);
    }

    if (!points.length) return <div style={{ padding: 40, textAlign: "center", color: "var(--text-secondary)", fontSize: 12 }}>WF Layout 또는 측정 shot 데이터 없음</div>;
    const xs = points.map(p => p.x), ysv = points.map(p => p.y);
    const minX = Math.min(...xs), maxX = Math.max(...xs), minYv = Math.min(...ysv), maxYv = Math.max(...ysv);
    const cols = maxX - minX + 1, rows = maxYv - minYv + 1;
    const cellW = Math.min(16, Math.max(4, 320 / cols)), cellH = Math.min(16, Math.max(4, 320 / rows));
    const W = cols * cellW + 80, H = rows * cellH + 80;
    const ox = 40, oy = 30;
    const wcx = ox + cols * cellW / 2, wcy = oy + rows * cellH / 2, wr = Math.max(cols * cellW, rows * cellH) / 2 + 8;
    const clipId = "waferClip-" + (cfg.id || "0");
    return (<div style={{ overflow: "hidden", background: "var(--bg-card)", borderRadius: 8, border: "1px solid var(--border)", padding: "12px 14px", position: "relative" }}>
      {Header}{Tip}
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" ref={svgRef} onMouseLeave={() => setTip(null)} style={{ display: "block" }}>
        <defs>
          <clipPath id={clipId}><circle cx={wcx} cy={wcy} r={wr} /></clipPath>
          <radialGradient id={"wBg-" + (cfg.id || "0")} cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="var(--bg-hover)" stopOpacity="0.3" />
            <stop offset="100%" stopColor="var(--bg-hover)" stopOpacity="0.08" />
          </radialGradient>
        </defs>
        {/* Wafer background */}
        <circle cx={wcx} cy={wcy} r={wr} fill={"url(#wBg-" + (cfg.id || "0") + ")"} />
        <circle cx={wcx} cy={wcy} r={wr} fill="none" stroke="var(--text-secondary)" strokeWidth={1.5} opacity={0.5} />
        {/* Notch (triangle at top) */}
        <polygon points={`${wcx - 5},${wcy - wr - 1} ${wcx + 5},${wcy - wr - 1} ${wcx},${wcy - wr + 7}`}
          fill="var(--text-secondary)" opacity={0.5} />
        {/* Center crosshair */}
        <line x1={wcx - 12} y1={wcy} x2={wcx + 12} y2={wcy} stroke="var(--text-secondary)" strokeWidth={0.5} strokeDasharray="3,3" opacity={0.3} />
        <line x1={wcx} y1={wcy - 12} x2={wcx} y2={wcy + 12} stroke="var(--text-secondary)" strokeWidth={0.5} strokeDasharray="3,3" opacity={0.3} />
        {/* Die cells — clipped to wafer circle, Y-axis inverted (high Y at top) */}
        <g clipPath={"url(#" + clipId + ")"}>
          {points.map((p, i) => {
            const cx = ox + (p.x - minX) * cellW, cy = oy + (maxYv - p.y) * cellH;
            return <rect key={i} x={cx} y={cy} width={cellW - 1} height={cellH - 1} fill={colorFn(p.val)} rx={1} opacity={0.88}
              onMouseMove={e => { const r = svgRef.current.getBoundingClientRect(); setTip({ x: e.clientX - r.left, y: e.clientY - r.top, lines: [`Die (${p.x}, ${p.y})`, `값: ${p.val}`] }); }}
              style={{ cursor: "pointer" }} />;
          })}
        </g>
        {xL && <text x={wcx} y={H - 4} textAnchor="middle" fill="var(--accent)" fontSize={11} fontWeight={700}>{xL}</text>}
        {yL && <text x={8} y={wcy} transform={`rotate(-90,8,${wcy})`} fill="var(--accent)" fontSize={11} fontWeight={700} textAnchor="middle">{yL}</text>}
      </svg>
      {/* Color bar */}
      <div style={{ display: "flex", alignItems: "center", gap: 4, marginTop: 6, fontSize: 9, color: "var(--text-secondary)" }}>
        {isNumeric ? <>
          <span>{Math.min(...vals.map(Number)).toFixed(2)}</span>
          <div style={{ flex: 1, height: 10, borderRadius: 3, background: "linear-gradient(to right, rgb(50,100,220), rgb(150,70,120), rgb(250,60,40))" }} />
          <span>{Math.max(...vals.map(Number)).toFixed(2)}</span>
        </> : <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {[...new Set(vals)].map((v, i) => <span key={i} style={{ display: "flex", alignItems: "center", gap: 3 }}>
            <span style={{ width: 8, height: 8, borderRadius: 2, background: SERIES[i % SERIES.length] }} />{String(v)}
          </span>)}
        </div>}
      </div>
    </div>);
  }

  /* ── Combo (Bar + Line) ── */
  if (type === "combo" && points.length > 0) {
    const hasLine = points.some(p => p.line != null);
    const barVals = points.map(p => p.bar).filter(v => v != null);
    const lineVals = hasLine ? points.map(p => p.line).filter(v => v != null) : [];
    const allVals = [...barVals, ...lineVals];
    const minV = Math.min(0, ...allVals), maxV = Math.max(...allVals), rangeV = maxV - minV || 1;
    const W = Math.max(400, Math.min(600, points.length * 30)), H = 260;
    const pad = { t: 24, r: 16, b: 44, l: 54 };
    const cw = W - pad.l - pad.r, ch = H - pad.t - pad.b;
    const toX = (i) => pad.l + (i + 0.5) * (cw / points.length);
    const toY = (v) => pad.t + ch - (v - minV) / rangeV * ch;
    const bw = Math.max(4, cw / points.length * 0.6);
    return (<div style={{ overflow: "hidden", background: "var(--bg-card)", borderRadius: 8, border: "1px solid var(--border)", padding: "12px 14px", position: "relative" }}>
      {Header}{Tip}
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" ref={svgRef} onMouseLeave={() => setTip(null)} style={{ display: "block" }}>
        <line x1={pad.l} y1={pad.t} x2={pad.l} y2={pad.t + ch} stroke="var(--text-secondary)" strokeWidth={0.5} opacity={0.4} />
        <line x1={pad.l} y1={pad.t + ch} x2={W - pad.r} y2={pad.t + ch} stroke="var(--text-secondary)" strokeWidth={0.5} opacity={0.4} />
        {[0, 0.25, 0.5, 0.75, 1].map((f, i) => { const y = pad.t + ch * (1 - f); return <g key={i}><line x1={pad.l} y1={y} x2={W - pad.r} y2={y} stroke="var(--border)" strokeDasharray="2,3" opacity={0.3} /><text x={pad.l - 6} y={y + 3} textAnchor="end" fill="var(--text-secondary)" fontSize={9}>{fmt(minV + rangeV * f)}</text></g>; })}
        {/* Bars */}
        {points.map((p, i) => p.bar != null && <rect key={"b" + i} x={toX(i) - bw / 2} y={toY(p.bar)} width={bw} height={Math.max(1, pad.t + ch - toY(p.bar))} fill={SERIES[0]} rx={2} opacity={0.7}
          onMouseMove={e => { const r = svgRef.current.getBoundingClientRect(); setTip({ x: e.clientX - r.left, y: e.clientY - r.top, lines: [`${p.x}`, `막대: ${fmt(p.bar)}`, p.line != null ? `선: ${fmt(p.line)}` : ""] }); }}
          style={{ cursor: "pointer" }} />)}
        {/* Line overlay */}
        {hasLine && <polyline points={points.filter(p => p.line != null).map((p, i) => `${toX(points.indexOf(p))},${toY(p.line)}`).join(" ")} fill="none" stroke={BAD.fg} strokeWidth={2} />}
        {hasLine && points.filter(p => p.line != null).map((p, i) => <circle key={"l" + i} cx={toX(points.indexOf(p))} cy={toY(p.line)} r={3} fill={BAD.fg} stroke={WHITE} strokeWidth={1} />)}
        {/* X labels */}
        {points.map((p, i) => <text key={i} x={toX(i)} y={H - pad.b + 12} textAnchor="middle" fill="var(--text-secondary)" fontSize={8} transform={`rotate(-30,${toX(i)},${H - pad.b + 12})`}>{(p.x || "").slice(0, 10)}</text>)}
        {xL && <text x={pad.l + cw / 2} y={H - 2} textAnchor="middle" fill="var(--accent)" fontSize={12} fontWeight={700}>{xL}</text>}
        {yL && <text x={10} y={pad.t + ch / 2} transform={`rotate(-90,10,${pad.t + ch / 2})`} fill="var(--accent)" fontSize={12} fontWeight={700} textAnchor="middle">{yL}</text>}
        {/* Legend */}
        <rect x={W - pad.r - 80} y={pad.t} width={8} height={8} fill={SERIES[0]} rx={2} />
        <text x={W - pad.r - 68} y={pad.t + 8} fill="var(--text-secondary)" fontSize={9}>{cfg.y_expr || "Bar"}</text>
        {hasLine && <><rect x={W - pad.r - 80} y={pad.t + 14} width={8} height={8} fill={BAD.fg} rx={2} />
        <text x={W - pad.r - 68} y={pad.t + 22} fill="var(--text-secondary)" fontSize={9}>{cfg.agg_col || "Line"}</text></>}
      </svg>
    </div>);
  }

  /* ── Scatter / Line / Bar / Area ── */
  // v8.8.13: multi-Y. BE 가 각 Y 시리즈를 p.series 로 태깅 → p.color 없을 때 시리즈명으로 그룹화.
  //   color_col 지정(categorical) 과 multi-Y 중 하나가 있으면 시리즈별 색상.
  const colorGroups = {};
  points.forEach(p => { const c = p.color || p.series || "default"; if (!colorGroups[c]) colorGroups[c] = []; colorGroups[c].push(p); });
  const groupNames = Object.keys(colorGroups);
  const hasColor = groupNames.length > 1 || (groupNames.length === 1 && groupNames[0] !== "default");

  const ys = points.map(p => maybeNum(p.y)).filter(v => v != null);
  // v6: Extend Y range to include spec lines + SPC limits
  // v7: spec_lines[] in addition to legacy single usl/lsl/target
  const extraSpec = (cfg.spec_lines || []).map(s => maybeNum(s.value)).filter(v => v != null);
  const specVals = [cfg.usl, cfg.lsl, cfg.target].map(maybeNum).filter(v => v != null);
  const spcVals = cfg._spc ? [cfg._spc.ucl, cfg._spc.lcl].map(maybeNum).filter(v => v != null) : [];
  const extraVals = [...specVals, ...extraSpec, ...spcVals];
  const rawMinY = ys.length ? Math.min(...ys) : 0, rawMaxY = ys.length ? Math.max(...ys) : 1;
  const minY = extraVals.length ? Math.min(rawMinY, ...extraVals) : rawMinY;
  const maxY = extraVals.length ? Math.max(rawMaxY, ...extraVals) : rawMaxY;
  const rangeY = maxY - minY || 1;
  const W = 500, H = 280;
  const pad = { t: 24, r: hasColor ? 110 : 16, b: 48, l: 58 };
  const cw = W - pad.l - pad.r, ch = H - pad.t - pad.b;
  const toX = (i) => pad.l + (points.length <= 1 ? cw / 2 : i / (points.length - 1) * cw);
  const toY = (v) => pad.t + ch - (num(v, minY) - minY) / rangeY * ch;

  // v8.4.4: polynomial fit (degree 1-4) with R² — cfg.fit_line_enabled / fit_line_degree / fit_line_show_r2
  // v8.8.16: scatter 기본값 변경 — 사용자가 체크박스를 직접 켜야만 fitting line 표시 (이전: 항상 on).
  let fitLine = null;
  const fitEnabled = cfg.fit_line_enabled === true;
  if (fitEnabled && points.length > 2) {
    const deg = Math.max(1, Math.min(4, cfg.fit_line_degree || 1));
    const xs = points.map((_, i) => i), yv = points.map(p => num(p.y));
    const n = xs.length;
    // Normal equation for polynomial fit: build Vandermonde-like matrix.
    // Solve the coefficient vector via Gauss elimination.
    const X = xs.map(x => Array.from({length: deg+1}, (_, k) => Math.pow(x, k)));
    const XT = Array.from({length: deg+1}, (_, k) => xs.map(x => Math.pow(x, k)));
    const XtX = XT.map(row => X[0].map((_, j) => row.reduce((s, _v, i) => s + XT[row === XT[0] ? 0 : XT.indexOf(row)][i] * X[i][j], 0)));
    // Simpler: manual matmul
    const A = [];
    for (let i = 0; i <= deg; i++) { A.push([]); for (let j = 0; j <= deg; j++) { let s = 0; for (let k = 0; k < n; k++) s += Math.pow(xs[k], i) * Math.pow(xs[k], j); A[i].push(s); } }
    const B = []; for (let i = 0; i <= deg; i++) { let s = 0; for (let k = 0; k < n; k++) s += Math.pow(xs[k], i) * yv[k]; B.push(s); }
    // Gauss elimination
    const M = A.map((row, i) => [...row, B[i]]);
    for (let i = 0; i <= deg; i++) {
      let p = i; for (let k = i+1; k <= deg; k++) if (Math.abs(M[k][i]) > Math.abs(M[p][i])) p = k;
      [M[i], M[p]] = [M[p], M[i]];
      const piv = M[i][i] || 1e-12;
      for (let j = i; j <= deg+1; j++) M[i][j] /= piv;
      for (let k = 0; k <= deg; k++) if (k !== i) { const f = M[k][i]; for (let j = i; j <= deg+1; j++) M[k][j] -= f * M[i][j]; }
    }
    const coef = M.map(r => r[deg+1]);
    const evalPoly = (x) => coef.reduce((s, c, k) => s + c * Math.pow(x, k), 0);
    const yPred = xs.map(evalPoly);
    const meanY = yv.reduce((a,b)=>a+b,0)/n;
    const ssTot = yv.reduce((a,y)=>a+(y-meanY)**2,0);
    const ssRes = yv.reduce((a,y,i)=>a+(y-yPred[i])**2,0);
    const r2 = ssTot > 0 ? (1 - ssRes/ssTot) : 0;
    // Build SVG path through fit curve
    const steps = Math.max(deg * 40, 40);
    let path = "";
    for (let s = 0; s <= steps; s++) {
      const x = (s/steps) * (n - 1);
      const y = evalPoly(x);
      path += (s === 0 ? "M" : "L") + toX(x) + "," + toY(y);
    }
    fitLine = { path, r2, degree: deg };
  }

  return (<div style={{ overflow: "hidden", background: "var(--bg-card)", borderRadius: 8, border: "1px solid var(--border)", padding: "14px 16px", position: "relative" }}>
    {Header}{Tip}
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" ref={svgRef} onMouseLeave={() => setTip(null)} style={{ display: "block" }}>
      {/* Solid axis lines */}
      <line x1={pad.l} y1={pad.t} x2={pad.l} y2={pad.t + ch} stroke="var(--text-secondary)" strokeWidth={1} opacity={0.5} />
      <line x1={pad.l} y1={pad.t + ch} x2={W - pad.r} y2={pad.t + ch} stroke="var(--text-secondary)" strokeWidth={1} opacity={0.5} />
      {/* Grid + Y tick labels */}
      {[0, 0.25, 0.5, 0.75, 1].map((f, i) => { const y = pad.t + ch * (1 - f); const v = minY + rangeY * f; return (<g key={i}><line x1={pad.l} y1={y} x2={W - pad.r} y2={y} stroke="var(--border)" strokeDasharray="2,4" opacity={0.4} /><text x={pad.l - 8} y={y + 4} textAnchor="end" fill="var(--text-secondary)" fontSize={10}>{fmt(v)}</text></g>); })}

      {/* v6: Spec lines (USL / LSL / Target) */}
      {cfg.usl != null && <g>
        <line x1={pad.l} y1={toY(cfg.usl)} x2={W - pad.r} y2={toY(cfg.usl)} stroke={BAD.fg} strokeWidth={2} strokeDasharray="6,3" opacity={0.8} />
        <rect x={W - pad.r - 62} y={toY(cfg.usl) - 14} width={58} height={15} rx={3} fill="rgba(239,68,68,0.15)" />
        <text x={W - pad.r - 6} y={toY(cfg.usl) - 3} textAnchor="end" fill={BAD.fg} fontSize={10} fontWeight={600}>USL {fmt(cfg.usl)}</text>
      </g>}
      {cfg.lsl != null && <g>
        <line x1={pad.l} y1={toY(cfg.lsl)} x2={W - pad.r} y2={toY(cfg.lsl)} stroke={BAD.fg} strokeWidth={2} strokeDasharray="6,3" opacity={0.8} />
        <rect x={W - pad.r - 62} y={toY(cfg.lsl) + 2} width={58} height={15} rx={3} fill="rgba(239,68,68,0.15)" />
        <text x={W - pad.r - 6} y={toY(cfg.lsl) + 13} textAnchor="end" fill={BAD.fg} fontSize={10} fontWeight={600}>LSL {fmt(cfg.lsl)}</text>
      </g>}
      {cfg.target != null && <g>
        <line x1={pad.l} y1={toY(cfg.target)} x2={W - pad.r} y2={toY(cfg.target)} stroke={OK.fg} strokeWidth={2} opacity={0.9} />
        <rect x={W - pad.r - 72} y={toY(cfg.target) - 14} width={68} height={15} rx={3} fill="rgba(16,185,129,0.15)" />
        <text x={W - pad.r - 6} y={toY(cfg.target) - 3} textAnchor="end" fill={OK.fg} fontSize={10} fontWeight={600}>Target {fmt(cfg.target)}</text>
      </g>}
      {/* v7: Multi spec_lines[] */}
      {(cfg.spec_lines || []).map((sl, i) => {
        const v = Number(sl.value); if (isNaN(v)) return null;
        const k = (sl.kind || "custom").toLowerCase();
          const color = sl.color || (k === "usl" || k === "lsl" ? BAD.fg : k === "target" ? OK.fg : PURPLE.fg);
        const dash = sl.style === "solid" ? null : (k === "target" ? null : "6,3");
        const lbl = (sl.name || k.toUpperCase()) + " " + fmt(v);
        const txtW = Math.max(48, lbl.length * 6);
        const yPos = toY(v);
        const side = (i % 2 === 0) ? "right" : "left";
        return (<g key={"sl"+i}>
          <line x1={pad.l} y1={yPos} x2={W - pad.r} y2={yPos} stroke={color} strokeWidth={1.8} strokeDasharray={dash || undefined} opacity={0.85} />
          {side === "right"
            ? <><rect x={W - pad.r - txtW - 4} y={yPos - 14} width={txtW} height={15} rx={3} fill={color + "22"} />
                <text x={W - pad.r - 6} y={yPos - 3} textAnchor="end" fill={color} fontSize={10} fontWeight={600}>{lbl}</text></>
            : <><rect x={pad.l + 4} y={yPos - 14} width={txtW} height={15} rx={3} fill={color + "22"} />
                <text x={pad.l + 8} y={yPos - 3} textAnchor="start" fill={color} fontSize={10} fontWeight={600}>{lbl}</text></>}
        </g>);
      })}
      {/* v6: SPC lines (UCL / CL / LCL) */}
      {cfg._spc && <g>
        <line x1={pad.l} y1={toY(cfg._spc.ucl)} x2={W - pad.r} y2={toY(cfg._spc.ucl)} stroke={WARN.fg} strokeWidth={1.5} strokeDasharray="4,4" opacity={0.8} />
        <text x={pad.l + 4} y={toY(cfg._spc.ucl) - 3} fill={WARN.fg} fontSize={9} fontWeight={600}>UCL {fmt(cfg._spc.ucl)}</text>
        <line x1={pad.l} y1={toY(cfg._spc.lcl)} x2={W - pad.r} y2={toY(cfg._spc.lcl)} stroke={WARN.fg} strokeWidth={1.5} strokeDasharray="4,4" opacity={0.8} />
        <text x={pad.l + 4} y={toY(cfg._spc.lcl) + 12} fill={WARN.fg} fontSize={9} fontWeight={600}>LCL {fmt(cfg._spc.lcl)}</text>
        <line x1={pad.l} y1={toY(cfg._spc.cl)} x2={W - pad.r} y2={toY(cfg._spc.cl)} stroke={BLUE.fg} strokeWidth={1.5} opacity={0.8} />
        <text x={pad.l + 4} y={toY(cfg._spc.cl) - 3} fill={BLUE.fg} fontSize={9} fontWeight={600}>CL {fmt(cfg._spc.cl)}</text>
      </g>}

      {type === "bar" ? points.map((p, i) => {
        const bw = Math.max(2, cw / points.length * 0.7); const ci = hasColor ? groupNames.indexOf(p.color || p.series || "default") : 0;
        return (<rect key={i} x={toX(i) - bw / 2} y={toY(p.y)} width={bw} height={Math.max(1, pad.t + ch - toY(p.y))} fill={SERIES[ci % SERIES.length]} rx={1} opacity={0.8}
          onMouseMove={e => { const r = svgRef.current.getBoundingClientRect(); setTip({ x: e.clientX - r.left, y: e.clientY - r.top, lines: [`X: ${p.x}`, `Y: ${fmt(p.y)}`, p.color ? `그룹: ${p.color}` : ""] }); }}
          style={{ cursor: "pointer" }} />);
      }) : type === "line" ? (
        hasColor ? groupNames.map((gn, gi) => {
          const gp = colorGroups[gn]; const ci = SERIES[gi % SERIES.length];
          const sorted = gp.map(p => ({ ...p, _gi: points.indexOf(p) })).sort((a, b) => a._gi - b._gi);
          return (<g key={gn}>
            <polyline points={sorted.map(p => `${toX(p._gi)},${toY(p.y)}`).join(" ")} fill="none" stroke={ci} strokeWidth={1.5} />
            {sorted.map((p, pi) => <circle key={pi} cx={toX(p._gi)} cy={toY(p.y)} r={ptSize} fill={ci}
              onMouseMove={e => { const r = svgRef.current.getBoundingClientRect(); setTip({ x: e.clientX - r.left, y: e.clientY - r.top, lines: [`X: ${p.x}`, `Y: ${fmt(p.y)}`, `그룹: ${gn}`] }); }}
              style={{ cursor: "pointer" }} />)}
          </g>);
        }) : <g>
          <polyline points={points.map((p, i) => `${toX(i)},${toY(p.y)}`).join(" ")} fill="none" stroke={SERIES[0]} strokeWidth={1.5} />
          {points.map((p, i) => <circle key={i} cx={toX(i)} cy={toY(p.y)} r={ptSize} fill={SERIES[0]}
            onMouseMove={e => { const r = svgRef.current.getBoundingClientRect(); setTip({ x: e.clientX - r.left, y: e.clientY - r.top, lines: [`X: ${p.x}`, `Y: ${fmt(p.y)}`] }); }}
            style={{ cursor: "pointer" }} />)}
        </g>
      ) : type === "area" ? <g>
        {/* Area fill */}
        <path d={`M ${toX(0)},${toY(points[0].y)} ${points.map((p, i) => `L ${toX(i)},${toY(p.y)}`).join(" ")} L ${toX(points.length - 1)},${pad.t + ch} L ${toX(0)},${pad.t + ch} Z`}
          fill={SERIES[0]} opacity={0.15} />
        <polyline points={points.map((p, i) => `${toX(i)},${toY(p.y)}`).join(" ")} fill="none" stroke={SERIES[0]} strokeWidth={2} />
        {points.map((p, i) => <circle key={i} cx={toX(i)} cy={toY(p.y)} r={2} fill={SERIES[0]}
          onMouseMove={e => { const r = svgRef.current.getBoundingClientRect(); setTip({ x: e.clientX - r.left, y: e.clientY - r.top, lines: [`X: ${p.x}`, `Y: ${fmt(p.y)}`] }); }}
          style={{ cursor: "pointer" }} />)}
      </g>
      : /* scatter */
        <g>
        {points.map((p, i) => {
          const ci = hasColor ? groupNames.indexOf(p.color || p.series || "default") : 0;
          const extraUSL = (cfg.spec_lines||[]).filter(s=>(s.kind||"").toLowerCase()==="usl").map(s=>Number(s.value)).filter(v=>!isNaN(v));
          const extraLSL = (cfg.spec_lines||[]).filter(s=>(s.kind||"").toLowerCase()==="lsl").map(s=>Number(s.value)).filter(v=>!isNaN(v));
          const tightUSL = [cfg.usl, ...extraUSL].filter(v=>v!=null&&!isNaN(v));
          const tightLSL = [cfg.lsl, ...extraLSL].filter(v=>v!=null&&!isNaN(v));
          const isOOS = (tightUSL.length && p.y > Math.min(...tightUSL)) || (tightLSL.length && p.y < Math.max(...tightLSL));
          // v7.2: cross-chart marking
          const isMarked = hasAnyMark && p.mark && marks.has(p.mark);
          const isDimmed = hasAnyMark && !isMarked;
          const r = isMarked ? ptSize + 2 : (isOOS ? ptSize + 1.5 : ptSize);
          const fill = isOOS ? BAD.fg : SERIES[ci % SERIES.length];
          const op = isDimmed ? 0.15 : (isMarked ? 1.0 : (isOOS ? 0.95 : ptOpacity));
          const stroke = isMarked ? MARK_STROKE : (isOOS ? WHITE : "rgba(0,0,0,0.3)");
          const sw = isMarked ? 2 : (isOOS ? 1.5 : 0.5);
          return <circle key={i} cx={toX(i)} cy={toY(p.y)} r={r}
            fill={fill} opacity={op} stroke={stroke} strokeWidth={sw}
            onMouseMove={e => { const br = svgRef.current.getBoundingClientRect(); setTip({ x: e.clientX - br.left, y: e.clientY - br.top, lines: [`X: ${p.x}`, `Y: ${fmt(p.y)}`, p.series ? `시리즈: ${p.series}` : "", p.color ? `그룹: ${p.color}` : "", p.mark ? `${cfg.selection_key || "mark"}: ${p.mark}` : "", isOOS ? "⚠ OOS" : "", isMarked ? "★ 표시됨" : ""].filter(Boolean) }); }}
            onClick={e => { e.stopPropagation(); if (p.mark) toggleMark(p.mark); }}
            style={{ cursor: p.mark ? "pointer" : "default" }} />;
        })}
        {/* Fit line with R² */}
        {fitLine && <g>
          <path d={fitLine.path} fill="none" stroke={BAD.fg} strokeWidth={2} strokeDasharray={fitLine.degree===1?"8,4":undefined} opacity={0.85} />
          {/* v8.8.16: R² 표시도 명시적 체크 필요 (기본 off). */}
          {(cfg.fit_line_show_r2 === true) && <text x={W - pad.r - 4} y={pad.t + 16} textAnchor="end" fill={BAD.fg} fontSize={13} fontWeight={800} fontFamily="monospace" style={{ textShadow: "0 0 4px var(--bg-card), 0 0 4px var(--bg-card)" }}>deg={fitLine.degree} · R² = {fixed(fitLine.r2, 4)}</text>}
        </g>}
        </g>
      }
      {/* Axes labels — large, bold, always visible */}
      {xL && <text x={pad.l + cw / 2} y={H - 2} textAnchor="middle" fill="var(--accent)" fontSize={13} fontWeight={700} style={{fontFamily:"monospace"}}>{xL}</text>}
      {yL && <text x={10} y={pad.t + ch / 2} transform={`rotate(-90,10,${pad.t + ch / 2})`} fill="var(--accent)" fontSize={13} fontWeight={700} textAnchor="middle" style={{fontFamily:"monospace"}}>{yL}</text>}
      {/* Color legend */}
      {hasColor && groupNames.map((gn, gi) => (
        <g key={gn} transform={`translate(${W - pad.r + 6},${pad.t + gi * 15})`}>
          <rect width={8} height={8} rx={2} fill={SERIES[gi % SERIES.length]} />
          <text x={12} y={8} fill="var(--text-secondary)" fontSize={9}>{(gn || "").slice(0, 14)}</text>
        </g>
      ))}
    </svg>
  </div>);
}

/* ═══ Column Input ═══ */
function ColInput({ label, value, onChange, columns, placeholder, guide }) {
  const [open, setOpen] = useState(false); const [sg, setSg] = useState(false);
  const filtered = value ? columns.filter(c => c.toLowerCase().includes(value.toLowerCase())) : columns;
  const S = { width: "100%", padding: "6px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12, outline: "none" };
  return (<div style={{ marginBottom: 8, position: "relative" }}>
    <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 2, display: "flex", justifyContent: "space-between" }}>
      <span>{label}</span>{guide && <span onClick={() => setSg(!sg)} style={{ cursor: "pointer", color: "var(--accent)", fontSize: 10 }}>{sg ? "▼" : "▶"}</span>}
    </div>
    <input value={value || ""} onChange={e => onChange(e.target.value)} onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 200)} style={S} placeholder={placeholder} />
    {open && filtered.length > 0 && <div style={{ position: "absolute", top: "100%", left: 0, right: 0, zIndex: 10, maxHeight: 320, overflow: "auto", background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: 6, marginTop: 2 }}>
      {filtered.slice(0, 500).map(c => <div key={c} onMouseDown={() => { onChange(c); setOpen(false); }} style={{ padding: "5px 10px", fontSize: 11, cursor: "pointer" }} onMouseEnter={e => e.currentTarget.style.background = "var(--bg-hover)"} onMouseLeave={e => e.currentTarget.style.background = "transparent"}>{c}</div>)}
    </div>}
    {sg && <div style={{ marginTop: 4, padding: "6px 10px", background: "var(--bg-card)", borderRadius: 6, border: "1px solid var(--border)", fontSize: 10, fontFamily: "monospace", lineHeight: 1.6, color: "var(--text-secondary)", whiteSpace: "pre-wrap" }}>{guide}</div>}
  </div>);
}

/* ═══ Chart Editor ═══ */
function ChartEditor({ cfg, onSave, onClose, isAdmin }) {
  const [form, setForm] = useState(cfg || { title: "", source_type: "", root: "", product: "", file: "", x_col: "", y_expr: "", time_col: "", days: null, chart_type: "scatter", filter_expr: "", agg_col: "", agg_method: "", color_col: "", x_label: "", y_label: "", bin_count: 10, bin_width: null, visible_to: "all", no_schedule: false, exclude_null: true, point_size: 3, opacity: 0.7, sort_x: false, limit_points: null, joins: [], group_ids: [] });
  const [sources, setSources] = useState([]); const [columns, setColumns] = useState([]);
  // v8.4.3: JOIN 된 소스별 컬럼 캐시. 키는 join 인덱스, 값은 컬럼명 배열.
  const [joinColumns, setJoinColumns] = useState({});
  const [columnsLoading, setColumnsLoading] = useState(false);
  const [columnsError, setColumnsError] = useState("");
  const [preview, setPreview] = useState(null); const [prevLoading, setPrevLoading] = useState(false);
  const [showAdv, setShowAdv] = useState(false);
  // v8.5.0: 내가 속한 그룹 목록 (관리자는 전체).
  const [myGroups, setMyGroups] = useState([]);
  const u = (k, v) => setForm({ ...form, [k]: v });
  useEffect(() => { sf(API + "/products").then(d => setSources(d.products || [])).catch(() => { }); }, []);
  useEffect(() => { sf("/api/groups/list").then(d => setMyGroups(d.groups || [])).catch(() => setMyGroups([])); }, []);
  // v8.8.2: /columns URL builder — base_file/root_parquet/hive 공통 진입점. 소스 타입 누락돼도 file 또는 root+product 로 fallback.
  const colUrl = (entry) => {
    if (!entry) return null;
    const st = entry.source_type || "";
    const file = entry.file || "";
    const root = entry.root || "";
    const product = entry.product || "";
    if (st === "base_file" && file) return API + "/columns?source_type=base_file&file=" + encodeURIComponent(file);
    if (st === "root_parquet" && file) return API + "/columns?file=" + encodeURIComponent(file);
    if (root && product) return API + "/columns?root=" + encodeURIComponent(root) + "&product=" + encodeURIComponent(product);
    // v8.8.2: fallback — source_type 은 비었지만 file 단독인 legacy 케이스.
    if (file) return API + "/columns?file=" + encodeURIComponent(file);
    return null;
  };
  useEffect(() => {
    const url = colUrl(form);
    if (!url) { setColumns([]); setColumnsError(""); setColumnsLoading(false); return; }
    setColumnsLoading(true); setColumnsError("");
    sf(url)
      .then(d => { setColumns(d.columns || []); setColumnsLoading(false); })
      .catch(e => { setColumns([]); setColumnsError((e && e.message) || "컬럼 로드 실패"); setColumnsLoading(false); });
  }, [form.root, form.product, form.file, form.source_type]);
  // v8.4.3: join 소스별 columns 프리페치. 소스 바뀌거나 추가될 때만 조회.
  useEffect(() => {
    (form.joins || []).forEach((j, i) => {
      if (joinColumns[i] && joinColumns[i]._for === JSON.stringify({ s: j.source_type, r: j.root, p: j.product, f: j.file })) return;
      const sig = JSON.stringify({ s: j.source_type, r: j.root, p: j.product, f: j.file });
      const q = colUrl(j);  // v8.8.2: base_file/fallback 지원
      if (!q) return;
      sf(q).then(d => setJoinColumns(prev => ({ ...prev, [i]: Object.assign([...(d.columns || [])], { _for: sig }) }))).catch(() => { });
    });
  }, [JSON.stringify((form.joins || []).map(j => ({ s: j.source_type, r: j.root, p: j.product, f: j.file })))]);
  // Main + joined columns — joined 은 suffix 붙여 중복 방지, X/Y/Filter/색상 등 어디서든 선택 가능.
  const allColumns = useMemo(() => {
    const out = [...columns];
    (form.joins || []).forEach((j, i) => {
      const cs = joinColumns[i] || [];
      const suffix = j.suffix || `_j${i + 1}`;
      cs.forEach(c => {
        const withSuffix = columns.includes(c) ? c + suffix : c;
        if (!out.includes(withSuffix)) out.push(withSuffix);
      });
    });
    return out;
  }, [columns, joinColumns, form.joins]);
  const selectSource = (val) => { const src = sources.find(s => s.label === val); if (src) { setForm({ ...form, source_type: src.source_type, root: src.root, product: src.product, file: src.file }); setPreview(null); } };
  const runPreview = () => {
    setPrevLoading(true); setPreview(null);
    const p = new URLSearchParams();
    ["source_type", "root", "product", "file", "x_col", "y_expr", "filter_expr", "time_col"].forEach(k => { if (form[k]) p.set(k, form[k]); });
    if (form.days) p.set("days", String(form.days)); p.set("limit", "10");
    sf(API + "/preview?" + p.toString()).then(d => { setPreview(d); setPrevLoading(false); }).catch(e => { setPreview({ error: e.message }); setPrevLoading(false); });
  };
  const doSave = () => {
    const payload = { ...form };
    ["days", "bin_count", "point_size", "limit_points"].forEach(k => { if (payload[k] === "" || payload[k] === undefined) payload[k] = null; else if (typeof payload[k] === "string") payload[k] = parseInt(payload[k]) || null; });
    ["bin_width", "opacity", "usl", "lsl", "target"].forEach(k => { if (payload[k] === "" || payload[k] === undefined) payload[k] = null; else if (typeof payload[k] === "string") payload[k] = parseFloat(payload[k]) || null; });
    delete payload._spc; delete payload._oos;
    onSave(payload);
  };
  const S = { width: "100%", padding: "6px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12, outline: "none" };
  // v8.4.5: match by actual source metadata (icons removed, base_file supported)
  const curLabel = (sources.find(s => (s.source_type || "") === (form.source_type || "")
    && (s.file || "") === (form.file || "")
    && (s.root || "") === (form.root || "")
    && (s.product || "") === (form.product || "")) || {}).label || "";
  const sqlG = `col == 'value'\ncol LIKE '%pattern%'\ncol.is_in(['A','B'])\n(a > 1) & (b == 'X')`;
  const yG = `컬럼 이름 (단순): Vth_01\n멀티 Y (쉼표): Vth_01, Ion_01\n수식: pl.col("a")/pl.col("b")*100\n  • 기본 산술 + pl.col("...") 래퍼 사용\n  • joined 컬럼은 suffix 적용된 이름 (예: Vth_01_j1)\n  • X 컬럼도 동일하게 수식 허용.`;
  const isPieOrBin = ["pie","donut","binning","pareto","treemap"].includes(form.chart_type);
  const isWaferMap = form.chart_type === "wafer_map";

  return (<div style={{ background: "var(--bg-secondary)", borderRadius: 10, border: "1px solid var(--border)", padding: 20, maxWidth: 660, marginBottom: 16 }}>
    <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 12 }}>
      <div style={{ fontSize: 14, fontWeight: 700 }}>차트 설정</div>
      <span onClick={onClose} style={{ cursor: "pointer", fontSize: 16, color: "var(--text-secondary)" }}>✕</span>
    </div>
    <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
      <div style={{ flex: 2 }}><div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 2 }}>제목</div><input value={form.title} onChange={e => u("title", e.target.value)} style={S} /></div>
      <div style={{ flex: 1 }}><div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 2 }}>타입</div>
        <select value={form.chart_type} onChange={e => u("chart_type", e.target.value)} style={S}>
          <option value="scatter">Scatter</option><option value="line">Line</option><option value="bar">Bar</option>
          <option value="pie">Pie</option><option value="donut">Donut</option><option value="binning">Histogram</option>
          <option value="box">Box Plot</option><option value="area">Area</option>
          <option value="pareto">Pareto</option><option value="treemap">Treemap</option>
          <option value="wafer_map">Wafer Map</option><option value="combo">Combo (Bar+Line)</option>
          <option value="step_knob_binning">Step + KNOB Ratio</option>
          <option value="heatmap">Heatmap (2D)</option>
          <option value="table">Table</option>
          <option value="cross_table">Cross Table (Pivot)</option>
        </select></div>
    </div>
    <div style={{ marginBottom: 8 }}><div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 2 }}>데이터 소스</div>
      <select value={curLabel} onChange={e => selectSource(e.target.value)} style={S}>
        <option value="">-- 선택 --</option>
        {sources.map(s => <option key={s.label} value={s.label}>{s.label} ({s.source_type})</option>)}
      </select></div>
    {/* Joins (LEFT JOIN additional sources) */}
    <div style={{ marginBottom: 10, padding: 10, background: "var(--bg-primary)", borderRadius: 6, border: "1px solid var(--border)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
        <span style={{ fontSize: 11, fontWeight: 700, color: "var(--accent)", fontFamily: "monospace" }}>⨝ LEFT JOINs ({(form.joins||[]).length})</span>
        <button onClick={() => u("joins", [...(form.joins || []), { source_type: "", root: "", product: "", file: "", left_on: "", right_on: "", suffix: "" }])} style={{ padding: "3px 10px", borderRadius: 4, border: "1px solid var(--accent)", background: "transparent", color: "var(--accent)", fontSize: 10, cursor: "pointer" }}>+ Join 추가</button>
      </div>
      <div style={{ fontSize: 10, color: "var(--text-secondary)", marginBottom: 8, lineHeight: 1.4 }}>
        다른 파일을 LEFT JOIN 하여 추가 컬럼을 차트에서 쓸 수 있습니다. 여러 키는 쉼표로 구분 (예: <code>LOT_ID,WAFER_ID</code>).
      </div>
      {(form.joins || []).map((j, i) => {
        const updJ = (k, v) => { const next = [...(form.joins || [])]; next[i] = { ...next[i], [k]: v }; u("joins", next); };
        const selJSource = (val) => { const src = sources.find(s => s.label === val); if (src) { const next = [...(form.joins||[])]; next[i] = { ...next[i], source_type: src.source_type, root: src.root, product: src.product, file: src.file }; u("joins", next); } };
        // v8.8.2: jLabel 은 실제 sources label 을 찾아야 select 가 선택 상태를 유지한다. (이전엔 "📊 file" 포맷이 option value 와 불일치 → "-- 선택 --" 로 되돌아가던 버그.)
        const jLabel = (sources.find(s => (s.source_type || "") === (j.source_type || "")
          && (s.file || "") === (j.file || "")
          && (s.root || "") === (j.root || "")
          && (s.product || "") === (j.product || "")) || {}).label || "";
        return (<div key={i} style={{ marginBottom: 8, padding: 8, background: "var(--bg-secondary)", borderRadius: 5, border: "1px solid var(--border)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
            <span style={{ fontSize: 10, color: "var(--text-secondary)", fontFamily: "monospace" }}>#{i + 1}</span>
            <span onClick={() => u("joins", (form.joins || []).filter((_, k) => k !== i))} style={{ cursor: "pointer", color: BAD.fg, fontSize: 11 }}>✕ 제거</span>
          </div>
          <div style={{ fontSize: 10, color: "var(--text-secondary)", marginBottom: 2 }}>우측 소스</div>
          <select value={jLabel} onChange={e => selJSource(e.target.value)} style={{ ...S, marginBottom: 6, fontSize: 11 }}>
            <option value="">-- 선택 --</option>
            {sources.map(s => <option key={s.label} value={s.label}>{s.label} ({s.source_type})</option>)}
          </select>
          <div style={{ display: "flex", gap: 6 }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 10, color: "var(--text-secondary)" }}>좌측 키 (main)</div>
              <input value={j.left_on || ""} onChange={e => updJ("left_on", e.target.value)} placeholder="LOT_ID,WAFER_ID" style={{ ...S, fontSize: 11, fontFamily: "monospace" }} />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 10, color: "var(--text-secondary)" }}>우측 키 (이 소스)</div>
              <input value={j.right_on || ""} onChange={e => updJ("right_on", e.target.value)} placeholder="LOT,WF" style={{ ...S, fontSize: 11, fontFamily: "monospace" }} />
            </div>
            <div style={{ width: 80 }}>
              <div style={{ fontSize: 10, color: "var(--text-secondary)" }}>접미사</div>
              <input value={j.suffix || ""} onChange={e => updJ("suffix", e.target.value)} placeholder={`_j${i + 1}`} style={{ ...S, fontSize: 11, fontFamily: "monospace" }} />
            </div>
          </div>
        </div>);
      })}
    </div>
    {/* v8.8.0/v8.8.2: X/Y 등 컬럼 선택 UI — 소스 미선택 시에도 항상 노출. 컬럼 로딩/에러 상태 가시화. */}
    {columnsLoading && (
      <div style={{ padding: "6px 10px", marginBottom: 8, borderRadius: 6, background: BLUE.soft, border: `1px dashed ${BLUE.border}`, color: INDIGO.fg, fontSize: 11 }}>
        … 소스에서 컬럼을 불러오는 중입니다.
      </div>
    )}
    {!columnsLoading && columnsError && (
      <div style={{ padding: "6px 10px", marginBottom: 8, borderRadius: 6, background: BAD.bg, border: `1px dashed ${BAD.fg}`, color: BAD.fg, fontSize: 11 }}>
        {/* v8.8.3: columnsError 에 서버 detail 이 포함되므로 prefix 없이 그대로 표시 */}
        ⚠ {columnsError}
      </div>
    )}
    {!columnsLoading && !columnsError && columns.length === 0 && (
      <div style={{ padding: "8px 10px", marginBottom: 8, borderRadius: 6, background: WARN.bg, border: `1px dashed ${WARN.fg}`, color: WARN.fg, fontSize: 11 }}>
        ⚠ 위 <b>소스 선택</b> 후에 컬럼이 로드됩니다 (선택해도 비어있다면 해당 parquet 가 빈 파일이거나 권한이 없는 경우).
      </div>
    )}
    <ColInput label={`X 컬럼 (검색/수식) ${columns.length === 0 ? " — 소스 선택 후 사용 가능" : ` · 컬럼 ${allColumns.length}개`}`} value={form.x_col} onChange={v => u("x_col", v)} columns={allColumns} placeholder={columns.length === 0 ? "먼저 위에서 소스를 선택하세요" : "컬럼 검색 또는 수식 (가이드 ▶)"} guide={yG} />
    {!isPieOrBin && <>
      <ColInput label="Y 컬럼 (여러 개는 쉼표 구분)" value={form.y_expr} onChange={v => u("y_expr", v)} columns={allColumns} placeholder={columns.length === 0 ? "먼저 위에서 소스를 선택하세요" : "col1 또는 col1,col2"} guide={yG} />
      <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
        <div style={{ flex: 1 }}><ColInput label="색상 / 그룹" value={form.color_col} onChange={v => u("color_col", v)} columns={allColumns} placeholder="범주형 컬럼" /></div>
        <div style={{ flex: 1 }}><ColInput label="집계 컬럼" value={form.agg_col} onChange={v => u("agg_col", v)} columns={allColumns} placeholder="그룹화 컬럼" /></div>
      </div>
      {form.agg_col && <div style={{ marginBottom: 8 }}><div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 2 }}>집계 방법</div>
        <select value={form.agg_method || ""} onChange={e => u("agg_method", e.target.value)} style={S}>
          <option value="">없음 (raw)</option><option value="mean">평균</option><option value="sum">합계</option><option value="count">개수</option><option value="min">최소</option><option value="max">최대</option>
        </select></div>}
      <ColInput label="시간 컬럼" value={form.time_col} onChange={v => u("time_col", v)} columns={allColumns} placeholder="시간 범위 필터용" />
    </>}
    <ColInput label="SQL 필터" value={form.filter_expr} onChange={v => u("filter_expr", v)} columns={allColumns} placeholder="예: col == 'value'" guide={sqlG} />
    <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
      <div style={{ flex: 1 }}><div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 2 }}>X 라벨</div><input value={form.x_label || ""} onChange={e => u("x_label", e.target.value)} style={S} /></div>
      <div style={{ flex: 1 }}><div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 2 }}>Y 라벨</div><input value={form.y_label || ""} onChange={e => u("y_label", e.target.value)} style={S} /></div>
      <div style={{ flex: 1 }}><div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 2 }}>Days (전체)</div>
        <input type="number" value={form.days || ""} onChange={e => u("days", e.target.value)} style={S} placeholder="전체" /></div>
    </div>
    {/* v6: Spec lines + SPC */}
    {!isPieOrBin && !isWaferMap && <div style={{ display: "flex", gap: 8, marginBottom: 8, padding: "8px 10px", background: "rgba(239,68,68,0.04)", borderRadius: 6, border: "1px solid rgba(239,68,68,0.1)" }}>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 10, color: BAD.fg, marginBottom: 2, fontWeight: 600 }}>USL</div>
        <input type="number" step="any" value={form.usl ?? ""} onChange={e => u("usl", e.target.value === "" ? null : parseFloat(e.target.value))} style={{ ...S, fontSize: 11 }} placeholder="상한선" />
      </div>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 10, color: OK.fg, marginBottom: 2, fontWeight: 600 }}>Target</div>
        <input type="number" step="any" value={form.target ?? ""} onChange={e => u("target", e.target.value === "" ? null : parseFloat(e.target.value))} style={{ ...S, fontSize: 11 }} placeholder="중앙값" />
      </div>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 10, color: BAD.fg, marginBottom: 2, fontWeight: 600 }}>LSL</div>
        <input type="number" step="any" value={form.lsl ?? ""} onChange={e => u("lsl", e.target.value === "" ? null : parseFloat(e.target.value))} style={{ ...S, fontSize: 11 }} placeholder="하한선" />
      </div>
      <div style={{ flex: "0 0 auto", display: "flex", alignItems: "flex-end", paddingBottom: 2 }}>
        <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 10, color: WARN.fg, whiteSpace: "nowrap" }}>
          <input type="checkbox" checked={form.enable_spc || false} onChange={e => u("enable_spc", e.target.checked)} style={{ accentColor: WARN.fg }} />SPC
        </label>
      </div>
    </div>}
    {/* v7: Extra spec lines (multi) */}
    {!isPieOrBin && !isWaferMap && <div style={{ marginBottom: 8, padding: "8px 10px", background: "rgba(139,92,246,0.04)", borderRadius: 6, border: "1px solid rgba(139,92,246,0.15)" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
        <span style={{ fontSize: 10, color: PURPLE.fg, fontWeight: 700 }}>추가 Spec Line ({(form.spec_lines || []).length})</span>
        <span onClick={() => u("spec_lines", [...(form.spec_lines || []), { name: "", value: 0, kind: "custom", color: PURPLE.fg, style: "dashed" }])}
              style={{ cursor: "pointer", color: PURPLE.fg, fontSize: 11, fontWeight: 600 }}>+ 추가</span>
      </div>
      {(form.spec_lines || []).map((sl, i) => (
        <div key={i} style={{ display: "flex", gap: 6, marginBottom: 4 }}>
          <input value={sl.name || ""} onChange={e => { const arr = [...form.spec_lines]; arr[i] = { ...sl, name: e.target.value }; u("spec_lines", arr); }} placeholder="이름" style={{ ...S, fontSize: 10, flex: 2 }} />
          <input type="number" step="any" value={sl.value ?? ""} onChange={e => { const arr = [...form.spec_lines]; arr[i] = { ...sl, value: e.target.value === "" ? 0 : parseFloat(e.target.value) }; u("spec_lines", arr); }} placeholder="값" style={{ ...S, fontSize: 10, flex: 1 }} />
          <select value={sl.kind || "custom"} onChange={e => { const arr = [...form.spec_lines]; arr[i] = { ...sl, kind: e.target.value }; u("spec_lines", arr); }} style={{ ...S, fontSize: 10, width: 80 }}>
            <option value="usl">USL</option><option value="lsl">LSL</option><option value="target">Target</option><option value="custom">사용자</option>
          </select>
          <input type="color" value={sl.color || PURPLE.fg} onChange={e => { const arr = [...form.spec_lines]; arr[i] = { ...sl, color: e.target.value }; u("spec_lines", arr); }} style={{ width: 32, height: 24, border: "none", background: "transparent", cursor: "pointer" }} />
          <select value={sl.style || "dashed"} onChange={e => { const arr = [...form.spec_lines]; arr[i] = { ...sl, style: e.target.value }; u("spec_lines", arr); }} style={{ ...S, fontSize: 10, width: 70 }}>
            <option value="solid">실선</option><option value="dashed">점선</option>
          </select>
          <span onClick={() => u("spec_lines", form.spec_lines.filter((_, j) => j !== i))} style={{ cursor: "pointer", color: BAD.fg, fontSize: 14, padding: "2px 6px" }}>✕</span>
        </div>
      ))}
    </div>}
    {isPieOrBin && <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
      <div style={{ flex: 1 }}><div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 2 }}>Bin 개수</div>
        <input type="number" value={form.bin_count || ""} onChange={e => u("bin_count", e.target.value)} style={S} placeholder="10" /></div>
      {form.chart_type === "binning" && <div style={{ flex: 1 }}><div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 2 }}>Bin 너비</div>
        <input type="number" step="0.01" value={form.bin_width || ""} onChange={e => u("bin_width", e.target.value)} style={S} placeholder="자동" /></div>}
    </div>}
        {/* Advanced options */}
    <div style={{ marginBottom: 8 }}>
      <span onClick={() => setShowAdv(!showAdv)} style={{ fontSize: 11, color: "var(--accent)", cursor: "pointer" }}>{showAdv ? "▼" : "▶"} 고급 설정</span>
      {showAdv && <div style={{ marginTop: 8, padding: 12, background: "var(--bg-primary)", borderRadius: 6, border: "1px solid var(--border)" }}>
        <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
          <div style={{ flex: 1 }}><div style={{ fontSize: 10, color: "var(--text-secondary)" }}>점 크기</div>
            <input type="number" value={form.point_size || ""} onChange={e => u("point_size", e.target.value)} style={{ ...S, fontSize: 10 }} placeholder="3" /></div>
          <div style={{ flex: 1 }}><div style={{ fontSize: 10, color: "var(--text-secondary)" }}>불투명도 (0-1)</div>
            <input type="number" step="0.1" value={form.opacity || ""} onChange={e => u("opacity", e.target.value)} style={{ ...S, fontSize: 10 }} placeholder="0.7" /></div>
          <div style={{ flex: 1 }}><div style={{ fontSize: 10, color: "var(--text-secondary)" }}>최대 점 수</div>
            <input type="number" value={form.limit_points || ""} onChange={e => u("limit_points", e.target.value)} style={{ ...S, fontSize: 10 }} placeholder="5000" /></div>
        </div>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, marginBottom: 6 }}>
          <input type="checkbox" checked={form.exclude_null !== false} onChange={e => u("exclude_null", e.target.checked)} style={{ accentColor: "var(--accent)" }} />null / (null) / NaN 값 제외</label>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, marginBottom: 6 }}>
          <input type="checkbox" checked={form.sort_x || false} onChange={e => u("sort_x", e.target.checked)} style={{ accentColor: "var(--accent)" }} />X축 정렬</label>
        {/* v8.4.3: Fitting line degree + R^2 — scatter/line 차트에서 추세선 표시 */}
        <div style={{ display: "flex", gap: 10, marginBottom: 6, alignItems: "center", padding: "6px 8px", background: "var(--bg-card)", borderRadius: 5, border: "1px solid var(--border)" }}>
          <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, flex: "0 0 auto" }}>
            <input type="checkbox" checked={form.fit_line_enabled || false} onChange={e => u("fit_line_enabled", e.target.checked)} style={{ accentColor: "var(--accent)" }} />Fitting Line</label>
          <div style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11, opacity: form.fit_line_enabled ? 1 : 0.5 }}>
            <span style={{ color: "var(--text-secondary)" }}>차수</span>
            <select disabled={!form.fit_line_enabled} value={form.fit_line_degree || 1} onChange={e => u("fit_line_degree", parseInt(e.target.value))} style={{ ...S, width: "auto", fontSize: 10 }}>
              <option value={1}>1차 (선형)</option><option value={2}>2차</option><option value={3}>3차</option><option value={4}>4차</option>
            </select>
          </div>
          <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, opacity: form.fit_line_enabled ? 1 : 0.5 }}>
            <input type="checkbox" disabled={!form.fit_line_enabled} checked={form.fit_line_show_r2 || false} onChange={e => u("fit_line_show_r2", e.target.checked)} style={{ accentColor: "var(--accent)" }} />R² 표시</label>
        </div>
        {/* v8.5.0: 그룹 가시성 (모든 유저) */}
        <div style={{ fontSize: 10, color: "var(--text-secondary)", marginBottom: 4, marginTop: 8, fontWeight: 600 }}>그룹 가시성 (비어있으면 공개)</div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {myGroups.length === 0 && <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>가입된 그룹 없음</span>}
          {myGroups.map(g => {
            const on = (form.group_ids || []).includes(g.id);
            return <label key={g.id} style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11, padding: "3px 8px", borderRadius: 999, border: "1px solid " + (on ? "var(--accent)" : "var(--border)"), background: on ? "var(--accent)22" : "transparent", cursor: "pointer" }}>
              <input type="checkbox" checked={on} onChange={e => {
                const s = new Set(form.group_ids || []);
                if (e.target.checked) s.add(g.id); else s.delete(g.id);
                u("group_ids", Array.from(s));
              }} style={{ accentColor: "var(--accent)" }} />
              {g.name}
            </label>;
          })}
        </div>

        {isAdmin && <>
          <div style={{ fontSize: 10, color: "var(--text-secondary)", marginBottom: 4, marginTop: 8, fontWeight: 600 }}>관리자 옵션 — 공개범위</div>
          <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
            <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11 }}>
              <select value={form.visible_to || "all"} onChange={e => {
                const v = e.target.value;
                u("visible_to", v);
                if (v !== "groups") u("group_ids", []);
              }} style={{ ...S, width: "auto", fontSize: 10 }}>
                <option value="all">모두에게 표시</option>
                <option value="admin">관리자 전용</option>
                <option value="groups">특정 그룹에게만</option>
              </select>
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11 }}>
              <input type="checkbox" checked={form.no_schedule || false} onChange={e => u("no_schedule", e.target.checked)} style={{ accentColor: "var(--accent)" }} />자동 새로고침 제외</label>
          </div>
          {(form.visible_to === "groups") && (
            <div style={{ marginTop: 8, padding: 8, borderRadius: 6, background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
              <div style={{ fontSize: 10, color: "var(--text-secondary)", marginBottom: 4 }}>
                선택한 그룹 멤버에게만 노출 ({(form.group_ids || []).length} 그룹 선택). 비워두면 admin 외엔 안 보입니다.
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
                {(myGroups || []).length === 0 && <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>등록된 그룹이 없습니다 (Admin → 그룹 관리)</span>}
                {(myGroups || []).map(g => {
                  const id = g.id || g.group_id || g.name;
                  const sel = (form.group_ids || []).includes(id);
                  return (
                    <span key={id} onClick={() => {
                      const cur = form.group_ids || [];
                      u("group_ids", sel ? cur.filter(x => x !== id) : [...cur, id]);
                    }} style={{
                      padding: "3px 10px", borderRadius: 999, fontSize: 10, cursor: "pointer", fontWeight: 600,
                      background: sel ? "var(--accent)" : "var(--bg-card)",
                      color: sel ? WHITE : "var(--text-secondary)",
                      border: "1px solid " + (sel ? "var(--accent)" : "var(--border)"),
                    }}>{g.name || id}</span>
                  );
                })}
              </div>
            </div>
          )}
        </>}
      </div>}
    </div>
    {/* Preview */}
    <div style={{ marginBottom: 12 }}>
      <button onClick={runPreview} disabled={prevLoading} style={{ padding: "6px 16px", borderRadius: 5, border: `1px solid ${BLUE.fg}`, background: BLUE.bg, color: BLUE.fg, fontSize: 11, fontWeight: 600, cursor: "pointer" }}>
        {prevLoading ? "..." : "미리보기 (10행)"}</button>
      {preview && !preview.error && <div style={{ marginTop: 8, background: "var(--bg-primary)", borderRadius: 6, border: "1px solid var(--border)", padding: 8, maxHeight: 180, overflow: "auto" }}>
        <div style={{ fontSize: 10, color: "var(--text-secondary)", marginBottom: 4 }}>합계: {preview.total} 행</div>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 10 }}>
          <thead><tr>{preview.columns?.map(c => <th key={c} style={{ textAlign: "left", padding: "3px 6px", borderBottom: "1px solid var(--border)", fontSize: 9 }}>{c}</th>)}</tr></thead>
          <tbody>{preview.rows?.map((r, i) => <tr key={i}>{preview.columns?.map(c => <td key={c} style={{ padding: "2px 6px", borderBottom: "1px solid var(--border)" }}>{r[c] == null ? "" : String(r[c])}</td>)}</tr>)}</tbody>
        </table>
      </div>}
      {preview?.error && <div style={{ marginTop: 6, fontSize: 11, color: BAD.fg }}>{preview.error}</div>}
    </div>
    <div style={{ display: "flex", gap: 8 }}>
      <button onClick={doSave} style={{ flex: 1, padding: 8, borderRadius: 6, border: "none", background: "var(--accent)", color: WHITE, fontWeight: 600, cursor: "pointer" }}>저장</button>
      <button onClick={onClose} style={{ padding: "8px 16px", borderRadius: 6, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", cursor: "pointer" }}>취소</button>
    </div>
  </div>);
}

/* ═══ Main Dashboard ═══ */
// v7.2: Global cross-chart selection context — Spotfire-style "mark a point → all matching highlight"
const SelectionContext = createContext({ marks: new Set(), toggle: () => { }, clear: () => { } });

export default function My_Dashboard({ user }) {
  const [charts, setCharts] = useState([]);
  const [snapshots, setSnapshots] = useState({});
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  const isAdmin = user?.role === "admin";
  const canEdit = isAdmin || user?.chart_edit === true;  // admin or chart_edit permission
  // v7.2: Cross-chart marks (LOT_WF or whatever selection_key is) — shared across all charts
  const [marks, setMarks] = useState(new Set());
  const toggleMark = (v) => setMarks(prev => { const s = new Set(prev); s.has(v) ? s.delete(v) : s.add(v); return s; });
  const clearMarks = () => setMarks(new Set());

  const load = () => {
    Promise.all([sf(API + "/charts"), sf(API + "/snapshots")])
      .then(([c, s]) => { setCharts(c.charts || []); setSnapshots(s.snapshots || {}); setLoading(false); })
      .catch(() => setLoading(false));
  };
  // v8.1.5: refresh interval from admin settings (default 10 min, admin can change via bottom-right gear)
  const [refreshMin, setRefreshMin] = useState(10);
  useEffect(() => {
    sf("/api/admin/settings").then(s => {
      if (s && typeof s.dashboard_refresh_minutes === "number") setRefreshMin(s.dashboard_refresh_minutes);
      if (s?.dashboard_sections && typeof s.dashboard_sections === "object") {
        setDashboardSections({ ...DASHBOARD_SECTIONS_DEFAULT, ...s.dashboard_sections });
      }
    }).catch(() => {});
  }, []);
  useEffect(() => { load(); const ms = Math.max(1, refreshMin) * 60 * 1000; const iv = setInterval(load, ms); return () => clearInterval(iv); }, [refreshMin]);
  const saveChart = (form) => sf(API + "/charts/save", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(form) }).then(() => { setEditing(null); load(); }).catch(e => alert(e.message));
  const deleteChart = (id) => { if (!confirm("삭제하시겠습니까?")) return; sf(API + "/charts/delete?chart_id=" + id, { method: "POST" }).then(load); };
  const doRefresh = () => { setRefreshing(true); sf(API + "/refresh", { method: "POST" }).then(() => setTimeout(() => { load(); setRefreshing(false); }, 3000)).catch(() => setRefreshing(false)); };

  const [expanded, setExpanded] = useState(null); // chart id for fullscreen view
  // v8.4.8: Group visibility + size resize + marks→filter chart
  const [hiddenGroups, setHiddenGroups] = useState(() => {
    try { return new Set(JSON.parse(localStorage.getItem("flow_dash_hidden_groups") || "[]")); }
    catch { return new Set(); }
  });
  const [layoutDensity, setLayoutDensity] = useState(() => localStorage.getItem("flow_dash_density") || "comfortable");
  const [dashProducts, setDashProducts] = useState([]);
  const [focusProduct, setFocusProduct] = useState("");
  const [targetStepId, setTargetStepId] = useState("");
  const [lotQuery, setLotQuery] = useState("");
  const [progressDays, setProgressDays] = useState(30);
  const [sampleLots, setSampleLots] = useState(3);
  const [knobCol, setKnobCol] = useState("KNOB_5.0 PC");
  const [knobValue, setKnobValue] = useState("");
  const [fabProgress, setFabProgress] = useState(null);
  const [fabSummary, setFabSummary] = useState(null);
  const [fabLoading, setFabLoading] = useState(false);
  const [speedFilter, setSpeedFilter] = useState("all");
  const [trendAlerts, setTrendAlerts] = useState([]);
  const [trendLoading, setTrendLoading] = useState(false);
  const [dashboardView, setDashboardView] = useState(() => localStorage.getItem("flow_dashboard_view") || "charts");
  const [dashboardSections, setDashboardSections] = useState(DASHBOARD_SECTIONS_DEFAULT);
  const visibleSections = isAdmin
    ? { charts: true, progress: true, alerts: true }
    : { ...DASHBOARD_SECTIONS_DEFAULT, ...dashboardSections };
  const toggleGroup = (g) => setHiddenGroups(prev => {
    const s = new Set(prev); s.has(g) ? s.delete(g) : s.add(g);
    localStorage.setItem("flow_dash_hidden_groups", JSON.stringify([...s]));
    return s;
  });
  const resizeChart = (c, w, h) => sf(API + "/charts/save", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...c, width: w, height: h })
  }).then(load).catch(e => alert(e.message));

  // Filter charts by visibility + group hide
  const visibleByVis = charts.filter(c => !(c.visible_to === "admin" && !isAdmin));
  const groupCounts = {};
  visibleByVis.forEach(c => { const g = c.group || "기타"; groupCounts[g] = (groupCounts[g] || 0) + 1; });
  const groupNames = Object.keys(groupCounts).sort();
  const visibleCharts = visibleByVis.filter(c => !hiddenGroups.has(c.group || "기타"));
  const snapshotStamp = useMemo(
    () => Object.values(snapshots || {}).map((s) => `${s?.computed_at || ""}:${s?.oos_count || 0}:${(s?.trend_alert || {}).count || 0}`).join("|"),
    [snapshots]
  );
  const densityMeta = {
    compact: { row: 360, gap: 12, label: "촘촘함" },
    comfortable: { row: 440, gap: 14, label: "기본" },
    presentation: { row: 540, gap: 18, label: "넓게" },
  }[layoutDensity] || { row: 440, gap: 14, label: "기본" };
  useEffect(() => {
    localStorage.setItem("flow_dashboard_view", dashboardView);
  }, [dashboardView]);
  useEffect(() => {
    const allowed = Object.entries(visibleSections).filter(([, on]) => on).map(([key]) => key);
    if (!allowed.includes(dashboardView)) setDashboardView(allowed[0] || "charts");
  }, [dashboardView, visibleSections.charts, visibleSections.progress, visibleSections.alerts]);
  useEffect(() => {
    if (!visibleSections.progress) {
      setDashProducts([]);
      return;
    }
    sf(API + "/products").then((d) => {
      const vals = new Set();
      (d.products || []).forEach((s) => {
        const cand = String(s.product || "").trim();
        if (cand) vals.add(cand);
      });
      const arr = [...vals].sort();
      setDashProducts(arr);
      if (!focusProduct && arr.length) setFocusProduct(arr[0]);
    }).catch(() => setDashProducts([]));
  }, [visibleSections.progress]);

  useEffect(() => {
    if (!visibleSections.progress || !focusProduct) return;
    setFabLoading(true);
    const q = new URLSearchParams({ product: focusProduct, days: String(progressDays || 30), limit: "24", sample_lots: String(sampleLots || 3) });
    if (targetStepId) q.set("target_step_id", targetStepId);
    if (lotQuery) q.set("lot_query", lotQuery);
    if (knobCol) q.set("knob_col", knobCol);
    if (knobValue) q.set("knob_value", knobValue);
    Promise.all([
      sf(API + "/fab-progress?" + q.toString()).catch(() => null),
      sf(API + "/summary?product=" + encodeURIComponent(focusProduct)).catch(() => null),
    ]).then(([progress, summary]) => {
      setFabProgress(progress);
      setFabSummary(summary);
    }).finally(() => setFabLoading(false));
  }, [visibleSections.progress, focusProduct, targetStepId, lotQuery, progressDays, sampleLots, knobCol, knobValue]);

  useEffect(() => {
    if (!visibleSections.alerts) {
      setTrendAlerts([]);
      setTrendLoading(false);
      return;
    }
    setTrendLoading(true);
    sf(API + "/trend-alerts?limit=8").then((d) => setTrendAlerts(d.alerts || [])).catch(() => setTrendAlerts([])).finally(() => setTrendLoading(false));
  }, [visibleSections.alerts, charts.length, snapshotStamp]);

  // Marks → new chart filter (사용자 요구: 마킹한 것으로 옆에 차트 만들기)
  const makeFilteredChart = () => {
    if (!marks.size) return;
    const first = visibleCharts[0] || {};
    const key = first.selection_key || "LOT_WF";
    const quoted = [...marks].slice(0, 60).map(v => `'${String(v).replace(/'/g, "''")}'`).join(",");
    setEditing({
      title: `[필터] ${key} ${marks.size}개`,
      source_type: first.source_type || "base_file",
      root: first.root || "",
      product: first.product || "",
      file: first.file || "",
      chart_type: "scatter",
      x_col: first.x_col || "",
      y_expr: first.y_expr || "",
      color_col: first.color_col || "",
      filter_expr: `${key} IN (${quoted})`,
      selection_key: key,
      group: first.group || "",
      width: 2, height: 1,
    });
  };

  if (loading) return <div style={{ padding: 40, textAlign: "center" }}><Loading text="로딩 중..." /></div>;
  return (<SelectionContext.Provider value={{ marks, toggle: toggleMark, clear: clearMarks }}>
  <div style={{ padding: "16px 18px", background: "var(--bg-primary)", color: "var(--text-primary)", maxWidth: "none", margin: 0, height: "100%", minHeight: 0, overflow: "auto", boxSizing: "border-box" }}>
    <PageHeader
      title="대시보드"
      subtitle={`차트 ${visibleCharts.length}개 · 그룹 ${groupNames.length}개 · 밀도 ${densityMeta.label}`}
      style={{ marginBottom: 16, borderRadius: 10, border: `1px solid ${uxColors.border}` }}
      right={<div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        {marks.size > 0 && (
          <div style={{ display: "flex", gap: 6, alignItems: "center", padding: "4px 10px", borderRadius: 20, background: "var(--accent-glow)", border: "1px solid var(--accent)" }}>
            <span style={{ fontSize: 11, fontFamily: "monospace", color: "var(--accent)", fontWeight: 700 }}>★ {marks.size} 개 표시됨</span>
            <span style={{ fontSize: 9, color: "var(--text-secondary)", maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{[...marks].slice(0, 3).join(", ")}{marks.size > 3 ? ` +${marks.size - 3}` : ""}</span>
            {canEdit && <span onClick={makeFilteredChart} title="표시된 항목으로 필터링된 새 차트 생성" style={{ cursor: "pointer", fontSize: 10, color: WHITE, padding: "2px 8px", background: "var(--accent)", borderRadius: 4, fontWeight: 700 }}>→ 필터 차트</span>}
            <span onClick={clearMarks} style={{ cursor: "pointer", fontSize: 12, color: "var(--text-secondary)", marginLeft: 4 }}>✕</span>
          </div>
        )}
        {isAdmin && <Button variant="subtle" onClick={doRefresh} disabled={refreshing}>{refreshing ? "계산 중..." : "전체 새로고침"}</Button>}
        <div style={{ display: "inline-flex", gap: 4, alignItems: "center", padding: "4px 6px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg-card)" }}>
          <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>밀도</span>
          {[["compact","촘촘"],["comfortable","기본"],["presentation","넓게"]].map(([k,l])=><span key={k} onClick={()=>{setLayoutDensity(k);localStorage.setItem("flow_dash_density",k);}} style={{cursor:"pointer",fontSize:10,padding:"2px 8px",borderRadius:4,background:layoutDensity===k?"var(--accent-glow)":"transparent",color:layoutDensity===k?"var(--accent)":"var(--text-secondary)",fontWeight:layoutDensity===k?700:500}}>{l}</span>)}
        </div>
        {canEdit && <Button variant="primary" onClick={() => setEditing({})}>+ 차트 추가</Button>}
        {/* v8.7.4: 전 탭 톱니 좌하단 통일 */}
        <PageGear title="대시보드 설정" canEdit={isAdmin} position="bottom-left">
          <DashboardSettings isAdmin={isAdmin} refreshMin={refreshMin} setRefreshMin={setRefreshMin}
            sections={dashboardSections} setSections={setDashboardSections} />
        </PageGear>
      </div>}
    />
    {editing !== null && <ChartEditor cfg={editing} onSave={saveChart} onClose={() => setEditing(null)} isAdmin={isAdmin} />}
    <div style={{ paddingRight: 2 }}>
    <DashboardSectionNav
      view={dashboardView}
      setView={setDashboardView}
      counts={{ charts: visibleCharts.length, products: dashProducts.length, alerts: trendAlerts.length }}
      sections={visibleSections}
    />

    {dashboardView === "progress" && visibleSections.progress && (
      <FabProgressPanel
        loading={fabLoading}
        data={fabProgress}
        summary={fabSummary}
        speedFilter={speedFilter}
        setSpeedFilter={setSpeedFilter}
        product={focusProduct}
        setProduct={setFocusProduct}
        products={dashProducts}
        targetStepId={targetStepId}
        setTargetStepId={setTargetStepId}
        lotQuery={lotQuery}
        setLotQuery={setLotQuery}
        progressDays={progressDays}
        setProgressDays={setProgressDays}
        sampleLots={sampleLots}
        setSampleLots={setSampleLots}
        knobCol={knobCol}
        setKnobCol={setKnobCol}
        knobValue={knobValue}
        setKnobValue={setKnobValue}
      />
    )}
    {dashboardView === "alerts" && visibleSections.alerts && <TrendAlertPanel loading={trendLoading} alerts={trendAlerts} />}

    {/* v8.4.8: 그룹 필터 칩 (그룹이 1개 이상이면 노출). 클릭해서 숨김/표시 토글. */}
    {dashboardView === "charts" && groupNames.length > 1 && <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 12, alignItems: "center" }}>
      <span style={{ fontSize: 10, color: "var(--text-secondary)", fontFamily: "monospace", marginRight: 4 }}>그룹:</span>
      {groupNames.map(g => {
        const hidden = hiddenGroups.has(g);
        return <span key={g} onClick={() => toggleGroup(g)} style={{
          cursor: "pointer", padding: "3px 10px", borderRadius: 12, fontSize: 11, fontFamily: "monospace",
          border: "1px solid " + (hidden ? "var(--border)" : "var(--accent)"),
          background: hidden ? "transparent" : "var(--accent-glow)",
          color: hidden ? "var(--text-secondary)" : "var(--accent)",
          fontWeight: hidden ? 400 : 700, opacity: hidden ? 0.6 : 1,
        }} title={hidden ? `${g} 표시` : `${g} 숨기기`}>
          {hidden ? "○" : "●"} {g} <span style={{ fontSize: 9, opacity: 0.7 }}>({groupCounts[g]})</span>
        </span>;
      })}
      {hiddenGroups.size > 0 && <span onClick={() => { setHiddenGroups(new Set()); localStorage.setItem("flow_dash_hidden_groups", "[]"); }}
        style={{ cursor: "pointer", fontSize: 10, color: "var(--accent)", marginLeft: 8 }}>모두 표시</span>}
    </div>}

    {dashboardView === "charts" && visibleCharts.length === 0 && !editing && <div style={{ textAlign: "center", padding: 60, color: "var(--text-secondary)" }}>차트 없음.{canEdit ? " + 차트 추가 를 클릭하세요." : ""}</div>}
    {/* 차트 뷰는 고정 row 높이를 없애고 카드가 자연 높이로 커지게 바꿔 잘림을 줄인다. */}
    {dashboardView === "charts" && <div style={{ display: "grid", gridTemplateColumns: "repeat(12, minmax(0, 1fr))", gap: densityMeta.gap, alignContent: "start", paddingBottom: 12 }}>
      {visibleCharts.map(c => { const snap = snapshots[c.id]; const isAdminChart = c.visible_to === "admin";
        const bgColor = isAdminChart ? "linear-gradient(180deg, rgba(99,102,241,0.055), rgba(255,255,255,0))" : "linear-gradient(180deg, rgba(15,23,42,0.022), rgba(255,255,255,0))";
        const w = Math.max(1, Math.min(4, c.width || 1));
        const h = Math.max(1, Math.min(3, c.height || 1));
        // 12-col grid: width 1=3cols(S), 2=6cols(M), 3=9cols(L), 4=12cols(XL). 모바일에서는 auto-min 으로 접힘.
        const colSpan = { 1: "span 4", 2: "span 6", 3: "span 9", 4: "span 12" }[w] || "span 4";
        const cardMinHeight = Math.max(340, densityMeta.row * h);
        return (<div key={c.id} className="chart-card" style={{ position: "relative", background: bgColor, borderRadius: 8, border: isAdminChart ? "1.5px dashed rgba(99,102,241,0.36)" : "1px solid var(--border)", gridColumn: colSpan, minWidth: 280, minHeight: cardMinHeight, display:"flex", flexDirection:"column", overflow:"hidden" }} onDoubleClick={() => setExpanded(c.id)}>
        <div style={{flex:1,minHeight:0,padding:8}}>
          <ChartCanvas cfg={{ ...c, _spc: snap?.spc, _oos: snap?.oos_count, _heatmap_meta: snap?.heatmap_meta, _wafer_layout: snap?.wafer_layout, _wafer_map_meta: snap?.wafer_map_meta, table_columns: snap?.table_columns, cross_cols: snap?.cross_cols, cross_rows: snap?.cross_rows, cross_method: snap?.cross_method, cross_val_col: snap?.cross_val_col }} points={snap?.points} computedAt={snap?.computed_at} />
        </div>
        <div style={{ fontSize: 10, color: "var(--text-secondary)", padding: "6px 12px", display: "flex", justifyContent: "space-between", alignItems: "center", borderTop:"1px solid var(--border)", background:"rgba(0,0,0,0.02)" }}>
          <span>
            {c.group && <span style={{ color: "var(--accent)", fontWeight: 700, marginRight: 6 }}>[{c.group}]</span>}
            {isAdmin ? chartTypeLabel(c.chart_type) : "업데이트된 시각화"}
          </span>
          <span style={{ display: "flex", gap: 4, alignItems: "center" }}>
            {snap?.error && <span style={{ color: BAD.fg }} title={snap.error}>오류</span>}
            <span style={{fontSize:9,fontFamily:"monospace",color:"var(--text-secondary)"}}>{w}×{h}</span>
            {isAdmin && (isAdminChart
              ? <span style={{ fontSize: 8, fontWeight: 700, color: PURPLE.fg, background: PURPLE.soft, padding: "1px 5px", borderRadius: 3, border: `1px solid ${PURPLE.border}` }}>관리자</span>
              : <span style={{ fontSize: 8, fontWeight: 700, color: GREEN.fg, background: GREEN.soft, padding: "1px 5px", borderRadius: 3, border: `1px solid ${GREEN.border}` }}>사용자</span>)}
          </span>
        </div>
        <div className="chart-actions" style={{ position: "absolute", top: 8, right: 8, display: "flex", gap: 4, alignItems: "center" }}>
          {/* v8.4.8: 크기 피커 — S/M/L/XL (width) × 1/2/3 (height) */}
          {canEdit && <span onClick={e => e.stopPropagation()} style={{ display: "inline-flex", gap: 2, padding: "2px 4px", background: "rgba(0,0,0,0.55)", borderRadius: 4 }} title="크기 조절">
            {[[1,"S"],[2,"M"],[3,"L"],[4,"XL"]].map(([wv, wl]) => (
              <span key={wv} onClick={() => resizeChart(c, wv, h)} style={{ cursor: "pointer", fontSize: 10, fontWeight: 700, padding: "1px 5px", borderRadius: 3, color: w === wv ? WHITE : DIM_TEXT, background: w === wv ? "var(--accent)" : "transparent" }}>{wl}</span>
            ))}
            <span style={{ width: 1, background: DIVIDER_DARK, margin: "2px 2px" }} />
            {[1,2,3].map(hv => (
              <span key={hv} onClick={() => resizeChart(c, w, hv)} style={{ cursor: "pointer", fontSize: 10, fontWeight: 700, padding: "1px 5px", borderRadius: 3, color: h === hv ? WHITE : DIM_TEXT, background: h === hv ? BLUE.fg : "transparent" }}>{hv}</span>
            ))}
          </span>}
          <span onClick={(e) => { e.stopPropagation(); setExpanded(c.id); }} style={{ cursor: "pointer", fontSize: 11, color: WHITE, padding: "3px 8px", background: "var(--accent)", borderRadius: 4, fontWeight: 600, boxShadow: "0 1px 4px rgba(0,0,0,0.3)" }}>확대</span>
          {canEdit && <>
            <span onClick={(e) => { e.stopPropagation(); setEditing(c); }} style={{ cursor: "pointer", fontSize: 11, color: WHITE, padding: "3px 8px", background: BLUE.fg, borderRadius: 4, fontWeight: 600, boxShadow: "0 1px 4px rgba(0,0,0,0.3)" }}>편집</span>
            <span onClick={(e) => { e.stopPropagation(); sf(API + "/charts/copy?chart_id=" + c.id, { method: "POST" }).then(load); }} style={{ cursor: "pointer", fontSize: 11, color: WHITE, padding: "3px 8px", background: PURPLE.fg, borderRadius: 4, fontWeight: 600, boxShadow: "0 1px 4px rgba(0,0,0,0.3)" }}>복사</span>
            {isAdmin && <span onClick={(e) => { e.stopPropagation(); deleteChart(c.id); }} style={{ cursor: "pointer", fontSize: 11, color: WHITE, padding: "3px 8px", background: BAD.fg, borderRadius: 4, fontWeight: 600, boxShadow: "0 1px 4px rgba(0,0,0,0.3)" }}>삭제</span>}
          </>}
        </div>
      </div>); })}
    </div>}
  </div>

    {/* Fullscreen chart modal */}
    {expanded && (() => {
      const c = charts.find(ch => ch.id === expanded); const snap = snapshots[expanded];
      if (!c) return null;
      return (<div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.85)", zIndex: 9999, display: "flex", alignItems: "center", justifyContent: "center", padding: 24 }} onClick={() => setExpanded(null)}>
        <div onClick={e => e.stopPropagation()} style={{ background: "var(--bg-secondary)", borderRadius: 12, padding: 24, width: "95vw", maxWidth: 1200, maxHeight: "90vh", overflow: "auto", border: "1px solid var(--border)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
            <div style={{ fontSize: 18, fontWeight: 700, fontFamily: "monospace", color: "var(--accent)" }}>{c.title}</div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>{snap?.total?.toLocaleString()} 개 점 | {chartTypeLabel(c.chart_type)}</span>
              <span onClick={() => setExpanded(null)} style={{ cursor: "pointer", fontSize: 20, color: "var(--text-secondary)", padding: "4px 8px" }}>✕</span>
            </div>
          </div>
          <ChartCanvas cfg={{ ...c, point_size: (c.point_size || 3) + 1, _spc: snap?.spc, _oos: snap?.oos_count, _heatmap_meta: snap?.heatmap_meta, _wafer_layout: snap?.wafer_layout, _wafer_map_meta: snap?.wafer_map_meta, table_columns: snap?.table_columns, cross_cols: snap?.cross_cols, cross_rows: snap?.cross_rows, cross_method: snap?.cross_method, cross_val_col: snap?.cross_val_col }} points={snap?.points} computedAt={snap?.computed_at} />
          {isAdmin && (
            <details style={{ marginTop: 8 }}>
              <summary style={{ fontSize: 10, color: "var(--text-secondary)", cursor: "pointer" }}>debug</summary>
              <div style={{ marginTop: 6, fontSize: 10, color: "var(--text-secondary)", fontFamily: "monospace" }}>
                {c.file || `${c.root}/${c.product}`} | type={c.chart_type} | X: {c.x_col}{c.y_expr ? " | Y: " + c.y_expr : ""}{c.color_col ? " | color: " + c.color_col : ""}{c.filter_expr ? " | filter: " + c.filter_expr : ""}
              </div>
            </details>
          )}
        </div>
      </div>);
    })()}
  </div>
  </SelectionContext.Provider>);
}

/* ═══ v8.5.2 Dashboard Settings panel (PageGear 내부) ═══ */
function DashboardSettings({ isAdmin, refreshMin, setRefreshMin, sections, setSections }) {
  const [val, setVal] = useState(refreshMin);
  const [bgVal, setBgVal] = useState(10);
  const [sectionDraft, setSectionDraft] = useState({ ...DASHBOARD_SECTIONS_DEFAULT, ...(sections || {}) });
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");
  useEffect(() => {
    sf("/api/admin/settings").then(s => {
      if (typeof s.dashboard_refresh_minutes === "number") setVal(s.dashboard_refresh_minutes);
      if (typeof s.dashboard_bg_refresh_minutes === "number") setBgVal(s.dashboard_bg_refresh_minutes);
      if (s?.dashboard_sections && typeof s.dashboard_sections === "object") {
        setSectionDraft({ ...DASHBOARD_SECTIONS_DEFAULT, ...s.dashboard_sections });
      }
    }).catch(() => {});
  }, []);
  useEffect(() => {
    setSectionDraft({ ...DASHBOARD_SECTIONS_DEFAULT, ...(sections || {}) });
  }, [sections?.charts, sections?.progress, sections?.alerts]);
  const save = () => {
    setSaving(true); setMsg("");
    sf("/api/admin/settings/save", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        dashboard_refresh_minutes: Number(val) || 10,
        dashboard_bg_refresh_minutes: Number(bgVal) || 10,
        dashboard_sections: sectionDraft,
      }),
    }).then(() => {
      setMsg("저장 완료");
      setRefreshMin(Number(val) || 10);
      setSections && setSections(sectionDraft);
    })
      .catch(e => setMsg(e.message))
      .finally(() => setSaving(false));
  };
  const toggleSection = (key) => setSectionDraft(prev => ({ ...prev, [key]: !prev[key] }));
  return (
    <div>
      <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>자동 새로고침 주기 (분)</div>
      <input type="number" min={1} max={240} value={val} onChange={e => setVal(e.target.value)} disabled={!isAdmin}
        style={{ width: "100%", padding: "6px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12 }} />
      <div style={{ fontSize: 10, color: "var(--text-secondary)", marginTop: 4 }}>프론트가 차트를 다시 불러오는 주기. 1~240분.</div>

      <div style={{ fontSize: 12, fontWeight: 600, marginTop: 14, marginBottom: 6 }}>백그라운드 재계산 주기 (분)</div>
      <input type="number" min={1} max={240} value={bgVal} onChange={e => setBgVal(e.target.value)} disabled={!isAdmin}
        style={{ width: "100%", padding: "6px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12 }} />
      <div style={{ fontSize: 10, color: "var(--text-secondary)", marginTop: 4 }}>백엔드가 각 차트 스냅샷을 재계산하는 주기.</div>

      <div style={{ fontSize: 12, fontWeight: 600, marginTop: 14, marginBottom: 6 }}>일반 사용자 대시보드 공개 섹션</div>
      <div style={{ display: "grid", gap: 6 }}>
        {[
          ["charts", "차트"],
          ["progress", "FAB 진행"],
          ["alerts", "알림 감시"],
        ].map(([key, label]) => (
          <label key={key} style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 8px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg-primary)", fontSize: 12, cursor: isAdmin ? "pointer" : "default" }}>
            <input type="checkbox" checked={sectionDraft[key] !== false} disabled={!isAdmin} onChange={() => toggleSection(key)} />
            <span style={{ fontWeight: 700 }}>{label}</span>
            {key !== "charts" && <span style={{ marginLeft: "auto", fontSize: 10, color: "var(--text-secondary)" }}>기본 비공개</span>}
          </label>
        ))}
      </div>
      <div style={{ fontSize: 10, color: "var(--text-secondary)", marginTop: 4 }}>관리자는 설정과 관계없이 모든 섹션을 볼 수 있습니다.</div>

      {isAdmin && (
        <button onClick={save} disabled={saving}
          style={{ marginTop: 14, width: "100%", padding: "8px 12px", borderRadius: 6, border: "none", background: "var(--accent)", color: WHITE, fontWeight: 600, fontSize: 12, cursor: saving ? "wait" : "pointer" }}>
          {saving ? "저장 중..." : "저장"}
        </button>
      )}
      {msg && <div style={{ marginTop: 8, fontSize: 11, color: msg === "저장 완료" ? OK.fg : BAD.fg }}>{msg}</div>}

      <div style={{ marginTop: 18, padding: 10, background: "var(--bg-primary)", borderRadius: 6, fontSize: 10, color: "var(--text-secondary)", lineHeight: 1.6 }}>
        • 일반 유저는 값 확인만 가능 (편집은 관리자).<br/>
        • 차트별 exclude_null / fitting line 등은 각 차트의 편집 화면에서 설정합니다.
      </div>
    </div>
  );
}
