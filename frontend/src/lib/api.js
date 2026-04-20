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
      let detail = "HTTP " + r.status;
      try {
        const ct = r.headers.get("content-type") || "";
        if (ct.includes("json")) {
          const body = await r.json();
          if (body && body.detail) detail = body.detail;
        }
      } catch (_) {}
      throw new Error(detail);
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
      let detail = "Download failed";
      try {
        const body = await r.json();
        if (body && body.detail) detail = body.detail;
      } catch (_) {}
      throw new Error(detail);
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
