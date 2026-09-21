import assert from "node:assert/strict";
import { buildDpmlComparison } from "../src/features/tracker/dpmlComparison.js";

const actual = [
  { x: "2.0", x_label: "2.0", y: "2026-09-01 12:00:00" },
  { x: "2.0", x_label: "2.0", y: "2026-09-02 12:00:00" },
  { x: "9.0", x_label: "9.0", y: "2026-09-05 12:00:00" },
];
// Number gaps and repeated layers do not multiply days per mask layer.
const comparison = buildDpmlComparison(actual, "1.5");
assert.deepEqual(comparison.map(({ x, y }) => ({ x, y })), [
  { x: "2.0", y: "2026-09-01 12:00:00" },
  { x: "9.0", y: "2026-09-03 00:00:00" },
]);
// Extending into the reference forecast uses DPML, not reference timestamps.
assert.equal(buildDpmlComparison([...actual, { x: "10.0", y: "2026-09-20 12:00:00" }], "1.5").at(-1).y, "2026-09-04 12:00:00");
for (const value of ["", " ", "0", "-1", "NaN", "Infinity", "1e300"]) {
  assert.deepEqual(buildDpmlComparison(actual, value), []);
}
assert.deepEqual(buildDpmlComparison([], "2"), []);
assert.deepEqual(buildDpmlComparison([{ x: "1", y: "invalid" }], "2"), []);
// A daylight-saving boundary must not change FAB wall-clock times.
assert.equal(buildDpmlComparison([
  { x: "0", y: "2026-03-07 12:00:00" },
  { x: "1", y: "2026-03-09 12:00:00" },
], "1")[1].y, "2026-03-08 12:00:00");
console.log("DPML comparison checks passed.");
