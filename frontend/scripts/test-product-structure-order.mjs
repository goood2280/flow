import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { Module } from "node:module";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { build } from "esbuild";

const frontendDir = dirname(dirname(fileURLToPath(import.meta.url)));
const sourceFile = join(frontendDir, "src/features/productwiki/ProductStructure.jsx");
const source = await readFile(sourceFile, "utf8");
const bundle = await build({
  stdin: {
    contents: `${source}\nmodule.exports.naturalStepCompare = naturalStepCompare; module.exports.sortedChildren = sortedChildren;`,
    resolveDir: dirname(sourceFile),
    loader: "jsx",
  },
  bundle: true,
  format: "cjs",
  platform: "node",
  packages: "external",
  loader: { ".css": "empty" },
  write: false,
  logLevel: "silent",
});
const testModule = new Module(join(frontendDir, "scripts/.product-structure-order-test.cjs"));
testModule.filename = join(frontendDir, "scripts/.product-structure-order-test.cjs");
testModule.paths = Module._nodeModulePaths(frontendDir);
testModule._compile(bundle.outputFiles[0].text, testModule.filename);
const { naturalStepCompare, sortedChildren } = testModule.exports;

const step = (stepId) => ({ type: "step", stepId, children: new Map() });
const branch = (label, stepIds) => ({ type: "path", label, children: new Map(
  stepIds.map((stepId, index) => [`${stepId}-${index}`, step(stepId)]),
) });

test("natural step order keeps source IDs and compares numeric chunks", () => {
  const ids = ["AA10", "AA1200", "AA001200", "AA2", "AA1201"];
  assert.deepEqual([...ids].sort(naturalStepCompare), ["AA2", "AA10", "AA001200", "AA1200", "AA1201"]);
  assert.deepEqual(ids, ["AA10", "AA1200", "AA001200", "AA2", "AA1201"]);
});

test("structure stages use the minimum descendant step_id", () => {
  const late = branch("late", ["AA20", "AA10"]);
  const early = branch("early", ["AA2"]);
  const parent = { children: new Map([["late", late], ["early", early]]) };
  assert.deepEqual(sortedChildren(parent).map((item) => item.label), ["early", "late"]);
  assert.deepEqual(sortedChildren(late).map((item) => item.stepId), ["AA10", "AA20"]);
});
