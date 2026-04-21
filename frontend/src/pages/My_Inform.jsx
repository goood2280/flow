/* My_Inform.jsx v8.7.0 — 모듈 인폼 시스템 (역할 뷰 + 체크 + flow 상태 + SplitTable 연동).
 *
 * 보안: auth 미들웨어 + 세션 토큰 그대로. sf() 가 X-Session-Token 자동 주입.
 * 삭제 정책: 작성자 본인만 (관리자도 불가) — 서버에서도 동일하게 강제됨.
 */
import { useEffect, useMemo, useState } from "react";
import { sf, authSrc, postJson } from "../lib/api";
import PageGear from "../components/PageGear";

const API = "/api/informs";

const STATUS_META = {
  received:    { label: "접수",   color: "#64748b", dot: "○" },
  reviewing:   { label: "검토중", color: "#3b82f6", dot: "◐" },
  in_progress: { label: "진행중", color: "#f59e0b", dot: "◑" },
  completed:   { label: "완료",   color: "#22c55e", dot: "●" },
};
const STATUS_ORDER = ["received", "reviewing", "in_progress", "completed"];

/* v8.7.1 — 모듈별 구분색 (좌측 리스트 / 루트카드 left border / Gantt bar fallback) */
const MODULE_COLORS = {
  GATE:   "#ef4444",
  STI:    "#f59e0b",
  PC:     "#eab308",
  MOL:    "#10b981",
  BEOL:   "#3b82f6",
  ET:     "#8b5cf6",
  EDS:    "#ec4899",
  "S-D Epi": "#14b8a6",
  Spacer: "#06b6d4",
  Well:   "#a855f7",
  MASK:   "#64748b",
  FAB:    "#334155",
  KNOB:   "#0ea5e9",
  "기타": "#6b7280",
};
const FALLBACK_PALETTE = ["#6366f1", "#db2777", "#0d9488", "#c2410c", "#7c3aed", "#be123c", "#16a34a"];

function moduleColor(name) {
  if (!name) return "#6b7280";
  if (MODULE_COLORS[name]) return MODULE_COLORS[name];
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) | 0;
  return FALLBACK_PALETTE[Math.abs(h) % FALLBACK_PALETTE.length];
}

function StatusBadge({ status }) {
  const m = STATUS_META[status] || { label: status || "-", color: "var(--text-secondary)", dot: "·" };
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 4,
      padding: "2px 8px", borderRadius: 999,
      background: m.color + "22", color: m.color,
      fontSize: 10, fontWeight: 700,
    }}>
      <span>{m.dot}</span>{m.label}
    </span>
  );
}

function CheckPill({ node }) {
  if (!node.checked) return null;
  return (
    <span title={`by ${node.checked_by} · ${(node.checked_at||"").replace("T"," ")}`}
      style={{
        fontSize: 10, padding: "2px 8px", borderRadius: 999,
        background: "#22c55e22", color: "#16a34a", fontWeight: 700,
      }}>✓ 확인 완료</span>
  );
}

function AutoGenPill({ node }) {
  if (!node.auto_generated) return null;
  return (
    <span style={{
      fontSize: 10, padding: "2px 8px", borderRadius: 999,
      background: "#8b5cf622", color: "#8b5cf6", fontWeight: 700,
    }}>⚙ 자동</span>
  );
}

function ImageGallery({ images }) {
  if (!images || images.length === 0) return null;
  return (
    <div style={{ marginTop: 8, display: "flex", flexWrap: "wrap", gap: 6 }}>
      {images.map((im, i) => (
        <a key={i} href={authSrc(im.url)} target="_blank" rel="noreferrer"
          style={{ display: "block", border: "1px solid var(--border)", borderRadius: 4, padding: 2, background: "var(--bg-primary)" }}>
          <img src={authSrc(im.url)} alt={im.filename}
            style={{ display: "block", maxHeight: 120, maxWidth: 180, objectFit: "contain" }} />
          <div style={{ fontSize: 9, color: "var(--text-secondary)", padding: "2px 4px", textAlign: "center", fontFamily: "monospace" }}>{im.filename}</div>
        </a>
      ))}
    </div>
  );
}

