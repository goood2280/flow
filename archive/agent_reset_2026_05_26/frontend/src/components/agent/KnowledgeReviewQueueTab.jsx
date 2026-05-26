import { useEffect, useState } from "react";
import { postJson, sf, qs } from "../../lib/api";
import { Banner, Button, EmptyState, Field, Pill, Panel } from "../UXKit";
import Loading from "../Loading";

const STATUS_FILTERS = [
  { key: "pending", label: "검토 대기" },
  { key: "edited", label: "편집됨" },
  { key: "approved", label: "승인됨" },
  { key: "rejected", label: "거부됨" },
];

const STATUS_TONE = { pending: "warn", edited: "info", approved: "ok", rejected: "neutral" };
const READ_ONLY_TITLE = "admin 또는 diagnosis/agent/knowledge 페이지 관리자만 실행할 수 있습니다.";

export default function KnowledgeReviewQueueTab({ user, canManage }) {
  const [status, setStatus] = useState("pending");
  const [drafts, setDrafts] = useState([]);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [editingId, setEditingId] = useState("");
  const [editForm, setEditForm] = useState({ title: "", body: "" });
  const [enqueueOpts, setEnqueueOpts] = useState({ window_days: 7, threshold: 2 });
  const [busy, setBusy] = useState(false);

  async function reload() {
    setLoading(true);
    setErr("");
    try {
      const out = await sf("/api/knowledge/draft-queue" + qs({ status, limit: 80 }));
      setDrafts(out.drafts || []);
      setStats(out.stats || null);
    } catch (e) {
      setErr(e?.message || "큐 로딩 실패");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { reload(); }, [status]);

  async function runEnqueue() {
    if (!canManage) return;
    setBusy(true);
    setMsg("");
    try {
      const out = await postJson("/api/knowledge/draft-queue/enqueue", enqueueOpts);
      setMsg(`재집계: 생성 ${out.created}, skip ${out.skipped}`);
      reload();
    } catch (e) {
      setErr(e?.message || "재집계 실패");
    } finally {
      setBusy(false);
    }
  }

  async function approveDraft(id) {
    if (!canManage) return;
    setBusy(true);
    setMsg("");
    try {
      const out = await postJson(`/api/knowledge/draft-queue/${encodeURIComponent(id)}/approve`, {});
      setMsg(`승인됨: ${out.draft?.published_doc_id || id}`);
      reload();
    } catch (e) {
      setErr(e?.message || "승인 실패");
    } finally {
      setBusy(false);
    }
  }

  async function rejectDraft(id) {
    if (!canManage) return;
    const reason = window.prompt("거부 사유 (선택)");
    if (reason === null) return;
    setBusy(true);
    setMsg("");
    try {
      await postJson(`/api/knowledge/draft-queue/${encodeURIComponent(id)}/reject`, { reason });
      setMsg(`거부됨: ${id}`);
      reload();
    } catch (e) {
      setErr(e?.message || "거부 실패");
    } finally {
      setBusy(false);
    }
  }

  async function saveEdit() {
    if (!canManage || !editingId) return;
    setBusy(true);
    try {
      await postJson(`/api/knowledge/draft-queue/${encodeURIComponent(editingId)}/edit`, editForm);
      setMsg("편집 저장됨");
      setEditingId("");
      reload();
    } catch (e) {
      setErr(e?.message || "저장 실패");
    } finally {
      setBusy(false);
    }
  }

  function startEdit(row) {
    setEditingId(row.draft_id);
    setEditForm({ title: row.title || "", body: row.body || "" });
  }

  return (
    <div style={{ padding: 16, display: "grid", gap: 12 }}>
      <Panel
        title="Wiki 검토 큐"
        subtitle="회의/이슈/lot 활동에서 자동 생성된 wiki 초안 — 검토 후 publish"
        right={
          <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
            <Field label="window">
              <input type="number" min={1} max={90} value={enqueueOpts.window_days} onChange={(e) => setEnqueueOpts((cur) => ({ ...cur, window_days: Number(e.target.value || 7) }))} style={{ width: 60 }} />
            </Field>
            <Field label="threshold">
              <input type="number" min={1} max={20} value={enqueueOpts.threshold} onChange={(e) => setEnqueueOpts((cur) => ({ ...cur, threshold: Number(e.target.value || 2) }))} style={{ width: 60 }} />
            </Field>
            <Button onClick={runEnqueue} disabled={!canManage || busy} title={!canManage ? READ_ONLY_TITLE : ""}>{busy ? "재집계 중" : "재집계 (LLM draft 생성)"}</Button>
            <Button onClick={reload} disabled={loading}>{loading ? "..." : "새로고침"}</Button>
          </div>
        }
      >
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          {STATUS_FILTERS.map((item) => (
            <button
              key={item.key}
              type="button"
              onClick={() => setStatus(item.key)}
              style={{
                padding: "6px 12px",
                border: "1px solid #e2e8f0",
                background: status === item.key ? "#3b82f6" : "#fff",
                color: status === item.key ? "#fff" : "#334155",
                borderRadius: 4,
                cursor: "pointer",
                fontSize: 12,
              }}
            >
              {item.label}
              {stats?.[item.key] !== undefined && <span style={{ marginLeft: 6, opacity: 0.7 }}>{stats[item.key]}</span>}
            </button>
          ))}
          {stats && <span style={{ marginLeft: "auto", fontSize: 12, color: "#64748b" }}>총 {stats.total}건</span>}
        </div>
      </Panel>

      {err && <Banner tone="warn">{err}</Banner>}
      {msg && <Banner tone="info">{msg}</Banner>}

      {loading ? (
        <Loading text="큐 로딩..." size="md" />
      ) : drafts.length ? (
        <div style={{ display: "grid", gap: 10 }}>
          {drafts.map((row) => (
            <DraftCard
              key={row.draft_id}
              row={row}
              isEditing={editingId === row.draft_id}
              editForm={editForm}
              setEditForm={setEditForm}
              onEdit={() => startEdit(row)}
              onCancelEdit={() => setEditingId("")}
              onSaveEdit={saveEdit}
              onApprove={() => approveDraft(row.draft_id)}
              onReject={() => rejectDraft(row.draft_id)}
              canManage={canManage}
              busy={busy}
            />
          ))}
        </div>
      ) : (
        <EmptyState title="해당 상태의 draft 없음" hint="threshold/window 를 조정하고 재집계를 눌러보세요." />
      )}
    </div>
  );
}

function DraftCard({ row, isEditing, editForm, setEditForm, onEdit, onCancelEdit, onSaveEdit, onApprove, onReject, canManage, busy }) {
  const tone = STATUS_TONE[row.status] || "muted";
  const eventIds = row.source_event_ids || [];
  return (
    <Panel
      title={row.title || row.target || row.draft_id}
      subtitle={
        <span style={{ fontSize: 11, color: "#64748b" }}>
          <Pill tone={tone}>{row.status}</Pill> · target: <code>{row.target}</code> · 이벤트 {row.event_count || eventIds.length}건 · {row.created_at?.slice(0, 19)} {row.created_by ? `· ${row.created_by}` : ""}
        </span>
      }
      right={
        <div style={{ display: "flex", gap: 6 }}>
          {row.status === "pending" || row.status === "edited" ? (
            <>
              {!isEditing && <Button onClick={onEdit} disabled={!canManage} title={!canManage ? READ_ONLY_TITLE : ""}>편집</Button>}
              {isEditing && <Button onClick={onSaveEdit} disabled={busy}>편집 저장</Button>}
              {isEditing && <Button onClick={onCancelEdit}>취소</Button>}
              {!isEditing && <Button variant="primary" onClick={onApprove} disabled={!canManage || busy} title={!canManage ? READ_ONLY_TITLE : ""}>승인 → Publish</Button>}
              {!isEditing && <Button onClick={onReject} disabled={!canManage || busy} title={!canManage ? READ_ONLY_TITLE : ""}>거부</Button>}
            </>
          ) : row.status === "approved" && row.published_doc_id ? (
            <a href={`#wiki/${encodeURIComponent(row.published_doc_id)}`} style={{ fontSize: 12 }}>publish 결과 → {row.published_doc_id}</a>
          ) : null}
        </div>
      }
    >
      {isEditing ? (
        <div style={{ display: "grid", gap: 8 }}>
          <Field label="제목">
            <input value={editForm.title} onChange={(e) => setEditForm((cur) => ({ ...cur, title: e.target.value }))} style={{ width: "100%" }} />
          </Field>
          <Field label="본문 (markdown)">
            <textarea value={editForm.body} onChange={(e) => setEditForm((cur) => ({ ...cur, body: e.target.value }))} rows={18} style={{ width: "100%", fontFamily: "monospace", fontSize: 12, lineHeight: 1.5 }} />
          </Field>
        </div>
      ) : (
        <div style={{ display: "grid", gap: 6 }}>
          <details>
            <summary style={{ cursor: "pointer", fontSize: 12, color: "#64748b" }}>본문 미리보기 ({(row.body || "").length}자)</summary>
            <pre style={{ marginTop: 6, padding: 10, background: "#f8fafc", borderRadius: 4, fontSize: 12, lineHeight: 1.6, whiteSpace: "pre-wrap", maxHeight: 400, overflow: "auto" }}>{row.body || "(empty)"}</pre>
          </details>
          {eventIds.length > 0 && (
            <details>
              <summary style={{ cursor: "pointer", fontSize: 12, color: "#64748b" }}>근거 이벤트 {eventIds.length}건</summary>
              <ul style={{ marginTop: 4, paddingLeft: 18, fontSize: 12 }}>
                {eventIds.slice(0, 30).map((id) => (
                  <li key={id}><code>{id}</code></li>
                ))}
                {eventIds.length > 30 && <li>... +{eventIds.length - 30} more</li>}
              </ul>
            </details>
          )}
          {row.reject_reason && <div style={{ fontSize: 12, color: "#b91c1c" }}>거부 사유: {row.reject_reason}</div>}
        </div>
      )}
    </Panel>
  );
}
