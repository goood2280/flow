import { useEffect, useMemo, useState } from "react";
import { Banner, Button, Chip, DataTable, Icon, LinkBtn, Pill, SegmentedSwitch, Select, StatusDot } from "../../components/ui";
import { sf } from "../../lib/api";
import "./OpsScanPanel.css";

// 관리자 > 에이전트 > 운영 점검 스캔.
// 스캔 버튼 또는 하루 1회 자동 점검 때 서버가 파일(DB 루트)·서버 자원·라이브러리·운영 설정을 읽어
// 규칙 점검 결과를 만들고, LLM(사내 Gemma4)이 연결돼 있으면 그 결과만 근거로 추천을 붙인다.
// 자동 점검 결과는 관리자 알림(bell)으로 남고, 알림을 누르면 이 화면(`/admin?tab=ops_scan`)이 열린다.
// 이 화면은 아무 설정도 바꾸지 않는다 — 각 항목의 "이동"으로 해당 화면에 가서 직접 고친다.

const SEVERITY = {
  high: { label: "높음", tone: "danger" },
  medium: { label: "보통", tone: "warn" },
  low: { label: "낮음", tone: "info" },
  info: { label: "참고", tone: "neutral" },
};
const PRIORITY_TONE = { high: "danger", medium: "warn", low: "info" };
const AREA_LABELS = { files: "파일", servers: "서버", libraries: "라이브러리", operations: "운영" };
const AREA_OPTIONS = [{ value: "", label: "전체" }, ...Object.entries(AREA_LABELS).map(([value, label]) => ({ value, label }))];
const PAGE_LABELS = {
  filebrowser: "파일탐색기",
  ramcache: "캐시관리",
  productwiki: "제품 위키",
  splittable: "SplitTable",
};
const ADMIN_TAB_LABELS = {
  data_roots: "데이터 루트",
  monitor: "모니터",
  backup_sched: "백업",
  mail_cfg: "메일 API",
  users: "사용자",
  llm_cfg: "LLM 설정",
  domain_knowledge: "기본지식",
  flowi_learning: "Flow-i 학습",
};

function when(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value || "") : date.toLocaleString();
}

function linkLabel(link) {
  if (!link) return "";
  if (link.startsWith("admin:")) return ADMIN_TAB_LABELS[link.slice(6)] || "관리자";
  return PAGE_LABELS[link] || link;
}

function SeverityDot({ severity }) {
  const meta = SEVERITY[severity] || SEVERITY.info;
  return <StatusDot tone={meta.tone} title={`중요도 ${meta.label}`} />;
}

function FindingRow({ finding, highlighted, onGo }) {
  return (
    <li id={`ops-finding-${finding.id}`} className={`ops-finding${highlighted ? " is-highlighted" : ""}`}>
      <div className="ops-finding__head">
        <SeverityDot severity={finding.severity} />
        <span className="ops-finding__title">{finding.title}</span>
        <Chip mono={false}>{AREA_LABELS[finding.area] || finding.area}</Chip>
        <span className="ops-finding__id">{finding.id}</span>
      </div>
      {finding.detail && <div className="ops-finding__detail">{finding.detail}</div>}
      {finding.suggestion && (
        <div className="ops-finding__suggestion">
          <Icon name="lightbulb" />
          <span>{finding.suggestion}</span>
          {finding.link && <LinkBtn onClick={() => onGo(finding.link)}>{linkLabel(finding.link)} 열기</LinkBtn>}
        </div>
      )}
    </li>
  );
}

