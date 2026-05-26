// UnitAIDetail — Agent V2 우측 디테일.
// 한 unit AI의 자원 전체를 단일 페이지에 세로 스크롤로 표시.
// (a) 헤더/요약 (b) 데이터 & 컬럼 의미 (c) 시멘틱 바인딩 (d) prompt template
// (e) feature md (f) handler entry — 다중 탭 없음.
import { useEffect, useMemo, useRef, useState } from "react";
import { sf, postJson, putJson } from "../../lib/api";
import { Banner, Button, DataTable, EmptyState, Pill } from "../UXKit";
import Loading from "../Loading";
import AgentRuntimeGraphView from "./AgentRuntimeGraphView";

const DEFAULT_RUNTIME_GOALS = {
  filebrowser: "PRODA A1000 현재 step과 관련 FAB 데이터를 조회해줘",
  splittable: "PRODA A1000 #21 KNOB 영향을 SplitTable 기준으로 확인해줘",
  inform: "PRODA A1000 관련 Inform 로그를 요약해줘",
  tracker: "PRODA A1000 관련 Tracker 이슈를 찾아줘",
  meeting: "PRODA A1000 관련 회의 결정사항을 찾아줘",
  dashboard: "PRODA A1000 LOT 진행을 차트로 볼 수 있는지 확인해줘",
};

export default function UnitAIDetail({ unitKey, user, canManageWiki }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const [reloadTick, setReloadTick] = useState(0);
  const sourceRef = useRef(null);

  useEffect(() => {
    if (!unitKey) return;
    if (sourceRef.current) sourceRef.current.close();
    let cancelled = false;
    setLoading(true); setErr(""); setData(null);
    sf(`/api/agent/unit-ai/${encodeURIComponent(unitKey)}/inspect`)
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setErr(e?.message || "unit AI inspect 로딩 실패"))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, [unitKey, reloadTick]);

  useEffect(() => () => {
    if (sourceRef.current) sourceRef.current.close();
  }, []);

  if (loading) return <div style={{ padding: 40, display: "flex", justifyContent: "center" }}><Loading text="unit AI 자원 로딩..." size="md" /></div>;
  if (err) return <div style={{ padding: 24 }}><Banner tone="warn">{err}</Banner></div>;
  if (!data || !data.ok) return <div style={{ padding: 24 }}><EmptyState title="unit AI를 선택하세요" /></div>;

  const reload = () => setReloadTick((v) => v + 1);

  return (
    <div style={{ padding: 20, display: "flex", flexDirection: "column", gap: 18 }}>
      <Header data={data} />
      <UnitRuntimeSection data={data} canManage={canManageWiki} sourceRef={sourceRef} />
      <DataSourcesSection sources={data.data_sources || []} />
      <SemanticSection bindings={data.semantic_bindings || {}} />
      <PromptTemplateSection tpl={data.prompt_template} unitKey={data.key} canManage={canManageWiki} onSaved={reload} />
      <FeatureMdSection md={data.feature_md} unitKey={data.key} canManage={canManageWiki} onSaved={reload} />
      <HandlerEntrySection entry={data.handler_entry} />
    </div>
  );
}

