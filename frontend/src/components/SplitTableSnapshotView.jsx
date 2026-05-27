import { useEffect, useMemo, useState } from "react";
import { sf, qs } from "../lib/api";
import { statusPalette } from "./UXKit";

const OK = statusPalette.ok;
const WARN = statusPalette.warn;
const BAD = statusPalette.bad;
const INFO = statusPalette.info;

export const SPLIT_CHECK_PREFIX_COLUMNS = ["항목", "값", "Split"];

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

function splitTableCellBg(val, uniq, pname) {
  if (!hasStValue(val)) return {};
  const pn = String(pname || "").toUpperCase();
  if (!ST_COLOR_PREFIXES.some(p => pn.startsWith(p + "_"))) return {};
  const s = String(val);
  const idx = uniq[pn]?.[s];
  if (idx == null) return {};
  const c = ST_CELL_COLORS[idx % ST_CELL_COLORS.length];
  return { background: c.bg, color: c.fg };
}

function splitCheckColorStyle(label) {
  const m = String(label || "").trim().match(/^S(\d+)$/i);
  if (!m) return {};
  const c = ST_CELL_COLORS[Number(m[1]) % ST_CELL_COLORS.length];
  return { background: c.bg, color: c.fg, fontWeight: 900 };
}

function stPlanStyle(cell) {
  if (!cell) return {};
  const hasPlan = hasStValue(cell.plan);
  const hasActual = hasStValue(cell.actual);
  if (hasPlan && hasActual) {
    if (String(cell.plan) === String(cell.actual)) return { borderLeft: `3px solid ${OK.fg}`, fontWeight: 700 };
    return { borderLeft: `3px solid ${BAD.fg}`, boxShadow: `inset 0 0 0 1px ${BAD.fg}66` };
  }
  if (hasPlan) return { borderLeft: `3px solid ${WARN.fg}`, fontStyle: "italic", fontWeight: 700 };
  return {};
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

function stepWarning(stepIds) {
  const ids = (Array.isArray(stepIds) ? stepIds : []).map(x => String(x || "").trim()).filter(Boolean);
  if (ids.length <= 1) return "";
  const hasManualLike = ids.some(sid => /[A-Z]{2}\d{6}[A-Z]{2}$/.test(sid));
  return hasManualLike
    ? "복수 step_id 및 manual/예외 step 후보가 있어 적용 엔지니어 확인이 필요합니다."
    : "복수 step_id 이므로 적용 전 담당 엔지니어가 실제 사용 step_id를 확인해 주세요.";
}

function stepIdsForGroup(group) {
  if (Array.isArray(group?.step_ids)) return group.step_ids.map(v => String(v || "").trim()).filter(Boolean);
  const sid = String(group?.step_id || "").trim();
  return sid ? [sid] : [];
}

function knobStepGroups(groups) {
  const byStep = new Map();
  (Array.isArray(groups) ? groups : []).forEach(g => {
    const desc = String(g?.step_desc || g?.func_step || "").trim();
    stepIdsForGroup(g).forEach(sid => {
      if (!byStep.has(sid)) byStep.set(sid, { step_id: sid, step_descs: [], seen: new Set() });
      const item = byStep.get(sid);
      const key = desc.toLowerCase();
      if (desc && !item.seen.has(key)) {
        item.seen.add(key);
        item.step_descs.push(desc);
      }
    });
  });
  return Array.from(byStep.values()).map(({ seen, ...item }) => item);
}

function metaLookup(metaMap, param, prefix) {
  if (!param || !metaMap) return null;
  const full = String(param || "").trim();
  const tail = full.replace(new RegExp(`^${prefix}_`, "i"), "").trim();
  if (metaMap[full]) return metaMap[full];
  if (metaMap[tail]) return metaMap[tail];
  const fullLower = full.toLowerCase();
  const tailLower = tail.toLowerCase();
  const hitKey = Object.keys(metaMap).find(k => {
    const key = String(k || "").trim().toLowerCase();
    return key === fullLower || key === tailLower;
  });
  return hitKey ? metaMap[hitKey] : null;
}

function buildLineageSummary(rows, knobMeta, vmMeta, inlineMeta) {
  const out = [];
  const knobLookup = (param) => metaLookup(knobMeta, param, "KNOB");
  const vmLookup = (param) => metaLookup(vmMeta, param, "VM");
  const inlineLookup = (param) => metaLookup(inlineMeta, param, "INLINE");
  (rows || []).forEach(row => {
    const param = String(row?._param || "");
    if (!param) return;
    const paramUpper = param.toUpperCase();
    const km = knobLookup(param);
    if (Array.isArray(km?.groups) && km.groups.length) {
      knobStepGroups(km.groups).forEach((item, gi) => out.push({
        key: `${param}-k-${gi}`,
        parameter: param,
        function_step: (item.step_descs || []).join(", "),
        step_desc: (item.step_descs || []).join(", "),
        step_ids: [item.step_id],
        module: "",
      }));
      return;
    }
    const vm = vmLookup(param) || {};
    if (paramUpper.startsWith("VM_") && (vm.step_id || vm.step_desc || vm.function_step || Array.isArray(vm.groups))) {
      if (Array.isArray(vm.groups) && vm.groups.length) {
        vm.groups.forEach((g, gi) => out.push({
          key: `${param}-v-${gi}`,
          parameter: param,
          function_step: g.function_step || vm.function_step || "",
          step_desc: g.step_desc || g.function_step || vm.step_desc || vm.function_step || "",
          step_ids: stepIdsForGroup(g).length ? stepIdsForGroup(g) : stepIdsForGroup(vm),
          module: "",
        }));
      } else {
        out.push({
          key: `${param}-v`,
          parameter: param,
          function_step: vm.function_step || "",
          step_desc: vm.step_desc || vm.function_step || "",
          step_ids: stepIdsForGroup(vm),
          module: "",
        });
      }
      return;
    }
    const im = inlineLookup(param) || {};
    if (paramUpper.startsWith("INLINE_") && (im.step_id || im.item_id || im.function_step || Array.isArray(im.groups))) {
      if (Array.isArray(im.groups) && im.groups.length) {
        im.groups.forEach((g, gi) => out.push({
          key: `${param}-i-${gi}`,
          parameter: param,
          function_step: g.function_step || im.function_step || "",
          step_desc: g.step_desc || g.function_step || im.function_step || "",
          step_ids: stepIdsForGroup(g).length ? stepIdsForGroup(g) : stepIdsForGroup(im),
          module: "",
        }));
      } else {
        out.push({
          key: `${param}-i`,
          parameter: param,
          function_step: im.function_step || "",
          step_desc: im.function_step || "",
          step_ids: stepIdsForGroup(im),
          module: "",
        });
      }
    }
  });
  return out;
}

function splitParamRefs(lineageSummary) {
  const out = {};
  const seen = {};
  lineageSummary.forEach(row => {
    const param = String(row.parameter || "").trim();
    if (!param) return;
    (row.step_ids || []).forEach(sid => {
      const stepId = String(sid || "").trim();
      if (!stepId) return;
      const desc = String(row.step_desc || row.function_step || "").trim();
      const key = `${stepId}|${desc}`;
      if (!seen[param]) seen[param] = new Set();
      if (seen[param].has(key)) return;
      seen[param].add(key);
      if (!out[param]) out[param] = [];
      out[param].push({ step_id: stepId, step_desc: desc });
    });
  });
  return out;
}

export function buildSplitCheckStView(matrix, { valueForCell, displayForValue } = {}) {
  const source = matrix || {};
  const headers = Array.isArray(source.headers) ? source.headers : [];
  const rows = Array.isArray(source.rows) ? source.rows : [];
  const splitRows = rows.flatMap(row => {
    const cells = row?._cells || {};
    const perHeader = headers.map((_, ci) => {
      const cell = cells[String(ci)] || cells[ci] || {};
      const rawValue = valueForCell
        ? valueForCell(cell, row, ci)
        : (hasStValue(cell?.plan) ? cell.plan : cell?.actual);
      if (!hasStValue(rawValue)) return { raw: "", display: "" };
      const raw = String(rawValue);
      const displayRaw = displayForValue ? displayForValue(rawValue, row, cell, ci) : raw;
      return { raw, display: String(displayRaw ?? raw) };
    });
    const order = [];
    const seen = new Set();
    perHeader.forEach(item => {
      if (!item.raw || seen.has(item.raw)) return;
      seen.add(item.raw);
      order.push(item);
    });
    return order.map((item, idx) => {
      const label = `S${idx}`;
      const checkCells = {};
      perHeader.forEach((value, ci) => {
        checkCells[String(ci)] = { actual: value.raw && value.raw === item.raw ? "✓" : "", plan: "", split_check: true };
      });
      return {
        _param: String(row?._param || row?._display || ""),
        _display: String(row?._display || row?._param || ""),
        _split_value: item.display,
        _split_label: label,
        _prefix_cells: [String(row?._display || row?._param || ""), item.display, label],
        _cells: checkCells,
      };
    });
  });
  return {
    ...source,
    headers,
    rows: splitRows,
    prefix_columns: SPLIT_CHECK_PREFIX_COLUMNS,
    display_mode: "split_check",
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
  showLineageSummary = true,
  showTitle = true,
  emptyMessage = "Split 체크로 표시할 값이 없습니다",
  maxHeight = 620,
}) {
  const st = stView || embed?.st_view;
  if (!st || !Array.isArray(st.headers) || !Array.isArray(st.rows)) return footer || null;

  const effectiveSource = source ?? embed?.source ?? "";
  const effectiveNote = note ?? embed?.note ?? "";
  const effectiveProduct = inferProductFromEmbed(embed, product, effectiveSource);
  const [knobMeta, setKnobMeta] = useState({});
  const [vmMeta, setVmMeta] = useState({});
  const [inlineMeta, setInlineMeta] = useState({});

  useEffect(() => {
    if (!effectiveProduct) {
      setKnobMeta({});
      setVmMeta({});
      setInlineMeta({});
      return;
    }
    const metaQs = qs({ product: effectiveProduct });
    sf(`/api/splittable/knob-meta${metaQs}`).then(d => setKnobMeta(d.features || {})).catch(() => setKnobMeta({}));
    sf(`/api/splittable/vm-meta${metaQs}`).then(d => setVmMeta(d.items || {})).catch(() => setVmMeta({}));
    sf(`/api/splittable/inline-meta${metaQs}`).then(d => setInlineMeta(d.items || {})).catch(() => setInlineMeta({}));
  }, [effectiveProduct]);

  const headers = st.headers || [];
  const rawPrefixColumns = Array.isArray(st.prefix_columns) ? st.prefix_columns.map(v => String(v || "").trim()).filter(Boolean) : [];
  const splitCheckMode = String(st.display_mode || embed?.display_mode || embed?.st_scope?.display_mode || "") === "split_check" && rawPrefixColumns.length >= 3;
  const firstColWidth = 288;
  const dataColWidth = 115;
  const prefixColumns = splitCheckMode ? rawPrefixColumns : [];
  const prefixColWidths = splitCheckMode ? [240, 140, 80].slice(0, prefixColumns.length) : [firstColWidth];
  while (prefixColWidths.length < (splitCheckMode ? prefixColumns.length : 1)) prefixColWidths.push(100);
  const prefixTotalWidth = prefixColWidths.reduce((sum, value) => sum + value, 0);
  const stickyLeft = (idx) => prefixColWidths.slice(0, idx).reduce((sum, value) => sum + value, 0);
  const headerGroups = splitTableHeaderGroups(st);
  const rootLotId = String(st.root_lot_id || "").trim();
  const groupLotValues = [...new Set(headerGroups.map(g => String(g?.label || "").trim()).filter(Boolean))];
  const lotIdLabel = groupLotValues.join(", ") || String(st.lot_id_label || "").trim();
  const hasLotContext = !!(rootLotId || lotIdLabel);
  const rowLabels = st.row_labels || {};
  const rootRowLabel = rowLabels.root_lot_id || "root_lot_id";
  const lotRowLabel = rowLabels.lot_id || "lot_id";
  const paramRowLabel = rowLabels.parameter || "항목";
  const visiblePrefixColumns = splitCheckMode ? prefixColumns : [paramRowLabel];
  const hasRootRow = hasLotContext;
  const hasLotRow = hasLotContext || headerGroups.length > 0;
  const rootHeaderHeight = hasRootRow ? 32 : 0;
  const lotHeaderHeight = hasLotRow ? 24 : 0;
  const waferTop = rootHeaderHeight + lotHeaderHeight;
  const lotContextTitle = `root_lot_id: ${rootLotId || "-"}\nlot_id: ${lotIdLabel || "-"}`;
  const lineageSummary = useMemo(
    () => buildLineageSummary(st.rows, knobMeta, vmMeta, inlineMeta),
    [st.rows, knobMeta, vmMeta, inlineMeta],
  );
  const stepRefsByParam = useMemo(() => splitParamRefs(lineageSummary), [lineageSummary]);
  const rowSpans = useMemo(() => {
    if (!splitCheckMode) return st.rows.map(() => 1);
    return st.rows.map((row, idx) => {
      const param = String(row?._param || "").trim();
      if (!param) return 1;
      const prev = idx > 0 ? String(st.rows[idx - 1]?._param || "").trim() : "";
      if (prev === param) return 0;
      let span = 1;
      for (let i = idx + 1; i < st.rows.length; i += 1) {
        if (String(st.rows[i]?._param || "").trim() !== param) break;
        span += 1;
      }
      return span;
    });
  }, [splitCheckMode, st.rows]);
  const uniq = useMemo(() => {
    const out = {};
    for (const r of st.rows) {
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
  }, [st.rows]);

  const renderSplitParamCell = (value, param) => {
    const refs = stepRefsByParam[String(param || "").trim()] || [];
    const warn = stepWarning(refs.map(ref => ref.step_id));
    if (!refs.length && !warn) return value;
    return (
      <>
        {value}
        {refs.map(ref => (
          <div key={`${ref.step_id}-${ref.step_desc}`} style={{ marginTop: 3, fontSize: 11, lineHeight: 1.25, color: "var(--text-secondary)", fontWeight: 700 }}>
            [ {ref.step_id}{ref.step_desc ? ` (${ref.step_desc})` : ""} ]
          </div>
        ))}
        {warn && (
          <div style={{ marginTop: 4, fontSize: 11, lineHeight: 1.25, color: "rgba(220,38,38,0.95)", fontWeight: 700 }}>
            {warn}
          </div>
        )}
      </>
    );
  };

  const shellStyle = { marginTop: 8, padding: 10, border: "1px solid var(--border)", borderRadius: 6, background: "var(--bg-primary)", maxWidth: "100%" };
  const scrollerStyle = { maxHeight, overflow: "auto", border: "1px solid #555", borderRadius: 0, background: "var(--bg-card)" };
  const tableStyle = { borderCollapse: "collapse", fontSize: 11, fontFamily: "monospace", width: "100%", minWidth: "100%", tableLayout: "fixed" };
  const headStyle = { border: "1px solid var(--border)", padding: "3px 4px", background: "var(--bg-secondary)", textAlign: "center", position: "sticky", top: 0, lineHeight: 1.25, zIndex: 2, wordBreak: "break-all" };
  const cellStyle = { border: "1px solid var(--border)", padding: "2px 3px", textAlign: "center", lineHeight: 1.25, whiteSpace: "normal", wordBreak: "break-all" };
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
  const rootLeftStyle = { boxSizing: "border-box", height: rootHeaderHeight, padding: "4px 8px", background: "var(--bg-tertiary)", border: "1px solid #555", position: "sticky", top: 0, left: 0, zIndex: 5, textAlign: "left", fontFamily: "monospace", fontSize: 14, lineHeight: 1.25, color: "var(--text-secondary)", fontWeight: 800, whiteSpace: "normal", wordBreak: "break-word", width: prefixTotalWidth, minWidth: prefixTotalWidth };
  const rootHeadStyle = { boxSizing: "border-box", height: rootHeaderHeight, textAlign: "center", padding: "0 8px", lineHeight: `${rootHeaderHeight - 1}px`, fontWeight: 700, fontSize: 14, color: "var(--accent)", background: "var(--bg-tertiary)", border: "1px solid #555", position: "sticky", top: 0, zIndex: 4, fontFamily: "monospace", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" };
  const lotLeftStyle = { boxSizing: "border-box", height: lotHeaderHeight, padding: "0 8px", background: "var(--bg-tertiary)", border: "1px solid #555", position: "sticky", top: rootHeaderHeight, left: 0, zIndex: 5, textAlign: "left", fontFamily: "monospace", fontSize: 14, color: "var(--text-secondary)", fontWeight: 800, width: prefixTotalWidth, minWidth: prefixTotalWidth };
  const lotHeadStyle = { boxSizing: "border-box", height: lotHeaderHeight, textAlign: "center", padding: "0 6px", fontWeight: 800, fontSize: 14, color: "var(--text-primary)", background: "var(--bg-tertiary)", border: "1px solid #555", position: "sticky", top: rootHeaderHeight, zIndex: 4, fontFamily: "monospace", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" };
  const waferLeftStyle = { textAlign: "left", padding: "8px 10px", fontWeight: 700, fontSize: 14, color: "var(--accent)", border: "1px solid #555", background: "var(--bg-tertiary)", position: "sticky", top: waferTop, left: 0, zIndex: 5, width: firstColWidth, minWidth: firstColWidth };
  const waferHeadStyle = { textAlign: "center", padding: "6px 8px", fontWeight: 600, fontSize: 14, color: "var(--text-secondary)", border: "1px solid #555", borderBottom: "2px solid #555", background: "var(--bg-tertiary)", position: "sticky", top: waferTop, zIndex: 3, whiteSpace: "normal", wordBreak: "break-word", minWidth: 100 };
  const paramCellStyle = { padding: "6px 10px", fontWeight: 600, fontSize: 14, color: "var(--text-primary)", border: "1px solid #555", background: "var(--bg-secondary)", position: "sticky", left: 0, zIndex: 2, whiteSpace: "normal", wordBreak: "break-word", lineHeight: 1.35 };
  const stCellStyle = { background: "var(--bg-card)", color: "var(--text-primary)", padding: "4px 8px", border: "1px solid #555", textAlign: "center", fontSize: 14, whiteSpace: "normal", wordBreak: "break-word", lineHeight: 1.35, position: "relative" };
  const prefixHeadStyle = (idx) => ({ ...waferLeftStyle, left: stickyLeft(idx), width: prefixColWidths[idx], minWidth: prefixColWidths[idx], zIndex: 6 - Math.min(idx, 3), color: "var(--text-secondary)" });
  const prefixCellStyle = (idx) => ({ ...paramCellStyle, left: stickyLeft(idx), width: prefixColWidths[idx], minWidth: prefixColWidths[idx], zIndex: 4 - Math.min(idx, 2), fontWeight: idx === 0 ? 700 : 600 });

  return (
    <div style={shellStyle}>
      {showTitle && (
        <div style={{ fontSize: 14, fontWeight: 700, color: "var(--accent)", marginBottom: 4 }}>
          SplitTable {effectiveSource && <span style={{ color: "var(--text-secondary)", fontWeight: 500 }}>· {effectiveSource}</span>}
        </div>
      )}
      {effectiveNote && <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 4 }}>{effectiveNote}</div>}
      <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 6 }}>
        <span style={{ padding: "2px 7px", borderRadius: 999, border: "1px solid var(--border)", background: "var(--bg-tertiary)", fontFamily: "monospace" }}>
          {headers.length} wafer 표시{headers.length ? " · 가로 스크롤" : ""}
        </span>
      </div>
      <div style={scrollerStyle}>
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
                <th key={i} style={waferHeadStyle}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {st.rows.map((r, ri) => {
              const rawPrefixCells = Array.isArray(r._prefix_cells) ? r._prefix_cells : [];
              const prefixValues = visiblePrefixColumns.map((_, idx) => {
                if (splitCheckMode) {
                  if (rawPrefixCells[idx] != null) return String(rawPrefixCells[idx]);
                  if (idx === 0) return String(r._display || r._param || "");
                  if (idx === 1) return String(r._split_value || "");
                  if (idx === 2) return String(r._split_label || "");
                  return "";
                }
                return String(r._display || r._param || "").replace(/^[A-Z]+_/, "");
              });
              const span = rowSpans[ri] || 0;
              return (
                <tr key={r.key || `${r._param || "row"}-${ri}`}>
                  {prefixValues.map((value, pi) => {
                    if (splitCheckMode && pi === 0 && span === 0) return null;
                    const splitStyle = splitCheckMode && pi === 2 ? splitCheckColorStyle(value) : {};
                    const rowSpanProps = splitCheckMode && pi === 0 && span > 1 ? { rowSpan: span } : {};
                    return (
                      <td key={`prefix-${pi}`} {...rowSpanProps} style={{ ...prefixCellStyle(pi), ...splitStyle, ...(splitCheckMode && pi === 0 ? { verticalAlign: "top" } : {}) }}>
                        {splitCheckMode && pi === 0 ? renderSplitParamCell(value, r._param) : value}
                      </td>
                    );
                  })}
                  {headers.map((_, ci) => {
                    const cell = (r._cells && (r._cells[ci] || r._cells[String(ci)])) || {};
                    const display = hasStValue(cell.actual) ? String(cell.actual) : "";
                    const bg = splitCheckMode ? (display ? splitCheckColorStyle(prefixValues[2] || r._split_label) : {}) : splitTableCellBg(hasStValue(cell.plan) ? cell.plan : cell.actual, uniq, r._param);
                    const plan = splitCheckMode ? {} : stPlanStyle(cell);
                    const hasPlan = hasStValue(cell.plan);
                    const hasActual = hasStValue(cell.actual);
                    const isPlanOnly = !splitCheckMode && hasPlan && !hasActual;
                    const isMismatch = !splitCheckMode && hasPlan && hasActual && String(cell.plan) !== String(cell.actual);
                    const isAppliedPlan = !splitCheckMode && hasPlan && hasActual && String(cell.plan) === String(cell.actual);
                    return (
                      <td key={ci} style={{ ...stCellStyle, ...bg, ...plan, ...(splitCheckMode && display ? { fontWeight: 900 } : {}) }}>
                        {splitCheckMode
                          ? display
                          : isMismatch
                            ? <span style={{ color: "var(--danger)", fontWeight: 700 }}>{"✗ "}{display}<span style={{ fontSize: 14, color: "var(--danger)" }}>{" (≠" + cell.plan + ")"}</span></span>
                            : isAppliedPlan
                              ? <span style={{ color: OK.fg, fontWeight: 700 }}>{"✓ "}{String(cell.plan)}<span style={{ fontSize: 14, color: OK.fg }}>{" (plan 적용)"}</span></span>
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
      {!splitCheckMode && showLineageSummary && lineageSummary.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: "var(--accent)", marginBottom: 6 }}>Parameter별 적용 step 요약</div>
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
                {lineageSummary.map(x => (
                  <tr key={x.key}>
                    <td style={{ ...cellStyle, textAlign: "left" }}>{x.parameter}</td>
                    <td style={{ ...cellStyle, textAlign: "left", color: "var(--text-secondary)" }}>{x.function_step || "-"}</td>
                    <td style={{ ...cellStyle, textAlign: "left", color: INFO.fg, fontWeight: 700 }}>
                      {(x.step_ids || []).length ? x.step_ids.join(", ") : "-"}
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
      {footer}
    </div>
  );
}
