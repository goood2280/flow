import { useCallback, useEffect, useRef, useState } from "react";
import Modal from "../../components/Modal";
import { toast } from "../../components/Toast";
import { authSrc, sf } from "../../lib/api";

const API = "/api/splittable";

export default function LotNotesModal({
  open = true,
  onClose,
  lotId = "",
  product = "",
  user,
}) {
  const [notes, setNotes] = useState([]);
  const [loading, setLoading] = useState(false);
  const [noteFilter, setNoteFilter] = useState("all"); // "all" | "wafer" | "param" | "lot"
  const [noteSearch, setNoteSearch] = useState("");
  const [expandedNoteId, setExpandedNoteId] = useState("");
  const [noteDraft, setNoteDraft] = useState("");
  const [noteImages, setNoteImages] = useState([]);
  const [noteUploading, setNoteUploading] = useState(false);
  const [saving, setSaving] = useState(false);

  const cleanLot = String(lotId || "").trim();
  const cleanProd = String(product || "").trim();
  const me = user?.username || "";

  const loadNotes = useCallback(async (showLoading = true) => {
    if (!cleanLot) return;
    if (showLoading) setLoading(true);
    try {
      const q = new URLSearchParams();
      if (cleanProd && cleanProd !== "-") q.set("product", cleanProd);
      q.set("root_lot_id", cleanLot);
      const res = await sf(`${API}/notes?${q.toString()}`);
      setNotes(Array.isArray(res?.notes) ? res.notes : []);
    } catch (e) {
      toast.error(`노트 불러오기 실패: ${e?.message || e}`);
    } finally {
      if (showLoading) setLoading(false);
    }
  }, [cleanLot, cleanProd]);

  useEffect(() => {
    if (open && cleanLot) {
      loadNotes(true);
    } else {
      setNotes([]);
      setNoteDraft("");
      setNoteImages([]);
      setNoteSearch("");
      setExpandedNoteId("");
      setNoteFilter("all");
    }
  }, [open, cleanLot, loadNotes]);

  const normalizeNoteFile = (f, i = 0) => {
    const extFromType = (f?.type || "").split("/")[1] || "png";
    const hasExt = /\.[A-Za-z0-9]{2,5}$/.test(f?.name || "");
    return hasExt ? f : new File([f], `note_${Date.now()}_${i}.${extFromType}`, { type: f?.type || "image/png" });
  };

  const uploadNoteFiles = async (files) => {
    const list = Array.from(files || []).filter(f => /^image\//.test(f?.type || ""));
    if (!list.length) return;
    setNoteUploading(true);
    const uploaded = [];
    for (let i = 0; i < list.length; i++) {
      try {
        const fd = new FormData();
        fd.append("file", normalizeNoteFile(list[i], i));
        const res = await sf("/api/informs/upload", { method: "POST", body: fd });
        uploaded.push({ filename: res.filename, url: res.url, size: res.size });
      } catch (e) {
        toast.error(`이미지 업로드 실패: ${e?.message || e}`);
      }
    }
    if (uploaded.length) {
      setNoteImages(prev => [...prev, ...uploaded].slice(0, 12));
    }
    setNoteUploading(false);
  };

  const handleNotePaste = (e) => {
    const items = e.clipboardData?.items || [];
    const files = [];
    for (const it of items) {
      if (it.kind === "file" && /^image\//.test(it.type || "")) {
        const f = it.getAsFile();
        if (f) files.push(f);
      }
    }
    if (!files.length) return;
    e.preventDefault();
    uploadNoteFiles(files);
  };

  const clearNoteDraft = () => {
    setNoteDraft("");
    setNoteImages([]);
  };

  const addNote = async () => {
    const txt = (noteDraft || "").trim();
    if (!txt && noteImages.length === 0) return;
    if (!cleanLot) return;

    setSaving(true);
    try {
      await sf(`${API}/notes/save`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          scope: "lot",
          product: cleanProd && cleanProd !== "-" ? cleanProd : "COMMON",
          root_lot_id: cleanLot,
          text: txt,
          images: noteImages,
          username: me,
        }),
      });
      clearNoteDraft();
      toast.ok("노트가 등록되었습니다.");
      await loadNotes(false);
    } catch (e) {
      toast.error(`노트 저장 실패: ${e?.message || e}`);
    } finally {
      setSaving(false);
    }
  };

  const deleteNote = async (id) => {
    if (!window.confirm("노트를 삭제하시겠습니까?")) return;
    try {
      await sf(`${API}/notes/delete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id, username: me }),
      });
      toast.ok("노트가 삭제되었습니다.");
      await loadNotes(false);
    } catch (e) {
      toast.error(`노트 삭제 실패: ${e?.message || e}`);
    }
  };

  if (!open) return null;

  // param_global 제외
  const base = notes.filter(n => n.scope !== "param_global");

  let filtered = base;
  if (noteFilter === "wafer") {
    filtered = base.filter(n => n.scope === "wafer");
  } else if (noteFilter === "param") {
    filtered = base.filter(n => n.scope === "param");
  } else if (noteFilter === "lot") {
    filtered = base.filter(n => n.scope === "lot");
  }

  const q = noteSearch.trim().toLowerCase();
  if (q) {
    filtered = filtered.filter(n => {
      const parts = (n.key || "").split("__");
      const wid = (parts[2] || "").replace(/^W/, "");
      const param = parts[3] || "";
      return (
        (n.text || "").toLowerCase().includes(q) ||
        wid.toLowerCase().includes(q) ||
        param.toLowerCase().includes(q) ||
        (n.username || "").toLowerCase().includes(q)
      );
    });
  }

  const canSave = (Boolean(noteDraft.trim()) || noteImages.length > 0) && !noteUploading && !saving;
  const prodLabel = cleanProd && cleanProd !== "-" ? cleanProd.replace(/^ML_TABLE_/, "") : "";

  return (
    <Modal open={open} onClose={onClose} width={540} zIndex={2500}>
      <div style={{ display: "flex", flexDirection: "column", maxHeight: "82vh" }}>
        {/* Header */}
        <div
          style={{
            padding: "12px 16px",
            borderBottom: "1px solid var(--border)",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
            <div style={{ fontSize: 15, fontWeight: 700, fontFamily: "monospace", color: "var(--accent)" }}>
              📝 LOT {cleanLot} 노트
            </div>
            {prodLabel && (
              <span
                style={{
                  fontSize: 11,
                  padding: "1px 6px",
                  borderRadius: 8,
                  background: "var(--surface-subtle)",
                  color: "var(--text-muted)",
                  fontWeight: 600,
                }}
              >
                {prodLabel}
              </span>
            )}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <button
              type="button"
              onClick={() => loadNotes(true)}
              title="새로고침"
              style={{
                background: "transparent",
                border: "none",
                cursor: "pointer",
                color: "var(--text-secondary)",
                fontSize: 13,
              }}
            >
              🔄
            </button>
            <span
              onClick={onClose}
              role="button"
              tabIndex={0}
              onKeyDown={e => { if (e.key === "Enter" || e.key === " ") onClose(); }}
              style={{ cursor: "pointer", fontSize: 18, color: "var(--text-secondary)", lineHeight: 1 }}
              aria-label="닫기"
            >
              ✕
            </span>
          </div>
        </div>

        {/* Scope Filter Chips */}
        <div
          style={{
            padding: "6px 16px",
            borderBottom: "1px solid var(--border)",
            display: "flex",
            gap: 4,
            flexWrap: "wrap",
            fontSize: 13,
            color: "var(--text-secondary)",
          }}
        >
          {[
            { k: "all", l: `전체 ${base.length}` },
            { k: "wafer", l: `🏷 wafer ${base.filter(n => n.scope === "wafer").length}` },
            { k: "param", l: `💬 param ${base.filter(n => n.scope === "param").length}` },
            { k: "lot", l: `📦 lot ${base.filter(n => n.scope === "lot").length}` },
          ].map(b => {
            const active = noteFilter === b.k;
            return (
              <span
                key={b.k}
                onClick={() => setNoteFilter(b.k)}
                style={{
                  padding: "2px 8px",
                  borderRadius: 10,
                  cursor: "pointer",
                  background: active ? "var(--accent)" : "var(--bg-card)",
                  color: active ? "var(--bg-secondary)" : "var(--text-secondary)",
                  fontWeight: active ? 700 : 500,
                  border: `1px solid ${active ? "var(--accent)" : "var(--border)"}`,
                }}
              >
                {b.l}
              </span>
            );
          })}
        </div>

        {/* Search Box */}
        <div style={{ padding: "6px 16px", borderBottom: "1px solid var(--border)" }}>
          <input
            value={noteSearch}
            onChange={e => setNoteSearch(e.target.value)}
            placeholder="🔍 wafer id · param 이름 · 본문 검색"
            style={{
              width: "100%",
              padding: "5px 8px",
              borderRadius: 4,
              border: "1px solid var(--border)",
              background: "var(--bg-primary)",
              color: "var(--text-primary)",
              fontSize: 13,
              boxSizing: "border-box",
            }}
          />
        </div>

        {/* Notes List */}
        <div
          style={{
            flex: 1,
            overflowY: "auto",
            padding: "8px 14px",
            display: "flex",
            flexDirection: "column",
            gap: 6,
            minHeight: 180,
            maxHeight: 380,
          }}
        >
          {loading ? (
            <div style={{ padding: 24, textAlign: "center", color: "var(--text-secondary)", fontSize: 13 }}>
              노트를 불러오는 중...
            </div>
          ) : filtered.length === 0 ? (
            <div style={{ padding: 24, textAlign: "center", color: "var(--text-secondary)", fontSize: 13 }}>
              기록된 노트 없음
            </div>
          ) : (
            [...filtered]
              .sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""))
              .map(n => {
                const parts = (n.key || "").split("__");
                const wid = (parts[2] || "").replace(/^W/, "");
                const param = n.scope === "param" ? parts[3] || "" : "";
                const lotOf = n.scope === "lot" ? parts[2] || "" : "";
                const isMine = (n.username || "") === me;
                const badge =
                  n.scope === "wafer"
                    ? { bg: "rgba(59,130,246,0.95)", txt: `🏷 W${wid}` }
                    : n.scope === "param"
                    ? { bg: "rgba(139,92,246,0.95)", txt: `💬 W${wid}·${param}` }
                    : n.scope === "lot"
                    ? { bg: "rgba(22,163,74,0.95)", txt: `📦 ${lotOf || cleanLot}` }
                    : { bg: "rgba(107,114,128,0.95)", txt: n.scope };
                const time = (n.created_at || "").replace("T", " ").slice(5, 16);
                const expanded = expandedNoteId === n.id;
                const imgs = Array.isArray(n.images) ? n.images : [];
                const comments = Array.isArray(n.comments) ? n.comments : [];

                return (
                  <div
                    key={n.id}
                    title={expanded ? "클릭해서 접기" : "클릭해서 전체 내용 보기"}
                    onClick={() => setExpandedNoteId(expanded ? "" : n.id)}
                    style={{
                      display: "grid",
                      gridTemplateColumns: "minmax(0,1fr)",
                      gap: expanded ? 6 : 0,
                      padding: "5px 8px",
                      borderRadius: 5,
                      background: expanded ? "var(--bg-secondary)" : "var(--bg-card)",
                      border: "1px solid var(--border)",
                      fontSize: 13,
                      minHeight: 28,
                      cursor: "pointer",
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
                      <span
                        style={{
                          flexShrink: 0,
                          fontSize: 12,
                          fontWeight: 700,
                          padding: "1px 6px",
                          borderRadius: 8,
                          background: badge.bg,
                          color: "var(--bg-secondary)",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {badge.txt}
                      </span>
                      <span
                        style={{
                          flex: 1,
                          whiteSpace: "nowrap",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          color: "var(--text-primary)",
                        }}
                      >
                        {n.text || "(이미지)"}
                      </span>
                      {imgs.length > 0 && (
                        <span
                          style={{
                            flexShrink: 0,
                            fontSize: 11,
                            padding: "1px 6px",
                            borderRadius: 8,
                            background: "rgba(59,130,246,0.15)",
                            color: "rgba(59,130,246,0.95)",
                            fontWeight: 700,
                          }}
                        >
                          이미지 {imgs.length}
                        </span>
                      )}
                      {comments.length > 0 && (
                        <span
                          style={{
                            flexShrink: 0,
                            fontSize: 11,
                            padding: "1px 6px",
                            borderRadius: 8,
                            background: "var(--bg-tertiary)",
                            color: "var(--text-secondary)",
                            fontWeight: 700,
                          }}
                        >
                          답글 {comments.length}
                        </span>
                      )}
                      <span style={{ flexShrink: 0, fontSize: 12, color: "var(--text-secondary)", fontFamily: "monospace" }}>
                        {n.username}
                      </span>
                      <span style={{ flexShrink: 0, fontSize: 12, color: "var(--text-secondary)", fontFamily: "monospace" }}>
                        {time}
                      </span>
                      {isMine && (
                        <span
                          onClick={e => {
                            e.stopPropagation();
                            deleteNote(n.id);
                          }}
                          title="작성자만 삭제 가능"
                          style={{
                            flexShrink: 0,
                            cursor: "pointer",
                            fontSize: 15,
                            fontWeight: 700,
                            color: "rgba(239,68,68,0.95)",
                            padding: "0 4px",
                          }}
                        >
                          ×
                        </span>
                      )}
                    </div>
                    {expanded && (
                      <div style={{ display: "grid", gap: 8, padding: "6px 4px", borderTop: "1px dashed var(--border)" }}>
                        {n.text && (
                          <div style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", lineHeight: 1.5, color: "var(--text-primary)" }}>
                            {n.text}
                          </div>
                        )}
                        {imgs.length > 0 && (
                          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(96px,1fr))", gap: 6 }}>
                            {imgs.map((im, ii) => (
                              <a
                                key={im.url || ii}
                                href={authSrc(im.url)}
                                target="_blank"
                                rel="noreferrer"
                                onClick={e => e.stopPropagation()}
                                title={im.filename || "image"}
                                style={{
                                  border: "1px solid var(--border)",
                                  borderRadius: 4,
                                  overflow: "hidden",
                                  background: "var(--bg-primary)",
                                  height: 86,
                                  display: "flex",
                                  alignItems: "center",
                                  justifyContent: "center",
                                }}
                              >
                                <img
                                  src={authSrc(im.url)}
                                  alt={im.filename || "note image"}
                                  style={{ maxWidth: "100%", maxHeight: "100%", objectFit: "contain", display: "block" }}
                                />
                              </a>
                            ))}
                          </div>
                        )}
                        {comments.length > 0 && (
                          <div style={{ display: "grid", gap: 5 }}>
                            {comments.map(c => (
                              <div
                                key={c.id || c.created_at}
                                style={{ padding: "5px 7px", borderRadius: 4, background: "var(--bg-card)", border: "1px solid var(--border)" }}
                              >
                                <div style={{ display: "flex", gap: 6, color: "var(--text-secondary)", fontSize: 11, marginBottom: 3 }}>
                                  <span>{c.username || "-"}</span>
                                  <span>{(c.created_at || "").replace("T", " ").slice(5, 16)}</span>
                                </div>
                                {c.text && <div style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{c.text}</div>}
                                {Array.isArray(c.images) && c.images.length > 0 && (
                                  <div style={{ display: "flex", gap: 5, flexWrap: "wrap", marginTop: 5 }}>
                                    {c.images.map((im, ii) => (
                                      <a key={im.url || ii} href={authSrc(im.url)} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()}>
                                        <img
                                          src={authSrc(im.url)}
                                          alt={im.filename || "comment image"}
                                          style={{ width: 64, height: 48, objectFit: "cover", border: "1px solid var(--border)", borderRadius: 4 }}
                                        />
                                      </a>
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
                );
              })
          )}
        </div>

        {/* Draft Panel */}
        <div
          style={{
            padding: "10px 16px",
            borderTop: "1px solid var(--border)",
            display: "flex",
            flexDirection: "column",
            gap: 6,
            background: "var(--bg-secondary)",
          }}
        >
          <div style={{ fontSize: 13, color: "var(--text-secondary)", display: "flex", alignItems: "center", gap: 6 }}>
            <span>대상:</span>
            <span style={{ color: "rgba(22,163,74,0.95)", fontWeight: 700 }}>
              📦 LOT {cleanLot}
            </span>
            {noteDraft && (
              <span style={{ marginLeft: "auto" }}>
                <span
                  onClick={clearNoteDraft}
                  style={{ cursor: "pointer", color: "var(--text-secondary)", fontSize: 12 }}
                >
                  ✕ 취소
                </span>
              </span>
            )}
          </div>
          <textarea
            value={noteDraft}
            onChange={e => setNoteDraft(e.target.value)}
            onPaste={handleNotePaste}
            placeholder="새 노트 내용… (이미지 붙여넣기 지원)"
            rows={2}
            style={{
              padding: "6px 10px",
              borderRadius: 5,
              border: "1px solid var(--border)",
              background: "var(--bg-primary)",
              color: "var(--text-primary)",
              fontSize: 13,
              resize: "vertical",
              fontFamily: "inherit",
              boxSizing: "border-box",
              width: "100%",
            }}
          />
          <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
            <label
              style={{
                padding: "4px 9px",
                borderRadius: 4,
                border: "1px solid var(--border)",
                background: "var(--bg-card)",
                color: "var(--text-secondary)",
                fontSize: 12,
                cursor: noteUploading ? "wait" : "pointer",
              }}
            >
              📷 이미지 첨부
              <input
                type="file"
                accept="image/*"
                multiple
                disabled={noteUploading}
                onChange={e => {
                  uploadNoteFiles(e.target.files);
                  e.target.value = "";
                }}
                style={{ display: "none" }}
              />
            </label>
            {noteUploading && <span style={{ fontSize: 12, color: "var(--accent)" }}>업로드 중...</span>}
            {noteImages.map((im, i) => (
              <span
                key={im.url || i}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 4,
                  padding: "2px 6px",
                  borderRadius: 4,
                  border: "1px solid var(--border)",
                  background: "var(--bg-card)",
                  fontSize: 12,
                }}
              >
                <img src={authSrc(im.url)} alt="" style={{ width: 22, height: 16, objectFit: "cover", borderRadius: 3 }} />
                <span style={{ maxWidth: 90, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {im.filename || "image"}
                </span>
                <span
                  onClick={() => setNoteImages(prev => prev.filter((_, idx) => idx !== i))}
                  style={{ cursor: "pointer", color: "var(--text-secondary)", fontWeight: 700 }}
                >
                  ×
                </span>
              </span>
            ))}
            <span style={{ marginLeft: "auto" }}>
              <button
                type="button"
                onClick={addNote}
                disabled={!canSave}
                style={{
                  padding: "5px 14px",
                  borderRadius: 4,
                  border: "none",
                  background: "var(--accent)",
                  color: "var(--bg-secondary)",
                  fontSize: 13,
                  fontWeight: 600,
                  cursor: canSave ? "pointer" : "not-allowed",
                  opacity: canSave ? 1 : 0.5,
                }}
              >
                {saving ? "저장 중..." : `저장 (${me || "anonymous"})`}
              </button>
            </span>
          </div>
        </div>
      </div>
    </Modal>
  );
}
