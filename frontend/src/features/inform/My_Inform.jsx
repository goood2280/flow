/* My_Inform.jsx v8.7.0 — 모듈 인폼 시스템 (역할 뷰 + 체크 + flow 상태 + SplitTable 연동).
 *
 * 보안: auth 미들웨어 + 세션 토큰 그대로. sf() 가 X-Session-Token 자동 주입.
 * 삭제 정책: 작성자 본인만 (관리자도 불가) — 서버에서도 동일하게 강제됨.
 */
import React, { useEffect, useMemo, useState, useRef } from "react";
import { sf, authSrc, postJson, userLabel, userMatches } from "../../lib/api";
import { htmlToText, sanitizeHtml } from "../../lib/sanitizeHtml";
import { canAccessSubTab, canManagePage } from "../../lib/permissions";
import PageGear from "../../components/PageGear";
import Modal from "../../components/Modal";
import Loading from "../../components/Loading";
import { toast } from "../../components/Toast";
import { Btn, Card, Chip, EmptyState, Input, Pill, Select, TabStrip, TableWrap, Tbl, Textarea, statusPalette, chartPalette } from "../../components/UXKit";
import SplitTableSnapshotView from "../../components/SplitTableSnapshotView";
import RichBoardEditor, { RichBoardContent, richTextHasContent } from "../../components/RichBoardEditor";

