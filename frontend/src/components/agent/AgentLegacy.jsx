import { useEffect, useMemo, useState } from "react";
import { postJson, qs, sf } from "../../lib/api";
import {
  Banner,
  Button,
  DataTable,
  EmptyState,
  Field,
  Panel,
  Pill,
  formControlStyle,
  uxColors,
} from "../UXKit";
import AgentKnowledgeVault from "../../pages/My_Knowledge";

const SAMPLE_PROMPT = "GAA nFET short Lg에서 DIBL과 SS가 증가했고 CA_RS도 올랐어. 원인 후보와 확인 차트 보여줘.";
const FUNCTION_TEST_PROMPT = "PRODA A1000 #6 현재 fab lot id가 뭐야?";
const FLOWI_ORCHESTRATOR_PREVIEW_API = "/api/llm/flowi/orchestrator/preview";
const DEFAULT_TABLE_CONTENT = "step_id,step_name,func_step\nAA200000,channel release etch,\nAB300000,inner spacer recess,\nCA100000,CA contact etch,";

const QUICK_PROMPTS = [
  { label: "RCA", prompt: SAMPLE_PROMPT },
  { label: "FAB lot", prompt: FUNCTION_TEST_PROMPT },
  { label: "Q1", prompt: "PRODA A1002 24.0 SORT KNOB 구성이 어떻게돼?" },
  { label: "Q2", prompt: "PRODA A1002 #1 24.0 SORT Split이 뭐야? 뭘로 진행했어?" },
  { label: "Q3", prompt: "24.0 SORT PPID_24_3인 자재 가장 빠른게 어디에 있어?" },
  { label: "Q4", prompt: "A1002A.1 어디에 있어?" },
  { label: "Q5", prompt: "A1000 #20 16.0 VIA2 Avg 몇이야?" },
  { label: "Q6", prompt: "PRODA A1000A.3 GATE 모듈 인폼해줘 test1 스플릿으로 선택해줘 내용은 GATE 모듈인폼입니다." },
  { label: "Q7", prompt: "A1003 GATE는 test1 STI는 test2 이런식으로 A1003에 대해서 인폼로그 다 만들어줘" },
  { label: "Q8", prompt: "A1004 인폼전체 작성해줘" },
];

const CATEGORIES = [
  { id: "workflow", icon: "01", label: "워크플로우", desc: "Flowi가 자연어를 받아 안전한 단위기능과 응답으로 바꾸는 전체 pipeline입니다." },
  { id: "persona", icon: "02", label: "유저 페르소나", desc: "현재 로그인 유저의 최근 사용 경향과 Flowi가 가정하는 업무 모델입니다." },
  { id: "prompt", icon: "03", label: "프롬프트 해석", desc: "입력 prompt를 slots, selected_function, arguments JSON, missing choices로 live preview합니다." },
  { id: "knowledge", icon: "04", label: "지식 인벤토리", desc: "RCA 지식, causal edge, similar case, custom knowledge, agent feature, promoted 문서를 통합 검색합니다." },
  { id: "recent", icon: "05", label: "최근 RAG 반영", desc: "최근 Flowi 호출에서 어떤 함수와 지식 id가 쓰였는지 trace를 확인합니다." },
  { id: "item", icon: "06", label: "Item 해석", desc: "semiconductor_knowledge 기반 item 의미와 knob/mask/step 변환 룰을 표로 봅니다." },
  { id: "llm", icon: "07", label: "LLM 설정", desc: "모델, endpoint, timeout, provider, auth mode를 확인하고 admin은 바로 수정합니다." },
  { id: "admin", icon: "🔒", label: "관리 도구", desc: "매칭, 룰북, 지식 주입 도구입니다. admin에게만 열립니다.", adminOnly: true },
  { id: "wiki", icon: "09", label: "Wiki 운영", desc: "raw source를 등록하고 maintained wiki, 검색, 로그, lint를 운영합니다." },
];

const WORKFLOW_STAGE_ICON = {
  input_prompt: "🧩",
  slot_extract: "🧠",
  intent_infer: "🧭",
  arguments: "🧾",
  dispatch: "⚙️",
  cross_db_join: "🔗",
  polish: "✨",
  response: "📤",
};

function categoryFor(id) {
  return CATEGORIES.find((item) => item.id === id) || CATEGORIES[0];
}

const WORKFLOW_STATUS_HINT = {
  input: "요청 수신",
  parse: "구문/slot 정규화",
  choose: "의도 라우팅",
  map: "인자 정형화",
  run: "도구 실행",
  join: "DB join/차트 준비",
  polish: "LLM 정리",
  output: "답변 합성",
};

function workflowStageIcon(key = "") {
  return WORKFLOW_STAGE_ICON[String(key || "").trim()] || "🔹";
}

function flowListFromStages(stages) {
  if (!Array.isArray(stages)) return [];
  return stages
    .map((s) => ({
      ...s,
      key: s?.key || `stage-${Math.random()}`,
      label: String(s?.label || "unnamed"),
      description: String(s?.description || ""),
      modules: Array.isArray(s?.modules) ? s.modules : [],
      knowledgeSources: Array.isArray(s?.knowledge_sources) ? s.knowledge_sources : [],
    }))
    .filter((s) => s.label || s.description || s.modules.length || s.knowledgeSources.length);
}

function WorkflowChainGraph({ stages = [] }) {
  const list = flowListFromStages(stages);
  if (!list.length) return null;
  return (
    <div style={{ display: "grid", gap: 10 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "flex-start", overflowX: "auto", paddingBottom: 4 }}>
        {list.map((stage, idx) => {
          const isLast = idx === list.length - 1;
          const moduleCount = Array.isArray(stage.modules) ? stage.modules.length : 0;
          const sourceCount = Array.isArray(stage.knowledgeSources) ? stage.knowledgeSources.length : 0;
          const hint = idx === 0 ? "input" : idx === list.length - 1 ? "output" : idx < 3 ? "parse" : idx < 5 ? "map" : idx < 7 ? "run" : "output";
          return (
            <div key={stage.key} style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 180 }}>
              <div style={{ border: "1px solid var(--border)", borderRadius: 8, background: "var(--bg-secondary)", padding: 10, minWidth: 180, maxWidth: 240 }}>
                <div style={{ display: "grid", gap: 6 }}>
                  <div style={{ display: "flex", gap: 8, alignItems: "center", justifyContent: "space-between" }}>
                    <div style={{ fontSize: 13, color: uxColors.textSub, fontFamily: "monospace" }}>{String(idx + 1).padStart(2, "0")} · {WORKFLOW_STATUS_HINT[hint] || "stage"}</div>
                    <span style={{ fontSize: 20 }}>{workflowStageIcon(stage.key)}</span>
                  </div>
                  <div style={{ fontSize: 14, fontWeight: 900, color: uxColors.text }}>{stage.label}</div>
                  <div style={{ fontSize: 12, color: uxColors.textSub, lineHeight: 1.45 }}>{stage.description}</div>
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 2 }}>
                    <span style={{ border: "1px solid #2f2f2f", borderRadius: 999, padding: "2px 6px", fontSize: 12, color: uxColors.textSub }}>module {moduleCount}</span>
                    <span style={{ border: "1px solid #2f2f2f", borderRadius: 999, padding: "2px 6px", fontSize: 12, color: uxColors.textSub }}>source {sourceCount}</span>
                  </div>
                </div>
              </div>
              {!isLast && (
                <div style={{ color: uxColors.textSub, fontFamily: "monospace", fontSize: 18, whiteSpace: "nowrap", lineHeight: 1.3, transform: "translateY(22px)" }}>→</div>
              )}
            </div>
          );
        })}
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 7, fontSize: 13, color: uxColors.textSub }}>
        <span style={{ fontFamily: "monospace" }}>{list.length}단계</span>
        <span>·</span>
        <span>연결: API 입력 → 규칙/slot → intent/arg → tool dispatch → join/polish → 응답</span>
      </div>
    </div>
  );
}

function parseGridText(text) {
  const body = String(text || "").trim();
  if (!body) return { columns: ["col_1"], rows: [[""]] };
  const lines = body.split(/\r?\n/).filter((line) => line.trim());
  const delimiter = lines.some((line) => line.includes("\t")) ? "\t" : (lines.some((line) => line.includes("|")) ? "|" : ",");
  const split = (line) => delimiter === "|"
    ? line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((x) => x.trim())
    : line.split(delimiter).map((x) => x.trim());
  const matrix = lines.map(split).filter((row) => !row.every((v) => /^:?-{2,}:?$/.test(v)));
  const width = Math.max(1, ...matrix.map((r) => r.length));
  const normalized = matrix.map((r) => [...r, ...Array(width - r.length).fill("")]);
  const columns = (normalized[0] || []).map((c, i) => c || `col_${i + 1}`);
  const rows = normalized.slice(1);
  return { columns, rows: rows.length ? rows : [Array(columns.length).fill("")] };
}

function gridToCsv(columns, rows) {
  const esc = (value) => {
    const s = String(value ?? "");
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [columns, ...rows].map((row) => row.map(esc).join(",")).join("\n");
}

function listText(v, max = 4) {
  const arr = Array.isArray(v) ? v : [];
  if (!arr.length) return "-";
  return arr.slice(0, max).map((x) => typeof x === "string" ? x : (x?.title || x?.id || x?.target || x?.source || "")).filter(Boolean).join(", ") + (arr.length > max ? ` +${arr.length - max}` : "");
}

function splitCommaTags(v) {
  return String(v || "").split(",").map((x) => x.trim()).filter(Boolean);
}

function JsonBlock({ value, maxHeight = 360 }) {
  return (
    <pre style={{ margin: 0, maxHeight, overflow: "auto", fontSize: 14, lineHeight: 1.45, background: "var(--bg-primary)", border: "1px solid var(--border)", borderRadius: 5, padding: 10 }}>
      {JSON.stringify(value || {}, null, 2)}
    </pre>
  );
}

function compactJsonText(value, limit = 180) {
  if (value === undefined || value === null || value === "") return "-";
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return text.length > limit ? `${text.slice(0, limit)}...` : text;
}

function CategoryNav({ active, onChange, isAdmin }) {
  return (
    <div style={{ display: "grid", gap: 6 }}>
      {CATEGORIES.map((item) => {
        const locked = item.adminOnly && !isAdmin;
        const selected = active === item.id;
        return (
          <button
            key={item.id}
            type="button"
            disabled={locked}
            onClick={() => !locked && onChange(item.id)}
            title={locked ? "admin 전용" : item.desc}
            style={{
              width: "100%",
              display: "grid",
              gridTemplateColumns: "32px 1fr",
              gap: 8,
              alignItems: "center",
              textAlign: "left",
              padding: "9px 10px",
              borderRadius: 6,
              border: `1px solid ${selected ? uxColors.accent : "var(--border)"}`,
              background: selected ? "var(--accent-glow)" : "var(--bg-secondary)",
              color: locked ? uxColors.textSub : (selected ? uxColors.accent : uxColors.text),
              cursor: locked ? "not-allowed" : "pointer",
              opacity: locked ? 0.62 : 1,
            }}
          >
            <span style={{ fontSize: 13, fontWeight: 900, fontFamily: "monospace", color: selected ? uxColors.accent : uxColors.textSub }}>{item.icon}</span>
            <span style={{ display: "grid", gap: 2, minWidth: 0 }}>
              <span style={{ fontSize: 14, fontWeight: 800 }}>{item.label}</span>
              <span style={{ fontSize: 13, color: uxColors.textSub, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{item.desc}</span>
            </span>
          </button>
        );
      })}
    </div>
  );
}

function CategoryFrame({ category, children, right = null }) {
  return (
    <div style={{ display: "grid", gap: 12 }}>
      <Panel title={category.label} subtitle={category.desc} right={right} bodyStyle={{ display: "none" }} />
      {children}
    </div>
  );
}

function WorkflowPanel() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    sf("/api/agent/workflow").then(setData).catch((e) => setErr(e.message || String(e)));
  }, []);
  return (
    <CategoryFrame category={CATEGORIES[0]} right={<Pill tone="accent">{data?.stage_count || 0} stages</Pill>}>
      {err && <Banner tone="bad">{err}</Banner>}
      <Panel title="Flowi LangChain chain" subtitle="입력 prompt부터 응답 렌더링까지 연결되는 주 flow를 표시합니다.">
        <WorkflowChainGraph stages={data?.stages || []} />
      </Panel>
      <Panel title="Flowi pipeline stage" subtitle="상세 모듈/knowledge 매핑">
        <div style={{ display: "grid", gap: 10 }}>
          {flowListFromStages(data?.stages || []).map((stage, idx) => (
            <div key={stage.key} style={{ border: "1px solid var(--border)", borderRadius: 6, background: "var(--bg-primary)", padding: 12 }}>
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginBottom: 6 }}>
                <Pill tone="accent">{String(idx + 1).padStart(2, "0")}</Pill>
                <span style={{ fontSize: 14, fontWeight: 900, color: uxColors.text }}>{stage.label}</span>
                {idx < (data?.stages || []).length - 1 && <span style={{ color: uxColors.textSub, fontFamily: "monospace" }}>→</span>}
              </div>
              <div style={{ fontSize: 14, color: uxColors.textSub, lineHeight: 1.55, marginBottom: 8 }}>{stage.description}</div>
              <div style={{ display: "grid", gap: 6 }}>
                <div style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>{(stage.modules || []).map((x) => <Pill key={x} tone="neutral">{x}</Pill>)}</div>
                <div style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>{(stage.knowledge_sources || []).map((x) => <Pill key={x} tone="info">{x}</Pill>)}</div>
              </div>
            </div>
          ))}
          {!data?.stages?.length && <EmptyState title="워크플로우 데이터 없음" hint="backend workflow endpoint 응답을 기다리는 중입니다." />}
        </div>
      </Panel>
    </CategoryFrame>
  );
}

