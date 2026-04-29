import { useCallback, useEffect, useState } from "react";
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
import { FlowiQualityPanel, LlmCfgPanel } from "./My_Admin";

const SAMPLE_PROMPT = "GAA nFET short Lg에서 DIBL과 SS가 증가했고 CA_RS도 올랐어. 원인 후보와 확인 차트 보여줘.";
const DEFAULT_TABLE_CONTENT = "step_id,step_name,func_step\nAA200000,channel release etch,\nAB300000,inner spacer recess,\nCA100000,CA contact etch,";

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

const AGENT_FLOW_STEPS = [
  {
    step: "01",
    label: "Intent / slot 해석",
    detail: "프롬프트에서 RCA, chart, lot, product, wafer, source, item 후보를 먼저 분리합니다.",
    refs: ["FLOWI_FEATURE_ALIASES", "lot/product/item parser"],
  },
  {
    step: "02",
    label: "Source profile 확인",
    detail: "파일/DB source가 들어오면 grain, join key, item shape, 기본 집계 기준을 먼저 확인합니다.",
    refs: ["dataset_profile", "source_type_profiles"],
  },
  {
    step: "03",
    label: "Item 의미 해석",
    detail: "raw item 이름만 믿지 않고 item_master의 unit, source_type, test_structure, layer, method를 함께 봅니다.",
    refs: ["item_master", "resolve_item_semantics"],
  },
  {
    step: "04",
    label: "RCA 지식 검색",
    detail: "Knowledge Card, causal graph, similar case, engineer/runtime 지식을 모아 후보 근거를 만듭니다.",
    refs: ["knowledge_cards", "causal_edges", "similar_cases", "custom_knowledge"],
  },
  {
    step: "05",
    label: "Whitelisted data tool",
    detail: "필요한 경우에만 안전한 backend tool로 ET/INLINE/VM/EDS/QTIME/FAB 데이터를 조회합니다. LLM SQL은 쓰지 않습니다.",
    refs: ["query_measurements", "Flow-i chart/query handlers"],
  },
  {
    step: "06",
    label: "Agent trace / 출력",
    detail: "최종 답변에는 후보, 근거, missing data, 확인 차트, 다음 액션과 실행 trace를 함께 붙입니다.",
    refs: ["workflow_state", "next_actions", "chart_spec"],
  },
];

function AgentStepCard({ item }) {
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 6, background: "var(--bg-secondary)", padding: 10, display: "grid", gap: 7 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <Pill tone="accent">{item.step}</Pill>
        <span style={{ fontSize: 13, fontWeight: 800, color: uxColors.text }}>{item.label}</span>
      </div>
      <div style={{ fontSize: 12, color: uxColors.textSub, lineHeight: 1.55 }}>{item.detail}</div>
      <div style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>
        {(item.refs || []).map((ref) => <Pill key={ref} tone="neutral">{ref}</Pill>)}
      </div>
    </div>
  );
}

