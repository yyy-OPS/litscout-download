import assert from "node:assert/strict";
import test from "node:test";
import { startNavigationPdfCapture } from "../src/background/navigation-pdf-capture.js";

function eventApi() {
  const listeners = new Set();
  return {
    listeners,
    addListener: (listener) => listeners.add(listener),
    removeListener: (listener) => listeners.delete(listener),
  };
}

test("captures a complete browser navigation PDF response without issuing a second fetch", async () => {
  const onEvent = eventApi();
  const onCreated = eventApi();
  const onUpdated = eventApi();
  const calls = [];
  const body = new TextEncoder().encode(`%PDF-1.7\n${"x".repeat(4096)}`);
  const binary = String.fromCharCode(...body);
  const chromeApi = {
    debugger: {
      onEvent,
      async attach(target, version) { calls.push(["attach", target, version]); },
      async detach(target) { calls.push(["detach", target]); },
      async sendCommand(target, command, params) {
        calls.push(["command", target, command, params]);
        if (command === "Fetch.getResponseBody") return { body: btoa(binary), base64Encoded: true };
        return {};
      },
    },
    tabs: { onCreated, onUpdated },
  };

  const capture = await startNavigationPdfCapture(chromeApi, {
    initialTabId: 11,
    expectedUrl: "https://pdf.sciencedirectassets.com/path/main.pdf?token=secret",
    maxBytes: 10000,
    timeoutMs: 1000,
  });
  const source = { tabId: 11 };
  for (const listener of onEvent.listeners) listener(source, "Fetch.requestPaused", {
    requestId: "request-1",
    request: { url: "https://pdf.sciencedirectassets.com/path/main.pdf?token=new" },
    responseStatusCode: 200,
    responseStatusText: "OK",
    responseHeaders: [
      { name: "content-type", value: "application/pdf" },
      { name: "content-length", value: String(body.byteLength) },
      { name: "content-disposition", value: "attachment; filename=paper.pdf" },
    ],
  });
  const result = await capture.waitForPdf();
  await capture.close();

  assert.equal(new TextDecoder("ascii").decode(result.bytes.slice(0, 5)), "%PDF-");
  assert.equal(calls.filter((entry) => entry[0] === "command" && entry[2] === "Fetch.getResponseBody").length, 1);
  assert.equal(calls.filter((entry) => entry[0] === "command" && entry[2] === "Fetch.fulfillRequest").length, 1);
  const fulfilled = calls.find((entry) => entry[0] === "command" && entry[2] === "Fetch.fulfillRequest")[3];
  const fulfilledHeaders = Object.fromEntries(fulfilled.responseHeaders.map((header) => [header.name.toLowerCase(), header.value]));
  assert.equal(fulfilledHeaders["content-type"], "application/pdf");
  assert.equal(fulfilledHeaders["content-disposition"], "inline");
  assert.equal(onEvent.listeners.size, 0);
  assert.deepEqual(calls.at(-1), ["detach", { tabId: 11 }]);
});

