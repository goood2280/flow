import { useEffect, useState } from "react";
import { qs, sf } from "../../lib/api";
import { Banner, Button, DataTable, EmptyState, Field, Pill, TabStrip } from "../UXKit";
import Loading from "../Loading";

const KIND_OPTIONS = [
  { value: "", label: "전체" },
  { value: "semantic_proposal", label: "시멘틱 제안" },
  { value: "wiki_page", label: "Wiki page" },
  { value: "wiki_source", label: "Wiki source" },
  { value: "prompt_history", label: "Prompt trace" },
  { value: "knowledge_event", label: "지식 event" },
  { value: "knowledge_inventory", label: "지식 inventory" },
];

const DETAIL_TABS = [
  { k: "items", l: "요약" },
  { k: "proposals", l: "제안" },
  { k: "wiki", l: "Wiki" },
  { k: "traces", l: "Trace" },
  { k: "events", l: "Event" },
];

export default function KnowledgeOverviewTab() {
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const [tab, setTab] = useState("items");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");

  function reload() {
    setLoading(true);
    setErr("");
    loadOverview(q, kind, 60)
      .then(setData)
      .catch((e) => setErr(e?.message || "누적 지식 현황 로딩 실패"))
      .finally(() => setLoading(false));
  }

  useEffect(() => { reload(); }, []);

  const counts = data?.counts || {};

  return (
    <div style={{ padding: 20, display: "flex", flexDirection: "column", gap: 14 }}>
      <section style={surfaceStyle}>
        <div style={{ display: "flex", alignItems: "flex-end", gap: 10, flexWrap: "wrap" }}>
          <Field label="검색어" style={{ minWidth: 260, flex: "1 1 320px" }}>
            <input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") reload(); }} style={inputStyle()} />
          </Field>
          <Field label="kind">
            <select value={kind} onChange={(e) => setKind(e.target.value)} style={inputStyle({ minWidth: 170 })}>
              {KIND_OPTIONS.map((opt) => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
            </select>
          </Field>
          <Button variant="primary" onClick={reload} disabled={loading}>{loading ? "로딩..." : "조회"}</Button>
        </div>
      </section>

      {err && <Banner tone="warn">{err}</Banner>}
      {loading && <Loading text="누적 지식 현황 로딩..." size="md" />}

      {!loading && data && (
        <>
          {data.fallback && (
            <Banner tone="warn">
              통합 overview API를 찾지 못해 기존 Agent endpoint 조합으로 표시 중입니다. 서버 재시작 후 통합 카운트가 표시됩니다.
            </Banner>
          )}
          <section style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(140px, 1fr))", gap: 8 }}>
            <Counter label="제안 대기" value={counts.pending_semantic_proposals} tone="warn" />
            <Counter label="Wiki page" value={counts.wiki_pages} tone="accent" />
            <Counter label="Wiki source" value={counts.wiki_sources} tone="info" />
            <Counter label="Prompt trace" value={counts.prompt_history} tone="neutral" />
            <Counter label="지식 event" value={counts.knowledge_events} tone="ok" />
            <Counter label="Semantic change" value={counts.semantic_changes} tone="neutral" />
            <Counter label="Inventory" value={counts.knowledge_inventory} tone="neutral" />
            <Counter label="요약 행" value={counts.recent_items} tone="accent" />
          </section>

          <section style={surfaceStyle}>
            <TabStrip items={DETAIL_TABS} active={tab} onChange={setTab} />
            <div style={{ paddingTop: 12 }}>
              {tab === "items" && <RecentItems rows={data.recent_items || []} />}
              {tab === "proposals" && <ProposalRows rows={data.pending_semantic_proposals || []} />}
              {tab === "wiki" && <WikiRows pages={data.recent_wiki_pages || []} sources={data.recent_wiki_sources || []} />}
              {tab === "traces" && <PromptRows rows={data.recent_prompt_history || []} />}
              {tab === "events" && <EventRows rows={data.recent_knowledge_events || []} />}
            </div>
          </section>
        </>
      )}
    </div>
  );
}

async function loadOverview(q, kind, limit) {
  try {
    return await sf("/api/agent/knowledge/overview" + qs({ q, kind, limit }));
  } catch (e) {
    if (e?.status !== 404 && !String(e?.message || "").toLowerCase().includes("not found")) {
      throw e;
    }
    return loadFallbackOverview(q, kind, limit);
  }
}

async function safeGet(url, fallback) {
  try {
    return await sf(url);
  } catch (_) {
    return fallback;
  }
}

async function loadFallbackOverview(q, kind, limit) {
  const [inventory, proposals, pages, sources, prompts, changes] = await Promise.all([
    safeGet("/api/agent/knowledge-inventory" + qs({ q, kind: legacyInventoryKind(kind) }), { items: [], counts: {} }),
    safeGet("/api/agent/semantic/proposals" + qs({ status: "pending", limit }), { proposals: [] }),
    safeGet("/api/agent/wiki/pages" + qs({ q, limit }), { pages: [] }),
    safeGet("/api/agent/wiki/sources" + qs({ q, limit }), { sources: [] }),
    safeGet("/api/agent/prompt-history" + qs({ limit }), { rows: [] }),
    safeGet("/api/agent/semantic/changes" + qs({ limit }), { changes: [] }),
  ]);
  const query = String(q || "").trim().toLowerCase();
  const kindFilter = String(kind || "").trim();
  const inventoryItems = (inventory.items || []).filter((row) => legacyAllowed(kindFilter, "knowledge_inventory", row.kind) && queryHit(row, query));
  const pending = (proposals.proposals || []).filter((row) => legacyAllowed(kindFilter, "semantic_proposal", row.category) && queryHit(row, query));
  const wikiPages = (pages.pages || []).filter((row) => legacyAllowed(kindFilter, "wiki_page", row.kind) && queryHit(row, query));
  const wikiSources = (sources.sources || []).filter((row) => legacyAllowed(kindFilter, "wiki_source", row.source_type) && queryHit(row, query));
  const promptRows = (prompts.rows || []).filter((row) => legacyAllowed(kindFilter, "prompt_history", row.feature || row.intent) && queryHit(row, query));
  const semanticChanges = (changes.changes || []).filter((row) => legacyAllowed(kindFilter, "semantic_change", row.scope) && queryHit(row, query));
  const recentItems = [
    ...inventoryItems.map((row) => overviewItem("knowledge_inventory", row, row.kind)),
    ...pending.map((row) => overviewItem("semantic_proposal", row, row.category)),
    ...wikiPages.map((row) => overviewItem("wiki_page", row, row.kind)),
    ...wikiSources.map((row) => overviewItem("wiki_source", row, row.source_type)),
    ...promptRows.map((row) => overviewItem("prompt_history", row, row.feature || row.intent)),
    ...semanticChanges.map((row) => overviewItem("semantic_change", row, row.scope)),
  ].sort((a, b) => String(b.timestamp || "").localeCompare(String(a.timestamp || ""))).slice(0, limit);
  return {
    ok: true,
    fallback: true,
    query: q,
    kind,
    limit,
    counts: {
      knowledge_inventory: inventoryItems.length,
      semantic_proposals: pending.length,
      pending_semantic_proposals: pending.length,
      semantic_changes: semanticChanges.length,
      wiki_pages: wikiPages.length,
      wiki_sources: wikiSources.length,
      prompt_history: promptRows.length,
      knowledge_events: 0,
      recent_items: recentItems.length,
      inventory_by_kind: inventory.counts || {},
    },
    recent_items: recentItems,
    pending_semantic_proposals: pending.slice(0, limit),
    recent_wiki_pages: wikiPages.slice(0, limit),
    recent_wiki_sources: wikiSources.slice(0, limit),
    recent_prompt_history: promptRows.slice(0, limit),
    recent_knowledge_events: [],
    recent_semantic_changes: semanticChanges.slice(0, limit),
  };
}

function Counter({ label, value, tone }) {
  return (
    <div style={surfaceStyle}>
      <div style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 4 }}>{label}</div>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 20, fontWeight: 800 }}>{Number(value || 0)}</span>
        <Pill tone={tone}>{label}</Pill>
      </div>
    </div>
  );
}

