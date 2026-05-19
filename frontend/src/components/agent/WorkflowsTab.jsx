// WorkflowsTab — Agent V2의 "워크플로우 템플릿" 항목 (M5).
// 반복 prompt 패턴을 declarative 템플릿으로 등록/수정/삭제하고, 임의 prompt에
// 대해 어떤 템플릿이 매치되는지 dry-run으로 미리 확인한다.
import { useEffect, useState } from "react";
import { sf, postJson, putJson } from "../../lib/api";
import { Banner, Button, EmptyState, Field } from "../UXKit";
import Loading from "../Loading";

const EMPTY_FORM = {
  key: "",
  title: "",
  prompt_contains: "",
  intent_in: "",
  slots_required: "",
  steps: "",
  shared: false,
};

export default function WorkflowsTab({ user }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [selected, setSelected] = useState("");
  const [form, setForm] = useState(EMPTY_FORM);
  const [creating, setCreating] = useState(false);
  const [testPrompt, setTestPrompt] = useState("PRODA A1000 GATE 모듈 인폼 작성해줘");
  const [testIntent, setTestIntent] = useState("inform");
  const [testResult, setTestResult] = useState(null);
  const [saving, setSaving] = useState(false);

  function reload() {
    setLoading(true);
    sf("/api/agent/workflows")
      .then((d) => setItems((d && d.items) || []))
      .catch((e) => setErr(e?.message || "워크플로우 목록 로딩 실패"))
      .finally(() => setLoading(false));
  }
  useEffect(() => { reload(); }, []);

  function pick(key) {
    setSelected(key);
    setCreating(false);
    const row = items.find((it) => it.key === key);
    if (row) {
      setForm({
        key: row.key,
        title: row.title || "",
        prompt_contains: (row.trigger?.prompt_contains || []).join(", "),
        intent_in: (row.trigger?.intent_in || []).join(", "),
        slots_required: (row.trigger?.slots_required || []).join(", "),
        steps: JSON.stringify(row.steps || [], null, 2),
        shared: !!row.shared,
      });
    }
  }

  async function save() {
    setSaving(true); setErr("");
    try {
      let steps = [];
      try { steps = JSON.parse(form.steps || "[]"); }
      catch (e) { setErr("steps는 JSON 배열이어야 합니다"); setSaving(false); return; }
      const payload = {
        key: form.key.trim(),
        title: form.title.trim(),
        trigger: {
          prompt_contains: splitCsv(form.prompt_contains),
          intent_in: splitCsv(form.intent_in),
          slots_required: splitCsv(form.slots_required),
        },
        steps,
        shared: !!form.shared,
      };
      const d = creating
        ? await postJson("/api/agent/workflows", payload)
        : await putJson(`/api/agent/workflows/${encodeURIComponent(payload.key)}`, payload);
      reload();
      setSelected(d.template?.key || payload.key);
      setCreating(false);
    } catch (e) { setErr(e?.message || "저장 실패"); }
    finally { setSaving(false); }
  }

  async function remove() {
    if (!selected) return;
    if (!confirm(`'${selected}' 템플릿을 삭제할까요?`)) return;
    try {
      await sf(`/api/agent/workflows/${encodeURIComponent(selected)}`, { method: "DELETE" });
      setSelected(""); setForm(EMPTY_FORM); reload();
    } catch (e) { setErr(e?.message || "삭제 실패"); }
  }

  async function runTest() {
    setTestResult(null);
    try {
      const d = await postJson("/api/agent/workflows/test", { prompt: testPrompt, intent: testIntent });
      setTestResult(d);
    } catch (e) { setErr(e?.message || "test 실행 실패"); }
  }

  function startCreate() {
    setCreating(true); setSelected("");
    setForm({ ...EMPTY_FORM, steps: "[]" });
  }

  return (
    <div style={{ padding: 20, display: "flex", flexDirection: "column", gap: 16 }}>
      <header>
        <h2 style={{ fontSize: 18, margin: "0 0 4px" }}>📐 워크플로우 템플릿</h2>
        <p style={{ fontSize: 12, color: "var(--text-secondary)", margin: 0 }}>
          반복되는 prompt 패턴을 declarative 템플릿으로 등록 — trigger(prompt_contains / intent_in)이 매치되면 미리 정한 unit AI step 시퀀스를 안내합니다. (실제 dispatcher 통합은 후속 PR)
        </p>
      </header>

      {err && <Banner tone="warn">{err}</Banner>}

      <section style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", borderRadius: 6, padding: 12 }}>
        <h3 style={subHead}>매치 테스트</h3>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <Field label="Prompt"><input value={testPrompt} onChange={(e) => setTestPrompt(e.target.value)} style={inputStyle({ minWidth: 280 })} /></Field>
          <Field label="intent (선택)"><input value={testIntent} onChange={(e) => setTestIntent(e.target.value)} style={inputStyle({ minWidth: 120 })} /></Field>
          <div style={{ alignSelf: "flex-end" }}><Button onClick={runTest}>매치 확인</Button></div>
        </div>
        {testResult && (
          <div style={{ marginTop: 10, fontSize: 12 }}>
            {testResult.matched ? (
              <div style={{ padding: 10, background: "var(--bg-primary)", border: "1px solid var(--border)", borderRadius: 4 }}>
                <span style={{ fontWeight: 700 }}>매치: </span>
                <code>{testResult.matched.key}</code> · <span style={{ color: "var(--text-secondary)" }}>{testResult.matched.title}</span>
                <pre style={preStyle}>{JSON.stringify(testResult.matched, null, 2)}</pre>
              </div>
            ) : (
              <Banner tone="info">매치되는 템플릿이 없습니다.</Banner>
            )}
          </div>
        )}
      </section>

      <section style={{ display: "grid", gridTemplateColumns: "260px 1fr", gap: 12, alignItems: "start" }}>
        <div style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", borderRadius: 6, padding: 8 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
            <h3 style={subHead}>등록된 템플릿 ({items.length})</h3>
            <Button onClick={startCreate}>+ 새로 만들기</Button>
          </div>
          {loading ? <Loading text="목록 로딩..." size="sm" /> : (
            items.length === 0 ? <EmptyState title="등록된 템플릿이 없습니다" hint="우측에서 새로 만들 수 있습니다" /> :
              items.map((it) => (
                <button key={it.key} type="button" onClick={() => pick(it.key)}
                  style={listItemStyle(selected === it.key)}>
                  <span style={{ fontWeight: 600 }}>{it.title || it.key}</span>
                  <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>{it.key}{it.shared ? " · shared" : ""}</span>
                </button>
              ))
          )}
        </div>

        <div style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", borderRadius: 6, padding: 12 }}>
          <h3 style={subHead}>{creating ? "새 템플릿" : (selected ? `편집: ${selected}` : "좌측에서 템플릿 선택 또는 + 새로 만들기")}</h3>
          {(creating || selected) && (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <Field label="key (영문/숫자/_)"><input value={form.key} onChange={(e) => setForm({ ...form, key: e.target.value })} disabled={!creating} style={inputStyle()} /></Field>
              <Field label="title"><input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} style={inputStyle()} /></Field>
              <Field label="trigger: prompt_contains (쉼표 구분)"><input value={form.prompt_contains} onChange={(e) => setForm({ ...form, prompt_contains: e.target.value })} placeholder="GATE 모듈, 인폼" style={inputStyle()} /></Field>
              <Field label="trigger: intent_in (쉼표 구분)"><input value={form.intent_in} onChange={(e) => setForm({ ...form, intent_in: e.target.value })} placeholder="inform" style={inputStyle()} /></Field>
              <Field label="trigger: slots_required (쉼표 구분, 정보용)"><input value={form.slots_required} onChange={(e) => setForm({ ...form, slots_required: e.target.value })} placeholder="product, lot" style={inputStyle()} /></Field>
              <Field label="steps (JSON 배열)"><textarea value={form.steps} onChange={(e) => setForm({ ...form, steps: e.target.value })} rows={8} style={{ ...inputStyle(), fontFamily: "monospace", fontSize: 12 }} /></Field>
              <label style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 6 }}>
                <input type="checkbox" checked={!!form.shared} onChange={(e) => setForm({ ...form, shared: e.target.checked })} />
                shared (admin만 가능)
              </label>
              <div style={{ display: "flex", gap: 8 }}>
                <Button onClick={save} disabled={saving}>{saving ? "저장 중..." : "저장"}</Button>
                {!creating && selected && <Button onClick={remove}>삭제</Button>}
              </div>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

function splitCsv(s) {
  return String(s || "").split(",").map((x) => x.trim()).filter(Boolean);
}

const subHead = { fontSize: 13, fontWeight: 700, margin: "0 0 6px", color: "var(--accent)" };
const inputStyle = (extra = {}) => ({ padding: "4px 8px", fontSize: 13, borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", width: "100%", boxSizing: "border-box", ...extra });
const preStyle = { margin: "6px 0 0", padding: 8, fontSize: 11, fontFamily: "monospace", background: "var(--bg-primary)", border: "1px solid var(--border)", borderRadius: 4, overflow: "auto", maxHeight: 200 };
function listItemStyle(active) {
  return {
    width: "100%", display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 2,
    padding: "8px 10px", border: "none", borderRadius: 4, cursor: "pointer",
    background: active ? "var(--accent-glow)" : "transparent",
    color: active ? "var(--accent)" : "var(--text-primary)",
    borderLeft: active ? "3px solid var(--accent)" : "3px solid transparent",
    fontSize: 12, textAlign: "left",
  };
}
