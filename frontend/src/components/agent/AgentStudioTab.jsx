import { useEffect, useMemo, useState } from "react";
import { qs, sf } from "../../lib/api";
import { Banner, Button, EmptyState, Field, Pill } from "../UXKit";
import Loading from "../Loading";

const STUDIO_VIEWS = [
  { key: "workflow", label: "전체 워크플로우" },
  { key: "agent", label: "에이전트 동작" },
  { key: "wiki", label: "Wiki 관계" },
];

export default function AgentStudioTab({ user }) {
  const isAdmin = user?.role === "admin";
  const [days, setDays] = useState(30);
  const [query, setQuery] = useState("");
  const [nodeQuery, setNodeQuery] = useState("");
  const [questions, setQuestions] = useState([]);
  const [workflowMap, setWorkflowMap] = useState(null);
  const [wikiHealth, setWikiHealth] = useState(null);
  const [wikiGraph, setWikiGraph] = useState({ nodes: [], links: [] });
  const [view, setView] = useState("workflow");
  const [selectedQuestionId, setSelectedQuestionId] = useState("");
  const [selectedNodeId, setSelectedNodeId] = useState("");
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");

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
      const historyRows = historyResult.status === "fulfilled" ? (historyResult.value.rows || []) : [];
      const nextMap = mapResult.status === "fulfilled" ? mapResult.value : null;
      setQuestions(historyRows);
      setWorkflowMap(nextMap);
      setWikiHealth(healthResult.status === "fulfilled" ? healthResult.value : null);
      setWikiGraph(graphResult.status === "fulfilled" ? normalizeWikiGraph(graphResult.value) : { nodes: [], links: [] });
      setSelectedQuestionId((cur) => historyRows.find((row) => row.id === cur)?.id || historyRows[0]?.id || "");
      setSelectedNodeId((cur) => (nextMap?.nodes || []).find((node) => node.id === cur)?.id || (nextMap?.nodes || []).find((node) => node.type !== "stage")?.id || "");
      const failures = [historyResult, mapResult, healthResult, graphResult].filter((res) => res.status === "rejected");
      if (failures.length) setErr(`${failures.length}개 운영 소스를 불러오지 못했습니다. 가능한 데이터만 표시합니다.`);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { reload(); }, [days, isAdmin]);

  const filteredQuestions = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return questions;
    return questions.filter((row) => JSON.stringify(row).toLowerCase().includes(needle));
  }, [questions, query]);
  const selectedQuestion = questions.find((row) => row.id === selectedQuestionId) || null;

  const mapNodes = workflowMap?.nodes || [];
  const mapEdges = workflowMap?.edges || [];
  const stages = workflowMap?.stages || [];
  const nodeNeedle = nodeQuery.trim().toLowerCase();
  const visibleNodes = useMemo(() => {
    if (!nodeNeedle) return mapNodes;
    return mapNodes.filter((node) => workflowNodeSearchText(node).includes(nodeNeedle) || node.type === "stage");
  }, [mapNodes, nodeNeedle]);
  const selectedNode = mapNodes.find((node) => node.id === selectedNodeId) || null;
  const selectedWikiNode = useMemo(() => {
    if (!selectedNode) return null;
    const candidates = [selectedNode.id, selectedNode.doc_id, selectedNode.ref_id, String(selectedNode.id || "").replace(/^wiki:/, "").replace(/^doc:/, "")].filter(Boolean);
    return wikiGraph.nodes.find((node) => candidates.includes(node.id) || candidates.includes(node.doc_id));
  }, [selectedNode, wikiGraph.nodes]);

  return (
    <div style={{ height: "100%", minHeight: 0, display: "grid", gridTemplateRows: err ? "auto auto minmax(0, 1fr)" : "auto minmax(0, 1fr)", gap: 0, overflow: "hidden" }}>
      <section style={toolbarStyle}>
        <div style={{ minWidth: 220 }}>
          <div style={{ fontSize: 16, fontWeight: 900, color: "var(--text-primary)" }}>Agent Studio</div>
          <div style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 2 }}>질문 큐, 워크플로우, Wiki 근거, 개선 조치를 한 화면에서 관리합니다.</div>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "end", flexWrap: "wrap", marginLeft: "auto" }}>
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

      {err && <Banner tone="warn">{err}</Banner>}

      <div style={studioGridStyle}>
        <aside style={paneStyle}>
          <PaneTitle title="질문 큐" meta={`${filteredQuestions.length}/${questions.length}`} />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="질문/사용자/action 검색" style={inputStyle({ width: "100%", marginBottom: 8 })} />
          {loading ? (
            <Loading text="질문 로딩..." size="sm" />
          ) : filteredQuestions.length ? (
            <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", gap: 0, overflow: "auto" }}>
              {filteredQuestions.map((row) => (
                <QuestionButton key={row.id} row={row} selected={selectedQuestionId === row.id} onClick={() => setSelectedQuestionId(row.id)} />
              ))}
            </div>
          ) : (
            <EmptyState title="질문 이력 없음" />
          )}
        </aside>

        <main style={canvasPaneStyle}>
          <div style={{ flexShrink: 0, display: "flex", alignItems: "end", gap: 8, marginBottom: 8, flexWrap: "wrap" }}>
            <PaneTitle title={viewTitle(view)} meta={`${mapNodes.length} nodes · ${mapEdges.length} edges`} />
            <ViewSwitch value={view} onChange={setView} />
            <div style={{ flex: 1 }} />
            <input value={nodeQuery} onChange={(e) => setNodeQuery(e.target.value)} placeholder="노드/Wiki 검색" style={inputStyle({ width: 220 })} />
          </div>
          {loading ? (
            <Loading text="워크플로우 로딩..." size="md" />
          ) : workflowMap && view === "workflow" ? (
            <WorkflowCanvas stages={stages} nodes={visibleNodes} selectedId={selectedNodeId} onSelect={setSelectedNodeId} />
          ) : workflowMap && view === "agent" ? (
            <AgentOperationPanel workflowMap={workflowMap} question={selectedQuestion} nodeNeedle={nodeNeedle} selectedId={selectedNodeId} onSelect={setSelectedNodeId} />
          ) : workflowMap && view === "wiki" ? (
            <WikiRelationPanel workflowMap={workflowMap} wikiGraph={wikiGraph} nodeNeedle={nodeNeedle} selectedNode={selectedNode} onSelect={setSelectedNodeId} />
          ) : (
            <EmptyState title="워크플로우 지도 없음" hint="AI Hub workflow-map API 상태를 확인하세요." />
          )}
        </main>

        <aside style={paneStyle}>
          <PaneTitle title="Wiki / 개선 상세" meta={wikiHealth ? `Wiki ${wikiHealth.counts?.docs || 0}` : ""} />
          <DetailPanel
            question={selectedQuestion}
            node={selectedNode}
            edges={mapEdges}
            nodes={mapNodes}
            wikiNode={selectedWikiNode}
            wikiHealth={wikiHealth}
            wikiGraph={wikiGraph}
            onNodeSelect={setSelectedNodeId}
          />
        </aside>
      </div>
    </div>
  );
}

