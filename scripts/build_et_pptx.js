#!/usr/bin/env node
const fs = require("fs");
const path = require("path");
const pptxgen = require("pptxgenjs");

const input = process.argv[2];
const output = process.argv[3];
if (!input || !output) {
  console.error("usage: node scripts/build_et_pptx.js payload.json output.pptx");
  process.exit(2);
}

const payload = JSON.parse(fs.readFileSync(input, "utf8"));
const pptx = new pptxgen();
pptx.layout = "LAYOUT_WIDE";
pptx.author = "flow ET Report";
pptx.company = "flow";
pptx.subject = "ET item report";
pptx.title = payload.title || "ET Report";
pptx.lang = "ko-KR";
pptx.theme = {
  headFontFace: "Aptos Display",
  bodyFontFace: "Aptos",
  lang: "ko-KR",
};
pptx.defineLayout({ name: "FLOW_WIDE", width: 13.333, height: 7.5 });
pptx.layout = "FLOW_WIDE";
pptx.margin = 0;

const COLORS = {
  ink: "111827",
  sub: "64748B",
  line: "CBD5E1",
  pale: "F8FAFC",
  card: "FFFFFF",
  blue: "2563EB",
  teal: "0F766E",
  orange: "F97316",
  red: "DC2626",
  purple: "7C3AED",
};

function nfmt(v, digits = 4) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "-";
  if (Math.abs(n) >= 100) return n.toFixed(2);
  if (Math.abs(n) >= 1) return n.toFixed(digits);
  return n.toPrecision(4);
}

function addText(slide, text, x, y, w, h, opts = {}) {
  slide.addText(String(text ?? ""), {
    x, y, w, h,
    margin: 0,
    breakLine: false,
    fit: "shrink",
    fontFace: opts.fontFace || "Aptos",
    fontSize: opts.fontSize || 10,
    bold: !!opts.bold,
    color: opts.color || COLORS.ink,
    align: opts.align || "left",
    valign: opts.valign || "mid",
    ...opts,
  });
}

function addHeader(slide, item) {
  const meta = payload.meta || {};
  slide.background = { color: "F8FAFC" };
  slide.addShape(pptx.ShapeType.rect, { x: 0, y: 0, w: 13.333, h: 0.55, fill: { color: COLORS.ink }, line: { color: COLORS.ink } });
  addText(slide, payload.title || "ET Report", 0.25, 0.12, 2.4, 0.24, { fontSize: 12, bold: true, color: "FFFFFF" });
  addText(slide, `${meta.product || "-"} · ${meta.root_lot_id || "-"} · ${meta.fab_lot_id || "-"} · ${meta.step_id || "-"}`, 2.6, 0.12, 6.4, 0.24, { fontSize: 9, color: "E2E8F0" });
  addText(slide, meta.generated_at || "", 10.05, 0.12, 3.0, 0.24, { fontSize: 8, color: "CBD5E1", align: "right" });
  addText(slide, item.alias || item.item_id || "Item", 0.28, 0.72, 4.5, 0.34, { fontSize: 20, bold: true, color: COLORS.ink });
  addText(slide, `${item.item_id || ""} · ${item.n || 0} points · ${item.package_count || 0} package(s)`, 0.3, 1.08, 5.8, 0.22, { fontSize: 9, color: COLORS.sub });
}

function addPanel(slide, title, x, y, w, h) {
  slide.addShape(pptx.ShapeType.roundRect, {
    x, y, w, h,
    rectRadius: 0.04,
    fill: { color: COLORS.card },
    line: { color: "E2E8F0", width: 0.8 },
  });
  addText(slide, title, x + 0.12, y + 0.08, w - 0.24, 0.2, { fontSize: 9, bold: true, color: COLORS.blue });
}