const API = "/api/informs";
export const WIZARD_STEPS = ["lot_module", "splittable", "mail_preview"];
export const WIZARD_BACKEND_CALLS = [
  "/api/informs/config",
  "/api/splittable/lot-candidates",
  "/api/informs/splittable-snapshot",
  "/api/informs/recipients",
  "/api/informs/mail-groups",
  "POST /api/informs",
  "POST /api/informs/bulk-create",
];
const WIZARD_DRAFT_KEY = "flow_inform_wizard_draft_v1";
const WIZARD_OPEN_KEY = "flow_inform_open_wizard_v1";
const LOT_CANDIDATE_LIMIT = 20000;
const OK = statusPalette.ok;
const WARN = statusPalette.warn;
const BAD = statusPalette.bad;
const INFO = statusPalette.info;
const NEUTRAL = statusPalette.neutral;
const MODULE_SERIES = [chartPalette.series[0], chartPalette.series[2], chartPalette.series[11], chartPalette.series[8], chartPalette.series[6], chartPalette.series[12], chartPalette.series[3]];
const INFORM_TABS_ALL = [
  ["inform", "인폼"],
  ["matrix", "매트릭스"],
  ["audit", "로그"],
];
// v9.1.x: 소탭 단위 권한 — 허용된 소탭만 노출.
// localStorage(hol_user)는 로그인 후 채워지므로 렌더/호출 시점에 평가한다 (모듈 상수 고정 금지).
const informTabs = () => INFORM_TABS_ALL.filter(([key]) => canAccessSubTab("inform", key));
const defaultInformTab = () => informTabs()[0]?.[0] || "inform";
const DEFAULT_SHARED_FILTERS = {
  products: [],
  modules: [],
  statuses: [],
  lot: "",
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

function formatBytes(n) {
  const v = Number(n || 0);
  if (!Number.isFinite(v) || v <= 0) return "0 KB";
  if (v >= 1024 * 1024) return `${(v / (1024 * 1024)).toFixed(2)} MB`;
  return `${Math.max(0.1, v / 1024).toFixed(1)} KB`;
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

function mergeLotCandidateOptions(...groups) {
  const out = [];
  const seen = new Set();
  groups.forEach(group => {
    const type = group?.type || "fab";
    (group?.candidates || []).forEach(v => {
      const value = String(v || "").trim();
      if (!value) return;
      const key = value.toLowerCase();
      if (seen.has(key)) return;
      seen.add(key);
      out.push({ value, type });
    });
  });
  return out;
}

function matrixLotId(lot) {
  return String(lot?.fab_lot_id || lot?.matrix_lot_id || lot?.lot_id || lot?.root_lot_id || "").trim();
}

function matrixLotSearchText(lot) {
  return [
    matrixLotId(lot),
    lot?.source_root_lot_id,
    lot?.root_lot_id,
    lot?.lot_id,
    lot?.fab_lot_id,
  ].filter(Boolean).join(" ");
}

function detailLotId(node) {
  const fabs = splitFabLotsFromNode(node);
  return String(node?.fab_lot_id || node?.matrix_lot_id || fabs[0] || node?.lot_id || node?.root_lot_id || node?.wafer_id || "").trim();
}

function emptyEmbedTable() {
  return { source: "", columns: [], rows: [], note: "" };
}

function hasEmbedSnapshot(embed) {
  if (!embed) return false;
  const attached = Array.isArray(embed.attached_sets) ? embed.attached_sets : [];
  const rows = Array.isArray(embed.rows) ? embed.rows : [];
  const columns = Array.isArray(embed.columns) ? embed.columns : [];
  const stRows = Array.isArray(embed.st_view?.rows) ? embed.st_view.rows : [];
  const stHeaders = Array.isArray(embed.st_view?.headers) ? embed.st_view.headers : [];
  return attached.length > 0 || rows.length > 0 || columns.length > 0 || stRows.length > 0 || stHeaders.length > 0;
}

function embedSnapshotRowCount(embed) {
  if (!embed) return 0;
  const stRows = Array.isArray(embed.st_view?.rows) ? embed.st_view.rows : [];
  if (stRows.length) return stRows.length;
  const rows = Array.isArray(embed.rows) ? embed.rows : [];
  if (rows.length) return rows.length;
  const attached = Array.isArray(embed.attached_sets) ? embed.attached_sets : [];
  return attached.reduce((sum, s) => sum + Number(s.wafer_count || (s.rows || []).length || 0), 0);
}

function embedSnapshotWaferCount(embed) {
  if (!embed) return 0;
  const stHeaders = Array.isArray(embed.st_view?.headers) ? embed.st_view.headers : [];
  if (stHeaders.length) return stHeaders.length;
  const columns = Array.isArray(embed.columns) ? embed.columns : [];
  if (columns.length > 1 && /^(parameter|param|항목)$/i.test(String(columns[0] || "").trim())) return columns.length - 1;
  return 0;
}

function embedSnapshotSummary(embed) {
  if (!embed) return "첨부 0개";
  const setCount = Array.isArray(embed.attached_sets) ? embed.attached_sets.length : 0;
  const rows = embedSnapshotRowCount(embed);
  const wafers = embedSnapshotWaferCount(embed);
  const unit = setCount ? `${setCount}개 세트` : "1개 snapshot";
  return `${unit} / ${rows} rows${wafers ? ` / ${wafers} wafers` : ""}`;
}

function hasLotSnapshotData(embed) {
  return embedSnapshotRowCount(embed) > 0 || embedSnapshotWaferCount(embed) > 0;
}

const STATUS_META = {
  registered:      { label: "등록", tone: "info", color: INFO.fg, dot: "○" },
  mail_completed:  { label: "메일완료", tone: "brand", color: WARN.fg, dot: "◑" },
  apply_confirmed: { label: "등록적용확인", tone: "ok", color: OK.fg, dot: "●" },
  reinform:        { label: "재인폼", tone: "neutral", color: "var(--text-secondary)", dot: "↳" },
};
const STATUS_ORDER = ["registered", "mail_completed", "apply_confirmed"];

function normalizeFlowStatus(status, node = null) {
  const raw = String(status || "").trim();
  if (raw === "registered" || raw === "mail_completed" || raw === "apply_confirmed") return raw;
  if (raw === "completed") return "apply_confirmed";
  if (raw === "received" || raw === "reviewing" || raw === "in_progress" || !raw) {
    return node && Array.isArray(node.mail_history) && node.mail_history.length ? "mail_completed" : "registered";
  }
  return raw;
}

function defaultInformForm() {
  return {
    wafer_id: "", lot_id: "", fab_lot_ids: [], product: "", module: "", reason: "PEMS", text: "",
    deadline: "",
    attach_split: false, split: { column: "", old_value: "", new_value: "" },
    attach_embed: false, embed: emptyEmbedTable(),
    // 스냅샷 표시 형식 — SplitTable 화면과 같은 3종 (matrix=기본 / split_check / merged).
    snapshot_mode: "matrix",
    // SplitTable "적용 공정 정보" 와 동일 — 항목명을 연결 step_id 로.
    show_step_ids: false,
  };
}

// 첨부 컬럼 prefix — SplitTable 이 보여주는 계열 전부.
const SNAPSHOT_COL_PREFIXES = ["KNOB", "MASK", "FAB", "INLINE", "VM", "TAG", "ET", "CUSTOM", "기타"];
// 표의 축(제품/lot/wafer)은 스냅샷이 헤더로 이미 들고 있어서 첨부 후보에서 뺀다.
const IDENTITY_COLS = ["PRODUCT", "ROOT_LOT_ID", "LOT_ID", "WAFER_ID"];
const isIdentityCol = (col) => IDENTITY_COLS.includes(String(col || "").trim().toUpperCase());
// 컬럼명 앞머리 → 계열. 목록 그룹 머리와 prefix 필터가 같은 판정을 쓴다.
const colPrefixOf = (col) => {
  const raw = String(col || "").toUpperCase();
  const m = raw.match(/^([A-Z]+)_/);
  const head = m ? m[1] : raw;
  return SNAPSHOT_COL_PREFIXES.includes(head) ? head : "기타";
};

// 스냅샷 표시 형식 라벨 — 위저드 선택 UI 와 SplitTable 화면 용어를 맞춘다.
const SNAPSHOT_MODES = [
  ["matrix", "기본"],
  ["split_check", "Split 체크 표시"],
  ["merged", "병합 표시"],
];

function reInformAttachedSets(root) {
  const embed = root?.embed_table && typeof root.embed_table === "object" ? root.embed_table : {};
  const attached = Array.isArray(embed.attached_sets)
    ? embed.attached_sets
    : (Array.isArray(root?.attached_sets) ? root.attached_sets : []);
  return attached.filter(s => s && typeof s === "object").map(s => ({
    ...s,
    columns: Array.isArray(s.columns) ? s.columns : [],
    rows: Array.isArray(s.rows) ? s.rows : [],
  }));
}

function reInformSelectedSetIds(root) {
  return uniqueClean(reInformAttachedSets(root).map(s => s.id || s.name || s.source));
}

function reInformFormFromParent(root) {
  const attached = reInformAttachedSets(root);
  const rawEmbed = root?.embed_table && typeof root.embed_table === "object" ? root.embed_table : null;
  const embed = rawEmbed
    ? { ...rawEmbed, attached_sets: attached }
    : emptyEmbedTable();
  const lot = detailLotId(root);
  const displayMode = String(embed.display_mode || embed.st_scope?.display_mode || "");
  return {
    ...defaultInformForm(),
    wafer_id: String(root?.wafer_id || ""),
    lot_id: lot,
    fab_lot_ids: lot ? [lot] : [],
    product: stripMlPrefix(root?.product || ""),
    module: root?.module || "",
    reason: "PEMS",
    text: String(root?.text || ""),
    attach_embed: hasEmbedSnapshot(embed),
    embed: hasEmbedSnapshot(embed) ? embed : emptyEmbedTable(),
    snapshot_mode: ["split_check", "merged"].includes(displayMode) ? displayMode : "matrix",
    show_step_ids: !!(embed.step_labels || embed.st_view?.step_labels),
  };
}

// ── 적용 공정(step) 줄 편집 ────────────────────────────────────────────────
// "적용 공정 표시" 스냅샷은 항목 칸에 연결 step 을 줄바꿈으로 쌓는다. 메일에는
// 그중 일부만 남기고 싶은 경우가 많아서, param 단위로 줄 목록을 뽑아 고를 수 있게 한다.
function embedRowStepLabel(row) {
  const prefix = Array.isArray(row?._prefix_cells) ? row._prefix_cells[0] : "";
  return String(prefix || row?._display || "");
}

function embedStepLineGroups(embed) {
  const rows = embed?.st_view?.rows;
  if (!Array.isArray(rows)) return [];
  const byParam = new Map();
  rows.forEach(r => {
    const label = embedRowStepLabel(r);
    const key = String(r?._param || label);
    if (!key) return;
    const lines = label.split("\n").map(s => s.trim()).filter(Boolean);
    const all = Array.isArray(r?._step_lines_all) ? r._step_lines_all : lines;
    if (all.length < 2) return;
    if (!byParam.has(key)) byParam.set(key, { param: key, lines, allLines: all });
  });
  return Array.from(byParam.values());
}

function embedWithStepLines(embed, param, lines, allLines) {
  const st = embed?.st_view;
  if (!st || !Array.isArray(st.rows)) return embed;
  const label = lines.join("\n");
  const rows = st.rows.map(r => {
    if (String(r?._param || embedRowStepLabel(r)) !== param) return r;
    const next = { ...r, _display: label, _step_lines_all: allLines };
    if (Array.isArray(r._prefix_cells)) next._prefix_cells = [label, ...r._prefix_cells.slice(1)];
    return next;
  });
  return { ...embed, st_view: { ...st, rows } };
}

function informTitle(node) {
  const text = node?.text_format === "html" ? htmlToText(node?.text || "") : String(node?.text || "").trim();
  const first = text.split(/\n+/).find(Boolean);
  if (first) return first;
  const module = String(node?.module || "").trim();
  return [module, "인폼"].filter(Boolean).join(" · ") || "(내용 없음)";
}

function withRePrefix(text) {
  const body = String(text || "").trim();
  if (!body) return "";
  return body.startsWith("[RE]") ? body : `[RE] ${body}`;
}

function reInformTextForDisplay(node) {
  const body = String(node?.text || "").trim();
  if (!body) return "";
  if (!node?.parent_id || body.startsWith("[RE]")) return body;
  return `[RE] ${body}`;
}

function renderMailSubjectTemplate(template, values = {}) {
  const raw = String(template || "").trim();
  if (!raw) return "";
  const vars = {
    product: stripMlPrefix(values.product || ""),
    lot: values.lot || "",
    module: values.module || "",
    reason: values.reason || "",
  };
  return raw.replace(/\{(product|lot|module|reason)\}/g, (_m, key) => vars[key] || "").trim();
}

function defaultInformMailSubject(form, lotLabel, reasonTemplates = {}) {
  const reason = String(form?.reason || "PEMS").trim() || "PEMS";
  const template = reasonTemplates?.[reason]?.subject || "";
  const rendered = renderMailSubjectTemplate(template, {
    product: form?.product || "",
    lot: lotLabel || "",
    module: form?.module || "",
    reason,
  });
  if (rendered) return rendered;
  // 기본 제목 = `[인폼] 제품 lot 모듈 split table` (backend _fallback_mail_subject 와 같은 규약).
  const parts = [stripMlPrefix(form?.product || ""), lotLabel || "", form?.module || ""]
    .map(x => String(x || "").trim()).filter(Boolean);
  return `[인폼] ${parts.join(" ")} split table`.replace(/\s+/g, " ").trim();
}

function relativeTime(iso) {
  if (!iso) return "-";
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return String(iso).replace("T", " ").slice(0, 16);
  const now = new Date();
  const yyyy = t.getFullYear();
  const mm = String(t.getMonth() + 1).padStart(2, "0");
  const dd = String(t.getDate()).padStart(2, "0");
  const hh = String(t.getHours()).padStart(2, "0");
  const mi = String(t.getMinutes()).padStart(2, "0");
  const sameDay = yyyy === now.getFullYear() && t.getMonth() === now.getMonth() && t.getDate() === now.getDate();
  if (sameDay) return `${hh}:${mi}`;
  if (yyyy === now.getFullYear()) return `${mm}/${dd} ${hh}:${mi}`;
  return `${yyyy}/${mm}/${dd}`;
}

function _entryLastUpdateForUi(entry) {
  const vals = [entry?.created_at, entry?.checked_at, entry?.updated_at];
  (entry?.status_history || []).forEach(h => vals.push(h?.at));
  (entry?.edit_history || []).forEach(h => vals.push(h?.at));
  (entry?.mail_history || []).forEach(h => vals.push(h?.at || h?.sent_at));
  return vals.filter(Boolean).sort().slice(-1)[0] || entry?.created_at || "";
}

const informConnectedPanel = { display: "flex", flexDirection: "column", gap: 0 };
const informConnectedSection = { padding: "14px 0", borderBottom: "1px solid var(--border)" };
const informConnectedSectionFirst = { ...informConnectedSection, paddingTop: 0 };
const informConnectedSectionLast = { ...informConnectedSection, borderBottom: "none", paddingBottom: 0 };
const informConnectedSectionOnly = { padding: 0, borderBottom: "none" };
const informConnectedSectionTitle = { fontSize: 14, fontWeight: 900, color: "var(--text-secondary)", marginBottom: 8 };

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
  if (typeof window === "undefined") return { tab: defaultInformTab(), filters: DEFAULT_SHARED_FILTERS };
  const params = new URLSearchParams(window.location.search);
  const tab = informTabs().some(([key]) => key === params.get("inform_tab")) ? params.get("inform_tab") : defaultInformTab();
  return {
    tab,
    filters: {
      products: parseCsvParam(params, "products"),
      modules: parseCsvParam(params, "modules"),
      statuses: parseCsvParam(params, "statuses").filter(v => v !== "pending"),
      lot: params.get("lot") || "",
      types: parseCsvParam(params, "types"),
    },
  };
}

function syncInformQuery(tab, filters) {
  if (typeof window === "undefined") return;
  const params = new URLSearchParams();
  params.set("inform_tab", tab || "inform");
  const setArray = (key, arr) => {
    params.delete(key);
    params.delete(`${key}[]`);
    uniqueClean(arr).filter(v => key !== "statuses" || v !== "pending").forEach(v => params.append(key, v));
  };
  setArray("products", filters.products);
  setArray("modules", filters.modules);
  setArray("statuses", filters.statuses);
  setArray("types", filters.types);
  if (filters.lot) params.set("lot", filters.lot); else params.delete("lot");
  const next = `${window.location.pathname}?${params.toString()}`;
  window.history.replaceState(null, "", params.toString() ? next : window.location.pathname);
}

/* v8.7.1 — 모듈별 구분색 (좌측 리스트 / 루트카드 left border / Gantt bar fallback) */
const MODULE_COLORS = {
  GATE: BAD.fg,
  STI: WARN.fg,
  PC: chartPalette.series[1],
  MOL: chartPalette.series[3],
  BEOL: INFO.fg,
  ET: chartPalette.series[6],
  EDS: chartPalette.series[2],
  "S-D Epi": chartPalette.series[11],
  Spacer: chartPalette.series[7],
  Well: chartPalette.series[10],
  MASK: NEUTRAL.fg,
  FAB: "rgba(51,65,85,0.95)",
  KNOB: chartPalette.series[13],
  "기타": "rgba(107,114,128,0.95)",
};
const FALLBACK_PALETTE = MODULE_SERIES;

function NewInformButton({ onClick }) {
  return (
    <Btn variant="primary" type="button" onClick={onClick} style={{ minWidth: 140 }}>
      + 신규 인폼 등록
    </Btn>
  );
}

// v8.8.29: Lot 선택 콤보박스 — 텍스트 타이핑으로 LOT_ID 부분일치 필터.
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
    ? "LOT_ID 직접 입력"
    : `LOT_ID 검색 (${(options || []).length}건 · 타이핑하면 필터)`;

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
              <span style={{ fontSize: 14, padding: "1px 5px", borderRadius: 8, background: o.type === "fab" ? INFO.bg : OK.bg, color: o.type === "fab" ? INFO.fg : OK.fg, fontFamily: "inherit", flexShrink: 0 }}>
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

function StatusBadge({ status, compact = false }) {
  const normalized = normalizeFlowStatus(status);
  const m = STATUS_META[normalized] || { label: status || "-", tone: "neutral", color: "var(--text-secondary)", dot: "·" };
  const label = m.label;
  return (
    <Pill tone={m.tone} title={m.label} style={{ minWidth: 0, width: compact ? "100%" : undefined, maxWidth: "100%", boxSizing: "border-box", lineHeight: 1.2 }}>
      <span style={{ flex: "0 0 auto" }}>{m.dot}</span>
      <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>{label}</span>
    </Pill>
  );
}

function CheckPill({ node }) {
  // v8.8.13: 완료 = 초록 · 미확인 = 빨강. 양쪽 다 표시 (이전에는 false 일 때 숨김).
  const checked = !!node.checked;
  const title = checked
    ? `확인 완료 · by ${node.checked_by||"?"} · ${(node.checked_at||"").replace("T"," ").slice(0,16)}`
    : "확인중 (미확인)";
  return (
    <Pill tone={checked ? "ok" : "warn"} size="md" title={title}>{checked ? "✓ 확인완료" : "○ 확인중"}</Pill>
  );
}

function AutoGenPill({ node }) {
  if (!node.auto_generated) return null;
  return <Pill tone="info" size="md">⚙️ 자동</Pill>;
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
function hasStValue(v) {
  return v != null && v !== "" && v !== "None" && v !== "null";
}
function stCellBg(val, uniq, pname) {
  if (!hasStValue(val)) return {};
  const pn = (pname || "").toUpperCase();
  if (!ST_COLOR_PREFIXES.some(p => pn.startsWith(p + "_"))) return {};
  const s = String(val);
  const idx = uniq[pn]?.[s];
  if (idx != null) { const c = ST_CELL_COLORS[idx % ST_CELL_COLORS.length]; return { background: c.bg, color: c.fg }; }
  return {};
}

function EmbedTableView({ embed, product, canEdit = false, onRemoveSet, showTitle = true, showMeta = true }) {
  if (!embed) return null;
  const shellStyle = { marginTop: 8, padding: 10, border: "1px solid var(--border)", borderRadius: 6, background: "var(--bg-primary)", maxWidth: "100%" };
  const scrollerStyle = { maxHeight: 620, overflow: "auto", border: "1px solid var(--border)", borderRadius: 4, background: "var(--bg-card)" };
  const tableStyle = { borderCollapse: "collapse", fontSize: 11, fontFamily: "monospace", width: "100%", minWidth: "100%", tableLayout: "fixed" };
  const leftHeadStyle = { border: "1px solid var(--border)", padding: "3px 4px", background: "var(--bg-secondary)", textAlign: "left", position: "sticky", top: 0, left: 0, zIndex: 3, width: 170, maxWidth: 170, lineHeight: 1.25, wordBreak: "break-all" };
  const headStyle = { border: "1px solid var(--border)", padding: "3px 4px", background: "var(--bg-secondary)", textAlign: "center", position: "sticky", top: 0, lineHeight: 1.25, zIndex: 2, wordBreak: "break-all" };
  const subHeadLeftStyle = { border: "1px solid var(--border)", padding: "3px 4px", background: "var(--bg-tertiary)", fontSize: 11, color: "var(--text-secondary)", textAlign: "left", position: "sticky", top: 34, zIndex: 2, width: 170, maxWidth: 170 };
  const subHeadStyle = { border: "1px solid var(--border)", padding: "3px 4px", background: "var(--bg-tertiary)", fontSize: 11, color: "var(--text-secondary)", textAlign: "center", position: "sticky", top: 34, zIndex: 1, wordBreak: "break-all" };
  const leftCellStyle = { border: "1px solid var(--border)", padding: "2px 3px", background: "var(--bg-secondary)", fontWeight: 700, position: "sticky", left: 0, zIndex: 1, lineHeight: 1.25, wordBreak: "break-all" };
  const cellStyle = { border: "1px solid var(--border)", padding: "2px 3px", textAlign: "center", lineHeight: 1.25, whiteSpace: "normal", wordBreak: "break-all" };
  const attachedSets = Array.isArray(embed.attached_sets) ? embed.attached_sets : [];
  const renderAttachedSets = () => attachedSets.length > 0 && (
    <div style={{ marginTop: 10, display: "grid", gap: 8 }}>
      <div style={{ fontSize: 14, fontWeight: 900, color: "var(--accent)" }}>📋 첨부된 세트</div>
      {attachedSets.map((set, idx) => {
        const cols = Array.isArray(set.columns) ? set.columns : [];
        const rows = Array.isArray(set.rows) ? set.rows : [];
        return (
          <section key={set.id || `${set.name}-${idx}`} style={{ border: "1px solid var(--border)", borderRadius: 8, background: "var(--bg-card)", overflow: "hidden" }}>
            <div style={{ padding: "7px 9px", display: "flex", alignItems: "center", gap: 8, borderBottom: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
              <b style={{ color: "var(--text-primary)" }}>{set.name || "set"}</b>
              <span style={{ color: "var(--text-secondary)", fontFamily: "monospace" }}>{set.source || "set"} · cols {set.columns_count || cols.length || 0} · rows {set.wafer_count || rows.length || 0}</span>
              {canEdit && onRemoveSet && (
                <Btn variant="danger" size="sm" type="button" onClick={() => onRemoveSet(set.id || `${set.name}-${idx}`)} style={{ marginLeft: "auto" }}>
                  x
                </Btn>
              )}
            </div>
            {cols.length > 0 && rows.length > 0 && (
              <div style={{ overflow: cols.length > 30 ? "auto" : "hidden", maxWidth: "100%" }}>
                <table style={{ ...tableStyle, width: cols.length > 30 ? "max-content" : "100%", minWidth: "100%" }}>
                  <thead>
                    <tr>{cols.map((c, i) => <th key={i} style={{ ...headStyle, textAlign: "left" }}>{c}</th>)}</tr>
                  </thead>
                  <tbody>
                    {rows.slice(0, 80).map((row, ri) => (
                      <tr key={ri}>{row.slice(0, cols.length).map((v, ci) => <td key={ci} style={{ ...cellStyle, textAlign: "left" }}>{v}</td>)}</tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {cols.length > 0 && rows.length === 0 && (
              <div style={{ padding: 9, display: "flex", gap: 5, flexWrap: "wrap", background: "var(--bg-primary)" }}>
                {cols.slice(0, 80).map(c => (
                  <span key={c} style={{ padding: "2px 6px", borderRadius: 6, border: "1px solid var(--border)", color: "var(--text-secondary)", fontFamily: "monospace", fontSize: 11 }}>
                    {c}
                  </span>
                ))}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
  // v8.8.11: st_view(SplitTable /view 응답) 는 공통 SplitTableSnapshotView 로 렌더한다.
  if (embed.st_view && embed.st_view.headers && embed.st_view.rows) {
    return <SplitTableSnapshotView embed={embed} product={product} footer={renderAttachedSets()} showTitle={showTitle} showMeta={showMeta} />;
  }
  // legacy 2D rows 모드 — columns=[parameter, #1, #2, ...] + rows=[[param, v1, ...], ...].
  // v8.8.13: legacy 도 SplitTable 팔레트로 컬러링. columns[0] 이 parameter/param 류면 st_view 로 변환 후 같은 렌더.
  if ((!embed.columns?.length && !embed.rows?.length)) return renderAttachedSets() || null;
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
        {showTitle && (
          <div style={{ fontSize: 14, fontWeight: 700, color: "var(--accent)", marginBottom: 4 }}>
            🔗 SplitTable {embed.source && <span style={{ color: "var(--text-secondary)", fontWeight: 500 }}>· {embed.source}</span>}
          </div>
        )}
        {showMeta && embed.note && <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 4 }}>{embed.note}</div>}
        {showMeta && <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 6 }}>최대 15줄 이상을 한 화면에서 검토할 수 있도록 확장 표시됩니다.</div>}
        <div style={scrollerStyle}>
          <table style={{ ...tableStyle, width: headers.length > 30 ? "max-content" : "100%" }}>
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
        {renderAttachedSets()}
      </div>
    );
  }
  // legacy (non-param-table) 그대로.
  return (
    <div style={shellStyle}>
      {showTitle && (
        <div style={{ fontSize: 14, fontWeight: 700, color: "var(--accent)", marginBottom: 4 }}>
          🔗 Embed {embed.source && <span style={{ color: "var(--text-secondary)", fontWeight: 500 }}>· {embed.source}</span>}
        </div>
      )}
      {showMeta && embed.note && <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 4 }}>{embed.note}</div>}
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
      {renderAttachedSets()}
    </div>
  );
}

/* 재귀 스레드 노드 */
function ThreadNode({
  node, childrenByParent, onReply, onDelete, onToggleCheck, onEdit, onReInform, user,
  depth = 0, constants,
}) {
  const [replyOpen, setReplyOpen] = useState(false);
  // v8.8.13: 답글의 module 은 부모에서 자동 상속. UI 에선 읽기전용으로 표시.
  const [reply, setReply] = useState({ module: node.module || "", reason: node.reason || "", text: "" });
  const [attachSplit, setAttachSplit] = useState(false);
  const [splitForm, setSplitForm] = useState({ column: "", old_value: "", new_value: "" });
  const [replyImages, setReplyImages] = useState([]);
  const [uploading, setUploading] = useState(false);
  const canReInform = !!onReInform;

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
        toast.error("업로드 실패: " + e.message);
      }
    }
    setReplyImages((prev) => [...prev, ...uploaded]);
    setUploading(false);
  };
  const canDelete = user && (user.role === "admin" || user.username === node.author);
  const kids = childrenByParent[node.id] || [];
  const indent = Math.min(depth, 5) * 28;
  const displayText = reInformTextForDisplay(node);

  const sc = node.splittable_change;

  return (
    <div style={{
      marginLeft: indent,
      borderLeft: depth > 0 ? "2px solid var(--border)" : "none",
      paddingLeft: depth > 0 ? 10 : 0,
    }}>
      <div style={{
        padding: depth === 0 ? "0 0 12px" : "10px 0",
        borderTop: depth > 0 ? "1px solid var(--border)" : "none",
        background: "transparent",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4, flexWrap: "wrap" }}>
          {node.module && (() => { const mc = moduleColor(node.module); return (
            <span style={{ fontSize: 14, padding: "2px 8px", borderRadius: 999, background: mc + "22", color: mc, fontWeight: 700, border: "1px solid " + mc + "55" }}>{node.module}</span>
          ); })()}
          <CheckPill node={node} />
          <AutoGenPill node={node} />
          <span style={{ fontSize: 14, fontWeight: 600 }}>{node.author}</span>
          {node.parent_id && (
            <span style={{ fontSize: 14, color: "var(--accent)", fontWeight: 900 }}>↳ [RE]</span>
          )}
          <span title={node.created_at || ""} style={{
            fontSize: 14, padding: "2px 8px", borderRadius: 999,
            background: "var(--bg-primary)", color: "var(--text-primary)",
            border: "1px solid var(--border)", fontFamily: "monospace",
            display: "inline-flex", alignItems: "center", gap: 4,
          }}>🕐 {(node.created_at || "").replace("T", " ").slice(0, 16)}</span>
          <div style={{ flex: 1 }} />
          {/* v8.8.13: 우측 액션은 확인 · 답글 · 재인폼 · 삭제. 상태 라벨은 CheckPill 로 좌측에 표시. */}
          <button onClick={() => onToggleCheck(node)} title={node.checked ? "미확인으로 되돌리기" : "확인 완료 처리"}
            style={{ fontSize: 14, padding: "2px 8px", borderRadius: 4, cursor: "pointer",
              border: "1px solid " + (node.checked ? BAD.fg : OK.fg),
              background: node.checked ? "transparent" : OK.fg,
              color: node.checked ? BAD.fg : "#fff", fontWeight: 700 }}>
            {node.checked ? "↺ 미확인" : "✓ 확인"}
          </button>
          <button onClick={() => setReplyOpen(!replyOpen)} title="답글 달기 (module 은 부모 자동 상속)"
            style={{ fontSize: 14, padding: "2px 8px", borderRadius: 4, cursor: "pointer",
              border: "1px solid var(--accent)", background: "transparent", color: "var(--accent)", fontWeight: 700 }}>
            {replyOpen ? "닫기" : "답글"}
          </button>
          {canReInform && (
            <button onClick={() => onReInform?.(node)}
              title="재인폼 작성"
              style={{ fontSize: 14, padding: "2px 8px", borderRadius: 4, cursor: "pointer",
                border: `1px solid ${INFO.fg}`, background: "transparent", color: INFO.fg, fontWeight: 700 }}>
              재인폼
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

        {node.text_format === "html"
          ? <RichBoardContent html={displayText} style={{ fontSize: 14, color: "var(--text-primary)" }} />
          : <div style={{ fontSize: 14, color: "var(--text-primary)", whiteSpace: "pre-wrap", lineHeight: 1.5 }}>{displayText}</div>}
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
              {sc.applied && <span style={{ marginLeft: 8, fontSize: 14, color: OK.fg, fontWeight: 700 }}>APPLIED</span>}
            </div>
          </div>
        )}

        {replyOpen && (
          <div style={{ marginTop: 8, paddingTop: 8, borderTop: "1px dashed var(--border)" }}>
            {/* v8.8.13: 답글의 module 은 부모 자동 상속 → 읽기전용 pill. */}
            <div style={{ display: "flex", gap: 6, marginBottom: 6, alignItems: "center", flexWrap: "wrap", fontSize: 14, color: "var(--text-secondary)" }}>
              <span>상속:</span>
              {(() => { const mc = moduleColor(reply.module || "—"); return (
                <span style={{ fontSize: 14, padding: "2px 8px", borderRadius: 999, background: mc + "22", color: mc, fontWeight: 700, border: "1px solid " + mc + "55" }}>{reply.module || "(모듈 없음)"}</span>
              ); })()}
              <label style={{ fontSize: 14, color: "var(--text-secondary)", display: "inline-flex", alignItems: "center", gap: 4, cursor: "pointer", marginLeft: "auto" }}>
                <input type="checkbox" checked={attachSplit} onChange={e => setAttachSplit(e.target.checked)} />
                SplitTable 변경요청 포함
              </label>
            </div>
            <textarea value={reply.text} onChange={e => setReply({ ...reply, text: e.target.value })} rows={2}
              placeholder="내용 (추가 조치, 확인 요청 등)"
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
                const txt = withRePrefix(replyText);
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
                style={{ padding: "5px 14px", borderRadius: 4, border: "none", background: "var(--accent)", color: "#fff", fontSize: 14, fontWeight: 600, cursor: "pointer" }}>등록</button>
              <button onClick={() => setReplyOpen(false)}
                style={{ padding: "5px 10px", borderRadius: 4, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", fontSize: 14, cursor: "pointer" }}>취소</button>
            </div>
          </div>
        )}
      </div>
      {kids.map(k => (
        <ThreadNode key={k.id} node={k} childrenByParent={childrenByParent}
          onReply={onReply} onDelete={onDelete} onToggleCheck={onToggleCheck}
          onEdit={onEdit} onReInform={onReInform}
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
          style={{ fontSize: 14, padding: "2px 8px", borderRadius: 3, border: "none", background: "var(--accent)", color: "#fff", cursor: "pointer" }}>저장</button>
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

function MailDialogPreviewPanel({ preview, subject, effectiveEmailCount, inlineImages, attachments, onToggleAttach }) {
  return (
    <>
      {preview?.html_body && (
        <details style={{ marginBottom: 10, border: "1px solid var(--border)", borderRadius: 5, padding: "4px 10px", background: "var(--bg-card)" }} open>
          <summary style={{ fontSize: 14, fontWeight: 600, cursor: "pointer", color: "var(--accent)" }}>
            👁️ 메일 미리보기 · 제목 [{subject || preview.subject || "자동"}] · 수신자 {effectiveEmailCount}명
          </summary>
          <div style={{ marginTop: 6, marginBottom: 6, display: "flex", gap: 8, flexWrap: "wrap", fontSize: 14 }}>
            <Pill tone="info" size="md">본문 {formatBytes(preview.html_size_bytes)}</Pill>
            <Pill tone="ok" size="md">자동 첨부 {formatBytes(preview.attachment_total_bytes)}</Pill>
            <Pill tone="warn" size="md">SplitTable xlsx {(preview.auto_attachments || []).length}개</Pill>
          </div>
          <div style={{ minHeight: "60vh", maxHeight: "70vh", overflowY: "auto", overflowX: "hidden", width: "100%", background: "var(--bg-secondary)", color: "var(--text-primary)", padding: 10, border: "1px solid var(--border)", borderRadius: 4 }}
               dangerouslySetInnerHTML={{ __html: sanitizeHtml(preview.html_body) }} />
        </details>
      )}
      {(preview?.mail_over_limit || preview?.html_over_limit) && (
        <div style={{ marginBottom: 8, padding: "6px 10px", border: `1px solid ${BAD.line}`, background: BAD.bg, borderRadius: 4, color: BAD.fg, fontSize: 14 }}>
          ⚠ 메일 총 용량 {preview.mail_total_kb ?? preview.html_size_kb}KB — 한도 {preview.mail_limit_mb ?? 2}MB 초과. 사진/SplitTable 컬럼을 줄여야 발송할 수 있습니다.
        </div>
      )}
      {preview && preview.html_size_kb != null && !preview.mail_over_limit && !preview.html_over_limit && (
        <div style={{ marginBottom: 8, fontSize: 14, color: "var(--text-secondary)" }}>
          📦 메일 용량: {preview.mail_total_kb ?? preview.html_size_kb}KB / {preview.mail_limit_mb ?? 2}MB
        </div>
      )}
      {inlineImages.length > 0 && <div style={{ marginBottom: 10 }}>
        <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 3 }}>📎 첨부 이미지 <span style={{ fontWeight: 400, color: "var(--text-secondary)" }}>(각 파일 10MB 한도 · 총합 제한)</span></div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {inlineImages.map(img => {
            const on = attachments.includes(img.url);
            return <span key={img.url} onClick={() => onToggleAttach(img.url)} style={{
              padding: "4px 10px", borderRadius: 4, fontSize: 14,
              background: on ? OK.bg : "var(--bg-card)",
              color: on ? OK.fg : "var(--text-primary)",
              border: "1px solid " + (on ? OK.line : "var(--border)"),
              cursor: "pointer",
            }}>{on ? "✔" : "＋"} {img.filename || img.url.split("/").pop()}</span>;
          })}
        </div>
      </div>}
      {preview?.auto_attachments?.length > 0 && (
        <div style={{ marginBottom: 10, padding: 8, borderRadius: 5, background: OK.bg, border: `1px solid ${OK.line}` }}>
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
    </>
  );
}

/* 루트 인폼 머리에 붙는 상태 패널 (flow 진행 + 이력) */
function MailDialog({ root, user, reasonTemplates, onClose, initialSelection }) {
  // v8.7.2: 인폼 → 사내 메일 API 로 HTML 본문 전송 (multipart).
  // v8.8.3: 공용 메일그룹(/api/mail-groups/list) 도 함께 노출 — 만들어진 그룹이 드롭다운에 안 뜨던 문제 해결.
  //          + 새 그룹 관리 서브 모달(z-index 10001 로 메일 다이얼로그 위에 올라오게).
  // 메일 기본값은 서버 미리보기와 동일하게 reason 표시 없이 생성한다.
  const [recipients, setRecipients] = useState([]);
  const [groups, setGroups] = useState({});          // {groupName: [emails]}
  const [publicGroups, setPublicGroups] = useState([]); // v8.8.3: 공용 메일 그룹 목록 [{id,name,members,extra_emails}]
  const mailPrefill = initialSelection || root?.__mail_prefill || root?.mail_draft || {};
  const [pickedUsers, setPickedUsers] = useState(() => Array.isArray(mailPrefill.to_users) ? mailPrefill.to_users : []);   // usernames
  const [pickedGroups, setPickedGroups] = useState(() => Array.isArray(mailPrefill.groups) ? mailPrefill.groups : []); // group names
  const _defSubject = String(mailPrefill.subject || "");
  const _defBody = String(mailPrefill.body || "");
  const [subject, setSubject] = useState(_defSubject);
  const [body, setBody] = useState(_defBody);
  const [statusCode, setStatusCode] = useState("");
  // v8.8.30: 스레드(답글) 포함 옵션 제거 — 본문에 스레드를 넣지 않는다.
  //   이유: 인폼 자체가 대화 스레드인데 메일 본문에 다시 넣으면 중복/용량 증가.
  //   필요 시 수신자가 인폼 페이지에서 직접 열람 (첨부 xlsx 로도 핵심은 전달).
  const includeThread = false;
  const [extraEmails, setExtraEmails] = useState(() => {
    const extras = Array.isArray(mailPrefill.extra_emails) ? mailPrefill.extra_emails : [];
    const fallbackTo = !mailPrefill.to_users?.length && !mailPrefill.groups?.length && Array.isArray(mailPrefill.to)
      ? mailPrefill.to
      : [];
    if (fallbackTo.length) return uniqueClean([...extras, ...fallbackTo]).join(", ");
    return extras.join(", ");
  });
  const [attachments, setAttachments] = useState([]); // inform image URLs to include
  const [filter, setFilter] = useState("");
  const [sending, setSending] = useState(false);
  const sendingRef = useRef(false);
  const [sent, setSent] = useState(null);
  const [error, setError] = useState("");
  const [showMgr, setShowMgr] = useState(false);  // v8.8.3: 공용 메일 그룹 관리 서브모달
  const [newGroupName, setNewGroupName] = useState("");
  const [newGroupEmails, setNewGroupEmails] = useState("");
  // v8.8.21: 실시간 메일 프리뷰 — body 바뀔 때마다 debounce 후 fetch.
  const [preview, setPreview] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(true);
  useEffect(() => {
    if (!root?.id) return;
    setPreviewLoading(true);
    const h = setTimeout(() => {
      const q = new URLSearchParams();
      q.set("body", body || "");
      q.set("subject", subject || "");
      computedEmails().forEach(em => q.append("to", em));
      pickedUsers.forEach(un => q.append("to_users", un));
      pickedGroups.forEach(g => q.append("groups", g));
      attachments.forEach(url => q.append("attachments", url));
      sf(API + "/" + encodeURIComponent(root.id) + "/mail-preview?" + q.toString())
        .then(d => setPreview(d)).catch(() => setPreview(null)).finally(() => setPreviewLoading(false));
    }, 250);
    return () => clearTimeout(h);
  }, [root?.id, body, subject, pickedUsers, pickedGroups, extraEmails, attachments, recipients, groups, publicGroups]);

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
      const r = recipients.find(r => r.username === un);
      const em = r?.effective_email || r?.email;
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
    if (sendingRef.current) return;
    setError(""); setSent(null);
    if (previewLoading || !preview) { setError("메일 전체 용량을 계산 중입니다. 잠시 후 다시 시도해 주세요."); return; }
    const to = computedEmails();
    if (to.length === 0 && !(root.module || "").trim()) { setError("수신자를 선택하거나 인폼 모듈을 지정하세요."); return; }
    if (to.length > 199) { setError(`수신자는 최대 199명입니다 (현재 ${to.length}명).`); return; }
    if (preview?.mail_over_limit) {
      setError(`메일 총 용량이 한도(${preview.mail_limit_mb}MB)를 초과했습니다 (${preview.mail_total_kb}KB). 사진이나 첨부를 줄여주세요.`);
      return;
    }
    sendingRef.current = true;
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
    }).finally(() => { sendingRef.current = false; setSending(false); });
  };

  return (
    <Modal open onClose={onClose} width={1180} zIndex={9999}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <div style={{ fontSize: 15, fontWeight: 700 }}>✉ 인폼 메일 보내기 <span style={{ fontSize: 14, fontWeight: 400, color: "var(--text-secondary)" }}>(최대 199명 · 본문 2MB · 첨부 10MB)</span></div>
          <span onClick={onClose} style={{ cursor: "pointer", fontSize: 18 }}>✕</span>
        </div>
        <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 8 }}>Admin 설정의 메일 API 로 multipart POST. 수신자 총 <b style={{ color: "var(--accent)" }}>{effectiveEmailCount}명</b> · Inform <code>{root.id}</code></div>
        {/* v8.8.1: 발송자 ID 자동 명시 제거. 제품 담당자 라인만 본문 상단에 삽입. */}
        <div style={{ fontSize: 14, padding: "6px 10px", marginBottom: 10, borderRadius: 4, background: INFO.bg, border: `1px solid ${INFO.line}`, color: INFO.fg }}>
          📨 발송계정: 시스템(Admin) · 본문 상단에 <b>제품 담당자</b> 라인 자동 삽입 (해당 제품에 등록된 담당자 있을 때).
        </div>

        {/* v8.8.3: Module recipient groups — admin 그룹 + 공용 메일그룹 합집합. 만들어진 그룹도 노출. */}
        <div style={{ marginBottom: 10 }}>
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 4, display: "flex", alignItems: "center", gap: 6 }}>
            <span>📮 메일 그룹 <span style={{ fontWeight: 400, color: "var(--text-secondary)" }}>({pickedGroups.length} 선택 · {allGroupNames.length} 가용)</span></span>
            <span style={{ flex: 1 }} />
            <Btn size="sm" type="button" onClick={() => setShowMgr(true)}>관리</Btn>
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
                    color: on ? "#fff" : "var(--text-primary)",
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
          <Modal open onClose={() => setShowMgr(false)} width={560} zIndex={10001}>
              <div style={{ display: "flex", alignItems: "center", marginBottom: 10 }}>
                <div style={{ fontSize: 14, fontWeight: 700 }}>📮 공용 메일 그룹 관리</div>
                <span style={{ flex: 1 }} />
                <span onClick={() => setShowMgr(false)} style={{ cursor: "pointer", fontSize: 16 }}>✕</span>
              </div>
              <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 8 }}>
                모든 로그인 유저가 공용으로 사용하는 메일 그룹 (inform / meeting 공용). 이름 + 이메일 콤마/세미콜론 구분으로 입력하면 바로 생성됩니다.
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 2fr", gap: 6, marginBottom: 8 }}>
                <Input value={newGroupName} onChange={e => setNewGroupName(e.target.value)}
                  placeholder="그룹 이름" />
                <Input value={newGroupEmails} onChange={e => setNewGroupEmails(e.target.value)}
                  placeholder="member1@x.com, member2@y.com" style={{ fontFamily: "monospace" }} />
              </div>
              <div style={{ display: "flex", gap: 6, marginBottom: 12 }}>
                <Btn variant="primary" type="button" onClick={() => {
                  const nm = (newGroupName || "").trim();
                  if (!nm) { toast.warn("그룹 이름을 입력하세요"); return; }
                  const extras = (newGroupEmails || "").split(/[,\s;]+/).map(s => s.trim()).filter(s => s && s.includes("@"));
                  postJson("/api/mail-groups/create", { name: nm, extra_emails: extras, members: [] })
                    .then(() => { setNewGroupName(""); setNewGroupEmails(""); reloadGroups(); toast.ok("그룹 생성됨"); })
                    .catch(e => toast.error(e.message));
                }}>+ 그룹 생성</Btn>
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
                        .catch(e => toast.error(e.message));
                    }} style={{ cursor: "pointer", color: BAD.fg, fontSize: 14, fontWeight: 600 }}>삭제</span>
                  </div>
                ))}
              </div>
              <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 10 }}>
                <Btn type="button" onClick={() => setShowMgr(false)}>닫기</Btn>
              </div>
          </Modal>
        )}

        {/* Individual recipient picker */}
        <div style={{ marginBottom: 10 }}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4, fontSize: 14, fontWeight: 600 }}>
            <span>개별 유저 ({pickedUsers.length} 선택)</span>
            <Input value={filter} onChange={e => setFilter(e.target.value)} placeholder="🔎 유저/이메일 검색" style={{ width: 200 }} />
          </div>
          <div style={{ maxHeight: 140, overflow: "auto", border: "1px solid var(--border)", borderRadius: 6, background: "var(--bg-card)" }}>
            {visibleList.length === 0 && <div style={{ padding: 14, textAlign: "center", fontSize: 14, color: "var(--text-secondary)" }}>유저가 없습니다.</div>}
            {/* 이름(실명) + username 동시 표시 — 동명이인 구분. email 미설정 유저도 flow-data/users.csv 기준으로 검색 후보에 노출. */}
            {visibleList.map(r => {
              const on = pickedUsers.includes(r.username);
              const nm = (r.name || "").trim();
              const em = r.effective_email || r.email || "";
              return (
                <div key={r.username} onClick={() => toggleUser(r.username)} style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 10px", fontSize: 14, cursor: "pointer", background: on ? "var(--info-50)" : "transparent", borderBottom: "1px solid var(--border)" }}>
                  <input type="checkbox" checked={on} readOnly />
                  {nm
                    ? <><span style={{ fontWeight: 600 }}>{nm}</span>
                        <span style={{ fontSize: 14, color: "var(--text-secondary)", fontFamily: "monospace" }}>({r.username})</span></>
                    : <span style={{ fontWeight: 600 }}>{r.username}</span>}
                  <span style={{ marginLeft: "auto", fontSize: 14, color: em ? "var(--text-secondary)" : WARN.fg, fontFamily: "monospace" }}>
                    {em || "email 미설정"}
                  </span>
                </div>
              );
            })}
          </div>
        </div>

        <div style={{ marginBottom: 10 }}>
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 3 }}>추가 이메일 <span style={{ fontWeight: 400, color: "var(--text-secondary)" }}>(콤마/공백/세미콜론 구분)</span></div>
          <Input value={extraEmails} onChange={e => setExtraEmails(e.target.value)} placeholder="ext1@vendor.com, ext2@vendor.com" style={{ width: "100%", fontFamily: "monospace" }} />
        </div>

        {/* v8.8.1: statusCode 등 백엔드 전용 필드는 UI 에서 제거 — admin 기본값으로 자동 주입됨. */}
        <div style={{ marginBottom: 8 }}>
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 3 }}>제목</div>
          <Input value={subject} onChange={e => setSubject(e.target.value)} placeholder={preview?.subject || "비워두면 [인폼] 제품 lot 모듈 split table 로 자동 생성"} style={{ width: "100%" }} />
        </div>
        <div style={{ marginBottom: 8 }}>
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 3 }}>본문 <span style={{ fontWeight: 400, color: "var(--text-secondary)" }}>(비워두면 인폼 note만 사용)</span></div>
          <Textarea value={body} onChange={e => setBody(e.target.value)} rows={4} placeholder="비워두면 인폼 note를 그대로 사용합니다." style={{ width: "100%" }} />
          {preview?.owners_line && (
            <div style={{ marginTop: 4, fontSize: 14, color: OK.fg, background: OK.bg, border: `1px solid ${OK.line}`, borderRadius: 4, padding: "4px 8px" }}>
              📌 자동 삽입: <b>제품담당자</b> : {preview.owners_line}
            </div>
          )}
          {preview?.auto_module_used && (preview.auto_module_recipients || []).length > 0 && (
            <div style={{ marginTop: 4, fontSize: 14, color: INFO.fg, background: INFO.bg, border: `1px solid ${INFO.line}`, borderRadius: 4, padding: "4px 8px" }}>
              자동 수신: {(preview.auto_module_recipients || []).map(r => `${r.username} <${r.email}>`).join(", ")}
            </div>
          )}
        </div>
        <MailDialogPreviewPanel
          preview={preview}
          subject={subject}
          effectiveEmailCount={effectiveEmailCount}
          inlineImages={inlineImages}
          attachments={attachments}
          onToggleAttach={toggleAttach}
        />

        {error && <div style={{ padding: "6px 10px", background: BAD.bg, color: BAD.fg, border: `1px solid ${BAD.line}`, borderRadius: 4, fontSize: 14, marginBottom: 8 }}>⚠ {error}</div>}
        {sending && <div style={{ padding: "6px 10px", background: INFO.bg, color: INFO.fg, border: `1px solid ${INFO.line}`, borderRadius: 4, fontSize: 14, marginBottom: 8 }}><Loading text="메일 전송 중..." size="sm" /></div>}
        {sent && <div style={{ padding: "6px 10px", background: OK.bg, color: OK.fg, border: `1px solid ${OK.line}`, borderRadius: 4, fontSize: 14, marginBottom: 8 }}>✔ 전송됨 ({(sent.to || []).length}명){sent.dry_run && " · DRY RUN (실제 전송 안됨)"}</div>}

        <div style={{ display: "flex", gap: 8 }}>
          <Btn variant="primary" disabled={sending || previewLoading || !preview || !!preview?.mail_over_limit} onClick={doSend}
            title={preview?.mail_over_limit ? "메일 전체 용량 한도를 초과해 발송할 수 없습니다" : ""}
            style={{ cursor: sending || previewLoading || !preview || preview?.mail_over_limit ? "not-allowed" : undefined }}>
            {sending ? "전송 중…" : (previewLoading ? "용량 계산 중…" : `📧 ${effectiveEmailCount}명에게 전송`)}
          </Btn>
          <Btn variant="ghost" onClick={onClose}>닫기</Btn>
        </div>
    </Modal>
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
  const isCompleted = normalizeFlowStatus(root.flow_status, root) === "apply_confirmed";
  const toggleDone = () => {
    const next = isCompleted ? "registered" : "apply_confirmed";
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
        style={{ padding: "2px 8px", borderRadius: 4, border: "1px solid var(--brand-line)",
                 background: "var(--brand-50)", color: "var(--brand)",
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
      background: WARN.bg, border: `1px solid ${WARN.line}`,
      borderRadius: 8, padding: 10, marginBottom: 10,
    }}>
      <div style={{ fontSize: 14, fontWeight: 700, color: WARN.fg, marginBottom: 6 }}>
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
            <span style={{ color: OK.fg, fontWeight: 700 }}>{sc.new_value || "-"}</span>
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
          <span style={{ fontSize: 14, fontWeight: 700, padding: "1px 6px", borderRadius: 8, background: color, color: "#fff" }}>{label}</span>
          <span style={{ fontSize: 14, color: "var(--text-secondary)", fontFamily: "monospace" }}>
            {n.username} · {(n.created_at || "").replace("T", " ").slice(0, 16)}
          </span>
        </div>
        <div style={{ fontSize: 14, whiteSpace: "pre-wrap", lineHeight: 1.45 }}>{n.text}</div>
      </div>
    );
  };
  return (
    <div style={{ background: INFO.bg, border: `1px solid ${INFO.line}`, borderRadius: 8, padding: 10, marginBottom: 10 }}>
      <div style={{ fontSize: 14, fontWeight: 700, color: INFO.fg, marginBottom: 6 }}>
        📝 SplitTable 노트 — root_lot_id <span style={{ fontFamily: "monospace" }}>{root_lot_id}</span> ({notes.length}건)
        <span style={{ fontSize: 14, fontWeight: 500, marginLeft: 8, color: "var(--text-secondary)" }}>
          wafer {wafers.length} · param {params.length} · lot {lots.length} · 전역 {pgs.length}
        </span>
      </div>
      {wafers.map(n => renderRow(n, "wafer", INFO.fg))}
      {params.map(n => renderRow(n, "param", statusPalette.violet.fg))}
      {lots.map(n => renderRow(n, "lot", chartPalette.series[13]))}
      {pgs.map(n => renderRow(n, "param_global", chartPalette.series[11]))}
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
      if (normalizeFlowStatus(e.flow_status, e) === "apply_confirmed") {
        const hist = (e.status_history || []).filter(h => normalizeFlowStatus(h.status) === "apply_confirmed");
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
                {done && <span style={{ color: OK.fg }}>확인</span>}
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
  const [informView, setInformView] = useState("list");
  const [auditRows, setAuditRows] = useState([]);
  const [auditLoading, setAuditLoading] = useState(false);

  // 제품 후보는 backend LOT progress cache 기준으로 채워진다.
  const [constants, setConstants] = useState({ modules: [], reasons: [], flow_statuses: [], products: [], raw_db_root: "", reason_templates: {}, mail_links: [], mail_max_mb: 2 });
  // v8.8.1: 선택 제품의 Lot 후보 (RAWDATA_DB 에서 폴더 스캔).
  const [productLots, setProductLots] = useState({ product: "", lots: [], source: "" });
  const [mode, setMode] = useState("all");           // all | mine | product | lot | wafer

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
  const [listChildrenByParent, setListChildrenByParent] = useState({});
  const [lotMatrix, setLotMatrix] = useState({ products: [], module_order: [] });
  const [lotMatrixLoading, setLotMatrixLoading] = useState(false);
  const [selectedRootId, setSelectedRootId] = useState("");
  const [detailTab, setDetailTab] = useState("body");
  const [wizardStep, setWizardStep] = useState(0);
  const [wizardAttachMode, setWizardAttachMode] = useState("sets");
  const [wizardSelectedSetIds, setWizardSelectedSetIds] = useState([]);
  const [wizardMailDraft, setWizardMailDraft] = useState({ subject: "", body: "", generatedFor: "" });
  const [wizardMailMeta, setWizardMailMeta] = useState({ recipients: [], knobMap: {} });
  const wizardMailMetaRef = useRef({ recipients: [], knobMap: {} });
  const [mailDialogRoot, setMailDialogRoot] = useState(null);

  const [creating, setCreating] = useState(false);
  const [informSubmitting, setInformSubmitting] = useState(false);
  const informSubmittingRef = useRef(false);
  const [wizardMode, setWizardMode] = useState("create");
  const [reInformParent, setReInformParent] = useState(null);
  const [form, setForm] = useState(defaultInformForm);
  const [createImages, setCreateImages] = useState([]);
  const [uploadingMain, setUploadingMain] = useState(false);
  const [embedFetching, setEmbedFetching] = useState(false);
  const [msg, setMsg] = useState("");

  const [moduleFilter, setModuleFilter] = useState([]);  // 체크된 모듈만 표시 (빈 배열=모두 해제)
  // v8.8.15: 제품 필터 nav — 체크된 제품만 통과. 빈 배열은 "모두 해제"라서 목록을 비운다.
  const [productFilter, setProductFilter] = useState([]);
  // moduleFilter 기본 = 모든 모듈. 이후엔 사용자 체크 토글이 우선.
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

  const setWizardMailMetaSynced = (next) => {
    if (typeof next === "function") {
      setWizardMailMeta((prev) => {
        const raw = next(prev) || {};
        const resolved = raw && typeof raw === "object" ? raw : { recipients: [], knobMap: {} };
        wizardMailMetaRef.current = resolved;
        return resolved;
      });
      return;
    }
    const resolved = next && typeof next === "object" ? next : { recipients: [], knobMap: {} };
    wizardMailMetaRef.current = resolved;
    setWizardMailMeta(resolved);
  };

  const resolveWizardMailMetaForSubmit = () => {
    const live = wizardMailMetaRef.current && typeof wizardMailMetaRef.current === "object" ? wizardMailMetaRef.current : {};
    const selectedTo = uniqueClean(
      (Array.isArray(live.to) && live.to.length) ? live.to : (Array.isArray(live.recipients) ? live.recipients : [])
    );
    return {
      to: selectedTo,
      recipients: selectedTo,
      to_users: uniqueClean(Array.isArray(live.to_users) ? live.to_users : []),
      groups: uniqueClean(Array.isArray(live.groups) ? live.groups : []),
      extra_emails: uniqueClean(Array.isArray(live.extra_emails) ? live.extra_emails : []),
      subject: String(live.subject || wizardMailDraft?.subject || "").trim(),
      body: String(live.body || wizardMailDraft?.body || form.text || "").trim(),
      __hydrated: Boolean(live.__hydrated),
    };
  };

  useEffect(() => {
    wizardMailMetaRef.current = wizardMailMeta && typeof wizardMailMeta === "object"
      ? wizardMailMeta
      : { recipients: [], knobMap: {} };
  }, [wizardMailMeta]);

  const canManageInform = canManagePage(user, "inform");
  const loadLotMatrix = () => {
    const q = new URLSearchParams();
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
    sf(API + "/recent?limit=500&include_children=true")
      .then(d => {
        setListRoots(d.informs || []);
        setListChildrenByParent(d.children_by_parent || {});
      })
      .catch(() => {
        setListRoots([]);
        setListChildrenByParent({});
      });
  };

  const loadAuditLog = () => {
    const q = new URLSearchParams();
    uniqueClean(sharedFilters.products).forEach(v => q.append("products[]", v));
    uniqueClean(sharedFilters.modules).forEach(v => q.append("modules[]", v));
    uniqueClean(sharedFilters.types).forEach(v => q.append("types[]", v));
    if ((sharedFilters.lot || "").trim()) q.set("lot_search", sharedFilters.lot.trim());
    setAuditLoading(true);
    return sf(API + "/audit-log?" + q.toString())
      .then(d => setAuditRows(d.audit || d.logs || []))
      .catch(() => setAuditRows([]))
      .finally(() => setAuditLoading(false));
  };

  const loadDetailForRoot = (root, opts = {}) => {
    if (!root) { setThread([]); setLotWafers([]); return; }
    const lotKey = detailLotId(root);
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
    setActiveTab(defaultInformTab());
    setInformView("detail");
    setSelectedRootId(root.id);
    setDetailTab("body");
    loadDetailForRoot(root);
  };

  const openRootForLotModule = (root, module) => {
    const rootLot = detailLotId(root);
    const target = (listRoots || [])
      .filter(x => detailLotId(x) === rootLot && (x.module || "기타") === module)
      .sort((a, b) => String(_entryLastUpdateForUi(b)).localeCompare(String(_entryLastUpdateForUi(a))))[0];
    openRootForDetail(target || root);
  };

  const openAuditRow = (row) => {
    const informId = String(row?.inform_id || row?.target_id || "").trim();
    if (!informId) return;
    setActiveTab(defaultInformTab());
    setInformView("detail");
    setSelectedRootId(informId);
    setDetailTab("body");
    loadDetailForRoot({
      id: informId,
      fab_lot_id: row.fab_lot_id || row.fab_lot_id_at_save || row.lot_id || row.root_lot_id || "",
      root_lot_id: row.root_lot_id || row.lot_id || "",
      lot_id: row.fab_lot_id || row.fab_lot_id_at_save || row.lot_id || row.root_lot_id || "",
      product: row.product || "",
      module: row.module || "",
    }, { includeDeleted: true });
  };

  const refreshWizardOptions = () => {
    Promise.allSettled([
      sf(API + "/config"),
      sf(API + "/modules"),
      sf(API + "/products"),
    ]).then(([configRes, modulesRes, productsRes]) => {
      const cfg = configRes.status === "fulfilled" ? (configRes.value || {}) : {};
      const moduleCfg = modulesRes.status === "fulfilled" ? (modulesRes.value || {}) : {};
      const productRows = productsRes.status === "fulfilled" ? (productsRes.value?.products || []) : [];
      const productOptions = uniqueClean([
        ...(cfg.products || []),
        ...productRows.map(p => typeof p === "string" ? p : p?.product),
      ].map(stripMlPrefix));
      setConstants(c => ({
        ...c,
        modules: (cfg.modules || []).length ? cfg.modules : ((moduleCfg.modules || []).length ? moduleCfg.modules : c.modules),
        reasons: (cfg.reasons || []).length ? cfg.reasons : ((moduleCfg.reasons || []).length ? moduleCfg.reasons : c.reasons),
        flow_statuses: (cfg.flow_statuses || []).length ? cfg.flow_statuses : ((moduleCfg.flow_statuses || []).length ? moduleCfg.flow_statuses : c.flow_statuses),
        products: productOptions.length ? productOptions : c.products,
        raw_db_root: cfg.raw_db_root ?? c.raw_db_root,
        reason_templates: cfg.reason_templates || c.reason_templates,
        mail_links: cfg.mail_links || c.mail_links || [],
        mail_max_mb: cfg.mail_max_mb ?? c.mail_max_mb,
      }));
    });
  };

  const openCreateWizard = () => {
    setWizardMode("create");
    setReInformParent(null);
    setForm(defaultInformForm());
    setCreateImages([]);
    setWizardStep(0);
    setWizardAttachMode("sets");
    setWizardSelectedSetIds([]);
    setEmbedCustomCols([]);
    setEmbedCustomSearch("");
    setSnapshotTick(0);
    setWizardMailDraft({ subject: "", body: "", generatedFor: "" });
    setWizardMailMetaSynced({ recipients: [], knobMap: {} });
    setWizardLotSearch("");
    // config/latest cache 하나가 잠깐 실패해도 product/module을 독립 endpoint에서
    // 보충한다. 신규 latest가 아직 없는 제품은 backend ML_TABLE catalog가 채운다.
    refreshWizardOptions();
    try {
      const raw = localStorage.getItem(WIZARD_DRAFT_KEY);
      if (raw) {
        const draft = JSON.parse(raw);
        if (draft?.form) setForm({ ...defaultInformForm(), ...draft.form });
        if (Array.isArray(draft?.createImages)) setCreateImages(draft.createImages);
        if (typeof draft?.wizardStep === "number") {
          // v1 초안의 4단계 인덱스도 새 3단계 흐름으로 자연스럽게 옮긴다.
          const savedStep = Math.max(0, Math.min(3, draft.wizardStep));
          setWizardStep(draft?.wizardVersion === 2 ? Math.min(2, savedStep) : (savedStep <= 1 ? 0 : savedStep - 1));
        }
        if (draft?.wizardAttachMode) {
          const mode = draft.wizardAttachMode === "auto" ? "sets" : (draft.wizardAttachMode === "custom" ? "knob" : draft.wizardAttachMode);
          setWizardAttachMode(["knob", "sets", "new"].includes(mode) ? mode : "sets");
        }
        if (Array.isArray(draft?.wizardSelectedSetIds)) setWizardSelectedSetIds(draft.wizardSelectedSetIds);
        if (Array.isArray(draft?.embedCustomCols)) setEmbedCustomCols(draft.embedCustomCols);
        if (draft?.wizardMailDraft) setWizardMailDraft(draft.wizardMailDraft);
        if (draft?.wizardMailMeta) setWizardMailMetaSynced(draft.wizardMailMeta);
      }
    } catch (_) {}
    setCreating(true);
  };

  const openReInformWizard = (root) => {
    if (!root?.id) return;
    const inherited = reInformFormFromParent(root);
    const selectedSetIds = reInformSelectedSetIds(root);
    setWizardMode("reinform");
    setReInformParent(root);
    setForm(inherited);
    setCreateImages([]);
    setWizardStep(1);
    setWizardAttachMode("sets");
    setWizardSelectedSetIds(selectedSetIds);
    setEmbedCustomCols([]);
    setEmbedCustomSearch("");
    setSnapshotTick(0);
    setWizardMailDraft({ subject: "", body: "", generatedFor: "" });
    setWizardMailMetaSynced({ recipients: [], knobMap: {} });
    setWizardLotSearch(inherited.lot_id || "");
    setMsg("");
    setCreating(true);
  };

  useEffect(() => {
    let shouldOpen = false;
    try {
      shouldOpen = localStorage.getItem(WIZARD_OPEN_KEY) === "1";
      if (shouldOpen) localStorage.removeItem(WIZARD_OPEN_KEY);
    } catch (_) {}
    try {
      const params = new URLSearchParams(window.location.search);
      shouldOpen = shouldOpen || params.get("create") === "1";
    } catch (_) {}
    if (shouldOpen) openCreateWizard();
  }, []);

  useEffect(() => {
    loadInformList();
    loadAuditLog();
  }, []);

  useEffect(() => {
    loadLotMatrix();
  }, [sharedFilters.products.join("|"), sharedFilters.lot]);

  useEffect(() => {
    loadAuditLog();
  }, [sharedFilters.products.join("|"), sharedFilters.modules.join("|"), sharedFilters.types.join("|"), sharedFilters.lot]);

  useEffect(() => {
    syncInformQuery(activeTab, sharedFilters);
  }, [activeTab, sharedFilters]);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      if (!(activeTab === "inform" && informView === "detail")) return;
      setSelectedRootId("");
      setInformView("list");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [activeTab, informView]);

  /* Load constants */
  useEffect(() => {
    sf(API + "/config").then(d => setConstants({
      modules: d.modules || [], reasons: d.reasons || [], flow_statuses: d.flow_statuses || [],
      products: d.products || [], raw_db_root: d.raw_db_root || "",
      reason_templates: d.reason_templates || {},
      mail_links: d.mail_links || [], mail_max_mb: d.mail_max_mb ?? 2,
    })).catch(() => {
      sf(API + "/modules").then(d => setConstants(c => ({ ...c,
        modules: d.modules || [], reasons: d.reasons || [], flow_statuses: d.flow_statuses || [],
      }))).catch(() => {});
    });
  }, []);

  const moduleFilterOptions = useMemo(() => {
    return constants.modules || [];
  }, [constants.modules]);

  // moduleFilter 기본값 = 전체 모듈 체크. 최초 한 번만.
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
    if (!product || !name) { toast.warn("product/name 필수"); return; }
    const url = id
      ? "/api/informs/product-contacts/update?id=" + encodeURIComponent(id)
      : "/api/informs/product-contacts";
    postJson(url, { product, name, role, email, phone, note })
      .then(() => {
        setEditContact(null);
        loadProductContacts();
      })
      .catch(e => toast.error("저장 실패: " + (e.message || e)));
  };
  const deleteContact = (product, id) => {
    if (!confirm("담당자를 삭제하시겠어요?")) return;
    postJson("/api/informs/product-contacts/delete?id=" + encodeURIComponent(id) + "&product=" + encodeURIComponent(product), {})
      .then(() => loadProductContacts())
      .catch(e => toast.error("삭제 실패: " + (e.message || e)));
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
    if (bulkSelUsers.length === 0 && bulkSelGroups.length === 0) { toast.warn("유저 또는 그룹을 선택하세요."); return; }
    setBulkBusy(true);
    postJson("/api/informs/product-contacts/bulk-add", {
      product: bulkPickProduct,
      usernames: bulkSelUsers,
      group_ids: bulkSelGroups,
      role: bulkRole,
    })
      .then(r => {
        setBulkBusy(false);
        const msg = `추가 ${r.added?.length || 0}명 / 스킵 ${r.skipped?.length || 0}명 (중복/차단).`;
        toast.ok(msg);
        setBulkPickProduct("");
        loadProductContacts();
      })
      .catch(e => { setBulkBusy(false); toast.error("추가 실패: " + (e.message || e)); });
  };

  const refreshAll = () => {
    loadSidebar();
    loadInformList();
    loadLotMatrix();
    loadAuditLog();
    const selected = listRoots.find(x => x.id === selectedRootId) || thread.find(x => x.id === selectedRootId);
    const lotForDetail = detailLotId(selected) || selectedLot;
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

  const create = async () => {
    if (informSubmittingRef.current) {
      setMsg("등록 처리 중입니다.");
      return Promise.reject(new Error("submit pending"));
    }
    const isReInform = wizardMode === "reinform" && !!reInformParent?.id;
    const fabTargets = uniqueClean(form.fab_lot_ids || []);
    const lot = (form.lot_id || "").trim() || (fabTargets[0] || "");
    const submitTargets = isReInform ? [lot] : (fabTargets.length ? fabTargets : [lot]);
    // root_lot 검색 결과에서 여러 LOT_ID를 선택한 경우 미리보기 embed에는
    // root 전체 LOT이 들어 있을 수 있다. 이 embed는 단일 LOT 등록에서만 재사용한다.
    const canReusePreviewSnapshot = submitTargets.length === 1;
    const mailMetaForSubmit = isReInform ? { to: [], recipients: [], to_users: [], groups: [], extra_emails: [] } : resolveWizardMailMetaForSubmit();
    if (!form.product.trim()) { setMsg("product 를 선택해 주세요."); return Promise.reject(new Error("product required")); }
    if (!lot) { setMsg("lot 을 선택해 주세요."); return Promise.reject(new Error("lot required")); }
    if (!form.module) { setMsg("module 을 선택해 주세요."); return Promise.reject(new Error("module required")); }
    if (embedFetching) { setMsg("SplitTable 스냅샷 생성이 끝난 뒤 등록해 주세요."); return Promise.reject(new Error("snapshot pending")); }
    if (!richTextHasContent(form.text) && !(isReInform && form.attach_embed && hasEmbedSnapshot(form.embed))) {
      setMsg("note 를 입력해 주세요."); return Promise.reject(new Error("text required"));
    }
    informSubmittingRef.current = true;
    setInformSubmitting(true);
    setMsg("등록 저장 중...");
    const attachedSetsForSubmit = () => (
      wizardAttachMode === "sets" && Array.isArray(form.embed?.attached_sets)
        ? form.embed.attached_sets.map(s => ({
            id: s.id, source: s.source, name: s.name,
            columns_count: s.columns_count, wafer_count: s.wafer_count,
            updated_at: s.updated_at, owner: s.owner,
            columns: s.columns || [], rows: s.rows || [],
          }))
        : []
    );
    const customColsForEmbed = () => {
      const attached = attachedSetsForSubmit();
      if (wizardAttachMode === "knob") {
        return uniqueClean(Array.isArray(embedCustomCols) ? embedCustomCols : [])
          .filter(c => c && c !== "parameter" && !String(c).startsWith("#") && !isIdentityCol(c));
      }
      if (wizardAttachMode !== "sets") return [];
      return uniqueClean([
        ...attached.flatMap(s => s.columns || []),
      ]).filter(c => c && c !== "parameter" && !String(c).startsWith("#") && !isIdentityCol(c));
    };
    const shouldAttachKnobSnapshot = wizardAttachMode === "knob" && embedCustomCols.length > 0;
    const shouldAttachSetSnapshot = wizardAttachMode === "sets" && form.attach_embed && attachedSetsForSubmit().length > 0;
    const buildEmbedForLot = async (targetLot) => {
      if (!shouldAttachKnobSnapshot && !shouldAttachSetSnapshot) return null;
      const attached = attachedSetsForSubmit();
      const customCols = customColsForEmbed();
      if (!customCols.length) {
        return wizardAttachMode === "sets"
          ? {
              source: `SplitTable selected sets/${stripMlPrefix(form.product || "")}`,
              columns: [],
              rows: [],
              note: `${attached.length} selected set(s) attached`,
              attached_sets: attached,
            }
          : null;
      }
      // 미리보기에서 이미 만든(그리고 사용자가 적용공정까지 손본) 스냅샷은 다시 만들지 않는다.
      // 재생성하면 편집이 날아갈 뿐 아니라 LOT 마다 무거운 조회가 한 번씩 더 돈다.
      if (canReusePreviewSnapshot
          && String(targetLot || "").trim() === String(form.lot_id || "").trim()
          && form.attach_embed && hasLotSnapshotData(form.embed)) {
        return { ...form.embed, attached_sets: attached, note: form.embed.note || "" };
      }
      const mlProd = form.product.trim().startsWith("ML_TABLE_") ? form.product.trim() : `ML_TABLE_${form.product.trim()}`;
      let d;
      try {
        d = await postJson("/api/informs/splittable-snapshot", {
          product: mlProd,
          lot_id: targetLot,
          custom_cols: customCols,
          is_fab_lot: (form.fab_lot_ids || []).includes(targetLot) || isFabLotInput(targetLot, lotOptions),
          display_mode: form.snapshot_mode || "matrix",
          show_step_ids: !!form.show_step_ids,
        });
      } catch (e) {
        throw new Error("SplitTable 스냅샷 생성 실패: " + (e.message || e));
      }
      const embed = d?.embed || emptyEmbedTable();
      return {
        ...embed,
        attached_sets: attached,
        note: embed.note || form.embed?.note || "",
      };
    };
    const buildBody = async (targetLot) => {
      const lot = String(targetLot || "").trim();
      const body = {
        wafer_id: "", lot_id: lot, product: form.product.trim(),
        module: form.module, reason: "PEMS", text: isReInform ? withRePrefix(form.text) : form.text,
        text_format: "html",
        parent_id: isReInform ? reInformParent.id : null,
        images: isReInform ? [] : createImages,
      };
      if (form.attach_split && (form.split.column || form.split.new_value)) {
        body.splittable_change = { ...form.split, applied: false };
      }
      if (shouldAttachSetSnapshot || shouldAttachKnobSnapshot) {
        const attached = attachedSetsForSubmit();
        const customCols = customColsForEmbed();
        const isPreviewLot = String(targetLot || "").trim() === String(form.lot_id || "").trim();
        if (canReusePreviewSnapshot && isPreviewLot && form.attach_embed && hasLotSnapshotData(form.embed)) {
          body.embed_table = { ...form.embed, attached_sets: attached, note: form.embed.note || "" };
        } else if (customCols.length) {
          // 다중 LOT은 첫 LOT도 root 전체 preview를 재사용하지 않는다. 서버가
          // 각 targetLot을 독립 조회해 LOT별 스냅샷을 붙인다.
          body.snapshot_request = {
            custom_cols: customCols,
            is_fab_lot: (form.fab_lot_ids || []).includes(targetLot) || isFabLotInput(targetLot, lotOptions),
            display_mode: form.snapshot_mode || "matrix",
            show_step_ids: !!form.show_step_ids,
          };
        } else if (wizardAttachMode === "sets") {
          body.embed_table = {
            source: `SplitTable selected sets/${stripMlPrefix(form.product || "")}`,
            columns: [], rows: [],
            note: `${attached.length} selected set(s) attached`,
            attached_sets: attached,
          };
        }
        body.attached_sets = attached;
      }
      if (lot) {
        body.fab_lot_id_at_save = targetLot;
      }
      if (!isReInform && (
        (mailMetaForSubmit.to || []).length ||
        (mailMetaForSubmit.to_users || []).length ||
        (mailMetaForSubmit.groups || []).length ||
        (mailMetaForSubmit.extra_emails || []).length ||
        mailMetaForSubmit.subject ||
        mailMetaForSubmit.body
      )) {
        body.mail_draft = mailMetaForSubmit;
      }
      return body;
    };
    try {
      // LOT 마다 순차로 스냅샷을 만들면 등록이 LOT 수만큼 느려진다 — 병렬로 모은다.
      if (submitTargets.length > 1) setMsg(`스냅샷 준비 중... (${submitTargets.length} LOT)`);
      const payloads = await Promise.all(submitTargets.map(targetLot => buildBody(targetLot)));
      if (isReInform) {
        const payload = payloads[0];
        const res = await postJson(API, payload);
        if (res?.inform) {
          setThread(prev => [...prev.filter(row => row.id !== res.inform.id), res.inform]);
        }
        setForm(defaultInformForm());
        setCreateImages([]);
        setWizardStep(0);
        setWizardAttachMode("sets");
        setWizardSelectedSetIds([]);
        setEmbedCustomCols([]);
        setEmbedCustomSearch("");
        setWizardMailDraft({ subject: "", body: "", generatedFor: "" });
        setWizardMailMetaSynced({ recipients: [], knobMap: {} });
        setWizardLotSearch("");
        setWizardMode("create");
        setReInformParent(null);
        setCreating(false); setMsg("");
        const rootForDetail = selectedRoot || reInformParent;
        if (rootForDetail?.id) {
          setSelectedRootId(rootForDetail.id);
          setDetailTab("body");
          setSelectedLot(detailLotId(rootForDetail) || lot);
          window.setTimeout(() => loadDetailForRoot(rootForDetail), 800);
        } else if (res?.inform?.id) {
          setSelectedRootId(res.inform.id);
          setThread([res.inform]);
        }
        setTimeout(refreshAll, 800);
        return res;
      }
      const bulk = await postJson(API + "/bulk-create", { informs: payloads });
      const results = (bulk.informs || []).map(inform => ({ inform }));
      const createdRoots = (bulk.informs || []).filter(inform => !inform?.parent_id);
      if (createdRoots.length) {
        setListRoots(prev => [
          ...createdRoots,
          ...prev.filter(row => !createdRoots.some(created => created.id === row.id)),
        ]);
      }
      setForm(defaultInformForm());
      setCreateImages([]);
      setWizardStep(0);
      setWizardAttachMode("sets");
      setWizardSelectedSetIds([]);
      setEmbedCustomCols([]);
      setEmbedCustomSearch("");
      setWizardMailDraft({ subject: "", body: "", generatedFor: "" });
      setWizardMailMetaSynced({ recipients: [], knobMap: {} });
      setWizardLotSearch("");
      setWizardMode("create");
      setReInformParent(null);
      setCreating(false); setMsg("");
      try { localStorage.removeItem(WIZARD_DRAFT_KEY); } catch (_) {}
      const created = results.find(r => r?.inform)?.inform;
      if (created?.id) {
        const resolvedMailMeta = mailMetaForSubmit;
        const selectedTo = uniqueClean(resolvedMailMeta?.to || []);
        const selectedUsers = uniqueClean(resolvedMailMeta?.to_users || []);
        const selectedGroups = uniqueClean(resolvedMailMeta?.groups || []);
        const selectedExtras = uniqueClean(resolvedMailMeta?.extra_emails || []);
        const hasMailPrefill = selectedTo.length > 0 || selectedUsers.length > 0 || selectedGroups.length > 0 || selectedExtras.length > 0;
        const mailPrefill = hasMailPrefill ? {
          to: selectedTo,
          to_users: selectedUsers,
          groups: selectedGroups,
          extra_emails: selectedExtras,
          subject: resolvedMailMeta?.subject || wizardMailDraft.subject || "",
          body: resolvedMailMeta?.body || wizardMailDraft.body || form.text || "",
        } : null;
        setSelectedRootId(created.id);
        setDetailTab(hasMailPrefill ? "mail" : "body");
        setSelectedLot(detailLotId(created) || submitTargets[0]);
        setThread([created]);
        if (mailPrefill) setMailDialogRoot({ ...created, __mail_prefill: mailPrefill });
      }
      setTimeout(refreshAll, 800);
      return bulk;
    } catch (e) {
      setMsg(e.message || "등록 저장 실패");
      throw e;
    } finally {
      informSubmittingRef.current = false;
      setInformSubmitting(false);
    }
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
      } catch (err) { toast.error("붙여넣기 업로드 실패: " + err.message); }
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
      } catch (e) { toast.error("업로드 실패: " + e.message); }
    }
    if (out.length) {
      setCreateImages((prev) => [...prev, ...out]);
      const markdown = out.map(img => `![${img.filename || "image"}](${img.url})`).join("\n");
      setForm(f => ({ ...f, text: `${f.text || ""}\n${markdown}`.trim() }));
    }
    setUploadingMain(false);
  };

  const removeCreateImage = (image) => {
    const url = String(image?.url || "");
    setCreateImages(prev => prev.filter(img => img?.url !== url));
    if (!url) return;
    setForm(f => ({
      ...f,
      text: String(f.text || "")
        .split("\n")
        .filter(line => !line.includes(`](${url})`))
        .join("\n")
        .trim(),
    }));
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
  const [wizardLotSearch, setWizardLotSearch] = useState("");

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
    if (wizardAttachMode !== "knob") {
      setEmbedFetching(false);
      return;
    }
    if (snapshotTick <= 0) { setEmbedFetching(false); return; }
    if (!embedCustomCols.length) {
      setEmbedFetching(false);
      setForm(f => f.attach_embed ? { ...f, attach_embed: false, embed: emptyEmbedTable() } : f);
      return;
    }
    if (!prod || !lot) {
      setEmbedFetching(false);
      setForm(f => (f.attach_embed && f.embed?.source?.startsWith?.("SplitTable/"))
        ? { ...f, attach_embed: false, embed: emptyEmbedTable() }
        : f);
      return;
    }
    const mlProd = prod.startsWith("ML_TABLE_") ? prod : `ML_TABLE_${prod}`;
    const customCols = (wizardAttachMode === "knob" ? (Array.isArray(embedCustomCols) ? embedCustomCols : []) : [])
      .map(c => String(c || "").trim())
      .filter(Boolean);
    const isFabLot = (form.fab_lot_ids || []).includes(lot) || isFabLotInput(lot, lotOptions);
    const handle = setTimeout(() => {
      setEmbedFetching(true);
      postJson("/api/informs/splittable-snapshot", {
        product: mlProd,
        lot_id: lot,
        custom_cols: customCols,
        is_fab_lot: isFabLot,
        display_mode: form.snapshot_mode || "matrix",
        show_step_ids: !!form.show_step_ids,
      })
        .then(d => {
          const embed = d?.embed || emptyEmbedTable();
          setForm(f => ({
            ...f, attach_embed: true,
            embed,
          }));
          setMsg(hasLotSnapshotData(embed) ? "" : "SplitTable 스냅샷 데이터 없음: 선택 LOT/컬럼에 표시할 값이 없습니다.");
          setEmbedFetching(false);
        })
        .catch(e => {
          setForm(f => f.attach_embed ? { ...f, attach_embed: false, embed: emptyEmbedTable() } : f);
          setMsg("SplitTable 스냅샷 생성 실패: " + (e.message || e));
          setEmbedFetching(false);
        });
    }, 400);
    return () => { clearTimeout(handle); setEmbedFetching(false); };
    // v8.8.16: snapshotTick 변경 시에도 재fetch — 사용자가 Search 버튼으로 명시적 갱신.
  }, [form.product, form.lot_id, form.snapshot_mode, form.show_step_ids, creating, embedCustomCols, snapshotTick, lotOptions, wizardAttachMode]);

  // SplitTable 검색과 같은 fab_lot_id 후보 풀을 신규 인폼의 LOT 선택 소스로 쓴다.
  useEffect(() => {
    const prod = (form.product || "").trim();
    if (!prod) { setLotOptions([]); return; }
    const mlProd = prod.startsWith("ML_TABLE_") ? prod : `ML_TABLE_${prod}`;
    let alive = true;
    const base = `/api/splittable/lot-candidates?product=${encodeURIComponent(mlProd)}&limit=${LOT_CANDIDATE_LIMIT}`;
    Promise.all([
      sf(`${base}&col=fab_lot_id`).then(d => ({ type: "fab", candidates: d.candidates || [] })).catch(() => ({ type: "fab", candidates: [] })),
    ]).then(groups => {
      if (!alive) return;
      setLotOptions(mergeLotCandidateOptions(...groups));
    }).catch(() => {
      if (alive) setLotOptions([]);
    });
    return () => { alive = false; };
  }, [form.product]);

  // 입력 중인 LOT_ID prefix 를 서버에서도 보강 조회한다. 초기 후보 밖의 LOT도 검색 가능하게 한다.
  useEffect(() => {
    const prod = (form.product || "").trim();
    const rawLot = (wizardLotSearch || "").trim();
    if (!prod || rawLot.length < 2) return;
    const mlProd = prod.startsWith("ML_TABLE_") ? prod : `ML_TABLE_${prod}`;
    let alive = true;
    const url = `/api/splittable/lot-candidates?product=${encodeURIComponent(mlProd)}&col=fab_lot_id&prefix=${encodeURIComponent(rawLot)}&limit=${LOT_CANDIDATE_LIMIT}`;
    Promise.all([
      sf(url).then(d => ({ type: "fab", candidates: d.candidates || [] })).catch(() => ({ type: "fab", candidates: [] })),
    ])
      .then(groups => {
        if (!alive) return;
        const scoped = mergeLotCandidateOptions(...groups);
        setLotOptions(prev => {
          if (!scoped.length) return prev;
          const needle = rawLot.toLowerCase();
          const out = (prev || []).filter(o => !String(o.value || "").trim().toLowerCase().includes(needle));
          const idxByValue = new Map(out.map((o, i) => [String(o.value || "").trim().toLowerCase(), i]));
          for (const item of scoped) {
            const value = String(item.value || "").trim();
            if (!value) continue;
            const key = value.toLowerCase();
            const idx = idxByValue.get(key);
            if (idx === undefined) {
              idxByValue.set(key, out.length);
              out.push(item);
            }
          }
          return out;
        });
      })
      .catch(() => {});
    return () => { alive = false; };
  }, [form.product, wizardLotSearch]);

  useEffect(() => {
    if (!creating || wizardMode !== "create") return;
    try {
      localStorage.setItem(WIZARD_DRAFT_KEY, JSON.stringify({
        wizardVersion: 2,
        form,
        createImages,
        wizardStep,
        wizardAttachMode,
        wizardSelectedSetIds,
        embedCustomCols,
        wizardMailDraft,
        wizardMailMeta,
      }));
    } catch (_) {}
  }, [creating, wizardMode, form, createImages, wizardStep, wizardAttachMode, wizardSelectedSetIds, embedCustomCols, wizardMailDraft, wizardMailMeta]);

  useEffect(() => {
    if (!creating || wizardMode !== "create" || wizardStep !== 2) return;
    const mod = (form.module || "").trim();
    const selectedLots = uniqueClean(form.fab_lot_ids || []);
    const lotLabel = selectedLots[0] || form.lot_id || "";
    setWizardMailMetaSynced({ recipients: [], knobMap: {} });
    const key = [form.product, selectedLots.join(","), lotLabel, mod, form.text].join("|");
    setWizardMailDraft(d => {
      // 사용자가 메일 본문을 직접 고쳤으면 note 로 덮어쓰지 않는다.
      if (d.generatedFor === key && d.subject) {
        return d.bodyEdited ? d : { ...d, body: form.text || "", generatedFor: key };
      }
      // 제목도 직접 고쳤으면 lot/module 이 바뀌어도 유지한다.
      const subject = d.subjectEdited && d.subject
        ? d.subject
        : defaultInformMailSubject(form, lotLabel, constants.reason_templates || {});
      return { ...d, subject, body: d.bodyEdited ? d.body : (form.text || ""), generatedFor: key };
    });
  }, [creating, wizardMode, wizardStep, form.product, form.lot_id, form.fab_lot_ids, form.module, form.reason, form.text, constants.reason_templates]);

  // v8.8.0: SplitTable 에서 현재 product 의 plan 스냅샷을 본문에 임베드.
  // 빈 history 인 경우 명시적으로 알림 + paste 폴백 제안.
  const embedFromSplitTable = async () => {
    const prod = (form.product || "").trim();
    if (!prod) { toast.warn("product 를 먼저 입력하세요."); return; }
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
      toast.error("SplitTable 가져오기 실패: " + e.message);
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
    if (!txt) { toast.warn("표 데이터를 먼저 붙여넣으세요"); return; }
    const lines = txt.split(/\r?\n/).filter(l => l.length);
    if (lines.length < 2) { toast.warn("최소 2줄 (헤더 + 1행) 이 필요합니다"); return; }
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
      .catch(e => toast.error(e.message));
  };

  const toggleCheck = (node) => sf(API + "/check?id=" + encodeURIComponent(node.id), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ checked: !node.checked }),
  }).then(refreshAll).catch(e => toast.error(e.message));

  // 원문 edit endpoint 는 유지보수성 패치(예: 첨부 세트 제거)에만 사용한다.
  // 상세의 "수정" 진입은 openReInformWizard 로 새 parent_id 재인폼을 만든다.
  const editInform = (id, patch) => sf(API + "/edit?id=" + encodeURIComponent(id), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch || {}),
  }).then(refreshAll).catch(e => toast.error(e.message));

  const changeStatus = (id, status, note) => sf(API + "/status?id=" + encodeURIComponent(id), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status, note: note || "" }),
  }).then(refreshAll).catch(e => toast.error(e.message));

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
    const lotQ = (sharedFilters.lot || "").trim().toLowerCase();
    const modules = lotMatrix.module_order || [];
    const productsOut = (lotMatrix.products || [])
      .map(product => ({
        ...product,
        lots: (product.lots || []).filter(lot => {
          if (lotQ && !matrixLotSearchText(lot).toLowerCase().includes(lotQ)) return false;
          return true;
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

  const filteredListRoots = useMemo(() => {
    const lotQ = (sharedFilters.lot || "").trim().toLowerCase();
    const productSet = new Set(uniqueClean(sharedFilters.products).map(p => stripMlPrefix(p).toLowerCase()));
    const moduleSet = new Set(uniqueClean(sharedFilters.modules));
    const statusSet = new Set(uniqueClean(sharedFilters.statuses));
    return (listRoots || []).filter(r => {
      if (productSet.size && !productSet.has(stripMlPrefix(r.product || "").toLowerCase())) return false;
      if (moduleSet.size && !moduleSet.has(r.module || "기타")) return false;
      if (statusSet.size) {
        const st = normalizeFlowStatus(r.flow_status, r);
        if (!statusSet.has(st)) return false;
      }
      if (lotQ && !lotSearchText(r).toLowerCase().includes(lotQ)) return false;
      return true;
    });
  }, [listRoots, sharedFilters]);

  const filterListByMatrixLot = (product, lot) => {
    const rootLot = matrixLotId(lot);
    const prod = stripMlPrefix(String(product || "").trim());
    setSharedFilters(f => ({
      ...f,
      products: prod && prod !== "미지정" ? [prod] : [],
      lot: rootLot,
    }));
    setActiveTab(defaultInformTab());
    setInformView("list");
    setSelectedRootId("");
  };

  const openLotMatrixCell = (product, lot, module, cell) => {
    const rootLot = matrixLotId(lot);
    if (!rootLot) return;
    const informId = String(cell?.inform_id || cell?.recent?.[0]?.inform_id || "").trim();
    if (!informId) {
      setSharedFilters(f => ({
        ...f,
        products: product && product !== "미지정" ? [stripMlPrefix(product)] : [],
        modules: module ? [module] : f.modules,
        lot: rootLot,
      }));
      setActiveTab(defaultInformTab());
      setInformView("list");
      setSelectedRootId("");
      return;
    }
    const recent = (cell?.recent || []).find(r => String(r?.inform_id || "") === informId) || (cell?.recent || [])[0] || {};
    setActiveTab(defaultInformTab());
    setInformView("detail");
    setSelectedRootId(informId);
    setDetailTab("body");
    setSelectedLot(rootLot);
    loadDetailForRoot({
      id: informId,
      fab_lot_id: rootLot,
      matrix_lot_id: rootLot,
      root_lot_id: lot?.source_root_lot_id || rootLot,
      lot_id: rootLot,
      product,
      module,
      reason: recent.reason || cell?.reason || "",
      text: recent.body_preview || "",
      author: recent.author || cell?.author || "",
      created_at: recent.updated_at || cell?.updated_at || cell?.created_at || "",
      flow_status: normalizeFlowStatus(cell?.state || "registered"),
    });
  };

  const openLotForStrip = (product, lot) => {
    filterListByMatrixLot(product, lot);
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

  const closeInformDetail = () => {
    setInformView("list");
    setSelectedRootId("");
    setDetailTab("body");
  };

  const matrixLotCount = filteredLotMatrix.products.reduce((sum, product) => sum + (product.lots || []).length, 0);
  const matrixInformCount = filteredLotMatrix.products.reduce((sum, product) => (
    sum + (filteredLotMatrix.module_order || []).reduce((inner, module) => (
      inner + (product.lots || []).reduce((lotSum, lot) => lotSum + matrixCellCount((lot.modules || {})[module]), 0)
    ), 0)
  ), 0);

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
    if (!canManageInform) return;
    if (!Array.isArray(modDraft)) return;
    // v8.8.27: 저장 직전에도 입력칸 값을 흡수 → "새 모듈 저장 안됨" 버그 방지.
    const finalList = commitPendingMod(modDraft, modNewName);
    postJson("/api/informs/config", { modules: finalList })
      .then(d => {
        setConstants(c => ({ ...c, modules: d.config?.modules || finalList }));
        loadLotMatrix();
        setModDraft(null); setModNewName("");
      })
      .catch(e => toast.error("모듈 순서 저장 실패: " + (e.message || e)));
  };
  const saveReasonTemplates = (draft) => {
    if (!canManageInform) return;
    postJson("/api/informs/config", { reason_templates: draft || {} })
      .then(d => {
        setConstants(c => ({ ...c, reason_templates: d.config?.reason_templates || {} }));
        toast.ok("메일 제목 템플릿 저장됨");
      })
      .catch(e => toast.error("메일 템플릿 저장 실패: " + (e.message || e)));
  };
  const saveMailSettings = ({ links, maxMb }) => {
    if (!canManageInform) return;
    postJson("/api/informs/config", { mail_links: links || [], mail_max_mb: maxMb })
      .then(d => {
        setConstants(c => ({
          ...c,
          mail_links: d.config?.mail_links || [],
          mail_max_mb: d.config?.mail_max_mb ?? c.mail_max_mb,
        }));
        toast.ok("메일 링크/용량 설정 저장됨");
      })
      .catch(e => toast.error("메일 설정 저장 실패: " + (e.message || e)));
  };
  const saveReasonOptions = (reasons) => {
    if (!canManageInform) return;
    const finalList = uniqueClean(reasons);
    postJson("/api/informs/config", { reasons: finalList })
      .then(d => {
        setConstants(c => ({ ...c, reasons: d.config?.reasons || finalList }));
        toast.ok("사유 옵션 저장됨");
      })
      .catch(e => toast.error("사유 옵션 저장 실패: " + (e.message || e)));
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
      <PageGear title="인폼 설정" canEdit={canManageInform} position="bottom-left">
        <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 8 }}>
          제품 후보는 LOT progress cache 기준으로 자동 표시합니다. 여기서는 모듈 순서, 사유 옵션, 메일 템플릿만 관리합니다.
        </div>
        <div style={{ marginTop: 8 }}>
          <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 8 }}>
            모듈 표시 순서를 관리합니다.
          </div>
          {!modDraft && (
            <Btn variant="outline" onClick={() => setModDraft([...(constants.modules || [])])} disabled={!canManageInform}>
              모듈 순서 편집 ({(constants.modules || []).length})
            </Btn>
          )}
          {modDraft && (
            <div>
              <div style={{ maxHeight: 260, overflowY: "auto", border: "1px solid var(--border)", borderRadius: 8 }}>
                {modDraft.map((m, i) => (
                  <div key={m + i} style={{ display: "flex", alignItems: "center", gap: 6, padding: "5px 8px", borderBottom: "1px solid var(--border)", fontSize: 14, fontFamily: "monospace" }}>
                    <span style={{ width: 20, color: "var(--text-secondary)" }}>{i + 1}</span>
                    <span style={{ flex: 1 }}>{m}</span>
                    <Btn size="sm" onClick={() => moveMod(i, -1)}>↑</Btn>
                    <Btn size="sm" onClick={() => moveMod(i, 1)}>↓</Btn>
                    <Btn size="sm" variant="danger" onClick={() => setModDraft(modDraft.filter((_, j) => j !== i))}>×</Btn>
                  </div>
                ))}
              </div>
              <div style={{ display: "flex", gap: 6, marginTop: 8, flexWrap: "wrap" }}>
                <Input value={modNewName} onChange={e => setModNewName(e.target.value)}
                  placeholder="새 모듈 이름"
                  style={{ flex: 1, minWidth: 120 }}
                  onKeyDown={e => { if (e.key === "Enter") { if(e.nativeEvent?.isComposing||e.keyCode===229)return; e.preventDefault(); addPendingMod(); } }} />
                <Btn variant="outline" onClick={addPendingMod} title="모듈 추가">+</Btn>
                <Btn variant="primary" onClick={saveModuleOrder}>저장</Btn>
                <Btn variant="ghost" onClick={() => { setModDraft(null); setModNewName(""); }}>취소</Btn>
              </div>
            </div>
          )}
        </div>
        <ReasonOptionsPanel
          reasons={constants.reasons || []}
          canEdit={canManageInform}
          onSave={saveReasonOptions}
        />
        {canManageInform && (
          <ReasonTemplatesPanel
            reasons={constants.reasons || []}
            templates={constants.reason_templates || {}}
            onSave={saveReasonTemplates}
          />
        )}
        <MailLinkPanel
          links={constants.mail_links || []}
          maxMb={constants.mail_max_mb ?? 2}
          canEdit={canManageInform}
          onSave={saveMailSettings}
        />
      </PageGear>

      <header className="flow-surface-header" style={{ flex: "0 0 auto", display: "flex", alignItems: "center", gap: 12, padding: "12px 16px 8px 18px", borderBottom: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
        <div style={{ display: "grid", gap: 1, minWidth: 118 }}>
          <span style={{ fontSize: 14, fontWeight: 900, color: "var(--text-primary)" }}>인폼 로그</span>
          <span style={{ fontSize: 12, color: "var(--text-secondary)", fontFamily: "monospace" }}>{activeTab}</span>
        </div>
        <div style={{ display: "inline-flex", alignItems: "center", gap: 4, padding: 3, borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg-primary)" }}>
          {informTabs().map(([key, label]) => (
            // 작성 중이어도 탭은 항상 이동된다. 위저드 입력은 draft 로 남아 있어
            // "+ 신규 인폼 등록" 으로 다시 들어오면 그대로 이어서 쓴다.
            <button key={key} type="button" onClick={() => { setCreating(false); setMsg(""); setActiveTab(key); }}
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
      </header>

      <CommonInformFilters
        tab={activeTab}
        filters={sharedFilters}
        setFilters={setSharedFilters}
        products={commonProductOptions}
        modules={constants.modules || []}
      />

      <main style={{ flex: 1, minHeight: 0, overflow: "hidden", display: "flex", flexDirection: "column", background: "var(--bg-primary)" }}>
        {creating && (
          <InformWizard
            mode={wizardMode}
            parentInform={reInformParent}
            form={form}
            setForm={setForm}
            constants={constants}
            products={commonProductOptions}
            productContacts={productContacts}
            lotOptions={lotOptions}
            fabSearch={wizardLotSearch}
            setFabSearch={setWizardLotSearch}
            step={wizardStep}
            setStep={setWizardStep}
            attachMode={wizardAttachMode}
            setAttachMode={setWizardAttachMode}
            selectedSetIds={wizardSelectedSetIds}
            setSelectedSetIds={setWizardSelectedSetIds}
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
            setMailMeta={setWizardMailMetaSynced}
            createImages={createImages}
            uploadingMain={uploadingMain}
            onNotePaste={handleBodyPaste}
            onNoteImages={uploadMain}
            onRemoveNoteImage={removeCreateImage}
            reasonTemplates={constants.reason_templates || {}}
            user={user}
            msg={msg}
            setMsg={setMsg}
            submitting={informSubmitting}
            onSubmit={() => create().catch(() => {})}
            onClose={() => {
              if (informSubmittingRef.current) return;
              setCreating(false);
              setWizardMode("create");
              setReInformParent(null);
              setWizardLotSearch("");
              setWizardSelectedSetIds([]);
              setEmbedCustomCols([]);
              setEmbedCustomSearch("");
              setMsg("");
              setWizardMailMetaSynced({ recipients: [], knobMap: {} });
            }}
          />
        )}
        {!creating && activeTab === "inform" && (
          <section style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
            {informView === "detail" ? (
              <InformFullDetail root={selectedRoot} onBack={closeInformDetail}>
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
                  onReInform={openReInformWizard}
                  onChangeStatus={changeStatus}
                  onOpenMail={(root) => setMailDialogRoot(root)}
                />
              </InformFullDetail>
            ) : (
              <>
                <div style={{ flex: "0 0 auto", padding: "9px 16px", display: "flex", alignItems: "center", gap: 8, borderBottom: "1px solid var(--border)", color: "var(--text-secondary)", background: "var(--bg-secondary)", transition: "color 120ms, background 120ms" }}>
                  <span><b style={{ color: "var(--accent)" }}>{filteredListRoots.length}</b>건 표시 / 전체 {listRoots.length}건</span>
                  <span style={{ marginLeft: "auto" }}><NewInformButton onClick={openCreateWizard} /></span>
                </div>
                <InformVirtualList roots={filteredListRoots} childrenByParent={listChildrenByParent} selectedId={selectedRootId} onOpen={openRootForDetail} />
              </>
            )}
          </section>
        )}
        {!creating && activeTab === "audit" && (
          <section style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
            <div style={{ flex: "0 0 auto", padding: "9px 16px", display: "flex", alignItems: "center", gap: 8, borderBottom: "1px solid var(--border)", color: "var(--text-secondary)", background: "var(--bg-secondary)" }}>
              <span><b style={{ color: "var(--accent)" }}>{auditRows.length}</b>건 로그</span>
              <span style={{ marginLeft: "auto" }}><NewInformButton onClick={openCreateWizard} /></span>
            </div>
            <AuditLogList rows={auditRows} loading={auditLoading} onOpen={openAuditRow} />
          </section>
        )}
        {!creating && activeTab === "matrix" && (
          <section style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
            <div style={{ flex: "0 0 auto", padding: "9px 16px", display: "flex", alignItems: "center", gap: 8, borderBottom: "1px solid var(--border)", color: "var(--text-secondary)", background: "var(--bg-secondary)" }}>
              <span><b style={{ color: "var(--accent)" }}>{matrixLotCount}</b> lot / 인폼 {matrixInformCount}건</span>
              <span style={{ marginLeft: "auto" }}><NewInformButton onClick={openCreateWizard} /></span>
            </div>
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
          </section>
        )}
      </main>

      {mailDialogRoot && <MailDialog root={mailDialogRoot} user={user} reasonTemplates={constants.reason_templates || {}} onClose={() => setMailDialogRoot(null)} />}
    </div>
  );

}

/* v8.7.9 — 시간순 이력 타임라인 (간트바 대신).
   각 줄: [시각] 모듈 LOT · 이벤트타입 · 요약 · 작성자.
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
        lot: informLotDisplay(x),
        lotSearch: lotSearchText(x),
        product: stripMlPrefix(x.product || ""),
        summary: (x.text || "").slice(0, 80),
        node: x,
      });
      // 2) 상태 이력 — registered/received(최초) 는 등록 이벤트가 이미 담당하므로 skip.
      let seenFirstReceived = false;
      let prevStat = "";
      for (const h of (x.status_history || [])) {
        if (!h || !h.at) continue;
        const hPrev = h.prev ?? prevStat;  // v8.8.2: prev 누락 시 walking 으로 복원
        const noteStr = h.note || "";
        const hStatus = normalizeFlowStatus(h.status);
        const hPrevStatus = normalizeFlowStatus(hPrev);
        const isInitial = hStatus === "registered" && !seenFirstReceived
          && (noteStr === "created" || noteStr === "auto from SplitTable" || (!noteStr && hPrev === ""));
        if (isInitial) { seenFirstReceived = true; prevStat = h.status || prevStat; continue; }
        const isUnconfirm = hStatus === "registered" && (
          hPrevStatus === "apply_confirmed"
          || noteStr.includes("확인 취소")
          || noteStr.includes("완료 해제")
          || noteStr.includes("취소")
          || noteStr.includes("해제")
        );
        evs.push({
          at: h.at,
          actor: h.actor || "",
          kind: hStatus === "apply_confirmed"
            ? "담당자확인"
            : (hStatus === "mail_completed" ? "메일완료" : (isUnconfirm ? "확인취소" : `상태:${STATUS_META[hStatus]?.label || h.status || "-"}`)),
          module: x.module || "",
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
    <Card style={{ fontFamily: "monospace" }} padding={10}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6, flexWrap: "wrap" }}>
        <span style={{ fontSize: 14, fontWeight: 700, color: "var(--accent)" }}>📜 이력 타임라인 ({filtered.length}{lotQ ? ` / ${events.length}` : ""}건)</span>
        <input value={lotQ} onChange={e => setLotQ(e.target.value)}
          placeholder="🔎 Lot 검색 (root_lot_id 또는 fab_lot_id 부분일치)"
          style={{ flex: 1, minWidth: 220, padding: "5px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14, fontFamily: "monospace" }} />
        {lotQ && <span onClick={() => setLotQ("")} style={{ cursor: "pointer", color: "var(--danger)", fontSize: 14 }}>✕ 초기화</span>}
      </div>
      <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 8 }}>작성 / 수정 / 이행(확인·완료) — 누가 언제 무엇을 했는지 시간순. Lot 입력 시 해당 Lot 만 필터링.</div>
      {filtered.length === 0 && <div style={{ padding: 40, textAlign: "center", color: "var(--text-secondary)" }}>{lotQ ? `'${lotQ}' 매칭 이력 없음.` : "이력 없음."}</div>}
      {filtered.map((e, i) => {
        const mc = moduleColor(e.module);
        const kindTone = e.kind === "인폼" ? "info"
          : e.kind === "담당자확인" ? "ok"
          : e.kind === "확인취소" ? "danger"
          : e.kind === "메일" ? "warn"
          : e.kind === "댓글" ? "violet"
          : e.kind === "체크" ? "brand"
          : "neutral";
        const lotLabel = e.product && e.lot ? `[${e.product}] ${e.lot}` : (e.lot || e.product || "-");
        return (
          <div key={i} onClick={() => onOpen && onOpen(e.node)} style={{
            display: "flex", gap: 10, alignItems: "center", padding: "4px 6px",
            borderRadius: 4, cursor: "pointer", fontSize: 14, lineHeight: 1.55,
            borderLeft: `3px solid ${mc}`, marginBottom: 2, background: i % 2 ? "var(--bg-primary)" : "transparent",
          }}>
            <span style={{ color: "var(--text-secondary)", minWidth: 115 }}>{(e.at || "").replace("T", " ").slice(0, 16)}</span>
            <span style={{ minWidth: 56, color: mc, fontWeight: 700 }}>{e.module || "-"}</span>
            <span title={lotLabel} style={{ minWidth: 220, maxWidth: 360, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{lotLabel}</span>
            <Pill tone={kindTone}>{e.kind}</Pill>
            <span style={{ color: "var(--text-primary)", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{e.summary}</span>
            <span style={{ color: "var(--text-secondary)", fontSize: 14 }}>· {e.actor || "-"}</span>
          </div>
        );
      })}
    </Card>
  );
}

/* PageGear 설정 패널 — inform page manager 이상이 모듈/사유/템플릿을 관리한다. */
function ReasonOptionsPanel({ reasons, canEdit, onSave }) {
  const [draft, setDraft] = React.useState(() => uniqueClean(reasons));
  const [newReason, setNewReason] = React.useState("");
  React.useEffect(() => { setDraft(uniqueClean(reasons)); }, [reasons]);
  const addReason = () => {
    const v = newReason.trim();
    if (!v) return;
    setDraft(d => uniqueClean([...(d || []), v]));
    setNewReason("");
  };
  const removeReason = (reason) => {
    setDraft(d => (d || []).filter(v => v !== reason));
  };
  const saveReasons = () => {
    const finalList = uniqueClean([...(draft || []), newReason]);
    setDraft(finalList);
    setNewReason("");
    onSave(finalList);
  };
  return (
    <div style={{ marginTop: 14, paddingTop: 10, borderTop: "1px dashed var(--border)" }}>
      <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 8 }}>
        사유 옵션을 관리합니다.
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 8 }}>
        {(draft || []).map(reason => (
          <span key={reason} style={{ padding: "4px 10px", borderRadius: 999, background: "var(--accent-glow)", color: "var(--accent)", fontSize: 14, fontWeight: 700, display: "inline-flex", alignItems: "center", gap: 6 }}>
            {reason}
            <button type="button" disabled={!canEdit} onClick={() => removeReason(reason)}
              style={{ border: "none", background: "transparent", color: BAD.fg, cursor: canEdit ? "pointer" : "not-allowed", fontSize: 14, padding: 0 }}>×</button>
          </span>
        ))}
        {(draft || []).length === 0 && <span style={{ fontSize: 14, color: "var(--text-secondary)" }}>없음</span>}
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        <Input value={newReason} onChange={e => setNewReason(e.target.value)}
          disabled={!canEdit}
          placeholder="새 사유"
          style={{ flex: 1, minWidth: 120 }}
          onKeyDown={e => { if (e.key === "Enter") { if(e.nativeEvent?.isComposing||e.keyCode===229)return; e.preventDefault(); addReason(); } }} />
        <Btn variant="outline" type="button" disabled={!canEdit} onClick={addReason}>+</Btn>
        <Btn variant="primary" type="button" disabled={!canEdit} onClick={saveReasons}>저장</Btn>
      </div>
    </div>
  );
}

/* v8.8.17: ReasonTemplatesPanel — inform page manager 가 사유별로 메일 제목 + 본문 템플릿을 편집.
   PageGear 안에서 사유 목록을 순회하며 제목/본문 2필드를 보여준다.
   저장은 단건이 아니라 템플릿 맵 전체를 일괄 PATCH 로 POST /api/informs/config. */
/* 메일 하단 링크 + 메일 용량 한도 — 인폼 톱니바퀴에서 관리자가 관리한다. */
function MailLinkPanel({ links, maxMb, canEdit, onSave }) {
  const [draft, setDraft] = React.useState(() => (links || []).map(x => ({ ...x })));
  const [limit, setLimit] = React.useState(() => String(maxMb ?? 2));
  React.useEffect(() => { setDraft((links || []).map(x => ({ ...x }))); }, [links]);
  React.useEffect(() => { setLimit(String(maxMb ?? 2)); }, [maxMb]);
  const setField = (i, key, v) => setDraft(d => d.map((x, j) => (j === i ? { ...x, [key]: v } : x)));
  return (
    <div style={{ marginTop: 14, paddingTop: 10, borderTop: "1px dashed var(--border)" }}>
      <div style={{ fontSize: 14, fontWeight: 900, marginBottom: 6 }}>메일 하단 링크 · 용량 한도</div>
      <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 8 }}>
        여기에 등록한 링크가 인폼 메일 본문 맨 아래에 붙습니다. 용량 한도를 넘는 메일은 등록·발송되지 않습니다.
      </div>
      <div style={{ display: "grid", gap: 6 }}>
        {draft.map((row, i) => (
          <div key={i} style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <Input value={row.label || ""} disabled={!canEdit} placeholder="표시 이름"
              onChange={e => setField(i, "label", e.target.value)} style={{ width: 180 }} />
            <Input value={row.url || ""} disabled={!canEdit} placeholder="http://go/..."
              onChange={e => setField(i, "url", e.target.value)} style={{ flex: 1, fontFamily: "monospace" }} />
            <Btn size="sm" variant="danger" disabled={!canEdit}
              onClick={() => setDraft(d => d.filter((_, j) => j !== i))}>×</Btn>
          </div>
        ))}
        {draft.length === 0 && <div style={{ fontSize: 14, color: "var(--text-secondary)" }}>등록된 링크 없음</div>}
      </div>
      <div style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 8, flexWrap: "wrap" }}>
        <Btn size="sm" variant="outline" disabled={!canEdit}
          onClick={() => setDraft(d => [...d, { label: "", url: "" }])}>+ 링크 추가</Btn>
        <span style={{ marginLeft: 8, fontSize: 14, fontWeight: 800 }}>메일 최대 용량(MB)</span>
        <Input value={limit} disabled={!canEdit} onChange={e => setLimit(e.target.value)}
          style={{ width: 90, fontFamily: "monospace" }} />
        <Btn size="sm" variant="primary" disabled={!canEdit}
          onClick={() => onSave({
            links: draft.filter(x => String(x.url || "").trim()),
            maxMb: Number(limit) > 0 ? Number(limit) : 2,
          })}>
          저장
        </Btn>
      </div>
    </div>
  );
}

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


