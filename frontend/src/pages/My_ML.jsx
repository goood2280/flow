import { useState, useEffect } from "react";
import Loading from "../components/Loading";
const API = "/api/ml";
const sf = (url, o) => fetch(url, o).then(r => { if (!r.ok) return r.json().then(d => { throw new Error(d.detail || "Error"); }); return r.json(); });
const COLORS = ["#6366f1","#f59e0b","#ec4899","#10b981","#3b82f6","#ef4444","#8b5cf6","#06b6d4","#f97316","#84cc16"];

/* ─── Feature Importance Bar Chart ─── */
function ImportanceBar({ importance }) {
  if (!importance?.length) return null;
  const top = importance.slice(0, 15);
  const maxV = Math.max(...top.map(i => i.importance));
  return (
    <div style={{ background: "var(--bg-card)", borderRadius: 8, border: "1px solid var(--border)", padding: 16 }}>
      <div style={{ fontSize: 12, fontWeight: 700, fontFamily: "monospace", color: "var(--accent)", marginBottom: 10 }}>피처 중요도 (상위 15)</div>
      {top.map((f, i) => (
        <div key={i} style={{ marginBottom: 6 }}>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, marginBottom: 2 }}>
            <span style={{ fontFamily: "monospace", color: "var(--text-primary)" }}>{f.direction === "+" ? "↑" : "↓"} {f.feature}</span>
            <span style={{ fontFamily: "monospace", color: "var(--accent)", fontWeight: 600 }}>{f.importance.toFixed(3)}</span>
          </div>
          <div style={{ height: 8, background: "var(--bg-hover)", borderRadius: 2, overflow: "hidden" }}>
            <div style={{ height: "100%", width: (f.importance / maxV * 100) + "%", background: f.direction === "+" ? "#10b981" : "#ef4444", transition: "width 0.3s" }} />
          </div>
        </div>
      ))}
    </div>
  );
}

/* ─── Prediction Scatter ─── */
function PredictionScatter({ scatter, isClassification }) {
  if (!scatter?.length) return null;
  const actuals = scatter.map(s => s.actual);
  const preds = scatter.map(s => s.predicted);
  const allV = [...actuals, ...preds];
  const minV = Math.min(...allV), maxV = Math.max(...allV), range = maxV - minV || 1;
  const W = 420, H = 320, pad = { t: 16, r: 16, b: 44, l: 54 };
  const cw = W - pad.l - pad.r, ch = H - pad.t - pad.b;
  const toX = v => pad.l + (v - minV) / range * cw;
  const toY = v => pad.t + ch - (v - minV) / range * ch;
  return (
    <div style={{ background: "var(--bg-card)", borderRadius: 8, border: "1px solid var(--border)", padding: 16 }}>
      <div style={{ fontSize: 12, fontWeight: 700, fontFamily: "monospace", color: "var(--accent)", marginBottom: 8 }}>실측 vs 예측</div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: "block" }}>
        {/* Diagonal ref */}
        <line x1={pad.l} y1={pad.t + ch} x2={W - pad.r} y2={pad.t} stroke="var(--text-secondary)" strokeDasharray="4,4" opacity={0.5} />
        {/* Grid */}
        {[0, 0.25, 0.5, 0.75, 1].map((f, i) => (<g key={i}>
          <line x1={pad.l} y1={pad.t + ch * (1 - f)} x2={W - pad.r} y2={pad.t + ch * (1 - f)} stroke="var(--border)" opacity={0.3} />
          <text x={pad.l - 6} y={pad.t + ch * (1 - f) + 3} textAnchor="end" fontSize={9} fill="var(--text-secondary)">{(minV + range * f).toFixed(2)}</text>
          <text x={pad.l + cw * f} y={H - pad.b + 12} textAnchor="middle" fontSize={9} fill="var(--text-secondary)">{(minV + range * f).toFixed(2)}</text>
        </g>))}
        {/* Points */}
        {scatter.map((s, i) => (
          <circle key={i} cx={toX(s.actual)} cy={toY(s.predicted)} r={2.5}
            fill={s.set === "test" ? "#f97316" : "#3b82f6"} opacity={0.7} />
        ))}
        {/* Axis labels */}
        <text x={pad.l + cw / 2} y={H - 6} textAnchor="middle" fontSize={11} fontWeight={700} fill="var(--accent)" fontFamily="monospace">실측</text>
        <text x={12} y={pad.t + ch / 2} transform={`rotate(-90,12,${pad.t + ch / 2})`} textAnchor="middle" fontSize={11} fontWeight={700} fill="var(--accent)" fontFamily="monospace">예측</text>
      </svg>
      <div style={{ display: "flex", gap: 10, fontSize: 10, marginTop: 4 }}>
        <span><span style={{ display: "inline-block", width: 8, height: 8, borderRadius: "50%", background: "#3b82f6", marginRight: 4 }} />학습</span>
        <span><span style={{ display: "inline-block", width: 8, height: 8, borderRadius: "50%", background: "#f97316", marginRight: 4 }} />테스트</span>
      </div>
    </div>
  );
}

/* ─── Analysis Pipeline Diagram ─── */
function AnalysisPipeline({ features, target, model, metrics }) {
  const fgroups = {};
  features.forEach(f => {
    const prefix = f.split("_")[0];
    fgroups[prefix] = (fgroups[prefix] || 0) + 1;
  });
  const groupKeys = Object.keys(fgroups);
  return (
    <div style={{ background: "var(--bg-card)", borderRadius: 8, border: "1px solid var(--border)", padding: 16 }}>
      <div style={{ fontSize: 12, fontWeight: 700, fontFamily: "monospace", color: "var(--accent)", marginBottom: 10 }}>분석 파이프라인</div>
      <div style={{ display: "flex", gap: 12, alignItems: "center", justifyContent: "space-between", flexWrap: "wrap" }}>
        {/* Feature groups */}
        <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 140 }}>
          <div style={{ fontSize: 10, color: "var(--text-secondary)", fontWeight: 600 }}>입력 피처</div>
          {groupKeys.map((g, i) => (
            <div key={g} style={{ padding: "4px 10px", borderRadius: 4, background: COLORS[i % COLORS.length] + "22", border: "1px solid " + COLORS[i % COLORS.length], fontSize: 11, fontWeight: 600, color: COLORS[i % COLORS.length] }}>
              {g} <span style={{ opacity: 0.7, fontWeight: 400 }}>({fgroups[g]})</span>
            </div>
          ))}
        </div>
        <div style={{ fontSize: 18, color: "var(--accent)" }}>→</div>
        {/* Model */}
        <div style={{ padding: "12px 20px", borderRadius: 8, background: "var(--accent-glow)", border: "2px solid var(--accent)", textAlign: "center", minWidth: 120 }}>
          <div style={{ fontSize: 9, color: "var(--text-secondary)", fontWeight: 600 }}>모델</div>
          <div style={{ fontSize: 13, fontWeight: 700, fontFamily: "monospace", color: "var(--accent)" }}>{model}</div>
        </div>
        <div style={{ fontSize: 18, color: "var(--accent)" }}>→</div>
        {/* Target + metrics */}
        <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 140 }}>
          <div style={{ fontSize: 10, color: "var(--text-secondary)", fontWeight: 600 }}>타겟: <span style={{ color: "var(--text-primary)", fontFamily: "monospace" }}>{target}</span></div>
          {metrics?.accuracy != null && <div style={{ fontSize: 14, fontWeight: 700, color: "#10b981", fontFamily: "monospace" }}>정확도: {(metrics.accuracy * 100).toFixed(1)}%</div>}
          {metrics?.r2 != null && <div style={{ fontSize: 14, fontWeight: 700, color: "#10b981", fontFamily: "monospace" }}>R² = {metrics.r2.toFixed(4)}</div>}
          {metrics?.rmse != null && <div style={{ fontSize: 11, color: "var(--text-secondary)", fontFamily: "monospace" }}>RMSE: {metrics.rmse.toFixed(4)}</div>}
          {metrics?.n_test != null && <div style={{ fontSize: 10, color: "var(--text-secondary)" }}>n_test: {metrics.n_test}</div>}
        </div>
      </div>
    </div>
  );
}

/* ─── v7: Process Window visualization ─── */
const FAM_COLORS = {
  KNOB: "#8b5cf6", MASK: "#ec4899", QTIME: "#06b6d4",
  FAB: "#f59e0b", INLINE: "#3b82f6", VM: "#10b981",
  ET: "#ef4444", YLD: "#dc2626", OTHER: "#6b7280",
};
// v7.1: level → color for L0-L3 causality viz
const LEVEL_COLORS = {
  "L0": "#8b5cf6",  // upstream process (KNOB/MASK/FAB/VM)
  "L1": "#3b82f6",  // inline metrology
  "L2": "#f59e0b",  // electrical test
  "L3": "#ef4444",  // yield / outcome
};
const LEVEL_DESC = {
  "L0": "L0 — FAB / VM / MASK / KNOB (process config)",
  "L1": "L1 — INLINE (in-line metrology)",
  "L2": "L2 — ET (electrical test)",
  "L3": "L3 — YLD (yield outcome)",
};

