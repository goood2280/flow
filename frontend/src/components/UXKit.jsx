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
  brand: "var(--brand)",
  ok: "var(--ok)",
  warn: "var(--warn)",
  bad: "var(--danger)",
  danger: "var(--danger)",
  info: "var(--info)",
  violet: "var(--violet)",
  pink: "var(--pink)",
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
  fontSize: 14,
  outline: "none",
};

export const chartPalette = {
  series: ["#6366f1","#f59e0b","#ec4899","#10b981","#3b82f6","#ef4444","#8b5cf6","#06b6d4","#E25822","#84cc16","#a855f7","#14b8a6","#e11d48","#0ea5e9","#d946ef"],
  pastel: ["#818cf8","#fbbf24","#f472b6","#34d399","#60a5fa","#f87171","#a78bfa","#22d3ee","#fb923c","#a3e635","#c084fc","#2dd4bf","#fb7185","#38bdf8","#e879f9"],
  heat: ["#dbeafe","#93c5fd","#60a5fa","#3b82f6","#1d4ed8","#1e3a8a"],
};

// ── 카테고리 시리즈 팔레트 (스택바/다계열 차트용) ──────────────────────────
// 앞 8슬롯은 표준 카테고리 순서, 9번째부터는 같은 hue 가족의 lightness 스텝
// (composite encoding) 으로 20계열까지 확장. 순서 자체가 색약 안전장치라 임의로
// 섞지 말 것. 인접쌍 기준 검증 결과(light 표면 #fff / dark 표면 #262626):
//   색약(protan·deutan) 최소 ΔE 9.1(light) / 8.4(dark)  — 기준 ≥ 8
//   일반시야 최소 ΔE      19.6(light) / 19.3(dark)       — 기준 ≥ 15
// 21번째부터는 색을 새로 만들지 않는다 → "기타"로 접고 표에서 원값을 본다.
export const categoricalSeries = {
  light: ["#2a78d6","#eb6834","#1baf7a","#eda100","#e87ba4","#008300","#4a3aa7","#e34948",
          "#035bb4","#fd7845","#5649b6","#56c050","#b0081d","#37bf89","#98335e","#62a6fe",
          "#9e3703","#9a96ff","#037202","#e97ca5"],
  dark:  ["#3987e5","#d95926","#199e70","#c98500","#d55181","#008300","#9085e9","#e66767",
          "#0761bc","#e36230","#6052b0","#3ea939","#aa285e","#4290ef","#a83902","#8a7fe2",
          "#027902","#df5a89","#875802","#29a778"],
};
// 정체성이 없는 두 버킷 — 시리즈 색을 쓰지 않고 중립 회색으로 뒤로 물린다.
export const seriesNeutral = {
  light: { other: "#8f8d87", missing: "#c9c7c1" },
  dark: { other: "#a9a7a0", missing: "#6e6d68" },
};
export const SERIES_COLOR_LIMIT = categoricalSeries.light.length;

// 값 목록 → 색 맵. 색은 "순위"가 아니라 "값" 을 따라가야 하므로 호출부에서
// 안정적인 순서(이름순)로 정렬한 배열을 넘긴다.
export function buildSeriesColors(values, { dark = false, missingLabel = "", otherLabel = "" } = {}) {
  const pal = dark ? categoricalSeries.dark : categoricalSeries.light;
  const nt = dark ? seriesNeutral.dark : seriesNeutral.light;
  const map = {};
  let i = 0;
  for (const v of values || []) {
    if (v && v === missingLabel) map[v] = nt.missing;
    else if (v && v === otherLabel) map[v] = nt.other;
    else map[v] = pal[i++ % pal.length];
  }
  return map;
}

