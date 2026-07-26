import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { toast } from "../components/Toast";
import { postJson, qs, sf } from "../lib/api";
import { isAdmin } from "../lib/permissions";
import { Filter, TabStrip } from "../components/UXKit";

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

const JOB_TONE = {
  running: "var(--info)", queued: "var(--warn)",
  done: "var(--ok)", failed: "var(--danger)", skipped: "var(--text-secondary)",
};
const SCAN_STAGE_LABEL = {
  lookup_build: "랏(lookup) 캐시 빌드",
  match_cache: "FAB 매칭 캐시",
  product_ram: "제품 원본 RAM 캐시",
  root_lot_ram: "Root lot lookup/RAM 캐시",
};
// 작업/단계 상태를 한국어로 — 화면에서 'done/failed' 대신 '완료/실패'로 읽히게.
const JOB_STATUS_KO = {
  running: "진행 중", queued: "대기", done: "완료", failed: "실패", skipped: "건너뜀",
};
const STAGE_MARK = { done: "✓", running: "●", failed: "✕", skipped: "—", queued: "○" };

function fmtDur(sec) {
  const s = Math.max(0, Math.round(Number(sec) || 0));
  if (s < 60) return `${s}초`;
  if (s < 3600) return `${Math.floor(s / 60)}분 ${s % 60}초`;
  return `${Math.floor(s / 3600)}시간 ${Math.floor((s % 3600) / 60)}분`;
}

// 캐시 이벤트 시간은 백엔드가 UTC(ts epoch / ts_iso)로 기록한다. 화면에는 항상
// 한국시간(Asia/Seoul)으로 표시한다. epoch(ts) 우선, 없으면 ts_iso 파싱.
function fmtKst(ev) {
  const ms = ev?.ts ? ev.ts * 1000 : (ev?.ts_iso ? Date.parse(ev.ts_iso) : NaN);
  if (!ms || Number.isNaN(ms)) {
    return ev?.ts_iso ? ev.ts_iso.replace("T", " ").slice(0, 19) : "-";
  }
  try {
    return new Date(ms).toLocaleString("sv-SE", {
      timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
    });
  } catch {
    return ev?.ts_iso ? ev.ts_iso.replace("T", " ").slice(0, 19) : "-";
  }
}

function scanStageLabel(event) {
  const stage = event?.detail?.stage;
  if (!stage) return "-";
  const label = SCAN_STAGE_LABEL[stage] || stage;
  const phase = event?.detail?.phase;
  const phaseLabel = phase === "started" ? "시작" : phase === "finished" ? "완료" : phase === "failed" ? "실패" : "";
  return phaseLabel ? `${label} · ${phaseLabel}` : label;
}

function CacheJobPanel({ jobs, queues }) {
  const visible = (jobs || []).slice(0, 3);
  const worker = queues?.worker || {};
  const lookup = queues?.lookup_build || {};
  const rootPrefetch = queues?.root_prefetch || {};
  const externalQueued = Number(worker.depth || 0) + (lookup.queued || []).length
    + Number(rootPrefetch.depth || 0)
    + Number(queues?.match_cache?.queued || 0) + Number(queues?.product_ram?.queued || 0);
  if (!visible.length && !externalQueued && !(worker.running || []).length && !lookup.running) return null;
  return <div style={{ display: "grid", gap: 8, padding: "8px 10px", borderRadius: 7,
    border: "1px solid var(--border)", background: "var(--bg-card)" }}>
    <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
      <span style={{ fontSize: 13, fontWeight: 800 }}>캐시 작업 파이프라인</span>
      <span style={{ fontSize: 11, color: "var(--text-secondary)", fontFamily: "monospace" }}>
        실행 {visible.filter(j => j.status === "running").length} · 외부 큐 {externalQueued}
      </span>
    </div>
    {visible.map(job => {
      const failedStages = (job.stages || []).filter(s => s.status === "failed");
      // 진행 신호가 한동안 없으면 '응답 없음'을 명시한다 — 무한 로딩처럼 보이지 않게.
      const idleWarn = job.status === "running" && Number(job.idle_sec || 0) > 120;
      return <div key={job.id} style={{ display: "grid", gap: 6, padding: "7px 8px",
        borderRadius: 6, border: `1px solid ${job.status === "failed" ? "var(--danger-line)" : "var(--border)"}`,
        background: job.status === "failed" ? "rgba(239,68,68,0.05)" : "var(--bg-secondary)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap", fontSize: 12 }}>
          <b>{job.label}</b>
          <span style={{ color: JOB_TONE[job.status] || "var(--text-secondary)", fontFamily: "monospace", fontWeight: 700 }}>
            {JOB_STATUS_KO[job.status] || job.status}
            {job.elapsed_sec != null && <span style={{ color: "var(--text-secondary)", fontWeight: 400 }}>
              {" · "}{fmtDur(job.elapsed_sec)} 경과</span>}
          </span>
        </div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {(job.stages || []).map(stage => <span key={stage.id} title={stage.detail?.error || ""}
            style={{ fontSize: 11, padding: "2px 7px", borderRadius: 999,
              border: `1px solid ${JOB_TONE[stage.status] || "var(--border)"}`,
              color: JOB_TONE[stage.status] || "var(--text-secondary)" }}>
            {STAGE_MARK[stage.status] || "○"} {stage.label} · {JOB_STATUS_KO[stage.status] || stage.status}
          </span>)}
        </div>
        {failedStages.length > 0 && <div style={{ fontSize: 11, color: "var(--danger)", lineHeight: 1.5 }}>
          {failedStages.map(s => <div key={s.id}>✕ {s.label} 실패{s.detail?.error ? ` — ${s.detail.error}` : ""}</div>)}
        </div>}
        {idleWarn && <div style={{ fontSize: 11, color: "var(--warn)" }}>
          ⚠ {fmtDur(job.idle_sec)} 동안 새 진행 로그가 없습니다
          {job.stale_after_sec ? ` — ${fmtDur(job.stale_after_sec)}까지 진행이 없으면 자동으로 실패 처리합니다` : ""}
        </div>}
        <div style={{ display: "flex", gap: 14, flexWrap: "wrap", fontSize: 11,
          color: "var(--text-secondary)", fontFamily: "monospace" }}>
          {job.last_event && <span style={{ color: "var(--text-primary)" }}>최근: {job.last_event}</span>}
          <span>RSS {Number(job.current_rss_gb || 0).toFixed(2)}GB</span>
          <span>API 작업 Peak {Number(job.peak_effective_gb || 0).toFixed(2)}GB</span>
          <span>Peak 증가 +{Number(job.peak_delta_gb || 0).toFixed(2)}GB</span>
          <span>최저 여유 {Number(job.min_system_available_gb || 0).toFixed(2)}GB</span>
        </div>
      </div>;
    })}
    {(externalQueued > 0 || (worker.running || []).length > 0 || lookup.running) &&
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", fontSize: 11,
        color: "var(--text-secondary)", fontFamily: "monospace" }}>
        <span>개발 워커: 실행 {(worker.running || []).length} / 대기 {worker.depth || 0}</span>
        <span>worker RAM {Number(worker.load?.mem_effective_gb || 0).toFixed(2)}GB / 프로세스 peak RSS {Number(worker.load?.peak_rss_gb || 0).toFixed(2)}GB</span>
        <span>lookup: {lookup.current || "-"} / 대기 {(lookup.queued || []).length}</span>
        <span>Root 유휴 예열: {rootPrefetch.current_root || "-"} / 대기 {rootPrefetch.depth || 0}</span>
        <span>FAB 대기 {queues?.match_cache?.queued || 0}</span>
        <span>제품 RAM 대기 {queues?.product_ram?.queued || 0}</span>
        {worker.overloaded_reason && <span style={{ color: "var(--danger)" }}>worker guard: {worker.overloaded_reason}</span>}
      </div>}
  </div>;
}

