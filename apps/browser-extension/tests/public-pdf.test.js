import test from "node:test";
import assert from "node:assert/strict";
import { fetchPublicPdf } from "../src/background/public-pdf.js";
import { directPdfUrl } from "../src/shared/protocol.js";

test("public PDF uses no cookies and never follows redirects", async () => {
  let options;
  const bytes = await fetchPublicPdf("https://arxiv.org/pdf/2010.08895", { maxBytes: 1000,
    fetchImpl: async (_, init) => { options = init; return new Response("%PDF-1.7\nfixture"); } });
  assert.equal(new TextDecoder().decode(bytes), "%PDF-1.7\nfixture");
  assert.equal(options.credentials, "omit");
  assert.equal(options.redirect, "error");
  assert.equal(options.referrerPolicy, "no-referrer");
});

test("HTML, failures, excessive sizes and unsafe targets fall back without PDF bytes", async () => {
  for (const response of [new Response("<html>captcha</html>"), new Response("%PDF-", { status: 403 }),
    new Response("%PDF-123456789"), new Response("%PDF-", { headers: { "Content-Length": "9999" } })]) {
    assert.equal(await fetchPublicPdf("https://example.org/paper", { maxBytes: 10, fetchImpl: async () => response }), null);
  }
  assert.equal(await fetchPublicPdf("http://127.0.0.1/private", { maxBytes: 1000,
    fetchImpl: () => { throw new Error("must not fetch"); } }), null);
});

test("an aborted public attempt cannot continue after a task pause", async () => {
  const controller = new AbortController();
  controller.abort(new Error("task_paused"));
  await assert.rejects(fetchPublicPdf("https://example.org/paper", { maxBytes: 1000, signal: controller.signal,
    fetchImpl: async (_, init) => { init.signal.throwIfAborted(); } }), /task_paused/);
});

test("arXiv extensionless PDFs and conventional PDFs are direct candidates", () => {
  for (const url of ["https://arxiv.org/pdf/2010.08895", "https://arxiv.org/pdf/2010.08895v3", "https://arxiv.org/pdf/hep-th/9901001", "https://example.org/paper.pdf?download=1"])
    assert.equal(directPdfUrl(url), true);
  for (const url of ["https://arxiv.org/abs/2010.08895", "https://evil.example/pdf/2010.08895", "http://127.0.0.1/paper.pdf"])
    assert.equal(directPdfUrl(url), false);
});
