/* S3StatusLight v8.6.4 — S3 동기화 신호등. FileBrowser/TableMap 헤더에서 공용.
   - GET /api/s3ingest/health 60s 폴링.
   - light: green/yellow/red/none → 색 + 라벨.
   - hover 시 상세 (마지막 동기화 시각, 실패 수, 설정 수, AWS CLI 가용성).
*/
import { useEffect, useState } from "react";
import { sf } from "../lib/api";

const COLORS = {
  green:  { bg: "#22c55e", label: "S3 정상" },
  yellow: { bg: "#f59e0b", label: "S3 지연/주의" },
  red:    { bg: "#ef4444", label: "S3 끊김" },
  none:   { bg: "#6b7280", label: "S3 미설정" },
};

export default function S3StatusLight({ compact = false }) {
  const [data, setData] = useState(null);
  const [hover, setHover] = useState(false);
  useEffect(() => {
    let alive = true;
    const load = () => sf("/api/s3ingest/health")
      .then(d => { if (alive) setData(d); })
      .catch(() => {});
    load();
    const t = setInterval(load, 60000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  const light = data?.light || "none";
  const c = COLORS[light] || COLORS.none;
  const blink = light === "red";
  const ringStyle = blink ? { animation: "s3blink 1.4s ease-in-out infinite" } : {};
  return (
    <span style={{ position: "relative", display: "inline-flex", alignItems: "center", gap: 6 }}
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      title={c.label + (data?.message ? " — " + data.message : "")}>
      <style>{`@keyframes s3blink { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:.45;transform:scale(0.85)} }`}</style>
      <span style={{
        width: 10, height: 10, borderRadius: "50%", background: c.bg,
        boxShadow: `0 0 6px ${c.bg}`, flexShrink: 0, ...ringStyle,
      }} />
      {!compact && (
        <span style={{ fontSize: 10, color: "var(--text-secondary)", fontFamily: "monospace", fontWeight: 600 }}>
          {c.label}
        </span>
      )}
      {hover && data && (
        <div style={{
          position: "absolute", top: "calc(100% + 6px)", right: 0, zIndex: 1000,
          minWidth: 240, padding: "8px 12px", borderRadius: 6,
          background: "var(--bg-secondary)", border: "1px solid var(--border)",
          boxShadow: "0 4px 12px rgba(0,0,0,0.3)", fontSize: 11, color: "var(--text-primary)",
          fontFamily: "monospace", lineHeight: 1.7,
        }}>
          <div style={{ fontWeight: 700, color: c.bg, marginBottom: 4 }}>● {c.label}</div>
          <div>설정 항목: <span style={{ color: "var(--accent)" }}>{data.items_configured}</span></div>
          <div>실행 중: <span style={{ color: "var(--accent)" }}>{data.running_now}</span></div>
          <div>최근 실패: <span style={{ color: data.recent_failures ? "#ef4444" : "var(--text-primary)" }}>{data.recent_failures}/{data.recent_total}</span></div>
          <div>AWS CLI: <span style={{ color: data.aws_available ? "#22c55e" : "#ef4444" }}>{data.aws_available ? "사용 가능" : "미설치"}</span></div>
          <div>마지막 동기화: <span style={{ color: "var(--text-secondary)" }}>{(data.last_synced_at || "—").replace("T", " ").slice(0, 16)}</span></div>
          {data.stale_6h && <div style={{ color: "#f59e0b" }}>⚠ 6시간 이상 동기화 없음</div>}
        </div>
      )}
    </span>
  );
}
