import { useEffect, useMemo, useState } from "react";
import { postJson, qs, sf } from "../../lib/api";
import { Banner, Button, EmptyState, Field, Pill, Textarea } from "../UXKit";
import Loading from "../Loading";

const DEFAULT_GOAL = "PRODA A1000 #21 현재 step과 KNOB 영향을 확인해줘";

const STATUS_FILTERS = [
  { key: "all", label: "전체" },
  { key: "problem", label: "문제 있음" },
  { key: "missing", label: "missing" },
  { key: "slow", label: "느림" },
];

const TECH_VIEWS = [
  { key: "map", label: "지도 보기" },
  { key: "wiki", label: "Wiki 관계" },
];

export default function AgentStudioTab({ user }) {
  const isAdmin = user?.role === "admin";
  const [days, setDays] = useState(30);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [nodeQuery, setNodeQuery] = useState("");
  const [questions, setQuestions] = useState([]);
  const [workflowMap, setWorkflowMap] = useState(null);
  const [wikiHealth, setWikiHealth] = useState(null);
  const [wikiGraph, setWikiGraph] = useState({ nodes: [], links: [] });
  const [view, setView] = useState("map");
  const [selectedQuestionId, setSelectedQuestionId] = useState("");
  const [selectedNodeId, setSelectedNodeId] = useState("");
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [goal, setGoal] = useState(DEFAULT_GOAL);
  const [runState, setRunState] = useState({ goal: "", semantic: null, plan: [], results: [], final: null, events: [] });
  const [runBusy, setRunBusy] = useState(false);
  const [runErr, setRunErr] = useState("");

  async function reload() {
    setLoading(true);
    setErr("");
    try {
      const [historyResult, mapResult, healthResult, graphResult] = await Promise.allSettled([
        sf("/api/agent/prompt-history" + qs({ limit: 80, scope: isAdmin ? "all" : "mine" })),
        sf("/api/ai-hub/workflow-map" + qs({ days, limit: 40, reference_limit: 160 })),
        sf("/api/ai-hub/wiki-health" + qs({ limit: 12 })),
        sf("/api/knowledge/wiki/graph"),
      ]);
      const historyRows = historyResult.status === "fulfilled" ? normalizeQuestions(historyResult.value.rows || []) : [];
      const nextMap = mapResult.status === "fulfilled" ? mapResult.value : null;
      setQuestions(historyRows);
      setWorkflowMap(nextMap);
      setWikiHealth(healthResult.status === "fulfilled" ? healthResult.value : null);
      setWikiGraph(graphResult.status === "fulfilled" ? normalizeWikiGraph(graphResult.value) : { nodes: [], links: [] });
      setSelectedQuestionId((cur) => historyRows.find((row) => row.__key === cur)?.__key || historyRows[0]?.__key || "");
      setSelectedNodeId((cur) => (nextMap?.nodes || []).find((node) => node.id === cur)?.id || (nextMap?.nodes || []).find((node) => node.type !== "stage")?.id || "");
      const failures = [historyResult, mapResult, healthResult, graphResult].filter((res) => res.status === "rejected");
      if (failures.length) setErr(`${failures.length}개 운영 소스를 불러오지 못했습니다. 가능한 데이터만 표시합니다.`);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { reload(); }, [days, isAdmin]);

  const selectedQuestion = questions.find((row) => row.__key === selectedQuestionId) || null;
  const filterCounts = useMemo(() => {
    const counts = {};
    for (const item of STATUS_FILTERS) counts[item.key] = questions.filter((row) => questionMatchesFilter(row, item.key)).length;
    return counts;
  }, [questions]);
  const filteredQuestions = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return questions.filter((row) => {
      if (!questionMatchesFilter(row, statusFilter)) return false;
      if (!needle) return true;
      return JSON.stringify(row).toLowerCase().includes(needle);
    });
  }, [questions, query, statusFilter]);

  const mapNodes = workflowMap?.nodes || [];
  const mapEdges = workflowMap?.edges || [];
  const stages = workflowMap?.stages || [];
  const nodeNeedle = nodeQuery.trim().toLowerCase();
  const visibleNodes = useMemo(() => {
    if (!nodeNeedle) return mapNodes;
    return mapNodes.filter((node) => workflowNodeSearchText(node).includes(nodeNeedle) || node.type === "stage");
  }, [mapNodes, nodeNeedle]);
  const selectedNode = mapNodes.find((node) => node.id === selectedNodeId) || null;

  const activeRun = runState.goal && runState.goal === goal.trim() ? runState : null;
  const activePrompt = activeRun?.goal || selectedQuestion?.prompt || goal;

  async function runGoal() {
    const trimmed = goal.trim();
    if (!trimmed) return;
    setRunBusy(true);
    setRunErr("");
    setRunState({ goal: trimmed, semantic: null, plan: [], results: [], final: null, events: [] });
    try {
      const [semanticResult, runResult] = await Promise.allSettled([
        postJson("/api/agent/runtime/semantic/resolve", { goal: trimmed, max_terms: 32 }),
        postJson("/api/agent/runtime/run", { goal: trimmed, max_terms: 32, use_llm: false }),
      ]);
      const run = runResult.status === "fulfilled" ? (runResult.value?.run || {}) : {};
      const semantic = run.semantic || (semanticResult.status === "fulfilled" ? semanticResult.value?.semantic : null);
      setRunState({
        goal: trimmed,
        semantic: semantic || null,
        plan: run.plan || [],
        results: run.results || [],
        final: run.conclusion || null,
        events: run.events || [],
      });
      const failures = [semanticResult, runResult].filter((res) => res.status === "rejected");
      if (failures.length) setRunErr(`${failures.length}개 실행 단계를 완료하지 못했습니다. 가능한 결과만 표시합니다.`);
    } catch (e) {
      setRunErr(e?.message || "질문 실행 실패");
    } finally {
      setRunBusy(false);
    }
  }

  const selectQuestion = (row) => {
    setSelectedQuestionId(row.__key);
    if (row.prompt) setGoal(row.prompt);
    setRunErr("");
  };

  return (
    <div className="agent-board-shell">
      <section className="agent-board-toolbar">
        <div className="agent-board-toolbar-title">
          <div className="agent-board-title">운영 보드</div>
          <div className="agent-board-subtitle">질문을 고르고 처리 흐름을 확인한 뒤 개선할 지식과 워크플로우를 정합니다.</div>
        </div>
        <div className="agent-board-runner">
          <Textarea
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            rows={2}
            placeholder="운영자가 확인할 질문을 입력하세요."
            style={{ width: "100%", resize: "vertical" }}
          />
          <div className="agent-board-run-actions">
            <Button variant="primary" onClick={runGoal} disabled={runBusy || !goal.trim()}>{runBusy ? "실행 중" : "처리 실행"}</Button>
            <Button onClick={() => selectedQuestion?.prompt && setGoal(selectedQuestion.prompt)} disabled={!selectedQuestion?.prompt}>선택 질문</Button>
          </div>
        </div>
        <div className="agent-board-source-actions">
          <Field label="기간">
            <select value={days} onChange={(e) => setDays(Number(e.target.value || 30))} style={inputStyle({ width: 110 })}>
              <option value={7}>7일</option>
              <option value={30}>30일</option>
              <option value={90}>90일</option>
            </select>
          </Field>
          <Button onClick={reload} disabled={loading}>{loading ? "갱신 중" : "새로고침"}</Button>
        </div>
      </section>

      {err && <Banner tone="warn" style={{ borderRadius: 0 }}>{err}</Banner>}
      {runErr && <Banner tone="warn" style={{ borderRadius: 0 }}>{runErr}</Banner>}

      <div className="agent-board-grid">
        <aside className="agent-board-pane agent-board-queue">
          <PaneTitle title="질문 큐" meta={`${filteredQuestions.length}/${questions.length}`} />
          <div className="agent-board-filter-row">
            {STATUS_FILTERS.map((item) => (
              <button
                key={item.key}
                type="button"
                onClick={() => setStatusFilter(item.key)}
                className={`agent-board-filter${statusFilter === item.key ? " is-active" : ""}`}
              >
                <span>{item.label}</span>
                <span>{filterCounts[item.key] || 0}</span>
              </button>
            ))}
          </div>
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="질문/사용자/action 검색" style={inputStyle({ width: "100%" })} />
          {loading ? (
            <Loading text="질문 로딩..." size="sm" />
          ) : filteredQuestions.length ? (
            <div className="agent-board-question-list">
              {filteredQuestions.map((row) => (
                <QuestionButton key={row.__key} row={row} selected={selectedQuestionId === row.__key} onClick={() => selectQuestion(row)} />
              ))}
            </div>
          ) : (
            <EmptyState title="질문 이력 없음" />
          )}
        </aside>

        <main className="agent-board-main">
          {loading ? (
            <Loading text="운영 데이터 로딩..." size="md" />
          ) : (
            <>
              <ProcessFlowPanel question={selectedQuestion} run={activeRun} prompt={activePrompt} />
              <details className="agent-board-details">
                <summary>기술 상세</summary>
                <div className="agent-board-detail-toolbar">
                  <PaneTitle title={view === "map" ? "워크플로우 지도" : "Wiki 관계"} meta={`${mapNodes.length} nodes · ${mapEdges.length} edges`} />
                  <ViewSwitch value={view} onChange={setView} />
                  <input value={nodeQuery} onChange={(e) => setNodeQuery(e.target.value)} placeholder="노드/Wiki 검색" style={inputStyle({ width: 220 })} />
                </div>
                <div className="agent-board-tech-surface">
                  {workflowMap && view === "map" ? (
                    <WorkflowCanvas stages={stages} nodes={visibleNodes} selectedId={selectedNodeId} onSelect={setSelectedNodeId} />
                  ) : workflowMap && view === "wiki" ? (
                    <WikiRelationPanel workflowMap={workflowMap} wikiGraph={wikiGraph} nodeNeedle={nodeNeedle} selectedNode={selectedNode} onSelect={setSelectedNodeId} />
                  ) : (
                    <EmptyState title="기술 상세 없음" hint="workflow-map 또는 wiki graph API 상태를 확인하세요." />
                  )}
                </div>
                <RuntimeRawDetails run={activeRun} question={selectedQuestion} node={selectedNode} edges={mapEdges} nodes={mapNodes} wikiHealth={wikiHealth} wikiGraph={wikiGraph} onNodeSelect={setSelectedNodeId} />
              </details>
            </>
          )}
        </main>

        <aside className="agent-board-pane agent-board-improve">
          <ImprovementPanel question={selectedQuestion} run={activeRun} workflowMap={workflowMap} wikiHealth={wikiHealth} />
        </aside>
      </div>
    </div>
  );
}

