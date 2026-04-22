/* My_Inform.jsx v8.7.0 — 모듈 인폼 시스템 (역할 뷰 + 체크 + flow 상태 + SplitTable 연동).
 *
 * 보안: auth 미들웨어 + 세션 토큰 그대로. sf() 가 X-Session-Token 자동 주입.
 * 삭제 정책: 작성자 본인만 (관리자도 불가) — 서버에서도 동일하게 강제됨.
 */
import React, { useEffect, useMemo, useState } from "react";
import { sf, authSrc, postJson } from "../lib/api";
import PageGear from "../components/PageGear";

const API = "/api/informs";

const STATUS_META = {
  received:    { label: "접수",   color: "#64748b", dot: "○" },
  reviewing:   { label: "검토중", color: "#3b82f6", dot: "◐" },
  in_progress: { label: "진행중", color: "#f59e0b", dot: "◑" },
  completed:   { label: "완료",   color: "#22c55e", dot: "●" },
};
// v8.7.9: 2단계 플로우 — 접수 → 완료. legacy 값은 값만 허용(UI 는 숨김).
const STATUS_ORDER = ["received", "completed"];

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
  // v8.8.13: 완료 = 초록 · 미확인 = 빨강. 양쪽 다 표시 (이전에는 false 일 때 숨김).
  const checked = !!node.checked;
  const title = checked
    ? `확인 완료 · by ${node.checked_by||"?"} · ${(node.checked_at||"").replace("T"," ").slice(0,16)}`
    : "확인중 (미확인)";
  return (
    <span title={title}
      style={{
        fontSize: 10, padding: "2px 8px", borderRadius: 999,
        background: checked ? "#22c55e22" : "#ef444422",
        color: checked ? "#16a34a" : "#ef4444", fontWeight: 700,
      }}>{checked ? "✓ 확인완료" : "○ 확인중"}</span>
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

// v8.8.11: SplitTable 셀 팔레트 (SplitTable 과 동일 — 공유 util 후속 추출 예정).
const ST_CELL_COLORS = [
  { bg: "#C6EFCE", fg: "#006100" },
  { bg: "#FFEB9C", fg: "#9C5700" },
  { bg: "#FBE5D6", fg: "#BF4E00" },
  { bg: "#BDD7EE", fg: "#1F4E79" },
  { bg: "#E2BFEE", fg: "#7030A0" },
  { bg: "#B4DED4", fg: "#0B5345" },
  { bg: "#F4CCCC", fg: "#75194C" },
];
const ST_COLOR_PREFIXES = ["KNOB", "MASK"];
function stCellBg(val, uniq, pname) {
  if (!val || val === "None" || val === "null") return {};
  const pn = (pname || "").toUpperCase();
  if (!ST_COLOR_PREFIXES.some(p => pn.startsWith(p + "_"))) return {};
  const s = String(val);
  const idx = uniq[pn]?.[s];
  if (idx != null) { const c = ST_CELL_COLORS[idx % ST_CELL_COLORS.length]; return { background: c.bg, color: c.fg }; }
  return {};
}
function stPlanStyle(cell) {
  if (!cell) return {};
  if (cell.plan && cell.actual) {
    if (String(cell.plan) === String(cell.actual)) return {};
    return { borderLeft: "3px solid #ef4444", background: "#fef2f2" };
  }
  if (cell.plan) return { borderLeft: "3px solid #f97316", fontStyle: "italic", fontWeight: 700 };
  return {};
}

function EmbedTableView({ embed }) {
  if (!embed) return null;
  // v8.8.11: st_view(SplitTable /view 응답) 가 있으면 컬러링 + plan pin 동일 렌더.
  const st = embed.st_view;
  if (st && st.headers && st.rows) {
    // uniqueMap 계산: param 별 값 → 인덱스.
    const uniq = {};
    for (const r of st.rows) {
      const pn = String(r._param || "").toUpperCase();
      if (!ST_COLOR_PREFIXES.some(p => pn.startsWith(p + "_"))) continue;
      const seen = {};
      Object.values(r._cells || {}).forEach(c => {
        const v = c?.actual ?? c?.plan;
        if (v == null || v === "") return;
        const s = String(v);
        if (!(s in seen)) seen[s] = Object.keys(seen).length;
      });
      uniq[pn] = seen;
    }
    return (
      <div style={{ marginTop: 8, padding: 8, border: "1px solid var(--border)", borderRadius: 4, background: "var(--bg-primary)" }}>
        <div style={{ fontSize: 10, fontWeight: 700, color: "var(--accent)", marginBottom: 4 }}>
          🔗 SplitTable {embed.source && <span style={{ color: "var(--text-secondary)", fontWeight: 500 }}>· {embed.source}</span>}
        </div>
        {embed.note && <div style={{ fontSize: 10, color: "var(--text-secondary)", marginBottom: 4 }}>{embed.note}</div>}
        {/* v8.8.22: CUSTOM 최대 15줄까지 한 화면에 표시 — maxHeight 확장. */}
        <div style={{ maxHeight: 460, overflow: "auto" }}>
          <table style={{ borderCollapse: "collapse", fontSize: 10, fontFamily: "monospace" }}>
            <thead>
              <tr>
                <th style={{ border: "1px solid var(--border)", padding: "2px 6px", background: "var(--bg-secondary)", textAlign: "left", position: "sticky", top: 0, left: 0, zIndex: 2, minWidth: 160 }}>parameter</th>
                {st.headers.map((h, i) => (
                  <th key={i} style={{ border: "1px solid var(--border)", padding: "2px 6px", background: "var(--bg-secondary)", textAlign: "center", position: "sticky", top: 0, minWidth: 70 }}>{h}</th>
                ))}
              </tr>
              {(st.wafer_fab_list || []).length > 0 && (
                <tr>
                  <th style={{ border: "1px solid var(--border)", padding: "2px 6px", background: "var(--bg-tertiary)", fontSize: 9, color: "var(--text-secondary)", textAlign: "left", position: "sticky", top: 24, zIndex: 1 }}>fab_lot_id</th>
                  {st.wafer_fab_list.map((f, i) => (
                    <th key={i} style={{ border: "1px solid var(--border)", padding: "2px 6px", background: "var(--bg-tertiary)", fontSize: 9, color: "var(--text-secondary)", textAlign: "center", position: "sticky", top: 24, zIndex: 1 }}>{f || "—"}</th>
                  ))}
                </tr>
              )}
            </thead>
            <tbody>
              {st.rows.map((r, ri) => (
                <tr key={ri}>
                  <td style={{ border: "1px solid var(--border)", padding: "2px 6px", background: "var(--bg-secondary)", fontWeight: 700, position: "sticky", left: 0 }}>{r._param}</td>
                  {st.headers.map((_, ci) => {
                    const cell = (r._cells && r._cells[ci]) || {};
                    const bg = stCellBg(cell.actual ?? cell.plan, uniq, r._param);
                    const plan = stPlanStyle(cell);
                    const isPlan = !!cell.plan;
                    const display = (cell.actual != null && cell.actual !== "") ? String(cell.actual)
                      : (cell.plan != null ? String(cell.plan) : "");
                    return (
                      <td key={ci} style={{ border: "1px solid var(--border)", padding: "2px 6px", textAlign: "center", ...bg, ...plan }}>
                        {isPlan && <span title="plan" style={{ marginRight: 3 }}>📌</span>}
                        {display}
                        {isPlan && cell.plan !== cell.actual && cell.actual != null && cell.actual !== "" && (
                          <span style={{ marginLeft: 3, color: "#f97316", fontWeight: 700 }}>→{cell.plan}</span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }
  // legacy 2D rows 모드 — columns=[parameter, #1, #2, ...] + rows=[[param, v1, ...], ...].
  // v8.8.13: legacy 도 SplitTable 팔레트로 컬러링. columns[0] 이 parameter/param 류면 st_view 로 변환 후 같은 렌더.
  if ((!embed.columns?.length && !embed.rows?.length)) return null;
  const cols = embed.columns || [];
  const rows = embed.rows || [];
  const looksLikeParamTable =
    cols.length >= 2 &&
    /^(parameter|param)$/i.test(String(cols[0] || "").trim());
  if (looksLikeParamTable) {
    // legacy → st_view 구조.
    const headers = cols.slice(1);
    const stRows = rows.map(r => {
      const _cells = {};
      for (let i = 0; i < headers.length; i++) {
        const v = r[i + 1];
        _cells[i] = { actual: (v == null ? "" : String(v)) };
      }
      return { _param: String(r[0] ?? ""), _cells };
    });
    const uniq = {};
    for (const r of stRows) {
      const pn = String(r._param || "").toUpperCase();
      if (!ST_COLOR_PREFIXES.some(p => pn.startsWith(p + "_"))) continue;
      const seen = {};
      Object.values(r._cells || {}).forEach(c => {
        const v = c?.actual;
        if (v == null || v === "") return;
        const s = String(v);
        if (!(s in seen)) seen[s] = Object.keys(seen).length;
      });
      uniq[pn] = seen;
    }
    return (
      <div style={{ marginTop: 8, padding: 8, border: "1px solid var(--border)", borderRadius: 4, background: "var(--bg-primary)" }}>
        <div style={{ fontSize: 10, fontWeight: 700, color: "var(--accent)", marginBottom: 4 }}>
          🔗 SplitTable {embed.source && <span style={{ color: "var(--text-secondary)", fontWeight: 500 }}>· {embed.source}</span>}
        </div>
        {embed.note && <div style={{ fontSize: 10, color: "var(--text-secondary)", marginBottom: 4 }}>{embed.note}</div>}
        {/* v8.8.22: legacy 2D 경로도 15줄 정도는 스크롤 없이 보이도록 확장. */}
        <div style={{ maxHeight: 460, overflow: "auto" }}>
          <table style={{ borderCollapse: "collapse", fontSize: 10, fontFamily: "monospace" }}>
            <thead>
              <tr>
                <th style={{ border: "1px solid var(--border)", padding: "2px 6px", background: "var(--bg-secondary)", textAlign: "left", position: "sticky", top: 0, left: 0, zIndex: 2, minWidth: 160 }}>parameter</th>
                {headers.map((h, i) => (
                  <th key={i} style={{ border: "1px solid var(--border)", padding: "2px 6px", background: "var(--bg-secondary)", textAlign: "center", position: "sticky", top: 0, minWidth: 70 }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {stRows.map((r, ri) => (
                <tr key={ri}>
                  <td style={{ border: "1px solid var(--border)", padding: "2px 6px", background: "var(--bg-secondary)", fontWeight: 700, position: "sticky", left: 0 }}>{r._param}</td>
                  {headers.map((_, ci) => {
                    const cell = (r._cells && r._cells[ci]) || {};
                    const bg = stCellBg(cell.actual, uniq, r._param);
                    const display = (cell.actual != null && cell.actual !== "") ? String(cell.actual) : "";
                    return (
                      <td key={ci} style={{ border: "1px solid var(--border)", padding: "2px 6px", textAlign: "center", ...bg }}>{display}</td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }
  // legacy (non-param-table) 그대로.
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
  node, childrenByParent, onReply, onDelete, onToggleCheck, onEdit, user,
  depth = 0, constants,
}) {
  const [replyOpen, setReplyOpen] = useState(false);
  // v8.8.13: 답글의 module/reason 은 부모에서 자동 상속. UI 에선 읽기전용으로 표시.
  const [reply, setReply] = useState({ module: node.module || "", reason: node.reason || "", text: "" });
  const [attachSplit, setAttachSplit] = useState(false);
  const [splitForm, setSplitForm] = useState({ column: "", old_value: "", new_value: "" });
  const [replyImages, setReplyImages] = useState([]);
  const [uploading, setUploading] = useState(false);
  // v8.8.13: admin 만 수정 가능. text + module + reason 수정 (embed 스냅샷은 원본 유지).
  //          나중에 체크한 모듈/사유 보정 가능하도록 module/reason 도 편집 허용.
  const [editOpen, setEditOpen] = useState(false);
  const [editText, setEditText] = useState(node.text || "");
  const [editModule, setEditModule] = useState(node.module || "");
  const [editReason, setEditReason] = useState(node.reason || "");
  const canEdit = user?.role === "admin" && !!onEdit;

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
          {/* v8.8.13: 우측 액션 3버튼 통일 — 확인 · 답글 · 삭제. 상태 라벨은 CheckPill 로 좌측에 표시. */}
          <button onClick={() => onToggleCheck(node)} title={node.checked ? "미확인으로 되돌리기" : "확인 완료 처리"}
            style={{ fontSize: 10, padding: "2px 8px", borderRadius: 4, cursor: "pointer",
              border: "1px solid " + (node.checked ? "#ef4444" : "#22c55e"),
              background: node.checked ? "transparent" : "#22c55e",
              color: node.checked ? "#ef4444" : "#fff", fontWeight: 700 }}>
            {node.checked ? "↺ 미확인" : "✓ 확인"}
          </button>
          <button onClick={() => setReplyOpen(!replyOpen)} title="답글 달기 (module/reason 은 부모 자동 상속)"
            style={{ fontSize: 10, padding: "2px 8px", borderRadius: 4, cursor: "pointer",
              border: "1px solid var(--accent)", background: "transparent", color: "var(--accent)", fontWeight: 700 }}>
            {replyOpen ? "닫기" : "답글"}
          </button>
          {/* v8.8.13: 수정 — admin 만. text 만 바뀌고 module/reason/embed 는 원본 유지. */}
          {canEdit && (
            <button onClick={() => { setEditText(node.text || ""); setEditModule(node.module || ""); setEditReason(node.reason || ""); setEditOpen(!editOpen); }}
              title="본문 수정 (admin 전용)"
              style={{ fontSize: 10, padding: "2px 8px", borderRadius: 4, cursor: "pointer",
                border: "1px solid #3b82f6", background: "transparent", color: "#3b82f6", fontWeight: 700 }}>
              {editOpen ? "닫기" : "✎ 수정"}
            </button>
          )}
          {canDelete && kids.length === 0 && (
            <button onClick={() => onDelete(node.id)} title="이 글 삭제 (자식이 없을 때만)"
              style={{ fontSize: 10, padding: "2px 8px", borderRadius: 4, cursor: "pointer",
                border: "1px solid #ef4444", background: "transparent", color: "#ef4444", fontWeight: 700 }}>
              🗑 삭제
            </button>
          )}
        </div>

        {editOpen ? (
          <div style={{ marginTop: 4 }}>
            {/* v8.8.13: module/사유 도 수정 허용 — 처음 등록 시 실수로 안 넣었어도 나중에 교정 가능. */}
            <div style={{ display: "flex", gap: 6, marginBottom: 6, flexWrap: "wrap" }}>
              <select value={editModule} onChange={e => setEditModule(e.target.value)}
                style={{ padding: "4px 6px", borderRadius: 4, border: "1px solid #3b82f6", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 11 }}>
                <option value="">(모듈 없음)</option>
                {constants.modules.map(m => <option key={m} value={m}>{m}</option>)}
              </select>
              <select value={editReason} onChange={e => setEditReason(e.target.value)}
                style={{ padding: "4px 6px", borderRadius: 4, border: "1px solid #3b82f6", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 11 }}>
                <option value="">(사유 없음)</option>
                {constants.reasons.map(r => <option key={r} value={r}>{r}</option>)}
              </select>
            </div>
            <textarea value={editText} onChange={e => setEditText(e.target.value)} rows={4}
              style={{ width: "100%", padding: 8, borderRadius: 4, border: "1px solid #3b82f6", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12, resize: "vertical", fontFamily: "inherit" }} />
            <div style={{ fontSize: 10, color: "var(--text-secondary)", marginTop: 4 }}>
              ※ 본문·모듈·사유 수정 가능. SplitTable 스냅샷은 작성 시점 값으로 유지됩니다.
            </div>
            <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
              <button onClick={() => {
                const patch = {};
                const t0 = (node.text || "").trim(), t1 = (editText || "").trim();
                if (t1 !== t0) patch.text = editText;
                if ((editModule || "") !== (node.module || "")) patch.module = editModule || "";
                if ((editReason || "") !== (node.reason || "")) patch.reason = editReason || "";
                if (Object.keys(patch).length === 0) { setEditOpen(false); return; }
                onEdit(node.id, patch).then(() => setEditOpen(false));
              }}
                style={{ padding: "5px 14px", borderRadius: 4, border: "none", background: "#3b82f6", color: "#fff", fontSize: 11, fontWeight: 700, cursor: "pointer" }}>저장 (admin)</button>
              <button onClick={() => { setEditOpen(false); setEditText(node.text || ""); setEditModule(node.module || ""); setEditReason(node.reason || ""); }}
                style={{ padding: "5px 10px", borderRadius: 4, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", fontSize: 11, cursor: "pointer" }}>취소</button>
            </div>
          </div>
        ) : (
          <div style={{ fontSize: 12, color: "var(--text-primary)", whiteSpace: "pre-wrap", lineHeight: 1.5 }}>{node.text}</div>
        )}
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
            {/* v8.8.13: 답글의 module/reason 은 부모 자동 상속 → 읽기전용 pill. */}
            <div style={{ display: "flex", gap: 6, marginBottom: 6, alignItems: "center", flexWrap: "wrap", fontSize: 10, color: "var(--text-secondary)" }}>
              <span>상속:</span>
              {(() => { const mc = moduleColor(reply.module || "—"); return (
                <span style={{ fontSize: 10, padding: "2px 8px", borderRadius: 999, background: mc + "22", color: mc, fontWeight: 700, border: "1px solid " + mc + "55" }}>{reply.module || "(모듈 없음)"}</span>
              ); })()}
              <span style={{ fontSize: 10, padding: "2px 8px", borderRadius: 999, background: "var(--bg-hover)", color: "var(--text-secondary)" }}>[{reply.reason || "(사유 없음)"}]</span>
              <label style={{ fontSize: 10, color: "var(--text-secondary)", display: "inline-flex", alignItems: "center", gap: 4, cursor: "pointer", marginLeft: "auto" }}>
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
                // v8.8.12: 답글 text 앞에 [RE] prefix 자동 (이미 있으면 중복 안 붙임).
                const replyText = (reply.text || "").trim();
                const txt = replyText.startsWith("[RE]") ? replyText : (replyText ? `[RE] ${replyText}` : replyText);
                const body = { ...reply, text: txt, images: replyImages };
                if (attachSplit && (splitForm.column || splitForm.new_value)) {
                  body.splittable_change = { ...splitForm, applied: false };
                }
                onReply(node.id, body).then(() => {
                  setReply({ module: node.module || "", reason: node.reason || "", text: "" });
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
          onEdit={onEdit}
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
function MailDialog({ root, user, reasonTemplates, onClose }) {
  // v8.7.2: 인폼 → 사내 메일 API 로 HTML 본문 전송 (multipart).
  // v8.8.3: 공용 메일그룹(/api/mail-groups/list) 도 함께 노출 — 만들어진 그룹이 드롭다운에 안 뜨던 문제 해결.
  //          + 새 그룹 관리 서브 모달(z-index 10001 로 메일 다이얼로그 위에 올라오게).
  // v8.8.17: 사유별 메일 템플릿 prefill — subject/body 초기값에 치환.
  const [recipients, setRecipients] = useState([]);
  const [groups, setGroups] = useState({});          // {groupName: [emails]}
  const [publicGroups, setPublicGroups] = useState([]); // v8.8.3: 공용 메일 그룹 목록 [{id,name,members,extra_emails}]
  const [pickedUsers, setPickedUsers] = useState([]);   // usernames
  const [pickedGroups, setPickedGroups] = useState([]); // group names
  const _tpl = (reasonTemplates || {})[root.reason || ""] || {};
  const _subst = (s) => (s || "")
    .replaceAll("{product}", root.product || "")
    .replaceAll("{lot}", root.lot_id || "")
    .replaceAll("{wafer}", root.wafer_id || "")
    .replaceAll("{module}", root.module || "")
    .replaceAll("{reason}", root.reason || "");
  const _defSubject = _tpl.subject
    ? _subst(_tpl.subject)
    : `[flow 인폼] ${root.module || ""} · ${root.lot_id || root.wafer_id || ""}`.trim();
  const _defBody = _tpl.body ? _subst(_tpl.body) : "";
  const [subject, setSubject] = useState(_defSubject);
  const [body, setBody] = useState(_defBody);
  const [statusCode, setStatusCode] = useState("");
  const [includeThread, setIncludeThread] = useState(true);
  const [extraEmails, setExtraEmails] = useState("");
  const [attachments, setAttachments] = useState([]); // inform image URLs to include
  const [filter, setFilter] = useState("");
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(null);
  const [error, setError] = useState("");
  const [showMgr, setShowMgr] = useState(false);  // v8.8.3: 공용 메일 그룹 관리 서브모달
  const [newGroupName, setNewGroupName] = useState("");
  const [newGroupEmails, setNewGroupEmails] = useState("");
  // v8.8.21: 실시간 메일 프리뷰 — body 바뀔 때마다 debounce 후 fetch.
  const [preview, setPreview] = useState(null);
  useEffect(() => {
    if (!root?.id) return;
    const h = setTimeout(() => {
      sf(API + "/" + encodeURIComponent(root.id) + "/mail-preview?body=" + encodeURIComponent(body || ""))
        .then(d => setPreview(d)).catch(() => setPreview(null));
    }, 250);
    return () => clearTimeout(h);
  }, [root?.id, body]);

  const reloadGroups = () => {
    sf(API + "/mail-groups").then(d => setGroups(d.groups || {})).catch(() => setGroups({}));
    sf("/api/mail-groups/list").then(d => setPublicGroups(d.groups || [])).catch(() => setPublicGroups([]));
  };

  useEffect(() => {
    sf(API + "/recipients").then(d => setRecipients(d.recipients || [])).catch(() => setRecipients([]));
    reloadGroups();
  }, []);

  // v8.8.3: admin 모듈 그룹(groups) + 공용 그룹(publicGroups) 병합.
  // 공용 그룹은 members(username 목록) → email 로 resolve + extra_emails 합집합.
  const resolveGroupEmails = (gname) => {
    if (groups[gname]) return groups[gname] || [];
    const pg = publicGroups.find(g => g.name === gname);
    if (!pg) return [];
    const out = new Set();
    (pg.members || []).forEach(un => {
      const em = recipients.find(r => r.username === un)?.email;
      if (em && em.includes("@")) out.add(em);
    });
    (pg.extra_emails || []).forEach(em => { if (em && em.includes("@")) out.add(em); });
    return Array.from(out);
  };
  const allGroupNames = Array.from(new Set([
    ...Object.keys(groups || {}),
    ...publicGroups.map(g => g.name).filter(Boolean),
  ])).sort();

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
      // v8.8.21: username 자체가 email id 인 경우 effective_email 로 해결.
      const r = recipients.find(r => r.username === un);
      const em = (r?.effective_email) || r?.email;
      if (em && em.includes("@")) out.add(em);
    });
    // v8.8.3: admin 그룹 + 공용 그룹 모두 지원.
    pickedGroups.forEach(g => resolveGroupEmails(g).forEach(em => { if (em && em.includes("@")) out.add(em); }));
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
        {/* v8.8.1: 발송자 ID 자동 명시 제거. 제품 담당자 라인만 본문 상단에 삽입. */}
        <div style={{ fontSize: 10, padding: "6px 10px", marginBottom: 10, borderRadius: 4, background: "rgba(59,130,246,0.10)", border: "1px solid rgba(59,130,246,0.5)", color: "#1d4ed8" }}>
          📨 발송계정: 시스템(Admin) · 본문 상단에 <b>제품 담당자</b> 라인 자동 삽입 (해당 제품에 등록된 담당자 있을 때).
        </div>

        {/* v8.8.3: Module recipient groups — admin 그룹 + 공용 메일그룹 합집합. 만들어진 그룹도 노출. */}
        <div style={{ marginBottom: 10 }}>
          <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4, display: "flex", alignItems: "center", gap: 6 }}>
            <span>📮 메일 그룹 <span style={{ fontWeight: 400, color: "var(--text-secondary)" }}>({pickedGroups.length} 선택 · {allGroupNames.length} 가용)</span></span>
            <span style={{ flex: 1 }} />
            <button type="button" onClick={() => setShowMgr(true)}
              style={{ padding: "2px 10px", borderRadius: 4, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", fontSize: 10, cursor: "pointer" }}>관리</button>
          </div>
          {allGroupNames.length === 0 && (
            <div style={{ fontSize: 10, color: "var(--text-secondary)", padding: 6, border: "1px dashed var(--border)", borderRadius: 4 }}>
              등록된 메일 그룹 없음 — 우측 [관리] 로 새 그룹을 만드세요.
            </div>
          )}
          {allGroupNames.length > 0 && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {allGroupNames.map((gname) => {
                const on = pickedGroups.includes(gname);
                const emails = resolveGroupEmails(gname);
                const isPublic = !groups[gname];
                return (
                  <span key={gname} onClick={() => toggleGroup(gname)} style={{
                    padding: "5px 12px", borderRadius: 999, fontSize: 11,
                    background: on ? "var(--accent)" : "var(--bg-card)",
                    color: on ? "#fff" : "var(--text-primary)",
                    border: "1px solid " + (on ? "var(--accent)" : "var(--border)"),
                    cursor: "pointer", fontWeight: 600,
                  }} title={isPublic ? "공용 메일 그룹" : "admin 모듈 그룹"}>
                    {isPublic ? "[공용] " : ""}{gname} · {emails.length}명
                  </span>
                );
              })}
            </div>
          )}
        </div>

        {/* v8.8.3: 공용 메일 그룹 관리 서브모달 — z-index 10001 로 부모 MailDialog(9999) 위에 확실히 올라옴. */}
        {showMgr && (
          <div onClick={() => setShowMgr(false)}
               style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", zIndex: 10001, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
            <div onClick={e => e.stopPropagation()}
                 style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", borderRadius: 8, padding: 16, width: "90%", maxWidth: 560, color: "var(--text-primary)" }}>
              <div style={{ display: "flex", alignItems: "center", marginBottom: 10 }}>
                <div style={{ fontSize: 14, fontWeight: 700 }}>📮 공용 메일 그룹 관리</div>
                <span style={{ flex: 1 }} />
                <span onClick={() => setShowMgr(false)} style={{ cursor: "pointer", fontSize: 16 }}>✕</span>
              </div>
              <div style={{ fontSize: 10, color: "var(--text-secondary)", marginBottom: 8 }}>
                모든 로그인 유저가 공용으로 사용하는 메일 그룹 (inform / meeting 공용). 이름 + 이메일 콤마/세미콜론 구분으로 입력하면 바로 생성됩니다.
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 2fr", gap: 6, marginBottom: 8 }}>
                <input value={newGroupName} onChange={e => setNewGroupName(e.target.value)}
                  placeholder="그룹 이름" style={S} />
                <input value={newGroupEmails} onChange={e => setNewGroupEmails(e.target.value)}
                  placeholder="member1@x.com, member2@y.com" style={{ ...S, fontFamily: "monospace" }} />
              </div>
              <div style={{ display: "flex", gap: 6, marginBottom: 12 }}>
                <button type="button" onClick={() => {
                  const nm = (newGroupName || "").trim();
                  if (!nm) { alert("그룹 이름을 입력하세요"); return; }
                  const extras = (newGroupEmails || "").split(/[,\s;]+/).map(s => s.trim()).filter(s => s && s.includes("@"));
                  postJson("/api/mail-groups/create", { name: nm, extra_emails: extras, members: [] })
                    .then(() => { setNewGroupName(""); setNewGroupEmails(""); reloadGroups(); })
                    .catch(e => alert(e.message));
                }}
                  style={{ padding: "6px 14px", borderRadius: 4, border: "none", background: "var(--accent)", color: "#fff", fontSize: 11, fontWeight: 600, cursor: "pointer" }}>+ 그룹 생성</button>
              </div>
              <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4 }}>현재 공용 그룹 ({publicGroups.length})</div>
              <div style={{ maxHeight: 240, overflowY: "auto", border: "1px solid var(--border)", borderRadius: 4, background: "var(--bg-card)" }}>
                {publicGroups.length === 0 && (
                  <div style={{ padding: 10, fontSize: 11, color: "var(--text-secondary)", textAlign: "center" }}>공용 그룹 없음</div>
                )}
                {publicGroups.map(g => (
                  <div key={g.id} style={{ display: "flex", alignItems: "center", gap: 6, padding: "5px 10px", borderBottom: "1px solid var(--border)", fontSize: 11 }}>
                    <b style={{ fontFamily: "monospace" }}>{g.name}</b>
                    <span style={{ color: "var(--text-secondary)", fontFamily: "monospace" }}>
                      · members {(g.members || []).length} · extras {(g.extra_emails || []).length}
                    </span>
                    <span style={{ flex: 1 }} />
                    <span onClick={() => {
                      if (!window.confirm(`그룹 "${g.name}" 삭제?`)) return;
                      sf("/api/mail-groups/delete?id=" + encodeURIComponent(g.id), { method: "POST" })
                        .then(() => reloadGroups())
                        .catch(e => alert(e.message));
                    }} style={{ cursor: "pointer", color: "#ef4444", fontSize: 10, fontWeight: 600 }}>삭제</span>
                  </div>
                ))}
              </div>
              <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 10 }}>
                <button type="button" onClick={() => setShowMgr(false)}
                  style={{ padding: "6px 14px", borderRadius: 4, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", fontSize: 11, cursor: "pointer" }}>닫기</button>
              </div>
            </div>
          </div>
        )}

        {/* Individual recipient picker */}
        <div style={{ marginBottom: 10 }}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4, fontSize: 11, fontWeight: 600 }}>
            <span>개별 유저 ({pickedUsers.length} 선택)</span>
            <input value={filter} onChange={e => setFilter(e.target.value)} placeholder="🔎 유저/이메일 검색" style={{ padding: "3px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 11, width: 200 }} />
          </div>
          <div style={{ maxHeight: 140, overflow: "auto", border: "1px solid var(--border)", borderRadius: 6, background: "var(--bg-card)" }}>
            {visibleList.length === 0 && <div style={{ padding: 14, textAlign: "center", fontSize: 11, color: "var(--text-secondary)" }}>유저가 없습니다.</div>}
            {/* v8.8.21: BE 가 이미 admin/hol/test/비email 계정을 필터링해서 내려주므로 FE 는 그대로 표시.
                 username = 사내 email id 이므로 별도 email 컬럼 노출 불필요. */}
            {visibleList.map(r => {
              const on = pickedUsers.includes(r.username);
              return (
                <div key={r.username} onClick={() => toggleUser(r.username)} style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 10px", fontSize: 11, cursor: "pointer", background: on ? "rgba(59,130,246,0.12)" : "transparent", borderBottom: "1px solid var(--border)" }}>
                  <input type="checkbox" checked={on} readOnly />
                  <span style={{ fontWeight: 600 }}>{r.username}</span>
                </div>
              );
            })}
          </div>
        </div>

        <div style={{ marginBottom: 10 }}>
          <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 3 }}>추가 이메일 <span style={{ fontWeight: 400, color: "var(--text-secondary)" }}>(콤마/공백/세미콜론 구분)</span></div>
          <input value={extraEmails} onChange={e => setExtraEmails(e.target.value)} placeholder="ext1@vendor.com, ext2@vendor.com" style={{ ...S, fontFamily: "monospace", fontSize: 11 }} />
        </div>

        {/* v8.8.1: statusCode 등 백엔드 전용 필드는 UI 에서 제거 — admin 기본값으로 자동 주입됨. */}
        <div style={{ marginBottom: 8 }}>
          <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 3 }}>제목</div>
          <input value={subject} onChange={e => setSubject(e.target.value)} style={S} />
        </div>
        <div style={{ marginBottom: 8 }}>
          <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 3 }}>본문 프로즈 <span style={{ fontWeight: 400, color: "var(--text-secondary)" }}>(HTML content 상단에 강조 삽입, 생략 가능)</span></div>
          <textarea value={body} onChange={e => setBody(e.target.value)} rows={4} style={{ ...S, resize: "vertical" }} />
          {preview?.owners_line && (
            <div style={{ marginTop: 4, fontSize: 10, color: "#16a34a", background: "rgba(16,163,74,0.08)", border: "1px solid #16a34a", borderRadius: 4, padding: "4px 8px" }}>
              📌 자동 삽입: <b>제품담당자</b> : {preview.owners_line}
            </div>
          )}
        </div>
        {/* v8.8.21: 실시간 미리보기 — 실제 보낼 HTML body, 수신자, 담당자 라인을 한눈에. */}
        {preview?.html_body && (
          <details style={{ marginBottom: 10, border: "1px solid var(--border)", borderRadius: 5, padding: "4px 10px", background: "var(--bg-card)" }} open>
            <summary style={{ fontSize: 11, fontWeight: 600, cursor: "pointer", color: "var(--accent)" }}>
              🔍 메일 미리보기 · 제목 [{subject}] · 수신자 {totalEmails}명
            </summary>
            <div style={{ marginTop: 6, fontSize: 10, color: "var(--text-secondary)", marginBottom: 4, fontFamily: "monospace" }}>
              To: {computedEmails().slice(0, 8).join(", ")}{computedEmails().length > 8 ? ` (+${computedEmails().length - 8}명)` : ""}
            </div>
            <div style={{ maxHeight: 320, overflow: "auto", background: "#fff", color: "#000", padding: 8, border: "1px solid var(--border)", borderRadius: 4 }}
                 dangerouslySetInnerHTML={{ __html: preview.html_body }} />
          </details>
        )}
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

        {/* v8.8.21: 직접 파일첨부 UI 제거 → 인폼 스냅샷 xlsx 자동 첨부로 대체.
             인폼에 담긴 제품/lot/wafer + splittable_change + body 를 SplitTable 엑셀 형식으로
             BE 가 렌더 → 메일 files 에 자동 포함 된다. 인라인 이미지 첨부는 그대로 유지. */}
        {preview?.auto_attachments?.length > 0 && (
          <div style={{ marginBottom: 10, padding: 8, borderRadius: 5, background: "rgba(16,185,129,0.08)", border: "1px solid #10b981" }}>
            <div style={{ fontSize: 11, fontWeight: 600, color: "#10b981" }}>📎 자동 첨부 (SplitTable 스냅샷 xlsx)</div>
            {preview.auto_attachments.map((a, i) => (
              <div key={i} style={{ fontSize: 10, fontFamily: "monospace", color: "var(--text-secondary)", marginTop: 2 }}>
                · {a.name} ({Math.round((a.bytes || 0) / 1024)} KB)
              </div>
            ))}
          </div>
        )}

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

function RootHeader({ root, onChangeStatus, user }) {
  // v8.7.9: FLOW 큰 카드 제거 → 접수/완료 2단계 + 크고 눈에 띄는 "확인 완료" 버튼.
  const [openHist, setOpenHist] = useState(false);
  const [openMail, setOpenMail] = useState(false);
  const hist = root.status_history || [];
  const mailHist = root.mail_history || [];
  const mailCount = mailHist.length;
  const lastMailAt = mailCount ? (mailHist[mailHist.length - 1].at || mailHist[mailHist.length - 1].sent_at || "") : "";
  const isCompleted = root.flow_status === "completed";
  const toggleDone = () => {
    const next = isCompleted ? "received" : "completed";
    onChangeStatus(root.id, next, "");
  };
  return (
    <div style={{
      padding: "0 2px", marginBottom: 4,
      display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap",
    }}>
      {/* v8.8.13: wafer + 메일 + 이력 을 한 줄로 통합. 외부 wafer 라벨 제거하고 이 내부에 흡수 →
          이전에 있던 빈 RootHeader 라인(왼쪽 공백+오른쪽 메일/이력만)을 제거. */}
      <span style={{ fontSize: 11, fontFamily: "monospace", color: "var(--text-secondary)" }}>
        wafer: <b style={{ color: "var(--text-primary)" }}>{root.wafer_id || "-"}</b>
      </span>
      <div style={{ flex: 1 }} />
      <span onClick={() => setOpenMail(true)}
        title={lastMailAt ? `최근 메일: ${(lastMailAt || "").replace("T"," ").slice(0,16)}` : "사내 메일 API 로 이 인폼 내용 전송"}
        style={{ padding: "2px 8px", borderRadius: 4, border: "1px solid var(--accent)",
                 background: "rgba(249,115,22,0.08)", color: "var(--accent)",
                 fontSize: 10, fontWeight: 700, cursor: "pointer", userSelect: "none", lineHeight: 1.3 }}>
        ✉ 메일{mailCount > 0 && ` (${mailCount})`}
      </span>
      <span onClick={() => setOpenHist(!openHist)}
        title="상태 변경 이력 토글"
        style={{ fontSize: 10, color: "var(--accent)", cursor: "pointer", padding: "2px 6px" }}>
        이력{hist.length > 0 && ` (${hist.length})`}
      </span>
      {openMail && <MailDialog root={root} user={user} onClose={() => setOpenMail(false)} />}
      {openHist && hist.length > 0 && (
        <div style={{ width: "100%", marginTop: 4, paddingTop: 4, borderTop: "1px dashed var(--border)", fontSize: 11, color: "var(--text-secondary)", fontFamily: "monospace" }}>
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

/* v8.8.0: SplitTable 노트 카드 — root_lot_id 키로 fetch 한 wafer/param/lot/param_global 노트 표시 */
function SplitNotesCard({ notes, root_lot_id }) {
  if (!notes || notes.length === 0) return null;
  const wafers = notes.filter(n => n.scope === "wafer");
  const params = notes.filter(n => n.scope === "param");
  const lots   = notes.filter(n => n.scope === "lot");
  const pgs    = notes.filter(n => n.scope === "param_global");
  const renderRow = (n, kind, color) => {
    const parts = (n.key || "").split("__");
    let label = "";
    if (n.scope === "wafer") label = `🏷 W${(parts[2] || "").replace(/^W/, "")}`;
    else if (n.scope === "param") label = `💬 W${(parts[2] || "").replace(/^W/, "")} × ${parts[3] || ""}`;
    else if (n.scope === "lot") label = `📌 LOT ${parts[2] || ""}`;
    else if (n.scope === "param_global") label = `🌐 ${parts[2] || ""} (전역)`;
    return (
      <div key={n.id} style={{ padding: "6px 10px", marginBottom: 4, borderRadius: 5, background: "var(--bg-card)", border: "1px solid var(--border)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 3, gap: 6 }}>
          <span style={{ fontSize: 9, fontWeight: 700, padding: "1px 6px", borderRadius: 8, background: color, color: "#fff" }}>{label}</span>
          <span style={{ fontSize: 9, color: "var(--text-secondary)", fontFamily: "monospace" }}>
            {n.username} · {(n.created_at || "").replace("T", " ").slice(0, 16)}
          </span>
        </div>
        <div style={{ fontSize: 11, whiteSpace: "pre-wrap", lineHeight: 1.45 }}>{n.text}</div>
      </div>
    );
  };
  return (
    <div style={{ background: "#3b82f611", border: "1px solid #3b82f666", borderRadius: 8, padding: 10, marginBottom: 10 }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: "#1d4ed8", marginBottom: 6 }}>
        📝 SplitTable 노트 — root_lot_id <span style={{ fontFamily: "monospace" }}>{root_lot_id}</span> ({notes.length}건)
        <span style={{ fontSize: 10, fontWeight: 500, marginLeft: 8, color: "var(--text-secondary)" }}>
          wafer {wafers.length} · param {params.length} · lot {lots.length} · 전역 {pgs.length}
        </span>
      </div>
      {wafers.map(n => renderRow(n, "wafer", "#3b82f6"))}
      {params.map(n => renderRow(n, "param", "#8b5cf6"))}
      {lots.map(n => renderRow(n, "lot", "#0ea5e9"))}
      {pgs.map(n => renderRow(n, "param_global", "#14b8a6"))}
    </div>
  );
}

/* v8.7.8: Lot drill-down 모듈별 요약 테이블
   각 모듈에 대해 (등록됨, 메일 전송됨) 을 체크/미체크로 한눈에 */
function LotModuleSummary({ thread, modules }) {
  const rows = (modules || []).map(m => {
    const entries = (thread || []).filter(e => (e.module || "") === m);
    const hasInform = entries.length > 0;
    // v8.7.9: 가장 최근 메일 날짜 뽑기.
    let lastMailAt = "";
    let mailCount = 0;
    for (const e of entries) {
      for (const mh of (e.mail_history || [])) {
        const at = mh.at || mh.sent_at || mh.time || "";
        if (!at) continue;
        mailCount += 1;
        if (at > lastMailAt) lastMailAt = at;
      }
    }
    // 완료(담당자 확인) 여부 + 가장 최근 확인 날짜
    let completedAt = "";
    for (const e of entries) {
      if (e.flow_status === "completed") {
        const hist = (e.status_history || []).filter(h => h.status === "completed");
        const last = hist.length ? (hist[hist.length - 1].at || "") : "";
        if (last > completedAt) completedAt = last;
      }
    }
    const count = entries.length;
    return { module: m, hasInform, mailCount, lastMailAt, completedAt, count };
  });
  if (!rows.length) return null;
  const cellBase = { padding: "8px 12px", borderBottom: "1px solid var(--border)" };
  // v8.8.12: LocalStorage 기반 접기 상태 유지.
  const COLLAPSE_KEY = "flow_inform_module_summary_collapsed";
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem(COLLAPSE_KEY) === "1"; } catch { return false; }
  });
  const toggle = () => {
    const nv = !collapsed;
    setCollapsed(nv);
    try { localStorage.setItem(COLLAPSE_KEY, nv ? "1" : "0"); } catch {}
  };
  return (
    <div style={{ marginBottom: 14, padding: 12, borderRadius: 8, background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
      <div onClick={toggle}
        style={{ fontSize: 14, fontWeight: 700, marginBottom: collapsed ? 0 : 8, fontFamily: "monospace", color: "var(--accent)", cursor: "pointer", userSelect: "none", display: "flex", alignItems: "center", gap: 6 }}>
        <span>{collapsed ? "▶" : "▼"}</span>
        <span>📋 모듈별 진행 요약</span>
        {collapsed && <span style={{ fontSize: 10, color: "var(--text-secondary)", fontWeight: 400, marginLeft: 8 }}>
          ({rows.filter(r => r.hasInform).length} 모듈 활성 / {rows.reduce((s, r) => s + (r.count || 0), 0)} 건)
        </span>}
      </div>
      {!collapsed && <div style={{ overflowX: "auto" }}>
        <table style={{ borderCollapse: "collapse", fontSize: 13, width: "100%", tableLayout: "fixed" }}>
          {/* v8.8.0: 3개 데이터 열 너비 균등 (등록 / 메일 / 담당자 확인). */}
          <colgroup>
            <col style={{ width: "18%" }} />
            <col style={{ width: "27%" }} />
            <col style={{ width: "27%" }} />
            <col style={{ width: "20%" }} />
            <col style={{ width: "8%" }} />
          </colgroup>
          <thead>
            <tr style={{ background: "var(--bg-tertiary)" }}>
              <th style={{ ...cellBase, textAlign: "left", fontWeight: 700 }}>모듈</th>
              <th style={{ ...cellBase, textAlign: "center", fontWeight: 700 }}>등록</th>
              <th style={{ ...cellBase, textAlign: "center", fontWeight: 700 }}>메일</th>
              <th style={{ ...cellBase, textAlign: "center", fontWeight: 700 }}>담당자 확인</th>
              <th style={{ ...cellBase, textAlign: "center", fontWeight: 700 }}>건수</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.module}>
                <td style={{ ...cellBase, fontFamily: "monospace", fontWeight: 600 }}>{r.module}</td>
                <td style={{ ...cellBase, textAlign: "center", color: r.hasInform ? "#22c55e" : "var(--text-secondary)", fontWeight: 700 }}>{r.hasInform ? "✓" : "·"}</td>
                <td style={{ ...cellBase, fontFamily: "monospace" }}>
                  {r.mailCount > 0
                    ? <>
                        <span style={{ color: "#3b82f6", fontWeight: 700 }}>✓ {r.mailCount}회</span>
                        {r.lastMailAt && <span style={{ marginLeft: 8, color: "var(--text-secondary)", fontSize: 11 }}>{(r.lastMailAt || "").replace("T", " ").slice(0, 16)}</span>}
                      </>
                    : <span style={{ color: "var(--text-secondary)" }}>·</span>}
                </td>
                <td style={{ ...cellBase, fontFamily: "monospace" }}>
                  {r.completedAt
                    ? <>
                        <span style={{ color: "#22c55e", fontWeight: 700 }}>✓ 완료</span>
                        <span style={{ marginLeft: 8, color: "var(--text-secondary)", fontSize: 11 }}>{(r.completedAt || "").replace("T", " ").slice(0, 16)}</span>
                      </>
                    : <span style={{ color: "var(--text-secondary)" }}>·</span>}
                </td>
                <td style={{ ...cellBase, textAlign: "center", color: "var(--text-secondary)" }}>{r.count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>}
    </div>
  );
}


/* ── 메인 페이지 ── */
export default function My_Inform({ user }) {
  // v8.8.1: 설정에 products(카탈로그) + raw_db_root 추가.
  const [constants, setConstants] = useState({ modules: [], reasons: [], flow_statuses: [], products: [], raw_db_root: "", reason_templates: {} });
  // v8.8.1: 선택 제품의 Lot 후보 (RAWDATA_DB 에서 폴더 스캔).
  const [productLots, setProductLots] = useState({ product: "", lots: [], source: "" });
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

  const [moduleFilter, setModuleFilter] = useState([]);  // 체크된 모듈만 표시 (빈 배열=전체)
  // v8.8.15: 제품 필터 nav — 빈 배열 = 전체, 아니면 선택된 제품만 통과.
  const [productFilter, setProductFilter] = useState([]);
  // v8.8.13: moduleFilter 기본 = 내 조회 권한 모든 모듈. admin 또는 all_rounder 이면 전체.
  //   myMods/constants 가 로딩되면 1회 자동 셋업. 이후엔 사용자 체크 토글이 우선.
  const [moduleFilterInit, setModuleFilterInit] = useState(false);

  // v8.8.0: SplitTable 노트 — Lot 뷰 하단에 표시 (root_lot_id 키).
  const [splitNotes, setSplitNotes] = useState([]);

  // v8.8.0: 제품별 담당자 (product_contacts). 사이드바 폴더블 + 메일 본문에 자동 첨부.
  const [productContacts, setProductContacts] = useState({}); // { product: [contacts] }
  const [openContactProducts, setOpenContactProducts] = useState({}); // { product: bool }
  const [editContact, setEditContact] = useState(null); // {product, id?, name, role, email, phone, note}
  // v8.8.2: 유저/그룹 혼합 일괄 추가 모달 상태.
  const [bulkPickProduct, setBulkPickProduct] = useState("");  // opened for which product
  const [bulkEligibleUsers, setBulkEligibleUsers] = useState([]);
  const [bulkGroups, setBulkGroups] = useState([]);
  const [bulkSelUsers, setBulkSelUsers] = useState([]);
  const [bulkSelGroups, setBulkSelGroups] = useState([]);
  const [bulkRole, setBulkRole] = useState("");
  const [bulkBusy, setBulkBusy] = useState(false);

  const isAdmin = user?.role === "admin";

  /* Load constants + my modules */
  useEffect(() => {
    // v8.8.1: /config 에서 products + raw_db_root 까지 같이 받는다.
    sf(API + "/config").then(d => setConstants({
      modules: d.modules || [], reasons: d.reasons || [], flow_statuses: d.flow_statuses || [],
      products: d.products || [], raw_db_root: d.raw_db_root || "",
      reason_templates: d.reason_templates || {},
    })).catch(() => {
      sf(API + "/modules").then(d => setConstants(c => ({ ...c,
        modules: d.modules || [], reasons: d.reasons || [], flow_statuses: d.flow_statuses || [],
      }))).catch(() => {});
    });
    // v8.8.13: 인폼 전용 my-modules — admin 이 유저별로 설정한 inform_user_modules 우선, 없으면 groups fallback.
    sf("/api/informs/my-modules").then(d => setMyMods({
      modules: d.modules || [], all_rounder: !!d.all_rounder,
    })).catch(() => setMyMods({ modules: [], all_rounder: !!isAdmin }));
  }, []);

  // v8.8.13: moduleFilter 기본값 = 내 권한 모듈 전체 체크. 최초 한 번만.
  useEffect(() => {
    if (moduleFilterInit) return;
    const all = constants.modules || [];
    if (all.length === 0) return;
    const my = (myMods.all_rounder || isAdmin) ? all : (myMods.modules || []).filter(m => all.includes(m));
    setModuleFilter(my.length ? [...my] : [...all]);
    setModuleFilterInit(true);
  }, [constants.modules, myMods, isAdmin, moduleFilterInit]);

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

  // v8.8.0: Lot 뷰 진입 시 SplitTable 노트 로드 (root_lot_id 키).
  useEffect(() => {
    if (mode === "lot" && selectedLot && thread.length > 0) {
      const prod = (thread.find(x => x.product) || {}).product || "";
      if (!prod) { setSplitNotes([]); return; }
      sf("/api/splittable/notes?product=" + encodeURIComponent(prod) + "&root_lot_id=" + encodeURIComponent(selectedLot))
        .then(d => setSplitNotes(d.notes || []))
        .catch(() => setSplitNotes([]));
    } else {
      setSplitNotes([]);
    }
  }, [mode, selectedLot, thread]);

  // v8.8.0: 사이드바에 표시할 제품 담당자 — 모든 product 한꺼번에 로드.
  const loadProductContacts = () => {
    sf("/api/informs/product-contacts")
      .then(d => setProductContacts(d.products || {}))
      .catch(() => setProductContacts({}));
  };
  useEffect(() => { loadProductContacts(); }, []);

  const saveContact = () => {
    if (!editContact) return;
    const { id, product, name, role, email, phone, note } = editContact;
    if (!product || !name) { alert("product/name 필수"); return; }
    const url = id
      ? "/api/informs/product-contacts/update?id=" + encodeURIComponent(id)
      : "/api/informs/product-contacts";
    postJson(url, { product, name, role, email, phone, note })
      .then(() => {
        // v8.8.7: 담당자가 붙은 제품은 카탈로그에도 자동 등록 (이미 있으면 no-op).
        //   이렇게 하면 새 인폼 폼 드롭다운에 바로 노출됨 (이전엔 productContacts 키에만 존재해서 누락).
        if (!(constants.products || []).includes(product)) {
          postJson(API + "/products/add", { product })
            .then(d => setConstants(c => ({ ...c, products: d.products || c.products })))
            .catch(() => {});
        }
        setEditContact(null);
        loadProductContacts();
      })
      .catch(e => alert("저장 실패: " + (e.message || e)));
  };
  const deleteContact = (product, id) => {
    if (!confirm("담당자를 삭제하시겠어요?")) return;
    postJson("/api/informs/product-contacts/delete?id=" + encodeURIComponent(id) + "&product=" + encodeURIComponent(product), {})
      .then(() => loadProductContacts())
      .catch(e => alert("삭제 실패: " + (e.message || e)));
  };

  // v8.8.2: 유저/그룹 혼합 일괄 추가 모달.
  const openBulkPick = (product) => {
    setBulkPickProduct(product);
    setBulkSelUsers([]); setBulkSelGroups([]); setBulkRole("");
    // v8.8.19: 인폼 담당자 전용 필터 (/api/informs/eligible-contacts) — admin 역할 + admin/hol/test 포함 username 제외.
    sf("/api/informs/eligible-contacts").then(d => setBulkEligibleUsers(d.users || [])).catch(() => setBulkEligibleUsers([]));
    sf("/api/groups/list").then(d => setBulkGroups(d.groups || [])).catch(() => setBulkGroups([]));
  };
  const runBulkAdd = () => {
    if (!bulkPickProduct) return;
    if (bulkSelUsers.length === 0 && bulkSelGroups.length === 0) { alert("유저 또는 그룹을 선택하세요."); return; }
    setBulkBusy(true);
    postJson("/api/informs/product-contacts/bulk-add", {
      product: bulkPickProduct,
      usernames: bulkSelUsers,
      group_ids: bulkSelGroups,
      role: bulkRole,
    })
      .then(r => {
        setBulkBusy(false);
        // v8.8.7: bulk add 도 동일 — 제품이 카탈로그에 없으면 자동 등록.
        if (!(constants.products || []).includes(bulkPickProduct)) {
          postJson(API + "/products/add", { product: bulkPickProduct })
            .then(d => setConstants(c => ({ ...c, products: d.products || c.products })))
            .catch(() => {});
        }
        const msg = `추가 ${r.added?.length || 0}명 / 스킵 ${r.skipped?.length || 0}명 (중복/차단).`;
        alert(msg);
        setBulkPickProduct("");
        loadProductContacts();
      })
      .catch(e => { setBulkBusy(false); alert("추가 실패: " + (e.message || e)); });
  };

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
    const lot = (form.lot_id || "").trim();
    if (!lot || (!form.text.trim() && createImages.length === 0)) {
      setMsg("Lot 과 내용(또는 이미지)을 입력하세요."); return;
    }
    const body = {
      wafer_id: "", lot_id: lot, product: form.product.trim(),
      module: form.module, reason: form.reason, text: form.text, parent_id: null,
      images: createImages,
    };
    if (form.attach_split && (form.split.column || form.split.new_value)) {
      body.splittable_change = { ...form.split, applied: false };
    }
    if (form.attach_embed && form.embed && (form.embed.columns.length || form.embed.rows.length)) {
      body.embed_table = form.embed;
    }
    // v8.8.15: fab_lot_id 스냅샷 — 입력값이 fab_lot_id 포맷이면 그대로 전달. 아니면 서버가 root5 기준 resolve.
    {
      const isFabLot = lot.length > 5 || /[._\-/]/.test(lot);
      if (isFabLot) body.fab_lot_id_at_save = lot;
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
      // v8.7.9: lot mode 로 바로 이동 (wafer mode 는 폐지).
      setMode("lot"); setSelectedLot(lot.slice(0, 5));
      setTimeout(refreshAll, 50);
    }).catch(e => setMsg(e.message));
  };

  // v8.8.0: 본문 textarea 에 이미지 Ctrl+V 붙여넣기 → 업로드 후 본문에 markdown 으로 즉시 inline 삽입.
  // (별도 첨부 카드 X. images 배열에도 추가해 메일 첨부 후보로 유지.)
  const handleBodyPaste = async (e) => {
    const items = (e.clipboardData && e.clipboardData.items) || [];
    const imgs = [];
    for (const it of items) {
      if (it.kind === "file" && /^image\//.test(it.type || "")) {
        const file = it.getAsFile();
        if (file) imgs.push(file);
      }
    }
    if (!imgs.length) return;
    e.preventDefault();
    setUploadingMain(true);
    const ta = e.currentTarget;
    const start = ta.selectionStart ?? (form.text || "").length;
    const end   = ta.selectionEnd   ?? start;
    let inserted = "";
    const out = [];
    for (const f of imgs) {
      try {
        const fd = new FormData();
        const ext = (f.type || "image/png").split("/")[1] || "png";
        const named = new File([f], `paste_${Date.now()}.${ext}`, { type: f.type });
        fd.append("file", named);
        const res = await sf("/api/informs/upload", { method: "POST", body: fd });
        out.push({ filename: res.filename, url: res.url, size: res.size });
        inserted += `\n![${res.filename}](${res.url})\n`;
      } catch (err) { alert("붙여넣기 업로드 실패: " + err.message); }
    }
    // 본문 inline 삽입 (커서 위치 보존).
    setForm(f => {
      const cur = f.text || "";
      const next = cur.slice(0, start) + inserted + cur.slice(end);
      return { ...f, text: next };
    });
    if (out.length) setCreateImages(prev => [...prev, ...out]);
    setUploadingMain(false);
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

  // v8.8.8: product + lot_id 쌍이 유효하면 /api/splittable/view 로 현재 시점 SplitTable 스냅샷(plan 포함)
  //   을 자동으로 fetch → form.embed 에 attach. 사용자가 "가져오기" 버튼을 누르는 단계 제거.
  //   lot_id 변경이나 product 변경 시 재실행. 실패하면 조용히 attach 끔 (에러 배너 X).
  // v8.8.11: SplitTable prefix (KNOB/MASK/INLINE/VM/FAB/ALL) 또는 CUSTOM 프리셋 중 하나 선택.
  //          변경 시 useEffect 가 해당 scope 로 /view 재호출.
  const [embedPrefix, setEmbedPrefix] = useState("ALL");   // string | null (CUSTOM 모드면 null)
  const [embedCustomName, setEmbedCustomName] = useState(""); // "" = prefix 모드
  const [customsList, setCustomsList] = useState([]);
  // v8.8.16: 인폼 전용 인라인 CUSTOM 편집기 — SplitTable 사이드바와 동일 UX.
  //   embedCustomCols: 현재 편집중 컬럼 리스트 (set 선택 시 로드). embedCustomSearch: 컬럼 필터링.
  //   embedSchemaCols: 제품 전체 스키마 (lot 없이 fetch).
  //   embedCustomOpen: 편집기 접힘/펼침 상태.
  //   snapshotTick: 사용자가 "Search" 버튼 누르면 증가 → useEffect 가 스냅샷 재fetch.
  const [embedCustomCols, setEmbedCustomCols] = useState([]);
  const [embedCustomSearch, setEmbedCustomSearch] = useState("");
  const [embedSchemaCols, setEmbedSchemaCols] = useState([]);
  const [embedCustomOpen, setEmbedCustomOpen] = useState(false);
  const [snapshotTick, setSnapshotTick] = useState(0);

  useEffect(() => {
    sf("/api/splittable/customs").then(d => setCustomsList(d.customs || [])).catch(() => {});
  }, []);

  // v8.8.16: 제품 변경 시 ML_TABLE 전체 스키마 fetch — CUSTOM 컬럼 선택 pool.
  useEffect(() => {
    const prod = (form.product || "").trim();
    if (!prod) { setEmbedSchemaCols([]); return; }
    const mlProd = prod.startsWith("ML_TABLE_") ? prod : `ML_TABLE_${prod}`;
    sf(`/api/splittable/schema?product=${encodeURIComponent(mlProd)}`)
      .then(d => setEmbedSchemaCols((d.columns || []).map(c => c.name || c)))
      .catch(() => setEmbedSchemaCols([]));
  }, [form.product]);

  useEffect(() => {
    const prod = (form.product || "").trim();
    const lot  = (form.lot_id || "").trim();
    if (!creating) { setEmbedFetching(false); return; }
    if (!prod || !lot) {
      setEmbedFetching(false);
      setForm(f => (f.attach_embed && f.embed?.source?.startsWith?.("SplitTable/"))
        ? { ...f, attach_embed: false, embed: { source: "", columns: [], rows: [], note: "" } }
        : f);
      return;
    }
    const mlProd = prod.startsWith("ML_TABLE_") ? prod : `ML_TABLE_${prod}`;
    // v8.8.13: root_lot_id 단독 입력 지원.
    //   - 입력값이 fab_lot_id 포맷(영문+숫자+구분자 포함, 길이>5)이면 fab_lot_id 로,
    //     root_lot_id 포맷(짧거나 구분자 없음)이면 root_lot_id 로만 사용.
    //   - 그동안 입력값을 항상 fab_lot_id 로 붙여서 root_lot_id("A0001") 입력 시 매칭 실패 → 빈 embed.
    const isFabLot = lot.length > 5 || /[._\-/]/.test(lot);
    const root5 = isFabLot ? lot.slice(0, 5) : lot;
    const handle = setTimeout(() => {
      setEmbedFetching(true);
      const params = new URLSearchParams();
      params.set("product", mlProd);
      params.set("root_lot_id", root5);
      if (isFabLot) params.set("fab_lot_id", lot);
      params.set("view_mode", "all");
      // v8.8.17: 인폼 scope 는 CUSTOM 전용 — 항상 ALL 로 서버에서 받아온 뒤
      //   FE 에서 embedCustomCols 로 필터링한다. prefix chip / saved CUSTOM 제거.
      params.set("prefix", "ALL");
      sf(`/api/splittable/view?${params.toString()}`)
        .then(d => {
          if (!d || !d.rows || d.rows.length === 0) {
            setEmbedFetching(false);
            setForm(f => (f.attach_embed && f.embed?.source?.startsWith?.("SplitTable/"))
              ? { ...f, attach_embed: false, embed: { source: "", columns: [], rows: [], note: "" } }
              : f);
            return;
          }
          // v8.8.11: SplitTable 원형 응답을 st_view 로 보존 — EmbedTableView 가 컬러링/plan-pin 동일 렌더.
          // 병행해서 legacy 2D (columns/rows) 도 유지 — 구버전 렌더러 호환.
          const headers = d.headers || [];
          const cols = ["parameter", ...headers];
          // v8.8.17: CUSTOM 모드 전용 — embedCustomCols 로 row 필터 + 빈 열 생성.
          //   컬럼 미선택이면 빈 상태 (row=0) 로 두어 사용자가 컬럼을 고르게 유도.
          let rowsAll = d.rows || [];
          if (embedCustomCols.length > 0) {
            const keep = new Set(embedCustomCols);
            const filtered = rowsAll.filter(r => keep.has(r._param));
            const byParam = new Map(filtered.map(r => [r._param, r]));
            rowsAll = embedCustomCols.map(p => byParam.get(p) || { _param: p, _cells: {} });
          } else {
            // 미선택 — 비어있는 프리뷰 보여서 컬럼 선택을 유도.
            rowsAll = [];
          }
          const rows = rowsAll.map(r => {
            const out = [r._param || ""];
            headers.forEach((_, i) => {
              const cell = (r._cells && r._cells[i]) || {};
              const v = cell.actual ?? "";
              const pv = cell.plan;
              out.push(pv != null && pv !== "" && pv !== v ? `${v} → ${pv}` : String(v ?? ""));
            });
            return out;
          });
          const scopeLabel = embedCustomCols.length > 0
            ? `CUSTOM(${embedCustomCols.length})`
            : "CUSTOM(미선택)";
          const lotLabel = isFabLot ? `fab_lot=${lot}` : `root_lot=${lot}`;
          setForm(f => ({
            ...f, attach_embed: true,
            embed: {
              source: `SplitTable/${mlProd} @ ${lot} · ${scopeLabel} (auto)`,
              columns: cols, rows,
              note: `auto-snapshot · ${rowsAll.length} params · ${lotLabel} · scope=${scopeLabel}`,
              st_view: {
                headers,
                rows: rowsAll,
                wafer_fab_list: d.wafer_fab_list || [],
                header_groups: d.header_groups || [],
              },
              st_scope: { prefix: "", custom_name: "", inline_cols: embedCustomCols },
            },
          }));
          setEmbedFetching(false);
        })
        .catch(() => { setEmbedFetching(false); });
    }, 400);
    return () => { clearTimeout(handle); setEmbedFetching(false); };
    // v8.8.16: snapshotTick 변경 시에도 재fetch — 사용자가 Search 버튼으로 명시적 갱신.
  }, [form.product, form.lot_id, creating, embedCustomCols, snapshotTick]);

  // v8.8.10: SplitTable 의 lot-candidates 로 root_lot_id + fab_lot_id 후보 fetch → Lot 드롭다운 소스.
  //   기존 /product-lots (RAWDATA_DB 폴더 스캔) 은 사내 실환경에서 빈 결과 자주 발생 → SplitTable 기반 primary.
  const [lotOptions, setLotOptions] = useState([]);  // [{value, type:"root"|"fab"}]
  useEffect(() => {
    const prod = (form.product || "").trim();
    if (!prod) { setLotOptions([]); return; }
    const mlProd = prod.startsWith("ML_TABLE_") ? prod : `ML_TABLE_${prod}`;
    Promise.all([
      sf(`/api/splittable/lot-candidates?product=${encodeURIComponent(mlProd)}&col=root_lot_id&limit=200`).catch(() => ({ candidates: [] })),
      sf(`/api/splittable/lot-candidates?product=${encodeURIComponent(mlProd)}&col=fab_lot_id&limit=500`).catch(() => ({ candidates: [] })),
    ]).then(([rr, fr]) => {
      const out = [];
      const seen = new Set();
      for (const v of (rr.candidates || [])) {
        const s = String(v || "").trim();
        if (s && !seen.has(s)) { seen.add(s); out.push({ value: s, type: "root" }); }
      }
      for (const v of (fr.candidates || [])) {
        const s = String(v || "").trim();
        if (s && !seen.has(s)) { seen.add(s); out.push({ value: s, type: "fab" }); }
      }
      setLotOptions(out);
    });
  }, [form.product]);

  // v8.8.0: SplitTable 에서 현재 product 의 plan 스냅샷을 본문에 임베드.
  // 빈 history 인 경우 명시적으로 알림 + paste 폴백 제안.
  const embedFromSplitTable = async () => {
    const prod = (form.product || "").trim();
    if (!prod) { alert("product 를 먼저 입력하세요."); return; }
    setEmbedFetching(true);
    try {
      const hist = await sf("/api/splittable/history?product=" + encodeURIComponent(prod) + "&limit=100");
      const all = (hist.history || []);
      const rows = all.slice(-50).map(h => [
        (h.time || "").replace("T", " ").slice(0, 19),
        h.user || "", h.action || "", h.cell || "",
        h.old === null || h.old === undefined ? "" : String(h.old),
        h.new === null || h.new === undefined ? "" : String(h.new),
        h.root_lot_id || "",
      ]);
      if (rows.length === 0) {
        // 폴백: 빈 history 면 paste 모드로 전환 — 사용자가 직접 표 데이터 붙여넣기.
        const want = window.confirm(
          `'${prod}' 의 SplitTable 히스토리가 비어 있습니다.\n\n대신 표 데이터를 직접 붙여넣을까요?\n(Excel/SplitTable 셀 영역을 복사한 뒤 OK → 붙여넣기 모달이 열립니다)`
        );
        if (want) setPasteOpen(true);
        return;
      }
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

  // v8.8.0/v8.8.6: TSV/CSV paste → embed_table. 첫 줄 = 컬럼.
  //   v8.8.6: 세트 저장/조회가 팀 공용 `/api/splittable/paste-sets` 로 이전.
  //           LocalStorage 는 fallback (서버 실패 시만).
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const [pasteSetName, setPasteSetName] = useState("");
  const [pasteSets, setPasteSets] = useState([]);
  const reloadPasteSets = () => {
    const prod = encodeURIComponent(form.product || "");
    sf("/api/splittable/paste-sets?product=" + prod)
      .then(d => setPasteSets(d.sets || []))
      .catch(() => {
        // fallback: localStorage legacy
        try { setPasteSets(JSON.parse(localStorage.getItem("flow_paste_sets_v1") || "[]")); } catch {}
      });
  };
  useEffect(() => { if (pasteOpen) reloadPasteSets(); }, [pasteOpen, form.product]);
  const applyPasteAsEmbed = () => {
    const txt = (pasteText || "").trim();
    if (!txt) { alert("표 데이터를 먼저 붙여넣으세요"); return; }
    const lines = txt.split(/\r?\n/).filter(l => l.length);
    if (lines.length < 2) { alert("최소 2줄 (헤더 + 1행) 이 필요합니다"); return; }
    const sep = lines[0].includes("\t") ? "\t" : (lines[0].includes(",") ? "," : "\t");
    const cols = lines[0].split(sep);
    const rows = lines.slice(1).map(l => l.split(sep));
    setForm(f => ({
      ...f, attach_embed: true,
      embed: {
        source: pasteSetName.trim() || `paste/${(form.product || "manual")}`,
        columns: cols, rows,
        note: `${rows.length} rows pasted${pasteSetName.trim() ? ` (set: ${pasteSetName.trim()})` : ""}`,
      },
    }));
    // v8.8.6: 이름 주어지면 팀 공용 세트에 저장 (BE + LocalStorage 폴백).
    if (pasteSetName.trim()) {
      const payload = { name: pasteSetName.trim(), product: form.product || "", columns: cols, rows, username: user?.username || "" };
      postJson("/api/splittable/paste-sets/save", payload)
        .catch(() => {
          try {
            const KEY = "flow_paste_sets_v1";
            const cur = JSON.parse(localStorage.getItem(KEY) || "[]");
            const next = [{ ...payload, saved_at: new Date().toISOString() },
                          ...cur.filter(s => s.name !== pasteSetName.trim())].slice(0, 50);
            localStorage.setItem(KEY, JSON.stringify(next));
          } catch {}
        });
    }
    setPasteOpen(false); setPasteText(""); setPasteSetName("");
  };
  // legacy helper (pasteSets state 가 주력이지만 일부 호출부가 함수 호출 형태 유지).
  const loadPasteSets = () => pasteSets;

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

  /* 모듈 필터: rootsSorted 를 2차 필터링
     v8.8.13: (1) '전체' 체크(= 내 권한 모든 모듈 on) 이면 패스스루 — module 없는 인폼도 모두 노출.
              (2) 부분 선택이면 체크된 모듈만. module 값이 없는/체크 외 모듈 인폼은 숨김.
     v8.8.16: 모듈/제품 필터를 **둘 다** 완전히 비우면 아무것도 표시하지 않음
              (이전: 빈 배열 → 전체 노출 → 메일 전송/작업 실수 가능). 명시적 선택을 요구. */
  const applyModFilter = (arr) => {
    // v8.8.16: 둘 다 비어있으면 빈 배열 반환 — 사용자가 최소 하나 이상 고르도록 유도.
    if ((!moduleFilter || moduleFilter.length === 0)
        && (!productFilter || productFilter.length === 0)) {
      return [];
    }
    let out = arr;
    // module filter
    if (moduleFilter && moduleFilter.length > 0) {
      const allowed = (myMods.all_rounder || isAdmin)
        ? (constants.modules || [])
        : (myMods.modules || []).filter(m => (constants.modules || []).includes(m));
      const allSelected = allowed.length > 0 && allowed.every(m => moduleFilter.includes(m));
      if (!allSelected) {
        out = out.filter(x => {
          const m = x.module || "";
          if (!m) return false;
          return moduleFilter.includes(m);
        });
      }
    }
    // v8.8.15: product filter — 빈 배열이면 패스스루, 아니면 product 값이 포함된 것만.
    if (productFilter && productFilter.length > 0) {
      out = out.filter(x => productFilter.includes(x.product || ""));
    }
    return out;
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

  // v8.8.13: admin 본문 수정. text 만 바뀌고 module/reason/embed/시각 은 원본 유지 (스냅샷 잠금).
  const editInform = (id, patch) => sf(API + "/edit?id=" + encodeURIComponent(id), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch || {}),
  }).then(refreshAll).catch(e => alert(e.message));

  const changeStatus = (id, status, note) => sf(API + "/status?id=" + encodeURIComponent(id), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status, note: note || "" }),
  }).then(refreshAll).catch(e => alert(e.message));

  // v8.7.9: deadline 폐지. 기존 changeDeadline 은 사용되지 않아 제거.

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
      <PageGear title="인폼 설정" canEdit={isAdmin} position="bottom-right">
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
        {/* v8.8.13: admin 전용 — 유저별 인폼 모듈 조회 권한 편집. */}
        {isAdmin && <UserModulePermsPanel allModules={constants.modules || []} />}
        {/* v8.8.17: admin 전용 — 사유별 메일 제목/본문 템플릿 편집. */}
        {isAdmin && (
          <ReasonTemplatesPanel
            reasons={constants.reasons || []}
            templates={constants.reason_templates || {}}
            onSave={(rt) => {
              sf(API + "/config", { method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ reason_templates: rt }) })
                .then(d => setConstants(c => ({ ...c, reason_templates: (d.config && d.config.reason_templates) || rt })))
                .catch(e => alert("저장 실패: " + e.message));
            }}
          />
        )}
      </PageGear>
      {/* Sidebar */}
      <div style={{ width: 340, minWidth: 300, borderRight: "1px solid var(--border)", background: "var(--bg-secondary)", display: "flex", flexDirection: "column" }}>
        <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span style={{ fontSize: 14, fontWeight: 700, fontFamily: "monospace", color: "var(--accent)" }}>{">"} 인폼 로그</span>
          <button onClick={() => {
            // v8.8.3 bugfix: 폼 열기 전 /config 를 갱신해 product 카탈로그를 최신화.
            sf(API + "/config").then(d => setConstants(c => ({
              ...c,
              modules: d.modules || c.modules,
              reasons: d.reasons || c.reasons,
              products: d.products || c.products,
              raw_db_root: d.raw_db_root ?? c.raw_db_root,
            }))).catch(() => {});
            setCreating(true);
          }} style={{ padding: "4px 12px", borderRadius: 5, border: "none", background: "var(--accent)", color: "#fff", fontSize: 11, fontWeight: 600, cursor: "pointer" }}>+ 신규</button>
        </div>

        <div style={{ padding: "8px 10px", borderBottom: "1px solid var(--border)", display: "flex", flexWrap: "wrap", gap: 4 }}>
          {modeButton("all",     "전체",    "최근 루트 인폼 (역할 필터 적용)")}
          {modeButton("product", "제품",  "제품 → Lot → Wafer drill-down")}
          {modeButton("lot",     "Lot",    "LOT 으로 전체 인폼 검색")}
          {modeButton("gantt",   "이력 타임라인", "시간순 이력 타임라인 (등록·확인·메일·댓글)")}
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

        {/* v8.8.0: 제품별 담당자 — 사이드바 하단 폴더블. 모든 유저가 +추가/수정/삭제 가능.
            v8.8.3: 제품 목록을 새 인폼 폼과 동일 소스(카탈로그+실제 기록+담당자 등록)로 통일.
                    `+제품` 버튼은 단순 담당자 모달을 여는 게 아니라 /products/add 로 카탈로그에 등록
                    → 새 인폼 폼 드롭다운에도 즉시 반영. 각 행에 `🗑` 제거 버튼 추가. */}
        <div style={{ borderTop: "2px solid var(--border)", maxHeight: 360, overflowY: "auto", background: "var(--bg-tertiary)" }}>
          <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontSize: 12, fontWeight: 700, color: "var(--accent)", fontFamily: "monospace" }}>👥 제품 · 담당자</span>
            {(() => {
              const all = Array.from(new Set([
                ...(constants.products || []),
                ...(products || []).map(p => typeof p === "string" ? p : p.product).filter(Boolean),
                ...Object.keys(productContacts || {}),
              ]));
              return <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>{all.length} 제품</span>;
            })()}
          </div>
          {(() => {
            const unified = Array.from(new Set([
              ...(constants.products || []),
              ...(products || []).map(p => typeof p === "string" ? p : p.product).filter(Boolean),
              ...Object.keys(productContacts || {}),
            ])).sort();
            if (unified.length === 0) {
              return (
                <div style={{ padding: 14, fontSize: 11, color: "var(--text-secondary)", textAlign: "center" }}>
                  등록된 제품 없음 — 아래 + 로 추가
                </div>
              );
            }
            return unified.map(prod => {
              const arr = productContacts[prod] || [];
              const open = !!openContactProducts[prod];
              const inCatalog = (constants.products || []).includes(prod);
              return (
                <div key={prod} style={{ borderBottom: "1px solid var(--border)" }}>
                  <div style={{ padding: "6px 10px", display: "flex", alignItems: "center", gap: 6, cursor: "pointer", background: open ? "var(--bg-secondary)" : "transparent" }}
                       onClick={() => setOpenContactProducts(o => ({ ...o, [prod]: !o[prod] }))}>
                    <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>{open ? "▼" : "▶"}</span>
                    <span style={{ flex: 1, fontSize: 12, fontWeight: 600, fontFamily: "monospace" }}>{prod}</span>
                    <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>{arr.length}</span>
                    {/* v8.8.13: 두 버튼(+ / 👥) 통합 — 사람 앞에 '+' 가 붙은 단일 추가 버튼. bulk 모달에서 유저/그룹 선택. */}
                    <span onClick={(e) => { e.stopPropagation(); openBulkPick(prod); }}
                          title="담당자 추가 — 유저/그룹에서 일괄 선택"
                          style={{ fontSize: 11, padding: "1px 8px", borderRadius: 4, background: "var(--accent)", color: "#fff", fontWeight: 700, cursor: "pointer" }}>+👤</span>
                    {/* v8.8.12: 삭제 버튼 항상 노출. catalog 에 있으면 /products/delete,
                          productContacts 만 있으면 해당 제품의 contacts 를 모두 개별 삭제해 key 제거. */}
                    <span onClick={(e) => {
                            e.stopPropagation();
                            if (!window.confirm(`"${prod}" 을(를) 제거하시겠어요?\n(담당자 목록 + 카탈로그 등록 모두 삭제. 기존 인폼 레코드는 유지.)`)) return;
                            const contacts = (productContacts[prod] || []);
                            const delContactReqs = contacts.map(c =>
                              sf("/api/informs/product-contacts/delete?id=" + encodeURIComponent(c.id) + "&product=" + encodeURIComponent(prod), { method: "POST" }).catch(() => {})
                            );
                            Promise.all(delContactReqs).then(() => {
                              if (inCatalog) {
                                postJson(API + "/products/delete", { product: prod })
                                  .then(d => setConstants(c => ({ ...c, products: d.products || c.products })))
                                  .catch(() => {});
                              }
                              loadProductContacts();
                            });
                          }}
                          title="제품 삭제 (담당자 + 카탈로그)"
                          style={{ fontSize: 10, padding: "1px 6px", borderRadius: 4, background: "transparent", color: "#ef4444", fontWeight: 700, cursor: "pointer", border: "1px solid #ef4444" }}>🗑</span>
                  </div>
                  {open && arr.length === 0 && (
                    <div style={{ padding: "6px 14px 8px 24px", fontSize: 10, color: "var(--text-secondary)" }}>담당자 없음</div>
                  )}
                  {open && arr.map(c => (
                    <div key={c.id} style={{ padding: "5px 14px 5px 24px", display: "flex", flexDirection: "column", borderTop: "1px dashed var(--border)" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                        <span style={{ fontSize: 11, fontWeight: 600 }}>{c.name}</span>
                        {c.role && <span style={{ fontSize: 9, padding: "1px 5px", borderRadius: 6, background: "var(--accent)22", color: "var(--accent)", fontWeight: 700 }}>{c.role}</span>}
                        <span style={{ flex: 1 }} />
                        <span onClick={() => setEditContact({ id: c.id, product: prod, name: c.name, role: c.role || "", email: c.email || "", phone: c.phone || "", note: c.note || "" })}
                              style={{ fontSize: 9, color: "var(--text-secondary)", cursor: "pointer" }}>수정</span>
                        <span onClick={() => deleteContact(prod, c.id)}
                              style={{ fontSize: 9, color: "#ef4444", cursor: "pointer" }}>삭제</span>
                      </div>
                      {(c.email || c.phone) && (
                        <div style={{ fontSize: 9, color: "var(--text-secondary)", fontFamily: "monospace", marginTop: 1 }}>
                          {c.email}{c.email && c.phone ? " · " : ""}{c.phone}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              );
            });
          })()}
          <div style={{ padding: 8, display: "flex", gap: 6 }}>
            <input id="__pc_new_prod" placeholder="신규 제품명 (카탈로그 등록)"
              style={{ flex: 1, minWidth: 0, padding: "5px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 11, fontFamily: "monospace" }}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  const v = e.target.value.trim();
                  if (!v) return;
                  postJson(API + "/products/add", { product: v })
                    .then(d => {
                      setConstants(c => ({ ...c, products: d.products || c.products }));
                      e.target.value = "";
                    })
                    .catch(err => alert(err.message));
                }
              }} />
            <button onClick={() => {
              const inp = document.getElementById("__pc_new_prod");
              const v = (inp?.value || "").trim();
              if (!v) return;
              postJson(API + "/products/add", { product: v })
                .then(d => {
                  setConstants(c => ({ ...c, products: d.products || c.products }));
                  if (inp) inp.value = "";
                })
                .catch(err => alert(err.message));
            }} style={{ padding: "5px 10px", borderRadius: 4, border: "1px solid var(--accent)", background: "transparent", color: "var(--accent)", fontSize: 10, fontWeight: 600, cursor: "pointer" }}>+제품</button>
          </div>
        </div>
      </div>

      {/* v8.8.0: 담당자 추가/수정 모달 */}
      {/* v8.8.0: 표 붙여넣기 모달 — TSV/CSV → embed_table */}
      {pasteOpen && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.55)", zIndex: 3100, display: "flex", alignItems: "center", justifyContent: "center" }}
             onClick={() => setPasteOpen(false)}>
          <div onClick={(e) => e.stopPropagation()}
               style={{ background: "var(--bg-secondary)", borderRadius: 10, border: "1px solid var(--border)", padding: 18, width: 640, maxWidth: "94vw" }}>
            <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 8 }}>📋 표 붙여넣기 (TSV/CSV)</div>
            <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 8 }}>
              Excel/SplitTable 셀 영역을 복사한 뒤 아래에 Ctrl+V. 첫 줄이 컬럼명. 세트명을 지정하면 LocalStorage 에 저장되어 다음에 재사용 가능.
            </div>
            {loadPasteSets().length > 0 && (
              <div style={{ marginBottom: 8, padding: 8, background: "var(--bg-card)", borderRadius: 6, fontSize: 11 }}>
                <div style={{ fontSize: 10, color: "var(--text-secondary)", marginBottom: 4 }}>저장된 세트 ({loadPasteSets().length})</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
                  {loadPasteSets().map(s => (
                    <span key={s.name} onClick={() => {
                      setForm(f => ({ ...f, attach_embed: true, embed: { source: s.name, columns: s.columns, rows: s.rows, note: `${s.rows.length} rows reused` } }));
                      setPasteOpen(false);
                    }}
                    style={{ padding: "3px 10px", borderRadius: 999, fontSize: 10, cursor: "pointer", background: "var(--accent-glow)", color: "var(--accent)", fontWeight: 600 }}
                    title={`${s.product || ""} · ${s.rows.length} rows · ${s.saved_at?.slice(0,16)}`}>{s.name}</span>
                  ))}
                </div>
              </div>
            )}
            <input value={pasteSetName} onChange={e => setPasteSetName(e.target.value)}
              placeholder="세트 이름 (선택, 비우면 1회용)"
              style={{ width: "100%", padding: "6px 10px", marginBottom: 6, borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12, boxSizing: "border-box" }} />
            <textarea value={pasteText} onChange={e => setPasteText(e.target.value)}
              placeholder="여기에 Ctrl+V (첫 줄 = 헤더, 탭 또는 콤마 구분)"
              rows={10}
              style={{ width: "100%", padding: 10, borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 11, fontFamily: "monospace", boxSizing: "border-box", resize: "vertical" }} />
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 10 }}>
              <button onClick={() => { setPasteOpen(false); setPasteText(""); setPasteSetName(""); }}
                style={{ padding: "6px 14px", borderRadius: 5, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", fontSize: 12, cursor: "pointer" }}>취소</button>
              <button onClick={applyPasteAsEmbed}
                style={{ padding: "6px 14px", borderRadius: 5, border: "none", background: "var(--accent)", color: "#fff", fontSize: 12, fontWeight: 600, cursor: "pointer" }}>본문에 첨부</button>
            </div>
          </div>
        </div>
      )}
      {/* v8.8.2: 유저/그룹 혼합 일괄 추가 모달 */}
      {bulkPickProduct && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.55)", zIndex: 3050, display: "flex", alignItems: "center", justifyContent: "center" }}
             onClick={() => !bulkBusy && setBulkPickProduct("")}>
          <div onClick={(e) => e.stopPropagation()}
               style={{ background: "var(--bg-secondary)", borderRadius: 10, border: "1px solid var(--border)", padding: 18, width: 620, maxWidth: "94vw", maxHeight: "86vh", display: "flex", flexDirection: "column" }}>
            <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 6, fontFamily: "monospace" }}>
              👥 일괄 담당자 추가 <span style={{ color: "var(--accent)" }}>· {bulkPickProduct}</span>
            </div>
            <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 10 }}>
              개별 유저와 그룹을 혼합해 선택할 수 있습니다. 이미 등록된 담당자(동일 username/email) 는 자동으로 건너뜁니다. admin/test 계정은 제외됩니다.
            </div>
            <div style={{ display: "flex", gap: 10, flex: 1, minHeight: 280 }}>
              {/* 유저 풀 */}
              <div style={{ flex: 1, display: "flex", flexDirection: "column", border: "1px solid var(--border)", borderRadius: 6, overflow: "hidden" }}>
                <div style={{ padding: "6px 10px", fontSize: 11, fontWeight: 700, background: "var(--bg-primary)", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between" }}>
                  <span>👤 유저 ({bulkEligibleUsers.length})</span>
                  <span style={{ color: "var(--accent)", cursor: "pointer", fontWeight: 600 }}
                        onClick={() => setBulkSelUsers(bulkSelUsers.length === bulkEligibleUsers.length ? [] : bulkEligibleUsers.map(u => u.username))}>
                    {bulkSelUsers.length === bulkEligibleUsers.length ? "전체 해제" : "전체 선택"}
                  </span>
                </div>
                <div style={{ flex: 1, overflowY: "auto" }}>
                  {bulkEligibleUsers.map(u => {
                    const sel = bulkSelUsers.includes(u.username);
                    return (
                      <div key={u.username} onClick={() => setBulkSelUsers(sel ? bulkSelUsers.filter(x => x !== u.username) : [...bulkSelUsers, u.username])}
                           style={{ padding: "5px 10px", fontSize: 11, cursor: "pointer", background: sel ? "var(--accent-glow)" : "transparent", borderBottom: "1px dashed var(--border)", display: "flex", alignItems: "center", gap: 6 }}>
                        <input type="checkbox" readOnly checked={sel} />
                        <span style={{ fontFamily: "monospace", fontWeight: 600 }}>{u.username}</span>
                        {u.email && <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>· {u.email}</span>}
                      </div>
                    );
                  })}
                </div>
              </div>
              {/* 그룹 풀 */}
              <div style={{ flex: 1, display: "flex", flexDirection: "column", border: "1px solid var(--border)", borderRadius: 6, overflow: "hidden" }}>
                <div style={{ padding: "6px 10px", fontSize: 11, fontWeight: 700, background: "var(--bg-primary)", borderBottom: "1px solid var(--border)" }}>
                  🏷 그룹 ({bulkGroups.length}) — 선택 시 해당 그룹 멤버 전체 합류
                </div>
                <div style={{ flex: 1, overflowY: "auto" }}>
                  {bulkGroups.map(g => {
                    const sel = bulkSelGroups.includes(g.id);
                    return (
                      <div key={g.id} onClick={() => setBulkSelGroups(sel ? bulkSelGroups.filter(x => x !== g.id) : [...bulkSelGroups, g.id])}
                           style={{ padding: "5px 10px", fontSize: 11, cursor: "pointer", background: sel ? "var(--accent-glow)" : "transparent", borderBottom: "1px dashed var(--border)", display: "flex", alignItems: "center", gap: 6 }}>
                        <input type="checkbox" readOnly checked={sel} />
                        <span style={{ fontWeight: 600 }}>{g.name}</span>
                        <span style={{ fontSize: 10, color: "var(--text-secondary)", marginLeft: "auto" }}>{(g.members || []).length}명</span>
                      </div>
                    );
                  })}
                  {bulkGroups.length === 0 && <div style={{ padding: 18, fontSize: 11, color: "var(--text-secondary)", textAlign: "center" }}>볼 수 있는 그룹 없음</div>}
                </div>
              </div>
            </div>
            <div style={{ marginTop: 10, display: "flex", gap: 8, alignItems: "center" }}>
              <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>역할(선택):</span>
              <input value={bulkRole} onChange={(e) => setBulkRole(e.target.value)}
                     placeholder="예: PIE, 측정 (비우면 유저 기본 role)"
                     style={{ flex: 1, padding: "5px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12 }} />
            </div>
            <div style={{ marginTop: 12, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>
                선택: 유저 {bulkSelUsers.length} · 그룹 {bulkSelGroups.length}
              </span>
              <div style={{ display: "flex", gap: 8 }}>
                <button onClick={() => !bulkBusy && setBulkPickProduct("")}
                  style={{ padding: "6px 14px", borderRadius: 5, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", fontSize: 12, cursor: bulkBusy ? "not-allowed" : "pointer" }}>취소</button>
                <button onClick={runBulkAdd} disabled={bulkBusy}
                  style={{ padding: "6px 16px", borderRadius: 5, border: "none", background: bulkBusy ? "var(--border)" : "var(--accent)", color: "#fff", fontSize: 12, fontWeight: 700, cursor: bulkBusy ? "not-allowed" : "pointer" }}>
                  {bulkBusy ? "추가 중…" : "일괄 추가"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
      {editContact && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.55)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 3000 }}
             onClick={() => setEditContact(null)}>
          <div onClick={(e) => e.stopPropagation()}
               style={{ background: "var(--bg-secondary)", borderRadius: 10, border: "1px solid var(--border)", padding: 18, width: 380, maxWidth: "92vw" }}>
            <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 12, fontFamily: "monospace" }}>
              {editContact.id ? "✏ 담당자 수정" : "+ 담당자 추가"} <span style={{ color: "var(--accent)" }}>· {editContact.product}</span>
            </div>
            {/* v8.8.17: 아이디(username=email) + 역할 2필드로 간소화.
                  이메일/전화/메모 제거 — username 이 곧 사내 메일이므로 이메일 컬럼 불필요. */}
            {[
              ["name", "아이디 (필수 · 사내 email id)"],
              ["role", "역할 (예: PIE, 측정)"],
            ].map(([k, ph]) => (
              <input key={k} placeholder={ph}
                value={editContact[k] || ""}
                onChange={(e) => setEditContact({ ...editContact, [k]: e.target.value })}
                style={{ display: "block", width: "100%", padding: "8px 10px", marginBottom: 8, borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12, boxSizing: "border-box" }} />
            ))}
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button onClick={() => setEditContact(null)}
                style={{ padding: "6px 14px", borderRadius: 5, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", fontSize: 12, cursor: "pointer" }}>취소</button>
              <button onClick={saveContact}
                style={{ padding: "6px 14px", borderRadius: 5, border: "none", background: "var(--accent)", color: "#fff", fontSize: 12, fontWeight: 600, cursor: "pointer" }}>저장</button>
            </div>
          </div>
        </div>
      )}

      {/* Main */}
      <div style={{ flex: 1, overflowY: "auto", padding: 24 }}>
        {creating && (
          <div style={{ background: "var(--bg-secondary)", borderRadius: 10, border: "1px solid var(--border)", padding: 18, marginBottom: 18 }}>
            <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 10 }}>새 인폼</div>
            {/* v8.7.9: product + lot_id 2개만. wafer_id 제거. lot_id 는 root/fab 어느 쪽이든 OK — 앞 5자가 root_lot_id. */}
            {/* v8.8.1: 제품명은 등록된 카탈로그(선택), Lot 은 RAWDATA_DB 에서 로드(선택 + 자유입력 병행). */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 8 }}>
              <div style={{ display: "flex", gap: 6 }}>
                <select value={form.product}
                  onChange={e => {
                    const p = e.target.value;
                    setForm({ ...form, product: p, lot_id: "" });
                    if (p) {
                      sf(API + "/product-lots?product=" + encodeURIComponent(p))
                        .then(d => setProductLots({ product: p, lots: d.lots || [], source: d.source || "" }))
                        .catch(() => setProductLots({ product: p, lots: [], source: "" }));
                    } else {
                      setProductLots({ product: "", lots: [], source: "" });
                    }
                  }}
                  style={{ flex: 1, padding: "8px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12, fontFamily: "monospace" }}>
                  <option value="">-- 제품 선택 --</option>
                  {/* v8.8.3 / v8.8.7: 카탈로그 + 실제 사용 제품 + 담당자 등록 제품 unified.
                      담당자 패널에서만 제품을 만든 경우에도 여기 드롭다운에 바로 노출되도록 한다. */}
                  {Array.from(new Set([
                    ...(constants.products || []),
                    ...(products || []).map(p => (typeof p === "string" ? p : p.product)).filter(Boolean),
                    ...Object.keys(productContacts || {}),
                  ])).sort().map(p => <option key={p} value={p}>{p}</option>)}
                </select>
                {/* v8.8.3: 제품 등록/제거를 admin 제한 해제 — 사이드바 카탈로그와 동일 권한. */}
                <button type="button"
                  title="제품 추가 (카탈로그 등록)"
                  onClick={() => {
                    const v = (prompt("새 제품명:") || "").trim();
                    if (!v) return;
                    postJson(API + "/products/add", { product: v })
                      .then(d => {
                        setConstants(c => ({ ...c, products: d.products || c.products }));
                        setForm(f => ({ ...f, product: v }));
                      })
                      .catch(e => alert(e.message));
                  }}
                  style={{ padding: "6px 10px", borderRadius: 5, border: "1px solid var(--accent)", background: "transparent", color: "var(--accent)", fontSize: 11, cursor: "pointer" }}>+</button>
                {form.product && (constants.products || []).includes(form.product) && (
                  <button type="button"
                    title="선택된 제품을 카탈로그에서 제거"
                    onClick={() => {
                      const v = form.product;
                      if (!window.confirm(`"${v}" 을(를) 카탈로그에서 제거할까요?`)) return;
                      postJson(API + "/products/delete", { product: v })
                        .then(d => {
                          setConstants(c => ({ ...c, products: d.products || c.products }));
                          setForm(f => ({ ...f, product: "" }));
                        })
                        .catch(e => alert(e.message));
                    }}
                    style={{ padding: "6px 10px", borderRadius: 5, border: "1px solid #ef4444", background: "transparent", color: "#ef4444", fontSize: 11, cursor: "pointer" }}>−</button>
                )}
              </div>
              {/* v8.8.19: Lot 선택 = 제품 선택처럼 스크롤 드롭다운(<select>) + 직접 입력 토글.
                     후보는 SplitTable lot-candidates (오버라이드 DB 기반 root_lot_id + fab_lot_id).
                     - 드롭다운: 제품의 최신 lot 목록을 스크롤해서 고름 (root 는 [root], fab 는 [fab] 표기).
                     - ✏ 직접 입력 버튼: 후보에 없는 값을 수동 입력할 때 text input 으로 전환. */}
              <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                {form._lotManual ? (
                  <input value={form.lot_id} onChange={e => setForm({ ...form, lot_id: e.target.value })}
                    placeholder="Lot 직접 입력 (root_lot_id 또는 fab_lot_id)"
                    style={{ flex: 1, padding: "8px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12, fontFamily: "monospace" }} />
                ) : (
                  <select value={form.lot_id}
                    onChange={e => setForm({ ...form, lot_id: e.target.value })}
                    disabled={!form.product}
                    size={1}
                    style={{ flex: 1, padding: "8px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12, fontFamily: "monospace" }}>
                    <option value="">
                      {form.product
                        ? (lotOptions.length > 0
                            ? `-- Lot 선택 (${lotOptions.length}건 · SplitTable DB) --`
                            : "-- 해당 제품 DB 매칭 없음 — 직접 입력 권장 --")
                        : "-- 제품 먼저 선택 --"}
                    </option>
                    {lotOptions.map(o => (
                      <option key={o.type + ":" + o.value} value={o.value}>
                        {o.type === "fab" ? "[fab] " : "[root] "}{o.value}
                      </option>
                    ))}
                  </select>
                )}
                <button type="button"
                  onClick={() => setForm(f => ({ ...f, _lotManual: !f._lotManual }))}
                  title={form._lotManual ? "드롭다운으로 전환" : "직접 입력으로 전환"}
                  style={{ padding: "6px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-card)", color: "var(--text-secondary)", fontSize: 11, cursor: "pointer" }}>
                  {form._lotManual ? "▼ 목록" : "✏ 직접"}
                </button>
              </div>
            </div>
            <div style={{ display: "flex", gap: 8, marginBottom: 8, flexWrap: "wrap" }}>
              <select value={form.module} onChange={e => setForm({ ...form, module: e.target.value })}
                style={{ padding: "8px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12 }}>
                <option value="">-- 모듈 --</option>{constants.modules.map(m => <option key={m} value={m}>{m}</option>)}
              </select>
              <select value={form.reason} onChange={e => {
                  // v8.8.17: 사유 선택 시 해당 사유의 메일 템플릿을 본문/제목에 자동 채움.
                  //   사용자가 이미 입력한 text 가 있으면 덮어쓰지 않음 (confirm).
                  const nr = e.target.value;
                  const tpl = (constants.reason_templates || {})[nr];
                  const empty = !(form.text || "").trim();
                  if (tpl && tpl.body && (empty || window.confirm(`'${nr}' 사유의 기본 본문 템플릿으로 교체할까요? (현재 입력 내용은 사라집니다)`))) {
                    setForm({ ...form, reason: nr, text: tpl.body, mail_subject_tpl: tpl.subject || "" });
                  } else {
                    setForm({ ...form, reason: nr, mail_subject_tpl: (tpl && tpl.subject) || form.mail_subject_tpl || "" });
                  }
                }}
                style={{ padding: "8px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12 }}>
                <option value="">-- 사유 --</option>{constants.reasons.map(r => <option key={r} value={r}>{r}</option>)}
              </select>
              {/* v8.8.8: SplitTable 변경요청 체크박스 제거 — fab_lot_id 입력 시 자동으로 SplitTable 스냅샷 attach. */}
            </div>
            <textarea value={form.text} onChange={e => setForm({ ...form, text: e.target.value })} rows={4}
              onPaste={handleBodyPaste}
              placeholder="인폼 내용 (배경, 영향, 조치 요청 등) — Ctrl+V 로 이미지도 바로 붙여넣을 수 있어요"
              style={{ width: "100%", padding: 10, borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 13, resize: "vertical", boxSizing: "border-box", lineHeight: 1.5 }} />
            {/* v8.8.17: 인폼 등록 SplitTable scope 는 CUSTOM only.
                   - prefix chip(ALL/KNOB/MASK/INLINE/VM/FAB) + Saved CUSTOM 드롭다운 모두 제거.
                   - 인라인 CUSTOM 빌더만 노출 — SplitTable 의 CUSTOM UX 와 동일 동작.
                   - Search 버튼으로 스냅샷 수동 갱신. */}
            <div style={{ marginTop: 8, display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap", fontSize: 10 }}>
              <span style={{ color: "var(--text-secondary)", fontWeight: 600 }}>
                SplitTable 컬럼 선택 <span style={{ fontWeight: 400, color: "var(--text-tertiary, var(--text-secondary))" }}>(CUSTOM 모드 전용)</span>
              </span>
              <button type="button" onClick={() => setEmbedCustomOpen(!embedCustomOpen)}
                style={{ padding: "2px 8px", borderRadius: 4, border: "1px solid var(--border)", background: embedCustomOpen ? "var(--accent-glow)" : "var(--bg-card)", color: embedCustomOpen ? "var(--accent)" : "var(--text-primary)", fontSize: 10, cursor: "pointer", fontWeight: 600 }}>
                {embedCustomOpen ? "▼" : "▶"} 컬럼 편집{embedCustomCols.length > 0 ? ` · ${embedCustomCols.length}개 선택` : " · 미선택"}
              </button>
              <button type="button" onClick={() => setSnapshotTick(x => x + 1)}
                title="스냅샷 재조회 — lot/컬럼 변경 없이도 서버에서 다시 가져옴"
                style={{ padding: "2px 10px", borderRadius: 4, border: "1px solid var(--accent)", background: "var(--accent)", color: "#fff", fontSize: 10, cursor: "pointer", fontWeight: 600 }}>
                🔎 Search
              </button>
            </div>
            {/* v8.8.16: 인라인 CUSTOM 편집기 — SplitTable 사이드바와 동일 UX.
                v8.8.19: SplitTable 공용 CUSTOM set 드롭다운 + 새 set 저장. 양방향 공유. */}
            {embedCustomOpen && (
              <div style={{ marginTop: 6, padding: "8px 10px", borderRadius: 5, border: "1px dashed var(--border)", background: "var(--bg-card)", fontSize: 10 }}>
                {/* v8.8.19: SplitTable 공용 CUSTOM set 선택/저장 행 */}
                <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6, flexWrap: "wrap" }}>
                  <span style={{ fontSize: 9, color: "var(--text-secondary)", fontWeight: 600 }}>공용 CUSTOM set:</span>
                  <select value={embedCustomName} onChange={e => {
                      const nm = e.target.value;
                      setEmbedCustomName(nm);
                      if (nm) {
                        const found = (customsList || []).find(c => c.name === nm);
                        if (found && Array.isArray(found.columns)) {
                          setEmbedCustomCols(found.columns.slice());
                        }
                      }
                    }}
                    style={{ padding: "2px 6px", fontSize: 10, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", borderRadius: 3, maxWidth: 180 }}>
                    <option value="">-- 선택 (직접 편집) --</option>
                    {(customsList || []).map(c => <option key={c.name} value={c.name}>{c.name} ({(c.columns||[]).length})</option>)}
                  </select>
                  <button type="button" disabled={!embedCustomCols.length}
                    onClick={() => {
                      const nm = prompt("저장할 CUSTOM set 이름:", embedCustomName || "");
                      if (!nm) return;
                      postJson("/api/splittable/customs/save", {
                        name: nm, username: user?.username || "",
                        columns: embedCustomCols, expected_version: null,
                      }).then(r => {
                        if (r && r.conflict) { alert("이미 같은 이름의 set 이 있고 버전이 다릅니다. 이름을 바꾸거나 다시 시도."); return; }
                        sf("/api/splittable/customs").then(d => setCustomsList(d.customs || []));
                        setEmbedCustomName(nm);
                        alert(`CUSTOM set '${nm}' 저장됨. SplitTable 과 공유.`);
                      }).catch(e => alert("저장 실패: " + (e.message || e)));
                    }}
                    style={{ padding: "2px 8px", fontSize: 9, border: "1px solid var(--accent)", background: "var(--accent)", color: "#fff", borderRadius: 3, cursor: embedCustomCols.length ? "pointer" : "not-allowed", fontWeight: 600 }}>
                    💾 set 저장
                  </button>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
                  <span style={{ fontWeight: 600, color: "var(--text-secondary)" }}>
                    인라인 CUSTOM 컬럼 선택 · {embedCustomCols.length}/{embedSchemaCols.length}
                  </span>
                  <span style={{ marginLeft: "auto", fontSize: 9, color: "var(--text-secondary)" }}>
                    {embedCustomName ? `set='${embedCustomName}' (SplitTable 공유)` : "저장하지 않은 상태 — 이 인폼에만 적용됨"}
                  </span>
                </div>
                <input value={embedCustomSearch} onChange={e => setEmbedCustomSearch(e.target.value)}
                  placeholder="컬럼 검색..." style={{ width: "100%", padding: "4px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 10, marginBottom: 4, boxSizing: "border-box" }} />
                {(() => {
                  // v8.8.19: PRODUCT / ROOT_LOT_ID / WAFER_ID / lot_id / fab_lot_id 는 자동 포함 기본 컬럼 → CUSTOM pool 에서 제외.
                  const HIDDEN = new Set(["product","root_lot_id","wafer_id","lot_id","fab_lot_id"]);
                  const pool = (() => { const seen = new Set(); const out = [];
                    for (const c of [...embedSchemaCols, ...embedCustomCols]) {
                      const key = String(c).toLowerCase();
                      if (!seen.has(c) && !HIDDEN.has(key)) { seen.add(c); out.push(c); }
                    } return out; })();
                  const filtered = embedCustomSearch
                    ? pool.filter(c => c.toLowerCase().includes(embedCustomSearch.toLowerCase()))
                    : pool;
                  return (<>
                    <div style={{ display: "flex", gap: 4, marginBottom: 4, alignItems: "center" }}>
                      <button type="button" onClick={() => setEmbedCustomCols(Array.from(new Set([...embedCustomCols, ...filtered])))}
                        style={{ padding: "2px 8px", borderRadius: 3, border: "1px solid var(--accent)", background: "transparent", color: "var(--accent)", fontSize: 9, cursor: "pointer", fontWeight: 600 }}>
                        ✓ 전체 체크{embedCustomSearch ? ` (${filtered.length})` : ""}
                      </button>
                      <button type="button" onClick={() => {
                          if (embedCustomSearch) { const fs = new Set(filtered); setEmbedCustomCols(embedCustomCols.filter(c => !fs.has(c))); }
                          else setEmbedCustomCols([]);
                        }}
                        style={{ padding: "2px 8px", borderRadius: 3, border: "1px solid #ef4444", background: "transparent", color: "#ef4444", fontSize: 9, cursor: "pointer", fontWeight: 600 }}>
                        ✕ 전체 제거
                      </button>
                    </div>
                    <div style={{ maxHeight: 140, overflow: "auto", border: "1px solid var(--border)", borderRadius: 4, padding: 2, background: "var(--bg-primary)" }}>
                      {filtered.length === 0 && <div style={{ padding: 6, fontStyle: "italic", color: "var(--text-secondary)" }}>
                        {embedSchemaCols.length === 0 ? "제품 스키마 로딩 중..." : "검색 결과 없음"}
                      </div>}
                      {filtered.map(c => {
                        const on = embedCustomCols.includes(c);
                        return (<div key={c} onClick={() => {
                            if (on) setEmbedCustomCols(embedCustomCols.filter(x => x !== c));
                            else setEmbedCustomCols([...embedCustomCols, c]);
                          }}
                          style={{ padding: "2px 6px", cursor: "pointer", color: on ? "var(--accent)" : "var(--text-secondary)", fontFamily: "monospace" }}>
                          {on ? "✓ " : "  "}{c}
                        </div>);
                      })}
                    </div>
                  </>);
                })()}
                {/* 선택된 컬럼 pill 표시 */}
                {embedCustomCols.length > 0 && (
                  <div style={{ marginTop: 6 }}>
                    <div style={{ fontSize: 9, color: "var(--text-secondary)", marginBottom: 2, fontWeight: 600 }}>선택됨 ({embedCustomCols.length})</div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 3 }}>
                      {embedCustomCols.map(c => (
                        <span key={c} title={c}
                          style={{ display: "inline-flex", alignItems: "center", gap: 2, padding: "1px 5px", borderRadius: 3, fontSize: 9, background: "var(--accent-glow)", color: "var(--accent)", fontFamily: "monospace" }}>
                          {c}<span onClick={() => setEmbedCustomCols(embedCustomCols.filter(x => x !== c))}
                            style={{ cursor: "pointer", fontSize: 10, lineHeight: 1, marginLeft: 2, color: "#ef4444" }} title="제거">×</span>
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}

            <div style={{ marginTop: 6, display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
              <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>이미지: 본문에 <b>Ctrl+V</b> 로 바로 붙여넣기 (markdown 으로 inline 삽입)</span>
              {uploadingMain && <span style={{ fontSize: 10, color: "var(--accent)" }}>업로드중…</span>}
              {embedFetching && <span style={{ fontSize: 10, color: "var(--accent)" }}>SplitTable 스냅샷 로딩…</span>}
              {form.attach_embed && form.embed.rows.length > 0 && (
                <span style={{ fontSize: 10, color: "#16a34a", fontWeight: 600 }}>
                  ✓ SplitTable 자동 첨부 ({form.embed.rows.length} rows)
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
            {/* v8.8.8: Split Table 변경 수동 입력 블록 제거 — fab_lot_id 기반 자동 스냅샷으로 대체. */}
            <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
              <button onClick={create} style={{ padding: "8px 20px", borderRadius: 6, border: "none", background: "var(--accent)", color: "#fff", fontWeight: 600, cursor: "pointer" }}>등록</button>
              <button onClick={() => { setCreating(false); setMsg(""); }} style={{ padding: "8px 16px", borderRadius: 6, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", cursor: "pointer" }}>취소</button>
              {msg && <span style={{ fontSize: 11, color: "#ef4444", alignSelf: "center" }}>{msg}</span>}
            </div>
          </div>
        )}

        {/* v8.8.8: 모듈 필터 칩 제거 — 모듈 개념이 인폼에서 덜 쓰여서 불필요. 향후 필요 시 사이드바로 이전. */}

        {/* 메인 컨텐츠 */}
        {mode === "gantt" && (
          <>
            <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 10, color: "var(--text-secondary)" }}>📜 이력 타임라인 — 시간순 로그</div>
            <TimelineLog
              thread={thread}
              onOpen={(r) => { setSelectedLot((r.root_lot_id || (r.lot_id || "").slice(0, 5))); setMode("lot"); }}
            />
          </>
        )}

        {mode === "all" && (
          <>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8, flexWrap: "wrap" }}>
              <div style={{ fontSize: 14, fontWeight: 700, color: "var(--text-secondary)" }}>최근 인폼</div>
              <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>
                · {applyModFilter(rootsSorted).length}건
                {(moduleFilter.length > 0 || productFilter.length > 0) && <span style={{ color: "var(--accent)", marginLeft: 4 }}>(필터됨)</span>}
              </span>
              {(moduleFilter.length > 0 || productFilter.length > 0) && (
                <button onClick={() => { setModuleFilter([]); setProductFilter([]); }}
                  style={{ padding: "2px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", fontSize: 10, cursor: "pointer" }}>필터 초기화</button>
              )}
            </div>
            {/* v8.8.15: 모듈 nav 스타일 필터 — 토글 pill (checkbox 제거). */}
            {(() => {
              const allowed = (myMods.all_rounder || isAdmin) ? (constants.modules || []) : (myMods.modules || []).filter(m => (constants.modules || []).includes(m));
              if (allowed.length === 0) return null;
              const allOn = allowed.length > 0 && allowed.every(m => moduleFilter.includes(m));
              return (
                <div style={{ display: "flex", alignItems: "center", gap: 4, flexWrap: "wrap", padding: "6px 10px", borderRadius: 6, background: "var(--bg-card)", border: "1px solid var(--border)", marginBottom: 6 }}>
                  <span style={{ fontSize: 10, color: "var(--text-secondary)", fontWeight: 700, marginRight: 4 }}>📁 모듈</span>
                  <button onClick={() => setModuleFilter(allOn ? [] : [...allowed])}
                    style={{ padding: "2px 10px", borderRadius: 999, border: "1px solid " + (allOn ? "var(--accent)" : "var(--border)"), background: allOn ? "var(--accent)22" : "transparent", color: allOn ? "var(--accent)" : "var(--text-secondary)", fontSize: 10, fontWeight: allOn ? 700 : 500, cursor: "pointer" }}>
                    전체
                  </button>
                  {allowed.map(m => {
                    const on = moduleFilter.includes(m);
                    const mc = moduleColor(m);
                    return (
                      <button key={m} onClick={() => setModuleFilter(on ? moduleFilter.filter(x => x !== m) : [...moduleFilter, m])}
                        style={{ padding: "2px 10px", borderRadius: 999, border: "1px solid " + (on ? mc + "aa" : "var(--border)"), background: on ? mc + "22" : "transparent", color: on ? mc : "var(--text-secondary)", fontSize: 10, fontWeight: on ? 700 : 500, cursor: "pointer" }}>
                        {m}
                      </button>
                    );
                  })}
                </div>
              );
            })()}
            {/* v8.8.15: 제품 nav 스타일 필터 — 현재 목록에 등장한 제품만 pill 노출. */}
            {(() => {
              const presentProds = Array.from(new Set(rootsSorted.map(r => r.product || "").filter(Boolean))).sort();
              if (presentProds.length === 0) return null;
              return (
                <div style={{ display: "flex", alignItems: "center", gap: 4, flexWrap: "wrap", padding: "6px 10px", borderRadius: 6, background: "var(--bg-card)", border: "1px solid var(--border)", marginBottom: 10 }}>
                  <span style={{ fontSize: 10, color: "var(--text-secondary)", fontWeight: 700, marginRight: 4 }}>📦 제품</span>
                  <button onClick={() => setProductFilter([])}
                    style={{ padding: "2px 10px", borderRadius: 999, border: "1px solid " + (productFilter.length === 0 ? "var(--accent)" : "var(--border)"), background: productFilter.length === 0 ? "var(--accent)22" : "transparent", color: productFilter.length === 0 ? "var(--accent)" : "var(--text-secondary)", fontSize: 10, fontWeight: productFilter.length === 0 ? 700 : 500, cursor: "pointer" }}>
                    전체
                  </button>
                  {presentProds.map(p => {
                    const on = productFilter.includes(p);
                    return (
                      <button key={p} onClick={() => setProductFilter(on ? productFilter.filter(x => x !== p) : [...productFilter, p])}
                        style={{ padding: "2px 10px", borderRadius: 999, border: "1px solid " + (on ? "var(--accent)" : "var(--border)"), background: on ? "var(--accent)22" : "transparent", color: on ? "var(--accent)" : "var(--text-secondary)", fontSize: 10, fontWeight: on ? 700 : 500, cursor: "pointer", fontFamily: "monospace" }}>
                        {p}
                      </button>
                    );
                  })}
                </div>
              );
            })()}
            {applyModFilter(rootsSorted).length === 0 && (
              (!moduleFilter?.length && !productFilter?.length)
                ? <div style={{ padding: 40, textAlign: "center", color: "var(--text-secondary)" }}>
                    <div style={{ fontSize: 13, marginBottom: 6 }}>필터를 하나 이상 선택하세요.</div>
                    <div style={{ fontSize: 11 }}>위 📁 모듈 · 📦 제품 pill 중 최소 1개 이상 체크해야 인폼이 표시됩니다.</div>
                  </div>
                : <div style={{ padding: 40, textAlign: "center", color: "var(--text-secondary)" }}>인폼 없음.</div>
            )}
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
                      <span style={{ fontSize: 13, fontWeight: 700, fontFamily: "monospace" }}>
                        <span style={{ color: "var(--accent)" }}>[{selectedProduct}]</span> {lid}
                      </span>
                      <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>· {lotRoots.length}건</span>
                      <span style={{ flex: 1 }} />
                      <span onClick={() => { setSelectedLot((lid || "").slice(0, 5)); setMode("lot"); }}
                            style={{ fontSize: 11, color: "var(--accent)", textDecoration: "underline", cursor: "pointer" }}>Lot 전용 뷰 ↗</span>
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
            <SplitNotesCard notes={splitNotes} root_lot_id={selectedLot} />
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
                      {/* v8.8.13: 외부 wafer 라벨 제거 — RootHeader 안에 wafer 가 이미 렌더됨 (중복 방지). */}
                      <RootHeader root={r} onChangeStatus={changeStatus} user={user} />
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
                <RootHeader root={r} onChangeStatus={changeStatus} user={user} />
                <ThreadNode node={r} childrenByParent={childrenByParent}
                  onReply={reply} onDelete={del} onToggleCheck={toggleCheck}
                  onEdit={editInform}
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

/* v8.7.9 — 시간순 이력 타임라인 (간트바 대신).
   각 줄: [시각] 모듈 [사유] LOT · 이벤트타입 · 요약 · 작성자.
   이벤트: 인폼 등록 / 담당자 확인 / 메일 발송 / 댓글 / 수정(status_history 기타).
*/
function TimelineLog({ thread, onOpen }) {
  // v8.8.0: Lot 검색 필터 + root_lot prefix 매칭.
  const [lotQ, setLotQ] = useState("");
  const events = useMemo(() => {
    const evs = [];
    for (const x of (thread || [])) {
      const isRoot = !x.parent_id;
      // 1) 등록(=루트) 또는 댓글(=비루트 첫 등장)
      evs.push({
        at: x.created_at || "",
        actor: x.author || "",
        kind: isRoot ? "인폼" : "댓글",
        module: x.module || "",
        reason: x.reason || "",
        lot: x.lot_id || "",
        product: x.product || "",
        summary: (x.text || "").slice(0, 80),
        node: x,
      });
      // 2) 상태 이력 — received(최초) 는 등록 이벤트가 이미 담당하므로 skip.
      //    v8.8.2: prev=completed → received 뿐 아니라 note/직전상태 trail 로도
      //    "확인 취소" 인식. 최초 등록 received 는 중복 피하려 한 번만 skip.
      let seenFirstReceived = false;
      let prevStat = "";
      for (const h of (x.status_history || [])) {
        if (!h || !h.at) continue;
        const hPrev = h.prev ?? prevStat;  // v8.8.2: prev 누락 시 walking 으로 복원
        const noteStr = h.note || "";
        const isInitial = (h.status === "received") && !seenFirstReceived
          && (noteStr === "created" || noteStr === "auto from SplitTable" || (!noteStr && hPrev === ""));
        if (isInitial) { seenFirstReceived = true; prevStat = h.status || prevStat; continue; }
        const isUnconfirm = h.status === "received" && (
          hPrev === "completed"
          || noteStr.includes("확인 취소")
          || noteStr.includes("완료 해제")
          || noteStr.includes("취소")
          || noteStr.includes("해제")
        );
        evs.push({
          at: h.at,
          actor: h.actor || "",
          kind: h.status === "completed"
            ? "담당자확인"
            : (isUnconfirm ? "확인취소" : `상태:${h.status || "-"}`),
          module: x.module || "",
          reason: x.reason || "",
          lot: x.lot_id || "",
          product: x.product || "",
          summary: noteStr || (isUnconfirm ? "완료 해제" : ""),
          node: x,
        });
        prevStat = h.status || prevStat;
      }
      // 3) 체크(구형) — status_history 에 잡히지 않은 경우 보완.
      if (x.checked && x.checked_at) {
        evs.push({
          at: x.checked_at,
          actor: x.checked_by || "",
          kind: "체크",
          module: x.module || "",
          reason: x.reason || "",
          lot: x.lot_id || "",
          product: x.product || "",
          summary: "",
          node: x,
        });
      }
      // 4) 메일 발송 이력
      for (const m of (x.mail_history || [])) {
        const at = m.at || m.sent_at || m.time || "";
        if (!at) continue;
        evs.push({
          at, actor: m.actor || m.sender || "",
          kind: "메일",
          module: x.module || "",
          reason: x.reason || "",
          lot: x.lot_id || "",
          product: x.product || "",
          summary: m.subject ? `[${m.subject}]` : (m.to ? `→ ${Array.isArray(m.to) ? m.to.join(", ") : m.to}` : ""),
          node: x,
        });
      }
    }
    // v8.8.13: 최신이 위로 — 내림차순 정렬.
    evs.sort((a, b) => (b.at || "").localeCompare(a.at || ""));
    return evs;
  }, [thread]);

  const filtered = useMemo(() => {
    const q = (lotQ || "").trim().toLowerCase();
    if (!q) return events;
    return events.filter(e => {
      const lot = (e.lot || "").toLowerCase();
      const root = (e.node?.root_lot_id || "").toLowerCase();
      return lot.includes(q) || root.includes(q) || lot.startsWith(q);
    });
  }, [events, lotQ]);

  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 8, background: "var(--bg-secondary)", padding: 10, fontFamily: "monospace" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6, flexWrap: "wrap" }}>
        <span style={{ fontSize: 13, fontWeight: 700, color: "var(--accent)" }}>📜 이력 타임라인 ({filtered.length}{lotQ ? ` / ${events.length}` : ""}건)</span>
        <input value={lotQ} onChange={e => setLotQ(e.target.value)}
          placeholder="🔎 Lot 검색 (root_lot_id 또는 fab_lot_id 부분일치)"
          style={{ flex: 1, minWidth: 220, padding: "5px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12, fontFamily: "monospace" }} />
        {lotQ && <span onClick={() => setLotQ("")} style={{ cursor: "pointer", color: "#ef4444", fontSize: 11 }}>✕ 초기화</span>}
      </div>
      <div style={{ fontSize: 10, color: "var(--text-secondary)", marginBottom: 8 }}>작성 / 수정 / 이행(확인·완료) — 누가 언제 무엇을 했는지 시간순. Lot 입력 시 해당 Lot 만 필터링.</div>
      {filtered.length === 0 && <div style={{ padding: 40, textAlign: "center", color: "var(--text-secondary)" }}>{lotQ ? `'${lotQ}' 매칭 이력 없음.` : "이력 없음."}</div>}
      {filtered.map((e, i) => {
        const mc = moduleColor(e.module);
        const kindColor = e.kind === "인폼" ? "#3b82f6"
          : e.kind === "담당자확인" ? "#22c55e"
          : e.kind === "확인취소" ? "#ef4444"
          : e.kind === "메일" ? "#f59e0b"
          : e.kind === "댓글" ? "#8b5cf6"
          : e.kind === "체크" ? "#14b8a6"
          : "#64748b";
        const lotLabel = e.product && e.lot ? `[${e.product}] ${e.lot}` : (e.lot || e.product || "-");
        return (
          <div key={i} onClick={() => onOpen && onOpen(e.node)} style={{
            display: "flex", gap: 10, alignItems: "center", padding: "4px 6px",
            borderRadius: 4, cursor: "pointer", fontSize: 12, lineHeight: 1.55,
            borderLeft: `3px solid ${mc}`, marginBottom: 2, background: i % 2 ? "var(--bg-primary)" : "transparent",
          }}>
            <span style={{ color: "var(--text-secondary)", minWidth: 115 }}>{(e.at || "").replace("T", " ").slice(0, 16)}</span>
            <span style={{ minWidth: 56, color: mc, fontWeight: 700 }}>{e.module || "-"}</span>
            <span style={{ minWidth: 88, color: "var(--text-secondary)" }}>{e.reason ? `[${e.reason}]` : ""}</span>
            <span style={{ minWidth: 180, color: "var(--text-primary)" }}>{lotLabel}</span>
            <span style={{ padding: "1px 8px", borderRadius: 999, background: kindColor + "22", color: kindColor, fontWeight: 700, fontSize: 11 }}>{e.kind}</span>
            <span style={{ color: "var(--text-primary)", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{e.summary}</span>
            <span style={{ color: "var(--text-secondary)", fontSize: 11 }}>· {e.actor || "-"}</span>
          </div>
        );
      })}
    </div>
  );
}

/* v8.8.13: admin 전용 — 유저별 인폼 모듈 조회 권한 편집 패널.
   PageGear 인폼 설정 하단에 표시. 체크가 하나도 없으면 "아무 모듈도 조회 못함",
   체크 해제 전 기본 상태(설정 없음)는 groups 기반으로 fallback. */
/* v8.8.17: ReasonTemplatesPanel — admin 이 사유별로 메일 제목 + 본문 템플릿을 편집.
   PageGear 안에서 사유 목록을 순회하며 제목/본문 2필드를 보여준다.
   저장은 단건이 아니라 템플릿 맵 전체를 일괄 PATCH 로 POST /api/informs/config. */
function ReasonTemplatesPanel({ reasons, templates, onSave }) {
  const [draft, setDraft] = React.useState(() => ({ ...(templates || {}) }));
  const [open, setOpen] = React.useState(false);
  const [active, setActive] = React.useState("");
  React.useEffect(() => { setDraft({ ...(templates || {}) }); }, [templates]);
  const setField = (reason, field, v) => {
    setDraft(d => ({ ...d, [reason]: { ...(d[reason] || { subject: "", body: "" }), [field]: v } }));
  };
  if (!open) {
    const count = Object.values(draft || {}).filter(v => v && (v.subject || v.body)).length;
    return (
      <div style={{ marginTop: 14, paddingTop: 10, borderTop: "1px dashed var(--border)" }}>
        <button onClick={() => setOpen(true)}
          style={{ padding: "8px 14px", borderRadius: 6, border: "1px solid var(--accent)", background: "transparent", color: "var(--accent)", fontSize: 12, cursor: "pointer", fontWeight: 600 }}>
          ✉ 사유별 메일 템플릿 편집 ({count}/{(reasons || []).length})
        </button>
      </div>
    );
  }
  return (
    <div style={{ marginTop: 14, paddingTop: 10, borderTop: "1px dashed var(--border)" }}>
      <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 6 }}>
        사유 선택 시 자동으로 채워지는 메일 제목/본문 템플릿. 저장 후 등록 폼에서 사유를 고르면 적용됩니다.
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 6 }}>
        {(reasons || []).map(r => {
          const has = draft[r] && (draft[r].subject || draft[r].body);
          return (
            <span key={r} onClick={() => setActive(r)}
              style={{ padding: "3px 8px", borderRadius: 999, cursor: "pointer", fontSize: 11,
                background: active === r ? "var(--accent)" : (has ? "var(--accent-glow)" : "var(--bg-card)"),
                color: active === r ? "#fff" : (has ? "var(--accent)" : "var(--text-primary)"),
                border: "1px solid " + (active === r ? "var(--accent)" : "var(--border)") }}>
              {has ? "● " : ""}{r}
            </span>
          );
        })}
      </div>
      {active && (
        <div style={{ padding: 8, border: "1px solid var(--border)", borderRadius: 5, background: "var(--bg-card)" }}>
          <div style={{ fontSize: 10, color: "var(--text-secondary)", marginBottom: 2 }}>제목 템플릿</div>
          <input value={(draft[active] || {}).subject || ""}
            onChange={e => setField(active, "subject", e.target.value)}
            placeholder="[인폼·장비이상] {product} · {lot}"
            style={{ width: "100%", padding: "4px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 11, boxSizing: "border-box", marginBottom: 6 }} />
          <div style={{ fontSize: 10, color: "var(--text-secondary)", marginBottom: 2 }}>본문 템플릿</div>
          <textarea value={(draft[active] || {}).body || ""}
            onChange={e => setField(active, "body", e.target.value)}
            placeholder="배경:&#10;영향:&#10;조치 요청:"
            rows={6}
            style={{ width: "100%", padding: 6, borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 11, boxSizing: "border-box", fontFamily: "inherit", lineHeight: 1.4 }} />
          <div style={{ fontSize: 9, color: "var(--text-secondary)", marginTop: 4 }}>
            변수 참고: <code>{"{product}"}</code> <code>{"{lot}"}</code> <code>{"{module}"}</code> <code>{"{reason}"}</code> — 현재 폼에 자동 치환.
          </div>
        </div>
      )}
      <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
        <button onClick={() => onSave(draft)}
          style={{ padding: "5px 12px", borderRadius: 4, border: "none", background: "var(--accent)", color: "#fff", fontSize: 11, fontWeight: 600, cursor: "pointer" }}>저장</button>
        <button onClick={() => setOpen(false)}
          style={{ padding: "5px 12px", borderRadius: 4, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", fontSize: 11, cursor: "pointer" }}>닫기</button>
      </div>
    </div>
  );
}


function UserModulePermsPanel({ allModules }) {
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(false);
  const [savingFor, setSavingFor] = useState("");
  const [q, setQ] = useState("");
  const load = () => {
    setLoading(true);
    sf("/api/informs/user-modules").then(d => {
      setUsers(d.users || []); setLoading(false);
    }).catch(e => { setLoading(false); alert("로드 실패: " + (e.message || e)); });
  };
  useEffect(() => { load(); }, []);
  const toggleOne = (username, module, on) => {
    const u = users.find(x => x.username === username); if (!u) return;
    const next = on
      ? [...(u.modules || []), module]
      : (u.modules || []).filter(m => m !== module);
    persist(username, next);
  };
  const setAllFor = (username, on) => persist(username, on ? [...allModules] : []);
  const clearFor = (username) => {
    setSavingFor(username);
    sf("/api/informs/user-modules/clear", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, modules: [] }) })
      .then(() => { setSavingFor(""); load(); })
      .catch(e => { setSavingFor(""); alert("초기화 실패: " + (e.message || e)); });
  };
  const persist = (username, modules) => {
    setSavingFor(username);
    // optimistic
    setUsers(list => list.map(u => u.username === username ? { ...u, modules, has_setting: true } : u));
    sf("/api/informs/user-modules/save", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, modules }) })
      .then(() => setSavingFor(""))
      .catch(e => { setSavingFor(""); alert("저장 실패: " + (e.message || e)); load(); });
  };
  const filtered = q ? users.filter(u => (u.username || "").toLowerCase().includes(q.toLowerCase()) || (u.email || "").toLowerCase().includes(q.toLowerCase())) : users;
  return (
    <div style={{ marginTop: 14, paddingTop: 10, borderTop: "1px dashed var(--border)" }}>
      <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 4 }}>🔒 유저별 모듈 조회 권한</div>
      <div style={{ fontSize: 10, color: "var(--text-secondary)", marginBottom: 6, lineHeight: 1.5 }}>
        인폼 탭 권한이 있는 유저에게 <b>모듈별 조회 권한</b> 을 부여합니다.
        체크된 모듈의 인폼만 목록·검색에 노출됩니다. admin 은 항상 전체. 설정을 초기화하면 그룹 기반으로 돌아갑니다.
      </div>
      <input value={q} onChange={e => setQ(e.target.value)} placeholder="🔎 유저/이메일 검색"
        style={{ width: "100%", padding: "4px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 11, marginBottom: 6, boxSizing: "border-box" }} />
      {loading && <div style={{ padding: 10, fontSize: 10, color: "var(--text-secondary)" }}>로딩...</div>}
      {!loading && filtered.length === 0 && <div style={{ padding: 10, fontSize: 10, color: "var(--text-secondary)" }}>해당 유저 없음</div>}
      <div style={{ maxHeight: 380, overflow: "auto", border: "1px solid var(--border)", borderRadius: 4 }}>
        {filtered.map(u => {
          const modsSet = new Set(u.modules || []);
          const allOn = allModules.length > 0 && allModules.every(m => modsSet.has(m));
          const busy = savingFor === u.username;
          return (
            <div key={u.username} style={{ padding: "6px 8px", borderBottom: "1px solid var(--border)", opacity: busy ? 0.6 : 1 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4, flexWrap: "wrap" }}>
                <span style={{ fontSize: 11, fontWeight: 700, fontFamily: "monospace" }}>{u.username}</span>
                {u.role === "admin" && <span style={{ fontSize: 9, padding: "1px 5px", borderRadius: 8, background: "#ef444422", color: "#ef4444", fontWeight: 700 }}>admin</span>}
                {u.email && <span style={{ fontSize: 9, color: "var(--text-secondary)", fontFamily: "monospace" }}>{u.email}</span>}
                <span style={{ flex: 1 }} />
                <span style={{ fontSize: 9, color: u.has_setting ? "#16a34a" : "var(--text-secondary)" }}>
                  {u.has_setting ? `✓ 설정됨 (${(u.modules || []).length})` : "기본(그룹 기반)"}
                </span>
                <span onClick={() => setAllFor(u.username, !allOn)}
                  style={{ fontSize: 9, padding: "1px 6px", borderRadius: 4, cursor: "pointer", border: "1px solid var(--border)", color: "var(--accent)" }}>
                  {allOn ? "전체 해제" : "전체 선택"}
                </span>
                {u.has_setting && <span onClick={() => clearFor(u.username)}
                  style={{ fontSize: 9, padding: "1px 6px", borderRadius: 4, cursor: "pointer", border: "1px solid #ef4444", color: "#ef4444" }}
                  title="이 유저의 권한 설정을 초기화 (groups 기반으로 복귀)">× 초기화</span>}
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                {allModules.map(m => {
                  const on = modsSet.has(m);
                  const mc = moduleColor(m);
                  return (
                    <label key={m}
                      style={{ fontSize: 10, padding: "1px 7px", borderRadius: 999, cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 3,
                        background: on ? (mc + "22") : "transparent",
                        color: on ? mc : "var(--text-secondary)",
                        fontWeight: on ? 700 : 500,
                        border: "1px solid " + (on ? (mc + "55") : "var(--border)") }}>
                      <input type="checkbox" checked={on} onChange={() => toggleOne(u.username, m, !on)} style={{ accentColor: mc }} />
                      {m}
                    </label>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* 요약 카드 (all/mine/product 모드에서 루트 리스트용) */
function CompactRow({ root, onOpen }) {
  const mc = moduleColor(root.module);
  // v8.8.8: 이 root 인폼의 (product, root_lot_id) 에 달린 SplitTable notes(꼬리표) 를 요약 배지로 표시.
  //   wafer 태그 / param 메모 / LOT 노트 / global 태그 각 카운트. 클릭 시 상세는 별도 동작 없이 hover tooltip 으로.
  const [tagSummary, setTagSummary] = useState(null);
  useEffect(() => {
    const prod = root.product || "";
    const rlot = root.root_lot_id || (root.lot_id || "").slice(0, 5);
    if (!prod || !rlot) { setTagSummary(null); return; }
    let alive = true;
    sf(`/api/splittable/notes?product=${encodeURIComponent(prod)}&root_lot_id=${encodeURIComponent(rlot)}`)
      .then(d => {
        if (!alive) return;
        const ns = d.notes || [];
        const by = { wafer: 0, param: 0, lot: 0, param_global: 0 };
        ns.forEach(n => { if (by[n.scope] != null) by[n.scope]++; });
        const total = ns.length;
        setTagSummary(total > 0 ? { total, by, sample: ns.slice(0, 3).map(n => n.text) } : null);
      })
      .catch(() => {});
    return () => { alive = false; };
  }, [root.product, root.root_lot_id, root.lot_id]);

  // v8.8.14: 레이아웃 재정비 — "어떤 제품 · 어떤 모듈" 이 한눈에 보이도록 좌측 88px 고정 컬럼으로 분리.
  //   좌측: 모듈 pill (색상 채우기) + 그 아래 reason 꼬리표. 모듈 미정이면 회색 placeholder.
  //   우측: 상태 pill + 첨부 icon + [제품] lot_id + 타임스탬프 + 본문 1-2줄.
  const hasModule = !!(root.module && String(root.module).trim());
  const modLabel = hasModule ? root.module : "미정";
  const modFill = hasModule ? mc : "#6b7280";
  const reasonLabel = (root.reason || "").trim();

  return (
    <div onClick={onOpen}
      style={{ display: "flex", padding: 0, marginBottom: 8, borderRadius: 8,
               border: "1px solid var(--border)", background: "var(--bg-secondary)",
               borderLeft: "5px solid " + modFill,
               cursor: "pointer", overflow: "hidden" }}>
      {/* 좌측 모듈/사유 컬럼 — 고정폭으로 행 간 정렬 시각화 */}
      <div style={{ flex: "0 0 96px", padding: "10px 8px 10px 10px",
                    display: "flex", flexDirection: "column", gap: 4,
                    alignItems: "center", justifyContent: "center",
                    background: modFill + "0D", borderRight: "1px solid var(--border)" }}>
        <span style={{ fontSize: 11, padding: "3px 10px", borderRadius: 999,
                       background: modFill, color: "#fff", fontWeight: 800,
                       letterSpacing: 0.3, whiteSpace: "nowrap",
                       boxShadow: "0 1px 2px rgba(0,0,0,0.15)" }}>{modLabel}</span>
        {reasonLabel
          ? <span style={{ fontSize: 9.5, padding: "1px 6px", borderRadius: 4,
                           background: modFill + "22", color: modFill, fontWeight: 700,
                           textAlign: "center", lineHeight: 1.25, whiteSpace: "nowrap",
                           maxWidth: "100%", overflow: "hidden", textOverflow: "ellipsis" }}
                  title={reasonLabel}>[{reasonLabel}]</span>
          : <span style={{ fontSize: 9, color: "var(--text-secondary)", fontStyle: "italic" }}>(사유 미정)</span>}
      </div>
      {/* 우측 본문 */}
      <div style={{ flex: 1, padding: "10px 14px", minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          {/* v8.8.13: '접수' StatusBadge 제거 — CheckPill(확인중/확인완료) 하나로 통합. */}
          <CheckPill node={root} />
          <AutoGenPill node={root} />
          {(root.images && root.images.length > 0) && <span title="이미지 첨부" style={{ fontSize: 10 }}>📎{root.images.length}</span>}
          {root.embed_table && <span title="SplitTable 스냅샷 첨부" style={{ fontSize: 10 }}>🔗</span>}
          {/* v8.8.8: lot/wafer 꼬리표 배지 — SplitTable notes 집계. */}
          {tagSummary && (
            <span title={`이 lot 의 꼬리표 ${tagSummary.total}개 (wafer ${tagSummary.by.wafer} · param ${tagSummary.by.param} · lot ${tagSummary.by.lot} · global ${tagSummary.by.param_global})\n샘플: ${tagSummary.sample.join(" / ")}`}
                  style={{ fontSize: 10, padding: "1px 6px", borderRadius: 8, background: "#8b5cf6", color: "#fff", fontWeight: 700, cursor: "help" }}>
              🏷 {tagSummary.total}
            </span>
          )}
          {/* v8.7.9: `[제품명] Lot` 표시. wafer_id 는 보조적으로만. */}
          <span style={{ fontSize: 12, fontFamily: "monospace", fontWeight: 700 }}>
            {root.product && <span style={{ color: "var(--accent)" }}>[{root.product}]</span>}
            {root.product && root.lot_id ? " " : ""}
            {root.lot_id || root.wafer_id || "-"}
          </span>
          {root.root_lot_id && root.lot_id && root.root_lot_id !== root.lot_id && (
            <span title="root_lot_id (앞 5자)" style={{ fontSize: 10, color: "var(--text-secondary)", fontFamily: "monospace" }}>root:{root.root_lot_id}</span>
          )}
          {/* v8.8.15: fab_lot_id 스냅샷 pill — 저장 시점의 실제 fab_lot_id. 이후 ML_TABLE 재빌드에도 불변. */}
          {root.fab_lot_id_at_save && (
            <span title={`저장 시점 fab_lot_id 스냅샷: ${root.fab_lot_id_at_save}`}
              style={{ fontSize: 9, padding: "1px 6px", borderRadius: 3, background: "rgba(34,197,94,0.12)", color: "#16a34a", border: "1px solid rgba(34,197,94,0.4)", fontFamily: "monospace", fontWeight: 700 }}>
              🔗 {root.fab_lot_id_at_save}
            </span>
          )}
          <div style={{ flex: 1 }} />
          <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>{(root.created_at || "").replace("T", " ").slice(0, 16)}</span>
          <span style={{ fontSize: 10, fontWeight: 600 }}>{root.author}</span>
        </div>
        <div style={{ fontSize: 12, marginTop: 4, whiteSpace: "pre-wrap", opacity: 0.95,
                      display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
          {root.text}
        </div>
      </div>
    </div>
  );
}
