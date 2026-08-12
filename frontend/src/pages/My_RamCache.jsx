import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { toast } from "../components/Toast";
import usePolling from "../hooks/usePolling";
import { postJson, qs, sf } from "../lib/api";
import { isAdmin } from "../lib/permissions";
import { Filter, TabStrip } from "../components/UXKit";
import { PageGearButton } from "../components/PageGear";

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

// 스캔 큐 행의 중단/취소 버튼 — 위험 동작이라 danger 톤, 행 안에 들어가므로 작게.
const scanCancelBtn = {
  padding: "2px 9px", borderRadius: 5, border: "1px solid var(--danger)",
  background: "transparent", color: "var(--danger)", fontSize: 11,
  fontWeight: 700, cursor: "pointer", flexShrink: 0,
};

const JOB_TONE = {
  running: "var(--info)", queued: "var(--warn)",
  done: "var(--ok)", failed: "var(--danger)", skipped: "var(--text-secondary)",
};
const SCAN_STAGE_LABEL = {
  lookup_build: "랏(lookup) 캐시 빌드",
  pivot_build: "root별 SplitTable pivot",
  latest_lot: "WIP latest-lot",
  fab_index: "root별 FAB latest 인덱스",
  et_history: "제품 ET history",
  match_cache: "FAB 매칭 캐시",
  product_ram: "제품 원본 RAM 캐시",
  root_lot_ram: "Root lot lookup/RAM 캐시",
  // cache_event_log.stage_detail() 이 쓰는 kind 이름 (CACHE_KIND_LABELS 와 같은 어휘).
  lookup: "랏(lookup) 캐시 빌드",
  pivot: "root별 SplitTable pivot",
  match: "FAB 매칭 캐시",
};
// 단계 phase 어휘는 두 벌이다: 옛 detail.phase(started/finished/failed) 와
// 현재 stage_detail() 의 phase(start/done/fail/skip). 둘 다 받는다.
const STAGE_PHASE_KO = {
  start: "시작", started: "시작",
  done: "완료", finished: "완료",
  fail: "실패", failed: "실패",
  skip: "건너뜀", skipped: "건너뜀",
};
// 작업/단계 상태를 한국어로 — 화면에서 'done/failed' 대신 '완료/실패'로 읽히게.
const JOB_STATUS_KO = {
  running: "진행 중", queued: "대기", done: "완료", failed: "실패", skipped: "건너뜀",
};
const STAGE_MARK = { done: "✓", running: "●", failed: "✕", skipped: "—", queued: "○" };
// 단계별 시간 배분 막대의 색. 의미가 있는 순서가 아니라 인접 구간을 구분하기 위한
// 것이라 토큰 팔레트를 순환해 쓴다 ('미계측'만 회색으로 따로 칠한다).
const PHASE_COLORS = [
  "var(--accent)", "var(--info)", "var(--ok)", "var(--warn)", "var(--danger)",
  "var(--info-line)", "var(--ok-line)", "var(--warn-line)",
];
// 캐시 이벤트 로그의 분류(category)는 백엔드 내부 코드명이라 그대로 두면
// "warmup"/"cache_op" 같은 토큰이 그대로 노출된다 — 한국어 배지로 바꿔 보여준다.
const CATEGORY_KO = {
  scan: "스캔", warmup: "예열", eviction: "축출", watchdog: "워치독",
  cache_op: "캐시 적재", build: "빌드", budget: "예산",
};
// 전체 진행 카드의 빌드 종류 — 백엔드 progress.kind 코드명을 한국어로 바꾼다.
const PROGRESS_KIND_KO = {
  lookup: "랏 lookup", pivot: "SplitTable pivot", match: "FAB 매칭",
  fab_index: "FAB latest 인덱스", latest_lot: "WIP latest-lot", et_history: "ET history",
};
const PRODUCT_STATUS_KIND_NO = { lookup: 1, pivot: 2, latest_lot: 3, fab_index: 4 };
// 제품별 캐시 상태 배지. '기록 없음'은 실패가 아니다 — 아직 안 돌았을 뿐이라
// 빨강으로 칠하지 않는다.
// 'stale' 은 빌드 로그만 성공이고 실제 산출물이 없는 상태다 — 준비 카운트에서
// 빠지므로 성공(초록)으로 칠하면 "4개 다 성공인데 3/4 준비"로 보인다.
const CACHE_STATE_KO = {
  ok: "성공", failed: "실패", running: "진행 중", partial: "일부 기록 없음", never: "기록 없음",
  stale: "산출물 없음",
};
function cacheStateTone(state) {
  if (state === "ok") return { background: "var(--ok-50)", color: "var(--ok)" };
  if (state === "failed") return { background: "rgba(239,68,68,0.12)", color: "var(--danger)" };
  if (state === "running") return { background: "var(--info-50)", color: "var(--info)" };
  if (state === "stale") return { background: "var(--warn-50)", color: "var(--warn)" };
  return { background: "var(--bg-tertiary)", color: "var(--text-secondary)" };
}
// 초 단위 epoch → KST 시각. 0/누락이면 "-".
function fmtTs(ts) {
  return ts ? fmtKst({ ts }) : "-";
}
const CATEGORY_TONE = {
  warmup: ["var(--info-50)", "var(--info)"],
  eviction: ["var(--warn-50)", "var(--warn)"],
  watchdog: ["rgba(239,68,68,0.12)", "var(--danger)"],
  scan: ["var(--ok-50)", "var(--ok)"],
  cache_op: ["var(--ok-50)", "var(--ok)"],
  build: ["var(--info-50)", "var(--info)"],
};
function categoryTone(cat) {
  const [bg, color] = CATEGORY_TONE[cat] || ["var(--bg-tertiary)", "var(--text-secondary)"];
  return { background: bg, color };
}

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

// 예열 중단 사유 → 사람이 읽는 문장. 화면에서 "왜 0인가"를 그대로 답한다.
const WARM_SKIP_KO = {
  user_requests_active: "사용자 검색 중이라 양보",
  process_memory_high: "메모리 여유 부족(자원 가드)",
  process_cpu_high: "CPU 과부하(자원 가드)",
  ram_budget_full: "RAM 예산 가득",
};

/** 제품 행의 '마지막 예열' 셀 — 적재/목표와 못 채운 이유. */
function warmCellOf(p) {
  const w = p?.warm;
  if (!w) return <span style={{ color: "var(--text-secondary)" }}>기록 없음</span>;
  if (w.build_pending) {
    return <span style={{ color: "var(--warn)" }}>
      랏(lookup) 캐시 빌드 대기{w.cache_status ? ` (${w.cache_status})` : ""}
    </span>;
  }
  const reason = w.skip_reason ? (WARM_SKIP_KO[w.skip_reason] || w.skip_reason) : "";
  const done = w.target_roots > 0 && w.cached_roots >= w.target_roots;
  // 목표가 0인데 랏캐시엔 root 가 있는 경우 — 예열 후보 소스가 통째로 비었다는 뜻.
  if (w.target_roots === 0 && w.available_roots > 0) {
    return <span style={{ color: "var(--warn)" }}>
      후보 0 · 랏캐시엔 {w.available_roots.toLocaleString()} root — 상한(root)이 0이 아닌지 확인
    </span>;
  }
  return <span style={{ color: done ? "var(--ok)" : reason ? "var(--warn)" : "var(--text-secondary)" }}>
    {w.cached_roots}/{w.target_roots} 랏{reason ? ` · ${reason}` : ""}
    {w.index_target_roots > 0 && <span style={{ color: "var(--text-secondary)" }}> · 목록순 {w.index_target_roots}</span>}
    {w.missing_roots > 0 && <span style={{ color: "var(--text-secondary)" }}> · 파티션 없음 {w.missing_roots}</span>}
  </span>;
}

/** 제품별 현황 위 한 줄 — 이 서버에서 예열이 실제로 도는지 / 예산이 설정대로인지. */
function WarmupBanner({ w, isDev }) {
  const bad = !w.scheduler_started || !!w.disabled_reason;
  return <div style={{ display: "grid", gap: 3, padding: "6px 10px", marginBottom: 6, borderRadius: 7,
    border: `1px solid ${bad ? "var(--danger-line)" : "var(--border)"}`,
    background: bad ? "rgba(239,68,68,0.05)" : "var(--bg-secondary)", fontSize: 12 }}>
    <div style={{ display: "flex", gap: 14, flexWrap: "wrap", fontFamily: "monospace" }}>
      <span>서버 <b>{isDev ? "개발" : "운영"}{w.server_role ? ` (${w.server_role})` : ""}</b></span>
      <span>예열 스케줄러 {w.scheduler_started
        ? <b style={{ color: "var(--ok)" }}>작동중</b>
        : <b style={{ color: "var(--danger)" }}>미시작</b>}</span>
      <span>주기 {w.interval_minutes}분{w.last_cycle_incomplete ? ` (덜 채워져 ${w.retry_minutes}분 뒤 재시도)` : ""}</span>
      <span>예산 <b style={{ color: w.budget_capped ? "var(--warn)" : "var(--accent)" }}>{w.budget_gb} GB</b>
        {w.budget_setting_gb > 0 && <span style={{ color: "var(--text-secondary)" }}> / 설정 {w.budget_setting_gb} GB</span>}</span>
      <span>동시 로드 {w.load_workers}</span>
      {w.last_refresh_at && <span style={{ color: "var(--text-secondary)" }}>마지막 {w.last_refresh_at.replace("T", " ")}</span>}
    </div>
    {w.disabled_reason && <div style={{ color: "var(--danger)" }}>✕ 랏 RAM 캐시 꺼짐 — {w.disabled_reason}</div>}
    {w.budget_capped && <div style={{ color: "var(--warn)" }}>
      ⚠ 설정({w.budget_setting_gb}GB)보다 실제 예산({w.budget_gb}GB)이 작습니다 — 전 캐시 합계 상한(호스트 × 캐시 풀 비율)의
      서버의 자동 메모리 안전 상한에 맞춰 적용되었습니다. 캐시 작업은 이 범위 안에서 오래된 항목을 자동 정리합니다.
    </div>}
    {w.last_resource_guard_reason && <div style={{ color: "var(--warn)" }}>
      ⚠ 자원 가드: {WARM_SKIP_KO[w.last_resource_guard_reason] || w.last_resource_guard_reason} — 이 상태에서는 새 랏을 적재하지 않습니다.
    </div>}
  </div>;
}

/** 기술 진단 `<details>`의 펼침 상태를 탭 이동·새로고침 뒤에도 유지한다. */
function useStickyOpen(key, initial = false) {
  const [open, setOpen] = useState(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw === null ? initial : raw === "1";
    } catch (_) { return initial; }
  });
  const set = useCallback((next) => {
    setOpen(next);
    try { localStorage.setItem(key, next ? "1" : "0"); } catch (_) {}
  }, [key]);
  return [open, set];
}

/** 이벤트의 단계 표식 → 항상 **문자열**.
 *
 *  detail.stage 는 두 형태로 온다: 옛 계약은 문자열("lookup_build")에 형제 키
 *  detail.phase, 지금 백엔드(cache_event_log.stage_detail)는 {kind, phase} 객체다.
 *  객체를 그대로 돌려주면 그 값이 JSX 자식으로 들어가 React #31
 *  ("Objects are not valid as a React child")로 캐시관리 화면 전체가 죽는다. */
function scanStageLabel(event) {
  const raw = event?.detail?.stage;
  if (!raw) return "-";
  const isObj = typeof raw === "object";
  const kind = String((isObj ? raw.kind : raw) || "").trim();
  if (!kind) return "-";
  const phase = String((isObj ? raw.phase : event?.detail?.phase) || "").trim();
  const label = SCAN_STAGE_LABEL[kind] || kind;
  const phaseLabel = STAGE_PHASE_KO[phase] || "";
  return phaseLabel ? `${label} · ${phaseLabel}` : label;
}

/** 이벤트 본문도 항상 문자열로 렌더한다.
 *
 * 백그라운드 작업 하나가 실수로 객체 payload 를 event 자리에 보내도 두 번째
 * 로그부터 React #31 로 목록 전체가 사라지지 않게 한다. */
function cacheEventText(event) {
  const raw = event?.event;
  if (raw == null) return "-";
  if (typeof raw === "string" || typeof raw === "number" || typeof raw === "boolean") return String(raw);
  try { return JSON.stringify(raw); } catch (_) { return String(raw); }
}

function cacheEventDetailText(event) {
  const detail = event?.detail;
  if (!detail || typeof detail !== "object" || Object.keys(detail).length === 0) return "";
  try { return JSON.stringify(detail); } catch (_) { return String(detail); }
}

function cacheProductKey(value) {
  return String(value || "").replace(/^ML_TABLE_/i, "").trim().toUpperCase();
}

function cacheTaskForProduct(scanQueue, product) {
  const wanted = cacheProductKey(product);
  const tasks = [scanQueue?.current, ...(scanQueue?.pending || [])].filter(Boolean);
  return tasks.find(task => cacheProductKey(task.product) === wanted)
    || tasks.find(task => !task.product && ["unified_scan", "full_setup"].includes(task.kind))
    || null;
}

/** 진행 중인 job의 현재 단계 라벨 — 상태 요약 줄에서 "어디까지"를 짧게 보여주는 데 쓴다. */
function currentStageLabel(job) {
  const st = (job?.stages || []).find(s => s.id === job.current_stage);
  return st?.label || "";
}

/** 캐싱 진행 탭 맨 위 한 줄 요약 — "지금 뭐가 돌고 있고, 뭐가 밀려 있고, 마지막으로 언제
    끝났는지"를 한 곳에 모은다. 예전엔 이 세 신호가 파이프라인 패널·스캔 대기열·이벤트
    로그에 각각 흩어져 있어서 한눈에 안 읽혔다. */
function JobsStatusLine({ jobs, scanQueue }) {
  const running = (jobs || []).find(j => j.status === "running");
  const lastDone = (jobs || [])
    .filter(j => j.finished_at)
    .sort((a, b) => String(b.finished_at).localeCompare(String(a.finished_at)))[0];
  const waiting = Number(scanQueue?.depth || 0);
  const stage = running ? currentStageLabel(running) : "";
  return <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
    padding: "9px 12px", borderRadius: 8,
    border: `1px solid ${running ? "var(--info-line)" : "var(--border)"}`,
    background: running ? "var(--info-50)" : "var(--bg-card)", fontSize: 13 }}>
    <span style={{ fontWeight: 800, color: running ? "var(--info)" : "var(--text-secondary)", whiteSpace: "nowrap" }}>
      {running ? "● 진행 중" : "○ 유휴"}
    </span>
    {running
      ? <span>
          <b>{running.product || running.label}</b>
          {stage && <span style={{ color: "var(--text-secondary)" }}> · {stage}</span>}
          <span style={{ color: "var(--text-secondary)" }}> · {fmtDur(running.elapsed_sec)} 경과</span>
        </span>
      : <span style={{ color: "var(--text-secondary)" }}>진행 중인 작업이 없습니다</span>}
    <span style={{ color: "var(--border)" }}>|</span>
    <span style={{ color: waiting > 0 ? "var(--warn)" : "var(--text-secondary)" }}>대기 {waiting}건</span>
    <span style={{ color: "var(--border)" }}>|</span>
    <span style={{ color: "var(--text-secondary)" }}>
      마지막 완료 {lastDone ? fmtKst({ ts_iso: lastDone.finished_at }) : "기록 없음"}
    </span>
  </div>;
}

