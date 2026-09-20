import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { Module } from "node:module";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { build } from "esbuild";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { consolidateShotItems, indexShotMains, shotFocusBounds } from "../src/features/teg/shotItems.mjs";

const frontendDir = dirname(dirname(fileURLToPath(import.meta.url)));
const tegFile = join(frontendDir, "src/features/teg/TegCheck.jsx");
const source = await readFile(tegFile, "utf8");
const bundle = await build({
  stdin: { contents: `${source}\nmodule.exports.ShotView = ShotView; module.exports.ShotExplorer = ShotExplorer;`, resolveDir: dirname(tegFile), loader: "jsx" },
  bundle: true,
  format: "cjs",
  platform: "node",
  packages: "external",
  jsx: "automatic",
  write: false,
  logLevel: "silent",
});
const testModule = new Module(join(frontendDir, "scripts/.shot-view-test.cjs"));
testModule.filename = join(frontendDir, "scripts/.shot-view-test.cjs");
testModule.paths = Module._nodeModulePaths(frontendDir);
testModule._compile(bundle.outputFiles[0].text, testModule.filename);
const { ShotView, ShotExplorer } = testModule.exports;

const row = (overrides = {}) => ({ mm_x: 1, mm_y: 2, name: "A", light: "green", w: 0.1, h: 0.2, ...overrides });

test("collapses 10,000 duplicate rows into one physical item", () => {
  const result = consolidateShotItems(Array.from({ length: 10000 }, (_, i) => row({ name: `T${i}` })));
  assert.equal(result.length, 1);
  assert.equal(result[0].count, 10000);
  assert.equal(result[0].uniqueNames.length, 10000);
  assert.match(result[0].tooltip, /외 9995개/);
});

test("merges target and MAIN rows at one coordinate", () => {
  const result = consolidateShotItems([
    row({ kind: "target", name: "S1", light: "green" }),
    row({ kind: "main", name: "MAIN01", light: "red", light_reason: "die 침범" }),
  ], "shot");
  assert.equal(result.length, 1);
  assert.equal(result[0].count, 2);
  assert.equal(result[0].light, "red");
  assert.match(result[0].tooltip, /S1/);
  assert.match(result[0].tooltip, /MAIN01/);
});

test("keeps an outside MAIN error owned by its MAIN", () => {
  const cells = [
    { name: "MAIN01", x: -2, y: -2, w: 4, h: 4 },
    { name: "MAIN02", x: 8, y: -2, w: 4, h: 4 },
  ];
  const raw = [row({ group: "MAIN01", name: "inside", mm_x: 0, mm_y: 0, light: "yellow" }),
    row({ group: "MAIN01", name: "outside", mm_x: 7, mm_y: 0, light: "red", light_reason: "MAIN01 밖" })];
  const groups = indexShotMains(raw, cells);
  const main01 = groups.find(group => group.name === "MAIN01");
  const main02 = groups.find(group => group.name === "MAIN02");
  assert.equal(main01.rawItems[1].name, "outside");
  assert.equal(main01.rawItems[1].light, "red");
  assert.equal(main02.rawItems.length, 0);
});

test("consolidates same-coordinate rows independently per MAIN ownership", () => {
  const raw = [
    row({ group: "MAIN01", name: "A", light: "red", light_reason: "MAIN01 밖" }),
    row({ group: "MAIN01", name: "A2", light: "green" }),
    row({ group: "MAIN02", name: "B", light: "green" }),
  ];
  const groups = indexShotMains(raw, [
    { name: "MAIN01", x: -2, y: -2, w: 4, h: 4 },
    { name: "MAIN02", x: -2, y: -2, w: 4, h: 4 },
  ]);
  const byName = Object.fromEntries(groups.map(group => [group.name,
    consolidateShotItems(group.rawItems, `main-${group.name}`)]));
  assert.equal(byName.MAIN01.length, 1);
  assert.equal(byName.MAIN01[0].light, "red");
  assert.equal(byName.MAIN02.length, 1);
  assert.equal(byName.MAIN02[0].light, "green");
});

