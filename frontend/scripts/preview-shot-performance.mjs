import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const frontendDir = dirname(dirname(fileURLToPath(import.meta.url)));
const repoDir = dirname(frontendDir);
const tegFile = join(frontendDir, "src/features/teg/TegCheck.jsx");
const outputDir = join(repoDir, "outputs/standalone-performance-20260920");
const outputJs = join(outputDir, "shot-preview.js");
const outputHtml = join(outputDir, "index.html");

const tegSource = await readFile(tegFile, "utf8");
const previewEntry = `
const PREVIEW_COLUMNS = 25;
const PREVIEW_ROWS = 20;
const PREVIEW_DUPLICATES = 10;

function previewStatusFor(position, duplicate) {
  if (duplicate !== PREVIEW_DUPLICATES - 1) return { light: "green", light_reason: "" };
  if (position % 17 === 0) return { light: "red", light_reason: "좌표 불일치 · die 침범" };
  if (position % 13 === 0) return { light: "orange", light_reason: "MAIN 정보없음" };
  if (position % 11 === 0) return { light: "yellow", light_reason: "die 경계 근처" };
  return { light: "green", light_reason: "" };
}

function makePreviewRows() {
  const positions = 1000;
  const duplicates = 10;
  return Array.from({ length: positions * duplicates }, (_, index) => {
    const position = index % positions;
    const duplicate = Math.floor(index / positions);
    const local = position % 500;
    const column = local % PREVIEW_COLUMNS;
    const row = Math.floor(local / PREVIEW_COLUMNS);
    const group = position < 500 ? "MAIN01" : "MAIN02";
    const left = group === "MAIN01";
    const rotated = position % 3 !== 0;
    const status = (position === 999 && duplicate === 9)
      ? { light: "red", light_reason: "MAIN02 밖" }
      : previewStatusFor(position, duplicate);
    return {
      key: \`raw-\${index}\`,
      kind: "main", group,
      name: \`\${group}_TEG_\${String(local + 1).padStart(3, "0")}_\${String(duplicate + 1).padStart(2, "0")}\`,
      mm_x: (position === 999 && duplicate === 9) ? 16 : (left ? -12 : 2) + column * 0.45,
      mm_y: -15.2 + row * 1.6,
      w: rotated ? 0.08 : 0.2,
      h: rotated ? 0.2 : 0.08,
      flat_used: rotated ? (position % 2 ? "v_R" : "v_L") : "h",
      ...status,
    };
  });
}

const previewRawItems = makePreviewRows();
const previewItems = consolidateShotItemsPure(previewRawItems, "preview");
const previewStatusCounts = previewItems.reduce((counts, item) => {
  counts[item.light] = (counts[item.light] || 0) + 1;
  return counts;
}, {});
const previewShot = {
  available: true,
  shot_w_mm: 26,
  shot_h_mm: 33,
  cells: [{ x: -12, y: -16, w: 12, h: 31 }, { x: 2, y: -16, w: 12, h: 31 }],
  main_cells: [
    { name: "MAIN01", x: -12, y: -16, w: 12, h: 31 },
    { name: "MAIN02", x: 2, y: -16, w: 12, h: 31 },
  ],
  cell_source: "preview",
};

const statusMeta = [
  ["red", "Error", "#dc2626"],
  ["orange", "Missing info", "#f97316"],
  ["yellow", "Review", "#d99a1a"],
  ["green", "Normal", "#2f9e63"],
];

function ShotPerformancePreview() {
  return (
    <main className="preview-shell">
      <header>
        <p className="eyebrow">TEG Mapfile · standalone browser fixture</p>
        <h1>Shot rendering performance preview</h1>
        <p className="intro">
          Real ShotExplorer with deterministic local data. Wheel to zoom, drag to pan, and choose a MAIN for its owned detail view.
        </p>
        <div className="metrics" aria-label="Preview dataset metrics">
          <span><strong>{previewRawItems.length.toLocaleString()}</strong> raw rows</span>
          <span><strong>{previewItems.length.toLocaleString()}</strong> unique positions</span>
          <span><strong>{(previewRawItems.length / previewItems.length).toFixed(1)}</strong> rows per position (average)</span>
          <span><strong>26 × 33 mm</strong> shot</span>
        </div>
        <div className="statuses" aria-label="Worst status per unique position">
          {statusMeta.map(([key, label, color]) => (
            <span className="status-chip" key={key} style={{ "--status-color": color }}>
              <i />{label} <strong>{previewStatusCounts[key] || 0}</strong>
            </span>
          ))}
        </div>
        <div className="diagnostic">
          <span>Initial render, double requestAnimationFrame:</span>
          <strong id="render-timing">measuring…</strong>
          <small>local diagnostic only</small>
        </div>
      </header>
      <section className="canvas-card" aria-label="Interactive TEG shot preview">
        <ShotExplorer shot={previewShot} items={previewItems} rawItems={previewRawItems} size={560} />
      </section>
  <p className="footnote">
        10,000 raw rows collapse to 1,001 exact positions, including an outside error, owned by two named MAINs. Duplicate source rows select the worst status; MAIN detail is rendered only after selection.
      </p>
    </main>
  );
}

const previewRoot = document.getElementById("root");
const previewStartedAt = performance.now();
createRoot(previewRoot).render(<ShotPerformancePreview />);
requestAnimationFrame(() => requestAnimationFrame(() => {
  const timing = document.getElementById("render-timing");
  if (timing) timing.textContent = \`\${(performance.now() - previewStartedAt).toFixed(1)} ms\`;
}));
`;

