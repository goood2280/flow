import assert from "node:assert/strict";
import { createPollingLoop } from "../src/hooks/pollingLoop.mjs";

const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
};

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

const createFakeTimers = () => {
  const pending = new Map();
  let nextId = 1;
  return {
    setTimer(fn) {
      const id = nextId++;
      pending.set(id, fn);
      return id;
    },
    clearTimer(id) {
      pending.delete(id);
    },
    fireNext() {
      const entry = pending.entries().next().value;
      assert.ok(entry, "expected a scheduled poll");
      pending.delete(entry[0]);
      entry[1]();
    },
    get size() {
      return pending.size;
    },
  };
};

{
  const timers = createFakeTimers();
  const loop = createPollingLoop(timers);
  const requests = [];
  loop.start(() => {
    const request = deferred();
    requests.push(request);
    return request.promise;
  });
  await flush();

  assert.equal(requests.length, 1, "the first fetch starts immediately");
  assert.equal(timers.size, 0, "no timer is armed while a fetch is pending");
  requests[0].resolve("first");
  await flush();
  assert.equal(timers.size, 1, "the next poll is scheduled only after completion");
  timers.fireNext();
  await flush();
  assert.equal(requests.length, 2, "the scheduled poll starts after the prior one settled");
  loop.stop();
}

{
  const timers = createFakeTimers();
  const loop = createPollingLoop(timers);
  const oldRequest = deferred();
  const newRequest = deferred();
  const shown = [];
  let oldErrors = 0;

  loop.start(() => oldRequest.promise, {
    onData: value => shown.push(value),
    onError: () => oldErrors++,
  });
  await flush();
  loop.start(() => newRequest.promise, { onData: value => shown.push(value) });
  await flush();
  oldRequest.resolve("stale");
  await flush();
  assert.deepEqual(shown, [], "a completion from before restart is discarded");
  assert.equal(oldErrors, 0);
  newRequest.resolve("current");
  await flush();
  assert.deepEqual(shown, ["current"]);

  const stoppedRequest = deferred();
  loop.start(() => stoppedRequest.promise, {
    onData: value => shown.push(value),
    onError: () => oldErrors++,
  });
  await flush();
  loop.stop();
  stoppedRequest.reject(new Error("late failure"));
  await flush();
  assert.deepEqual(shown, ["current"], "stopped runs cannot publish data");
  assert.equal(oldErrors, 0, "stopped runs cannot publish errors");
  assert.equal(loop.isRunning(), false);
  assert.equal(timers.size, 0);
}

{
  const timers = createFakeTimers();
  const loop = createPollingLoop(timers);
  const events = [];
  let calls = 0;
  loop.start(() => ++calls, {
    maxTicks: 2,
    onData: value => events.push(["data", value]),
    onError: (err, reason) => events.push([reason, err]),
  });
  await flush();
  timers.fireNext();
  await flush();
  timers.fireNext();
  await flush();
  assert.equal(calls, 2, "maxTicks limits the number of fetches");
  assert.deepEqual(events, [["data", 1], ["data", 2], ["timeout", null]]);
  assert.equal(loop.isRunning(), false);
}

{
  const timers = createFakeTimers();
  const loop = createPollingLoop(timers);
  const failure = new Error("offline");
  const events = [];
  let calls = 0;
  loop.start(() => {
    calls += 1;
    if (calls === 2) return "recovered";
    throw failure;
  }, {
    maxErrors: 2,
    onData: value => events.push(["data", value]),
    onError: (err, reason) => events.push([reason, err]),
  });
  await flush();
  timers.fireNext();
  await flush();
  timers.fireNext();
  await flush();
  timers.fireNext();
  await flush();
  assert.equal(calls, 4, "a success resets the consecutive error count");
  assert.deepEqual(events, [["data", "recovered"], ["errors", failure]]);
  assert.equal(loop.isRunning(), false);
  assert.equal(timers.size, 0);
}

console.log("polling loop serialization, stale completion guards and limits: passed");
