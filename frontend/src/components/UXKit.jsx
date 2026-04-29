// UXKit.jsx — v1.0.0 (v8.8.33)
// flow 공용 UX 프리미티브. FileBrowser / SplitTable 패턴을 기준으로 추출.
// 목적: 페이지별로 중복 작성되는 pill / tab / header / badge / table styling 을
//       단일 소스로 통일. 각 페이지는 이 컴포넌트만 import 하면 FileBrowser 와
//       동일한 톤·색·스페이싱을 얻는다.

// ── Colors (CSS variable 우선) ──────────────────────────
const c = {
  accent: "var(--accent)",
  accentGlow: "var(--accent-glow)",
  text: "var(--text-primary)",
  textSub: "var(--text-secondary)",
  border: "var(--border)",
  bg1: "var(--bg-primary)",
  bg2: "var(--bg-secondary)",
  bg3: "var(--bg-tertiary)",
  bgHover: "var(--bg-hover)",
  ok: "#22c55e",
  warn: "#f97316",
  bad: "#ef4444",
  info: "#3b82f6",
};

export const uxColors = c;
export const uxRadii = { xs: 2, sm: 3, md: 4, lg: 5 };
export const flowLabels = {
  "Charts": "차트",
  "FAB Progress": "FAB 진행",
  "Alert Watch": "알림 감시",
  "Lot Search": "랏 검색",
  "Measured ET": "ET 측정 이력",
  "Reformatter Index": "레포트 인덱스",
  "Report Scoreboard": "레포트 스코어보드",
  "Statistical Table": "통계 테이블",
  "Trend": "추이",
  "Cumulative Plot": "누적 분포",
  "Box Table": "박스 테이블",
  "WF Map": "WF 맵",
  "Radius Plot": "Radius 플롯",
  "Index Page": "인덱스 페이지",
  "Product Connection": "제품 연결",
  "Graph": "그래프",
  "Manage": "관리",
  "Preview": "미리보기",
  "Save": "저장",
  "Delete": "삭제",
};

export function uiLabel(value) {
  const s = String(value ?? "");
  return flowLabels[s] || s.replace(/\bPage\b/g, "페이지");
}

export const formControlStyle = {
  padding: "6px 10px",
  borderRadius: uxRadii.sm,
  border: `1px solid ${c.border}`,
  background: c.bg1,
  color: c.text,
  fontSize: 12,
  outline: "none",
};

export const chartPalette = {
  series: ["#6366f1","#f59e0b","#ec4899","#10b981","#3b82f6","#ef4444","#8b5cf6","#06b6d4","#f97316","#84cc16","#a855f7","#14b8a6","#e11d48","#0ea5e9","#d946ef"],
  pastel: ["#818cf8","#fbbf24","#f472b6","#34d399","#60a5fa","#f87171","#a78bfa","#22d3ee","#fb923c","#a3e635","#c084fc","#2dd4bf","#fb7185","#38bdf8","#e879f9"],
  heat: ["#dbeafe","#93c5fd","#60a5fa","#3b82f6","#1d4ed8","#1e3a8a"],
};

// 상태 팔레트 (SplitTable stCellBg 기반) — knob/mask/fab/action 공통 톤.
export const statusPalette = {
  ok: { bg: "#22c55e22", fg: "#22c55e" },
  warn: { bg: "#f9731622", fg: "#f97316" },
  bad: { bg: "#ef444422", fg: "#ef4444" },
  info: { bg: "#3b82f622", fg: "#3b82f6" },
  neutral: { bg: "var(--bg-tertiary)", fg: "var(--text-secondary)" },
  accent: { bg: "var(--accent-glow)", fg: "var(--accent)" },
};


// ── Pill ───────────────────────────────────────────────
// FileBrowser/SplitTable 의 작은 라벨 pill 표준.
// tone: "neutral"|"accent"|"ok"|"warn"|"bad"|"info"
// size: "sm"|"md"
export function Pill({ children, tone = "neutral", size = "sm", title, onClick, style = {} }) {
  const p = statusPalette[tone] || statusPalette.neutral;
  const sizeMap = {
    sm: { fontSize: 10, padding: "1px 6px", borderRadius: 3 },
    md: { fontSize: 11, padding: "2px 8px", borderRadius: 4 },
  };
  return (
    <span
      title={title}
      onClick={onClick}
      style={{
        ...sizeMap[size],
        background: p.bg,
        color: p.fg,
        fontWeight: 600,
        cursor: onClick ? "pointer" : undefined,
        whiteSpace: "nowrap",
        ...style,
      }}
    >{children}</span>
  );
}


