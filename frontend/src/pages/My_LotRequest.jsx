import { useCallback, useEffect, useMemo, useState } from "react";

import Modal from "../components/Modal";
import PageGear from "../components/PageGear";
import RichBoardEditor, { RichBoardContent, richTextHasContent } from "../components/RichBoardEditor";
import { toast } from "../components/Toast";
import {
  Avatar, Button, EmptyState, Filter, Input, Pill, Select, Textarea,
} from "../components/UXKit";
import { postJson, putJson, qs, sf, userLabel } from "../lib/api";

const API = "/api/lot-requests";
const LEGACY_TYPES = {
  lot_assignment: { label: "랏 배정", tone: "info" },
  hot_grade: { label: "Hot grade", tone: "pink" },
  other: { label: "기타 요청", tone: "neutral" },
};
const STATUSES = {
  registered: { label: "등록", tone: "info" },
  completed: { label: "처리완료", tone: "ok" },
  rejected: { label: "반려", tone: "bad" },
};
const PRIORITIES = {
  normal: { label: "일반", tone: "neutral" },
  high: { label: "중요", tone: "warn" },
  urgent: { label: "긴급", tone: "bad" },
};
const FALLBACK_REQUEST_TYPES = ["랏 배정", "Hot grade", "기타 요청"];
// 요청한 팀은 설정(톱니바퀴)에 등록된 값이 유일한 출처다. 기본값을 섞으면
// 관리자가 지운 팀이 다음 로드에서 되살아나므로 하드코딩 목록을 두지 않는다.
const MODULE_COLORS = {
  GATE: "#dc2626", STI: "#d97706", PC: "#2563eb", MOL: "#7c3aed", BEOL: "#0891b2",
  ET: "#16a34a", EDS: "#db2777", "S-D Epi": "#9333ea", Spacer: "#ea580c", Well: "#0d9488", 기타: "#6b7280",
};
// 배정 요청 랏/수량 칸은 없앴다 — 조건·수량·후보 랏은 모두 상세 요청 본문에 쓴다
// (본문은 그림·표를 붙여넣을 수 있어서 표로 적는 편이 원래 형식에 가깝다).
// 예전 요청에 남아 있는 target_lots 는 상세 화면에서 읽기 전용으로만 보여준다.
const EMPTY_FORM = {
  request_type: "", product: "", title: "", details: "",
  requester_team: "", priority: "normal",
};