function PersonaPanel() {
  const [persona, setPersona] = useState(null);
  const [card, setCard] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    sf("/api/agent/persona").then(setPersona).catch((e) => setErr(e.message || String(e)));
    sf("/api/llm/flowi/persona-card").then(setCard).catch(() => {});
  }, []);
  const doList = card?.do_list || [];
  const dontList = card?.dont_list || [];
  return (
    <CategoryFrame category={CATEGORIES[1]} right={<Pill tone="accent">{persona?.role || "-"}</Pill>}>
      {err && <Banner tone="bad">{err}</Banner>}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(260px,1fr))", gap: 12 }}>
        <Panel title="최근 사용 모듈" subtitle="사용자 본인 activity 기반">
          <DataTable
            rows={persona?.recent_modules || []}
            empty="최근 모듈 사용 기록이 없습니다."
            columns={[
              { key: "name", label: "module/function" },
              { key: "count", label: "count", width: 70 },
            ]}
          />
        </Panel>
        <Panel title="자주 본 product/lot" subtitle="prompt와 활동 로그에서 추정">
          <DataTable
            rows={persona?.frequent_products || []}
            empty="product 사용 기록이 아직 없습니다."
            columns={[
              { key: "product", label: "product" },
              { key: "count", label: "count", width: 70 },
            ]}
          />
        </Panel>
      </div>
      <Panel title="도우미가 도와주는 일 / 하지 않는 일" subtitle="홈 Flowi persona-card 응답 재사용" right={<Pill tone="ok">기본 펼침</Pill>}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(260px,1fr))", gap: 12 }}>
          <div style={{ display: "grid", gap: 6 }}>
            <div style={{ fontSize: 14, fontWeight: 900, color: uxColors.accent }}>도와주는 일</div>
            <div style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>{doList.map((x) => <Pill key={x} tone="accent">{x}</Pill>)}</div>
            {!doList.length && <EmptyState title="do_list 없음" hint="persona-card 응답이 비어 있습니다." />}
          </div>
          <div style={{ display: "grid", gap: 6 }}>
            <div style={{ fontSize: 14, fontWeight: 900, color: uxColors.accent }}>하지 않는 일</div>
            <div style={{ display: "grid", gap: 5 }}>{dontList.map((x, i) => <div key={i} style={{ fontSize: 14, color: uxColors.textSub, lineHeight: 1.45 }}>{x}</div>)}</div>
            {!dontList.length && <EmptyState title="dont_list 없음" hint="persona-card 응답이 비어 있습니다." />}
          </div>
        </div>
      </Panel>
      <Panel title="도메인 관리자/Admin 페르소나" subtitle="앱이 가정하는 행동 모델">
        <div style={{ display: "grid", gap: 8 }}>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            <Pill tone="accent">{persona?.admin_persona?.label || "반도체 공정 데이터 분석가"}</Pill>
            <Pill tone="neutral">{persona?.admin_persona?.role || "semiconductor_process_data_analyst"}</Pill>
          </div>
          {(persona?.admin_persona?.principles || []).map((x, i) => <Banner key={i} tone="info">{x}</Banner>)}
          <DataTable
            rows={persona?.last_actions || []}
            empty="최근 Flowi action 기록이 없습니다."
            columns={[
              { key: "timestamp", label: "time", width: 128, render: (r) => String(r.timestamp || "").replace("T", " ").slice(0, 16) },
              { key: "selected_function", label: "function", width: 170 },
              { key: "result_status", label: "status", width: 90 },
              { key: "prompt", label: "prompt" },
            ]}
          />
        </div>
      </Panel>
    </CategoryFrame>
  );
}

function PromptPanel() {
  const [prompt, setPrompt] = useState(SAMPLE_PROMPT);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const inputStyle = { ...formControlStyle, width: "100%", boxSizing: "border-box", fontSize: 14 };
  const run = () => {
    setBusy(true);
    setErr("");
    postJson("/api/agent/prompt-preview", { prompt, product: "", max_rows: 20 })
      .then(setResult)
      .catch((e) => setErr(e.message || String(e)))
      .finally(() => setBusy(false));
  };
  const args = result?.function_call?.function?.arguments || {};
  const selected = result?.selected_function || {};
  const missing = result?.validation?.missing || [];
  const choices = result?.arguments_choices?.fields || [];
  return (
    <CategoryFrame category={CATEGORIES[2]} right={<Pill tone={result?.validation?.valid ? "ok" : "neutral"}>{result ? (result.validation?.valid ? "valid" : "missing") : "ready"}</Pill>}>
      <Panel title="Live prompt demo" subtitle="기존 Intent / slot 해석과 function-call preview를 통합했습니다.">
        <div style={{ display: "grid", gap: 10, alignItems: "end" }}>
          <Field label="prompt">
            <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={4} style={{ ...inputStyle, resize: "vertical", lineHeight: 1.55 }} />
          </Field>
        </div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center", marginTop: 10 }}>
          <Button variant="primary" onClick={run} disabled={busy || !prompt.trim()}>{busy ? "실행 중" : "해석 실행"}</Button>
          {QUICK_PROMPTS.map((item) => <Button key={item.label} variant="subtle" onClick={() => setPrompt(item.prompt)}>{item.label}</Button>)}
        </div>
        {err && <Banner tone="bad" style={{ marginTop: 10 }}>{err}</Banner>}
      </Panel>
      {result && (
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0,0.9fr) minmax(0,1.1fr)", gap: 12, alignItems: "start" }}>
          <Panel title="추출 결과" subtitle={selected.reason || ""}>
            <div style={{ display: "grid", gap: 10 }}>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                <Pill tone="accent">{selected.name || "-"}</Pill>
                <Pill tone="neutral">{selected.intent || "-"}</Pill>
                <Pill tone={missing.length ? "warn" : "ok"}>{missing.length ? "missing" : "complete"}</Pill>
              </div>
              <DataTable
                rows={[
                  { key: "root_lot", value: listText(result?.slots?.root_lot_ids || args.root_lot_ids) },
                  { key: "fab_lot", value: listText(result?.slots?.fab_lot_ids || args.fab_lot_ids) },
                  { key: "wafer", value: listText(result?.slots?.wafers || args.wafer_ids) },
                  { key: "step", value: listText(result?.slots?.steps || (args.step ? [args.step] : [])) },
                  { key: "knob", value: listText(result?.slots?.knobs || (args.knob_value ? [args.knob_value] : [])) },
                ]}
                columns={[
                  { key: "key", label: "slot", width: 120 },
                  { key: "value", label: "value" },
                ]}
              />
              {missing.length ? <Banner tone="warn">missing: {missing.join(", ")}</Banner> : null}
              <DataTable
                rows={choices}
                empty="missing field 선택지가 없습니다."
                columns={[
                  { key: "field", label: "field", width: 150 },
                  { key: "choices", label: "1/2/3 choices", render: (r) => listText(r.choices, 3) },
                  { key: "free_input_label", label: "free input", width: 140 },
                ]}
              />
            </div>
          </Panel>
          <Panel title="Arguments JSON" subtitle="실제 router/tool 호출 전에 쓰는 구조">
            <JsonBlock value={{ selected_function: selected, arguments: args, few_shot_examples: result.few_shot_examples || [] }} maxHeight={560} />
          </Panel>
        </div>
      )}
    </CategoryFrame>
  );
}

function KnowledgePanel({ user, isAdmin }) {
  const [q, setQ] = useState("");
  const [tag, setTag] = useState("");
  const [kind, setKind] = useState("all");
  const [data, setData] = useState(null);
  const [selected, setSelected] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const load = () => {
    setBusy(true);
    setErr("");
    sf("/api/agent/knowledge-inventory" + qs({ q, tag, kind: kind === "all" ? "" : kind }))
      .then((d) => {
        setData(d);
        setSelected((cur) => (cur && (d.items || []).find((x) => x.id === cur.id)) || (d.items || [])[0] || null);
      })
      .catch((e) => setErr(e.message || String(e)))
      .finally(() => setBusy(false));
  };
  useEffect(() => { load(); }, []);
  const togglePromote = (item) => {
    if (!isAdmin || !item) return;
    postJson("/api/agent/knowledge-inventory/promote", {
      id: item.id,
      kind: item.kind,
      title: item.title,
      summary: item.summary,
      content: item.content,
      tags: item.tags || [],
      source: item.source || "",
      promoted: !item.promoted,
    }).then(load).catch((e) => setErr(e.message || String(e)));
  };
  return (
    <CategoryFrame category={CATEGORIES[3]} right={<Pill tone="accent">{data?.items?.length || 0} items</Pill>}>
      {err && <Banner tone="bad">{err}</Banner>}
      <Panel title="검색 / 필터" subtitle="kind와 tag를 조합해 통합 지식 리스트를 좁힙니다.">
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 180px 210px auto auto", gap: 8, alignItems: "end" }}>
          <Field label="검색어">
            <input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && load()} style={{ ...formControlStyle, width: "100%" }} placeholder="item, module, 원인, 함수" />
          </Field>
          <Field label="tag">
            <input value={tag} onChange={(e) => setTag(e.target.value)} onKeyDown={(e) => e.key === "Enter" && load()} style={{ ...formControlStyle, width: "100%" }} placeholder="DIBL, CA" />
          </Field>
          <Field label="kind">
            <select value={kind} onChange={(e) => setKind(e.target.value)} style={{ ...formControlStyle, width: "100%" }}>
              <option value="all">전체</option>
              {(data?.kinds || ["knowledge_cards", "causal_edges", "similar_cases", "custom_knowledge", "agent_features", "promoted_docs"]).map((x) => <option key={x} value={x}>{x}</option>)}
            </select>
          </Field>
          <Button variant="primary" onClick={load} disabled={busy}>{busy ? "검색 중" : "검색"}</Button>
          <Button variant="subtle" onClick={() => { setQ(""); setTag(""); setKind("all"); setTimeout(load, 0); }}>초기화</Button>
        </div>
      </Panel>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(320px,0.72fr)", gap: 12, alignItems: "start" }}>
        <Panel title="분류된 카드 list" subtitle="카드 클릭 시 우측 detail 패널에 본문과 관련 함수를 표시합니다.">
          <DataTable
            rows={data?.items || []}
            empty="조건에 맞는 지식이 없습니다."
            onRowClick={setSelected}
            columns={[
              { key: "promoted", label: "★", width: 52, render: (r) => isAdmin ? <Button variant="subtle" onClick={(e) => { e?.stopPropagation?.(); togglePromote(r); }} title="promote/unpromote">{r.promoted ? "★" : "☆"}</Button> : (r.promoted ? "★" : "") },
              { key: "kind", label: "kind", width: 140, render: (r) => <Pill tone={r.kind === "promoted_docs" ? "accent" : "neutral"}>{r.kind}</Pill> },
              { key: "title", label: "title" },
              { key: "tags", label: "tags", render: (r) => listText(r.tags, 4) },
            ]}
          />
        </Panel>
        <Panel title="Detail" subtitle={selected?.kind || "선택된 카드 없음"}>
          {selected ? (
            <div style={{ display: "grid", gap: 10 }}>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
                <Pill tone="accent">{selected.id}</Pill>
                {selected.promoted && <Pill tone="ok">promoted</Pill>}
              </div>
              <div style={{ fontSize: 16, fontWeight: 900, color: uxColors.text }}>{selected.title}</div>
              <div style={{ fontSize: 14, color: uxColors.textSub, lineHeight: 1.6, whiteSpace: "pre-wrap" }}>{selected.content || selected.summary || "-"}</div>
              <div style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>{(selected.related_functions || []).map((x) => <Pill key={x} tone="info">{x}</Pill>)}</div>
              <JsonBlock value={selected.raw} maxHeight={300} />
            </div>
          ) : <EmptyState title="선택된 지식 없음" hint="왼쪽 리스트에서 카드를 선택하세요." />}
        </Panel>
      </div>
      <Panel title="Agent Knowledge Vault / Ontology" subtitle="에이전트가 참고하는 raw event, wiki, ontology graph를 같은 화면에서 관리합니다.">
        <AgentKnowledgeVault user={user} embedded />
      </Panel>
    </CategoryFrame>
  );
}

