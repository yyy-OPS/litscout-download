import assert from "node:assert/strict";
import test from "node:test";
import { createBackgroundRunner } from "../src/background/background-runner.js";

test("No SW from a detached background context cannot reject the event entry point", async () => {
  let writes = 0;
  const run = createBackgroundRunner({ recordError: async () => { writes++; } });
  await assert.doesNotReject(run(async () => { throw new Error("No SW"); }));
  await assert.doesNotReject(run(() => { throw new Error("Extension context invalidated."); }));
  assert.equal(writes, 0);
});

test("an error reporter that itself loses its worker does not create a second rejection", async () => {
  const log = [];
  const run = createBackgroundRunner({ recordError: async () => { throw new Error("No SW"); },
    logError: message => log.push(message) });
  await assert.doesNotReject(run(async () => { throw new Error("CONNECTION_FAILED"); }));
  assert.deepEqual(log, []);
});

test("ordinary failures remain visible with signed URLs redacted and later work still runs", async () => {
  const recorded = [];
  const run = createBackgroundRunner({ recordError: async message => recorded.push(message) });
  await run(async () => { throw new Error("Download failed https://publisher.test/paper?token=secret"); });
  assert.deepEqual(recorded, ["Download failed [URL]"]);
  assert.equal(await run(async () => "resumed"), "resumed");
});

test("unexpected storage failure records the original sanitized failure in the console", async () => {
  const log = [];
  const run = createBackgroundRunner({ recordError: async () => { throw new Error("QUOTA_EXCEEDED"); },
    logError: message => log.push(message) });
  await run(() => { throw new Error("NETWORK_FAILED"); });
  assert.deepEqual(log, ["NETWORK_FAILED"]);
});