const html = `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>TEG Shot performance preview</title>
  <style>
    :root {
      color-scheme: light;
      --bg-primary: #ffffff;
      --bg-secondary: #f4f6f8;
      --text: #172033;
      --muted: #687386;
      --line: #d8dee8;
      --accent: #2563eb;
      --ok: #2f9e63;
      --warn: #d99a1a;
      --danger: #dc2626;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #edf1f6;
      color: var(--text);
    }
    * { box-sizing: border-box; }
    body { margin: 0; min-width: 820px; background: #edf1f6; }
    button, input { font: inherit; }
    .preview-shell { width: min(1100px, calc(100% - 32px)); margin: 28px auto 44px; }
    header, .canvas-card {
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 14px;
      box-shadow: 0 8px 30px rgba(30, 42, 62, 0.08);
    }
    header { padding: 22px 24px 18px; margin-bottom: 16px; }
    h1 { margin: 3px 0 7px; font-size: 25px; letter-spacing: -0.02em; }
    .eyebrow { margin: 0; color: var(--accent); font-size: 11px; font-weight: 800; letter-spacing: 0.08em; text-transform: uppercase; }
    .intro { margin: 0; color: var(--muted); font-size: 13px; line-height: 1.55; }
    .metrics, .statuses { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 15px; }
    .metrics span, .status-chip { border: 1px solid var(--line); border-radius: 999px; padding: 5px 9px; font-size: 12px; background: #f8fafc; }
    .metrics strong { color: #0f172a; }
    .status-chip { display: inline-flex; align-items: center; gap: 5px; }
    .status-chip i { width: 8px; height: 8px; border-radius: 50%; background: var(--status-color); }
    .diagnostic { display: flex; gap: 7px; align-items: baseline; margin-top: 14px; color: var(--muted); font-size: 12px; }
    .diagnostic strong { color: var(--text); font-variant-numeric: tabular-nums; }
    .diagnostic small { margin-left: auto; color: #8a94a5; }
    .canvas-card { padding: 20px; display: flex; justify-content: center; overflow: hidden; }
    .canvas-card svg { display: block; background: #fbfcfe; border-radius: 8px; }
    .footnote { margin: 10px 7px 0; color: var(--muted); font-size: 11px; line-height: 1.5; }
  </style>
</head>
<body>
  <div id="root"></div>
  <script src="./shot-preview.js"></script>
</body>
</html>
`;

await mkdir(outputDir, { recursive: true });
await build({
  stdin: {
    contents: `
import { createRoot } from "react-dom/client";
import { ShotExplorer } from "teg-shot-preview:component";
import { consolidateShotItems as consolidateShotItemsPure } from "./src/features/teg/shotItems.mjs";
${previewEntry}`,
    resolveDir: frontendDir,
    sourcefile: "shot-performance-preview.jsx",
    loader: "jsx",
  },
  outfile: outputJs,
  bundle: true,
  format: "iife",
  platform: "browser",
  target: ["chrome100", "edge100", "firefox100"],
  jsx: "automatic",
  minify: false,
  sourcemap: false,
  legalComments: "none",
  define: { "process.env.NODE_ENV": '"production"' },
  plugins: [{
    name: "real-shot-view",
    setup(context) {
      context.onResolve({ filter: /^teg-shot-preview:component$/ }, () => ({
        path: "component",
        namespace: "teg-shot-preview",
      }));
      context.onLoad({ filter: /.*/, namespace: "teg-shot-preview" }, () => ({
        contents: `${tegSource}\nexport { ShotExplorer };`,
        resolveDir: dirname(tegFile),
        loader: "jsx",
      }));
    },
  }],
  logLevel: "info",
});
await writeFile(outputHtml, html, "utf8");

console.log(`Wrote ${outputHtml}`);
console.log(`Wrote ${outputJs}`);
