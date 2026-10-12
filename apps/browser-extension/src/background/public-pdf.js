import { articleUrl } from "../shared/protocol.js";

// Public candidates only: no cookies, redirect following, or challenge retries.
// The companion still verifies the paper identity before making it readable.
export async function fetchPublicPdf(url, { maxBytes, signal, fetchImpl = fetch, timeoutMs = 20000 }) {
  const target = articleUrl(url);
  if (!target) return null;
  const timeout = AbortSignal.timeout(timeoutMs);
  const response = await fetchImpl(target, {
    credentials: "omit", redirect: "error", cache: "no-store", referrerPolicy: "no-referrer",
    signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
  });
  if (!response.ok || !response.body) { await response.body?.cancel(); return null; }
  if (Number(response.headers.get("content-length")) > maxBytes) {
    await response.body.cancel();
    return null;
  }
  const reader = response.body.getReader();
  const chunks = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > maxBytes) return null;
      chunks.push(value);
    }
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  const header = new TextDecoder().decode(bytes.subarray(0, 1024));
  return header.includes("%PDF-") ? bytes : null;
}
