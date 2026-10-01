import { useId } from "react";

// Isometric app pictograms in the IBM manner: flat faces, no outlines, light
// from the top-left (top = light, front-left = base, front-right = dark).
// Shapes are written in world units (x → right-down, y → left-down, z → up)
// and projected once; each icon's viewBox is fitted to its own bounds so every
// tile carries the same visual weight. Tones map to CSS classes in layouts.css:
// c = the tile group's main hue, b = its accent (one element per icon),
// p = paper, k = ink.
//
// Solids are rounded like emoji: the body is inset by a radius and then
// stroked in its own color with round joins, which restores the size and
// softens every silhouette corner.
//
// These shapes are also the source of the 3D icon set: scripts/home-icons-3d
// renders the same ICONS as lit solids into icons3d/<key>.webp. A baked image
// wins when it exists; the SVG remains the fallback for any key without one.

const COS30 = Math.sqrt(3) / 2;
const ELLIPSE_RX = Math.SQRT2 * COS30;
const ELLIPSE_RY = Math.SQRT2 / 2;
const SPHERE_R = Math.sqrt(1.5);

const ROUND = 0.9;
const roundFor = (...sizes) => Math.min(ROUND, 0.3 * Math.min(...sizes));

const project = ([x, y, z]) => [(x - y) * COS30, (x + y) / 2 - z];
const n = (value) => Number(value.toFixed(2));
const pt = ([x, y]) => `${n(x)} ${n(y)}`;

function polygon(cls, points) {
  return { cls, points, d: `M${points.map(pt).join("L")}Z` };
}

function polyline(cls, points, width) {
  return { cls, points, width, d: `M${points.map(pt).join("L")}` };
}

const poly3 = (cls, points) => polygon(cls, points.map(project));
const top = (cls, z, points) => poly3(cls, points.map(([x, y]) => [x, y, z]));
const frontLeft = (cls, y, points) => poly3(cls, points.map(([x, z]) => [x, y, z]));
const topLine = (cls, z, points, width = 0.9) => polyline(cls, points.map(([x, y]) => project([x, y, z])), width);
const frontLeftLine = (cls, y, points, width = 0.9) => polyline(cls, points.map(([x, z]) => project([x, y, z])), width);
const rect = (a, b, w, h) => [[a, b], [a + w, b], [a + w, b + h], [a, b + h]];
const solid = (items, r) => items.map((item) => ({ ...item, cls: `${item.cls} hi-solid`, width: 2 * r }));

function box(tone, bx, by, bz, bw, bd, bh) {
  const r = roundFor(bw, bd, bh);
  const [x, y, z] = [bx + r, by + r, bz + r];
  const [x1, y1, z1] = [bx + bw - r, by + bd - r, bz + bh - r];
  return solid([
    frontLeft(`hi-${tone}l`, y1, [[x, z], [x1, z], [x1, z1], [x, z1]]),
    poly3(`hi-${tone}r`, [[x1, y, z], [x1, y1, z], [x1, y1, z1], [x1, y, z1]]),
    top(`hi-${tone}t`, z1, [[x, y], [x1, y], [x1, y1], [x, y1]]),
  ], r);
}

function ellipse(cls, cx, cy, rx, ry) {
  return { cls, ellipse: { cx: n(cx), cy: n(cy), rx: n(rx), ry: n(ry) }, points: [[cx - rx, cy - ry], [cx + rx, cy + ry]] };
}

// A flat circle lying on the plane z.
function disc(cls, x, y, z, r) {
  const [cx, cy] = project([x, y, z]);
  return ellipse(cls, cx, cy, r * ELLIPSE_RX, r * ELLIPSE_RY);
}

