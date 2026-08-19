import fs from "node:fs";
import path from "node:path";
import process from "node:process";

const root = process.cwd();
const src = path.join(root, "src");
const manifestPath = path.join(src, "app", "pageManifest.jsx");
const manifestSource = fs.readFileSync(manifestPath, "utf8");
const errors = [];

function readPageImplementation(pageName) {
  const pagePath = path.join(src, "pages", `${pageName}.jsx`);
  const entrySource = fs.readFileSync(pagePath, "utf8");
  const featureTarget = entrySource.match(/export \{ default \} from "(\.\.\/features\/[^"]+)";/)?.[1];
  if (!featureTarget) return entrySource;
  return fs.readFileSync(path.resolve(path.dirname(pagePath), `${featureTarget}.jsx`), "utf8");
}

const legacyPageKeys = new Set([
  "home", "filebrowser", "dashboard", "splittable", "ramcache", "matchfill",
  "chartbuilder", "templatereport", "autoreport", "lotrequest", "inform",
  "meeting", "calendar", "tracker", "valve", "teg", "yieldmap", "ettime",
  "reformatize", "dcop", "diagnosis", "admin", "devguide", "tablemap", "knowledge",
]);

const entryLines = manifestSource.split(/\r?\n/).filter((line) => /\{\s*key:\s*"/.test(line));
const entries = entryLines.map((line) => {
  const key = line.match(/key:\s*"([^"]+)"/)?.[1];
  const page = line.match(/import\("\.\.\/pages\/([^"]+)"\)/)?.[1];
  return { key, page, line };
});

for (const entry of entries) {
  for (const field of ["label", "group", "layout", "helpId", "defaultEnabled", "load"]) {
    if (!new RegExp(`\\b${field}:`).test(entry.line)) {
      errors.push(`manifest '${entry.key}' is missing ${field}`);
    }
  }
  if (!legacyPageKeys.has(entry.key) && !/\bdesignSystem:\s*true\b/.test(entry.line)) {
    errors.push(`new page '${entry.key}' must opt in with designSystem: true`);
  }
}

const registeredPages = new Set(entries.map((entry) => `${entry.page}.jsx`));
const pageFiles = fs.readdirSync(path.join(src, "pages"))
  .filter((name) => /^My_.+\.jsx$/.test(name) && name !== "My_Login.jsx");
for (const file of pageFiles) {
  if (!registeredPages.has(file)) errors.push(`${file} is not registered in pageManifest.jsx`);
}

for (const entry of entries.filter((item) => /\bdesignSystem:\s*true\b/.test(item.line))) {
  const pageSource = readPageImplementation(entry.page);
  if (!pageSource.includes("components/ui")) errors.push(`${entry.page}.jsx must import from components/ui`);
  if (!pageSource.includes("<PageShell")) errors.push(`${entry.page}.jsx must render PageShell`);
}

for (const relative of [
  "styles/components.css",
  "styles/layouts.css",
  "styles/utilities.css",
]) {
  const source = fs.readFileSync(path.join(src, relative), "utf8");
  if (/#[0-9a-f]{3,8}\b/i.test(source) || /\brgba?\(/i.test(source)) {
    errors.push(`${relative} contains a raw color; add it to styles/tokens.css`);
  }
  if (/!important/.test(source)) errors.push(`${relative} contains !important`);
  if (/\[style\*=/.test(source)) errors.push(`${relative} contains a style-attribute selector`);
}

const uiDir = path.join(src, "components", "ui");
for (const file of fs.readdirSync(uiDir).filter((name) => /\.(js|jsx)$/.test(name))) {
  const source = fs.readFileSync(path.join(uiDir, file), "utf8");
  if (/#[0-9a-f]{3,8}\b/i.test(source) || /\brgba?\(/i.test(source)) {
    errors.push(`components/ui/${file} contains a raw color`);
  }
}

const mainSource = fs.readFileSync(path.join(src, "main.jsx"), "utf8");
if (mainSource.includes("carbon.css")) errors.push("main.jsx must not load the legacy Carbon override layer");

const authSource = fs.readFileSync(path.join(root, "..", "backend", "core", "auth.py"), "utf8");
const backendBlock = authSource.match(/TAB_SUBTABS\s*=\s*\{([\s\S]*?)\n\}/)?.[1] || "";
const backendSubtabs = new Map();
for (const match of backendBlock.matchAll(/"([^"]+)"\s*:\s*\(([^)]*)\)/g)) {
  backendSubtabs.set(match[1], [...match[2].matchAll(/"([^"]+)"/g)].map((item) => item[1]));
}
for (const match of manifestSource.matchAll(/key:\s*"([^"]+)"[^\n]*subtabs:\s*\[([^\]]+)\]/g)) {
  const frontendKeys = [...match[2].matchAll(/key:\s*"([^"]+)"/g)].map((item) => item[1]).sort();
  const backendKeys = [...(backendSubtabs.get(match[1]) || [])].sort();
  if (frontendKeys.join(",") !== backendKeys.join(",")) {
    errors.push(`subtab contract mismatch for '${match[1]}': frontend=${frontendKeys} backend=${backendKeys}`);
  }
  backendSubtabs.delete(match[1]);
}
for (const key of backendSubtabs.keys()) errors.push(`backend subtab '${key}' is missing from pageManifest.jsx`);

if (errors.length) {
  console.error("Design system checks failed:\n- " + errors.join("\n- "));
  process.exit(1);
}

console.log(`Design system checks passed (${entries.length} page definitions).`);
