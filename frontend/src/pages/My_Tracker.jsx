import { useState, useEffect, useRef, useCallback } from "react";
import Loading from "../components/Loading";
import PageGear from "../components/PageGear";
import { toast } from "../components/Toast";
import { Button, Card, Chip, EmptyState, Filter, Pill, TabStrip, TableWrap, Tbl } from "../components/UXKit";
import { authSrc, sf as apiSf } from "../lib/api";
const API = "/api/tracker";
const TRACKER_PRIORITY_TONE = { critical: "danger", high: "brand", normal: "info", low: "neutral" };
const connectedPanelSection = { padding: 16, borderBottom: "1px solid var(--border)" };
const connectedPanelSectionLast = { ...connectedPanelSection, borderBottom: "none" };
const connectedSectionTitle = { fontSize: 14, fontWeight: 600, marginBottom: 8, color: "var(--text-secondary)" };
const connectedListRow = { padding: "12px 0", borderTop: "1px solid var(--border)" };
const connectedListRowFirst = { ...connectedListRow, borderTop: "none", paddingTop: 0 };
// v8.8.3: 인증 헤더 자동 주입을 위해 lib/api.sf 로 교체. legacy 시그니처 유지.
const sf = (url, o) => apiSf(url, o);

function trackerStepInfo(lot, fab, et){
  const stepId = lot.current_step || fab?.step_id || et?.[0]?.step_id || "";
  const funcStep = (
    lot.current_function_step || lot.function_step || lot.func_step ||
    fab?.function_step || fab?.func_step ||
    et?.[0]?.function_step || et?.[0]?.func_step || ""
  );
  const seq = lot.current_step_seq ?? lot.step_seq ?? et?.[0]?.step_seq ?? null;
  return { stepId, funcStep, seq };
}

function formatTrackerStep(lot, fab, et){
  const { stepId, funcStep, seq } = trackerStepInfo(lot, fab, et);
  if (!stepId && !funcStep) return "조회 필요";
  const stepLabel = stepId ? (seq !== null && seq !== "" ? `${stepId} / seq ${seq}` : stepId) : "";
  if (funcStep && stepLabel) return `${stepLabel} > ${funcStep}`;
  return funcStep || stepLabel;
}

function etStepSummaries(lot, et){
  if (!Array.isArray(et) || !et.length) {
    return Array.isArray(lot?.et_step_summary) && lot.et_step_summary.length ? lot.et_step_summary : [];
  }
  const grouped = new Map();
  (Array.isArray(et) ? et : []).forEach(p => {
    const stepId = p?.step_id || "";
    const funcStep = p?.function_step || p?.func_step || "";
    const key = `${stepId}::${funcStep}`;
    const row = grouped.get(key) || {
      step_id: stepId,
      function_step: funcStep,
      func_step: funcStep,
      step_seqs: [],
      seq_points: {},
      flats: [],
      pt_count: 0,
      package_count: 0,
      last_time: "",
    };
    const seq = p?.step_seq;
    if (seq !== null && seq !== undefined && seq !== "") {
      if (!row.step_seqs.includes(seq)) row.step_seqs.push(seq);
      const seqKey = String(seq);
      row.seq_points[seqKey] = Number(row.seq_points[seqKey] || 0) + Number(p?.pt_count || 0);
    }
    if (p?.flat && !row.flats.includes(p.flat)) row.flats.push(p.flat);
    row.pt_count += Number(p?.pt_count || 0);
    row.package_count += 1;
    if (p?.time && String(p.time) > String(row.last_time || "")) row.last_time = p.time;
    grouped.set(key, row);
  });
  return Array.from(grouped.values())
    .map(r => {
      const seqs = [...(r.step_seqs || [])].sort((a, b) => {
        const na = Number(a); const nb = Number(b);
        if (Number.isFinite(na) && Number.isFinite(nb)) return na - nb;
        return String(a).localeCompare(String(b));
      });
      const seqPoints = seqs
        .map(seq => ({ seq, pt_count: Number(r.seq_points?.[String(seq)] || 0) }))
        .filter(p => p.pt_count > 0);
      const func = r.function_step || r.func_step || "";
      return {
        ...r,
        step_seqs: seqPoints.map(p => p.seq),
        seq_points: seqPoints,
        step_seq_combo: seqPoints.map(p => p.seq).join(", "),
        seq_pt_combo: seqPoints.map(p => `seq${p.seq}(${p.pt_count}pt)`).join(","),
        flat_combo: r.flats.join(", "),
        label: `${r.step_id || "-"} > ${func || "function step 미등록"}`,
        display_label: func ? `${func}(${r.step_id || "-"})` : (r.step_id || "-"),
      };
    })
    .sort((a, b) => String(b.last_time || "").localeCompare(String(a.last_time || "")));
}

function formatEtSummaryLine(row){
  if (!row) return "";
  const func = row.function_step || row.func_step || "";
  const label = row.display_label || (func ? `${func}(${row.step_id || "-"})` : (row.step_id || "-"));
  let seq = row.seq_pt_combo || "";
  if (!seq && Array.isArray(row.seq_points) && row.seq_points.length) {
    seq = row.seq_points
      .filter(p => Number(p.pt_count || 0) > 0)
      .map(p => `seq${p.seq}(${Number(p.pt_count || 0)}pt)`)
      .join(",");
  }
  if (!seq) {
    const combo = row.step_seq_combo || (Array.isArray(row.step_seqs) ? row.step_seqs.join(", ") : "");
    seq = combo ? `seq ${combo}${row.pt_count ? ` (${row.pt_count}pt)` : ""}` : "";
  }
  return `${label}${seq ? ` ${seq}` : ""}`;
}

