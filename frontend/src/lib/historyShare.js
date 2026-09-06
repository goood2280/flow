let _cachedShareBaseUrl = "";

export function setCachedShareBaseUrl(url) {
  _cachedShareBaseUrl = String(url || "").trim().replace(/\/+$/, "");
}

export function getCachedShareBaseUrl() {
  return _cachedShareBaseUrl;
}

export async function fetchShareBaseUrl() {
  if (_cachedShareBaseUrl) return _cachedShareBaseUrl;
  if (typeof fetch !== "undefined") {
    try {
      const res = await fetch("/api/admin/share-base-url");
      if (res.ok) {
        const data = await res.json();
        if (data?.share_base_url) {
          setCachedShareBaseUrl(data.share_base_url);
          return _cachedShareBaseUrl;
        }
      }
    } catch (_e) {
      // ignore
    }
  }
  return "";
}

export function historyIdFromLocation(pattern) {
  if (typeof window === "undefined" || !window.location) return "";
  const value = new URLSearchParams(window.location.search || "").get("history_id") || "";
  return pattern.test(value) ? value : "";
}

export function historyShareUrl(path, historyId, baseUrl = "") {
  const effectiveBase = baseUrl || _cachedShareBaseUrl || (typeof window !== "undefined" && window.location ? window.location.origin : "http://localhost");
  const base = new URL(effectiveBase);
  if (!["http:", "https:"].includes(base.protocol) || base.username || base.password) {
    throw new Error("공유 주소는 http:// 또는 https:// 주소를 입력하세요.");
  }
  base.search = "";
  base.hash = "";
  const url = new URL(path.replace(/^\/+/, ""), base.toString().replace(/\/?$/, "/"));
  url.searchParams.set("history_id", String(historyId || "").trim());
  return url.toString();
}

export async function copyHistoryShareLink(path, historyId, baseUrl = "") {
  let effectiveBase = baseUrl || _cachedShareBaseUrl;
  if (!effectiveBase) {
    effectiveBase = await fetchShareBaseUrl();
  }
  const url = historyShareUrl(path, historyId, effectiveBase);
  await navigator.clipboard.writeText(url);
  return url;
}