function ModulePill({ module, solid = false }) {
  const mc = moduleColor(module || "기타");
  return (
    <span title={module || "기타"} style={{
      display: "inline-flex", alignItems: "center", maxWidth: 150,
      padding: "2px 9px", borderRadius: 999,
      background: solid ? mc : colorWithAlpha(mc, 0.12),
      color: solid ? "#fff" : mc,
      border: "1px solid " + (solid ? mc : colorWithAlpha(mc, 0.36)),
      fontSize: 14, fontWeight: 800, fontFamily: "monospace",
      overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
    }}>{module || "기타"}</span>
  );
}

function LotPill({ root }) {
  const label = informLotDisplay(root, { maxFabLots: 2 }) || "-";
  // pill 로 감싸지 않고 평범한 글로 — 제품/LOT 은 읽는 값이지 상태 배지가 아니다.
  return (
    <span title={label} style={{
      display: "inline-flex", alignItems: "center", minWidth: 0, maxWidth: 260,
      color: "var(--text-primary)", fontSize: 14, fontFamily: "monospace", fontWeight: 700,
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
  // 단일 선택 필터 — 저장 형태는 기존 배열 그대로 두고(소비하는 쪽이 여럿) 길이만 0/1 로 쓴다.
  const pickOne = (key) => String((filters[key] || [])[0] || "");
  const setOne = (key, value) => {
    const v = String(value || "").trim();
    setFilters(f => ({ ...f, [key]: v ? [v] : [] }));
  };
  if (tab === "matrix") {
    return (
      <div style={{ flex: "0 0 auto", position: "sticky", top: 0, zIndex: 20, padding: "10px 16px", borderBottom: "1px solid var(--border)", background: "var(--bg-secondary)", display: "flex", gap: 8, alignItems: "center", boxShadow: "0 1px 0 rgba(15,23,42,0.04)" }}>
        <Input value={lotDraft} onChange={e => setLotDraft(e.target.value)}
          placeholder="랏 검색"
          style={{ width: 240, fontSize: 13, fontFamily: "monospace" }} />
        {filters.lot && <FilterChip label={`lot ${filters.lot}`} onRemove={() => { setLotDraft(""); update({ lot: "" }); }} />}
        <Btn variant="ghost" type="button" onClick={() => { setLotDraft(""); setFilters(f => ({ ...f, lot: "", products: [], modules: [], statuses: [] })); }} title="랏 검색 초기화"
          style={{ marginLeft: "auto" }}>
          초기화
        </Btn>
      </div>
    );
  }
  const statusOptions = [
    ["registered", "등록"],
    ["mail_completed", "메일완료"],
    ["apply_confirmed", "등록적용확인"],
  ];
  const showStatus = tab === "inform" || tab === "matrix";
  const showTypes = tab === "audit";
  const pickerStyle = { width: 148, fontSize: 13 };
  return (
    <div style={{ flex: "0 0 auto", position: "sticky", top: 0, zIndex: 20, padding: "10px 16px", borderBottom: "1px solid var(--border)", background: "var(--bg-secondary)", display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", boxShadow: "0 1px 0 rgba(15,23,42,0.04)" }}>
      {/* 목록에서 고르면 그 값이 그대로 필터가 된다 — 항목당 하나만 선택되고,
          빈 값(전체)을 고르면 해제된다. 아무것도 안 골라도 lot 검색은 그대로 동작한다.
          (예전엔 고를 때마다 옆에 칩이 쌓이는 다중 선택이라 지금 뭐가 걸렸는지
           칩을 읽어야 알 수 있었다.) */}
      <Select value={pickOne("products")} onChange={e => setOne("products", e.target.value)} style={pickerStyle}>
        <option value="">제품 전체</option>
        {(products || []).map(p => <option key={p} value={p}>{p}</option>)}
      </Select>
      <Select value={pickOne("modules")} onChange={e => setOne("modules", e.target.value)} style={pickerStyle}>
        <option value="">모듈 전체</option>
        {(modules || []).map(m => <option key={m} value={m}>{m}</option>)}
      </Select>
      {showStatus && (
        <Select value={pickOne("statuses")} onChange={e => setOne("statuses", e.target.value)} style={pickerStyle}>
          <option value="">상태 전체</option>
          {statusOptions.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
        </Select>
      )}
      {showTypes && (
        <Select value={pickOne("types")} onChange={e => setOne("types", e.target.value)} style={pickerStyle}>
          <option value="">유형 전체</option>
          {AUDIT_TYPES.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
        </Select>
      )}
      <Input value={lotDraft} onChange={e => setLotDraft(e.target.value)}
        placeholder="lot 검색"
        style={{ width: 180, fontSize: 13, fontFamily: "monospace" }} />
      {filters.lot && <FilterChip label={`lot ${filters.lot}`} onRemove={() => { setLotDraft(""); update({ lot: "" }); }} />}
    </div>
  );
}

function FilterChip({ label, onRemove }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6, minHeight: 27, maxWidth: 220, padding: "3px 8px", borderRadius: 999, border: "1px solid var(--accent)", background: "var(--accent)", color: "#fff", fontSize: 13, fontWeight: 900, boxShadow: "0 1px 2px rgba(15,23,42,0.10)" }}>
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label}</span>
      <button type="button" onClick={onRemove} title="필터 해제"
        style={{ border: "none", background: "transparent", color: "#fff", cursor: "pointer", fontWeight: 900, padding: 0, lineHeight: 1, opacity: 0.9 }}>
        x
      </button>
    </span>
  );
}

function AuditLogList({ rows, loading, onOpen }) {
  const typeMeta = {
    status_change: { label: "상태변경", icon: "●", color: WARN.fg },
    mail: { label: "메일", icon: "✉", color: INFO.fg },
    comment: { label: "댓글", icon: "💬", color: chartPalette.series[6] },
    edit: { label: "수정", icon: "✎", color: chartPalette.series[11] },
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
        const module = String(row.module || "").trim() || "기타";
        return (
          <button key={row.id || i} type="button" onClick={() => onOpen(row)}
            style={{ width: "100%", minHeight: 52, padding: "7px 16px", border: "none", borderBottom: "1px solid var(--border)", background: "transparent", color: "var(--text-primary)", display: "grid", gridTemplateColumns: "148px 112px minmax(180px, 1fr) 116px 112px minmax(0, 1.4fr)", gap: 8, alignItems: "center", cursor: "pointer", textAlign: "left", fontSize: 14 }}>
            <span style={{ color: "var(--text-secondary)", fontFamily: "monospace" }}>{String(row.at || "").replace("T", " ").slice(0, 16)}</span>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: meta.color, fontWeight: 900 }}>
              <span>{meta.icon}</span>{meta.label}
            </span>
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontFamily: "monospace" }}>{lot}</span>
            <ModulePill module={module} solid />
            <span style={{ color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row.actor || "-"}</span>
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row.summary || "-"}</span>
          </button>
        );
      })}
    </div>
  );
}

