import { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D from "react-force-graph-2d";
import Modal from "../Modal";
import { postJson, qs, sf } from "../../lib/api";
import { Banner, Button, DataTable, EmptyState, Field, Panel, Pill, TabStrip, formControlStyle } from "../UXKit";

const DEFAULT_SEED_SCHEMA = "default_agent_wiki_seed_v1";

function nodeId(value) {
  if (value && typeof value === "object") return String(value.id || "");
  return String(value || "");
}

function isDefaultSeedNode(node = {}) {
  return Boolean(node.is_default_seed || node.schema_type === DEFAULT_SEED_SCHEMA || node.kind === "default_seed");
}

function splitTags(value) {
  if (Array.isArray(value)) return value.map(String).map((x) => x.trim()).filter(Boolean);
  return String(value || "").split(",").map((x) => x.trim()).filter(Boolean);
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

function listText(value, limit = 3) {
  const arr = Array.isArray(value) ? value : value ? [value] : [];
  const head = arr.map(String).filter(Boolean).slice(0, limit);
  return head.join(", ") + (arr.length > limit ? ` +${arr.length - limit}` : "");
}

function KoreanClamp({ children, lines = 2, title = "" }) {
  return (
    <div
      className={`korean-wrap clamp-${lines === 1 ? 2 : lines}`}
      title={title || String(children || "")}
      style={lines === 1 ? {
        display: "block",
        minWidth: 0,
        overflow: "hidden",
        textOverflow: "ellipsis",
        whiteSpace: "nowrap",
      } : { lineHeight: 1.45 }}
    >
      {children || "-"}
    </div>
  );
}

function pageSourceText(page = {}) {
  const fm = page.frontmatter && typeof page.frontmatter === "object" ? page.frontmatter : {};
  return listText(page.source_ids || fm.source_ids || page.source_event_ids || [], 2) || "-";
}

function wikiDocIdFromNode(node = {}) {
  const id = String(node?.id || "");
  if (!id) return "";
  if (id.startsWith("doc:")) return id.slice(4);
  if (node?.kind === "wiki_doc") return id;
  return "";
}

function cssVar(name, fallback) {
  const root = getComputedStyle(document.documentElement);
  const value = root.getPropertyValue(name).trim();
  return value || fallback;
}

function isLightColor(value = "") {
  const match = value.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/i);
  if (!match) return !String(value).includes("1a1a1a");
  const [, r, g, b] = match.map(Number);
  return (r * 299 + g * 587 + b * 114) / 1000 > 150;
}

function roundRect(ctx, x, y, w, h, r) {
  const radius = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + radius, y);
  ctx.lineTo(x + w - radius, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + radius);
  ctx.lineTo(x + w, y + h - radius);
  ctx.quadraticCurveTo(x + w, y + h, x + w - radius, y + h);
  ctx.lineTo(x + radius, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - radius);
  ctx.lineTo(x, y + radius);
  ctx.quadraticCurveTo(x, y, x + radius, y);
  ctx.closePath();
}

