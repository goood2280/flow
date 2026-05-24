import { useState } from "react";
import AgentV2 from "./AgentV2";
import KnowledgeOverviewTab from "./KnowledgeOverviewTab";
import QuestionDesignTab from "./QuestionDesignTab";
import WikiTab from "./WikiTab";

const SECTIONS = [
  { key: "question", label: "질문 설계", hint: "prompt를 workflow 초안으로 정리" },
  { key: "unit", label: "기능 AI / 시멘틱", hint: "Unit AI, alias, workflow template" },
  { key: "wiki", label: "Wiki 그래프", hint: "Obsidian식 graph와 page/source" },
  { key: "overview", label: "누적 지식", hint: "proposal, trace, event overview" },
];

export default function AgentManageTab({ user, canManageWiki }) {
  const [section, setSection] = useState("question");
  const active = SECTIONS.find((item) => item.key === section) || SECTIONS[0];

  return (
    <div style={layoutStyle}>
      <aside style={navStyle}>
        <div style={{ padding: "12px 12px 8px", borderBottom: "1px solid var(--border)" }}>
          <div style={{ fontSize: 13, fontWeight: 900, color: "var(--text-primary)" }}>설계·지식 관리</div>
          <div style={{ marginTop: 4, fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.4 }}>
            겹치는 관리 화면을 한 흐름으로 묶었습니다.
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
        <div style={bodyStyle}>
          {section === "question" && <QuestionDesignTab user={user} canShare={canManageWiki} />}
          {section === "unit" && <AgentV2 user={user} canManageWiki={canManageWiki} />}
          {section === "wiki" && <WikiTab user={user} canManage={canManageWiki} />}
          {section === "overview" && <KnowledgeOverviewTab user={user} />}
        </div>
      </main>
    </div>
  );
}

const layoutStyle = {
  height: "100%",
  minHeight: 0,
  display: "grid",
  gridTemplateColumns: "220px minmax(0, 1fr)",
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
