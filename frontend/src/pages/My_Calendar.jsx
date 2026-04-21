/* My_Calendar.jsx v8.7.8 — 변경점 달력 + 회의 결정/액션 auto-sync 뷰.
   - 월간 그리드 + 날짜 클릭 → 좌측 사이드 상세/입력.
   - 카테고리 색상 (admin 편집 가능).
   - 검색 (title/body/author/category 키워드).
   - 회의별 필터 (source_type=meeting_*인 이벤트 대상).
   - 결정사항(filled) / 액션아이템(outline, 범위 bar) 시각 구분.
   - 낙관적 잠금 / 변경 이력 유지.
*/
import { useEffect, useMemo, useState } from "react";
import { sf, postJson } from "../lib/api";
import PageGear from "../components/PageGear";

const API = "/api/calendar";
const WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"];

function pad(n) { return n < 10 ? "0" + n : "" + n; }
function ymd(d) { return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()); }
function ym(d) { return d.getFullYear() + "-" + pad(d.getMonth() + 1); }

function buildMonthGrid(viewDate) {
  const first = new Date(viewDate.getFullYear(), viewDate.getMonth(), 1);
  const start = new Date(first);
  start.setDate(first.getDate() - first.getDay());
  const cells = [];
  for (let i = 0; i < 42; i++) {
    const d = new Date(start);
    d.setDate(start.getDate() + i);
    cells.push(d);
  }
  return cells;
}

// "YYYY-MM-DD" range (inclusive) → array of ISO dates
function dateRange(start, end) {
  if (!start) return [];
  const [sy, sm, sd] = start.split("-").map(Number);
  const [ey, em, ed] = (end || start).split("-").map(Number);
  const a = new Date(sy, sm - 1, sd);
  const b = new Date(ey, em - 1, ed);
  if (b < a) return [start];
  const out = [];
  const cur = new Date(a);
  while (cur <= b) { out.push(ymd(cur)); cur.setDate(cur.getDate() + 1); }
  return out;
}

const SOURCE_LABEL = {
  manual: "일반",
  meeting_decision: "결정사항",
  meeting_action: "액션아이템",
};

