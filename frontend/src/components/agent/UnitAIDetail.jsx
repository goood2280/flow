// UnitAIDetail — Agent V2 우측 디테일.
// 한 unit AI의 자원 전체를 단일 페이지에 세로 스크롤로 표시.
// (a) 헤더/요약 (b) 데이터 & 컬럼 의미 (c) 시멘틱 바인딩 (d) prompt template
// (e) LLM profile (f) feature md (g) handler entry — 다중 탭 없음.
import { useEffect, useState } from "react";
import { sf } from "../../lib/api";
import { Banner, EmptyState } from "../UXKit";
import Loading from "../Loading";

export default function UnitAIDetail({ unitKey, user, canManageWiki }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!unitKey) return;
    let cancelled = false;
    setLoading(true); setErr(""); setData(null);
    sf(`/api/agent/unit-ai/${encodeURIComponent(unitKey)}/inspect`)
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setErr(e?.message || "unit AI inspect 로딩 실패"))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, [unitKey]);

  if (loading) return <div style={{ padding: 40, display: "flex", justifyContent: "center" }}><Loading text="unit AI 자원 로딩..." size="md" /></div>;
  if (err) return <div style={{ padding: 24 }}><Banner tone="warn">{err}</Banner></div>;
  if (!data || !data.ok) return <div style={{ padding: 24 }}><EmptyState title="unit AI를 선택하세요" /></div>;

  return (
    <div style={{ padding: 20, display: "flex", flexDirection: "column", gap: 18 }}>
      <Header data={data} />
      <DataSourcesSection sources={data.data_sources || []} />
      <SemanticSection bindings={data.semantic_bindings || {}} />
      <PromptTemplateSection tpl={data.prompt_template} />
      <LlmProfileSection profile={data.llm_profile} />
      <FeatureMdSection md={data.feature_md} />
      <HandlerEntrySection entry={data.handler_entry} />
    </div>
  );
}

