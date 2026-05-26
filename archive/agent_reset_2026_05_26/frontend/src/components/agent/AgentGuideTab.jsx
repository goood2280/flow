import { Pill } from "../UXKit";

const GUIDE_ROWS = [
  {
    stage: "1",
    title: "질문 수집",
    owner: "user/admin",
    body: "Agent Studio의 질문 큐에서 반복 질문, blocked, missing slot, 느린 action을 먼저 고릅니다.",
    evidence: "prompt-history, actor, action, status, answer excerpt",
  },
  {
    stage: "2",
    title: "흐름 확인",
    owner: "admin/manager",
    body: "워크플로우 캔버스에서 prompt가 어떤 policy, tool, Wiki/schema 근거로 흘렀는지 확인합니다.",
    evidence: "workflow-map nodes, edges, warnings",
  },
  {
    stage: "3",
    title: "지식 보강",
    owner: "manager",
    body: "근거가 비어 있거나 용어가 흔들리는 질문은 Wiki source/page, schema relation, semantic alias 후보로 보강합니다.",
    evidence: "wiki-health, Obsidian graph, semantic proposals",
  },
  {
    stage: "4",
    title: "검증",
    owner: "admin",
    body: "Runbook dry-run, workflow warning, deep-eval 결과를 보고 변경이 실제 질문 처리 품질을 개선했는지 확인합니다.",
    evidence: "workflow-runbook, deep-eval, timeline",
  },
  {
    stage: "5",
    title: "운영 반영",
    owner: "admin",
    body: "반복되는 좋은 처리 경로는 shared workflow template이나 유지 Wiki로 승격하고, 변경 로그를 남깁니다.",
    evidence: "shared workflow, maintained Wiki, operation timeline",
  },
];

const ROLE_ROWS = [
  { role: "일반 사용자", can: "질문 실행, 개인 workflow 초안 저장, 본인 질문 이력 확인", guard: "공유 workflow/Wiki 직접 반영은 하지 않음" },
  { role: "페이지 관리자", can: "Wiki source/page 승인, semantic alias 검토, 질문 개선 후보 정리", guard: "원본 DB/file 직접 수정 금지" },
  { role: "Admin", can: "도구 활성화, starter workflow 생성, deep-eval 재생성, 운영 export", guard: "LLM이 권한/저장을 단독 결정하지 않음" },
];

