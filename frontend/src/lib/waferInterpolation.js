// Fill only shot centers inside the convex hull of measured shots. Values outside
// that hull would be extrapolations and must remain unmeasured on the map.
const keyOf = point => `${Number(point.x)},${Number(point.y)}`;
const cross = (a, b, c) => (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x);

function hull(points) {
  const sorted = [...points].sort((a, b) => a.x - b.x || a.y - b.y);
  if (sorted.length < 3) return [];
  const lower = [], upper = [];
  for (const point of sorted) {
    while (lower.length > 1 && cross(lower[lower.length - 2], lower[lower.length - 1], point) <= 0) lower.pop();
    lower.push(point);
  }
  for (const point of sorted.reverse()) {
    while (upper.length > 1 && cross(upper[upper.length - 2], upper[upper.length - 1], point) <= 0) upper.pop();
    upper.push(point);
  }
  lower.pop(); upper.pop();
  return [...lower, ...upper];
}

function insidePolygon(point, polygon) {
  if (polygon.length < 3) return false;
  const orientation = cross(polygon[0], polygon[1], polygon[2]) >= 0 ? 1 : -1;
  return polygon.every((vertex, index) => orientation * cross(vertex, polygon[(index + 1) % polygon.length], point) >= -1e-9);
}

export function interpolateWaferShots(shots, measuredPoints, neighborCount = 4) {
  const measured = new Map();
  for (const point of measuredPoints || []) {
    const x = Number(point.x), y = Number(point.y), value = Number(point.value ?? point.y);
    if ([x, y, value].every(Number.isFinite)) measured.set(`${x},${y}`, { x, y, value, n: point.n, interpolated: false });
  }
  const observations = [...measured.values()];
  const boundary = hull(observations);
  if (boundary.length < 3) return { values: measured, interpolatedCount: 0 };
  let interpolatedCount = 0;
  for (const shot of shots || []) {
    const x = Number(shot.x), y = Number(shot.y), key = keyOf(shot);
    if (!Number.isFinite(x) || !Number.isFinite(y) || measured.has(key) || !insidePolygon({ x, y }, boundary)) continue;
    const neighbors = observations.map(point => ({ point, distance2: (point.x - x) ** 2 + (point.y - y) ** 2 }))
      .sort((a, b) => a.distance2 - b.distance2).slice(0, Math.max(3, neighborCount));
    if (neighbors.length < 3) continue;
    const totalWeight = neighbors.reduce((sum, item) => sum + 1 / item.distance2, 0);
    const value = neighbors.reduce((sum, item) => sum + item.point.value / item.distance2, 0) / totalWeight;
    if (!Number.isFinite(value)) continue;
    measured.set(key, { value, interpolated: true });
    interpolatedCount += 1;
  }
  return { values: measured, interpolatedCount };
}
