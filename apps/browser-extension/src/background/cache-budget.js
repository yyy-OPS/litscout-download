const MAX_CACHE_BYTES = 256 * 1024 * 1024;
const UNKNOWN_FILE_BYTES = 32 * 1024 * 1024;

export async function ensureCacheRoom(cache, newBytes, storageManager = globalThis.navigator?.storage) {
  let used = 0;
  for (const key of await cache.keys()) {
    const response = await cache.match(key);
    const length = Number(response?.headers?.get("Content-Length"));
    used += Number.isFinite(length) && length > 0 ? length : UNKNOWN_FILE_BYTES;
  }
  if (used + newBytes > MAX_CACHE_BYTES) throw new Error("BROWSER_CACHE_FULL");
  if (storageManager?.estimate) {
    const estimate = await storageManager.estimate();
    if (estimate.quota > 0 && estimate.quota - (estimate.usage || 0) < newBytes + UNKNOWN_FILE_BYTES) {
      throw new Error("BROWSER_CACHE_FULL");
    }
  }
}
