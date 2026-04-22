/* S3StatusLight v8.8.3 — S3 동기화 신호등. TableMap 헤더에서 공용.
   - GET /api/s3ingest/health 60s 폴링.
   - light: green/yellow/red/none → 색 + 라벨.
   - v8.8.2: 방향(다운/업) 구분을 신호등만 보고도 알 수 있도록 ⬇︎다운 / ⬆︎업 텍스트 라벨 추가 + 아이콘 크기 확대.
   - v8.8.3: 화살표를 신호등(원) 바깥 텍스트가 아닌 원 "안"에 배치 — 다운↓/업↑ 직관화.
   - v8.8.23: 디자인 변경 — 원형 배경 제거, 화살표 자체가 신호등 색. 업=위 화살표, 다운=아래 화살표.
     녹색=정상 / 빨강=에러 / 회색=대기·미활성. 아이콘 크기·굵기는 기존 시각 비중 유지(22×22, stroke 3).
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

  // v8.8.23: 원형 배경 제거 — 화살표 자체가 신호등 색.
  //   direction="down" → 아래 화살표, "up" → 위 화살표.
  //   stroke=color 3px, linecap/join=round, fill=none. viewBox 22x22 로 여유 여백.
  //   red 상태는 깜빡임(애니메이션 유지). boxShadow 는 제거하고 drop-shadow filter 로 대체(색감 유지).
  const ArrowSvg = ({ direction, color, blink }) => {
    const isDown = direction === "down";
    const style = {
      display: "block",
      filter: `drop-shadow(0 0 2px ${color}66)`,
      ...(blink ? ringStyle : {}),
    };
    return (
      <svg width="22" height="22" viewBox="0 0 22 22" style={style}>
        {isDown ? (
          <g stroke={color} strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" fill="none">
            <line x1="11" y1="3.5" x2="11" y2="16" />
            <polyline points="5,11 11,17 17,11" />
          </g>
        ) : (
          <g stroke={color} strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" fill="none">
            <line x1="11" y1="18.5" x2="11" y2="6" />
            <polyline points="5,11 11,5 17,11" />
          </g>
        )}
      </svg>
    );
  };

  const pill = (direction, text, color, tip, isRed) => (
    <span style={{
      display:"inline-flex", alignItems:"center", gap:4,
      padding:"1px 6px 1px 2px", borderRadius:10,
      lineHeight: 1,
    }} title={tip}>
      <ArrowSvg direction={direction} color={color} blink={isRed} />
      <span style={{fontSize:10, color, fontWeight:700, letterSpacing:"-0.02em"}}>{text}</span>
    </span>
  );

  return (
    <span style={{ position: "relative", display: "inline-flex", alignItems: "center", gap: 6 }}
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      title={c.label + (data?.message ? " — " + data.message : "")}>
      <style>{`@keyframes s3blink { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:.45;transform:scale(0.85)} }`}</style>
      {pill("down", "다운", downColor, "다운로드(S3→로컬) — " + downLabel, downKey==="red")}
      {pill("up", "업", upColor, "업로드(로컬→S3) — " + upLabel, upKey==="red")}
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