// 상태 팔레트 (SplitTable stCellBg 기반) — knob/mask/fab/action 공통 톤.
export const statusPalette = {
  ok: { bg: "var(--ok-50)", fg: "var(--ok)", line: "var(--ok-line)" },
  warn: { bg: "var(--warn-50)", fg: "var(--warn)", line: "var(--warn-line)" },
  bad: { bg: "var(--danger-50)", fg: "var(--danger)", line: "var(--danger-line)" },
  danger: { bg: "var(--danger-50)", fg: "var(--danger)", line: "var(--danger-line)" },
  info: { bg: "var(--info-50)", fg: "var(--info)", line: "var(--info-line)" },
  brand: { bg: "var(--brand-50)", fg: "var(--brand)", line: "var(--brand-line)" },
  violet: { bg: "var(--violet-50)", fg: "var(--violet)", line: "var(--violet-line)" },
  pink: { bg: "var(--pink-50)", fg: "var(--pink)", line: "var(--pink-line)" },
  neutral: { bg: "var(--bg-tertiary)", fg: "var(--text-secondary)" },
  accent: { bg: "var(--accent-glow)", fg: "var(--accent)" },
};


// ── Pill ───────────────────────────────────────────────
// FileBrowser/SplitTable 의 작은 라벨 pill 표준.
// tone: "neutral"|"accent"|"brand"|"ok"|"warn"|"bad"|"danger"|"info"|"violet"|"pink"
// size: "sm"|"md"
export function Pill({ children, tone = "neutral", size = "sm", title, onClick, className = "", style = {} }) {
  const p = statusPalette[tone] || statusPalette.neutral;
  const sizeMap = {
    sm: { fontSize: 14, padding: "1px 6px", borderRadius: 3 },
    md: { fontSize: 14, padding: "2px 8px", borderRadius: 4 },
  };
  const toneClass = tone === "neutral" ? "" : ` pill--${tone}`;
  return (
    <span
      className={`pill${toneClass}${className ? ` ${className}` : ""}`}
      title={title}
      onClick={onClick}
      style={{
        ...sizeMap[size],
        "--pill-bg": p.bg,
        "--pill-fg": p.fg,
        fontWeight: 600,
        cursor: onClick ? "pointer" : undefined,
        whiteSpace: "nowrap",
        ...style,
      }}
    >{children}</span>
  );
}

export function Card({ title, right, children, padding = 16, className = "", style = {}, bodyStyle = {} }) {
  return (
    <section className={`card${className ? ` ${className}` : ""}`} style={{ padding, ...style }}>
      {(title || right) && (
        <div className="card__head">
          {title && <div className="card__title">{uiLabel(title)}</div>}
          {right != null && <div className="card__right">{right}</div>}
        </div>
      )}
      <div style={bodyStyle}>{children}</div>
    </section>
  );
}

export function Chip({ mono = true, title, children, className = "", style = {} }) {
  return (
    <span className={`chip${className ? ` ${className}` : ""}`} title={title} style={{ fontFamily: mono ? "var(--font-mono)" : "inherit", ...style }}>
      {children}
    </span>
  );
}

export function TableWrap({ maxHeight, children, className = "", style = {} }) {
  return (
    <div className={`tablewrap${className ? ` ${className}` : ""}`} style={{ maxHeight, ...style }}>
      {children}
    </div>
  );
}

export function Tbl({ children, className = "", style = {}, ...props }) {
  return <table className={`tbl${className ? ` ${className}` : ""}`} style={style} {...props}>{children}</table>;
}

export function Filter({ value = "", onChange, options = [], placeholder = "전체", className = "", style = {}, ...props }) {
  const hasValue = value !== "" && value !== null && value !== undefined;
  return (
    <select
      className={`filter${hasValue ? " has-value" : ""}${className ? ` ${className}` : ""}`}
      value={value}
      onChange={onChange}
      style={style}
      {...props}
    >
      {placeholder != null && <option value="">{placeholder}</option>}
      {options.map((opt) => {
        const value = typeof opt === "object" ? opt.value : opt;
        const label = typeof opt === "object" ? opt.label : opt;
        return <option key={String(value)} value={value}>{label}</option>;
      })}
    </select>
  );
}

export function Btn({ variant = "outline", size = "md", children, className = "", disabled, ...props }) {
  const variantClass = variant === "primary" ? "btn--primary"
    : variant === "danger" ? "btn--danger"
    : variant === "ghost" ? "btn--ghost"
    : "btn--outline";
  const sizeClass = size === "sm" ? " btn--sm" : "";
  return (
    <button className={`btn ${variantClass}${sizeClass}${className ? ` ${className}` : ""}`} disabled={disabled} {...props}>
      {children}
    </button>
  );
}

