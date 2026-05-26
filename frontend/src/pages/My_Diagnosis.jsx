import { useEffect, useMemo, useState } from "react";
import dagre from "dagre";
import { PageHeader, PageShell, Panel, Banner, Button, DataTable, Field, Input, Pill, Select, TabStrip, Textarea } from "../components/UXKit";
import LlmTab from "../components/agent/LlmTab";
import { postJson, sf } from "../lib/api";

const AGENT_UNIT_RUN_ENDPOINT = "/api/agent/unit-ai/filebrowser_ai_sql/runtime/run";

const TARGET_MODES = [
  { value: "db_product", label: "DB root/product" },
  { value: "rootpq", label: "root parquet" },
  { value: "base", label: "Base file" },
];

function toneForStatus(status) {
  if (status === "success") return "ok";
  if (status === "warning") return "warn";
  if (status === "failed") return "bad";
  return "neutral";
}

function statusColor(status) {
  if (status === "success") return { bg: "var(--ok-50)", fg: "var(--ok)", line: "var(--ok-line)" };
  if (status === "warning") return { bg: "var(--warn-50)", fg: "var(--warn)", line: "var(--warn-line)" };
  if (status === "failed") return { bg: "var(--danger-50)", fg: "var(--danger)", line: "var(--danger-line)" };
  return { bg: "var(--bg-tertiary)", fg: "var(--text-secondary)", line: "var(--border)" };
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

function RuntimeGraph({ graph }) {
  const layout = useMemo(() => {
    const nodes = graph?.nodes || [];
    const edges = graph?.edges || [];
    if (!nodes.length) return { nodes: [], edges: [], width: 640, height: 160 };
    const g = new dagre.graphlib.Graph();
    const nodeW = 150;
    const nodeH = 58;
    g.setGraph({ rankdir: "LR", nodesep: 34, ranksep: 54, marginx: 22, marginy: 22 });
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
    const maxX = Math.max(...laidNodes.map((node) => node.x + node.w), 620);
    const maxY = Math.max(...laidNodes.map((node) => node.y + node.h), 130);
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
    const x1 = source.x + source.w;
    const y1 = source.y + source.h / 2;
    const x2 = target.x;
    const y2 = target.y + target.h / 2;
    const mid = (x1 + x2) / 2;
    return `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`;
  };

  return (
    <div style={{ width: "100%", overflowX: "auto", border: "1px solid var(--border)", background: "var(--bg-primary)" }}>
      <svg viewBox={`0 0 ${layout.width} ${layout.height}`} width="100%" height="210" style={{ minWidth: Math.max(layout.width, 720), display: "block" }}>
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
          return (
            <g key={node.id} transform={`translate(${node.x}, ${node.y})`}>
              <rect width={node.w} height={node.h} rx="4" fill={color.bg} stroke={color.line || color.fg} />
              <text x="12" y="23" fill="var(--text-primary)" fontSize="12" fontWeight="700">{node.label}</text>
              <text x="12" y="42" fill={color.fg} fontSize="11">{node.status || "pending"}</text>
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

function TraceView({ trace }) {
  const rows = (trace || []).map((node) => ({
    node: node.label || node.node_id,
    status: node.status,
    duration: `${node.duration_ms || 0} ms`,
    warnings: (node.warnings || []).join(" / ") || "-",
  }));
  return (
    <div style={{ display: "grid", gap: 10 }}>
      <DataTable
        rows={rows}
        empty="trace 없음"
        columns={[
          { key: "node", label: "node", width: 170 },
          { key: "status", label: "status", width: 90, render: (row) => <Pill tone={toneForStatus(row.status)}>{row.status}</Pill> },
          { key: "duration", label: "time", width: 90 },
          { key: "warnings", label: "warnings" },
        ]}
      />
      {(trace || []).map((node) => (
        <div key={node.node_id} style={{ display: "grid", gridTemplateColumns: "minmax(160px, 220px) minmax(0, 1fr) minmax(0, 1.4fr)", gap: 8, alignItems: "start", borderTop: "1px solid var(--border)", paddingTop: 8 }}>
          <div style={{ display: "grid", gap: 4 }}>
            <strong style={{ fontSize: 13 }}>{node.label || node.node_id}</strong>
            <Pill tone={toneForStatus(node.status)}>{node.status}</Pill>
            <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>{node.duration_ms || 0} ms</span>
          </div>
          <JsonBlock value={node.input_summary} maxHeight={120} />
          <JsonBlock value={{ output: node.output, warnings: node.warnings || [] }} maxHeight={180} />
        </div>
      ))}
    </div>
  );
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
  const [prompt, setPrompt] = useState("A1000 #3 IOFF만 보고싶어");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [result, setResult] = useState(null);

  useEffect(() => {
    setLoading(true);
    Promise.all([
      sf("/api/agent/unit-ai/catalog").catch((e) => ({ error: e.message || String(e) })),
      sf("/api/agent/unit-ai/filebrowser_ai_sql/runtime/graph").catch((e) => ({ error: e.message || String(e) })),
      sf("/api/filebrowser/roots").catch(() => ({ roots: [] })),
      sf("/api/filebrowser/base-files").catch(() => ({ files: [] })),
    ]).then(([catalogPayload, graphPayload, rootsPayload, filesPayload]) => {
      if (catalogPayload?.error) setErr(catalogPayload.error);
      setCatalog(catalogPayload);
      setGraph(graphPayload?.graph || null);
      const nextRoots = rootsPayload?.roots || [];
      setRoots(nextRoots);
      if (!root && nextRoots[0]?.name) setRoot(nextRoots[0].name);
      const nextFiles = filesPayload?.files || [];
      setBaseFiles(nextFiles);
      const firstFile = nextFiles.find((item) => ["parquet", "csv"].includes(String(item.ext || "").toLowerCase()));
      if (!file && firstFile?.path) setFile(firstFile.path);
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
  const parquetOptions = fileOptions.filter((item) => String(item.ext || "").toLowerCase() === "parquet");
  const activeFileOptions = targetMode === "rootpq" ? parquetOptions : fileOptions;
  const unit = (catalog?.units || []).find((item) => item.key === "filebrowser_ai_sql");
  const activeGraph = result?.graph || graph;
  const canRun = prompt.trim() && (
    targetMode === "db_product" ? (root && product) : file.trim()
  );

  const run = () => {
    if (!canRun) return;
    setBusy(true);
    setErr("");
    setResult(null);
    const body = {
      natural_language: prompt.trim(),
      scope: targetMode,
      root: targetMode === "db_product" ? root : "",
      product: targetMode === "db_product" ? product : "",
      file: targetMode !== "db_product" ? file.trim() : "",
    };
    postJson(AGENT_UNIT_RUN_ENDPOINT, body)
      .then(setResult)
      .catch((e) => setErr(e.message || String(e)))
      .finally(() => setBusy(false));
  };

  return (
    <div style={{ display: "grid", gap: 10 }}>
      {err && <Banner tone="bad" onClose={() => setErr("")}>{err}</Banner>}
      <Panel
        title="FileBrowser AI SQL"
        subtitle={unit?.llm_profile || "filter_draft / column_draft"}
        right={<Pill tone={result?.ok ? "ok" : "neutral"}>{result?.run_id || (loading ? "loading" : "ready")}</Pill>}
      >
        <div style={{ display: "grid", gridTemplateColumns: "220px minmax(180px, 1fr) minmax(180px, 1fr)", gap: 10, alignItems: "end" }}>
          <Field label="target">
            <Select value={targetMode} onChange={(e) => setTargetMode(e.target.value)}>
              {TARGET_MODES.map((mode) => <option key={mode.value} value={mode.value}>{mode.label}</option>)}
            </Select>
          </Field>
          {targetMode === "db_product" ? (
            <>
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
            </>
          ) : (
            <Field label="file" style={{ gridColumn: "span 2" }}>
              <Input list="agentFilebrowserAiSqlFiles" value={file} onChange={(e) => setFile(e.target.value)} placeholder={targetMode === "rootpq" ? "root parquet path" : "Base file path"} />
              <datalist id="agentFilebrowserAiSqlFiles">
                {activeFileOptions.map((item) => <option key={`${item.source}:${item.value}`} value={item.value}>{item.label}</option>)}
              </datalist>
            </Field>
          )}
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) auto", gap: 10, marginTop: 10, alignItems: "end" }}>
          <Field label="natural_language">
            <Textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={3} />
          </Field>
          <Button variant="primary" onClick={run} disabled={!canRun || busy} style={{ height: 34 }}>{busy ? "실행 중" : "실행"}</Button>
        </div>
      </Panel>

      <Panel title="LangGraph DAG" subtitle="context_sample -> semantic_layer -> filter_draft -> column_draft -> merge -> preview_apply">
        <RuntimeGraph graph={activeGraph} />
      </Panel>

      <Panel title="노드 trace" subtitle={result ? `${result.unit_ai} / ${result.run_id}` : "waiting"}>
        <TraceView trace={result?.trace || []} />
      </Panel>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 0.9fr) minmax(0, 1.1fr)", gap: 10 }}>
        <Panel title="최종 SQL / 컬럼">
          <div style={{ display: "grid", gap: 8 }}>
            <DataTable
              rows={[
                { key: "sql", value: result?.merged?.sql || "-" },
                { key: "selected_columns", value: (result?.merged?.selected_columns || []).join(", ") || "-" },
                { key: "filter_llm", value: result?.filter?.llm?.used ? "used" : "fallback" },
                { key: "column_llm", value: result?.columns?.llm?.used ? "used" : "fallback" },
              ]}
              columns={[
                { key: "key", label: "field", width: 140 },
                { key: "value", label: "value" },
              ]}
            />
            <JsonBlock value={{ semantic_frame: result?.semantic_frame || {}, filter: result?.filter || {}, columns: result?.columns || {} }} maxHeight={260} />
          </div>
        </Panel>
        <Panel
          title="preview"
          subtitle={result?.preview ? `${result.preview.rows?.length || 0} rows / total ${result.preview.total_rows ?? 0}` : "waiting"}
          right={result?.preview?.preview_capped ? <Pill tone="warn">capped</Pill> : null}
        >
          {result?.preview?.warnings?.length ? <Banner tone="warn" style={{ marginBottom: 8 }}>{result.preview.warnings.join(" / ")}</Banner> : null}
          <PreviewTable preview={result?.preview} />
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
                { k: "unit-ai", l: "단위기능 AI" },
                { k: "llm", l: "LLM 설정" },
              ]}
            />
          </div>
          <div className="flow-agent-surface" style={{ overflow: "auto" }}>
            {activeTab === "unit-ai" ? <FileBrowserAiSqlUnitPanel /> : <LlmTab isAdmin={isAdminUser} />}
          </div>
        </div>
      </PageShell>
    </div>
  );
}