// ── Header ──────────────────────────────────────────────
function Header({ data }) {
  return (
    <header style={{ borderBottom: "1px solid var(--border)", paddingBottom: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <h2 style={{ fontSize: 20, margin: 0 }}>🤖 {data.title}</h2>
        <code style={{ fontSize: 12, padding: "2px 6px", background: "var(--bg-secondary)", color: "var(--text-secondary)", borderRadius: 4 }}>{data.key}</code>
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
          LLM profile: <strong style={{ color: "var(--text-primary)" }}>{data.llm_profile || "—"}</strong>
        </span>
      </div>
    </header>
  );
}

// ── Section helpers ────────────────────────────────────
function Section({ title, hint, right, children }) {
  return (
    <section>
      <SectionHeader title={title} hint={hint} right={right} />
      <div style={{ marginTop: 8 }}>{children}</div>
    </section>
  );
}

function SectionHeader({ title, hint, right }) {
  return (
    <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
      <h3 style={{ fontSize: 14, fontWeight: 700, margin: 0, color: "var(--accent)" }}>{title}</h3>
      {hint && <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>{hint}</span>}
      <span style={{ flex: 1 }} />
      {right}
    </div>
  );
}

// ── Data sources & columns ─────────────────────────────
function DataSourcesSection({ sources }) {
  return (
    <Section
      title={`데이터 & 컬럼 의미 (${sources.length})`}
      hint="이 AI가 읽는 단일파일/DB와 각 컬럼이 무엇을 뜻하는지"
    >
      {sources.length === 0 && <EmptyState title="등록된 데이터 소스가 없습니다" hint="M2 PR에서 채워집니다" />}
      {sources.map((ds, i) => (
        <div key={i} style={{
          marginTop: 8, border: "1px solid var(--border)", borderRadius: 6,
          background: "var(--bg-secondary)", overflow: "hidden",
        }}>
          <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)", display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <span style={{ fontSize: 11, padding: "2px 6px", borderRadius: 3, background: "var(--accent-glow)", color: "var(--accent)", fontWeight: 700, letterSpacing: 0.3 }}>{ds.kind}</span>
            <code style={{ fontSize: 12, color: "var(--text-primary)" }}>{ds.path}</code>
          </div>
          {ds.description && (
            <div style={{ padding: "6px 12px", fontSize: 13, color: "var(--text-primary)", borderBottom: ds.columns.length ? "1px solid var(--border)" : "none" }}>
              {ds.description}
            </div>
          )}
          {ds.columns.length > 0 && <ColumnsTable columns={ds.columns} />}
        </div>
      ))}
    </Section>
  );
}

function ColumnsTable({ columns }) {
  return (
    <div style={{ overflow: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
        <thead>
          <tr style={{ background: "var(--bg-hover)", color: "var(--text-secondary)" }}>
            <th style={th()}>컬럼</th>
            <th style={th()}>의미</th>
            <th style={th()}>예시</th>
            <th style={th()}>Wiki</th>
          </tr>
        </thead>
        <tbody>
          {columns.map((c) => (
            <tr key={c.name} style={{ borderTop: "1px solid var(--border)" }}>
              <td style={td("160px", true)}><code>{c.name}</code></td>
              <td style={td()}>{c.meaning || <span style={{ color: "var(--text-secondary)" }}>(미작성)</span>}</td>
              <td style={td("140px")}>{(c.sample_values || []).join(", ") || "—"}</td>
              <td style={td("160px")}>{c.wiki_doc_id ? <code style={{ fontSize: 11 }}>{c.wiki_doc_id}</code> : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const th = () => ({ textAlign: "left", padding: "6px 10px", fontWeight: 700, fontSize: 11 });
const td = (width = "auto", mono = false) => ({ padding: "6px 10px", verticalAlign: "top", width, fontFamily: mono ? "monospace" : "inherit" });

// ── Semantic bindings ──────────────────────────────────
function SemanticSection({ bindings }) {
  const items = [
    { k: "relation_ids", l: "Relation IDs (schema_relations.json)" },
    { k: "column_catalog_keys", l: "Column catalog keys" },
    { k: "graph_node_ids", l: "Knowledge graph nodes" },
    { k: "wiki_doc_ids", l: "Wiki docs (schema_doc kind)" },
  ];
  return (
    <Section title="시멘틱 바인딩" hint="용어 해석에 쓰는 메타 (편집은 M4)">
      {items.map(({ k, l }) => {
        const arr = bindings[k] || [];
        return (
          <div key={k} style={{ display: "flex", gap: 8, padding: "4px 0", fontSize: 12 }}>
            <span style={{ width: 230, color: "var(--text-secondary)" }}>{l}</span>
            <span style={{ flex: 1 }}>
              {arr.length === 0 ? <span style={{ color: "var(--text-secondary)" }}>—</span> :
                arr.map((v, i) => <code key={i} style={{ marginRight: 6, fontSize: 11, padding: "1px 5px", background: "var(--bg-secondary)", borderRadius: 3 }}>{v}</code>)}
            </span>
          </div>
        );
      })}
    </Section>
  );
}

// ── Prompt template ────────────────────────────────────
function PromptTemplateSection({ tpl }) {
  if (!tpl || !tpl.path) {
    return (
      <Section title="Prompt template" hint="이 AI 전용 prompt 파일 (없으면 handler 내부 string 사용)">
        <Banner tone="info">전용 prompt template 파일이 등록돼 있지 않습니다. handler 내부 prompt string 사용.</Banner>
      </Section>
    );
  }
  const parsed = tpl.parsed;
  return (
    <Section title="Prompt template" hint={tpl.path} right={!tpl.exists && <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>(파일 없음)</span>}>
      {tpl.error && <Banner tone="warn">{tpl.error}</Banner>}
      {parsed && typeof parsed === "object" && !Array.isArray(parsed) ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {Object.keys(parsed).map((k) => (
            <details key={k} style={{ border: "1px solid var(--border)", borderRadius: 4, background: "var(--bg-secondary)" }}>
              <summary style={{ padding: "6px 10px", cursor: "pointer", fontWeight: 600, fontSize: 13 }}>{k}</summary>
              <pre style={preStyle}>{typeof parsed[k] === "string" ? parsed[k] : JSON.stringify(parsed[k], null, 2)}</pre>
            </details>
          ))}
        </div>
      ) : (
        <pre style={preStyle}>{tpl.text || "(empty)"}</pre>
      )}
    </Section>
  );
}

const preStyle = {
  margin: 0, padding: "8px 12px", whiteSpace: "pre-wrap", wordBreak: "break-word",
  fontSize: 12, fontFamily: "monospace", background: "var(--bg-primary)",
  borderTop: "1px solid var(--border)", maxHeight: 320, overflow: "auto",
};

// ── LLM profile ────────────────────────────────────────
function LlmProfileSection({ profile }) {
  return (
    <Section title="LLM profile" hint="data/flow-data/admin_settings.json의 llm_profiles 키">
      <code style={{ fontSize: 13, padding: "4px 8px", background: "var(--bg-secondary)", borderRadius: 4 }}>{profile || "—"}</code>
      <span style={{ marginLeft: 10, fontSize: 11, color: "var(--text-secondary)" }}>(profile 전환 UI는 M4)</span>
    </Section>
  );
}

// ── Feature md ─────────────────────────────────────────
function FeatureMdSection({ md }) {
  if (!md || !md.path) return null;
  return (
    <Section title="Feature 규칙 md" hint={md.path} right={!md.exists && <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>(파일 없음)</span>}>
      {md.error && <Banner tone="warn">{md.error}</Banner>}
      <pre style={{ ...preStyle, maxHeight: 360 }}>{md.text || "(empty)"}</pre>
    </Section>
  );
}

// ── Handler entry ──────────────────────────────────────
function HandlerEntrySection({ entry }) {
  if (!entry) return null;
  const has = entry.module || entry.function;
  return (
    <Section title="Handler entry" hint="이 AI의 실제 처리 함수">
      {!has && <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>(미연결 — M2 다음 PR에서 위임)</span>}
      {has && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12 }}>
          <div><span style={{ color: "var(--text-secondary)" }}>file: </span><code>{entry.file_path || entry.module}</code>{entry.lineno > 0 && <span style={{ color: "var(--text-secondary)" }}>:{entry.lineno}</span>}</div>
          <div><span style={{ color: "var(--text-secondary)" }}>function: </span><code>{entry.function}</code></div>
          {entry.description && <div style={{ color: "var(--text-secondary)" }}>{entry.description}</div>}
        </div>
      )}
    </Section>
  );
}