function addStatsTable(slide, item, x, y, w, h) {
  addPanel(slide, "Statistical Table", x, y, w, h);
  const s = item.stats || {};
  const rows = [
    ["N", item.n],
    ["Mean", nfmt(s.mean)],
    ["Std", nfmt(s.std)],
    ["Min", nfmt(s.min)],
    ["Q1", nfmt(s.q1)],
    ["Median", nfmt(s.median)],
    ["Q3", nfmt(s.q3)],
    ["Max", nfmt(s.max)],
    ["P95", nfmt(s.p95)],
  ];
  const rowH = (h - 0.48) / rows.length;
  rows.forEach((r, i) => {
    const yy = y + 0.36 + i * rowH;
    const fill = i % 2 ? "F8FAFC" : "FFFFFF";
    slide.addShape(pptx.ShapeType.rect, { x: x + 0.12, y: yy, w: w - 0.24, h: rowH, fill: { color: fill }, line: { color: "E2E8F0", transparency: 100 } });
    addText(slide, r[0], x + 0.22, yy + 0.02, 0.8, rowH - 0.04, { fontSize: 8.5, color: COLORS.sub });
    addText(slide, r[1], x + 1.05, yy + 0.02, w - 1.3, rowH - 0.04, { fontSize: 8.5, bold: i === 1, align: "right" });
  });
}

function extents(values, pad = 0.08) {
  const nums = values.map(Number).filter(Number.isFinite);
  if (!nums.length) return [0, 1];
  let lo = Math.min(...nums);
  let hi = Math.max(...nums);
  if (lo === hi) {
    lo -= 1;
    hi += 1;
  }
  const p = (hi - lo) * pad;
  return [lo - p, hi + p];
}

function mapX(v, lo, hi, x, w) {
  return x + ((Number(v) - lo) / (hi - lo)) * w;
}

function mapY(v, lo, hi, y, h) {
  return y + h - ((Number(v) - lo) / (hi - lo)) * h;
}

function addAxes(slide, x, y, w, h, yMin, yMax, xLabel = "", yLabel = "") {
  slide.addShape(pptx.ShapeType.line, { x, y: y + h, w, h: 0, line: { color: COLORS.line, width: 1 } });
  slide.addShape(pptx.ShapeType.line, { x, y, w: 0, h, line: { color: COLORS.line, width: 1 } });
  for (let i = 0; i <= 3; i += 1) {
    const yy = y + h - (h * i / 3);
    slide.addShape(pptx.ShapeType.line, { x, y: yy, w, h: 0, line: { color: "E2E8F0", width: 0.5, transparency: 30 } });
  }
  addText(slide, nfmt(yMax, 3), x - 0.38, y - 0.03, 0.34, 0.14, { fontSize: 6.5, color: COLORS.sub, align: "right" });
  addText(slide, nfmt(yMin, 3), x - 0.38, y + h - 0.08, 0.34, 0.14, { fontSize: 6.5, color: COLORS.sub, align: "right" });
  if (xLabel) addText(slide, xLabel, x + w - 0.8, y + h + 0.06, 0.8, 0.14, { fontSize: 6.5, color: COLORS.sub, align: "right" });
  if (yLabel) addText(slide, yLabel, x - 0.02, y - 0.22, 1.0, 0.14, { fontSize: 6.5, color: COLORS.sub });
}

