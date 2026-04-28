import { useCallback, useEffect, useMemo, useState } from "react";
import { postJson, sf, qs } from "../lib/api";
import {
  Banner,
  Button,
  DataTable,
  Field,
  PageHeader,
  PageShell,
  Panel,
  Pill,
  TabStrip,
  formControlStyle,
  statusPalette,
  uxColors,
} from "../components/UXKit";

const SAMPLE_PROMPT = "GAA nFET short Lg에서 DIBL과 SS가 증가했고 CA_RS도 올랐어. 원인 후보와 확인 차트 보여줘.";

function cx(...parts) {
  return parts.filter(Boolean).join(" ");
}

function fmt(v) {
  if (v === undefined || v === null || v === "") return "-";
  if (typeof v === "number") {
    if (!Number.isFinite(v)) return "-";
    if (Math.abs(v) >= 1000 || Math.abs(v) < 0.01) return v.toExponential(2);
    return v.toFixed(3).replace(/\.?0+$/, "");
  }
  return String(v);
}

function miniTableRows(report) {
  return (report?.ranked_hypotheses || []).map((h) => ({
    rank: h.rank,
    hypothesis: h.hypothesis,
    mechanism: h.electrical_mechanism,
    confidence: h.confidence,
    card: h.knowledge_card_id,
  }));
}

function ScatterPreview({ chart }) {
  const points = chart?.data?.points || [];
  const fit = chart?.data?.fit || {};
  const w = 460;
  const h = 220;
  const pad = 34;
  if (!points.length) {
    return <div style={{ color: uxColors.textSub, fontSize: 12, padding: 24 }}>이 chart spec은 데이터 포인트 없이 생성되었습니다.</div>;
  }
  const xs = points.map((p) => Number(p.x)).filter(Number.isFinite);
  const ys = points.map((p) => Number(p.y)).filter(Number.isFinite);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const sx = (x) => pad + ((Number(x) - minX) / ((maxX - minX) || 1)) * (w - pad * 2);
  const sy = (y) => h - pad - ((Number(y) - minY) / ((maxY - minY) || 1)) * (h - pad * 2);
  const x1 = minX;
  const x2 = maxX;
  const y1 = fit.slope != null ? fit.slope * x1 + fit.intercept : null;
  const y2 = fit.slope != null ? fit.slope * x2 + fit.intercept : null;
  return (
    <div style={{ overflow: "auto" }}>
      <svg width={w} height={h} style={{ display: "block", width: "100%", maxWidth: w, minWidth: 320 }}>
        <rect x="0" y="0" width={w} height={h} fill="var(--bg-primary)" />
        <line x1={pad} y1={h - pad} x2={w - pad} y2={h - pad} stroke="var(--border)" />
        <line x1={pad} y1={pad} x2={pad} y2={h - pad} stroke="var(--border)" />
        {y1 != null && y2 != null && (
          <line x1={sx(x1)} y1={sy(y1)} x2={sx(x2)} y2={sy(y2)} stroke="#f59e0b" strokeWidth="2" />
        )}
        {points.map((p, i) => (
          <circle key={i} cx={sx(p.x)} cy={sy(p.y)} r="4" fill="#3b82f6">
            <title>{p.lot_wf}: {fmt(p.x)}, {fmt(p.y)}</title>
          </circle>
        ))}
        <text x={pad} y={h - 10} fill="var(--text-secondary)" fontSize="10">{chart.x}</text>
        <text x="8" y={pad - 8} fill="var(--text-secondary)" fontSize="10">{chart.y}</text>
      </svg>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 6 }}>
        <Pill tone="info">n={chart?.data?.n || points.length}</Pill>
        {chart?.data?.correlation != null && <Pill tone="accent">corr={fmt(chart.data.correlation)}</Pill>}
        {fit?.r2 != null && <Pill tone="warn">R2={fmt(fit.r2)}</Pill>}
      </div>
    </div>
  );
}

