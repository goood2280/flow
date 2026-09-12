import { useEffect, useRef, useState } from "react";
import { sf } from "../../lib/api";
import { Button, Card, Pill } from "../../components/UXKit";

const API = "/api/teg-map/mapfile-traffic/comments";

function formatTime(value) {
  return value ? String(value).replace("T", " ").replace(/[+-]\d\d:\d\d$/, "") : "";
}

export default function TegMapfileVersionComments({ versionInfo }) {
  const vehicle = versionInfo?.vehicle || "";
  const filename = versionInfo?.filename || "";
  const version = versionInfo?.signature || "";
  const identity = `${vehicle}\0${filename}\0${version}`;
  const requestRef = useRef({ id: 0, controller: null });
  const saveControllerRef = useRef(null);
  const identityRef = useRef(identity);
  const [comments, setComments] = useState([]);
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => { identityRef.current = identity; }, [identity]);

  useEffect(() => {
    requestRef.current.controller?.abort();
    saveControllerRef.current?.abort();
    setComments([]);
    setText("");
    setError("");
    setSaving(false);
    if (!vehicle || !filename || !version) return undefined;
    const controller = new AbortController();
    const id = requestRef.current.id + 1;
    requestRef.current = { id, controller };
    setLoading(true);
    const query = new URLSearchParams({ vehicle, filename, version });
    sf(`${API}?${query.toString()}`, { signal: controller.signal })
      .then(result => {
        if (requestRef.current.id === id && identityRef.current === identity) {
          setComments(Array.isArray(result.comments) ? result.comments : []);
        }
      })
      .catch(err => {
        if (!controller.signal.aborted && requestRef.current.id === id) setError(err.message || String(err));
      })
      .finally(() => {
        if (requestRef.current.id === id) {
          requestRef.current.controller = null;
          setLoading(false);
        }
      });
    return () => {
      controller.abort();
      saveControllerRef.current?.abort();
    };
  }, [identity, vehicle, filename, version]);

  if (!vehicle || !filename || !version) return null;

  const submit = async () => {
    const value = text.trim();
    if (!value || saving) return;
    const submittedIdentity = identity;
    saveControllerRef.current?.abort();
    const controller = new AbortController();
    saveControllerRef.current = controller;
    setSaving(true);
    setError("");
    try {
      const result = await sf(API, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ vehicle, filename, version, text: value }),
        signal: controller.signal,
      });
      if (identityRef.current === submittedIdentity) {
        setComments(Array.isArray(result.comments) ? result.comments : []);
        setText("");
      }
    } catch (err) {
      if (!controller.signal.aborted && identityRef.current === submittedIdentity) setError(err.message || String(err));
    } finally {
      if (saveControllerRef.current === controller) saveControllerRef.current = null;
      if (identityRef.current === submittedIdentity) setSaving(false);
    }
  };

  const hash = version.includes(":sha256:") ? version.split(":sha256:").pop() : version;
  return <Card title="이 Mapfile 버전의 검토 코멘트"
    right={<div style={{ display: "flex", gap: 8, alignItems: "center" }}>
      <Pill tone="neutral">{filename}</Pill>
      <span title={version} style={{ fontFamily: "monospace", fontSize: 11, color: "var(--muted)" }}>
        {hash.slice(0, 10)}
      </span>
    </div>}>
    <div style={{ display: "grid", gap: 9 }}>
      {loading && <div style={{ color: "var(--muted)", fontSize: 12 }}>코멘트를 불러오는 중…</div>}
      {!loading && comments.length === 0 && <div style={{ color: "var(--muted)", fontSize: 12 }}>등록된 코멘트가 없습니다.</div>}
      {comments.map(comment => <div key={comment.id || `${comment.created_at}:${comment.author}`}
        style={{ border: "1px solid var(--line)", borderRadius: 7, padding: "8px 10px", background: "var(--bg-hover)" }}>
        <div style={{ display: "flex", gap: 8, marginBottom: 4, fontSize: 11 }}>
          <b>{comment.author || "작성자 미상"}</b>
          <span style={{ color: "var(--muted)" }}>{formatTime(comment.created_at)}</span>
        </div>
        <div style={{ fontSize: 12, whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{comment.text}</div>
      </div>)}
      {error && <div style={{ color: "var(--danger)", fontSize: 12 }}>{error}</div>}
      <div style={{ display: "flex", gap: 8, alignItems: "flex-end" }}>
        <textarea value={text} onChange={event => setText(event.target.value)} maxLength={4000} rows={2}
          placeholder="이 버전의 검토 내용을 남겨 주세요"
          style={{ flex: 1, resize: "vertical", minHeight: 54, padding: "7px 9px", borderRadius: 6,
            border: "1px solid var(--line)", color: "var(--text)", background: "var(--bg-primary)" }} />
        <Button variant="primary" disabled={!text.trim() || saving} onClick={submit}>
          {saving ? "등록 중…" : "코멘트 등록"}
        </Button>
      </div>
    </div>
  </Card>;
}