test("focus bounds include named MAIN cells and an outside red item", () => {
  const cells = [
    { name: "MAIN01", x: -2, y: -2, w: 4, h: 4 },
    { name: "MAIN02", x: 8, y: -2, w: 4, h: 4 },
  ];
  const bounds = shotFocusBounds([
    row({ group: "MAIN01", mm_x: 20, mm_y: 6, w: 1, h: 1, light: "red" }),
  ], cells);
  assert.deepEqual(bounds, { centerX: 9.5, centerY: 2.5, width: 23, height: 9 });
});

test("10,000 rows consolidate to 500 positions for each MAIN group", () => {
  const make = group => Array.from({ length: 10000 }, (_, i) => {
    const position = i % 500;
    const duplicate = Math.floor(i / 500);
    return row({ group, name: `${group}_T${position}_${duplicate}`, mm_x: position % 25,
      mm_y: Math.floor(position / 25), light: duplicate === 19 ? "red" : "green" });
  });
  for (const group of ["MAIN01", "MAIN02"]) {
    const [indexed] = indexShotMains(make(group), [{ name: group, x: -1, y: -1, w: 30, h: 30 }]);
    const items = consolidateShotItems(indexed.rawItems, group);
    assert.equal(indexed.rawItems.length, 10000);
    assert.equal(items.length, 500);
    assert.ok(items.every(item => item.count === 20 && item.light === "red"));
  }
});

test("rejects null, blank, and nonfinite coordinates", () => {
  const result = consolidateShotItems([
    row({ mm_x: null }), row({ mm_y: "" }), row({ mm_x: "nope" }), row({ mm_y: Infinity }),
    row({ name: "valid" }),
  ]);
  assert.equal(result.length, 1);
  assert.equal(result[0].name, "valid");
});

test("uses representative dimensions instead of a union rectangle", () => {
  const result = consolidateShotItems([
    row({ name: "small", light: "green", w: 0.1, h: 8 }),
    row({ name: "large", light: "red", w: 9, h: 0.2 }),
  ]);
  assert.equal(result.length, 1);
  assert.equal(result[0].name, "large");
  assert.equal(result[0].w, 9);
  assert.equal(result[0].h, 0.2);
});

test("uses one complete size pair when the representative lacks dimensions", () => {
  const [item] = consolidateShotItems([
    row({ light: "red", w: 9, h: null }),
    row({ light: "green", w: 0.1, h: 0.2 }),
  ]);
  assert.deepEqual([item.w, item.h], [0.1, 0.2]);
});

test("deduplicates exact normalized coordinates without merging nearby positions", () => {
  const items = consolidateShotItems([
    row({ mm_x: -0 }), row({ mm_x: 0 }),
    row({ mm_x: -0.00001 }), row({ mm_x: 0.00001 }),
    row({ mm_x: 1.000049 }), row({ mm_x: 1.000051 }),
  ]);
  assert.deepEqual(items.map(item => item.count), [2, 1, 1, 1, 1]);
});

test("keeps fallback coordinate rotation while selecting the worst status", () => {
  const [item] = consolidateShotItems([
    row({ light: "green", flat_used: "v_R" }),
    row({ light: "red", flat_used: "", light_reason: "좌표 불일치" }),
  ]);
  assert.equal(item.light, "red");
  assert.equal(item.light_reason, "좌표 불일치");
  assert.equal(item.flat_used, "v_R");
  assert.match(item.tooltip, /좌표 불일치/);
});

test("keeps tooltip names and reasons bounded", () => {
  const result = consolidateShotItems(Array.from({ length: 20 }, (_, i) => row({
    name: `N${i}`,
    light: "red",
    light_reason: `reason-${i}`,
  })));
  const tooltip = result[0].tooltip;
  assert.match(tooltip, /외 15개/);
  assert.match(tooltip, /reason-0/);
  assert.match(tooltip, /reason-4/);
  assert.match(tooltip, /외 15개/);
  assert.doesNotMatch(tooltip, /reason-5/);
});

