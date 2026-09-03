import { useMemo, useRef } from "react";

export const SPLIT_CHECK_PREFIX_COLUMNS = ["항목", "값", "Split"];

// 표시용 항목명. 실제 데이터 키(_param)는 그대로 두고 화면 라벨만 다듬는다.
//  - 선행 prefix(KNOB_/MASK_/INLINE_/VM_/ET_ ...) 제거
//  - KNOB 항목은 꼬리에 붙은 `_Split` 도 제거 (KNOB_1.5_Vt_Split → 1.5_Vt)
// plan/tag/노트/편집은 전부 raw _param 을 쓰므로 여기 결과를 키로 쓰지 말 것.
export function splitParamDisplayName(name, rawParam) {
  const raw = String(name ?? "").trim();
  if (!raw) return "";
  const source = String(rawParam ?? "").trim() || raw;
  const isKnob = /^KNOB_/i.test(source) || /^KNOB_/i.test(raw);
  let out = raw.replace(/^[A-Za-z]+_/, "");
  if (isKnob) out = out.replace(/_Split$/i, "");
  return out.trim() || raw;
}

export function normalizeKnobParamKey(str) {
  return String(str || "")
    .trim()
    .toUpperCase()
    .replace(/^KNOB[_\s]+/i, "")
    .replace(/[_\s]*SPLIT$/i, "")
    .replace(/[\s_]+/g, " ");
}

export function s0ValueForParam(source, param) {
  const mapping = source?.s0_by_knob || {};
  if (!param) return "";
  const exact = mapping?.[param];
  if (exact?.ppid != null && String(exact.ppid).trim()) return String(exact.ppid).trim();
  const wantedNorm = normalizeKnobParamKey(param);
  const key = Object.keys(mapping).find(item => {
    if (String(item || "").trim().toUpperCase() === String(param).trim().toUpperCase()) return true;
    return normalizeKnobParamKey(item) === wantedNorm;
  });
  return key && mapping[key]?.ppid != null ? String(mapping[key].ppid).trim() : "";
}

function orderedSplitValues(perCells, preferredValue, draftValues, ensureRow = false, resolveDisplay = null) {
  const order = [];
  const seen = new Set();
  const getDisplay = (raw, fallbackDisplay) => {
    if (typeof resolveDisplay === "function") {
      const resolved = resolveDisplay(raw);
      if (resolved) return resolved;
    }
    return String(fallbackDisplay ?? raw ?? "");
  };
  const add = (raw, display, extra = {}) => {
    const value = String(raw ?? "").trim();
    if (!value || seen.has(value)) return;
    seen.add(value);
    order.push({ raw: value, display: getDisplay(value, display), ...extra });
  };
  if (preferredValue && String(preferredValue).trim()) {
    add(preferredValue, preferredValue, { is_s0: true });
  }
  (perCells || []).forEach(item => add(item.raw, item.display));
  (draftValues || []).forEach((value, draftIndex) => {
    const clean = String(value ?? "").trim();
    if (clean) add(clean, clean, { is_draft: true, draft_index: draftIndex });
    else order.push({ raw: "", display: "", is_draft: true, draft_index: draftIndex });
  });
  if (!order.length && ensureRow) order.push({ raw: "", display: "", is_s0: true });
  return order;
}

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
const ST_GRID_TEXT = "#000000";

function hasStValue(v) {
  return v != null && v !== "" && v !== "None" && v !== "null";
}

function splitTableCellBg(val, uniq, pname) {
  if (!hasStValue(val)) return {};
  const pn = String(pname || "").toUpperCase();
  if (!ST_COLOR_PREFIXES.some(p => pn.startsWith(p + "_"))) return {};
  const s = String(val);
  const idx = uniq[pn]?.[s];
  if (idx == null) return {};
  const c = ST_CELL_COLORS[idx % ST_CELL_COLORS.length];
  return { background: c.bg, color: ST_GRID_TEXT };
}

function splitCheckColorStyle(label) {
  const m = String(label || "").trim().match(/^S(\d+)$/i);
  if (!m) return {};
  const c = ST_CELL_COLORS[Number(m[1]) % ST_CELL_COLORS.length];
  return { background: c.bg, color: ST_GRID_TEXT, fontWeight: 900 };
}

// SplitTable 그리드(My_SplitTable getCellPlanStyle)와 같은 규약이어야 한다.
//   왼쪽 파란선 = 이 셀에 plan 이 있다 · 칸 빨강(흰 글씨) = plan 과 다르게 진행됐다.
const PLAN_LINE = "3px solid #3b82f6";
function stPlanStyle(cell) {
  if (!cell) return {};
  const hasPlan = hasStValue(cell.plan);
  const hasActual = hasStValue(cell.actual);
  if (hasPlan && hasActual) {
    if (String(cell.plan) === String(cell.actual)) return { borderLeft: PLAN_LINE };
    return { borderLeft: PLAN_LINE, background: "#ef4444", color: "#fff" };
  }
  if (hasPlan) return { borderLeft: PLAN_LINE, fontStyle: "italic", fontWeight: 700 };
  return {};
}

