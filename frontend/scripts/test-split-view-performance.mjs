import assert from "node:assert/strict";
import {
  appendSplitViewPerformanceSample,
  isSplitViewPerformanceEnabled,
  summarizeSplitViewPerformance,
} from "../src/lib/splitViewPerformance.js";

assert.equal(isSplitViewPerformanceEnabled("?split_perf=1"), true);
assert.equal(isSplitViewPerformanceEnabled("?split_perf=0"), false);
assert.equal(isSplitViewPerformanceEnabled("?other=1"), false);

let samples = [];
for (let index = 1; index <= 45; index += 1) {
  samples = appendSplitViewPerformanceSample(samples, { totalMs: index * 10 }, 40);
}
assert.equal(samples.length, 40);
assert.equal(samples[0].totalMs, 60);
assert.equal(samples.at(-1).totalMs, 450);

const passing = summarizeSplitViewPerformance([
  { totalMs: 100 },
  { totalMs: 200 },
  { totalMs: 300 },
  { totalMs: 400 },
]);
assert.equal(passing.count, 4);
assert.equal(passing.p50Ms, 200);
assert.equal(passing.p95Ms, 400);
assert.equal(passing.pass, true);

const failing = summarizeSplitViewPerformance([{ totalMs: 200 }, { totalMs: 501 }]);
assert.equal(failing.pass, false);
assert.equal(summarizeSplitViewPerformance([{ totalMs: 500 }]).pass, true);

console.log("split view performance helpers: ok");
