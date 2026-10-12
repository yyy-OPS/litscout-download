import test from "node:test";
import assert from "node:assert/strict";
import { ensureCacheRoom } from "../src/background/cache-budget.js";

test("bounded temporary PDF cache refuses new data beyond its budget", async () => {
  const cache = { async keys() { return ["existing"]; }, async match() { return { headers: { get() { return String(256 * 1024 * 1024); } } }; } };
  await assert.rejects(() => ensureCacheRoom(cache, 1, null), /BROWSER_CACHE_FULL/);
});

test("low browser storage quota refuses capture before caching", async () => {
  const cache = { async keys() { return []; } };
  await assert.rejects(() => ensureCacheRoom(cache, 1024, { async estimate() { return { quota: 4096, usage: 3000 }; } }), /BROWSER_CACHE_FULL/);
  await ensureCacheRoom(cache, 1024, { async estimate() { return { quota: 1024 * 1024 * 1024, usage: 0 }; } });
});
