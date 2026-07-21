import { useEffect, useMemo, useState } from "react";
import { Banner, Button, Field, Panel, Pill, TabStrip, Textarea } from "../UXKit";
import { postJson, putJson, sf } from "../../lib/api";
import JsonBlock from "./JsonBlock";

// v9.1.x: Semantic layer 편집기 — 에이전트 탭에서 관리 탭(Flow-i 학습)으로 이관하며
// My_Diagnosis.jsx에서 추출. API 계약(/api/agent/semantic/*)은 그대로 유지한다.
const SEMANTIC_LEXICON_ENDPOINT = "/api/agent/semantic/lexicon";
const SEMANTIC_SOURCES_ENDPOINT = "/api/agent/semantic/sources";
const SEMANTIC_MEASUREMENTS_ENDPOINT = "/api/agent/semantic/measurements";
const SEMANTIC_PROPOSALS_ENDPOINT = "/api/agent/semantic/proposals?status=pending&limit=100";
const SEMANTIC_SECTIONS = [
  { k: "lexicon", l: "Lexicon 관리" },
  { k: "sources", l: "Sources 관리" },
  { k: "measurements", l: "Measurements 관리" },
  { k: "review", l: "검토 이력" },
];

function semanticSectionSummary(section, counts) {
  if (section === "sources") return `${counts.sourceCount || 0} sources`;
  if (section === "measurements") return `${counts.measurementCount || 0} terms`;
  if (section === "review") return `${counts.proposalCount || 0} pending · ${counts.changeCount || 0} changes`;
  return `${counts.aliasCount || 0} alias · ${counts.intentCount || 0} intent`;
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

export default function SemanticLayerPanel() {
  const [payload, setPayload] = useState(null);
  const [sourceCatalog, setSourceCatalog] = useState({ sources: {}, disk: {}, deleted_ids: [], roles: {}, docs_base: "docs/semantic" });
  const [measurementCatalog, setMeasurementCatalog] = useState({ terms: [], path: "", change_log_path: "" });
  const [aliasJson, setAliasJson] = useState("{}");
  const [intentJson, setIntentJson] = useState("{}");
  const [sourceJson, setSourceJson] = useState("{}");
  const [measurementJson, setMeasurementJson] = useState("{}");
  const [sourceNaturalText, setSourceNaturalText] = useState("");
  const [measurementNaturalText, setMeasurementNaturalText] = useState("");
  const [draftText, setDraftText] = useState("");
  const [draft, setDraft] = useState(null);
  const [proposalCanonicals, setProposalCanonicals] = useState({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [activeSection, setActiveSection] = useState("lexicon");
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
        disk: sourcesPayload?.disk || {},
        deleted_ids: sourcesPayload?.deleted_ids || [],
        roles: sourcesPayload?.roles || {},
        docs_base: sourcesPayload?.docs_base || "docs/semantic",
        path: sourcesPayload?.path || "",
        change_log_path: sourcesPayload?.change_log_path || "",
      });
      const sourceRows = Object.values(sourcesPayload?.sources || {});
      setSourceJson(JSON.stringify(Object.fromEntries(sourceRows.map((source) => [source.id || source.source_id, source])), null, 2));
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

  const saveSourceJson = () => {
    let next = {};
    try {
      next = parseJsonObject(sourceJson, "source_catalog");
    } catch (e) {
      setErr(e.message || String(e));
      return;
    }
    const current = sourceCatalog?.disk || {};
    const deletions = Object.keys(current).filter((key) => !Object.prototype.hasOwnProperty.call(next, key));
    setBusy(true);
    setErr("");
    setMsg("");
    Promise.all([
      ...deletions.map((key) => sf(`/api/agent/semantic/sources/${encodeURIComponent(key)}`, { method: "DELETE" })),
      ...Object.entries(next).map(([key, value]) => putJson(
        `/api/agent/semantic/sources/${encodeURIComponent(key)}`,
        { source: { ...(value || {}), id: key } }
      )),
    ]).then(() => {
      setMsg("source catalog 저장 완료");
      return load();
    }).catch((e) => setErr(e.message || String(e)))
      .finally(() => setBusy(false));
  };

  const saveSourceNatural = () => {
    setBusy(true);
    setErr("");
    setMsg("");
    postJson("/api/agent/semantic/draft", { text: sourceNaturalText })
      .then((out) => {
        const draftPayload = out?.draft || {};
        const entries = draftPayload.source_catalog || {};
        setDraft(draftPayload);
        if (!Object.keys(entries).length) {
          throw new Error("source catalog 초안을 만들 수 없습니다. id/title/path/role/docs_path 중 일부를 포함해 주세요.");
        }
        return Promise.all(Object.entries(entries).map(([key, value]) => putJson(
          `/api/agent/semantic/sources/${encodeURIComponent(key)}`,
          { source: { ...(value || {}), id: key } }
        )));
      })
      .then(() => {
        setSourceNaturalText("");
        setMsg("source 자연어 저장 완료");
        return load();
      })
      .catch((e) => setErr(e.message || String(e)))
      .finally(() => setBusy(false));
  };

  const editSourceEntry = (source) => {
    const id = source?.id || source?.source_id || "";
    if (!id) return;
    let next = {};
    try {
      next = parseJsonObject(sourceJson, "source_catalog");
    } catch {
      const sources = sourceCatalog?.sources || {};
      next = Array.isArray(sources) ? Object.fromEntries(sources.map((row) => [row.id || row.source_id, row])) : { ...sources };
    }
    next[id] = source;
    setSourceJson(JSON.stringify(next, null, 2));
    setMsg(`${id} source를 JSON 편집기에 올렸습니다.`);
  };

  const deleteSourceEntry = (source) => {
    const id = source?.id || source?.source_id || "";
    if (!id) return;
    if (typeof window !== "undefined" && !window.confirm(`${id} source를 삭제할까요?`)) return;
    setBusy(true);
    setErr("");
    setMsg("");
    sf(`/api/agent/semantic/sources/${encodeURIComponent(id)}`, { method: "DELETE" })
      .then(() => {
        setMsg(`${id} source 삭제 완료`);
        return load();
      })
      .catch((e) => setErr(e.message || String(e)))
      .finally(() => setBusy(false));
  };

  const addSourceTemplate = () => {
    try {
      const next = parseJsonObject(sourceJson, "source_catalog");
      let idx = 1;
      let id = "source_custom";
      while (Object.prototype.hasOwnProperty.call(next, id)) {
        idx += 1;
        id = `source_custom_${idx}`;
      }
      next[id] = {
        id,
        title: "Custom source",
        role: "source_search",
        roles: ["source_search"],
        path_patterns: ["FLOW_DB_ROOT/<path>"],
        fallback_path_patterns: [],
        owner: "",
        write_policy: "Agent read-only. Update source data through owner feature APIs.",
        docs_path: `docs/semantic/${id}.md`,
        related_question_ids: [],
        related_unit_keys: [],
        columns: [],
        search_terms: [],
        base_confidence: 0.42,
      };
      setSourceJson(JSON.stringify(next, null, 2));
    } catch (e) {
      setErr(e.message || String(e));
    }
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
    const current = Object.fromEntries((measurementCatalog?.terms || []).map((term) => [term.id, term]));
    const deletions = Object.keys(current).filter((key) => !Object.prototype.hasOwnProperty.call(next, key));
    Promise.all([
      ...deletions.map((key) => sf(`/api/agent/semantic/measurements/${encodeURIComponent(key)}`, { method: "DELETE" })),
      ...Object.entries(next).map(([key, value]) => putJson(
        `/api/agent/semantic/measurements/${encodeURIComponent(key)}`,
        { term: { ...(value || {}), id: key } }
      )),
    ]).then(() => {
      setMsg("measurement terms 저장 완료");
      return load();
    }).catch((e) => setErr(e.message || String(e)))
      .finally(() => setBusy(false));
  };

  const saveMeasurementNatural = () => {
    setBusy(true);
    setErr("");
    setMsg("");
    postJson("/api/agent/semantic/draft", { text: measurementNaturalText })
      .then((out) => {
        const draftPayload = out?.draft || {};
        const entries = draftPayload.measurement_terms || {};
        setDraft(draftPayload);
        if (!Object.keys(entries).length) {
          throw new Error("measurement term 초안을 만들 수 없습니다. term/source_type/item_id 중 일부를 포함해 주세요.");
        }
        return Promise.all(Object.entries(entries).map(([key, value]) => putJson(
          `/api/agent/semantic/measurements/${encodeURIComponent(key)}`,
          { term: { ...(value || {}), id: key } }
        )));
      })
      .then(() => {
        setMeasurementNaturalText("");
        setMsg("measurement 자연어 저장 완료");
        return load();
      })
      .catch((e) => setErr(e.message || String(e)))
      .finally(() => setBusy(false));
  };

  const editMeasurementEntry = (term) => {
    const id = term?.id || "";
    if (!id) return;
    let next = {};
    try {
      next = parseJsonObject(measurementJson, "measurement_terms");
    } catch {
      next = Object.fromEntries((measurementCatalog?.terms || []).map((row) => [row.id, row]));
    }
    next[id] = term;
    setMeasurementJson(JSON.stringify(next, null, 2));
    setMsg(`${id} measurement를 JSON 편집기에 올렸습니다.`);
  };

  const deleteMeasurementEntry = (term) => {
    const id = term?.id || "";
    if (!id) return;
    if (typeof window !== "undefined" && !window.confirm(`${term?.term || id} measurement를 삭제할까요?`)) return;
    setBusy(true);
    setErr("");
    setMsg("");
    sf(`/api/agent/semantic/measurements/${encodeURIComponent(id)}`, { method: "DELETE" })
      .then(() => {
        setMsg(`${term?.term || id} measurement 삭제 완료`);
        return load();
      })
      .catch((e) => setErr(e.message || String(e)))
      .finally(() => setBusy(false));
  };

  const addMeasurementTemplate = () => {
    try {
      const next = parseJsonObject(measurementJson, "measurement_terms");
      let idx = 1;
      let id = "measure_custom";
      while (Object.prototype.hasOwnProperty.call(next, id)) {
        idx += 1;
        id = `measure_custom_${idx}`;
      }
      next[id] = {
        id,
        term: "Custom measurement",
        aliases: ["Custom measurement"],
        source_type: "INLINE",
        product: "",
        step_id: "",
        item_id: "",
        value_column: "",
        default_agg: "avg",
        target: null,
        spec_low: null,
        spec_high: null,
        evidence: [],
      };
      setMeasurementJson(JSON.stringify(next, null, 2));
    } catch (e) {
      setErr(e.message || String(e));
    }
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
  const semanticCounts = {
    aliasCount: Object.keys(payload?.alias_group_entries?.disk || payload?.alias_groups?.disk || {}).length,
    intentCount: Object.keys(payload?.intent_hints?.disk || {}).length,
    sourceCount: sourceRows.length,
    measurementCount: measurementCatalog.terms.length,
    proposalCount: proposals.length,
    changeCount: changes.length,
  };
  const semanticSectionItems = SEMANTIC_SECTIONS.map((section) => {
    if (section.k === "lexicon") return { ...section, badge: semanticCounts.aliasCount + semanticCounts.intentCount };
    if (section.k === "sources") return { ...section, badge: semanticCounts.sourceCount };
    if (section.k === "measurements") return { ...section, badge: semanticCounts.measurementCount };
    return { ...section, badge: semanticCounts.proposalCount };
  });

  return (
    <div style={{ display: "grid", gap: 10 }}>
      {err ? <Banner tone="bad" onClose={() => setErr("")}>{err}</Banner> : null}
      {msg ? <Banner tone="ok" onClose={() => setMsg("")}>{msg}</Banner> : null}

      <TabStrip
        active={activeSection}
        onChange={setActiveSection}
        items={semanticSectionItems}
        right={<Pill tone="neutral">{semanticSectionSummary(activeSection, semanticCounts)}</Pill>}
      />

      {activeSection === "lexicon" ? (
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
      ) : null}

      {activeSection === "sources" ? (
      <Panel title="Source catalog" subtitle={`${sourceRows.length} sources`}>
        <div style={{ display: "grid", gap: 8, marginBottom: 10 }}>
          <Field label="source 자연어">
            <Textarea
              value={sourceNaturalText}
              onChange={(e) => setSourceNaturalText(e.target.value)}
              rows={3}
              placeholder="id=custom_inline; title=Custom Inline source; role=inline_db; path=FLOW_DB_ROOT/custom_inline.parquet; columns=product,step_id; search_terms=inline,trend"
            />
          </Field>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <Button variant="primary" onClick={saveSourceNatural} disabled={!sourceNaturalText.trim() || busy}>source 자연어 저장</Button>
          </div>
          <Field label="source_catalog">
            <Textarea value={sourceJson} onChange={(e) => setSourceJson(e.target.value)} rows={10} />
          </Field>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <Button variant="primary" onClick={saveSourceJson} disabled={busy}>source 저장</Button>
            <Button variant="ghost" onClick={addSourceTemplate} disabled={busy}>source 추가</Button>
          </div>
          <div style={{ fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.45 }}>
            path {sourceCatalog.path || "-"} · change log {sourceCatalog.change_log_path || "-"}
          </div>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 8 }}>
          {sourceRows.map((source) => {
            const id = source?.id || source?.source_id || "";
            const docsPath = source?.docs_path || `${sourceCatalog?.docs_base || "docs/semantic"}/${id}.md`;
            return (
              <div key={id || source?.title} style={{ display: "grid", gap: 6, padding: 9, border: "1px solid var(--border)", background: "var(--bg-primary)" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                  <strong style={{ fontSize: 13 }}>{source?.title || id}</strong>
                  <Pill tone="neutral">{source?.role || "source"}</Pill>
                  <div style={{ marginLeft: "auto", display: "flex", gap: 5, alignItems: "center", flexWrap: "wrap" }}>
                    {docsPath ? (
                      <a href={docsPath} target="_blank" rel="noreferrer" style={{ fontSize: 11, color: "var(--brand, var(--text-primary))" }}>
                        docs
                      </a>
                    ) : null}
                    <Button variant="ghost" onClick={() => editSourceEntry(source)} disabled={busy} style={{ fontSize: 11, padding: "2px 7px", height: 24 }}>수정</Button>
                    <Button variant="ghost" onClick={() => deleteSourceEntry(source)} disabled={busy} style={{ fontSize: 11, padding: "2px 7px", height: 24 }}>삭제</Button>
                  </div>
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
      ) : null}

      {activeSection === "measurements" ? (
      <Panel title="Measurement terms" subtitle={`${measurementCatalog.terms.length} semantic measurement aliases`}>
        <div style={{ display: "grid", gridTemplateColumns: "minmax(320px, 0.9fr) minmax(0, 1.1fr)", gap: 10, alignItems: "start" }}>
          <div style={{ display: "grid", gap: 8 }}>
            <Field label="measurement 자연어">
              <Textarea
                value={measurementNaturalText}
                onChange={(e) => setMeasurementNaturalText(e.target.value)}
                rows={3}
                placeholder="term=CA BCD; source_type=INLINE; product=PRODA; step_id=AA100001; item_id=CA_BCD; target=10; spec_low=8; spec_high=12; aliases=CA BCD,CABCD"
              />
            </Field>
            <Button variant="primary" onClick={saveMeasurementNatural} disabled={!measurementNaturalText.trim() || busy}>measurement 자연어 저장</Button>
            <Field label="measurement_terms">
              <Textarea value={measurementJson} onChange={(e) => setMeasurementJson(e.target.value)} rows={14} />
            </Field>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <Button variant="primary" onClick={saveMeasurementJson} disabled={busy}>measurement 저장</Button>
              <Button variant="ghost" onClick={addMeasurementTemplate} disabled={busy}>measurement 추가</Button>
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
                  <Button variant="ghost" onClick={() => editMeasurementEntry(term)} disabled={busy} style={{ fontSize: 11, padding: "2px 7px", height: 24 }}>수정</Button>
                  <Button variant="ghost" onClick={() => deleteMeasurementEntry(term)} disabled={busy} style={{ fontSize: 11, padding: "2px 7px", height: 24 }}>삭제</Button>
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
      ) : null}

      {activeSection === "review" ? (
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
      ) : null}
    </div>
  );
}