// Upright cylinder; the side is split into a base (left) and a shaded (right) half.
function cylinder(tone, x, y, cz, cr, ch) {
  const round = roundFor(cr * 2, ch);
  const [z, r, h] = [cz + round, cr - round, ch - 2 * round];
  const [bx, by] = project([x, y, z]);
  const rx = r * ELLIPSE_RX;
  const ry = r * ELLIPSE_RY;
  const ty = by - h;
  const left = `M${pt([bx - rx, ty])}L${pt([bx - rx, by])}A${n(rx)} ${n(ry)} 0 0 0 ${pt([bx, by + ry])}L${pt([bx, ty + ry])}A${n(rx)} ${n(ry)} 0 0 1 ${pt([bx - rx, ty])}Z`;
  const right = `M${pt([bx, ty + ry])}L${pt([bx, by + ry])}A${n(rx)} ${n(ry)} 0 0 0 ${pt([bx + rx, by])}L${pt([bx + rx, ty])}A${n(rx)} ${n(ry)} 0 0 1 ${pt([bx, ty + ry])}Z`;
  const bounds = [[bx - rx, ty - ry], [bx + rx, by + ry]];
  return solid([
    { cls: `hi-${tone}l`, d: left, points: bounds },
    { cls: `hi-${tone}r`, d: right, points: bounds },
    ellipse(`hi-${tone}t`, bx, ty, rx, ry),
  ], round);
}

function sphere(tone, x, y, z, r) {
  const [cx, cy] = project([x, y, z]);
  const R = r * SPHERE_R;
  const bounds = [[cx - R, cy - R], [cx + R, cy + R]];
  return [
    { cls: `hi-${tone}l`, d: `M${pt([cx, cy - R])}A${n(R)} ${n(R)} 0 0 0 ${pt([cx, cy + R])}Z`, points: bounds },
    { cls: `hi-${tone}r`, d: `M${pt([cx, cy - R])}A${n(R)} ${n(R)} 0 0 1 ${pt([cx, cy + R])}Z`, points: bounds },
  ];
}

function signedArea(points) {
  return points.reduce((sum, p, i) => {
    const q = points[(i + 1) % points.length];
    return sum + p[0] * q[1] - q[0] * p[1];
  }, 0);
}

// Extrude an x-y outline upward from z by h.
function prismZ(tone, outline, pz, ph) {
  const round = roundFor(ph);
  const [z, h] = [pz + round, ph - 2 * round];
  const ring = signedArea(outline) > 0 ? outline : [...outline].reverse();
  const sides = [];
  ring.forEach((p, i) => {
    const q = ring[(i + 1) % ring.length];
    const nx = q[1] - p[1];
    const ny = p[0] - q[0];
    if (nx + ny <= 1e-6) return;
    sides.push({
      depth: p[0] + q[0] + p[1] + q[1],
      item: poly3(ny >= nx ? `hi-${tone}l` : `hi-${tone}r`, [[p[0], p[1], z], [q[0], q[1], z], [q[0], q[1], z + h], [p[0], p[1], z + h]]),
    });
  });
  sides.sort((a, b) => a.depth - b.depth);
  return solid([...sides.map((side) => side.item), top(`hi-${tone}t`, z + h, ring)], round);
}

// Extrude an x-z outline toward the viewer from y by d; the front face is at y + d.
function prismY(tone, outline, py, pd) {
  const round = roundFor(pd);
  const [y, d] = [py + round, pd - 2 * round];
  const ring = signedArea(outline) > 0 ? outline : [...outline].reverse();
  const sides = [];
  ring.forEach((p, i) => {
    const q = ring[(i + 1) % ring.length];
    const nx = q[1] - p[1];
    const nz = p[0] - q[0];
    if (nx + nz <= 1e-6) return;
    sides.push({
      depth: p[0] + q[0] + p[1] + q[1],
      item: poly3(nz >= nx ? `hi-${tone}t` : `hi-${tone}r`, [[p[0], y, p[1]], [q[0], y, q[1]], [q[0], y + d, q[1]], [p[0], y + d, p[1]]]),
    });
  });
  sides.sort((a, b) => a.depth - b.depth);
  return solid([...sides.map((side) => side.item), frontLeft(`hi-${tone}l`, y + d, ring)], round);
}

