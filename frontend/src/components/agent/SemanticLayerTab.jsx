// SemanticLayerTab — AgentV2의 "시멘틱 레이어" 항목 본체 (M7).
// 3가지 sub-view 한 페이지: 스키마 관계 / 운영 Wiki / 컬럼 카탈로그.
// 스키마 관계와 운영 Wiki는 기존 AgentLegacy의 panel을 재사용 (코드 복제 X).
import { useEffect, useMemo, useState } from "react";
import { sf } from "../../lib/api";
import { Banner, EmptyState, TabStrip } from "../UXKit";
import { AgentWikiPanel, SchemaRelationsPanel } from "./AgentLegacy";
import Loading from "../Loading";

const SUB_TABS = [
  { k: "inventory", l: "DB / 파일 인벤토리" },
  { k: "relations", l: "스키마 관계" },
  { k: "wiki", l: "운영 Wiki" },
  { k: "columns", l: "컬럼 카탈로그" },
];

export default function SemanticLayerTab({ user, canManageWiki }) {
  const [tab, setTab] = useState("inventory");

  return (
    <div style={{ padding: 20, display: "flex", flexDirection: "column", gap: 12 }}>
      <header>
        <h2 style={{ fontSize: 18, margin: "0 0 4px" }}>🧠 시멘틱 레이어</h2>
        <p style={{ fontSize: 12, color: "var(--text-secondary)", margin: 0 }}>
          단위 AI들이 자연어 prompt를 데이터로 연결할 때 쓰는 의미 자원입니다.
          DB·파일 인벤토리 / 스키마 관계 / agent_wiki / 컬럼 카탈로그 (ColumnDoc) 네 가지를 한 화면에서 운영합니다.
        </p>
      </header>
      <TabStrip items={SUB_TABS} active={tab} onChange={setTab} />
      <div>
        {tab === "inventory" && <SourceInventoryView />}
        {tab === "relations" && <SchemaRelationsPanel canManage={!!canManageWiki} />}
        {tab === "wiki" && <AgentWikiPanel canManage={!!canManageWiki} />}
        {tab === "columns" && <ColumnCatalogView />}
      </div>
    </div>
  );
}

function SourceInventoryView() {
  const [sources, setSources] = useState([]);
  const [meta, setMeta] = useState({ total: 0, relations_total: 0 });
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    sf("/api/agent/source-inventory")
      .then((d) => {
        if (cancelled) return;
        setSources((d && d.sources) || []);
        setMeta({ total: d?.total || 0, relations_total: d?.relations_total || 0 });
      })
      .catch((e) => !cancelled && setErr(e?.message || "데이터 소스 인벤토리 로딩 실패"))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, []);

  if (loading) return <div style={{ padding: 24 }}><Loading text="인벤토리 로딩..." size="md" /></div>;
  if (err) return <div style={{ padding: 12 }}><Banner tone="warn">{err}</Banner></div>;
  if (sources.length === 0) return <EmptyState title="등록된 데이터 소스가 없습니다" hint="schema_relations.json에 source를 추가하면 여기 나타납니다" />;

  const dbs = sources.filter((s) => s.source_type === "db");
  const files = sources.filter((s) => s.source_type !== "db");

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <p style={{ fontSize: 12, color: "var(--text-secondary)", margin: 0 }}>
        총 데이터 소스 {meta.total}개 · 등록된 join relation {meta.relations_total}개. 각 source가 다른 source와 어떻게 연결되는지 한눈에 봅니다. 행 클릭은 아직 비활성 — 편집은 '스키마 관계' 탭에서.
      </p>
      <SourceGroup title="DB 소스" sources={dbs} />
      <SourceGroup title="파일 소스" sources={files} />
    </div>
  );
}

