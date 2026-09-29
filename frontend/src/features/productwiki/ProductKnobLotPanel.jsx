import { useCallback, useEffect, useMemo, useState } from "react";
import SpreadsheetPasteGrid, { normalizeSpreadsheetRows } from "../../components/SpreadsheetPasteGrid";
import { Banner, Button, Input, SegmentedSwitch, Select, Textarea } from "../../components/ui";
import { sf } from "../../lib/api";
import "./ProductKnobLot.css";

// 제품 위키의 개선 knob 레지스트리와 LOT 목적 표.
// knob 은 "소구조물 → Step set" 으로 묶어 보여 주고, 여러 step 을 한 code 로 바꾸는
// knob 은 step 들을 한 set 으로 다룬다. 서버가 step 별 PPID 의 ppid_knob 등록 여부와
// 보상 관계 경로(A → nRch 저하 → B 보상)를 계산해 내려준다.

const API = "/api/product-wiki";
const post = (path, body) => sf(`${API}${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

const POR_LABELS = { idea: "아이디어", experiment: "평가 중", validated: "효과 검증", por: "POR 반영", sop: "SOP 반영", dropped: "중단" };
const AXES = [["yield", "수율"], ["performance", "성능"], ["reliability", "신뢰성"]];
const EFFECTS = [["", "-"], ["up", "개선 ▲"], ["down", "저하 ▼"], ["neutral", "영향 없음"], ["mixed", "혼재"], ["unknown", "미확인"]];
const EFFECT_SHORT = { up: "▲", down: "▼", neutral: "=", mixed: "±", unknown: "?" };
const PPID_LABELS = {
  registered: "등록",
  step_only: "PPID 미등록",
  ppid_elsewhere: "다른 step에만 등록",
  step_unmapped: "Step 매칭 없음",
  no_ppid: "PPID 없음",
};
const STEP_COLUMNS = ["step_id", "ppid", "change"];
const MATCH_CLASS = { name: "is-name", alias: "is-alias", ppid: "is-ppid", none: "is-none" };
const LOT_COLUMNS = ["lot_id", "purpose", "knobs", "status", "result", "owner", "note", "lot_management_purpose"];
const LOT_LABELS = {
  lot_id: "LOT ID", purpose: "목적", knobs: "knob (쉼표 구분)", status: "상태", result: "결과",
  owner: "담당", note: "메모", lot_management_purpose: "랏 관리 purpose (참고)",
};
const LOT_STATUS_LABELS = { planned: "계획", running: "진행", done: "완료", hold: "보류", scrapped: "폐기" };

function newKnob() {
  const id = `k_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
  return {
    id, name: "", code: "", aliases: [], structure: "", steps: [], purpose: "",
    impacts: { yield: { effect: "", note: "" }, performance: { effect: "", note: "" }, reliability: { effect: "", note: "" } },
    side_effects: "", compensates: [], por_status: "idea", por_ref: "", por_date: "", lot_ids: [], note: "",
  };
}

function withImpacts(knob) {
  const impacts = {};
  AXES.forEach(([axis]) => { impacts[axis] = { effect: "", note: "", ...(knob.impacts?.[axis] || {}) }; });
  return { ...newKnob(), ...knob, impacts };
}

function PorBadge({ status }) {
  return <span className={`pk-por is-${status || "idea"}`}>{POR_LABELS[status] || status}</span>;
}

function ImpactChips({ impacts }) {
  return (
    <span className="pk-impacts">
      {AXES.map(([axis, label]) => {
        const effect = impacts?.[axis]?.effect || "";
        return <span key={axis} className={`pk-impact is-${effect || "none"}`} title={impacts?.[axis]?.note || ""}>{label} {EFFECT_SHORT[effect] || "-"}</span>;
      })}
    </span>
  );
}