function addBoxPlot(slide, item, x, y, w, h) {
  addPanel(slide, "Box Plot", x, y, w, h);
  const s = item.stats || {};
  const [lo, hi] = extents([s.min, s.q1, s.median, s.q3, s.max, item.lsl, item.usl]);
  const cx0 = x + 0.55;
  const cy = y + 1.0;
  const bw = w - 1.0;
  const midY = y + h * 0.6;
  const minX = mapX(s.min, lo, hi, cx0, bw);
  const q1X = mapX(s.q1, lo, hi, cx0, bw);
  const medX = mapX(s.median, lo, hi, cx0, bw);
  const q3X = mapX(s.q3, lo, hi, cx0, bw);
  const maxX = mapX(s.max, lo, hi, cx0, bw);
  slide.addShape(pptx.ShapeType.line, { x: minX, y: midY, w: maxX - minX, h: 0, line: { color: COLORS.sub, width: 1.2 } });
  slide.addShape(pptx.ShapeType.line, { x: minX, y: midY - 0.28, w: 0, h: 0.56, line: { color: COLORS.sub, width: 1.2 } });
  slide.addShape(pptx.ShapeType.line, { x: maxX, y: midY - 0.28, w: 0, h: 0.56, line: { color: COLORS.sub, width: 1.2 } });
  slide.addShape(pptx.ShapeType.rect, { x: q1X, y: midY - 0.35, w: Math.max(0.05, q3X - q1X), h: 0.7, fill: { color: "DBEAFE" }, line: { color: COLORS.blue, width: 1.2 } });
  slide.addShape(pptx.ShapeType.line, { x: medX, y: midY - 0.42, w: 0, h: 0.84, line: { color: COLORS.orange, width: 2 } });
  if (Number.isFinite(Number(item.lsl))) {
    const sx = mapX(item.lsl, lo, hi, cx0, bw);
    slide.addShape(pptx.ShapeType.line, { x: sx, y: cy - 0.15, w: 0, h: 1.55, line: { color: COLORS.red, width: 1, dash: "dash" } });
    addText(slide, "LSL", sx - 0.15, cy - 0.32, 0.3, 0.12, { fontSize: 6.5, color: COLORS.red, align: "center" });
  }
  if (Number.isFinite(Number(item.usl))) {
    const sx = mapX(item.usl, lo, hi, cx0, bw);
    slide.addShape(pptx.ShapeType.line, { x: sx, y: cy - 0.15, w: 0, h: 1.55, line: { color: COLORS.red, width: 1, dash: "dash" } });
    addText(slide, "USL", sx - 0.15, cy - 0.32, 0.3, 0.12, { fontSize: 6.5, color: COLORS.red, align: "center" });
  }
  addText(slide, `min ${nfmt(s.min)}   q1 ${nfmt(s.q1)}   med ${nfmt(s.median)}   q3 ${nfmt(s.q3)}   max ${nfmt(s.max)}`, x + 0.22, y + h - 0.3, w - 0.44, 0.16, { fontSize: 7.2, color: COLORS.sub, align: "center" });
}

function addTrend(slide, item, x, y, w, h) {
  addPanel(slide, "Trend", x, y, w, h);
  const rows = item.trend || [];
  const vals = rows.map(r => Number(r.mean)).filter(Number.isFinite);
  const [lo, hi] = extents(vals);
  const px = x + 0.52;
  const py = y + 0.55;
  const pw = w - 0.74;
  const ph = h - 0.95;
  addAxes(slide, px, py, pw, ph, lo, hi, "time", "mean");
  if (rows.length > 1) {
    rows.forEach((r, i) => {
      if (i === 0) return;
      const p = rows[i - 1];
      const x1 = px + ((i - 1) / (rows.length - 1)) * pw;
      const x2 = px + (i / (rows.length - 1)) * pw;
      const y1 = mapY(p.mean, lo, hi, py, ph);
      const y2 = mapY(r.mean, lo, hi, py, ph);
      slide.addShape(pptx.ShapeType.line, { x: x1, y: y1, w: x2 - x1, h: y2 - y1, line: { color: COLORS.teal, width: 1.5 } });
    });
  }
  rows.forEach((r, i) => {
    const xx = rows.length > 1 ? px + (i / (rows.length - 1)) * pw : px + pw / 2;
    const yy = mapY(r.mean, lo, hi, py, ph);
    slide.addShape(pptx.ShapeType.ellipse, { x: xx - 0.035, y: yy - 0.035, w: 0.07, h: 0.07, fill: { color: COLORS.teal }, line: { color: COLORS.teal } });
  });
}