test("assembles Chromium PDF Viewer byte-range responses before accepting the file", async () => {
  const onEvent = eventApi();
  const onCreated = eventApi();
  const onUpdated = eventApi();
  const body = new TextEncoder().encode(`%PDF-1.7\n${"x".repeat(4096)}`);
  const parts = [body.slice(0, 1024), body.slice(1024)];
  let currentPart = 0;
  const chromeApi = {
    debugger: {
      onEvent,
      async attach() {},
      async detach() {},
      async sendCommand(_target, command) {
        if (command === "Fetch.getResponseBody") {
          const bytes = parts[currentPart++];
          return { body: btoa(String.fromCharCode(...bytes)), base64Encoded: true };
        }
        return {};
      },
    },
    tabs: { onCreated, onUpdated },
  };
  const url = "https://pdf.sciencedirectassets.com/path/main.pdf?token=range";
  const capture = await startNavigationPdfCapture(chromeApi, {
    initialTabId: 11,
    expectedUrl: url,
    maxBytes: 10000,
    timeoutMs: 1000,
  });
  const source = { tabId: 11 };
  const emitRange = (requestId, start, end) => {
    for (const listener of onEvent.listeners) listener(source, "Fetch.requestPaused", {
      requestId,
      request: { url },
      responseStatusCode: 206,
      responseStatusText: "Partial Content",
      responseHeaders: [
        { name: "content-type", value: "application/pdf" },
        { name: "content-range", value: `bytes ${start}-${end}/${body.byteLength}` },
      ],
    });
  };
  emitRange("range-1", 0, 1023);
  await new Promise((resolve) => setTimeout(resolve, 0));
  emitRange("range-2", 1024, body.byteLength - 1);
  const result = await capture.waitForPdf();
  await capture.close();
  assert.equal(result.bytes.byteLength, body.byteLength);
  assert.equal(new TextDecoder("ascii").decode(result.bytes.slice(0, 5)), "%PDF-");
});

test("continues a publisher 403 response and captures the later authorized PDF without a second fetch", async () => {
  const onEvent = eventApi();
  const onCreated = eventApi();
  const onUpdated = eventApi();
  const calls = [];
  const body = new TextEncoder().encode(`%PDF-1.7\n${"authorized".repeat(512)}`);
  const binary = String.fromCharCode(...body);
  const chromeApi = {
    debugger: {
      onEvent,
      async attach() {},
      async detach() {},
      async sendCommand(target, command, params) {
        calls.push([target, command, params]);
        if (command === "Fetch.getResponseBody") return { body: btoa(binary), base64Encoded: true };
        return {};
      },
    },
    tabs: { onCreated, onUpdated },
  };
  const url = "https://pdf.sciencedirectassets.com/path/1-s2.0-S1/main.pdf?pii=S1";
  const capture = await startNavigationPdfCapture(chromeApi, {
    initialTabId: 11,
    expectedPii: "S1",
    maxBytes: 10000,
    timeoutMs: 1000,
  });
  const emit = (requestId, responseStatusCode) => {
    for (const listener of onEvent.listeners) listener({ tabId: 11 }, "Fetch.requestPaused", {
      requestId,
      request: { url },
      responseStatusCode,
      responseStatusText: responseStatusCode === 200 ? "OK" : "Forbidden",
      responseHeaders: [{ name: "content-type", value: responseStatusCode === 200 ? "application/pdf" : "text/html" }],
    });
  };
  emit("challenge", 403);
  await new Promise((resolve) => setTimeout(resolve, 0));
  emit("authorized", 200);
  const result = await capture.waitForPdf();
  await capture.close();

  assert.equal(new TextDecoder("ascii").decode(result.bytes.slice(0, 5)), "%PDF-");
  assert.equal(calls.filter((entry) => entry[1] === "Fetch.continueResponse").length, 1);
  assert.equal(calls.filter((entry) => entry[1] === "Fetch.getResponseBody").length, 1);
  assert.equal(calls.filter((entry) => entry[1] === "Fetch.fulfillRequest").length, 1);
});

