import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import Modal from "../../components/Modal";
import PageGear from "../../components/PageGear";
import RichBoardEditor, { RichBoardContent, richTextHasContent } from "../../components/RichBoardEditor";
import SplitTableSnapshotView from "../../components/SplitTableSnapshotView";
import { toast } from "../../components/Toast";
import { Avatar, EmptyState } from "../../components/UXKit";
import { Button, Filter, IconLabel, Input, PageShell, Pill, Select, Textarea } from "../../components/ui";
import { dl, postJson, putJson, qs, sf, userLabel } from "../../lib/api";
import { canManagePage } from "../../lib/permissions";
import "./My_AnalysisRequest.css";

// 분석의뢰 — 의뢰서는 정형화된 의뢰 내용과 대상 Lot 만 받는다. 실제 진행·측정이 의뢰와 맞는지는
// 보고서를 쓰는 엔지니어가 SplitTable 과 같은 표(랏별 wafer 열 + 아래 DC layer 별 ET 측정 행)에서
// 직접 확인하고, Template Report 결과(PPTX)를 답글에 첨부한다. 답글이 달리면 의뢰자에게 메일이 간다.
const API = "/api/analysis-requests";
// Template Report 가 한 번 읽고 지우는 키 — My_TemplateReport.jsx 의 REPORT_RUN_TRANSFER_KEY 와 같은 값.
const REPORT_RUN_TRANSFER_KEY = "flow:templatereport:run-transfer";
// Template Report → 분석의뢰: 첨부할 보고서와 답글 초안 — My_TemplateReport.jsx 의 REPLY_DRAFT_KEY 와 같은 값.
const REPLY_DRAFT_KEY = "flow:analysisrequest:reply-draft";
const STATUSES = {
  registered: { label: "등록", tone: "info" },
  in_progress: { label: "분석중", tone: "warn" },
  completed: { label: "완료", tone: "ok" },
  rejected: { label: "반려", tone: "bad" },
};
const PRIORITIES = {
  normal: { label: "일반", tone: "neutral" },
  high: { label: "중요", tone: "warn" },
  urgent: { label: "긴급", tone: "bad" },
};
const EMPTY_FORM = {
  template_id: "", request_type: "", product: "", title: "", details: "", requester_team: "", priority: "normal",
  lots: "", split_columns: [], report_template_id: "", copied_from: "",
};

function text(value) { return value == null ? "" : String(value); }

function prettyTime(value) {
  if (!value) return "-";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return text(value).replace("T", " ");
  return d.toLocaleString("ko-KR", { year: "2-digit", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
}

function fileSize(bytes) {
  const n = Number(bytes) || 0;
  return n >= 1048576 ? `${(n / 1048576).toFixed(1)}MB` : `${Math.max(1, Math.round(n / 1024))}KB`;
}

function StatusPill({ value }) {
  const item = STATUSES[value] || STATUSES.registered;
  return <Pill tone={item.tone}>{item.label}</Pill>;
}

// 라벨 글자와 필수 표시(*)를 한 줄로 묶는다 — label 이 세로 flex 라 * 가 따로 떨어지면 칸이 한 줄 내려간다.
function FieldLabel({ children, required = false, hint = "" }) {
  return <span className="anareq-label">{children}{required && <em className="anareq-required">*</em>}{hint && <small>{hint}</small>}</span>;
}

function openTemplateReport(handoff) {
  if (!handoff?.root_lot_ids?.length) { toast.warn("대상 Lot 이 없습니다. 먼저 대상 Lot 을 넣고 '지금 갱신'을 눌러 주세요."); return; }
  try {
    window.sessionStorage.setItem(REPORT_RUN_TRANSFER_KEY, JSON.stringify({ ...handoff, source: "analysisrequest", timestamp: Date.now() }));
  } catch (_error) { toast.error("보고서 화면으로 조건을 넘기지 못했습니다."); return; }
  const search = handoff.template_id ? `?template_id=${encodeURIComponent(handoff.template_id)}` : "";
  window.dispatchEvent(new CustomEvent("flow:navigate", { detail: { tab: "templatereport", search } }));
}

function takeReplyDraft(requestId) {
  try {
    const raw = window.sessionStorage.getItem(REPLY_DRAFT_KEY);
    if (!raw) return null;
    const value = JSON.parse(raw);
    if (!value || value.request_id !== requestId || Date.now() - Number(value.timestamp || 0) > 30 * 60 * 1000) return null;
    window.sessionStorage.removeItem(REPLY_DRAFT_KEY);
    return value;
  } catch (_error) { return null; }
}

// ML_TABLE 열 고르기 — SplitTable 형식 표에 보일 항목(KNOB_ · FAB_(EQP) · MASK_ …).
function MlColumnPicker({ product, value, onChange, disabled }) {
  const [query, setQuery] = useState("");
  const [result, setResult] = useState({ columns: [], matched: 0, prefixes: {}, error: "" });
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!product) { setResult({ columns: [], matched: 0, prefixes: {}, error: "" }); return undefined; }
    setLoading(true);
    const timer = setTimeout(() => {
      sf(`${API}/ml-columns${qs({ product, q: query, limit: 200 })}`)
        .then((data) => setResult({ columns: data.columns || [], matched: data.matched || 0, prefixes: data.prefixes || {}, error: data.error || "", file: data.file || "" }))
        .catch((error) => setResult({ columns: [], matched: 0, prefixes: {}, error: error.message || "열 목록을 불러오지 못했습니다" }))
        .finally(() => setLoading(false));
    }, 200);
    return () => clearTimeout(timer);
  }, [product, query]);
  const selected = new Set(value);
  return <div className="anareq-picker">
    <div className="anareq-chips">
      {value.map((col) => <span className="anareq-chip" key={col}>{col}{!disabled && <button type="button" aria-label={`${col} 빼기`} onClick={() => onChange(value.filter((item) => item !== col))}>×</button>}</span>)}
      {!value.length && <span className="anareq-muted">고르지 않으면 의뢰 템플릿의 열, 그것도 없으면 KNOB_ 열 전체를 보여줍니다</span>}
    </div>
    {!disabled && <>
      <div className="anareq-picker-search">
        <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={product ? "열 이름 검색 (예: KNOB_5.0, FAB_, PC)" : "제품을 먼저 고르세요"} disabled={!product} />
        <div className="anareq-picker-prefix">
          {Object.entries(result.prefixes || {}).slice(0, 8).map(([prefix, count]) => <button type="button" key={prefix} className={query.toUpperCase() === `${prefix}_` ? "is-active" : ""} onClick={() => setQuery(query.toUpperCase() === `${prefix}_` ? "" : `${prefix}_`)}>{prefix} {count}</button>)}
        </div>
      </div>
      {result.error ? <div className="anareq-error">{result.error}</div> : <div className="anareq-picker-list">
        {result.columns.map((col) => <button type="button" key={col} disabled={selected.has(col)} onClick={() => onChange([...value, col])}>{col}</button>)}
        {!loading && product && !result.columns.length && <span className="anareq-muted">검색 결과가 없습니다.</span>}
      </div>}
      {!!product && <div className="anareq-muted">{loading ? "불러오는 중…" : `${result.file || ""} · ${result.matched}개 열 중 ${result.columns.length}개 표시`}</div>}
    </>}
  </div>;
}

