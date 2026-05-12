import { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D from "react-force-graph-2d";
import Modal from "../Modal";
import { postJson, sf } from "../../lib/api";
import { Banner, Button, DataTable, EmptyState, Field, Panel, Pill, TabStrip, formControlStyle } from "../UXKit";
import AgentKnowledgeVault from "../../pages/My_Knowledge";
import { AgentWikiPanel } from "./AgentLegacy";

function nodeId(value) {
  if (value && typeof value === "object") return String(value.id || "");
  return String(value || "");
}

function normalizeGraph(data) {
  const rawNodes = Array.isArray(data?.nodes) ? data.nodes : Array.isArray(data?.graph?.nodes) ? data.graph.nodes : [];
  const rawLinks = Array.isArray(data?.links) ? data.links : Array.isArray(data?.edges) ? data.edges : Array.isArray(data?.graph?.edges) ? data.graph.edges : [];
  const nodes = rawNodes.map((n) => ({
    ...n,
    id: String(n.id || n.doc_id || n.label || ""),
    label: String(n.label || n.title || n.doc_id || n.id || ""),
    kind: String(n.kind || n.type || "node"),
  })).filter((n) => n.id);
  const nodeSet = new Set(nodes.map((n) => n.id));
  const links = rawLinks.map((e, idx) => ({
    ...e,
    id: String(e.id || e.edge_id || `edge_${idx}`),
    source: nodeId(e.source),
    target: nodeId(e.target),
    label: String(e.label || e.relation || e.type || ""),
  })).filter((e) => nodeSet.has(e.source) && nodeSet.has(e.target));
  return { nodes, links };
}

function useElementWidth(ref, fallback = 900) {
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    if (!ref.current) return undefined;
    const update = () => setWidth(Math.max(320, Math.floor(ref.current.getBoundingClientRect().width || fallback)));
    update();
    const ro = new ResizeObserver(update);
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, [ref, fallback]);
  return width;
}

function WikiGraph({ graph, query, selected, highlightId, onSelect }) {
  const wrapRef = useRef(null);
  const width = useElementWidth(wrapRef);
  const [hover, setHover] = useState(null);
  const q = String(query || "").trim().toLowerCase();
  const graphData = useMemo(() => ({
    nodes: graph.nodes.map((n) => ({ ...n })),
    links: graph.links.map((l) => ({ ...l })),
  }), [graph]);
  const linked = useMemo(() => {
    const ids = new Set();
    const linkIds = new Set();
    const active = hover?.id || selected?.id || "";
    if (!active) return { ids, linkIds };
    ids.add(active);
    for (const link of graph.links) {
      const src = nodeId(link.source);
      const tgt = nodeId(link.target);
      if (src === active || tgt === active) {
        ids.add(src);
        ids.add(tgt);
        linkIds.add(link.id);
      }
    }
    return { ids, linkIds };
  }, [graph.links, hover, selected]);
  const matching = useMemo(() => {
    if (!q) return new Set();
    return new Set(graph.nodes.filter((n) => `${n.label} ${n.id} ${n.kind}`.toLowerCase().includes(q)).map((n) => n.id));
  }, [graph.nodes, q]);
  return (
    <div ref={wrapRef} style={{ width: "100%", height: 560, border: "1px solid var(--border)", borderRadius: 8, background: "var(--bg-primary)", overflow: "hidden" }}>
      {graph.nodes.length ? (
        <ForceGraph2D
          graphData={graphData}
          width={width}
          height={560}
          backgroundColor="rgba(0,0,0,0)"
          cooldownTicks={80}
          nodeLabel={(n) => `${n.label || n.id} (${n.kind || "node"})`}
          onNodeHover={setHover}
          onNodeClick={(node) => onSelect?.(node)}
          linkColor={(link) => linked.linkIds.size && !linked.linkIds.has(link.id) ? "rgba(148,163,184,.18)" : "rgba(148,163,184,.55)"}
          linkWidth={(link) => linked.linkIds.has(link.id) ? 2.8 : 1}
          nodeCanvasObject={(node, ctx, scale) => {
            const isConnected = !linked.ids.size || linked.ids.has(node.id);
            const isMatch = matching.has(node.id);
            const isSelected = selected?.id === node.id;
            const isNew = highlightId && highlightId === node.id;
            const radius = isSelected || isNew ? 7 : isMatch ? 6 : 4.5;
            const color = isNew ? "#f97316" : isSelected ? "#22c55e" : isMatch ? "#3b82f6" : node.kind === "wiki_doc" ? "#8b5cf6" : "#94a3b8";
            ctx.globalAlpha = isConnected ? 1 : 0.18;
            ctx.beginPath();
            ctx.arc(node.x, node.y, radius, 0, 2 * Math.PI, false);
            ctx.fillStyle = color;
            ctx.fill();
            const label = node.label || node.id;
            const fontSize = Math.max(8, 13 / scale);
            ctx.font = `${fontSize}px sans-serif`;
            ctx.fillStyle = "#e5e7eb";
            ctx.textAlign = "center";
            ctx.textBaseline = "top";
            if (isSelected || isMatch || isNew || scale > 1.2) ctx.fillText(label.slice(0, 28), node.x, node.y + radius + 3);
            ctx.globalAlpha = 1;
          }}
        />
      ) : (
        <div style={{ padding: 24 }}>
          <EmptyState title="graph node 없음" hint="Wiki 문서를 추가하거나 graph rebuild 후 다시 확인하세요." />
        </div>
      )}
    </div>
  );
}

