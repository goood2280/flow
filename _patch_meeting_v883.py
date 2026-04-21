"""v8.8.3 patcher — add meeting visibility (group_ids) picker to create/edit modals.
One-shot script; safe to re-run (idempotent via marker comments).
"""
from pathlib import Path

FILE = Path(__file__).parent / "frontend" / "src" / "pages" / "My_Meeting.jsx"
src = FILE.read_text(encoding="utf-8")

MARKER = "/* v8.8.3: 공개범위 group_ids FE picker */"
if MARKER in src:
    print("already patched, skipping")
    raise SystemExit(0)

# 1) Extend initial draft state with group_ids: [].
old_draft_init = '''  const [draft, setDraft] = useState({
    title: "", owner: "", first_scheduled_at: "",
    recurrence: { type: "none", count_per_week: 1, weekday: [], note: "" },
    category: "",
  });'''
new_draft_init = '''  const [draft, setDraft] = useState({
    title: "", owner: "", first_scheduled_at: "",
    recurrence: { type: "none", count_per_week: 1, weekday: [], note: "" },
    category: "",
    group_ids: [],  /* v8.8.3: 공개범위 group_ids FE picker */
  });'''
assert old_draft_init in src, "initial draft block not found"
src = src.replace(old_draft_init, new_draft_init, 1)

# 2) submitCreate POST — include group_ids.
old_create_post = '''      category: draft.category || "",
    }).then(d => {'''
new_create_post = '''      category: draft.category || "",
      group_ids: draft.group_ids || [],  /* v8.8.3 */
    }).then(d => {'''
assert old_create_post in src
src = src.replace(old_create_post, new_create_post, 1)

# 3) reset draft after create.
old_reset = '''      setDraft({
        title: "", owner: "", first_scheduled_at: "",
        recurrence: { type: "none", count_per_week: 1, weekday: [], note: "" },
        category: "",
      });'''
new_reset = '''      setDraft({
        title: "", owner: "", first_scheduled_at: "",
        recurrence: { type: "none", count_per_week: 1, weekday: [], note: "" },
        category: "",
        group_ids: [],  /* v8.8.3 */
      });'''
assert old_reset in src
src = src.replace(old_reset, new_reset, 1)

# 4) startEditMeta → load group_ids from selected.
old_meta_init = '''    setMetaDraft({
      title: selected.title || "",
      owner: selected.owner || "",
      status: selected.status || "active",
      category: selected.category || "",
      recurrence: { ...(selected.recurrence || { type: "none", count_per_week: 0, weekday: [], note: "" }) },
    });'''
new_meta_init = '''    setMetaDraft({
      title: selected.title || "",
      owner: selected.owner || "",
      status: selected.status || "active",
      category: selected.category || "",
      recurrence: { ...(selected.recurrence || { type: "none", count_per_week: 0, weekday: [], note: "" }) },
      group_ids: Array.isArray(selected.group_ids) ? [...selected.group_ids] : [],  /* v8.8.3 */
    });'''
assert old_meta_init in src
src = src.replace(old_meta_init, new_meta_init, 1)

# 5) submitEditMeta POST — include group_ids.
old_update_post = '''      recurrence: {
        type: metaDraft.recurrence.type || "none",
        count_per_week: Number(metaDraft.recurrence.count_per_week) || 0,
        weekday: metaDraft.recurrence.weekday || [],
        note: metaDraft.recurrence.note || "",
      },
    }).then(() => { setEditingMeta(false); setMetaDraft(null); reload(); })'''
new_update_post = '''      recurrence: {
        type: metaDraft.recurrence.type || "none",
        count_per_week: Number(metaDraft.recurrence.count_per_week) || 0,
        weekday: metaDraft.recurrence.weekday || [],
        note: metaDraft.recurrence.note || "",
      },
      group_ids: metaDraft.group_ids || [],  /* v8.8.3 */
    }).then(() => { setEditingMeta(false); setMetaDraft(null); reload(); })'''
assert old_update_post in src
src = src.replace(old_update_post, new_update_post, 1)

