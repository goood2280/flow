import { useState } from "react";
import { Banner, PageHeader, PageShell, TabStrip } from "../components/UXKit";
import AgentRuntime from "../components/agent/AgentRuntime";
import LlmTab from "../components/agent/LlmTab";

const AGENT_TABS = [
  { k: "runtime", l: "런타임 설계" },
  { k: "ai", l: "LLM 연결" },
];

const AGENT_TAB_HINT = {
  runtime: "FastAPI SSE, LangGraph astream, LangSmith tracing을 기준으로 추상 목표를 시멘틱 레이어와 단위 에이전트 실행 흐름으로 분해합니다.",
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
];

export default function My_Diagnosis({ user }) {
  const [tab, setTab] = useState("runtime");
  const isAdminUser = user?.role === "admin";
  const tabHint = AGENT_TAB_HINT[tab];
  const activeTab = (
    <>
      {tab === "runtime" && <AgentRuntime user={user} />}
      {tab === "ai" && <LlmTab isAdmin={isAdminUser} />}
    </>
  );

  return (
    <div className="flow-connected-page flow-agent-page" style={{ minHeight: "calc(100vh - 52px)", background: "var(--bg-primary)", color: "var(--text-primary)" }}>
      <PageShell style={{ minHeight: "calc(100vh - 52px)" }}>
        <PageHeader title="에이전트" subtitle="추상 목표를 시멘틱 레이어와 단위 에이전트 graph로 분해하고 실행 상태를 스트리밍합니다." />
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