test("waits through a 200 WAF page and captures a later octet-stream PDF from the reader", async () => {
  const onEvent = eventApi();
  const onCreated = eventApi();
  const onUpdated = eventApi();
  const challenge = new TextEncoder().encode("<!doctype html><meta name=aliyun_waf_aa><script>location.reload()</script>");
  const pdf = new TextEncoder().encode(`%PDF-1.7\n${"opticsjournal".repeat(400)}`);
  const bodies = new Map([
    ["waf", challenge],
    ["reader-pdf", pdf],
  ]);
  const calls = [];
  const chromeApi = {
    debugger: {
      onEvent,
      async attach() {},
      async detach() {},
      async sendCommand(_target, command, params) {
        calls.push([command, params]);
        if (command === "Fetch.getResponseBody") {
          const bytes = bodies.get(params.requestId);
          return { body: btoa(String.fromCharCode(...bytes)), base64Encoded: true };
        }
        return {};
      },
    },
    tabs: { onCreated, onUpdated },
  };
  const capture = await startNavigationPdfCapture(chromeApi, {
    initialTabId: 12,
    expectedUrl: "https://www.opticsjournal.net/Articles/GetArticlePDF/OJ94d87db07ad65c80",
    allowAnyHttpPdf: true,
    allowUnboundPdfResponse: true,
    failOnAccessDenied: false,
    maxBytes: 10000,
    timeoutMs: 1000,
  });
  const emit = (requestId, url, contentType, resourceType = "Document") => {
    for (const listener of onEvent.listeners) listener({ tabId: 12 }, "Fetch.requestPaused", {
      requestId,
      networkId: requestId,
      resourceType,
      request: { url },
      responseStatusCode: 200,
      responseStatusText: "OK",
      responseHeaders: [{ name: "content-type", value: contentType }],
    });
  };

  emit("waf", "https://www.opticsjournal.net/Articles/GetArticlePDF/OJ94d87db07ad65c80", "text/html");
  await new Promise((resolve) => setTimeout(resolve, 0));
  emit("reader-pdf", "https://www.opticsjournal.net/reader/content/OJ94d87db07ad65c80", "application/octet-stream", "Fetch");
  const result = await capture.waitForPdf();
  await capture.close();

  assert.equal(result.sourceUrl, "https://www.opticsjournal.net/reader/content/OJ94d87db07ad65c80");
  assert.equal(new TextDecoder("ascii").decode(result.bytes.slice(0, 5)), "%PDF-");
  assert.equal(calls.filter(([command]) => command === "Fetch.getResponseBody").length, 2);
});

test("follows only the trusted cross-origin redirect chain for a generic publisher PDF", async () => {
  const onEvent = eventApi();
  const onCreated = eventApi();
  const onUpdated = eventApi();
  const calls = [];
  const body = new TextEncoder().encode(`%PDF-1.7\n${"redirected".repeat(512)}`);
  const binary = String.fromCharCode(...body);
  const chromeApi = {
    debugger: {
      onEvent,
      async attach() {},
      async detach() {},
      async sendCommand(_target, command, params) {
        calls.push([command, params]);
        if (command === "Fetch.getResponseBody") return { body: btoa(binary), base64Encoded: true };
        return {};
      },
    },
    tabs: { onCreated, onUpdated },
  };
  const capture = await startNavigationPdfCapture(chromeApi, {
    initialTabId: 21,
    expectedUrl: "https://publisher.example/article/10/pdf",
    allowAnyHttpPdf: true,
    maxBytes: 10000,
    timeoutMs: 1000,
  });
  const emit = (requestId, url, status, networkId, headers = []) => {
    for (const listener of onEvent.listeners) listener({ tabId: 21 }, "Fetch.requestPaused", {
      requestId,
      networkId,
      resourceType: "Document",
      request: { url },
      responseStatusCode: status,
      responseStatusText: status === 200 ? "OK" : "Found",
      responseHeaders: headers,
    });
  };
  emit("unrelated", "https://publisher.example/supplement.pdf", 200, "other");
  await new Promise((resolve) => setTimeout(resolve, 0));
  emit("redirect", "https://publisher.example/article/10/pdf", 302, "navigation-1", [
    { name: "location", value: "https://assets.publisher-cdn.example/files/main.pdf?token=one" },
  ]);
  await new Promise((resolve) => setTimeout(resolve, 0));
  emit("pdf", "https://assets.publisher-cdn.example/files/main.pdf?token=two", 200, "navigation-1", [
    { name: "content-type", value: "application/pdf" },
  ]);
  const result = await capture.waitForPdf();
  await capture.close();

  assert.equal(result.sourceUrl, "https://assets.publisher-cdn.example/files/main.pdf?token=two");
  assert.equal(new TextDecoder("ascii").decode(result.bytes.slice(0, 5)), "%PDF-");
  assert.equal(calls.filter(([command]) => command === "Fetch.getResponseBody").length, 1);
  assert.equal(calls.filter(([command]) => command === "Fetch.continueResponse").length, 2);
});