function ProcessWindowFlow({ steps, target, targetStep }) {
  if (!steps?.length) return null;
  const maxSum = Math.max(...steps.map(s => s.sum_imp || 0), 0.001);
  const families = ["KNOB", "MASK", "QTIME", "FAB", "INLINE", "VM", "ET", "OTHER"];
  // Group by family for lane layout
  const byFam = {}; families.forEach(f => byFam[f] = []);
  steps.forEach(s => { if (byFam[s.family]) byFam[s.family].push(s); });
  const W = 780, laneH = 58, pad = 20;
  const activeFams = families.filter(f => byFam[f].length > 0);
  const H = pad * 2 + activeFams.length * laneH + 40;
  // x domain: combine all majors. KNOB/MASK = -1, OTHER = 500, ET = 999. We'll use a piecewise layout
  const majors = [...new Set(steps.filter(s => s.major >= 0 && s.major < 500).map(s => s.major))].sort((a, b) => a - b);
  const xMin = majors.length ? majors[0] : 0;
  const xMax = majors.length ? majors[majors.length - 1] : 1;
  const stageCount = Math.max(1, xMax - xMin);
  const procW = W - pad * 2 - 180;
  const toX = (m) => {
    if (m === -1) return pad + 40;        // pre-process lane
    if (m === 999) return W - pad - 40;   // post-process lane
    if (m === 500) return W - pad - 80;   // OTHER
    return pad + 100 + ((m - xMin) / stageCount) * procW;
  };

  return (
    <div style={{ background: "var(--bg-card)", borderRadius: 8, border: "1px solid var(--border)", padding: 16, overflow: "auto" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <div style={{ fontSize: 12, fontWeight: 700, fontFamily: "monospace", color: "var(--accent)" }}>
          공정 윈도우 — 타겟: {target} (스텝 {targetStep})
        </div>
        <div style={{ fontSize: 10, color: "var(--text-secondary)" }}>버블 크기 = Σ|중요도|, 점선 = 비인과(다운스트림) 마스킹됨</div>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: "block" }}>
        {/* Target step vertical line */}
        {targetStep >= 0 && targetStep < 500 && (
          <line x1={toX(targetStep)} y1={pad} x2={toX(targetStep)} y2={H - pad - 20} stroke="var(--accent)" strokeWidth={2} strokeDasharray="4,4" opacity={0.5} />
        )}
        {/* Step grid ticks */}
        {majors.map(m => (
          <g key={m}>
            <line x1={toX(m)} y1={pad} x2={toX(m)} y2={H - pad - 20} stroke="var(--border)" opacity={0.25} />
            <text x={toX(m)} y={H - pad - 6} textAnchor="middle" fontSize={9} fill="var(--text-secondary)">FAB_{m}</text>
          </g>
        ))}
        <text x={pad + 40} y={H - pad - 6} textAnchor="middle" fontSize={9} fill="#8b5cf6">pre</text>
        <text x={W - pad - 40} y={H - pad - 6} textAnchor="middle" fontSize={9} fill="#ef4444">post</text>

        {/* Lanes by family */}
        {activeFams.map((fam, li) => {
          const y = pad + li * laneH + 24;
          const color = FAM_COLORS[fam] || "#6b7280";
          return (
            <g key={fam}>
              <text x={pad} y={y + 4} fill={color} fontSize={11} fontWeight={700} fontFamily="monospace">{fam}</text>
              <line x1={pad + 50} y1={y} x2={W - pad} y2={y} stroke={color} strokeWidth={0.5} opacity={0.2} />
              {byFam[fam].map((s, i) => {
                const r = Math.max(4, Math.min(22, Math.sqrt(s.sum_imp / maxSum) * 24));
                const topF = s.top_features?.[0];
                // Non-causal if family is FAB/INLINE/VM and major > targetStep
                const isBlocked = ["FAB", "INLINE", "VM"].includes(fam) && s.major > targetStep && targetStep < 500;
                return (
                  <g key={i}>
                    <circle cx={toX(s.major)} cy={y} r={r}
                      fill={isBlocked ? "transparent" : color} stroke={color} strokeWidth={1.5}
                      strokeDasharray={isBlocked ? "3,2" : undefined} opacity={isBlocked ? 0.35 : 0.75}>
                      <title>{`${fam}_${s.major} — 피처 ${s.count}개, sum_imp=${s.sum_imp.toFixed(3)}${topF ? `\n상위: ${topF.feature} (${topF.importance.toFixed(3)})` : ""}${isBlocked ? "\n⚠ 타겟 다운스트림 (마스킹됨)" : ""}`}</title>
                    </circle>
                    {r > 8 && <text x={toX(s.major)} y={y + 3} textAnchor="middle" fontSize={9} fill="#fff" fontWeight={700}>{s.count}</text>}
                  </g>
                );
              })}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function CausalityMask({ features, blockedCount, keptCount }) {
  if (!features?.length) return null;
  const top = features.slice(0, 12);
  const maxV = Math.max(...top.map(f => f.raw_importance || 0), 0.001);
  return (
    <div style={{ background: "var(--bg-card)", borderRadius: 8, border: "1px solid var(--border)", padding: 16 }}>
      <div style={{ fontSize: 12, fontWeight: 700, fontFamily: "monospace", color: "var(--accent)", marginBottom: 6 }}>
        인과 마스크 — 유지 {keptCount} / 차단 {blockedCount}
      </div>
      <div style={{ fontSize: 10, color: "var(--text-secondary)", marginBottom: 8 }}>원본 vs 가중치 적용 (거리에 따른 지수 감쇠)</div>
      {top.map((f, i) => (
        <div key={i} style={{ marginBottom: 5 }}>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, marginBottom: 2, fontFamily: "monospace" }}>
            <span style={{ color: FAM_COLORS[f.family] || "#6b7280", fontWeight: 600 }}>
              [{f.family}{f.major >= 0 && f.major < 500 ? "_" + f.major : ""}] {f.feature}
              {!f.causal_valid && <span style={{ color: "#ef4444", marginLeft: 6 }}>⊘ 다운스트림</span>}
            </span>
            <span style={{ color: "var(--text-secondary)" }}>w={f.weight} · d={f.distance ?? "-"}</span>
          </div>
          <div style={{ position: "relative", height: 8, background: "var(--bg-hover)", borderRadius: 2, overflow: "hidden" }}>
            <div style={{ position: "absolute", left: 0, top: 0, height: "100%", width: (f.raw_importance / maxV * 100) + "%", background: "var(--text-secondary)", opacity: 0.25 }} />
            <div style={{ position: "absolute", left: 0, top: 0, height: "100%", width: (f.importance / maxV * 100) + "%", background: f.causal_valid ? (f.direction === "+" ? "#10b981" : "#ef4444") : "rgba(239,68,68,0.2)" }} />
          </div>
        </div>
      ))}
    </div>
  );
}

function KnobSplitPanel({ splits, target }) {
  const keys = Object.keys(splits || {});
  if (!keys.length) return null;
  // Global target range across all splits (for consistent bar scaling)
  const allMeans = [];
  keys.forEach(k => splits[k].forEach(r => { if (r.target_mean != null) allMeans.push(r.target_mean); }));
  const hasTarget = allMeans.length > 0;
  const gMin = hasTarget ? Math.min(...allMeans) : 0;
  const gMax = hasTarget ? Math.max(...allMeans) : 1;
  const gRange = gMax - gMin || 1;
  const mark = (v) => ((v - gMin) / gRange * 100);
  return (
    <div style={{ background: "var(--bg-card)", borderRadius: 8, border: "1px solid var(--border)", padding: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 8 }}>
        <div style={{ fontSize: 12, fontWeight: 700, fontFamily: "monospace", color: "#8b5cf6" }}>KNOB 분할 ({keys.length})</div>
        {hasTarget && <div style={{ fontSize: 9, color: "var(--text-secondary)", fontFamily: "monospace" }}>μ 범위: [{gMin.toFixed(3)}, {gMax.toFixed(3)}] / {target}</div>}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))", gap: 8 }}>
        {keys.map(k => {
          const rows = splits[k]; const total = rows.reduce((a, r) => a + r.count, 0) || 1;
          // Effect size = max(mean) - min(mean) across splits
          const means = rows.filter(r => r.target_mean != null).map(r => r.target_mean);
          const effect = means.length >= 2 ? (Math.max(...means) - Math.min(...means)) : null;
          return (
            <div key={k} style={{ padding: 8, background: "var(--bg-primary)", borderRadius: 4, border: "1px solid var(--border)" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                <span style={{ fontSize: 10, fontWeight: 700, color: "#8b5cf6", fontFamily: "monospace" }}>{k}</span>
                {effect != null && (
                  <span style={{ fontSize: 9, color: effect > gRange * 0.3 ? "#ef4444" : "var(--text-secondary)", fontFamily: "monospace", fontWeight: 600 }}>
                    Δ={effect.toFixed(3)}{effect > gRange * 0.3 ? " ⚠" : ""}
                  </span>
                )}
              </div>
              {rows.map((r, i) => (
                <div key={i} style={{ marginBottom: 3 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: 9, fontFamily: "monospace" }}>
                    <span style={{ color: "var(--text-primary)" }}>{r.value}</span>
                    <span style={{ color: "var(--text-secondary)" }}>
                      n={r.count}
                      {r.target_mean != null && <span style={{ color: "#10b981", marginLeft: 4 }}>μ={r.target_mean.toFixed(3)}</span>}
                      {r.target_std != null && r.target_std > 0 && <span style={{ color: "var(--text-secondary)", marginLeft: 2 }}>±{r.target_std.toFixed(3)}</span>}
                    </span>
                  </div>
                  {r.target_mean != null && (
                    <div style={{ position: "relative", height: 5, background: "var(--bg-hover)", borderRadius: 1, marginTop: 2 }}>
                      <div style={{ position: "absolute", left: Math.max(0, mark(r.target_mean - (r.target_std || 0))) + "%", width: Math.max(1, mark(r.target_mean + (r.target_std || 0)) - mark(r.target_mean - (r.target_std || 0))) + "%", height: "100%", background: "rgba(139,92,246,0.2)", borderRadius: 1 }} />
                      <div style={{ position: "absolute", left: mark(r.target_mean) + "%", width: 2, height: "100%", background: "#8b5cf6" }} />
                    </div>
                  )}
                  {r.target_mean == null && (
                    <div style={{ height: 3, background: "linear-gradient(to right, var(--bg-hover), transparent)", width: ((r.count / total) * 100) + "%", marginTop: 2, borderRadius: 1 }} />
                  )}
                </div>
              ))}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ─── v7.1: Level-hierarchy panel ─── */
function LevelHierarchyPanel({ perLevel, targetLabel }) {
  if (!perLevel) return null;
  const levels = ["L0", "L1", "L2", "L3"];
  const maxSum = Math.max(...Object.values(perLevel).map(v => v.sum_imp || 0), 0.001);
  return (
    <div style={{ background: "var(--bg-card)", borderRadius: 8, border: "1px solid var(--border)", padding: 16 }}>
      <div style={{ fontSize: 12, fontWeight: 700, fontFamily: "monospace", color: "var(--accent)", marginBottom: 8 }}>
        인과 계층 — 타겟: {targetLabel}
      </div>
      <div style={{ display: "flex", gap: 4, alignItems: "stretch", marginBottom: 8 }}>
        {levels.map((lvl, i) => {
          const v = perLevel[lvl]; const color = LEVEL_COLORS[lvl];
          const hasData = v && v.count > 0;
          return (
            <div key={lvl} style={{ flex: 1, padding: "10px 8px", borderRadius: 6, background: hasData ? color + "18" : "var(--bg-hover)", border: "1px solid " + (hasData ? color : "var(--border)"), opacity: hasData ? 1 : 0.4 }}>
              <div style={{ fontSize: 10, fontWeight: 700, color, fontFamily: "monospace" }}>{lvl}</div>
              <div style={{ fontSize: 9, color: "var(--text-secondary)", marginBottom: 4 }}>{LEVEL_DESC[lvl].split("—")[1]?.trim()}</div>
              {hasData && (<>
                <div style={{ fontSize: 14, fontWeight: 700, fontFamily: "monospace", color }}>{v.sum_imp.toFixed(3)}</div>
                <div style={{ fontSize: 9, color: "var(--text-secondary)" }}>피처 {v.kept}/{v.count}</div>
                <div style={{ height: 4, background: "var(--bg-hover)", borderRadius: 2, marginTop: 4, overflow: "hidden" }}>
                  <div style={{ height: "100%", width: (v.sum_imp / maxSum * 100) + "%", background: color }} />
                </div>
              </>)}
              {!hasData && <div style={{ fontSize: 9, color: "var(--text-secondary)", fontStyle: "italic" }}>피처 없음</div>}
              {i < 3 && <div style={{ textAlign: "right", fontSize: 14, color: "var(--text-secondary)", marginTop: 2 }}>→</div>}
            </div>
          );
        })}
      </div>
      <div style={{ fontSize: 9, color: "var(--text-secondary)", fontStyle: "italic" }}>
        L0→L1→L2→L3 엄격한 인과. 레벨 K 의 피처는 레벨 ≥ K 의 신호에만 영향.
      </div>
    </div>
  );
}

function ParsimonyPanel({ pars }) {
  if (!pars) return null;
  return (
    <div style={{ background: "rgba(16,185,129,0.06)", border: "1px solid rgba(16,185,129,0.3)", borderRadius: 8, padding: 12 }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: "#10b981", fontFamily: "monospace", marginBottom: 6 }}>🎯 간결성 점수</div>
      <div style={{ display: "flex", gap: 16, fontFamily: "monospace", fontSize: 11 }}>
        <span>k<sub>80%</sub> = <b style={{ color: "#10b981", fontSize: 14 }}>{pars.k_for_80pct}</b> 피처</span>
        <span style={{ color: "var(--text-secondary)" }}>상위 5 커버리지: <b style={{ color: "var(--text-primary)" }}>{(pars.top5_coverage * 100).toFixed(1)}%</b></span>
        <span style={{ color: "var(--text-secondary)" }}>상위 10: <b style={{ color: "var(--text-primary)" }}>{(pars.top10_coverage * 100).toFixed(1)}%</b></span>
        <span style={{ color: "var(--text-secondary)" }}>전체: {pars.total_features}</span>
      </div>
      <div style={{ fontSize: 9, color: "var(--text-secondary)", marginTop: 4 }}>
        ↑ k 가 작을수록 설명이 간결. 실행 가능한 인사이트를 위해 작은 k 모델 선호.
      </div>
    </div>
  );
}

/* ─── v7.1: Transfer (PRODA → PRODB) visualization ─── */
function TransferPanel({ result }) {
  if (!result) return null;
  const rs = result.rank_shift || [];
  const ds = result.distribution_shift || [];
  const maxRank = Math.max(1, ...rs.map(r => Math.max(r.rank_src, r.rank_tgt)));
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ background: "var(--bg-card)", borderRadius: 8, border: "1px solid var(--border)", padding: 16 }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 10 }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: "var(--accent)", fontFamily: "monospace" }}>
            전이: {result.source_label} → {result.target_label}
          </div>
          <div style={{ fontSize: 10, color: "var(--text-secondary)" }}>n<sub>src</sub>={result.src_rows} · n<sub>tgt</sub>={result.tgt_rows}</div>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 }}>
          <div style={{ padding: 10, background: "rgba(16,185,129,0.1)", borderRadius: 6, border: "1px solid rgba(16,185,129,0.4)" }}>
            <div style={{ fontSize: 10, fontWeight: 700, color: "#10b981" }}>✓ 불변 ({result.invariant_features?.length || 0})</div>
            <div style={{ fontSize: 9, color: "var(--text-secondary)", marginBottom: 4 }}>제품 간 안정</div>
            {(result.invariant_features || []).slice(0, 5).map(f => <div key={f} style={{ fontSize: 10, fontFamily: "monospace", color: "var(--text-primary)" }}>{f}</div>)}
          </div>
          <div style={{ padding: 10, background: "rgba(59,130,246,0.1)", borderRadius: 6, border: "1px solid rgba(59,130,246,0.4)" }}>
            <div style={{ fontSize: 10, fontWeight: 700, color: "#3b82f6" }}>★ 타겟에서 신규 ({result.novel_features?.length || 0})</div>
            <div style={{ fontSize: 9, color: "var(--text-secondary)", marginBottom: 4 }}>PRODB 에서만 새로 등장</div>
            {(result.novel_features || []).slice(0, 5).map(f => <div key={f} style={{ fontSize: 10, fontFamily: "monospace", color: "var(--text-primary)" }}>{f}</div>)}
          </div>
          <div style={{ padding: 10, background: "rgba(239,68,68,0.1)", borderRadius: 6, border: "1px solid rgba(239,68,68,0.4)" }}>
            <div style={{ fontSize: 10, fontWeight: 700, color: "#ef4444" }}>⊘ 소멸 ({result.vanishing_features?.length || 0})</div>
            <div style={{ fontSize: 9, color: "var(--text-secondary)", marginBottom: 4 }}>PRODA 에서만 중요</div>
            {(result.vanishing_features || []).slice(0, 5).map(f => <div key={f} style={{ fontSize: 10, fontFamily: "monospace", color: "var(--text-primary)" }}>{f}</div>)}
          </div>
        </div>
        <div style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 8, lineHeight: 1.5, padding: "8px 10px", background: "var(--bg-primary)", borderRadius: 4 }}>{result.note}</div>
      </div>
      {/* Rank shift table */}
      <div style={{ background: "var(--bg-card)", borderRadius: 8, border: "1px solid var(--border)", padding: 16 }}>
        <div style={{ fontSize: 12, fontWeight: 700, fontFamily: "monospace", color: "var(--accent)", marginBottom: 8 }}>랭크 변화 (상위 15)</div>
        <table style={{ width: "100%", fontSize: 10, borderCollapse: "collapse", fontFamily: "monospace" }}>
          <thead><tr style={{ borderBottom: "1px solid var(--border)", color: "var(--text-secondary)" }}>
            <th style={{ textAlign: "left", padding: "4px 6px" }}>피처</th>
            <th style={{ textAlign: "right", padding: "4px 6px" }}>rank_src</th>
            <th style={{ textAlign: "right", padding: "4px 6px" }}>rank_tgt</th>
            <th style={{ textAlign: "right", padding: "4px 6px" }}>Δrank</th>
            <th style={{ textAlign: "right", padding: "4px 6px" }}>imp_src</th>
            <th style={{ textAlign: "right", padding: "4px 6px" }}>imp_tgt</th>
          </tr></thead>
          <tbody>
            {rs.slice(0, 15).map((r, i) => {
              const d = r.delta_rank;
              const col = Math.abs(d) <= 3 ? "#10b981" : Math.abs(d) <= 10 ? "#f59e0b" : "#ef4444";
              return (<tr key={i} style={{ borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
                <td style={{ padding: "3px 6px", color: "var(--text-primary)" }}>{r.feature}</td>
                <td style={{ padding: "3px 6px", textAlign: "right" }}>{r.rank_src}</td>
                <td style={{ padding: "3px 6px", textAlign: "right" }}>{r.rank_tgt}</td>
                <td style={{ padding: "3px 6px", textAlign: "right", color: col, fontWeight: 600 }}>{d > 0 ? "+" + d : d}</td>
                <td style={{ padding: "3px 6px", textAlign: "right", color: "var(--text-secondary)" }}>{r.imp_src.toFixed(3)}</td>
                <td style={{ padding: "3px 6px", textAlign: "right", color: "var(--text-secondary)" }}>{r.imp_tgt.toFixed(3)}</td>
              </tr>);
            })}
          </tbody>
        </table>
      </div>
      {/* Distribution shift */}
      {ds.length > 0 && <div style={{ background: "var(--bg-card)", borderRadius: 8, border: "1px solid var(--border)", padding: 16 }}>
        <div style={{ fontSize: 12, fontWeight: 700, fontFamily: "monospace", color: "var(--accent)", marginBottom: 8 }}>분포 시프트 (|z| 상위 10)</div>
        {ds.slice(0, 10).map((r, i) => {
          const sev = r.z_shift > 2 ? "#ef4444" : r.z_shift > 1 ? "#f59e0b" : "#10b981";
          return (<div key={i} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "4px 0", borderBottom: "1px solid rgba(255,255,255,0.05)", fontSize: 10, fontFamily: "monospace" }}>
            <span style={{ color: "var(--text-primary)" }}>{r.feature}</span>
            <span style={{ color: "var(--text-secondary)" }}>
              μ<sub>src</sub>={r.src_mean.toFixed(3)} → μ<sub>tgt</sub>={r.tgt_mean.toFixed(3)}
              <span style={{ color: sev, fontWeight: 700, marginLeft: 8 }}>|z|={r.z_shift}</span>
            </span>
          </div>);
        })}
      </div>}
    </div>
  );
}