function FlowiPersonaPanel() {
  const [persona, setPersona] = useState(null);
  const [form, setForm] = useState({ system_prompt: "", must_not: "", notes: "" });
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const fallbackPrompt = "Flowi는 사내 Flow 홈 화면의 fab 데이터 assistant입니다. 답변은 짧고 실행 가능하게 작성합니다. 사용자 Markdown 정보가 있으면 담당 제품, 관심 공정, 선호 출력 방식을 반영합니다. 요청이 애매하면 바로 실행한다고 말하지 말고 1/2/3 형태의 선택지를 제시합니다. 신규 인폼/이슈/회의/일정 등록은 조건이 충분하면 바로 실행하고, 기존 기록 수정/삭제/상태 변경은 권한과 대상 내용을 확인한 뒤 진행합니다.";
  const fallbackMustNot = "- DB root/raw data 원본을 직접 수정, 삭제, 덮어쓰기, 이동하지 않는다.\n- 로컬 tool/cache/schema 결과에 없는 숫자, lot, product, step, item 값을 지어내지 않는다.\n- step_id는 영문 2자 + 숫자 6자리 또는 등록된 func_step 이름이 아니면 step으로 확정하지 않는다.\n- 기존 인폼/회의/이슈/일정 수정, 삭제, 상태 변경은 권한과 대상 내용을 확인하기 전 실행하지 않는다.\n- 파일 변경은 FLOWI_FILE_OP 또는 전용 단일파일 반영 플로우 없이 실행하지 않는다.\n- RAG/문서 내용은 flow-data 내부 저장소 밖으로 내보내지 않는다.";
  const inputStyle = {
    ...formControlStyle,
    width: "100%",
    boxSizing: "border-box",
  };
  const applyPersona = (d = {}) => {
    const raw = d.flowi_persona || d;
    const next = {
      system_prompt: raw.system_prompt || raw.default_system_prompt || fallbackPrompt,
      must_not: raw.must_not || raw.default_must_not || fallbackMustNot,
      notes: raw.notes || "",
    };
    setPersona({ ...raw, ...next });
    setForm(next);
  };
  const reload = () => {
    setMsg("");
    sf("/api/llm/flowi/persona")
      .then(applyPersona)
      .catch(() => sf("/api/admin/settings").then(applyPersona).catch((e) => {
        applyPersona({});
        setMsg("로드 오류: " + (e.message || e));
      }));
  };
  useEffect(() => { reload(); }, []);
  const save = () => {
    setBusy(true);
    setMsg("");
    const payload = {
      system_prompt: form.system_prompt || fallbackPrompt,
      must_not: form.must_not || fallbackMustNot,
      notes: form.notes || "",
    };
    const saveViaAdminSettings = () => sf("/api/admin/settings")
      .then((cur) => postJson("/api/admin/settings/save", {
        dashboard_refresh_minutes: cur.dashboard_refresh_minutes ?? 10,
        dashboard_bg_refresh_minutes: cur.dashboard_bg_refresh_minutes ?? 10,
        flowi_persona: payload,
      }));
    postJson("/api/llm/flowi/persona", payload)
      .catch(saveViaAdminSettings)
      .then((d) => {
        applyPersona(d?.flowi_persona ? d : { ...payload, ...(d || {}) });
        setMsg("저장됨");
      })
      .catch((e) => setMsg("저장 오류: " + (e.message || e)))
      .finally(() => setBusy(false));
  };
  return (
    <Panel
      title="Flow-i 페르소나"
      subtitle="에이전트가 기본적으로 따라야 하는 업무 방식과 금지 규칙입니다."
      right={<Pill tone="accent">active persona</Pill>}
    >
      <div style={{ display: "grid", gap: 12 }}>
        {msg && <Banner tone={msg.includes("오류") ? "bad" : "ok"}>{msg}</Banner>}
        <Banner tone="info">
          여기 적힌 내용이 홈 Flow-i와 외부 Agent API의 기본 system prompt로 사용됩니다. 사용자별 담당 제품이나 선호 출력은 별도 사용자 메모가 추가로 붙습니다.
        </Banner>
        <Field label="기본 페르소나">
          <textarea
            value={form.system_prompt}
            onChange={(e) => setForm({ ...form, system_prompt: e.target.value })}
            rows={9}
            style={{ ...inputStyle, resize: "vertical", lineHeight: 1.55, fontFamily: "monospace" }}
          />
        </Field>
        <Field label="반드시 하지 말아야 할 것">
          <textarea
            value={form.must_not}
            onChange={(e) => setForm({ ...form, must_not: e.target.value })}
            rows={8}
            style={{ ...inputStyle, resize: "vertical", lineHeight: 1.55, fontFamily: "monospace" }}
          />
        </Field>
        <Field label="운영 메모">
          <textarea
            value={form.notes}
            onChange={(e) => setForm({ ...form, notes: e.target.value })}
            rows={3}
            style={{ ...inputStyle, resize: "vertical", lineHeight: 1.5 }}
            placeholder="변경 이유, 적용 범위, 금지 조건"
          />
        </Field>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <Button variant="primary" onClick={save} disabled={busy || !form.system_prompt.trim()}>{busy ? "저장 중" : "저장"}</Button>
          <Button variant="ghost" onClick={reload} disabled={busy}>새로고침</Button>
          <span style={{ fontSize: 11, color: uxColors.textSub }}>
            {persona?.updated_at ? `마지막 수정: ${String(persona.updated_at).replace("T", " ").slice(0, 16)} · ${persona.updated_by || "-"}` : "저장 전 기본 페르소나"}
          </span>
        </div>
      </div>
    </Panel>
  );
}