export default function WikiTab({ user, canManage }) {
  const [subtab, setSubtab] = useState("graph");
  const [graph, setGraph] = useState({ nodes: [], links: [] });
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(null);
  const [highlightId, setHighlightId] = useState("");
  const [modalOpen, setModalOpen] = useState(false);
  const [form, setForm] = useState({ doc_id: "", tags: "", body: "" });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const load = () => {
    setBusy(true);
    sf("/api/knowledge/wiki/graph")
      .then((d) => {
        const next = normalizeGraph(d);
        setGraph(next);
        setSelected((cur) => cur && next.nodes.find((n) => n.id === cur.id) ? cur : next.nodes[0] || null);
        setMsg("");
      })
      .catch((e) => setMsg("graph 오류: " + (e.message || e)))
      .finally(() => setBusy(false));
  };

  useEffect(() => { load(); }, []);

  const matchingRows = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return [];
    return graph.nodes.filter((n) => `${n.label} ${n.id} ${n.kind}`.toLowerCase().includes(q)).slice(0, 30);
  }, [graph.nodes, query]);

  const upsert = () => {
    if (!canManage || !form.body.trim()) return;
    setBusy(true);
    setMsg("");
    postJson("/api/knowledge/wiki/ai-upsert", {
      doc_id: form.doc_id.trim(),
      tags: form.tags.split(",").map((x) => x.trim()).filter(Boolean),
      body: form.body,
    })
      .then((d) => {
        if (d?.ok === false) throw new Error(d.error || "ai-upsert failed");
        const docId = d?.doc?.doc_id || form.doc_id.trim();
        setHighlightId(docId);
        setModalOpen(false);
        setForm({ doc_id: "", tags: "", body: "" });
        setMsg(`wiki 저장됨: ${docId || "-"}`);
        load();
      })
      .catch((e) => setMsg("upsert 오류: " + (e.message || e)))
      .finally(() => setBusy(false));
  };

  return (
    <div style={{ display: "grid", gap: 12 }}>
      <TabStrip items={[{ k: "graph", l: "Graph" }, { k: "advanced", l: "고급" }]} active={subtab} onChange={setSubtab} />
      {msg && <Banner tone={msg.includes("오류") ? "bad" : "ok"}>{msg}</Banner>}
      {subtab === "graph" && (
        <div style={{ display: "grid", gap: 12 }}>
          <Panel
            title="Wiki Graph"
            subtitle="문서, source event, product/lot/wafer/entity 관계를 graph로 확인합니다."
            right={<div style={{ display: "flex", gap: 8, alignItems: "center" }}><Pill tone="accent">{graph.nodes.length} nodes</Pill><Pill tone="info">{graph.links.length} links</Pill><Button onClick={load} disabled={busy}>{busy ? "로딩 중" : "새로고침"}</Button>{canManage && <Button variant="primary" onClick={() => setModalOpen(true)}>+</Button>}</div>}
          >
            <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(280px,0.36fr)", gap: 12, alignItems: "start" }}>
              <div style={{ display: "grid", gap: 10 }}>
                <Field label="search">
                  <input value={query} onChange={(e) => setQuery(e.target.value)} style={{ ...formControlStyle, width: "100%", boxSizing: "border-box" }} placeholder="doc, product, lot, relation" />
                </Field>
                <WikiGraph graph={graph} query={query} selected={selected} highlightId={highlightId} onSelect={setSelected} />
              </div>
              <div style={{ display: "grid", gap: 12 }}>
                <Panel title="Detail" subtitle={selected?.id || "선택된 node 없음"}>
                  {selected ? (
                    <DataTable
                      rows={Object.entries(selected).filter(([k]) => !["x", "y", "vx", "vy", "index"].includes(k)).map(([key, value]) => ({ key, value: typeof value === "object" ? JSON.stringify(value) : String(value ?? "") }))}
                      columns={[
                        { key: "key", label: "field", width: 110 },
                        { key: "value", label: "value" },
                      ]}
                    />
                  ) : <EmptyState title="node detail 없음" hint="graph node를 클릭하세요." />}
                </Panel>
                <Panel title="Search Matches" subtitle={query ? `${matchingRows.length} matches` : "검색어를 입력하세요"}>
                  <DataTable
                    rows={matchingRows}
                    empty="검색 결과가 없습니다."
                    onRowClick={setSelected}
                    maxHeight={260}
                    columns={[
                      { key: "kind", label: "kind", width: 90, render: (r) => <Pill tone="neutral">{r.kind}</Pill> },
                      { key: "label", label: "label" },
                    ]}
                  />
                </Panel>
              </div>
            </div>
          </Panel>
        </div>
      )}
      {subtab === "advanced" && (
        <div style={{ display: "grid", gap: 12 }}>
          <AgentWikiPanel canManage={canManage} />
          <Panel title="Knowledge Vault" subtitle="기존 Knowledge Vault 기능을 그대로 렌더합니다.">
            <AgentKnowledgeVault user={user} embedded />
          </Panel>
        </div>
      )}
      <Modal open={modalOpen} onClose={() => setModalOpen(false)} title="Wiki paste" width={720}>
        <div style={{ display: "grid", gap: 10 }}>
          {!canManage && <Banner tone="warn">관리 권한이 필요합니다.</Banner>}
          <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)", gap: 8 }}>
            <Field label="doc_id">
              <input value={form.doc_id} onChange={(e) => setForm({ ...form, doc_id: e.target.value })} style={{ ...formControlStyle, width: "100%" }} placeholder="optional" />
            </Field>
            <Field label="tags">
              <input value={form.tags} onChange={(e) => setForm({ ...form, tags: e.target.value })} style={{ ...formControlStyle, width: "100%" }} placeholder="comma separated" />
            </Field>
          </div>
          <Field label="body">
            <textarea value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })} rows={12} style={{ ...formControlStyle, width: "100%", resize: "vertical", boxSizing: "border-box", lineHeight: 1.55 }} />
          </Field>
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
            <Button onClick={() => setModalOpen(false)}>취소</Button>
            <Button variant="primary" onClick={upsert} disabled={!canManage || busy || !form.body.trim()}>{busy ? "저장 중" : "AI upsert"}</Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
