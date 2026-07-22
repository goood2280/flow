import { useState, useEffect, useCallback } from "react";
import { toast } from "../components/Toast";
import { sf } from "../lib/api";
import { isAdmin } from "../lib/permissions";

// v9.3.x: RAM 캐시 관리 — SplitTable 톱니바퀴(설정 모달) 안에 있던 캐시 관리를
// 데이터 그룹의 독립 탭으로 승격. 제품별 분해(전 제품 현황)를 추가.
// v9.5.x: 톱니바퀴 고급 탭의 캐시 수동 스캔(FAB/제품 원본/Root lot)과
// Root lot RAM cache 설정, 쿼리 병렬 코어 수 조정을 이 탭으로 이동.
const API = "/api/splittable";


const S_INPUT = {
  padding: "4px 6px", borderRadius: 4, border: "1px solid var(--border)",
  background: "var(--bg-secondary)", color: "var(--text-primary)", fontSize: 13,
};
const S_BTN = {
  padding: "5px 10px", borderRadius: 6, border: "1px solid var(--border)",
  background: "transparent", color: "var(--text-primary)", fontSize: 13, cursor: "pointer",
};

export default function My_RamCache({ user }) {
  const canManage = isAdmin(user);
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
  const [budgetDraftDev, setBudgetDraftDev] = useState("");
  const [budgetSaving, setBudgetSaving] = useState(false);
  // 관리자 전용 — 캐시 수동 스캔 (SplitTable 톱니바퀴에서 이동)
  const [fabCacheBusy, setFabCacheBusy] = useState(false);
  const [productCacheStatus, setProductCacheStatus] = useState(null);
  const [productCacheBusy, setProductCacheBusy] = useState(false);
  const [rootLotCacheStatus, setRootLotCacheStatus] = useState(null);
  const [rootLotCacheBusy, setRootLotCacheBusy] = useState(false);
  const [queryWorkersStatus, setQueryWorkersStatus] = useState(null);
  const [queryWorkersDraft, setQueryWorkersDraft] = useState(3);
  const [queryWorkersSaveBusy, setQueryWorkersSaveBusy] = useState(false);
  // 관리자 전용 — 캐시 이벤트 로그 + Peak RAM
  const [cacheEventLog, setCacheEventLog] = useState(null);
  const [cacheEventLogFilter, setCacheEventLogFilter] = useState("");
  const [peakRam, setPeakRam] = useState(null);

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
        setBudgetDraftDev(pb ? String(pb.max_roots_dev ?? "") : "");
      })
      .catch(() => {});
  }, []);

  const loadQueryWorkers = useCallback(() => {
    sf(API + "/query-workers")
      .then(d => { setQueryWorkersStatus(d); setQueryWorkersDraft(d.effective || 3); })
      .catch(() => {});
  }, []);
  const loadCacheEventLog = useCallback((cat) => {
    const q = cat ? ("?category=" + encodeURIComponent(cat)) : "";
    sf(API + "/cache-event-log" + q)
      .then(d => { setCacheEventLog(d.events || []); setPeakRam(d.peak_ram || null); })
      .catch(() => { setCacheEventLog(null); setPeakRam(null); });
  }, []);
  const reloadProductCacheStatus = useCallback((prod) => {
    const q = prod ? ("?product=" + encodeURIComponent(prod)) : "";
    return sf(API + "/product-cache/status" + q)
      .then(d => setProductCacheStatus(d))
      .catch(() => setProductCacheStatus(null));
  }, []);
  const reloadRootLotCacheStatus = useCallback((prod) => {
    const q = prod ? ("?product=" + encodeURIComponent(prod)) : "";
    return sf(API + "/root-lot-cache/status" + q)
      .then(d => setRootLotCacheStatus(d))
      .catch(() => setRootLotCacheStatus(null));
  }, []);

  useEffect(() => { loadOverview(); }, [loadOverview]);
  useEffect(() => {
    if (!canManage) return;
    loadQueryWorkers();
    loadCacheEventLog("");
  }, [canManage, loadQueryWorkers, loadCacheEventLog]);
  useEffect(() => {
    if (!selProd) return;
    loadPriority(selProd);
    loadContents(selProd);
    loadBudgets(selProd);
    if (canManage) { reloadProductCacheStatus(selProd); reloadRootLotCacheStatus(selProd); }
  }, [selProd, canManage, loadPriority, loadContents, loadBudgets, reloadProductCacheStatus, reloadRootLotCacheStatus]);

  const runFabMatchCache = () => {
    setFabCacheBusy(true);
    sf(API + "/match-cache/refresh", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ product: selProd || "", force: true }) })
      .then(r => {
        const rows = r.products || [];
        if (r.queued) toast.info(`FAB 매칭 캐시 스캔 예약됨: ${rows.length}개 제품`);
        else if (r.running) toast.warn("FAB 매칭 캐시 스캔이 이미 실행 중입니다.");
        else toast.ok(`FAB 매칭 캐시 스캔 완료: ${rows.filter(x => x.ok).length}/${rows.length}`);
      })
      .catch(e => toast.error("FAB 매칭 캐시 스캔 실패: " + (e?.message || e)))
      .finally(() => setFabCacheBusy(false));
  };
  const runProductRamCache = () => {
    setProductCacheBusy(true);
    sf(API + "/product-cache/refresh", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ product: selProd || "", force: true }) })
      .then(r => {
        const rows = r.products || [];
        if (r.queued) toast.info(`제품 원본 RAM cache 갱신 예약됨: ${rows.length}개 제품`);
        else if (r.running) toast.warn("제품 원본 RAM cache 갱신이 이미 실행 중입니다.");
        else toast.ok(`제품 원본 RAM cache 갱신 완료: ${rows.filter(x => x.ok).length}/${rows.length}`);
        reloadProductCacheStatus(selProd);
      })
      .catch(e => toast.error("제품 원본 RAM cache 갱신 실패: " + (e?.message || e)))
      .finally(() => setProductCacheBusy(false));
  };
  const runRootLotRamCache = () => {
    setRootLotCacheBusy(true);
    sf(API + "/root-lot-cache/refresh", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ product: selProd || "", force: true }) })
      .then(r => {
        const rows = r.products || [];
        toast.ok(`Root lot RAM cache 갱신 완료: ${rows.filter(x => x.ok).length}/${rows.length}`);
        reloadRootLotCacheStatus(selProd);
        reloadProductCacheStatus(selProd);
        loadOverview();
        if (selProd) loadContents(selProd);
      })
      .catch(e => toast.error("Root lot RAM cache 갱신 실패: " + (e?.message || e)))
      .finally(() => setRootLotCacheBusy(false));
  };
  const saveQueryWorkers = () => {
    setQueryWorkersSaveBusy(true);
    sf(API + "/query-workers/save", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query_workers: queryWorkersDraft }) })
      .then(d => { setQueryWorkersStatus(d); setQueryWorkersDraft(d.configured || 0); toast.ok("쿼리 워커 수 저장됨 (effective: " + d.effective + ")"); })
      .catch(e => toast.error("쿼리 워커 수 저장 실패: " + (e?.message || e)))
      .finally(() => setQueryWorkersSaveBusy(false));
  };

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
    const payload = { product: selProd, max_roots: Number(budgetDraft) || 1000 };
    if (budgetDraftDev !== "") payload.max_roots_dev = Number(budgetDraftDev) || 200;
    sf(API + "/ram-cache/product-budgets/save", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
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
    if (canManage) {
      loadQueryWorkers();
      reloadProductCacheStatus(selProd); reloadRootLotCacheStatus(selProd);
      loadCacheEventLog(cacheEventLogFilter);
    }
  };

  const usagePct = overview?.max_mb ? Math.min(100, overview.total_mb / overview.max_mb * 100) : 0;

  return (
    <div style={{ padding: 20, maxWidth: 1100, margin: "0 auto" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", marginBottom: 14 }}>
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

      {/* 관리자 — 캐시 수동 스캔/설정 (SplitTable 톱니바퀴에서 이동) */}
      {canManage && <div style={{ display: "grid", gap: 10, padding: "10px 12px", borderRadius: 8,
        border: "1px solid var(--border)", background: "var(--bg-card)", marginBottom: 16 }}>
        <div style={{ fontSize: 14, fontWeight: 800 }}>관리자 · 캐시 수동 스캔 / 설정</div>

        {/* FAB root/fab_lot 매칭 캐시 스캔 */}
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <button onClick={runFabMatchCache} disabled={fabCacheBusy}
            style={{ ...S_BTN, border: "1px solid var(--accent)", background: "var(--accent-glow)", color: "var(--accent)",
              fontWeight: 700, borderRadius: 999, cursor: fabCacheBusy ? "wait" : "pointer", opacity: fabCacheBusy ? 0.65 : 1 }}>
            {fabCacheBusy ? "FAB 캐시 스캔 중..." : "FAB root/fab_lot 캐시 수동 스캔"}
          </button>
          <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
            {selProd ? `선택 제품(${selProd}) 기준` : "제품 미선택 시 전체 표시 제품을 스캔합니다"}
          </span>
        </div>

        {/* 제품 원본 RAM cache 수동 갱신 */}
        {(() => {
          const pc = (productCacheStatus?.products || [])[0] || {};
          const hit = !!pc.hit; const stale = !!pc.stale;
          const refreshing = !!pc.refreshing || !!productCacheStatus?.job?.running;
          const tone = stale ? "rgba(245,158,11,0.95)" : hit ? "rgba(37,99,235,0.95)" : "var(--text-secondary)";
          return (
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <button onClick={runProductRamCache} disabled={productCacheBusy}
                style={{ ...S_BTN, border: "1px solid rgba(37,99,235,0.8)", background: "rgba(37,99,235,0.10)",
                  color: "rgba(37,99,235,0.95)", fontWeight: 700, borderRadius: 999,
                  cursor: productCacheBusy ? "wait" : "pointer", opacity: productCacheBusy ? 0.65 : 1 }}>
                {productCacheBusy ? "제품 원본 RAM cache 갱신 중..." : "제품 원본 RAM cache 수동 갱신"}
              </button>
              <span style={{ fontSize: 13, color: tone, fontWeight: 700 }}>
                {refreshing ? "refreshing" : stale ? "stale" : hit ? "hit" : "miss"}
              </span>
              <span style={{ fontSize: 12, color: "var(--text-secondary)", fontFamily: "monospace" }}>
                rows {pc.row_count || 0} · {Number(pc.estimated_mb || 0).toFixed(1)} MB · {pc.loaded_at || "not loaded"}
              </span>
            </div>);
        })()}

        {/* Root lot RAM cache 수동 갱신 */}
        {(() => {
          const rc = rootLotCacheStatus?.cache || {};
          return (
            <div style={{ display: "grid", gap: 8, padding: "8px 10px", borderRadius: 8,
              border: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <div style={{ fontSize: 13, fontWeight: 800 }}>Root lot RAM cache</div>
                <span style={{ fontSize: 12, color: "var(--text-secondary)", fontFamily: "monospace" }}>
                  cached {rc.hit_roots || 0} roots (step {rc.step_hit_roots || 0} / other {rc.other_hit_roots || 0}) · {Number(rc.estimated_mb || 0).toFixed(1)} MB / {rc.max_gb || 0} GB · CPU {Number(rc.cpu_budget_cores || 0).toFixed(1)} cores
                </span>
              </div>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <button onClick={runRootLotRamCache} disabled={rootLotCacheBusy}
                  style={{ ...S_BTN, border: "1px solid rgba(37,99,235,0.8)", background: "rgba(37,99,235,0.10)",
                    color: "rgba(37,99,235,0.95)", fontWeight: 700, borderRadius: 999,
                    cursor: rootLotCacheBusy ? "wait" : "pointer", whiteSpace: "nowrap" }}>
                  {rootLotCacheBusy ? "갱신 중..." : selProd ? `Root cache 갱신 (${selProd})` : "Root cache 전체 갱신"}
                </button>
                <button onClick={() => reloadRootLotCacheStatus(selProd)}
                  style={{ ...S_BTN, fontWeight: 700, whiteSpace: "nowrap" }}>
                  상태 새로고침
                </button>
              </div>
            </div>);
        })()}

        {/* 쿼리 병렬 코어 수 */}
        <div style={{ display: "grid", gap: 8, padding: "8px 10px", borderRadius: 8,
          border: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <div style={{ fontSize: 13, fontWeight: 800 }}>쿼리 병렬 코어 수</div>
            {queryWorkersStatus && <span style={{ fontSize: 12, color: "var(--text-secondary)", fontFamily: "monospace" }}>
              현재 {queryWorkersStatus.effective}코어 · CPU {queryWorkersStatus.cpu_count}코어
            </span>}
          </div>
          <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.5 }}>
            SplitTable 조회 시 사용할 CPU 코어 수. 숫자가 높으면 단일 조회는 빠르지만, 동시 사용자가 많으면 서버가 느려집니다. 기본 3코어 권장.
            {queryWorkersStatus?.essential_concurrency && <span> (동시 조회 상한: {queryWorkersStatus.essential_concurrency}건)</span>}
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <select value={queryWorkersDraft} onChange={e => setQueryWorkersDraft(Number(e.target.value))}
              style={{ ...S_INPUT, fontFamily: "monospace", cursor: "pointer" }}>
              {[1, 2, 3, 4].filter(n => n <= (queryWorkersStatus?.cpu_count || 4)).map(n => (
                <option key={n} value={n}>{n}코어{n === 3 ? " (권장)" : n === 1 ? " (절약)" : ""}</option>
              ))}
            </select>
            <button onClick={saveQueryWorkers} disabled={queryWorkersSaveBusy}
              style={{ ...S_BTN, fontWeight: 700, cursor: queryWorkersSaveBusy ? "wait" : "pointer", whiteSpace: "nowrap" }}>
              {queryWorkersSaveBusy ? "저장 중" : "저장"}
            </button>
            <button onClick={loadQueryWorkers}
              style={{ ...S_BTN, border: "1px solid rgba(37,99,235,0.8)", background: "rgba(37,99,235,0.10)",
                color: "rgba(37,99,235,0.95)", fontWeight: 700, whiteSpace: "nowrap" }}>
              새로고침
            </button>
          </div>
        </div>
      </div>}

      {/* 관리자 — Peak RAM 사용량 + 캐시 이벤트 로그 */}
      {canManage && <div style={{ display: "grid", gap: 10, padding: "10px 12px", borderRadius: 8,
        border: "1px solid var(--border)", background: "var(--bg-card)", marginBottom: 16 }}>
        <div style={{ fontSize: 14, fontWeight: 800 }}>관리자 · Peak RAM & 캐시 이벤트 로그</div>

        {/* Peak RAM 표시 */}
        {peakRam && <div style={{ display: "grid", gap: 4, padding: "8px 10px", borderRadius: 8,
          border: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
          <div style={{ fontSize: 13, fontWeight: 800 }}>Peak RAM 사용량</div>
          <div style={{ display: "flex", gap: 16, flexWrap: "wrap", fontSize: 13, fontFamily: "monospace" }}>
            <span>현재 RSS: <b style={{ color: "var(--accent)" }}>{peakRam.rss_gb?.toFixed(2) || "?"} GB</b></span>
            <span>Peak RSS: <b style={{ color: (peakRam.peak_rss_gb || 0) > (peakRam.limit_gb || 999) * 0.85
              ? "rgba(239,68,68,0.9)" : "var(--accent)" }}>{peakRam.peak_rss_gb?.toFixed(2) || "?"} GB</b></span>
            <span>Effective: <b>{peakRam.effective_gb?.toFixed(2) || "?"} GB</b></span>
            <span>Limit: <b>{peakRam.limit_gb?.toFixed(2) || "?"} GB</b></span>
            <span>System: <b>{peakRam.system_total_gb?.toFixed(1) || "?"} GB</b>
              {peakRam.system_available_gb != null && <span style={{ color: "var(--text-secondary)" }}>
                {" "}(avail {peakRam.system_available_gb.toFixed(1)})</span>}
            </span>
          </div>
          {peakRam.watchdog && <div style={{ display: "flex", gap: 16, flexWrap: "wrap", fontSize: 12,
            color: "var(--text-secondary)", fontFamily: "monospace" }}>
            <span>Watchdog warn: {peakRam.watchdog.warn_pct}%</span>
            <span>critical: {peakRam.watchdog.critical_pct}%</span>
            <span>safe: {peakRam.watchdog.safe_pct}%</span>
          </div>}
        </div>}

        {/* 캐시 이벤트 로그 */}
        <div style={{ display: "grid", gap: 6 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <span style={{ fontSize: 13, fontWeight: 800 }}>캐시 이벤트 로그</span>
            <select value={cacheEventLogFilter} onChange={e => { setCacheEventLogFilter(e.target.value); loadCacheEventLog(e.target.value); }}
              style={{ ...S_INPUT, fontSize: 12, cursor: "pointer" }}>
              <option value="">전체</option>
              <option value="warmup">예열</option>
              <option value="eviction">축출</option>
              <option value="watchdog">워치독</option>
              <option value="cache_op">캐시 작업</option>
            </select>
            <button onClick={() => loadCacheEventLog(cacheEventLogFilter)}
              style={{ ...S_BTN, fontSize: 12, padding: "3px 8px" }}>새로고침</button>
          </div>
          <div style={{ maxHeight: 320, overflow: "auto", borderRadius: 6, border: "1px solid var(--border)" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, fontFamily: "monospace" }}>
              <thead>
                <tr style={{ background: "var(--bg-secondary)", position: "sticky", top: 0 }}>
                  <th style={{ padding: "4px 6px", textAlign: "left", borderBottom: "1px solid var(--border)", whiteSpace: "nowrap" }}>시간</th>
                  <th style={{ padding: "4px 6px", textAlign: "center", borderBottom: "1px solid var(--border)", whiteSpace: "nowrap" }}>분류</th>
                  <th style={{ padding: "4px 6px", textAlign: "center", borderBottom: "1px solid var(--border)", whiteSpace: "nowrap" }}>상태</th>
                  <th style={{ padding: "4px 6px", textAlign: "left", borderBottom: "1px solid var(--border)" }}>이벤트</th>
                </tr>
              </thead>
              <tbody>
                {(cacheEventLog || []).map((ev, i) => (
                  <tr key={i} style={{ borderBottom: "1px solid var(--border)",
                    background: !ev.ok ? "rgba(239,68,68,0.04)" : ev.category === "eviction" ? "rgba(245,158,11,0.04)" : "transparent" }}>
                    <td style={{ padding: "3px 6px", whiteSpace: "nowrap", color: "var(--text-secondary)" }}>
                      {ev.ts_iso ? ev.ts_iso.replace("T", " ").slice(0, 19) : "-"}
                    </td>
                    <td style={{ padding: "3px 6px", textAlign: "center" }}>
                      <span style={{ padding: "1px 5px", borderRadius: 3, fontSize: 11,
                        background: ev.category === "warmup" ? "rgba(37,99,235,0.12)" :
                                    ev.category === "eviction" ? "rgba(245,158,11,0.12)" :
                                    ev.category === "watchdog" ? "rgba(239,68,68,0.12)" : "rgba(156,163,175,0.12)",
                        color: ev.category === "warmup" ? "rgba(37,99,235,0.95)" :
                               ev.category === "eviction" ? "rgba(245,158,11,0.95)" :
                               ev.category === "watchdog" ? "rgba(239,68,68,0.9)" : "var(--text-secondary)" }}>
                        {ev.category}
                      </span>
                    </td>
                    <td style={{ padding: "3px 6px", textAlign: "center",
                      color: ev.ok ? "rgba(34,197,94,0.9)" : "rgba(239,68,68,0.9)", fontWeight: 700 }}>
                      {ev.ok ? "OK" : "FAIL"}
                    </td>
                    <td style={{ padding: "3px 6px", fontFamily: "inherit", fontSize: 12, wordBreak: "break-all" }}>
                      {ev.event}
                      {ev.product && <span style={{ marginLeft: 6, color: "var(--text-secondary)" }}>({ev.product})</span>}
                    </td>
                  </tr>
                ))}
                {(!cacheEventLog || cacheEventLog.length === 0) &&
                  <tr><td colSpan={4} style={{ padding: 14, textAlign: "center", color: "var(--text-secondary)" }}>
                    이벤트 로그가 없습니다</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      </div>}

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
            border: "1px solid var(--border)", background: "var(--bg-card)", flexWrap: "wrap" }}>
            <span style={{ fontSize: 13, fontWeight: 700, whiteSpace: "nowrap" }}>이 제품 캐시 상한</span>
            <span style={{ fontSize: 12, color: "var(--text-secondary)", whiteSpace: "nowrap" }}>운영</span>
            <input type="number" min={0} max={50000} value={budgetDraft} onChange={e => setBudgetDraft(e.target.value)}
              placeholder={String(budgets?.default_max_roots || 1000)}
              style={{ ...S_INPUT, width: 80, fontFamily: "monospace" }} />
            <span style={{ fontSize: 12, color: "var(--text-secondary)", whiteSpace: "nowrap" }}>개발</span>
            <input type="number" min={0} max={50000} value={budgetDraftDev} onChange={e => setBudgetDraftDev(e.target.value)}
              placeholder={String(budgets?.default_max_roots_dev || 200)}
              style={{ ...S_INPUT, width: 80, fontFamily: "monospace" }} />
            <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>root lots</span>
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
