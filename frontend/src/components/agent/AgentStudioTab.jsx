import { useEffect, useMemo, useState } from "react";
import { dl, qs, sf } from "../../lib/api";
import { Banner, Button, EmptyState, Field, Pill } from "../UXKit";
import Loading from "../Loading";

export default function AgentStudioTab({ user }) {
  const isAdmin = user?.role === "admin";
  const [days, setDays] = useState(30);
  const [query, setQuery] = useState("");
  const [nodeQuery, setNodeQuery] = useState("");
  const [questions, setQuestions] = useState([]);
  const [workflowMap, setWorkflowMap] = useState(null);
  const [wikiHealth, setWikiHealth] = useState(null);
  const [wikiGraph, setWikiGraph] = useState({ nodes: [], links: [] });
  const [selectedQuestionId, setSelectedQuestionId] = useState("");
  const [selectedNodeId, setSelectedNodeId] = useState("");
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState("");
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

  async function exportWorkflow(format) {
    setExporting(format);
    setErr("");
    try {
      const params = { format: format === "obsidian_zip" ? "obsidian" : "n8n", days, limit: 40, reference_limit: 160 };
      if (format === "obsidian_zip") {
        await dl("/api/ai-hub/workflow-map/export/download" + qs(params), "flow-ai-hub-workflow-map.obsidian.zip");
        return;
      }
      const out = await sf("/api/ai-hub/workflow-map/export" + qs(params));
      downloadJson(out.filename || "flow-ai-hub-workflow-map.n8n.json", out);
    } catch (e) {
      setErr(e?.message || "export 실패");
    } finally {
      setExporting("");
    }
  }

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
    <div style={{ minHeight: "calc(100vh - 190px)", display: "grid", gridTemplateRows: "auto minmax(560px, 1fr)", gap: 10 }}>
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
          <Button onClick={() => exportWorkflow("n8n")} disabled={!!exporting}>{exporting === "n8n" ? "준비 중" : "n8n JSON"}</Button>
          <Button onClick={() => exportWorkflow("obsidian_zip")} disabled={!!exporting}>{exporting === "obsidian_zip" ? "준비 중" : "Obsidian ZIP"}</Button>
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
            <div style={{ display: "flex", flexDirection: "column", gap: 6, overflow: "auto", minHeight: 0 }}>
              {filteredQuestions.map((row) => (
                <QuestionButton key={row.id} row={row} selected={selectedQuestionId === row.id} onClick={() => setSelectedQuestionId(row.id)} />
              ))}
            </div>
          ) : (
            <EmptyState title="질문 이력 없음" />
          )}
        </aside>

        <main style={canvasPaneStyle}>
          <div style={{ display: "flex", alignItems: "end", gap: 8, marginBottom: 8, flexWrap: "wrap" }}>
            <PaneTitle title="워크플로우 캔버스" meta={`${mapNodes.length} nodes · ${mapEdges.length} edges`} />
            <div style={{ flex: 1 }} />
            <input value={nodeQuery} onChange={(e) => setNodeQuery(e.target.value)} placeholder="노드 검색" style={inputStyle({ width: 220 })} />
          </div>
          {loading ? (
            <Loading text="워크플로우 로딩..." size="md" />
          ) : workflowMap ? (
            <WorkflowCanvas stages={stages} nodes={visibleNodes} selectedId={selectedNodeId} onSelect={setSelectedNodeId} />
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
      border: `1px solid ${selected ? "var(--accent)" : "var(--border)"}`,
      background: selected ? "var(--accent-glow)" : "var(--bg-primary)",
      color: "var(--text-primary)",
      borderRadius: 5,
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
    <div style={{ display: "grid", gridTemplateColumns: `repeat(${Math.max(1, visibleStages.length)}, minmax(170px, 1fr))`, gap: 8, overflowX: "auto", minHeight: 480 }}>
      {visibleStages.map((stage) => {
        const stageNodes = nodes.filter((node) => node.stage === stage.id && node.type !== "stage");
        return (
          <div key={stage.id} style={{ border: "1px solid var(--border)", background: "var(--bg-secondary)", borderRadius: 6, padding: 8, minWidth: 170 }}>
            <div style={{ fontSize: 12, fontWeight: 900, color: "var(--text-primary)", marginBottom: 3 }}>{stage.title || stage.label || stage.id}</div>
            <div style={{ fontSize: 10, color: "var(--text-secondary)", lineHeight: 1.35, minHeight: 30, marginBottom: 8 }}>{stage.detail || stage.id}</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6, maxHeight: 430, overflowY: "auto" }}>
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
      border: `1px solid ${selected ? "var(--accent)" : "var(--border)"}`,
      background: selected ? "var(--accent-glow)" : "var(--bg-primary)",
      color: "var(--text-primary)",
      borderRadius: 5,
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

function DetailPanel({ question, node, edges, nodes, wikiNode, wikiHealth, wikiGraph, onNodeSelect }) {
  const byId = new Map(nodes.map((item) => [item.id, item]));
  const incoming = node ? edges.filter((edge) => edge.to === node.id) : [];
  const outgoing = node ? edges.filter((edge) => edge.from === node.id) : [];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10, overflow: "auto" }}>
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

function downloadJson(filename, payload) {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
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
  gap: 10,
  minHeight: 0,
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
};

const canvasPaneStyle = {
  border: "0",
  borderRight: "1px solid var(--border)",
  background: "var(--bg-primary)",
  padding: 10,
  minWidth: 0,
  minHeight: 0,
  overflow: "hidden",
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

const inputStyle = (extra = {}) => ({
  padding: "6px 8px",
  borderRadius: 4,
  border: "1px solid var(--border)",
  background: "var(--bg-primary)",
  color: "var(--text-primary)",
  fontSize: 13,
  ...extra,
});
