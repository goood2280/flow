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
import Modal from "../components/Modal";
import { toast } from "../components/Toast";
import { Button, Card, Chip, EmptyState, PageHeader, Pill } from "../components/UXKit";
import FlowiPromptBox from "../components/FlowiPromptBox";

const API = "/api/calendar";
const WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"];
const MAX_VISIBLE_DAY_EVENTS = 3;
const FALLBACK_EVENT_COLOR = "var(--muted)";

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
  const [askQuestion, setAskQuestion] = useState("전체 회의와 변경점에서 마감 임박 액션 알려줘");
  const [askBusy, setAskBusy] = useState(false);
  const [askResult, setAskResult] = useState(null);
  const [askLlmAvailable, setAskLlmAvailable] = useState(false);
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
  useEffect(() => { sf("/api/llm/status").then(d => setAskLlmAvailable(!!d.available)).catch(() => setAskLlmAvailable(false)); }, []);
  useEffect(() => {
    if (meetingFilter === "all" || meetingFilter === "manual") return;
    setAskResult(null);
  }, [meetingFilter]);

  const filteredEvents = useMemo(() => {
    if (meetingFilter === "all") return events;
    if (meetingFilter === "manual") return events.filter(e => (e.source_type || "manual") === "manual");
    return events.filter(e => (e.meeting_ref || {}).meeting_id === meetingFilter);
  }, [events, meetingFilter]);

  const grid = useMemo(() => buildMonthGrid(view), [view]);

  const runMeetingAsk = (meetingId = "") => {
    const question = (askQuestion || "").trim();
    if (!question) { toast.warn("질문을 입력하세요"); return; }
    setAskBusy(true);
    const body = { question };
    if (meetingId) body.meeting_id = meetingId;
    postJson("/api/meetings/ask", body)
      .then(d => setAskResult(d))
      .catch(e => {
        setAskResult(null);
        toast.error(e.message || "회의 확인 실패");
      })
      .finally(() => setAskBusy(false));
  };

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

  const catColor = (name) => (cats.find(c => c.name === name)?.color) || "var(--muted)";
  const safeEventColor = (color) => (String(color || "").trim() || FALLBACK_EVENT_COLOR);
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
    if (!t) { toast.warn("제목을 입력하세요"); return; }
    if ((selected.source_type || "manual") !== "manual" && !selected._new) {
      toast.warn("회의에서 auto-sync 된 이벤트는 회의관리에서 수정해주세요.");
      return;
    }
    if (selected._new) {
      postJson(`${API}/event`, {
        date: selected.date, end_date: selected.end_date || "",
        title: t, body: selected.body || "", category: selected.category || "",
        group_ids: selected.group_ids || [],
      }).then(d => { setSelected(d.event); reload(); toast.ok("이벤트 생성됨"); })
        .catch(e => toast.error(e.message || "생성 실패"));
    } else {
      postJson(`${API}/event/update`, {
        id: selected.id, version: selected.version,
        date: selected.date, end_date: selected.end_date || "",
        title: t, body: selected.body || "", category: selected.category || "",
        group_ids: selected.group_ids || [],
      }).then(d => {
        if (d.conflict) { setConflict(d.event); return; }
        setSelected(d.event); reload(); toast.ok("이벤트 저장됨");
      }).catch(e => toast.error(e.message || "저장 실패"));
    }
  };

  const acceptServer = () => {
    if (conflict) { setSelected(conflict); setConflict(null); }
  };

  const remove = () => {
    if (!selected?.id) { setSelected(null); return; }
    if ((selected.source_type || "manual") !== "manual") {
      toast.warn("회의 auto-sync 이벤트는 회의관리에서 해당 결정/액션을 삭제해주세요.");
      return;
    }
    if (!confirm("이 이벤트를 삭제하시겠습니까?")) return;
    sf(`${API}/event/delete?id=${encodeURIComponent(selected.id)}`, { method: "POST" })
      .then(() => { setSelected(null); reload(); toast.ok("이벤트 삭제됨"); })
      .catch(e => toast.error(e.message));
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
      .then(d => { setCats(d.categories || []); setEditCats(false); toast.ok("카테고리 저장됨"); })
      .catch(e => toast.error(e.message));
  };

  const renderOccurrence = (occ) => {
    const e = occ.event;
    const srcType = e.source_type || "manual";
    // v8.7.9: meeting events use the meeting's unique palette color; manual events fall back to category color.
    const meetingColor = (e.meeting_ref && e.meeting_ref.color) || "";
    const color = safeEventColor(meetingColor || catColor(e.category));
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
    const fill = `color-mix(in srgb, ${color} ${isAction ? 8 : 14}%, transparent)`;
    const border = `1px solid ${color}`;
    const borderLeft = isAction ? `4px solid ${color}` : `3px solid ${color}`;
    return (
      <div key={`${e.id || e.title || "event"}_${occ.dayIdx}`} onClick={ev => { ev.stopPropagation(); openEdit(e); }} style={{
        minWidth: 0, maxWidth: "100%",
        fontSize: 12, lineHeight: 1.25, padding: "2px 5px", borderRadius: radius,
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
    <div className="flow-connected-page" style={{ display: "flex", height: "calc(100vh - 52px)", background: "var(--bg-primary)", color: "var(--text-primary)", position: "relative" }}>
      <PageGear title="변경점 달력 설정" canEdit={isAdmin} position="bottom-left">
        <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 10 }}>
          카테고리별 색상을 관리합니다. 회의관리의 회의 카테고리도 이 팔레트를 공유합니다.
        </div>
        <button onClick={startEditCats} style={{ padding: "8px 14px", borderRadius: 6, border: "1px solid var(--accent)", background: "transparent", color: "var(--accent)", fontSize: 14, cursor: "pointer", fontWeight: 600 }}>🎨 카테고리 팔레트 편집</button>
      </PageGear>
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
        <PageHeader
          title="변경점 관리"
          subtitle={`${view.getFullYear()}년 ${view.getMonth() + 1}월`}
          right={(
            <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap", justifyContent: "flex-end" }}>
              <Button variant="subtle" onClick={() => navMonth(-1)} style={{ padding: "4px 10px" }}>‹</Button>
              <Button variant="subtle" onClick={() => navMonth(1)} style={{ padding: "4px 10px" }}>›</Button>
              <Button variant="subtle" onClick={() => setView(new Date())}>오늘</Button>
              <Button variant="subtle" onClick={() => { reload(); }} title="회의 auto-sync 이벤트를 포함해 서버에서 다시 불러옵니다">↻ 새로고침</Button>
              <select value={meetingFilter} onChange={e => setMeetingFilter(e.target.value)}
                style={{ padding: "5px 10px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14, outline: "none" }}
                title="회의별 필터">
                <option value="all">전체 이벤트</option>
                <option value="manual">일반 이벤트만</option>
                {meetings.map(m => (
                  <option key={m.meeting_id} value={m.meeting_id}>{m.color ? "● " : "🗓 "}{m.meeting_title || m.meeting_id} ({m.count})</option>
                ))}
              </select>
              <input value={search} onChange={e => setSearch(e.target.value)} onKeyDown={e => e.key === "Enter" && runSearch()}
                placeholder="검색…"
                style={{ width: 180, padding: "6px 10px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14, outline: "none" }} />
              <Button variant="ghost" onClick={runSearch}>검색</Button>
              {searchResults && <Button variant="subtle" onClick={() => { setSearch(""); setSearchResults(null); }}>×</Button>}
            </div>
          )}
        />
        <div style={{ padding: "10px 16px 0" }}>
          <FlowiPromptBox
            defaultScope={{ kind: "meeting", date_window: ym(view), calendar_filter: meetingFilter }}
            placeholder="Flow-i 변경점 질문"
            maxRows={8}
          />
        </div>
        <div style={{ padding: "10px 16px 0" }}>
          <Card padding={10}>
            <div style={{ display: "flex", gap: 14, fontSize: 14, color: "var(--text-secondary)", flexWrap: "wrap" }}>
              <span><span style={{ display: "inline-block", width: 10, height: 10, background: "var(--info-50)", border: "1px solid var(--info)", marginRight: 4, verticalAlign: "middle" }} /> 일반</span>
              <span><span style={{ display: "inline-block", width: 10, height: 10, background: "var(--ok-50)", border: "1px solid var(--ok)", marginRight: 4, verticalAlign: "middle" }} /> 결정사항</span>
              <span><span style={{ display: "inline-block", width: 10, height: 10, background: "var(--pink-50)", border: "1px solid var(--pink)", borderLeft: "3px solid var(--pink)", marginRight: 4, verticalAlign: "middle" }} /> 📍 액션아이템</span>
            </div>
          </Card>
        </div>

        <div style={{ flex: 1, overflow: "auto", padding: 16 }}>
          {searchResults ? (
            <div>
              <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 8 }}>검색 결과: {searchResults.length}건</div>
              {searchResults.length === 0 && <EmptyState title="일치하는 이벤트 없음" hint="검색어를 바꾸거나 필터를 해제하세요." />}
              {searchResults.map(e => (
                <div key={e.id} onClick={() => openEdit(e)} style={{
                  padding: 10, marginBottom: 6, borderRadius: 6, background: "var(--bg-card)",
                  border: "1px solid var(--border)", cursor: "pointer", display: "flex", gap: 10, alignItems: "center",
                }}>
                  <span style={{ width: 8, height: 8, borderRadius: "50%", background: catColor(e.category), flexShrink: 0 }} />
                  <span style={{ fontSize: 14, fontFamily: "monospace", color: "var(--text-secondary)", minWidth: 90 }}>{e.date}{e.end_date && e.end_date !== e.date ? ` ~ ${e.end_date}` : ""}</span>
                  <span style={{ fontSize: 14, fontWeight: 600, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{e.title}</span>
                  <Pill tone="neutral">{SOURCE_LABEL[e.source_type || "manual"]}</Pill>
                  {e.category && <Chip mono={false} style={{ background: `color-mix(in srgb, ${catColor(e.category)} 14%, transparent)`, borderColor: catColor(e.category), color: catColor(e.category) }}>{e.category}</Chip>}
                  <span style={{ fontSize: 14, color: "var(--text-secondary)" }}>{e.author}</span>
                </div>
              ))}
            </div>
          ) : (
            <div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(7,minmax(0,1fr))", gap: 0, marginBottom: 0, borderLeft: "1px solid var(--border)", borderTop: "1px solid var(--border)" }}>
                {WEEKDAYS.map((w, i) => (
                  <div key={w} style={{
                    padding: "6px 8px", fontSize: 14, fontWeight: 700, textAlign: "center",
                    color: i === 0 ? "var(--danger)" : i === 6 ? "var(--info)" : "var(--ink)",
                    fontFamily: "monospace",
                    borderRight: "1px solid var(--border)",
                    borderBottom: "1px solid var(--border)",
                    background: "var(--bg-secondary)",
                  }}>{w}</div>
                ))}
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(7,minmax(0,1fr))", gridAutoRows: 112, gap: 0, borderLeft: "1px solid var(--border)", minWidth: 0 }}>
                {grid.map((d, i) => {
                  const k = ymd(d);
                  const inMonth = d.getMonth() === view.getMonth();
                  const isToday = k === today;
                  const occs = byDate[k] || [];
                  const visibleOccs = occs.slice(0, MAX_VISIBLE_DAY_EVENTS);
                  const hiddenCount = Math.max(0, occs.length - visibleOccs.length);
                  return (
                    <div key={i} onClick={() => openNew(k)} style={{
                      background: isToday ? "var(--brand-50)" : (inMonth ? "var(--bg-secondary)" : "var(--bg-primary)"),
                      borderRight: "1px solid var(--border)",
                      borderBottom: "1px solid var(--border)",
                      outline: isToday ? "2px solid var(--brand)" : "none",
                      outlineOffset: "-2px",
                      boxShadow: isToday ? "inset 0 0 0 3px var(--brand-line)" : "none",
                      borderRadius: 0, padding: 6, cursor: "pointer", overflow: "hidden",
                      display: "flex", flexDirection: "column", gap: 3,
                      opacity: inMonth ? 1 : 0.45,
                      position: "relative",
                      minWidth: 0,
                    }}>
                      <div style={{
                        display: "flex", alignItems: "center", gap: 6,
                        fontSize: isToday ? 13 : 11, fontWeight: isToday ? 800 : 500,
                        color: isToday ? "var(--brand)" : (d.getDay() === 0 ? "var(--danger)" : d.getDay() === 6 ? "var(--info)" : "var(--ink)"),
                        fontFamily: "monospace",
                        textShadow: "none",
                        whiteSpace: "nowrap",
                        flexShrink: 0,
                        minWidth: 0,
                      }}>
                        <span style={{ flexShrink: 0 }}>{d.getDate()}</span>
                        {isToday && (
                          <span title="오늘" style={{
                            background: "var(--brand)", color: "#fff",
                            padding: "1px 6px", borderRadius: 999,
                            fontSize: 11, fontWeight: 700, letterSpacing: 0,
                            lineHeight: 1.4, fontFamily: "monospace",
                            boxShadow: "0 1px 3px rgba(0,0,0,0.3)",
                            overflow: "hidden", textOverflow: "ellipsis",
                          }}>TODAY</span>
                        )}
                      </div>
                      <div style={{ display: "flex", flexDirection: "column", gap: 2, overflow: "hidden", minHeight: 0, minWidth: 0, flex: 1 }}>
                        {visibleOccs.map(renderOccurrence)}
                        {hiddenCount > 0 && (
                          <div title={`${hiddenCount}건 더 있음`} style={{
                            minWidth: 0, maxWidth: "100%",
                            fontSize: 12, lineHeight: 1.25, color: "var(--text-secondary)",
                            padding: "1px 5px", borderRadius: 3,
                            background: "var(--bg-card)", border: "1px dashed var(--border)",
                            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                            flexShrink: 0,
                          }}>+{hiddenCount}건</div>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
              <div style={{ marginTop: 10, fontSize: 14, color: "var(--text-secondary)" }}>
                {loading ? "로딩…" : `${filteredEvents.length}건`} · 셀 클릭 → 신규 등록 · 이벤트 클릭 → 편집 · 회의 이벤트는 회의관리에서만 수정 가능
              </div>
            </div>
          )}
          <MeetingAskPanel
            question={askQuestion}
            busy={askBusy}
            result={askResult}
            llmAvailable={askLlmAvailable}
            onQuestionChange={setAskQuestion}
            onAsk={() => runMeetingAsk("")}
            onChooseCandidate={(meetingId) => runMeetingAsk(meetingId)}
            onUsePrompt={setAskQuestion}
          />
        </div>
      </div>

      {selected && (
        <div style={{ width: 360, minWidth: 320, borderLeft: "1px solid var(--border)", background: "var(--bg-secondary)", display: "flex", flexDirection: "column" }}>
          <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontSize: 14, fontWeight: 700, fontFamily: "monospace", color: "var(--accent)" }}>
              {selected._new ? "+ 신규 이벤트" : `이벤트 상세 · ${SOURCE_LABEL[selected.source_type || "manual"]}`}
            </span>
            <span onClick={() => { setSelected(null); setConflict(null); }} style={{ cursor: "pointer", fontSize: 16 }}>✕</span>
          </div>
          <div style={{ flex: 1, overflow: "auto", padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
            {conflict && (
              <div style={{ padding: 10, borderRadius: 6, background: "var(--danger-50)", border: "1px solid var(--danger-line)", fontSize: 14 }}>
                ⚠ 다른 사용자가 이 이벤트를 수정했습니다.
                <div style={{ marginTop: 6, display: "flex", gap: 6 }}>
                  <button onClick={acceptServer} style={smallBtnPrimary}>최신 데이터 불러오기</button>
                  <button onClick={() => setConflict(null)} style={smallBtn}>닫기</button>
                </div>
              </div>
            )}
            {!selected._new && (selected.source_type || "manual") !== "manual" && (
              <div style={{ padding: 8, borderRadius: 5, background: "var(--info-50)", border: "1px dashed var(--info-line)", fontSize: 14, color: "var(--text-secondary)" }}>
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
                    <span style={{ fontSize: 14, color: "var(--text-secondary)" }}>열람 가능한 그룹이 없습니다.</span>
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
                              padding: "3px 10px", borderRadius: 999, fontSize: 14, cursor: "pointer",
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
                  <div style={{ fontSize: 14, color: "var(--text-secondary)", marginTop: 4 }}>
                    선택한 {(selected.group_ids || []).length}개 그룹의 멤버와 본인·관리자만 열람합니다.
                  </div>
                )}
              </Field>
            )}
            {!selected._new && (
              <div style={{ fontSize: 14, color: "var(--text-secondary)", fontFamily: "monospace", lineHeight: 1.7 }}>
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
              <button onClick={remove} style={{ padding: "8px 14px", borderRadius: 5, border: "1px solid var(--danger)", background: "transparent", color: "var(--danger)", cursor: "pointer" }}>삭제</button>}
          </div>
        </div>
      )}

      <Modal open={editCats} onClose={() => setEditCats(false)} title="🎨 카테고리 관리" width={480}>
            {!isAdmin && <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 8 }}>(관리자만 저장할 수 있습니다 — 보기 전용)</div>}
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
            {isAdmin && <button onClick={() => setDraftCats([...draftCats, { name: "신규", color: "#E25822" }])} style={{ ...smallBtn, marginTop: 4 }}>+ 카테고리 추가</button>}
            <div style={{ display: "flex", gap: 6, marginTop: 14, justifyContent: "flex-end" }}>
              <button onClick={() => setEditCats(false)} style={smallBtn}>닫기</button>
              {isAdmin && <button onClick={saveCats} style={smallBtnPrimary}>저장</button>}
            </div>
      </Modal>
    </div>
  );
}

function MeetingAskPanel({
  question,
  busy,
  result,
  llmAvailable,
  onQuestionChange,
  onAsk,
  onChooseCandidate,
  onUsePrompt,
}) {
  const disabled = busy || !(question || "").trim();
  const scopeLabel = result?.scope === "session" ? "차수 범위"
    : result?.scope === "meeting" || result?.scope === "meeting_auto" ? "회의 범위"
    : result?.scope === "clarification" ? "확인 필요"
    : "자동 범위";
  return (
    <section style={{ marginTop: 18, paddingTop: 14, borderTop: "1px solid var(--border)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10, flexWrap: "wrap" }}>
        <div style={{ fontSize: 14, fontWeight: 800, color: "var(--text-primary)" }}>{llmAvailable ? "회의·변경점 LLM 확인" : "회의·변경점 저장 데이터 확인"}</div>
        {!result && <Pill tone={llmAvailable ? "accent" : "neutral"}>{llmAvailable ? "LLM 설정됨" : "LLM 미설정"}</Pill>}
        {result?.llm && <Pill tone={result.llm.used ? "accent" : "neutral"}>{result.llm.used ? "LLM 답변" : "저장 데이터 답변"}</Pill>}
        {result?.scope && <Pill tone={result.needs_clarification ? "warn" : "info"}>{scopeLabel}</Pill>}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(240px, 1fr) auto", gap: 8, alignItems: "start" }}>
        <textarea
          value={question}
          onChange={e => onQuestionChange(e.target.value)}
          onKeyDown={e => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") onAsk(); }}
          rows={2}
          spellCheck={false}
          placeholder="회의와 변경점 관리 내용 질문"
          style={{ ...inp, minHeight: 38, resize: "vertical", fontFamily: "inherit", lineHeight: 1.45 }}
        />
        <Button variant="primary" onClick={onAsk} disabled={disabled} style={{ minHeight: 38 }}>{busy ? "확인 중…" : "확인"}</Button>
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
        {[
          ["전체 회의와 변경점에서 마감 임박 액션 알려줘", "마감 액션"],
          ["Device Change 회의 결정사항 정리해줘", "회의 후보"],
          ["회의에 등록된 이벤트와 변경점 관리 일반 이벤트를 같이 요약해줘", "이벤트 요약"],
          ["회의록 없는 회의는 어떤 정보만 있나?", "회의록 없음"],
        ].map(([prompt, label]) => (
          <button key={label} onClick={() => onUsePrompt(prompt)} style={{
            padding: "3px 8px", borderRadius: 4, border: "1px solid var(--border)",
            background: "transparent", color: "var(--text-secondary)", fontSize: 14, cursor: "pointer",
          }}>{label}</button>
        ))}
      </div>
      {result && (
        <div style={{ marginTop: 10, border: "1px solid var(--border)", borderRadius: 6, background: "var(--bg-secondary)", overflow: "hidden" }}>
          <div style={{ padding: "7px 10px", borderBottom: "1px solid var(--border)", display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
            <span style={{ fontSize: 14, fontWeight: 700, color: "var(--text-primary)" }}>{result.meeting?.title || (result.needs_clarification ? "회의 선택 필요" : "회의·변경점 답변")}</span>
            {!result.needs_clarification && (result.sources || []).slice(0, 8).map(src => (
              <Pill key={(src.meeting_id || "") + (src.session_id || src.label)} tone="neutral">
                {src.meeting_title ? `${src.meeting_title} · ` : ""}{src.label} · 아젠다 {src.agendas} · 결정 {src.decisions} · 액션 {src.action_items}
              </Pill>
            ))}
            {!result.needs_clarification && (result.calendar_events || []).length > 0 && (
              <Pill tone="info">변경점 {(result.calendar_events || []).length}건</Pill>
            )}
          </div>
          {result.needs_clarification && (
            <div style={{ padding: "10px 12px", borderBottom: "1px solid var(--border)", display: "flex", gap: 6, flexWrap: "wrap" }}>
              {(result.candidates || []).length === 0 && (
                <span style={{ fontSize: 14, color: "var(--text-secondary)" }}>선택 가능한 후보가 없습니다.</span>
              )}
              {(result.candidates || []).map(c => (
                <button key={c.meeting_id || c.id} onClick={() => onChooseCandidate(c.meeting_id || c.id)} disabled={busy} style={{
                  padding: "5px 10px", borderRadius: 5, border: "1px solid var(--accent)",
                  background: "transparent", color: "var(--accent)", fontSize: 14, fontWeight: 700, cursor: "pointer",
                }}>
                  {c.title || c.meeting_id || c.id}
                  {c.last_scheduled_at ? ` · ${String(c.last_scheduled_at).replace("T", " ").slice(0, 10)}` : ""}
                </button>
              ))}
            </div>
          )}
          <div style={{ padding: 12, whiteSpace: "pre-wrap", lineHeight: 1.6, color: "var(--text-primary)", fontSize: 14 }}>
            {result.answer || "확인된 답변이 없습니다."}
          </div>
          {result.llm?.error && (
            <div style={{ padding: "0 12px 10px", color: "var(--text-secondary)", fontSize: 14 }}>
              LLM 대체 답변: {result.llm.error}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

const navBtn = { padding: "4px 12px", borderRadius: 5, border: "1px solid var(--border)", background: "transparent", color: "var(--text-primary)", fontSize: 14, cursor: "pointer", fontFamily: "monospace" };
const inp = { width: "100%", padding: "6px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14, outline: "none" };
const smallBtn = { padding: "5px 12px", borderRadius: 4, border: "1px solid var(--border)", background: "transparent", color: "var(--text-primary)", fontSize: 14, cursor: "pointer" };
const smallBtnPrimary = { padding: "5px 12px", borderRadius: 4, border: "none", background: "var(--accent)", color: "#fff", fontSize: 14, cursor: "pointer", fontWeight: 600 };

function Field({ label, children }) {
  return (
    <div>
      <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 3, fontFamily: "monospace" }}>{label}</div>
      {children}
    </div>
  );
}
