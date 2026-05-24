// AgentV2 — FileBrowser/SplitTable-style 단순 UI for the Agent tab.
// 좌측 리스트(11개 unit AI + 공통 자원 3항목) + 우측 디테일 단일 페이지.
// 다중 탭 없이 한 화면에서 모든 unit AI의 자원을 직관적으로 확인.
import { useEffect, useState } from "react";
import { sf } from "../../lib/api";
import { Banner, EmptyState } from "../UXKit";
import ExecutionFlowTab from "./ExecutionFlowTab";
import SemanticLayerTab from "./SemanticLayerTab";
import UnitAIDetail from "./UnitAIDetail";
import WorkflowsTab from "./WorkflowsTab";

const COMMON_ITEMS = [
  { key: "__semantic", label: "시멘틱 레이어", icon: "🧠", hint: "schema_relations · wiki · 컬럼 의미" },
  { key: "__workflows", label: "워크플로우 템플릿", icon: "📐", hint: "반복 prompt를 규격화 (M5)" },
  { key: "__execution", label: "최근 실행 흐름", icon: "🔎", hint: "Thought flow와 인라인 교정 (M4)" },
];

export default function AgentV2({ user, canManageWiki }) {
  const [catalog, setCatalog] = useState([]);
  const [loadErr, setLoadErr] = useState("");
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    sf("/api/agent/unit-ai/catalog")
      .then((d) => {
        if (cancelled) return;
        const items = (d && d.items) || [];
        setCatalog(items);
        if (!selected && items.length > 0) setSelected(items[0].key);
      })
      .catch((e) => !cancelled && setLoadErr(e?.message || "unit AI catalog 로딩 실패"))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
    // selected intentionally excluded — only set on first load.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const isUnitAI = selected && !selected.startsWith("__");
  const commonItem = !isUnitAI && COMMON_ITEMS.find((it) => it.key === selected);

  return (
    <div className="flow-connected-page flow-agent-v2" style={{
      display: "flex", height: "100%", minHeight: 0,
      background: "var(--bg-primary)", color: "var(--text-primary)",
      border: "0", borderRadius: 0, overflow: "hidden",
    }}>
      <Sidebar
        catalog={catalog}
        loading={loading}
        selected={selected}
        onSelect={setSelected}
      />
      <main style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        {loadErr && <Banner tone="warn" style={{ borderRadius: 0 }}>unit AI catalog: {loadErr}</Banner>}
        <div style={{ flex: 1, overflow: "auto" }}>
          {isUnitAI && <UnitAIDetail unitKey={selected} user={user} canManageWiki={canManageWiki} />}
          {commonItem && commonItem.key === "__semantic" && <SemanticLayerTab user={user} canManageWiki={canManageWiki} />}
          {commonItem && commonItem.key === "__execution" && <ExecutionFlowTab user={user} />}
          {commonItem && commonItem.key === "__workflows" && <WorkflowsTab user={user} />}
          {commonItem && !["__semantic", "__execution", "__workflows"].includes(commonItem.key) && <CommonPlaceholder item={commonItem} />}
          {!isUnitAI && !commonItem && (
            <div style={{ padding: 24 }}>
              <EmptyState title="좌측에서 항목을 선택하세요" hint="11개 단위 기능 AI 또는 공통 자원 3가지" />
            </div>
          )}
        </div>
      </main>
    </div>
  );
}