function convexHull(points) {
  const sorted = [...points].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const half = (list) => {
    const out = [];
    for (const p of list) {
      while (out.length >= 2 && cross(out[out.length - 2], out[out.length - 1], p) <= 0) out.pop();
      out.push(p);
    }
    out.pop();
    return out;
  };
  return [...half(sorted), ...half([...sorted].reverse())];
}

// A round body lying on its back (axis along y): one solid side, then the face.
// Faceted prism sides would band and seam at icon size, so the side is the hull.
function drum(tone, outline, py, pd) {
  const round = roundFor(pd);
  const [y, d] = [py + round, pd - 2 * round];
  const back = outline.map(([x, z]) => project([x, y, z]));
  const front = outline.map(([x, z]) => project([x, y + d, z]));
  return solid([polygon(`hi-${tone}r`, convexHull([...back, ...front])), frontLeft(`hi-${tone}l`, y + d, outline)], round);
}

const circle = (cx, cy, r, from = 0, to = Math.PI * 2, steps = 40) =>
  Array.from({ length: steps + 1 }, (_, i) => {
    const a = from + ((to - from) * i) / steps;
    return [cx + r * Math.sin(a), cy + r * Math.cos(a)];
  });

function gear(teeth, outer, inner) {
  const points = [];
  const step = (Math.PI * 2) / teeth;
  for (let i = 0; i < teeth; i += 1) {
    const a = i * step;
    for (const [r, da] of [[inner, -0.3], [outer, -0.18], [outer, 0.18], [inner, 0.3]]) {
      points.push([r * Math.cos(a + da * step * 1.6), r * Math.sin(a + da * step * 1.6)]);
    }
  }
  return points;
}

const grid = (count, start, size, gap) => Array.from({ length: count }, (_, i) => start + i * (size + gap));