function prettyTime(value) {
  if (!value) return "-";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value).replace("T", " ");
  return d.toLocaleString("ko-KR", {
    year: "2-digit", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

function StatusPill({ value }) {
  const item = STATUSES[value] || STATUSES.registered;
  return <Pill tone={item.tone}>{item.label}</Pill>;
}

function TypePill({ value }) {
  const item = LEGACY_TYPES[value];
  const label = item?.label || value || "기타 요청";
  const tones = ["info", "pink", "neutral", "warn", "ok"];
  let hash = 0;
  for (const char of label) hash = (hash * 31 + char.charCodeAt(0)) | 0;
  return <Pill tone={item?.tone || tones[Math.abs(hash) % tones.length]}>{label}</Pill>;
}

function moduleColor(name) {
  if (MODULE_COLORS[name]) return MODULE_COLORS[name];
  let hash = 0;
  for (const char of String(name || "기타")) hash = (hash * 31 + char.charCodeAt(0)) | 0;
  return ["#2563eb", "#7c3aed", "#db2777", "#0891b2", "#16a34a", "#ea580c"][Math.abs(hash) % 6];
}

function ModulePill({ value }) {
  if (!value) return null;
  const color = moduleColor(value);
  return <span className="lotreq-module-pill" style={{ color, borderColor: `${color}66`, background: `${color}14` }}>{value}</span>;
}

function RequestForm({ initial, busy, products, requestTypes, requestTeams, onCancel, onSave }) {
  const { target_lots: _legacyLots, ...seed } = initial || {};
  const [form, setForm] = useState({ ...EMPTY_FORM, request_type: requestTypes[0] || "랏 배정", ...seed });
  const change = (key) => (e) => setForm((prev) => ({ ...prev, [key]: e.target.value }));
  const valid = form.product.trim() && form.requester_team.trim() && form.title.trim() && richTextHasContent(form.details);
  return (
    <div className="lotreq-form">
      <div className="lotreq-grid2">
        <label>요청 유형<Select value={form.request_type} onChange={change("request_type")}>
          {Array.from(new Set([form.request_type, ...requestTypes].filter(Boolean))).map((value) => <option key={value} value={value}>{LEGACY_TYPES[value]?.label || value}</option>)}
        </Select></label>
        <label>우선순위<Select value={form.priority} onChange={change("priority")}>
          {Object.entries(PRIORITIES).map(([key, item]) => <option key={key} value={key}>{item.label}</option>)}
        </Select></label>
        <label>제품 <span className="lotreq-required">*</span><Select value={form.product} onChange={change("product")} autoFocus>
          <option value="">제품 선택</option>{products.map((product) => <option key={product} value={product}>{product}</option>)}
        </Select></label>
        <label>요청한 팀 <span className="lotreq-required">*</span><Select value={form.requester_team} onChange={change("requester_team")}>
          <option value="">{requestTeams.length ? "요청한 팀 선택" : "등록된 팀 없음 · 설정(⚙)에서 추가하세요"}</option>{Array.from(new Set([form.requester_team, ...requestTeams].filter(Boolean))).map((team) => <option key={team} value={team}>{team}</option>)}
        </Select></label>
      </div>
      <label>제목 <span className="lotreq-required">*</span><Input value={form.title} onChange={change("title")} placeholder="요청 내용을 한 줄로 요약" /></label>
      <label>상세 요청 <span className="lotreq-required">*</span>
        <RichBoardEditor value={form.details} onChange={(details) => setForm((prev) => ({ ...prev, details }))}
          uploadUrl={`${API}/upload`} minHeight={220} showCommands={false}
          placeholder="요청 배경·조건·대상 랏과 수량을 입력하세요. 이미지 또는 Excel 표는 Ctrl+V로 본문에 붙여넣을 수 있습니다."
          onUploadError={(error) => toast.error("이미지 붙여넣기 실패: " + (error?.message || error))} />
      </label>
      <div className="lotreq-actions">
        <Button onClick={onCancel}>취소</Button>
        <Button variant="primary" disabled={!valid || busy} onClick={() => onSave(form)}>{busy ? "저장 중…" : "저장"}</Button>
      </div>
    </div>
  );
}

// 배정/처리 랏 칸도 없앴다 — 배정 결과는 답글 본문에 표나 글로 적는다(예전 답글에
// 남아 있는 값은 아래에서 읽기 전용으로만 보여주고, 저장할 때 지우지 않는다).
function ResponseEditor({ initial, busy, onCancel, onSave }) {
  const [body, setBody] = useState(initial?.body || "");
  return <div className="lotreq-response-editor">
    <RichBoardEditor value={body} onChange={setBody} uploadUrl={`${API}/upload`} minHeight={120} showCommands={false}
      placeholder="답글을 입력하세요. 이미지 또는 Excel 표는 Ctrl+V로 붙여넣을 수 있습니다."
      onUploadError={(error) => toast.error("이미지 붙여넣기 실패: " + (error?.message || error))} />
    <div className="lotreq-actions">
      {onCancel && <Button size="sm" onClick={onCancel}>취소</Button>}
      <Button size="sm" variant="primary" disabled={!richTextHasContent(body) || busy} onClick={() => onSave({ body })}>{busy ? "저장 중…" : initial ? "답글 수정" : "답글 등록"}</Button>
    </div>
  </div>;
}

function History({ item }) {
  const actionLabels = {
    request_created: "요청 등록", request_updated: "요청 수정", status_changed: "상태 변경",
    response_created: "답글 등록", response_updated: "답글 수정", response_deleted: "답글 삭제",
    mail_sent: "메일 발송",
  };
  const events = item.activity_history || [];
  return <div className="lotreq-history">
    {events.map((event, index) => <div className="lotreq-history-row" key={`${event.at}-${index}`}>
      <span className="lotreq-history-dot" />
      <div>
        <div><strong>{actionLabels[event.action] || event.action || "이력"}</strong> · {event.actor || "-"} · {prettyTime(event.at)}</div>
        <div className="lotreq-muted">
          {event.action === "status_changed" && <>{event.from ? <><StatusPill value={event.from} /> <span>→</span> </> : null}<StatusPill value={event.to} /></>}
          {event.note && <span className="lotreq-history-note">{event.note}</span>}
        </div>
      </div>
    </div>)}
  </div>;
}

function RequestListItem({ item, active, onClick }) {
  const priority = PRIORITIES[item.priority] || PRIORITIES.normal;
  return <button type="button" className={`lotreq-list-item${active ? " is-active" : ""}`} style={{ "--module-color": moduleColor(item.requester_team) }} onClick={onClick}>
    <span className="lotreq-list-product" title={item.product || "제품 미지정"}>{item.product || "-"}</span>
    <span className="lotreq-list-title" title={item.title}>{item.title}</span>
    <span className="lotreq-list-author" title={userLabel({ username: item.author, name: item.author_name })}>{userLabel({ username: item.author, name: item.author_name })}</span>
    <span><ModulePill value={item.requester_team} /></span>
    <span><TypePill value={item.request_type} /></span>
    <span><StatusPill value={item.status} /></span>
    <span><Pill tone={priority.tone}>{priority.label}</Pill></span>
    <span className="lotreq-list-date">{prettyTime(item.created_at)}</span>
    <span className="lotreq-list-replies">{item.response_count || 0}</span>
  </button>;
}

function RequestMailDialog({ item, onClose, onSent }) {
  const [recipients, setRecipients] = useState([]);
  const [groups, setGroups] = useState([]);
  const [pickedUsers, setPickedUsers] = useState([]);
  const [pickedGroups, setPickedGroups] = useState([]);
  const [extraEmails, setExtraEmails] = useState("");
  const [filter, setFilter] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [preview, setPreview] = useState(null);
  const [previewing, setPreviewing] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.allSettled([sf("/api/informs/recipients"), sf("/api/mail-groups/list")]).then(([people, mailGroups]) => {
      if (people.status === "fulfilled") setRecipients(people.value.recipients || []);
      if (mailGroups.status === "fulfilled") setGroups(mailGroups.value.groups || []);
    });
  }, []);

  const payload = useMemo(() => ({
    to_users: pickedUsers,
    groups: pickedGroups,
    extra_emails: extraEmails.split(/[,;\s]+/).map((value) => value.trim()).filter((value) => value.includes("@")),
    subject: subject.trim(), body: body.trim(),
  }), [pickedUsers, pickedGroups, extraEmails, subject, body]);

  useEffect(() => {
    setPreviewing(true);
    const timer = setTimeout(() => {
      postJson(`${API}/${item.id}/mail-preview`, payload).then(setPreview).catch((reason) => setError(reason.message || "메일 미리보기를 만들지 못했습니다")).finally(() => setPreviewing(false));
    }, 250);
    return () => clearTimeout(timer);
  }, [item.id, payload]);

  const toggle = (setter) => (value) => setter((current) => current.includes(value) ? current.filter((entry) => entry !== value) : [...current, value]);
  const visibleRecipients = recipients.filter((recipient) => {
    const query = filter.trim().toLowerCase();
    return !query || [recipient.name, recipient.username, recipient.effective_email, recipient.email].some((value) => String(value || "").toLowerCase().includes(query));
  });
  const groupMemberCount = (group) => (group.members || []).length + (group.extra_emails || []).length;
  const selectedCount = new Set([...(preview?.resolved_recipients || []), ...payload.extra_emails]).size;

  const send = async () => {
    setError("");
    if (!pickedUsers.length && !pickedGroups.length && !payload.extra_emails.length) { setError("메일 수신자를 선택해주세요."); return; }
    if (!window.confirm(`현재 요청과 전체 업무 히스토리를 ${selectedCount || "선택한"}명에게 메일로 보낼까요?`)) return;
    setSending(true);
    try {
      const result = await postJson(`${API}/${item.id}/send-mail`, payload);
      toast.ok(`메일을 ${result.to?.length || 0}명에게 발송했습니다`);
      await onSent();
      onClose();
    } catch (reason) { setError(reason.message || "메일 발송에 실패했습니다"); }
    finally { setSending(false); }
  };

  return <Modal open onClose={onClose} width={1120} title="요청·처리 이력 메일 보내기">
    <div className="lotreq-mail-grid">
      <div className="lotreq-mail-targets">
        <div className="lotreq-section-title">메일 그룹 · {pickedGroups.length}개 선택</div>
        <div className="lotreq-mail-groups">{groups.map((group) => <button type="button" key={group.id || group.name} className={pickedGroups.includes(group.name) ? "is-active" : ""} onClick={() => toggle(setPickedGroups)(group.name)}>{group.name} · {groupMemberCount(group)}명</button>)}</div>
        {!groups.length && <div className="lotreq-muted">등록된 공용 메일 그룹이 없습니다.</div>}
        <div className="lotreq-mail-user-head"><div className="lotreq-section-title">개별 인원 · {pickedUsers.length}명 선택</div><Input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="이름·ID·메일 검색" /></div>
        <div className="lotreq-mail-users">{visibleRecipients.map((recipient) => <label key={recipient.username} className={pickedUsers.includes(recipient.username) ? "is-active" : ""}>
          <input type="checkbox" checked={pickedUsers.includes(recipient.username)} onChange={() => toggle(setPickedUsers)(recipient.username)} />
          <span><strong>{recipient.name || recipient.username}</strong>{recipient.name && <small>{recipient.username}</small>}</span><small>{recipient.effective_email || recipient.email || "메일 미설정"}</small>
        </label>)}</div>
        <label className="lotreq-mail-field">추가 이메일<Input value={extraEmails} onChange={(event) => setExtraEmails(event.target.value)} placeholder="외부 이메일을 콤마 또는 공백으로 구분" /></label>
        <label className="lotreq-mail-field">메일 제목<Input value={subject} onChange={(event) => setSubject(event.target.value)} placeholder={preview?.subject || `[랏 배정/요청] ${item.product} · ${item.title}`} /></label>
        <label className="lotreq-mail-field">추가 안내<Textarea rows={2} value={body} onChange={(event) => setBody(event.target.value)} placeholder="비워두면 요청 본문·PI 답변·전체 업무 히스토리만 발송합니다." /></label>
      </div>
      <div className="lotreq-mail-preview"><div className="lotreq-mail-preview-head"><strong>메일 미리보기</strong><span>수신 {selectedCount}명{previewing ? " · 갱신 중…" : ""}</span></div>
        {preview?.html ? <iframe title="랏 요청 메일 미리보기" srcDoc={preview.html} /> : <div className="lotreq-muted">미리보기를 준비하고 있습니다…</div>}
      </div>
    </div>
    {error && <div className="lotreq-mail-error">{error}</div>}
    <div className="lotreq-actions"><Button onClick={onClose}>닫기</Button><Button variant="primary" disabled={sending || previewing || !preview} onClick={send}>{sending ? "발송 중…" : `✉ ${selectedCount}명에게 발송`}</Button></div>
  </Modal>;
}

