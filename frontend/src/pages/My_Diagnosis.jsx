import { useState } from "react";
import { canManagePage } from "../lib/permissions";
import { Banner, PageHeader, PageShell, TabStrip } from "../components/UXKit";
import LoopTab from "../components/agent/LoopTab";
import WikiTab from "../components/agent/WikiTab";
import SchemaTab from "../components/agent/SchemaTab";
import LlmTab from "../components/agent/LlmTab";

const AGENT_TABS = [
  { k: "loop", l: "실행 흐름" },
  { k: "wiki", l: "지식 위키" },
  { k: "schema", l: "스키마 관계" },
  { k: "ai", l: "LLM 연결" },
];

const AGENT_TAB_HINT = {
  loop: "프롬프트를 보내면 오케스트레이터 라우팅과 단위기능 호출이 한 화면에서 보입니다. 이전에 실행한 프롬프트는 아래 ‘프롬프트 기록’ 카드에서 다시 확인하거나 재실행할 수 있습니다.",
  wiki: "Agent가 참고하는 raw source, maintained wiki page, ingest 로그, lint 결과를 한 곳에서 운영합니다.",
  schema: "DB product와 단일파일 사이의 join key 후보를 preview로 보고, admin이 확인 저장한 relation만 운영 graph로 남깁니다.",
  ai: "Flow-i가 호출하는 사내 LLM endpoint의 상태와 설정을 확인합니다. admin은 endpoint/모델/timeout을 바로 수정할 수 있습니다.",
};

const AGENT_TRACE_CONTRACT_MARKERS = [
  "Flow-i 실행 흐름",
  "프롬프트별 오케스트레이터 활성화",
  "/api/llm/flowi/orchestrator/preview",
  "Activation Map (5단계)",
  "에이전트가 받은 prompt",
  "활성화된 기능",
  "호출 결과",
  "예시 prompt",
  "API 호출 그래프",
  "FastAPI / handler 호출",
  "call_graph",
  "/api/llm/flowi/agent/chat",
  "feature_subagent",
];

export default function My_Diagnosis({ user }) {
  const [tab, setTab] = useState("loop");
  const isAdminUser = user?.role === "admin";
  const canManageWiki = canManagePage(user, "diagnosis") || canManagePage(user, "agent") || canManagePage(user, "knowledge");
  const tabHint = AGENT_TAB_HINT[tab];
  const activeTab = (
    <>
      {tab === "loop" && <LoopTab user={user} />}
      {tab === "wiki" && <WikiTab user={user} canManage={canManageWiki} />}
      {tab === "schema" && <SchemaTab canManage={canManageWiki} />}
      {tab === "ai" && <LlmTab isAdmin={isAdminUser} />}
    </>
  );

  return (
    <div className="flow-connected-page flow-agent-page" style={{ minHeight: "calc(100vh - 52px)", background: "var(--bg-primary)", color: "var(--text-primary)" }}>
      <PageShell style={{ minHeight: "calc(100vh - 52px)" }}>
        <PageHeader title="에이전트" subtitle="Flow-i orchestrator가 프롬프트를 기능별 단위기능으로 라우팅하고 실행한 흐름을 확인합니다." />
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
