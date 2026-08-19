import { useCallback, useEffect, useState } from "react";
import { dl, postJson, sf } from "../../lib/api";
import { toast } from "../../components/Toast";
import { Banner, Button, DataTable, EmptyState, PageHeader, Panel, Pill } from "../../components/UXKit";

// Hyphen-free compatibility path avoids older reverse-proxy normalization
// issues; the backend also keeps /api/auto-report for existing clients.
const API = "/api/autoreport";
const inputStyle = {
  width: "min(720px, 100%)", boxSizing: "border-box", padding: "9px 11px",
  border: "1px solid var(--border)", borderRadius: 4,
  background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14,
  fontFamily: "monospace",
};

const STATE_LABEL = {
  queued: "대기", running: "생성 중", completed: "완료", failed: "실패",
};
const STATE_TONE = {
  queued: "warn", running: "info", completed: "ok", failed: "bad",
};

function formatTime(value) {
  if (!value) return "-";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString("ko-KR");
}

export default function My_AutoReport() {
  const [config, setConfig] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [keyValue, setKeyValue] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [downloading, setDownloading] = useState("");

  const load = useCallback(async (silent = false, includeConfig = true) => {
    try {
      const [cfg, history] = await Promise.all([
        includeConfig ? sf(`${API}/config`) : Promise.resolve(null),
        sf(`${API}/jobs?limit=100`),
      ]);
      if (cfg) setConfig(cfg);
      setJobs(history?.jobs || []);
      setError("");
    } catch (err) {
      if (!silent) setError(err.message || "Auto report 정보를 불러오지 못했습니다.");
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(false, true);
    const timer = window.setInterval(() => load(true, false), 2500);
    return () => window.clearInterval(timer);
  }, [load]);

  const submit = async () => {
    const value = keyValue.trim();
    if (!value) {
      toast("제품·LOT·STEP key를 입력해 주세요.", "warn");
      return;
    }
    setSubmitting(true);
    try {
      const result = await postJson(`${API}/jobs`, { key: value });
      setJobs(current => [result.job, ...current.filter(row => row.id !== result.job.id)]);
      setKeyValue("");
      toast("개발 서버 실행 큐에 전달했습니다.", "ok");
    } catch (err) {
      toast(err.message || "Auto report 요청에 실패했습니다.", "bad");
    } finally {
      setSubmitting(false);
    }
  };

  const download = async (row) => {
    setDownloading(row.id);
    try {
      await dl(`${API}/jobs/${encodeURIComponent(row.id)}/download`, row.filename || `${row.key}.pptx`);
      toast("PPT 다운로드를 시작했습니다.", "ok");
      load(true, false);
    } catch (err) {
      toast(err.message || "PPT 다운로드에 실패했습니다.", "bad");
    } finally {
      setDownloading("");
    }
  };

  if (loading) return <div style={{ padding: 24, color: "var(--text-secondary)" }}>Auto report를 불러오는 중…</div>;

  const execution = config?.execution || {};
  const history = config?.history || {};
  const assetsReady = Boolean(config?.ok);
  const columns = [
    { key: "state", label: "상태", width: 90, render: row => <Pill tone={STATE_TONE[row.state] || "muted"}>{STATE_LABEL[row.state] || row.state}</Pill> },
    { key: "key", label: "제품 key", render: row => <code style={{ fontSize: 12 }}>{row.key}</code> },
    { key: "phase", label: "진행", render: row => (
      <div style={{ minWidth: 180 }}>
        <div>{row.phase || "-"}</div>
        {row.error && <div title={row.error} style={{ color: "var(--bad)", fontSize: 12, maxWidth: 460, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row.error}</div>}
      </div>
    ) },
    { key: "username", label: "요청자", width: 110, render: row => row.username || "-" },
    { key: "created_at", label: "요청 시각", width: 170, render: row => formatTime(row.created_at) },
    { key: "download", label: "PPT", width: 105, align: "center", render: row => row.download_ready
      ? <Button variant="primary" disabled={downloading === row.id} onClick={() => download(row)}>{downloading === row.id ? "준비 중" : "다운로드"}</Button>
      : <span style={{ color: "var(--text-secondary)" }}>-</span>
    },
  ];

  return (
    <div style={{ minHeight: "calc(100vh - 52px)", background: "var(--bg-primary)" }}>
      <PageHeader
        title="Auto report"
        subtitle="ET · INLINE · FAB 기반 PPT 생성"
        right={<span style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <Pill tone={execution.server_role === "worker" ? "info" : "muted"}>{execution.server_role === "worker" ? "개발 서버" : "운영 서버"}</Pill>
          <Pill tone={execution.worker_alive ? "ok" : "warn"}>{execution.worker_alive ? "개발 worker 연결" : "개발 worker 대기"}</Pill>
        </span>}
      />

      <main style={{ padding: 16, display: "grid", gap: 14 }}>
        {error && <Banner tone="bad">{error}</Banner>}
        {!assetsReady && (
          <Banner tone="warn">
            DB의 <code>Auto report</code> 폴더 준비가 필요합니다. 누락: {(config?.missing || []).join(", ") || "폴더 확인 필요"}
          </Banner>
        )}
        {!execution.worker_alive && (
          <Banner tone="info">요청은 공유 큐에 보관됩니다. 개발 서버 worker가 연결되면 순서대로 실행됩니다.</Banner>
        )}
        {history.state === "failed" && (
          <Banner tone="warn">ET history 최근 갱신에 실패했습니다. {history.error || "제품별 상세 오류를 확인해 주세요."}</Banner>
        )}

        <Panel title="PPT 생성 요청" subtitle="운영 서버는 큐 전달만 하며 실제 생성은 개발 서버에서 수행합니다.">
          <div style={{ display: "grid", gap: 10 }}>
            <label style={{ display: "grid", gap: 5 }}>
              <span style={{ color: "var(--text-secondary)", fontSize: 13 }}>제품 key</span>
              <div style={{ display: "flex", gap: 8, alignItems: "stretch", flexWrap: "wrap" }}>
                <input
                  autoFocus
                  value={keyValue}
                  onChange={event => setKeyValue(event.target.value)}
                  onKeyDown={event => { if (event.key === "Enter") submit(); }}
                  placeholder="예: PRODUCT_A1000A.3_4500"
                  style={inputStyle}
                />
                <Button variant="primary" disabled={submitting || !assetsReady} onClick={submit}>{submitting ? "전달 중…" : "생성 요청"}</Button>
              </div>
              <span style={{ color: "var(--text-secondary)", fontSize: 12 }}>
                <code>제품_LOT_STEP</code>, <code>TRIGGER_제품_LOT_STEP</code>, <code>_TRIGGER_제품_LOT_STEP</code> 형식을 사용할 수 있습니다.
              </span>
            </label>
            {(config?.products || []).length > 0 && (
              <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                <span style={{ color: "var(--text-secondary)", fontSize: 12 }}>설정 제품</span>
                {config.products.map(product => (
                  <button key={product} onClick={() => setKeyValue(`${product}_`)} style={{ border: "1px solid var(--border)", borderRadius: 999, padding: "3px 9px", background: "var(--bg-secondary)", color: "var(--text-primary)", cursor: "pointer", fontSize: 12 }}>{product}</button>
                ))}
              </div>
            )}
            <div style={{ color: "var(--text-secondary)", fontSize: 12 }}>
              asset: <code>{config?.asset_dir || "-"}</code> · 큐 {execution.queue_depth || 0}건
              {history.finished_at ? ` · ET history ${formatTime(history.finished_at)}` : " · ET history 생성 대기"}
            </div>
            <div style={{ color: "var(--text-secondary)", fontSize: 12 }}>
              산출물: <code>{config?.asset_dir ? `${config.asset_dir}\\RUN\\PPTX` : "Auto report/RUN/PPTX"}</code>
              {" · HTML: "}<code>{config?.asset_dir ? `${config.asset_dir}\\RUN\\HTML` : "Auto report/RUN/HTML"}</code>
            </div>
          </div>
        </Panel>

        <Panel title="생성 내역" subtitle="완료된 PPT 다운로드는 관리자 다운로드 기록에도 남습니다." right={<Button variant="subtle" onClick={() => load(false)}>새로고침</Button>} bodyStyle={{ padding: 0 }}>
          {jobs.length
            ? <DataTable columns={columns} rows={jobs} />
            : <EmptyState icon="📑" title="생성 내역이 없습니다" hint="제품 key를 입력해 첫 Auto report를 생성하세요." />}
        </Panel>
      </main>
    </div>
  );
}
