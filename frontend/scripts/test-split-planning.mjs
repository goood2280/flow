import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createServer } from "vite";

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
