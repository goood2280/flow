import { useEffect, useRef, useState } from "react";
import { sf } from "../../lib/api";
import { Button, Input, Select, Textarea, Banner } from "../../components/ui";

const API = "/api/product-semantics";
const post = (path, body) => sf(`${API}${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const aliases = (text) => text.split(/[,\n]/).map((x) => x.trim()).filter(Boolean);
const pairKey = (row) => JSON.stringify([row.module, row.source_type, row.step_id, row.item_id]);

export default function ProductSemanticPanel({ product: fixedProduct = "", admin = false, refreshKey = 0 }) {
  const [catalog, setCatalog] = useState(null);
  const [product, setProduct] = useState(fixedProduct);
  const [data, setData] = useState(null);
  const [text, setText] = useState("");
  const [aliasText, setAliasText] = useState("");
  const [editing, setEditing] = useState(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const generation = useRef(0);
  async function reload(name = product) {
    const ticket = generation.current;
    if (!name) return;
    const value = await sf(`${API}/product?product=${encodeURIComponent(name)}`);
    if (ticket !== generation.current) return;
    setData(value);
    setAliasText((value.product_aliases?.[0]?.aliases || []).join(", "));
  }
  useEffect(() => { if (admin) sf(`${API}/catalog`).then(setCatalog).catch((e) => setMessage(e.message)); }, [admin]);
  useEffect(() => { setProduct(fixedProduct); }, [fixedProduct]);
  useEffect(() => {
    generation.current += 1; setEditing(null); setData(null); setText(""); setMessage(""); setBusy(false);
    reload(product).catch((e) => setMessage(e.message));
    return () => { generation.current += 1; };
  }, [product, refreshKey]);
  async function act(fn) {
    if (busy) return;
    setBusy(true); setMessage(""); const ticket = generation.current;
    try { await fn(); } catch (e) { if (ticket === generation.current) setMessage(e.message); }
    finally { if (ticket === generation.current) setBusy(false); }
  }
  function editField(group, index, values) {
    setEditing((old) => ({ ...old, draft: { ...old.draft, [group]: old.draft[group].map((row, i) => i === index ? { ...row, ...values } : row) } }));
  }
  function removeField(group, index) {
    setEditing((old) => ({ ...old, draft: { ...old.draft, [group]: old.draft[group].filter((_, i) => i !== index) } }));
  }
  function confirmedDraft() {
    return Object.fromEntries(Object.entries(editing.draft).map(([group, rows]) => [group, rows.map(({ aliases_text, ...row }) => ({ ...row, aliases: aliases_text === undefined ? row.aliases || [] : aliases(aliases_text) }))]));
  }
  const measurements = data?.measurements || [];
  const modules = [...new Set([...(data?.steps || []), ...measurements].map((row) => row.module).filter(Boolean))];
  const records = data?.records || [];
  return <section className="pw-semantic" aria-label="제품 용어와 구조 연결" style={{ border: "1px solid var(--border)", borderRadius: 10, padding: 18, display: "grid", gap: 12 }}>
    <h3 style={{ margin: 0 }}>제품 · Inline 별칭과 모듈 / 세부 구조</h3>
    <p className="pw-muted">기록 원문과 해석 초안을 보존합니다. 실제 Step·Item 연결은 확인 후 공용 지식에 반영하며, 홈 Flow-i에서도 같은 연결을 참고합니다.</p>
    {admin && <>
      <Button disabled={busy} onClick={() => act(async () => {
        const value = await post("/bootstrap", {}); setCatalog(value); await reload();
        setMessage(value.warning || "실제 DB 제품·모듈·Step·Inline 아이템을 확인했습니다.");
      })}>{busy ? "처리 중…" : "실제 DB에서 초기 Semantic 생성"}</Button>
      <small>FAB/INLINE 파일의 식별 열을 제한된 범위로 읽습니다. 측정값은 읽지 않으며, 표본에서 빠진 항목은 추측하지 않습니다.</small>
      {catalog?.generated_at && <span>기준 생성: {new Date(catalog.generated_at).toLocaleString()} · 제품 {catalog.product_names?.length || 0}개</span>}
      {!fixedProduct && <label>제품<Select value={product} disabled={busy} onChange={(e) => setProduct(e.target.value)}>
        <option value="">실제 제품 선택</option>{(catalog?.product_names || []).map((p) => <option key={p}>{p}</option>)}
      </Select></label>}
    </>}
    {message && <Banner tone="info"><span role="status">{message}</span></Banner>}
    {product && <>
      {admin && <>
        <label>제품 별칭 모음<Input value={aliasText} disabled={busy} onChange={(e) => setAliasText(e.target.value)} placeholder="쉼표로 구분한 별칭" /></label>
        <Button disabled={busy} onClick={() => act(async () => { await post("/product-aliases", { product, aliases: aliases(aliasText) }); await reload(); setMessage("제품 별칭을 저장했습니다. 중복 별칭은 홈에서 확인 질문으로 처리합니다."); })}>제품 별칭 저장</Button>
        <label>Inline 아이템 · 모듈과 하위 구조 설명<Textarea value={text} onChange={(e) => setText(e.target.value)} rows={4} maxLength={40000} disabled={busy}
          placeholder="PC CD1의 별칭은 … / SD 안에 eSD, eSiGe가 있고 eSD의 Step 범위는 … 처럼 적어 주세요. 제품은 위에서 선택한 제품입니다." /></label>
        <Button disabled={busy || !text.trim()} onClick={() => act(async () => {
          const record = await post("/propose", { product, text }); await reload(); setEditing(record); setText("");
          setMessage(record.warning || "원문을 저장하고 연결 초안을 만들었습니다. 아래에서 실제 ID와 별칭을 확인해 주세요.");
        })}>설명 저장 · LLM 구조화</Button>
      </>}
      <details><summary>관찰된 제품 공정 순서 · Inline 아이템 ({data?.steps?.length || 0} Step / {measurements.length} 조합)</summary>
        <div style={{ maxHeight: 250, overflow: "auto" }}><table><thead><tr><th>모듈</th><th>Step</th><th>공정 설명</th><th>Item</th><th>아이템 설명</th></tr></thead><tbody>
          {(measurements.length ? measurements : data?.steps || []).map((row, i) => <tr key={i}><td>{row.module}</td><td>{row.step_id}</td><td>{row.step_desc}</td><td>{row.item_id || "—"}</td><td>{row.item_desc}</td></tr>)}
        </tbody></table></div>
      </details>
      {!data?.generated_at && <p>관리자에서 초기 Semantic을 생성하면 실제 Step·Item을 선택할 수 있습니다. 원문 기록은 먼저 저장할 수 있습니다.</p>}
      {records.length > 0 && <div style={{ display: "grid", gap: 8 }}>{records.map((record) => <details key={record.id}>
        <summary>{record.status === "confirmed" ? "연결 확인됨" : "연결 확인 필요"} · {record.source_text.slice(0, 100)}</summary>
        <p style={{ whiteSpace: "pre-wrap" }}>{record.source_text}</p>
        {record.warning && <p>{record.warning}</p>}
        {(record.draft.measurements || []).map((r, i) => <p key={`m${i}`}><b>{r.term}</b> {r.aliases?.length ? `(${r.aliases.join(", ")})` : ""} → {r.module} / {r.source_type} / {r.step_id || "Step 확인 필요"} / {r.item_id || "Item 확인 필요"}</p>)}
        {(record.draft.structures || []).map((r, i) => <p key={`s${i}`}><b>{r.module} / {r.path}</b> {r.aliases?.join(", ")} → {r.step_start || "시작 확인"} ~ {r.step_end || "끝 확인"}</p>)}
        {record.status === "pending" && <Button disabled={busy} onClick={() => setEditing(JSON.parse(JSON.stringify(record)))}>연결 · 별칭 확인</Button>}
      </details>)}</div>}
      {editing && <div style={{ display: "grid", gap: 12, borderTop: "1px solid var(--border)", paddingTop: 12 }}>
        <h4>연결 확인: {editing.source_text.slice(0, 80)}</h4>
        {(editing.draft.measurements || []).map((row, i) => <fieldset key={`m${i}`} style={{ display: "grid", gap: 8 }}>
          <legend>Inline / 측정 용어 {i+1}</legend>
          <label>용어<Input value={row.term} onChange={(e) => editField("measurements", i, { term: e.target.value })} /></label>
          <label>별칭<Input value={row.aliases_text ?? (row.aliases || []).join(", ")} onChange={(e) => editField("measurements", i, { aliases_text: e.target.value })} /></label>
          <label>실제 모듈 · Step · Item<Select value={pairKey(row)} onChange={(e) => { const value = measurements.find((r) => pairKey(r) === e.target.value); if (value) editField("measurements", i, { module: value.module, source_type: value.source_type, step_id: value.step_id, item_id: value.item_id }); }}>
            <option value="">연결할 실제 항목을 선택하세요</option>{measurements.filter((r) => r.item_id).map((r, j) => <option key={j} value={pairKey(r)}>{r.source_type} · {r.module} · {r.step_id} {r.step_desc} · {r.item_id} {r.item_desc}</option>)}
          </Select></label>
          <Button disabled={busy} onClick={() => removeField("measurements", i)}>이 연결 제외</Button>
        </fieldset>)}
        {(editing.draft.structures || []).map((row, i) => {
          const steps = (data?.steps || []).filter((r) => r.module === row.module);
          return <fieldset key={`s${i}`} style={{ display: "grid", gap: 8 }}><legend>모듈 / 세부 구조 {i+1}</legend>
            <label>모듈<Select value={row.module} onChange={(e) => editField("structures", i, { module: e.target.value, step_start: "", step_end: "" })}><option value="">모듈 선택</option>{modules.map((m) => <option key={m}>{m}</option>)}</Select></label>
            <label>하위 구조 경로<Input value={row.path} placeholder="eSD / eSiGe 등, 계층은 / 로 구분" onChange={(e) => editField("structures", i, { path: e.target.value })} /></label>
            <label>별칭<Input value={row.aliases_text ?? (row.aliases || []).join(", ")} onChange={(e) => editField("structures", i, { aliases_text: e.target.value })} /></label>
            {[['step_start', '시작 Step'], ['step_end', '끝 Step']].map(([key, label]) => <label key={key}>{label}<Select value={row[key]} onChange={(e) => editField("structures", i, { [key]: e.target.value })}><option value="">실제 Step 선택</option>{steps.map((r,j) => <option key={j} value={r.step_id}>{r.step_id} {r.step_desc}</option>)}</Select></label>)}
            <Button disabled={busy} onClick={() => removeField("structures", i)}>이 연결 제외</Button>
          </fieldset>;
        })}
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <Button disabled={busy} onClick={() => setEditing((old) => ({ ...old, draft: { ...old.draft, measurements: [...old.draft.measurements, { term: "", aliases: [], module: "", source_type: "INLINE", step_id: "", item_id: "" }] } }))}>측정 용어 추가</Button>
          <Button disabled={busy} onClick={() => setEditing((old) => ({ ...old, draft: { ...old.draft, structures: [...old.draft.structures, { module: "", path: "", aliases: [], step_start: "", step_end: "" }] } }))}>하위 구조 추가</Button>
          <Button variant="primary" disabled={busy} onClick={() => act(async () => { await post("/confirm", { product, id: editing.id, draft: confirmedDraft() }); setEditing(null); await reload(); setMessage("연결을 확인했습니다. 제품 Wiki와 홈 Flow-i에서 같은 지식을 참고합니다."); })}>확인한 연결 저장</Button>
          <Button disabled={busy} onClick={() => setEditing(null)}>닫기</Button>
        </div>
      </div>}
    </>}
  </section>;
}
