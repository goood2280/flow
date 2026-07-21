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
  if (body && body.error) return _detailText(body.error);
  if (body && body.message) return _detailText(body.message);
  if (body && body.errors) return _detailText(body.errors);
  if (body) return _detailText(body);
  return fallback || ("HTTP " + status);
}

const _errorExplainCache = new Map();
const _AUTH_ERROR_EXPLAIN_EXEMPT = new Set([
  "/api/auth/login",
  "/api/auth/register",
  "/api/auth/reset-request",
  "/api/auth/forgot-password",
  "/api/auth/logout",
]);
const _RESOURCE_GUARD_ERROR_EXPLAIN_EXEMPT = new Set([
  "resource_queue_timeout",
  "resource_memory_guard",
]);

function _requestMethod(opts) {
  return String((opts && opts.method) || "GET").toUpperCase();
}

function _pathOnly(url) {
  const q = String(url || "").indexOf("?");
  return q >= 0 ? String(url || "").slice(0, q) : String(url || "");
}

function _compactForExplain(value, limit = 5000) {
  if (value === undefined || value === null || value === "") return "";
  let text = "";
  try {
    text = typeof value === "string" ? value : JSON.stringify(value);
  } catch (_) {
    text = String(value);
  }
  if (text.length > limit) return text.slice(0, limit) + "\n...<truncated>";
  return text;
}

function _canExplainApiError(url) {
  if (typeof window === "undefined") return false;
  if (typeof url !== "string" || !url.startsWith("/api/")) return false;
  const path = _pathOnly(url);
  if (_AUTH_ERROR_EXPLAIN_EXEMPT.has(path)) return false;
  if (path === "/api/llm/error/explain") return false;
  if (path === "/api/llm/status") return false;
  return true;
}

function _explainCacheKey(url, method, status, rawMessage) {
  return [method, url, status, String(rawMessage || "").slice(0, 500)].join("\n");
}

async function _explainApiError(url, opts, status, body, rawMessage) {
  if (!_canExplainApiError(url)) return null;
  if (_RESOURCE_GUARD_ERROR_EXPLAIN_EXEMPT.has(String(body?.error_code || ""))) return null;
  const method = _requestMethod(opts);
  const key = _explainCacheKey(url, method, status, rawMessage);
  if (_errorExplainCache.has(key)) return _errorExplainCache.get(key);
  const payload = {
    status,
    method,
    url,
    page: (window.location && (window.location.pathname + window.location.search)) || "",
    raw_error: _compactForExplain(rawMessage, 6000),
    body: _compactForExplain(body, 6000),
  };
  const promise = fetch("/api/llm/error/explain", _withAuthHeaders({
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Flow-Error-Explain": "1" },
    body: JSON.stringify(payload),
  })).then(async (r) => {
    if (!r.ok) return null;
    const ct = r.headers.get("content-type") || "";
    if (!ct.includes("json")) return null;
    const data = await r.json();
    if (!data || !data.llm || !data.llm.used || !data.message) return null;
    return data;
  }).catch(() => null);
  _errorExplainCache.set(key, promise);
  const out = await promise;
  if (_errorExplainCache.size > 40) {
    const first = _errorExplainCache.keys().next().value;
    _errorExplainCache.delete(first);
  }
  return out;
}

async function _throwApiError(url, opts, status, body, fallback) {
  const rawMessage = _formatApiError(status, body, fallback);
  // v9.3.x: LLM 설명을 동기 대기하지 않고 즉시 에러를 throw.
  // 설명은 백그라운드로 받아 err.explainPromise 에 첨부.
  const err = new Error(rawMessage);
  err.status = status;
  err.body = body;
  err.rawMessage = rawMessage;
  err.explanation = null;
  err.llm = null;
  err.explainPromise = _explainApiError(url, opts, status, body, rawMessage).then(explained => {
    if (explained && explained.message) {
      err.explanation = explained.explanation || null;
      err.llm = explained.llm || null;
      err.message = explained.message;
    }
    return explained;
  }).catch(() => null);
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
      await _throwApiError(url, opts, r.status, body, "HTTP " + r.status);
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

export function putJson(url, body) {
  return sf(url, {
    method: "PUT",
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
      await _throwApiError(url, {}, r.status, body, "Download failed");
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
