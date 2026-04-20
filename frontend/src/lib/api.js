// lib/api.js v4.0.0 — shared fetch, download, and query-string helpers.
// Pages import { sf, postJson, dl, qs } from '../lib/api' to stop redefining them.

export function qs(params) {
  const parts = [];
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v === undefined || v === null || v === "") return;
    parts.push(encodeURIComponent(k) + "=" + encodeURIComponent(v));
  });
  return parts.length ? "?" + parts.join("&") : "";
}

export function sf(url, opts) {
  return fetch(url, opts).then(async (r) => {
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
  return fetch(url).then(async (r) => {
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

// Log user activity (fire-and-forget).
export function logActivity(username, action, detail) {
  postJson("/api/admin/log", { username, action, detail: detail || "" }).catch(() => {});
}