export default function AgentGuideTab() {
  return (
    <div style={{ padding: 20, display: "flex", flexDirection: "column", gap: 14 }}>
      <section style={heroStyle}>
        <div>
          <div style={{ fontSize: 18, fontWeight: 900, color: "var(--text-primary)" }}>Agent 운영/개선 가이드</div>
          <div style={{ marginTop: 5, fontSize: 13, color: "var(--text-secondary)", lineHeight: 1.55 }}>
            질문 이력에서 개선 대상을 고르고, n8n식 workflow 지도와 Obsidian식 Wiki 근거를 따라 보강한 뒤, Runbook과 deep-eval로 검증하는 운영 루프입니다.
          </div>
        </div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", justifyContent: "flex-end" }}>
          <Pill tone="accent">Agent Studio</Pill>
          <Pill tone="info">Workflow Map</Pill>
          <Pill tone="ok">Wiki Evidence</Pill>
          <Pill tone="warn">Deep Eval</Pill>
        </div>
      </section>

      <section style={sectionStyle}>
        <SectionHeader title="개선 루프" meta="Dify/n8n처럼 한 화면에서 추적" />
        <div style={{ display: "grid", gridTemplateColumns: "repeat(5, minmax(150px, 1fr))", gap: 8, overflowX: "auto" }}>
          {GUIDE_ROWS.map((row) => (
            <div key={row.stage} style={stepStyle}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 6, alignItems: "center", marginBottom: 8 }}>
                <span style={stepNoStyle}>{row.stage}</span>
                <Pill tone="neutral">{row.owner}</Pill>
              </div>
              <div style={{ fontSize: 13, fontWeight: 900, color: "var(--text-primary)", marginBottom: 6 }}>{row.title}</div>
              <div style={{ fontSize: 12, color: "var(--text-primary)", lineHeight: 1.5, minHeight: 72 }}>{row.body}</div>
              <div style={{ marginTop: 8, fontSize: 10, color: "var(--text-secondary)", lineHeight: 1.4 }}>{row.evidence}</div>
            </div>
          ))}
        </div>
      </section>

      <section style={sectionStyle}>
        <SectionHeader title="권한 경계" meta="초안 + 승인 방식" />
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(180px, 1fr))", gap: 8 }}>
          {ROLE_ROWS.map((row) => (
            <div key={row.role} style={roleStyle}>
              <div style={{ fontSize: 13, fontWeight: 900, color: "var(--text-primary)", marginBottom: 7 }}>{row.role}</div>
              <InfoLine label="가능" value={row.can} />
              <InfoLine label="가드" value={row.guard} />
            </div>
          ))}
        </div>
      </section>

      <section style={sectionStyle}>
        <SectionHeader title="운영 판단 기준" meta="작업 후 남기는 증거" />
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(160px, 1fr))", gap: 8 }}>
          <Check title="질문 처리" body="질문별 actor, action, status, missing slot, answer excerpt가 남아야 합니다." />
          <Check title="워크플로우" body="Prompt -> Policy -> Tool -> Wiki/Schema -> Improve 경로가 지도에서 끊기지 않아야 합니다." />
          <Check title="Wiki" body="새 지식은 source와 page가 연결되고 lint/graph warning이 줄어야 합니다." />
          <Check title="검증" body="dry-run 또는 deep-eval 결과가 변경 전보다 나빠지지 않아야 합니다." />
        </div>
      </section>
    </div>
  );
}

function SectionHeader({ title, meta }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
      <div style={{ fontSize: 14, fontWeight: 900, color: "var(--text-primary)" }}>{title}</div>
      {meta && <Pill tone="neutral">{meta}</Pill>}
    </div>
  );
}

function InfoLine({ label, value }) {
  return (
    <div style={{ marginTop: 7 }}>
      <div style={{ fontSize: 10, fontWeight: 850, color: "var(--text-secondary)", marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: 12, color: "var(--text-primary)", lineHeight: 1.45 }}>{value}</div>
    </div>
  );
}

function Check({ title, body }) {
  return (
    <div style={roleStyle}>
      <div style={{ fontSize: 13, fontWeight: 900, color: "var(--text-primary)", marginBottom: 6 }}>{title}</div>
      <div style={{ fontSize: 12, color: "var(--text-primary)", lineHeight: 1.5 }}>{body}</div>
    </div>
  );
}

const heroStyle = {
  border: "0",
  borderBottom: "1px solid var(--border)",
  background: "var(--bg-secondary)",
  padding: 14,
  display: "grid",
  gridTemplateColumns: "minmax(0, 1fr) auto",
  gap: 12,
  alignItems: "center",
};

const sectionStyle = {
  border: "0",
  borderBottom: "1px solid var(--border)",
  background: "var(--bg-secondary)",
  padding: 12,
};

const stepStyle = {
  border: "0",
  borderLeft: "2px solid var(--border)",
  background: "var(--bg-primary)",
  padding: 10,
  minWidth: 150,
};

const stepNoStyle = {
  display: "inline-flex",
  width: 24,
  height: 24,
  alignItems: "center",
  justifyContent: "center",
  border: "1px solid var(--accent)",
  borderRadius: 5,
  color: "var(--accent)",
  fontSize: 12,
  fontWeight: 900,
};

const roleStyle = {
  border: "0",
  borderLeft: "2px solid var(--border)",
  background: "var(--bg-primary)",
  padding: 10,
  minWidth: 0,
};