function RecommendationCard({ rec, findingsById, onJump }) {
  return (
    <li className="ops-rec">
      <div className="ops-rec__head">
        <Pill tone={PRIORITY_TONE[rec.priority] || "neutral"}>{SEVERITY[rec.priority]?.label || rec.priority}</Pill>
        <span className="ops-rec__title">{rec.title}</span>
        <Chip mono={false}>{AREA_LABELS[rec.area] || rec.area}</Chip>
        {!rec.grounded && <Pill tone="neutral" title="스캔 결과 항목과 연결되지 않은 일반 제안입니다">일반 제안</Pill>}
      </div>
      {rec.reason && <p className="ops-rec__reason">{rec.reason}</p>}
      <p className="ops-rec__action"><b>할 일</b> {rec.action}</p>
      {rec.finding_ids?.length > 0 && (
        <div className="ops-rec__refs">
          <span>근거</span>
          {rec.finding_ids.map((id) => (
            <LinkBtn key={id} title={findingsById[id]?.title || id} onClick={() => onJump(id)}>{id}</LinkBtn>
          ))}
        </div>
      )}
    </li>
  );
}

const HOUR_OPTIONS = Array.from({ length: 24 }, (_, h) => h);
const LAST_RESULT_LABELS = {
  notified: "관리자에게 알림을 남겼습니다",
  quiet: "높음·보통 항목이 없어 알림을 생략했습니다",
  no_admin: "알림을 받을 관리자 계정이 없습니다",
  error: "실패",
};

// 하루 1회 자동 점검 설정. 결과는 관리자 전원의 알림(bell)에 한 건으로 남는다.
function ScheduleBar({ schedule, onSave, saving }) {
  if (!schedule) return null;
  const set = (patch) => onSave(patch);
  const last = schedule.last_auto_at
    ? `${when(schedule.last_auto_at)} · ${LAST_RESULT_LABELS[schedule.last_result] || schedule.last_result || "-"}`
    : "아직 없음";
  return (
    <section className="ops-schedule" aria-label="매일 자동 점검">
      <div className="ops-schedule__controls">
        <label className="ops-toggle">
          <input type="checkbox" checked={!!schedule.enabled} disabled={saving} onChange={(e) => set({ enabled: e.target.checked })} />
          <b>매일 자동 점검</b>
        </label>
        <label className="ops-schedule__field">
          시각
          <Select value={schedule.hour} disabled={saving || !schedule.enabled} onChange={(e) => set({ hour: Number(e.target.value) })}>
            {HOUR_OPTIONS.map((h) => <option key={h} value={h}>{String(h).padStart(2, "0")}:00</option>)}
          </Select>
        </label>
        <label className="ops-schedule__field">
          알림
          <Select value={schedule.notify} disabled={saving || !schedule.enabled} onChange={(e) => set({ notify: e.target.value })}>
            <option value="issues">높음·보통 항목이 있을 때만</option>
            <option value="always">매일 결과 남기기</option>
          </Select>
        </label>
        <label className="ops-toggle">
          <input type="checkbox" checked={!!schedule.use_ai} disabled={saving || !schedule.enabled} onChange={(e) => set({ use_ai: e.target.checked })} />
          AI 추천 포함
        </label>
      </div>
      <div className="ops-schedule__status">
        <span>다음 실행 {schedule.enabled ? when(schedule.next_run_at) : "꺼짐"}</span>
        <span>마지막 자동 실행 {last}</span>
        {schedule.last_error && <span className="ops-missing">{schedule.last_error}</span>}
      </div>
    </section>
  );
}

