import { useEffect, useMemo, useState } from "react";
import { Button } from "./UXKit";
import { mergeProductOrder, normalizeProductOrder } from "../lib/productOrder";


export default function ProductOrderEditor({ products, productOrder, onSave, busy = false }) {
  const available = useMemo(() => normalizeProductOrder(products), [products]);
  const merged = useMemo(() => mergeProductOrder(available, productOrder), [available, productOrder]);
  const [draft, setDraft] = useState(merged);

  useEffect(() => { setDraft(merged); }, [merged]);

  const move = (index, delta) => {
    const target = index + delta;
    if (target < 0 || target >= draft.length) return;
    setDraft((current) => {
      const next = [...current];
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  };
  const dirty = JSON.stringify(draft) !== JSON.stringify(merged);

  return (
    <section style={{ display: "grid", gap: 8 }}>
      <div>
        <div style={{ fontSize: 13, fontWeight: 750, color: "var(--text-primary)" }}>제품 선택 순서</div>
        <div style={{ marginTop: 2, fontSize: 11, lineHeight: 1.5, color: "var(--text-secondary)" }}>
          이 순서는 SplitTable·랏 관리·대시보드에 공통 적용됩니다. 새 제품은 목록 끝에 이름순으로 붙습니다.
        </div>
      </div>
      <div style={{ maxHeight: 280, overflowY: "auto", border: "1px solid var(--border)", borderRadius: 7 }}>
        {draft.length ? draft.map((name, index) => (
          <div key={name} style={{ display: "grid", gridTemplateColumns: "34px minmax(0, 1fr) 28px 28px", gap: 4, alignItems: "center", padding: "5px 7px", borderBottom: index === draft.length - 1 ? 0 : "1px solid var(--border)" }}>
            <span style={{ color: "var(--text-secondary)", fontSize: 11, fontVariantNumeric: "tabular-nums" }}>{index + 1}</span>
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 12.5, fontWeight: 650 }}>{name}</span>
            <button type="button" aria-label={`${name} 위로`} title="위로" disabled={busy || index === 0} onClick={() => move(index, -1)} style={moveButtonStyle}>↑</button>
            <button type="button" aria-label={`${name} 아래로`} title="아래로" disabled={busy || index === draft.length - 1} onClick={() => move(index, 1)} style={moveButtonStyle}>↓</button>
          </div>
        )) : <div style={{ padding: 12, color: "var(--text-secondary)", fontSize: 12 }}>표시할 제품이 없습니다.</div>}
      </div>
      <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
        <Button variant="subtle" disabled={busy || !draft.length} onClick={() => setDraft([...draft].sort((a, b) => a.localeCompare(b)))}>이름순</Button>
        <Button variant="primary" disabled={busy || !dirty} onClick={() => onSave(draft)}>{busy ? "저장 중…" : "순서 저장"}</Button>
      </div>
    </section>
  );
}

const moveButtonStyle = {
  width: 28, height: 26, padding: 0, borderRadius: 5,
  border: "1px solid var(--border)", background: "var(--bg-card)",
  color: "var(--text-primary)", cursor: "pointer", fontWeight: 800,
};
