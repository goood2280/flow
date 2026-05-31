import { useEffect, useMemo, useState } from "react";
import dagre from "dagre";
import { PageHeader, PageShell, Panel, Banner, Button, Field, Pill, Select, TabStrip, Textarea } from "../components/UXKit";
import LlmTab from "../components/agent/LlmTab";
import { postJson, putJson, sf } from "../lib/api";

const AGENT_UNIT_CATALOG_ENDPOINT = "/api/agent/catalog";
const SEMANTIC_LEXICON_ENDPOINT = "/api/agent/semantic/lexicon";
const SEMANTIC_SOURCES_ENDPOINT = "/api/agent/semantic/sources";
const SEMANTIC_MEASUREMENTS_ENDPOINT = "/api/agent/semantic/measurements";
const SEMANTIC_PROPOSALS_ENDPOINT = "/api/agent/semantic/proposals?status=pending&limit=100";
const EMPTY_GRAPH = { nodes: [], edges: [], state_design: {} };

function agentUnitGraphEndpoint(unitKey) {
  return `/api/agent/unit/${encodeURIComponent(unitKey)}/graph`;
}

function agentUnitRunEndpoint(unitKey) {
  return `/api/agent/unit/${encodeURIComponent(unitKey)}/run`;
}

function agentUnitHistoryEndpoint(unitKey, limit = 50) {
  return `/api/agent/unit/${encodeURIComponent(unitKey)}/history?limit=${encodeURIComponent(String(limit))}`;
}

function agentUnitFeedbackProfileEndpoint(unitKey) {
  return `/api/agent/unit-ai/${encodeURIComponent(unitKey)}/feedback-profile`;
}

function agentUnitFeedbackEndpoint(unitKey) {
  return `/api/agent/unit-ai/${encodeURIComponent(unitKey)}/feedback`;
}

function formatAgentEndpointError(error, endpoint, method = "GET") {
  const statusText = error?.status ? `HTTP ${error.status}` : "request failed";
  const detail = error?.body?.detail || error?.message || String(error || "");
  if (error?.status === 410 && String(detail).includes("Agent implementation is archived")) {
    return `${method} ${endpoint} -> HTTP 410. 실행 중인 backend가 active Agent unit route를 로딩하지 않았습니다. 서버 재시작 또는 배포 갱신 후 프론트 번들을 새로 로드하세요. detail: ${detail}`;
  }
  return `${method} ${endpoint} -> ${statusText}${detail ? `: ${detail}` : ""}`;
}

