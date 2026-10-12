import assert from "node:assert/strict";
import { test } from "node:test";
import { createTaskNotifier } from "../src/background/task-notifications.js";

function fixture({ permission = "granted", fail = false } = {}) {
  const state = {}, notifications = [], cleared = [], opened = [], badges = [];
  let time = 1000000, writes = 0;
  const chrome = {
    storage: { local: {
      async get(keys) { const names = Array.isArray(keys) ? keys : [keys]; return Object.fromEntries(names.map(key => [key, structuredClone(state[key])])); },
      async set(values) { Object.assign(state, structuredClone(values)); writes++; },
    } },
    runtime: { getURL: path => "chrome-extension://fixture/" + path },
    notifications: {
      async getPermissionLevel() { return permission; },
      async create(id, options) { if (fail) throw new Error("native notification unavailable"); notifications.push({ id, options }); },
      async clear(id) { cleared.push(id); return true; },
    },
    action: {
      async setBadgeText(value) { badges.push(value.text); },
      async setBadgeBackgroundColor() {}, async setTitle() {},
    },
  };
  const make = () => createTaskNotifier(chrome, { now: () => time, openTask: async target => opened.push(target) });
  return { state, notifications, cleared, opened, badges, make, setTime: value => { time = value; }, writes: () => writes };
}
function task(values = {}) {
  return { taskId: "task-1", taskName: "工科文献实测", createdAt: 1000, status: "running", paused: false,
    attention: null, progress: { total: 20 }, ...values };
}
const handoff = { itemId: "item-1", title: "Actual engineering paper", reason: "publisher_verification", tabId: 42 };

test("background receipt notifies without a side-panel call and persists across restarts", async () => {
  const f = fixture();
  await f.make().sync([task()]);
  assert.equal(f.notifications.length, 1);
  assert.match(f.notifications[0].options.title, /已接收/);
  assert.match(f.notifications[0].options.message, /20 篇/);
  assert.equal(f.badges.at(-1), "1");
  await f.make().sync([task()]);
  assert.equal(f.notifications.length, 1);
});

test("historical completed tasks do not produce upgrade notifications", async () => {
  const f = fixture();
  await f.make().sync([task({ status: "completed", createdAt: 1 })]);
  assert.equal(f.notifications.length, 0);
  assert.equal(f.badges.at(-1), "");
});

test("a current human handoff alerts immediately and repeats only after five minutes", async () => {
  const f = fixture(), notifier = f.make();
  const waiting = task({ status: "waiting_user", attention: handoff });
  await notifier.sync([waiting]);
  assert.equal(f.notifications.length, 1); // Combined receipt + hand-off.
  assert.equal(f.notifications[0].options.requireInteraction, true);
  await notifier.sync([waiting]);
  f.setTime(1299999);
  await notifier.sync([waiting]);
  assert.equal(f.notifications.length, 1);
  f.setTime(1300000);
  await notifier.sync([waiting]);
  assert.equal(f.notifications.length, 2);
  await f.make().sync([waiting]);
  assert.equal(f.notifications.length, 2);
});

test("changed current article and verification reason notify without approving identity", async () => {
  const f = fixture(), notifier = f.make();
  await notifier.sync([task({ attention: handoff, status: "waiting_user" })]);
  await notifier.sync([task({ attention: { ...handoff, reason: "identity_mismatch" }, status: "waiting_user" })]);
  assert.equal(f.notifications.length, 2);
  assert.match(f.notifications.at(-1).options.message, /重新获取/);
  assert.equal(f.state.taskNotificationState.targets["litscout-attention-task-1"].itemId, "item-1");
});

test("manual pause suppresses repeat alerts and resume restores the same handoff", async () => {
  const f = fixture(), notifier = f.make();
  const waiting = task({ attention: handoff, status: "waiting_user" });
  await notifier.sync([waiting]);
  f.setTime(1400000);
  await notifier.sync([{ ...waiting, paused: true, status: "paused" }]);
  assert.equal(f.notifications.length, 1);
  assert(f.cleared.includes("litscout-attention-task-1"));
  await notifier.sync([waiting]);
  assert.equal(f.notifications.length, 2);
});

test("resolved attention clears its notification and badge", async () => {
  const f = fixture(), notifier = f.make();
  await notifier.sync([task({ attention: handoff, status: "waiting_user" })]);
  await notifier.sync([task({ status: "completed" })]);
  assert(f.cleared.includes("litscout-attention-task-1"));
  assert.equal(f.badges.at(-1), "");
  assert.equal(await notifier.open("litscout-attention-task-1"), false);
});

test("permission denial preserves toolbar attention and actionable status", async () => {
  const f = fixture({ permission: "denied" }), notifier = f.make();
  const result = await notifier.sync([task({ attention: handoff, status: "waiting_user" })]);
  assert.equal(f.notifications.length, 0);
  assert.equal(f.badges.at(-1), "1");
  assert.equal(result.permission, "denied");
  await assert.rejects(notifier.test(), /未授权/);
});

test("disabled alerts preserve task processing and re-enable alerts for the current handoff", async () => {
  const f = fixture(), notifier = f.make();
  const waiting = task({ attention: handoff, status: "waiting_user" });
  await notifier.configure(false);
  await notifier.sync([waiting]);
  assert.equal(f.notifications.length, 0);
  assert.equal(f.badges.at(-1), "1");
  await notifier.configure(true);
  await notifier.sync([waiting]);
  assert.equal(f.notifications.length, 1);
});

test("native notification failures do not break task observation", async () => {
  const f = fixture({ fail: true });
  const result = await f.make().sync([task({ attention: handoff, status: "waiting_user" })]);
  assert.match(result.error, /创建失败/);
  assert.equal(f.badges.at(-1), "1");
});

test("notification clicks recover the exact task and current article after a restart", async () => {
  const f = fixture();
  await f.make().sync([task({ attention: handoff, status: "waiting_user" })]);
  assert.equal(await f.make().open("litscout-attention-task-1"), true);
  assert.deepEqual(f.opened, [{ taskId: "task-1", itemId: "item-1", tabId: 42 }]);
  assert.equal(await f.make().open("unknown-id"), false);
});

test("concurrent snapshots do not duplicate receipt notifications or stable storage writes", async () => {
  const f = fixture(), notifier = f.make();
  await Promise.all([notifier.sync([task()]), notifier.sync([task()]), notifier.sync([task()])]);
  assert.equal(f.notifications.length, 1);
  assert.equal(f.writes(), 1);
});

test("only the queue head gets repeated human alerts; later tasks get a receipt", async () => {
  const f = fixture();
  await f.make().sync([task({ taskId: "later", createdAt: 1000, attention: handoff, status: "waiting_user" }),
    task({ taskId: "head", createdAt: 999, attention: { ...handoff, itemId: "head-paper" }, status: "waiting_user" })]);
  assert.equal(f.notifications.filter(value => value.id.startsWith("litscout-attention-")).length, 1);
  assert.equal(f.notifications[0].id, "litscout-attention-head");
});
