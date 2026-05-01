import { useEffect, useState } from "react";
import { postJson, qs, sf } from "../lib/api";
import {
  Banner,
  Button,
  DataTable,
  EmptyState,
  Field,
  PageHeader,
  PageShell,
  Panel,
  Pill,
  formControlStyle,
  uxColors,
} from "../components/UXKit";
import { LlmCfgPanel } from "./My_Admin";

const SAMPLE_PROMPT = "GAA nFET short Lg에서 DIBL과 SS가 증가했고 CA_RS도 올랐어. 원인 후보와 확인 차트 보여줘.";
const FUNCTION_TEST_PROMPT = "PRODA A1000 #6 현재 fab lot id가 뭐야?";
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
];

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

function JsonBlock({ value, maxHeight = 360 }) {
  return (
    <pre style={{ margin: 0, maxHeight, overflow: "auto", fontSize: 14, lineHeight: 1.45, background: "var(--bg-primary)", border: "1px solid var(--border)", borderRadius: 5, padding: 10 }}>
      {JSON.stringify(value || {}, null, 2)}
    </pre>
  );
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
      <Panel title="Flowi pipeline stage" subtitle="입력 prompt에서 응답까지 이어지는 단일 chain">
        <div style={{ display: "grid", gap: 10 }}>
          {(data?.stages || []).map((stage, idx) => (
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
  const [product, setProduct] = useState("");
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const inputStyle = { ...formControlStyle, width: "100%", boxSizing: "border-box", fontSize: 14 };
  const run = () => {
    setBusy(true);
    setErr("");
    postJson("/api/agent/prompt-preview", { prompt, product, max_rows: 20 })
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
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 160px", gap: 10, alignItems: "end" }}>
          <Field label="prompt">
            <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={4} style={{ ...inputStyle, resize: "vertical", lineHeight: 1.55 }} />
          </Field>
          <Field label="product override">
            <input value={product} onChange={(e) => setProduct(e.target.value)} style={inputStyle} placeholder="optional" />
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
                  { key: "product", value: result?.slots?.product || args.product || "-" },
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

function KnowledgePanel({ isAdmin }) {
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

function LlmPanel({ isAdmin }) {
  const [status, setStatus] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    if (!isAdmin) {
      sf("/api/llm/status").then(setStatus).catch((e) => setErr(e.message || String(e)));
    }
  }, [isAdmin]);
  return (
    <CategoryFrame category={CATEGORIES[6]} right={<Pill tone={isAdmin ? "accent" : (status?.available ? "ok" : "warn")}>{isAdmin ? "admin editable" : (status?.available ? "available" : "read only")}</Pill>}>
      {isAdmin ? (
        <LlmCfgPanel />
      ) : (
        <Panel title="LLM 설정 확인" subtitle="일반 유저는 서버에 저장된 redacted 상태만 확인합니다.">
          {err && <Banner tone="bad">{err}</Banner>}
          <DataTable
            rows={[
              { key: "available", value: status?.available ? "true" : "false" },
              { key: "provider", value: status?.config?.provider || "-" },
              { key: "model", value: status?.config?.model || "-" },
              { key: "endpoint", value: status?.config?.api_url || "-" },
              { key: "timeout", value: status?.config?.timeout_s || "-" },
              { key: "auth_mode", value: status?.config?.auth_mode || "-" },
            ]}
            columns={[
              { key: "key", label: "field", width: 130 },
              { key: "value", label: "value" },
            ]}
          />
        </Panel>
      )}
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

export default function My_Diagnosis({ user }) {
  const [active, setActive] = useState("workflow");
  const isAdmin = user?.role === "admin";
  const activeCategory = CATEGORIES.find((item) => item.id === active) || CATEGORIES[0];
  useEffect(() => {
    if (active === "admin" && !isAdmin) setActive("workflow");
  }, [active, isAdmin]);
  return (
    <PageShell>
      <PageHeader title="에이전트" subtitle="Flowi agent workflow, persona, RAG, item rules, LLM, admin tools" />
      <div style={{ padding: 12, display: "grid", gridTemplateColumns: "250px minmax(0,1fr)", gap: 12, alignItems: "start" }}>
        <Panel title="카테고리" subtitle="8개 영역" bodyStyle={{ padding: 10 }}>
          <CategoryNav active={active} onChange={setActive} isAdmin={isAdmin} />
        </Panel>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: active === "workflow" ? "block" : "none" }}><WorkflowPanel /></div>
          <div style={{ display: active === "persona" ? "block" : "none" }}><PersonaPanel /></div>
          <div style={{ display: active === "prompt" ? "block" : "none" }}><PromptPanel /></div>
          <div style={{ display: active === "knowledge" ? "block" : "none" }}><KnowledgePanel isAdmin={isAdmin} /></div>
          <div style={{ display: active === "recent" ? "block" : "none" }}><RecentRagPanel user={user} /></div>
          <div style={{ display: active === "item" ? "block" : "none" }}><ItemRulesPanel /></div>
          <div style={{ display: active === "llm" ? "block" : "none" }}><LlmPanel isAdmin={isAdmin} /></div>
          <div style={{ display: active === "admin" ? "block" : "none" }}><AdminToolsPanel isAdmin={isAdmin} /></div>
          {!activeCategory && <EmptyState title="카테고리를 선택하세요." />}
        </div>
      </div>
    </PageShell>
  );
}