export default function My_Diagnosis({ user }) {
  const [active, setActive] = useState("agent");
  const [prompt, setPrompt] = useState(SAMPLE_PROMPT);
  const [product, setProduct] = useState("PRODA");
  const [sourceFile, setSourceFile] = useState("");
  const [sourceRoot, setSourceRoot] = useState("");
  const [sourceLabel, setSourceLabel] = useState("");
  const [sourcePayload, setSourcePayload] = useState(null);
  const [sourceProfile, setSourceProfile] = useState(null);
  const [err, setErr] = useState("");
  const [q, setQ] = useState("CA");
  const [items, setItems] = useState([]);
  const [manifest, setManifest] = useState(null);
  const [ragView, setRagView] = useState(null);
  const [ragQ, setRagQ] = useState("");
  const [useCases, setUseCases] = useState([]);
  const [prior, setPrior] = useState({ module: "", use_case: "", prior_knowledge: "", tags: "" });
  const [ragPrompt, setRagPrompt] = useState("[flow-i RAG Update] PC-CB-M1 Chain item은 14x14, 13x13, 12x12 DOE TEG가 다르고 gate pitch와 Cell height를 구분해서 봐야 함.");
  const [ragResult, setRagResult] = useState(null);
  const [docForm, setDocForm] = useState({
    title: "",
    document_type: "gpt_deep_research",
    product: "",
    module: "",
    tags: "",
    content: "",
  });
  const [docResult, setDocResult] = useState(null);
  const [pageAdmin, setPageAdmin] = useState(null);
  const [tableForm, setTableForm] = useState({
    title: "Process plan / func_step preview",
    table_type: "process_plan_func_step",
    content: DEFAULT_TABLE_CONTENT,
    apply_instructions: "",
    target_file: "",
  });
  const [tableGrid, setTableGrid] = useState(() => parseGridText(DEFAULT_TABLE_CONTENT));
  const [baseFiles, setBaseFiles] = useState([]);
  const [tablePreview, setTablePreview] = useState(null);
  const [tableResult, setTableResult] = useState(null);
  const [refPrompt, setRefPrompt] = useState("PC-CB-M1 Chain item은 14x14, 13x13, 12x12 DOE TEG가 다르고 gate pitch와 Cell height discriminator를 유지해서 alias화해야 함.");
  const [refProposal, setRefProposal] = useState(null);
  const [tegRowsText, setTegRowsText] = useState('[{"name":"TEG_TOP","x":13.6,"y":29.6,"width":1.2,"height":0.6},{"name":"TEG_RIGHT","x":27.6,"y":14.6}]');
  const [tegProposal, setTegProposal] = useState(null);
  const isAdmin = user?.role === "admin";
  const canFileWrite = isAdmin || (pageAdmin?.pages || []).includes("filebrowser");
  const compactInput = {
    ...formControlStyle,
    width: "100%",
    height: 34,
    boxSizing: "border-box",
    fontFamily: "monospace",
  };
  const sourceGridStyle = {
    display: "grid",
    gridTemplateColumns: "minmax(120px,0.45fr) minmax(280px,1fr) 128px",
    gap: 10,
    alignItems: "start",
  };
  const sourceFieldStyle = { gridTemplateRows: "14px 34px 28px", alignItems: "start" };
  const tableInputStyle = {
    width: "100%",
    minWidth: 92,
    height: 30,
    border: 0,
    outline: "none",
    boxShadow: "none",
    background: "transparent",
    color: uxColors.text,
    fontSize: 12,
    fontFamily: "monospace",
    padding: "6px 8px",
    boxSizing: "border-box",
  };
  const tableHeaderInputStyle = {
    ...tableInputStyle,
    fontWeight: 800,
    color: uxColors.text,
  };
  const tableCellStyle = {
    padding: 0,
    borderRight: "1px solid var(--border)",
    borderBottom: "1px solid var(--border)",
    background: "var(--bg-primary)",
  };
  const sourcePathValue = sourceFile || sourceRoot;
  const setSourcePathValue = (value) => {
    const next = value || "";
    const looksLikeFile = /\.(parquet|csv|json|jsonl|xlsx?)$/i.test(next.trim());
    if (!next.trim()) {
      setSourceRoot("");
      setSourceFile("");
    } else if (looksLikeFile) {
      setSourceFile(next);
      setSourceRoot("");
    } else {
      setSourceRoot(next);
      setSourceFile("");
    }
  };

  const tabs = [
    { k: "agent", l: "RAG 반영" },
    { k: "dictionary", l: "Item Dictionary" },
    { k: "knowledge", l: "전체 지식" },
    ...(isAdmin ? [
      { k: "persona", l: "기본 페르소나" },
      { k: "quality", l: "품질/워크플로우" },
      { k: "llm", l: "LLM 설정" },
    ] : []),
  ];

  const loadRagView = (nextQ = ragQ) => {
    sf("/api/semiconductor/knowledge/rag-view" + qs({ q: nextQ, limit: 180 }))
      .then(setRagView)
      .catch((e) => setErr(e.message || String(e)));
  };

  const loadManifest = () => {
    sf("/api/semiconductor/knowledge")
      .then(setManifest)
      .catch((e) => setErr(e.message || String(e)));
    loadRagView(ragQ);
    sf("/api/admin/my-page-admin")
      .then(setPageAdmin)
      .catch(() => {});
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
    sf("/api/filebrowser/base-files")
      .then((d) => setBaseFiles((d.files || []).filter((f) => ["csv", "parquet"].includes(String(f.ext || "").toLowerCase()) && ["base_root", "db_root"].includes(f.source))))
      .catch(() => setBaseFiles([]));
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

  const saveDocumentKnowledge = () => {
    setErr("");
    const tags = String(docForm.tags || "").split(",").map((x) => x.trim()).filter(Boolean);
    postJson("/api/semiconductor/knowledge/document", { ...docForm, tags })
      .then((d) => {
        setDocResult(d);
        setDocForm({ ...docForm, title: "", content: "", tags: "" });
        loadManifest();
      })
      .catch((e) => setErr(e.message || String(e)));
  };

  const tableContentFromGrid = () => gridToCsv(tableGrid.columns, tableGrid.rows);
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
    content: tableContentFromGrid(),
    visibility: "private",
    product: "",
    module: "",
    tags: [],
  });

  const previewTableKnowledge = () => {
    setErr("");
    setTableResult(null);
    postJson("/api/semiconductor/knowledge/table/preview", tablePayload())
      .then(setTablePreview)
      .catch((e) => setErr(e.message || String(e)));
  };

  const commitTableKnowledge = () => {
    setErr("");
    postJson("/api/semiconductor/knowledge/table/commit", { ...tablePayload(), apply_to_file: true, preview: tablePreview || {} })
      .then((d) => {
        setTableResult(d);
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

  return (
    <PageShell>
      <PageHeader
        title="에이전트"
      />
      <div style={{ padding: 12, display: "grid", gap: 12 }}>
        {err && <Banner tone="bad" onClose={() => setErr("")}>{err}</Banner>}
        <Panel
          title="RAG 반영 현황"
          subtitle="[flow-i update], 문서형 지식, 표 지식이 custom_knowledge에 어떻게 저장되고 검색 대상으로 잡히는지 확인합니다."
          right={<Pill tone="accent">append-only RAG</Pill>}
        >
          <div style={{ display: "grid", gap: 10 }}>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(150px,1fr))", gap: 8 }}>
              {Object.entries(ragView?.counts || manifest?.counts || {}).filter(([k]) => ["custom_knowledge", "documents", "tables", "knowledge_cards", "matched_runtime"].includes(k)).map(([k, v]) => (
                <div key={k} style={{ border: "1px solid var(--border)", borderRadius: 5, background: "var(--bg-primary)", padding: "10px 12px" }}>
                  <div style={{ fontSize: 10, color: uxColors.textSub, marginBottom: 4 }}>{k}</div>
                  <div style={{ fontSize: 20, fontWeight: 900, fontFamily: "monospace", color: uxColors.text }}>{v}</div>
                </div>
              ))}
            </div>
            <Banner tone="info">
              문서 본문은 사람이 보는 한국어를 유지하고, RAG 검색용으로 chunk, canonical item 후보, tag, raw token을 같이 저장합니다. 실제 답변 실행은 홈 Flow-i에서 하고, 이 화면은 지식 반영 상태를 검토하는 곳입니다.
            </Banner>
          </div>
        </Panel>

        <TabStrip items={tabs} active={active} onChange={setActive} />

        {active === "agent" && (
          <div style={{ display: "grid", gap: 12 }}>
            <Panel title="최근 RAG 반영 내역" subtitle="[flow-i update], 문서형 지식, 표 지식이 어떤 형태로 RAG에 반영됐는지 시간순으로 봅니다.">
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginBottom: 10 }}>
                <input
                  value={ragQ}
                  onChange={(e) => setRagQ(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && loadRagView(e.currentTarget.value)}
                  style={{ ...formControlStyle, width: 300 }}
                  placeholder="문서 제목, item, module, tag 검색"
                />
                <Button onClick={() => loadRagView(ragQ)}>검색</Button>
                <Button variant="ghost" onClick={() => { setRagQ(""); loadRagView(""); }}>전체</Button>
                <span style={{ flex: 1 }} />
                <Pill tone="accent">runtime {ragView?.counts?.custom_knowledge ?? manifest?.counts?.custom_cards ?? 0}</Pill>
                <Pill tone="info">documents {ragView?.counts?.documents ?? 0}</Pill>
                <Pill tone="neutral">tables {ragView?.counts?.tables ?? 0}</Pill>
              </div>
              <DataTable
                rows={ragView?.recent_updates || ragView?.runtime_knowledge || []}
                maxHeight={430}
                columns={[
                  { key: "created_at", label: "time", width: 116, render: (r) => String(r.created_at || "").replace("T", " ").slice(0, 16) },
                  { key: "username", label: "by", width: 90 },
                  { key: "kind", label: "type", width: 120, render: (r) => <Pill tone={r.kind === "document" ? "accent" : "neutral"}>{r.document_type || r.kind || "-"}</Pill> },
                  { key: "visibility", label: "vis", width: 70 },
                  { key: "display_title", label: "표시명", render: (r) => r.display_title || r.title || "-" },
                  { key: "key_terms", label: "검색키", render: (r) => listText(r.key_terms || r.items || r.tags, 5) },
                  { key: "chunk_count", label: "chunks/rows", width: 88, render: (r) => r.kind === "table" ? (r.row_count || "-") : (r.chunk_count || "-") },
                  { key: "rag_effect", label: "RAG 반영 방식", render: (r) => r.rag_effect || "-" },
                ]}
              />
            </Panel>

            <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)", gap: 12, alignItems: "start" }}>
              <Panel title="문서 타입 지식 등록" subtitle="Admin이 긴 문서를 공용 runtime RAG 지식으로 저장하고, 모든 유저의 에이전트 검색에 반영합니다.">
                <div style={{ display: "grid", gap: 8 }}>
                  <Banner tone="info">문서 지식은 admin이 공용 RAG로 등록합니다. 등록 내용은 <code>flow-data/semiconductor/custom_knowledge.jsonl</code>에만 append-only 저장되며 외부로 전송하거나 별도 문서 파일로 내보내지 않습니다.</Banner>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 180px", gap: 8 }}>
                    <Field label="제목">
                      <input value={docForm.title} onChange={(e) => setDocForm({ ...docForm, title: e.target.value })} style={formControlStyle} placeholder="예: PRODA PC-CB-M1 심층리서치" />
                    </Field>
                    <Field label="문서 타입">
                      <select value={docForm.document_type} onChange={(e) => setDocForm({ ...docForm, document_type: e.target.value })} style={formControlStyle}>
                        <option value="gpt_deep_research">GPT 심층리서치</option>
                        <option value="internal_knowledge">사내 정보지식</option>
                        <option value="process_spec">공정/spec 문서</option>
                        <option value="rca_report">RCA 보고서</option>
                        <option value="meeting_note">회의/결정사항</option>
                        <option value="external_paper">논문/외부자료(로컬 등록)</option>
                      </select>
                    </Field>
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "160px 160px 1fr", gap: 8 }}>
                    <Field label="product">
                      <input value={docForm.product} onChange={(e) => setDocForm({ ...docForm, product: e.target.value })} style={formControlStyle} placeholder="PRODA" />
                    </Field>
                    <Field label="module">
                      <input value={docForm.module} onChange={(e) => setDocForm({ ...docForm, module: e.target.value })} style={formControlStyle} placeholder="CA, RMG, BEOL" />
                    </Field>
                    <Field label="tags">
                      <input value={docForm.tags} onChange={(e) => setDocForm({ ...docForm, tags: e.target.value })} style={formControlStyle} placeholder="DIBL, PC-CB-M1, chain" />
                    </Field>
                  </div>
                  <Field label="문서 본문">
                    <textarea value={docForm.content} onChange={(e) => setDocForm({ ...docForm, content: e.target.value })} rows={10} style={{ ...formControlStyle, resize: "vertical", lineHeight: 1.55 }} />
                  </Field>
                  <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                    <Button variant="primary" onClick={saveDocumentKnowledge} disabled={!isAdmin || !docForm.content.trim()}>문서 RAG 등록</Button>
                    <span style={{ fontSize: 11, color: uxColors.textSub }}>{isAdmin ? "본문은 표시용으로 보존되고, 공용 RAG 검색용 chunk/token/요약 구조가 함께 저장됩니다." : "문서 지식 등록은 admin 전용입니다. 등록된 문서는 모든 유저의 에이전트 검색에 사용됩니다."}</span>
                  </div>
                  {docResult?.structured && <Banner tone="ok">저장됨: {docResult.structured.chunk_count} chunks</Banner>}
                </div>
              </Panel>

              <Panel title="[flow-i update] 빠른 지식 등록" subtitle="짧은 운영 지식은 marker 기반으로 저장하고 최근 반영 내역에서 바로 확인합니다.">
                <div style={{ display: "grid", gap: 8 }}>
                  <textarea value={ragPrompt} onChange={(e) => setRagPrompt(e.target.value)} rows={7} style={{ ...formControlStyle, resize: "vertical", lineHeight: 1.55 }} />
                  <Button variant="primary" onClick={saveRagUpdate} disabled={!ragPrompt.trim()}>RAG Update 저장</Button>
                  {ragResult?.structured && (
                    <pre style={{ maxHeight: 220, overflow: "auto", fontSize: 11, background: "var(--bg-primary)", border: "1px solid var(--border)", borderRadius: 5, padding: 8, margin: 0 }}>
                      {JSON.stringify(ragResult.structured, null, 2)}
                    </pre>
                  )}
                </div>
              </Panel>
            </div>

            <Panel title="표 지식 Preview → 확정 반영" subtitle="표 본문을 대상 단일파일 schema에 맞춰 매핑하고, 확인 후 해당 CSV/Parquet에 행을 추가합니다.">
              <div style={{ display: "grid", gridTemplateColumns: "minmax(0,0.9fr) minmax(0,1.1fr)", gap: 12, alignItems: "start" }}>
                <div style={{ display: "grid", gap: 8 }}>
                  <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 220px", gap: 8 }}>
                    <Field label="제목">
                      <input value={tableForm.title} onChange={(e) => setTableForm({ ...tableForm, title: e.target.value })} style={formControlStyle} />
                    </Field>
                    <Field label="표 타입">
                      <select value={tableForm.table_type} onChange={(e) => setTableForm({ ...tableForm, table_type: e.target.value })} style={formControlStyle}>
                        <option value="process_plan_func_step">공정 plan → func_step</option>
                        <option value="inline_item_semantics">Inline step/item/item_desc</option>
                        <option value="teg_coordinate_table">TEG 좌표/layout 표</option>
                        <option value="data_cleaning_plan">데이터 클리닝 기준</option>
                        <option value="relation_mapping_table">Relation/Table map 기준</option>
                      </select>
                    </Field>
                  </div>
                  <Field label="대상 단일파일(schema 기준)">
                    <select value={tableForm.target_file} onChange={(e) => setTableForm({ ...tableForm, target_file: e.target.value })} style={formControlStyle}>
                      <option value="">-- 추가할 CSV/Parquet 선택 --</option>
                      {baseFiles.map((f) => <option key={f.path || f.name} value={f.name}>{f.name} · {f.role || f.source}</option>)}
                    </select>
                  </Field>
                  <Field label="표 본문">
                    <div
                      onPaste={(e) => {
                        const text = e.clipboardData?.getData("text/plain") || "";
                        if (!text.trim()) return;
                        e.preventDefault();
                        setGridAndContent(parseGridText(text));
                      }}
                      style={{ border: "1px solid var(--border)", borderRadius: 6, overflow: "auto", maxHeight: 260, background: "var(--bg-primary)" }}
                    >
                      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, tableLayout: "auto" }}>
                        <thead>
                          <tr>
                            <th style={{ width: 40, padding: "6px 8px", borderRight: "1px solid var(--border)", borderBottom: "1px solid var(--border)", color: uxColors.textSub, background: "var(--bg-tertiary)", textAlign: "center" }}>#</th>
                            {tableGrid.columns.map((col, ci) => (
                              <th key={ci} style={{ ...tableCellStyle, background: "var(--bg-tertiary)" }}>
                                <input value={col} onChange={(e) => updateGridHeader(ci, e.target.value)} style={tableHeaderInputStyle} />
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {tableGrid.rows.map((row, ri) => (
                            <tr key={ri}>
                              <td style={{ width: 40, padding: "6px 8px", textAlign: "center", borderRight: "1px solid var(--border)", borderBottom: "1px solid var(--border)", color: uxColors.textSub, fontFamily: "monospace", background: "var(--bg-secondary)" }}>{ri + 1}</td>
                              {tableGrid.columns.map((_, ci) => (
                                <td key={ci} style={tableCellStyle}>
                                  <input value={row[ci] || ""} onChange={(e) => updateGridCell(ri, ci, e.target.value)} style={tableInputStyle} />
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
                      <Button onClick={() => setGridAndContent({ ...tableGrid, rows: [...tableGrid.rows, Array(tableGrid.columns.length).fill("")] })}>+ 행</Button>
                      <Button onClick={() => setGridAndContent({ columns: [...tableGrid.columns, `col_${tableGrid.columns.length + 1}`], rows: tableGrid.rows.map((r) => [...r, ""]) })}>+ 열</Button>
                      <span style={{ fontSize: 11, color: uxColors.textSub, alignSelf: "center" }}>엑셀/CSV 표를 이 영역에 붙여넣으면 표로 변환됩니다.</span>
                    </div>
                  </Field>
                  <Field label="반영 지시 프롬프트">
                    <textarea
                      value={tableForm.apply_instructions}
                      onChange={(e) => setTableForm({ ...tableForm, apply_instructions: e.target.value })}
                      rows={4}
                      style={{ ...formControlStyle, resize: "vertical", lineHeight: 1.5 }}
                      placeholder={"예: step_name이랑 operation_name은 같은 열이다\nfunc_step 열은 그대로 넣지 말고 기존 func_step 값에 맞게 trim/정규화해줘\ncomment 열은 넣지 말고 제외해줘"}
                    />
                    <div style={{ marginTop: 4, fontSize: 11, color: uxColors.textSub }}>
                      이 지시는 확정 시 schema별 반영 규칙으로 저장됩니다. 같은 입력/대상 schema가 다시 들어오면 preview에서 자동으로 참고합니다.
                    </div>
                  </Field>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                    <Button onClick={previewTableKnowledge} disabled={!tableGrid.columns.length || !tableForm.target_file}>반영 방식 Preview</Button>
                    <Button variant="primary" onClick={commitTableKnowledge} disabled={!tablePreview?.target_file_preview?.mapped_row_count || !canFileWrite}>확인 후 단일파일 반영</Button>
                    <span style={{ fontSize: 11, color: uxColors.textSub }}>반영 시 백업 생성 후 행 append</span>
                  </div>
                  {!canFileWrite && <Banner tone="warn">단일파일 반영은 admin 또는 FileBrowser 위임 사용자만 가능합니다.</Banner>}
                  {tableResult?.ok && <Banner tone="ok">저장됨: {tableResult.file_apply?.file || "RAG"} / added {tableResult.file_apply?.added || 0} rows</Banner>}
                </div>
                <div style={{ display: "grid", gap: 8 }}>
                  {tablePreview?.warnings?.length ? <Banner tone="warn">{tablePreview.warnings.join(" / ")}</Banner> : null}
                  {tablePreview?.target_file_preview?.column_mapping?.length ? (
                    <DataTable
                      rows={tablePreview.target_file_preview.column_mapping}
                      maxHeight={180}
                      columns={[
                        { key: "target_col", label: "target column" },
                        { key: "target_dtype", label: "dtype", width: 90 },
                        { key: "source_col", label: "input column" },
                        { key: "apply_action", label: "action", width: 130 },
                        { key: "sample_before", label: "before", width: 130 },
                        { key: "sample_after", label: "after", width: 130 },
                        { key: "reason", label: "reason" },
                      ]}
                    />
                  ) : null}
                  {tablePreview?.table_apply_policy?.prior_policy_count ? (
                    <Banner tone="info">같은 schema의 이전 반영 규칙 {tablePreview.table_apply_policy.prior_policy_count}개를 함께 적용했습니다.</Banner>
                  ) : null}
                  {tablePreview?.cleaning_summary?.note ? <Banner tone="info">{tablePreview.cleaning_summary.note}</Banner> : null}
                  <DataTable
                    rows={tablePreview?.preview_rows || []}
                    maxHeight={330}
                    columns={[
                      { key: "step_id", label: "step_id", width: 110 },
                      { key: "step_name", label: "step/name" },
                      { key: "raw_item_id", label: "item", width: 120 },
                      { key: "canonical_item_id", label: "canonical", width: 120 },
                      { key: "proposed_func_step", label: "func_step", width: 150 },
                      { key: "confidence", label: "conf", width: 58 },
                      { key: "cleaning_actions", label: "cleaning", render: (r) => listText(r.cleaning_actions, 3) },
                    ]}
                  />
                  {tablePreview?.teg_definitions?.length ? (
                    <DataTable
                      rows={tablePreview.teg_definitions}
                      maxHeight={180}
                      columns={[
                        { key: "id", label: "TEG id", width: 120 },
                        { key: "label", label: "label" },
                        { key: "dx_mm", label: "x", width: 70 },
                        { key: "dy_mm", label: "y", width: 70 },
                        { key: "role", label: "role", width: 90 },
                      ]}
                    />
                  ) : null}
                </div>
              </div>
            </Panel>

            <Panel title="문서형 RAG 반영 방식" subtitle="사내 GPT가 활용하기 좋은 형태로 저장되는 필드">
              <DataTable
                rows={[
                  { field: "display_title / display_content", value: "사용자가 입력한 한국어 제목과 본문을 화면 표시용으로 그대로 보존" },
                  { field: "chunks", value: "긴 문서를 900자 안팎 passage로 나누어 검색/인용 단위로 사용" },
                  { field: "known_canonical_candidates", value: "item_master와 매칭되는 DIBL, VTH, CA_RS 같은 canonical item 후보" },
                  { field: "raw_item_tokens / tags", value: "PC-CB-M1, gate_pitch 같은 검색 토큰과 문서 타입 tag" },
                  { field: "table classifications", value: "공정 plan, inline item, TEG 좌표 표는 preview 후 확정 시 table knowledge로 저장" },
                  { field: "review_status", value: "admin_added 또는 needs_admin_review로 운영 검토 상태 구분" },
                ]}
                columns={[
                  { key: "field", label: "field", width: 230 },
                  { key: "value", label: "RAG 사용 의미" },
                ]}
              />
            </Panel>
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
          <div style={{ display: "grid", gap: 12 }}>
            <Panel title="RAG 지식 한눈에 보기" subtitle="등록된 지식카드와 Item Dictionary가 어떻게 연결되는지 확인합니다.">
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                <input
                  value={ragQ}
                  onChange={(e) => setRagQ(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && loadRagView(e.currentTarget.value)}
                  style={{ ...formControlStyle, width: 280 }}
                  placeholder="Item, module, cause, raw token 검색"
                />
                <Button onClick={() => loadRagView(ragQ)}>검색</Button>
                <Button variant="ghost" onClick={() => { setRagQ(""); loadRagView(""); }}>전체</Button>
                <span style={{ flex: 1 }} />
                <Pill tone="accent">version {ragView?.version || manifest?.knowledge_version || "-"}</Pill>
              </div>
            </Panel>

            <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1.2fr) minmax(320px,0.8fr)", gap: 12, alignItems: "start" }}>
              <Panel title="Item Dictionary + 연결" subtitle="각 Item이 어떤 지식카드/edge에 연결되는지">
                <DataTable
                  rows={ragView?.items || []}
                  maxHeight={430}
                  columns={[
                    { key: "canonical_item_id", label: "item", width: 120 },
                    { key: "source_type", label: "source", width: 76 },
                    { key: "unit", label: "unit", width: 76 },
                    { key: "test_structure", label: "structure", width: 130 },
                    { key: "module", label: "module", width: 120 },
                    { key: "meaning", label: "meaning" },
                    { key: "knowledge_cards", label: "cards", render: (r) => listText(r.knowledge_cards, 3) },
                    { key: "connections", label: "links", render: (r) => listText((r.connections || []).map((x) => `${x.source}->${x.target}`), 3) },
                  ]}
                />
              </Panel>
              <Panel title="Runtime 추가 지식" subtitle="문서/표/RAG Update로 flow-data에 쌓인 지식">
                <DataTable
                  rows={ragView?.runtime_knowledge || []}
                  maxHeight={430}
                  columns={[
                    { key: "created_at", label: "time", width: 116, render: (r) => String(r.created_at || "").replace("T", " ").slice(0, 16) },
                    { key: "kind", label: "kind", width: 96 },
                    { key: "visibility", label: "vis", width: 70 },
                    { key: "display_title", label: "표시명", render: (r) => r.display_title || r.title || "-" },
                    { key: "key_terms", label: "검색키", render: (r) => listText(r.key_terms || r.items || r.tags, 5) },
                    { key: "display_content", label: "내용", render: (r) => r.display_content || r.content || "-" },
                  ]}
                />
              </Panel>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)", gap: 12, alignItems: "start" }}>
              <Panel title="Knowledge Cards" subtitle="증상 item에서 원인/확인 항목으로 붙는 RAG 카드">
                <DataTable
                  rows={ragView?.knowledge_cards || []}
                  maxHeight={360}
                  columns={[
                    { key: "source_kind", label: "src", width: 64, render: (r) => <Pill tone={r.source_kind === "custom" ? "accent" : "neutral"}>{r.source_kind}</Pill> },
                    { key: "title", label: "title" },
                    { key: "symptom_items", label: "items", render: (r) => listText(r.symptom_items, 5) },
                    { key: "module_tags", label: "module", render: (r) => listText(r.module_tags, 3) },
                    { key: "recommended_checks", label: "checks", render: (r) => listText(r.recommended_checks, 3) },
                  ]}
                />
              </Panel>
              <Panel title="Causal Connections" subtitle="RCA에서 따라가는 source → relation → target 연결">
                <DataTable
                  rows={ragView?.causal_edges || []}
                  maxHeight={360}
                  columns={[
                    { key: "source", label: "source" },
                    { key: "relation", label: "relation", width: 140 },
                    { key: "target", label: "target" },
                    { key: "module", label: "module", width: 130 },
                    { key: "evidence", label: "evidence" },
                  ]}
                />
              </Panel>
            </div>
          </div>
        )}

        {active === "quality" && isAdmin && (
          <FlowiQualityPanel />
        )}

        {active === "persona" && isAdmin && (
          <FlowiPersonaPanel />
        )}

        {active === "llm" && isAdmin && (
          <LlmCfgPanel />
        )}
      </div>
    </PageShell>
  );
}
