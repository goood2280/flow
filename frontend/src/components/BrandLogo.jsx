// v8.4.2: brand 로고는 CSS 타이포그래피 (lowercase "flow"), terminal 프롬프트는
// pixel glyphs (`>FLOW_`). 두 레이어가 공존 — 사용자 클릭으로 terminal→brand 전환.
//
//  - GLYPHS / PixelGlyph: `>FLOW_` 등 pixel 프롬프트 표기용
//  - FlowWordmark:        brand 로고용 clean sans-serif (NIKKON/CASIO 지향)
//  - BrandLogo (default): Home / Nav 에서 바로 FlowWordmark 렌더

export const GLYPHS = {
  ">": { w: 5, rows: [
    [1,1,0,0,0],
    [0,1,1,0,0],
    [0,0,1,1,0],
    [0,0,0,1,1],
    [0,0,1,1,0],
    [0,1,1,0,0],
    [1,1,0,0,0],
  ]},
  F: { w: 5, rows: [
    [1,1,1,1,1],
    [1,1,1,1,1],
    [1,1,0,0,0],
    [1,1,1,1,0],
    [1,1,1,1,0],
    [1,1,0,0,0],
    [1,1,0,0,0],
  ]},
  L: { w: 5, rows: [
    [1,1,0,0,0],
    [1,1,0,0,0],
    [1,1,0,0,0],
    [1,1,0,0,0],
    [1,1,0,0,0],
    [1,1,0,0,0],
    [1,1,1,1,1],
  ]},
  O: { w: 6, rows: [
    [0,1,1,1,1,0],
    [1,1,0,0,1,1],
    [1,1,0,0,1,1],
    [1,1,0,0,1,1],
    [1,1,0,0,1,1],
    [1,1,0,0,1,1],
    [0,1,1,1,1,0],
  ]},
  W: { w: 7, rows: [
    [1,1,0,0,0,1,1],
    [1,1,0,0,0,1,1],
    [1,1,0,1,0,1,1],
    [1,1,0,1,0,1,1],
    [1,1,1,1,1,1,1],
    [1,1,1,0,1,1,1],
    [1,1,0,0,0,1,1],
  ]},
  _: { w: 4, rows: [
    [0,0,0,0],
    [0,0,0,0],
    [0,0,0,0],
    [0,0,0,0],
    [0,0,0,0],
    [0,0,0,0],
    [1,1,1,1],
  ]},
};

const GLOW_SOFT   = "0 0 4px #f97316aa, 0 0 10px #f9731644";
const GLOW_STRONG = "0 0 6px #f97316cc, 0 0 14px #f9731666";

export function PixelGlyph({ ch, sz = 4, gap = null, color = "#f97316", glow = true, strong = false }) {
  const g = GLYPHS[ch];
  if (!g) return null;
  // gap 기본값: sz 의 20% (각 pixel 이 distinct 한 dot 처럼 보이게)
  const gp = gap != null ? gap : Math.max(1, Math.round(sz * 0.2));
  const shadow = glow ? (strong ? GLOW_STRONG : GLOW_SOFT) : "none";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: gp }}>
      {g.rows.map((row, ri) => (
        <div key={ri} style={{ display: "flex", gap: gp }}>
          {row.map((on, ci) => (
            <div key={ci} style={{
              width: sz,
              height: sz,
              background: on ? color : "transparent",
              boxShadow: on && glow ? shadow : "none",
            }} />
          ))}
        </div>
      ))}
    </div>
  );
}

// Clean brand wordmark — Outfit 900, #FF5E00 orange + #1e293b dot.
// 사용자 지정 SVG 타이포를 그대로 반영 (v8.4.4).
export function FlowWordmark({ size = "home", strong = false, onClick }) {
  const presets = {
    nav:   { fontSize: 28 },
    home:  { fontSize: 72 },
    login: { fontSize: 92 },
  };
  const p = presets[size] || presets.home;
  return (
    <span
      onClick={onClick}
      style={{
        fontFamily: "'Outfit','Pretendard','Inter','Segoe UI',sans-serif",
        fontSize: p.fontSize,
        fontWeight: 900,
        letterSpacing: size === "nav" ? "-0.05em" : "-0.06em",
        color: "#FF5E00",
        lineHeight: 1,
        userSelect: "none",
        cursor: onClick ? "pointer" : "default",
        display: "inline-block",
        transition: "transform 0.35s cubic-bezier(0.34,1.56,0.64,1), filter 0.35s ease",
        ...(strong ? { transform: "scale(1.03)", filter: "brightness(1.1)" } : {}),
      }}
    >
      flow<span style={{ color: "#1e293b" }}>.</span>
    </span>
  );
}

export default function BrandLogo({ size = "home", version, onClick }) {
  if (size === "nav") {
    return (
      <div
        className="nav-brand-logo"
        data-testid="nav-brand-logo"
        onClick={onClick}
        onMouseEnter={(e) => { e.currentTarget.style.filter = "brightness(1.2)"; }}
        onMouseLeave={(e) => { e.currentTarget.style.filter = "none"; }}
        style={{
          display: "flex",
          alignItems: "center",
          cursor: onClick ? "pointer" : "default",
          userSelect: "none",
          padding: "0 10px",
          marginRight: 14,
          flexShrink: 0,
          transition: "filter 0.25s ease",
        }}
      >
        <FlowWordmark size="nav" />
      </div>
    );
  }

  return (
    <div
      className="home-brand-logo"
      data-testid="home-brand-logo"
      style={{ display: "flex", flexDirection: "column", gap: 4, padding: "6px 0 12px", userSelect: "none" }}
    >
      <FlowWordmark size="home" />
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginLeft: 2 }}>
        <span style={{ fontSize: 10, fontFamily: "'JetBrains Mono',monospace", color: "#f97316", letterSpacing: "0.18em", fontWeight: 700, opacity: 0.85 }}>v{version || "8.4.2"}</span>
      </div>
    </div>
  );
}
