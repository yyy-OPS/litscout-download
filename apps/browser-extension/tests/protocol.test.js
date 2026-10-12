import test from "node:test";
import assert from "node:assert/strict";
import { articleUrl, blockedReason, landingPage, localEndpoint, pairingInput } from "../src/shared/protocol.js";

test("only a local loopback companion can receive browser tokens and PDF bytes", () => {
  assert.equal(localEndpoint("http://127.0.0.1:57133"), "http://127.0.0.1:57133");
  for (const url of ["http://127.0.0.1", "http://127.0.0.1:1023", "https://evil.example", "http://localhost:57133", "http://127.0.0.1:57133/?token=x", "http://x:y@127.0.0.1:57133"]) {
    assert.throws(() => localEndpoint(url));
  }
});

test("connection code chooses any valid port without accepting a remote endpoint", () => {
  const code = "test-code-abcdefghijklmnopqrstuv";
  assert.deepEqual(pairingInput(`litscout-download:v1:57143:${code}`, "http://127.0.0.1:57133"),
    {code, endpoint: "http://127.0.0.1:57143"});
  assert.deepEqual(pairingInput(code, "http://127.0.0.1:57133"), {code, endpoint: "http://127.0.0.1:57133"});
  for (const port of [1024, 57123, 65535]) assert.equal(pairingInput(`litscout-download:v1:${port}:${code}`, "http://127.0.0.1:57133").endpoint, `http://127.0.0.1:${port}`);
  for (const port of [99999, 80]) assert.throws(() => pairingInput(`litscout-download:v1:${port}:${code}`, "http://127.0.0.1:57133"));
  assert.throws(() => pairingInput("litscout-download:v1:https://evil.example:secret", "http://127.0.0.1:57133"));
});

test("publisher navigation rejects local targets, credentials and unsupported schemes", () => {
  for (const url of ["javascript:alert(1)", "file:///private", "http://example.org", "https://127.0.0.1", "https://[::1]", "https://localhost", "https://foo.internal", "https://user:pass@example.org"]) {
    assert.equal(articleUrl(url), null);
  }
  assert.equal(landingPage({ links: { doiUrl: "https://doi.org/10.1234/test" } }), "https://doi.org/10.1234/test");
});

test("challenges always pause; a generic institutional-login footer is not a failure", () => {
  assert.equal(blockedReason({ access: { captcha: true }, candidates: [{}] }), "publisher_verification");
  assert.equal(blockedReason({ access: { loginRequired: true }, candidates: [{}] }), null);
  assert.equal(blockedReason({ access: { noEntitlement: true }, candidates: [] }), "no_entitlement");
});

test("browser fallback opens the article rather than retrying a failed public candidate", () => {
  const pdfUrl = "https://cdn.example/paper.pdf";
  const doiUrl = "https://doi.org/10.1234/test";
  assert.equal(landingPage({ links: { pdfUrl, doiUrl } }), doiUrl);
  assert.equal(landingPage({ links: { pdfUrl, doiUrl, landingPage: "https://publisher.example/reader" } }), doiUrl);
  assert.equal(landingPage({ links: { pdfUrl, landingPage: "https://publisher.example/article" } }), "https://publisher.example/article");
  assert.equal(landingPage({ links: { pdfUrl } }), pdfUrl);
});