function ConfigItemsEditor({ title, items, draft, setDraft, setItems, canEdit, placeholder }) {
  const add = () => {
    const value = draft.trim();
    if (!value) return;
    if (items.some((item) => item.toLowerCase() === value.toLowerCase())) {
      toast.warn(`이미 등록된 ${title}입니다`);
      return;
    }
    setItems((current) => [...current, value]);
    setDraft("");
  };
  return <div className="lotreq-settings-group">
    <div className="lotreq-settings-label">{title}</div>
    <div className="lotreq-settings-add">
      <Input value={draft} disabled={!canEdit} onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); add(); } }} placeholder={placeholder} />
      <Button size="sm" disabled={!canEdit || !draft.trim()} onClick={add}>추가</Button>
    </div>
    <div className="lotreq-settings-items">
      {items.map((item) => <div className="lotreq-settings-item" key={item}>
        <span>{item}</span>
        {canEdit && <button type="button" aria-label={`${item} 삭제`} title="삭제" onClick={() => setItems((current) => current.filter((value) => value !== item))}>×</button>}
      </div>)}
      {!items.length && <div className="lotreq-muted">등록된 항목이 없습니다.</div>}
    </div>
  </div>;
}

function BoardSettings({ requestTypes, requestTeams, canEdit, onSaved }) {
  const [types, setTypes] = useState(requestTypes);
  const [teams, setTeams] = useState(requestTeams);
  const [typeDraft, setTypeDraft] = useState("");
  const [teamDraft, setTeamDraft] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => setTypes(requestTypes), [requestTypes]);
  useEffect(() => setTeams(requestTeams), [requestTeams]);
  const save = async () => {
    if (!types.length || !teams.length) { toast.warn("요청 유형과 요청한 팀을 각각 한 개 이상 등록해주세요"); return; }
    setSaving(true);
    try {
      const result = await postJson(`${API}/config`, { request_types: types, request_teams: teams });
      onSaved(result);
      toast.ok("랏 배정/요청 설정을 저장했습니다");
    } catch (error) { toast.error(error.message || "설정을 저장하지 못했습니다"); }
    finally { setSaving(false); }
  };
  return <div className="lotreq-settings">
    <p>입력칸에 항목을 하나씩 추가하거나 등록된 항목을 개별 삭제한 뒤 저장하세요. 저장 즉시 신규 요청과 목록 필터에 반영됩니다.</p>
    <ConfigItemsEditor title="요청 유형" items={types} draft={typeDraft} setDraft={setTypeDraft} setItems={setTypes} canEdit={canEdit} placeholder="예: 품질 확인 요청" />
    <ConfigItemsEditor title="요청한 팀" items={teams} draft={teamDraft} setDraft={setTeamDraft} setItems={setTeams} canEdit={canEdit} placeholder="예: Module" />
    <Button variant="primary" disabled={!canEdit || saving} onClick={save}>{saving ? "저장 중…" : "설정 저장"}</Button>
  </div>;
}

