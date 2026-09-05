import assert from "node:assert/strict";
import { createServer } from "vite";

const server = await createServer({
  server: { middlewareMode: true },
  appType: "custom",
  logLevel: "error",
});

try {
  const snapshot = await server.ssrLoadModule("/src/components/SplitTableSnapshotView.jsx");
  const splitTable = await server.ssrLoadModule("/src/features/splittable/My_SplitTable.jsx");
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
  assert.equal(splitView.rows[0]._cells["0"].actual, "✓");
  assert.equal(splitView.rows[1]._cells["0"].actual, "");
  assert.equal(splitView.rows[0]._cells["1"].actual, "");
  assert.equal(splitView.rows[1]._cells["1"].actual, "✓");

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