function EmbedTableView({ embed }) {
  if (!embed || (!embed.columns?.length && !embed.rows?.length)) return null;
  const cols = embed.columns || [];
  const rows = embed.rows || [];
  return (
    <div style={{ marginTop: 8, padding: 8, border: "1px solid var(--border)", borderRadius: 4, background: "var(--bg-primary)" }}>
      <div style={{ fontSize: 10, fontWeight: 700, color: "var(--accent)", marginBottom: 4 }}>
        🔗 Embed {embed.source && <span style={{ color: "var(--text-secondary)", fontWeight: 500 }}>· {embed.source}</span>}
      </div>
      {embed.note && <div style={{ fontSize: 10, color: "var(--text-secondary)", marginBottom: 4 }}>{embed.note}</div>}
      <div style={{ maxHeight: 240, overflow: "auto" }}>
        <table style={{ borderCollapse: "collapse", fontSize: 10, fontFamily: "monospace" }}>
          <thead>
            <tr>{cols.map((c, i) => (
              <th key={i} style={{ border: "1px solid var(--border)", padding: "2px 6px", background: "var(--bg-secondary)", textAlign: "left", position: "sticky", top: 0 }}>{c}</th>
            ))}</tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>{r.map((v, j) => (
                <td key={j} style={{ border: "1px solid var(--border)", padding: "2px 6px" }}>{v}</td>
              ))}</tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* 재귀 스레드 노드 */
function ThreadNode({
  node, childrenByParent, onReply, onDelete, onToggleCheck, user,
  depth = 0, constants,
}) {
  const [replyOpen, setReplyOpen] = useState(false);
  const [reply, setReply] = useState({ module: "", reason: "", text: "" });
  const [attachSplit, setAttachSplit] = useState(false);
  const [splitForm, setSplitForm] = useState({ column: "", old_value: "", new_value: "" });
  const [replyImages, setReplyImages] = useState([]);
  const [uploading, setUploading] = useState(false);

  const handleFile = async (fl) => {
    if (!fl || fl.length === 0) return;
    setUploading(true);
    const uploaded = [];
    for (const f of Array.from(fl)) {
      try {
        const fd = new FormData();
        fd.append("file", f);
        const res = await sf("/api/informs/upload", { method: "POST", body: fd });
        uploaded.push({ filename: res.filename, url: res.url, size: res.size });
      } catch (e) {
        alert("업로드 실패: " + e.message);
      }
    }
    setReplyImages((prev) => [...prev, ...uploaded]);
    setUploading(false);
  };
  const canDelete = user && user.username === node.author;
  const kids = childrenByParent[node.id] || [];
  const indent = Math.min(depth, 5) * 28;

  const sc = node.splittable_change;

  return (
    <div style={{ marginLeft: indent }}>
      <div style={{
        background: depth === 0 ? "var(--bg-secondary)" : "var(--bg-card)",
        border: "1px solid var(--border)", borderRadius: 8, padding: 10, marginBottom: 6,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4, flexWrap: "wrap" }}>
          {node.module && (() => { const mc = moduleColor(node.module); return (
            <span style={{ fontSize: 10, padding: "2px 8px", borderRadius: 999, background: mc + "22", color: mc, fontWeight: 700, border: "1px solid " + mc + "55" }}>{node.module}</span>
          ); })()}
          {node.reason && <span style={{ fontSize: 10, padding: "2px 8px", borderRadius: 999, background: "var(--bg-hover)", color: "var(--text-secondary)" }}>[{node.reason}]</span>}
          <CheckPill node={node} />
          <AutoGenPill node={node} />
          <span style={{ fontSize: 11, fontWeight: 600 }}>{node.author}</span>
          <span title={node.created_at || ""} style={{
            fontSize: 10, padding: "2px 8px", borderRadius: 999,
            background: "var(--bg-primary)", color: "var(--text-primary)",
            border: "1px solid var(--border)", fontFamily: "monospace",
            display: "inline-flex", alignItems: "center", gap: 4,
          }}>🕐 {(node.created_at || "").replace("T", " ").slice(0, 16)}</span>
          <div style={{ flex: 1 }} />
          <span onClick={() => onToggleCheck(node)} style={{ fontSize: 10, color: node.checked ? "#ef4444" : "#22c55e", cursor: "pointer" }}>
            {node.checked ? "미확인으로" : "확인 체크"}
          </span>
          <span onClick={() => setReplyOpen(!replyOpen)} style={{ fontSize: 10, color: "var(--accent)", cursor: "pointer" }}>
            {replyOpen ? "닫기" : (depth === 0 ? "재인폼/답글" : "답글")}
          </span>
          {canDelete && kids.length === 0 && (
            <span onClick={() => onDelete(node.id)} style={{ fontSize: 10, color: "#ef4444", cursor: "pointer" }}>삭제</span>
          )}
        </div>

        <div style={{ fontSize: 12, color: "var(--text-primary)", whiteSpace: "pre-wrap", lineHeight: 1.5 }}>{node.text}</div>
        <ImageGallery images={node.images} />
        <EmbedTableView embed={node.embed_table} />

        {sc && (sc.column || sc.new_value) && (
          <div style={{ marginTop: 8, padding: "6px 10px", borderLeft: "3px solid #f59e0b",
                        background: "#f59e0b11", borderRadius: 4, fontSize: 11 }}>
            <b>SplitTable 변경 요청</b>
            <div style={{ fontFamily: "monospace", marginTop: 2 }}>
              {sc.column ? <><span style={{ color: "#f59e0b" }}>{sc.column}</span>: </> : null}
              <span style={{ textDecoration: "line-through", opacity: 0.7 }}>{sc.old_value || "-"}</span>
              {" → "}
              <span style={{ color: "#22c55e", fontWeight: 700 }}>{sc.new_value || "-"}</span>
              {sc.applied && <span style={{ marginLeft: 8, fontSize: 9, color: "#16a34a", fontWeight: 700 }}>APPLIED</span>}
            </div>
          </div>
        )}

        {replyOpen && (
          <div style={{ marginTop: 8, paddingTop: 8, borderTop: "1px dashed var(--border)" }}>
            <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
              <select value={reply.module} onChange={e => setReply({ ...reply, module: e.target.value })}
                style={{ padding: "4px 6px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 11 }}>
                <option value="">모듈</option>{constants.modules.map(m => <option key={m} value={m}>{m}</option>)}
              </select>
              <select value={reply.reason} onChange={e => setReply({ ...reply, reason: e.target.value })}
                style={{ padding: "4px 6px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 11 }}>
                <option value="">사유</option>{constants.reasons.map(r => <option key={r} value={r}>{r}</option>)}
              </select>
              <label style={{ fontSize: 10, color: "var(--text-secondary)", display: "inline-flex", alignItems: "center", gap: 4, cursor: "pointer" }}>
                <input type="checkbox" checked={attachSplit} onChange={e => setAttachSplit(e.target.checked)} />
                SplitTable 변경요청 포함
              </label>
            </div>
            <textarea value={reply.text} onChange={e => setReply({ ...reply, text: e.target.value })} rows={2}
              placeholder="내용 (재인폼 사유, 조치 제안 등)"
              style={{ width: "100%", padding: 6, borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12, resize: "vertical" }} />
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 6, flexWrap: "wrap" }}>
              <label style={{ fontSize: 10, color: "var(--text-secondary)", cursor: "pointer" }}>
                📎 이미지
                <input type="file" accept="image/*" multiple
                  style={{ display: "none" }}
                  onChange={e => { handleFile(e.target.files); e.target.value = ""; }} />
              </label>
              {uploading && <span style={{ fontSize: 10, color: "var(--accent)" }}>업로드중…</span>}
              {replyImages.map((im, i) => (
                <span key={i} style={{ fontSize: 10, padding: "2px 6px", borderRadius: 3, background: "var(--bg-primary)", border: "1px solid var(--border)", display: "inline-flex", alignItems: "center", gap: 4 }}>
                  <img src={authSrc(im.url)} alt="" style={{ width: 24, height: 24, objectFit: "cover", borderRadius: 2 }} />
                  <span style={{ fontFamily: "monospace" }}>{im.filename}</span>
                  <button onClick={() => setReplyImages(replyImages.filter((_, j) => j !== i))}
                    style={{ border: "none", background: "transparent", color: "#ef4444", cursor: "pointer", padding: 0 }}>×</button>
                </span>
              ))}
            </div>
            {attachSplit && (
              <div style={{ marginTop: 6, padding: 8, background: "var(--bg-primary)", borderRadius: 4, border: "1px dashed var(--border)" }}>
                <div style={{ fontSize: 10, color: "var(--text-secondary)", marginBottom: 4, fontWeight: 600 }}>Split Table 변경 (예: KNOB A → B)</div>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  <input value={splitForm.column} onChange={e => setSplitForm({ ...splitForm, column: e.target.value })}
                    placeholder="column (예: KNOB/GATE_PPID)"
                    style={{ flex: "1 1 180px", padding: "4px 6px", borderRadius: 3, border: "1px solid var(--border)", background: "var(--bg-secondary)", color: "var(--text-primary)", fontSize: 11, fontFamily: "monospace" }} />
                  <input value={splitForm.old_value} onChange={e => setSplitForm({ ...splitForm, old_value: e.target.value })}
                    placeholder="old"
                    style={{ flex: "1 1 100px", padding: "4px 6px", borderRadius: 3, border: "1px solid var(--border)", background: "var(--bg-secondary)", color: "var(--text-primary)", fontSize: 11, fontFamily: "monospace" }} />
                  <input value={splitForm.new_value} onChange={e => setSplitForm({ ...splitForm, new_value: e.target.value })}
                    placeholder="new"
                    style={{ flex: "1 1 100px", padding: "4px 6px", borderRadius: 3, border: "1px solid var(--border)", background: "var(--bg-secondary)", color: "var(--text-primary)", fontSize: 11, fontFamily: "monospace" }} />
                </div>
              </div>
            )}
            <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
              <button onClick={() => {
                if (!reply.text.trim() && replyImages.length === 0) return;
                const body = { ...reply, images: replyImages };
                if (attachSplit && (splitForm.column || splitForm.new_value)) {
                  body.splittable_change = { ...splitForm, applied: false };
                }
                onReply(node.id, body).then(() => {
                  setReply({ module: "", reason: "", text: "" });
                  setSplitForm({ column: "", old_value: "", new_value: "" });
                  setAttachSplit(false);
                  setReplyImages([]);
                  setReplyOpen(false);
                });
              }}
                style={{ padding: "5px 14px", borderRadius: 4, border: "none", background: "var(--accent)", color: "#fff", fontSize: 11, fontWeight: 600, cursor: "pointer" }}>등록</button>
              <button onClick={() => setReplyOpen(false)}
                style={{ padding: "5px 10px", borderRadius: 4, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", fontSize: 11, cursor: "pointer" }}>취소</button>
            </div>
          </div>
        )}
      </div>
      {kids.map(k => (
        <ThreadNode key={k.id} node={k} childrenByParent={childrenByParent}
          onReply={onReply} onDelete={onDelete} onToggleCheck={onToggleCheck}
          user={user} depth={depth + 1} constants={constants} />
      ))}
    </div>
  );
}

/* 데드라인 badge + 편집 */
function DeadlineBadge({ deadline, onChange, canEdit }) {
  const [editing, setEditing] = useState(false);
  const [val, setVal] = useState(deadline || "");
  useEffect(() => { setVal(deadline || ""); }, [deadline]);
  const today = new Date().toISOString().slice(0, 10);
  const overdue = deadline && deadline < today;
  const near = deadline && !overdue && (new Date(deadline) - new Date(today)) / 86400000 <= 3;
  const color = overdue ? "#ef4444" : near ? "#f59e0b" : "#3b82f6";
  if (editing && canEdit) {
    return (
      <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
        <input type="date" value={val} onChange={e => setVal(e.target.value)}
          style={{ fontSize: 11, padding: "2px 4px", borderRadius: 3, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)" }} />
        <button onClick={() => { onChange(val); setEditing(false); }}
          style={{ fontSize: 10, padding: "2px 8px", borderRadius: 3, border: "none", background: "var(--accent)", color: "#fff", cursor: "pointer" }}>저장</button>
        {deadline && <button onClick={() => { onChange(""); setEditing(false); }}
          style={{ fontSize: 10, padding: "2px 8px", borderRadius: 3, border: "1px solid var(--border)", background: "transparent", color: "#ef4444", cursor: "pointer" }}>해제</button>}
        <button onClick={() => setEditing(false)}
          style={{ fontSize: 10, padding: "2px 6px", borderRadius: 3, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", cursor: "pointer" }}>×</button>
      </span>
    );
  }
  if (!deadline) {
    if (!canEdit) return null;
    return <span onClick={() => setEditing(true)} style={{ fontSize: 10, color: "var(--text-secondary)", cursor: "pointer", padding: "2px 8px", borderRadius: 999, border: "1px dashed var(--border)" }}>🗓 데드라인 설정</span>;
  }
  return (
    <span onClick={() => canEdit && setEditing(true)}
      title={overdue ? "마감 초과" : near ? "임박" : "데드라인"}
      style={{
        fontSize: 10, fontWeight: 700,
        padding: "2px 8px", borderRadius: 999,
        background: color + "22", color, border: "1px solid " + color,
        cursor: canEdit ? "pointer" : "default",
        fontFamily: "monospace",
      }}>🗓 {deadline}{overdue ? " ⚠" : near ? " ⏳" : ""}</span>
  );
}

/* 루트 인폼 머리에 붙는 상태 패널 (flow 진행 + 이력) */
function MailDialog({ root, user, onClose }) {
  // v8.7.2: 인폼 → 사내 메일 API 로 HTML 본문 전송 (multipart).
  const [recipients, setRecipients] = useState([]);
  const [groups, setGroups] = useState({});          // {groupName: [emails]}
  const [pickedUsers, setPickedUsers] = useState([]);   // usernames
  const [pickedGroups, setPickedGroups] = useState([]); // group names
  const [subject, setSubject] = useState(`[flow 인폼] ${root.module || ""} · ${root.lot_id || root.wafer_id || ""}`.trim());
  const [body, setBody] = useState("");
  const [statusCode, setStatusCode] = useState("");
  const [includeThread, setIncludeThread] = useState(true);
  const [extraEmails, setExtraEmails] = useState("");
  const [attachments, setAttachments] = useState([]); // inform image URLs to include
  const [filter, setFilter] = useState("");
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    sf(API + "/recipients").then(d => setRecipients(d.recipients || [])).catch(() => setRecipients([]));
    sf(API + "/mail-groups").then(d => setGroups(d.groups || {})).catch(() => setGroups({}));
  }, []);

  // Collect attachable images from root + any thread child (if provided via root.images)
  const inlineImages = [...(root.images || [])].filter(x => x && x.url);

  const toggleUser = (un) => setPickedUsers(p => p.includes(un) ? p.filter(x => x !== un) : [...p, un]);
  const toggleGroup = (g) => setPickedGroups(p => p.includes(g) ? p.filter(x => x !== g) : [...p, g]);
  const toggleAttach = (u) => setAttachments(a => a.includes(u) ? a.filter(x => x !== u) : [...a, u]);
  const visibleList = recipients.filter(r => {
    if (!filter.trim()) return true;
    const q = filter.trim().toLowerCase();
    return r.username.toLowerCase().includes(q) || (r.email || "").toLowerCase().includes(q);
  });
  const computedEmails = () => {
    const out = new Set();
    pickedUsers.forEach(un => {
      const em = recipients.find(r => r.username === un)?.email;
      if (em && em.includes("@")) out.add(em);
    });
    pickedGroups.forEach(g => (groups[g] || []).forEach(em => { if (em && em.includes("@")) out.add(em); }));
    (extraEmails || "").split(/[,\s;]+/).map(s => s.trim()).filter(s => s && s.includes("@")).forEach(em => out.add(em));
    return Array.from(out);
  };
  const totalEmails = computedEmails().length;

  const doSend = () => {
    setError(""); setSent(null);
    const to = computedEmails();
    if (to.length === 0) { setError("수신자 이메일을 1명 이상 선택하세요 (그룹·유저·추가 이메일)."); return; }
    if (to.length > 199) { setError(`수신자는 최대 199명입니다 (현재 ${to.length}명).`); return; }
    if (!subject.trim()) { setError("제목을 입력하세요."); return; }
    setSending(true);
    sf(`${API}/${root.id}/send-mail`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        to, to_users: pickedUsers, groups: pickedGroups,
        subject: subject.trim(), body: body.trim(),
        include_thread: includeThread, status_code: statusCode.trim(),
        attachments,
      }),
    }).then(r => {
      setSent({ ok: true, to: r.to || to, status: r.status, dry_run: !!r.dry_run });
    }).catch(e => {
      setError(e?.message || "메일 전송 실패");
    }).finally(() => setSending(false));
  };

  const S = { width: "100%", padding: "6px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12, outline: "none" };

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.75)", zIndex: 9999, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
      <div onClick={e => e.stopPropagation()} style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", borderRadius: 10, padding: 18, width: "95%", maxWidth: 820, maxHeight: "92vh", overflow: "auto", color: "var(--text-primary)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <div style={{ fontSize: 15, fontWeight: 700 }}>✉ 인폼 메일 보내기 <span style={{ fontSize: 10, fontWeight: 400, color: "var(--text-secondary)" }}>(최대 199명 · 본문 2MB · 첨부 10MB)</span></div>
          <span onClick={onClose} style={{ cursor: "pointer", fontSize: 18 }}>✕</span>
        </div>
        <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 8 }}>Admin 설정의 메일 API 로 multipart POST. 수신자 총 <b style={{ color: "var(--accent)" }}>{totalEmails}명</b> · Inform <code>{root.id}</code></div>

        {/* Module recipient groups */}
        {Object.keys(groups).length > 0 && <div style={{ marginBottom: 10 }}>
          <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4 }}>📮 모듈 그룹 <span style={{ fontWeight: 400, color: "var(--text-secondary)" }}>({pickedGroups.length} 선택)</span></div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {Object.entries(groups).map(([gname, emails]) => {
              const on = pickedGroups.includes(gname);
              return (
                <span key={gname} onClick={() => toggleGroup(gname)} style={{
                  padding: "5px 12px", borderRadius: 999, fontSize: 11,
                  background: on ? "var(--accent)" : "var(--bg-card)",
                  color: on ? "#fff" : "var(--text-primary)",
                  border: "1px solid " + (on ? "var(--accent)" : "var(--border)"),
                  cursor: "pointer", fontWeight: 600,
                }}>{gname} · {(emails || []).length}명</span>
              );
            })}
          </div>
        </div>}

        {/* Individual recipient picker */}
        <div style={{ marginBottom: 10 }}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4, fontSize: 11, fontWeight: 600 }}>
            <span>개별 유저 ({pickedUsers.length} 선택)</span>
            <input value={filter} onChange={e => setFilter(e.target.value)} placeholder="🔎 유저/이메일 검색" style={{ padding: "3px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 11, width: 200 }} />
          </div>
          <div style={{ maxHeight: 140, overflow: "auto", border: "1px solid var(--border)", borderRadius: 6, background: "var(--bg-card)" }}>
            {visibleList.length === 0 && <div style={{ padding: 14, textAlign: "center", fontSize: 11, color: "var(--text-secondary)" }}>유저가 없습니다. Admin → 사용자 탭에서 email 을 설정해야 합니다.</div>}
            {visibleList.map(r => {
              const on = pickedUsers.includes(r.username);
              const hasEmail = !!(r.email && r.email.includes("@"));
              return (
                <div key={r.username} onClick={() => hasEmail && toggleUser(r.username)} style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 10px", fontSize: 11, cursor: hasEmail ? "pointer" : "not-allowed", background: on ? "rgba(59,130,246,0.12)" : "transparent", opacity: hasEmail ? 1 : 0.5, borderBottom: "1px solid var(--border)" }}>
                  <input type="checkbox" checked={on} disabled={!hasEmail} readOnly />
                  <span style={{ fontWeight: 600, minWidth: 100 }}>{r.username}</span>
                  <span style={{ fontFamily: "monospace", color: hasEmail ? "var(--text-secondary)" : "#ef4444", flex: 1 }}>{r.email || "(no email)"}</span>
                  {r.role === "admin" && <span style={{ fontSize: 9, padding: "1px 6px", borderRadius: 10, background: "rgba(239,68,68,0.15)", color: "#ef4444", fontWeight: 700 }}>ADMIN</span>}
                </div>
              );
            })}
          </div>
        </div>

        <div style={{ marginBottom: 10 }}>
          <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 3 }}>추가 이메일 <span style={{ fontWeight: 400, color: "var(--text-secondary)" }}>(콤마/공백/세미콜론 구분)</span></div>
          <input value={extraEmails} onChange={e => setExtraEmails(e.target.value)} placeholder="ext1@vendor.com, ext2@vendor.com" style={{ ...S, fontFamily: "monospace", fontSize: 11 }} />
        </div>

        <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
          <div style={{ flex: 3 }}>
            <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 3 }}>제목 (title)</div>
            <input value={subject} onChange={e => setSubject(e.target.value)} style={S} />
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 3 }}>statusCode</div>
            <input value={statusCode} onChange={e => setStatusCode(e.target.value)} placeholder="(admin 기본값)" style={S} />
          </div>
        </div>
        <div style={{ marginBottom: 8 }}>
          <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 3 }}>본문 프로즈 <span style={{ fontWeight: 400, color: "var(--text-secondary)" }}>(HTML content 상단에 강조 삽입, 생략 가능)</span></div>
          <textarea value={body} onChange={e => setBody(e.target.value)} rows={4} style={{ ...S, resize: "vertical" }} />
        </div>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, color: "var(--text-secondary)", marginBottom: 8 }}>
          <input type="checkbox" checked={includeThread} onChange={e => setIncludeThread(e.target.checked)} />
          전체 스레드(답글 포함) HTML 로 첨부
        </label>

        {inlineImages.length > 0 && <div style={{ marginBottom: 10 }}>
          <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 3 }}>📎 첨부 이미지 <span style={{ fontWeight: 400, color: "var(--text-secondary)" }}>(각 파일 10MB 한도 · 총합 제한)</span></div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {inlineImages.map(img => {
              const on = attachments.includes(img.url);
              return <span key={img.url} onClick={() => toggleAttach(img.url)} style={{
                padding: "4px 10px", borderRadius: 4, fontSize: 10,
                background: on ? "rgba(16,185,129,0.15)" : "var(--bg-card)",
                color: on ? "#10b981" : "var(--text-primary)",
                border: "1px solid " + (on ? "#10b981" : "var(--border)"),
                cursor: "pointer",
              }}>{on ? "✔" : "＋"} {img.filename || img.url.split("/").pop()}</span>;
            })}
          </div>
        </div>}

        {error && <div style={{ padding: "6px 10px", background: "rgba(239,68,68,0.1)", color: "#ef4444", border: "1px solid #ef4444", borderRadius: 4, fontSize: 11, marginBottom: 8 }}>⚠ {error}</div>}
        {sent && <div style={{ padding: "6px 10px", background: "rgba(16,185,129,0.1)", color: "#10b981", border: "1px solid #10b981", borderRadius: 4, fontSize: 11, marginBottom: 8 }}>✔ 전송됨 ({(sent.to || []).length}명){sent.dry_run && " · DRY RUN (실제 전송 안됨)"}</div>}

        <div style={{ display: "flex", gap: 8 }}>
          <button disabled={sending} onClick={doSend} style={{ padding: "8px 20px", borderRadius: 6, border: "none", background: sending ? "var(--text-secondary)" : "var(--accent)", color: "#fff", fontWeight: 600, cursor: sending ? "wait" : "pointer" }}>{sending ? "전송 중…" : `📧 ${totalEmails}명에게 전송`}</button>
          <button onClick={onClose} style={{ padding: "8px 16px", borderRadius: 6, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", cursor: "pointer" }}>닫기</button>
        </div>
      </div>
    </div>
  );
}