// ── Header ──────────────────────────────────────────────
function Header({ data }) {
  return (
    <header style={{ borderBottom: "1px solid var(--border)", paddingBottom: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <h2 style={{ fontSize: 20, margin: 0 }}>🤖 {data.title}</h2>
        <code style={{ fontSize: 12, padding: "2px 6px", background: "var(--bg-secondary)", color: "var(--text-secondary)", borderRadius: 4 }}>{data.key}</code>
      </div>
    </header>
  );
}

// ── Section helpers ────────────────────────────────────
function Section({ title, hint, right, children }) {
  return (
    <section>
      <SectionHeader title={title} hint={hint} right={right} />
      <div style={{ marginTop: 8 }}>{children}</div>
    </section>
  );
}

function SectionHeader({ title, hint, right }) {
  return (
    <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
      <h3 style={{ fontSize: 14, fontWeight: 700, margin: 0, color: "var(--accent)" }}>{title}</h3>
      {hint && <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>{hint}</span>}
      <span style={{ flex: 1 }} />
      {right}
    </div>
  );
}

// ── Scoped runtime runner ─────────────────────────────
function UnitRuntimeSection({ data, canManage, sourceRef }) {
  const unitKey = data.key;
  const [blueprint, setBlueprint] = useState(null);
  const [goal, setGoal] = useState(DEFAULT_RUNTIME_GOALS[unitKey] || `${data.title || unitKey} 기준으로 처리 가능 여부를 점검해줘`);
  const [semantic, setSemantic] = useState(null);
  const [plan, setPlan] = useState([]);
  const [results, setResults] = useState([]);
  const [events, setEvents] = useState([]);
  const [final, setFinal] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [selectedNodeId, setSelectedNodeId] = useState("semantic_layer");
  const [proposals, setProposals] = useState([]);
  const [proposalErr, setProposalErr] = useState("");
  const [proposalBusy, setProposalBusy] = useState(false);
  const [proposalRunKey, setProposalRunKey] = useState("");
  const [applyBusy, setApplyBusy] = useState("");

  useEffect(() => {
    setGoal(DEFAULT_RUNTIME_GOALS[unitKey] || `${data.title || unitKey} 기준으로 처리 가능 여부를 점검해줘`);
    setSemantic(null); setPlan([]); setResults([]); setEvents([]); setFinal(null);
    setProposals([]); setErr(""); setProposalErr(""); setSelectedNodeId("semantic_layer");
    setStreaming(false); setBlueprint(null); setProposalRunKey("");
    sf(`/api/agent/unit-ai/${encodeURIComponent(unitKey)}/runtime/blueprint`)
      .then((d) => setBlueprint(d))
      .catch((e) => setErr(e?.message || "runtime blueprint 로딩 실패"));
  }, [unitKey, data.title]);

  const nodeStates = useMemo(
    () => buildNodeStates(blueprint, events, semantic, plan, results, final),
    [blueprint, events, semantic, plan, results, final],
  );
  const nodeDetail = useMemo(
    () => buildNodeDetail(blueprint, selectedNodeId, { semantic, plan, results, events, final, proposals }),
    [blueprint, selectedNodeId, semantic, plan, results, events, final, proposals],
  );
  const actionPlan = (plan || []).filter((row) => row.unit_ai === unitKey && row.action);
  const guardrail = final?.guardrail || latestRuntimeGuardrail(events) || {};
  const canRun = goal.trim().length > 0 && !busy && !streaming;
  const latestRunKey = useMemo(() => (events || []).find((event) => event.run_id)?.run_id || "", [events]);

  useEffect(() => {
    if (!latestRunKey || streaming || busy || proposalBusy || proposalRunKey === latestRunKey) return;
    if (!final && !results.length) return;
    setProposalRunKey(latestRunKey);
    loadProposals();
  }, [latestRunKey, streaming, busy, proposalBusy, proposalRunKey, final, results.length]);

  function resetRun() {
    setEvents([]); setFinal(null); setPlan([]); setResults([]); setSemantic(null); setProposals([]); setProposalErr("");
    setProposalRunKey("");
  }

  function ingestEvent(row) {
    setEvents((prev) => [...prev, row]);
    if (row.stage === "semantic_layer" && row.data?.semantic) setSemantic(row.data.semantic);
    if (row.stage === "task_planner") setPlan(row.data?.plan || []);
    if (row.data?.result) {
      setResults((prev) => mergeRuntimeResult(prev, row.data.result));
    }
    if (row.stage === "unit_agents") setResults(row.data?.results || []);
    if (row.event === "final") setFinal(row.data?.conclusion || null);
  }

  async function runOnce() {
    const trimmed = goal.trim();
    if (!trimmed) return;
    resetRun();
    setBusy(true); setErr("");
    try {
      const d = await postJson(`/api/agent/unit-ai/${encodeURIComponent(unitKey)}/runtime/run`, {
        goal: trimmed,
        max_terms: 32,
        use_llm: false,
      });
      const run = d?.run || {};
      setSemantic(run.semantic || null);
      setPlan(run.plan || []);
      setResults(run.results || []);
      setEvents(run.events || []);
      setFinal(run.conclusion || null);
    } catch (e) {
      setErr(e?.message || "runtime run 실패");
    } finally {
      setBusy(false);
    }
  }

  function startStream() {
    const trimmed = goal.trim();
    if (!trimmed) return;
    if (sourceRef.current) sourceRef.current.close();
    resetRun();
    setErr("");
    setStreaming(true);
    const params = new URLSearchParams({ goal: trimmed, max_terms: "32", use_llm: "false" });
    const token = sessionToken();
    if (token) params.set("t", token);
    const es = new EventSource(`/api/agent/unit-ai/${encodeURIComponent(unitKey)}/runtime/stream?${params.toString()}`);
    let finished = false;
    sourceRef.current = es;
    const pushEvent = (ev) => {
      try {
        const row = JSON.parse(ev.data || "{}");
        ingestEvent(row);
        if (row.event === "done") {
          finished = true;
          setStreaming(false);
          sourceRef.current = null;
          es.close();
        }
      } catch (e) {
        setErr(e?.message || "stream parse 실패");
      }
    };
    ["status", "final", "done"].forEach((name) => es.addEventListener(name, pushEvent));
    es.onerror = () => {
      if (finished) return;
      setStreaming(false);
      setErr("SSE 연결이 종료되었습니다. 인증 또는 서버 로그를 확인하세요.");
      es.close();
    };
  }

  function stopStream() {
    if (sourceRef.current) sourceRef.current.close();
    sourceRef.current = null;
    setStreaming(false);
  }

  async function loadProposals(runOverride = null) {
    setProposalBusy(true); setProposalErr("");
    try {
      const runPayload = runOverride || { goal, semantic, plan, results, conclusion: final, events };
      const d = await postJson(`/api/agent/unit-ai/${encodeURIComponent(unitKey)}/runtime/improvement-proposals`, {
        run: runPayload,
      });
      setProposals(d?.proposals || []);
    } catch (e) {
      setProposalErr(e?.message || "개선 제안 생성 실패");
    } finally {
      setProposalBusy(false);
    }
  }

  async function applyProposal(p) {
    if (!canManage || !p?.endpoint || !p?.payload) return;
    setApplyBusy(p.id);
    setProposalErr("");
    try {
      if ((p.method || "POST").toUpperCase() === "PUT") {
        await putJson(p.endpoint, p.payload);
      } else {
        await postJson(p.endpoint, p.payload);
      }
      setProposals((prev) => prev.map((row) => row.id === p.id ? { ...row, applied: true } : row));
    } catch (e) {
      setProposalErr(e?.message || "제안 적용 실패");
    } finally {
      setApplyBusy("");
    }
  }

  return (
    <Section
      title="질문 실행 / 에이전트 그래프"
      hint={`${unitKey} scope 고정`}
      right={<StatusPill status={streaming ? "running" : final ? "completed" : "pending"} />}
    >
      {err && <Banner tone="warn" style={{ marginBottom: 8 }}>{err}</Banner>}
      <div className="unit-runtime-control">
        <textarea
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          rows={3}
          style={{ width: "100%", resize: "vertical", padding: 10, border: "1px solid var(--border)", borderRadius: 4, background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 13 }}
        />
        <div className="unit-runtime-actions">
          <Button onClick={runOnce} disabled={!canRun}>{busy ? "실행 중..." : "1회 실행"}</Button>
          {!streaming && <Button variant="primary" onClick={startStream} disabled={busy || !goal.trim()}>SSE 실행</Button>}
          {streaming && <Button variant="danger" onClick={stopStream}>중지</Button>}
          <Button onClick={() => loadProposals()} disabled={proposalBusy || streaming || (!final && !results.length)}>
            {proposalBusy ? "생성 중..." : "개선 제안"}
          </Button>
        </div>
      </div>

      <div className="unit-runtime-grid">
        <div className="unit-runtime-graph-wrap">
          <AgentRuntimeGraphView
            blueprint={blueprint}
            nodeStates={nodeStates}
            selectedNodeId={selectedNodeId}
            onSelectNode={(node) => setSelectedNodeId(node.id)}
          />
        </div>
        <div className="unit-runtime-detail">
          <div className="unit-runtime-detail-head">
            <span>{nodeDetail.title}</span>
            <StatusPill status={nodeStates[selectedNodeId] || "pending"} />
          </div>
          <pre style={{ ...preStyle, maxHeight: 260 }}>{shortJson(nodeDetail.payload)}</pre>
        </div>
      </div>

      <RuntimeExplanationPanel
        unitKey={unitKey}
        semantic={semantic}
        plan={actionPlan}
        results={results}
        final={final}
        proposals={proposals}
      />

      <div className="unit-runtime-summary">
        <div>
          <SectionMiniTitle title="결론" meta={final?.intent || guardrail.status || "대기"} />
          {final ? (
            <div className="unit-runtime-answer">{final.answer}</div>
          ) : (
            <div className="agent-runtime-empty">실행 후 결론이 표시됩니다.</div>
          )}
        </div>
        <div>
          <SectionMiniTitle title="선택 AI 실행" meta={`${actionPlan.length} actions · ${results.length} results`} />
          <DataTable
            rows={results}
            columns={[
              { key: "agent_id", label: "node", width: 150 },
              { key: "status", label: "status", width: 90, render: (r) => <StatusPill status={runtimeRowStatus(r)} /> },
              { key: "summary", label: "summary" },
            ]}
            empty="아직 결과가 없습니다."
            maxHeight={180}
          />
        </div>
      </div>

      <div className="unit-runtime-summary">
        <div>
          <SectionMiniTitle title="이벤트" meta={`${events.length} events`} />
          <RuntimeEventRail events={events} />
        </div>
        <div>
          <SectionMiniTitle title="개선 제안" meta={canManage ? "적용 가능" : "읽기 모드"} />
          {proposalErr && <Banner tone="warn" style={{ marginBottom: 8 }}>{proposalErr}</Banner>}
          <ProposalList proposals={proposals} canManage={canManage} applyBusy={applyBusy} onApply={applyProposal} />
        </div>
      </div>
    </Section>
  );
}

function SectionMiniTitle({ title, meta }) {
  return (
    <div className="unit-runtime-mini-title">
      <strong>{title}</strong>
      {meta && <span>{meta}</span>}
    </div>
  );
}

function RuntimeEventRail({ events }) {
  const rows = (events || []).filter((event) => event.stage && !["start", "done"].includes(event.stage)).slice(-10);
  if (!rows.length) return <div className="agent-runtime-empty">아직 이벤트가 없습니다.</div>;
  return (
    <ol className="unit-runtime-events">
      {rows.map((event, idx) => (
        <li key={event.event_id || idx}>
          <span className="agent-runtime-step-dot" data-status={event.status || "running"} />
          <div>
            <div className="agent-runtime-step-head">
              <span>{event.stage}</span>
              <StatusPill status={event.data?.anomaly || event.status} />
            </div>
            <div className="agent-runtime-step-msg">{event.message}</div>
          </div>
        </li>
      ))}
    </ol>
  );
}

function ProposalList({ proposals, canManage, applyBusy, onApply }) {
  if (!proposals.length) return <div className="agent-runtime-empty">생성된 제안이 없습니다.</div>;
  return (
    <div className="unit-runtime-proposals">
      {proposals.map((p) => (
        <div key={p.id} className="unit-runtime-proposal">
          <div className="unit-runtime-proposal-head">
            <strong>{p.title}</strong>
            <Pill tone={p.applied ? "ok" : "warn"}>{p.target}</Pill>
          </div>
          <div className="agent-runtime-step-msg">{p.rationale}</div>
          <div className="unit-runtime-proposal-meta">
            <code>{p.method}</code>
            <code>{p.endpoint}</code>
          </div>
          {canManage && (
            <Button onClick={() => onApply(p)} disabled={!!applyBusy || p.applied} style={{ marginTop: 6 }}>
              {p.applied ? "적용됨" : applyBusy === p.id ? "적용 중..." : "적용"}
            </Button>
          )}
        </div>
      ))}
    </div>
  );
}

function RuntimeExplanationPanel({ unitKey, semantic, plan, results, final, proposals }) {
  const valueRows = buildValueRows(semantic);
  const decisionRows = buildDecisionRows(unitKey, plan, results, final);
  const fixRows = buildFixRows(semantic, results, final, proposals);
  const hasRun = !!semantic || !!(plan || []).length || !!(results || []).length || !!final;

  return (
    <div className="unit-runtime-explain">
      <RuntimeExplainBlock
        title="해석된 값"
        meta={semantic ? `coverage ${Math.round(Number(semantic.coverage || 0) * 100)}% · ${semantic.intent || "intent 없음"}` : "질문 실행 후 표시"}
        rows={valueRows}
        empty={hasRun ? "추출된 slot/candidate가 없습니다." : "질문을 실행하면 단어와 slot이 표시됩니다."}
      />
      <RuntimeExplainBlock
        title="선택 AI 판단"
        meta={`${unitKey} 고정 라우팅`}
        rows={decisionRows}
        empty={hasRun ? "실행 가능한 action/result가 아직 없습니다." : "실행 후 action, missing slot, handler 결과가 표시됩니다."}
      />
      <RuntimeExplainBlock
        title="개선 방향"
        meta={proposals.length ? `${proposals.length} proposals` : "자동 반영 없음"}
        rows={fixRows}
        empty={hasRun ? "현재 추가 개선 제안이 없습니다." : "실행 결과에 따라 alias, prompt, feature md, workflow 후보가 표시됩니다."}
      />
    </div>
  );
}

function RuntimeExplainBlock({ title, meta, rows, empty }) {
  return (
    <div className="unit-runtime-explain-block">
      <div className="unit-runtime-mini-title">
        <strong>{title}</strong>
        {meta && <span>{meta}</span>}
      </div>
      {(rows || []).length ? (
        <div className="unit-runtime-kv-list">
          {rows.map((row, idx) => (
            <div key={`${row.k}-${idx}`} className="unit-runtime-kv-row">
              <span>{row.k}</span>
              <strong>{row.v}</strong>
              {row.note && <em>{row.note}</em>}
            </div>
          ))}
        </div>
      ) : (
        <div className="agent-runtime-empty">{empty}</div>
      )}
    </div>
  );
}

// ── Data sources & columns ─────────────────────────────
function DataSourcesSection({ sources }) {
  return (
    <Section
      title={`데이터 & 컬럼 의미 (${sources.length})`}
      hint="이 AI가 읽는 단일파일/DB와 각 컬럼이 무엇을 뜻하는지"
    >
      {sources.length === 0 && <EmptyState title="등록된 데이터 소스가 없습니다" hint="M2 PR에서 채워집니다" />}
      {sources.map((ds, i) => (
        <div key={i} style={{
          marginTop: 8, border: "1px solid var(--border)", borderRadius: 6,
          background: "var(--bg-secondary)", overflow: "hidden",
        }}>
          <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)", display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <span style={{ fontSize: 11, padding: "2px 6px", borderRadius: 3, background: "var(--accent-glow)", color: "var(--accent)", fontWeight: 700, letterSpacing: 0.3 }}>{ds.kind}</span>
            <code style={{ fontSize: 12, color: "var(--text-primary)" }}>{ds.path}</code>
          </div>
          {ds.description && (
            <div style={{ padding: "6px 12px", fontSize: 13, color: "var(--text-primary)", borderBottom: ds.columns.length ? "1px solid var(--border)" : "none" }}>
              {ds.description}
            </div>
          )}
          {ds.columns.length > 0 && <ColumnsTable columns={ds.columns} />}
        </div>
      ))}
    </Section>
  );
}

function ColumnsTable({ columns }) {
  return (
    <div style={{ overflow: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
        <thead>
          <tr style={{ background: "var(--bg-hover)", color: "var(--text-secondary)" }}>
            <th style={th()}>컬럼</th>
            <th style={th()}>의미</th>
            <th style={th()}>예시</th>
            <th style={th()}>Wiki</th>
          </tr>
        </thead>
        <tbody>
          {columns.map((c) => (
            <tr key={c.name} style={{ borderTop: "1px solid var(--border)" }}>
              <td style={td("160px", true)}><code>{c.name}</code></td>
              <td style={td()}>{c.meaning || <span style={{ color: "var(--text-secondary)" }}>(미작성)</span>}</td>
              <td style={td("140px")}>{(c.sample_values || []).join(", ") || "—"}</td>
              <td style={td("160px")}>{c.wiki_doc_id ? <code style={{ fontSize: 11 }}>{c.wiki_doc_id}</code> : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const th = () => ({ textAlign: "left", padding: "6px 10px", fontWeight: 700, fontSize: 11 });
const td = (width = "auto", mono = false) => ({ padding: "6px 10px", verticalAlign: "top", width, fontFamily: mono ? "monospace" : "inherit" });

// ── Semantic bindings ──────────────────────────────────
function SemanticSection({ bindings }) {
  const items = [
    { k: "relation_ids", l: "Relation IDs (schema_relations.json)" },
    { k: "column_catalog_keys", l: "Column catalog keys" },
    { k: "graph_node_ids", l: "Knowledge graph nodes" },
    { k: "wiki_doc_ids", l: "Wiki docs (schema_doc kind)" },
  ];
  return (
    <Section title="시멘틱 바인딩" hint="용어 해석에 쓰는 메타 (편집은 M4)">
      {items.map(({ k, l }) => {
        const arr = bindings[k] || [];
        return (
          <div key={k} style={{ display: "flex", gap: 8, padding: "4px 0", fontSize: 12 }}>
            <span style={{ width: 230, color: "var(--text-secondary)" }}>{l}</span>
            <span style={{ flex: 1 }}>
              {arr.length === 0 ? <span style={{ color: "var(--text-secondary)" }}>—</span> :
                arr.map((v, i) => <code key={i} style={{ marginRight: 6, fontSize: 11, padding: "1px 5px", background: "var(--bg-secondary)", borderRadius: 3 }}>{v}</code>)}
            </span>
          </div>
        );
      })}
    </Section>
  );
}

// ── Prompt template ────────────────────────────────────
function PromptTemplateSection({ tpl, unitKey, canManage, onSaved }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  if (!tpl || !tpl.path) {
    return (
      <Section title="Prompt template" hint="이 AI 전용 prompt 파일 (없으면 handler 내부 string 사용)">
        <Banner tone="info">전용 prompt template 파일이 등록돼 있지 않습니다. handler 내부 prompt string 사용.</Banner>
      </Section>
    );
  }

  async function save() {
    setBusy(true); setErr("");
    try {
      await putJson(`/api/agent/unit-ai/${encodeURIComponent(unitKey)}/prompt-template`, { text: draft });
      setEditing(false);
      if (onSaved) onSaved();
    } catch (e) { setErr(e?.message || "저장 실패"); }
    finally { setBusy(false); }
  }

  const right = (
    <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
      {!tpl.exists && <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>(파일 없음)</span>}
      {canManage && !editing && tpl.exists && <Button onClick={() => { setDraft(tpl.text || ""); setEditing(true); setErr(""); }}>편집</Button>}
      {editing && <><Button onClick={save} disabled={busy}>{busy ? "저장 중..." : "저장"}</Button><Button onClick={() => setEditing(false)}>취소</Button></>}
    </span>
  );

  const parsed = tpl.parsed;
  return (
    <Section title="Prompt template" hint={tpl.path} right={right}>
      {tpl.error && <Banner tone="warn">{tpl.error}</Banner>}
      {err && <Banner tone="warn">{err}</Banner>}
      {editing ? (
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          rows={16}
          style={{ width: "100%", padding: 10, fontSize: 12, fontFamily: "monospace", border: "1px solid var(--border)", borderRadius: 4, background: "var(--bg-primary)", color: "var(--text-primary)", whiteSpace: "pre" }}
        />
      ) : parsed && typeof parsed === "object" && !Array.isArray(parsed) ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {Object.keys(parsed).map((k) => (
            <details key={k} style={{ border: "1px solid var(--border)", borderRadius: 4, background: "var(--bg-secondary)" }}>
              <summary style={{ padding: "6px 10px", cursor: "pointer", fontWeight: 600, fontSize: 13 }}>{k}</summary>
              <pre style={preStyle}>{typeof parsed[k] === "string" ? parsed[k] : JSON.stringify(parsed[k], null, 2)}</pre>
            </details>
          ))}
        </div>
      ) : (
        <pre style={preStyle}>{tpl.text || "(empty)"}</pre>
      )}
    </Section>
  );
}

const preStyle = {
  margin: 0, padding: "8px 12px", whiteSpace: "pre-wrap", wordBreak: "break-word",
  fontSize: 12, fontFamily: "monospace", background: "var(--bg-primary)",
  borderTop: "1px solid var(--border)", maxHeight: 320, overflow: "auto",
};

// ── Feature md ─────────────────────────────────────────
function FeatureMdSection({ md, unitKey, canManage, onSaved }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  if (!md || !md.path) return null;

  async function save() {
    setBusy(true); setErr("");
    try {
      await putJson(`/api/agent/unit-ai/${encodeURIComponent(unitKey)}/feature-md`, { text: draft });
      setEditing(false);
      if (onSaved) onSaved();
    } catch (e) { setErr(e?.message || "저장 실패"); }
    finally { setBusy(false); }
  }

  const right = (
    <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
      {!md.exists && <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>(파일 없음)</span>}
      {canManage && !editing && <Button onClick={() => { setDraft(md.text || ""); setEditing(true); setErr(""); }}>편집</Button>}
      {editing && <><Button onClick={save} disabled={busy}>{busy ? "저장 중..." : "저장"}</Button><Button onClick={() => setEditing(false)}>취소</Button></>}
    </span>
  );

  return (
    <Section title="Feature 규칙 md" hint={md.path} right={right}>
      {md.error && <Banner tone="warn">{md.error}</Banner>}
      {err && <Banner tone="warn">{err}</Banner>}
      {editing ? (
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          rows={16}
          style={{ width: "100%", padding: 10, fontSize: 13, fontFamily: "monospace", border: "1px solid var(--border)", borderRadius: 4, background: "var(--bg-primary)", color: "var(--text-primary)", whiteSpace: "pre-wrap" }}
        />
      ) : (
        <pre style={{ ...preStyle, maxHeight: 360 }}>{md.text || "(empty)"}</pre>
      )}
    </Section>
  );
}

// ── Handler entry ──────────────────────────────────────
function HandlerEntrySection({ entry }) {
  if (!entry) return null;
  const has = entry.module || entry.function;
  return (
    <Section title="Handler entry" hint="이 AI의 실제 처리 함수">
      {!has && <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>(미연결 — M2 다음 PR에서 위임)</span>}
      {has && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12 }}>
          <div><span style={{ color: "var(--text-secondary)" }}>file: </span><code>{entry.file_path || entry.module}</code>{entry.lineno > 0 && <span style={{ color: "var(--text-secondary)" }}>:{entry.lineno}</span>}</div>
          <div><span style={{ color: "var(--text-secondary)" }}>function: </span><code>{entry.function}</code></div>
          {entry.description && <div style={{ color: "var(--text-secondary)" }}>{entry.description}</div>}
        </div>
      )}
    </Section>
  );
}

function sessionToken() {
  try {
    const raw = localStorage.getItem("hol_user");
    if (!raw) return "";
    return JSON.parse(raw)?.token || "";
  } catch (_) {
    return "";
  }
}

function shortJson(value) {
  try {
    return JSON.stringify(value || {}, null, 2);
  } catch (_) {
    return String(value || "");
  }
}

function blueprintNodes(blueprint) {
  const nodes = blueprint?.graph?.nodes || blueprint?.nodes || [];
  return (nodes || [])
    .map((node) => (typeof node === "string" ? { id: node, label: node } : node))
    .filter((node) => node && node.id);
}

function buildNodeStates(blueprint, events, semantic, plan, results, final) {
  const states = {};
  blueprintNodes(blueprint).forEach((node) => { states[node.id] = "pending"; });
  (events || []).forEach((event) => {
    const nodeId = runtimeEventNodeId(event);
    if (!nodeId || !(nodeId in states)) return;
    const anomaly = event?.data?.anomaly;
    states[nodeId] = anomaly || event.status || states[nodeId];
  });
  if (semantic) {
    states.semantic_layer = Number(semantic.coverage || 0) > 0 && Number(semantic.coverage || 0) < 0.35 ? "low_coverage" : "completed";
  }
  if ((plan || []).length) states.task_planner = "completed";
  (results || []).forEach((result) => {
    if (!result?.agent_id || !(result.agent_id in states)) return;
    states[result.agent_id] = runtimeRowStatus(result);
  });
  if (final?.guardrail) states.critic = guardrailNodeState(final.guardrail);
  if (final) states.conclusion = "completed";
  return states;
}

function buildNodeDetail(blueprint, selectedNodeId, runtime) {
  const node = blueprintNodes(blueprint).find((row) => row.id === selectedNodeId) || { id: selectedNodeId || "semantic_layer", label: selectedNodeId || "semantic" };
  const events = (runtime.events || []).filter((event) => runtimeEventNodeId(event) === node.id || event.stage === node.stage);
  const plan = (runtime.plan || []).filter((row) => row.agent_id === node.id || row.unit_ai === node.unit_ai);
  const results = (runtime.results || []).filter((row) => row.agent_id === node.id);
  const proposals = (runtime.proposals || []).filter((row) => {
    if (node.id === "critic") return true;
    if (node.kind === "unit_action") return ["feature_md", "prompt_template", "workflow_template"].includes(row.target);
    if (node.id === "semantic_layer") return ["semantic_alias", "semantic_intent"].includes(row.target);
    return false;
  }).map((row) => ({
    id: row.id,
    target: row.target,
    title: row.title,
    endpoint: row.endpoint,
    method: row.method,
    issue_tags: row.issue_tags,
    payload_keys: Object.keys(row.payload || {}),
  }));
  const payload = {
    node,
    semantic: node.id === "semantic_layer" ? {
      coverage: runtime.semantic?.coverage,
      intent: runtime.semantic?.intent,
      slots: runtime.semantic?.slots,
      tokens: runtime.semantic?.tokens,
      warnings: runtime.semantic?.warnings,
    } : undefined,
    plan,
    results,
    final: node.id === "conclusion" ? runtime.final : undefined,
    guardrail: node.id === "critic" ? (runtime.final?.guardrail || latestRuntimeGuardrail(runtime.events)) : undefined,
    proposals,
    events,
  };
  return { title: node.label || node.id, payload };
}

function runtimeEventNodeId(event) {
  return event?.data?.node_id || event?.agent_id || event?.stage || "";
}

function mergeRuntimeResult(prev, result) {
  if (!result?.agent_id) return prev;
  const next = [...(prev || [])];
  const idx = next.findIndex((row) => row.agent_id === result.agent_id);
  if (idx >= 0) next[idx] = result;
  else next.push(result);
  return next;
}

function latestRuntimeGuardrail(events) {
  for (let i = (events || []).length - 1; i >= 0; i -= 1) {
    const guardrail = events[i]?.data?.guardrail;
    if (guardrail && typeof guardrail === "object") return guardrail;
  }
  return null;
}

function runtimeRowStatus(row) {
  const guardrail = row?.guardrail || {};
  const guardrailStatus = guardrail.status || "";
  if (["missing_slots", "approval_required", "blocked", "no_handler", "error"].includes(guardrailStatus)) return guardrailStatus;
  if (row?.status === "failed") return "failed";
  if (row?.status === "skipped" && !row?.handled) return guardrailStatus || "skipped";
  return row?.status || "pending";
}

function guardrailNodeState(guardrail) {
  const status = guardrail?.status || "";
  if (["missing_slots", "approval_required", "blocked", "no_handler"].includes(status)) return status;
  if (status === "error") return "failed";
  return "completed";
}

function buildValueRows(semantic) {
  if (!semantic) return [];
  const rows = [];
  const slots = semantic.slots || {};
  Object.entries(slots).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "" || (Array.isArray(value) && value.length === 0)) return;
    rows.push({ k: `slot.${key}`, v: formatRuntimeValue(value), note: "질문에서 추출" });
  });
  const normalized = semantic.normalized_terms || {};
  Object.entries(normalized).slice(0, 8).forEach(([token, value]) => {
    if (!value) return;
    rows.push({ k: `term.${token}`, v: String(value), note: "semantic alias" });
  });
  (semantic.candidates || []).slice(0, 5).forEach((cand) => {
    const column = cand.column || cand.canonical_alias || cand.normalized || "";
    if (!column) return;
    rows.push({
      k: `candidate.${cand.token || column}`,
      v: column,
      note: [cand.source, cand.score != null ? `score ${Number(cand.score || 0).toFixed(2)}` : ""].filter(Boolean).join(" · "),
    });
  });
  if (semantic.warnings?.length) {
    rows.push({ k: "warnings", v: semantic.warnings.join(" · "), note: "해석 단계 경고" });
  }
  return rows.slice(0, 16);
}

function buildDecisionRows(unitKey, plan, results, final) {
  const rows = [];
  (plan || []).forEach((action) => {
    rows.push({
      k: `${action.unit_ai || unitKey}.${action.action || "action"}`,
      v: action.policy || "read_only",
      note: action.missing_slots?.length ? `missing: ${action.missing_slots.join(", ")}` : "선택 AI scope 안에서 계획",
    });
  });
  (results || []).forEach((result) => {
    const guardrail = result.guardrail || {};
    rows.push({
      k: result.agent_id || "result",
      v: `${runtimeRowStatus(result)} · handled=${result.handled ? "yes" : "no"}`,
      note: result.summary || guardrail.status || "",
    });
    if (result.metrics && Object.keys(result.metrics).length) {
      rows.push({ k: `${result.agent_id || "result"}.metrics`, v: formatRuntimeValue(result.metrics), note: "handler 반환값" });
    }
    if (result.warnings?.length) {
      rows.push({ k: `${result.agent_id || "result"}.warnings`, v: result.warnings.join(" · "), note: "실행 경고" });
    }
  });
  if (final?.guardrail) {
    rows.push({
      k: "critic.guardrail",
      v: final.guardrail.status || "allowed",
      note: `read-only ${final.guardrail.read_only_actions || 0}, approval ${final.guardrail.approval_required || 0}, blocked ${final.guardrail.blocked || 0}`,
    });
  }
  return rows.slice(0, 16);
}

function buildFixRows(semantic, results, final, proposals) {
  if ((proposals || []).length) {
    return proposals.slice(0, 8).map((proposal) => ({
      k: proposal.target,
      v: proposal.title || proposal.endpoint || "proposal",
      note: proposal.rationale || proposal.endpoint || "",
    }));
  }
  const rows = [];
  const coverage = Number(semantic?.coverage || 0);
  if (semantic && coverage < 0.35) {
    rows.push({ k: "semantic alias/intent", v: "용어 보강 필요", note: "coverage가 낮아 질문 단어가 컬럼/intent에 충분히 묶이지 않았습니다." });
  }
  (results || []).forEach((result) => {
    const status = runtimeRowStatus(result);
    if (status === "missing_slots") {
      rows.push({ k: result.agent_id || "missing_slots", v: "slot 보완", note: (result.guardrail?.missing_slots || []).join(", ") || "product/lot/step 값을 더 명확히 입력" });
    } else if (status === "no_handler") {
      rows.push({ k: result.agent_id || "no_handler", v: "handler/prompt 연결 보강", note: "선택 AI dispatcher가 처리 가능한 handler를 찾지 못했습니다." });
    } else if (status === "approval_required") {
      rows.push({ k: result.agent_id || "approval_required", v: "승인 workflow 필요", note: "저장성 작업은 workflow/template으로 승인 절차를 고정합니다." });
    } else if (status === "blocked") {
      rows.push({ k: result.agent_id || "blocked", v: "정책 우회 불가", note: "raw DB/file 수정 대신 기능 API 승인 경로가 필요합니다." });
    } else if (status === "failed") {
      rows.push({ k: result.agent_id || "failed", v: "실행 오류 점검", note: (result.warnings || []).join(" · ") || "handler 예외를 확인해야 합니다." });
    }
  });
  (final?.warnings || []).slice(0, 4).forEach((warning) => {
    rows.push({ k: "conclusion.warning", v: String(warning), note: "최종 결론 경고" });
  });
  return rows.slice(0, 10);
}

function formatRuntimeValue(value) {
  if (Array.isArray(value)) return value.map((v) => String(v)).join(", ");
  if (value && typeof value === "object") {
    try {
      return JSON.stringify(value);
    } catch (_) {
      return String(value);
    }
  }
  return String(value ?? "");
}

function StatusPill({ status }) {
  const text = status || "pending";
  const tone = text === "completed" || text === "allowed" || text === "handled" ? "ok"
    : text === "failed" || text === "blocked" ? "bad"
    : text === "running" ? "info"
    : text === "pending" ? "neutral"
    : "warn";
  return <Pill tone={tone}>{text}</Pill>;
}
