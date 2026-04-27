/* My_Meeting.jsx v8.8.3 — 회의관리 (반복 + 차수 + 아젠다 + 달력 selective push).
   - 좌측: 회의 목록 (status 필터 + 검색).
   - 우측 상단: 회의 메타 (제목/주관자/반복/카테고리/상태).
   - 우측 가운데: 차수(세션) 탭. "+ 차수 추가" 가능.
   - 우측 하단: 선택된 차수의 아젠다 + 회의록 + 액션아이템.
   - 아젠다 link 클릭: 새 창(target=_blank).
   - 액션아이템 옆 📅 버튼: 달력 selective push/unpush. 등록됨 표시 + 등록 유저/시간.
*/
import { useEffect, useMemo, useState, useRef } from "react";
import PageGear from "../components/PageGear";
import { authSrc, sf, postJson, userLabel } from "../lib/api";

const API = "/api/meetings";

const SESS_STATUS_LABEL = {
  scheduled: "예정",
  in_progress: "진행중",
  completed: "완료",
  cancelled: "취소",
};
const SESS_STATUS_COLOR = {
  scheduled: "#3b82f6",
  in_progress: "#f59e0b",
  completed: "#22c55e",
  cancelled: "#6b7280",
};
const MEET_STATUS_LABEL = { active: "활성", archived: "보관", cancelled: "취소" };
const WEEKDAY_LABEL = ["월", "화", "수", "목", "금", "토", "일"];
const WEEKDAY_ORDER = [0, 1, 2, 3, 4, 5, 6]; // Mon..Sun (Python weekday 기준)