# 6) Inject group_ids picker UI into meta-edit modal, right before the 저장/취소 button row.
old_meta_btn = '''                  <div />
                  <div style={{ display: "flex", gap: 6 }}>
                    <button onClick={submitEditMeta} style={btnPrimary}>저장</button>
                    <button onClick={() => { setEditingMeta(false); setMetaDraft(null); }} style={btnGhost}>취소</button>
                  </div>'''
new_meta_btn = '''                  {/* v8.8.3: 공개범위 group_ids FE picker */}
                  <span style={lbl}>공개 그룹</span>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                    {(allGroups || []).length === 0 && <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>(등록된 그룹 없음 — 모두에게 공개)</span>}
                    {(allGroups || []).map(g => {
                      const on = (metaDraft.group_ids || []).includes(g.id);
                      return (
                        <span key={g.id} onClick={() => {
                          const cur = metaDraft.group_ids || [];
                          const next = on ? cur.filter(x => x !== g.id) : [...cur, g.id];
                          setMetaDraft({ ...metaDraft, group_ids: next });
                        }} style={{ padding: "3px 10px", borderRadius: 999, fontSize: 11, cursor: "pointer", border: "1px solid var(--border)", background: on ? "var(--accent-glow)" : "transparent", color: on ? "var(--accent)" : "var(--text-secondary)" }}>
                          {on ? "✓ " : ""}{g.name}
                        </span>
                      );
                    })}
                    {(metaDraft.group_ids || []).length === 0 && (allGroups || []).length > 0 && (
                      <span style={{ fontSize: 10, color: "var(--text-secondary)", marginLeft: 4 }}>비워두면 모두에게 공개</span>
                    )}
                  </div>
                  <div />
                  <div style={{ display: "flex", gap: 6 }}>
                    <button onClick={submitEditMeta} style={btnPrimary}>저장</button>
                    <button onClick={() => { setEditingMeta(false); setMetaDraft(null); }} style={btnGhost}>취소</button>
                  </div>'''
assert old_meta_btn in src
src = src.replace(old_meta_btn, new_meta_btn, 1)

# 7) Inject group_ids picker into create modal, right before the 생성/취소 button row.
old_create_btn = '''            </div>
            <div style={{ display: "flex", gap: 6, marginTop: 14, justifyContent: "flex-end" }}>
              <button onClick={() => setCreating(false)} style={btnGhost}>취소</button>
              <button onClick={submitCreate} style={btnPrimary}>생성</button>
            </div>'''
new_create_btn = '''              {/* v8.8.3: 공개범위 group_ids FE picker (create) */}
              <span style={lbl}>공개 그룹</span>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                {(allGroups || []).length === 0 && <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>(등록된 그룹 없음 — 모두에게 공개)</span>}
                {(allGroups || []).map(g => {
                  const on = (draft.group_ids || []).includes(g.id);
                  return (
                    <span key={g.id} onClick={() => {
                      const cur = draft.group_ids || [];
                      const next = on ? cur.filter(x => x !== g.id) : [...cur, g.id];
                      setDraft({ ...draft, group_ids: next });
                    }} style={{ padding: "3px 10px", borderRadius: 999, fontSize: 11, cursor: "pointer", border: "1px solid var(--border)", background: on ? "var(--accent-glow)" : "transparent", color: on ? "var(--accent)" : "var(--text-secondary)" }}>
                      {on ? "✓ " : ""}{g.name}
                    </span>
                  );
                })}
                {(draft.group_ids || []).length === 0 && (allGroups || []).length > 0 && (
                  <span style={{ fontSize: 10, color: "var(--text-secondary)", marginLeft: 4 }}>비워두면 모두에게 공개</span>
                )}
              </div>
            </div>
            <div style={{ display: "flex", gap: 6, marginTop: 14, justifyContent: "flex-end" }}>
              <button onClick={() => setCreating(false)} style={btnGhost}>취소</button>
              <button onClick={submitCreate} style={btnPrimary}>생성</button>
            </div>'''
assert old_create_btn in src
src = src.replace(old_create_btn, new_create_btn, 1)

FILE.write_text(src, encoding="utf-8")
print("My_Meeting.jsx patched OK")
