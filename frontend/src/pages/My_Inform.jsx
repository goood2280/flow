/* My_Inform.jsx v8.7.0 — 모듈 인폼 시스템 (역할 뷰 + 체크 + flow 상태 + SplitTable 연동).
 *
 * 보안: auth 미들웨어 + 세션 토큰 그대로. sf() 가 X-Session-Token 자동 주입.
 * 삭제 정책: 작성자 본인만 (관리자도 불가) — 서버에서도 동일하게 강제됨.
 */
import { useEffect, useMemo, useState } from "react";
import { sf } from "../lib/api";

const API = "/api/informs";

const STATUS_META = {
  received:    { label: "접수",   color: "#64748b", dot: "○" },
  reviewing:   { label: "검토중", color: "#3b82f6", dot: "◐" },
  in_progress: { label: "진행중", color: "#f59e0b", dot: "◑" },
  completed:   { label: "완료",   color: "#22c55e", dot: "●" },
};
const STATUS_ORDER = ["received", "reviewing", "in_progress", "completed"];

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
        <a key={i} href={im.url} target="_blank" rel="noreferrer"
          style={{ display: "block", border: "1px solid var(--border)", borderRadius: 4, padding: 2, background: "var(--bg-primary)" }}>
          <img src={im.url} alt={im.filename}
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
          {node.module && <span style={{ fontSize: 10, padding: "2px 8px", borderRadius: 999, background: "var(--accent)22", color: "var(--accent)", fontWeight: 700 }}>{node.module}</span>}
          {node.reason && <span style={{ fontSize: 10, padding: "2px 8px", borderRadius: 999, background: "var(--bg-hover)", color: "var(--text-secondary)" }}>{node.reason}</span>}
          <CheckPill node={node} />
          <AutoGenPill node={node} />
          <span style={{ fontSize: 11, fontWeight: 600 }}>{node.author}</span>
          <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>{(node.created_at || "").replace("T", " ")}</span>
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
                  <img src={im.url} alt="" style={{ width: 24, height: 24, objectFit: "cover", borderRadius: 2 }} />
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

