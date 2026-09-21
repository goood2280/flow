const DAY_MS = 86400000;

// Treat FAB timestamps as wall-clock values so DST/browser timezone cannot
// change the number of days between comparison points.
export function buildDpmlComparison(points, rawDpml) {
  const dpml = Number(rawDpml);
  if (!String(rawDpml ?? "").trim() || !Number.isFinite(dpml) || dpml <= 0) return [];
  const layers = new Map();
  for (const point of points || []) {
    if (point.x && point.y && !layers.has(String(point.x))) layers.set(String(point.x), point);
  }
  const ordered = [...layers.values()].sort((a, b) => {
    const na = Number(a.x);
    const nb = Number(b.x);
    return Number.isFinite(na) && Number.isFinite(nb) ? na - nb : String(a.x).localeCompare(String(b.x));
  });
  if (!ordered.length) return [];
  const firstCompleted = (points || []).find((point) => point.x && point.y);
  const anchorIndex = ordered.findIndex((point) => String(point.x) === String(firstCompleted.x));
  const rawTime = String(firstCompleted.y).trim().replace(" ", "T").replace(/(?:Z|[+-]\d{2}:?\d{2})$/, "");
  const anchorTime = Date.parse(`${rawTime}Z`);
  if (!Number.isFinite(anchorTime)) return [];
  const series = `DPML ${dpml} (비교)`;
  const result = [];
  for (let index = anchorIndex; index < ordered.length; index += 1) {
    const time = new Date(anchorTime + (index - anchorIndex) * dpml * DAY_MS);
    if (!Number.isFinite(time.getTime())) return [];
    const point = ordered[index];
    result.push({
      x: point.x,
      x_label: point.x_label,
      y: time.toISOString().slice(0, 19).replace("T", " "),
      series,
      label: `${point.x} · DPML ${dpml} 일/Mask Layer 비교`,
    });
  }
  return result;
}
