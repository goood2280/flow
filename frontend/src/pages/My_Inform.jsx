/* My_Inform.jsx v8.7.0 — 모듈 인폼 시스템 (역할 뷰 + 체크 + flow 상태 + SplitTable 연동).
 *
 * 보안: auth 미들웨어 + 세션 토큰 그대로. sf() 가 X-Session-Token 자동 주입.
 * 삭제 정책: 작성자 본인만 (관리자도 불가) — 서버에서도 동일하게 강제됨.
 */
import React, { useEffect, useMemo, useState, useRef } from "react";
import { sf, authSrc, postJson, userLabel, userMatches } from "../lib/api";
import PageGear from "../components/PageGear";
import { Button, Pill, statusPalette, chartPalette } from "../components/UXKit";

const API = "/api/informs";
export const WIZARD_STEPS = ["lot", "module_reason", "splittable", "mail_preview", "review"];
export const WIZARD_BACKEND_CALLS = [
  "/api/informs/config",
  "/api/splittable/lot-candidates",
  "/api/informs/splittable-snapshot",
  "/api/informs/modules/recipients",
  "/api/informs/modules/knob-map",
  "POST /api/informs",
];
const WIZARD_DRAFT_KEY = "flow_inform_wizard_draft_v1";
const OK = statusPalette.ok;
const WARN = statusPalette.warn;
const BAD = statusPalette.bad;
const INFO = statusPalette.info;
const NEUTRAL = statusPalette.neutral;
const INDIGO = { fg: chartPalette.series[0], bg: `${chartPalette.series[0]}22`, soft: `${chartPalette.series[0]}11`, border: `${chartPalette.series[0]}66` };
const PURPLE = { fg: chartPalette.series[6], bg: `${chartPalette.series[6]}22`, soft: `${chartPalette.series[6]}11`, border: `${chartPalette.series[6]}33` };
const GREEN = { fg: chartPalette.series[3], bg: `${chartPalette.series[3]}22`, soft: `${chartPalette.series[3]}11`, border: `${chartPalette.series[3]}33` };
const SKY = { fg: chartPalette.series[7], bg: `${chartPalette.series[7]}22` };
const TEAL = { fg: chartPalette.series[11], bg: `${chartPalette.series[11]}22` };
const SLATE = { fg: "var(--text-secondary)", bg: "var(--bg-tertiary)" };
const WHITE = "var(--bg-secondary)";
const MODULE_SERIES = [chartPalette.series[0], chartPalette.series[2], chartPalette.series[11], chartPalette.series[8], chartPalette.series[6], chartPalette.series[12], chartPalette.series[3]];
const INFORM_TABS = [
  ["matrix", "매트릭스"],
  ["inform", "인폼"],
  ["modules", "모듈 요약"],
  ["audit", "로그"],
];
const DEFAULT_SHARED_FILTERS = {
  products: [],
  modules: [],
  statuses: [],
  lot: "",
  period: "30",
  start: "",
  end: "",
  types: [],
};
const AUDIT_TYPES = [
  ["status_change", "상태변경"],
  ["mail", "메일"],
  ["comment", "댓글"],
  ["edit", "수정"],
  ["create", "생성"],
  ["delete", "삭제"],
];

// v8.8.30: ML_TABLE_PRODA 같은 내부 식별자를 UI 에서는 PRODA 로 축약 표시.
//   서버 호출 / 저장 / ML_TABLE 매칭은 그대로 full name 유지, 화면 렌더만 축약.
function stripMlPrefix(s) {
  if (!s) return "";
  const v = String(s);
  return v.startsWith("ML_TABLE_") ? v.slice("ML_TABLE_".length) : v;
}

function addLotToken(out, seen, value) {
  const s = String(value || "").trim();
  if (!s || s === "—" || s === "-" || s === "None" || s === "null") return;
  const key = s.toLowerCase();
  if (seen.has(key)) return;
  seen.add(key);
  out.push(s);
}

function splitFabLotsFromNode(node) {
  const out = [];
  const seen = new Set();
  const addMany = (value) => {
    String(value || "")
      .split(/\s*[,/]\s*/)
      .forEach(v => addLotToken(out, seen, v));
  };
  addMany(node?.fab_lot_id_at_save);
  const st = node?.embed_table?.st_view || {};
  (st.header_groups || []).forEach(g => addLotToken(out, seen, g?.label));
  (st.wafer_fab_list || []).forEach(v => addLotToken(out, seen, v));
  return out;
}

function lotSearchText(node) {
  return [
    node?.lot_id,
    node?.root_lot_id,
    node?.wafer_id,
    node?.fab_lot_id_at_save,
    ...splitFabLotsFromNode(node),
  ].filter(Boolean).join(" ");
}

function informLotDisplay(node, { maxFabLots = 4 } = {}) {
  const root = String(node?.lot_id || node?.root_lot_id || node?.wafer_id || "").trim();
  const fabs = splitFabLotsFromNode(node).filter(v => v !== root);
  if (!fabs.length) return root;
  const shown = fabs.slice(0, maxFabLots).join(" / ");
  const more = fabs.length > maxFabLots ? ` +${fabs.length - maxFabLots}` : "";
  return root ? `${root} · ${shown}${more}` : `${shown}${more}`;
}

function isFabLotInput(lot, options = []) {
  const s = String(lot || "").trim();
  const picked = (options || []).find(o => String(o.value || "").trim() === s);
  if (picked?.type) return picked.type === "fab";
  return /[._\-/]/.test(s);
}

function emptyEmbedTable() {
  return { source: "", columns: [], rows: [], note: "" };
}

function hasEmbedSnapshot(embed) {
  if (!embed) return false;
  const rows = Array.isArray(embed.rows) ? embed.rows : [];
  const columns = Array.isArray(embed.columns) ? embed.columns : [];
  const stRows = Array.isArray(embed.st_view?.rows) ? embed.st_view.rows : [];
  const stHeaders = Array.isArray(embed.st_view?.headers) ? embed.st_view.headers : [];
  return rows.length > 0 || columns.length > 0 || stRows.length > 0 || stHeaders.length > 0;
}

function embedSnapshotRowCount(embed) {
  if (!embed) return 0;
  const stRows = Array.isArray(embed.st_view?.rows) ? embed.st_view.rows : [];
  if (stRows.length) return stRows.length;
  const rows = Array.isArray(embed.rows) ? embed.rows : [];
  return rows.length;
}

function parseDuplicateProductError(error) {
  const text = String(error?.message || error || "");
  const match = text.match(/existing_product['"]?\s*:\s*['"]?([^'",}\]]+)/i);
  return match?.[1] ? stripMlPrefix(match[1].trim()) : "";
}

const STATUS_META = {
  received:    { label: "접수",   color: INFO.fg, dot: "○" },
  reviewing:   { label: "검토중", color: INFO.fg, dot: "◐" },
  in_progress: { label: "진행중", color: WARN.fg, dot: "◑" },
  completed:   { label: "완료",   color: OK.fg, dot: "●" },
};
// v8.7.9: 2단계 플로우 — 접수 → 완료. legacy 값은 값만 허용(UI 는 숨김).
const STATUS_ORDER = ["received", "completed"];

function defaultInformForm() {
  return {
    wafer_id: "", lot_id: "", product: "", module: "", reason: "PEMS", text: "",
    deadline: "",
    attach_split: false, split: { column: "", old_value: "", new_value: "" },
    attach_embed: false, embed: emptyEmbedTable(),
  };
}

function informTitle(node) {
  const text = String(node?.text || "").trim();
  const first = text.split(/\n+/).find(Boolean);
  if (first) return first;
  const reason = String(node?.reason || "").trim();
  const module = String(node?.module || "").trim();
  return [module, reason, "인폼"].filter(Boolean).join(" · ") || "(내용 없음)";
}

function relativeTime(iso) {
  if (!iso) return "-";
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return String(iso).replace("T", " ").slice(0, 16);
  const diff = Date.now() - t.getTime();
  const abs = Math.abs(diff);
  const suffix = diff >= 0 ? "전" : "후";
  const mins = Math.round(abs / 60000);
  if (mins < 1) return "방금 전";
  if (mins < 60) return `${mins}분 ${suffix}`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}시간 ${suffix}`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days}일 ${suffix}`;
  return String(iso).replace("T", " ").slice(0, 10);
}

function _entryLastUpdateForUi(entry) {
  const vals = [entry?.created_at, entry?.checked_at, entry?.updated_at];
  (entry?.status_history || []).forEach(h => vals.push(h?.at));
  (entry?.edit_history || []).forEach(h => vals.push(h?.at));
  (entry?.mail_history || []).forEach(h => vals.push(h?.at || h?.sent_at));
  return vals.filter(Boolean).sort().slice(-1)[0] || entry?.created_at || "";
}

function inputStyle(extra = {}) {
  return {
    width: "100%",
    minWidth: 0,
    boxSizing: "border-box",
    padding: "7px 9px",
    borderRadius: 8,
    border: "1px solid var(--border)",
    background: "var(--bg-primary)",
    color: "var(--text-primary)",
    fontSize: 14,
    outline: "none",
    ...extra,
  };
}

function uniqueClean(values) {
  const seen = new Set();
  const out = [];
  (values || []).forEach(v => {
    const s = String(v || "").trim();
    if (!s || seen.has(s)) return;
    seen.add(s);
    out.push(s);
  });
  return out;
}

function parseCsvParam(params, key) {
  return uniqueClean([
    ...params.getAll(key),
    ...params.getAll(`${key}[]`),
  ].flatMap(v => String(v || "").split(",")));
}

function parseInformFiltersFromUrl() {
  if (typeof window === "undefined") return { tab: "matrix", filters: DEFAULT_SHARED_FILTERS };
  const params = new URLSearchParams(window.location.search);
  const tab = INFORM_TABS.some(([key]) => key === params.get("inform_tab")) ? params.get("inform_tab") : "matrix";
  return {
    tab,
    filters: {
      products: parseCsvParam(params, "products"),
      modules: parseCsvParam(params, "modules"),
      statuses: parseCsvParam(params, "statuses"),
      lot: params.get("lot") || "",
      period: ["7", "30", "90", "custom"].includes(params.get("period")) ? params.get("period") : "30",
      start: params.get("start") || "",
      end: params.get("end") || "",
      types: parseCsvParam(params, "types"),
    },
  };
}

function syncInformQuery(tab, filters) {
  if (typeof window === "undefined") return;
  const params = new URLSearchParams(window.location.search);
  params.set("inform_tab", tab || "matrix");
  const setArray = (key, arr) => {
    params.delete(key);
    params.delete(`${key}[]`);
    uniqueClean(arr).forEach(v => params.append(key, v));
  };
  setArray("products", filters.products);
  setArray("modules", filters.modules);
  setArray("statuses", filters.statuses);
  setArray("types", filters.types);
  if (filters.lot) params.set("lot", filters.lot); else params.delete("lot");
  if (filters.period && filters.period !== "30") params.set("period", filters.period); else params.delete("period");
  if (filters.start) params.set("start", filters.start); else params.delete("start");
  if (filters.end) params.set("end", filters.end); else params.delete("end");
  const next = `${window.location.pathname}?${params.toString()}`;
  window.history.replaceState(null, "", params.toString() ? next : window.location.pathname);
}

function filterDays(filters) {
  const raw = String(filters?.period || "30");
  if (raw === "custom") return 3650;
  const n = Number(raw);
  return Number.isFinite(n) && n > 0 ? n : 30;
}

function dateInSharedPeriod(iso, filters) {
  const day = String(iso || "").slice(0, 10);
  if (filters.period === "custom") {
    if (filters.start && day && day < filters.start) return false;
    if (filters.end && day && day > filters.end) return false;
    return true;
  }
  const dt = new Date(iso || "");
  if (Number.isNaN(dt.getTime())) return true;
  return dt.getTime() >= Date.now() - filterDays(filters) * 86400000;
}

/* v8.7.1 — 모듈별 구분색 (좌측 리스트 / 루트카드 left border / Gantt bar fallback) */
const MODULE_COLORS = {
  GATE: BAD.fg,
  STI: WARN.fg,
  PC: chartPalette.series[1],
  MOL: GREEN.fg,
  BEOL: INFO.fg,
  ET: PURPLE.fg,
  EDS: chartPalette.series[2],
  "S-D Epi": TEAL.fg,
  Spacer: SKY.fg,
  Well: chartPalette.series[10],
  MASK: NEUTRAL.fg,
  FAB: "rgba(51,65,85,0.95)",
  KNOB: chartPalette.series[13],
  "기타": "rgba(107,114,128,0.95)",
};
const FALLBACK_PALETTE = MODULE_SERIES;

// v8.8.29: Lot 선택 콤보박스 — 텍스트 타이핑으로 root_lot_id / fab_lot_id 부분일치 필터.
//   기존 <select> 드롭다운이 180건 중에서 스크롤로 찾아야 해서 비효율 → 타이핑 검색 지원.
//   - value: 현재 선택된/입력된 Lot ID.
//   - options: [{value, type:"root"|"fab"}] (중복 제거된 목록).
//   - productSelected: 제품 미선택 상태면 placeholder 만 보이고 비활성.
//   - manualMode: 단순 text input 모드 (옵션 필터 없음) — 호환을 위해 유지.
function LotCombobox({ value, onChange, options, productSelected, manualMode, onToggleManual }) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    const onDoc = (e) => {
      if (!wrapRef.current) return;
      if (!wrapRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const q = String(value || "").trim().toLowerCase();
  const filtered = (options || []).filter(o => !q || String(o.value || "").toLowerCase().includes(q));
  const showDropdown = open && productSelected && !manualMode && filtered.length > 0;

  const placeholder = !productSelected
    ? "-- 제품 먼저 선택 --"
    : manualMode
    ? "Lot 직접 입력 (root_lot_id 또는 fab_lot_id)"
    : `Lot 검색 (${(options || []).length}건 · 타이핑하면 필터)`;

  const iS = { flex: 1, padding: "8px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14, fontFamily: "monospace", outline: "none" };

  return (
    <div ref={wrapRef} style={{ display: "flex", gap: 6, alignItems: "center", position: "relative", flex: 1 }}>
      <input
        ref={inputRef}
        value={value || ""}
        onChange={e => { onChange(e.target.value); if (!manualMode) setOpen(true); }}
        onFocus={() => { if (!manualMode) setOpen(true); }}
        onKeyDown={e => {
          if (e.key === "Escape") { setOpen(false); inputRef.current?.blur(); }
          else if (e.key === "ArrowDown" && !manualMode && filtered.length) { e.preventDefault(); setOpen(true); }
        }}
        disabled={!productSelected && !manualMode}
        placeholder={placeholder}
        style={iS}
      />
      <button type="button"
        onClick={onToggleManual}
        title={manualMode ? "검색 드롭다운으로 전환" : "직접 입력 (필터 off)"}
        style={{ padding: "6px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-card)", color: "var(--text-secondary)", fontSize: 14, cursor: "pointer", whiteSpace: "nowrap" }}>
        {manualMode ? "🔎 검색" : "✏ 직접"}
      </button>
      {showDropdown && (
        <div style={{
          position: "absolute", top: "calc(100% + 2px)", left: 0, right: 60, zIndex: 100,
          maxHeight: 260, overflow: "auto",
          border: "1px solid var(--border)", borderRadius: 5,
          background: "var(--bg-primary)",
          boxShadow: "0 6px 20px rgba(0,0,0,0.25)",
        }}>
          {filtered.slice(0, 300).map(o => (
            <div key={o.type + ":" + o.value}
              onMouseDown={e => { e.preventDefault(); onChange(o.value); setOpen(false); }}
              style={{
                padding: "5px 10px", fontSize: 14, fontFamily: "monospace",
                cursor: "pointer", borderBottom: "1px solid var(--border)",
                display: "flex", gap: 8, alignItems: "center",
              }}
              onMouseEnter={e => e.currentTarget.style.background = "var(--accent-glow)"}
              onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
              <span style={{ fontSize: 14, padding: "1px 5px", borderRadius: 8, background: o.type === "fab" ? INFO.bg : GREEN.bg, color: o.type === "fab" ? INFO.fg : GREEN.fg, fontFamily: "inherit", flexShrink: 0 }}>
                {o.type}
              </span>
              <span style={{ flex: 1 }}>{o.value}</span>
            </div>
          ))}
          {filtered.length > 300 && (
            <div style={{ padding: "6px 10px", fontSize: 14, color: "var(--text-secondary)", fontStyle: "italic", textAlign: "center", borderTop: "1px solid var(--border)" }}>
              … {filtered.length - 300}개 더 있음. 더 구체적으로 타이핑하세요.
            </div>
          )}
        </div>
      )}
    </div>
  );
}


function moduleColor(name) {
  if (!name) return MODULE_COLORS["기타"];
  if (MODULE_COLORS[name]) return MODULE_COLORS[name];
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) | 0;
  return FALLBACK_PALETTE[Math.abs(h) % FALLBACK_PALETTE.length];
}

function StatusBadge({ status }) {
  const m = STATUS_META[status] || { label: status || "-", color: "var(--text-secondary)", dot: "·" };
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 4,
      padding: "2px 8px", borderRadius: 6,
      background: m.color + "16", color: m.color,
      border: "1px solid " + m.color + "33",
      fontSize: 14, fontWeight: 700,
    }}>
      <span>{m.dot}</span>{m.label}
    </span>
  );
}

function CheckPill({ node }) {
  // v8.8.13: 완료 = 초록 · 미확인 = 빨강. 양쪽 다 표시 (이전에는 false 일 때 숨김).
  const checked = !!node.checked;
  const title = checked
    ? `확인 완료 · by ${node.checked_by||"?"} · ${(node.checked_at||"").replace("T"," ").slice(0,16)}`
    : "확인중 (미확인)";
  return (
    <span title={title}
      style={{
        fontSize: 14, padding: "2px 8px", borderRadius: 6,
        background: checked ? OK.bg : WARN.bg,
        color: checked ? GREEN.fg : WARN.fg,
        border: "1px solid " + (checked ? GREEN.fg + "33" : WARN.fg + "33"),
        fontWeight: 700,
      }}>{checked ? "✓ 확인완료" : "○ 확인중"}</span>
  );
}

function AutoGenPill({ node }) {
  if (!node.auto_generated) return null;
  return (
    <span style={{
      fontSize: 14, padding: "2px 8px", borderRadius: 6,
      background: INFO.bg, color: INFO.fg, border: "1px solid " + INFO.fg + "33", fontWeight: 700,
    }}>⚙ 자동</span>
  );
}

function ImageGallery({ images }) {
  if (!images || images.length === 0) return null;
  return (
    <div style={{ marginTop: 8, display: "flex", flexWrap: "wrap", gap: 6 }}>
      {images.map((im, i) => (
        <a key={i} href={authSrc(im.url)} target="_blank" rel="noreferrer"
          style={{ display: "block", border: "1px solid var(--border)", borderRadius: 4, padding: 2, background: "var(--bg-primary)" }}>
          <img src={authSrc(im.url)} alt={im.filename}
            style={{ display: "block", maxHeight: 120, maxWidth: 180, objectFit: "contain" }} />
          <div style={{ fontSize: 14, color: "var(--text-secondary)", padding: "2px 4px", textAlign: "center", fontFamily: "monospace" }}>{im.filename}</div>
        </a>
      ))}
    </div>
  );
}

// v8.8.11: SplitTable 셀 팔레트 (SplitTable 과 동일 — 공유 util 후속 추출 예정).
const ST_CELL_COLORS = [
  { bg: "rgba(198,239,206,0.95)", fg: "rgba(0,97,0,0.95)" },
  { bg: "rgba(255,235,156,0.95)", fg: "rgba(156,87,0,0.95)" },
  { bg: "rgba(251,229,214,0.95)", fg: "rgba(191,78,0,0.95)" },
  { bg: "rgba(189,215,238,0.95)", fg: "rgba(31,78,121,0.95)" },
  { bg: "rgba(226,191,238,0.95)", fg: "rgba(112,48,160,0.95)" },
  { bg: "rgba(180,222,212,0.95)", fg: "rgba(11,83,69,0.95)" },
  { bg: "rgba(244,204,204,0.95)", fg: "rgba(117,25,76,0.95)" },
];
const ST_COLOR_PREFIXES = ["KNOB", "MASK"];
function stCellBg(val, uniq, pname) {
  if (!val || val === "None" || val === "null") return {};
  const pn = (pname || "").toUpperCase();
  if (!ST_COLOR_PREFIXES.some(p => pn.startsWith(p + "_"))) return {};
  const s = String(val);
  const idx = uniq[pn]?.[s];
  if (idx != null) { const c = ST_CELL_COLORS[idx % ST_CELL_COLORS.length]; return { background: c.bg, color: c.fg }; }
  return {};
}
function stPlanStyle(cell) {
  if (!cell) return {};
  if (cell.plan && cell.actual) {
    if (String(cell.plan) === String(cell.actual)) return {};
    return { borderLeft: `3px solid ${BAD.fg}`, boxShadow: `inset 0 0 0 1px ${BAD.fg}66` };
  }
  if (cell.plan) return { borderLeft: `3px solid ${WARN.fg}`, fontStyle: "italic", fontWeight: 700 };
  return {};
}

function summarizePlanByWafer(st) {
  const headers = st?.headers || [];
  const rows = st?.rows || [];
  if (!headers.length || !rows.length) return [];
  const perWafer = headers.map((wafer, idx) => {
    const plans = [];
    rows.forEach((r) => {
      const cell = (r._cells && (r._cells[idx] || r._cells[String(idx)])) || {};
      if (cell?.plan == null || cell.plan === "") return;
      plans.push({
        parameter: String(r._param || ""),
        actual: cell.actual == null ? "" : String(cell.actual),
        plan: String(cell.plan),
      });
    });
    return { wafer, plans };
  }).filter(x => x.plans.length > 0);
  return perWafer;
}

function splitTableHeaderGroups(st) {
  const headers = st?.headers || [];
  const rawGroups = Array.isArray(st?.header_groups) ? st.header_groups : [];
  const normalized = rawGroups
    .map(g => ({ label: String(g?.label || "").trim(), span: Math.max(1, Number(g?.span || 0)) }))
    .filter(g => g.label && g.span > 0);
  const rawSpan = normalized.reduce((acc, g) => acc + g.span, 0);
  if (normalized.length && rawSpan === headers.length) return normalized;

  const fabs = Array.isArray(st?.wafer_fab_list) ? st.wafer_fab_list.map(v => String(v || "").trim()) : [];
  if (fabs.length !== headers.length || !fabs.some(Boolean)) return [];
  const groups = [];
  fabs.forEach(label => {
    const text = label || "—";
    const last = groups[groups.length - 1];
    if (last && last.label === text) last.span += 1;
    else groups.push({ label: text, span: 1 });
  });
  return groups;
}

