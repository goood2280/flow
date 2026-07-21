import { useState, useEffect, useCallback } from "react";
import { toast } from "../components/Toast";
import { sf } from "../lib/api";

// v9.3.x: RAM 캐시 관리 — SplitTable 톱니바퀴(설정 모달) 안에 있던 캐시 관리를
// 데이터 그룹의 독립 탭으로 승격. 제품별 분해(전 제품 현황)를 추가.
const API = "/api/splittable";

const S_INPUT = {
  padding: "4px 6px", borderRadius: 4, border: "1px solid var(--border)",
  background: "var(--bg-secondary)", color: "var(--text-primary)", fontSize: 13,
};
const S_BTN = {
  padding: "5px 10px", borderRadius: 6, border: "1px solid var(--border)",
  background: "transparent", color: "var(--text-primary)", fontSize: 13, cursor: "pointer",
};

export default function My_RamCache() {
  const [overview, setOverview] = useState(null);
  const [overviewLoading, setOverviewLoading] = useState(false);
  const [memOverview, setMemOverview] = useState(null);
  const [selProd, setSelProd] = useState("");
  const [subTab, setSubTab] = useState("priority");
  const [priorityLots, setPriorityLots] = useState([]);
  const [lotStatuses, setLotStatuses] = useState({});
  const [lotStatusSkipped, setLotStatusSkipped] = useState("");
  const [latestMainStep, setLatestMainStep] = useState(null);
  const [contents, setContents] = useState(null);
  const [contentsLoading, setContentsLoading] = useState(false);
  const [budgets, setBudgets] = useState(null);
  const [budgetDraft, setBudgetDraft] = useState("");
  const [budgetSaving, setBudgetSaving] = useState(false);

  const loadOverview = useCallback(() => {
    setOverviewLoading(true);
    sf(API + "/ram-cache/overview")
      .then(d => {
        setOverview(d);
        setSelProd(prev => prev || (d.products?.[0]?.product || ""));
      })
      .catch(e => toast.error("캐시 현황 로드 실패: " + (e?.message || e)))
      .finally(() => setOverviewLoading(false));
    sf(API + "/memory/overview")
      .then(d => setMemOverview(d))
      .catch(() => setMemOverview(null));
  }, []);

  const loadPriority = useCallback((prod) => {
    if (!prod) return;
    sf(API + "/ram-cache/priority-lots?product=" + encodeURIComponent(prod))
      .then(d => setPriorityLots(d.lots || []))
      .catch(() => setPriorityLots([]));
    sf(API + "/ram-cache/lot-status?product=" + encodeURIComponent(prod))
      .then(d => { setLotStatuses(d.statuses || {}); setLatestMainStep(d.latest_main_step || null); setLotStatusSkipped(d.skipped_reason || ""); })
      .catch(() => { setLotStatuses({}); setLatestMainStep(null); setLotStatusSkipped(""); });
  }, []);

  const loadContents = useCallback((prod) => {
    if (!prod) return;
    setContentsLoading(true);
    sf(API + "/ram-cache/contents?product=" + encodeURIComponent(prod))
      .then(d => setContents(d))
      .catch(() => setContents(null))
      .finally(() => setContentsLoading(false));
  }, []);

  const loadBudgets = useCallback((prod) => {
    sf(API + "/ram-cache/product-budgets")
      .then(d => {
        setBudgets(d);
        const pb = (d.products || {})[prod];
        setBudgetDraft(pb ? String(pb.max_roots) : "");
      })
      .catch(() => {});
  }, []);

  useEffect(() => { loadOverview(); }, [loadOverview]);
  useEffect(() => {
    if (!selProd) return;
    loadPriority(selProd);
    loadContents(selProd);
    loadBudgets(selProd);
  }, [selProd, loadPriority, loadContents, loadBudgets]);

  const savePriority = (lots) => {
    sf(API + "/ram-cache/priority-lots/save", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ product: selProd, lots }),
    })
      .then(() => { toast.ok("주요 lot 저장됨"); loadPriority(selProd); loadOverview(); })
      .catch(e => toast.error("저장 실패: " + (e?.message || e)));
  };

  const saveBudget = () => {
    if (!selProd) return;
    setBudgetSaving(true);
    sf(API + "/ram-cache/product-budgets/save", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ product: selProd, max_roots: Number(budgetDraft) || 1000 }),
    })
      .then(() => { toast.ok("예산 저장됨"); loadBudgets(selProd); loadOverview(); })
      .catch(e => toast.error("저장 실패: " + (e?.message || e)))
      .finally(() => setBudgetSaving(false));
  };

  // 엑셀 다중 셀 붙여넣기 — 표 컬럼 순서(purpose|lot_id|step_id|step_desc|comment)로
  // 위치 매핑하고, 자동 컬럼(step_id/step_desc)에 해당하는 값은 버린다.
  const PASTE_COLS = ["purpose", "lot_id", "step_id", "step_desc", "comment"];
  const EDITABLE_COLS = ["purpose", "lot_id", "comment"];
  const handleGridPaste = (e, rowIdx, field) => {
    const text = (e.clipboardData && e.clipboardData.getData("text")) || "";
    if (!text.includes("\t") && !text.includes("\n")) return; // 단일 셀은 기본 동작
    e.preventDefault();
    const lines = text.replace(/\r/g, "").split("\n");
    if (lines.length && lines[lines.length - 1] === "") lines.pop();
    const grid = lines.map(l => l.split("\t"));
    const startCol = Math.max(0, PASTE_COLS.indexOf(field));
    setPriorityLots(prev => {
      const v = [...prev];
      grid.forEach((cells, ri) => {
        const ti = rowIdx + ri;
        while (v.length <= ti) v.push({ lot_id: "", purpose: "", comment: "", cache_enabled: true });
        const row = { ...v[ti] };
        cells.forEach((val, ci) => {
          const col = PASTE_COLS[startCol + ci];
          if (col && EDITABLE_COLS.includes(col)) row[col] = String(val || "").trim();
        });
        v[ti] = row;
      });
      return v;
    });
    toast.ok(grid.length + "행 붙여넣기 완료");
  };

  const refreshAll = () => {
    loadOverview();
    if (selProd) { loadPriority(selProd); loadContents(selProd); loadBudgets(selProd); }
  };

  const usagePct = overview?.max_mb ? Math.min(100, overview.total_mb / overview.max_mb * 100) : 0;

  return (
    <div style={{ padding: 20, maxWidth: 1100, margin: "0 auto" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
        <div>
          <div style={{ fontSize: 18, fontWeight: 800, color: "var(--text-primary)" }}>🧠 주요 Lot · RAM 캐시 관리</div>
          <div style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 2 }}>
            제품별 주요 lot 관리(목적·위치·코멘트) — 등록된 lot은 RAM 캐시에 우선 적재됩니다
          </div>
        </div>
        <button onClick={refreshAll} style={{ ...S_BTN, color: "var(--accent)" }}>새로고침</button>
      </div>

      {/* 전체 사용량 바 */}
      <div style={{ padding: "10px 12px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg-card)", marginBottom: 14 }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
          <span style={{ fontSize: 14, fontWeight: 800 }}>전체 RAM 캐시 사용량</span>
          {overview && <span style={{ fontSize: 13, color: "var(--text-secondary)", fontFamily: "monospace" }}>
            {overview.total_mb} MB / {overview.max_mb} MB
          </span>}
        </div>
        <div style={{ height: 8, borderRadius: 4, background: "var(--bg-secondary)", overflow: "hidden" }}>
          <div style={{ height: "100%", borderRadius: 4, transition: "width 0.3s", width: usagePct + "%",
            background: usagePct > 85 ? "rgba(239,68,68,0.8)" : "var(--accent)" }} />
        </div>
      </div>

      {/* 서버 메모리 종합 — root RAM 캐시 외의 캐시(파일탐색기·ET Index 등)까지 합산 */}
      {memOverview && <div style={{ padding: "10px 12px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg-card)", marginBottom: 14 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 6 }}>
          <span style={{ fontSize: 14, fontWeight: 800 }}>서버 메모리 종합 (전체 캐시)</span>
          <span style={{ fontSize: 13, color: "var(--text-secondary)", fontFamily: "monospace" }}>
            캐시 합계 {memOverview.caches_total_mb} MB
            {memOverview.process?.rss_gb != null && <> · 프로세스 RSS {memOverview.process.rss_gb} GB</>}
            {memOverview.process?.system_total_gb != null && <> · 호스트 {memOverview.process.system_available_gb}/{memOverview.process.system_total_gb} GB 여유</>}
          </span>
        </div>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr style={{ color: "var(--text-secondary)" }}>
              <th style={{ padding: "3px 6px", textAlign: "left", borderBottom: "1px solid var(--border)" }}>캐시</th>
              <th style={{ padding: "3px 6px", textAlign: "right", borderBottom: "1px solid var(--border)" }}>항목</th>
              <th style={{ padding: "3px 6px", textAlign: "right", borderBottom: "1px solid var(--border)" }}>사용 MB</th>
              <th style={{ padding: "3px 6px", textAlign: "right", borderBottom: "1px solid var(--border)" }}>예산 MB</th>
              <th style={{ padding: "3px 6px", textAlign: "left", borderBottom: "1px solid var(--border)", width: 140 }}></th>
            </tr>
          </thead>
          <tbody>
            {(memOverview.caches || []).map(c => {
              const pct = c.budget_mb > 0 ? Math.min(100, c.mb / c.budget_mb * 100) : 0;
              return (
                <tr key={c.key} style={{ borderBottom: "1px solid var(--border)" }}>
                  <td style={{ padding: "3px 6px" }}>{c.label}</td>
                  <td style={{ padding: "3px 6px", textAlign: "right", fontFamily: "monospace" }}>{c.entries?.toLocaleString()}</td>
                  <td style={{ padding: "3px 6px", textAlign: "right", fontFamily: "monospace" }}>{c.mb}</td>
                  <td style={{ padding: "3px 6px", textAlign: "right", fontFamily: "monospace" }}>{c.budget_mb || "-"}</td>
                  <td style={{ padding: "3px 6px" }}>
                    <div style={{ height: 5, borderRadius: 3, background: "var(--bg-secondary)", overflow: "hidden" }}>
                      <div style={{ height: "100%", borderRadius: 3, width: pct + "%",
                        background: pct > 85 ? "rgba(239,68,68,0.8)" : "var(--accent)" }} />
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>}

      {/* 제품별 현황 */}
      <div style={{ marginBottom: 16 }}>
        <div style={{ fontSize: 14, fontWeight: 800, marginBottom: 6 }}>제품별 현황</div>
        <div style={{ borderRadius: 8, border: "1px solid var(--border)", overflow: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ background: "var(--bg-secondary)" }}>
                <th style={{ padding: "6px 10px", textAlign: "left", borderBottom: "1px solid var(--border)" }}>제품</th>
                <th style={{ padding: "6px 10px", textAlign: "right", borderBottom: "1px solid var(--border)" }}>캐시 root</th>
                <th style={{ padding: "6px 10px", textAlign: "right", borderBottom: "1px solid var(--border)" }}>MB</th>
                <th style={{ padding: "6px 10px", textAlign: "right", borderBottom: "1px solid var(--border)" }}>우선 lot (적재/등록)</th>
                <th style={{ padding: "6px 10px", textAlign: "right", borderBottom: "1px solid var(--border)" }}>상한 (root)</th>
              </tr>
            </thead>
            <tbody>
              {(overview?.products || []).map(p => (
                <tr key={p.product} onClick={() => setSelProd(p.product)}
                  style={{ cursor: "pointer", borderBottom: "1px solid var(--border)",
                    background: selProd === p.product ? "var(--accent-glow)" : "transparent" }}>
                  <td style={{ padding: "6px 10px", fontFamily: "monospace", fontWeight: selProd === p.product ? 700 : 400,
                    color: selProd === p.product ? "var(--accent)" : "var(--text-primary)" }}>{p.product}</td>
                  <td style={{ padding: "6px 10px", textAlign: "right", fontFamily: "monospace" }}>{p.roots.toLocaleString()}</td>
                  <td style={{ padding: "6px 10px", textAlign: "right", fontFamily: "monospace" }}>{p.mb}</td>
                  <td style={{ padding: "6px 10px", textAlign: "right", fontFamily: "monospace" }}>
                    {p.priority_total > 0
                      ? <span style={{ color: p.priority_cached >= p.priority_total ? "rgba(34,197,94,0.9)" : "rgba(239,68,68,0.8)" }}>
                          {p.priority_cached}/{p.priority_total}
                        </span>
                      : <span style={{ color: "var(--text-secondary)" }}>-</span>}
                  </td>
                  <td style={{ padding: "6px 10px", textAlign: "right", fontFamily: "monospace" }}>
                    {p.max_roots?.toLocaleString()}{p.max_roots_custom && <span style={{ marginLeft: 4, color: "var(--accent)" }}>*</span>}
                  </td>
                </tr>
              ))}
              {!overviewLoading && (overview?.products || []).length === 0 &&
                <tr><td colSpan={5} style={{ padding: 16, textAlign: "center", color: "var(--text-secondary)" }}>제품이 없습니다</td></tr>}
              {overviewLoading &&
                <tr><td colSpan={5} style={{ padding: 16, textAlign: "center", color: "var(--text-secondary)" }}>로딩 중...</td></tr>}
            </tbody>
          </table>
        </div>
        <div style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 4 }}>
          * = 제품별 상한 직접 설정됨 (기본 {overview?.default_max_roots?.toLocaleString() || "-"}). 행을 클릭하면 아래에서 해당 제품을 관리합니다.
        </div>
      </div>

      {/* 선택 제품 상세 */}
      {selProd && <div>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
          <span style={{ fontSize: 15, fontWeight: 800, fontFamily: "monospace", color: "var(--accent)" }}>{selProd}</span>
          <div style={{ display: "flex", gap: 4, borderBottom: "1px solid var(--border)", flex: 1 }}>
            {[["priority", "주요 Lot"], ["contents", "전체 캐시"]].map(([k, l]) => (
              <span key={k} onClick={() => setSubTab(k)}
                style={{ padding: "4px 10px", fontSize: 13, cursor: "pointer",
                  fontWeight: subTab === k ? 700 : 500,
                  borderBottom: subTab === k ? "2px solid var(--accent)" : "2px solid transparent",
                  color: subTab === k ? "var(--accent)" : "var(--text-secondary)" }}>{l}</span>
            ))}
          </div>
        </div>

        {/* 주요 Lot 서브탭 — 엑셀형 랏 운영 표 */}
        {subTab === "priority" && <div style={{ display: "grid", gap: 8 }}>
          <div style={{ fontSize: 13, color: "var(--text-secondary)" }}>
            제품별 주요 lot 관리 — purpose/comment는 엔지니어가 직접 정리하고, 위치(step_id/step_desc)는 최신 진행 데이터에서 자동으로 채워집니다.
            등록된 lot은 RAM 캐시에 우선 적재됩니다 (lot_id 앞 5자리 = root_lot_id).
          </div>
          {lotStatusSkipped && <div style={{ padding: "6px 10px", borderRadius: 6, fontSize: 12,
            border: "1px solid rgba(239,68,68,0.35)", background: "rgba(239,68,68,0.06)", color: "var(--text-secondary)" }}>
            서버 메모리 여유가 부족해 lot 위치(step) 조회를 건너뛰었습니다 ({lotStatusSkipped}) — 잠시 후 새로고침하면 다시 시도합니다.
          </div>}
          {latestMainStep && latestMainStep.step_id && <div style={{ display: "flex", alignItems: "center", gap: 8,
            padding: "7px 10px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg-card)", fontSize: 13 }}>
            <span style={{ fontWeight: 700, color: "var(--text-secondary)", whiteSpace: "nowrap" }}>참고 · 최근 진행 main step</span>
            <span style={{ fontFamily: "monospace", fontWeight: 700, color: "var(--accent)" }}>{latestMainStep.step_id}</span>
            <span style={{ fontWeight: 600 }}>{latestMainStep.approx ? "≈ " : ""}{latestMainStep.step_desc}</span>
            <span style={{ fontSize: 12, color: "var(--text-secondary)", marginLeft: "auto", fontFamily: "monospace", whiteSpace: "nowrap" }}
              title={"tkout: " + (latestMainStep.tkout_time || "-")}>
              {latestMainStep.root_lot_id}{latestMainStep.tkout_time ? " · " + String(latestMainStep.tkout_time).slice(0, 16) : ""}
            </span>
          </div>}
          <div style={{ borderRadius: 6, border: "1px solid var(--border)", overflow: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr style={{ background: "var(--bg-secondary)" }}>
                  {["purpose", "lot_id", "step_id (위치)", "step_desc", "comment", "캐싱", ""].map((h, i) => (
                    <th key={i} style={{ padding: "6px 8px", textAlign: "left", borderBottom: "1px solid var(--border)",
                      borderRight: i < 6 ? "1px solid var(--border)" : "none", whiteSpace: "nowrap",
                      fontSize: 12, color: "var(--text-secondary)" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {priorityLots.map((lot, i) => {
                  const rootId = (lot.lot_id || "").slice(0, 5).toUpperCase();
                  const st = lotStatuses[rootId] || {};
                  const inCache = contents ? (contents.entries || []).some(e => e.root_lot_id === rootId) : null;
                  const cellInput = (field, mono, placeholder) => (
                    <input value={lot[field] || ""} placeholder={placeholder}
                      onChange={e => { const v = [...priorityLots]; v[i] = { ...v[i], [field]: e.target.value }; setPriorityLots(v); }}
                      onPaste={e => handleGridPaste(e, i, field)}
                      style={{ width: "100%", boxSizing: "border-box", padding: "5px 8px", border: "none", outline: "none",
                        background: "transparent", color: "var(--text-primary)", fontSize: 13,
                        fontFamily: mono ? "monospace" : "inherit" }} />
                  );
                  return (
                    <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
                      <td style={{ borderRight: "1px solid var(--border)", minWidth: 120 }}>{cellInput("purpose", false, "목적")}</td>
                      <td style={{ borderRight: "1px solid var(--border)", minWidth: 110 }}>
                        <div style={{ display: "flex", alignItems: "center" }}>
                          {cellInput("lot_id", true, "lot_id")}
                          {rootId && inCache !== null &&
                            <span title={inCache ? "RAM 캐시 적재됨" : "캐시 미적재"}
                              style={{ marginRight: 6, fontSize: 11, color: inCache ? "rgba(34,197,94,0.9)" : "rgba(239,68,68,0.8)" }}>
                              {inCache ? "●" : "○"}
                            </span>}
                        </div>
                      </td>
                      <td style={{ borderRight: "1px solid var(--border)", padding: "5px 8px", fontFamily: "monospace",
                        whiteSpace: "nowrap", color: st.step_id ? "var(--text-primary)" : "var(--text-secondary)" }}
                        title={st.tkout_time ? "tkout: " + st.tkout_time : ""}>
                        {st.step_id || "-"}
                      </td>
                      <td style={{ borderRight: "1px solid var(--border)", padding: "5px 8px", whiteSpace: "nowrap",
                        color: st.step_desc ? "var(--text-primary)" : "var(--text-secondary)" }}
                        title={st.step_desc_approx ? "정확 매칭 없음 — 같은 계열에서 이전 main step 기준 근사" : ""}>
                        {st.step_desc_approx ? "≈ " : ""}{st.step_desc || "-"}
                      </td>
                      <td style={{ borderRight: "1px solid var(--border)", minWidth: 160 }}>{cellInput("comment", false, "코멘트")}</td>
                      <td style={{ borderRight: "1px solid var(--border)", padding: "0 8px", textAlign: "center" }}>
                        <input type="checkbox" checked={lot.cache_enabled !== false}
                          onChange={e => { const v = [...priorityLots]; v[i] = { ...v[i], cache_enabled: e.target.checked }; setPriorityLots(v); }} />
                      </td>
                      <td style={{ padding: "0 6px", textAlign: "center" }}>
                        <button onClick={() => { const v = [...priorityLots]; v.splice(i, 1); setPriorityLots(v); }}
                          style={{ padding: "2px 6px", borderRadius: 4, border: "none", background: "transparent",
                            color: "rgba(239,68,68,0.8)", fontSize: 12, cursor: "pointer" }}>✕</button>
                      </td>
                    </tr>
                  );
                })}
                {priorityLots.length === 0 &&
                  <tr><td colSpan={7} style={{ padding: 14, textAlign: "center", color: "var(--text-secondary)", fontSize: 13 }}>
                    등록된 주요 lot이 없습니다 — + 추가 후 셀에 엑셀 범위를 붙여넣어도 됩니다</td></tr>}
              </tbody>
            </table>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <button onClick={() => setPriorityLots([...priorityLots, { lot_id: "", purpose: "", comment: "", cache_enabled: true }])}
              style={S_BTN}>+ 추가</button>
            <button onClick={() => savePriority(priorityLots)}
              style={{ ...S_BTN, border: "1px solid var(--accent)", background: "rgba(37,99,235,0.10)", color: "var(--accent)", fontWeight: 700 }}>저장</button>
            <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
              ● = RAM 캐시 적재됨 · 위치는 wafer 중 최신 tkout 기준 · 엑셀에서 여러 행/열을 복사해 셀에 붙여넣으면 표에 채워집니다
            </span>
          </div>
        </div>}

        {/* 전체 캐시 서브탭 */}
        {subTab === "contents" && <div style={{ display: "grid", gap: 8 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", padding: "6px 8px", borderRadius: 6,
            border: "1px solid var(--border)", background: "var(--bg-card)" }}>
            <span style={{ fontSize: 13, fontWeight: 700, whiteSpace: "nowrap" }}>이 제품 캐시 상한</span>
            <input type="number" min={0} max={50000} value={budgetDraft} onChange={e => setBudgetDraft(e.target.value)}
              placeholder={String(budgets?.default_max_roots || 1000)}
              style={{ ...S_INPUT, width: 80, fontFamily: "monospace" }} />
            <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>root lots (기본: {budgets?.default_max_roots || 1000})</span>
            <button onClick={saveBudget} disabled={budgetSaving}
              style={{ padding: "4px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "transparent",
                color: "var(--accent)", fontSize: 12, fontWeight: 700, cursor: budgetSaving ? "wait" : "pointer" }}>
              {budgetSaving ? "저장 중" : "저장"}
            </button>
          </div>
          {contentsLoading && <div style={{ fontSize: 13, color: "var(--text-secondary)", padding: 8 }}>로딩 중...</div>}
          {contents && !contentsLoading && <div style={{ maxHeight: 480, overflow: "auto", borderRadius: 6, border: "1px solid var(--border)" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, fontFamily: "monospace" }}>
              <thead>
                <tr style={{ background: "var(--bg-secondary)", position: "sticky", top: 0 }}>
                  <th style={{ padding: "4px 6px", textAlign: "left", borderBottom: "1px solid var(--border)" }}>root_lot_id</th>
                  <th style={{ padding: "4px 6px", textAlign: "right", borderBottom: "1px solid var(--border)" }}>행수</th>
                  <th style={{ padding: "4px 6px", textAlign: "right", borderBottom: "1px solid var(--border)" }}>MB</th>
                  <th style={{ padding: "4px 6px", textAlign: "right", borderBottom: "1px solid var(--border)" }}>조회</th>
                  <th style={{ padding: "4px 6px", textAlign: "center", borderBottom: "1px solid var(--border)" }}>그룹</th>
                  <th style={{ padding: "4px 6px", textAlign: "left", borderBottom: "1px solid var(--border)" }}>적재 시각</th>
                </tr>
              </thead>
              <tbody>
                {(contents.entries || []).map((e, i) => (
                  <tr key={i} style={{ borderBottom: "1px solid var(--border)", background: e.is_priority ? "rgba(37,99,235,0.05)" : "transparent" }}>
                    <td style={{ padding: "3px 6px" }}>{e.is_priority && <span style={{ color: "var(--accent)", marginRight: 4 }}>★</span>}{e.root_lot_id}</td>
                    <td style={{ padding: "3px 6px", textAlign: "right" }}>{e.row_count?.toLocaleString()}</td>
                    <td style={{ padding: "3px 6px", textAlign: "right" }}>{e.estimated_mb}</td>
                    <td style={{ padding: "3px 6px", textAlign: "right" }}>{e.access_count}</td>
                    <td style={{ padding: "3px 6px", textAlign: "center" }}>
                      <span style={{ padding: "1px 6px", borderRadius: 3, fontSize: 11,
                        background: e.cache_group === "priority" ? "rgba(37,99,235,0.15)" : e.cache_group === "step" ? "rgba(34,197,94,0.15)" : "rgba(156,163,175,0.15)",
                        color: e.cache_group === "priority" ? "var(--accent)" : e.cache_group === "step" ? "rgba(34,197,94,0.9)" : "var(--text-secondary)" }}>{e.cache_group}</span>
                    </td>
                    <td style={{ padding: "3px 6px", fontSize: 11, color: "var(--text-secondary)" }}>{e.loaded_at}</td>
                  </tr>
                ))}
                {(contents.entries || []).length === 0 &&
                  <tr><td colSpan={6} style={{ padding: 12, textAlign: "center", color: "var(--text-secondary)" }}>캐시된 root lot이 없습니다</td></tr>}
              </tbody>
            </table>
          </div>}
        </div>}
      </div>}
    </div>
  );
}
