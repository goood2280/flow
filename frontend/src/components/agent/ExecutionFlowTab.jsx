// ExecutionFlowTab — Agent V2의 "최근 실행 흐름" 항목 본체.
// 사용자가 직접 prompt를 입력해서 실행하면, 백엔드 trace.activation /
// trace.interpretation.term_resolution / trace.evidence / trace.call_graph /
// trace.steps 를 한 화면에 인라인으로 보여준다. (M4 1차)
//
// 후속 M4 follow-up: 잘못된 token 해석/intent에 대한 인라인 교정 저장과
// 최근 호출 자동 listing은 ExecutionFlowFeedback 단계에서 붙는다.
import { useState } from "react";
import { sf, postJson } from "../../lib/api";
import { Banner, Button, EmptyState, Field } from "../UXKit";
import Loading from "../Loading";

export default function ExecutionFlowTab({ user }) {
  const [prompt, setPrompt] = useState("PRODA A1000 #6 현재 fab lot id가 뭐야?");
  const [product, setProduct] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [result, setResult] = useState(null);

  async function runPrompt() {
    if (!prompt.trim()) { setErr("prompt를 입력해주세요"); return; }
    setBusy(true); setErr(""); setResult(null);
    try {
      const d = await postJson("/api/llm/flowi/chat", { prompt, product, source: "agent_v2_execution_flow" });
      setResult(d);
    } catch (e) {
      setErr(e?.message || "실행 실패");
    } finally {
      setBusy(false);
    }
  }

  const trace = result && result.trace ? result.trace : null;

  return (
    <div style={{ padding: 20, display: "flex", flexDirection: "column", gap: 16 }}>
      <header>
        <h2 style={{ fontSize: 18, margin: "0 0 4px" }}>🔎 최근 실행 흐름</h2>
        <p style={{ fontSize: 12, color: "var(--text-secondary)", margin: 0 }}>
          prompt를 직접 실행해서 단위 AI가 어떻게 해석하고 진행했는지 한눈에 봅니다 — token 해석 / 5단계 activation / call graph / evidence.
        </p>
      </header>

      <section style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", borderRadius: 6, padding: 12, display: "flex", flexDirection: "column", gap: 8 }}>
        <Field label="Prompt">
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={2}
            style={{ width: "100%", padding: 8, fontSize: 13, fontFamily: "inherit", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)" }}
          />
        </Field>
        <Field label="Product hint (선택)">
          <input
            value={product}
            onChange={(e) => setProduct(e.target.value)}
            placeholder="PRODA / PRODB"
            style={{ width: 200, padding: "4px 8px", fontSize: 13, borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)" }}
          />
        </Field>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <Button onClick={runPrompt} disabled={busy}>{busy ? "실행 중..." : "실행"}</Button>
          {result && result.tool && <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>feature={String(result.tool.feature || "—")} · action={String(result.tool.action || "—")}</span>}
        </div>
        {err && <Banner tone="warn">{err}</Banner>}
      </section>

      {busy && <Loading text="단위 AI 실행 중..." size="md" />}

      {result && <AnswerCard result={result} />}
      {trace && <ActivationCard activation={trace.activation || (trace.call_graph && trace.call_graph.activation) || {}} />}
      {trace && trace.interpretation && <InterpretationCard interpretation={trace.interpretation} />}
      {trace && trace.evidence && <EvidenceCard evidence={trace.evidence} />}
      {trace && Array.isArray(trace.steps) && trace.steps.length > 0 && <StepsCard steps={trace.steps} />}
      {trace && trace.call_graph && <CallGraphCard graph={trace.call_graph} />}
    </div>
  );
}

function Section({ title, hint, children }) {
  return (
    <section>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 6 }}>
        <h3 style={{ fontSize: 14, fontWeight: 700, margin: 0, color: "var(--accent)" }}>{title}</h3>
        {hint && <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>{hint}</span>}
      </div>
      {children}
    </section>
  );
}

function AnswerCard({ result }) {
  const answer = result.answer || "";
  return (
    <Section title="답변" hint="LLM이 정리한 사용자용 답변">
      <div style={{ padding: 12, background: "var(--bg-secondary)", border: "1px solid var(--border)", borderRadius: 6, fontSize: 13, whiteSpace: "pre-wrap", maxHeight: 320, overflow: "auto" }}>
        {answer || <span style={{ color: "var(--text-secondary)" }}>(answer 비어있음)</span>}
      </div>
    </Section>
  );
}