// SplitTable 그리드(My_SplitTable formatCell)와 같은 규약 — prefix 별 소수 자리수.
export function formatSplitCellValue(val, paramName, precision) {
  if (val === null || val === undefined || val === "") return val;
  const s = String(val);
  if (s === "None" || s === "null" || s === "NaN") return val;
  const num = Number(s);
  if (!isFinite(num) || isNaN(num)) return val;
  const pn = String(paramName || "").toUpperCase();
  for (const pfx of Object.keys(precision || {})) {
    if (pn.startsWith(pfx.toUpperCase() + "_")) {
      const n = precision[pfx];
      if (typeof n === "number" && n >= 0 && n <= 10) return num.toFixed(n);
    }
  }
  return val;
}

// step_progress(랏/wafer 별 최신 step) 기준 미진행 판정 — 그리드와 같은 우선순위.
function buildNotReachedLookup(st) {
  const normalize = value => String(value ?? "").replace(/^(?:#|WAFER|WF|W)\s*/i, "").replace(/^0+(?=\d)/, "");
  const progress = st?.step_progress || {};
  const byWafer = Object.fromEntries(Object.entries(progress.by_wafer || {}).map(([wafer, meta]) => [
    normalize(wafer),
    new Set(Array.isArray(meta?.not_reached) ? meta.not_reached.map(String) : []),
  ]));
  const root = new Set(Array.isArray(progress.not_reached) ? progress.not_reached.map(String) : []);
  const hasWafer = Object.keys(byWafer).length > 0;
  const headers = st?.headers || [];
  const keyAt = ci => normalize(st?.wafer_keys?.[ci] ?? headers[ci] ?? "");
  const isKnobProgressParam = param => {
    const value = String(param || "").trim().toUpperCase();
    return value === "KNOB" || value.startsWith("KNOB_");
  };
  const cell = (param, ci) => {
    const name = String(param || "");
    if (!name || !isKnobProgressParam(name)) return false;
    if (!hasWafer) return root.has(name);
    return byWafer[keyAt(ci)]?.has(name) === true;
  };
  return {
    cell,
    row: (param) => {
      const name = String(param || "");
      if (!name || !isKnobProgressParam(name)) return false;
      if (!hasWafer) return root.has(name);
      return headers.length > 0 && headers.every((_, ci) => cell(name, ci));
    },
  };
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
    const text = label || "-";
    const last = groups[groups.length - 1];
    if (last && last.label === text) last.span += 1;
    else groups.push({ label: text, span: 1 });
  });
  return groups;
}