export default function My_Calendar({ user }) {
  const [view, setView] = useState(new Date());
  const [events, setEvents] = useState([]);
  const [cats, setCats] = useState([]);
  const [meetings, setMeetings] = useState([]);
  const [meetingFilter, setMeetingFilter] = useState("all"); // "all" | "none-manual" | meeting_id
  const [selected, setSelected] = useState(null);
  const [search, setSearch] = useState("");
  const [searchResults, setSearchResults] = useState(null);
  const [editCats, setEditCats] = useState(false);
  const [draftCats, setDraftCats] = useState([]);
  const [conflict, setConflict] = useState(null);
  const [loading, setLoading] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  // v8.8.2: 공개범위 그룹 선택 (일반 이벤트).
  const [myGroups, setMyGroups] = useState([]);

  const monthStr = ym(view);
  const isAdmin = user?.role === "admin";

  const reload = () => {
    setLoading(true);
    sf(`${API}/events?month=${monthStr}`)
      .then(d => setEvents(d.events || []))
      .catch(() => setEvents([]))
      .finally(() => setLoading(false));
    sf(`${API}/meetings`).then(d => setMeetings(d.meetings || [])).catch(() => setMeetings([]));
  };
  const reloadCats = () => sf(`${API}/categories`).then(d => setCats(d.categories || [])).catch(() => setCats([]));

  useEffect(() => { reload(); }, [monthStr]);
  useEffect(() => { reloadCats(); }, []);
  useEffect(() => { sf("/api/groups/list").then(d => setMyGroups(d.groups || [])).catch(() => setMyGroups([])); }, []);

  const filteredEvents = useMemo(() => {
    if (meetingFilter === "all") return events;
    if (meetingFilter === "manual") return events.filter(e => (e.source_type || "manual") === "manual");
    return events.filter(e => (e.meeting_ref || {}).meeting_id === meetingFilter);
  }, [events, meetingFilter]);

  const grid = useMemo(() => buildMonthGrid(view), [view]);

  // Expand events to occurrences per date (handles end_date).
  // Returns { [ymd]: [{event, kind:'single'|'start'|'middle'|'end', dayIdx}] }
  const byDate = useMemo(() => {
    const m = {};
    for (const e of filteredEvents) {
      const start = (e.date || "").slice(0, 10);
      if (!start) continue;
      const end = (e.end_date || "").slice(0, 10);
      const days = end && end !== start ? dateRange(start, end) : [start];
      days.forEach((k, i) => {
        if (!m[k]) m[k] = [];
        const kind = days.length === 1 ? "single"
          : i === 0 ? "start"
          : i === days.length - 1 ? "end"
          : "middle";
        m[k].push({ event: e, kind, dayIdx: i, total: days.length });
      });
    }
    return m;
  }, [filteredEvents]);

  const catColor = (name) => (cats.find(c => c.name === name)?.color) || "#6b7280";
  const today = ymd(new Date());

  const openNew = (date) => {
    setConflict(null); setHistoryOpen(false);
    setSelected({ date, title: "", body: "", category: cats[0]?.name || "", end_date: "", version: 0, _new: true, group_ids: [] });
  };
  const openEdit = (e) => {
    setConflict(null); setHistoryOpen(false);
    setSelected({ ...e });
  };

  const save = () => {
    if (!selected) return;
    const t = (selected.title || "").trim();
    if (!t) { alert("제목을 입력하세요"); return; }
    if ((selected.source_type || "manual") !== "manual" && !selected._new) {
      alert("회의에서 auto-sync 된 이벤트는 회의관리에서 수정해주세요.");
      return;
    }
    if (selected._new) {
      postJson(`${API}/event`, {
        date: selected.date, end_date: selected.end_date || "",
        title: t, body: selected.body || "", category: selected.category || "",
        group_ids: selected.group_ids || [],
      }).then(d => { setSelected(d.event); reload(); })
        .catch(e => alert(e.message || "생성 실패"));
    } else {
      postJson(`${API}/event/update`, {
        id: selected.id, version: selected.version,
        date: selected.date, end_date: selected.end_date || "",
        title: t, body: selected.body || "", category: selected.category || "",
        group_ids: selected.group_ids || [],
      }).then(d => {
        if (d.conflict) { setConflict(d.event); return; }
        setSelected(d.event); reload();
      }).catch(e => alert(e.message || "저장 실패"));
    }
  };

  const acceptServer = () => {
    if (conflict) { setSelected(conflict); setConflict(null); }
  };

  const remove = () => {
    if (!selected?.id) { setSelected(null); return; }
    if ((selected.source_type || "manual") !== "manual") {
      alert("회의 auto-sync 이벤트는 회의관리에서 해당 결정/액션을 삭제해주세요.");
      return;
    }
    if (!confirm("이 이벤트를 삭제하시겠습니까?")) return;
    sf(`${API}/event/delete?id=${encodeURIComponent(selected.id)}`, { method: "POST" })
      .then(() => { setSelected(null); reload(); })
      .catch(e => alert(e.message));
  };

  const runSearch = () => {
    const q = (search || "").trim();
    if (!q) { setSearchResults(null); return; }
    sf(`${API}/events/search?q=${encodeURIComponent(q)}`)
      .then(d => setSearchResults(d.events || []))
      .catch(() => setSearchResults([]));
  };

  const navMonth = (delta) => {
    const d = new Date(view); d.setMonth(d.getMonth() + delta); d.setDate(1);
    setView(d);
  };

  const startEditCats = () => {
    setDraftCats(cats.map(c => ({ ...c })));
    setEditCats(true);
  };
  const saveCats = () => {
    postJson(`${API}/categories/save`, { categories: draftCats })
      .then(d => { setCats(d.categories || []); setEditCats(false); })
      .catch(e => alert(e.message));
  };

  const renderOccurrence = (occ) => {
    const e = occ.event;
    const srcType = e.source_type || "manual";
    // v8.7.9: meeting events use the meeting's unique palette color; manual events fall back to category color.
    const meetingColor = (e.meeting_ref && e.meeting_ref.color) || "";
    const color = meetingColor || catColor(e.category);
    const isAction = srcType === "meeting_action";
    const isDecision = srcType === "meeting_decision";
    // v8.7.9: actions = pin on due date (single-day), decisions = filled single-day.
    const isMid = occ.kind === "middle";
    const isEnd = occ.kind === "end";
    const isStart = occ.kind === "start";
    const radius = isMid ? 0
      : isStart ? "3px 0 0 3px"
      : isEnd ? "0 3px 3px 0"
      : "3px";
    const label = (isStart || occ.kind === "single")
      ? (isDecision ? "● " : isAction ? "📍 " : "") + (e.title || "")
      : (isEnd ? "↘ " : "…");
    // Styles
    const fill = isAction ? color + "14" : (color + "22");
    const border = `1px solid ${color}`;
    const borderLeft = isAction ? `4px solid ${color}` : `3px solid ${color}`;
    return (
      <div key={e.id + "_" + occ.dayIdx} onClick={ev => { ev.stopPropagation(); openEdit(e); }} style={{
        fontSize: 10, padding: "2px 5px", borderRadius: radius,
        background: fill,
        border: isMid || isEnd || isStart ? border : "none",
        borderLeft: (occ.kind === "single" || isStart) ? borderLeft : (isMid ? "none" : border),
        borderRight: (occ.kind === "single" || isEnd) ? undefined : (isMid ? "none" : undefined),
        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        color: "var(--text-primary)",
        fontWeight: isDecision ? 600 : (isAction ? 500 : 400),
        fontStyle: "normal",
        opacity: isMid ? 0.75 : 1,
      }} title={`${SOURCE_LABEL[srcType] || "이벤트"} · ${e.title}\n${e.body || ""}`}>
        {label}
      </div>
    );
  };

  return (
    <div style={{ display: "flex", height: "calc(100vh - 48px)", background: "var(--bg-primary)", color: "var(--text-primary)", position: "relative" }}>
      <PageGear title="변경점 달력 설정" canEdit={isAdmin} position="bottom-left">
        <div style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 10 }}>
          카테고리별 색상을 관리합니다. 회의관리의 회의 카테고리도 이 팔레트를 공유합니다.
        </div>
        <button onClick={startEditCats} style={{ padding: "8px 14px", borderRadius: 6, border: "1px solid var(--accent)", background: "transparent", color: "var(--accent)", fontSize: 12, cursor: "pointer", fontWeight: 600 }}>🎨 카테고리 팔레트 편집</button>
      </PageGear>
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
        <div style={{ padding: "14px 20px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <span style={{ fontSize: 16, fontWeight: 700, fontFamily: "monospace", color: "var(--accent)" }}>📅 변경점 달력</span>
          <button onClick={() => navMonth(-1)} style={navBtn}>‹</button>
          <span style={{ fontSize: 15, fontWeight: 700, minWidth: 130, textAlign: "center" }}>{view.getFullYear()}년 {view.getMonth() + 1}월</span>
          <button onClick={() => navMonth(1)} style={navBtn}>›</button>
          <button onClick={() => setView(new Date())} style={{ ...navBtn, padding: "4px 10px" }}>오늘</button>
          <button onClick={() => { reload(); }} style={{ ...navBtn, padding: "4px 10px" }} title="회의 auto-sync 이벤트를 포함해 서버에서 다시 불러옵니다">↻ 새로고침</button>
          <select value={meetingFilter} onChange={e => setMeetingFilter(e.target.value)}
            style={{ padding: "4px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12, outline: "none" }}
            title="회의별 필터">
            <option value="all">전체 이벤트</option>
            <option value="manual">일반 이벤트만</option>
            {meetings.map(m => (
              <option key={m.meeting_id} value={m.meeting_id}>{m.color ? "● " : "🗓 "}{m.meeting_title || m.meeting_id} ({m.count})</option>
            ))}
          </select>
          <div style={{ flex: 1 }} />
          <input value={search} onChange={e => setSearch(e.target.value)} onKeyDown={e => e.key === "Enter" && runSearch()}
            placeholder="검색…"
            style={{ width: 180, padding: "6px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12, outline: "none" }} />
          <button onClick={runSearch} style={navBtn}>검색</button>
          {searchResults && <button onClick={() => { setSearch(""); setSearchResults(null); }} style={navBtn}>×</button>}
        </div>
        <div style={{ padding: "6px 20px", display: "flex", gap: 14, fontSize: 10, color: "var(--text-secondary)", borderBottom: "1px solid var(--border)" }}>
          <span><span style={{ display: "inline-block", width: 10, height: 10, background: "#3b82f680", border: "1px solid #3b82f6", marginRight: 4, verticalAlign: "middle" }} /> 일반</span>
          <span><span style={{ display: "inline-block", width: 10, height: 10, background: "#3b82f680", border: "1px solid #3b82f6", marginRight: 4, verticalAlign: "middle" }} /> 결정사항 (N차 회의 결정사항, 회의일자)</span>
          <span><span style={{ display: "inline-block", width: 10, height: 10, background: "#3b82f620", border: "1px solid #3b82f6", borderLeft: "3px solid #3b82f6", marginRight: 4, verticalAlign: "middle" }} /> 📍 액션아이템 (마감일 단독)</span>
        </div>

        <div style={{ flex: 1, overflow: "auto", padding: 16 }}>
          {searchResults ? (
            <div>
              <div style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 8 }}>검색 결과: {searchResults.length}건</div>
              {searchResults.length === 0 && <div style={{ padding: 40, textAlign: "center", color: "var(--text-secondary)" }}>일치하는 이벤트 없음</div>}
              {searchResults.map(e => (
                <div key={e.id} onClick={() => openEdit(e)} style={{
                  padding: 10, marginBottom: 6, borderRadius: 6, background: "var(--bg-card)",
                  border: "1px solid var(--border)", cursor: "pointer", display: "flex", gap: 10, alignItems: "center",
                }}>
                  <span style={{ width: 8, height: 8, borderRadius: "50%", background: catColor(e.category), flexShrink: 0 }} />
                  <span style={{ fontSize: 11, fontFamily: "monospace", color: "var(--text-secondary)", minWidth: 90 }}>{e.date}{e.end_date && e.end_date !== e.date ? ` ~ ${e.end_date}` : ""}</span>
                  <span style={{ fontSize: 13, fontWeight: 600, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{e.title}</span>
                  <span style={{ fontSize: 9, padding: "1px 6px", borderRadius: 999, background: "var(--bg-secondary)", color: "var(--text-secondary)" }}>{SOURCE_LABEL[e.source_type || "manual"]}</span>
                  {e.category && <span style={{ fontSize: 10, padding: "2px 8px", borderRadius: 999, background: catColor(e.category) + "33", color: catColor(e.category) }}>{e.category}</span>}
                  <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>{e.author}</span>
                </div>
              ))}
            </div>
          ) : (
            <div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(7,1fr)", gap: 4, marginBottom: 4 }}>
                {WEEKDAYS.map((w, i) => (
                  <div key={w} style={{
                    padding: "6px 8px", fontSize: 11, fontWeight: 700, textAlign: "center",
                    color: i === 0 ? "#ef4444" : i === 6 ? "#3b82f6" : "var(--text-secondary)",
                    fontFamily: "monospace",
                  }}>{w}</div>
                ))}
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(7,1fr)", gridAutoRows: "minmax(96px,1fr)", gap: 4 }}>
                {grid.map((d, i) => {
                  const k = ymd(d);
                  const inMonth = d.getMonth() === view.getMonth();
                  const isToday = k === today;
                  const occs = byDate[k] || [];
                  return (
                    <div key={i} onClick={() => openNew(k)} style={{
                      background: isToday ? "var(--accent-glow, rgba(255,94,0,0.08))" : (inMonth ? "var(--bg-secondary)" : "var(--bg-primary)"),
                      border: isToday ? "2px solid var(--accent)" : "1px solid var(--border)",
                      boxShadow: isToday ? "0 0 0 3px rgba(255,94,0,0.18), 0 0 12px rgba(255,94,0,0.28)" : "none",
                      borderRadius: 6, padding: 6, cursor: "pointer", overflow: "visible",
                      display: "flex", flexDirection: "column", gap: 3,
                      opacity: inMonth ? 1 : 0.45,
                      position: "relative",
                    }}>
                      <div style={{
                        display: "flex", alignItems: "center", gap: 6,
                        fontSize: isToday ? 13 : 11, fontWeight: isToday ? 800 : 500,
                        color: isToday ? "var(--accent)" : (d.getDay() === 0 ? "#ef4444" : d.getDay() === 6 ? "#3b82f6" : "var(--text-primary)"),
                        fontFamily: "monospace",
                        textShadow: isToday ? "0 0 6px rgba(255,94,0,0.4)" : "none",
                        whiteSpace: "nowrap",
                      }}>
                        <span>{d.getDate()}</span>
                        {isToday && (
                          <span title="오늘" style={{
                            background: "var(--accent)", color: "#fff",
                            padding: "1px 6px", borderRadius: 999,
                            fontSize: 9, fontWeight: 700, letterSpacing: 0.3,
                            lineHeight: 1.4, fontFamily: "monospace",
                            boxShadow: "0 1px 3px rgba(0,0,0,0.3)",
                          }}>TODAY</span>
                        )}
                      </div>
                      <div style={{ display: "flex", flexDirection: "column", gap: 2, overflow: "hidden" }}>
                        {occs.slice(0, 4).map(renderOccurrence)}
                        {occs.length > 4 && <div style={{ fontSize: 9, color: "var(--text-secondary)", padding: "0 4px" }}>+{occs.length - 4}건</div>}
                      </div>
                    </div>
                  );
                })}
              </div>
              <div style={{ marginTop: 10, fontSize: 10, color: "var(--text-secondary)" }}>
                {loading ? "로딩…" : `${filteredEvents.length}건`} · 셀 클릭 → 신규 등록 · 이벤트 클릭 → 편집 · 회의 이벤트는 회의관리에서만 수정 가능
              </div>
            </div>
          )}
        </div>
      </div>

      {selected && (
        <div style={{ width: 360, minWidth: 320, borderLeft: "1px solid var(--border)", background: "var(--bg-secondary)", display: "flex", flexDirection: "column" }}>
          <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontSize: 13, fontWeight: 700, fontFamily: "monospace", color: "var(--accent)" }}>
              {selected._new ? "+ 신규 이벤트" : `이벤트 상세 · ${SOURCE_LABEL[selected.source_type || "manual"]}`}
            </span>
            <span onClick={() => { setSelected(null); setConflict(null); }} style={{ cursor: "pointer", fontSize: 16 }}>✕</span>
          </div>
          <div style={{ flex: 1, overflow: "auto", padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
            {conflict && (
              <div style={{ padding: 10, borderRadius: 6, background: "rgba(239,68,68,0.1)", border: "1px solid #ef4444", fontSize: 11 }}>
                ⚠ 다른 사용자가 이 이벤트를 수정했습니다.
                <div style={{ marginTop: 6, display: "flex", gap: 6 }}>
                  <button onClick={acceptServer} style={smallBtnPrimary}>최신 데이터 불러오기</button>
                  <button onClick={() => setConflict(null)} style={smallBtn}>닫기</button>
                </div>
              </div>
            )}
            {!selected._new && (selected.source_type || "manual") !== "manual" && (
              <div style={{ padding: 8, borderRadius: 5, background: "rgba(59,130,246,0.08)", border: "1px dashed #3b82f6", fontSize: 11, color: "var(--text-secondary)" }}>
                🔗 회의에서 auto-sync 된 이벤트입니다. 수정/삭제는 회의관리의 해당 결정/액션에서.
                {selected.meeting_ref?.meeting_title && <div style={{ marginTop: 4, fontWeight: 600, color: "var(--accent)" }}>🗓 {selected.meeting_ref.meeting_title}</div>}
              </div>
            )}
            <Field label="날짜">
              <input type="date" value={(selected.date || "").slice(0, 10)} onChange={e => setSelected({ ...selected, date: e.target.value })} style={inp} disabled={!selected._new && (selected.source_type || "manual") !== "manual"} />
            </Field>
            <Field label="종료일 (선택 · 범위 이벤트)">
              <input type="date" value={(selected.end_date || "").slice(0, 10)}
                min={(selected.date || "").slice(0, 10)}
                onChange={e => setSelected({ ...selected, end_date: e.target.value })} style={inp}
                disabled={!selected._new && (selected.source_type || "manual") !== "manual"} />
            </Field>
            <Field label="제목">
              <input value={selected.title || ""} onChange={e => setSelected({ ...selected, title: e.target.value })} placeholder="이벤트 제목"
                style={inp} disabled={!selected._new && (selected.source_type || "manual") !== "manual"} />
            </Field>
            <Field label="카테고리">
              <select value={selected.category || ""} onChange={e => setSelected({ ...selected, category: e.target.value })} style={inp}
                disabled={!selected._new && (selected.source_type || "manual") !== "manual"}>
                <option value="">(없음)</option>
                {cats.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
              </select>
            </Field>
            <Field label="내용">
              <textarea value={selected.body || ""} onChange={e => setSelected({ ...selected, body: e.target.value })} rows={8}
                placeholder="변경 내용·배경·참석자 등"
                style={{ ...inp, resize: "vertical", fontFamily: "inherit" }}
                disabled={!selected._new && (selected.source_type || "manual") !== "manual"} />
            </Field>
            {/* v8.8.2: 일반 이벤트 공개범위 — 그룹 지정. 비우면 전원 공개. */}
            {(selected._new || (selected.source_type || "manual") === "manual") && (
              <Field label={`공개범위 · 그룹 선택 (비우면 전원 공개)`}>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 4, padding: 6, background: "var(--bg-primary)", border: "1px solid var(--border)", borderRadius: 5, minHeight: 32 }}>
                  {myGroups.length === 0 && (
                    <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>열람 가능한 그룹이 없습니다.</span>
                  )}
                  {myGroups.map(g => {
                    const gid = g.id;
                    const sel = (selected.group_ids || []).includes(gid);
                    return (
                      <span key={gid}
                            onClick={() => {
                              const cur = selected.group_ids || [];
                              const next = sel ? cur.filter(x => x !== gid) : [...cur, gid];
                              setSelected({ ...selected, group_ids: next });
                            }}
                            style={{
                              padding: "3px 10px", borderRadius: 999, fontSize: 11, cursor: "pointer",
                              background: sel ? "var(--accent)" : "var(--bg-card)",
                              color: sel ? "#fff" : "var(--text-primary)",
                              border: "1px solid " + (sel ? "var(--accent)" : "var(--border)"),
                              fontWeight: sel ? 700 : 500,
                            }}>
                        {sel ? "● " : "○ "}{g.name}
                      </span>
                    );
                  })}
                </div>
                {(selected.group_ids || []).length > 0 && (
                  <div style={{ fontSize: 10, color: "var(--text-secondary)", marginTop: 4 }}>
                    선택한 {(selected.group_ids || []).length}개 그룹의 멤버와 본인·관리자만 열람합니다.
                  </div>
                )}
              </Field>
            )}
            {!selected._new && (
              <div style={{ fontSize: 10, color: "var(--text-secondary)", fontFamily: "monospace", lineHeight: 1.7 }}>
                <div>id: <span style={{ color: "var(--text-primary)" }}>{selected.id}</span></div>
                <div>version: <span style={{ color: "var(--text-primary)" }}>{selected.version}</span></div>
                <div>작성자: <span style={{ color: "var(--text-primary)" }}>{selected.author}</span></div>
                <div>생성: {(selected.created_at || "").replace("T", " ")}</div>
                <div>수정: {(selected.updated_at || "").replace("T", " ")}</div>
                <div onClick={() => setHistoryOpen(!historyOpen)} style={{ marginTop: 4, color: "var(--accent)", cursor: "pointer" }}>
                  {historyOpen ? "▼" : "▶"} 변경 이력 ({(selected.history || []).length})
                </div>
                {historyOpen && (
                  <div style={{ marginTop: 4, paddingLeft: 8, borderLeft: "2px solid var(--border)" }}>
                    {(selected.history || []).length === 0 && <div>이력 없음</div>}
                    {(selected.history || []).slice().reverse().map((h, i) => (
                      <div key={i} style={{ marginBottom: 6 }}>
                        <div>{(h.ts || "").replace("T", " ")} · <span style={{ color: "var(--accent)" }}>{h.actor}</span> · {h.action}</div>
                        {h.before && Object.keys(h.before).length > 0 && (
                          <div style={{ paddingLeft: 8, color: "var(--text-secondary)" }}>
                            {Object.entries(h.before).map(([k, v]) => (
                              <div key={k}>· {k}: <span style={{ textDecoration: "line-through" }}>{String(v).slice(0, 80)}</span></div>
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
          <div style={{ padding: 12, borderTop: "1px solid var(--border)", display: "flex", gap: 8 }}>
            {(selected._new || (selected.source_type || "manual") === "manual") &&
              <button onClick={save} style={{ flex: 1, padding: "8px 0", borderRadius: 5, border: "none", background: "var(--accent)", color: "#fff", fontWeight: 600, cursor: "pointer" }}>{selected._new ? "등록" : "저장"}</button>}
            {!selected._new && (selected.source_type || "manual") === "manual" &&
              <button onClick={remove} style={{ padding: "8px 14px", borderRadius: 5, border: "1px solid #ef4444", background: "transparent", color: "#ef4444", cursor: "pointer" }}>삭제</button>}
          </div>
        </div>
      )}

      {editCats && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.55)", zIndex: 9999, display: "flex", alignItems: "center", justifyContent: "center" }} onClick={() => setEditCats(false)}>
          <div onClick={e => e.stopPropagation()} style={{ width: 480, maxWidth: "90%", background: "var(--bg-secondary)", borderRadius: 10, border: "1px solid var(--border)", padding: 18 }}>
            <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 10, fontFamily: "monospace", color: "var(--accent)" }}>🎨 카테고리 관리</div>
            {!isAdmin && <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 8 }}>(관리자만 저장할 수 있습니다 — 보기 전용)</div>}
            {draftCats.map((c, i) => (
              <div key={i} style={{ display: "flex", gap: 6, marginBottom: 6, alignItems: "center" }}>
                <input value={c.name} onChange={e => { const n = [...draftCats]; n[i] = { ...n[i], name: e.target.value }; setDraftCats(n); }}
                  placeholder="이름" style={{ ...inp, flex: 1 }} disabled={!isAdmin} />
                <input type="color" value={c.color} onChange={e => { const n = [...draftCats]; n[i] = { ...n[i], color: e.target.value }; setDraftCats(n); }}
                  style={{ width: 40, height: 32, border: "1px solid var(--border)", borderRadius: 4, background: "transparent" }} disabled={!isAdmin} />
                <button onClick={() => { if (!isAdmin) return; const n = draftCats.filter((_, j) => j !== i); setDraftCats(n); }}
                  style={smallBtn} disabled={!isAdmin}>삭제</button>
              </div>
            ))}
            {isAdmin && <button onClick={() => setDraftCats([...draftCats, { name: "신규", color: "#6b7280" }])} style={{ ...smallBtn, marginTop: 4 }}>+ 카테고리 추가</button>}
            <div style={{ display: "flex", gap: 6, marginTop: 14, justifyContent: "flex-end" }}>
              <button onClick={() => setEditCats(false)} style={smallBtn}>닫기</button>
              {isAdmin && <button onClick={saveCats} style={smallBtnPrimary}>저장</button>}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const navBtn = { padding: "4px 12px", borderRadius: 5, border: "1px solid var(--border)", background: "transparent", color: "var(--text-primary)", fontSize: 12, cursor: "pointer", fontFamily: "monospace" };
const inp = { width: "100%", padding: "6px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12, outline: "none" };
const smallBtn = { padding: "5px 12px", borderRadius: 4, border: "1px solid var(--border)", background: "transparent", color: "var(--text-primary)", fontSize: 11, cursor: "pointer" };
const smallBtnPrimary = { padding: "5px 12px", borderRadius: 4, border: "none", background: "var(--accent)", color: "#fff", fontSize: 11, cursor: "pointer", fontWeight: 600 };

function Field({ label, children }) {
  return (
    <div>
      <div style={{ fontSize: 10, color: "var(--text-secondary)", marginBottom: 3, fontFamily: "monospace" }}>{label}</div>
      {children}
    </div>
  );
}