function addRadius(slide, item, x, y, w, h) {
  addPanel(slide, "Radius Plot", x, y, w, h);
  const pts = item.radius || [];
  const vals = pts.map(p => Number(p.value)).filter(Number.isFinite);
  const radii = pts.map(p => Number(p.radius)).filter(Number.isFinite);
  const [vLo, vHi] = extents(vals);
  const rMax = Math.max(1, ...radii);
  const px = x + 0.52;
  const py = y + 0.55;
  const pw = w - 0.74;
  const ph = h - 0.95;
  addAxes(slide, px, py, pw, ph, vLo, vHi, "radius", "value");
  pts.slice(0, 110).forEach(p => {
    const xx = px + (Number(p.radius) / rMax) * pw;
    const yy = mapY(p.value, vLo, vHi, py, ph);
    const color = Number(p.value) > Number(item.usl) || Number(p.value) < Number(item.lsl) ? COLORS.red : COLORS.purple;
    slide.addShape(pptx.ShapeType.ellipse, { x: xx - 0.025, y: yy - 0.025, w: 0.05, h: 0.05, fill: { color, transparency: 5 }, line: { color, transparency: 100 } });
  });
}

function addWfMap(slide, item, x, y, w, h) {
  addPanel(slide, "WF Map", x, y, w, h);
  const pts = (item.wf_map && item.wf_map.length ? item.wf_map : item.radius || [])
    .filter(p => Number.isFinite(Number(p.shot_x)) && Number.isFinite(Number(p.shot_y)) && Number.isFinite(Number(p.value)));
  if (!pts.length) {
    addText(slide, "No shot coordinate", x + 0.22, y + h / 2 - 0.08, w - 0.44, 0.16, { fontSize: 8, color: COLORS.sub, align: "center" });
    return;
  }
  const xs = pts.map(p => Number(p.shot_x));
  const ys = pts.map(p => Number(p.shot_y));
  const vals = pts.map(p => Number(p.value));
  const [xLo, xHi] = extents(xs, 0.12);
  const [yLo, yHi] = extents(ys, 0.12);
  const [vLo, vHi] = extents(vals, 0.02);
  const px = x + 0.28;
  const py = y + 0.42;
  const pw = w - 0.56;
  const ph = h - 0.7;
  slide.addShape(pptx.ShapeType.rect, { x: px, y: py, w: pw, h: ph, fill: { color: "F8FAFC" }, line: { color: "E2E8F0", width: 0.6 } });
  pts.slice(0, 220).forEach(p => {
    const xx = mapX(Number(p.shot_x), xLo, xHi, px + 0.08, pw - 0.16);
    const yy = mapY(Number(p.shot_y), yLo, yHi, py + 0.08, ph - 0.16);
    const v = Number(p.value);
    const bad = (Number.isFinite(Number(item.usl)) && v > Number(item.usl)) || (Number.isFinite(Number(item.lsl)) && v < Number(item.lsl));
    const t = (v - vLo) / Math.max(1e-9, vHi - vLo);
    const color = bad ? COLORS.red : (t > 0.5 ? COLORS.orange : COLORS.blue);
    slide.addShape(pptx.ShapeType.ellipse, { x: xx - 0.035, y: yy - 0.035, w: 0.07, h: 0.07, fill: { color, transparency: 2 }, line: { color, transparency: 100 } });
  });
}

function addCumulative(slide, item, x, y, w, h) {
  addPanel(slide, "Cumulative Plot", x, y, w, h);
  const pts = item.cdf || [];
  const vals = pts.map(p => Number(p.x)).filter(Number.isFinite);
  const [xLo, xHi] = extents(vals);
  const px = x + 0.52;
  const py = y + 0.55;
  const pw = w - 0.74;
  const ph = h - 0.95;
  addAxes(slide, px, py, pw, ph, 0, 1, "value", "cdf");
  pts.forEach((p, i) => {
    if (i === 0) return;
    const prev = pts[i - 1];
    const x1 = mapX(prev.x, xLo, xHi, px, pw);
    const y1 = mapY(prev.p, 0, 1, py, ph);
    const x2 = mapX(p.x, xLo, xHi, px, pw);
    const y2 = mapY(p.p, 0, 1, py, ph);
    slide.addShape(pptx.ShapeType.line, { x: x1, y: y1, w: x2 - x1, h: y2 - y1, line: { color: COLORS.orange, width: 1.4 } });
  });
}

