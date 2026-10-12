// Only a user notification click focuses a task or opens its workbench.
export async function openTaskFromNotification(chrome, target) {
  if (target) await chrome.storage.local.set({ selectedTaskId: target.taskId, paperFilter: "all", focusItemId: target.itemId });
  let windowId;
  if (Number.isInteger(target?.tabId)) {
    try { windowId = (await chrome.tabs.update(target.tabId, { active: true })).windowId; }
    catch { /* The original article tab may have been closed. */ }
  }
  if (!Number.isInteger(windowId)) {
    try { windowId = (await chrome.windows.getLastFocused({ windowTypes: ["normal"] })).id; }
    catch { /* The browser may be running without a normal window. */ }
  }
  const url = chrome.runtime.getURL("index.html");
  if (!Number.isInteger(windowId)) {
    await chrome.windows.create({ url, focused: true });
    return;
  }
  await chrome.windows.update(windowId, { focused: true });
  try { await chrome.sidePanel.open({ windowId }); }
  catch {
    // Some browser builds restrict sidePanel.open for notification gestures.
    await chrome.tabs.create({ windowId, active: true, url });
  }
}