const LOT_MATRIX_STATES = {
  apply_confirmed: { label: "등록적용확인", mark: "✓", bg: "#dcfce7", fg: "#15803d" },
  mail_completed: { label: "메일완료", mark: "◯", bg: "#fed7aa", fg: "#c2410c" },
  registered: { label: "등록", mark: "◎", bg: "#dbeafe", fg: "#1d4ed8" },
};
const LOT_MATRIX_COUNT_COLOR = "#111827";

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
    ].join("\n");
  };
  const progressWidth = (progress) => {
    const done = Number(progress?.done || 0);
    const total = Number(progress?.total || 0);
    return total ? Math.max(0, Math.min(100, Math.round((done / total) * 100))) : 0;
  };
  const headStyle = {
    position: "sticky", top: 0, zIndex: 5,
    height: 36, padding: "4px 5px",
    borderBottom: "1px solid var(--border)", borderRight: "1px solid var(--border)",
    background: "var(--bg-secondary)", color: "var(--text-secondary)",
    fontSize: 14, fontWeight: 900, textAlign: "center",
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
        <Select value={filters.product || ""} onChange={e => setFilter("product", e.target.value)} style={{ fontSize: 13 }}>
          <option value="">제품 전체</option>
          {(productOptions || []).map(product => <option key={product} value={product}>{product}</option>)}
        </Select>
        <Input value={filters.search || ""} onChange={e => setFilter("search", e.target.value)}
          placeholder="LOT_ID 검색"
          style={{ fontSize: 13, fontFamily: "monospace" }} />
        <div style={{ display: "flex", gap: 5, flexWrap: "wrap", alignItems: "center" }}>
          {["registered", "mail_completed", "apply_confirmed"].map(state => {
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
                <th style={leftHeadStyle}>LOT_ID</th>
                {modules.map(module => (
                  <th key={module} title={module} onClick={() => onToggleModule && onToggleModule(module)}
                    style={{ ...headStyle, width: 64, minWidth: 64, maxWidth: 64, cursor: onToggleModule ? "pointer" : "default", color: activeModuleSet.has(module) ? "var(--accent)" : "var(--text-secondary)" }}>
                    <span style={{ display: "block", maxWidth: 56, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 14, fontWeight: 900 }}>{module}</span>
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
                  {product.lots.map(lot => {
                    const lotId = matrixLotId(lot);
                    return (
                    <tr key={`${product.product}:${lotId}`} onClick={() => onOpenLot ? onOpenLot(product.product, lot) : onPickLot(product.product, lot)}
                      style={{ cursor: "pointer" }}>
                      <td title={lotId} style={leftCellStyle}>
                        <span style={{ display: "inline-flex", alignItems: "center", gap: 4, width: "100%" }}>
                          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>{lotId}</span>
                          <button type="button" title="인폼 탭에서 이 lot 필터"
                            onClick={e => { e.stopPropagation(); onPickLot(product.product, lot); }}
                            style={{ width: 16, height: 22, border: "none", background: "transparent", color: "var(--accent)", cursor: "pointer", padding: 0, fontWeight: 900 }}>
                            ↗
                          </button>
                        </span>
                      </td>
                      {modules.map(module => {
                        const cell = (lot.modules || {})[module];
                        const state = cell?.state || "";
                        const meta = LOT_MATRIX_STATES[state] || { fg: "var(--border)" };
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
                                color: count ? LOT_MATRIX_COUNT_COLOR : "var(--text-muted)",
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
                    );
                  })}
                </React.Fragment>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function InformVirtualList({ roots, childrenByParent = {}, selectedId, onOpen }) {
  const tableStyle = {
    width: "100%",
    minWidth: 1060,
    tableLayout: "fixed",
    borderCollapse: "separate",
    borderSpacing: 0,
    fontSize: 14,
  };
  const headStyle = {
    position: "sticky",
    top: 0,
    zIndex: 5,
    padding: "6px 8px",
    borderBottom: "1px solid var(--border)",
    background: "var(--bg-secondary)",
    color: "var(--text-secondary)",
    fontWeight: 900,
    textAlign: "left",
    whiteSpace: "nowrap",
  };
  const renderRows = (node, root, depth = 0) => {
    const kids = (childrenByParent[node.id] || [])
      .slice()
      .sort((a, b) => String(a.created_at || "").localeCompare(String(b.created_at || "")));
    return (
      <React.Fragment key={`${root.id}:${node.id || depth}`}>
        <InformListRow root={root} node={node} depth={depth} selected={selectedId === root.id} onOpen={() => onOpen(root)} />
        {kids.map(child => renderRows(child, root, depth + 1))}
      </React.Fragment>
    );
  };

  return (
    <TableWrap style={{ flex: 1, minHeight: 0, border: "none", borderRadius: 0, background: "var(--bg-secondary)" }}>
      {roots.length === 0 && <div style={{ padding: 24, textAlign: "center", color: "var(--text-secondary)" }}>조건에 맞는 인폼이 없어요</div>}
      {roots.length > 0 && (
        <Tbl style={tableStyle}>
          <colgroup>
            <col style={{ width: 6 }} />
            <col style={{ width: 110 }} />
            <col style={{ width: 132 }} />
            <col style={{ width: 82 }} />
            <col />
            <col style={{ width: 188 }} />
            <col style={{ width: 112 }} />
            <col style={{ width: 140 }} />
            <col style={{ width: 90 }} />
          </colgroup>
          <thead>
            <tr>
              <th style={{ ...headStyle, padding: 0 }}></th>
              <th style={headStyle}>제품</th>
              <th style={headStyle}>fab_lot_id</th>
              <th style={headStyle}>root_lot</th>
              <th style={headStyle}>제목</th>
              <th style={{ ...headStyle, padding: "6px 4px" }}>상태</th>
              <th style={headStyle}>작성자</th>
              <th style={headStyle}>시간</th>
              <th style={{ ...headStyle, right: 0, zIndex: 7, textAlign: "center" }}>모듈</th>
            </tr>
          </thead>
          <tbody>
            {roots.map(root => renderRows(root, root, 0))}
          </tbody>
        </Tbl>
      )}
    </TableWrap>
  );
}

function rowFabLotLabel(root) {
  const rootLot = String(root?.root_lot_id || root?.lot_id || root?.wafer_id || "").trim();
  const fabs = splitFabLotsFromNode(root).filter(v => v && v !== rootLot);
  return fabs[0] || rootLot.slice(0, 5) || "-";
}

function rowRootLotLabel(root) {
  const rootLot = String(root?.root_lot_id || root?.lot_id || root?.wafer_id || "").trim();
  return rootLot ? rootLot.slice(0, 5) : "-";
}

function InformListRow({ root, node, depth = 0, selected, onOpen }) {
  const [hover, setHover] = useState(false);
  const row = node || root;
  const isChild = depth > 0 || !!row.parent_id;
  const status = normalizeFlowStatus(row.flow_status, row);
  const module = row.module || root.module || "기타";
  const mc = moduleColor(module);
  const titleText = isChild
    ? (reInformTextForDisplay(row).replace(/^\[RE\]\s*/, "") || informTitle(row))
    : informTitle(row);
  const timeValue = row.created_at || "";
  const timeLabel = isChild ? "재인폼" : "등록";
  const cellStyle = {
    padding: "6px 8px",
    borderBottom: "1px solid var(--border)",
    verticalAlign: "middle",
    background: selected ? colorWithAlpha(mc, 0.13) : (hover ? "var(--bg-hover)" : "var(--bg-primary)"),
    color: "var(--text-primary)",
    fontWeight: selected ? 800 : 500,
  };
  return (
    <tr onClick={onOpen} onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      onFocus={() => setHover(true)} onBlur={() => setHover(false)}
      style={{
        height: 42,
        cursor: "pointer",
        transition: "background 120ms",
      }}>
      <td style={{ ...cellStyle, padding: 0, width: 6, background: mc }}>
        <span style={{ display: "block", width: selected ? 6 : 4, height: 42, background: mc }} />
      </td>
      <td title={stripMlPrefix(root.product || row.product || "")} style={{ ...cellStyle, fontFamily: "monospace", fontWeight: 800, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
        {stripMlPrefix(root.product || row.product || "-")}
      </td>
      <td title={rowFabLotLabel(root)} style={{ ...cellStyle, fontFamily: "monospace", fontSize: 14, fontWeight: 900, whiteSpace: "nowrap", overflow: "visible" }}>
        {rowFabLotLabel(root)}
      </td>
      <td title={root.root_lot_id || root.lot_id || ""} style={{ ...cellStyle, fontFamily: "monospace", fontSize: 13, color: "var(--text-secondary)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
        {rowRootLotLabel(root)}
      </td>
      <td title={isChild ? `↳ [RE] ${titleText}` : titleText} style={{ ...cellStyle, minWidth: 0 }}>
        <span style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0, overflow: "hidden", whiteSpace: "nowrap", fontWeight: 900, paddingLeft: isChild ? Math.min(depth, 5) * 22 : 0 }}>
          {isChild && <span style={{ flex: "0 0 auto", color: "var(--accent)", fontWeight: 900 }}>↳ [RE]</span>}
          <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>{titleText}</span>
        </span>
      </td>
      <td style={{ ...cellStyle, minWidth: 0, overflow: "hidden", padding: "6px 4px" }}>
        <StatusBadge status={isChild ? "reinform" : status} compact />
      </td>
      <td title={row.author || ""} style={{ ...cellStyle, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{row.author || "-"}</td>
      <td title={`${timeLabel} ${(timeValue || "").replace("T", " ").slice(0, 16)}`} style={{ ...cellStyle, fontFamily: "monospace", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 5, maxWidth: "100%", minWidth: 0 }}>
          <span style={{ flex: "0 0 auto", color: "var(--text-secondary)", fontFamily: "inherit", fontSize: 11, fontWeight: 900 }}>{timeLabel}</span>
          <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>{relativeTime(timeValue)}</span>
        </span>
      </td>
      <td style={{ ...cellStyle, position: "sticky", right: 0, zIndex: 2, textAlign: "center", boxShadow: "-1px 0 0 var(--border)" }}>
        <span title={module} style={{ display: "inline-flex", justifyContent: "center", alignItems: "center", width: 76, maxWidth: 76, padding: "3px 7px", borderRadius: 999, background: mc, color: "#fff", fontSize: 13, fontWeight: 900, fontFamily: "monospace", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {module}
        </span>
      </td>
    </tr>
  );
}

function InformFullDetail({ root, onBack, children }) {
  return (
    <section style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", background: "var(--bg-primary)" }}>
      <div style={{ flex: "0 0 auto", padding: "10px 16px", display: "flex", alignItems: "center", gap: 10, borderBottom: "1px solid var(--border)", background: "var(--bg-secondary)", minWidth: 0 }}>
        <button type="button" onClick={onBack}
          style={{ padding: "7px 12px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontWeight: 900, cursor: "pointer", fontSize: 14, whiteSpace: "nowrap" }}>
          ← 뒤로가기
        </button>
        <span style={{ fontWeight: 900, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {root ? informTitle(root) : "인폼 상세"}
        </span>
        {root && (
          <span style={{ marginLeft: "auto", display: "inline-flex", alignItems: "center", gap: 6, flexWrap: "wrap", justifyContent: "flex-end", minWidth: 0 }}>
            <ModulePill module={root.module || "기타"} solid />
            {root.product && <span style={{ fontWeight: 800, fontFamily: "monospace", color: "var(--text-primary)" }}>{stripMlPrefix(root.product)}</span>}
            <LotPill root={root} />
            <StatusBadge status={normalizeFlowStatus(root.flow_status, root)} />
          </span>
        )}
      </div>
      <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
        {children}
      </div>
    </section>
  );
}

function InformDetailPane({ root, thread, childrenByParent, constants, user, tab, setTab, onReply, onDelete, onToggleCheck, onEdit, onReInform, onChangeStatus, onOpenMail }) {
  if (!root) {
    return (
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-secondary)", fontSize: 14 }}>
        왼쪽에서 인폼을 선택하면 자세한 내용이 여기 표시돼요
      </div>
    );
  }
  const tabs = [
    ["body", "본문"],
    ["mail", "메일 이력"],
  ];
  const commentCount = commentTreeCount(root.id, childrenByParent);
  const lotText = informLotDisplay(root, { maxFabLots: 8 }) || "-";
  const status = normalizeFlowStatus(root.flow_status, root);
  const completed = status === "apply_confirmed";
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
              <ModulePill module={root.module || "기타"} solid />
              <StatusBadge status={status} />
              {root.product && <span style={{ fontWeight: 800, fontFamily: "monospace", color: "var(--text-primary)" }}>{stripMlPrefix(root.product)}</span>}
              <LotPill root={root} />
              <span style={{ color: "var(--text-secondary)", fontSize: 14 }}>{root.author || "-"} · {relativeTime(root.created_at)}</span>
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", justifyContent: "flex-end" }}>
            <button type="button" disabled={!canEditDelete} onClick={() => onDelete(root.id)}
              style={{ padding: "7px 12px", borderRadius: 8, border: "1px solid " + BAD.fg, background: "transparent", color: canEditDelete ? BAD.fg : "var(--text-muted)", fontWeight: 800, cursor: canEditDelete ? "pointer" : "not-allowed", fontSize: 14 }}>
              삭제
            </button>
            <button type="button" onClick={() => onChangeStatus(root.id, completed ? "registered" : "apply_confirmed", "")}
              style={{ padding: "7px 12px", borderRadius: 8, border: "1px solid " + (completed ? WARN.fg : OK.fg), background: completed ? "transparent" : OK.fg, color: completed ? WARN.fg : "#fff", fontWeight: 800, cursor: "pointer", fontSize: 14 }}>
              {completed ? "확인 해제" : "등록적용확인"}
            </button>
            <button type="button" onClick={() => onOpenMail(root)}
              style={{ padding: "7px 12px", borderRadius: 8, border: "1px solid var(--accent)", background: "transparent", color: "var(--accent)", fontWeight: 800, cursor: "pointer", fontSize: 14 }}>
              ✉ 메일
            </button>
            <button type="button" onClick={() => onReInform?.(root)}
              title="재인폼 작성"
              style={{ padding: "7px 12px", borderRadius: 8, border: "1px solid var(--border)", background: "transparent", color: "var(--text-primary)", fontWeight: 800, cursor: "pointer", fontSize: 14 }}>
              재인폼 {commentCount}
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
          <div style={informConnectedPanel}>
            <section style={informConnectedSectionFirst}>
              <div style={informConnectedSectionTitle}>내용</div>
              <div style={{ whiteSpace: "pre-wrap", lineHeight: 1.55 }}>{root.text || "(내용 없음)"}</div>
            </section>
            {root.embed_table && (
              <section style={informConnectedSection}>
                <EmbedTableView embed={root.embed_table} product={root.product} />
              </section>
            )}
          </div>
        )}
        {tab === "mail" && <InformMailHistoryPanel root={root} onOpenMail={() => onOpenMail(root)} />}
      </div>
    </div>
  );
}

function commentTreeCount(rootId, childrenByParent) {
  const walk = (id) => (childrenByParent[id] || []).reduce((sum, child) => sum + 1 + walk(child.id), 0);
  return walk(rootId);
}

function InformCommentsPanel({ root, childrenByParent, constants, user, onReply, onDelete, onToggleCheck, onEdit, onReInform }) {
  const [text, setText] = useState("");
  const comments = childrenByParent[root.id] || [];
  const submit = () => {
    const body = withRePrefix(text);
    if (!body) return;
    onReply(root.id, {
      module: root.module || "",
      reason: root.reason || "",
      text: body,
      images: [],
    }).then(() => setText(""));
  };
  return (
    <div style={{ padding: 16, borderBottom: "1px solid var(--border)", background: "var(--bg-primary)", display: "grid", gap: 10 }}>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) auto", gap: 8, alignItems: "start" }}>
        <Textarea value={text} onChange={e => setText(e.target.value)} rows={2}
          placeholder="댓글 입력"
          style={{ fontFamily: "inherit" }} />
        <Btn variant="primary" type="button" onClick={submit} disabled={!text.trim()}>
          등록
        </Btn>
      </div>
      {comments.length === 0 && <div style={{ color: "var(--text-secondary)" }}>댓글이 없습니다.</div>}
      {comments.length > 0 && (
        <div style={{ display: "grid", gap: 8 }}>
          {comments.map(k => (
            <ThreadNode key={k.id} node={k} childrenByParent={childrenByParent}
              onReply={onReply} onDelete={onDelete} onToggleCheck={onToggleCheck}
              onEdit={onEdit} onReInform={onReInform}
              user={user} depth={0} constants={constants} />
          ))}
        </div>
      )}
    </div>
  );
}

function InformMailHistoryPanel({ root, onOpenMail }) {
  const history = root.mail_history || [];
  return (
    <div style={informConnectedPanel}>
      <section style={informConnectedSectionOnly}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
          <div style={{ ...informConnectedSectionTitle, marginBottom: 0 }}>메일 이력</div>
          <span style={{ color: "var(--text-secondary)" }}>{history.length}건</span>
          <Btn variant="outline" type="button" onClick={onOpenMail} style={{ marginLeft: "auto" }}>
            전송 창 열기
          </Btn>
        </div>
        {history.length === 0 && <div style={{ color: "var(--text-secondary)" }}>발송 이력이 없습니다.</div>}
        {history.slice().reverse().map((m, i) => (
          <div key={i} style={{ display: "grid", gridTemplateColumns: "150px 120px minmax(0,1fr) 80px", gap: 8, padding: "7px 0", borderBottom: i < history.length - 1 ? "1px dashed var(--border)" : "none", alignItems: "center" }}>
            <span style={{ color: "var(--text-secondary)", fontFamily: "monospace" }}>{(m.at || m.sent_at || "").replace("T", " ").slice(0, 16)}</span>
            <span>{m.actor || m.sender || "-"}</span>
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{m.subject || "-"}</span>
            <span style={{ color: "var(--text-secondary)" }}>{(m.to || []).length || 0}명</span>
          </div>
        ))}
      </section>
    </div>
  );
}

function MailPreviewPanel({ root, onOpenMail }) {
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [preview, setPreview] = useState(null);
  const draft = root?.mail_draft || {};
  useEffect(() => {
    setSubject(String((root?.mail_draft || {}).subject || ""));
    setBody(String((root?.mail_draft || {}).body || ""));
  }, [root?.id]);
  useEffect(() => {
    if (!root?.id) return;
    const h = setTimeout(() => {
      const q = new URLSearchParams();
      q.set("subject", subject || "");
      q.set("body", body || "");
      (draft.to || draft.recipients || []).forEach(em => q.append("to", em));
      (draft.to_users || []).forEach(un => q.append("to_users", un));
      (draft.groups || []).forEach(g => q.append("groups", g));
      (draft.extra_emails || []).forEach(em => q.append("to", em));
      sf(API + "/" + encodeURIComponent(root.id) + "/mail-preview?" + q.toString())
        .then(d => setPreview(d))
        .catch(() => setPreview(null));
    }, 250);
    return () => clearTimeout(h);
  }, [root?.id, subject, body, JSON.stringify(draft)]);
  const history = root.mail_history || [];
  const savedTargets = uniqueClean([
    ...((draft.to_users || []).map(v => `user:${v}`)),
    ...((draft.groups || []).map(v => `group:${v}`)),
    ...((draft.extra_emails || []).map(v => `mail:${v}`)),
    ...((draft.to || draft.recipients || []).map(v => `mail:${v}`)),
  ]);
  return (
    <div style={informConnectedPanel}>
      <section style={informConnectedSectionFirst}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
          <div style={{ ...informConnectedSectionTitle, marginBottom: 0 }}>수신자 미리보기</div>
          <span style={{ color: "var(--text-secondary)" }}>{(preview?.resolved_recipients || []).length}명</span>
          {savedTargets.length > 0 && <span style={{ color: "var(--text-secondary)" }}>저장 대상 {savedTargets.length}개</span>}
          <Btn variant="outline" type="button" onClick={onOpenMail} style={{ marginLeft: "auto" }}>전송 창 열기</Btn>
        </div>
        {savedTargets.length > 0 && (
          <div style={{ marginBottom: 6, color: "var(--text-secondary)", fontFamily: "monospace", lineHeight: 1.5 }}>
            {(draft.groups || []).map(g => `[group] ${g}`).concat(draft.to_users || []).concat(draft.extra_emails || []).join(", ")}
          </div>
        )}
        <div style={{ color: "var(--text-secondary)", fontFamily: "monospace", lineHeight: 1.5 }}>
          {(preview?.resolved_recipients || []).slice(0, 12).join(", ") || "모듈 수신자 없음"}
          {(preview?.resolved_recipients || []).length > 12 ? ` +${preview.resolved_recipients.length - 12}` : ""}
        </div>
      </section>
      <section style={{ ...informConnectedSection, display: "grid", gap: 8 }}>
        <label style={{ display: "grid", gap: 4 }}>
          <span style={{ fontWeight: 800 }}>제목</span>
          <Input value={subject} onChange={e => setSubject(e.target.value)} placeholder={preview?.subject || "자동 제목"} />
        </label>
        <label style={{ display: "grid", gap: 4 }}>
          <span style={{ fontWeight: 800 }}>본문</span>
          <Textarea value={body} onChange={e => setBody(e.target.value)} rows={4} placeholder="비워두면 자동 본문을 사용합니다." style={{ fontFamily: "inherit" }} />
        </label>
      </section>
      {preview?.html_body && (
        <section style={informConnectedSection}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8, flexWrap: "wrap" }}>
            <b style={{ ...informConnectedSectionTitle, marginBottom: 0 }}>메일 미리보기</b>
            <span style={{ color: "var(--text-secondary)" }}>수신 {(preview?.resolved_recipients || []).length}명</span>
            <span style={{ color: "var(--text-secondary)" }}>본문 {preview?.html_size_kb ?? 0} kB</span>
          </div>
          <div style={{ minHeight: "60vh", maxHeight: "70vh", overflowY: "auto", overflowX: "hidden", width: "100%", background: "var(--bg-secondary)", border: "1px solid var(--border)", borderRadius: 8, padding: 10 }}
            dangerouslySetInnerHTML={{ __html: sanitizeHtml(preview.html_body) }} />
        </section>
      )}
      <section style={informConnectedSectionLast}>
        <div style={informConnectedSectionTitle}>발송 이력</div>
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
    <div style={informConnectedPanel}>
      <section style={informConnectedSectionOnly}>
        <div style={informConnectedSectionTitle}>변경 이력</div>
      {rows.map((r, i) => (
        <div key={i} style={{ display: "grid", gridTemplateColumns: "150px 120px 120px minmax(0,1fr)", gap: 8, padding: "7px 0", borderBottom: i < rows.length - 1 ? "1px dashed var(--border)" : "none", alignItems: "center" }}>
          <span style={{ color: "var(--text-secondary)", fontFamily: "monospace" }}>{(r.at || "").replace("T", " ").slice(0, 16)}</span>
          <span>{r.actor || "-"}</span>
          <span style={{ fontWeight: 800 }}>{r.label}</span>
          <span style={{ color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.note || "-"}</span>
        </div>
      ))}
      </section>
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
    <div style={informConnectedPanel}>
      {images.length > 0 && (
        <section style={hasEmbed ? informConnectedSectionFirst : informConnectedSectionOnly}>
          <div style={informConnectedSectionTitle}>이미지 / 파일</div>
          <ImageGallery images={images} />
        </section>
      )}
      {hasEmbed && (
        <section style={images.length > 0 ? informConnectedSectionLast : informConnectedSectionOnly}>
          <EmbedTableView embed={root.embed_table} product={root.product} />
        </section>
      )}
    </div>
  );
}

/* 첨부 컬럼 후보 목록 — prefix(KNOB/MASK/FAB…) 끼리 묶어서 보여준다.
   그룹 머리를 누르면 그 계열 전체가 한 번에 켜지고 꺼진다. */
function ColumnPickList({ cols, selected, prefixOf, onToggle, onToggleGroup, maxHeight = "34vh" }) {
  const groups = useMemo(() => {
    const map = new Map();
    (cols || []).forEach(c => {
      const key = prefixOf(c);
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(c);
    });
    return Array.from(map.entries());
  }, [cols, prefixOf]);
  const picked = new Set(selected || []);
  return (
    <div style={{ minHeight: 160, maxHeight, overflowY: "auto", overflowX: "hidden",
      border: "1px solid var(--border)", borderRadius: 8, background: "var(--bg-primary)" }}>
      {groups.map(([prefix, list]) => {
        const onCount = list.filter(c => picked.has(c)).length;
        const allOn = onCount === list.length && list.length > 0;
        return (
          <section key={prefix}>
            <div onClick={() => onToggleGroup(list, !allOn)}
              title={allOn ? "이 계열 전체 해제" : "이 계열 전체 선택"}
              style={{ position: "sticky", top: 0, zIndex: 1, display: "flex", alignItems: "center", gap: 8,
                padding: "5px 9px", background: "var(--bg-secondary)", borderBottom: "1px solid var(--border)",
                borderTop: "1px solid var(--border)", cursor: "pointer", fontWeight: 900, fontFamily: "monospace" }}>
              <span style={{ color: allOn ? "var(--accent)" : "var(--text-primary)" }}>{prefix}</span>
              <span style={{ color: "var(--text-secondary)", fontWeight: 700 }}>{onCount}/{list.length}</span>
            </div>
            {list.map(c => {
              const on = picked.has(c);
              return (
                <label key={c} style={{ display: "flex", gap: 7, alignItems: "center", minHeight: 30,
                  padding: "4px 9px", borderBottom: "1px solid var(--border)",
                  background: on ? "var(--accent-glow)" : "transparent", cursor: "pointer",
                  fontFamily: "monospace", minWidth: 0 }}>
                  <input type="checkbox" checked={on} onChange={() => onToggle(c)} />
                  <span style={{ minWidth: 0, whiteSpace: "normal", wordBreak: "break-all", overflowWrap: "anywhere" }}>{c}</span>
                </label>
              );
            })}
          </section>
        );
      })}
      {groups.length === 0 && (
        <div style={{ padding: 20, textAlign: "center", color: "var(--text-secondary)" }}>표시할 컬럼이 없습니다</div>
      )}
    </div>
  );
}

function InformWizard({
  mode = "create", parentInform = null,
  form, setForm, constants, products, productContacts, lotOptions, fabSearch, setFabSearch, step, setStep,
  attachMode, setAttachMode, selectedSetIds, setSelectedSetIds, embedFetching, embedSchemaCols,
  embedCustomCols, setEmbedCustomCols, embedCustomSearch, setEmbedCustomSearch,
  setSnapshotTick, mailDraft, setMailDraft, mailMeta, setMailMeta, reasonTemplates, user, msg, setMsg, submitting = false, onSubmit, onClose,
  createImages = [], uploadingMain = false, onNotePaste, onNoteImages, onRemoveNoteImage,
}) {
  const isReInform = mode === "reinform";
  const productOptions = Array.from(new Set((products || []).map(p => stripMlPrefix(String(p || "").trim())).filter(Boolean))).sort();
  // v9.5.x: "검토 + 등록" 단계 삭제 — 메일 미리보기에서 바로 수정하고 등록한다.
  const steps = ["LOT 선택 + 모듈 내용", "SplitTable 첨부", "메일 미리보기 + 등록"];
  const lastStep = isReInform ? 1 : 2;
  const [setRows, setSetRows] = useState([]);
  const [setsLoading, setSetsLoading] = useState(false);
  const [setSearch, setSetSearch] = useState("");
  const [previewSet, setPreviewSet] = useState(null);
  const [setSnapshotState, setSetSnapshotState] = useState({ status: "idle", message: "" });
  const [newSetName, setNewSetName] = useState("");
  const [newSetCols, setNewSetCols] = useState([]);
  const [newSetSaving, setNewSetSaving] = useState(false);
  const [colPrefixFilter, setColPrefixFilter] = useState([]);   // 빈 배열 = 전체
  // 메일 미리보기 — 실제 발송에 쓰는 서버 렌더러(/mail-preview-draft) 결과.
  const [draftPreview, setDraftPreview] = useState(null);
  const [previewErr, setPreviewErr] = useState("");
  const [mailUploading, setMailUploading] = useState(false);
  const [stepEditTick, setStepEditTick] = useState(0);
  const [mailRecipients, setMailRecipients] = useState([]);
  const [mailGroups, setMailGroups] = useState({});
  const [publicMailGroups, setPublicMailGroups] = useState([]);
  const [pickedMailUsers, setPickedMailUsers] = useState([]);
  const [pickedMailGroups, setPickedMailGroups] = useState([]);
  const [extraMailEmails, setExtraMailEmails] = useState("");
  const [mailUserSearch, setMailUserSearch] = useState("");
  const mailMetaHydratedRef = useRef(false);
  const loadSetRows = () => {
    const raw = String(form.product || "").trim();
    if (!raw) { setSetRows([]); return Promise.resolve([]); }
    const product = raw.startsWith("ML_TABLE_") ? raw : `ML_TABLE_${raw}`;
    setSetsLoading(true);
    return sf(`${API}/splittable-sets?product=${encodeURIComponent(product)}`)
      .then(d => {
        const rows = Array.isArray(d.sets) ? d.sets : [];
        setSetRows(rows);
        setMsg("");
        return rows;
      })
      .catch(e => {
        setSetRows([]);
        setMsg("커스텀 세트 목록 로딩 실패: " + (e.message || e));
        return [];
      })
      .finally(() => setSetsLoading(false));
  };
  useEffect(() => {
    if (step !== 1 || attachMode !== "sets") return;
    loadSetRows();
  }, [step, attachMode, form.product]);
  useEffect(() => {
    if (step !== 2) return;
    sf(API + "/recipients").then(d => setMailRecipients(d.recipients || [])).catch(() => setMailRecipients([]));
    sf(API + "/mail-groups").then(d => setMailGroups(d.groups || {})).catch(() => setMailGroups({}));
    sf("/api/mail-groups/list").then(d => setPublicMailGroups(d.groups || [])).catch(() => setPublicMailGroups([]));
    if (mailMeta && !mailMetaHydratedRef.current) {
      setPickedMailUsers(Array.isArray(mailMeta.to_users) ? mailMeta.to_users : []);
      setPickedMailGroups(Array.isArray(mailMeta.groups) ? mailMeta.groups : []);
      setExtraMailEmails(Array.isArray(mailMeta.extra_emails) ? mailMeta.extra_emails.join(", ") : "");
      mailMetaHydratedRef.current = true;
    }
  }, [step]);
  const selectedSetIdSet = new Set(selectedSetIds || []);
  const selectedSetRowsFromCatalog = (setRows || []).filter(s => selectedSetIdSet.has(s.id));
  const selectedSetRowsFromParent = (form.embed?.attached_sets || [])
    .filter(s => {
      const key = s?.id || s?.name || s?.source;
      return key && selectedSetIdSet.has(key) && !selectedSetRowsFromCatalog.some(row => row.id === key);
    });
  const selectedSetRows = [...selectedSetRowsFromCatalog, ...selectedSetRowsFromParent];
  useEffect(() => {
    if (attachMode !== "sets") {
      setSetSnapshotState({ status: "idle", message: "" });
      return;
    }
    if (!selectedSetRows.length) {
      setSetSnapshotState({ status: "idle", message: "" });
      setForm(f => f.attach_embed ? { ...f, attach_embed: false, embed: emptyEmbedTable() } : f);
      return;
    }
    const attached = selectedSetRows.map(s => ({
      id: s.id || s.name || s.source,
      name: s.name,
      source: s.source,
      columns_count: s.columns_count || (s.columns || []).length || 0,
      wafer_count: s.wafer_count || (s.rows || []).length || 0,
      updated_at: s.updated_at || "",
      owner: s.owner || "",
      columns: s.columns || [],
      rows: s.rows || [],
    }));
    const baseEmbed = {
      source: `SplitTable selected sets/${stripMlPrefix(form.product || "")}`,
      columns: [],
      rows: [],
      note: `${attached.length} selected set(s) attached`,
      attached_sets: attached,
    };
    const snapshotCols = uniqueClean(selectedSetRows.flatMap(s => s.columns || []));
    const prod = String(form.product || "").trim();
    const lot = String(form.lot_id || "").trim();
    if (!prod || !lot || snapshotCols.length === 0) {
      setSetSnapshotState({
        status: snapshotCols.length === 0 ? "no_data" : "idle",
        message: snapshotCols.length === 0 ? "선택한 세트에 LOT snapshot으로 조회할 컬럼이 없습니다." : "",
      });
      setForm(f => ({ ...f, attach_embed: true, embed: baseEmbed }));
      return;
    }
    let alive = true;
    const mlProd = prod.startsWith("ML_TABLE_") ? prod : `ML_TABLE_${prod}`;
    setSetSnapshotState({ status: "loading", message: "SplitTable LOT snapshot 생성 중..." });
    postJson("/api/informs/splittable-snapshot", {
      product: mlProd,
      lot_id: lot,
      custom_cols: snapshotCols,
      is_fab_lot: (form.fab_lot_ids || []).includes(lot) || isFabLotInput(lot, lotOptions),
      display_mode: form.snapshot_mode || "matrix",
      show_step_ids: !!form.show_step_ids,
    })
      .then(d => {
        if (!alive) return;
        const embed = d?.embed || {};
        const hasData = hasLotSnapshotData(embed);
        setSetSnapshotState({
          status: hasData ? "success" : "no_data",
          message: hasData
            ? `${embedSnapshotRowCount(embed)} rows${embedSnapshotWaferCount(embed) ? ` / ${embedSnapshotWaferCount(embed)} wafers` : ""} snapshot 생성됨`
            : "데이터 없음: 선택 LOT/컬럼에 표시할 값이 없습니다.",
        });
        if (hasData) setMsg("");
        setForm(f => ({
          ...f,
          attach_embed: true,
          embed: {
            ...embed,
            source: embed.source || baseEmbed.source,
            note: embed.note || baseEmbed.note,
            attached_sets: attached,
          },
        }));
      })
      .catch(e => {
        if (!alive) return;
        const message = "SplitTable 스냅샷 생성 실패: " + (e.message || e);
        setSetSnapshotState({ status: "error", message });
        setMsg(message);
        setForm(f => ({ ...f, attach_embed: true, embed: baseEmbed }));
      });
    return () => { alive = false; };
  }, [attachMode, selectedSetIds, setRows, form.product, form.lot_id, form.snapshot_mode, form.show_step_ids, lotOptions, isReInform]);
  const validate = () => {
    if (step === 0) {
      if (!(form.product || "").trim()) { setMsg("product 를 선택해 주세요"); return false; }
      if (!uniqueClean([...(form.fab_lot_ids || []), form.lot_id]).length) { setMsg("lot 을 선택해 주세요"); return false; }
      if (!(form.module || "").trim()) { setMsg("module 을 선택해 주세요"); return false; }
      if (!(form.text || "").trim()) { setMsg("note 를 입력해 주세요"); return false; }
    }
    setMsg("");
    return true;
  };
  const next = () => {
    if (!validate()) return;
    if (step === 1 && embedFetching) {
      setMsg("SplitTable 스냅샷 생성이 끝난 뒤 다음 단계로 이동해 주세요.");
      return;
    }
    if (step === 1 && attachMode === "sets" && selectedSetRows.length > 0) {
      if (setSnapshotState.status === "loading") {
        setMsg("SplitTable LOT snapshot 생성이 끝난 뒤 다음 단계로 이동해 주세요.");
        return;
      }
      if (setSnapshotState.status === "error") {
        setMsg(setSnapshotState.message || "SplitTable 스냅샷 생성 실패");
        return;
      }
    }
    if (step === 1 && attachMode === "knob" && embedCustomCols.length > 0 && !(form.attach_embed && hasEmbedSnapshot(form.embed))) {
      setSnapshotTick(x => x + 1);
      setMsg("미리보기 생성 중입니다. 완료 후 다음 단계로 이동해 주세요.");
      return;
    }
    setStep(Math.min(lastStep, step + 1));
  };
  const prev = () => { setMsg(""); setStep(Math.max(0, step - 1)); };
  const toggleCol = (col) => {
    setForm(f => ({ ...f, attach_embed: false, embed: emptyEmbedTable() }));
    setEmbedCustomCols(embedCustomCols.includes(col)
      ? embedCustomCols.filter(x => x !== col)
      : [...embedCustomCols, col]);
  };
  const toggleNewCol = (col) => {
    setNewSetCols(newSetCols.includes(col)
      ? newSetCols.filter(x => x !== col)
      : [...newSetCols, col]);
  };
  const fabLotOptions = (lotOptions || []).filter(o => o.type !== "root");
  const lotIdFilterText = String(fabSearch || "").trim().toLowerCase();
  const visibleFabOptions = fabLotOptions
    .filter(o => !lotIdFilterText || String(o.value || "").toLowerCase().includes(lotIdFilterText));
  const selectedFabs = new Set(form.fab_lot_ids || []);
  const clearSplitAttachmentState = ({ clearSets = false, clearCols = false } = {}) => {
    if (clearSets) setSelectedSetIds([]);
    if (clearCols) {
      setEmbedCustomCols([]);
      setEmbedCustomSearch("");
    }
    setForm(f => ({ ...f, attach_embed: false, embed: emptyEmbedTable() }));
  };
  const addSelectedFab = (fab) => {
    const value = String(fab || "").trim();
    if (!value) return;
    clearSplitAttachmentState({ clearSets: true, clearCols: true });
    setForm(f => {
      const cur = new Set(f.fab_lot_ids || []);
      cur.add(value);
      const nextFabs = Array.from(cur);
      return { ...f, lot_id: nextFabs[0] || "", fab_lot_ids: nextFabs };
    });
    setFabSearch("");
  };
  const removeSelectedFab = (fab) => {
    clearSplitAttachmentState({ clearSets: true, clearCols: true });
    setForm(f => {
      const nextFabs = (f.fab_lot_ids || []).filter(v => v !== fab);
      return { ...f, lot_id: nextFabs[0] || "", fab_lot_ids: nextFabs, attach_embed: false, embed: emptyEmbedTable() };
    });
  };
  // 첨부 가능한 컬럼 — KNOB 뿐 아니라 MASK/FAB/INLINE/VM/TAG/CUSTOM 등 SplitTable
  // 이 보여주는 prefix 전부. prefix 필터로 좁혀 볼 수 있다.
  const availablePrefixes = Array.from(new Set((embedSchemaCols || []).map(colPrefixOf)))
    .sort((a, b) => SNAPSHOT_COL_PREFIXES.indexOf(a) - SNAPSHOT_COL_PREFIXES.indexOf(b));
  // PRODUCT/ROOT_LOT_ID/LOT_ID/WAFER_ID 는 표의 축이라 첨부 후보로 따로 보여주지 않는다.
  const selectableCols = (embedSchemaCols || []).filter(c => !isIdentityCol(c));
  const attachableCols = selectableCols.filter(c => (
    !colPrefixFilter.length || colPrefixFilter.includes(colPrefixOf(c))
  ));
  // 검색어에 콤마를 넣으면 토큰 여러 개로 친다 — "A,B,C" 로 한 번에 여러 컬럼을 고를 수 있게.
  const searchTokens = String(embedCustomSearch || "").split(",").map(t => t.trim().toLowerCase()).filter(Boolean);
  const matchTokens = (c) => !searchTokens.length || searchTokens.some(t => String(c).toLowerCase().includes(t));
  const filteredCols = attachableCols.filter(matchTokens).slice(0, 240);
  const filteredNewCols = selectableCols.filter(matchTokens).slice(0, 300);
  // Enter = 지금 걸러진 컬럼 전부 체크 (콤마 입력과 짝).
  const checkAllFiltered = (cols, setter) => {
    if (!cols.length) { setMsg("검색 결과가 없습니다"); return; }
    setForm(f => ({ ...f, attach_embed: false, embed: emptyEmbedTable() }));
    setter(cur => uniqueClean([...cur, ...cols]));
    setMsg("");
  };
  const saveNewSet = () => {
    const name = newSetName.trim();
    const cols = uniqueClean(newSetCols);
    if (!name) { setMsg("세트 이름을 입력해 주세요"); return; }
    if (!cols.length) { setMsg("컬럼을 하나 이상 선택해 주세요"); return; }
    setMsg("");
    setNewSetSaving(true);
    postJson("/api/splittable/customs/save", {
      name,
      username: user?.username || "",
      columns: cols,
    })
      .then(() => loadSetRows())
      .then(rows => {
        const found = rows.find(s => s.source === "custom" && s.name === name) || rows.find(s => s.name === name);
        if (found?.id) setSelectedSetIds(cur => uniqueClean([...(cur || []), found.id]));
        setEmbedCustomCols([]);
        setEmbedCustomSearch("");
        setForm(f => ({ ...f, attach_embed: false, embed: emptyEmbedTable() }));
        setAttachMode("sets");
        setNewSetName("");
        setNewSetCols([]);
      })
      .catch(e => setMsg(e.message || "세트 저장 실패"))
      .finally(() => setNewSetSaving(false));
  };
  const resolveMailGroupEmails = (name) => {
    if (mailGroups?.[name]) return mailGroups[name] || [];
    const pg = (publicMailGroups || []).find(g => g.name === name);
    if (!pg) return [];
    const out = new Set();
    (pg.members || []).forEach(un => {
      const r = mailRecipients.find(x => x.username === un);
      const em = r?.effective_email || r?.email;
      if (em && em.includes("@")) out.add(em);
    });
    (pg.extra_emails || []).forEach(em => { if (em && em.includes("@")) out.add(em); });
    return Array.from(out);
  };
  const allMailGroupNames = Array.from(new Set([
    ...Object.keys(mailGroups || {}),
    ...(publicMailGroups || []).map(g => g.name).filter(Boolean),
  ])).sort();
  const toggleMailUser = (username) => setPickedMailUsers(cur => cur.includes(username) ? cur.filter(x => x !== username) : [...cur, username]);
  const toggleMailGroup = (name) => setPickedMailGroups(cur => cur.includes(name) ? cur.filter(x => x !== name) : [...cur, name]);
  const selectedMailEmails = (() => {
    const out = new Set();
    pickedMailUsers.forEach(un => {
      const r = mailRecipients.find(x => x.username === un);
      const em = r?.effective_email || r?.email;
      if (em && em.includes("@")) out.add(em);
    });
    pickedMailGroups.forEach(g => resolveMailGroupEmails(g).forEach(em => { if (em && em.includes("@")) out.add(em); }));
    String(extraMailEmails || "").split(/[,\s;]+/).map(s => s.trim()).filter(s => s && s.includes("@")).forEach(em => out.add(em));
    return Array.from(out);
  })();
  const visibleMailRecipients = mailRecipients.filter(r => userMatches(r, mailUserSearch));
  const mailPreviewText = String(form.text || "").trim();
  const selectedFabList = uniqueClean(form.fab_lot_ids || []);
  const mailPreviewLot = selectedFabList[0] || form.lot_id || "";
  const multiLotPreview = selectedFabList.length > 1;
  const mailLotLabel = mailPreviewLot;
  const mailSubject = mailDraft.subject || defaultInformMailSubject(form, mailLotLabel, reasonTemplates || {});
  const mailBodyValue = typeof mailDraft.body === "string" ? mailDraft.body : (form.text || "");
  const mailOverLimit = !!draftPreview?.mail_over_limit;
  // 적용 공정이 여러 step 인 행 — 메일에 남길 줄만 고를 수 있게 param 단위로 묶는다.
  const stepLineGroups = useMemo(() => embedStepLineGroups(form.embed), [form.embed, stepEditTick]);
  const toggleStepLine = (param, line) => {
    const group = stepLineGroups.find(g => g.param === param);
    if (!group) return;
    const nextLines = group.lines.includes(line)
      ? group.lines.filter(x => x !== line)
      : group.allLines.filter(x => group.lines.includes(x) || x === line);
    if (!nextLines.length) { setMsg("적용 공정은 최소 한 줄은 남겨야 합니다"); return; }
    setForm(f => ({ ...f, embed: embedWithStepLines(f.embed, param, nextLines, group.allLines) }));
    setStepEditTick(x => x + 1);
  };
  const uploadMailImages = async (files) => {
    const out = [];
    for (const file of Array.from(files || [])) {
      const fd = new FormData();
      fd.append("file", file);
      const res = await sf("/api/informs/upload", { method: "POST", body: fd });
      if (res?.url) out.push(res);
    }
    return out;
  };
  const appendMailImages = (images) => {
    if (!images.length) return;
    const md = images.map(img => `![${img.filename || "image"}](${img.url})`).join("\n");
    setMailDraft(d => ({ ...d, body: `${typeof d.body === "string" ? d.body : (form.text || "")}\n${md}`.trim(), bodyEdited: true }));
  };
  const addMailImages = async (files) => {
    if (!files || !files.length) return;
    setMailUploading(true);
    try {
      appendMailImages(await uploadMailImages(files));
    } catch (e) {
      setMsg("이미지 업로드 실패: " + (e?.message || e));
    } finally {
      setMailUploading(false);
    }
  };
  const handleMailBodyPaste = async (e) => {
    const items = (e.clipboardData && e.clipboardData.items) || [];
    const files = [];
    for (const it of items) {
      if (it.kind === "file" && String(it.type || "").startsWith("image/")) {
        const f = it.getAsFile();
        if (f) files.push(f);
      }
    }
    if (!files.length) return;
    e.preventDefault();
    await addMailImages(files);
  };
  // 미리보기는 발송과 같은 서버 렌더러를 쓴다 — 위저드와 인폼 상세 메일 양식이 어긋나지 않게.
  useEffect(() => {
    if (step !== 2) return;
    let alive = true;
    setPreviewErr("");
    const timer = setTimeout(() => {
      postJson("/api/informs/mail-preview-draft", {
        product: stripMlPrefix(form.product || ""),
        lot_id: mailPreviewLot,
        root_lot_id: form.lot_id || mailPreviewLot,
        module: form.module || "",
        reason: form.reason || "PEMS",
        text: form.text || "",
        subject: mailSubject,
        body: mailBodyValue,
        author: user?.username || "",
        embed_table: form.attach_embed && hasEmbedSnapshot(form.embed) ? form.embed : null,
      })
        .then(d => { if (alive) setDraftPreview(d); })
        .catch(e => { if (alive) { setDraftPreview(null); setPreviewErr("메일 미리보기 생성 실패: " + (e?.message || e)); } });
    }, 300);
    return () => { alive = false; clearTimeout(timer); };
  }, [step, form.product, mailPreviewLot, form.module, form.text, form.attach_embed,
      embedSnapshotSummary(form.embed), stepEditTick, mailBodyValue, mailSubject]);
  const snapshotStatusText = (() => {
    if (attachMode === "knob") {
      if (embedFetching) return "미리보기 생성 중...";
      if (form.attach_embed && hasLotSnapshotData(form.embed)) return `${embedSnapshotRowCount(form.embed)} rows${embedSnapshotWaferCount(form.embed) ? ` / ${embedSnapshotWaferCount(form.embed)} wafers` : ""} 미리보기 생성됨`;
      if (embedCustomCols.length > 0) return "선택 컬럼 있음 · 미리보기 생성 필요";
      return "선택 컬럼 없음";
    }
    if (attachMode === "sets") {
      if (setSnapshotState.status === "loading") return setSnapshotState.message;
      if (setSnapshotState.message) return setSnapshotState.message;
      if (selectedSetRows.length > 0 && form.attach_embed && hasLotSnapshotData(form.embed)) return embedSnapshotSummary(form.embed);
      if (selectedSetRows.length > 0) return "세트 목록 미리보기만 있음 · LOT snapshot 대기";
      return "선택 세트 없음";
    }
    return "";
  })();
  const snapshotStatusTone = (() => {
    if (attachMode === "knob") {
      if (embedFetching) return INFO;
      if (form.attach_embed && hasLotSnapshotData(form.embed)) return OK;
      if (embedCustomCols.length > 0) return WARN;
      return INFO;
    }
    if (setSnapshotState.status === "success") return OK;
    if (setSnapshotState.status === "error") return BAD;
    if (setSnapshotState.status === "no_data") return WARN;
    return INFO;
  })();
  useEffect(() => {
    if (!setMailMeta) return;
    const extras = String(extraMailEmails || "")
      .split(/[,\s;]+/)
      .map(s => s.trim())
      .filter(s => s && s.includes("@"));
    setMailMeta?.({
      __hydrated: true,
      to: selectedMailEmails,
      recipients: selectedMailEmails,
      to_users: pickedMailUsers,
      groups: pickedMailGroups,
      extra_emails: extras,
      subject: mailSubject,
      body: mailDraft.body || mailPreviewText,
    });
  }, [
    step,
    selectedMailEmails.join("|"),
    pickedMailUsers.join("|"),
    pickedMailGroups.join("|"),
    extraMailEmails,
    mailSubject,
    mailDraft.body,
    mailPreviewText,
  ]);
  // v9.5.x — 팝업 Modal 대신 인폼 로그 페이지 본문을 차지하는 인라인 뷰로 렌더.
  // 감싸는 컨테이너만 Modal → section/div 로 바꾸고, 내부 단계 로직은 그대로 둔다.
  return (
    <section className="inform-wizard-shell" style={{ flex: 1, minHeight: 0, overflow: "hidden", display: "flex", flexDirection: "column", background: "var(--bg-primary)" }}>
      <div className="inform-wizard-frame" style={{ width: "100%", height: "100%", minHeight: 0, padding: "16px 24px 12px", boxSizing: "border-box", display: "flex", flexDirection: "column" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
          <button type="button" onClick={onClose} disabled={submitting}
            style={{ padding: "7px 12px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg-secondary)", color: "var(--text-primary)", fontWeight: 900, cursor: submitting ? "wait" : "pointer", fontSize: 14, whiteSpace: "nowrap", opacity: submitting ? 0.5 : 1 }}>
            ← 목록으로
          </button>
          <div style={{ fontSize: 16, fontWeight: 900 }}>{isReInform ? "재인폼 작성" : "신규 인폼 등록"}</div>
          {isReInform && parentInform?.id && (
            <div style={{ color: "var(--text-secondary)", fontFamily: "monospace" }}>
              parent {parentInform.id}
            </div>
          )}
          {!isReInform && <div style={{ marginLeft: "auto", color: "var(--text-secondary)" }}>draft 자동 저장</div>}
          {isReInform && <div style={{ marginLeft: "auto" }} />}
        </div>
        <div style={{ display: "grid", gridTemplateColumns: `repeat(${steps.length},1fr)`, gap: 6, marginBottom: 14 }}>
          {steps.map((label, i) => {
            const disabled = submitting || (isReInform && i === 2);
            return (
              <button key={label} type="button" disabled={disabled} onClick={() => { if (!disabled) setStep(i); }}
                style={{ padding: "7px 8px", borderRadius: 8, border: "1px solid " + (i === step ? "var(--accent)" : "var(--border)"), background: i === step ? "var(--accent)" : "var(--bg-primary)", color: i === step ? "#fff" : "var(--text-secondary)", fontSize: 14, fontWeight: 800, cursor: disabled ? "not-allowed" : "pointer", opacity: disabled ? 0.45 : 1 }}>
                {i + 1}. {label}
              </button>
            );
          })}
        </div>
        <div className={`inform-wizard-body inform-wizard-body-step-${step}`}>
        {step === 0 && (
          <div className="inform-wizard-lot-column" style={{ gap: 12, minHeight: 0 }}>
            <label style={{ display: "grid", gap: 5, maxWidth: 520 }}>
              <span style={{ fontWeight: 800 }}>product</span>
              <Select value={form.product} disabled={isReInform} onChange={e => {
                setSelectedSetIds([]);
                setEmbedCustomCols([]);
                setEmbedCustomSearch("");
                setAttachMode("sets");
                setFabSearch("");
                setForm(f => ({ ...f, product: e.target.value, lot_id: "", fab_lot_ids: [], attach_embed: false, embed: emptyEmbedTable() }));
              }} style={{ opacity: isReInform ? 0.75 : 1 }}>
                <option value="">-- product 선택 --</option>
                {productOptions.map(p => <option key={p} value={p}>{p}</option>)}
              </Select>
            </label>
            <section className="inform-wizard-lot-panel" style={{ border: "1px solid var(--border)", borderRadius: 10, background: "var(--bg-card)", padding: 10, display: "grid", gridTemplateRows: "auto auto minmax(0, 1fr) auto", gap: 8, flex: 1, minHeight: 0 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ fontWeight: 900 }}>LOT_ID 검색 · 추가</span>
                  <span style={{ color: "var(--text-secondary)", fontFamily: "monospace" }}>{selectedFabs.size}개 추가됨</span>
                  <Btn size="sm" type="button" disabled={isReInform} onClick={() => {
                    clearSplitAttachmentState({ clearSets: true, clearCols: true });
                    setForm(f => ({ ...f, lot_id: "", fab_lot_ids: [], attach_embed: false, embed: emptyEmbedTable() }));
                  }}
                    style={{ marginLeft: "auto" }}>
                    선택 해제
                  </Btn>
                </div>
                <Input value={fabSearch} onChange={e => setFabSearch(e.target.value)}
                  onKeyDown={e => {
                    if (e.key !== "Enter" || e.nativeEvent?.isComposing || e.keyCode === 229) return;
                    e.preventDefault();
                    const first = visibleFabOptions.find(o => !selectedFabs.has(o.value));
                    if (first) addSelectedFab(first.value);
                  }}
                  placeholder="SplitTable처럼 LOT_ID 입력 또는 검색"
                  style={{ fontFamily: "monospace", maxWidth: 520 }} disabled={!form.product || isReInform} />
                <div className="inform-wizard-lot-options" style={{ minHeight: 0, overflow: "auto", border: "1px solid var(--border)", borderRadius: 8, background: "var(--bg-primary)" }}>
                  {!form.product && <div style={{ padding: 18, textAlign: "center", color: "var(--text-secondary)" }}>product 선택 후 후보 표시</div>}
                  {form.product && visibleFabOptions.length === 0 && <div style={{ padding: 18, textAlign: "center", color: "var(--text-secondary)" }}>LOT_ID 후보 없음</div>}
                  {visibleFabOptions.slice(0, 50).map(o => {
                    const on = selectedFabs.has(o.value);
                    return (
                      <button key={o.value} type="button" disabled={isReInform || on} onClick={() => addSelectedFab(o.value)}
                        style={{ width: "100%", display: "flex", alignItems: "center", gap: 8, minHeight: 34, padding: "6px 10px", border: "none", borderBottom: "1px solid var(--border)", background: on ? "var(--accent-glow)" : "transparent", color: on ? "var(--accent)" : "var(--text-primary)", cursor: isReInform || on ? "default" : "pointer", fontFamily: "monospace", fontWeight: 800, textAlign: "left" }}
                        onMouseEnter={e => { if (!on) e.currentTarget.style.background = "var(--bg-hover)"; }}
                        onMouseLeave={e => { e.currentTarget.style.background = on ? "var(--accent-glow)" : "transparent"; }}>
                        <span style={{ flex: 1 }}>{o.value}</span>
                        <span style={{ fontFamily: "inherit", color: on ? "var(--accent)" : "var(--text-secondary)" }}>{on ? "추가됨" : "+ 추가"}</span>
                      </button>
                    );
                  })}
                </div>
                {selectedFabs.size > 0 && (
                  <div style={{ display: "grid", gap: 6, padding: 8, border: "1px solid var(--border)", borderRadius: 8, background: "var(--bg-primary)" }}>
                    <div style={{ fontWeight: 900 }}>추가된 LOT_ID</div>
                    <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                      {(form.fab_lot_ids || []).map(fab => (
                        <button key={fab} type="button" disabled={isReInform} onClick={() => removeSelectedFab(fab)}
                          title="선택 해제"
                          style={{ display: "inline-flex", alignItems: "center", gap: 5, padding: "4px 7px", borderRadius: 7, border: "1px solid var(--accent)", background: "var(--accent-glow)", color: "var(--accent)", cursor: isReInform ? "default" : "pointer", fontFamily: "monospace", fontWeight: 900, opacity: isReInform ? 0.8 : 1 }}>
                          {fab} <span style={{ fontFamily: "inherit" }}>×</span>
                        </button>
                      ))}
                    </div>
                  </div>
                )}
            </section>
          </div>
        )}
        {step === 0 && (
          <div className="inform-wizard-module-column" style={{ gap: 12, minHeight: 0 }}>
            <label style={{ display: "grid", gap: 5, maxWidth: 520 }}>
              <span style={{ fontWeight: 800 }}>module</span>
              <Select value={form.module} disabled={isReInform} onChange={e => setForm(f => ({ ...f, module: e.target.value, reason: "PEMS" }))} style={{ opacity: isReInform ? 0.75 : 1 }}>
                <option value="">-- module --</option>
                {(constants.modules || []).map(m => <option key={m} value={m}>{m}</option>)}
              </Select>
            </label>
            <label className="inform-wizard-note-field" style={{ display: "flex", flexDirection: "column", gap: 5, flex: 1, minHeight: 0 }}>
              <span style={{ fontWeight: 800 }}>note</span>
              <RichBoardEditor value={form.text}
                onChange={(text) => setForm(f => ({ ...f, text }))}
                uploadUrl="/api/informs/upload" minHeight={245}
                placeholder="note를 입력하세요. 이미지 또는 Excel 표는 Ctrl+V로 본문에 바로 붙여넣을 수 있습니다."
                onUploadError={(error) => setMsg("이미지 붙여넣기 실패: " + (error?.message || error))} />
            </label>
          </div>
        )}
        {step === 1 && (
          <div style={{ display: "grid", gap: 12 }}>
            {isReInform && (
              <div style={{ display: "grid", gridTemplateColumns: "120px minmax(0,1fr)", gap: 8, padding: 10, border: "1px solid var(--border)", borderRadius: 10, background: "var(--bg-primary)", color: "var(--text-secondary)" }}>
                <b style={{ color: "var(--text-primary)" }}>원본</b>
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {stripMlPrefix(form.product || "-")} · {form.lot_id || "-"} · {form.module || "-"}
                </span>
                <b style={{ color: "var(--text-primary)" }}>본문</b>
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{form.text || "(내용 없음)"}</span>
              </div>
            )}
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {[["knob", "KNOB / CUSTOM 컬럼 직접 첨부"], ["sets", "기존 커스텀 세트"], ["new", "새 커스텀 세트 만들기"]].map(([key, label]) => (
                <button key={key} type="button" onClick={() => {
                  if (key !== attachMode) {
                    setSelectedSetIds([]);
                    setPreviewSet(null);
                    setEmbedCustomCols([]);
                    setEmbedCustomSearch("");
                    setForm(f => ({ ...f, attach_embed: false, embed: emptyEmbedTable() }));
                  }
                  setAttachMode(key);
                }}
                  style={{ padding: "8px 12px", borderRadius: 8, border: "1px solid " + (attachMode === key ? "var(--accent)" : "var(--border)"), background: attachMode === key ? "var(--accent)" : "var(--bg-primary)", color: attachMode === key ? "#fff" : "var(--text-secondary)", fontWeight: 800, cursor: "pointer", fontSize: 14 }}>
                  {label}
                </button>
              ))}
              {snapshotStatusText && (
                <span style={{ alignSelf: "center", padding: "3px 8px", borderRadius: 999, border: `1px solid ${snapshotStatusTone.fg}`, color: snapshotStatusTone.fg, background: snapshotStatusTone.bg, fontWeight: 800 }}>
                  {snapshotStatusText}
                </span>
              )}
            </div>
            {/* 표시 형식 + 적용 공정 — SplitTable 화면과 같은 옵션을 스냅샷에도 그대로 건다. */}
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <span style={{ fontWeight: 900 }}>표시 형식</span>
              {SNAPSHOT_MODES.map(([key, label]) => {
                const on = (form.snapshot_mode || "matrix") === key;
                return (
                  <button key={key} type="button" onClick={() => setForm(f => ({ ...f, snapshot_mode: key }))}
                    style={{ padding: "6px 11px", borderRadius: 8, border: "1px solid " + (on ? "var(--accent)" : "var(--border)"), background: on ? "var(--accent)" : "var(--bg-primary)", color: on ? "#fff" : "var(--text-secondary)", fontWeight: 800, cursor: "pointer", fontSize: 14 }}>
                    {label}
                  </button>
                );
              })}
              <label title="항목명을 SplitTable 처럼 연결된 공정(step_id) 으로 바꿔 표시합니다"
                style={{ display: "inline-flex", alignItems: "center", gap: 8, padding: "6px 10px", border: "1px solid var(--border)", borderRadius: 8, background: "var(--bg-primary)", color: "var(--text-primary)", fontWeight: 800, cursor: "pointer" }}>
                <input type="checkbox" checked={!!form.show_step_ids} onChange={e => setForm(f => ({ ...f, show_step_ids: e.target.checked }))} />
                <span>적용 공정 표시</span>
              </label>
            </div>
            {attachMode === "sets" && (
              <div style={{ border: "1px solid var(--border)", borderRadius: 10, padding: 12, background: "var(--bg-card)", display: "grid", gap: 8 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                  <div style={{ fontWeight: 900 }}>📋 첨부할 세트 선택</div>
                  <span style={{ color: "var(--text-secondary)" }}>{setsLoading ? "loading..." : `${setRows.length}개`}</span>
                  <Input value={setSearch} onChange={e => setSetSearch(e.target.value)} placeholder="세트 이름 검색"
                    style={{ marginLeft: "auto", width: 220, fontSize: 13 }} />
                  <Btn size="sm" type="button" onClick={() => {
                    setForm(f => ({ ...f, attach_embed: false, embed: emptyEmbedTable() }));
                    setSelectedSetIds((setRows || []).map(s => s.id));
                  }}>
                    전체 선택
                  </Btn>
                  <Btn size="sm" type="button" onClick={() => {
                    setForm(f => ({ ...f, attach_embed: false, embed: emptyEmbedTable() }));
                    setSelectedSetIds([]);
                  }}>
                    선택 해제
                  </Btn>
                </div>
                <div style={{ maxHeight: 340, overflow: "auto", border: "1px solid var(--border)", borderRadius: 8 }}>
                  {(setRows || [])
                    .filter(s => !setSearch || String(s.name || "").toLowerCase().includes(setSearch.toLowerCase()))
                    .map(s => {
                      const on = (selectedSetIds || []).includes(s.id);
                      return (
                        <label key={s.id} style={{ display: "grid", gridTemplateColumns: "24px minmax(0,1fr) 90px 90px 140px 76px", gap: 8, alignItems: "center", minHeight: 38, padding: "6px 9px", borderBottom: "1px solid var(--border)", background: on ? "var(--accent-glow)" : "transparent", cursor: "pointer" }}>
                          <input type="checkbox" checked={on} onChange={() => {
                            setForm(f => ({ ...f, attach_embed: false, embed: emptyEmbedTable() }));
                            setSelectedSetIds(cur => cur.includes(s.id) ? cur.filter(x => x !== s.id) : [...cur, s.id]);
                          }} />
                          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontWeight: 900 }}>{s.name || "set"}</span>
                          <span style={{ color: "var(--text-secondary)", fontFamily: "monospace" }}>{s.columns_count || 0} cols</span>
                          <span style={{ color: "var(--text-secondary)", fontFamily: "monospace" }}>{s.wafer_count || 0} rows</span>
                          <span style={{ color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{(s.updated_at || "").replace("T", " ").slice(0, 16) || s.source}</span>
                          <Btn size="sm" type="button" onClick={e => { e.preventDefault(); e.stopPropagation(); setPreviewSet(s); }}>
                            미리보기
                          </Btn>
                        </label>
                      );
                    })}
                  {!setsLoading && setRows.length === 0 && <div style={{ padding: 24, textAlign: "center", color: "var(--text-secondary)" }}>저장된 세트가 없습니다</div>}
                </div>
                <div style={{ color: "var(--text-secondary)" }}>선택 {selectedSetRows.length}개 · 세트 목록 미리보기와 실제 LOT snapshot 미리보기는 아래에서 구분됩니다</div>
                {previewSet && (
                  <div style={{ border: "1px solid var(--border)", borderRadius: 8, padding: 10, background: "var(--bg-primary)" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                      <b>원본 세트 목록 · {previewSet.name}</b>
                      <span style={{ color: "var(--text-secondary)" }}>{previewSet.source} · {(previewSet.columns || []).length} cols · {(previewSet.rows || []).length} rows</span>
                      <button type="button" onClick={() => setPreviewSet(null)} style={{ marginLeft: "auto", border: "none", background: "transparent", color: "var(--text-secondary)", cursor: "pointer", fontSize: 18 }}>×</button>
                    </div>
                    {/* 전체폭 뷰라 열/행을 더 보여준다. */}
                    <div style={{ maxHeight: 420, overflow: "auto" }}>
                      <table style={{ width: "100%", borderCollapse: "collapse", tableLayout: "fixed", fontSize: 11, fontFamily: "monospace" }}>
                        <thead><tr>{(previewSet.columns || []).slice(0, 24).map(c => <th key={c} style={{ border: "1px solid var(--border)", padding: "2px 3px", background: "var(--bg-secondary)", textAlign: "left", wordBreak: "break-all" }}>{c}</th>)}</tr></thead>
                        <tbody>{(previewSet.rows || []).slice(0, 20).map((row, ri) => <tr key={ri}>{row.slice(0, 24).map((v, ci) => <td key={ci} style={{ border: "1px solid var(--border)", padding: "2px 3px", wordBreak: "break-all" }}>{v}</td>)}</tr>)}</tbody>
                      </table>
                    </div>
                  </div>
                )}
              </div>
            )}
            {attachMode === "knob" && (
              <div style={{ border: "1px solid var(--border)", borderRadius: 10, padding: 12, background: "var(--bg-card)", display: "grid", gap: 8 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                  <b>컬럼 직접 첨부 <span style={{ fontWeight: 600, color: "var(--text-secondary)" }}>(KNOB · MASK · FAB · INLINE · VM · TAG …)</span></b>
                  <span style={{ color: "var(--text-secondary)" }}>선택 {embedCustomCols.length}개</span>
                  <Btn size="sm" type="button" onClick={() => {
                    setForm(f => ({ ...f, attach_embed: false, embed: emptyEmbedTable() }));
                    setEmbedCustomCols(filteredCols);
                  }}
                    style={{ marginLeft: "auto" }}>
                    전체 선택
                  </Btn>
                  <Btn size="sm" type="button" onClick={() => {
                    setForm(f => ({ ...f, attach_embed: false, embed: emptyEmbedTable() }));
                    setEmbedCustomCols([]);
                  }}>
                    선택 해제
                  </Btn>
                </div>
                {/* prefix 필터 — 아무것도 안 고르면 전체 계열을 다 보여준다. */}
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
                  <button type="button" onClick={() => setColPrefixFilter([])}
                    style={{ padding: "4px 10px", borderRadius: 999, border: "1px solid " + (colPrefixFilter.length === 0 ? "var(--accent)" : "var(--border)"), background: colPrefixFilter.length === 0 ? "var(--accent)" : "var(--bg-primary)", color: colPrefixFilter.length === 0 ? "#fff" : "var(--text-secondary)", fontWeight: 800, cursor: "pointer" }}>
                    전체
                  </button>
                  {availablePrefixes.map(p => {
                    const on = colPrefixFilter.includes(p);
                    return (
                      <button key={p} type="button"
                        onClick={() => setColPrefixFilter(cur => cur.includes(p) ? cur.filter(x => x !== p) : [...cur, p])}
                        style={{ padding: "4px 10px", borderRadius: 999, border: "1px solid " + (on ? "var(--accent)" : "var(--border)"), background: on ? "var(--accent)" : "var(--bg-primary)", color: on ? "#fff" : "var(--text-secondary)", fontWeight: 800, cursor: "pointer", fontFamily: "monospace" }}>
                        {p}
                      </button>
                    );
                  })}
                </div>
                <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                  <Input value={embedCustomSearch} onChange={e => setEmbedCustomSearch(e.target.value)}
                    onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); checkAllFiltered(filteredCols, setEmbedCustomCols); } }}
                    placeholder="컬럼 검색 — 콤마로 여러 개 (예: 1.0 STI, GATE, VM_)"
                    style={{ flex: 1, minWidth: 220, fontFamily: "monospace" }} />
                  <Btn size="sm" variant="outline" type="button"
                    onClick={() => checkAllFiltered(filteredCols, setEmbedCustomCols)}>
                    검색 결과 {filteredCols.length}개 체크
                  </Btn>
                </div>
                {embedCustomCols.length > 0 && (
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap", maxHeight: 70, overflow: "auto", padding: 8, border: "1px solid var(--border)", borderRadius: 8, background: "var(--bg-primary)" }}>
                    {embedCustomCols.map(c => (
                      <button key={c} type="button" onClick={() => toggleCol(c)}
                        style={{ maxWidth: "100%", padding: "3px 7px", borderRadius: 7, border: "1px solid var(--accent)", background: "var(--accent-glow)", color: "var(--accent)", cursor: "pointer", fontFamily: "monospace", fontWeight: 900, whiteSpace: "normal", wordBreak: "break-all", overflowWrap: "anywhere", textAlign: "left" }}>
                        {c} ×
                      </button>
                    ))}
                  </div>
                )}
                <ColumnPickList cols={filteredCols} selected={embedCustomCols} prefixOf={colPrefixOf}
                  onToggle={toggleCol}
                  onToggleGroup={(list, on) => {
                    setForm(f => ({ ...f, attach_embed: false, embed: emptyEmbedTable() }));
                    setEmbedCustomCols(cur => (on ? uniqueClean([...cur, ...list]) : cur.filter(x => !list.includes(x))));
                  }} />
              </div>
            )}
            {attachMode === "new" && (
              <div style={{ border: "1px solid var(--border)", borderRadius: 10, padding: 12, background: "var(--bg-card)", display: "grid", gap: 8 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <b>새 커스텀 세트 만들기</b>
                  <span style={{ color: "var(--text-secondary)" }}>만든 뒤 자동 선택</span>
                </div>
                <Input value={newSetName} onChange={e => setNewSetName(e.target.value)} placeholder="세트 이름" />
                <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                  <Input value={embedCustomSearch} onChange={e => setEmbedCustomSearch(e.target.value)}
                    onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); checkAllFiltered(filteredNewCols, setNewSetCols); } }}
                    placeholder="컬럼 검색 — 콤마로 여러 개 (예: 1.0 STI, GATE, VM_)"
                    style={{ flex: 1, minWidth: 220, fontFamily: "monospace" }} />
                  <Btn size="sm" variant="outline" type="button"
                    onClick={() => checkAllFiltered(filteredNewCols, setNewSetCols)}>
                    검색 결과 {filteredNewCols.length}개 체크
                  </Btn>
                </div>
                {newSetCols.length > 0 && (
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap", maxHeight: 64, overflow: "auto", padding: 8, border: "1px solid var(--border)", borderRadius: 8, background: "var(--bg-primary)" }}>
                    {newSetCols.map(c => (
                      <button key={c} type="button" onClick={() => toggleNewCol(c)}
                        style={{ maxWidth: "100%", padding: "3px 7px", borderRadius: 7, border: "1px solid var(--accent)", background: "var(--accent-glow)", color: "var(--accent)", cursor: "pointer", fontFamily: "monospace", fontWeight: 900, whiteSpace: "normal", wordBreak: "break-all", overflowWrap: "anywhere", textAlign: "left" }}>
                        {c} ×
                      </button>
                    ))}
                  </div>
                )}
                <ColumnPickList cols={filteredNewCols} selected={newSetCols} prefixOf={colPrefixOf}
                  onToggle={toggleNewCol}
                  onToggleGroup={(list, on) => setNewSetCols(cur => (on ? uniqueClean([...cur, ...list]) : cur.filter(x => !list.includes(x))))} />
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ color: "var(--text-secondary)" }}>선택 {newSetCols.length}개</span>
                  <Btn variant="primary" type="button" disabled={newSetSaving} onClick={saveNewSet}
                    style={{ marginLeft: "auto" }}>
                    {newSetSaving ? "저장 중..." : "세트 만들기"}
                  </Btn>
                </div>
              </div>
            )}
            <Btn variant="outline" type="button" onClick={() => setSnapshotTick(x => x + 1)}
              disabled={attachMode !== "knob" || embedCustomCols.length === 0 || embedFetching}
              style={{ justifySelf: "start" }}>
              {embedFetching ? "미리보기 생성 중..." : "미리보기 생성"}
            </Btn>
            {attachMode === "sets" && form.attach_embed && selectedSetRows.length > 0 && (
              <div style={{ padding: "8px 10px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-secondary)", fontWeight: 800 }}>
                실제 LOT snapshot 미리보기 · {hasLotSnapshotData(form.embed) ? embedSnapshotSummary(form.embed) : "데이터 없음"}
              </div>
            )}
            {form.attach_embed && hasLotSnapshotData(form.embed) && <EmbedTableView embed={form.embed} product={form.product} />}
          </div>
        )}
        {step === 2 && (
          <div className="inform-wizard-mail-grid" style={{ display: "grid", gap: 12, minHeight: 0 }}>
            <section className="inform-wizard-mail-recipients" style={{ border: "1px solid var(--border)", borderRadius: 10, padding: 12, background: "var(--bg-card)", display: "grid", gap: 10 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <b>수신자 선택</b>
                <span style={{ color: "var(--text-secondary)" }}>선택 {selectedMailEmails.length}명</span>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)", gap: 12, alignItems: "stretch" }}>
                <div style={{ display: "grid", gap: 7, gridTemplateRows: "auto 150px" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ fontWeight: 800 }}>메일 그룹</span>
                    <span style={{ color: "var(--text-secondary)" }}>{pickedMailGroups.length}/{allMailGroupNames.length}</span>
                  </div>
                  <div style={{ height: 150, overflow: "auto", display: "flex", alignContent: "flex-start", flexWrap: "wrap", gap: 6, padding: 8, border: "1px solid var(--border)", borderRadius: 8, background: "var(--bg-primary)" }}>
                    {allMailGroupNames.length === 0 && <span style={{ color: "var(--text-secondary)" }}>등록된 그룹 없음</span>}
                    {allMailGroupNames.map(name => {
                      const on = pickedMailGroups.includes(name);
                      const count = resolveMailGroupEmails(name).length;
                      return (
                        <button key={name} type="button" onClick={() => toggleMailGroup(name)}
                          style={{ padding: "5px 9px", borderRadius: 999, border: "1px solid " + (on ? "var(--accent)" : "var(--border)"), background: on ? "var(--accent)" : "var(--bg-card)", color: on ? "#fff" : "var(--text-primary)", cursor: "pointer", fontWeight: 800 }}>
                          {name} · {count}
                        </button>
                      );
                    })}
                  </div>
                </div>
                <div style={{ display: "grid", gap: 7, gridTemplateRows: "auto 150px" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ fontWeight: 800 }}>개인</span>
                    <span style={{ color: "var(--text-secondary)" }}>{pickedMailUsers.length} 선택</span>
                    <Input value={mailUserSearch} onChange={e => setMailUserSearch(e.target.value)} placeholder="유저/이메일 검색"
                      style={{ marginLeft: "auto", width: 190, fontSize: 13 }} />
                  </div>
                  <div style={{ height: 150, overflow: "auto", border: "1px solid var(--border)", borderRadius: 8, background: "var(--bg-primary)" }}>
                    {visibleMailRecipients.length === 0 && <div style={{ padding: 14, textAlign: "center", color: "var(--text-secondary)" }}>유저 없음</div>}
                    {visibleMailRecipients.map(r => {
                      const on = pickedMailUsers.includes(r.username);
                      const nm = String(r.name || "").trim();
                      const em = r.effective_email || r.email || "";
                      return (
                        <label key={r.username} style={{ display: "flex", alignItems: "center", gap: 8, minHeight: 34, padding: "5px 9px", borderBottom: "1px solid var(--border)", background: on ? "var(--accent-glow)" : "transparent", cursor: "pointer" }}>
                          <input type="checkbox" checked={on} onChange={() => toggleMailUser(r.username)} />
                          <span style={{ fontWeight: 800 }}>{nm || r.username}</span>
                          <span style={{ color: em ? "var(--text-secondary)" : WARN.fg, fontFamily: "monospace", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{nm ? `(${r.username}) ` : ""}{em || "email 미설정"}</span>
                        </label>
                      );
                    })}
                  </div>
                </div>
              </div>
              <Input value={extraMailEmails} onChange={e => setExtraMailEmails(e.target.value)}
                placeholder="추가 이메일 (콤마/공백/세미콜론 구분)" style={{ fontFamily: "monospace" }} />
              <label style={{ display: "grid", gap: 5 }}>
                <span style={{ fontWeight: 800 }}>제목</span>
                <Input value={mailDraft.subject || ""}
                  onChange={e => setMailDraft(d => ({ ...d, subject: e.target.value, subjectEdited: true }))} />
              </label>
            </section>
            {/* 메일 본문 편집 — 여기서 고친 내용이 그대로 발송된다. */}
            <section className="inform-wizard-mail-editor" style={{ padding: 12, border: "1px solid var(--border)", borderRadius: 10, background: "var(--bg-card)", display: "grid", gap: 8 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <b>메일 본문</b>
                <span style={{ color: "var(--text-secondary)" }}>비워 두면 인폼 note 가 그대로 나갑니다</span>
                <label style={{ marginLeft: "auto", padding: "6px 11px", borderRadius: 8, border: "1px solid var(--accent)", color: "var(--accent)", background: "var(--accent-glow)", fontWeight: 800, cursor: mailUploading ? "wait" : "pointer" }}>
                  {mailUploading ? "업로드 중..." : "＋ 사진 추가"}
                  <input type="file" accept="image/*" multiple style={{ display: "none" }} disabled={mailUploading}
                    onChange={e => { addMailImages(e.target.files); e.target.value = ""; }} />
                </label>
              </div>
              <Textarea rows={7} value={mailBodyValue} onPaste={handleMailBodyPaste}
                onChange={e => setMailDraft(d => ({ ...d, body: e.target.value, bodyEdited: true }))}
                placeholder="메일로 보낼 본문 (사진은 붙여넣기 또는 '사진 추가')" style={{ fontFamily: "inherit" }} />
              {stepLineGroups.length > 0 && (
                <div style={{ display: "grid", gap: 6, padding: 10, border: "1px solid var(--border)", borderRadius: 8, background: "var(--bg-primary)" }}>
                  <div style={{ fontWeight: 900 }}>적용 공정 정리 <span style={{ fontWeight: 600, color: "var(--text-secondary)" }}>— 여러 step 중 메일에 남길 것만 켜 두세요</span></div>
                  {stepLineGroups.map(g => (
                    <div key={g.param} style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
                      <span style={{ fontFamily: "monospace", color: "var(--text-secondary)", minWidth: 160 }}>{g.param}</span>
                      {g.allLines.map(line => {
                        const on = g.lines.includes(line);
                        return (
                          <button key={line} type="button" onClick={() => toggleStepLine(g.param, line)}
                            style={{ padding: "3px 9px", borderRadius: 999, border: "1px solid " + (on ? "var(--accent)" : "var(--border)"), background: on ? "var(--accent)" : "var(--bg-card)", color: on ? "#fff" : "var(--text-secondary)", fontFamily: "monospace", fontWeight: 800, cursor: "pointer" }}>
                            {line}
                          </button>
                        );
                      })}
                    </div>
                  ))}
                </div>
              )}
            </section>
            {/* 실제 발송될 HTML — 인폼 상세의 메일 다이얼로그와 같은 서버 렌더러를 쓴다. */}
            <section className="inform-wizard-mail-preview" style={{ width: "100%", minHeight: 0, padding: 12, border: "1px solid var(--border)", borderRadius: 10, background: "var(--bg-card)", display: "flex", flexDirection: "column" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10, flexWrap: "wrap" }}>
                <b>메일 미리보기</b>
                <span style={{ color: "var(--text-secondary)" }}>수신 {selectedMailEmails.length}명</span>
                <span style={{ color: "var(--text-secondary)" }}>제목 {mailSubject}</span>
                {multiLotPreview && <span style={{ color: "var(--text-secondary)", fontFamily: "monospace" }}>미리보기 대상 {mailPreviewLot}</span>}
                {draftPreview && (
                  <span style={{ marginLeft: "auto", padding: "3px 9px", borderRadius: 999, fontWeight: 800,
                    border: `1px solid ${mailOverLimit ? BAD.fg : OK.fg}`, color: mailOverLimit ? BAD.fg : OK.fg,
                    background: mailOverLimit ? BAD.bg : OK.bg }}>
                    {draftPreview.mail_total_kb} kB / {draftPreview.mail_limit_mb} MB
                  </span>
                )}
              </div>
              {mailOverLimit && (
                <div style={{ marginBottom: 10, padding: "8px 10px", borderRadius: 8, border: `1px solid ${BAD.fg}`, color: BAD.fg, background: BAD.bg, fontWeight: 800 }}>
                  메일 용량이 한도({draftPreview?.mail_limit_mb} MB)를 초과했습니다 ({draftPreview?.mail_total_kb} kB). 사진이나 첨부 컬럼을 줄여야 등록할 수 있습니다.
                </div>
              )}
              {previewErr && (
                <div style={{ marginBottom: 10, padding: "8px 10px", borderRadius: 8, border: `1px solid ${WARN.fg}`, color: WARN.fg, background: WARN.bg }}>{previewErr}</div>
              )}
              {multiLotPreview && (
                <div style={{ marginBottom: 10, padding: "8px 10px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-secondary)", fontWeight: 800 }}>
                  여러 LOT_ID 중 가장 위에 선택된 LOT_ID만 미리보기로 표시합니다.
                </div>
              )}
              <div className="inform-wizard-mail-preview-scroll" style={{ flex: 1, minHeight: 0, overflow: "auto", padding: 12, border: "1px solid var(--border)", borderRadius: 8, background: "#ffffff", color: "#111827" }}>
                {draftPreview?.html_body
                  ? <div dangerouslySetInnerHTML={{ __html: sanitizeHtml(draftPreview.html_body) }} />
                  : <Loading text="메일 미리보기 생성 중..." size="sm" />}
              </div>
            </section>
          </div>
        )}
        </div>
        {msg && <div style={{ marginTop: 12, padding: "8px 10px", borderRadius: 8, border: `1px solid ${submitting ? INFO.fg : BAD.fg}`, color: submitting ? INFO.fg : BAD.fg, background: submitting ? INFO.bg : BAD.bg }}>{submitting ? <Loading text={msg} size="sm" /> : msg}</div>}
        <div className="inform-wizard-actions" style={{ display: "flex", gap: 8, justifyContent: "flex-end", flex: "0 0 auto", marginTop: 10, paddingTop: 10, borderTop: "1px solid var(--border)", background: "var(--bg-primary)" }}>
          <button type="button" onClick={prev} disabled={step === 0 || submitting}
            style={{ padding: "8px 14px", borderRadius: 8, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", cursor: step === 0 || submitting ? "not-allowed" : "pointer", opacity: step === 0 || submitting ? 0.5 : 1, fontSize: 14 }}>이전</button>
          {step < lastStep ? (
            <button type="button" onClick={next} disabled={submitting}
              style={{ padding: "8px 16px", borderRadius: 8, border: "none", background: submitting ? "var(--text-secondary)" : "var(--accent)", color: "#fff", fontWeight: 900, cursor: submitting ? "wait" : "pointer", fontSize: 14, opacity: submitting ? 0.75 : 1 }}>다음</button>
          ) : (
            <button type="button" disabled={submitting || mailOverLimit} onClick={() => {
              if (submitting) return;
              if (mailOverLimit) {
                setMsg(`메일 용량이 한도(${draftPreview?.mail_limit_mb} MB)를 초과했습니다 (${draftPreview?.mail_total_kb} kB). 사진이나 첨부를 줄인 뒤 등록해 주세요.`);
                return;
              }
              if (validate()) onSubmit();
            }}
              title={mailOverLimit ? "메일 용량 한도를 초과해 등록할 수 없습니다" : ""}
              style={{ padding: "8px 16px", borderRadius: 8, border: "none", background: submitting ? "var(--text-secondary)" : "var(--accent)", color: "#fff", fontWeight: 900, cursor: submitting ? "wait" : (mailOverLimit ? "not-allowed" : "pointer"), fontSize: 14, opacity: submitting || mailOverLimit ? 0.6 : 1 }}>{submitting ? "등록 저장 중..." : (isReInform ? "재인폼 등록" : "등록")}</button>
          )}
        </div>
      </div>
    </section>
  );
}