// ── Activation 5단계 ────────────────────────────────
const ACTIVATION_STAGES = [
  { key: "01_prompt", label: "01 prompt", hint: "에이전트가 받은 prompt" },
  { key: "02_orchestrator", label: "02 orchestrator", hint: "intent / feature / action 판정" },
  { key: "03_feature_subagent", label: "03 feature subagent", hint: "활성화된 기능 AI / handler" },
  { key: "04_unit_action", label: "04 unit action", hint: "호출된 단위기능 + payload" },
  { key: "05_result", label: "05 result", hint: "결과 / next action / missing" },
];

function ActivationCard({ activation }) {
  return (
    <Section title="Activation Map (5단계)" hint="prompt → orchestrator → feature → unit action → result">
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 8 }}>
        {ACTIVATION_STAGES.map((stage) => {
          const v = activation[stage.key] || activation[stage.label] || {};
          const status = v.status || "—";
          return (
            <div key={stage.key} style={{ padding: 10, background: "var(--bg-secondary)", border: "1px solid var(--border)", borderRadius: 6, display: "flex", flexDirection: "column", gap: 4, fontSize: 12 }}>
              <div style={{ fontWeight: 700, color: "var(--accent)" }}>{stage.label}</div>
              <div style={{ color: "var(--text-secondary)", fontSize: 11 }}>{stage.hint}</div>
              {Object.keys(v).length === 0 ? (
                <div style={{ color: "var(--text-secondary)" }}>—</div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                  {Object.entries(v).map(([k, val]) => (
                    <div key={k}><code style={{ fontSize: 10, color: "var(--text-secondary)" }}>{k}</code>: <span>{renderActivationValue(val)}</span></div>
                  ))}
                </div>
              )}
              <StatusPill status={status} />
            </div>
          );
        })}
      </div>
    </Section>
  );
}

function renderActivationValue(v) {
  if (v == null) return "—";
  if (Array.isArray(v)) return v.slice(0, 4).join(", ");
  if (typeof v === "object") return JSON.stringify(v).slice(0, 80);
  return String(v).slice(0, 100);
}

const STATUS_COLORS = {
  ready: "#60a5fa",
  done: "#10b981",
  needs_input: "#f97316",
  awaiting_confirmation: "#fbbf24",
  blocked: "#ef4444",
  error: "#ef4444",
};

function StatusPill({ status }) {
  const color = STATUS_COLORS[String(status || "").toLowerCase()] || "#9ca3af";
  return <span style={{ alignSelf: "flex-start", fontSize: 10, padding: "1px 6px", borderRadius: 3, background: color + "22", color, fontWeight: 700, marginTop: 4 }}>{status}</span>;
}