function RootHeader({ root, onChangeStatus, onChangeDeadline, user }) {
  const [note, setNote] = useState("");
  const [openHist, setOpenHist] = useState(false);
  const [openMail, setOpenMail] = useState(false);
  const canEditDeadline = !!user && (user.role === "admin" || user.username === root.author);
  const hist = root.status_history || [];
  const mailCount = (root.mail_history || []).length;
  return (
    <div style={{
      background: "var(--bg-secondary)", border: "1px solid var(--border)",
      borderRadius: 8, padding: 12, marginBottom: 10,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: "var(--text-secondary)" }}>FLOW</div>
        {STATUS_ORDER.map((s, i) => {
          const m = STATUS_META[s];
          const active = root.flow_status === s;
          return (
            <div key={s} style={{ display: "flex", alignItems: "center", gap: 4 }}>
              <button onClick={() => onChangeStatus(root.id, s, note).then(() => setNote(""))}
                style={{
                  padding: "4px 10px", borderRadius: 999,
                  border: "1px solid " + (active ? m.color : "var(--border)"),
                  background: active ? m.color + "22" : "transparent",
                  color: active ? m.color : "var(--text-secondary)",
                  fontSize: 11, fontWeight: active ? 700 : 500, cursor: "pointer",
                }}>{m.dot} {m.label}</button>
              {i < STATUS_ORDER.length - 1 && <span style={{ color: "var(--text-secondary)", fontSize: 10 }}>→</span>}
            </div>
          );
        })}
        <div style={{ flex: 1 }} />
        <DeadlineBadge deadline={root.deadline} onChange={v => onChangeDeadline(root.id, v)} canEdit={canEditDeadline} />
        <input value={note} onChange={e => setNote(e.target.value)} placeholder="상태변경 메모 (optional)"
          style={{ padding: "4px 8px", borderRadius: 4, border: "1px solid var(--border)",
                   background: "var(--bg-primary)", color: "var(--text-primary)",
                   fontSize: 11, width: 220 }} />
        <span onClick={() => setOpenMail(true)}
          title="사내 메일 API 로 이 인폼 내용 전송"
          style={{ padding: "4px 10px", borderRadius: 4, border: "1px solid var(--accent)",
                   background: "rgba(249,115,22,0.1)", color: "var(--accent)",
                   fontSize: 10, fontWeight: 700, cursor: "pointer", userSelect: "none" }}>
          ✉ 메일 보내기{mailCount > 0 && ` (${mailCount})`}
        </span>
        <span onClick={() => setOpenHist(!openHist)}
          style={{ fontSize: 10, color: "var(--accent)", cursor: "pointer" }}>
          이력 {hist.length > 0 && `(${hist.length})`}
        </span>
      </div>
      {openMail && <MailDialog root={root} user={user} onClose={() => setOpenMail(false)} />}
      {openHist && hist.length > 0 && (
        <div style={{ marginTop: 8, paddingTop: 8, borderTop: "1px dashed var(--border)", fontSize: 10, color: "var(--text-secondary)", fontFamily: "monospace" }}>
          {hist.slice().reverse().map((h, i) => (
            <div key={i} style={{ marginBottom: 2 }}>
              {(h.at || "").replace("T", " ")} · <b>{h.actor}</b> → <StatusBadge status={h.status} />
              {h.note && <> · <span style={{ opacity: 0.8 }}>{h.note}</span></>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* Plan change summary — 스레드 내 모든 splittable_change 를 상단에 묶어서 노출 */
function PlanSummaryCard({ thread }) {
  const changes = (thread || []).filter(x => x.splittable_change && (x.splittable_change.column || x.splittable_change.new_value));
  if (changes.length === 0) return null;
  return (
    <div style={{
      background: "#f59e0b11", border: "1px solid #f59e0b66",
      borderRadius: 8, padding: 10, marginBottom: 10,
    }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: "#c2410c", marginBottom: 6 }}>
        ■ Split Table 변경 요약 ({changes.length}건)
      </div>
      {changes.map(x => {
        const sc = x.splittable_change;
        return (
          <div key={x.id} style={{ fontSize: 11, fontFamily: "monospace", marginBottom: 2 }}>
            <span style={{ opacity: 0.7 }}>{x.author}</span>
            {" · "}
            {sc.column && <span style={{ color: "#c2410c" }}>{sc.column}</span>}
            {sc.column && ": "}
            <span style={{ textDecoration: "line-through", opacity: 0.6 }}>{sc.old_value || "-"}</span>
            {" → "}
            <span style={{ color: "#16a34a", fontWeight: 700 }}>{sc.new_value || "-"}</span>
          </div>
        );
      })}
      <div style={{ fontSize: 10, color: "#92400e", marginTop: 6, opacity: 0.85 }}>
        * 위 column 은 SplitTable 에서 해당 인폼과 연결된 컬럼입니다.
      </div>
    </div>
  );
}

/* v8.7.8: Lot drill-down 모듈별 요약 테이블
   각 모듈에 대해 (등록됨, 메일 전송됨) 을 체크/미체크로 한눈에 */
function LotModuleSummary({ thread, modules }) {
  const rows = (modules || []).map(m => {
    const entries = (thread || []).filter(e => (e.module || "") === m);
    const hasInform = entries.length > 0;
    // mail 여부: 현재 스키마엔 mail flag 가 인폼 level 에 없음 → 댓글/상태 변경 any 존재 여부로 대체
    const hasMail = entries.some(e => e.mail_sent || e.mail || (e.history || []).some(h => (h.action || "").includes("mail")));
    const rootCount = entries.filter(e => !e.parent_id).length;
    const replyCount = entries.filter(e => e.parent_id).length;
    return { module: m, hasInform, hasMail, rootCount, replyCount };
  });
  if (!rows.length) return null;
  return (
    <div style={{ marginBottom: 14, padding: 10, borderRadius: 8, background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
      <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 6, fontFamily: "monospace", color: "var(--accent)" }}>📋 모듈별 진행 요약</div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ borderCollapse: "collapse", fontSize: 11, width: "100%" }}>
          <thead>
            <tr style={{ background: "var(--bg-tertiary)" }}>
              <th style={{ padding: "4px 8px", textAlign: "left", borderBottom: "1px solid var(--border)", fontWeight: 600 }}>모듈</th>
              <th style={{ padding: "4px 8px", textAlign: "center", borderBottom: "1px solid var(--border)", fontWeight: 600 }}>인폼</th>
              <th style={{ padding: "4px 8px", textAlign: "center", borderBottom: "1px solid var(--border)", fontWeight: 600 }}>메일</th>
              <th style={{ padding: "4px 8px", textAlign: "center", borderBottom: "1px solid var(--border)", fontWeight: 600 }}>루트</th>
              <th style={{ padding: "4px 8px", textAlign: "center", borderBottom: "1px solid var(--border)", fontWeight: 600 }}>답글</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.module}>
                <td style={{ padding: "3px 8px", fontFamily: "monospace", borderBottom: "1px solid var(--border)" }}>{r.module}</td>
                <td style={{ padding: "3px 8px", textAlign: "center", borderBottom: "1px solid var(--border)", color: r.hasInform ? "#22c55e" : "var(--text-secondary)", fontWeight: 700 }}>{r.hasInform ? "✓" : "·"}</td>
                <td style={{ padding: "3px 8px", textAlign: "center", borderBottom: "1px solid var(--border)", color: r.hasMail ? "#3b82f6" : "var(--text-secondary)", fontWeight: 700 }}>{r.hasMail ? "✓" : "·"}</td>
                <td style={{ padding: "3px 8px", textAlign: "center", borderBottom: "1px solid var(--border)", color: "var(--text-secondary)" }}>{r.rootCount}</td>
                <td style={{ padding: "3px 8px", textAlign: "center", borderBottom: "1px solid var(--border)", color: "var(--text-secondary)" }}>{r.replyCount}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}


/* ── 메인 페이지 ── */
export default function My_Inform({ user }) {
  const [constants, setConstants] = useState({ modules: [], reasons: [], flow_statuses: [] });
  const [mode, setMode] = useState("all");           // all | mine | product | lot | wafer
  const [myMods, setMyMods] = useState({ modules: [], all_rounder: false });

  const [wafers, setWafers] = useState([]);
  const [products, setProducts] = useState([]);
  const [lots, setLots] = useState([]);

  const [search, setSearch] = useState("");
  const [selectedWafer, setSelectedWafer] = useState("");
  const [selectedLot, setSelectedLot] = useState("");
  const [selectedProduct, setSelectedProduct] = useState("");

  const [thread, setThread] = useState([]);          // 선택 scope 의 전체 entries (wafer/lot/product)
  const [lotWafers, setLotWafers] = useState([]);    // lot 모드에서 포함된 wafer 들

  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({
    wafer_id: "", lot_id: "", product: "", module: "", reason: "", text: "",
    deadline: "",
    attach_split: false, split: { column: "", old_value: "", new_value: "" },
    attach_embed: false, embed: { source: "", columns: [], rows: [], note: "" },
  });
  const [createImages, setCreateImages] = useState([]);
  const [uploadingMain, setUploadingMain] = useState(false);
  const [embedFetching, setEmbedFetching] = useState(false);
  const [msg, setMsg] = useState("");

  const [moduleFilter, setModuleFilter] = useState([]);  // admin 대시보드식 모듈 필터

  const isAdmin = user?.role === "admin";

  /* Load constants + my modules */
  useEffect(() => {
    sf(API + "/modules").then(d => setConstants({
      modules: d.modules || [], reasons: d.reasons || [], flow_statuses: d.flow_statuses || [],
    })).catch(() => {});
    sf("/api/groups/my-modules").then(d => setMyMods({
      modules: d.modules || [], all_rounder: !!d.all_rounder,
    })).catch(() => setMyMods({ modules: [], all_rounder: !!isAdmin }));
  }, []);

  const loadSidebar = () => {
    sf(API + "/wafers").then(d => setWafers(d.wafers || [])).catch(() => setWafers([]));
    sf(API + "/products").then(d => setProducts(d.products || [])).catch(() => setProducts([]));
    sf(API + "/lots").then(d => setLots(d.lots || [])).catch(() => setLots([]));
  };
  useEffect(loadSidebar, [mode]);

  /* Scope 별 thread 로드 */
  useEffect(() => {
    if (mode === "wafer" && selectedWafer) {
      sf(API + "?wafer_id=" + encodeURIComponent(selectedWafer))
        .then(d => { setThread(d.informs || []); setLotWafers([]); })
        .catch(() => setThread([]));
    } else if (mode === "lot" && selectedLot) {
      sf(API + "/by-lot?lot_id=" + encodeURIComponent(selectedLot))
        .then(d => { setThread(d.informs || []); setLotWafers(d.wafers || []); })
        .catch(() => { setThread([]); setLotWafers([]); });
    } else if (mode === "product" && selectedProduct) {
      sf(API + "/by-product?product=" + encodeURIComponent(selectedProduct))
        .then(d => { setThread(d.informs || []); setLotWafers([]); })
        .catch(() => setThread([]));
    } else if (mode === "mine") {
      sf(API + "/my").then(d => { setThread(d.informs || []); setLotWafers([]); })
        .catch(() => setThread([]));
    } else if (mode === "all" || mode === "gantt") {
      sf(API + "/recent?limit=300").then(d => { setThread(d.informs || []); setLotWafers([]); })
        .catch(() => setThread([]));
    } else {
      setThread([]); setLotWafers([]);
    }
  }, [mode, selectedWafer, selectedLot, selectedProduct]);

  const refreshAll = () => {
    loadSidebar();
    if (mode === "wafer" && selectedWafer) {
      sf(API + "?wafer_id=" + encodeURIComponent(selectedWafer)).then(d => setThread(d.informs || []));
    } else if (mode === "lot" && selectedLot) {
      sf(API + "/by-lot?lot_id=" + encodeURIComponent(selectedLot))
        .then(d => { setThread(d.informs || []); setLotWafers(d.wafers || []); });
    } else if (mode === "product" && selectedProduct) {
      sf(API + "/by-product?product=" + encodeURIComponent(selectedProduct)).then(d => setThread(d.informs || []));
    } else if (mode === "mine") {
      sf(API + "/my").then(d => setThread(d.informs || []));
    } else {
      sf(API + "/recent?limit=300").then(d => setThread(d.informs || []));
    }
  };

  const create = () => {
    const wid = (form.wafer_id || "").trim();
    if (!wid || (!form.text.trim() && createImages.length === 0)) {
      setMsg("wafer_id 와 내용(또는 이미지)을 입력하세요."); return;
    }
    const body = {
      wafer_id: wid, lot_id: form.lot_id.trim(), product: form.product.trim(),
      module: form.module, reason: form.reason, text: form.text, parent_id: null,
      images: createImages, deadline: (form.deadline || "").trim(),
    };
    if (form.attach_split && (form.split.column || form.split.new_value)) {
      body.splittable_change = { ...form.split, applied: false };
    }
    if (form.attach_embed && form.embed && (form.embed.columns.length || form.embed.rows.length)) {
      body.embed_table = form.embed;
    }
    sf(API, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(() => {
      setForm({
        wafer_id: "", lot_id: "", product: "", module: "", reason: "", text: "",
        deadline: "",
        attach_split: false, split: { column: "", old_value: "", new_value: "" },
        attach_embed: false, embed: { source: "", columns: [], rows: [], note: "" },
      });
      setCreateImages([]);
      setCreating(false); setMsg("");
      setMode("wafer"); setSelectedWafer(wid);
      setTimeout(refreshAll, 50);
    }).catch(e => setMsg(e.message));
  };

  const uploadMain = async (fl) => {
    if (!fl || fl.length === 0) return;
    setUploadingMain(true);
    const out = [];
    for (const f of Array.from(fl)) {
      try {
        const fd = new FormData();
        fd.append("file", f);
        const res = await sf("/api/informs/upload", { method: "POST", body: fd });
        out.push({ filename: res.filename, url: res.url, size: res.size });
      } catch (e) { alert("업로드 실패: " + e.message); }
    }
    setCreateImages((prev) => [...prev, ...out]);
    setUploadingMain(false);
  };

  // SplitTable 에서 현재 product 의 plan 스냅샷을 본문에 임베드.
  const embedFromSplitTable = async () => {
    const prod = (form.product || "").trim();
    if (!prod) { alert("product 를 먼저 입력하세요."); return; }
    setEmbedFetching(true);
    try {
      const hist = await sf("/api/splittable/history?product=" + encodeURIComponent(prod) + "&limit=100");
      const rows = (hist.history || []).slice(-50).map(h => [
        (h.time || "").replace("T", " ").slice(0, 19),
        h.user || "", h.action || "", h.cell || "",
        h.old === null || h.old === undefined ? "" : String(h.old),
        h.new === null || h.new === undefined ? "" : String(h.new),
        h.root_lot_id || "",
      ]);
      setForm(f => ({
        ...f, attach_embed: true,
        embed: {
          source: `SplitTable/${prod} (history)`,
          columns: ["time", "user", "action", "cell", "old", "new", "lot"],
          rows,
          note: `${rows.length} entries embedded`,
        },
      }));
    } catch (e) {
      alert("SplitTable 가져오기 실패: " + e.message);
    } finally { setEmbedFetching(false); }
  };

  const reply = (parentId, body) => {
    // parent 의 wafer/lot/product 상속은 서버가 알아서
    const parent = thread.find(x => x.id === parentId);
    return sf(API, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...body, wafer_id: parent?.wafer_id || "", parent_id: parentId,
        images: body.images || [],
      }),
    }).then(refreshAll);
  };

  /* admin 대시보드식 모듈 필터: rootsSorted 를 2차 필터링 */
  const applyModFilter = (arr) => {
    if (!moduleFilter || moduleFilter.length === 0) return arr;
    return arr.filter(x => moduleFilter.includes(x.module || ""));
  };

  const del = (id) => {
    if (!confirm("삭제하시겠습니까? (작성자 본인만 가능 · 답글 있으면 불가)")) return;
    sf(API + "/delete?id=" + encodeURIComponent(id), { method: "POST" })
      .then(refreshAll).catch(e => alert(e.message));
  };

  const toggleCheck = (node) => sf(API + "/check?id=" + encodeURIComponent(node.id), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ checked: !node.checked }),
  }).then(refreshAll).catch(e => alert(e.message));

  const changeStatus = (id, status, note) => sf(API + "/status?id=" + encodeURIComponent(id), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status, note: note || "" }),
  }).then(refreshAll).catch(e => alert(e.message));

  const changeDeadline = (id, deadline) => sf(API + "/deadline?id=" + encodeURIComponent(id), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ deadline: deadline || "" }),
  }).then(refreshAll).catch(e => alert(e.message));

  /* thread → (roots + childrenByParent) — wafer 모드는 한 wafer 전체 트리,
     lot/product 모드는 여러 루트 흐름이 섞여있을 수 있음. */
  const { rootsSorted, childrenByParent } = useMemo(() => {
    const kids = {};
    const roots = [];
    for (const x of thread) {
      if (x.parent_id) (kids[x.parent_id] = kids[x.parent_id] || []).push(x);
      else roots.push(x);
    }
    // roots sort: 모듈 섞인 뷰(lot/product/all/mine)는 최근순, wafer 단일뷰는 시간순
    const single = mode === "wafer";
    roots.sort((a, b) => single
      ? (a.created_at || "").localeCompare(b.created_at || "")
      : (b.created_at || "").localeCompare(a.created_at || ""));
    // children 시간순
    Object.values(kids).forEach(arr => arr.sort((a, b) => (a.created_at || "").localeCompare(b.created_at || "")));
    return { rootsSorted: roots, childrenByParent: kids };
  }, [thread, mode]);

  /* 사이드바 목록 (mode 별) */
  const sidebarItems = useMemo(() => {
    const q = search.trim().toLowerCase();
    const match = (s) => !q || (s || "").toLowerCase().includes(q);
    if (mode === "wafer") {
      return wafers.filter(w => match(w.wafer_id) || match(w.lot_id) || match(w.product))
        .map(w => ({ key: w.wafer_id, label: w.wafer_id, sub: `${w.count || 0}건 · ${(w.lot_id || "-")} · ${w.product || "-"}` }));
    }
    if (mode === "lot") {
      return lots.filter(l => match(l.lot_id) || match(l.product))
        .map(l => ({ key: l.lot_id, label: l.lot_id, sub: `${l.count || 0}건 · ${l.product || "-"}` }));
    }
    if (mode === "product") {
      return products.filter(p => match(p.product))
        .map(p => ({ key: p.product, label: p.product, sub: `${p.count || 0}건 · 최근 ${(p.last || "").slice(0, 10)}` }));
    }
    return []; // mine/all 은 사이드바 없이 메인에 직접 표시
  }, [mode, wafers, lots, products, search]);

  const selectedKey = mode === "wafer" ? selectedWafer
                    : mode === "lot"  ? selectedLot
                    : mode === "product" ? selectedProduct : "";
  const setSelected = (k) => {
    if (mode === "wafer") setSelectedWafer(k);
    else if (mode === "lot") setSelectedLot(k);
    else if (mode === "product") setSelectedProduct(k);
  };

  const modeButton = (key, label, hint) => (
    <button onClick={() => setMode(key)}
      title={hint}
      style={{
        padding: "6px 12px", borderRadius: 6,
        border: "1px solid " + (mode === key ? "var(--accent)" : "var(--border)"),
        background: mode === key ? "var(--accent)22" : "transparent",
        color: mode === key ? "var(--accent)" : "var(--text-secondary)",
        fontSize: 11, fontWeight: mode === key ? 700 : 500, cursor: "pointer",
      }}>{label}</button>
  );

  // v8.7.8: 모듈 순서 편집 (admin → PageGear)
  const [modDraft, setModDraft] = useState(null);
  const saveModuleOrder = () => {
    if (!Array.isArray(modDraft)) return;
    postJson("/api/informs/config", { modules: modDraft })
      .then(d => { setConstants(c => ({ ...c, modules: d.config?.modules || modDraft })); setModDraft(null); })
      .catch(e => alert("모듈 순서 저장 실패: " + (e.message || e)));
  };
  const moveMod = (i, delta) => {
    if (!Array.isArray(modDraft)) return;
    const j = i + delta; if (j < 0 || j >= modDraft.length) return;
    const n = modDraft.slice(); [n[i], n[j]] = [n[j], n[i]]; setModDraft(n);
  };

  return (
    <div style={{ display: "flex", height: "calc(100vh - 48px)", background: "var(--bg-primary)", color: "var(--text-primary)", position: "relative" }}>
      <PageGear title="인폼 설정" canEdit={isAdmin} position="bottom-left">
        <div style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 8 }}>
          모듈 표시 순서를 관리합니다 (Lot 뷰에서 이 순서대로 그룹핑).
        </div>
        {!modDraft && (
          <button onClick={() => setModDraft([...(constants.modules || [])])} disabled={!isAdmin}
            style={{ padding: "8px 14px", borderRadius: 6, border: "1px solid var(--accent)", background: "transparent", color: "var(--accent)", fontSize: 12, cursor: "pointer", fontWeight: 600 }}>
            📋 모듈 순서 편집 ({(constants.modules || []).length})
          </button>
        )}
        {modDraft && (
          <div>
            <div style={{ fontSize: 10, color: "var(--text-secondary)", marginBottom: 6 }}>드래그 대신 ↑↓ 버튼으로 순서 조정</div>
            <div style={{ maxHeight: 260, overflowY: "auto", border: "1px solid var(--border)", borderRadius: 4 }}>
              {modDraft.map((m, i) => (
                <div key={m + i} style={{ display: "flex", alignItems: "center", gap: 6, padding: "4px 8px", borderBottom: "1px solid var(--border)", fontSize: 11, fontFamily: "monospace" }}>
                  <span style={{ width: 20, color: "var(--text-secondary)" }}>{i + 1}</span>
                  <span style={{ flex: 1 }}>{m}</span>
                  <button onClick={() => moveMod(i, -1)} style={{ padding: "1px 6px", fontSize: 10, border: "1px solid var(--border)", background: "transparent", color: "var(--text-primary)", borderRadius: 3, cursor: "pointer" }}>↑</button>
                  <button onClick={() => moveMod(i, 1)} style={{ padding: "1px 6px", fontSize: 10, border: "1px solid var(--border)", background: "transparent", color: "var(--text-primary)", borderRadius: 3, cursor: "pointer" }}>↓</button>
                  <button onClick={() => setModDraft(modDraft.filter((_, j) => j !== i))} style={{ padding: "1px 6px", fontSize: 10, border: "1px solid #ef4444", background: "transparent", color: "#ef4444", borderRadius: 3, cursor: "pointer" }}>×</button>
                </div>
              ))}
            </div>
            <div style={{ display: "flex", gap: 6, marginTop: 8, flexWrap: "wrap" }}>
              <input id="__mod_add_input" placeholder="새 모듈 이름" style={{ flex: 1, minWidth: 120, padding: "4px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 11 }}
                onKeyDown={e => { if (e.key === "Enter") { const v = e.target.value.trim(); if (v && !modDraft.includes(v)) { setModDraft([...modDraft, v]); e.target.value = ""; } } }} />
              <button onClick={saveModuleOrder} style={{ padding: "4px 10px", borderRadius: 4, border: "none", background: "var(--accent)", color: "#fff", fontSize: 11, fontWeight: 600, cursor: "pointer" }}>저장</button>
              <button onClick={() => setModDraft(null)} style={{ padding: "4px 10px", borderRadius: 4, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", fontSize: 11, cursor: "pointer" }}>취소</button>
            </div>
          </div>
        )}
      </PageGear>
      {/* Sidebar */}
      <div style={{ width: 340, minWidth: 300, borderRight: "1px solid var(--border)", background: "var(--bg-secondary)", display: "flex", flexDirection: "column" }}>
        <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span style={{ fontSize: 14, fontWeight: 700, fontFamily: "monospace", color: "var(--accent)" }}>{">"} 인폼 로그</span>
          <button onClick={() => setCreating(true)} style={{ padding: "4px 12px", borderRadius: 5, border: "none", background: "var(--accent)", color: "#fff", fontSize: 11, fontWeight: 600, cursor: "pointer" }}>+ 신규</button>
        </div>

        <div style={{ padding: "8px 10px", borderBottom: "1px solid var(--border)", display: "flex", flexWrap: "wrap", gap: 4 }}>
          {modeButton("all",     "전체",    "최근 루트 인폼 (역할 필터 적용)")}
          {modeButton("product", "제품",  "제품 → Lot → Wafer drill-down")}
          {modeButton("lot",     "Lot",    "LOT 으로 전체 인폼 검색")}
          {modeButton("gantt",   "간트",    "데드라인 간트 차트")}
          {/* v8.7.8: wafer 모드 제거 — product/lot drill-down 으로 통합. */}
        </div>

        <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>
          <input value={search} onChange={e => setSearch(e.target.value)}
            placeholder={mode === "lot" ? "lot_id 검색..."
                       : mode === "product" ? "product 검색..."
                       : "검색 (해당 모드에서는 미사용)"}
            style={{ width: "100%", padding: "6px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12, outline: "none", boxSizing: "border-box" }} />
        </div>

        {/* 담당 모듈 요약 */}
        {!myMods.all_rounder && (
          <div style={{ padding: "6px 12px", borderBottom: "1px solid var(--border)", fontSize: 10, color: "var(--text-secondary)" }}>
            내 담당: {(myMods.modules || []).length === 0 ? "없음 (Admin→그룹에서 설정)"
                                                          : (myMods.modules || []).join(", ")}
          </div>
        )}
        {myMods.all_rounder && (
          <div style={{ padding: "6px 12px", borderBottom: "1px solid var(--border)", fontSize: 10, color: "#22c55e" }}>
            ● 전체 담당 (admin) — 모든 모듈 열람
          </div>
        )}

        <div style={{ flex: 1, overflowY: "auto" }}>
          {(mode === "all" || mode === "gantt") && (
            <div style={{ padding: 16, textAlign: "center", color: "var(--text-secondary)", fontSize: 11 }}>
              메인 패널에서 목록을 확인하세요
            </div>
          )}
          {(mode === "wafer" || mode === "lot" || mode === "product") && sidebarItems.length === 0 && (
            <div style={{ padding: 20, textAlign: "center", color: "var(--text-secondary)", fontSize: 11 }}>기록 없음</div>
          )}
          {(mode === "wafer" || mode === "lot" || mode === "product") && sidebarItems.map(it => (
            <div key={it.key} onClick={() => setSelected(it.key)}
              style={{ padding: "10px 14px", borderBottom: "1px solid var(--border)", cursor: "pointer",
                       background: selectedKey === it.key ? "var(--bg-hover)" : "transparent" }}>
              <div style={{ fontSize: 12, fontWeight: 600, fontFamily: "monospace" }}>{it.label}</div>
              <div style={{ fontSize: 10, color: "var(--text-secondary)", marginTop: 2 }}>{it.sub}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Main */}
      <div style={{ flex: 1, overflowY: "auto", padding: 24 }}>
        {creating && (
          <div style={{ background: "var(--bg-secondary)", borderRadius: 10, border: "1px solid var(--border)", padding: 18, marginBottom: 18 }}>
            <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 10 }}>새 인폼</div>
            <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr 1fr", gap: 8, marginBottom: 8 }}>
              <input value={form.wafer_id} onChange={e => setForm({ ...form, wafer_id: e.target.value })}
                placeholder="wafer_id (예: A0001B.1-W03)"
                style={{ padding: "8px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12, fontFamily: "monospace" }} />
              <input value={form.lot_id} onChange={e => setForm({ ...form, lot_id: e.target.value })}
                placeholder="lot_id (예: A0001B.1)"
                style={{ padding: "8px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12, fontFamily: "monospace" }} />
              <input value={form.product} onChange={e => setForm({ ...form, product: e.target.value })}
                placeholder="product (예: PROD_A)"
                style={{ padding: "8px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12, fontFamily: "monospace" }} />
            </div>
            <div style={{ display: "flex", gap: 8, marginBottom: 8, flexWrap: "wrap" }}>
              <select value={form.module} onChange={e => setForm({ ...form, module: e.target.value })}
                style={{ padding: "8px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12 }}>
                <option value="">-- 모듈 --</option>{constants.modules.map(m => <option key={m} value={m}>{m}</option>)}
              </select>
              <select value={form.reason} onChange={e => setForm({ ...form, reason: e.target.value })}
                style={{ padding: "8px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12 }}>
                <option value="">-- 사유 --</option>{constants.reasons.map(r => <option key={r} value={r}>{r}</option>)}
              </select>
              <label style={{ fontSize: 11, color: "var(--text-secondary)", display: "inline-flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
                <input type="checkbox" checked={form.attach_split}
                  onChange={e => setForm({ ...form, attach_split: e.target.checked })} />
                SplitTable 변경요청 포함
              </label>
              <label style={{ fontSize: 11, color: "var(--text-secondary)", display: "inline-flex", alignItems: "center", gap: 6 }}>
                🗓 데드라인
                <input type="date" value={form.deadline}
                  onChange={e => setForm({ ...form, deadline: e.target.value })}
                  style={{ padding: "6px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 11 }} />
                {form.deadline && <span onClick={() => setForm({ ...form, deadline: "" })}
                  style={{ cursor: "pointer", color: "#ef4444", fontSize: 11 }}>×</span>}
              </label>
            </div>
            <textarea value={form.text} onChange={e => setForm({ ...form, text: e.target.value })} rows={3}
              placeholder="인폼 내용 (배경, 영향, 조치 요청 등)"
              style={{ width: "100%", padding: 10, borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12, resize: "vertical", boxSizing: "border-box" }} />
            <div style={{ marginTop: 8, display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
              <label style={{ fontSize: 11, color: "var(--text-secondary)", cursor: "pointer", padding: "4px 10px", borderRadius: 4, border: "1px dashed var(--border)" }}>
                📎 이미지 첨부
                <input type="file" accept="image/*" multiple style={{ display: "none" }}
                  onChange={e => { uploadMain(e.target.files); e.target.value = ""; }} />
              </label>
              {uploadingMain && <span style={{ fontSize: 10, color: "var(--accent)" }}>업로드중…</span>}
              <button type="button" onClick={embedFromSplitTable}
                disabled={embedFetching || !form.product}
                title={!form.product ? "product 를 먼저 입력하세요" : "현재 product SplitTable 이력을 본문에 첨부"}
                style={{ padding: "4px 10px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 11, cursor: (embedFetching || !form.product) ? "default" : "pointer", opacity: (!form.product) ? 0.5 : 1 }}>
                🔗 SplitTable 에서 가져오기
              </button>
              {embedFetching && <span style={{ fontSize: 10, color: "var(--accent)" }}>로딩…</span>}
              {form.attach_embed && form.embed.rows.length > 0 && (
                <span style={{ fontSize: 10, color: "#16a34a", fontWeight: 600 }}>
                  embed: {form.embed.rows.length} rows
                  <button type="button" onClick={() => setForm(f => ({ ...f, attach_embed: false, embed: { source: "", columns: [], rows: [], note: "" } }))}
                    style={{ marginLeft: 6, border: "none", background: "transparent", color: "#ef4444", cursor: "pointer" }}>×</button>
                </span>
              )}
            </div>
            {createImages.length > 0 && (
              <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap", gap: 6 }}>
                {createImages.map((im, i) => (
                  <span key={i} style={{ fontSize: 10, padding: "2px 6px", borderRadius: 3, background: "var(--bg-primary)", border: "1px solid var(--border)", display: "inline-flex", alignItems: "center", gap: 4 }}>
                    <img src={authSrc(im.url)} alt="" style={{ width: 28, height: 28, objectFit: "cover", borderRadius: 2 }} />
                    <span style={{ fontFamily: "monospace" }}>{im.filename}</span>
                    <button onClick={() => setCreateImages(createImages.filter((_, j) => j !== i))}
                      style={{ border: "none", background: "transparent", color: "#ef4444", cursor: "pointer", padding: 0 }}>×</button>
                  </span>
                ))}
              </div>
            )}
            {form.attach_embed && form.embed && form.embed.rows.length > 0 && (
              <div style={{ marginTop: 6 }}>
                <EmbedTableView embed={form.embed} />
              </div>
            )}
            {form.attach_split && (
              <div style={{ marginTop: 8, padding: 10, background: "var(--bg-primary)", borderRadius: 5, border: "1px dashed var(--border)" }}>
                <div style={{ fontSize: 10, color: "var(--text-secondary)", marginBottom: 4, fontWeight: 600 }}>Split Table 변경 (예: KNOB A → B)</div>
                <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr 1fr", gap: 6 }}>
                  <input value={form.split.column} onChange={e => setForm({ ...form, split: { ...form.split, column: e.target.value } })}
                    placeholder="column (예: KNOB/GATE_PPID)"
                    style={{ padding: "6px 8px", borderRadius: 3, border: "1px solid var(--border)", background: "var(--bg-secondary)", color: "var(--text-primary)", fontSize: 11, fontFamily: "monospace" }} />
                  <input value={form.split.old_value} onChange={e => setForm({ ...form, split: { ...form.split, old_value: e.target.value } })}
                    placeholder="old"
                    style={{ padding: "6px 8px", borderRadius: 3, border: "1px solid var(--border)", background: "var(--bg-secondary)", color: "var(--text-primary)", fontSize: 11, fontFamily: "monospace" }} />
                  <input value={form.split.new_value} onChange={e => setForm({ ...form, split: { ...form.split, new_value: e.target.value } })}
                    placeholder="new"
                    style={{ padding: "6px 8px", borderRadius: 3, border: "1px solid var(--border)", background: "var(--bg-secondary)", color: "var(--text-primary)", fontSize: 11, fontFamily: "monospace" }} />
                </div>
              </div>
            )}
            <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
              <button onClick={create} style={{ padding: "8px 20px", borderRadius: 6, border: "none", background: "var(--accent)", color: "#fff", fontWeight: 600, cursor: "pointer" }}>등록</button>
              <button onClick={() => { setCreating(false); setMsg(""); }} style={{ padding: "8px 16px", borderRadius: 6, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", cursor: "pointer" }}>취소</button>
              {msg && <span style={{ fontSize: 11, color: "#ef4444", alignSelf: "center" }}>{msg}</span>}
            </div>
          </div>
        )}

        {/* 대시보드식 모듈 필터 (admin/all-rounder 에서 활용) */}
        {(isAdmin || myMods.all_rounder || (myMods.modules || []).length > 1) && (mode === "all" || mode === "mine" || mode === "product" || mode === "lot") && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 12, alignItems: "center" }}>
            <span style={{ fontSize: 10, color: "var(--text-secondary)", fontWeight: 600, marginRight: 4 }}>모듈 필터:</span>
            {constants.modules.map(m => {
              const on = moduleFilter.includes(m);
              return (
                <span key={m} onClick={() => setModuleFilter(on ? moduleFilter.filter(x => x !== m) : [...moduleFilter, m])}
                  style={{
                    padding: "3px 10px", borderRadius: 999, fontSize: 10, fontWeight: on ? 700 : 500,
                    cursor: "pointer",
                    background: on ? "var(--accent)22" : "var(--bg-secondary)",
                    color: on ? "var(--accent)" : "var(--text-secondary)",
                    border: "1px solid " + (on ? "var(--accent)" : "var(--border)"),
                  }}>{m}</span>
              );
            })}
            {moduleFilter.length > 0 && (
              <span onClick={() => setModuleFilter([])}
                style={{ fontSize: 10, color: "#ef4444", cursor: "pointer", marginLeft: 4 }}>필터 해제</span>
            )}
          </div>
        )}

        {/* 메인 컨텐츠 */}
        {mode === "gantt" && (
          <>
            <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 10, color: "var(--text-secondary)" }}>📊 간트 차트 — 데드라인 타임라인</div>
            <GanttView
              roots={applyModFilter(rootsSorted)}
              onOpen={(r) => { setSelectedWafer(r.wafer_id); setMode("wafer"); }}
            />
          </>
        )}

        {mode === "all" && (
          <>
            <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 10, color: "var(--text-secondary)" }}>최근 루트 인폼</div>
            {applyModFilter(rootsSorted).length === 0 && <div style={{ padding: 40, textAlign: "center", color: "var(--text-secondary)" }}>인폼 없음.</div>}
            {applyModFilter(rootsSorted).map(r => (
              <CompactRow key={r.id} root={r} onOpen={() => { setSelectedWafer(r.wafer_id); setMode("wafer"); }} />
            ))}
          </>
        )}

        {mode === "mine" && (
          <>
            <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 4 }}>
              내 모듈 인폼 {myMods.all_rounder
                ? <span style={{ fontSize: 11, color: "#22c55e", marginLeft: 6 }}>(전체 담당)</span>
                : <span style={{ fontSize: 11, color: "var(--text-secondary)", marginLeft: 6 }}>({(myMods.modules || []).join(", ") || "모듈 미배정"})</span>}
            </div>
            <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 10 }}>
              나의 그룹 담당 모듈에 해당하는 루트 인폼만 노출됩니다. {isAdmin ? "admin 은 모듈 필터 칩으로 단일 모듈을 좁혀 볼 수 있습니다." : ""}
            </div>
            {applyModFilter(rootsSorted).length === 0 && <div style={{ padding: 40, textAlign: "center", color: "var(--text-secondary)" }}>해당 없음.</div>}
            {applyModFilter(rootsSorted).map(r => (
              <CompactRow key={r.id} root={r}
                onOpen={() => { setSelectedWafer(r.wafer_id); setMode("wafer"); }} />
            ))}
          </>
        )}

        {mode === "product" && selectedProduct && (
          <>
            <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 6, fontFamily: "monospace" }}>
              📦 {selectedProduct}
              <span style={{ fontSize: 11, fontWeight: 500, marginLeft: 8, color: "var(--text-secondary)" }}>
                — 이 제품 인폼 {rootsSorted.length}건 · drill-down 가능
              </span>
            </div>
            {/* v8.7.6: 제품 선택 시 Lot 리스트 drill-down */}
            {(() => {
              const lotMap = {};
              for (const r of applyModFilter(rootsSorted)) {
                const lid = r.lot_id || "(lot 미지정)";
                (lotMap[lid] = lotMap[lid] || []).push(r);
              }
              const lotKeys = Object.keys(lotMap).sort();
              if (lotKeys.length === 0) {
                return <div style={{ padding: 20, color: "var(--text-secondary)", fontSize: 11 }}>해당 제품 인폼 없음.</div>;
              }
              return lotKeys.map(lid => {
                const lotRoots = lotMap[lid];
                const waferSet = Array.from(new Set(lotRoots.map(r => r.wafer_id).filter(Boolean))).sort();
                return (
                  <div key={lid} style={{ marginBottom: 12, padding: 10, borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg-card)" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                      <span style={{ fontSize: 12, fontWeight: 700, fontFamily: "monospace" }}>🧾 {lid}</span>
                      <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>· {lotRoots.length} 루트 · {waferSet.length} wafer</span>
                      <span style={{ flex: 1 }} />
                      <span onClick={() => { setSelectedLot(lid); setMode("lot"); }}
                            style={{ fontSize: 10, color: "var(--accent)", textDecoration: "underline", cursor: "pointer" }}>Lot 전용 뷰 ↗</span>
                    </div>
                    {waferSet.length > 0 && (
                      <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginBottom: 6 }}>
                        {waferSet.slice(0, 30).map(w => (
                          <span key={w} onClick={() => { setSelectedWafer(w); setMode("wafer"); }}
                                style={{ padding: "2px 8px", borderRadius: 999, fontSize: 10, fontFamily: "monospace", cursor: "pointer",
                                         background: "var(--accent-glow)", color: "var(--accent)", border: "1px solid var(--accent)" }}>
                            {w}
                          </span>
                        ))}
                        {waferSet.length > 30 && <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>+{waferSet.length - 30}</span>}
                      </div>
                    )}
                    {lotRoots.slice(0, 5).map(r => (
                      <CompactRow key={r.id} root={r}
                        onOpen={() => { setSelectedWafer(r.wafer_id); setMode("wafer"); }} />
                    ))}
                  </div>
                );
              });
            })()}
          </>
        )}

        {mode === "lot" && selectedLot && (
          <>
            <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 6, fontFamily: "monospace" }}>
              🧾 Lot: {selectedLot}
              <span style={{ fontSize: 11, fontWeight: 500, marginLeft: 8, color: "var(--text-secondary)" }}>
                — wafer {lotWafers.length}개 · inform {thread.length}건
              </span>
            </div>
            {lotWafers.length > 0 && (
              <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 10, fontFamily: "monospace" }}>
                연결 wafer: {lotWafers.join(", ")}
              </div>
            )}
            <LotModuleSummary thread={thread} modules={constants.modules} />
            <PlanSummaryCard thread={thread} />
            {(() => {
              const grouped = {};
              for (const r of applyModFilter(rootsSorted)) {
                const m = r.module || "(미지정)";
                (grouped[m] = grouped[m] || []).push(r);
              }
              const order = [...(constants.modules || []), "(미지정)"];
              const modKeys = Object.keys(grouped).sort((a, b) => {
                const ia = order.indexOf(a); const ib = order.indexOf(b);
                return (ia < 0 ? 999 : ia) - (ib < 0 ? 999 : ib);
              });
              if (modKeys.length === 0) return <div style={{ padding: 40, textAlign: "center", color: "var(--text-secondary)" }}>해당 없음.</div>;
              return modKeys.map(mk => (
                <div key={mk} style={{ marginBottom: 22, padding: 10, borderRadius: 8, background: "var(--bg-card)", border: "1px solid var(--border)" }}>
                  <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8, fontFamily: "monospace", color: "var(--accent)" }}>
                    ▣ {mk} <span style={{ fontSize: 10, color: "var(--text-secondary)", fontWeight: 500, marginLeft: 6 }}>{grouped[mk].length}건</span>
                  </div>
                  {grouped[mk].map(r => (
                    <div key={r.id} style={{ marginBottom: 14, paddingBottom: 10, borderBottom: "1px dashed var(--border)" }}>
                      <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 4, fontFamily: "monospace" }}>
                        wafer: <b style={{ color: "var(--text-primary)" }}>{r.wafer_id}</b>
                      </div>
                      <RootHeader root={r} onChangeStatus={changeStatus} onChangeDeadline={changeDeadline} user={user} />
                      <ThreadNode node={r} childrenByParent={childrenByParent}
                        onReply={reply} onDelete={del} onToggleCheck={toggleCheck}
                        user={user} depth={0} constants={constants} />
                    </div>
                  ))}
                </div>
              ));
            })()}
          </>
        )}

        {mode === "wafer" && selectedWafer && (
          <div>
            <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 12, fontFamily: "monospace" }}>{selectedWafer}</div>
            <PlanSummaryCard thread={thread} />
            {rootsSorted.length === 0 && <div style={{ padding: 40, textAlign: "center", color: "var(--text-secondary)" }}>아직 인폼 없음.</div>}
            {rootsSorted.map(r => (
              <div key={r.id} style={{ marginBottom: 16 }}>
                <RootHeader root={r} onChangeStatus={changeStatus} onChangeDeadline={changeDeadline} user={user} />
                <ThreadNode node={r} childrenByParent={childrenByParent}
                  onReply={reply} onDelete={del} onToggleCheck={toggleCheck}
                  user={user} depth={0} constants={constants} />
              </div>
            ))}
          </div>
        )}

        {mode !== "all" && mode !== "mine" && mode !== "gantt" && !selectedKey && !creating && (
          <div style={{ padding: 60, textAlign: "center", color: "var(--text-secondary)" }}>
            좌측에서 항목을 선택하거나 <span onClick={() => setCreating(true)} style={{ color: "var(--accent)", cursor: "pointer" }}>+ 신규 인폼</span> 을 등록하세요.
          </div>
        )}
      </div>
    </div>
  );
}

