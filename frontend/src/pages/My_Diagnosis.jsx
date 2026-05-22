import { useState } from "react";
import { Banner, PageHeader, PageShell, TabStrip } from "../components/UXKit";
import AgentV2 from "../components/agent/AgentV2";
import AgentRuntime from "../components/agent/AgentRuntime";
import KnowledgeOverviewTab from "../components/agent/KnowledgeOverviewTab";
import LlmTab from "../components/agent/LlmTab";
import QuestionDesignTab from "../components/agent/QuestionDesignTab";
import { canManagePage } from "../lib/permissions";

const AGENT_TABS = [
  { k: "question", l: "질문 설계" },
  { k: "semantic", l: "기능 AI/시멘틱" },
  { k: "knowledge", l: "누적 지식" },
  { k: "runtime", l: "런타임 추적" },
  { k: "ai", l: "LLM 연결" },
];

const AGENT_TAB_HINT = {
  question: "질문 하나를 semantic resolve, workflow match, dry-run step으로 분해하고 개인 workflow 초안으로 저장합니다.",
  semantic: "단위 기능 AI, 시멘틱 alias/intent, workflow template, Agent Wiki 입력과 제안 큐를 운영합니다.",
  knowledge: "회의/인폼/이슈추적/SplitTable comment, Wiki source/page, semantic proposal, prompt trace를 한 화면에서 조회합니다.",
  runtime: "FastAPI SSE, LangGraph astream, LangSmith tracing을 기준으로 실행 상태 스트림과 최종 결론을 추적합니다.",
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
  const [tab, setTab] = useState("question");
  const isAdminUser = user?.role === "admin";
  const canManageWiki = canManagePage(user, "diagnosis") || canManagePage(user, "agent") || canManagePage(user, "knowledge");
  const tabHint = AGENT_TAB_HINT[tab];
  const activeTab = (
    <>
      {tab === "question" && <QuestionDesignTab user={user} canShare={canManageWiki} />}
      {tab === "semantic" && <AgentV2 user={user} canManageWiki={canManageWiki} />}
      {tab === "knowledge" && <KnowledgeOverviewTab user={user} />}
      {tab === "runtime" && <AgentRuntime user={user} />}
      {tab === "ai" && <LlmTab isAdmin={isAdminUser} />}
    </>
  );

  return (
    <div className="flow-connected-page flow-agent-page" style={{ minHeight: "calc(100vh - 52px)", background: "var(--bg-primary)", color: "var(--text-primary)" }}>
      <PageShell style={{ minHeight: "calc(100vh - 52px)" }}>
        <PageHeader title="에이전트" subtitle="질문 처리 설계, 시멘틱 운영, 누적 지식, 런타임 추적, LLM 연결을 한 화면에서 관리합니다." />
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
