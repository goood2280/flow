import { useState } from "react";
import AgentGuideTab from "./AgentGuideTab";
import AgentV2 from "./AgentV2";
import KnowledgeOverviewTab from "./KnowledgeOverviewTab";
import KnowledgeReviewQueueTab from "./KnowledgeReviewQueueTab";
import QuestionDesignTab from "./QuestionDesignTab";
import WikiTab from "./WikiTab";

const SECTIONS = [
  { key: "guide", label: "운영 가이드", hint: "질문 -> 지도 -> 근거 -> 검증 운영 루프" },
  { key: "question", label: "질문/워크플로우", hint: "질문을 workflow 초안과 dry-run으로 정리" },
  { key: "unit", label: "용어/기능 AI", hint: "Unit AI, alias, intent, workflow template" },
  { key: "wiki", label: "Wiki 근거", hint: "graph, page, source, lint 상태" },
  { key: "review", label: "검토 큐", hint: "회의/이슈/lot 자동 draft 검토 -> publish" },
  { key: "overview", label: "변경 이력", hint: "proposal, prompt trace, knowledge event" },
];

export default function AgentManageTab({ user, canManageWiki }) {
  const [section, setSection] = useState("guide");
  const active = SECTIONS.find((item) => item.key === section) || SECTIONS[0];
  const readOnly = !canManageWiki;

  return (
    <div className="agent-manage-shell" style={layoutStyle}>
      <aside style={navStyle}>
        <div style={{ padding: "12px 12px 8px", borderBottom: "1px solid var(--border)" }}>
          <div style={{ fontSize: 13, fontWeight: 900, color: "var(--text-primary)" }}>Agent 관리</div>
          <div style={{ marginTop: 4, fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.4 }}>
            운영 가이드에서 루프를 확인한 뒤 질문, 용어, Wiki 근거, 검토 큐를 관리합니다.
          </div>
        </div>
        <div style={sectionListStyle}>
          {SECTIONS.map((item) => (
            <button
              key={item.key}
              type="button"
              onClick={() => setSection(item.key)}
              data-active={section === item.key ? "true" : undefined}
              style={{
                width: "100%",
                display: "block",
                textAlign: "left",
                border: "0",
                borderLeft: section === item.key ? "3px solid var(--accent)" : "3px solid transparent",
                borderBottom: "1px solid var(--border)",
                borderRadius: 0,
                background: section === item.key ? "var(--bg-primary)" : "transparent",
                color: section === item.key ? "var(--accent)" : "var(--text-primary)",
                padding: "10px 12px",
                cursor: "pointer",
                font: "inherit",
                appearance: "none",
              }}
            >
              <div style={{ fontSize: 13, fontWeight: 850 }}>{item.label}</div>
              <div style={{ marginTop: 2, fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.35 }}>{item.hint}</div>
            </button>
          ))}
        </div>
      </aside>
      <main style={contentStyle}>
        <div style={headerStyle}>
          <span style={{ fontSize: 14, fontWeight: 900, color: "var(--text-primary)" }}>{active.label}</span>
          <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>{active.hint}</span>
        </div>
        {readOnly && (
          <div style={readOnlyStyle}>
            읽기 전용입니다. 공유 workflow, semantic alias, 유지 Wiki 반영은 admin 또는 diagnosis/agent/knowledge 페이지 관리자만 승인할 수 있습니다.
          </div>
        )}
        <div style={bodyStyle}>
          {section === "guide" && <AgentGuideTab />}
          {section === "question" && <QuestionDesignTab user={user} canShare={canManageWiki} />}
          {section === "unit" && <AgentV2 user={user} canManageWiki={canManageWiki} />}
          {section === "wiki" && <WikiTab user={user} canManage={canManageWiki} />}
          {section === "review" && <KnowledgeReviewQueueTab user={user} canManage={canManageWiki} />}
          {section === "overview" && <KnowledgeOverviewTab user={user} />}
        </div>
      </main>
    </div>
  );
}

const layoutStyle = {
  minHeight: "100%",
  display: "grid",
  border: "1px solid var(--border)",
  background: "var(--bg-primary)",
};

const navStyle = {
  minHeight: 0,
  borderRight: "1px solid var(--border)",
  background: "var(--bg-secondary)",
  overflow: "auto",
};

const sectionListStyle = {
  display: "flex",
  flexDirection: "column",
  alignItems: "stretch",
};

const contentStyle = {
  minWidth: 0,
  minHeight: 0,
  display: "flex",
  flexDirection: "column",
  overflow: "hidden",
};

const headerStyle = {
  minHeight: 42,
  display: "flex",
  gap: 10,
  alignItems: "baseline",
  padding: "10px 14px",
  borderBottom: "1px solid var(--border)",
  background: "var(--bg-secondary)",
};

const bodyStyle = {
  flex: 1,
  minHeight: 0,
  overflow: "auto",
};

const readOnlyStyle = {
  padding: "8px 14px",
  borderBottom: "1px solid var(--border)",
  background: "var(--warn-glow, var(--bg-secondary))",
  color: "var(--text-secondary)",
  fontSize: 12,
  lineHeight: 1.45,
};
