/* My_Meeting.jsx v8.7.4 — 회의관리 (반복 + 차수 + 아젠다 + 달력 selective push).
   - 좌측: 회의 목록 (status 필터 + 검색).
   - 우측 상단: 회의 메타 (제목/주관자/반복/카테고리/상태).
   - 우측 가운데: 차수(세션) 탭. "+ 차수 추가" 가능.
   - 우측 하단: 선택된 차수의 아젠다 + 회의록 + 액션아이템.
   - 아젠다 link 클릭: 새 창(target=_blank).
   - 액션아이템 옆 📅 버튼: 달력 selective push/unpush. 등록됨 표시 + 등록 유저/시간.
*/
import { useEffect, useMemo, useState } from "react";
import PageGear from "../components/PageGear";
import { sf, postJson } from "../lib/api";

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

export default function My_Meeting({ user }) {
  const [meetings, setMeetings] = useState([]);
  const [categories, setCategories] = useState([]);
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
  });
  const [editingMeta, setEditingMeta] = useState(false);
  const [metaDraft, setMetaDraft] = useState(null);
  const [agendaDraft, setAgendaDraft] = useState({ title: "", description: "", link: "", owner: "" });
  const [editingAgendaId, setEditingAgendaId] = useState(null);
  const [agendaEditDraft, setAgendaEditDraft] = useState(null);
  const [minutesDraft, setMinutesDraft] = useState(null);
  const [editingMinutes, setEditingMinutes] = useState(false);

  const isAdmin = user?.role === "admin";
  const me = user?.username || "";

  const reload = () => {
    setLoading(true);
    sf(`${API}/list${filterStatus ? `?status=${encodeURIComponent(filterStatus)}` : ""}`)
      .then(d => setMeetings(d.meetings || []))
      .catch(() => setMeetings([]))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    sf("/api/calendar/categories").then(d => setCategories(d.categories || [])).catch(() => {});
  }, []);
  useEffect(() => { reload(); /* eslint-disable-next-line */ }, [filterStatus]);

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
    }).then(d => {
      setCreating(false);
      setDraft({
        title: "", owner: "", first_scheduled_at: "",
        recurrence: { type: "none", count_per_week: 1, weekday: [], note: "" },
        category: "",
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
      owner: (agendaDraft.owner || me).trim(),
    }).then(() => {
      setAgendaDraft({ title: "", description: "", link: "", owner: "" });
      reload();
    }).catch(e => alert(e.message || "추가 실패"));
  };
  const startEditAgenda = (a) => {
    setEditingAgendaId(a.id);
    setAgendaEditDraft({ title: a.title || "", description: a.description || "", link: a.link || "", owner: a.owner || "" });
  };
  const submitEditAgenda = () => {
    if (!selected || !selectedSession || !editingAgendaId || !agendaEditDraft) return;
    postJson(`${API}/agenda/update`, {
      meeting_id: selected.id, session_id: selectedSession.id, agenda_id: editingAgendaId,
      title: agendaEditDraft.title,
      description: agendaEditDraft.description,
      link: agendaEditDraft.link,
      owner: agendaEditDraft.owner,
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
      // v8.7.5: decisions — 기존 문자열도 포용하도록 객체로 정규화
      decisions: (m.decisions || []).map(d => typeof d === "string" ? { text: d, due: "" } : { ...d }),
      action_items: (m.action_items || []).map(a => ({ ...a })),
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
  const submitMinutes = () => {
    if (!selected || !selectedSession || !minutesDraft) return;
    postJson(`${API}/minutes/save`, {
      meeting_id: selected.id, session_id: selectedSession.id,
      body: minutesDraft.body,
      decisions: minutesDraft.decisions,
      action_items: minutesDraft.action_items,
    }).then(() => { setEditingMinutes(false); setMinutesDraft(null); reload(); })
      .catch(e => alert(e.message || "저장 실패"));
  };

  // v8.7.5: 결정사항 단위 달력 push/unpush
  const pushDecision = (d) => {
    if (!selected || !selectedSession || !d?.id) {
      alert("결정사항을 먼저 저장해야 달력에 등록할 수 있습니다.");
      return;
    }
    const due = d.due || (selectedSession.scheduled_at ? selectedSession.scheduled_at.slice(0, 10) : "");
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
            <span style={{ fontSize: 14, fontWeight: 700, fontFamily: "monospace", color: "var(--accent)" }}>🗓 회의관리</span>
            <span style={{ flex: 1 }} />
            <button onClick={() => setCreating(true)} style={btnPrimary}>+ 새 회의</button>
          </div>
          <input value={search} onChange={e => setSearch(e.target.value)} placeholder="제목/아젠다/결정 검색..." style={inp} />
          <div style={{ marginTop: 8, display: "flex", gap: 4, flexWrap: "wrap" }}>
            {["", "active", "archived", "cancelled"].map(s => (
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
            const color = m.category ? categoryColor(m.category) : SESS_STATUS_COLOR[latestStatus];
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
        {!selected && (
          <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-secondary)", fontSize: 12 }}>
            ← 좌측에서 회의를 선택하거나 "+ 새 회의" 버튼으로 생성하세요.
          </div>
        )}
        {selected && (
          <div style={{ padding: 20, maxWidth: 980 }}>
            {/* Meta */}
            <div style={{ marginBottom: 14, padding: 16, borderRadius: 8, background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
                {selected.category && (
                  <span style={{ fontSize: 11, padding: "3px 10px", borderRadius: 999, color: "#fff", background: categoryColor(selected.category) }}>
                    ● {selected.category}
                  </span>
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
                        {a.description && <div style={{ fontSize: 12, color: "var(--text-primary)", marginBottom: 4, whiteSpace: "pre-wrap", paddingLeft: 34 }}>{a.description}</div>}
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
                  <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 6, fontFamily: "monospace" }}>+ 새 아젠다 추가 (담당자: {(agendaDraft.owner || me)})</div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
                    <input value={agendaDraft.title} onChange={e => setAgendaDraft({ ...agendaDraft, title: e.target.value })} placeholder="아젠다 제목 *" style={inp} />
                    <input value={agendaDraft.owner} onChange={e => setAgendaDraft({ ...agendaDraft, owner: e.target.value })} placeholder={`담당자 (기본: ${me})`} style={inp} />
                  </div>
                  <textarea value={agendaDraft.description} onChange={e => setAgendaDraft({ ...agendaDraft, description: e.target.value })} rows={2} placeholder="설명 (선택)" style={{ ...inp, marginTop: 6, resize: "vertical", fontFamily: "inherit" }} />
                  <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
                    <input value={agendaDraft.link} onChange={e => setAgendaDraft({ ...agendaDraft, link: e.target.value })} placeholder="https://참고 링크 (선택, 새 창 열기)" style={{ ...inp, flex: 1 }} />
                    <button onClick={addAgenda} style={btnPrimary}>+ 추가</button>
                  </div>
                </div>
              </div>

              {/* Minutes */}
              <div style={{ marginBottom: 14, padding: 16, borderRadius: 8, background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
                <div style={{ display: "flex", alignItems: "center", marginBottom: 10 }}>
                  <span style={{ fontSize: 13, fontWeight: 700, color: "var(--accent)", fontFamily: "monospace", flex: 1 }}>📝 {selectedSession.idx}차 회의록</span>
                  {canEditMinutes(selected) && !editingMinutes && (
                    <button onClick={startEditMinutes} style={btnGhost}>{selectedSession.minutes ? "✎ 수정" : "+ 작성"}</button>
                  )}
                </div>
                {!editingMinutes && !selectedSession.minutes && (
                  <div style={{ padding: 20, textAlign: "center", color: "var(--text-secondary)", fontSize: 11 }}>
                    회의록 미작성. 주관자({selected.owner || "—"})가 작성합니다.
                  </div>
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
                              <th style={{ ...th, width: 100 }}>마감</th>
                              <th style={{ ...th, width: 180 }}>📅 달력</th>
                            </tr>
                          </thead>
                          <tbody>
                            {(selectedSession.minutes.decisions || []).map((d, i) => {
                              const obj = typeof d === "string" ? { id: "", text: d } : d;
                              return (
                                <tr key={obj.id || i}>
                                  <td style={td}>{obj.text}</td>
                                  <td style={td}>{obj.due || "—"}</td>
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
                          <div key={i} style={{ display: "grid", gridTemplateColumns: "1fr 130px auto", gap: 6, marginTop: 4 }}>
                            <input value={obj.text} onChange={e => updDecision(i, "text", e.target.value)} placeholder={`결정사항 #${i + 1}`} style={inp} />
                            <input value={obj.due || ""} onChange={e => updDecision(i, "due", e.target.value)} placeholder="마감 (YYYY-MM-DD · 선택)" style={inp} />
                            <button onClick={() => delDecision(i)} style={btnTinyDanger}>×</button>
                          </div>
                        );
                      })}
                    </div>
                    <div>
                      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                        <span style={lbl}>✅ 액션 아이템 (저장 후 📅 달력 등록 가능)</span>
                        <button onClick={addAction} style={btnTiny}>+ 추가</button>
                      </div>
                      {minutesDraft.action_items.map((a, i) => (
                        <div key={i} style={{ display: "grid", gridTemplateColumns: "1fr 130px 130px auto", gap: 6, marginTop: 4 }}>
                          <input value={a.text} onChange={e => updAction(i, "text", e.target.value)} placeholder="할 일" style={inp} />
                          <input value={a.owner} onChange={e => updAction(i, "owner", e.target.value)} placeholder="담당자" style={inp} />
                          <input value={a.due} onChange={e => updAction(i, "due", e.target.value)} placeholder="마감 (YYYY-MM-DD)" style={inp} />
                          <button onClick={() => delAction(i)} style={btnTinyDanger}>×</button>
                        </div>
                      ))}
                    </div>
                    <div style={{ display: "flex", gap: 6 }}>
                      <button onClick={submitMinutes} style={btnPrimary}>저장 (status → 완료)</button>
                      <button onClick={() => { setEditingMinutes(false); setMinutesDraft(null); }} style={btnGhost}>취소</button>
                    </div>
                  </div>
                )}
              </div>
            </>)}
          </div>
        )}
      </div>

      {/* Create modal */}
      {creating && (
        <div style={modalBack} onClick={() => setCreating(false)}>
          <div onClick={e => e.stopPropagation()} style={modalCard}>
            <div style={{ fontSize: 14, fontWeight: 700, color: "var(--accent)", fontFamily: "monospace", marginBottom: 12 }}>+ 새 회의 생성</div>
            <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "8px 10px", alignItems: "center" }}>
              <span style={lbl}>제목 *</span>
              <input value={draft.title} onChange={e => setDraft({ ...draft, title: e.target.value })} placeholder="회의 제목" style={inp} autoFocus />
              <span style={lbl}>주관자</span>
              <input value={draft.owner} onChange={e => setDraft({ ...draft, owner: e.target.value })} placeholder={`기본: ${me}`} style={inp} />
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
            </div>
            <div style={{ display: "flex", gap: 6, marginTop: 14, justifyContent: "flex-end" }}>
              <button onClick={() => setCreating(false)} style={btnGhost}>취소</button>
              <button onClick={submitCreate} style={btnPrimary}>생성</button>
            </div>
          </div>
        </div>
      )}

      {/* PageGear — 좌하단 고정 (전 탭 통일) */}
      <PageGear title="회의관리 설정" canEdit={isAdmin} position="bottom-left">
        <div style={{ fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.6 }}>
          카테고리 팔레트는 <b>변경점 달력 → ⚙ 설정</b> 에서 편집합니다.<br />
          여기서 회의 카테고리 색은 달력 카테고리와 공유됩니다.
        </div>
      </PageGear>
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