function applyTemplate(form, template) {
  if (!template) return form;
  return {
    ...form,
    template_id: template.id,
    request_type: template.request_type || form.request_type,
    title: form.title || template.title || "",
    details: richTextHasContent(form.details) ? form.details : (template.details || ""),
    split_columns: template.split_columns?.length ? [...template.split_columns] : form.split_columns,
    report_template_id: template.report_template_id || form.report_template_id,
  };
}

function RequestForm({ initial, mode, config, products, busy, onCancel, onSave }) {
  const [form, setForm] = useState(() => {
    const seed = { ...EMPTY_FORM, request_type: config.request_types?.[0] || "", ...(initial || {}) };
    if (mode === "create" && !initial?.template_id) {
      return applyTemplate(seed, (config.templates || []).find((item) => item.id === config.default_template_id) || config.templates?.[0]);
    }
    return seed;
  });
  const change = (key) => (event) => setForm((prev) => ({ ...prev, [key]: event.target.value }));
  const chooseTemplate = (templateId) => {
    const template = (config.templates || []).find((item) => item.id === templateId);
    setForm((prev) => (template ? applyTemplate({ ...prev, details: "", split_columns: [], report_template_id: "" }, template) : { ...prev, template_id: "" }));
  };
  const valid = form.product && form.title.trim() && richTextHasContent(form.details);
  return <div className="anareq-form">
    {mode === "create" && !!config.templates?.length && <div className="anareq-template-row">
      <label><FieldLabel>의뢰 템플릿</FieldLabel><Select value={form.template_id} onChange={(event) => chooseTemplate(event.target.value)}>
        <option value="">템플릿 없이 작성</option>
        {config.templates.map((template) => <option key={template.id} value={template.id}>{template.name}{template.id === config.default_template_id ? " (기본)" : ""}</option>)}
      </Select></label>
      <span className="anareq-muted">톱니바퀴에 등록된 기본 양식이 의뢰 내용에 채워집니다. 비슷한 의뢰는 기존 의뢰의 '복제'가 더 빠릅니다.</span>
    </div>}
    {form.copied_from && <div className="anareq-note">기존 의뢰를 복제했습니다 — 보통 대상 Lot 과 제목만 바꾸면 됩니다.</div>}
    <div className="anareq-grid4">
      <label><FieldLabel>의뢰 유형</FieldLabel><Select value={form.request_type} onChange={change("request_type")}>
        {Array.from(new Set([form.request_type, ...(config.request_types || [])].filter(Boolean))).map((value) => <option key={value} value={value}>{value}</option>)}
      </Select></label>
      <label><FieldLabel>우선순위</FieldLabel><Select value={form.priority} onChange={change("priority")}>
        {Object.entries(PRIORITIES).map(([key, item]) => <option key={key} value={key}>{item.label}</option>)}
      </Select></label>
      <label><FieldLabel required>제품</FieldLabel><Select value={form.product} onChange={change("product")}>
        <option value="">제품 선택</option>{products.map((product) => <option key={product} value={product}>{product}</option>)}
      </Select></label>
      <label><FieldLabel>요청한 팀</FieldLabel><Select value={form.requester_team} onChange={change("requester_team")}>
        <option value="">{config.request_teams?.length ? "요청한 팀 선택" : "등록된 팀 없음 · 톱니 설정에서 추가"}</option>
        {Array.from(new Set([form.requester_team, ...(config.request_teams || [])].filter(Boolean))).map((team) => <option key={team} value={team}>{team}</option>)}
      </Select></label>
    </div>
    <label><FieldLabel required>제목</FieldLabel><Input value={form.title} onChange={change("title")} placeholder="예: [ECN-2609] PC PPID 변경 평가" /></label>
    <label><FieldLabel hint="쉼표로 여러 개 · wafer 지정은 A7001(1~12)">대상 Lot</FieldLabel><Input value={form.lots} onChange={change("lots")} placeholder="예: A7001, B7002(1~12) — 비워 두면 담당 엔지니어가 채웁니다" /></label>
    <label><FieldLabel required>의뢰 내용</FieldLabel>
      <RichBoardEditor value={form.details} onChange={(details) => setForm((prev) => ({ ...prev, details }))} uploadUrl={`${API}/upload`} minHeight={220} showCommands={false}
        placeholder="배경 · 변경 내용 · 확인할 항목 · 판정 기준 · 원하는 측정(DC layer)을 적어 주세요. 이미지와 Excel 표는 Ctrl+V 로 붙여넣을 수 있습니다."
        onUploadError={(error) => toast.error("이미지 붙여넣기 실패: " + (error?.message || error))} />
    </label>
    <div className="anareq-actions">
      <Button onClick={onCancel}>취소</Button>
      <Button variant="primary" disabled={!valid || busy} onClick={() => onSave(form)}>{busy ? "저장 중…" : mode === "edit" ? "수정 저장" : "의뢰 등록"}</Button>
    </div>
  </div>;
}

function ViewControls({ item, onSaved }) {
  const [lots, setLots] = useState(item.lots_text || "");
  const [columns, setColumns] = useState(item.split_columns || []);
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  useEffect(() => { setLots(item.lots_text || ""); setColumns(item.split_columns || []); }, [item.id, item.lots_text, item.split_columns]);
  const save = async () => {
    setSaving(true);
    try { onSaved(await postJson(`${API}/${item.id}/view`, { lots, split_columns: columns })); toast.ok("조회 조건을 바꾸고 다시 읽었습니다"); setOpen(false); }
    catch (error) { toast.error(error.message || "조회 조건을 바꾸지 못했습니다"); }
    finally { setSaving(false); }
  };
  if (!item.permissions?.can_change_view) return null;
  return <div className="anareq-view-controls">
    <div className="anareq-view-row">
      <label><FieldLabel hint="A7001, B7002(1~12)">대상 Lot</FieldLabel><Input value={lots} onChange={(event) => setLots(event.target.value)} placeholder="root_lot_id" /></label>
      <Button size="sm" onClick={() => setOpen((value) => !value)}>{open ? "표시 열 닫기" : `표시 열 바꾸기 · ${columns.length || "기본"}`}</Button>
      <Button size="sm" variant="primary" disabled={saving} onClick={save}>{saving ? "적용 중…" : "조건 적용"}</Button>
    </div>
    {open && <MlColumnPicker product={item.product} value={columns} onChange={setColumns} />}
  </div>;
}

