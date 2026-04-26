import { useState, useEffect, useRef } from "react";
import { PixelGlyph, FlowWordmark } from "../components/BrandLogo";

/* ═══ Matrix Rain — semiconductor keywords ═══ */
function MatrixRain() {
  const ref = useRef(null);
  useEffect(() => {
    const c = ref.current, ctx = c.getContext("2d");
    let w, h, cols, drops;
    const pool = "OPENSHORTLKGISOFAILPASSBINWAFERLOTDIEYIELDDEFECTSPECCPFTEDSATESPCPROBEMAPVTHRDSONBVDSSFLOWETCHCVDPVDCMPLINE01>_".split("");
    const resize = () => {
      w = c.width = window.innerWidth;
      h = c.height = window.innerHeight;
      cols = Math.floor(w / 18);
      drops = Array.from({ length: cols }, () => Math.random() * -80 | 0);
    };
    resize();
    window.addEventListener("resize", resize);
    const draw = () => {
      ctx.fillStyle = "rgba(5,5,8,0.07)";
      ctx.fillRect(0, 0, w, h);
      ctx.font = "13px monospace";
      for (let i = 0; i < cols; i++) {
        const ch = pool[Math.random() * pool.length | 0];
        const y = drops[i] * 18;
        ctx.fillStyle = `rgba(249,115,22,${0.08 + Math.random() * 0.18})`;
        ctx.fillText(ch, i * 18, y);
        if (y > h && Math.random() > 0.975) drops[i] = 0;
        drops[i]++;
      }
    };
    const iv = setInterval(draw, 50);
    return () => { clearInterval(iv); window.removeEventListener("resize", resize); };
  }, []);
  return <canvas ref={ref} style={{ position: "fixed", inset: 0, width: "100%", height: "100%", zIndex: 0 }} />;
}

/* Pixel glyphs live in components/BrandLogo.jsx (GLYPHS/PixelGlyph/FlowWordmark). */

// v8.4.3: auto 4-phase brand reveal. 클릭 필요 없음.
//   typing   — `>FLOW` 한 글자씩 찍힘 (dot pixel)
//   hold     — `>FLOW_` 캐럿 블링크 잠시 (500ms)
//   pulse    — 엔터 친 효과: flash + scale burst (450ms)
//   brand    — clean sans-serif `flow` (lowercase, 최종 상태)
const TERMINAL_SEQ = [">", "F", "L", "O", "W"];
const TYPE_MS = 130;
const HOLD_MS = 500;
const PULSE_MS = 450;

function BrandReveal() {
  const [mode, setMode] = useState("typing");   // typing | hold | pulse | brand
  const [len, setLen] = useState(0);
  const [curOn, setCurOn] = useState(true);

  useEffect(() => {
    if (mode === "typing") {
      if (len < TERMINAL_SEQ.length) {
        const t = setTimeout(() => setLen((n) => n + 1), TYPE_MS);
        return () => clearTimeout(t);
      }
      const t = setTimeout(() => setMode("hold"), 60);
      return () => clearTimeout(t);
    }
    if (mode === "hold") {
      const t = setTimeout(() => setMode("pulse"), HOLD_MS);
      return () => clearTimeout(t);
    }
    if (mode === "pulse") {
      const t = setTimeout(() => setMode("brand"), PULSE_MS);
      return () => clearTimeout(t);
    }
  }, [mode, len]);

  // 캐럿 블링크 — typing/hold 에서만
  useEffect(() => {
    if (mode !== "typing" && mode !== "hold") return;
    const iv = setInterval(() => setCurOn((v) => !v), 530);
    return () => clearInterval(iv);
  }, [mode]);

  if (mode === "brand") {
    return (
      <div style={{ marginBottom: 32, display: "flex", justifyContent: "center", animation: "brandReveal 0.55s cubic-bezier(0.34,1.56,0.64,1) both" }}>
        <FlowWordmark size="login" />
        <style>{`
          @keyframes brandReveal {
            0%   { transform: scale(0.78); opacity: 0; filter: blur(4px); }
            60%  { transform: scale(1.08); opacity: 1; filter: blur(0); }
            100% { transform: scale(1.0);  opacity: 1; filter: blur(0); }
          }
        `}</style>
      </div>
    );
  }

  // typing / hold / pulse — dot pixel glyphs with enter-pulse animation.
  const sz = 10;
  const lgp = 6;
  const shown = TERMINAL_SEQ.slice(0, len);
  const pulseAnim = mode === "pulse"
    ? "enterKeyPulse 0.45s cubic-bezier(0.34,1.56,0.64,1) both"
    : "none";
  return (
    <div
      style={{
        marginBottom: 32,
        display: "flex",
        justifyContent: "center",
        alignItems: "flex-end",
        gap: lgp,
        minHeight: 7 * 12 + 6 * 2,  // reserve height so card doesn't jump
        animation: pulseAnim,
      }}
    >
      {shown.map((ch, i) => (
        <PixelGlyph key={i} ch={ch} sz={sz} strong={mode === "pulse"} />
      ))}
      {(mode === "typing" || mode === "hold") && (
        <div style={{ opacity: curOn ? 1 : 0, transition: "opacity 0.08s" }}>
          <PixelGlyph ch="_" sz={sz} />
        </div>
      )}
      <style>{`
        @keyframes enterKeyPulse {
          0%   { transform: scale(1.0);  filter: brightness(1.0) drop-shadow(0 0 2px #f9731633); }
          30%  { transform: scale(1.22); filter: brightness(1.9) drop-shadow(0 0 18px #f97316dd); }
          65%  { transform: scale(0.96); filter: brightness(1.3) drop-shadow(0 0 8px #f9731699); }
          100% { transform: scale(1.0);  filter: brightness(1.0) drop-shadow(0 0 6px #f9731688); }
        }
      `}</style>
    </div>
  );
}

