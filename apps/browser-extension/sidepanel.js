import { articleUrl, landingPage, pairingInput } from "./src/shared/protocol.js";

const $ = id => document.getElementById(id);
const terminal = new Set(["completed", "partial", "failed", "cancelled"]);
const states = { needs_browser: "等待浏览器获取",
  browser_running: "浏览器获取中", uploading: "PDF 已取得 · 正在保存核验", waiting_user: "等待人工协助",
  verification_required: "本机已保存 · 文献身份待确认", completed: "本机已保存 · 等待 Agent 导出", failed: "获取失败",
  no_entitlement: "当前无全文权限", cancelled: "已跳过" };
const taskStates = { running: "获取中", paused: "已手动暂停", waiting_user: "队列已暂停 · 等待人工", completed: "已完成", partial: "部分完成", failed: "未完成", cancelled: "已取消" };
const reasons = { article_opened: "识别当前文章的 PDF 入口", pdf_response_listening: "正在捕获 PDF 响应，最多 45 秒",
  public_pdf_attempt: "正在尝试公开 PDF 直链，最多 20 秒",
  public_pdf_captured: "公开 PDF 已获取，正在保存并核验身份",
  public_pdf_fallback: "公开尝试未成功，转入 DOI 或文章页获取",
  publisher_page_unavailable: "当前页面禁止脚本注入，已改用 PDF 网络响应监听",
  pdf_captured: "已捕获 PDF，正在回传本机", browser_save_detected: "检测到下载动作，尚未捕获完整 PDF；仍在监听，保存后回传仅作备用",
  browser_download_needs_user: "请完成验证后继续，或监听手动下载", manual_capture_active: "监听中，请在原文章页点击 PDF（90 秒）",
  publisher_verification: "请在原文章页完成人机验证", login_required: "请在原文章页完成登录",
  no_visible_pdf: "未识别到 PDF 入口，请手动下载", task_paused: "任务已暂停，继续后从此篇恢复",
  user_paused: "当前篇已暂停", lease_expired: "连接中断，请从此篇继续", identity_requires_user: "请预览确认，Agent 暂不可读取",
  identity_mismatch: "PDF 题名和 DOI 与目标文献不匹配，不能直接确认。请重新获取或导入正确 PDF，也可跳过此篇",
  saved_download_received: "浏览器保存文件已接收，正在核验", saved_download_import_failed: "自动回传失败，可选择已保存 PDF 导入" };
let tasks = [], selectedId = null, filter = "pending", snapshot = "", refreshing = false, working = 0, settingsOpen = false, importId = null;
let pendingFocusId = null;

async function send(message) {
  const response = await chrome.runtime.sendMessage(message);
  if (!response?.ok) throw new Error(response?.error || "本机服务暂不可用");
  return response.data;
}
async function operate(fn) {
  working++;
  try { await fn(); $("notice").textContent = ""; }
  catch (error) { $("notice").textContent = error.message; }
  finally { working--; snapshot = ""; await refresh(); }
}
const current = () => tasks.find(task => task.taskId === selectedId);