function QuestionButton({ row, selected, onClick }) {
  const status = questionStatus(row);
  return (
    <button type="button" onClick={onClick} className={`agent-board-question${selected ? " is-selected" : ""}`}>
      <div className="agent-board-question-meta">
        <Pill tone={row.actor_type === "admin" ? "accent" : "info"}>{row.actor_type === "admin" ? "admin" : "user"}</Pill>
        <Pill tone={status.tone}>{status.label}</Pill>
        <span>{row.user || "-"} · {shortTime(row.timestamp)}</span>
      </div>
      <div className="agent-board-question-title">{row.prompt || "-"}</div>
      <div className="agent-board-question-foot">
        <span>{[row.feature, row.intent, row.action].filter(Boolean).join(" / ") || "action 없음"}</span>
        {Number(row.elapsed_ms || 0) > 0 && <span>{Number(row.elapsed_ms).toLocaleString()}ms</span>}
      </div>
    </button>
  );
}

function ProcessFlowPanel({ question, run, prompt }) {
  const steps = buildFlowSteps(question, run, prompt);
  const mode = run ? "새 실행 결과" : question ? "선택 질문 이력" : "대기";
  return (
    <section className="agent-process-panel">
      <div className="agent-process-head">
        <PaneTitle title="처리 흐름" meta={mode} />
        <div className="agent-process-head-note">공개 흐름은 질문, 단어 해석, 계획, 도구 실행, 결과만 보여줍니다.</div>
      </div>
      <div className="agent-process-rail">
        {steps.map((step, idx) => <FlowStep key={step.key} step={step} idx={idx} />)}
      </div>
    </section>
  );
}