function SourceGroup({ title, sources }) {
  if (sources.length === 0) return null;
  return (
    <div>
      <h3 style={{ fontSize: 13, fontWeight: 700, margin: "0 0 6px", color: "var(--accent)" }}>{title} ({sources.length})</h3>
      <div style={{ overflow: "auto", border: "1px solid var(--border)", borderRadius: 6 }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr style={{ background: "var(--bg-hover)", color: "var(--text-secondary)" }}>
              <th style={th()}>label</th>
              <th style={th()}>source_id</th>
              <th style={th()}>join 회수</th>
              <th style={th()}>canonical join keys</th>
              <th style={th()}>연결된 다른 소스</th>
            </tr>
          </thead>
          <tbody>
            {sources.map((s) => (
              <tr key={`${s.source_type}:${s.label}:${s.source_id}`} style={{ borderTop: "1px solid var(--border)" }}>
                <td style={td("160px", true)}>{s.label}</td>
                <td style={td("220px", true)}>{s.source_id || "—"}</td>
                <td style={td("80px")}>{s.relation_count}</td>
                <td style={td("200px")}>{(s.join_keys || []).join(", ") || "—"}</td>
                <td style={td()}>{(s.connects_to || []).join(", ") || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ColumnCatalogView() {
  const [items, setItems] = useState([]);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    sf("/api/agent/column-catalog")
      .then((d) => !cancelled && setItems((d && d.items) || []))
      .catch((e) => !cancelled && setErr(e?.message || "컬럼 카탈로그 로딩 실패"))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, []);

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return items;
    return items.filter((it) =>
      String(it.name || "").toLowerCase().includes(q) ||
      String(it.meaning || "").toLowerCase().includes(q) ||
      (it.used_by || []).some((u) => String(u).toLowerCase().includes(q))
    );
  }, [items, filter]);

  if (loading) return <div style={{ padding: 24 }}><Loading text="컬럼 카탈로그 로딩..." size="md" /></div>;
  if (err) return <div style={{ padding: 12 }}><Banner tone="warn">{err}</Banner></div>;
  if (items.length === 0) return <EmptyState title="등록된 컬럼이 없습니다" hint="ColumnDoc은 backend/core/flowi_units/registry.py에서 정의됩니다" />;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="컬럼/의미/사용처 검색"
          style={{ padding: "4px 8px", fontSize: 13, borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", minWidth: 240 }}
        />
        <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>{filtered.length} / {items.length}</span>
      </div>
      <div style={{ overflow: "auto", border: "1px solid var(--border)", borderRadius: 6 }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr style={{ background: "var(--bg-hover)", color: "var(--text-secondary)", position: "sticky", top: 0 }}>
              <th style={th()}>이름</th>
              <th style={th()}>의미</th>
              <th style={th()}>예시</th>
              <th style={th()}>사용처 (unit AI)</th>
              <th style={th()}>Wiki doc</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((c) => (
              <tr key={c.name} style={{ borderTop: "1px solid var(--border)" }}>
                <td style={td("160px", true)}><code>{c.name}</code></td>
                <td style={td()}>{c.meaning || <span style={{ color: "var(--text-secondary)" }}>(미작성)</span>}</td>
                <td style={td("160px")}>{(c.sample_values || []).slice(0, 4).join(", ") || "—"}</td>
                <td style={td("200px")}>{(c.used_by || []).join(", ")}</td>
                <td style={td("180px", true)}>{c.wiki_doc_id || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p style={{ fontSize: 11, color: "var(--text-secondary)", margin: 0 }}>
        ※ ColumnDoc은 코드에서 관리됩니다 (backend/core/flowi_units/schema_columns.py 와 각 unit AI 모듈). 의미 추가/수정은 코드 PR로 합니다. Wiki schema_doc kind(4개)는 M7에서 중복으로 deprecate되었습니다.
      </p>
    </div>
  );
}

const th = () => ({ textAlign: "left", padding: "6px 10px", fontWeight: 700, fontSize: 11 });
const td = (width = "auto", mono = false) => ({ padding: "6px 10px", verticalAlign: "top", width, fontFamily: mono ? "monospace" : "inherit" });
