/* My_Inform.jsx v8.5.1 — wafer별 인폼 스레드. */
import { useEffect, useState, useMemo } from "react";
import { sf } from "../lib/api";

const API = "/api/informs";
const MODULES = ["GATE", "STI", "PC", "MOL", "BEOL", "ET", "EDS", "S-D Epi", "Spacer", "Well", "기타"];
const REASONS = ["재측정", "장비 이상", "공정 OOS", "혐의 확인", "레시피 변경", "외관 결함", "기타"];

/* 스레드 트리 렌더 */
function ThreadNode({ node, childrenByParent, onReply, onDelete, user, depth = 0 }) {
  const [replyOpen, setReplyOpen] = useState(false);
  const [reply, setReply] = useState({ module: "", reason: "", text: "" });
  const canDel = user && (user.username === node.author || user.role === "admin");
  const kids = childrenByParent[node.id] || [];
  const indent = Math.min(depth, 5) * 28;
  return (
    <div style={{ marginLeft: indent }}>
      <div style={{
        background: depth === 0 ? "var(--bg-secondary)" : "var(--bg-card)",
        border: "1px solid var(--border)", borderRadius: 8, padding: 10, marginBottom: 6,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4, flexWrap: "wrap" }}>
          {node.module && <span style={{ fontSize: 10, padding: "2px 8px", borderRadius: 999, background: "var(--accent)22", color: "var(--accent)", fontWeight: 700 }}>{node.module}</span>}
          {node.reason && <span style={{ fontSize: 10, padding: "2px 8px", borderRadius: 999, background: "var(--bg-hover)", color: "var(--text-secondary)" }}>{node.reason}</span>}
          <span style={{ fontSize: 11, fontWeight: 600 }}>{node.author}</span>
          <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>{(node.created_at || "").replace("T", " ")}</span>
          <div style={{ flex: 1 }} />
          <span onClick={() => setReplyOpen(!replyOpen)} style={{ fontSize: 10, color: "var(--accent)", cursor: "pointer" }}>
            {replyOpen ? "닫기" : (depth === 0 ? "재인폼/답글" : "답글")}
          </span>
          {canDel && kids.length === 0 && <span onClick={() => onDelete(node.id)} style={{ fontSize: 10, color: "#ef4444", cursor: "pointer" }}>삭제</span>}
        </div>
        <div style={{ fontSize: 12, color: "var(--text-primary)", whiteSpace: "pre-wrap", lineHeight: 1.5 }}>{node.text}</div>
        {replyOpen && (
          <div style={{ marginTop: 8, paddingTop: 8, borderTop: "1px dashed var(--border)" }}>
            <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
              <select value={reply.module} onChange={e => setReply({ ...reply, module: e.target.value })}
                style={{ padding: "4px 6px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 11 }}>
                <option value="">모듈</option>{MODULES.map(m => <option key={m} value={m}>{m}</option>)}
              </select>
              <select value={reply.reason} onChange={e => setReply({ ...reply, reason: e.target.value })}
                style={{ padding: "4px 6px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 11 }}>
                <option value="">사유</option>{REASONS.map(r => <option key={r} value={r}>{r}</option>)}
              </select>
            </div>
            <textarea value={reply.text} onChange={e => setReply({ ...reply, text: e.target.value })} rows={2}
              placeholder="내용 (재인폼 사유 등)"
              style={{ width: "100%", padding: 6, borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12, resize: "vertical" }} />
            <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
              <button onClick={() => { if (!reply.text.trim()) return; onReply(node.id, reply).then(() => { setReply({ module: "", reason: "", text: "" }); setReplyOpen(false); }); }}
                style={{ padding: "5px 14px", borderRadius: 4, border: "none", background: "var(--accent)", color: "#fff", fontSize: 11, fontWeight: 600, cursor: "pointer" }}>등록</button>
              <button onClick={() => setReplyOpen(false)}
                style={{ padding: "5px 10px", borderRadius: 4, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", fontSize: 11, cursor: "pointer" }}>취소</button>
            </div>
          </div>
        )}
      </div>
      {kids.map(k => (
        <ThreadNode key={k.id} node={k} childrenByParent={childrenByParent}
          onReply={onReply} onDelete={onDelete} user={user} depth={depth + 1} />
      ))}
    </div>
  );
}

export default function My_Inform({ user }) {
  const [wafers, setWafers] = useState([]);
  const [selectedWafer, setSelectedWafer] = useState("");
  const [search, setSearch] = useState("");
  const [thread, setThread] = useState([]);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ wafer_id: "", module: "", reason: "", text: "" });
  const [msg, setMsg] = useState("");

  const loadWafers = () => sf(API + "/wafers").then(d => setWafers(d.wafers || [])).catch(() => setWafers([]));
  const loadThread = (wid) => {
    if (!wid) { setThread([]); return; }
    sf(API + "?wafer_id=" + encodeURIComponent(wid)).then(d => setThread(d.informs || [])).catch(() => setThread([]));
  };
  useEffect(() => { loadWafers(); }, []);
  useEffect(() => { loadThread(selectedWafer); }, [selectedWafer]);

  const create = () => {
    const wid = (form.wafer_id || "").trim();
    if (!wid || !form.text.trim()) { setMsg("wafer_id 와 내용을 입력하세요."); return; }
    sf(API, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...form, wafer_id: wid, parent_id: null }),
    }).then(() => {
      setForm({ wafer_id: "", module: "", reason: "", text: "" });
      setCreating(false); setMsg(""); loadWafers(); setSelectedWafer(wid);
    }).catch(e => setMsg(e.message));
  };

  const reply = (parentId, body) => sf(API, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...body, wafer_id: selectedWafer, parent_id: parentId }),
  }).then(() => { loadThread(selectedWafer); loadWafers(); });

  const del = (id) => {
    if (!confirm("삭제하시겠습니까? (자식이 있으면 삭제 불가)")) return;
    sf(API + "/delete?id=" + encodeURIComponent(id), { method: "POST" })
      .then(() => { loadThread(selectedWafer); loadWafers(); })
      .catch(e => alert(e.message));
  };

  // tree 구성: parent_id 기준 children 맵.
  const { roots, childrenByParent } = useMemo(() => {
    const children = {};
    const roots = [];
    for (const x of thread) {
      if (x.parent_id) { (children[x.parent_id] = children[x.parent_id] || []).push(x); }
      else roots.push(x);
    }
    return { roots, childrenByParent: children };
  }, [thread]);

  const filteredWafers = wafers.filter(w =>
    !search || (w.wafer_id || "").toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div style={{ display: "flex", height: "calc(100vh - 48px)", background: "var(--bg-primary)", color: "var(--text-primary)" }}>
      {/* Sidebar */}
      <div style={{ width: 320, minWidth: 280, borderRight: "1px solid var(--border)", background: "var(--bg-secondary)", display: "flex", flexDirection: "column" }}>
        <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span style={{ fontSize: 14, fontWeight: 700, fontFamily: "monospace", color: "var(--accent)" }}>{">"} Inform Log</span>
          <button onClick={() => setCreating(true)} style={{ padding: "4px 12px", borderRadius: 5, border: "none", background: "var(--accent)", color: "#fff", fontSize: 11, fontWeight: 600, cursor: "pointer" }}>+ 신규</button>
        </div>
        <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>
          <input value={search} onChange={e => setSearch(e.target.value)} placeholder="wafer_id 검색..."
            style={{ width: "100%", padding: "6px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12, outline: "none" }} />
        </div>
        <div style={{ flex: 1, overflowY: "auto" }}>
          {filteredWafers.length === 0 && <div style={{ padding: 20, textAlign: "center", color: "var(--text-secondary)", fontSize: 11 }}>인폼 기록 없음</div>}
          {filteredWafers.map(w => (
            <div key={w.wafer_id} onClick={() => setSelectedWafer(w.wafer_id)}
              style={{ padding: "10px 14px", borderBottom: "1px solid var(--border)", cursor: "pointer", background: selectedWafer === w.wafer_id ? "var(--bg-hover)" : "transparent" }}>
              <div style={{ fontSize: 12, fontWeight: 600, fontFamily: "monospace" }}>{w.wafer_id}</div>
              <div style={{ fontSize: 10, color: "var(--text-secondary)", marginTop: 2 }}>
                {w.count || 0}건 · 최근 {(w.last || "").replace("T", " ").slice(0, 16)}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Main */}
      <div style={{ flex: 1, overflowY: "auto", padding: 24 }}>
        {creating && (
          <div style={{ background: "var(--bg-secondary)", borderRadius: 10, border: "1px solid var(--border)", padding: 18, marginBottom: 18 }}>
            <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 10 }}>새 인폼</div>
            <div style={{ display: "flex", gap: 8, marginBottom: 8, flexWrap: "wrap" }}>
              <input value={form.wafer_id} onChange={e => setForm({ ...form, wafer_id: e.target.value })} placeholder="wafer_id (예: A0001B.1-W03)"
                style={{ flex: "1 1 220px", padding: "8px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12, fontFamily: "monospace" }} />
              <select value={form.module} onChange={e => setForm({ ...form, module: e.target.value })}
                style={{ padding: "8px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12 }}>
                <option value="">-- 모듈 --</option>{MODULES.map(m => <option key={m} value={m}>{m}</option>)}
              </select>
              <select value={form.reason} onChange={e => setForm({ ...form, reason: e.target.value })}
                style={{ padding: "8px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12 }}>
                <option value="">-- 사유 --</option>{REASONS.map(r => <option key={r} value={r}>{r}</option>)}
              </select>
            </div>
            <textarea value={form.text} onChange={e => setForm({ ...form, text: e.target.value })} rows={3}
              placeholder="인폼 내용 (배경, 영향, 조치 요청 등)"
              style={{ width: "100%", padding: 10, borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12, resize: "vertical" }} />
            <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
              <button onClick={create} style={{ padding: "8px 20px", borderRadius: 6, border: "none", background: "var(--accent)", color: "#fff", fontWeight: 600, cursor: "pointer" }}>등록</button>
              <button onClick={() => { setCreating(false); setMsg(""); }} style={{ padding: "8px 16px", borderRadius: 6, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", cursor: "pointer" }}>취소</button>
              {msg && <span style={{ fontSize: 11, color: "#ef4444", alignSelf: "center" }}>{msg}</span>}
            </div>
          </div>
        )}

        {!selectedWafer && !creating && (
          <div style={{ padding: 60, textAlign: "center", color: "var(--text-secondary)" }}>
            좌측에서 wafer 를 선택하거나 <span onClick={() => setCreating(true)} style={{ color: "var(--accent)", cursor: "pointer" }}>+ 신규 인폼</span> 을 등록하세요.
          </div>
        )}

        {selectedWafer && (
          <div>
            <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 12, fontFamily: "monospace" }}>{selectedWafer}</div>
            {roots.length === 0 && <div style={{ padding: 40, textAlign: "center", color: "var(--text-secondary)" }}>아직 인폼 없음.</div>}
            {roots.map(r => (
              <ThreadNode key={r.id} node={r} childrenByParent={childrenByParent}
                onReply={reply} onDelete={del} user={user} depth={0} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