function IssueDetail({ selected, user, busy, statusDraft, setStatusDraft, saveStatus, setMailOpen, setEditOpen, deleteRequest, editingResponse, setEditingResponse, saveResponse, deleteResponse }) {
  // 처리 답변은 더 이상 소탭이 아니다 — 요청 내용 바로 아래에 답글로 이어 붙인다.
  // 이력만 접어 두고, 펼침 상태는 다른 요청을 열면 초기화한다.
  const [historyOpen, setHistoryOpen] = useState(false);
  useEffect(() => setHistoryOpen(false), [selected.id]);
  const replies = selected.responses || [];
  return <div className="lotreq-detail" style={{ "--module-color": moduleColor(selected.requester_team) }}>
    <div className="lotreq-detail-main">
      <div className="lotreq-detail-nav">
        <div className="lotreq-detail-heading">요청 내용</div>
        <div className="lotreq-actions">{selected.permissions?.can_mail && <Button size="sm" variant="primary" onClick={() => setMailOpen(true)}>✉ 메일</Button>}{selected.permissions?.can_edit && <><Button size="sm" onClick={() => setEditOpen(true)}>수정</Button><Button size="sm" variant="danger" onClick={deleteRequest}>삭제</Button></>}</div>
      </div>

      <div className="lotreq-section lotreq-request-content">
        <RichBoardContent html={selected.details} className="lotreq-body" />
        {selected.target_lots && <div className="lotreq-lots"><strong>대상/요청 랏</strong>{"\n"}{selected.target_lots}</div>}
      </div>

      <div className="lotreq-section">
        <div className="lotreq-section-title">답글 · {replies.length}건</div>
        {replies.map((response) => <div key={response.id} className={`lotreq-response${response.author === user?.username ? " mine" : ""}`}>
          {editingResponse === response.id ? <ResponseEditor initial={response} busy={busy} onCancel={() => setEditingResponse("")} onSave={(payload) => saveResponse(payload, response.id)} /> : <>
            <div className="lotreq-response-head"><Avatar name={response.author_name || response.author} /><div><strong>{userLabel({ username: response.author, name: response.author_name })}</strong><div className="lotreq-muted">{prettyTime(response.created_at)}{response.updated_at !== response.created_at && <span className="lotreq-edited">· 수정 {prettyTime(response.updated_at)}</span>}</div></div>
              {response.permissions?.can_edit && <div className="lotreq-actions"><Button size="sm" onClick={() => setEditingResponse(response.id)}>수정</Button><Button size="sm" variant="danger" onClick={() => deleteResponse(response.id)}>삭제</Button></div>}
            </div>
            <RichBoardContent html={response.body} className="lotreq-response-body" />
            {response.assigned_lots && <div className="lotreq-lots"><strong>배정/처리 랏</strong>{"\n"}{response.assigned_lots}</div>}
          </>}
        </div>)}
        {selected.permissions?.can_respond
          ? <ResponseEditor key={`${selected.id}-${replies.length}`} busy={busy} onSave={saveResponse} />
          : <div className="lotreq-muted">답글은 이 페이지를 위임받은 담당자와 관리자만 등록할 수 있습니다.</div>}
      </div>

      {/* 처리 상태는 답글을 다 읽은 뒤 마지막에 찍는 동작이라 답글 아래에 한 줄로 둔다. */}
      {selected.permissions?.can_process && <div className="lotreq-section">
        <div className="lotreq-process">
          <span className="lotreq-process-title">PI 처리 상태</span>
          <Select value={statusDraft.status} onChange={(event) => setStatusDraft((current) => ({ ...current, status: event.target.value }))}>{Object.entries(STATUSES).map(([key, value]) => <option key={key} value={key}>{value.label}</option>)}</Select>
          <Input value={statusDraft.note} onChange={(event) => setStatusDraft((current) => ({ ...current, note: event.target.value }))} placeholder="상태 변경 사유 (선택)" />
          <Button variant="primary" disabled={busy || statusDraft.status === selected.status} onClick={saveStatus}>상태 반영</Button>
        </div>
      </div>}

      <div className="lotreq-section">
        <button type="button" className="lotreq-history-toggle" aria-expanded={historyOpen} onClick={() => setHistoryOpen((current) => !current)}>
          {historyOpen ? "▾" : "▸"} 전체 업무 이력 · {selected.activity_history?.length || 0}건
        </button>
        {historyOpen && <History item={selected} />}
      </div>
    </div>
  </div>;
}