// ── StatusDot ──────────────────────────────────────────
// S3StatusLight 형 점(signal light).  6px 원형, tone 만 달리.
export function StatusDot({ tone = "ok", title }) {
  const p = statusPalette[tone] || statusPalette.neutral;
  return (
    <span title={title} style={{ display: "inline-block", width: 8, height: 8, borderRadius: 8, background: p.fg, verticalAlign: "middle" }} />
  );
}


// ── Tab strip ──────────────────────────────────────────
// SplitTable 의 splittable-tab 패턴 포팅.  active 항목은 accent-glow 배경.
// items: [{k, l, badge?}], active: string, onChange: fn
export function TabStrip({ items = [], active, onChange, right = null }) {
  return (
    <div style={{ display: "flex", gap: 4, alignItems: "center", borderBottom: `1px solid ${c.border}` }}>
      {items.map(({ k, l, badge }) => {
        const isA = active === k;
        return (
          <span key={k} onClick={() => onChange && onChange(k)}
                data-active={isA ? "1" : "0"}
                style={{
                  padding: "6px 12px", fontSize: 12,
                  cursor: "pointer", userSelect: "none",
                  background: isA ? c.accentGlow : "transparent",
                  color: isA ? c.accent : c.textSub,
                  fontWeight: isA ? 600 : 400,
                  borderBottom: isA ? `2px solid ${c.accent}` : "2px solid transparent",
                  marginBottom: -1,
                  display: "inline-flex", alignItems: "center", gap: 6,
                }}>
            {uiLabel(l)}
            {badge != null && <Pill tone={isA ? "accent" : "neutral"} size="sm">{badge}</Pill>}
          </span>
        );
      })}
      {right && <span style={{ marginLeft: "auto" }}>{right}</span>}
    </div>
  );
}


// ── PageHeader ─────────────────────────────────────────
// 페이지 최상단 compact header (RootHeader 와 함께). left/center/right slot.
export function PageHeader({ title, subtitle, right, style = {} }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 12,
      padding: "8px 14px", borderBottom: `1px solid ${c.border}`,
      background: c.bg2, minHeight: 34, ...style,
    }}>
      {title && <span style={{ fontSize: 12, fontWeight: 700, color: c.textSub }}>{uiLabel(title)}</span>}
      {subtitle && <span style={{ fontSize: 11, color: c.textSub }}>{subtitle}</span>}
      {right != null && <span style={{ marginLeft: "auto" }}>{right}</span>}
    </div>
  );
}


// ── PageShell / Toolbar / Panel ───────────────────────
// SplitTable/FileBrowser 와 같은 full-height operational page frame.
export function PageShell({ children, split = false, style = {} }) {
  return (
    <div className={split ? "flow-split-page" : "flow-page"} style={{
      minHeight: split ? undefined : "calc(100vh - 52px)",
      display: split ? "flex" : "block",
      overflow: split ? "hidden" : "auto",
      ...style,
    }}>
      {children}
    </div>
  );
}

export function Toolbar({ children, right = null, style = {} }) {
  return (
    <div className="flow-toolbar" style={style}>
      {children}
      {right != null && <span style={{ marginLeft: "auto", display: "inline-flex", alignItems: "center", gap: 6 }}>{right}</span>}
    </div>
  );
}

export function Panel({ title, subtitle, right, children, style = {}, bodyStyle = {} }) {
  return (
    <section className="flow-panel" style={{ overflow: "hidden", ...style }}>
      {(title || subtitle || right) && (
        <div style={{ minHeight: 34, padding: "8px 12px", borderBottom: `1px solid ${c.border}`, display: "flex", alignItems: "center", gap: 10, background: c.bg2 }}>
          {title && <span style={{ fontSize: 12, fontWeight: 800, color: c.accent }}>{uiLabel(title)}</span>}
          {subtitle && <span style={{ fontSize: 10, color: c.textSub }}>{subtitle}</span>}
          {right != null && <span style={{ marginLeft: "auto" }}>{right}</span>}
        </div>
      )}
      <div style={{ padding: 12, ...bodyStyle }}>{children}</div>
    </section>
  );
}


// ── Banner ─────────────────────────────────────────────
// 상단 알림 배너.  tone 색깔 사용.
export function Banner({ tone = "info", children, onClose, style = {} }) {
  const p = statusPalette[tone] || statusPalette.info;
  return (
    <div style={{
      padding: "8px 12px", fontSize: 12,
      background: p.bg, color: p.fg, borderRadius: 4,
      display: "flex", alignItems: "center", gap: 10, ...style,
    }}>
      <span style={{ flex: 1 }}>{children}</span>
      {onClose && <span onClick={onClose} style={{ cursor: "pointer", fontSize: 14, fontWeight: 700 }}>×</span>}
    </div>
  );
}