function etSummarySeqPoints(row){
  if (!row) return [];
  if (Array.isArray(row.seq_points) && row.seq_points.length) {
    return row.seq_points
      .map(p => ({ seq: p.seq, pt_count: Number(p.pt_count || 0) }))
      .filter(p => p.pt_count > 0);
  }
  const combo = String(row.seq_pt_combo || "");
  if (combo) {
    const matches = [...combo.matchAll(/seq\s*([^,(]+)\s*\(\s*(\d+)\s*pt\s*\)/gi)];
    if (matches.length) {
      return matches
        .map(m => ({ seq: String(m[1] || "").trim(), pt_count: Number(m[2] || 0) }))
        .filter(p => p.seq && p.pt_count > 0);
    }
  }
  const seqs = Array.isArray(row.step_seqs) ? row.step_seqs : [];
  const total = Number(row.pt_count || 0);
  if (seqs.length === 1 && total > 0) return [{ seq: seqs[0], pt_count: total }];
  return [];
}

function etSummaryBlock(row){
  if (!row) return null;
  const func = row.function_step || row.func_step || "";
  const label = row.display_label || (func ? `${func}(${row.step_id || "-"})` : (row.step_id || "-"));
  return { label, seqs: etSummarySeqPoints(row), pt_count: Number(row.pt_count || 0) };
}

function formatEtSummaryDetail(row){
  const block = etSummaryBlock(row);
  if (!block) return "";
  const seqLines = block.seqs.map(p => `  step_seq ${p.seq}: ${Number(p.pt_count || 0)}pt`);
  if (!seqLines.length) seqLines.push("  step_seq 상세 없음");
  return [block.label, ...seqLines].join("\n");
}

function getEtStatus(lot, et){
  const hasMeasure = typeof lot.et_measured === "boolean" ? lot.et_measured : (Array.isArray(et) && et.length > 0);
  if (hasMeasure) {
    const summary = etStepSummaries(lot, et);
    const first = summary[0];
    const lines = summary.slice(0, 8).map(formatEtSummaryLine).filter(Boolean);
    const blocks = summary.slice(0, 8).map(etSummaryBlock).filter(Boolean);
    const detail = summary.slice(0, 8).map(formatEtSummaryDetail).filter(Boolean).join("\n\n");
    return {
      icon: "",
      text: first ? formatEtSummaryLine(first) : `측정 완료${lot.et_last_seq !== null && lot.et_last_seq !== undefined && lot.et_last_seq !== "" ? ` · seq ${lot.et_last_seq}` : ""}`,
      lines,
      blocks,
      color: "var(--ok)",
      title: detail || (lines.length ? lines.join("\n") : (lot.et_recent_formatted || lot.et_last_time || "")),
    };
  }
  if (lot.last_checked_at) {
    return { icon: "❌", text: "관련 ET 데이터 없음", color: "var(--danger)" };
  }
  return { icon: "⏳", text: "모니터 중, 미측정", color: "var(--warn)" };
}

function isMonitorCategory(category, roleNames = {}) {
  const monitorName = String(roleNames?.monitor || "Monitor").trim().toLowerCase();
  return String(category || "").trim().toLowerCase() === monitorName;
}

function isAnalysisCategory(category, roleNames = {}) {
  const analysisName = String(roleNames?.analysis || "Analysis").trim().toLowerCase();
  const value = String(category || "").trim().toLowerCase();
  return value === "analysis" || value === analysisName;
}

function visibleTrackerCategories(cats = [], roleNames = {}) {
  return (Array.isArray(cats) ? cats : []).filter(c => !isAnalysisCategory(c?.name || c, roleNames));
}

function trackerCategorySource(category, roleNames = {}, cats = []) {
  const c = String(category || "").trim().toLowerCase();
  if (c === String(roleNames?.monitor || "Monitor").trim().toLowerCase()) return "fab";
  const cat = (Array.isArray(cats) ? cats : []).find(x => String(x?.name || "").trim().toLowerCase() === c);
  const src = String(cat?.source || "").trim().toLowerCase();
  if (["fab", "et", "both", "auto"].includes(src)) return src;
  return "fab";
}

function monitorCategoryName(roleNames = {}, cats = []) {
  const wanted = String(roleNames?.monitor || "Monitor").trim() || "Monitor";
  const hit = (Array.isArray(cats) ? cats : []).find(c => String(c?.name || c || "").trim().toLowerCase() === wanted.toLowerCase());
  return String(hit?.name || hit || wanted).trim() || "Monitor";
}

function waferSummaryText(summary) {
  const wafers = Array.isArray(summary?.wafer_ids) ? summary.wafer_ids : [];
  const label = String(summary?.wafer_label || compressWaferIds(wafers) || "").trim();
  const count = Number(summary?.wafer_count || wafers.length || 0);
  if (!count && !label) return "조회 전";
  return `Qty ${count || wafers.length}매${label ? ` · ${label}` : ""}`;
}

function compressWaferIds(values = []) {
  const nums = [];
  const labels = [];
  const seenLabels = new Set();
  (Array.isArray(values) ? values : String(values || "").split(/[,;\s]+/)).forEach(v => {
    const raw = String(v || "").trim();
    if (!raw) return;
    const core = raw.replace(/^(#|WAFER|WF|W)\s*/i, "").trim();
    if (/^\d+$/.test(core)) {
      nums.push(Number(core));
      return;
    }
    const key = raw.toUpperCase();
    if (!seenLabels.has(key)) {
      seenLabels.add(key);
      labels.push(raw);
    }
  });
  const sorted = Array.from(new Set(nums)).sort((a, b) => a - b);
  const parts = [];
  for (let i = 0; i < sorted.length; i += 1) {
    const start = sorted[i];
    let end = start;
    while (i + 1 < sorted.length && sorted[i + 1] === end + 1) {
      i += 1;
      end = sorted[i];
    }
    parts.push(start === end ? `${start}` : `${start}~${end}`);
  }
  parts.push(...labels);
  return parts.length ? `#${parts.join(",")}` : "";
}

function stepSummaryText(rows = []) {
  const source = Array.isArray(rows) ? rows : [];
  if (!source.length) return "조회 전";
  const seen = new Set();
  const parts = [];
  source.forEach(r => {
    const step = String(r?.step_id || "").trim();
    const func = String(r?.func_step || r?.function_step || "").trim();
    const label = step && func ? `${step}(${func})` : (step || func || "-");
    if (seen.has(label)) return;
    seen.add(label);
    parts.push(label);
  });
  return parts.length > 2 ? `${parts.slice(0, 2).join(", ")} 외 ${parts.length - 2}` : (parts.join(", ") || "조회 전");
}

function stepSummaryTitle(rows = []) {
  return (Array.isArray(rows) ? rows : [])
    .map(r => `W${r?.wafer_id || "-"} · ${r?.step_id || "-"} · ${r?.func_step || "-"}${r?.update_time ? ` · ${r.update_time}` : ""}`)
    .join("\n");
}

function expandLotsForSubmit(lots = []) {
  const out = [];
  (Array.isArray(lots) ? lots : []).forEach(lot => {
    const { _summary, _summaryRows, ...base } = lot || {};
    const rows = Array.isArray(_summaryRows) ? _summaryRows : [];
    const hasBaseValue = ["product", "monitor_prod", "root_lot_id", "lot_id", "wafer_id", "purpose", "comment"].some(k => String(base?.[k] || "").trim());
    if (!rows.length && !hasBaseValue) return;
    const first = rows[0] || {};
    const latest = rows.slice().sort((a, b) => String(b?.update_time || "").localeCompare(String(a?.update_time || "")))[0] || first;
    const wafers = Array.isArray(_summary?.wafer_ids) ? _summary.wafer_ids : rows.map(r => r?.wafer_id).filter(Boolean);
    const func = latest?.func_step || latest?.function_step || "";
    out.push({
      ...base,
      product: _summary?.product || latest?.product || base.product || base.monitor_prod || "",
      monitor_prod: _summary?.product || latest?.product || base.monitor_prod || base.product || "",
      root_lot_id: _summary?.root_lot_id || latest?.root_lot_id || base.root_lot_id || "",
      lot_id: _summary?.lot_id || latest?.lot_id || base.lot_id || "",
      wafer_id: base.wafer_id || "",
      wafer_ids: wafers,
      wafer_count: Number(_summary?.wafer_count || wafers.length || 0),
      wafer_label: _summary?.wafer_label || compressWaferIds(wafers),
      lot_progress_rows: rows,
      current_step: _summary?.step_id || latest?.step_id || base.current_step || "",
      current_function_step: _summary?.func_step || func || base.current_function_step || "",
      function_step: _summary?.func_step || func || base.function_step || "",
      func_step: _summary?.func_step || func || base.func_step || "",
      last_move_at: _summary?.update_time || latest?.update_time || base.last_move_at || "",
      last_scan_source: rows.length ? "fab" : (base.last_scan_source || ""),
      last_scan_source_root: rows.length ? "lot_progress_cache" : (base.last_scan_source_root || ""),
      last_scan_status: rows.length ? "ok" : (base.last_scan_status || ""),
    });
  });
  return out;
}

// v8.8.3: description_html 에 박힌 `/api/tracker/image?name=...` URL 에 세션 토큰(t=) 을
// 쿼리로 덧붙여서 dangerouslySetInnerHTML 로 렌더된 <img> 도 인증을 통과하도록 한다.
// (인폼로그에서 authSrc 로 해결한 패턴을 tracker 에 동일 적용.)
function withTrackerImageAuth(html) {
  if (!html || typeof html !== "string") return html;
  return html.replace(/\/api\/tracker\/image\?name=([^"'&\s>]+)/g, (m) => authSrc(m));
}

/* ─── Inject tracker image styles once ─── */
if(typeof document!=="undefined"&&!document.getElementById("trk-img-styles")){
  const s=document.createElement("style");s.id="trk-img-styles";
  // v8.8.13: hover 확대 제거 — 확대 미리보기 없이 본 이미지 크기로만 표시.
  s.textContent=`
.desc-editor img,.desc-view img{max-width:300px!important;border-radius:6px;transition:max-width 0.2s;display:block;margin:4px 0}
.desc-editor img{cursor:pointer}
.desc-editor img:hover{outline:2px solid var(--brand);outline-offset:2px}
`;
  document.head.appendChild(s);
}

/* ─── Rich Description Editor (contentEditable + image paste + click resize) ─── */
function DescEditor({ value, onChange, placeholder }) {
  const ref = useRef(null);

  const handlePaste = useCallback((e) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (const item of items) {
      if (item.type.startsWith("image/")) {
        e.preventDefault();
        const blob = item.getAsFile();
        const reader = new FileReader();
        reader.onload = () => {
          const img = document.createElement("img");
          img.src = reader.result;
          img.style.cssText = "max-width:300px;border-radius:6px;display:block;margin:6px 0;cursor:pointer;";
          img.title = "클릭해서 크기 변경 (S/M/L)";
          img.dataset.size = "L";
          const sel = window.getSelection();
          if (sel.rangeCount) {
            const range = sel.getRangeAt(0);
            range.deleteContents();
            range.insertNode(document.createElement("br"));
            range.insertNode(img);
            range.collapse(false);
          }
          if (ref.current) onChange(ref.current.innerHTML);
        };
        reader.readAsDataURL(blob);
        return;
      }
    }
  }, [onChange]);

  // Click on image inside editor → cycle size
  const handleClick = useCallback((e) => {
    if (e.target.tagName === "IMG") {
      e.preventDefault();
      const img = e.target;
      const cur = parseInt(img.style.maxWidth) || 300;
      if (cur >= 250) { img.style.maxWidth = "150px"; img.dataset.size = "M"; }
      else if (cur >= 120) { img.style.maxWidth = "80px"; img.dataset.size = "S"; }
      else { img.style.maxWidth = "300px"; img.dataset.size = "L"; }
      if (ref.current) onChange(ref.current.innerHTML);
    }
  }, [onChange]);

  const handleInput = useCallback(() => {
    if (ref.current) onChange(ref.current.innerHTML);
  }, [onChange]);

  useEffect(() => {
    if (ref.current && ref.current.innerHTML !== value) {
      ref.current.innerHTML = value || "";
    }
  }, []);

  return (
    <div ref={ref} contentEditable suppressContentEditableWarning className="desc-editor"
      onPaste={handlePaste} onInput={handleInput} onClick={handleClick}
      data-placeholder={placeholder}
      style={{
        width: "100%", minHeight: 80, padding: "8px 12px", borderRadius: 6,
        border: "1px solid var(--border)", background: "var(--bg-primary)",
        color: "var(--text-primary)", fontSize: 14, outline: "none", lineHeight: 1.7,
        marginBottom: 8, overflowY: "auto", maxHeight: 400, whiteSpace: "pre-wrap",
        wordBreak: "break-word",
      }} />
  );
}

/* ─── Lot/Wafer Editable Table ─── */
// v8.8.33: currentStep 맵 + step watcher 통합.
//   - 새 입력은 lot_id 기준. 기존 이슈의 root_lot_id 는 조회 호환용으로만 유지.
//   - Monitor 는 FAB step 만, ET source 계열은 ET 측정 패키지도 함께 조회.
//   - 특정 step 설정 + 메일 옵션 인라인 저장.
function LotTable({ lots, setLots, readOnly, issueId, product, category, roleNames, cats }) {
  const [stepData, setStepData] = useState({});  // {rowIdx: {fab:{...}, et:[...]} }
  const [busyRow, setBusyRow] = useState(null);
  const [batchBusy, setBatchBusy] = useState(false);
  const [batchDone, setBatchDone] = useState(0);
  const autoFetchRef = useRef("");
  // Readonly 면 Lot 별 실시간 step 조회 — 이슈 상세에서 호출.
  const fetchStep = useCallback((idx, lot) => {
    if (!lot) return;
    const params = new URLSearchParams();
    const rowProduct = String(lot?.product || lot?.monitor_prod || product || "").trim();
    if (rowProduct) params.set("product", rowProduct);
    if (rowProduct) params.set("monitor_prod", rowProduct);
    if (category) params.set("category", category);
    const root = (lot.root_lot_id || "").trim();
    const lid = (lot.lot_id || "").trim();
    if (root && root.length === 5 && /^[A-Za-z0-9]+$/.test(root)) params.set("root_lot_id", root);
    else if (root) params.set("lot_id", root);
    else if (lid) params.set("lot_id", lid);
    if (lot.wafer_id) params.set("wafer_id", String(lot.wafer_id));
    setBusyRow(idx);
    sf(API + "/lot-step?" + params.toString())
      .then(d => setStepData(prev => ({ ...prev, [idx]: d.snapshot || {} })))
      .catch(() => {})
      .finally(() => setBusyRow(null));
  }, [product, category]);
  const fetchAllSteps = useCallback(() => {
    if (!readOnly || !issueId || batchBusy) return;
    setBatchBusy(true);
    setBatchDone(0);
    sf(API + "/lot-check-all", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ issue_id: issueId }),
    }).then(d => {
      const rows = Array.isArray(d?.rows) ? d.rows : [];
      const nextStepData = {};
      rows.forEach(row => { nextStepData[row.row_index] = row.snapshot || {}; });
      setStepData(nextStepData);
      setBatchDone(Number(d?.done || rows.length || 0));
      const serverLots = Array.isArray(d?.lots) ? d.lots : null;
      setLots(prev => (serverLots || prev).map((lot, idx) => {
        const row = rows.find(r => r.row_index === idx);
        if (!row) return lot;
        const rowProduct = row.product || row.monitor_prod || lot.product || lot.monitor_prod || "";
        return {
          ...lot,
          product: rowProduct,
          monitor_prod: rowProduct,
          wafer_ids: Array.isArray(row.wafer_ids) ? row.wafer_ids : (Array.isArray(lot.wafer_ids) ? lot.wafer_ids : []),
          wafer_count: Number(row.wafer_count || lot.wafer_count || 0),
          wafer_label: row.wafer_label || lot.wafer_label || "",
          lot_progress_rows: Array.isArray(row.lot_progress_rows) ? row.lot_progress_rows : (Array.isArray(lot.lot_progress_rows) ? lot.lot_progress_rows : []),
          current_step: row.current_step || "",
          current_function_step: row.current_function_step || row.function_step || row.func_step || "",
          function_step: row.function_step || row.current_function_step || row.func_step || "",
          func_step: row.func_step || row.current_function_step || row.function_step || "",
          current_step_seq: row.current_step_seq ?? row.step_seq ?? null,
          step_seq: row.current_step_seq ?? row.step_seq ?? null,
          et_measured: typeof row.et_measured === "boolean" ? row.et_measured : null,
          et_last_seq: row.et_last_seq ?? null,
          et_last_time: row.et_last_time || "",
          et_last_step: row.et_last_step || "",
          et_last_function_step: row.et_last_function_step || "",
          et_step_summary: Array.isArray(row.et_step_summary) ? row.et_step_summary : [],
          et_step_seq_summary: row.et_step_seq_summary || "",
          et_recent_formatted: row.et_recent_formatted || "",
          last_checked_at: row.last_checked_at || "",
          last_move_at: row.last_move_at || "",
          last_scan_source: row.last_scan_source || "",
          last_scan_source_root: row.last_scan_source_root || lot.last_scan_source_root || "",
          last_scan_status: row.last_scan_status || "",
        };
      }));
    }).catch(() => {
      setBatchDone(0);
    }).finally(() => setBatchBusy(false));
  }, [readOnly, issueId, batchBusy, setLots]);
  useEffect(() => {
    if (!readOnly || !issueId || !lots.length || batchBusy) return;
    const key = `${issueId}:${String(category || "").trim().toLowerCase()}`;
    if (autoFetchRef.current === key) return;
    autoFetchRef.current = key;
    fetchAllSteps();
  }, [readOnly, issueId, category, lots.length, batchBusy, fetchAllSteps]);
  return LotTableInner({
    lots, setLots, readOnly, issueId, product, category,
    roleNames, cats,
    stepData, setStepData, busyRow, setBusyRow, fetchStep, fetchAllSteps, batchBusy, batchDone,
  });
}

function LotTableInner({ lots, setLots, readOnly, issueId, product, category, roleNames, cats, stepData, setStepData, busyRow, setBusyRow, fetchStep, fetchAllSteps, batchBusy, batchDone }) {
  const [productOptions, setProductOptions] = useState([]);
  const [lotOptions, setLotOptions] = useState({});
  const [openLotDrop, setOpenLotDrop] = useState(null);
  const categorySource = trackerCategorySource(category, roleNames, cats);
  const monitorMode = isMonitorCategory(category, roleNames);
  const createMonitorMode = !readOnly && monitorMode;

  const handlePaste = (e) => {
    const text = e.clipboardData?.getData("text/plain");
    if (!text) return;
    const lines = text.trim().split("\n");
    if (lines.length === 0) return;
    // Check if tab-separated (Excel paste)
    if (lines[0].includes("\t")) {
      e.preventDefault();
      const newRows = lines.map(line => {
        const parts = line.split("\t");
        if (createMonitorMode) {
          const multi = parts.length > 1;
          return {
            product: multi ? (parts[0] || "").trim() : "",
            monitor_prod: multi ? (parts[0] || "").trim() : "",
            root_lot_id: "",
            lot_id: (multi ? parts[1] : parts[0] || "").trim(),
            wafer_id: "",
            purpose: (parts[2] || "").trim(),
            comment: (parts[3] || "").trim(),
          };
        }
        const hasPurposeColumn = parts.length >= 5;
        return {
          product: (parts[0] || "").trim(),
          monitor_prod: (parts[0] || "").trim(),
          root_lot_id: "",
          lot_id: (parts[1] || "").trim(),
          wafer_id: (parts[2] || "").trim(),
          purpose: (hasPurposeColumn ? parts[3] : "").trim(),
          comment: (parts[hasPurposeColumn ? 4 : 3] || "").trim(),
        };
      }).filter(r => r.product || r.lot_id || r.wafer_id || r.purpose || r.comment);
      setLots(prev => [...prev, ...newRows]);
    }
  };

  const updateCell = (idx, field, value) => {
    setLots(prev => prev.map((r, i) => i === idx ? { ...r, [field]: value } : r));
  };
  const updateRow = (idx, patch) => {
    setLots(prev => prev.map((r, i) => i === idx ? { ...r, ...patch } : r));
  };
  const fetchLotSummary = useCallback((idx, row) => {
    if (readOnly) return;
    const lotId = String(row?.lot_id || row?.root_lot_id || "").trim();
    if (!lotId) return;
    const params = new URLSearchParams();
    params.set("lot_id", lotId);
    if (category) params.set("category", category);
    const rowProduct = String(row?.product || row?.monitor_prod || "").trim();
    if (rowProduct) params.set("product", rowProduct);
    setBusyRow(idx);
    sf(API + "/lot-summary?" + params.toString())
      .then(d => {
        const rows = Array.isArray(d?.rows) ? d.rows : [];
        const first = rows[0] || {};
        const func = first.func_step || first.function_step || "";
        updateRow(idx, {
          _summary: d || {},
          _summaryRows: rows,
          product: first.product || rowProduct || "",
          monitor_prod: first.product || rowProduct || "",
          root_lot_id: d?.root_lot_id || first.root_lot_id || row?.root_lot_id || "",
          lot_id: d?.lot_id || first.lot_id || lotId,
          wafer_id: row?.wafer_id || "",
          wafer_ids: Array.isArray(d?.wafer_ids) ? d.wafer_ids : [],
          wafer_count: Number(d?.wafer_count || 0),
          wafer_label: d?.wafer_label || compressWaferIds(d?.wafer_ids || []),
          lot_progress_rows: rows,
          current_step: first.step_id || "",
          current_function_step: func || "",
          function_step: func || "",
          func_step: func || "",
          last_move_at: first.update_time || "",
          last_scan_status: rows.length ? "ok" : "no_match",
        });
        if (rows.length) {
          setStepData(prev => ({
            ...prev,
            [idx]: {
              fab: {
                step_id: first.step_id || "",
                function_step: func || "",
                func_step: func || "",
                time: first.update_time || "",
                root_lot_id: first.root_lot_id || "",
                lot_id: first.lot_id || lotId,
                wafer_id: first.wafer_id || "",
              },
              et: [],
            },
          }));
        }
      })
      .catch(() => {})
      .finally(() => setBusyRow(null));
  }, [readOnly, category, setLots, setBusyRow, setStepData]);

  useEffect(() => {
    if (readOnly) return;
    const params = new URLSearchParams();
    if (category) params.set("category", category);
    sf(API + "/products?" + params.toString())
      .then(d => setProductOptions(Array.isArray(d?.products) ? d.products : []))
      .catch(() => setProductOptions([]));
  }, [readOnly, category]);
  const loadLotOptions = useCallback((idx, row, prefixValue = "") => {
    if (readOnly) return;
    const params = new URLSearchParams();
    if (category) params.set("category", category);
    const rowProduct = String(row?.product || row?.monitor_prod || "").trim();
    if (!rowProduct && !createMonitorMode) {
      setLotOptions(prev => ({ ...prev, [idx]: [] }));
      return;
    }
    if (rowProduct) params.set("product", rowProduct);
    const prefix = String(prefixValue ?? row?.root_lot_id ?? row?.lot_id ?? "").trim();
    if (prefix) params.set("prefix", prefix);
    params.set("limit", "200");
    sf(API + "/lot-candidates?" + params.toString())
      .then(d => setLotOptions(prev => ({ ...prev, [idx]: Array.isArray(d?.candidates) ? d.candidates : [] })))
      .catch(() => setLotOptions(prev => ({ ...prev, [idx]: [] })));
  }, [readOnly, createMonitorMode, category]);

  const productChoices = (current) => {
    const cur = String(current || "").trim();
    const seen = new Set();
    const out = [];
    [cur, ...productOptions].forEach(v => {
      const text = String(v || "").trim();
      if (!text) return;
      const key = text.toUpperCase();
      if (seen.has(key)) return;
      seen.add(key);
      out.push(text);
    });
    return out;
  };
  const lotChoices = (idx, current) => {
    const cur = String(current || "").trim();
    const rows = Array.isArray(lotOptions[idx]) ? lotOptions[idx] : [];
    const seen = new Set();
    const out = [];
    if (cur) {
      seen.add(cur.toUpperCase());
      out.push({ value: cur, type: "current" });
    }
    rows.forEach(c => {
      const value = String(c?.value || c || "").trim();
      if (!value) return;
      const key = value.toUpperCase();
      if (seen.has(key)) return;
      seen.add(key);
      out.push(typeof c === "object" ? c : { value, type: "lot_id" });
    });
    return out;
  };

  const lotChoiceRows = (idx, current) => {
    const q = String(current || "").trim().toLowerCase();
    const rows = lotChoices(idx, current);
    if (!q) return rows;
    return rows.filter(c => {
      const value = String(c?.value || "").toLowerCase();
      const root = String(c?.root_lot_id || "").toLowerCase();
      const prod = String(c?.product || "").toLowerCase();
      return value.includes(q) || root.includes(q) || prod.includes(q);
    });
  };
  const lotChoiceMeta = (c) => [
    c?.product,
    c?.root_lot_id ? `root ${c.root_lot_id}` : "",
    c?.step_id,
    c?.source_root,
  ].filter(Boolean).join(" · ");
  const renderLotInput = ({ idx, row, value, disabled, placeholder, onValueChange, onPick, onBlur }) => {
    const choices = lotChoiceRows(idx, value);
    const isOpen = openLotDrop === idx && !disabled && choices.length > 0;
    return (
      <div style={{ position: "relative", width: "100%" }}>
        <input value={value}
          disabled={disabled}
          onFocus={() => {
            if (!disabled) {
              setOpenLotDrop(idx);
              if (!(lotOptions[idx] || []).length) loadLotOptions(idx, row, value);
            }
          }}
          onChange={e => {
            const v = e.target.value;
            setOpenLotDrop(idx);
            onValueChange(v);
          }}
          onBlur={e => {
            const nextValue = e.target.value;
            setTimeout(() => setOpenLotDrop(cur => cur === idx ? null : cur), 140);
            if (onBlur) onBlur(nextValue);
          }}
          placeholder={placeholder}
          style={{ ...sheetInput, color: disabled ? "var(--text-secondary)" : "var(--text-primary)", cursor: disabled ? "not-allowed" : "text" }} />
        {isOpen && (
          <div style={{ maxHeight: 170, overflow: "auto", border: "1px solid var(--border)", borderRadius: 6, background: "var(--bg-card)", marginTop: 2 }}>
            {choices.slice(0, 80).map((c, j) => (
              <div key={`${c.type || "lot"}-${c.value}-${j}`}
                onMouseDown={e => {
                  e.preventDefault();
                  onPick(c);
                  setOpenLotDrop(null);
                }}
                style={{ padding: "6px 10px", fontSize: 14, cursor: "pointer", borderBottom: "1px solid var(--border)", color: "var(--text-primary)" }}
                onMouseEnter={e => e.currentTarget.style.background = "var(--bg-hover)"}
                onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
                <div style={{ fontFamily: "monospace", fontWeight: 700, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{c.value}</div>
                {lotChoiceMeta(c) && <div style={{ marginTop: 2, fontSize: 14, color: "var(--text-secondary)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{lotChoiceMeta(c)}</div>}
              </div>
            ))}
          </div>
        )}
      </div>
    );
  };
  const removeRow = (idx) => setLots(prev => prev.filter((_, i) => i !== idx));
  const addRow = () => setLots(prev => [...prev, { product: "", monitor_prod: "", root_lot_id: "", lot_id: "", wafer_id: "", purpose: "", comment: "" }]);

  const cellStyle = {
    padding: "5px 8px", borderBottom: "1px solid var(--border)", fontSize: 14,
  };
  const sheetCell = {
    padding: 0,
    borderBottom: "1px solid var(--border)",
    borderRight: "1px solid var(--border)",
    background: "rgba(255,255,255,0.55)",
    verticalAlign: "middle",
  };
  const sheetInput = {
    width: "100%",
    padding: "9px 10px",
    border: "none",
    background: "transparent",
    color: "var(--text-primary)",
    fontSize: 14,
    outline: "none",
    boxSizing: "border-box",
    fontFamily: "monospace",
  };
  const showEtColumn = readOnly && (categorySource === "et" || categorySource === "both");
  const showStepColumn = !createMonitorMode && (readOnly || monitorMode) && (categorySource === "fab" || categorySource === "both" || categorySource === "auto");
  const baseHeaders = createMonitorMode
    ? ["product", "lot_id", "purpose", "comment", "wafer_ids", "step_id(func_step)"]
    : monitorMode
    ? ["product", "lot_id", "wafer_ids", "purpose", "comment"]
    : ["product", "lot_id", "wafer_id", "purpose", "comment"];
  const readOnlyColSpan = baseHeaders.length + (showStepColumn ? 1 : 0) + (showEtColumn ? 1 : 0) + 3;

  // v8.8.5: 빈 상태 플레이스홀더 행 대신, 항상 테이블 형태 유지 + 맨 아래 [+ 행추가] 빈 행.
  //   - readOnly 가 아닐 때: 데이터 행들 아래에 "+ 버튼만 있는 빈 셀 행" 하나 (여기 클릭 = addRow).
  //   - 외부 상단 `+ 행 추가` 버튼은 제거 — 테이블 안 한 곳에서만 추가.
  // v9.0.0: watch 저장 핸들러.
  //   - category.source 기반 자동 결정 (Monitor=fab, ET source=et). 행별 수동 변경은 UI 에서 허용하지 않음.
  //   - v9.0.0 fix: sf() 로 교체 — 이전 raw fetch 는 세션 토큰 미주입으로 401 → 메일 체크 저장이 조용히 실패.
  const saveWatch = (i, patch) => {
    if (!issueId) return;
    const lot = lots[i] || {};
    const watch = { ...(lot.watch || {}), ...patch };
    const body = {
      issue_id: issueId, row_index: i,
      target_step_id: watch.target_step_id || "",
      target_et_step_id: watch.target_et_step_id || "",
      target_et_seqs: watch.target_et_seqs || "",
      // v9.0.0: source 는 카테고리에서 가져옴. Monitor 는 FAB 로 강제.
      source: isMonitorCategory(category, roleNames) ? "fab" : ((patch.source) || watch.source || (categorySource === "et" ? "et" : "fab")),
    };
    sf(API + "/lot-watch", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(d => {
      if (d?.watch) {
        setLots(prev => prev.map((r, idx) => idx === i ? { ...r, watch: d.watch } : r));
      }
    }).catch(e => { console.warn("watch 저장 실패:", e?.message || e); });
  };
  return (
    <div onPaste={!readOnly ? handlePaste : undefined}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
        <span style={{ fontSize: 14, fontWeight: 600 }}>product / lot_id / purpose / comment ({lots.length})</span>
        {readOnly ? (
          <div style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 14, color: "var(--text-secondary)", fontFamily: "monospace" }}>{batchBusy ? `0/${lots.length} 완료` : `${batchDone}/${lots.length} 완료`}</span>
            <button onClick={fetchAllSteps} disabled={!issueId || batchBusy || lots.length === 0}
              style={{ padding: "6px 12px", borderRadius: 6, border: "1px solid var(--accent)", background: batchBusy ? "var(--bg-tertiary)" : "transparent", color: "var(--accent)", fontSize: 14, fontWeight: 700, cursor: !issueId || batchBusy || lots.length === 0 ? "not-allowed" : "pointer", opacity: !issueId || batchBusy || lots.length === 0 ? 0.6 : 1 }}>
              {batchBusy ? "조회 중..." : "전체 조회"}
            </button>
          </div>
        ) : <span style={{ fontSize: 14, color: "var(--text-secondary)" }}>{createMonitorMode ? "product와 lot_id 입력 후 wafer/step 자동 조회" : "Excel TSV 붙여넣기 지원 · product / lot_id / wafer_id / purpose / comment 순서"}</span>}
      </div>
      {!readOnly && (
        <>
          <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 4 }}>
            {createMonitorMode ? "Monitor 이슈는 product와 lot_id를 기준으로 cache에서 wafer 매수, wafer 번호, step_id, func_step을 불러옵니다. purpose와 comment는 별도 관리 컬럼입니다." : "product를 먼저 선택하면 해당 product의 lot_id 후보가 입력 중 자동 필터링됩니다."}
          </div>
        </>
      )}
      <TableWrap maxHeight={260}>
        <Tbl style={{ borderCollapse: "separate", borderSpacing: 0 }}>
          <thead><tr>
            {baseHeaders.map(h => (
              <th key={h} style={{ textAlign: "left", padding: "8px 10px", background: "var(--bg-tertiary)", borderBottom: "1px solid var(--border)", borderRight: "1px solid var(--border)", fontSize: 14, color: "var(--text-secondary)", fontWeight: 600, fontFamily: "monospace", whiteSpace: "nowrap", position: "sticky", top: 0, zIndex: 1 }}>{h}</th>
            ))}
            {showStepColumn && <th style={{ textAlign: "left", padding: "8px 10px", background: "var(--bg-tertiary)", borderBottom: "1px solid var(--border)", borderRight: "1px solid var(--border)", fontSize: 14, color: "var(--text-secondary)", fontWeight: 600, fontFamily: "monospace", whiteSpace: "nowrap", position: "sticky", top: 0, zIndex: 1 }}>{monitorMode ? "step_id(func_step)" : "step_id > func_step"}</th>}
            {readOnly && <>
              {showEtColumn && <th style={{ textAlign: "left", padding: "8px 10px", background: "var(--bg-tertiary)", borderBottom: "1px solid var(--border)", borderRight: "1px solid var(--border)", fontSize: 14, color: "var(--text-secondary)", fontWeight: 600, fontFamily: "monospace", position: "sticky", top: 0, zIndex: 1 }}>ET 측정</th>}
              <th style={{ textAlign: "left", padding: "8px 10px", background: "var(--bg-tertiary)", borderBottom: "1px solid var(--border)", borderRight: "1px solid var(--border)", fontSize: 14, color: "var(--text-secondary)", fontWeight: 600, fontFamily: "monospace", position: "sticky", top: 0, zIndex: 1 }}>watch</th>
            </>}
            {!readOnly && <th style={{ width: 40, background: "var(--bg-tertiary)", borderBottom: "1px solid var(--border)", position: "sticky", top: 0, zIndex: 1 }} />}
            {readOnly && <>
              <th style={{ textAlign: "left", padding: "8px 10px", background: "var(--bg-tertiary)", borderBottom: "1px solid var(--border)", borderRight: "1px solid var(--border)", fontSize: 14, color: "var(--text-secondary)", fontFamily: "monospace", position: "sticky", top: 0, zIndex: 1 }}>작성자</th>
              <th style={{ textAlign: "left", padding: "8px 10px", background: "var(--bg-tertiary)", borderBottom: "1px solid var(--border)", fontSize: 14, color: "var(--text-secondary)", fontFamily: "monospace", position: "sticky", top: 0, zIndex: 1 }}>날짜</th>
            </>}
          </tr></thead>
          <tbody>
            {lots.map((l, i) => {
              const step = stepData?.[i] || {};
              const fab = step.fab || {};
              const et = Array.isArray(step.et) ? step.et : [];
              const watch = l.watch || {};
              const rowProduct = l.product || l.monitor_prod || "";
              const lotValue = l.lot_id || l.root_lot_id || "";
              const stepInfo = trackerStepInfo(l, fab, et);
              const currentStepText = formatTrackerStep(l, fab, et);
              const stepIdText = stepInfo.stepId ? (stepInfo.seq !== null && stepInfo.seq !== "" ? `${stepInfo.stepId} / seq ${stepInfo.seq}` : stepInfo.stepId) : "";
              const compactStepText = stepIdText && stepInfo.funcStep ? `${stepIdText}(${stepInfo.funcStep})` : (stepIdText || stepInfo.funcStep || "조회 필요");
              const lastMoveAt = l.last_move_at || fab.time || et[0]?.time || "";
              const checkedAt = l.last_checked_at || "";
              const scanStatus = l.last_scan_status || "";
              const scanRoot = l.last_scan_source_root || "";
              const etStatus = getEtStatus(l, et);
              const lotProgressRows = Array.isArray(l.lot_progress_rows) ? l.lot_progress_rows : [];
              const stepTitle = [
                lotProgressRows.length ? stepSummaryTitle(lotProgressRows) : "",
                stepIdText && `step_id: ${stepIdText}`,
                stepInfo.funcStep && `func_step: ${stepInfo.funcStep}`,
                lastMoveAt && `step time: ${lastMoveAt}`,
                checkedAt && `refreshed: ${checkedAt}`,
                scanRoot && `DB: ${scanRoot}`,
                scanStatus && `status: ${scanStatus}`,
              ].filter(Boolean).join("\n");
              if (createMonitorMode) {
                const summaryRows = Array.isArray(l._summaryRows) ? l._summaryRows : [];
                const summary = l._summary || {};
                const summaryTitle = stepSummaryTitle(summaryRows) || stepTitle;
                return (
                  <tr key={i}>
                    <td style={{ ...sheetCell, minWidth: 150 }}>
                      <select value={rowProduct}
                        onChange={e => {
                          const v = e.target.value;
                          updateRow(i, { product: v, monitor_prod: v, root_lot_id: "", lot_id: "", wafer_id: "", _summary: null, _summaryRows: [] });
                          setLotOptions(prev => ({ ...prev, [i]: [] }));
                          loadLotOptions(i, { ...l, product: v, monitor_prod: v, root_lot_id: "", lot_id: "" }, "");
                        }}
                        style={sheetInput}>
                        <option value="">product 선택</option>
                        {productChoices(rowProduct).map(p => <option key={p} value={p}>{p}</option>)}
                      </select>
                    </td>
                    <td style={{ ...sheetCell, minWidth: 260 }}>
                      <div style={{ display: "flex", alignItems: "flex-start", gap: 6 }}>
                        {renderLotInput({
                          idx: i,
                          row: l,
                          value: lotValue,
                          disabled: false,
                          placeholder: "lot_id 입력/검색",
                          onValueChange: v => {
                            updateRow(i, { root_lot_id: "", lot_id: v, wafer_id: "", _summary: null, _summaryRows: [] });
                            loadLotOptions(i, { ...l, root_lot_id: "", lot_id: v }, v);
                          },
                          onPick: c => {
                            const v = String(c?.value || "").trim();
                            updateRow(i, { product: c?.product || rowProduct || "", monitor_prod: c?.product || rowProduct || "", root_lot_id: "", lot_id: v, wafer_id: "", _summary: null, _summaryRows: [] });
                            fetchLotSummary(i, { ...l, product: c?.product || rowProduct || "", monitor_prod: c?.product || rowProduct || "", root_lot_id: "", lot_id: v });
                          },
                          onBlur: v => fetchLotSummary(i, { ...l, root_lot_id: "", lot_id: v }),
                        })}
                        <button onClick={() => fetchLotSummary(i, l)} disabled={busyRow === i || !lotValue}
                          style={{ marginRight: 6, padding: "3px 7px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-secondary)", fontSize: 14, cursor: busyRow === i || !lotValue ? "not-allowed" : "pointer", flexShrink: 0 }}>
                          {busyRow === i ? "…" : "조회"}
                        </button>
                      </div>
                    </td>
                    <td style={{ ...sheetCell, minWidth: 170 }}>
                      <input value={l.purpose || ""} onChange={e => updateCell(i, "purpose", e.target.value)} style={sheetInput} placeholder="purpose" />
                    </td>
                    <td style={{ ...sheetCell, minWidth: 180 }}>
                      <input value={l.comment || ""} onChange={e => updateCell(i, "comment", e.target.value)} style={sheetInput} placeholder="comment" />
                    </td>
                    <td title={Array.isArray(summary?.wafer_ids) ? summary.wafer_ids.join(", ") : ""} style={{ ...cellStyle, minWidth: 180, fontFamily: "monospace", color: summaryRows.length ? "var(--accent)" : "var(--text-secondary)", fontWeight: summaryRows.length ? 800 : 500 }}>
                      {waferSummaryText(summary)}
                    </td>
                    <td title={summaryTitle} style={{ ...cellStyle, minWidth: 260, fontFamily: "monospace", color: summaryRows.length ? "var(--text-primary)" : "var(--text-secondary)" }}>
                      {stepSummaryText(summaryRows)}
                    </td>
                    <td style={{ ...cellStyle, textAlign: "center" }}>
                      <span onClick={() => removeRow(i)} style={{ cursor: "pointer", color: "var(--danger)", fontSize: 14, fontWeight: 700 }}>×</span>
                    </td>
                  </tr>
                );
              }
              return (
              <tr key={i}>
                <td style={readOnly ? cellStyle : { ...sheetCell, minWidth: 140 }}>{readOnly ? (rowProduct || "-") : (
                  <select value={rowProduct}
                    onChange={e => {
                      const v = e.target.value;
                      updateRow(i, { product: v, monitor_prod: v, root_lot_id: "", lot_id: "" });
                      setLotOptions(prev => ({ ...prev, [i]: [] }));
                      if (v) loadLotOptions(i, { ...l, product: v, monitor_prod: v, root_lot_id: "", lot_id: "" }, "");
                    }}
                    style={sheetInput}>
                    <option value="">product 선택</option>
                    {productChoices(rowProduct).map(p => <option key={p} value={p}>{p}</option>)}
                  </select>
                )}</td>
                <td style={readOnly ? cellStyle : { ...sheetCell, minWidth: 210 }}>{readOnly ? (l.lot_id || l.root_lot_id) : (
                  renderLotInput({
                    idx: i,
                    row: l,
                    value: lotValue,
                    disabled: !rowProduct,
                    placeholder: rowProduct ? "lot_id 입력/검색" : "product 먼저 선택",
                    onValueChange: v => {
                      updateRow(i, { root_lot_id: "", lot_id: v });
                      if (rowProduct) loadLotOptions(i, { ...l, root_lot_id: "", lot_id: v }, v);
                    },
                    onPick: c => {
                      const v = String(c?.value || "").trim();
                      updateRow(i, { root_lot_id: "", lot_id: v });
                      if (monitorMode) fetchStep(i, { ...l, root_lot_id: "", lot_id: v });
                    },
                    onBlur: v => { if (monitorMode && v) fetchStep(i, { ...l, root_lot_id: "", lot_id: v }); },
                  })
                )}</td>
                <td style={readOnly ? { ...cellStyle, minWidth: monitorMode ? 170 : undefined, fontFamily: monitorMode ? "monospace" : undefined, color: monitorMode ? "var(--accent)" : undefined, fontWeight: monitorMode ? 800 : undefined } : { ...sheetCell, width: 100 }}
                    title={monitorMode ? (Array.isArray(l.wafer_ids) ? l.wafer_ids.join(", ") : "") : ""}>
                  {readOnly ? (monitorMode ? waferSummaryText(l) : l.wafer_id) : <input value={l.wafer_id || ""} onChange={e => updateCell(i, "wafer_id", e.target.value)} onBlur={() => { if (monitorMode && (l.root_lot_id || l.lot_id)) fetchStep(i, l); }} style={sheetInput} placeholder="all / 1,2 / 1~10" />}
                </td>
                <td style={readOnly ? cellStyle : { ...sheetCell, minWidth: 170 }}>{readOnly ? (l.purpose || "") : <input value={l.purpose || ""} onChange={e => updateCell(i, "purpose", e.target.value)} style={sheetInput} placeholder="purpose" />}</td>
                <td style={readOnly ? cellStyle : { ...sheetCell, minWidth: 180 }}>{readOnly ? l.comment : <input value={l.comment || ""} onChange={e => updateCell(i, "comment", e.target.value)} style={sheetInput} placeholder="comment" />}</td>
                {showStepColumn && <td style={cellStyle}>
                    {busyRow === i ? <span style={{ fontSize: 14, color: "var(--text-secondary)" }}>…</span>
                      : monitorMode ? (
                        <span title={stepTitle} style={{ display: "inline-block", maxWidth: 240, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontFamily: "monospace", fontSize: 14, color: compactStepText === "조회 필요" ? "var(--text-secondary)" : "var(--accent)", fontWeight: compactStepText === "조회 필요" ? 500 : 800 }}>
                          {compactStepText}
                        </span>
                      )
                      : currentStepText !== "조회 필요" ? (
                        <div title={stepTitle} style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 150 }}>
                          <span style={{ fontFamily: "monospace", fontSize: 14, color: "var(--accent)", fontWeight: 700 }}>{stepIdText || stepInfo.funcStep}</span>
                          {stepInfo.funcStep && stepIdText && (
                            <span style={{ fontFamily: "monospace", fontSize: 14, color: "var(--text-primary)" }}>→ {stepInfo.funcStep}</span>
                          )}
                          <span style={{ fontSize: 14, color: "var(--text-secondary)" }}>step {lastMoveAt ? String(lastMoveAt).slice(0, 16) : "-"}</span>
                          <span style={{ fontSize: 14, color: "var(--text-secondary)" }}>갱신 {checkedAt ? String(checkedAt).slice(0, 16) : "-"}{scanStatus === "no_match" ? " · DB 매칭 없음" : ""}</span>
                        </div>
                      ) : (
                        <div title={stepTitle} style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 150 }}>
                          <span style={{ fontSize: 14, color: "var(--text-secondary)" }}>조회 필요</span>
                          <span style={{ fontSize: 14, color: "var(--text-secondary)" }}>갱신 {checkedAt ? String(checkedAt).slice(0, 16) : "-"}{scanStatus === "no_match" ? " · DB 매칭 없음" : ""}</span>
                        </div>
                      )}
                    {!readOnly && (
                      <button onClick={() => fetchStep(i, l)} disabled={busyRow === i || !(l.root_lot_id || l.lot_id)}
                        style={{ marginLeft: 8, padding: "3px 7px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-secondary)", fontSize: 14, cursor: busyRow === i || !(l.root_lot_id || l.lot_id) ? "not-allowed" : "pointer" }}>
                        조회
                      </button>
                    )}
                  </td>}
                {readOnly && <>
                  {showEtColumn && <td style={cellStyle}>
                    <div
                      title={etStatus.title || (et.length > 0 ? etStepSummaries(l, et).slice(0, 5).map(formatEtSummaryLine).join("\n") : (l.et_last_time || l.last_checked_at || ""))}
                      style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 210, fontSize: 14, color: etStatus.color, fontWeight: 700, lineHeight: 1.35, whiteSpace: "normal" }}
                    >
                      {etStatus.blocks?.length
                        ? etStatus.blocks.map((block, idx) => (
                          <div key={idx} style={{ display: "flex", flexDirection: "column", gap: 1, paddingBottom: idx < etStatus.blocks.length - 1 ? 4 : 0, borderBottom: idx < etStatus.blocks.length - 1 ? "1px dashed var(--border)" : "none" }}>
                            <span style={{ color: "var(--accent)", fontFamily: "monospace", fontWeight: 800 }}>{block.label}</span>
                            {block.seqs.length ? block.seqs.map((p, j) => (
                              <span key={j} style={{ color: "var(--text-primary)", fontFamily: "monospace", fontWeight: 600 }}>
                                step_seq {p.seq} · {Number(p.pt_count || 0)}pt
                              </span>
                            )) : <span style={{ color: "var(--text-secondary)", fontWeight: 500 }}>step_seq 상세 없음</span>}
                          </div>
                        ))
                        : <span>{etStatus.icon ? `${etStatus.icon} ` : ""}{etStatus.text}</span>}
                    </div>
                  </td>}
                  <td style={cellStyle}>
                    {/* v9.0.0: watch source 는 category 기반 자동 결정 (Monitor→FAB, ET source→ET).
                        사용자는 target step (FAB) 또는 자동 이력 관측 (ET) + 메일 체크만 설정. */}
                    {(() => {
                      const effSrc = isMonitorCategory(category, roleNames) ? "fab" : (watch.source || (categorySource === "et" ? "et" : "fab"));
                      const isEt = effSrc === "et";
                      return (
                        <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 14, flexWrap: "wrap" }}>
                          <span title={isEt ? "ET source 카테고리: ET 측정 이력 감지" : "Monitor 카테고리: FAB step 도달 감지"}
                                className={`pill ${isEt ? "pill--pink" : "pill--info"}`}
                                style={{ padding: "3px 8px", fontSize: 14, fontWeight: 700 }}>
                            {isEt ? "ET" : "FAB"}
                          </span>
                          {!isEt && (
                            <input value={watch.target_step_id || ""} placeholder="target step"
                              onBlur={e => saveWatch(i, { target_step_id: e.target.value })}
                              onChange={e => {
                                const v = e.target.value;
                                setLots(prev => prev.map((r, idx) => idx === i ? { ...r, watch: { ...(r.watch || {}), target_step_id: v } } : r));
                              }}
                              title="대문자2+숫자6+뒤6 형식. 뒤 6자리 숫자가 target 이상이면 fire (앞 prefix+head 동일 필요)"
                              style={{ ...sheetInput, width: 130, fontSize: 14, border: "1px solid var(--border)", borderRadius: 6, background: "var(--bg-primary)", padding: "6px 8px" }} />
                          )}
                          {isEt && (
                            <>
                              <input value={watch.target_et_step_id || ""} placeholder="ET step/func"
                                onBlur={e => saveWatch(i, { target_et_step_id: e.target.value })}
                                onChange={e => {
                                  const v = e.target.value;
                                  setLots(prev => prev.map((r, idx) => idx === i ? { ...r, watch: { ...(r.watch || {}), target_et_step_id: v } } : r));
                                }}
                                title="비우면 모든 ET step 관측. step_id 또는 VIA_DC 같은 func_step 이름 일부도 매칭"
                                style={{ ...sheetInput, width: 118, fontSize: 14, border: "1px solid var(--border)", borderRadius: 6, background: "var(--bg-primary)", padding: "6px 8px" }} />
                              <input value={watch.target_et_seqs || ""} placeholder="%seq1% OR %seq2%"
                                onBlur={e => saveWatch(i, { target_et_seqs: e.target.value })}
                                onChange={e => {
                                  const v = e.target.value;
                                  setLots(prev => prev.map((r, idx) => idx === i ? { ...r, watch: { ...(r.watch || {}), target_et_seqs: v } } : r));
                                }}
                                title="비우면 모든 seq. 1,2는 둘 다 찍혔을 때, %seq1% OR %seq2%는 둘 중 하나가 찍혔을 때 알림"
                                style={{ ...sheetInput, width: 126, fontSize: 14, border: "1px solid var(--border)", borderRadius: 6, background: "var(--bg-primary)", padding: "6px 8px" }} />
                            </>
                          )}
                          {watch.last_fired_at && (
                            <span title={`최근 알림: ${watch.last_fired_et_signature || watch.last_fired_step_id || "-"}\n${watch.last_fired_at}`} style={{ fontSize: 14, color: "var(--text-secondary)", fontFamily: "monospace" }}>
                              알림 {String(watch.last_fired_at).slice(5, 16).replace("T", " ")}
                            </span>
                          )}
                        </div>
                      );
                    })()}
                  </td>
                </>}
                {!readOnly && <td style={{ ...cellStyle, textAlign: "center" }}>
                  <span onClick={() => removeRow(i)} style={{ cursor: "pointer", color: "var(--danger)", fontSize: 14, fontWeight: 700 }}>×</span>
                </td>}
                {readOnly && <>
                  <td style={{ ...cellStyle, color: "var(--text-secondary)", fontSize: 14 }}>{l.username}</td>
                  <td style={{ ...cellStyle, color: "var(--text-secondary)", fontSize: 14 }}>{l.added?.slice(0, 10)}</td>
                </>}
              </tr>
              );
            })}
            {/* v8.8.5: 빈행 + 버튼 — readOnly 가 아닐 때만 항상 노출 (데이터 없어도 표 형태 유지). */}
            {!readOnly && (
              <tr onClick={addRow} style={{ cursor: "pointer" }}
                  title="클릭 또는 + 로 행 추가 · Excel TSV 붙여넣기 지원">
                <td colSpan={baseHeaders.length} style={{ ...cellStyle, color: "var(--text-secondary)", fontSize: 14, background: "var(--bg-tertiary)", opacity: 0.7, fontFamily: "monospace" }}>
                  {lots.length === 0 ? (createMonitorMode ? "product / lot_id / purpose / comment 입력 행 추가" : "Excel 붙여넣기 (product \\t lot_id \\t wafer_id \\t purpose \\t comment) 또는 + 로 행 추가") : "(빈 행)"}
                </td>
                <td style={{ ...cellStyle, textAlign: "center", background: "var(--bg-tertiary)" }}>
                  <span style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", width: 20, height: 20, borderRadius: "50%", background: "var(--accent)", color: "#fff", fontSize: 14, fontWeight: 700, lineHeight: 1 }}>+</span>
                </td>
              </tr>
            )}
            {readOnly && lots.length === 0 && <tr><td colSpan={readOnlyColSpan} style={{ padding: 16, textAlign: "center", color: "var(--text-secondary)", fontSize: 14 }}>Lot/Wafer 데이터 없음</td></tr>}
          </tbody>
        </Tbl>
      </TableWrap>
    </div>
  );
}