function ProgressPanel({ item, reportTemplates, refreshing, onRefresh, onItem, canDraft }) {
  const [view, setView] = useState(null);
  const [loading, setLoading] = useState(false);
  const [draftOpen, setDraftOpen] = useState(false);
  const load = useCallback(async () => {
    setLoading(true);
    try { setView(await sf(`${API}/${item.id}/split-view`)); }
    catch (error) { setView({ lots: [], error: error.message || "SplitTable 을 불러오지 못했습니다" }); }
    finally { setLoading(false); }
  }, [item.id]);
  useEffect(() => { load(); }, [load, item.revision, item.tracking?.et_checked_at]);
  const summary = item.summary || {};
  const tracking = item.tracking || {};
  const templateName = reportTemplates.find((row) => row.id === item.report_template_id)?.name || item.report_template_id;
  const layerChips = Object.entries(summary.layers || {});
  return <div className="anareq-progress">
    <ViewControls item={item} onSaved={onItem} />
    <div className="anareq-progress-head">
      <div className="anareq-kpis">
        <div><small>대상 Lot</small><strong>{summary.lots || 0}</strong></div>
        <div><small>wafer</small><strong>{summary.wafers || 0}</strong></div>
        <div><small>ET 측정 wafer</small><strong>{summary.et_measured || 0}<em>/{summary.wafers || 0}</em></strong></div>
        <div className="anareq-kpi-wide"><small>측정된 DC layer (wafer 수)</small><span>{layerChips.length ? layerChips.map(([layer, count]) => <b key={layer}>{layer} {count}</b>) : "-"}</span></div>
      </div>
      <div className="anareq-progress-actions">
        <Button size="sm" onClick={async () => { await onRefresh(); }} disabled={refreshing}>{refreshing ? "갱신 중…" : "지금 갱신"}</Button>
        {canDraft && !!item.report_template_id && <Button size="sm" onClick={() => setDraftOpen(true)}>이 의뢰에 맞게 Template 고치기</Button>}
        <Button size="sm" variant="primary" onClick={() => openTemplateReport(item.report_handoff)} disabled={!item.report_handoff?.root_lot_ids?.length}>
          <IconLabel icon="chart-bar">{item.report_template_id ? `Template Report 실행 · ${templateName}` : "Template Report 열기"}</IconLabel>
        </Button>
      </div>
    </div>
    <div className="anareq-source-line">
      <span>SplitTable · {tracking.split_status === "ok" ? "정상" : (tracking.split_error || tracking.split_status || "갱신 전")}</span>
      <span>ET · {tracking.et_status === "ok" ? "정상" : (tracking.et_error || tracking.et_status || "갱신 전")}{tracking.et_cache_built_at ? ` · 캐시 ${prettyTime(tracking.et_cache_built_at)}` : ""} · {prettyTime(tracking.et_checked_at)}</span>
      <span>ET 추적 스캔 시각에 자동 갱신 · step_id 는 공용 DC layer 매핑으로 표시</span>
    </div>
    {!!summary.unresolved_lots?.length && <div className="anareq-note">wafer 를 아직 찾지 못한 Lot: {summary.unresolved_lots.join(", ")} — ML_TABLE 캐시 준비 후 다시 갱신해 주세요.</div>}
    {loading && !view && <div className="anareq-muted">SplitTable 을 불러오는 중…</div>}
    {view?.error && <div className="anareq-error">{view.error}</div>}
    {!item.lots?.length && <div className="anareq-muted">대상 Lot 이 없습니다. 위에서 대상 Lot 을 넣고 '조건 적용'을 누르세요.</div>}
    {(view?.lots || []).map((lot) => <section key={lot.root_lot_id} className="anareq-lot-block">
      <div className="anareq-lot-head">
        <strong className="mono">{lot.root_lot_id}</strong>
        {lot.wafer_filter && <span className="anareq-muted">wafer {lot.wafer_filter}</span>}
        {!lot.error && <span className="anareq-muted">wafer {lot.wafers} · ET 측정 {lot.et_measured} · {lot.et_layers?.join(", ") || "측정 없음"}</span>}
      </div>
      {lot.error ? <div className="anareq-error">{lot.error}</div>
        : <SplitTableSnapshotView embed={lot.embed} product={item.product} showTitle={false} showMeta={false} maxHeight={520} emptyMessage="표시할 SplitTable 값이 없습니다" />}
    </section>)}
    {draftOpen && <ReportDraftDialog item={item} templateName={templateName} onClose={() => setDraftOpen(false)} onLinked={onItem} />}
  </div>;
}