test("ignores a Wiley ePDF shell and captures its unbound PDF document response", async () => {
  const onEvent = eventApi();
  const onCreated = eventApi();
  const onUpdated = eventApi();
  const html = new TextEncoder().encode("<!doctype html><title>Wiley ePDF</title>");
  const pdf = new TextEncoder().encode(`%PDF-1.7\n${"wiley".repeat(900)}`);
  const bodies = new Map([
    ["epdf-shell", html],
    ["pdfdirect", pdf],
  ]);
  const chromeApi = {
    debugger: {
      onEvent,
      async attach() {},
      async detach() {},
      async sendCommand(_target, command, params) {
        if (command === "Fetch.getResponseBody") {
          const bytes = bodies.get(params.requestId);
          return { body: btoa(String.fromCharCode(...bytes)), base64Encoded: true };
        }
        return {};
      },
    },
    tabs: { onCreated, onUpdated },
  };
  const directUrl = "https://advanced.onlinelibrary.wiley.com/doi/pdfdirect/10.1002/qute.202100049?download=true";
  const capture = await startNavigationPdfCapture(chromeApi, {
    initialTabId: 24,
    expectedUrl: directUrl,
    allowAnyHttpPdf: true,
    allowUnboundDocument: true,
    maxBytes: 10000,
    timeoutMs: 1000,
  });
  const emit = (requestId, url, contentType) => {
    for (const listener of onEvent.listeners) listener({ tabId: 24 }, "Fetch.requestPaused", {
      requestId,
      networkId: requestId,
      resourceType: "Document",
      request: { url },
      responseStatusCode: 200,
      responseStatusText: "OK",
      responseHeaders: [
        { name: "content-type", value: contentType },
        { name: "content-disposition", value: contentType === "application/pdf" ? "attachment; filename=paper.pdf" : "inline" },
      ],
    });
  };
  emit("epdf-shell", "https://advanced.onlinelibrary.wiley.com/doi/epdf/10.1002/qute.202100049", "text/html");
  await new Promise((resolve) => setTimeout(resolve, 0));
  emit("pdfdirect", directUrl, "application/pdf");
  const result = await capture.waitForPdf();
  await capture.close();
  assert.equal(new TextDecoder("ascii").decode(result.bytes.slice(0, 5)), "%PDF-");
  assert.equal(result.sourceUrl, directUrl);
});

test("captures only the IEEE getPDF response with the expected arnumber", async () => {
  const onEvent = eventApi();
  const onCreated = eventApi();
  const onUpdated = eventApi();
  const pdf = new TextEncoder().encode(`%PDF-1.7\n${"ieee".repeat(900)}`);
  let bodyReads = 0;
  let continued = 0;
  const chromeApi = {
    debugger: {
      onEvent,
      async attach() {},
      async detach() {},
      async sendCommand(_target, command) {
        if (command === "Fetch.getResponseBody") {
          bodyReads += 1;
          return { body: btoa(String.fromCharCode(...pdf)), base64Encoded: true };
        }
        if (command === "Fetch.continueResponse") continued += 1;
        return {};
      },
    },
    tabs: { onCreated, onUpdated },
  };
  const expectedUrl = "https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber=10654720";
  const capture = await startNavigationPdfCapture(chromeApi, {
    initialTabId: 25,
    expectedUrl,
    expectedQueryParams: { arnumber: "10654720" },
    allowAnyHttpPdf: true,
    maxBytes: 10000,
    timeoutMs: 1000,
  });
  const emit = (requestId, arnumber) => {
    for (const listener of onEvent.listeners) listener({ tabId: 25 }, "Fetch.requestPaused", {
      requestId,
      networkId: requestId,
      resourceType: "Document",
      request: { url: `https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber=${arnumber}` },
      responseStatusCode: 200,
      responseStatusText: "OK",
      responseHeaders: [{ name: "content-type", value: "application/pdf" }],
    });
  };
  emit("wrong-paper", "99999999");
  await new Promise((resolve) => setTimeout(resolve, 0));
  emit("expected-paper", "10654720");
  const result = await capture.waitForPdf();
  await capture.close();
  assert.equal(result.sourceUrl, expectedUrl);
  assert.equal(bodyReads, 1);
  assert.equal(continued, 1);
});