function QuestionButton({ row, selected, onClick }) {
  const admin = row.actor_type === "admin";
  return (
    <button type="button" onClick={onClick} style={{
      textAlign: "left",
      border: "0",
      borderBottom: "1px solid var(--border)",
      borderLeft: selected ? "3px solid var(--accent)" : "3px solid transparent",
      background: selected ? "var(--accent-glow)" : "var(--bg-primary)",
      color: "var(--text-primary)",
      borderRadius: 0,
      padding: 8,
      cursor: "pointer",
      minWidth: 0,
    }}>
      <div style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 4 }}>
        <Pill tone={admin ? "accent" : "info"}>{admin ? "admin" : "user"}</Pill>
        <span style={{ fontSize: 11, color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row.user || "-"} · {shortTime(row.timestamp)}</span>
      </div>
      <div style={{ fontSize: 12, fontWeight: 800, lineHeight: 1.35, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{row.prompt || "-"}</div>
      <div style={{ marginTop: 4, fontSize: 11, color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row.action || row.intent || row.feature || "action 없음"}</div>
    </button>
  );
}

function WorkflowCanvas({ stages, nodes, selectedId, onSelect }) {
  const visibleStages = stages.length ? stages : fallbackStages(nodes);
  return (
    <div style={{ height: "100%", minHeight: 0, display: "grid", gridTemplateColumns: `repeat(${Math.max(1, visibleStages.length)}, minmax(170px, 1fr))`, gap: 0, overflowX: "auto" }}>
      {visibleStages.map((stage) => {
        const stageNodes = nodes.filter((node) => node.stage === stage.id && node.type !== "stage");
        return (
          <div key={stage.id} style={{ borderRight: "1px solid var(--border)", background: "var(--bg-primary)", padding: 8, minWidth: 170, minHeight: 0, display: "flex", flexDirection: "column" }}>
            <div style={{ flexShrink: 0, fontSize: 12, fontWeight: 900, color: "var(--text-primary)", marginBottom: 3 }}>{stage.title || stage.label || stage.id}</div>
            <div style={{ flexShrink: 0, fontSize: 10, color: "var(--text-secondary)", lineHeight: 1.35, minHeight: 30, marginBottom: 8 }}>{stage.detail || stage.id}</div>
            <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", gap: 0, overflowY: "auto" }}>
              {stageNodes.map((node) => <WorkflowNode key={node.id} node={node} selected={selectedId === node.id} onSelect={onSelect} />)}
              {!stageNodes.length && <div style={{ fontSize: 11, color: "var(--text-secondary)", padding: "8px 0" }}>연결 노드 없음</div>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function WorkflowNode({ node, selected, onSelect }) {
  return (
    <button type="button" onClick={() => onSelect(node.id)} style={{
      textAlign: "left",
      border: "0",
      borderBottom: "1px solid var(--border)",
      borderLeft: selected ? "3px solid var(--accent)" : "3px solid transparent",
      background: selected ? "var(--accent-glow)" : "var(--bg-primary)",
      color: "var(--text-primary)",
      borderRadius: 0,
      padding: 7,
      cursor: "pointer",
      opacity: node.enabled === false ? 0.62 : 1,
      minWidth: 0,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 5 }}>
        <span style={{ fontSize: 11, fontWeight: 850, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{node.label || node.id}</span>
        <span style={{ fontSize: 9, fontWeight: 900, color: toneColor(node.tone), textTransform: "uppercase" }}>{node.type}</span>
      </div>
      <div style={{ marginTop: 4, fontSize: 10, color: "var(--text-secondary)", lineHeight: 1.3, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{node.detail || node.id}</div>
    </button>
  );
}

function ViewSwitch({ value, onChange }) {
  return (
    <div style={viewSwitchStyle}>
      {STUDIO_VIEWS.map((item) => (
        <button
          key={item.key}
          type="button"
          onClick={() => onChange(item.key)}
          style={viewSwitchButtonStyle(value === item.key)}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}

function AgentOperationPanel({ workflowMap, question, nodeNeedle, selectedId, onSelect }) {
  const nodes = workflowMap?.nodes || [];
  const stages = workflowMap?.stages || [];
  const counts = workflowMap?.counts || {};
  const warnings = Array.isArray(workflowMap?.warnings) ? workflowMap.warnings : [];
  const filtered = (items) => {
    if (!nodeNeedle) return items;
    return items.filter((node) => workflowNodeSearchText(node).includes(nodeNeedle));
  };
  const workflowNodes = filtered(nodes.filter((node) => node.type === "workflow"));
  const toolNodes = filtered(nodes.filter((node) => node.type === "tool"));

  return (
    <div style={operationPanelStyle}>
      <div style={metricStripStyle}>
        <MiniStat label="workflow" value={counts.workflow_templates_visible || workflowNodes.length} />
        <MiniStat label="tools" value={counts.tools_visible || toolNodes.length} />
        <MiniStat label="recent runs" value={counts.workflow_runs_recent || 0} />
        <MiniStat label="warnings" value={(warnings || []).length + Number(counts.workflow_run_warnings || 0)} />
      </div>

      {question && (
        <section style={operationSectionStyle}>
          <SectionTitle title="질문 라우팅" meta={question.status || "history"} />
          <DetailLine label="question" value={question.prompt || "-"} strong />
          <DetailLine label="semantic/action" value={[question.feature, question.intent, question.action].filter(Boolean).join(" / ") || "-"} />
          <DetailLine label="result" value={question.answer_excerpt || questionImproveText(question)} />
        </section>
      )}

      <div style={operationFlowStyle}>
        {stages.map((stage) => {
          const stageNodes = filtered(nodes.filter((node) => node.stage === stage.id && node.type !== "stage"));
          return (
            <section key={stage.id} style={operationStageStyle}>
              <div style={{ fontSize: 12, fontWeight: 900, color: "var(--text-primary)" }}>{stage.title || stage.id}</div>
              <div style={{ marginTop: 2, fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.35 }}>{stage.detail || stage.id}</div>
              <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 0 }}>
                {stageNodes.slice(0, 10).map((node) => (
                  <StageNodeButton key={node.id} node={node} selected={selectedId === node.id} onSelect={onSelect} />
                ))}
                {stageNodes.length > 10 && <div style={moreRowStyle}>+{stageNodes.length - 10} more</div>}
                {!stageNodes.length && <div style={emptyRowStyle}>노드 없음</div>}
              </div>
            </section>
          );
        })}
      </div>

      {!!warnings.length && (
        <section style={operationSectionStyle}>
          <SectionTitle title="운영 경고" meta={`${warnings.length}`} />
          {warnings.slice(0, 6).map((warning, idx) => (
            <div key={`${warning.key || warning.message || idx}`} style={warningRowStyle}>
              <span style={{ fontWeight: 850, color: "var(--warn)" }}>{warning.title || warning.key || "warning"}</span>
              <span>{warning.message || warning.action || "-"}</span>
            </div>
          ))}
        </section>
      )}
    </div>
  );
}

function StageNodeButton({ node, selected, onSelect }) {
  const metrics = node.metrics && typeof node.metrics === "object" ? node.metrics : {};
  const metricText = [
    metrics.steps ? `${metrics.steps} steps` : "",
    metrics.count ? `${metrics.count} runs` : "",
    metrics.warning_count ? `${metrics.warning_count} warn` : "",
  ].filter(Boolean).join(" · ");
  return (
    <button type="button" onClick={() => onSelect(node.id)} style={operationNodeButtonStyle(selected)}>
      <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{node.label || node.id}</span>
      <span style={{ color: toneColor(node.tone), fontWeight: 900 }}>{nodeTypeLabel(node.type)}</span>
      {metricText && <span style={{ gridColumn: "1 / -1", color: "var(--text-secondary)", fontSize: 10 }}>{metricText}</span>}
    </button>
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
    <div style={wikiPanelStyle}>
      <section style={wikiColumnStyle}>
        <SectionTitle title="워크플로우 근거" meta={`${evidenceNodes.length}`} />
        <div style={relationListStyle}>
          {evidenceNodes.slice(0, 80).map((node) => (
            <button key={node.id} type="button" onClick={() => onSelect(node.id)} style={relationRowButtonStyle(selectedNode?.id === node.id)}>
              <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{node.label || node.id}</span>
              <span style={{ color: "var(--text-secondary)", fontSize: 10 }}>{nodeTypeLabel(node.type)}</span>
            </button>
          ))}
          {!evidenceNodes.length && <div style={emptyRowStyle}>근거 노드 없음</div>}
        </div>
      </section>

      <section style={wikiColumnStyle}>
        <SectionTitle title="Wiki 노드" meta={`${graphNodes.length}`} />
        <div style={relationListStyle}>
          {graphNodes.slice(0, 100).map((node) => {
            const active = selectedKey && [node.id, node.doc_id, node.label].filter(Boolean).includes(selectedKey);
            return (
              <div key={node.id} style={relationRowStyle(active)}>
                <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{node.label || node.id}</span>
                <span style={{ color: "var(--text-secondary)", fontSize: 10 }}>{node.kind || "node"} · {degree.get(node.id) || 0}</span>
              </div>
            );
          })}
          {!graphNodes.length && <div style={emptyRowStyle}>Wiki 노드 없음</div>}
        </div>
      </section>

      <section style={wikiColumnStyle}>
        <SectionTitle title="관계 링크" meta={`${graphLinks.length}`} />
        <div style={relationListStyle}>
          {graphLinks.slice(0, 120).map((edge) => (
            <div key={edge.id || `${edge.source}:${edge.target}`} style={relationEdgeStyle}>
              <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{edge.source}</span>
              <span style={{ color: "var(--accent)", fontWeight: 900 }}>{edge.label || "link"}</span>
              <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{edge.target}</span>
            </div>
          ))}
          {!graphLinks.length && <div style={emptyRowStyle}>관계 링크 없음</div>}
        </div>
      </section>
    </div>
  );
}

function DetailPanel({ question, node, edges, nodes, wikiNode, wikiHealth, wikiGraph, onNodeSelect }) {
  const byId = new Map(nodes.map((item) => [item.id, item]));
  const incoming = node ? edges.filter((edge) => edge.to === node.id) : [];
  const outgoing = node ? edges.filter((edge) => edge.from === node.id) : [];
  return (
    <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", gap: 0, overflow: "auto" }}>
      {question && (
        <section style={detailSectionStyle}>
          <SectionTitle title="선택 질문" />
          <DetailLine label="actor" value={`${question.user || "-"} · ${question.actor_type || "user"}`} />
          <DetailLine label="status" value={question.status || "done"} />
          <DetailLine label="question" value={question.prompt || "-"} strong />
          <DetailLine label="worked" value={[question.feature, question.intent, question.action].filter(Boolean).join(" / ") || "-"} />
          <DetailLine label="improve" value={questionImproveText(question)} />
        </section>
      )}

      {node ? (
        <section style={detailSectionStyle}>
          <SectionTitle title="선택 노드" meta={node.type} />
          <DetailLine label="id" value={node.id} mono />
          <DetailLine label="detail" value={node.detail || "-"} />
          <EdgeList title="입력" edges={incoming} other={(edge) => byId.get(edge.from)} onSelect={onNodeSelect} />
          <EdgeList title="출력" edges={outgoing} other={(edge) => byId.get(edge.to)} onSelect={onNodeSelect} />
        </section>
      ) : (
        <section style={detailSectionStyle}><EmptyState title="노드 선택 없음" /></section>
      )}

      <section style={detailSectionStyle}>
        <SectionTitle title="Obsidian Wiki" meta={`${wikiGraph.nodes.length} nodes · ${wikiGraph.links.length} links`} />
        {wikiNode ? (
          <>
            <DetailLine label="node" value={wikiNode.label || wikiNode.id} strong />
            <DetailLine label="kind" value={wikiNode.kind || "-"} />
          </>
        ) : (
          <DetailLine label="node" value="워크플로우의 Wiki/schema 노드를 선택하면 연결 근거를 표시합니다." />
        )}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginTop: 8 }}>
          <MiniStat label="docs" value={wikiHealth?.counts?.docs || 0} />
          <MiniStat label="sources" value={wikiHealth?.counts?.sources || 0} />
          <MiniStat label="graph" value={wikiHealth?.counts?.graph_nodes || wikiGraph.nodes.length || 0} />
          <MiniStat label="lint" value={wikiHealth?.counts?.lint_issues || 0} />
        </div>
      </section>

      <section style={detailSectionStyle}>
        <SectionTitle title="개선 루프" />
        {["질문 이력에서 blocked/missing/반복 질문 확인", "워크플로우 캔버스에서 tool, policy, Wiki/schema 근거 확인", "Wiki source/page 또는 semantic alias를 승인형으로 보강", "Runbook dry-run과 deep-eval로 회귀 확인"].map((text, idx) => (
          <div key={text} style={{ display: "grid", gridTemplateColumns: "22px 1fr", gap: 6, alignItems: "start", marginTop: idx ? 6 : 0 }}>
            <span style={{ border: "1px solid var(--border)", borderRadius: 4, textAlign: "center", fontSize: 11, fontWeight: 900, color: "var(--accent)" }}>{idx + 1}</span>
            <span style={{ fontSize: 12, color: "var(--text-primary)", lineHeight: 1.45 }}>{text}</span>
          </div>
        ))}
      </section>
    </div>
  );
}

function EdgeList({ title, edges, other, onSelect }) {
  if (!edges.length) return null;
  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ fontSize: 11, fontWeight: 850, color: "var(--text-secondary)", marginBottom: 4 }}>{title}</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {edges.slice(0, 8).map((edge, idx) => {
          const node = other(edge);
          return (
            <button key={`${edge.from}:${edge.to}:${idx}`} type="button" onClick={() => node && onSelect(node.id)} style={edgeButtonStyle}>
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{node?.label || node?.id || edge.from || edge.to}</span>
              <span style={{ color: "var(--text-secondary)" }}>{edge.label || edge.type || ""}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function PaneTitle({ title, meta }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, minHeight: 28 }}>
      <div style={{ fontSize: 13, fontWeight: 900, color: "var(--text-primary)" }}>{title}</div>
      {meta && <Pill tone="neutral">{meta}</Pill>}
    </div>
  );
}

function SectionTitle({ title, meta }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 8 }}>
      <div style={{ fontSize: 12, fontWeight: 900, color: "var(--text-primary)" }}>{title}</div>
      {meta && <Pill tone="neutral">{meta}</Pill>}
    </div>
  );
}

function DetailLine({ label, value, strong, mono }) {
  return (
    <div style={{ marginBottom: 7 }}>
      <div style={{ fontSize: 10, fontWeight: 850, color: "var(--text-secondary)", marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: strong ? 13 : 12, fontWeight: strong ? 850 : 500, color: "var(--text-primary)", lineHeight: 1.45, fontFamily: mono ? "JetBrains Mono, monospace" : "inherit", wordBreak: "break-word" }}>{value || "-"}</div>
    </div>
  );
}

function MiniStat({ label, value }) {
  return (
    <div style={{ border: "1px solid var(--border)", background: "var(--bg-primary)", borderRadius: 4, padding: 6 }}>
      <div style={{ fontSize: 10, color: "var(--text-secondary)" }}>{label}</div>
      <div style={{ fontSize: 16, fontWeight: 900, color: "var(--text-primary)" }}>{Number(value || 0)}</div>
    </div>
  );
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

function questionImproveText(question) {
  const missing = Array.isArray(question?.missing) ? question.missing.filter(Boolean) : [];
  if (missing.length) return `missing slot: ${missing.join(", ")}`;
  const status = String(question?.status || "").toLowerCase();
  if (["blocked", "missing", "failed", "error"].includes(status)) return "실패 상태를 기준으로 Wiki/source/workflow 보강 후보";
  return "반복 질문이면 workflow template 또는 Wiki 근거로 승격";
}

function viewTitle(view) {
  return STUDIO_VIEWS.find((item) => item.key === view)?.label || "전체 워크플로우";
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

const toolbarStyle = {
  border: "0",
  borderBottom: "1px solid var(--border)",
  background: "var(--bg-secondary)",
  padding: 12,
  display: "flex",
  gap: 12,
  alignItems: "center",
  flexWrap: "wrap",
};

const studioGridStyle = {
  display: "grid",
  gridTemplateColumns: "280px minmax(460px, 1fr) 320px",
  gap: 0,
  height: "100%",
  minHeight: 0,
  overflow: "hidden",
};

const paneStyle = {
  border: "0",
  borderRight: "1px solid var(--border)",
  background: "var(--bg-secondary)",
  padding: 10,
  minWidth: 0,
  minHeight: 0,
  display: "flex",
  flexDirection: "column",
  overflow: "hidden",
};

const canvasPaneStyle = {
  border: "0",
  borderRight: "1px solid var(--border)",
  background: "var(--bg-primary)",
  padding: 10,
  minWidth: 0,
  minHeight: 0,
  overflow: "hidden",
  display: "flex",
  flexDirection: "column",
};

const detailSectionStyle = {
  border: "0",
  borderBottom: "1px solid var(--border)",
  background: "var(--bg-primary)",
  borderRadius: 0,
  padding: 9,
};

const edgeButtonStyle = {
  display: "grid",
  gridTemplateColumns: "minmax(0, 1fr) auto",
  gap: 5,
  alignItems: "center",
  textAlign: "left",
  border: "1px solid var(--border)",
  background: "var(--bg-secondary)",
  color: "var(--text-primary)",
  borderRadius: 4,
  padding: "4px 6px",
  cursor: "pointer",
  fontSize: 11,
};

const viewSwitchStyle = {
  display: "inline-flex",
  border: "1px solid var(--border)",
  borderRadius: 4,
  overflow: "hidden",
  background: "var(--bg-secondary)",
};

const viewSwitchButtonStyle = (active) => ({
  border: "0",
  borderRight: "1px solid var(--border)",
  background: active ? "var(--accent)" : "transparent",
  color: active ? "#fff" : "var(--text-primary)",
  padding: "6px 9px",
  fontSize: 12,
  fontWeight: 850,
  cursor: "pointer",
});

const operationPanelStyle = {
  flex: 1,
  minHeight: 0,
  overflow: "auto",
  display: "flex",
  flexDirection: "column",
  gap: 0,
  borderTop: "1px solid var(--border)",
};

const metricStripStyle = {
  display: "grid",
  gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
  gap: 6,
  padding: "8px 0",
  borderBottom: "1px solid var(--border)",
};

const operationSectionStyle = {
  borderBottom: "1px solid var(--border)",
  padding: "10px 0",
};

const operationFlowStyle = {
  display: "grid",
  gridTemplateColumns: "repeat(5, minmax(150px, 1fr))",
  gap: 0,
  minHeight: 260,
  overflowX: "auto",
  borderBottom: "1px solid var(--border)",
};

const operationStageStyle = {
  borderRight: "1px solid var(--border)",
  padding: 8,
  minWidth: 150,
};

const operationNodeButtonStyle = (selected) => ({
  width: "100%",
  display: "grid",
  gridTemplateColumns: "minmax(0, 1fr) auto",
  gap: 6,
  alignItems: "center",
  textAlign: "left",
  border: "0",
  borderBottom: "1px solid var(--border)",
  borderLeft: selected ? "3px solid var(--accent)" : "3px solid transparent",
  background: selected ? "var(--accent-glow)" : "transparent",
  color: "var(--text-primary)",
  padding: "7px 6px",
  cursor: "pointer",
  fontSize: 11,
});

const warningRowStyle = {
  display: "grid",
  gridTemplateColumns: "150px minmax(0, 1fr)",
  gap: 8,
  padding: "6px 0",
  borderTop: "1px solid var(--border)",
  fontSize: 12,
  color: "var(--text-primary)",
};

const wikiPanelStyle = {
  flex: 1,
  minHeight: 0,
  overflow: "hidden",
  display: "grid",
  gridTemplateColumns: "minmax(190px, 0.9fr) minmax(210px, 1fr) minmax(240px, 1.1fr)",
  borderTop: "1px solid var(--border)",
};

const wikiColumnStyle = {
  minHeight: 0,
  minWidth: 0,
  overflow: "hidden",
  borderRight: "1px solid var(--border)",
  padding: 8,
  display: "flex",
  flexDirection: "column",
};

const relationListStyle = {
  flex: 1,
  minHeight: 0,
  overflow: "auto",
  display: "flex",
  flexDirection: "column",
  gap: 0,
};

const relationRowButtonStyle = (selected) => ({
  width: "100%",
  display: "grid",
  gridTemplateColumns: "minmax(0, 1fr) auto",
  gap: 6,
  alignItems: "center",
  textAlign: "left",
  border: "0",
  borderBottom: "1px solid var(--border)",
  borderLeft: selected ? "3px solid var(--accent)" : "3px solid transparent",
  background: selected ? "var(--accent-glow)" : "transparent",
  color: "var(--text-primary)",
  padding: "7px 6px",
  cursor: "pointer",
  fontSize: 11,
});

const relationRowStyle = (active) => ({
  display: "grid",
  gridTemplateColumns: "minmax(0, 1fr) auto",
  gap: 6,
  alignItems: "center",
  borderBottom: "1px solid var(--border)",
  borderLeft: active ? "3px solid var(--accent)" : "3px solid transparent",
  background: active ? "var(--accent-glow)" : "transparent",
  padding: "7px 6px",
  fontSize: 11,
  color: "var(--text-primary)",
});

const relationEdgeStyle = {
  display: "grid",
  gridTemplateColumns: "minmax(0, 1fr) auto minmax(0, 1fr)",
  gap: 6,
  alignItems: "center",
  borderBottom: "1px solid var(--border)",
  padding: "7px 6px",
  fontSize: 11,
  color: "var(--text-primary)",
};

const moreRowStyle = {
  padding: "7px 6px",
  borderBottom: "1px solid var(--border)",
  fontSize: 11,
  color: "var(--text-secondary)",
};

const emptyRowStyle = {
  padding: "8px 0",
  fontSize: 11,
  color: "var(--text-secondary)",
};

const inputStyle = (extra = {}) => ({
  padding: "6px 8px",
  borderRadius: 4,
  border: "1px solid var(--border)",
  background: "var(--bg-primary)",
  color: "var(--text-primary)",
  fontSize: 13,
  ...extra,
});
