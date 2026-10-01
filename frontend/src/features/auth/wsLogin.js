// 사내 WebSocket 로그인 — 브라우저 쪽 통신만 맡는다.
// 프레임에서 ID·토큰·주소를 찾고 인증서버에 다시 확인하는 판정은 서버
// (backend/core/auth_providers.py WebsocketAuthProvider.authenticate) 가 한다.
//
// 흐름: new WebSocket(ws_url) → 연결되면 send 전송 → 받은 프레임마다(순서대로 하나씩)
//   POST /api/auth/sso/ws/login {message, step, via}
//   200 세션 → 로그인 끝
//   400 ID/토큰 없는 중간 프레임 → 다음 프레임을 기다린다
//   202 {action:"open", url}    → 인증 창을 열고 같은 연결에서 다음 프레임을 기다린다
//   202 {action:"browser", url} → 브라우저 자격(쿠키·Windows 인증)으로 주소를 읽어 그 본문을 다시 POST
//   그 외 → 오류 문구로 끝
// 받은 프레임 원문은 브라우저 개발자 도구 Network → WS → Messages 에서 본다.

const WAIT_MS = 60000;
const AFTER_URL_WAIT_MS = 180000;

// ws_url·send 의 {nonce}(시도마다 새 값)·{origin}(Flow 주소)을 채운다.
export function fillTemplate(text, vars) {
  return String(text || "").replace(/\{(nonce|origin)\}/g, (_, key) => vars[key] || "");
}

function randomNonce() {
  try {
    const bytes = new Uint8Array(12);
    window.crypto.getRandomValues(bytes);
    return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  } catch (_) {
    return `${Date.now().toString(16)}${Math.random().toString(16).slice(2, 10)}`;
  }
}

// 텍스트·Blob·ArrayBuffer 프레임을 모두 문자열로 바꾼다.
export async function frameText(data) {
  if (typeof data === "string") return data;
  try {
    if (typeof Blob !== "undefined" && data instanceof Blob) return await data.text();
    if (data instanceof ArrayBuffer) return new TextDecoder("utf-8").decode(data);
  } catch (_) { /* 아래 String() 으로 */ }
  return String(data ?? "");
}

// 인증 창으로 열어도 되는 주소(http/https)만 통과시킨다.
export function safeHttpUrl(value) {
  try {
    const url = new URL(String(value || ""));
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : "";
  } catch (_) {
    return "";
  }
}

/**
 * provider: /api/auth/providers 의 websocket 항목 {ws_url, send, login_url, url_action, contact}
 * handlers: onUrl(url) 인증 창 열기 · onDone({session}|{error}|{info}|{cancelled})
 * 돌려주는 함수를 부르면 시도를 취소한다.
 */
export function startWsLogin(provider, { onUrl = () => {}, onDone = () => {} } = {}) {
  const vars = { nonce: randomNonce(), origin: window.location.origin };
  const wsUrl = fillTemplate(provider.ws_url, vars);
  const sendText = fillTemplate(provider.send, vars);
  const loginUrl = provider.login_url || "/api/auth/sso/ws/login";
  let finished = false;
  let socket = null;
  let timer = 0;
  let frames = 0;
  let urlShown = false;
  let chain = Promise.resolve();

  const finish = (result) => {
    if (finished) return;
    finished = true;
    window.clearTimeout(timer);
    try { socket?.close(); } catch (_) { /* already closed */ }
    onDone(result);
  };
  const arm = (ms) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => finish({ error: urlShown
      ? "인증 창에서 로그인을 마친 뒤 다시 [사내 로그인]을 누르세요."
      : "사내 인증서버 응답이 없습니다. 잠시 후 다시 시도하세요." }), ms);
  };

  const post = async (message, step, via) => {
    const r = await fetch(loginUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, step, via }),
    });
    const data = await r.json().catch(() => ({}));
    return { status: r.status, ok: r.ok, data };
  };

  const handle = async (text, step, via) => {
    const res = await post(text, step, via);
    if (res.ok && res.status === 200) return finish({ session: res.data });
    if (res.status === 400) return undefined;
    if (res.status === 202) {
      const url = safeHttpUrl(res.data.url);
      if (!url) return finish({ error: "인증서버가 보낸 주소를 열 수 없습니다." });
      if (res.data.action === "browser" && via !== "browser_fetch") {
        let body = "";
        try {
          const r = await fetch(url, { credentials: "include", cache: "no-store" });
          body = await r.text();
        } catch (_) {
          return finish({ error: "인증 주소를 브라우저에서 읽지 못했습니다(CORS 또는 네트워크). 관리자에게 알려 주세요." });
        }
        return handle(body, step, "browser_fetch");
      }
      urlShown = true;
      arm(AFTER_URL_WAIT_MS);
      onUrl(url);
      return undefined;
    }
    let message = typeof res.data.detail === "string" ? res.data.detail : "사내 로그인에 실패했습니다.";
    if (provider.contact && !message.includes("문의")) message += ` 문의 ${provider.contact}`;
    return finish({ error: message, status: res.status });
  };

  arm(WAIT_MS);
  try {
    socket = new WebSocket(wsUrl);
  } catch (_) {
    finish({ error: "사내 인증서버에 연결하지 못했습니다." });
    return () => {};
  }
  socket.binaryType = "arraybuffer";
  socket.onopen = () => { if (sendText) socket.send(sendText); };
  socket.onerror = () => {
    chain = chain.then(() => finish({ error: "사내 인증서버에 연결하지 못했습니다." }));
  };
  socket.onmessage = (event) => {
    // 프레임은 받은 순서대로 하나씩 서버에 넘긴다.
    chain = chain.then(async () => {
      if (finished) return;
      frames += 1;
      await handle(await frameText(event.data), frames, "ws");
    }).catch(() => finish({ error: "로그인 처리 중 오류가 발생했습니다." }));
  };
  socket.onclose = () => {
    chain = chain.then(() => {
      if (finished) return;
      finish(urlShown
        ? { info: "인증 창에서 로그인을 마친 뒤 다시 [사내 로그인]을 누르세요." }
        : { error: frames ? "사내 인증서버가 로그인 정보 없이 연결을 닫았습니다." : "사내 인증서버가 아무 메시지 없이 연결을 닫았습니다." });
    });
  };
  return () => finish({ cancelled: true });
}
