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
  const [homeAgent, setHomeAgent] = useState({ goal: "", plan: [], trace: [], reply: "", busy: false, err: "" });
  const [nodeRunStatus, setNodeRunStatus] = useState({});  // { "tool:filebrowser": "running"|"ok"|"error" }
  const [inspector, setInspector] = useState({ input: {}, busy: false, result: null, err: "" });

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

  function runHomeAgent() {
    const trimmed = goal.trim();
    if (!trimmed) return;
    setHomeAgent({ goal: trimmed, plan: [], trace: [], reply: "", busy: true, err: "" });
    setNodeRunStatus({});
    const token = (typeof localStorage !== "undefined" && localStorage.getItem("flow_session_token")) || "";
    const url = `/api/home-agent/orchestrate/stream?prompt=${encodeURIComponent(trimmed)}&top_k=4${token ? `&t=${encodeURIComponent(token)}` : ""}`;
    let es;
    try {
      es = new EventSource(url, { withCredentials: true });
    } catch (e) {
      setHomeAgent((cur) => ({ ...cur, busy: false, err: "EventSource unsupported" }));
      return;
    }
    const closeAll = () => { try { es.close(); } catch {} };
    es.addEventListener("plan", (ev) => {
      try {
        const obj = JSON.parse(ev.data);
        setHomeAgent((cur) => ({ ...cur, plan: obj.steps || [] }));
        const next = {};
        for (const s of obj.steps || []) next[`tool:${s.tool}`] = "queued";
        setNodeRunStatus(next);
      } catch {}
    });
    es.addEventListener("step_start", (ev) => {
      try {
        const obj = JSON.parse(ev.data);
        setNodeRunStatus((cur) => ({ ...cur, [`tool:${obj.tool}`]: "running" }));
      } catch {}
    });
    es.addEventListener("step_end", (ev) => {
      try {
        const obj = JSON.parse(ev.data);
        setNodeRunStatus((cur) => ({ ...cur, [`tool:${obj.tool}`]: obj.ok ? "ok" : "error" }));
      } catch {}
    });
    es.addEventListener("reply", (ev) => {
      try {
        const obj = JSON.parse(ev.data);
        setHomeAgent((cur) => ({ ...cur, trace: obj.trace || [], reply: obj.reply || "", busy: false }));
      } catch {}
      closeAll();
    });
    es.onerror = () => {
      setHomeAgent((cur) => ({ ...cur, busy: false, err: cur.busy ? "스트림 연결 종료" : cur.err }));
      closeAll();
    };
  }

  async function runSingleTool(toolName, inputDict) {
    if (!toolName) return;
    setInspector((cur) => ({ ...cur, busy: true, err: "", result: null }));
    setNodeRunStatus((cur) => ({ ...cur, [`tool:${toolName}`]: "running" }));
    try {
      const out = await postJson("/api/home-agent/run-tool", { tool: toolName, input: inputDict || {} });
      setInspector({ input: inputDict || {}, busy: false, err: "", result: out });
      setNodeRunStatus((cur) => ({ ...cur, [`tool:${toolName}`]: out.ok ? "ok" : "error" }));
    } catch (e) {
      setInspector((cur) => ({ ...cur, busy: false, err: e?.message || "도구 실행 실패", result: null }));
      setNodeRunStatus((cur) => ({ ...cur, [`tool:${toolName}`]: "error" }));
    }
  }

  // 선택 노드가 바뀌면 inspector 폼을 노드의 example/input_schema로 초기화.
  useEffect(() => {
    if (!selectedNodeId || !selectedNodeId.startsWith("tool:")) return;
    setInspector({ input: { prompt: goal.trim() || DEFAULT_GOAL }, busy: false, result: null, err: "" });
  }, [selectedNodeId]);

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
            <Button onClick={runHomeAgent} disabled={homeAgent.busy || !goal.trim()} title="홈 에이전트가 도구를 자동 선택해 멀티스텝 실행 (SSE)">{homeAgent.busy ? "Home 실행 중" : "Home Agent"}</Button>
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
      {homeAgent.err && <Banner tone="warn" style={{ borderRadius: 0 }}>Home Agent: {homeAgent.err}</Banner>}

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
              <ConnectedWorkflowPanel
                workflowMap={workflowMap}
                stages={stages}
                nodes={visibleNodes}
                edges={mapEdges}
                selectedId={selectedNodeId}
                selectedNode={selectedNode}
                nodeNeedle={nodeNeedle}
                runStatus={nodeRunStatus}
                onSelect={setSelectedNodeId}
              />
              <details className="agent-board-details">
                <summary>기술 상세 및 원본</summary>
                <div className="agent-board-detail-toolbar">
                  <PaneTitle title={view === "map" ? "워크플로우 지도" : "Wiki 관계"} meta={`${mapNodes.length} nodes · ${mapEdges.length} edges`} />
                  <ViewSwitch value={view} onChange={setView} />
                  <input value={nodeQuery} onChange={(e) => setNodeQuery(e.target.value)} placeholder="노드/Wiki 검색" style={inputStyle({ width: 220 })} />
                </div>
                <div className="agent-board-tech-surface">
                  {workflowMap && view === "map" ? (
                    <WorkflowCanvas stages={stages} nodes={visibleNodes} selectedId={selectedNodeId} onSelect={setSelectedNodeId} runStatus={nodeRunStatus} />
                  ) : workflowMap && view === "wiki" ? (
                    <WikiRelationPanel workflowMap={workflowMap} wikiGraph={wikiGraph} nodeNeedle={nodeNeedle} selectedNode={selectedNode} onSelect={setSelectedNodeId} />
                  ) : (
                    <EmptyState title="기술 상세 없음" hint="workflow-map 또는 wiki graph API 상태를 확인하세요." />
                  )}
                  {selectedNodeId && selectedNodeId.startsWith("tool:") && (
                    <NodeInspectorPanel
                      nodeId={selectedNodeId}
                      workflowMap={workflowMap}
                      input={inspector.input}
                      busy={inspector.busy}
                      err={inspector.err}
                      result={inspector.result}
                      onChangeInput={(next) => setInspector((cur) => ({ ...cur, input: next }))}
                      onRun={() => runSingleTool(selectedNodeId.slice(5), inspector.input)}
                    />
                  )}
                  {(homeAgent.trace.length > 0 || homeAgent.reply) && (
                    <HomeAgentTracePanel plan={homeAgent.plan} trace={homeAgent.trace} reply={homeAgent.reply} prompt={goal.trim()} />
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

function ConnectedWorkflowPanel({ workflowMap, stages, nodes, edges, selectedId, selectedNode, nodeNeedle, runStatus, onSelect }) {
  const visibleStages = stages.length ? stages : fallbackStages(nodes);
  const layout = useMemo(() => buildConnectedLayout(visibleStages, nodes), [visibleStages, nodes]);
  const counts = workflowMap?.counts || {};
  const visibleIds = useMemo(() => new Set(layout.items.map((item) => item.node.id)), [layout]);
  const visibleEdges = useMemo(() => {
    return (edges || []).filter((edge) => visibleIds.has(edge.from) && visibleIds.has(edge.to));
  }, [edges, visibleIds]);

  return (
    <section className="agent-connected-panel">
      <div className="agent-connected-head">
        <PaneTitle title="연결 지도" meta={`${visibleEdges.length}/${edges.length || 0} edges`} />
        <div className="agent-connected-summary">
          <Pill tone="info">도구 {counts.tools_visible || 0}/{counts.tools_total || 0}</Pill>
          <Pill tone={(counts.workflow_missing_tools || counts.workflow_empty_templates) ? "warn" : "neutral"}>workflow {counts.workflow_templates_visible || 0}</Pill>
          <Pill tone={counts.tools_without_refs_visible ? "warn" : "ok"}>근거 없음 {counts.tools_without_refs_visible || 0}</Pill>
          {nodeNeedle && <Pill tone="accent">검색 {layout.items.length}</Pill>}
        </div>
      </div>
      {!workflowMap ? (
        <EmptyState title="연결 지도 없음" hint="workflow-map API 상태를 확인하세요." />
      ) : (
        <div className="agent-connected-grid">
          <ConnectedWorkflowMap
            layout={layout}
            edges={visibleEdges}
            selectedId={selectedId}
            runStatus={runStatus}
            onSelect={onSelect}
          />
          <ConnectedNodeDetail
            node={selectedNode}
            edges={edges}
            nodes={workflowMap?.nodes || []}
            onSelect={onSelect}
          />
        </div>
      )}
    </section>
  );
}

function ConnectedWorkflowMap({ layout, edges, selectedId, runStatus, onSelect }) {
  const activeEdges = new Set(
    (edges || [])
      .filter((edge) => edge.from === selectedId || edge.to === selectedId)
      .map((edge) => `${edge.from}->${edge.to}:${edge.kind || ""}:${edge.label || ""}`)
  );
  return (
    <div className="agent-connected-scroll">
      <div
        className="agent-connected-canvas"
        style={{ minWidth: layout.minWidth, height: layout.height }}
      >
        <svg className="agent-connected-svg" viewBox={`0 0 1000 ${layout.height}`} preserveAspectRatio="none" aria-hidden="true">
          <defs>
            <marker id="agent-connected-arrow" markerWidth="8" markerHeight="8" refX="7" refY="3.5" orient="auto">
              <path d="M0,0 L8,3.5 L0,7 Z" />
            </marker>
          </defs>
          {layout.stageBands.map((band) => (
            <g key={band.id} className="agent-connected-band">
              <rect x={band.x} y="0" width={band.width} height={layout.height} />
              <text x={band.x + 12} y="22">{band.title}</text>
            </g>
          ))}
          {(edges || []).map((edge, idx) => {
            const from = layout.positions.get(edge.from);
            const to = layout.positions.get(edge.to);
            if (!from || !to) return null;
            const key = `${edge.from}->${edge.to}:${edge.kind || ""}:${edge.label || ""}`;
            const active = activeEdges.has(key);
            return (
              <g key={`${key}:${idx}`} className={`agent-connected-edge${active ? " is-active" : ""}`}>
                <path d={edgePath(from, to)} markerEnd="url(#agent-connected-arrow)" />
                {active && <text x={(from.x + to.x) / 2} y={(from.y + to.y) / 2 - 5}>{edge.label || edge.kind || "link"}</text>}
              </g>
            );
          })}
        </svg>
        <div className="agent-connected-node-layer">
          {layout.items.map(({ node, x, y }) => (
            <ConnectedWorkflowNode
              key={node.id}
              node={node}
              selected={selectedId === node.id}
              status={(runStatus || {})[node.id]}
              x={x}
              y={y}
              onSelect={onSelect}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function ConnectedWorkflowNode({ node, selected, status, x, y, onSelect }) {
  return (
    <button
      type="button"
      onClick={() => onSelect(node.id)}
      className={`agent-connected-node${selected ? " is-selected" : ""}${node.enabled === false ? " is-disabled" : ""}`}
      style={{ left: `${x / 10}%`, top: y - 34 }}
    >
      <div className="agent-connected-node-head">
        <span>{node.label || node.id}</span>
        <strong style={{ color: toneColor(node.tone) }}>{nodeTypeLabel(node.type)}</strong>
      </div>
      <div className="agent-connected-node-detail">{node.detail || node.id}</div>
      <div className="agent-connected-node-foot">
        {status && <Pill tone={status === "ok" ? "ok" : status === "error" ? "bad" : status === "running" ? "info" : "neutral"}>{NODE_STATUS_LABEL[status] || status}</Pill>}
        {node.type === "tool" && <span>{node.metrics?.count || 0} calls</span>}
        {node.type === "workflow" && <span>{node.metrics?.steps || 0} steps</span>}
        {node.type === "deep_eval" && <span>{node.metrics?.status || "eval"}</span>}
      </div>
    </button>
  );
}

function ConnectedNodeDetail({ node, edges, nodes, onSelect }) {
  const byId = useMemo(() => new Map((nodes || []).map((item) => [item.id, item])), [nodes]);
  if (!node) {
    return (
      <aside className="agent-connected-detail">
        <SectionTitle title="선택 노드" />
        <div className="agent-empty-row">지도에서 노드를 선택하면 입력/출력 엣지, Wiki/schema 근거, 개선 액션을 바로 확인합니다.</div>
      </aside>
    );
  }
  const incoming = (edges || []).filter((edge) => edge.to === node.id);
  const outgoing = (edges || []).filter((edge) => edge.from === node.id);
  const evidence = nodeEvidenceRows(node, incoming, outgoing, byId);
  const metrics = nodeMetricRows(node);
  return (
    <aside className="agent-connected-detail">
      <SectionTitle title="선택 노드" meta={nodeTypeLabel(node.type)} />
      <div className="agent-connected-detail-title">{node.label || node.id}</div>
      <div className="agent-connected-detail-id">{node.id}</div>
      <div className="agent-connected-detail-body">{node.detail || "상세 설명 없음"}</div>
      {!!metrics.length && (
        <div className="agent-connected-metrics">
          {metrics.map((row) => <MiniStat key={row.label} label={row.label} value={row.value} />)}
        </div>
      )}
      {!!(node.tags || []).length && (
        <div className="agent-connected-tags">
          {(node.tags || []).slice(0, 10).map((tag) => <Pill key={tag} tone="neutral">{tag}</Pill>)}
        </div>
      )}
      <ConnectedEdgeList title="입력" edges={incoming} other={(edge) => byId.get(edge.from)} onSelect={onSelect} />
      <ConnectedEdgeList title="출력" edges={outgoing} other={(edge) => byId.get(edge.to)} onSelect={onSelect} />
      <div className="agent-connected-evidence">
        <SectionTitle title="Wiki/schema 근거" meta={`${evidence.length}`} />
        {evidence.length ? evidence.slice(0, 8).map((row) => (
          <button key={`${row.id}:${row.label}`} type="button" onClick={() => row.nodeId && onSelect(row.nodeId)} disabled={!row.nodeId}>
            <span>{row.label}</span>
            <em>{row.type}</em>
          </button>
        )) : <div className="agent-empty-row">연결된 근거가 없습니다.</div>}
      </div>
      <div className="agent-connected-action">
        <SectionTitle title="개선 액션" />
        <div>{nodeNextAction(node, evidence)}</div>
      </div>
    </aside>
  );
}

function ConnectedEdgeList({ title, edges, other, onSelect }) {
  return (
    <div className="agent-connected-edge-list">
      <div>{title}</div>
      {edges.length ? edges.slice(0, 8).map((edge, idx) => {
        const node = other(edge);
        return (
          <button key={`${edge.from}:${edge.to}:${idx}`} type="button" onClick={() => node && onSelect(node.id)} disabled={!node}>
            <span>{node?.label || node?.id || edge.from || edge.to}</span>
            <em>{edge.label || edge.kind || "link"}</em>
          </button>
        );
      }) : <p>없음</p>}
    </div>
  );
}

function WorkflowCanvas({ stages, nodes, selectedId, onSelect, runStatus }) {
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
              {stageNodes.map((node) => (
                <WorkflowNode
                  key={node.id}
                  node={node}
                  selected={selectedId === node.id}
                  onSelect={onSelect}
                  status={(runStatus || {})[node.id]}
                />
              ))}
              {!stageNodes.length && <div className="agent-empty-row">연결 노드 없음</div>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

const NODE_STATUS_STYLE = {
  queued: { borderColor: "#94a3b8", background: "#f1f5f9" },
  running: { borderColor: "#3b82f6", background: "#dbeafe", boxShadow: "0 0 0 2px #3b82f660" },
  ok: { borderColor: "#16a34a", background: "#dcfce7" },
  error: { borderColor: "#dc2626", background: "#fee2e2" },
};
const NODE_STATUS_LABEL = { queued: "대기", running: "실행 중", ok: "성공", error: "실패" };

function WorkflowNode({ node, selected, onSelect, status }) {
  const statusStyle = (status && NODE_STATUS_STYLE[status]) || {};
  return (
    <button
      type="button"
      onClick={() => onSelect(node.id)}
      className={`agent-tech-node${selected ? " is-selected" : ""}`}
      style={statusStyle}
    >
      <div className="agent-tech-node-head">
        <span>{node.label || node.id}</span>
        <strong style={{ color: toneColor(node.tone) }}>{nodeTypeLabel(node.type)}</strong>
      </div>
      <div className="agent-tech-node-detail">{node.detail || node.id}</div>
      {status && <div style={{ marginTop: 4, fontSize: 11, color: "#475569" }}>{NODE_STATUS_LABEL[status] || status}</div>}
    </button>
  );
}

function NodeInspectorPanel({ nodeId, workflowMap, input, busy, err, result, onChangeInput, onRun }) {
  const toolName = nodeId.startsWith("tool:") ? nodeId.slice(5) : "";
  const node = (workflowMap?.nodes || []).find((n) => n.id === nodeId);
  const schema = node?.input_schema || node?.tool?.input_schema || null;
  const properties = (schema && schema.properties) || { prompt: { type: "string" }, product: { type: "string" }, max_rows: { type: "integer", default: 12 } };
  return (
    <section className="agent-board-detail" style={{ border: "1px solid #e2e8f0", borderRadius: 6, padding: 12, marginTop: 12, background: "#fafafa" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <strong>도구 단독 실행 — {node?.label || toolName}</strong>
        <Button variant="primary" onClick={onRun} disabled={busy || !toolName}>{busy ? "실행 중" : "단독 실행"}</Button>
      </div>
      <div style={{ fontSize: 12, color: "#64748b", marginBottom: 8 }}>{node?.detail || node?.description || "입력값을 채우고 '단독 실행' 을 누르면 이 도구만 호출합니다."}</div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
        {Object.entries(properties).map(([key, def]) => (
          <SchemaField key={key} name={key} def={def} value={input?.[key] ?? ""} onChange={(v) => onChangeInput({ ...(input || {}), [key]: v })} />
        ))}
      </div>
      {err && <Banner tone="warn" style={{ marginTop: 8 }}>{err}</Banner>}
      {result && (
        <div style={{ marginTop: 10, padding: 8, borderRadius: 4, background: result.ok ? "#dcfce7" : "#fee2e2" }}>
          <div style={{ fontSize: 12 }}><strong>{result.ok ? "성공" : "실패"}</strong> · {result.ms || 0}ms</div>
          <div style={{ fontSize: 12, marginTop: 4, whiteSpace: "pre-wrap" }}>{result.result_preview}</div>
        </div>
      )}
    </section>
  );
}

function SchemaField({ name, def, value, onChange }) {
  const type = def?.type || "string";
  const inputType = type === "integer" || type === "number" ? "number" : "text";
  const placeholder = def?.description ? String(def.description).slice(0, 80) : name;
  return (
    <Field label={name}>
      {type === "boolean" ? (
        <input type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} />
      ) : (
        <input
          type={inputType}
          value={value ?? ""}
          onChange={(e) => onChange(inputType === "number" ? Number(e.target.value || 0) : e.target.value)}
          placeholder={placeholder}
          style={inputStyle({ width: "100%" })}
        />
      )}
    </Field>
  );
}

function HomeAgentTracePanel({ plan, trace, reply, prompt }) {
  const [feedbackMsg, setFeedbackMsg] = useState("");
  const [aliasTool, setAliasTool] = useState("");
  async function sendFeedback(rating, opts = {}) {
    try {
      await postJson("/api/home-agent/feedback", {
        prompt,
        rating,
        suggested_tool: opts.tool || "",
        note: opts.note || "",
        trace_summary: trace.map((tr) => ({ tool: tr.tool, ok: tr.ok, ms: tr.ms })),
      });
      setFeedbackMsg(`피드백 저장됨 (${rating}${opts.tool ? ` · ${opts.tool}` : ""})`);
    } catch (e) {
      setFeedbackMsg("피드백 저장 실패: " + (e?.message || e));
    }
  }
  async function registerAlias() {
    if (!aliasTool.trim() || !prompt) return;
    try {
      await postJson("/api/home-agent/alias", { pattern: prompt, tool: aliasTool.trim(), note: "user-registered from trace panel" });
      setFeedbackMsg(`alias 등록: '${prompt.slice(0, 30)}' → ${aliasTool.trim()}`);
      setAliasTool("");
    } catch (e) {
      setFeedbackMsg("alias 등록 실패: " + (e?.message || e));
    }
  }
  return (
    <section className="agent-board-detail" style={{ border: "1px solid #c7d2fe", borderRadius: 6, padding: 12, marginTop: 12, background: "#eef2ff" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
        <div style={{ fontSize: 14, fontWeight: 600 }}>Home Agent trace ({trace.length} step{trace.length === 1 ? "" : "s"})</div>
        <div style={{ display: "flex", gap: 6 }}>
          <button type="button" onClick={() => sendFeedback("up")} style={feedbackBtnStyle("#16a34a")}>👍 좋아요</button>
          <button type="button" onClick={() => sendFeedback("down")} style={feedbackBtnStyle("#dc2626")}>👎 아쉬워요</button>
        </div>
      </div>
      {plan.length > 0 && (
        <div style={{ fontSize: 12, color: "#475569", marginBottom: 8 }}>
          plan: {plan.map((s) => s.tool).join(" → ")}
        </div>
      )}
      {trace.map((row, i) => (
        <div key={i} style={{ padding: 6, marginBottom: 4, background: row.ok ? "#ffffff" : "#fff7ed", borderLeft: `3px solid ${row.ok ? "#16a34a" : "#dc2626"}`, borderRadius: 3 }}>
          <div style={{ fontSize: 12 }}><strong>{row.title || row.tool}</strong> · {row.kind} · {row.ms}ms · {row.ok ? "ok" : "fail"}</div>
          <div style={{ fontSize: 12, marginTop: 2, color: "#475569" }}>{row.result_preview}</div>
          {row.reason && <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 2 }}>이유: {row.reason}</div>}
        </div>
      ))}
      {reply && <div style={{ marginTop: 8, fontSize: 13, fontWeight: 600 }}>{reply}</div>}
      <div style={{ marginTop: 10, padding: 8, background: "#ffffff", borderRadius: 4, border: "1px dashed #c7d2fe" }}>
        <div style={{ fontSize: 12, marginBottom: 4 }}><strong>이 prompt 패턴은 항상 이 도구로</strong> (다음 호출부터 적용)</div>
        <div style={{ display: "flex", gap: 6 }}>
          <input value={aliasTool} onChange={(e) => setAliasTool(e.target.value)} placeholder="tool name (예: ettime)" style={inputStyle({ flex: 1, minWidth: 120 })} />
          <Button onClick={registerAlias} disabled={!aliasTool.trim()}>alias 등록</Button>
        </div>
      </div>
      {feedbackMsg && <div style={{ marginTop: 6, fontSize: 12, color: "#475569" }}>{feedbackMsg}</div>}
    </section>
  );
}

function feedbackBtnStyle(color) {
  return {
    padding: "4px 10px",
    border: `1px solid ${color}`,
    background: "#fff",
    color,
    borderRadius: 4,
    cursor: "pointer",
    fontSize: 12,
  };
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

function buildConnectedLayout(stages, nodes) {
  const visibleStages = (stages || []).length ? stages : fallbackStages(nodes);
  const stageIndex = new Map(visibleStages.map((stage, idx) => [stage.id, idx]));
  const columns = Math.max(1, visibleStages.length);
  const grouped = new Map(visibleStages.map((stage) => [stage.id, []]));
  for (const node of nodes || []) {
    const stage = node.stage || visibleStages[0]?.id || "stage";
    if (!grouped.has(stage)) grouped.set(stage, []);
    grouped.get(stage).push(node);
  }
  for (const [stage, rows] of grouped.entries()) {
    grouped.set(stage, rows.slice().sort(workflowNodeSort));
  }
  const maxRows = Math.max(1, ...Array.from(grouped.values()).map((rows) => rows.length));
  const height = Math.max(430, maxRows * 92 + 118);
  const minWidth = Math.max(760, columns * 230);
  const items = [];
  const positions = new Map();
  const stageBands = visibleStages.map((stage, idx) => {
    const width = 1000 / columns;
    return {
      id: stage.id,
      title: stage.title || stage.label || stage.id,
      x: idx * width,
      width,
    };
  });
  for (const stage of visibleStages) {
    const idx = stageIndex.get(stage.id) ?? 0;
    const x = ((idx + 0.5) / columns) * 1000;
    const rows = grouped.get(stage.id) || [];
    rows.forEach((node, rowIdx) => {
      const y = 82 + rowIdx * 92;
      const item = { node, x, y };
      items.push(item);
      positions.set(node.id, { x, y });
    });
  }
  return { items, positions, stageBands, height, minWidth };
}

function workflowNodeSort(a, b) {
  const order = { stage: 0, workflow: 1, workflow_step: 2, tool: 3, deep_eval: 4, wiki: 5, relation: 6, column: 7, graph: 8, feature: 9, arg: 10 };
  const ai = order[a.type] ?? 20;
  const bi = order[b.type] ?? 20;
  if (ai !== bi) return ai - bi;
  return String(a.label || a.id || "").localeCompare(String(b.label || b.id || ""));
}

function edgePath(from, to) {
  const dx = Math.max(60, Math.abs(to.x - from.x) * 0.45);
  const c1 = from.x <= to.x ? from.x + dx : from.x - dx;
  const c2 = from.x <= to.x ? to.x - dx : to.x + dx;
  return `M ${from.x} ${from.y} C ${c1} ${from.y}, ${c2} ${to.y}, ${to.x} ${to.y}`;
}

const WORKFLOW_EVIDENCE_NODE_TYPES = new Set(["wiki", "relation", "column", "graph", "feature", "arg"]);

function nodeEvidenceRows(node, incoming, outgoing, byId) {
  const rows = [];
  const add = (id, label, type, nodeId = "") => {
    const cleanLabel = String(label || id || "").trim();
    if (!cleanLabel) return;
    const key = `${type}:${cleanLabel}`;
    if (rows.some((row) => row.key === key)) return;
    rows.push({ key, id, label: cleanLabel, type, nodeId });
  };
  if (WORKFLOW_EVIDENCE_NODE_TYPES.has(node.type)) {
    add(node.id, node.label || node.id, nodeTypeLabel(node.type), node.id);
  }
  for (const edge of [...incoming, ...outgoing]) {
    const otherId = edge.from === node.id ? edge.to : edge.from;
    const other = byId.get(otherId);
    if (other && WORKFLOW_EVIDENCE_NODE_TYPES.has(other.type)) {
      add(other.id, other.label || other.id, nodeTypeLabel(other.type), other.id);
    }
  }
  const refs = node.knowledge_refs && typeof node.knowledge_refs === "object" ? node.knowledge_refs : {};
  for (const [type, values] of Object.entries(refs)) {
    const list = Array.isArray(values) ? values : values ? [values] : [];
    for (const value of list.slice(0, 6)) add(String(value), String(value), type);
  }
  return rows;
}

function nodeMetricRows(node) {
  const metrics = node?.metrics && typeof node.metrics === "object" ? node.metrics : {};
  const candidates = [
    ["calls", metrics.count],
    ["users", metrics.users],
    ["steps", metrics.steps],
    ["runs", metrics.run_count],
    ["warnings", metrics.warning_count],
    ["failed", metrics.failed],
  ];
  return candidates
    .filter(([, value]) => value !== undefined && value !== null && value !== "")
    .slice(0, 4)
    .map(([label, value]) => ({ label, value }));
}

function nodeNextAction(node, evidence) {
  if (!node) return "노드를 선택해 개선 지점을 확인합니다.";
  if (node.type === "tool" && node.enabled === false) return "도구 카탈로그에서 enabled 상태와 guardrail을 확인하고, workflow step이 이 도구에 의존하는지 점검합니다.";
  if (node.type === "tool" && !evidence.length) return "Wiki source/page 또는 schema relation 근거를 연결해 운영자가 실행 이유를 추적할 수 있게 합니다.";
  if (node.type === "workflow") return "Runbook에서 dry-run으로 step 순서와 missing tool을 검증한 뒤 shared template 승격 여부를 결정합니다.";
  if (node.type === "workflow_step") return "step의 unit_ai/action 바인딩과 required slot을 질문/워크플로우 섹션에서 확인합니다.";
  if (node.type === "deep_eval") return "실패 case가 있으면 deep-eval 리포트를 재생성하고, 회귀한 workflow나 Wiki 근거를 우선 보강합니다.";
  if (WORKFLOW_EVIDENCE_NODE_TYPES.has(node.type)) return "Wiki 근거 섹션에서 source/page/lint 상태를 확인하고 끊긴 graph 관계를 보강합니다.";
  return "입력/출력 엣지를 따라 끊긴 stage를 찾고, 필요한 Wiki/source 또는 workflow template 보강으로 연결합니다.";
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