function EmbedTableView({ embed, product }) {
  if (!embed) return null;
  const stepWarning = (stepIds) => {
    const ids = (Array.isArray(stepIds) ? stepIds : []).map((x) => String(x || "").trim()).filter(Boolean);
    if (ids.length <= 1) return "";
    const hasManualLike = ids.some((sid) => /[A-Z]{2}\d{6}[A-Z]{2}$/.test(sid));
    return hasManualLike
      ? "복수 step_id 및 manual/예외 step 후보가 있어 적용 엔지니어 확인이 필요합니다."
      : "복수 step_id 이므로 적용 전 담당 엔지니어가 실제 사용 step_id를 확인해 주세요.";
  };
  const inferProduct = () => {
    const p = String(product || "").trim();
    if (p) return p;
    const src = String(embed?.source || "");
    const m = src.match(/SplitTable\/([^ @·]+)/);
    return m ? String(m[1] || "").trim() : "";
  };
  const effectiveProduct = inferProduct();
  const [knobMeta, setKnobMeta] = useState({});
  const [vmMeta, setVmMeta] = useState({});
  const [inlineMeta, setInlineMeta] = useState({});
  useEffect(() => {
    if (!effectiveProduct) {
      setKnobMeta({}); setVmMeta({}); setInlineMeta({});
      return;
    }
    fetch(`/api/splittable/knob-meta?product=${encodeURIComponent(effectiveProduct)}`).then(r => r.json()).then(d => setKnobMeta(d.features || {})).catch(() => setKnobMeta({}));
    fetch(`/api/splittable/vm-meta?product=${encodeURIComponent(effectiveProduct)}`).then(r => r.json()).then(d => setVmMeta(d.items || {})).catch(() => setVmMeta({}));
    fetch(`/api/splittable/inline-meta?product=${encodeURIComponent(effectiveProduct)}`).then(r => r.json()).then(d => setInlineMeta(d.items || {})).catch(() => setInlineMeta({}));
  }, [effectiveProduct]);
  const vmLookup = (param) => { if (!param) return null; const tail = String(param).replace(/^VM_/, ""); return vmMeta[param] || vmMeta[tail] || null; };
  const inlineLookup = (param) => { if (!param) return null; const tail = String(param).replace(/^INLINE_/, ""); return inlineMeta[param] || inlineMeta[tail] || null; };
  const lineageSummary = (() => {
    const st = embed?.st_view;
    const rows = st?.rows || [];
    const out = [];
    rows.forEach((r) => {
      const param = String(r._param || "");
      if (!param) return;
      if (knobMeta[param]?.groups?.length) {
        knobMeta[param].groups.forEach((g, gi) => out.push({
          key: `${param}-k-${gi}`,
          parameter: param,
          function_step: g.func_step || "",
          step_ids: Array.isArray(g.step_ids) ? g.step_ids : [],
          module: Array.isArray(g.modules) ? g.modules.join(", ") : "",
        }));
        return;
      }
      const vm = vmLookup(param) || {};
      if (String(param).startsWith("VM_") && (vm.step_id || vm.function_step || (vm.groups || []).length)) {
        if (Array.isArray(vm.groups) && vm.groups.length) {
          vm.groups.forEach((g, gi) => out.push({
            key: `${param}-v-${gi}`,
            parameter: param,
            function_step: g.function_step || vm.function_step || "",
            step_ids: g.step_id ? [g.step_id] : (vm.step_id ? [vm.step_id] : []),
            module: "",
          }));
        } else {
          out.push({ key: `${param}-v`, parameter: param, function_step: vm.function_step || "", step_ids: vm.step_id ? [vm.step_id] : [], module: "" });
        }
        return;
      }
      const im = inlineLookup(param) || {};
      if (String(param).startsWith("INLINE_") && (im.step_id || im.function_step || (im.groups || []).length)) {
        if (Array.isArray(im.groups) && im.groups.length) {
          im.groups.forEach((g, gi) => out.push({
            key: `${param}-i-${gi}`,
            parameter: param,
            function_step: g.function_step || im.function_step || "",
            step_ids: g.step_id ? [g.step_id] : (Array.isArray(im.step_ids) ? im.step_ids : (im.step_id ? [im.step_id] : [])),
            module: "",
          }));
        } else {
          out.push({ key: `${param}-i`, parameter: param, function_step: im.function_step || "", step_ids: Array.isArray(im.step_ids) ? im.step_ids : (im.step_id ? [im.step_id] : []), module: "" });
        }
      }
    });
    return out;
  })();
  const shellStyle = { marginTop: 8, padding: 10, border: "1px solid var(--border)", borderRadius: 6, background: "var(--bg-primary)" };
  const scrollerStyle = { maxHeight: 620, overflow: "auto", border: "1px solid var(--border)", borderRadius: 4, background: "var(--bg-card)" };
  const tableStyle = { borderCollapse: "collapse", fontSize: 14, fontFamily: "monospace", width: "max-content", minWidth: "100%" };
  const leftHeadStyle = { border: "1px solid var(--border)", padding: "6px 10px", background: "var(--bg-secondary)", textAlign: "left", position: "sticky", top: 0, left: 0, zIndex: 3, minWidth: 220, lineHeight: 1.35 };
  const headStyle = { border: "1px solid var(--border)", padding: "6px 10px", background: "var(--bg-secondary)", textAlign: "center", position: "sticky", top: 0, minWidth: 84, lineHeight: 1.35, zIndex: 2 };
  const subHeadLeftStyle = { border: "1px solid var(--border)", padding: "5px 10px", background: "var(--bg-tertiary)", fontSize: 14, color: "var(--text-secondary)", textAlign: "left", position: "sticky", top: 34, zIndex: 2, minWidth: 220 };
  const subHeadStyle = { border: "1px solid var(--border)", padding: "5px 10px", background: "var(--bg-tertiary)", fontSize: 14, color: "var(--text-secondary)", textAlign: "center", position: "sticky", top: 34, zIndex: 1, minWidth: 84 };
  const leftCellStyle = { border: "1px solid var(--border)", padding: "5px 10px", background: "var(--bg-secondary)", fontWeight: 700, position: "sticky", left: 0, zIndex: 1, lineHeight: 1.35 };
  const cellStyle = { border: "1px solid var(--border)", padding: "5px 10px", textAlign: "center", lineHeight: 1.35, whiteSpace: "nowrap" };
  // v8.8.11: st_view(SplitTable /view 응답) 가 있으면 컬러링 + plan pin 동일 렌더.
  const st = embed.st_view;
  if (st && st.headers && st.rows) {
    const planSummary = summarizePlanByWafer(st);
    const headers = st.headers || [];
    const headerGroups = splitTableHeaderGroups(st);
    const rootLotId = String(st.root_lot_id || "").trim();
    const lotIdValues = [...new Set(headerGroups.map(g => String(g?.label || "").trim()).filter(Boolean))];
    const lotIdLabel = lotIdValues.join(", ");
    const hasLotContext = !!(rootLotId || lotIdLabel);
    const rootHeaderHeight = hasLotContext ? 44 : 0;
    const groupHeaderHeight = headerGroups.length ? 30 : 0;
    const waferTop = rootHeaderHeight + groupHeaderHeight;
    const lotContextTitle = `root_lot_id: ${rootLotId || "-"}\nlot_id: ${lotIdLabel || "-"}`;
    const rootLeftStyle = { border: "1px solid var(--border)", padding: "4px 10px", background: "var(--bg-secondary)", position: "sticky", top: 0, left: 0, zIndex: 5, minWidth: 220, height: rootHeaderHeight, fontSize: 14, lineHeight: 1.25, whiteSpace: "normal", wordBreak: "break-word" };
    const rootHeadStyle = { border: "1px solid var(--border)", padding: "6px 10px", background: "var(--bg-secondary)", color: "var(--accent)", textAlign: "center", position: "sticky", top: 0, zIndex: 4, fontWeight: 800, fontSize: 14, fontFamily: "monospace", height: rootHeaderHeight };
    const groupLeftStyle = { border: "1px solid var(--border)", padding: 0, background: "var(--bg-tertiary)", position: "sticky", top: rootHeaderHeight, left: 0, zIndex: 5, minWidth: 220 };
    const groupHeadStyle = { border: "1px solid var(--border)", padding: "5px 10px", background: "var(--bg-tertiary)", color: "rgba(251,191,36,0.95)", textAlign: "center", position: "sticky", top: rootHeaderHeight, zIndex: 4, fontWeight: 800, fontSize: 14, fontFamily: "monospace", whiteSpace: "nowrap" };
    const waferLeftStyle = { ...leftHeadStyle, top: waferTop };
    const waferHeadStyle = { ...headStyle, top: waferTop };
    // uniqueMap 계산: param 별 값 → 인덱스.
    const uniq = {};
    for (const r of st.rows) {
      const pn = String(r._param || "").toUpperCase();
      if (!ST_COLOR_PREFIXES.some(p => pn.startsWith(p + "_"))) continue;
      const seen = {};
      Object.values(r._cells || {}).forEach(c => {
        [c?.actual, c?.plan].forEach(v => {
          if (v == null || v === "") return;
          const s = String(v);
          if (!(s in seen)) seen[s] = Object.keys(seen).length;
        });
      });
      uniq[pn] = seen;
    }
    return (
      <div style={shellStyle}>
        <div style={{ fontSize: 14, fontWeight: 700, color: "var(--accent)", marginBottom: 4 }}>
          🔗 SplitTable {embed.source && <span style={{ color: "var(--text-secondary)", fontWeight: 500 }}>· {embed.source}</span>}
        </div>
        {embed.note && <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 4 }}>{embed.note}</div>}
        <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 6 }}>최대 15줄 이상을 한 화면에서 검토할 수 있도록 확장 표시됩니다.</div>
        <div style={scrollerStyle}>
          <table style={tableStyle}>
            <thead>
              {hasLotContext && (
                <tr>
                  <th style={rootLeftStyle} title={lotContextTitle}>
                    <div><span style={{ color: "var(--text-secondary)", fontWeight: 700 }}>root_lot_id</span> {rootLotId || "-"}</div>
                    <div><span style={{ color: "var(--text-secondary)", fontWeight: 700 }}>lot_id</span> {lotIdLabel || "-"}</div>
                  </th>
                  <th colSpan={headers.length || 1} style={rootHeadStyle}>{rootLotId || lotIdLabel}</th>
                </tr>
              )}
              {headerGroups.length > 0 && (
                <tr>
                  <th style={groupLeftStyle}></th>
                  {headerGroups.map((g, i) => (
                    <th key={i} colSpan={g.span} style={groupHeadStyle} title={g.label}>{g.label}</th>
                  ))}
                </tr>
              )}
              <tr>
                <th style={waferLeftStyle}>parameter</th>
                {headers.map((h, i) => (
                  <th key={i} style={waferHeadStyle}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {st.rows.map((r, ri) => (
                <tr key={ri}>
                  <td style={leftCellStyle}>{r._param}</td>
                  {headers.map((_, ci) => {
                    const cell = (r._cells && r._cells[ci]) || {};
                    const bg = stCellBg(cell.plan ?? cell.actual, uniq, r._param);
                    const plan = stPlanStyle(cell);
                    const isPlan = !!cell.plan;
                    const display = (cell.actual != null && cell.actual !== "") ? String(cell.actual)
                      : (cell.plan != null ? String(cell.plan) : "");
                    return (
                      <td key={ci} style={{ ...cellStyle, ...bg, ...plan }}>
                        {isPlan && <span title="plan" style={{ marginRight: 3 }}>📌</span>}
                        {display}
                        {isPlan && cell.plan !== cell.actual && cell.actual != null && cell.actual !== "" && (
                          <span style={{ marginLeft: 3, color: WARN.fg, fontWeight: 700 }}>→{cell.plan}</span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {planSummary.length > 0 && (
          <div style={{ marginTop: 10 }}>
            <div style={{ fontSize: 14, fontWeight: 700, color: "var(--accent)", marginBottom: 6 }}>📋 Wafer별 적용 plan 요약</div>
            <div style={{ maxHeight: 320, overflow: "auto", border: "1px solid var(--border)", borderRadius: 4, background: "var(--bg-card)" }}>
              <table style={{ ...tableStyle, width: "100%" }}>
                <thead>
                  <tr>
                    <th style={{ ...headStyle, textAlign: "left", minWidth: 84 }}>wafer</th>
                    <th style={{ ...headStyle, textAlign: "left", minWidth: 220 }}>parameter</th>
                    <th style={{ ...headStyle, textAlign: "left", minWidth: 140 }}>actual</th>
                    <th style={{ ...headStyle, textAlign: "left", minWidth: 140 }}>plan</th>
                  </tr>
                </thead>
                <tbody>
                  {planSummary.flatMap(({ wafer, plans }) => plans.map((p, i) => (
                    <tr key={`${wafer}-${p.parameter}-${i}`}>
                      <td style={{ ...cellStyle, textAlign: "left", fontWeight: 700 }}>{wafer}</td>
                      <td style={{ ...cellStyle, textAlign: "left" }}>{p.parameter}</td>
                      <td style={{ ...cellStyle, textAlign: "left", color: "var(--text-secondary)" }}>{p.actual || "—"}</td>
                      <td style={{ ...cellStyle, textAlign: "left", color: "rgba(234,88,12,0.95)", fontWeight: 700 }}>{p.plan}</td>
                    </tr>
                  )))}
                </tbody>
              </table>
            </div>
          </div>
        )}
        {lineageSummary.length > 0 && (
          <div style={{ marginTop: 10 }}>
            <div style={{ fontSize: 14, fontWeight: 700, color: "var(--accent)", marginBottom: 6 }}>🧭 Parameter별 적용 step 요약</div>
            <div style={{ maxHeight: 320, overflow: "auto", border: "1px solid var(--border)", borderRadius: 4, background: "var(--bg-card)" }}>
              <table style={{ ...tableStyle, width: "100%" }}>
                <thead>
                  <tr>
                    <th style={{ ...headStyle, textAlign: "left", minWidth: 220 }}>parameter</th>
                    <th style={{ ...headStyle, textAlign: "left", minWidth: 180 }}>function_step</th>
                    <th style={{ ...headStyle, textAlign: "left", minWidth: 240 }}>step_id</th>
                  </tr>
                </thead>
                <tbody>
                  {lineageSummary.map((x) => (
                    <tr key={x.key}>
                      <td style={{ ...cellStyle, textAlign: "left" }}>{x.parameter}</td>
                      <td style={{ ...cellStyle, textAlign: "left", color: "var(--text-secondary)" }}>{x.function_step || "—"}</td>
                      <td style={{ ...cellStyle, textAlign: "left", color: INFO.fg, fontWeight: 700 }}>
                        {(x.step_ids || []).length ? x.step_ids.join(", ") : "—"}
                        {stepWarning(x.step_ids) && (
                          <div style={{ marginTop: 4, fontSize: 14, lineHeight: 1.35, color: "rgba(220,38,38,0.95)", fontFamily: "system-ui, sans-serif", fontWeight: 600 }}>
                            {stepWarning(x.step_ids)}
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div style={{ marginTop: 6, fontSize: 14, color: "var(--text-secondary)" }}>
              function_step 에 여러 step_id 가 연결되면 현재 제품에서 실제 적용할 step_id 를 담당 엔지니어가 확인한 뒤 진행해야 합니다.
            </div>
          </div>
        )}
      </div>
    );
  }
  // legacy 2D rows 모드 — columns=[parameter, #1, #2, ...] + rows=[[param, v1, ...], ...].
  // v8.8.13: legacy 도 SplitTable 팔레트로 컬러링. columns[0] 이 parameter/param 류면 st_view 로 변환 후 같은 렌더.
  if ((!embed.columns?.length && !embed.rows?.length)) return null;
  const cols = embed.columns || [];
  const rows = embed.rows || [];
  const looksLikeParamTable =
    cols.length >= 2 &&
    /^(parameter|param)$/i.test(String(cols[0] || "").trim());
  if (looksLikeParamTable) {
    // legacy → st_view 구조.
    const headers = cols.slice(1);
    const stRows = rows.map(r => {
      const _cells = {};
      for (let i = 0; i < headers.length; i++) {
        const v = r[i + 1];
        _cells[i] = { actual: (v == null ? "" : String(v)) };
      }
      return { _param: String(r[0] ?? ""), _cells };
    });
    const uniq = {};
    for (const r of stRows) {
      const pn = String(r._param || "").toUpperCase();
      if (!ST_COLOR_PREFIXES.some(p => pn.startsWith(p + "_"))) continue;
      const seen = {};
      Object.values(r._cells || {}).forEach(c => {
        const v = c?.actual;
        if (v == null || v === "") return;
        const s = String(v);
        if (!(s in seen)) seen[s] = Object.keys(seen).length;
      });
      uniq[pn] = seen;
    }
    return (
      <div style={shellStyle}>
        <div style={{ fontSize: 14, fontWeight: 700, color: "var(--accent)", marginBottom: 4 }}>
          🔗 SplitTable {embed.source && <span style={{ color: "var(--text-secondary)", fontWeight: 500 }}>· {embed.source}</span>}
        </div>
        {embed.note && <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 4 }}>{embed.note}</div>}
        <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 6 }}>최대 15줄 이상을 한 화면에서 검토할 수 있도록 확장 표시됩니다.</div>
        <div style={scrollerStyle}>
          <table style={tableStyle}>
            <thead>
              <tr>
                <th style={leftHeadStyle}>parameter</th>
                {headers.map((h, i) => (
                  <th key={i} style={headStyle}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {stRows.map((r, ri) => (
                <tr key={ri}>
                  <td style={leftCellStyle}>{r._param}</td>
                  {headers.map((_, ci) => {
                    const cell = (r._cells && r._cells[ci]) || {};
                    const bg = stCellBg(cell.actual, uniq, r._param);
                    const display = (cell.actual != null && cell.actual !== "") ? String(cell.actual) : "";
                    return (
                      <td key={ci} style={{ ...cellStyle, ...bg }}>{display}</td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }
  // legacy (non-param-table) 그대로.
  return (
    <div style={shellStyle}>
      <div style={{ fontSize: 14, fontWeight: 700, color: "var(--accent)", marginBottom: 4 }}>
        🔗 Embed {embed.source && <span style={{ color: "var(--text-secondary)", fontWeight: 500 }}>· {embed.source}</span>}
      </div>
      {embed.note && <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 4 }}>{embed.note}</div>}
      <div style={scrollerStyle}>
        <table style={tableStyle}>
          <thead>
            <tr>{cols.map((c, i) => (
              <th key={i} style={{ ...headStyle, textAlign: "left" }}>{c}</th>
            ))}</tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>{r.map((v, j) => (
                <td key={j} style={{ ...cellStyle, textAlign: "left", whiteSpace: "normal" }}>{v}</td>
              ))}</tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* 재귀 스레드 노드 */
function ThreadNode({
  node, childrenByParent, onReply, onDelete, onToggleCheck, onEdit, user,
  depth = 0, constants,
}) {
  const [replyOpen, setReplyOpen] = useState(false);
  // v8.8.13: 답글의 module/reason 은 부모에서 자동 상속. UI 에선 읽기전용으로 표시.
  const [reply, setReply] = useState({ module: node.module || "", reason: node.reason || "", text: "" });
  const [attachSplit, setAttachSplit] = useState(false);
  const [splitForm, setSplitForm] = useState({ column: "", old_value: "", new_value: "" });
  const [replyImages, setReplyImages] = useState([]);
  const [uploading, setUploading] = useState(false);
  // 작성자 또는 admin 은 text + module + reason 수정 가능 (embed 스냅샷은 원본 유지).
  const [editOpen, setEditOpen] = useState(false);
  const [editText, setEditText] = useState(node.text || "");
  const [editModule, setEditModule] = useState(node.module || "");
  const [editReason, setEditReason] = useState(node.reason || "");
  const canEdit = !!onEdit && (user?.role === "admin" || user?.username === node.author);

  const handleFile = async (fl) => {
    if (!fl || fl.length === 0) return;
    setUploading(true);
    const uploaded = [];
    for (const f of Array.from(fl)) {
      try {
        const fd = new FormData();
        fd.append("file", f);
        const res = await sf("/api/informs/upload", { method: "POST", body: fd });
        uploaded.push({ filename: res.filename, url: res.url, size: res.size });
      } catch (e) {
        alert("업로드 실패: " + e.message);
      }
    }
    setReplyImages((prev) => [...prev, ...uploaded]);
    setUploading(false);
  };
  const canDelete = user && (user.role === "admin" || user.username === node.author);
  const kids = childrenByParent[node.id] || [];
  const indent = Math.min(depth, 5) * 28;

  const sc = node.splittable_change;

  return (
    <div style={{ marginLeft: indent }}>
      <div style={{
        background: depth === 0 ? "var(--bg-secondary)" : "var(--bg-card)",
        border: "1px solid var(--border)", borderRadius: 8, padding: 10, marginBottom: 6,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4, flexWrap: "wrap" }}>
          {node.module && (() => { const mc = moduleColor(node.module); return (
            <span style={{ fontSize: 14, padding: "2px 8px", borderRadius: 999, background: mc + "22", color: mc, fontWeight: 700, border: "1px solid " + mc + "55" }}>{node.module}</span>
          ); })()}
          {node.reason && <span style={{ fontSize: 14, padding: "2px 8px", borderRadius: 999, background: "var(--bg-hover)", color: "var(--text-secondary)" }}>[{node.reason}]</span>}
          <CheckPill node={node} />
          <AutoGenPill node={node} />
          <span style={{ fontSize: 14, fontWeight: 600 }}>{node.author}</span>
          <span title={node.created_at || ""} style={{
            fontSize: 14, padding: "2px 8px", borderRadius: 999,
            background: "var(--bg-primary)", color: "var(--text-primary)",
            border: "1px solid var(--border)", fontFamily: "monospace",
            display: "inline-flex", alignItems: "center", gap: 4,
          }}>🕐 {(node.created_at || "").replace("T", " ").slice(0, 16)}</span>
          <div style={{ flex: 1 }} />
          {/* v8.8.13: 우측 액션 3버튼 통일 — 확인 · 답글 · 삭제. 상태 라벨은 CheckPill 로 좌측에 표시. */}
          <button onClick={() => onToggleCheck(node)} title={node.checked ? "미확인으로 되돌리기" : "확인 완료 처리"}
            style={{ fontSize: 14, padding: "2px 8px", borderRadius: 4, cursor: "pointer",
              border: "1px solid " + (node.checked ? BAD.fg : OK.fg),
              background: node.checked ? "transparent" : OK.fg,
              color: node.checked ? BAD.fg : WHITE, fontWeight: 700 }}>
            {node.checked ? "↺ 미확인" : "✓ 확인"}
          </button>
          <button onClick={() => setReplyOpen(!replyOpen)} title="답글 달기 (module/reason 은 부모 자동 상속)"
            style={{ fontSize: 14, padding: "2px 8px", borderRadius: 4, cursor: "pointer",
              border: "1px solid var(--accent)", background: "transparent", color: "var(--accent)", fontWeight: 700 }}>
            {replyOpen ? "닫기" : "답글"}
          </button>
          {/* 수정 — 작성자/admin. text/module/reason 만 바뀌고 embed 는 원본 유지. */}
          {canEdit && (
            <button onClick={() => { setEditText(node.text || ""); setEditModule(node.module || ""); setEditReason(node.reason || ""); setEditOpen(!editOpen); }}
              title="본문 수정"
              style={{ fontSize: 14, padding: "2px 8px", borderRadius: 4, cursor: "pointer",
                border: `1px solid ${INFO.fg}`, background: "transparent", color: INFO.fg, fontWeight: 700 }}>
              {editOpen ? "닫기" : "✎ 수정"}
            </button>
          )}
          {canDelete && kids.length === 0 && (
            <button onClick={() => onDelete(node.id)} title="이 글 삭제 (자식이 없을 때만)"
              style={{ fontSize: 14, padding: "2px 8px", borderRadius: 4, cursor: "pointer",
                border: `1px solid ${BAD.fg}`, background: "transparent", color: BAD.fg, fontWeight: 700 }}>
              🗑 삭제
            </button>
          )}
        </div>

        {editOpen ? (
          <div style={{ marginTop: 4 }}>
            {/* v8.8.13: module/사유 도 수정 허용 — 처음 등록 시 실수로 안 넣었어도 나중에 교정 가능. */}
            <div style={{ display: "flex", gap: 6, marginBottom: 6, flexWrap: "wrap" }}>
              <select value={editModule} onChange={e => setEditModule(e.target.value)}
                style={{ padding: "4px 6px", borderRadius: 4, border: `1px solid ${INFO.fg}`, background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14 }}>
                <option value="">(모듈 없음)</option>
                {constants.modules.map(m => <option key={m} value={m}>{m}</option>)}
              </select>
              <select value={editReason} onChange={e => setEditReason(e.target.value)}
                style={{ padding: "4px 6px", borderRadius: 4, border: `1px solid ${INFO.fg}`, background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14 }}>
                <option value="">(사유 없음)</option>
                {constants.reasons.map(r => <option key={r} value={r}>{r}</option>)}
              </select>
            </div>
            <textarea value={editText} onChange={e => setEditText(e.target.value)} rows={4}
              style={{ width: "100%", padding: 8, borderRadius: 4, border: `1px solid ${INFO.fg}`, background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14, resize: "vertical", fontFamily: "inherit" }} />
            <div style={{ fontSize: 14, color: "var(--text-secondary)", marginTop: 4 }}>
              ※ 본문·모듈·사유 수정 가능. SplitTable 스냅샷은 작성 시점 값으로 유지됩니다.
            </div>
            <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
              <button onClick={() => {
                const patch = {};
                const t0 = (node.text || "").trim(), t1 = (editText || "").trim();
                if (t1 !== t0) patch.text = editText;
                if ((editModule || "") !== (node.module || "")) patch.module = editModule || "";
                if ((editReason || "") !== (node.reason || "")) patch.reason = editReason || "";
                if (Object.keys(patch).length === 0) { setEditOpen(false); return; }
                onEdit(node.id, patch).then(() => setEditOpen(false));
              }}
                style={{ padding: "5px 14px", borderRadius: 4, border: "none", background: INFO.fg, color: WHITE, fontSize: 14, fontWeight: 700, cursor: "pointer" }}>저장</button>
              <button onClick={() => { setEditOpen(false); setEditText(node.text || ""); setEditModule(node.module || ""); setEditReason(node.reason || ""); }}
                style={{ padding: "5px 10px", borderRadius: 4, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", fontSize: 14, cursor: "pointer" }}>취소</button>
            </div>
          </div>
        ) : (
          <div style={{ fontSize: 14, color: "var(--text-primary)", whiteSpace: "pre-wrap", lineHeight: 1.5 }}>{node.text}</div>
        )}
        <ImageGallery images={node.images} />
        <EmbedTableView embed={node.embed_table} product={node.product} />

        {sc && (sc.column || sc.new_value) && (
          <div style={{ marginTop: 8, padding: "6px 10px", borderLeft: `3px solid ${WARN.fg}`,
                        background: WARN.bg, borderRadius: 4, fontSize: 14 }}>
            <b>SplitTable 변경 요청</b>
            <div style={{ fontFamily: "monospace", marginTop: 2 }}>
              {sc.column ? <><span style={{ color: WARN.fg }}>{sc.column}</span>: </> : null}
              <span style={{ textDecoration: "line-through", opacity: 0.7 }}>{sc.old_value || "-"}</span>
              {" → "}
              <span style={{ color: OK.fg, fontWeight: 700 }}>{sc.new_value || "-"}</span>
              {sc.applied && <span style={{ marginLeft: 8, fontSize: 14, color: GREEN.fg, fontWeight: 700 }}>APPLIED</span>}
            </div>
          </div>
        )}

        {replyOpen && (
          <div style={{ marginTop: 8, paddingTop: 8, borderTop: "1px dashed var(--border)" }}>
            {/* v8.8.13: 답글의 module/reason 은 부모 자동 상속 → 읽기전용 pill. */}
            <div style={{ display: "flex", gap: 6, marginBottom: 6, alignItems: "center", flexWrap: "wrap", fontSize: 14, color: "var(--text-secondary)" }}>
              <span>상속:</span>
              {(() => { const mc = moduleColor(reply.module || "—"); return (
                <span style={{ fontSize: 14, padding: "2px 8px", borderRadius: 999, background: mc + "22", color: mc, fontWeight: 700, border: "1px solid " + mc + "55" }}>{reply.module || "(모듈 없음)"}</span>
              ); })()}
              <span style={{ fontSize: 14, padding: "2px 8px", borderRadius: 999, background: "var(--bg-hover)", color: "var(--text-secondary)" }}>[{reply.reason || "(사유 없음)"}]</span>
              <label style={{ fontSize: 14, color: "var(--text-secondary)", display: "inline-flex", alignItems: "center", gap: 4, cursor: "pointer", marginLeft: "auto" }}>
                <input type="checkbox" checked={attachSplit} onChange={e => setAttachSplit(e.target.checked)} />
                SplitTable 변경요청 포함
              </label>
            </div>
            <textarea value={reply.text} onChange={e => setReply({ ...reply, text: e.target.value })} rows={2}
              placeholder="내용 (재인폼 사유, 조치 제안 등)"
              style={{ width: "100%", padding: 6, borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14, resize: "vertical" }} />
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 6, flexWrap: "wrap" }}>
              <label style={{ fontSize: 14, color: "var(--text-secondary)", cursor: "pointer" }}>
                📎 이미지
                <input type="file" accept="image/*" multiple
                  style={{ display: "none" }}
                  onChange={e => { handleFile(e.target.files); e.target.value = ""; }} />
              </label>
              {uploading && <span style={{ fontSize: 14, color: "var(--accent)" }}>업로드중…</span>}
              {replyImages.map((im, i) => (
                <span key={i} style={{ fontSize: 14, padding: "2px 6px", borderRadius: 3, background: "var(--bg-primary)", border: "1px solid var(--border)", display: "inline-flex", alignItems: "center", gap: 4 }}>
                  <img src={authSrc(im.url)} alt="" style={{ width: 24, height: 24, objectFit: "cover", borderRadius: 2 }} />
                  <span style={{ fontFamily: "monospace" }}>{im.filename}</span>
                  <button onClick={() => setReplyImages(replyImages.filter((_, j) => j !== i))}
                    style={{ border: "none", background: "transparent", color: BAD.fg, cursor: "pointer", padding: 0 }}>×</button>
                </span>
              ))}
            </div>
            {attachSplit && (
              <div style={{ marginTop: 6, padding: 8, background: "var(--bg-primary)", borderRadius: 4, border: "1px dashed var(--border)" }}>
                <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 4, fontWeight: 600 }}>Split Table 변경 (예: KNOB A → B)</div>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  <input value={splitForm.column} onChange={e => setSplitForm({ ...splitForm, column: e.target.value })}
                    placeholder="column (예: KNOB/GATE_PPID)"
                    style={{ flex: "1 1 180px", padding: "4px 6px", borderRadius: 3, border: "1px solid var(--border)", background: "var(--bg-secondary)", color: "var(--text-primary)", fontSize: 14, fontFamily: "monospace" }} />
                  <input value={splitForm.old_value} onChange={e => setSplitForm({ ...splitForm, old_value: e.target.value })}
                    placeholder="old"
                    style={{ flex: "1 1 100px", padding: "4px 6px", borderRadius: 3, border: "1px solid var(--border)", background: "var(--bg-secondary)", color: "var(--text-primary)", fontSize: 14, fontFamily: "monospace" }} />
                  <input value={splitForm.new_value} onChange={e => setSplitForm({ ...splitForm, new_value: e.target.value })}
                    placeholder="new"
                    style={{ flex: "1 1 100px", padding: "4px 6px", borderRadius: 3, border: "1px solid var(--border)", background: "var(--bg-secondary)", color: "var(--text-primary)", fontSize: 14, fontFamily: "monospace" }} />
                </div>
              </div>
            )}
            <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
              <button onClick={() => {
                if (!reply.text.trim() && replyImages.length === 0) return;
                // v8.8.12: 답글 text 앞에 [RE] prefix 자동 (이미 있으면 중복 안 붙임).
                const replyText = (reply.text || "").trim();
                const txt = replyText.startsWith("[RE]") ? replyText : (replyText ? `[RE] ${replyText}` : replyText);
                const body = { ...reply, text: txt, images: replyImages };
                if (attachSplit && (splitForm.column || splitForm.new_value)) {
                  body.splittable_change = { ...splitForm, applied: false };
                }
                onReply(node.id, body).then(() => {
                  setReply({ module: node.module || "", reason: node.reason || "", text: "" });
                  setSplitForm({ column: "", old_value: "", new_value: "" });
                  setAttachSplit(false);
                  setReplyImages([]);
                  setReplyOpen(false);
                });
              }}
                style={{ padding: "5px 14px", borderRadius: 4, border: "none", background: "var(--accent)", color: WHITE, fontSize: 14, fontWeight: 600, cursor: "pointer" }}>등록</button>
              <button onClick={() => setReplyOpen(false)}
                style={{ padding: "5px 10px", borderRadius: 4, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", fontSize: 14, cursor: "pointer" }}>취소</button>
            </div>
          </div>
        )}
      </div>
      {kids.map(k => (
        <ThreadNode key={k.id} node={k} childrenByParent={childrenByParent}
          onReply={onReply} onDelete={onDelete} onToggleCheck={onToggleCheck}
          onEdit={onEdit}
          user={user} depth={depth + 1} constants={constants} />
      ))}
    </div>
  );
}

/* 데드라인 badge + 편집 */
function DeadlineBadge({ deadline, onChange, canEdit }) {
  const [editing, setEditing] = useState(false);
  const [val, setVal] = useState(deadline || "");
  useEffect(() => { setVal(deadline || ""); }, [deadline]);
  const today = new Date().toISOString().slice(0, 10);
  const overdue = deadline && deadline < today;
  const near = deadline && !overdue && (new Date(deadline) - new Date(today)) / 86400000 <= 3;
  const color = overdue ? BAD.fg : near ? WARN.fg : INFO.fg;
  if (editing && canEdit) {
    return (
      <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
        <input type="date" value={val} onChange={e => setVal(e.target.value)}
          style={{ fontSize: 14, padding: "2px 4px", borderRadius: 3, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)" }} />
        <button onClick={() => { onChange(val); setEditing(false); }}
          style={{ fontSize: 14, padding: "2px 8px", borderRadius: 3, border: "none", background: "var(--accent)", color: WHITE, cursor: "pointer" }}>저장</button>
        {deadline && <button onClick={() => { onChange(""); setEditing(false); }}
          style={{ fontSize: 14, padding: "2px 8px", borderRadius: 3, border: "1px solid var(--border)", background: "transparent", color: BAD.fg, cursor: "pointer" }}>해제</button>}
        <button onClick={() => setEditing(false)}
          style={{ fontSize: 14, padding: "2px 6px", borderRadius: 3, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", cursor: "pointer" }}>×</button>
      </span>
    );
  }
  if (!deadline) {
    if (!canEdit) return null;
    return <span onClick={() => setEditing(true)} style={{ fontSize: 14, color: "var(--text-secondary)", cursor: "pointer", padding: "2px 8px", borderRadius: 999, border: "1px dashed var(--border)" }}>🗓 데드라인 설정</span>;
  }
  return (
    <span onClick={() => canEdit && setEditing(true)}
      title={overdue ? "마감 초과" : near ? "임박" : "데드라인"}
      style={{
        fontSize: 14, fontWeight: 700,
        padding: "2px 8px", borderRadius: 999,
        background: color + "22", color, border: "1px solid " + color,
        cursor: canEdit ? "pointer" : "default",
        fontFamily: "monospace",
      }}>🗓 {deadline}{overdue ? " ⚠" : near ? " ⏳" : ""}</span>
  );
}

/* 루트 인폼 머리에 붙는 상태 패널 (flow 진행 + 이력) */
function MailDialog({ root, user, reasonTemplates, onClose }) {
  // v8.7.2: 인폼 → 사내 메일 API 로 HTML 본문 전송 (multipart).
  // v8.8.3: 공용 메일그룹(/api/mail-groups/list) 도 함께 노출 — 만들어진 그룹이 드롭다운에 안 뜨던 문제 해결.
  //          + 새 그룹 관리 서브 모달(z-index 10001 로 메일 다이얼로그 위에 올라오게).
  // v8.8.17: 사유별 메일 템플릿 prefill — subject/body 초기값에 치환.
  const [recipients, setRecipients] = useState([]);
  const [groups, setGroups] = useState({});          // {groupName: [emails]}
  const [publicGroups, setPublicGroups] = useState([]); // v8.8.3: 공용 메일 그룹 목록 [{id,name,members,extra_emails}]
  const [pickedUsers, setPickedUsers] = useState([]);   // usernames
  const [pickedGroups, setPickedGroups] = useState([]); // group names
  const _tpl = (reasonTemplates || {})[root.reason || ""] || {};
  const _subst = (s) => (s || "")
    .replaceAll("{product}", root.product || "")
    .replaceAll("{lot}", root.lot_id || "")
    .replaceAll("{wafer}", root.wafer_id || "")
    .replaceAll("{module}", root.module || "")
    .replaceAll("{reason}", root.reason || "");
  const _defSubject = _tpl.subject
    ? _subst(_tpl.subject)
    : "";
  const _defBody = _tpl.body ? _subst(_tpl.body) : "";
  const [subject, setSubject] = useState(_defSubject);
  const [body, setBody] = useState(_defBody);
  const [statusCode, setStatusCode] = useState("");
  // v8.8.30: 스레드(답글) 포함 옵션 제거 — 본문에 스레드를 넣지 않는다.
  //   이유: 인폼 자체가 대화 스레드인데 메일 본문에 다시 넣으면 중복/용량 증가.
  //   필요 시 수신자가 인폼 페이지에서 직접 열람 (첨부 xlsx 로도 핵심은 전달).
  const includeThread = false;
  const [extraEmails, setExtraEmails] = useState("");
  const [attachments, setAttachments] = useState([]); // inform image URLs to include
  const [filter, setFilter] = useState("");
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(null);
  const [error, setError] = useState("");
  const [showMgr, setShowMgr] = useState(false);  // v8.8.3: 공용 메일 그룹 관리 서브모달
  const [newGroupName, setNewGroupName] = useState("");
  const [newGroupEmails, setNewGroupEmails] = useState("");
  // v8.8.21: 실시간 메일 프리뷰 — body 바뀔 때마다 debounce 후 fetch.
  const [preview, setPreview] = useState(null);
  const formatBytes = (n) => {
    const v = Number(n || 0);
    if (!Number.isFinite(v) || v <= 0) return "0 KB";
    if (v >= 1024 * 1024) return `${(v / (1024 * 1024)).toFixed(2)} MB`;
    return `${Math.max(0.1, v / 1024).toFixed(1)} KB`;
  };
  useEffect(() => {
    if (!root?.id) return;
    const h = setTimeout(() => {
      const q = new URLSearchParams();
      q.set("body", body || "");
      q.set("subject", subject || "");
      computedEmails().forEach(em => q.append("to", em));
      sf(API + "/" + encodeURIComponent(root.id) + "/mail-preview?" + q.toString())
        .then(d => setPreview(d)).catch(() => setPreview(null));
    }, 250);
    return () => clearTimeout(h);
  }, [root?.id, body, subject, pickedUsers, pickedGroups, extraEmails, recipients, groups, publicGroups]);

  const reloadGroups = () => {
    sf(API + "/mail-groups").then(d => setGroups(d.groups || {})).catch(() => setGroups({}));
    sf("/api/mail-groups/list").then(d => setPublicGroups(d.groups || [])).catch(() => setPublicGroups([]));
  };

  useEffect(() => {
    sf(API + "/recipients").then(d => setRecipients(d.recipients || [])).catch(() => setRecipients([]));
    reloadGroups();
  }, []);

  // v8.8.3: admin 모듈 그룹(groups) + 공용 그룹(publicGroups) 병합.
  // 공용 그룹은 members(username 목록) → email 로 resolve + extra_emails 합집합.
  const resolveGroupEmails = (gname) => {
    if (groups[gname]) return groups[gname] || [];
    const pg = publicGroups.find(g => g.name === gname);
    if (!pg) return [];
    const out = new Set();
    (pg.members || []).forEach(un => {
      const em = recipients.find(r => r.username === un)?.email;
      if (em && em.includes("@")) out.add(em);
    });
    (pg.extra_emails || []).forEach(em => { if (em && em.includes("@")) out.add(em); });
    return Array.from(out);
  };
  const allGroupNames = Array.from(new Set([
    ...Object.keys(groups || {}),
    ...publicGroups.map(g => g.name).filter(Boolean),
  ])).sort();

  // Collect attachable images from root + any thread child (if provided via root.images)
  const inlineImages = [...(root.images || [])].filter(x => x && x.url);

  const toggleUser = (un) => setPickedUsers(p => p.includes(un) ? p.filter(x => x !== un) : [...p, un]);
  const toggleGroup = (g) => setPickedGroups(p => p.includes(g) ? p.filter(x => x !== g) : [...p, g]);
  const toggleAttach = (u) => setAttachments(a => a.includes(u) ? a.filter(x => x !== u) : [...a, u]);
  // v8.8.27: name / username / email 모두 매칭. 동명이인 대응은 그 아래 라벨 렌더에서.
  const visibleList = recipients.filter(r => userMatches(r, filter));
  const computedEmails = () => {
    const out = new Set();
    pickedUsers.forEach(un => {
      // v8.8.21: username 자체가 email id 인 경우 effective_email 로 해결.
      const r = recipients.find(r => r.username === un);
      const em = (r?.effective_email) || r?.email;
      if (em && em.includes("@")) out.add(em);
    });
    // v8.8.3: admin 그룹 + 공용 그룹 모두 지원.
    pickedGroups.forEach(g => resolveGroupEmails(g).forEach(em => { if (em && em.includes("@")) out.add(em); }));
    (extraEmails || "").split(/[,\s;]+/).map(s => s.trim()).filter(s => s && s.includes("@")).forEach(em => out.add(em));
    return Array.from(out);
  };
  const totalEmails = computedEmails().length;
  const previewEmails = totalEmails ? computedEmails() : (preview?.resolved_recipients || []);
  const effectiveEmailCount = totalEmails || (preview?.auto_module_used ? previewEmails.length : 0);

  const doSend = () => {
    setError(""); setSent(null);
    const to = computedEmails();
    if (to.length === 0 && !(root.module || "").trim()) { setError("수신자를 선택하거나 인폼 모듈을 지정하세요."); return; }
    if (to.length > 199) { setError(`수신자는 최대 199명입니다 (현재 ${to.length}명).`); return; }
    setSending(true);
    sf(`${API}/${root.id}/send-mail`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        to, to_users: pickedUsers, groups: pickedGroups,
        subject: subject.trim(), body: body.trim(),
        include_thread: includeThread, status_code: statusCode.trim(),
        attachments,
      }),
    }).then(r => {
      setSent({ ok: true, to: r.to || to, status: r.status, dry_run: !!r.dry_run });
    }).catch(e => {
      setError(e?.message || "메일 전송 실패");
    }).finally(() => setSending(false));
  };

  const S = { width: "100%", padding: "6px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14, outline: "none" };

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.75)", zIndex: 9999, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
      <div onClick={e => e.stopPropagation()} style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", borderRadius: 10, padding: 18, width: "96%", maxWidth: 1180, maxHeight: "94vh", overflow: "auto", color: "var(--text-primary)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <div style={{ fontSize: 15, fontWeight: 700 }}>✉ 인폼 메일 보내기 <span style={{ fontSize: 14, fontWeight: 400, color: "var(--text-secondary)" }}>(최대 199명 · 본문 2MB · 첨부 10MB)</span></div>
          <span onClick={onClose} style={{ cursor: "pointer", fontSize: 18 }}>✕</span>
        </div>
        <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 8 }}>Admin 설정의 메일 API 로 multipart POST. 수신자 총 <b style={{ color: "var(--accent)" }}>{effectiveEmailCount}명</b> · Inform <code>{root.id}</code></div>
        {/* v8.8.1: 발송자 ID 자동 명시 제거. 제품 담당자 라인만 본문 상단에 삽입. */}
        <div style={{ fontSize: 14, padding: "6px 10px", marginBottom: 10, borderRadius: 4, background: INFO.bg, border: `1px solid ${INFO.fg}`, color: "rgba(29,78,216,0.95)" }}>
          📨 발송계정: 시스템(Admin) · 본문 상단에 <b>제품 담당자</b> 라인 자동 삽입 (해당 제품에 등록된 담당자 있을 때).
        </div>

        {/* v8.8.3: Module recipient groups — admin 그룹 + 공용 메일그룹 합집합. 만들어진 그룹도 노출. */}
        <div style={{ marginBottom: 10 }}>
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 4, display: "flex", alignItems: "center", gap: 6 }}>
            <span>📮 메일 그룹 <span style={{ fontWeight: 400, color: "var(--text-secondary)" }}>({pickedGroups.length} 선택 · {allGroupNames.length} 가용)</span></span>
            <span style={{ flex: 1 }} />
            <button type="button" onClick={() => setShowMgr(true)}
              style={{ padding: "2px 10px", borderRadius: 4, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", fontSize: 14, cursor: "pointer" }}>관리</button>
          </div>
          {allGroupNames.length === 0 && (
            <div style={{ fontSize: 14, color: "var(--text-secondary)", padding: 6, border: "1px dashed var(--border)", borderRadius: 4 }}>
              등록된 메일 그룹 없음 — 우측 [관리] 로 새 그룹을 만드세요.
            </div>
          )}
          {allGroupNames.length > 0 && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {allGroupNames.map((gname) => {
                const on = pickedGroups.includes(gname);
                const emails = resolveGroupEmails(gname);
                const isPublic = !groups[gname];
                return (
                  <span key={gname} onClick={() => toggleGroup(gname)} style={{
                    padding: "5px 12px", borderRadius: 999, fontSize: 14,
                    background: on ? "var(--accent)" : "var(--bg-card)",
                    color: on ? WHITE : "var(--text-primary)",
                    border: "1px solid " + (on ? "var(--accent)" : "var(--border)"),
                    cursor: "pointer", fontWeight: 600,
                  }} title={isPublic ? "공용 메일 그룹" : "admin 모듈 그룹"}>
                    {isPublic ? "[공용] " : ""}{gname} · {emails.length}명
                  </span>
                );
              })}
            </div>
          )}
        </div>

        {/* v8.8.3: 공용 메일 그룹 관리 서브모달 — z-index 10001 로 부모 MailDialog(9999) 위에 확실히 올라옴. */}
        {showMgr && (
          <div onClick={() => setShowMgr(false)}
               style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", zIndex: 10001, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
            <div onClick={e => e.stopPropagation()}
                 style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", borderRadius: 8, padding: 16, width: "90%", maxWidth: 560, color: "var(--text-primary)" }}>
              <div style={{ display: "flex", alignItems: "center", marginBottom: 10 }}>
                <div style={{ fontSize: 14, fontWeight: 700 }}>📮 공용 메일 그룹 관리</div>
                <span style={{ flex: 1 }} />
                <span onClick={() => setShowMgr(false)} style={{ cursor: "pointer", fontSize: 16 }}>✕</span>
              </div>
              <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 8 }}>
                모든 로그인 유저가 공용으로 사용하는 메일 그룹 (inform / meeting 공용). 이름 + 이메일 콤마/세미콜론 구분으로 입력하면 바로 생성됩니다.
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 2fr", gap: 6, marginBottom: 8 }}>
                <input value={newGroupName} onChange={e => setNewGroupName(e.target.value)}
                  placeholder="그룹 이름" style={S} />
                <input value={newGroupEmails} onChange={e => setNewGroupEmails(e.target.value)}
                  placeholder="member1@x.com, member2@y.com" style={{ ...S, fontFamily: "monospace" }} />
              </div>
              <div style={{ display: "flex", gap: 6, marginBottom: 12 }}>
                <button type="button" onClick={() => {
                  const nm = (newGroupName || "").trim();
                  if (!nm) { alert("그룹 이름을 입력하세요"); return; }
                  const extras = (newGroupEmails || "").split(/[,\s;]+/).map(s => s.trim()).filter(s => s && s.includes("@"));
                  postJson("/api/mail-groups/create", { name: nm, extra_emails: extras, members: [] })
                    .then(() => { setNewGroupName(""); setNewGroupEmails(""); reloadGroups(); })
                    .catch(e => alert(e.message));
                }}
                  style={{ padding: "6px 14px", borderRadius: 4, border: "none", background: "var(--accent)", color: WHITE, fontSize: 14, fontWeight: 600, cursor: "pointer" }}>+ 그룹 생성</button>
              </div>
              <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 4 }}>현재 공용 그룹 ({publicGroups.length})</div>
              <div style={{ maxHeight: 240, overflowY: "auto", border: "1px solid var(--border)", borderRadius: 4, background: "var(--bg-card)" }}>
                {publicGroups.length === 0 && (
                  <div style={{ padding: 10, fontSize: 14, color: "var(--text-secondary)", textAlign: "center" }}>공용 그룹 없음</div>
                )}
                {publicGroups.map(g => (
                  <div key={g.id} style={{ display: "flex", alignItems: "center", gap: 6, padding: "5px 10px", borderBottom: "1px solid var(--border)", fontSize: 14 }}>
                    <b style={{ fontFamily: "monospace" }}>{g.name}</b>
                    <span style={{ color: "var(--text-secondary)", fontFamily: "monospace" }}>
                      · members {(g.members || []).length} · extras {(g.extra_emails || []).length}
                    </span>
                    <span style={{ flex: 1 }} />
                    <span onClick={() => {
                      if (!window.confirm(`그룹 "${g.name}" 삭제?`)) return;
                      sf("/api/mail-groups/delete?id=" + encodeURIComponent(g.id), { method: "POST" })
                        .then(() => reloadGroups())
                        .catch(e => alert(e.message));
                    }} style={{ cursor: "pointer", color: BAD.fg, fontSize: 14, fontWeight: 600 }}>삭제</span>
                  </div>
                ))}
              </div>
              <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 10 }}>
                <button type="button" onClick={() => setShowMgr(false)}
                  style={{ padding: "6px 14px", borderRadius: 4, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", fontSize: 14, cursor: "pointer" }}>닫기</button>
              </div>
            </div>
          </div>
        )}

        {/* Individual recipient picker */}
        <div style={{ marginBottom: 10 }}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4, fontSize: 14, fontWeight: 600 }}>
            <span>개별 유저 ({pickedUsers.length} 선택)</span>
            <input value={filter} onChange={e => setFilter(e.target.value)} placeholder="🔎 유저/이메일 검색" style={{ padding: "3px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14, width: 200 }} />
          </div>
          <div style={{ maxHeight: 140, overflow: "auto", border: "1px solid var(--border)", borderRadius: 6, background: "var(--bg-card)" }}>
            {visibleList.length === 0 && <div style={{ padding: 14, textAlign: "center", fontSize: 14, color: "var(--text-secondary)" }}>유저가 없습니다.</div>}
            {/* v8.8.27: 이름(실명) + username 동시 표시 — 동명이인 구분. BE 는 admin/hol/test/비email 이미 필터. */}
            {visibleList.map(r => {
              const on = pickedUsers.includes(r.username);
              const nm = (r.name || "").trim();
              return (
                <div key={r.username} onClick={() => toggleUser(r.username)} style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 10px", fontSize: 14, cursor: "pointer", background: on ? "rgba(59,130,246,0.12)" : "transparent", borderBottom: "1px solid var(--border)" }}>
                  <input type="checkbox" checked={on} readOnly />
                  {nm
                    ? <><span style={{ fontWeight: 600 }}>{nm}</span>
                        <span style={{ fontSize: 14, color: "var(--text-secondary)", fontFamily: "monospace" }}>({r.username})</span></>
                    : <span style={{ fontWeight: 600 }}>{r.username}</span>}
                </div>
              );
            })}
          </div>
        </div>

        <div style={{ marginBottom: 10 }}>
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 3 }}>추가 이메일 <span style={{ fontWeight: 400, color: "var(--text-secondary)" }}>(콤마/공백/세미콜론 구분)</span></div>
          <input value={extraEmails} onChange={e => setExtraEmails(e.target.value)} placeholder="ext1@vendor.com, ext2@vendor.com" style={{ ...S, fontFamily: "monospace", fontSize: 14 }} />
        </div>

        {/* v8.8.1: statusCode 등 백엔드 전용 필드는 UI 에서 제거 — admin 기본값으로 자동 주입됨. */}
        <div style={{ marginBottom: 8 }}>
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 3 }}>제목</div>
          <input value={subject} onChange={e => setSubject(e.target.value)} placeholder={preview?.subject || "비워두면 plan 적용 통보 제목 자동 생성"} style={S} />
        </div>
        <div style={{ marginBottom: 8 }}>
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 3 }}>본문 프로즈 <span style={{ fontWeight: 400, color: "var(--text-secondary)" }}>(HTML content 상단에 강조 삽입, 생략 가능)</span></div>
          <textarea value={body} onChange={e => setBody(e.target.value)} rows={4} placeholder="비워두면 모듈팀 plan 적용 통보 문구가 자동 생성됩니다." style={{ ...S, resize: "vertical" }} />
          {preview?.owners_line && (
            <div style={{ marginTop: 4, fontSize: 14, color: GREEN.fg, background: GREEN.soft, border: `1px solid ${GREEN.fg}`, borderRadius: 4, padding: "4px 8px" }}>
              📌 자동 삽입: <b>제품담당자</b> : {preview.owners_line}
            </div>
          )}
          {preview?.auto_module_used && (preview.auto_module_recipients || []).length > 0 && (
            <div style={{ marginTop: 4, fontSize: 14, color: INFO.fg, background: INFO.bg, border: `1px solid ${INFO.fg}55`, borderRadius: 4, padding: "4px 8px" }}>
              자동 수신: {(preview.auto_module_recipients || []).map(r => `${r.username} <${r.email}>`).join(", ")}
            </div>
          )}
        </div>
        {/* v8.8.21: 실시간 미리보기 — 실제 보낼 HTML body, 수신자, 담당자 라인을 한눈에. */}
        {preview?.html_body && (
          <details style={{ marginBottom: 10, border: "1px solid var(--border)", borderRadius: 5, padding: "4px 10px", background: "var(--bg-card)" }} open>
            <summary style={{ fontSize: 14, fontWeight: 600, cursor: "pointer", color: "var(--accent)" }}>
              🔍 메일 미리보기 · 제목 [{subject || preview.subject || "자동"}] · 수신자 {effectiveEmailCount}명
            </summary>
            <div style={{ marginTop: 6, marginBottom: 6, display: "flex", gap: 8, flexWrap: "wrap", fontSize: 14 }}>
              <span style={{ padding: "3px 8px", borderRadius: 999, background: INFO.bg, color: INFO.fg, border: `1px solid ${INFO.fg}55` }}>
                본문 {formatBytes(preview.html_size_bytes)}
              </span>
              <span style={{ padding: "3px 8px", borderRadius: 999, background: GREEN.bg, color: "rgba(5,150,105,0.95)", border: "1px solid rgba(5,150,105,0.28)" }}>
                자동 첨부 {formatBytes(preview.attachment_total_bytes)}
              </span>
              <span style={{ padding: "3px 8px", borderRadius: 999, background: WARN.bg, color: "rgba(180,83,9,0.95)", border: "1px solid rgba(180,83,9,0.28)" }}>
                SplitTable xlsx {(preview.auto_attachments || []).length}개
              </span>
            </div>
            <div style={{ marginTop: 6, fontSize: 14, color: "var(--text-secondary)", marginBottom: 4, fontFamily: "monospace" }}>
              To: {previewEmails.slice(0, 8).join(", ")}{previewEmails.length > 8 ? ` (+${previewEmails.length - 8}명)` : ""}
            </div>
            <div style={{ maxHeight: 560, overflowY: "auto", overflowX: "hidden", background: WHITE, color: "var(--text-primary)", padding: 10, border: "1px solid var(--border)", borderRadius: 4 }}
                 dangerouslySetInnerHTML={{ __html: preview.html_body }} />
          </details>
        )}
        {/* v8.8.30: 스레드 포함 옵션 제거 — 메일 본문은 제품/Lot/작성자/작성시간 + SplitTable 스냅샷 중심으로 간결화. */}
        {preview?.html_over_limit && (
          <div style={{ marginBottom: 8, padding: "6px 10px", border: `1px solid ${BAD.fg}`, background: BAD.bg, borderRadius: 4, color: BAD.fg, fontSize: 14 }}>
            ⚠ 메일 본문 HTML 크기 {preview.html_size_kb}KB — 2MB 한도 초과. SplitTable 컬럼 수를 줄이거나 본문을 단축해야 발송 가능합니다.
          </div>
        )}
        {preview && preview.html_size_kb != null && !preview.html_over_limit && (
          <div style={{ marginBottom: 8, fontSize: 14, color: "var(--text-secondary)" }}>
            📦 HTML 본문 크기: {preview.html_size_kb}KB / {Math.round((preview.html_size_limit_bytes || 2097152) / 1024)}KB
          </div>
        )}

        {inlineImages.length > 0 && <div style={{ marginBottom: 10 }}>
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 3 }}>📎 첨부 이미지 <span style={{ fontWeight: 400, color: "var(--text-secondary)" }}>(각 파일 10MB 한도 · 총합 제한)</span></div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {inlineImages.map(img => {
              const on = attachments.includes(img.url);
              return <span key={img.url} onClick={() => toggleAttach(img.url)} style={{
                padding: "4px 10px", borderRadius: 4, fontSize: 14,
                background: on ? "rgba(16,185,129,0.15)" : "var(--bg-card)",
                color: on ? OK.fg : "var(--text-primary)",
                border: "1px solid " + (on ? OK.fg : "var(--border)"),
                cursor: "pointer",
              }}>{on ? "✔" : "＋"} {img.filename || img.url.split("/").pop()}</span>;
            })}
          </div>
        </div>}

        {/* v8.8.21: 직접 파일첨부 UI 제거 → 인폼 스냅샷 xlsx 자동 첨부로 대체.
             인폼에 담긴 제품/lot/wafer + splittable_change + body 를 SplitTable 엑셀 형식으로
             BE 가 렌더 → 메일 files 에 자동 포함 된다. 인라인 이미지 첨부는 그대로 유지. */}
        {preview?.auto_attachments?.length > 0 && (
          <div style={{ marginBottom: 10, padding: 8, borderRadius: 5, background: GREEN.soft, border: `1px solid ${OK.fg}` }}>
            <div style={{ fontSize: 14, fontWeight: 600, color: OK.fg }}>📎 자동 첨부 (SplitTable 스냅샷 xlsx)</div>
            {preview.auto_attachments.map((a, i) => (
              <div key={i} style={{ fontSize: 14, fontFamily: "monospace", color: "var(--text-secondary)", marginTop: 2 }}>
                · {a.name} ({formatBytes(a.bytes)})
              </div>
            ))}
            <div style={{ marginTop: 6, fontSize: 14, color: "var(--text-secondary)" }}>
              총 첨부 용량: {formatBytes(preview.attachment_total_bytes)}
            </div>
          </div>
        )}

        {error && <div style={{ padding: "6px 10px", background: BAD.bg, color: BAD.fg, border: `1px solid ${BAD.fg}`, borderRadius: 4, fontSize: 14, marginBottom: 8 }}>⚠ {error}</div>}
        {sent && <div style={{ padding: "6px 10px", background: GREEN.bg, color: OK.fg, border: `1px solid ${OK.fg}`, borderRadius: 4, fontSize: 14, marginBottom: 8 }}>✔ 전송됨 ({(sent.to || []).length}명){sent.dry_run && " · DRY RUN (실제 전송 안됨)"}</div>}

        <div style={{ display: "flex", gap: 8 }}>
          <button disabled={sending} onClick={doSend} style={{ padding: "8px 20px", borderRadius: 6, border: "none", background: sending ? "var(--text-secondary)" : "var(--accent)", color: WHITE, fontWeight: 600, cursor: sending ? "wait" : "pointer" }}>{sending ? "전송 중…" : `📧 ${effectiveEmailCount}명에게 전송`}</button>
          <button onClick={onClose} style={{ padding: "8px 16px", borderRadius: 6, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", cursor: "pointer" }}>닫기</button>
        </div>
      </div>
    </div>
  );
}

function RootHeader({ root, onChangeStatus, user }) {
  // v8.7.9: FLOW 큰 카드 제거 → 접수/완료 2단계 + 크고 눈에 띄는 "확인 완료" 버튼.
  const [openHist, setOpenHist] = useState(false);
  const [openMail, setOpenMail] = useState(false);
  const hist = root.status_history || [];
  const mailHist = root.mail_history || [];
  const mailCount = mailHist.length;
  const lastMailAt = mailCount ? (mailHist[mailHist.length - 1].at || mailHist[mailHist.length - 1].sent_at || "") : "";
  const isCompleted = root.flow_status === "completed";
  const toggleDone = () => {
    const next = isCompleted ? "received" : "completed";
    onChangeStatus(root.id, next, "");
  };
  return (
    <div style={{
      padding: "0 2px", marginBottom: 4,
      display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap",
    }}>
      {/* v8.8.13: wafer + 메일 + 이력 을 한 줄로 통합. 외부 wafer 라벨 제거하고 이 내부에 흡수 →
          이전에 있던 빈 RootHeader 라인(왼쪽 공백+오른쪽 메일/이력만)을 제거. */}
      <span style={{ fontSize: 14, fontFamily: "monospace", color: "var(--text-secondary)" }}>
        wafer: <b style={{ color: "var(--text-primary)" }}>{root.wafer_id || "-"}</b>
      </span>
      <div style={{ flex: 1 }} />
      <span onClick={() => setOpenMail(true)}
        title={lastMailAt ? `최근 메일: ${(lastMailAt || "").replace("T"," ").slice(0,16)}` : "사내 메일 API 로 이 인폼 내용 전송"}
        style={{ padding: "2px 8px", borderRadius: 4, border: "1px solid var(--accent)",
                 background: "rgba(249,115,22,0.08)", color: "var(--accent)",
                 fontSize: 14, fontWeight: 700, cursor: "pointer", userSelect: "none", lineHeight: 1.3 }}>
        ✉ 메일{mailCount > 0 && ` (${mailCount})`}
      </span>
      <span onClick={() => setOpenHist(!openHist)}
        title="상태 변경 이력 토글"
        style={{ fontSize: 14, color: "var(--accent)", cursor: "pointer", padding: "2px 6px" }}>
        이력{hist.length > 0 && ` (${hist.length})`}
      </span>
      {openMail && <MailDialog root={root} user={user} onClose={() => setOpenMail(false)} />}
      {openHist && hist.length > 0 && (
        <div style={{ width: "100%", marginTop: 4, paddingTop: 4, borderTop: "1px dashed var(--border)", fontSize: 14, color: "var(--text-secondary)", fontFamily: "monospace" }}>
          {hist.slice().reverse().map((h, i) => (
            <div key={i} style={{ marginBottom: 2 }}>
              {(h.at || "").replace("T", " ")} · <b>{h.actor}</b> → <StatusBadge status={h.status} />
              {h.note && <> · <span style={{ opacity: 0.8 }}>{h.note}</span></>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* Plan change summary — 스레드 내 모든 splittable_change 를 상단에 묶어서 노출 */
function PlanSummaryCard({ thread }) {
  const changes = (thread || []).filter(x => x.splittable_change && (x.splittable_change.column || x.splittable_change.new_value));
  if (changes.length === 0) return null;
  return (
    <div style={{
      background: WARN.bg, border: `1px solid ${WARN.fg}66`,
      borderRadius: 8, padding: 10, marginBottom: 10,
    }}>
      <div style={{ fontSize: 14, fontWeight: 700, color: "rgba(194,65,12,0.95)", marginBottom: 6 }}>
        ■ Split Table 변경 요약 ({changes.length}건)
      </div>
      {changes.map(x => {
        const sc = x.splittable_change;
        return (
          <div key={x.id} style={{ fontSize: 14, fontFamily: "monospace", marginBottom: 2 }}>
            <span style={{ opacity: 0.7 }}>{x.author}</span>
            {" · "}
            {sc.column && <span style={{ color: "rgba(194,65,12,0.95)" }}>{sc.column}</span>}
            {sc.column && ": "}
            <span style={{ textDecoration: "line-through", opacity: 0.6 }}>{sc.old_value || "-"}</span>
            {" → "}
            <span style={{ color: GREEN.fg, fontWeight: 700 }}>{sc.new_value || "-"}</span>
          </div>
        );
      })}
      <div style={{ fontSize: 14, color: "rgba(146,64,14,0.95)", marginTop: 6, opacity: 0.85 }}>
        * 위 column 은 SplitTable 에서 해당 인폼과 연결된 컬럼입니다.
      </div>
    </div>
  );
}

/* v8.8.0: SplitTable 노트 카드 — root_lot_id 키로 fetch 한 wafer/param/lot/param_global 노트 표시 */
function SplitNotesCard({ notes, root_lot_id }) {
  if (!notes || notes.length === 0) return null;
  const wafers = notes.filter(n => n.scope === "wafer");
  const params = notes.filter(n => n.scope === "param");
  const lots   = notes.filter(n => n.scope === "lot");
  const pgs    = notes.filter(n => n.scope === "param_global");
  const renderRow = (n, kind, color) => {
    const parts = (n.key || "").split("__");
    let label = "";
    if (n.scope === "wafer") label = `🏷 W${(parts[2] || "").replace(/^W/, "")}`;
    else if (n.scope === "param") label = `💬 W${(parts[2] || "").replace(/^W/, "")} × ${parts[3] || ""}`;
    else if (n.scope === "lot") label = `📌 LOT ${parts[2] || ""}`;
    else if (n.scope === "param_global") label = `🌐 ${parts[2] || ""} (전역)`;
    return (
      <div key={n.id} style={{ padding: "6px 10px", marginBottom: 4, borderRadius: 5, background: "var(--bg-card)", border: "1px solid var(--border)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 3, gap: 6 }}>
          <span style={{ fontSize: 14, fontWeight: 700, padding: "1px 6px", borderRadius: 8, background: color, color: WHITE }}>{label}</span>
          <span style={{ fontSize: 14, color: "var(--text-secondary)", fontFamily: "monospace" }}>
            {n.username} · {(n.created_at || "").replace("T", " ").slice(0, 16)}
          </span>
        </div>
        <div style={{ fontSize: 14, whiteSpace: "pre-wrap", lineHeight: 1.45 }}>{n.text}</div>
      </div>
    );
  };
  return (
    <div style={{ background: INFO.bg, border: `1px solid ${INFO.fg}66`, borderRadius: 8, padding: 10, marginBottom: 10 }}>
      <div style={{ fontSize: 14, fontWeight: 700, color: "rgba(29,78,216,0.95)", marginBottom: 6 }}>
        📝 SplitTable 노트 — root_lot_id <span style={{ fontFamily: "monospace" }}>{root_lot_id}</span> ({notes.length}건)
        <span style={{ fontSize: 14, fontWeight: 500, marginLeft: 8, color: "var(--text-secondary)" }}>
          wafer {wafers.length} · param {params.length} · lot {lots.length} · 전역 {pgs.length}
        </span>
      </div>
      {wafers.map(n => renderRow(n, "wafer", INFO.fg))}
      {params.map(n => renderRow(n, "param", PURPLE.fg))}
      {lots.map(n => renderRow(n, "lot", chartPalette.series[13]))}
      {pgs.map(n => renderRow(n, "param_global", TEAL.fg))}
    </div>
  );
}

/* v8.7.8: Lot drill-down 모듈별 요약 테이블
   각 모듈에 대해 (등록됨, 메일 전송됨) 을 체크/미체크로 한눈에 */
function LotModuleSummary({ thread, modules }) {
  const rows = (modules || []).map(m => {
    const entries = (thread || []).filter(e => (e.module || "") === m);
    const hasInform = entries.length > 0;
    // v8.7.9: 가장 최근 메일 날짜 뽑기.
    let lastMailAt = "";
    let mailCount = 0;
    for (const e of entries) {
      for (const mh of (e.mail_history || [])) {
        const at = mh.at || mh.sent_at || mh.time || "";
        if (!at) continue;
        mailCount += 1;
        if (at > lastMailAt) lastMailAt = at;
      }
    }
    // 완료(담당자 확인) 여부 + 가장 최근 확인 날짜
    let completedAt = "";
    for (const e of entries) {
      if (e.flow_status === "completed") {
        const hist = (e.status_history || []).filter(h => h.status === "completed");
        const last = hist.length ? (hist[hist.length - 1].at || "") : "";
        if (last > completedAt) completedAt = last;
      }
    }
    const count = entries.length;
    return { module: m, hasInform, mailCount, lastMailAt, completedAt, count };
  });
  if (!rows.length) return null;
  // v8.8.12: LocalStorage 기반 접기 상태 유지.
  const COLLAPSE_KEY = "flow_inform_module_summary_collapsed";
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem(COLLAPSE_KEY) === "1"; } catch { return false; }
  });
  const toggle = () => {
    const nv = !collapsed;
    setCollapsed(nv);
    try { localStorage.setItem(COLLAPSE_KEY, nv ? "1" : "0"); } catch {}
  };
  return (
    <div style={{ marginBottom: 8, padding: "7px 8px", borderRadius: 7, background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
      <div onClick={toggle}
        style={{ fontSize: 14, fontWeight: 800, marginBottom: collapsed ? 0 : 6, fontFamily: "monospace", color: "var(--accent)", cursor: "pointer", userSelect: "none", display: "flex", alignItems: "center", gap: 6, lineHeight: 1.2 }}>
        <span>{collapsed ? "▶" : "▼"}</span>
        <span>모듈별 진행 요약</span>
        <span style={{ fontSize: 14, color: "var(--text-secondary)", fontWeight: 600, marginLeft: 4 }}>
          ({rows.filter(r => r.hasInform).length} 모듈 활성 / {rows.reduce((s, r) => s + (r.count || 0), 0)} 건)
        </span>
      </div>
      {!collapsed && <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(126px,1fr))", gap: 5 }}>
        {rows.map(r => {
          const mc = moduleColor(r.module);
          const done = !!r.completedAt;
          const border = done ? OK.fg : (r.hasInform ? mc : "var(--border)");
          return (
            <div key={r.module} title={`메일: ${r.mailCount}회 ${r.lastMailAt ? r.lastMailAt.replace("T", " ").slice(0, 16) : ""}\n담당자 확인: ${r.completedAt ? r.completedAt.replace("T", " ").slice(0, 16) : "미완료"}`}
              style={{ minWidth: 0, padding: "5px 7px", borderRadius: 6, border: `1px solid ${border}`, background: r.hasInform ? `${mc}12` : "var(--bg-primary)", display: "grid", gap: 3 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 5, minWidth: 0 }}>
                <span style={{ width: 6, height: 6, borderRadius: 999, background: done ? OK.fg : (r.hasInform ? mc : "var(--text-tertiary, var(--text-secondary))"), flex: "0 0 auto" }} />
                <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 14, fontFamily: "monospace", fontWeight: 800, color: r.hasInform ? "var(--text-primary)" : "var(--text-secondary)" }}>{r.module}</span>
                <span style={{ marginLeft: "auto", fontSize: 14, fontFamily: "monospace", color: "var(--text-secondary)" }}>{r.count || 0}</span>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 14, fontFamily: "monospace", color: "var(--text-secondary)", whiteSpace: "nowrap" }}>
                <span style={{ color: r.hasInform ? OK.fg : "var(--text-tertiary, var(--text-secondary))", fontWeight: 700 }}>{r.hasInform ? "등록" : "미등록"}</span>
                <span style={{ color: r.mailCount ? INFO.fg : "var(--text-tertiary, var(--text-secondary))" }}>메일 {r.mailCount || 0}</span>
                <span style={{ color: done ? OK.fg : "var(--text-tertiary, var(--text-secondary))" }}>{done ? "확인" : "대기"}</span>
              </div>
            </div>
          );
        })}
      </div>}
    </div>
  );
}


/* ── 메인 페이지 ── */
export default function My_Inform({ user }) {
  const initialInformQueryRef = useRef(null);
  if (!initialInformQueryRef.current) initialInformQueryRef.current = parseInformFiltersFromUrl();
  const [activeTab, setActiveTab] = useState(initialInformQueryRef.current.tab);
  const [sharedFilters, setSharedFilters] = useState(initialInformQueryRef.current.filters);
  const [informViewMode, setInformViewMode] = useState("table");
  const [auditRows, setAuditRows] = useState([]);
  const [auditLoading, setAuditLoading] = useState(false);
  const [drawerMode, setDrawerMode] = useState("detail");
  const [drawerBackMode, setDrawerBackMode] = useState("");
  const [cellDrawer, setCellDrawer] = useState(null);
  const [lotDrawer, setLotDrawer] = useState(null);
  const [lotDrawerModule, setLotDrawerModule] = useState("");

  // v8.8.1: 설정에 products(카탈로그) + raw_db_root 추가.
  const [constants, setConstants] = useState({ modules: [], reasons: [], flow_statuses: [], products: [], raw_db_root: "", reason_templates: {} });
  // v8.8.1: 선택 제품의 Lot 후보 (RAWDATA_DB 에서 폴더 스캔).
  const [productLots, setProductLots] = useState({ product: "", lots: [], source: "" });
  const [mode, setMode] = useState("all");           // all | mine | product | lot | wafer
  const [myMods, setMyMods] = useState({ modules: [], all_rounder: false });

  const [wafers, setWafers] = useState([]);
  const [products, setProducts] = useState([]);
  const [lots, setLots] = useState([]);

  const [search, setSearch] = useState("");
  const [selectedWafer, setSelectedWafer] = useState("");
  const [selectedLot, setSelectedLot] = useState("");
  const [selectedProduct, setSelectedProduct] = useState("");

  const [thread, setThread] = useState([]);          // 선택 scope 의 전체 entries (wafer/lot/product)
  const [lotWafers, setLotWafers] = useState([]);    // lot 모드에서 포함된 wafer 들
  const [listRoots, setListRoots] = useState([]);
  const [moduleSummary, setModuleSummary] = useState([]);
  const [summaryTab, setSummaryTab] = useState("matrix");
  const [lotMatrix, setLotMatrix] = useState({ products: [], module_order: [] });
  const [lotMatrixLoading, setLotMatrixLoading] = useState(false);
  const [lotMatrixFilters, setLotMatrixFilters] = useState({ product: "", search: "", states: [] });
  const [selectedRootId, setSelectedRootId] = useState("");
  const [detailTab, setDetailTab] = useState("body");
  const [listFilters, setListFilters] = useState({
    query: "", module: "", status: "", product: "", root_lot: "", fab_lot: "", author: "", start: "", end: "",
  });
  const [wizardStep, setWizardStep] = useState(0);
  const [wizardAttachMode, setWizardAttachMode] = useState("auto");
  const [wizardMailDraft, setWizardMailDraft] = useState({ subject: "", body: "", generatedFor: "" });
  const [wizardMailMeta, setWizardMailMeta] = useState({ recipients: [], knobMap: {} });
  const [mailDialogRoot, setMailDialogRoot] = useState(null);

  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState(defaultInformForm);
  const [createImages, setCreateImages] = useState([]);
  const [uploadingMain, setUploadingMain] = useState(false);
  const [embedFetching, setEmbedFetching] = useState(false);
  const [msg, setMsg] = useState("");

  const [moduleFilter, setModuleFilter] = useState([]);  // 체크된 모듈만 표시 (빈 배열=모두 해제)
  // v8.8.15: 제품 필터 nav — 체크된 제품만 통과. 빈 배열은 "모두 해제"라서 목록을 비운다.
  const [productFilter, setProductFilter] = useState([]);
  // v8.8.13: moduleFilter 기본 = 내 조회 권한 모든 모듈. admin 또는 all_rounder 이면 전체.
  //   myMods/constants 가 로딩되면 1회 자동 셋업. 이후엔 사용자 체크 토글이 우선.
  const [moduleFilterInit, setModuleFilterInit] = useState(false);
  const [productFilterInit, setProductFilterInit] = useState(false);

  // v8.8.0: SplitTable 노트 — Lot 뷰 하단에 표시 (root_lot_id 키).
  const [splitNotes, setSplitNotes] = useState([]);

  // v8.8.0: 제품별 담당자 (product_contacts). 사이드바 폴더블 + 메일 본문에 자동 첨부.
  const [productContacts, setProductContacts] = useState({}); // { product: [contacts] }
  const [openContactProducts, setOpenContactProducts] = useState({}); // { product: bool }
  const [editContact, setEditContact] = useState(null); // {product, id?, name, role, email, phone, note}
  // v8.8.2: 유저/그룹 혼합 일괄 추가 모달 상태.
  const [bulkPickProduct, setBulkPickProduct] = useState("");  // opened for which product
  const [bulkEligibleUsers, setBulkEligibleUsers] = useState([]);
  const [bulkGroups, setBulkGroups] = useState([]);
  const [bulkSelUsers, setBulkSelUsers] = useState([]);
  const [bulkSelGroups, setBulkSelGroups] = useState([]);
  const [bulkRole, setBulkRole] = useState("");
  const [bulkBusy, setBulkBusy] = useState(false);

  const isAdmin = user?.role === "admin";
  const addCatalogProduct = async (rawProduct, { onAdded, onDuplicate } = {}) => {
    const product = stripMlPrefix(String(rawProduct || "").trim());
    if (!product) return null;
    try {
      const d = await postJson(API + "/products/add", { product });
      if (onAdded) onAdded(product, d);
      return d;
    } catch (error) {
      const existing = parseDuplicateProductError(error);
      if (String(error?.message || "").includes("duplicate_product") || existing) {
        const target = existing || product;
        setMsg("기존 제품으로 이동");
        try { window.alert("기존 제품으로 이동"); } catch (_) {}
        if (onDuplicate) onDuplicate(target);
        return { duplicate: true, existing_product: target };
      }
      throw error;
    }
  };

  const loadModuleSummary = () => {
    sf(API + "/modules/summary?days=" + encodeURIComponent(filterDays(sharedFilters)))
      .then(d => setModuleSummary(Array.isArray(d) ? d : (d.modules || [])))
      .catch(() => setModuleSummary([]));
  };

  const loadLotMatrix = () => {
    const q = new URLSearchParams();
    q.set("days", String(filterDays(sharedFilters)));
    if ((sharedFilters.products || []).length === 1) q.set("product", sharedFilters.products[0]);
    if ((sharedFilters.lot || "").trim()) q.set("search", sharedFilters.lot.trim());
    setLotMatrixLoading(true);
    return sf(API + "/lot-matrix?" + q.toString())
      .then(d => setLotMatrix({
        products: Array.isArray(d.products) ? d.products : [],
        module_order: Array.isArray(d.module_order) ? d.module_order : [],
      }))
      .catch(() => setLotMatrix({ products: [], module_order: [] }))
      .finally(() => setLotMatrixLoading(false));
  };

  const loadInformList = () => {
    sf(API + "/recent?limit=500")
      .then(d => setListRoots(d.informs || []))
      .catch(() => setListRoots([]));
  };

  const loadAuditLog = () => {
    const q = new URLSearchParams();
    q.set("days", String(filterDays(sharedFilters)));
    uniqueClean(sharedFilters.products).forEach(v => q.append("products[]", v));
    uniqueClean(sharedFilters.modules).forEach(v => q.append("modules[]", v));
    uniqueClean(sharedFilters.types).forEach(v => q.append("types[]", v));
    if ((sharedFilters.lot || "").trim()) q.set("lot_search", sharedFilters.lot.trim());
    if (sharedFilters.period === "custom") {
      if (sharedFilters.start) q.set("start", sharedFilters.start);
      if (sharedFilters.end) q.set("end", sharedFilters.end);
    }
    setAuditLoading(true);
    return sf(API + "/audit-log?" + q.toString())
      .then(d => setAuditRows(d.audit || d.logs || []))
      .catch(() => setAuditRows([]))
      .finally(() => setAuditLoading(false));
  };

  const loadDetailForRoot = (root, opts = {}) => {
    if (!root) { setThread([]); setLotWafers([]); return; }
    const rawLot = String(root?.lot_id || "").trim();
    const lotKey = (root?.root_lot_id || (isFabLotInput(rawLot) ? rawLot.slice(0, 5) : rawLot) || "").trim();
    if (!lotKey) {
      setThread([root]);
      setLotWafers([]);
      return;
    }
    setSelectedLot(lotKey);
    const q = new URLSearchParams();
    q.set("lot_id", lotKey);
    if (opts.includeDeleted) q.set("include_deleted", "true");
    sf(API + "/by-lot?" + q.toString())
      .then(d => {
        setThread(d.informs || []);
        setLotWafers(d.wafers || []);
      })
      .catch(() => { setThread([root]); setLotWafers([]); });
  };

  const openRootForDetail = (root) => {
    if (!root) return;
    setDrawerMode("detail");
    setDrawerBackMode("");
    setSelectedRootId(root.id);
    setDetailTab("body");
    loadDetailForRoot(root);
  };

  const openRootFromDrawerStack = (root, backMode) => {
    if (!root) return;
    setDrawerMode("detail");
    setDrawerBackMode(backMode || "");
    setSelectedRootId(root.id);
    setDetailTab("body");
    loadDetailForRoot(root);
  };

  const openAuditRow = (row) => {
    const informId = String(row?.inform_id || row?.target_id || "").trim();
    if (!informId) return;
    setDrawerMode("detail");
    setDrawerBackMode("");
    setSelectedRootId(informId);
    setDetailTab("history");
    loadDetailForRoot({
      id: informId,
      root_lot_id: row.root_lot_id || row.lot_id || "",
      lot_id: row.lot_id || row.root_lot_id || "",
      product: row.product || "",
      module: row.module || "",
    }, { includeDeleted: true });
  };

  const openCreateWizard = () => {
    sf(API + "/config").then(d => setConstants(c => ({
      ...c,
      modules: d.modules || c.modules,
      reasons: d.reasons || c.reasons,
      products: d.products || c.products,
      raw_db_root: d.raw_db_root ?? c.raw_db_root,
      reason_templates: d.reason_templates || c.reason_templates,
    }))).catch(() => {});
    try {
      const raw = localStorage.getItem(WIZARD_DRAFT_KEY);
      if (raw) {
        const draft = JSON.parse(raw);
        if (draft?.form) setForm({ ...defaultInformForm(), ...draft.form });
        if (Array.isArray(draft?.createImages)) setCreateImages(draft.createImages);
        if (typeof draft?.wizardStep === "number") setWizardStep(Math.max(0, Math.min(4, draft.wizardStep)));
        if (draft?.wizardAttachMode) setWizardAttachMode(draft.wizardAttachMode);
        if (Array.isArray(draft?.embedCustomCols)) setEmbedCustomCols(draft.embedCustomCols);
        if (draft?.wizardMailDraft) setWizardMailDraft(draft.wizardMailDraft);
      }
    } catch (_) {}
    setCreating(true);
  };

  useEffect(() => {
    loadInformList();
    loadModuleSummary();
    loadAuditLog();
  }, []);

  useEffect(() => {
    loadLotMatrix();
  }, [sharedFilters.products.join("|"), sharedFilters.lot, sharedFilters.period, sharedFilters.start, sharedFilters.end]);

  useEffect(() => {
    loadModuleSummary();
  }, [sharedFilters.period, sharedFilters.start, sharedFilters.end]);

  useEffect(() => {
    loadAuditLog();
  }, [sharedFilters.products.join("|"), sharedFilters.modules.join("|"), sharedFilters.types.join("|"), sharedFilters.lot, sharedFilters.period, sharedFilters.start, sharedFilters.end]);

  useEffect(() => {
    syncInformQuery(activeTab, sharedFilters);
  }, [activeTab, sharedFilters]);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      if (!(selectedRootId || drawerMode === "cell" || drawerMode === "lot")) return;
      setSelectedRootId("");
      setDrawerMode("detail");
      setDrawerBackMode("");
      setCellDrawer(null);
      setLotDrawer(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectedRootId, drawerMode]);

  /* Load constants + my modules */
  useEffect(() => {
    // v8.8.1: /config 에서 products + raw_db_root 까지 같이 받는다.
    sf(API + "/config").then(d => setConstants({
      modules: d.modules || [], reasons: d.reasons || [], flow_statuses: d.flow_statuses || [],
      products: d.products || [], raw_db_root: d.raw_db_root || "",
      reason_templates: d.reason_templates || {},
    })).catch(() => {
      sf(API + "/modules").then(d => setConstants(c => ({ ...c,
        modules: d.modules || [], reasons: d.reasons || [], flow_statuses: d.flow_statuses || [],
      }))).catch(() => {});
    });
    // v8.8.13: 인폼 전용 my-modules — admin 이 유저별로 설정한 inform_user_modules 우선, 없으면 groups fallback.
    sf("/api/informs/my-modules").then(d => setMyMods({
      modules: d.modules || [], all_rounder: !!d.all_rounder,
    })).catch(() => setMyMods({ modules: [], all_rounder: !!isAdmin }));
  }, []);

  const moduleFilterOptions = useMemo(() => {
    const all = constants.modules || [];
    const my = (myMods.all_rounder || isAdmin) ? all : (myMods.modules || []).filter(m => all.includes(m));
    return my.length ? my : all;
  }, [constants.modules, myMods, isAdmin]);

  // v8.8.13: moduleFilter 기본값 = 내 권한 모듈 전체 체크. 최초 한 번만.
  useEffect(() => {
    if (moduleFilterInit) return;
    if (moduleFilterOptions.length === 0) return;
    setModuleFilter([...moduleFilterOptions]);
    setModuleFilterInit(true);
  }, [moduleFilterOptions, moduleFilterInit]);

  const loadSidebar = () => {
    sf(API + "/sidebar")
      .then(d => {
        setWafers(d.wafers || []);
        setProducts(d.products || []);
        setLots(d.lots || []);
      })
      .catch(() => {
        sf(API + "/wafers").then(d => setWafers(d.wafers || [])).catch(() => setWafers([]));
        sf(API + "/products").then(d => setProducts(d.products || [])).catch(() => setProducts([]));
        sf(API + "/lots").then(d => setLots(d.lots || [])).catch(() => setLots([]));
      });
  };
  useEffect(()=>{loadSidebar();}, [mode]);

  /* Scope 별 thread 로드 */
  useEffect(() => {
    if (mode === "wafer" && selectedWafer) {
      sf(API + "?wafer_id=" + encodeURIComponent(selectedWafer))
        .then(d => { setThread(d.informs || []); setLotWafers([]); })
        .catch(() => setThread([]));
    } else if (mode === "lot" && selectedLot) {
      sf(API + "/by-lot?lot_id=" + encodeURIComponent(selectedLot))
        .then(d => { setThread(d.informs || []); setLotWafers(d.wafers || []); })
        .catch(() => { setThread([]); setLotWafers([]); });
    } else if (mode === "product" && selectedProduct) {
      sf(API + "/by-product?product=" + encodeURIComponent(selectedProduct))
        .then(d => { setThread(d.informs || []); setLotWafers([]); })
        .catch(() => setThread([]));
    } else if (mode === "mine") {
      sf(API + "/my").then(d => { setThread(d.informs || []); setLotWafers([]); })
        .catch(() => setThread([]));
    } else if (mode === "all" || mode === "gantt") {
      sf(API + "/recent?limit=300").then(d => { setThread(d.informs || []); setLotWafers([]); })
        .catch(() => setThread([]));
    } else {
      setThread([]); setLotWafers([]);
    }
  }, [mode, selectedWafer, selectedLot, selectedProduct]);

  // v8.8.0: Lot 뷰 진입 시 SplitTable 노트 로드 (root_lot_id 키).
  useEffect(() => {
    if (mode === "lot" && selectedLot && thread.length > 0) {
      const prod = (thread.find(x => x.product) || {}).product || "";
      if (!prod) { setSplitNotes([]); return; }
      sf("/api/splittable/notes?product=" + encodeURIComponent(prod) + "&root_lot_id=" + encodeURIComponent(selectedLot))
        .then(d => setSplitNotes(d.notes || []))
        .catch(() => setSplitNotes([]));
    } else {
      setSplitNotes([]);
    }
  }, [mode, selectedLot, thread]);

  // v8.8.0: 사이드바에 표시할 제품 담당자 — 모든 product 한꺼번에 로드.
  const loadProductContacts = () => {
    sf("/api/informs/product-contacts")
      .then(d => setProductContacts(d.products || {}))
      .catch(() => setProductContacts({}));
  };
  useEffect(() => { loadProductContacts(); }, []);

  const saveContact = () => {
    if (!editContact) return;
    const { id, product, name, role, email, phone, note } = editContact;
    if (!product || !name) { alert("product/name 필수"); return; }
    const url = id
      ? "/api/informs/product-contacts/update?id=" + encodeURIComponent(id)
      : "/api/informs/product-contacts";
    postJson(url, { product, name, role, email, phone, note })
      .then(() => {
        // v8.8.7: 담당자가 붙은 제품은 카탈로그에도 자동 등록 (이미 있으면 no-op).
        //   이렇게 하면 새 인폼 폼 드롭다운에 바로 노출됨 (이전엔 productContacts 키에만 존재해서 누락).
        if (!(constants.products || []).includes(product)) {
          addCatalogProduct(product, {
            onAdded: (_product, d) => setConstants(c => ({ ...c, products: d.products || c.products })),
          })
            .catch(() => {});
        }
        setEditContact(null);
        loadProductContacts();
      })
      .catch(e => alert("저장 실패: " + (e.message || e)));
  };
  const deleteContact = (product, id) => {
    if (!confirm("담당자를 삭제하시겠어요?")) return;
    postJson("/api/informs/product-contacts/delete?id=" + encodeURIComponent(id) + "&product=" + encodeURIComponent(product), {})
      .then(() => loadProductContacts())
      .catch(e => alert("삭제 실패: " + (e.message || e)));
  };

  // v8.8.2: 유저/그룹 혼합 일괄 추가 모달.
  const openBulkPick = (product) => {
    setBulkPickProduct(product);
    setBulkSelUsers([]); setBulkSelGroups([]); setBulkRole("");
    // v8.8.19: 인폼 담당자 전용 필터 (/api/informs/eligible-contacts) — admin 역할 + admin/hol/test 포함 username 제외.
    sf("/api/informs/eligible-contacts").then(d => setBulkEligibleUsers(d.users || [])).catch(() => setBulkEligibleUsers([]));
    sf("/api/groups/list").then(d => setBulkGroups(d.groups || [])).catch(() => setBulkGroups([]));
  };
  const runBulkAdd = () => {
    if (!bulkPickProduct) return;
    if (bulkSelUsers.length === 0 && bulkSelGroups.length === 0) { alert("유저 또는 그룹을 선택하세요."); return; }
    setBulkBusy(true);
    postJson("/api/informs/product-contacts/bulk-add", {
      product: bulkPickProduct,
      usernames: bulkSelUsers,
      group_ids: bulkSelGroups,
      role: bulkRole,
    })
      .then(r => {
        setBulkBusy(false);
        // v8.8.7: bulk add 도 동일 — 제품이 카탈로그에 없으면 자동 등록.
        if (!(constants.products || []).includes(bulkPickProduct)) {
          addCatalogProduct(bulkPickProduct, {
            onAdded: (_product, d) => setConstants(c => ({ ...c, products: d.products || c.products })),
          })
            .catch(() => {});
        }
        const msg = `추가 ${r.added?.length || 0}명 / 스킵 ${r.skipped?.length || 0}명 (중복/차단).`;
        alert(msg);
        setBulkPickProduct("");
        loadProductContacts();
      })
      .catch(e => { setBulkBusy(false); alert("추가 실패: " + (e.message || e)); });
  };

  const refreshAll = () => {
    loadSidebar();
    loadInformList();
    loadModuleSummary();
    loadLotMatrix();
    loadAuditLog();
    const selected = listRoots.find(x => x.id === selectedRootId) || thread.find(x => x.id === selectedRootId);
    const rawLot = String(selected?.lot_id || "").trim();
    const lotForDetail = selected?.root_lot_id || selectedLot || (isFabLotInput(rawLot) ? rawLot.slice(0, 5) : rawLot);
    if (lotForDetail) {
      sf(API + "/by-lot?lot_id=" + encodeURIComponent(lotForDetail))
        .then(d => { setThread(d.informs || []); setLotWafers(d.wafers || []); });
    } else if (mode === "wafer" && selectedWafer) {
      sf(API + "?wafer_id=" + encodeURIComponent(selectedWafer)).then(d => setThread(d.informs || []));
    } else if (mode === "lot" && selectedLot) {
      sf(API + "/by-lot?lot_id=" + encodeURIComponent(selectedLot))
        .then(d => { setThread(d.informs || []); setLotWafers(d.wafers || []); });
    } else if (mode === "product" && selectedProduct) {
      sf(API + "/by-product?product=" + encodeURIComponent(selectedProduct)).then(d => setThread(d.informs || []));
    } else if (mode === "mine") {
      sf(API + "/my").then(d => setThread(d.informs || []));
    } else {
      sf(API + "/recent?limit=300").then(d => setThread(d.informs || []));
    }
  };

  const openRootDetail = (root) => {
    openRootForDetail(root);
  };

  const create = () => {
    const lot = (form.lot_id || "").trim();
    if (!form.product.trim()) { setMsg("product 를 선택해 주세요."); return Promise.reject(new Error("product required")); }
    if (!lot) { setMsg("lot 을 선택해 주세요."); return Promise.reject(new Error("lot required")); }
    if (!form.module) { setMsg("module 을 선택해 주세요."); return Promise.reject(new Error("module required")); }
    if (!form.reason) { setMsg("사유를 선택해 주세요."); return Promise.reject(new Error("reason required")); }
    if (!form.text.trim() && createImages.length === 0) {
      setMsg("note 를 입력해 주세요."); return Promise.reject(new Error("text required"));
    }
    const body = {
      wafer_id: "", lot_id: lot, product: form.product.trim(),
      module: form.module, reason: form.reason, text: form.text, parent_id: null,
      images: createImages,
    };
    if (form.attach_split && (form.split.column || form.split.new_value)) {
      body.splittable_change = { ...form.split, applied: false };
    }
    if (form.attach_embed && hasEmbedSnapshot(form.embed)) {
      body.embed_table = form.embed;
    }
    // v8.8.15: fab_lot_id 스냅샷 — 입력값이 fab_lot_id 포맷이면 그대로 전달. 아니면 서버가 root5 기준 resolve.
    {
      const isFabLot = isFabLotInput(lot, lotOptions);
      if (isFabLot) body.fab_lot_id_at_save = lot;
    }
    return sf(API, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((res) => {
      setForm(defaultInformForm());
      setCreateImages([]);
      setWizardStep(0);
      setWizardAttachMode("auto");
      setWizardMailDraft({ subject: "", body: "", generatedFor: "" });
      setCreating(false); setMsg("");
      try { localStorage.removeItem(WIZARD_DRAFT_KEY); } catch (_) {}
      const created = res?.inform;
      if (created?.id) {
        setSelectedRootId(created.id);
        setDetailTab("body");
        setSelectedLot(created.root_lot_id || (isFabLotInput(lot, lotOptions) ? lot.slice(0, 5) : lot));
        loadDetailForRoot(created);
      }
      setTimeout(refreshAll, 50);
    }).catch(e => setMsg(e.message));
  };

  // v8.8.0: 본문 textarea 에 이미지 Ctrl+V 붙여넣기 → 업로드 후 본문에 markdown 으로 즉시 inline 삽입.
  // (별도 첨부 카드 X. images 배열에도 추가해 메일 첨부 후보로 유지.)
  const handleBodyPaste = async (e) => {
    const items = (e.clipboardData && e.clipboardData.items) || [];
    const imgs = [];
    for (const it of items) {
      if (it.kind === "file" && /^image\//.test(it.type || "")) {
        const file = it.getAsFile();
        if (file) imgs.push(file);
      }
    }
    if (!imgs.length) return;
    e.preventDefault();
    setUploadingMain(true);
    const ta = e.currentTarget;
    const start = ta.selectionStart ?? (form.text || "").length;
    const end   = ta.selectionEnd   ?? start;
    let inserted = "";
    const out = [];
    for (const f of imgs) {
      try {
        const fd = new FormData();
        const ext = (f.type || "image/png").split("/")[1] || "png";
        const named = new File([f], `paste_${Date.now()}.${ext}`, { type: f.type });
        fd.append("file", named);
        const res = await sf("/api/informs/upload", { method: "POST", body: fd });
        out.push({ filename: res.filename, url: res.url, size: res.size });
        inserted += `\n![${res.filename}](${res.url})\n`;
      } catch (err) { alert("붙여넣기 업로드 실패: " + err.message); }
    }
    // 본문 inline 삽입 (커서 위치 보존).
    setForm(f => {
      const cur = f.text || "";
      const next = cur.slice(0, start) + inserted + cur.slice(end);
      return { ...f, text: next };
    });
    if (out.length) setCreateImages(prev => [...prev, ...out]);
    setUploadingMain(false);
  };

  const uploadMain = async (fl) => {
    if (!fl || fl.length === 0) return;
    setUploadingMain(true);
    const out = [];
    for (const f of Array.from(fl)) {
      try {
        const fd = new FormData();
        fd.append("file", f);
        const res = await sf("/api/informs/upload", { method: "POST", body: fd });
        out.push({ filename: res.filename, url: res.url, size: res.size });
      } catch (e) { alert("업로드 실패: " + e.message); }
    }
    setCreateImages((prev) => [...prev, ...out]);
    setUploadingMain(false);
  };

  // v8.8.8: product + lot_id 쌍이 유효하면 /api/splittable/view 로 현재 시점 SplitTable 스냅샷(plan 포함)
  //   을 자동으로 fetch → form.embed 에 attach. 사용자가 "가져오기" 버튼을 누르는 단계 제거.
  //   lot_id 변경이나 product 변경 시 재실행. 실패하면 조용히 attach 끔 (에러 배너 X).
  // v8.8.11: SplitTable prefix (KNOB/MASK/INLINE/VM/FAB/ALL) 또는 CUSTOM 프리셋 중 하나 선택.
  //          변경 시 useEffect 가 해당 scope 로 /view 재호출.
  const [embedPrefix, setEmbedPrefix] = useState("ALL");   // string | null (CUSTOM 모드면 null)
  const [embedCustomName, setEmbedCustomName] = useState(""); // "" = prefix 모드
  const [customsList, setCustomsList] = useState([]);
  // v8.8.16: 인폼 전용 인라인 CUSTOM 편집기 — SplitTable 사이드바와 동일 UX.
  //   embedCustomCols: 현재 편집중 컬럼 리스트 (set 선택 시 로드). embedCustomSearch: 컬럼 필터링.
  //   embedSchemaCols: 제품 전체 스키마 (lot 없이 fetch).
  //   embedCustomOpen: 편집기 접힘/펼침 상태.
  //   snapshotTick: 사용자가 "Search" 버튼 누르면 증가 → useEffect 가 스냅샷 재fetch.
  const [embedCustomCols, setEmbedCustomCols] = useState([]);
  const [embedCustomSearch, setEmbedCustomSearch] = useState("");
  const [embedSchemaCols, setEmbedSchemaCols] = useState([]);
  const [embedCustomOpen, setEmbedCustomOpen] = useState(false);
  const [snapshotTick, setSnapshotTick] = useState(0);
  const [lotOptions, setLotOptions] = useState([]);  // [{value, type:"root"|"fab"}]

  useEffect(() => {
    sf("/api/splittable/customs").then(d => setCustomsList(d.customs || [])).catch(() => {});
  }, []);

  // v8.8.16: 제품 변경 시 ML_TABLE 전체 스키마 fetch — CUSTOM 컬럼 선택 pool.
  useEffect(() => {
    const prod = (form.product || "").trim();
    if (!prod) { setEmbedSchemaCols([]); return; }
    const mlProd = prod.startsWith("ML_TABLE_") ? prod : `ML_TABLE_${prod}`;
    sf(`/api/splittable/schema?product=${encodeURIComponent(mlProd)}`)
      .then(d => setEmbedSchemaCols((d.columns || []).map(c => c.name || c)))
      .catch(() => setEmbedSchemaCols([]));
  }, [form.product]);

  useEffect(() => {
    const prod = (form.product || "").trim();
    const lot  = (form.lot_id || "").trim();
    if (!creating) { setEmbedFetching(false); return; }
    if (wizardAttachMode === "none") {
      setEmbedFetching(false);
      setForm(f => f.attach_embed ? { ...f, attach_embed: false, embed: emptyEmbedTable() } : f);
      return;
    }
    if (snapshotTick <= 0) { setEmbedFetching(false); return; }
    if (!prod || !lot) {
      setEmbedFetching(false);
      setForm(f => (f.attach_embed && f.embed?.source?.startsWith?.("SplitTable/"))
        ? { ...f, attach_embed: false, embed: emptyEmbedTable() }
        : f);
      return;
    }
    const mlProd = prod.startsWith("ML_TABLE_") ? prod : `ML_TABLE_${prod}`;
    const customCols = (wizardAttachMode === "custom" ? (Array.isArray(embedCustomCols) ? embedCustomCols : []) : [])
      .map(c => String(c || "").trim())
      .filter(Boolean);
    const isFabLot = isFabLotInput(lot, lotOptions);
    const handle = setTimeout(() => {
      setEmbedFetching(true);
      postJson("/api/informs/splittable-snapshot", {
        product: mlProd,
        lot_id: lot,
        custom_cols: customCols,
        is_fab_lot: isFabLot,
      })
        .then(d => {
          const embed = d?.embed || emptyEmbedTable();
          setForm(f => ({
            ...f, attach_embed: true,
            embed,
          }));
          setEmbedFetching(false);
        })
        .catch(() => { setEmbedFetching(false); });
    }, 400);
    return () => { clearTimeout(handle); setEmbedFetching(false); };
    // v8.8.16: snapshotTick 변경 시에도 재fetch — 사용자가 Search 버튼으로 명시적 갱신.
  }, [form.product, form.lot_id, creating, embedCustomCols, snapshotTick, lotOptions, wizardAttachMode]);

  useEffect(() => {
    if (!creating) return;
    if (!(form.product || "").trim() || !(form.lot_id || "").trim()) return;
    setSnapshotTick(x => x > 0 ? x : 1);
  }, [creating, form.product, form.lot_id]);

  // v8.8.10: SplitTable 의 lot-candidates 로 root_lot_id + fab_lot_id 후보 fetch → Lot 드롭다운 소스.
  //   기존 /product-lots (RAWDATA_DB 폴더 스캔) 은 사내 실환경에서 빈 결과 자주 발생 → SplitTable 기반 primary.
  useEffect(() => {
    const prod = (form.product || "").trim();
    if (!prod) { setLotOptions([]); return; }
    const mlProd = prod.startsWith("ML_TABLE_") ? prod : `ML_TABLE_${prod}`;
    Promise.all([
      sf(`/api/splittable/lot-candidates?product=${encodeURIComponent(mlProd)}&col=root_lot_id&limit=200`).catch(() => ({ candidates: [] })),
      sf(`/api/splittable/lot-candidates?product=${encodeURIComponent(mlProd)}&col=fab_lot_id&limit=500`).catch(() => ({ candidates: [] })),
    ]).then(([rr, fr]) => {
      const out = [];
      const seen = new Set();
      for (const v of (rr.candidates || [])) {
        const s = String(v || "").trim();
        if (s && !seen.has(s)) { seen.add(s); out.push({ value: s, type: "root" }); }
      }
      for (const v of (fr.candidates || [])) {
        const s = String(v || "").trim();
        if (s && !seen.has(s)) { seen.add(s); out.push({ value: s, type: "fab" }); }
      }
      setLotOptions(out);
    });
  }, [form.product]);

  useEffect(() => {
    if (!creating) return;
    try {
      localStorage.setItem(WIZARD_DRAFT_KEY, JSON.stringify({
        form,
        createImages,
        wizardStep,
        wizardAttachMode,
        embedCustomCols,
        wizardMailDraft,
      }));
    } catch (_) {}
  }, [creating, form, createImages, wizardStep, wizardAttachMode, embedCustomCols, wizardMailDraft]);

  useEffect(() => {
    if (!creating || wizardStep !== 3) return;
    const mod = (form.module || "").trim();
    if (!mod) {
      setWizardMailMeta({ recipients: [], knobMap: {} });
      return;
    }
    Promise.all([
      sf(API + "/modules/recipients?module=" + encodeURIComponent(mod)).catch(() => ({ recipients: [] })),
      sf(API + "/modules/knob-map").catch(() => ({ knob_map: {} })),
    ]).then(([rec, knobs]) => {
      setWizardMailMeta({ recipients: rec.recipients || [], knobMap: knobs.knob_map || {} });
      const key = [form.product, form.lot_id, form.module, form.reason].join("|");
      setWizardMailDraft(d => {
        if (d.generatedFor === key && (d.subject || d.body)) return d;
        const subject = `[plan 적용 통보] ${stripMlPrefix(form.product || "")} ${form.lot_id || ""} - ${mod}`.trim();
        const body = [
          `안녕하세요. ${mod}팀에 다음과 같이 plan 적용 인폼을 등록합니다.`,
          `제품: ${stripMlPrefix(form.product || "-")}`,
          `Lot: ${form.lot_id || "-"}`,
          `사유: ${form.reason || "-"}`,
          "",
          form.text || "",
        ].join("\n");
        return { subject, body, generatedFor: key };
      });
    });
  }, [creating, wizardStep, form.product, form.lot_id, form.module, form.reason, form.text]);

  // v8.8.0: SplitTable 에서 현재 product 의 plan 스냅샷을 본문에 임베드.
  // 빈 history 인 경우 명시적으로 알림 + paste 폴백 제안.
  const embedFromSplitTable = async () => {
    const prod = (form.product || "").trim();
    if (!prod) { alert("product 를 먼저 입력하세요."); return; }
    setEmbedFetching(true);
    try {
      const hist = await sf("/api/splittable/history?product=" + encodeURIComponent(prod) + "&limit=100");
      const all = (hist.history || []);
      const rows = all.slice(-50).map(h => [
        (h.time || "").replace("T", " ").slice(0, 19),
        h.user || "", h.action || "", h.cell || "",
        h.old === null || h.old === undefined ? "" : String(h.old),
        h.new === null || h.new === undefined ? "" : String(h.new),
        h.root_lot_id || "",
      ]);
      if (rows.length === 0) {
        // 폴백: 빈 history 면 paste 모드로 전환 — 사용자가 직접 표 데이터 붙여넣기.
        const want = window.confirm(
          `'${prod}' 의 SplitTable 히스토리가 비어 있습니다.\n\n대신 표 데이터를 직접 붙여넣을까요?\n(Excel/SplitTable 셀 영역을 복사한 뒤 OK → 붙여넣기 모달이 열립니다)`
        );
        if (want) setPasteOpen(true);
        return;
      }
      setForm(f => ({
        ...f, attach_embed: true,
        embed: {
          source: `SplitTable/${stripMlPrefix(prod)} (history)`,
          columns: ["time", "user", "action", "cell", "old", "new", "lot"],
          rows,
          note: `${rows.length} entries embedded`,
        },
      }));
    } catch (e) {
      alert("SplitTable 가져오기 실패: " + e.message);
    } finally { setEmbedFetching(false); }
  };

  // v8.8.0/v8.8.6: TSV/CSV paste → embed_table. 첫 줄 = 컬럼.
  //   v8.8.6: 세트 저장/조회가 팀 공용 `/api/splittable/paste-sets` 로 이전.
  //           LocalStorage 는 fallback (서버 실패 시만).
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const [pasteSetName, setPasteSetName] = useState("");
  const [pasteSets, setPasteSets] = useState([]);
  const reloadPasteSets = () => {
    const prod = encodeURIComponent(form.product || "");
    sf("/api/splittable/paste-sets?product=" + prod)
      .then(d => setPasteSets(d.sets || []))
      .catch(() => {
        // fallback: localStorage legacy
        try { setPasteSets(JSON.parse(localStorage.getItem("flow_paste_sets_v1") || "[]")); } catch {}
      });
  };
  useEffect(() => { if (pasteOpen) reloadPasteSets(); }, [pasteOpen, form.product]);
  const applyPasteAsEmbed = () => {
    const txt = (pasteText || "").trim();
    if (!txt) { alert("표 데이터를 먼저 붙여넣으세요"); return; }
    const lines = txt.split(/\r?\n/).filter(l => l.length);
    if (lines.length < 2) { alert("최소 2줄 (헤더 + 1행) 이 필요합니다"); return; }
    const sep = lines[0].includes("\t") ? "\t" : (lines[0].includes(",") ? "," : "\t");
    const cols = lines[0].split(sep);
    const rows = lines.slice(1).map(l => l.split(sep));
    setForm(f => ({
      ...f, attach_embed: true,
      embed: {
        source: pasteSetName.trim() || `paste/${(form.product || "manual")}`,
        columns: cols, rows,
        note: `${rows.length} rows pasted${pasteSetName.trim() ? ` (set: ${pasteSetName.trim()})` : ""}`,
      },
    }));
    // v8.8.6: 이름 주어지면 팀 공용 세트에 저장 (BE + LocalStorage 폴백).
    if (pasteSetName.trim()) {
      const payload = { name: pasteSetName.trim(), product: form.product || "", columns: cols, rows, username: user?.username || "" };
      postJson("/api/splittable/paste-sets/save", payload)
        .catch(() => {
          try {
            const KEY = "flow_paste_sets_v1";
            const cur = JSON.parse(localStorage.getItem(KEY) || "[]");
            const next = [{ ...payload, saved_at: new Date().toISOString() },
                          ...cur.filter(s => s.name !== pasteSetName.trim())].slice(0, 50);
            localStorage.setItem(KEY, JSON.stringify(next));
          } catch {}
        });
    }
    setPasteOpen(false); setPasteText(""); setPasteSetName("");
  };
  // legacy helper (pasteSets state 가 주력이지만 일부 호출부가 함수 호출 형태 유지).
  const loadPasteSets = () => pasteSets;

  const reply = (parentId, body) => {
    // parent 의 wafer/lot/product 상속은 서버가 알아서
    const parent = thread.find(x => x.id === parentId);
    return sf(API, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...body, wafer_id: parent?.wafer_id || "", parent_id: parentId,
        images: body.images || [],
      }),
    }).then(refreshAll);
  };

  /* 모듈/제품 필터: 빈 배열은 "모두 해제" 로 간주해서 목록을 비운다.
     기본 상태는 모듈/제품 모두 전체 체크로 초기화한다.
     제품 매칭은 canonical (ML_TABLE_ 제거) 기준 — `PRODA` 하나 체크하면 records 의
     `PRODA` 와 `ML_TABLE_PRODA` 양쪽 다 통과. */
  const canonProd = (s) => stripMlPrefix(String(s || "").trim()).toLowerCase();
  const applyModFilter = (arr) => {
    if (moduleFilterOptions.length > 0 && (!moduleFilter || moduleFilter.length === 0)) return [];
    if (presentProducts.length > 0 && (!productFilter || productFilter.length === 0)) return [];
    let out = arr;
    if (moduleFilter && moduleFilter.length > 0) {
      const mfSet = new Set(moduleFilter);
      out = out.filter(x => {
        const m = x.module || "";
        if (!m) return mfSet.has("기타") || mfSet.has("미지정");
        return mfSet.has(m);
      });
    }
    if (productFilter && productFilter.length > 0) {
      const pfSet = new Set(productFilter.map(canonProd));
      out = out.filter(x => pfSet.has(canonProd(x.product || "")));
    }
    return out;
  };

  const del = (id) => {
    if (!confirm("삭제하시겠습니까? (작성자/admin만 가능 · 목록에서는 숨김 처리)")) return;
    sf(API + "/" + encodeURIComponent(id), { method: "DELETE" })
      .then(() => { setSelectedRootId(""); setThread([]); refreshAll(); })
      .catch(e => alert(e.message));
  };

  const toggleCheck = (node) => sf(API + "/check?id=" + encodeURIComponent(node.id), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ checked: !node.checked }),
  }).then(refreshAll).catch(e => alert(e.message));

  // 작성자/admin 본문 수정. text/module/reason 만 바뀌고 embed/시각 은 원본 유지 (스냅샷 잠금).
  const editInform = (id, patch) => sf(API + "/edit?id=" + encodeURIComponent(id), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch || {}),
  }).then(refreshAll).catch(e => alert(e.message));

  const changeStatus = (id, status, note) => sf(API + "/status?id=" + encodeURIComponent(id), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status, note: note || "" }),
  }).then(refreshAll).catch(e => alert(e.message));

  // v8.7.9: deadline 폐지. 기존 changeDeadline 은 사용되지 않아 제거.

  /* thread → (roots + childrenByParent) — wafer 모드는 한 wafer 전체 트리,
     lot/product 모드는 여러 루트 흐름이 섞여있을 수 있음. */
  const { rootsSorted, childrenByParent } = useMemo(() => {
    const kids = {};
    const roots = [];
    for (const x of thread) {
      if (x.parent_id) (kids[x.parent_id] = kids[x.parent_id] || []).push(x);
      else roots.push(x);
    }
    // roots sort: 모듈 섞인 뷰(lot/product/all/mine)는 최근순, wafer 단일뷰는 시간순
    const single = mode === "wafer";
    roots.sort((a, b) => single
      ? (a.created_at || "").localeCompare(b.created_at || "")
      : (b.created_at || "").localeCompare(a.created_at || ""));
    // children 시간순
    Object.values(kids).forEach(arr => arr.sort((a, b) => (a.created_at || "").localeCompare(b.created_at || "")));
    return { rootsSorted: roots, childrenByParent: kids };
  }, [thread, mode]);

  const presentProducts = useMemo(() => {
    const seen = new Map();
    (rootsSorted || []).forEach(r => {
      const raw = (r.product || "").trim();
      if (!raw) return;
      const canon = stripMlPrefix(raw);
      const k = canon.toLowerCase();
      if (!seen.has(k)) seen.set(k, canon);
    });
    return Array.from(seen.values()).sort();
  }, [rootsSorted]);

  const selectedRoot = useMemo(() => {
    if (!selectedRootId) return null;
    return (thread || []).find(x => x.id === selectedRootId)
      || (listRoots || []).find(x => x.id === selectedRootId)
      || null;
  }, [selectedRootId, thread, listRoots]);

  const listProductOptions = useMemo(() => {
    const seen = new Map();
    (listRoots || []).forEach(r => {
      const p = stripMlPrefix(r.product || "");
      if (p) seen.set(p.toLowerCase(), p);
    });
    return Array.from(seen.values()).sort();
  }, [listRoots]);

  const matrixProductOptions = useMemo(() => {
    const seen = new Map();
    [
      ...(constants.products || []),
      ...listProductOptions,
      ...((lotMatrix.products || []).map(p => p.product).filter(Boolean)),
    ].forEach(raw => {
      const p = stripMlPrefix(String(raw || "").trim());
      if (p && p !== "미지정") seen.set(p.toLowerCase(), p);
    });
    return Array.from(seen.values()).sort();
  }, [constants.products, listProductOptions, lotMatrix.products]);

  const commonProductOptions = useMemo(() => {
    const seen = new Map();
    [
      ...(constants.products || []),
      ...listProductOptions,
      ...(products || []).map(p => typeof p === "string" ? p : p.product).filter(Boolean),
      ...((lotMatrix.products || []).map(p => p.product).filter(Boolean)),
    ].forEach(raw => {
      const p = stripMlPrefix(String(raw || "").trim());
      if (p && p !== "미지정") seen.set(p.toLowerCase(), p);
    });
    return Array.from(seen.values()).sort();
  }, [constants.products, listProductOptions, products, lotMatrix.products]);

  const filteredLotMatrix = useMemo(() => {
    const productSet = new Set(uniqueClean(sharedFilters.products).map(p => stripMlPrefix(p).toLowerCase()));
    const moduleSet = new Set(uniqueClean(sharedFilters.modules));
    const statusSet = new Set(uniqueClean(sharedFilters.statuses));
    const lotQ = (sharedFilters.lot || "").trim().toLowerCase();
    const modules = (lotMatrix.module_order || []).filter(m => !moduleSet.size || moduleSet.has(m));
    const productsOut = (lotMatrix.products || [])
      .filter(p => !productSet.size || productSet.has(stripMlPrefix(p.product || "").toLowerCase()))
      .map(product => ({
        ...product,
        lots: (product.lots || []).filter(lot => {
          if (lotQ && !String(lot.root_lot_id || "").toLowerCase().includes(lotQ)) return false;
          if (!statusSet.size) return true;
          return modules.some(m => {
            const state = (lot.modules || {})[m]?.state || "pending";
            return statusSet.has(state) || (state !== "completed" && statusSet.has("pending"));
          });
        }),
      }))
      .filter(product => product.lots.length > 0);
    productsOut.forEach(product => {
      const totals = {};
      (product.lots || []).forEach(lot => {
        modules.forEach(module => {
          totals[module] = (totals[module] || 0) + matrixCellCount((lot.modules || {})[module]);
        });
      });
      product.module_totals = totals;
    });
    return { products: productsOut, module_order: modules };
  }, [lotMatrix, sharedFilters]);

  const filteredModuleSummary = useMemo(() => {
    const moduleSet = new Set(uniqueClean(sharedFilters.modules));
    return (moduleSummary || []).filter(row => !moduleSet.size || moduleSet.has(row.module));
  }, [moduleSummary, sharedFilters.modules]);

  const filteredListRoots = useMemo(() => {
    const lotQ = (sharedFilters.lot || "").trim().toLowerCase();
    const productSet = new Set(uniqueClean(sharedFilters.products).map(p => stripMlPrefix(p).toLowerCase()));
    const moduleSet = new Set(uniqueClean(sharedFilters.modules));
    const statusSet = new Set(uniqueClean(sharedFilters.statuses));
    return (listRoots || []).filter(r => {
      if (!dateInSharedPeriod(_entryLastUpdateForUi(r), sharedFilters)) return false;
      if (productSet.size && !productSet.has(stripMlPrefix(r.product || "").toLowerCase())) return false;
      if (moduleSet.size && !moduleSet.has(r.module || "기타")) return false;
      if (statusSet.size) {
        const st = r.flow_status || "received";
        const pending = st !== "completed";
        if (!statusSet.has(st) && !(pending && statusSet.has("pending"))) return false;
      }
      if (lotQ && !lotSearchText(r).toLowerCase().includes(lotQ)) return false;
      return true;
    });
  }, [listRoots, sharedFilters]);

  const setListFilter = (key, value) => setListFilters(f => ({ ...f, [key]: value }));

  const filterListByMatrixLot = (product, lot) => {
    const rootLot = String(lot?.root_lot_id || "").trim();
    const prod = stripMlPrefix(String(product || "").trim());
    setSharedFilters(f => ({
      ...f,
      products: prod && prod !== "미지정" ? [prod] : [],
      lot: rootLot,
    }));
    setActiveTab("inform");
  };

  const openLotMatrixCell = (product, lot, module, cell) => {
    const rootLot = String(lot?.root_lot_id || "").trim();
    if (!rootLot) return;
    const initialRoots = (cell?.recent || []).map(r => ({
      id: r.inform_id,
      root_lot_id: rootLot,
      lot_id: rootLot,
      product,
      module,
      reason: r.reason || "",
      text: r.body_preview || "",
      author: r.author || "",
      created_at: r.updated_at || "",
      flow_status: r.state || "received",
    }));
    setDrawerMode("cell");
    setDrawerBackMode("");
    setCellDrawer({ product, lot, module, cell: cell || { inform_count: 0, recent: [] }, roots: initialRoots, loading: true });
    setSelectedRootId("");
    setSelectedLot(rootLot);
    sf(API + "/by-lot?lot_id=" + encodeURIComponent(rootLot))
      .then(d => {
        const roots = (d.informs || [])
          .filter(x => !x.parent_id && (x.module || "기타") === module)
          .sort((a, b) => String(_entryLastUpdateForUi(b)).localeCompare(String(_entryLastUpdateForUi(a))));
        setThread(d.informs || []);
        setLotWafers(d.wafers || []);
        setCellDrawer(cur => cur ? { ...cur, roots, loading: false, byLot: d } : cur);
      })
      .catch(() => setCellDrawer(cur => cur ? { ...cur, loading: false } : cur));
  };

  const openLotForStrip = (product, lot) => {
    const rootLot = String(lot?.root_lot_id || "").trim();
    if (!rootLot) return;
    setDrawerMode("lot");
    setDrawerBackMode("");
    setSelectedRootId("");
    setSelectedLot(rootLot);
    setLotDrawerModule("");
    setLotDrawer({ product, lot, root_lot_id: rootLot, informs: [], loading: true, module_counts: {}, available_modules: lotMatrix.module_order || [] });
    sf(API + "/by-lot?lot_id=" + encodeURIComponent(rootLot))
      .then(d => {
        setThread(d.informs || []);
        setLotWafers(d.wafers || []);
        setLotDrawer({
          product,
          lot,
          root_lot_id: d.root_lot_id || rootLot,
          informs: d.informs || [],
          loading: false,
          module_counts: d.module_counts || {},
          informed_modules: d.informed_modules || [],
          available_modules: d.available_modules || lotMatrix.module_order || [],
          wafers: d.wafers || [],
        });
      })
      .catch(() => setLotDrawer(cur => cur ? { ...cur, loading: false } : cur));
  };

  const toggleSharedModule = (module) => {
    const mod = String(module || "").trim();
    if (!mod) return;
    setSharedFilters(f => {
      const cur = new Set(f.modules || []);
      if (cur.has(mod)) cur.delete(mod);
      else cur.add(mod);
      return { ...f, modules: Array.from(cur) };
    });
  };

  useEffect(() => {
    if (productFilterInit) return;
    if (presentProducts.length === 0) return;
    setProductFilter([...presentProducts]);
    setProductFilterInit(true);
  }, [presentProducts, productFilterInit]);

  const resetInformFilters = () => {
    setModuleFilter([...moduleFilterOptions]);
    setProductFilter([...presentProducts]);
  };

  /* 사이드바 목록 (mode 별) */
  const sidebarItems = useMemo(() => {
    const q = search.trim().toLowerCase();
    const match = (s) => !q || (s || "").toLowerCase().includes(q);
    if (mode === "wafer") {
      return wafers.filter(w => match(w.wafer_id) || match(w.lot_id) || match(w.product))
        .map(w => ({ key: w.wafer_id, label: w.wafer_id, sub: `${w.count || 0}건 · ${(w.lot_id || "-")} · ${w.product || "-"}` }));
    }
    if (mode === "lot") {
      return lots.filter(l => match(l.lot_id) || match(l.product))
        .map(l => ({ key: l.lot_id, label: l.lot_id, sub: `${l.count || 0}건 · ${l.product || "-"}` }));
    }
    if (mode === "product") {
      return products.filter(p => match(p.product))
        .map(p => ({ key: p.product, label: p.product, sub: `${p.count || 0}건 · 최근 ${(p.last || "").slice(0, 10)}` }));
    }
    return []; // mine/all 은 사이드바 없이 메인에 직접 표시
  }, [mode, wafers, lots, products, search]);

  const selectedKey = mode === "wafer" ? selectedWafer
                    : mode === "lot"  ? selectedLot
                    : mode === "product" ? selectedProduct : "";
  const setSelected = (k) => {
    if (mode === "wafer") setSelectedWafer(k);
    else if (mode === "lot") setSelectedLot(k);
    else if (mode === "product") setSelectedProduct(k);
  };

  const modeButton = (key, label, hint) => (
    <button
      type="button"
      onClick={() => setMode(key)}
      title={hint}
      style={{
        padding: "6px 12px",
        borderRadius: 6,
        border: "1px solid " + (mode === key ? "var(--accent)" : "var(--border)"),
        background: mode === key ? "var(--accent)" : "var(--bg-primary)",
        color: mode === key ? "#fff" : "var(--text-secondary)",
        fontSize: 14,
        fontWeight: 700,
        cursor: "pointer",
      }}
    >
      {label}
    </button>
  );

  // v8.7.8: 모듈 순서 편집 (admin → PageGear)
  // v8.8.27: 새 모듈 이름 input 을 controlled state 로 승격 — 사용자가 Enter 대신 저장 버튼만
  //   눌러도 입력값이 drop 되지 않도록 보정. + 버튼으로 명시적 추가.
  const [modDraft, setModDraft] = useState(null);
  const [modNewName, setModNewName] = useState("");
  const commitPendingMod = (draft, pending) => {
    const v = (pending || "").trim();
    if (!v) return draft;
    if (draft.includes(v)) return draft;
    return [...draft, v];
  };
  const addPendingMod = () => {
    if (!Array.isArray(modDraft)) return;
    const next = commitPendingMod(modDraft, modNewName);
    setModDraft(next);
    setModNewName("");
  };
  const saveModuleOrder = () => {
    if (!Array.isArray(modDraft)) return;
    // v8.8.27: 저장 직전에도 입력칸 값을 흡수 → "새 모듈 저장 안됨" 버그 방지.
    const finalList = commitPendingMod(modDraft, modNewName);
    postJson("/api/informs/config", { modules: finalList })
      .then(d => {
        setConstants(c => ({ ...c, modules: d.config?.modules || finalList }));
        setModDraft(null); setModNewName("");
      })
      .catch(e => alert("모듈 순서 저장 실패: " + (e.message || e)));
  };
  const moveMod = (i, delta) => {
    if (!Array.isArray(modDraft)) return;
    const j = i + delta; if (j < 0 || j >= modDraft.length) return;
    const n = modDraft.slice(); [n[i], n[j]] = [n[j], n[i]]; setModDraft(n);
  };

  return (
    <div className="flow-connected-page" style={{
      height: "calc(100vh - 52px)",
      background: "var(--bg-primary)",
      color: "var(--text-primary)",
      fontSize: 14,
      position: "relative",
      overflow: "hidden",
      display: "flex",
      flexDirection: "column",
      minWidth: 0,
    }}>
      <PageGear title="인폼 설정" canEdit={isAdmin} position="bottom-right">
        <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 8 }}>
          카탈로그, 모듈 순서, 권한, 메일 템플릿을 여기에서 관리합니다.
        </div>
        <ProductCatalogPanel
          products={[
            ...(constants.products || []),
            ...(products || []).map(p => typeof p === "string" ? p : p.product).filter(Boolean),
            ...Object.keys(productContacts || {}),
          ]}
          canEdit={isAdmin}
          onAdd={(product) => addCatalogProduct(product, {
            onAdded: (_product, d) => setConstants(c => ({ ...c, products: d.products || c.products })),
          })}
          onDelete={(product) => postJson(API + "/products/delete", { product })
            .then(d => setConstants(c => ({ ...c, products: d.products || c.products })))
            .catch(e => alert("제품 삭제 실패: " + (e.message || e)))}
        />
        <div style={{ marginTop: 14, paddingTop: 10, borderTop: "1px dashed var(--border)" }}>
          <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 8 }}>
            모듈 표시 순서를 관리합니다.
          </div>
          {!modDraft && (
            <button onClick={() => setModDraft([...(constants.modules || [])])} disabled={!isAdmin}
              style={{ padding: "8px 14px", borderRadius: 8, border: "1px solid var(--accent)", background: "transparent", color: "var(--accent)", fontSize: 14, cursor: "pointer", fontWeight: 700 }}>
              모듈 순서 편집 ({(constants.modules || []).length})
            </button>
          )}
          {modDraft && (
            <div>
              <div style={{ maxHeight: 260, overflowY: "auto", border: "1px solid var(--border)", borderRadius: 8 }}>
                {modDraft.map((m, i) => (
                  <div key={m + i} style={{ display: "flex", alignItems: "center", gap: 6, padding: "5px 8px", borderBottom: "1px solid var(--border)", fontSize: 14, fontFamily: "monospace" }}>
                    <span style={{ width: 20, color: "var(--text-secondary)" }}>{i + 1}</span>
                    <span style={{ flex: 1 }}>{m}</span>
                    <button onClick={() => moveMod(i, -1)} style={{ padding: "1px 6px", fontSize: 14, border: "1px solid var(--border)", background: "transparent", color: "var(--text-primary)", borderRadius: 6, cursor: "pointer" }}>↑</button>
                    <button onClick={() => moveMod(i, 1)} style={{ padding: "1px 6px", fontSize: 14, border: "1px solid var(--border)", background: "transparent", color: "var(--text-primary)", borderRadius: 6, cursor: "pointer" }}>↓</button>
                    <button onClick={() => setModDraft(modDraft.filter((_, j) => j !== i))} style={{ padding: "1px 6px", fontSize: 14, border: `1px solid ${BAD.fg}`, background: "transparent", color: BAD.fg, borderRadius: 6, cursor: "pointer" }}>×</button>
                  </div>
                ))}
              </div>
              <div style={{ display: "flex", gap: 6, marginTop: 8, flexWrap: "wrap" }}>
                <input value={modNewName} onChange={e => setModNewName(e.target.value)}
                  placeholder="새 모듈 이름"
                  style={inputStyle({ flex: 1, minWidth: 120 })}
                  onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); addPendingMod(); } }} />
                <button onClick={addPendingMod} title="모듈 추가"
                  style={{ padding: "5px 10px", borderRadius: 8, border: "1px solid var(--accent)", background: "transparent", color: "var(--accent)", fontSize: 14, fontWeight: 700, cursor: "pointer" }}>+</button>
                <button onClick={saveModuleOrder} style={{ padding: "5px 12px", borderRadius: 8, border: "none", background: "var(--accent)", color: "#fff", fontSize: 14, fontWeight: 700, cursor: "pointer" }}>저장</button>
                <button onClick={() => { setModDraft(null); setModNewName(""); }} style={{ padding: "5px 12px", borderRadius: 8, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", fontSize: 14, cursor: "pointer" }}>취소</button>
              </div>
            </div>
          )}
        </div>
        {isAdmin && <UserModulePermsPanel allModules={constants.modules || []} />}
        {isAdmin && (
          <ReasonTemplatesPanel
            reasons={constants.reasons || []}
            templates={constants.reason_templates || {}}
            onSave={(rt) => {
              sf(API + "/config", { method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ reason_templates: rt }) })
                .then(d => setConstants(c => ({ ...c, reason_templates: (d.config && d.config.reason_templates) || rt })))
                .catch(e => alert("저장 실패: " + e.message));
            }}
          />
        )}
      </PageGear>

      <header style={{ flex: "0 0 auto", display: "flex", alignItems: "center", gap: 12, padding: "12px 16px 8px", borderBottom: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
        <div style={{ display: "inline-flex", alignItems: "center", gap: 4, padding: 3, borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg-primary)" }}>
          {INFORM_TABS.map(([key, label]) => (
            <button key={key} type="button" onClick={() => setActiveTab(key)}
              style={{
                minWidth: 82,
                height: 34,
                borderRadius: 7,
                border: "1px solid " + (activeTab === key ? "var(--accent)" : "transparent"),
                background: activeTab === key ? "var(--accent)" : "transparent",
                color: activeTab === key ? "#fff" : "var(--text-secondary)",
                fontSize: 14,
                fontWeight: 900,
                cursor: "pointer",
              }}>
              {label}
            </button>
          ))}
        </div>
        {!myMods.all_rounder && (
          <div style={{ color: "var(--text-secondary)", fontSize: 14, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            내 담당: {(myMods.modules || []).length ? (myMods.modules || []).join(", ") : "미지정"}
          </div>
        )}
        <button type="button" onClick={openCreateWizard} title="신규 인폼 등록"
          style={{ marginLeft: "auto", width: 36, height: 36, borderRadius: 8, border: "none", background: "var(--accent)", color: "#fff", fontSize: 22, fontWeight: 900, cursor: "pointer", lineHeight: 1 }}>
          +
        </button>
      </header>

      <CommonInformFilters
        tab={activeTab}
        filters={sharedFilters}
        setFilters={setSharedFilters}
        products={commonProductOptions}
        modules={constants.modules || []}
      />

      <main style={{ flex: 1, minHeight: 0, overflow: "hidden", display: "flex", flexDirection: "column", background: "var(--bg-primary)" }}>
        {activeTab === "inform" && (
          <section style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
            <div style={{ flex: "0 0 auto", padding: "9px 16px", display: "flex", alignItems: "center", gap: 8, borderBottom: "1px solid var(--border)", color: "var(--text-secondary)", background: "var(--bg-secondary)" }}>
              <span>{filteredListRoots.length}건 표시 / 전체 {listRoots.length}건</span>
              <span style={{ marginLeft: "auto", display: "inline-flex", border: "1px solid var(--border)", borderRadius: 8, overflow: "hidden" }}>
                {["table", "card"].map(modeKey => (
                  <button key={modeKey} type="button" onClick={() => setInformViewMode(modeKey)}
                    style={{ padding: "5px 10px", border: "none", borderRight: modeKey === "table" ? "1px solid var(--border)" : "none", background: informViewMode === modeKey ? "var(--accent)" : "var(--bg-primary)", color: informViewMode === modeKey ? "#fff" : "var(--text-secondary)", fontWeight: 800, cursor: "pointer", fontSize: 14 }}>
                    {modeKey === "table" ? "표" : "카드"}
                  </button>
                ))}
              </span>
            </div>
            {informViewMode === "card" ? (
              <InformCardList roots={filteredListRoots} selectedId={selectedRootId} onOpen={openRootForDetail} />
            ) : (
              <InformVirtualList roots={filteredListRoots} selectedId={selectedRootId} onOpen={openRootForDetail} />
            )}
          </section>
        )}
        {activeTab === "audit" && (
          <AuditLogList rows={auditRows} loading={auditLoading} onOpen={openAuditRow} />
        )}
        {activeTab === "matrix" && (
          <LotProgressMatrix
            matrix={filteredLotMatrix}
            loading={lotMatrixLoading}
            filters={{ states: [] }}
            setFilters={() => {}}
            productOptions={matrixProductOptions}
            onOpenCell={openLotMatrixCell}
            onPickLot={filterListByMatrixLot}
            onOpenLot={openLotForStrip}
            activeModules={sharedFilters.modules || []}
            onToggleModule={toggleSharedModule}
            showControls={false}
          />
        )}
        {activeTab === "modules" && (
          <ModuleSummaryTab
            rows={filteredModuleSummary}
            onPick={(module) => {
              setSharedFilters(f => ({ ...f, modules: [module] }));
              setActiveTab("inform");
            }}
          />
        )}
      </main>

      {(selectedRoot || drawerMode === "cell" || drawerMode === "lot") && (
        <InformDrawer onClose={() => { setSelectedRootId(""); setDrawerMode("detail"); setDrawerBackMode(""); setCellDrawer(null); setLotDrawer(null); }}>
          {drawerMode === "cell" && (
            <CellInformStackPane
              context={cellDrawer}
              onOpenDetail={(root) => openRootFromDrawerStack(root, "cell")}
            />
          )}
          {drawerMode === "lot" && (
            <LotInformStripPane
              context={lotDrawer}
              activeModule={lotDrawerModule}
              setActiveModule={setLotDrawerModule}
              onOpenDetail={(root) => openRootFromDrawerStack(root, "lot")}
            />
          )}
          {drawerMode === "detail" && selectedRoot && (
            <>
              {drawerBackMode && (
                <button type="button" onClick={() => { setDrawerMode(drawerBackMode); setSelectedRootId(""); setDrawerBackMode(""); }}
                  style={{ flex: "0 0 auto", alignSelf: "flex-start", margin: "10px 12px 0", padding: "5px 9px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg-secondary)", color: "var(--text-secondary)", cursor: "pointer", fontWeight: 800, fontSize: 14 }}>
                  ← 뒤로
                </button>
              )}
              <InformDetailPane
                root={selectedRoot}
                thread={thread}
                childrenByParent={childrenByParent}
                constants={constants}
                user={user}
                tab={detailTab}
                setTab={setDetailTab}
                onReply={reply}
                onDelete={del}
                onToggleCheck={toggleCheck}
                onEdit={editInform}
                onChangeStatus={changeStatus}
                onOpenMail={(root) => setMailDialogRoot(root)}
              />
            </>
          )}
        </InformDrawer>
      )}

      {creating && (
        <InformWizard
          form={form}
          setForm={setForm}
          constants={constants}
          products={[...(constants.products || []), ...listProductOptions]}
          lotOptions={lotOptions}
          step={wizardStep}
          setStep={setWizardStep}
          attachMode={wizardAttachMode}
          setAttachMode={setWizardAttachMode}
          embedFetching={embedFetching}
          embedSchemaCols={embedSchemaCols}
          embedCustomCols={embedCustomCols}
          setEmbedCustomCols={setEmbedCustomCols}
          embedCustomSearch={embedCustomSearch}
          setEmbedCustomSearch={setEmbedCustomSearch}
          setSnapshotTick={setSnapshotTick}
          mailDraft={wizardMailDraft}
          setMailDraft={setWizardMailDraft}
          mailMeta={wizardMailMeta}
          msg={msg}
          setMsg={setMsg}
          onSubmit={() => create().catch(() => {})}
          onClose={() => { setCreating(false); setMsg(""); }}
        />
      )}
      {mailDialogRoot && <MailDialog root={mailDialogRoot} user={user} reasonTemplates={constants.reason_templates || {}} onClose={() => setMailDialogRoot(null)} />}
    </div>
  );

}

/* v8.7.9 — 시간순 이력 타임라인 (간트바 대신).
   각 줄: [시각] 모듈 [사유] LOT · 이벤트타입 · 요약 · 작성자.
   이벤트: 인폼 등록 / 담당자 확인 / 메일 발송 / 댓글 / 수정(status_history 기타).
*/
function TimelineLog({ thread, onOpen }) {
  // v8.8.0: Lot 검색 필터 + root_lot prefix 매칭.
  const [lotQ, setLotQ] = useState("");
  const events = useMemo(() => {
    const evs = [];
    for (const x of (thread || [])) {
      const isRoot = !x.parent_id;
      // 1) 등록(=루트) 또는 댓글(=비루트 첫 등장)
      evs.push({
        at: x.created_at || "",
        actor: x.author || "",
        kind: isRoot ? "인폼" : "댓글",
        module: x.module || "",
        reason: x.reason || "",
        lot: informLotDisplay(x),
        lotSearch: lotSearchText(x),
        product: stripMlPrefix(x.product || ""),
        summary: (x.text || "").slice(0, 80),
        node: x,
      });
      // 2) 상태 이력 — received(최초) 는 등록 이벤트가 이미 담당하므로 skip.
      //    v8.8.2: prev=completed → received 뿐 아니라 note/직전상태 trail 로도
      //    "확인 취소" 인식. 최초 등록 received 는 중복 피하려 한 번만 skip.
      let seenFirstReceived = false;
      let prevStat = "";
      for (const h of (x.status_history || [])) {
        if (!h || !h.at) continue;
        const hPrev = h.prev ?? prevStat;  // v8.8.2: prev 누락 시 walking 으로 복원
        const noteStr = h.note || "";
        const isInitial = (h.status === "received") && !seenFirstReceived
          && (noteStr === "created" || noteStr === "auto from SplitTable" || (!noteStr && hPrev === ""));
        if (isInitial) { seenFirstReceived = true; prevStat = h.status || prevStat; continue; }
        const isUnconfirm = h.status === "received" && (
          hPrev === "completed"
          || noteStr.includes("확인 취소")
          || noteStr.includes("완료 해제")
          || noteStr.includes("취소")
          || noteStr.includes("해제")
        );
        evs.push({
          at: h.at,
          actor: h.actor || "",
          kind: h.status === "completed"
            ? "담당자확인"
            : (isUnconfirm ? "확인취소" : `상태:${h.status || "-"}`),
          module: x.module || "",
          reason: x.reason || "",
          lot: informLotDisplay(x),
          lotSearch: lotSearchText(x),
          product: stripMlPrefix(x.product || ""),
          summary: noteStr || (isUnconfirm ? "완료 해제" : ""),
          node: x,
        });
        prevStat = h.status || prevStat;
      }
      // 3) 체크(구형) — status_history 에 잡히지 않은 경우 보완.
      if (x.checked && x.checked_at) {
        evs.push({
          at: x.checked_at,
          actor: x.checked_by || "",
          kind: "체크",
          module: x.module || "",
          reason: x.reason || "",
          lot: informLotDisplay(x),
          lotSearch: lotSearchText(x),
          product: stripMlPrefix(x.product || ""),
          summary: "",
          node: x,
        });
      }
      // 4) 메일 발송 이력
      for (const m of (x.mail_history || [])) {
        const at = m.at || m.sent_at || m.time || "";
        if (!at) continue;
        evs.push({
          at, actor: m.actor || m.sender || "",
          kind: "메일",
          module: x.module || "",
          reason: x.reason || "",
          lot: informLotDisplay(x),
          lotSearch: lotSearchText(x),
          product: stripMlPrefix(x.product || ""),
          summary: m.subject ? `[${m.subject}]` : (m.to ? `→ ${Array.isArray(m.to) ? m.to.join(", ") : m.to}` : ""),
          node: x,
        });
      }
    }
    // v8.8.13: 최신이 위로 — 내림차순 정렬.
    evs.sort((a, b) => (b.at || "").localeCompare(a.at || ""));
    return evs;
  }, [thread]);

  const filtered = useMemo(() => {
    const q = (lotQ || "").trim().toLowerCase();
    if (!q) return events;
    return events.filter(e => {
      const hay = [e.lot, e.lotSearch, e.node?.root_lot_id, e.node?.fab_lot_id_at_save]
        .filter(Boolean).join(" ").toLowerCase();
      return hay.includes(q);
    });
  }, [events, lotQ]);

  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 8, background: "var(--bg-secondary)", padding: 10, fontFamily: "monospace" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6, flexWrap: "wrap" }}>
        <span style={{ fontSize: 14, fontWeight: 700, color: "var(--accent)" }}>📜 이력 타임라인 ({filtered.length}{lotQ ? ` / ${events.length}` : ""}건)</span>
        <input value={lotQ} onChange={e => setLotQ(e.target.value)}
          placeholder="🔎 Lot 검색 (root_lot_id 또는 fab_lot_id 부분일치)"
          style={{ flex: 1, minWidth: 220, padding: "5px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14, fontFamily: "monospace" }} />
        {lotQ && <span onClick={() => setLotQ("")} style={{ cursor: "pointer", color: "#ef4444", fontSize: 14 }}>✕ 초기화</span>}
      </div>
      <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 8 }}>작성 / 수정 / 이행(확인·완료) — 누가 언제 무엇을 했는지 시간순. Lot 입력 시 해당 Lot 만 필터링.</div>
      {filtered.length === 0 && <div style={{ padding: 40, textAlign: "center", color: "var(--text-secondary)" }}>{lotQ ? `'${lotQ}' 매칭 이력 없음.` : "이력 없음."}</div>}
      {filtered.map((e, i) => {
        const mc = moduleColor(e.module);
        const kindColor = e.kind === "인폼" ? "#3b82f6"
          : e.kind === "담당자확인" ? "#22c55e"
          : e.kind === "확인취소" ? "#ef4444"
          : e.kind === "메일" ? "#f59e0b"
          : e.kind === "댓글" ? "#8b5cf6"
          : e.kind === "체크" ? "#14b8a6"
          : "#64748b";
        const lotLabel = e.product && e.lot ? `[${e.product}] ${e.lot}` : (e.lot || e.product || "-");
        return (
          <div key={i} onClick={() => onOpen && onOpen(e.node)} style={{
            display: "flex", gap: 10, alignItems: "center", padding: "4px 6px",
            borderRadius: 4, cursor: "pointer", fontSize: 14, lineHeight: 1.55,
            borderLeft: `3px solid ${mc}`, marginBottom: 2, background: i % 2 ? "var(--bg-primary)" : "transparent",
          }}>
            <span style={{ color: "var(--text-secondary)", minWidth: 115 }}>{(e.at || "").replace("T", " ").slice(0, 16)}</span>
            <span style={{ minWidth: 56, color: mc, fontWeight: 700 }}>{e.module || "-"}</span>
            <span style={{ minWidth: 88, color: "var(--text-secondary)" }}>{e.reason ? `[${e.reason}]` : ""}</span>
            <span title={lotLabel} style={{ minWidth: 220, maxWidth: 360, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{lotLabel}</span>
            <span style={{ padding: "1px 8px", borderRadius: 999, background: kindColor + "22", color: kindColor, fontWeight: 700, fontSize: 14 }}>{e.kind}</span>
            <span style={{ color: "var(--text-primary)", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{e.summary}</span>
            <span style={{ color: "var(--text-secondary)", fontSize: 14 }}>· {e.actor || "-"}</span>
          </div>
        );
      })}
    </div>
  );
}

/* v8.8.13: admin 전용 — 유저별 인폼 모듈 조회 권한 편집 패널.
   PageGear 인폼 설정 하단에 표시. 체크가 하나도 없으면 "아무 모듈도 조회 못함",
   체크 해제 전 기본 상태(설정 없음)는 groups 기반으로 fallback. */
/* v8.8.17: ReasonTemplatesPanel — admin 이 사유별로 메일 제목 + 본문 템플릿을 편집.
   PageGear 안에서 사유 목록을 순회하며 제목/본문 2필드를 보여준다.
   저장은 단건이 아니라 템플릿 맵 전체를 일괄 PATCH 로 POST /api/informs/config. */
function ReasonTemplatesPanel({ reasons, templates, onSave }) {
  const [draft, setDraft] = React.useState(() => ({ ...(templates || {}) }));
  const [open, setOpen] = React.useState(false);
  const [active, setActive] = React.useState("");
  React.useEffect(() => { setDraft({ ...(templates || {}) }); }, [templates]);
  const setField = (reason, field, v) => {
    setDraft(d => ({ ...d, [reason]: { ...(d[reason] || { subject: "", body: "" }), [field]: v } }));
  };
  if (!open) {
    const count = Object.values(draft || {}).filter(v => v && (v.subject || v.body)).length;
    return (
      <div style={{ marginTop: 14, paddingTop: 10, borderTop: "1px dashed var(--border)" }}>
        <button onClick={() => setOpen(true)}
          style={{ padding: "8px 14px", borderRadius: 6, border: "1px solid var(--accent)", background: "transparent", color: "var(--accent)", fontSize: 14, cursor: "pointer", fontWeight: 600 }}>
          ✉ 사유별 메일 템플릿 편집 ({count}/{(reasons || []).length})
        </button>
      </div>
    );
  }
  return (
    <div style={{ marginTop: 14, paddingTop: 10, borderTop: "1px dashed var(--border)" }}>
      <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 6 }}>
        사유 선택 시 자동으로 채워지는 메일 제목/본문 템플릿. 저장 후 등록 폼에서 사유를 고르면 적용됩니다.
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 6 }}>
        {(reasons || []).map(r => {
          const has = draft[r] && (draft[r].subject || draft[r].body);
          return (
            <span key={r} onClick={() => setActive(r)}
              style={{ padding: "3px 8px", borderRadius: 999, cursor: "pointer", fontSize: 14,
                background: active === r ? "var(--accent)" : (has ? "var(--accent-glow)" : "var(--bg-card)"),
                color: active === r ? "#fff" : (has ? "var(--accent)" : "var(--text-primary)"),
                border: "1px solid " + (active === r ? "var(--accent)" : "var(--border)") }}>
              {has ? "● " : ""}{r}
            </span>
          );
        })}
      </div>
      {active && (
        <div style={{ padding: 8, border: "1px solid var(--border)", borderRadius: 5, background: "var(--bg-card)" }}>
          <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 2 }}>제목 템플릿</div>
          <input value={(draft[active] || {}).subject || ""}
            onChange={e => setField(active, "subject", e.target.value)}
            placeholder="[인폼·장비이상] {product} · {lot}"
            style={{ width: "100%", padding: "4px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14, boxSizing: "border-box", marginBottom: 6 }} />
          <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 2 }}>본문 템플릿</div>
          <textarea value={(draft[active] || {}).body || ""}
            onChange={e => setField(active, "body", e.target.value)}
            placeholder="배경:&#10;영향:&#10;조치 요청:"
            rows={6}
            style={{ width: "100%", padding: 6, borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14, boxSizing: "border-box", fontFamily: "inherit", lineHeight: 1.4 }} />
          <div style={{ fontSize: 14, color: "var(--text-secondary)", marginTop: 4 }}>
            변수 참고: <code>{"{product}"}</code> <code>{"{lot}"}</code> <code>{"{module}"}</code> <code>{"{reason}"}</code> — 현재 폼에 자동 치환.
          </div>
        </div>
      )}
      <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
        <button onClick={() => onSave(draft)}
          style={{ padding: "5px 12px", borderRadius: 4, border: "none", background: "var(--accent)", color: "#fff", fontSize: 14, fontWeight: 600, cursor: "pointer" }}>저장</button>
        <button onClick={() => setOpen(false)}
          style={{ padding: "5px 12px", borderRadius: 4, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", fontSize: 14, cursor: "pointer" }}>닫기</button>
      </div>
    </div>
  );
}