export default function My_RamCache({ user }) {
  // 이 페이지의 관리 기능(수동 스캔·예산·이벤트 로그)은 백엔드가 전부
  // splittable page manager 권한으로 막고 있다. 프런트도 같은 판정을 써야
  // "보이는데 403" 이 안 난다 — 판정값은 overview 응답의 can_manage 가 정본이고,
  // 응답 전까지는 역할만으로 낙관 판단(admin) 한다.
  const [canManage, setCanManage] = useState(() => isAdmin(user));
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
  // 관리자 전용 — 캐시 수동 스캔 (통합)
  const [unifiedScanBusy, setUnifiedScanBusy] = useState(false);
  const [productCacheStatus, setProductCacheStatus] = useState(null);
  const [rootLotCacheStatus, setRootLotCacheStatus] = useState(null);
  const [queryWorkersStatus, setQueryWorkersStatus] = useState(null);
  const [queryWorkersDraft, setQueryWorkersDraft] = useState(3);
  const [queryWorkersSaveBusy, setQueryWorkersSaveBusy] = useState(false);
  // 관리자 전용 — 캐시 이벤트 로그 + Peak RAM
  const [cacheEventLog, setCacheEventLog] = useState(null);
  const [cacheEventLogFilter, setCacheEventLogFilter] = useState("");
  const [peakRam, setPeakRam] = useState(null);
  const [cacheJobs, setCacheJobs] = useState([]);
  const [cacheQueues, setCacheQueues] = useState(null);
  // 관리자 전용 — 캐시 예산 조절 톱니바퀴
  const [budgetModalOpen, setBudgetModalOpen] = useState(false);
  const [budgetCfg, setBudgetCfg] = useState(null);
  const [budgetForm, setBudgetForm] = useState({});
  const [budgetCfgSaving, setBudgetCfgSaving] = useState(false);

  const loadBudgetCfg = useCallback(() => {
    sf(API + "/cache-budget/settings")
      .then(d => {
        setBudgetCfg(d);
        const s = d.saved || {};
        setBudgetForm({
          pool_fraction: s.pool_fraction ?? "",
          pool_fraction_dev: s.pool_fraction_dev ?? "",
          dev_factor: s.dev_factor ?? "",
          root_ram_gb: s.root_ram_gb ?? "",
          root_ram_gb_dev: s.root_ram_gb_dev ?? "",
          product_ram_enabled: s.product_ram_enabled ?? true,
          product_ram_enabled_dev: s.product_ram_enabled_dev ?? (s.product_ram_enabled ?? true),
          product_ram_gb: s.product_ram_gb ?? "",
          product_ram_gb_dev: s.product_ram_gb_dev ?? "",
          match_cache_batch_roots: s.match_cache_batch_roots ?? "",
          match_cache_batch_roots_dev: s.match_cache_batch_roots_dev ?? "",
          view_cold_concurrency: s.view_cold_concurrency ?? "",
          view_cold_concurrency_dev: s.view_cold_concurrency_dev ?? "",
        });
      })
      .catch(e => toast.error("예산 설정 로드 실패: " + (e?.message || e)));
  }, []);

  const openBudgetModal = () => { setBudgetModalOpen(true); loadBudgetCfg(); };

  const saveBudgetCfg = () => {
    setBudgetCfgSaving(true);
    const num = v => (v === "" || v === null || v === undefined ? null : Number(v));
    const payload = {
      pool_fraction: num(budgetForm.pool_fraction),
      pool_fraction_dev: num(budgetForm.pool_fraction_dev),
      dev_factor: num(budgetForm.dev_factor),
      root_ram_gb: num(budgetForm.root_ram_gb),
      root_ram_gb_dev: num(budgetForm.root_ram_gb_dev),
      product_ram_gb: num(budgetForm.product_ram_gb),
      product_ram_gb_dev: num(budgetForm.product_ram_gb_dev),
      product_ram_enabled: !!budgetForm.product_ram_enabled,
      product_ram_enabled_dev: !!budgetForm.product_ram_enabled_dev,
      match_cache_batch_roots: num(budgetForm.match_cache_batch_roots),
      match_cache_batch_roots_dev: num(budgetForm.match_cache_batch_roots_dev),
      view_cold_concurrency: num(budgetForm.view_cold_concurrency),
      view_cold_concurrency_dev: num(budgetForm.view_cold_concurrency_dev),
    };
    postJson(API + "/cache-budget/settings/save", payload)
      .then(d => { setBudgetCfg(d); toast.ok("캐시 예산 설정 저장됨"); loadOverview(); })
      .catch(e => toast.error("저장 실패: " + (e?.message || e)))
      .finally(() => setBudgetCfgSaving(false));
  };

  const loadOverview = useCallback(() => {
    setOverviewLoading(true);
    sf(API + "/ram-cache/overview")
      .then(d => {
        setOverview(d);
        if (typeof d.can_manage === "boolean") setCanManage(d.can_manage);
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
    sf(API + "/ram-cache/priority-lots" + qs({ product: prod }))
      .then(d => setPriorityLots(d.lots || []))
      .catch(() => setPriorityLots([]));
    sf(API + "/ram-cache/lot-status" + qs({ product: prod }))
      .then(d => { setLotStatuses(d.statuses || {}); setLatestMainStep(d.latest_main_step || null); setLotStatusSkipped(d.skipped_reason || ""); })
      .catch(() => { setLotStatuses({}); setLatestMainStep(null); setLotStatusSkipped(""); });
  }, []);

  const loadContents = useCallback((prod) => {
    if (!prod) return;
    setContentsLoading(true);
    sf(API + "/ram-cache/contents" + qs({ product: prod }))
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
    sf(API + "/cache-event-log" + qs({ category: cat }))
      .then(d => {
        setCacheEventLog(d.events || []); setPeakRam(d.peak_ram || null);
        setCacheJobs(d.jobs || []); setCacheQueues(d.queues || null);
        setUnifiedScanBusy((d.jobs || []).some(job => job.status === "running"));
      })
      .catch(() => { setCacheEventLog(null); setPeakRam(null); });
  }, []);
  const reloadProductCacheStatus = useCallback((prod) => {
    return sf(API + "/product-cache/status" + qs({ product: prod }))
      .then(d => setProductCacheStatus(d))
      .catch(() => setProductCacheStatus(null));
  }, []);
  const reloadRootLotCacheStatus = useCallback((prod) => {
    return sf(API + "/root-lot-cache/status" + qs({ product: prod }))
      .then(d => setRootLotCacheStatus(d))
      .catch(() => setRootLotCacheStatus(null));
  }, []);

  useEffect(() => { loadOverview(); }, [loadOverview]);
  useEffect(() => {
    if (!canManage) return;
    loadQueryWorkers();
    loadCacheEventLog("");
  }, [canManage, loadQueryWorkers, loadCacheEventLog]);
  // 캐시 작업 진행 폴링 — 수동 스캔뿐 아니라 **예약/자동 캐싱**도 같은 화면에
  // 실시간으로 보이게 한다. 실행 중인 작업이 있으면 2.5초, 없으면 15초 간격.
  // (수동 스캔 중에는 startScanAndPoll 의 인터벌이 담당하므로 중복 폴링 생략)
  useEffect(() => {
    if (!canManage || unifiedScanBusy) return;
    const busyJob = (cacheJobs || []).some(job => job.status === "running");
    const timer = setTimeout(() => loadCacheEventLog(cacheEventLogFilter), busyJob ? 2500 : 15000);
    return () => clearTimeout(timer);
  }, [canManage, unifiedScanBusy, cacheJobs, cacheEventLogFilter, loadCacheEventLog]);
  useEffect(() => {
    if (!selProd) return;
    loadPriority(selProd);
    loadContents(selProd);
    loadBudgets(selProd);
    if (canManage) {
      // 관리자 상태 조회를 약간 지연해 동시 요청 폭주(429) 방지
      const t = setTimeout(() => {
        reloadProductCacheStatus(selProd);
        reloadRootLotCacheStatus(selProd);
      }, 300);
      return () => clearTimeout(t);
    }
  }, [selProd, canManage, loadPriority, loadContents, loadBudgets, reloadProductCacheStatus, reloadRootLotCacheStatus]);

  // 통합 스캔/전체 셋업 공용 — 시작 요청 후 진행 로그를 실시간 폴링한다.
  // 탭 이탈(언마운트) 시 인터벌을 반드시 정리 — 이전엔 최대 1시간 동안 유령 폴링이 남았다.
  const scanPollRef = useRef(null);
  useEffect(() => () => { if (scanPollRef.current) clearInterval(scanPollRef.current); }, []);
  const startScanAndPoll = (reqPromise, okMsg, maxTicks = 240) => {
    setUnifiedScanBusy(true);
    reqPromise
      .then(r => {
        if (r.queued) toast.ok(okMsg);
        else if (r.running) toast.warn("스캔이 이미 실행 중입니다.");
        else toast.ok("요청 완료");
        // 진행 로그 실시간 폴링 — 스캔이 끝날 때까지(scan-status.running=false) 이벤트
        // 로그를 2.5초마다 갱신한다. 예전엔 3/8/15초 후 한 번씩만 갱신해 오래 걸리는
        // 적재의 진행 로그가 뜨지 않았다. 최대 10분(240틱) 상한.
        // 이전에 예열/축출 필터를 선택했어도, 이번 수동 스캔의 단계·적재 이력이
        // 모두 보이도록 '전체' 로 전환한다(단계 마커=scan, 적재 진행=cache_op,
        // 예열 요약=warmup 을 한 로그에서 시간순으로 본다).
        const scanLogFilter = "";
        setCacheEventLogFilter(scanLogFilter);
        loadCacheEventLog(scanLogFilter);
        let ticks = 0;
        let errStreak = 0;
        const MAX_TICKS = maxTicks;
        const MAX_ERR_STREAK = 5;   // 상태 조회가 연속 실패하면 조용히 도는 대신 중단
        const finishPolling = () => {
          clearInterval(poll);
          scanPollRef.current = null;
          setUnifiedScanBusy(false);
          loadCacheEventLog(scanLogFilter);
          reloadProductCacheStatus(selProd);
          reloadRootLotCacheStatus(selProd);
          loadOverview();
          if (selProd) loadContents(selProd);
        };
        const poll = setInterval(() => {
          ticks += 1;
          loadCacheEventLog(scanLogFilter);
          if (ticks % 2 === 0) {   // 5초마다 현황/상태도 갱신
            reloadProductCacheStatus(selProd);
            reloadRootLotCacheStatus(selProd);
            loadOverview();
            if (selProd) loadContents(selProd);
          }
          sf(API + "/ram-cache/scan-status")
            .then(s => {
              errStreak = 0;
              setCacheJobs(s.jobs || []); setCacheQueues(s.queues || null);
              if (!s.running) {
                // 끝났으면 성공/실패를 반드시 알린다 — 예전엔 조용히 멈춰
                // 실패해도 화면상 '그냥 끝난' 것처럼 보였다.
                const failed = (s.last_stages || []).filter(st => st.status === "failed");
                if (s.last_status === "failed" || failed.length) {
                  toast.error("캐시 작업 실패 — "
                    + (failed.length
                      ? failed.map(st => `${st.label}${st.error ? `: ${st.error}` : ""}`).join(" / ")
                      : "자세한 사유는 아래 캐시 이벤트 로그를 확인하세요"));
                } else {
                  toast.ok("캐시 작업 완료");
                }
                finishPolling();
                return;
              }
              if (ticks >= MAX_TICKS) {
                // 서버 작업은 계속 진행 중 — 화면 폴링만 멈춘다(무한 폴링 방지).
                toast.warn("작업이 아직 진행 중입니다 — 자동 갱신을 멈춥니다. "
                  + "'새로고침'으로 진행 상황을 계속 확인할 수 있습니다.");
                finishPolling();
              }
            })
            .catch(e => {
              errStreak += 1;
              if (errStreak >= MAX_ERR_STREAK || ticks >= MAX_TICKS) {
                toast.error("진행 상태를 확인할 수 없습니다 (" + (e?.message || e) + ") — 자동 갱신을 중단합니다.");
                finishPolling();
              }
            });
        }, 2500);
        scanPollRef.current = poll;
      })
      .catch(e => { toast.error("스캔 시작 실패: " + (e?.message || e)); setUnifiedScanBusy(false); });
  };

  const runUnifiedScan = () => startScanAndPoll(
    postJson(API + "/ram-cache/unified-scan", { product: selProd || "", force: true }),
    `통합 캐시 스캔 시작됨 (${selProd || "전체 제품"}) — FAB/product/root lot 순서대로 갱신됩니다.`,
  );

  const runFullSetup = () => {
    if (!window.confirm(
      "전체 셋업(초기 1회)을 시작합니다.\n\n" +
      "· 개발 워커로 넘기지 않고 운영 서버에서 직접 처리\n" +
      "· 5병렬 · 최대 20GB 로 전 제품 캐시(랏→매칭→제품RAM→예열)를 빠르게 빌드\n" +
      "· 제품 수가 많으면 오래 걸릴 수 있고 운영 서버 자원을 많이 사용합니다.\n\n계속할까요?"
    )) return;
    startScanAndPoll(
      postJson(API + "/ram-cache/full-setup"),
      "전체 셋업 시작됨 — 운영 로컬에서 전 제품 캐시를 병렬 빌드합니다.",
      1440,   // 전체 셋업은 오래 걸릴 수 있어 폴링 상한을 1시간으로
    );
  };
  const saveQueryWorkers = () => {
    setQueryWorkersSaveBusy(true);
    postJson(API + "/query-workers/save", { query_workers: queryWorkersDraft })
      .then(d => { setQueryWorkersStatus(d); setQueryWorkersDraft(d.effective || d.configured || 3); toast.ok("쿼리 워커 수 저장됨 (effective: " + d.effective + ")"); })
      .catch(e => toast.error("쿼리 워커 수 저장 실패: " + (e?.message || e)))
      .finally(() => setQueryWorkersSaveBusy(false));
  };

  const savePriority = (lots) => {
    postJson(API + "/ram-cache/priority-lots/save", { product: selProd, lots })
      .then(() => { toast.ok("주요 lot 저장됨"); loadPriority(selProd); loadOverview(); })
      .catch(e => toast.error("저장 실패: " + (e?.message || e)));
  };

  const saveBudget = () => {
    if (!selProd) return;
    setBudgetSaving(true);
    const payload = { product: selProd, max_roots: Number(budgetDraft) || 1000 };
    if (budgetDraftDev !== "") payload.max_roots_dev = Number(budgetDraftDev) || 200;
    postJson(API + "/ram-cache/product-budgets/save", payload)
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
  // 주요 Lot 표의 적재 여부 표시용 — 행×엔트리 선형 스캔 대신 Set 조회
  const cachedRootIds = useMemo(() => contents ? new Set((contents.entries || []).map(e => e.root_lot_id)) : null, [contents]);

  return (
    <div style={{ padding: 20, maxWidth: 1100, margin: "0 auto" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 8, marginBottom: 14 }}>
        {canManage && <button onClick={openBudgetModal} title="캐시 예산 조절"
          style={{ ...S_BTN, display: "flex", alignItems: "center", gap: 4 }}>⚙ 예산 설정</button>}
        <button onClick={refreshAll} style={{ ...S_BTN, color: "var(--accent)" }}>새로고침</button>
      </div>

      {/* 캐시 예산 조절 모달 (톱니바퀴) */}
      {budgetModalOpen && canManage && (
        <div onClick={() => setBudgetModalOpen(false)}
          style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.45)", zIndex: 1000,
            display: "flex", alignItems: "flex-start", justifyContent: "center", padding: "6vh 16px", overflow: "auto" }}>
          <div onClick={e => e.stopPropagation()}
            style={{ width: "min(560px, 100%)", background: "var(--bg-card)", border: "1px solid var(--border)",
              borderRadius: 12, padding: 18, display: "grid", gap: 14 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontSize: 16, fontWeight: 800 }}>⚙ 캐시 예산 조절</span>
              <button onClick={() => setBudgetModalOpen(false)} style={{ ...S_BTN, padding: "2px 8px" }}>✕</button>
            </div>
            {budgetCfg && <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.6 }}>
              현재 서버: <b>{budgetCfg.is_dev ? "개발" : "운영"}</b> · 호스트 메모리 {budgetCfg.host_total_gb} GB ·
              실제 캐시 풀 <b style={{ color: "var(--accent)" }}>{budgetCfg.effective?.pool_gb} GB</b>
              <div>예산을 넘으면 오래된 항목부터 자동 축출됩니다(크래시 없음). 빈칸/0 = 자동. env 로 고정된 항목은 편집이 무시됩니다.</div>
            </div>}
            {budgetCfg && (() => {
              const eff = budgetCfg.effective || {}; const pins = budgetCfg.env_pins || {};
              return <div style={{ display: "grid", gap: 8 }}>
                {/* 전체 캐시 풀 비율 — 운영/개발 분리 */}
                <div style={{ display: "grid", gap: 6, padding: "8px 10px", borderRadius: 8,
                  border: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
                  <div style={{ fontSize: 13, fontWeight: 700 }}>전체 캐시 풀 비율 (×)
                    {pins.pool_fraction && <span style={{ fontSize: 10, color: "var(--warn)" }}> · env 고정</span>}</div>
                  <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>호스트 메모리 × 이 비율이 전 캐시 합계 상한 (0.1~0.8). 현재 서버({budgetCfg.is_dev ? "개발" : "운영"}) 적용: <b>{eff.pool_fraction} → 풀 {eff.pool_gb}GB</b></div>
                  <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
                    <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12 }}>운영
                      <input type="number" step="any" value={budgetForm.pool_fraction ?? ""} disabled={pins.pool_fraction}
                        placeholder={String(budgetCfg.defaults?.pool_fraction ?? 0.45)}
                        onChange={e => setBudgetForm(f => ({ ...f, pool_fraction: e.target.value }))}
                        style={{ ...S_INPUT, width: 76, fontFamily: "monospace" }} /></label>
                    <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12 }}>개발
                      <input type="number" step="any" value={budgetForm.pool_fraction_dev ?? ""} disabled={pins.pool_fraction}
                        placeholder="운영×축소 자동"
                        onChange={e => setBudgetForm(f => ({ ...f, pool_fraction_dev: e.target.value }))}
                        style={{ ...S_INPUT, width: 110, fontFamily: "monospace" }} /></label>
                  </div>
                  <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>
                    개발을 비우면 <b>운영값 × 개발 축소계수({eff.dev_factor})</b>로 자동 계산됩니다.
                    {budgetCfg.is_dev && !budgetForm.pool_fraction_dev && <label style={{ display: "inline-flex", alignItems: "center", gap: 4, marginLeft: 6 }}>· 축소계수
                      <input type="number" step="any" value={budgetForm.dev_factor ?? ""} disabled={pins.dev_factor}
                        placeholder={String(budgetCfg.defaults?.dev_factor ?? 0.35)}
                        onChange={e => setBudgetForm(f => ({ ...f, dev_factor: e.target.value }))}
                        style={{ ...S_INPUT, width: 64, fontFamily: "monospace" }} /></label>}
                  </div>
                </div>
                {/* Root lot RAM 캐시 — 운영/개발 분리 */}
                <div style={{ display: "grid", gap: 6, padding: "8px 10px", borderRadius: 8,
                  border: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
                  <div style={{ fontSize: 13, fontWeight: 700 }}>Root lot RAM 캐시 (GB)
                    {pins.root_ram_gb && <span style={{ fontSize: 10, color: "var(--warn)" }}> · env 고정</span>}</div>
                  <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>SplitTable 검색용 per-root 메모리 캐시 (빈칸=적응형). 현재 서버({budgetCfg.is_dev ? "개발" : "운영"}) 적용: <b>{eff.root_ram_gb} GB</b></div>
                  <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
                    <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12 }}>운영
                      <input type="number" step="any" value={budgetForm.root_ram_gb ?? ""} disabled={pins.root_ram_gb} placeholder="자동"
                        onChange={e => setBudgetForm(f => ({ ...f, root_ram_gb: e.target.value }))}
                        style={{ ...S_INPUT, width: 76, fontFamily: "monospace" }} /></label>
                    <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12 }}>개발
                      <input type="number" step="any" value={budgetForm.root_ram_gb_dev ?? ""} disabled={pins.root_ram_gb} placeholder="운영값 따름"
                        onChange={e => setBudgetForm(f => ({ ...f, root_ram_gb_dev: e.target.value }))}
                        style={{ ...S_INPUT, width: 96, fontFamily: "monospace" }} /></label>
                  </div>
                </div>
                {/* 제품 원본 RAM 캐시 — 운영/개발 각각 켜기/끄기 + 상한 */}
                <div style={{ display: "grid", gap: 8, padding: "8px 10px", borderRadius: 8,
                  border: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
                  <div style={{ fontSize: 13, fontWeight: 700 }}>제품 원본 RAM 캐시 (운영/개발 개별)
                    {pins.product_ram_enabled && <span style={{ fontSize: 10, color: "var(--warn)" }}> · env 고정</span>}</div>
                  <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>
                    제품 전체 테이블을 통째로 메모리에 상주(가장 무거움). SplitTable 검색엔 필수 아님 — 10~15GB 개발서버는 <b>꺼두는 것 권장</b>.
                    현재 서버({budgetCfg.is_dev ? "개발" : "운영"}) 적용: <b>{eff.product_ram_enabled ? "켜짐" : "꺼짐"}{eff.product_ram_enabled ? ` · ${eff.product_ram_gb}GB` : ""}</b>
                  </div>
                  {[["운영", "product_ram_enabled", "product_ram_gb"], ["개발", "product_ram_enabled_dev", "product_ram_gb_dev"]].map(([lbl, ek, gk]) => (
                    <div key={ek} style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                      <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 700, minWidth: 56, cursor: pins.product_ram_enabled ? "default" : "pointer" }}>
                        <input type="checkbox" checked={!!budgetForm[ek]} disabled={pins.product_ram_enabled}
                          onChange={e => setBudgetForm(f => ({ ...f, [ek]: e.target.checked }))} />{lbl}
                      </label>
                      {budgetForm[ek] && <span style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12, color: "var(--text-secondary)" }}>상한
                        <input type="number" step="any" value={budgetForm[gk] ?? ""} disabled={pins.product_ram_gb} placeholder="자동"
                          onChange={e => setBudgetForm(f => ({ ...f, [gk]: e.target.value }))}
                          style={{ ...S_INPUT, width: 76, fontFamily: "monospace" }} /> GB</span>}
                    </div>
                  ))}
                </div>
                {/* FAB 매칭캐시 빌드 배치 크기 — 운영/개발 분리 */}
                <div style={{ display: "grid", gap: 6, padding: "8px 10px", borderRadius: 8,
                  border: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
                  <div style={{ fontSize: 13, fontWeight: 700 }}>FAB 매칭캐시 빌드 배치 (root 개수)
                    {pins.match_cache_batch_roots && <span style={{ fontSize: 10, color: "var(--warn)" }}> · env 고정</span>}</div>
                  <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>
                    수동스캔의 FAB 매칭캐시를 root_lot_id 단위로 나눠 빌드할 때 한 번에 처리할 root 수.
                    <b>작을수록 peak RAM ↓, 대신 FAB 재스캔이 늘어 느려짐</b>. 메모리 작은 개발서버는 낮게(예: 50~100) 권장.
                    빈칸/0 = 기본 300. 현재 서버({budgetCfg.is_dev ? "개발" : "운영"}) 적용: <b>{eff.match_cache_batch_roots} root/배치</b>
                  </div>
                  <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
                    <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12 }}>운영
                      <input type="number" step="1" min="1" value={budgetForm.match_cache_batch_roots ?? ""} disabled={pins.match_cache_batch_roots}
                        placeholder={String(budgetCfg.defaults?.match_cache_batch_roots ?? 300)}
                        onChange={e => setBudgetForm(f => ({ ...f, match_cache_batch_roots: e.target.value }))}
                        style={{ ...S_INPUT, width: 90, fontFamily: "monospace" }} /></label>
                    <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12 }}>개발
                      <input type="number" step="1" min="1" value={budgetForm.match_cache_batch_roots_dev ?? ""} disabled={pins.match_cache_batch_roots}
                        placeholder="운영값 따름"
                        onChange={e => setBudgetForm(f => ({ ...f, match_cache_batch_roots_dev: e.target.value }))}
                        style={{ ...S_INPUT, width: 110, fontFamily: "monospace" }} /></label>
                  </div>
                </div>
                {/* SplitTable 검색 동시 슬롯 (cold 레인) — 운영/개발 분리 */}
                <div style={{ display: "grid", gap: 6, padding: "8px 10px", borderRadius: 8,
                  border: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
                  <div style={{ fontSize: 13, fontWeight: 700 }}>SplitTable 검색 동시 슬롯 (첫 조회)
                    {pins.view_cold_concurrency && <span style={{ fontSize: 10, color: "var(--warn)" }}> · env 고정</span>}</div>
                  <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>
                    캐시에 없는 <b>첫 조회(cold)만</b> 이 슬롯 수만큼 동시에 처리하고 나머지는 대기시킨다.
                    이미 계산된 결과를 돌려주는 재조회는 줄서지 않으므로 영향 없음.
                    <b> 올리면 동시 첫조회는 덜 기다리지만 메모리 피크와 CPU 경쟁이 커진다.</b>
                    <u>활동 대시보드 → SplitTable 검색 타이밍의 &apos;대기&apos; 수치가 지속적으로 클 때만 올리세요</u>
                    (대기는 작은데 &apos;계산&apos;이 크면 슬롯을 늘려도 나아지지 않습니다).
                    빈칸/0 = 자동({budgetCfg.defaults?.view_cold_concurrency ?? "-"}).
                    현재 서버({budgetCfg.is_dev ? "개발" : "운영"}) 적용: <b>{eff.view_cold_concurrency} 슬롯</b>
                    {budgetCfg.cold_lane ? <> · 지금 실행 중 <b>{budgetCfg.cold_lane.active}</b>건</> : null}
                  </div>
                  <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
                    <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12 }}>운영
                      <input type="number" step="1" min="1" max="8" value={budgetForm.view_cold_concurrency ?? ""} disabled={pins.view_cold_concurrency}
                        placeholder={String(budgetCfg.defaults?.view_cold_concurrency ?? "")}
                        onChange={e => setBudgetForm(f => ({ ...f, view_cold_concurrency: e.target.value }))}
                        style={{ ...S_INPUT, width: 90, fontFamily: "monospace" }} /></label>
                    <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12 }}>개발
                      <input type="number" step="1" min="1" max="8" value={budgetForm.view_cold_concurrency_dev ?? ""} disabled={pins.view_cold_concurrency}
                        placeholder="운영값 따름"
                        onChange={e => setBudgetForm(f => ({ ...f, view_cold_concurrency_dev: e.target.value }))}
                        style={{ ...S_INPUT, width: 110, fontFamily: "monospace" }} /></label>
                  </div>
                  <div style={{ fontSize: 10.5, color: "var(--text-secondary)" }}>
                    저장 즉시 적용됩니다 (재시작 불필요).
                  </div>
                </div>
              </div>;
            })()}
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button onClick={() => setBudgetModalOpen(false)} style={S_BTN}>닫기</button>
              <button onClick={saveBudgetCfg} disabled={budgetCfgSaving}
                style={{ ...S_BTN, border: "1px solid var(--accent)", background: "var(--accent-glow)", color: "var(--accent)",
                  fontWeight: 700, cursor: budgetCfgSaving ? "wait" : "pointer" }}>
                {budgetCfgSaving ? "저장 중" : "저장"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 전체 사용량 바 — 서버 전체 메모리 현황이라 관리자 전용 */}
      {canManage && <div style={{ padding: "10px 12px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg-card)", marginBottom: 14 }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
          <span style={{ fontSize: 14, fontWeight: 800 }}>전체 RAM 캐시 사용량</span>
          {overview && <span style={{ fontSize: 13, color: "var(--text-secondary)", fontFamily: "monospace" }}>
            {overview.total_mb} MB / {overview.max_mb} MB
          </span>}
        </div>
        <div style={{ height: 8, borderRadius: 4, background: "var(--bg-secondary)", overflow: "hidden" }}>
          <div style={{ height: "100%", borderRadius: 4, transition: "width 0.3s", width: usagePct + "%",
            background: usagePct > 85 ? "var(--danger)" : "var(--accent)" }} />
        </div>
      </div>}

      {/* 서버 메모리 종합 — root RAM 캐시 외의 캐시(파일탐색기·ET Index 등)까지 합산 */}
      {canManage && memOverview && <div style={{ padding: "10px 12px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg-card)", marginBottom: 14 }}>
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
                        background: pct > 85 ? "var(--danger)" : "var(--accent)" }} />
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>}

      {/* 제품 선택 — 일반 유저는 제품별 현황 표 대신 이 드롭다운으로 제품을 고른다 */}
      {!canManage && <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 14,
        padding: "8px 12px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg-card)" }}>
        <span style={{ fontSize: 14, fontWeight: 800, whiteSpace: "nowrap" }}>제품</span>
        <select value={selProd} onChange={e => setSelProd(e.target.value)}
          style={{ ...S_INPUT, fontFamily: "monospace", cursor: "pointer", minWidth: 200 }}>
          {(overview?.products || []).map(p => <option key={p.product} value={p.product}>{p.product}</option>)}
        </select>
        {overviewLoading && <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>로딩 중...</span>}
      </div>}

      {/* 제품별 현황 */}
      {canManage && <div style={{ marginBottom: 16 }}>
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
                      ? <span style={{ color: p.priority_cached >= p.priority_total ? "var(--ok)" : "var(--danger)" }}>
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
      </div>}

      {/* 관리자 — 캐시 수동 스캔/설정 (SplitTable 톱니바퀴에서 이동) */}
      {canManage && <div style={{ display: "grid", gap: 10, padding: "10px 12px", borderRadius: 8,
        border: "1px solid var(--border)", background: "var(--bg-card)", marginBottom: 16 }}>
        <div style={{ fontSize: 14, fontWeight: 800 }}>관리자 · 캐시 수동 스캔 / 설정</div>

        {/* 통합 수동 스캔 버튼 */}
        <div style={{ display: "grid", gap: 8, padding: "8px 10px", borderRadius: 8,
          border: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <button onClick={runUnifiedScan} disabled={unifiedScanBusy}
              style={{ ...S_BTN, border: "1px solid var(--accent)", background: "var(--accent-glow)", color: "var(--accent)",
                fontWeight: 700, borderRadius: 999, cursor: unifiedScanBusy ? "wait" : "pointer",
                opacity: unifiedScanBusy ? 0.65 : 1, fontSize: 14, padding: "7px 16px" }}>
              {unifiedScanBusy ? "통합 스캔 중..." : selProd ? `수동 스캔 (${selProd})` : "수동 스캔 (전체 제품)"}
            </button>
            <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
              FAB 매칭 → 제품 원본 → Root lot RAM 캐시를 순서대로 갱신합니다
            </span>
          </div>
          {/* 전체 셋업 (초기 1회) — 운영 로컬·5병렬·20GB 로 전 제품 빠른 캐싱. 관리자 전용. */}
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap",
            paddingTop: 8, borderTop: "1px dashed var(--border)" }}>
            <button onClick={runFullSetup} disabled={unifiedScanBusy}
              style={{ ...S_BTN, border: "1px solid var(--warn-line)", background: "var(--warn-50)",
                color: "var(--warn)", fontWeight: 800, borderRadius: 999,
                cursor: unifiedScanBusy ? "wait" : "pointer", opacity: unifiedScanBusy ? 0.65 : 1,
                fontSize: 14, padding: "7px 16px" }}>
              {unifiedScanBusy ? "실행 중..." : "⚡ 전체 셋업 (초기 1회)"}
            </button>
            <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
              운영 서버에서 <b>직접</b> 전 제품 캐시를 <b>5병렬·최대 20GB</b> 로 빠르게 빌드(개발 워커로 넘기지 않음). 초기 배포 시 1회용.
            </span>
          </div>
          {/* 캐시 상태 요약 */}
          {(() => {
            const pc = (productCacheStatus?.products || [])[0] || {};
            const rc = rootLotCacheStatus?.cache || {};
            const hit = !!pc.hit; const stale = !!pc.stale;
            const refreshing = !!pc.refreshing || !!productCacheStatus?.job?.running;
            const tone = stale ? "var(--warn)" : hit ? "var(--info)" : "var(--text-secondary)";
            return (
              <div style={{ display: "flex", gap: 16, flexWrap: "wrap", fontSize: 12, fontFamily: "monospace", color: "var(--text-secondary)" }}>
                <span>제품 원본: <b style={{ color: tone }}>{refreshing ? "refreshing" : stale ? "stale" : hit ? "hit" : "miss"}</b> · rows {pc.row_count || 0} · {Number(pc.estimated_mb || 0).toFixed(1)} MB</span>
                <span>Root lot: <b style={{ color: "var(--accent)" }}>{rc.hit_roots || 0}</b> roots (step {rc.step_hit_roots || 0} / priority {rc.priority_hit_roots || 0} / other {rc.other_hit_roots || 0}) · {Number(rc.estimated_mb || 0).toFixed(1)} MB / {rc.max_gb || 0} GB</span>
                <span>스케줄러: {rc.scheduler_started
                  ? <b style={{ color: "var(--ok)" }}>작동중</b>
                  : <b style={{ color: "var(--danger)" }}>미시작</b>}
                  {rc.last_refresh_at && <span> · 마지막 {rc.last_refresh_at}</span>}
                  {rc.last_error && <span style={{ color: "var(--danger)" }}> · {rc.last_error}</span>}
                  {rc.last_resource_guard_reason && <span style={{ color: "var(--warn)" }}> · guard: {rc.last_resource_guard_reason}</span>}
                </span>
                <button onClick={() => { reloadProductCacheStatus(selProd); reloadRootLotCacheStatus(selProd); }}
                  style={{ ...S_BTN, fontSize: 11, padding: "2px 6px", whiteSpace: "nowrap" }}>상태 새로고침</button>
                {/* 랏(root lot) 캐시가 0 인 이유를 명시 — 개발서버에서 '고장'으로 오인되던 부분.
                    제품 원본 RAM 캐시 온오프와는 무관하다는 점까지 문장에 포함된다. */}
                {rc.enabled === false && <div style={{ flexBasis: "100%", fontFamily: "inherit",
                  fontSize: 12, color: "var(--warn)", lineHeight: 1.5 }}>
                  ⚠ Root lot RAM 캐시 비활성 — {rc.disabled_reason || "이 서버에서 꺼져 있습니다"}
                </div>}
                {productCacheStatus?.enabled === false && <div style={{ flexBasis: "100%", fontFamily: "inherit",
                  fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.5 }}>
                  ℹ 제품 원본 RAM 캐시가 꺼져 있습니다(개발서버 권장). 이 설정은 <b>랏(lookup)/Root lot 캐시와 독립</b>이며,
                  꺼도 랏 캐시 빌드·적재는 정상 동작합니다.
                </div>}
              </div>);
          })()}
          {/* 진행 상황 패널 — 수동 스캔/전체 셋업/예약 작업 모두 같은 형태로 표시된다.
              (단계별 상태 · 경과시간 · 실패 사유 · 메모리 · 외부 큐) */}
          <CacheJobPanel jobs={cacheJobs} queues={cacheQueues} />
          <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>
            수동 스캔의 단계별 진행은 위 <b>캐시 작업 파이프라인</b>과 아래 <b>캐시 이벤트 로그</b>에 실시간으로 표시됩니다.
            실패하면 단계 배지가 <b style={{ color: "var(--danger)" }}>실패</b>로 바뀌고 사유가 함께 표시됩니다.
          </div>
        </div>

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
              style={{ ...S_BTN, border: "1px solid var(--info-line)", background: "var(--info-50)",
                color: "var(--info)", fontWeight: 700, whiteSpace: "nowrap" }}>
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
              ? "var(--danger)" : "var(--accent)" }}>{peakRam.peak_rss_gb?.toFixed(2) || "?"} GB</b></span>
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
            <Filter value={cacheEventLogFilter}
              onChange={e => { setCacheEventLogFilter(e.target.value); loadCacheEventLog(e.target.value); }}
              options={[{ value: "scan", label: "수동 스캔" }, { value: "warmup", label: "예열" },
                { value: "eviction", label: "축출" }, { value: "watchdog", label: "워치독" },
                { value: "cache_op", label: "캐시 적재" }]}
              placeholder="전체" style={{ fontSize: 12 }} />
            <button onClick={() => loadCacheEventLog(cacheEventLogFilter)}
              style={{ ...S_BTN, fontSize: 12, padding: "3px 8px" }}>새로고침</button>
          </div>
          <div style={{ maxHeight: 320, overflow: "auto", borderRadius: 6, border: "1px solid var(--border)" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, fontFamily: "monospace" }}>
              <thead>
                <tr style={{ background: "var(--bg-secondary)", position: "sticky", top: 0 }}>
                  <th style={{ padding: "4px 6px", textAlign: "left", borderBottom: "1px solid var(--border)", whiteSpace: "nowrap" }}>시간</th>
                  <th style={{ padding: "4px 6px", textAlign: "center", borderBottom: "1px solid var(--border)", whiteSpace: "nowrap" }}>서버</th>
                  <th style={{ padding: "4px 6px", textAlign: "center", borderBottom: "1px solid var(--border)", whiteSpace: "nowrap" }}>분류</th>
                  <th style={{ padding: "4px 6px", textAlign: "left", borderBottom: "1px solid var(--border)", whiteSpace: "nowrap" }}>단계</th>
                  <th style={{ padding: "4px 6px", textAlign: "center", borderBottom: "1px solid var(--border)", whiteSpace: "nowrap" }}>상태</th>
                  <th style={{ padding: "4px 6px", textAlign: "left", borderBottom: "1px solid var(--border)" }}>이벤트</th>
                </tr>
              </thead>
              <tbody>
                {(cacheEventLog || []).map((ev, i) => (
                  <tr key={i} style={{ borderBottom: "1px solid var(--border)",
                    background: !ev.ok ? "rgba(239,68,68,0.04)" : ev.category === "eviction" ? "rgba(245,158,11,0.04)" : "transparent" }}>
                    <td style={{ padding: "3px 6px", whiteSpace: "nowrap", color: "var(--text-secondary)" }}>
                      {fmtKst(ev)}
                    </td>
                    <td style={{ padding: "3px 6px", textAlign: "center", whiteSpace: "nowrap" }}>
                      {ev.origin
                        ? <span title={ev.host || ""} style={{ padding: "1px 5px", borderRadius: 3, fontSize: 10, fontWeight: 700,
                            background: ev.origin === "운영" ? "var(--info-50)" : "var(--warn-50)",
                            color: ev.origin === "운영" ? "var(--info)" : "var(--warn)" }}>{ev.origin}</span>
                        : <span style={{ color: "var(--text-secondary)" }}>-</span>}
                    </td>
                    <td style={{ padding: "3px 6px", textAlign: "center" }}>
                      <span style={{ padding: "1px 5px", borderRadius: 3, fontSize: 11,
                        background: ev.category === "warmup" ? "var(--info-50)" :
                                    ev.category === "eviction" ? "var(--warn-50)" :
                                    ev.category === "watchdog" ? "rgba(239,68,68,0.12)" :
                                    ev.category === "scan" ? "var(--ok-50)" :
                                    ev.category === "cache_op" ? "var(--ok-50)" : "var(--bg-tertiary)",
                        color: ev.category === "warmup" ? "var(--info)" :
                               ev.category === "eviction" ? "var(--warn)" :
                               ev.category === "watchdog" ? "var(--danger)" :
                               ev.category === "scan" ? "var(--ok)" :
                               ev.category === "cache_op" ? "var(--ok)" : "var(--text-secondary)" }}>
                        {ev.category}
                      </span>
                    </td>
                    <td style={{ padding: "3px 6px", whiteSpace: "nowrap", color: ev.detail?.stage ? "var(--text-primary)" : "var(--text-secondary)" }}>
                      {scanStageLabel(ev)}
                    </td>
                    <td style={{ padding: "3px 6px", textAlign: "center",
                      color: ev.ok ? "var(--ok)" : "var(--danger)", fontWeight: 700 }}>
                      {ev.ok ? "OK" : "FAIL"}
                    </td>
                    <td style={{ padding: "3px 6px", fontFamily: "inherit", fontSize: 12, wordBreak: "break-all" }}>
                      {ev.event}
                      {ev.product && <span style={{ marginLeft: 6, color: "var(--text-secondary)" }}>({ev.product})</span>}
                    </td>
                  </tr>
                ))}
                {(!cacheEventLog || cacheEventLog.length === 0) &&
                  <tr><td colSpan={6} style={{ padding: 14, textAlign: "center", color: "var(--text-secondary)" }}>
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
          <div style={{ flex: 1 }}>
            <TabStrip active={subTab} onChange={setSubTab}
              items={[{ k: "priority", l: "주요 Lot" }, { k: "contents", l: "전체 캐시" }]} />
          </div>
        </div>

        {/* 주요 Lot 서브탭 — 엑셀형 랏 운영 표 */}
        {subTab === "priority" && <div style={{ display: "grid", gap: 8 }}>
          <div style={{ fontSize: 13, color: "var(--text-secondary)" }}>
            제품별 주요 lot 관리 — purpose/comment는 엔지니어가 직접 정리하고, 위치(step_id/step_desc)는 최신 진행 데이터에서 자동으로 채워집니다.
            등록된 lot은 RAM 캐시에 우선 적재됩니다 (lot_id 앞 5자리 = root_lot_id).
          </div>
          {lotStatusSkipped && <div style={{ padding: "6px 10px", borderRadius: 6, fontSize: 12,
            border: "1px solid var(--danger-line)", background: "rgba(239,68,68,0.06)", color: "var(--text-secondary)" }}>
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
                  const inCache = cachedRootIds ? cachedRootIds.has(rootId) : null;
                  // 저장 API 가 page manager 전용이라 일반 유저에겐 읽기 전용으로 준다.
                  const cellInput = (field, mono, placeholder) => (
                    <input value={lot[field] || ""} placeholder={canManage ? placeholder : ""} readOnly={!canManage}
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
                              style={{ marginRight: 6, fontSize: 11, color: inCache ? "var(--ok)" : "var(--danger)" }}>
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
                        <input type="checkbox" checked={lot.cache_enabled !== false} disabled={!canManage}
                          onChange={e => { const v = [...priorityLots]; v[i] = { ...v[i], cache_enabled: e.target.checked }; setPriorityLots(v); }} />
                      </td>
                      <td style={{ padding: "0 6px", textAlign: "center" }}>
                        {canManage && <button onClick={() => { const v = [...priorityLots]; v.splice(i, 1); setPriorityLots(v); }}
                          style={{ padding: "2px 6px", borderRadius: 4, border: "none", background: "transparent",
                            color: "var(--danger)", fontSize: 12, cursor: "pointer" }}>✕</button>}
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
            {canManage && <>
              <button onClick={() => setPriorityLots([...priorityLots, { lot_id: "", purpose: "", comment: "", cache_enabled: true }])}
                style={S_BTN}>+ 추가</button>
              <button onClick={() => savePriority(priorityLots)}
                style={{ ...S_BTN, border: "1px solid var(--accent)", background: "var(--accent-glow)", color: "var(--accent)", fontWeight: 700 }}>저장</button>
            </>}
            <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
              ● = RAM 캐시 적재됨 · 위치는 wafer 중 최신 tkout 기준
              {canManage ? " · 엑셀에서 여러 행/열을 복사해 셀에 붙여넣으면 표에 채워집니다" : " · 등록/수정은 이 페이지 관리 권한이 필요합니다"}
            </span>
          </div>
        </div>}

        {/* 전체 캐시 서브탭 */}
        {subTab === "contents" && <div style={{ display: "grid", gap: 8 }}>
          {/* 상한 편집도 저장 API 가 page manager 전용 — 일반 유저에겐 감춘다 */}
          {canManage && <div style={{ display: "flex", gap: 8, alignItems: "center", padding: "6px 8px", borderRadius: 6,
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
          </div>}
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
                  <tr key={i} style={{ borderBottom: "1px solid var(--border)", background: e.is_priority ? "var(--accent-glow)" : "transparent" }}>
                    <td style={{ padding: "3px 6px" }}>{e.is_priority && <span style={{ color: "var(--accent)", marginRight: 4 }}>★</span>}{e.root_lot_id}</td>
                    <td style={{ padding: "3px 6px", textAlign: "right" }}>{e.row_count?.toLocaleString()}</td>
                    <td style={{ padding: "3px 6px", textAlign: "right" }}>{e.estimated_mb}</td>
                    <td style={{ padding: "3px 6px", textAlign: "right" }}>{e.access_count}</td>
                    <td style={{ padding: "3px 6px", textAlign: "center" }}>
                      <span style={{ padding: "1px 6px", borderRadius: 3, fontSize: 11,
                        background: e.cache_group === "priority" ? "var(--accent-glow)" : e.cache_group === "step" ? "var(--ok-50)" : "var(--bg-tertiary)",
                        color: e.cache_group === "priority" ? "var(--accent)" : e.cache_group === "step" ? "var(--ok)" : "var(--text-secondary)" }}>{e.cache_group}</span>
                    </td>
                    <td style={{ padding: "3px 6px", fontSize: 11, color: "var(--text-secondary)" }}>{e.loaded_at}</td>
                  </tr>
                ))}
                {(contents.entries || []).length === 0 &&
                  <tr><td colSpan={6} style={{ padding: 12, textAlign: "center", color: "var(--text-secondary)" }}>
                    캐시된 root lot이 없습니다
                    {(() => {
                      // 원인 진단은 관리자만 받는 root-lot 상태가 있을 때만 —
                      // 상태 없이 추측하면 일반 유저에게 "스케줄러 미시작" 오진이 뜬다.
                      if (!rootLotCacheStatus) return null;
                      const rc = rootLotCacheStatus.cache || {};
                      if (!rc.scheduler_started) return <div style={{ fontSize: 11, marginTop: 4 }}>스케줄러가 시작되지 않았습니다. 서버 재시작 후 약 2분 뒤 자동 적재됩니다.</div>;
                      if (rc.last_resource_guard_reason) return <div style={{ fontSize: 11, marginTop: 4 }}>리소스 가드 활성: {rc.last_resource_guard_reason} — 메모리 여유가 생기면 자동 적재됩니다.</div>;
                      if (rc.max_gb <= 0) return <div style={{ fontSize: 11, marginTop: 4 }}>캐시 예산이 0입니다. FLOW_SPLITTABLE_ROOT_LOT_RAM_CACHE_MAX_GB 환경변수를 확인하세요.</div>;
                      return null;
                    })()}
                  </td></tr>}
              </tbody>
            </table>
          </div>}
        </div>}
      </div>}
    </div>
  );
}
