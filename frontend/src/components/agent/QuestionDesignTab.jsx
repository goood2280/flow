import { useMemo, useState } from "react";
import { postJson } from "../../lib/api";
import { Banner, Button, DataTable, EmptyState, Field, Pill, Textarea } from "../UXKit";

const DEFAULT_PROMPT = "PRODA A1000 #21 현재 step과 KNOB 영향을 확인하고 필요한 업무 흐름을 설계해줘";

const EMPTY_FORM = {
  key: "",
  title: "",
  prompt_contains: "",
  intent_in: "",
  slots_required: "",
  steps: "[]",
  shared: false,
};

export default function QuestionDesignTab({ user, canShare = false }) {
  const [prompt, setPrompt] = useState(DEFAULT_PROMPT);
  const [semantic, setSemantic] = useState(null);
  const [matchResult, setMatchResult] = useState(null);
  const [execution, setExecution] = useState(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");

  const intent = semantic?.intent || "";
  const slots = semantic?.slots || {};
  const matched = matchResult?.matched || null;
  const executionSteps = execution?.execution?.steps || [];
  const expectedSteps = executionSteps.length ? executionSteps : (matched?.steps || []);
  const missingInputs = useMemo(() => {
    const values = [];
    (semantic?.warnings || []).forEach((w) => values.push(w));
    executionSteps.forEach((step) => (step.missing_slots || []).forEach((slot) => values.push(slot)));
    (matched?.trigger?.slots_required || []).forEach((slot) => {
      const value = slots[slot];
      if (value === undefined || value === null || value === "") values.push(slot);
    });
    return [...new Set(values.filter(Boolean))];
  }, [semantic, executionSteps, matched, slots]);

  async function analyze() {
    const trimmed = prompt.trim();
    if (!trimmed) {
      setErr("질문을 입력하세요");
      return;
    }
    setBusy(true);
    setErr("");
    setMsg("");
    setSemantic(null);
    setMatchResult(null);
    setExecution(null);
    try {
      const resolved = await postJson("/api/agent/runtime/semantic/resolve", { goal: trimmed, max_terms: 32 });
      const frame = resolved?.semantic || null;
      const nextIntent = frame?.intent || "";
      const nextSlots = frame?.slots || {};
      setSemantic(frame);

      const tested = await postJson("/api/agent/workflows/test", { prompt: trimmed, intent: nextIntent });
      setMatchResult(tested);

      const dryRun = await postJson("/api/agent/workflows/execute", {
        prompt: trimmed,
        intent: nextIntent,
        slots: nextSlots,
        dry_run: true,
      });
      setExecution(dryRun);
      seedDraftForm(trimmed, frame, tested?.matched || null);
    } catch (e) {
      setErr(e?.message || "질문 설계 분석 실패");
    } finally {
      setBusy(false);
    }
  }

  function seedDraftForm(text, frame, matchedTemplate) {
    const nextIntent = frame?.intent || "";
    const tokenTerms = (frame?.tokens || [])
      .map((token) => String(token || "").trim())
      .filter((token) => token.length >= 2)
      .slice(0, 4);
    const slotKeys = Object.keys(frame?.slots || {}).filter(Boolean);
    const steps = matchedTemplate?.steps || [];
    const key = `draft_${safeKey(nextIntent || "question")}_${Date.now().toString(36)}`;
    setForm({
      key,
      title: `${(nextIntent || "질문")} 설계 초안`,
      prompt_contains: tokenTerms.join(", "),
      intent_in: nextIntent,
      slots_required: slotKeys.join(", "),
      steps: JSON.stringify(steps, null, 2),
      shared: false,
    });
  }

  async function saveDraft() {
    setErr("");
    setMsg("");
    if (!form.key.trim()) {
      setErr("key를 입력하세요");
      return;
    }
    if (form.shared && !canShare) {
      setErr("shared 저장은 admin 또는 agent/knowledge manager만 가능합니다");
      return;
    }
    let steps = [];
    try {
      steps = JSON.parse(form.steps || "[]");
      if (!Array.isArray(steps)) throw new Error("steps must be array");
    } catch (_) {
      setErr("steps는 JSON 배열이어야 합니다");
      return;
    }
    setSaving(true);
    try {
      const payload = {
        key: form.key.trim(),
        title: form.title.trim() || form.key.trim(),
        trigger: {
          prompt_contains: splitCsv(form.prompt_contains),
          intent_in: splitCsv(form.intent_in),
          slots_required: splitCsv(form.slots_required),
        },
        steps,
        shared: !!form.shared,
      };
      const saved = await postJson("/api/agent/workflows", payload);
      setMsg(`저장됨: ${saved?.template?.key || payload.key}`);
    } catch (e) {
      setErr(e?.message || "워크플로우 초안 저장 실패");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div style={{ padding: 20, display: "flex", flexDirection: "column", gap: 14 }}>
      {err && <Banner tone="warn">{err}</Banner>}
      {msg && <Banner tone="info">{msg}</Banner>}

      <section style={surfaceStyle}>
        <div style={sectionHeadStyle}>
          <div>
            <h2 style={titleStyle}>질문 처리 설계</h2>
            <p style={mutedStyle}>입력 질문을 시멘틱 frame, workflow match, dry-run step으로 분해합니다.</p>
          </div>
          <Button variant="primary" onClick={analyze} disabled={busy}>{busy ? "분석 중..." : "질문 분석"}</Button>
        </div>
        <Textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          rows={4}
          style={{ width: "100%", resize: "vertical" }}
        />
      </section>

      <section style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) 360px", gap: 12, alignItems: "start" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
          <section style={surfaceStyle}>
            <SectionTitle title="Semantic Resolve" meta={semantic ? `coverage ${Math.round((semantic.coverage || 0) * 100)}%` : "대기"} />
            {semantic ? (
              <>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
                  <Pill tone="accent">{semantic.intent || "general"}</Pill>
                  {Object.entries(slots).slice(0, 8).map(([key, value]) => (
                    <Pill key={key} tone="neutral">{key}: {formatShort(value)}</Pill>
                  ))}
                </div>
                <DataTable
                  rows={(semantic.candidates || []).slice(0, 12)}
                  columns={[
                    { key: "token", label: "token", width: 110 },
                    { key: "column", label: "column", width: 160 },
                    { key: "source", label: "source", width: 180 },
                    { key: "score", label: "score", width: 70, render: (r) => Number(r.score || 0).toFixed(2) },
                    { key: "meaning", label: "meaning" },
                  ]}
                  empty="후보 컬럼 없음"
                  maxHeight={260}
                />
              </>
            ) : (
              <EmptyState title="아직 분석되지 않았습니다" hint="질문 분석을 실행하면 semantic frame이 표시됩니다." />
            )}
          </section>

          <section style={surfaceStyle}>
            <SectionTitle title="Workflow Match / Dry-run" meta={matched ? matched.key : "매치 없음"} />
            {matched ? (
              <div style={{ marginBottom: 10, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <Pill tone="ok">{matched.key}</Pill>
                <span style={mutedStyle}>{matched.title || ""}</span>
              </div>
            ) : (
              <Banner tone="info" style={{ marginBottom: 10 }}>매치되는 공유/개인 workflow template이 없습니다.</Banner>
            )}
            <DataTable
              rows={expectedSteps.map((step, index) => ({ index, ...step }))}
              columns={[
                { key: "index", label: "#", width: 48 },
                { key: "unit_ai", label: "unit_ai", width: 130 },
                { key: "action", label: "action", width: 180 },
                { key: "status", label: "status", width: 120, render: (r) => r.status || (executionSteps.length ? "dry_run" : "template") },
                { key: "missing_slots", label: "missing", render: (r) => (r.missing_slots || []).join(", ") || "없음" },
              ]}
              empty="예상 step이 없습니다. 우측 초안에서 steps를 직접 입력할 수 있습니다."
              maxHeight={280}
            />
            {missingInputs.length > 0 && (
              <div style={{ marginTop: 10, display: "flex", gap: 6, flexWrap: "wrap" }}>
                {missingInputs.slice(0, 12).map((item) => <Pill key={item} tone="warn">{item}</Pill>)}
              </div>
            )}
          </section>
        </div>

        <section style={surfaceStyle}>
          <SectionTitle title="개인 Workflow 초안" meta={form.shared ? "shared" : "personal"} />
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <Field label="key">
              <input value={form.key} onChange={(e) => setForm({ ...form, key: e.target.value })} style={inputStyle()} />
            </Field>
            <Field label="title">
              <input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} style={inputStyle()} />
            </Field>
            <Field label="trigger.prompt_contains">
              <input value={form.prompt_contains} onChange={(e) => setForm({ ...form, prompt_contains: e.target.value })} style={inputStyle()} />
            </Field>
            <Field label="trigger.intent_in">
              <input value={form.intent_in} onChange={(e) => setForm({ ...form, intent_in: e.target.value })} style={inputStyle()} />
            </Field>
            <Field label="trigger.slots_required">
              <input value={form.slots_required} onChange={(e) => setForm({ ...form, slots_required: e.target.value })} style={inputStyle()} />
            </Field>
            <Field label="steps">
              <textarea value={form.steps} onChange={(e) => setForm({ ...form, steps: e.target.value })} rows={8} style={{ ...inputStyle(), fontFamily: "var(--font-mono)" }} />
            </Field>
            <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13, color: "var(--text-secondary)" }}>
              <input
                type="checkbox"
                checked={!!form.shared}
                disabled={!canShare}
                onChange={(e) => setForm({ ...form, shared: e.target.checked })}
              />
              shared
            </label>
            {!canShare && <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>shared 반영은 admin 또는 agent/knowledge manager 권한이 필요합니다.</div>}
            <Button variant="primary" onClick={saveDraft} disabled={saving}>{saving ? "저장 중..." : "초안 저장"}</Button>
          </div>
        </section>
      </section>
    </div>
  );
}