export function Avatar({ name = "", tone, title, className = "", style = {} }) {
  const text = String(name || "?").trim();
  let idx = Number(tone);
  if (!Number.isFinite(idx)) {
    idx = [...text].reduce((sum, ch) => sum + ch.charCodeAt(0), 0) % 5;
  }
  const initial = text ? [...text][0].toUpperCase() : "?";
  return <span className={`av c${idx + 1}${className ? ` ${className}` : ""}`} title={title || text} style={style}>{initial}</span>;
}

export function Input({ className = "", style = {}, ...props }) {
  return <input className={`input${className ? ` ${className}` : ""}`} style={style} {...props} />;
}

export function Select({ className = "", style = {}, children, ...props }) {
  return <select className={`select${className ? ` ${className}` : ""}`} style={style} {...props}>{children}</select>;
}

export function Textarea({ className = "", style = {}, ...props }) {
  return <textarea className={`textarea${className ? ` ${className}` : ""}`} style={style} {...props} />;
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
    <div style={{ display: "flex", flexWrap: "wrap", gap: 4, rowGap: 2, alignItems: "center", borderBottom: `1px solid ${c.border}` }}>
      {items.map(({ k, l, badge }) => {
        const isA = active === k;
        return (
          <span key={k} onClick={() => onChange && onChange(k)}
                data-active={isA ? "1" : "0"}
                style={{
                  padding: "6px 12px", fontSize: 14,
                  cursor: "pointer", userSelect: "none",
                  whiteSpace: "nowrap",
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
    <div className="flow-surface-header" style={{
      display: "flex", alignItems: "center", gap: 12,
      padding: "8px 14px 8px 18px", borderBottom: `1px solid ${c.border}`,
      background: c.bg2, minHeight: 34, ...style,
    }}>
      {title && <span style={{ fontSize: 14, fontWeight: 800, color: c.text }}>{uiLabel(title)}</span>}
      {subtitle && <span style={{ fontSize: 14, color: c.textSub }}>{subtitle}</span>}
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
          {title && <span style={{ fontSize: 14, fontWeight: 800, color: c.accent }}>{uiLabel(title)}</span>}
          {subtitle && <span style={{ fontSize: 14, color: c.textSub }}>{subtitle}</span>}
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
      padding: "8px 12px", fontSize: 14,
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
    return <div style={{ textAlign: "center", padding: 40, color: c.textSub, fontSize: 14 }}>{empty}</div>;
  }
  return (
    <div style={{ overflow: "auto", maxHeight: maxHeight }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
        <thead>
          <tr>
            {columns.map(col => (
              <th key={col.key} style={{
                textAlign: col.align || "left",
                padding: "8px 10px",
                borderBottom: `1px solid ${c.border}`,
                color: c.textSub, fontSize: 14,
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
                  color: c.text, fontSize: 14,
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
    padding: "5px 12px", borderRadius: 4, fontSize: 14,
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
    <div style={{ padding: "40px 20px", textAlign: "center", color: c.textSub, fontSize: 14 }}>
      <div style={{ fontSize: 28, marginBottom: 8 }}>{icon}</div>
      <div style={{ fontWeight: 600, color: c.text, fontSize: 14 }}>{title}</div>
      {hint && <div style={{ marginTop: 4 }}>{hint}</div>}
    </div>
  );
}

export function Field({ label, children, hint, style = {} }) {
  return (
    <label style={{ display: "grid", gap: 4, ...style }}>
      <span style={{ fontSize: 14, color: c.textSub, fontFamily: "monospace" }}>{label}</span>
      {children}
      {hint && <span style={{ fontSize: 14, color: c.textSub }}>{hint}</span>}
    </label>
  );
}

export default {
  Pill, Card, Chip, TableWrap, Tbl, Filter, Btn, Avatar, Input, Select, Textarea,
  StatusDot, TabStrip, PageHeader, PageShell, Toolbar, Panel, Banner, TwoCol, DataTable, Button, EmptyState, Field,
  statusPalette, formControlStyle,
};
