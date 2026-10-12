import assert from "node:assert/strict";
import test from "node:test";
import { startTaskMonitor } from "../src/background/task-monitor.js";

const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const tasks = state => [{ taskId: "old-task", items: [{ id: "old-item", state }] }];
function fixture(overrides = {}) {
  const timers = new Map(), cleared = [], closed = [], synced = [];
  const stop = startTaskMonitor({ item: { taskId: "old-task", itemId: "old-item" },
    processCommands: async () => {}, readTasks: async () => tasks("browser_running"),
    syncTasks: async value => synced.push(value), renewLease: async () => {},
    stopCapture: async error => closed.push(error.message),
    setTimer(fn, delay) { timers.set(delay, fn); return delay; },
    clearTimer(id) { cleared.push(id); }, ...overrides });
  return { stop, timers, cleared, closed, synced };
}

test("an old task response cannot close the next capture after monitor shutdown", async () => {
  const response = deferred();
  const f = fixture({ readTasks: () => response.promise });
  const pending = f.timers.get(2000)();
  await Promise.resolve();
  f.stop();
  response.resolve(tasks("completed"));
  await pending;
  assert.deepEqual(f.closed, []);
  assert.deepEqual(f.synced, []);
  assert.deepEqual(f.cleared, [2000, 20000]);
});

test("shutdown during notification sync cannot close the next capture", async () => {
  const notification = deferred();
  const f = fixture({ readTasks: async () => tasks("cancelled"), syncTasks: () => notification.promise });
  const pending = f.timers.get(2000)();
  await Promise.resolve(); await Promise.resolve();
  f.stop();
  notification.resolve();
  await pending;
  assert.deepEqual(f.closed, []);
});

test("a late lease rejection cannot close the next capture", async () => {
  const lease = deferred();
  const f = fixture({ renewLease: () => lease.promise });
  const pending = f.timers.get(20000)();
  f.stop(); lease.reject(new Error("LEASE_EXPIRED"));
  await pending;
  assert.deepEqual(f.closed, []);
});

test("a current stopped task closes capture and heartbeat cleanup rejection is contained", async () => {
  const f = fixture({ readTasks: async () => tasks("waiting_user") });
  await f.timers.get(2000)();
  assert.deepEqual(f.closed, ["TASK_CAPTURE_STOPPED"]);
  const failed = fixture({ renewLease: async () => { throw new Error("LEASE_EXPIRED"); },
    stopCapture: async () => { throw new Error("DETACH_FAILED"); } });
  await assert.doesNotReject(failed.timers.get(20000)());
  f.stop(); failed.stop();
});

test("slow task reads and lease renewals do not accumulate overlapping requests", async () => {
  const response = deferred(), lease = deferred();
  let reads = 0, renewals = 0;
  const f = fixture({ readTasks() { reads++; return response.promise; },
    renewLease() { renewals++; return lease.promise; } });
  const pending = f.timers.get(2000)(), heartbeat = f.timers.get(20000)();
  await Promise.resolve();
  await f.timers.get(2000)(); await f.timers.get(20000)();
  assert.equal(reads, 1); assert.equal(renewals, 1);
  response.resolve(tasks("uploading")); lease.resolve();
  await Promise.all([pending, heartbeat]);
  assert.deepEqual(f.closed, []);
  f.stop();
});
