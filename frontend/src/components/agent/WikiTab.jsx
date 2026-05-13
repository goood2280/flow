import { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D from "react-force-graph-2d";
import Modal from "../Modal";
import { postJson, qs, sf } from "../../lib/api";
import { Banner, Button, DataTable, EmptyState, Field, Panel, Pill, TabStrip, formControlStyle } from "../UXKit";

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
  const [selectedDetail, setSelectedDetail] = useState(null);
  const [detailBusy, setDetailBusy] = useState(false);
  const [highlightId, setHighlightId] = useState("");
  const [modalOpen, setModalOpen] = useState(false);
  const [form, setForm] = useState({ doc_id: "", tags: "", body: "" });
  const [sources, setSources] = useState([]);
  const [pages, setPages] = useState([]);
  const [selectedPage, setSelectedPage] = useState(null);
  const [preview, setPreview] = useState(null);
  const [lint, setLint] = useState(null);
  const [wikiSearch, setWikiSearch] = useState("");
  const [wikiForm, setWikiForm] = useState({ title: "", tags: "", content: "" });
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

  const loadWiki = () => {
    setBusy(true);
    return Promise.all([
      sf("/api/agent/wiki/sources?limit=60"),
      sf("/api/agent/wiki/pages" + qs({ q: wikiSearch.trim(), limit: 160 })),
    ])
      .then(([s, p]) => {
        setSources(s.sources || []);
        const nextPages = p.pages || [];
        setPages(nextPages);
        setSelectedPage((cur) => cur && nextPages.find((row) => row.doc_id === cur.doc_id) ? cur : nextPages[0] || null);
        setMsg("");
      })
      .catch((e) => setMsg("wiki 오류: " + (e.message || e)))
      .finally(() => setBusy(false));
  };

  useEffect(() => { loadWiki(); }, []);

  useEffect(() => {
    if (!selected?.id) {
      setSelectedDetail(null);
      return undefined;
    }
    let alive = true;
    setDetailBusy(true);
    sf("/api/agent/wiki/page" + qs({ doc_id: selected.id }))
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
  }, [selected?.id]);

  const openPage = (row) => {
    const docId = row?.doc_id || row?.id;
    if (!docId) return;
    setBusy(true);
    sf("/api/agent/wiki/page" + qs({ doc_id: docId }))
      .then((d) => {
        const page = d.page || null;
        setSelectedPage(page);
        setSelected(graph.nodes.find((n) => n.id === docId) || { id: docId, label: page?.title || docId, kind: page?.kind || "wiki_doc" });
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
                <Panel title="선택 지식 상세" subtitle={selected?.id || "선택된 node 없음"} right={detailBusy ? <Pill tone="warn">loading</Pill> : null}>
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
                  {canManage && <Button onClick={registerSource} disabled={busy || !wikiForm.content.trim()}>Source 등록</Button>}
                  <Button onClick={previewWiki} disabled={busy || !wikiForm.content.trim()}>미리보기</Button>
                  {canManage && <Button variant="primary" onClick={commitWiki} disabled={busy || !preview}>Wiki 저장</Button>}
                  {canManage && <Button onClick={runLint} disabled={busy}>Lint</Button>}
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
                    {lint?.counts && Object.entries(lint.counts).slice(0, 4).map(([k, v]) => <Pill key={k} tone={v ? "warn" : "neutral"}>{k}: {v}</Pill>)}
                  </div>
                </div>
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
              right={canManage && selectedPage?.doc_id ? <Button variant="danger" onClick={deletePage} disabled={busy}>삭제</Button> : null}
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