test("assembles generic redirected PDF ranges even when each range has a new network id", async () => {
  const onEvent = eventApi();
  const onCreated = eventApi();
  const onUpdated = eventApi();
  const body = new TextEncoder().encode(`%PDF-1.7\n${"range".repeat(900)}`);
  const parts = [body.slice(0, 1536), body.slice(1536)];
  let partIndex = 0;
  const chromeApi = {
    debugger: {
      onEvent,
      async attach() {},
      async detach() {},
      async sendCommand(_target, command) {
        if (command === "Fetch.getResponseBody") {
          const bytes = parts[partIndex++];
          return { body: btoa(String.fromCharCode(...bytes)), base64Encoded: true };
        }
        return {};
      },
    },
    tabs: { onCreated, onUpdated },
  };
  const entryUrl = "https://publisher.example/article/pdf";
  const assetUrl = "https://cdn.example/main.pdf?signature=one";
  const capture = await startNavigationPdfCapture(chromeApi, {
    initialTabId: 22,
    expectedUrl: entryUrl,
    allowAnyHttpPdf: true,
    maxBytes: 10000,
    timeoutMs: 1000,
  });
  for (const listener of onEvent.listeners) listener({ tabId: 22 }, "Fetch.requestPaused", {
    requestId: "redirect",
    networkId: "redirect-network",
    resourceType: "Document",
    request: { url: entryUrl },
    responseStatusCode: 302,
    responseHeaders: [{ name: "location", value: assetUrl }],
  });
  await new Promise((resolve) => setTimeout(resolve, 0));
  const emitRange = (requestId, networkId, start, end) => {
    for (const listener of onEvent.listeners) listener({ tabId: 22 }, "Fetch.requestPaused", {
      requestId,
      networkId,
      resourceType: "Other",
      request: { url: assetUrl.replace("one", networkId) },
      responseStatusCode: 206,
      responseStatusText: "Partial Content",
      responseHeaders: [
        { name: "content-type", value: "application/pdf" },
        { name: "content-range", value: `bytes ${start}-${end}/${body.byteLength}` },
      ],
    });
  };
  emitRange("range-1", "range-network-1", 0, 1535);
  await new Promise((resolve) => setTimeout(resolve, 0));
  emitRange("range-2", "range-network-2", 1536, body.byteLength - 1);
  const result = await capture.waitForPdf();
  await capture.close();

  assert.equal(result.bytes.byteLength, body.byteLength);
  assert.equal(new TextDecoder("ascii").decode(result.bytes.slice(0, 5)), "%PDF-");
});

