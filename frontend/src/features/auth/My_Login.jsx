import { useState, useEffect, useRef } from "react";
import { FlowWordmark } from "../../components/BrandLogo";
import "./My_Login.css";

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

/* ═══ Login ═══ */
// 아이디 저장: 비밀번호는 저장하지 않고 아이디만 이 브라우저에 남긴다.
const SAVED_USERNAME_KEY = "flow_saved_username";

function readSavedUsername() {
  try { return String(localStorage.getItem(SAVED_USERNAME_KEY) || ""); } catch { return ""; }
}

function writeSavedUsername(value) {
  try {
    if (value) localStorage.setItem(SAVED_USERNAME_KEY, value);
    else localStorage.removeItem(SAVED_USERNAME_KEY);
  } catch { /* 저장소가 막힌 브라우저에서는 저장 없이 로그인만 한다 */ }
}

export default function My_Login({ onLogin }) {
  const [u, setU] = useState(readSavedUsername);
  const [rememberId, setRememberId] = useState(() => !!readSavedUsername());
  const [p, setP] = useState("");
  const [releaseVersion, setReleaseVersion] = useState("");
  // v8.8.27: 회원가입 시 실명(name) 수집 — 동명이인 대비 + 이름 검색 지원.
  const [nm, setNm] = useState("");
  const [mode, setMode] = useState("login");
  const [msg, setMsg] = useState("");
  const [loading, setLoading] = useState(false);
  const [authProviders, setAuthProviders] = useState(null);

  useEffect(() => {
    let active = true;
    fetch("/deploy-info.json", { cache: "no-store" })
      .then((response) => response.ok ? response.json() : null)
      .then((data) => {
        const version = String(data?.version || "").trim();
        if (active && version) setReleaseVersion(version);
      })
      .catch(() => {});
    return () => { active = false; };
  }, []);

  // 서버가 활성화한 방식만 노출한다. OIDC 환경변수가 아직 없으면 password만,
  // 연결 후에는 SSO 버튼도 표시한다. password provider를 끄면 SSO-only 화면이다.
  useEffect(() => {
    let active = true;
    fetch("/api/auth/providers", { cache: "no-store" })
      .then((response) => response.ok ? response.json() : Promise.reject(new Error("providers")))
      .then((data) => {
        if (active) setAuthProviders(Array.isArray(data?.providers) ? data.providers : []);
      })
      .catch(() => {
        // 구버전 백엔드/일시 오류에서는 기존 ID/PW 로그인을 잃지 않는다.
        if (active) setAuthProviders([{ name: "password", kind: "password", label: "ID / PW" }]);
      });
    return () => { active = false; };
  }, []);

  // 아이디는 앞뒤 공백만 걷어내고 그대로 보낸다. `hong` 과 `hong@사내도메인` 을
  // 같은 계정으로 보는 판정은 서버(core.auth.canonical_username)가 한다 — 프런트가
  // 도메인을 추측해서 잘라내면 서버 규칙과 어긋난다.
  const uid = u.trim();
  const passwordEnabled = authProviders === null || authProviders.some((provider) => provider?.name === "password");
  const ssoProviders = (authProviders || []).filter((provider) => provider?.kind === "sso" && provider?.start_url);
  const wsProvider = (authProviders || []).find((provider) => provider?.kind === "websocket" && provider?.ws_url);
  const recoveryEnabled = passwordEnabled && !wsProvider;
  useEffect(() => {
    if (!recoveryEnabled && mode === "reset") setMode("login");
  }, [recoveryEnabled, mode]);
  // 접속 IP 로그인 — 등록된 PC 에서 버튼 하나로 들어온다(서버가 접속 주소로 사용자를 정한다).
  const ipProvider = (authProviders || []).find((provider) => provider?.kind === "ip");
  const ipLogin = async () => {
    setLoading(true); setMsg("");
    try {
      const r = await fetch(ipProvider?.login_url || "/api/auth/sso/ip/login", { method: "POST" });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { setMsg(d.detail || "로그인에 실패했습니다."); setLoading(false); return; }
      setLoading(false);
      onLogin(d);
    } catch (_) {
      setMsg("서버에 연결하지 못했습니다."); setLoading(false);
    }
  };

  // 사내 websocket 인증서버에 브라우저가 직접 접속한다. 받은 메시지를 Flow 에 넘기면
  // 서버가 인증서버에 토큰을 다시 확인한 뒤 세션을 연다(사용자 정보는 저장하지 않음).
  const wsLogin = (provider, { silent = false } = {}) => {
    if (!provider?.ws_url) return;
    setLoading(true);
    if (!silent) setMsg("");
    let done = false;
    let socket;
    const finish = (text) => {
      if (done) return;
      done = true;
      setLoading(false);
      if (text && !silent) setMsg(text);
      try { socket?.close(); } catch (_) { /* already closed */ }
    };
    const timer = window.setTimeout(() => finish("사내 인증서버 응답이 없습니다. 잠시 후 다시 시도하세요."), 60000);
    try {
      socket = new WebSocket(provider.ws_url);
    } catch (_) {
      window.clearTimeout(timer);
      finish("사내 인증서버에 연결하지 못했습니다.");
      return;
    }
    socket.onopen = () => { if (provider.send) socket.send(provider.send); };
    socket.onerror = () => { window.clearTimeout(timer); finish("사내 인증서버에 연결하지 못했습니다."); };
    socket.onmessage = async (event) => {
      if (done) return;
      try {
        const r = await fetch(provider.login_url || "/api/auth/sso/ws/login", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: event.data }),
        });
        const d = await r.json();
        // 인증 정보가 아직 없는 중간 메시지(400)는 다음 메시지를 기다린다.
        if (r.status === 400) return;
        window.clearTimeout(timer);
        if (!r.ok) { finish(d.detail || "사내 로그인에 실패했습니다."); return; }
        finish("");
        onLogin(d);
      } catch (_) {
        window.clearTimeout(timer);
        finish("로그인 처리 중 오류가 발생했습니다.");
      }
    };
  };

  // websocket 이 유일한(또는 자동) 로그인 수단이면 화면을 열자마자 한 번 시도한다.
  const wsAutoTried = useRef(false);
  useEffect(() => {
    if (!wsProvider || wsAutoTried.current || !wsProvider.auto) return;
    wsAutoTried.current = true;
    wsLogin(wsProvider, { silent: passwordEnabled });
  }, [wsProvider]);

  const submit = async () => {
    setLoading(true); setMsg("");
    try {
      if (mode === "login") {
        const r = await fetch("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: uid, password: p }) });
        const d = await r.json();
        if (!r.ok) { setMsg(d.detail || "Login failed"); setLoading(false); return; }
        writeSavedUsername(rememberId ? uid : "");
        onLogin(d);
      } else if (mode === "register") {
        // v8.8.27: name 필드도 함께 전송. BE 는 비어있어도 수락.
        const r = await fetch("/api/auth/register", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: uid, password: p, name: nm.trim() }) });
        const d = await r.json();
        if (!r.ok) { setMsg(d.detail || "Registration failed"); setLoading(false); return; }
        setMsg("Registered! Waiting for admin approval."); setMode("login"); setP(""); setNm("");
      } else if (mode === "reset") {
        if (!recoveryEnabled) { setMode("login"); setLoading(false); return; }
        if (!uid) { setMsg("Enter username first"); setLoading(false); return; }
        const r = await fetch("/api/auth/forgot-password", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: uid }) });
        const d = await r.json();
        setMsg(r.ok ? (d.message || "Temporary password sent by email.") : (d.detail || "Error"));
      }
    } catch { setMsg("Connection failed"); }
    setLoading(false);
  };

  const isOk = msg.includes("Wait") || msg.includes("sent");

  const title = mode === "register" ? "회원가입" : mode === "reset" ? "비밀번호 재설정" : "로그인";

  return (
    <main className="flow-login">
      <MatrixRain />
      <div className="flow-login__vignette" aria-hidden="true" />
      <div className="flow-login__content">
        <div className="flow-login__brand" aria-label="flow">
          <FlowWordmark size="login" />
        </div>

        <section className="flow-login__card" aria-label={title}>
          {mode !== "login" && <header className="flow-login__header">
            <h1 id="flow-login-title">{title}</h1>
            <p>{mode === "register" ? "사용할 계정 정보를 입력해 주세요." : "가입한 아이디로 임시 비밀번호를 받으세요."}</p>
          </header>}

          {mode === "login" && (ipProvider || wsProvider || ssoProviders.length > 0) && (
            <div className="flow-login__providers">
              {ipProvider && (
                <button type="button" className="flow-login__provider" disabled={loading} onClick={ipLogin}>
                  {loading ? "로그인 중…" : (ipProvider.label || "로그인")}
                </button>
              )}
              {wsProvider && (
                <button type="button" className="flow-login__provider" disabled={loading} onClick={() => wsLogin(wsProvider)}>
                  {loading ? "사내 인증 확인 중…" : (wsProvider.label || "사내 로그인")}
                </button>
              )}
              {ssoProviders.map((provider) => (
                <button key={provider.name} type="button" className="flow-login__provider" disabled={loading} onClick={() => window.location.assign(provider.start_url)}>
                  {provider.label || "SSO"} 로그인
                </button>
              ))}
            </div>
          )}

          {mode === "login" && passwordEnabled && (ssoProviders.length > 0 || wsProvider || ipProvider) && (
            <div className="flow-login__divider"><span>또는 아이디로 로그인</span></div>
          )}

          {passwordEnabled && (
            <form className="flow-login__form" onSubmit={e => { e.preventDefault(); if (!loading) submit(); }}
              onKeyDown={e => { if (e.key === "Enter" && (e.nativeEvent?.isComposing || e.keyCode === 229)) e.preventDefault(); }}>
              {mode === "register" && (
                <div className="flow-login__field">
                  <label htmlFor="flow-login-name">이름</label>
                  <input id="flow-login-name" value={nm} onChange={e => setNm(e.target.value)} autoComplete="name" placeholder="이름을 입력하세요" />
                </div>
              )}
              <div className="flow-login__field">
                <label htmlFor="flow-login-username">아이디</label>
                <input id="flow-login-username" value={u} onChange={e => setU(e.target.value)} autoComplete="username" autoCapitalize="none" spellCheck={false}
                  placeholder={mode === "login" ? "아이디를 입력하세요" : "Knox ID"}
                  aria-describedby={mode !== "login" ? "flow-login-id-hint" : undefined} />
                {mode !== "login" && (
                  <p id="flow-login-id-hint" className="flow-login__hint">아이디 또는 id@메일도메인으로 입력해 주세요. 둘 다 같은 계정으로 인식합니다.</p>
                )}
              </div>
              {(mode === "login" || mode === "register") && (
                <div className="flow-login__field">
                  <label htmlFor="flow-login-password">비밀번호</label>
                  <input id="flow-login-password" value={p} onChange={e => setP(e.target.value)} type="password"
                    autoFocus={mode === "login" && rememberId && !!uid}
                    autoComplete={mode === "register" ? "new-password" : "current-password"} placeholder="비밀번호를 입력하세요" />
                </div>
              )}
              {mode === "login" && (
                <label className="flow-login__remember">
                  <input type="checkbox" checked={rememberId} onChange={e => {
                    const next = e.target.checked;
                    setRememberId(next);
                    if (!next) writeSavedUsername("");
                  }} />
                  아이디 저장
                </label>
              )}
              <button type="submit" className="flow-login__submit" disabled={loading}>
                {loading ? "처리 중…" : mode === "login" ? "로그인" : mode === "register" ? "가입 신청" : "임시 비밀번호 받기"}
              </button>
            </form>
          )}

          {msg && <div role="status" className={`flow-login__message${isOk ? " flow-login__message--success" : ""}`}>{msg}</div>}

          {passwordEnabled && (
            <nav className="flow-login__links" aria-label="계정 도움말">
              {mode === "login" ? <>
                <button type="button" onClick={() => { setMode("register"); setMsg(""); }}>회원가입</button>
                {recoveryEnabled && <button type="button" onClick={() => { setMode("reset"); setMsg(""); }}>비밀번호 찾기</button>}
              </> : (
                <button type="button" onClick={() => { setMode("login"); setMsg(""); }}>로그인으로 돌아가기</button>
              )}
            </nav>
          )}
          {authProviders !== null && !passwordEnabled && ssoProviders.length === 0 && !wsProvider && !ipProvider && (
            <div role="status" className="flow-login__message">사용할 수 있는 로그인 방식이 없습니다. 관리자에게 문의하세요.</div>
          )}
        </section>
        {releaseVersion && <div className="flow-login__version">flow · v{releaseVersion}</div>}
      </div>
    </main>
  );
}
