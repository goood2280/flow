// components/GuideHelp.jsx — 상단 바 오른쪽 "사용법" 버튼 + 사용법 영상 창.
// 가이드 목록은 /api/guides (routers/guides.py) 가 운영 폴더({DB}/_guides/<helpId>/guide.json)에서 읽는다.
// 버튼은 현재 탭의 pageManifest helpId 에 가이드가 있을 때만 보이고, 영상은 창을 열기 전에는 받지 않는다.
import { useEffect, useRef, useState } from "react";
import Modal from "./Modal";
import { Icon } from "./ui/Icon";
import { authSrc, sf } from "../lib/api";

let guidesRequest = null;

function loadGuides() {
  if (!guidesRequest) {
    guidesRequest = sf("/api/guides")
      .then((d) => (d && typeof d.guides === "object" && d.guides) || {})
      .catch(() => { guidesRequest = null; return {}; });
  }
  return guidesRequest;
}

const fmtTime = (sec) => {
  const t = Math.max(0, Math.floor(Number(sec) || 0));
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`;
};

function GuideDialog({ guide, onClose }) {
  const videoRef = useRef(null);
  const sources = guide.sources || [];
  const [sourceIndex, setSourceIndex] = useState(0);
  const [now, setNow] = useState(0);
  const pendingSeek = useRef(null);
  const source = sources[sourceIndex] || sources[0];
  const chapters = guide.chapters || [];
  let activeChapter = 0;
  chapters.forEach((c, i) => { if (now + 0.05 >= c.t) activeChapter = i; });

  const switchSource = (index) => {
    const v = videoRef.current;
    if (index === sourceIndex) return;
    pendingSeek.current = v ? { t: v.currentTime, play: !v.paused } : null;
    setSourceIndex(index);
  };
  const seek = (t) => {
    const v = videoRef.current;
    if (!v) return;
    v.currentTime = t + 0.01;
    v.play().catch(() => {});
  };
  const onLoaded = () => {
    const v = videoRef.current;
    const p = pendingSeek.current;
    pendingSeek.current = null;
    if (!v || !p) return;
    v.currentTime = p.t;
    if (p.play) v.play().catch(() => {});
  };

  return (
    <Modal open onClose={onClose} title={guide.title} width={1320} maxHeight="94vh">
      <div className="flow-guide">
        <div className="flow-guide__player">
          <video
            key={source.url}
            ref={videoRef}
            className="flow-guide__video"
            src={authSrc(source.url)}
            poster={guide.poster ? authSrc(guide.poster) : undefined}
            controls
            autoPlay
            playsInline
            preload="metadata"
            onLoadedMetadata={onLoaded}
            onTimeUpdate={(e) => setNow(e.currentTarget.currentTime)}
          >
            {guide.captions && <track kind="subtitles" srcLang="ko" label="한국어 설명" src={authSrc(guide.captions)} />}
          </video>
          <div className="flow-guide__meta">
            <span>{guide.duration ? fmtTime(guide.duration) : ""}{guide.updated ? ` · ${guide.updated} 기준` : ""}</span>
            {sources.length > 1 && (
              <span className="flow-guide__quality" role="group" aria-label="화질">
                {sources.map((s, i) => (
                  <button key={s.url} type="button" data-on={i === sourceIndex ? "1" : "0"} onClick={() => switchSource(i)}>
                    {s.label}
                  </button>
                ))}
              </span>
            )}
          </div>
        </div>
        <aside className="flow-guide__side">
          {guide.summary && <p className="flow-guide__summary">{guide.summary}</p>}
          {chapters.length > 0 && <div className="flow-guide__label">단계 — 누르면 그 장면으로 이동</div>}
          <ol className="flow-guide__chapters">
            {chapters.map((c, i) => (
              <li key={`${c.t}-${i}`}>
                <button type="button" data-on={i === activeChapter ? "1" : "0"} onClick={() => seek(c.t)}>
                  <time>{fmtTime(c.t)}</time>
                  <span>
                    <strong>{c.title}</strong>
                    {c.desc && <small>{c.desc}</small>}
                  </span>
                </button>
              </li>
            ))}
          </ol>
          {(guide.notes || []).map((note, i) => (
            <section key={i} className="flow-guide__note">
              {note.title && <h3>{note.title}</h3>}
              {(note.lines || []).map((line, j) => <p key={j}>{line}</p>)}
              {(note.code || []).length > 0 && <pre>{note.code.join("\n")}</pre>}
            </section>
          ))}
        </aside>
      </div>
    </Modal>
  );
}

export default function GuideHelpButton({ helpId, pageLabel }) {
  const [guides, setGuides] = useState(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    let alive = true;
    loadGuides().then((g) => { if (alive) setGuides(g); });
    return () => { alive = false; };
  }, []);
  useEffect(() => { setOpen(false); }, [helpId]);
  const guide = helpId && guides ? guides[helpId] : null;
  if (!guide) return null;
  const label = `${pageLabel || guide.title} 사용법 영상`;
  return (
    <>
      <button type="button" className="flow-guide-help" onClick={() => setOpen(true)} title={label} aria-label={label}>
        <Icon name="help" size={18} />
        <span>사용법</span>
      </button>
      {open && <GuideDialog guide={guide} onClose={() => setOpen(false)} />}
    </>
  );
}