function WikiGraph({ graph, query, selected, focusId, highlightId, onSelect, onFocusChange }) {
  const wrapRef = useRef(null);
  const width = useElementWidth(wrapRef);
  const [hover, setHover] = useState(null);
  const [theme, setTheme] = useState({
    light: true,
    text: "#111827",
    muted: "#475569",
    labelBg: "rgba(255,255,255,.9)",
    labelLine: "rgba(15,23,42,.18)",
    link: "rgba(71,85,105,.62)",
    linkFocus: "rgba(15,23,42,.88)",
    linkDim: "rgba(148,163,184,.22)",
  });
  const q = String(query || "").trim().toLowerCase();
  useEffect(() => {
    const readTheme = () => {
      const bg = cssVar("--bg-primary", "#fff");
      const light = isLightColor(bg);
      setTheme({
        light,
        text: cssVar("--text-primary", light ? "#111827" : "#f8fafc"),
        muted: cssVar("--text-secondary", light ? "#475569" : "#cbd5e1"),
        labelBg: light ? "rgba(255,255,255,.92)" : "rgba(15,23,42,.88)",
        labelLine: light ? "rgba(15,23,42,.18)" : "rgba(226,232,240,.22)",
        link: light ? "rgba(71,85,105,.66)" : "rgba(203,213,225,.58)",
        linkFocus: light ? "rgba(15,23,42,.92)" : "rgba(248,250,252,.9)",
        linkDim: light ? "rgba(148,163,184,.24)" : "rgba(100,116,139,.28)",
      });
    };
    readTheme();
    const observer = new MutationObserver(readTheme);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["style", "class"] });
    return () => observer.disconnect();
  }, []);
  const focus = useMemo(() => {
    const active = String(focusId || "");
    const ids = new Set();
    const linkIds = new Set();
    if (!active) return { ids, linkIds, links: [] };
    ids.add(active);
    const links = [];
    for (const link of graph.links) {
      const src = nodeId(link.source);
      const tgt = nodeId(link.target);
      if (src === active || tgt === active) {
        ids.add(src);
        ids.add(tgt);
        linkIds.add(link.id);
        links.push(link);
      }
    }
    return { ids, linkIds, links };
  }, [focusId, graph.links]);
  const graphData = useMemo(() => {
    const focused = focus.ids.size > 0;
    const nodes = (focused ? graph.nodes.filter((n) => focus.ids.has(n.id)) : graph.nodes).map((n) => ({ ...n }));
    const links = (focused ? focus.links : graph.links).map((l) => ({ ...l }));
    return { nodes, links };
  }, [focus, graph]);
  const linked = useMemo(() => {
    const ids = new Set();
    const linkIds = new Set();
    const active = hover?.id || focusId || "";
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
  }, [focusId, graph.links, hover]);
  const matching = useMemo(() => {
    if (!q) return new Set();
    return new Set(graph.nodes.filter((n) => `${n.label} ${n.id} ${n.kind}`.toLowerCase().includes(q)).map((n) => n.id));
  }, [graph.nodes, q]);
  const focusLabel = focusId ? (graph.nodes.find((n) => n.id === focusId)?.label || focusId) : "";
  return (
    <div ref={wrapRef} style={{ position: "relative", width: "100%", height: 560, border: "1px solid var(--border)", borderRadius: 8, background: "var(--bg-primary)", overflow: "hidden" }}>
      {focusId && (
        <div style={{ position: "absolute", top: 10, right: 10, zIndex: 3, display: "flex", gap: 8, alignItems: "center", maxWidth: "calc(100% - 20px)" }}>
          <Pill tone="accent">연결만 · {focus.ids.size} nodes</Pill>
          <Button onClick={() => onFocusChange?.("")}>전체 보기</Button>
        </div>
      )}
      {graph.nodes.length ? (
        <ForceGraph2D
          graphData={graphData}
          width={width}
          height={560}
          backgroundColor="rgba(0,0,0,0)"
          cooldownTicks={80}
          nodeLabel={(n) => `${n.label || n.id} (${n.kind || "node"})`}
          onNodeHover={setHover}
          onBackgroundClick={() => onFocusChange?.("")}
          onNodeClick={(node) => {
            onSelect?.(node);
            onFocusChange?.(node.id);
          }}
          linkColor={(link) => linked.linkIds.size && !linked.linkIds.has(link.id) ? theme.linkDim : linked.linkIds.has(link.id) || focusId ? theme.linkFocus : theme.link}
          linkWidth={(link) => linked.linkIds.has(link.id) || focusId ? 2.6 : 1.35}
          linkDirectionalArrowLength={(link) => linked.linkIds.has(link.id) || focusId ? 5 : 3}
          linkDirectionalArrowRelPos={1}
          linkDirectionalParticles={(link) => linked.linkIds.has(link.id) || focusId ? 2 : 0}
          linkDirectionalParticleWidth={2.4}
          linkCanvasObjectMode={() => "after"}
          linkCanvasObject={(link, ctx, scale) => {
            if (!focusId && scale < 1.25) return;
            const src = link.source;
            const tgt = link.target;
            if (!src || !tgt || typeof src !== "object" || typeof tgt !== "object") return;
            const label = String(link.label || "").slice(0, 24);
            if (!label) return;
            const x = (src.x + tgt.x) / 2;
            const y = (src.y + tgt.y) / 2;
            const fontSize = Math.max(8, 11 / scale);
            ctx.font = `700 ${fontSize}px sans-serif`;
            const padX = 4 / scale;
            const padY = 2 / scale;
            const metrics = ctx.measureText(label);
            const w = metrics.width + padX * 2;
            const h = fontSize + padY * 2;
            ctx.fillStyle = theme.labelBg;
            roundRect(ctx, x - w / 2, y - h / 2, w, h, 4 / scale);
            ctx.fill();
            ctx.strokeStyle = theme.labelLine;
            ctx.stroke();
            ctx.fillStyle = theme.muted;
            ctx.textAlign = "center";
            ctx.textBaseline = "middle";
            ctx.fillText(label, x, y + 0.5 / scale);
          }}
          nodeCanvasObject={(node, ctx, scale) => {
            const isConnected = !linked.ids.size || linked.ids.has(node.id);
            const isMatch = matching.has(node.id);
            const isSelected = selected?.id === node.id;
            const isNew = highlightId && (highlightId === node.id || `doc:${highlightId}` === node.id);
            const isFocus = focusId === node.id;
            const isSeed = isDefaultSeedNode(node);
            const radius = isSelected || isNew ? 7 : isMatch || isSeed ? 6 : 4.5;
            const color = isNew ? "#f97316" : isSelected ? "#22c55e" : isSeed ? "#f97316" : isMatch ? "#3b82f6" : node.kind === "wiki_doc" ? "#8b5cf6" : "#94a3b8";
            ctx.globalAlpha = isConnected ? 1 : 0.16;
            ctx.beginPath();
            ctx.arc(node.x, node.y, radius, 0, 2 * Math.PI, false);
            ctx.shadowColor = theme.light ? "rgba(15,23,42,.24)" : "rgba(255,255,255,.2)";
            ctx.shadowBlur = isSelected || isFocus || isNew ? 10 : 4;
            ctx.fillStyle = color;
            ctx.fill();
            ctx.shadowBlur = 0;
            if (isSelected || isFocus || isNew) {
              ctx.lineWidth = 2 / scale;
              ctx.strokeStyle = theme.text;
              ctx.stroke();
            }
            const label = node.label || node.id;
            const isHover = hover?.id === node.id;
            const isFocusedView = Boolean(focusId);
            const showFocusLabel = isFocusedView && focus.ids.has(node.id);
            const showLabel = showFocusLabel || (!isFocusedView && (isSeed || isSelected || isMatch || isNew || isHover));
            if (showLabel) {
              const display = label.length > 30 ? label.slice(0, 29) + "…" : label;
              const fontSize = Math.max(6.5, Math.min(12, 10.5 / scale));
              ctx.font = `800 ${fontSize}px sans-serif`;
              const metrics = ctx.measureText(display);
              const padX = 5 / scale;
              const padY = 3 / scale;
              const labelW = metrics.width + padX * 2;
              const labelH = fontSize + padY * 2;
              const labelX = node.x - labelW / 2;
              const labelY = node.y + radius + 4 / scale;
              ctx.fillStyle = theme.labelBg;
              roundRect(ctx, labelX, labelY, labelW, labelH, 5 / scale);
              ctx.fill();
              ctx.strokeStyle = theme.labelLine;
              ctx.lineWidth = 1 / scale;
              ctx.stroke();
              ctx.fillStyle = isConnected ? theme.text : theme.muted;
              ctx.textAlign = "center";
              ctx.textBaseline = "middle";
              ctx.fillText(display, node.x, labelY + labelH / 2 + 0.5 / scale);
            }
            ctx.globalAlpha = 1;
          }}
        />
      ) : (
        <div style={{ padding: 24 }}>
          <EmptyState title="graph node 없음" hint="Wiki 문서를 추가하거나 graph rebuild 후 다시 확인하세요." />
        </div>
      )}
      {focusLabel && (
        <div className="korean-wrap" style={{ position: "absolute", left: 12, bottom: 10, right: 12, zIndex: 2, pointerEvents: "none", color: "var(--text-secondary)", fontSize: 12, fontWeight: 700 }}>
          {focusLabel}
        </div>
      )}
    </div>
  );
}

const vaultSideSectionStyle = {
  minWidth: 0,
  border: "1px solid var(--border)",
  borderRadius: 5,
  background: "var(--bg-secondary)",
  padding: 8,
};

const vaultSideHeadStyle = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "baseline",
  gap: 8,
  marginBottom: 8,
  color: "var(--text-secondary)",
  fontSize: 12,
};

