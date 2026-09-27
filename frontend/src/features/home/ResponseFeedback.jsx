import { useEffect, useRef, useState } from "react";
import { sf } from "../../lib/api";
import "./ResponseFeedback.css";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const FEEDBACK_COPY = "수정 의견은 본인 질문에만 참고됩니다. 여러 사용자의 좋아요·싫어요는 익명 집계해 유사 질문의 처리 경로에 약하게 반영합니다. 기본 지식·Semantic에 자동 등록하지 않습니다.";

export function isFeedbackUuid(value) {
  return typeof value === "string" && UUID_RE.test(value);
}

export default function ResponseFeedback({ conversationId, messageId }) {
  const [feedback, setFeedback] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [correction, setCorrection] = useState("");
  const generation = useRef(0);
  const rootRef = useRef(null);
  const [nearViewport, setNearViewport] = useState(false);

  useEffect(() => {
    const node = rootRef.current;
    if (!node || nearViewport) return undefined;
    if (typeof IntersectionObserver === "undefined") {
      setNearViewport(true);
      return undefined;
    }
    const observer = new IntersectionObserver(([entry]) => {
      if (entry?.isIntersecting) {
        setNearViewport(true);
        observer.disconnect();
      }
    }, { rootMargin: "160px" });
    observer.observe(node);
    return () => observer.disconnect();
  }, [nearViewport]);

  useEffect(() => {
    if (!nearViewport) return undefined;
    const current = ++generation.current;
    const controller = new AbortController();
    setFeedback(null);
    setCorrection("");
    setEditing(false);
    setBusy(false);
    setError("");
    setLoading(true);

    sf(`/api/home-agent/feedback/${encodeURIComponent(conversationId)}/${encodeURIComponent(messageId)}`, { signal: controller.signal })
      .then((payload) => {
        if (generation.current !== current) return;
        const saved = payload?.feedback && typeof payload.feedback === "object" ? payload.feedback : null;
        setFeedback(saved);
        setCorrection(typeof saved?.correction === "string" ? saved.correction : "");
      })
      .catch((loadError) => {
        if (generation.current === current && loadError?.name !== "AbortError") setError("의견을 불러오지 못했습니다.");
      })
      .finally(() => {
        if (generation.current === current) setLoading(false);
      });

    return () => {
      generation.current += 1;
      controller.abort();
    };
  }, [conversationId, messageId, nearViewport]);

  const save = async (rating, note = correction) => {
    const current = generation.current;
    setBusy(true);
    setError("");
    try {
      const payload = await sf("/api/home-agent/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversation_id: conversationId, message_id: messageId, rating, correction: note.slice(0, 1000) }),
      });
      if (generation.current !== current) return;
      const saved = payload?.feedback && typeof payload.feedback === "object" ? payload.feedback : { rating, correction: note.slice(0, 1000) };
      setFeedback(saved);
      setCorrection(typeof saved.correction === "string" ? saved.correction : note.slice(0, 1000));
      setEditing(false);
    } catch {
      if (generation.current === current) setError("의견을 저장하지 못했습니다. 다시 시도해 주세요.");
    } finally {
      if (generation.current === current) setBusy(false);
    }
  };

  const remove = async () => {
    const current = generation.current;
    setBusy(true);
    setError("");
    try {
      await sf(`/api/home-agent/feedback/${encodeURIComponent(conversationId)}/${encodeURIComponent(messageId)}`, { method: "DELETE" });
      if (generation.current !== current) return;
      setFeedback(null);
      setCorrection("");
      setEditing(false);
    } catch {
      if (generation.current === current) setError("의견을 취소하지 못했습니다. 다시 시도해 주세요.");
    } finally {
      if (generation.current === current) setBusy(false);
    }
  };

  return (
    <div className="response-feedback" ref={rootRef}>
      <div className="response-feedback__actions" aria-label="답변 의견">
        <button type="button" aria-pressed={feedback?.rating === "up"} disabled={loading || busy} onClick={() => save("up")}>
          좋아요
        </button>
        <button type="button" aria-pressed={feedback?.rating === "down"} disabled={loading || busy} onClick={() => save("down")}>
          싫어요
        </button>
        <button type="button" disabled={loading || busy} aria-expanded={editing} onClick={() => {
          setError("");
          setCorrection(typeof feedback?.correction === "string" ? feedback.correction : "");
          setEditing((value) => !value);
        }}>
          {editing ? "수정 닫기" : "수정 의견"}
        </button>
        {feedback && <button type="button" disabled={loading || busy} onClick={remove}>의견 취소</button>}
      </div>
      {editing && (
        <div className="response-feedback__editor">
          <label>
            수정 의견
            <textarea value={correction} maxLength={1000} onChange={(event) => setCorrection(event.target.value)} rows={3} disabled={busy} />
          </label>
          <div className="response-feedback__editor-actions">
            <span>{correction.length}/1000</span>
            <button type="button" disabled={busy || !feedback?.rating} onClick={() => feedback?.rating && save(feedback.rating, correction)}>저장</button>
            <button type="button" disabled={busy} onClick={() => {
              setCorrection(typeof feedback?.correction === "string" ? feedback.correction : "");
              setEditing(false);
              setError("");
            }}>취소</button>
          </div>
          {!feedback?.rating && <div className="response-feedback__hint">수정 의견을 저장하려면 먼저 좋아요 또는 싫어요를 선택하세요.</div>}
        </div>
      )}
      {error && <div className="response-feedback__error" role="alert">{error}</div>}
      <div className="response-feedback__copy">{FEEDBACK_COPY}</div>
    </div>
  );
}