function FlowStep({ step, idx }) {
  return (
    <section className="agent-flow-step">
      <div className="agent-flow-index">{idx + 1}</div>
      <div className="agent-flow-body">
        <div className="agent-flow-title-row">
          <span>{step.title}</span>
          <Pill tone={step.tone || "neutral"}>{step.status}</Pill>
        </div>
        {step.meta && <div className="agent-flow-meta">{step.meta}</div>}
        <div className="agent-flow-text">{step.body}</div>
        {!!step.rows?.length && (
          <div className="agent-flow-row-list">
            {step.rows.map((row, i) => (
              <div key={`${row.label}:${i}`} className="agent-flow-row">
                <span>{row.label}</span>
                <strong>{row.value}</strong>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

function ImprovementPanel({ question, run, workflowMap, wikiHealth }) {
  const suggestions = buildImprovementSuggestions(question, run, workflowMap, wikiHealth);
  const activeCount = suggestions.filter((row) => row.active).length;
  return (
    <div className="agent-improve-panel">
      <PaneTitle title="개선 제안" meta={`${activeCount}/${suggestions.length}`} />
      <div className="agent-improve-list">
        {suggestions.map((row) => (
          <section key={row.key} className={`agent-improve-item${row.active ? " is-active" : ""}`}>
            <div className="agent-improve-title-row">
              <span>{row.title}</span>
              <Pill tone={row.tone}>{row.active ? "조치 후보" : "점검"}</Pill>
            </div>
            <div className="agent-improve-body">{row.body}</div>
            <div className="agent-improve-action">{row.action}</div>
          </section>
        ))}
      </div>
      <section className="agent-improve-checklist">
        <SectionTitle title="운영 체크리스트" />
        {[
          "질문 큐에서 blocked/missing/느린 질문을 먼저 고릅니다.",
          "처리 흐름에서 단어 해석과 계획이 끊기는 지점을 확인합니다.",
          "Wiki/source, semantic alias, workflow template 중 하나로 보강합니다.",
          "Runbook dry-run 또는 deep-eval로 회귀를 확인합니다.",
        ].map((text, idx) => (
          <div key={text} className="agent-check-row">
            <span>{idx + 1}</span>
            <p>{text}</p>
          </div>
        ))}
      </section>
    </div>
  );
}

function WorkflowCanvas({ stages, nodes, selectedId, onSelect }) {
  const visibleStages = stages.length ? stages : fallbackStages(nodes);
  return (
    <div className="agent-tech-map">
      {visibleStages.map((stage) => {
        const stageNodes = nodes.filter((node) => node.stage === stage.id && node.type !== "stage");
        return (
          <div key={stage.id} className="agent-tech-stage">
            <div className="agent-tech-stage-title">{stage.title || stage.label || stage.id}</div>
            <div className="agent-tech-stage-detail">{stage.detail || stage.id}</div>
            <div className="agent-tech-node-list">
              {stageNodes.map((node) => <WorkflowNode key={node.id} node={node} selected={selectedId === node.id} onSelect={onSelect} />)}
              {!stageNodes.length && <div className="agent-empty-row">연결 노드 없음</div>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function WorkflowNode({ node, selected, onSelect }) {
  return (
    <button type="button" onClick={() => onSelect(node.id)} className={`agent-tech-node${selected ? " is-selected" : ""}`}>
      <div className="agent-tech-node-head">
        <span>{node.label || node.id}</span>
        <strong style={{ color: toneColor(node.tone) }}>{nodeTypeLabel(node.type)}</strong>
      </div>
      <div className="agent-tech-node-detail">{node.detail || node.id}</div>
    </button>
  );
}

function ViewSwitch({ value, onChange }) {
  return (
    <div className="agent-view-switch">
      {TECH_VIEWS.map((item) => (
        <button key={item.key} type="button" onClick={() => onChange(item.key)} className={value === item.key ? "is-active" : ""}>
          {item.label}
        </button>
      ))}
    </div>
  );
}

function WikiRelationPanel({ workflowMap, wikiGraph, nodeNeedle, selectedNode, onSelect }) {
  const workflowNodes = workflowMap?.nodes || [];
  const evidenceNodes = workflowNodes
    .filter((node) => node.stage === "evidence" && node.type !== "stage")
    .filter((node) => !nodeNeedle || workflowNodeSearchText(node).includes(nodeNeedle));
  const graphNodes = (wikiGraph?.nodes || []).filter((node) => {
    if (!nodeNeedle) return true;
    return [node.id, node.label, node.kind, node.doc_id].filter(Boolean).join(" ").toLowerCase().includes(nodeNeedle);
  });
  const graphLinks = (wikiGraph?.links || []).filter((edge) => {
    if (!nodeNeedle) return true;
    return [edge.source, edge.target, edge.label, edge.kind].filter(Boolean).join(" ").toLowerCase().includes(nodeNeedle);
  });
  const degree = new Map();
  for (const edge of wikiGraph?.links || []) {
    degree.set(edge.source, (degree.get(edge.source) || 0) + 1);
    degree.set(edge.target, (degree.get(edge.target) || 0) + 1);
  }
  const selectedKey = selectedNode ? String(selectedNode.id || "").replace(/^(wiki|graph|relation|column|feature):/, "") : "";

  return (
    <div className="agent-wiki-grid">
      <section className="agent-wiki-column">
        <SectionTitle title="워크플로우 근거" meta={`${evidenceNodes.length}`} />
        <div className="agent-relation-list">
          {evidenceNodes.slice(0, 80).map((node) => (
            <button key={node.id} type="button" onClick={() => onSelect(node.id)} className={`agent-relation-row is-button${selectedNode?.id === node.id ? " is-selected" : ""}`}>
              <span>{node.label || node.id}</span>
              <em>{nodeTypeLabel(node.type)}</em>
            </button>
          ))}
          {!evidenceNodes.length && <div className="agent-empty-row">근거 노드 없음</div>}
        </div>
      </section>

      <section className="agent-wiki-column">
        <SectionTitle title="Wiki 노드" meta={`${graphNodes.length}`} />
        <div className="agent-relation-list">
          {graphNodes.slice(0, 100).map((node) => {
            const active = selectedKey && [node.id, node.doc_id, node.label].filter(Boolean).includes(selectedKey);
            return (
              <div key={node.id} className={`agent-relation-row${active ? " is-selected" : ""}`}>
                <span>{node.label || node.id}</span>
                <em>{node.kind || "node"} · {degree.get(node.id) || 0}</em>
              </div>
            );
          })}
          {!graphNodes.length && <div className="agent-empty-row">Wiki 노드 없음</div>}
        </div>
      </section>

      <section className="agent-wiki-column">
        <SectionTitle title="관계 링크" meta={`${graphLinks.length}`} />
        <div className="agent-relation-list">
          {graphLinks.slice(0, 120).map((edge) => (
            <div key={edge.id || `${edge.source}:${edge.target}`} className="agent-relation-edge">
              <span>{edge.source}</span>
              <strong>{edge.label || "link"}</strong>
              <span>{edge.target}</span>
            </div>
          ))}
          {!graphLinks.length && <div className="agent-empty-row">관계 링크 없음</div>}
        </div>
      </section>
    </div>
  );
}

function RuntimeRawDetails({ run, question, node, edges, nodes, wikiHealth, wikiGraph, onNodeSelect }) {
  const byId = new Map(nodes.map((item) => [item.id, item]));
  const incoming = node ? edges.filter((edge) => edge.to === node.id) : [];
  const outgoing = node ? edges.filter((edge) => edge.from === node.id) : [];
  return (
    <div className="agent-raw-grid">
      <section className="agent-raw-section">
        <SectionTitle title="선택 질문/노드" />
        {question ? (
          <>
            <DetailLine label="actor" value={`${question.user || "-"} · ${question.actor_type || "user"}`} />
            <DetailLine label="status" value={question.status || "done"} />
            <DetailLine label="question" value={question.prompt || "-"} strong />
            <DetailLine label="improve" value={questionImproveText(question)} />
          </>
        ) : (
          <DetailLine label="question" value="질문 큐에서 행을 선택하면 이력 상세가 표시됩니다." />
        )}
        {node && (
          <>
            <DetailLine label="node" value={node.id} mono />
            <DetailLine label="detail" value={node.detail || "-"} />
            <EdgeList title="입력" edges={incoming} other={(edge) => byId.get(edge.from)} onSelect={onNodeSelect} />
            <EdgeList title="출력" edges={outgoing} other={(edge) => byId.get(edge.to)} onSelect={onNodeSelect} />
          </>
        )}
      </section>
      <section className="agent-raw-section">
        <SectionTitle title="Wiki 상태" meta={`${wikiGraph.nodes.length} nodes · ${wikiGraph.links.length} links`} />
        <div className="agent-mini-stat-grid">
          <MiniStat label="docs" value={wikiHealth?.counts?.docs || 0} />
          <MiniStat label="sources" value={wikiHealth?.counts?.sources || 0} />
          <MiniStat label="graph" value={wikiHealth?.counts?.graph_nodes || wikiGraph.nodes.length || 0} />
          <MiniStat label="lint" value={wikiHealth?.counts?.lint_issues || 0} />
        </div>
      </section>
      <section className="agent-raw-section agent-raw-json-section">
        <SectionTitle title="raw trace" meta={run ? `${run.events?.length || 0} events` : "접힘 상세"} />
        {run ? (
          <pre className="agent-runtime-json">{shortJson({ semantic: run.semantic, plan: run.plan, results: run.results, conclusion: run.final, events: run.events })}</pre>
        ) : (
          <div className="agent-empty-row">새 질문을 실행하면 semantic, plan, result, event 원본이 이 접힘 영역에만 표시됩니다.</div>
        )}
      </section>
    </div>
  );
}

function EdgeList({ title, edges, other, onSelect }) {
  if (!edges.length) return null;
  return (
    <div className="agent-edge-list">
      <div>{title}</div>
      {edges.slice(0, 8).map((edge, idx) => {
        const node = other(edge);
        return (
          <button key={`${edge.from}:${edge.to}:${idx}`} type="button" onClick={() => node && onSelect(node.id)}>
            <span>{node?.label || node?.id || edge.from || edge.to}</span>
            <em>{edge.label || edge.type || ""}</em>
          </button>
        );
      })}
    </div>
  );
}

function PaneTitle({ title, meta }) {
  return (
    <div className="agent-pane-title">
      <div>{title}</div>
      {meta && <Pill tone="neutral">{meta}</Pill>}
    </div>
  );
}

function SectionTitle({ title, meta }) {
  return (
    <div className="agent-section-title">
      <div>{title}</div>
      {meta && <Pill tone="neutral">{meta}</Pill>}
    </div>
  );
}

function DetailLine({ label, value, strong, mono }) {
  return (
    <div className="agent-detail-line">
      <div>{label}</div>
      <p className={`${strong ? "is-strong" : ""}${mono ? " is-mono" : ""}`}>{value || "-"}</p>
    </div>
  );
}

function MiniStat({ label, value }) {
  return (
    <div className="agent-mini-stat">
      <div>{label}</div>
      <strong>{Number(value || 0).toLocaleString()}</strong>
    </div>
  );
}

function buildFlowSteps(question, run, prompt) {
  const semantic = run?.semantic || null;
  const plan = run?.plan || [];
  const actionPlan = publicActionPlan(plan);
  const results = run?.results || [];
  const final = run?.final || null;
  const missing = collectMissing(question, run);
  const guardrail = final?.guardrail || latestGuardrail(run?.events) || {};
  const semanticRows = semanticRowsFor(semantic);
  const planRows = actionPlan.slice(0, 4).map((row) => ({
    label: row.unit_ai || row.agent_id || "unit",
    value: [row.action, row.policy, (row.missing_slots || []).join(", ")].filter(Boolean).join(" · ") || "계획 있음",
  }));
  const resultRows = results.slice(0, 4).map((row) => ({
    label: row.agent_id || row.unit_ai || "result",
    value: [row.status, row.summary || row.message, row.guardrail?.status].filter(Boolean).join(" · ") || (row.handled ? "handled" : "not handled"),
  }));

  return [
    {
      key: "question",
      title: "질문",
      status: prompt ? "준비" : "대기",
      tone: prompt ? "info" : "neutral",
      meta: question ? `${question.user || "-"} · ${shortTime(question.timestamp)}` : run ? "새 실행" : "",
      body: prompt || "왼쪽 질문 큐에서 질문을 선택하거나 상단에 새 질문을 입력하세요.",
    },
    {
      key: "terms",
      title: "단어 해석",
      status: semantic ? `coverage ${Math.round(Number(semantic.coverage || 0) * 100)}%` : question ? "이력 요약" : "대기",
      tone: semantic ? coverageTone(semantic.coverage) : question ? "neutral" : "neutral",
      meta: semantic?.intent || [question?.feature, question?.intent].filter(Boolean).join(" / "),
      body: semantic ? semanticSummary(semantic) : ([question?.feature, question?.intent, question?.action].filter(Boolean).join(" / ") || "질문 실행 후 단어 해석이 표시됩니다."),
      rows: semanticRows,
    },
    {
      key: "plan",
      title: "계획",
      status: actionPlan.length ? `${actionPlan.length} actions` : question?.action ? "이력 action" : "대기",
      tone: actionPlan.length ? "info" : "neutral",
      meta: guardrail.status ? `guardrail ${guardrail.status}` : "",
      body: actionPlan.length ? "질문 의도에 맞는 unit action 계획을 read-only/approval 정책과 함께 정리했습니다." : (question?.action || "실행 계획이 아직 없습니다."),
      rows: planRows,
    },
    {
      key: "tools",
      title: "도구 실행",
      status: results.length ? `${results.length} results` : question?.status || "대기",
      tone: results.some((row) => row.status === "blocked" || row.guardrail?.status === "blocked") ? "bad" : results.length ? "ok" : questionStatus(question).tone,
      meta: Number(question?.elapsed_ms || 0) > 0 ? `${Number(question.elapsed_ms).toLocaleString()}ms` : "",
      body: results.length ? "Unit AI 실행 결과와 guardrail 요약을 표시합니다." : historyToolText(question),
      rows: resultRows,
    },
    {
      key: "result",
      title: "결과",
      status: final ? "완료" : missing.length ? "보강 필요" : question ? questionStatus(question).label : "대기",
      tone: final ? (missing.length ? "warn" : "ok") : missing.length ? "warn" : questionStatus(question).tone,
      meta: missing.length ? `missing: ${missing.join(", ")}` : "",
      body: final?.answer || question?.answer_excerpt || questionImproveText(question),
      rows: [
        ...(final?.warnings || []).slice(0, 3).map((value) => ({ label: "warning", value })),
        ...(final?.next_actions || []).slice(0, 3).map((value) => ({ label: "next", value })),
      ],
    },
  ];
}

function buildImprovementSuggestions(question, run, workflowMap, wikiHealth) {
  const semantic = run?.semantic || null;
  const missing = collectMissing(question, run);
  const coverage = Number(semantic?.coverage || 0);
  const warnings = Array.isArray(workflowMap?.warnings) ? workflowMap.warnings : [];
  const lint = Number(wikiHealth?.counts?.lint_issues || 0);
  const planMissing = (run?.plan || []).flatMap((row) => row.missing_slots || []).filter(Boolean);
  const guardrail = run?.final?.guardrail || latestGuardrail(run?.events) || {};
  const blocked = (run?.results || []).filter((row) => row.status === "blocked" || row.guardrail?.status === "blocked").length;
  return [
    {
      key: "missing",
      title: "missing slot",
      tone: missing.length || planMissing.length ? "warn" : "neutral",
      active: !!(missing.length || planMissing.length),
      body: missing.length || planMissing.length ? [...new Set([...missing, ...planMissing])].join(", ") : "필수 slot 누락은 아직 보이지 않습니다.",
      action: "질문 설계에서 required slot을 확인하고, 자주 빠지는 slot은 workflow template으로 고정합니다.",
    },
    {
      key: "alias",
      title: "semantic alias",
      tone: semantic && coverage < 0.55 ? "warn" : "neutral",
      active: !!semantic && coverage < 0.55,
      body: semantic ? `현재 coverage ${Math.round(coverage * 100)}% · ${semantic.warnings?.[0] || semantic.intent || "semantic 확인됨"}` : "새 질문 실행 후 alias 후보를 판단합니다.",
      action: "용어/기능 AI에서 alias 또는 intent hint 초안을 만들고 관리자 승인 흐름으로 반영합니다.",
    },
    {
      key: "wiki",
      title: "Wiki/source",
      tone: lint > 0 ? "warn" : "neutral",
      active: lint > 0 || questionStatus(question).key === "missing",
      body: lint > 0 ? `Wiki lint ${lint}건을 먼저 줄여야 합니다.` : "근거가 약한 질문은 source/page 연결 상태를 확인합니다.",
      action: "Wiki에서 source와 maintained page를 연결하고 graph/lint 상태를 확인합니다.",
    },
    {
      key: "workflow",
      title: "workflow template",
      tone: warnings.length ? "warn" : "neutral",
      active: warnings.length > 0 || questionStatus(question).key === "slow",
      body: warnings.length ? `${warnings.length}개 workflow 경고가 있습니다.` : "반복 질문이면 질문 설계에서 template 승격 후보로 봅니다.",
      action: "지도 보기에서 끊긴 node/tool을 확인한 뒤 Runbook dry-run으로 검증합니다.",
    },
    {
      key: "runbook",
      title: "runbook 검증",
      tone: blocked || guardrail.status === "blocked" ? "bad" : "neutral",
      active: !!blocked || guardrail.status === "blocked",
      body: blocked ? `${blocked}개 도구 실행이 blocked 상태입니다.` : `guardrail ${guardrail.status || "pending"}`,
      action: "raw DB/file 직접 수정 대신 승인형 app-action 경로가 있는지 확인합니다.",
    },
  ];
}

function normalizeQuestions(rows) {
  return (rows || []).map((row, idx) => ({
    ...row,
    __key: String(row.id || row.client_run_id || `${row.timestamp || idx}:${row.user || ""}:${row.prompt || ""}`),
  }));
}

function normalizeWikiGraph(data) {
  const rawNodes = Array.isArray(data?.nodes) ? data.nodes : Array.isArray(data?.graph?.nodes) ? data.graph.nodes : [];
  const rawLinks = Array.isArray(data?.links) ? data.links : Array.isArray(data?.edges) ? data.edges : Array.isArray(data?.graph?.edges) ? data.graph.edges : [];
  const nodes = rawNodes.map((node) => ({
    ...node,
    id: String(node.id || node.doc_id || node.label || ""),
    label: String(node.label || node.title || node.doc_id || node.id || ""),
    kind: String(node.kind || node.type || "node"),
  })).filter((node) => node.id);
  const ids = new Set(nodes.map((node) => node.id));
  const links = rawLinks.map((edge, idx) => ({
    ...edge,
    id: String(edge.id || edge.edge_id || `edge_${idx}`),
    source: typeof edge.source === "object" ? String(edge.source?.id || "") : String(edge.source || ""),
    target: typeof edge.target === "object" ? String(edge.target?.id || "") : String(edge.target || ""),
    label: String(edge.label || edge.relation || edge.type || ""),
  })).filter((edge) => ids.has(edge.source) && ids.has(edge.target));
  return { nodes, links };
}

function fallbackStages(nodes) {
  const stages = [...new Set((nodes || []).map((node) => node.stage).filter(Boolean))];
  return stages.map((id) => ({ id, title: id, detail: "" }));
}

function workflowNodeSearchText(node) {
  return [node?.id, node?.type, node?.stage, node?.label, node?.detail, node?.tool_name, node?.workflow_key, ...(node?.tags || [])].filter(Boolean).join(" ").toLowerCase();
}

function questionMatchesFilter(row, filter) {
  if (filter === "all") return true;
  return questionStatus(row).key === filter || (filter === "problem" && ["problem", "missing", "slow"].includes(questionStatus(row).key));
}

function questionStatus(row) {
  if (!row) return { key: "empty", label: "대기", tone: "neutral" };
  const status = String(row.status || "").toLowerCase();
  const missing = Array.isArray(row.missing) ? row.missing.filter(Boolean) : [];
  const elapsed = Number(row.elapsed_ms || 0);
  if (missing.length || status.includes("missing")) return { key: "missing", label: "missing", tone: "warn" };
  if (["blocked", "failed", "error"].includes(status)) return { key: "problem", label: status, tone: "bad" };
  if (elapsed >= 3000 || status.includes("slow")) return { key: "slow", label: "느림", tone: "warn" };
  if (status === "done" || status === "ok" || status === "success") return { key: "ok", label: "정상", tone: "ok" };
  return { key: status || "ok", label: status || "정상", tone: "neutral" };
}

function collectMissing(question, run) {
  const values = [];
  if (Array.isArray(question?.missing)) values.push(...question.missing);
  if (Array.isArray(run?.final?.missing)) values.push(...run.final.missing);
  for (const row of run?.plan || []) {
    if (Array.isArray(row.missing_slots)) values.push(...row.missing_slots);
  }
  return [...new Set(values.filter(Boolean).map(String))];
}

function publicActionPlan(plan) {
  return (plan || []).filter((row) => row.unit_ai && row.action && !(row.unit_ai === "agent_runtime" && ["resolve_semantic", "plan", "review_guardrail", "conclude"].includes(row.action)));
}

function latestGuardrail(events) {
  for (let i = (events || []).length - 1; i >= 0; i -= 1) {
    const g = events[i]?.data?.guardrail;
    if (g && typeof g === "object") return g;
  }
  return null;
}

function semanticRowsFor(semantic) {
  if (!semantic) return [];
  const normalized = semantic.normalized_terms || {};
  const rows = (semantic.tokens || []).slice(0, 7).map((token) => ({ label: token, value: normalized[token] || "원문" }));
  const candidates = (semantic.candidates || []).slice(0, 3).map((cand) => ({ label: cand.token || "candidate", value: [cand.column, cand.relation_id, cand.source].filter(Boolean).join(" · ") || "candidate" }));
  return [...rows, ...candidates].slice(0, 8);
}

function semanticSummary(semantic) {
  const slots = semantic?.slots || {};
  const slotText = Object.entries(slots).filter(([, value]) => value != null && value !== "").slice(0, 4).map(([key, value]) => `${key}=${value}`).join(" · ");
  const warnings = (semantic?.warnings || []).slice(0, 2).join(" · ");
  return slotText || warnings || "질문 단어를 intent, slot, column 후보로 정규화했습니다.";
}

function coverageTone(value) {
  const coverage = Number(value || 0);
  if (coverage >= 0.7) return "ok";
  if (coverage >= 0.35) return "warn";
  return "bad";
}

function historyToolText(question) {
  if (!question) return "질문 실행 후 도구 실행 요약이 표시됩니다.";
  const parts = [question.status, question.answer_excerpt].filter(Boolean);
  return parts.join(" · ") || "prompt-history에 남은 실행 이력을 표시합니다.";
}

function questionImproveText(question) {
  const missing = Array.isArray(question?.missing) ? question.missing.filter(Boolean) : [];
  if (missing.length) return `missing slot: ${missing.join(", ")}`;
  const status = String(question?.status || "").toLowerCase();
  if (["blocked", "missing", "failed", "error"].includes(status)) return "실패 상태를 기준으로 Wiki/source/workflow 보강 후보";
  return "반복 질문이면 workflow template 또는 Wiki 근거로 승격";
}

function nodeTypeLabel(type) {
  const value = String(type || "");
  if (value === "workflow") return "workflow";
  if (value === "workflow_step") return "step";
  if (value === "tool") return "agent";
  if (["wiki", "relation", "column", "graph", "feature", "arg"].includes(value)) return "wiki";
  return value || "node";
}

function toneColor(tone) {
  if (tone === "bad") return "var(--danger)";
  if (tone === "ok") return "var(--ok)";
  if (tone === "warn") return "var(--warn)";
  if (tone === "info") return "var(--info)";
  return "var(--text-secondary)";
}

function shortTime(value) {
  return String(value || "").replace("T", " ").slice(0, 16) || "-";
}

function shortJson(value) {
  try {
    return JSON.stringify(value || {}, null, 2);
  } catch (_) {
    return String(value || "");
  }
}

function inputStyle(extra = {}) {
  return {
    border: "1px solid var(--border)",
    borderRadius: 4,
    background: "var(--bg-primary)",
    color: "var(--text-primary)",
    padding: "6px 8px",
    fontSize: 12,
    minWidth: 0,
    ...extra,
  };
}