function ReportDraftDialog({ item, templateName, onClose, onLinked }) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [name, setName] = useState("");
  const run = async () => {
    setBusy(true);
    try {
      const out = await postJson(`${API}/report-draft`, { request_id: item.id, template_id: item.report_template_id, note });
      setResult(out); setName(out.template?.name || `${templateName} · ${item.lots_text}`);
    } catch (error) { toast.error(error.message || "Template 초안을 만들지 못했습니다"); }
    finally { setBusy(false); }
  };
  const save = async () => {
    setBusy(true);
    try {
      const saved = await postJson("/api/template-report/templates", { ...result.template, id: "", name: name.trim() || result.template.name });
      const linked = await postJson(`${API}/${item.id}/report-template`, { template_id: saved.template.id });
      onLinked(linked); toast.ok(`새 Template '${saved.template.name}'을 저장하고 이 의뢰에 연결했습니다`); onClose();
    } catch (error) { toast.error(error.message || "Template 을 저장하지 못했습니다"); }
    finally { setBusy(false); }
  };
  const errors = (result?.verification?.checks || []).filter((check) => check.level === "error");
  return <Modal open onClose={onClose} width={860} title="이 의뢰에 맞게 Template 고치기">
    <div className="anareq-draft">
      <p className="anareq-muted">원본 <b>{templateName}</b>의 랏 · lot_id · split 열 · wafer slot · split 값 · 측정 매수 · 변경 번호를 이 의뢰({item.lots_text})에 맞게 규칙으로 바꾸고, 연결된 AI 가 있으면 설명 문장만 다듬습니다. 저장 전에 검증 결과를 확인하세요.</p>
      <label><FieldLabel hint="선택">AI 에 덧붙일 말</FieldLabel><Textarea rows={2} value={note} onChange={(event) => setNote(event.target.value)} placeholder="예: 목적 문장에 'Spacer 증착 변경'을 넣어 주세요" /></label>
      <div className="anareq-actions"><Button variant="primary" disabled={busy} onClick={run}>{busy && !result ? "만드는 중…" : result ? "다시 만들기" : "초안 만들기"}</Button></div>
      {result && <>
        <div className="anareq-note"><b>{result.mode === "rule+llm" ? "규칙 + AI" : "규칙"}</b> · {result.message}</div>
        {!!result.rule_changes?.length && <ul className="anareq-list-plain">{result.rule_changes.map((change) => <li key={change}>{change}</li>)}</ul>}
        <table className="anareq-table"><thead><tr><th>검증</th><th>결과</th><th>내용</th></tr></thead><tbody>
          {(result.verification?.checks || []).map((check) => <tr key={check.label}><td>{check.label}</td><td className={`anareq-check-${check.level}`}>{check.level === "ok" ? "통과" : check.level === "warn" ? "확인" : "실패"}</td><td>{check.detail}</td></tr>)}
        </tbody></table>
        <details><summary className="anareq-muted">바뀐 글 미리보기</summary>
          {(result.template?.pages || []).map((page, index) => <div key={index} className="anareq-draft-page"><b>{page.title}</b>{(page.slots || []).filter((slot) => slot.kind === "text").map((slot, i) => <pre key={i}>{slot.text}</pre>)}</div>)}
        </details>
        <div className="anareq-actions">
          <label className="anareq-inline"><FieldLabel>새 Template 이름</FieldLabel><Input value={name} onChange={(event) => setName(event.target.value)} /></label>
          <Button variant="primary" disabled={busy || !!errors.length} onClick={save}>{errors.length ? "검증 실패 — 저장 불가" : "새 Template 으로 저장하고 연결"}</Button>
        </div>
      </>}
    </div>
  </Modal>;
}

function AttachmentList({ attachments, onRemove }) {
  if (!attachments?.length) return null;
  return <div className="anareq-attachments">{attachments.map((file) => <span key={file.uid} className="anareq-attachment">
    <button type="button" className="anareq-link" onClick={() => dl(file.url, file.name)}><IconLabel icon="paperclip">{file.name}</IconLabel></button>
    <small>{fileSize(file.size)}</small>
    {onRemove && <button type="button" aria-label={`${file.name} 빼기`} className="anareq-x" onClick={() => onRemove(file.uid)}>×</button>}
  </span>)}</div>;
}

function ResponseEditor({ initial, draft, busy, onCancel, onSave }) {
  const [body, setBody] = useState(initial?.body || draft?.body || "");
  const [attachments, setAttachments] = useState(initial?.attachments || draft?.attachments || []);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef(null);
  const upload = async (files) => {
    setUploading(true);
    try {
      const added = [];
      for (const file of Array.from(files || [])) {
        const fd = new FormData();
        fd.append("file", file);
        added.push(await sf(`${API}/upload-file`, { method: "POST", body: fd }));
      }
      setAttachments((current) => [...current, ...added]);
    } catch (error) { toast.error(error.message || "파일을 올리지 못했습니다"); }
    finally { setUploading(false); if (fileRef.current) fileRef.current.value = ""; }
  };
  const canSave = (richTextHasContent(body) || attachments.length) && !busy && !uploading;
  return <div className="anareq-response-editor">
    <RichBoardEditor value={body} onChange={setBody} uploadUrl={`${API}/upload`} minHeight={110} showCommands={false}
      placeholder="엔지니어 코멘트를 입력하세요. 분석 결과 표나 그림은 Ctrl+V 로 붙여넣을 수 있습니다."
      onUploadError={(error) => toast.error("이미지 붙여넣기 실패: " + (error?.message || error))} />
    <AttachmentList attachments={attachments} onRemove={(uid) => setAttachments((current) => current.filter((file) => file.uid !== uid))} />
    <div className="anareq-actions">
      <input ref={fileRef} type="file" multiple hidden accept=".pptx,.ppt,.xlsx,.xls,.csv,.pdf,.docx,.doc,.zip,.txt,.png,.jpg,.jpeg,.gif,.webp" onChange={(event) => upload(event.target.files)} />
      <span className="anareq-muted">Template Report 결과를 내려받아 고친 PPTX 도 첨부할 수 있습니다 · 등록하면 의뢰자에게 메일이 갑니다</span>
      <Button size="sm" disabled={uploading} onClick={() => fileRef.current?.click()}><IconLabel icon="paperclip">{uploading ? "올리는 중…" : "파일 첨부"}</IconLabel></Button>
      {onCancel && <Button size="sm" onClick={onCancel}>취소</Button>}
      <Button size="sm" variant="primary" disabled={!canSave} onClick={() => onSave({ body, attachments: attachments.map((file) => ({ uid: file.uid, name: file.name })) })}>{busy ? "저장 중…" : initial ? "답글 수정" : "답글 등록"}</Button>
    </div>
  </div>;
}

function History({ item }) {
  const labels = {
    request_created: "의뢰 등록", request_updated: "의뢰 수정", status_changed: "상태 변경", view_updated: "조회 조건 변경",
    response_created: "답글 등록", response_updated: "답글 수정", response_deleted: "답글 삭제",
    mail_sent: "의뢰자 메일", mail_failed: "메일 실패", report_template_linked: "보고서 Template 연결",
  };
  return <div className="anareq-history">{(item.activity_history || []).map((event, index) => <div key={`${event.at}-${index}`}>
    <strong>{labels[event.action] || event.action}</strong> · {event.actor || "-"} · {prettyTime(event.at)}
    {event.action === "status_changed" && <> · {STATUSES[event.from]?.label || event.from || "-"} → {STATUSES[event.to]?.label || event.to}</>}
    {event.note && <span className="anareq-muted"> · {event.note}</span>}
  </div>)}</div>;
}