// processInfoForParam: 적용 공정 정보를 항목과 분리된 step_id / step_desc 열로
// 넣기 위한 훅. labelForParam은 이전 저장 스냅샷 호환용으로만 유지한다.
export function buildSplitCheckStView(matrix, { valueForCell, displayForValue, labelForParam, processInfoForParam, preferredValueForParam, extraValuesForParam, ensureEmptyRows = false } = {}) {
  const source = matrix || {};
  const headers = Array.isArray(source.headers) ? source.headers : [];
  const rows = Array.isArray(source.rows) ? source.rows : [];
  const normalizeWafer = value => String(value ?? "").replace(/^(?:#|WAFER|WF|W)\s*/i, "").replace(/^0+(?=\d)/, "");
  const rootNotReached = new Set(Array.isArray(source?.step_progress?.not_reached) ? source.step_progress.not_reached.map(String) : []);
  const byWafer = Object.fromEntries(Object.entries(source?.step_progress?.by_wafer || {}).map(([wafer, meta]) => [
    normalizeWafer(wafer),
    new Set(Array.isArray(meta?.not_reached) ? meta.not_reached.map(String) : []),
  ]));
  const hasWaferProgress = Object.keys(byWafer).length > 0;
  const splitRows = rows.flatMap(row => {
    const cells = row?._cells || {};
    const param = String(row?._param || "");
    const perHeader = headers.map((header, ci) => {
      const cell = cells[String(ci)] || cells[ci] || {};
      const waferNotReached = byWafer[normalizeWafer(source?.wafer_keys?.[ci] ?? header)]?.has(param) === true;
      const notReached = hasWaferProgress ? waferNotReached : rootNotReached.has(param);
      const rawValue = valueForCell
        ? valueForCell(cell, row, ci)
        : (hasStValue(cell?.plan) ? cell.plan : cell?.actual);
      if (!hasStValue(rawValue)) return { raw: "", display: "", not_reached: notReached };
      const raw = String(rawValue);
      const displayRaw = displayForValue ? displayForValue(rawValue, row, cell, ci) : raw;
      return { raw, display: String(displayRaw ?? raw), not_reached: notReached };
    });
    const preferred = preferredValueForParam
      ? preferredValueForParam(row?._param, row)
      : s0ValueForParam(source, row?._param);
    const extras = extraValuesForParam ? extraValuesForParam(row?._param, row) : [];
    const resolveDisplay = (val) => (displayForValue ? displayForValue(val, row) : val);
    const order = orderedSplitValues(perHeader, preferred, extras, ensureEmptyRows, resolveDisplay);
    return order.map((item, idx) => {
      const label = `S${idx}`;
      const checkCells = {};
      perHeader.forEach((value, ci) => {
        const isMatch = value.raw && (
          value.raw === item.raw ||
          (item.display && value.display === item.display) ||
          (item.raw && value.display === item.raw) ||
          (item.display && value.raw === item.display)
        );
        checkCells[String(ci)] = { actual: isMatch ? "✓" : "", plan: "", split_check: true, not_reached: !!value.not_reached };
      });
      const process = processInfoForParam ? (processInfoForParam(row?._param, row?._display) || {}) : null;
      const basePrefix = [
        (labelForParam && labelForParam(row?._param, row?._display))
          || splitParamDisplayName(row?._display || row?._param || "", row?._param),
        item.display, label,
      ];
      return {
        _param: String(row?._param || row?._display || ""),
        _display: String(row?._display || row?._param || ""),
        _split_value: item.display,
        _split_value_raw: item.raw,
        _split_label: label,
        _is_s0: idx === 0,
        _is_split_draft: !!item.is_draft,
        _split_draft_index: item.draft_index,
        _process_columns: process || undefined,
        _prefix_cells: process
          ? [String(process.step_id||""),String(process.step_desc||""),...basePrefix]
          : basePrefix,
        _cells: checkCells,
        _not_reached_all: perHeader.length > 0 && perHeader.every(value => value.not_reached),
      };
    });
  });
  return {
    ...source,
    headers,
    rows: splitRows,
    prefix_columns: Array.isArray(source.prefix_columns)&&source.prefix_columns.length?source.prefix_columns:SPLIT_CHECK_PREFIX_COLUMNS,
    parameter_prefix_index: (Array.isArray(source.prefix_columns) && (source.prefix_columns.includes("step_id") || source.prefix_columns.includes("step_desc"))) ? 2 : 0,
    display_mode: "split_check",
    row_labels: { ...(source.row_labels || {}), parameter: SPLIT_CHECK_PREFIX_COLUMNS[0] },
  };
}

// PEMS 표시: root lot 의 wafer 축을 물리 wafer 번호 1..25로 고정하고,
// Split 체크와 같은 S0/S1... 그룹에 체크 대신 그룹명을 직접 표시한다.
// 조회 결과에 없는 wafer(또는 값이 비어 있는 wafer)는 S0에 포함하되 회색으로 남긴다.
export function buildPemsStView(matrix, { valueForCell, displayForValue, labelForParam, processInfoForParam, preferredValueForParam, extraValuesForParam } = {}) {
  const source = matrix || {};
  const sourceHeaders = Array.isArray(source.headers) ? source.headers : [];
  const sourceRows = Array.isArray(source.rows) ? source.rows : [];
  const normalizeWafer = value => String(value ?? "").replace(/^(?:#|WAFER|WF|W)\s*/i, "").replace(/^0+(?=\d)/, "");
  const sourceIndexByWafer = new Map();
  sourceHeaders.forEach((header, ci) => {
    const wafer = normalizeWafer(source?.wafer_keys?.[ci] ?? header);
    if (wafer && !sourceIndexByWafer.has(wafer)) sourceIndexByWafer.set(wafer, ci);
  });

  const headers = Array.from({ length: 25 }, (_, idx) => String(idx + 1));
  const waferKeys = Array.from({ length: 25 }, (_, idx) => String(idx + 1));
  const missingWaferIndices = waferKeys
    .map((wafer, ci) => sourceIndexByWafer.has(wafer) ? null : ci)
    .filter(ci => ci != null);
  const rootNotReached = new Set(Array.isArray(source?.step_progress?.not_reached) ? source.step_progress.not_reached.map(String) : []);
  const byWafer = Object.fromEntries(Object.entries(source?.step_progress?.by_wafer || {}).map(([wafer, meta]) => [
    normalizeWafer(wafer),
    new Set(Array.isArray(meta?.not_reached) ? meta.not_reached.map(String) : []),
  ]));
  const hasWaferProgress = Object.keys(byWafer).length > 0;

  const pemsRows = sourceRows.flatMap(row => {
    const cells = row?._cells || {};
    const param = String(row?._param || "");
    const perWafer = waferKeys.map(wafer => {
      const sourceIndex = sourceIndexByWafer.get(wafer);
      const missingWafer = sourceIndex == null;
      const cell = missingWafer ? {} : (cells[String(sourceIndex)] || cells[sourceIndex] || {});
      const progressNotReached = missingWafer
        ? true
        : (hasWaferProgress ? byWafer[wafer]?.has(param) === true : rootNotReached.has(param));
      const rawValue = missingWafer
        ? ""
        : (valueForCell ? valueForCell(cell, row, sourceIndex) : (hasStValue(cell?.plan) ? cell.plan : cell?.actual));
      if (!hasStValue(rawValue)) {
        return { raw: "", display: "", missing_wafer: missingWafer, not_reached: true };
      }
      const raw = String(rawValue);
      const displayRaw = displayForValue ? displayForValue(rawValue, row, cell, sourceIndex) : raw;
      return { raw, display: String(displayRaw ?? raw), missing_wafer: missingWafer, not_reached: progressNotReached };
    });
    const preferred = preferredValueForParam
      ? preferredValueForParam(row?._param, row)
      : s0ValueForParam(source, row?._param);
    const extras = extraValuesForParam ? extraValuesForParam(row?._param, row) : [];
    const resolveDisplay = (val) => (displayForValue ? displayForValue(val, row) : val);
    const order = orderedSplitValues(perWafer, preferred, extras, true, resolveDisplay);
    // 실제 값이 하나도 없어도 PEMS 에서는 S0 행이 있어야 25개 wafer가 모두 보인다.
    if (!order.length) order.push({ raw: "", display: "" });

    return order.map((item, idx) => {
      const label = `S${idx}`;
      const splitCells = {};
      perWafer.forEach((value, ci) => {
        // 빈 값과 조회 결과에 없는 wafer는 항상 S0 소속이다.
        const belongs = idx === 0 ? (!value.raw || value.raw === item.raw) : value.raw === item.raw;
        splitCells[String(ci)] = {
          actual: belongs ? label : "",
          plan: "",
          split_check: true,
          pems: true,
          missing_wafer: !!value.missing_wafer,
          not_reached: !!(value.missing_wafer || value.not_reached),
        };
      });
      const process = processInfoForParam ? (processInfoForParam(row?._param, row?._display) || {}) : null;
      const basePrefix = [
        (labelForParam && labelForParam(row?._param, row?._display))
          || splitParamDisplayName(row?._display || row?._param || "", row?._param),
        item.display, label,
      ];
      return {
        _param: String(row?._param || row?._display || ""),
        _display: String(row?._display || row?._param || ""),
        _split_value: item.display,
        _split_value_raw: item.raw,
        _split_label: label,
        _is_s0: idx === 0 && !!preferred,
        _is_split_draft: !!item.is_draft,
        _split_draft_index: item.draft_index,
        _process_columns: process || undefined,
        _prefix_cells: process
          ? [String(process.step_id||""),String(process.step_desc||""),...basePrefix]
          : basePrefix,
        _cells: splitCells,
        _not_reached_all: perWafer.every(value => value.not_reached),
      };
    });
  });

  return {
    ...source,
    headers,
    wafer_keys: waferKeys,
    rows: pemsRows,
    header_groups: [],
    wafer_fab_list: [],
    lot_id_label: "",
    hide_lot_id_row: true,
    pems_missing_wafer_indices: missingWaferIndices,
    prefix_columns: Array.isArray(source.prefix_columns)&&source.prefix_columns.length?source.prefix_columns:SPLIT_CHECK_PREFIX_COLUMNS,
    parameter_prefix_index: (Array.isArray(source.prefix_columns) && (source.prefix_columns.includes("step_id") || source.prefix_columns.includes("step_desc"))) ? 2 : 0,
    display_mode: "pems",
    row_labels: { ...(source.row_labels || {}), parameter: SPLIT_CHECK_PREFIX_COLUMNS[0] },
  };
}

function inferProductFromEmbed(embed, product, source) {
  const p = String(product || "").trim();
  if (p) return p;
  const src = String(source || embed?.source || "");
  const m = src.match(/SplitTable\/([^ @·]+)/);
  return m ? String(m[1] || "").trim() : "";
}

export default function SplitTableSnapshotView({
  embed,
  stView,
  product,
  source,
  note,
  footer = null,
  showTitle = true,
  showMeta = true, // v9.5.x: 메일 본문/미리보기에서 note 자동 생성 줄 + wafer 배지 같은 기술 메타를 숨기기 위한 스위치.
  emptyMessage = "Split 체크로 표시할 값이 없습니다",
  maxHeight = 620,
  editable = false,
  onAssignSplit = null,
  onEditSplitValue = null,
  onAddSplitRequest = null,
}) {
  const st = stView || embed?.st_view;
  const splitPaintRef = useRef(null);
  const stValid = st && Array.isArray(st?.headers) && Array.isArray(st?.rows);

  const effectiveSource = source ?? embed?.source ?? "";
  const effectiveNote = note ?? embed?.note ?? "";
  const effectiveProduct = inferProductFromEmbed(embed, product, effectiveSource);
  const headers = st?.headers || [];
  const rawPrefixColumns = Array.isArray(st?.prefix_columns) ? st.prefix_columns.map(v => String(v || "").trim()).filter(Boolean) : [];
  const snapshotMode = String(st?.display_mode || embed?.display_mode || embed?.st_scope?.display_mode || "");
  const pemsMode = snapshotMode === "pems" && rawPrefixColumns.length >= 3;
  const splitCheckMode = (snapshotMode === "split_check" || pemsMode) && rawPrefixColumns.length >= 3;
  // 병합 표시 — 서버가 행마다 _merged_runs(연속 동일값 구간)를 실어 준다.
  const mergedMode = snapshotMode === "merged";
  const stepLabels = !!(st?.step_labels || embed?.step_labels);
  // 화면과 같은 표시 규약 — 소수 자리수와 미진행 회색은 스냅샷에 실려 온다.
  const precision = st?.precision || embed?.precision || {};
  const notReached = useMemo(() => buildNotReachedLookup(st || {}), [st]);
  const rowLabels = st?.row_labels || {};
  const rootRowLabel = rowLabels.root_lot_id || "root_lot_id";
  const lotRowLabel = rowLabels.lot_id || "lot_id";
  const paramRowLabel = rowLabels.parameter || "항목";
  const firstColWidth = 288;
  const dataColWidth = 115;
  const prefixColumns = splitCheckMode ? rawPrefixColumns : [];
  const matrixPrefixColumns = stepLabels ? ["step_id", "step_desc", paramRowLabel] : [paramRowLabel];
  const widthForPrefix = label => String(label||"")===paramRowLabel?240:({step_id:168,step_desc:180,"항목":240,"값":140,"Split":80}[String(label||"")]||100);
  const prefixColWidths = (splitCheckMode ? prefixColumns : matrixPrefixColumns).map(widthForPrefix);
  while (prefixColWidths.length < (splitCheckMode ? prefixColumns.length : 1)) prefixColWidths.push(100);
  const prefixTotalWidth = prefixColWidths.reduce((sum, value) => sum + value, 0);
  const stickyLeft = (idx) => prefixColWidths.slice(0, idx).reduce((sum, value) => sum + value, 0);
  const headerGroups = splitTableHeaderGroups(st || {});
  const rootLotId = String(st?.root_lot_id || "").trim();
  const groupLotValues = [...new Set(headerGroups.map(g => String(g?.label || "").trim()).filter(Boolean))];
  const lotIdLabel = groupLotValues.join(", ") || String(st?.lot_id_label || "").trim();
  const hasLotContext = !!(rootLotId || lotIdLabel);
  const visiblePrefixColumns = splitCheckMode ? prefixColumns : matrixPrefixColumns;
  const parameterPrefixIndex = splitCheckMode
    ? Math.max(0,Math.min(visiblePrefixColumns.length-1,visiblePrefixColumns.includes("step_id")?2:(Number.isInteger(st?.parameter_prefix_index)&&st.parameter_prefix_index<visiblePrefixColumns.length-2?st.parameter_prefix_index:0)))
    : (stepLabels?2:0);
  const splitPrefixIndex = splitCheckMode ? parameterPrefixIndex+2 : -1;
  const hasRootRow = hasLotContext;
  const hasLotRow = !pemsMode && (hasLotContext || headerGroups.length > 0);
  const rootHeaderHeight = hasRootRow ? 32 : 0;
  const lotHeaderHeight = hasLotRow ? 24 : 0;
  const waferTop = rootHeaderHeight + lotHeaderHeight;
  const lotContextTitle = `root_lot_id: ${rootLotId || "-"}\nlot_id: ${lotIdLabel || "-"}`;
  const rowSpans = useMemo(() => {
    if (!splitCheckMode) return (st?.rows || []).map(() => 1);
    return (st?.rows || []).map((row, idx) => {
      const param = String(row?._param || "").trim();
      if (!param) return 1;
      const prev = idx > 0 ? String((st?.rows || [])[idx - 1]?._param || "").trim() : "";
      if (prev === param) return 0;
      let span = 1;
      for (let i = idx + 1; i < (st?.rows || []).length; i += 1) {
        if (String((st?.rows || [])[i]?._param || "").trim() !== param) break;
        span += 1;
      }
      return span;
    });
  }, [splitCheckMode, st?.rows]);
  const uniq = useMemo(() => {
    const out = {};
    for (const r of (st?.rows || [])) {
      const pn = String(r._param || "").toUpperCase();
      if (!ST_COLOR_PREFIXES.some(p => pn.startsWith(p + "_"))) continue;
      const seen = {};
      Object.values(r._cells || {}).forEach(c => {
        [c?.actual, c?.plan].forEach(v => {
          if (!hasStValue(v)) return;
          const s = String(v);
          if (!(s in seen)) seen[s] = Object.keys(seen).length;
        });
      });
      out[pn] = seen;
    }
    return out;
  }, [st?.rows]);

  // v9.3.x: early return을 훅 아래로 이동 (Rules-of-Hooks 준수)
  if (!stValid) return footer || null;

  const shellStyle = { marginTop: 8, padding: 10, border: "1px solid var(--border)", borderRadius: 6, background: "var(--bg-primary)", maxWidth: "100%" };
  const scrollerStyle = { maxHeight, overflow: "auto", border: "1px solid #555", borderRadius: 0, background: "var(--bg-card)" };
  const stTableWidth = prefixTotalWidth + Math.max(headers.length, 1) * dataColWidth;
  const stTableStyle = {
    borderCollapse: "collapse",
    fontSize: 14,
    background: "var(--bg-card)",
    tableLayout: "fixed",
    width: stTableWidth,
    minWidth: stTableWidth,
    fontFamily: "inherit",
  };
  const rootLeftStyle = { boxSizing: "border-box", height: rootHeaderHeight, padding: "4px 8px", background: "var(--bg-tertiary)", border: "1px solid #555", position: "sticky", top: 0, left: 0, zIndex: 5, textAlign: "left", fontSize: 14, lineHeight: 1.25, color: ST_GRID_TEXT, fontWeight: 800, whiteSpace: "normal", wordBreak: "break-word", width: prefixTotalWidth, minWidth: prefixTotalWidth };
  const rootHeadStyle = { boxSizing: "border-box", height: rootHeaderHeight, textAlign: "center", padding: "0 8px", lineHeight: `${rootHeaderHeight - 1}px`, fontWeight: 700, fontSize: 14, color: ST_GRID_TEXT, background: "var(--bg-tertiary)", border: "1px solid #555", position: "sticky", top: 0, zIndex: 4, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" };
  const lotLeftStyle = { boxSizing: "border-box", height: lotHeaderHeight, padding: "0 8px", background: "var(--bg-tertiary)", border: "1px solid #555", position: "sticky", top: rootHeaderHeight, left: 0, zIndex: 5, textAlign: "left", fontSize: 14, color: ST_GRID_TEXT, fontWeight: 800, width: prefixTotalWidth, minWidth: prefixTotalWidth };
  const lotHeadStyle = { boxSizing: "border-box", height: lotHeaderHeight, textAlign: "center", padding: "0 6px", fontWeight: 800, fontSize: 14, color: ST_GRID_TEXT, background: "var(--bg-tertiary)", border: "1px solid #555", position: "sticky", top: rootHeaderHeight, zIndex: 4, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" };
  const waferLeftStyle = { textAlign: "left", padding: "8px 10px", fontWeight: 700, fontSize: 14, color: ST_GRID_TEXT, border: "1px solid #555", background: "var(--bg-tertiary)", position: "sticky", top: waferTop, left: 0, zIndex: 5, width: firstColWidth, minWidth: firstColWidth };
  const waferHeadStyle = { textAlign: "center", padding: "6px 8px", fontWeight: 600, fontSize: 14, color: ST_GRID_TEXT, border: "1px solid #555", borderBottom: "2px solid #555", background: "var(--bg-tertiary)", position: "sticky", top: waferTop, zIndex: 3, whiteSpace: "normal", wordBreak: "break-word", minWidth: 100 };
  const paramCellStyle = { padding: "6px 10px", fontWeight: 600, fontSize: 14, color: ST_GRID_TEXT, border: "1px solid #555", background: "var(--bg-secondary)", position: "sticky", left: 0, zIndex: 2, whiteSpace: "normal", wordBreak: "break-word", lineHeight: 1.35 };
  const stCellStyle = { background: "var(--bg-card)", color: ST_GRID_TEXT, padding: "4px 8px", border: "1px solid #555", textAlign: "center", fontSize: 14, whiteSpace: "normal", wordBreak: "break-word", lineHeight: 1.35, position: "relative" };
  const prefixHeadStyle = (idx) => ({ ...waferLeftStyle, left: stickyLeft(idx), width: prefixColWidths[idx], minWidth: prefixColWidths[idx], zIndex: 6 - Math.min(idx, 3), color: ST_GRID_TEXT });
  const prefixCellStyle = (idx) => ({ ...paramCellStyle, left: stickyLeft(idx), width: prefixColWidths[idx], minWidth: prefixColWidths[idx], zIndex: 4 - Math.min(idx, 2), fontWeight: idx === parameterPrefixIndex ? 700 : 600, whiteSpace: "pre-line" });
  const notReachedStyle = { background: "rgba(107,114,128,0.45)" };
  const pemsMissingWaferIndices = new Set(Array.isArray(st?.pems_missing_wafer_indices) ? st.pems_missing_wafer_indices.map(Number) : []);

  return (
    <div style={shellStyle}>
      {showTitle && (
        <div style={{ fontSize: 14, fontWeight: 700, color: "var(--accent)", marginBottom: 4 }}>
          SplitTable {effectiveSource && <span style={{ color: "var(--text-secondary)", fontWeight: 500 }}>· {effectiveSource}</span>}
        </div>
      )}
      {showMeta && effectiveNote && <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 4 }}>{effectiveNote}</div>}
      {showMeta && (
        <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 6 }}>
          <span style={{ padding: "2px 7px", borderRadius: 999, border: "1px solid var(--border)", background: "var(--bg-tertiary)", fontFamily: "monospace" }}>
            {headers.length} wafer 표시{headers.length ? " · 가로 스크롤" : ""}
          </span>
        </div>
      )}
      <div style={scrollerStyle} onMouseLeave={() => { splitPaintRef.current = null; }}>
        <table style={stTableStyle}>
          <colgroup>
            {prefixColWidths.map((width, i) => <col key={`prefix-${i}`} style={{ width }} />)}
            {headers.map((_, i) => <col key={i} style={{ width: dataColWidth }} />)}
          </colgroup>
          <thead>
            {hasRootRow && (
              <tr>
                <th colSpan={visiblePrefixColumns.length} style={rootLeftStyle} title={lotContextTitle}>{rootRowLabel}</th>
                <th colSpan={headers.length || 1} style={rootHeadStyle}>{rootLotId || lotIdLabel}</th>
              </tr>
            )}
            {hasLotRow && (
              <tr>
                <th colSpan={visiblePrefixColumns.length} style={lotLeftStyle} title={lotContextTitle}>{lotRowLabel}</th>
                {headerGroups.length > 0
                  ? headerGroups.map((g, i) => (
                    <th key={i} colSpan={g.span} style={lotHeadStyle} title={g.label}>{g.label}</th>
                  ))
                  : <th colSpan={headers.length || 1} style={lotHeadStyle} title={lotIdLabel}>{lotIdLabel || "-"}</th>}
              </tr>
            )}
            <tr>
              {visiblePrefixColumns.map((label, i) => (
                <th key={`${label}-${i}`} style={prefixHeadStyle(i)}>{label}</th>
              ))}
              {headers.map((h, i) => (
                <th key={i} title={pemsMissingWaferIndices.has(i) ? "데이터가 없는 wafer · S0로 표시" : undefined}
                  style={{ ...waferHeadStyle, ...(pemsMissingWaferIndices.has(i) ? notReachedStyle : {}) }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {st.rows.map((r, ri) => {
              const rawPrefixCells = Array.isArray(r._prefix_cells) ? r._prefix_cells : [];
              const prefixValues = visiblePrefixColumns.map((_, idx) => {
                if (splitCheckMode) {
                  if (idx === parameterPrefixIndex) return splitParamDisplayName(r._display || r._param || "", r._param);
                  if (idx === parameterPrefixIndex+1) return String(r._split_value || "");
                  if (idx === parameterPrefixIndex+2) return String(r._split_label || "");
                  if (rawPrefixCells[idx] != null) return String(rawPrefixCells[idx]);
                  return "";
                }
                const process = r?._process_columns || r?._applied_process || {};
                if (stepLabels && idx === 0) return String(process.step_id || "");
                if (stepLabels && idx === 1) return String(process.step_desc || "");
                return splitParamDisplayName(r._display || r._param || "", r._param);
              });
              const span = rowSpans[ri] || 0;
              const rowHasValue = (mergedMode && Array.isArray(r._merged_runs))
                ? r._merged_runs.some(run => hasStValue(run?.value))
                : headers.some((_, ci) => {
                    const cell = (r._cells && (r._cells[ci] || r._cells[String(ci)])) || {};
                    return hasStValue(cell.actual) || hasStValue(cell.plan);
                  });
              const rowNotReached = (r._not_reached_all === true || notReached.row(r._param)) && !rowHasValue;
              return (
                <tr key={r.key || `${r._param || "row"}-${ri}`}>
                  {prefixValues.map((value, pi) => {
                    if (splitCheckMode && pi <= parameterPrefixIndex && span === 0) return null;
                    const splitStyle = splitCheckMode && pi === splitPrefixIndex ? splitCheckColorStyle(value) : {};
                    const rowSpanProps = splitCheckMode && pi <= parameterPrefixIndex && span > 1 ? { rowSpan: span } : {};
                    return (
                      <td key={`prefix-${pi}`} {...rowSpanProps}
                        onContextMenu={editable && splitCheckMode && pi === parameterPrefixIndex ? (event) => {
                          event.preventDefault();
                          onAddSplitRequest?.(event, r._param, r);
                        } : undefined}
                        onClick={editable && splitCheckMode && pi === parameterPrefixIndex + 1 && r._is_split_draft ? () => onEditSplitValue?.(r) : undefined}
                        title={editable && splitCheckMode && pi === parameterPrefixIndex ? "우클릭: 스플릿 추가" : (editable && r._is_split_draft && pi === parameterPrefixIndex + 1 ? "클릭: KNOB 값 선택 또는 새 값 입력" : undefined)}
                        style={{ ...prefixCellStyle(pi), ...splitStyle, ...(splitCheckMode && pi <= parameterPrefixIndex ? { verticalAlign: "top" } : {}), ...(rowNotReached ? notReachedStyle : {}), ...(editable && splitCheckMode && ((pi === parameterPrefixIndex) || (r._is_split_draft && pi === parameterPrefixIndex + 1)) ? { cursor: "pointer" } : {}) }}>
                        {value}
                      </td>
                    );
                  })}
                  {mergedMode && Array.isArray(r._merged_runs) && r._merged_runs.map((run, ri2) => {
                    const value = String(run?.value ?? "");
                    const runSpan = Math.max(1, Number(run?.span || 1));
                    const runNotReached = (run?.not_reached === true || notReached.cell(r._param, Number(run?.start ?? 0))) && !hasStValue(value);
                    return (
                      <td key={`run-${ri2}`} colSpan={runSpan}
                        style={{ ...stCellStyle, ...splitTableCellBg(value, uniq, r._param), ...(runSpan > 1 ? { fontWeight: 700 } : {}), ...(runNotReached ? notReachedStyle : {}) }}>
                        {String(formatSplitCellValue(value, r._param, precision) ?? value)}
                      </td>
                    );
                  })}
                  {!(mergedMode && Array.isArray(r._merged_runs)) && headers.map((_, ci) => {
                    const cell = (r._cells && (r._cells[ci] || r._cells[String(ci)])) || {};
                    const display = hasStValue(cell.actual)
                      ? String(splitCheckMode ? cell.actual : (formatSplitCellValue(cell.actual, r._param, precision) ?? cell.actual))
                      : "";
                    const bg = splitCheckMode ? (display ? splitCheckColorStyle(prefixValues[splitPrefixIndex] || r._split_label) : {}) : splitTableCellBg(hasStValue(cell.plan) ? cell.plan : cell.actual, uniq, r._param);
                    const plan = splitCheckMode ? {} : stPlanStyle(cell);
                    const hasPlan = hasStValue(cell.plan);
                    const hasActual = hasStValue(cell.actual);
                    const isPlanOnly = !splitCheckMode && hasPlan && !hasActual;
                    const isMismatch = !splitCheckMode && hasPlan && hasActual && String(cell.plan) !== String(cell.actual);
                    // PEMS 는 미진행/누락 wafer도 S0/S1 그룹 라벨을 유지해야 한다.
                    // 따라서 값(S0 등)이 들어 있어도 회색 배경을 덮어쓴다. 일반
                    // Split 체크/스냅샷은 실제 값이 있는 셀을 회색 처리하지 않는다.
                    const cellNotReached = pemsMode
                      ? (cell.not_reached === true || pemsMissingWaferIndices.has(ci))
                      : (cell.not_reached === true || notReached.cell(r._param, ci)) && !hasActual && !hasPlan;
                    return (
                      <td key={ci}
                        onMouseDown={editable && splitCheckMode ? (event) => {
                          if (event.button !== 0 || !r._split_value_raw) return;
                          event.preventDefault();
                          splitPaintRef.current = { param: r._param, value: r._split_value_raw };
                          onAssignSplit?.(r._param, r._split_value_raw, ci);
                        } : undefined}
                        onMouseEnter={editable && splitCheckMode ? () => {
                          const paint = splitPaintRef.current;
                          if (paint && paint.param === r._param) onAssignSplit?.(paint.param, paint.value, ci);
                        } : undefined}
                        onMouseUp={editable && splitCheckMode ? () => { splitPaintRef.current = null; } : undefined}
                        title={editable && splitCheckMode ? (r._split_value_raw ? `${r._split_label} 배정 · 클릭하거나 드래그` : "먼저 값 칸에서 KNOB 값을 입력하세요") : undefined}
                        style={{ ...stCellStyle, ...bg, ...plan, ...(splitCheckMode && display ? { fontWeight: 900 } : {}), ...(cellNotReached ? notReachedStyle : {}), ...(editable && splitCheckMode ? { cursor: r._split_value_raw ? "cell" : "not-allowed" } : {}) }}>
                        {/* 진한 빨강 배경 위라 글자는 흰색이다 (stPlanStyle 과 한 쌍). */}
                        {splitCheckMode
                          ? display
                          : isMismatch
                            ? <span style={{ color: "#fff", fontWeight: 800 }}>{"✗ "}{display}<span style={{ fontSize: 14, color: "rgba(255,255,255,0.85)" }}>{" (≠" + cell.plan + ")"}</span></span>
                            : isPlanOnly
                              ? <span style={{ fontStyle: "italic", fontWeight: 700 }}>{"📌 "}{cell.plan}</span>
                              : display}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
            {st.rows.length === 0 && (
              <tr>
                <td colSpan={(headers.length || 0) + visiblePrefixColumns.length} style={{ padding: 24, textAlign: "center", color: "var(--text-secondary)", border: "1px solid #555" }}>
                  {emptyMessage}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {footer}
    </div>
  );
}