// ── Two-Column Layout ─────────────────────────────────
// FileBrowser 좌측 sidebar + 우측 content 표준 골격.
// left: JSX, right: JSX, leftWidth: px (default 260)
export function TwoCol({ left, right, leftWidth = 260, style = {} }) {
  return (
    <div style={{ display: "flex", flex: 1, minHeight: 0, ...style }}>
      <div style={{
        width: leftWidth, flexShrink: 0,
        borderRight: `1px solid ${c.border}`,
        background: c.bg2, overflow: "auto",
      }}>{left}</div>
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0, overflow: "hidden" }}>{right}</div>
    </div>
  );
}


// ── DataTable — sticky header, row hover, compact.
// columns: [{key, label, width?, align?, render?(row)}]
// rows: array of objects keyed by columns[].key
// empty: string shown when rows is empty
export function DataTable({ columns = [], rows = [], empty = "데이터 없음", rowStyle, onRowClick, maxHeight }) {
  if (!rows || rows.length === 0) {
    return <div style={{ textAlign: "center", padding: 40, color: c.textSub, fontSize: 12 }}>{empty}</div>;
  }
  return (
    <div style={{ overflow: "auto", maxHeight: maxHeight }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
        <thead>
          <tr>
            {columns.map(col => (
              <th key={col.key} style={{
                textAlign: col.align || "left",
                padding: "8px 10px",
                borderBottom: `1px solid ${c.border}`,
                color: c.textSub, fontSize: 11,
                fontWeight: 600,
                background: c.bg3, position: "sticky", top: 0, zIndex: 1,
                width: col.width, whiteSpace: "nowrap",
              }}>{uiLabel(col.label)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr key={ri} onClick={onRowClick ? () => onRowClick(row) : undefined}
                style={{
                  cursor: onRowClick ? "pointer" : undefined,
                  ...(rowStyle ? rowStyle(row) : {}),
                }}>
              {columns.map(col => (
                <td key={col.key} style={{
                  padding: "6px 10px",
                  borderBottom: `1px solid ${c.border}`,
                  textAlign: col.align || "left",
                  verticalAlign: "middle",
                  color: c.text, fontSize: 12,
                }}>
                  {col.render ? col.render(row) : row[col.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}


// ── Button (primary / ghost) ─────────────────────────
export function Button({ variant = "ghost", children, onClick, disabled, title, style = {} }) {
  const base = {
    padding: "5px 12px", borderRadius: 4, fontSize: 11,
    fontWeight: 600, cursor: disabled ? "not-allowed" : "pointer",
    opacity: disabled ? 0.5 : 1, border: `1px solid ${c.accent}`,
    whiteSpace: "nowrap",
  };
  const variants = {
    primary: { background: c.accent, color: "#fff", border: `1px solid ${c.accent}` },
    ghost: { background: "transparent", color: c.accent, border: `1px solid ${c.accent}` },
    subtle: { background: "transparent", color: c.textSub, border: `1px solid ${c.border}` },
    danger: { background: "transparent", color: c.bad, border: `1px solid ${c.bad}` },
  };
  return (
    <button onClick={disabled ? undefined : onClick} disabled={disabled} title={title}
            style={{ ...base, ...variants[variant], ...style }}>
      {children}
    </button>
  );
}


// ── EmptyState ────────────────────────────────────────
export function EmptyState({ icon = "○", title, hint }) {
  return (
    <div style={{ padding: "40px 20px", textAlign: "center", color: c.textSub, fontSize: 12 }}>
      <div style={{ fontSize: 28, marginBottom: 8 }}>{icon}</div>
      <div style={{ fontWeight: 600, color: c.text, fontSize: 13 }}>{title}</div>
      {hint && <div style={{ marginTop: 4 }}>{hint}</div>}
    </div>
  );
}

export function Field({ label, children, hint, style = {} }) {
  return (
    <label style={{ display: "grid", gap: 4, ...style }}>
      <span style={{ fontSize: 10, color: c.textSub, fontFamily: "monospace" }}>{label}</span>
      {children}
      {hint && <span style={{ fontSize: 10, color: c.textSub }}>{hint}</span>}
    </label>
  );
}

export default {
  Pill, StatusDot, TabStrip, PageHeader, PageShell, Toolbar, Panel, Banner, TwoCol, DataTable, Button, EmptyState, Field,
  statusPalette, formControlStyle,
};