function FactsSection({ facts }) {
  const sources = facts?.files?.sources || [];
  const server = facts?.servers?.this_server || {};
  const heavy = facts?.servers?.heavy_jobs || {};
  const packages = facts?.libraries?.packages || [];
  const ops = facts?.operations || {};
  const heavyRunning = heavy.running || [];
  const serverRows = [
    { k: "role", label: "이 서버", value: `${server.host || "-"} · CPU ${server.cpu_count ?? "-"}코어 · RAM ${server.memory_total_gb ?? "-"}GB` },
    { k: "mem", label: "메모리/디스크", value: `메모리 ${server.memory_percent ?? "-"}% · 디스크 ${server.disk_percent ?? "-"}% (남은 ${server.disk_free_gb ?? "-"}GB)` },
    { k: "budget", label: "프로세스 한도", value: `CPU 예산 ${server.cpu_budget_cores ?? "-"}코어 · 메모리 한도 ${server.process_memory_limit_gb ?? "-"}GB · 프로필 ${server.resource_profile || "-"}` },
    { k: "mem24", label: "최근 메모리", value: facts?.servers?.memory_24h ? `평균 ${facts.servers.memory_24h.avg}% · p95 ${facts.servers.memory_24h.p95}% · 최대 ${facts.servers.memory_24h.max}%` : "-" },
    { k: "heavy", label: "무거운 작업", value: `${heavyRunning.length ? heavyRunning.map((r) => `${r.label} ${Math.round(r.elapsed_sec)}초`).join(" · ") : "실행 중 없음"} · 기동 후 실행 ${heavy.stats?.ran ?? 0} · 메모리로 미룸 ${heavy.stats?.memory_guard ?? 0}` },
    { k: "env", label: "환경변수 지정", value: Object.entries(facts?.servers?.env_overrides || {}).map(([k, v]) => `${k}=${v}`).join(" · ") || "없음(자동값)" },
    { k: "cache", label: "캐시 예산 설정", value: Object.entries(facts?.servers?.cache_settings || {}).map(([k, v]) => `${k}=${v}`).join(" · ") || "없음(적응형 기본값)" },
  ];
  const opsRows = [
    { k: "llm", label: "LLM", value: ops.llm ? `${ops.llm.available ? "연결됨" : "미연결"} · ${ops.llm.model || "-"} · 상태 ${ops.llm.status || "-"}` : "-" },
    { k: "backup", label: "백업", value: ops.backup ? `${ops.backup.enabled ? "켜짐" : "꺼짐"} · ${ops.backup.interval_hours}시간 주기 · ${ops.backup.count}개 · 최근 ${ops.backup.latest ? when(ops.backup.latest) : "없음"}` : "-" },
    { k: "mail", label: "메일", value: ops.mail ? `${ops.mail.enabled ? "켜짐" : "꺼짐"} · API ${ops.mail.api_url_set ? "설정됨" : "없음"} · 앱 주소 ${ops.mail.app_base_url_set ? "설정됨" : "없음"}` : "-" },
    { k: "users", label: "사용자", value: ops.users ? `${ops.users.total}명 · 승인 대기 ${ops.users.pending}명` : "-" },
    { k: "dk", label: "기본지식", value: ops.domain_knowledge ? `${Number(ops.domain_knowledge.chars || 0).toLocaleString()}자` : "-" },
    { k: "log", label: "로그 폴더", value: ops.log_dir_mb != null ? `${ops.log_dir_mb}MB` : "-" },
  ];
  const kv = [{ key: "label", label: "항목", width: 140 }, { key: "value", label: "값" }];
  return (
    <details className="ops-facts">
      <summary>수집한 사실 보기 — 원천 DB {sources.length}개 · 라이브러리 {packages.length}개</summary>
      <div className="ops-facts__body">
        <h5>원천 DB <span>{facts?.files?.db_root}</span></h5>
        <DataTable
          rowKey="name"
          rows={sources}
          emptyTitle="훑은 원천 DB 가 없습니다"
          columns={[
            { key: "name", label: "폴더" },
            { key: "products", label: "제품", numeric: true, width: 70 },
            { key: "files", label: "파일", numeric: true, width: 90, render: (r) => `${Number(r.files || 0).toLocaleString()}${r.truncated ? "+" : ""}` },
            { key: "size_gb", label: "크기(GB)", numeric: true, width: 90 },
            { key: "fmt", label: "parquet/csv", width: 110, render: (r) => `${r.parquet}/${r.csv}` },
            { key: "newest", label: "최신 파일", width: 170, render: (r) => (r.newest ? `${r.newest.replace("T", " ")} (${r.newest_age_days}일 전)` : "-") },
          ]}
        />
        <h5>서버</h5>
        <DataTable rowKey="k" rows={serverRows} columns={kv} />
        <h5>라이브러리 <span>Python {facts?.libraries?.python}</span></h5>
        <DataTable
          rowKey="name"
          rows={packages}
          columns={[
            { key: "name", label: "패키지", width: 170 },
            { key: "version", label: "버전", width: 110, render: (r) => r.version || <span className="ops-missing">없음</span> },
            { key: "required", label: "구분", width: 70, render: (r) => (r.required ? "필수" : "선택") },
            { key: "used_for", label: "쓰는 곳" },
          ]}
        />
        <h5>운영 설정</h5>
        <DataTable rowKey="k" rows={opsRows} columns={kv} />
      </div>
    </details>
  );
}

