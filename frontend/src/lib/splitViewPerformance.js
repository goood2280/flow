export const SPLIT_VIEW_PERF_LIMIT = 40;
export const SPLIT_VIEW_PERF_TARGET_MS = 500;

export function isSplitViewPerformanceEnabled(search = "") {
  try {
    return new URLSearchParams(String(search || "")).get("split_perf") === "1";
  } catch {
    return false;
  }
}

export function appendSplitViewPerformanceSample(samples, sample, limit = SPLIT_VIEW_PERF_LIMIT) {
  const safeLimit = Math.max(1, Number(limit) || SPLIT_VIEW_PERF_LIMIT);
  return [...(Array.isArray(samples) ? samples : []), sample].slice(-safeLimit);
}

function percentile(values, ratio) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const index = Math.max(0, Math.ceil(sorted.length * ratio) - 1);
  return sorted[index];
}

export function summarizeSplitViewPerformance(samples, targetMs = SPLIT_VIEW_PERF_TARGET_MS) {
  const safeSamples = (Array.isArray(samples) ? samples : []).filter(
    (sample) => Number.isFinite(sample?.totalMs),
  );
  if (!safeSamples.length) {
    return { count: 0, last: null, p50Ms: null, p95Ms: null, pass: null };
  }
  const totals = safeSamples.map((sample) => sample.totalMs);
  const p95Ms = percentile(totals, 0.95);
  return {
    count: safeSamples.length,
    last: safeSamples[safeSamples.length - 1],
    p50Ms: percentile(totals, 0.5),
    p95Ms,
    pass: p95Ms <= targetMs,
  };
}