function Sidebar({ catalog, loading, selected, onSelect }) {
  return (
    <aside style={{
      width: 260, minWidth: 260, borderRight: "1px solid var(--border)",
      background: "var(--bg-secondary)", display: "flex", flexDirection: "column", overflow: "hidden",
    }}>
      <SidebarHeader title="단위 기능 AI" meta={catalog.length ? `${catalog.length} units` : ""} />
      <div style={{ flex: 1, overflow: "auto" }}>
        <SidebarSection label={loading ? "로딩 중..." : `기능 AI (${catalog.length})`}>
          {catalog.map((item) => (
            <SidebarItem
              key={item.key}
              active={selected === item.key}
              icon="🤖"
              label={item.title || item.key}
              hint={`데이터 ${item.data_source_count}개 · 컬럼 ${item.column_doc_count}개`}
              onClick={() => onSelect(item.key)}
            />
          ))}
          {!loading && catalog.length === 0 && (
            <div style={{ padding: "10px 12px", fontSize: 13, color: "var(--text-secondary)" }}>
              unit AI 카탈로그가 비어있습니다.
            </div>
          )}
        </SidebarSection>
        <SidebarSection label="공통 자원">
          {COMMON_ITEMS.map((it) => (
            <SidebarItem
              key={it.key}
              active={selected === it.key}
              icon={it.icon}
              label={it.label}
              hint={it.hint}
              onClick={() => onSelect(it.key)}
            />
          ))}
        </SidebarSection>
      </div>
    </aside>
  );
}

function SidebarHeader({ title, meta }) {
  return (
    <div className="flow-sidebar-header" style={{
      padding: "12px 16px", borderBottom: "1px solid var(--border)",
      fontSize: 14, fontWeight: 700, color: "var(--text-secondary)",
      display: "flex", flexDirection: "column", justifyContent: "center", alignItems: "flex-start", gap: 2,
      textAlign: "left",
    }}>
      <span className="flow-sidebar-header-title">{title}</span>
      {meta && <span className="flow-sidebar-header-meta" style={{ fontSize: 12, fontWeight: 500 }}>{meta}</span>}
    </div>
  );
}

function SidebarSection({ label, children }) {
  return (
    <div>
      <div style={{
        fontSize: 12, fontWeight: 700, color: "var(--text-secondary)",
        padding: "10px 12px 6px", textTransform: "uppercase", letterSpacing: 0.4,
        textAlign: "left",
      }}>{label}</div>
      <div style={{ paddingBottom: 6 }}>{children}</div>
    </div>
  );
}

function SidebarItem({ active, icon, label, meta, hint, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        width: "100%", textAlign: "left", padding: "8px 12px", display: "flex", flexDirection: "column", gap: 2,
        alignItems: "stretch", justifyContent: "flex-start",
        background: active ? "var(--accent-glow)" : "transparent",
        color: active ? "var(--accent)" : "var(--text-primary)",
        borderLeft: active ? "3px solid var(--accent)" : "3px solid transparent",
        border: "none", borderBottom: "1px solid var(--border-soft, transparent)",
        cursor: "pointer", fontSize: 13,
      }}
    >
      <span style={{ width: "100%", display: "flex", alignItems: "center", gap: 6, fontWeight: active ? 700 : 500 }}>
        <span aria-hidden>{icon}</span>
        <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label}</span>
        {meta && <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>{meta}</span>}
      </span>
      {hint && <span style={{ width: "100%", fontSize: 11, color: "var(--text-secondary)", paddingLeft: 22, textAlign: "left" }}>{hint}</span>}
    </button>
  );
}

function CommonPlaceholder({ item }) {
  return (
    <div style={{ padding: 24 }}>
      <h2 style={{ fontSize: 18, margin: "0 0 6px", display: "flex", alignItems: "center", gap: 8 }}>
        <span>{item.icon}</span>{item.label}
      </h2>
      <p style={{ fontSize: 13, color: "var(--text-secondary)", margin: "0 0 16px" }}>{item.hint}</p>
      <Banner tone="info">
        {item.key === "__semantic" && "M3.4에서 기존 SchemaRelationsPanel + AgentWikiPanel을 여기로 이관합니다."}
        {item.key === "__workflows" && "M5에서 워크플로우 템플릿 등록/실행 UI를 추가합니다."}
        {item.key === "__execution" && "M4에서 token 해석/activation 5단계/call graph 가시화와 인라인 교정 UI를 추가합니다."}
      </Banner>
    </div>
  );
}