function SectionTitle({ title, meta }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
      <h3 style={{ fontSize: 14, fontWeight: 800, margin: 0 }}>{title}</h3>
      {meta && <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--text-secondary)" }}>{meta}</span>}
    </div>
  );
}

function splitCsv(value) {
  return String(value || "").split(",").map((part) => part.trim()).filter(Boolean);
}

function safeKey(value) {
  return String(value || "draft").replace(/[^A-Za-z0-9_.-]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 40) || "draft";
}

function formatShort(value) {
  if (Array.isArray(value)) return value.join(",");
  if (value && typeof value === "object") return JSON.stringify(value);
  return String(value ?? "");
}

const surfaceStyle = {
  border: "1px solid var(--border)",
  borderRadius: 6,
  background: "var(--bg-secondary)",
  padding: 12,
};
const sectionHeadStyle = { display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12, marginBottom: 10 };
const titleStyle = { fontSize: 18, margin: "0 0 4px" };
const mutedStyle = { fontSize: 12, color: "var(--text-secondary)", margin: 0 };
const inputStyle = () => ({
  width: "100%",
  boxSizing: "border-box",
  padding: "6px 8px",
  borderRadius: 4,
  border: "1px solid var(--border)",
  background: "var(--bg-primary)",
  color: "var(--text-primary)",
  fontSize: 13,
});