const ICONS = {
  // Database browser: a three-tier storage cylinder.
  filebrowser: () => [
    ...cylinder("c", 0, 0, 0, 6, 3.4),
    ...cylinder("c", 0, 0, 4.2, 6, 3.4),
    ...cylinder("b", 0, 0, 8.4, 6, 3.4),
  ],
  // Dashboard: a monitor showing widget tiles.
  dashboard: () => [
    ...box("k", 3.5, -1.5, 0, 7, 4, 0.8),
    ...box("k", 6, 0, 0.8, 2, 1, 2.4),
    ...box("k", 0, 0, 3, 14, 1, 10.5),
    frontLeft("hi-pt", 1, rect(0.8, 3.8, 12.4, 8.9)),
    frontLeft("hi-cl", 1, rect(1.6, 10.6, 10.8, 1.3)),
    frontLeft("hi-ct", 1, rect(1.6, 4.6, 4.8, 5.2)),
    frontLeft("hi-cl", 1, rect(7.4, 4.6, 1.3, 2.4)),
    frontLeft("hi-cl", 1, rect(9.2, 4.6, 1.3, 3.8)),
    frontLeft("hi-bl", 1, rect(11, 4.6, 1.4, 5.2)),
  ],
  // Split table: a sheet with a raised header row and a highlighted split column.
  splittable: () => [
    ...box("p", 0, 0, 0, 14, 12, 2.2),
    ...grid(3, 3.2, 0, 2.4).map((y) => topLine("hi-line", 2.2, [[0.6, y + 2.2], [13.4, y + 2.2]], 0.4)),
    topLine("hi-line", 2.2, [[4.8, 3], [4.8, 11.4]], 0.4),
    ...box("c", 0, 0, 2.2, 14, 2.8, 1.6),
    ...box("b", 9.4, 3.4, 2.2, 4.2, 8.2, 1.1),
  ],
  // Lots: a stack of wafers with a die grid on top.
  lotmanage: () => [
    ...cylinder("c", 0, 0, 0, 7, 1),
    ...cylinder("b", 0, 0, 2.8, 7, 1),
    ...cylinder("c", 0, 0, 5.6, 7, 1),
    ...grid(5, -5.1, 1.6, 0.5).flatMap((x) =>
      grid(5, -5.1, 1.6, 0.5)
        .filter((y) => Math.hypot(x + 0.8, y + 0.8) < 5.4)
        .map((y) => top("hi-cl", 6.6, rect(x, y, 1.6, 1.6))),
    ),
  ],
  // Product wiki: two stacked books.
  productwiki: () => [
    ...box("c", 0, 0, 0, 13, 9, 2.8),
    frontLeft("hi-pt", 9, rect(0.3, 0.4, 12.2, 2)),
    ...box("b", 1.2, 0.6, 2.8, 10.6, 7.6, 2.4),
    frontLeft("hi-pt", 8.2, rect(1.5, 3.2, 9.8, 1.6)),
    ...box("c", 2.6, 1.2, 5.2, 7.8, 6.2, 2),
    frontLeft("hi-pt", 7.4, rect(2.9, 5.5, 7, 1.3)),
  ],
  // Memory cache: a RAM module in its slot.
  ramcache: () => [
    ...box("k", 0, 0, 0, 16, 2.4, 1.2),
    ...box("c", 0.6, 0.7, 1.2, 14.8, 1, 6.4),
    ...grid(4, 1.4, 2.8, 0.8).flatMap((x, i) => box(i === 2 ? "b" : "k", x, 1.7, 2.6, 2.8, 0.5, 3.6)),
  ],
  // Matching fill: a cell grid where the last missing cell drops into place.
  matchfill: () => {
    const cells = [];
    for (const [i, j] of [[0, 0], [1, 0], [0, 1], [2, 0], [1, 1], [0, 2], [2, 1], [1, 2], [2, 2]]) {
      const x = 0.8 + i * 4.2;
      const y = 0.8 + j * 4.2;
      if ((i === 2 && j === 2) || (i === 0 && j === 2)) cells.push(top("hi-pr", 1, rect(x, y, 3.4, 3.4)));
      else cells.push(...box("c", x, y, 1, 3.4, 3.4, 1.4));
    }
    return [...box("p", 0, 0, 0, 13.4, 13.4, 1), ...cells, ...box("b", 9.2, 9.2, 4.2, 3.4, 3.4, 3.4)];
  },
  // File check: two source sheets converging on a checked reference table.
  filecheck: () => [
    ...box("p", 0, 0, 0, 12, 10, 1.4),
    ...box("c", 1, 1, 1.4, 3.2, 3.2, 1.2),
    ...box("c", 1, 5.6, 1.4, 3.2, 3.2, 1.2),
    ...box("b", 6, 2.6, 1.4, 5, 5, 1.5),
    topLine("hi-ink", 2.9, [[7, 5], [8.1, 6.1], [10.2, 3.8]], 0.9),
  ],
  // Chart builder: a query source feeding a chart board (scatter + fitted trend).
  chartbuilder: () => [
    ...box("p", 0, 0, 0, 13, 0.9, 11),
    frontLeftLine("hi-ink", 0.9, [[1.4, 9.8], [1.4, 1.4], [11.8, 1.4]], 0.7),
    ...[[3, 2.4], [4.4, 5.6], [5.9, 3.4], [7.3, 7.6], [8.8, 4.8], [10.3, 9]].map(([x, z]) => {
      // Screen-round markers: a projected circle would shear into a leaf shape.
      const [cx, cy] = project([x, 0.9, z]);
      return ellipse("hi-cl", cx, cy, 0.85, 0.85);
    }),
    frontLeftLine("hi-brand-line", 0.9, [[2.4, 2.6], [11.2, 8.8]], 1),
    ...[0, 1.9, 3.8].flatMap((z, i) => cylinder(i === 2 ? "b" : "c", 0.8, 7.2, z, 2.4, 1.5)),
  ],
  // Template report: a deck of slides, the front one laid out.
  templatereport: () => [
    ...box("p", 0, 0, 0, 13, 0.6, 9.5),
    ...box("p", 0, 2.4, 0, 13, 0.6, 9.5),
    ...box("p", 0, 4.8, 0, 13, 0.6, 9.5),
    frontLeft("hi-cl", 5.4, rect(1, 7.4, 11, 1.2)),
    frontLeft("hi-pr", 5.4, rect(1, 5.6, 5.4, 0.6)),
    frontLeft("hi-pr", 5.4, rect(1, 4.2, 4.2, 0.6)),
    frontLeft("hi-ct", 5.4, rect(7.4, 1.2, 1.4, 2.8)),
    frontLeft("hi-cl", 5.4, rect(9.1, 1.2, 1.4, 4.2)),
    frontLeft("hi-bl", 5.4, rect(10.8, 1.2, 1.4, 5.4)),
  ],
  // Scheduled report: a document with a clock resting on it.
  autoreport: () => [
    ...box("p", 0, 0, 0, 11, 14, 1.8),
    ...grid(4, 2, 0.9, 1.6).map((y) => top("hi-pr", 1.8, rect(1.6, y, 7.4, 0.9))),
    ...cylinder("b", 8.6, 11.4, 1.8, 4.4, 2.6),
    disc("hi-pt", 8.6, 11.4, 4.4, 3.5),
    topLine("hi-ink", 4.4, [[6.9, 9.7], [8.6, 11.4], [10.9, 9.1]], 0.8),
  ],
  // Request board: notes pinned to a standing board.
  lotrequest: () => [
    ...box("k", 1.5, -1, 0, 2, 3, 0.8),
    ...box("k", 11.5, -1, 0, 2, 3, 0.8),
    ...box("c", 0, 0, 0.8, 15, 1, 11.5),
    ...[[1.2, 6.8, "p"], [8.1, 6.8, "b"], [1.2, 1.8, "p"], [8.1, 1.8, "p"]].flatMap(([x, z, tone]) => box(tone, x, 1, z, 5.7, 0.5, 4.2)),
  ],
  // Analysis request: a request sheet with rows and a lens resting on it.
  analysisrequest: () => [
    ...box("p", 0, 0, 0, 12, 14, 1.4),
    ...grid(4, 2, 0.9, 1.7).map((y) => top("hi-pr", 1.4, rect(1.5, y, 6.6, 0.9))),
    ...grid(4, 2, 0.9, 1.7).map((y) => top("hi-ct", 1.4, rect(8.8, y, 1.6, 0.9))),
    ...cylinder("b", 9.4, 10.6, 1.4, 3.2, 1.2),
    disc("hi-pt", 9.4, 10.6, 2.6, 2.4),
  ],
  // Current WIP location: a pin above one step of a process line.
  lotlocation: () => {
    const [gx, gy] = project([10.1, 2.8, 2.6]);
    const R = 3.6;
    const hx = gx;
    const hy = gy - 9;
    const lt = [hx - R * 0.87, hy + R * 0.5];
    const rt = [hx + R * 0.87, hy + R * 0.5];
    const peak = [hx, hy - R];
    const bounds = [[hx - R, hy - R], [gx, gy]];
    return [
      ...box("p", 0, 0, 0, 17, 5.6, 0.8),
      top("hi-pr", 0.8, rect(0.6, 2.5, 15.8, 0.6)),
      ...grid(4, 0.9, 2.8, 1.4).flatMap((x) => box("c", x, 1.4, 0.8, 2.8, 2.8, 1.8)),
      disc("hi-kl", 10.1, 2.8, 2.6, 1.1),
      { cls: "hi-bl", d: `M${pt([gx, gy])}L${pt(lt)}A${R} ${R} 0 0 1 ${pt(peak)}Z`, points: bounds },
      { cls: "hi-br", d: `M${pt([gx, gy])}L${pt(peak)}A${R} ${R} 0 0 1 ${pt(rt)}Z`, points: bounds },
      ellipse("hi-pt", hx, hy, R * 0.42, R * 0.42),
    ];
  },
  // Inform mail: an envelope with a notification badge.
  inform: () => [
    ...box("p", 0, 0, 0, 15, 10.5, 2.4),
    topLine("hi-fold", 2.4, [[1.4, 9.1], [6.6, 5.2]], 0.6),
    topLine("hi-fold", 2.4, [[13.6, 9.1], [8.4, 5.2]], 0.6),
    top("hi-cl", 2.4, [[0.9, 0.9], [14.1, 0.9], [7.5, 6.2]]),
    ...cylinder("b", 12.6, 8.4, 2.4, 2.4, 2),
  ],
  // Meeting: people around a round table.
  meeting: () => {
    const person = (tone, x, y) => [...cylinder(tone, x, y, 0, 1.7, 3.6), ...sphere(tone, x, y, 5.4, 1.4)];
    return [
      ...person("c", -7, -1.5),
      ...person("c", -1.5, -7),
      ...cylinder("k", 0, 0, 0, 1.2, 2.6),
      ...cylinder("p", 0, 0, 2.6, 4.6, 1.3),
      ...person("b", 7.4, 1),
      ...person("c", 1, 7.4),
    ];
  },
  // Calendar: a tear-off block with a date grid.
  calendar: () => [
    ...box("p", 0, 0, 0, 13, 13, 3),
    ...[0.9, 1.9].map((z) => frontLeft("hi-pr", 13, rect(0, z, 13, 0.25))),
    ...box("c", 0, 0, 3, 13, 3.4, 1.4),
    ...grid(3, 1.6, 2.6, 1.3).flatMap((x) =>
      grid(2, 5.4, 2.6, 1.3).map((y) => top(x === 1.6 + 2 * 3.9 && y === 5.4 + 3.9 ? "hi-bl" : "hi-pr", 3, rect(x, y, 2.6, 2.6))),
    ),
  ],
  // Tracker: a gantt of staggered bars.
  tracker: () => [
    ...box("p", 0, 0, 0, 17, 11.5, 1.6),
    ...box("c", 1, 1, 1.6, 7, 2.6, 2.6),
    ...box("b", 5.2, 4.5, 1.6, 7.6, 2.6, 2.6),
    ...box("c", 9.4, 8, 1.6, 6.6, 2.6, 2.6),
  ],
  // LOT tracker: a lot climbing its process steps toward the forecast target flag.
  // Steps rise toward the back so nearer (lower) steps never cover the lot.
  lottracker: () => {
    const steps = [4, 3, 2, 1, 0].flatMap((i) => box(i <= 2 ? "c" : "p", 0, 9 - 3 * i, 0, 6, 3, 1.4 * (i + 1)));
    return [
      ...steps,
      ...[2.2, 0.9].map((y) => topLine("hi-ink", 5.6, [[3, y], [3, y - 0.6]], 0.5)),
      ...[4.2, 5.1, 6].flatMap((z) => cylinder("b", 3, 4.5, z, 1.5, 0.7)),
      ...box("k", 2.8, -1.7, 7, 0.4, 0.4, 6),
      ...prismY("c", [[3.2, 12.8], [3.2, 10.2], [6.6, 11.5]], -1.7, 0.4),
    ];
  },
  // Alarms: a warning beacon.
  valve: () => {
    const [cx, cy] = project([0, 0, 7.2]);
    const rays = [[-1, -0.35], [-0.55, -1], [0.55, -1], [1, -0.35]].map(([dx, dy]) =>
      polyline("hi-brand-line", [[cx + dx * 7.4, cy + dy * 5.2], [cx + dx * 9.4, cy + dy * 6.6]], 1.2),
    );
    return [
      ...box("k", -5, -5, 0, 10, 10, 1.6),
      ...cylinder("c", 0, 0, 1.6, 3.8, 4.6),
      ...sphere("c", 0, 0, 6.2, 3.1),
      ...rays,
    ];
  },
  // TEG map: a shot grid with a crosshair on one scribe crossing.
  teg: () => [
    ...box("p", 0, 0, 0, 14, 14, 2),
    ...grid(3, 0.8, 3.6, 0.8).flatMap((x) => grid(3, 0.8, 3.6, 0.8).map((y) => top("hi-ct", 2, rect(x, y, 3.6, 3.6)))),
    disc("hi-brand-line", 9.2, 9.2, 2, 2.6),
    topLine("hi-brand-line", 2, [[9.2, 5.4], [9.2, 13]], 0.7),
    topLine("hi-brand-line", 2, [[5.4, 9.2], [13, 9.2]], 0.7),
  ],
  // Yield map: a wafer with passing and failing dies.
  yieldmap: () => {
    const fails = new Set(["1,4", "4,1", "3,3", "5,4"]);
    const dies = [];
    grid(7, -7.35, 1.6, 0.5).forEach((x, i) => {
      grid(7, -7.35, 1.6, 0.5).forEach((y, j) => {
        if (Math.hypot(x + 0.8, y + 0.8) > 6.3) return;
        dies.push(top(fails.has(`${i},${j}`) ? "hi-bl" : "hi-cl", 1.2, rect(x, y, 1.6, 1.6)));
      });
    });
    return [...cylinder("p", 0, 0, 0, 7.8, 1.2), ...dies];
  },
  // ET time: a stopwatch.
  ettime: () => [
    ...box("k", -1, 1, 13.2, 2, 1.4, 1.4),
    ...drum("c", circle(0, 7, 6.4), 0, 3),
    frontLeft("hi-pt", 3, circle(0, 7, 5.2)),
    frontLeft("hi-bl", 3, [[0, 7], ...circle(0, 7, 4.2, 0, Math.PI / 2, 12)]),
    frontLeftLine("hi-ink", 3, [[0, 7], [-2.4, 9.4]], 0.9),
  ],
  // ET index download: an arrow dropping into a tray.
  reformatize: () => [
    ...box("c", 0, 0, 0, 14, 10, 3),
    top("hi-cr", 3, rect(1.2, 1.2, 11.6, 7.6)),
    ...prismY("b", [[5.5, 15], [8.5, 15], [8.5, 9], [11, 9], [7, 4.6], [3, 9], [5.5, 9]], 3.8, 2.4),
  ],
  // DCOP rule check: a clipboard with passes and one violation.
  dcop: () => [
    ...box("c", 0, 0, 0, 11.5, 15, 1.8),
    ...box("p", 1, 1.6, 1.8, 9.5, 12.4, 0.4),
    ...box("k", 3.8, 0.3, 1.8, 4, 2.6, 1.2),
    ...[4.4, 8, 11.6].flatMap((y, i) => [
      i < 2
        ? topLine("hi-line", 2.2, [[2.2, y], [3, y + 0.8], [4.4, y - 0.9]], 0.7)
        : topLine("hi-brand-line", 2.2, [[2.3, y - 0.8], [3.9, y + 0.8], [3.1, y], [2.3, y + 0.8], [3.9, y - 0.8]], 0.7),
      top("hi-pr", 2.2, rect(5.4, y - 0.4, 3.8, 0.8)),
    ]),
  ],
  // Admin settings: a gear with a brand hub.
  admin: () => [
    ...prismZ("c", gear(8, 7.4, 5.6), 0, 2.6),
    disc("hi-cr", 0, 0, 2.6, 2.8),
    ...cylinder("b", 0, 0, 1.2, 2, 3),
  ],
};