function Detail({ item, user, busy, refreshing, reportTemplates, canDraft, onRefresh, onItem, onEdit, onCopy, onDelete, onStatus, onResponse, onDeleteResponse }) {
  const [editingResponse, setEditingResponse] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [draft, setDraft] = useState(() => takeReplyDraft(item.id));
  const [statusDraft, setStatusDraft] = useState({ status: item.status === "registered" ? "in_progress" : item.status, note: "" });
  useEffect(() => { setHistoryOpen(false); setEditingResponse(""); }, [item.id]);
  const replies = item.responses || [];
  const templateName = reportTemplates.find((row) => row.id === item.report_template_id)?.name || item.report_template_id;
  return <div className="anareq-detail">
    <div className="anareq-detail-nav">
      <div className="anareq-detail-heading">의뢰 내용</div>
      <div className="anareq-actions">
        <Button size="sm" onClick={onCopy}>복제해서 새 의뢰</Button>
        {item.permissions?.can_edit && <><Button size="sm" onClick={onEdit}>수정</Button><Button size="sm" variant="danger" onClick={onDelete}>삭제</Button></>}
      </div>
    </div>
    <div className="anareq-section anareq-two">
      <RichBoardContent html={item.details} className="anareq-body" />
      <dl className="anareq-conditions">
        <dt>대상 Lot</dt><dd className="mono">{item.lots_text || "-"}</dd>
        <dt>표시 열</dt><dd className="mono">{item.split_columns?.length ? item.split_columns.join(", ") : "기본(템플릿 · KNOB_)"}</dd>
        <dt>보고서 Template</dt><dd>{templateName || "연결 안 함"}</dd>
        {item.copied_from && <><dt>복제 원본</dt><dd className="mono">{item.copied_from.slice(0, 8)}</dd></>}
      </dl>
    </div>
    <div className="anareq-section">
      <div className="anareq-section-title">실제 진행 · ET 측정 (SplitTable 형식 — 랏별 wafer 열, 아래 행이 wafer 별 ET 측정)</div>
      <ProgressPanel item={item} reportTemplates={reportTemplates} refreshing={refreshing} onRefresh={onRefresh} onItem={onItem} canDraft={canDraft} />
    </div>
    <div className="anareq-section">
      <div className="anareq-section-title">답글 · {replies.length}건</div>
      {replies.map((response) => <div key={response.id} className={`anareq-response${response.author === user?.username ? " mine" : ""}`}>
        {editingResponse === response.id ? <ResponseEditor initial={response} busy={busy} onCancel={() => setEditingResponse("")} onSave={async (payload) => { if (await onResponse(payload, response.id)) setEditingResponse(""); }} /> : <>
          <div className="anareq-response-head"><Avatar name={response.author_name || response.author} /><div><strong>{userLabel({ username: response.author, name: response.author_name })}</strong>
            <div className="anareq-muted">{prettyTime(response.created_at)}{response.mail && <span className={response.mail.ok ? "anareq-mail-ok" : "anareq-mail-fail"}> · {response.mail.ok ? `의뢰자 메일 발송${response.mail.dry_run ? "(테스트)" : ""}` : `메일 미발송: ${response.mail.reason}`}</span>}</div></div>
            {response.permissions?.can_edit && <div className="anareq-actions"><Button size="sm" onClick={() => setEditingResponse(response.id)}>수정</Button><Button size="sm" variant="danger" onClick={() => onDeleteResponse(response.id)}>삭제</Button></div>}
          </div>
          {response.body && <RichBoardContent html={response.body} className="anareq-body" />}
          <AttachmentList attachments={response.attachments} />
        </>}
      </div>)}
      {item.permissions?.can_respond
        ? <>{draft && <div className="anareq-note">Template Report 결과가 첨부된 답글 초안입니다. 코멘트를 넣고 등록하세요.</div>}
          {/* 초안 유무도 key 에 넣는다 — 등록으로 답글 수가 늘어 다시 마운트될 때 아직 남은 초안이
              새 편집기 초기값으로 되살아나 같은 첨부가 두 번 등록될 수 있었다. */}
          <ResponseEditor key={`${item.id}-${replies.length}-${draft ? "draft" : "blank"}`} draft={draft} busy={busy} onSave={async (payload) => { const ok = await onResponse(payload); if (ok) setDraft(null); return ok; }} /></>
        : <div className="anareq-muted">답글은 이 페이지를 위임받은 담당자와 관리자만 등록할 수 있습니다.</div>}
    </div>
    {item.permissions?.can_process && <div className="anareq-section">
      <div className="anareq-process">
        <span>처리 상태</span>
        <Select value={statusDraft.status} onChange={(event) => setStatusDraft((prev) => ({ ...prev, status: event.target.value }))}>{Object.entries(STATUSES).map(([key, value]) => <option key={key} value={key}>{value.label}</option>)}</Select>
        <Input value={statusDraft.note} onChange={(event) => setStatusDraft((prev) => ({ ...prev, note: event.target.value }))} placeholder="상태 변경 사유 (선택)" />
        <Button variant="primary" disabled={busy || statusDraft.status === item.status} onClick={() => onStatus(statusDraft)}>상태 반영</Button>
      </div>
    </div>}
    <div className="anareq-section">
      <button type="button" className="anareq-link" aria-expanded={historyOpen} onClick={() => setHistoryOpen((open) => !open)}>{historyOpen ? "▾" : "▸"} 전체 업무 이력 · {item.activity_history?.length || 0}건</button>
      {historyOpen && <History item={item} />}
    </div>
  </div>;
}

function ListEditor({ title, items, setItems, canEdit, placeholder }) {
  const [draft, setDraft] = useState("");
  const add = () => {
    const value = draft.trim();
    if (!value) return;
    if (items.some((item) => item.toLowerCase() === value.toLowerCase())) { toast.warn(`이미 등록된 ${title}입니다`); return; }
    setItems([...items, value]); setDraft("");
  };
  return <div className="anareq-settings-group">
    <div className="anareq-section-label">{title}</div>
    <div className="anareq-settings-add"><Input value={draft} disabled={!canEdit} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); add(); } }} placeholder={placeholder} /><Button size="sm" disabled={!canEdit || !draft.trim()} onClick={add}>추가</Button></div>
    <div className="anareq-chips">{items.map((item) => <span className="anareq-chip" key={item}>{item}{canEdit && <button type="button" aria-label={`${item} 삭제`} onClick={() => setItems(items.filter((value) => value !== item))}>×</button>}</span>)}{!items.length && <span className="anareq-muted">등록된 항목이 없습니다.</span>}</div>
  </div>;
}