function dtPretty(s) { if (!s) return ""; return s.replace("T", " ").slice(0, 16); }
function dtForInput(s) { if (!s) return ""; return s.slice(0, 16); }
function isUrl(s) { return !!s && /^https?:\/\//i.test(s); }
function withTrackerImageAuth(html) {
  if (!html || typeof html !== "string") return html;
  return html.replace(/\/api\/tracker\/image\?name=([^"'&\s>]+)/g, (m) => authSrc(m));
}
function trackerImageName(img) {
  if (!img) return "";
  if (typeof img === "string") return img.trim();
  return String(img.name || img.filename || img.file_name || img.url || img.src || "").trim();
}
function trackerImages(iss) {
  return Array.isArray(iss?.images) ? iss.images.map(trackerImageName).filter(Boolean) : [];
}
function trackerImageSrc(img) {
  const name = trackerImageName(img);
  if (!name) return "";
  if (/^(data:image\/|https?:\/\/)/i.test(name)) return name;
  if (name.startsWith("/api/tracker/image")) return authSrc(name);
  return authSrc(`/api/tracker/image?name=${encodeURIComponent(name)}`);
}
function issueSnapshot(iss, fallbackId = "") {
  const lots = Array.isArray(iss?.lots) ? iss.lots : [];
  const links = Array.isArray(iss?.links) ? iss.links : [];
  const images = trackerImages(iss);
  return {
    issue_id: iss?.id || fallbackId,
    id: iss?.id || fallbackId,
    title: iss?.title || "",
    status: iss?.status || "",
    category: iss?.category || "",
    priority: iss?.priority || "",
    username: iss?.username || "",
    description: iss?.description || "",
    description_html: iss?.description_html || "",
    images,
    image_count: images.length,
    links,
    lots,
    lot_count: lots.length,
    comment_count: Array.isArray(iss?.comments) ? iss.comments.length : Number(iss?.comment_count || 0),
    updated_at: iss?.updated_at || iss?.created || iss?.timestamp || "",
  };
}
function lotWfText(lot) {
  const product = lot?.product || lot?.monitor_prod || "-";
  const root = lot?.root_lot_id || lot?.lot_id || "-";
  const wafer = lot?.wafer_id || "-";
  const step = lot?.current_step ? ` · ${lot.current_step}${lot.current_function_step ? ` > ${lot.current_function_step}` : ""}` : "";
  return `${product} · ${root} · WF ${wafer}${step}`;
}

export default function My_Meeting({ user }) {
  const [meetings, setMeetings] = useState([]);
  const [categories, setCategories] = useState([]);
  const [allGroups, setAllGroups] = useState([]);       // v8.7.6: 그룹 담당자 선택
  const [mailGroups, setMailGroups] = useState([]);     // v8.7.7: 공용 메일 그룹
  const [mailRecipients, setMailRecipients] = useState([]); // username+email
  const [mgEditor, setMgEditor] = useState(false);      // v8.7.7: 메일 그룹 관리 모달
  const [sendDialog, setSendDialog] = useState(null);   // v8.7.7: 이미 저장된 차수 재발송 다이얼로그
  const [viewMode, setViewMode] = useState("list");     // v8.7.6: list | gantt
  const [loading, setLoading] = useState(false);
  const [filterStatus, setFilterStatus] = useState("");
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState(null);
  const [selectedSid, setSelectedSid] = useState(null);

  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState({
    title: "", owner: "", first_scheduled_at: "",
    recurrence: { type: "none", count_per_week: 1, weekday: [], note: "" },
    category: "",
    group_ids: [],  /* v8.8.3: 공개범위 group_ids FE picker */
  });
  const [editingMeta, setEditingMeta] = useState(false);
  const [metaDraft, setMetaDraft] = useState(null);
  const [agendaDraft, setAgendaDraft] = useState({ title: "", description: "", link: "", owner: "", issue_ref: null });
  const [editingAgendaId, setEditingAgendaId] = useState(null);
  const [agendaEditDraft, setAgendaEditDraft] = useState(null);
  const [issueDetails, setIssueDetails] = useState({});
  // v8.8.13: 이슈 가져오기 picker — 회의 group_ids 와 교집합 있는 이슈만 후보.
  const [issuePickerOpen, setIssuePickerOpen] = useState(false);
  const [issuePickerList, setIssuePickerList] = useState([]);
  const [issuePickerSearch, setIssuePickerSearch] = useState("");
  const [issuePickerBusy, setIssuePickerBusy] = useState(false);
  // v8.8.13: 회의록 공동 작성 append.
  const [appendText, setAppendText] = useState("");
  const [appendBusy, setAppendBusy] = useState(false);
  const [minutesDraft, setMinutesDraft] = useState(null);
  const [editingMinutes, setEditingMinutes] = useState(false);
  // v8.8.6: SSE 내부 ref — useEffect 가 editingMinutes 상태를 stale 없이 참조.
  const editingMinutesRef = useRef(false);
  useEffect(() => { editingMinutesRef.current = editingMinutes; }, [editingMinutes]);

  const isAdmin = user?.role === "admin";
  const me = user?.username || "";

  // v8.8.3: 공용 메일그룹(/api/mail-groups) + 일반 그룹(/api/groups) 병합.
  // id 충돌 방지: mail-groups → "mg:<id>", 일반 groups → "grp:<id>".
  const reloadMailGroups = () => {
    Promise.all([
      sf("/api/mail-groups/list").catch(() => ({ groups: [] })),
      sf("/api/groups/list").catch(() => ({ groups: [] })),
    ]).then(([mgData, grpData]) => {
      const mgItems = (mgData.groups || []).map(g => ({
        ...g,
        id: `mg:${g.id}`,
        _source: "mail_groups",
      }));
      const grpItems = (grpData.groups || []).map(g => ({
        ...g,
        id: `grp:${g.id}`,
        _source: "groups",
        // groups 에는 extra_emails 없음 — 빈 배열 기본값
        extra_emails: g.extra_emails || [],
      }));
      // mail-groups 우선 → 일반 groups 순서로 정렬 (이름 asc)
      const merged = [...mgItems, ...grpItems].sort((a, b) =>
        (a.name || "").localeCompare(b.name || "", "ko")
      );
      setMailGroups(merged);
    });
  };

  const reload = () => {
    setLoading(true);
    sf(`${API}/list${filterStatus ? `?status=${encodeURIComponent(filterStatus)}` : ""}`)
      .then(d => setMeetings(d.meetings || []))
      .catch(() => setMeetings([]))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    sf("/api/calendar/categories").then(d => setCategories(d.categories || [])).catch(() => {});
    // v8.7.6: 액션아이템 그룹 담당자용 그룹 목록 + 메일 수신자 목록
    sf("/api/groups/list").then(d => setAllGroups(d.groups || [])).catch(() => {});
    sf("/api/informs/recipients").then(d => setMailRecipients(d.recipients || [])).catch(() => {});
    reloadMailGroups();
  }, []);
  useEffect(() => { reload(); /* eslint-disable-next-line */ }, [filterStatus]);

  // v8.8.6: 회의록 동시편집 SSE 구독 — 선택된 회의가 바뀔 때 재연결.
  //   다른 사람이 `/minutes/save` 하면 `update` 이벤트 수신 → 현재 편집 중 아니면 자동 reload,
  //   편집 중이면 "외부 저장 알림" 배너만 표시 + 수동 병합 유도.
  const [externalUpdate, setExternalUpdate] = useState(null);
  useEffect(() => {
    if (!selectedId) return;
    const tk = (() => {
      try { return (JSON.parse(localStorage.getItem("hol_user") || "{}")?.token) || ""; }
      catch { return ""; }
    })();
    const url = `/api/meetings/stream?meeting_id=${encodeURIComponent(selectedId)}${tk ? `&t=${encodeURIComponent(tk)}` : ""}`;
    let es;
    try { es = new EventSource(url); } catch { return; }
    es.addEventListener("update", (ev) => {
      try {
        const d = JSON.parse(ev.data || "{}");
        setExternalUpdate(d);
        // 편집 중 아니면 즉시 reload
        if (!editingMinutesRef.current) reload();
      } catch {}
    });
    es.onerror = () => { /* silent — 네트워크 일시 끊김 */ };
    return () => { try { es.close(); } catch {} };
    /* eslint-disable-next-line */
  }, [selectedId]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return meetings;
    return meetings.filter(m => {
      const hay = [
        m.title || "", m.owner || "", m.status || "", m.category || "",
        ...(m.sessions || []).flatMap(s => [
          ...(s.agendas || []).map(a => `${a.title || ""} ${a.owner || ""} ${a.description || ""}`),
          s.minutes?.body || "",
          ...((s.minutes?.decisions) || []),
          ...((s.minutes?.action_items) || []).map(a => a.text || ""),
        ]),
      ].join(" ").toLowerCase();
      return hay.includes(q);
    });
  }, [meetings, search]);

  const selected = useMemo(() => meetings.find(m => m.id === selectedId) || null, [meetings, selectedId]);
  const selectedSession = useMemo(() => {
    if (!selected) return null;
    const sessions = selected.sessions || [];
    if (!sessions.length) return null;
    const byId = sessions.find(s => s.id === selectedSid);
    return byId || sessions[sessions.length - 1];
  }, [selected, selectedSid]);

  useEffect(() => {
    if (!selected) return;
    const sessions = selected.sessions || [];
    if (sessions.length && !sessions.find(s => s.id === selectedSid)) {
      setSelectedSid(sessions[sessions.length - 1].id);
    }
  }, [selected, selectedSid]);

  const canEditMeta = (m) => isAdmin || (m && m.owner === me);
  const canEditMinutes = canEditMeta;
  const canEditAgenda = (m, a) => isAdmin || (m && m.owner === me) || (a && a.owner === me);
  const loadIssueDetail = (issueId) => {
    const iid = String(issueId || "").trim();
    if (!iid || issueDetails[iid]) return;
    sf(`/api/tracker/issue?issue_id=${encodeURIComponent(iid)}`)
      .then(d => setIssueDetails(prev => ({ ...prev, [iid]: d.issue || {} })))
      .catch(() => {});
  };
  useEffect(() => {
    (selectedSession?.agendas || []).forEach(a => {
      const iid = a?.issue_ref?.issue_id || "";
      if (iid) loadIssueDetail(iid);
    });
  }, [selectedSession]);

  const categoryColor = (name) => (categories.find(c => c.name === name) || {}).color || "#6b7280";

  // ── Create new meeting ──
  const toggleWeekday = (arr, d) => arr.includes(d) ? arr.filter(x => x !== d) : [...arr, d].sort();
  const submitCreate = () => {
    const t = draft.title.trim();
    if (!t) { alert("회의 제목을 입력하세요"); return; }
    postJson(`${API}/create`, {
      title: t,
      owner: (draft.owner || me).trim(),
      first_scheduled_at: draft.first_scheduled_at || "",
      recurrence: {
        type: draft.recurrence.type || "none",
        count_per_week: Number(draft.recurrence.count_per_week) || 0,
        weekday: draft.recurrence.weekday || [],
        note: draft.recurrence.note || "",
      },
      category: draft.category || "",
      group_ids: draft.group_ids || [],  /* v8.8.3 */
    }).then(d => {
      setCreating(false);
      setDraft({
        title: "", owner: "", first_scheduled_at: "",
        recurrence: { type: "none", count_per_week: 1, weekday: [], note: "" },
        category: "",
        group_ids: [],  /* v8.8.3 */
      });
      reload();
      setSelectedId(d.meeting?.id || null);
      setSelectedSid(d.meeting?.sessions?.[0]?.id || null);
    }).catch(e => alert(e.message || "생성 실패"));
  };

  // ── Meeting meta edit ──
  const startEditMeta = () => {
    if (!selected) return;
    setMetaDraft({
      title: selected.title || "",
      owner: selected.owner || "",
      status: selected.status || "active",
      category: selected.category || "",
      recurrence: { ...(selected.recurrence || { type: "none", count_per_week: 0, weekday: [], note: "" }) },
      group_ids: Array.isArray(selected.group_ids) ? [...selected.group_ids] : [],  /* v8.8.3 */
    });
    setEditingMeta(true);
  };
  const submitEditMeta = () => {
    if (!selected || !metaDraft) return;
    postJson(`${API}/update`, {
      id: selected.id,
      title: metaDraft.title,
      owner: metaDraft.owner,
      status: metaDraft.status,
      category: metaDraft.category,
      recurrence: {
        type: metaDraft.recurrence.type || "none",
        count_per_week: Number(metaDraft.recurrence.count_per_week) || 0,
        weekday: metaDraft.recurrence.weekday || [],
        note: metaDraft.recurrence.note || "",
      },
      group_ids: metaDraft.group_ids || [],  /* v8.8.3 */
    }).then(() => { setEditingMeta(false); setMetaDraft(null); reload(); })
      .catch(e => alert(e.message || "저장 실패"));
  };
  const removeMeeting = () => {
    if (!selected) return;
    if (!confirm(`회의 "${selected.title}" 을(를) 삭제할까요? 연동된 달력 이벤트도 제거됩니다.`)) return;
    sf(`${API}/delete?id=${encodeURIComponent(selected.id)}`, { method: "POST" })
      .then(() => { setSelectedId(null); setSelectedSid(null); reload(); })
      .catch(e => alert(e.message));
  };

  // ── Sessions ──
  const addSession = () => {
    if (!selected) return;
    const dtStr = prompt("새 차수 예정 일시 (YYYY-MM-DD HH:MM, 공란 가능):", "");
    const sched = (dtStr || "").trim().replace(" ", "T");
    postJson(`${API}/session/add`, { meeting_id: selected.id, scheduled_at: sched })
      .then(d => { reload(); setSelectedSid(d.session?.id || null); })
      .catch(e => alert(e.message || "차수 추가 실패"));
  };
  const updateSessionMeta = (patch) => {
    if (!selected || !selectedSession) return;
    postJson(`${API}/session/update`, {
      meeting_id: selected.id, session_id: selectedSession.id, ...patch,
    }).then(() => reload()).catch(e => alert(e.message));
  };
  const removeSession = () => {
    if (!selected || !selectedSession) return;
    if ((selected.sessions || []).length <= 1) { alert("마지막 차수는 삭제할 수 없습니다. 회의 자체를 삭제하세요."); return; }
    if (!confirm(`${selectedSession.idx}차 차수를 삭제할까요?`)) return;
    sf(`${API}/session/delete?meeting_id=${encodeURIComponent(selected.id)}&session_id=${encodeURIComponent(selectedSession.id)}`,
       { method: "POST" })
      .then(() => { setSelectedSid(null); reload(); })
      .catch(e => alert(e.message));
  };

  // v8.8.13: 이슈 가져오기 — 회의 group_ids 와 교집합 있는 이슈만 후보로 노출.
  //   admin 은 전체. 선택 시 title/description/owner/link 를 agendaDraft 에 채움.
  const openIssuePicker = () => {
    if (!selected) return;
    setIssuePickerOpen(true); setIssuePickerBusy(true); setIssuePickerSearch("");
    sf("/api/tracker/issues?limit=500").then(d => {
      const meetGids = new Set((selected.group_ids || []).map(String));
      const list = (d.issues || []).filter(iss => {
        if (isAdmin) return true;
        if (meetGids.size === 0) return true; // 회의가 전체공개면 이슈도 전체 후보
        const iGids = (iss.group_ids || []).map(String);
        if (iGids.length === 0) return true;  // 이슈가 전체공개면 후보
        return iGids.some(g => meetGids.has(g));
      });
      setIssuePickerList(list); setIssuePickerBusy(false);
    }).catch(e => { setIssuePickerBusy(false); alert("이슈 목록 로드 실패: " + (e.message || e)); });
  };
  const attachIssueToAgenda = (issueId) => {
    // 상세로 description/links 채움.
    sf(`/api/tracker/issue?issue_id=${encodeURIComponent(issueId)}`).then(d => {
      const iss = d.issue || {};
      const snapshot = issueSnapshot(iss, issueId);
      // description 은 HTML 이므로 plain text 로 strip.
      const tmp = document.createElement("div");
      tmp.innerHTML = iss.description_html || iss.description || "";
      const plain = (tmp.textContent || "").trim();
      const firstLink = Array.isArray(iss.links) ? (iss.links.find(l => /^https?:\/\//i.test(l)) || iss.links[0] || "") : "";
      setAgendaDraft({
        title: iss.title || "",
        description: plain,
        link: firstLink || "",
        owner: iss.username || "",
        issue_ref: snapshot,
      });
      setIssueDetails(prev => ({ ...prev, [String(iss.id || issueId)]: iss }));
      setIssuePickerOpen(false);
    }).catch(e => alert("이슈 상세 로드 실패: " + (e.message || e)));
  };

  // ── Agenda CRUD ──
  const addAgenda = () => {
    if (!selected || !selectedSession) return;
    const t = agendaDraft.title.trim();
    if (!t) { alert("아젠다 제목을 입력하세요"); return; }
    postJson(`${API}/agenda/add`, {
      meeting_id: selected.id, session_id: selectedSession.id,
      title: t,
      description: agendaDraft.description,
      link: agendaDraft.link,
      issue_ref: agendaDraft.issue_ref || null,
      // v8.8.16: 빈 채로 저장하면 BE(meetings.add_agenda) 가 세션 유저명으로 자동 채움.
      owner: (agendaDraft.owner || "").trim(),
    }).then(() => {
      setAgendaDraft({ title: "", description: "", link: "", owner: "", issue_ref: null });
      reload();
    }).catch(e => alert(e.message || "추가 실패"));
  };
  const startEditAgenda = (a) => {
    setEditingAgendaId(a.id);
    setAgendaEditDraft({ title: a.title || "", description: a.description || "", link: a.link || "", owner: a.owner || "", issue_ref: a.issue_ref || null });
  };
  const submitEditAgenda = () => {
    if (!selected || !selectedSession || !editingAgendaId || !agendaEditDraft) return;
    postJson(`${API}/agenda/update`, {
      meeting_id: selected.id, session_id: selectedSession.id, agenda_id: editingAgendaId,
      title: agendaEditDraft.title,
      description: agendaEditDraft.description,
      link: agendaEditDraft.link,
      owner: agendaEditDraft.owner,
      issue_ref: agendaEditDraft.issue_ref || null,
    }).then(() => { setEditingAgendaId(null); setAgendaEditDraft(null); reload(); })
      .catch(e => alert(e.message || "수정 실패"));
  };
  const removeAgenda = (a) => {
    if (!selected || !selectedSession) return;
    if (!confirm(`아젠다 "${a.title}" 을(를) 삭제할까요?`)) return;
    sf(`${API}/agenda/delete?meeting_id=${encodeURIComponent(selected.id)}&session_id=${encodeURIComponent(selectedSession.id)}&agenda_id=${encodeURIComponent(a.id)}`,
      { method: "POST" })
      .then(() => reload()).catch(e => alert(e.message));
  };

  // ── Minutes ──
  const startEditMinutes = () => {
    if (!selectedSession) return;
    const m = selectedSession.minutes || {};
    setMinutesDraft({
      body: m.body || "",
      decisions: (m.decisions || []).map(d => typeof d === "string" ? { text: d, due: "" } : { ...d }),
      action_items: (m.action_items || []).map(a => ({ ...a, group_ids: a.group_ids || [] })),
      // v8.7.6: 메일 옵션
      send_mail: false,
      mail_to_users: [],
      mail_groups: [],
      mail_group_ids: [],   // v8.7.7: 공용 메일 그룹 선택
      mail_to: "",
      mail_subject: "",
      // v8.8.16: 공동 작성 본문과 독립된 메일 전용 본문. 빈 채로 두면 메일 본문 섹션 생략.
      mail_body: "",
      // v8.8.15: OT-lite — 편집 시작 시점의 서버 rev 스냅샷. 저장 때 base_rev 로 전송.
      base_rev: Number(m.rev || 0),
    });
    setEditingMinutes(true);
  };
  // v8.7.5: decisions 는 {id, text, due, calendar_*} 객체. 편집 시 text 만 다룸.
  const addDecision = () => setMinutesDraft(d => ({ ...d, decisions: [...d.decisions, { text: "", due: "" }] }));
  const updDecision = (i, k, v) => setMinutesDraft(d => { const n = d.decisions.slice(); const prev = typeof n[i] === "string" ? { text: n[i] } : { ...n[i] }; prev[k] = v; n[i] = prev; return { ...d, decisions: n }; });
  const delDecision = (i) => setMinutesDraft(d => ({ ...d, decisions: d.decisions.filter((_, j) => j !== i) }));
  const decText = (d) => typeof d === "string" ? d : (d?.text || "");
  const addAction = () => setMinutesDraft(d => ({ ...d, action_items: [...d.action_items, { text: "", owner: "", due: "" }] }));
  const updAction = (i, k, v) => setMinutesDraft(d => { const n = d.action_items.slice(); n[i] = { ...n[i], [k]: v }; return { ...d, action_items: n }; });
  const delAction = (i) => setMinutesDraft(d => ({ ...d, action_items: d.action_items.filter((_, j) => j !== i) }));
  // v8.8.13: 본문 공동 작성 append / 삭제.
  const submitAppend = () => {
    if (!selected || !selectedSession) return;
    const t = (appendText || "").trim();
    if (!t) return;
    setAppendBusy(true);
    postJson(`${API}/minutes/append`, {
      meeting_id: selected.id, session_id: selectedSession.id, text: t,
    }).then(() => {
      setAppendText(""); setAppendBusy(false); reload();
    }).catch(e => {
      setAppendBusy(false);
      alert(e.message || "추가 실패 (그룹 멤버만 가능합니다)");
    });
  };
  const deleteAppend = (appendId) => {
    if (!selected || !selectedSession) return;
    if (!window.confirm("이 추가글을 삭제할까요? (작성자 본인 또는 주관자만 가능)")) return;
    postJson(`${API}/minutes/append/delete`, {
      meeting_id: selected.id, session_id: selectedSession.id, append_id: appendId,
    }).then(() => reload()).catch(e => alert(e.message || "삭제 실패"));
  };

  const submitMinutes = () => {
    if (!selected || !selectedSession || !minutesDraft) return;
    const mailTo = (minutesDraft.mail_to || "")
      .split(/[,;\s]+/).map(s => s.trim()).filter(s => s && s.includes("@"));
    postJson(`${API}/minutes/save`, {
      meeting_id: selected.id, session_id: selectedSession.id,
      body: minutesDraft.body,
      decisions: minutesDraft.decisions,
      action_items: minutesDraft.action_items.map(a => ({
        id: a.id || "", text: a.text, owner: a.owner, due: a.due, group_ids: a.group_ids || [],
      })),
      send_mail: !!minutesDraft.send_mail,
      mail_to_users: minutesDraft.mail_to_users || [],
      mail_groups: minutesDraft.mail_groups || [],
      mail_group_ids: minutesDraft.mail_group_ids || [],
      mail_to: mailTo,
      mail_subject: minutesDraft.mail_subject || "",
      // v8.8.15: OT-lite — 편집 시작 시점의 rev 을 함께 전송. 서버 rev 과 다르면 409.
      base_rev: Number(minutesDraft.base_rev || 0),
      // v8.8.16: 메일 전용 본문 — BE 가 minutes.body 대신 이걸 사용. 빈 문자열이면 본문 섹션 생략.
      mail_body: minutesDraft.mail_body || "",
    }).then(r => {
      setEditingMinutes(false); setMinutesDraft(null); reload();
      // v8.7.9: surface calendar sync failure loudly — v8.7.8 silently swallowed it.
      if (r && r.calendar_sync && r.calendar_sync.ok === false) {
        alert(`달력 auto-sync 실패: ${r.calendar_sync.error || "unknown"}\n(결정사항·액션아이템이 달력에 반영되지 않았습니다.)`);
      }
      if (r && r.mail) {
        if (r.mail.ok) alert(`메일 발송 완료${r.mail.dry_run ? " (dry-run)" : ""} · ${(r.mail.to || []).length}명`);
        else alert(`메일 발송 실패: ${r.mail.error || "unknown"}`);
      }
    }).catch(e => {
      // v8.8.15: 409 conflict — 다른 사람이 먼저 저장. 서버 최신본 보여주고 병합 옵션 제공.
      let detail = null;
      try { detail = (e && e.detail) || (e && e.body && e.body.detail) || null; } catch {}
      const msg = String(e?.message || e || "");
      const isConflict = (e?.status === 409) || /409/.test(msg) || /minutes_rev_conflict/.test(msg);
      if (isConflict) {
        const cur = (detail && detail.current_body) || "";
        const author = (detail && detail.current_author) || "다른 사용자";
        const serverRev = (detail && detail.server_rev) || 0;
        const keep = window.confirm(
          `⚠ 동시편집 감지\n\n${author} 님이 먼저 저장했습니다 (rev ${serverRev}).\n\n[확인] = 내 편집을 유지하고 최신 rev 로 재시도 저장 (상대 변경 덮어쓰기)\n[취소] = 내 편집 취소하고 최신본 불러오기`
        );
        if (keep) {
          // 최신 rev 으로 갱신 후 재시도
          setMinutesDraft(d => d ? { ...d, base_rev: Number(serverRev) } : d);
          alert("base_rev 을 최신으로 갱신했습니다. 다시 [저장] 버튼을 눌러주세요.");
        } else {
          setEditingMinutes(false); setMinutesDraft(null); reload();
        }
        return;
      }
      alert(msg || "저장 실패");
    });
  };

  // v8.7.5: 결정사항 단위 달력 push/unpush
  // v8.7.6: 결정사항은 별도 마감일을 받지 않음 — 무조건 해당 회의 세션 날짜로 달력에 등록.
  const pushDecision = (d) => {
    if (!selected || !selectedSession || !d?.id) {
      alert("결정사항을 먼저 저장해야 달력에 등록할 수 있습니다.");
      return;
    }
    const due = selectedSession.scheduled_at
      ? selectedSession.scheduled_at.slice(0, 10)
      : new Date().toISOString().slice(0, 10);
    postJson(`${API}/decision/push`, {
      meeting_id: selected.id, session_id: selectedSession.id,
      decision_id: d.id, due,
    }).then(() => reload()).catch(e => alert(e.message || "달력 등록 실패"));
  };
  const unpushDecision = (d) => {
    if (!selected || !selectedSession || !d?.id) return;
    if (!confirm("결정사항의 달력 등록을 해제할까요?")) return;
    postJson(`${API}/decision/unpush`, {
      meeting_id: selected.id, session_id: selectedSession.id, decision_id: d.id,
    }).then(() => reload()).catch(e => alert(e.message || "해제 실패"));
  };

  const pushAction = (ai) => {
    if (!selected || !selectedSession || !ai?.id) return;
    if (!ai.text || !ai.due) { alert("액션아이템에 내용과 마감일(due)이 모두 필요합니다."); return; }
    postJson(`${API}/action/push`, {
      meeting_id: selected.id, session_id: selectedSession.id, action_item_id: ai.id,
    }).then(() => reload()).catch(e => alert(e.message || "달력 등록 실패"));
  };
  const unpushAction = (ai) => {
    if (!selected || !selectedSession || !ai?.id) return;
    if (!confirm("달력 등록을 해제할까요? 달력 이벤트가 삭제됩니다.")) return;
    postJson(`${API}/action/unpush`, {
      meeting_id: selected.id, session_id: selectedSession.id, action_item_id: ai.id,
    }).then(() => reload()).catch(e => alert(e.message || "해제 실패"));
  };

  const recurrenceSummary = (r) => {
    if (!r || r.type === "none") return "반복 없음";
    if (r.type === "weekly") {
      const days = (r.weekday || []).map(d => WEEKDAY_LABEL[d]).join(",");
      const cnt = r.count_per_week ? `${r.count_per_week}회/주` : "";
      return `매주 ${cnt}${days ? ` (${days})` : ""}${r.note ? ` · ${r.note}` : ""}`;
    }
    return r.type;
  };

  return (
    <div style={{ position: "relative", display: "flex", height: "calc(100vh - 48px)", background: "var(--bg-primary)", color: "var(--text-primary)" }}>
      {/* Left list */}
      <div style={{ width: 340, minWidth: 300, borderRight: "1px solid var(--border)", background: "var(--bg-secondary)", display: "flex", flexDirection: "column" }}>
        <div style={{ padding: "12px 14px", borderBottom: "1px solid var(--border)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
            <span style={{ fontSize: 12, fontWeight: 700, color: "var(--text-secondary)" }}>회의 관리</span>
            <span style={{ flex: 1 }} />
            <button onClick={() => setCreating(true)} style={btnPrimary}>+ 새 회의</button>
          </div>
          {/* v8.7.7: 간트 뷰 제거 — 결정사항/액션아이템은 변경점 달력에 통합 표시됨. */}
          <input value={search} onChange={e => setSearch(e.target.value)} placeholder="제목/아젠다/결정 검색..." style={inp} />
          <div style={{ marginTop: 8, display: "flex", gap: 4, flexWrap: "wrap" }}>
            {/* v8.8.13: 보관(archived) 제거 — 전체/활성/취소 만. */}
            {["", "active", "cancelled"].map(s => (
              <span key={s || "all"} onClick={() => setFilterStatus(s)} style={{
                padding: "3px 10px", borderRadius: 999, fontSize: 10, cursor: "pointer", fontFamily: "monospace",
                background: filterStatus === s ? "var(--accent-glow)" : "var(--bg-card)",
                color: filterStatus === s ? "var(--accent)" : "var(--text-secondary)",
                border: "1px solid " + (filterStatus === s ? "var(--accent)" : "var(--border)"),
              }}>{s ? MEET_STATUS_LABEL[s] : "전체"}</span>
            ))}
          </div>
        </div>
        <div style={{ flex: 1, overflow: "auto", padding: "8px 6px" }}>
          {loading && <div style={{ padding: 20, textAlign: "center", color: "var(--text-secondary)", fontSize: 11 }}>로딩...</div>}
          {!loading && filtered.length === 0 && <div style={{ padding: 30, textAlign: "center", color: "var(--text-secondary)", fontSize: 11 }}>회의 없음</div>}
          {filtered.map(m => {
            const sel = m.id === selectedId;
            const sessions = m.sessions || [];
            const latest = sessions[sessions.length - 1];
            const latestStatus = latest?.status || "scheduled";
            // v8.8.28: 카테고리 제거 — 회의 생성 시 자동 배정된 고유 color(MEETING_PALETTE) 만 사용.
            //   legacy 데이터 호환: m.color 없으면 status 색, 그래도 없으면 회색.
            const color = m.color || SESS_STATUS_COLOR[latestStatus] || "#6b7280";
            return (
              <div key={m.id} onClick={() => { setSelectedId(m.id); setSelectedSid(latest?.id || null); setEditingMeta(false); setEditingMinutes(false); setEditingAgendaId(null); }} style={{
                margin: "4px 6px", padding: "10px 12px", borderRadius: 6, cursor: "pointer",
                background: sel ? "var(--accent-glow)" : "var(--bg-card)",
                border: "1px solid " + (sel ? "var(--accent)" : "var(--border)"),
                borderLeft: `4px solid ${color}`,
              }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
                  <span style={{ fontSize: 12, fontWeight: 600, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{m.title}</span>
                  <span style={{ fontSize: 9, padding: "1px 6px", borderRadius: 3, color: SESS_STATUS_COLOR[latestStatus], border: "1px solid " + SESS_STATUS_COLOR[latestStatus] }}>{SESS_STATUS_LABEL[latestStatus]}</span>
                </div>
                <div style={{ fontSize: 10, color: "var(--text-secondary)", fontFamily: "monospace", display: "flex", gap: 8, flexWrap: "wrap" }}>
                  <span>👤 {m.owner || "—"}</span>
                  <span>🔢 {sessions.length}차</span>
                  {latest?.scheduled_at && <span>🕒 {dtPretty(latest.scheduled_at)}</span>}
                </div>
                <div style={{ marginTop: 4, fontSize: 10, color: "var(--text-secondary)" }}>
                  {recurrenceSummary(m.recurrence)}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Right: detail */}
      <div style={{ flex: 1, overflow: "auto", display: "flex", flexDirection: "column" }}>
        {viewMode === "gantt" && (
          <ActionItemsGantt meetings={filtered} onPickMeeting={(id) => { setSelectedId(id); setViewMode("list"); }} />
        )}
        {viewMode === "list" && !selected && (
          <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-secondary)", fontSize: 12 }}>
            ← 좌측에서 회의를 선택하거나 "+ 새 회의" 버튼으로 생성하세요.
          </div>
        )}
        {viewMode === "list" && selected && (
          <div style={{ padding: 20, maxWidth: 980 }}>
            {/* Meta */}
            <div style={{ marginBottom: 14, padding: 16, borderRadius: 8, background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
                {/* v8.8.28: 카테고리 chip 제거 → 회의 고유 color dot 으로 대체. 변경점 달력에도 이 색상이 전파됨. */}
                {selected.color && (
                  <span title="이 회의의 고유 색상 (변경점 달력에도 동일 색으로 표시)"
                        style={{ width: 14, height: 14, borderRadius: "50%", background: selected.color, display: "inline-block", flexShrink: 0, border: "2px solid rgba(255,255,255,0.2)" }} />
                )}
                <span style={{ fontSize: 11, padding: "3px 10px", borderRadius: 999, color: SESS_STATUS_COLOR[(selectedSession?.status) || "scheduled"], border: "1px solid " + SESS_STATUS_COLOR[(selectedSession?.status) || "scheduled"] }}>
                  차수: {SESS_STATUS_LABEL[(selectedSession?.status) || "scheduled"]}
                </span>
                <span style={{ fontSize: 18, fontWeight: 700, flex: 1 }}>{selected.title}</span>
                {canEditMeta(selected) && !editingMeta && <button onClick={startEditMeta} style={btnGhost}>✎ 수정</button>}
                {canEditMeta(selected) && <button onClick={removeMeeting} style={btnDanger}>삭제</button>}
              </div>
              {!editingMeta && (
                <div style={{ display: "grid", gridTemplateColumns: "auto 1fr auto 1fr", gap: "6px 14px", fontSize: 12 }}>
                  <span style={lbl}>주관자</span><span style={val}>{selected.owner || "—"}</span>
                  <span style={lbl}>반복</span><span style={val}>{recurrenceSummary(selected.recurrence)}</span>
                  <span style={lbl}>카테고리</span><span style={val}>{selected.category || "—"}</span>
                  <span style={lbl}>상태</span><span style={val}>{MEET_STATUS_LABEL[selected.status || "active"]}</span>
                  <span style={lbl}>생성</span><span style={val}>{dtPretty(selected.created_at)} ({selected.created_by})</span>
                  <span style={lbl}>최근 수정</span><span style={val}>{dtPretty(selected.updated_at)}</span>
                </div>
              )}
              {editingMeta && metaDraft && (
                <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "8px 12px", alignItems: "center" }}>
                  <span style={lbl}>제목</span>
                  <input value={metaDraft.title} onChange={e => setMetaDraft({ ...metaDraft, title: e.target.value })} style={inp} />
                  <span style={lbl}>주관자</span>
                  <input value={metaDraft.owner} onChange={e => setMetaDraft({ ...metaDraft, owner: e.target.value })} style={inp} />
                  <span style={lbl}>상태</span>
                  <select value={metaDraft.status} onChange={e => setMetaDraft({ ...metaDraft, status: e.target.value })} style={inp}>
                    {Object.entries(MEET_STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </select>
                  <span style={lbl}>카테고리</span>
                  <select value={metaDraft.category} onChange={e => setMetaDraft({ ...metaDraft, category: e.target.value })} style={inp}>
                    <option value="">(없음)</option>
                    {categories.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
                  </select>
                  <span style={lbl}>반복 타입</span>
                  <select value={metaDraft.recurrence.type}
                          onChange={e => setMetaDraft({ ...metaDraft, recurrence: { ...metaDraft.recurrence, type: e.target.value } })}
                          style={inp}>
                    <option value="none">반복 없음</option>
                    <option value="weekly">매주</option>
                  </select>
                  {metaDraft.recurrence.type === "weekly" && (<>
                    <span style={lbl}>주당 횟수</span>
                    <input type="number" min={0} max={7} value={metaDraft.recurrence.count_per_week}
                           onChange={e => setMetaDraft({ ...metaDraft, recurrence: { ...metaDraft.recurrence, count_per_week: e.target.value } })}
                           style={inp} />
                    <span style={lbl}>요일</span>
                    <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                      {WEEKDAY_ORDER.map(d => {
                        const on = (metaDraft.recurrence.weekday || []).includes(d);
                        return (
                          <span key={d} onClick={() => setMetaDraft({ ...metaDraft, recurrence: { ...metaDraft.recurrence, weekday: toggleWeekday(metaDraft.recurrence.weekday || [], d) } })}
                                style={{ padding: "3px 10px", borderRadius: 999, fontSize: 11, cursor: "pointer", border: "1px solid var(--border)", background: on ? "var(--accent-glow)" : "transparent", color: on ? "var(--accent)" : "var(--text-secondary)" }}>
                            {WEEKDAY_LABEL[d]}
                          </span>
                        );
                      })}
                    </div>
                  </>)}
                  <span style={lbl}>메모</span>
                  <input value={metaDraft.recurrence.note}
                         onChange={e => setMetaDraft({ ...metaDraft, recurrence: { ...metaDraft.recurrence, note: e.target.value } })}
                         placeholder="추가 설명 (선택)" style={inp} />
                  {/* v8.8.3: 공개범위 group_ids FE picker */}
                  <span style={lbl}>공개 그룹</span>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                    {(allGroups || []).length === 0 && <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>(등록된 그룹 없음 — 모두에게 공개)</span>}
                    {(allGroups || []).map(g => {
                      const on = (metaDraft.group_ids || []).includes(g.id);
                      return (
                        <span key={g.id} onClick={() => {
                          const cur = metaDraft.group_ids || [];
                          const next = on ? cur.filter(x => x !== g.id) : [...cur, g.id];
                          setMetaDraft({ ...metaDraft, group_ids: next });
                        }} style={{ padding: "3px 10px", borderRadius: 999, fontSize: 11, cursor: "pointer", border: "1px solid var(--border)", background: on ? "var(--accent-glow)" : "transparent", color: on ? "var(--accent)" : "var(--text-secondary)" }}>
                          {on ? "✓ " : ""}{g.name}
                        </span>
                      );
                    })}
                    {(metaDraft.group_ids || []).length === 0 && (allGroups || []).length > 0 && (
                      <span style={{ fontSize: 10, color: "var(--text-secondary)", marginLeft: 4 }}>비워두면 모두에게 공개</span>
                    )}
                  </div>
                  <div />
                  <div style={{ display: "flex", gap: 6 }}>
                    <button onClick={submitEditMeta} style={btnPrimary}>저장</button>
                    <button onClick={() => { setEditingMeta(false); setMetaDraft(null); }} style={btnGhost}>취소</button>
                  </div>
                </div>
              )}
            </div>

            {/* Session tabs */}
            <div style={{ marginBottom: 14, padding: "10px 16px", borderRadius: 8, background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                <span style={{ fontSize: 11, color: "var(--text-secondary)", fontFamily: "monospace", marginRight: 6 }}>차수:</span>
                {(selected.sessions || []).map(s => {
                  const on = s.id === (selectedSession?.id);
                  return (
                    <span key={s.id} onClick={() => setSelectedSid(s.id)} style={{
                      padding: "4px 12px", borderRadius: 6, cursor: "pointer", fontSize: 11, fontFamily: "monospace",
                      border: "1px solid " + (on ? "var(--accent)" : "var(--border)"),
                      background: on ? "var(--accent-glow)" : "var(--bg-card)",
                      color: on ? "var(--accent)" : "var(--text-primary)",
                    }}>
                      {s.idx}차{s.scheduled_at ? ` (${dtPretty(s.scheduled_at).slice(0, 10)})` : ""}
                      <span style={{ marginLeft: 6, width: 6, height: 6, borderRadius: "50%", background: SESS_STATUS_COLOR[s.status || "scheduled"], display: "inline-block" }} />
                    </span>
                  );
                })}
                {canEditMeta(selected) && <button onClick={addSession} style={btnGhost}>+ 차수 추가</button>}
                {selectedSession && canEditMeta(selected) && <button onClick={removeSession} style={btnDanger}>차수 삭제</button>}
              </div>
              {selectedSession && (
                <div style={{ marginTop: 8, display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", fontSize: 11 }}>
                  <span style={lbl}>예정 일시</span>
                  {canEditMeta(selected) ? (
                    <input type="datetime-local"
                           value={dtForInput(selectedSession.scheduled_at || "")}
                           onChange={e => updateSessionMeta({ scheduled_at: e.target.value })}
                           style={{ ...inp, width: 200 }} />
                  ) : <span style={val}>{dtPretty(selectedSession.scheduled_at) || "—"}</span>}
                  <span style={lbl}>상태</span>
                  {canEditMeta(selected) ? (
                    <select value={selectedSession.status || "scheduled"}
                            onChange={e => updateSessionMeta({ status: e.target.value })}
                            style={{ ...inp, width: 130 }}>
                      {Object.entries(SESS_STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                    </select>
                  ) : <span style={val}>{SESS_STATUS_LABEL[selectedSession.status || "scheduled"]}</span>}
                </div>
              )}
            </div>

            {selectedSession && (<>
              {/* Agendas */}
              <div style={{ marginBottom: 14, padding: 16, borderRadius: 8, background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
                <div style={{ fontSize: 13, fontWeight: 700, color: "var(--accent)", marginBottom: 10, fontFamily: "monospace" }}>
                  📋 {selectedSession.idx}차 아젠다 ({(selectedSession.agendas || []).length})
                </div>
                {(selectedSession.agendas || []).length === 0 && (
                  <div style={{ padding: 14, textAlign: "center", color: "var(--text-secondary)", fontSize: 11, marginBottom: 10 }}>
                    이 차수에 아젠다가 아직 없습니다.
                  </div>
                )}
                {(selectedSession.agendas || []).map((a, i) => (
                  <div key={a.id} style={{ marginBottom: 8, padding: 10, borderRadius: 6, background: "var(--bg-card)", border: "1px solid var(--border)" }}>
                    {editingAgendaId === a.id ? (
                      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                        <input value={agendaEditDraft.title} onChange={e => setAgendaEditDraft({ ...agendaEditDraft, title: e.target.value })} placeholder="아젠다 제목" style={inp} />
                        <textarea value={agendaEditDraft.description} onChange={e => setAgendaEditDraft({ ...agendaEditDraft, description: e.target.value })} rows={2} placeholder="설명 (선택)" style={{ ...inp, resize: "vertical", fontFamily: "inherit" }} />
                        <input value={agendaEditDraft.link} onChange={e => setAgendaEditDraft({ ...agendaEditDraft, link: e.target.value })} placeholder="https://링크 (선택)" style={inp} />
                        <input value={agendaEditDraft.owner} onChange={e => setAgendaEditDraft({ ...agendaEditDraft, owner: e.target.value })} placeholder="담당자 (username)" style={inp} />
                        <div style={{ display: "flex", gap: 6 }}>
                          <button onClick={submitEditAgenda} style={btnPrimary}>저장</button>
                          <button onClick={() => { setEditingAgendaId(null); setAgendaEditDraft(null); }} style={btnGhost}>취소</button>
                        </div>
                      </div>
                    ) : (
                      <>
                        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                          <span style={{ fontSize: 10, color: "var(--text-secondary)", fontFamily: "monospace", minWidth: 26 }}>#{i + 1}</span>
                          <span style={{ fontSize: 13, fontWeight: 600, flex: 1 }}>{a.title}</span>
                          <span style={{ fontSize: 10, color: "var(--text-secondary)", fontFamily: "monospace" }}>👤 {a.owner}</span>
                          {canEditAgenda(selected, a) && <span onClick={() => startEditAgenda(a)} style={editLink}>수정</span>}
                          {canEditAgenda(selected, a) && <span onClick={() => removeAgenda(a)} style={delLink}>삭제</span>}
                        </div>
                        {/* v8.7.7: 아젠다 등록/수정 시각 */}
                        {(a.created_at || a.updated_at) && (
                          <div style={{ paddingLeft: 34, fontSize: 9, color: "var(--text-secondary)", fontFamily: "monospace", marginBottom: 4 }}>
                            {a.created_at && <>🕐 등록 {dtPretty(a.created_at)}</>}
                            {a.updated_at && a.updated_at !== a.created_at && <> · ✎ 수정 {dtPretty(a.updated_at)}</>}
                          </div>
                        )}
                        {a.description && <div style={{ fontSize: 12, color: "var(--text-primary)", marginBottom: 4, whiteSpace: "pre-wrap", paddingLeft: 34 }}>{a.description}</div>}
                        {a.issue_ref?.issue_id && (() => {
                          const issue = issueDetails[String(a.issue_ref.issue_id)] || a.issue_ref || null;
                          const issueLinks = Array.isArray(issue?.links) ? issue.links : [];
                          const issueLots = Array.isArray(issue?.lots) ? issue.lots : [];
                          const issueImages = trackerImages(issue);
                          const trackerHref = `/tracker?issue_id=${encodeURIComponent(a.issue_ref.issue_id)}`;
                          return (
                            <div style={{ marginTop: 6, marginLeft: 34, padding: 10, borderRadius: 6, border: "1px solid rgba(139,92,246,0.28)", background: "rgba(139,92,246,0.06)" }}>
                              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 6 }}>
                                <span style={{ fontSize: 11, fontWeight: 700, color: "#8b5cf6" }}>연결 이슈</span>
                                <span style={{ fontSize: 10, fontFamily: "monospace", color: "var(--text-secondary)" }}>#{a.issue_ref.issue_id}</span>
                                {a.issue_ref.category && <span style={{ fontSize: 9, padding: "1px 6px", borderRadius: 999, background: "var(--bg-card)", color: "var(--text-secondary)" }}>{a.issue_ref.category}</span>}
                                {a.issue_ref.status && <span style={{ fontSize: 9, padding: "1px 6px", borderRadius: 999, background: "rgba(245,158,11,0.16)", color: "#b45309" }}>{a.issue_ref.status}</span>}
                                <a href={trackerHref} style={{ fontSize: 10, color: "var(--accent)", textDecoration: "underline" }}>트래커에서 열기</a>
                              </div>
                              {issue?.title && <div style={{ fontSize: 12, fontWeight: 700, color: "var(--text-primary)", marginBottom: 4 }}>{issue.title}</div>}
                              {issue?.description_html ? (
                                <div style={{ fontSize: 11, color: "var(--text-primary)", background: "rgba(255,255,255,0.7)", borderRadius: 4, padding: 8, border: "1px solid var(--border)" }}
                                  dangerouslySetInnerHTML={{ __html: withTrackerImageAuth(issue.description_html) }} />
                              ) : issue?.description ? (
                                <div style={{ fontSize: 11, color: "var(--text-primary)", whiteSpace: "pre-wrap" }}>{issue.description}</div>
                              ) : (
                                <div style={{ fontSize: 10, color: "var(--text-secondary)" }}>이슈 상세 로딩 중…</div>
                              )}
                              {issueImages.length > 0 && (
                                <div style={{ marginTop: 8 }}>
                                  <div style={{ fontSize: 10, fontWeight: 700, color: "var(--text-secondary)", marginBottom: 5 }}>첨부 이미지 ({issueImages.length})</div>
                                  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(104px, 1fr))", gap: 6 }}>
                                    {issueImages.map((img, ii) => {
                                      const src = trackerImageSrc(img);
                                      if (!src) return null;
                                      return (
                                        <a key={`${img}-${ii}`} href={src} target="_blank" rel="noopener noreferrer"
                                          style={{ display: "block", aspectRatio: "4 / 3", borderRadius: 4, overflow: "hidden", border: "1px solid var(--border)", background: "rgba(255,255,255,0.78)" }}>
                                          <img src={src} alt={`이슈 첨부 이미지 ${ii + 1}`} loading="lazy"
                                            style={{ width: "100%", height: "100%", objectFit: "contain", display: "block" }} />
                                        </a>
                                      );
                                    })}
                                  </div>
                                </div>
                              )}
                              {issueLinks.length > 0 && (
                                <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 3 }}>
                                  {issueLinks.map((lnk, li) => isUrl(lnk) ? (
                                    <a key={li} href={lnk} target="_blank" rel="noopener noreferrer" style={{ fontSize: 10, color: "var(--accent)", textDecoration: "underline", wordBreak: "break-all" }}>{lnk}</a>
                                  ) : (
                                    <span key={li} style={{ fontSize: 10, color: "var(--text-secondary)", wordBreak: "break-all" }}>{lnk}</span>
                                  ))}
                                </div>
                              )}
                              {issueLots.length > 0 && (
                                <div style={{ marginTop: 8, borderTop: "1px dashed rgba(139,92,246,0.32)", paddingTop: 7 }}>
                                  <div style={{ fontSize: 10, fontWeight: 700, color: "var(--text-secondary)", marginBottom: 4 }}>관련 LOT_WF ({issueLots.length})</div>
                                  <div style={{ display: "grid", gap: 3, maxHeight: 96, overflow: "auto" }}>
                                    {issueLots.slice(0, 30).map((lot, li) => (
                                      <div key={li} title={lotWfText(lot)} style={{ fontSize: 10, color: "var(--text-primary)", fontFamily: "monospace", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                                        {lotWfText(lot)}
                                      </div>
                                    ))}
                                    {issueLots.length > 30 && <div style={{ fontSize: 10, color: "var(--text-secondary)" }}>+ {issueLots.length - 30} rows</div>}
                                  </div>
                                </div>
                              )}
                            </div>
                          );
                        })()}
                        {a.link && (
                          <div style={{ paddingLeft: 34 }}>
                            {isUrl(a.link) ? (
                              <a href={a.link} target="_blank" rel="noopener noreferrer" style={{ fontSize: 11, color: "var(--accent)", textDecoration: "underline", wordBreak: "break-all" }}>🔗 {a.link}</a>
                            ) : (
                              <span style={{ fontSize: 11, color: "var(--text-secondary)", wordBreak: "break-all" }}>🔗 {a.link}</span>
                            )}
                          </div>
                        )}
                      </>
                    )}
                  </div>
                ))}
                <div style={{ marginTop: 12, paddingTop: 12, borderTop: "1px dashed var(--border)" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                    <div style={{ fontSize: 11, color: "var(--text-secondary)", fontFamily: "monospace", flex: 1 }}>+ 새 아젠다 추가 (담당자: {(agendaDraft.owner || me)})</div>
                    {/* v8.8.13: 같은 그룹 이슈 불러와서 자동 채움 */}
                    <button onClick={openIssuePicker} title="같은 그룹의 이슈에서 가져오기 (제목·설명·담당자·링크·이미지 자동 채움)"
                      style={{ padding: "3px 10px", borderRadius: 4, border: "1px solid #8b5cf6", background: "transparent", color: "#8b5cf6", fontSize: 10, fontWeight: 700, cursor: "pointer" }}>
                      📎 이슈 가져오기
                    </button>
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
                    <input value={agendaDraft.title} onChange={e => setAgendaDraft({ ...agendaDraft, title: e.target.value })} placeholder="아젠다 제목 *" style={inp} />
                    <input value={agendaDraft.owner} onChange={e => setAgendaDraft({ ...agendaDraft, owner: e.target.value })} placeholder="담당자" style={inp} />
                  </div>
                  <textarea value={agendaDraft.description} onChange={e => setAgendaDraft({ ...agendaDraft, description: e.target.value })} rows={2} placeholder="설명 (선택)" style={{ ...inp, marginTop: 6, resize: "vertical", fontFamily: "inherit" }} />
                  {agendaDraft.issue_ref?.issue_id && (() => {
                    const issue = issueDetails[String(agendaDraft.issue_ref.issue_id)] || agendaDraft.issue_ref;
                    const issueImages = trackerImages(issue);
                    return (
                      <div style={{ marginTop: 6, padding: 10, borderRadius: 6, border: "1px solid rgba(139,92,246,0.28)", background: "rgba(139,92,246,0.06)" }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 6 }}>
                          <span style={{ fontSize: 11, fontWeight: 700, color: "#8b5cf6" }}>가져온 이슈</span>
                          <span style={{ fontSize: 10, fontFamily: "monospace", color: "var(--text-secondary)" }}>#{agendaDraft.issue_ref.issue_id}</span>
                          {issue?.category && <span style={{ fontSize: 9, padding: "1px 6px", borderRadius: 999, background: "var(--bg-card)", color: "var(--text-secondary)" }}>{issue.category}</span>}
                          {issueImages.length > 0 && <span style={{ fontSize: 9, color: "var(--text-secondary)" }}>이미지 {issueImages.length}개 함께 저장</span>}
                        </div>
                        {issue?.title && <div style={{ fontSize: 12, fontWeight: 700, color: "var(--text-primary)", marginBottom: 4 }}>{issue.title}</div>}
                        {issue?.description_html ? (
                          <div style={{ fontSize: 11, color: "var(--text-primary)", background: "rgba(255,255,255,0.7)", borderRadius: 4, padding: 8, border: "1px solid var(--border)" }}
                            dangerouslySetInnerHTML={{ __html: withTrackerImageAuth(issue.description_html) }} />
                        ) : issue?.description ? (
                          <div style={{ fontSize: 11, color: "var(--text-primary)", whiteSpace: "pre-wrap" }}>{issue.description}</div>
                        ) : null}
                        {issueImages.length > 0 && (
                          <div style={{ marginTop: 8, display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(104px, 1fr))", gap: 6 }}>
                            {issueImages.map((img, ii) => {
                              const src = trackerImageSrc(img);
                              if (!src) return null;
                              return (
                                <a key={`${img}-${ii}`} href={src} target="_blank" rel="noopener noreferrer"
                                  style={{ display: "block", aspectRatio: "4 / 3", borderRadius: 4, overflow: "hidden", border: "1px solid var(--border)", background: "rgba(255,255,255,0.78)" }}>
                                  <img src={src} alt={`가져온 이슈 이미지 ${ii + 1}`} loading="lazy"
                                    style={{ width: "100%", height: "100%", objectFit: "contain", display: "block" }} />
                                </a>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    );
                  })()}
                  <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
                    <input value={agendaDraft.link} onChange={e => setAgendaDraft({ ...agendaDraft, link: e.target.value })} placeholder="https://참고 링크 (선택, 새 창 열기)" style={{ ...inp, flex: 1 }} />
                    <button onClick={addAgenda} style={btnPrimary}>+ 추가</button>
                  </div>
                </div>
              </div>

              {/* v8.8.6/v8.8.15: 외부 저장 알림 배너 — 편집 중 다른 유저가 저장하면 표시. rev 표시 + "유지하며 rebase" 옵션. */}
              {externalUpdate && editingMinutes && (
                <div style={{ marginBottom: 10, padding: "8px 12px", borderRadius: 6, background: "rgba(245,158,11,0.12)", border: "1px solid #f59e0b", display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                  <span style={{ fontSize: 11, color: "#b45309", fontWeight: 700 }}>⚠ 동시편집 감지</span>
                  <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>
                    {externalUpdate.author} 님이 방금 저장함 ({(externalUpdate.at || "").slice(11, 16)})
                    {externalUpdate.rev !== undefined && <> · <span style={{ fontFamily: "monospace", color: "#b45309", fontWeight: 700 }}>rev {externalUpdate.rev}</span></>}
                    · 결정 {externalUpdate.decisions}개 · 액션 {externalUpdate.actions}개
                  </span>
                  <span style={{ flex: 1 }} />
                  <button onClick={() => {
                    // v8.8.15: 내 편집 유지한 채 base_rev 만 최신으로 rebase — 저장 시 정상 통과.
                    setMinutesDraft(d => d ? { ...d, base_rev: Number(externalUpdate.rev || 0) } : d);
                    setExternalUpdate(null);
                  }} title="내 편집 유지 + 서버 rev 에만 맞춰 재동기화 (저장 시 상대 변경 덮어씀)"
                    style={{ padding: "3px 10px", borderRadius: 4, border: "1px solid var(--accent)", background: "transparent", color: "var(--accent)", fontSize: 10, fontWeight: 700, cursor: "pointer" }}>내 편집 유지 · rebase</button>
                  <button onClick={() => { setExternalUpdate(null); reload(); setEditingMinutes(false); setMinutesDraft(null); }}
                    style={{ padding: "3px 10px", borderRadius: 4, border: "1px solid #f59e0b", background: "#f59e0b", color: "#fff", fontSize: 10, fontWeight: 700, cursor: "pointer" }}>외부 내용 불러오기</button>
                  <button onClick={() => setExternalUpdate(null)}
                    style={{ padding: "3px 10px", borderRadius: 4, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", fontSize: 10, cursor: "pointer" }}>무시</button>
                </div>
              )}

              {/* Minutes */}
              <div style={{ marginBottom: 14, padding: 16, borderRadius: 8, background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
                <div style={{ display: "flex", alignItems: "center", marginBottom: 10 }}>
                  <span style={{ fontSize: 13, fontWeight: 700, color: "var(--accent)", fontFamily: "monospace", flex: 1 }}>📝 {selectedSession.idx}차 회의록</span>
                  {canEditMinutes(selected) && !editingMinutes && (
                    <button onClick={startEditMinutes} style={btnGhost}>{selectedSession.minutes ? "✎ 수정" : "+ 작성"}</button>
                  )}
                  {/* v8.7.7: 저장된 차수는 메일만 재발송 가능 */}
                  {canEditMinutes(selected) && !editingMinutes && selectedSession.minutes && (
                    <button onClick={() => setSendDialog({
                      mail_group_ids: [], mail_to_users: [], mail_to: "", mail_subject: "",
                    })} style={{ ...btnGhost, marginLeft: 6 }}>📧 메일 발송</button>
                  )}
                </div>
                {!editingMinutes && !selectedSession.minutes && (
                  <div style={{ padding: 20, textAlign: "center", color: "var(--text-secondary)", fontSize: 11 }}>
                    회의록 미작성. 주관자({selected.owner || "—"})가 작성합니다. <b>그룹 멤버는 아래 공동 작성란으로 추가 내용을 남길 수 있습니다.</b>
                  </div>
                )}
                {/* v8.8.13: 공동 작성 — minutes 가 없어도 append 섹션 노출. */}
                {!editingMinutes && (
                  <MinutesAppendix session={selectedSession} meeting={selected} user={user}
                    appendText={appendText} setAppendText={setAppendText}
                    appendBusy={appendBusy} onSubmit={submitAppend} onDelete={deleteAppend}
                    lbl={lbl} inp={inp} btnPrimary={btnPrimary} isAdmin={isAdmin} />
                )}
                {!editingMinutes && selectedSession.minutes && (
                  <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                    {selectedSession.minutes.body && (
                      <div>
                        <div style={lbl}>본문</div>
                        <div style={{ marginTop: 4, padding: 10, borderRadius: 5, background: "var(--bg-card)", border: "1px solid var(--border)", fontSize: 12, lineHeight: 1.6, whiteSpace: "pre-wrap" }}>
                          {selectedSession.minutes.body}
                        </div>
                      </div>
                    )}
                    {(selectedSession.minutes.decisions || []).length > 0 && (
                      <div>
                        <div style={lbl}>⚡ 결정사항 ({selectedSession.minutes.decisions.length})</div>
                        <table style={{ width: "100%", marginTop: 4, fontSize: 11, borderCollapse: "collapse" }}>
                          <thead>
                            <tr style={{ background: "var(--bg-card)" }}>
                              <th style={th}>내용</th>
                              <th style={{ ...th, width: 180 }}>📅 달력 (회의 일자로 등록)</th>
                            </tr>
                          </thead>
                          <tbody>
                            {(selectedSession.minutes.decisions || []).map((d, i) => {
                              const obj = typeof d === "string" ? { id: "", text: d } : d;
                              return (
                                <tr key={obj.id || i}>
                                  <td style={td}>{obj.text}</td>
                                  <td style={td}>
                                    {obj.calendar_pushed ? (
                                      <div style={{ fontSize: 10, lineHeight: 1.4 }}>
                                        <span style={{ color: "#22c55e", fontWeight: 600 }}>✓ 등록됨</span>
                                        <div style={{ color: "var(--text-secondary)", fontFamily: "monospace" }}>{obj.calendar_pushed_by} · {dtPretty(obj.calendar_pushed_at)}</div>
                                        <span onClick={() => unpushDecision(obj)} style={delLink}>해제</span>
                                      </div>
                                    ) : (
                                      <button onClick={() => pushDecision(obj)} style={btnTiny} disabled={!obj.id}>📅 달력 등록</button>
                                    )}
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    )}
                    {(selectedSession.minutes.action_items || []).length > 0 && (
                      <div>
                        <div style={lbl}>✅ 액션 아이템 ({selectedSession.minutes.action_items.length})</div>
                        <table style={{ width: "100%", marginTop: 4, fontSize: 11, borderCollapse: "collapse" }}>
                          <thead>
                            <tr style={{ background: "var(--bg-card)" }}>
                              <th style={th}>내용</th>
                              <th style={{ ...th, width: 100 }}>담당</th>
                              <th style={{ ...th, width: 100 }}>마감</th>
                              <th style={{ ...th, width: 160 }}>📅 달력</th>
                            </tr>
                          </thead>
                          <tbody>
                            {(selectedSession.minutes.action_items || []).map((a, i) => (
                              <tr key={a.id || i}>
                                <td style={td}>{a.text}</td>
                                <td style={td}>{a.owner || "—"}</td>
                                <td style={td}>{a.due || "—"}</td>
                                <td style={td}>
                                  {a.calendar_pushed ? (
                                    <div style={{ fontSize: 10, lineHeight: 1.4 }}>
                                      <span style={{ color: "#22c55e", fontWeight: 600 }}>✓ 등록됨</span>
                                      <div style={{ color: "var(--text-secondary)", fontFamily: "monospace" }}>
                                        {a.calendar_pushed_by} · {dtPretty(a.calendar_pushed_at)}
                                      </div>
                                      <span onClick={() => unpushAction(a)} style={delLink}>해제</span>
                                    </div>
                                  ) : (
                                    <button onClick={() => pushAction(a)} style={btnTiny}>📅 달력 등록</button>
                                  )}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                    <div style={{ fontSize: 10, color: "var(--text-secondary)", fontFamily: "monospace" }}>
                      작성: {selectedSession.minutes.author} · {dtPretty(selectedSession.minutes.updated_at)}
                    </div>
                  </div>
                )}
                {editingMinutes && minutesDraft && (
                  <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                    <div>
                      <div style={lbl}>본문</div>
                      <textarea value={minutesDraft.body} onChange={e => setMinutesDraft({ ...minutesDraft, body: e.target.value })} rows={6} placeholder="회의 진행 요약, 논의 내용..." style={{ ...inp, marginTop: 4, resize: "vertical", fontFamily: "inherit" }} />
                    </div>
                    <div>
                      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                        <span style={lbl}>⚡ 결정사항</span>
                        <button onClick={addDecision} style={btnTiny}>+ 추가</button>
                      </div>
                      {minutesDraft.decisions.map((d, i) => {
                        const obj = typeof d === "string" ? { text: d, due: "" } : d;
                        return (
                          <div key={i} style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 6, marginTop: 4 }}>
                            <input value={obj.text} onChange={e => updDecision(i, "text", e.target.value)} placeholder={`결정사항 #${i + 1}`} style={inp} />
                            <button onClick={() => delDecision(i)} style={btnTinyDanger}>×</button>
                          </div>
                        );
                      })}
                      <div style={{ fontSize: 10, color: "var(--text-secondary)", marginTop: 4 }}>
                        * 결정사항에는 별도 마감일이 없으며 달력 등록 시 회의 세션 날짜로 자동 기록됩니다.
                      </div>
                    </div>
                    <div>
                      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                        <span style={lbl}>✅ 액션 아이템 (회의록 저장 시 달력에 자동 반영 — 회의일~마감 구간)</span>
                        <button onClick={addAction} style={btnTiny}>+ 추가</button>
                      </div>
                      {minutesDraft.action_items.map((a, i) => (
                        <div key={i} style={{ marginTop: 6, padding: 8, border: "1px dashed var(--border)", borderRadius: 5 }}>
                          <div style={{ display: "grid", gridTemplateColumns: "1fr 130px 130px auto", gap: 6 }}>
                            <input value={a.text} onChange={e => updAction(i, "text", e.target.value)} placeholder="할 일" style={inp} />
                            <input value={a.owner} onChange={e => updAction(i, "owner", e.target.value)} placeholder="담당자 (username)" style={inp} />
                            <input type="date" value={(a.due || "").slice(0, 10)} onChange={e => updAction(i, "due", e.target.value)} placeholder="마감" style={inp} />
                            <button onClick={() => delAction(i)} style={btnTinyDanger}>×</button>
                          </div>
                          <div style={{ marginTop: 6, display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                            <span style={{ ...lbl, minWidth: 68 }}>그룹 담당</span>
                            {allGroups.length === 0 && <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>(그룹이 없습니다 — Admin → 그룹 에서 생성)</span>}
                            {allGroups.map(g => {
                              const on = (a.group_ids || []).includes(g.id);
                              return (
                                <span key={g.id} onClick={() => {
                                  const cur = a.group_ids || [];
                                  const next = on ? cur.filter(x => x !== g.id) : [...cur, g.id];
                                  updAction(i, "group_ids", next);
                                }} style={{
                                  padding: "2px 8px", borderRadius: 999, fontSize: 10, cursor: "pointer",
                                  border: "1px solid " + (on ? "var(--accent)" : "var(--border)"),
                                  background: on ? "var(--accent-glow)" : "transparent",
                                  color: on ? "var(--accent)" : "var(--text-secondary)",
                                }}>{g.name}</span>
                              );
                            })}
                          </div>
                        </div>
                      ))}
                    </div>
                    {/* v8.7.6: 메일 발송 옵션 */}
                    <div style={{ marginTop: 6, padding: 10, border: "1px solid var(--accent)", borderRadius: 6, background: "var(--bg-card)" }}>
                      <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 600 }}>
                        <input type="checkbox" checked={!!minutesDraft.send_mail} onChange={e => setMinutesDraft({ ...minutesDraft, send_mail: e.target.checked })} />
                        📧 저장과 동시에 아젠다+회의록+액션아이템을 메일로 발송
                      </label>
                      {minutesDraft.send_mail && (
                        <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 6 }}>
                          <input value={minutesDraft.mail_subject} onChange={e => setMinutesDraft({ ...minutesDraft, mail_subject: e.target.value })} placeholder={`메일 제목 (기본: [flow 회의록] ${selected.title} · ${selectedSession.idx}차)`} style={inp} />
                          <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                            <span style={{ ...lbl, minWidth: 68 }}>수신 유저</span>
                            {mailRecipients.length === 0 && <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>(승인된 유저 없음)</span>}
                            {/* v8.8.27: username = 사내 email id (자동 도메인 합성). "(no email)" disable 로직 제거. 이름 있으면 `홍길동 (id)` 로 표시. */}
                            {mailRecipients.map(u => {
                              const on = (minutesDraft.mail_to_users || []).includes(u.username);
                              return (
                                <span key={u.username} onClick={() => {
                                  const cur = minutesDraft.mail_to_users || [];
                                  const next = on ? cur.filter(x => x !== u.username) : [...cur, u.username];
                                  setMinutesDraft({ ...minutesDraft, mail_to_users: next });
                                }} title={u.username}
                                  style={{
                                    padding: "2px 8px", borderRadius: 999, fontSize: 10, cursor: "pointer",
                                    border: "1px solid " + (on ? "var(--accent)" : "var(--border)"),
                                    background: on ? "var(--accent-glow)" : "transparent",
                                    color: on ? "var(--accent)" : "var(--text-secondary)",
                                  }}>{userLabel(u)}</span>
                              );
                            })}
                          </div>
                          <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                            <span style={{ ...lbl, minWidth: 68 }}>메일 그룹</span>
                            {mailGroups.length === 0 && <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>(그룹 없음 — 아래 "관리" 버튼)</span>}
                            {mailGroups.map(g => {
                              const on = (minutesDraft.mail_group_ids || []).includes(g.id);
                              return (
                                <span key={g.id} onClick={() => {
                                  const cur = minutesDraft.mail_group_ids || [];
                                  const next = on ? cur.filter(x => x !== g.id) : [...cur, g.id];
                                  setMinutesDraft({ ...minutesDraft, mail_group_ids: next });
                                }} title={`${(g.members || []).length}명 + ${(g.extra_emails || []).length}외부`}
                                  style={{
                                    padding: "2px 8px", borderRadius: 999, fontSize: 10, cursor: "pointer",
                                    border: "1px solid " + (on ? "var(--accent)" : "var(--border)"),
                                    background: on ? "var(--accent-glow)" : "transparent",
                                    color: on ? "var(--accent)" : "var(--text-secondary)",
                                  }}>📮 {g.name}</span>
                              );
                            })}
                            <button onClick={() => setMgEditor(true)} style={btnTiny} type="button">관리</button>
                          </div>
                          <input value={minutesDraft.mail_to} onChange={e => setMinutesDraft({ ...minutesDraft, mail_to: e.target.value })} placeholder="추가 이메일 (쉼표/공백 구분, 선택)" style={inp} />
                          <div style={{ fontSize: 10, color: "var(--text-secondary)" }}>
                            * 액션아이템에 지정된 그룹 멤버의 이메일도 자동 포함됩니다.
                          </div>
                          {/* v8.8.16: 메일 전용 본문 — 공동 작성된 minutes.body 와 분리. 빈 채 발송하면 메일에 본문 섹션 없음. */}
                          <div style={{ marginTop: 6, padding: "6px 8px", borderRadius: 5, border: "1px dashed var(--border)", background: "var(--bg-primary)" }}>
                            <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4, color: "var(--text-secondary)" }}>📝 메일 본문 (선택)</div>
                            <div style={{ fontSize: 10, color: "var(--text-secondary)", marginBottom: 4 }}>
                              공동 작성된 회의록 본문은 메일에 자동 첨부되지 않습니다. 아래는 참고용이고, 메일에 넣을 내용은 아래 텍스트 영역에 직접 작성하세요.
                            </div>
                            {minutesDraft.body && (
                              <details style={{ marginBottom: 6 }}>
                                <summary style={{ cursor: "pointer", fontSize: 10, color: "var(--accent)", fontWeight: 600 }}>참고: 공동 작성된 회의록 본문 (클릭하여 펼치기/접기)</summary>
                                <pre style={{ fontSize: 10, margin: "4px 0 0", padding: "6px 8px", background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: 4, whiteSpace: "pre-wrap", maxHeight: 160, overflow: "auto" }}>{minutesDraft.body}</pre>
                                <div style={{ textAlign: "right", marginTop: 4 }}>
                                  <button type="button" onClick={() => setMinutesDraft({ ...minutesDraft, mail_body: (minutesDraft.mail_body ? minutesDraft.mail_body + "\n\n" : "") + minutesDraft.body })}
                                    style={{ padding: "2px 8px", borderRadius: 3, border: "1px solid var(--accent)", background: "transparent", color: "var(--accent)", fontSize: 10, cursor: "pointer", fontWeight: 600 }}>
                                    ↓ 메일 본문에 복사
                                  </button>
                                </div>
                              </details>
                            )}
                            <textarea value={minutesDraft.mail_body || ""}
                              onChange={e => setMinutesDraft({ ...minutesDraft, mail_body: e.target.value })}
                              rows={4}
                              placeholder="메일에 넣을 본문 내용 (비워두면 아젠다/결정사항/액션아이템만 발송)"
                              style={{ ...inp, width: "100%", resize: "vertical", fontFamily: "inherit", boxSizing: "border-box" }} />
                          </div>
                        </div>
                      )}
                    </div>
                    <div style={{ display: "flex", gap: 6 }}>
                      <button onClick={submitMinutes} style={btnPrimary}>저장 (status → 완료){minutesDraft.send_mail ? " + 메일" : ""}</button>
                      <button onClick={() => { setEditingMinutes(false); setMinutesDraft(null); }} style={btnGhost}>취소</button>
                    </div>
                  </div>
                )}
              </div>
            </>)}
          </div>
        )}
      </div>

      {/* v8.8.13: 이슈 가져오기 picker modal — 같은 그룹 이슈 → agendaDraft 자동 채움. */}
      {issuePickerOpen && (
        <div style={modalBack} onClick={() => setIssuePickerOpen(false)}>
          <div onClick={e => e.stopPropagation()} style={{ ...modalCard, width: 640, maxWidth: "94vw" }}>
            <div style={{ display: "flex", alignItems: "center", marginBottom: 10 }}>
              <div style={{ fontSize: 14, fontWeight: 700, color: "#8b5cf6", fontFamily: "monospace", flex: 1 }}>📎 이슈에서 가져오기</div>
              <span onClick={() => setIssuePickerOpen(false)} style={{ cursor: "pointer", fontSize: 18, color: "var(--text-secondary)" }}>✕</span>
            </div>
            <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 8 }}>
              선택하면 제목·설명·담당자·첫 번째 링크가 채워지고, 이슈 본문·이미지는 연결 이슈로 함께 보관됩니다.
              {!isAdmin && (selected?.group_ids || []).length > 0 && <> (회의 그룹과 겹치는 이슈만)</>}
            </div>
            <input value={issuePickerSearch} onChange={e => setIssuePickerSearch(e.target.value)}
              placeholder="🔎 제목/카테고리/작성자 검색"
              style={{ ...inp, marginBottom: 8 }} />
            <div style={{ maxHeight: 360, overflow: "auto", border: "1px solid var(--border)", borderRadius: 6 }}>
              {issuePickerBusy && <div style={{ padding: 24, textAlign: "center", fontSize: 11, color: "var(--text-secondary)" }}>로딩…</div>}
              {!issuePickerBusy && (() => {
                const q = issuePickerSearch.trim().toLowerCase();
                // v8.8.28: 검색 범위에 summary 포함. BE 가 이미 updated_at desc 정렬해서 내려줌.
                const filtered = q
                  ? issuePickerList.filter(x =>
                      (x.title || "").toLowerCase().includes(q) ||
                      (x.summary || "").toLowerCase().includes(q) ||
                      (x.category || "").toLowerCase().includes(q) ||
                      (x.username || "").toLowerCase().includes(q))
                  : issuePickerList;
                if (filtered.length === 0) return <div style={{ padding: 24, textAlign: "center", fontSize: 11, color: "var(--text-secondary)" }}>이슈 없음 {q ? "(검색 조건 일치 없음)" : "(같은 그룹 이슈 없음)"}.</div>;
                // v8.8.28: 고유번호(iss.id) 대신 최신 수정 날짜를 왼쪽에 노출.
                //   포맷: "MM/DD HH:mm" (ISO YYYY-MM-DDTHH:MM:SS → 월/일 시:분).
                const fmtUpdated = (s) => {
                  if (!s) return "—";
                  const m = String(s).match(/^(\d{4})-(\d{2})-(\d{2})T?(\d{2})?:?(\d{2})?/);
                  if (!m) return String(s).slice(0, 16);
                  return `${m[2]}/${m[3]} ${m[4] || "00"}:${m[5] || "00"}`;
                };
                return filtered.map(iss => (
                  <div key={iss.id} onClick={() => attachIssueToAgenda(iss.id)}
                    title={`#${iss.id} · ${iss.summary || ""}`}
                    style={{ display: "flex", gap: 8, alignItems: "center", padding: "6px 10px", borderBottom: "1px solid var(--border)", cursor: "pointer", fontSize: 11 }}
                    onMouseEnter={e => e.currentTarget.style.background = "rgba(139,92,246,0.08)"}
                    onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
                    {/* v8.8.28: 왼쪽은 고유번호 대신 최신 수정 시각. */}
                    <span style={{ fontFamily: "monospace", fontSize: 10, color: "var(--text-secondary)", width: 88, flexShrink: 0, whiteSpace: "nowrap" }}>🕘 {fmtUpdated(iss.updated_at)}</span>
                    {iss.category && <span style={{ fontSize: 9, padding: "1px 6px", borderRadius: 10, background: "var(--bg-card)", color: "var(--text-secondary)", flexShrink: 0 }}>{iss.category}</span>}
                    {iss.status && <span style={{ fontSize: 9, padding: "1px 6px", borderRadius: 10, background: iss.status === "closed" ? "#22c55e22" : "#f59e0b22", color: iss.status === "closed" ? "#16a34a" : "#c2410c", flexShrink: 0 }}>{iss.status}</span>}
                    {/* v8.8.28: 제목 + 한 줄 요약 (회색, nowrap ellipsis). title/summary 합쳐서 flex:1. */}
                    <span style={{ flex: 1, minWidth: 0, display: "flex", gap: 8, alignItems: "baseline", overflow: "hidden" }}>
                      <span style={{ color: "var(--text-primary)", fontWeight: 600, flexShrink: 0, maxWidth: "45%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{iss.title || "(제목 없음)"}</span>
                      {iss.summary && <span style={{ fontSize: 10, color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1, minWidth: 0 }}>— {iss.summary}</span>}
                    </span>
                    <span style={{ fontSize: 9, color: "var(--text-secondary)", fontFamily: "monospace", flexShrink: 0 }}>{iss.username}</span>
                  </div>
                ));
              })()}
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 10 }}>
              <button onClick={() => setIssuePickerOpen(false)} style={btnGhost}>닫기</button>
            </div>
          </div>
        </div>
      )}

      {/* Create modal */}
      {creating && (
        <div style={modalBack} onClick={() => setCreating(false)}>
          <div onClick={e => e.stopPropagation()} style={modalCard}>
            <div style={{ fontSize: 14, fontWeight: 700, color: "var(--accent)", fontFamily: "monospace", marginBottom: 12 }}>+ 새 회의 생성</div>
            <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "8px 10px", alignItems: "center" }}>
              <span style={lbl}>제목 *</span>
              <input value={draft.title} onChange={e => setDraft({ ...draft, title: e.target.value })} placeholder="회의 제목" style={inp} autoFocus />
              <span style={lbl}>주관자</span>
              {/* v8.8.12: 주관자 기본값을 로그인 유저로 즉시 채움 (placeholder 대신). */}
              <input value={draft.owner || me} onChange={e => setDraft({ ...draft, owner: e.target.value })} placeholder={`기본: ${me}`} style={inp} />
              <span style={lbl}>1차 일시</span>
              <input type="datetime-local" value={draft.first_scheduled_at} onChange={e => setDraft({ ...draft, first_scheduled_at: e.target.value })} style={inp} />
              <span style={lbl}>카테고리</span>
              <select value={draft.category} onChange={e => setDraft({ ...draft, category: e.target.value })} style={inp}>
                <option value="">(없음)</option>
                {categories.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
              </select>
              <span style={lbl}>반복</span>
              <select value={draft.recurrence.type}
                      onChange={e => setDraft({ ...draft, recurrence: { ...draft.recurrence, type: e.target.value } })}
                      style={inp}>
                <option value="none">반복 없음</option>
                <option value="weekly">매주</option>
              </select>
              {draft.recurrence.type === "weekly" && (<>
                <span style={lbl}>주당 횟수</span>
                <input type="number" min={0} max={7} value={draft.recurrence.count_per_week}
                       onChange={e => setDraft({ ...draft, recurrence: { ...draft.recurrence, count_per_week: e.target.value } })} style={inp} />
                <span style={lbl}>요일</span>
                <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                  {WEEKDAY_ORDER.map(d => {
                    const on = draft.recurrence.weekday.includes(d);
                    return (
                      <span key={d} onClick={() => setDraft({ ...draft, recurrence: { ...draft.recurrence, weekday: toggleWeekday(draft.recurrence.weekday, d) } })}
                            style={{ padding: "3px 10px", borderRadius: 999, fontSize: 11, cursor: "pointer", border: "1px solid var(--border)", background: on ? "var(--accent-glow)" : "transparent", color: on ? "var(--accent)" : "var(--text-secondary)" }}>
                        {WEEKDAY_LABEL[d]}
                      </span>
                    );
                  })}
                </div>
              </>)}
              {/* v8.8.3: 공개범위 group_ids FE picker (create) */}
              <span style={lbl}>공개 그룹</span>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                {(allGroups || []).length === 0 && <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>(등록된 그룹 없음 — 모두에게 공개)</span>}
                {(allGroups || []).map(g => {
                  const on = (draft.group_ids || []).includes(g.id);
                  return (
                    <span key={g.id} onClick={() => {
                      const cur = draft.group_ids || [];
                      const next = on ? cur.filter(x => x !== g.id) : [...cur, g.id];
                      setDraft({ ...draft, group_ids: next });
                    }} style={{ padding: "3px 10px", borderRadius: 999, fontSize: 11, cursor: "pointer", border: "1px solid var(--border)", background: on ? "var(--accent-glow)" : "transparent", color: on ? "var(--accent)" : "var(--text-secondary)" }}>
                      {on ? "✓ " : ""}{g.name}
                    </span>
                  );
                })}
                {(draft.group_ids || []).length === 0 && (allGroups || []).length > 0 && (
                  <span style={{ fontSize: 10, color: "var(--text-secondary)", marginLeft: 4 }}>비워두면 모두에게 공개</span>
                )}
              </div>
            </div>
            <div style={{ display: "flex", gap: 6, marginTop: 14, justifyContent: "flex-end" }}>
              <button onClick={() => setCreating(false)} style={btnGhost}>취소</button>
              <button onClick={submitCreate} style={btnPrimary}>생성</button>
            </div>
          </div>
        </div>
      )}

      {/* v8.7.7: 메일 그룹 관리 모달 */}
      {mgEditor && (
        <MailGroupsEditor
          groups={mailGroups}
          mailRecipients={mailRecipients}
          me={me}
          onClose={() => { setMgEditor(false); reloadMailGroups(); }}
          onReload={reloadMailGroups}
        />
      )}

      {/* v8.7.7: 저장된 차수 메일 재발송 다이얼로그 */}
      {sendDialog && selected && selectedSession && (
        <SendMailDialog
          meeting={selected}
          session={selectedSession}
          mailGroups={mailGroups}
          mailRecipients={mailRecipients}
          draft={sendDialog}
          onChange={(patch) => setSendDialog(d => ({ ...d, ...patch }))}
          onOpenManager={() => setMgEditor(true)}
          onClose={() => setSendDialog(null)}
          onSent={(r) => {
            setSendDialog(null);
            if (r?.mail?.ok) alert(`메일 발송 완료${r.mail.dry_run ? " (dry-run)" : ""} · ${(r.mail.to || []).length}명`);
            else alert(`메일 발송 실패: ${r?.mail?.error || "unknown"}`);
          }}
        />
      )}

      {/* PageGear — 좌하단 고정 (전 탭 통일) */}
      <PageGear title="회의관리 설정" canEdit={isAdmin} position="bottom-left">
        <MeetingCategoryEditor categories={categories} setCategories={setCategories} isAdmin={isAdmin} />
      </PageGear>
    </div>
  );
}

/* v8.8.13: 회의록 공동 작성 appendix — 그룹 멤버도 '추가' 가능. 삭제는 작성자 본인 또는 주관자/admin. */
function MinutesAppendix({ session, meeting, user, appendText, setAppendText, appendBusy, onSubmit, onDelete, lbl, inp, btnPrimary, isAdmin }) {
  const list = ((session && session.minutes && session.minutes.body_appendix) || []);
  const me = user?.username || "";
  const isOwner = !!meeting && meeting.owner === me;
  return (
    <div style={{ marginTop: list.length ? 8 : 0, padding: 10, borderRadius: 5, background: "var(--bg-card)", border: "1px dashed var(--border)" }}>
      <div style={{ ...lbl, marginBottom: 6, display: "flex", alignItems: "center", gap: 6 }}>
        <span>✍ 공동 작성</span>
        <span style={{ fontSize: 9, color: "var(--text-secondary)", fontWeight: 400 }}>(그룹 멤버는 추가만 가능 · 본인 글만 삭제 · 주관자는 전체 정리)</span>
      </div>
      {list.length === 0 && <div style={{ fontSize: 10, color: "var(--text-secondary)", padding: "4px 2px" }}>추가된 내용 없음</div>}
      {list.map(e => {
        const mine = e.author === me;
        const canDel = mine || isOwner || isAdmin;
        return (
          <div key={e.id} style={{ display: "flex", gap: 8, padding: "6px 4px", borderBottom: "1px dashed var(--border)" }}>
            <div style={{ minWidth: 130, fontSize: 10, color: "var(--text-secondary)", fontFamily: "monospace", flexShrink: 0, lineHeight: 1.4 }}>
              <div style={{ fontWeight: 700, color: mine ? "var(--accent)" : "var(--text-primary)" }}>{e.author}</div>
              <div>{(e.at || "").replace("T", " ").slice(5, 16)}</div>
            </div>
            <div style={{ flex: 1, fontSize: 12, whiteSpace: "pre-wrap", lineHeight: 1.55 }}>{e.text}</div>
            {canDel && (
              <span onClick={() => onDelete(e.id)} title="삭제"
                style={{ cursor: "pointer", color: "#ef4444", fontSize: 11, padding: "0 4px", flexShrink: 0 }}>×</span>
            )}
          </div>
        );
      })}
      <div style={{ marginTop: 8, display: "flex", gap: 6 }}>
        <textarea value={appendText} onChange={e => setAppendText(e.target.value)} rows={2}
          placeholder="추가 내용 (로그인 유저: 그룹 멤버만 허용. 본인 글만 지울 수 있음)"
          style={{ ...inp, flex: 1, resize: "vertical", fontFamily: "inherit" }} />
        <button onClick={onSubmit} disabled={appendBusy || !(appendText || "").trim()}
          style={{ ...btnPrimary, opacity: appendBusy || !(appendText || "").trim() ? 0.5 : 1, cursor: appendBusy ? "not-allowed" : "pointer", alignSelf: "flex-start" }}>
          {appendBusy ? "추가 중…" : "+ 추가"}
        </button>
      </div>
    </div>
  );
}

/* v8.7.8: 회의 카테고리 관리 (PageGear 내부). 달력 카테고리 endpoint 재사용. */
function MeetingCategoryEditor({ categories, setCategories, isAdmin }) {
  const [draft, setDraft] = useState(null);
  const start = () => setDraft((categories || []).map(c => ({ ...c })));
  const save = () => {
    fetch("/api/calendar/categories/save", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Session-Token": localStorage.getItem("flow_session_token") || "" },
      body: JSON.stringify({ categories: draft }),
    }).then(r => r.json()).then(d => {
      if (d.ok) { setCategories(d.categories || draft); setDraft(null); }
      else alert(d.detail || "저장 실패");
    }).catch(e => alert("저장 실패: " + e.message));
  };
  const move = (i, delta) => {
    const j = i + delta; if (j < 0 || j >= draft.length) return;
    const n = draft.slice(); [n[i], n[j]] = [n[j], n[i]]; setDraft(n);
  };
  if (!draft) {
    return (
      <div style={{ fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.6 }}>
        회의 카테고리는 달력 카테고리 팔레트와 공유됩니다.<br />
        <button onClick={start} disabled={!isAdmin}
          style={{ marginTop: 8, padding: "6px 12px", borderRadius: 5, border: "1px solid var(--accent)", background: "transparent", color: "var(--accent)", fontSize: 11, cursor: isAdmin ? "pointer" : "not-allowed", fontWeight: 600, opacity: isAdmin ? 1 : 0.5 }}>
          🎨 카테고리 편집 ({(categories || []).length})
        </button>
      </div>
    );
  }
  return (
    <div>
      <div style={{ fontSize: 10, color: "var(--text-secondary)", marginBottom: 6 }}>이름/색 변경 + 순서 조정 + 추가/삭제</div>
      <div style={{ maxHeight: 300, overflowY: "auto", border: "1px solid var(--border)", borderRadius: 4 }}>
        {draft.map((c, i) => (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 4, padding: "4px 6px", borderBottom: "1px solid var(--border)" }}>
            <span style={{ width: 18, fontSize: 10, color: "var(--text-secondary)" }}>{i + 1}</span>
            <input value={c.name} onChange={e => { const n = draft.slice(); n[i] = { ...n[i], name: e.target.value }; setDraft(n); }}
              style={{ flex: 1, padding: "3px 6px", fontSize: 11, border: "1px solid var(--border)", borderRadius: 3, background: "var(--bg-primary)", color: "var(--text-primary)" }} />
            <input type="color" value={c.color || "#6b7280"} onChange={e => { const n = draft.slice(); n[i] = { ...n[i], color: e.target.value }; setDraft(n); }}
              style={{ width: 32, height: 24, border: "1px solid var(--border)", borderRadius: 3, background: "transparent" }} />
            <button onClick={() => move(i, -1)} style={{ padding: "1px 5px", fontSize: 10, border: "1px solid var(--border)", background: "transparent", borderRadius: 3, cursor: "pointer" }}>↑</button>
            <button onClick={() => move(i, 1)} style={{ padding: "1px 5px", fontSize: 10, border: "1px solid var(--border)", background: "transparent", borderRadius: 3, cursor: "pointer" }}>↓</button>
            <button onClick={() => setDraft(draft.filter((_, j) => j !== i))} style={{ padding: "1px 5px", fontSize: 10, border: "1px solid #ef4444", background: "transparent", color: "#ef4444", borderRadius: 3, cursor: "pointer" }}>×</button>
          </div>
        ))}
      </div>
      <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
        <button onClick={() => setDraft([...draft, { name: "신규", color: "#6b7280" }])} style={{ padding: "4px 10px", fontSize: 11, border: "1px solid var(--border)", background: "transparent", color: "var(--text-primary)", borderRadius: 4, cursor: "pointer" }}>+ 추가</button>
        <div style={{ flex: 1 }} />
        <button onClick={save} style={{ padding: "4px 12px", fontSize: 11, border: "none", background: "var(--accent)", color: "#fff", borderRadius: 4, cursor: "pointer", fontWeight: 600 }}>저장</button>
        <button onClick={() => setDraft(null)} style={{ padding: "4px 12px", fontSize: 11, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", borderRadius: 4, cursor: "pointer" }}>취소</button>
      </div>
    </div>
  );
}

const inp = { width: "100%", padding: "6px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12, outline: "none", boxSizing: "border-box" };
const lbl = { fontSize: 10, color: "var(--text-secondary)", fontFamily: "monospace" };
const val = { fontSize: 12, color: "var(--text-primary)" };
const btnPrimary = { padding: "6px 14px", borderRadius: 5, border: "none", background: "var(--accent)", color: "#fff", fontSize: 11, fontWeight: 600, cursor: "pointer" };
const btnGhost = { padding: "5px 12px", borderRadius: 5, border: "1px solid var(--border)", background: "transparent", color: "var(--text-primary)", fontSize: 11, cursor: "pointer" };
const btnDanger = { padding: "5px 10px", borderRadius: 5, border: "1px solid #ef4444", background: "transparent", color: "#ef4444", fontSize: 11, cursor: "pointer" };
const btnTiny = { padding: "2px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", fontSize: 10, cursor: "pointer" };
const btnTinyDanger = { padding: "2px 10px", borderRadius: 4, border: "1px solid #ef4444", background: "transparent", color: "#ef4444", fontSize: 11, cursor: "pointer" };
const editLink = { fontSize: 10, color: "var(--accent)", cursor: "pointer", textDecoration: "underline" };
const delLink = { fontSize: 10, color: "#ef4444", cursor: "pointer", textDecoration: "underline" };
const th = { padding: "6px 8px", textAlign: "left", fontSize: 10, color: "var(--text-secondary)", borderBottom: "1px solid var(--border)", fontWeight: 600 };
const td = { padding: "6px 8px", borderBottom: "1px solid var(--border)", verticalAlign: "top" };
const modalBack = { position: "fixed", inset: 0, background: "rgba(0,0,0,0.55)", zIndex: 9999, display: "flex", alignItems: "center", justifyContent: "center" };
const modalCard = { width: 520, maxWidth: "92%", padding: 18, borderRadius: 10, background: "var(--bg-secondary)", border: "1px solid var(--border)", boxShadow: "0 8px 24px rgba(0,0,0,0.4)" };

// v8.7.6: 액션아이템 간트 차트 (모든 회의·차수 취합). SVG 기반.
function ActionItemsGantt({ meetings, onPickMeeting }) {
  const rows = [];
  for (const m of (meetings || [])) {
    for (const s of (m.sessions || [])) {
      const ais = (s.minutes?.action_items) || [];
      for (const a of ais) {
        if (!a.due) continue;
        // v8.7.6: 간트 시작일 = 회의(세션) 날짜, 끝 = 액션아이템 데드라인.
        const sessionDate = (s.scheduled_at || s.created_at || "").slice(0, 10)
                          || (m.created_at || "").slice(0, 10);
        rows.push({
          meeting_id: m.id,
          meeting_title: m.title,
          session_idx: s.idx,
          start: sessionDate,
          end: a.due.slice(0, 10),
          text: a.text,
          owner: a.owner || "",
          status: a.status || "pending",
          group_count: (a.group_ids || []).length,
        });
      }
    }
  }
  if (rows.length === 0) {
    return (
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-secondary)", fontSize: 12, flexDirection: "column", gap: 8 }}>
        <div>간트 차트에 표시할 액션아이템이 없습니다.</div>
        <div style={{ fontSize: 10 }}>액션아이템에 마감일(due) 을 설정하면 여기에 나타납니다.</div>
      </div>
    );
  }
  const toDate = s => s ? new Date(s).getTime() : Date.now();
  const minT = Math.min(...rows.map(r => toDate(r.start || r.end)));
  const maxT = Math.max(...rows.map(r => toDate(r.end || r.start))) + 86400000;
  const span = Math.max(maxT - minT, 86400000 * 7);
  const W = 1100, headerH = 30, rowH = 26, padL = 220, padR = 20;
  const H = headerH + rows.length * rowH + 20;
  const xOf = t => padL + ((t - minT) / span) * (W - padL - padR);
  const today = Date.now();
  // month ticks
  const ticks = [];
  const start = new Date(minT); start.setDate(1); start.setHours(0, 0, 0, 0);
  for (let d = start.getTime(); d < maxT; ) {
    ticks.push(d);
    const dt = new Date(d); dt.setMonth(dt.getMonth() + 1);
    d = dt.getTime();
  }
  const statusColor = (s) => s === "done" ? "#22c55e" : s === "in_progress" ? "#f59e0b" : "#3b82f6";
  return (
    <div style={{ padding: 16, overflow: "auto" }}>
      <div style={{ marginBottom: 8, fontSize: 12, color: "var(--text-secondary)" }}>
        📊 액션아이템 간트 차트 · {rows.length} items · {new Date(minT).toISOString().slice(0, 10)} ~ {new Date(maxT).toISOString().slice(0, 10)}
      </div>
      <svg width={W} height={H} style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", borderRadius: 6 }}>
        {/* month grid */}
        {ticks.map(t => (
          <g key={t}>
            <line x1={xOf(t)} y1={headerH} x2={xOf(t)} y2={H} stroke="var(--border)" strokeDasharray="2,3" />
            <text x={xOf(t) + 4} y={18} fontSize="10" fill="var(--text-secondary)" fontFamily="monospace">
              {new Date(t).toISOString().slice(0, 7)}
            </text>
          </g>
        ))}
        {/* today line */}
        {today >= minT && today <= maxT && (
          <>
            <line x1={xOf(today)} y1={headerH - 6} x2={xOf(today)} y2={H - 4} stroke="var(--accent)" strokeWidth="2" />
            <text x={xOf(today) + 4} y={headerH - 10} fontSize="9" fill="var(--accent)" fontFamily="monospace">TODAY</text>
          </>
        )}
        {/* rows */}
        {rows.map((r, i) => {
          const y = headerH + i * rowH + 6;
          const x1 = xOf(toDate(r.start));
          const x2 = Math.max(xOf(toDate(r.end)), x1 + 4);
          const overdue = toDate(r.end) < today && r.status !== "done";
          const fill = overdue ? "#ef4444" : statusColor(r.status);
          return (
            <g key={i}>
              <text x={6} y={y + 13} fontSize="10" fill="var(--text-primary)" fontFamily="monospace" style={{ cursor: "pointer" }}
                    onClick={() => onPickMeeting && onPickMeeting(r.meeting_id)}>
                {(r.meeting_title || "").slice(0, 22)} · {r.session_idx}차
              </text>
              <rect x={x1} y={y} width={x2 - x1} height={14} rx={3} fill={fill} opacity={0.85} />
              <text x={x1 + 4} y={y + 11} fontSize="10" fill="#fff" fontFamily="inherit">
                {r.text.slice(0, 38)}{r.owner ? ` · ${r.owner}` : ""}{r.group_count ? ` · +${r.group_count}` : ""}
              </text>
            </g>
          );
        })}
      </svg>
      <div style={{ marginTop: 8, fontSize: 10, color: "var(--text-secondary)", display: "flex", gap: 14 }}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}><span style={{ width: 10, height: 10, background: "#3b82f6", borderRadius: 2, display: "inline-block" }} />pending</span>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}><span style={{ width: 10, height: 10, background: "#f59e0b", borderRadius: 2, display: "inline-block" }} />in_progress</span>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}><span style={{ width: 10, height: 10, background: "#22c55e", borderRadius: 2, display: "inline-block" }} />done</span>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}><span style={{ width: 10, height: 10, background: "#ef4444", borderRadius: 2, display: "inline-block" }} />overdue</span>
        <span style={{ color: "var(--accent)" }}>세로선 = 오늘</span>
      </div>
    </div>
  );
}

// v8.8.3: 공용 메일 그룹 관리 모달.
// groups 배열은 병합된 목록 (mg:* 편집 가능 / grp:* 읽기 전용 — Groups 탭에서 관리).
function MailGroupsEditor({ groups, mailRecipients, me, onClose, onReload }) {
  const [editId, setEditId] = useState(null);
  const [draft, setDraft] = useState({ name: "", members: [], extra_emails: "", note: "" });
  const [msg, setMsg] = useState("");
  const startCreate = () => { setEditId("__new__"); setDraft({ name: "", members: [me], extra_emails: "", note: "" }); setMsg(""); };
  // editId 는 실제 API id (prefix 제거된 값). g.id 는 "mg:<rawId>" 형태.
  const startEdit = (g) => {
    // grp: prefix 그룹은 이 모달에서 편집 불가 — Groups 탭에서 관리.
    if ((g.id || "").startsWith("grp:")) { setMsg("일반 그룹은 그룹관리 탭에서 편집하세요."); return; }
    const rawId = g.id.replace(/^mg:/, "");
    setEditId(rawId);
    setDraft({ name: g.name || "", members: g.members || [], extra_emails: (g.extra_emails || []).join(", "), note: g.note || "" });
    setMsg("");
  };
  const toggleMember = (un) => setDraft(d => ({ ...d, members: d.members.includes(un) ? d.members.filter(x => x !== un) : [...d.members, un] }));
  const submit = () => {
    const name = (draft.name || "").trim();
    if (!name) { setMsg("그룹 이름을 입력하세요"); return; }
    const extras = (draft.extra_emails || "").split(/[,;\s]+/).map(s => s.trim()).filter(Boolean);
    const payload = { name, members: draft.members || [], extra_emails: extras, note: draft.note || "" };
    const isNew = editId === "__new__";
    const url = isNew ? "/api/mail-groups/create" : `/api/mail-groups/update?id=${encodeURIComponent(editId)}`;
    postJson(url, payload).then(() => {
      setEditId(null); setMsg(isNew ? "생성 완료" : "저장 완료"); onReload();
    }).catch(e => setMsg(e.message || "저장 실패"));
  };
  const remove = (g) => {
    if ((g.id || "").startsWith("grp:")) { setMsg("일반 그룹은 그룹관리 탭에서 삭제하세요."); return; }
    const rawId = g.id.replace(/^mg:/, "");
    if (!confirm(`메일 그룹 "${g.name}" 을(를) 삭제할까요?`)) return;
    sf(`/api/mail-groups/delete?id=${encodeURIComponent(rawId)}`, { method: "POST" })
      .then(() => { setEditId(null); onReload(); }).catch(e => setMsg(e.message));
  };
  const inp2 = { width: "100%", padding: "6px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12, outline: "none", boxSizing: "border-box" };
  return (
    // v8.8.3: z-index 10001 — SendMailDialog(9999) 위에 확실히 올라오도록.
    <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.55)", zIndex: 10001, display: "flex", alignItems: "center", justifyContent: "center" }} onClick={onClose}>
      <div onClick={e => e.stopPropagation()} style={{ width: 720, maxWidth: "94%", maxHeight: "86vh", overflow: "auto", padding: 18, borderRadius: 10, background: "var(--bg-secondary)", border: "1px solid var(--border)", boxShadow: "0 8px 24px rgba(0,0,0,0.4)" }}>
        <div style={{ display: "flex", alignItems: "center", marginBottom: 12 }}>
          <span style={{ fontSize: 14, fontWeight: 700, color: "var(--accent)", fontFamily: "monospace", flex: 1 }}>공용 메일 그룹 관리</span>
          <button onClick={startCreate} style={{ padding: "4px 12px", borderRadius: 5, border: "none", background: "var(--accent)", color: "#fff", fontSize: 11, cursor: "pointer" }}>+ 새 그룹</button>
          <button onClick={onClose} style={{ marginLeft: 6, padding: "4px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "transparent", color: "var(--text-primary)", fontSize: 11, cursor: "pointer" }}>닫기</button>
        </div>
        <div style={{ fontSize: 10, color: "var(--text-secondary)", marginBottom: 10 }}>
          * 공용 메일그룹(직접 생성) + 일반 그룹(그룹관리 탭)이 함께 표시됩니다. 일반 그룹은 여기서 편집 불가.
        </div>
        {editId && (
          <div style={{ padding: 12, borderRadius: 6, background: "var(--bg-card)", border: "1px solid var(--accent)", marginBottom: 12 }}>
            <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 6 }}>{editId === "__new__" ? "새 그룹 생성" : "그룹 편집"}</div>
            <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "6px 10px", alignItems: "center", marginBottom: 6 }}>
              <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>이름</span>
              <input value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} placeholder="예: GATE 담당 팀" style={inp2} />
              <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>메모</span>
              <input value={draft.note} onChange={e => setDraft({ ...draft, note: e.target.value })} placeholder="(선택)" style={inp2} />
            </div>
            <div style={{ fontSize: 10, color: "var(--text-secondary)", marginTop: 6, marginBottom: 4 }}>멤버 (클릭 토글)</div>
            <div style={{ display: "flex", gap: 4, flexWrap: "wrap", maxHeight: 120, overflow: "auto", padding: 4, border: "1px solid var(--border)", borderRadius: 4 }}>
              {/* v8.8.27: username = 이메일 → disable 로직 제거. 이름(있으면) + id 로 표시. */}
              {(mailRecipients || []).map(u => {
                const on = (draft.members || []).includes(u.username);
                return (
                  <span key={u.username} onClick={() => toggleMember(u.username)}
                    title={u.username}
                    style={{ padding: "2px 8px", borderRadius: 999, fontSize: 10, cursor: "pointer", border: "1px solid " + (on ? "var(--accent)" : "var(--border)"), background: on ? "var(--accent-glow)" : "transparent", color: on ? "var(--accent)" : "var(--text-secondary)" }}>{userLabel(u)}</span>
                );
              })}
            </div>
            <div style={{ fontSize: 10, color: "var(--text-secondary)", marginTop: 8, marginBottom: 4 }}>추가 외부 이메일 (콤마/공백/줄바꿈 구분)</div>
            <textarea value={draft.extra_emails} onChange={e => setDraft({ ...draft, extra_emails: e.target.value })} rows={2} placeholder="vendor@partner.com, external@x.com" style={{ ...inp2, resize: "vertical", fontFamily: "monospace", fontSize: 11 }} />
            <div style={{ display: "flex", gap: 6, marginTop: 10, alignItems: "center" }}>
              <button onClick={submit} style={{ padding: "6px 14px", borderRadius: 5, border: "none", background: "var(--accent)", color: "#fff", fontSize: 11, fontWeight: 600, cursor: "pointer" }}>저장</button>
              <button onClick={() => setEditId(null)} style={{ padding: "6px 12px", borderRadius: 5, border: "1px solid var(--border)", background: "transparent", color: "var(--text-primary)", fontSize: 11, cursor: "pointer" }}>취소</button>
              {msg && <span style={{ fontSize: 11, color: "var(--accent)" }}>{msg}</span>}
            </div>
          </div>
        )}
        {msg && !editId && <div style={{ marginBottom: 8, fontSize: 11, color: "var(--accent)" }}>{msg}</div>}
        <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: 6 }}>
          {(groups || []).length === 0 && <div style={{ padding: 20, textAlign: "center", color: "var(--text-secondary)", fontSize: 12 }}>아직 그룹이 없습니다. "+ 새 그룹" 으로 생성하세요.</div>}
          {(groups || []).map(g => {
            const isGrp = (g.id || "").startsWith("grp:");
            return (
              <div key={g.id} style={{ padding: "8px 12px", border: "1px solid var(--border)", borderRadius: 6, background: "var(--bg-card)", opacity: isGrp ? 0.85 : 1 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span style={{ fontSize: 12, fontWeight: 600, flex: 1 }}>
                    {isGrp ? "[그룹]" : "[메일그룹]"} {g.name}
                  </span>
                  <span style={{ fontSize: 10, color: "var(--text-secondary)", fontFamily: "monospace" }}>
                    {(g.members || []).length}명{!isGrp && ` + ${(g.extra_emails || []).length}외부`}
                    {!isGrp && g.created_by ? ` · by ${g.created_by}` : ""}
                  </span>
                  {isGrp
                    ? <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>그룹관리 탭</span>
                    : <>
                        <span onClick={() => startEdit(g)} style={{ fontSize: 11, color: "var(--accent)", cursor: "pointer", textDecoration: "underline" }}>편집</span>
                        <span onClick={() => remove(g)} style={{ fontSize: 11, color: "#ef4444", cursor: "pointer", textDecoration: "underline" }}>삭제</span>
                      </>
                  }
                </div>
                {g.note && <div style={{ fontSize: 10, color: "var(--text-secondary)", marginTop: 2 }}>{g.note}</div>}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// v8.7.7: 저장된 차수 메일 재발송 (minutes 수정 없이 send-mail 만 호출).
function SendMailDialog({ meeting, session, mailGroups, mailRecipients, draft, onChange, onOpenManager, onClose, onSent }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const submit = () => {
    const mailTo = (draft.mail_to || "").split(/[,;\s]+/).map(s => s.trim()).filter(s => s && s.includes("@"));
    setBusy(true); setErr("");
    postJson("/api/meetings/session/send-mail", {
      meeting_id: meeting.id, session_id: session.id,
      mail_group_ids: draft.mail_group_ids || [],
      mail_to_users: draft.mail_to_users || [],
      mail_to: mailTo,
      mail_subject: draft.mail_subject || "",
    }).then(onSent).catch(e => { setErr(e.message || "발송 실패"); setBusy(false); });
  };
  const inp3 = { width: "100%", padding: "6px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12, outline: "none", boxSizing: "border-box" };
  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.55)", zIndex: 9999, display: "flex", alignItems: "center", justifyContent: "center" }} onClick={onClose}>
      <div onClick={e => e.stopPropagation()} style={{ width: 620, maxWidth: "94%", padding: 18, borderRadius: 10, background: "var(--bg-secondary)", border: "1px solid var(--border)", boxShadow: "0 8px 24px rgba(0,0,0,0.4)" }}>
        <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 8 }}>📧 {session.idx}차 회의록 메일 발송</div>
        <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 10 }}>
          "{meeting.title}" · {session.idx}차 의 아젠다 + 회의록 + 액션아이템을 HTML 메일로 전송합니다.
        </div>
        <input value={draft.mail_subject} onChange={e => onChange({ mail_subject: e.target.value })} placeholder={`메일 제목 (기본: [flow 회의록] ${meeting.title} · ${session.idx}차)`} style={inp3} />
        <div style={{ fontSize: 10, color: "var(--text-secondary)", marginTop: 10, marginBottom: 4 }}>📮 메일 그룹</div>
        <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
          {(mailGroups || []).length === 0 && <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>(없음)</span>}
          {(mailGroups || []).map(g => {
            const on = (draft.mail_group_ids || []).includes(g.id);
            return (
              <span key={g.id} onClick={() => onChange({ mail_group_ids: on ? draft.mail_group_ids.filter(x => x !== g.id) : [...(draft.mail_group_ids || []), g.id] })}
                style={{ padding: "2px 8px", borderRadius: 999, fontSize: 10, cursor: "pointer", border: "1px solid " + (on ? "var(--accent)" : "var(--border)"), background: on ? "var(--accent-glow)" : "transparent", color: on ? "var(--accent)" : "var(--text-secondary)" }}>{g.name}</span>
            );
          })}
          <button onClick={onOpenManager} type="button" style={{ padding: "2px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", fontSize: 10, cursor: "pointer" }}>관리</button>
        </div>
        <div style={{ fontSize: 10, color: "var(--text-secondary)", marginTop: 10, marginBottom: 4 }}>개별 수신자 (선택)</div>
        <div style={{ display: "flex", gap: 4, flexWrap: "wrap", maxHeight: 90, overflow: "auto" }}>
          {/* v8.8.27: username=email 전제. disable 제거. userLabel(name+id) 적용. */}
          {(mailRecipients || []).map(u => {
            const on = (draft.mail_to_users || []).includes(u.username);
            return (
              <span key={u.username} onClick={() => onChange({ mail_to_users: on ? draft.mail_to_users.filter(x => x !== u.username) : [...(draft.mail_to_users || []), u.username] })}
                title={u.username}
                style={{ padding: "2px 8px", borderRadius: 999, fontSize: 10, cursor: "pointer", border: "1px solid " + (on ? "var(--accent)" : "var(--border)"), background: on ? "var(--accent-glow)" : "transparent", color: on ? "var(--accent)" : "var(--text-secondary)" }}>{userLabel(u)}</span>
            );
          })}
        </div>
        <div style={{ marginTop: 8 }}>
          <input value={draft.mail_to} onChange={e => onChange({ mail_to: e.target.value })} placeholder="추가 이메일 (콤마/공백 구분)" style={inp3} />
        </div>
        {err && <div style={{ marginTop: 8, fontSize: 11, color: "#ef4444" }}>{err}</div>}
        <div style={{ marginTop: 14, display: "flex", gap: 6, justifyContent: "flex-end" }}>
          <button onClick={onClose} style={{ padding: "6px 14px", borderRadius: 5, border: "1px solid var(--border)", background: "transparent", color: "var(--text-primary)", fontSize: 12, cursor: "pointer" }} disabled={busy}>취소</button>
          <button onClick={submit} style={{ padding: "6px 14px", borderRadius: 5, border: "none", background: "var(--accent)", color: "#fff", fontSize: 12, fontWeight: 600, cursor: busy ? "wait" : "pointer" }} disabled={busy}>{busy ? "발송 중…" : "📧 발송"}</button>
        </div>
      </div>
    </div>
  );
}