test("fails a generic publisher capture promptly on a trusted access denial", async () => {
  const onEvent = eventApi();
  const onCreated = eventApi();
  const onUpdated = eventApi();
  let continued = 0;
  const chromeApi = {
    debugger: {
      onEvent,
      async attach() {},
      async detach() {},
      async sendCommand(_target, command) {
        if (command === "Fetch.continueResponse") continued += 1;
        return {};
      },
    },
    tabs: { onCreated, onUpdated },
  };
  const url = "https://ieeexplore.ieee.org/stamp/stamp.jsp?arnumber=10654720";
  const capture = await startNavigationPdfCapture(chromeApi, {
    initialTabId: 23,
    expectedUrl: url,
    allowAnyHttpPdf: true,
    failOnAccessDenied: true,
    maxBytes: 10000,
    timeoutMs: 1000,
  });
  const pending = capture.waitForPdf();
  for (const listener of onEvent.listeners) listener({ tabId: 23 }, "Fetch.requestPaused", {
    requestId: "denied",
    networkId: "denied-network",
    resourceType: "Document",
    request: { url },
    responseStatusCode: 418,
    responseHeaders: [{ name: "content-type", value: "text/html" }],
  });
  await assert.rejects(pending, /HTTP 418/);
  await capture.close();
  assert.equal(continued, 1);
});

test("close rejects a pending navigation capture and detaches promptly", async () => {
  const onEvent = eventApi();
  const onCreated = eventApi();
  const onUpdated = eventApi();
  let detached = false;
  const chromeApi = {
    debugger: {
      onEvent,
      async attach() {},
      async detach() { detached = true; },
      async sendCommand() { return {}; },
    },
    tabs: { onCreated, onUpdated },
  };
  const capture = await startNavigationPdfCapture(chromeApi, {
    initialTabId: 11,
    expectedPii: "S1",
    maxBytes: 10000,
    timeoutMs: 10000,
  });
  const pending = capture.waitForPdf();
  await capture.close(new Error("stopped"));
  await assert.rejects(pending, /stopped/);
  assert.equal(detached, true);
  assert.equal(onEvent.listeners.size, 0);
});

test("closing before waitForPdf does not emit an unhandled rejection", async () => {
  const onEvent = eventApi();
  const onCreated = eventApi();
  const onUpdated = eventApi();
  const chromeApi = {
    debugger: {
      onEvent,
      async attach() {},
      async detach() {},
      async sendCommand() { return {}; },
    },
    tabs: { onCreated, onUpdated },
  };
  const unhandled = [];
  const onUnhandled = (reason) => unhandled.push(reason);
  process.on("unhandledRejection", onUnhandled);
  try {
    const capture = await startNavigationPdfCapture(chromeApi, {
      initialTabId: 11,
      expectedPii: "S1",
      maxBytes: 10000,
      timeoutMs: 10000,
    });
    await capture.close();
    await new Promise((resolve) => setImmediate(resolve));
    assert.deepEqual(unhandled, []);
    await assert.rejects(capture.waitForPdf(), /捕获已停止/);
  } finally {
    process.off("unhandledRejection", onUnhandled);
  }
});

test("captures a PDF loaded inside a bound direct-reader tab without a PDF URL suffix", async () => {
  const onEvent = eventApi();
  const onCreated = eventApi();
  const onUpdated = eventApi();
  const pdf = new TextEncoder().encode(`%PDF-1.7\n${"reader".repeat(900)}`);
  let reads = 0;
  const chromeApi = {
    debugger: {
      onEvent,
      async attach() {},
      async detach() {},
      async sendCommand(_target, command) {
        if (command === "Fetch.getResponseBody") {
          reads += 1;
          return { body: btoa(String.fromCharCode(...pdf)), base64Encoded: true };
        }
        return {};
      },
    },
    tabs: { onCreated, onUpdated },
  };
  const capture = await startNavigationPdfCapture(chromeApi, {
    initialTabId: 31,
    expectedUrl: "https://www.opticsjournal.net/Articles/GetArticlePDF/OJ94d87db07ad65c80",
    allowAnyHttpPdf: true,
    allowUnboundPdfResponse: true,
    maxBytes: 10000,
    timeoutMs: 1000,
  });

  for (const listener of onEvent.listeners) listener({ tabId: 31 }, "Fetch.requestPaused", {
    requestId: "reader-pdf",
    networkId: "reader-pdf",
    resourceType: "Fetch",
    request: { url: "https://static.opticsjournal.net/content/OJ94d87db07ad65c80" },
    responseStatusCode: 200,
    responseStatusText: "OK",
    responseHeaders: [{ name: "content-type", value: "application/pdf" }],
  });

  const result = await capture.waitForPdf();
  await capture.close();
  assert.equal(result.sourceUrl, "https://static.opticsjournal.net/content/OJ94d87db07ad65c80");
  assert.equal(new TextDecoder("ascii").decode(result.bytes.slice(0, 5)), "%PDF-");
  assert.equal(reads, 1);
});