function queryUrl(path, params) {
  const query = Object.entries(params || {})
    .filter(([, value]) => value !== undefined && value !== null && value !== "")
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`)
    .join("&");
  return query ? `${path}?${query}` : path;
}

function toneForStatus(status) {
  if (status === "success" || status === "registered") return "ok";
  if (status === "warning" || status === "action_required" || status === "review" || status === "collecting" || status === "needs_clarification") return "warn";
  if (status === "failed" || status === "blocked" || status === "no_match") return "bad";
  return "neutral";
}

function statusColor(status) {
  if (status === "success" || status === "registered") return { bg: "var(--ok-50)", fg: "var(--ok)", line: "var(--ok-line)" };
  if (status === "warning" || status === "action_required" || status === "review" || status === "collecting" || status === "needs_clarification") return { bg: "var(--warn-50)", fg: "var(--warn)", line: "var(--warn-line)" };
  if (status === "failed" || status === "blocked" || status === "no_match") return { bg: "var(--danger-50)", fg: "var(--danger)", line: "var(--danger-line)" };
  if (status === "running" || status === "planned") return { bg: "var(--warn-50)", fg: "var(--warn)", line: "var(--warn-line)" };
  if (status === "available" || status === "skipped") return { bg: "var(--bg-primary)", fg: "var(--text-secondary)", line: "var(--border)" };
  return { bg: "var(--bg-tertiary)", fg: "var(--text-secondary)", line: "var(--border)" };
}

function formatHistoryTimestamp(value) {
  const raw = String(value || "").trim();
  if (!raw) return "시간 없음";
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return raw;
  return new Intl.DateTimeFormat("ko-KR", {
    year: "2-digit",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}

function historyActorLabel(item) {
  return String(item?.username || item?.created_by || item?.user || "작성자 미상");
}

function historyTimestampLabel(item) {
  return formatHistoryTimestamp(item?.timestamp || item?.created_at || item?.updated_at || "");
}

function JsonBlock({ value, maxHeight = 160 }) {
  return (
    <pre style={{
      margin: 0,
      maxHeight,
      overflow: "auto",
      padding: 8,
      border: "1px solid var(--border)",
      background: "var(--bg-primary)",
      color: "var(--text-secondary)",
      fontSize: 12,
      lineHeight: 1.45,
      whiteSpace: "pre-wrap",
      wordBreak: "break-word",
    }}>
      {JSON.stringify(value ?? {}, null, 2)}
    </pre>
  );
}

function RuntimeGraph({ graph, selectedId, onSelect }) {
  const layout = useMemo(() => {
    const nodes = graph?.nodes || [];
    const edges = graph?.edges || [];
    if (!nodes.length) return { nodes: [], edges: [], width: 320, height: 200 };
    const g = new dagre.graphlib.Graph();
    const rankdir = graph?.layout?.rankdir || graph?.metadata?.layout?.rankdir || "TB";
    const horizontal = rankdir === "LR" || rankdir === "RL";
    const nodeW = 178;
    const nodeH = 58;
    g.setGraph({
      rankdir,
      nodesep: horizontal ? 24 : 28,
      ranksep: horizontal ? 54 : 36,
      marginx: 22,
      marginy: 22,
    });
    g.setDefaultEdgeLabel(() => ({}));
    nodes.forEach((node) => g.setNode(node.id, { width: nodeW, height: nodeH }));
    edges.forEach((edge) => g.setEdge(edge.source, edge.target));
    try { dagre.layout(g); } catch (e) { console.warn("[agent] dagre layout failed", e); }
    const laidNodes = nodes.map((node) => {
      const pos = g.node(node.id) || {};
      return {
        ...node,
        x: Number.isFinite(pos.x) ? pos.x - nodeW / 2 : 0,
        y: Number.isFinite(pos.y) ? pos.y - nodeH / 2 : 0,
        w: nodeW,
        h: nodeH,
      };
    });
    const laidEdges = edges.map((edge) => {
      const meta = g.edge(edge.source, edge.target) || {};
      return { ...edge, points: meta.points || [] };
    });
    const maxX = Math.max(...laidNodes.map((node) => node.x + node.w), 280);
    const maxY = Math.max(...laidNodes.map((node) => node.y + node.h), 360);
    return { nodes: laidNodes, edges: laidEdges, width: maxX + 28, height: maxY + 28, rankdir };
  }, [graph]);

  const nodeById = Object.fromEntries(layout.nodes.map((node) => [node.id, node]));
  const pathForEdge = (edge) => {
    const points = edge.points && edge.points.length ? edge.points : [];
    if (points.length >= 2) {
      return points.map((point, idx) => `${idx === 0 ? "M" : "L"} ${point.x} ${point.y}`).join(" ");
    }
    const source = nodeById[edge.source];
    const target = nodeById[edge.target];
    if (!source || !target) return "";
    const horizontal = layout.rankdir === "LR" || layout.rankdir === "RL";
    if (horizontal) {
      const x1 = source.x + source.w;
      const y1 = source.y + source.h / 2;
      const x2 = target.x;
      const y2 = target.y + target.h / 2;
      const mid = (x1 + x2) / 2;
      return `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`;
    }
    const x1 = source.x + source.w / 2;
    const y1 = source.y + source.h;
    const x2 = target.x + target.w / 2;
    const y2 = target.y;
    const mid = (y1 + y2) / 2;
    return `M ${x1} ${y1} C ${x1} ${mid}, ${x2} ${mid}, ${x2} ${y2}`;
  };

  const clickable = typeof onSelect === "function";
  return (
    <div style={{ width: "100%", overflow: "auto", border: "1px solid var(--border)", background: "var(--bg-primary)" }}>
      <svg viewBox={`0 0 ${layout.width} ${layout.height}`} width="100%" height={Math.max(layout.height, 360)} style={{ display: "block" }}>
        <defs>
          <marker id="agentRuntimeArrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--text-secondary)" />
          </marker>
        </defs>
        {layout.edges.map((edge, idx) => (
          <path
            key={`${edge.source}-${edge.target}-${idx}`}
            d={pathForEdge(edge)}
            fill="none"
            stroke="var(--border-strong, var(--border))"
            strokeWidth="1.5"
            markerEnd="url(#agentRuntimeArrow)"
          />
        ))}
        {layout.nodes.map((node) => {
          const color = statusColor(node.status);
          const isSelected = selectedId && node.id === selectedId;
          return (
            <g
              key={node.id}
              transform={`translate(${node.x}, ${node.y})`}
              style={clickable ? { cursor: "pointer" } : undefined}
              onClick={clickable ? () => onSelect(node.id) : undefined}
            >
              {isSelected && (
                <rect
                  x="-4"
                  y="-4"
                  width={node.w + 8}
                  height={node.h + 8}
                  rx="6"
                  fill="none"
                  stroke="var(--brand, var(--text-primary))"
                  strokeWidth="2"
                />
              )}
              <rect
                width={node.w}
                height={node.h}
                rx="4"
                fill={color.bg}
                stroke={color.line || color.fg}
                strokeWidth={isSelected ? 2 : 1}
              />
              <text x="12" y="21" fill="var(--text-primary)" fontSize="12" fontWeight="700">{node.label}</text>
              <text x="12" y="39" fill={color.fg} fontSize="11">{node.phase || "node"} · {node.status || "pending"}</text>
              {node.action_required ? <circle cx={node.w - 14} cy="14" r="4" fill="var(--warn)" /> : null}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function PreviewTable({ preview }) {
  const rows = preview?.rows || [];
  const columns = (preview?.columns || []).slice(0, 40);
  if (!rows.length || !columns.length) {
    return <div style={{ padding: 28, textAlign: "center", color: "var(--text-secondary)", fontSize: 14 }}>preview 없음</div>;
  }
  return (
    <div style={{ overflow: "auto", maxHeight: 360, border: "1px solid var(--border)" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
        <thead>
          <tr>
            {columns.map((col) => (
              <th key={col} style={{
                position: "sticky",
                top: 0,
                background: "var(--bg-tertiary)",
                color: "var(--text-secondary)",
                borderBottom: "1px solid var(--border)",
                padding: "6px 8px",
                textAlign: "left",
                whiteSpace: "nowrap",
              }}>{col}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, idx) => (
            <tr key={idx}>
              {columns.map((col) => (
                <td key={col} style={{
                  borderBottom: "1px solid var(--border)",
                  padding: "5px 8px",
                  color: "var(--text-primary)",
                  maxWidth: 240,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }} title={String(row?.[col] ?? "")}>
                  {String(row?.[col] ?? "")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function compactRowsPayload(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return value;
  if (!Array.isArray(value.rows)) return value;
  return {
    ...value,
    rows: [],
    rows_returned: value.rows_returned ?? value.rows.length,
  };
}

const HOME_FLOWI_FALLBACK_GRAPH = {
  nodes: [
    { id: "prompt_input", label: "프롬프트 입력", phase: "input", status: "pending" },
    { id: "semantic_layer", label: "용어해석", phase: "semantic", status: "pending" },
    { id: "orchestrator", label: "오케스트레이터", phase: "plan", status: "pending" },
    { id: "result_renderer", label: "결과 정리", phase: "render", status: "pending" },
    { id: "unit_ai:filebrowser_ai_sql", label: "FileBrowser AI SQL", phase: "unit_ai_mcp", status: "available" },
    { id: "unit_ai:inform_registration", label: "Inform 등록 도우미", phase: "unit_ai_mcp", status: "available" },
    { id: "unit_ai:change_management", label: "변경점 관리 Flow-i", phase: "unit_ai_mcp", status: "available" },
    { id: "unit_ai:dashboard_agent", label: "Dashboard Agent", phase: "unit_ai_mcp", status: "available" },
    { id: "unit_ai:step_lookup", label: "Step ID 매칭", phase: "unit_ai_mcp", status: "available" },
    { id: "unit_ai:ppid_knob", label: "PPID Knob 분류", phase: "unit_ai_mcp", status: "available" },
  ],
  edges: [
    { source: "prompt_input", target: "semantic_layer" },
    { source: "semantic_layer", target: "orchestrator" },
    { source: "orchestrator", target: "unit_ai:filebrowser_ai_sql" },
    { source: "orchestrator", target: "unit_ai:inform_registration" },
    { source: "orchestrator", target: "unit_ai:change_management" },
    { source: "orchestrator", target: "unit_ai:dashboard_agent" },
    { source: "orchestrator", target: "unit_ai:step_lookup" },
    { source: "orchestrator", target: "unit_ai:ppid_knob" },
    { source: "orchestrator", target: "result_renderer" },
  ],
};

const FLOWI_FEW_SHOT_QUESTIONS = [
  {
    title: "SplitTable KNOB 기본 조회",
    prompt: "PRODA A1001 스플릿테이블 보여줘",
    target: "SplitTable 탭 KNOB 열 기준 조회",
  },
  {
    title: "제품명 확인 요청",
    prompt: "A1001 스플릿테이블 보여줘",
    target: "제품명을 먼저 확인",
  },
  {
    title: "FileBrowser SQL 조회",
    prompt: "PRODA INLINE에서 CA_BCD wafer별 평균 보여줘",
    target: "filebrowser_ai_sql",
  },
  {
    title: "Dashboard 추이",
    prompt: "PRODA CA_BCD wafer별 trend 차트 보여줘",
    target: "dashboard_agent",
  },
  {
    title: "Step ID 매칭",
    prompt: "AA100090은 어떤 공정이야",
    target: "step_lookup",
  },
  {
    title: "FAB 진행 상태",
    prompt: "PRODA A1001 현재 step 알려줘",
    target: "lot_current_step_lookup",
  },
];

function stateKeyByNodeFromGraph(graph) {
  const design = graph?.state_design || {};
  return Object.fromEntries(
    Object.entries(design)
      .map(([key, meta]) => [meta?.producer, key])
      .filter(([producer]) => producer && producer !== "runtime")
  );
}

function buildAccumulatedState(result, request, upToIdx, graph = result?.graph || EMPTY_GRAPH) {
  const trace = result?.trace || [];
  const stateKeyByNode = stateKeyByNodeFromGraph(graph);
  const state = {
    run_id: result?.run_id || null,
    request: request || null,
  };
  const limit = Number.isFinite(upToIdx) ? upToIdx + 1 : trace.length;
  for (let i = 0; i < limit && i < trace.length; i += 1) {
    const row = trace[i];
    const key = stateKeyByNode[row.node_id];
    if (key) state[key] = row.output;
  }
  return state;
}

function parseJsonObject(text, label) {
  let parsed = {};
  try {
    parsed = JSON.parse(text || "{}");
  } catch (e) {
    throw new Error(`${label} JSON 파싱 실패: ${e.message || String(e)}`);
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`${label} JSON은 object여야 합니다.`);
  }
  return parsed;
}

function listFromValue(value) {
  if (Array.isArray(value)) return value.map((item) => String(item || "").trim()).filter(Boolean);
  if (typeof value === "string") return value.split(/[,;\n]+/).map((item) => item.trim()).filter(Boolean);
  if (value && typeof value === "object" && Array.isArray(value.aliases)) return listFromValue(value.aliases);
  return [];
}

function aliasPayloadFromValue(value) {
  const payload = { aliases: listFromValue(value) };
  if (value && typeof value === "object" && !Array.isArray(value)) {
    if (value.semantic_class !== undefined) payload.semantic_class = String(value.semantic_class || "");
    if (value.normalization !== undefined) payload.normalization = value.normalization;
    if (value.value_domain !== undefined) payload.value_domain = value.value_domain;
  }
  return payload;
}

function useAgentFeedbackProfile(unitKey) {
  const [profile, setProfile] = useState(null);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");

  const load = () => {
    if (!unitKey) return Promise.resolve();
    setErr("");
    return sf(agentUnitFeedbackProfileEndpoint(unitKey))
      .then((payload) => setProfile(payload?.profile || null))
      .catch((e) => setErr(e.message || String(e)));
  };

  const submit = ({ rating, nodeId = "", runId = "", reason = "" }) => {
    if (!unitKey || !rating) return Promise.resolve();
    const target = `${rating}:${nodeId || "unit"}`;
    setBusy(target);
    setErr("");
    setMsg("");
    return postJson(agentUnitFeedbackEndpoint(unitKey), {
      rating,
      node_id: nodeId,
      run_id: runId,
      reason,
    }).then((payload) => {
      setProfile(payload?.profile || null);
      setMsg(nodeId ? `node feedback 저장: ${nodeId}` : "unit feedback 저장");
    }).catch((e) => setErr(e.message || String(e)))
      .finally(() => setBusy(""));
  };

  useEffect(() => { load(); }, [unitKey]);
  return { profile, busy, err, msg, load, submit, setErr, setMsg };
}

function formatPenaltyNumber(value) {
  const num = Number(value || 0);
  if (!Number.isFinite(num)) return "0";
  return Number.isInteger(num) ? String(num) : num.toFixed(1);
}

function RatingButtons({ feedback, nodeId = "", runId = "", reason = "" }) {
  const target = nodeId || "unit";
  const upBusy = feedback?.busy === `up:${target}`;
  const downBusy = feedback?.busy === `down:${target}`;
  return (
    <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
      <Button
        variant="ghost"
        onClick={() => feedback?.submit?.({ rating: "up", nodeId, runId, reason })}
        disabled={upBusy || downBusy}
        style={{ fontSize: 12, padding: "4px 10px", height: 28 }}
      >좋아요</Button>
      <Button
        variant="ghost"
        onClick={() => feedback?.submit?.({ rating: "down", nodeId, runId, reason })}
        disabled={upBusy || downBusy}
        style={{ fontSize: 12, padding: "4px 10px", height: 28, color: "var(--danger)" }}
      >싫어요</Button>
    </div>
  );
}

function NodeFeedbackInline({ feedback, nodeId, runId, fallback = null }) {
  if (!nodeId) return null;
  const nodeProfile = feedback?.profile?.nodes?.[nodeId] || fallback?.feedback_penalty || {};
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
      <RatingButtons feedback={feedback} nodeId={nodeId} runId={runId} reason="agent_node_detail" />
      <Pill tone={Number(nodeProfile.penalty || 0) > 0 ? "warn" : "neutral"}>
        penalty {formatPenaltyNumber(nodeProfile.penalty)}
      </Pill>
      <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>
        up {nodeProfile.up_count || 0} · down {nodeProfile.down_count || 0}
      </span>
    </div>
  );
}

function UnitFeedbackStatus({ feedback }) {
  return (
    <>
      {feedback?.err ? <Banner tone="bad" onClose={() => feedback.setErr("")}>{feedback.err}</Banner> : null}
      {feedback?.msg ? <Banner tone="ok" onClose={() => feedback.setMsg("")}>{feedback.msg}</Banner> : null}
    </>
  );
}

function UnitAnswerFeedback({ feedback, runId = "", reason = "agent_unit_answer" }) {
  const unit = feedback?.profile?.unit || {};
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
      <span style={{ fontSize: 11, color: "var(--text-secondary)", fontWeight: 700 }}>답변 feedback</span>
      <RatingButtons feedback={feedback} runId={runId} reason={reason} />
      <Pill tone={Number(unit.penalty || 0) > 0 ? "warn" : "neutral"}>
        penalty {formatPenaltyNumber(unit.penalty)}
      </Pill>
      <Pill tone={Number(unit.boost || 0) > 0 ? "ok" : "neutral"}>
        boost {formatPenaltyNumber(unit.boost)}
      </Pill>
      <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>
        up {unit.up_count || 0} · down {unit.down_count || 0}
      </span>
      <Button variant="ghost" onClick={feedback?.load} disabled={!!feedback?.busy} style={{ fontSize: 11, padding: "2px 8px", height: 24 }}>
        새로고침
      </Button>
    </div>
  );
}

function FileBrowserAiSqlUnitPanel() {
  const [graph, setGraph] = useState(null);
  const [roots, setRoots] = useState([]);
  const [products, setProducts] = useState([]);
  const [baseFiles, setBaseFiles] = useState([]);
  const [targetMode, setTargetMode] = useState("db_product");
  const [root, setRoot] = useState("");
  const [product, setProduct] = useState("");
  const [file, setFile] = useState("");
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [graphErr, setGraphErr] = useState("");
  const [agentRoutesPresent, setAgentRoutesPresent] = useState(true);
  const [result, setResult] = useState(null);
  const [lastRequest, setLastRequest] = useState(null);
  const [selectedNodeId, setSelectedNodeId] = useState(null);
  const [appliedSql, setAppliedSql] = useState("");
  const [applyBusy, setApplyBusy] = useState(false);
  const [history, setHistory] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [selectedHistoryId, setSelectedHistoryId] = useState("");
  const [manualPreviewVisible, setManualPreviewVisible] = useState(false);
  const feedback = useAgentFeedbackProfile("filebrowser_ai_sql");

  useEffect(() => {
    setAppliedSql(result?.preview?.applied_sql || result?.merged?.display_sql || result?.merged?.sql || "");
  }, [result?.run_id]);

  const loadHistory = () => {
    setHistoryLoading(true);
    return sf(agentUnitHistoryEndpoint("filebrowser_ai_sql"))
      .then((payload) => {
        const nextHistory = payload?.history || [];
        setHistory(nextHistory);
        if (!selectedHistoryId && nextHistory[0]?.history_id) setSelectedHistoryId(nextHistory[0].history_id);
      })
      .catch(() => setHistory([]))
      .finally(() => setHistoryLoading(false));
  };

  useEffect(() => {
    setLoading(true);
    Promise.all([
      sf("/api/agent/status").catch((e) => ({ error: e.message || String(e) })),
      sf(AGENT_UNIT_CATALOG_ENDPOINT).catch((e) => ({ error: e.message || String(e) })),
      sf(agentUnitGraphEndpoint("filebrowser_ai_sql")).catch((e) => ({ error: e.message || String(e) })),
      sf("/api/filebrowser/roots").catch(() => ({ roots: [] })),
      sf("/api/filebrowser/base-files").catch(() => ({ files: [] })),
      sf(agentUnitHistoryEndpoint("filebrowser_ai_sql")).catch(() => ({ history: [] })),
    ]).then(([statusPayload, catalogPayload, graphPayload, rootsPayload, filesPayload, historyPayload]) => {
      const routesOk = statusPayload?.ok === true;
      setAgentRoutesPresent(routesOk);
      if (catalogPayload?.error) setErr(catalogPayload.error);
      if (graphPayload?.error) {
        setGraphErr(graphPayload.error);
        setGraph(null);
      } else {
        setGraphErr("");
        setGraph(graphPayload?.graph || null);
      }
      const nextRoots = rootsPayload?.roots || [];
      setRoots(nextRoots);
      if (!root && nextRoots[0]?.name) setRoot(nextRoots[0].name);
      const nextFiles = filesPayload?.files || [];
      setBaseFiles(nextFiles);
      const firstFile = nextFiles.find((item) => ["parquet", "csv"].includes(String(item.ext || "").toLowerCase()));
      if (!file && firstFile?.path) setFile(firstFile.path);
      const nextHistory = historyPayload?.history || [];
      setHistory(nextHistory);
      if (!selectedHistoryId && nextHistory[0]?.history_id) setSelectedHistoryId(nextHistory[0].history_id);
    }).catch((e) => setErr(e.message || String(e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!root || targetMode !== "db_product") return;
    sf(`/api/filebrowser/products?root=${encodeURIComponent(root)}`)
      .then((payload) => {
        const nextProducts = payload?.products || [];
        setProducts(nextProducts);
        if (!nextProducts.find((item) => item.name === product)) {
          setProduct(nextProducts[0]?.name || "");
        }
      })
      .catch(() => setProducts([]));
  }, [root, targetMode]);

  const fileOptions = useMemo(() => (
    baseFiles
      .filter((item) => ["parquet", "csv"].includes(String(item.ext || "").toLowerCase()))
      .map((item) => ({ value: item.path || item.name, label: item.path || item.name, ext: item.ext, source: item.source || "" }))
  ), [baseFiles]);
  const activeGraph = result?.graph || graph || EMPTY_GRAPH;
  const graphNodes = activeGraph?.nodes || [];
  const firstGraphNodeId = graphNodes[0]?.id || null;
  const currentSelectedNodeId = selectedNodeId || firstGraphNodeId;

  useEffect(() => {
    const traceRows = result?.trace || [];
    if (traceRows.length) setSelectedNodeId(traceRows[traceRows.length - 1]?.node_id || null);
  }, [result?.run_id]);

  useEffect(() => {
    if (!selectedNodeId && firstGraphNodeId) setSelectedNodeId(firstGraphNodeId);
  }, [selectedNodeId, firstGraphNodeId]);

  const canRun = prompt.trim() && (
    targetMode === "db_product" ? (root && product) : file.trim()
  );
  const debugRequest = {
    natural_language: prompt.trim(),
    scope: targetMode,
    root: targetMode === "db_product" ? root : "",
    product: targetMode === "db_product" ? product : "",
    file: targetMode !== "db_product" ? file.trim() : "",
  };

  const run = () => {
    if (!canRun) return;
    setBusy(true);
    setErr("");
    setResult(null);
    setManualPreviewVisible(false);
    const body = {
      natural_language: prompt.trim(),
      scope: targetMode,
      root: targetMode === "db_product" ? root : "",
      product: targetMode === "db_product" ? product : "",
      file: targetMode !== "db_product" ? file.trim() : "",
    };
    setLastRequest(body);
    postJson(agentUnitRunEndpoint("filebrowser_ai_sql"), body)
      .then((payload) => {
        setResult(payload);
        loadHistory();
      })
      .catch((e) => setErr(e.message || String(e)))
      .finally(() => setBusy(false));
  };

  const applyHistoryItem = (item) => {
    const nextScope = item?.scope === "hive" ? "db_product" : (item?.scope || "db_product");
    setPrompt(item?.natural_language || "");
    setTargetMode(nextScope);
    setRoot(item?.root || "");
    setProduct(item?.product || "");
    setFile(item?.file || "");
    setAppliedSql(item?.display_sql || item?.sql || "");
    setResult(null);
    setSelectedNodeId(null);
    setManualPreviewVisible(false);
    setErr("");
  };

  const applyPreviewSql = () => {
    const target = lastRequest || {
      scope: targetMode,
      root: targetMode === "db_product" ? root : "",
      product: targetMode === "db_product" ? product : "",
      file: targetMode !== "db_product" ? file.trim() : "",
    };
    if (!target?.scope) return;
    setApplyBusy(true);
    setErr("");
    const params = {
      sql: appliedSql || "",
      rows: 100,
      page: 0,
      page_size: 100,
      cols: 100,
      meta_only: false,
      _ts: Date.now(),
    };
    let url = "";
    if (target.scope === "db_product") {
      url = queryUrl("/api/filebrowser/view", { ...params, root: target.root, product: target.product });
    } else if (target.scope === "rootpq") {
      url = queryUrl("/api/filebrowser/root-parquet-view", { ...params, file: target.file });
    } else {
      url = queryUrl("/api/filebrowser/base-file-view", { ...params, file: target.file });
    }
    sf(url).then((payload) => {
      const selectedCols = typeof payload?.selected_cols === "string"
        ? payload.selected_cols.split(",").map((item) => item.trim()).filter(Boolean)
        : [];
      const preview = {
        columns: payload?.columns || [],
        rows: payload?.data || [],
        total_rows: payload?.total_rows ?? 0,
        preview_capped: !!payload?.preview_capped,
        warnings: [],
        selected_cols: payload?.selected_cols || "",
        total_cols: payload?.total_cols ?? 0,
        row_count_unknown: !!payload?.row_count_unknown,
        applied_sql: appliedSql || "",
        display_sql: appliedSql || "",
        applied_where_sql: "",
        applied_select_cols: selectedCols,
      };
      setResult((prev) => {
        if (!prev) return prev;
        const nextTrace = (prev.trace || []).map((row) => (
          row.node_id === "preview_apply"
            ? { ...row, status: "success", output: preview, warnings: [...(row.warnings || []), "manual SQL preview applied"] }
            : row
        ));
        return { ...prev, ok: true, preview, trace: nextTrace };
      });
      setManualPreviewVisible(true);
      setSelectedNodeId("preview_apply");
    }).catch((e) => setErr(e.message || String(e)))
      .finally(() => setApplyBusy(false));
  };

  const trace = result?.trace || [];
  const selectedIdx = currentSelectedNodeId
    ? trace.findIndex((row) => row.node_id === currentSelectedNodeId)
    : -1;
  const selectedTraceNode = selectedIdx >= 0 ? trace[selectedIdx] : null;
  const selectedGraphNode = graphNodes.find((node) => node.id === currentSelectedNodeId) || graphNodes[0] || null;
  const selectedNode = selectedTraceNode
    ? {
      ...selectedGraphNode,
      ...selectedTraceNode,
      id: selectedGraphNode?.id || selectedTraceNode.node_id,
      node_id: selectedTraceNode.node_id || selectedGraphNode?.id,
      persona: selectedGraphNode?.persona || selectedTraceNode.persona || "",
      prompt: selectedGraphNode?.prompt || selectedTraceNode.prompt || {},
      state_io: selectedGraphNode?.state_io || selectedTraceNode.state_io || {},
      reads: selectedGraphNode?.reads || selectedTraceNode.reads || [],
      writes: selectedGraphNode?.writes || selectedTraceNode.writes || [],
      shared_state: selectedGraphNode?.shared_state || selectedTraceNode.shared_state || [],
      answer_attach_rule: selectedGraphNode?.answer_attach_rule || selectedTraceNode.answer_attach_rule || "",
    }
    : (selectedGraphNode ? { ...selectedGraphNode, node_id: selectedGraphNode.id } : null);
  const accumulatedState = useMemo(
    () => buildAccumulatedState(result, lastRequest, selectedIdx >= 0 ? selectedIdx : undefined, activeGraph),
    [result, lastRequest, selectedIdx, activeGraph]
  );
  const stateDesign = activeGraph?.state_design || {};
  const stateValue = trace.length ? accumulatedState : stateDesign;

  const stateSubtitle = selectedTraceNode
    ? `up to ${selectedTraceNode.label || selectedTraceNode.node_id}`
    : (trace.length ? "final state" : "");
  const graphSubtitle = trace.length
    ? `${trace.length}/${graphNodes.length} nodes · click to inspect`
    : "";

  const uiMode = targetMode === "db_product" ? "db" : "file";
  const fileExt = (() => {
    const matched = fileOptions.find((item) => item.value === file);
    if (matched?.ext) return String(matched.ext).toLowerCase();
    const trimmed = (file || "").trim();
    if (!trimmed) return "";
    const dot = trimmed.lastIndexOf(".");
    return dot >= 0 ? trimmed.slice(dot + 1).toLowerCase() : "";
  })();
  let targetTone = "neutral";
  let targetLabel = "대상 미설정";
  if (uiMode === "db" && root && product) {
    targetTone = "ok";
    targetLabel = `📁 ${root} / ${product}`;
  } else if (uiMode === "file" && file.trim()) {
    targetTone = "ok";
    const fileShort = file.split(/[\\/]/).pop();
    targetLabel = `📄 ${fileShort}${fileExt ? ` · ${fileExt}` : ""}`;
  } else {
    targetTone = "warn";
  }
  const targetPin = <Pill tone={targetTone}>{targetLabel}</Pill>;

  const setUiMode = (next) => {
    if (next === "db") {
      setTargetMode("db_product");
      return;
    }
    const ext = fileExt;
    setTargetMode(ext === "parquet" ? "rootpq" : "base");
  };
  const selectedHistory = useMemo(() => (
    history.find((item) => item.history_id === selectedHistoryId) || history[0] || null
  ), [history, selectedHistoryId]);
  const historyTargetLabel = (item) => {
    const itemScope = item?.scope === "hive" ? "db_product" : (item?.scope || "db_product");
    return itemScope === "db_product"
      ? `${item?.root || "-"} / ${item?.product || "-"}`
      : (item?.file || "-");
  };
  const selectedNodeOutput = compactRowsPayload(selectedTraceNode?.output);
  const selectedPromptSystem = selectedNode?.prompt?.system || selectedTraceNode?.output?.llm?.system || "";
  const selectedPromptMode = selectedNode?.prompt?.mode || (selectedTraceNode?.output?.llm ? "llm_json" : "deterministic");
  const selectedStateIo = selectedNode?.state_io || {
    reads: selectedNode?.reads || [],
    writes: selectedNode?.writes || [],
  };
  const segBtnStyle = (active) => ({
    flex: 1,
    padding: "10px 12px",
    fontSize: 13,
    fontWeight: 600,
    border: "1px solid var(--border)",
    background: active ? "var(--bg-tertiary)" : "var(--bg-primary)",
    color: active ? "var(--text-primary)" : "var(--text-secondary)",
    cursor: "pointer",
    transition: "background 80ms",
  });

  return (
    <div style={{ display: "grid", gap: 10 }}>
      {!loading && !agentRoutesPresent ? (
        <Banner tone="bad">
          백엔드에 agent 라우트가 없어요 (`/api/agent/*` → "API not found"). `python app.py` 또는 uvicorn 프로세스를 재시작해 주세요. 재시작 후에도 같은 에러면 `backend/routers/agent.py`가 최신인지 확인.
        </Banner>
      ) : (
        <>
          {err && <Banner tone="bad" onClose={() => setErr("")}>{err}</Banner>}
          {graphErr && (
            <Banner tone="warn" onClose={() => setGraphErr("")}>
              graph fetch 실패 — 기본 노드 구조로 표시: {graphErr}
            </Banner>
          )}
          <UnitFeedbackStatus feedback={feedback} />
        </>
      )}
      <Panel
        title="질문 이력"
        subtitle={historyLoading ? "loading" : `${history.length} items`}
        right={<Button variant="ghost" onClick={loadHistory} disabled={historyLoading} style={{ fontSize: 11, padding: "2px 8px", height: 24 }}>새로고침</Button>}
      >
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.05fr) minmax(320px, 0.95fr)", gap: 10, alignItems: "start" }}>
          <div style={{ maxHeight: 230, overflow: "auto", border: "1px solid var(--border)", background: "var(--bg-primary)" }}>
            {history.length ? history.map((item) => {
              const active = (item.history_id || "") === (selectedHistory?.history_id || "");
              return (
                <button
                  type="button"
                  key={item.history_id || `${item.timestamp}:${item.natural_language}`}
                  onClick={() => setSelectedHistoryId(item.history_id || "")}
                  style={{
                    display: "grid",
                    gap: 3,
                    width: "100%",
                    textAlign: "left",
                    justifyContent: "stretch",
                    justifyItems: "stretch",
                    alignItems: "start",
                    padding: "8px 9px",
                    border: 0,
                    borderBottom: "1px solid var(--border)",
                    background: active ? "var(--bg-tertiary)" : "transparent",
                    color: "var(--text-primary)",
                    cursor: "pointer",
                  }}
                >
                  <span style={{ width: "100%", textAlign: "left", fontSize: 12, fontWeight: 700, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {item.natural_language || "(empty)"}
                  </span>
                  <span style={{ width: "100%", textAlign: "left", fontSize: 11, color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {historyActorLabel(item)} · {historyTimestampLabel(item)}
                  </span>
                  <span style={{ width: "100%", textAlign: "left", fontSize: 11, color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {item.source || "history"} · {historyTargetLabel(item)}
                  </span>
                  {item.display_sql ? (
                    <span style={{ width: "100%", textAlign: "left", fontSize: 11, color: "var(--text-secondary)", fontFamily: "monospace", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {item.display_sql}
                    </span>
                  ) : null}
                </button>
              );
            }) : (
              <div style={{ padding: 12, fontSize: 12, color: "var(--text-secondary)" }}>
                저장된 질문 이력이 없습니다.
              </div>
            )}
          </div>
          <div style={{ display: "grid", gap: 8 }}>
            {selectedHistory ? (
              <>
                <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                  <Pill tone={selectedHistory.ok ? "ok" : "neutral"}>{selectedHistory.source || "history"}</Pill>
                  <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                    {historyActorLabel(selectedHistory)} · {historyTimestampLabel(selectedHistory)}
                  </span>
                  <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>{historyTargetLabel(selectedHistory)}</span>
                  <Button
                    variant="primary"
                    onClick={() => applyHistoryItem(selectedHistory)}
                    style={{ marginLeft: "auto", fontSize: 12, padding: "4px 10px", height: 28 }}
                  >재현</Button>
                </div>
                <div style={{ textAlign: "left", fontSize: 12, fontWeight: 700, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {selectedHistory.natural_language || "(empty)"}
                </div>
                <UnitAnswerFeedback
                  feedback={feedback}
                  runId={selectedHistory.run_id || selectedHistory.history_id || ""}
                  reason="agent_unit_history"
                />
                {selectedHistory.answer ? (
                  <div style={{ textAlign: "left", fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.45 }}>
                    {selectedHistory.answer}
                  </div>
                ) : null}
                <JsonBlock
                  value={{
                    display_sql: selectedHistory.display_sql || selectedHistory.sql || "",
                    where_sql: selectedHistory.where_sql || "",
                    selected_columns: selectedHistory.selected_columns || [],
                    warnings: selectedHistory.warnings || [],
                    trace_summary: selectedHistory.trace_summary || [],
                    action_log_summary: selectedHistory.action_log_summary || [],
                    preview_summary: selectedHistory.preview_summary || {},
                  }}
                  maxHeight={190}
                />
              </>
            ) : (
              <div style={{ padding: 12, fontSize: 12, color: "var(--text-secondary)", border: "1px solid var(--border)", background: "var(--bg-primary)" }}>
                이력을 선택하면 SQL과 trace 요약을 확인할 수 있습니다.
              </div>
            )}
          </div>
        </div>
      </Panel>

      <div className="flow-agent-unit-grid">
        <Panel title="State" subtitle={stateSubtitle}>
          <div style={{ display: "grid", gap: 8 }}>
            <JsonBlock value={stateValue} maxHeight={trace.length ? 520 : 620} />
            {trace.length && Object.keys(stateDesign).length ? (
              <details style={{ border: "1px solid var(--border)", background: "var(--bg-primary)" }}>
                <summary style={{ padding: "6px 8px", fontSize: 11, color: "var(--text-secondary)", cursor: "pointer", background: "var(--bg-tertiary)" }}>
                  state_design
                </summary>
                <JsonBlock value={stateDesign} maxHeight={220} />
              </details>
            ) : null}
          </div>
        </Panel>

        <Panel title="LangGraph" subtitle={graphSubtitle}>
          <div className="flow-agent-node-grid">
            <RuntimeGraph graph={activeGraph} selectedId={currentSelectedNodeId} onSelect={setSelectedNodeId} />
            <div style={{ display: "grid", gap: 8 }}>
              {selectedNode ? (
                <>
                  <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                    <strong style={{ fontSize: 13 }}>{selectedNode.label || selectedNode.node_id}</strong>
                    <Pill tone={toneForStatus(selectedNode.status)}>{selectedNode.status || "pending"}</Pill>
                    {selectedTraceNode ? (
                      <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>{selectedTraceNode.duration_ms || 0} ms</span>
                    ) : null}
                  </div>
                  <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>{selectedNode.node_id}</span>
                  <NodeFeedbackInline
                    feedback={feedback}
                    nodeId={selectedNode.node_id}
                    runId={result?.run_id || ""}
                    fallback={selectedNode}
                  />
                  {(selectedTraceNode?.warnings || []).length ? (
                    <Banner tone="warn">{(selectedTraceNode.warnings || []).join(" / ")}</Banner>
                  ) : null}
                  <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>Persona</div>
                  <JsonBlock
                    value={{
                      persona: selectedNode.persona || "",
                      prompt: {
                        mode: selectedPromptMode,
                        system: selectedPromptSystem,
                      },
                      answer_attach_rule: selectedNode.answer_attach_rule || "",
                    }}
                    maxHeight={selectedPromptSystem ? 220 : 140}
                  />
                  <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>State I/O</div>
                  <JsonBlock
                    value={{
                      reads: selectedStateIo.reads || selectedNode.reads || [],
                      writes: selectedStateIo.writes || selectedNode.writes || [],
                    }}
                    maxHeight={150}
                  />
                  <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>공유 state</div>
                  <JsonBlock value={selectedNode.shared_state || []} maxHeight={120} />
                  <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>실행 결과</div>
                  <JsonBlock
                    value={selectedTraceNode ? {
                      status: selectedTraceNode.status,
                      input_summary: selectedTraceNode.input_summary || {},
                      output: selectedNodeOutput || {},
                      duration_ms: selectedTraceNode.duration_ms || 0,
                    } : {
                      status: selectedNode.status || "pending",
                      input_summary: {},
                      output: {},
                    }}
                    maxHeight={230}
                  />
                </>
              ) : (
                <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                  노드 정보 없음
                </div>
              )}
            </div>
          </div>
        </Panel>

        <Panel
          title="Test prompt"
          subtitle={result ? `${result.unit_ai} · ${result.run_id}` : (busy ? "running" : "")}
          right={<Pill tone={result?.ok ? "ok" : "neutral"}>{result?.run_id || (loading ? "loading" : "ready")}</Pill>}
        >
          <div style={{ display: "grid", gap: 10 }}>
            <Textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={3}
              placeholder="질문을 입력하거나 이력에서 재현을 누르세요."
            />
            <div style={{ display: "grid", gap: 8 }}>
              <div style={{ display: "flex", gap: 0, border: "1px solid var(--border)", borderRadius: 4, overflow: "hidden" }}>
                <button type="button" style={segBtnStyle(uiMode === "db")} onClick={() => setUiMode("db")}>DB</button>
                <button type="button" style={{ ...segBtnStyle(uiMode === "file"), borderLeft: "1px solid var(--border)" }} onClick={() => setUiMode("file")}>단일 File</button>
              </div>

              {uiMode === "db" ? (
                <div style={{ display: "grid", gap: 8 }}>
                  <Field label="root">
                    <Select value={root} onChange={(e) => setRoot(e.target.value)}>
                      {roots.map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}
                    </Select>
                  </Field>
                  <Field label="product">
                    <Select value={product} onChange={(e) => setProduct(e.target.value)}>
                      {products.map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}
                    </Select>
                  </Field>
                </div>
              ) : (
                <Field label={`단일 파일 (${fileOptions.length})`}>
                  <Select
                    value={file}
                    onChange={(e) => {
                      const next = e.target.value;
                      setFile(next);
                      const matched = fileOptions.find((item) => item.value === next);
                      const ext = String(matched?.ext || "").toLowerCase();
                      setTargetMode(ext === "parquet" ? "rootpq" : "base");
                    }}
                  >
                    <option value="">(단일 파일 선택)</option>
                    {file && !fileOptions.find((item) => item.value === file) ? <option value={file}>{file}</option> : null}
                    {fileOptions.map((item) => (
                      <option key={`${item.source}:${item.value}`} value={item.value}>
                        {item.label}{item.ext ? ` · ${item.ext}` : ""}
                      </option>
                    ))}
                  </Select>
                </Field>
              )}

              <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                {targetPin}
                <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>scope=<code>{targetMode}</code></span>
              </div>
            </div>

            <div style={{ display: "grid", gap: 6 }}>
              <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>debug request</div>
              <JsonBlock value={debugRequest} maxHeight={110} />
            </div>
            <Button variant="primary" onClick={run} disabled={!canRun || busy}>
              {busy ? "실행 중" : "실행"}
            </Button>

            {result?.preview?.applied_sql || result?.merged?.sql ? (
              <div style={{ borderTop: "1px solid var(--border)", paddingTop: 10, display: "grid", gap: 6 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <strong style={{ fontSize: 12 }}>결과</strong>
                  <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>FileBrowser와 같은 SQL</span>
                  <Button
                    variant="ghost"
                    onClick={() => navigator.clipboard?.writeText?.(appliedSql)}
                    style={{ marginLeft: "auto", fontSize: 11, padding: "2px 8px", height: 22 }}
                  >복사</Button>
                </div>
                <UnitAnswerFeedback feedback={feedback} runId={result?.run_id || ""} />
                <Textarea value={appliedSql} onChange={(e) => setAppliedSql(e.target.value)} rows={3} />
                <Button variant="primary" onClick={applyPreviewSql} disabled={applyBusy}>
                  {applyBusy ? "적용 중" : "적용"}
                </Button>
                <JsonBlock
                  value={{
                    display_sql: result?.preview?.display_sql || result?.merged?.display_sql || result?.merged?.sql || "",
                    where_sql: result?.preview?.applied_where_sql || result?.merged?.where_sql || "",
                    selected_columns: result?.preview?.applied_select_cols || result?.merged?.selected_columns || [],
                    preview_summary: {
                      columns: result?.preview?.columns || [],
                      rows_returned: result?.preview?.rows_returned ?? result?.preview?.rows?.length ?? 0,
                      total_rows: result?.preview?.total_rows ?? 0,
                      preview_capped: !!result?.preview?.preview_capped,
                    },
                  }}
                  maxHeight={140}
                />
                {manualPreviewVisible && result?.preview?.rows?.length ? (
                  <>
                    <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>
                      preview ({result.preview.rows.length} rows / total {result.preview.total_rows ?? 0}
                      {result.preview.preview_capped ? " · capped" : ""})
                    </div>
                    <PreviewTable preview={result.preview} />
                  </>
                ) : null}
              </div>
            ) : null}
          </div>
        </Panel>
      </div>
    </div>
  );
}

function InformRegistrationUnitPanel() {
  const [graph, setGraph] = useState(null);
  const [prompt, setPrompt] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [graphErr, setGraphErr] = useState("");
  const [result, setResult] = useState(null);
  const [lastRequest, setLastRequest] = useState(null);
  const [selectedNodeId, setSelectedNodeId] = useState(null);
  const [history, setHistory] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [selectedHistoryId, setSelectedHistoryId] = useState("");
  const feedback = useAgentFeedbackProfile("inform_registration");

  const loadHistory = () => {
    setHistoryLoading(true);
    return sf(agentUnitHistoryEndpoint("inform_registration"))
      .then((payload) => {
        const nextHistory = payload?.history || [];
        setHistory(nextHistory);
        if (!selectedHistoryId && nextHistory[0]?.history_id) setSelectedHistoryId(nextHistory[0].history_id);
      })
      .catch(() => setHistory([]))
      .finally(() => setHistoryLoading(false));
  };

  useEffect(() => {
    setLoading(true);
    Promise.all([
      sf(agentUnitGraphEndpoint("inform_registration")).catch((e) => ({
        error: formatAgentEndpointError(e, agentUnitGraphEndpoint("inform_registration")),
      })),
      sf(agentUnitHistoryEndpoint("inform_registration")).catch(() => ({ history: [] })),
    ]).then(([graphPayload, historyPayload]) => {
      if (graphPayload?.error) {
        setGraphErr(graphPayload.error);
        setGraph(null);
      } else {
        setGraphErr("");
        setGraph(graphPayload?.graph || null);
      }
      const nextHistory = historyPayload?.history || [];
      setHistory(nextHistory);
      if (!selectedHistoryId && nextHistory[0]?.history_id) setSelectedHistoryId(nextHistory[0].history_id);
    }).catch((e) => setErr(e.message || String(e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (result?.session_id) setSessionId(result.session_id);
  }, [result?.session_id]);

  const activeGraph = result?.graph || graph || EMPTY_GRAPH;
  const graphNodes = activeGraph?.nodes || [];
  const firstGraphNodeId = graphNodes[0]?.id || null;
  const currentSelectedNodeId = selectedNodeId || firstGraphNodeId;
  const trace = result?.trace || [];

  useEffect(() => {
    if (result?.requires_confirmation) {
      setSelectedNodeId("human_review");
      return;
    }
    if (trace.length) setSelectedNodeId(trace[trace.length - 1]?.node_id || null);
  }, [result?.run_id, result?.requires_confirmation]);

  useEffect(() => {
    if (!selectedNodeId && firstGraphNodeId) setSelectedNodeId(firstGraphNodeId);
  }, [selectedNodeId, firstGraphNodeId]);

  const selectedIdx = currentSelectedNodeId
    ? trace.findIndex((row) => row.node_id === currentSelectedNodeId)
    : -1;
  const selectedTraceNode = selectedIdx >= 0 ? trace[selectedIdx] : null;
  const selectedGraphNode = graphNodes.find((node) => node.id === currentSelectedNodeId) || graphNodes[0] || null;
  const selectedNode = selectedTraceNode
    ? {
      ...selectedGraphNode,
      ...selectedTraceNode,
      id: selectedGraphNode?.id || selectedTraceNode.node_id,
      node_id: selectedTraceNode.node_id || selectedGraphNode?.id,
      persona: selectedGraphNode?.persona || selectedTraceNode.persona || "",
      prompt: selectedGraphNode?.prompt || selectedTraceNode.prompt || {},
      state_io: selectedGraphNode?.state_io || selectedTraceNode.state_io || {},
      reads: selectedGraphNode?.reads || selectedTraceNode.reads || [],
      writes: selectedGraphNode?.writes || selectedTraceNode.writes || [],
      shared_state: selectedGraphNode?.shared_state || selectedTraceNode.shared_state || [],
      answer_attach_rule: selectedGraphNode?.answer_attach_rule || selectedTraceNode.answer_attach_rule || "",
    }
    : (selectedGraphNode ? { ...selectedGraphNode, node_id: selectedGraphNode.id } : null);
  const accumulatedState = useMemo(
    () => buildAccumulatedState(result, lastRequest, selectedIdx >= 0 ? selectedIdx : undefined, activeGraph),
    [result, lastRequest, selectedIdx, activeGraph]
  );
  const stateDesign = activeGraph?.state_design || {};
  const stateValue = trace.length ? accumulatedState : stateDesign;
  const stateSubtitle = selectedTraceNode
    ? `up to ${selectedTraceNode.label || selectedTraceNode.node_id}`
    : (trace.length ? "final state" : "");
  const graphSubtitle = trace.length
    ? `${trace.length}/${graphNodes.length} nodes · click to inspect`
    : "";
  const selectedNodeOutput = compactRowsPayload(selectedTraceNode?.output);
  const selectedPromptSystem = selectedNode?.prompt?.system || "";
  const selectedPromptMode = selectedNode?.prompt?.mode || "deterministic";
  const selectedStateIo = selectedNode?.state_io || {
    reads: selectedNode?.reads || [],
    writes: selectedNode?.writes || [],
  };

  const selectedHistory = useMemo(() => (
    history.find((item) => item.history_id === selectedHistoryId) || history[0] || null
  ), [history, selectedHistoryId]);
  const historyPrompt = (item) => item?.prompt || item?.natural_language || "";
  const debugRequest = {
    prompt: prompt.trim(),
    session_id: sessionId,
    action: "continue",
    slot_overrides: {},
  };
  const canContinue = !!(prompt.trim() || sessionId);
  const canConfirm = !!(result?.requires_confirmation && result?.human_review?.can_confirm && sessionId);

  const runAction = (action) => {
    if (action === "continue" && !canContinue) return;
    if (action === "confirm" && !sessionId) return;
    if (action === "cancel" && !sessionId) return;
    const body = {
      prompt: action === "continue" ? prompt.trim() : "",
      session_id: sessionId,
      action,
      slot_overrides: {},
    };
    setBusy(true);
    setErr("");
    setLastRequest(body);
    postJson(agentUnitRunEndpoint("inform_registration"), body)
      .then((payload) => {
        setResult(payload);
        if (payload?.session_id) setSessionId(payload.session_id);
        loadHistory();
      })
      .catch((e) => setErr(e.message || String(e)))
      .finally(() => setBusy(false));
  };

  const continueFromHistory = (item) => {
    setSessionId(item?.session_id || "");
    setPrompt(historyPrompt(item));
    setSelectedHistoryId(item?.history_id || "");
    setErr("");
  };

  return (
    <div style={{ display: "grid", gap: 10 }}>
      {err && <Banner tone="bad" onClose={() => setErr("")}>{err}</Banner>}
      {graphErr && (
        <Banner tone="warn" onClose={() => setGraphErr("")}>
          Inform graph fetch 진단 — 기본 노드 구조로 표시: {graphErr}
        </Banner>
      )}
      <UnitFeedbackStatus feedback={feedback} />
      <Panel
        title="질문 이력"
        subtitle={historyLoading ? "loading" : `${history.length} items`}
        right={<Button variant="ghost" onClick={loadHistory} disabled={historyLoading} style={{ fontSize: 11, padding: "2px 8px", height: 24 }}>새로고침</Button>}
      >
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.05fr) minmax(320px, 0.95fr)", gap: 10, alignItems: "start" }}>
          <div style={{ maxHeight: 230, overflow: "auto", border: "1px solid var(--border)", background: "var(--bg-primary)" }}>
            {history.length ? history.map((item) => {
              const active = (item.history_id || "") === (selectedHistory?.history_id || "");
              return (
                <button
                  type="button"
                  key={item.history_id || `${item.timestamp}:${historyPrompt(item)}`}
                  onClick={() => setSelectedHistoryId(item.history_id || "")}
                  style={{
                    display: "grid",
                    gap: 3,
                    width: "100%",
                    textAlign: "left",
                    justifyContent: "stretch",
                    justifyItems: "stretch",
                    alignItems: "start",
                    padding: "8px 9px",
                    border: 0,
                    borderBottom: "1px solid var(--border)",
                    background: active ? "var(--bg-tertiary)" : "transparent",
                    color: "var(--text-primary)",
                    cursor: "pointer",
                  }}
                >
                  <span style={{ width: "100%", textAlign: "left", fontSize: 12, fontWeight: 700, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {historyPrompt(item) || item.answer || "(empty)"}
                  </span>
                  <span style={{ width: "100%", textAlign: "left", fontSize: 11, color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {historyActorLabel(item)} · {historyTimestampLabel(item)}
                  </span>
                  <span style={{ width: "100%", textAlign: "left", fontSize: 11, color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {item.status || "status"} · {item.action || "continue"} · {item.session_id || ""}
                  </span>
                </button>
              );
            }) : (
              <div style={{ padding: 12, fontSize: 12, color: "var(--text-secondary)" }}>
                저장된 Inform 등록 이력이 없습니다.
              </div>
            )}
          </div>
          <div style={{ display: "grid", gap: 8 }}>
            {selectedHistory ? (
              <>
                <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                  <Pill tone={toneForStatus(selectedHistory.status)}>{selectedHistory.status || "history"}</Pill>
                  <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                    {historyActorLabel(selectedHistory)} · {historyTimestampLabel(selectedHistory)}
                  </span>
                  <Button
                    variant="primary"
                    onClick={() => continueFromHistory(selectedHistory)}
                    style={{ marginLeft: "auto", fontSize: 12, padding: "4px 10px", height: 28 }}
                  >이어하기</Button>
                </div>
                <div style={{ textAlign: "left", fontSize: 12, fontWeight: 700, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {historyPrompt(selectedHistory) || selectedHistory.answer || "(empty)"}
                </div>
                <UnitAnswerFeedback
                  feedback={feedback}
                  runId={selectedHistory.run_id || selectedHistory.history_id || ""}
                  reason="agent_unit_history"
                />
                {selectedHistory.answer ? (
                  <div style={{ textAlign: "left", fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.45 }}>
                    {selectedHistory.answer}
                  </div>
                ) : null}
                <JsonBlock
                  value={{
                    missing: selectedHistory.missing || [],
                    slots: selectedHistory.slots || {},
                    draft: selectedHistory.draft || {},
                    human_review: selectedHistory.human_review || {},
                    requires_confirmation: !!selectedHistory.requires_confirmation,
                    created_inform: selectedHistory.created_inform || {},
                    warnings: selectedHistory.warnings || [],
                  }}
                  maxHeight={190}
                />
              </>
            ) : (
              <div style={{ padding: 12, fontSize: 12, color: "var(--text-secondary)", border: "1px solid var(--border)", background: "var(--bg-primary)" }}>
                이력을 선택하면 Inform draft와 session 상태를 확인할 수 있습니다.
              </div>
            )}
          </div>
        </div>
      </Panel>

      <div className="flow-agent-unit-grid">
        <Panel title="State" subtitle={stateSubtitle}>
          <div style={{ display: "grid", gap: 8 }}>
            <JsonBlock value={stateValue} maxHeight={trace.length ? 520 : 620} />
            {trace.length && Object.keys(stateDesign).length ? (
              <details style={{ border: "1px solid var(--border)", background: "var(--bg-primary)" }}>
                <summary style={{ padding: "6px 8px", fontSize: 11, color: "var(--text-secondary)", cursor: "pointer", background: "var(--bg-tertiary)" }}>
                  state_design
                </summary>
                <JsonBlock value={stateDesign} maxHeight={220} />
              </details>
            ) : null}
          </div>
        </Panel>

        <Panel title="LangGraph" subtitle={graphSubtitle}>
          <div className="flow-agent-node-grid">
            <RuntimeGraph graph={activeGraph} selectedId={currentSelectedNodeId} onSelect={setSelectedNodeId} />
            <div style={{ display: "grid", gap: 8 }}>
              {selectedNode ? (
                <>
                  <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                    <strong style={{ fontSize: 13 }}>{selectedNode.label || selectedNode.node_id}</strong>
                    <Pill tone={toneForStatus(selectedNode.status)}>{selectedNode.status || "pending"}</Pill>
                    {selectedTraceNode ? (
                      <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>{selectedTraceNode.duration_ms || 0} ms</span>
                    ) : null}
                  </div>
                  <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>{selectedNode.node_id}</span>
                  <NodeFeedbackInline
                    feedback={feedback}
                    nodeId={selectedNode.node_id}
                    runId={result?.run_id || ""}
                    fallback={selectedNode}
                  />
                  {(selectedTraceNode?.warnings || []).length ? (
                    <Banner tone="warn">{(selectedTraceNode.warnings || []).join(" / ")}</Banner>
                  ) : null}
                  <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>Persona</div>
                  <JsonBlock
                    value={{
                      persona: selectedNode.persona || "",
                      prompt: {
                        mode: selectedPromptMode,
                        system: selectedPromptSystem,
                      },
                      answer_attach_rule: selectedNode.answer_attach_rule || "",
                    }}
                    maxHeight={selectedPromptSystem ? 220 : 140}
                  />
                  <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>State I/O</div>
                  <JsonBlock
                    value={{
                      reads: selectedStateIo.reads || selectedNode.reads || [],
                      writes: selectedStateIo.writes || selectedNode.writes || [],
                    }}
                    maxHeight={150}
                  />
                  <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>공유 state</div>
                  <JsonBlock value={selectedNode.shared_state || []} maxHeight={120} />
                  <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>실행 결과</div>
                  <JsonBlock
                    value={selectedTraceNode ? {
                      status: selectedTraceNode.status,
                      input_summary: selectedTraceNode.input_summary || {},
                      output: selectedNodeOutput || {},
                      duration_ms: selectedTraceNode.duration_ms || 0,
                    } : {
                      status: selectedNode.status || "pending",
                      input_summary: {},
                      output: {},
                    }}
                    maxHeight={230}
                  />
                </>
              ) : (
                <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                  노드 정보 없음
                </div>
              )}
            </div>
          </div>
        </Panel>

        <Panel
          title="Test prompt"
          subtitle={result ? `${result.unit_ai} · ${result.run_id}` : (busy ? "running" : "")}
          right={<Pill tone={toneForStatus(result?.status)}>{result?.status || (loading ? "loading" : "ready")}</Pill>}
        >
          <div style={{ display: "grid", gap: 10 }}>
            <Textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={4}
              placeholder="예: product: PRODA lot: R1000 module: GATE note: IOFF drift to alice@example.test"
            />
            <Field label="session_id">
              <input
                value={sessionId}
                onChange={(e) => setSessionId(e.target.value)}
                placeholder="새 session은 비워두세요"
                style={{ width: "100%", padding: "8px 10px", border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", borderRadius: 4 }}
              />
            </Field>
            <div style={{ display: "grid", gap: 6 }}>
              <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>debug request</div>
              <JsonBlock value={debugRequest} maxHeight={120} />
            </div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <Button variant="primary" onClick={() => runAction("continue")} disabled={!canContinue || busy}>
                {busy ? "실행 중" : "실행"}
              </Button>
              <Button variant="primary" onClick={() => runAction("confirm")} disabled={!canConfirm || busy}>
                승인 후 등록
              </Button>
              <Button variant="ghost" onClick={() => runAction("cancel")} disabled={!sessionId || busy}>
                취소
              </Button>
            </div>
            {result ? (
              <div style={{ borderTop: "1px solid var(--border)", paddingTop: 10, display: "grid", gap: 6 }}>
                <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                  <strong style={{ fontSize: 12 }}>결과</strong>
                  <Pill tone={result.requires_confirmation ? "warn" : toneForStatus(result.status)}>
                    {result.requires_confirmation ? "confirm 필요" : (result.status || "done")}
                  </Pill>
                  {result.created_inform?.id ? <Pill tone="ok">{result.created_inform.id}</Pill> : null}
                </div>
                <UnitAnswerFeedback feedback={feedback} runId={result?.run_id || ""} />
                {result.answer ? (
                  <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.45 }}>
                    {result.answer}
                  </div>
                ) : null}
                {result.question ? (
                  <Banner tone={result.missing?.length ? "warn" : "neutral"}>{result.question}</Banner>
                ) : null}
                {result.human_review ? (
                  <div style={{ display: "grid", gap: 6, padding: 8, border: "1px solid var(--border)", background: "var(--bg-primary)" }}>
                    <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                      <strong style={{ fontSize: 12 }}>Human review</strong>
                      <Pill tone={result.human_review.action_required ? "warn" : toneForStatus(result.human_review.approval_status)}>
                        {result.human_review.approval_status || "pending"}
                      </Pill>
                      <Pill tone={result.human_review.can_confirm ? "ok" : "neutral"}>
                        can_confirm={String(!!result.human_review.can_confirm)}
                      </Pill>
                    </div>
                    <JsonBlock
                      value={{
                        approval_status: result.human_review.approval_status || "",
                        action_required: !!result.human_review.action_required,
                        can_confirm: !!result.human_review.can_confirm,
                        required_values: result.human_review.required_values || {},
                        missing: result.human_review.missing || [],
                      }}
                      maxHeight={140}
                    />
                  </div>
                ) : null}
                <JsonBlock
                  value={{
                    missing: result.missing || [],
                    slots: result.slots || {},
                    draft: result.draft || {},
                    human_review: result.human_review || {},
                    mail_draft: result.draft?.mail_draft || {},
                    requires_confirmation: !!result.requires_confirmation,
                    created_inform: result.created_inform || {},
                    warnings: result.warnings || [],
                  }}
                  maxHeight={260}
                />
              </div>
            ) : null}
          </div>
        </Panel>
      </div>
    </div>
  );
}

function ChangeManagementUnitPanel() {
  const [graph, setGraph] = useState(null);
  const [prompt, setPrompt] = useState("");
  const [meetingId, setMeetingId] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [graphErr, setGraphErr] = useState("");
  const [result, setResult] = useState(null);
  const [lastRequest, setLastRequest] = useState(null);
  const [selectedNodeId, setSelectedNodeId] = useState(null);
  const [history, setHistory] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [selectedHistoryId, setSelectedHistoryId] = useState("");
  const feedback = useAgentFeedbackProfile("change_management");

  const loadHistory = () => {
    setHistoryLoading(true);
    return sf(agentUnitHistoryEndpoint("change_management"))
      .then((payload) => {
        const nextHistory = payload?.history || [];
        setHistory(nextHistory);
        if (!selectedHistoryId && nextHistory[0]?.history_id) setSelectedHistoryId(nextHistory[0].history_id);
      })
      .catch(() => setHistory([]))
      .finally(() => setHistoryLoading(false));
  };

  useEffect(() => {
    setLoading(true);
    Promise.all([
      sf(agentUnitGraphEndpoint("change_management")).catch((e) => ({
        error: formatAgentEndpointError(e, agentUnitGraphEndpoint("change_management")),
      })),
      sf(agentUnitHistoryEndpoint("change_management")).catch(() => ({ history: [] })),
    ]).then(([graphPayload, historyPayload]) => {
      if (graphPayload?.error) {
        setGraphErr(graphPayload.error);
        setGraph(null);
      } else {
        setGraphErr("");
        setGraph(graphPayload?.graph || null);
      }
      const nextHistory = historyPayload?.history || [];
      setHistory(nextHistory);
      if (!selectedHistoryId && nextHistory[0]?.history_id) setSelectedHistoryId(nextHistory[0].history_id);
    }).catch((e) => setErr(e.message || String(e)))
      .finally(() => setLoading(false));
  }, []);

  const activeGraph = result?.graph || graph || EMPTY_GRAPH;
  const graphNodes = activeGraph?.nodes || [];
  const firstGraphNodeId = graphNodes[0]?.id || null;
  const currentSelectedNodeId = selectedNodeId || firstGraphNodeId;
  const trace = result?.trace || [];

  useEffect(() => {
    if (trace.length) setSelectedNodeId(trace[trace.length - 1]?.node_id || null);
  }, [result?.run_id]);

  useEffect(() => {
    if (!selectedNodeId && firstGraphNodeId) setSelectedNodeId(firstGraphNodeId);
  }, [selectedNodeId, firstGraphNodeId]);

  const selectedIdx = currentSelectedNodeId
    ? trace.findIndex((row) => row.node_id === currentSelectedNodeId)
    : -1;
  const selectedTraceNode = selectedIdx >= 0 ? trace[selectedIdx] : null;
  const selectedGraphNode = graphNodes.find((node) => node.id === currentSelectedNodeId) || graphNodes[0] || null;
  const selectedNode = selectedTraceNode
    ? {
      ...selectedGraphNode,
      ...selectedTraceNode,
      id: selectedGraphNode?.id || selectedTraceNode.node_id,
      node_id: selectedTraceNode.node_id || selectedGraphNode?.id,
      persona: selectedGraphNode?.persona || selectedTraceNode.persona || "",
      prompt: selectedGraphNode?.prompt || selectedTraceNode.prompt || {},
      state_io: selectedGraphNode?.state_io || selectedTraceNode.state_io || {},
      reads: selectedGraphNode?.reads || selectedTraceNode.reads || [],
      writes: selectedGraphNode?.writes || selectedTraceNode.writes || [],
      shared_state: selectedGraphNode?.shared_state || selectedTraceNode.shared_state || [],
      answer_attach_rule: selectedGraphNode?.answer_attach_rule || selectedTraceNode.answer_attach_rule || "",
    }
    : (selectedGraphNode ? { ...selectedGraphNode, node_id: selectedGraphNode.id } : null);
  const accumulatedState = useMemo(
    () => buildAccumulatedState(result, lastRequest, selectedIdx >= 0 ? selectedIdx : undefined, activeGraph),
    [result, lastRequest, selectedIdx, activeGraph]
  );
  const stateDesign = activeGraph?.state_design || {};
  const stateValue = trace.length ? accumulatedState : stateDesign;
  const stateSubtitle = selectedTraceNode
    ? `up to ${selectedTraceNode.label || selectedTraceNode.node_id}`
    : (trace.length ? "final state" : "");
  const graphSubtitle = trace.length
    ? `${trace.length}/${graphNodes.length} nodes · click to inspect`
    : "";
  const selectedNodeOutput = compactRowsPayload(selectedTraceNode?.output);
  const selectedPromptSystem = selectedNode?.prompt?.system || "";
  const selectedPromptMode = selectedNode?.prompt?.mode || "deterministic";
  const selectedStateIo = selectedNode?.state_io || {
    reads: selectedNode?.reads || [],
    writes: selectedNode?.writes || [],
  };

  const selectedHistory = useMemo(() => (
    history.find((item) => item.history_id === selectedHistoryId) || history[0] || null
  ), [history, selectedHistoryId]);
  const historyPrompt = (item) => item?.prompt || item?.natural_language || "";
  const debugRequest = {
    prompt: prompt.trim(),
    meeting_id: meetingId.trim(),
    session_id: sessionId.trim(),
  };
  const canRun = !!prompt.trim();

  const run = () => {
    if (!canRun) return;
    const body = {
      prompt: prompt.trim(),
      meeting_id: meetingId.trim(),
      session_id: sessionId.trim(),
    };
    setBusy(true);
    setErr("");
    setResult(null);
    setLastRequest(body);
    postJson(agentUnitRunEndpoint("change_management"), body)
      .then((payload) => {
        setResult(payload);
        loadHistory();
      })
      .catch((e) => setErr(e.message || String(e)))
      .finally(() => setBusy(false));
  };

  const replayHistory = (item) => {
    setPrompt(historyPrompt(item));
    setMeetingId(item?.meeting_reference?.focus_meeting_id || item?.meeting?.id || "");
    setSessionId("");
    setSelectedHistoryId(item?.history_id || "");
    setErr("");
  };

  return (
    <div style={{ display: "grid", gap: 10 }}>
      {err && <Banner tone="bad" onClose={() => setErr("")}>{err}</Banner>}
      {graphErr && (
        <Banner tone="warn" onClose={() => setGraphErr("")}>
          변경점관리 graph fetch 진단 — 기본 노드 구조로 표시: {graphErr}
        </Banner>
      )}
      <UnitFeedbackStatus feedback={feedback} />
      <Panel
        title="질문 이력"
        subtitle={historyLoading ? "loading" : `${history.length} items`}
        right={<Button variant="ghost" onClick={loadHistory} disabled={historyLoading} style={{ fontSize: 11, padding: "2px 8px", height: 24 }}>새로고침</Button>}
      >
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.05fr) minmax(320px, 0.95fr)", gap: 10, alignItems: "start" }}>
          <div style={{ maxHeight: 230, overflow: "auto", border: "1px solid var(--border)", background: "var(--bg-primary)" }}>
            {history.length ? history.map((item) => {
              const active = (item.history_id || "") === (selectedHistory?.history_id || "");
              return (
                <button
                  type="button"
                  key={item.history_id || `${item.timestamp}:${historyPrompt(item)}`}
                  onClick={() => setSelectedHistoryId(item.history_id || "")}
                  style={{
                    display: "grid",
                    gap: 3,
                    width: "100%",
                    textAlign: "left",
                    justifyContent: "stretch",
                    justifyItems: "stretch",
                    alignItems: "start",
                    padding: "8px 9px",
                    border: 0,
                    borderBottom: "1px solid var(--border)",
                    background: active ? "var(--bg-tertiary)" : "transparent",
                    color: "var(--text-primary)",
                    cursor: "pointer",
                  }}
                >
                  <span style={{ width: "100%", textAlign: "left", fontSize: 12, fontWeight: 700, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {historyPrompt(item) || "(empty)"}
                  </span>
                  <span style={{ width: "100%", textAlign: "left", fontSize: 11, color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {historyActorLabel(item)} · {historyTimestampLabel(item)}
                  </span>
                  <span style={{ width: "100%", textAlign: "left", fontSize: 11, color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {item.status || "status"} · {item.meeting_reference?.focus_meeting_title || item.meeting?.title || "범위 자동"}
                  </span>
                </button>
              );
            }) : (
              <div style={{ padding: 12, fontSize: 12, color: "var(--text-secondary)" }}>
                저장된 변경점관리 질문 이력이 없습니다.
              </div>
            )}
          </div>
          <div style={{ display: "grid", gap: 8 }}>
            {selectedHistory ? (
              <>
                <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                  <Pill tone={toneForStatus(selectedHistory.status)}>{selectedHistory.status || "history"}</Pill>
                  <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                    {historyActorLabel(selectedHistory)} · {historyTimestampLabel(selectedHistory)}
                  </span>
                  <Button
                    variant="primary"
                    onClick={() => replayHistory(selectedHistory)}
                    style={{ marginLeft: "auto", fontSize: 12, padding: "4px 10px", height: 28 }}
                  >재현</Button>
                </div>
                <div style={{ textAlign: "left", fontSize: 12, fontWeight: 700, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {historyPrompt(selectedHistory) || "(empty)"}
                </div>
                <UnitAnswerFeedback
                  feedback={feedback}
                  runId={selectedHistory.run_id || selectedHistory.history_id || ""}
                  reason="agent_unit_history"
                />
                {selectedHistory.answer ? (
                  <div style={{ textAlign: "left", fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.45, whiteSpace: "pre-wrap" }}>
                    {selectedHistory.answer}
                  </div>
                ) : null}
                <JsonBlock
                  value={{
                    meeting_reference: selectedHistory.meeting_reference || {},
                    sources: selectedHistory.sources || [],
                    calendar_events: selectedHistory.calendar_events || [],
                    llm: selectedHistory.llm || {},
                    warnings: selectedHistory.warnings || [],
                  }}
                  maxHeight={190}
                />
              </>
            ) : (
              <div style={{ padding: 12, fontSize: 12, color: "var(--text-secondary)", border: "1px solid var(--border)", background: "var(--bg-primary)" }}>
                이력을 선택하면 답변과 근거 요약을 확인할 수 있습니다.
              </div>
            )}
          </div>
        </div>
      </Panel>

      <div className="flow-agent-unit-grid">
        <Panel title="State" subtitle={stateSubtitle}>
          <div style={{ display: "grid", gap: 8 }}>
            <JsonBlock value={stateValue} maxHeight={trace.length ? 520 : 620} />
            {trace.length && Object.keys(stateDesign).length ? (
              <details style={{ border: "1px solid var(--border)", background: "var(--bg-primary)" }}>
                <summary style={{ padding: "6px 8px", fontSize: 11, color: "var(--text-secondary)", cursor: "pointer", background: "var(--bg-tertiary)" }}>
                  state_design
                </summary>
                <JsonBlock value={stateDesign} maxHeight={220} />
              </details>
            ) : null}
          </div>
        </Panel>

        <Panel title="LangGraph" subtitle={graphSubtitle}>
          <div className="flow-agent-node-grid">
            <RuntimeGraph graph={activeGraph} selectedId={currentSelectedNodeId} onSelect={setSelectedNodeId} />
            <div style={{ display: "grid", gap: 8 }}>
              {selectedNode ? (
                <>
                  <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                    <strong style={{ fontSize: 13 }}>{selectedNode.label || selectedNode.node_id}</strong>
                    <Pill tone={toneForStatus(selectedNode.status)}>{selectedNode.status || "pending"}</Pill>
                    {selectedTraceNode ? (
                      <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>{selectedTraceNode.duration_ms || 0} ms</span>
                    ) : null}
                  </div>
                  <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>{selectedNode.node_id}</span>
                  <NodeFeedbackInline
                    feedback={feedback}
                    nodeId={selectedNode.node_id}
                    runId={result?.run_id || ""}
                    fallback={selectedNode}
                  />
                  {(selectedTraceNode?.warnings || []).length ? (
                    <Banner tone="warn">{(selectedTraceNode.warnings || []).join(" / ")}</Banner>
                  ) : null}
                  <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>Persona</div>
                  <JsonBlock
                    value={{
                      persona: selectedNode.persona || "",
                      prompt: {
                        mode: selectedPromptMode,
                        system: selectedPromptSystem,
                      },
                      answer_attach_rule: selectedNode.answer_attach_rule || "",
                    }}
                    maxHeight={selectedPromptSystem ? 220 : 140}
                  />
                  <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>State I/O</div>
                  <JsonBlock
                    value={{
                      reads: selectedStateIo.reads || selectedNode.reads || [],
                      writes: selectedStateIo.writes || selectedNode.writes || [],
                    }}
                    maxHeight={150}
                  />
                  <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>공유 state</div>
                  <JsonBlock value={selectedNode.shared_state || []} maxHeight={120} />
                  <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>실행 결과</div>
                  <JsonBlock
                    value={selectedTraceNode ? {
                      status: selectedTraceNode.status,
                      input_summary: selectedTraceNode.input_summary || {},
                      output: selectedNodeOutput || {},
                      duration_ms: selectedTraceNode.duration_ms || 0,
                    } : {
                      status: selectedNode.status || "pending",
                      input_summary: {},
                      output: {},
                    }}
                    maxHeight={230}
                  />
                </>
              ) : (
                <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                  노드 정보 없음
                </div>
              )}
            </div>
          </div>
        </Panel>

        <Panel
          title="Test prompt"
          subtitle={result ? `${result.unit_ai} · ${result.run_id}` : (busy ? "running" : "")}
          right={<Pill tone={toneForStatus(result?.status)}>{result?.status || (loading ? "loading" : "ready")}</Pill>}
        >
          <div style={{ display: "grid", gap: 10 }}>
            <Textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={4}
              placeholder="예: Device Change Sync 회의 액션아이템과 결정사항 정리해줘"
            />
            <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", gap: 8 }}>
              <Field label="meeting_id">
                <input
                  value={meetingId}
                  onChange={(e) => setMeetingId(e.target.value)}
                  placeholder="선택"
                  style={{ width: "100%", padding: "8px 10px", border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", borderRadius: 4 }}
                />
              </Field>
              <Field label="session_id">
                <input
                  value={sessionId}
                  onChange={(e) => setSessionId(e.target.value)}
                  placeholder="선택"
                  style={{ width: "100%", padding: "8px 10px", border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", borderRadius: 4 }}
                />
              </Field>
            </div>
            <div style={{ display: "grid", gap: 6 }}>
              <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>debug request</div>
              <JsonBlock value={debugRequest} maxHeight={120} />
            </div>
            <Button variant="primary" onClick={run} disabled={!canRun || busy}>
              {busy ? "실행 중" : "실행"}
            </Button>
            {result ? (
              <div style={{ borderTop: "1px solid var(--border)", paddingTop: 10, display: "grid", gap: 6 }}>
                <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                  <strong style={{ fontSize: 12 }}>결과</strong>
                  <Pill tone={result.needs_clarification ? "warn" : toneForStatus(result.status)}>
                    {result.needs_clarification ? "확인 필요" : (result.status || "done")}
                  </Pill>
                  {result.meeting_reference?.focus_meeting_title ? <Pill tone="neutral">{result.meeting_reference.focus_meeting_title}</Pill> : null}
                </div>
                <UnitAnswerFeedback feedback={feedback} runId={result?.run_id || ""} />
                {result.answer ? (
                  <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.5, whiteSpace: "pre-wrap" }}>
                    {result.answer}
                  </div>
                ) : null}
                <JsonBlock
                  value={{
                    meeting_reference: result.meeting_reference || {},
                    sources: result.sources || [],
                    calendar_events: result.calendar_events || [],
                    llm: result.llm || {},
                    warnings: result.warnings || [],
                  }}
                  maxHeight={260}
                />
              </div>
            ) : null}
          </div>
        </Panel>
      </div>
    </div>
  );
}

function DashboardAgentUnitPanel() {
  const [graph, setGraph] = useState(null);
  const [prompt, setPrompt] = useState("wafer별 IOFF 산점도 그려줘");
  const [columnsText, setColumnsText] = useState("wafer_id, IOFF, lot_id");
  const [rowsText, setRowsText] = useState('[{"wafer_id":1,"IOFF":0.12,"lot_id":"A1000"},{"wafer_id":2,"IOFF":0.2,"lot_id":"A1000"}]');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [graphErr, setGraphErr] = useState("");
  const [result, setResult] = useState(null);
  const [lastRequest, setLastRequest] = useState(null);
  const [selectedNodeId, setSelectedNodeId] = useState(null);
  const [history, setHistory] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [selectedHistoryId, setSelectedHistoryId] = useState("");
  const feedback = useAgentFeedbackProfile("dashboard_agent");

  const loadHistory = () => {
    setHistoryLoading(true);
    return sf(agentUnitHistoryEndpoint("dashboard_agent"))
      .then((payload) => {
        const nextHistory = payload?.history || [];
        setHistory(nextHistory);
        if (!selectedHistoryId && nextHistory[0]?.history_id) setSelectedHistoryId(nextHistory[0].history_id);
      })
      .catch(() => setHistory([]))
      .finally(() => setHistoryLoading(false));
  };

  useEffect(() => {
    setLoading(true);
    Promise.all([
      sf(agentUnitGraphEndpoint("dashboard_agent")).catch((e) => ({
        error: formatAgentEndpointError(e, agentUnitGraphEndpoint("dashboard_agent")),
      })),
      sf(agentUnitHistoryEndpoint("dashboard_agent")).catch(() => ({ history: [] })),
    ]).then(([graphPayload, historyPayload]) => {
      if (graphPayload?.error) {
        setGraph(null);
        setGraphErr(graphPayload.error);
      } else {
        setGraph(graphPayload?.graph || null);
        setGraphErr("");
      }
      const nextHistory = historyPayload?.history || [];
      setHistory(nextHistory);
      if (!selectedHistoryId && nextHistory[0]?.history_id) setSelectedHistoryId(nextHistory[0].history_id);
    }).catch((e) => setErr(e.message || String(e)))
      .finally(() => setLoading(false));
  }, []);

  const activeGraph = result?.graph || graph || EMPTY_GRAPH;
  const graphNodes = activeGraph?.nodes || [];
  const firstGraphNodeId = graphNodes[0]?.id || null;
  const currentSelectedNodeId = selectedNodeId || firstGraphNodeId;
  const trace = result?.trace || [];

  useEffect(() => {
    if (trace.length) setSelectedNodeId(trace[trace.length - 1]?.node_id || null);
  }, [result?.run_id]);

  useEffect(() => {
    if (!selectedNodeId && firstGraphNodeId) setSelectedNodeId(firstGraphNodeId);
  }, [selectedNodeId, firstGraphNodeId]);

  const selectedIdx = currentSelectedNodeId
    ? trace.findIndex((row) => row.node_id === currentSelectedNodeId)
    : -1;
  const selectedTraceNode = selectedIdx >= 0 ? trace[selectedIdx] : null;
  const selectedGraphNode = graphNodes.find((node) => node.id === currentSelectedNodeId) || graphNodes[0] || null;
  const selectedNode = selectedTraceNode
    ? {
      ...selectedGraphNode,
      ...selectedTraceNode,
      id: selectedGraphNode?.id || selectedTraceNode.node_id,
      node_id: selectedTraceNode.node_id || selectedGraphNode?.id,
      persona: selectedGraphNode?.persona || selectedTraceNode.persona || "",
      prompt: selectedGraphNode?.prompt || selectedTraceNode.prompt || {},
      state_io: selectedGraphNode?.state_io || selectedTraceNode.state_io || {},
      shared_state: selectedGraphNode?.shared_state || selectedTraceNode.shared_state || [],
      answer_attach_rule: selectedGraphNode?.answer_attach_rule || selectedTraceNode.answer_attach_rule || "",
    }
    : (selectedGraphNode ? { ...selectedGraphNode, node_id: selectedGraphNode.id } : null);
  const accumulatedState = useMemo(
    () => buildAccumulatedState(result, lastRequest, selectedIdx >= 0 ? selectedIdx : undefined, activeGraph),
    [result, lastRequest, selectedIdx, activeGraph]
  );
  const stateDesign = activeGraph?.state_design || {};
  const stateValue = trace.length ? accumulatedState : stateDesign;
  const selectedNodeOutput = compactRowsPayload(selectedTraceNode?.output);
  const selectedPromptSystem = selectedNode?.prompt?.system || "";
  const selectedPromptMode = selectedNode?.prompt?.mode || "deterministic";
  const selectedStateIo = selectedNode?.state_io || {};
  const selectedHistory = useMemo(() => (
    history.find((item) => item.history_id === selectedHistoryId) || history[0] || null
  ), [history, selectedHistoryId]);
  const historyPrompt = (item) => item?.prompt || item?.natural_language || "";

  const parseColumns = () => {
    const raw = columnsText.trim();
    if (!raw) return [];
    try {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) return parsed.map((item) => String(item || "").trim()).filter(Boolean);
    } catch {
      // fall through to comma parsing
    }
    return raw.split(/[,;\n]+/).map((item) => item.trim()).filter(Boolean);
  };

  const parseRows = () => {
    const raw = rowsText.trim();
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) throw new Error("sample_rows JSON은 array여야 합니다.");
    return parsed.filter((row) => row && typeof row === "object" && !Array.isArray(row));
  };

  const debugRequest = useMemo(() => {
    let rows = [];
    try { rows = parseRows(); } catch { rows = []; }
    return {
      natural_language: prompt.trim(),
      columns: parseColumns(),
      sample_rows: rows.slice(0, 3),
    };
  }, [prompt, columnsText, rowsText]);

  const run = () => {
    if (!prompt.trim()) return;
    let rows = [];
    try {
      rows = parseRows();
    } catch (e) {
      setErr(e.message || String(e));
      return;
    }
    const body = {
      natural_language: prompt.trim(),
      columns: parseColumns(),
      sample_rows: rows,
    };
    setBusy(true);
    setErr("");
    setResult(null);
    setLastRequest(body);
    postJson(agentUnitRunEndpoint("dashboard_agent"), body)
      .then((payload) => {
        setResult(payload);
        loadHistory();
      })
      .catch((e) => setErr(e.message || String(e)))
      .finally(() => setBusy(false));
  };

  const replayHistory = (item) => {
    setPrompt(historyPrompt(item));
    if (Array.isArray(item?.columns)) {
      setColumnsText(item.columns.join(", "));
    }
    setSelectedHistoryId(item?.history_id || "");
    setResult(null);
    setLastRequest(null);
    setSelectedNodeId(null);
    setErr("");
  };

  return (
    <div style={{ display: "grid", gap: 10 }}>
      {err && <Banner tone="bad" onClose={() => setErr("")}>{err}</Banner>}
      {graphErr && <Banner tone="warn" onClose={() => setGraphErr("")}>Dashboard graph fetch 진단: {graphErr}</Banner>}
      <Panel
        title="질문 이력"
        subtitle={historyLoading ? "loading" : `${history.length} items`}
        right={<Button variant="ghost" onClick={loadHistory} disabled={historyLoading} style={{ fontSize: 11, padding: "2px 8px", height: 24 }}>새로고침</Button>}
      >
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.05fr) minmax(320px, 0.95fr)", gap: 10, alignItems: "start" }}>
          <div style={{ maxHeight: 230, overflow: "auto", border: "1px solid var(--border)", background: "var(--bg-primary)" }}>
            {history.length ? history.map((item) => {
              const active = (item.history_id || "") === (selectedHistory?.history_id || "");
              return (
                <button
                  type="button"
                  key={item.history_id || `${item.timestamp}:${historyPrompt(item)}`}
                  onClick={() => setSelectedHistoryId(item.history_id || "")}
                  style={{
                    display: "grid",
                    gap: 3,
                    width: "100%",
                    textAlign: "left",
                    justifyContent: "stretch",
                    justifyItems: "stretch",
                    alignItems: "start",
                    padding: "8px 9px",
                    border: 0,
                    borderBottom: "1px solid var(--border)",
                    background: active ? "var(--bg-tertiary)" : "transparent",
                    color: "var(--text-primary)",
                    cursor: "pointer",
                  }}
                >
                  <span style={{ width: "100%", textAlign: "left", fontSize: 12, fontWeight: 700, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {historyPrompt(item) || "(empty)"}
                  </span>
                  <span style={{ width: "100%", textAlign: "left", fontSize: 11, color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {historyActorLabel(item)} · {historyTimestampLabel(item)}
                  </span>
                  <span style={{ width: "100%", textAlign: "left", fontSize: 11, color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {item.status || "status"} · {item.chart_summary?.chart_type || item.chart_type || "chart"} · {(item.columns || []).length} cols
                  </span>
                </button>
              );
            }) : (
              <div style={{ padding: 12, fontSize: 12, color: "var(--text-secondary)" }}>
                저장된 Dashboard 질문 이력이 없습니다.
              </div>
            )}
          </div>
          <div style={{ display: "grid", gap: 8 }}>
            {selectedHistory ? (
              <>
                <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                  <Pill tone={toneForStatus(selectedHistory.status)}>{selectedHistory.status || "history"}</Pill>
                  <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                    {historyActorLabel(selectedHistory)} · {historyTimestampLabel(selectedHistory)}
                  </span>
                  <Button
                    variant="primary"
                    onClick={() => replayHistory(selectedHistory)}
                    style={{ marginLeft: "auto", fontSize: 12, padding: "4px 10px", height: 28 }}
                  >재현</Button>
                </div>
                <div style={{ textAlign: "left", fontSize: 12, fontWeight: 700, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {historyPrompt(selectedHistory) || "(empty)"}
                </div>
                <JsonBlock
                  value={{
                    columns: selectedHistory.columns || [],
                    run_metadata: selectedHistory.run_metadata || {},
                    chart_summary: selectedHistory.chart_summary || {},
                    warnings: selectedHistory.warnings || [],
                    trace_summary: selectedHistory.trace_summary || [],
                  }}
                  maxHeight={190}
                />
              </>
            ) : (
              <div style={{ padding: 12, fontSize: 12, color: "var(--text-secondary)", border: "1px solid var(--border)", background: "var(--bg-primary)" }}>
                저장된 Dashboard 질문 이력이 없습니다.
              </div>
            )}
          </div>
        </div>
      </Panel>
      <UnitFeedbackStatus feedback={feedback} />

      <div className="flow-agent-unit-grid">
        <Panel title="State" subtitle={selectedTraceNode ? `up to ${selectedTraceNode.label || selectedTraceNode.node_id}` : ""}>
          <JsonBlock value={stateValue} maxHeight={trace.length ? 520 : 620} />
        </Panel>

        <Panel title="LangGraph" subtitle={trace.length ? `${trace.length}/${graphNodes.length} nodes · click to inspect` : ""}>
          <div className="flow-agent-node-grid">
            <RuntimeGraph graph={activeGraph} selectedId={currentSelectedNodeId} onSelect={setSelectedNodeId} />
            <div style={{ display: "grid", gap: 8 }}>
              {selectedNode ? (
                <>
                  <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                    <strong style={{ fontSize: 13 }}>{selectedNode.label || selectedNode.node_id}</strong>
                    <Pill tone={toneForStatus(selectedNode.status)}>{selectedNode.status || "pending"}</Pill>
                    {selectedTraceNode ? <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>{selectedTraceNode.duration_ms || 0} ms</span> : null}
                  </div>
                  <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>{selectedNode.node_id}</span>
                  <NodeFeedbackInline
                    feedback={feedback}
                    nodeId={selectedNode.node_id}
                    runId={result?.run_id || ""}
                    fallback={selectedNode}
                  />
                  {(selectedTraceNode?.warnings || []).length ? <Banner tone="warn">{(selectedTraceNode.warnings || []).join(" / ")}</Banner> : null}
                  <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>Persona</div>
                  <JsonBlock value={{ persona: selectedNode.persona || "", prompt: { mode: selectedPromptMode, system: selectedPromptSystem }, answer_attach_rule: selectedNode.answer_attach_rule || "" }} maxHeight={selectedPromptSystem ? 220 : 140} />
                  <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>State I/O</div>
                  <JsonBlock value={{ reads: selectedStateIo.reads || [], writes: selectedStateIo.writes || [] }} maxHeight={150} />
                  <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>실행 결과</div>
                  <JsonBlock value={selectedTraceNode ? { status: selectedTraceNode.status, input_summary: selectedTraceNode.input_summary || {}, output: selectedNodeOutput || {}, duration_ms: selectedTraceNode.duration_ms || 0 } : { status: selectedNode.status || "pending" }} maxHeight={230} />
                </>
              ) : <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>노드 정보 없음</div>}
            </div>
          </div>
        </Panel>

        <Panel
          title="Test prompt"
          subtitle={result ? `${result.unit_ai} · ${result.run_id}` : (busy ? "running" : "")}
          right={<Pill tone={toneForStatus(result?.status)}>{result?.status || (loading ? "loading" : "ready")}</Pill>}
        >
          <div style={{ display: "grid", gap: 10 }}>
            <Textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={3} />
            <Field label="columns">
              <Textarea value={columnsText} onChange={(e) => setColumnsText(e.target.value)} rows={2} />
            </Field>
            <Field label="sample_rows">
              <Textarea value={rowsText} onChange={(e) => setRowsText(e.target.value)} rows={5} />
            </Field>
            <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>debug request</div>
            <JsonBlock value={debugRequest} maxHeight={130} />
            <Button variant="primary" onClick={run} disabled={!prompt.trim() || busy}>{busy ? "실행 중" : "실행"}</Button>
            {result?.chart_result ? (
              <div style={{ borderTop: "1px solid var(--border)", paddingTop: 10, display: "grid", gap: 6 }}>
                <UnitAnswerFeedback feedback={feedback} runId={result?.run_id || ""} />
                <JsonBlock
                  value={{
                    data_context: result.data_context || {},
                    spec: result.spec || {},
                    chart_type: result.chart_result.chart_type,
                    config: result.chart_result.chart_config || result.chart_result.config || {},
                    total: result.chart_result.total,
                    chart_result_preview: result.chart_result_preview || {},
                    warnings: result.warnings || [],
                  }}
                  maxHeight={220}
                />
              </div>
            ) : null}
          </div>
        </Panel>
      </div>
    </div>
  );
}

function DeterministicLookupUnitPanel({ unitKey, title, defaultPrompt }) {
  const [graph, setGraph] = useState(null);
  const [prompt, setPrompt] = useState(defaultPrompt || "");
  const [product, setProduct] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [graphErr, setGraphErr] = useState("");
  const [result, setResult] = useState(null);
  const [lastRequest, setLastRequest] = useState(null);
  const [history, setHistory] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [selectedHistoryId, setSelectedHistoryId] = useState("");
  const [selectedNodeId, setSelectedNodeId] = useState(null);
  const feedback = useAgentFeedbackProfile(unitKey);

  const loadHistory = () => {
    setHistoryLoading(true);
    return sf(agentUnitHistoryEndpoint(unitKey))
      .then((payload) => {
        const nextHistory = payload?.history || [];
        setHistory(nextHistory);
        if (!selectedHistoryId && nextHistory[0]?.history_id) setSelectedHistoryId(nextHistory[0].history_id);
      })
      .catch(() => setHistory([]))
      .finally(() => setHistoryLoading(false));
  };

  useEffect(() => {
    setLoading(true);
    Promise.all([
      sf(agentUnitGraphEndpoint(unitKey)).catch((e) => ({
        error: formatAgentEndpointError(e, agentUnitGraphEndpoint(unitKey)),
      })),
      sf(agentUnitHistoryEndpoint(unitKey)).catch(() => ({ history: [] })),
    ]).then(([graphPayload, historyPayload]) => {
      if (graphPayload?.error) {
        setGraph(null);
        setGraphErr(graphPayload.error);
      } else {
        setGraph(graphPayload?.graph || null);
        setGraphErr("");
      }
      const nextHistory = historyPayload?.history || [];
      setHistory(nextHistory);
      if (!selectedHistoryId && nextHistory[0]?.history_id) setSelectedHistoryId(nextHistory[0].history_id);
    }).catch((e) => setErr(e.message || String(e)))
      .finally(() => setLoading(false));
  }, [unitKey]);

  const activeGraph = result?.graph || graph || EMPTY_GRAPH;
  const graphNodes = activeGraph?.nodes || [];
  const firstGraphNodeId = graphNodes[0]?.id || null;
  const currentSelectedNodeId = selectedNodeId || firstGraphNodeId;
  const trace = result?.trace || [];

  useEffect(() => {
    if (trace.length) setSelectedNodeId(trace[trace.length - 1]?.node_id || null);
  }, [result?.run_id]);

  useEffect(() => {
    if (!selectedNodeId && firstGraphNodeId) setSelectedNodeId(firstGraphNodeId);
  }, [selectedNodeId, firstGraphNodeId]);

  const selectedIdx = currentSelectedNodeId
    ? trace.findIndex((row) => row.node_id === currentSelectedNodeId)
    : -1;
  const selectedTraceNode = selectedIdx >= 0 ? trace[selectedIdx] : null;
  const selectedGraphNode = graphNodes.find((node) => node.id === currentSelectedNodeId) || graphNodes[0] || null;
  const selectedNode = selectedTraceNode
    ? {
      ...selectedGraphNode,
      ...selectedTraceNode,
      id: selectedGraphNode?.id || selectedTraceNode.node_id,
      node_id: selectedTraceNode.node_id || selectedGraphNode?.id,
      persona: selectedGraphNode?.persona || selectedTraceNode.persona || "",
      prompt: selectedGraphNode?.prompt || selectedTraceNode.prompt || {},
      state_io: selectedGraphNode?.state_io || selectedTraceNode.state_io || {},
      shared_state: selectedGraphNode?.shared_state || selectedTraceNode.shared_state || [],
      answer_attach_rule: selectedGraphNode?.answer_attach_rule || selectedTraceNode.answer_attach_rule || "",
    }
    : (selectedGraphNode ? { ...selectedGraphNode, node_id: selectedGraphNode.id } : null);
  const accumulatedState = useMemo(
    () => buildAccumulatedState(result, lastRequest, selectedIdx >= 0 ? selectedIdx : undefined, activeGraph),
    [result, lastRequest, selectedIdx, activeGraph]
  );
  const stateValue = trace.length ? accumulatedState : (activeGraph?.state_design || {});
  const selectedNodeOutput = compactRowsPayload(selectedTraceNode?.output);
  const selectedStateIo = selectedNode?.state_io || {};
  const selectedHistory = useMemo(() => (
    history.find((item) => item.history_id === selectedHistoryId) || history[0] || null
  ), [history, selectedHistoryId]);

  const run = () => {
    if (!prompt.trim()) return;
    const body = { prompt: prompt.trim(), product: product.trim() };
    setBusy(true);
    setErr("");
    setResult(null);
    setLastRequest(body);
    postJson(agentUnitRunEndpoint(unitKey), body)
      .then((payload) => {
        setResult(payload);
        loadHistory();
      })
      .catch((e) => setErr(e.message || String(e)))
      .finally(() => setBusy(false));
  };

  const replayHistory = (item) => {
    setPrompt(item?.prompt || item?.natural_language || "");
    setProduct(item?.product || "");
    setSelectedHistoryId(item?.history_id || "");
    setResult(null);
    setLastRequest(null);
    setSelectedNodeId(null);
    setErr("");
  };

  return (
    <div style={{ display: "grid", gap: 10 }}>
      {err && <Banner tone="bad" onClose={() => setErr("")}>{err}</Banner>}
      {graphErr && <Banner tone="warn" onClose={() => setGraphErr("")}>{title} graph fetch 진단: {graphErr}</Banner>}
      <UnitFeedbackStatus feedback={feedback} />
      <Panel
        title="질문 이력"
        subtitle={historyLoading ? "loading" : `${history.length} items`}
        right={<Button variant="ghost" onClick={loadHistory} disabled={historyLoading} style={{ fontSize: 11, padding: "2px 8px", height: 24 }}>새로고침</Button>}
      >
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.05fr) minmax(320px, 0.95fr)", gap: 10, alignItems: "start" }}>
          <div style={{ maxHeight: 230, overflow: "auto", border: "1px solid var(--border)", background: "var(--bg-primary)" }}>
            {history.length ? history.map((item) => {
              const active = (item.history_id || "") === (selectedHistory?.history_id || "");
              return (
                <button
                  type="button"
                  key={item.history_id || `${item.timestamp}:${item.prompt}`}
                  onClick={() => setSelectedHistoryId(item.history_id || "")}
                  style={{
                    display: "grid",
                    gap: 3,
                    width: "100%",
                    textAlign: "left",
                    justifyContent: "stretch",
                    justifyItems: "stretch",
                    alignItems: "start",
                    padding: "8px 9px",
                    border: 0,
                    borderBottom: "1px solid var(--border)",
                    background: active ? "var(--bg-tertiary)" : "transparent",
                    color: "var(--text-primary)",
                    cursor: "pointer",
                  }}
                >
                  <span style={{ width: "100%", textAlign: "left", fontSize: 12, fontWeight: 700, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {item.prompt || item.natural_language || "(empty)"}
                  </span>
                  <span style={{ width: "100%", textAlign: "left", fontSize: 11, color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {historyActorLabel(item)} · {historyTimestampLabel(item)}
                  </span>
                  <span style={{ width: "100%", textAlign: "left", fontSize: 11, color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {item.status || "status"} · {item.table_summary?.kind || unitKey} · {item.table_summary?.total || 0} rows
                  </span>
                </button>
              );
            }) : (
              <div style={{ padding: 12, fontSize: 12, color: "var(--text-secondary)" }}>
                저장된 {title} 질문 이력이 없습니다.
              </div>
            )}
          </div>
          <div style={{ display: "grid", gap: 8 }}>
            {selectedHistory ? (
              <>
                <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                  <Pill tone={toneForStatus(selectedHistory.status)}>{selectedHistory.status || "history"}</Pill>
                  <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                    {historyActorLabel(selectedHistory)} · {historyTimestampLabel(selectedHistory)}
                  </span>
                  <Button
                    variant="primary"
                    onClick={() => replayHistory(selectedHistory)}
                    style={{ marginLeft: "auto", fontSize: 12, padding: "4px 10px", height: 28 }}
                  >재현</Button>
                </div>
                <div style={{ textAlign: "left", fontSize: 12, fontWeight: 700, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {selectedHistory.prompt || selectedHistory.natural_language || "(empty)"}
                </div>
                {selectedHistory.answer ? (
                  <div style={{ textAlign: "left", fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.45, whiteSpace: "pre-wrap" }}>
                    {selectedHistory.answer}
                  </div>
                ) : null}
                <JsonBlock
                  value={{
                    product: selectedHistory.product || "",
                    table_summary: selectedHistory.table_summary || {},
                    warnings: selectedHistory.warnings || [],
                    trace_summary: selectedHistory.trace_summary || [],
                  }}
                  maxHeight={190}
                />
              </>
            ) : (
              <div style={{ padding: 12, fontSize: 12, color: "var(--text-secondary)", border: "1px solid var(--border)", background: "var(--bg-primary)" }}>
                이력을 선택하면 조회 답변과 table 요약을 확인할 수 있습니다.
              </div>
            )}
          </div>
        </div>
      </Panel>

      <div className="flow-agent-unit-grid">
        <Panel title="State" subtitle={selectedTraceNode ? `up to ${selectedTraceNode.label || selectedTraceNode.node_id}` : ""}>
          <JsonBlock value={stateValue} maxHeight={trace.length ? 520 : 620} />
        </Panel>

        <Panel title="LangGraph" subtitle={trace.length ? `${trace.length}/${graphNodes.length} nodes · click to inspect` : ""}>
          <div className="flow-agent-node-grid">
            <RuntimeGraph graph={activeGraph} selectedId={currentSelectedNodeId} onSelect={setSelectedNodeId} />
            <div style={{ display: "grid", gap: 8 }}>
              {selectedNode ? (
                <>
                  <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                    <strong style={{ fontSize: 13 }}>{selectedNode.label || selectedNode.node_id}</strong>
                    <Pill tone={toneForStatus(selectedNode.status)}>{selectedNode.status || "pending"}</Pill>
                    {selectedTraceNode ? <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>{selectedTraceNode.duration_ms || 0} ms</span> : null}
                  </div>
                  <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>{selectedNode.node_id}</span>
                  <NodeFeedbackInline feedback={feedback} nodeId={selectedNode.node_id} runId={result?.run_id || ""} fallback={selectedNode} />
                  {(selectedTraceNode?.warnings || []).length ? <Banner tone="warn">{(selectedTraceNode.warnings || []).join(" / ")}</Banner> : null}
                  <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>Persona</div>
                  <JsonBlock value={{ persona: selectedNode.persona || "", prompt: selectedNode.prompt || {}, answer_attach_rule: selectedNode.answer_attach_rule || "" }} maxHeight={140} />
                  <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>State I/O</div>
                  <JsonBlock value={{ reads: selectedStateIo.reads || [], writes: selectedStateIo.writes || [] }} maxHeight={150} />
                  <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>실행 결과</div>
                  <JsonBlock value={selectedTraceNode ? { status: selectedTraceNode.status, input_summary: selectedTraceNode.input_summary || {}, output: selectedNodeOutput || {}, duration_ms: selectedTraceNode.duration_ms || 0 } : { status: selectedNode.status || "pending" }} maxHeight={230} />
                </>
              ) : <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>노드 정보 없음</div>}
            </div>
          </div>
        </Panel>

        <Panel
          title="Test prompt"
          subtitle={result ? `${result.unit_ai} · ${result.run_id}` : (busy ? "running" : "")}
          right={<Pill tone={toneForStatus(result?.status)}>{result?.status || (loading ? "loading" : "ready")}</Pill>}
        >
          <div style={{ display: "grid", gap: 10 }}>
            <Textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={3} />
            <Field label="product">
              <input
                value={product}
                onChange={(e) => setProduct(e.target.value)}
                placeholder="선택"
                style={{ width: "100%", padding: "8px 10px", border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", borderRadius: 4 }}
              />
            </Field>
            <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>debug request</div>
            <JsonBlock value={{ prompt: prompt.trim(), product: product.trim() }} maxHeight={100} />
            <Button variant="primary" onClick={run} disabled={!prompt.trim() || busy}>{busy ? "실행 중" : "실행"}</Button>
            {result ? (
              <div style={{ borderTop: "1px solid var(--border)", paddingTop: 10, display: "grid", gap: 6 }}>
                <UnitAnswerFeedback feedback={feedback} runId={result?.run_id || ""} />
                {result.answer ? <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.5, whiteSpace: "pre-wrap" }}>{result.answer}</div> : null}
                <JsonBlock
                  value={{
                    product: result.product || "",
                    semantic_frame: result.semantic_frame || {},
                    lookup_result: result.lookup_result || {},
                    table: result.table || {},
                    warnings: result.warnings || [],
                  }}
                  maxHeight={260}
                />
              </div>
            ) : null}
          </div>
        </Panel>
      </div>
    </div>
  );
}

function StepLookupUnitPanel() {
  return <DeterministicLookupUnitPanel unitKey="step_lookup" title="Step ID 매칭" defaultPrompt="AA100090는 무슨 step이야" />;
}

function PpidKnobUnitPanel() {
  return <DeterministicLookupUnitPanel unitKey="ppid_knob" title="PPID Knob 분류" defaultPrompt="PPID_08_0는 어떤 knob으로 분류돼" />;
}

function SemanticLayerPanel() {
  const [payload, setPayload] = useState(null);
  const [sourceCatalog, setSourceCatalog] = useState({ sources: {}, roles: {}, docs_base: "docs/semantic" });
  const [measurementCatalog, setMeasurementCatalog] = useState({ terms: [], path: "", change_log_path: "" });
  const [aliasJson, setAliasJson] = useState("{}");
  const [intentJson, setIntentJson] = useState("{}");
  const [measurementJson, setMeasurementJson] = useState("{}");
  const [draftText, setDraftText] = useState("");
  const [draft, setDraft] = useState(null);
  const [proposalCanonicals, setProposalCanonicals] = useState({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");

  const syncPayload = (next) => {
    setPayload(next || null);
    setAliasJson(JSON.stringify(next?.alias_group_entries?.disk || next?.alias_groups?.disk || {}, null, 2));
    setIntentJson(JSON.stringify(next?.intent_hints?.disk || {}, null, 2));
  };

  const load = () => {
    setLoading(true);
    setErr("");
    return Promise.all([
      sf(SEMANTIC_LEXICON_ENDPOINT),
      sf(SEMANTIC_SOURCES_ENDPOINT).catch(() => ({ sources: {}, roles: {}, docs_base: "docs/semantic" })),
      sf(SEMANTIC_MEASUREMENTS_ENDPOINT).catch(() => ({ terms: [], path: "", change_log_path: "" })),
      sf(SEMANTIC_PROPOSALS_ENDPOINT).catch(() => ({ proposals: [] })),
    ]).then(([lexiconPayload, sourcesPayload, measurementsPayload, proposalsPayload]) => {
      setSourceCatalog({
        sources: sourcesPayload?.sources || {},
        roles: sourcesPayload?.roles || {},
        docs_base: sourcesPayload?.docs_base || "docs/semantic",
      });
      const terms = measurementsPayload?.terms || measurementsPayload?.catalog?.terms || [];
      setMeasurementCatalog({
        terms,
        path: measurementsPayload?.path || measurementsPayload?.catalog?.path || "",
        change_log_path: measurementsPayload?.change_log_path || measurementsPayload?.catalog?.change_log_path || "",
      });
      setMeasurementJson(JSON.stringify(Object.fromEntries((terms || []).map((term) => [term.id, term])), null, 2));
      syncPayload({
        ...lexiconPayload,
        proposals: proposalsPayload?.proposals || lexiconPayload?.proposals || [],
      });
    }).catch((e) => setErr(e.message || String(e)))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const saveAliasJson = () => {
    let next = {};
    try {
      next = parseJsonObject(aliasJson, "alias_groups");
    } catch (e) {
      setErr(e.message || String(e));
      return;
    }
    const current = payload?.alias_group_entries?.disk || payload?.alias_groups?.disk || {};
    const deletions = Object.keys(current).filter((key) => !Object.prototype.hasOwnProperty.call(next, key));
    setBusy(true);
    setErr("");
    setMsg("");
    Promise.all([
      ...deletions.map((key) => sf(`/api/agent/semantic/alias-groups/${encodeURIComponent(key)}`, { method: "DELETE" })),
      ...Object.entries(next).map(([key, value]) => putJson(
        `/api/agent/semantic/alias-groups/${encodeURIComponent(key)}`,
        aliasPayloadFromValue(value)
      )),
    ]).then(() => {
      setMsg("alias_groups 저장 완료");
      return load();
    }).catch((e) => setErr(e.message || String(e)))
      .finally(() => setBusy(false));
  };

  const saveIntentJson = () => {
    let next = {};
    try {
      next = parseJsonObject(intentJson, "intent_hints");
    } catch (e) {
      setErr(e.message || String(e));
      return;
    }
    const current = payload?.intent_hints?.disk || {};
    const deletions = Object.keys(current).filter((key) => !Object.prototype.hasOwnProperty.call(next, key));
    setBusy(true);
    setErr("");
    setMsg("");
    Promise.all([
      ...deletions.map((key) => sf(`/api/agent/semantic/intent-hints/${encodeURIComponent(key)}`, { method: "DELETE" })),
      ...Object.entries(next).map(([key, value]) => putJson(
        `/api/agent/semantic/intent-hints/${encodeURIComponent(key)}`,
        { required_canonicals: listFromValue(value) }
      )),
    ]).then(() => {
      setMsg("intent_hints 저장 완료");
      return load();
    }).catch((e) => setErr(e.message || String(e)))
      .finally(() => setBusy(false));
  };

  const saveMeasurementJson = () => {
    let next = {};
    try {
      next = parseJsonObject(measurementJson, "measurement_terms");
    } catch (e) {
      setErr(e.message || String(e));
      return;
    }
    setBusy(true);
    setErr("");
    setMsg("");
    Promise.all(Object.entries(next).map(([key, value]) => putJson(
      `/api/agent/semantic/measurements/${encodeURIComponent(key)}`,
      { term: { ...(value || {}), id: key } }
    ))).then(() => {
      setMsg("measurement terms 저장 완료");
      return load();
    }).catch((e) => setErr(e.message || String(e)))
      .finally(() => setBusy(false));
  };

  const makeDraft = () => {
    setBusy(true);
    setErr("");
    setMsg("");
    postJson("/api/agent/semantic/draft", { text: draftText })
      .then((out) => setDraft(out?.draft || null))
      .catch((e) => setErr(e.message || String(e)))
      .finally(() => setBusy(false));
  };

  const applyDraft = () => {
    const aliasGroups = draft?.alias_groups || {};
    const intentHints = draft?.intent_hints || {};
    setBusy(true);
    setErr("");
    setMsg("");
    Promise.all([
      ...Object.entries(aliasGroups).map(([key, value]) => putJson(
        `/api/agent/semantic/alias-groups/${encodeURIComponent(key)}`,
        aliasPayloadFromValue(value)
      )),
      ...Object.entries(intentHints).map(([key, value]) => putJson(
        `/api/agent/semantic/intent-hints/${encodeURIComponent(key)}`,
        { required_canonicals: listFromValue(value) }
      )),
    ]).then(() => {
      setMsg("semantic draft 저장 완료");
      return load();
    }).catch((e) => setErr(e.message || String(e)))
      .finally(() => setBusy(false));
  };

  const decideProposal = (proposal, decision) => {
    const id = proposal?.id || "";
    if (!id) return;
    const canonical = proposalCanonicals[id] ?? proposal?.canonical_match ?? (proposal?.category === "new_canonical" ? proposal?.term : "");
    setBusy(true);
    setErr("");
    setMsg("");
    postJson(`/api/agent/semantic/proposals/${encodeURIComponent(id)}/decision`, {
      decision,
      canonical,
    }).then(() => {
      setMsg(`proposal ${decision === "approve" ? "승인" : "거절"} 완료`);
      return load();
    }).catch((e) => setErr(e.message || String(e)))
      .finally(() => setBusy(false));
  };

  const proposals = payload?.proposals || [];
  const changes = payload?.changes || [];
  const sourceRows = useMemo(() => {
    const sources = sourceCatalog?.sources || {};
    return Array.isArray(sources) ? sources : Object.values(sources);
  }, [sourceCatalog]);
  const canApplyDraft = draft && (Object.keys(draft.alias_groups || {}).length || Object.keys(draft.intent_hints || {}).length);

  return (
    <div style={{ display: "grid", gap: 10 }}>
      {err ? <Banner tone="bad" onClose={() => setErr("")}>{err}</Banner> : null}
      {msg ? <Banner tone="ok" onClose={() => setMsg("")}>{msg}</Banner> : null}

      <div style={{ display: "grid", gridTemplateColumns: "minmax(320px, 0.95fr) minmax(0, 1.05fr)", gap: 10, alignItems: "start" }}>
        <Panel
          title="Lexicon"
          subtitle={loading ? "loading" : "disk overrides"}
          right={<Button variant="ghost" onClick={load} disabled={loading || busy} style={{ fontSize: 11, padding: "2px 8px", height: 24 }}>새로고침</Button>}
        >
          <div style={{ display: "grid", gap: 10 }}>
            <Field label="alias_groups">
              <Textarea value={aliasJson} onChange={(e) => setAliasJson(e.target.value)} rows={12} />
            </Field>
            <Button variant="primary" onClick={saveAliasJson} disabled={busy}>alias 저장</Button>
            <Field label="intent_hints">
              <Textarea value={intentJson} onChange={(e) => setIntentJson(e.target.value)} rows={8} />
            </Field>
            <Button variant="primary" onClick={saveIntentJson} disabled={busy}>intent 저장</Button>
          </div>
        </Panel>

        <Panel title="Effective view" subtitle="merged">
          <div style={{ display: "grid", gap: 8 }}>
            <JsonBlock
              value={{
                alias_groups: payload?.alias_groups?.effective || {},
                alias_group_entries: payload?.alias_group_entries?.effective || {},
                intent_hints: payload?.intent_hints?.effective || {},
              }}
              maxHeight={360}
            />
            <div style={{ display: "grid", gap: 8, borderTop: "1px solid var(--border)", paddingTop: 10 }}>
              <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                <strong style={{ fontSize: 12 }}>Draft</strong>
                {draft?.source ? <Pill tone="neutral">{draft.source}</Pill> : null}
              </div>
              <div style={{ display: "grid", gap: 8 }}>
                <Textarea
                  value={draftText}
                  onChange={(e) => setDraftText(e.target.value)}
                  rows={4}
                  placeholder='{"alias_groups":{"ioff":["IOFF","누설전류"]}}'
                />
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  <Button variant="primary" onClick={makeDraft} disabled={!draftText.trim() || busy}>초안 생성</Button>
                  <Button variant="primary" onClick={applyDraft} disabled={!canApplyDraft || busy}>초안 저장</Button>
                </div>
                <JsonBlock value={draft || {}} maxHeight={220} />
              </div>
            </div>
          </div>
        </Panel>
      </div>

      <Panel title="Source catalog" subtitle={`${sourceRows.length} sources`}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 8 }}>
          {sourceRows.map((source) => {
            const id = source?.id || source?.source_id || "";
            const docsPath = source?.docs_path || `${sourceCatalog?.docs_base || "docs/semantic"}/${id}.md`;
            return (
              <div key={id || source?.title} style={{ display: "grid", gap: 6, padding: 9, border: "1px solid var(--border)", background: "var(--bg-primary)" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                  <strong style={{ fontSize: 13 }}>{source?.title || id}</strong>
                  <Pill tone="neutral">{source?.role || "source"}</Pill>
                  {docsPath ? (
                    <a href={docsPath} target="_blank" rel="noreferrer" style={{ marginLeft: "auto", fontSize: 11, color: "var(--brand, var(--text-primary))" }}>
                      docs
                    </a>
                  ) : null}
                </div>
                <div style={{ display: "grid", gap: 4 }}>
                  {(source?.path_patterns || []).map((pattern) => (
                    <code key={pattern} style={{ fontSize: 11, color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{pattern}</code>
                  ))}
                  {(source?.fallback_path_patterns || []).map((pattern) => (
                    <code key={pattern} style={{ fontSize: 11, color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>fallback {pattern}</code>
                  ))}
                </div>
                <div style={{ fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.45 }}>
                  <strong style={{ color: "var(--text-primary)" }}>owner</strong> {source?.owner || "-"}
                </div>
                <div style={{ fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.45 }}>
                  <strong style={{ color: "var(--text-primary)" }}>write</strong> {source?.write_policy || "-"}
                </div>
                <div style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>
                  {(source?.related_question_ids || []).map((qid) => <Pill key={qid} tone="neutral">{qid}</Pill>)}
                </div>
              </div>
            );
          })}
          {!sourceRows.length ? (
            <div style={{ padding: 12, fontSize: 12, color: "var(--text-secondary)", border: "1px solid var(--border)", background: "var(--bg-primary)" }}>
              source catalog 없음
            </div>
          ) : null}
        </div>
      </Panel>

      <Panel title="Measurement terms" subtitle={`${measurementCatalog.terms.length} semantic measurement aliases`}>
        <div style={{ display: "grid", gridTemplateColumns: "minmax(320px, 0.9fr) minmax(0, 1.1fr)", gap: 10, alignItems: "start" }}>
          <div style={{ display: "grid", gap: 8 }}>
            <Field label="measurement_terms">
              <Textarea value={measurementJson} onChange={(e) => setMeasurementJson(e.target.value)} rows={14} />
            </Field>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <Button variant="primary" onClick={saveMeasurementJson} disabled={busy}>measurement 저장</Button>
              <Button variant="ghost" onClick={() => postJson("/api/agent/semantic/measurements/merge-defaults", {}).then(load).catch((e) => setErr(e.message || String(e)))} disabled={busy}>기본 병합</Button>
            </div>
            <div style={{ fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.45 }}>
              path {measurementCatalog.path || "-"} · evidence/change log {measurementCatalog.change_log_path || "-"}
            </div>
          </div>
          <div style={{ display: "grid", gap: 8 }}>
            {(measurementCatalog.terms || []).slice(0, 12).map((term) => (
              <div key={term.id} style={{ display: "grid", gap: 5, padding: 9, border: "1px solid var(--border)", background: "var(--bg-primary)" }}>
                <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                  <strong style={{ fontSize: 13 }}>{term.term}</strong>
                  <Pill tone="neutral">{term.source_type}</Pill>
                  {term.product ? <Pill tone="neutral">{term.product}</Pill> : null}
                  <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--text-secondary)" }}>{term.updated_at || ""}</span>
                </div>
                <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.45 }}>
                  item_id {term.item_id || "-"} · step_id {term.step_id || "-"} · agg {term.default_agg || "-"} · target {term.target ?? "-"} · spec {term.spec_low ?? "-"} ~ {term.spec_high ?? "-"}
                </div>
                <div style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>
                  {(term.aliases || []).slice(0, 6).map((alias) => <Pill key={alias} tone="neutral">{alias}</Pill>)}
                </div>
                {(term.evidence || []).length ? (
                  <div style={{ fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.4 }}>
                    근거 {(term.evidence || []).slice(0, 2).map((ev) => ev.label || ev.source || ev.type).filter(Boolean).join(" · ")}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      </Panel>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) 360px", gap: 10, alignItems: "start" }}>
        <Panel title="Proposals" subtitle={`${proposals.length} pending`}>
          <div style={{ display: "grid", gap: 6, maxHeight: 420, overflow: "auto" }}>
            {proposals.length ? proposals.map((proposal) => {
              const id = proposal.id || `${proposal.term}:${proposal.created_at}`;
              const canonical = proposalCanonicals[id] ?? proposal.canonical_match ?? (proposal.category === "new_canonical" ? proposal.term : "");
              return (
                <div key={id} style={{ display: "grid", gap: 6, padding: 9, border: "1px solid var(--border)", background: "var(--bg-primary)" }}>
                  <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                    <strong style={{ fontSize: 13 }}>{proposal.term || "(empty)"}</strong>
                    <Pill tone={proposal.category === "conflict" ? "warn" : "neutral"}>{proposal.category || "proposal"}</Pill>
                    <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>{proposal.confidence ?? ""}</span>
                  </div>
                  <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.4 }}>
                    {proposal.rationale || ""} {proposal.origin?.kind ? `· ${proposal.origin.kind}` : ""}
                  </div>
                  <input
                    value={canonical || ""}
                    onChange={(e) => setProposalCanonicals((prev) => ({ ...prev, [id]: e.target.value }))}
                    placeholder="canonical"
                    style={{ width: "100%", padding: "7px 9px", border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", borderRadius: 4 }}
                  />
                  <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
                    <Button variant="ghost" onClick={() => decideProposal(proposal, "reject")} disabled={busy} style={{ fontSize: 12, padding: "4px 10px", height: 28 }}>거절</Button>
                    <Button variant="primary" onClick={() => decideProposal(proposal, "approve")} disabled={busy} style={{ fontSize: 12, padding: "4px 10px", height: 28 }}>승인</Button>
                  </div>
                </div>
              );
            }) : (
              <div style={{ padding: 12, fontSize: 12, color: "var(--text-secondary)", border: "1px solid var(--border)", background: "var(--bg-primary)" }}>
                pending proposal 없음
              </div>
            )}
          </div>
        </Panel>

        <Panel title="Changes" subtitle={`${changes.length} rows`}>
          <div style={{ display: "grid", gap: 6, maxHeight: 420, overflow: "auto" }}>
            {changes.length ? changes.map((change, idx) => (
              <div key={`${change.scope}:${change.key}:${idx}`} style={{ display: "grid", gap: 4, padding: 8, border: "1px solid var(--border)", background: "var(--bg-primary)" }}>
                <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                  <Pill tone="neutral">{change.scope || "change"}</Pill>
                  <strong style={{ fontSize: 12 }}>{change.key || ""}</strong>
                </div>
                <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>{change.by || ""}</div>
                <JsonBlock value={{ before: change.before || [], after: change.after || [] }} maxHeight={120} />
              </div>
            )) : (
              <div style={{ padding: 12, fontSize: 12, color: "var(--text-secondary)", border: "1px solid var(--border)", background: "var(--bg-primary)" }}>
                change 없음
              </div>
            )}
          </div>
        </Panel>
      </div>
    </div>
  );
}

const UNIT_PANEL_RENDERERS = {
  filebrowser_ai_sql: FileBrowserAiSqlUnitPanel,
  inform_registration: InformRegistrationUnitPanel,
  change_management: ChangeManagementUnitPanel,
  dashboard_agent: DashboardAgentUnitPanel,
  step_lookup: StepLookupUnitPanel,
  ppid_knob: PpidKnobUnitPanel,
};

const UNIT_PANEL_FALLBACK_ITEMS = [
  { k: "filebrowser_ai_sql", l: "FileBrowser AI SQL" },
  { k: "inform_registration", l: "Inform 등록 도우미" },
  { k: "change_management", l: "변경점 관리 Flow-i" },
  { k: "dashboard_agent", l: "Dashboard Agent" },
  { k: "step_lookup", l: "Step ID 매칭" },
  { k: "ppid_knob", l: "PPID Knob 분류" },
];

function UnitAiPanel() {
  const [activeUnit, setActiveUnit] = useState("filebrowser_ai_sql");
  const [catalogUnits, setCatalogUnits] = useState([]);
  const items = useMemo(() => {
    const catalogItems = (catalogUnits || [])
      .filter((unit) => UNIT_PANEL_RENDERERS[unit?.key])
      .map((unit) => ({ k: unit.key, l: unit.title || unit.key }));
    return catalogItems.length ? catalogItems : UNIT_PANEL_FALLBACK_ITEMS;
  }, [catalogUnits]);
  const ActivePanel = UNIT_PANEL_RENDERERS[activeUnit] || FileBrowserAiSqlUnitPanel;

  useEffect(() => {
    sf(AGENT_UNIT_CATALOG_ENDPOINT)
      .then((payload) => setCatalogUnits(payload?.units || []))
      .catch(() => setCatalogUnits([]));
  }, []);

  useEffect(() => {
    if (!items.find((item) => item.k === activeUnit)) {
      setActiveUnit(items[0]?.k || "filebrowser_ai_sql");
    }
  }, [items, activeUnit]);

  return (
    <div style={{ display: "grid", gap: 10 }}>
      <TabStrip
        active={activeUnit}
        onChange={setActiveUnit}
        items={items}
      />
      <ActivePanel />
    </div>
  );
}

function HomeFlowiFewShotPanel() {
  const [copiedPrompt, setCopiedPrompt] = useState("");
  const copyPrompt = (prompt) => {
    setCopiedPrompt(prompt);
    if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(prompt).catch(() => {});
    }
  };

  return (
    <Panel
      title="주요 few-shot 질문"
      subtitle="Home Flow-i 라우팅 기준"
      right={copiedPrompt ? <Pill tone="ok">복사됨</Pill> : null}
    >
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 8 }}>
        {FLOWI_FEW_SHOT_QUESTIONS.map((item) => (
          <button
            key={item.prompt}
            type="button"
            onClick={() => copyPrompt(item.prompt)}
            title="프롬프트 복사"
            style={{
              display: "grid",
              gap: 6,
              minHeight: 104,
              textAlign: "left",
              padding: "10px 11px",
              border: "1px solid var(--border)",
              borderRadius: 6,
              background: "var(--bg-primary)",
              color: "var(--text-primary)",
              cursor: "pointer",
            }}
          >
            <span style={{ fontSize: 12, fontWeight: 900, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{item.title}</span>
            <span style={{ fontSize: 12, lineHeight: 1.45, color: "var(--text-primary)", overflowWrap: "anywhere" }}>{item.prompt}</span>
            <span style={{ alignSelf: "end", fontSize: 11, color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{item.target}</span>
          </button>
        ))}
      </div>
    </Panel>
  );
}

function HomeFlowiRuntimePanel() {
  const [baseGraph, setBaseGraph] = useState(null);
  const [runs, setRuns] = useState([]);
  const [selectedRunId, setSelectedRunId] = useState("");
  const [run, setRun] = useState(null);
  const [selectedNodeId, setSelectedNodeId] = useState("result_renderer");
  const [loading, setLoading] = useState(true);
  const [runLoading, setRunLoading] = useState(false);
  const [err, setErr] = useState("");

  const loadRuns = () => {
    setLoading(true);
    setErr("");
    return Promise.all([
      sf("/api/agent/home-flowi/runtime/graph").catch((e) => ({ error: e.message || String(e) })),
      sf("/api/agent/home-flowi/runtime/runs?limit=20").catch((e) => ({ error: e.message || String(e), runs: [] })),
    ]).then(([graphPayload, runsPayload]) => {
      if (graphPayload?.error) setErr(graphPayload.error);
      else setBaseGraph(graphPayload?.graph || null);
      if (runsPayload?.error) setErr(runsPayload.error);
      const nextRuns = runsPayload?.runs || [];
      setRuns(nextRuns);
      if (!selectedRunId && nextRuns[0]?.run_id) setSelectedRunId(nextRuns[0].run_id);
    }).catch((e) => setErr(e.message || String(e)))
      .finally(() => setLoading(false));
  };

  useEffect(() => { loadRuns(); }, []);

  useEffect(() => {
    if (!selectedRunId) {
      setRun(null);
      return;
    }
    setRunLoading(true);
    setErr("");
    sf(`/api/agent/home-flowi/runtime/runs/${encodeURIComponent(selectedRunId)}`)
      .then((payload) => {
        const nextRun = payload?.run || null;
        setRun(nextRun);
        const nodes = nextRun?.graph?.nodes || [];
        const preferred = nodes.find((node) => node.id === "result_renderer") || nodes[nodes.length - 1];
        setSelectedNodeId(preferred?.id || "");
      })
      .catch((e) => {
        setRun(null);
        setErr(e.message || String(e));
      })
      .finally(() => setRunLoading(false));
  }, [selectedRunId]);

  const activeGraph = run?.graph || baseGraph || HOME_FLOWI_FALLBACK_GRAPH;
  const nodes = activeGraph?.nodes || [];
  const selectedNode = nodes.find((node) => node.id === selectedNodeId) || nodes[0] || null;
  const details = run?.node_details || {};
  const detail = (selectedNodeId && details[selectedNodeId]) || selectedNode || {};
  const detailWarnings = Array.isArray(detail?.warnings) ? detail.warnings.filter(Boolean) : [];
  const preview = detail?.preview && typeof detail.preview === "object" ? detail.preview : {};
  const promptText = run?.prompt || "";

  return (
    <div style={{ display: "grid", gap: 10 }}>
      <HomeFlowiFewShotPanel />
      {err && <Banner tone="bad" onClose={() => setErr("")}>{err}</Banner>}
      <div style={{ display: "grid", gridTemplateColumns: "300px minmax(0, 1fr) 380px", gap: 10, alignItems: "start" }}>
        <Panel
          title="최근 실행"
          subtitle={loading ? "loading" : `${runs.length} runs`}
          right={<Button variant="ghost" onClick={loadRuns} disabled={loading} style={{ fontSize: 11, padding: "2px 8px", height: 24 }}>새로고침</Button>}
        >
          <div style={{ display: "grid", gap: 6, maxHeight: 680, overflow: "auto" }}>
            {runs.length ? runs.map((item) => {
              const active = item.run_id === selectedRunId;
              return (
                <button
                  key={item.run_id}
                  type="button"
                  onClick={() => setSelectedRunId(item.run_id)}
                  style={{
                    display: "grid",
                    gap: 4,
                    textAlign: "left",
                    width: "100%",
                    padding: "8px 9px",
                    border: `1px solid ${active ? "var(--brand, var(--text-primary))" : "var(--border)"}`,
                    borderRadius: 4,
                    background: active ? "var(--bg-tertiary)" : "var(--bg-primary)",
                    color: "var(--text-primary)",
                    cursor: "pointer",
                  }}
                >
                  <span style={{ fontSize: 12, fontWeight: 800, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {item.prompt || "(empty)"}
                  </span>
                  <span style={{ fontSize: 11, color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {item.run_id}
                  </span>
                  <span style={{ display: "flex", gap: 6, alignItems: "center", minWidth: 0 }}>
                    <Pill tone={toneForStatus(item.status)}>{item.status || "pending"}</Pill>
                    <span style={{ fontSize: 11, color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {item.created_at || ""}
                    </span>
                  </span>
                </button>
              );
            }) : (
              <div style={{ padding: 12, fontSize: 12, color: "var(--text-secondary)", border: "1px solid var(--border)", background: "var(--bg-primary)" }}>
                기록 없음
              </div>
            )}
          </div>
        </Panel>

        <Panel
          title="Flow-i Runtime"
          subtitle={run ? `${run.source || "home"} · ${run.run_id}` : (runLoading ? "loading" : "default graph")}
          right={<Pill tone={toneForStatus(run?.status)}>{run?.status || "ready"}</Pill>}
        >
          {promptText ? (
            <div style={{ marginBottom: 8, fontSize: 12, color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={promptText}>
              {promptText}
            </div>
          ) : null}
          <RuntimeGraph graph={activeGraph} selectedId={selectedNodeId} onSelect={setSelectedNodeId} />
        </Panel>

        <Panel
          title={selectedNode?.label || "Node"}
          subtitle={selectedNode?.id || ""}
          right={<Pill tone={toneForStatus(detail?.status || selectedNode?.status)}>{detail?.status || selectedNode?.status || "pending"}</Pill>}
        >
          <div style={{ display: "grid", gap: 8 }}>
            {detailWarnings.length ? <Banner tone="warn">{detailWarnings.slice(0, 4).join(" / ")}</Banner> : null}
            <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>input</div>
            <JsonBlock value={detail?.input_summary || {}} maxHeight={120} />
            <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>output</div>
            <JsonBlock value={detail?.output_summary || detail || {}} maxHeight={230} />
            {preview?.rows?.length ? (
              <>
                <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>
                  preview ({preview.rows.length}{preview.total !== undefined ? ` / ${preview.total}` : ""})
                </div>
                <PreviewTable preview={{ columns: preview.columns || Object.keys(preview.rows[0] || {}), rows: preview.rows }} />
              </>
            ) : preview && Object.keys(preview).length ? (
              <>
                <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>preview</div>
                <JsonBlock value={preview} maxHeight={160} />
              </>
            ) : null}
            {run?.action_log && selectedNode?.id === "result_renderer" ? (
              <>
                <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>action_log</div>
                <JsonBlock value={run.action_log} maxHeight={180} />
              </>
            ) : null}
          </div>
        </Panel>
      </div>
    </div>
  );
}

export default function My_Diagnosis({ user }) {
  const isAdminUser = user?.role === "admin";
  const [activeTab, setActiveTab] = useState("unit-ai");

  return (
    <div className="flow-connected-page flow-agent-page" style={{ minHeight: "calc(100vh - 52px)", background: "var(--bg-primary)", color: "var(--text-primary)" }}>
      <PageShell style={{ minHeight: "calc(100vh - 52px)", display: "flex", flexDirection: "column" }}>
        <PageHeader title="에이전트" subtitle="단위기능 AI 실행과 LLM 연결" />
        <div className="flow-agent-shell">
          <div className="flow-agent-tabs">
            <TabStrip
              active={activeTab}
              onChange={setActiveTab}
              items={[
                { k: "home-flowi", l: "Flow-i" },
                { k: "semantic", l: "Semantic layer" },
                { k: "unit-ai", l: "단위기능 AI" },
                { k: "llm", l: "LLM 설정" },
              ]}
            />
          </div>
          <div className="flow-agent-surface" style={{ overflow: "auto" }}>
            {activeTab === "home-flowi"
              ? <HomeFlowiRuntimePanel />
              : (activeTab === "semantic" ? <SemanticLayerPanel /> : (activeTab === "unit-ai" ? <UnitAiPanel /> : <LlmTab isAdmin={isAdminUser} />))}
          </div>
        </div>
      </PageShell>
    </div>
  );
}
