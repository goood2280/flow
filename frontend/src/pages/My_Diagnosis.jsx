import { useEffect, useMemo, useState } from "react";
import dagre from "dagre";
import { PageHeader, PageShell, Panel, Banner, Button, Field, Pill, Select, TabStrip, Textarea } from "../components/UXKit";
import LlmTab from "../components/agent/LlmTab";
import { postJson, sf } from "../lib/api";

const AGENT_UNIT_RUN_ENDPOINT = "/api/agent/unit-ai/filebrowser_ai_sql/runtime/run";
const FILEBROWSER_AI_SQL_HISTORY_ENDPOINT = "/api/filebrowser/sql/history?limit=50";

function queryUrl(path, params) {
  const query = Object.entries(params || {})
    .filter(([, value]) => value !== undefined && value !== null && value !== "")
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`)
    .join("&");
  return query ? `${path}?${query}` : path;
}

function toneForStatus(status) {
  if (status === "success") return "ok";
  if (status === "warning") return "warn";
  if (status === "failed" || status === "blocked") return "bad";
  return "neutral";
}

function statusColor(status) {
  if (status === "success") return { bg: "var(--ok-50)", fg: "var(--ok)", line: "var(--ok-line)" };
  if (status === "warning") return { bg: "var(--warn-50)", fg: "var(--warn)", line: "var(--warn-line)" };
  if (status === "failed" || status === "blocked") return { bg: "var(--danger-50)", fg: "var(--danger)", line: "var(--danger-line)" };
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
    const nodeW = 170;
    const nodeH = 58;
    g.setGraph({ rankdir: "TB", nodesep: 28, ranksep: 36, marginx: 22, marginy: 22 });
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
    return { nodes: laidNodes, edges: laidEdges, width: maxX + 28, height: maxY + 28 };
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
              <text x="12" y="23" fill="var(--text-primary)" fontSize="12" fontWeight="700">{node.label}</text>
              <text x="12" y="42" fill={color.fg} fontSize="11">{node.id} · {node.status || "pending"}</text>
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

const STATE_KEY_BY_NODE = {
  context_sample: "context_sample",
  semantic_layer: "semantic_frame",
  filter_draft: "filter",
  column_draft: "columns_result",
  merge: "merged",
  preview_apply: "preview",
};

const FALLBACK_GRAPH = {
  nodes: [
    { id: "context_sample", label: "용어해석 준비", phase: "context", status: "pending" },
    { id: "semantic_layer", label: "용어해석", phase: "semantic", status: "pending" },
    { id: "filter_draft", label: "filter SQL 생성", phase: "llm", status: "pending" },
    { id: "column_draft", label: "표시 컬럼 생성", phase: "llm", status: "pending" },
    { id: "merge", label: "병합", phase: "validate", status: "pending" },
    { id: "preview_apply", label: "preview 적용", phase: "preview", status: "pending" },
  ],
  edges: [
    { source: "context_sample", target: "semantic_layer" },
    { source: "semantic_layer", target: "filter_draft" },
    { source: "semantic_layer", target: "column_draft" },
    { source: "filter_draft", target: "merge" },
    { source: "column_draft", target: "merge" },
    { source: "merge", target: "preview_apply" },
  ],
};

const HOME_FLOWI_FALLBACK_GRAPH = {
  nodes: [
    { id: "prompt_input", label: "프롬프트 입력", phase: "input", status: "pending" },
    { id: "semantic_layer", label: "용어해석", phase: "semantic", status: "pending" },
    { id: "orchestrator", label: "오케스트레이터", phase: "plan", status: "pending" },
    { id: "result_renderer", label: "결과 정리", phase: "render", status: "pending" },
    { id: "unit_ai:filebrowser_ai_sql", label: "FileBrowser AI SQL", phase: "unit_ai_mcp", status: "available" },
  ],
  edges: [
    { source: "prompt_input", target: "semantic_layer" },
    { source: "semantic_layer", target: "orchestrator" },
    { source: "orchestrator", target: "unit_ai:filebrowser_ai_sql" },
    { source: "orchestrator", target: "result_renderer" },
  ],
};

function buildAccumulatedState(result, request, upToIdx) {
  const trace = result?.trace || [];
  const state = {
    run_id: result?.run_id || null,
    request: request || null,
  };
  const limit = Number.isFinite(upToIdx) ? upToIdx + 1 : trace.length;
  for (let i = 0; i < limit && i < trace.length; i += 1) {
    const row = trace[i];
    const key = STATE_KEY_BY_NODE[row.node_id];
    if (key) state[key] = row.output;
  }
  return state;
}

function FileBrowserAiSqlUnitPanel() {
  const [catalog, setCatalog] = useState(null);
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

  useEffect(() => {
    const trace = result?.trace || [];
    if (!trace.length) {
      setSelectedNodeId(null);
      return;
    }
    setSelectedNodeId(trace[trace.length - 1]?.node_id || null);
  }, [result]);

  useEffect(() => {
    setAppliedSql(result?.preview?.applied_sql || result?.merged?.display_sql || result?.merged?.sql || "");
  }, [result?.run_id]);

  const loadHistory = () => {
    setHistoryLoading(true);
    return sf(FILEBROWSER_AI_SQL_HISTORY_ENDPOINT)
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
      sf("/api/agent/unit-ai/catalog").catch((e) => ({ error: e.message || String(e) })),
      sf("/api/agent/unit-ai/filebrowser_ai_sql/runtime/graph").catch((e) => ({ error: e.message || String(e) })),
      sf("/api/filebrowser/roots").catch(() => ({ roots: [] })),
      sf("/api/filebrowser/base-files").catch(() => ({ files: [] })),
      sf(FILEBROWSER_AI_SQL_HISTORY_ENDPOINT).catch(() => ({ history: [] })),
    ]).then(([statusPayload, catalogPayload, graphPayload, rootsPayload, filesPayload, historyPayload]) => {
      const routesOk = statusPayload?.ok === true;
      setAgentRoutesPresent(routesOk);
      if (catalogPayload?.error) setErr(catalogPayload.error);
      setCatalog(catalogPayload);
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
  const unit = (catalog?.units || []).find((item) => item.key === "filebrowser_ai_sql");
  const activeGraph = result?.graph || graph || FALLBACK_GRAPH;
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
    postJson(AGENT_UNIT_RUN_ENDPOINT, body)
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
  const selectedIdx = selectedNodeId
    ? trace.findIndex((row) => row.node_id === selectedNodeId)
    : -1;
  const selectedNode = selectedIdx >= 0 ? trace[selectedIdx] : null;
  const accumulatedState = useMemo(
    () => buildAccumulatedState(result, lastRequest, selectedIdx >= 0 ? selectedIdx : undefined),
    [result, lastRequest, selectedIdx]
  );

  const stateSubtitle = selectedNode
    ? `up to ${selectedNode.label || selectedNode.node_id}`
    : (trace.length ? "final state" : "empty");
  const graphSubtitle = trace.length
    ? `${trace.length}/${(activeGraph?.nodes || []).length} nodes · click to inspect`
    : "context_sample → semantic_layer → filter_draft → column_draft → merge → preview_apply";

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
  const selectedNodeOutput = compactRowsPayload(selectedNode?.output);
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

      <div style={{ display: "grid", gridTemplateColumns: "280px minmax(0, 1fr) 360px", gap: 10, alignItems: "start" }}>
        <Panel title="State" subtitle={stateSubtitle}>
          {trace.length ? (
            <JsonBlock value={accumulatedState} maxHeight={620} />
          ) : (
            <div style={{ fontSize: 12, color: "var(--text-secondary)", padding: 8 }}>
              아직 실행 결과 없음. 우측에서 prompt를 입력하고 [실행] 후 state가 누적됩니다.
            </div>
          )}
        </Panel>

        <Panel title="LangGraph" subtitle={graphSubtitle}>
          <div style={{ display: "grid", gridTemplateColumns: "minmax(320px, 1fr) minmax(260px, 360px)", gap: 10, alignItems: "start" }}>
            <RuntimeGraph graph={activeGraph} selectedId={selectedNodeId} onSelect={setSelectedNodeId} />
            <div style={{ display: "grid", gap: 8 }}>
              {selectedNode ? (
                <>
                  <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                    <strong style={{ fontSize: 13 }}>{selectedNode.label || selectedNode.node_id}</strong>
                    <Pill tone={toneForStatus(selectedNode.status)}>{selectedNode.status}</Pill>
                    <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>{selectedNode.duration_ms || 0} ms</span>
                  </div>
                  <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>{selectedNode.node_id}</span>
                  {(selectedNode.warnings || []).length ? (
                    <Banner tone="warn">{(selectedNode.warnings || []).join(" / ")}</Banner>
                  ) : null}
                  {selectedNode.output?.llm?.system ? (
                    <details style={{ border: "1px solid var(--border)", background: "var(--bg-primary)" }}>
                      <summary style={{ padding: "6px 8px", fontSize: 11, color: "var(--text-secondary)", cursor: "pointer", background: "var(--bg-tertiary)" }}>
                        system prompt · llm={selectedNode.output.llm?.used ? "used" : (selectedNode.output.llm?.available ? "available" : "off")}
                      </summary>
                      <pre style={{ margin: 0, padding: 8, fontSize: 12, lineHeight: 1.45, color: "var(--text-primary)", whiteSpace: "pre-wrap", wordBreak: "break-word", maxHeight: 180, overflow: "auto" }}>
                        {selectedNode.output.llm.system}
                      </pre>
                    </details>
                  ) : null}
                  <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>input_summary</div>
                  <JsonBlock value={selectedNode.input_summary} maxHeight={130} />
                  <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>output</div>
                  <JsonBlock value={selectedNodeOutput} maxHeight={230} />
                </>
              ) : (
                <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                  실행 후 그래프 노드를 클릭하면 해당 시점의 input / output 스냅샷을 볼 수 있습니다.
                </div>
              )}
            </div>
          </div>
        </Panel>

        <Panel
          title="Test prompt"
          subtitle={result ? `${result.unit_ai} · ${result.run_id}` : (busy ? "running" : (unit?.llm_profile || "filter_draft / column_draft"))}
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
                { k: "unit-ai", l: "단위기능 AI" },
                { k: "llm", l: "LLM 설정" },
              ]}
            />
          </div>
          <div className="flow-agent-surface" style={{ overflow: "auto" }}>
            {activeTab === "home-flowi"
              ? <HomeFlowiRuntimePanel />
              : (activeTab === "unit-ai" ? <FileBrowserAiSqlUnitPanel /> : <LlmTab isAdmin={isAdminUser} />)}
          </div>
        </div>
      </PageShell>
    </div>
  );
}
