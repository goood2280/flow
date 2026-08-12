import { useState, useEffect, useRef, useCallback } from "react";
import Loading from "../components/Loading";
import PageGear from "../components/PageGear";
import Modal from "../components/Modal";
import { toast } from "../components/Toast";
import { Button, Card, EmptyState, Filter, Pill, TabStrip, TableWrap, Tbl } from "../components/UXKit";
import FlowiPromptBox from "../components/FlowiPromptBox";
import { authSrc, sf as apiSf } from "../lib/api";
import { sanitizeHtml } from "../lib/sanitizeHtml";
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

/* v9.5.13: ET Tracker 측정이력 — lot 행의 et_history(스캔 누적)를 step_id 별로 묶어 표시. */
function etHistoryStepGroups(history) {
  const rows = Array.isArray(history) ? history.filter(h => h && typeof h === "object") : [];
  const grouped = new Map();
  rows.forEach(h => {
    const step = String(h.step_id || "-");
    const g = grouped.get(step) || { step_id: step, function_step: "", dc_layer: "", pgms: [], items: [], last: "" };
    const func = h.function_step || h.func_step || "";
    if (func && !g.function_step) g.function_step = func;
    if (h.dc_layer && !g.dc_layer) g.dc_layer = String(h.dc_layer);
    const pgm = String(h.pgm || "");
    if (pgm && !g.pgms.includes(pgm)) g.pgms.push(pgm);
    g.items.push(h);
    const t = String(h.time || "");
    if (t > g.last) g.last = t;
    grouped.set(step, g);
  });
  return Array.from(grouped.values()).sort((a, b) => String(b.last).localeCompare(String(a.last)));
}

function etHistoryTitle(history) {
  const rows = Array.isArray(history) ? history : [];
  return rows
    .slice()
    .sort((a, b) => String(b?.time || "").localeCompare(String(a?.time || "")))
    .map(h => `${h?.dc_layer ? `[${h.dc_layer}] ` : ""}${h?.step_id || "-"}${h?.function_step ? `(${h.function_step})` : ""} · ${h?.pgm || "-"} · ${String(h?.time || "").slice(0, 19)}`)
    .join("\n");
}

function renderEtHistoryCell(lot) {
  const groups = etHistoryStepGroups(lot?.et_history);
  if (!groups.length) {
    // v9.5.87: 조회 실패를 "측정 이력 없음" 으로 숨기지 않는다 — 사유를 보여준다.
    const err = String(lot?.last_scan_error || "").trim();
    if (err) {
      return <span title={err} style={{ color: "var(--danger)", fontSize: 14 }}>조회 실패 · {err}</span>;
    }
    return <span style={{ color: "var(--text-secondary)", fontSize: 14 }}>{lot?.last_checked_at ? "측정 이력 없음" : "스캔 전"}</span>;
  }
  const shown = groups.slice(0, 6);
  return (
    <div title={etHistoryTitle(lot?.et_history)} style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 240, whiteSpace: "normal", lineHeight: 1.4 }}>
      {shown.map((g, i) => (
        <div key={`${g.step_id}-${i}`} style={{ display: "flex", gap: 6, alignItems: "baseline", flexWrap: "wrap" }}>
          <span style={{ fontFamily: "monospace", fontWeight: 800, color: "var(--accent)" }}>
            {g.dc_layer ? `${g.dc_layer} · ` : ""}{g.function_step ? `${g.step_id}(${g.function_step})` : g.step_id}
          </span>
          <span style={{ fontFamily: "monospace", color: "var(--text-primary)", fontWeight: 600 }}>{g.pgms.join(", ")}</span>
        </div>
      ))}
      {groups.length > shown.length && <span style={{ fontSize: 14, color: "var(--text-secondary)" }}>외 {groups.length - shown.length}개 step</span>}
    </div>
  );
}

/* v9.5.13: 이슈 등록 시 조회 버튼으로 확인하는 ET 측정이력 미리보기 — step_id(func) · PGM(pt) 나열. */
function renderEtPreviewCell(lot, et, fetched) {
  const rows = etStepSummaries(lot, Array.isArray(et) ? et : []);
  if (!rows.length) {
    return <span style={{ color: "var(--text-secondary)", fontSize: 14 }}>{fetched ? "측정 없음" : "조회 필요"}</span>;
  }
  const shown = rows.slice(0, 5);
  const title = rows.map(r => {
    const pgms = (r.seq_points || []).map(p => `${p.seq}(${Number(p.pt_count || 0)}pt)`).join(", ");
    return `${r.display_label || r.step_id || "-"} ${pgms}`;
  }).join("\n");
  return (
    <div title={title} style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 220, whiteSpace: "normal", lineHeight: 1.4 }}>
      {shown.map((r, i) => (
        <div key={`${r.step_id}-${i}`} style={{ display: "flex", gap: 6, alignItems: "baseline", flexWrap: "wrap" }}>
          <span style={{ fontFamily: "monospace", fontWeight: 800, color: "var(--accent)", fontSize: 14 }}>{r.display_label || r.step_id || "-"}</span>
          <span style={{ fontFamily: "monospace", color: "var(--text-primary)", fontWeight: 600, fontSize: 14 }}>
            {(r.seq_points || []).map(p => `${p.seq}(${Number(p.pt_count || 0)}pt)`).join(", ")}
          </span>
        </div>
      ))}
      {rows.length > shown.length && <span style={{ fontSize: 14, color: "var(--text-secondary)" }}>외 {rows.length - shown.length}개 step</span>}
    </div>
  );
}

function isMonitorCategory(category, roleNames = {}) {
  const monitorName = String(roleNames?.monitor || "Monitor").trim().toLowerCase();
  return String(category || "").trim().toLowerCase() === monitorName;
}

function trackerCategorySource(category, roleNames = {}, cats = []) {
  const c = String(category || "").trim().toLowerCase();
  if (c === String(roleNames?.monitor || "Monitor").trim().toLowerCase()) return "fab";
  if (c === String(roleNames?.analysis || "Analysis").trim().toLowerCase()) return "et";
  const cat = (Array.isArray(cats) ? cats : []).find(x => String(x?.name || "").trim().toLowerCase() === c);
  const src = String(cat?.source || "").trim().toLowerCase();
  if (["fab", "et", "both"].includes(src)) return src;
  // v9.5.87: 미지정(auto)/미등록 카테고리는 ET — 백엔드 _category_source 와 같은 규칙.
  //   Monitor 만 FAB 이고, ET 추적 탭의 나머지는 전부 ET DB 추적이다.
  return "et";
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
    const hasBaseValue = ["product", "monitor_prod", "root_lot_id", "lot_id", "wafer_id", "purpose", "comment"].some(k => String(base?.[k] || "").trim());
    if (!hasBaseValue) return;
    const wafers = Array.isArray(base.wafer_ids) ? base.wafer_ids : (Array.isArray(_summary?.wafer_ids) ? _summary.wafer_ids : []);
    // Creation only needs user input. Sending preview/cache rows made the JSON
    // payload and the persisted issues file unnecessarily large and slow.
    out.push({
      product: base.product || base.monitor_prod || "",
      monitor_prod: base.monitor_prod || base.product || "",
      root_lot_id: base.root_lot_id || "",
      lot_id: base.lot_id || "",
      wafer_id: base.wafer_id || "",
      ...(wafers.length ? { wafer_ids: wafers } : {}),
      purpose: base.purpose || "",
      comment: base.comment || "",
    });
  });
  return out;
}

