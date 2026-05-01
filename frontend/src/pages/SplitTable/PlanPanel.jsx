export function ActiveCellModal({ activeCell, colValCache, setActiveCell, setPendingPlans }) {
  if (!activeCell) return null;
  const sugg = colValCache[activeCell.param] || [];
  const commit = (v) => {
    const t = (v ?? "").trim();
    if (t) setPendingPlans((p) => ({ ...p, [activeCell.key]: t }));
    setActiveCell(null);
  };
  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 9998, background: "rgba(0,0,0,0.55)", display: "flex", alignItems: "center", justifyContent: "center" }} onClick={() => setActiveCell(null)}>
      <div onClick={(e) => e.stopPropagation()} style={{ background: "var(--bg-secondary)", borderRadius: 10, padding: 18, width: 360, border: "1px solid var(--border)" }}>
        <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 4, fontFamily: "monospace" }}>{activeCell.key.split("|").slice(0, 2).join(" · ")}</div>
        <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 10, color: "var(--accent)", fontFamily: "monospace" }}>{activeCell.param}</div>
        <input
          autoFocus
          value={activeCell.value}
          onChange={(e) => setActiveCell((c) => ({ ...c, value: e.target.value }))}
          onKeyDown={(e) => {
            if (e.key === "Enter") commit(activeCell.value);
            else if (e.key === "Escape") setActiveCell(null);
          }}
          list={`cv-${activeCell.key}`}
          placeholder="값 입력 또는 아래 리스트 선택"
          style={{ width: "100%", padding: "8px 10px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg-card)", color: "var(--text-primary)", fontSize: 14, fontFamily: "monospace", boxSizing: "border-box" }}
        />
        <datalist id={`cv-${activeCell.key}`}>{sugg.map((v) => <option key={v} value={v} />)}</datalist>
        <div style={{ marginTop: 10, maxHeight: 180, overflow: "auto", border: "1px solid var(--border)", borderRadius: 6, background: "var(--bg-card)" }}>
          {sugg.length === 0
            ? <div style={{ padding: "10px 12px", fontSize: 14, color: "var(--text-secondary)" }}>{colValCache[activeCell.param] === undefined ? "로딩…" : "suggestion 없음"}</div>
            : sugg.slice(0, 100).map((v, i) => (
              <div
                key={i}
                onClick={() => commit(v)}
                style={{ padding: "6px 10px", fontSize: 14, fontFamily: "monospace", cursor: "pointer", borderBottom: i < sugg.length - 1 ? "1px solid var(--border)" : "none" }}
                onMouseEnter={(e) => { e.currentTarget.style.background = "var(--accent-glow)"; }}
                onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
              >
                {v}
              </div>
            ))}
        </div>
        {sugg.length > 0 && <div style={{ fontSize: 14, color: "var(--text-secondary)", marginTop: 6 }}>{sugg.length} 개 (전체 데이터셋 unique + plan 포함)</div>}
        <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
          <button onClick={() => commit(activeCell.value)} style={{ flex: 1, padding: "8px 12px", borderRadius: 6, border: "none", background: "var(--accent)", color: "var(--bg-secondary)", fontWeight: 600, cursor: "pointer", fontSize: 14 }}>Apply</button>
          <button onClick={() => setActiveCell(null)} style={{ padding: "8px 16px", borderRadius: 6, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", cursor: "pointer", fontSize: 14 }}>Cancel</button>
        </div>
      </div>
    </div>
  );
}

export function ConfirmModal({ showConfirm, pendingPlans, setShowConfirm, savePlans }) {
  if (!showConfirm) return null;
  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 9999, background: "rgba(0,0,0,0.6)", display: "flex", alignItems: "center", justifyContent: "center" }} onClick={() => setShowConfirm(false)}>
      <div onClick={(e) => e.stopPropagation()} style={{ background: "var(--bg-secondary)", borderRadius: 12, padding: 24, width: 400, border: "1px solid var(--border)", maxHeight: "80vh", overflow: "auto" }}>
        <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 12 }}>Confirm Changes</div>
        <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 16 }}>{Object.keys(pendingPlans).length} cells will be updated</div>
        {Object.entries(pendingPlans).map(([k, v]) => (
          <div key={k} style={{ fontSize: 14, padding: "4px 0", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between" }}>
            <span style={{ fontFamily: "monospace", color: "var(--text-secondary)", maxWidth: 250, overflow: "hidden", textOverflow: "ellipsis" }}>{k.split("|").pop()}</span>
            <span style={{ color: "rgba(249,115,22,0.95)", fontWeight: 600 }}>{v}</span>
          </div>
        ))}
        <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
          <button onClick={savePlans} style={{ flex: 1, padding: 10, borderRadius: 6, border: "none", background: "rgba(34,197,94,0.95)", color: "var(--bg-secondary)", fontWeight: 600, cursor: "pointer" }}>Confirm</button>
          <button onClick={() => setShowConfirm(false)} style={{ padding: "10px 20px", borderRadius: 6, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", cursor: "pointer" }}>Cancel</button>
        </div>
      </div>
    </div>
  );
}