test("does not read an unbound HTML response in direct-reader mode", async () => {
  const onEvent = eventApi();
  const onCreated = eventApi();
  const onUpdated = eventApi();
  let reads = 0;
  const chromeApi = {
    debugger: {
      onEvent,
      async attach() {},
      async detach() {},
      async sendCommand(_target, command) {
        if (command === "Fetch.getResponseBody") reads += 1;
        return {};
      },
    },
    tabs: { onCreated, onUpdated },
  };
  const capture = await startNavigationPdfCapture(chromeApi, {
    initialTabId: 32,
    expectedUrl: "https://reader.example/article/1",
    allowAnyHttpPdf: true,
    allowUnboundPdfResponse: true,
    maxBytes: 10000,
    timeoutMs: 30,
  });

  for (const listener of onEvent.listeners) listener({ tabId: 32 }, "Fetch.requestPaused", {
    requestId: "challenge",
    networkId: "challenge",
    resourceType: "Fetch",
    request: { url: "https://reader.example/challenge" },
    responseStatusCode: 200,
    responseStatusText: "OK",
    responseHeaders: [{ name: "content-type", value: "text/html" }],
  });

  await assert.rejects(capture.waitForPdf());
  await capture.close();
  assert.equal(reads, 0);
});

test("rejects HTML even when the download response claims a PDF filename", async () => {
  const onEvent = eventApi();
  const onCreated = eventApi();
  const onUpdated = eventApi();
  const html = new TextEncoder().encode(`<!doctype html>${"not a pdf".repeat(300)}`);
  const chromeApi = {
    debugger: {
      onEvent,
      async attach() {},
      async detach() {},
      async sendCommand(_target, command) {
        if (command === "Fetch.getResponseBody") {
          return { body: btoa(String.fromCharCode(...html)), base64Encoded: true };
        }
        return {};
      },
    },
    tabs: { onCreated, onUpdated },
  };
  const capture = await startNavigationPdfCapture(chromeApi, {
    initialTabId: 33,
    allowAnyHttpPdf: true,
    allowUnboundPdfResponse: true,
    maxBytes: 10000,
    timeoutMs: 30,
  });
  for (const listener of onEvent.listeners) listener({ tabId: 33 }, "Fetch.requestPaused", {
    requestId: "fake-pdf",
    resourceType: "Document",
    request: { url: "https://publisher.example/export?id=33" },
    responseStatusCode: 200,
    responseStatusText: "OK",
    responseHeaders: [
      { name: "content-type", value: "application/octet-stream" },
      { name: "content-disposition", value: "attachment; filename=paper.pdf" },
    ],
  });
  await assert.rejects(capture.waitForPdf(), /尚不完整/);
  await capture.close();
});

