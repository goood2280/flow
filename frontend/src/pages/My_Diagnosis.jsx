import { useState } from "react";
import { Banner, PageHeader, PageShell, TabStrip } from "../components/UXKit";
import AgentManageTab from "../components/agent/AgentManageTab";
import AgentStudioTab from "../components/agent/AgentStudioTab";
import LlmTab from "../components/agent/LlmTab";
import { canManagePage } from "../lib/permissions";

const AGENT_TABS = [
  { k: "board", l: "운영 보드" },
  { k: "manage", l: "설계·지식" },
  { k: "settings", l: "설정" },
];

const AGENT_TAB_HINT = {
  board: "질문 선택/입력 -> 처리 흐름 확인 -> 개선할 지식/워크플로우 제안 흐름으로 운영합니다.",
  manage: "질문 설계, 용어/기능 AI, Wiki, 변경 이력을 승인 흐름과 함께 관리합니다.",
  settings: "Flow-i가 호출하는 사내 LLM endpoint의 상태와 설정을 확인합니다. admin은 endpoint/모델/timeout을 바로 수정할 수 있습니다.",
};

const AGENT_TRACE_CONTRACT_MARKERS = [
  "단위 에이전트 오케스트레이션",
  "시멘틱 레이어",
  "실시간 상태 업데이트",
  "최종 결론",
  "FastAPI SSE",
  "LangGraph astream",
  "LangSmith tracing",
  "/api/agent/runtime/stream",
  "/api/agent/runtime/semantic/resolve",
  "/api/agent/runtime/run",
  "/api/agent/knowledge/overview",
  "/api/agent/workflows/test",
  "/api/agent/workflows/execute",
];

export default function My_Diagnosis({ user }) {
  const [tab, setTab] = useState("board");
  const isAdminUser = user?.role === "admin";
  const canManageWiki = canManagePage(user, "diagnosis") || canManagePage(user, "agent") || canManagePage(user, "knowledge");
  const tabHint = AGENT_TAB_HINT[tab];
  const activeTab = (
    <>
      {tab === "board" && <AgentStudioTab user={user} />}
      {tab === "manage" && <AgentManageTab user={user} canManageWiki={canManageWiki} />}
      {tab === "settings" && <LlmTab isAdmin={isAdminUser} />}
    </>
  );

  return (
    <div className="flow-connected-page flow-agent-page" style={{ minHeight: "calc(100vh - 52px)", background: "var(--bg-primary)", color: "var(--text-primary)" }}>
      <PageShell style={{ height: "calc(100vh - 52px)", minHeight: 0, overflow: "hidden", display: "flex", flexDirection: "column" }}>
        <PageHeader title="에이전트" subtitle="운영 보드에서 질문 처리 품질을 보고, 설계·지식과 설정으로 개선 경로를 좁힙니다." />
        <div className="flow-agent-shell">
          <div className="flow-agent-tabs">
            <TabStrip items={AGENT_TABS} active={tab} onChange={setTab} />
          </div>
          {tabHint && <Banner tone="info" style={{ borderRadius: 0 }}>{tabHint}</Banner>}
          <div className="flow-agent-surface">
            {activeTab}
          </div>
        </div>
      </PageShell>
    </div>
  );
}