test("a late red error reason remains visible after five earlier warning reasons", () => {
  const [item] = consolidateShotItems([
    ...Array.from({ length: 10 }, (_, i) => row({ light: "yellow", light_reason: `warning-${i}` })),
    row({ light: "red", light_reason: "late critical error" }),
  ]);
  assert.equal(item.light, "red");
  assert.match(item.tooltip, /late critical error/);
  assert.match(item.tooltip, /외 6개/);
});

test("10,000 raw rows become 500 exact markers with worst status and all positions preserved", () => {
  const raw = Array.from({ length: 10000 }, (_, i) => {
    const position = i % 500;
    const duplicate = Math.floor(i / 500);
    return row({
      mm_x: (position % 25) / 10,
      mm_y: Math.floor(position / 25) / 10,
      name: `T${position}-${duplicate}`,
      light: duplicate === 19 ? "red" : "green",
      light_reason: duplicate === 19 ? `좌표 불일치 ${position}` : "",
      flat_used: position % 2 ? "v_R" : "v_L",
    });
  });
  const items = consolidateShotItems(raw, "shot");
  assert.equal(items.length, 500);
  assert.equal(new Set(items.map(item => `${item.mm_x},${item.mm_y}`)).size, 500);
  assert.ok(items.every(item => item.count === 20 && item.light === "red"));
  assert.ok(items.every(item => item.tooltip.includes("좌표 불일치")));
  assert.equal(items.filter(item => item.flat_used === "v_R").length, 250);
  assert.equal(items.filter(item => item.flat_used === "v_L").length, 250);
  const markup = renderToStaticMarkup(React.createElement(ShotView, {
    shot: { shot_w_mm: 100, shot_h_mm: 100, cells: [] }, items, size: 400,
  }));
  assert.equal((markup.match(/data-shot-index=/g) || []).length, 500);
  assert.equal((markup.match(/<rect/g) || []).length, 501);
  assert.equal((markup.match(/<title/g) || []).length, 0);
});

test("ShotView bounds eager labels while retaining every marker", () => {
  const items = consolidateShotItems(Array.from({ length: 500 }, (_, i) => row({
    mm_x: (i % 25) * 2,
    mm_y: Math.floor(i / 25) * 2,
    name: `L${i}`,
    w: 20,
    h: 20,
    light: i % 7 === 0 ? "red" : "green",
    flat_used: i % 2 ? "v_R" : "v_L",
  })), "shot");
  const markup = renderToStaticMarkup(React.createElement(ShotView, {
    shot: { shot_w_mm: 100, shot_h_mm: 100, cells: [] }, items, size: 400,
  }));
  assert.equal((markup.match(/data-shot-index=/g) || []).length, 500);
  assert.equal((markup.match(/data-shot-label="true"/g) || []).length, 80);
  assert.match(markup, /rotate\(-90/);
  assert.match(markup, /rotate\(90/);
});

test("ShotExplorer SSR renders one overview and leaves MAIN detail lazy", () => {
  const cells = [
    { name: "MAIN01", x: -2, y: -2, w: 4, h: 4 },
    { name: "MAIN02", x: 8, y: -2, w: 4, h: 4 },
  ];
  const rawItems = [
    row({ group: "MAIN01", name: "bad-outside", mm_x: 20, mm_y: 0, light: "red", light_reason: "MAIN01 밖" }),
    row({ group: "MAIN02", name: "ok", mm_x: 9, mm_y: 0, light: "green" }),
  ];
  const items = consolidateShotItems(rawItems, "shot");
  const markup = renderToStaticMarkup(React.createElement(ShotExplorer, {
    shot: { shot_w_mm: 30, shot_h_mm: 20, cells: [{ x: -12, y: -8, w: 24, h: 16 }], main_cells: cells }, items, rawItems, size: 300,
  }));
  assert.equal((markup.match(/data-shot-explorer=/g) || []).length, 1);
  assert.equal((markup.match(/<svg/g) || []).length, 1);
  assert.match(markup, /빨간 오류만/);
  assert.match(markup, /MAIN 확대/);
  assert.doesNotMatch(markup, /MAIN01 ·/);
});
