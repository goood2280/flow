export function historyIdFromLocation(pattern) {
  const value = new URLSearchParams(window.location.search || "").get("history_id") || "";
  return pattern.test(value) ? value : "";
}

export function historyShareUrl(path, historyId) {
  const url = new URL(path, window.location.origin);
  url.searchParams.set("history_id", String(historyId || "").trim());
  return url.toString();
}

export async function copyHistoryShareLink(path, historyId) {
  await navigator.clipboard.writeText(historyShareUrl(path, historyId));
}
