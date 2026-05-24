import { useState } from "react";
import { Banner, PageHeader, PageShell, TabStrip } from "../components/UXKit";
import AgentGuideTab from "../components/agent/AgentGuideTab";
import AgentManageTab from "../components/agent/AgentManageTab";
import AgentStudioTab from "../components/agent/AgentStudioTab";
import AgentRuntime from "../components/agent/AgentRuntime";
import LlmTab from "../components/agent/LlmTab";
import { canManagePage } from "../lib/permissions";

const AGENT_TABS = [
  { k: "studio", l: "스튜디오" },
  { k: "manage", l: "설계·지식" },
  { k: "runtime", l: "실행 추적" },
  { k: "guide", l: "운영 가이드" },
  { k: "ai", l: "LLM 연결" },
];

const AGENT_TAB_HINT = {
  studio: "Dify/n8n처럼 질문 큐, 워크플로우 캔버스, Wiki 근거, 개선 루프를 한 화면에서 봅니다.",
  manage: "질문 설계, 기능 AI/시멘틱, Wiki 그래프, 누적 지식을 한 관리 화면으로 묶었습니다.",
  runtime: "FastAPI SSE, LangGraph astream, LangSmith tracing을 기준으로 실행 상태 스트림과 최종 결론을 추적합니다.",
  guide: "질문 이력에서 개선 대상을 고르고 Wiki/workflow를 보강한 뒤 Runbook/deep-eval로 검증하는 운영 기준입니다.",
  ai: "Flow-i가 호출하는 사내 LLM endpoint의 상태와 설정을 확인합니다. admin은 endpoint/모델/timeout을 바로 수정할 수 있습니다.",
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
  const [tab, setTab] = useState("studio");
  const isAdminUser = user?.role === "admin";
  const canManageWiki = canManagePage(user, "diagnosis") || canManagePage(user, "agent") || canManagePage(user, "knowledge");
  const tabHint = AGENT_TAB_HINT[tab];
  const activeTab = (
    <>
      {tab === "studio" && <AgentStudioTab user={user} />}
      {tab === "manage" && <AgentManageTab user={user} canManageWiki={canManageWiki} />}
      {tab === "runtime" && <AgentRuntime user={user} />}
      {tab === "guide" && <AgentGuideTab />}
      {tab === "ai" && <LlmTab isAdmin={isAdminUser} />}
    </>
  );

  return (
    <div className="flow-connected-page flow-agent-page" style={{ minHeight: "calc(100vh - 52px)", background: "var(--bg-primary)", color: "var(--text-primary)" }}>
      <PageShell style={{ height: "calc(100vh - 52px)", minHeight: 0, overflow: "hidden", display: "flex", flexDirection: "column" }}>
        <PageHeader title="에이전트" subtitle="질문 큐, 워크플로우 캔버스, Wiki 근거, 설계·지식 관리, 개선 가이드를 한 화면 안에서 관리합니다." />
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
