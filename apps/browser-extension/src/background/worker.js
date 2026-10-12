import { startNavigationPdfCapture } from "./navigation-pdf-capture.js";
import { ensureCacheRoom } from "./cache-budget.js";
import { createSavedDownloadBridge } from "./saved-download-bridge.js";
import { startBlobDownloadProbe } from "./blob-download-probe.js";
import { createTaskNotifier } from "./task-notifications.js";
import { openTaskFromNotification } from "./notification-navigation.js";
import { startTaskMonitor } from "./task-monitor.js";
import { createBackgroundRunner } from "./background-runner.js";
import { fetchPublicPdf } from "./public-pdf.js";
import { articleUrl, blockedReason, CACHE, cacheKey, directPdfUrl, landingPage, localEndpoint } from "../shared/protocol.js";

let running = false;
let wakePending = false;
let currentCapture = null;
let activeItemId = null;
let activeTaskId = null;
let opening = false;
let captureNonce = null;
let alertRefresh = null;
let alertTimer = null;
const notifier = createTaskNotifier(chrome, { openTask: target => openTaskFromNotification(chrome, target) });
const background = createBackgroundRunner({ recordError: lastError => chrome.storage.local.set({ lastError }) });
const wake = () => void background(poll);
const downloadBridge = createSavedDownloadBridge(chrome, { api, wake, background,
  stopCapture: async itemId => {
    if (activeItemId === itemId) await currentCapture?.close(new Error("SAVED_DOWNLOAD_READY"));
  } });

async function settings() {
  return chrome.storage.local.get(["endpoint", "browserToken", "browserId", "active"]);
}

async function api(path, { method = "GET", json, body, lease, timeoutMs = body ? 90000 : 15000 } = {}) {
  const config = await settings();
  if (!config.browserToken) throw new Error("请先配对本机服务");
  const headers = { Authorization: `Bearer ${config.browserToken}`, "X-LitScout-Extension": chrome.runtime.id };
  if (lease) headers["Upload-Lease"] = lease;
  if (json) headers["Content-Type"] = "application/json";
  const response = await fetch(localEndpoint(config.endpoint) + path, {
    method, headers, body: json ? JSON.stringify(json) : body, cache: "no-store",
    credentials: "omit", redirect: "error", signal: AbortSignal.timeout(timeoutMs),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.code || `本机服务 HTTP ${response.status}`);
  }
  return response;
}

async function report(item, state, reason = "", tabId = null) {
  const response = await api(`/v1/browser/items/${item.itemId}/report`, {
    method: "POST", lease: item.lease, json: { state, reason, tabId },
  });
  if (state === "waiting_user") await refreshTaskAlerts();
  return response;
}

async function refreshTaskAlerts() {
  if (alertRefresh) return alertRefresh;
  alertRefresh = (async () => {
    const config = await settings();
    if (!config.browserToken) return;
    const tasks = await (await api("/v1/browser/tasks")).json();
    await notifier.sync(tasks);
  })().catch(() => undefined).finally(() => { alertRefresh = null; });
  return alertRefresh;
}

async function processOpenCommands() {
  if (opening) return;
  opening = true;
  try {
    const config = await settings();
    if (!config.browserToken) return;
    const commands = await (await api("/v1/browser/commands")).json();
    for (const command of commands) {
      let tabId = command.tabId;
      try {
        if (Number.isInteger(tabId)) {
          try { await chrome.tabs.update(tabId, { active: true }); }
          catch { tabId = null; }
        }
        if (!Number.isInteger(tabId)) {
          const url = landingPage(command.paper);
          if (!url) throw new Error("no_article_url");
          tabId = (await chrome.tabs.create({ url, active: true })).id;
        }
        await api(`/v1/browser/commands/${command.commandId}/done`, { method: "POST", json: { tabId } });
      } catch {
        await api(`/v1/browser/commands/${command.commandId}/done`, { method: "POST", json: { tabId: null } }).catch(() => undefined);
      }
    }
  } finally { opening = false; }
}

