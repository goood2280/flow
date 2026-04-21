/* My_Meeting.jsx v8.7.2 — 회의관리 (Meeting Management).
   - 좌측: 회의 목록 (status 필터 + 검색).
   - 우측: 선택된 회의 상세 → 메타 / 아젠다 / 회의록 / 결정사항·액션아이템.
   - 회의 생성: 누구나. 메타 수정·삭제: 주관자/admin.
   - 아젠다 추가: 누구나 (담당자=본인). 수정·삭제: 담당자·주관자·admin.
   - 회의록 작성: 주관자·admin. 저장 시 status auto → completed.
*/
import { useEffect, useMemo, useState } from "react";
import { sf, postJson } from "../lib/api";

const API = "/api/meetings";

const STATUS_LABEL = {
  scheduled: "예정",
  in_progress: "진행중",
  completed: "완료",
  cancelled: "취소",
};
const STATUS_COLOR = {
  scheduled: "#3b82f6",
  in_progress: "#f59e0b",
  completed: "#22c55e",
  cancelled: "#6b7280",
};

function dtPretty(s) {
  if (!s) return "";
  const v = s.replace("T", " ").slice(0, 16);
  return v;
}

function dtForInput(s) {
  if (!s) return "";
  return s.slice(0, 16);
}

function isUrl(s) {
  if (!s) return false;
  return /^https?:\/\//i.test(s);
}

