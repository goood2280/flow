import { useState, useEffect, useRef, useCallback } from "react";
import Loading from "../components/Loading";
import PageGear from "../components/PageGear";
import { authSrc, sf as apiSf } from "../lib/api";
const API = "/api/tracker";
// v8.8.3: 인증 헤더 자동 주입을 위해 lib/api.sf 로 교체. legacy 시그니처 유지.
const sf = (url, o) => apiSf(url, o);

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
  s.textContent=`
.desc-editor img,.desc-view img{max-width:300px!important;border-radius:6px;cursor:pointer;transition:max-width 0.2s;display:block;margin:4px 0}
.desc-editor img:hover{outline:2px solid #f97316;outline-offset:2px}
.desc-view img:hover{transform:scale(2);transform-origin:top left;z-index:100;position:relative;box-shadow:0 8px 30px rgba(0,0,0,0.6);transition:transform 0.2s}
.trk-img-thumb:hover{transform:scale(2.5);transform-origin:top left;z-index:100;position:relative;box-shadow:0 8px 30px rgba(0,0,0,0.6)}
.trk-img-thumb{transition:transform 0.2s;cursor:zoom-in}
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
        color: "var(--text-primary)", fontSize: 13, outline: "none", lineHeight: 1.7,
        marginBottom: 8, overflowY: "auto", maxHeight: 400, whiteSpace: "pre-wrap",
        wordBreak: "break-word",
      }} />
  );
}

/* ─── Lot/Wafer Editable Table ─── */
function LotTable({ lots, setLots, readOnly }) {
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
        return { root_lot_id: (parts[0] || "").trim(), wafer_id: (parts[1] || "").trim(), comment: (parts[2] || "").trim() };
      }).filter(r => r.root_lot_id || r.wafer_id);
      setLots(prev => [...prev, ...newRows]);
    }
  };

  const updateCell = (idx, field, value) => {
    setLots(prev => prev.map((r, i) => i === idx ? { ...r, [field]: value } : r));
  };

  const removeRow = (idx) => setLots(prev => prev.filter((_, i) => i !== idx));
  const addRow = () => setLots(prev => [...prev, { root_lot_id: "", wafer_id: "", comment: "" }]);

  const cellStyle = {
    padding: "5px 8px", borderBottom: "1px solid var(--border)", fontSize: 12,
  };
  const inputStyle = {
    width: "100%", padding: "4px 6px", border: "1px solid var(--border)", borderRadius: 3,
    background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 11, outline: "none",
  };

  return (
    <div onPaste={!readOnly ? handlePaste : undefined}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
        <span style={{ fontSize: 12, fontWeight: 600 }}>Lot / Wafer ({lots.length})</span>
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          {!readOnly && <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>Excel 붙여넣기 지원 (Ctrl+V)</span>}
          {!readOnly && <button onClick={addRow} style={{ padding: "3px 10px", borderRadius: 4, border: "1px solid var(--border)", background: "transparent", color: "var(--text-primary)", fontSize: 10, cursor: "pointer" }}>+ 행 추가</button>}
        </div>
      </div>
      <div style={{ maxHeight: 240, overflow: "auto", border: "1px solid var(--border)", borderRadius: 6 }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead><tr>
            {["Root Lot ID", "Wafer ID", "코멘트"].map(h => (
              <th key={h} style={{ textAlign: "left", padding: "6px 8px", background: "var(--bg-tertiary)", borderBottom: "1px solid var(--border)", fontSize: 10, color: "var(--text-secondary)", fontWeight: 600 }}>{h}</th>
            ))}
            {!readOnly && <th style={{ width: 30, background: "var(--bg-tertiary)", borderBottom: "1px solid var(--border)" }} />}
            {readOnly && <>
              <th style={{ textAlign: "left", padding: "6px 8px", background: "var(--bg-tertiary)", borderBottom: "1px solid var(--border)", fontSize: 10, color: "var(--text-secondary)" }}>작성자</th>
              <th style={{ textAlign: "left", padding: "6px 8px", background: "var(--bg-tertiary)", borderBottom: "1px solid var(--border)", fontSize: 10, color: "var(--text-secondary)" }}>날짜</th>
            </>}
          </tr></thead>
          <tbody>
            {lots.map((l, i) => (
              <tr key={i}>
                <td style={cellStyle}>{readOnly ? (l.root_lot_id || l.lot_id) : <input value={l.root_lot_id || ""} onChange={e => updateCell(i, "root_lot_id", e.target.value)} style={inputStyle} />}</td>
                <td style={cellStyle}>{readOnly ? l.wafer_id : <input value={l.wafer_id || ""} onChange={e => updateCell(i, "wafer_id", e.target.value)} style={inputStyle} />}</td>
                <td style={cellStyle}>{readOnly ? l.comment : <input value={l.comment || ""} onChange={e => updateCell(i, "comment", e.target.value)} style={inputStyle} />}</td>
                {!readOnly && <td style={{ ...cellStyle, textAlign: "center" }}>
                  <span onClick={() => removeRow(i)} style={{ cursor: "pointer", color: "#ef4444", fontSize: 12 }}>×</span>
                </td>}
                {readOnly && <>
                  <td style={{ ...cellStyle, color: "var(--text-secondary)", fontSize: 11 }}>{l.username}</td>
                  <td style={{ ...cellStyle, color: "var(--text-secondary)", fontSize: 10 }}>{l.added?.slice(0, 10)}</td>
                </>}
              </tr>
            ))}
            {lots.length === 0 && <tr><td colSpan={readOnly ? 5 : 4} style={{ padding: 16, textAlign: "center", color: "var(--text-secondary)", fontSize: 11 }}>
              {readOnly ? "Lot/Wafer 데이터 없음" : "Excel 에서 붙여넣기 (LOT_ID \\t WAFER_ID \\t COMMENT) 또는 + 행 추가"}
            </td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ─── Issue Form ─── */
function IssueForm({ onSubmit, onClose, user }) {
  const [title, setTitle] = useState(""); const [desc, setDesc] = useState(""); const [priority, setPriority] = useState("normal");
  const [lots, setLots] = useState([]); const [links, setLinks] = useState([""]);
  const [category, setCategory] = useState(""); const [cats, setCats] = useState([]);
  // v8.5.0: group visibility
  const [myGroups, setMyGroups] = useState([]); const [groupIds, setGroupIds] = useState([]);
  useEffect(() => { sf(API + "/categories").then(d => setCats((d.categories || []).map(c => typeof c === "string" ? { name: c, color: "#64748b" } : c))).catch(() => { }); }, []);
  useEffect(() => { sf("/api/groups/list").then(d => setMyGroups(d.groups || [])).catch(() => setMyGroups([])); }, []);
  const S = { width: "100%", padding: "8px 12px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 13, outline: "none" };
  return (
    <div style={{ background: "var(--bg-secondary)", borderRadius: 10, border: "1px solid var(--border)", padding: 20, marginBottom: 20 }}>
      <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 12 }}>새 이슈</div>
      <input value={title} onChange={e => setTitle(e.target.value)} placeholder="제목" style={{ ...S, marginBottom: 8 }} />
      <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 4 }}>설명 (Ctrl+V 로 이미지 붙여넣기)</div>
      <DescEditor value={desc} onChange={setDesc} placeholder="설명 입력... Ctrl+V 로 이미지 붙여넣기" />
      <div style={{ display: "flex", gap: 8, marginBottom: 12, alignItems: "center" }}>
        <select value={priority} onChange={e => setPriority(e.target.value)} style={{ ...S, width: "auto" }}>
          <option value="low">낮음</option><option value="normal">보통</option><option value="high">높음</option><option value="critical">긴급</option>
        </select>
        <select value={category} onChange={e => setCategory(e.target.value)} style={{ ...S, width: "auto" }}>
          <option value="">-- 카테고리 --</option>
          {cats.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
        </select>
      </div>
      {/* Related Links */}
      <div style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 4, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span>관련 링크 ({links.filter(l => l.trim()).length})</span>
          <span onClick={() => setLinks([...links, ""])} style={{ cursor: "pointer", color: "var(--accent)", fontSize: 10, fontWeight: 600 }}>+ 추가</span>
        </div>
        {links.map((lnk, i) => (
          <div key={i} style={{ display: "flex", gap: 6, marginBottom: 4 }}>
            <input value={lnk} onChange={e => { const nl = [...links]; nl[i] = e.target.value; setLinks(nl); }} placeholder="https://... 또는 설명" style={{ ...S, fontSize: 12 }} />
            {links.length > 1 && <span onClick={() => setLinks(links.filter((_, j) => j !== i))} style={{ cursor: "pointer", color: "#ef4444", fontSize: 14, padding: "6px 4px", flexShrink: 0 }}>✕</span>}
          </div>
        ))}
      </div>
      <div style={{ marginBottom: 12 }}>
        <LotTable lots={lots} setLots={setLots} readOnly={false} />
      </div>
      {/* v8.5.0: 그룹 가시성 */}
      <div style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 4 }}>그룹 가시성 (비어있으면 공개)</div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {myGroups.length === 0 && <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>가입된 그룹 없음</span>}
          {myGroups.map(g => {
            const on = groupIds.includes(g.id);
            return <label key={g.id} style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11, padding: "3px 8px", borderRadius: 999, border: "1px solid " + (on ? "var(--accent)" : "var(--border)"), background: on ? "var(--accent)22" : "transparent", cursor: "pointer" }}>
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
        <button onClick={() => { if (!title.trim()) return; onSubmit({ title, description: desc, priority, category, images: [], lots, links: links.filter(l => l.trim()), group_ids: groupIds }); }}
          style={{ padding: "8px 20px", borderRadius: 6, border: "none", background: "var(--accent)", color: "#fff", fontWeight: 600, cursor: "pointer" }}>생성</button>
        <button onClick={onClose} style={{ padding: "8px 16px", borderRadius: 6, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", cursor: "pointer" }}>취소</button>
      </div>
    </div>);
}

/* ─── Gantt Chart ─── */
function GanttChart({ issues, onIssueClick }) {
  if (!issues.length) return <div style={{ padding: 40, textAlign: "center", color: "var(--text-secondary)" }}>이슈 없음</div>;
  // v8.1.5: look up category color from stored list; fall back to hash for orphan categories
  const [cats, setCats] = useState([]);
  useEffect(() => { sf(API + "/categories").then(d => setCats((d.categories || []).map(c => typeof c === "string" ? { name: c, color: "" } : c))).catch(() => { }); }, []);
  const hashColor = (name) => { let h = 0; for (let i = 0; i < name.length; i++) h = ((h << 5) - h + name.charCodeAt(i)) | 0; return `hsl(${Math.abs(h) % 360}, 58%, 58%)`; };
  const catColor = (name) => { if (!name) return "#64748b"; const c = cats.find(x => x.name === name); return (c && c.color) || hashColor(name); };
  const now = new Date(); const [month, setMonth] = useState(now.getMonth()); const [year, setYear] = useState(now.getFullYear());
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const days = Array.from({ length: daysInMonth }, (_, i) => i + 1);
  const mStart = new Date(year, month, 1); const mEnd = new Date(year, month + 1, 0, 23, 59);
  const filtered = issues.filter(iss => { const c = new Date(iss.created || iss.timestamp); const e = iss.closed_at ? new Date(iss.closed_at) : now; return c <= mEnd && e >= mStart; });
  const prioColor = { critical: "#ef4444", high: "#f97316", normal: "#3b82f6", low: "#94a3b8" };
  const prevM = () => { if (month === 0) { setMonth(11); setYear(y => y - 1); } else setMonth(m => m - 1); };
  const nextM = () => { if (month === 11) { setMonth(0); setYear(y => y + 1); } else setMonth(m => m + 1); };
  return (<div>
    <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12 }}>
      <button onClick={prevM} style={{ background: "none", border: "1px solid var(--border)", borderRadius: 4, color: "var(--text-primary)", cursor: "pointer", padding: "2px 8px" }}>◀</button>
      <span style={{ fontSize: 14, fontWeight: 700, minWidth: 120, textAlign: "center" }}>{year}.{String(month + 1).padStart(2, "0")}</span>
      <button onClick={nextM} style={{ background: "none", border: "1px solid var(--border)", borderRadius: 4, color: "var(--text-primary)", cursor: "pointer", padding: "2px 8px" }}>▶</button>
    </div>
    <div style={{ overflow: "auto" }}>
      <table style={{ borderCollapse: "collapse", fontSize: 10, minWidth: "100%" }}>
        <thead><tr>
          <th style={{ textAlign: "left", padding: "6px 8px", borderBottom: "2px solid var(--border)", background: "var(--bg-tertiary)", position: "sticky", left: 0, zIndex: 2, minWidth: 140 }}>이슈</th>
          {days.map(d => <th key={d} style={{ padding: "4px 2px", borderBottom: "2px solid var(--border)", background: "var(--bg-tertiary)", minWidth: 20, textAlign: "center", color: new Date(year, month, d).getDay() === 0 ? "#ef4444" : "var(--text-secondary)" }}>{d}</th>)}
        </tr></thead>
        <tbody>{filtered.map(iss => {
          const created = new Date(iss.created || iss.timestamp); const ended = iss.closed_at ? new Date(iss.closed_at) : now;
          return (<tr key={iss.id}>
            <td style={{ padding: "4px 8px", borderBottom: "1px solid var(--border)", background: "var(--bg-secondary)", position: "sticky", left: 0, zIndex: 1, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: 160 }} title={iss.title}><span onClick={() => onIssueClick && onIssueClick(iss.id)} style={{ fontWeight: 600, cursor: "pointer", color: "var(--accent)", textDecoration: "none" }} onMouseEnter={e=>e.currentTarget.style.textDecoration="underline"} onMouseLeave={e=>e.currentTarget.style.textDecoration="none"}>{iss.category ? `[${iss.category}] ` : ""}{iss.title}</span></td>
            {days.map(d => {
              const day = new Date(year, month, d);
              const inRange = day >= new Date(created.getFullYear(), created.getMonth(), created.getDate()) && day <= new Date(ended.getFullYear(), ended.getMonth(), ended.getDate());
              const isStart = day.toDateString() === created.toDateString(); const isEnd = iss.closed_at && day.toDateString() === ended.toDateString();
              return <td key={d} style={{ borderBottom: "1px solid var(--border)", borderRight: "1px solid var(--border)", padding: 0 }}>
                {inRange && <div style={{ height: 14, background: iss.category ? catColor(iss.category) : (prioColor[iss.priority] || "#3b82f6"), borderRadius: isStart ? "7px 0 0 7px" : isEnd ? "0 7px 7px 0" : "0", opacity: iss.status === "closed" ? 0.5 : 0.85 }} title={`${iss.title} (${iss.status})`} />}
              </td>;
            })}
          </tr>);
        })}</tbody>
      </table>
    </div>
  </div>);
}

/* ─── Main Tracker ─── */
export default function My_Tracker({ user }) {
  const [issues, setIssues] = useState([]); const [selected, setSelected] = useState(null); const [creating, setCreating] = useState(false);
  const [filter, setFilter] = useState(""); const [comment, setComment] = useState(""); const [search, setSearch] = useState("");
  const [viewTab, setViewTab] = useState("list");
  const [editMode, setEditMode] = useState(false); const [editTitle, setEditTitle] = useState(""); const [editDesc, setEditDesc] = useState(""); const [editPrio, setEditPrio] = useState("normal");
  const isAdmin = user?.role === "admin";
  const statusColor = { in_progress: "#f97316", closed: "#22c55e" };
  const prioColor = { critical: "#ef4444", high: "#f97316", normal: "#3b82f6", low: "#94a3b8" };
  // v8.1.5: look up category color from stored list; fall back to hash for orphans
  const [cats, setCats] = useState([]);
  useEffect(() => { sf(API + "/categories").then(d => setCats((d.categories || []).map(c => typeof c === "string" ? { name: c, color: "" } : c))).catch(() => { }); }, []);
  const hashColor = (name) => { let h = 0; for (let i = 0; i < name.length; i++) h = ((h << 5) - h + name.charCodeAt(i)) | 0; return `hsl(${Math.abs(h) % 360}, 58%, 58%)`; };
  const catColor = (name) => { if (!name) return "#64748b"; const c = cats.find(x => x.name === name); return (c && c.color) || hashColor(name); };

  const load = () => sf(API + "/issues").then(d => setIssues(d.issues || []));
  useEffect(() => { load(); }, []);
  const loadDetail = (id) => { sf(API + "/issue?issue_id=" + id).then(d => { setSelected(d.issue || d); setEditMode(false); }); };
  const create = (data) => {
    const body = { ...data, username: user?.username || "anonymous" };
    sf(API + "/create", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then(d => {
      const iid = d.id || d.issue_id;
      if (data.lots?.length) { sf(API + "/lots/bulk", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ issue_id: iid, rows: data.lots, username: user?.username || "" }) }); }
      setCreating(false); load();
    });
  };
  const updateStatus = (id, status) => { sf(API + "/update", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ issue_id: id, status }) }).then(() => { loadDetail(id); load(); }); };
  const addComment = () => { if (!comment.trim() || !selected) return; sf(API + "/comment", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ issue_id: selected.id, username: user?.username || "", text: comment }) }).then(() => { setComment(""); loadDetail(selected.id); }); };
  const deleteIssue = () => { if (!confirm("이 이슈를 삭제할까요?")) return; sf(API + "/delete?issue_id=" + selected.id, { method: "POST" }).then(() => { setSelected(null); load(); }); };
  const canEdit = selected && (selected.username === user?.username || isAdmin);
  const startEdit = () => { if (!canEdit) return; setEditMode(true); setEditTitle(selected.title); setEditDesc(selected.description_html || selected.description || ""); setEditPrio(selected.priority || "normal"); };
  const saveEdit = () => {
    if (!editTitle.trim()) return;
    sf(API + "/update", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ issue_id: selected.id, title: editTitle, description: editDesc, priority: editPrio }) })
      .then(() => { setEditMode(false); loadDetail(selected.id); load(); }).catch(e => alert(e.message));
  };

  const filteredIssues = issues.filter(iss => {
    if (filter && iss.status !== filter) return false;
    if (search) { const s = search.toLowerCase(); return (iss.title || "").toLowerCase().includes(s) || (iss.username || "").toLowerCase().includes(s) || (iss.category || "").toLowerCase().includes(s); }
    return true;
  });

  return (
    <div style={{ display: "flex", height: "calc(100vh - 48px)", background: "var(--bg-primary)", color: "var(--text-primary)" }}>
      {/* Sidebar */}
      <div style={{ width: 400, minWidth: 350, borderRight: "1px solid var(--border)", display: "flex", flexDirection: "column", background: "var(--bg-secondary)" }}>
        <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span style={{ fontSize: 14, fontWeight: 700, fontFamily: "monospace", color: "var(--accent)" }}>{">"} 이슈 추적</span>
          <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
            {["list", "gantt"].map(t => <span key={t} onClick={() => setViewTab(t)} style={{ padding: "3px 8px", borderRadius: 4, fontSize: 10, cursor: "pointer", fontWeight: viewTab === t ? 600 : 400, background: viewTab === t ? "var(--accent-glow)" : "transparent", color: viewTab === t ? "var(--accent)" : "var(--text-secondary)" }}>{t === "list" ? "목록" : "간트"}</span>)}
            <button onClick={() => setCreating(!creating)} style={{ padding: "4px 12px", borderRadius: 5, border: "none", background: "var(--accent)", color: "#fff", fontSize: 11, fontWeight: 600, cursor: "pointer", marginLeft: 4 }}>+ 새 이슈</button>
            <PageGear title="이슈 추적 설정" canEdit={isAdmin} position="bottom-left">
              <TrackerSettings isAdmin={isAdmin} />
            </PageGear>
          </div>
        </div>
        <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>
          <input value={search} onChange={e => setSearch(e.target.value)} placeholder="제목 또는 작성자 검색..."
            style={{ width: "100%", padding: "6px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12, outline: "none" }} />
        </div>
        <div style={{ display: "flex", gap: 4, padding: "8px 12px", flexWrap: "wrap" }}>
          {["", "in_progress", "closed"].map(s => {
            const label = s === "" ? "전체" : s === "in_progress" ? "진행중" : "완료";
            return <span key={s} onClick={() => setFilter(s)} style={{ padding: "3px 8px", borderRadius: 4, fontSize: 10, cursor: "pointer", fontWeight: filter === s ? 600 : 400, background: filter === s ? "var(--accent-glow)" : "transparent", color: filter === s ? "var(--accent)" : "var(--text-secondary)" }}>{label}</span>;
          })}
          <span style={{ fontSize: 10, color: "var(--text-secondary)", marginLeft: "auto" }}>{filteredIssues.length}</span>
        </div>
        <div style={{ flex: 1, overflow: "auto" }}>
          {filteredIssues.map(iss => (
            <div key={iss.id} onClick={() => { loadDetail(iss.id); setViewTab("list"); }} style={{ padding: "10px 16px", borderBottom: "1px solid var(--border)", cursor: "pointer", background: selected?.id === iss.id ? "var(--bg-hover)" : "transparent" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 3 }}>
                <span title={iss.category ? `카테고리: ${iss.category}` : `상태: ${iss.status}`} style={{ width: 9, height: 9, borderRadius: "50%", background: iss.category ? catColor(iss.category) : (statusColor[iss.status] || "#666"), flexShrink: 0, border: iss.category ? "1px solid rgba(255,255,255,0.2)" : "none" }} />
                {iss.category && <span style={{ fontSize: 9, padding: "1px 5px", borderRadius: 3, background: catColor(iss.category) + "22", color: catColor(iss.category), fontWeight: 700, flexShrink: 0, fontFamily: "monospace", letterSpacing: "0.02em" }}>{iss.category}</span>}
                <span style={{ fontSize: 13, fontWeight: 600, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{iss.title}</span>
                <span style={{ fontSize: 9, padding: "1px 5px", borderRadius: 3, background: (prioColor[iss.priority] || "#666") + "22", color: prioColor[iss.priority] || "#666", fontWeight: 700 }}>{({low:"낮음",normal:"보통",high:"높음",critical:"긴급"}[iss.priority]) || iss.priority}</span>
              </div>
              <div style={{ fontSize: 10, color: "var(--text-secondary)", display: "flex", gap: 8 }}>
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
        {creating && <IssueForm onSubmit={create} onClose={() => setCreating(false)} user={user} />}
        {viewTab === "gantt" ? <GanttChart issues={issues} onIssueClick={(id) => { loadDetail(id); setViewTab("list"); }} />
          : selected ? (<div>
            {/* Header */}
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 16, flexWrap: "wrap" }}>
              {editMode ? <input value={editTitle} onChange={e => setEditTitle(e.target.value)} style={{ fontSize: 18, fontWeight: 700, padding: "4px 8px", borderRadius: 6, border: "1px solid var(--accent)", background: "var(--bg-primary)", color: "var(--text-primary)", outline: "none", flex: 1 }} />
                : <span style={{ fontSize: 18, fontWeight: 700 }}>{selected.title}</span>}
              {canEdit && !editMode && <span onClick={startEdit} style={{ cursor: "pointer", fontSize: 12, color: "var(--accent)", padding: "4px 8px", borderRadius: 4, background: "var(--accent-glow)" }}>수정</span>}
              {editMode && <span onClick={saveEdit} style={{ cursor: "pointer", fontSize: 12, color: "#22c55e", padding: "4px 8px", borderRadius: 4, background: "#22c55e22", fontWeight: 600 }}>저장</span>}
              {editMode && <span onClick={() => setEditMode(false)} style={{ cursor: "pointer", fontSize: 12, color: "var(--text-secondary)", padding: "4px 8px", borderRadius: 4, background: "var(--bg-hover)" }}>취소</span>}
              {canEdit && <span onClick={deleteIssue} style={{ cursor: "pointer", fontSize: 12, color: "#ef4444", padding: "4px 8px", borderRadius: 4, background: "#ef444411" }}>삭제</span>}
              <select value={selected.status} onChange={e => updateStatus(selected.id, e.target.value)} style={{ padding: "4px 8px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-card)", color: "var(--text-primary)", fontSize: 11, marginLeft: "auto" }}>
                {[["in_progress","진행중"], ["closed","완료"]].map(([v,lbl]) => <option key={v} value={v}>{lbl}</option>)}
              </select>
            </div>

            {/* Meta */}
            <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 12, display: "flex", gap: 12 }}>
              <span>작성자 <strong>{selected.username}</strong></span>
              <span>{(selected.created || selected.timestamp || "")?.slice(0, 16)}</span>
              {selected.closed_at && <span>완료: {selected.closed_at?.slice(0, 16)}</span>}
            </div>

            {/* Description */}
            {editMode ? (
              <div style={{ marginBottom: 12 }}>
                <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 4 }}>설명 (Ctrl+V 로 이미지 붙여넣기)</div>
                <DescEditor value={editDesc} onChange={setEditDesc} placeholder="설명 수정..." />
              </div>
            ) : (selected.description_html || selected.description) && (<>
              <style>{`.desc-view img{max-width:400px!important;border-radius:6px;display:block;margin:4px 0;}`}</style>
              <div className="desc-view" style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 16, lineHeight: 1.7, background: "var(--bg-card)", padding: 12, borderRadius: 8, border: "1px solid var(--border)", wordBreak: "break-word" }}
                dangerouslySetInnerHTML={{ __html: withTrackerImageAuth(selected.description_html || selected.description) }} /></>

            )}

            {/* Priority (edit) */}
            {editMode && <div style={{ marginBottom: 12 }}>
              <span style={{ fontSize: 11, color: "var(--text-secondary)", marginRight: 8 }}>우선순위:</span>
              <select value={editPrio} onChange={e => setEditPrio(e.target.value)} style={{ padding: "4px 8px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-card)", color: "var(--text-primary)", fontSize: 11 }}>
                <option value="low">낮음</option><option value="normal">보통</option><option value="high">높음</option><option value="critical">긴급</option></select>
            </div>}

            {/* Standalone images (legacy) */}
            {selected.images?.length > 0 && <div style={{ display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap" }}>
              {selected.images.map((img, i) => <img key={i} className="trk-img-thumb" src={authSrc("/api/tracker/image?name=" + img)} style={{ maxWidth: 150, maxHeight: 120, borderRadius: 8, border: "1px solid var(--border)", objectFit: "cover" }} />)}
            </div>}

            {/* Related Links */}
            {selected.links?.length > 0 && <div style={{ marginBottom: 16 }}>
              <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6, color: "var(--text-secondary)" }}>관련 링크</div>
              {selected.links.map((lnk, i) => (
                <div key={i} style={{ marginBottom: 4 }}>
                  {lnk.startsWith("http") ? <a href={lnk} target="_blank" rel="noopener noreferrer" style={{ color: "#3b82f6", fontSize: 12, textDecoration: "none", wordBreak: "break-all" }}>{lnk}</a>
                    : <span style={{ fontSize: 12, color: "var(--text-primary)" }}>{lnk}</span>}
                </div>
              ))}
            </div>}
            {/* Lots table */}
            {selected.lots?.length > 0 && <div style={{ marginBottom: 16 }}>
              <LotTable lots={selected.lots} setLots={() => { }} readOnly={true} />
            </div>}

            {/* Comments */}
            <div style={{ marginTop: 16 }}>
              <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>댓글 ({selected.comments?.length || 0})</div>
              {selected.comments?.map((c, i) => (
                <div key={i} style={{ padding: "10px 12px", marginBottom: 8, background: "var(--bg-card)", borderRadius: 8, border: "1px solid var(--border)" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4, alignItems: "center" }}>
                    <span style={{ fontSize: 12, fontWeight: 600 }}>{c.username}</span>
                    <span title={c.timestamp || ""} style={{
                      fontSize: 10, padding: "2px 8px", borderRadius: 999,
                      background: "var(--bg-primary)", color: "var(--text-primary)",
                      border: "1px solid var(--border)", fontFamily: "monospace",
                    }}>🕐 {(c.timestamp || "").replace("T", " ").slice(0, 16) || "시간 없음"}</span>
                  </div>
                  <div style={{ fontSize: 13, lineHeight: 1.6 }}>{c.text}</div>
                  {(c.lot_id || c.wafer_id) && <div style={{ fontSize: 10, color: "var(--text-secondary)", marginTop: 4 }}>{c.lot_id} / {c.wafer_id}</div>}
                </div>))}
              <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                <input value={comment} onChange={e => setComment(e.target.value)} placeholder="댓글 입력..."
                  style={{ flex: 1, padding: "8px 12px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 13, outline: "none" }}
                  onKeyDown={e => e.key === "Enter" && addComment()} />
                <button onClick={addComment} style={{ padding: "8px 16px", borderRadius: 6, border: "none", background: "var(--accent)", color: "#fff", fontSize: 12, fontWeight: 600, cursor: "pointer" }}>전송</button>
              </div>
            </div>
          </div>) : <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "var(--text-secondary)", fontSize: 13 }}>이슈를 선택하거나 새 이슈를 생성하세요</div>}
      </div>
    </div>);
}

/* ═══ v8.5.2 Tracker Settings (PageGear 내부) ═══ */
function TrackerSettings({ isAdmin }) {
  const [cats, setCats] = useState([]);
  const [name, setName] = useState("");
  const [color, setColor] = useState("#3b82f6");
  const [msg, setMsg] = useState("");
  const load = () => sf(API + "/categories").then(d => setCats((d.categories || []).map(c => typeof c === "string" ? { name: c, color: "#64748b" } : c)));
  useEffect(load, []);
  const save = (next) => sf(API + "/categories/save", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(next),
  }).then(() => { setMsg("저장 완료"); load(); }).catch(e => setMsg(e.message));
  const add = () => { if (!name.trim()) return; const next = [...cats, { name: name.trim(), color }]; setName(""); save(next); };
  const remove = (n) => save(cats.filter(c => c.name !== n));
  const updColor = (n, c) => save(cats.map(x => x.name === n ? { ...x, color: c } : x));
  return (
    <div>
      <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>카테고리 관리</div>
      {!isAdmin && <div style={{ fontSize: 10, color: "var(--text-secondary)", marginBottom: 8 }}>편집은 관리자만 가능합니다.</div>}
      <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 10 }}>
        {cats.map(c => (
          <div key={c.name} style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <input type="color" value={c.color || "#64748b"} disabled={!isAdmin}
              onChange={e => updColor(c.name, e.target.value)}
              style={{ width: 28, height: 26, border: "1px solid var(--border)", borderRadius: 4, background: "transparent", cursor: isAdmin ? "pointer" : "default" }} />
            <span style={{ flex: 1, fontSize: 12 }}>{c.name}</span>
            {isAdmin && <span onClick={() => remove(c.name)} style={{ cursor: "pointer", color: "#ef4444", fontSize: 11 }}>삭제</span>}
          </div>
        ))}
      </div>
      {isAdmin && (
        <div style={{ display: "flex", gap: 6 }}>
          <input type="color" value={color} onChange={e => setColor(e.target.value)}
            style={{ width: 28, height: 30, border: "1px solid var(--border)", borderRadius: 4, background: "transparent" }} />
          <input value={name} onChange={e => setName(e.target.value)} placeholder="새 카테고리 이름"
            style={{ flex: 1, padding: "6px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12 }} />
          <button onClick={add} style={{ padding: "6px 12px", borderRadius: 4, border: "none", background: "var(--accent)", color: "#fff", fontSize: 11, cursor: "pointer" }}>추가</button>
        </div>
      )}
      {msg && <div style={{ marginTop: 8, fontSize: 11, color: msg === "저장 완료" ? "#22c55e" : "#ef4444" }}>{msg}</div>}
      <div style={{ marginTop: 16, padding: 10, background: "var(--bg-primary)", borderRadius: 6, fontSize: 10, color: "var(--text-secondary)", lineHeight: 1.6 }}>
        • 카테고리 색상은 이슈 리스트/간트 차트 bar/카테고리 chip 에 반영됩니다.<br/>
        • 일반 유저는 현재 카테고리 목록만 조회 가능.
      </div>
    </div>
  );
}