export default function My_LotRequest({ user }) {
  const [items, setItems] = useState([]);
  const [stats, setStats] = useState({ total: 0, status: {} });
  const [products, setProducts] = useState([]);
  const [authors, setAuthors] = useState([]);
  const [requestTypes, setRequestTypes] = useState(FALLBACK_REQUEST_TYPES);
  const [requestTeams, setRequestTeams] = useState([]);
  const [teamFacets, setTeamFacets] = useState([]);
  const [canManageConfig, setCanManageConfig] = useState(false);
  const [selectedId, setSelectedId] = useState("");
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [mailOpen, setMailOpen] = useState(false);
  const [editingResponse, setEditingResponse] = useState("");
  const [filters, setFilters] = useState({ status: "", product: "", requester_team: "", request_type: "", author: "", q: "", mine: false });
  const [statusDraft, setStatusDraft] = useState({ status: "completed", note: "" });

  useEffect(() => {
    Promise.all([sf("/api/informs/config"), sf(`${API}/config`)]).then(([informConfig, boardConfig]) => {
      const nextProducts = Array.from(new Set((informConfig.products || []).map((value) => String(value || "").replace(/^ML_TABLE_/i, "").trim()).filter(Boolean))).sort();
      setProducts((current) => Array.from(new Set([...current, ...nextProducts])).sort());
      setRequestTypes(boardConfig.request_types?.length ? boardConfig.request_types : FALLBACK_REQUEST_TYPES);
      setRequestTeams(boardConfig.request_teams || []);
      setCanManageConfig(!!boardConfig.can_edit);
    }).catch(() => {});
  }, []);

  const loadList = useCallback(async (keepSelection = true) => {
    setLoading(true);
    try {
      const query = qs({ ...filters, mine: filters.mine ? "true" : "" });
      const { status: _status, ...summaryFilters } = filters;
      const statsQuery = qs({ ...summaryFilters, mine: filters.mine ? "true" : "" });
      const [list, summary] = await Promise.all([sf(API + query), sf(API + "/stats" + statsQuery)]);
      const next = list.requests || [];
      setItems(next);
      setProducts((current) => Array.from(new Set([...current, ...(summary.facets?.products || []), ...next.map((row) => row.product).filter(Boolean)])).sort());
      // 기존 요청에 쓰인 팀은 "필터"에서만 쓴다. 설정 목록에 섞으면 지운 팀이 되살아난다.
      setTeamFacets((summary.facets?.requester_teams || []).filter(Boolean));
      setAuthors(summary.facets?.authors || []);
      setStats(summary || { total: 0, status: {} });
      setSelectedId((current) => {
        if (keepSelection && current && next.some((row) => row.id === current)) return current;
        return "";
      });
    } catch (error) {
      toast.error(error.message || "요청 목록을 불러오지 못했습니다");
    } finally { setLoading(false); }
  }, [filters]);

  const loadDetail = useCallback(async (id) => {
    if (!id) { setSelected(null); return; }
    try {
      const data = await sf(`${API}/${id}`);
      setSelected(data);
      setStatusDraft({ status: data.status === "registered" ? "completed" : data.status, note: "" });
    } catch (error) { toast.error(error.message || "요청 상세를 불러오지 못했습니다"); }
  }, []);

  useEffect(() => { const timer = setTimeout(() => loadList(), filters.q ? 250 : 0); return () => clearTimeout(timer); }, [loadList]);
  useEffect(() => { loadDetail(selectedId); }, [selectedId, loadDetail]);

  const refresh = async () => { await loadList(true); if (selectedId) await loadDetail(selectedId); };
  const mutate = async (fn, success) => {
    setBusy(true);
    try { await fn(); toast.ok(success); await refresh(); return true; }
    catch (error) { toast.error(error.message || "처리에 실패했습니다"); return false; }
    finally { setBusy(false); }
  };

  const saveRequest = async (form) => {
    const ok = await mutate(
      () => editOpen ? putJson(`${API}/${selected.id}`, form) : postJson(API, form),
      editOpen ? "요청을 수정했습니다" : "요청을 등록했습니다",
    );
    if (ok) {
      setCreateOpen(false);
      setEditOpen(false);
    }
  };
  const deleteRequest = async () => {
    if (!selected || !window.confirm("이 요청을 삭제하시겠습니까? 삭제 이력은 감사 로그에 남습니다.")) return;
    const id = selected.id;
    setBusy(true);
    try {
      await sf(`${API}/${id}`, { method: "DELETE" });
      toast.ok("요청을 삭제했습니다");
      setSelected(null); setSelectedId("");
      await loadList(false);
    } catch (error) { toast.error(error.message || "삭제하지 못했습니다"); }
    finally { setBusy(false); }
  };
  const saveStatus = () => mutate(
    () => postJson(`${API}/${selected.id}/status`, statusDraft), "처리 상태를 변경했습니다",
  );
  const saveResponse = async (payload, responseId = "") => {
    const ok = await mutate(
      () => responseId ? putJson(`${API}/${selected.id}/responses/${responseId}`, payload) : postJson(`${API}/${selected.id}/responses`, payload),
      responseId ? "답변을 수정했습니다" : "답변을 등록했습니다",
    );
    if (ok) setEditingResponse("");
  };
  const deleteResponse = (responseId) => {
    if (!window.confirm("이 답변을 삭제하시겠습니까?")) return;
    mutate(() => sf(`${API}/${selected.id}/responses/${responseId}`, { method: "DELETE" }), "답변을 삭제했습니다");
  };

  const cards = useMemo(() => [
    ["", "전체", stats.total || 0], ["registered", "등록", stats.status?.registered || 0],
    ["completed", "처리완료", stats.status?.completed || 0], ["rejected", "반려", stats.status?.rejected || 0],
  ], [stats]);

  const formProducts = Array.from(new Set([...products, selected?.product].filter(Boolean))).sort();
  // 필터는 등록된 팀 + 과거 요청에 남은 팀을 모두 보여준다(설정 목록은 오염시키지 않는다).
  const filterTeams = useMemo(() => Array.from(new Set([...requestTeams, ...teamFacets])), [requestTeams, teamFacets]);

  return <div className="lotreq-page">
    <style>{`
      .lotreq-page{padding:14px 24px 36px;max-width:1680px;margin:0 auto;color:var(--text-primary)}
      .lotreq-new-row{display:flex;justify-content:flex-end;margin:8px 0 10px}
      .lotreq-summary{display:grid;grid-template-columns:repeat(4,minmax(110px,1fr));gap:8px;margin:14px 0}
      .lotreq-summary button{display:flex;align-items:center;gap:8px;white-space:nowrap;border:1px solid var(--border);background:var(--bg-secondary);color:var(--text-primary);border-radius:7px;padding:10px 13px;text-align:left;cursor:pointer}
      .lotreq-summary button.is-active{border-color:var(--accent);box-shadow:inset 3px 0 var(--accent);background:var(--accent-glow)}
      .lotreq-summary small{color:var(--text-secondary);font-size:12px}.lotreq-summary strong{font:750 16px var(--font-mono);line-height:1}
      .lotreq-toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px}.lotreq-toolbar .input{min-width:240px;flex:1}.lotreq-toolbar select{min-width:125px}
      .lotreq-layout{display:block}.lotreq-list{border:1px solid var(--border);background:var(--bg-secondary);border-radius:8px;overflow:auto}.lotreq-feed-row{min-width:1200px;border-bottom:1px solid var(--border)}.lotreq-feed-row:last-child{border-bottom:0}
      .lotreq-list-header,.lotreq-list-item{display:grid;grid-template-columns:105px minmax(240px,2fr) 120px 110px 110px 85px 80px 145px 60px;gap:10px;align-items:center;min-width:1200px}
      .lotreq-list-header{padding:8px 14px 8px 16px;border-bottom:1px solid var(--border);background:var(--bg-tertiary);color:var(--text-secondary);font-size:11px;font-weight:800}
      .lotreq-list-item{width:100%;border:0;border-left:4px solid var(--module-color,#6b7280);background:transparent;color:inherit;padding:11px 14px 11px 12px;text-align:left;cursor:pointer}
      .lotreq-list-item:hover{background:var(--bg-hover)}.lotreq-list-item.is-active{background:var(--accent-glow);box-shadow:inset 2px 0 var(--module-color,#6b7280)}
      .lotreq-module-pill{display:inline-flex;align-items:center;border:1px solid;border-radius:999px;padding:2px 7px;font-size:11px;font-weight:800;line-height:1.35}
      .lotreq-list-top,.lotreq-list-meta,.lotreq-detail-head,.lotreq-meta,.lotreq-actions{display:flex;gap:7px;align-items:center;flex-wrap:wrap}
      .lotreq-list-product{font:750 12px var(--font-mono);color:var(--text-primary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.lotreq-list-title{font-size:13px;font-weight:780;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.lotreq-list-author,.lotreq-list-date{font-size:12px;color:var(--text-secondary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.lotreq-list-replies{text-align:center;font:750 12px var(--font-mono)}
      .lotreq-detail{border-top:1px solid var(--border);border-left:4px solid var(--module-color,#6b7280);background:var(--bg-primary);overflow:hidden;box-shadow:inset 0 8px 18px rgba(0,0,0,.035)}
      .lotreq-detail-main{padding:0 22px 20px}.lotreq-detail-head{justify-content:space-between;align-items:flex-start;border-bottom:1px solid var(--border);padding-bottom:14px}
      .lotreq-detail-nav{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:11px 0 9px;border-bottom:1px solid var(--border)}.lotreq-detail-heading{font-size:13px;font-weight:800;color:var(--accent)}.lotreq-request-content{min-height:90px}
      .lotreq-history-toggle{border:0;background:transparent;color:var(--text-secondary);font-size:13px;font-weight:800;padding:0;cursor:pointer}.lotreq-history-toggle:hover{color:var(--text-primary)}
      .lotreq-title{font-size:20px;font-weight:800;margin:8px 0 5px}.lotreq-meta{font-size:12px;color:var(--text-secondary)}
      .lotreq-section{padding:16px 0;border-bottom:1px solid var(--border)}.lotreq-section:last-child{border-bottom:0}.lotreq-section-title{font-size:13px;font-weight:800;margin-bottom:9px;color:var(--text-secondary)}
      .lotreq-body{font-size:14px;white-space:pre-wrap;line-height:1.65}.lotreq-lots{font:13px/1.6 var(--font-mono);white-space:pre-wrap;background:var(--bg-tertiary);border:1px solid var(--border);border-radius:5px;padding:9px 11px;margin-top:10px}
      .lotreq-response{padding:13px;border:1px solid var(--border);border-radius:7px;background:var(--bg-card);margin-bottom:8px}.lotreq-response.mine{border-left:3px solid var(--accent)}
      .lotreq-response-head{display:flex;align-items:center;gap:8px;margin-bottom:8px}.lotreq-response-head .lotreq-actions{margin-left:auto}.lotreq-response-body{white-space:pre-wrap;font-size:14px;line-height:1.6}
      .lotreq-muted{color:var(--text-secondary);font-size:12px;display:flex;align-items:center;gap:6px;flex-wrap:wrap}.lotreq-edited{font-size:11px;color:var(--text-secondary)}
      .lotreq-process{display:flex;align-items:center;gap:9px;background:var(--bg-tertiary);border:1px solid var(--border);border-radius:7px;padding:9px 12px}
      .lotreq-process-title{font-size:13px;font-weight:800;color:var(--text-secondary);white-space:nowrap}.lotreq-process select{width:130px;flex:none}.lotreq-process .input{flex:1;min-width:140px}
      .lotreq-form label,.lotreq-response-editor label{display:flex;flex-direction:column;gap:5px;font-size:12px;font-weight:650;color:var(--text-secondary)}
      .lotreq-history{padding-left:5px}.lotreq-history-row{display:grid;grid-template-columns:12px 1fr;gap:9px;position:relative;padding:0 0 14px;font-size:12px}.lotreq-history-row:before{content:'';position:absolute;left:4px;top:9px;bottom:-2px;border-left:1px solid var(--border)}.lotreq-history-row:last-child:before{display:none}
      .lotreq-history-dot{width:9px;height:9px;border:2px solid var(--accent);background:var(--bg-secondary);border-radius:50%;z-index:1;margin-top:3px}.lotreq-history-note{white-space:pre-wrap}.lotreq-form,.lotreq-response-editor{display:flex;flex-direction:column;gap:8px}.lotreq-grid2{display:grid;grid-template-columns:1fr 1fr;gap:8px 14px}.lotreq-actions{justify-content:flex-end}.lotreq-required{color:var(--danger)}
      .lotreq-compose{max-width:1040px;margin:0 auto;border:1px solid var(--border);background:var(--bg-secondary);border-radius:8px;overflow:hidden}.lotreq-compose-head{display:flex;align-items:center;gap:12px;padding:11px 16px;border-bottom:1px solid var(--border)}.lotreq-compose-title{font-size:16px;font-weight:850}.lotreq-compose-body{padding:12px 18px}
      .lotreq-mail-grid{display:grid;grid-template-columns:minmax(360px,440px) minmax(0,1fr);gap:14px;min-height:560px}.lotreq-mail-targets{display:flex;flex-direction:column;gap:9px;min-height:0}.lotreq-mail-groups{display:flex;gap:6px;flex-wrap:wrap}.lotreq-mail-groups button{border:1px solid var(--border);background:var(--bg-card);color:var(--text-primary);border-radius:999px;padding:5px 10px;cursor:pointer}.lotreq-mail-groups button.is-active{border-color:var(--accent);background:var(--accent);color:#fff}.lotreq-mail-user-head{display:flex;align-items:center;gap:8px}.lotreq-mail-user-head .lotreq-section-title{margin:0;flex:1}.lotreq-mail-user-head .input{width:190px}.lotreq-mail-users{height:210px;overflow:auto;border:1px solid var(--border);border-radius:6px;background:var(--bg-card)}.lotreq-mail-users label{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:8px;align-items:center;padding:6px 9px;border-bottom:1px solid var(--border);cursor:pointer;font-size:12px}.lotreq-mail-users label.is-active{background:var(--accent-glow)}.lotreq-mail-users span{display:flex;gap:6px;align-items:baseline}.lotreq-mail-users small{color:var(--text-secondary)}.lotreq-mail-field{display:flex;flex-direction:column;gap:4px;font-size:12px;font-weight:700;color:var(--text-secondary)}.lotreq-mail-preview{border:1px solid var(--border);border-radius:7px;overflow:hidden;display:flex;flex-direction:column;min-height:0}.lotreq-mail-preview-head{display:flex;justify-content:space-between;padding:9px 11px;border-bottom:1px solid var(--border);font-size:12px}.lotreq-mail-preview-head span{color:var(--text-secondary)}.lotreq-mail-preview iframe{border:0;width:100%;flex:1;background:#fff}.lotreq-mail-error{padding:8px 10px;margin:10px 0;background:var(--danger-50);color:var(--danger);border:1px solid var(--danger);border-radius:5px;font-size:12px}
      .lotreq-settings{display:flex;flex-direction:column;gap:14px}.lotreq-settings p{margin:0;color:var(--text-secondary);font-size:12px;line-height:1.6}.lotreq-settings-group{display:flex;flex-direction:column;gap:7px}.lotreq-settings-label{font-size:13px;font-weight:800}.lotreq-settings-add{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:7px;align-items:center}.lotreq-settings-items{display:flex;gap:6px;flex-wrap:wrap;min-height:30px;padding:8px;border:1px solid var(--border);border-radius:6px;background:var(--bg-primary)}.lotreq-settings-item{display:inline-flex;align-items:center;gap:5px;border:1px solid var(--border);border-radius:999px;padding:4px 5px 4px 10px;background:var(--bg-card);font-size:12px;font-weight:700}.lotreq-settings-item button{width:20px;height:20px;border:0;border-radius:50%;background:transparent;color:var(--text-secondary);font-size:16px;line-height:18px;cursor:pointer}.lotreq-settings-item button:hover{background:var(--danger-50);color:var(--danger)}
      @media(max-width:900px){.lotreq-page{padding:14px}.lotreq-summary{grid-template-columns:repeat(2,1fr)}.lotreq-process{flex-wrap:wrap}.lotreq-grid2,.lotreq-mail-grid{grid-template-columns:1fr}.lotreq-mail-preview{min-height:420px}.lotreq-detail-main{padding:0 14px 14px}}
    `}</style>
    {!createOpen && !editOpen && <div className="lotreq-new-row"><Button className="lotreq-new" variant="primary" onClick={() => setCreateOpen(true)}>+ 새 요청</Button></div>}

    {(createOpen || editOpen) ? <div className="lotreq-compose">
      <div className="lotreq-compose-head"><Button size="sm" onClick={() => { setCreateOpen(false); setEditOpen(false); }}>← 목록으로</Button><div className="lotreq-compose-title">{editOpen ? "요청 수정" : "신규 요청 생성"}</div></div>
      <div className="lotreq-compose-body"><RequestForm key={editOpen ? selected?.id : "new-request"} initial={editOpen ? selected : {}} busy={busy} products={formProducts} requestTypes={requestTypes} requestTeams={requestTeams} onCancel={() => { setCreateOpen(false); setEditOpen(false); }} onSave={saveRequest} /></div>
    </div> : <>

    <div className="lotreq-summary">{cards.map(([key, label, count]) => <button key={label} className={filters.status === key ? "is-active" : ""} onClick={() => setFilters((f) => ({ ...f, status: key }))}><small>{label}</small><strong>{count}</strong></button>)}</div>
    <div className="lotreq-toolbar">
      <Filter value={filters.product} onChange={(e) => setFilters((f) => ({ ...f, product: e.target.value }))} options={products.map((value) => ({ value, label: value }))} placeholder="전체 제품" />
      <Filter value={filters.requester_team} onChange={(e) => setFilters((f) => ({ ...f, requester_team: e.target.value }))} options={filterTeams.map((value) => ({ value, label: value }))} placeholder="전체 요청 팀" />
      <Filter value={filters.request_type} onChange={(e) => setFilters((f) => ({ ...f, request_type: e.target.value }))} options={requestTypes.map((value) => ({ value, label: LEGACY_TYPES[value]?.label || value }))} placeholder="전체 유형" />
      <Filter value={filters.author} onChange={(e) => setFilters((f) => ({ ...f, author: e.target.value }))} options={authors.map((value) => ({ value: value.username, label: userLabel(value) }))} placeholder="전체 등록자" />
      <Button variant={filters.mine ? "primary" : "subtle"} onClick={() => setFilters((f) => ({ ...f, mine: !f.mine }))}>내 요청</Button>
      {/* 검색은 목록을 좁히는 마지막 단계라 필터 오른쪽에 둔다(남는 폭을 검색이 차지). */}
      <Input value={filters.q} onChange={(e) => setFilters((f) => ({ ...f, q: e.target.value }))} placeholder="이슈, 랏, 상세 요청 내용, 등록자 검색" />
      <Button onClick={() => setFilters({ status: "", product: "", requester_team: "", request_type: "", author: "", q: "", mine: false })}>필터 초기화</Button>
      <Button onClick={() => loadList()}>새로고침</Button>
    </div>

    <div className="lotreq-layout">
      <div className="lotreq-list">
        <div className="lotreq-list-header"><span>제품</span><span>이슈</span><span>등록자</span><span>요청 팀</span><span>요청 유형</span><span>상태</span><span>우선순위</span><span>등록일</span><span>답글</span></div>
        {loading && <div className="lotreq-muted" style={{ padding: 24 }}>불러오는 중…</div>}
        {!loading && items.length === 0 && <EmptyState icon="📨" title="조건에 맞는 요청이 없습니다" hint="새 요청을 등록하거나 검색 조건을 바꿔보세요." />}
        {items.map((item) => <div className="lotreq-feed-row" key={item.id}>
          <RequestListItem item={item} active={selectedId === item.id} onClick={() => setSelectedId((current) => current === item.id ? "" : item.id)} />
          {selectedId === item.id && selected?.id === item.id && <IssueDetail selected={selected} user={user} busy={busy} statusDraft={statusDraft} setStatusDraft={setStatusDraft} saveStatus={saveStatus} setMailOpen={setMailOpen} setEditOpen={setEditOpen} deleteRequest={deleteRequest} editingResponse={editingResponse} setEditingResponse={setEditingResponse} saveResponse={saveResponse} deleteResponse={deleteResponse} />}
        </div>)}
      </div>

    </div>
    </>}
    {mailOpen && selected && <RequestMailDialog item={selected} onClose={() => setMailOpen(false)} onSent={refresh} />}
    <PageGear title="랏 배정/요청 설정" canEdit={canManageConfig} position="bottom-left">
      <BoardSettings requestTypes={requestTypes} requestTeams={requestTeams} canEdit={canManageConfig} onSaved={(config) => { setRequestTypes(config.request_types || FALLBACK_REQUEST_TYPES); setRequestTeams(config.request_teams || []); }} />
    </PageGear>
  </div>;
}