function addSummaryBadges(slide, item) {
  const s = item.stats || {};
  const badges = [
    ["Mean", nfmt(s.mean), COLORS.blue],
    ["Std", nfmt(s.std), COLORS.teal],
    ["Median", nfmt(s.median), COLORS.orange],
    ["Out", String(item.spec_out_points || 0), item.spec_out_points ? COLORS.red : COLORS.teal],
  ];
  badges.forEach((b, i) => {
    const x = 7.15 + i * 1.45;
    slide.addShape(pptx.ShapeType.roundRect, { x, y: 0.74, w: 1.25, h: 0.5, rectRadius: 0.04, fill: { color: "FFFFFF" }, line: { color: "E2E8F0" } });
    addText(slide, b[0], x + 0.08, 0.82, 0.45, 0.13, { fontSize: 6.5, color: COLORS.sub });
    addText(slide, b[1], x + 0.52, 0.78, 0.63, 0.2, { fontSize: 9.5, bold: true, color: b[2], align: "right" });
  });
}

function addScoreboardSlide() {
  const meta = payload.meta || {};
  const rows = (payload.scoreboard || []).slice(0, 24);
  const slide = pptx.addSlide();
  slide.background = { color: "F8FAFC" };
  slide.addShape(pptx.ShapeType.rect, { x: 0, y: 0, w: 13.333, h: 0.55, fill: { color: COLORS.ink }, line: { color: COLORS.ink } });
  addText(slide, payload.title || "ET Measurement Report", 0.25, 0.12, 3.0, 0.24, { fontSize: 12, bold: true, color: "FFFFFF" });
  addText(slide, `${meta.product || "-"} · ${meta.root_lot_id || "-"} · ${meta.fab_lot_id || "-"} · ${meta.step_id || "-"}`, 3.05, 0.12, 6.8, 0.24, { fontSize: 9, color: "E2E8F0" });
  addText(slide, meta.generated_at || "", 10.05, 0.12, 3.0, 0.24, { fontSize: 8, color: "CBD5E1", align: "right" });

  addText(slide, "ET 측정이력", 0.35, 0.82, 2.3, 0.28, { fontSize: 18, bold: true });
  addText(slide, `측정시간 ${meta.package_time || "-"}   Step Seq ${meta.step_seq_points || "-"}   Request ${meta.request_key || "-"}`, 0.35, 1.16, 9.8, 0.22, { fontSize: 9, color: COLORS.sub });

  const summary = [
    ["제품", meta.product || "-"],
    ["Lot", meta.fab_lot_id || meta.root_lot_id || "-"],
    ["Step", meta.step_label || meta.step_id || "-"],
    ["Rows", meta.row_count || 0],
  ];
  summary.forEach((item, i) => {
    const x = 0.35 + i * 2.25;
    slide.addShape(pptx.ShapeType.roundRect, { x, y: 1.52, w: 2.05, h: 0.58, rectRadius: 0.04, fill: { color: "FFFFFF" }, line: { color: "E2E8F0" } });
    addText(slide, item[0], x + 0.12, 1.63, 0.55, 0.15, { fontSize: 7, color: COLORS.sub });
    addText(slide, item[1], x + 0.68, 1.58, 1.2, 0.22, { fontSize: 10, bold: true, color: i === 1 ? COLORS.teal : COLORS.ink, align: "right" });
  });

  const x0 = 0.35;
  const y0 = 2.45;
  const widths = [1.95, 1.8, 1.35, 1.0, 1.0, 1.0, 0.62, 0.78, 1.0];
  const headers = ["Index", "Raw", "Seq/PT", "Mean", "Min", "Max", "Out", "Status", "Spec"];
  let x = x0;
  headers.forEach((h, i) => {
    slide.addShape(pptx.ShapeType.rect, { x, y: y0, w: widths[i], h: 0.26, fill: { color: "E2E8F0" }, line: { color: "CBD5E1", width: 0.4 } });
    addText(slide, h, x + 0.04, y0 + 0.05, widths[i] - 0.08, 0.13, { fontSize: 7.2, bold: true, color: COLORS.sub });
    x += widths[i];
  });
  const rowH = 0.18;
  rows.forEach((r, idx) => {
    const yy = y0 + 0.26 + idx * rowH;
    const fill = idx % 2 ? "FFFFFF" : "F8FAFC";
    const vals = [
      r.alias || "",
      r.rawitem_id || "",
      r.step_seq_points || "",
      nfmt(r.mean_value),
      nfmt(r.min_value),
      nfmt(r.max_value),
      String(r.spec_out_points || 0),
      String(r.status || "").toUpperCase(),
      `${r.spec || "none"} ${r.lsl ?? "-"}~${r.usl ?? "-"}`,
    ];
    x = x0;
    vals.forEach((v, i) => {
      const bad = i === 6 && Number(r.spec_out_points || 0) > 0;
      const statusBad = i === 7 && ["ABNORMAL", "CRITICAL", "WARN"].includes(String(v).toUpperCase());
      slide.addShape(pptx.ShapeType.rect, { x, y: yy, w: widths[i], h: rowH, fill: { color: fill }, line: { color: "E2E8F0", width: 0.25 } });
      addText(slide, v, x + 0.04, yy + 0.025, widths[i] - 0.08, 0.11, {
        fontSize: 6.4,
        color: bad || statusBad ? COLORS.red : COLORS.ink,
        bold: bad || statusBad || i === 0,
        align: i >= 3 && i <= 6 ? "right" : "left",
      });
      x += widths[i];
    });
  });
  if (!rows.length) {
    addText(slide, "No scoreboard rows matched the selected ET measurement.", x0, y0 + 0.6, 7.0, 0.25, { fontSize: 11, color: COLORS.sub });
  }
}

