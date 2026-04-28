// lib/api.js v8.4.6 — shared fetch, download, and query-string helpers.
// v8.4.6: 모든 fetch 에 X-Session-Token 자동 주입 + 401 → 글로벌 로그아웃 이벤트.
//   - 토큰은 localStorage.hol_user.token 에 저장 (로그인 응답에서 발급).
//   - 401 수신 시 localStorage 정리 후 window 에 `flow:session-expired` 디스패치.
//     App.jsx 가 이를 수신해 로그인 화면으로 되돌림.
//   - 모든 API 호출은 idle 타이머 reset 트리거 — window 에 `flow:activity` 발행.

function _getToken() {
  try {
    const raw = localStorage.getItem("hol_user");
    if (!raw) return "";
    const o = JSON.parse(raw);
    return (o && o.token) || "";
  } catch (_) { return ""; }
}

function _withAuthHeaders(opts) {
  const h = new Headers((opts && opts.headers) || {});
  const tk = _getToken();
  if (tk && !h.has("X-Session-Token")) h.set("X-Session-Token", tk);
  return { ...(opts || {}), headers: h };
}

function _onAuthFailure() {
  try { localStorage.removeItem("hol_user"); } catch (_) {}
  try { window.dispatchEvent(new CustomEvent("flow:session-expired")); } catch (_) {}
}

function _touchActivity() {
  try { window.dispatchEvent(new CustomEvent("flow:activity")); } catch (_) {}
}

function _detailText(detail) {
  if (detail === undefined || detail === null || detail === "") return "";
  return typeof detail === "string" ? detail : JSON.stringify(detail);
}

function _formatApiError(status, body, fallback) {
  if (body && body.error_code === "router_load_failed") {
    const parts = [
      _detailText(body.detail) || ("HTTP " + status),
      body.router_error ? "\n[router diagnostics]\n" + body.router_error : "",
    ].filter(Boolean);
    return parts.join("\n");
  }
  if (body && body.detail) return _detailText(body.detail);
  return fallback || ("HTTP " + status);
}

function _throwApiError(status, body, fallback) {
  const err = new Error(_formatApiError(status, body, fallback));
  err.status = status;
  err.body = body;
  throw err;
}

// v8.7.1: 브라우저 <img>/<a download> 는 커스텀 헤더를 못 실어서 X-Session-Token
// 를 URL 쿼리로 붙인다. 서버가 ?t=<token> fallback 을 수락하는 엔드포인트에서만 사용.
export function authSrc(url) {
  if (!url) return url;
  const tk = _getToken();
  if (!tk) return url;
  const sep = url.includes("?") ? "&" : "?";
  return url + sep + "t=" + encodeURIComponent(tk);
}

// v8.8.27: 유저 라벨 헬퍼 — 동명이인 대비 + 이름 검색.
//   입력: {username, name} (name 은 optional)
//   출력: name 있으면 "홍길동 (hol)", 없으면 "hol".
//   전역에서 같은 포맷을 쓰기 위해 lib 에 집중.
export function userLabel(u) {
  if (!u) return "";
  const nm = ((u.name ?? u.display_name) || "").toString().trim();
  const un = (u.username || "").toString().trim();
  if (nm && un) return nm + " (" + un + ")";
  return un || nm;
}

// 검색용 — name/username 둘 다 매칭. q 가 공백이면 항상 true.
export function userMatches(u, q) {
  if (!q || !q.trim()) return true;
  const needle = q.trim().toLowerCase();
  const nm = ((u?.name ?? u?.display_name) || "").toString().toLowerCase();
  const un = (u?.username || "").toString().toLowerCase();
  const em = (u?.email || u?.effective_email || "").toString().toLowerCase();
  return nm.includes(needle) || un.includes(needle) || em.includes(needle);
}

export function qs(params) {
  const parts = [];
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v === undefined || v === null || v === "") return;
    parts.push(encodeURIComponent(k) + "=" + encodeURIComponent(v));
  });
  return parts.length ? "?" + parts.join("&") : "";
}

export function sf(url, opts) {
  const _isApi = typeof url === "string" && url.startsWith("/api/");
  if (_isApi) _touchActivity();
  return fetch(url, _withAuthHeaders(opts)).then(async (r) => {
    if (r.status === 401 && _isApi) {
      _onAuthFailure();
      throw new Error("Session expired — please log in again");
    }
    if (!r.ok) {
      let body = null;
      try {
        const ct = r.headers.get("content-type") || "";
        if (ct.includes("json")) body = await r.json();
      } catch (_) {}
      _throwApiError(r.status, body, "HTTP " + r.status);
    }
    const ct = r.headers.get("content-type") || "";
    return ct.includes("json") ? r.json() : r.text();
  });
}

export function postJson(url, body) {
  return sf(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
}

// Stream a URL to a file download. Returns promise resolving once triggered.
export function dl(url, filename) {
  const _isApi = typeof url === "string" && url.startsWith("/api/");
  if (_isApi) _touchActivity();
  return fetch(url, _withAuthHeaders()).then(async (r) => {
    if (r.status === 401 && _isApi) {
      _onAuthFailure();
      throw new Error("Session expired");
    }
    if (!r.ok) {
      let body = null;
      try {
        body = await r.json();
      } catch (_) {}
      _throwApiError(r.status, body, "Download failed");
    }
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename || "download.csv";
    document.body.appendChild(a);
    a.click();
    a.remove();
  });
}

// Log user activity (fire-and-forget). v8.4.6: username 은 서버에서 토큰으로 덮어씀 — 여기서는 참고용.
export function logActivity(username, action, detail) {
  postJson("/api/admin/log", { username, action, detail: detail || "" }).catch(() => {});
}