export default function My_Meeting({ user }) {
  const [meetings, setMeetings] = useState([]);
  const [loading, setLoading] = useState(false);
  const [filterStatus, setFilterStatus] = useState("");
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState(null);
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState({ title: "", owner: "", scheduled_at: "" });
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

  useEffect(() => { reload(); /* eslint-disable-next-line */ }, [filterStatus]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return meetings;
    return meetings.filter(m => {
      const hay = [
        m.title || "", m.owner || "", m.status || "",
        ...(m.agendas || []).map(a => `${a.title || ""} ${a.owner || ""} ${a.description || ""}`),
        m.minutes?.body || "",
        ...((m.minutes?.decisions) || []),
      ].join(" ").toLowerCase();
      return hay.includes(q);
    });
  }, [meetings, search]);

  const selected = useMemo(() => meetings.find(m => m.id === selectedId) || null, [meetings, selectedId]);

  const canEditMeta = (m) => isAdmin || (m && m.owner === me);
  const canEditMinutes = canEditMeta;
  const canEditAgenda = (m, a) => isAdmin || (m && m.owner === me) || (a && a.owner === me);

  // ── Create new meeting ──
  const submitCreate = () => {
    const t = draft.title.trim();
    if (!t) { alert("회의 제목을 입력하세요"); return; }
    postJson(`${API}/create`, {
      title: t,
      owner: (draft.owner || me).trim(),
      scheduled_at: draft.scheduled_at || "",
    }).then(d => {
      setCreating(false);
      setDraft({ title: "", owner: "", scheduled_at: "" });
      reload();
      setSelectedId(d.meeting?.id || null);
    }).catch(e => alert(e.message || "생성 실패"));
  };

  // ── Meeting meta edit ──
  const startEditMeta = () => {
    if (!selected) return;
    setMetaDraft({
      title: selected.title || "",
      owner: selected.owner || "",
      scheduled_at: dtForInput(selected.scheduled_at || ""),
      status: selected.status || "scheduled",
    });
    setEditingMeta(true);
  };
  const submitEditMeta = () => {
    if (!selected || !metaDraft) return;
    postJson(`${API}/update`, {
      id: selected.id,
      title: metaDraft.title,
      owner: metaDraft.owner,
      scheduled_at: metaDraft.scheduled_at,
      status: metaDraft.status,
    }).then(() => { setEditingMeta(false); setMetaDraft(null); reload(); })
      .catch(e => alert(e.message || "저장 실패"));
  };
  const removeMeeting = () => {
    if (!selected) return;
    if (!confirm(`회의 "${selected.title}" 을(를) 삭제할까요?`)) return;
    sf(`${API}/delete?id=${encodeURIComponent(selected.id)}`, { method: "POST" })
      .then(() => { setSelectedId(null); reload(); })
      .catch(e => alert(e.message));
  };

  // ── Agenda CRUD ──
  const addAgenda = () => {
    if (!selected) return;
    const t = agendaDraft.title.trim();
    if (!t) { alert("아젠다 제목을 입력하세요"); return; }
    postJson(`${API}/agenda/add`, {
      meeting_id: selected.id,
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
    if (!selected || !editingAgendaId || !agendaEditDraft) return;
    postJson(`${API}/agenda/update`, {
      meeting_id: selected.id,
      agenda_id: editingAgendaId,
      title: agendaEditDraft.title,
      description: agendaEditDraft.description,
      link: agendaEditDraft.link,
      owner: agendaEditDraft.owner,
    }).then(() => {
      setEditingAgendaId(null); setAgendaEditDraft(null); reload();
    }).catch(e => alert(e.message || "수정 실패"));
  };
  const removeAgenda = (a) => {
    if (!selected) return;
    if (!confirm(`아젠다 "${a.title}" 을(를) 삭제할까요?`)) return;
    sf(`${API}/agenda/delete?meeting_id=${encodeURIComponent(selected.id)}&agenda_id=${encodeURIComponent(a.id)}`,
      { method: "POST" })
      .then(() => reload()).catch(e => alert(e.message));
  };

  // ── Minutes ──
  const startEditMinutes = () => {
    if (!selected) return;
    const m = selected.minutes || {};
    setMinutesDraft({
      body: m.body || "",
      decisions: (m.decisions || []).slice(),
      action_items: (m.action_items || []).map(a => ({ ...a })),
    });
    setEditingMinutes(true);
  };
  const addDecision = () => setMinutesDraft(d => ({ ...d, decisions: [...d.decisions, ""] }));
  const updDecision = (i, v) => setMinutesDraft(d => {
    const n = d.decisions.slice(); n[i] = v; return { ...d, decisions: n };
  });
  const delDecision = (i) => setMinutesDraft(d => ({ ...d, decisions: d.decisions.filter((_, j) => j !== i) }));
  const addAction = () => setMinutesDraft(d => ({ ...d, action_items: [...d.action_items, { text: "", owner: "", due: "" }] }));
  const updAction = (i, k, v) => setMinutesDraft(d => {
    const n = d.action_items.slice(); n[i] = { ...n[i], [k]: v }; return { ...d, action_items: n };
  });
  const delAction = (i) => setMinutesDraft(d => ({ ...d, action_items: d.action_items.filter((_, j) => j !== i) }));
  const submitMinutes = () => {
    if (!selected || !minutesDraft) return;
    postJson(`${API}/minutes/save`, {
      meeting_id: selected.id,
      body: minutesDraft.body,
      decisions: minutesDraft.decisions,
      action_items: minutesDraft.action_items,
    }).then(() => { setEditingMinutes(false); setMinutesDraft(null); reload(); })
      .catch(e => alert(e.message || "저장 실패"));
  };

  return (
    <div style={{ display: "flex", height: "calc(100vh - 48px)", background: "var(--bg-primary)", color: "var(--text-primary)" }}>
      {/* Left list */}
      <div style={{ width: 340, minWidth: 300, borderRight: "1px solid var(--border)", background: "var(--bg-secondary)", display: "flex", flexDirection: "column" }}>
        <div style={{ padding: "12px 14px", borderBottom: "1px solid var(--border)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
            <span style={{ fontSize: 14, fontWeight: 700, fontFamily: "monospace", color: "var(--accent)" }}>🗓 회의관리</span>
            <span style={{ flex: 1 }} />
            <button onClick={() => setCreating(true)} style={btnPrimary}>+ 새 회의</button>
          </div>
          <input value={search} onChange={e => setSearch(e.target.value)} placeholder="제목/주관자/아젠다 검색..." style={inp} />
          <div style={{ marginTop: 8, display: "flex", gap: 4, flexWrap: "wrap" }}>
            {["", "scheduled", "in_progress", "completed", "cancelled"].map(s => (
              <span key={s || "all"} onClick={() => setFilterStatus(s)} style={{
                padding: "3px 10px", borderRadius: 999, fontSize: 10, cursor: "pointer",
                fontFamily: "monospace",
                background: filterStatus === s ? "var(--accent-glow)" : "var(--bg-card)",
                color: filterStatus === s ? "var(--accent)" : "var(--text-secondary)",
                border: "1px solid " + (filterStatus === s ? "var(--accent)" : "var(--border)"),
              }}>{s ? STATUS_LABEL[s] : "전체"}</span>
            ))}
          </div>
        </div>
        <div style={{ flex: 1, overflow: "auto", padding: "8px 6px" }}>
          {loading && <div style={{ padding: 20, textAlign: "center", color: "var(--text-secondary)", fontSize: 11 }}>로딩...</div>}
          {!loading && filtered.length === 0 && <div style={{ padding: 30, textAlign: "center", color: "var(--text-secondary)", fontSize: 11 }}>회의 없음</div>}
          {filtered.map(m => {
            const sel = m.id === selectedId;
            const status = m.status || "scheduled";
            return (
              <div key={m.id} onClick={() => { setSelectedId(m.id); setEditingMeta(false); setEditingMinutes(false); setEditingAgendaId(null); }} style={{
                margin: "4px 6px", padding: "10px 12px", borderRadius: 6, cursor: "pointer",
                background: sel ? "var(--accent-glow)" : "var(--bg-card)",
                border: "1px solid " + (sel ? "var(--accent)" : "var(--border)"),
              }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
                  <span style={{ width: 7, height: 7, borderRadius: "50%", background: STATUS_COLOR[status] }} />
                  <span style={{ fontSize: 12, fontWeight: 600, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{m.title}</span>
                  <span style={{ fontSize: 9, padding: "1px 6px", borderRadius: 3, color: STATUS_COLOR[status], border: "1px solid " + STATUS_COLOR[status] }}>{STATUS_LABEL[status]}</span>
                </div>
                <div style={{ fontSize: 10, color: "var(--text-secondary)", fontFamily: "monospace", display: "flex", gap: 8 }}>
                  <span>👤 {m.owner}</span>
                  {m.scheduled_at && <span>🕒 {dtPretty(m.scheduled_at)}</span>}
                </div>
                <div style={{ marginTop: 4, fontSize: 10, color: "var(--text-secondary)", display: "flex", gap: 8 }}>
                  <span>📋 아젠다 {(m.agendas || []).length}</span>
                  {m.minutes && <span style={{ color: "#22c55e" }}>📝 회의록 ✓</span>}
                  {m.minutes?.decisions?.length > 0 && <span>⚡ 결정 {m.minutes.decisions.length}</span>}
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
            <div style={{ marginBottom: 18, padding: 16, borderRadius: 8, background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
                <span style={{ fontSize: 11, padding: "3px 10px", borderRadius: 999, color: STATUS_COLOR[selected.status || "scheduled"], border: "1px solid " + STATUS_COLOR[selected.status || "scheduled"] }}>
                  ● {STATUS_LABEL[selected.status || "scheduled"]}
                </span>
                <span style={{ fontSize: 18, fontWeight: 700, flex: 1 }}>{selected.title}</span>
                {canEditMeta(selected) && !editingMeta && <button onClick={startEditMeta} style={btnGhost}>✎ 수정</button>}
                {canEditMeta(selected) && <button onClick={removeMeeting} style={btnDanger}>삭제</button>}
              </div>
              {!editingMeta && (
                <div style={{ display: "grid", gridTemplateColumns: "auto 1fr auto 1fr", gap: "6px 14px", fontSize: 12 }}>
                  <span style={lbl}>주관자</span><span style={val}>{selected.owner}</span>
                  <span style={lbl}>예정 일시</span><span style={val}>{dtPretty(selected.scheduled_at) || "—"}</span>
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
                  <span style={lbl}>예정 일시</span>
                  <input type="datetime-local" value={metaDraft.scheduled_at} onChange={e => setMetaDraft({ ...metaDraft, scheduled_at: e.target.value })} style={inp} />
                  <span style={lbl}>상태</span>
                  <select value={metaDraft.status} onChange={e => setMetaDraft({ ...metaDraft, status: e.target.value })} style={inp}>
                    {Object.entries(STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </select>
                  <div />
                  <div style={{ display: "flex", gap: 6 }}>
                    <button onClick={submitEditMeta} style={btnPrimary}>저장</button>
                    <button onClick={() => { setEditingMeta(false); setMetaDraft(null); }} style={btnGhost}>취소</button>
                  </div>
                </div>
              )}
            </div>

            {/* Agendas */}
            <div style={{ marginBottom: 18, padding: 16, borderRadius: 8, background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
              <div style={{ fontSize: 13, fontWeight: 700, color: "var(--accent)", marginBottom: 10, fontFamily: "monospace" }}>
                📋 아젠다 ({(selected.agendas || []).length})
              </div>
              {(selected.agendas || []).length === 0 && (
                <div style={{ padding: 14, textAlign: "center", color: "var(--text-secondary)", fontSize: 11, marginBottom: 10 }}>
                  아젠다 없음. 아래에서 첫 아젠다를 추가하세요.
                </div>
              )}
              {(selected.agendas || []).map((a, i) => (
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
              {/* New agenda inline form */}
              <div style={{ marginTop: 12, paddingTop: 12, borderTop: "1px dashed var(--border)" }}>
                <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 6, fontFamily: "monospace" }}>+ 새 아젠다 추가 (담당자: {(agendaDraft.owner || me)})</div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
                  <input value={agendaDraft.title} onChange={e => setAgendaDraft({ ...agendaDraft, title: e.target.value })} placeholder="아젠다 제목 *" style={inp} />
                  <input value={agendaDraft.owner} onChange={e => setAgendaDraft({ ...agendaDraft, owner: e.target.value })} placeholder={`담당자 (기본: ${me})`} style={inp} />
                </div>
                <textarea value={agendaDraft.description} onChange={e => setAgendaDraft({ ...agendaDraft, description: e.target.value })} rows={2} placeholder="설명 (선택)" style={{ ...inp, marginTop: 6, resize: "vertical", fontFamily: "inherit" }} />
                <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
                  <input value={agendaDraft.link} onChange={e => setAgendaDraft({ ...agendaDraft, link: e.target.value })} placeholder="https://참고 링크 (선택)" style={{ ...inp, flex: 1 }} />
                  <button onClick={addAgenda} style={btnPrimary}>+ 추가</button>
                </div>
              </div>
            </div>

            {/* Minutes */}
            <div style={{ marginBottom: 18, padding: 16, borderRadius: 8, background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
              <div style={{ display: "flex", alignItems: "center", marginBottom: 10 }}>
                <span style={{ fontSize: 13, fontWeight: 700, color: "var(--accent)", fontFamily: "monospace", flex: 1 }}>📝 회의록</span>
                {canEditMinutes(selected) && !editingMinutes && (
                  <button onClick={startEditMinutes} style={btnGhost}>{selected.minutes ? "✎ 수정" : "+ 작성"}</button>
                )}
              </div>
              {!editingMinutes && !selected.minutes && (
                <div style={{ padding: 20, textAlign: "center", color: "var(--text-secondary)", fontSize: 11 }}>
                  회의록 미작성. 회의가 끝난 뒤 주관자({selected.owner})가 작성합니다.
                </div>
              )}
              {!editingMinutes && selected.minutes && (
                <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  {selected.minutes.body && (
                    <div>
                      <div style={lbl}>본문</div>
                      <div style={{ marginTop: 4, padding: 10, borderRadius: 5, background: "var(--bg-card)", border: "1px solid var(--border)", fontSize: 12, lineHeight: 1.6, whiteSpace: "pre-wrap" }}>
                        {selected.minutes.body}
                      </div>
                    </div>
                  )}
                  {(selected.minutes.decisions || []).length > 0 && (
                    <div>
                      <div style={lbl}>⚡ 결정사항 ({selected.minutes.decisions.length})</div>
                      <ol style={{ marginTop: 4, paddingLeft: 22, fontSize: 12, lineHeight: 1.7 }}>
                        {selected.minutes.decisions.map((d, i) => <li key={i} style={{ color: "var(--text-primary)" }}>{d}</li>)}
                      </ol>
                    </div>
                  )}
                  {(selected.minutes.action_items || []).length > 0 && (
                    <div>
                      <div style={lbl}>✅ 액션 아이템 ({selected.minutes.action_items.length})</div>
                      <table style={{ width: "100%", marginTop: 4, fontSize: 11, borderCollapse: "collapse" }}>
                        <thead>
                          <tr style={{ background: "var(--bg-card)" }}>
                            <th style={th}>내용</th>
                            <th style={{ ...th, width: 110 }}>담당</th>
                            <th style={{ ...th, width: 110 }}>마감</th>
                          </tr>
                        </thead>
                        <tbody>
                          {selected.minutes.action_items.map((a, i) => (
                            <tr key={i}>
                              <td style={td}>{a.text}</td>
                              <td style={td}>{a.owner || "—"}</td>
                              <td style={td}>{a.due || "—"}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                  <div style={{ fontSize: 10, color: "var(--text-secondary)", fontFamily: "monospace" }}>
                    작성: {selected.minutes.author} · {dtPretty(selected.minutes.updated_at)}
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
                    {minutesDraft.decisions.map((d, i) => (
                      <div key={i} style={{ display: "flex", gap: 6, marginTop: 4 }}>
                        <input value={d} onChange={e => updDecision(i, e.target.value)} placeholder={`결정사항 #${i + 1}`} style={{ ...inp, flex: 1 }} />
                        <button onClick={() => delDecision(i)} style={btnTinyDanger}>×</button>
                      </div>
                    ))}
                  </div>
                  <div>
                    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      <span style={lbl}>✅ 액션 아이템</span>
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
              <span style={lbl}>예정 일시</span>
              <input type="datetime-local" value={draft.scheduled_at} onChange={e => setDraft({ ...draft, scheduled_at: e.target.value })} style={inp} />
            </div>
            <div style={{ display: "flex", gap: 6, marginTop: 14, justifyContent: "flex-end" }}>
              <button onClick={() => setCreating(false)} style={btnGhost}>취소</button>
              <button onClick={submitCreate} style={btnPrimary}>생성</button>
            </div>
          </div>
        </div>
      )}
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
const modalCard = { width: 460, maxWidth: "92%", padding: 18, borderRadius: 10, background: "var(--bg-secondary)", border: "1px solid var(--border)", boxShadow: "0 8px 24px rgba(0,0,0,0.4)" };
