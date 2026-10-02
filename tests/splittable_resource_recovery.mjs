import assert from "node:assert/strict";
import test from "node:test";
import { fetchSplitTableResource, splitTableResourceError } from "../frontend/src/features/splittable/splitTableResource.js";

for (const status of [429, 500, 502, 503, 504]) {
  test(`HTTP ${status} recovers without reloading the page`, async () => {
    let calls = 0;
    const items = { candidates: ["R0001"] };
    const result = await fetchSplitTableResource("/api/test", { retryDelayMs: 0 }, async () => {
      if (++calls === 1) throw Object.assign(new Error("temporarily unavailable"), { status });
      return items;
    });
    assert.equal(calls, 2);
    assert.equal(result, items);
  });
}

test("a network interruption recovers", async () => {
  let calls = 0;
  await fetchSplitTableResource("/api/test", { retryDelayMs: 0 }, async () => {
    if (++calls === 1) throw new TypeError("Failed to fetch");
    return {};
  });
  assert.equal(calls, 2);
});

test("persistent server errors stop after three attempts and retain the cause", async () => {
  let calls = 0;
  const error = Object.assign(new Error("cache build failed"), { status: 500 });
  await assert.rejects(fetchSplitTableResource("/api/test", { retryDelayMs: 0 }, async () => {
    calls += 1;
    throw error;
  }), actual => actual === error);
  assert.equal(calls, 3);
  assert.equal(splitTableResourceError(error), "HTTP 500: cache build failed");
});

for (const status of [401, 403, 404, 422]) {
  test(`HTTP ${status} is not retried`, async () => {
    let calls = 0;
    const error = Object.assign(new Error(`HTTP ${status}`), { status });
    await assert.rejects(fetchSplitTableResource("/api/test", { retryDelayMs: 0 }, async () => {
      calls += 1;
      throw error;
    }), actual => actual === error);
    assert.equal(calls, 1);
    assert.equal(splitTableResourceError(error), `HTTP ${status}`);
  });
}

test("changing products cancels a pending retry", async () => {
  const controller = new AbortController();
  let calls = 0;
  const pending = fetchSplitTableResource("/api/test", { signal: controller.signal }, async () => {
    calls += 1;
    throw Object.assign(new Error("busy"), { status: 429 });
  });
  await Promise.resolve(); // Let the failure schedule its retry.
  controller.abort();
  await assert.rejects(pending, { name: "AbortError" });
  assert.equal(calls, 1);
});

test("a late response from the previous product is discarded", async () => {
  const controller = new AbortController();
  let finish;
  const pending = fetchSplitTableResource("/api/test", { signal: controller.signal }, () => new Promise(resolve => { finish = resolve; }));
  controller.abort();
  finish({ candidates: ["OLD_PRODUCT_ROOT"] });
  await assert.rejects(pending, { name: "AbortError" });
});

test("invalid JSON is reported without repeated requests", async () => {
  let calls = 0;
  await assert.rejects(fetchSplitTableResource("/api/test", { retryDelayMs: 0 }, async () => {
    calls += 1;
    throw new SyntaxError("Invalid JSON");
  }), { name: "SyntaxError" });
  assert.equal(calls, 1);
});