/* ═══ Login ═══ */
export default function My_Login({ onLogin }) {
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  // v8.8.27: 회원가입 시 실명(name) 수집 — 동명이인 대비 + 이름 검색 지원.
  const [nm, setNm] = useState("");
  const [mode, setMode] = useState("login");
  const [msg, setMsg] = useState("");
  const [loading, setLoading] = useState(false);
  const [formIn, setFormIn] = useState(true);  // v8.2.0: show form immediately

  const submit = async () => {
    setLoading(true); setMsg("");
    try {
      if (mode === "login") {
        const r = await fetch("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: u, password: p }) });
        const d = await r.json();
        if (!r.ok) { setMsg(d.detail || "Login failed"); setLoading(false); return; }
        onLogin(d);
      } else if (mode === "register") {
        // v8.8.27: name 필드도 함께 전송. BE 는 비어있어도 수락.
        const r = await fetch("/api/auth/register", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: u, password: p, name: nm.trim() }) });
        const d = await r.json();
        if (!r.ok) { setMsg(d.detail || "Registration failed"); setLoading(false); return; }
        setMsg("Registered! Waiting for admin approval."); setMode("login"); setP(""); setNm("");
      } else if (mode === "reset") {
        if (!u) { setMsg("Enter username or email first"); setLoading(false); return; }
        const r = await fetch("/api/auth/forgot-password", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: u }) });
        const d = await r.json();
        setMsg(r.ok ? (d.message || "Temporary password sent by email.") : (d.detail || "Error"));
      }
    } catch { setMsg("Connection failed"); }
    setLoading(false);
  };

  const isOk = msg.includes("Wait") || msg.includes("sent");

  const inputStyle = {
    width: "100%", padding: "11px 14px", borderRadius: 3,
    border: "1px solid #2a2a2a", background: "rgba(0,0,0,0.5)", color: "#d4d4d4",
    fontSize: 13, outline: "none", marginBottom: 14,
    fontFamily: "'JetBrains Mono',monospace", letterSpacing: .5, caretColor: "#f97316",
    transition: "border-color 0.2s, box-shadow 0.2s",
    boxSizing: "border-box",
  };
  const onF = e => { e.target.style.borderColor = "#f97316"; e.target.style.boxShadow = "0 0 8px rgba(249,115,22,0.15)"; };
  const onB = e => { e.target.style.borderColor = "#2a2a2a"; e.target.style.boxShadow = "none"; };

  return (
    <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", background: "#050508", overflow: "hidden", position: "relative" }}>
      <MatrixRain />
      {/* scanlines */}
      <div style={{ position: "fixed", inset: 0, background: "repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,0,0,0.05) 2px,rgba(0,0,0,0.05) 4px)", pointerEvents: "none", zIndex: 3 }} />
      {/* vignette */}
      <div style={{ position: "fixed", inset: 0, background: "radial-gradient(ellipse at center,transparent 40%,rgba(0,0,0,0.65) 100%)", pointerEvents: "none", zIndex: 2 }} />

      <div style={{ position: "relative", zIndex: 4, display: "flex", flexDirection: "column", alignItems: "center" }}>
        <BrandReveal />

        {/* Card */}
        <div style={{
          width: 360, background: "rgba(12,12,15,0.9)", borderRadius: 10,
          padding: "28px 30px 22px", border: "1px solid #1a1a1e",
          boxShadow: "0 0 60px rgba(249,115,22,0.04), 0 20px 80px rgba(0,0,0,0.5), inset 0 1px 0 rgba(255,255,255,0.02)",
          backdropFilter: "blur(16px)",
          opacity: formIn ? 1 : 0, transform: formIn ? "translateY(0)" : "translateY(10px)",
          transition: "opacity 0.5s ease, transform 0.5s ease",
        }}>
          {/* v8.8.27: register 모드면 NAME 을 맨 위에 배치 — 이름·아이디·비번 순으로 수집. */}
          {mode === "register" && <>
            <div style={{ fontSize: 10, color: "#555", fontFamily: "'JetBrains Mono',monospace", marginBottom: 5, letterSpacing: 1.5, fontWeight: 600 }}>NAME</div>
            <input value={nm} onChange={e => setNm(e.target.value)} style={inputStyle} onFocus={onF} onBlur={onB} onKeyDown={e => e.key === "Enter" && submit()} autoComplete="name" placeholder="이름" />
          </>}

          <div style={{ fontSize: 10, color: "#555", fontFamily: "'JetBrains Mono',monospace", marginBottom: 5, letterSpacing: 1.5, fontWeight: 600 }}>
            {mode === "register" ? "USERNAME (ID)" : mode === "reset" ? "USERNAME / EMAIL" : "USERNAME"}
          </div>
          <input
            value={u}
            onChange={e => setU(e.target.value)}
            style={inputStyle}
            onFocus={onF}
            onBlur={onB}
            onKeyDown={e => e.key === "Enter" && submit()}
            autoComplete="username"
            placeholder={mode === "register" ? "knox id" : mode === "reset" ? "registered username or email" : ""}
          />

          {(mode === "login" || mode === "register") && <>
            <div style={{ fontSize: 10, color: "#555", fontFamily: "'JetBrains Mono',monospace", marginBottom: 5, letterSpacing: 1.5, fontWeight: 600 }}>PASSWORD</div>
            <input value={p} onChange={e => setP(e.target.value)} type="password" style={inputStyle} onFocus={onF} onBlur={onB} onKeyDown={e => e.key === "Enter" && submit()} autoComplete="current-password" />
          </>}

          <button onClick={submit} disabled={loading}
            onMouseEnter={e => { if (!loading) { e.target.style.background = "#ea580c"; e.target.style.boxShadow = "0 0 20px rgba(249,115,22,0.3)"; } }}
            onMouseLeave={e => { e.target.style.background = "#f97316"; e.target.style.boxShadow = "0 0 10px rgba(249,115,22,0.1)"; }}
            style={{
              width: "100%", padding: "12px", borderRadius: 3, border: "none",
              background: "#f97316", color: "#000", fontSize: 13, fontWeight: 800,
              cursor: loading ? "wait" : "pointer", opacity: loading ? 0.5 : 1,
              marginTop: 2, fontFamily: "'JetBrains Mono',monospace",
              letterSpacing: 2, textTransform: "uppercase",
              boxShadow: "0 0 10px rgba(249,115,22,0.1)", transition: "all 0.2s",
            }}>
            {loading ? "..." : mode === "login" ? "Sign In" : mode === "register" ? "Create Account" : "Send Temp Password"}
          </button>

          {msg && <div style={{
            marginTop: 12, fontSize: 12, textAlign: "center", lineHeight: 1.6, padding: "8px 12px", borderRadius: 4,
            fontFamily: "'JetBrains Mono',monospace",
            color: isOk ? "#4ade80" : "#fb7185",
            background: isOk ? "rgba(34,197,94,0.06)" : "rgba(248,113,113,0.06)",
            border: `1px solid ${isOk ? "rgba(34,197,94,0.12)" : "rgba(248,113,113,0.12)"}`,
          }}>{msg}</div>}

          <div style={{ margin: "18px 0 14px", borderTop: "1px solid #1a1a1e" }} />

          {mode === "login" ? (
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span onClick={() => { setMode("register"); setMsg(""); }}
                onMouseEnter={e => e.target.style.color = "#f97316"}
                onMouseLeave={e => e.target.style.color = "#555"}
                style={{ cursor: "pointer", fontFamily: "'JetBrains Mono',monospace", fontSize: 11, color: "#555", transition: "color 0.2s" }}>
                Create Account
              </span>
              <span onClick={() => { setMode("reset"); setMsg(""); }}
                onMouseEnter={e => e.target.style.color = "#f97316"}
                onMouseLeave={e => e.target.style.color = "#555"}
                style={{ cursor: "pointer", fontFamily: "'JetBrains Mono',monospace", fontSize: 11, color: "#555", transition: "color 0.2s" }}>
                Forgot Password?
              </span>
            </div>
          ) : (
            <div style={{ textAlign: "center" }}>
              <span onClick={() => { setMode("login"); setMsg(""); }}
                onMouseEnter={e => e.target.style.color = "#f97316"}
                onMouseLeave={e => e.target.style.color = "#555"}
                style={{ cursor: "pointer", fontFamily: "'JetBrains Mono',monospace", fontSize: 11, color: "#555", transition: "color 0.2s" }}>
                Back to Sign In
              </span>
            </div>
          )}

          <div style={{ marginTop: 14, textAlign: "center", fontSize: 9, fontFamily: "'JetBrains Mono',monospace", color: "#1e1e1e", letterSpacing: 1 }}>flow · v8.4.2</div>
        </div>
      </div>
    </div>
  );
}