function UserModulePermsPanel({ allModules }) {
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(false);
  const [savingFor, setSavingFor] = useState("");
  const [q, setQ] = useState("");
  const load = () => {
    setLoading(true);
    sf("/api/informs/user-modules").then(d => {
      setUsers(d.users || []); setLoading(false);
    }).catch(e => { setLoading(false); alert("로드 실패: " + (e.message || e)); });
  };
  useEffect(() => { load(); }, []);
  const toggleOne = (username, module, on) => {
    const u = users.find(x => x.username === username); if (!u) return;
    const next = on
      ? [...(u.modules || []), module]
      : (u.modules || []).filter(m => m !== module);
    persist(username, next);
  };
  const setAllFor = (username, on) => persist(username, on ? [...allModules] : []);
  const clearFor = (username) => {
    setSavingFor(username);
    sf("/api/informs/user-modules/clear", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, modules: [] }) })
      .then(() => { setSavingFor(""); load(); })
      .catch(e => { setSavingFor(""); alert("초기화 실패: " + (e.message || e)); });
  };
  const persist = (username, modules) => {
    setSavingFor(username);
    // optimistic
    setUsers(list => list.map(u => u.username === username ? { ...u, modules, has_setting: true } : u));
    sf("/api/informs/user-modules/save", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, modules }) })
      .then(() => setSavingFor(""))
      .catch(e => { setSavingFor(""); alert("저장 실패: " + (e.message || e)); load(); });
  };
  const filtered = q ? users.filter(u => (u.username || "").toLowerCase().includes(q.toLowerCase()) || (u.email || "").toLowerCase().includes(q.toLowerCase())) : users;
  return (
    <div style={{ marginTop: 14, paddingTop: 10, borderTop: "1px dashed var(--border)" }}>
      <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 4 }}>🔒 유저별 모듈 조회 권한</div>
      <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 6, lineHeight: 1.5 }}>
        인폼 탭 권한이 있는 유저에게 <b>모듈별 조회 권한</b> 을 부여합니다.
        체크된 모듈의 인폼만 목록·검색에 노출됩니다. admin 은 항상 전체. 설정을 초기화하면 그룹 기반으로 돌아갑니다.
      </div>
      <input value={q} onChange={e => setQ(e.target.value)} placeholder="🔎 유저/이메일 검색"
        style={{ width: "100%", padding: "4px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14, marginBottom: 6, boxSizing: "border-box" }} />
      {loading && <div style={{ padding: 10, fontSize: 14, color: "var(--text-secondary)" }}>로딩...</div>}
      {!loading && filtered.length === 0 && <div style={{ padding: 10, fontSize: 14, color: "var(--text-secondary)" }}>해당 유저 없음</div>}
      <div style={{ maxHeight: 380, overflow: "auto", border: "1px solid var(--border)", borderRadius: 4 }}>
        {filtered.map(u => {
          const modsSet = new Set(u.modules || []);
          const allOn = allModules.length > 0 && allModules.every(m => modsSet.has(m));
          const busy = savingFor === u.username;
          return (
            <div key={u.username} style={{ padding: "6px 8px", borderBottom: "1px solid var(--border)", opacity: busy ? 0.6 : 1 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4, flexWrap: "wrap" }}>
                <span style={{ fontSize: 14, fontWeight: 700, fontFamily: "monospace" }}>{u.username}</span>
                {u.role === "admin" && <span style={{ fontSize: 14, padding: "1px 5px", borderRadius: 8, background: "#ef444422", color: "#ef4444", fontWeight: 700 }}>admin</span>}
                {u.email && <span style={{ fontSize: 14, color: "var(--text-secondary)", fontFamily: "monospace" }}>{u.email}</span>}
                <span style={{ flex: 1 }} />
                <span style={{ fontSize: 14, color: u.has_setting ? "#16a34a" : "var(--text-secondary)" }}>
                  {u.has_setting ? `✓ 설정됨 (${(u.modules || []).length})` : "기본(그룹 기반)"}
                </span>
                <span onClick={() => setAllFor(u.username, !allOn)}
                  style={{ fontSize: 14, padding: "1px 6px", borderRadius: 4, cursor: "pointer", border: "1px solid var(--border)", color: "var(--accent)" }}>
                  {allOn ? "전체 해제" : "전체 선택"}
                </span>
                {u.has_setting && <span onClick={() => clearFor(u.username)}
                  style={{ fontSize: 14, padding: "1px 6px", borderRadius: 4, cursor: "pointer", border: "1px solid #ef4444", color: "#ef4444" }}
                  title="이 유저의 권한 설정을 초기화 (groups 기반으로 복귀)">× 초기화</span>}
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                {allModules.map(m => {
                  const on = modsSet.has(m);
                  const mc = moduleColor(m);
                  return (
                    <label key={m}
                      style={{ fontSize: 14, padding: "1px 7px", borderRadius: 999, cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 3,
                        background: on ? (mc + "22") : "transparent",
                        color: on ? mc : "var(--text-secondary)",
                        fontWeight: on ? 700 : 500,
                        border: "1px solid " + (on ? (mc + "55") : "var(--border)") }}>
                      <input type="checkbox" checked={on} onChange={() => toggleOne(u.username, m, !on)} style={{ accentColor: mc }} />
                      {m}
                    </label>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* 요약 카드 (all/mine/product 모드에서 루트 리스트용) */
function CompactRow({ root, onOpen }) {
  // v8.8.8: 이 root 인폼의 (product, root_lot_id) 에 달린 SplitTable notes(꼬리표) 를 요약 배지로 표시.
  //   wafer 태그 / param 메모 / LOT 노트 / global 태그 각 카운트. 클릭 시 상세는 별도 동작 없이 hover tooltip 으로.
  const [tagSummary, setTagSummary] = useState(null);
  useEffect(() => {
    const prod = root.product || "";
    const rawLot = String(root.lot_id || "").trim();
    const rlot = root.root_lot_id || (isFabLotInput(rawLot) ? rawLot.slice(0, 5) : rawLot);
    if (!prod || !rlot) { setTagSummary(null); return; }
    let alive = true;
    sf(`/api/splittable/notes?product=${encodeURIComponent(prod)}&root_lot_id=${encodeURIComponent(rlot)}`)
      .then(d => {
        if (!alive) return;
        const ns = d.notes || [];
        const by = { wafer: 0, param: 0, lot: 0, param_global: 0 };
        ns.forEach(n => { if (by[n.scope] != null) by[n.scope]++; });
        const total = ns.length;
        setTagSummary(total > 0 ? { total, by, sample: ns.slice(0, 3).map(n => n.text) } : null);
      })
      .catch(() => {});
    return () => { alive = false; };
  }, [root.product, root.root_lot_id, root.lot_id]);

  const hasModule = !!(root.module && String(root.module).trim());
  const modLabel = hasModule ? root.module : "미정";
  const reasonLabel = (root.reason || "").trim();
  const rootLotLabel = root.lot_id || root.wafer_id || "-";
  const fabLots = splitFabLotsFromNode(root).filter(v => v !== rootLotLabel);
  const fabLotLabel = fabLots.join(" / ");
  const text = String(root.text || "").trim();
  const headline = text.split(/\n+/).find(Boolean) || "(내용 없음)";
  const summary = text && text !== headline ? text.replace(headline, "").trim() : "";
  const metaBadge = {
    display: "inline-flex",
    alignItems: "center",
    minHeight: 22,
    padding: "2px 8px",
    borderRadius: 6,
    border: "1px solid var(--border)",
    background: "var(--bg-tertiary)",
    color: "var(--text-secondary)",
    fontSize: 14,
    fontWeight: 700,
    fontFamily: "monospace",
    maxWidth: 280,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };

  return (
    <div onClick={onOpen}
      style={{
        padding: 14,
        marginBottom: 10,
        borderRadius: 8,
        border: "1px solid var(--border)",
        background: "var(--bg-secondary)",
        cursor: "pointer",
        boxShadow: "0 1px 2px rgba(15,23,42,0.04)",
      }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <span style={{ ...metaBadge, color: "var(--accent)", background: "var(--accent-glow)", borderColor: "rgba(249,115,22,0.24)" }}>
          {stripMlPrefix(root.product || "제품 미정")}
        </span>
        <span style={{ fontSize: 14, color: "var(--text-secondary)" }}>{(root.created_at || "").replace("T", " ").slice(0, 16)}</span>
        <span style={{ fontSize: 14, color: "var(--text-secondary)", fontWeight: 700 }}>{root.author || "-"}</span>
        <div style={{ flex: 1 }} />
        <div style={{ display: "inline-flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
          <CheckPill node={root} />
          <AutoGenPill node={root} />
          {(root.images && root.images.length > 0) && <span title="이미지 첨부" style={{ fontSize: 14 }}>📎{root.images.length}</span>}
          {root.embed_table && <span title="SplitTable 스냅샷 첨부" style={{ fontSize: 14 }}>🔗</span>}
          {tagSummary && (
            <span title={`이 lot 의 꼬리표 ${tagSummary.total}개 (wafer ${tagSummary.by.wafer} · param ${tagSummary.by.param} · lot ${tagSummary.by.lot} · global ${tagSummary.by.param_global})\n샘플: ${tagSummary.sample.join(" / ")}`}
                  style={{ fontSize: 14, padding: "2px 8px", borderRadius: 6, background: INFO.bg, color: INFO.fg, border: "1px solid " + INFO.fg + "33", fontWeight: 700, cursor: "help" }}>
              🏷 {tagSummary.total}
            </span>
          )}
        </div>
      </div>

      <div style={{ marginTop: 10 }}>
        <div style={{
          fontSize: 14,
          lineHeight: 1.45,
          fontWeight: 900,
          color: "var(--text-primary)",
          whiteSpace: "pre-wrap",
          display: "-webkit-box",
          WebkitLineClamp: 2,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
        }}>
          {headline}
        </div>
        {summary && (
          <div style={{
            marginTop: 4,
            fontSize: 14,
            lineHeight: 1.5,
            color: "var(--text-secondary)",
            whiteSpace: "pre-wrap",
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}>
            {summary}
          </div>
        )}
      </div>

      <div style={{ marginTop: 10, display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap", maxHeight: 58, overflow: "hidden" }}>
        <span style={metaBadge}>lot:{rootLotLabel}</span>
        {root.root_lot_id && root.lot_id && root.root_lot_id !== root.lot_id && (
          <span title="root_lot_id" style={metaBadge}>root:{root.root_lot_id}</span>
        )}
        {fabLots.length > 0 && (
          <span title={`SplitTable fab_lot_id: ${fabLotLabel}`} style={metaBadge}>
            fab:{fabLotLabel}
          </span>
        )}
        <span style={metaBadge}>module:{modLabel}</span>
        {reasonLabel && <span title={reasonLabel} style={metaBadge}>reason:{reasonLabel}</span>}
      </div>
    </div>
  );
}

function ModulePill({ module }) {
  const mc = moduleColor(module || "기타");
  return (
    <span title={module || "기타"} style={{
      display: "inline-flex", alignItems: "center", maxWidth: 96,
      padding: "2px 7px", borderRadius: 999,
      background: mc + "18", color: mc, border: "1px solid " + mc + "44",
      fontSize: 14, fontWeight: 800, fontFamily: "monospace",
      overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
    }}>{module || "기타"}</span>
  );
}

function LotPill({ root }) {
  const label = informLotDisplay(root, { maxFabLots: 2 }) || "-";
  return (
    <span title={label} style={{
      display: "inline-flex", alignItems: "center", minWidth: 0, maxWidth: 160,
      padding: "2px 7px", borderRadius: 999,
      background: "var(--bg-tertiary)", color: "var(--text-secondary)",
      border: "1px solid var(--border)", fontSize: 14, fontFamily: "monospace",
      overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
    }}>{label}</span>
  );
}

function CommonInformFilters({ tab, filters, setFilters, products, modules }) {
  const [lotDraft, setLotDraft] = useState(filters.lot || "");
  useEffect(() => setLotDraft(filters.lot || ""), [filters.lot]);
  useEffect(() => {
    const h = setTimeout(() => {
      setFilters(f => f.lot === lotDraft ? f : { ...f, lot: lotDraft });
    }, 250);
    return () => clearTimeout(h);
  }, [lotDraft]);

  const update = (patch) => setFilters(f => ({ ...f, ...patch }));
  const addToken = (key, value) => {
    const v = String(value || "").trim();
    if (!v) return;
    setFilters(f => ({ ...f, [key]: uniqueClean([...(f[key] || []), v]) }));
  };
  const removeToken = (key, value) => {
    setFilters(f => ({ ...f, [key]: (f[key] || []).filter(x => x !== value) }));
  };
  const reset = () => setFilters(DEFAULT_SHARED_FILTERS);
  const statusOptions = [
    ["received", "접수"],
    ["in_progress", "진행중"],
    ["completed", "완료"],
    ["pending", "대기"],
  ];
  const showStatus = tab === "inform" || tab === "matrix";
  const showModules = tab !== "modules";
  const showProducts = tab !== "modules";
  const showTypes = tab === "audit";
  const pickerStyle = inputStyle({ width: 148, fontSize: 13, padding: "6px 8px" });
  return (
    <div style={{ flex: "0 0 auto", padding: "10px 16px", borderBottom: "1px solid var(--border)", background: "var(--bg-secondary)", display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
      {showProducts && (
        <select value="" onChange={e => addToken("products", e.target.value)} style={pickerStyle}>
          <option value="">제품 추가</option>
          {(products || []).map(p => <option key={p} value={p}>{p}</option>)}
        </select>
      )}
      {showModules && (
        <select value="" onChange={e => addToken("modules", e.target.value)} style={pickerStyle}>
          <option value="">모듈 추가</option>
          {(modules || []).map(m => <option key={m} value={m}>{m}</option>)}
        </select>
      )}
      {showStatus && (
        <select value="" onChange={e => addToken("statuses", e.target.value)} style={pickerStyle}>
          <option value="">상태 추가</option>
          {statusOptions.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
        </select>
      )}
      {showTypes && (
        <select value="" onChange={e => addToken("types", e.target.value)} style={pickerStyle}>
          <option value="">유형 추가</option>
          {AUDIT_TYPES.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
        </select>
      )}
      {tab !== "modules" && (
        <input value={lotDraft} onChange={e => setLotDraft(e.target.value)}
          placeholder="lot 검색"
          style={inputStyle({ width: 180, fontSize: 13, padding: "6px 8px", fontFamily: "monospace" })} />
      )}
      <div style={{ display: "inline-flex", gap: 4, alignItems: "center", border: "1px solid var(--border)", borderRadius: 8, padding: 2, background: "var(--bg-primary)" }}>
        {["7", "30", "90", "custom"].map(v => (
          <button key={v} type="button" onClick={() => update({ period: v })}
            style={{ padding: "5px 8px", borderRadius: 6, border: "none", background: filters.period === v ? "var(--accent)" : "transparent", color: filters.period === v ? "#fff" : "var(--text-secondary)", fontWeight: 800, cursor: "pointer", fontSize: 13 }}>
            {v === "custom" ? "직접" : `${v}일`}
          </button>
        ))}
      </div>
      {filters.period === "custom" && (
        <>
          <input type="date" value={filters.start || ""} onChange={e => update({ start: e.target.value })} style={inputStyle({ width: 138, fontSize: 13, padding: "6px 8px" })} />
          <input type="date" value={filters.end || ""} onChange={e => update({ end: e.target.value })} style={inputStyle({ width: 138, fontSize: 13, padding: "6px 8px" })} />
        </>
      )}
      <div style={{ display: "flex", gap: 5, flexWrap: "wrap", minWidth: 0 }}>
        {(filters.products || []).map(v => <FilterChip key={`p:${v}`} label={`제품 ${v}`} onRemove={() => removeToken("products", v)} />)}
        {(filters.modules || []).map(v => <FilterChip key={`m:${v}`} label={`모듈 ${v}`} onRemove={() => removeToken("modules", v)} />)}
        {(filters.statuses || []).map(v => <FilterChip key={`s:${v}`} label={`상태 ${(statusOptions.find(x => x[0] === v) || [v, v])[1]}`} onRemove={() => removeToken("statuses", v)} />)}
        {(filters.types || []).map(v => <FilterChip key={`t:${v}`} label={`유형 ${(AUDIT_TYPES.find(x => x[0] === v) || [v, v])[1]}`} onRemove={() => removeToken("types", v)} />)}
        {filters.lot && <FilterChip label={`lot ${filters.lot}`} onRemove={() => { setLotDraft(""); update({ lot: "" }); }} />}
      </div>
      <button type="button" onClick={reset} title="필터 초기화"
        style={{ marginLeft: "auto", padding: "6px 10px", borderRadius: 8, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", cursor: "pointer", fontWeight: 800, fontSize: 14 }}>
        필터 초기화
      </button>
    </div>
  );
}

function FilterChip({ label, onRemove }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 5, minHeight: 27, maxWidth: 220, padding: "3px 7px", borderRadius: 999, border: "1px solid var(--border)", background: "var(--bg-tertiary)", color: "var(--text-secondary)", fontSize: 13, fontWeight: 800 }}>
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label}</span>
      <button type="button" onClick={onRemove} title="필터 해제"
        style={{ border: "none", background: "transparent", color: BAD.fg, cursor: "pointer", fontWeight: 900, padding: 0, lineHeight: 1 }}>
        x
      </button>
    </span>
  );
}

function InformDrawer({ children, onClose }) {
  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 2600, pointerEvents: "none" }}>
      <div onClick={onClose} style={{ position: "absolute", inset: 0, background: "rgba(15,23,42,0.18)", pointerEvents: "auto" }} />
      <aside style={{ position: "absolute", top: 0, right: 0, bottom: 0, width: "min(480px, 96vw)", background: "var(--bg-primary)", borderLeft: "1px solid var(--border)", boxShadow: "-14px 0 30px rgba(15,23,42,0.24)", pointerEvents: "auto", display: "flex", flexDirection: "column" }}>
        <button type="button" onClick={onClose} title="닫기"
          style={{ position: "absolute", top: 10, right: 10, zIndex: 3, width: 30, height: 30, borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg-secondary)", color: "var(--text-secondary)", cursor: "pointer", fontSize: 18 }}>
          x
        </button>
        {children}
      </aside>
    </div>
  );
}

function InformCardList({ roots, selectedId, onOpen }) {
  return (
    <div style={{ flex: 1, minHeight: 0, overflow: "auto", padding: 12, background: "var(--bg-primary)" }}>
      {roots.length === 0 && <div style={{ padding: 32, textAlign: "center", color: "var(--text-secondary)" }}>조건에 맞는 인폼이 없어요</div>}
      {roots.map(root => (
        <div key={root.id} style={{ outline: selectedId === root.id ? "2px solid var(--accent)" : "none", borderRadius: 8 }}>
          <CompactRow root={root} onOpen={() => onOpen(root)} />
        </div>
      ))}
    </div>
  );
}

function InformStackCard({ root, allInforms, onOpen }) {
  const replies = (allInforms || []).filter(x => x.parent_id === root.id).length;
  const mailCount = (root.mail_history || []).length;
  const attachCount = (root.images || []).length + (root.embed_table ? 1 : 0);
  const meta = LOT_MATRIX_STATES[root.flow_status || "received"] || LOT_MATRIX_STATES.received;
  const preview = String(root.text || "").trim().replace(/\s+/g, " ").slice(0, 80);
  return (
    <button type="button" onClick={() => onOpen(root)}
      style={{ width: "100%", padding: 10, borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg-secondary)", color: "var(--text-primary)", cursor: "pointer", textAlign: "left", display: "grid", gap: 6 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 7, minWidth: 0 }}>
        <span style={{ width: 7, height: 7, borderRadius: 999, background: meta.fg, flex: "0 0 auto" }} />
        <span style={{ color: "var(--text-secondary)", fontFamily: "monospace" }}>{String(_entryLastUpdateForUi(root) || root.created_at || "").replace("T", " ").slice(0, 16)}</span>
        <span style={{ color: "var(--text-secondary)" }}>{root.author || "-"}</span>
        <span style={{ marginLeft: "auto" }}><StatusBadge status={root.flow_status || "received"} /></span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
        <ModulePill module={root.module || "기타"} />
        {root.reason && <span style={{ color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{root.reason}</span>}
      </div>
      <div style={{ color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{preview || "(내용 없음)"}</div>
      <div style={{ display: "flex", gap: 10, color: "var(--text-secondary)", fontSize: 13 }}>
        <span>💬 {replies}</span>
        <span>✉ {mailCount}</span>
        <span>📎 {attachCount}</span>
      </div>
    </button>
  );
}

function CellInformStackPane({ context, onOpenDetail }) {
  const product = stripMlPrefix(context?.product || "");
  const lot = context?.lot?.root_lot_id || "";
  const module = context?.module || "";
  const roots = context?.roots || [];
  const count = Number(context?.cell?.inform_count ?? roots.length);
  return (
    <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
      <div style={{ flex: "0 0 auto", padding: "16px 44px 12px 16px", borderBottom: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
        <div style={{ fontSize: 18, fontWeight: 900, lineHeight: 1.3 }}>{product || "미지정"} · {lot || "-"} · {module || "기타"}</div>
        <div style={{ marginTop: 6, color: "var(--text-secondary)", fontWeight: 800 }}>{count}건 인폼</div>
      </div>
      <div style={{ flex: 1, minHeight: 0, overflow: "auto", padding: 12, display: "grid", alignContent: "start", gap: 8 }}>
        {context?.loading && <div style={{ color: "var(--text-secondary)" }}>loading...</div>}
        {!context?.loading && roots.length === 0 && <div style={{ padding: 32, textAlign: "center", color: "var(--text-secondary)" }}>이 모듈에 인폼된 기록이 없어요</div>}
        {roots.map(root => <InformStackCard key={root.id} root={root} allInforms={context?.byLot?.informs || roots} onOpen={onOpenDetail} />)}
      </div>
    </div>
  );
}

function LotInformStripPane({ context, activeModule, setActiveModule, onOpenDetail }) {
  const informs = context?.informs || [];
  const roots = informs.filter(x => !x.parent_id);
  const modules = context?.available_modules || [];
  const counts = context?.module_counts || {};
  const selectedRoots = roots
    .filter(x => !activeModule || (x.module || "기타") === activeModule)
    .sort((a, b) => String(_entryLastUpdateForUi(b)).localeCompare(String(_entryLastUpdateForUi(a))));
  const product = stripMlPrefix(context?.product || roots.find(x => x.product)?.product || "");
  const rootLot = context?.root_lot_id || context?.lot?.root_lot_id || "";
  const fabLots = uniqueClean(roots.flatMap(r => splitFabLotsFromNode(r))).filter(v => v !== rootLot);
  const informed = modules.filter(m => Number(counts[m] || 0) > 0).length;
  return (
    <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
      <div style={{ flex: "0 0 auto", padding: "16px 44px 12px 16px", borderBottom: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ fontSize: 18, fontWeight: 900 }}>{product || "미지정"}</span>
          <span style={{ fontSize: 18, fontWeight: 900, fontFamily: "monospace", color: "var(--accent)" }}>{rootLot || "-"}</span>
          <span style={{ color: "var(--text-secondary)", fontWeight: 800 }}>{informed}/{modules.length || 0} 모듈 인폼</span>
        </div>
        {fabLots.length > 0 && (
          <div style={{ marginTop: 7, display: "flex", gap: 5, flexWrap: "wrap" }}>
            {fabLots.slice(0, 8).map(v => <span key={v} style={{ padding: "2px 7px", borderRadius: 999, border: "1px solid var(--border)", background: "var(--bg-tertiary)", color: "var(--text-secondary)", fontFamily: "monospace", fontSize: 13 }}>{v}</span>)}
          </div>
        )}
      </div>
      <div style={{ flex: "0 0 auto", padding: 12, borderBottom: "1px solid var(--border)", display: "flex", gap: 6, flexWrap: "wrap", background: "var(--bg-secondary)" }}>
        <button type="button" onClick={() => setActiveModule("")}
          style={{ padding: "5px 9px", borderRadius: 999, border: "1px solid " + (!activeModule ? "var(--accent)" : "var(--border)"), background: !activeModule ? "var(--accent)" : "var(--bg-primary)", color: !activeModule ? "#fff" : "var(--text-secondary)", fontWeight: 900, cursor: "pointer" }}>
          전체 {roots.length}
        </button>
        {modules.map(module => {
          const n = Number(counts[module] || 0);
          const mc = moduleColor(module);
          const active = activeModule === module;
          return (
            <button key={module} type="button" onClick={() => setActiveModule(active ? "" : module)}
              style={{ padding: "5px 9px", borderRadius: 999, border: "1px solid " + (n ? mc : "var(--border)"), background: active ? mc : (n ? mc + "22" : "transparent"), color: active ? "#fff" : (n ? mc : "var(--text-secondary)"), fontWeight: 900, cursor: "pointer", fontFamily: "monospace" }}>
              {module} {n || "—"}
            </button>
          );
        })}
      </div>
      <div style={{ flex: 1, minHeight: 0, overflow: "auto", padding: 12, display: "grid", alignContent: "start", gap: 8 }}>
        {context?.loading && <div style={{ color: "var(--text-secondary)" }}>loading...</div>}
        {!context?.loading && selectedRoots.length === 0 && <div style={{ padding: 32, textAlign: "center", color: "var(--text-secondary)" }}>이 모듈에 인폼된 기록이 없어요</div>}
        {selectedRoots.map(root => <InformStackCard key={root.id} root={root} allInforms={informs} onOpen={onOpenDetail} />)}
      </div>
    </div>
  );
}

function AuditLogList({ rows, loading, onOpen }) {
  const typeMeta = {
    status_change: { label: "상태변경", icon: "●", color: WARN.fg },
    mail: { label: "메일", icon: "✉", color: INFO.fg },
    comment: { label: "댓글", icon: "💬", color: PURPLE.fg },
    edit: { label: "수정", icon: "✎", color: TEAL.fg },
    create: { label: "생성", icon: "+", color: OK.fg },
    delete: { label: "삭제", icon: "x", color: BAD.fg },
  };
  return (
    <div style={{ flex: 1, minHeight: 0, overflow: "auto", background: "var(--bg-secondary)" }}>
      {loading && <div style={{ padding: 16, color: "var(--text-secondary)" }}>loading...</div>}
      {!loading && rows.length === 0 && <div style={{ padding: 32, textAlign: "center", color: "var(--text-secondary)" }}>조건에 맞는 활동 로그가 없어요</div>}
      {rows.map((row, i) => {
        const meta = typeMeta[row.type] || { label: row.type || "이벤트", icon: "·", color: NEUTRAL.fg };
        const lot = [stripMlPrefix(row.product || ""), row.root_lot_id || row.lot_id || row.fab_lot_id_at_save || ""].filter(Boolean).join(" · ") || "-";
        return (
          <button key={row.id || i} type="button" onClick={() => onOpen(row)}
            style={{ width: "100%", minHeight: 52, padding: "7px 16px", border: "none", borderBottom: "1px solid var(--border)", background: "transparent", color: "var(--text-primary)", display: "grid", gridTemplateColumns: "148px 112px minmax(180px, 1fr) 116px 112px minmax(0, 1.4fr)", gap: 8, alignItems: "center", cursor: "pointer", textAlign: "left", fontSize: 14 }}>
            <span style={{ color: "var(--text-secondary)", fontFamily: "monospace" }}>{String(row.at || "").replace("T", " ").slice(0, 16)}</span>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: meta.color, fontWeight: 900 }}>
              <span>{meta.icon}</span>{meta.label}
            </span>
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontFamily: "monospace" }}>{lot}</span>
            <ModulePill module={row.module || "기타"} />
            <span style={{ color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row.actor || "-"}</span>
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row.summary || "-"}</span>
          </button>
        );
      })}
    </div>
  );
}

function ModuleSummaryTab({ rows, onPick }) {
  const list = Array.isArray(rows) ? rows : [];
  return (
    <div style={{ flex: 1, minHeight: 0, overflow: "auto", padding: 16, background: "var(--bg-primary)" }}>
      {list.length === 0 && <div style={{ padding: 32, textAlign: "center", color: "var(--text-secondary)" }}>모듈 요약이 없어요</div>}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(220px,1fr))", gap: 10 }}>
        {list.map(row => {
          const received = Number(row.received || 0);
          const completed = Number(row.completed || 0);
          const inProgress = Number(row.in_progress || 0);
          const pending = Number(row.pending || 0);
          const total = received + completed + inProgress;
          const doneRate = total ? Math.round((completed / total) * 100) : 0;
          const mc = moduleColor(row.module);
          return (
            <button key={row.module} type="button" onClick={() => onPick(row.module)}
              style={{ minHeight: 132, padding: 12, borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg-secondary)", color: "var(--text-primary)", textAlign: "left", cursor: "pointer", display: "grid", gap: 8 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ width: 8, height: 8, borderRadius: 999, background: mc }} />
                <span style={{ fontSize: 16, fontWeight: 900, fontFamily: "monospace" }}>{row.module}</span>
                <span style={{ marginLeft: "auto", color: "var(--text-secondary)", fontFamily: "monospace" }}>{doneRate}%</span>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, color: "var(--text-secondary)" }}>
                <span>받음 {received}</span>
                <span>완료 {completed}</span>
                <span>진행중 {inProgress}</span>
                <span>대기 {pending}</span>
              </div>
              <div style={{ height: 7, borderRadius: 999, background: "var(--bg-primary)", overflow: "hidden" }}>
                <div style={{ width: `${doneRate}%`, height: "100%", background: doneRate >= 100 ? OK.fg : mc }} />
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function ModuleProgressSummary({ rows, activeModule, onPick }) {
  const list = Array.isArray(rows) ? rows : [];
  return (
    <div style={{ padding: 12, borderBottom: "1px solid var(--border)" }}>
      <div style={{ display: "flex", alignItems: "center", marginBottom: 8 }}>
        <div style={{ fontWeight: 900, fontSize: 14 }}>모듈별 진행 요약</div>
        <span style={{ marginLeft: "auto", color: "var(--text-secondary)", fontSize: 14 }}>30일</span>
      </div>
      <div style={{ display: "grid", gap: 6, maxHeight: 252, overflow: "auto" }}>
        {list.length === 0 && <div style={{ color: "var(--text-secondary)", fontSize: 14, padding: 10, textAlign: "center" }}>요약 없음</div>}
        {list.map(row => {
          const received = Number(row.received || 0);
          const completed = Number(row.completed || 0);
          const inProgress = Number(row.in_progress || 0);
          const pending = Number(row.pending || 0);
          const total = received + completed + inProgress;
          const pendingRate = total ? Math.round((pending / total) * 100) : 0;
          const mc = moduleColor(row.module);
          const active = activeModule === row.module;
          return (
            <button key={row.module} type="button" onClick={() => onPick(row.module)}
              style={{
                display: "grid", gap: 4, textAlign: "left",
                padding: 8, borderRadius: 10,
                border: "1px solid " + (active ? mc : "var(--border)"),
                background: active ? mc + "12" : "var(--bg-card)",
                color: "var(--text-primary)", cursor: "pointer",
              }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <span style={{ width: 7, height: 7, borderRadius: 999, background: mc, flex: "0 0 auto" }} />
                <span style={{ fontWeight: 900, fontFamily: "monospace", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row.module}</span>
                <span style={{ marginLeft: "auto", color: "var(--text-secondary)", fontFamily: "monospace", fontSize: 14 }}>
                  받음 {received + inProgress + completed} · 완료 {completed}
                </span>
              </div>
              <div style={{ height: 5, borderRadius: 999, background: "var(--bg-primary)", overflow: "hidden" }}>
                <div style={{ width: `${pendingRate}%`, height: "100%", background: pendingRate ? WARN.fg : OK.fg }} />
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", color: "var(--text-secondary)", fontSize: 14 }}>
                <span>진행 {inProgress}</span>
                <span>미완료 {pendingRate}%</span>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

const LOT_MATRIX_STATES = {
  completed: { label: "완료", mark: "✓", bg: "#dcfce7", fg: "#15803d" },
  in_progress: { label: "진행중", mark: "◯", bg: "#fed7aa", fg: "#c2410c" },
  received: { label: "접수", mark: "◎", bg: "#dbeafe", fg: "#1d4ed8" },
  pending: { label: "대기", mark: "—", bg: "#f3f4f6", fg: "#6b7280" },
};

function colorWithAlpha(color, alpha) {
  const raw = String(color || "").trim();
  if (/^#[0-9a-f]{6}$/i.test(raw)) {
    const n = parseInt(raw.slice(1), 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`;
  }
  const rgb = raw.match(/^rgba?\(([^)]+)\)$/i);
  if (rgb) {
    const parts = rgb[1].split(",").slice(0, 3).map(x => x.trim());
    return `rgba(${parts.join(",")},${alpha})`;
  }
  return alpha <= 0 ? "var(--bg-primary)" : raw;
}

function matrixCountAlpha(count) {
  if (count <= 0) return 0;
  if (count <= 2) return 0.15;
  if (count <= 5) return 0.30;
  return 0.50;
}

function matrixCellCount(cell) {
  if (!cell) return 0;
  const n = Number(cell.inform_count);
  return Number.isFinite(n) ? n : 1;
}

function LotProgressMatrix({ matrix, loading, filters, setFilters, productOptions, onOpenCell, onPickLot, onOpenLot, activeModules = [], onToggleModule, showControls = true }) {
  const modules = Array.isArray(matrix?.module_order) ? matrix.module_order : [];
  const activeStates = new Set(filters.states || []);
  const activeModuleSet = new Set(activeModules || []);
  const setFilter = (key, value) => setFilters(prev => ({ ...prev, [key]: value }));
  const toggleState = (state) => {
    setFilters(prev => {
      const cur = new Set(prev.states || []);
      if (cur.has(state)) cur.delete(state);
      else cur.add(state);
      return { ...prev, states: Array.from(cur) };
    });
  };
  const visibleProducts = (matrix?.products || [])
    .map(product => ({
      ...product,
      lots: (product.lots || []).filter(lot => {
        if (activeStates.size === 0) return true;
        return Object.values(lot.modules || {}).some(cell => activeStates.has(cell?.state));
      }),
    }))
    .filter(product => product.lots.length > 0);
  const visibleModuleTotals = {};
  visibleProducts.forEach(product => {
    (product.lots || []).forEach(lot => {
      modules.forEach(module => {
        visibleModuleTotals[module] = (visibleModuleTotals[module] || 0) + matrixCellCount((lot.modules || {})[module]);
      });
    });
  });
  const lotCount = visibleProducts.reduce((sum, product) => sum + product.lots.length, 0);
  const cellTitle = (module, cell) => {
    const count = matrixCellCount(cell);
    const recent = (cell?.recent || [cell]).filter(Boolean)[0] || {};
    const time = String(recent.updated_at || cell?.updated_at || cell?.created_at || "").replace("T", " ").slice(0, 16) || "-";
    return [
      `모듈: ${module}`,
      `인폼 ${count}건`,
      `최근 작성자: ${recent.author || cell?.author || "-"}`,
      `시간: ${time}`,
      `사유: ${recent.reason || cell?.reason || "-"}`,
    ].join("\n");
  };
  const progressWidth = (progress) => {
    const done = Number(progress?.done || 0);
    const total = Number(progress?.total || 0);
    return total ? Math.max(0, Math.min(100, Math.round((done / total) * 100))) : 0;
  };
  const headStyle = {
    position: "sticky", top: 0, zIndex: 5,
    height: 38, padding: "4px 5px",
    borderBottom: "1px solid var(--border)", borderRight: "1px solid var(--border)",
    background: "var(--bg-secondary)", color: "var(--text-secondary)",
    fontSize: 12, fontWeight: 900, textAlign: "center",
    whiteSpace: "nowrap",
  };
  const leftHeadStyle = {
    ...headStyle,
    left: 0,
    zIndex: 7,
    minWidth: 104,
    textAlign: "left",
  };
  const leftCellStyle = {
    position: "sticky", left: 0, zIndex: 3,
    height: 32, width: 104, maxWidth: 104,
    padding: "0 8px",
    borderBottom: "1px solid var(--border)", borderRight: "1px solid var(--border)",
    background: "var(--bg-secondary)",
    fontFamily: "monospace", fontSize: 13, fontWeight: 900,
    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
  };
  return (
    <div style={{ flex: 1, minHeight: 0, padding: 12, borderBottom: "1px solid var(--border)", display: "grid", gridTemplateRows: showControls ? "auto minmax(0,1fr)" : "minmax(0,1fr)", gap: 8, background: "var(--bg-primary)" }}>
      {showControls && <div style={{ display: "grid", gap: 7 }}>
        <select value={filters.product || ""} onChange={e => setFilter("product", e.target.value)} style={inputStyle({ fontSize: 13, padding: "6px 8px" })}>
          <option value="">제품 전체</option>
          {(productOptions || []).map(product => <option key={product} value={product}>{product}</option>)}
        </select>
        <input value={filters.search || ""} onChange={e => setFilter("search", e.target.value)}
          placeholder="root_lot_id 검색"
          style={inputStyle({ fontSize: 13, padding: "6px 8px", fontFamily: "monospace" })} />
        <div style={{ display: "flex", gap: 5, flexWrap: "wrap", alignItems: "center" }}>
          {["received", "in_progress", "completed"].map(state => {
            const meta = LOT_MATRIX_STATES[state];
            const on = activeStates.has(state);
            return (
              <button key={state} type="button" onClick={() => toggleState(state)}
                style={{
                  padding: "4px 7px",
                  borderRadius: 999,
                  border: "1px solid " + (on ? meta.fg : "var(--border)"),
                  background: on ? meta.bg : "var(--bg-primary)",
                  color: on ? meta.fg : "var(--text-secondary)",
                  fontSize: 12,
                  fontWeight: 900,
                  cursor: "pointer",
                }}>
                {meta.mark} {meta.label}
              </button>
            );
          })}
          <span style={{ marginLeft: "auto", color: "var(--text-secondary)", fontSize: 12, fontFamily: "monospace" }}>
            {loading ? "loading" : `${lotCount} lots`}
          </span>
        </div>
      </div>}
      <div style={{ minHeight: 0, overflow: "auto", border: "1px solid var(--border)", borderRadius: 8, background: "var(--bg-card)" }}>
        {visibleProducts.length === 0 ? (
          <div style={{ padding: 18, textAlign: "center", color: "var(--text-secondary)", fontSize: 14 }}>
            검색 조건에 맞는 lot 이 없어요
          </div>
        ) : (
          <table style={{ borderCollapse: "separate", borderSpacing: 0, width: "max-content", minWidth: "100%", tableLayout: "fixed" }}>
            <thead>
              <tr>
                <th style={leftHeadStyle}>root_lot_id</th>
                {modules.map(module => (
                  <th key={module} title={`${module}\nΣ ${visibleModuleTotals[module] || 0}`} onClick={() => onToggleModule && onToggleModule(module)}
                    style={{ ...headStyle, width: 50, minWidth: 50, maxWidth: 50, cursor: onToggleModule ? "pointer" : "default", color: activeModuleSet.has(module) ? "var(--accent)" : "var(--text-secondary)" }}>
                    <span style={{ display: "block", maxWidth: 42, overflow: "hidden", textOverflow: "ellipsis" }}>{module}</span>
                    <span style={{ display: "block", marginTop: 1, fontSize: 11, color: activeModuleSet.has(module) ? "var(--accent)" : "var(--text-muted)" }}>Σ {visibleModuleTotals[module] || 0}</span>
                  </th>
                ))}
                <th style={{ ...headStyle, width: 70, minWidth: 70 }}>진행도</th>
              </tr>
            </thead>
            <tbody>
              {visibleProducts.map(product => (
                <React.Fragment key={product.product || "미지정"}>
                  <tr>
                    <td colSpan={modules.length + 2}
                      style={{
                        position: "sticky",
                        left: 0,
                        zIndex: 2,
                        height: 30,
                        padding: "5px 8px",
                        borderBottom: "1px solid var(--border)",
                        background: "var(--bg-tertiary)",
                        color: "var(--text-primary)",
                        fontSize: 13,
                        fontWeight: 900,
                        fontFamily: "monospace",
                      }}>
                      {stripMlPrefix(product.product || "미지정")} · {product.lots.length} · 인폼 {modules.reduce((sum, m) => sum + Number((product.module_totals || {})[m] || 0), 0)} 건
                    </td>
                  </tr>
                  {product.lots.map(lot => (
                    <tr key={`${product.product}:${lot.root_lot_id}`} onClick={() => onOpenLot ? onOpenLot(product.product, lot) : onPickLot(product.product, lot)}
                      style={{ cursor: "pointer" }}>
                      <td title={lot.root_lot_id} style={leftCellStyle}>
                        <span style={{ display: "inline-flex", alignItems: "center", gap: 4, width: "100%" }}>
                          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>{lot.root_lot_id}</span>
                          <button type="button" title="인폼 탭에서 이 lot 필터"
                            onClick={e => { e.stopPropagation(); onPickLot(product.product, lot); }}
                            style={{ width: 16, height: 22, border: "none", background: "transparent", color: "var(--accent)", cursor: "pointer", padding: 0, fontWeight: 900 }}>
                            ↗
                          </button>
                        </span>
                      </td>
                      {modules.map(module => {
                        const cell = (lot.modules || {})[module];
                        const state = cell?.state || "pending";
                        const meta = LOT_MATRIX_STATES[state] || LOT_MATRIX_STATES.pending;
                        const count = matrixCellCount(cell);
                        const mc = moduleColor(module);
                        return (
                          <td key={module}
                            style={{
                              height: 32,
                              width: 46,
                              minWidth: 46,
                              maxWidth: 46,
                              padding: 0,
                              textAlign: "center",
                              borderBottom: "1px solid var(--border)",
                              borderRight: "1px solid var(--border)",
                              background: "var(--bg-primary)",
                            }}>
                            <button type="button" title={cellTitle(module, cell)}
                              onClick={e => { e.stopPropagation(); onOpenCell(product.product, lot, module, cell); }}
                              style={{
                                position: "relative",
                                width: 42,
                                height: 28,
                                borderRadius: 6,
                                border: "1px solid " + (count ? colorWithAlpha(mc, 0.42) : "var(--border)"),
                                background: count ? colorWithAlpha(mc, matrixCountAlpha(count)) : "var(--bg-primary)",
                                color: count ? mc : "var(--text-muted)",
                                fontSize: 14,
                                lineHeight: "24px",
                                fontWeight: 900,
                                cursor: "pointer",
                                padding: 0,
                              }}>
                              {count || "—"}
                              <span style={{ position: "absolute", top: 3, right: 3, width: 6, height: 6, borderRadius: 999, background: count ? meta.fg : "var(--border)" }} />
                            </button>
                          </td>
                        );
                      })}
                      <td style={{
                        height: 32,
                        width: 70,
                        padding: "4px 6px",
                        borderBottom: "1px solid var(--border)",
                        background: "var(--bg-primary)",
                        fontSize: 12,
                        fontFamily: "monospace",
                        color: "var(--text-secondary)",
                      }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
                          <span style={{ fontWeight: 900, color: "var(--text-primary)" }}>{lot.progress?.done || 0}/{lot.progress?.total || modules.length}</span>
                          <span style={{ flex: 1, height: 5, borderRadius: 999, background: "var(--bg-tertiary)", overflow: "hidden" }}>
                            <span style={{ display: "block", height: "100%", width: `${progressWidth(lot.progress)}%`, background: OK.fg }} />
                          </span>
                        </div>
                      </td>
                    </tr>
                  ))}
                </React.Fragment>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function InformFilterPanel({ filters, setFilter, modules, products, onReset }) {
  const statusOptions = [
    ["", "상태 전체"],
    ["received", "접수"],
    ["in_progress", "진행중"],
    ["completed", "완료"],
    ["pending", "미완료"],
  ];
  return (
    <div style={{ padding: 12, borderBottom: "1px solid var(--border)", display: "grid", gap: 8 }}>
      <input value={filters.query} onChange={e => setFilter("query", e.target.value)}
        placeholder="검색: 제목, lot, 작성자"
        style={inputStyle()} />
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
        <select value={filters.module} onChange={e => setFilter("module", e.target.value)} style={inputStyle()}>
          <option value="">모듈 전체</option>
          {(modules || []).map(m => <option key={m} value={m}>{m}</option>)}
        </select>
        <select value={filters.status} onChange={e => setFilter("status", e.target.value)} style={inputStyle()}>
          {statusOptions.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
        </select>
      </div>
      <select value={filters.product} onChange={e => setFilter("product", e.target.value)} style={inputStyle()}>
        <option value="">product 전체</option>
        {(products || []).map(p => <option key={p} value={p}>{p}</option>)}
      </select>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
        <input value={filters.root_lot} onChange={e => setFilter("root_lot", e.target.value)} placeholder="root_lot" style={inputStyle()} />
        <input value={filters.fab_lot} onChange={e => setFilter("fab_lot", e.target.value)} placeholder="fab_lot" style={inputStyle()} />
      </div>
      <input value={filters.author} onChange={e => setFilter("author", e.target.value)} placeholder="작성자" style={inputStyle()} />
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr auto", gap: 8, alignItems: "center" }}>
        <input type="date" value={filters.start} onChange={e => setFilter("start", e.target.value)} style={inputStyle()} />
        <input type="date" value={filters.end} onChange={e => setFilter("end", e.target.value)} style={inputStyle()} />
        <button type="button" onClick={onReset} title="필터 초기화"
          style={{ height: 34, padding: "0 10px", borderRadius: 8, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", cursor: "pointer", fontSize: 14 }}>
          초기화
        </button>
      </div>
    </div>
  );
}

function InformVirtualList({ roots, selectedId, onOpen }) {
  const rowHeight = 64;
  const ref = useRef(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [height, setHeight] = useState(480);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const update = () => setHeight(el.clientHeight || 480);
    update();
    if (typeof ResizeObserver !== "undefined") {
      const ro = new ResizeObserver(update);
      ro.observe(el);
      return () => ro.disconnect();
    }
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);
  const count = roots.length;
  const start = Math.max(0, Math.floor(scrollTop / rowHeight) - 6);
  const end = Math.min(count, start + Math.ceil(height / rowHeight) + 14);
  const visible = roots.slice(start, end);
  return (
    <div ref={ref} onScroll={e => setScrollTop(e.currentTarget.scrollTop)}
      style={{ flex: 1, overflow: "auto", minHeight: 0, background: "var(--bg-secondary)" }}>
      {count === 0 && <div style={{ padding: 24, textAlign: "center", color: "var(--text-secondary)" }}>조건에 맞는 인폼이 없어요</div>}
      <div style={{ height: count * rowHeight, position: "relative" }}>
        <div style={{ transform: `translateY(${start * rowHeight}px)` }}>
          {visible.map(root => (
            <InformListRow key={root.id} root={root} selected={selectedId === root.id} onOpen={() => onOpen(root)} />
          ))}
        </div>
      </div>
    </div>
  );
}

function InformListRow({ root, selected, onOpen }) {
  const [hover, setHover] = useState(false);
  const status = root.flow_status || "received";
  const mailCount = (root.mail_history || []).length;
  const attachCount = (root.images || []).length + (root.embed_table ? 1 : 0);
  return (
    <button type="button" onClick={onOpen} onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      onFocus={() => setHover(true)} onBlur={() => setHover(false)}
      style={{
        height: 64, width: "100%", boxSizing: "border-box",
        border: "none", borderBottom: "1px solid var(--border)",
        background: selected ? "var(--bg-hover)" : "transparent",
        color: "var(--text-primary)", cursor: "pointer", textAlign: "left",
        display: "grid", gridTemplateRows: "28px 28px", gap: 0,
        padding: "5px 12px",
      }}>
      <div style={{ display: "flex", alignItems: "center", gap: 7, minWidth: 0 }}>
        <span title={informTitle(root)} style={{ minWidth: 0, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontWeight: 900, fontSize: 14 }}>
          {informTitle(root)}
        </span>
        <ModulePill module={root.module || "기타"} />
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0, color: "var(--text-secondary)" }}>
        <LotPill root={root} />
        <StatusBadge status={status} />
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{root.author || "-"}</span>
        <span>·</span>
        <span style={{ whiteSpace: "nowrap" }}>{relativeTime(root.created_at)}</span>
        <span style={{ marginLeft: "auto", display: "inline-flex", gap: 5, opacity: hover ? 1 : 0.35, transition: "opacity 120ms" }}>
          <span title="메일 발송">✉{mailCount || ""}</span>
          <span title="댓글">💬</span>
          <span title="첨부">📎{attachCount || ""}</span>
        </span>
      </div>
    </button>
  );
}

function InformDetailPane({ root, thread, childrenByParent, constants, user, tab, setTab, onReply, onDelete, onToggleCheck, onEdit, onChangeStatus, onOpenMail }) {
  if (!root) {
    return (
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-secondary)", fontSize: 14 }}>
        왼쪽에서 인폼을 선택하면 자세한 내용이 여기 표시돼요
      </div>
    );
  }
  const tabs = [
    ["body", "본문"],
    ["mail", "메일"],
    ["comments", "댓글"],
    ["history", "이력"],
    ["attachments", "첨부"],
  ];
  const lotText = informLotDisplay(root, { maxFabLots: 8 }) || "-";
  const completed = root.flow_status === "completed";
  const canEditDelete = user?.role === "admin" || userMatches(user?.username, root.author);
  return (
    <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
      <div style={{ padding: 16, borderBottom: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
        <div style={{ display: "flex", gap: 10, alignItems: "flex-start", minWidth: 0 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div title={informTitle(root)} style={{ fontSize: 18, fontWeight: 900, lineHeight: 1.35, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {informTitle(root)}
            </div>
            <div style={{ marginTop: 8, display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
              <ModulePill module={root.module || "기타"} />
              <StatusBadge status={root.flow_status || "received"} />
              <LotPill root={root} />
              {root.product && <span style={{ padding: "2px 8px", borderRadius: 999, background: "var(--accent-glow)", color: "var(--accent)", border: "1px solid rgba(249,115,22,0.24)", fontFamily: "monospace", fontSize: 14 }}>{stripMlPrefix(root.product)}</span>}
              <span style={{ color: "var(--text-secondary)", fontSize: 14 }}>{root.author || "-"} · {relativeTime(root.created_at)}</span>
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", justifyContent: "flex-end" }}>
            <button type="button" disabled={!canEditDelete} onClick={() => {
              const next = window.prompt("인폼 본문 수정", root.text || "");
              if (next !== null) onEdit(root.id, { text: next });
            }}
              style={{ padding: "7px 12px", borderRadius: 8, border: "1px solid var(--border)", background: "transparent", color: canEditDelete ? "var(--text-primary)" : "var(--text-muted)", fontWeight: 800, cursor: canEditDelete ? "pointer" : "not-allowed", fontSize: 14 }}>
              수정
            </button>
            <button type="button" disabled={!canEditDelete} onClick={() => onDelete(root.id)}
              style={{ padding: "7px 12px", borderRadius: 8, border: "1px solid " + BAD.fg, background: "transparent", color: canEditDelete ? BAD.fg : "var(--text-muted)", fontWeight: 800, cursor: canEditDelete ? "pointer" : "not-allowed", fontSize: 14 }}>
              삭제
            </button>
            <button type="button" onClick={() => onChangeStatus(root.id, completed ? "received" : "completed", "")}
              style={{ padding: "7px 12px", borderRadius: 8, border: "1px solid " + (completed ? WARN.fg : OK.fg), background: completed ? "transparent" : OK.fg, color: completed ? WARN.fg : "#fff", fontWeight: 800, cursor: "pointer", fontSize: 14 }}>
              {completed ? "완료 해제" : "완료"}
            </button>
            <button type="button" onClick={() => onOpenMail(root)}
              style={{ padding: "7px 12px", borderRadius: 8, border: "1px solid var(--accent)", background: "transparent", color: "var(--accent)", fontWeight: 800, cursor: "pointer", fontSize: 14 }}>
              ✉ 메일
            </button>
          </div>
        </div>
        <div style={{ marginTop: 8, color: "var(--text-secondary)", fontFamily: "monospace", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          lot {lotText}
        </div>
      </div>
      <div style={{ display: "flex", gap: 4, padding: "8px 16px 0", borderBottom: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
        {tabs.map(([key, label]) => (
          <button key={key} type="button" onClick={() => setTab(key)}
            style={{ padding: "8px 12px", borderRadius: "8px 8px 0 0", border: "1px solid " + (tab === key ? "var(--border)" : "transparent"), borderBottom: "none", background: tab === key ? "var(--bg-primary)" : "transparent", color: tab === key ? "var(--text-primary)" : "var(--text-secondary)", fontWeight: 800, cursor: "pointer", fontSize: 14 }}>
            {label}
          </button>
        ))}
      </div>
      <div style={{ flex: 1, minHeight: 0, overflow: "auto", padding: 16 }}>
        {tab === "body" && (
          <div style={{ display: "grid", gap: 12 }}>
            <section style={{ padding: 12, border: "1px solid var(--border)", borderRadius: 10, background: "var(--bg-secondary)" }}>
              <div style={{ marginBottom: 8, color: "var(--text-secondary)", fontWeight: 800 }}>사유 / 내용</div>
              {root.reason && <div style={{ marginBottom: 8 }}><Pill tone="info">{root.reason}</Pill></div>}
              <div style={{ whiteSpace: "pre-wrap", lineHeight: 1.55 }}>{root.text || "(내용 없음)"}</div>
            </section>
            {root.embed_table && <EmbedTableView embed={root.embed_table} product={root.product} />}
          </div>
        )}
        {tab === "mail" && <MailPreviewPanel root={root} onOpenMail={() => onOpenMail(root)} />}
        {tab === "comments" && (
          <ThreadNode node={root} childrenByParent={childrenByParent}
            onReply={onReply} onDelete={onDelete} onToggleCheck={onToggleCheck}
            onEdit={onEdit} user={user} depth={0} constants={constants} />
        )}
        {tab === "history" && <InformHistoryPanel root={root} thread={thread} />}
        {tab === "attachments" && <InformAttachmentsPanel root={root} />}
      </div>
    </div>
  );
}

function MailPreviewPanel({ root, onOpenMail }) {
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [preview, setPreview] = useState(null);
  useEffect(() => {
    setSubject("");
    setBody("");
  }, [root?.id]);
  useEffect(() => {
    if (!root?.id) return;
    const h = setTimeout(() => {
      const q = new URLSearchParams();
      q.set("subject", subject || "");
      q.set("body", body || "");
      sf(API + "/" + encodeURIComponent(root.id) + "/mail-preview?" + q.toString())
        .then(d => setPreview(d))
        .catch(() => setPreview(null));
    }, 250);
    return () => clearTimeout(h);
  }, [root?.id, subject, body]);
  const history = root.mail_history || [];
  return (
    <div style={{ display: "grid", gap: 12 }}>
      <section style={{ padding: 12, border: "1px solid var(--border)", borderRadius: 10, background: "var(--bg-secondary)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
          <div style={{ fontWeight: 900 }}>수신자 미리보기</div>
          <span style={{ color: "var(--text-secondary)" }}>{(preview?.resolved_recipients || []).length}명</span>
          <button type="button" onClick={onOpenMail} style={{ marginLeft: "auto", padding: "6px 12px", borderRadius: 8, border: "1px solid var(--accent)", background: "transparent", color: "var(--accent)", fontWeight: 800, cursor: "pointer", fontSize: 14 }}>전송 창 열기</button>
        </div>
        <div style={{ color: "var(--text-secondary)", fontFamily: "monospace", lineHeight: 1.5 }}>
          {(preview?.resolved_recipients || []).slice(0, 12).join(", ") || "모듈 수신자 없음"}
          {(preview?.resolved_recipients || []).length > 12 ? ` +${preview.resolved_recipients.length - 12}` : ""}
        </div>
      </section>
      <section style={{ padding: 12, border: "1px solid var(--border)", borderRadius: 10, background: "var(--bg-secondary)", display: "grid", gap: 8 }}>
        <label style={{ display: "grid", gap: 4 }}>
          <span style={{ fontWeight: 800 }}>제목</span>
          <input value={subject} onChange={e => setSubject(e.target.value)} placeholder={preview?.subject || "자동 제목"} style={inputStyle()} />
        </label>
        <label style={{ display: "grid", gap: 4 }}>
          <span style={{ fontWeight: 800 }}>본문</span>
          <textarea value={body} onChange={e => setBody(e.target.value)} rows={4} placeholder="비워두면 자동 본문을 사용합니다." style={inputStyle({ resize: "vertical", fontFamily: "inherit" })} />
        </label>
      </section>
      {preview?.html_body && (
        <section style={{ padding: 12, border: "1px solid var(--border)", borderRadius: 10, background: "var(--bg-secondary)" }}>
          <div style={{ fontWeight: 900, marginBottom: 8 }}>본문 미리보기</div>
          <div style={{ maxHeight: 480, overflow: "auto", background: WHITE, border: "1px solid var(--border)", borderRadius: 8, padding: 10 }}
            dangerouslySetInnerHTML={{ __html: preview.html_body }} />
        </section>
      )}
      <section style={{ padding: 12, border: "1px solid var(--border)", borderRadius: 10, background: "var(--bg-secondary)" }}>
        <div style={{ fontWeight: 900, marginBottom: 8 }}>발송 이력</div>
        {history.length === 0 && <div style={{ color: "var(--text-secondary)" }}>발송 이력이 없습니다.</div>}
        {history.slice().reverse().map((m, i) => (
          <div key={i} style={{ padding: "6px 0", borderBottom: "1px dashed var(--border)", color: "var(--text-secondary)" }}>
            {(m.at || m.sent_at || "").replace("T", " ").slice(0, 16)} · {m.actor || m.sender || "-"} · {(m.to || []).length || 0}명 {m.subject ? `· ${m.subject}` : ""}
          </div>
        ))}
      </section>
    </div>
  );
}

function InformHistoryPanel({ root, thread }) {
  const rows = [];
  rows.push({ at: root.created_at, actor: root.author, label: "등록", note: informTitle(root) });
  (root.status_history || []).forEach(h => rows.push({ at: h.at, actor: h.actor, label: `상태: ${h.status}`, note: h.note || "" }));
  (root.edit_history || []).forEach(h => rows.push({ at: h.at, actor: h.actor, label: `수정: ${h.field}`, note: h.after || "" }));
  (root.mail_history || []).forEach(h => rows.push({ at: h.at || h.sent_at, actor: h.actor || h.sender, label: "메일", note: h.subject || "" }));
  (thread || []).filter(x => x.parent_id === root.id).forEach(x => rows.push({ at: x.created_at, actor: x.author, label: "댓글", note: informTitle(x) }));
  rows.sort((a, b) => String(b.at || "").localeCompare(String(a.at || "")));
  return (
    <div style={{ padding: 12, border: "1px solid var(--border)", borderRadius: 10, background: "var(--bg-secondary)" }}>
      {rows.map((r, i) => (
        <div key={i} style={{ display: "grid", gridTemplateColumns: "150px 120px 120px minmax(0,1fr)", gap: 8, padding: "7px 0", borderBottom: "1px dashed var(--border)", alignItems: "center" }}>
          <span style={{ color: "var(--text-secondary)", fontFamily: "monospace" }}>{(r.at || "").replace("T", " ").slice(0, 16)}</span>
          <span>{r.actor || "-"}</span>
          <span style={{ fontWeight: 800 }}>{r.label}</span>
          <span style={{ color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.note || "-"}</span>
        </div>
      ))}
    </div>
  );
}

function InformAttachmentsPanel({ root }) {
  const images = root.images || [];
  const hasEmbed = !!root.embed_table;
  if (!images.length && !hasEmbed) {
    return <div style={{ padding: 32, textAlign: "center", color: "var(--text-secondary)" }}>첨부가 없습니다.</div>;
  }
  return (
    <div style={{ display: "grid", gap: 12 }}>
      <section style={{ padding: 12, border: "1px solid var(--border)", borderRadius: 10, background: "var(--bg-secondary)" }}>
        <div style={{ fontWeight: 900, marginBottom: 8 }}>이미지 / 파일</div>
        {images.length ? <ImageGallery images={images} /> : <div style={{ color: "var(--text-secondary)" }}>이미지 첨부 없음</div>}
      </section>
      {hasEmbed && (
        <section>
          <EmbedTableView embed={root.embed_table} product={root.product} />
        </section>
      )}
    </div>
  );
}

function ProductCatalogPanel({ products, canEdit, onAdd, onDelete }) {
  const [draft, setDraft] = useState("");
  const seen = new Map();
  (products || []).forEach(p => {
    const value = stripMlPrefix(String(p || "").trim());
    if (value) seen.set(value.toLowerCase(), value);
  });
  const list = Array.from(seen.values()).sort();
  const add = () => {
    const v = draft.trim();
    if (!v) return;
    Promise.resolve(onAdd(v)).then(() => setDraft("")).catch(e => alert(e.message || e));
  };
  return (
    <div>
      <div style={{ fontWeight: 900, marginBottom: 6 }}>제품 카탈로그</div>
      <div style={{ maxHeight: 150, overflow: "auto", border: "1px solid var(--border)", borderRadius: 8, background: "var(--bg-card)" }}>
        {list.length === 0 && <div style={{ padding: 10, color: "var(--text-secondary)" }}>등록된 제품 없음</div>}
        {list.map(p => (
          <div key={p} style={{ display: "flex", alignItems: "center", gap: 6, padding: "5px 8px", borderBottom: "1px solid var(--border)", fontFamily: "monospace" }}>
            <span style={{ flex: 1 }}>{p}</span>
            {canEdit && <button type="button" onClick={() => onDelete(p)} style={{ border: "1px solid " + BAD.fg, background: "transparent", color: BAD.fg, borderRadius: 6, cursor: "pointer", fontSize: 14 }}>삭제</button>}
          </div>
        ))}
      </div>
      {canEdit && (
        <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
          <input value={draft} onChange={e => setDraft(e.target.value)} onKeyDown={e => { if (e.key === "Enter") add(); }}
            placeholder="신규 제품명" style={inputStyle({ flex: 1 })} />
          <button type="button" onClick={add} style={{ padding: "6px 12px", borderRadius: 8, border: "1px solid var(--accent)", background: "transparent", color: "var(--accent)", fontWeight: 800, cursor: "pointer", fontSize: 14 }}>추가</button>
        </div>
      )}
    </div>
  );
}

function InformWizard({
  form, setForm, constants, products, lotOptions, step, setStep,
  attachMode, setAttachMode, embedFetching, embedSchemaCols,
  embedCustomCols, setEmbedCustomCols, embedCustomSearch, setEmbedCustomSearch,
  setSnapshotTick, mailDraft, setMailDraft, mailMeta, msg, setMsg, onSubmit, onClose,
}) {
  const productOptions = Array.from(new Set((products || []).map(p => stripMlPrefix(String(p || "").trim())).filter(Boolean))).sort();
  const steps = ["lot 선택", "모듈 + 사유", "SplitTable 첨부", "메일 미리보기", "검토 + 등록"];
  const validate = () => {
    if (step === 0) {
      if (!(form.product || "").trim()) { setMsg("product 를 선택해 주세요"); return false; }
      if (!(form.lot_id || "").trim()) { setMsg("lot 을 선택해 주세요"); return false; }
    }
    if (step === 1) {
      if (!(form.module || "").trim()) { setMsg("module 을 선택해 주세요"); return false; }
      if (!(form.reason || "").trim()) { setMsg("사유를 선택해 주세요"); return false; }
      if (!(form.text || "").trim()) { setMsg("note 를 입력해 주세요"); return false; }
    }
    if (step === 2 && attachMode === "custom" && embedCustomCols.length === 0) {
      setMsg("CUSTOM 컬럼을 하나 이상 선택해 주세요");
      return false;
    }
    setMsg("");
    return true;
  };
  const next = () => {
    if (!validate()) return;
    if (step === 2 && attachMode !== "none") setSnapshotTick(x => x + 1);
    setStep(Math.min(4, step + 1));
  };
  const prev = () => { setMsg(""); setStep(Math.max(0, step - 1)); };
  const toggleCol = (col) => {
    setEmbedCustomCols(embedCustomCols.includes(col)
      ? embedCustomCols.filter(x => x !== col)
      : [...embedCustomCols, col]);
  };
  const filteredCols = (embedSchemaCols || []).filter(c => !embedCustomSearch || String(c).toLowerCase().includes(embedCustomSearch.toLowerCase())).slice(0, 200);
  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, zIndex: 3200, background: "rgba(0,0,0,0.62)", display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
      <div onClick={e => e.stopPropagation()} style={{ width: "min(980px,96vw)", maxHeight: "92vh", overflow: "auto", background: "var(--bg-secondary)", border: "1px solid var(--border)", borderRadius: 10, padding: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
          <div style={{ fontSize: 16, fontWeight: 900 }}>신규 인폼 등록</div>
          <div style={{ marginLeft: "auto", color: "var(--text-secondary)" }}>draft 자동 저장</div>
          <button type="button" onClick={onClose} style={{ border: "none", background: "transparent", color: "var(--text-secondary)", cursor: "pointer", fontSize: 20 }}>×</button>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(5,1fr)", gap: 6, marginBottom: 14 }}>
          {steps.map((label, i) => (
            <button key={label} type="button" onClick={() => setStep(i)}
              style={{ padding: "7px 8px", borderRadius: 8, border: "1px solid " + (i === step ? "var(--accent)" : "var(--border)"), background: i === step ? "var(--accent)" : "var(--bg-primary)", color: i === step ? "#fff" : "var(--text-secondary)", fontSize: 14, fontWeight: 800, cursor: "pointer" }}>
              {i + 1}. {label}
            </button>
          ))}
        </div>
        {step === 0 && (
          <div style={{ display: "grid", gap: 12 }}>
            <label style={{ display: "grid", gap: 5 }}>
              <span style={{ fontWeight: 800 }}>product</span>
              <select value={form.product} onChange={e => setForm(f => ({ ...f, product: e.target.value, lot_id: "" }))} style={inputStyle()}>
                <option value="">-- product 선택 --</option>
                {productOptions.map(p => <option key={p} value={p}>{p}</option>)}
              </select>
            </label>
            <label style={{ display: "grid", gap: 5 }}>
              <span style={{ fontWeight: 800 }}>root_lot_id / fab_lot_id</span>
              <LotCombobox
                value={form.lot_id}
                onChange={v => setForm(f => ({ ...f, lot_id: v }))}
                options={lotOptions}
                productSelected={!!form.product}
                manualMode={false}
                onToggleManual={() => {}}
              />
            </label>
          </div>
        )}
        {step === 1 && (
          <div style={{ display: "grid", gap: 12 }}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              <label style={{ display: "grid", gap: 5 }}>
                <span style={{ fontWeight: 800 }}>module</span>
                <select value={form.module} onChange={e => setForm(f => ({ ...f, module: e.target.value }))} style={inputStyle()}>
                  <option value="">-- module --</option>
                  {(constants.modules || []).map(m => <option key={m} value={m}>{m}</option>)}
                </select>
              </label>
              <label style={{ display: "grid", gap: 5 }}>
                <span style={{ fontWeight: 800 }}>사유</span>
                <select value={form.reason} onChange={e => setForm(f => ({ ...f, reason: e.target.value }))} style={inputStyle()}>
                  <option value="">-- 사유 --</option>
                  {(constants.reasons || []).map(r => <option key={r} value={r}>{r}</option>)}
                </select>
              </label>
            </div>
            <label style={{ display: "grid", gap: 5 }}>
              <span style={{ fontWeight: 800 }}>note</span>
              <textarea value={form.text} onChange={e => setForm(f => ({ ...f, text: e.target.value }))} rows={8} style={inputStyle({ resize: "vertical", fontFamily: "inherit" })} />
            </label>
          </div>
        )}
        {step === 2 && (
          <div style={{ display: "grid", gap: 12 }}>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {[["auto", "자동 첨부"], ["custom", "CUSTOM 검색 후 첨부"], ["none", "안 함"]].map(([key, label]) => (
                <button key={key} type="button" onClick={() => {
                  setAttachMode(key);
                  if (key === "none") setForm(f => ({ ...f, attach_embed: false, embed: emptyEmbedTable() }));
                  else setSnapshotTick(x => x + 1);
                }}
                  style={{ padding: "8px 12px", borderRadius: 8, border: "1px solid " + (attachMode === key ? "var(--accent)" : "var(--border)"), background: attachMode === key ? "var(--accent)" : "var(--bg-primary)", color: attachMode === key ? "#fff" : "var(--text-secondary)", fontWeight: 800, cursor: "pointer", fontSize: 14 }}>
                  {label}
                </button>
              ))}
              {embedFetching && <span style={{ alignSelf: "center", color: "var(--accent)" }}>SplitTable 스냅샷 로딩...</span>}
            </div>
            {attachMode === "custom" && (
              <div style={{ border: "1px solid var(--border)", borderRadius: 10, padding: 12, background: "var(--bg-card)" }}>
                <input value={embedCustomSearch} onChange={e => setEmbedCustomSearch(e.target.value)} placeholder="컬럼 검색" style={inputStyle({ marginBottom: 8 })} />
                <div style={{ maxHeight: 230, overflow: "auto", display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(190px,1fr))", gap: 4 }}>
                  {filteredCols.map(c => {
                    const on = embedCustomCols.includes(c);
                    return (
                      <label key={c} style={{ display: "flex", gap: 5, alignItems: "center", padding: "4px 6px", borderRadius: 6, background: on ? "var(--accent-glow)" : "transparent", cursor: "pointer", fontFamily: "monospace" }}>
                        <input type="checkbox" checked={on} onChange={() => toggleCol(c)} />
                        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c}</span>
                      </label>
                    );
                  })}
                </div>
                <div style={{ marginTop: 8, color: "var(--text-secondary)" }}>선택 {embedCustomCols.length}개</div>
              </div>
            )}
            <button type="button" onClick={() => setSnapshotTick(x => x + 1)}
              disabled={attachMode === "none"}
              style={{ justifySelf: "start", padding: "7px 12px", borderRadius: 8, border: "1px solid var(--accent)", background: "transparent", color: "var(--accent)", fontWeight: 800, cursor: attachMode === "none" ? "not-allowed" : "pointer", opacity: attachMode === "none" ? 0.5 : 1, fontSize: 14 }}>
              Search
            </button>
            {form.attach_embed && hasEmbedSnapshot(form.embed) && <EmbedTableView embed={form.embed} product={form.product} />}
          </div>
        )}
        {step === 3 && (
          <div style={{ display: "grid", gap: 12 }}>
            <section style={{ padding: 12, border: "1px solid var(--border)", borderRadius: 10, background: "var(--bg-card)" }}>
              <div style={{ fontWeight: 900, marginBottom: 6 }}>자동 수신자</div>
              <div style={{ color: "var(--text-secondary)", fontFamily: "monospace" }}>
                {(mailMeta.recipients || []).map(r => `${r.username} <${r.email}>`).join(", ") || "모듈 수신자 없음"}
              </div>
            </section>
            <section style={{ padding: 12, border: "1px solid var(--border)", borderRadius: 10, background: "var(--bg-card)" }}>
              <div style={{ fontWeight: 900, marginBottom: 6 }}>KNOB 강조</div>
              <div style={{ color: "var(--text-secondary)", fontFamily: "monospace" }}>
                {((mailMeta.knobMap || {})[form.module] || []).join(", ") || "설정 없음"}
              </div>
            </section>
            <label style={{ display: "grid", gap: 5 }}>
              <span style={{ fontWeight: 800 }}>제목</span>
              <input value={mailDraft.subject || ""} onChange={e => setMailDraft(d => ({ ...d, subject: e.target.value }))} style={inputStyle()} />
            </label>
            <label style={{ display: "grid", gap: 5 }}>
              <span style={{ fontWeight: 800 }}>본문</span>
              <textarea value={mailDraft.body || ""} onChange={e => setMailDraft(d => ({ ...d, body: e.target.value }))} rows={8} style={inputStyle({ resize: "vertical", fontFamily: "inherit" })} />
            </label>
          </div>
        )}
        {step === 4 && (
          <div style={{ display: "grid", gap: 10 }}>
            {[
              ["product", stripMlPrefix(form.product || "-")],
              ["lot", form.lot_id || "-"],
              ["module", form.module || "-"],
              ["사유", form.reason || "-"],
              ["SplitTable", attachMode === "none" ? "첨부 안 함" : (form.attach_embed ? `${embedSnapshotRowCount(form.embed)} rows` : "대기")],
              ["메일 수신자", `${(mailMeta.recipients || []).length}명`],
            ].map(([k, v]) => (
              <div key={k} style={{ display: "grid", gridTemplateColumns: "140px minmax(0,1fr)", gap: 10, padding: 10, border: "1px solid var(--border)", borderRadius: 10, background: "var(--bg-card)" }}>
                <b>{k}</b><span style={{ color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{v}</span>
              </div>
            ))}
            <div style={{ padding: 12, border: "1px solid var(--border)", borderRadius: 10, background: "var(--bg-card)", whiteSpace: "pre-wrap", lineHeight: 1.55 }}>
              {form.text || "(note 없음)"}
            </div>
          </div>
        )}
        {msg && <div style={{ marginTop: 12, padding: "8px 10px", borderRadius: 8, border: `1px solid ${BAD.fg}`, color: BAD.fg, background: BAD.bg }}>{msg}</div>}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 16 }}>
          <button type="button" onClick={prev} disabled={step === 0}
            style={{ padding: "8px 14px", borderRadius: 8, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", cursor: step === 0 ? "not-allowed" : "pointer", opacity: step === 0 ? 0.5 : 1, fontSize: 14 }}>이전</button>
          {step < 4 ? (
            <button type="button" onClick={next}
              style={{ padding: "8px 16px", borderRadius: 8, border: "none", background: "var(--accent)", color: "#fff", fontWeight: 900, cursor: "pointer", fontSize: 14 }}>다음</button>
          ) : (
            <button type="button" onClick={() => { if (validate()) onSubmit(); }}
              style={{ padding: "8px 16px", borderRadius: 8, border: "none", background: "var(--accent)", color: "#fff", fontWeight: 900, cursor: "pointer", fontSize: 14 }}>등록</button>
          )}
        </div>
      </div>
    </div>
  );
}