test("captures a JavaScript exportPdf attachment response after the article button is clicked", async () => {
  const onEvent = eventApi();
  const onCreated = eventApi();
  const onUpdated = eventApi();
  const body = new TextEncoder().encode(`%PDF-1.7\n${"dynamic".repeat(800)}`);
  const chromeApi = {
    debugger: {
      onEvent,
      async attach() {},
      async detach() {},
      async sendCommand(_target, command) {
        if (command === "Fetch.getResponseBody") {
          return { body: btoa(String.fromCharCode(...body)), base64Encoded: true };
        }
        return {};
      },
    },
    tabs: { onCreated, onUpdated, async get() { return { url: "http://www.applmathmech.cn/en/article/doi/10.21656/1000-0887.370092" }; } },
  };
  const capture = await startNavigationPdfCapture(chromeApi, {
    initialTabId: 77,
    maxBytes: 10000,
    timeoutMs: 1000,
    allowAnyHttpPdf: true,
    allowUnboundDocument: true,
    allowUnboundPdfResponse: true,
  });
  for (const listener of onEvent.listeners) listener({ tabId: 77 }, "Fetch.requestPaused", {
    requestId: "export-pdf-5086",
    request: { url: "http://www.applmathmech.cn/en/article/exportPdf?id=5086&language=en" },
    resourceType: "Document",
    responseStatusCode: 200,
    responseStatusText: "OK",
    responseHeaders: [
      { name: "content-type", value: "application/octet-stream" },
      { name: "content-disposition", value: "attachment; filename=370092.pdf" },
    ],
  });
  const result = await capture.waitForPdf();
  await capture.close();
  assert.equal(result.sourceUrl, "http://www.applmathmech.cn/en/article/exportPdf?id=5086&language=en");
  assert.equal(new TextDecoder("ascii").decode(result.bytes.slice(0, 5)), "%PDF-");
});

test("observes same-page browser download candidates and removes the listener after capture", async () => {
  const onEvent = eventApi();
  const onCreated = eventApi();
  const onUpdated = eventApi();
  const onDownloadCreated = eventApi();
  const observed = [];
  const chromeApi = {
    debugger: {
      onEvent,
      async attach() {},
      async detach() {},
      async sendCommand() { return {}; },
    },
    tabs: {
      onCreated,
      onUpdated,
      async get() { return { url: "https://publisher.example/article/42" }; },
    },
    downloads: { onCreated: onDownloadCreated },
  };
  const capture = await startNavigationPdfCapture(chromeApi, {
    initialTabId: 42,
    maxBytes: 10000,
    timeoutMs: 1000,
    allowAnyHttpPdf: true,
    allowUnboundPdfResponse: true,
    onDownloadCandidate: (candidate) => observed.push(candidate),
  });
  for (const listener of onDownloadCreated.listeners) {
    listener({ id: 1, url: "https://other.example/wrong.pdf", filename: "wrong.pdf", mime: "application/pdf", tabId: 99 });
    listener({ id: 3, url: "https://publisher.example/other.pdf", filename: "other.pdf", mime: "application/pdf", referrer: "https://publisher.example/article/43" });
    listener({ id: 4, url: "https://publisher.example/export.pdf", filename: "paper.pdf", mime: "application/pdf", referrer: "https://publisher.example/article/42", tabId: -1 });
    listener({ id: 2, url: "https://publisher.example/export?id=42", filename: "paper.pdf", mime: "application/octet-stream", tabId: 42 });
  }
  assert.equal(observed.length, 2);
  assert.equal(observed[0].downloadId, 4);
  assert.equal(observed[1].downloadId, 2);
  const blob = "blob:https://publisher.example/current-pdf";
  const blobDownload = { id: 5, url: blob, filename: "paper.pdf", mime: "application/pdf", tabId: -1, startTime: new Date(Date.now() + 10).toISOString() };
  capture.observeDownloadUrl(blob, 99);
  for (const listener of onDownloadCreated.listeners) listener(blobDownload);
  assert.equal(observed.length, 2);
  capture.observeDownloadUrl(blob, 42);
  for (const listener of onDownloadCreated.listeners) listener({ ...blobDownload, startTime: "2000-01-01T00:00:00Z" });
  assert.equal(observed.length, 2);
  for (const listener of onDownloadCreated.listeners) listener(blobDownload);
  assert.equal(observed.length, 3);
  assert.equal(observed[2].downloadId, 5);
  await capture.close();
  assert.equal(onDownloadCreated.listeners.size, 0);
});