function RecentItems({ rows }) {
  if (!rows.length) return <EmptyState title="표시할 지식 요약이 없습니다" />;
  return (
    <DataTable
      rows={rows}
      columns={[
        { key: "timestamp", label: "time", width: 170, render: (r) => shortTime(r.timestamp) },
        { key: "kind", label: "kind", width: 150, render: (r) => <Pill tone={kindTone(r.kind)}>{r.kind}</Pill> },
        { key: "row_kind", label: "row", width: 140 },
        { key: "title", label: "title", width: 240 },
        { key: "summary", label: "summary" },
        { key: "status", label: "status", width: 110 },
      ]}
      maxHeight={520}
    />
  );
}

function ProposalRows({ rows }) {
  if (!rows.length) return <EmptyState title="대기 중인 시멘틱 제안이 없습니다" />;
  return (
    <DataTable
      rows={rows}
      columns={[
        { key: "created_at", label: "created", width: 170, render: (r) => shortTime(r.created_at) },
        { key: "term", label: "term", width: 180 },
        { key: "category", label: "category", width: 120 },
        { key: "canonical_match", label: "canonical", width: 150 },
        { key: "confidence", label: "score", width: 70, render: (r) => Number(r.confidence || 0).toFixed(2) },
        { key: "rationale", label: "rationale" },
      ]}
      maxHeight={520}
    />
  );
}