function AgentWikiPanel({ canManage }) {
  const [sources, setSources] = useState([]);
  const [pages, setPages] = useState([]);
  const [logs, setLogs] = useState([]);
  const [selectedSource, setSelectedSource] = useState(null);
  const [selectedPage, setSelectedPage] = useState(null);
  const [sourceForm, setSourceForm] = useState({ source_type: "markdown", title: "", tags: "", content: "" });
  const [preview, setPreview] = useState(null);
  const [searchQ, setSearchQ] = useState("");
  const [searchRows, setSearchRows] = useState([]);
  const [lint, setLint] = useState(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const inputStyle = { ...formControlStyle, width: "100%", boxSizing: "border-box", fontSize: 14 };

  const load = () => {
    setBusy(true);
    Promise.all([
      sf("/api/agent/wiki/sources?limit=80"),
      sf("/api/agent/wiki/pages?limit=120"),
      sf("/api/agent/wiki/log?limit=80"),
    ])
      .then(([s, p, l]) => {
        setSources(s.sources || []);
        setPages(p.pages || []);
        setLogs(l.logs || []);
        setMsg("");
      })
      .catch((e) => setMsg("오류: " + (e.message || e)))
      .finally(() => setBusy(false));
  };

  useEffect(() => { load(); }, []);

  const registerSource = () => {
    if (!canManage) return;
    const content = sourceForm.content.trim();
    if (!content) {
      setMsg("source content가 필요합니다.");
      return;
    }
    setBusy(true);
    postJson("/api/agent/wiki/sources", {
      source_type: sourceForm.source_type || "markdown",
      title: sourceForm.title || "Agent Wiki Source",
      tags: splitCommaTags(sourceForm.tags),
      content,
    })
      .then((d) => {
        setSelectedSource(d.source || null);
        setSourceForm((f) => ({ ...f, content: "" }));
        setMsg(`source 등록됨: ${d.source?.source_id || "-"}`);
        load();
      })
      .catch((e) => setMsg("오류: " + (e.message || e)))
      .finally(() => setBusy(false));
  };

  const previewIngest = () => {
    const sourceIds = selectedSource?.source_id ? [selectedSource.source_id] : [];
    const content = sourceIds.length ? "" : sourceForm.content.trim();
    if (!sourceIds.length && !content) {
      setMsg("선택된 source 또는 본문이 필요합니다.");
      return;
    }
    setBusy(true);
    postJson("/api/agent/wiki/ingest/preview", {
      source_ids: sourceIds,
      title: sourceForm.title || selectedSource?.title || "",
      tags: splitCommaTags(sourceForm.tags),
      content,
    })
      .then((d) => {
        setPreview(d.preview || null);
        setMsg("preview 생성됨");
      })
      .catch((e) => setMsg("오류: " + (e.message || e)))
      .finally(() => setBusy(false));
  };

  const commitIngest = () => {
    if (!canManage || !preview) return;
    const sourceIds = Array.isArray(preview.source_ids) ? preview.source_ids : [];
    setBusy(true);
    postJson("/api/agent/wiki/ingest/commit", {
      source_ids: sourceIds,
      doc_id: preview.doc_id,
      title: preview.title,
      summary: preview.summary,
      body: preview.body,
      tags: preview.tags || splitCommaTags(sourceForm.tags),
      content: sourceIds.length ? "" : sourceForm.content.trim(),
    })
      .then((d) => {
        setSelectedPage(d.doc || null);
        setMsg(`wiki page 저장됨: ${d.doc?.doc_id || "-"}`);
        load();
      })
      .catch((e) => setMsg("오류: " + (e.message || e)))
      .finally(() => setBusy(false));
  };

  const openPage = (row) => {
    const docId = row?.doc_id || row?.id;
    if (!docId) return;
    setBusy(true);
    sf("/api/agent/wiki/page" + qs({ doc_id: docId }))
      .then((d) => setSelectedPage(d.page || null))
      .catch((e) => setMsg("오류: " + (e.message || e)))
      .finally(() => setBusy(false));
  };

  const runSearch = () => {
    const q = searchQ.trim();
    if (!q) return;
    setBusy(true);
    sf("/api/agent/wiki/search" + qs({ q, limit: 50 }))
      .then((d) => setSearchRows(d.results || []))
      .catch((e) => setMsg("오류: " + (e.message || e)))
      .finally(() => setBusy(false));
  };

  const runLint = () => {
    if (!canManage) return;
    setBusy(true);
    postJson("/api/agent/wiki/lint", {})
      .then((d) => {
        setLint(d);
        setMsg("lint 완료");
        sf("/api/agent/wiki/log?limit=80").then((x) => setLogs(x.logs || [])).catch(() => {});
      })
      .catch((e) => setMsg("오류: " + (e.message || e)))
      .finally(() => setBusy(false));
  };

  const lintCounts = lint?.counts || {};
  return (
    <CategoryFrame category={categoryFor("wiki")} right={<Pill tone={canManage ? "accent" : "neutral"}>{canManage ? "manage" : "read only"}</Pill>}>
      {msg && <Banner tone={msg.startsWith("오류") ? "bad" : msg.includes("필요") ? "warn" : "ok"}>{msg}</Banner>}
      <Panel title="Sources" subtitle="raw source는 append-only로 저장하고 wiki page의 입력으로 사용합니다.">
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(320px,0.95fr)", gap: 12, alignItems: "start" }}>
          <div style={{ display: "grid", gap: 8 }}>
            <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 150px", gap: 8 }}>
              <Field label="source title">
                <input value={sourceForm.title} onChange={(e) => setSourceForm({ ...sourceForm, title: e.target.value })} style={inputStyle} placeholder="회의록, spec, RCA 메모" />
              </Field>
              <Field label="type">
                <select value={sourceForm.source_type} onChange={(e) => setSourceForm({ ...sourceForm, source_type: e.target.value })} style={inputStyle}>
                  <option value="markdown">markdown</option>
                  <option value="text">text</option>
                  <option value="meeting_note">meeting_note</option>
                  <option value="rca_report">rca_report</option>
                </select>
              </Field>
            </div>
            <Field label="tags">
              <input value={sourceForm.tags} onChange={(e) => setSourceForm({ ...sourceForm, tags: e.target.value })} style={inputStyle} placeholder="DIBL, CA, GAA" />
            </Field>
            <Field label="source content">
              <textarea value={sourceForm.content} onChange={(e) => setSourceForm({ ...sourceForm, content: e.target.value })} rows={8} style={{ ...inputStyle, resize: "vertical", lineHeight: 1.55 }} />
            </Field>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
              {canManage && <Button variant="primary" onClick={registerSource} disabled={busy || !sourceForm.content.trim()}>Source 등록</Button>}
              <Button onClick={previewIngest} disabled={busy || (!selectedSource && !sourceForm.content.trim())}>Ingest Preview</Button>
              {canManage && <Button variant="primary" onClick={commitIngest} disabled={busy || !preview}>Commit Wiki</Button>}
              {selectedSource && <Pill tone="accent">{selectedSource.source_id}</Pill>}
            </div>
          </div>
          <DataTable
            rows={sources}
            empty="등록된 source가 없습니다."
            onRowClick={setSelectedSource}
            maxHeight={360}
            columns={[
              { key: "created_at", label: "time", width: 128, render: (r) => String(r.created_at || "").replace("T", " ").slice(0, 16) },
              { key: "title", label: "title" },
              { key: "source_type", label: "type", width: 110 },
              { key: "content_chars", label: "chars", width: 80 },
              { key: "tags", label: "tags", render: (r) => listText(r.tags, 3) },
            ]}
          />
        </div>
      </Panel>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(320px,0.9fr)", gap: 12, alignItems: "start" }}>
        <Panel title="Ingest Preview" subtitle="commit 전 생성될 maintained markdown page입니다.">
          {preview ? (
            <div style={{ display: "grid", gap: 10 }}>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                <Pill tone="accent">{preview.doc_id}</Pill>
                <Pill tone="neutral">{preview.content_chars || 0} chars</Pill>
                <Pill tone="info">{preview.source_count || 0} sources</Pill>
              </div>
              <div style={{ fontSize: 16, fontWeight: 900 }}>{preview.title}</div>
              <div style={{ fontSize: 14, color: uxColors.textSub, lineHeight: 1.55 }}>{preview.summary}</div>
              <pre style={{ margin: 0, maxHeight: 360, overflow: "auto", whiteSpace: "pre-wrap", fontSize: 13, lineHeight: 1.55, background: "var(--bg-primary)", border: "1px solid var(--border)", borderRadius: 5, padding: 10 }}>{preview.body}</pre>
            </div>
          ) : <EmptyState title="preview 없음" hint="source를 선택하거나 본문을 입력한 뒤 preview를 실행하세요." />}
        </Panel>
        <Panel title="Wiki Pages" subtitle="Agent Wiki kind로 저장된 maintained page 목록">
          <DataTable
            rows={pages}
            empty="Agent Wiki page가 없습니다."
            onRowClick={openPage}
            maxHeight={430}
            columns={[
              { key: "title", label: "title" },
              { key: "updated_at", label: "updated", width: 128, render: (r) => String(r.updated_at || "").replace("T", " ").slice(0, 16) },
              { key: "source_ids", label: "sources", render: (r) => listText(r.source_ids, 2) },
              { key: "tags", label: "tags", render: (r) => listText(r.tags, 3) },
            ]}
          />
        </Panel>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,0.95fr) minmax(320px,1.05fr)", gap: 12, alignItems: "start" }}>
        <Panel title="Wiki Search" subtitle="wiki_index 기반 검색과 page drill-down">
          <div style={{ display: "flex", gap: 8, alignItems: "end", marginBottom: 10 }}>
            <Field label="query">
              <input value={searchQ} onChange={(e) => setSearchQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && runSearch()} style={{ ...inputStyle, minWidth: 260 }} placeholder="DIBL, source id, summary" />
            </Field>
            <Button variant="primary" onClick={runSearch} disabled={busy || !searchQ.trim()}>검색</Button>
          </div>
          <DataTable
            rows={searchRows}
            empty="검색 결과가 없습니다."
            onRowClick={openPage}
            maxHeight={320}
            columns={[
              { key: "score", label: "score", width: 70 },
              { key: "title", label: "title" },
              { key: "snippet", label: "snippet" },
            ]}
          />
        </Panel>
        <Panel title="Page Detail" subtitle={selectedPage?.doc_id || "선택된 page 없음"}>
          {selectedPage ? (
            <div style={{ display: "grid", gap: 10 }}>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                <Pill tone="accent">{selectedPage.kind}</Pill>
                <Pill tone="neutral">{selectedPage.path || "-"}</Pill>
              </div>
              <div style={{ fontSize: 18, fontWeight: 900 }}>{selectedPage.title}</div>
              <div style={{ fontSize: 14, color: uxColors.textSub, lineHeight: 1.55 }}>{selectedPage.summary}</div>
              <pre style={{ margin: 0, maxHeight: 420, overflow: "auto", whiteSpace: "pre-wrap", fontSize: 13, lineHeight: 1.55, background: "var(--bg-primary)", border: "1px solid var(--border)", borderRadius: 5, padding: 10 }}>{selectedPage.body}</pre>
            </div>
          ) : <EmptyState title="page detail 없음" hint="page 목록 또는 search 결과에서 하나를 선택하세요." />}
        </Panel>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(320px,0.85fr)", gap: 12, alignItems: "start" }}>
        <Panel title="Log" subtitle="source_register, ingest_commit, lint 기록">
          <DataTable
            rows={logs}
            empty="Agent Wiki log가 없습니다."
            maxHeight={320}
            columns={[
              { key: "created_at", label: "time", width: 128, render: (r) => String(r.created_at || "").replace("T", " ").slice(0, 16) },
              { key: "action", label: "action", width: 120 },
              { key: "actor", label: "actor", width: 100 },
              { key: "doc_id", label: "doc/source", render: (r) => r.doc_id || listText(r.source_ids, 1) },
              { key: "message", label: "message" },
            ]}
          />
        </Panel>
        <Panel title="Lint" subtitle="orphan, broken link, stale summary, contradiction 후보">
          <div style={{ display: "grid", gap: 10 }}>
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              {canManage && <Button variant="primary" onClick={runLint} disabled={busy}>Lint 실행</Button>}
              {!canManage && <Pill tone="neutral">read only</Pill>}
              {Object.entries(lintCounts).map(([k, v]) => <Pill key={k} tone={v ? "warn" : "neutral"}>{k}: {v}</Pill>)}
            </div>
            {lint ? <JsonBlock value={lint} maxHeight={300} /> : <EmptyState title="lint 결과 없음" hint="관리 권한이 있으면 lint를 실행할 수 있습니다." />}
          </div>
        </Panel>
      </div>
    </CategoryFrame>
  );
}

function RecentRagPanel({ user }) {
  const [rows, setRows] = useState([]);
  const [err, setErr] = useState("");
  const [limit, setLimit] = useState(50);
  const load = () => {
    setErr("");
    sf("/api/agent/recent-rag" + qs({ limit }))
      .then((d) => setRows(d.traces || []))
      .catch((e) => setErr(e.message || String(e)));
  };
  useEffect(() => { load(); }, []);
  return (
    <CategoryFrame category={CATEGORIES[4]} right={<Pill tone="accent">{rows.length} traces</Pill>}>
      {err && <Banner tone="bad">{err}</Banner>}
      <Panel title="최근 Flowi 호출 trace" subtitle="prompt, selected_function, retrieved knowledge id, score, 사용 시간, 결과 분류">
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 10 }}>
          <Field label="limit">
            <input type="number" min={1} max={50} value={limit} onChange={(e) => setLimit(Number(e.target.value) || 50)} style={{ ...formControlStyle, width: 90 }} />
          </Field>
          <Button onClick={load}>새로고침</Button>
          <span style={{ fontSize: 14, color: uxColors.textSub }}>현재 사용자: {user?.username || "-"}</span>
        </div>
        <DataTable
          rows={rows}
          empty="최근 Flowi trace가 없습니다."
          columns={[
            { key: "timestamp", label: "time", width: 128, render: (r) => String(r.timestamp || "").replace("T", " ").slice(0, 16) },
            { key: "selected_function", label: "function", width: 180 },
            { key: "retrieved_ids", label: "knowledge ids", render: (r) => listText(r.retrieved_ids, 4) },
            { key: "score", label: "score", width: 70, render: (r) => r.score ?? "-" },
            { key: "elapsed_ms", label: "ms", width: 70, render: (r) => r.elapsed_ms ?? "-" },
            { key: "result_type", label: "result", width: 90 },
            { key: "prompt", label: "prompt" },
          ]}
        />
      </Panel>
    </CategoryFrame>
  );
}

function ItemRulesPanel() {
  const [sourceType, setSourceType] = useState("");
  const [product, setProduct] = useState("");
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const load = () => {
    setErr("");
    sf("/api/agent/item-rules" + qs({ source_type: sourceType, product }))
      .then(setData)
      .catch((e) => setErr(e.message || String(e)));
  };
  useEffect(() => { load(); }, []);
  return (
    <CategoryFrame category={CATEGORIES[5]} right={<Pill tone="accent">{data?.rules?.length || 0} rules</Pill>}>
      {err && <Banner tone="bad">{err}</Banner>}
      <Panel title="Item → knob/mask/step 변환 룰" subtitle="semiconductor_knowledge ITEM_MASTER 요약 dump">
        <div style={{ display: "flex", gap: 8, alignItems: "end", flexWrap: "wrap", marginBottom: 10 }}>
          <Field label="source_type">
            <select value={sourceType} onChange={(e) => setSourceType(e.target.value)} style={{ ...formControlStyle, width: 140 }}>
              <option value="">전체</option>
              <option value="ET">ET</option>
              <option value="INLINE">INLINE</option>
              <option value="VM">VM</option>
              <option value="FAB">FAB</option>
            </select>
          </Field>
          <Field label="product">
            <input value={product} onChange={(e) => setProduct(e.target.value)} style={{ ...formControlStyle, width: 140 }} placeholder="optional" />
          </Field>
          <Button onClick={load}>필터 적용</Button>
        </div>
        <DataTable
          rows={data?.rules || []}
          empty="item rule 데이터가 없습니다."
          columns={[
            { key: "item", label: "item", width: 130 },
            { key: "matching_step_id", label: "step_id/module", width: 150 },
            { key: "matching_knob", label: "knob", width: 140 },
            { key: "matching_mask", label: "mask", width: 140 },
            { key: "source_type", label: "source", width: 80 },
            { key: "product", label: "product", width: 90 },
            { key: "raw_names", label: "raw names", render: (r) => listText(r.raw_names, 4) },
            { key: "rule", label: "rule/source" },
          ]}
        />
      </Panel>
    </CategoryFrame>
  );
}

function AdminToolsPanel({ isAdmin }) {
  const [section, setSection] = useState("matching");
  if (!isAdmin) {
    return (
      <CategoryFrame category={CATEGORIES[7]} right={<Pill tone="warn">locked</Pill>}>
        <Panel title="Admin only">
          <EmptyState icon="🔒" title="관리 도구는 admin 전용입니다." hint="일반 유저에게는 매칭/룰북/지식 주입 폼을 표시하지 않습니다." />
        </Panel>
      </CategoryFrame>
    );
  }
  const sections = [
    { id: "matching", label: "매칭 어시스턴트" },
    { id: "rulebook", label: "룰북 어시스턴트" },
    { id: "knowledge", label: "지식 주입" },
  ];
  return (
    <CategoryFrame category={CATEGORIES[7]} right={<Pill tone="accent">admin</Pill>}>
      <Panel title="관리 도구 sub-section" subtitle="한 번에 하나의 관리 흐름만 엽니다.">
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {sections.map((item) => <Button key={item.id} variant={section === item.id ? "primary" : "subtle"} onClick={() => setSection(item.id)}>{item.label}</Button>)}
        </div>
      </Panel>
      {section === "matching" && <MatchingAssistant />}
      {section === "rulebook" && <RulebookAssistant />}
      {section === "knowledge" && <KnowledgeIngestAssistant />}
    </CategoryFrame>
  );
}