function IssueMailControl({ issue, mailGroups, canEdit, onSave }) {
  if (!issue) return null;
  const cfg = issue.mail_watch || {};
  const enabled = !!cfg.enabled;
  const selectedGroups = Array.isArray(cfg.mail_group_ids) ? cfg.mail_group_ids : [];
  const groupLabel = selectedGroups.length
    ? mailGroups.filter(g => selectedGroups.includes(g.id)).map(g => g.name).join(", ") || `${selectedGroups.length}개 그룹`
    : "User only";
  const save = (patch) => {
    if (!canEdit || !onSave) return;
    onSave({
      mail: patch.mail ?? enabled,
      mail_group_ids: patch.mail_group_ids ?? selectedGroups,
    });
  };
  const toggleGroup = (groupId) => {
    const cur = new Set(selectedGroups);
    if (cur.has(groupId)) cur.delete(groupId);
    else cur.add(groupId);
    save({ mail_group_ids: Array.from(cur) });
  };
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
      <label title="메일 발송 여부는 lot/wafer 행이 아니라 이슈 단위로 적용됩니다." style={{ display: "inline-flex", alignItems: "center", gap: 6, cursor: canEdit ? "pointer" : "default", fontSize: 14, fontWeight: 700, color: enabled ? "var(--accent)" : "var(--text-secondary)" }}>
        <input type="checkbox" checked={enabled} disabled={!canEdit} onChange={e => save({ mail: e.target.checked })} />
        <span>이슈 메일 발송</span>
      </label>
      <span style={{ fontSize: 14, color: "var(--text-secondary)" }}>watch 조건이 감지되면 이 이슈 설정으로 메일을 보냅니다.</span>
      <details style={{ position: "relative" }}>
        <summary title="선택하지 않으면 이슈 작성자와 lot 추가자만 받습니다." style={{ listStyle: "none", cursor: canEdit ? "pointer" : "default", padding: "4px 8px", border: "1px solid var(--border)", borderRadius: 5, background: "var(--bg-primary)", color: selectedGroups.length ? "var(--accent)" : "var(--text-secondary)", fontSize: 14, fontWeight: selectedGroups.length ? 700 : 500 }}>
          수신 {groupLabel}
        </summary>
        {canEdit && (
          <div style={{ position: "absolute", top: 28, left: 0, zIndex: 10, minWidth: 240, maxHeight: 240, overflow: "auto", padding: 8, border: "1px solid var(--border)", borderRadius: 6, background: "var(--bg-card)", boxShadow: "0 8px 24px rgba(0,0,0,0.18)" }}>
            <label style={{ display: "flex", alignItems: "center", gap: 6, padding: "5px 4px", cursor: "pointer", fontSize: 14 }}>
              <input type="checkbox" checked={selectedGroups.length === 0} onChange={() => save({ mail_group_ids: [] })} />
              <span>User only</span>
            </label>
            <div style={{ borderTop: "1px solid var(--border)", margin: "4px 0" }} />
            {mailGroups.length === 0 && <div style={{ padding: 6, color: "var(--text-secondary)", fontSize: 14 }}>등록된 수신처 그룹 없음</div>}
            {mailGroups.map(g => (
              <label key={g.id} style={{ display: "flex", alignItems: "center", gap: 6, padding: "5px 4px", cursor: "pointer", fontSize: 14 }}>
                <input type="checkbox" checked={selectedGroups.includes(g.id)} onChange={() => toggleGroup(g.id)} />
                <span style={{ flex: 1 }}>{g.name}</span>
                <span style={{ color: "var(--text-secondary)", fontSize: 14 }}>{(g.members?.length || 0) + (g.extra_emails?.length || 0)}</span>
              </label>
            ))}
          </div>
        )}
      </details>
    </div>
  );
}