function TemplateEditor({ template, products, reportTemplates, canEdit, onChange, onRemove }) {
  const [product, setProduct] = useState(products[0] || "");
  const patch = (next) => onChange({ ...template, ...next });
  return <div className="anareq-template-editor">
    <div className="anareq-grid2">
      <label><FieldLabel>템플릿 이름</FieldLabel><Input value={template.name} disabled={!canEdit} onChange={(event) => patch({ name: event.target.value })} /></label>
      <label><FieldLabel>의뢰 유형</FieldLabel><Input value={template.request_type || ""} disabled={!canEdit} onChange={(event) => patch({ request_type: event.target.value })} placeholder="예: 변경점(ECN) 평가" /></label>
    </div>
    <label><FieldLabel>기본 제목</FieldLabel><Input value={template.title || ""} disabled={!canEdit} onChange={(event) => patch({ title: event.target.value })} placeholder="예: [ECN-____] ____ 변경 평가" /></label>
    <label><FieldLabel>기본 의뢰 내용 (반드시 볼 항목을 양식으로)</FieldLabel>
      <RichBoardEditor value={template.details || ""} onChange={(details) => patch({ details })} uploadUrl={`${API}/upload`} minHeight={140} showCommands={false} placeholder="배경 / 변경 내용 / 확인 항목(ET · Inline · 수율) / 원하는 DC layer / 판정 기준" />
    </label>
    <label><FieldLabel>보고서 Template</FieldLabel><Select value={template.report_template_id || ""} disabled={!canEdit} onChange={(event) => patch({ report_template_id: event.target.value })}>
      <option value="">연결 안 함</option>{reportTemplates.map((item) => <option key={item.id} value={item.id}>{item.name || item.id}</option>)}
    </Select></label>
    <label><FieldLabel hint="SplitTable 형식 표에 보일 열 · 열 검색용 제품">표시 열</FieldLabel><Select value={product} onChange={(event) => setProduct(event.target.value)}>{products.map((value) => <option key={value} value={value}>{value}</option>)}</Select></label>
    <MlColumnPicker product={product} value={template.split_columns || []} onChange={(columns) => patch({ split_columns: columns })} disabled={!canEdit} />
    {canEdit && <div className="anareq-actions"><Button size="sm" variant="danger" onClick={onRemove}>이 템플릿 삭제</Button></div>}
  </div>;
}

// DC layer ↔ step_id — flow 공용 매핑(ET 추적 · 분석의뢰 · 홈 챗). auto report 의 dict 를 그대로 붙여넣는다.
function DcLayerSettings() {
  const [mapping, setMapping] = useState({ rows: [], can_edit: false });
  const [textValue, setTextValue] = useState("");
  const [preview, setPreview] = useState(null);
  const [mode, setMode] = useState("replace");
  const [busy, setBusy] = useState(false);
  useEffect(() => { sf("/api/dc-layers").then(setMapping).catch(() => {}); }, []);
  const parse = async () => {
    setBusy(true);
    try { setPreview(await postJson("/api/dc-layers/parse", { text: textValue })); }
    catch (error) { setPreview(null); toast.error(error.message || "매핑을 읽지 못했습니다"); }
    finally { setBusy(false); }
  };
  const save = async () => {
    setBusy(true);
    try { setMapping(await postJson("/api/dc-layers", { text: textValue, mode })); setPreview(null); setTextValue(""); toast.ok("DC layer 매핑을 저장했습니다 — flow 전체에 바로 반영됩니다"); }
    catch (error) { toast.error(error.message || "저장하지 못했습니다"); }
    finally { setBusy(false); }
  };
  const filled = (mapping.rows || []).filter((row) => row.step_ids?.length);
  return <div className="anareq-settings-group">
    <div className="anareq-section-label">DC layer ↔ step_id (flow 공용)</div>
    <p className="anareq-muted">ET 추적 · 분석의뢰 · 홈 챗이 같은 매핑으로 step_id 를 M1DC 같은 DC layer 로 읽습니다. auto report 의 <code>dc_step_to_ids</code> dict 를 그대로 붙여넣으세요.</p>
    <div className="anareq-dc-current">{filled.length ? filled.map((row) => <span key={row.dc_layer} title={row.step_ids.join(", ")}><b>{row.dc_layer}</b> {row.step_ids.length}</span>) : <span className="anareq-muted">등록된 step_id 가 없습니다.</span>}</div>
    {mapping.can_edit ? <>
      <Textarea rows={5} value={textValue} onChange={(event) => { setTextValue(event.target.value); setPreview(null); }} placeholder={"{'M1DC': ['NU467300', 'NU467310'], 'M2DC': ['NU468100']}"} />
      <div className="anareq-actions">
        <Select value={mode} onChange={(event) => setMode(event.target.value)}><option value="replace">전체 바꾸기</option><option value="merge">붙여넣은 layer 만 덮어쓰기</option></Select>
        <Button size="sm" disabled={busy || !textValue.trim()} onClick={parse}>읽어 보기</Button>
        <Button size="sm" variant="primary" disabled={busy || !preview} onClick={save}>저장</Button>
      </div>
      {preview && <div className="anareq-note">DC layer {preview.layers}개 · step_id {preview.step_ids}개 — {preview.rows.slice(0, 8).map((row) => `${row.dc_layer}(${row.step_ids.length})`).join(", ")}{preview.rows.length > 8 ? " …" : ""}</div>}
    </> : <div className="anareq-muted">저장은 관리자와 분석의뢰·ET 추적·Auto report·ET 측정시간 위임자만 할 수 있습니다.</div>}
  </div>;
}

function BoardSettings({ config, canEdit, products, reportTemplates, onSaved }) {
  const [types, setTypes] = useState(config.request_types || []);
  const [teams, setTeams] = useState(config.request_teams || []);
  const [templates, setTemplates] = useState(config.templates || []);
  const [defaultId, setDefaultId] = useState(config.default_template_id || "");
  const [notify, setNotify] = useState(config.notify_on_response !== false);
  const [activeId, setActiveId] = useState(config.templates?.[0]?.id || "");
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    setTypes(config.request_types || []); setTeams(config.request_teams || []);
    setTemplates(config.templates || []); setDefaultId(config.default_template_id || ""); setNotify(config.notify_on_response !== false);
    setActiveId((current) => current || config.templates?.[0]?.id || "");
  }, [config]);
  const active = templates.find((item) => item.id === activeId);
  const addTemplate = () => {
    const id = `tpl-${Date.now().toString(36)}`;
    setTemplates([...templates, { id, name: `새 템플릿 ${templates.length + 1}`, details: "", split_columns: [], report_template_id: "", request_type: "", title: "" }]);
    setActiveId(id);
    if (!defaultId) setDefaultId(id);
  };
  const save = async () => {
    setSaving(true);
    try {
      onSaved(await postJson(`${API}/config`, { request_types: types, request_teams: teams, templates, default_template_id: defaultId, notify_on_response: notify }));
      toast.ok("분석의뢰 설정을 저장했습니다");
    } catch (error) { toast.error(error.message || "설정을 저장하지 못했습니다"); }
    finally { setSaving(false); }
  };
  return <div className="anareq-settings">
    <p className="anareq-muted">기본 의뢰 템플릿을 등록하면 새 의뢰의 의뢰 내용에 그 양식이 채워집니다. 저장 즉시 반영됩니다.</p>
    <ListEditor title="의뢰 유형" items={types} setItems={setTypes} canEdit={canEdit} placeholder="예: 변경점(ECN) 평가" />
    <ListEditor title="요청한 팀" items={teams} setItems={setTeams} canEdit={canEdit} placeholder="예: Module" />
    <label className="anareq-check-row"><input type="checkbox" checked={notify} disabled={!canEdit} onChange={(event) => setNotify(event.target.checked)} /> 답글이 달리면 의뢰자에게 메일 보내기 (답글 본문 · 첨부 포함)</label>
    <div className="anareq-settings-group">
      <div className="anareq-section-label">의뢰 템플릿</div>
      <div className="anareq-template-tabs">
        {templates.map((template) => <button type="button" key={template.id} className={template.id === activeId ? "is-active" : ""} onClick={() => setActiveId(template.id)}>{template.name}{template.id === defaultId ? " · 기본" : ""}</button>)}
        {canEdit && <Button size="sm" onClick={addTemplate}>+ 템플릿</Button>}
      </div>
      {active && <>
        {canEdit && active.id !== defaultId && <Button size="sm" onClick={() => setDefaultId(active.id)}>기본 템플릿으로 지정</Button>}
        <TemplateEditor key={active.id} template={active} products={products} reportTemplates={reportTemplates} canEdit={canEdit}
          onChange={(next) => setTemplates((list) => list.map((item) => (item.id === active.id ? next : item)))}
          onRemove={() => { setTemplates((list) => list.filter((item) => item.id !== active.id)); setActiveId(""); if (defaultId === active.id) setDefaultId(""); }} />
      </>}
    </div>
    <Button variant="primary" disabled={!canEdit || saving} onClick={save}>{saving ? "저장 중…" : "설정 저장"}</Button>
    <DcLayerSettings />
  </div>;
}