const FALLBACK = () => [
  ...box("c", 0, 0, 0, 5, 5, 5),
  ...box("c", 6, 0, 0, 5, 5, 3),
  ...box("c", 0, 6, 0, 5, 5, 3),
  ...box("b", 6, 6, 0, 5, 5, 5),
];

const PADDING = 0.03;

// Faces get a second, translucent coat so the flat tones read as lit volume:
// top faces carry a sheen that fades from the lit top-left corner, side faces a
// vertical ramp (light rim at the top edge, occlusion at the floor).
const FACE_RE = /hi-[cbpk]([tlr])/;

function build(render) {
  const items = render();
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const item of items) {
    const pad = item.width ? item.width / 2 : 0;
    for (const [x, y] of item.points) {
      minX = Math.min(minX, x - pad);
      minY = Math.min(minY, y - pad);
      maxX = Math.max(maxX, x + pad);
      maxY = Math.max(maxY, y + pad);
    }
  }
  const size = Math.max(maxX - minX, maxY - minY) * (1 + PADDING * 2);
  const viewBox = [(minX + maxX - size) / 2, (minY + maxY - size) / 2, size, size].map(n).join(" ");
  const groundRx = (maxX - minX) * 0.44;
  const groundRy = groundRx * 0.2;
  const ground = { cx: n((minX + maxX) / 2), cy: n(maxY - groundRy * 0.5), rx: n(groundRx), ry: n(groundRy) };
  const glazed = items.map((item) => {
    const face = FACE_RE.exec(item.cls)?.[1];
    return { ...item, glaze: face ? (face === "t" ? "sheen" : "ramp") : null };
  });
  return { viewBox, ground, items: glazed };
}