// 제품 단위 캐시 이력 — 시작/완료/실패만. 이전에는 job의 stage 칩과 RSS·Peak
// 증가·최저 여유 같은 내부 계측이 그대로 나와서, "어느 제품의 무슨 캐시가
// 언제 끝났나"를 읽어내기 어려웠다. 캐시 이벤트 로그와 같은 한 줄 형식으로
// 두되 큰 단위(제품 × lookup/Pivot/FAB 매칭/…)만 남긴다.
const MILESTONE_PHASE = {
  start: { mark: "▶", label: "시작", color: "var(--info)" },
  done: { mark: "✓", label: "완료", color: "var(--ok)" },
  fail: { mark: "✕", label: "실패", color: "var(--danger)" },
  skip: { mark: "–", label: "건너뜀", color: "var(--text-secondary)" },
};

function MilestoneLog({ milestones }) {
  const rows = milestones || [];
  if (!rows.length) return null;
  return <div style={{ display: "grid", gap: 4 }}>
    <span style={{ fontSize: 12, fontWeight: 700, color: "var(--text-secondary)" }}>
      제품별 캐시 작업 이력 · 최근 {rows.length}건
    </span>
    <div style={{ maxHeight: 220, overflowY: "auto", borderRadius: 6, border: "1px solid var(--border)" }}>
      {rows.map((m, i) => {
        const ph = MILESTONE_PHASE[m.phase] || MILESTONE_PHASE.start;
        return <div key={i} style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap",
          padding: "4px 8px", fontSize: 12, borderBottom: "1px solid var(--border)",
          borderLeft: `3px solid ${m.phase === "fail" ? "var(--danger)" : "transparent"}`,
          background: m.phase === "fail" ? "rgba(239,68,68,0.04)" : "transparent" }}>
          <span style={{ fontFamily: "monospace", color: "var(--text-secondary)", whiteSpace: "nowrap" }}>
            {fmtKst(m)}
          </span>
          <b style={{ color: "var(--accent)", minWidth: 180 }}>{m.product || "-"}</b>
          <span style={{ padding: "1px 6px", borderRadius: 3, fontSize: 11, fontWeight: 700,
            background: "var(--bg-tertiary)" }}>{m.label}</span>
          <b style={{ color: ph.color, whiteSpace: "nowrap" }}>{ph.mark} {ph.label}</b>
          <span style={{ color: "var(--text-secondary)", wordBreak: "break-word" }}>{m.event}</span>
        </div>;
      })}
    </div>
  </div>;
}

function CacheJobPanel({ jobs, queues, canManage, onStopProduct, milestones }) {
  const visible = (jobs || []).slice(0, 3);
  const matchCache = queues?.match_cache || {};
  const worker = queues?.worker || {};
  const lookup = queues?.lookup_build || {};
  const rootPrefetch = queues?.root_prefetch || {};
  const externalQueued = Number(worker.depth || 0) + (lookup.queued || []).length
    + Number(rootPrefetch.depth || 0)
    + Number(queues?.match_cache?.queued || 0) + Number(queues?.product_ram?.queued || 0);
  const stopTarget = matchCache.running ? String(matchCache.current || "") : "";
  const hasLog = ((milestones || []).length > 0);
  if (!hasLog && !visible.length && !externalQueued && !stopTarget
      && !(worker.running || []).length && !lookup.running) return null;
  return <div style={{ display: "grid", gap: 8, padding: "8px 10px", borderRadius: 7,
    border: "1px solid var(--border)", background: "var(--bg-card)" }}>
    <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
      <span style={{ fontSize: 13, fontWeight: 800 }}>캐시 작업 이력</span>
      <span style={{ fontSize: 11, color: "var(--text-secondary)", fontFamily: "monospace" }}>
        실행 {visible.filter(j => j.status === "running").length} · 외부 큐 {externalQueued}
      </span>
    </div>
    {/* 제품 단위 이력이 본문이다. 실패한 job만 따로 짚어주고, 정상 진행 중인
        job의 stage 칩·메모리 계측은 접어둔다 — 평소에 읽을 정보가 아니다. */}
    <MilestoneLog milestones={milestones} />
    {visible.filter(j => j.status === "failed").map(job => {
      const failedStages = (job.stages || []).filter(s => s.status === "failed");
      if (!failedStages.length) return null;
      return <div key={job.id} style={{ fontSize: 11, color: "var(--danger)", lineHeight: 1.5,
        padding: "6px 8px", borderRadius: 6, border: "1px solid var(--danger-line)",
        background: "rgba(239,68,68,0.05)" }}>
        <b>{job.label}</b>
        {failedStages.map(s => <div key={s.id}>✕ {s.label} 실패{s.detail?.error ? ` — ${s.detail.error}` : ""}</div>)}
      </div>;
    })}
    {visible.some(j => j.status === "running" && Number(j.idle_sec || 0) > 120) &&
      visible.filter(j => j.status === "running" && Number(j.idle_sec || 0) > 120).map(job =>
        <div key={`idle-${job.id}`} style={{ fontSize: 11, color: "var(--warn)" }}>
          ⚠ {job.label} — {fmtDur(job.idle_sec)} 동안 새 진행 로그가 없습니다
          {job.stale_after_sec ? ` (${fmtDur(job.stale_after_sec)}까지 없으면 자동 실패 처리)` : ""}
        </div>)}
    {visible.length > 0 && <details>
      <summary style={{ cursor: "pointer", fontSize: 11, color: "var(--text-secondary)" }}>
        작업 단계·메모리 계측 (내부 진단용)
      </summary>
      <div style={{ display: "grid", gap: 6, marginTop: 6 }}>
        {visible.map(job => <div key={job.id} style={{ display: "grid", gap: 4, padding: "6px 8px",
          borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
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
              {stage.peak_effective_gb > 0 && <> · Peak {Number(stage.peak_effective_gb).toFixed(2)}GB
                {` (+${Number(stage.peak_delta_gb || 0).toFixed(2)})`}</>}
            </span>)}
          </div>
          <div style={{ display: "flex", gap: 14, flexWrap: "wrap", fontSize: 11,
            color: "var(--text-secondary)", fontFamily: "monospace" }}>
            {job.last_event && <span style={{ color: "var(--text-primary)" }}>최근: {job.last_event}</span>}
            <span>실사용 {Number(job.current_effective_gb || 0).toFixed(2)}GB</span>
            <span>작업 Peak {Number(job.peak_effective_gb || 0).toFixed(2)}GB</span>
            <span>Peak 증가 +{Number(job.peak_delta_gb || 0).toFixed(2)}GB</span>
            <span>{job.memory_metric_kind === "container_working_set" ? "Grafana working set" : (job.memory_metric_kind || "fallback")}</span>
            <span>최저 여유 {Number(job.min_system_available_gb || 0).toFixed(2)}GB</span>
          </div>
        </div>)}
      </div>
    </details>}
    {stopTarget && <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap",
      padding: "6px 8px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
      <span style={{ fontSize: 12 }}>FAB 매칭 캐싱 중: <b>{stopTarget}</b></span>
      {canManage && <button
        onClick={() => onStopProduct && onStopProduct(stopTarget)}
        disabled={!!matchCache.cancel_product}
        style={{ fontSize: 11, padding: "3px 10px", borderRadius: 5, cursor: matchCache.cancel_product ? "wait" : "pointer",
          border: "1px solid var(--danger-line)", background: "transparent",
          color: matchCache.cancel_product ? "var(--text-secondary)" : "var(--danger)", fontWeight: 700 }}>
        {matchCache.cancel_product ? "중단 요청됨…" : "이 제품 중단"}
      </button>}
      <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>
        중단하면 이 제품은 건너뛰고 다음 제품으로 넘어갑니다. 이어받기가 없으므로 <b>다음 스캔에서 처음부터</b> 다시 빌드됩니다.
      </span>
      {Number(matchCache.cancelled_count || 0) > 0 && <span style={{ fontSize: 11, color: "var(--warn)", fontFamily: "monospace" }}>
        이번 작업에서 중단 {matchCache.cancelled_count}건</span>}
    </div>}
    {(externalQueued > 0 || (worker.running || []).length > 0 || lookup.running) &&
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", fontSize: 11,
        color: "var(--text-secondary)", fontFamily: "monospace" }}>
        <span>개발 워커: 실행 {(worker.running || []).length} / 대기 {worker.depth || 0}</span>
        <span>worker 실사용 RAM {Number(worker.load?.mem_effective_gb || 0).toFixed(2)}GB</span>
        <span>lookup: {lookup.current || "-"} / 대기 {(lookup.queued || []).length}</span>
        <span>Root 유휴 예열: {rootPrefetch.current_root || "-"} / 대기 {rootPrefetch.depth || 0}</span>
        <span>FAB 대기 {queues?.match_cache?.queued || 0}</span>
        <span>제품 RAM 대기 {queues?.product_ram?.queued || 0}</span>
        {worker.overloaded_reason && <span style={{ color: "var(--danger)" }}>worker guard: {worker.overloaded_reason}</span>}
      </div>}
  </div>;
}