/* ─── Issue Form ─── */
function IssueForm({ onSubmit, onClose, user, roleNames }) {
  const [title, setTitle] = useState(""); const [desc, setDesc] = useState(""); const [priority, setPriority] = useState("normal");
  const [lots, setLots] = useState([]); const [links, setLinks] = useState([""]);
  const [category, setCategory] = useState(monitorCategoryName(roleNames)); const [cats, setCats] = useState([]);
  // v8.5.0: group visibility
  const [myGroups, setMyGroups] = useState([]); const [groupIds, setGroupIds] = useState([]);
  useEffect(() => { sf(API + "/categories").then(d => setCats((d.categories || []).map(c => typeof c === "string" ? { name: c, color: "#E25822" } : c))).catch(() => { }); }, []);
  useEffect(() => { sf("/api/groups/list").then(d => setMyGroups(d.groups || [])).catch(() => setMyGroups([])); }, []);
  const visibleCats = visibleTrackerCategories(cats, roleNames);
  useEffect(() => {
    const monitor = monitorCategoryName(roleNames, visibleCats);
    if (!category || !visibleCats.some(c => String(c.name || c).trim() === category)) {
      setCategory(monitor);
    }
  }, [roleNames, visibleCats, category]);
  const S = { width: "100%", padding: "8px 12px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14, outline: "none" };
  return (
    <div style={{ background: "var(--bg-secondary)", borderRadius: 10, border: "1px solid var(--border)", padding: 20, marginBottom: 20 }}>
      <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 12 }}>새 이슈</div>
      <input value={title} onChange={e => setTitle(e.target.value)} placeholder="제목" style={{ ...S, marginBottom: 8 }} />
      <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 4 }}>설명 (Ctrl+V 로 이미지 붙여넣기)</div>
      <DescEditor value={desc} onChange={setDesc} placeholder="설명 입력... Ctrl+V 로 이미지 붙여넣기" />
      <div style={{ display: "flex", gap: 8, marginBottom: 12, alignItems: "center" }}>
        <select value={priority} onChange={e => setPriority(e.target.value)} style={{ ...S, width: "auto" }}>
          <option value="low">낮음</option><option value="normal">보통</option><option value="high">높음</option><option value="critical">긴급</option>
        </select>
        <select value={category} onChange={e => setCategory(e.target.value)} style={{ ...S, width: "auto" }}>
          {!category && <option value="">-- 카테고리 필수 --</option>}
          {visibleCats.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
        </select>
      </div>
      {/* Related Links */}
      <div style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 4, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span>관련 링크 ({links.filter(l => l.trim()).length})</span>
          <span onClick={() => setLinks([...links, ""])} style={{ cursor: "pointer", color: "var(--accent)", fontSize: 14, fontWeight: 600 }}>+ 추가</span>
        </div>
        {links.map((lnk, i) => (
          <div key={i} style={{ display: "flex", gap: 6, marginBottom: 4 }}>
            <input value={lnk} onChange={e => { const nl = [...links]; nl[i] = e.target.value; setLinks(nl); }} placeholder="https://... 또는 설명" style={{ ...S, fontSize: 14 }} />
            {links.length > 1 && <span onClick={() => setLinks(links.filter((_, j) => j !== i))} style={{ cursor: "pointer", color: "var(--danger)", fontSize: 14, padding: "6px 4px", flexShrink: 0 }}>✕</span>}
          </div>
        ))}
      </div>
      <div style={{ marginBottom: 12 }}>
        <LotTable lots={lots} setLots={setLots} readOnly={false} category={category} roleNames={roleNames} cats={cats} />
      </div>
      {/* v8.5.0: 그룹 가시성 */}
      <div style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 4 }}>그룹 가시성 (비어있으면 공개)</div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {myGroups.length === 0 && <span style={{ fontSize: 14, color: "var(--text-secondary)" }}>가입된 그룹 없음</span>}
          {myGroups.map(g => {
            const on = groupIds.includes(g.id);
            return <label key={g.id} style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 14, padding: "3px 8px", borderRadius: 999, border: "1px solid " + (on ? "var(--accent)" : "var(--border)"), background: on ? "var(--accent)22" : "transparent", cursor: "pointer" }}>
              <input type="checkbox" checked={on} onChange={e => {
                const s = new Set(groupIds);
                if (e.target.checked) s.add(g.id); else s.delete(g.id);
                setGroupIds(Array.from(s));
              }} style={{ accentColor: "var(--accent)" }} />
              {g.name}
            </label>;
          })}
        </div>
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        <button onClick={() => {
          if (!title.trim()) return;
          if (!category) { toast.warn("카테고리를 지정해주세요."); return; }
          onSubmit({ title, description: desc, priority, category, images: [], lots: expandLotsForSubmit(lots), links: links.filter(l => l.trim()), group_ids: groupIds });
        }}
          style={{ padding: "8px 20px", borderRadius: 6, border: "none", background: "var(--accent)", color: "#fff", fontWeight: 600, cursor: "pointer" }}>생성</button>
        <button onClick={onClose} style={{ padding: "8px 16px", borderRadius: 6, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", cursor: "pointer" }}>취소</button>
      </div>
    </div>);
}

