import { useState, useEffect } from "react";
import Loading from "../components/Loading";
import { sf } from "../lib/api";

// v8.8.18: psutil 기반 CPU/Mem/Disk 실시간 + 24h 히스토리 mini-chart + 유휴 부하 상태 배너.
export default function My_Monitor() {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = () => {
    sf("/api/system/stats?history_limit=288")
      .then(d => { setStats(d); setLoading(false); })
      .catch(e => { console.warn("[Monitor] load failed:", e); setLoading(false); });
  };
  useEffect(() => {
    let alive = true; const tick = () => { if (alive) load(); };
    tick(); const iv = setInterval(tick, 15000);
    return () => { alive = false; clearInterval(iv); };
  }, []);

  if (loading) return <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "calc(100vh-48px)", background: "var(--bg-primary)" }}><Loading text="Loading..." /></div>;

  const cur = stats?.current || {};
  const state = stats?.state || {};
  const hist = stats?.history || [];

  const bar = (pct, color) => (
    <div style={{ height: 8, borderRadius: 4, background: "var(--bg-hover,#333)", overflow: "hidden", flex: 1 }}>
      <div style={{ height: "100%", borderRadius: 4, background: color, width: (pct || 0) + "%", transition: "width 0.5s" }} />
    </div>
  );
  const pctColor = v => v > 85 ? "#ef4444" : v > 70 ? "#fbbf24" : "#22c55e";

  // Mini sparkline: SVG 다중 라인 (CPU=orange, Mem=blue, Disk=gray).
  const Sparkline = ({ data, field, color }) => {
    if (!data || data.length < 2) return <div style={{ height: 50, color: "var(--text-secondary)", fontSize: 10 }}>데이터 수집 중...</div>;
    const vals = data.map(d => Number(d[field] || 0));
    const n = vals.length;
    const W = 600, H = 60, pad = 4;
    const step = (W - pad * 2) / Math.max(1, n - 1);
    const pts = vals.map((v, i) => [pad + i * step, H - pad - ((v / 100) * (H - pad * 2))].join(",")).join(" ");
    return (
      <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{ display: "block", maxWidth: "100%" }}>
        {[25, 50, 75, 85].map(y => {
          const yy = H - pad - ((y / 100) * (H - pad * 2));
          return <line key={y} x1={pad} x2={W - pad} y1={yy} y2={yy}
            stroke={y === 85 ? "#ef4444" : "#444"} strokeWidth={y === 85 ? 1 : 0.4} strokeDasharray={y === 85 ? "4 3" : "2 2"} />;
        })}
        <polyline fill="none" stroke={color} strokeWidth="1.5" points={pts} />
      </svg>
    );
  };

  const loadActive = state.load_active;
  const pausedUntil = state.paused_until;
  const psutilOK = state.psutil_available;

  return (
    <div style={{ padding: "24px 32px", background: "var(--bg-primary,#1a1a1a)", minHeight: "calc(100vh - 48px)", color: "var(--text-primary)", fontFamily: "'Pretendard',sans-serif", maxWidth: 1200 }}>
      <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 16, fontFamily: "'JetBrains Mono',monospace", color: "var(--accent,#f97316)" }}>
        {">"} system_monitor
      </div>

      {!psutilOK && (
        <div style={{ marginBottom: 16, padding: "10px 12px", border: "1px solid #ef4444", background: "rgba(239,68,68,0.08)", borderRadius: 6, color: "#ef4444", fontSize: 12 }}>
          ⚠ psutil 미설치 — CPU/Mem/Disk 측정치가 0으로 나올 수 있습니다. <code>pip install psutil</code>
        </div>
      )}
      {loadActive && (
        <div style={{ marginBottom: 12, padding: "8px 12px", border: "1px solid #f97316", background: "rgba(249,115,22,0.08)", borderRadius: 6, color: "#f97316", fontSize: 12 }}>
          🔥 유휴 부하 생성 중 — 시작 {state.load_started_at?.slice(11, 19)}, 예상 종료 {state.load_estimated_end?.slice(11, 19)}. 사용자 활동 감지 시 즉시 중단.
        </div>
      )}
      {!loadActive && pausedUntil && (
        <div style={{ marginBottom: 12, padding: "8px 12px", border: "1px solid #3b82f6", background: "rgba(59,130,246,0.08)", borderRadius: 6, color: "#3b82f6", fontSize: 12 }}>
          ⏸ 유휴 체크 대기 중 (사용자 활동 감지 후 30분) — 해제 예정 {pausedUntil.slice(11, 19)}
        </div>
      )}

      {/* 3 게이지 */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 16, marginBottom: 20 }}>
        <div style={{ background: "var(--bg-secondary,#262626)", borderRadius: 10, border: "1px solid var(--border,#333)", padding: 20 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
            <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>CPU</span>
            <span style={{ fontSize: 20, fontWeight: 700, fontFamily: "monospace", color: pctColor(cur.cpu_percent || 0) }}>{cur.cpu_percent || 0}%</span>
          </div>
          {bar(cur.cpu_percent || 0, pctColor(cur.cpu_percent || 0))}
          <div style={{ marginTop: 10 }}><Sparkline data={hist} field="cpu_percent" color="#f97316" /></div>
        </div>

        <div style={{ background: "var(--bg-secondary,#262626)", borderRadius: 10, border: "1px solid var(--border,#333)", padding: 20 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
            <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>Memory</span>
            <span style={{ fontSize: 20, fontWeight: 700, fontFamily: "monospace", color: pctColor(cur.memory_percent || 0) }}>{cur.memory_percent || 0}%</span>
          </div>
          {bar(cur.memory_percent || 0, pctColor(cur.memory_percent || 0))}
          <div style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 8 }}>{cur.memory_used_gb || 0} / {cur.memory_total_gb || 0} GB</div>
          <div style={{ marginTop: 6 }}><Sparkline data={hist} field="memory_percent" color="#3b82f6" /></div>
        </div>

        <div style={{ background: "var(--bg-secondary,#262626)", borderRadius: 10, border: "1px solid var(--border,#333)", padding: 20 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
            <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>Disk</span>
            <span style={{ fontSize: 20, fontWeight: 700, fontFamily: "monospace", color: pctColor(cur.disk_percent || 0) }}>{cur.disk_percent || 0}%</span>
          </div>
          {bar(cur.disk_percent || 0, pctColor(cur.disk_percent || 0))}
          <div style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 8 }}>{cur.disk_used_gb || 0} / {cur.disk_total_gb || 0} GB</div>
          <div style={{ marginTop: 6 }}><Sparkline data={hist} field="disk_percent" color="#94a3b8" /></div>
        </div>
      </div>

      {/* Idle policy block */}
      <div style={{ background: "var(--bg-secondary,#262626)", borderRadius: 10, border: "1px solid var(--border,#333)", padding: 20, marginBottom: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
          <span style={{ fontSize: 13, fontWeight: 700 }}>자원 활용 정책</span>
          <span style={{ fontSize: 10, padding: "2px 8px", borderRadius: 4, background: "var(--accent-glow)", color: "var(--accent)" }}>v8.8.18</span>
        </div>
        <div style={{ fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.6 }}>
          최근 {state.window_hours || 6}시간 동안 CPU/Memory 가 한 번도 <b style={{ color: "#ef4444" }}>{state.threshold_pct || 85}%</b> 이상이 아니었으면 5~10분 간 더미 부하를 생성해 서버 유휴를 방지합니다. 사용자 활동(API 호출·페이지 이동·로그인) 감지 시 <b>즉시 중단 + 30분 대기</b>.<br />
          마지막 사용자 활동: <code>{state.last_user_activity || "(아직 없음)"}</code>
        </div>
      </div>

      <div style={{ marginTop: 8, fontSize: 11, color: "var(--text-secondary)", fontFamily: "monospace", textAlign: "right" }}>
        샘플링: 5분 주기 · 마지막 업데이트 {(cur.timestamp || "-").slice(11, 19)} · 히스토리 {hist.length} rows · auto-refresh 15s
      </div>
    </div>
  );
}