function RequestRow({ item, active, onClick }) {
  const summary = item.summary || {};
  const priority = PRIORITIES[item.priority] || PRIORITIES.normal;
  const layers = Object.keys(summary.layers || {});
  return <button type="button" className={`anareq-row${active ? " is-active" : ""}`} onClick={onClick}>
    <span className="mono strong" title={item.product}>{item.product}</span>
    <span className="anareq-row-title" title={item.title}>{item.title}</span>
    <span className="mono anareq-ellipsis" title={item.lots_text}>{item.lots_text || "-"}</span>
    <span className="anareq-ellipsis">{userLabel({ username: item.author, name: item.author_name })}</span>
    <span className="anareq-ellipsis">{item.request_type || "-"}</span>
    <span><StatusPill value={item.status} /></span>
    <span><Pill tone={priority.tone}>{priority.label}</Pill></span>
    <span className="anareq-row-progress">
      <span>ET {summary.et_measured || 0}/{summary.wafers || 0}</span>
      {layers.slice(0, 3).map((layer) => <span key={layer}>{layer}</span>)}
    </span>
    <span className="anareq-muted">{prettyTime(item.created_at)}</span>
    <span className="mono">{item.response_count || 0}</span>
  </button>;
}

export default function My_AnalysisRequest({ user }) {
  const [items, setItems] = useState([]);
  const [meta, setMeta] = useState({ status: {}, all_total: 0, facets: { products: [], requester_teams: [], authors: [] } });
  const [config, setConfig] = useState({ request_types: [], request_teams: [], templates: [], default_template_id: "", can_edit: false });
  const [products, setProducts] = useState([]);
  const [reportTemplates, setReportTemplates] = useState([]);
  // 규칙 치환은 누구나, AI 다듬기는 서버가 Template Report 관리자만 허용한다 — 버튼은 Template 을 저장할 수 있는 사람에게만.
  const canDraft = canManagePage(user, "templatereport") || canManagePage(user, "analysisrequest");
  const [selectedId, setSelectedId] = useState(() => new URLSearchParams(window.location.search).get("request") || "");
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [compose, setCompose] = useState(null); // {mode, initial}
  const [filters, setFilters] = useState({ status: "", product: "", requester_team: "", request_type: "", author: "", q: "", mine: false });

  useEffect(() => {
    Promise.allSettled([sf(`${API}/config`), sf("/api/informs/config"), sf("/api/template-report/templates")]).then(([board, inform, reports]) => {
      if (board.status === "fulfilled") setConfig(board.value);
      if (inform.status === "fulfilled") {
        const names = (inform.value.products || []).map((value) => text(value).replace(/^ML_TABLE_/i, "").trim()).filter(Boolean);
        setProducts((current) => Array.from(new Set([...current, ...names])).sort());
      }
      if (reports.status === "fulfilled") setReportTemplates((reports.value.templates || []).map((item) => ({ id: item.id, name: item.name })));
    });
  }, []);

  const loadList = useCallback(async () => {
    setLoading(true);
    try {
      const data = await sf(API + qs({ ...filters, mine: filters.mine ? "true" : "" }));
      setItems(data.requests || []);
      setMeta({ status: data.status || {}, all_total: data.all_total || 0, facets: data.facets || {} });
      setProducts((current) => Array.from(new Set([...current, ...(data.facets?.products || [])])).sort());
      setSelectedId((current) => (current && (data.requests || []).some((row) => row.id === current) ? current : ""));
    } catch (error) { toast.error(error.message || "분석의뢰 목록을 불러오지 못했습니다"); }
    finally { setLoading(false); }
  }, [filters]);

  const loadDetail = useCallback(async (id) => {
    if (!id) { setSelected(null); return; }
    try { setSelected(await sf(`${API}/${id}`)); }
    catch (error) { toast.error(error.message || "분석의뢰를 불러오지 못했습니다"); }
  }, []);

  useEffect(() => { const timer = setTimeout(loadList, filters.q ? 250 : 0); return () => clearTimeout(timer); }, [loadList]);
  useEffect(() => { loadDetail(selectedId); }, [selectedId, loadDetail]);

  const mutate = async (fn, success) => {
    setBusy(true);
    try {
      const result = await fn();
      toast.ok(success);
      if (result?.id) setSelected(result);
      await loadList();
      return result || true;
    } catch (error) { toast.error(error.message || "처리에 실패했습니다"); return false; }
    finally { setBusy(false); }
  };

  const save = async (form) => {
    const editing = compose?.mode === "edit";
    const result = await mutate(() => (editing ? putJson(`${API}/${selected.id}`, form) : postJson(API, form)), editing ? "의뢰를 수정했습니다" : "의뢰를 등록했습니다");
    if (result) { setCompose(null); if (result.id) setSelectedId(result.id); }
  };
  const refresh = async () => {
    if (!selected) return;
    setRefreshing(true);
    try { setSelected(await postJson(`${API}/${selected.id}/refresh`, {})); toast.ok("SplitTable · ET 측정을 다시 읽었습니다"); loadList(); }
    catch (error) { toast.error(error.message || "갱신하지 못했습니다"); }
    finally { setRefreshing(false); }
  };
  const copyRequest = () => {
    if (!selected) return;
    const { product, request_type, requester_team, priority, details, split_columns, report_template_id, template_id } = selected;
    setCompose({ mode: "copy", initial: { product, request_type, requester_team, priority, details, split_columns, report_template_id, template_id, lots: selected.lots_text || "", title: `${selected.title} (복제)`, copied_from: selected.id } });
  };
  const deleteRequest = async () => {
    if (!selected || !window.confirm("이 의뢰를 삭제하시겠습니까? 삭제 이력은 감사 로그에 남습니다.")) return;
    setBusy(true);
    try { await sf(`${API}/${selected.id}`, { method: "DELETE" }); toast.ok("의뢰를 삭제했습니다"); setSelected(null); setSelectedId(""); await loadList(); }
    catch (error) { toast.error(error.message || "삭제하지 못했습니다"); }
    finally { setBusy(false); }
  };
  const saveResponse = (payload, responseId = "") => mutate(
    () => (responseId ? putJson(`${API}/${selected.id}/responses/${responseId}`, payload) : postJson(`${API}/${selected.id}/responses`, payload)),
    responseId ? "답글을 수정했습니다" : "답글을 등록했습니다 — 의뢰자에게 메일을 보냅니다",
  ).then((result) => { if (result && !responseId) window.setTimeout(() => loadDetail(selected.id), 2500); return result; });
  const deleteResponse = async (responseId) => {
    if (!window.confirm("이 답글을 삭제하시겠습니까?")) return;
    await mutate(() => sf(`${API}/${selected.id}/responses/${responseId}`, { method: "DELETE" }), "답글을 삭제했습니다");
    await loadDetail(selected.id);
  };
  const saveStatus = (draft) => mutate(() => postJson(`${API}/${selected.id}/status`, draft), "처리 상태를 변경했습니다");

  const cards = useMemo(() => [
    ["", "전체", meta.all_total || 0],
    ...Object.entries(STATUSES).map(([key, item]) => [key, item.label, meta.status?.[key] || 0]),
  ], [meta]);
  const teams = useMemo(() => Array.from(new Set([...(config.request_teams || []), ...(meta.facets?.requester_teams || [])])), [config, meta]);
  const formProducts = Array.from(new Set([...products, selected?.product].filter(Boolean))).sort();

  return <PageShell layout="workboard" className="anareq-page">
    {compose ? <div className="anareq-compose">
      <div className="anareq-compose-head"><Button size="sm" onClick={() => setCompose(null)}>← 목록으로</Button><strong>{compose.mode === "edit" ? "분석의뢰 수정" : compose.mode === "copy" ? "기존 의뢰 복제" : "새 분석의뢰"}</strong></div>
      <RequestForm key={`${compose.mode}-${selected?.id || "new"}`} mode={compose.mode} initial={compose.initial} config={config} products={formProducts} busy={busy} onCancel={() => setCompose(null)} onSave={save} />
    </div> : <>
      <div className="anareq-top">
        <div className="anareq-summary">{cards.map(([key, label, count]) => <button type="button" key={label} className={filters.status === key ? "is-active" : ""} onClick={() => setFilters((prev) => ({ ...prev, status: key }))}><small>{label}</small><strong>{count}</strong></button>)}</div>
        <Button variant="primary" onClick={() => setCompose({ mode: "create", initial: null })}>+ 새 분석의뢰</Button>
      </div>
      <div className="anareq-toolbar">
        <Filter value={filters.product} onChange={(event) => setFilters((prev) => ({ ...prev, product: event.target.value }))} options={products.map((value) => ({ value, label: value }))} placeholder="전체 제품" />
        <Filter value={filters.requester_team} onChange={(event) => setFilters((prev) => ({ ...prev, requester_team: event.target.value }))} options={teams.map((value) => ({ value, label: value }))} placeholder="전체 요청 팀" />
        <Filter value={filters.request_type} onChange={(event) => setFilters((prev) => ({ ...prev, request_type: event.target.value }))} options={(config.request_types || []).map((value) => ({ value, label: value }))} placeholder="전체 유형" />
        <Filter value={filters.author} onChange={(event) => setFilters((prev) => ({ ...prev, author: event.target.value }))} options={(meta.facets?.authors || []).map((value) => ({ value: value.username, label: userLabel(value) }))} placeholder="전체 등록자" />
        <Button variant={filters.mine ? "primary" : "subtle"} onClick={() => setFilters((prev) => ({ ...prev, mine: !prev.mine }))}>내 의뢰</Button>
        <Input value={filters.q} onChange={(event) => setFilters((prev) => ({ ...prev, q: event.target.value }))} placeholder="제목 · 랏 · 의뢰 내용 · 등록자 검색" />
        <Button onClick={() => setFilters({ status: "", product: "", requester_team: "", request_type: "", author: "", q: "", mine: false })}>필터 초기화</Button>
        <Button onClick={loadList}>새로고침</Button>
      </div>
      <div className="anareq-list">
        <div className="anareq-list-header"><span>제품</span><span>제목</span><span>대상 Lot</span><span>등록자</span><span>유형</span><span>상태</span><span>우선순위</span><span>ET 측정</span><span>등록일</span><span>답글</span></div>
        {loading && <div className="anareq-muted anareq-pad">불러오는 중…</div>}
        {!loading && !items.length && <EmptyState icon="send" title="조건에 맞는 분석의뢰가 없습니다" hint="새 의뢰를 등록하거나, 비슷한 기존 의뢰를 열어 '복제해서 새 의뢰'를 쓰세요." />}
        {items.map((item) => <div className="anareq-feed-row" key={item.id}>
          <RequestRow item={item} active={selectedId === item.id} onClick={() => setSelectedId((current) => (current === item.id ? "" : item.id))} />
          {selectedId === item.id && selected?.id === item.id && <Detail item={selected} user={user} busy={busy} refreshing={refreshing} reportTemplates={reportTemplates} canDraft={canDraft}
            onRefresh={refresh} onItem={(next) => { setSelected(next); loadList(); }} onEdit={() => setCompose({ mode: "edit", initial: { ...selected, lots: selected.lots_text || "" } })} onCopy={copyRequest} onDelete={deleteRequest}
            onStatus={saveStatus} onResponse={saveResponse} onDeleteResponse={deleteResponse} />}
        </div>)}
      </div>
    </>}
    <PageGear title="분석의뢰 설정" canEdit={!!config.can_edit} position="bottom-left" width={760}>
      <BoardSettings config={config} canEdit={!!config.can_edit} products={products} reportTemplates={reportTemplates} onSaved={(next) => setConfig(next)} />
    </PageGear>
  </PageShell>;
}
