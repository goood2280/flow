import { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D from "react-force-graph-2d";
import dagre from "dagre";

const KIND_COLOR = {
  semantic: "#2563eb",
  control: "#7c3aed",
  unit_action: "#0f766e",
  guardrail: "#b45309",
  output: "#475569",
};

const STATUS_COLOR = {
  pending: "#94a3b8",
  running: "#f59e0b",
  completed: "#16a34a",
  failed: "#dc2626",
  skipped: "#d97706",
  missing_slots: "#d97706",
  approval_required: "#7c3aed",
  blocked: "#b91c1c",
  no_handler: "#64748b",
  low_coverage: "#ea580c",
};

const NODE_W = 148;
const NODE_H = 50;
const FIT_PAD_X = 70;
const FIT_PAD_Y = 58;

export default function AgentRuntimeGraphView({ blueprint, nodeStates = {}, selectedNodeId = "", onSelectNode }) {
  const containerRef = useRef(null);
  const fgRef = useRef(null);
  const [size, setSize] = useState({ w: 680, h: 420 });

  useEffect(() => {
    if (!containerRef.current) return undefined;
    const ro = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (!rect) return;
      setSize({ w: Math.max(360, rect.width), h: Math.max(360, rect.height) });
    });
    ro.observe(containerRef.current);
    return () => ro.disconnect();
  }, []);

  const data = useMemo(() => {
    const graph = blueprint?.graph || blueprint || {};
    const rawNodes = Array.isArray(graph.nodes) ? graph.nodes : [];
    const rawEdges = Array.isArray(graph.edges) ? graph.edges : [];
    const nodesInput = rawNodes
      .map((node) => (typeof node === "string" ? { id: node, label: node, kind: "control" } : node))
      .filter((node) => node && node.id);
    const edgesInput = rawEdges
      .map((edge) => Array.isArray(edge) ? { from: edge[0], to: edge[1] } : edge)
      .filter((edge) => edge && edge.from && edge.to);
    if (!nodesInput.length) return { nodes: [], links: [] };

    const actionCount = nodesInput.filter((node) => node.kind === "unit_action").length;
    const g = new dagre.graphlib.Graph();
    g.setGraph({
      rankdir: "LR",
      nodesep: actionCount > 4 ? 16 : 28,
      ranksep: actionCount > 4 ? 150 : 130,
      marginx: 42,
      marginy: 36,
    });
    g.setDefaultEdgeLabel(() => ({}));
    nodesInput.forEach((node) => g.setNode(node.id, { width: NODE_W, height: NODE_H }));
    edgesInput.forEach((edge) => g.setEdge(edge.from, edge.to));
    dagre.layout(g);

    const nodes = nodesInput.map((node) => {
      const pos = g.node(node.id) || { x: 0, y: 0 };
      return { ...node, x: pos.x, y: pos.y, fx: pos.x, fy: pos.y };
    });
    const links = edgesInput.map((edge) => ({ source: edge.from, target: edge.to, label: edge.label || edge.cond || "" }));
    return { nodes, links };
  }, [blueprint]);

  useEffect(() => {
    if (!fgRef.current || !data.nodes.length) return;
    fgRef.current.d3Force("charge", null);
    fgRef.current.d3Force("link", null);
    fgRef.current.d3Force("center", null);
    const fit = () => fitGraphToViewport(fgRef.current, data.nodes, size);
    setTimeout(fit, 80);
    setTimeout(fit, 320);
  }, [data, size.w, size.h]);

  if (!data.nodes.length) {
    return <div className="agent-runtime-empty">그래프 정보 없음</div>;
  }

  return (
    <div ref={containerRef} className="unit-runtime-graph">
      <ForceGraph2D
        ref={fgRef}
        width={size.w}
        height={size.h}
        graphData={data}
        backgroundColor="rgba(0,0,0,0)"
        nodeCanvasObjectMode={() => "replace"}
        nodeCanvasObject={(node, ctx) => drawNode(node, ctx, nodeStates[node.id] || "pending", selectedNodeId === node.id)}
        nodePointerAreaPaint={(node, color, ctx) => {
          ctx.fillStyle = color;
          roundRect(ctx, node.x - NODE_W / 2, node.y - NODE_H / 2, NODE_W, NODE_H, 5);
          ctx.fill();
        }}
        onNodeClick={(node) => onSelectNode && onSelectNode(node)}
        linkDirectionalArrowLength={7}
        linkDirectionalArrowRelPos={1}
        linkColor={() => "rgba(100,116,139,0.72)"}
        linkWidth={1.2}
        enableNodeDrag={false}
        enablePanInteraction
        enableZoomInteraction
        cooldownTicks={0}
      />
    </div>
  );
}

function fitGraphToViewport(graph, nodes, size) {
  if (!graph || !nodes?.length) return;
  const minX = Math.min(...nodes.map((node) => Number(node.x || 0) - NODE_W / 2));
  const maxX = Math.max(...nodes.map((node) => Number(node.x || 0) + NODE_W / 2));
  const minY = Math.min(...nodes.map((node) => Number(node.y || 0) - NODE_H / 2));
  const maxY = Math.max(...nodes.map((node) => Number(node.y || 0) + NODE_H / 2));
  const graphW = Math.max(1, maxX - minX);
  const graphH = Math.max(1, maxY - minY);
  const viewportW = Math.max(1, Number(size.w || 1) - FIT_PAD_X * 2);
  const viewportH = Math.max(1, Number(size.h || 1) - FIT_PAD_Y * 2);
  const zoom = Math.max(0.35, Math.min(2.2, Math.min(viewportW / graphW, viewportH / graphH)));
  graph.centerAt((minX + maxX) / 2, (minY + maxY) / 2, 0);
  graph.zoom(zoom, 0);
}

function drawNode(node, ctx, status, selected) {
  const w = NODE_W;
  const h = NODE_H;
  const fill = STATUS_COLOR[status] || KIND_COLOR[node.kind] || "#64748b";
  ctx.save();
  ctx.shadowColor = selected ? "rgba(37,99,235,0.35)" : "transparent";
  ctx.shadowBlur = selected ? 12 : 0;
  ctx.fillStyle = fill;
  ctx.strokeStyle = selected ? "#1d4ed8" : "rgba(15,23,42,0.6)";
  ctx.lineWidth = selected ? 2 : 1;
  roundRect(ctx, node.x - w / 2, node.y - h / 2, w, h, 5);
  ctx.fill();
  ctx.stroke();
  ctx.shadowBlur = 0;
  ctx.fillStyle = "#ffffff";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.font = "700 12px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif";
  ctx.fillText(trimLabel(node.label || node.id, 16), node.x, node.y - 7);
  ctx.font = "10px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif";
  ctx.fillStyle = "rgba(255,255,255,0.88)";
  ctx.fillText(status || "pending", node.x, node.y + 9);
  ctx.restore();
}

function trimLabel(label, max) {
  const text = String(label || "");
  return text.length > max ? text.slice(0, max - 3) + "..." : text;
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}