async function openArticle(item) {
  if (Number.isInteger(item.tab)) {
    try { await chrome.tabs.update(item.tab, { active: true }); return; } catch { /* Restore a closed article tab. */ }
  }
  const url = articleUrl(item.articleUrl) || landingPage({ links: { doiUrl: item.identifiers?.doi ? `https://doi.org/${encodeURIComponent(item.identifiers.doi)}` : null } });
  if (!url) throw new Error("文章链接不可用");
  await chrome.tabs.create({ url, active: true });
}
function action(label, type, item) {
  const button = document.createElement("button");
  button.type = "button"; button.textContent = label; button.dataset.action = type;
  button.addEventListener("click", () => {
    if (type === "import") { importId = item.id; $("pdfFile").value = ""; $("pdfFile").click(); return; }
    void operate(async () => {
      if (type === "tab") return openArticle(item);
      if (type === "copy-export") return navigator.clipboard.writeText(item.exportPath);
      if (type === "preview") { await chrome.tabs.create({ url: chrome.runtime.getURL(`preview.html#${item.id}`) }); return; }
      if (type === "approve" && !confirm("已预览并确认这是当前文献？确认后 Agent 可以读取。")) return;
      await send({ type: "ACTION", itemId: item.id, action: type });
    });
  });
  return button;
}
function renderPapers(task) {
  const visible = task.items.filter(item => filter === "all" || filter === "downloaded" && item.fileAvailable || filter === "pending" && !["completed", "cancelled", "failed", "no_entitlement"].includes(item.state));
  const fragment = document.createDocumentFragment();
  for (const item of visible) {
    const card = document.createElement("article"); card.dataset.state = item.state; card.dataset.itemId = item.id;
    const number = document.createElement("span"); number.className = "paper-number"; number.textContent = `PAPER ${String(task.items.indexOf(item) + 1).padStart(2, "0")}`;
    const title = document.createElement("h2"); title.textContent = item.title;
    const identity = document.createElement(item.identifiers?.doi ? "a" : "span"); identity.className = "identity";
    identity.textContent = item.identifiers?.doi ? `DOI ${item.identifiers.doi}` : item.identifiers?.arxivId ? `arXiv ${item.identifiers.arxivId}` : "未提供 DOI";
    if (item.identifiers?.doi) { identity.href = `https://doi.org/${encodeURIComponent(item.identifiers.doi)}`; identity.target = "_blank"; identity.rel = "noopener noreferrer"; }
    const mismatched = item.state === "verification_required" && item.verification === "mismatch";
    const state = document.createElement("div"); state.className = "state"; state.textContent = mismatched ? "本机已保存 · 文献身份不匹配" : item.exportStatus === "exported" ? "已导出到工作目录 · Agent 可读" : item.downloadStatus === "captured" && item.state === "browser_running" ? "PDF 已取得 · 正在回传" : states[item.state] || item.state;
    const detail = document.createElement("div"); detail.className = "state-detail";
    detail.textContent = item.exportPath || (mismatched ? reasons.identity_mismatch : reasons[item.reason] || item.reason || (item.state === "completed" ? "身份已核验，Agent 可读取正文并导出到指定目录" : ""));
    detail.style.overflowWrap = "anywhere";
    const buttons = document.createElement("div"); buttons.className = "actions";
    if (!["completed", "cancelled"].includes(item.state)) buttons.append(action("打开文章", "tab", item));
    if (item.state === "waiting_user") buttons.append(action("验证完成，继续", "resume", item), action("监听手动下载", "capture", item));
    if (["waiting_user", "verification_required", "cancelled"].includes(item.state)) buttons.append(action("重新获取 PDF", "retry", item));
    if (item.state === "verification_required" || ["waiting_user", "needs_browser", "cancelled"].includes(item.state) && (!item.fileAvailable || !["verified", "user_approved"].includes(item.verification))) buttons.append(action(item.fileAvailable ? "重新导入正确 PDF" : "导入已下载 PDF", "import", item));
    if (item.fileAvailable) buttons.append(action("预览 PDF", "preview", item));
    if (item.exportPath) buttons.append(action("复制文件路径", "copy-export", item));
    if (item.state === "verification_required" && item.verification !== "mismatch") buttons.append(action("确认是当前文献", "approve", item));
    if (!["completed", "cancelled", "failed", "no_entitlement", "uploading"].includes(item.state)) buttons.append(action("跳过此篇", "skip", item));
    card.append(number, title, identity, state, detail, buttons); fragment.append(card);
  }
  if (!visible.length) { const empty = document.createElement("p"); empty.className = "list-empty"; empty.textContent = filter === "pending" ? "当前任务没有待处理文献。切换“已下载”查看全文。" : "这个分类暂时没有文献。"; fragment.append(empty); }
  $("papers").replaceChildren(fragment);
}
function render() {
  const scroll = document.scrollingElement.scrollTop;
  const focused = document.activeElement;
  const focusedId = focused?.id;
  const focusedItem = focused?.closest("article")?.dataset.itemId;
  const focusedAction = focused?.dataset.action;
  const task = current();
  $("emptyState").hidden = Boolean(tasks.length); $("taskWorkspace").hidden = !task;
  $("taskSelect").replaceChildren(...tasks.map(value => { const option = document.createElement("option"); option.value = value.taskId; option.textContent = `${value.taskName} · ${taskStates[value.status] || value.status}`; return option; }));
  $("taskSelect").value = selectedId || "";
  $("taskSelect").hidden = !tasks.length;
  if (task) {
    const head = [...tasks].sort((a, b) => a.createdAt - b.createdAt).find(value => !terminal.has(value.status));
    const waitingForOther = head && head.taskId !== task.taskId && !terminal.has(task.status);
    $("queueBlocker").hidden = !waitingForOther;
    $("showQueueHead").dataset.taskId = head?.taskId || "";
    $("queueBlockerText").textContent = waitingForOther ? `当前先处理“${head.taskName}”。${head.paused || head.attention ? "请处理该任务的暂停或人工待办，完成或跳过后才会继续本任务。" : "本任务等待前面的文献处理完成。"}` : "";
    $("taskName").textContent = task.taskName; $("taskState").textContent = taskStates[task.status] || task.status;
    $("downloadedCount").textContent = task.progress.downloaded; $("totalCount").textContent = task.progress.total;
    $("readableCount").textContent = `${task.progress.readable} 篇可读 · ${task.progress.exported || 0} 篇已导出`;
    $("taskProgress").max = task.progress.total || 1; $("taskProgress").value = task.progress.downloaded;
    $("toggleTask").textContent = task.paused ? "继续任务 ▶" : "暂停任务 Ⅱ"; $("toggleTask").disabled = terminal.has(task.status);
    $("attention").hidden = !task.attention && !task.paused;
    $("attentionText").textContent = task.paused ? "你已暂停此任务。当前正在保存的文件会完成核验，后续文献等待你继续。" : task.attention ? `${task.attention.title}：${reasons[task.attention.reason] || "请检查对应文献的操作按钮"}。成功或跳过后继续下一篇。` : "";
    $("listIdentity").textContent = `清单 ID：${task.listId}`;
    $("paperListJson").textContent = JSON.stringify(paperList(task), null, 2);
    renderPapers(task);
  }
  const others = tasks.filter(value => value.taskId !== selectedId);
  $("otherTasks").hidden = !others.length; $("otherCount").textContent = `(${others.length})`;
  $("taskArchiveList").replaceChildren(...others.map(value => {
    const button = document.createElement("button"); button.type = "button"; button.className = "archive-item";
    const name = document.createElement("span"); name.textContent = value.taskName;
    const count = document.createElement("small"); count.textContent = `${value.progress.downloaded}/${value.progress.total}`;
    button.append(name, count); button.addEventListener("click", () => selectTask(value.taskId)); return button;
  }));
  const restored = focusedItem && focusedAction ? document.querySelector(`article[data-item-id="${CSS.escape(focusedItem)}"] button[data-action="${CSS.escape(focusedAction)}"]`) : focusedId ? $(focusedId) : null;
  if (restored && !restored.disabled) restored.focus({ preventScroll: true });
  document.scrollingElement.scrollTop = scroll;
  if (pendingFocusId) {
    const paper = document.querySelector(`article[data-item-id="${CSS.escape(pendingFocusId)}"]`);
    if (paper) { paper.scrollIntoView({ block: "center" }); pendingFocusId = null; }
  }
}
async function selectTask(id) { selectedId = id; await chrome.storage.local.set({ selectedTaskId: id }); render(); }
async function refresh() {
  if (refreshing || working) return;
  refreshing = true;
  try {
    const data = await send({ type: "STATE" });
    $("connection").textContent = data.browserId ? "本机已连接" : "未配对"; $("connection").classList.toggle("online", Boolean(data.browserId));
    $("pairPanel").hidden = Boolean(data.browserId) && !settingsOpen; $("tasksPanel").hidden = !data.browserId || settingsOpen;
    $("closeSettings").hidden = !data.browserId; $("browserId").textContent = data.browserId || "";
    const notification = data.notifications || {};
    $("desktopNotifications").checked = notification.enabled !== false;
    $("notificationStatus").textContent = notification.error || (notification.enabled === false ? "桌面提醒已关闭；角标和侧栏继续显示待办。" : notification.permission !== "granted" ? "浏览器通知未授权，请允许 Edge 发送通知；角标仍显示待办。" : "接收任务和人工待办时提醒；未处理待办每 5 分钟再提醒。系统免打扰可能隐藏弹窗。");
    $("notificationStatus").classList.toggle("warning", Boolean(notification.error) || notification.enabled !== false && notification.permission !== "granted");
    const fingerprint = JSON.stringify(data.tasks);
    if (fingerprint !== snapshot) {
      tasks = data.tasks;
      if (!tasks.some(task => task.taskId === selectedId)) selectedId = tasks[0]?.taskId || null;
      snapshot = fingerprint; render();
    }
  } catch (error) { $("connection").textContent = "本机服务离线"; $("connection").classList.remove("online"); $("notice").textContent = error.message; }
  finally { refreshing = false; }
}
$("taskSelect").addEventListener("change", event => void selectTask(event.target.value));
$("showQueueHead").addEventListener("click", () => void selectTask($("showQueueHead").dataset.taskId));
document.querySelectorAll("[data-filter]").forEach(button => button.addEventListener("click", () => {
  filter = button.dataset.filter;
  document.querySelectorAll("[data-filter]").forEach(node => node.setAttribute("aria-pressed", String(node === button)));
  void chrome.storage.local.set({ paperFilter: filter }); render();
}));
$("toggleTask").addEventListener("click", () => void operate(async () => {
  const task = current(); if (task) await send({ type: "TASK_ACTION", taskId: task.taskId, action: task.paused ? "resume" : "pause" });
}));
$("renameTask").addEventListener("click", () => { $("renameInput").value = current().taskName; $("renameDialog").showModal(); });
$("cancelRename").addEventListener("click", () => $("renameDialog").close());
$("renameForm").addEventListener("submit", event => { event.preventDefault(); void operate(async () => { await send({ type: "TASK_ACTION", taskId: selectedId, action: "rename", name: $("renameInput").value.trim() }); $("renameDialog").close(); }); });
$("copyTaskId").addEventListener("click", () => void operate(async () => { await navigator.clipboard.writeText(selectedId); }));
$("pairForm").addEventListener("submit", event => { event.preventDefault(); void operate(async () => {
  const pair = pairingInput($("code").value, $("endpoint").value);
  await send({ type: "PAIR", ...pair, name: $("name").value }); $("endpoint").value = pair.endpoint; $("code").value = ""; settingsOpen = false;
}); });
$("reconnect").addEventListener("click", () => { settingsOpen = true; void refresh(); });
$("closeSettings").addEventListener("click", () => { settingsOpen = false; void refresh(); });
$("refresh").addEventListener("click", () => void operate(async () => { await send({ type: "POLL" }); }));
$("desktopNotifications").addEventListener("change", event => void operate(async () => { await send({ type: "NOTIFICATION_SETTINGS", enabled: event.target.checked }); }));
$("testNotification").addEventListener("click", () => void operate(async () => { await send({ type: "TEST_NOTIFICATION" }); }));
$("pdfFile").addEventListener("change", () => void operate(async () => {
  const file = $("pdfFile").files[0], itemId = importId;
  if (!file || !itemId) return;
  if (file.size > 32 * 1024 * 1024) throw new Error("PDF 超过 32 MiB 限制");
  const bytes = new Uint8Array(await file.arrayBuffer());
  if (new TextDecoder().decode(bytes.slice(0, 5)) !== "%PDF-") throw new Error("所选文件不是 PDF");
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 32768) binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
  $("notice").textContent = "正在导入文件并核验文献身份…";
  await send({ type: "IMPORT_PDF", itemId, base64: btoa(binary) });
}));
const preferences = await chrome.storage.local.get(["selectedTaskId", "paperFilter", "focusItemId"]);
selectedId = preferences.selectedTaskId;
pendingFocusId = preferences.focusItemId || null;
if (["pending", "downloaded", "all"].includes(preferences.paperFilter)) {
  filter = preferences.paperFilter;
  document.querySelectorAll("[data-filter]").forEach(node => node.setAttribute("aria-pressed", String(node.dataset.filter === filter)));
}
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local") return;
  if (!changes.selectedTaskId && !changes.paperFilter && !changes.focusItemId) return;
  if (changes.selectedTaskId) selectedId = changes.selectedTaskId.newValue;
  if (changes.paperFilter && ["pending", "downloaded", "all"].includes(changes.paperFilter.newValue)) {
    filter = changes.paperFilter.newValue;
    document.querySelectorAll("[data-filter]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.filter === filter)));
  }
  if (changes.focusItemId) pendingFocusId = changes.focusItemId.newValue || null;
  snapshot = "";
  void refresh();
});
void refresh(); setInterval(() => { if (!document.hidden && !$("renameDialog").open) void refresh(); }, 1000);

function paperList(task) {
  return {schemaVersion: 1, listId: task.listId, ...(task.cloudJobId ? {cloudJobId: task.cloudJobId} : {}), papers: task.items.map(item => item.paperRecord)};
}
$("copyPaperList").addEventListener("click", () => void operate(async () => {
  await navigator.clipboard.writeText(JSON.stringify(paperList(current()), null, 2));
}));