/* 루트 인폼 머리에 붙는 상태 패널 (flow 진행 + 이력) */
function RootHeader({ root, onChangeStatus }) {
  const [note, setNote] = useState("");
  const [openHist, setOpenHist] = useState(false);
  const hist = root.status_history || [];
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
        <input value={note} onChange={e => setNote(e.target.value)} placeholder="상태변경 메모 (optional)"
          style={{ padding: "4px 8px", borderRadius: 4, border: "1px solid var(--border)",
                   background: "var(--bg-primary)", color: "var(--text-primary)",
                   fontSize: 11, width: 220 }} />
        <span onClick={() => setOpenHist(!openHist)}
          style={{ fontSize: 10, color: "var(--accent)", cursor: "pointer" }}>
          이력 {hist.length > 0 && `(${hist.length})`}
        </span>
      </div>
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
    } else if (mode === "all") {
      sf(API + "/recent?limit=100").then(d => { setThread(d.informs || []); setLotWafers([]); })
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
      sf(API + "/recent?limit=100").then(d => setThread(d.informs || []));
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
      images: createImages,
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

  return (
    <div style={{ display: "flex", height: "calc(100vh - 48px)", background: "var(--bg-primary)", color: "var(--text-primary)" }}>
      {/* Sidebar */}
      <div style={{ width: 340, minWidth: 300, borderRight: "1px solid var(--border)", background: "var(--bg-secondary)", display: "flex", flexDirection: "column" }}>
        <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span style={{ fontSize: 14, fontWeight: 700, fontFamily: "monospace", color: "var(--accent)" }}>{">"} 인폼 로그</span>
          <button onClick={() => setCreating(true)} style={{ padding: "4px 12px", borderRadius: 5, border: "none", background: "var(--accent)", color: "#fff", fontSize: 11, fontWeight: 600, cursor: "pointer" }}>+ 신규</button>
        </div>

        <div style={{ padding: "8px 10px", borderBottom: "1px solid var(--border)", display: "flex", flexWrap: "wrap", gap: 4 }}>
          {modeButton("all",     "전체",    "최근 루트 인폼 (역할 필터 적용)")}
          {modeButton("mine",    "내 모듈", myMods.all_rounder ? "전체 담당 (admin)" : (myMods.modules || []).join(",") || "모듈 미배정")}
          {modeButton("product", "제품별",  "제품별 의뢰 목록")}
          {modeButton("lot",     "랏별",    "LOT 으로 전체 인폼 검색")}
          {modeButton("wafer",   "wafer",   "wafer 별 스레드")}
        </div>

        <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>
          <input value={search} onChange={e => setSearch(e.target.value)}
            placeholder={mode === "lot" ? "lot_id 검색..."
                       : mode === "product" ? "product 검색..."
                       : mode === "wafer" ? "wafer_id/lot/product 검색..."
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
          {(mode === "all" || mode === "mine") && (
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
                    <img src={im.url} alt="" style={{ width: 28, height: 28, objectFit: "cover", borderRadius: 2 }} />
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
                — 이 제품 인폼 {rootsSorted.length}건
              </span>
            </div>
            {applyModFilter(rootsSorted).map(r => (
              <CompactRow key={r.id} root={r}
                onOpen={() => { setSelectedWafer(r.wafer_id); setMode("wafer"); }} />
            ))}
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
            <PlanSummaryCard thread={thread} />
            {applyModFilter(rootsSorted).length === 0 && <div style={{ padding: 40, textAlign: "center", color: "var(--text-secondary)" }}>해당 없음.</div>}
            {applyModFilter(rootsSorted).map(r => (
              <div key={r.id} style={{ marginBottom: 18, paddingBottom: 14, borderBottom: "1px dashed var(--border)" }}>
                <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 4, fontFamily: "monospace" }}>
                  wafer: <b style={{ color: "var(--text-primary)" }}>{r.wafer_id}</b>
                </div>
                <RootHeader root={r} onChangeStatus={changeStatus} />
                <ThreadNode node={r} childrenByParent={childrenByParent}
                  onReply={reply} onDelete={del} onToggleCheck={toggleCheck}
                  user={user} depth={0} constants={constants} />
              </div>
            ))}
          </>
        )}

        {mode === "wafer" && selectedWafer && (
          <div>
            <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 12, fontFamily: "monospace" }}>{selectedWafer}</div>
            <PlanSummaryCard thread={thread} />
            {rootsSorted.length === 0 && <div style={{ padding: 40, textAlign: "center", color: "var(--text-secondary)" }}>아직 인폼 없음.</div>}
            {rootsSorted.map(r => (
              <div key={r.id} style={{ marginBottom: 16 }}>
                <RootHeader root={r} onChangeStatus={changeStatus} />
                <ThreadNode node={r} childrenByParent={childrenByParent}
                  onReply={reply} onDelete={del} onToggleCheck={toggleCheck}
                  user={user} depth={0} constants={constants} />
              </div>
            ))}
          </div>
        )}

        {mode !== "all" && mode !== "mine" && !selectedKey && !creating && (
          <div style={{ padding: 60, textAlign: "center", color: "var(--text-secondary)" }}>
            좌측에서 항목을 선택하거나 <span onClick={() => setCreating(true)} style={{ color: "var(--accent)", cursor: "pointer" }}>+ 신규 인폼</span> 을 등록하세요.
          </div>
        )}
      </div>
    </div>
  );
}

/* 요약 카드 (all/mine/product 모드에서 루트 리스트용) */
function CompactRow({ root, onOpen }) {
  return (
    <div onClick={onOpen}
      style={{ padding: "10px 14px", marginBottom: 8, borderRadius: 8,
               border: "1px solid var(--border)", background: "var(--bg-secondary)",
               cursor: "pointer" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <StatusBadge status={root.flow_status || "received"} />
        {root.module && <span style={{ fontSize: 10, padding: "2px 8px", borderRadius: 999, background: "var(--accent)22", color: "var(--accent)", fontWeight: 700 }}>{root.module}</span>}
        {root.reason && <span style={{ fontSize: 10, padding: "2px 8px", borderRadius: 999, background: "var(--bg-hover)", color: "var(--text-secondary)" }}>{root.reason}</span>}
        <CheckPill node={root} />
        <AutoGenPill node={root} />
        {(root.images && root.images.length > 0) && <span title="이미지 첨부" style={{ fontSize: 10 }}>📎{root.images.length}</span>}
        {root.embed_table && <span title="임베드" style={{ fontSize: 10 }}>🔗</span>}
        <span style={{ fontSize: 11, fontFamily: "monospace", fontWeight: 600 }}>{root.wafer_id}</span>
        {root.lot_id && <span style={{ fontSize: 10, color: "var(--text-secondary)", fontFamily: "monospace" }}>· {root.lot_id}</span>}
        {root.product && <span style={{ fontSize: 10, color: "var(--text-secondary)", fontFamily: "monospace" }}>· {root.product}</span>}
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>{(root.created_at || "").replace("T", " ")}</span>
        <span style={{ fontSize: 10, fontWeight: 600 }}>{root.author}</span>
      </div>
      <div style={{ fontSize: 12, marginTop: 4, whiteSpace: "pre-wrap", opacity: 0.95,
                    display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
        {root.text}
      </div>
    </div>
  );
}