// v8.8.3: description_html 에 박힌 `/api/tracker/image?name=...` URL 에 세션 토큰(t=) 을
// 쿼리로 덧붙여서 dangerouslySetInnerHTML 로 렌더된 <img> 도 인증을 통과하도록 한다.
// (인폼로그에서 authSrc 로 해결한 패턴을 tracker 에 동일 적용.)
//
// ⚠️ 경로 앞의 따옴표 경계를 반드시 요구한다. 예전 패턴은 문자열 아무 데나 있는
//    `/api/tracker/image?name=` 를 잡아서, 다른 사용자가 설명에
//    `<img src="https://evil.example/api/tracker/image?name=x">` 를 심으면
//    **세션 토큰이 외부 도메인 쿼리로 붙어 나갔다.** 이제 속성값이 그 경로로
//    시작할 때만(= 동일 출처 상대경로) 토큰을 붙인다.
function withTrackerImageAuth(html) {
  if (!html || typeof html !== "string") return html;
  return html.replace(
    /(["'])(\/api\/tracker\/image\?name=[^"'&\s>]+)/g,
    (_m, quote, url) => quote + authSrc(url),
  );
}

// description_html 은 **다른 사용자가 쓴 HTML** 이다. innerHTML 로 넣기 전 마지막
// 관문에서 살균한다 (allowlist — lib/sanitizeHtml.js).
function trackerDescHtml(html) {
  return sanitizeHtml(withTrackerImageAuth(html));
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
    if (!ref.current) return;
    // 편집기에 남의 이슈 설명을 불러오는 지점이다. 여기는 **문서에 붙어 있는**
    // contentEditable 이라 살균 없이 넣으면 <img onerror> 가 곧바로 실행된다
    // (보기 화면보다 위험하다 — 이슈를 고치러 들어가는 건 보통 담당자·관리자다).
    // (살균 호출은 대입문에 인라인으로 둔다 — 싱크 한 줄만 봐도 안전한지
    //  드러나야 하고, tests/test_frontend_html_sanitize.py 정적 가드도 그 형태를 본다.)
    if (ref.current.innerHTML !== value) {
      ref.current.innerHTML = sanitizeHtml(value);
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
    if (root) params.set("root_lot_id", root);
    else if (lid) params.set("lot_id", lid);
    if (lot.wafer_id) params.set("wafer_id", String(lot.wafer_id));
    setBusyRow(idx);
    sf(API + "/lot-step?" + params.toString())
      .then(d => setStepData(prev => ({ ...prev, [idx]: d.snapshot || {} })))
      .catch(() => {})
      .finally(() => setBusyRow(null));
  }, [product, category]);
  const isEtSource = trackerCategorySource(category, roleNames, cats) === "et";
  // 준비된 제품 ET history에서 root lot 결과만 읽는다. Tracker는 원본 ET DB를
  // 다시 스캔하지 않으며, full=true면 history cache 전 구간을 다시 반영한다.
  const fetchAllSteps = useCallback((opts) => {
    const full = opts === true || opts?.full === true;
    if (!readOnly || !issueId || batchBusy) return;
    setBatchBusy(true);
    setBatchDone(0);
    if (isEtSource) {
      // v9.5.13: ET 이슈는 lot-check-all 대신 이력 스캔 API — 새로 추가된 측정만 et_history 에 누적.
      sf(API + "/et-scan/run-issue", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ issue_id: issueId, full }),
      }).then(d => {
        const serverLots = Array.isArray(d?.lots) ? d.lots : null;
        if (serverLots) setLots(() => serverLots);
        setBatchDone(serverLots ? serverLots.length : 0);
        const added = Number(d?.new_entries || 0);
        // v9.5.87: 조회 실패는 "측정 없음" 과 다르다 — 이유를 그대로 띄운다.
        if (d?.scan_error) toast.error(`ET history 조회 실패 · ${d.scan_error}`);
        else if (added > 0) toast.info(`신규 ET 측정 ${added}건이 이력에 추가되었습니다.`);
        else toast.info("스캔 완료 — 새로 감지된 ET 측정이 없습니다.");
      }).catch(e => {
        toast.error(e?.message || "ET 이력 스캔 실패");
        setBatchDone(0);
      }).finally(() => setBatchBusy(false));
      return;
    }
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
  }, [readOnly, issueId, batchBusy, setLots, isEtSource]);
  useEffect(() => {
    if (!readOnly || !issueId || !lots.length || batchBusy) return;
    // ET 이슈는 열 때마다 자동 조회하지 않는다 — 저장된 et_history 를 보여주고,
    // 갱신은 스케줄 조회(톱니바퀴 시각 지정) 또는 '즉시 반영' 버튼으로만 한다.
    if (isEtSource) return;
    const key = `${issueId}:${String(category || "").trim().toLowerCase()}`;
    if (autoFetchRef.current === key) return;
    autoFetchRef.current = key;
    fetchAllSteps();
  }, [readOnly, issueId, category, lots.length, batchBusy, fetchAllSteps, isEtSource]);
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
        // v9.5.13: ET 이슈 붙여넣기는 root_lot_id 기준 (5자리가 아니면 서버가 lot_id 로 해석).
        return {
          product: (parts[0] || "").trim(),
          monitor_prod: (parts[0] || "").trim(),
          root_lot_id: (parts[1] || "").trim(),
          lot_id: "",
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

  // v9.5.13: ET 이슈 상세는 측정이력 테이블로 교체 —
  //   root_lot_id / wafer_id / 목적 / 참고 / 측정이력(step_id · PGM(pt)) / 작성자 / 날짜.
  //   watch·ET측정 컬럼 대신 스케줄 스캔이 누적한 et_history 를 그대로 보여준다.
  if (readOnly && categorySource === "et") {
    const etCellStyle = { padding: "7px 9px", borderBottom: "1px solid var(--border)", fontSize: 14, verticalAlign: "top" };
    const etHeadStyle = { textAlign: "left", padding: "8px 10px", background: "var(--bg-tertiary)", borderBottom: "1px solid var(--border)", borderRight: "1px solid var(--border)", fontSize: 14, color: "var(--text-secondary)", fontWeight: 600, fontFamily: "monospace", whiteSpace: "nowrap", position: "sticky", top: 0, zIndex: 1 };
    const lastChecked = lots.reduce((acc, l) => (String(l?.last_checked_at || "") > acc ? String(l.last_checked_at) : acc), "");
    return (
      <div>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6, gap: 8, flexWrap: "wrap" }}>
          <span style={{ fontSize: 14, fontWeight: 600 }}>LOT LIST ({lots.length})</span>
          <div style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 14, color: "var(--text-secondary)", fontFamily: "monospace" }}>
              최근 스캔 {lastChecked ? lastChecked.replace("T", " ").slice(0, 16) : "-"}
            </span>
            <button onClick={fetchAllSteps} disabled={!issueId || batchBusy || lots.length === 0}
              title="준비된 ET history에서 이 root lot의 신규 측정만 가져옵니다"
              style={{ padding: "6px 12px", borderRadius: 6, border: "1px solid var(--accent)", background: batchBusy ? "var(--bg-tertiary)" : "transparent", color: "var(--accent)", fontSize: 14, fontWeight: 700, cursor: !issueId || batchBusy || lots.length === 0 ? "not-allowed" : "pointer", opacity: !issueId || batchBusy || lots.length === 0 ? 0.6 : 1 }}>
              {batchBusy ? "반영 중..." : "측정이력 반영"}
            </button>
            {/* v9.5.87: 증분 스캔이 기본이라, 과거 구간까지 다시 읽고 싶을 때 쓰는 탈출구. */}
            <button onClick={() => fetchAllSteps({ full: true })} disabled={!issueId || batchBusy || lots.length === 0}
              title="원본 DB가 아니라 준비된 ET history cache 전 구간을 다시 반영합니다"
              style={{ padding: "6px 10px", borderRadius: 6, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", fontSize: 14, cursor: !issueId || batchBusy || lots.length === 0 ? "not-allowed" : "pointer", opacity: !issueId || batchBusy || lots.length === 0 ? 0.6 : 1 }}>
              전체 이력 반영
            </button>
          </div>
        </div>
        <TableWrap maxHeight={340}>
          <Tbl style={{ borderCollapse: "separate", borderSpacing: 0 }}>
            <thead><tr>
              {["root_lot_id", "wafer_id", "목적", "참고", "측정이력 (DC Layer · step_id · PGM(pt))", "작성자", "날짜"].map(h => (
                <th key={h} style={etHeadStyle}>{h}</th>
              ))}
            </tr></thead>
            <tbody>
              {lots.map((l, i) => (
                <tr key={i}>
                  <td style={{ ...etCellStyle, fontFamily: "monospace", fontWeight: 700 }}>{l.root_lot_id || l.lot_id || "-"}</td>
                  <td style={{ ...etCellStyle, fontFamily: "monospace" }} title={Array.isArray(l.wafer_ids) ? l.wafer_ids.join(", ") : ""}>{l.wafer_id || l.wafer_label || "-"}</td>
                  <td style={etCellStyle}>{l.purpose || ""}</td>
                  <td style={etCellStyle}>{l.comment || ""}</td>
                  <td style={etCellStyle}>{renderEtHistoryCell(l)}</td>
                  <td style={{ ...etCellStyle, color: "var(--text-secondary)" }}>{l.username || ""}</td>
                  <td style={{ ...etCellStyle, color: "var(--text-secondary)", fontFamily: "monospace", whiteSpace: "nowrap" }}>{(l.added || "").slice(0, 10)}</td>
                </tr>
              ))}
              {lots.length === 0 && <tr><td colSpan={7} style={{ padding: 16, textAlign: "center", color: "var(--text-secondary)", fontSize: 14 }}>Lot/Wafer 데이터 없음</td></tr>}
            </tbody>
          </Tbl>
        </TableWrap>
      </div>
    );
  }

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
  // v9.5.13: ET 이슈 입력 테이블 — root_lot_id/wafer_id 로 등록하고 step 대신 측정이력을 미리 조회.
  const showEtPreviewColumn = !readOnly && !monitorMode;
  const baseHeaders = createMonitorMode
    ? ["product", "lot_id", "purpose", "comment", "wafer_ids", "step_id(func_step)"]
    : monitorMode
    ? ["product", "lot_id", "wafer_ids", "purpose", "comment"]
    : ["product", "root_lot_id", "wafer_id", "purpose", "comment"];
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
        <span style={{ fontSize: 14, fontWeight: 600 }}>LOT LIST ({lots.length})</span>
        {readOnly ? (
          <div style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 14, color: "var(--text-secondary)", fontFamily: "monospace" }}>{batchBusy ? `0/${lots.length} 완료` : `${batchDone}/${lots.length} 완료`}</span>
            <button onClick={fetchAllSteps} disabled={!issueId || batchBusy || lots.length === 0}
              style={{ padding: "6px 12px", borderRadius: 6, border: "1px solid var(--accent)", background: batchBusy ? "var(--bg-tertiary)" : "transparent", color: "var(--accent)", fontSize: 14, fontWeight: 700, cursor: !issueId || batchBusy || lots.length === 0 ? "not-allowed" : "pointer", opacity: !issueId || batchBusy || lots.length === 0 ? 0.6 : 1 }}>
              {batchBusy ? "조회 중..." : "전체 조회"}
            </button>
          </div>
        ) : <span style={{ fontSize: 14, color: "var(--text-secondary)" }}>{createMonitorMode ? "product와 lot_id 입력 후 wafer/step 자동 조회" : "Excel TSV 붙여넣기 지원 · product / root_lot_id / wafer_id / purpose / comment 순서"}</span>}
      </div>
      {!readOnly && (
        <>
          <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 4 }}>
            {createMonitorMode ? "Monitor 이슈는 product와 lot_id를 기준으로 cache에서 wafer 매수, wafer 번호, step_id, func_step을 불러옵니다. purpose와 comment는 별도 관리 컬럼입니다." : "product 선택 후 root_lot_id 를 입력하세요. wafer_id 는 all / 1,2 / 1~10 형식 모두 허용, 조회를 누르면 ET 측정이력을 미리 확인합니다."}
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
            {showEtPreviewColumn && <th style={{ textAlign: "left", padding: "8px 10px", background: "var(--bg-tertiary)", borderBottom: "1px solid var(--border)", borderRight: "1px solid var(--border)", fontSize: 14, color: "var(--text-secondary)", fontWeight: 600, fontFamily: "monospace", whiteSpace: "nowrap", position: "sticky", top: 0, zIndex: 1 }}>측정이력 (step_id · PGM(pt))</th>}
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
              const lotValue = createMonitorMode ? (l.lot_id || l.root_lot_id || "") : (l.root_lot_id || l.lot_id || "");
              const etFetched = stepData?.[i] !== undefined;
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
                  // ET 이슈 입력은 길이와 무관하게 root_lot_id 기준으로 조회한다.
                  renderLotInput({
                    idx: i,
                    row: l,
                    value: lotValue,
                    disabled: !rowProduct,
                    placeholder: rowProduct ? "root_lot_id 입력/검색" : "product 먼저 선택",
                    onValueChange: v => {
                      updateRow(i, { root_lot_id: v, lot_id: "" });
                      if (rowProduct) loadLotOptions(i, { ...l, root_lot_id: v, lot_id: "" }, v);
                    },
                    onPick: c => {
                      const v = String(c?.value || "").trim();
                      updateRow(i, { root_lot_id: v, lot_id: "" });
                      fetchStep(i, { ...l, root_lot_id: v, lot_id: "" });
                    },
                    onBlur: v => { if (v) fetchStep(i, { ...l, root_lot_id: v, lot_id: "" }); },
                  })
                )}</td>
                <td style={readOnly ? { ...cellStyle, minWidth: monitorMode ? 170 : undefined, fontFamily: monitorMode ? "monospace" : undefined, color: monitorMode ? "var(--accent)" : undefined, fontWeight: monitorMode ? 800 : undefined } : { ...sheetCell, width: 100 }}
                    title={monitorMode ? (Array.isArray(l.wafer_ids) ? l.wafer_ids.join(", ") : "") : ""}>
                  {readOnly ? (monitorMode ? waferSummaryText(l) : l.wafer_id) : <input value={l.wafer_id || ""} onChange={e => updateCell(i, "wafer_id", e.target.value)} onBlur={() => { if (l.root_lot_id || l.lot_id) fetchStep(i, l); }} style={sheetInput} placeholder="all / 1,2 / 1~10" />}
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
                {showEtPreviewColumn && <td style={{ ...cellStyle, minWidth: 250, verticalAlign: "top" }}>
                  <div style={{ display: "flex", alignItems: "flex-start", gap: 6 }}>
                    {busyRow === i
                      ? <span style={{ fontSize: 14, color: "var(--text-secondary)" }}>…</span>
                      : renderEtPreviewCell(l, et, etFetched)}
                    <button onClick={() => fetchStep(i, l)} disabled={busyRow === i || !(l.root_lot_id || l.lot_id)}
                      style={{ marginLeft: "auto", padding: "3px 7px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-secondary)", fontSize: 14, cursor: busyRow === i || !(l.root_lot_id || l.lot_id) ? "not-allowed" : "pointer", flexShrink: 0 }}>
                      조회
                    </button>
                  </div>
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
                <td colSpan={baseHeaders.length + (showEtPreviewColumn ? 1 : 0)} style={{ ...cellStyle, color: "var(--text-secondary)", fontSize: 14, background: "var(--bg-tertiary)", opacity: 0.7, fontFamily: "monospace" }}>
                  {lots.length === 0 ? (createMonitorMode ? "product / lot_id / purpose / comment 입력 행 추가" : "Excel 붙여넣기 (product \\t root_lot_id \\t wafer_id \\t purpose \\t comment) 또는 + 로 행 추가") : "(빈 행)"}
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

/* v9.5.84: 이슈별 ET Tracker 메일 수신 그룹.
   메일 on/off·스캔 시각은 톱니바퀴에서 일괄 관리하고, '누구에게 보낼지'만 이슈마다
   따로 고른다. 비워두면 톱니바퀴의 기본 수신 그룹으로 발송한다 (작성자·lot 추가자는
   어느 경우든 항상 수신). 저장 위치는 기존 issue.mail_watch.mail_group_ids. */
function IssueMailGroups({ issue, canEdit, onSaved }) {
  const issueId = issue?.id || "";
  const [groups, setGroups] = useState([]);
  const [picked, setPicked] = useState([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const savedIds = (issue?.mail_watch?.mail_group_ids || []).map(String);
  useEffect(() => {
    sf("/api/mail-groups/list").then(d => setGroups(Array.isArray(d?.groups) ? d.groups : [])).catch(() => setGroups([]));
  }, []);
  useEffect(() => { setPicked(savedIds); setMsg(""); }, [issueId, savedIds.join(",")]);
  const dirty = picked.slice().sort().join(",") !== savedIds.slice().sort().join(",");
  const toggle = (gid) => setPicked(prev => prev.includes(gid) ? prev.filter(x => x !== gid) : [...prev, gid]);
  const save = () => {
    if (!canEdit || busy) return;
    setBusy(true);
    setMsg("");
    sf(API + "/issue-mail", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        issue_id: issueId,
        // 기존 mail_watch.enabled 는 그대로 유지 — 여기서는 수신처만 바꾼다.
        mail: !!issue?.mail_watch?.enabled,
        mail_group_ids: picked,
      }),
    }).then(() => { setMsg("수신 그룹 저장 완료"); if (onSaved) onSaved(); })
      .catch(e => setMsg(e.message)).finally(() => setBusy(false));
  };
  return (
    <section style={connectedPanelSection}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
        <div style={{ ...connectedSectionTitle, marginBottom: 0 }}>메일 수신 그룹</div>
        <span style={{ fontSize: 14, color: "var(--text-secondary)" }}>
          {picked.length ? `이 이슈 전용 ${picked.length}개 그룹` : "톱니바퀴 기본 수신 그룹 사용"}
        </span>
        {canEdit && dirty && (
          <button onClick={save} disabled={busy}
            style={{ marginLeft: "auto", padding: "5px 12px", borderRadius: 6, border: "none", background: "var(--accent)", color: "#fff", fontSize: 14, fontWeight: 600, cursor: busy ? "not-allowed" : "pointer" }}>
            {busy ? "저장 중..." : "저장"}
          </button>
        )}
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
        {groups.length === 0 && <span style={{ fontSize: 14, color: "var(--text-secondary)" }}>등록된 수신처 그룹 없음</span>}
        {groups.map(g => {
          const gid = String(g.id);
          const on = picked.includes(gid);
          return (
            <label key={gid} title={canEdit ? "" : "이슈 작성자 또는 관리자만 변경할 수 있습니다"}
              style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 14, padding: "3px 8px", borderRadius: 999, border: "1px solid " + (on ? "var(--accent)" : "var(--border)"), background: on ? "var(--accent-glow)" : "transparent", cursor: canEdit ? "pointer" : "default", color: on ? "var(--accent)" : "var(--text-secondary)" }}>
              <input type="checkbox" checked={on} disabled={!canEdit || busy} onChange={() => toggle(gid)} style={{ accentColor: "var(--accent)" }} />
              {g.name}
              <span style={{ color: "var(--text-secondary)" }}>{(g.members?.length || 0) + (g.extra_emails?.length || 0)}</span>
            </label>
          );
        })}
      </div>
      {msg && <div style={{ marginTop: 6, fontSize: 14, color: msg.includes("완료") ? "var(--ok)" : "var(--danger)" }}>{msg}</div>}
    </section>
  );
}