/* ─── Gantt Chart ─── */
function GanttChart({ issues, onIssueClick }) {
  // v8.1.5: look up category color from stored list; fall back to hash for orphan categories
  const [cats, setCats] = useState([]);
  useEffect(() => { sf(API + "/categories").then(d => setCats((d.categories || []).map(c => typeof c === "string" ? { name: c, color: "" } : c))).catch(() => { }); }, []);
  const hashColor = (name) => { let h = 0; for (let i = 0; i < name.length; i++) h = ((h << 5) - h + name.charCodeAt(i)) | 0; return `hsl(${Math.abs(h) % 360}, 58%, 58%)`; };
  const catColor = (name) => { if (!name) return "var(--muted)"; const c = cats.find(x => x.name === name); return (c && c.color) || hashColor(name); };
  const now = new Date(); const [month, setMonth] = useState(now.getMonth()); const [year, setYear] = useState(now.getFullYear());
  // v8.8.13: 간트 전용 검색 필터 (제목/담당자). 좌측 이슈 리스트 검색과 독립.
  const [gQuery, setGQuery] = useState("");
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const days = Array.from({ length: daysInMonth }, (_, i) => i + 1);
  const mStart = new Date(year, month, 1); const mEnd = new Date(year, month + 1, 0, 23, 59);
  const q = (gQuery || "").trim().toLowerCase();
  const filtered = (issues || []).filter(iss => {
    const c = new Date(iss.created || iss.timestamp); const e = iss.closed_at ? new Date(iss.closed_at) : now;
    if (!(c <= mEnd && e >= mStart)) return false;
    if (!q) return true;
    return (iss.title || "").toLowerCase().includes(q)
      || (iss.username || "").toLowerCase().includes(q)
      || (iss.category || "").toLowerCase().includes(q);
  });
  const prioColor = { critical: "var(--danger)", high: "var(--brand)", normal: "var(--info)", low: "var(--muted)" };
  const prevM = () => { if (month === 0) { setMonth(11); setYear(y => y - 1); } else setMonth(m => m - 1); };
  const nextM = () => { if (month === 11) { setMonth(0); setYear(y => y + 1); } else setMonth(m => m + 1); };
  return (<div>
    <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12, flexWrap: "wrap" }}>
      <button onClick={prevM} style={{ background: "none", border: "1px solid var(--border)", borderRadius: 4, color: "var(--text-primary)", cursor: "pointer", padding: "2px 8px" }}>◀</button>
      <span style={{ fontSize: 14, fontWeight: 700, minWidth: 120, textAlign: "center" }}>{year}.{String(month + 1).padStart(2, "0")}</span>
      <button onClick={nextM} style={{ background: "none", border: "1px solid var(--border)", borderRadius: 4, color: "var(--text-primary)", cursor: "pointer", padding: "2px 8px" }}>▶</button>
      {/* v8.8.13: 제목 / 담당자 / 카테고리 부분일치 필터 */}
      <input value={gQuery} onChange={e => setGQuery(e.target.value)}
        placeholder="🔎 제목 · 담당자 · 카테고리 검색"
        style={{ flex: 1, minWidth: 220, padding: "4px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14 }} />
      {gQuery && <span onClick={() => setGQuery("")} style={{ cursor: "pointer", color: "var(--danger)", fontSize: 14 }}>✕ 초기화</span>}
      <span style={{ fontSize: 14, color: "var(--text-secondary)" }}>{filtered.length}{gQuery ? ` / ${(issues || []).length}` : ""}건</span>
    </div>
    {filtered.length === 0 && <div style={{ padding: 40, textAlign: "center", color: "var(--text-secondary)" }}>{gQuery ? "매칭 이슈 없음" : "이슈 없음"}</div>}
    {filtered.length > 0 && (<>

    <div style={{ overflow: "auto" }}>
      <table style={{ borderCollapse: "collapse", fontSize: 14, minWidth: "100%" }}>
        <thead><tr>
          <th style={{ textAlign: "left", padding: "6px 8px", borderBottom: "2px solid var(--border)", background: "var(--bg-tertiary)", position: "sticky", left: 0, zIndex: 2, minWidth: 140 }}>이슈</th>
          {days.map(d => <th key={d} style={{ padding: "4px 2px", borderBottom: "2px solid var(--border)", background: "var(--bg-tertiary)", minWidth: 20, textAlign: "center", color: new Date(year, month, d).getDay() === 0 ? "var(--danger)" : "var(--text-secondary)" }}>{d}</th>)}
        </tr></thead>
        <tbody>{filtered.map(iss => {
          const created = new Date(iss.created || iss.timestamp); const ended = iss.closed_at ? new Date(iss.closed_at) : now;
          return (<tr key={iss.id}>
            <td style={{ padding: "4px 8px", borderBottom: "1px solid var(--border)", background: "var(--bg-secondary)", position: "sticky", left: 0, zIndex: 1, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: 220 }} title={`${iss.title} · 담당: ${iss.username || "-"}`}>
              <span onClick={() => onIssueClick && onIssueClick(iss.id)} style={{ fontWeight: 600, cursor: "pointer", color: "var(--accent)", textDecoration: "none" }} onMouseEnter={e=>e.currentTarget.style.textDecoration="underline"} onMouseLeave={e=>e.currentTarget.style.textDecoration="none"}>{iss.category ? `[${iss.category}] ` : ""}{iss.title}</span>
              {/* v8.8.13: 이슈 옆에 담당자 회색 표시 */}
              {iss.username && <span style={{ marginLeft: 6, fontSize: 14, color: "var(--text-secondary)", fontFamily: "monospace" }}>· {iss.username}</span>}
            </td>
            {days.map(d => {
              const day = new Date(year, month, d);
              const inRange = day >= new Date(created.getFullYear(), created.getMonth(), created.getDate()) && day <= new Date(ended.getFullYear(), ended.getMonth(), ended.getDate());
              const isStart = day.toDateString() === created.toDateString(); const isEnd = iss.closed_at && day.toDateString() === ended.toDateString();
              return <td key={d} style={{ borderBottom: "1px solid var(--border)", borderRight: "1px solid var(--border)", padding: 0 }}>
                {inRange && <div style={{ height: 14, background: iss.category ? catColor(iss.category) : (prioColor[iss.priority] || "var(--info)"), borderRadius: isStart ? "7px 0 0 7px" : isEnd ? "0 7px 7px 0" : "0", opacity: iss.status === "closed" ? 0.5 : 0.85 }} title={`${iss.title} (${iss.status})`} />}
              </td>;
            })}
          </tr>);
        })}</tbody>
      </table>
    </div>
    </>)}
  </div>);
}