async function uploadCached(item) {
  const cache = await caches.open(CACHE);
  const response = await cache.match(cacheKey(item.itemId));
  if (!response) return false;
  const blob = await response.blob();
  await api(`/v1/browser/items/${item.itemId}/pdf`, { method: "PUT", lease: item.lease, body: blob });
  // Delete only after the companion has durably stored and parsed the file.
  await cache.delete(cacheKey(item.itemId));
  await refreshTaskAlerts();
  return true;
}

async function scan(tabId) {
  await chrome.scripting.executeScript({ target: { tabId }, files: ["src/content/publisher-probe.js"] });
  const response = await chrome.tabs.sendMessage(tabId, { type: "PROBE_PUBLISHER_PAGE" });
  if (!response?.ok) throw new Error("无法读取文章页面");
  return response.snapshot;
}

async function waitReady(tabId) {
  const until = Date.now() + 30000;
  while (Date.now() < until) {
    const tab = await chrome.tabs.get(tabId);
    if (tab.status === "complete") return;
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  throw new Error("页面加载超时");
}

async function capture(item) {
  let tabId = item.tabId;
  if (await uploadCached(item)) return;
  if (item.tryPublicPdf === true && !Number.isInteger(tabId) && !item.assisted && articleUrl(item.paper.links?.pdfUrl)) {
    await report(item, "browser_running", "public_pdf_attempt");
    const controller = new AbortController();
    const attempt = { close: async reason => controller.abort(reason) };
    currentCapture = attempt;
    let bytes;
    try {
      bytes = await fetchPublicPdf(item.paper.links.pdfUrl, { maxBytes: item.maxBytes, signal: controller.signal });
    } catch {
      // Public-link failure falls through to the paired publisher browser.
    } finally {
      if (currentCapture === attempt) currentCapture = null;
    }
    if (controller.signal.aborted) throw controller.signal.reason;
    if (bytes) {
      await report(item, "browser_running", "public_pdf_captured");
      const cache = await caches.open(CACHE);
      await ensureCacheRoom(cache, bytes.byteLength);
      await cache.put(cacheKey(item.itemId), new Response(bytes, { headers: { "Content-Type": "application/pdf" } }));
      await uploadCached(item);
      return;
    }
    await report(item, "browser_running", "public_pdf_fallback");
  }
  const url = landingPage(item.paper);
  if (!url) {
    await report(item, "failed", "no_article_url");
    return;
  }
  // Resume the exact tab where the user completed verification.
  if (Number.isInteger(tabId)) {
    try { await chrome.tabs.get(tabId); } catch { tabId = null; }
  }
  // Attachment navigation may hand the response to the download manager before
  // a page is ready. Attach the listener before the very first PDF navigation.
  const firstPdfNavigation = !Number.isInteger(tabId)
    && (directPdfUrl(url) || url === articleUrl(item.paper.links?.pdfUrl));
  if (!Number.isInteger(tabId)) tabId = (await chrome.tabs.create({ url: firstPdfNavigation ? "about:blank" : url, active: false })).id;
  await report(item, "browser_running", "article_opened", tabId);
  await chrome.storage.local.set({ active: { ...item, tabId } });
  await waitReady(tabId);
  const currentUrl = (await chrome.tabs.get(tabId)).url;
  let directPdf = firstPdfNavigation || directPdfUrl(currentUrl);
  const pdfTarget = firstPdfNavigation ? url : currentUrl;
  let pageProbeFailed = false;
  let snapshot;
  if (directPdf) snapshot = { pageUrl: pdfTarget, candidates: [{ url: pdfTarget }] };
  else {
    try {
      snapshot = await scan(tabId);
    } catch (error) {
      // Edge can reject script injection on a PDF viewer or a restricted
      // publisher frame. A supplied PDF candidate is still safe to try after
      // the response listener is attached; do not surface the browser's vague
      // "unknown error fetching the script" message to the user.
      const fallbackPdf = articleUrl(item.paper.links?.pdfUrl);
      if (!fallbackPdf) throw new Error("publisher_page_unavailable");
      pageProbeFailed = true;
      snapshot = { pageUrl: currentUrl, candidates: [{ url: fallbackPdf }], access: {} };
    }
  }
  if (snapshot.isPdf) {
    directPdf = true;
    snapshot.candidates = [{ url: currentUrl }];
  }
  const reason = blockedReason(snapshot);
  if (reason && !item.assisted) {
    await report(item, reason === "no_entitlement" ? "no_entitlement" : "waiting_user", reason, tabId);
    return;
  }
  const candidate = snapshot.candidates?.find(value => articleUrl(value.url));
  if (!candidate && !item.assisted) {
    await report(item, "waiting_user", "no_visible_pdf", tabId);
    return;
  }
  const target = articleUrl(candidate?.url) || articleUrl(snapshot.pageUrl);
  if (!target) throw new Error("文章页面地址不可用");
  const params = Object.fromEntries(new URL(target).searchParams);
  const session = await startNavigationPdfCapture(chrome, { initialTabId: tabId,
    expectedUrl: target, expectedQueryParams: params, expectedPii: snapshot.pii || null,
    maxBytes: item.maxBytes, timeoutMs: item.assisted ? 90000 : 45000, allowAnyHttpPdf: true,
    allowUnboundDocument: true, allowUnboundPdfResponse: true, failOnAccessDenied: !item.assisted,
    onDownloadCandidate: candidate => {
      void background(() => downloadBridge.register(item, candidate));
      void report(item, "browser_running", "browser_save_detected", tabId).catch(() => undefined);
    } });
  currentCapture = session;
  let stopProbe;
  try {
    captureNonce = crypto.randomUUID();
    if (!directPdf) stopProbe = await startBlobDownloadProbe(chrome, tabId, captureNonce);
    if (item.assisted) {
      await chrome.tabs.update(tabId, { active: true });
      await report(item, "browser_running", "manual_capture_active", tabId);
    } else if (firstPdfNavigation) {
      await report(item, "browser_running", "pdf_response_listening", tabId);
      await chrome.tabs.update(tabId, { url: target });
    } else if (directPdf) {
      await report(item, "browser_running", "pdf_response_listening", tabId);
      await chrome.tabs.reload(tabId);
    } else if (pageProbeFailed) {
      await report(item, "browser_running", "pdf_response_listening", tabId);
      await chrome.tabs.update(tabId, { url: target });
    } else {
      await report(item, "browser_running", "pdf_response_listening", tabId);
      const action = await chrome.tabs.sendMessage(tabId, {
        type: "NAVIGATE_PUBLISHER_PDF", url: target, actionId: candidate.actionId,
      });
      if (!action?.ok) throw new Error("PDF 入口不能自动打开");
    }
    const captured = await session.waitForPdf();
    await report(item, "browser_running", "pdf_captured", tabId);
    if (captured.bytes.byteLength > item.maxBytes) throw new Error("PDF 超过大小限制");
    const cache = await caches.open(CACHE);
    await ensureCacheRoom(cache, captured.bytes.byteLength);
    await cache.put(cacheKey(item.itemId), new Response(captured.bytes, { headers: {
      "Content-Type": "application/pdf", "Content-Length": String(captured.bytes.byteLength) } }));
    await uploadCached(item);
  } finally {
    captureNonce = null;
    try { await stopProbe?.(); }
    finally {
      try { await session.close(); }
      finally { if (currentCapture === session) currentCapture = null; }
    }
  }
}

async function poll() {
  if (!alertTimer) alertTimer = setInterval(() => void refreshTaskAlerts(), 10000);
  void refreshTaskAlerts();
  if (running) { wakePending = true; return; }
  running = true;
  let item, stopMonitor, drained = false;
  try {
    const config = await settings();
    if (!config.browserToken) return;
    await processOpenCommands();
    drained = await downloadBridge.drain();
    if (drained) return;
    ({ item } = await (await api("/v1/browser/claim", { method: "POST" })).json());
    if (!item) return;
    activeItemId = item.itemId;
    activeTaskId = item.taskId;
    stopMonitor = startTaskMonitor({ item, processCommands: processOpenCommands,
      readTasks: async () => (await api("/v1/browser/tasks")).json(),
      syncTasks: tasks => notifier.sync(tasks),
      renewLease: () => report(item, "browser_running", "lease_renewed"),
      stopCapture: reason => currentCapture?.close(reason) });
    try {
      await capture(item);
    } catch (error) {
      if (!["SAVED_DOWNLOAD_READY", "TASK_CAPTURE_STOPPED"].includes(error.message)) {
      // Never expose signed publisher URLs or retry challenges automatically.
      const safeError = String(error?.message || "browser_capture_failed").replace(/https?:\/\/\S+/g, "[URL]").slice(0, 300);
      await chrome.storage.local.set({ lastError: safeError });
      const reason = { LOCAL_STORAGE_FULL: "local_storage_full", BROWSER_CACHE_FULL: "browser_cache_full",
        PDF_PARSE_FAILED: "pdf_parse_failed", NOT_A_PDF: "not_a_pdf",
        publisher_page_unavailable: "publisher_page_unavailable" }[error?.message] || "browser_download_needs_user";
      await report(item, "waiting_user", reason, (await settings()).active?.tabId).catch(() => undefined);
      }
    }
    await chrome.storage.local.remove("active");
  } catch (error) {
    await chrome.storage.local.set({ lastError: error.message });
  } finally {
    stopMonitor?.();
    try { await currentCapture?.close(); }
    finally {
      currentCapture = null;
      running = false;
      activeItemId = null;
      activeTaskId = null;
    }
    await refreshTaskAlerts();
    // The companion keeps the queue blocked on the current human hand-off.
    if (item || drained || wakePending) {
      wakePending = false;
      queueMicrotask(wake);
    }
  }
}

async function importPdf(itemId, base64) {
  // The service can publish waiting_user just before this worker finishes its
  // capture cleanup. Let an immediate UI import wait for that same item only.
  const until = Date.now() + 3000;
  while (running && activeItemId === itemId && Date.now() < until) {
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  if (running) throw new Error("正在获取文献，请先暂停任务后导入");
  if (typeof base64 !== "string" || base64.length > Math.ceil(32 * 1024 * 1024 / 3) * 4) throw new Error("PDF 超过 32 MiB 限制");
  const binary = atob(base64);
  if (!binary.startsWith("%PDF-") || binary.length > 32 * 1024 * 1024) throw new Error("请选择有效 PDF（最多 32 MiB）");
  running = true;
  let item, heartbeat;
  try {
    ({ item } = await (await api(`/v1/browser/items/${itemId}/import`, { method: "POST" })).json());
    activeItemId = itemId;
    heartbeat = setInterval(() => void report(item, "browser_running", "lease_renewed").catch(() => undefined), 20000);
    const bytes = Uint8Array.from(binary, char => char.charCodeAt(0));
    await api(`/v1/browser/items/${itemId}/pdf`, { method: "PUT", lease: item.lease, body: new Blob([bytes], { type: "application/pdf" }) });
    return { ok: true };
  } catch (error) {
    if (item) await report(item, "waiting_user", "manual_import_failed", item.tabId).catch(() => undefined);
    throw error;
  } finally {
    clearInterval(heartbeat);
    running = false;
    activeItemId = null;
    setTimeout(wake, 250);
  }
}

async function initialize() {
  await chrome.storage.local.setAccessLevel({ accessLevel: "TRUSTED_CONTEXTS" });
  await chrome.alarms.create("litscout-poll", { periodInMinutes: 0.5 });
  await chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
  clearInterval(alertTimer);
  alertTimer = setInterval(() => void refreshTaskAlerts(), 10000);
  wake();
}
chrome.runtime.onInstalled.addListener(() => void background(initialize));
chrome.runtime.onStartup.addListener(() => void background(initialize));
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === "litscout-poll") wake(); });
chrome.notifications.onClicked.addListener(id => void notifier.open(id).catch(() => undefined));
chrome.notifications.onButtonClicked.addListener((id, index) => { if (index === 0) void notifier.open(id).catch(() => undefined); });

chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (message.type === "BOUND_BLOB_DOWNLOAD" && sender.id === chrome.runtime.id && sender.tab && captureNonce && message.nonce === captureNonce) {
    currentCapture?.observeDownloadUrl(message.url, sender.tab.id);
    return;
  }
  // Only extension-owned UI may pair, resume, approve or obtain task state.
  if (sender.id !== chrome.runtime.id || !sender.url?.startsWith(chrome.runtime.getURL(""))) return;
  (async () => {
    if (message.type === "PAIR") {
      const endpoint = localEndpoint(message.endpoint);
      const response = await fetch(endpoint + "/v1/pair", { method: "POST", credentials: "omit", redirect: "error",
        headers: { "Content-Type": "application/json", "X-LitScout-Extension": chrome.runtime.id },
        body: JSON.stringify({ code: message.code, name: message.name }), signal: AbortSignal.timeout(15000) });
      if (!response.ok) throw new Error("配对失败：检查本机服务和一次性配对码");
      const pair = await response.json();
      await chrome.storage.local.set({ endpoint, ...pair });
      await initialize();
      return { browserId: pair.browserId };
    }
    if (message.type === "POLL") { wake(); return { ok: true }; }
    if (message.type === "STATE") {
      const config = await settings();
      await processOpenCommands();
      const tasks = config.browserToken ? await (await api("/v1/browser/tasks")).json() : [];
      await notifier.sync(tasks);
      return { browserId: config.browserId, tasks, notifications: await notifier.status(), lastError: (await chrome.storage.local.get("lastError")).lastError };
    }
    if (message.type === "NOTIFICATION_SETTINGS") return notifier.configure(message.enabled);
    if (message.type === "TEST_NOTIFICATION") return notifier.test();
    if (message.type === "TASK_ACTION") {
      await api(`/v1/browser/tasks/${message.taskId}/action`, { method: "POST", json: { action: message.action, name: message.name || "" } });
      if (message.action === "pause" && activeTaskId === message.taskId) await currentCapture?.close(new Error("TASK_CAPTURE_STOPPED"));
      wake();
      return { ok: true };
    }
    if (message.type === "ACTION") {
      // An explicit reacquire must not replay the previously rejected bytes.
      if (message.action === "retry") await (await caches.open(CACHE)).delete(cacheKey(message.itemId));
      await api(`/v1/browser/items/${message.itemId}/action`, { method: "POST", json: { action: message.action } });
      if (message.action === "skip") await (await caches.open(CACHE)).delete(cacheKey(message.itemId));
      if (["pause", "skip"].includes(message.action) && activeItemId === message.itemId) await currentCapture?.close();
      wake();
      return { ok: true };
    }
    if (message.type === "IMPORT_PDF") return importPdf(message.itemId, message.base64);
    if (message.type === "PREVIEW") {
      const response = await api(`/v1/browser/items/${message.itemId}/preview`);
      return { base64: await blobBase64(await response.blob()) };
    }
    throw new Error("未知操作");
  })().then(data => reply({ ok: true, data })).catch(error => reply({ ok: false, error: error.message }))
    .catch(() => undefined); // The UI port may close during an extension reload.
  return true;
});

async function blobBase64(blob) {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 32768) binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
  return btoa(binary);
}
