import { useEffect, useMemo, useRef, useState } from "react";
import { postJson, sf } from "../../lib/api";
import { Banner, Button, DataTable, Pill, Textarea } from "../UXKit";

const DEFAULT_GOAL = "PRODA A1000 #21 현재 step과 KNOB 영향을 확인하고 실행 흐름을 추적해줘";

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

export default function AgentRuntime() {
  const [goal, setGoal] = useState(DEFAULT_GOAL);
  const [blueprint, setBlueprint] = useState(null);
  const [semantic, setSemantic] = useState(null);
  const [events, setEvents] = useState([]);
  const [final, setFinal] = useState(null);
  const [plan, setPlan] = useState([]);
  const [results, setResults] = useState([]);
  const [busy, setBusy] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [err, setErr] = useState("");
  const [useLlm, setUseLlm] = useState(false);
  const sourceRef = useRef(null);

  const loadBlueprint = () => {
    setErr("");
    sf("/api/agent/runtime/blueprint")
      .then(setBlueprint)
      .catch((e) => setErr(e?.message || "runtime blueprint 로딩 실패"));
  };

  useEffect(() => {
    loadBlueprint();
    return () => {
      if (sourceRef.current) sourceRef.current.close();
    };
  }, []);

  const resolve = () => {
    const trimmed = goal.trim();
    if (!trimmed) return;
    setBusy(true);
    setErr("");
    postJson("/api/agent/runtime/semantic/resolve", { goal: trimmed, max_terms: 32 })
      .then((d) => setSemantic(d?.semantic || null))
      .catch((e) => setErr(e?.message || "semantic resolve 실패"))
      .finally(() => setBusy(false));
  };

  const runOnce = () => {
    const trimmed = goal.trim();
    if (!trimmed) return;
    setBusy(true);
    setErr("");
    setEvents([]);
    setFinal(null);
    setPlan([]);
    setResults([]);
    postJson("/api/agent/runtime/run", { goal: trimmed, max_terms: 32, use_llm: useLlm })
      .then((d) => {
        const run = d?.run || {};
        setSemantic(run.semantic || null);
        setPlan(run.plan || []);
        setResults(run.results || []);
        setEvents(run.events || []);
        setFinal(run.conclusion || null);
      })
      .catch((e) => setErr(e?.message || "runtime run 실패"))
      .finally(() => setBusy(false));
  };

  const startStream = () => {
    const trimmed = goal.trim();
    if (!trimmed) return;
    if (sourceRef.current) sourceRef.current.close();
    setErr("");
    setEvents([]);
    setFinal(null);
    setPlan([]);
    setResults([]);
    setStreaming(true);
    const params = new URLSearchParams({
      goal: trimmed,
      max_terms: "32",
      use_llm: useLlm ? "true" : "false",
    });
    const token = sessionToken();
    if (token) params.set("t", token);
    const es = new EventSource(`/api/agent/runtime/stream?${params.toString()}`);
    let finished = false;
    sourceRef.current = es;
    const pushEvent = (ev) => {
      try {
        const row = JSON.parse(ev.data || "{}");
        setEvents((prev) => [...prev, row]);
        if (row.stage === "semantic_layer" && row.data?.semantic) setSemantic(row.data.semantic);
        if (row.stage === "task_planner") setPlan(row.data?.plan || []);
        if (row.stage === "unit_agents") setResults(row.data?.results || []);
        if (row.event === "final") setFinal(row.data?.conclusion || null);
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
  };

  const stopStream = () => {
    if (sourceRef.current) sourceRef.current.close();
    setStreaming(false);
  };

  const candidates = useMemo(() => (semantic?.candidates || []).slice(0, 24), [semantic]);
  const unitAgents = blueprint?.unit_agents || [];
  const graph = blueprint?.graph || {};
  const smith = blueprint?.langsmith || {};
  const llm = blueprint?.llm || {};
  const actions = blueprint?.actions || [];
  const actionPlan = (plan || []).filter((row) => row.unit_ai && row.action && !(row.unit_ai === "agent_runtime" && ["resolve_semantic", "plan", "review_guardrail", "conclude"].includes(row.action)));
  const guardrail = final?.guardrail || latestGuardrail(events) || {};

  return (
    <div className="agent-runtime-shell">
      {err && <Banner tone="bad" style={{ borderRadius: 0 }}>{err}</Banner>}
      <section className="agent-runtime-section agent-runtime-control">
        <div>
          <div className="agent-runtime-eyebrow">FastAPI SSE · LangGraph astream · LangSmith tracing</div>
          <h2>단위 에이전트 오케스트레이션</h2>
        </div>
        <div className="agent-runtime-actions">
          <Button onClick={loadBlueprint} disabled={busy || streaming}>설계 새로고침</Button>
          <Button onClick={resolve} disabled={busy || streaming}>시멘틱 확인</Button>
          <Button onClick={runOnce} disabled={busy || streaming}>1회 실행</Button>
          {!streaming && <Button variant="primary" onClick={startStream} disabled={busy}>SSE 실행</Button>}
          {streaming && <Button variant="danger" onClick={stopStream}>중지</Button>}
        </div>
        <Textarea
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          rows={4}
          style={{ width: "100%", resize: "vertical" }}
        />
        <label className="agent-runtime-check">
          <input type="checkbox" checked={useLlm} onChange={(e) => setUseLlm(e.target.checked)} />
          <span>마지막 결론만 LLM 문장 정리 사용</span>
        </label>
      </section>

      <section className="agent-runtime-section">
        <div className="agent-runtime-status">
          <Status label="LangGraph" ok={graph.available} warn={graph.fallback} text={graph.available ? "available" : "fallback"} />
          <Status label="LangSmith" ok={smith.enabled} warn={smith.available && !smith.enabled} text={smith.enabled ? smith.project : (smith.available ? "installed" : "not installed")} />
          <Status label="LLM" ok={llm.available} warn={!llm.available} text={llm.available ? "available" : "optional"} />
          <Status label="SSE" ok text="/api/agent/runtime/stream" />
        </div>
      </section>

      <section className="agent-runtime-section">
        <SectionTitle title="시멘틱 레이어" meta={semantic ? `coverage ${Math.round((semantic.coverage || 0) * 100)}% · intent ${semantic.intent}` : "단어를 컬럼/slot/intent로 정규화"} />
        {semantic?.warnings?.length > 0 && <Banner tone="warn" style={{ marginBottom: 8 }}>{semantic.warnings.join(" · ")}</Banner>}
        <div className="agent-runtime-split">
          <div>
            <DataTable
              rows={(semantic?.tokens || []).map((token) => ({ token, normalized: semantic?.normalized_terms?.[token] || "-" }))}
              columns={[
                { key: "token", label: "token", width: 160 },
                { key: "normalized", label: "normalized" },
              ]}
              empty="시멘틱 확인을 먼저 실행하세요."
              maxHeight={240}
            />
          </div>
          <div>
            <DataTable
              rows={candidates}
              columns={[
                { key: "token", label: "token", width: 120 },
                { key: "column", label: "column", width: 150 },
                { key: "relation_id", label: "relation", width: 160 },
                { key: "source", label: "source", width: 160 },
                { key: "score", label: "score", width: 70, render: (r) => Number(r.score || 0).toFixed(2) },
              ]}
              empty="후보 컬럼 없음"
              maxHeight={240}
            />
          </div>
        </div>
        {semantic && <pre className="agent-runtime-json">{shortJson({ slots: semantic.slots, polars_profile: semantic.polars_profile })}</pre>}
      </section>

      <section className="agent-runtime-section agent-runtime-grid">
        <div>
          <SectionTitle title="Goal / 최종 결과" meta={final?.intent || "실행 후 표시"} />
          <div className="agent-runtime-goal">{goal}</div>
          {final ? (
            <div className="agent-runtime-final">
              <p>{final.answer}</p>
              <div className="agent-runtime-columns">
                <ListBlock title="missing" items={final.missing || []} empty="없음" />
                <ListBlock title="warnings" items={final.warnings || []} empty="없음" />
                <ListBlock title="next actions" items={final.next_actions || []} empty="없음" />
              </div>
            </div>
          ) : (
            <div className="agent-runtime-empty">SSE 실행 또는 1회 실행 후 결론이 표시됩니다.</div>
          )}
        </div>
        <div>
          <SectionTitle title="AgentSteps trace" meta={streaming ? "streaming" : `${events.length} events`} />
          <StepRail events={events} />
          <div className="agent-runtime-guardrail">
            <span>guardrail</span>
            <Pill tone={guardrail.status === "allowed" ? "ok" : guardrail.status === "blocked" ? "bad" : "warn"}>{guardrail.status || "pending"}</Pill>
            <span>read-only {guardrail.read_only_actions || 0}</span>
            <span>approval {guardrail.approval_required || 0}</span>
            <span>blocked {guardrail.blocked || 0}</span>
          </div>
        </div>
      </section>

      <section className="agent-runtime-section">
        <SectionTitle title="실행 계획 / 결과" meta={`${actionPlan.length} actions · ${results.length} results`} />
        <div className="agent-runtime-split">
          <DataTable
            rows={actionPlan}
            columns={[
              { key: "unit_ai", label: "unit", width: 120 },
              { key: "action", label: "action", width: 160 },
              { key: "policy", label: "policy", width: 150 },
              { key: "missing_slots", label: "missing", render: (r) => (r.missing_slots || []).join(", ") || "-" },
            ]}
            empty="실행 계획이 없습니다."
            maxHeight={240}
          />
          <DataTable
            rows={results}
            columns={[
              { key: "agent_id", label: "agent", width: 180 },
              { key: "status", label: "status", width: 100 },
              { key: "handled", label: "handled", width: 90, render: (r) => r.handled ? "yes" : "no" },
              { key: "summary", label: "summary" },
              { key: "guardrail", label: "guardrail", width: 140, render: (r) => r.guardrail?.status || "-" },
            ]}
            empty="아직 unit action 결과가 없습니다."
            maxHeight={240}
          />
        </div>
      </section>

      <section className="agent-runtime-section">
        <SectionTitle title="보일러플레이트 구조" meta={`${unitAgents.length} unit agents · ${actions.length} actions`} />
        <DataTable
          rows={unitAgents}
          columns={[
            { key: "agent_id", label: "agent", width: 180 },
            { key: "title", label: "title", width: 220 },
            { key: "role", label: "role" },
            { key: "outputs", label: "outputs", render: (r) => (r.outputs || []).join(", ") },
          ]}
          empty="runtime blueprint 없음"
          maxHeight={260}
        />
      </section>
    </div>
  );
}

function Status({ label, ok, warn, text }) {
  return (
    <span className="agent-runtime-status-item">
      <span>{label}</span>
      <Pill tone={ok ? "ok" : warn ? "warn" : "neutral"}>{text}</Pill>
    </span>
  );
}

function latestGuardrail(events) {
  for (let i = (events || []).length - 1; i >= 0; i -= 1) {
    const g = events[i]?.data?.guardrail;
    if (g && typeof g === "object") return g;
  }
  return null;
}

function StepRail({ events }) {
  const rows = (events || []).filter((event) => event.stage && !["start", "done"].includes(event.stage));
  if (!rows.length) return <div className="agent-runtime-empty">아직 실행 단계가 없습니다.</div>;
  return (
    <ol className="agent-runtime-steps">
      {rows.slice(-12).map((event, idx) => (
        <li key={event.event_id || idx}>
          <span className="agent-runtime-step-dot" data-status={event.status || "running"} />
          <div>
            <div className="agent-runtime-step-head">
              <span>{event.stage}</span>
              <Pill tone={event.status === "completed" ? "ok" : event.status === "failed" ? "bad" : event.status === "skipped" ? "warn" : "info"}>{event.status}</Pill>
            </div>
            <div className="agent-runtime-step-msg">{event.message}</div>
          </div>
        </li>
      ))}
    </ol>
  );
}

function SectionTitle({ title, meta }) {
  return (
    <div className="agent-runtime-title">
      <h3>{title}</h3>
      {meta && <span>{meta}</span>}
    </div>
  );
}

function ListBlock({ title, items, empty }) {
  return (
    <div>
      <div className="agent-runtime-list-title">{title}</div>
      {(items || []).length ? (
        <ul>
          {(items || []).map((item, idx) => <li key={idx}>{item}</li>)}
        </ul>
      ) : (
        <div className="agent-runtime-empty">{empty}</div>
      )}
    </div>
  );
}
