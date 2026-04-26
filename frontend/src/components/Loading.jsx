const spinnerCSS = `
@keyframes holSpin { 0%{transform:rotate(0deg)} 100%{transform:rotate(360deg)} }
@keyframes holPulse { 0%,100%{opacity:0.4} 50%{opacity:1} }
`;

function Spinner({ size = 24 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" style={{ animation: "holSpin 0.8s linear infinite" }}>
      <circle cx="12" cy="12" r="10" fill="none" stroke="var(--border, #334155)" strokeWidth="3" />
      <path d="M12 2 A10 10 0 0 1 22 12" fill="none" stroke="var(--accent, #2dd4bf)" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

export default function Loading({ text, size = "md", overlay = false }) {
  const sizes = { sm: 16, md: 24, lg: 40 };
  const s = sizes[size] || sizes.md;
  const inner = (
    <div style={{ display:"flex", flexDirection: size==="sm"?"row":"column", alignItems:"center", justifyContent:"center", gap: size==="sm"?8:12, padding: size==="sm"?0:32 }}>
      <style>{spinnerCSS}</style>
      <Spinner size={s} />
      {text && <span style={{ fontSize: size==="sm"?12:14, color:"var(--text-secondary, #94a3b8)", fontFamily:"monospace" }}>{text}</span>}
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
