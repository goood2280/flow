import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './global.css';

// v8.4.7: Global fetch monkey-patch — 모든 /api/* 호출에 X-Session-Token 자동 주입.
// 여러 페이지가 각자 로컬 sf() 에서 raw fetch 를 쓰고 있어도 여기서 일괄로 해결.
// exempt (login/register/reset-request/logout) 은 토큰 없이 그대로 통과.
(function installAuthFetch() {
  if (typeof window === "undefined" || !window.fetch) return;
  if (window.__flowFetchPatched) return;
  window.__flowFetchPatched = true;
  const origFetch = window.fetch.bind(window);
  const EXEMPT = new Set([
    "/api/auth/login", "/api/auth/register",
    "/api/auth/reset-request", "/api/auth/forgot-password", "/api/auth/logout",
  ]);
  const isApi = (u) => typeof u === "string" && u.startsWith("/api/");
  const pathOnly = (u) => {
    try {
      const q = u.indexOf("?");
      return q >= 0 ? u.slice(0, q) : u;
    } catch (_) { return u; }
  };
  const getToken = () => {
    try {
      const raw = localStorage.getItem("hol_user");
      if (!raw) return "";
      const o = JSON.parse(raw);
      return (o && o.token) || "";
    } catch (_) { return ""; }
  };
  window.fetch = function patchedFetch(input, init) {
    const url = typeof input === "string" ? input : (input && input.url) || "";
    if (!isApi(url)) return origFetch(input, init);
    const path = pathOnly(url);
    if (EXEMPT.has(path)) return origFetch(input, init);
    // 활동 이벤트 (idle 타이머 reset 용)
    try { window.dispatchEvent(new CustomEvent("flow:activity")); } catch (_) {}
    const headers = new Headers((init && init.headers) || {});
    const tok = getToken();
    if (tok && !headers.has("X-Session-Token")) headers.set("X-Session-Token", tok);
    const next = { ...(init || {}), headers };
    return origFetch(input, next).then(r => {
      if (r.status === 401) {
        try { localStorage.removeItem("hol_user"); } catch (_) {}
        try { window.dispatchEvent(new CustomEvent("flow:session-expired")); } catch (_) {}
      }
      return r;
    });
  };
})();

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