export default function WikiTab({ user, canManage }) {
  const [subtab, setSubtab] = useState("vault");
  const [graph, setGraph] = useState({ nodes: [], links: [] });
  const [showFullGraph, setShowFullGraph] = useState(false);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(null);
  const [graphFocusId, setGraphFocusId] = useState("");
  const [selectedDetail, setSelectedDetail] = useState(null);
  const [detailBusy, setDetailBusy] = useState(false);
  const [highlightId, setHighlightId] = useState("");
  const [modalOpen, setModalOpen] = useState(false);
  const [form, setForm] = useState({ doc_id: "", tags: "", body: "" });
  const [editOpen, setEditOpen] = useState(false);
  const [editForm, setEditForm] = useState({ doc_id: "", kind: "agent_wiki", title: "", summary: "", body: "", tags: "", frontmatter: {} });
  const [sources, setSources] = useState([]);
  const [pages, setPages] = useState([]);
  const [selectedPage, setSelectedPage] = useState(null);
  const [schemaGraph, setSchemaGraph] = useState({ relations: [], column_catalog: [] });
  const [preview, setPreview] = useState(null);
  const [lint, setLint] = useState(null);
  const [wikiSearch, setWikiSearch] = useState("");
  const [wikiForm, setWikiForm] = useState({ title: "", tags: "", content: "" });
  const [singleFileForm, setSingleFileForm] = useState({
    root: "base_root",
    file: "ppid_knob.csv",
    purpose: "rulebook",
    key_columns: "product, feature_name, function_step, category",
    output_columns: "operator, rule_order",
    title: "",
  });
  const [singleFilePreview, setSingleFilePreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const load = () => {
    setBusy(true);
    const view = canManage && showFullGraph ? "full" : "curated";
    sf("/api/knowledge/wiki/graph" + qs({ view }))
      .then((d) => {
        const next = normalizeGraph(d);
        const fallbackSelected = next.nodes.find((n) => n.kind === "wiki_doc") || next.nodes[0] || null;
        setGraph(next);
        setSelected((cur) => cur && next.nodes.find((n) => n.id === cur.id) ? cur : fallbackSelected);
        setGraphFocusId((cur) => cur && next.nodes.find((n) => n.id === cur) ? cur : "");
        setMsg("");
      })
      .catch((e) => setMsg("graph 오류: " + (e.message || e)))
      .finally(() => setBusy(false));
  };

  useEffect(() => { load(); }, [showFullGraph]);

  const loadWiki = () => {
    setBusy(true);
    return Promise.all([
      sf("/api/agent/wiki/sources?limit=60"),
      sf("/api/agent/wiki/pages" + qs({ q: wikiSearch.trim(), limit: 160 })),
      sf("/api/agent/schema-relations/graph").catch(() => ({ relations: [], column_catalog: [] })),
    ])
      .then(([s, p, schema]) => {
        setSources(s.sources || []);
        const nextPages = p.pages || [];
        setPages(nextPages);
        setSchemaGraph({ relations: schema.relations || [], column_catalog: schema.column_catalog || [] });
        setSelectedPage((cur) => cur && nextPages.find((row) => row.doc_id === cur.doc_id) ? cur : nextPages[0] || null);
        setMsg("");
      })
      .catch((e) => setMsg("wiki 오류: " + (e.message || e)))
      .finally(() => setBusy(false));
  };

  useEffect(() => { loadWiki(); }, []);

  useEffect(() => {
    const detailDocId = wikiDocIdFromNode(selected);
    if (!detailDocId) {
      setSelectedDetail(null);
      return undefined;
    }
    let alive = true;
    setDetailBusy(true);
    sf("/api/agent/wiki/page" + qs({ doc_id: detailDocId }))
      .then((d) => {
        if (alive) setSelectedDetail(d.page || null);
      })
      .catch(() => {
        if (alive) setSelectedDetail(null);
      })
      .finally(() => {
        if (alive) setDetailBusy(false);
      });
    return () => { alive = false; };
  }, [selected]);

  useEffect(() => {
    const docId = selectedPage?.doc_id || "";
    if (!docId || typeof selectedPage.body === "string") return undefined;
    let alive = true;
    sf("/api/agent/wiki/page" + qs({ doc_id: docId }))
      .then((d) => {
        if (alive) setSelectedPage(d.page || selectedPage);
      })
      .catch(() => {})
      .finally(() => {});
    return () => { alive = false; };
  }, [selectedPage?.doc_id]);

  const openPage = (row) => {
    const docId = row?.doc_id || row?.id;
    if (!docId) return;
    setBusy(true);
    sf("/api/agent/wiki/page" + qs({ doc_id: docId }))
      .then((d) => {
        const page = d.page || null;
        const graphNode = graph.nodes.find((n) => n.id === docId || n.id === `doc:${docId}`);
        setSelectedPage(page);
        setSelected(graphNode || { id: docId, label: page?.title || docId, kind: page?.kind || "wiki_doc" });
        setGraphFocusId(graphNode?.id || docId);
      })
      .catch((e) => setMsg("상세 오류: " + (e.message || e)))
      .finally(() => setBusy(false));
  };

  const registerSource = () => {
    if (!canManage || !wikiForm.content.trim()) return;
    setBusy(true);
    postJson("/api/agent/wiki/sources", {
      source_type: "markdown",
      title: wikiForm.title || "한글 지식 입력",
      tags: wikiForm.tags.split(",").map((x) => x.trim()).filter(Boolean),
      content: wikiForm.content,
    })
      .then((d) => {
        setMsg(`source 등록됨: ${d.source?.source_id || "-"}`);
        return loadWiki();
      })
      .catch((e) => setMsg("source 등록 오류: " + (e.message || e)))
      .finally(() => setBusy(false));
  };

  const previewWiki = () => {
    if (!wikiForm.content.trim()) {
      setMsg("한글 지식 본문을 입력하세요.");
      return;
    }
    setBusy(true);
    postJson("/api/agent/wiki/ingest/preview", {
      title: wikiForm.title,
      tags: wikiForm.tags.split(",").map((x) => x.trim()).filter(Boolean),
      content: wikiForm.content,
    })
      .then((d) => {
        setPreview(d.preview || null);
        setMsg("미리보기 생성됨");
      })
      .catch((e) => setMsg("미리보기 오류: " + (e.message || e)))
      .finally(() => setBusy(false));
  };

  const commitWiki = () => {
    if (!canManage || !preview) return;
    setBusy(true);
    postJson("/api/agent/wiki/ingest/commit", {
      doc_id: preview.doc_id,
      title: preview.title,
      summary: preview.summary,
      body: preview.body,
      tags: preview.tags || wikiForm.tags.split(",").map((x) => x.trim()).filter(Boolean),
      content: wikiForm.content,
    })
      .then((d) => {
        const page = d.doc || d.page || null;
        setSelectedPage(page);
        setHighlightId(page?.doc_id || "");
        setMsg(`wiki 저장됨: ${page?.doc_id || "-"}`);
        setPreview(null);
        setWikiForm({ title: "", tags: "", content: "" });
        return Promise.all([loadWiki(), load()]);
      })
      .catch((e) => setMsg("저장 오류: " + (e.message || e)))
      .finally(() => setBusy(false));
  };

  const deletePage = () => {
    if (!canManage || !selectedPage?.doc_id) return;
    if (!window.confirm(`선택한 지식 page를 삭제할까요?\n${selectedPage.doc_id}`)) return;
    const docId = selectedPage.doc_id;
    setBusy(true);
    postJson("/api/agent/wiki/page/delete", { doc_id: docId })
      .then(() => {
        setMsg(`wiki 삭제됨: ${docId}`);
        setSelectedPage(null);
        return Promise.all([loadWiki(), load()]);
      })
      .catch((e) => setMsg("삭제 오류: " + (e.message || e)))
      .finally(() => setBusy(false));
  };

  const runLint = () => {
    if (!canManage) return;
    setBusy(true);
    postJson("/api/agent/wiki/lint", {})
      .then((d) => {
        setLint(d);
        setMsg("lint 완료");
      })
      .catch((e) => setMsg("lint 오류: " + (e.message || e)))
      .finally(() => setBusy(false));
  };

  const singleFilePayload = () => ({
    source: {
      source_type: "file",
      root: singleFileForm.root,
      file: singleFileForm.file.trim(),
      label: singleFileForm.title.trim() || singleFileForm.file.trim().replace(/\.[^.]+$/, ""),
    },
    sample_rows: 20,
  });

  const previewSingleFile = () => {
    if (!singleFileForm.file.trim()) {
      setMsg("등록할 파일명을 입력하세요.");
      return;
    }
    setBusy(true);
    postJson("/api/agent/schema_doc/single-file/preview", singleFilePayload())
      .then((d) => {
        setSingleFilePreview(d.source || null);
        setMsg(`단일 파일 미리보기: ${d.source?.source_id || "-"}`);
      })
      .catch((e) => setMsg("단일 파일 미리보기 오류: " + (e.message || e)))
      .finally(() => setBusy(false));
  };

  const registerSingleFile = () => {
    if (!canManage || !singleFileForm.file.trim()) return;
    const splitCols = (value) => String(value || "").split(",").map((x) => x.trim()).filter(Boolean);
    setBusy(true);
    postJson("/api/agent/schema_doc/single-file/register", {
      ...singleFilePayload(),
      purpose: singleFileForm.purpose,
      key_columns: splitCols(singleFileForm.key_columns),
      output_columns: splitCols(singleFileForm.output_columns),
      title: singleFileForm.title.trim(),
    })
      .then((d) => {
        setSingleFilePreview(d.source || null);
        setMsg(`실행 지식 등록됨: ${d.doc?.doc_id || d.source?.source_id || "-"}`);
        return Promise.all([loadWiki(), load()]);
      })
      .catch((e) => setMsg("실행 지식 등록 오류: " + (e.message || e)))
      .finally(() => setBusy(false));
  };

  const beginEdit = (page) => {
    const fm = page.frontmatter && typeof page.frontmatter === "object" ? page.frontmatter : {};
    setEditForm({
      doc_id: page.doc_id,
      kind: page.kind || "agent_wiki",
      title: page.title || page.doc_id,
      summary: page.summary || "",
      body: page.body || "",
      tags: (page.tags || []).join(", "),
      frontmatter: fm,
    });
    setEditOpen(true);
  };

  const openEdit = (page) => {
    if (!canManage || !page?.doc_id) return;
    if (typeof page.body !== "string") {
      setBusy(true);
      sf("/api/agent/wiki/page" + qs({ doc_id: page.doc_id }))
        .then((d) => beginEdit(d.page || page))
        .catch((e) => setMsg("상세 오류: " + (e.message || e)))
        .finally(() => setBusy(false));
      return;
    }
    beginEdit(page);
  };

  const saveEdit = () => {
    if (!canManage || !editForm.doc_id || !editForm.title.trim()) return;
    setBusy(true);
    postJson("/api/agent/wiki/page/save", {
      doc_id: editForm.doc_id,
      kind: editForm.kind,
      title: editForm.title.trim(),
      summary: editForm.summary.trim(),
      body: editForm.body,
      tags: splitTags(editForm.tags),
      frontmatter: editForm.frontmatter || {},
    })
      .then((d) => {
        const page = d.page || d.doc || null;
        setSelectedDetail(page);
        setSelectedPage(page);
        setHighlightId(page?.doc_id || "");
        setEditOpen(false);
        setMsg(`wiki 수정됨: ${page?.doc_id || editForm.doc_id}`);
        return Promise.all([loadWiki(), load()]);
      })
      .catch((e) => setMsg("수정 오류: " + (e.message || e)))
      .finally(() => setBusy(false));
  };

  const matchingRows = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return [];
    return graph.nodes.filter((n) => `${n.label} ${n.id} ${n.kind}`.toLowerCase().includes(q)).slice(0, 30);
  }, [graph.nodes, query]);

  const defaultSeedRows = useMemo(() => {
    return graph.nodes
      .filter((n) => n.kind === "wiki_doc" && isDefaultSeedNode(n))
      .sort((a, b) => String(a.label || a.id).localeCompare(String(b.label || b.id)));
  }, [graph.nodes]);

  const defaultSeedHub = useMemo(() => {
    return graph.nodes.find((n) => n.id === "concept:default_agent_wiki_seed" || n.kind === "default_seed") || null;
  }, [graph.nodes]);

  const graphNodeById = useMemo(() => new Map(graph.nodes.map((node) => [node.id, node])), [graph.nodes]);

  const visiblePages = useMemo(() => {
    const q = wikiSearch.trim().toLowerCase();
    const rows = q
      ? pages.filter((row) => `${row.doc_id} ${row.title} ${row.summary} ${row.kind} ${(row.tags || []).join(" ")}`.toLowerCase().includes(q))
      : pages;
    return rows.slice(0, 220);
  }, [pages, wikiSearch]);

  const vaultStats = useMemo(() => {
    const byKind = {};
    for (const row of pages) byKind[row.kind || "manual"] = (byKind[row.kind || "manual"] || 0) + 1;
    return byKind;
  }, [pages]);

  const selectedSourceIds = useMemo(() => {
    if (!selectedPage) return [];
    const fm = selectedPage.frontmatter && typeof selectedPage.frontmatter === "object" ? selectedPage.frontmatter : {};
    const ids = selectedPage.source_ids || fm.source_ids || selectedPage.source_event_ids || [];
    return Array.isArray(ids) ? ids.map(String).filter(Boolean) : [];
  }, [selectedPage]);

  const backlinkRows = useMemo(() => {
    const docId = selectedPage?.doc_id || "";
    if (!docId) return [];
    const ids = new Set([docId, `doc:${docId}`]);
    const rows = [];
    const seen = new Set();
    for (const link of graph.links) {
      const src = nodeId(link.source);
      const tgt = nodeId(link.target);
      if (!ids.has(src) && !ids.has(tgt)) continue;
      const otherId = ids.has(src) ? tgt : src;
      const key = `${src}:${tgt}:${link.label || ""}`;
      if (!otherId || seen.has(key)) continue;
      seen.add(key);
      const other = graphNodeById.get(otherId) || { id: otherId, label: otherId, kind: "node" };
      rows.push({
        id: key,
        direction: ids.has(src) ? "out" : "in",
        relation: link.label || link.relation || "link",
        node_id: otherId,
        label: other.label || otherId,
        kind: other.kind || "node",
      });
    }
    return rows.slice(0, 30);
  }, [graph.links, graphNodeById, selectedPage?.doc_id]);

  const selectGraphNode = (node) => {
    if (!node?.id) return;
    setSelected(node);
    setGraphFocusId(node.id);
  };

  const focusDefaultSeed = () => {
    const node = defaultSeedHub || defaultSeedRows[0];
    if (node) selectGraphNode(node);
  };

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
      <TabStrip items={[{ k: "vault", l: "Vault" }, { k: "graph", l: "Graph" }, { k: "advanced", l: "고급" }]} active={subtab} onChange={setSubtab} />
      {msg && <Banner tone={msg.includes("오류") ? "bad" : "ok"}>{msg}</Banner>}
      {subtab === "vault" && (
        <Panel
          title="Wiki Vault"
          subtitle="문서 목록, 본문, backlinks, 출처를 한 화면에서 확인합니다."
          right={<div style={{ display: "flex", gap: 6, flexWrap: "wrap", justifyContent: "flex-end" }}><Pill tone="accent">pages {pages.length}</Pill>{Object.entries(vaultStats).slice(0, 4).map(([kind, count]) => <Pill key={kind} tone="neutral">{kind} {count}</Pill>)}<Button onClick={loadWiki} disabled={busy}>{busy ? "로딩 중" : "새로고침"}</Button><Button onClick={() => setSubtab("advanced")}>새 지식</Button></div>}
        >
          <div className="agent-wiki-vault-grid">
            <div style={{ display: "grid", gap: 8, minWidth: 0 }}>
              <Field label="search">
                <div style={{ display: "flex", gap: 6 }}>
                  <input value={wikiSearch} onChange={(e) => setWikiSearch(e.target.value)} onKeyDown={(e) => e.key === "Enter" && loadWiki()} style={{ ...formControlStyle, width: "100%", boxSizing: "border-box" }} placeholder="title, tag, kind" />
                  <Button onClick={loadWiki} disabled={busy}>검색</Button>
                </div>
              </Field>
              <DataTable
                rows={visiblePages}
                empty="저장된 지식이 없습니다."
                onRowClick={openPage}
                maxHeight={640}
                columns={[
                  { key: "title", label: "문서", render: (r) => <div style={{ display: "grid", gap: 3 }}><KoreanClamp lines={1} title={r.title}>{r.title || r.doc_id}</KoreanClamp><span style={{ color: "var(--text-secondary)", fontSize: 11 }}>{r.doc_id}</span></div> },
                  { key: "kind", label: "분류", width: 92, render: (r) => <Pill tone={r.doc_id === selectedPage?.doc_id ? "accent" : "neutral"}>{r.kind || "-"}</Pill> },
                ]}
              />
            </div>
            <div style={{ minWidth: 0 }}>
              {selectedPage ? (
                <div style={{ display: "grid", gap: 10 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "flex-start" }}>
                    <div style={{ minWidth: 0 }}>
                      <div className="korean-wrap" style={{ fontSize: 22, fontWeight: 900, lineHeight: 1.28 }}>{selectedPage.title || selectedPage.doc_id}</div>
                      <div style={{ marginTop: 4, color: "var(--text-secondary)", fontSize: 12, wordBreak: "break-all" }}>{selectedPage.doc_id}</div>
                    </div>
                    <div style={{ display: "flex", gap: 6, flexWrap: "wrap", justifyContent: "flex-end" }}>
                      <Pill tone="accent">{selectedPage.kind || "wiki"}</Pill>
                      {(selectedPage.tags || []).slice(0, 6).map((tag) => <Pill key={tag} tone="neutral">{tag}</Pill>)}
                    </div>
                  </div>
                  {selectedPage.summary && <div className="korean-wrap" style={{ color: "var(--text-secondary)", lineHeight: 1.55 }}>{selectedPage.summary}</div>}
                  <pre className="korean-wrap" style={{ margin: 0, minHeight: 420, maxHeight: 640, overflow: "auto", whiteSpace: "pre-wrap", fontSize: 13, lineHeight: 1.68, background: "var(--bg-primary)", border: "1px solid var(--border)", borderRadius: 5, padding: 14 }}>{selectedPage.body || ""}</pre>
                </div>
              ) : (
                <EmptyState title="선택된 지식 없음" hint="왼쪽 Vault 목록에서 문서를 선택하세요." />
              )}
            </div>
            <div style={{ display: "grid", gap: 12, minWidth: 0 }}>
              <div style={vaultSideSectionStyle}>
                <div style={vaultSideHeadStyle}><strong>Backlinks</strong><span>{backlinkRows.length} links</span></div>
                <DataTable
                  rows={backlinkRows}
                  empty="연결된 문서가 없습니다."
                  maxHeight={260}
                  onRowClick={(row) => {
                    const node = graphNodeById.get(row.node_id);
                    if (node) selectGraphNode(node);
                    const docId = wikiDocIdFromNode({ id: row.node_id, kind: row.kind });
                    if (docId) openPage({ doc_id: docId });
                  }}
                  columns={[
                    { key: "label", label: "node", render: (r) => <KoreanClamp lines={1}>{r.label}</KoreanClamp> },
                    { key: "relation", label: "rel", width: 86, render: (r) => <Pill tone={r.direction === "in" ? "info" : "accent"}>{r.relation}</Pill> },
                  ]}
                />
              </div>
              <div style={vaultSideSectionStyle}>
                <div style={vaultSideHeadStyle}><strong>Sources</strong><span>{selectedSourceIds.length} refs</span></div>
                {selectedSourceIds.length ? (
                  <div style={{ display: "grid", gap: 5 }}>
                    {selectedSourceIds.slice(0, 12).map((id) => <code key={id} style={{ fontSize: 11, color: "var(--text-secondary)", wordBreak: "break-all" }}>{id}</code>)}
                  </div>
                ) : <EmptyState title="출처 없음" hint="source 등록 후 Wiki로 승격하면 여기에 남습니다." />}
              </div>
              <div style={vaultSideSectionStyle}>
                <div style={vaultSideHeadStyle}><strong>Metadata</strong><span>{selectedPage?.updated_at ? selectedPage.updated_at.replace("T", " ").slice(0, 16) : ""}</span></div>
                {selectedPage ? (
                  <DataTable
                    rows={[
                      { key: "kind", value: selectedPage.kind || "" },
                      { key: "tags", value: listText(selectedPage.tags, 8) },
                      { key: "path", value: selectedPage.path || "" },
                      { key: "actor", value: selectedPage.actor || "" },
                    ]}
                    columns={[
                      { key: "key", label: "key", width: 76 },
                      { key: "value", label: "value", render: (r) => <KoreanClamp lines={1}>{r.value}</KoreanClamp> },
                    ]}
                  />
                ) : <EmptyState title="메타 없음" hint="문서를 선택하세요." />}
              </div>
            </div>
          </div>
        </Panel>
      )}
      {subtab === "graph" && (
        <div style={{ display: "grid", gap: 12 }}>
          <Panel
            title="Wiki Graph"
            subtitle={showFullGraph ? "전체 raw graph edge를 확인합니다." : "승인된 Wiki/schema 연결만 확인합니다."}
            right={<div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", justifyContent: "flex-end" }}><Pill tone={showFullGraph ? "warn" : "accent"}>{showFullGraph ? "full" : "curated"}</Pill><Pill tone="accent">{graph.nodes.length} nodes</Pill><Pill tone="info">{graph.links.length} links</Pill>{defaultSeedRows.length > 0 && <Pill tone="warn">seed {defaultSeedRows.length}</Pill>}{defaultSeedRows.length > 0 && <Button onClick={focusDefaultSeed}>Seed 보기</Button>}{canManage && <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 800, color: "var(--text-secondary)" }}><input type="checkbox" checked={showFullGraph} onChange={(e) => setShowFullGraph(e.target.checked)} />전체 edge 보기</label>}<Button onClick={load} disabled={busy}>{busy ? "로딩 중" : "새로고침"}</Button><Button variant="primary" onClick={() => setModalOpen(true)} disabled={!canManage} title={!canManage ? "관리 권한이 필요합니다." : ""}>+</Button></div>}
          >
            <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(280px,0.36fr)", gap: 12, alignItems: "start" }}>
              <div style={{ display: "grid", gap: 10 }}>
                <Field label="search">
                  <input value={query} onChange={(e) => setQuery(e.target.value)} style={{ ...formControlStyle, width: "100%", boxSizing: "border-box" }} placeholder="doc, product, lot, relation" />
                </Field>
                <WikiGraph
                  graph={graph}
                  query={query}
                  selected={selected}
                  focusId={graphFocusId}
                  highlightId={highlightId}
                  onSelect={setSelected}
                  onFocusChange={setGraphFocusId}
                />
              </div>
              <div style={{ display: "grid", gap: 12 }}>
                <Panel
                  title="선택 지식 상세"
                  subtitle={selected?.id || "선택된 node 없음"}
                  right={<div style={{ display: "flex", gap: 8, alignItems: "center" }}>{detailBusy && <Pill tone="warn">loading</Pill>}{selectedDetail?.doc_id && <Button onClick={() => openEdit(selectedDetail)} disabled={!canManage || busy} title={!canManage ? "관리 권한이 필요합니다." : ""}>수정</Button>}</div>}
                >
                  {selected ? (
                    selectedDetail ? (
                      <div style={{ display: "grid", gap: 10 }}>
                        <div className="korean-wrap" style={{ fontSize: 18, fontWeight: 900, lineHeight: 1.35 }}>{selectedDetail.title || selected.label || selected.id}</div>
                        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                          <Pill tone="accent">{selectedDetail.kind || selected.kind || "wiki"}</Pill>
                          {(selectedDetail.tags || []).slice(0, 5).map((tag) => <Pill key={tag} tone="neutral">{tag}</Pill>)}
                        </div>
                        <div className="korean-wrap clamp-3" style={{ color: "var(--text-secondary)", lineHeight: 1.55 }}>{selectedDetail.summary || "요약 없음"}</div>
                        <pre className="korean-wrap" style={{ margin: 0, maxHeight: 360, overflow: "auto", whiteSpace: "pre-wrap", fontSize: 13, lineHeight: 1.6, background: "var(--bg-primary)", border: "1px solid var(--border)", borderRadius: 5, padding: 10 }}>{selectedDetail.body || ""}</pre>
                      </div>
                    ) : (
                      <DataTable
                        rows={Object.entries(selected).filter(([k]) => !["x", "y", "vx", "vy", "index"].includes(k)).map(([key, value]) => ({ key, value: typeof value === "object" ? JSON.stringify(value) : String(value ?? "") }))}
                        columns={[
                          { key: "key", label: "field", width: 110 },
                          { key: "value", label: "value", render: (r) => <KoreanClamp>{r.value}</KoreanClamp> },
                        ]}
                      />
                    )
                  ) : <EmptyState title="node detail 없음" hint="graph node를 클릭하세요." />}
                </Panel>
                {defaultSeedRows.length > 0 && (
                  <Panel title="기본 Seed" subtitle={`${defaultSeedRows.length} pages`}>
                    <DataTable
                      rows={defaultSeedRows}
                      empty="기본 seed 문서가 없습니다."
                      onRowClick={selectGraphNode}
                      maxHeight={240}
                      columns={[
                        { key: "label", label: "title", render: (r) => <KoreanClamp lines={1}>{r.label}</KoreanClamp> },
                        { key: "kind", label: "node", width: 86, render: () => <Pill tone="warn">seed</Pill> },
                      ]}
                    />
                  </Panel>
                )}
                <Panel title="Search Matches" subtitle={query ? `${matchingRows.length} matches` : "검색어를 입력하세요"}>
                  <DataTable
                    rows={matchingRows}
                    empty="검색 결과가 없습니다."
                    onRowClick={selectGraphNode}
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
          <Panel
            title="한글 지식 입력"
            subtitle="업무 메모, 회의 요약, RCA 문장을 그대로 붙여 넣고 maintained wiki page로 정리합니다."
            right={<div style={{ display: "flex", gap: 6, alignItems: "center" }}><Pill tone={canManage ? "accent" : "neutral"}>{canManage ? "manage" : "read only"}</Pill><Button onClick={loadWiki} disabled={busy}>새로고침</Button></div>}
          >
            <div style={{ display: "grid", gridTemplateColumns: "minmax(0,0.9fr) minmax(300px,0.55fr)", gap: 12, alignItems: "start" }}>
              <div style={{ display: "grid", gap: 8 }}>
                <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(0,0.7fr)", gap: 8 }}>
                  <Field label="제목">
                    <input value={wikiForm.title} onChange={(e) => setWikiForm({ ...wikiForm, title: e.target.value })} style={{ ...formControlStyle, width: "100%", boxSizing: "border-box" }} placeholder="예: SORT KNOB 운영 메모" />
                  </Field>
                  <Field label="태그">
                    <input value={wikiForm.tags} onChange={(e) => setWikiForm({ ...wikiForm, tags: e.target.value })} style={{ ...formControlStyle, width: "100%", boxSizing: "border-box" }} placeholder="PRODA, SORT, KNOB" />
                  </Field>
                </div>
                <Field label="본문">
                  <textarea value={wikiForm.content} onChange={(e) => setWikiForm({ ...wikiForm, content: e.target.value })} rows={10} className="korean-wrap" style={{ ...formControlStyle, width: "100%", boxSizing: "border-box", resize: "vertical", lineHeight: 1.6 }} placeholder="한국어 지식 내용을 입력하세요." />
                </Field>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                  <Button onClick={registerSource} disabled={!canManage || busy || !wikiForm.content.trim()} title={!canManage ? "관리 권한이 필요합니다." : ""}>Source 등록</Button>
                  <Button onClick={previewWiki} disabled={busy || !wikiForm.content.trim()}>미리보기</Button>
                  <Button variant="primary" onClick={commitWiki} disabled={!canManage || busy || !preview} title={!canManage ? "관리 권한이 필요합니다." : ""}>Wiki 저장</Button>
                  <Button onClick={runLint} disabled={!canManage || busy} title={!canManage ? "관리 권한이 필요합니다." : ""}>Lint</Button>
                  <label style={{ display: "inline-flex", alignItems: "center", gap: 4, cursor: "pointer", fontSize: 12, padding: "4px 8px", border: "1px solid #cbd5e1", borderRadius: 4, background: "#f8fafc" }}>
                    Markdown 파일
                    <input
                      type="file"
                      accept=".md,.markdown,.txt,text/markdown,text/plain"
                      style={{ display: "none" }}
                      onChange={(e) => {
                        const file = e.target.files && e.target.files[0];
                        if (!file) return;
                        const reader = new FileReader();
                        reader.onload = () => {
                          const text = String(reader.result || "");
                          setWikiForm((cur) => ({
                            ...cur,
                            content: text,
                            title: cur.title || file.name.replace(/\.[^.]+$/, ""),
                          }));
                          setMsg(`파일 로드: ${file.name} (${text.length}자)`);
                        };
                        reader.onerror = () => setMsg("파일 읽기 실패");
                        reader.readAsText(file, "utf-8");
                        e.target.value = "";
                      }}
                    />
                  </label>
                </div>
              </div>
              <div style={{ display: "grid", gap: 10 }}>
                <div style={{ display: "grid", gap: 8 }}>
                  <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                    <div style={{ fontSize: 14, fontWeight: 800, color: "var(--accent)" }}>미리보기</div>
                    <Pill tone="neutral">{preview?.doc_id || "commit 전 확인"}</Pill>
                  </div>
                  {preview ? (
                    <div style={{ display: "grid", gap: 8 }}>
                      <div className="korean-wrap" style={{ fontSize: 16, fontWeight: 900 }}>{preview.title}</div>
                      <KoreanClamp lines={3}>{preview.summary}</KoreanClamp>
                      <pre className="korean-wrap" style={{ margin: 0, maxHeight: 230, overflow: "auto", whiteSpace: "pre-wrap", fontSize: 13, lineHeight: 1.55, background: "var(--bg-primary)", border: "1px solid var(--border)", borderRadius: 5, padding: 10 }}>{preview.body}</pre>
                    </div>
                  ) : <EmptyState title="미리보기 없음" hint="본문을 입력하고 미리보기를 실행하세요." />}
                </div>
                <div style={{ display: "grid", gap: 8 }}>
                  <div style={{ fontSize: 14, fontWeight: 800, color: "var(--accent)" }}>운영 점검</div>
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                    <Pill tone="info">sources {sources.length}</Pill>
                    <Pill tone="accent">pages {pages.length}</Pill>
                    <Pill tone="info">relations {schemaGraph.relations.length}</Pill>
                    <Pill tone="neutral">columns {schemaGraph.column_catalog.length}</Pill>
                    {lint?.counts && Object.entries(lint.counts).slice(0, 4).map(([k, v]) => <Pill key={k} tone={v ? "warn" : "neutral"}>{k}: {v}</Pill>)}
                  </div>
                </div>
                <div style={{ display: "grid", gap: 8 }}>
                  <div style={{ fontSize: 14, fontWeight: 800, color: "var(--accent)" }}>DB/File 연결성</div>
                  <DataTable
                    rows={(schemaGraph.relations || []).slice(0, 8)}
                    empty="확인 저장된 relation이 없습니다."
                    maxHeight={220}
                    columns={[
                      { key: "left_label", label: "left", render: (r) => <KoreanClamp lines={1}>{r.left_label}</KoreanClamp> },
                      { key: "right_label", label: "right", render: (r) => <KoreanClamp lines={1}>{r.right_label}</KoreanClamp> },
                      { key: "canonical_key", label: "key", width: 110, render: (r) => <Pill tone="accent">{r.canonical_key || "-"}</Pill> },
                      { key: "relation_id", label: "id", width: 120, render: (r) => <KoreanClamp lines={1}>{r.relation_id}</KoreanClamp> },
                    ]}
                  />
                </div>
              </div>
            </div>
          </Panel>

          <Panel
            title="단일 파일 실행 지식"
            subtitle="관리자가 파일과 컬럼 역할을 확인한 뒤 schema catalog와 Wiki에 등록합니다."
            right={<div style={{ display: "flex", gap: 6, alignItems: "center" }}><Pill tone={canManage ? "accent" : "neutral"}>{canManage ? "approve" : "read only"}</Pill></div>}
          >
            <div style={{ display: "grid", gridTemplateColumns: "minmax(0,0.95fr) minmax(300px,0.55fr)", gap: 12, alignItems: "start" }}>
              <div style={{ display: "grid", gap: 8 }}>
                <div style={{ display: "grid", gridTemplateColumns: "130px minmax(0,1fr) 150px", gap: 8 }}>
                  <Field label="Root">
                    <select value={singleFileForm.root} onChange={(e) => setSingleFileForm({ ...singleFileForm, root: e.target.value })} style={{ ...formControlStyle, width: "100%" }}>
                      <option value="base_root">base_root</option>
                      <option value="db_root">db_root</option>
                    </select>
                  </Field>
                  <Field label="파일명">
                    <input value={singleFileForm.file} onChange={(e) => setSingleFileForm({ ...singleFileForm, file: e.target.value })} style={{ ...formControlStyle, width: "100%", boxSizing: "border-box" }} placeholder="ppid_knob.csv" />
                  </Field>
                  <Field label="용도">
                    <select value={singleFileForm.purpose} onChange={(e) => setSingleFileForm({ ...singleFileForm, purpose: e.target.value })} style={{ ...formControlStyle, width: "100%" }}>
                      <option value="rulebook">rulebook</option>
                      <option value="matching">matching</option>
                      <option value="schema_doc">schema_doc</option>
                      <option value="lookup_table">lookup_table</option>
                    </select>
                  </Field>
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)", gap: 8 }}>
                  <Field label="Key 컬럼">
                    <input value={singleFileForm.key_columns} onChange={(e) => setSingleFileForm({ ...singleFileForm, key_columns: e.target.value })} style={{ ...formControlStyle, width: "100%", boxSizing: "border-box" }} placeholder="product, step_id" />
                  </Field>
                  <Field label="Output 컬럼">
                    <input value={singleFileForm.output_columns} onChange={(e) => setSingleFileForm({ ...singleFileForm, output_columns: e.target.value })} style={{ ...formControlStyle, width: "100%", boxSizing: "border-box" }} placeholder="function_step, ppid" />
                  </Field>
                </div>
                <Field label="Wiki 제목">
                  <input value={singleFileForm.title} onChange={(e) => setSingleFileForm({ ...singleFileForm, title: e.target.value })} style={{ ...formControlStyle, width: "100%", boxSizing: "border-box" }} placeholder="SORT KNOB rulebook" />
                </Field>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  <Button onClick={previewSingleFile} disabled={busy || !singleFileForm.file.trim()}>미리보기</Button>
                  <Button variant="primary" onClick={registerSingleFile} disabled={!canManage || busy || !singleFileForm.file.trim()} title={!canManage ? "관리 권한이 필요합니다." : ""}>승인 등록</Button>
                </div>
              </div>
              <div style={{ display: "grid", gap: 8 }}>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  <Pill tone="info">{singleFilePreview?.source_id || "source 미확인"}</Pill>
                  <Pill tone="neutral">{singleFilePreview?.row_count ?? "-"} rows</Pill>
                  <Pill tone="neutral">{(singleFilePreview?.columns || []).length} columns</Pill>
                </div>
                <DataTable
                  rows={(singleFilePreview?.columns || []).slice(0, 24).map((col) => ({ column: col, samples: listText(singleFilePreview?.sample_values?.[col] || [], 4), dtype: singleFilePreview?.dtypes?.[col] || "" }))}
                  empty="미리보기할 파일을 선택하세요."
                  maxHeight={260}
                  columns={[
                    { key: "column", label: "column", render: (r) => <KoreanClamp lines={1}>{r.column}</KoreanClamp> },
                    { key: "dtype", label: "dtype", width: 86 },
                    { key: "samples", label: "samples", render: (r) => <KoreanClamp lines={1}>{r.samples}</KoreanClamp> },
                  ]}
                />
              </div>
            </div>
          </Panel>

          <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(320px,0.55fr)", gap: 12, alignItems: "start" }}>
            <Panel
              title="저장된 지식 표"
              subtitle="제목, 분류, 태그, 요약, 수정일, 출처 중심으로 확인합니다."
              right={<div style={{ display: "flex", gap: 8, alignItems: "end" }}><input value={wikiSearch} onChange={(e) => setWikiSearch(e.target.value)} onKeyDown={(e) => e.key === "Enter" && loadWiki()} style={{ ...formControlStyle, width: 220 }} placeholder="검색" /><Button onClick={loadWiki} disabled={busy}>검색</Button></div>}
            >
              <DataTable
                rows={pages}
                empty="저장된 지식이 없습니다."
                onRowClick={openPage}
                maxHeight={480}
                columns={[
                  { key: "title", label: "제목", render: (r) => <KoreanClamp lines={1} title={r.title}>{r.title}</KoreanClamp> },
                  { key: "kind", label: "분류", width: 110, render: (r) => <Pill tone="accent">{r.kind || "-"}</Pill> },
                  { key: "tags", label: "태그", width: 150, render: (r) => <KoreanClamp lines={1}>{listText(r.tags, 3)}</KoreanClamp> },
                  { key: "summary", label: "요약", render: (r) => <KoreanClamp lines={2}>{r.summary}</KoreanClamp> },
                  { key: "updated_at", label: "수정일", width: 128, render: (r) => String(r.updated_at || "").replace("T", " ").slice(0, 16) },
                  { key: "source_ids", label: "출처", width: 130, render: (r) => <KoreanClamp lines={1}>{pageSourceText(r)}</KoreanClamp> },
                ]}
              />
            </Panel>

            <Panel
              title="선택 지식 상세"
              subtitle={selectedPage?.doc_id || "표에서 지식을 선택하세요."}
              right={selectedPage?.doc_id ? <div style={{ display: "flex", gap: 8 }}><Button onClick={() => openEdit(selectedPage)} disabled={!canManage || busy} title={!canManage ? "관리 권한이 필요합니다." : ""}>수정</Button><Button variant="danger" onClick={deletePage} disabled={!canManage || busy} title={!canManage ? "관리 권한이 필요합니다." : ""}>삭제</Button></div> : null}
            >
              {selectedPage ? (
                <div style={{ display: "grid", gap: 10 }}>
                  <div className="korean-wrap" style={{ fontSize: 18, fontWeight: 900, lineHeight: 1.35 }}>{selectedPage.title}</div>
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                    <Pill tone="accent">{selectedPage.kind}</Pill>
                    {(selectedPage.tags || []).slice(0, 6).map((tag) => <Pill key={tag} tone="neutral">{tag}</Pill>)}
                  </div>
                  <KoreanClamp lines={3}>{selectedPage.summary || "요약 없음"}</KoreanClamp>
                  <pre className="korean-wrap" style={{ margin: 0, maxHeight: 430, overflow: "auto", whiteSpace: "pre-wrap", fontSize: 13, lineHeight: 1.6, background: "var(--bg-primary)", border: "1px solid var(--border)", borderRadius: 5, padding: 10 }}>{selectedPage.body || ""}</pre>
                </div>
              ) : <EmptyState title="선택된 지식 없음" hint="왼쪽 표에서 지식을 선택하세요." />}
            </Panel>
          </div>
        </div>
      )}
      <Modal open={editOpen} onClose={() => setEditOpen(false)} title="Wiki 수정" width={860}>
        <div style={{ display: "grid", gap: 10 }}>
          {!canManage && <Banner tone="warn">관리 권한이 필요합니다.</Banner>}
          <div style={{ display: "grid", gridTemplateColumns: "minmax(0,0.55fr) minmax(0,1fr)", gap: 8 }}>
            <Field label="doc_id">
              <input value={editForm.doc_id} readOnly style={{ ...formControlStyle, width: "100%", opacity: 0.78 }} />
            </Field>
            <Field label="title">
              <input value={editForm.title} onChange={(e) => setEditForm({ ...editForm, title: e.target.value })} style={{ ...formControlStyle, width: "100%" }} />
            </Field>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(0,0.55fr)", gap: 8 }}>
            <Field label="summary">
              <input value={editForm.summary} onChange={(e) => setEditForm({ ...editForm, summary: e.target.value })} style={{ ...formControlStyle, width: "100%" }} />
            </Field>
            <Field label="tags">
              <input value={editForm.tags} onChange={(e) => setEditForm({ ...editForm, tags: e.target.value })} style={{ ...formControlStyle, width: "100%" }} />
            </Field>
          </div>
          <Field label="body">
            <textarea value={editForm.body} onChange={(e) => setEditForm({ ...editForm, body: e.target.value })} rows={16} className="korean-wrap" style={{ ...formControlStyle, width: "100%", resize: "vertical", boxSizing: "border-box", lineHeight: 1.6 }} />
          </Field>
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
            <Button onClick={() => setEditOpen(false)}>취소</Button>
            <Button variant="primary" onClick={saveEdit} disabled={!canManage || busy || !editForm.title.trim()}>{busy ? "저장 중" : "저장"}</Button>
          </div>
        </div>
      </Modal>
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
