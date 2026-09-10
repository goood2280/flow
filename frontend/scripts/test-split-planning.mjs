import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createServer } from "vite";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

const server = await createServer({
  server: { middlewareMode: true },
  appType: "custom",
  logLevel: "error",
});

try {
  const snapshot = await server.ssrLoadModule("/src/components/SplitTableSnapshotView.jsx");
  const splitTable = await server.ssrLoadModule("/src/features/splittable/My_SplitTable.jsx");
  const normalizedLookup = splitTable.buildNormalizedLookup({
    " KNOB_Recipe_A ": { id: 1 },
    knob_recipe_b: { id: 2 },
    "": { id: 3 },
  });
  assert.equal(normalizedLookup.size, 2);
  assert.equal(normalizedLookup.get("knob_recipe_a").value.id, 1);
  assert.equal(normalizedLookup.get("knob_recipe_b").value.id, 2);
  const collisionLookup = splitTable.buildNormalizedLookup({ recipe_a: "tail-first", knob_recipe_a: "full-second" });
  assert.equal(
    splitTable.firstNormalizedLookupValue(collisionLookup, ["knob_recipe_a", "recipe_a"]),
    "tail-first",
    "case-insensitive full/tail collisions retain source insertion precedence",
  );
  // Exercise the real editor callbacks without mounting the Modal portal.
  let editorValue = "SOP_RCP", commits = [], closes = 0;
  const editor = () => splitTable.SplitTableCellEditor({
    activeCell: { key: "LOT|split|KNOB_TEST", param: "KNOB_TEST", kind: "split_value", value: editorValue },
    suggestions: [{ value: "OTHER_RCP", label: "Other" }],
    onValueChange: value => { editorValue = value; },
    onCommit: value => commits.push(value),
    onClose: () => { closes++; },
  });
  const elements = node => !node || typeof node !== "object" ? [] : Array.isArray(node)
    ? node.flatMap(elements) : [node, ...elements(node.props?.children)];
  let nodes = elements(editor());
  nodes.find(node => node.type === "div" && node.props.onClick).props.onClick();
  assert.equal(editorValue, "OTHER_RCP");
  assert.deepEqual(commits, [], "selecting a suggestion must not apply it");
  nodes.find(node => node.type === "button" && node.props.children === "Cancel").props.onClick();
  assert.equal(closes, 1);
  assert.deepEqual(commits, []);
  nodes = elements(editor());
  const input = nodes.find(node => node.type === "input");
  input.props.onKeyDown({ key: "Escape" });
  input.props.onKeyDown({ key: "Enter" });
  assert.deepEqual(commits, [], "split values require Apply");
  nodes.find(node => node.type === "button" && node.props.children === "Apply").props.onClick();
  assert.deepEqual(commits, ["OTHER_RCP"]);

  const source = readFileSync(new URL("../src/features/splittable/My_SplitTable.jsx", import.meta.url), "utf8");
  const addDraftBody = source.split("  const addSplitDraft=(param)=>{")[1].split("  const assignSplitPlanValue=")[0].replace(/};\s*$/, "");
  let opened;
  new Function("splitDraftValues", "planningS0ValueForParam", "data", "hasValue", "resolveSplitDisplayName", "nextSplitDraftLabelIndex", "setSplitDraftValues", "setSplitContextMenu", "openSplitDraftEditor", "param", addDraftBody)(
    {}, () => "SOP_RCP", { rows: [] }, Boolean, value => value, splitTable.nextSplitDraftLabelIndex,
    () => assert.fail("opening an editor must not mutate drafts"), () => {}, (...args) => { opened = args; }, "KNOB_TEST",
  );
  assert.deepEqual(opened, ["KNOB_TEST", 0, "SOP_RCP", "S0"]);
  const param = "KNOB_TEST";
  const mapping = ppid => ({ [param]: { ppid } });
  const cell = actual => ({ actual, plan: "" });

  const unprogressed = {
    headers: ["W1"],
    rows: [{ _param: param, _cells: { 0: cell("OLD_RCP") } }],
    s0_by_knob: mapping("OLD_RCP"),
    s0_edit_by_knob: mapping("CURRENT_RCP"),
    step_progress: { not_reached: [param] },
  };
  assert.equal(snapshot.planningS0ValueForParam(unprogressed, param), "CURRENT_RCP");

  const progressed = { ...unprogressed, step_progress: { not_reached: [] } };
  assert.equal(snapshot.planningS0ValueForParam(progressed, param), "OLD_RCP");
  assert.equal(
    snapshot.planningS0ValueForParam({ ...progressed, rows: [{ _param: param, _cells: { 0: cell("") } }] }, param),
    "CURRENT_RCP",
  );

  const splitView = snapshot.buildSplitCheckStView({
    headers: ["W1", "W2"],
    rows: [{ _param: param, _cells: { 0: cell("SOP_CATEGORY"), 1: cell("OTHER_PHYSICAL_RCP") } }],
    s0_by_knob: mapping("SOP_PHYSICAL_RCP"),
  }, {
    preferredValueForParam: () => "SOP_PHYSICAL_RCP",
    displayForValue: raw => ({
      SOP_PHYSICAL_RCP: "SOP_CATEGORY",
      SOP_CATEGORY: "SOP_CATEGORY",
      OTHER_PHYSICAL_RCP: "SOP_CATEGORY",
    }[raw] || raw),
  });
  assert.deepEqual(
    splitView.rows.map(row => row._split_value_raw),
    ["SOP_PHYSICAL_RCP", "OTHER_PHYSICAL_RCP"],
    "stored SOP category is an alias, but a distinct physical recipe remains distinct",
  );
  assert.equal(splitView.rows[0]._cells["0"].actual, "S0");
  assert.equal(splitView.rows[1]._cells["0"].actual, "");
  assert.equal(splitView.rows[0]._cells["1"].actual, "");
  assert.equal(splitView.rows[1]._cells["1"].actual, "S1");

  // Mixed split snapshots expand only KNOB rows. Other parameter families
  // retain their source actual/plan cells for ordinary rendering.
  const mixedRows = [
    { _param: "KNOB_TEMP", _cells: { 0: cell("K0"), 1: cell("K1") } },
    { _param: "INLINE_ITEM", _cells: { 0: { actual: "1.234", plan: "2.345" }, 1: { actual: "", plan: "3.456" } } },
    { _param: "VM_ITEM", _cells: { 0: { actual: "VM_ACT", plan: "VM_PLAN" }, 1: { actual: "", plan: "" } } },
    { _param: "MASK_ITEM", _cells: { 0: { actual: "AAAA_PC_B", plan: "PC_B" }, 1: { actual: "PC_B", plan: "PC_B" } } },
    { _param: "FAB_ITEM", _cells: { 0: { actual: "FAB_ACT", plan: "FAB_PLAN" }, 1: { actual: "", plan: "" } } },
    { _param: "TAG_PURPOSE", _cells: { 0: { actual: "TAG_ACT", plan: "TAG_PLAN" }, 1: { actual: "", plan: "" } } },
  ];
  mixedRows.push({ _param: "TAG_NOTE", _cells: { 0: cell("same"), 1: cell("same") } });
  const mixedSource = { headers: ["W1", "W2"], rows: mixedRows };
  const mixedSplit = snapshot.buildSplitCheckStView(mixedSource);
  assert.deepEqual(mixedSplit.rows.filter(row => row._param !== "KNOB_TEMP").map(row => [row._param, row._cells]), [
    ["INLINE_ITEM", mixedRows[1]._cells],
    ["VM_ITEM", mixedRows[2]._cells],
    ["MASK_ITEM", mixedRows[3]._cells],
    ["FAB_ITEM", mixedRows[4]._cells],
    ["TAG_NOTE", mixedRows[6]._cells],
  ], "non-KNOB actual/plan cells stay unchanged in split view");
  assert.equal(mixedSplit.rows.filter(row => row._param === "KNOB_TEMP").length, 2);
  assert.deepEqual(mixedSplit.rows.filter(row => row._param === "KNOB_TEMP").map(row => row._cells), [
    { 0: { actual: "S0", plan: "", split_check: true, not_reached: false }, 1: { actual: "", plan: "", split_check: true, not_reached: false } },
    { 0: { actual: "", plan: "", split_check: true, not_reached: false }, 1: { actual: "S1", plan: "", split_check: true, not_reached: false } },
  ]);
  assert.deepEqual(mixedSplit.purpose_row._cells, mixedRows[5]._cells, "TAG purpose cells remain source values");
  assert.equal(snapshot.formatSplitCellValue("AAAA_PC_B", "MASK_ITEM"), "PC_B");
  assert.equal(snapshot.formatSplitCellValue("PC_B", "MASK_ITEM"), "PC_B");

  const mixedMarkup = renderToStaticMarkup(React.createElement(snapshot.default, {
    stView: { ...mixedSplit, prefix_columns: ["항목", "값", "Split"], precision: { INLINE: 2 } },
    showTitle: false,
    showMeta: false,
  }));
  assert.ok(mixedMarkup.includes("1.23"), "ordinary INLINE actual values use precision formatting");
  assert.match(mixedMarkup, /≠2\.35/, "ordinary INLINE plan mismatch remains visible");
  assert.match(mixedMarkup, /✗ VM_ACT[\s\S]*≠VM_PLAN/, "ordinary VM actual/plan cells stay ordinary values");

  // PEMS always exposes physical wafers 1..25, regardless of source order or
  // the source wafer labels. Missing wafers remain represented as S0.
  const pemsRows = [
    { _param: "KNOB_TEMP", _cells: { 0: cell("K3"), 1: cell("K1"), 2: cell("K25") } },
    { _param: "INLINE_ITEM", _cells: { 0: { actual: "I3", plan: "IP3" }, 1: { actual: "I1", plan: "IP1" }, 2: { actual: "I25", plan: "IP25" } } },
    { _param: "TAG_PURPOSE", _cells: { 0: { actual: "T3", plan: "TP3" }, 1: { actual: "T1", plan: "TP1" }, 2: { actual: "T25", plan: "TP25" } } },
  ];
  const pems = snapshot.buildPemsStView({
    headers: ["#03", "W01", "25"], wafer_keys: ["#03", "W01", "25"], rows: pemsRows,
  });
  assert.deepEqual(pems.headers, Array.from({ length: 25 }, (_, i) => String(i + 1)));
  const pemsInline = pems.rows.find(row => row._param === "INLINE_ITEM");
  assert.deepEqual(pemsInline._cells["0"], pemsRows[1]._cells[1], "PEMS wafer 1 remaps from W01");
  assert.deepEqual(pemsInline._cells["2"], pemsRows[1]._cells[0], "PEMS wafer 3 remaps from #03");
  assert.deepEqual(pemsInline._cells["24"], pemsRows[1]._cells[2], "PEMS wafer 25 remaps from source wafer 25");
  assert.deepEqual(pems.purpose_row._cells["0"], pemsRows[2]._cells[1], "PEMS purpose row follows wafer remap");
  const pemsKnobs = pems.rows.filter(row => row._param === "KNOB_TEMP");
  assert.equal(pemsKnobs.length, 3);
  assert.equal(pemsKnobs[0]._cells["0"].actual, "S0");
  assert.equal(pemsKnobs[1]._cells["2"].actual, "S1");
  assert.equal(pemsKnobs[2]._cells["24"].actual, "S2");
  assert.equal(pemsKnobs[0]._cells["1"].actual, "S0", "missing wafer remains in S0");

  assert.equal(splitTable.nextSplitDraftLabelIndex("SOP_RCP", [], ["SOP_RCP"]), 1);
  assert.equal(splitTable.nextSplitDraftLabelIndex("SOP_RCP", ["SOP_RCP"], ["SOP_RCP"]), 1);
  assert.equal(splitTable.nextSplitDraftLabelIndex("SOP_RCP", ["SOP_CATEGORY"], [], "SOP_CATEGORY"), 1);
  assert.equal(splitTable.nextSplitDraftLabelIndex("SOP_RCP", ["OTHER_PHYSICAL_RCP"], [], "SOP_CATEGORY"), 2);
  assert.equal(splitTable.nextSplitDraftLabelIndex("", [], [""]), 1);
  assert.equal(splitTable.nextSplitDraftLabelIndex("SOP_RCP", ["S1_RCP"], ["S1_RCP"]), 2);

  console.log("split planning runtime checks passed");
} finally {
  await server.close();
}