function fmtScheduleAt(value) {
  if (!value) return "미정";
  const ms = Date.parse(value);
  if (Number.isNaN(ms)) return String(value).replace("T", " ").slice(0, 16);
  return new Date(ms).toLocaleString("ko-KR", {
    timeZone: "Asia/Seoul", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

function activeProductQueue(queues) {
  const candidates = [
    ["lookup_build", "ML lookup", queues?.lookup_build],
  ];
  return candidates.find(([, , q]) => q?.running || q?.current) || null;
}

/** 캐싱 진행의 운영자 뷰. 기술 로그보다 현재·다음·예약 시각과 제품 순서를 먼저 보여준다. */
function CachingScheduleBoard({ jobs, queues, scanQueue, canManage, onCancelTask, onStopProduct, milestones }) {
  const [techOpen, setTechOpen] = useStickyOpen("flow.ramcache.techOpen", false);
  const gateCurrent = scanQueue?.current || null;
  const runningJobs = (jobs || []).filter(j => j.status === "running");
  // 여러 서버/보조 job이 섞여도 실제 gate task와 같은 job을 현재 단계로 쓴다.
  const runningJob = runningJobs.find(j => gateCurrent?.id && j.task_id === gateCurrent.id)
    || runningJobs.find(j => gateCurrent?.product && j.product === gateCurrent.product)
    || runningJobs[0];
  const active = activeProductQueue(queues);
  const activeQueue = active?.[2] || {};
  const schedules = queues?.schedules || [];
  const rotation = schedules.find(s => s.key === "product_rotation") || queues?.auto_product_cache || null;
  const order = (activeQueue.order || []).map(String).filter(Boolean);
  // 현재는 실제 scan gate/job만 정본이다. rotation.queued_product 는 아직
  // 대기열에 있는 다음 제품이므로 현재로 표시하면 이벤트 로그와 어긋난다.
  const currentProduct = String(gateCurrent?.product || runningJob?.product || activeQueue.current || rotation?.current_product || "");
  const currentIndex = currentProduct ? order.indexOf(currentProduct) : -1;
  const nextTask = (scanQueue?.pending || [])[0] || null;
  const nextProduct = String(nextTask?.product || rotation?.queued_product || rotation?.next_product || (currentIndex >= 0 ? order[currentIndex + 1] : order[Number(activeQueue.done || 0)]) || "");
  const stage = runningJob ? currentStageLabel(runningJob) : "";
  const nextSchedule = schedules
    .filter(s => s.started && s.enabled !== false && s.next_at)
    .map(s => ({ ...s, ms: Date.parse(s.next_at) }))
    .filter(s => !Number.isNaN(s.ms))
    .sort((a, b) => a.ms - b.ms)[0];
  const flowStages = (runningJob?.stages?.length ? runningJob.stages : [
    // 유휴 상태의 숫자는 실행 순서일 뿐 성공 표시가 아니다. 실제 성공/실패는
    // 아래 제품별 4종 상태만 정본으로 삼는다.
    { id: "lookup_build", label: "랏 lookup", status: "queued" },
    { id: "pivot_build", label: "SplitTable pivot", status: "queued" },
    { id: "latest_lot", label: "WIP latest-lot", status: "queued" },
    { id: "fab_index", label: "FAB latest 인덱스", status: "queued" },
  ]);
  const idle = !gateCurrent && !runningJob && !active && !nextTask;
  const summaryCard = (label, title, detail, tone = "var(--text-primary)") => (
    <div style={{ minWidth: 0, padding: "10px 12px", borderRadius: 8,
      border: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
      <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 14, fontWeight: 800, color: tone, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }} title={title}>{title}</div>
      <div style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 3, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }} title={detail}>{detail}</div>
    </div>
  );
  return <div style={{ display: "grid", gap: 10, padding: "12px", borderRadius: 9,
    border: `1px solid ${idle ? "var(--border)" : "var(--info-line)"}`,
    background: "var(--bg-card)" }}>
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
      <div>
        <div style={{ fontSize: 14, fontWeight: 800 }}>캐싱 진행 및 스케줄</div>
        <div style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 2 }}>제품 하나의 4개 캐시를 모두 완료한 뒤 다음 제품으로 즉시 이동합니다.</div>
      </div>
      <span style={{ fontSize: 12, fontWeight: 800, color: idle ? "var(--text-secondary)" : "var(--info)" }}>
        {idle ? "○ 대기 중" : "● 작업 중"}
      </span>
    </div>

    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))", gap: 8 }}>
      {summaryCard("현재", currentProduct || gateCurrent?.label || "진행 중인 작업 없음",
        [stage || active?.[1], currentProduct ? "제품 내 캐시 1개씩 순차 실행" : "", runningJob?.elapsed_sec != null ? `${fmtDur(runningJob.elapsed_sec)} 경과` : ""].filter(Boolean).join(" · ") || "스케줄러 대기",
        currentProduct || gateCurrent ? "var(--info)" : "var(--text-secondary)")}
      {summaryCard("다음", nextProduct || nextTask?.label || "대기 작업 없음",
        nextTask ? `실제 큐 대기 · 앞 작업 종료 직후 (대기 ${Number(scanQueue?.depth || 0)}건)` : nextProduct ? `${rotation?.next_after_current ? `현재 제품 4단계 완료 직후 · 예상 ${fmtScheduleAt(rotation?.next_at)}` : fmtScheduleAt(rotation?.next_at)}${rotation?.delayed ? ` · 밀림: ${rotation.delayed_reason || "앞 작업 진행 중"}` : ""}` : "새 예약을 기다립니다",
        nextProduct || nextTask ? "var(--warn)" : "var(--text-secondary)")}
      {summaryCard("다음 자동 실행", nextSchedule?.next_product || nextSchedule?.label || "예약 없음",
        nextSchedule ? `${nextSchedule.next_after_current ? "현재 제품 완료 직후" : fmtScheduleAt(nextSchedule.next_at)} · 전체 순환 후 ${nextSchedule.interval_minutes}분 휴식` : rotation?.enabled === false ? "캐시 예산 설정에서 비활성화됨" : "스케줄러 상태를 확인하세요",
        nextSchedule ? "var(--accent)" : "var(--text-secondary)")}
    </div>

    <div style={{ display: "flex", alignItems: "center", gap: 0, overflowX: "auto", padding: "2px 0 4px" }}>
      {flowStages.map((s, i) => {
        const running = s.status === "running" || s.id === runningJob?.current_stage || s.id === active?.[0];
        const done = s.status === "done";
        return <div key={s.id} style={{ display: "flex", alignItems: "center", flexShrink: 0 }}>
          <span style={{ padding: "4px 9px", borderRadius: 999, fontSize: 11, fontWeight: running ? 800 : 600,
            border: `1px solid ${running ? "var(--info)" : done ? "var(--ok-line)" : "var(--border)"}`,
            background: running ? "var(--info-50)" : done ? "var(--ok-50)" : "transparent",
            color: running ? "var(--info)" : done ? "var(--ok)" : "var(--text-secondary)" }}>
            {done ? "✓" : running ? "●" : i + 1} {s.label}
          </span>
          {i < flowStages.length - 1 && <span style={{ width: 20, height: 1, background: "var(--border)" }} />}
        </div>;
      })}
    </div>

    {order.length > 0 && <div style={{ display: "grid", gap: 5 }}>
      <div style={{ fontSize: 12, fontWeight: 700 }}>{active?.[1]} 제품 순서 <span style={{ color: "var(--text-secondary)", fontWeight: 400 }}>({Number(activeQueue.done || 0)}/{activeQueue.total || order.length} 완료)</span></div>
      <div style={{ display: "flex", gap: 5, overflowX: "auto", paddingBottom: 3 }}>
        {order.map((product, i) => {
          const isCurrent = product === currentProduct;
          const isDone = i < Number(activeQueue.done || 0);
          const isNext = product === nextProduct;
          return <span key={`${product}-${i}`} style={{ flexShrink: 0, padding: "3px 8px", borderRadius: 5, fontSize: 11,
            border: `1px solid ${isCurrent ? "var(--info)" : isNext ? "var(--warn-line)" : "var(--border)"}`,
            background: isCurrent ? "var(--info-50)" : isNext ? "var(--warn-50)" : "transparent",
            color: isCurrent ? "var(--info)" : isDone ? "var(--ok)" : isNext ? "var(--warn)" : "var(--text-secondary)",
            fontWeight: isCurrent || isNext ? 800 : 500 }}>
            {isDone ? "✓ " : isCurrent ? "● " : isNext ? "다음 " : `${i + 1}. `}{product}
          </span>;
        })}
      </div>
    </div>}

    <div style={{ display: "grid", gap: 4 }}>
      <div style={{ fontSize: 12, fontWeight: 700 }}>자동 실행 일정</div>
      {schedules.map(s => <div key={s.key} style={{ display: "grid", gridTemplateColumns: "minmax(160px, 1fr) 96px minmax(220px, 1.4fr)", gap: 8,
        alignItems: "center", padding: "4px 7px", borderTop: "1px solid var(--border)", fontSize: 11 }}>
        <b>{s.label}</b>
        <span style={{ color: s.started && s.enabled !== false ? "var(--ok)" : "var(--danger)" }}>
          {s.started && s.enabled !== false ? `1바퀴 후 ${s.interval_minutes}분` : "꺼짐"}
        </span>
        <span style={{ color: s.delayed ? "var(--warn)" : "var(--text-secondary)" }}>
          {s.current_product
            ? `현재 ${s.current_product} · 다음 ${s.next_product || "-"} ${s.next_after_current ? "(현재 제품 완료 직후)" : fmtScheduleAt(s.next_at)}`
            : s.queued_product
              ? `실제 큐 대기 ${s.queued_product} · 이후 ${s.next_product || "-"} ${fmtScheduleAt(s.next_at)}`
            : s.enabled === false
              ? "자동 실행 비활성"
              : `다음 ${s.next_product || "-"} · ${fmtScheduleAt(s.next_at)}`}
          {s.delayed && <> · 밀림 ({s.delayed_reason || "앞 작업 진행 중"})</>}
        </span>
      </div>)}
    </div>

    {(gateCurrent || (scanQueue?.pending || []).length > 0) && <div style={{ display: "grid", gap: 4, paddingTop: 2 }}>
      <div style={{ fontSize: 12, fontWeight: 700 }}>작업 대기열</div>
      {[gateCurrent, ...(scanQueue?.pending || [])].filter(Boolean).map((task, i) => <div key={task.id || i}
        style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0, padding: "4px 7px", borderTop: "1px solid var(--border)", fontSize: 11 }}>
        <span style={{ width: 44, flexShrink: 0, color: i === 0 ? "var(--info)" : "var(--warn)", fontWeight: 700 }}>{i === 0 ? "실행" : `대기 ${i}`}</span>
        <b style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{task.label}</b>
        <span style={{ marginLeft: "auto", color: "var(--text-secondary)", whiteSpace: "nowrap" }}>{i === 0 ? `${fmtDur(task.elapsed_sec)} 경과` : `${fmtDur(task.waited_sec)} 대기`}</span>
        {canManage && task.id && <button onClick={() => onCancelTask(task, i === 0)} style={scanCancelBtn}>{i === 0 ? "중단" : "취소"}</button>}
      </div>)}
    </div>}

    {/* 기술 진단은 선택적으로 접되 사용자가 고른 상태는 기억한다. 이벤트 로그는 아래에서 항상 펼친다. */}
    <details open={techOpen} onToggle={e => setTechOpen(e.currentTarget.open)}>
      <summary style={{ cursor: "pointer", fontSize: 11, color: "var(--text-secondary)" }}>기술 진행 정보·메모리·외부 큐 보기</summary>
      <div style={{ marginTop: 8 }}><CacheJobPanel jobs={jobs} queues={queues} canManage={canManage} onStopProduct={onStopProduct} milestones={milestones} /></div>
    </details>
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
  const [mainTab, setMainTab] = useState("jobs");
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
  // 우선적재(주요 Lot) 미등록 제품의 적재 순서 기준 — step_id 숫자 임계값
  const [stepThresholdDraft, setStepThresholdDraft] = useState("");
  const [budgetSaving, setBudgetSaving] = useState(false);
  // 관리자 전용 — 캐시 수동 스캔 (통합)
  const [unifiedScanBusy, setUnifiedScanBusy] = useState(false);
  const [productCacheStatus, setProductCacheStatus] = useState(null);
  const [rootLotCacheStatus, setRootLotCacheStatus] = useState(null);
  const [queryWorkersStatus, setQueryWorkersStatus] = useState(null);
  const [queryWorkersDraft, setQueryWorkersDraft] = useState(3);
  const [queryWorkersSaveBusy, setQueryWorkersSaveBusy] = useState(false);
  // 관리자 전용 — 검색 속도(히트/미스) 패널. 측정(이 패널)과 튜닝(쿼리 코어·⚙️ 슬롯)이
  // 같은 페이지에 있어야 한다 — 예전엔 타이밍이 활동 대시보드(My_Admin)에 있어
  // 루프가 두 페이지에 쪼개져 있었다.
  const [timing, setTiming] = useState(null);
  const [timingHours, setTimingHours] = useState(24);
  // 검색 기록은 운영/개발이 **같은 공유 JSONL** 에 쌓인다. 기본은 '이 서버'로 본다 —
  // 예전엔 항상 합산이라, 개발서버에서 열어도 운영 검색까지 섞인 히트율이 떠서
  // "제품별 현황은 0인데 히트율 100%" 로 읽혔다.
  const [timingScope, setTimingScope] = useState("this");
  // 단계별 배분을 전체/미스/히트 중 무엇으로 볼지. 미스가 기본이 아닌 이유는
  // 히트가 느린 경우(캐시 밖 비용)도 똑같이 흔하기 때문이다.
  const [phaseScope, setPhaseScope] = useState("all");
  // 관리자 전용 — 캐시 이벤트 로그 + Peak RAM
  const [cacheEventLog, setCacheEventLog] = useState(null);
  const [cacheEventLogFilter, setCacheEventLogFilter] = useState("");
  // 지금 돌고 있는 빌드의 "몇 랏 중 몇 랏". 끝난 작업은 여기서 빠진다.
  const [cacheProgress, setCacheProgress] = useState(null);
  // 제품 × 캐싱 작업별 최근 성공/실패. 평소에 보는 건 진행률이 아니라 이쪽이다.
  const [productStatus, setProductStatus] = useState(null);
  // 제품은 한 줄 요약으로 두고 사용자가 고른 제품만 상세 4종을 펼친다.
  const [expandedCacheProducts, setExpandedCacheProducts] = useState({});
  // 제품 단위 시작/완료/실패만 뽑은 이력 — 위 진행 카드의 본문.
  const [cacheMilestones, setCacheMilestones] = useState([]);
  // 폴링 자체가 살아 있는지. tick 은 성공/실패와 무관하게 오르며 다음 주기를 건다.
  const [cacheLoadError, setCacheLoadError] = useState("");
  const [pollTick, setPollTick] = useState(0);
  const [peakRam, setPeakRam] = useState(null);
  const [cacheJobs, setCacheJobs] = useState([]);
  const [cacheQueues, setCacheQueues] = useState(null);
  // 서버 스캔 게이트 — 한 서버에서 스캔은 하나만 돌고 나머지는 대기열에 선다.
  const [scanQueue, setScanQueue] = useState(null);
  // 관리자 전용 — 자동 일정·캐싱 속도·검색 슬롯만 제공하는 톱니바퀴
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
          view_cold_concurrency: s.view_cold_concurrency ?? "",
          view_cold_concurrency_dev: 1,
          cache_speed_level: s.cache_speed_level ?? 1,
          cache_speed_level_dev: s.cache_speed_level_dev ?? (s.cache_speed_level ?? 1),
          // 자동 제품 캐싱 기본은 **꺼짐**이다. 저장된 값이 없을 때 true 로 두면
          // 톱니바퀴를 열기만 해도 체크가 켜져 보이고, 저장하는 순간 서버 기본값과
          // 무관하게 자동 캐싱이 실제로 켜진다.
          auto_product_cache_enabled: s.auto_product_cache_enabled ?? false,
          auto_product_cache_enabled_dev: s.auto_product_cache_enabled_dev ?? (s.auto_product_cache_enabled ?? false),
          auto_product_cache_interval_minutes: s.auto_product_cache_interval_minutes ?? "",
          auto_product_cache_interval_minutes_dev: s.auto_product_cache_interval_minutes_dev ?? "",
        });
      })
      .catch(e => toast.error("예산 설정 로드 실패: " + (e?.message || e)));
  }, []);

  const openBudgetModal = () => { setBudgetModalOpen(true); loadBudgetCfg(); };

  const saveBudgetCfg = () => {
    setBudgetCfgSaving(true);
    const num = v => (v === "" || v === null || v === undefined ? null : Number(v));
    const payload = {
      view_cold_concurrency: num(budgetForm.view_cold_concurrency),
      view_cold_concurrency_dev: 1,
      cache_speed_level: num(budgetForm.cache_speed_level),
      cache_speed_level_dev: num(budgetForm.cache_speed_level_dev),
      auto_product_cache_enabled: !!budgetForm.auto_product_cache_enabled,
      auto_product_cache_enabled_dev: !!budgetForm.auto_product_cache_enabled_dev,
      auto_product_cache_interval_minutes: num(budgetForm.auto_product_cache_interval_minutes),
      auto_product_cache_interval_minutes_dev: num(budgetForm.auto_product_cache_interval_minutes_dev),
    };
    postJson(API + "/cache-budget/settings/save", payload)
      .then(d => { setBudgetCfg(d); toast.ok("캐시 설정 저장됨"); loadOverview(); })
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
  }, []);

  const loadLotStatus = useCallback((prod) => {
    if (!prod) return;
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
        setStepThresholdDraft(pb ? String(pb.step_threshold ?? "") : "");
      })
      .catch(() => {});
  }, []);

  // 관리자 중단 — 지금 캐싱 중인 제품만 끊고 다음 제품으로 넘긴다. 부분 산출은
  // 버려지므로 이 제품은 다음 스캔에서 처음부터 다시 빌드된다.
  const stopMatchCacheProduct = useCallback((product) => {
    if (!product) return;
    if (!confirm(`'${product}' 캐싱을 중단할까요?\n\n지금까지 만든 부분 결과는 버려지고 다음 제품으로 넘어갑니다.\n이 제품은 다음 스캔에서 처음부터 다시 빌드됩니다.`)) return;
    sf(API + "/match-cache/stop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ product }),
    })
      .then(() => toast.info(`${product} 중단을 요청했습니다 — 현재 배치가 끝나는 대로 다음 제품으로 넘어갑니다`))
      .catch(e => toast.error(e.message || "중단 요청 실패"));
  }, []);

  // 스캔 큐 작업 취소 — 대기 중이면 큐에서 바로 빼고, 실행 중이면 안전한
  // 지점(제품/배치 경계)에서 접도록 요청한다. 강제 종료가 아니다.
  const cancelScanTask = useCallback((task, running) => {
    const id = task?.id;
    if (!id) return;
    const label = task.label || id;
    const msg = running
      ? `'${label}' 를 중단할까요?\n\n즉시 멈추지 않고 현재 제품/배치가 끝나는 대로 접고 다음 대기 작업으로 넘어갑니다.\n이어받기가 없어 다음 스캔에서 처음부터 다시 빌드합니다.\n(이미 완성된 제품 캐시는 그대로 남습니다)`
      : `대기 중인 '${label}' 를 큐에서 뺄까요?`;
    if (!confirm(msg)) return;
    postJson(API + "/scan-queue/cancel", { task_id: id })
      .then(d => {
        setScanQueue(d.queue || null);
        toast.info(d.state === "removed"
          ? `대기 작업을 큐에서 제거했습니다 — ${label}`
          : `중단을 요청했습니다 — 현재 제품/배치가 끝나는 대로 다음 작업으로 넘어갑니다`);
      })
      .catch(e => toast.error(e.message || "중단 요청 실패"));
  }, []);

  const loadQueryWorkers = useCallback(() => {
    sf(API + "/query-workers")
      .then(d => { setQueryWorkersStatus(d); setQueryWorkersDraft(d.desired || d.configured || 1); })
      .catch(() => {});
  }, []);
  const loadCacheEventLog = useCallback((cat) => {
    sf(API + "/cache-event-log" + qs({ category: cat }))
      .then(d => {
        setCacheEventLog(d.events || []); setPeakRam(d.peak_ram || null);
        setCacheJobs(d.jobs || []); setCacheQueues(d.queues || null);
        setScanQueue(d.scan_queue || null); setCacheProgress(d.progress || null);
        setProductStatus(d.product_status || null); setCacheMilestones(d.milestones || []);
        setUnifiedScanBusy((d.jobs || []).some(job => job.status === "running"));
      })
      .then(() => setCacheLoadError(""))
      // 실패해도 마지막으로 받은 값을 지우지 않는다. 예전에는 전부 null 로
      // 비워서 서버 재시작 같은 한 번의 실패로 화면이 통째로 텅 비었고, 그게
      // "캐시가 안 돌고 있다"로 오해됐다. 대신 갱신 실패만 위에 알린다.
      .catch(e => setCacheLoadError(e?.message || "갱신 실패"))
      // 성공이든 실패든 tick 을 올린다 — 아래 폴링 effect 가 이 값으로 다음
      // 주기를 건다. 예전에는 성공 시 새로 만들어지는 cacheJobs 배열이 유일한
      // 재실행 트리거라, 한 번만 실패해도 폴링이 영영 멈췄다(화면 정지).
      .finally(() => setPollTick(t => t + 1));
  }, []);
  const loadTiming = useCallback((hours, scope) => {
    sf(API + "/search-timings" + qs({ hours, limit: 200, origin: scope === "all" ? "" : "__self__" }))
      .then(setTiming)
      .catch(() => setTiming(null));
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
  useEffect(() => {
    if (!canManage) return;
    loadTiming(timingHours, timingScope);
  }, [canManage, timingHours, timingScope, loadTiming]);
  // 캐시 작업 진행 폴링 — 수동 스캔뿐 아니라 **예약/자동 캐싱**도 같은 화면에
  // 실시간으로 보이게 한다. 실행 중인 작업이 있으면 2.5초, 없으면 15초 간격.
  // (수동 스캔 중에는 startScanAndPoll 의 인터벌이 담당하므로 중복 폴링 생략)
  //
  // 다음 주기는 `pollTick` 이 건다. 이전에는 성공 응답에서 새로 만들어지는
  // `cacheJobs` 배열이 유일한 재실행 트리거였고, 실패 경로는 그 상태를 건드리지
  // 않아 **한 번의 실패로 폴링이 영구히 멈췄다** — 서버 재시작 중 요청 하나가
  // 어긋나면 화면이 그대로 굳어 아무것도 갱신되지 않았다.
  useEffect(() => {
    if (!canManage || unifiedScanBusy) return;
    const busyJob = (cacheJobs || []).some(job => job.status === "running");
    const timer = setTimeout(() => loadCacheEventLog(cacheEventLogFilter), busyJob ? 2500 : 15000);
    return () => clearTimeout(timer);
  }, [canManage, unifiedScanBusy, cacheJobs, cacheEventLogFilter, loadCacheEventLog, pollTick]);
  useEffect(() => {
    if (!selProd) return;
    loadPriority(selProd);
    loadContents(selProd);
    loadBudgets(selProd);
    // 원본/인덱스 스캔 가능성이 있는 lot 위치 계산은 첫 화면이 그려진 뒤 시작한다.
    const t = setTimeout(() => {
      loadLotStatus(selProd);
      if (canManage) {
        reloadProductCacheStatus(selProd);
        reloadRootLotCacheStatus(selProd);
      }
    }, 750);
    return () => clearTimeout(t);
  }, [selProd, canManage, loadPriority, loadLotStatus, loadContents, loadBudgets, reloadProductCacheStatus, reloadRootLotCacheStatus]);

  // 통합 스캔/전체 셋업 공용 — 시작 요청 후 진행 로그를 실시간 폴링한다.
  // 탭 이탈(언마운트) 시 정리는 공용 훅이 보장한다 — 이전엔 최대 1시간 동안
  // 유령 폴링이 남았고, 같은 타이머/실패 카운트 로직이 화면마다 중복됐다.
  const scanPoll = usePolling();
  const startScanAndPoll = (reqPromise, okMsg, maxTicks = 240) => {
    setUnifiedScanBusy(true);
    reqPromise
      .then(r => {
        // 스캔은 서버당 하나만 돈다. 다른 스캔이 진행 중이면 거절이 아니라
        // 대기열에 들어가고, 앞 작업이 끝나면(실패해도) 이어서 실행된다.
        if (r.ok === false) toast.warn(r.detail || "스캔을 시작할 수 없습니다.");
        else if (r.duplicate) toast.warn(r.detail || "같은 스캔이 이미 대기 중입니다.");
        else if (r.ahead > 0) toast.warn(`다른 스캔이 진행 중 — 대기열 ${r.ahead + 1}번째로 등록했습니다. `
          + "앞 작업이 끝나는 대로(실패해도) 이어서 실행됩니다.");
        else if (r.queued) toast.ok(okMsg);
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
        const finishPolling = () => {
          scanPoll.stop();
          setUnifiedScanBusy(false);
          loadCacheEventLog(scanLogFilter);
          reloadProductCacheStatus(selProd);
          reloadRootLotCacheStatus(selProd);
          loadOverview();
          if (selProd) loadContents(selProd);
        };
        let ticks = 0;
        // 폴링 자체(타이머 정리·연속 실패 중단·상한)는 공용 훅이 맡는다.
        scanPoll.start(() => sf(API + "/ram-cache/scan-status"), {
          intervalMs: 2500,
          maxErrors: 5,             // 상태 조회가 연속 실패하면 조용히 도는 대신 중단
          maxTicks: maxTicks,
          onData: (st) => {
            ticks += 1;
            loadCacheEventLog(scanLogFilter);
            if (ticks % 2 === 0) {   // 5초마다 현황/상태도 갱신
              reloadProductCacheStatus(selProd);
              reloadRootLotCacheStatus(selProd);
              loadOverview();
              if (selProd) loadContents(selProd);
            }
            setCacheJobs(st.jobs || []); setCacheQueues(st.queues || null);
            setScanQueue(st.scan_queue || null);
            if (!st.running) {
              // 끝났으면 성공/실패를 반드시 알린다 — 예전엔 조용히 멈춰
              // 실패해도 화면상 '그냥 끝난' 것처럼 보였다.
              const failed = (st.last_stages || []).filter(x => x.status === "failed");
              if (st.last_status === "failed" || failed.length) {
                toast.error("캐시 작업 실패 — "
                  + (failed.length
                    ? failed.map(x => `${x.label}${x.error ? `: ${x.error}` : ""}`).join(" / ")
                    : "자세한 사유는 아래 캐시 이벤트 로그를 확인하세요"));
              } else {
                toast.ok("캐시 작업 완료");
              }
              finishPolling();
            }
          },
          onError: (e, reason) => {
            if (reason === "timeout") {
              // 서버 작업은 계속 진행 중 — 화면 폴링만 멈춘다(무한 폴링 방지).
              toast.warn("작업이 아직 진행 중입니다 — 자동 갱신을 멈춥니다. "
                + "'새로고침'으로 진행 상황을 계속 확인할 수 있습니다.");
            } else {
              toast.error("진행 상태를 확인할 수 없습니다 (" + (e?.message || e) + ") — 자동 갱신을 중단합니다.");
            }
            finishPolling();
          },
        });
      })
      .catch(e => { toast.error("스캔 시작 실패: " + (e?.message || e)); setUnifiedScanBusy(false); });
  };

  const runUnifiedScan = () => startScanAndPoll(
    postJson(API + "/ram-cache/unified-scan", { product: selProd || "", force: true }),
    `필수 캐시 작업이 큐에 등록됩니다 (${selProd || "전체 제품"}) — lookup/pivot/WIP latest/FAB index.`,
  );

  const runFullSetup = () => {
    if (!window.confirm(
      "전체 셋업(초기 1회)을 시작합니다.\n\n" +
      "· 개발 워커로 넘기지 않고 운영 서버에서 직접 처리\n" +
      "· 서버당 제품 1개씩 순차로 전 제품 캐시(랏→매칭→제품RAM→예열)를 빌드\n" +
      "· 제품 수가 많으면 오래 걸릴 수 있고 운영 서버 자원을 많이 사용합니다.\n\n계속할까요?"
    )) return;
    startScanAndPoll(
      postJson(API + "/ram-cache/full-setup"),
      "전체 셋업 시작됨 — 운영 로컬에서 전 제품 캐시를 제품별로 순차 빌드합니다.",
      1440,   // 전체 셋업은 오래 걸릴 수 있어 폴링 상한을 1시간으로
    );
  };
  const saveQueryWorkers = () => {
    setQueryWorkersSaveBusy(true);
    postJson(API + "/query-workers/save", { query_workers: queryWorkersDraft })
      .then(d => {
        setQueryWorkersStatus(d); setQueryWorkersDraft(d.desired || d.configured || 1);
        toast.ok(d.is_dev ? "개발 서버는 SplitTable 검색 1코어로 고정됩니다" : "운영 검색 코어 저장됨 · 서버 재시작 후 적용");
      })
      .catch(e => toast.error("쿼리 워커 수 저장 실패: " + (e?.message || e)))
      .finally(() => setQueryWorkersSaveBusy(false));
  };

  const savePriority = (lots) => {
    postJson(API + "/ram-cache/priority-lots/save", { product: selProd, lots })
      .then(() => { toast.ok("주요 lot 저장됨"); loadPriority(selProd); loadOverview(); })
      .catch(e => toast.error("저장 실패: " + (e?.message || e)));
  };

  // 검색 속도 패널의 "미스 반복 root" → 원클릭 주요 Lot 등록.
  // 저장은 product 전체 교체 방식이라, 먼저 현재 목록을 읽어 뒤에 붙인다.
  const registerMissRoot = (product, rootId) => {
    if (!product || !rootId) { toast.warn("product 정보가 없어 등록할 수 없습니다"); return; }
    sf(API + "/ram-cache/priority-lots" + qs({ product }))
      .then(d => {
        const lots = d.lots || [];
        if (lots.some(l => ((l.root_lot_id || (l.lot_id || "").slice(0, 5)).toUpperCase()) === rootId)) {
          toast.warn(`${rootId} 은 이미 ${product} 주요 Lot에 등록되어 있습니다`);
          return null;
        }
        const next = [...lots, { lot_id: rootId, purpose: "", comment: "검색 미스 반복 — 속도 패널에서 등록", cache_enabled: true }];
        return postJson(API + "/ram-cache/priority-lots/save", { product, lots: next })
          .then(() => {
            toast.ok(`${rootId} → ${product} 주요 Lot 등록됨 — 다음 예열 때 우선 적재됩니다`);
            if (selProd === product) loadPriority(product);
            loadOverview();
          });
      })
      .catch(e => toast.error("등록 실패: " + (e?.message || e)));
  };

  // 전체 캐시 표의 개별 축출 — 개발서버에서 "축출 → 재검색(미스 측정) → 재적재 후
  // 재검색(히트 측정)" 흐름의 도구. (활동 대시보드의 RAM 캐시 항목 표를 이 페이지로 이전)
  const evictCacheEntry = (entry) => {
    postJson(API + "/root-lot-cache/evict", { source_path: entry.source_path || "", root_lot_id: entry.root_lot_id })
      .then(() => { toast.ok(`${entry.root_lot_id} 캐시에서 제거됨`); loadContents(selProd); loadOverview(); })
      .catch(e => toast.error("제거 실패: " + (e?.message || e)));
  };

  const saveBudget = () => {
    if (!selProd) return;
    setBudgetSaving(true);
    const payload = { product: selProd, max_roots: Number(budgetDraft) || 1000 };
    if (budgetDraftDev !== "") payload.max_roots_dev = Number(budgetDraftDev) || 200;
    // 빈칸이면 기존/기본값 유지 — 0 은 "이 규칙 끄기"라 명시적으로 보낸다.
    if (stepThresholdDraft !== "") payload.step_threshold = Number(stepThresholdDraft) || 0;
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
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, marginBottom: 16 }}>
        <div style={{ display: "flex", flex: 1, maxWidth: 450 }}>
          <TabStrip active={mainTab} onChange={setMainTab}
            items={[
              { k: "jobs", l: "캐싱 진행·스케줄" },
              { k: "speed_config", l: "검색 속도 & 설정" },
            ]} />
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button onClick={refreshAll} style={{ ...S_BTN, color: "var(--accent)" }}>새로고침</button>
          {/* 톱니는 공용 PageGearButton 으로 통일 (40x40 원형 ⚙️).
              예전엔 이 페이지만 텍스트 글리프 ⚙ + 라벨 버튼이라 혼자 달라 보였다. */}
          {canManage && <PageGearButton title="캐시 설정" position="inline" onClick={openBudgetModal} />}
        </div>
      </div>

      {/* 단순 캐시 설정 모달 (톱니바퀴) */}
      {budgetModalOpen && canManage && (
        <div onClick={() => setBudgetModalOpen(false)}
          style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.45)", zIndex: 1000,
            display: "flex", alignItems: "flex-start", justifyContent: "center", padding: "6vh 16px", overflow: "auto" }}>
          <div onClick={e => e.stopPropagation()}
            style={{ width: "min(560px, 100%)", background: "var(--bg-card)", border: "1px solid var(--border)",
              borderRadius: 12, padding: 18, display: "grid", gap: 14 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontSize: 16, fontWeight: 800 }}>⚙️ 캐시 설정</span>
              <button onClick={() => setBudgetModalOpen(false)} style={{ ...S_BTN, padding: "2px 8px" }}>✕</button>
            </div>
            {budgetCfg && <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.6 }}>
              현재 서버: <b>{budgetCfg.is_dev ? "개발" : "운영"}</b> · 복잡한 RAM 예산은 서버가 자동으로 안전하게 관리합니다.
            </div>}
            {budgetCfg && (() => {
              const eff = budgetCfg.effective || {}; const pins = budgetCfg.env_pins || {};
              return <div style={{ display: "grid", gap: 8 }}>
                <div style={{ display: "grid", gap: 7, padding: "10px", borderRadius: 8,
                  border: "1px solid var(--accent)", background: "var(--accent-glow)" }}>
                  <div style={{ fontSize: 13, fontWeight: 800 }}>자동 제품 캐싱 일정</div>
                  <div style={{ fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.55 }}>
                    캐시 종류마다 별도 스케줄을 만들지 않고 제품별 전체 파이프라인을 연속 실행합니다. 한 서버에서는 한 제품의 한 캐시 단계만 실행하며,
                    lookup → SplitTable pivot → WIP latest-lot → FAB latest를 모두 완료한 뒤 다음 제품을 즉시 시작합니다.
                    모든 제품을 한 바퀴 돈 뒤에만 아래 간격만큼 쉽니다.
                    다른 SplitTable 작업이 길어지면 다음 제품 예정 시각도 실제 대기만큼 뒤로 조정됩니다.
                  </div>
                  <div style={{ fontSize: 11, color: "var(--warn)" }}>
                    운영·개발 모두 <b>기본은 꺼짐</b>입니다 — 여기서 켜야 자동 순환이 시작됩니다.
                    꺼져 있어도 위 <b>수동 캐싱</b> 버튼은 언제든 사용할 수 있고, 누른 서버에서 바로 실행됩니다.
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "72px 90px minmax(120px, 1fr)", gap: 8, alignItems: "center", fontSize: 12 }}>
                    <b>서버</b><b>자동 실행</b><b>전체 순환 후 휴식(분)</b>
                    <span>운영</span>
                    <label style={{ display: "flex", alignItems: "center", gap: 4 }}>
                      <input type="checkbox" checked={!!budgetForm.auto_product_cache_enabled}
                        disabled={pins.auto_product_cache_enabled}
                        onChange={e => setBudgetForm(f => ({ ...f, auto_product_cache_enabled: e.target.checked }))} /> 사용
                    </label>
                    <input type="number" min="1" max="1440" step="1"
                      value={budgetForm.auto_product_cache_interval_minutes ?? ""}
                      disabled={pins.auto_product_cache_interval_minutes}
                      placeholder={String(budgetCfg.defaults?.auto_product_cache_interval_minutes ?? 15)}
                      onChange={e => setBudgetForm(f => ({ ...f, auto_product_cache_interval_minutes: e.target.value }))}
                      style={{ ...S_INPUT, width: 118, fontFamily: "monospace" }} />
                    <span>개발</span>
                    <label style={{ display: "flex", alignItems: "center", gap: 4 }}>
                      <input type="checkbox" checked={!!budgetForm.auto_product_cache_enabled_dev}
                        disabled={pins.auto_product_cache_enabled}
                        onChange={e => setBudgetForm(f => ({ ...f, auto_product_cache_enabled_dev: e.target.checked }))} /> 사용
                    </label>
                    <input type="number" min="1" max="1440" step="1"
                      value={budgetForm.auto_product_cache_interval_minutes_dev ?? ""}
                      disabled={pins.auto_product_cache_interval_minutes}
                      placeholder="운영값 상속"
                      onChange={e => setBudgetForm(f => ({ ...f, auto_product_cache_interval_minutes_dev: e.target.value }))}
                      style={{ ...S_INPUT, width: 118, fontFamily: "monospace" }} />
                  </div>
                  <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>
                    현재 서버 적용: <b>{eff.auto_product_cache_enabled ? "사용" : "꺼짐"}</b>
                    {eff.auto_product_cache_enabled && <> · 전체 순환 후 <b>{eff.auto_product_cache_interval_minutes}분 휴식</b></>}
                    {budgetCfg.auto_schedule?.next_product && <> · 다음 <b>{budgetCfg.auto_schedule.next_product}</b> {fmtScheduleAt(budgetCfg.auto_schedule.next_at)}</>}
                  </div>
                </div>
                <div style={{ display: "grid", gap: 8, padding: "10px", borderRadius: 8,
                  border: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
                  <div style={{ fontSize: 13, fontWeight: 800 }}>캐싱 속도 (1~5단계)
                    {pins.cache_speed_level && <span style={{ fontSize: 10, color: "var(--warn)" }}> · env 고정</span>}
                  </div>
                  <div style={{ fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.55 }}>
                    랏 lookup과 Pivot 캐시의 처리 묶음을 함께 조절합니다. 기본 1단계는 root를 1개씩 처리해
                    가장 안정적이며, 단계가 높을수록 더 많은 root를 묶어 빠르게 진행합니다.
                    현재 적용: <b>{eff.cache_speed_level}단계</b> · lookup {eff.lookup_build_chunk_roots}개 / pivot {eff.pivot_build_chunk_roots}개
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "72px minmax(180px, 1fr) 48px", gap: 8, alignItems: "center", fontSize: 12 }}>
                    <b>운영</b>
                    <input type="range" min="1" max="5" step="1" value={budgetForm.cache_speed_level ?? 1}
                      disabled={pins.cache_speed_level}
                      onChange={e => setBudgetForm(f => ({ ...f, cache_speed_level: Number(e.target.value) }))} />
                    <b>{budgetForm.cache_speed_level ?? 1}단계</b>
                    <b>개발</b>
                    <input type="range" min="1" max="5" step="1" value={budgetForm.cache_speed_level_dev ?? 1}
                      disabled={pins.cache_speed_level}
                      onChange={e => setBudgetForm(f => ({ ...f, cache_speed_level_dev: Number(e.target.value) }))} />
                    <b>{budgetForm.cache_speed_level_dev ?? 1}단계</b>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10.5, color: "var(--text-secondary)" }}>
                    <span>1 · 안정적</span><span>2</span><span>3</span><span>4</span><span>5 · 빠름</span>
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
                    <u>이 페이지의 &apos;검색 속도&apos; 패널에서 &apos;대기&apos; 수치가 지속적으로 클 때만 올리세요</u>
                    (대기는 작은데 &apos;계산&apos;이 크면 슬롯을 늘려도 나아지지 않습니다).
                    운영 빈칸/0 = 기본({budgetCfg.defaults?.view_cold_concurrency ?? 3}).
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
                      <input type="number" step="1" min="1" max="1" value="1" disabled
                        title="개발 서버는 1슬롯으로 고정됩니다"
                        style={{ ...S_INPUT, width: 110, fontFamily: "monospace" }} /></label>
                  </div>
                  <div style={{ fontSize: 10.5, color: "var(--text-secondary)" }}>
                    운영 설정은 저장 즉시 적용됩니다. 개발 서버는 1슬롯 × 1코어로 고정됩니다.
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

      
      {mainTab === "products" && (
        <div style={{ display: "grid", gap: 14 }}>
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
            {memOverview.process?.container_working_set_gb > 0 && <> · Grafana working set {memOverview.process.container_working_set_gb} GB</>}
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
        {/* 예열 상태 요약 — "이 표가 0인 이유"를 표 위에서 먼저 설명한다. 이 표는
            **이 서버 프로세스의 root lot RAM 캐시**만 센다(아래 검색 히트율과
            세는 대상이 다르다). */}
        {overview?.warmup && <WarmupBanner w={overview.warmup} isDev={overview.is_dev} />}
        <div style={{ borderRadius: 8, border: "1px solid var(--border)", overflow: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ background: "var(--bg-secondary)" }}>
                <th style={{ padding: "6px 10px", textAlign: "left", borderBottom: "1px solid var(--border)" }}>제품</th>
                <th style={{ padding: "6px 10px", textAlign: "right", borderBottom: "1px solid var(--border)" }}>캐시 root</th>
                <th style={{ padding: "6px 10px", textAlign: "right", borderBottom: "1px solid var(--border)" }}>MB</th>
                <th style={{ padding: "6px 10px", textAlign: "right", borderBottom: "1px solid var(--border)" }}>우선 lot (적재/등록)</th>
                <th style={{ padding: "6px 10px", textAlign: "right", borderBottom: "1px solid var(--border)" }}>상한 (root)</th>
                <th style={{ padding: "6px 10px", textAlign: "left", borderBottom: "1px solid var(--border)" }}>마지막 예열</th>
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
                  <td style={{ padding: "6px 10px", fontSize: 12 }}>{warmCellOf(p)}</td>
                </tr>
              ))}
              {!overviewLoading && (overview?.products || []).length === 0 &&
                <tr><td colSpan={6} style={{ padding: 16, textAlign: "center", color: "var(--text-secondary)" }}>제품이 없습니다</td></tr>}
              {overviewLoading &&
                <tr><td colSpan={6} style={{ padding: 16, textAlign: "center", color: "var(--text-secondary)" }}>로딩 중...</td></tr>}
            </tbody>
          </table>
        </div>
        <div style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 4 }}>
          * = 제품별 상한 직접 설정됨 (기본 {overview?.default_max_roots?.toLocaleString() || "-"}). 행을 클릭하면 아래에서 해당 제품을 관리합니다.
          {" "}이 표의 <b>캐시 root</b> 는 이 서버 메모리에 실제로 올라온 랏 수입니다 —
          아래 <b>검색 속도</b> 패널의 히트율은 pivot(디스크)·응답 캐시 히트까지 포함하므로
          이 표가 0이어도 히트율은 높을 수 있습니다.
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
            <span style={{ width: 1, alignSelf: "stretch", background: "var(--border)" }} />
            <span style={{ fontSize: 13, fontWeight: 700, whiteSpace: "nowrap" }}
              title="주요 Lot(우선적재)이 등록되지 않은 제품의 적재 순서 기준입니다. step_id 안의 숫자가 이 값 이상인 lot 을 이 값에 가까운 순서로 먼저 올립니다. 0 = 이 규칙 끄기.">
              적재 순서 step 임계
            </span>
            <input type="number" min={0} step={1000} value={stepThresholdDraft}
              onChange={e => setStepThresholdDraft(e.target.value)}
              placeholder={String(budgets?.default_step_threshold ?? 400000)}
              style={{ ...S_INPUT, width: 96, fontFamily: "monospace" }} />
            <button onClick={saveBudget} disabled={budgetSaving}
              style={{ padding: "4px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "transparent",
                color: "var(--accent)", fontSize: 12, fontWeight: 700, cursor: budgetSaving ? "wait" : "pointer" }}>
              {budgetSaving ? "저장 중" : "저장"}
            </button>
            <div style={{ flexBasis: "100%", fontSize: 11, color: "var(--text-secondary)" }}>
              주요 Lot 탭에 등록된 lot 이 있으면 그 목록이 우선이고, 없을 때만 step 임계 순서로 채웁니다
              (기본 {budgets?.default_step_threshold ?? 400000} 이상 · 임계값에 가까운 lot 부터).
            </div>
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
                  {canManage && <th style={{ padding: "4px 6px", borderBottom: "1px solid var(--border)" }}></th>}
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
                    {canManage && <td style={{ padding: "3px 6px", textAlign: "center" }}>
                      <button onClick={() => evictCacheEntry(e)}
                        title="이 항목을 RAM 캐시에서 제거 — 다음 검색은 미스(원본 읽기)가 됩니다. 히트/미스 속도 비교 측정용"
                        style={{ padding: "1px 6px", borderRadius: 4, border: "1px solid var(--border)",
                          background: "transparent", color: "var(--danger)", fontSize: 11, cursor: "pointer" }}>축출</button>
                    </td>}
                  </tr>
                ))}
                {(contents.entries || []).length === 0 &&
                  <tr><td colSpan={canManage ? 7 : 6} style={{ padding: 12, textAlign: "center", color: "var(--text-secondary)" }}>
                    캐시된 root lot이 없습니다
                    {(() => {
                      // 원인 진단은 관리자만 받는 root-lot 상태가 있을 때만 —
                      // 상태 없이 추측하면 일반 유저에게 "스케줄러 미시작" 오진이 뜬다.
                      if (!rootLotCacheStatus) return null;
                      const rc = rootLotCacheStatus.cache || {};
                      if (!rc.scheduler_started) return <div style={{ fontSize: 11, marginTop: 4 }}>자동 예열은 기본 꺼짐(정상)입니다. 필요하면 주요 Lot/수동 적재를 사용하세요.</div>;
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
      )}

      {mainTab === "jobs" && (
        <div style={{ display: "grid", gap: 14 }}>
          {!canManage ? <div style={{ padding: 20, textAlign: "center", color: "var(--text-secondary)" }}>이 탭은 관리자 권한이 필요합니다.</div> : null}
      {/* 갱신이 끊겼는지 화면이 스스로 말해야 한다 — 값이 안 바뀌는 것과
          서버에 못 닿는 것이 예전에는 똑같이 '조용함'으로 보였다. */}
      {canManage && cacheLoadError && <div style={{ display: "flex", gap: 8, alignItems: "center",
        flexWrap: "wrap", padding: "8px 12px", borderRadius: 8, fontSize: 12,
        border: "1px solid var(--warn-line)", background: "var(--warn-50)", color: "var(--warn)" }}>
        <b>⚠ 캐시 상태 갱신 실패</b>
        <span>{cacheLoadError}</span>
        <span style={{ color: "var(--text-secondary)" }}>
          — 아래 값은 마지막으로 받은 것입니다. 다음 주기에 자동으로 다시 시도합니다.
        </span>
        <button onClick={() => loadCacheEventLog(cacheEventLogFilter)}
          style={{ ...S_BTN, fontSize: 11, padding: "3px 8px" }}>지금 다시 시도</button>
      </div>}
      {canManage && <CachingScheduleBoard jobs={cacheJobs} queues={cacheQueues} scanQueue={scanQueue}
        canManage={canManage} onCancelTask={cancelScanTask} onStopProduct={stopMatchCacheProduct}
        milestones={cacheMilestones} />}
{/* 관리자 — 캐시 수동 스캔/설정 (SplitTable 톱니바퀴에서 이동) */}
      {canManage && <div style={{ display: "grid", gap: 10, padding: "10px 12px", borderRadius: 8,
        border: "1px solid var(--border)", background: "var(--bg-card)", marginBottom: 16 }}>
        <div style={{ fontSize: 14, fontWeight: 800 }}>캐시 수동 스캔 / 큐 관리</div>

        {/* 통합 수동 스캔 버튼 */}
        <div style={{ display: "grid", gap: 8, padding: "8px 10px", borderRadius: 8,
          border: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <select value={selProd} onChange={e => setSelProd(e.target.value)}
              style={{ ...S_INPUT, minWidth: 190, fontFamily: "monospace" }}>
              <option value="">전체 제품</option>
              {(overview?.products || []).map(p => <option key={p.product} value={p.product}>{p.product}</option>)}
            </select>
            <button onClick={runUnifiedScan} disabled={unifiedScanBusy}
              style={{ ...S_BTN, border: "1px solid var(--accent)", background: "var(--accent-glow)", color: "var(--accent)",
                fontWeight: 700, borderRadius: 999, cursor: unifiedScanBusy ? "wait" : "pointer",
                opacity: unifiedScanBusy ? 0.65 : 1, fontSize: 14, padding: "7px 16px" }}>
              {unifiedScanBusy ? "큐 등록 중..." : selProd ? `수동 캐싱 (${selProd})` : "수동 캐싱 (전체 제품)"}
            </button>
            <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
              필수 디스크 캐시 4단계를 <b>이 서버에서 바로</b> 1회 실행합니다 (개발 워커로 넘기지 않음)
            </span>
          </div>
          <div style={{ display: "grid", gap: 5, paddingTop: 8, borderTop: "1px dashed var(--border)",
            fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.55 }}>
            <div><b>1. 랏 lookup</b> — 제품의 전체 Root Lot·LOT 후보와 root별 원천 행.</div>
            <div><b>2. SplitTable pivot</b> — root별로 바로 그릴 수 있는 표 데이터.</div>
            <div><b>3. WIP latest-lot</b> — root_lot_id + wafer_id별 최신 lot·step·tkout.</div>
            <div><b>4. FAB latest 인덱스</b> — root별 최신 FAB 행.</div>
          </div>
          <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>
            버튼은 빌드 완료를 기다리지 않고 큐 등록만 합니다. 실제 진행은 위 일정 보드와 ‘기술 진행 정보’에서 확인하세요.
          </div>
        </div>

              </div>}


{/* 관리자 — Peak RAM 사용량 + 캐시 이벤트 로그 */}
      {canManage && <div style={{ display: "grid", gap: 10, padding: "10px 12px", borderRadius: 8,
        border: "1px solid var(--border)", background: "var(--bg-card)", marginBottom: 16 }}>
        {/* 진행 중인 캐시 작업 — (제품 × 캐시 종류)마다 **자기 분모로** 따로 보여준다.
            여러 제품의 랏 수를 한 분모로 더하면 그 숫자가 무엇의 몇인지 알 수 없다.
            아래에 방금 끝난 몇 건만 붙여 진행이 없을 때도 직전 결과가 보이게 한다. */}
        {cacheProgress && (
          <div className="ramcache-current-progress" style={{ display: "grid", gap: 8, padding: "10px 12px", borderRadius: 8,
            border: `1px solid ${(cacheProgress.active_count || 0) > 0 ? "var(--accent)" : "var(--border)"}`,
            background: "var(--bg-secondary)" }}>
            <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
              <span style={{ fontSize: 13, fontWeight: 800 }}>현재 진행 중인 캐싱 작업</span>
              <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                {(cacheProgress.active_count || 0) > 0
                  ? `제품 ${(cacheProgress.products_active || []).length}개 · 작업 ${cacheProgress.active_count}건`
                  : "진행 중인 작업 없음"}
                {cacheProgress.stalled_count > 0 &&
                  <b style={{ color: "var(--warn)" }}> · 정지 의심 {cacheProgress.stalled_count}건</b>}
              </span>
            </div>

            {(cacheProgress.items || []).length === 0 && (
              <div style={{ padding: "8px 0", color: "var(--text-secondary)", fontSize: 12 }}>
                현재 진행 중인 캐싱 작업이 없습니다.
              </div>
            )}
            {(cacheProgress.items || []).map((it, i) => {
              const scanTask = cacheTaskForProduct(scanQueue, it.product);
              const taskRunning = scanTask?.id && scanQueue?.current?.id === scanTask.id;
              return <div key={`run-${i}`} style={{ display: "grid", gap: 3 }}>
                <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap", fontSize: 12 }}>
                  <span style={{ padding: "1px 6px", borderRadius: 3, fontSize: 11, fontWeight: 700,
                    ...cacheStateTone("running") }}>{PROGRESS_KIND_KO[it.kind] || it.kind}</span>
                  <b style={{ fontSize: 13, color: "var(--accent)" }}>{it.product || "-"}</b>
                  <span style={{ fontFamily: "monospace", fontWeight: 700 }}>
                    {(it.done || 0).toLocaleString()} / {(it.total || 0).toLocaleString()} {it.unit || "랏"}
                    <span style={{ marginLeft: 4, color: "var(--text-secondary)" }}>({it.pct || 0}%)</span>
                  </span>
                  {it.stalled && <b style={{ color: "var(--warn)" }}>{fmtDur(it.idle_sec)}째 갱신 없음</b>}
                  {it.origin && <span style={{ color: "var(--text-secondary)" }}>{it.origin}</span>}
                  {canManage && scanTask?.id && <button
                    onClick={() => cancelScanTask(scanTask, taskRunning)}
                    style={{ ...scanCancelBtn, marginLeft: "auto" }}>
                    {taskRunning ? "중단" : "취소"}
                  </button>}
                </div>
                {/* 막대도 이 작업 하나의 것이다 — 전체 합산 막대가 아니다. */}
                <div style={{ height: 6, borderRadius: 3, background: "var(--bg-tertiary)", overflow: "hidden" }}>
                  <div style={{ height: "100%", width: `${Math.min(100, it.pct || 0)}%`,
                    background: it.stalled ? "var(--warn)" : "var(--accent)", transition: "width .3s" }} />
                </div>
              </div>;
            })}
          </div>
        )}

        {/* 제품은 한 줄씩만 보여 주고, 선택한 행의 4종 상세만 아래로 펼친다. */}
        {productStatus && (productStatus.products || []).length > 0 && (
          <div className="ramcache-product-status" style={{ display: "grid", gap: 6 }}>
            <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
              <span style={{ fontSize: 13, fontWeight: 800 }}>제품별 캐시 상태</span>
              <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                성공 {productStatus.ok_count || 0} · 실패 {productStatus.failed_count || 0} ·
                진행 중 {productStatus.running_count || 0} · 필수 4종 · 최근 7일 기록 기준
              </span>
            </div>
            <div className="ramcache-product-status-scroll" style={{ display: "grid", gap: 6,
              maxHeight: 520, overflowY: "auto", paddingRight: 3 }}>
              {(productStatus.products || []).map((row) => {
                const statusKinds = Object.keys(PRODUCT_STATUS_KIND_NO).map((kind) =>
                  (row.kinds || []).find((item) => item.kind === kind) || {
                    kind, label: PROGRESS_KIND_KO[kind] || kind, state: "never",
                    started_ts: 0, success_ts: 0, failed_ts: 0,
                  });
                const productProgress = (cacheProgress?.items || []).filter((item) =>
                  cacheProductKey(item.product) === cacheProductKey(row.product));
                const expanded = !!expandedCacheProducts[cacheProductKey(row.product)];
                const scanTask = cacheTaskForProduct(scanQueue, row.product);
                const taskRunning = scanTask?.id && scanQueue?.current?.id === scanTask.id;
                const readyCount = Number(row.ready_count ?? statusKinds.filter(k => k.ready || k.state === "ok").length);
                const toggle = () => setExpandedCacheProducts(prev => ({
                  ...prev, [cacheProductKey(row.product)]: !expanded,
                }));
                return (
                  <div key={row.product} className="ramcache-product-card" style={{ borderRadius: 8, overflow: "hidden",
                    border: `1px solid ${row.state === "failed" ? "var(--danger)" : "var(--border)"}`,
                    background: "var(--bg-secondary)" }}>
                    <div style={{ display: "flex", alignItems: "center", minHeight: 38,
                      borderBottom: expanded ? "1px solid var(--border)" : "none" }}>
                      <button type="button" aria-expanded={expanded} onClick={toggle}
                        style={{ display: "flex", flex: 1, gap: 9, alignItems: "center", minWidth: 0,
                          padding: "8px 10px", cursor: "pointer", textAlign: "left",
                          border: "none", background: "transparent", color: "var(--text-primary)" }}>
                      <span aria-hidden="true" style={{ width: 14, color: "var(--text-secondary)", flexShrink: 0 }}>
                        {expanded ? "▾" : "▸"}
                      </span>
                      <b style={{ fontSize: 14, color: "var(--accent)", minWidth: 180 }}>{row.product}</b>
                      <span style={{ padding: "2px 10px", borderRadius: 4, fontSize: 12, fontWeight: 800,
                        ...cacheStateTone(row.state) }}>
                        {CACHE_STATE_KO[row.state] || row.state}
                      </span>
                      <span style={{ fontSize: 11, color: "var(--text-secondary)", fontFamily: "monospace",
                        whiteSpace: "nowrap" }}>{readyCount}/{statusKinds.length} 준비</span>
                      <span style={{ display: "flex", gap: 4, minWidth: 0, overflow: "hidden" }}>
                        {statusKinds.map(k => {
                          const running = productProgress.some(item => item.kind === k.kind);
                          const state = running ? "running" : k.state;
                          return <span key={`summary-${k.kind}`} title={`${PROGRESS_KIND_KO[k.kind] || k.label}: ${CACHE_STATE_KO[state] || state}`}
                            style={{ width: 8, height: 8, borderRadius: 999, flexShrink: 0,
                              background: state === "ok" ? "var(--ok)" : state === "failed" ? "var(--danger)"
                                : state === "running" ? "var(--info)" : state === "stale" ? "var(--warn)"
                                  : "var(--border)" }} />;
                        })}
                      </span>
                      <span style={{ marginLeft: "auto", minWidth: 0, fontSize: 11, color: "var(--text-secondary)",
                        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                        fontFamily: "monospace" }}>
                        {row.state === "ok"
                          ? (row.all_success_ts ? `전체 성공 ${fmtTs(row.all_success_ts)}` : "실제 캐시 4종 준비 완료")
                          : row.state === "failed"
                            ? `실패 ${fmtTs(row.last_failure_ts)} · ${(row.failed_kinds || []).join(", ")}`
                            : row.state === "running"
                               ? `진행 중 · ${(row.running_kinds || []).join(", ")}`
                               : `최근 성공 ${fmtTs(row.last_success_ts)}`}
                      </span>
                      </button>
                      {canManage && scanTask?.id && (row.state === "running" || productProgress.length > 0) && <button
                        onClick={() => cancelScanTask(scanTask, taskRunning)}
                        style={{ ...scanCancelBtn, marginRight: 10 }}>{taskRunning ? "중단" : "취소"}</button>}
                    </div>
                    {expanded && <div className="ramcache-product-kind-grid">
                      {statusKinds.map((k) => {
                        const progress = productProgress.find((item) => item.kind === k.kind);
                        const state = progress ? "running" : k.state;
                        const timeLabel = state === "failed" ? "실패 시각"
                          : state === "ok" ? "성공 시각" : state === "running" ? "시작 시각" : "최근 기록";
                        const timeValue = state === "failed" ? k.failed_ts
                          : state === "ok" ? k.success_ts : k.started_ts;
                        return (
                          <div key={k.kind} className="ramcache-product-kind-card">
                            <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}>
                              <b>{PRODUCT_STATUS_KIND_NO[k.kind]}. {PROGRESS_KIND_KO[k.kind] || k.label}</b>
                              <span style={{ padding: "2px 7px", borderRadius: 4, fontSize: 11, fontWeight: 800,
                                ...cacheStateTone(state) }}>
                                {CACHE_STATE_KO[state] || state}
                              </span>
                            </div>
                            <div style={{ display: "grid", gap: 3, fontSize: 11, color: "var(--text-secondary)" }}>
                              <span>{timeLabel} · <b style={{ fontFamily: "monospace", color: state === "failed" ? "var(--danger)" : state === "ok" ? "var(--ok)" : "var(--text-primary)" }}>{fmtTs(timeValue)}</b></span>
                              {k.started_ts > 0 && state !== "running" && <span>시작 시각 · <span style={{ fontFamily: "monospace" }}>{fmtTs(k.started_ts)}</span></span>}
                              {k.duration_sec > 0 && <span>소요 · {fmtDur(k.duration_sec)}</span>}
                            </div>
                            {progress ? (
                              <div style={{ display: "grid", gap: 4 }}>
                                <div style={{ display: "flex", justifyContent: "space-between", gap: 8, fontSize: 12, fontFamily: "monospace" }}>
                                  <b>{Number(progress.done || 0).toLocaleString()} / {Number(progress.total || 0).toLocaleString()} {progress.unit || "랏"}</b>
                                  <span>{progress.pct || 0}%</span>
                                </div>
                                <div style={{ height: 6, borderRadius: 3, background: "var(--bg-tertiary)", overflow: "hidden" }}>
                                  <div style={{ height: "100%", width: `${Math.min(100, progress.pct || 0)}%`,
                                    background: progress.stalled ? "var(--warn)" : "var(--accent)" }} />
                                </div>
                                {progress.stalled && <b style={{ color: "var(--warn)", fontSize: 11 }}>{fmtDur(progress.idle_sec)}째 갱신 없음</b>}
                              </div>
                            ) : (
                              <div title={state === "failed" ? (k.fail_message || "") : (k.message || "")}
                                style={{ fontSize: 11, color: state === "failed" ? "var(--danger)" : "var(--text-secondary)",
                                  overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                {state === "failed" ? (k.fail_message || "실패") : (k.message || CACHE_STATE_KO[state] || state)}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        <div className="ramcache-event-log-always-open" style={{ display: "grid", gap: 10 }}>
          <div style={{ fontSize: 13, fontWeight: 800 }}>
            상세 로그·Peak RAM
            <span style={{ marginLeft: 8, color: "var(--text-secondary)", fontSize: 11, fontWeight: 400 }}>
              최근 {(cacheEventLog || []).length}건 · 항상 펼쳐서 표시합니다
            </span>
          </div>
          <div style={{ display: "grid", gap: 10 }}>

        {/* mmap 파일 페이지가 섞이는 RSS 대신 실제 메모리 압박 판단값을 주 지표로 쓴다. */}
        {peakRam && <div style={{ display: "grid", gap: 4, padding: "8px 10px", borderRadius: 8,
          border: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
          <div style={{ fontSize: 13, fontWeight: 800 }}>캐싱 작업 RAM 사용량</div>
          <div style={{ display: "flex", gap: 16, flexWrap: "wrap", fontSize: 13, fontFamily: "monospace" }}>
            <span>현재 실사용: <b style={{ color: "var(--accent)" }}>{peakRam.effective_gb?.toFixed(2) || "?"} GB</b></span>
            {(peakRam.recent_48h_effective_sample_count || 0) > 0 &&
              <span>최근 2일 Peak: <b style={{ color: (peakRam.recent_48h_peak_effective_gb || 0) > (peakRam.limit_gb || 999) * 0.85
                ? "var(--danger)" : "var(--accent)" }}>{peakRam.recent_48h_peak_effective_gb?.toFixed(2) || "?"} GB</b></span>}
            <span>Limit: <b>{peakRam.limit_gb?.toFixed(2) || "?"} GB</b></span>
            <span>System 여유: <b>{peakRam.system_available_gb != null ? peakRam.system_available_gb.toFixed(1) : "?"} GB</b>
              <span style={{ color: "var(--text-secondary)" }}> / {peakRam.system_total_gb?.toFixed(1) || "?"} GB</span>
            </span>
          </div>
          <details>
            <summary style={{ fontSize: 12, color: "var(--text-secondary)", cursor: "pointer" }}>
              RSS 진단값 · 측정 기준 · Watchdog 자세히
            </summary>
            <div style={{ display: "flex", gap: 16, flexWrap: "wrap", fontSize: 12, fontFamily: "monospace", marginTop: 6 }}>
              <span>전체 최대 Peak RSS: <b style={{ color: (peakRam.peak_rss_gb || 0) > (peakRam.limit_gb || 999) * 0.85
                ? "var(--danger)" : "var(--accent)" }}>{peakRam.peak_rss_gb?.toFixed(2) || "?"} GB</b></span>
              <span>최근 2일 Peak RSS: <b style={{ color: (peakRam.recent_48h_peak_rss_gb || 0) > (peakRam.limit_gb || 999) * 0.85
                ? "var(--danger)" : "var(--accent)" }}>{peakRam.recent_48h_peak_rss_gb?.toFixed(2) || "?"} GB</b></span>
              <span>현재 RSS: <b>{peakRam.rss_gb?.toFixed(2) || "?"} GB</b></span>
              <span>실사용 기준: <b>{peakRam.effective_kind === "container_working_set" ? "Grafana working set" : peakRam.effective_kind === "anonymous" ? "Anonymous" : peakRam.effective_kind === "pss" ? "PSS" : peakRam.effective_kind === "uss" ? "USS" : "RSS fallback"}</b></span>
            </div>
            {/* RSS 가 Grafana 보다 훨씬 높다는 신고가 반복돼 화면에서 직접 설명한다.
                RSS 는 mmap 된 parquet 페이지까지 세고(polars 가 parquet 을 mmap 한다),
                Grafana 의 컨테이너 working set 은 회수 가능한 file cache 를 뺀다. */}
            <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.5, marginTop: 6 }}>
              <b>Peak RSS</b> 는 메모리 맵된 parquet 페이지까지 포함하므로 Grafana 의 컨테이너 working set
              (inactive file cache 제외) 보다 높게 나올 수 있습니다.
              {peakRam.effective_kind === "container_working_set"
                ? <> 화면의 <b>실사용/작업 Peak</b>은 Grafana와 같은 컨테이너 working set입니다.</>
                : peakRam.effective_kind === "anonymous"
                ? <> 화면의 <b>실사용</b>은 파일 캐시를 제외한 Anonymous 메모리입니다.</>
                : peakRam.effective_kind === "pss"
                  ? <> 화면의 <b>실사용</b>은 공유 페이지를 비례 배분한 PSS입니다.</>
                  : <> 이 서버는 정밀 지표를 읽지 못해 실사용 값이 RSS로 폴백될 수 있습니다.</>}
              {" "}또한 peak 은 워치독 샘플의 <b>최댓값</b>이라, 스크랩 간격(15~60초)으로 그리는 Grafana 그래프에는
              짧은 빌드 스파이크가 아예 안 보일 수 있습니다. 컨테이너 working set 기준에서는 같은
              cgroup의 빌드 자식 프로세스도 함께 포함합니다.
            </div>
            {peakRam.watchdog && <div style={{ display: "flex", gap: 16, flexWrap: "wrap", fontSize: 12,
              color: "var(--text-secondary)", fontFamily: "monospace", marginTop: 6 }}>
              <span>Watchdog warn: {peakRam.watchdog.warn_pct}%</span>
              <span>critical: {peakRam.watchdog.critical_pct}%</span>
              <span>safe: {peakRam.watchdog.safe_pct}%</span>
            </div>}
          </details>
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
          {/* 표 대신 한 줄짜리 로그 리스트 — 시각·제품·무슨 일이었는지가 한 줄에서 바로
              읽히게 한다. 서버/분류/상태는 작은 배지로 접고, 단계(있으면)는 이벤트
              문장 앞에 붙여 열을 하나 줄인다. 최신이 위(백엔드가 이미 내림차순). */}
          <div style={{ maxHeight: 520, overflowY: "auto", borderRadius: 6, border: "1px solid var(--border)" }}>
            {(cacheEventLog || []).map((ev, i) => {
              const stage = scanStageLabel(ev);
              return (
                <div key={ev.eid || `${ev.ts || ""}-${ev.origin || ""}-${i}`} style={{
                  display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap",
                  padding: "5px 8px", fontSize: 12,
                  borderBottom: "1px solid var(--border)",
                  borderLeft: `3px solid ${ev.ok ? "transparent" : "var(--danger)"}`,
                  background: !ev.ok ? "rgba(239,68,68,0.04)" : ev.category === "eviction" ? "rgba(245,158,11,0.04)" : "transparent" }}>
                  <span style={{ fontFamily: "monospace", color: "var(--text-secondary)", whiteSpace: "nowrap" }}>
                    {fmtKst(ev)}
                  </span>
                  {ev.origin && <span title={ev.host || ""} style={{ padding: "1px 5px", borderRadius: 3, fontSize: 10, fontWeight: 700,
                    background: ev.origin === "운영" ? "var(--info-50)" : "var(--warn-50)",
                    color: ev.origin === "운영" ? "var(--info)" : "var(--warn)" }}>{ev.origin}</span>}
                  <span style={{ padding: "1px 5px", borderRadius: 3, fontSize: 10, fontWeight: 700, ...categoryTone(ev.category) }}>
                    {CATEGORY_KO[ev.category] || ev.category}
                  </span>
                  {!ev.ok && <span style={{ fontSize: 10, fontWeight: 800, color: "var(--danger)" }}>실패</span>}
                  {ev.product && <b style={{ color: "var(--accent)" }}>{ev.product}</b>}
                  <span style={{ color: ev.ok ? "var(--text-primary)" : "var(--danger)", wordBreak: "break-word" }}>
                    {stage !== "-" && <span style={{ color: "var(--text-secondary)" }}>{stage} · </span>}
                    {cacheEventText(ev)}
                  </span>
                  {cacheEventDetailText(ev) && (
                    <div className="ramcache-event-detail" style={{ flexBasis: "100%", minWidth: 0,
                      marginLeft: 0, padding: "3px 7px", borderRadius: 4,
                      background: "var(--bg-tertiary)", color: "var(--text-secondary)",
                      fontFamily: "monospace", fontSize: 10.5, lineHeight: 1.45,
                      whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
                      상세 · {cacheEventDetailText(ev)}
                    </div>
                  )}
                </div>
              );
            })}
            {(!cacheEventLog || cacheEventLog.length === 0) &&
              <div style={{ padding: 14, textAlign: "center", color: "var(--text-secondary)", fontSize: 12 }}>
                이벤트 로그가 없습니다
              </div>}
          </div>
        </div>
          </div>
        </div>
      </div>}

      
        </div>
      )}

      {mainTab === "speed_config" && (
        <div style={{ display: "grid", gap: 14 }}>
          {!canManage ? <div style={{ padding: 20, textAlign: "center", color: "var(--text-secondary)" }}>이 탭은 관리자 권한이 필요합니다.</div> : null}

      {canManage && <div style={{ display: "grid", gap: 10, padding: "10px 12px", borderRadius: 8,
        border: "1px solid var(--border)", background: "var(--bg-card)", marginBottom: 16 }}>
        <div style={{ fontSize: 14, fontWeight: 800 }}>검색 속도 및 설정</div>
{/* 검색 속도 (히트/미스) — 활동 대시보드에서 이 페이지로 이전.
            측정과 튜닝(아래 쿼리 코어, ⚙️ 검색 동시 슬롯)이 같은 화면에 있어야
            "속도 보고 설정 조절" 루프가 한 곳에서 돈다. */}
        {(() => {
          const sum = timing?.summary || null;
          const rows = timing?.rows || [];
          const missRoots = timing?.miss_roots || [];
          const ms = v => Number(v || 0) >= 1000 ? (Number(v) / 1000).toFixed(1) + "s" : Number(v || 0).toFixed(0) + "ms";
          const dsLabel = { payload_cache: "응답캐시", pivot_cache: "pivot캐시", product_ram: "제품RAM", ram: "메모리HIT", ram_load: "메모리적재", disk: "디스크(첫검색)", root_cache: "캐시", raw: "원본스캔" };
          const hitDs = new Set(["payload_cache", "pivot_cache", "product_ram", "ram", "root_cache"]);
          const waitOf = r => Number(r.wait_ms ?? (Number(r.lane_wait_ms || 0) + Number(r.cold_lane_wait_ms || 0)));
          const computeOf = r => Number(r.compute_ms ?? (Number(r.total_ms || 0) - Number(r.cold_lane_wait_ms || 0)));
          const slowPct = Number(sum?.slow_wait_pct || 0);
          const hitRate = Number(sum?.hit_rate_pct || 0);
          const ramHitRate = Number(sum?.ram_hit_rate_pct || 0);
          const missP50 = Number(sum?.miss_wall_ms?.p50 || 0);
          const tile = (label, value, sub, color) => (
            <div style={{ display: "grid", gap: 2, padding: "6px 10px", borderRadius: 6,
              border: "1px solid var(--border)", background: "var(--bg-card)", minWidth: 118 }}>
              <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>{label}</span>
              <span style={{ fontSize: 15, fontWeight: 800, fontFamily: "monospace", color: color || "var(--text-primary)" }}>{value}</span>
              {sub && <span style={{ fontSize: 10.5, color: "var(--text-secondary)", fontFamily: "monospace" }}>{sub}</span>}
            </div>);
          return (
            <div style={{ display: "grid", gap: 8, padding: "8px 10px", borderRadius: 8,
              border: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <div style={{ fontSize: 13, fontWeight: 800 }}>사용자 검색 속도 (캐시 히트/미스)</div>
                {[[24, "24시간"], [72, "3일"], [168, "7일"], [720, "30일"]].map(([h, l]) => (
                  <span key={h} onClick={() => setTimingHours(h)}
                    style={{ padding: "1px 9px", borderRadius: 5, fontSize: 12, cursor: "pointer",
                      border: "1px solid " + (timingHours === h ? "var(--accent)" : "var(--border)"),
                      color: timingHours === h ? "var(--accent)" : "var(--text-secondary)",
                      fontWeight: timingHours === h ? 700 : 400 }}>{l}</span>))}
                {/* 서버 범위 — 기록은 운영/개발 공유 파일이라 기본은 '이 서버'다. */}
                <span style={{ display: "inline-flex", gap: 4, marginLeft: 4 }}>
                  {[["this", `이 서버${timing?.this_origin ? ` (${timing.this_origin})` : ""}`], ["all", "운영+개발 전체"]].map(([v, l]) => (
                    <span key={v} onClick={() => setTimingScope(v)}
                      style={{ padding: "1px 9px", borderRadius: 5, fontSize: 12, cursor: "pointer",
                        border: "1px solid " + (timingScope === v ? "var(--accent)" : "var(--border)"),
                        color: timingScope === v ? "var(--accent)" : "var(--text-secondary)",
                        fontWeight: timingScope === v ? 700 : 400 }}>{l}</span>))}
                </span>
                <button onClick={() => loadTiming(timingHours, timingScope)}
                  style={{ ...S_BTN, fontSize: 11, padding: "2px 8px" }}>새로고침</button>
                {timing && <span style={{ fontSize: 12, color: "var(--text-secondary)", fontFamily: "monospace", marginLeft: "auto" }}>
                  사용자·관리자 검색 {timing.count || 0}건{timing.persisted === false ? " · 메모리 기록만" : ""}
                </span>}
              </div>
              {!sum || !timing?.count ? (
                <div style={{ fontSize: 12, color: "var(--text-secondary)", padding: 4 }}>
                  이 기간에 사용자 검색 기록이 없습니다 — 캐시 작업·Flow-i·내부 조회는 이 통계에서 제외됩니다.
                </div>
              ) : (<>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  {tile("히트율 (전 캐시)", `${sum.hit_rate_pct || 0}%`, `${sum.hit_count || 0} / ${timing.count}건`,
                    (sum.hit_rate_pct || 0) >= 70 ? "var(--ok)" : (sum.hit_rate_pct || 0) >= 40 ? "var(--warn)" : "var(--danger)")}
                  {tile("랏 RAM 히트", `${sum.ram_hit_rate_pct || 0}%`, `${sum.ram_hit_count || 0}건 · 위 표의 캐시 root`,
                    (sum.ram_hit_rate_pct || 0) >= 50 ? "var(--ok)" : (sum.ram_hit_rate_pct || 0) > 0 ? "var(--warn)" : "var(--text-secondary)")}
                  {tile("히트 속도", ms(sum.hit_wall_ms?.p50), `p90 ${ms(sum.hit_wall_ms?.p90)}`, "var(--ok)")}
                  {tile("미스 속도", ms(sum.miss_wall_ms?.p50), `p90 ${ms(sum.miss_wall_ms?.p90)}`, "var(--accent)")}
                  {tile("대기 (줄서기)", ms(sum.wait_ms?.p90) + " p90", `≥200ms ${sum.slow_wait_count || 0}건 (${slowPct}%)`,
                    slowPct >= 20 ? "var(--danger)" : slowPct >= 5 ? "var(--warn)" : "var(--text-primary)")}
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11 }}>
                  <span style={{ padding: "2px 7px", borderRadius: 999, background: "var(--accent-glow)", color: "var(--accent)", fontWeight: 800 }}>자동 진단</span>
                  <span style={{ color: "var(--text-secondary)" }}>위 측정값과 고정 기준으로 자동 계산됩니다.</span>
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))", gap: 7 }}>
                  <div style={{ padding: "7px 9px", borderRadius: 7, border: `1px solid ${hitRate >= 70 && ramHitRate < 20 ? "var(--warn-line)" : "var(--border)"}`, background: "var(--bg-card)" }}>
                    <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>캐시 구성</div>
                    <b style={{ fontSize: 12, color: hitRate >= 70 && ramHitRate < 20 ? "var(--warn)" : hitRate >= 70 ? "var(--ok)" : "var(--danger)" }}>
                      {hitRate >= 70 && ramHitRate < 20 ? "공유 디스크 캐시 중심" : hitRate >= 70 ? "히트율 양호" : hitRate >= 40 ? "히트율 주의" : "히트율 낮음"}
                    </b>
                    <div style={{ fontSize: 10.5, color: "var(--text-secondary)", marginTop: 2 }}>
                      {hitRate >= 70 && ramHitRate < 20 ? "전체 히트는 높지만 랏 RAM 적중은 적습니다." : hitRate < 70 ? "미스 반복 root를 주요 Lot에 등록하세요." : "현재 구성 유지"}
                    </div>
                  </div>
                  <div style={{ padding: "7px 9px", borderRadius: 7, border: `1px solid ${slowPct >= 20 ? "var(--danger-line)" : slowPct >= 5 ? "var(--warn-line)" : "var(--border)"}`, background: "var(--bg-card)" }}>
                    <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>대기열</div>
                    <b style={{ fontSize: 12, color: slowPct >= 20 ? "var(--danger)" : slowPct >= 5 ? "var(--warn)" : "var(--ok)" }}>
                      {slowPct >= 20 ? "대기 잦음" : slowPct >= 5 ? "가끔 대기" : "줄서기 거의 없음"}
                    </b>
                    <div style={{ fontSize: 10.5, color: "var(--text-secondary)", marginTop: 2 }}>
                      {slowPct >= 20 ? "검색 동시 슬롯 상향 검토" : slowPct >= 5 ? "추세 관찰" : "슬롯 변경 불필요"} · 200ms↑ {slowPct}%
                    </div>
                  </div>
                  <div style={{ padding: "7px 9px", borderRadius: 7, border: `1px solid ${missP50 >= 3000 ? "var(--danger-line)" : missP50 >= 1000 ? "var(--warn-line)" : "var(--border)"}`, background: "var(--bg-card)" }}>
                    <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>캐시 미스</div>
                    <b style={{ fontSize: 12, color: missP50 >= 3000 ? "var(--danger)" : missP50 >= 1000 ? "var(--warn)" : "var(--ok)" }}>
                      {missP50 >= 3000 ? "미스 검색 느림" : missP50 >= 1000 ? "미스 검색 주의" : "미스 속도 양호"}
                    </b>
                    <div style={{ fontSize: 10.5, color: "var(--text-secondary)", marginTop: 2 }}>
                      p50 {ms(missP50)} · {missP50 >= 1000 ? "예열·주요 Lot·쿼리 코어 확인" : "현재 설정 유지"}
                    </div>
                  </div>
                </div>
                <details>
                  <summary style={{ cursor: "pointer", fontSize: 10.5, color: "var(--text-secondary)" }}>자동 진단 기준 보기</summary>
                  <div style={{ marginTop: 4, fontSize: 10.5, color: "var(--text-secondary)", lineHeight: 1.5 }}>
                    캐시 구성: 전체 히트율 70% 이상 + 랏 RAM 히트율 20% 미만이면 ‘공유 디스크 캐시 중심’ ·
                    대기열: 200ms 이상 대기 비율 5%/20% 기준 · 캐시 미스: p50 1초/3초 기준.
                    전체 히트율은 응답·pivot·제품RAM·랏RAM을 합산하고, 랏 RAM 히트만 제품별 현황의 캐시 root와 같은 항목입니다.
                  </div>
                </details>
                {(sum.by_origin || []).length > 1 && (
                  <div style={{ display: "flex", gap: 14, flexWrap: "wrap", fontSize: 12, fontFamily: "monospace",
                    padding: "5px 8px", borderRadius: 6, background: "var(--bg-card)", border: "1px solid var(--border)" }}>
                    {(sum.by_origin || []).map(o => (
                      <span key={o.origin}>
                        <b>{o.origin}</b> {o.count}건 · 히트 {o.hit_rate_pct}% ·
                        {" "}히트 {ms(o.hit_wall_ms?.p50)} / 미스 {ms(o.miss_wall_ms?.p50)}
                      </span>))}
                  </div>
                )}
                {/* 단계별 시간 배분 — "느리다"를 "어디가 느리다"로 바꿔 읽는 표.
                    히트/미스를 나눠 본다: 히트가 느리면 캐시 밖(진입·마무리·직렬화),
                    미스가 느리면 읽기(스캔·polars) 문제다. */}
                {(() => {
                  const scopeKey = { all: "phase_breakdown", miss: "phase_breakdown_miss", hit: "phase_breakdown_hit" }[phaseScope];
                  const bd = (sum[scopeKey] || []).filter(p => p.total_ms > 0);
                  if (!(sum.phase_breakdown || []).length) return (
                    <div style={{ fontSize: 11.5, color: "var(--text-secondary)", lineHeight: 1.5 }}>
                      이 기간에는 단계별 기록이 없습니다 — 단계 계측이 들어가기 전 검색이거나, 아직 새 검색이 없습니다.
                    </div>);
                  const grand = bd.reduce((a, p) => a + p.total_ms, 0) || 1;
                  return (
                    <div style={{ display: "grid", gap: 5 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                        <div style={{ fontSize: 12, fontWeight: 700 }}>단계별 시간 배분</div>
                        {[["all", "전체"], ["miss", "미스만"], ["hit", "히트만"]].map(([v, l]) => (
                          <span key={v} onClick={() => setPhaseScope(v)}
                            style={{ cursor: "pointer", fontSize: 11, padding: "1px 7px", borderRadius: 999,
                              border: "1px solid " + (phaseScope === v ? "var(--accent)" : "var(--border)"),
                              color: phaseScope === v ? "var(--accent)" : "var(--text-secondary)",
                              fontWeight: phaseScope === v ? 700 : 400 }}>{l}</span>))}
                        {bd.length > 0 && <span style={{ fontSize: 11, color: "var(--text-secondary)", fontFamily: "monospace", marginLeft: "auto" }}>
                          검색당 평균 {ms(bd.reduce((a, p) => a + p.avg_ms, 0))}
                        </span>}
                      </div>
                      {bd.length === 0 ? (
                        <div style={{ fontSize: 11.5, color: "var(--text-secondary)" }}>이 구분에는 기록이 없습니다.</div>
                      ) : (<>
                        <div style={{ display: "flex", height: 14, borderRadius: 4, overflow: "hidden", border: "1px solid var(--border)" }}>
                          {bd.map((p, i) => (
                            <div key={p.key} title={`${p.label} ${p.pct}% · 평균 ${ms(p.avg_ms)}`}
                              style={{ width: `${100 * p.total_ms / grand}%`,
                                background: p.key === "unaccounted_ms" ? "var(--text-secondary)" : PHASE_COLORS[i % PHASE_COLORS.length] }} />))}
                        </div>
                        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", fontSize: 11, fontFamily: "monospace" }}>
                          {bd.slice(0, 8).map((p, i) => (
                            <span key={p.key} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                              <span style={{ width: 8, height: 8, borderRadius: 2, display: "inline-block",
                                background: p.key === "unaccounted_ms" ? "var(--text-secondary)" : PHASE_COLORS[i % PHASE_COLORS.length] }} />
                              {p.label} <b>{p.pct}%</b> <span style={{ color: "var(--text-secondary)" }}>{ms(p.avg_ms)}</span>
                            </span>))}
                        </div>
                        {(() => {
                          const un = bd.find(p => p.key === "unaccounted_ms");
                          return un && un.pct >= 15 ? (
                            <div style={{ fontSize: 11.5, color: "var(--warn)", lineHeight: 1.5 }}>
                              <b>미계측이 {un.pct}%</b> 입니다 — 아직 어느 단계로도 잡히지 않은 구간이 남아 있다는 뜻입니다.
                              이 상태에서는 breakdown 만 보고 최적화 대상을 정하면 안 됩니다.
                            </div>) : null;
                        })()}
                      </>)}
                    </div>);
                })()}
                {missRoots.length > 0 && (
                  <div style={{ display: "grid", gap: 4 }}>
                    <div style={{ fontSize: 12, fontWeight: 700 }}>
                      미스 반복 root — 자주 검색되는데 캐시에 없어 매번 느린 랏 (등록하면 예열 대상)
                    </div>
                    <div style={{ maxHeight: 150, overflow: "auto", borderRadius: 6, border: "1px solid var(--border)" }}>
                      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, fontFamily: "monospace" }}>
                        <thead>
                          <tr style={{ background: "var(--bg-card)", position: "sticky", top: 0 }}>
                            {["root_lot_id", "product", "미스", "p50", "최근", ""].map((h, i) => (
                              <th key={i} style={{ padding: "3px 8px", textAlign: i === 2 || i === 3 ? "right" : "left",
                                borderBottom: "1px solid var(--border)", fontSize: 11, color: "var(--text-secondary)" }}>{h}</th>))}
                          </tr>
                        </thead>
                        <tbody>
                          {missRoots.map((m, i) => (
                            <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
                              <td style={{ padding: "2px 8px", color: "var(--accent)", fontWeight: 600 }}>{m.root_lot_id}</td>
                              <td style={{ padding: "2px 8px" }}>{m.product || "-"}</td>
                              <td style={{ padding: "2px 8px", textAlign: "right" }}>{m.count}회</td>
                              <td style={{ padding: "2px 8px", textAlign: "right" }}>{ms(m.wall_ms_p50)}</td>
                              <td style={{ padding: "2px 8px", fontSize: 11, color: "var(--text-secondary)", whiteSpace: "nowrap" }}>
                                {(m.last_at || "").replace("T", " ").slice(5, 16)}</td>
                              <td style={{ padding: "2px 8px", textAlign: "right" }}>
                                <button onClick={() => registerMissRoot(m.product, m.root_lot_id)}
                                  style={{ ...S_BTN, fontSize: 11, padding: "1px 8px", color: "var(--accent)",
                                    border: "1px solid var(--accent)", whiteSpace: "nowrap" }}>주요 Lot 등록</button>
                              </td>
                            </tr>))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
                <div>
                  {/* 최근엔 접혀 있어 매번 펼쳐야 했다 — 200건으로 늘리며 항상 펼친 상태로
                      고정한다(토글 제거). 대신 목록 높이를 max-height + overflow-y:auto 로
                      막아 페이지가 한없이 길어지지 않게 한다. */}
                  <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                    최근 검색 상세 ({rows.length}건) — wall(체감) = 대기 + 계산
                  </div>
                  <div style={{ maxHeight: 420, overflowY: "auto", overflowX: "auto", borderRadius: 6, border: "1px solid var(--border)", marginTop: 4 }}>
                    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, fontFamily: "monospace" }}>
                      <thead>
                        <tr style={{ background: "var(--bg-card)", position: "sticky", top: 0 }}>
                          {["시각", "서버", "검색 유저", "product", "root", "소스", "느린 단계", "체감", "대기", "계산", "rows"].map((h, i) => (
                            <th key={i} style={{ padding: "3px 8px", textAlign: i >= 7 ? "right" : "left",
                              borderBottom: "1px solid var(--border)", fontSize: 11, color: "var(--text-secondary)", whiteSpace: "nowrap" }}>{h}</th>))}
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map((r, i) => (
                          <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
                            <td style={{ padding: "2px 8px", color: "var(--text-secondary)", whiteSpace: "nowrap" }}>{(r.at || "").replace("T", " ").slice(5, 19)}</td>
                            <td style={{ padding: "2px 8px", fontSize: 11 }}>{r.origin || "-"}</td>
                            <td style={{ padding: "2px 8px", fontWeight: 700 }} title={r.user_role || ""}>{r.username || "-"}</td>
                            <td style={{ padding: "2px 8px" }}>{r.product || ""}</td>
                            <td style={{ padding: "2px 8px", color: "var(--accent)" }}>{r.root_lot_id || ""}</td>
                            <td style={{ padding: "2px 8px", fontWeight: 700,
                              color: hitDs.has(r.data_source) ? "var(--ok)" : "var(--accent)" }}>{dsLabel[r.data_source] || r.data_source || "-"}</td>
                            {/* 이 검색에서 가장 오래 걸린 단계 — 한 행만 봐도 원인 방향이 잡힌다. */}
                            <td style={{ padding: "2px 8px", whiteSpace: "nowrap",
                              color: r.top_phase === "미계측" ? "var(--warn)" : "var(--text-secondary)" }}>
                              {r.top_phase ? `${r.top_phase} ${ms(r.top_phase_ms)}` : "-"}</td>
                            <td style={{ padding: "2px 8px", textAlign: "right", fontWeight: 700 }}>{ms(r.wall_ms || r.total_ms)}</td>
                            <td style={{ padding: "2px 8px", textAlign: "right",
                              color: waitOf(r) >= 200 ? "var(--danger)" : "var(--text-secondary)" }}>{ms(waitOf(r))}</td>
                            <td style={{ padding: "2px 8px", textAlign: "right" }}>{ms(computeOf(r))}</td>
                            <td style={{ padding: "2px 8px", textAlign: "right", color: "var(--text-secondary)" }}>{r.row_count || 0}</td>
                          </tr>))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </>)}
            </div>);
        })()}

        
{/* 쿼리 병렬 코어 수 */}
        <div style={{ display: "grid", gap: 8, padding: "8px 10px", borderRadius: 8,
          border: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <div style={{ fontSize: 13, fontWeight: 800 }}>쿼리 병렬 코어 수</div>
            {queryWorkersStatus && <span style={{ fontSize: 12, color: "var(--text-secondary)", fontFamily: "monospace" }}>
              현재 {queryWorkersStatus.effective}코어 · 정책 {queryWorkersStatus.desired}코어 · CPU {queryWorkersStatus.cpu_count}코어
            </span>}
          </div>
          <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.5 }}>
            {queryWorkersStatus?.is_dev
              ? <>개발 서버의 SplitTable 검색은 <b>1코어 고정</b>입니다. 남은 CPU는 캐시 백그라운드 작업, Flow-i, 파일탐색기 SQL 등에 우선 사용합니다.</>
              : <>운영 서버의 SplitTable 검색은 <b>기본 4코어</b>이며 1~4코어로 조절할 수 있습니다. Polars 풀은 시작할 때 고정되므로 저장 후 서버를 재시작해야 적용됩니다.</>}
            {queryWorkersStatus?.essential_concurrency && <span> (동시 조회 상한: {queryWorkersStatus.essential_concurrency}건)</span>}
            {queryWorkersStatus?.restart_required && <span style={{ color: "var(--warn)" }}> · 저장값 적용을 위해 서버 재시작이 필요합니다.</span>}
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <select value={queryWorkersDraft} disabled={!!queryWorkersStatus?.fixed}
              onChange={e => setQueryWorkersDraft(Number(e.target.value))}
              style={{ ...S_INPUT, fontFamily: "monospace", cursor: queryWorkersStatus?.fixed ? "not-allowed" : "pointer" }}>
              {[1, 2, 3, 4].filter(n => n <= (queryWorkersStatus?.cpu_count || 4)).map(n => (
                <option key={n} value={n}>{n}코어{n === 4 ? " (운영 기본)" : n === 1 ? " (개발 고정/절약)" : ""}</option>
              ))}
            </select>
            <button onClick={saveQueryWorkers} disabled={queryWorkersSaveBusy || !!queryWorkersStatus?.fixed}
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

        </div>
      )}
    </div>
  );
}
