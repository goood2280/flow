/* S3StatusLight v8.8.3 — S3 동기화 신호등. TableMap 헤더에서 공용.
   - GET /api/s3ingest/health 60s 폴링.
   - light: green/yellow/red/none → 색 + 라벨.
   - v8.8.2: 방향(다운/업) 구분을 신호등만 보고도 알 수 있도록 ⬇︎다운 / ⬆︎업 텍스트 라벨 추가 + 아이콘 크기 확대.
   - v8.8.3: 화살표를 신호등(원) 바깥 텍스트가 아닌 원 "안"에 배치 — 다운↓/업↑ 직관화.
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
  // v8.7.5: 다운로드(pull)/업로드(push) 각각의 최근 상태를 별도 표시.
  const downKey = data?.download_light || light;
  const upKey = data?.upload_light || "none";
  const downColor = (COLORS[downKey] || COLORS.none).bg;
  const upColor = (COLORS[upKey] || COLORS.none).bg;
  const downLabel = (COLORS[downKey] || COLORS.none).label;
  const upLabel = (COLORS[upKey] || COLORS.none).label;

  // v8.8.3: 방향별 pill — 신호등(원) 안에 화살표(↓/↑) 를 새겨 한눈에 방향 파악.
  // 원을 키워(14px) 화살표가 원 내부에 완전히 들어가도록, pill 옆 텍스트는 "다운"/"업".
  const pill = (arrow, text, color, tip, isRed) => (
    <span style={{
      display:"inline-flex", alignItems:"center", gap:4,
      padding:"2px 7px 2px 3px", borderRadius:12,
      background: color + "1e",
      border: "1px solid " + color,
      lineHeight: 1,
    }} title={tip}>
      <span style={{
        width: 18, height: 18, borderRadius: "50%", background: color,
        boxShadow: `0 0 6px ${color}`, flexShrink: 0,
        display:"inline-flex", alignItems:"center", justifyContent:"center",
        ...(isRed?ringStyle:{}),
      }}>
        <span style={{
          fontSize: 13, lineHeight: 1, color: "#fff", fontWeight: 900,
          fontFamily: "Arial, sans-serif", textShadow: "0 0 2px rgba(0,0,0,0.8)",
        }}>{arrow}</span>
      </span>
      <span style={{fontSize:10, color, fontWeight:700, letterSpacing:"-0.02em"}}>{text}</span>
    </span>
  );

  return (
    <span style={{ position: "relative", display: "inline-flex", alignItems: "center", gap: 6 }}
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      title={c.label + (data?.message ? " — " + data.message : "")}>
      <style>{`@keyframes s3blink { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:.45;transform:scale(0.85)} }`}</style>
      {pill("↓", "다운", downColor, "다운로드(S3→로컬) — " + downLabel, downKey==="red")}
      {pill("↑", "업", upColor, "업로드(로컬→S3) — " + upLabel, upKey==="red")}
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