/* ─── Main Tracker ─── */
export default function My_Tracker({ user }) {
  const [issues, setIssues] = useState([]); const [selected, setSelected] = useState(null); const [creating, setCreating] = useState(false);
  const [filter, setFilter] = useState(""); const [comment, setComment] = useState(""); const [search, setSearch] = useState("");
  const [replyDrafts, setReplyDrafts] = useState({});
  const [viewTab, setViewTab] = useState("list");
  const [editMode, setEditMode] = useState(false); const [editTitle, setEditTitle] = useState(""); const [editDesc, setEditDesc] = useState(""); const [editPrio, setEditPrio] = useState("normal"); const [editLots, setEditLots] = useState([]);
  // v8.8.13: 수정 시 카테고리도 변경 가능하도록 state 추가.
  const [editCategory, setEditCategory] = useState("");
  const [trackerPageConfig, setTrackerPageConfig] = useState({ role_names: { monitor: "Monitor" } });
  const [issueMailGroups, setIssueMailGroups] = useState([]);
  const isAdmin = user?.role === "admin";
  const statusColor = { in_progress: "var(--warn)", closed: "var(--ok)" };
  const prioColor = { critical: "var(--danger)", high: "var(--brand)", normal: "var(--info)", low: "var(--muted)" };
  // v8.1.5: look up category color from stored list; fall back to hash for orphans
  const [cats, setCats] = useState([]);
  useEffect(() => { sf(API + "/categories").then(d => setCats((d.categories || []).map(c => typeof c === "string" ? { name: c, color: "" } : c))).catch(() => { }); }, []);
  useEffect(() => {
    sf("/api/mail-groups/list")
      .then(d => setIssueMailGroups(Array.isArray(d?.groups) ? d.groups : []))
      .catch(() => setIssueMailGroups([]));
  }, []);
  const loadTrackerPageConfig = useCallback(() => {
    return sf(API + "/db-sources").then(d => setTrackerPageConfig({
      role_names: { monitor: d.role_names?.monitor || d.monitor_name || "Monitor" },
      mail_templates: d.mail_templates || {},
      template_variables: d.template_variables || [],
    })).catch(() => {});
  }, []);
  useEffect(() => { loadTrackerPageConfig(); }, [loadTrackerPageConfig]);
  const roleNames = trackerPageConfig.role_names || { monitor: "Monitor" };
  const hashColor = (name) => { let h = 0; for (let i = 0; i < name.length; i++) h = ((h << 5) - h + name.charCodeAt(i)) | 0; return `hsl(${Math.abs(h) % 360}, 58%, 58%)`; };
  const catColor = (name) => { if (!name) return "var(--muted)"; const c = cats.find(x => x.name === name); return (c && c.color) || hashColor(name); };

  const load = () => sf(API + "/issues").then(d => setIssues(d.issues || []));
  useEffect(() => { load(); }, []);
  const loadDetail = (id) => { sf(API + "/issue?issue_id=" + id).then(d => { setSelected(d.issue || d); setEditMode(false); setReplyDrafts({}); }); };
  useEffect(() => {
    const issueId = new URLSearchParams(window.location.search || "").get("issue_id");
    if (issueId) loadDetail(issueId);
  }, []);
  const create = (data) => {
    // v9.0.0 fix: lots 를 create payload 에서 제외. /create 가 lots 를 저장하고, 뒤이어 /lots/bulk 를 또
    //   호출하면 같은 행이 2번 추가되어 UI 에 중복으로 보였음. 이제 /create 에 lots=[] 로 보내고
    //   행 추가는 /lots/bulk 한 번만.
    const { lots: _lots, ...rest } = data || {};
    const body = { ...rest, lots: [], username: user?.username || "anonymous" };
    sf(API + "/create", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then(d => {
      const iid = d.id || d.issue_id;
      if (_lots && _lots.length) { sf(API + "/lots/bulk", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ issue_id: iid, rows: _lots, username: user?.username || "" }) }); }
      setCreating(false); load();
    });
  };
  const updateStatus = (id, status) => { sf(API + "/update", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ issue_id: id, status }) }).then(() => { loadDetail(id); load(); }); };
  const commentTotal = (comments = []) => (comments || []).reduce((acc, c) => acc + 1 + ((c.replies || []).length || 0), 0);
  const addComment = () => { if (!comment.trim() || !selected) return; sf(API + "/comment", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ issue_id: selected.id, username: user?.username || "", text: comment }) }).then(() => { setComment(""); loadDetail(selected.id); load(); }); };
  const addReply = (parentIndex) => {
    const text = String(replyDrafts[parentIndex] || "").trim();
    if (!text || !selected) return;
    sf(API + "/comment/reply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ issue_id: selected.id, parent_index: parentIndex, username: user?.username || "", text }),
    }).then(() => {
      setReplyDrafts(m => ({ ...m, [parentIndex]: "" }));
      loadDetail(selected.id);
      load();
    }).catch(e => toast.error(e.message || "대댓글 저장 실패"));
  };
  const canDeleteCommentItem = (item) => isAdmin || String(item?.username || "") === String(user?.username || "");
  const deleteCommentItem = (commentIndex, replyIndex = null) => {
    if (!selected) return;
    const isReply = replyIndex !== null && replyIndex !== undefined;
    if (!confirm(isReply ? "이 대댓글을 삭제할까요?" : "이 댓글을 삭제할까요?")) return;
    sf(API + "/comment/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        issue_id: selected.id,
        comment_index: commentIndex,
        reply_index: isReply ? replyIndex : null,
      }),
    }).then(() => {
      loadDetail(selected.id);
      load();
    }).catch(e => toast.error(e.message || "댓글 삭제 실패"));
  };
  const deleteIssue = () => { if (!confirm("이 이슈를 삭제할까요?")) return; sf(API + "/delete?issue_id=" + selected.id, { method: "POST" }).then(() => { setSelected(null); load(); }); };
  const canEdit = selected && (selected.username === user?.username || isAdmin);
  const startEdit = () => { if (!canEdit) return; setEditMode(true); setEditTitle(selected.title); setEditDesc(selected.description_html || selected.description || ""); setEditPrio(selected.priority || "normal"); setEditCategory(selected.category || ""); setEditLots((selected.lots || []).map(l => ({ ...l }))); };
  const saveEdit = () => {
    if (!editTitle.trim()) return;
    if (!editCategory) { toast.warn("카테고리를 지정해주세요."); return; }
    // v8.8.13: category 도 payload 에 포함 — 이전에는 FE 에서 누락되어 수정 불가였음.
    sf(API + "/update", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ issue_id: selected.id, title: editTitle, description: editDesc, priority: editPrio, category: editCategory, lots: editLots }) })
      .then(() => { setEditMode(false); loadDetail(selected.id); load(); }).catch(e => toast.error(e.message));
  };
  const saveIssueMail = (patch) => {
    if (!selected) return;
    sf(API + "/issue-mail", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        issue_id: selected.id,
        mail: !!patch.mail,
        mail_group_ids: Array.isArray(patch.mail_group_ids) ? patch.mail_group_ids : [],
      }),
    }).then(d => {
      setSelected(d.issue || selected);
      load();
    }).catch(e => {
      const msg = String(e?.message || "");
      if (/method not allowed/i.test(msg)) {
        toast.error("메일 설정 API는 POST /api/tracker/issue-mail 입니다. 현재 실행 중인 backend가 이전 버전이거나 다른 포트에 연결된 상태라서 서버 재시작이 필요합니다.");
      } else {
        toast.error(msg || "메일 설정 저장 실패");
      }
    });
  };

  const filteredIssues = issues.filter(iss => {
    if (filter && iss.status !== filter) return false;
    if (search) { const s = search.toLowerCase(); return (iss.title || "").toLowerCase().includes(s) || (iss.username || "").toLowerCase().includes(s) || (iss.category || "").toLowerCase().includes(s); }
    return true;
  });

  return (
    <div className="flow-connected-page" style={{ display: "flex", height: "calc(100vh - 52px)", background: "var(--bg-primary)", color: "var(--text-primary)" }}>
      {/* Sidebar */}
      <div style={{ width: 320, minWidth: 320, borderRight: "1px solid var(--border)", display: "flex", flexDirection: "column", background: "var(--bg-secondary)" }}>
        <div className="flow-sidebar-header" style={{ padding: "12px 16px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div style={{ minWidth: 0 }}>
            <span className="flow-sidebar-header-title" style={{ fontSize: 14, fontWeight: 700, color: "var(--text-secondary)" }}>이슈 추적</span>
            <div className="flow-sidebar-header-meta">{filteredIssues.length} / {issues.length} issues</div>
          </div>
          <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
            <Pill tone="neutral">{filteredIssues.length}</Pill>
            <Button variant="primary" onClick={() => setCreating(!creating)}>+ 새 이슈</Button>
            <PageGear title="이슈 추적 설정" canEdit={isAdmin} position="bottom-left">
              <TrackerSettings isAdmin={isAdmin} onChanged={() => { loadTrackerPageConfig(); sf(API + "/categories").then(d => setCats((d.categories || []).map(c => typeof c === "string" ? { name: c, color: "" } : c))).catch(() => {}); load(); }} />
            </PageGear>
          </div>
        </div>
        <TabStrip
          items={[{ k: "list", l: "목록" }, { k: "gantt", l: "간트" }]}
          active={viewTab}
          onChange={setViewTab}
        />
        <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>
          <input value={search} onChange={e => setSearch(e.target.value)} placeholder="제목 또는 작성자 검색..."
            style={{ width: "100%", padding: "6px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14, outline: "none" }} />
        </div>
        <div style={{ display: "flex", gap: 4, padding: "8px 12px", flexWrap: "wrap" }}>
          {["", "in_progress", "closed"].map(s => {
            const label = s === "" ? "전체" : s === "in_progress" ? "진행중" : "완료";
            return <Pill key={s || "all"} tone={filter === s ? "brand" : "neutral"} onClick={() => setFilter(s)}>{label}</Pill>;
          })}
          <span style={{ fontSize: 14, color: "var(--text-secondary)", marginLeft: "auto" }}>{filteredIssues.length}</span>
        </div>
        <div style={{ flex: 1, overflow: "auto" }}>
          {filteredIssues.map(iss => (
            <div key={iss.id} onClick={() => { loadDetail(iss.id); setViewTab("list"); }} style={{ padding: "10px 16px", borderBottom: "1px solid var(--border)", cursor: "pointer", background: selected?.id === iss.id ? "var(--bg-hover)" : "transparent" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 3 }}>
                <span title={iss.category ? `카테고리: ${iss.category}` : `상태: ${iss.status}`} style={{ width: 9, height: 9, borderRadius: "50%", background: iss.category ? catColor(iss.category) : (statusColor[iss.status] || "var(--muted)"), flexShrink: 0, border: iss.category ? "1px solid rgba(255,255,255,0.2)" : "none" }} />
                {iss.category && <Chip style={{ background: `color-mix(in srgb, ${catColor(iss.category)} 14%, transparent)`, borderColor: catColor(iss.category), color: catColor(iss.category), flexShrink: 0 }}>{iss.category}</Chip>}
                <span style={{ fontSize: 14, fontWeight: 600, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{iss.title}</span>
                <Pill tone={TRACKER_PRIORITY_TONE[iss.priority] || "neutral"}>{({low:"낮음",normal:"보통",high:"높음",critical:"긴급"}[iss.priority]) || iss.priority}</Pill>
              </div>
              <div style={{ fontSize: 14, color: "var(--text-secondary)", display: "flex", gap: 8 }}>
                <span style={{ fontWeight: 500 }}>{iss.username || "?"}</span>
                <span>{(iss.created || iss.timestamp || "")?.slice(0, 10)}</span>
                {iss.lot_count > 0 && <span>lot {iss.lot_count}건</span>}
                {iss.comment_count > 0 && <span>댓글 {iss.comment_count}개</span>}
              </div>
            </div>))}
        </div>
      </div>

      {/* Main */}
      <div style={{ flex: 1, overflow: "auto", padding: 20 }}>
        {creating && <IssueForm onSubmit={create} onClose={() => setCreating(false)} user={user} roleNames={roleNames} />}
        {viewTab === "gantt" ? <GanttChart issues={issues} onIssueClick={(id) => { loadDetail(id); setViewTab("list"); }} />
          : selected ? (<Card padding={0}>
            <section style={connectedPanelSection}>
            {/* Header */}
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 16, flexWrap: "wrap" }}>
              {editMode ? <input value={editTitle} onChange={e => setEditTitle(e.target.value)} style={{ fontSize: 18, fontWeight: 700, padding: "4px 8px", borderRadius: 6, border: "1px solid var(--accent)", background: "var(--bg-primary)", color: "var(--text-primary)", outline: "none", flex: 1 }} />
                : <span style={{ fontSize: 18, fontWeight: 700 }}>{selected.title}</span>}
              {canEdit && !editMode && <span onClick={startEdit} style={{ cursor: "pointer", fontSize: 14, color: "var(--accent)", padding: "4px 8px", borderRadius: 4, background: "var(--accent-glow)" }}>수정</span>}
              {editMode && <Pill tone="ok" onClick={saveEdit}>저장</Pill>}
              {editMode && <span onClick={() => { setEditMode(false); setEditLots([]); }} style={{ cursor: "pointer", fontSize: 14, color: "var(--text-secondary)", padding: "4px 8px", borderRadius: 4, background: "var(--bg-hover)" }}>취소</span>}
              {canEdit && <Pill tone="danger" onClick={deleteIssue}>삭제</Pill>}
              <Filter
                value={selected.status}
                onChange={e => updateStatus(selected.id, e.target.value)}
                options={[{ value: "in_progress", label: "진행중" }, { value: "closed", label: "완료" }]}
                placeholder={null}
                style={{ marginLeft: "auto" }}
              />
            </div>

            {/* Meta */}
            <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 12, display: "flex", gap: 12 }}>
              <span>작성자 <strong>{selected.username}</strong></span>
              <span>{(selected.created || selected.timestamp || "")?.slice(0, 16)}</span>
              {selected.closed_at && <span>완료: {selected.closed_at?.slice(0, 16)}</span>}
            </div>
            </section>
            <section style={connectedPanelSection}>
            <div style={connectedSectionTitle}>메일 알림</div>
            <IssueMailControl
              issue={selected}
              mailGroups={issueMailGroups}
              canEdit={canEdit}
              onSave={saveIssueMail}
            />
            </section>

            {/* Description */}
            {editMode ? (
              <section style={connectedPanelSection}>
                <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 4 }}>설명 (Ctrl+V 로 이미지 붙여넣기)</div>
                <DescEditor value={editDesc} onChange={setEditDesc} placeholder="설명 수정..." />
              </section>
            ) : (selected.description_html || selected.description) && (
              <section style={connectedPanelSection}>
              <div style={connectedSectionTitle}>설명</div>
              <style>{`.desc-view img{max-width:400px!important;border-radius:6px;display:block;margin:4px 0;}`}</style>
              <div className="desc-view" style={{ fontSize: 14, color: "var(--text-secondary)", lineHeight: 1.7, wordBreak: "break-word" }}
                dangerouslySetInnerHTML={{ __html: withTrackerImageAuth(selected.description_html || selected.description) }} />
              </section>

            )}

            {/* Priority (edit) */}
            {editMode && <section style={{ ...connectedPanelSection, display: "flex", gap: 16, flexWrap: "wrap", alignItems: "center" }}>
              <span style={{ fontSize: 14, color: "var(--text-secondary)" }}>우선순위:
                <select value={editPrio} onChange={e => setEditPrio(e.target.value)} style={{ marginLeft: 6, padding: "4px 8px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-card)", color: "var(--text-primary)", fontSize: 14 }}>
                  <option value="low">낮음</option><option value="normal">보통</option><option value="high">높음</option><option value="critical">긴급</option></select>
              </span>
              {/* v8.8.13: 카테고리 수정 허용 — 이전엔 FE state 누락으로 저장 시 변경 안 됨. */}
              <span style={{ fontSize: 14, color: "var(--text-secondary)" }}>카테고리:
                <select value={editCategory} onChange={e => setEditCategory(e.target.value)} style={{ marginLeft: 6, padding: "4px 8px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-card)", color: "var(--text-primary)", fontSize: 14 }}>
                  <option value="">카테고리 필수</option>
                  {cats.map(c => <option key={c.name || c} value={c.name || c}>{c.name || c}</option>)}
                </select>
              </span>
            </section>}

            {/* v8.8.13: 하단 썸네일 블록 제거 — 설명(desc_html) 내부의 inline 이미지만 노출.
                 legacy images 배열은 더 이상 별도 표시하지 않음 (중복 방지). */}

            {/* Related Links */}
            {selected.links?.length > 0 && <section style={connectedPanelSection}>
              <div style={connectedSectionTitle}>관련 링크</div>
              {selected.links.map((lnk, i) => (
                <div key={i} style={{ marginBottom: 4 }}>
                  {lnk.startsWith("http") ? <a href={lnk} target="_blank" rel="noopener noreferrer" style={{ color: "var(--info)", fontSize: 14, textDecoration: "none", wordBreak: "break-all" }}>{lnk}</a>
                    : <span style={{ fontSize: 14, color: "var(--text-primary)" }}>{lnk}</span>}
                </div>
              ))}
            </section>}
            {editMode && editLots.length > 0 && <section style={connectedPanelSection}>
              <div style={connectedSectionTitle}>LOT purpose/comment 수정</div>
              <TableWrap>
                <Tbl>
                  <thead><tr>
                    {["product", "lot_id", "wafer_ids", "purpose", "comment"].map(h => <th key={h} style={{ textAlign: "left", padding: "7px 9px", borderBottom: "1px solid var(--border)", background: "var(--bg-tertiary)", color: "var(--text-secondary)", fontFamily: "monospace" }}>{h}</th>)}
                  </tr></thead>
                  <tbody>{editLots.map((lot, i) => (
                    <tr key={`${lot.lot_id || lot.root_lot_id || i}-${i}`}>
                      <td style={{ padding: "6px 9px", borderBottom: "1px solid var(--border)", fontFamily: "monospace" }}>{lot.product || lot.monitor_prod || "-"}</td>
                      <td style={{ padding: "6px 9px", borderBottom: "1px solid var(--border)", fontFamily: "monospace" }}>{lot.lot_id || lot.root_lot_id || "-"}</td>
                      <td title={Array.isArray(lot.wafer_ids) ? lot.wafer_ids.join(", ") : ""} style={{ padding: "6px 9px", borderBottom: "1px solid var(--border)", fontFamily: "monospace", color: "var(--accent)", fontWeight: 700 }}>{waferSummaryText(lot)}</td>
                      <td style={{ padding: 0, borderBottom: "1px solid var(--border)" }}><input className="input" value={lot.purpose || ""} onChange={e => setEditLots(prev => prev.map((r, idx) => idx === i ? { ...r, purpose: e.target.value } : r))} style={{ width: "100%", boxSizing: "border-box", border: "none", background: "transparent" }} /></td>
                      <td style={{ padding: 0, borderBottom: "1px solid var(--border)" }}><input className="input" value={lot.comment || ""} onChange={e => setEditLots(prev => prev.map((r, idx) => idx === i ? { ...r, comment: e.target.value } : r))} style={{ width: "100%", boxSizing: "border-box", border: "none", background: "transparent" }} /></td>
                    </tr>
                  ))}</tbody>
                </Tbl>
              </TableWrap>
            </section>}
            {/* Lots table */}
            {!editMode && selected.lots?.length > 0 && <section style={connectedPanelSection}>
              <LotTable lots={selected.lots} setLots={(fn) => {
                // readonly 이긴 하지만 watch 저장 후 로컬 반영 위해 setLots 는 유용.
                if (typeof fn === "function") {
                  const next = fn(selected.lots);
                  setSelected(s => s ? { ...s, lots: next } : s);
                }
              }} readOnly={true}
              issueId={selected.id} product={selected.product || ""} category={selected.category || ""} roleNames={roleNames} cats={cats} />
            </section>}

            {/* Comments */}
            <section style={connectedPanelSectionLast}>
              <div style={connectedSectionTitle}>댓글 ({commentTotal(selected.comments || [])})</div>
              {selected.comments?.map((c, i) => (
                <div key={i} style={i === 0 ? connectedListRowFirst : connectedListRow}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4, alignItems: "center" }}>
                    <span style={{ fontSize: 14, fontWeight: 600 }}>{c.username}</span>
                    <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                      <span title={c.timestamp || ""} style={{
                        fontSize: 14, padding: "2px 8px", borderRadius: 999,
                        background: "var(--bg-primary)", color: "var(--text-primary)",
                        border: "1px solid var(--border)", fontFamily: "monospace",
                      }}>🕐 {(c.timestamp || "").replace("T", " ").slice(0, 16) || "시간 없음"}</span>
                      {canDeleteCommentItem(c) && <button type="button" onClick={() => deleteCommentItem(i)}
                        style={{ padding: "2px 7px", borderRadius: 999, border: "1px solid var(--danger-line)", background: "var(--danger-50)", color: "var(--danger)", fontSize: 14, fontWeight: 700, cursor: "pointer" }}>삭제</button>}
                    </div>
                  </div>
                  <div style={{ fontSize: 14, lineHeight: 1.6 }}>{c.text}</div>
                  {(c.lot_id || c.wafer_id) && <div style={{ fontSize: 14, color: "var(--text-secondary)", marginTop: 4 }}>{c.lot_id} / {c.wafer_id}</div>}
                  {(c.replies || []).length > 0 && <div style={{ marginTop: 8, paddingLeft: 12, borderLeft: "2px solid var(--border)", display: "grid", gap: 6 }}>
                    {(c.replies || []).map((r, ri) => (
                      <div key={ri} style={{ padding: ri === 0 ? "0 0 0 8px" : "7px 0 0 8px", borderTop: ri === 0 ? "none" : "1px solid var(--border)" }}>
                        <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center", marginBottom: 3 }}>
                          <span style={{ fontSize: 14, fontWeight: 700 }}>{r.username || "-"}</span>
                          <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                            <span title={r.timestamp || ""} style={{ fontSize: 14, color: "var(--text-secondary)", fontFamily: "monospace", whiteSpace: "nowrap" }}>{(r.timestamp || "").replace("T", " ").slice(0, 16) || "시간 없음"}</span>
                            {canDeleteCommentItem(r) && <button type="button" onClick={() => deleteCommentItem(i, ri)}
                              style={{ padding: "1px 6px", borderRadius: 999, border: "1px solid var(--danger-line)", background: "var(--danger-50)", color: "var(--danger)", fontSize: 14, fontWeight: 700, cursor: "pointer" }}>삭제</button>}
                          </div>
                        </div>
                        <div style={{ fontSize: 14, lineHeight: 1.55 }}>{r.text}</div>
                      </div>
                    ))}
                  </div>}
                  <div style={{ display: "flex", gap: 6, marginTop: 8, paddingLeft: 12 }}>
                    <input value={replyDrafts[i] || ""} onChange={e => setReplyDrafts(m => ({ ...m, [i]: e.target.value }))} placeholder="대댓글 입력..."
                      style={{ flex: 1, padding: "6px 9px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14, outline: "none" }}
                      onKeyDown={e => e.key === "Enter" && addReply(i)} />
                    <button onClick={() => addReply(i)} style={{ padding: "6px 11px", borderRadius: 6, border: "1px solid var(--accent)", background: "var(--accent-glow)", color: "var(--accent)", fontSize: 14, fontWeight: 700, cursor: "pointer" }}>답글</button>
                  </div>
                </div>))}
              <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                <input value={comment} onChange={e => setComment(e.target.value)} placeholder="댓글 입력..."
                  style={{ flex: 1, padding: "8px 12px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14, outline: "none" }}
                  onKeyDown={e => e.key === "Enter" && addComment()} />
                <button onClick={addComment} style={{ padding: "8px 16px", borderRadius: 6, border: "none", background: "var(--accent)", color: "#fff", fontSize: 14, fontWeight: 600, cursor: "pointer" }}>전송</button>
              </div>
            </section>
          </Card>) : <EmptyState title="이슈를 선택하세요" hint="좌측 목록에서 이슈를 고르거나 새 이슈를 생성하세요." />}
      </div>
    </div>);
}

/* ═══ v8.5.2 Tracker Settings (PageGear 내부) ═══ */
function TrackerSettings({ isAdmin, onChanged }) {
  const [cats, setCats] = useState([]);
  const [name, setName] = useState("");
  const [color, setColor] = useState("#E25822");
  const [msg, setMsg] = useState("");
  const [sched, setSched] = useState({ enabled: true, interval_minutes: 30, et_stable_delay_minutes: 180, status: {} });
  const [schedMsg, setSchedMsg] = useState("");
  const [schedBusy, setSchedBusy] = useState(false);
  const [dbSources, setDbSources] = useState({
    roots: [],
    monitor: "",
    analysis: "",
    monitor_name: "Monitor",
    analysis_name: "Analysis",
    mail_templates: {
      monitor: { subject: "", body: "" },
      analysis: { subject: "", body: "" },
    },
    default_mail_templates: {},
    template_variables: [],
  });
  const [dbMsg, setDbMsg] = useState("");
  const [dbBusy, setDbBusy] = useState(false);
  const [mailPreview, setMailPreview] = useState(null);
  const [previewBusy, setPreviewBusy] = useState("");
  const [lotProgressRows, setLotProgressRows] = useState([]);
  const [lotProgressMeta, setLotProgressMeta] = useState({});
  const [lotProgressFilter, setLotProgressFilter] = useState("");
  const [lotProgressBusy, setLotProgressBusy] = useState(false);
  const load = () => sf(API + "/categories").then(d => setCats((d.categories || []).map(c => typeof c === "string" ? { name: c, color: "#E25822" } : c)));
  const loadScheduler = () => sf(API + "/scheduler").then(d => setSched({
    enabled: d.enabled !== false,
    interval_minutes: Number(d.interval_minutes || 30),
    et_stable_delay_minutes: Number(d.et_stable_delay_minutes || 180),
    min_interval_minutes: Number(d.min_interval_minutes || 1),
    max_interval_minutes: Number(d.max_interval_minutes || 1440),
    min_et_stable_delay_minutes: Number(d.min_et_stable_delay_minutes || 1),
    max_et_stable_delay_minutes: Number(d.max_et_stable_delay_minutes || 1440),
    status: d.status || {},
  }));
  const loadDbSources = () => sf(API + "/db-sources").then(d => setDbSources({
    roots: Array.isArray(d.roots) ? d.roots : [],
    monitor: d.monitor || "",
    analysis: d.analysis || "",
    monitor_name: d.monitor_name || d.role_names?.monitor || "Monitor",
    analysis_name: d.analysis_name || d.role_names?.analysis || "Analysis",
    mail_templates: d.mail_templates || {
      monitor: { subject: "", body: "" },
      analysis: { subject: "", body: "" },
    },
    default_mail_templates: d.default_mail_templates || {},
    template_variables: d.template_variables || [],
  }));
  const loadLotProgressTable = (prefixValue = lotProgressFilter) => {
    const params = new URLSearchParams();
    const prefix = String(prefixValue || "").trim();
    if (prefix) params.set("prefix", prefix);
    params.set("limit", "500");
    setLotProgressBusy(true);
    return sf("/api/lot-progress/table?" + params.toString())
      .then(d => {
        setLotProgressRows(Array.isArray(d?.items) ? d.items : []);
        setLotProgressMeta({ generated_at: d?.generated_at || "", count: d?.count || 0, total: d?.total || 0 });
      })
      .catch(() => {
        setLotProgressRows([]);
        setLotProgressMeta({});
      })
      .finally(() => setLotProgressBusy(false));
  };
  // fix: arrow+Promise → Promise 가 cleanup 에 저장되어 unmount 시 crash 방지.
  useEffect(() => { load(); loadScheduler().catch(() => {}); loadDbSources().catch(() => {}); loadLotProgressTable("").catch(() => {}); }, []);
  const save = (next) => sf(API + "/categories/save", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(next),
  }).then(() => { setMsg("저장 완료"); load(); if (onChanged) onChanged(); }).catch(e => setMsg(e.message));
  const add = () => { if (!name.trim()) return; const next = [...cats, { name: name.trim(), color }]; setName(""); save(next); };
  const remove = (n) => save(cats.filter(c => c.name !== n));
  const updColor = (n, c) => save(cats.map(x => x.name === n ? { ...x, color: c } : x));
  const fmtTime = (v) => v ? String(v).replace("T", " ").slice(0, 19) : "-";
  const saveScheduler = () => {
    if (!isAdmin || schedBusy) return;
    setSchedBusy(true);
    setSchedMsg("");
    sf(API + "/scheduler/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        enabled: !!sched.enabled,
        interval_minutes: Number(sched.interval_minutes || 30),
        et_stable_delay_minutes: Number(sched.et_stable_delay_minutes || 180),
      }),
    }).then(d => {
      setSched({
        enabled: d.enabled !== false,
        interval_minutes: Number(d.interval_minutes || sched.interval_minutes || 30),
        et_stable_delay_minutes: Number(d.et_stable_delay_minutes || sched.et_stable_delay_minutes || 180),
        min_interval_minutes: Number(d.min_interval_minutes || 1),
        max_interval_minutes: Number(d.max_interval_minutes || 1440),
        min_et_stable_delay_minutes: Number(d.min_et_stable_delay_minutes || 1),
        max_et_stable_delay_minutes: Number(d.max_et_stable_delay_minutes || 1440),
        status: d.status || {},
      });
      setSchedMsg("스케줄 저장 완료");
    }).catch(e => setSchedMsg(e.message)).finally(() => setSchedBusy(false));
  };
  const runSchedulerNow = () => {
    if (!isAdmin || schedBusy) return;
    setSchedBusy(true);
    setSchedMsg("");
    sf(API + "/scheduler/run-now", { method: "POST" })
      .then(d => {
        setSched(prev => ({
          ...prev,
          enabled: d.enabled !== false,
          interval_minutes: Number(d.interval_minutes || prev.interval_minutes || 30),
          et_stable_delay_minutes: Number(d.et_stable_delay_minutes || prev.et_stable_delay_minutes || 180),
          min_interval_minutes: Number(d.min_interval_minutes || prev.min_interval_minutes || 1),
          max_interval_minutes: Number(d.max_interval_minutes || prev.max_interval_minutes || 1440),
          min_et_stable_delay_minutes: Number(d.min_et_stable_delay_minutes || prev.min_et_stable_delay_minutes || 1),
          max_et_stable_delay_minutes: Number(d.max_et_stable_delay_minutes || prev.max_et_stable_delay_minutes || 1440),
          status: d.status || d.run || {},
        }));
        setSchedMsg(d?.run?.ok === false ? (d.run.last_error || "스캔 실패") : "즉시 스캔 완료");
      }).catch(e => setSchedMsg(e.message)).finally(() => setSchedBusy(false));
  };
  const refreshLotProgressNow = () => {
    if (!isAdmin || lotProgressBusy) return;
    setLotProgressBusy(true);
    sf("/api/lot-progress/refresh", { method: "POST" })
      .then(() => loadLotProgressTable(lotProgressFilter))
      .catch(() => setLotProgressRows([]))
      .finally(() => setLotProgressBusy(false));
  };
  const saveDbSources = () => {
    if (!isAdmin || dbBusy) return;
    setDbBusy(true);
    setDbMsg("");
    sf(API + "/db-sources/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        monitor: dbSources.monitor || "",
        analysis: dbSources.analysis || "",
        monitor_name: dbSources.monitor_name || "Monitor",
        analysis_name: dbSources.analysis_name || "Analysis",
        monitor_mail_subject: dbSources.mail_templates?.monitor?.subject || "",
        monitor_mail_body: dbSources.mail_templates?.monitor?.body || "",
        analysis_mail_subject: dbSources.mail_templates?.analysis?.subject || "",
        analysis_mail_body: dbSources.mail_templates?.analysis?.body || "",
      }),
    }).then(d => {
      setDbSources({
        roots: Array.isArray(d.roots) ? d.roots : [],
        monitor: d.monitor || dbSources.monitor || "",
        analysis: d.analysis || dbSources.analysis || "",
        monitor_name: d.monitor_name || d.role_names?.monitor || dbSources.monitor_name || "Monitor",
        analysis_name: d.analysis_name || d.role_names?.analysis || dbSources.analysis_name || "Analysis",
        mail_templates: d.mail_templates || dbSources.mail_templates || {},
        default_mail_templates: d.default_mail_templates || dbSources.default_mail_templates || {},
        template_variables: d.template_variables || dbSources.template_variables || [],
      });
      setDbMsg("페이지 설정 저장 완료");
      if (onChanged) onChanged(d);
    }).catch(e => setDbMsg(e.message)).finally(() => setDbBusy(false));
  };
  const status = sched.status || {};
  const dbRootOptions = Array.from(new Set([...(dbSources.roots || []), dbSources.monitor].filter(Boolean)));
  const updateMailTemplate = (kind, field, value) => {
    setDbSources(prev => ({
      ...prev,
      mail_templates: {
        ...(prev.mail_templates || {}),
        [kind]: { ...((prev.mail_templates || {})[kind] || {}), [field]: value },
      },
    }));
  };
  const applyDefaultMailTemplate = (kind) => {
    const tpl = dbSources.default_mail_templates?.[kind];
    if (!tpl) return;
    setDbSources(prev => ({
      ...prev,
      mail_templates: {
        ...(prev.mail_templates || {}),
        [kind]: { subject: tpl.subject || "", body: tpl.body || "" },
      },
    }));
    setMailPreview(null);
  };
  const previewMailTemplate = (kind) => {
    if (previewBusy) return;
    const tpl = dbSources.mail_templates?.[kind] || {};
    setPreviewBusy(kind);
    sf(API + "/mail-template-preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        kind,
        monitor_name: dbSources.monitor_name || "Monitor",
        analysis_name: dbSources.analysis_name || "Analysis",
        subject: tpl.subject || "",
        body: tpl.body || "",
      }),
    }).then(d => setMailPreview(d)).catch(e => setMailPreview({
      kind,
      subject: "미리보기 실패",
      body: `<p>${String(e.message || e)}</p>`,
    })).finally(() => setPreviewBusy(""));
  };
  const templateVars = dbSources.template_variables?.length ? dbSources.template_variables : ["issue_id", "issue_title", "lot", "wafer_id", "reason", "recent_et"];
  return (
    <div>
      <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>DB 연결</div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: 6, marginBottom: 12 }}>
        <label style={{ display: "grid", gridTemplateColumns: "70px 1fr", gap: 8, alignItems: "center", fontSize: 14, color: "var(--text-secondary)" }}>
          Monitor명
          <input value={dbSources.monitor_name || ""} disabled={!isAdmin || dbBusy}
            onChange={e => setDbSources(prev => ({ ...prev, monitor_name: e.target.value }))}
            style={{ padding: "6px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14 }} />
        </label>
        <label style={{ display: "grid", gridTemplateColumns: "70px 1fr", gap: 8, alignItems: "center", fontSize: 14, color: "var(--text-secondary)" }}>
          Monitor DB
          <select value={dbSources.monitor || ""} disabled={!isAdmin || dbBusy}
            onChange={e => setDbSources(prev => ({ ...prev, monitor: e.target.value }))}
            style={{ padding: "6px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14 }}>
            {dbRootOptions.map(root => <option key={root} value={root}>{root}</option>)}
          </select>
        </label>
        <div style={{ fontSize: 14, fontWeight: 600, marginTop: 4 }}>메일 템플릿</div>
        <div style={{ fontSize: 14, color: "var(--text-secondary)", lineHeight: 1.5 }}>
          사용 변수: {templateVars.map(v => `{${v}}`).join(" ")}
        </div>
        {[
          ["monitor", dbSources.monitor_name || "Monitor"],
        ].map(([kind, label]) => (
          <div key={kind} style={{ display: "grid", gap: 4, padding: "6px 0", borderTop: "1px solid var(--border)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
              <div style={{ fontSize: 14, fontWeight: 600 }}>{label} 메일</div>
              <button onClick={() => applyDefaultMailTemplate(kind)} disabled={!isAdmin || dbBusy}
                style={{ padding: "4px 7px", borderRadius: 4, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", fontSize: 14, cursor: isAdmin && !dbBusy ? "pointer" : "not-allowed" }}>
                기본값 적용
              </button>
            </div>
            <input value={dbSources.mail_templates?.[kind]?.subject || ""} disabled={!isAdmin || dbBusy}
              onChange={e => updateMailTemplate(kind, "subject", e.target.value)}
              placeholder="[flow · {role_name}] {issue_title}"
              style={{ padding: "6px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14 }} />
            <textarea value={dbSources.mail_templates?.[kind]?.body || ""} disabled={!isAdmin || dbBusy}
              onChange={e => updateMailTemplate(kind, "body", e.target.value)}
              rows={5}
              placeholder="<p>{reason}</p>"
              style={{ padding: "6px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14, minHeight: 86, resize: "vertical", fontFamily: "monospace" }} />
            <button onClick={() => previewMailTemplate(kind)} disabled={!!previewBusy}
              style={{ justifySelf: "start", padding: "5px 9px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14, cursor: previewBusy ? "not-allowed" : "pointer" }}>
              {previewBusy === kind ? "미리보기 중..." : "미리보기"}
            </button>
            {mailPreview?.kind === kind && (
              <div style={{ border: "1px solid var(--border)", borderRadius: 6, overflow: "hidden", background: "var(--bg-primary)" }}>
                <div style={{ padding: "7px 9px", borderBottom: "1px solid var(--border)", fontSize: 14, lineHeight: 1.5 }}>
                  <div style={{ color: "var(--text-secondary)", marginBottom: 2 }}>제목</div>
                  <div style={{ fontWeight: 700, color: "var(--text-primary)", wordBreak: "break-word" }}>{mailPreview.subject || "-"}</div>
                </div>
                <iframe
                  title={`${kind}-mail-preview`}
                  sandbox=""
                  srcDoc={mailPreview.body || ""}
                  style={{ width: "100%", height: 220, border: 0, background: "#fff" }}
                />
              </div>
            )}
          </div>
        ))}
        <button onClick={saveDbSources} disabled={!isAdmin || dbBusy}
          style={{ justifySelf: "end", padding: "6px 10px", borderRadius: 4, border: "none", background: "var(--accent)", color: "#fff", fontSize: 14, cursor: isAdmin && !dbBusy ? "pointer" : "not-allowed", opacity: isAdmin ? 1 : 0.55 }}>
          페이지 설정 저장
        </button>
        {dbMsg && <div style={{ fontSize: 14, color: dbMsg.includes("완료") ? "var(--ok)" : "var(--danger)" }}>{dbMsg}</div>}
      </div>
      <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>자동 갱신</div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 8, alignItems: "center", marginBottom: 8 }}>
        <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 14, color: "var(--text-secondary)" }}>
          <input type="checkbox" checked={!!sched.enabled} disabled={!isAdmin || schedBusy}
            onChange={e => setSched(prev => ({ ...prev, enabled: e.target.checked }))} />
          자동 스캔
        </label>
        <button onClick={runSchedulerNow} disabled={!isAdmin || schedBusy}
          style={{ padding: "6px 10px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14, cursor: isAdmin && !schedBusy ? "pointer" : "not-allowed", opacity: isAdmin ? 1 : 0.55 }}>
          즉시 스캔
        </button>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 14, color: "var(--text-secondary)" }}>
          몇 분마다 갱신
          <input type="number" min={sched.min_interval_minutes || 1} max={sched.max_interval_minutes || 1440}
            value={sched.interval_minutes}
            disabled={!isAdmin || schedBusy}
            onChange={e => setSched(prev => ({ ...prev, interval_minutes: e.target.value }))}
            style={{ width: 70, padding: "6px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14 }} />
          분
        </label>
        <button onClick={saveScheduler} disabled={!isAdmin || schedBusy}
          style={{ padding: "6px 10px", borderRadius: 4, border: "none", background: "var(--accent)", color: "#fff", fontSize: 14, cursor: isAdmin && !schedBusy ? "pointer" : "not-allowed", opacity: isAdmin ? 1 : 0.55 }}>
          저장
        </button>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginBottom: 14, fontSize: 14, color: "var(--text-secondary)" }}>
        <span>최근 스캔 {fmtTime(status.finished_at || status.started_at)}</span>
        <span>스캔 랏 {status.lots_scanned ?? 0} / 갱신 {status.lots_updated ?? 0}</span>
        <span>watch {status.watches_checked ?? 0} / 알림 {status.notify_count ?? 0}</span>
        <span>상태 {status.running ? "실행 중" : (status.ok === false ? "오류" : "대기")} / 메일 {status.mail_count ?? 0}</span>
        <span>자동 갱신 {sched.enabled === false ? "꺼짐" : `${sched.interval_minutes ?? 30}분마다`}</span>
      </div>
      {status.last_error && <div style={{ marginBottom: 10, fontSize: 14, color: "var(--danger)" }}>{status.last_error}</div>}
      {schedMsg && <div style={{ marginBottom: 12, fontSize: 14, color: schedMsg.includes("완료") ? "var(--ok)" : "var(--danger)" }}>{schedMsg}</div>}
      <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>LOT_WF 현재 위치 파일</div>
      <div style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 8 }}>
        <input value={lotProgressFilter} onChange={e => setLotProgressFilter(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter") loadLotProgressTable(e.currentTarget.value); }}
          placeholder="lot_id 또는 root_lot_id prefix"
          style={{ flex: 1, padding: "6px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14 }} />
        <button onClick={() => loadLotProgressTable(lotProgressFilter)} disabled={lotProgressBusy}
          style={{ padding: "6px 10px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14, cursor: lotProgressBusy ? "not-allowed" : "pointer" }}>
          조회
        </button>
        <button onClick={refreshLotProgressNow} disabled={!isAdmin || lotProgressBusy}
          style={{ padding: "6px 10px", borderRadius: 4, border: "none", background: "var(--accent)", color: "#fff", fontSize: 14, cursor: isAdmin && !lotProgressBusy ? "pointer" : "not-allowed", opacity: isAdmin ? 1 : 0.55 }}>
          파일 갱신
        </button>
      </div>
      <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 6 }}>
        기준 {fmtTime(lotProgressMeta.generated_at)} · 표시 {lotProgressMeta.count ?? lotProgressRows.length} / 전체 {lotProgressMeta.total ?? "-"}
      </div>
      <div style={{ maxHeight: 220, overflow: "auto", border: "1px solid var(--border)", borderRadius: 6, marginBottom: 14 }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
          <thead>
            <tr>
              {["product", "root_lot_id", "wafer_id", "lot_id", "step_id", "func_step", "updated_at"].map(h => (
                <th key={h} style={{ position: "sticky", top: 0, textAlign: "left", padding: "7px 8px", background: "var(--bg-tertiary)", borderBottom: "1px solid var(--border)", fontFamily: "monospace" }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {lotProgressRows.length ? lotProgressRows.map((r, i) => (
              <tr key={`${r.root_lot_id}-${r.wafer_id}-${r.lot_id}-${i}`}>
                <td style={{ padding: "6px 8px", borderBottom: "1px solid var(--border)", fontFamily: "monospace" }}>{r.product || "-"}</td>
                <td style={{ padding: "6px 8px", borderBottom: "1px solid var(--border)", fontFamily: "monospace" }}>{r.root_lot_id || "-"}</td>
                <td style={{ padding: "6px 8px", borderBottom: "1px solid var(--border)", fontFamily: "monospace" }}>{r.wafer_id || "-"}</td>
                <td style={{ padding: "6px 8px", borderBottom: "1px solid var(--border)", fontFamily: "monospace" }}>{r.lot_id || "-"}</td>
                <td style={{ padding: "6px 8px", borderBottom: "1px solid var(--border)", fontFamily: "monospace" }}>{r.step_id || "-"}</td>
                <td style={{ padding: "6px 8px", borderBottom: "1px solid var(--border)", fontFamily: "monospace" }}>{r.func_step || r.function_step || "-"}</td>
                <td style={{ padding: "6px 8px", borderBottom: "1px solid var(--border)", fontFamily: "monospace", whiteSpace: "nowrap" }}>{fmtTime(r.time || r.updated_at || r.tkout_time || r.tkin_time)}</td>
              </tr>
            )) : (
              <tr><td colSpan={7} style={{ padding: 12, color: "var(--text-secondary)", textAlign: "center" }}>{lotProgressBusy ? "조회 중..." : "표시할 행 없음"}</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>카테고리 관리</div>
      {!isAdmin && <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 8 }}>편집은 관리자만 가능합니다.</div>}
      <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 10 }}>
        {cats.map(c => (
          <div key={c.name} style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <input type="color" value={c.color || "#E25822"} disabled={!isAdmin}
              onChange={e => updColor(c.name, e.target.value)}
              style={{ width: 28, height: 26, border: "1px solid var(--border)", borderRadius: 4, background: "transparent", cursor: isAdmin ? "pointer" : "default" }} />
            <span style={{ flex: 1, fontSize: 14 }}>{c.name}</span>
            {isAdmin && <span onClick={() => remove(c.name)} style={{ cursor: "pointer", color: "var(--danger)", fontSize: 14 }}>삭제</span>}
          </div>
        ))}
      </div>
      {isAdmin && (
        <div style={{ display: "flex", gap: 6 }}>
          <input type="color" value={color} onChange={e => setColor(e.target.value)}
            style={{ width: 28, height: 30, border: "1px solid var(--border)", borderRadius: 4, background: "transparent" }} />
          <input value={name} onChange={e => setName(e.target.value)} placeholder="새 카테고리 이름"
            style={{ flex: 1, padding: "6px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14 }} />
          <button onClick={add} style={{ padding: "6px 12px", borderRadius: 4, border: "none", background: "var(--accent)", color: "#fff", fontSize: 14, cursor: "pointer" }}>추가</button>
        </div>
      )}
      {msg && <div style={{ marginTop: 8, fontSize: 14, color: msg === "저장 완료" ? "var(--ok)" : "var(--danger)" }}>{msg}</div>}
      <div style={{ marginTop: 16, padding: 10, background: "var(--bg-primary)", borderRadius: 6, fontSize: 14, color: "var(--text-secondary)", lineHeight: 1.6 }}>
        • 카테고리 색상은 이슈 리스트/간트 차트 bar/카테고리 chip 에 반영됩니다.<br/>
        • 일반 유저는 현재 카테고리 목록만 조회 가능.
      </div>
    </div>
  );
}