function Shape({ item, className, fill, stroke }) {
  const props = { className, fill, stroke, strokeWidth: item.width };
  return item.ellipse ? <ellipse {...item.ellipse} {...props} /> : <path d={item.d} {...props} />;
}

const cache = new Map();

function iconFor(appKey) {
  const key = ICONS[appKey] ? appKey : "";
  if (!cache.has(key)) cache.set(key, build(ICONS[appKey] || FALLBACK));
  return cache.get(key);
}

export const HOME_APP_ICON_KEYS = Object.keys(ICONS);

// 128px WebP 는 vite.config.js(assetsInlineLimit)가 홈 청크에 data URL 로 넣는다 —
// 타일마다 이미지 요청(최대 24개)을 보내 서버를 기다리던 지연·팝인 없이 한 번에 그린다.
const BAKED = import.meta.glob("./icons3d/*.webp", { eager: true, import: "default" });

export default function HomeAppIcon({ appKey }) {
  const uid = `hi${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const baked = BAKED[`./icons3d/${appKey}.webp`];
  if (baked) return <img className="home-app-icon home-app-icon--3d" src={baked} width={58} height={58} decoding="sync" alt="" aria-hidden="true" draggable={false} />;
  const { viewBox, ground, items } = iconFor(appKey);
  const ref = (name) => `url(#${uid}-${name})`;
  return (
    <svg className="home-app-icon" viewBox={viewBox} aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id={`${uid}-sheen`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" className="hi-g-light" stopOpacity="0.6" />
          <stop offset="0.55" className="hi-g-light" stopOpacity="0.08" />
          <stop offset="1" className="hi-g-light" stopOpacity="0" />
        </linearGradient>
        <linearGradient id={`${uid}-ramp`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" className="hi-g-light" stopOpacity="0.28" />
          <stop offset="0.45" className="hi-g-light" stopOpacity="0" />
          <stop offset="1" className="hi-g-dark" stopOpacity="0.3" />
        </linearGradient>
        <radialGradient id={`${uid}-ground`}>
          <stop offset="0" className="hi-g-dark" stopOpacity="0.32" />
          <stop offset="1" className="hi-g-dark" stopOpacity="0" />
        </radialGradient>
      </defs>
      <ellipse className="hi-ground" {...ground} fill={ref("ground")} />
      {items.map((item, i) => (
        <g key={i}>
          <Shape item={item} className={item.cls} />
          {item.glaze && (
            <Shape
              item={item}
              className="hi-glaze"
              fill={ref(item.glaze)}
              stroke={item.width ? ref(item.glaze) : undefined}
            />
          )}
        </g>
      ))}
    </svg>
  );
}
