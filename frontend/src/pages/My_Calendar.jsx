/* My_Calendar.jsx v8.6.0 — 변경점 기록 달력.
   - 월간 그리드 + 날짜 클릭 → 좌측 사이드 상세/입력.
   - 카테고리 색상 (admin 편집 가능).
   - 검색 (title/body/author/category 키워드).
   - 낙관적 잠금: 저장 시 version 충돌이면 새로고침 안내.
   - 추적 관리: 이벤트 상세에 history 표시.
*/
import { useEffect, useMemo, useState } from "react";
import { sf, postJson } from "../lib/api";
import PageGear from "../components/PageGear";

const API = "/api/calendar";
const WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"];

function pad(n) { return n < 10 ? "0" + n : "" + n; }
function ymd(d) { return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()); }
function ym(d) { return d.getFullYear() + "-" + pad(d.getMonth() + 1); }
function parseISO(s) { const [y, m, dd] = (s || "").slice(0, 10).split("-").map(Number); return new Date(y, (m || 1) - 1, dd || 1); }

function buildMonthGrid(viewDate) {
  // 6주 × 7일 = 42셀. 첫 주는 일요일 시작.
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

export default function My_Calendar({ user }) {
  const [view, setView] = useState(new Date());
  const [events, setEvents] = useState([]);
  const [cats, setCats] = useState([]);
  const [selected, setSelected] = useState(null); // {date,id?,title,body,category,version,...}
  const [search, setSearch] = useState("");
  const [searchResults, setSearchResults] = useState(null);
  const [editCats, setEditCats] = useState(false);
  const [draftCats, setDraftCats] = useState([]);
  const [conflict, setConflict] = useState(null);
  const [loading, setLoading] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);

  const monthStr = ym(view);
  const isAdmin = user?.role === "admin";

  const reload = () => {
    setLoading(true);
    sf(`${API}/events?month=${monthStr}`)
      .then(d => setEvents(d.events || []))
      .catch(() => setEvents([]))
      .finally(() => setLoading(false));
  };
  const reloadCats = () => sf(`${API}/categories`).then(d => setCats(d.categories || [])).catch(() => setCats([]));

  useEffect(() => { reload(); }, [monthStr]);
  useEffect(() => { reloadCats(); }, []);

  const grid = useMemo(() => buildMonthGrid(view), [view]);
  const eventsByDate = useMemo(() => {
    const m = {};
    for (const e of events) {
      const k = (e.date || "").slice(0, 10);
      if (!k) continue;
      (m[k] = m[k] || []).push(e);
    }
    return m;
  }, [events]);

  const catColor = (name) => (cats.find(c => c.name === name)?.color) || "#6b7280";

  const today = ymd(new Date());

  const openNew = (date) => {
    setConflict(null); setHistoryOpen(false);
    setSelected({ date, title: "", body: "", category: cats[0]?.name || "", version: 0, _new: true });
  };
  const openEdit = (e) => {
    setConflict(null); setHistoryOpen(false);
    setSelected({ ...e });
  };

  const save = () => {
    if (!selected) return;
    const t = (selected.title || "").trim();
    if (!t) { alert("제목을 입력하세요"); return; }
    if (selected._new) {
      postJson(`${API}/event`, {
        date: selected.date, title: t,
        body: selected.body || "", category: selected.category || "",
      }).then(d => { setSelected(d.event); reload(); })
        .catch(e => alert(e.message || "생성 실패"));
    } else {
      postJson(`${API}/event/update`, {
        id: selected.id, version: selected.version,
        date: selected.date, title: t,
        body: selected.body || "", category: selected.category || "",
      }).then(d => {
        if (d.conflict) {
          setConflict(d.event);
          return;
        }
        setSelected(d.event); reload();
      }).catch(e => alert(e.message || "저장 실패"));
    }
  };

  const acceptServer = () => {
    if (conflict) { setSelected(conflict); setConflict(null); }
  };

  const remove = () => {
    if (!selected?.id) { setSelected(null); return; }
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

  return (
    <div style={{ display: "flex", height: "calc(100vh - 48px)", background: "var(--bg-primary)", color: "var(--text-primary)", position: "relative" }}>
      <PageGear title="변경점 달력 설정" canEdit={isAdmin} position="bottom-left">
        <div style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 10 }}>
          카테고리별 색상을 관리합니다. 회의관리의 회의 카테고리도 이 팔레트를 공유합니다.
        </div>
        <button onClick={startEditCats} style={{ padding: "8px 14px", borderRadius: 6, border: "1px solid var(--accent)", background: "transparent", color: "var(--accent)", fontSize: 12, cursor: "pointer", fontWeight: 600 }}>🎨 카테고리 팔레트 편집</button>
      </PageGear>
      {/* Left: calendar */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
        {/* Header */}
        <div style={{ padding: "14px 20px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <span style={{ fontSize: 16, fontWeight: 700, fontFamily: "monospace", color: "var(--accent)" }}>📅 변경점 달력</span>
          <button onClick={() => navMonth(-1)} style={navBtn}>‹</button>
          <span style={{ fontSize: 15, fontWeight: 700, minWidth: 130, textAlign: "center" }}>{view.getFullYear()}년 {view.getMonth() + 1}월</span>
          <button onClick={() => navMonth(1)} style={navBtn}>›</button>
          <button onClick={() => setView(new Date())} style={{ ...navBtn, padding: "4px 10px" }}>오늘</button>
          <div style={{ flex: 1 }} />
          <input value={search} onChange={e => setSearch(e.target.value)} onKeyDown={e => e.key === "Enter" && runSearch()}
            placeholder="검색 (제목/내용/작성자)…"
            style={{ width: 220, padding: "6px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12, outline: "none" }} />
          <button onClick={runSearch} style={navBtn}>검색</button>
          {searchResults && <button onClick={() => { setSearch(""); setSearchResults(null); }} style={navBtn}>×</button>}
          <button onClick={startEditCats} style={navBtn} title="카테고리 색상 관리">🎨 카테고리</button>
        </div>

        {/* Body: calendar grid OR search results */}
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
                  <span style={{ fontSize: 11, fontFamily: "monospace", color: "var(--text-secondary)", minWidth: 90 }}>{e.date}</span>
                  <span style={{ fontSize: 13, fontWeight: 600, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{e.title}</span>
                  {e.category && <span style={{ fontSize: 10, padding: "2px 8px", borderRadius: 999, background: catColor(e.category) + "33", color: catColor(e.category) }}>{e.category}</span>}
                  <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>{e.author}</span>
                </div>
              ))}
            </div>
          ) : (
            <div>
              {/* Weekday header */}
              <div style={{ display: "grid", gridTemplateColumns: "repeat(7,1fr)", gap: 4, marginBottom: 4 }}>
                {WEEKDAYS.map((w, i) => (
                  <div key={w} style={{
                    padding: "6px 8px", fontSize: 11, fontWeight: 700, textAlign: "center",
                    color: i === 0 ? "#ef4444" : i === 6 ? "#3b82f6" : "var(--text-secondary)",
                    fontFamily: "monospace",
                  }}>{w}</div>
                ))}
              </div>
              {/* Day cells */}
              <div style={{ display: "grid", gridTemplateColumns: "repeat(7,1fr)", gridAutoRows: "minmax(96px,1fr)", gap: 4 }}>
                {grid.map((d, i) => {
                  const k = ymd(d);
                  const inMonth = d.getMonth() === view.getMonth();
                  const isToday = k === today;
                  const isSelected = selected && selected.date === k && !selected._new;
                  const evs = eventsByDate[k] || [];
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
                      {/* v8.7.5: 잘림 방지 — TODAY 라벨을 day 숫자와 한 줄로 배치. */}
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
                            lineHeight: 1.4,
                            fontFamily: "monospace",
                            boxShadow: "0 1px 3px rgba(0,0,0,0.3)",
                          }}>TODAY</span>
                        )}
                      </div>
                      <div style={{ display: "flex", flexDirection: "column", gap: 2, overflow: "hidden" }}>
                        {evs.slice(0, 4).map(e => (
                          <div key={e.id} onClick={ev => { ev.stopPropagation(); openEdit(e); }} style={{
                            fontSize: 10, padding: "2px 5px", borderRadius: 3,
                            background: catColor(e.category) + "22",
                            borderLeft: "3px solid " + catColor(e.category),
                            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                            color: "var(--text-primary)",
                          }} title={`${e.title}\n${e.body || ""}`}>{e.title}</div>
                        ))}
                        {evs.length > 4 && <div style={{ fontSize: 9, color: "var(--text-secondary)", padding: "0 4px" }}>+{evs.length - 4}건</div>}
                      </div>
                    </div>
                  );
                })}
              </div>
              <div style={{ marginTop: 10, fontSize: 10, color: "var(--text-secondary)" }}>
                {loading ? "로딩…" : `${events.length}건`} · 셀 클릭 → 신규 등록 · 이벤트 클릭 → 편집
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Right: detail */}
      {selected && (
        <div style={{ width: 360, minWidth: 320, borderLeft: "1px solid var(--border)", background: "var(--bg-secondary)", display: "flex", flexDirection: "column" }}>
          <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontSize: 13, fontWeight: 700, fontFamily: "monospace", color: "var(--accent)" }}>{selected._new ? "+ 신규 이벤트" : "이벤트 상세"}</span>
            <span onClick={() => { setSelected(null); setConflict(null); }} style={{ cursor: "pointer", fontSize: 16 }}>✕</span>
          </div>
          <div style={{ flex: 1, overflow: "auto", padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
            {conflict && (
              <div style={{ padding: 10, borderRadius: 6, background: "rgba(239,68,68,0.1)", border: "1px solid #ef4444", fontSize: 11 }}>
                ⚠ 다른 사용자가 이 이벤트를 수정했습니다. 새로고침하지 않고 저장하면 변경분이 덮어쓰일 수 있습니다.
                <div style={{ marginTop: 6, display: "flex", gap: 6 }}>
                  <button onClick={acceptServer} style={smallBtnPrimary}>최신 데이터 불러오기</button>
                  <button onClick={() => setConflict(null)} style={smallBtn}>닫기</button>
                </div>
              </div>
            )}
            <Field label="날짜">
              <input type="date" value={(selected.date || "").slice(0, 10)} onChange={e => setSelected({ ...selected, date: e.target.value })} style={inp} />
            </Field>
            <Field label="제목">
              <input value={selected.title || ""} onChange={e => setSelected({ ...selected, title: e.target.value })} placeholder="이벤트 제목"
                style={inp} />
            </Field>
            <Field label="카테고리">
              <select value={selected.category || ""} onChange={e => setSelected({ ...selected, category: e.target.value })} style={inp}>
                <option value="">(없음)</option>
                {cats.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
              </select>
            </Field>
            <Field label="내용">
              <textarea value={selected.body || ""} onChange={e => setSelected({ ...selected, body: e.target.value })} rows={8}
                placeholder="변경 내용·배경·참석자 등"
                style={{ ...inp, resize: "vertical", fontFamily: "inherit" }} />
            </Field>
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
            <button onClick={save} style={{ flex: 1, padding: "8px 0", borderRadius: 5, border: "none", background: "var(--accent)", color: "#fff", fontWeight: 600, cursor: "pointer" }}>{selected._new ? "등록" : "저장"}</button>
            {!selected._new && <button onClick={remove} style={{ padding: "8px 14px", borderRadius: 5, border: "1px solid #ef4444", background: "transparent", color: "#ef4444", cursor: "pointer" }}>삭제</button>}
          </div>
        </div>
      )}

      {/* Categories editor modal */}
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
