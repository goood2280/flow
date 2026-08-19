import fs from "node:fs";
import path from "node:path";
import process from "node:process";

const root = process.cwd();
const src = path.join(root, "src");
const pagesDir = path.join(src, "pages");
const featuresDir = path.join(src, "features");
const errors = [];

const pageFiles = fs.readdirSync(pagesDir).filter((name) => name.endsWith(".jsx")).sort();
const featureTargets = new Set();

for (const pageFile of pageFiles) {
  const pagePath = path.join(pagesDir, pageFile);
  const source = fs.readFileSync(pagePath, "utf8").trim();
  const match = source.match(
    /^export \{ default \} from "(\.\.\/features\/[^"]+)";\r?\nexport \* from "\1";$/,
  );
  if (!match) {
    errors.push(`pages/${pageFile} must be a two-line feature compatibility entrypoint`);
    continue;
  }

  const targetPath = path.resolve(pagesDir, `${match[1]}.jsx`);
  const relativeTarget = path.relative(featuresDir, targetPath);
  if (relativeTarget.startsWith("..") || path.isAbsolute(relativeTarget)) {
    errors.push(`pages/${pageFile} resolves outside features/: ${match[1]}`);
  } else if (!fs.existsSync(targetPath)) {
    errors.push(`pages/${pageFile} target is missing: ${match[1]}.jsx`);
  } else {
    featureTargets.add(path.normalize(targetPath));
  }
}

function walk(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const target = path.join(dir, entry.name);
    return entry.isDirectory() ? walk(target) : [target];
  });
}

for (const featureFile of walk(featuresDir).filter((file) => /\.(js|jsx)$/.test(file))) {
  const source = fs.readFileSync(featureFile, "utf8");
  if (/from\s+["'][^"']*\/pages(?:\/|["'])/.test(source)) {
    errors.push(`${path.relative(src, featureFile)} must not import from pages/`);
  }
}

const manifestSource = fs.readFileSync(path.join(src, "app", "pageManifest.jsx"), "utf8");
for (const match of manifestSource.matchAll(/import\("\.\.\/pages\/([^"\)]+)"\)/g)) {
  const pageFile = path.join(pagesDir, `${match[1]}.jsx`);
  if (!fs.existsSync(pageFile)) errors.push(`pageManifest target is missing: pages/${match[1]}.jsx`);
}

if (errors.length) {
  console.error("Feature boundary checks failed:\n- " + errors.join("\n- "));
  process.exit(1);
}

console.log(`Feature boundary checks passed (${pageFiles.length} compatibility entries, ${featureTargets.size} feature pages).`);