/* ─── Issue Form ─── */
function IssueForm({ onSubmit, onClose, user, roleNames }) {
  const [title, setTitle] = useState(""); const [desc, setDesc] = useState("");
  const [lots, setLots] = useState([]); const [links, setLinks] = useState([""]);
  const [cats, setCats] = useState([]);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const submitLockRef = useRef(false);
  const requestIdRef = useRef(
    typeof crypto !== "undefined" && crypto.randomUUID
      ? crypto.randomUUID()
      : `tracker-${Date.now()}-${Math.random().toString(36).slice(2)}`,
  );
  useEffect(() => { sf(API + "/categories").then(d => setCats((d.categories || []).map(c => typeof c === "string" ? { name: c, color: "#E25822" } : c))).catch(() => { }); }, []);
  // v9.5.13: 우선순위/카테고리/그룹 가시성 선택 제거 — ET 추적탭 이슈는 ET(source=et) 카테고리로 자동 등록.
  const etCategory = (() => {
    const list = Array.isArray(cats) ? cats : [];
    const bySource = list.find(c => String(c?.source || "").trim().toLowerCase() === "et");
    if (bySource?.name) return bySource.name;
    const byName = list.find(c => /analysis/i.test(String(c?.name || "")));
    return byName?.name || "Analysis";
  })();
  const S = { width: "100%", padding: "8px 12px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14, outline: "none" };
  return (
    <div style={{ background: "var(--bg-secondary)", borderRadius: 10, border: "1px solid var(--border)", padding: 20, marginBottom: 20 }}>
      <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 12 }}>새 이슈</div>
      <input value={title} onChange={e => setTitle(e.target.value)} placeholder="제목" style={{ ...S, marginBottom: 8 }} />
      <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 4 }}>설명 (Ctrl+V 로 이미지 붙여넣기)</div>
      <DescEditor value={desc} onChange={setDesc} placeholder="설명 입력... Ctrl+V 로 이미지 붙여넣기" />
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
        <LotTable lots={lots} setLots={setLots} readOnly={false} category={etCategory} roleNames={roleNames} cats={cats} />
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        <button disabled={submitting} onClick={async () => {
          if (!title.trim() || submitting || submitLockRef.current) return;
          submitLockRef.current = true;
          setSubmitting(true);
          setSubmitError("");
          try {
            await onSubmit({ title, description: desc, priority: "normal", category: etCategory, images: [], lots: expandLotsForSubmit(lots), links: links.filter(l => l.trim()), group_ids: [], client_request_id: requestIdRef.current });
          } catch (e) {
            setSubmitError(e?.message || "이슈 등록에 실패했습니다");
          } finally {
            submitLockRef.current = false;
            setSubmitting(false);
          }
        }}
          style={{ padding: "8px 20px", borderRadius: 6, border: "none", background: "var(--accent)", color: "#fff", fontWeight: 600, cursor: submitting ? "wait" : "pointer", opacity: submitting ? 0.65 : 1 }}>
          {submitting ? "등록 중..." : "생성"}
        </button>
        <button disabled={submitting} onClick={onClose} style={{ padding: "8px 16px", borderRadius: 6, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", cursor: submitting ? "not-allowed" : "pointer", opacity: submitting ? 0.6 : 1 }}>취소</button>
      </div>
      {submitError && <div style={{ marginTop: 8, color: "var(--danger)", fontSize: 13 }}>{submitError}</div>}
    </div>);
}

