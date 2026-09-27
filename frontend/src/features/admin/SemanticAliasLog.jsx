import { useEffect, useMemo, useState } from "react";
import { Button, DataTable, Input, Select } from "../../components/ui";
import { sf } from "../../lib/api";

// 제품 별칭 · Inline/ET 별칭 · item_desc 별칭 변경 이력 (관리자 화면·홈 챗·백업 복원).
const KIND_LABELS = { product: "제품 별칭", item: "항목 별칭", desc: "item_desc 별칭" };
const VIA_LABELS = { admin: "관리자 화면", chat: "홈 챗 승인", import: "백업 복원" };

function when(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value || "") : date.toLocaleString();
}

function target(entry) {
  if (entry.kind === "product") return entry.product;
  const source = entry.kind === "item" ? entry.source_type || "INLINE" : "INLINE";
  const step = entry.step_id || (source === "ET" ? "" : "-");
  return [source, step, entry.item_id, entry.item_desc || entry.desc].filter(Boolean).join(" · ");
}

const COLUMNS = [
  { key: "at", label: "시각", width: 150, render: (row) => when(row.at) },
  { key: "actor", label: "작업자", width: 110 },
  { key: "via", label: "경로", width: 100, render: (row) => VIA_LABELS[row.via] || row.via },
  { key: "kind", label: "종류", width: 110, render: (row) => KIND_LABELS[row.kind] || row.kind },
  { key: "product", label: "제품", width: 110 },
  { key: "target", label: "대상", render: target },
  { key: "added", label: "추가", render: (row) => (row.added || []).join(", ") },
  { key: "removed", label: "삭제", render: (row) => (row.removed || []).join(", ") },
  { key: "after", label: "변경 후 별칭", render: (row) => (row.after || []).join(", ") },
];

export default function SemanticAliasLog({ product, refreshKey = 0 }) {
  const [scope, setScope] = useState("product");
  const [kind, setKind] = useState("");
  const [query, setQuery] = useState("");
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (scope === "product" && !product) return undefined;
    let active = true;
    const params = new URLSearchParams({ limit: "500" });
    if (scope === "product") params.set("product", product);
    if (kind) params.set("kind", kind);
    setLoading(true);
    setError("");
    sf(`/api/product-semantics/alias-log?${params.toString()}`)
      .then((data) => { if (active) setEntries(Array.isArray(data?.entries) ? data.entries : []); })
      .catch((err) => { if (active) setError(err.message || "변경 이력을 불러오지 못했습니다."); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [product, scope, kind, refreshKey, reloadKey]);

  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return entries;
    return entries.filter((entry) => [entry.actor, entry.product, target(entry), ...(entry.after || []), ...(entry.before || [])]
      .join(" ").toLowerCase().includes(needle));
  }, [entries, query]);

  return (
    <section style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: 4, padding: 20, display: "grid", gap: 12 }}>
      <div>
        <h4 style={{ margin: "0 0 4px 0", fontSize: 16, fontWeight: 700 }}>별칭 변경 이력</h4>
        <p style={{ margin: 0, fontSize: 13, color: "var(--text-secondary)" }}>
          제품 별칭, Inline/ET 항목 별칭, item_desc 별칭을 누가 언제 어떤 경로(관리자 화면 · 홈 챗 승인 · 백업 복원)로 바꿨는지 보여 줍니다. 최근 5,000건을 보관합니다.
        </p>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8 }}>
        <Select value={scope} onChange={(event) => setScope(event.target.value)} aria-label="조회 범위" style={{ width: "auto", minWidth: 150 }}>
          <option value="product">{product ? `${product}만` : "선택한 제품만"}</option>
          <option value="all">전체 제품</option>
        </Select>
        <Select value={kind} onChange={(event) => setKind(event.target.value)} aria-label="별칭 종류" style={{ width: "auto", minWidth: 170 }}>
          <option value="">모든 종류</option>
          <option value="product">제품 별칭</option>
          <option value="item">항목 별칭 (Inline/ET)</option>
          <option value="desc">item_desc 별칭</option>
        </Select>
        <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="작업자·Step·Item·별칭 검색" style={{ width: 260 }} aria-label="이력 검색" />
        <Button onClick={() => setReloadKey((value) => value + 1)} disabled={loading}>{loading ? "불러오는 중…" : "새로고침"}</Button>
        <small style={{ color: "var(--text-secondary)" }}>{rows.length}건</small>
      </div>
      <DataTable
        columns={COLUMNS}
        rows={rows}
        loading={loading && !entries.length}
        error={error}
        emptyTitle="변경 이력이 없습니다"
        emptyMessage="별칭을 저장하거나 홈 챗에서 승인하면 여기에 기록됩니다."
        caption="별칭 변경 이력"
      />
    </section>
  );
}
