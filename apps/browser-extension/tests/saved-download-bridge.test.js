import assert from "node:assert/strict";
import test from "node:test";
import { createSavedDownloadBridge } from "../src/background/saved-download-bridge.js";

function fixture(state = "waiting_user", failure = null) {
  const values = {}, listeners = new Set(), requests = [], stopped = [];
  let wakes = 0;
  const chromeApi = { storage: { local: {
    async get(key) { return typeof key === "string" ? { [key]: structuredClone(values[key]) } : structuredClone(values); },
    async set(data) { Object.assign(values, structuredClone(data)); },
  } }, downloads: {
    onChanged: { addListener(listener) { listeners.add(listener); } },
    async search(query) { return query.id === 7 ? [{ id: 7, state: "complete", filename: "D:/LitScoutFixture/Downloads/paper.pdf" }] : []; },
  } };
  const bridge = createSavedDownloadBridge(chromeApi, {
    wake() { wakes++; }, async stopCapture(id) { stopped.push(id); },
    async api(path, options) {
      if (path === "/v1/browser/tasks") return { async json() { return [{ taskId: "task", items: [{ id: "item", state, fileAvailable: state === "completed" }] }]; } };
      requests.push({ path, options }); if (failure) throw new Error(failure); return {};
    },
  });
  return { bridge, values, listeners, requests, stopped, wakes: () => wakes };
}

test("a task-bound completed browser download imports the exact file without a second publisher fetch", async () => {
  const f = fixture();
  await f.bridge.register({ itemId: "item", taskId: "task", lease: "fixture-lease" }, { downloadId: 7, sourceUrl: "https://publisher.test/export?secret=signed" });
  assert.deepEqual(f.stopped, ["item"]);
  assert.equal(f.wakes(), 1);
  assert.equal(await f.bridge.drain(), true);
  assert.equal(f.requests.length, 1);
  assert.equal(f.requests[0].path, "/v1/browser/items/item/download");
  assert.equal(f.requests[0].options.json.downloadId, 7);
  assert.equal(f.requests[0].options.json.filename, "D:/LitScoutFixture/Downloads/paper.pdf");
  assert.equal(JSON.stringify(f.values).includes("signed"), false);
  assert.deepEqual(f.values.pendingDownloads, {});
});

test("completed and skipped items reject late automatic downloads", async () => {
  for (const state of ["completed", "cancelled"]) {
    const f = fixture(state);
    await f.bridge.register({ itemId: "item", taskId: "task" }, { downloadId: 7 });
    await f.bridge.drain();
    assert.equal(f.requests.length, 0);
    assert.deepEqual(f.values.pendingDownloads, {});
  }
});

test("unknown download events never open or import unrelated files", async () => {
  const f = fixture();
  for (const listener of f.listeners) listener({ id: 99, state: { current: "complete" } });
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(f.wakes(), 0);
  assert.equal(f.requests.length, 0);
  assert.equal(await f.bridge.drain(), false);
});

test("a directory rejection is recorded once and leaves explicit file import as recovery", async () => {
  const f = fixture("waiting_user", "SAVED_FILE_OUTSIDE_DOWNLOADS");
  await f.bridge.register({ itemId: "item", taskId: "task" }, { downloadId: 7 });
  await f.bridge.drain();
  assert.equal(f.values.lastError, "SAVED_FILE_OUTSIDE_DOWNLOADS");
  assert.equal(await f.bridge.drain(), false);
  assert.equal(f.requests.length, 1);
});

test("failed saved-file import immediately hands the active item back to the user", async () => {
  const f = fixture("browser_running", "SAVED_FILE_OUTSIDE_DOWNLOADS");
  await f.bridge.register({ itemId: "item", taskId: "task", lease: "fixture-lease" }, { downloadId: 7 });
  await f.bridge.drain();
  assert.equal(f.requests.length, 2);
  assert.equal(f.requests[0].options.timeoutMs, 90000);
  assert.equal(f.requests[1].path, "/v1/browser/items/item/report");
  assert.equal(f.requests[1].options.json.state, "waiting_user");
  assert.equal(f.requests[1].options.json.reason, "saved_download_import_failed");
});