function MatchingAssistant() {
  const [form, setForm] = useState({ product: "PRODA", source_table: "ML_TABLE" });
  const [result, setResult] = useState(null);
  const [msg, setMsg] = useState("");
  const suggest = () => {
    setMsg("");
    postJson("/api/agent/admin-tools/matching/suggest", form)
      .then(setResult)
      .catch((e) => setMsg("오류: " + (e.message || e)));
  };
  const apply = () => {
    setMsg("");
    postJson("/api/agent/admin-tools/matching/apply", { ...form, candidates: result?.candidates || [] })
      .then((d) => setMsg(`적용됨 / backup: ${d.backup || "-"}`))
      .catch((e) => setMsg("오류: " + (e.message || e)));
  };
  return (
    <Panel title="매칭 어시스턴트" subtitle="product/source_table에서 ML_TABLE 컬럼 매핑 후보를 추천하고 backup 후 적용합니다.">
      {msg && <Banner tone={msg.startsWith("오류") ? "bad" : "ok"}>{msg}</Banner>}
      <div style={{ display: "grid", gridTemplateColumns: "160px minmax(0,1fr) auto", gap: 8, alignItems: "end", marginBottom: 10 }}>
        <Field label="product">
          <input value={form.product} onChange={(e) => setForm({ ...form, product: e.target.value })} style={{ ...formControlStyle, width: "100%" }} />
        </Field>
        <Field label="source_table">
          <input value={form.source_table} onChange={(e) => setForm({ ...form, source_table: e.target.value })} style={{ ...formControlStyle, width: "100%" }} />
        </Field>
        <Button variant="primary" onClick={suggest}>추천</Button>
      </div>
      <DataTable
        rows={result?.candidates || []}
        empty="아직 추천 결과가 없습니다."
        columns={[
          { key: "target", label: "target", width: 130 },
          { key: "source_column", label: "ML_TABLE column", width: 180 },
          { key: "score", label: "score", width: 70 },
          { key: "reason", label: "reason" },
        ]}
      />
      <div style={{ marginTop: 10 }}>
        <Button variant="primary" onClick={apply} disabled={!result?.candidates?.length}>적용</Button>
      </div>
    </Panel>
  );
}

function RulebookAssistant() {
  const [form, setForm] = useState({ product: "PRODA", knob: "", mask: "", change_summary: "" });
  const [result, setResult] = useState(null);
  const [msg, setMsg] = useState("");
  const inputStyle = { ...formControlStyle, width: "100%", boxSizing: "border-box" };
  const suggest = () => {
    setMsg("");
    postJson("/api/agent/admin-tools/rulebook/suggest", form)
      .then(setResult)
      .catch((e) => setMsg("오류: " + (e.message || e)));
  };
  const apply = () => {
    setMsg("");
    postJson("/api/agent/admin-tools/rulebook/apply", { ...form, candidates: result?.candidates || [] })
      .then((d) => setMsg(`적용됨 / backup: ${d.backup || "-"}`))
      .catch((e) => setMsg("오류: " + (e.message || e)));
  };
  return (
    <Panel title="룰북 어시스턴트" subtitle="knob/mask 변경 요약에서 영향 step/item 후보를 제안하고 backup 후 적용합니다.">
      {msg && <Banner tone={msg.startsWith("오류") ? "bad" : "ok"}>{msg}</Banner>}
      <div style={{ display: "grid", gridTemplateColumns: "140px 1fr 1fr", gap: 8, marginBottom: 8 }}>
        <Field label="product"><input value={form.product} onChange={(e) => setForm({ ...form, product: e.target.value })} style={inputStyle} /></Field>
        <Field label="knob"><input value={form.knob} onChange={(e) => setForm({ ...form, knob: e.target.value })} style={inputStyle} /></Field>
        <Field label="mask"><input value={form.mask} onChange={(e) => setForm({ ...form, mask: e.target.value })} style={inputStyle} /></Field>
      </div>
      <Field label="변경 요약">
        <textarea value={form.change_summary} onChange={(e) => setForm({ ...form, change_summary: e.target.value })} rows={4} style={{ ...inputStyle, resize: "vertical", lineHeight: 1.55 }} />
      </Field>
      <div style={{ display: "flex", gap: 8, marginTop: 10, marginBottom: 10 }}>
        <Button variant="primary" onClick={suggest}>추천</Button>
        <Button variant="primary" onClick={apply} disabled={!result?.candidates?.length}>적용</Button>
      </div>
      <DataTable
        rows={result?.candidates || []}
        empty="영향 후보가 아직 없습니다."
        columns={[
          { key: "affected_item", label: "item", width: 140 },
          { key: "affected_step", label: "step/module", width: 160 },
          { key: "knob", label: "knob", width: 120 },
          { key: "mask", label: "mask", width: 120 },
          { key: "reason", label: "reason" },
        ]}
      />
    </Panel>
  );
}