// ── Interpretation (token 해석) ────────────────────
function InterpretationCard({ interpretation }) {
  const terms = (interpretation && Array.isArray(interpretation.term_resolution)) ? interpretation.term_resolution : [];
  const slots = (interpretation && interpretation.input_slots) || {};
  return (
    <Section title="생각의 흐름 (token 해석 · slot)" hint="검증 가능한 schema/Wiki/filter 근거만 표시 (hidden chain-of-thought 노출 X)">
      {Object.keys(slots).length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 8 }}>
          {Object.entries(slots).map(([k, v]) => (
            <div key={k} style={{ fontSize: 11, padding: "2px 6px", background: "var(--bg-secondary)", borderRadius: 3 }}>
              <span style={{ color: "var(--text-secondary)" }}>{k}: </span>
              <code>{Array.isArray(v) ? v.join(", ") : String(v || "—")}</code>
            </div>
          ))}
        </div>
      )}
      {terms.length === 0 ? (
        <Banner tone="info">이 prompt에는 별도 token 해석 trace가 없습니다. (Wiki/schema 호출이 발생하지 않은 경우)</Banner>
      ) : (
        <div style={{ overflow: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ background: "var(--bg-hover)", color: "var(--text-secondary)" }}>
                <th style={th()}>token</th>
                <th style={th()}>meaning</th>
                <th style={th()}>wiki refs</th>
                <th style={th()}>query filter</th>
                <th style={th()}>status</th>
              </tr>
            </thead>
            <tbody>
              {terms.map((t, i) => (
                <tr key={i} style={{ borderTop: "1px solid var(--border)" }}>
                  <td style={td("160px", true)}><code>{t.token}</code></td>
                  <td style={td()}>{t.meaning || <span style={{ color: "var(--text-secondary)" }}>—</span>}</td>
                  <td style={td("180px")}>{(t.wiki_refs || []).join(", ") || "—"}</td>
                  <td style={td("240px", true)}>{t.query_filter || "—"}</td>
                  <td style={td("80px")}><StatusPill status={t.status || "—"} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

const th = () => ({ textAlign: "left", padding: "6px 10px", fontWeight: 700, fontSize: 11 });
const td = (width = "auto", mono = false) => ({ padding: "6px 10px", verticalAlign: "top", width, fontFamily: mono ? "monospace" : "inherit" });

// ── Evidence (SQL / sources / relations / knowledge) ─
function EvidenceCard({ evidence }) {
  const rows = [
    { k: "feature", v: evidence.used_feature_ai },
    { k: "endpoint", v: evidence.endpoint },
    { k: "SQL / filter", v: evidence.sql },
    { k: "선택 컬럼", v: evidence.selected_columns },
    { k: "source ids", v: evidence.source_ids },
    { k: "confirmed relations", v: evidence.relation_ids },
    { k: "join keys", v: evidence.join_keys },
    { k: "knowledge sources", v: evidence.knowledge_sources },
  ];
  return (
    <Section title="Evidence (근거)" hint="실제 사용된 데이터 source / SQL / 컬럼 / wiki / relation">
      <div style={{ display: "flex", flexDirection: "column", gap: 4, padding: 10, background: "var(--bg-secondary)", border: "1px solid var(--border)", borderRadius: 6, fontSize: 12 }}>
        {rows.filter((r) => r.v != null && (Array.isArray(r.v) ? r.v.length > 0 : String(r.v))).map((r) => (
          <div key={r.k} style={{ display: "flex", gap: 8 }}>
            <span style={{ width: 160, color: "var(--text-secondary)" }}>{r.k}</span>
            <span style={{ flex: 1, fontFamily: "monospace", wordBreak: "break-word" }}>{Array.isArray(r.v) ? r.v.slice(0, 12).join(", ") : String(r.v)}</span>
          </div>
        ))}
      </div>
    </Section>
  );
}

// ── Steps ──────────────────────────────────────────
function StepsCard({ steps }) {
  return (
    <Section title="Trace Steps" hint="공개 가능한 단계별 진행">
      <ol style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
        {steps.map((s, i) => (
          <li key={i} style={{ marginBottom: 4 }}>
            <span style={{ fontWeight: 600 }}>{s.title || s.stage}</span>
            {s.detail && <span style={{ color: "var(--text-secondary)" }}> — {s.detail}</span>}
            {s.status && <StatusPill status={s.status} />}
          </li>
        ))}
      </ol>
    </Section>
  );
}

// ── Call graph (간단 표시) ────────────────────────
function CallGraphCard({ graph }) {
  const nodes = Array.isArray(graph.nodes) ? graph.nodes : [];
  const edges = Array.isArray(graph.edges) ? graph.edges : [];
  return (
    <Section title="Call Graph" hint="FastAPI → orchestrator → feature → API/data → answer">
      <div style={{ padding: 10, background: "var(--bg-secondary)", border: "1px solid var(--border)", borderRadius: 6, fontSize: 12 }}>
        {nodes.length === 0 ? <span style={{ color: "var(--text-secondary)" }}>nodes 비어있음</span> :
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {nodes.map((n, i) => (
              <code key={i} style={{ padding: "2px 6px", background: "var(--bg-primary)", border: "1px solid var(--border)", borderRadius: 3 }}>
                {n.id || n.key || n.label || JSON.stringify(n).slice(0, 40)}
              </code>
            ))}
          </div>}
        {edges.length > 0 && (
          <div style={{ marginTop: 8, color: "var(--text-secondary)", fontSize: 11 }}>
            edges: {edges.length}개 (상세는 backend trace.call_graph.edges 참조)
          </div>
        )}
      </div>
    </Section>
  );
}
