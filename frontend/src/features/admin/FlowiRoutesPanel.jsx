import { useEffect, useMemo, useRef, useState } from "react";
import SpreadsheetPasteGrid, { normalizeSpreadsheetRows } from "../../components/SpreadsheetPasteGrid";
import { sf } from "../../lib/api";
import "./FlowiRoutesPanel.css";

const API = "/api/flowi-learning";
const SEGMENT_COLUMNS = ["text", "kind", "value"];
const SEGMENT_KINDS = ["product", "lot", "item", "source", "step", "intent"];
const EMPTY_RULE = { id: "", title: "", question: "", normalized_question: "", route: "auto", enabled: false, segments: [], notes: "" };
const asArray = value => Array.isArray(value) ? value : [];
const post = (path, body) => sf(`${API}${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const timeLabel = value => {
  if (!value) return "";
  const date = new Date(typeof value === "number" && value < 1e12 ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("ko-KR");
};
const pretty = value => typeof value === "string" ? value : JSON.stringify(value, null, 2);
const text = value => value == null ? "" : String(value);

function responseTrace(message) {
  const response = message?.response || {};
  const tool = response.tool || message?.tool_payload || {};
  const routing = response.routing_trace ?? tool.routing_trace ?? message?.routing_trace;
  const execution = response.execution_trace ?? tool.execution_trace ?? message?.execution_trace;
  const parts = response.questions ?? response.subquestions ?? response.sub_questions ?? response.subresults ?? routing?.subquestions ?? routing?.sub_questions ?? tool.subquestions ?? tool.sub_questions ?? [];
  return { routing, execution, parts: asArray(parts), action: tool.action || response.action || routing?.action || routing?.selected_action || "" };
}

function TraceCard({ title, value }) {
  if (value == null || value === "") return null;
  return <details className="fr-trace" open><summary>{title}</summary><pre>{pretty(value)}</pre></details>;
}

function RoutingDiagram({ nodes, edges, actions, maxQuestions, maxCalls }) {
  const labels = Object.fromEntries(nodes.map(node => [node.id, node.label || node.id]));
  return <section className="fr-card" aria-label="질문 라우팅 흐름">
    <div className="fr-section-head"><h3>질문 처리 경로</h3><span>질문 최대 {maxQuestions ?? 4}개 · LLM 호출 최대 {maxCalls ?? 6}회</span></div>
    <div className="fr-diagram">{nodes.map((node, index) => <div className="fr-node-group" key={node.id}>
      <div className="fr-node"><b>{index + 1}. {node.label || node.id}</b>{node.detail && <span>{node.detail}</span>}</div>
      {edges.filter(edge => edge[0] === node.id).map((edge, i) => <div className="fr-edge" key={`${edge[1]}-${i}`}>↳ {labels[edge[1]] || edge[1]}</div>)}
    </div>)}</div>
    {actions.length > 0 && <div className="fr-action-list">{actions.map(action => <span key={action.id} title={action.description}>{action.id} · {action.description}</span>)}</div>}
  </section>;
}

export default function FlowiRoutesPanel() {
  const [data, setData] = useState(null);
  const [conversations, setConversations] = useState([]);
  const [selectedConversation, setSelectedConversation] = useState("");
  const [detail, setDetail] = useState(null);
  const [selectedMessage, setSelectedMessage] = useState("");
  const [search, setSearch] = useState("");
  const [draft, setDraft] = useState(EMPTY_RULE);
  const [origin, setOrigin] = useState(null);
  const [preview, setPreview] = useState(null);
  const [previewQuestion, setPreviewQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const detailRequest = useRef(0);

  async function reload() {
    setLoading(true); setError("");
    try {
      const [routing, history] = await Promise.all([sf(`${API}/routing`), sf(`${API}/conversations`)]);
      setData(routing); setConversations(asArray(history.conversations));
    } catch (e) { setError(e.message || "라우팅 정보를 불러오지 못했습니다."); }
    finally { setLoading(false); }
  }
  useEffect(() => { reload(); }, []);
  useEffect(() => {
    const ticket = ++detailRequest.current;
    setDetail(null); setSelectedMessage("");
    if (!selectedConversation) return;
    sf(`${API}/conversations/${encodeURIComponent(selectedConversation)}`)
      .then(value => { if (ticket === detailRequest.current) setDetail(value); })
      .catch(e => { if (ticket === detailRequest.current) setError(e.message || "대화 내용을 불러오지 못했습니다."); });
  }, [selectedConversation]);

  const questions = useMemo(() => asArray(detail?.messages).map((message, index, all) => {
    if (message.role !== "user") return null;
    const response = all.slice(index + 1).find(next => next.role === "assistant" || next.role === "user");
    return { message, response: response?.role === "assistant" ? response : null };
  }).filter(Boolean), [detail]);
  const selectedPair = questions.find(pair => pair.message.id === selectedMessage);
  const filteredConversations = conversations.filter(row => [row.username, row.title, row.id].some(value => text(value).toLowerCase().includes(search.toLowerCase())));
  const update = (name, value) => { setDraft(old => ({ ...old, [name]: value })); setPreview(null); };

  function startFromQuestion(pair) {
    const trace = responseTrace(pair.response);
    const successful = pair.response && !pair.response.error && !pair.response.response?.error && pair.response.response?.ok !== false && !pair.response.response?.tool?.missing?.length && pair.response.response?.tool?.approval?.status !== "pending";
    const available = new Set(asArray(data?.actions).map(action => action.id));
    const route = successful && available.has(trace.action) ? trace.action : "auto";
    setSelectedMessage(pair.message.id);
    setDraft({ ...EMPTY_RULE, question: text(pair.message.content), normalized_question: text(pair.message.content), route });
    setOrigin({ conversation_id: selectedConversation, message_id: pair.message.id });
    setPreview(null); setError(""); setNotice("");
  }
  function editRule(rule) {
    setDraft({ ...EMPTY_RULE, ...rule, segments: asArray(rule.segments) });
    setOrigin(null); setPreview(null); setError(""); setNotice("");
  }
  function validate() {
    if (!draft.title.trim() || !draft.question.trim() || !draft.normalized_question.trim()) return "제목, 원본 질문, 정규화 질문을 입력하세요.";
    const segments = asArray(draft.segments).filter(row => SEGMENT_COLUMNS.some(name => text(row[name]).trim()));
    const incomplete = segments.find(row => !text(row.text).trim() || !SEGMENT_KINDS.includes(text(row.kind).trim()) || !text(row.value).trim());
    if (incomplete) return `세그먼트의 text·kind·value를 채우고 kind를 ${SEGMENT_KINDS.join(" / ")} 중 하나로 입력하세요.`;
    if (segments.some(row => !draft.question.toLowerCase().includes(text(row.text).trim().toLowerCase()))) return "각 세그먼트의 질문 표현은 원본 질문 또는 템플릿에 실제로 포함되어야 합니다.";
    return "";
  }
  async function save() {
    const problem = validate();
    if (problem) { setError(problem); return; }
    setBusy(true); setError(""); setNotice("");
    try {
      const segments = asArray(draft.segments).filter(row => SEGMENT_COLUMNS.some(name => text(row[name]).trim())).map(row => Object.fromEntries(SEGMENT_COLUMNS.map(name => [name, text(row[name]).trim()])));
      const body = { ...draft, id: draft.id || undefined, title: draft.title.trim(), question: draft.question.trim(), normalized_question: draft.normalized_question.trim(), route: draft.route || "auto", enabled: !!draft.enabled, segments, notes: draft.notes.trim(), ...(origin || {}) };
      const saved = await post("/routing/rules", body);
      setData(await sf(`${API}/routing`));
      setDraft({ ...EMPTY_RULE, ...(saved.rule || body) }); setOrigin(null);
      setNotice("라우팅 규칙을 저장했습니다.");
    } catch (e) { setError(e.message || "규칙을 저장하지 못했습니다."); }
    finally { setBusy(false); }
  }
  async function remove(rule) {
    if (!window.confirm(`'${rule.title}' 규칙을 삭제하시겠습니까?`)) return;
    setBusy(true); setError(""); setNotice("");
    try { await post("/routing/rules/delete", { id: rule.id }); setData(await sf(`${API}/routing`)); if (draft.id === rule.id) setDraft(EMPTY_RULE); setNotice("규칙을 삭제했습니다."); }
    catch (e) { setError(e.message || "규칙을 삭제하지 못했습니다."); }
    finally { setBusy(false); }
  }
  async function toggle(rule) {
    setBusy(true); setError(""); setNotice("");
    try { await post("/routing/rules", { ...rule, enabled: !rule.enabled }); setData(await sf(`${API}/routing`)); if (draft.id === rule.id) setDraft(old => ({ ...old, enabled: !rule.enabled })); }
    catch (e) { setError(e.message || "사용 여부를 바꾸지 못했습니다."); }
    finally { setBusy(false); }
  }
  async function runPreview() {
    const question = (previewQuestion || draft.question).trim();
    if (!question || /\{(?:product|lot|item|step)\}/.test(question)) { setError("미리보기에는 변수를 실제 제품·랏 값으로 바꾼 질문을 입력하세요."); return; }
    setBusy(true); setError("");
    try { setPreview(await post("/routing/preview", { question })); }
    catch (e) { setError(e.message || "경로를 미리 볼 수 없습니다."); }
    finally { setBusy(false); }
  }

  const trace = responseTrace(selectedPair?.response);
  return <section className="fr-panel" aria-label="Flow-i 질문 라우팅 관리">
    <div className="fr-heading"><div><h2>Flow-i 질문 라우팅</h2><p>관리자가 질문을 확인해 경로 규칙을 큐레이션합니다. 정확히 같은 질문 또는 자리표시자가 있는 템플릿에만 매칭하며, 응답 결과는 저장·재사용하지 않습니다.</p></div><button type="button" onClick={reload} disabled={loading || busy}>새로고침</button></div>
    {error && <div className="fr-alert fr-error" role="alert">{error}</div>}
    {notice && <div className="fr-alert fr-success" role="status">{notice}</div>}
    {loading && <p>라우팅 정보 로딩 중…</p>}
    {data && <>
      <RoutingDiagram nodes={asArray(data.nodes)} edges={asArray(data.edges)} actions={asArray(data.actions)} maxQuestions={data.max_questions} maxCalls={data.max_llm_calls} />
      <p className="fr-storage">규칙 저장 위치: <code>{data.storage_path || "—"}</code></p>
      <div className="fr-columns">
        <section className="fr-card"><div className="fr-section-head"><h3>대화에서 질문 선택</h3><span>전체 사용자 · 최근 100개 대화</span></div>
          <input aria-label="대화 검색" value={search} onChange={e => setSearch(e.target.value)} placeholder="사용자·제목 검색" />
          <div className="fr-conversations">{filteredConversations.map(row => <button type="button" className={row.id === selectedConversation ? "selected" : ""} key={row.id} onClick={() => { setSelectedConversation(row.id); setError(""); }}><b>{row.title || "새 대화"}</b><small>{row.username || "사용자 미상"} · {timeLabel(row.updated_at)}</small></button>)}{filteredConversations.length === 0 && <p>대화가 없습니다.</p>}</div>
          {selectedConversation && !detail && <p>대화 내용을 불러오는 중…</p>}
          {detail && <div className="fr-questions">{questions.map(pair => <button type="button" className={pair.message.id === selectedMessage ? "selected" : ""} key={pair.message.id} onClick={() => startFromQuestion(pair)}><b>{pair.message.content}</b><small>{pair.response ? "응답 있음" : "응답 없음"}</small></button>)}{questions.length === 0 && <p>사용자 질문이 없습니다.</p>}</div>}
        </section>
        <section className="fr-card"><div className="fr-section-head"><h3>선택한 질문과 응답</h3><span>{detail?.username || ""}</span></div>
          {!selectedPair && <p>왼쪽에서 질문을 선택하세요. 성공한 응답의 action이 있으면 경로 초안에 제안합니다.</p>}
          {selectedPair && <div className="fr-response"><div><strong>질문</strong><p>{selectedPair.message.content}</p></div><div><strong>인접 응답</strong><p>{selectedPair.response?.content || selectedPair.response?.error || "응답 없음"}</p></div>
            {trace.action && <p>응답 action: <code>{trace.action}</code></p>}
            <TraceCard title="routing_trace" value={trace.routing} /><TraceCard title="execution_trace" value={trace.execution} />
            {trace.parts.map((part, index) => <div className="fr-subtrace" key={index}><b>하위 질문 {index + 1}: {text(part.question || part.subquestion || "")}</b><TraceCard title="routing_trace" value={part.routing_trace || part.response?.routing_trace || part.tool?.routing_trace} /><TraceCard title="execution_trace" value={part.execution_trace || part.response?.execution_trace || part.tool?.execution_trace} /><pre>{pretty(part)}</pre></div>)}
          </div>}
        </section>
      </div>
      <section className="fr-card fr-editor"><div className="fr-section-head"><h3>{draft.id ? "규칙 수정" : "새 규칙"}</h3><button type="button" onClick={() => { setDraft(EMPTY_RULE); setOrigin(null); setPreview(null); setError(""); }}>새 규칙 작성</button></div>
        <div className="fr-form-grid"><label>제목<input value={draft.title} onChange={e => update("title", e.target.value)} placeholder="예: Lot의 특정 Item 추이" /></label><label>실행 경로<select value={draft.route} onChange={e => update("route", e.target.value)}><option value="auto">auto · 일반 판단</option>{asArray(data.actions).filter(action => action.id !== "auto").map(action => <option key={action.id} value={action.id}>{action.id} · {action.description}</option>)}</select></label></div>
        <label>원본 질문<input value={draft.question} onChange={e => update("question", e.target.value)} placeholder="대화에서 선택하거나 직접 입력" /></label>
        <label>정규화 질문 · 템플릿<input value={draft.normalized_question} onChange={e => update("normalized_question", e.target.value)} placeholder="예: {product}의 {lot}에서 {item} 추이는?" /></label>
        <p className="fr-hint">자리표시자: <code>{"{product}"}</code> <code>{"{lot}"}</code> <code>{"{item}"}</code> <code>{"{step}"}</code>. 원본과 교정 질문에 같은 변수를 유지하세요. 질문 전체가 정확히 일치할 때만 적용하며 실행 결과는 캐시하지 않습니다.</p>
        <div><b>질문 세그먼트</b><p className="fr-hint">text는 질문에 나온 표현, kind는 {SEGMENT_KINDS.join(" / ")}, value는 교정할 표현입니다. source는 참조·표현 교정용 표시이며 실제 DB를 읽었다는 증거가 아닙니다. 실제 조회 출처는 아래 응답 실행 흔적에서 확인하세요. 셀에서 Excel/Sheets 범위를 붙여넣을 수 있습니다.</p><SpreadsheetPasteGrid columns={SEGMENT_COLUMNS} rows={normalizeSpreadsheetRows(draft.segments, SEGMENT_COLUMNS, { minRows: 10, maxRows: 20 })} onChange={rows => update("segments", rows)} ariaLabel="라우팅 질문 세그먼트" columnLabels={{ text: "질문 표현 text", kind: "종류 kind", value: "교정 표현 value" }} placeholders={{ text: "예: ABC", kind: "product", value: "ABC" }} minRows={10} maxRows={20} maxHeight={340} minTableWidth={500} /></div>
        <label>관리자 메모<textarea rows={3} value={draft.notes} onChange={e => update("notes", e.target.value)} placeholder="경로를 선택한 이유와 확인할 사항" /></label>
        <label className="fr-check"><input type="checkbox" checked={!!draft.enabled} onChange={e => update("enabled", e.target.checked)} /> 규칙 사용</label>
        <label>매칭 확인용 실제 질문<input value={previewQuestion} onChange={e => { setPreviewQuestion(e.target.value); setPreview(null); }} placeholder="변수를 실제 값으로 바꾼 질문을 입력하세요. DB 조회 없이 매칭만 확인합니다." /></label>
        <div className="fr-actions"><button type="button" onClick={runPreview} disabled={busy}>저장된 활성 규칙으로 미리보기</button><button type="button" className="primary" onClick={save} disabled={busy}>{busy ? "처리 중…" : "규칙 저장"}</button></div>
        {preview && <div className="fr-preview" role="status"><b>미리보기</b><p>{preview.ambiguous ? "여러 규칙이 겹칩니다. 질문이나 템플릿을 구체화하세요." : preview.matched ? "일치하는 규칙이 있습니다." : "일치하는 규칙이 없습니다."}</p><dl><dt>정규화</dt><dd>{preview.normalized_question || "—"}</dd><dt>선택 규칙</dt><dd>{preview.rule?.title || preview.rule?.id || "—"}</dd><dt>바인딩</dt><dd><code>{pretty(preview.bindings || {})}</code></dd>{preview.ambiguous && <><dt>중복 후보</dt><dd>{asArray(preview.candidates).map(row => row.title || row.id).join(", ") || "—"}</dd></>}</dl></div>}
      </section>
      <section className="fr-card"><div className="fr-section-head"><h3>저장된 규칙 ({asArray(data.rules).length})</h3></div><div className="fr-rules">{asArray(data.rules).map(rule => <div className="fr-rule" key={rule.id}><div><b>{rule.title}</b><small>{rule.question}</small><small><code>{rule.normalized_question}</code> → {rule.route || "auto"} · {timeLabel(rule.updated_at)}</small></div><div className="fr-rule-actions"><label><input type="checkbox" checked={!!rule.enabled} disabled={busy} onChange={() => toggle(rule)} /> 사용</label><button type="button" onClick={() => editRule(rule)}>편집</button><button type="button" onClick={() => remove(rule)} disabled={busy}>삭제</button></div></div>)}{asArray(data.rules).length === 0 && <p>저장된 규칙이 없습니다.</p>}</div></section>
    </>}
  </section>;
}
