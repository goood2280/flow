import { sf } from "../../lib/api.js";

const RETRYABLE_STATUSES = new Set([408, 429, 500, 502, 503, 504]);

function aborted() {
  return new DOMException("Request cancelled", "AbortError");
}

function waitForRetry(delay, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(aborted()); return; }
    const cleanup = () => signal?.removeEventListener("abort", cancel);
    const cancel = () => { clearTimeout(timer); cleanup(); reject(aborted()); };
    const timer = setTimeout(() => { cleanup(); resolve(); }, delay);
    signal?.addEventListener("abort", cancel, { once: true });
  });
}

// Retry only reads that can recover from a short server/connection interruption.
// Product changes cancel both the request and any pending retry.
export async function fetchSplitTableResource(url, { signal, retries = 2, retryDelayMs = 1000 } = {}, fetchResource = sf) {
  for (let attempt = 0; ; attempt += 1) {
    if (signal?.aborted) throw aborted();
    try {
      const result = await fetchResource(url, { signal });
      if (signal?.aborted) throw aborted();
      return result;
    } catch (error) {
      if (signal?.aborted || error?.name === "AbortError") throw aborted();
      const retryable = RETRYABLE_STATUSES.has(error?.status)
        || (error instanceof TypeError && !error.status);
      if (!retryable || attempt >= retries) throw error;
      await waitForRetry(retryDelayMs * (attempt + 1), signal);
    }
  }
}

export function splitTableResourceError(error) {
  const status = error?.status ? `HTTP ${error.status}` : "";
  const detail = String(error?.message || "서버 연결을 확인해 주세요.").replace(/\s+/g, " ").slice(0, 240);
  return [status, detail === status ? "" : detail].filter(Boolean).join(": ");
}