function WikiRows({ pages, sources }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
      <div>
        <h3 style={subTitleStyle}>Wiki page</h3>
        <DataTable
          rows={pages}
          columns={[
            { key: "updated_at", label: "updated", width: 160, render: (r) => shortTime(r.updated_at || r.created_at) },
            { key: "kind", label: "kind", width: 110 },
            { key: "title", label: "title" },
          ]}
          empty="Wiki page 없음"
          maxHeight={420}
        />
      </div>
      <div>
        <h3 style={subTitleStyle}>Wiki source</h3>
        <DataTable
          rows={sources}
          columns={[
            { key: "created_at", label: "created", width: 160, render: (r) => shortTime(r.created_at) },
            { key: "source_type", label: "type", width: 110 },
            { key: "title", label: "title" },
          ]}
          empty="Wiki source 없음"
          maxHeight={420}
        />
      </div>
    </div>
  );
}

function PromptRows({ rows }) {
  if (!rows.length) return <EmptyState title="최근 prompt trace가 없습니다" />;
  return (
    <DataTable
      rows={rows}
      columns={[
        { key: "timestamp", label: "time", width: 170, render: (r) => shortTime(r.timestamp) },
        { key: "prompt", label: "prompt", width: 300 },
        { key: "action", label: "action", width: 210 },
        { key: "status", label: "status", width: 110 },
        { key: "answer_excerpt", label: "answer" },
      ]}
      maxHeight={520}
    />
  );
}

function EventRows({ rows }) {
  if (!rows.length) return <EmptyState title="최근 지식 event가 없습니다" />;
  return (
    <DataTable
      rows={rows}
      columns={[
        { key: "created_at", label: "created", width: 170, render: (r) => shortTime(r.created_at) },
        { key: "event_type", label: "event", width: 140 },
        { key: "source_type", label: "source", width: 120 },
        { key: "title", label: "title", width: 240 },
        { key: "summary", label: "summary" },
      ]}
      maxHeight={520}
    />
  );
}

function shortTime(value) {
  return String(value || "").replace("T", " ").slice(0, 19) || "-";
}

function kindTone(kind) {
  switch (kind) {
    case "semantic_proposal": return "warn";
    case "wiki_page": return "accent";
    case "wiki_source": return "info";
    case "knowledge_event": return "ok";
    default: return "neutral";
  }
}

function legacyInventoryKind(kind) {
  const value = String(kind || "");
  return ["semantic_proposal", "semantic_change", "wiki_page", "wiki_source", "prompt_history", "knowledge_event"].includes(value) ? "" : value;
}

function legacyAllowed(filterKind, overviewKind, rowKind) {
  const value = String(filterKind || "");
  if (!value || value === "all") return true;
  const groups = {
    semantic: ["semantic_proposal", "semantic_change"],
    proposal: ["semantic_proposal"],
    wiki: ["wiki_page", "wiki_source"],
    source: ["wiki_source"],
    prompt: ["prompt_history"],
    trace: ["prompt_history"],
    event: ["knowledge_event"],
  };
  const allowed = groups[value] || [value];
  return allowed.includes(overviewKind) || String(rowKind || "") === value;
}

function queryHit(row, query) {
  if (!query) return true;
  try {
    return JSON.stringify(row || {}).toLowerCase().includes(query);
  } catch (_) {
    return false;
  }
}

function overviewItem(kind, row, rowKind) {
  const timestamp = row.timestamp || row.updated_at || row.created_at || row.ts || "";
  const title = row.title || row.term || row.prompt || row.action || rowKind || kind;
  const summary = row.summary || row.content_preview || row.answer_excerpt || row.rationale || "";
  return {
    id: row.id || row.doc_id || row.source_id || row.event_id || row.key || "",
    kind,
    row_kind: rowKind || "",
    title,
    summary,
    timestamp,
    source: row.source || row.source_type || row.event || "",
    status: row.status || row.result_type || "",
    tags: row.tags || [],
    raw: row,
  };
}

const surfaceStyle = {
  border: "1px solid var(--border)",
  borderRadius: 6,
  background: "var(--bg-secondary)",
  padding: 12,
};
const subTitleStyle = { fontSize: 13, fontWeight: 800, margin: "0 0 8px" };
const inputStyle = (extra = {}) => ({
  padding: "6px 8px",
  borderRadius: 4,
  border: "1px solid var(--border)",
  background: "var(--bg-primary)",
  color: "var(--text-primary)",
  fontSize: 13,
  ...extra,
});