/* ─── v7.1: Pareto (Performance vs Yield) visualization ─── */
function ParetoPanel({ result }) {
  if (!result || !result.points?.length) return null;
  const pts = result.points;
  const perfs = pts.map(p => p.perf_mean); const ylds = pts.map(p => p.yield_mean);
  const pMin = Math.min(...perfs), pMax = Math.max(...perfs);
  const yMin = Math.min(...ylds), yMax = Math.max(...ylds);
  const pR = (pMax - pMin) || 1; const yR = (yMax - yMin) || 1;
  const W = 540, H = 340, pad = { t: 16, r: 16, b: 44, l: 60 };
  const cw = W - pad.l - pad.r, ch = H - pad.t - pad.b;
  const toX = (v) => pad.l + (v - pMin) / pR * cw;
  const toY = (v) => pad.t + ch - (v - yMin) / yR * ch;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ background: "var(--bg-card)", borderRadius: 8, border: "1px solid var(--border)", padding: 16 }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: "var(--accent)", fontFamily: "monospace" }}>
            성능 × 수율 파레토
          </div>
          <div style={{ fontSize: 10, color: "var(--text-secondary)", fontFamily: "monospace" }}>
            그룹 기준 {(result.group_cols || []).join(" × ") || "(전체)"}
          </div>
        </div>
        <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: "block" }}>
          {/* Grid */}
          {[0, 0.25, 0.5, 0.75, 1].map((f, i) => (<g key={i}>
            <line x1={pad.l} y1={pad.t + ch * (1 - f)} x2={W - pad.r} y2={pad.t + ch * (1 - f)} stroke="var(--border)" opacity={0.3} />
            <line x1={pad.l + cw * f} y1={pad.t} x2={pad.l + cw * f} y2={pad.t + ch} stroke="var(--border)" opacity={0.3} />
            <text x={pad.l - 6} y={pad.t + ch * (1 - f) + 3} textAnchor="end" fontSize={9} fill="var(--text-secondary)">{(yMin + yR * f).toFixed(2)}</text>
            <text x={pad.l + cw * f} y={H - pad.b + 14} textAnchor="middle" fontSize={9} fill="var(--text-secondary)">{(pMin + pR * f).toFixed(2)}</text>
          </g>))}
          {/* Frontier line (sorted by performance) */}
          {result.frontier?.length > 1 && (() => {
            const fr = [...result.frontier].sort((a, b) => a.perf_mean - b.perf_mean);
            const pts = fr.map(p => `${toX(p.perf_mean)},${toY(p.yield_mean)}`).join(" ");
            return <polyline points={pts} fill="none" stroke="#10b981" strokeWidth={2} strokeDasharray="4,3" opacity={0.7} />;
          })()}
          {/* Points with error bars */}
          {pts.map((p, i) => {
            const cx = toX(p.perf_mean); const cy = toY(p.yield_mean);
            const pErrLeft = toX(p.perf_mean - p.perf_std); const pErrRight = toX(p.perf_mean + p.perf_std);
            const yErrBot = toY(p.yield_mean - p.yield_std); const yErrTop = toY(p.yield_mean + p.yield_std);
            const col = p.is_pareto ? "#10b981" : "#6b7280";
            return (<g key={i}>
              <line x1={pErrLeft} y1={cy} x2={pErrRight} y2={cy} stroke={col} strokeWidth={1} opacity={0.4} />
              <line x1={cx} y1={yErrBot} x2={cx} y2={yErrTop} stroke={col} strokeWidth={1} opacity={0.4} />
              <circle cx={cx} cy={cy} r={p.is_pareto ? 8 : 5} fill={col} opacity={0.85} stroke={p.is_pareto ? "#fff" : "none"} strokeWidth={1.5}>
                <title>{`${p.group}\n성능=${p.perf_mean.toFixed(3)}±${p.perf_std.toFixed(3)}\n수율=${p.yield_mean.toFixed(3)}±${p.yield_std.toFixed(3)}\nn=${p.n}${p.is_pareto ? "\n★ 파레토 최적" : ""}`}</title>
              </circle>
            </g>);
          })}
          {/* Axis labels */}
          <text x={pad.l + cw / 2} y={H - 6} textAnchor="middle" fontSize={11} fontWeight={700} fill="var(--accent)" fontFamily="monospace">{result.performance_col} (성능 →)</text>
          <text x={14} y={pad.t + ch / 2} transform={`rotate(-90,14,${pad.t + ch / 2})`} textAnchor="middle" fontSize={11} fontWeight={700} fill="var(--accent)" fontFamily="monospace">{result.yield_col} (수율 →)</text>
        </svg>
        <div style={{ fontSize: 10, color: "var(--text-secondary)", marginTop: 6 }}>
          녹색 원 = 파레토 최적 (두 축 모두에서 지배되지 않음). 오차 막대 = ±1σ.
        </div>
      </div>
      {/* Frontier table */}
      <div style={{ background: "var(--bg-card)", borderRadius: 8, border: "1px solid var(--border)", padding: 16 }}>
        <div style={{ fontSize: 12, fontWeight: 700, fontFamily: "monospace", color: "#10b981", marginBottom: 8 }}>★ 프론티어 ({result.frontier?.length || 0})</div>
        {(result.frontier || []).map((p, i) => (
          <div key={i} style={{ padding: "6px 10px", background: "var(--bg-primary)", borderRadius: 4, marginBottom: 4, fontFamily: "monospace", fontSize: 11, display: "flex", justifyContent: "space-between", alignItems: "center", border: p === result.best ? "1px solid #10b981" : "none" }}>
            <span style={{ color: p === result.best ? "#10b981" : "var(--text-primary)", fontWeight: p === result.best ? 700 : 400 }}>
              {p === result.best ? "⭐ " : ""}{p.group}
            </span>
            <span style={{ color: "var(--text-secondary)" }}>
              성능=<b style={{ color: "var(--text-primary)" }}>{p.perf_mean.toFixed(3)}</b>  ·
              수율=<b style={{ color: "var(--text-primary)" }}>{p.yield_mean.toFixed(3)}</b>  ·
              n={p.n}
            </span>
          </div>
        ))}
      </div>
      {result.recommendation && (
        <div style={{ background: "rgba(16,185,129,0.08)", border: "1px solid rgba(16,185,129,0.3)", borderRadius: 8, padding: 12 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: "#10b981", marginBottom: 4, fontFamily: "monospace" }}>💡 추천</div>
          <div style={{ fontSize: 11, color: "var(--text-primary)", lineHeight: 1.6 }}>{result.recommendation}</div>
        </div>
      )}
    </div>
  );
}

