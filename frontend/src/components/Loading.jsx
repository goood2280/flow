import { useEffect, useMemo, useState } from "react";

const spinnerCSS = `
@keyframes holSpin { 0%{transform:rotate(0deg)} 100%{transform:rotate(360deg)} }
@keyframes holPulse { 0%,100%{opacity:0.4} 50%{opacity:1} }
@keyframes flowLoadingSweep { 0%{transform:translateX(-40%)} 100%{transform:translateX(140%)} }
@keyframes flowLoadingRise { 0%{opacity:0;transform:translateY(4px)} 100%{opacity:1;transform:translateY(0)} }
`;

const DEFAULT_STEPS = ["데이터 확인", "컬럼 준비", "화면 갱신"];

function labelText(text) {
  const raw = String(text || "").trim();
  if (!raw || /^loading\.?\.?\.?$/i.test(raw) || raw === "로딩 중...") return "데이터 준비 중";
  if (/loading features/i.test(raw)) return "Feature 목록 준비 중";
  return raw;
}

function Spinner({ size = 24 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" style={{ animation: "holSpin 0.8s linear infinite" }}>
      <circle cx="12" cy="12" r="10" fill="none" stroke="var(--border, #334155)" strokeWidth="3" />
      <path d="M12 2 A10 10 0 0 1 22 12" fill="none" stroke="var(--accent, #2dd4bf)" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

/* activeStep 을 주면 단계 자동 순환 대신 그 인덱스를 현재 단계로 고정한다
   (서버가 실제 진행 단계를 알려주는 화면 — 예: ET 다운로드 대기열).
   children 은 스텝 아래에 그대로 렌더 — 취소 버튼·대기 순번 같은 부가 정보용. */
export default function Loading({ text, size = "md", overlay = false, steps = DEFAULT_STEPS,
                                  activeStep = null, children = null }) {
  const sizes = { sm: 16, md: 24, lg: 40 };
  const s = sizes[size] || sizes.md;
  const label = labelText(text);
  const activeSteps = useMemo(() => {
    const list = Array.isArray(steps) && steps.length ? steps : DEFAULT_STEPS;
    return list.map(v => String(v || "").trim()).filter(Boolean).slice(0, 4);
  }, [steps]);
  const [stepIdx, setStepIdx] = useState(0);
  const fixedStep = Number.isFinite(activeStep) && activeStep !== null;
  useEffect(() => {
    if (fixedStep || size === "sm" || activeSteps.length <= 1) return undefined;
    const id = setInterval(() => setStepIdx(i => (i + 1) % activeSteps.length), 1100);
    return () => clearInterval(id);
  }, [activeSteps.length, size, fixedStep]);
  const curStep = fixedStep ? Math.max(0, Math.min(activeSteps.length - 1, Number(activeStep))) : stepIdx;

  if (size === "sm") {
    return (
      <div style={{ display:"inline-flex", alignItems:"center", justifyContent:"center", gap:8 }}>
        <style>{spinnerCSS}</style>
        <Spinner size={s} />
        {label && <span style={{ fontSize:14, color:"var(--text-secondary, #94a3b8)", fontFamily:"monospace" }}>{label}</span>}
      </div>
    );
  }

  const inner = (
    <div role="status" aria-live="polite" style={{ display:"flex", flexDirection:"column", alignItems:"center", justifyContent:"center", gap:12, padding:32, animation:"flowLoadingRise .22s ease-out" }}>
      <style>{spinnerCSS}</style>
      <Spinner size={s} />
      {/* 서버가 보고하는 진행 문구는 길어질 수 있다(파일명·행 수) — 오버레이가
          화면 밖으로 벌어지지 않게 폭을 묶고 줄바꿈을 허용한다. */}
      {label && <span style={{ fontSize:14, color:"var(--text-primary, #e5e7eb)", fontFamily:"monospace", fontWeight:700, maxWidth:360, textAlign:"center", wordBreak:"break-word" }}>{label}</span>}
      <div style={{ width:220, maxWidth:"70vw", height:5, borderRadius:999, overflow:"hidden", background:"var(--bg-card, #1f2937)", border:"1px solid var(--border, #334155)" }}>
        <div style={{ width:"42%", height:"100%", borderRadius:999, background:"var(--accent, #2dd4bf)", opacity:.85, animation:"flowLoadingSweep 1.15s ease-in-out infinite" }} />
      </div>
      {activeSteps.length > 0 && (
        <div style={{ display:"flex", gap:6, flexWrap:"wrap", justifyContent:"center", maxWidth:320 }}>
          {activeSteps.map((step, i) => (
            <span key={step + i} style={{
              fontSize:14,
              color:i===curStep?"var(--accent, #2dd4bf)":"var(--text-secondary, #94a3b8)",
              border:"1px solid " + (i===curStep ? "var(--accent, #2dd4bf)" : "var(--border, #334155)"),
              background:i===curStep?"var(--accent-glow, rgba(45,212,191,0.12))":"transparent",
              borderRadius:999,
              padding:"2px 7px",
              fontFamily:"monospace",
              fontWeight:i===curStep?800:500,
            }}>{step}</span>
          ))}
        </div>
      )}
      {children}
    </div>
  );
  if (overlay) {
    return (
      <div style={{ position:"fixed", inset:0, zIndex:9999, background:"rgba(0,0,0,0.6)", backdropFilter:"blur(4px)", display:"flex", alignItems:"center", justifyContent:"center" }}>
        <div style={{ background:"var(--bg-secondary, #1e293b)", borderRadius:16, padding:"32px 48px", border:"1px solid var(--border, #334155)" }}>{inner}</div>
      </div>
    );
  }
  return inner;
}

export function Skeleton({ width = "100%", height = 16 }) {
  return (
    <div style={{ width, height, borderRadius:4, background:"var(--border, #334155)", animation:"holPulse 1.5s ease-in-out infinite" }}>
      <style>{spinnerCSS}</style>
    </div>
  );
}