export default function OpsScanPanel({ onOpenAdminTab }) {
  const [report, setReport] = useState(null);
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [useAi, setUseAi] = useState(true);
  const [error, setError] = useState("");
  const [area, setArea] = useState("");
  const [highlight, setHighlight] = useState("");
  const [schedule, setSchedule] = useState(null);
  const [savingSchedule, setSavingSchedule] = useState(false);

  useEffect(() => {
    let active = true;
    sf("/api/admin/ops-scan")
      .then((data) => {
        if (!active) return;
        setReport(data?.report || null);
        setHistory(Array.isArray(data?.history) ? data.history : []);
        setSchedule(data?.schedule || null);
        if (data?.busy) setError("다른 관리자가 스캔 중입니다. 끝나면 다시 열어 보세요.");
      })
      .catch((err) => { if (active) setError(err.message || "마지막 스캔 결과를 불러오지 못했습니다."); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  const runScan = () => {
    setRunning(true);
    setError("");
    sf("/api/admin/ops-scan/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ use_ai: useAi }),
    })
      .then((data) => {
        const next = data?.report || null;
        setReport(next);
        if (next) {
          setHistory((prev) => [{ scanned_at: next.scanned_at, actor: next.actor, counts: next.counts, ai_used: !!next.ai?.used, elapsed_s: next.elapsed_s }, ...prev].slice(0, 30));
        }
      })
      .catch((err) => setError(err.message || "스캔을 끝내지 못했습니다."))
      .finally(() => setRunning(false));
  };

  const saveSchedule = (patch) => {
    setSavingSchedule(true);
    setSchedule((prev) => (prev ? { ...prev, ...patch } : prev));
    sf("/api/admin/ops-scan/schedule", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    })
      .then((data) => setSchedule(data?.schedule || null))
      .catch((err) => setError(err.message || "자동 점검 설정을 저장하지 못했습니다."))
      .finally(() => setSavingSchedule(false));
  };

  const findings = report?.findings || [];
  const findingsById = useMemo(() => Object.fromEntries(findings.map((f) => [f.id, f])), [findings]);
  const visible = area ? findings.filter((f) => f.area === area) : findings;
  const areaCounts = useMemo(() => findings.reduce((acc, f) => ({ ...acc, [f.area]: (acc[f.area] || 0) + 1 }), {}), [findings]);
  const ai = report?.ai || null;

  const go = (link) => {
    if (!link) return;
    if (link.startsWith("admin:")) {
      onOpenAdminTab?.(link.slice(6));
      return;
    }
    window.dispatchEvent(new CustomEvent("flow:navigate", { detail: { tab: link } }));
  };

  const jump = (id) => {
    setArea("");
    setHighlight(id);
    window.requestAnimationFrame(() => {
      document.getElementById(`ops-finding-${id}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  };

  return (
    <section className="ops-panel">
      <header className="ops-header">
        <div>
          <h4>운영 점검 스캔</h4>
          <p>
            파일탐색기 DB·기준 파일, 서버 자원·무거운 작업 상태, 설치된 라이브러리, 백업·메일·LLM 같은 운영 설정을 훑어
            고치면 좋을 점을 추천합니다. <b>아무것도 자동으로 바꾸지 않습니다</b> — 항목의 이동 링크에서 직접 고치세요.
          </p>
        </div>
        <div className="ops-header__actions">
          <label className="ops-toggle">
            <input type="checkbox" checked={useAi} onChange={(e) => setUseAi(e.target.checked)} disabled={running} />
            AI 추천 포함
          </label>
          <Button variant="primary" onClick={runScan} disabled={running}>
            <Icon name={running ? "hourglass" : "search"} />
            {running ? (useAi ? "스캔·AI 추천 중…" : "스캔 중…") : "스캔"}
          </Button>
        </div>
      </header>

      <ScheduleBar schedule={schedule} onSave={saveSchedule} saving={savingSchedule} />

      {error && <Banner tone="danger" onClose={() => setError("")}>{error}</Banner>}

      {loading ? (
        <div className="ops-empty">마지막 스캔 결과를 불러오는 중입니다…</div>
      ) : !report ? (
        <div className="ops-empty">아직 스캔한 적이 없습니다. 스캔을 누르면 수 초 안에(AI 추천 포함 시 1분 안팎) 결과가 나옵니다.</div>
      ) : (
        <>
          <div className="ops-meta">
            <span><Icon name="clock" /> {when(report.scanned_at)} · {report.actor || "-"} · {report.elapsed_s}초</span>
            {Object.entries(SEVERITY).map(([key, meta]) => (
              <span key={key} className="ops-meta__count"><StatusDot tone={meta.tone} /> {meta.label} {report.counts?.[key] || 0}</span>
            ))}
          </div>

          <section className="ops-section">
            <div className="ops-section__head">
              <h5><Icon name="sparkle" /> AI 추천</h5>
              {ai?.used && <span className="ops-muted">{ai.model || "LLM"} · {ai.elapsed_s}초</span>}
            </div>
            {ai?.used ? (
              <>
                {ai.summary && <p className="ops-ai-summary">{ai.summary}</p>}
                {ai.recommendations?.length ? (
                  <ul className="ops-list">
                    {ai.recommendations.map((rec, i) => (
                      <RecommendationCard key={`${rec.title}-${i}`} rec={rec} findingsById={findingsById} onJump={jump} />
                    ))}
                  </ul>
                ) : <div className="ops-empty">AI 가 추가로 추천할 항목을 찾지 못했습니다. 아래 점검 결과를 보세요.</div>}
              </>
            ) : (
              <Banner tone="info">{ai?.message || "AI 추천 없이 규칙 점검만 실행했습니다."} 아래 점검 결과마다 권장 조치가 있습니다.</Banner>
            )}
          </section>

          <section className="ops-section">
            <div className="ops-section__head">
              <h5><Icon name="clipboard" /> 점검 결과 {findings.length}건</h5>
              <SegmentedSwitch
                size="sm"
                ariaLabel="영역"
                value={area}
                onChange={setArea}
                options={AREA_OPTIONS.map((o) => ({ ...o, label: o.value ? `${o.label} ${areaCounts[o.value] || 0}` : o.label }))}
              />
            </div>
            {visible.length ? (
              <ul className="ops-list">
                {visible.map((f) => <FindingRow key={f.id} finding={f} highlighted={highlight === f.id} onGo={go} />)}
              </ul>
            ) : <div className="ops-empty">이 영역에는 점검할 항목이 없습니다.</div>}
          </section>

          <FactsSection facts={report.facts} />

          {history.length > 1 && (
            <details className="ops-facts">
              <summary>이전 스캔 {history.length}회</summary>
              <div className="ops-facts__body">
                <DataTable
                  rowKey="scanned_at"
                  rows={history}
                  columns={[
                    { key: "scanned_at", label: "시각", width: 170, render: (r) => when(r.scanned_at) },
                    { key: "actor", label: "실행자", width: 110 },
                    { key: "counts", label: "높음/보통/낮음/참고", render: (r) => ["high", "medium", "low", "info"].map((k) => r.counts?.[k] || 0).join(" / ") },
                    { key: "ai_used", label: "AI", width: 60, render: (r) => (r.ai_used ? "사용" : "-") },
                    { key: "elapsed_s", label: "소요(초)", numeric: true, width: 80 },
                  ]}
                />
              </div>
            </details>
          )}
        </>
      )}
    </section>
  );
}