if (payload.mode === "scoreboard") {
  addScoreboardSlide();
  fs.mkdirSync(path.dirname(output), { recursive: true });
  (async () => {
    await pptx.writeFile({ fileName: output });
  })().catch((err) => {
    console.error(err && err.stack ? err.stack : String(err));
    process.exit(1);
  });
} else {

const items = payload.items || [];
if (!items.length) {
  const slide = pptx.addSlide();
  slide.background = { color: "F8FAFC" };
  addText(slide, "ET Report", 0.4, 0.5, 4, 0.4, { fontSize: 24, bold: true });
  addText(slide, "No item data matched the selected lot / step.", 0.4, 1.1, 6, 0.3, { fontSize: 12, color: COLORS.sub });
}

items.forEach(item => {
  const slide = pptx.addSlide();
  addHeader(slide, item);
  addSummaryBadges(slide, item);
  addStatsTable(slide, item, 0.28, 1.52, 2.05, 5.45);
  addBoxPlot(slide, item, 2.52, 1.52, 3.1, 2.0);
  addWfMap(slide, item, 5.82, 1.52, 2.95, 2.0);
  addTrend(slide, item, 8.97, 1.52, 4.08, 2.0);
  addRadius(slide, item, 2.52, 3.78, 5.05, 3.18);
  addCumulative(slide, item, 7.78, 3.78, 5.27, 3.18);
});

fs.mkdirSync(path.dirname(output), { recursive: true });
(async () => {
  await pptx.writeFile({ fileName: output });
})().catch((err) => {
  console.error(err && err.stack ? err.stack : String(err));
  process.exit(1);
});
}