/* ─── Gantt Chart ─── */
// v9.5.84: 날짜 칸/이슈 칸 폭 고정 — 바를 단일 div 로 그리기 위한 픽셀 기준.
const GANTT_CELL_W = 26;
// v9.5.84: 이슈명이 잘려서 안 보인다는 피드백 — 이슈 칸을 220 → 420 으로 넓혔다.
const GANTT_LABEL_W = 420;

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
    // v9.5.13: 검색 확장 — 제목/담당자/카테고리 + 랏리스트 root_lot_id + 본문 내용.
    const rootIds = Array.isArray(iss.root_lot_ids) ? iss.root_lot_ids.join(" ").toLowerCase() : "";
    const descText = String(iss.desc_text || iss.summary || "").toLowerCase();
    return (iss.title || "").toLowerCase().includes(q)
      || (iss.username || "").toLowerCase().includes(q)
      || (iss.category || "").toLowerCase().includes(q)
      || rootIds.includes(q)
      || descText.includes(q);
  });
  const prioColor = { critical: "var(--danger)", high: "var(--brand)", normal: "var(--info)", low: "var(--muted)" };
  const prevM = () => { if (month === 0) { setMonth(11); setYear(y => y - 1); } else setMonth(m => m - 1); };
  const nextM = () => { if (month === 11) { setMonth(0); setYear(y => y + 1); } else setMonth(m => m + 1); };
  return (<div>
    <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12, flexWrap: "wrap" }}>
      <button onClick={prevM} style={{ background: "none", border: "1px solid var(--border)", borderRadius: 4, color: "var(--text-primary)", cursor: "pointer", padding: "2px 8px" }}>◀</button>
      <span style={{ fontSize: 14, fontWeight: 700, minWidth: 120, textAlign: "center" }}>{year}.{String(month + 1).padStart(2, "0")}</span>
      <button onClick={nextM} style={{ background: "none", border: "1px solid var(--border)", borderRadius: 4, color: "var(--text-primary)", cursor: "pointer", padding: "2px 8px" }}>▶</button>
      {/* v9.5.13: 제목 / 담당자 / root_lot_id / 본문 내용 부분일치 필터 */}
      <input value={gQuery} onChange={e => setGQuery(e.target.value)}
        placeholder="🔎 제목 · 담당자 · root_lot_id · 본문 검색"
        style={{ flex: 1, minWidth: 220, padding: "4px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14 }} />
      {gQuery && <span onClick={() => setGQuery("")} style={{ cursor: "pointer", color: "var(--danger)", fontSize: 14 }}>✕ 초기화</span>}
      <span style={{ fontSize: 14, color: "var(--text-secondary)" }}>{filtered.length}{gQuery ? ` / ${(issues || []).length}` : ""}건</span>
    </div>
    {filtered.length === 0 && <div style={{ padding: 40, textAlign: "center", color: "var(--text-secondary)" }}>{gQuery ? "매칭 이슈 없음" : "이슈 없음"}</div>}
    {filtered.length > 0 && (<>

    <div style={{ overflow: "auto" }}>
      {/* v9.5.84: 날짜 칸을 고정 폭(px)으로 고정하고, 바는 시작일 칸에서 완료일까지
          '한 개'의 div 로 그린다. 예전에는 날짜마다 조각 div 를 이어 붙였는데,
          표 폭이 늘어나면 칸 폭이 소수점(예: 31.47px)이 되어 조각마다 반올림이
          달라지면서 이음매가 올록볼록해 보였다. */}
      <table style={{ borderCollapse: "collapse", fontSize: 14, tableLayout: "fixed", width: GANTT_LABEL_W + daysInMonth * GANTT_CELL_W }}>
        <thead><tr>
          <th style={{ textAlign: "left", padding: "6px 8px", borderBottom: "2px solid var(--border)", background: "var(--bg-tertiary)", position: "sticky", left: 0, zIndex: 2, width: GANTT_LABEL_W, boxSizing: "border-box" }}>이슈</th>
          {days.map(d => <th key={d} style={{ padding: "4px 0", borderBottom: "2px solid var(--border)", background: "var(--bg-tertiary)", width: GANTT_CELL_W, boxSizing: "border-box", textAlign: "center", color: new Date(year, month, d).getDay() === 0 ? "var(--danger)" : "var(--text-secondary)" }}>{d}</th>)}
        </tr></thead>
        <tbody>{filtered.map(iss => {
          const created = new Date(iss.created || iss.timestamp); const ended = iss.closed_at ? new Date(iss.closed_at) : now;
          // 날짜만 비교 (시:분 무시). 이슈가 이번 달 밖에서 시작/종료하면 달 경계로 자른다.
          const dayOf = (dt) => new Date(dt.getFullYear(), dt.getMonth(), dt.getDate());
          const cStart = dayOf(created), cEnd = dayOf(ended);
          const monthStart = new Date(year, month, 1), monthEnd = new Date(year, month, daysInMonth);
          const barFrom = cStart <= monthStart ? 1 : cStart.getDate();
          const barTo = cEnd >= monthEnd ? daysInMonth : cEnd.getDate();
          const span = Math.max(0, barTo - barFrom + 1);
          const startInMonth = cStart >= monthStart && cStart <= monthEnd;
          const endInMonth = cEnd >= monthStart && cEnd <= monthEnd;
          const barColor = iss.category ? catColor(iss.category) : (prioColor[iss.priority] || "var(--info)");
          return (<tr key={iss.id}>
            <td style={{ padding: "4px 8px", borderBottom: "1px solid var(--border)", background: "var(--bg-secondary)", position: "sticky", left: 0, zIndex: 3, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", width: GANTT_LABEL_W, boxSizing: "border-box" }} title={`${iss.title} · 담당: ${iss.username || "-"}`}>
              <span onClick={() => onIssueClick && onIssueClick(iss.id)} style={{ fontWeight: 600, cursor: "pointer", color: "var(--accent)", textDecoration: "none" }} onMouseEnter={e=>e.currentTarget.style.textDecoration="underline"} onMouseLeave={e=>e.currentTarget.style.textDecoration="none"}>{iss.title}</span>
              {/* v8.8.13: 이슈 옆에 담당자 회색 표시 */}
              {iss.username && <span style={{ marginLeft: 6, fontSize: 14, color: "var(--text-secondary)", fontFamily: "monospace" }}>· {iss.username}</span>}
            </td>
            {days.map(d => (
              <td key={d} style={{ borderBottom: "1px solid var(--border)", borderRight: "1px solid var(--border)", padding: 0, height: 30, width: GANTT_CELL_W, boxSizing: "border-box", position: "relative", overflow: "visible" }}>
                {span > 0 && d === barFrom && <>
                  {/* 시작 칸 하나에서 완료 칸까지 이어지는 단일 바 — 중간 칸 경계선 위를 덮는다. */}
                  <div title={`${iss.title} (${iss.status})`}
                    style={{
                      position: "absolute", left: 0, top: 13, height: 14, zIndex: 1,
                      width: span * GANTT_CELL_W - 1,
                      background: barColor,
                      borderRadius: `${startInMonth ? 7 : 0}px ${endInMonth ? 7 : 0}px ${endInMonth ? 7 : 0}px ${startInMonth ? 7 : 0}px`,
                      opacity: iss.status === "closed" ? 0.5 : 0.85,
                    }} />
                  {startInMonth && (
                    <div style={{ position: "absolute", top: 0, left: 1, zIndex: 2, display: "flex", alignItems: "center", gap: 2, whiteSpace: "nowrap", pointerEvents: "none", lineHeight: 1 }} title={`시작 ${created.toISOString().slice(0, 10)}`}>
                      <span style={{ color: "#e11d48", fontSize: 10 }}>▼</span>
                      <span style={{ color: "#e11d48", fontSize: 9, fontWeight: 800, letterSpacing: 0.5 }}>START</span>
                    </div>
                  )}
                  {!!iss.closed_at && endInMonth && (
                    <span style={{ position: "absolute", top: -1, left: span * GANTT_CELL_W - 11, zIndex: 2, fontSize: 13, pointerEvents: "none" }} title={`완료 ${ended.toISOString().slice(0, 10)}`}>🏁</span>
                  )}
                </>}
              </td>
            ))}
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
  const isAdmin = user?.role === "admin";
  const statusColor = { in_progress: "var(--warn)", closed: "var(--ok)" };
  const prioColor = { critical: "var(--danger)", high: "var(--brand)", normal: "var(--info)", low: "var(--muted)" };
  // v8.1.5: look up category color from stored list; fall back to hash for orphans
  const [cats, setCats] = useState([]);
  useEffect(() => { sf(API + "/categories").then(d => setCats((d.categories || []).map(c => typeof c === "string" ? { name: c, color: "" } : c))).catch(() => { }); }, []);
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
    // Issue + LOT rows are one atomic write. The former create-then-/lots/bulk
    // flow rewrote the entire issue store twice and silently lost bulk errors.
    const body = { ...(data || {}), username: user?.username || "anonymous" };
    return sf(API + "/create", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then(d => {
      const iid = d.id || d.issue_id;
      setCreating(false);
      // Render the durable response immediately. LOT/history enrichment and a
      // canonical refresh can finish without holding the registration dialog.
      if (d.list_row) {
        setIssues(prev => [d.list_row, ...prev.filter(row => row.id !== d.list_row.id)]);
      }
      if (d.issue) {
        setSelected(d.issue);
        setEditMode(false);
        setReplyDrafts({});
      }
      window.setTimeout(() => {
        load();
        loadDetail(iid);
      }, d.postprocess_pending ? 800 : 100);
      return d;
    }).catch(e => {
      toast.error(e.message || "이슈 등록 실패");
      throw e;
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
    // v9.5.13: 우선순위/카테고리 UI 제거 — 기존 값 그대로 유지해 저장 (빈 카테고리는 payload 에서 제외).
    const payload = { issue_id: selected.id, title: editTitle, description: editDesc, priority: editPrio, lots: editLots };
    if ((editCategory || "").trim()) payload.category = editCategory;
    sf(API + "/update", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })
      .then(() => { setEditMode(false); loadDetail(selected.id); load(); }).catch(e => toast.error(e.message));
  };
  const filteredIssues = issues.filter(iss => {
    if (filter && iss.status !== filter) return false;
    if (search) { const s = search.toLowerCase(); return (iss.title || "").toLowerCase().includes(s) || (iss.username || "").toLowerCase().includes(s) || (iss.category || "").toLowerCase().includes(s); }
    return true;
  });

  return (
    <div className="flow-connected-page" style={{ display: "flex", height: "calc(100vh - 52px)", background: "var(--bg-primary)", color: "var(--text-primary)" }}>
      {/* Sidebar — v9.5.13: 카테고리 라벨 제거만큼 제목이 더 길게 보이도록 폭 확대 (320→380). */}
      <div style={{ width: 380, minWidth: 380, borderRight: "1px solid var(--border)", display: "flex", flexDirection: "column", background: "var(--bg-secondary)" }}>
        <div className="flow-sidebar-header" style={{ padding: "12px 16px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div style={{ minWidth: 0 }}>
            <span className="flow-sidebar-header-title" style={{ fontSize: 14, fontWeight: 700, color: "var(--text-secondary)" }}>ET 추적</span>
            <div className="flow-sidebar-header-meta">{filteredIssues.length} / {issues.length} issues</div>
          </div>
          <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
            <Pill tone="neutral">{filteredIssues.length}</Pill>
            <Button variant="primary" onClick={() => setCreating(!creating)}>+ 새 이슈</Button>
            <PageGear title="ET 추적 설정" canEdit={isAdmin} position="bottom-left">
              <TrackerSettings isAdmin={isAdmin} />
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
                {/* v9.5.13: 카테고리 텍스트 라벨(변경점/불량/Monitor 등) 제거 — 색 점만 유지해 제목이 길게 보이도록. */}
                <span title={iss.category ? `카테고리: ${iss.category}` : `상태: ${iss.status}`} style={{ width: 9, height: 9, borderRadius: "50%", background: iss.category ? catColor(iss.category) : (statusColor[iss.status] || "var(--muted)"), flexShrink: 0, border: iss.category ? "1px solid rgba(255,255,255,0.2)" : "none" }} />
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
        <div style={{ marginBottom: 12 }}>
          <FlowiPromptBox
            defaultScope={{ kind: "tracker", issue_id: selected?.id || "", status: filter || "all" }}
            placeholder="Flow-i 이슈 질문"
            maxRows={8}
          />
        </div>
        {creating && <IssueForm onSubmit={create} onClose={() => setCreating(false)} user={user} roleNames={roleNames} />}
        {viewTab === "gantt" ? <GanttChart issues={issues} onIssueClick={(id) => { loadDetail(id); setViewTab("list"); }} />
          : selected ? (<Card padding={0}>
            <section style={connectedPanelSection}>
            {/* Header */}
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: selected.closed_at ? 8 : 0, flexWrap: "nowrap" }}>
              <div style={{ display: "flex", alignItems: "baseline", gap: 10, minWidth: 0, flex: 1, overflow: "hidden", whiteSpace: "nowrap" }}>
                {editMode ? <input value={editTitle} onChange={e => setEditTitle(e.target.value)} style={{ minWidth: 120, fontSize: 18, fontWeight: 700, padding: "4px 8px", borderRadius: 6, border: "1px solid var(--accent)", background: "var(--bg-primary)", color: "var(--text-primary)", outline: "none", flex: 1 }} />
                  : <span title={selected.title} style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", fontSize: 18, fontWeight: 700 }}>{selected.title}</span>}
                <span style={{ flexShrink: 0, fontSize: 14, color: "var(--text-secondary)", fontFamily: "monospace" }}>
                  작성자 <strong style={{ color: "var(--text-primary)" }}>{selected.username}</strong> · {(selected.created || selected.timestamp || "")?.slice(0, 16)}
                </span>
              </div>
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

            {selected.closed_at && <div style={{ fontSize: 14, color: "var(--text-secondary)" }}>완료: {selected.closed_at?.slice(0, 16)}</div>}
            </section>
            {/* v9.5.84: 수신 그룹은 이슈마다 지정 (메일 on/off·스캔 시각은 톱니바퀴). */}
            <IssueMailGroups issue={selected} canEdit={!!canEdit} onSaved={() => loadDetail(selected.id)} />

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
                dangerouslySetInnerHTML={{ __html: trackerDescHtml(selected.description_html || selected.description) }} />
              </section>

            )}

            {/* v9.5.13: 수정 모드의 우선순위/카테고리 선택 제거 — 기존 값 유지한 채 저장. */}

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
                      onKeyDown={e => {if(e.key === "Enter"){if(e.nativeEvent?.isComposing||e.keyCode===229)return;addReply(i);}}} />
                    <button onClick={() => addReply(i)} style={{ padding: "6px 11px", borderRadius: 6, border: "1px solid var(--accent)", background: "var(--accent-glow)", color: "var(--accent)", fontSize: 14, fontWeight: 700, cursor: "pointer" }}>답글</button>
                  </div>
                </div>))}
              <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                <input value={comment} onChange={e => setComment(e.target.value)} placeholder="댓글 입력..."
                  style={{ flex: 1, padding: "8px 12px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14, outline: "none" }}
                  onKeyDown={e => {if(e.key === "Enter"){if(e.nativeEvent?.isComposing||e.keyCode===229)return;addComment();}}} />
                <button onClick={addComment} style={{ padding: "8px 16px", borderRadius: 6, border: "none", background: "var(--accent)", color: "#fff", fontSize: 14, fontWeight: 600, cursor: "pointer" }}>전송</button>
              </div>
            </section>
          </Card>) : <EmptyState title="이슈를 선택하세요" hint="좌측 목록에서 이슈를 고르거나 새 이슈를 생성하세요." />}
      </div>
    </div>);
}

/* ═══ v8.5.2 Tracker Settings (PageGear 내부) ═══ */
function TrackerSettings({ isAdmin }) {
  // v9.5.84: 구 '자동 갱신'(lot watch 30분 폴링) 설정 제거 — Tracker 는 ET DB 만
  //   추적하므로 갱신 경로는 아래 ET Tracker 스캔 하나뿐이다.
  // v9.5.13: ET Tracker 일일 스캔 설정 — 스캔 시각·메일 on/off·PGM 필터·수신 그룹·링크 주소.
  const [etScan, setEtScan] = useState({ enabled: true, scan_times: [], mail_enabled: false, mail_group_ids: [], pgm_filters: [], app_base_url: "", status: {} });
  const [etTimesText, setEtTimesText] = useState("");
  const [etPgmText, setEtPgmText] = useState("");
  const [etScanMsg, setEtScanMsg] = useState("");
  const [etScanBusy, setEtScanBusy] = useState(false);
  const [mailGroups, setMailGroups] = useState([]);
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
  const [dcRows, setDcRows] = useState([]);
  const [dcPath, setDcPath] = useState("");
  const [dcExists, setDcExists] = useState(false);
  const [dcBusy, setDcBusy] = useState(false);
  const [dcMsg, setDcMsg] = useState("");
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
  const loadDcMapping = () => sf(API + "/dc-layer-mapping").then(d => {
    setDcRows(Array.isArray(d?.rows) ? d.rows.map(r => ({ dc_layer: r.dc_layer || "", step_ids: Array.isArray(r.step_ids) ? r.step_ids.join(", ") : String(r.step_ids || "") })) : []);
    setDcPath(d?.path || d?.file_name || "dc_layer_step_mapping.csv");
    setDcExists(!!d?.exists);
  });
  const applyEtScan = (d) => {
    const next = {
      enabled: d.enabled !== false,
      scan_times: Array.isArray(d.scan_times) ? d.scan_times : [],
      mail_enabled: !!d.mail_enabled,
      mail_group_ids: Array.isArray(d.mail_group_ids) ? d.mail_group_ids.map(String) : [],
      pgm_filters: Array.isArray(d.pgm_filters) ? d.pgm_filters : [],
      app_base_url: d.app_base_url || "",
      status: d.status || {},
    };
    setEtScan(next);
    setEtTimesText(next.scan_times.join(", "));
    setEtPgmText(next.pgm_filters.join(", "));
  };
  const loadEtScan = () => sf(API + "/et-scan").then(applyEtScan);
  const loadMailGroups = () => sf("/api/mail-groups/list").then(d => setMailGroups(Array.isArray(d?.groups) ? d.groups : [])).catch(() => setMailGroups([]));
  // fix: arrow+Promise → Promise 가 cleanup 에 저장되어 unmount 시 crash 방지.
  useEffect(() => { loadDbSources().catch(() => {}); loadDcMapping().catch(() => {}); loadEtScan().catch(() => {}); loadMailGroups(); }, []);
  const fmtTime = (v) => v ? String(v).replace("T", " ").slice(0, 19) : "-";
  const saveEtScan = () => {
    if (!isAdmin || etScanBusy) return;
    const times = etTimesText.split(/[,\s]+/).map(s => s.trim()).filter(Boolean);
    const bad = times.filter(t => !/^([01]?\d|2[0-3]):[0-5]\d$/.test(t));
    if (bad.length) { setEtScanMsg(`시간 형식 오류: ${bad.join(", ")} (HH:MM 로 입력)`); return; }
    const pgms = etPgmText.split(/[,\s]+/).map(s => s.trim()).filter(Boolean);
    setEtScanBusy(true);
    setEtScanMsg("");
    sf(API + "/et-scan/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        enabled: !!etScan.enabled,
        scan_times: times,
        mail_enabled: !!etScan.mail_enabled,
        mail_group_ids: etScan.mail_group_ids,
        pgm_filters: pgms,
        app_base_url: etScan.app_base_url || "",
      }),
    }).then(d => { applyEtScan(d); setEtScanMsg("ET Tracker 설정 저장 완료"); })
      .catch(e => setEtScanMsg(e.message)).finally(() => setEtScanBusy(false));
  };
  const runEtScanNow = (opts) => {
    const full = opts === true || opts?.full === true;
    if (!isAdmin || etScanBusy) return;
    setEtScanBusy(true);
    setEtScanMsg("");
    sf(API + `/et-scan/run-now${full ? "?full=1" : ""}`, { method: "POST" })
      .then(d => {
        applyEtScan(d);
        const run = d?.run || {};
        // 대상에서 빠진 이슈가 있으면 첫 사유를 같이 보여준다.
        const skipped = Array.isArray(run.skipped) ? run.skipped : [];
        const skipNote = skipped.length
          ? ` · 대상 제외 ${skipped.length}건 (${skipped[0]?.reason || ""})`
          : "";
        setEtScanMsg(run.ok === false
          ? (run.last_error || "측정이력 반영 실패")
          : `즉시 반영 완료 · 이슈 ${run.issues_scanned ?? 0}건 / 신규 ${run.new_entries ?? 0}건 / 메일 ${run.mail_count ?? 0}건${skipNote}`);
      }).catch(e => setEtScanMsg(e.message)).finally(() => setEtScanBusy(false));
  };
  const toggleEtMailGroup = (gid) => {
    setEtScan(prev => {
      const cur = new Set(prev.mail_group_ids || []);
      if (cur.has(gid)) cur.delete(gid); else cur.add(gid);
      return { ...prev, mail_group_ids: Array.from(cur) };
    });
  };
  const saveDcMapping = () => {
    if (!isAdmin || dcBusy) return;
    setDcBusy(true);
    setDcMsg("");
    sf(API + "/dc-layer-mapping/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rows: dcRows.map(r => ({ dc_layer: r.dc_layer, step_ids: String(r.step_ids || "").split(",").map(v => v.trim()).filter(Boolean) })) }),
    }).then(d => {
      setDcRows((d.rows || []).map(r => ({ dc_layer: r.dc_layer || "", step_ids: (r.step_ids || []).join(", ") })));
      setDcPath(d.path || dcPath);
      setDcExists(true);
      setDcMsg("DC Layer 매칭 저장 완료");
    }).catch(e => setDcMsg(e.message)).finally(() => setDcBusy(false));
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
        // v9.5.84: role 이름은 화면에서 편집하지 않는다 — 빈 값으로 두면 서버가 기존 값 유지.
        monitor_name: "",
        analysis_name: "",
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
    }).catch(e => setDbMsg(e.message)).finally(() => setDbBusy(false));
  };
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
        {/* v9.5.84: 'Monitor명' 입력 제거 — Tracker 는 ET DB 만 추적하므로 role 이름을
            여기서 바꿀 일이 없다. 저장 시에는 서버가 기존 값을 그대로 유지한다. */}
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
          </div>
        ))}
        <button onClick={saveDbSources} disabled={!isAdmin || dbBusy}
          style={{ justifySelf: "end", padding: "6px 10px", borderRadius: 4, border: "none", background: "var(--accent)", color: "#fff", fontSize: 14, cursor: isAdmin && !dbBusy ? "pointer" : "not-allowed", opacity: isAdmin ? 1 : 0.55 }}>
          페이지 설정 저장
        </button>
        {dbMsg && <div style={{ fontSize: 14, color: dbMsg.includes("완료") ? "var(--ok)" : "var(--danger)" }}>{dbMsg}</div>}
      </div>
      <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>DC Layer · step_id 매칭</div>
      <div style={{ fontSize: 14, color: "var(--text-secondary)", lineHeight: 1.5, marginBottom: 8 }}>
        각 DC Layer의 step_id를 쉼표로 구분해 입력합니다. 저장 파일: <span style={{ fontFamily: "monospace" }}>{dcPath || "dc_layer_step_mapping.csv"}</span>
        {!dcExists && <span style={{ color: "var(--warn)", marginLeft: 6 }}>아직 파일이 없으며 저장하면 DB 단일 CSV 파일로 생성됩니다.</span>}
      </div>
      <div style={{ display: "grid", gap: 6, marginBottom: 8 }}>
        {dcRows.map((row, idx) => (
          <div key={`${row.dc_layer}-${idx}`} style={{ display: "grid", gridTemplateColumns: "100px 1fr auto", gap: 6, alignItems: "center" }}>
            <input value={row.dc_layer} disabled={!isAdmin || dcBusy}
              onChange={e => setDcRows(prev => prev.map((r, i) => i === idx ? { ...r, dc_layer: e.target.value.toUpperCase() } : r))}
              placeholder="M1DC"
              style={{ padding: "6px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14, fontFamily: "monospace", fontWeight: 700 }} />
            <input value={row.step_ids} disabled={!isAdmin || dcBusy}
              onChange={e => setDcRows(prev => prev.map((r, i) => i === idx ? { ...r, step_ids: e.target.value } : r))}
              placeholder="step_id를 쉼표로 구분 (예: ET100, ET110)"
              style={{ padding: "6px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14, fontFamily: "monospace" }} />
            <button onClick={() => setDcRows(prev => prev.filter((_, i) => i !== idx))} disabled={!isAdmin || dcBusy}
              style={{ padding: "5px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "transparent", color: "var(--danger)", cursor: isAdmin && !dcBusy ? "pointer" : "not-allowed" }}>삭제</button>
          </div>
        ))}
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, marginBottom: 14 }}>
        <button onClick={() => setDcRows(prev => [...prev, { dc_layer: "", step_ids: "" }])} disabled={!isAdmin || dcBusy}
          style={{ padding: "6px 10px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", cursor: isAdmin && !dcBusy ? "pointer" : "not-allowed" }}>+ DC 추가</button>
        <button onClick={saveDcMapping} disabled={!isAdmin || dcBusy}
          style={{ padding: "6px 10px", borderRadius: 4, border: "none", background: "var(--accent)", color: "#fff", cursor: isAdmin && !dcBusy ? "pointer" : "not-allowed", opacity: isAdmin ? 1 : 0.55 }}>{dcBusy ? "저장 중..." : "DC Layer 매칭 저장"}</button>
      </div>
      {dcMsg && <div style={{ marginTop: -8, marginBottom: 12, fontSize: 14, color: dcMsg.includes("완료") ? "var(--ok)" : "var(--danger)" }}>{dcMsg}</div>}
      {/* ET Tracker 일일 반영 — ET history의 root_lot/wafer 기준 PGM(pt) 측정 감지. */}
      <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>ET Tracker 스캔</div>
      <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 8, lineHeight: 1.5 }}>
        별도 ET history scan이 준비한 결과에서 진행중 이슈의 root_lot_id / wafer_id에 해당하는 측정만 바로 가져와
        step_id 별 PGM(pt) 이력에 누적합니다. ET Tracker에서는 원본 ET DB를 다시 스캔하지 않습니다.
      </div>
      <div style={{ display: "grid", gap: 6, marginBottom: 8 }}>
        <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
          <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 14, color: "var(--text-secondary)" }}>
            <input type="checkbox" checked={!!etScan.enabled} disabled={!isAdmin || etScanBusy}
              onChange={e => setEtScan(prev => ({ ...prev, enabled: e.target.checked }))} />
            스캔 사용
          </label>
          <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 14, color: etScan.mail_enabled ? "var(--accent)" : "var(--text-secondary)", fontWeight: etScan.mail_enabled ? 700 : 500 }}>
            <input type="checkbox" checked={!!etScan.mail_enabled} disabled={!isAdmin || etScanBusy}
              onChange={e => setEtScan(prev => ({ ...prev, mail_enabled: e.target.checked }))} />
            메일 발송
          </label>
          <button onClick={runEtScanNow} disabled={!isAdmin || etScanBusy}
            title="준비된 ET history에서 각 root lot의 신규 측정만 가져옵니다"
            style={{ marginLeft: "auto", padding: "6px 10px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14, cursor: isAdmin && !etScanBusy ? "pointer" : "not-allowed", opacity: isAdmin ? 1 : 0.55 }}>
            즉시 반영
          </button>
          <button onClick={() => runEtScanNow({ full: true })} disabled={!isAdmin || etScanBusy}
            title="준비된 ET history cache 전 구간을 다시 반영합니다"
            style={{ padding: "6px 10px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-secondary)", fontSize: 14, cursor: isAdmin && !etScanBusy ? "pointer" : "not-allowed", opacity: isAdmin ? 1 : 0.55 }}>
            전체 이력 반영
          </button>
        </div>
        <label style={{ display: "grid", gridTemplateColumns: "110px 1fr", gap: 8, alignItems: "center", fontSize: 14, color: "var(--text-secondary)" }}>
          스캔 시간
          <input value={etTimesText} disabled={!isAdmin || etScanBusy}
            onChange={e => setEtTimesText(e.target.value)}
            placeholder="예: 08:00, 13:00, 18:00 (하루 n번)"
            style={{ padding: "6px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14, fontFamily: "monospace" }} />
        </label>
        <label style={{ display: "grid", gridTemplateColumns: "110px 1fr", gap: 8, alignItems: "center", fontSize: 14, color: "var(--text-secondary)" }}>
          PGM 필터
          <input value={etPgmText} disabled={!isAdmin || etScanBusy}
            onChange={e => setEtPgmText(e.target.value)}
            placeholder="예: H1, H2 — PGM(pt)에 포함될 때만 알림/메일. 비우면 전체"
            style={{ padding: "6px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14, fontFamily: "monospace" }} />
        </label>
        <label style={{ display: "grid", gridTemplateColumns: "110px 1fr", gap: 8, alignItems: "center", fontSize: 14, color: "var(--text-secondary)" }}>
          메일 링크 주소
          <input value={etScan.app_base_url} disabled={!isAdmin || etScanBusy}
            onChange={e => setEtScan(prev => ({ ...prev, app_base_url: e.target.value }))}
            placeholder="예: http://flow-server:8080 — 메일 본문의 이슈 링크 베이스"
            style={{ padding: "6px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14, fontFamily: "monospace" }} />
        </label>
        {/* v9.5.84: 이슈에 수신 그룹이 지정돼 있으면 그쪽이 우선. 여기는 지정 없는 이슈의 기본값. */}
        <div style={{ display: "grid", gridTemplateColumns: "110px 1fr", gap: 8, alignItems: "start", fontSize: 14, color: "var(--text-secondary)" }}>
          기본 수신 그룹
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {mailGroups.length === 0 && <span style={{ fontSize: 14, color: "var(--text-secondary)" }}>등록된 수신처 그룹 없음 (작성자·lot 추가자에게만 발송)</span>}
            <div style={{ width: "100%", fontSize: 14, color: "var(--text-secondary)", marginBottom: 2 }}>이슈에 수신 그룹을 따로 지정하면 그 이슈는 그쪽으로만 발송됩니다.</div>
            {mailGroups.map(g => {
              const on = (etScan.mail_group_ids || []).includes(String(g.id));
              return (
                <label key={g.id} style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 14, padding: "3px 8px", borderRadius: 999, border: "1px solid " + (on ? "var(--accent)" : "var(--border)"), background: on ? "var(--accent)22" : "transparent", cursor: isAdmin ? "pointer" : "default", color: on ? "var(--accent)" : "var(--text-secondary)" }}>
                  <input type="checkbox" checked={on} disabled={!isAdmin || etScanBusy} onChange={() => toggleEtMailGroup(String(g.id))} style={{ accentColor: "var(--accent)" }} />
                  {g.name}
                  <span style={{ color: "var(--text-secondary)" }}>{(g.members?.length || 0) + (g.extra_emails?.length || 0)}</span>
                </label>
              );
            })}
          </div>
        </div>
        <button onClick={saveEtScan} disabled={!isAdmin || etScanBusy}
          style={{ justifySelf: "end", padding: "6px 10px", borderRadius: 4, border: "none", background: "var(--accent)", color: "#fff", fontSize: 14, cursor: isAdmin && !etScanBusy ? "pointer" : "not-allowed", opacity: isAdmin ? 1 : 0.55 }}>
          ET Tracker 설정 저장
        </button>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginBottom: 8, fontSize: 14, color: "var(--text-secondary)" }}>
        <span>최근 스캔 {fmtTime(etScan.status?.finished_at || etScan.status?.started_at)}</span>
        <span>이슈 {etScan.status?.issues_scanned ?? 0} / 랏 {etScan.status?.lots_scanned ?? 0}</span>
        <span>신규 측정 {etScan.status?.new_entries ?? 0}건 / 메일 {etScan.status?.mail_count ?? 0}건</span>
        <span>스캔 {etScan.enabled === false ? "꺼짐" : (etScan.scan_times.length ? `매일 ${etScan.scan_times.join(", ")}` : "시간 미지정")}</span>
        {/* v9.5.14: 개발 워커 우선 실행 — 마지막 스캔이 어디서 돌았는지 표시. */}
        <span>실행 위치 {etScan.status?.executed_on === "worker" ? "개발 워커" : "운영(로컬)"}</span>
      </div>
      {etScan.status?.last_error && <div style={{ marginBottom: 10, fontSize: 14, color: "var(--danger)" }}>{etScan.status.last_error}</div>}
      {etScanMsg && <div style={{ marginBottom: 12, fontSize: 14, color: etScanMsg.includes("완료") ? "var(--ok)" : "var(--danger)" }}>{etScanMsg}</div>}
      {/* v9.5.84: 메일 미리보기를 톱니바퀴 패널 아래 인라인이 아니라 큰 팝업으로 띄운다.
          좁은 설정 패널 폭에 맞춰 줄바꿈된 미리보기는 실제 메일과 달라 보였다. */}
      <Modal open={!!mailPreview} onClose={() => setMailPreview(null)} title="메일 미리보기" width={1040} maxHeight="92vh">
        <div style={{ display: "grid", gap: 10 }}>
          <div style={{ padding: "9px 12px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg-primary)", fontSize: 14, lineHeight: 1.5 }}>
            <div style={{ color: "var(--text-secondary)", marginBottom: 2 }}>제목</div>
            <div style={{ fontWeight: 700, color: "var(--text-primary)", wordBreak: "break-word" }}>{mailPreview?.subject || "-"}</div>
          </div>
          <iframe
            title="tracker-mail-preview"
            sandbox=""
            srcDoc={mailPreview?.body || ""}
            style={{ width: "100%", height: "70vh", minHeight: 420, border: "1px solid var(--border)", borderRadius: 6, background: "#fff" }}
          />
          <div style={{ display: "flex", justifyContent: "flex-end" }}>
            <button onClick={() => setMailPreview(null)}
              style={{ padding: "6px 14px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14, cursor: "pointer" }}>
              닫기
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