/* v8.7.1 — Inform Gantt View (데드라인 시각화) */
function GanttView({ roots, onOpen }) {
  const withRange = useMemo(() => {
    const today = new Date(); today.setHours(0, 0, 0, 0);
    return (roots || []).map(r => {
      const start = new Date((r.created_at || today.toISOString()).slice(0, 10));
      const end = r.deadline ? new Date(r.deadline) :
        new Date(today.getTime() + 7 * 86400000);
      return { r, start, end, synthetic: !r.deadline };
    });
  }, [roots]);

  if (!withRange.length) {
    return <div style={{ padding: 60, textAlign: "center", color: "var(--text-secondary)" }}>표시할 인폼 없음.</div>;
  }

  const today = new Date(); today.setHours(0, 0, 0, 0);
  const lo = new Date(Math.min(...withRange.map(x => x.start.getTime()), today.getTime()));
  const hi = new Date(Math.max(...withRange.map(x => x.end.getTime()), today.getTime() + 7 * 86400000));
  lo.setDate(lo.getDate() - 2);
  hi.setDate(hi.getDate() + 2);
  const totalDays = Math.max(1, Math.round((hi - lo) / 86400000));
  const rowH = 26;
  const barH = 18;
  const labelW = 260;
  const dayW = Math.max(6, Math.min(30, 900 / totalDays));
  const chartW = totalDays * dayW;
  const height = withRange.length * rowH + 40;
  const svgW = labelW + chartW + 20;

  const xForDate = (d) => labelW + ((d - lo) / 86400000) * dayW;

  // Month ticks
  const ticks = [];
  let t = new Date(lo); t.setDate(1);
  while (t <= hi) {
    ticks.push(new Date(t));
    t.setMonth(t.getMonth() + 1);
  }

  const todayX = xForDate(today);

  return (
    <div style={{ overflowX: "auto", border: "1px solid var(--border)", borderRadius: 8, background: "var(--bg-secondary)", padding: 10 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 6, flexWrap: "wrap" }}>
        <span style={{ fontSize: 13, fontWeight: 700, color: "var(--accent)" }}>📊 Inform 간트 차트</span>
        <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>{withRange.length}건 · 기간 {lo.toISOString().slice(0, 10)} ~ {hi.toISOString().slice(0, 10)}</span>
        <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>· 바 클릭 → wafer 상세</span>
        <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>· 점선 바 = 데드라인 미설정 (기본 +7일)</span>
      </div>
      <svg width={svgW} height={height} style={{ display: "block" }}>
        {/* Month gridlines */}
        {ticks.map((tk, i) => {
          const x = xForDate(tk);
          return (
            <g key={i}>
              <line x1={x} x2={x} y1={0} y2={height - 20} stroke="var(--border)" strokeDasharray="2,3" />
              <text x={x + 2} y={height - 6} fontSize={9} fill="var(--text-secondary)" fontFamily="monospace">
                {tk.getFullYear()}-{String(tk.getMonth() + 1).padStart(2, "0")}
              </text>
            </g>
          );
        })}
        {/* Today line */}
        <line x1={todayX} x2={todayX} y1={0} y2={height - 20} stroke="var(--accent)" strokeWidth={2} />
        <text x={todayX + 3} y={12} fontSize={10} fill="var(--accent)" fontWeight={700} fontFamily="monospace">TODAY</text>

        {/* Rows */}
        {withRange.map((item, i) => {
          const y = 20 + i * rowH;
          const xs = xForDate(item.start);
          const xe = Math.max(xs + 4, xForDate(item.end));
          const st = item.r.flow_status || "received";
          const stColor = (STATUS_META[st] || {}).color || "#64748b";
          const mc = moduleColor(item.r.module);
          const overdue = item.r.deadline && item.r.deadline < today.toISOString().slice(0, 10) && st !== "completed";
          return (
            <g key={item.r.id} style={{ cursor: "pointer" }} onClick={() => onOpen && onOpen(item.r)}>
              <rect x={0} y={y - 2} width={labelW - 6} height={rowH - 4} fill={i % 2 ? "var(--bg-primary)" : "transparent"} />
              <circle cx={8} cy={y + barH / 2} r={5} fill={mc} />
              <text x={20} y={y + barH / 2 + 4} fontSize={11} fill="var(--text-primary)" fontFamily="monospace">
                {(item.r.module || "-")} · {(item.r.wafer_id || "").slice(0, 18)}
              </text>
              <text x={20} y={y + barH / 2 - 6} fontSize={8} fill="var(--text-secondary)">
                [{item.r.reason || "-"}] {(item.r.text || "").slice(0, 36)}
              </text>
              <rect x={xs} y={y} width={xe - xs} height={barH} rx={4} ry={4}
                fill={overdue ? "#ef4444" : stColor} opacity={item.synthetic ? 0.35 : 0.75}
                stroke={mc} strokeWidth={1.5}
                strokeDasharray={item.synthetic ? "4,3" : "0"} />
              <text x={xs + 4} y={y + barH / 2 + 4} fontSize={9} fill="#fff" fontWeight={700}>
                {(STATUS_META[st] || {}).label || st}
              </text>
              {item.r.deadline && (
                <text x={xe + 4} y={y + barH / 2 + 4} fontSize={9} fill={overdue ? "#ef4444" : "var(--text-secondary)"} fontFamily="monospace">
                  🗓 {item.r.deadline}{overdue ? " ⚠" : ""}
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/* 요약 카드 (all/mine/product 모드에서 루트 리스트용) */
function CompactRow({ root, onOpen }) {
  const mc = moduleColor(root.module);
  return (
    <div onClick={onOpen}
      style={{ padding: "10px 14px", marginBottom: 8, borderRadius: 8,
               border: "1px solid var(--border)", background: "var(--bg-secondary)",
               borderLeft: "5px solid " + mc,
               cursor: "pointer" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <StatusBadge status={root.flow_status || "received"} />
        {root.module && <span style={{ fontSize: 10, padding: "2px 8px", borderRadius: 999, background: mc + "22", color: mc, fontWeight: 700, border: "1px solid " + mc + "55" }}>{root.module}</span>}
        <CheckPill node={root} />
        <AutoGenPill node={root} />
        {(root.images && root.images.length > 0) && <span title="이미지 첨부" style={{ fontSize: 10 }}>📎{root.images.length}</span>}
        {root.embed_table && <span title="임베드" style={{ fontSize: 10 }}>🔗</span>}
        {root.deadline && (
          <span title={"데드라인 " + root.deadline} style={{
            fontSize: 10, padding: "2px 8px", borderRadius: 999,
            background: (root.deadline < new Date().toISOString().slice(0,10) ? "#ef4444" : "#3b82f6") + "22",
            color: root.deadline < new Date().toISOString().slice(0,10) ? "#ef4444" : "#3b82f6",
            fontFamily: "monospace", fontWeight: 700,
          }}>🗓 {root.deadline}</span>
        )}
        <span style={{ fontSize: 11, fontFamily: "monospace", fontWeight: 600 }}>{root.wafer_id}</span>
        {root.lot_id && <span style={{ fontSize: 10, color: "var(--text-secondary)", fontFamily: "monospace" }}>· {root.lot_id}</span>}
        {root.product && <span style={{ fontSize: 10, color: "var(--text-secondary)", fontFamily: "monospace" }}>· {root.product}</span>}
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>{(root.created_at || "").replace("T", " ").slice(0, 16)}</span>
        <span style={{ fontSize: 10, fontWeight: 600 }}>{root.author}</span>
      </div>
      <div style={{ fontSize: 12, marginTop: 4, whiteSpace: "pre-wrap", opacity: 0.95,
                    display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
        {root.reason && <span style={{ color: mc, fontWeight: 700, marginRight: 6 }}>[{root.reason}]</span>}
        {root.text}
      </div>
    </div>
  );
}
