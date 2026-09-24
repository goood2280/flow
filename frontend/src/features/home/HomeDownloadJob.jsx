import { useEffect, useState } from "react";
import { dl, sf } from "../../lib/api";

export default function HomeDownloadJob({ job }) {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  // Construct same-origin paths from the job id; responses cannot supply download hosts.
  const base = `/api/reformatize/download`;
  const query = `?job_id=${encodeURIComponent(job.job_id)}`;
  useEffect(() => {
    let alive = true;
    let timer;
    let failures = 0;
    setStatus(null);
    setError("");
    const refresh = async () => {
      try {
        const next = await sf(`${base}/status${query}`);
        if (!alive) return;
        failures = 0;
        setStatus(next);
        setError("");
        if (["queued", "running"].includes(next.state)) timer = setTimeout(refresh, 2000);
      } catch (e) {
        if (!alive) return;
        setError(e.message || "작업 상태를 확인하지 못했습니다.");
        // Recover from temporary outages without requiring a page reload.
        failures += 1;
        timer = setTimeout(refresh, Math.min(30000, 3000 * 2 ** Math.min(failures - 1, 4)));
      }
    };
    refresh();
    return () => { alive = false; clearTimeout(timer); };
  }, [job.job_id, query]);
  const labels = { queued: "대기 중", running: "파일 생성 중", ready: "다운로드 준비 완료", error: "다운로드 실패", canceled: "취소됨", expired: "파일 만료" };
  const save = async () => {
    setSaving(true);
    try { await dl(`${base}/file${query}`, status?.filename || job.filename || "ET.csv"); }
    catch (e) { setError(e.message || "파일을 받지 못했습니다."); }
    finally { setSaving(false); }
  };
  return <div className="home-workspace__download-card" aria-label="다운로드 작업">
    <div className="home-workspace__download-title">📥 {job.filename || "ET 다운로드"}</div>
    <div role="status">{labels[status?.state] || "작업 상태 확인 중"}{status?.percent != null ? ` · ${status.percent}%` : ""}</div>
    {status?.state === "ready" && status.rows != null && <div className="home-workspace__download-meta">{Number(status.rows).toLocaleString()}행 · {Number(status.cols || 0).toLocaleString()}열</div>}
    {status?.phase && <div className="home-workspace__download-meta">{status.phase}</div>}
    {(error || status?.error) && <div role="alert">{error || status.error}</div>}
    <button type="button" className="home-workspace__btn is-approve" disabled={status?.state !== "ready" || saving} onClick={save}>
      {saving ? "파일 받는 중…" : "파일 받기"}
    </button>
  </div>;
}