function Pipeline({ report }) {
  const steps = report?.pipeline || [];
  return (
    <div style={{ display: "grid", gap: 8 }}>
      {steps.map((s) => (
        <div key={s.stage} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}>
          <Pill tone={s.status === "done" ? "ok" : "neutral"}>{s.status}</Pill>
          <span style={{ fontFamily: "monospace", color: uxColors.text }}>{s.stage}</span>
          <span style={{ color: uxColors.textSub, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {typeof s.output === "string" ? s.output : JSON.stringify(s.output)}
          </span>
        </div>
      ))}
    </div>
  );
}

function CardList({ title, rows, render }) {
  return (
    <Panel title={title} bodyStyle={{ display: "grid", gap: 8 }}>
      {(rows || []).length ? rows.map(render) : <div style={{ color: uxColors.textSub, fontSize: 12 }}>데이터 없음</div>}
    </Panel>
  );
}

export default function My_Diagnosis({ user }) {
  const [active, setActive] = useState("diagnosis");
  const [prompt, setPrompt] = useState(SAMPLE_PROMPT);
  const [product, setProduct] = useState("PRODA");
  const [sourceFile, setSourceFile] = useState("");
  const [sourceRoot, setSourceRoot] = useState("");
  const [sourceLabel, setSourceLabel] = useState("");
  const [sourcePayload, setSourcePayload] = useState(null);
  const [sourceProfile, setSourceProfile] = useState(null);
  const [report, setReport] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [q, setQ] = useState("CA");
  const [items, setItems] = useState([]);
  const [manifest, setManifest] = useState(null);
  const [useCases, setUseCases] = useState([]);
  const [prior, setPrior] = useState({ module: "", use_case: "", prior_knowledge: "", tags: "" });
  const [ragPrompt, setRagPrompt] = useState("[flow-i RAG Update] PC-CB-M1 Chain item은 14x14, 13x13, 12x12 DOE TEG가 다르고 gate pitch와 Cell height를 구분해서 봐야 함.");
  const [ragResult, setRagResult] = useState(null);
  const [refPrompt, setRefPrompt] = useState("PC-CB-M1 Chain item은 14x14, 13x13, 12x12 DOE TEG가 다르고 gate pitch와 Cell height discriminator를 유지해서 alias화해야 함.");
  const [refProposal, setRefProposal] = useState(null);
  const [tegRowsText, setTegRowsText] = useState('[{"name":"TEG_TOP","x":13.6,"y":29.6,"width":1.2,"height":0.6},{"name":"TEG_RIGHT","x":27.6,"y":14.6}]');
  const [tegProposal, setTegProposal] = useState(null);
  const isAdmin = user?.role === "admin";

  const tabs = [
    { k: "diagnosis", l: "RCA" },
    { k: "dictionary", l: "Item Dictionary" },
    { k: "knowledge", l: "Knowledge" },
  ];

  const loadManifest = () => {
    sf("/api/semiconductor/knowledge")
      .then(setManifest)
      .catch((e) => setErr(e.message || String(e)));
    sf("/api/semiconductor/use-cases")
      .then((d) => setUseCases(d.use_cases || []))
      .catch(() => {});
  };

  const search = (nextQ = q) => {
    sf("/api/items/search" + qs({ q: nextQ, limit: 100 }))
      .then((d) => setItems(d.items || []))
      .catch((e) => setErr(e.message || String(e)));
  };

  useEffect(() => {
    loadManifest();
    search(q);
  }, []);

  const buildSourceFilter = useCallback(() => {
    if (sourceFile.trim()) return { source_type: "base_file", file: sourceFile.trim(), product };
    if (sourceRoot.trim()) return { root: sourceRoot.trim(), product };
    return {};
  }, [product, sourceFile, sourceRoot]);

  const refreshSourceProfile = useCallback((source) => {
    if (!source || !Object.keys(source).length) {
      setSourceProfile(null);
      return;
    }
    postJson("/api/semiconductor/dataset/profile", { source, limit: 300 })
      .then(setSourceProfile)
      .catch((e) => setSourceProfile({ ok: false, reason: e.message || String(e), warnings: [e.message || String(e)] }));
  }, []);

  const applyIncomingSource = useCallback((payload) => {
    if (!payload?.source) return;
    const src = payload.source || {};
    setSourcePayload(payload);
    setSourceLabel(payload.label || src.file || (src.root && src.product ? `${src.root}/${src.product}` : ""));
    if (payload.product || src.product) setProduct(payload.product || src.product);
    if (src.file) {
      setSourceFile(src.file);
      setSourceRoot("");
    } else if (src.root) {
      setSourceRoot(src.root);
      setSourceFile("");
    }
    refreshSourceProfile(src);
  }, [refreshSourceProfile]);

  useEffect(() => {
    const readStoredSource = () => {
      try {
        const raw = sessionStorage.getItem("flow_diagnosis_source");
        if (!raw) return;
        const payload = JSON.parse(raw);
        if (!payload?.source || payload.ts === sourcePayload?.ts) return;
        applyIncomingSource(payload);
      } catch (_) {}
    };
    const onSource = (e) => applyIncomingSource(e.detail);
    readStoredSource();
    window.addEventListener("flow:diagnosis-source", onSource);
    window.addEventListener("focus", readStoredSource);
    return () => {
      window.removeEventListener("flow:diagnosis-source", onSource);
      window.removeEventListener("focus", readStoredSource);
    };
  }, [applyIncomingSource, sourcePayload?.ts]);

  const run = () => {
    setBusy(true);
    setErr("");
    const source = buildSourceFilter();
    postJson("/api/diagnosis/run", { prompt, product, filters: source, save: true })
      .then((d) => {
        setReport(d);
        setActive("diagnosis");
      })
      .catch((e) => setErr(e.message || String(e)))
      .finally(() => setBusy(false));
  };

  const savePrior = () => {
    const tags = String(prior.tags || "").split(",").map((x) => x.trim()).filter(Boolean);
    postJson("/api/semiconductor/engineer-knowledge", { ...prior, product, tags })
      .then(() => {
        setPrior({ module: "", use_case: "", prior_knowledge: "", tags: "" });
        loadManifest();
      })
      .catch((e) => setErr(e.message || String(e)));
  };

  const saveRagUpdate = () => {
    setErr("");
    postJson("/api/semiconductor/knowledge/update-prompt", { prompt: ragPrompt })
      .then((d) => {
        setRagResult(d);
        loadManifest();
      })
      .catch((e) => setErr(e.message || String(e)));
  };

  const proposeReformatter = () => {
    setErr("");
    const source = buildSourceFilter();
    postJson("/api/semiconductor/reformatter/propose", { product, prompt: refPrompt, sample_columns: [], source, use_dataset: !!Object.keys(source).length })
      .then(setRefProposal)
      .catch((e) => setErr(e.message || String(e)));
  };

  const applyReformatter = () => {
    if (!refProposal?.rules?.length) return;
    setErr("");
    postJson("/api/semiconductor/reformatter/apply", { product, rules: refProposal.rules })
      .then((d) => setRefProposal({ ...refProposal, applied: d }))
      .catch((e) => setErr(e.message || String(e)));
  };

  const proposeTeg = () => {
    setErr("");
    let rows = [];
    try {
      rows = JSON.parse(tegRowsText || "[]");
      if (!Array.isArray(rows)) rows = [];
    } catch (e) {
      setErr("TEG rows는 JSON array 형식이어야 합니다.");
      return;
    }
    const source = buildSourceFilter();
    postJson("/api/semiconductor/teg/propose", { product, rows, prompt: "", source, use_dataset: !!Object.keys(source).length })
      .then(setTegProposal)
      .catch((e) => setErr(e.message || String(e)));
  };

  const applyTeg = () => {
    if (!tegProposal?.teg_definitions?.length) return;
    setErr("");
    postJson("/api/semiconductor/teg/apply", { product, teg_definitions: tegProposal.teg_definitions })
      .then((d) => setTegProposal({ ...tegProposal, applied: d }))
      .catch((e) => setErr(e.message || String(e)));
  };

  const resolvedRows = useMemo(() => {
    const ok = report?.interpreted_items?.resolved || [];
    return ok.map((r) => ({
      raw: r.raw_item,
      status: r.status,
      canonical: r.canonical_item_id || (r.candidates || []).map((c) => c.canonical_item_id).join(" / "),
      meaning: r.item?.meaning || r.ambiguity || "",
      unit: r.item?.unit || "",
      structure: r.item?.test_structure || "",
    }));
  }, [report]);

  return (
    <PageShell>
      <PageHeader
        title="반도체 진단/RCA"
        subtitle="Feature Extractor + Knowledge Card + Causal Graph + RAG + Case DB + Eval"
        right={<Pill tone="accent">mock deterministic</Pill>}
      />
      <div style={{ padding: 12, display: "grid", gap: 12 }}>
        {err && <Banner tone="bad" onClose={() => setErr("")}>{err}</Banner>}
        <Panel
          title="Chat/RCA"
          subtitle="LLM은 직접 SQL을 만들지 않고 backend whitelist tool만 호출하는 구조"
          right={<Button variant="primary" onClick={run} disabled={busy}>{busy ? "실행 중" : "진단 실행"}</Button>}
        >
          <div style={{ display: "grid", gap: 10 }}>
            <div style={{ display: "grid", gridTemplateColumns: "120px 220px 220px auto", gap: 8, alignItems: "end" }}>
              <Field label="product">
                <input value={product} onChange={(e) => setProduct(e.target.value)} style={formControlStyle} />
              </Field>
              <Field label="DB root/product" hint="FileBrowser DB 선택 시 자동 입력">
                <input value={sourceRoot} onChange={(e) => { setSourceRoot(e.target.value); if (e.target.value) setSourceFile(""); }} style={formControlStyle} placeholder="1.RAWDATA_DB_ET" />
              </Field>
              <Field label="Files single file" hint="ET/INLINE/EDS/VM/QTIME parquet/csv">
                <input value={sourceFile} onChange={(e) => { setSourceFile(e.target.value); if (e.target.value) setSourceRoot(""); }} style={formControlStyle} placeholder="ET_PRODA.parquet" />
              </Field>
              <Button onClick={() => refreshSourceProfile(buildSourceFilter())} disabled={!Object.keys(buildSourceFilter()).length}>Profile</Button>
            </div>
            <Field label="prompt">
              <textarea
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                rows={3}
                style={{ ...formControlStyle, resize: "vertical", minHeight: 72, lineHeight: 1.5 }}
                onKeyDown={(e) => {
                  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") run();
                }}
              />
            </Field>
            {(sourceLabel || sourceProfile) && (
              <div style={{ border: "1px solid var(--border)", background: "var(--bg-secondary)", borderRadius: 6, padding: 8, display: "grid", gap: 6 }}>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
                  {sourceLabel && <Pill tone="accent">source: {sourceLabel}</Pill>}
                  {sourcePayload?.mode && <Pill tone="neutral">{sourcePayload.mode}</Pill>}
                  {sourceProfile?.ok && <Pill tone="info">{sourceProfile.suggested_source_type}</Pill>}
                  {sourceProfile?.ok && <Pill tone="neutral">{sourceProfile.metric_shape} / {sourceProfile.grain}</Pill>}
                  {sourceProfile?.ok && <Pill tone="neutral">join: {(sourceProfile.join_keys || []).slice(0, 6).join(", ") || "-"}</Pill>}
                  {sourceProfile?.ok === false && <Pill tone="warn">profile failed</Pill>}
                </div>
                {sourceProfile?.ok && (
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap", fontSize: 11, color: uxColors.textSub }}>
                    <span>aggregation: {sourceProfile.default_aggregation}</span>
                    {(sourceProfile.unique_items || []).slice(0, 10).map((x) => <Pill key={x} tone="neutral">{x}</Pill>)}
                  </div>
                )}
                {!!(sourceProfile?.warnings || []).length && (
                  <div style={{ fontSize: 11, color: statusPalette.warn.fg }}>{sourceProfile.warnings.join(" / ")}</div>
                )}
              </div>
            )}
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {(manifest?.tools || []).slice(0, 11).map((t) => <Pill key={t.name} tone="neutral">{t.name}</Pill>)}
            </div>
          </div>
        </Panel>

        <TabStrip items={tabs} active={active} onChange={setActive} />

        {active === "diagnosis" && (
          <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.2fr) minmax(320px, 0.8fr)", gap: 12 }}>
            <div style={{ display: "grid", gap: 12, minWidth: 0 }}>
              <Panel title="Interpreted Items" subtitle="item_master + unit/source/test_structure/layer/method 기반">
                <DataTable
                  rows={resolvedRows}
                  columns={[
                    { key: "raw", label: "raw" },
                    { key: "status", label: "status", render: (r) => <Pill tone={r.status === "ambiguous" ? "warn" : "ok"}>{r.status}</Pill> },
                    { key: "canonical", label: "canonical" },
                    { key: "unit", label: "unit" },
                    { key: "structure", label: "structure" },
                    { key: "meaning", label: "meaning" },
                  ]}
                />
              </Panel>

              <Panel title="Top Root-Cause Hypotheses">
                <DataTable
                  rows={miniTableRows(report)}
                  columns={[
                    { key: "rank", label: "rank", width: 54 },
                    { key: "hypothesis", label: "hypothesis" },
                    { key: "mechanism", label: "electrical mechanism" },
                    { key: "confidence", label: "confidence", render: (r) => <Pill tone={r.confidence >= 0.65 ? "accent" : "warn"}>{fmt(r.confidence)}</Pill> },
                    { key: "card", label: "card" },
                  ]}
                />
              </Panel>

              <Panel title="Charts">
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(320px,1fr))", gap: 12 }}>
                  {(report?.charts || []).map((chart, i) => (
                    <div key={i} style={{ border: "1px solid var(--border)", borderRadius: 6, padding: 10, minWidth: 0 }}>
                      <div style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 8 }}>
                        <Pill tone="info">{chart.type}</Pill>
                        <span style={{ fontSize: 12, fontWeight: 700, color: uxColors.text }}>{chart.x || chart.metric} vs {chart.y || chart.metric}</span>
                      </div>
                      <ScatterPreview chart={chart} />
                    </div>
                  ))}
                  {!(report?.charts || []).length && <div style={{ color: uxColors.textSub, fontSize: 12 }}>진단을 실행하면 chart spec이 표시됩니다.</div>}
                </div>
              </Panel>
            </div>

            <div style={{ display: "grid", gap: 12, alignContent: "start", minWidth: 0 }}>
              <Panel title="Execution Pipeline">
                <Pipeline report={report} />
              </Panel>
              <CardList
                title="Recommended Checks"
                rows={report?.recommended_action_plan || []}
                render={(r, i) => (
                  <div key={i} style={{ padding: 8, border: "1px solid var(--border)", borderRadius: 6, fontSize: 12 }}>
                    <Pill tone="accent">P{r.priority}</Pill>
                    <span style={{ marginLeft: 8 }}>{r.action}</span>
                  </div>
                )}
              />
              <CardList
                title="Missing Data / Guardrails"
                rows={[...(report?.missing_data || []), ...(report?.do_not_conclude || [])]}
                render={(r, i) => (
                  <div key={i} style={{ display: "flex", gap: 8, fontSize: 12 }}>
                    <span style={{ width: 8, height: 8, borderRadius: 8, background: i < (report?.missing_data || []).length ? statusPalette.warn.fg : statusPalette.bad.fg, marginTop: 5 }} />
                    <span>{r}</span>
                  </div>
                )}
              />
              <CardList
                title="Similar Cases"
                rows={report?.similar_cases || []}
                render={(r) => (
                  <div key={r.case_id} style={{ border: "1px solid var(--border)", borderRadius: 6, padding: 10, fontSize: 12 }}>
                    <div style={{ fontWeight: 800 }}>{r.title}</div>
                    <div style={{ color: uxColors.textSub, marginTop: 4 }}>{(r.evidence || []).join(" / ")}</div>
                  </div>
                )}
              />
            </div>
          </div>
        )}

        {active === "dictionary" && (
          <div style={{ display: "grid", gap: 12 }}>
            <Panel title="Item Dictionary" subtitle="raw item name만으로 의미를 추론하지 않기 위한 기준 테이블">
              <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
                <input
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && search(e.currentTarget.value)}
                  style={{ ...formControlStyle, width: 260 }}
                  placeholder="DIBL, CA, RSD..."
                />
                <Button onClick={() => search(q)}>검색</Button>
              </div>
              <DataTable
                rows={items}
                maxHeight={520}
                columns={[
                  { key: "canonical_item_id", label: "canonical", width: 130 },
                  { key: "source_type", label: "source", width: 80 },
                  { key: "unit", label: "unit", width: 90 },
                  { key: "test_structure", label: "structure", width: 140 },
                  { key: "layer", label: "layer", width: 90 },
                  { key: "measurement_method", label: "method" },
                  { key: "meaning", label: "meaning" },
                ]}
              />
            </Panel>
          </div>
        )}

        {active === "knowledge" && (
          <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(320px,0.8fr)", gap: 12 }}>
            <Panel title="Knowledge Storage" subtitle="기본 seed는 코드/Git, 사내 추가 지식은 flow-data">
              <div style={{ display: "grid", gap: 8, fontSize: 12 }}>
                <div><Pill tone="accent">seed</Pill> <code>{manifest?.code_seed?.python_module}</code></div>
                <div><Pill tone="info">runtime</Pill> <code>{manifest?.runtime_data?.custom_knowledge}</code></div>
                <div><Pill tone="info">engineer</Pill> <code>{manifest?.runtime_data?.engineer_knowledge}</code></div>
                <div style={{ color: uxColors.textSub }}>{manifest?.setup_policy?.operator_action}</div>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 4 }}>
                  {Object.entries(manifest?.counts || {}).map(([k, v]) => <Pill key={k} tone="neutral">{k}: {v}</Pill>)}
                </div>
              </div>
            </Panel>
            <Panel title="Source Type Profiles" subtitle="FAB/INLINE/ET 외 VM/QTIME/EDS 확장 시 지식 부착 기준" style={{ gridColumn: "1 / -1" }}>
              <DataTable
                rows={manifest?.source_type_profiles || []}
                columns={[
                  { key: "source_type", label: "source", width: 90 },
                  { key: "default_grain", label: "grain" },
                  { key: "join_keys", label: "join keys", render: (r) => (r.join_keys || []).join(", ") },
                  { key: "default_aggregation", label: "aggregation" },
                  { key: "knowledge_to_attach", label: "knowledge", render: (r) => (r.knowledge_to_attach || []).join(", ") },
                  { key: "guardrails", label: "guardrails", render: (r) => (r.guardrails || []).join(" / ") },
                ]}
              />
            </Panel>
            <Panel title="Engineer Prior Knowledge" subtitle="사용자별 업무 성향과 사전지식 입력">
              <div style={{ display: "grid", gap: 8 }}>
                <Field label="module">
                  <input value={prior.module} onChange={(e) => setPrior({ ...prior, module: e.target.value })} style={formControlStyle} placeholder="RMG_WFM, CA_MOL_CONTACT..." />
                </Field>
                <Field label="use_case">
                  <input value={prior.use_case} onChange={(e) => setPrior({ ...prior, use_case: e.target.value })} style={formControlStyle} placeholder="Daily excursion triage" />
                </Field>
                <Field label="prior_knowledge">
                  <textarea value={prior.prior_knowledge} onChange={(e) => setPrior({ ...prior, prior_knowledge: e.target.value })} rows={4} style={{ ...formControlStyle, resize: "vertical" }} />
                </Field>
                <Field label="tags">
                  <input value={prior.tags} onChange={(e) => setPrior({ ...prior, tags: e.target.value })} style={formControlStyle} placeholder="DIBL, GAA, short Lg" />
                </Field>
                <Button variant="primary" onClick={savePrior} disabled={!prior.prior_knowledge.trim()}>내 사전지식 저장</Button>
              </div>
            </Panel>
            <Panel title="Flow-i RAG Update" subtitle="[flow-i update] 또는 [flow-i RAG Update] 마커가 있는 지식만 flow-data에 append-only 저장" style={{ gridColumn: "1 / -1" }}>
              <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(280px,0.6fr)", gap: 12 }}>
                <div style={{ display: "grid", gap: 8 }}>
                  <textarea value={ragPrompt} onChange={(e) => setRagPrompt(e.target.value)} rows={5} style={{ ...formControlStyle, resize: "vertical" }} />
                  <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                    <Button variant="primary" onClick={saveRagUpdate} disabled={!ragPrompt.trim()}>RAG Update 저장</Button>
                    <span style={{ fontSize: 11, color: uxColors.textSub }}>Admin은 public, 일반 user는 private으로 저장됩니다.</span>
                  </div>
                </div>
                <div style={{ fontSize: 12, color: uxColors.textSub }}>
                  <div><Pill tone="accent">target</Pill> <code>{manifest?.runtime_data?.custom_knowledge}</code></div>
                  <div style={{ marginTop: 8 }}>입력 예: real item의 TEG size, gate pitch, cell height, 좌표계, DOE 의미, alias 시 보존할 discriminator.</div>
                  {ragResult?.structured && (
                    <pre style={{ marginTop: 8, maxHeight: 160, overflow: "auto", fontSize: 11, background: "var(--bg-primary)", border: "1px solid var(--border)", borderRadius: 6, padding: 8 }}>
                      {JSON.stringify(ragResult.structured, null, 2)}
                    </pre>
                  )}
                </div>
              </div>
            </Panel>
            <Panel title="Reformatter Alias Proposal" subtitle="real item alias 후보를 만들고 admin만 product reformatter에 apply" style={{ gridColumn: "1 / -1" }}>
              <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(340px,0.8fr)", gap: 12 }}>
                <div style={{ display: "grid", gap: 8 }}>
                  <textarea value={refPrompt} onChange={(e) => setRefPrompt(e.target.value)} rows={4} style={{ ...formControlStyle, resize: "vertical" }} />
                  <div style={{ display: "flex", gap: 8 }}>
                    <Button onClick={proposeReformatter}>후보 생성</Button>
                    <Button variant="primary" onClick={applyReformatter} disabled={!isAdmin || !refProposal?.rules?.length}>Admin apply</Button>
                  </div>
                  {!isAdmin && <Banner tone="warn">apply는 admin만 가능합니다. 일반 사용자는 후보 생성과 지식 저장까지만 가능합니다.</Banner>}
                </div>
                <div>
                  <DataTable
                    rows={refProposal?.table_rows || []}
                    maxHeight={260}
                    columns={[
                      { key: "item_id", label: "raw item" },
                      { key: "alias", label: "alias" },
                      { key: "report_cat1", label: "cat" },
                      { key: "report_cat2", label: "review" },
                    ]}
                  />
                  {refProposal?.applied && <Banner tone={refProposal.applied.ok ? "ok" : "warn"} style={{ marginTop: 8 }}>saved: {refProposal.applied.path} / added {refProposal.applied.added}</Banner>}
                </div>
              </div>
            </Panel>
            <Panel title="TEG YAML Proposal" subtitle="TEG 좌표 표를 product YAML wafer_layout.teg_definitions 후보로 변환" style={{ gridColumn: "1 / -1" }}>
              <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(340px,0.8fr)", gap: 12 }}>
                <div style={{ display: "grid", gap: 8 }}>
                  <textarea value={tegRowsText} onChange={(e) => setTegRowsText(e.target.value)} rows={5} style={{ ...formControlStyle, resize: "vertical", fontFamily: "monospace" }} />
                  <div style={{ display: "flex", gap: 8 }}>
                    <Button onClick={proposeTeg}>YAML 후보 생성</Button>
                    <Button variant="primary" onClick={applyTeg} disabled={!isAdmin || !tegProposal?.teg_definitions?.length}>Admin apply</Button>
                  </div>
                </div>
                <div>
                  <DataTable
                    rows={tegProposal?.teg_definitions || []}
                    maxHeight={260}
                    columns={[
                      { key: "id", label: "id" },
                      { key: "label", label: "label" },
                      { key: "dx_mm", label: "dx_mm" },
                      { key: "dy_mm", label: "dy_mm" },
                    ]}
                  />
                  {tegProposal?.applied && <Banner tone={tegProposal.applied.ok ? "ok" : "warn"} style={{ marginTop: 8 }}>saved: {tegProposal.applied.path} / TEG {tegProposal.applied.teg_count}</Banner>}
                </div>
              </div>
            </Panel>
            <Panel title="Engineer Use Case Templates" style={{ gridColumn: "1 / -1" }}>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(260px,1fr))", gap: 10 }}>
                {useCases.map((uc) => (
                  <div key={uc.id} style={{ border: "1px solid var(--border)", borderRadius: 6, padding: 10, fontSize: 12 }}>
                    <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                      <Pill tone="accent">{uc.role}</Pill>
                      <b>{uc.workflow}</b>
                    </div>
                    <div style={{ marginTop: 8, color: uxColors.textSub }}>{(uc.default_questions || []).join(" / ")}</div>
                    <div style={{ marginTop: 8, display: "flex", gap: 5, flexWrap: "wrap" }}>
                      {(uc.prior_knowledge_slots || []).map((x) => <Pill key={x}>{x}</Pill>)}
                    </div>
                  </div>
                ))}
              </div>
            </Panel>
          </div>
        )}
      </div>
    </PageShell>
  );
}
