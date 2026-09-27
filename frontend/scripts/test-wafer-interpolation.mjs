import assert from "node:assert/strict";
import { interpolateWaferShots } from "../src/lib/waferInterpolation.js";

const samples = [
  { x: 0, y: 0, value: 0 },
  { x: 2, y: 0, value: 2 },
  { x: 0, y: 2, value: 2 },
  { x: 2, y: 2, value: 4 },
];
const output = interpolateWaferShots([
  { x: 0, y: 0 }, { x: 1, y: 1 }, { x: 3, y: 1 },
], samples);
assert.equal(output.values.get("0,0").value, 0);
assert.equal(output.values.get("0,0").interpolated, false);
assert.equal(output.values.get("1,1").value, 2);
assert.equal(output.values.get("1,1").interpolated, true);
assert.equal(output.values.has("3,1"), false);
assert.equal(output.interpolatedCount, 1);
assert.equal(interpolateWaferShots([{ x: 1, y: 1 }], samples.slice(0, 2)).interpolatedCount, 0);
