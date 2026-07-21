import { useEffect, useState } from "react";
import Loading from "../components/Loading";
import { Banner, PageHeader, Panel, Pill, StatusDot, statusPalette } from "../components/UXKit";
import { sf } from "../lib/api";

// v8.8.18: psutil 기반 CPU/Mem/Disk 실시간 + 24h 히스토리 mini-chart + 유휴 부하 상태 배너.
export default function My_Monitor() {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = () => {
    sf("/api/system/stats?history_limit=288")
      .then((d) => { setStats(d); setLoading(false); })
      .catch((e) => { console.warn("[Monitor] load failed:", e); setLoading(false); });
  };

  useEffect(() => {
    let alive = true;
    const tick = () => { if (alive) load(); };
    tick();
    const iv = setInterval(tick, 15000);
    return () => { alive = false; clearInterval(iv); };
  }, []);

  if (loading) {
    return (
      <div className="flow-page" style={{ display: "grid", placeItems: "center", minHeight: "calc(100vh - 48px)" }}>
        <Loading text="Loading..." />
      </div>
    );
  }

  const cur = stats?.current || {};
  const state = stats?.state || {};
  const hist = stats?.history || [];

  const toneForPct = (v) => (v > 85 ? "bad" : v > 70 ? "warn" : "ok");
  const pctColor = (v) => statusPalette[toneForPct(v)].fg;

  const bar = (pct) => {
    const v = Number(pct || 0);
    return (
      <div style={{ height: 8, borderRadius: 4, background: "var(--bg-hover)", overflow: "hidden", flex: 1 }}>
        <div style={{ height: "100%", borderRadius: 4, background: pctColor(v), width: `${v}%`, transition: "width 0.5s" }} />
      </div>
    );
  };

  const Sparkline = ({ data, field, color }) => {
    if (!data || data.length < 2) {
      return <div style={{ height: 50, color: "var(--text-secondary)", fontSize: 10 }}>데이터 수집 중...</div>;
    }
    const vals = data.map((d) => Number(d[field] || 0));
    const n = vals.length;
    const W = 600;
    const H = 60;
    const pad = 4;
    const step = (W - pad * 2) / Math.max(1, n - 1);
    const pts = vals.map((v, i) => [pad + i * step, H - pad - ((v / 100) * (H - pad * 2))].join(",")).join(" ");
    return (
      <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{ display: "block", maxWidth: "100%" }}>
        {[25, 50, 75, 85].map((y) => {
          const yy = H - pad - ((y / 100) * (H - pad * 2));
          return (
            <line
              key={y}
              x1={pad}
              x2={W - pad}
              y1={yy}
              y2={yy}
              stroke={y === 85 ? "var(--bad)" : "var(--border)"}
              strokeWidth={y === 85 ? 1 : 0.4}
              strokeDasharray={y === 85 ? "4 3" : "2 2"}
            />
          );
        })}
        <polyline fill="none" stroke={color} strokeWidth="1.5" points={pts} />
      </svg>
    );
  };

  const GaugePanel = ({ title, value, sub, field, color }) => {
    const v = Number(value || 0);
    const tone = toneForPct(v);
    return (
      <Panel
        title={title}
        right={<Pill tone={tone}>{v}%</Pill>}
        bodyStyle={{ display: "grid", gap: 10 }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <StatusDot tone={tone} />
          <span style={{ fontSize: 22, fontWeight: 800, fontFamily: "monospace", color: pctColor(v) }}>{v}%</span>
          {sub && <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--text-secondary)" }}>{sub}</span>}
        </div>
        {bar(v)}
        <Sparkline data={hist} field={field} color={color} />
      </Panel>
    );
  };

  const loadActive = state.load_active;
  const pausedUntil = state.paused_until;
  const psutilOK = state.psutil_available;

  return (
    <div className="flow-page" style={{ minHeight: "calc(100vh - 48px)", display: "flex", flexDirection: "column" }}>
      <PageHeader
        title="> system_monitor"
        subtitle="CPU / Memory / Disk · 24h history"
        right={<Pill tone="neutral">auto-refresh 15s</Pill>}
      />

      <div style={{ padding: 16, display: "grid", gap: 12, maxWidth: 1180, width: "100%" }}>
        {!psutilOK && (
          <Banner tone="bad">
            psutil 미설치 - CPU/Mem/Disk 측정치가 0으로 나올 수 있습니다. pip install psutil
          </Banner>
        )}
        {loadActive && (
          <Banner tone="warn">
            유휴 부하 생성 중 - 시작 {state.load_started_at?.slice(11, 19)}, 예상 종료 {state.load_estimated_end?.slice(11, 19)}. 사용자 활동 감지 시 즉시 중단.
          </Banner>
        )}
        {!loadActive && pausedUntil && (
          <Banner tone="info">
            유휴 체크 대기 중 - 사용자 활동 감지 후 30분. 해제 예정 {pausedUntil.slice(11, 19)}
          </Banner>
        )}

        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(220px, 1fr))", gap: 12 }}>
          <GaugePanel title="CPU" value={cur.cpu_percent} field="cpu_percent" color="var(--accent)" />
          <GaugePanel title="Memory" value={cur.memory_percent} field="memory_percent" color="var(--info)" sub={`${cur.memory_used_gb || 0} / ${cur.memory_total_gb || 0} GB`} />
          <GaugePanel title="Disk" value={cur.disk_percent} field="disk_percent" color="var(--text-secondary)" sub={`${cur.disk_used_gb || 0} / ${cur.disk_total_gb || 0} GB`} />
        </div>

        <Panel title="자원 활용 정책" right={<Pill tone="accent">v8.8.18</Pill>}>
          <div style={{ fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.7 }}>
            최근 {state.window_hours || 6}시간 동안 CPU/Memory 가 한 번도 <b style={{ color: "var(--bad)" }}>{state.threshold_pct || 85}%</b> 이상이 아니었으면
            5~10분 간 더미 부하를 생성해 서버 유휴를 방지합니다. 사용자 활동(API 호출·페이지 이동·로그인) 감지 시 <b>즉시 중단 + 30분 대기</b>.
            <br />
            마지막 사용자 활동: <code>{state.last_user_activity || "(아직 없음)"}</code>
          </div>
        </Panel>

        <div style={{ fontSize: 11, color: "var(--text-secondary)", fontFamily: "monospace", textAlign: "right" }}>
          샘플링 5분 주기 · 마지막 업데이트 {(cur.timestamp || "-").slice(11, 19)} · 히스토리 {hist.length} rows
        </div>
      </div>
    </div>
  );
}