function KnowledgeIngestAssistant() {
  const [form, setForm] = useState({ title: "", tags: "", doc_type: "internal_knowledge", content: "", file_name: "" });
  const [result, setResult] = useState(null);
  const [list, setList] = useState([]);
  const [msg, setMsg] = useState("");
  const [tableForm, setTableForm] = useState({ title: "Process plan / func_step preview", table_type: "process_plan_func_step", content: DEFAULT_TABLE_CONTENT, apply_instructions: "", target_file: "" });
  const [tableGrid, setTableGrid] = useState(() => parseGridText(DEFAULT_TABLE_CONTENT));
  const [baseFiles, setBaseFiles] = useState([]);
  const [tablePreview, setTablePreview] = useState(null);
  const [tableResult, setTableResult] = useState(null);
  const inputStyle = { ...formControlStyle, width: "100%", boxSizing: "border-box" };

  useEffect(() => {
    sf("/api/agent/admin-tools/knowledge/list").then((d) => setList(d.rows || [])).catch(() => {});
    sf("/api/filebrowser/base-files")
      .then((d) => setBaseFiles((d.files || []).filter((f) => ["csv", "parquet"].includes(String(f.ext || "").toLowerCase()) && ["base_root", "db_root"].includes(f.source))))
      .catch(() => setBaseFiles([]));
  }, []);

  const ingest = () => {
    setMsg("");
    const tags = String(form.tags || "").split(",").map((x) => x.trim()).filter(Boolean);
    postJson("/api/agent/admin-tools/knowledge/ingest", { ...form, tags })
      .then((d) => {
        setResult(d);
        setMsg(`저장됨: ${d.structured?.chunk_count || 0} chunks`);
        sf("/api/agent/admin-tools/knowledge/list").then((x) => setList(x.rows || [])).catch(() => {});
      })
      .catch((e) => setMsg("오류: " + (e.message || e)));
  };
  const readFile = (file) => {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => setForm((f) => ({ ...f, file_name: file.name, title: f.title || file.name, content: String(reader.result || "") }));
    reader.readAsText(file);
  };
  const setGridAndContent = (nextGrid) => {
    setTableGrid(nextGrid);
    setTableForm((f) => ({ ...f, content: gridToCsv(nextGrid.columns, nextGrid.rows) }));
  };
  const updateGridCell = (r, c, value) => {
    const rows = tableGrid.rows.map((row) => row.slice());
    rows[r][c] = value;
    setGridAndContent({ ...tableGrid, rows });
  };
  const updateGridHeader = (c, value) => {
    const columns = tableGrid.columns.slice();
    columns[c] = value;
    setGridAndContent({ ...tableGrid, columns });
  };
  const tablePayload = () => ({
    ...tableForm,
    content: gridToCsv(tableGrid.columns, tableGrid.rows),
    visibility: "private",
    product: "",
    module: "",
    tags: [],
  });
  const previewTableKnowledge = () => {
    setMsg("");
    postJson("/api/semiconductor/knowledge/table/preview", tablePayload())
      .then(setTablePreview)
      .catch((e) => setMsg("오류: " + (e.message || e)));
  };
  const commitTableKnowledge = () => {
    setMsg("");
    postJson("/api/semiconductor/knowledge/table/commit", { ...tablePayload(), apply_to_file: true, preview: tablePreview || {} })
      .then(setTableResult)
      .catch((e) => setMsg("오류: " + (e.message || e)));
  };
  const tableInputStyle = { width: "100%", minWidth: 92, height: 30, border: 0, outline: "none", background: "transparent", color: uxColors.text, fontSize: 14, fontFamily: "monospace", padding: "6px 8px", boxSizing: "border-box" };
  return (
    <div style={{ display: "grid", gap: 12 }}>
      <Panel title="지식 주입" subtitle="제목/태그/타입 + 본문 또는 파일을 1500자±200 chunk로 저장합니다.">
        {msg && <Banner tone={msg.startsWith("오류") ? "bad" : "ok"}>{msg}</Banner>}
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 180px", gap: 8 }}>
          <Field label="제목"><input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} style={inputStyle} /></Field>
          <Field label="타입">
            <select value={form.doc_type} onChange={(e) => setForm({ ...form, doc_type: e.target.value })} style={inputStyle}>
              <option value="internal_knowledge">사내 정보지식</option>
              <option value="process_spec">공정/spec</option>
              <option value="rca_report">RCA 보고서</option>
              <option value="meeting_note">회의/결정사항</option>
              <option value="external_paper">논문/외부자료</option>
            </select>
          </Field>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 220px", gap: 8, marginTop: 8 }}>
          <Field label="태그">
            <input value={form.tags} onChange={(e) => setForm({ ...form, tags: e.target.value })} style={inputStyle} placeholder="DIBL, CA, GAA" />
          </Field>
          <Field label="파일 업로드 (.md/.csv/.pdf)">
            <input type="file" accept=".md,.csv,.pdf,text/markdown,text/csv,application/pdf" onChange={(e) => readFile(e.target.files?.[0])} style={inputStyle} />
          </Field>
        </div>
        <Field label="본문">
          <textarea value={form.content} onChange={(e) => setForm({ ...form, content: e.target.value })} rows={10} style={{ ...inputStyle, resize: "vertical", lineHeight: 1.55 }} />
        </Field>
        <div style={{ display: "flex", gap: 8, marginTop: 10, alignItems: "center" }}>
          <Button variant="primary" onClick={ingest} disabled={!form.content.trim()}>주입</Button>
          {result?.structured && <Pill tone="ok">{result.structured.chunk_count} chunks</Pill>}
        </div>
      </Panel>
      <Panel title="주입 목록" subtitle="agent admin tools로 추가한 promoted_docs 후보">
        <DataTable
          rows={list}
          empty="아직 주입한 지식이 없습니다."
          columns={[
            { key: "created_at", label: "time", width: 128, render: (r) => String(r.created_at || "").replace("T", " ").slice(0, 16) },
            { key: "title", label: "title" },
            { key: "doc_type", label: "type", width: 140 },
            { key: "chunk_count", label: "chunks", width: 80 },
            { key: "tags", label: "tags", render: (r) => listText(r.tags, 4) },
          ]}
        />
      </Panel>
      <Panel title="표 지식 Preview → 확정 반영" subtitle="기존 RCA 입력 table 폼을 admin 지식 주입 영역으로 이동했습니다.">
        <div style={{ display: "grid", gap: 8 }}>
          <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 220px", gap: 8 }}>
            <Field label="제목"><input value={tableForm.title} onChange={(e) => setTableForm({ ...tableForm, title: e.target.value })} style={inputStyle} /></Field>
            <Field label="표 타입">
              <select value={tableForm.table_type} onChange={(e) => setTableForm({ ...tableForm, table_type: e.target.value })} style={inputStyle}>
                <option value="process_plan_func_step">공정 plan → func_step</option>
                <option value="inline_item_semantics">Inline step/item/item_desc</option>
                <option value="teg_coordinate_table">TEG 좌표/layout 표</option>
                <option value="data_cleaning_plan">데이터 클리닝 기준</option>
                <option value="relation_mapping_table">Relation/Table map 기준</option>
              </select>
            </Field>
          </div>
          <Field label="대상 단일파일(schema 기준)">
            <select value={tableForm.target_file} onChange={(e) => setTableForm({ ...tableForm, target_file: e.target.value })} style={inputStyle}>
              <option value="">-- 추가할 CSV/Parquet 선택 --</option>
              {baseFiles.map((f) => <option key={f.path || f.name} value={f.name}>{f.name} · {f.role || f.source}</option>)}
            </select>
          </Field>
          <div
            onPaste={(e) => {
              const text = e.clipboardData?.getData("text/plain") || "";
              if (!text.trim()) return;
              e.preventDefault();
              setGridAndContent(parseGridText(text));
            }}
            style={{ border: "1px solid var(--border)", borderRadius: 6, overflow: "auto", background: "var(--bg-primary)" }}
          >
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
              <thead>
                <tr>
                  <th style={{ width: 40, padding: "6px 8px", borderRight: "1px solid var(--border)", borderBottom: "1px solid var(--border)", color: uxColors.textSub, background: "var(--bg-tertiary)", textAlign: "center" }}>#</th>
                  {tableGrid.columns.map((col, ci) => (
                    <th key={ci} style={{ padding: 0, borderRight: "1px solid var(--border)", borderBottom: "1px solid var(--border)", background: "var(--bg-tertiary)" }}>
                      <input value={col} onChange={(e) => updateGridHeader(ci, e.target.value)} style={{ ...tableInputStyle, fontWeight: 800 }} />
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {tableGrid.rows.map((row, ri) => (
                  <tr key={ri}>
                    <td style={{ width: 40, padding: "6px 8px", textAlign: "center", borderRight: "1px solid var(--border)", borderBottom: "1px solid var(--border)", color: uxColors.textSub, fontFamily: "monospace", background: "var(--bg-secondary)" }}>{ri + 1}</td>
                    {tableGrid.columns.map((_, ci) => (
                      <td key={ci} style={{ padding: 0, borderRight: "1px solid var(--border)", borderBottom: "1px solid var(--border)", background: "var(--bg-primary)" }}>
                        <input value={row[ci] || ""} onChange={(e) => updateGridCell(ri, ci, e.target.value)} style={tableInputStyle} />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            <Button onClick={() => setGridAndContent({ ...tableGrid, rows: [...tableGrid.rows, Array(tableGrid.columns.length).fill("")] })}>+ 행</Button>
            <Button onClick={() => setGridAndContent({ columns: [...tableGrid.columns, `col_${tableGrid.columns.length + 1}`], rows: tableGrid.rows.map((r) => [...r, ""]) })}>+ 열</Button>
          </div>
          <Field label="반영 지시 프롬프트">
            <textarea value={tableForm.apply_instructions} onChange={(e) => setTableForm({ ...tableForm, apply_instructions: e.target.value })} rows={3} style={{ ...inputStyle, resize: "vertical", lineHeight: 1.5 }} />
          </Field>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <Button onClick={previewTableKnowledge} disabled={!tableGrid.columns.length || !tableForm.target_file}>반영 방식 Preview</Button>
            <Button variant="primary" onClick={commitTableKnowledge} disabled={!tablePreview?.target_file_preview?.mapped_row_count}>확인 후 단일파일 반영</Button>
            {tableResult?.ok && <Pill tone="ok">added {tableResult.file_apply?.added || 0}</Pill>}
          </div>
          <DataTable
            rows={tablePreview?.target_file_preview?.column_mapping || []}
            empty="preview 후 컬럼 매핑이 표시됩니다."
            columns={[
              { key: "target_col", label: "target column" },
              { key: "target_dtype", label: "dtype", width: 90 },
              { key: "source_col", label: "input column" },
              { key: "apply_action", label: "action", width: 130 },
              { key: "reason", label: "reason" },
            ]}
          />
        </div>
      </Panel>
    </div>
  );
}

function SchemaRelationSourceFields({ title, value, onChange, files = [] }) {
  const inputStyle = { ...formControlStyle, width: "100%", boxSizing: "border-box", fontSize: 14 };
  const update = (patch) => onChange?.({ ...value, ...patch });
  const isDb = value.source_type === "db";
  return (
    <Panel title={title} subtitle={isDb ? "DB product/root schema" : "root-level single file schema"}>
      <div style={{ display: "grid", gap: 8 }}>
        <div style={{ display: "grid", gridTemplateColumns: "140px minmax(0,1fr)", gap: 8 }}>
          <Field label="source type">
            <select value={value.source_type || "file"} onChange={(e) => update({ source_type: e.target.value })} style={inputStyle}>
              <option value="db">DB product</option>
              <option value="file">단일파일</option>
            </select>
          </Field>
          <Field label="label">
            <input value={value.label || ""} onChange={(e) => update({ label: e.target.value })} style={inputStyle} placeholder="graph label optional" />
          </Field>
        </div>
        <Field label={isDb ? "root (예: 1.RAWDATA_DB_FAB)" : "root"}>
          <input value={value.root || ""} onChange={(e) => update({ root: e.target.value })} style={inputStyle} placeholder={isDb ? "1.RAWDATA_DB_FAB 또는 db_root" : "db_root/base_root 또는 비움"} />
        </Field>
        {isDb ? (
          <Field label="product">
            <input value={value.product || ""} onChange={(e) => update({ product: e.target.value })} style={inputStyle} placeholder="PRODA" />
          </Field>
        ) : (
          <Field label="file">
            <select value={value.file || ""} onChange={(e) => update({ file: e.target.value })} style={inputStyle}>
              <option value="">-- base file 선택 --</option>
              {files.map((file) => (
                <option key={file.path || file.name} value={file.path || file.name}>{file.name || file.path}</option>
              ))}
            </select>
          </Field>
        )}
      </div>
    </Panel>
  );
}

function confidenceColor(cf) {
  const v = Number(cf) || 0;
  if (v >= 0.9) return "var(--ok)";
  if (v >= 0.8) return "var(--info)";
  if (v >= 0.7) return "var(--warn)";
  return "var(--danger)";
}

function SchemaRelationGraph({ candidates = [], onDismiss = null, onConfirm = null, showLegend = true }) {
  const visible = (candidates || []).filter((c) => c && c.left_source_id && c.right_source_id && c.left_column && c.right_column);
  if (!visible.length) {
    return <EmptyState icon="○" title="관계 그래프가 비어 있습니다" hint="Preview 실행 또는 후보 추가가 필요합니다." />;
  }

  const leftMap = new Map();
  const rightMap = new Map();
  for (const c of visible) {
    if (!leftMap.has(c.left_source_id)) leftMap.set(c.left_source_id, { id: c.left_source_id, label: c.left_label || c.left_source_id, source_type: c.left_source_type, cols: [] });
    if (!rightMap.has(c.right_source_id)) rightMap.set(c.right_source_id, { id: c.right_source_id, label: c.right_label || c.right_source_id, source_type: c.right_source_type, cols: [] });
    const L = leftMap.get(c.left_source_id);
    const R = rightMap.get(c.right_source_id);
    if (!L.cols.find((x) => x.name === c.left_column)) L.cols.push({ name: c.left_column, dtype: c.left_dtype });
    if (!R.cols.find((x) => x.name === c.right_column)) R.cols.push({ name: c.right_column, dtype: c.right_dtype });
  }
  const leftSources = Array.from(leftMap.values());
  const rightSources = Array.from(rightMap.values());

  const ROW = 30;
  const HDR = 36;
  const PAD = 12;
  const COL_W = 280;
  const GAP = 200;

  const yOf = new Map();
  let leftCursor = 0;
  for (const src of leftSources) {
    let y = leftCursor + HDR;
    for (const col of src.cols) {
      yOf.set(`L::${src.id}::${col.name}`, y + ROW / 2);
      y += ROW;
    }
    src._height = HDR + src.cols.length * ROW;
    leftCursor = y + PAD;
  }
  let rightCursor = 0;
  for (const src of rightSources) {
    let y = rightCursor + HDR;
    for (const col of src.cols) {
      yOf.set(`R::${src.id}::${col.name}`, y + ROW / 2);
      y += ROW;
    }
    src._height = HDR + src.cols.length * ROW;
    rightCursor = y + PAD;
  }
  const height = Math.max(leftCursor, rightCursor, 220);
  const totalW = COL_W * 2 + GAP;

  const renderSourceBox = (src, side) => (
    <div key={src.id} style={{ width: COL_W, marginBottom: PAD, border: "1px solid var(--border)", borderRadius: 8, overflow: "hidden", background: "var(--bg-secondary)" }}>
      <div style={{ height: HDR, display: "flex", alignItems: "center", gap: 8, padding: "0 10px", background: "var(--bg-tertiary)", borderBottom: "1px solid var(--border)" }}>
        <Pill tone={String(src.source_type || "").toLowerCase() === "db" ? "accent" : "info"}>{src.source_type || "src"}</Pill>
        <span style={{ fontSize: 13, fontWeight: 800, color: uxColors.text, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={src.label}>{src.label}</span>
        <span style={{ marginLeft: "auto", fontSize: 11, color: uxColors.textSub, fontFamily: "monospace" }}>{src.cols.length} col</span>
      </div>
      {src.cols.map((col, ci) => (
        <div key={col.name} style={{ height: ROW, display: "flex", alignItems: "center", justifyContent: side === "left" ? "space-between" : "flex-start", gap: 8, padding: "0 10px", borderTop: ci ? "1px solid var(--border)" : undefined, background: "var(--bg-primary)" }}>
          {side === "right" && <span style={{ width: 8, height: 8, borderRadius: 999, background: uxColors.accent, flexShrink: 0 }} />}
          <span style={{ fontSize: 13, fontFamily: "monospace", color: uxColors.text, fontWeight: 700, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={col.name}>{col.name}</span>
          <span style={{ fontSize: 11, color: uxColors.textSub, fontFamily: "monospace" }}>{col.dtype || ""}</span>
          {side === "left" && <span style={{ width: 8, height: 8, borderRadius: 999, background: uxColors.accent, flexShrink: 0 }} />}
        </div>
      ))}
    </div>
  );

  return (
    <div style={{ display: "grid", gap: 8 }}>
      {showLegend && (
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center", fontSize: 12, color: uxColors.textSub }}>
          <span>왼쪽 컬럼을 기준으로 오른쪽 컬럼이 연결됩니다. 선 색은 신뢰도, 가운데 점수, 점수 옆 ✕ 는 후보에서 제거.</span>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}><span style={{ width: 16, height: 3, background: "var(--ok)", borderRadius: 2 }} />≥0.9</span>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}><span style={{ width: 16, height: 3, background: "var(--info)", borderRadius: 2 }} />≥0.8</span>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}><span style={{ width: 16, height: 3, background: "var(--warn)", borderRadius: 2 }} />≥0.7</span>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}><span style={{ width: 16, height: 3, background: "var(--danger)", borderRadius: 2 }} />&lt;0.7</span>
        </div>
      )}
      <div style={{ overflowX: "auto" }}>
        <div style={{ position: "relative", width: totalW, minHeight: height + 8, paddingTop: 4 }}>
          <div style={{ position: "absolute", left: 0, top: 4, width: COL_W, display: "flex", flexDirection: "column" }}>
            {leftSources.map((src) => renderSourceBox(src, "left"))}
          </div>
          <svg style={{ position: "absolute", left: COL_W, top: 4, width: GAP, height, overflow: "visible" }} viewBox={`0 0 ${GAP} ${height}`}>
            {visible.map((c) => {
              const yL = yOf.get(`L::${c.left_source_id}::${c.left_column}`);
              const yR = yOf.get(`R::${c.right_source_id}::${c.right_column}`);
              if (yL == null || yR == null) return null;
              const color = confidenceColor(c.confidence || 0);
              const mid = GAP / 2;
              const labelX = mid;
              const labelY = (yL + yR) / 2;
              return (
                <g key={c.relation_id}>
                  <path d={`M 0 ${yL} C ${mid} ${yL}, ${mid} ${yR}, ${GAP} ${yR}`} stroke={color} fill="none" strokeWidth={Math.max(1.5, 1 + (Number(c.confidence) || 0.7) * 1.6)} opacity="0.92" />
                  <g>
                    <rect x={labelX - 24} y={labelY - 9} width={48} height={18} rx={4} fill="var(--bg-primary)" stroke={color} strokeWidth="1" />
                    <text x={labelX} y={labelY + 4} textAnchor="middle" fontSize="11" fontWeight="700" fill={color}>{(Number(c.confidence) || 0).toFixed(2)}</text>
                  </g>
                  {onConfirm && c.status !== "confirmed" && (
                    <g style={{ cursor: "pointer" }} onClick={() => onConfirm(c)}>
                      <circle cx={labelX - 38} cy={labelY} r={9} fill="var(--bg-primary)" stroke="var(--ok)" strokeWidth="1" />
                      <text x={labelX - 38} y={labelY + 4} textAnchor="middle" fontSize="11" fontWeight="800" fill="var(--ok)">✓</text>
                    </g>
                  )}
                  {onDismiss && (
                    <g style={{ cursor: "pointer" }} onClick={() => onDismiss(c)}>
                      <circle cx={labelX + 38} cy={labelY} r={9} fill="var(--bg-primary)" stroke="var(--danger)" strokeWidth="1" />
                      <text x={labelX + 38} y={labelY + 4} textAnchor="middle" fontSize="12" fontWeight="800" fill="var(--danger)">×</text>
                    </g>
                  )}
                </g>
              );
            })}
          </svg>
          <div style={{ position: "absolute", left: COL_W + GAP, top: 4, width: COL_W, display: "flex", flexDirection: "column" }}>
            {rightSources.map((src) => renderSourceBox(src, "right"))}
          </div>
        </div>
      </div>
    </div>
  );
}

function relationCandidateRationale(row) {
  const text = String(row?.rationale || "").trim();
  if (text) return text;
  const evidence = Array.isArray(row?.evidence) ? row.evidence.map(String).filter(Boolean) : [];
  if (evidence.length) return `근거: ${evidence.slice(0, 3).join(", ")}`;
  return "컬럼명, alias, dtype 기반 자동 후보입니다.";
}

function ConfidenceBar({ value }) {
  const score = Math.max(0, Math.min(1, Number(value) || 0));
  return (
    <div style={{ display: "grid", gap: 4, minWidth: 90 }}>
      <div style={{ height: 7, borderRadius: 999, background: "var(--bg-primary)", border: "1px solid var(--border)", overflow: "hidden" }}>
        <div style={{ width: `${score * 100}%`, height: "100%", background: confidenceColor(score) }} />
      </div>
      <span style={{ fontFamily: "monospace", color: uxColors.textSub }}>{score.toFixed(2)}</span>
    </div>
  );
}

function SchemaRelationsPanel({ canManage }) {
  const [left, setLeft] = useState({ source_type: "db", root: "1.RAWDATA_DB_FAB", product: "PRODA", file: "", label: "FAB PRODA" });
  const [right, setRight] = useState({ source_type: "file", root: "", product: "", file: "ML_TABLE_PRODA.parquet", label: "ML_TABLE PRODA" });
  const [baseFiles, setBaseFiles] = useState([]);
  const [preview, setPreview] = useState(null);
  const [graph, setGraph] = useState(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [dismissedIds, setDismissedIds] = useState(new Set());
  const [checkedIds, setCheckedIds] = useState(new Set());
  const [showAlternatives, setShowAlternatives] = useState(false);
  const inputStyle = { ...formControlStyle, width: "100%", boxSizing: "border-box", fontSize: 14 };
  const canEditRelations = !!canManage;

  const loadGraph = () => {
    sf("/api/agent/schema-relations/graph")
      .then(setGraph)
      .catch((e) => setMsg("graph 로드 오류: " + (e.message || e)));
  };

  useEffect(() => {
    sf("/api/filebrowser/base-files")
      .then((d) => setBaseFiles((d.files || []).filter((file) => ["csv", "parquet"].includes(String(file.ext || "").toLowerCase()))))
      .catch(() => setBaseFiles([]));
    loadGraph();
  }, []);

  const allCandidates = Array.isArray(preview?.candidates) ? preview.candidates : [];
  const sourceRows = Array.isArray(preview?.sources) ? preview.sources : [];

  const primaryCandidates = useMemo(() => {
    const best = new Map();
    for (const c of allCandidates) {
      if (!c?.left_source_id || !c?.left_column) continue;
      const key = `${c.left_source_id}::${c.left_column}`;
      const prev = best.get(key);
      if (!prev || (Number(c.confidence) || 0) > (Number(prev.confidence) || 0)) {
        best.set(key, c);
      }
    }
    return Array.from(best.values()).sort((a, b) => (Number(b.confidence) || 0) - (Number(a.confidence) || 0));
  }, [allCandidates]);

  const filteredCandidates = useMemo(() => {
    const source = showAlternatives ? allCandidates : primaryCandidates;
    return source.filter((c) => !dismissedIds.has(c.relation_id));
  }, [allCandidates, primaryCandidates, showAlternatives, dismissedIds]);

  const alternativeCount = Math.max(0, allCandidates.length - primaryCandidates.length);
  const checkedCandidates = useMemo(() => {
    return filteredCandidates.filter((c) => checkedIds.has(c.relation_id));
  }, [filteredCandidates, checkedIds]);

  const setDefaultChecked = (candidates, mode = "preview") => {
    const rows = Array.isArray(candidates) ? candidates : [];
    setCheckedIds(new Set(rows.filter((c) => mode !== "scan" || (Number(c.confidence) || 0) >= 0.85).map((c) => c.relation_id).filter(Boolean)));
  };

  const toggleCandidateChecked = (candidate) => {
    const id = candidate?.relation_id;
    if (!id) return;
    setCheckedIds((cur) => {
      const next = new Set(cur);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const dismissCandidate = (candidate) => {
    if (!candidate?.relation_id) return;
    setDismissedIds((cur) => {
      const next = new Set(cur);
      next.add(candidate.relation_id);
      return next;
    });
    setCheckedIds((cur) => {
      const next = new Set(cur);
      next.delete(candidate.relation_id);
      return next;
    });
  };

  const restoreDismissed = () => setDismissedIds(new Set());

  const updateCandidate = (relationId, patch) => {
    if (!relationId) return;
    setPreview((cur) => {
      const candidates = (cur?.candidates || []).map((row) => (
        row.relation_id === relationId ? { ...row, ...patch, status: "edited" } : row
      ));
      return { ...(cur || {}), candidates };
    });
  };

  const runPreview = () => {
    setBusy(true);
    setMsg("");
    postJson("/api/agent/schema-relations/preview", { sources: [left, right], max_candidates: 40, sample_rows: 20 })
      .then((d) => {
        const candidates = [...(d.candidates || [])].sort((a, b) => (Number(b.confidence) || 0) - (Number(a.confidence) || 0));
        setPreview({ ...d, candidates });
        setDismissedIds(new Set());
        setDefaultChecked(candidates, "preview");
        setMsg(`preview 후보 ${d.candidates?.length || 0}개 (왼쪽 컬럼 기준 ${(new Set((d.candidates || []).map((c) => `${c.left_source_id}::${c.left_column}`))).size}개)`);
      })
      .catch((e) => setMsg("preview 오류: " + (e.message || e)))
      .finally(() => setBusy(false));
  };

  const runScan = () => {
    setBusy(true);
    setMsg("");
    postJson("/api/agent/schema-relations/scan", { max_sources: 32, max_candidates: 100, sample_rows: 20 })
      .then((d) => {
        const candidates = [...(d.candidates || [])].sort((a, b) => (Number(b.confidence) || 0) - (Number(a.confidence) || 0));
        setPreview({ ...d, candidates });
        setDismissedIds(new Set());
        setDefaultChecked(candidates, "scan");
        setMsg(`전체 스캔: source ${d.discovered_count || d.sources?.length || 0}개, relation 후보 ${d.candidates?.length || 0}개`);
      })
      .catch((e) => setMsg("scan 오류: " + (e.message || e)))
      .finally(() => setBusy(false));
  };

  const saveCandidates = () => {
    if (!canEditRelations || !checkedCandidates.length) return;
    setBusy(true);
    setMsg("");
    postJson("/api/agent/schema-relations/save", { candidates: checkedCandidates, note })
      .then((d) => {
        setGraph(d);
        setMsg(`저장됨: ${d.saved_count || 0} relation`);
      })
      .catch((e) => setMsg("저장 오류: " + (e.message || e)))
      .finally(() => setBusy(false));
  };

  const deleteRelation = (edge) => {
    const relationId = edge?.relation_id || edge?.id;
    if (!canEditRelations || !relationId) return;
    if (!confirm("저장된 relation 정의만 삭제합니다. 원본 DB/단일파일은 수정하지 않습니다. 계속할까요?")) return;
    setBusy(true);
    setMsg("");
    postJson("/api/agent/schema-relations/delete", { relation_ids: [relationId], note })
      .then((d) => {
        setGraph(d);
        setMsg(`삭제됨: ${d.deleted_count || 0} relation`);
      })
      .catch((e) => setMsg("삭제 오류: " + (e.message || e)))
      .finally(() => setBusy(false));
  };

  const savedRelations = Array.isArray(graph?.relations) ? graph.relations : [];

  return (
    <div style={{ display: "grid", gap: 12 }}>
      {msg && <Banner tone={msg.includes("오류") ? "bad" : msg.includes("저장") || msg.includes("삭제") ? "ok" : "info"}>{msg}</Banner>}
      <Panel
        title="자동 스캔"
        subtitle="DB product와 단일 파일 schema를 읽어 join key 후보를 찾습니다. confidence 0.85 이상 후보만 기본 체크합니다."
        right={<Pill tone="accent">{checkedCandidates.length} checked</Pill>}
      >
        <div style={{ display: "flex", gap: 8, alignItems: "end", flexWrap: "wrap" }}>
          <Button variant="primary" onClick={runScan} disabled={busy}>{busy ? "스캔 중" : "자동 스캔"}</Button>
          <Field label="save note">
            <input value={note} onChange={(e) => setNote(e.target.value)} style={{ ...inputStyle, minWidth: 260 }} placeholder="저장 근거/확인 메모" />
          </Field>
          <Button variant="primary" onClick={saveCandidates} disabled={!canEditRelations || busy || !checkedCandidates.length}>체크 후보 저장</Button>
          <Pill tone="info">원본 DB/파일 read-only</Pill>
          {!canEditRelations && <Pill tone="neutral">admin / 위임 권한만 저장</Pill>}
        </div>
      </Panel>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)", gap: 12, alignItems: "start" }}>
        <SchemaRelationSourceFields title="Source A (왼쪽 기준)" value={left} onChange={setLeft} files={baseFiles} />
        <SchemaRelationSourceFields title="Source B (오른쪽 매칭)" value={right} onChange={setRight} files={baseFiles} />
      </div>

      <Panel
        title="Relation Preview"
        subtitle="왼쪽 컬럼을 기준으로 오른쪽에서 가장 점수가 높은 매칭만 남깁니다. ✕ 로 후보를 제거하고, ‘대체 후보 보기’ 로 같은 왼쪽 컬럼의 다른 매칭을 검토할 수 있습니다."
        right={<Pill tone="accent">{checkedCandidates.length} checked · {filteredCandidates.length} / {allCandidates.length}</Pill>}
      >
        <div style={{ display: "flex", gap: 8, alignItems: "end", flexWrap: "wrap", marginBottom: 10 }}>
          <Button variant="primary" onClick={runPreview} disabled={busy}>{busy ? "처리 중" : "Preview 생성"}</Button>
          <Field label="save note">
            <input value={note} onChange={(e) => setNote(e.target.value)} style={{ ...inputStyle, minWidth: 260 }} placeholder="저장 근거/확인 메모" />
          </Field>
          <Button variant="primary" onClick={saveCandidates} disabled={!canEditRelations || busy || !checkedCandidates.length}>체크 후보 저장</Button>
          <Button variant={showAlternatives ? "primary" : "subtle"} onClick={() => setShowAlternatives((v) => !v)} disabled={!alternativeCount}>
            {showAlternatives ? "대표 매칭만 보기" : `대체 후보 보기${alternativeCount ? ` (+${alternativeCount})` : ""}`}
          </Button>
          {dismissedIds.size > 0 && <Button variant="subtle" onClick={restoreDismissed}>제거 취소 ({dismissedIds.size})</Button>}
          <Pill tone="info">원본 DB/파일 read-only</Pill>
          {!canEditRelations && <Pill tone="neutral">admin / 위임 권한만 저장</Pill>}
        </div>
        <SchemaRelationGraph candidates={filteredCandidates} onDismiss={canEditRelations ? dismissCandidate : null} />
      </Panel>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,0.85fr) minmax(0,1.15fr)", gap: 12, alignItems: "start" }}>
        <Panel title="Schema Sources" subtitle="Preview에 사용한 schema와 key 후보">
          <DataTable
            rows={sourceRows}
            empty="schema source 없음"
            columns={[
              { key: "label", label: "label" },
              { key: "source_type", label: "type", width: 80 },
              { key: "columns", label: "cols", width: 70, render: (r) => (r.columns || []).length },
              { key: "key_columns", label: "key columns", render: (r) => listText(r.key_columns, 5) },
            ]}
          />
        </Panel>
        <Panel
          title="후보 표"
          subtitle="graph 의 동일 후보를 표로 다시 확인합니다. ✕ 으로 후보를 제거할 수 있습니다."
        >
          <DataTable
            rows={filteredCandidates}
            empty="Preview를 실행하면 관계 후보가 표시됩니다."
            columns={[
              { key: "checked", label: "", width: 54, render: (r) => <input type="checkbox" checked={checkedIds.has(r.relation_id)} onChange={() => toggleCandidateChecked(r)} disabled={!canEditRelations} /> },
              { key: "left_label", label: "left source", width: 130 },
              { key: "left_column", label: "left column", width: 160, render: (r) => canEditRelations ? <input value={r.left_column || ""} onChange={(e) => updateCandidate(r.relation_id, { left_column: e.target.value })} style={{ ...inputStyle, minWidth: 130 }} /> : (r.left_column || "") },
              { key: "right_label", label: "right source", width: 130 },
              { key: "right_column", label: "right column", width: 160, render: (r) => canEditRelations ? <input value={r.right_column || ""} onChange={(e) => updateCandidate(r.relation_id, { right_column: e.target.value })} style={{ ...inputStyle, minWidth: 130 }} /> : (r.right_column || "") },
              { key: "canonical_key", label: "key", width: 130, render: (r) => canEditRelations ? <input value={r.canonical_key || ""} onChange={(e) => updateCandidate(r.relation_id, { canonical_key: e.target.value })} style={{ ...inputStyle, minWidth: 100 }} /> : (r.canonical_key || "") },
              { key: "confidence", label: "score", width: 120, render: (r) => canEditRelations ? <div style={{ display: "grid", gap: 5 }}><input type="number" min="0" max="1" step="0.01" value={r.confidence ?? 0} onChange={(e) => updateCandidate(r.relation_id, { confidence: Number(e.target.value) })} style={{ ...inputStyle, minWidth: 78 }} /><ConfidenceBar value={r.confidence} /></div> : <ConfidenceBar value={r.confidence} /> },
              { key: "rationale", label: "rationale", render: (r) => relationCandidateRationale(r) },
              { key: "left_dtype", label: "left dtype", width: 90 },
              { key: "right_dtype", label: "right dtype", width: 90 },
              { key: "evidence", label: "evidence", render: (r) => listText(r.evidence, 3) },
              ...(canEditRelations ? [{ key: "dismiss", label: "", width: 70, render: (r) => <Button variant="subtle" onClick={() => dismissCandidate(r)} title="후보에서 제거">✕</Button> }] : []),
            ]}
          />
        </Panel>
      </div>

      <Panel
        title="Saved Relation Graph"
        subtitle="엔지니어가 확인 저장한 relation만 표시합니다. graph 의 ✕ 또는 표의 삭제 버튼으로 정의를 제거할 수 있고, 원본 DB/단일파일은 건드리지 않습니다."
        right={<Pill tone="accent">{savedRelations.length} saved</Pill>}
      >
        <SchemaRelationGraph candidates={savedRelations} onDismiss={canEditRelations ? deleteRelation : null} showLegend={false} />
        {canEditRelations && savedRelations.length > 0 && (
          <div style={{ marginTop: 10 }}>
            <DataTable
              rows={savedRelations}
              empty=""
              columns={[
                { key: "left_label", label: "left source", width: 160 },
                { key: "left_column", label: "left column", width: 140 },
                { key: "right_label", label: "right source", width: 160 },
                { key: "right_column", label: "right column", width: 140 },
                { key: "confidence", label: "score", width: 70, render: (r) => (Number(r.confidence) || 0).toFixed(2) },
                { key: "delete", label: "", width: 80, render: (r) => <Button variant="danger" onClick={() => deleteRelation(r)}>삭제</Button> },
              ]}
            />
          </div>
        )}
      </Panel>
    </div>
  );
}

function compactToolPayload(tool = {}) {
  const keep = {};
  ["action", "intent", "feature", "arguments", "filters", "slots", "missing", "validation", "clarification", "requires_confirmation", "blocked", "reject_reason"].forEach((key) => {
    if (tool && tool[key] !== undefined && tool[key] !== null && tool[key] !== "" && !(Array.isArray(tool[key]) && !tool[key].length)) keep[key] = tool[key];
  });
  return keep;
}

function flowiOutputRows(result = {}) {
  const tool = result?.tool || {};
  const workflow = result?.workflow_state || tool.workflow_state || {};
  const table = tool.table || tool.samples_table || tool.stats_table || null;
  const chart = tool.chart_result || tool.chart || null;
  const rows = [
    { key: "workflow_state", value: workflow.status || "-" },
    { key: "waiting_for", value: workflow.waiting_for || "-" },
    { key: "answer", value: result.answer || tool.answer || "-" },
  ];
  if (table) rows.push({ key: "table", value: `${table.kind || table.title || "table"} · ${table.total ?? table.rows?.length ?? 0} rows` });
  if (chart) rows.push({ key: "chart", value: chart.title || chart.kind || chart.status || "chart result" });
  if (Array.isArray(result.next_actions) && result.next_actions.length) rows.push({ key: "next_actions", value: listText(result.next_actions.map((x) => x.title || x.id), 4) });
  if (tool.draft_id || tool.session_id) rows.push({ key: "draft/session", value: tool.draft_id || tool.session_id });
  return rows;
}

const CALL_NODE_TONE = {
  input: { border: "var(--muted)", bg: "var(--bg-tertiary)" },
  fastapi: { border: "#2563eb", bg: "rgba(37,99,235,.12)" },
  orchestrator: { border: "#7c3aed", bg: "rgba(124,58,237,.12)" },
  guardrail: { border: "#ca8a04", bg: "rgba(202,138,4,.12)" },
  feature_subagent: { border: "#0891b2", bg: "rgba(8,145,178,.12)" },
  api_call: { border: "var(--ok)", bg: "var(--ok-50)" },
  result: { border: "#0f766e", bg: "rgba(15,118,110,.12)" },
  answer: { border: "#4b5563", bg: "rgba(75,85,99,.12)" },
};
const ORCHESTRATOR_PREVIEW_ENDPOINT = "/api/llm/flowi/orchestrator/preview";

function callStatusTone(status) {
  if (status === "done") return "ok";
  if (status === "blocked" || status === "error") return "bad";
  if (status === "waiting") return "warn";
  return "neutral";
}

function FlowiCallGraph({ trace }) {
  const graph = trace?.call_graph || {};
  const nodes = Array.isArray(graph.nodes) ? graph.nodes : [];
  const edges = Array.isArray(graph.edges) ? graph.edges : [];
  if (!nodes.length) return <EmptyState title="호출 그래프 없음" hint="실행 후 FastAPI와 기능 핸들러 흐름이 표시됩니다." />;
  const edgeFrom = (id) => edges.find((edge) => edge.source === id);
  return (
    <div style={{ display: "grid", gap: 10 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "stretch", overflowX: "auto", paddingBottom: 4 }}>
        {nodes.map((node, idx) => {
          const tone = CALL_NODE_TONE[node.type] || CALL_NODE_TONE.result;
          const edge = edgeFrom(node.id);
          const isLast = idx === nodes.length - 1;
          return (
            <div key={node.id || idx} style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 190 }}>
              <div style={{ minWidth: 190, maxWidth: 230, minHeight: 122, border: `1px solid ${tone.border}`, borderRadius: 8, background: tone.bg, padding: 10, display: "grid", gap: 8, alignContent: "start" }}>
                <div style={{ display: "flex", gap: 8, alignItems: "center", justifyContent: "space-between" }}>
                  <span style={{ fontSize: 12, color: uxColors.textSub, fontFamily: "monospace" }}>{String(idx + 1).padStart(2, "0")} · {node.type || "node"}</span>
                  <Pill tone={callStatusTone(node.status)}>{node.status || "-"}</Pill>
                </div>
                <div style={{ fontSize: 14, fontWeight: 900, color: uxColors.text, lineHeight: 1.3 }}>{node.title || node.id}</div>
                <div style={{ fontSize: 12, color: uxColors.textSub, lineHeight: 1.45, wordBreak: "break-word" }}>{node.detail || "-"}</div>
              </div>
              {!isLast && (
                <div style={{ display: "grid", justifyItems: "center", alignContent: "center", gap: 4, minWidth: 48 }}>
                  <span style={{ color: uxColors.textSub, fontFamily: "monospace", fontSize: 18 }}>→</span>
                  <span style={{ color: uxColors.textSub, fontSize: 11, maxWidth: 68, textAlign: "center", overflowWrap: "anywhere" }}>{edge?.label || ""}</span>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function flowiApiCallRows(trace) {
  const calls = Array.isArray(trace?.api_calls) ? trace.api_calls : [];
  return calls.map((call, idx) => ({
    no: idx + 1,
    stage: call.stage || "-",
    method: call.method || "-",
    target: call.path || call.callee || call.name || "-",
    callee: call.callee || "-",
    purpose: call.purpose || "-",
    output: call.output || "-",
    status: call.status || "-",
    payload: compactJsonText(call.payload || {}, 160),
  }));
}

function flowiActivation(trace, result, prompt) {
  const graph = trace?.call_graph || {};
  const activation = trace?.activation || graph.activation || {};
  const tool = result?.tool || {};
  const workflow = result?.workflow_state || tool.workflow_state || {};
  const calls = Array.isArray(trace?.api_calls) ? trace.api_calls : [];
  const featureCall = calls.find((call) => call.stage === "feature_api") || {};
  const missing = Array.isArray(activation.missing) ? activation.missing
    : Array.isArray(tool.missing) ? tool.missing
    : Array.isArray(tool.validation?.missing) ? tool.validation.missing
    : [];
  return {
    prompt: activation.prompt || prompt || "-",
    endpoint: activation.endpoint || calls[0]?.path || "/api/llm/flowi/agent/chat",
    intent: activation.intent || tool.intent || workflow.intent || "-",
    feature: activation.feature || tool.feature || workflow.feature || "-",
    action: activation.action || tool.unit_action || workflow.unit_action || tool.action || workflow.action || tool.intent || "-",
    api: activation.api || featureCall.path || featureCall.callee || "-",
    handler: activation.handler || featureCall.callee || tool.action || "-",
    status: activation.status || workflow.status || "-",
    output: activation.output || result?.answer || tool.answer || "-",
    nextAction: activation.next_action || (Array.isArray(result?.next_actions) && result.next_actions[0]?.title) || "-",
    missing,
    cause: activation.cause || tool.reject_reason || (missing.length ? "필수 slot 부족" : ""),
  };
}

function FlowiActivationMap({ trace, result, prompt }) {
  const active = flowiActivation(trace, result, prompt);
  const hasResult = Boolean(result);
  const cards = [
    {
      step: "01",
      title: "에이전트가 받은 prompt",
      value: active.prompt,
      meta: active.endpoint,
      type: "input",
      status: hasResult ? "done" : "waiting",
    },
    {
      step: "02",
      title: "오케스트레이터 판단",
      value: `intent=${active.intent}`,
      meta: `feature=${active.feature} / action=${active.action}`,
      type: "orchestrator",
      status: active.status,
    },
    {
      step: "03",
      title: "활성화된 기능",
      value: active.feature,
      meta: active.handler || active.api,
      type: "feature_subagent",
      status: active.status,
    },
    {
      step: "04",
      title: "호출된 API / handler",
      value: active.api,
      meta: active.output,
      type: "api_call",
      status: active.status,
    },
    {
      step: "05",
      title: "호출 결과",
      value: active.output,
      meta: `next=${active.nextAction || "-"}${active.missing.length ? ` / missing=${active.missing.join(", ")}` : ""}`,
      type: "result",
      status: active.status,
    },
  ];
  const showIssue = ["needs_input", "awaiting_confirmation", "blocked", "error", "waiting"].includes(String(active.status || ""));
  return (
    <div style={{ display: "grid", gap: 10 }}>
      {showIssue && (
        <Banner tone={active.status === "blocked" || active.status === "error" ? "bad" : "warn"}>
          {active.cause || active.status} {active.missing.length ? `· missing: ${active.missing.join(", ")}` : ""}
        </Banner>
      )}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 10 }}>
        {cards.map((card, idx) => {
          const tone = CALL_NODE_TONE[card.type] || CALL_NODE_TONE.result;
          return (
            <div key={card.step} style={{ border: `1px solid ${tone.border}`, background: tone.bg, borderRadius: 8, padding: 12, minHeight: 136, display: "grid", gap: 8, alignContent: "start" }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}>
                <span style={{ fontSize: 12, color: uxColors.textSub, fontFamily: "monospace" }}>{card.step}</span>
                <Pill tone={callStatusTone(card.status)}>{card.status || "-"}</Pill>
              </div>
              <div style={{ fontSize: 13, color: uxColors.textSub, fontWeight: 800 }}>{card.title}</div>
              <div style={{ fontSize: idx === 0 ? 14 : 16, color: uxColors.text, fontWeight: 900, lineHeight: 1.4, wordBreak: "break-word" }}>{card.value || "-"}</div>
              <div style={{ fontSize: 12, color: uxColors.textSub, lineHeight: 1.45, wordBreak: "break-word" }}>{card.meta || "-"}</div>
            </div>
          );
        })}
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", color: uxColors.textSub, fontSize: 13 }}>
        <span style={{ fontFamily: "monospace" }}>prompt</span>
        <span>→</span>
        <span style={{ fontFamily: "monospace" }}>orchestrator</span>
        <span>→</span>
        <span style={{ fontFamily: "monospace" }}>{active.feature}</span>
        <span>→</span>
        <span style={{ fontFamily: "monospace" }}>{active.action}</span>
      </div>
    </div>
  );
}

function FlowiPublicContext({ trace }) {
  const persona = trace?.persona_snapshot || {};
  const promptCache = trace?.prompt_cache || {};
  const subagent = trace?.subagent_context || {};
  const loop = trace?.clarification_loop || {};
  const rows = [
    { section: "persona", key: "role", value: persona.role || "-" },
    { section: "persona", key: "prompt_source", value: persona.prompt_source || "-" },
    { section: "orchestrator", key: "preview_endpoint", value: ORCHESTRATOR_PREVIEW_ENDPOINT },
    { section: "prompt_cache", key: "allowed_features", value: listText(promptCache.allowed_features, 8) },
    { section: "prompt_cache", key: "feature_docs", value: listText(promptCache.feature_docs, 5) },
    { section: "subagent", key: "feature_subagent", value: subagent.feature_subagent || "-" },
    { section: "subagent", key: "unit_action", value: subagent.unit_action || "-" },
    { section: "subagent", key: "api_or_handler", value: subagent.api_or_handler || "-" },
  ];
  const choiceRows = Array.isArray(loop.choices) ? loop.choices.map((choice, idx) => ({ no: idx + 1, ...choice })) : [];
  return (
    <div style={{ display: "grid", gap: 12 }}>
      <Panel title="Persona / Prompt Cache / Subagent Context" subtitle="서버가 공개해도 되는 system/persona cache와 deterministic handler 요약입니다.">
        <DataTable
          rows={rows}
          columns={[
            { key: "section", label: "section", width: 130 },
            { key: "key", label: "key", width: 170 },
            { key: "value", label: "value" },
          ]}
        />
      </Panel>
      <Panel
        title="재질문 / Missing Slot Loop"
        subtitle="needs_input 상태에서 question, missing, choices, next unit action, pending prompt를 표시합니다."
        right={<Pill tone={loop.needs_input ? "warn" : "neutral"}>{loop.status || "ready"}</Pill>}
      >
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0,0.85fr) minmax(0,1.15fr)", gap: 12, alignItems: "start" }}>
          <DataTable
            rows={[
              { key: "question", value: loop.question || "-" },
              { key: "missing", value: listText(loop.missing, 8) },
              { key: "next_unit_action", value: loop.next_unit_action || "-" },
              { key: "pending_prompt", value: loop.pending_prompt || "-" },
            ]}
            columns={[
              { key: "key", label: "field", width: 150 },
              { key: "value", label: "value" },
            ]}
          />
          <DataTable
            rows={choiceRows}
            empty="선택지가 필요한 상태가 아닙니다."
            columns={[
              { key: "no", label: "#", width: 44 },
              { key: "label", label: "label", width: 100 },
              { key: "title", label: "title" },
              { key: "prompt", label: "prompt" },
              { key: "recommended", label: "rec", width: 70, render: (r) => r.recommended ? "yes" : "" },
            ]}
          />
        </div>
      </Panel>
    </div>
  );
}

function activationStatusTone(status) {
  if (status === "ready" || status === "done") return "ok";
  if (status === "needs_input" || status === "awaiting_confirmation" || status === "waiting") return "warn";
  if (status === "error" || status === "blocked") return "bad";
  return "neutral";
}

function historyStatusTone(status) {
  const s = String(status || "").toLowerCase();
  if (s === "ready" || s === "done" || s === "completed") return "ok";
  if (s === "needs_input" || s === "awaiting_confirmation" || s === "waiting") return "warn";
  if (s === "blocked" || s === "error") return "bad";
  return "neutral";
}

function truncateText(value, limit) {
  const text = String(value || "");
  if (!text) return "-";
  return text.length > limit ? text.slice(0, limit) + "…" : text;
}

function PromptHistoryPanel({ entries = [], onSelect, onRerun, activePrompt, busy = false }) {
  const rows = entries.map((entry, idx) => ({
    ...entry,
    no: idx + 1,
    timeShort: String(entry.ts || entry.timestamp || "").replace("T", " ").slice(5, 16),
    promptShort: truncateText(entry.prompt, 110),
    answerShort: truncateText(entry.answer || entry.answer_excerpt || entry.error, 90),
    missingText: entry.missing && entry.missing.length ? entry.missing.join(", ") : "-",
  }));
  return (
    <Panel
      title="프롬프트 기록"
      subtitle="flow-data에 저장된 실행 프롬프트와 결과를 최신순으로 보여줍니다. 행을 클릭하면 입력창에 채워지고, 재실행 버튼으로 같은 prompt 를 다시 보낼 수 있습니다."
      right={<Pill tone={busy ? "warn" : "accent"}>{busy ? "실행 중" : `${entries.length} 건`}</Pill>}
    >
      <DataTable
        rows={rows}
        empty="아직 실행한 프롬프트가 없습니다. 위쪽 입력창에서 prompt 를 보내 보세요."
        onRowClick={(row) => onSelect?.(row.prompt)}
        rowStyle={(row) => row.prompt === activePrompt ? { background: "var(--accent-glow)" } : undefined}
        maxHeight={360}
        columns={[
          { key: "no", label: "#", width: 44 },
          { key: "timeShort", label: "time", width: 110 },
          { key: "promptShort", label: "prompt" },
          { key: "feature", label: "feature", width: 130, render: (r) => <Pill tone="accent">{r.feature || "-"}</Pill> },
          { key: "action", label: "action", width: 180 },
          { key: "status", label: "status", width: 120, render: (r) => <Pill tone={historyStatusTone(r.status)}>{r.status || "-"}</Pill> },
          { key: "missingText", label: "missing", width: 140 },
          { key: "answerShort", label: "answer / error" },
          { key: "rerun", label: "", width: 88, render: (r) => <Button variant="subtle" onClick={(e) => { e?.stopPropagation?.(); onRerun?.(r.prompt); }}>재실행</Button> },
        ]}
      />
    </Panel>
  );
}

function FlowiOrchestratorPreview({ rows = [], busy = false, onSelectPrompt }) {
  const featureCounts = rows.reduce((acc, row) => {
    const key = row.feature || "general";
    acc[key] = (acc[key] || 0) + 1;
    return acc;
  }, {});
  return (
    <Panel
      title="프롬프트별 오케스트레이터 활성화"
      subtitle="예시 prompt/실행 prompt가 어떤 기능 subagent와 action을 켜는지 dry-run으로 비교합니다."
      right={<Pill tone={busy ? "warn" : "accent"}>{busy ? "previewing" : `${rows.length} prompts`}</Pill>}
    >
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 10 }}>
        {Object.entries(featureCounts).map(([featureName, count]) => (
          <Pill key={featureName} tone="info">{featureName} {count}</Pill>
        ))}
        {!rows.length && <Pill tone="neutral">no preview</Pill>}
      </div>
      <DataTable
        rows={rows.map((row, idx) => ({ no: idx + 1, ...row, missingText: Array.isArray(row.missing) && row.missing.length ? row.missing.join(", ") : "-" }))}
        empty="프롬프트 예시를 불러오는 중입니다."
        columns={[
          { key: "no", label: "#", width: 44 },
          { key: "prompt", label: "prompt" },
          { key: "feature", label: "feature", width: 130, render: (r) => <Pill tone="accent">{r.feature || "-"}</Pill> },
          { key: "action", label: "action", width: 210 },
          { key: "status", label: "status", width: 120, render: (r) => <Pill tone={activationStatusTone(r.status)}>{r.status || "-"}</Pill> },
          { key: "api", label: "api / data", width: 190 },
          { key: "missingText", label: "missing", width: 150 },
          { key: "select", label: "", width: 82, render: (r) => <Button variant="subtle" onClick={() => onSelectPrompt?.(r.prompt)}>선택</Button> },
        ]}
      />
    </Panel>
  );
}

function FlowiAgentThinkingPanel({ row, busy = false, manualSlots = {}, onManualSlot, onUseGuess, onManualRun, onRunRaw }) {
  const missing = Array.isArray(row?.missing) ? row.missing.map(String).filter(Boolean) : [];
  const args = row?.arguments && typeof row.arguments === "object" ? row.arguments : {};
  const guessed = row?.guessed && typeof row.guessed === "object" ? row.guessed : {};
  const guessedValues = guessed.values && typeof guessed.values === "object" ? guessed.values : {};
  const slotKeys = Array.from(new Set([
    "product",
    "root_lot_ids",
    "fab_lot_ids",
    "wafer_ids",
    "step",
    "module",
    "knob_value",
    ...missing,
  ]));
  const slotRows = slotKeys.map((key) => {
    const value = args[key] ?? guessedValues[key] ?? "";
    const text = Array.isArray(value) ? value.join(", ") : String(value || "");
    return {
      slot: key,
      value: text || "-",
      state: missing.includes(key) ? (guessedValues[key] ? "guess" : "missing") : (text ? "filled" : "empty"),
    };
  });
  const hasGuess = Object.values(guessedValues).some((value) => String(value || "").trim());
  return (
    <Panel
      title="Agent thinking"
      subtitle="내부 추론 원문이 아니라 dry-run으로 공개 가능한 intent, slot, missing 보강 후보만 표시합니다."
      right={<Pill tone={busy ? "warn" : row ? activationStatusTone(row.status) : "neutral"}>{busy ? "dry-run" : (row?.status || "ready")}</Pill>}
    >
      {busy && <Banner tone="info">dry-run preview를 계산하고 있습니다.</Banner>}
      {!busy && !row && <EmptyState title="dry-run 결과 없음" hint="드라이런 버튼을 누르면 오케스트레이터 판단과 missing slot 상태가 표시됩니다." />}
      {row && (
        <div style={{ display: "grid", gap: 12 }}>
          <DataTable
            rows={[
              { key: "intent", value: row.intent || "-" },
              { key: "feature", value: row.feature || "-" },
              { key: "action", value: row.action || row.unit_action || "-" },
              { key: "api", value: row.api || row.handler || "-" },
            ]}
            columns={[
              { key: "key", label: "field", width: 130 },
              { key: "value", label: "value" },
            ]}
          />
          <DataTable
            rows={slotRows}
            empty="slot 정보가 없습니다."
            columns={[
              { key: "slot", label: "slot", width: 160 },
              { key: "value", label: "value" },
              { key: "state", label: "state", width: 110, render: (r) => <Pill tone={r.state === "filled" ? "ok" : r.state === "guess" ? "info" : r.state === "missing" ? "warn" : "neutral"}>{r.state}</Pill> },
            ]}
          />
          {missing.length ? (
            <div style={{ display: "grid", gap: 10 }}>
              <Banner tone={hasGuess ? "info" : "warn"}>
                missing slot: {missing.join(", ")} {guessed.rationale ? `· ${guessed.rationale}` : ""}
              </Banner>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(180px,1fr))", gap: 8 }}>
                {missing.map((field) => (
                  <Field key={field} label={field} hint={guessedValues[field] ? `추천: ${guessedValues[field]}` : "직접 입력"}>
                    <input
                      value={manualSlots[field] || ""}
                      onChange={(e) => onManualSlot?.(field, e.target.value)}
                      placeholder={guessedValues[field] || field}
                      style={{ ...formControlStyle, width: "100%", boxSizing: "border-box" }}
                    />
                  </Field>
                ))}
              </div>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <Button variant="primary" onClick={() => onUseGuess?.(guessedValues)} disabled={!hasGuess}>추천대로 진행</Button>
                <Button onClick={() => onManualRun?.(manualSlots)} disabled={!Object.values(manualSlots).some((value) => String(value || "").trim())}>직접 입력 실행</Button>
                <Button variant="subtle" onClick={() => onRunRaw?.()}>그냥 실행</Button>
              </div>
            </div>
          ) : (
            <Banner tone="ok">모든 슬롯 추정 완료</Banner>
          )}
        </div>
      )}
    </Panel>
  );
}

function FlowiExecutionPanel({ user }) {
  const [prompt, setPrompt] = useState(FUNCTION_TEST_PROMPT);
  const [result, setResult] = useState(null);
  const [promptHistory, setPromptHistory] = useState([]);
  const [dryRunRow, setDryRunRow] = useState(null);
  const [dryRunBusy, setDryRunBusy] = useState(false);
  const [manualSlots, setManualSlots] = useState({});
  const [busy, setBusy] = useState(false);
  const [historyBusy, setHistoryBusy] = useState(false);
  const [err, setErr] = useState("");
  const inputStyle = { ...formControlStyle, width: "100%", boxSizing: "border-box", fontSize: 14 };

  const loadPromptHistory = () => {
    setHistoryBusy(true);
    return sf("/api/agent/prompt-history" + qs({ limit: 50 }))
      .then((d) => {
        const rows = Array.isArray(d?.rows) ? d.rows : [];
        setPromptHistory(rows.map((row, idx) => ({
          id: row.id || `server_${idx}_${row.timestamp || row.ts || ""}`,
          ts: row.ts || row.timestamp || "",
          prompt: row.prompt || "",
          feature: row.feature || "-",
          action: row.action || row.selected_function || row.intent || "-",
          intent: row.intent || "-",
          status: row.status || row.result_status || "-",
          missing: Array.isArray(row.missing) ? row.missing.map(String) : [],
          answer: row.answer || row.answer_excerpt || "",
          source_ai: row.source_ai || "",
          client_run_id: row.client_run_id || "",
          elapsed_ms: row.elapsed_ms,
        })));
      })
      .catch(() => {})
      .finally(() => setHistoryBusy(false));
  };

  const rememberPrompt = (body, data, errMessage = "") => {
    const tool = data?.tool || {};
    const workflow = data?.workflow_state || tool.workflow_state || {};
    const entry = {
      id: `h_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
      ts: new Date().toISOString(),
      prompt: body,
      feature: tool.feature || workflow.feature || "-",
      action: tool.action || workflow.action || tool.intent || "-",
      intent: tool.intent || workflow.intent || "-",
      status: errMessage
        ? "error"
        : (workflow.status || (tool.blocked ? "blocked" : (data ? "done" : "waiting"))),
      missing: Array.isArray(tool.missing) ? tool.missing.map(String) : [],
      error: errMessage,
      answer: data?.answer || tool.answer || "",
    };
    setPromptHistory((cur) => [entry, ...cur].slice(0, 50));
  };
  const runDry = (overridePrompt = "") => {
    const body = String(overridePrompt || prompt).trim();
    if (!body) return;
    if (body !== prompt) setPrompt(body);
    setDryRunBusy(true);
    setErr("");
    postJson(ORCHESTRATOR_PREVIEW_ENDPOINT, {
      prompts: [body],
      product: "",
      max_rows: 12,
      context: { ask_llm_to_guess_missing: true, surface: "agent_page" },
    })
      .then((d) => {
        const row = Array.isArray(d?.rows) ? d.rows[0] : null;
        setDryRunRow(row || null);
        setManualSlots({});
      })
      .catch((e) => setErr(e.message || String(e)))
      .finally(() => setDryRunBusy(false));
  };

  const run = (overridePrompt = "", options = {}) => {
    const body = String(overridePrompt || prompt).trim();
    if (!body) return;
    if (body !== prompt) setPrompt(body);
    setBusy(true);
    setErr("");
    postJson("/api/llm/flowi/agent/chat", {
      prompt: body,
      source_ai: "agent_page",
      client_run_id: `agent_page_${Date.now()}`,
      product: "",
      max_rows: 12,
      context: { type: "agent_execution_flow", surface: "agent_page", ...(options.context || {}) },
    })
      .then((d) => {
        setResult(d);
        rememberPrompt(body, d);
        loadPromptHistory();
      })
      .catch((e) => {
        const msg = e.message || String(e);
        setErr(msg);
        rememberPrompt(body, null, msg);
      })
      .finally(() => setBusy(false));
  };
  useEffect(() => {
    loadPromptHistory();
    runDry();
  }, []);

  const tool = result?.tool || {};
  const workflow = result?.workflow_state || tool.workflow_state || {};
  const trace = result?.trace || {};
  const traceSteps = Array.isArray(trace?.steps) ? trace.steps : [];
  const apiRows = flowiApiCallRows(trace);
  const feature = tool.feature || workflow.feature || "-";
  const action = tool.action || workflow.action || tool.intent || "-";
  const intent = tool.intent || workflow.intent || "-";
  const statusTone = tool.blocked ? "bad" : workflow.status === "ready" || workflow.status === "completed" ? "ok" : workflow.waiting_for ? "warn" : "neutral";
  const callPayload = compactToolPayload(tool);

  return (
    <div style={{ display: "grid", gap: 12 }}>
      <div className="flow-agent-loop-grid">
      <Panel
        title="프롬프트 입력"
        subtitle="입력 prompt에서 오케스트레이터 판단, 기능별 단위기능 호출, 결과까지 한 번에 확인합니다."
        right={<Pill tone={statusTone}>{workflow.status || (result ? "done" : "ready")}</Pill>}
      >
        {err && <Banner tone="bad">{err}</Banner>}
        <div style={{ display: "grid", gridTemplateColumns: "minmax(260px,1fr) auto", gap: 8, alignItems: "end" }}>
          <Field label="prompt" hint="자연어 그대로 입력하세요. 이전에 실행한 prompt 는 아래 ‘프롬프트 기록’ 카드에서 다시 선택할 수 있습니다.">
            <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={4} style={{ ...inputStyle, resize: "vertical", lineHeight: 1.55 }} />
          </Field>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <Button onClick={() => runDry()} disabled={dryRunBusy || busy || !prompt.trim()}>{dryRunBusy ? "드라이런 중" : "드라이런"}</Button>
            <Button variant="primary" onClick={() => run()} disabled={busy || !prompt.trim()}>{busy ? "실행 중" : "실행"}</Button>
          </div>
        </div>
      </Panel>

      <PromptHistoryPanel
        entries={promptHistory}
        activePrompt={prompt}
        busy={busy || historyBusy}
        onSelect={(text) => setPrompt(text)}
        onRerun={(text) => run(text)}
      />

      <FlowiAgentThinkingPanel
        row={dryRunRow}
        busy={dryRunBusy}
        manualSlots={manualSlots}
        onManualSlot={(field, value) => setManualSlots((cur) => ({ ...cur, [field]: value }))}
        onUseGuess={(values) => run(prompt, { context: { missing_strategy: "use_preview_guess", missing_slot_overrides: values, preview_row: dryRunRow } })}
        onManualRun={(values) => run(prompt, { context: { missing_strategy: "manual_input", missing_slot_overrides: values, preview_row: dryRunRow } })}
        onRunRaw={() => run(prompt, { context: { missing_strategy: "run_without_guess", preview_row: dryRunRow } })}
      />
      </div>

      <details style={{ border: "1px solid var(--border)", borderRadius: 8, background: "var(--bg-secondary)", padding: 12 }}>
        <summary style={{ cursor: "pointer", fontWeight: 900, color: uxColors.text }}>실행 trace 펼치기</summary>
        <div style={{ display: "grid", gap: 12, marginTop: 12 }}>
          <Panel title="Activation Map (5단계)" subtitle="현재 실행한 프롬프트 하나의 전달 경로와 활성 action입니다.">
            <FlowiActivationMap trace={trace} result={result} prompt={prompt} />
          </Panel>

          <FlowiPublicContext trace={trace} />

          <div style={{ display: "grid", gridTemplateColumns: "minmax(0,0.9fr) minmax(0,1.1fr)", gap: 12, alignItems: "start" }}>
            <Panel title="오케스트레이터 판단" subtitle="프롬프트를 어떤 기능별 단위기능으로 보냈는지">
              <DataTable
                rows={[
                  { key: "user", value: user?.username || "-" },
                  { key: "intent", value: intent },
                  { key: "feature_subagent", value: feature },
                  { key: "unit_action", value: action },
                  { key: "requires_confirmation", value: tool.requires_confirmation ? "true" : "false" },
                  { key: "blocked", value: tool.blocked ? (tool.reject_reason || "true") : "false" },
                ]}
                columns={[
                  { key: "key", label: "field", width: 170 },
                  { key: "value", label: "value" },
                ]}
              />
            </Panel>

            <Panel title="단위기능 콜" subtitle="서버가 검증한 tool payload 요약">
              <JsonBlock value={callPayload} maxHeight={330} />
            </Panel>
          </div>

          <Panel title="API 호출 그래프" subtitle="FastAPI endpoint에서 오케스트레이터, 기능 subagent, 내부 API/data 조회, 답변 payload까지의 흐름입니다.">
            <FlowiCallGraph trace={trace} />
          </Panel>

          <Panel title="FastAPI / handler 호출" subtitle="각 단계가 어떤 endpoint, handler, cache/table을 사용했는지 확인합니다.">
            <DataTable
              rows={apiRows}
              empty="아직 API 호출 내역이 없습니다."
              columns={[
                { key: "no", label: "#", width: 46 },
                { key: "stage", label: "stage", width: 110 },
                { key: "method", label: "method", width: 84 },
                { key: "target", label: "target" },
                { key: "purpose", label: "purpose" },
                { key: "output", label: "output", width: 180 },
                { key: "status", label: "status", width: 96, render: (r) => <Pill tone={callStatusTone(r.status)}>{r.status || "-"}</Pill> },
              ]}
            />
          </Panel>

          <Panel title="진행 단계" subtitle="사용자에게 공개 가능한 실행 trace입니다.">
            <DataTable
              rows={traceSteps.map((step, idx) => ({ ...step, no: idx + 1, stage: step.stage || step.key || step.label, title: step.title || step.label || step.key }))}
              empty="아직 실행 trace가 없습니다."
              columns={[
                { key: "no", label: "#", width: 48 },
                { key: "stage", label: "stage", width: 120 },
                { key: "title", label: "title", width: 150 },
                { key: "status", label: "status", width: 90, render: (r) => <Pill tone={r.status === "done" ? "ok" : r.status === "blocked" || r.status === "error" ? "bad" : r.status === "skipped" ? "neutral" : "warn"}>{r.status || "-"}</Pill> },
                { key: "detail", label: "detail" },
              ]}
            />
          </Panel>
        </div>
      </details>

      <Panel title="결과" subtitle="answer, table/chart, 다음 액션 요약">
        <DataTable
          rows={flowiOutputRows(result || {})}
          empty="실행 후 결과가 표시됩니다."
          columns={[
            { key: "key", label: "field", width: 150 },
            { key: "value", label: "value" },
          ]}
        />
      </Panel>
    </div>
  );
}

export {
  FlowiExecutionPanel,
  AgentWikiPanel,
  SchemaRelationsPanel,
};