/* ─── v7.4: Model Flow diagram ─── */
function ModelFlowDiagram({ result }) {
  if (!result) return null;
  const nodes = result.nodes || [];
  const edges = result.edges || [];
  // Layout: left = L0 groups stacked vertically, center = INLINE accumulator (if any), right = target
  const W = 820, H = Math.max(300, 70 + (nodes.filter(n => n.kind === "group").length) * 46);
  const groups = nodes.filter(n => n.kind === "group");
  const acc = nodes.find(n => n.kind === "accumulator");
  const tgt = nodes.find(n => n.kind === "target");
  const leftX = 40, leftW = 220;
  const midX = 380, midW = 160;
  const rightX = 640, rightW = 140;
  const rowH = 40;
  const groupY = (i) => 50 + i * rowH;
  const nodeCenter = (nid) => {
    if (nid === "TARGET") return { x: rightX + rightW / 2, y: H / 2 };
    if (nid === "INLINE_ACCUMULATOR") return { x: midX + midW / 2, y: H / 2 };
    const i = groups.findIndex(g => `${g.family}_${g.major}` === nid);
    if (i < 0) return { x: leftX + leftW / 2, y: 50 };
    return { x: leftX + leftW, y: groupY(i) + 16 };
  };
  const maxW = Math.max(0.01, ...edges.map(e => e.weight || 0));
  return (
    <div style={{ background: "var(--bg-card)", borderRadius: 8, border: "1px solid var(--border)", padding: 16, overflow: "auto" }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: "var(--accent)", fontFamily: "monospace" }}>🧩 모델 흐름 — {result.target} ({result.target_level_label})</div>
        <div style={{ fontSize: 10, color: "var(--text-secondary)", fontFamily: "monospace" }}>
          룩백: FAB 스텝 {result.lookback?.range_low ?? "-"}-{result.lookback?.range_high ?? "-"} (±{result.lookback?.margin})
        </div>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: "block" }}>
        {/* Lookback uncertainty band behind left column */}
        {result.lookback?.range_low != null && (
          <rect x={leftX - 8} y={40} width={leftW + 16} height={H - 70} rx={8} fill="rgba(245,158,11,0.05)" stroke="rgba(245,158,11,0.25)" strokeDasharray="5,3" />
        )}
        <text x={leftX + leftW / 2} y={30} textAnchor="middle" fill="#f59e0b" fontSize={10} fontFamily="monospace">업스트림 (L0) — 룩백 불확실</text>
        {/* L0 group nodes */}
        {groups.map((g, i) => {
          const y = groupY(i); const col = FAM_COLORS[g.family] || "#6b7280";
          const w = Math.max(4, (g.sum_imp || 0) / (Math.max(...groups.map(x => x.sum_imp || 0)) || 1) * 100);
          return (
            <g key={g.id}>
              <rect x={leftX} y={y} width={leftW} height={32} rx={5} fill={col + "18"} stroke={col} strokeWidth={1.2} />
              <text x={leftX + 8} y={y + 14} fill={col} fontSize={10} fontWeight={700} fontFamily="monospace">{g.label} · L{g.level}</text>
              <text x={leftX + 8} y={y + 26} fill="var(--text-secondary)" fontSize={9} fontFamily="monospace">{g.count}피처 · Σimp={g.sum_imp?.toFixed(3)}</text>
              {/* importance bar */}
              <rect x={leftX + leftW - 12} y={y + 10} width={10} height={(w / 100) * 16 + 1} fill={col} opacity={0.7} />
            </g>
          );
        })}
        {/* INLINE accumulator */}
        {acc && (
          <g>
            <rect x={midX} y={H / 2 - 40} width={midW} height={80} rx={8} fill="rgba(59,130,246,0.1)" stroke="#3b82f6" strokeWidth={1.6} strokeDasharray="4,2" />
            <text x={midX + midW / 2} y={H / 2 - 18} textAnchor="middle" fill="#3b82f6" fontSize={11} fontWeight={700} fontFamily="monospace">INLINE (L1)</text>
            <text x={midX + midW / 2} y={H / 2 - 4} textAnchor="middle" fill="#3b82f6" fontSize={10} fontFamily="monospace">누적기</text>
            <text x={midX + midW / 2} y={H / 2 + 10} textAnchor="middle" fill="var(--text-secondary)" fontSize={9}>잠재 상태</text>
            <text x={midX + midW / 2} y={H / 2 + 26} textAnchor="middle" fill="var(--text-secondary)" fontSize={9} fontFamily="monospace">Σimp={acc.sum_imp?.toFixed(3)}</text>
          </g>
        )}
        {/* Target */}
        {tgt && (
          <g>
            <rect x={rightX} y={H / 2 - 30} width={rightW} height={60} rx={8} fill="var(--accent-glow)" stroke="var(--accent)" strokeWidth={2} />
            <text x={rightX + rightW / 2} y={H / 2 - 10} textAnchor="middle" fill="var(--accent)" fontSize={12} fontWeight={700} fontFamily="monospace">{tgt.label}</text>
            <text x={rightX + rightW / 2} y={H / 2 + 4} textAnchor="middle" fill="var(--text-secondary)" fontSize={10}>{tgt.level_label}</text>
            <text x={rightX + rightW / 2} y={H / 2 + 20} textAnchor="middle" fill="var(--text-secondary)" fontSize={9}>🎯 타겟</text>
          </g>
        )}
        {/* Edges */}
        {edges.map((e, i) => {
          const a = nodeCenter(e.from); const b = nodeCenter(e.to);
          const thick = Math.max(0.5, (e.weight || 0) / maxW * 4);
          const col = !e.causal_valid ? "#ef4444" : e.kind === "via_inline" ? "#3b82f6" : e.kind === "accumulator_out" ? "#3b82f6" : e.kind === "weak_reverse" ? "#f59e0b" : "var(--accent)";
          const dash = (e.causal_valid === false || e.kind === "weak_reverse") ? "4,3" : null;
          const mx = (a.x + b.x) / 2; const my = (a.y + b.y) / 2 - Math.abs(a.y - b.y) * 0.1;
          return (
            <g key={i}>
              <path d={`M ${a.x},${a.y} Q ${mx},${my} ${b.x},${b.y}`} stroke={col} strokeWidth={thick} fill="none" strokeDasharray={dash || undefined} opacity={0.65} />
            </g>
          );
        })}
      </svg>
      <div style={{ marginTop: 10, padding: "8px 10px", background: "rgba(59,130,246,0.06)", borderRadius: 6, border: "1px solid rgba(59,130,246,0.25)" }}>
        <div style={{ fontSize: 11, color: "#3b82f6", fontWeight: 700, fontFamily: "monospace" }}>🤖 추천</div>
        <div style={{ fontSize: 11, color: "var(--text-primary)", lineHeight: 1.6 }}>{result.recommendation}</div>
      </div>
      {result.lookback?.note && (
        <div style={{ fontSize: 10, color: "#f59e0b", marginTop: 6, fontStyle: "italic" }}>ℹ {result.lookback.note}</div>
      )}
    </div>
  );
}