function KnobRegistry({ product, structurePaths }) {
  const [doc, setDoc] = useState(null);
  const [draft, setDraft] = useState([]);
  const [selectedId, setSelectedId] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    setError("");
    return sf(`${API}/knobs?product=${encodeURIComponent(product)}`)
      .then((data) => { setDoc(data); setDraft((data.knobs || []).map(withImpacts)); })
      .catch((err) => setError(err.message));
  }, [product]);
  useEffect(() => { setSelectedId(""); load(); }, [load]);

  const dirty = useMemo(() => JSON.stringify(draft) !== JSON.stringify((doc?.knobs || []).map(withImpacts)), [draft, doc]);
  const byId = useMemo(() => Object.fromEntries(draft.map((k) => [k.id, k])), [draft]);
  const selected = byId[selectedId] || null;
  const checks = doc?.ppid_checks || {};

  const update = (patch) => setDraft((list) => list.map((k) => (k.id === selectedId ? { ...k, ...patch } : k)));
  const updateImpact = (axis, patch) => update({ impacts: { ...selected.impacts, [axis]: { ...selected.impacts[axis], ...patch } } });

  const add = () => { const k = newKnob(); setDraft((list) => [...list, k]); setSelectedId(k.id); };
  const remove = () => {
    if (!selected) return;
    setDraft((list) => list.filter((k) => k.id !== selected.id).map((k) => ({ ...k, compensates: k.compensates.filter((c) => c !== selected.id) })));
    setSelectedId("");
  };

  const save = async () => {
    setBusy(true); setError(""); setNotice("");
    try {
      const knobs = draft.map((k) => ({ ...k, steps: (k.steps || []).filter((s) => STEP_COLUMNS.some((c) => String(s[c] || "").trim())) }));
      const data = await post("/knobs", { product, expected_revision: doc?.revision || 0, knobs });
      setDoc(data); setDraft((data.knobs || []).map(withImpacts));
      setNotice(`저장했습니다 (rev ${data.revision}). PPID 등록 확인이 갱신됐습니다.`);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  // 저장 전 draft 기준으로 묶어 보여 준다(서버 knob_groups 와 같은 규칙).
  // 코드가 있는 knob 은 같은 코드끼리 step 을 합쳐 하나의 코드 set, 코드가 없으면 step 별로 따로 인식한다.
  const groups = useMemo(() => {
    const map = new Map();
    const codeHome = new Map();
    const bucket = (structure) => {
      if (!map.has(structure)) map.set(structure, new Map());
      return map.get(structure);
    };
    draft.forEach((k) => {
      const structure = k.structure || "(소구조물 미지정)";
      const code = String(k.code || "").trim();
      const stepIds = [...new Set((k.steps || []).map((s) => String(s.step_id || "").trim()).filter(Boolean))].sort();
      if (code) {
        const codeKey = code.toLowerCase();
        if (!codeHome.has(codeKey)) codeHome.set(codeKey, structure);
        const sets = bucket(codeHome.get(codeKey));
        const key = `code:${codeKey}`;
        if (!sets.has(key)) sets.set(key, { kind: "code", code, stepIds: [], knobs: [] });
        const set = sets.get(key);
        set.stepIds = [...new Set([...set.stepIds, ...stepIds])].sort();
        set.knobs.push(k);
        return;
      }
      const sets = bucket(structure);
      (stepIds.length ? stepIds : [""]).forEach((stepId) => {
        const key = `step:${stepId.toLowerCase()}`;
        if (!sets.has(key)) sets.set(key, { kind: "step", code: "", stepIds: stepId ? [stepId] : [], knobs: [] });
        const set = sets.get(key);
        if (!set.knobs.includes(k)) set.knobs.push(k);
      });
    });
    const label = (set) => `${set.kind === "code" ? `${set.code} · ` : ""}${set.stepIds.join(" + ") || "(Step 미지정)"}`;
    return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(([structure, sets]) => ({
      structure,
      sets: [...sets.values()]
        .sort((a, b) => (a.kind === b.kind ? label(a).localeCompare(label(b)) : a.kind === "code" ? -1 : 1))
        .map((set) => [label(set), set]),
    }));
  }, [draft]);
  const needsCode = (k) => !String(k.code || "").trim() && new Set((k.steps || []).map((s) => String(s.step_id || "").trim()).filter(Boolean)).size > 1;
  const matches = doc?.feature_matches || {};

  const summary = doc?.ppid_summary || { steps: 0, registered: 0, missing: 0 };
  return (
    <div className="pk-registry">
      {error && <Banner tone="danger"><span>{error}</span></Banner>}
      {notice && <Banner tone="info"><span>{notice}</span></Banner>}
      <div className="pk-toolbar">
        <span className="pk-chip">knob {draft.length}</span>
        {Object.entries(doc?.por_counts || {}).filter(([, n]) => n).map(([status, n]) => <span key={status} className="pk-chip"><PorBadge status={status} /> {n}</span>)}
        <span className="pk-chip" title={`${doc?.rulebook?.knob_file || "ppid_knob.csv"} 규칙 ${doc?.rulebook?.rules || 0}개 · ${doc?.rulebook?.step_file || "step 매칭"}`}>
          PPID 등록 {summary.registered}/{summary.steps}{summary.missing ? ` · 확인 필요 ${summary.missing}` : ""}
        </span>
        <span className="pk-spacer" />
        <Button size="sm" variant="secondary" onClick={add}>새 knob</Button>
        <Button size="sm" variant="primary" onClick={save} disabled={!dirty || busy}>{busy ? "저장 중…" : "저장"}</Button>
      </div>

      {(doc?.chains || []).length > 0 && (
        <section className="pk-chains" aria-label="보상 관계">
          <h4>부작용 → 보상 흐름</h4>
          {doc.chains.map((chain, i) => (
            <div key={i} className="pk-chain">
              {chain.map((node, j) => (
                <span key={node.id} className="pk-chain-node">
                  {j > 0 && <span className="pk-chain-arrow">→ 보상</span>}
                  <button type="button" className="pk-link" onClick={() => setSelectedId(node.id)}>{node.name}</button>
                  <PorBadge status={node.por_status} />
                  {node.side_effects && <span className="pk-side">({node.side_effects})</span>}
                </span>
              ))}
            </div>
          ))}
        </section>
      )}

      <div className="pk-layout">
        <div className="pk-groups">
          {groups.length === 0 && <p className="pk-empty">등록된 knob이 없습니다. ‘새 knob’으로 추가하세요.</p>}
          {groups.map((group) => (
            <section key={group.structure} className="pk-group">
              <h4>{group.structure}</h4>
              {group.sets.map(([setKey, set]) => (
                <div key={setKey} className="pk-stepset">
                  <div className="pk-stepset-head">
                    {set.kind === "code" && <span className="pk-set-tag">{set.stepIds.length > 1 ? "코드 set" : "코드"}</span>}
                    <code>{setKey}</code>
                  </div>
                  {set.knobs.map((k) => {
                    const stepChecks = checks[k.id] || [];
                    const bad = stepChecks.filter((c) => c.status !== "registered").length;
                    return (
                      <button key={k.id} type="button" className={`pk-knob${k.id === selectedId ? " is-selected" : ""}`} onClick={() => setSelectedId(k.id)}>
                        <span className="pk-knob-name">{k.name || "(이름 없음)"}{k.code ? <code> {k.code}</code> : null}</span>
                        <PorBadge status={k.por_status} />
                        <ImpactChips impacts={k.impacts} />
                        {stepChecks.length > 0 && <span className={`pk-ppid ${bad ? "is-bad" : "is-ok"}`}>{bad ? `PPID 확인 ${bad}` : "PPID 등록"}</span>}
                        {matches[k.id] && (
                          <span className={`pk-match ${MATCH_CLASS[matches[k.id].status] || ""}`}
                            title={matches[k.id].feature ? `ppid_knob: ${matches[k.id].feature}` : (matches[k.id].suggestions || []).length ? `추천: ${matches[k.id].suggestions.join(", ")}` : "비슷한 feature 이름 없음"}>
                            {matches[k.id].label}
                          </span>
                        )}
                        {needsCode(k) && <span className="pk-set-hint">코드를 입력하면 set로 묶입니다</span>}
                        {k.compensates.length > 0 && <span className="pk-side">보상: {k.compensates.map((c) => byId[c]?.name || c).join(", ")}</span>}
                      </button>
                    );
                  })}
                </div>
              ))}
            </section>
          ))}
        </div>

        {selected && (
          <form className="pk-editor" onSubmit={(e) => { e.preventDefault(); save(); }}>
            <div className="pk-grid2">
              <label>knob 이름<Input value={selected.name} onChange={(e) => update({ name: e.target.value })} placeholder="예: CA deep etch" /></label>
              <label>knob code<Input value={selected.code} onChange={(e) => update({ code: e.target.value })} placeholder="같은 코드끼리 한 set으로 묶입니다" />
                {needsCode(selected) && <span className="pk-set-hint">코드를 입력하면 set로 묶입니다 (지금은 step별로 따로 인식)</span>}
              </label>
              <label>별칭 (쉼표 구분 · ppid_knob feature 이름)
                <Input value={(selected.aliases || []).join(", ")}
                  onChange={(e) => update({ aliases: e.target.value.split(/[,;]/).map((a) => a.trim()).filter(Boolean) })}
                  placeholder="예: 10.0 CA_DEEP" />
                {matches[selected.id] && (
                  <span className={`pk-match ${MATCH_CLASS[matches[selected.id].status] || ""}`}>
                    {matches[selected.id].label}{matches[selected.id].feature ? ` · ${matches[selected.id].feature}` : ""}
                    {matches[selected.id].status === "none" && (matches[selected.id].suggestions || []).length > 0 && ` · 추천: ${matches[selected.id].suggestions.join(", ")}`}
                  </span>
                )}
              </label>
              <label>소구조물 (module/path)
                <Input list="pk-structure-paths" value={selected.structure} onChange={(e) => update({ structure: e.target.value })} placeholder="예: MOL/Contact/CA" />
                <datalist id="pk-structure-paths">{structurePaths.map((p) => <option key={p} value={p} />)}</datalist>
              </label>
              <label>POR/SOP 상태
                <Select value={selected.por_status} onChange={(e) => update({ por_status: e.target.value })}>
                  {Object.entries(POR_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </Select>
              </label>
              <label>POR/SOP 근거 (ECN·문서)<Input value={selected.por_ref} onChange={(e) => update({ por_ref: e.target.value })} /></label>
              <label>반영일<Input type="date" value={selected.por_date} onChange={(e) => update({ por_date: e.target.value })} /></label>
            </div>
            <label>목적<Textarea rows={2} value={selected.purpose} onChange={(e) => update({ purpose: e.target.value })} placeholder="이 knob을 넣는 이유" /></label>

            <div className="pk-impact-grid">
              {AXES.map(([axis, label]) => (
                <label key={axis}>{label} 영향
                  <Select value={selected.impacts[axis].effect} onChange={(e) => updateImpact(axis, { effect: e.target.value })}>
                    {EFFECTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                  </Select>
                  <Input value={selected.impacts[axis].note} onChange={(e) => updateImpact(axis, { note: e.target.value })} placeholder="근거·수치" />
                </label>
              ))}
            </div>

            <div className="pk-grid2">
              <label>부작용<Input value={selected.side_effects} onChange={(e) => update({ side_effects: e.target.value })} placeholder="예: nRch 저하" /></label>
              <label>평가 LOT (쉼표 구분)<Input value={selected.lot_ids.join(", ")} onChange={(e) => update({ lot_ids: e.target.value.split(/[,\s]+/).filter(Boolean) })} /></label>
            </div>

            <fieldset className="pk-compensates">
              <legend>이 knob이 보상하는 knob (부작용을 메우는 대상)</legend>
              {draft.filter((k) => k.id !== selected.id).map((k) => (
                <label key={k.id} className="pk-check">
                  <input type="checkbox" checked={selected.compensates.includes(k.id)}
                    onChange={(e) => update({ compensates: e.target.checked ? [...selected.compensates, k.id] : selected.compensates.filter((c) => c !== k.id) })} />
                  {k.name || k.id}{k.side_effects ? ` (${k.side_effects})` : ""}
                </label>
              ))}
              {draft.length < 2 && <span className="pk-empty">다른 knob이 없습니다.</span>}
            </fieldset>

            <div>
              <div className="pk-label">변경 Step (여러 행 = 한 Step set) — Excel에서 붙여넣을 수 있습니다</div>
              <SpreadsheetPasteGrid
                columns={STEP_COLUMNS}
                rows={normalizeSpreadsheetRows(selected.steps, STEP_COLUMNS, { minRows: 4, maxRows: 60 })}
                onChange={(rows) => update({ steps: rows })}
                ariaLabel="knob 변경 step"
                columnLabels={{ step_id: "Step ID", ppid: "PPID", change: "변경 내용" }}
                aliases={{ stepid: "step_id", recipe: "ppid", ppid_id: "ppid", 변경: "change" }}
                minRows={4}
                maxRows={60}
                maxHeight={220}
                minTableWidth={520}
              />
              {(checks[selected.id] || []).length > 0 && (
                <ul className="pk-checklist">
                  {(doc?.knobs || []).find((k) => k.id === selected.id)?.steps.map((s, i) => {
                    const c = checks[selected.id][i] || {};
                    return (
                      <li key={`${s.step_id}-${i}`} className={c.status === "registered" ? "is-ok" : "is-bad"}>
                        <code>{s.step_id}</code> · {s.ppid || "-"} — {PPID_LABELS[c.status] || c.status}
                        {c.features?.length ? ` (${c.features.join(", ")})` : ""}
                        {c.step_desc?.length ? ` · ${c.step_desc.join(", ")}` : ""}
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
            <label>메모<Textarea rows={2} value={selected.note} onChange={(e) => update({ note: e.target.value })} /></label>
            <div className="pk-editor-actions">
              <Button size="sm" variant="danger" type="button" onClick={remove}>knob 삭제</Button>
              <span className="pk-spacer" />
              <Button size="sm" variant="secondary" type="button" onClick={() => setSelectedId("")}>닫기</Button>
              <Button size="sm" variant="primary" type="submit" disabled={!dirty || busy}>저장</Button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}

function lotRowsFromDoc(doc) {
  return (doc?.lots || []).map((lot) => ({
    lot_id: lot.lot_id, purpose: lot.purpose, knobs: (lot.knob_names || []).join(", "),
    status: LOT_STATUS_LABELS[lot.status] || lot.status, result: lot.result, owner: lot.owner, note: lot.note,
    lot_management_purpose: lot.lot_management_purpose || "",
  }));
}

function LotTable({ product }) {
  const [doc, setDoc] = useState(null);
  const [rows, setRows] = useState([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => sf(`${API}/lots?product=${encodeURIComponent(product)}`)
    .then((data) => { setDoc(data); setRows(lotRowsFromDoc(data)); setError(""); })
    .catch((err) => setError(err.message)), [product]);
  useEffect(() => { load(); }, [load]);

  const filled = rows.filter((r) => LOT_COLUMNS.slice(0, 7).some((c) => String(r[c] || "").trim()));
  const dirty = JSON.stringify(filled.map(({ lot_management_purpose, ...r }) => r))
    !== JSON.stringify(lotRowsFromDoc(doc).map(({ lot_management_purpose, ...r }) => r));

  const save = async () => {
    setBusy(true); setError(""); setNotice("");
    try {
      const lots = filled.map(({ lot_management_purpose, ...r }) => r);
      const data = await post("/lots", { product, expected_revision: doc?.revision || 0, lots });
      setDoc(data); setRows(lotRowsFromDoc(data));
      setNotice(`저장했습니다 (rev ${data.revision}).`);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const addSuggested = (item) => setRows((list) => [...list.filter((r) => String(r.lot_id || "").trim()),
    { lot_id: item.lot_id, purpose: "", knobs: item.knob_names.join(", "), status: "계획", result: "", owner: "", note: "", lot_management_purpose: "" }]);

  return (
    <div className="pk-lots">
      {error && <Banner tone="danger"><span>{error}</span></Banner>}
      {notice && <Banner tone="info"><span>{notice}</span></Banner>}
      <div className="pk-toolbar">
        <span className="pk-chip">LOT {filled.length}</span>
        <span className="pk-hint">상태: {Object.values(LOT_STATUS_LABELS).join(" / ")} · knob은 이름 또는 ID를 쉼표로 · 랏 관리 purpose 열은 읽기 전용</span>
        <span className="pk-spacer" />
        <Button size="sm" variant="primary" onClick={save} disabled={!dirty || busy}>{busy ? "저장 중…" : "저장"}</Button>
      </div>
      {(doc?.suggested || []).length > 0 && (
        <div className="pk-suggested">
          <span>knob에 적힌 평가 LOT 중 표에 없는 것:</span>
          {doc.suggested.map((item) => (
            <button key={item.lot_id} type="button" className="pk-link" onClick={() => addSuggested(item)} title={item.knob_names.join(", ")}>+ {item.lot_id}</button>
          ))}
        </div>
      )}
      <SpreadsheetPasteGrid
        columns={LOT_COLUMNS}
        rows={normalizeSpreadsheetRows(rows, LOT_COLUMNS, { minRows: 10, maxRows: 3000 })}
        onChange={setRows}
        ariaLabel="제품 LOT 목적 표"
        columnLabels={LOT_LABELS}
        aliases={{ root_lot_id: "lot_id", lot: "lot_id", 목적: "purpose", knob: "knobs", 상태: "status", 결과: "result", 담당: "owner", 메모: "note" }}
        readOnlyColumns={["lot_management_purpose"]}
        minRows={10}
        maxRows={3000}
        maxHeight={365}
        minTableWidth={1100}
      />
    </div>
  );
}

export default function ProductKnobLotPanel({ product }) {
  const [view, setView] = useState("knobs");
  const [structurePaths, setStructurePaths] = useState([]);

  useEffect(() => {
    let alive = true;
    sf(`${API}/structure?product=${encodeURIComponent(product)}`)
      .then((data) => { if (alive) setStructurePaths((data.rows || []).map((r) => `${r.module}/${r.path}`)); })
      .catch(() => { if (alive) setStructurePaths([]); });
    return () => { alive = false; };
  }, [product]);

  return (
    <div className="pk-panel">
      <SegmentedSwitch
        value={view}
        onChange={setView}
        ariaLabel="knob · LOT 보기"
        options={[{ value: "knobs", label: "개선 knob" }, { value: "lots", label: "LOT 목적" }]}
      />
      {view === "knobs" ? <KnobRegistry key={product} product={product} structurePaths={structurePaths} /> : <LotTable key={product} product={product} />}
    </div>
  );
}