/* ─── v7.4: Wafer-map panel ─── */
function WFMapPanel({ result }) {
  if (!result || !result.ensemble?.length) {
    return <div style={{ padding: 20, color: "var(--text-secondary)", fontSize: 12 }}>{result?.note || "Shot 레벨 데이터가 없습니다."}</div>;
  }
  const pts = result.ensemble;
  const xs = pts.map(p => p.x); const ys = pts.map(p => p.y);
  const xMin = Math.min(...xs), xMax = Math.max(...xs);
  const yMin = Math.min(...ys), yMax = Math.max(...ys);
  const means = pts.map(p => p.mean);
  const vMin = Math.min(...means), vMax = Math.max(...means); const vR = (vMax - vMin) || 1;
  const colorize = (v) => {
    const t = (v - vMin) / vR;
    const r = Math.round(60 + t * 200); const b = Math.round(255 - t * 220);
    return `rgb(${r},80,${b})`;
  };
  const renderMap = (shots, W = 180, H = 180) => {
    const dx = (xMax - xMin) || 1; const dy = (yMax - yMin) || 1;
    const cell = Math.min((W - 20) / (dx + 1), (H - 20) / (dy + 1));
    return (
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H}>
        {/* wafer circle */}
        <circle cx={W / 2} cy={H / 2} r={Math.min(W, H) / 2 - 6} fill="none" stroke="var(--border)" strokeWidth={1} />
        {shots.map((p, i) => {
          const cx = W / 2 + (p.x - (xMin + xMax) / 2) * cell;
          const cy = H / 2 + (p.y - (yMin + yMax) / 2) * cell;
          return <rect key={i} x={cx - cell / 2 + 1} y={cy - cell / 2 + 1} width={cell - 2} height={cell - 2} rx={1} fill={colorize(p.value ?? p.mean)} opacity={0.9} />;
        })}
      </svg>
    );
  };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ background: "var(--bg-card)", borderRadius: 8, border: "1px solid var(--border)", padding: 16 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: "var(--accent)", fontFamily: "monospace" }}>🗺 앙상블 WF Map — {result.value_col}</div>
          <div style={{ fontSize: 11, fontFamily: "monospace" }}>
            일관성=<b style={{ color: result.consistency > 0.85 ? "#10b981" : result.consistency > 0.6 ? "#f59e0b" : "#ef4444" }}>{(result.consistency * 100).toFixed(1)}%</b> ·
            패턴=<b style={{ color: "var(--accent)" }}>{result.pattern.label}</b>
          </div>
        </div>
        <div style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
          <div>{renderMap(pts.map(p => ({ ...p, value: p.mean })), 220, 220)}</div>
          <div style={{ fontSize: 10, fontFamily: "monospace", lineHeight: 1.7 }}>
            <div>center μ = {result.pattern.center_mean}</div>
            <div>edge μ   = {result.pattern.edge_mean}</div>
            <div>c − e    = {result.pattern.center_minus_edge}</div>
            <div>radial   = {result.pattern.radial_slope}</div>
            <div>tilt_x   = {result.pattern.tilt_x}</div>
            <div>tilt_y   = {result.pattern.tilt_y}</div>
            <div style={{ marginTop: 8, padding: "4px 8px", background: "var(--accent-glow)", borderRadius: 3 }}>
              <b style={{ color: "var(--accent)" }}>{result.pattern.label}</b>
            </div>
          </div>
        </div>
        <div style={{ fontSize: 10, color: "var(--text-secondary)", marginTop: 8, fontStyle: "italic" }}>{result.note}</div>
      </div>
      {result.wafers?.length > 0 && (
        <div style={{ background: "var(--bg-card)", borderRadius: 8, border: "1px solid var(--border)", padding: 16 }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: "var(--accent)", fontFamily: "monospace", marginBottom: 8 }}>웨이퍼별 맵 (앙상블 편차 순, 위쪽일수록 전형적)</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))", gap: 8 }}>
            {result.wafers.map(w => (
              <div key={w.wafer} style={{ padding: 6, background: "var(--bg-primary)", borderRadius: 4, border: "1px solid var(--border)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, fontFamily: "monospace", marginBottom: 4 }}>
                  <span style={{ color: "var(--accent)", fontWeight: 700 }}>{w.wafer}</span>
                  <span style={{ color: w.deviation_score < 0.1 ? "#10b981" : w.deviation_score < 0.3 ? "#f59e0b" : "#ef4444" }}>dev={w.deviation_score}</span>
                </div>
                {renderMap(w.shots, 140, 140)}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/* ─── v7.4: INLINE correlation search panel ─── */
function InlineCorrPanel({ result }) {
  if (!result) return null;
  const top = result.top || [];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ background: "var(--bg-card)", borderRadius: 8, border: "1px solid var(--border)", padding: 16 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: "var(--accent)", fontFamily: "monospace" }}>🔎 INLINE ↔ {result.target}</div>
          <div style={{ fontSize: 10, color: "var(--text-secondary)", fontFamily: "monospace" }}>검사: 단일 {result.singles_tested}개 + 페어 {result.pairs_tested}개</div>
        </div>
        <div style={{ fontSize: 10, color: "var(--text-secondary)", marginBottom: 6 }}>
          타겟 WF 패턴: <b style={{ color: "var(--accent)" }}>{result.target_pattern}</b>. ★ = 패턴이 타겟과 일치 (+0.15 보너스)
        </div>
        <table style={{ width: "100%", fontSize: 10, borderCollapse: "collapse", fontFamily: "monospace" }}>
          <thead><tr style={{ color: "var(--text-secondary)", borderBottom: "1px solid var(--border)" }}>
            <th style={{ textAlign: "left", padding: "4px 6px" }}>표현식</th>
            <th style={{ textAlign: "center", padding: "4px 6px" }}>종류</th>
            <th style={{ textAlign: "right", padding: "4px 6px" }}>상관</th>
            <th style={{ textAlign: "center", padding: "4px 6px" }}>피처 패턴</th>
            <th style={{ textAlign: "right", padding: "4px 6px" }}>점수</th>
          </tr></thead>
          <tbody>
            {top.map((r, i) => (
              <tr key={i} style={{ borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
                <td style={{ padding: "3px 6px", color: "var(--text-primary)" }}>{r.expr}</td>
                <td style={{ padding: "3px 6px", textAlign: "center", color: r.kind === "pair" ? "#f59e0b" : "#10b981" }}>{r.kind}</td>
                <td style={{ padding: "3px 6px", textAlign: "right", color: Math.abs(r.corr) > 0.5 ? "#10b981" : Math.abs(r.corr) > 0.3 ? "#f59e0b" : "var(--text-secondary)", fontWeight: 600 }}>{r.corr > 0 ? "+" : ""}{r.corr.toFixed(3)}</td>
                <td style={{ padding: "3px 6px", textAlign: "center" }}>{r.map_match ? <span style={{ color: "#10b981" }}>★ {r.feat_pattern}</span> : <span style={{ color: "var(--text-secondary)" }}>{r.feat_pattern}</span>}</td>
                <td style={{ padding: "3px 6px", textAlign: "right", fontWeight: 700, color: "var(--accent)" }}>{r.score}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{ fontSize: 10, color: "var(--text-secondary)", fontStyle: "italic", padding: "6px 10px", background: "rgba(59,130,246,0.06)", borderRadius: 4 }}>{result.note}</div>
    </div>
  );
}

/* ─── v7.4: PPID stratify panel ─── */
function PpidStratifyPanel({ result }) {
  if (!result) return null;
  const gs = result.groups || [];
  const rs = result.rank_stability || [];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ background: "var(--bg-card)", borderRadius: 8, border: "1px solid var(--border)", padding: 16 }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: "var(--accent)", fontFamily: "monospace", marginBottom: 8 }}>🎛 {result.stratifier}별 중요도</div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 8 }}>
          {gs.map(g => (
            <div key={g.group} style={{ padding: 8, background: "var(--bg-primary)", borderRadius: 4, border: "1px solid var(--border)" }}>
              <div style={{ fontSize: 10, fontWeight: 700, color: "#8b5cf6", fontFamily: "monospace", marginBottom: 4 }}>{g.group} <span style={{ color: "var(--text-secondary)", fontWeight: 400 }}>n={g.n}</span></div>
              {g.top_features.map((f, i) => (
                <div key={i} style={{ display: "flex", justifyContent: "space-between", fontSize: 9, fontFamily: "monospace", marginBottom: 2 }}>
                  <span style={{ color: "var(--text-primary)" }}>{f.direction === "+" ? "↑" : "↓"} {f.feature}</span>
                  <span style={{ color: "var(--accent)" }}>{f.importance.toFixed(3)}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
      <div style={{ background: "var(--bg-card)", borderRadius: 8, border: "1px solid var(--border)", padding: 16 }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: "var(--accent)", fontFamily: "monospace", marginBottom: 8 }}>그룹 간 랭크 안정성 (안정 {result.stable_count} / 불안정 {result.unstable_count})</div>
        <table style={{ width: "100%", fontSize: 10, borderCollapse: "collapse", fontFamily: "monospace" }}>
          <thead><tr style={{ color: "var(--text-secondary)", borderBottom: "1px solid var(--border)" }}>
            <th style={{ textAlign: "left", padding: "3px 6px" }}>피처</th>
            <th style={{ textAlign: "center", padding: "3px 6px" }}>그룹별 랭크</th>
            <th style={{ textAlign: "right", padding: "3px 6px" }}>평균 랭크</th>
            <th style={{ textAlign: "right", padding: "3px 6px" }}>범위</th>
            <th style={{ textAlign: "center", padding: "3px 6px" }}>안정?</th>
          </tr></thead>
          <tbody>
            {rs.map((r, i) => (
              <tr key={i} style={{ borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
                <td style={{ padding: "3px 6px", color: "var(--text-primary)" }}>{r.feature}</td>
                <td style={{ padding: "3px 6px", textAlign: "center", color: "var(--text-secondary)" }}>{r.ranks.join(", ")}</td>
                <td style={{ padding: "3px 6px", textAlign: "right" }}>{r.mean_rank}</td>
                <td style={{ padding: "3px 6px", textAlign: "right", color: r.span <= 3 ? "#10b981" : "#ef4444", fontWeight: 600 }}>{r.span}</td>
                <td style={{ padding: "3px 6px", textAlign: "center" }}>{r.stable ? <span style={{ color: "#10b981" }}>✓</span> : <span style={{ color: "#ef4444" }}>✗</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{ fontSize: 11, color: "var(--text-primary)", padding: "10px 12px", background: "rgba(139,92,246,0.08)", borderRadius: 6, border: "1px solid rgba(139,92,246,0.3)", lineHeight: 1.6 }}>{result.recommendation}</div>
    </div>
  );
}

/* ─── v7.4: In-app Guide ─── */
function MLGuide() {
  const S = { fontSize: 12, color: "var(--text-primary)", lineHeight: 1.7 };
  const H = { fontSize: 14, fontWeight: 700, color: "var(--accent)", fontFamily: "monospace", marginTop: 18, marginBottom: 6 };
  const K = { fontFamily: "monospace", background: "var(--bg-hover)", padding: "1px 5px", borderRadius: 3, fontSize: 11 };
  return (
    <div style={{ background: "var(--bg-card)", borderRadius: 8, border: "1px solid var(--border)", padding: 22, lineHeight: 1.7, maxWidth: 900 }}>
      <div style={{ fontSize: 18, fontWeight: 700, fontFamily: "monospace", color: "var(--accent)", marginBottom: 4 }}>📖 ML 분석 — 가이드</div>
      <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 14 }}>어떤 순서로 써야 하는지, 언제 어떤 탭을 쓰는지, 설정은 어떻게 조정할지.</div>

      <div style={H}>0. 메모리 예산</div>
      <div style={S}>
        웹 서버는 최대 <b>15 GB</b> 메모리 제약. 이 페이지는 <b>상관/랭킹 기반 분석</b>과 <b>모델 구조 설계</b>만 담당합니다.
        실제 heavy ML 학습(TabPFN/GBM 풀 학습)은 <b>S3 → 원격 컴퓨팅</b>에서 실행하는 것이 원칙입니다.
        여기서 얻은 feature 중요도 · flow diagram · 추천 모델을 원격 파이프라인에 전달합니다.
      </div>

      <div style={H}>1. 학습 — 빠른 상관 기반 중요도</div>
      <div style={S}>
        ML_TABLE 소스를 선택하고 피처 / 타겟을 고른 뒤 <b>학습</b>. 결과: 피처 중요도 바 + 실측-예측 산점도.
        <br/>👉 모델의 "방향성" 검증용. 실제 결정은 <b>공정 윈도우</b> / <b>모델 흐름</b>에서.
      </div>

      <div style={H}>2. 공정 윈도우 — L0→L1→L2→L3 인과</div>
      <div style={S}>
        컬럼 이름을 파싱해 레벨(L0 FAB/VM/MASK/KNOB · L1 INLINE · L2 ET · L3 YLD)을 붙이고,
        다운스트림 피처를 <b>인과 마스크</b>로 차단. 같은 레벨 피처는 0.7× 가중치(공변량).
        <br/>FAB 스텝 번호가 있으면 <span style={K}>exp(−distance/5)</span> 감쇠 적용.
        <br/>👉 타겟이 <span style={K}>FAB_YIELD</span> 이면 자동으로 L3 로 재지정됩니다.
      </div>

      <div style={H}>3. 모델 흐름 — 흐름도 + 룩백 불확실성</div>
      <div style={S}>
        피처/타겟이 주어졌을 때 <b>실제 ML 모델 구조를 그림</b>으로 보여줍니다.
        L0 그룹 → (INLINE 누적기) → 타겟. INLINE 이 있으면 잠재 중간 상태로 표현.
        앞단 몇 스텝까지 영향이 있는지 정확히 모르므로 <b>룩백 윈도우를 ±margin</b> 으로 패딩합니다 (기본 ±3).
        <br/>⚙ admin 기어에서 <b>Lookback margin</b>을 조절하세요.
      </div>

      <div style={H}>4. 전이 — PRODA → PRODB</div>
      <div style={S}>
        같은 피처 세트로 두 제품의 중요도를 계산 후 비교:
        <ul style={{ margin: "6px 0 6px 18px" }}>
          <li><b>불변(invariant)</b>: 양쪽에서 상위권 — 안전한 전이 prior</li>
          <li><b>신규(novel)</b>: PRODB 에서만 상위권 — 새 실험 필요</li>
          <li><b>소멸(vanishing)</b>: PRODA 에서만 상위권 — 공정 변화 의심</li>
        </ul>
      </div>

      <div style={H}>5. 파레토 — 성능 × 수율</div>
      <div style={S}>
        성능 컬럼 (예: ET_VTH 재구성값) vs 수율 컬럼(FAB_YIELD)을 KNOB/MASK 그룹별로 평균. 비열등해(파레토 프론티어)에 올라온 split 들이 "성능↑ + 수율 유지"의 후보.
      </div>

      <div style={H}>6. WF Map — Wafer 맵 일관성 / 공간 패턴</div>
      <div style={S}>
        shot 레벨 데이터 (ET/INLINE)의 앙상블 맵을 계산하고 center−edge, radial slope, tilt 로 <b>공간 패턴을 자동 분류</b> (uniform / center-hot / edge-hot / radial / tilt).
        웨이퍼별 맵도 함께 보여주며 앙상블에서 가장 벗어난 웨이퍼를 탐지합니다.
      </div>

      <div style={H}>7. INLINE 상관 — 조합 검색</div>
      <div style={S}>
        ET 타겟 하나를 고정하고 INLINE 피처를 <b>단일 + 2-조합</b> (ratio/diff/sum/product/abs_diff)으로 돌려 |corr| 랭킹. <b>WF-map 패턴이 타겟과 일치하면 점수 +0.15</b> 보너스. corr 이 크면서 WF map 경향성까지 맞으면 최상 후보.
      </div>

      <div style={H}>8. PPID 분할 — 제품별/PPID별 층화</div>
      <div style={S}>
        PPID는 그룹에 따라 trend가 달라지는 경우가 많습니다. 이 탭은 각 PPID 값마다 별도 importance를 계산해 rank 안정성(|Δrank|≤3 → stable)을 리포트. unstable이 많으면 <b>per-PPID sub-model</b> 권장.
      </div>

      <div style={H}>추천 모델 맵</div>
      <ul style={{ ...S, margin: "6px 0 6px 18px" }}>
        <li><b>데이터 ≤ 10k 웨이퍼, 피처 ≤ 50</b> → TabPFN (zero-shot)</li>
        <li><b>데이터 큼, 해석 중요</b> → LightGBM + 인과 마스크 + KNOB 그룹 인덱스</li>
        <li><b>PPID 별 경향 다름</b> → PPID 별 서브모델 + 상위 메타모델</li>
        <li><b>shot 레벨 분석</b> → shot 단위 피처 → WF map 보간 → 웨이퍼 레벨 집계로 상위 모델에 공급</li>
      </ul>

      <div style={H}>Admin 설정 (⚙)</div>
      <div style={S}>
        각 탭 좌하단의 <span style={K}>⚙ admin 설정</span> 에서 조정 가능:
        <ul style={{ margin: "6px 0 0 18px" }}>
          <li><b>Lookback margin</b>: 앞단 몇 스텝까지 영향을 볼지 불확실성 범위 (기본 ±3)</li>
          <li><b>Max pair combinations</b>: INLINE 상관 탭에서 시도할 조합 수 상한 (기본 50)</li>
        </ul>
      </div>
    </div>
  );
}

/* ─── Main ML Page ─── */
export default function My_ML({ user }) {
  const [config, setConfig] = useState(null);
  const [sources, setSources] = useState([]);
  const [source, setSource] = useState(null);  // {source_type, root, product, file}
  const [colGroups, setColGroups] = useState({});
  const [targetCandidates, setTargetCandidates] = useState([]);
  const [allCols, setAllCols] = useState([]);
  const [selFeatures, setSelFeatures] = useState(new Set());
  const [target, setTarget] = useState("");
  const [model, setModel] = useState("correlation");
  const [filter, setFilter] = useState("");
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [transferSource, setTransferSource] = useState(null); // For PRODA → PRODB transfer
  const [transferResult, setTransferResult] = useState(null);
  const [tab, setTab] = useState("train"); // train | window | transfer | pareto | flow | wfmap | inlcorr | ppid | guide
  const [pwResult, setPwResult] = useState(null);
  const [trResult, setTrResult] = useState(null);
  const [prResult, setPrResult] = useState(null);
  const [xferTgt, setXferTgt] = useState(null);
  const [perfCol, setPerfCol] = useState("");
  const [yldCol, setYldCol] = useState("FAB_YIELD");
  const [groupCols, setGroupCols] = useState([]);
  // v7.4 new state
  const [flowResult, setFlowResult] = useState(null);
  const [wfResult, setWfResult] = useState(null);
  const [inlResult, setInlResult] = useState(null);
  const [ppidResult, setPpidResult] = useState(null);
  const [wfValueCol, setWfValueCol] = useState("");
  const [stratifier, setStratifier] = useState("");
  const [lookbackMargin, setLookbackMargin] = useState(3);
  const [maxPairs, setMaxPairs] = useState(50);
  const [showGear, setShowGear] = useState(false);

  useEffect(() => {
    sf(API + "/config").then(setConfig).catch(() => {});
    sf(API + "/sources").then(d => {
      setSources(d.sources || []);
      if (d.sources?.[0]) setSource(d.sources[0]);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (!source) return;
    const p = new URLSearchParams();
    p.set("source_type", source.source_type || "flat");
    if (source.root) p.set("root", source.root);
    if (source.product) p.set("product", source.product);
    if (source.file) p.set("file", source.file);
    sf(API + "/columns?" + p.toString()).then(d => {
      setColGroups(d.groups || {});
      setTargetCandidates(d.target_candidates || []);
      setAllCols(d.all_columns || []);
      if (d.target_candidates?.[0]) setTarget(d.target_candidates[0]);
      // Auto-select KNOB + MASK + INLINE + VM features by default
      const autoFeatures = new Set();
      ["KNOB", "MASK", "INLINE", "VM"].forEach(g => {
        (d.groups?.[g] || []).forEach(c => autoFeatures.add(c));
      });
      setSelFeatures(autoFeatures);
    }).catch(() => {});
  }, [source]);

  const toggleFeature = (col) => setSelFeatures(prev => { const s = new Set(prev); s.has(col) ? s.delete(col) : s.add(col); return s; });
  const toggleGroup = (groupCols) => {
    setSelFeatures(prev => {
      const s = new Set(prev);
      const allIn = groupCols.every(c => s.has(c));
      groupCols.forEach(c => allIn ? s.delete(c) : s.add(c));
      return s;
    });
  };

  const train = () => {
    if (!selFeatures.size) { alert("피처를 최소 1개 이상 선택하세요"); return; }
    if (!target) { alert("타겟을 선택하세요"); return; }
    setLoading(true); setResult(null); setTransferResult(null); setPwResult(null); setTrResult(null); setPrResult(null); setFlowResult(null); setWfResult(null); setInlResult(null); setPpidResult(null);
    const body = {
      source_type: source.source_type || "flat",
      root: source.root || "", product: source.product || "", file: source.file || "",
      features: [...selFeatures], target, model, test_ratio: 0.2, filter_expr: filter,
    };
    if (tab === "window") {
      sf(API + "/process_window", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
        .then(d => { setPwResult(d); setLoading(false); })
        .catch(e => { alert(e.message); setLoading(false); });
    } else if (tab === "transfer") {
      if (!xferTgt) { alert("전이 대상 소스를 선택하세요"); setLoading(false); return; }
      const tBody = {
        source_type: source.source_type || "flat",
        source_root: source.root || "", source_product: source.product || "", source_file: source.file || "",
        target_root: xferTgt.root || "", target_product: xferTgt.product || "", target_file: xferTgt.file || "",
        features: [...selFeatures], target, filter_expr: filter,
      };
      sf(API + "/transfer", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(tBody) })
        .then(d => { setTrResult(d); setLoading(false); })
        .catch(e => { alert(e.message); setLoading(false); });
    } else if (tab === "pareto") {
      if (!perfCol) { alert("성능 컬럼을 선택하세요"); setLoading(false); return; }
      const pBody = {
        source_type: source.source_type || "flat",
        root: source.root || "", product: source.product || "", file: source.file || "",
        performance_col: perfCol, yield_col: yldCol, group_cols: groupCols,
        filter_expr: filter, higher_is_better_perf: true, higher_is_better_yield: true,
      };
      sf(API + "/pareto", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(pBody) })
        .then(d => { setPrResult(d); setLoading(false); })
        .catch(e => { alert(e.message); setLoading(false); });
    } else if (tab === "flow") {
      sf(API + "/model_flow", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...body, lookback_margin: lookbackMargin }) })
        .then(d => { setFlowResult(d); setLoading(false); })
        .catch(e => { alert(e.message); setLoading(false); });
    } else if (tab === "wfmap") {
      if (!wfValueCol) { alert("값 컬럼을 선택하세요"); setLoading(false); return; }
      sf(API + "/wf_map", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ source_type: source.source_type || "flat", root: source.root || "", product: source.product || "", file: source.file || "", value_col: wfValueCol, filter_expr: filter }) })
        .then(d => { setWfResult(d); setLoading(false); })
        .catch(e => { alert(e.message); setLoading(false); });
    } else if (tab === "inlcorr") {
      sf(API + "/inline_corr_search", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ source_type: source.source_type || "flat", root: source.root || "", product: source.product || "", file: source.file || "", target, max_pairs: maxPairs, top_k: 25, filter_expr: filter }) })
        .then(d => { setInlResult(d); setLoading(false); })
        .catch(e => { alert(e.message); setLoading(false); });
    } else if (tab === "ppid") {
      if (!stratifier) { alert("층화 컬럼을 선택하세요"); setLoading(false); return; }
      sf(API + "/ppid_stratify", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...body, stratifier }) })
        .then(d => { setPpidResult(d); setLoading(false); })
        .catch(e => { alert(e.message); setLoading(false); });
    } else {
      sf(API + "/train", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
        .then(d => { setResult(d); setLoading(false); })
        .catch(e => { alert(e.message); setLoading(false); });
    }
  };

  const transferTo = (newSource) => {
    if (!result) { alert("먼저 학습을 실행하세요"); return; }
    setTransferSource(newSource); setLoading(true);
    const body = {
      source_type: newSource.source_type || "flat",
      root: newSource.root || "", product: newSource.product || "", file: newSource.file || "",
      features: [...selFeatures], target, model, test_ratio: 0.2, filter_expr: filter,
    };
    sf(API + "/train", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .then(d => { setTransferResult(d); setLoading(false); })
      .catch(e => { alert(e.message); setLoading(false); });
  };

  if (!config) return <div style={{ padding: 40, textAlign: "center" }}><Loading text="로딩 중..." /></div>;

  const S = { padding: "6px 10px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg-card)", color: "var(--text-primary)", fontSize: 12, outline: "none" };

  return (
    <div style={{ padding: "24px 32px", background: "var(--bg-primary)", minHeight: "calc(100vh - 48px)", color: "var(--text-primary)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16, flexWrap: "wrap", gap: 8 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <div style={{ fontSize: 16, fontWeight: 700, fontFamily: "monospace", color: "var(--accent)" }}>{">"} ml_analysis</div>
          <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
            {[["train", "학습"], ["window", "공정 윈도우"], ["flow", "모델 흐름"],
              ["transfer", "전이"], ["pareto", "파레토"],
              ["wfmap", "WF Map"], ["inlcorr", "INLINE 상관"], ["ppid", "PPID 분할"],
              ["guide", "가이드"]].map(([k, l]) => (
              <span key={k} onClick={() => { setTab(k); }} style={{ padding: "4px 12px", borderRadius: 4, fontSize: 11, cursor: "pointer", fontWeight: tab === k ? 700 : 400, background: tab === k ? "var(--accent-glow)" : "transparent", color: tab === k ? "var(--accent)" : "var(--text-secondary)", border: "1px solid " + (tab === k ? "var(--accent)" : "transparent"), fontFamily: "monospace" }}>{l}</span>
            ))}
          </div>
        </div>
        <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>
          {tab === "window" ? "L0→L1→L2→L3 인과 분석"
            : tab === "flow" ? "모델 아키텍처 + 룩백 불확실성"
            : tab === "transfer" ? "PRODA → PRODB 지식 전이"
            : tab === "pareto" ? "성능 × 수율 트레이드오프"
            : tab === "wfmap" ? "Wafer-map 일관성 + 공간 패턴"
            : tab === "inlcorr" ? "INLINE ↔ ET 상관 + 페어 조합"
            : tab === "ppid" ? "PPID/KNOB 별 중요도 안정성"
            : tab === "guide" ? "사용법 — 단계별 가이드"
            : "Wide-table ML (TabPFN / TabICL 지원)"}
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "320px 1fr", gap: 16 }}>
        {/* Left: Configuration */}
        <div style={{ background: "var(--bg-secondary)", borderRadius: 8, border: "1px solid var(--border)", padding: 16 }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: "var(--accent)", marginBottom: 10, fontFamily: "monospace" }}>1. 소스</div>
          <select value={source ? `${source.root || source.file}/${source.product || ""}` : ""}
            onChange={e => { const s = sources.find(x => `${x.root || x.file}/${x.product || ""}` === e.target.value); if (s) setSource(s); }}
            style={{ ...S, width: "100%", marginBottom: 12 }}>
            {sources.map(s => <option key={s.label} value={`${s.root || s.file}/${s.product || ""}`}>{s.label}</option>)}
          </select>

          <div style={{ fontSize: 12, fontWeight: 700, color: "var(--accent)", marginBottom: 8, fontFamily: "monospace" }}>2. 피처 ({selFeatures.size})</div>
          <div style={{ maxHeight: 260, overflow: "auto", border: "1px solid var(--border)", borderRadius: 6, padding: 8, marginBottom: 12 }}>
            {Object.entries(colGroups).map(([g, cols]) => {
              const allIn = cols.every(c => selFeatures.has(c));
              return (
                <div key={g} style={{ marginBottom: 6 }}>
                  <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, fontWeight: 700, color: "var(--accent)", cursor: "pointer", padding: "2px 0" }}>
                    <input type="checkbox" checked={allIn} onChange={() => toggleGroup(cols)} style={{ accentColor: "var(--accent)" }} />
                    {g} <span style={{ color: "var(--text-secondary)", fontWeight: 400 }}>({cols.length})</span>
                  </label>
                  <div style={{ marginLeft: 20, display: "flex", flexWrap: "wrap", gap: 3 }}>
                    {cols.slice(0, 20).map(c => (
                      <label key={c} style={{ display: "flex", alignItems: "center", gap: 2, fontSize: 9, cursor: "pointer", padding: "1px 4px", borderRadius: 2, background: selFeatures.has(c) ? "var(--accent-glow)" : "transparent" }}>
                        <input type="checkbox" checked={selFeatures.has(c)} onChange={() => toggleFeature(c)} style={{ transform: "scale(0.8)", accentColor: "var(--accent)" }} />
                        <span style={{ fontFamily: "monospace" }}>{c.replace(g + "_", "")}</span>
                      </label>
                    ))}
                    {cols.length > 20 && <span style={{ fontSize: 9, color: "var(--text-secondary)" }}>+{cols.length - 20}개 더</span>}
                  </div>
                </div>
              );
            })}
          </div>

          <div style={{ fontSize: 12, fontWeight: 700, color: "var(--accent)", marginBottom: 6, fontFamily: "monospace" }}>3. 타겟</div>
          <select value={target} onChange={e => setTarget(e.target.value)} style={{ ...S, width: "100%", marginBottom: 12 }}>
            <option value="">-- 타겟 선택 --</option>
            {targetCandidates.map(c => <option key={c} value={c}>{c} ⭐</option>)}
            {allCols.filter(c => !targetCandidates.includes(c)).map(c => <option key={c} value={c}>{c}</option>)}
          </select>

          <div style={{ fontSize: 12, fontWeight: 700, color: "var(--accent)", marginBottom: 6, fontFamily: "monospace" }}>4. 모델</div>
          <select value={model} onChange={e => setModel(e.target.value)} style={{ ...S, width: "100%", marginBottom: 10 }}>
            {(config.available_models || []).map(m => <option key={m} value={m}>{m}</option>)}
          </select>
          <div style={{ fontSize: 9, color: "var(--text-secondary)", marginBottom: 12, lineHeight: 1.4 }}>
            💡 correlation = 데모 대체용. <code>backend/routers/ml.py</code>의 <code>_train_model</code>을 교체해 TabPFN/TabICL 연결.
          </div>

          <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 4 }}>필터 (선택, SQL)</div>
          <input value={filter} onChange={e => setFilter(e.target.value)} placeholder="예: RESULT == 'PASS'" style={{ ...S, width: "100%", marginBottom: 12 }} />

          {/* Transfer-specific: target source selector */}
          {tab === "transfer" && sources.length > 1 && (
            <div style={{ marginBottom: 12, padding: 10, background: "rgba(59,130,246,0.05)", borderRadius: 6, border: "1px solid rgba(59,130,246,0.3)" }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: "#3b82f6", marginBottom: 6, fontFamily: "monospace" }}>타겟 제품</div>
              <select value={xferTgt ? `${xferTgt.root || xferTgt.file}/${xferTgt.product || ""}` : ""}
                onChange={e => { const s = sources.find(x => `${x.root || x.file}/${x.product || ""}` === e.target.value); if (s) setXferTgt(s); }}
                style={{ ...S, width: "100%", fontSize: 11 }}>
                <option value="">-- 타겟 제품 선택 --</option>
                {sources.filter(s => !source || `${s.root || s.file}/${s.product || ""}` !== `${source.root || source.file}/${source.product || ""}`).map(s => (
                  <option key={s.label} value={`${s.root || s.file}/${s.product || ""}`}>{s.label}</option>
                ))}
              </select>
            </div>
          )}
          {/* Pareto-specific: perf / yield / group columns */}
          {tab === "pareto" && (
            <div style={{ marginBottom: 12, padding: 10, background: "rgba(16,185,129,0.05)", borderRadius: 6, border: "1px solid rgba(16,185,129,0.3)" }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: "#10b981", marginBottom: 6, fontFamily: "monospace" }}>파레토 축</div>
              <div style={{ fontSize: 10, color: "var(--text-secondary)", marginBottom: 2 }}>성능 컬럼</div>
              <select value={perfCol} onChange={e => setPerfCol(e.target.value)} style={{ ...S, width: "100%", fontSize: 11, marginBottom: 6 }}>
                <option value="">-- 선택 --</option>
                {allCols.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
              <div style={{ fontSize: 10, color: "var(--text-secondary)", marginBottom: 2 }}>수율 컬럼</div>
              <select value={yldCol} onChange={e => setYldCol(e.target.value)} style={{ ...S, width: "100%", fontSize: 11, marginBottom: 6 }}>
                {allCols.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
              <div style={{ fontSize: 10, color: "var(--text-secondary)", marginBottom: 2 }}>그룹 기준 (KNOB 권장)</div>
              <div style={{ maxHeight: 80, overflow: "auto", border: "1px solid var(--border)", borderRadius: 4, padding: 4 }}>
                {(colGroups.KNOB || []).concat(colGroups.MASK || []).map(c => (
                  <label key={c} style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 10, cursor: "pointer" }}>
                    <input type="checkbox" checked={groupCols.includes(c)} onChange={() => setGroupCols(p => p.includes(c) ? p.filter(x => x !== c) : [...p, c])} style={{ transform: "scale(0.9)" }} />
                    <span style={{ fontFamily: "monospace" }}>{c}</span>
                  </label>
                ))}
              </div>
            </div>
          )}
          {/* v7.4: WF Map controls */}
          {tab === "wfmap" && (
            <div style={{ marginBottom: 12, padding: 10, background: "rgba(6,182,212,0.05)", borderRadius: 6, border: "1px solid rgba(6,182,212,0.3)" }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: "#06b6d4", marginBottom: 6, fontFamily: "monospace" }}>값 컬럼 (shot 레벨)</div>
              <select value={wfValueCol} onChange={e => setWfValueCol(e.target.value)} style={{ ...S, width: "100%", fontSize: 11 }}>
                <option value="">-- 선택 --</option>
                {allCols.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
              <div style={{ fontSize: 9, color: "var(--text-secondary)", marginTop: 4, lineHeight: 1.4 }}>
                소스에 SHOT_X/SHOT_Y/LOT_WF 컬럼이 필요합니다. 실제 shot 레벨 분석은 ET 또는 INLINE 사용.
              </div>
            </div>
          )}
          {/* v7.4: PPID stratify controls */}
          {tab === "ppid" && (
            <div style={{ marginBottom: 12, padding: 10, background: "rgba(139,92,246,0.05)", borderRadius: 6, border: "1px solid rgba(139,92,246,0.3)" }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: "#8b5cf6", marginBottom: 6, fontFamily: "monospace" }}>층화 기준 (PPID / KNOB)</div>
              <select value={stratifier} onChange={e => setStratifier(e.target.value)} style={{ ...S, width: "100%", fontSize: 11 }}>
                <option value="">-- 선택 --</option>
                {(colGroups.KNOB || []).concat(colGroups.MASK || [], colGroups.OTHER || []).filter(c => !c.includes("TIME") && !c.includes("DATE")).map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
          )}
          <button onClick={train} disabled={loading} style={{ width: "100%", padding: "10px 16px", borderRadius: 6, border: "none", background: "var(--accent)", color: "#fff", fontSize: 13, fontWeight: 600, cursor: loading ? "wait" : "pointer" }}>
            {loading ? "실행 중..." : (
              tab === "window" ? "▶ 공정 윈도우 분석"
              : tab === "flow" ? "▶ 모델 흐름 생성"
              : tab === "transfer" ? "▶ PRODA → PRODB 비교"
              : tab === "pareto" ? "▶ 파레토 프론티어 탐색"
              : tab === "wfmap" ? "▶ WF Map 분석"
              : tab === "inlcorr" ? "▶ INLINE 상관 탐색"
              : tab === "ppid" ? "▶ PPID 별 층화"
              : tab === "guide" ? "(우측 가이드 참조)"
              : "▶ 모델 학습"
            )}
          </button>
          {/* v7.4: Gear icon for admin per-feature settings */}
          {user?.role === "admin" && tab !== "guide" && (
            <div style={{ position: "relative", marginTop: 10 }}>
              <span onClick={() => setShowGear(!showGear)} title="고급 설정" style={{ display: "inline-flex", alignItems: "center", gap: 4, cursor: "pointer", fontSize: 10, color: "var(--text-secondary)", padding: "4px 8px", borderRadius: 4, background: showGear ? "var(--accent-glow)" : "transparent" }}>
                ⚙ admin 설정
              </span>
              {showGear && (
                <div style={{ marginTop: 6, padding: 10, background: "var(--bg-primary)", borderRadius: 6, border: "1px solid var(--border)" }}>
                  <div style={{ fontSize: 10, color: "var(--text-secondary)", marginBottom: 4 }}>Lookback margin (스텝)</div>
                  <input type="number" min="0" max="10" value={lookbackMargin} onChange={e => setLookbackMargin(parseInt(e.target.value) || 3)} style={{ ...S, width: "100%", fontSize: 11, marginBottom: 6 }} />
                  <div style={{ fontSize: 10, color: "var(--text-secondary)", marginBottom: 4 }}>최대 페어 조합 수 (INLINE 상관)</div>
                  <input type="number" min="5" max="500" value={maxPairs} onChange={e => setMaxPairs(parseInt(e.target.value) || 50)} style={{ ...S, width: "100%", fontSize: 11 }} />
                  <div style={{ fontSize: 9, color: "var(--text-secondary)", marginTop: 6, lineHeight: 1.5 }}>
                    메모리 예산: 15GB — 무거운 ML 은 S3 동기화를 통해 원격 컴퓨팅에 위임해야 합니다.
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Right: Results */}
        <div style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
          {!result && !pwResult && !trResult && !prResult && !loading && (
            <div style={{ background: "var(--bg-card)", borderRadius: 8, border: "1px dashed var(--border)", padding: 60, textAlign: "center", color: "var(--text-secondary)" }}>
              피처와 타겟을 선택한 뒤 실행 버튼을 클릭하세요
            </div>
          )}
          {loading && <div style={{ padding: 40, textAlign: "center" }}><Loading text="실행 중..." /></div>}
          {tab === "transfer" && trResult && <TransferPanel result={trResult} />}
          {tab === "pareto" && prResult && <ParetoPanel result={prResult} />}
          {tab === "flow" && flowResult && <ModelFlowDiagram result={flowResult} />}
          {tab === "wfmap" && wfResult && <WFMapPanel result={wfResult} />}
          {tab === "inlcorr" && inlResult && <InlineCorrPanel result={inlResult} />}
          {tab === "ppid" && ppidResult && <PpidStratifyPanel result={ppidResult} />}
          {tab === "guide" && <MLGuide />}
          {tab === "window" && pwResult && (<>
            <LevelHierarchyPanel perLevel={pwResult.per_level} targetLabel={`${pwResult.target} (${pwResult.target_level_label})`} />
            <ParsimonyPanel pars={pwResult.parsimony} />
            <ProcessWindowFlow steps={pwResult.steps || []} target={pwResult.target} targetStep={pwResult.target_fab_step} />
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <CausalityMask features={pwResult.features || []} blockedCount={pwResult.blocked_count} keptCount={pwResult.kept_count} />
              <KnobSplitPanel splits={pwResult.knob_splits || {}} target={pwResult.target} />
            </div>
            <div style={{ background: "rgba(59,130,246,0.06)", border: "1px solid rgba(59,130,246,0.25)", borderRadius: 8, padding: 12 }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: "#3b82f6", marginBottom: 6, fontFamily: "monospace" }}>🧭 인과 요약</div>
              <div style={{ fontSize: 11, color: "var(--text-primary)", lineHeight: 1.6 }}>{pwResult.causality_note}</div>
              <div style={{ marginTop: 8, fontSize: 11, fontWeight: 700, color: "#10b981", fontFamily: "monospace" }}>🤖 추천 모델</div>
              <div style={{ fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.6 }}>{pwResult.recommended_model}</div>
            </div>
            <div style={{ fontSize: 10, color: "var(--text-secondary)", fontStyle: "italic" }}>
              분석 행 수: {pwResult.total_rows}. 기여 상위 {(pwResult.top_steps || []).length} 개 스텝 그룹을 버블 차트로 표시.
            </div>
          </>)}
          {tab === "train" && result && (<>
            <AnalysisPipeline features={[...selFeatures]} target={target} model={model} metrics={result.metrics} />

            {/* Transfer to another product */}
            {sources.length > 1 && (
              <div style={{ background: "rgba(16,185,129,0.06)", border: "1px solid rgba(16,185,129,0.3)", borderRadius: 8, padding: 12 }}>
                <div style={{ fontSize: 12, fontWeight: 700, color: "#10b981", marginBottom: 8, fontFamily: "monospace" }}>🔄 이 분석을 다른 제품에 적용</div>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  {sources.filter(s => !source || (s.product !== source.product || s.root !== source.root || s.file !== source.file)).map(s => (
                    <button key={s.label} onClick={() => transferTo(s)}
                      style={{ padding: "6px 12px", borderRadius: 4, border: "1px solid #10b981", background: transferSource === s ? "#10b981" : "transparent", color: transferSource === s ? "#fff" : "#10b981", fontSize: 11, fontWeight: 600, cursor: "pointer", fontFamily: "monospace" }}>
                      → {s.product || s.file}
                    </button>
                  ))}
                </div>
                {transferResult && (
                  <div style={{ marginTop: 10, padding: 10, background: "var(--bg-card)", borderRadius: 6, fontSize: 11 }}>
                    <div style={{ fontWeight: 700, color: "#10b981", marginBottom: 4 }}>전이 결과 → {transferSource?.product || transferSource?.file}</div>
                    <div style={{ display: "flex", gap: 16, fontFamily: "monospace" }}>
                      {transferResult.metrics?.accuracy != null && <span>정확도: <b>{(transferResult.metrics.accuracy * 100).toFixed(1)}%</b></span>}
                      {transferResult.metrics?.r2 != null && <span>R²: <b>{transferResult.metrics.r2.toFixed(4)}</b></span>}
                      {transferResult.metrics?.rmse != null && <span>RMSE: <b>{transferResult.metrics.rmse.toFixed(4)}</b></span>}
                      <span style={{ color: "var(--text-secondary)" }}>n={transferResult.metrics?.n_test || 0}</span>
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Importance + Scatter */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <ImportanceBar importance={result.importance} />
              <PredictionScatter scatter={result.scatter} isClassification={result.is_classification} />
            </div>

            <div style={{ fontSize: 10, color: "var(--text-secondary)", fontStyle: "italic" }}>
              ℹ️ {result.note}
            </div>
          </>)}
        </div>
      </div>
    </div>
  );
}
