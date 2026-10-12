// Task discovery and human hand-off alerts, independent of the side panel.
const TERMINAL = new Set(["completed", "partial", "failed", "cancelled"]);
const REMIND_MS = 5 * 60 * 1000;
const text = (value, limit = 160) => String(value || "").replace(/[\x00-\x1f]/g, " ").slice(0, limit);
const reasons = {
  publisher_verification: "请在原文章页面完成人机验证，再继续当前篇。",
  login_required: "请在原文章页面登录，再继续当前篇。",
  identity_requires_user: "PDF 已保存在本机，请预览并确认文献身份。",
  identity_mismatch: "捕获文件与目标文献不符，请重新获取或导入正确 PDF。",
  no_visible_pdf: "请打开文章，监听手动下载或导入已下载 PDF。",
};

export function createTaskNotifier(chrome, { openTask, now = () => Date.now() } = {}) {
  let serial = Promise.resolve();
  let lastError = null;
  let badgeSnapshot = "";
  const enqueue = operation => {
    const result = serial.then(operation);
    serial = result.catch(() => undefined);
    return result;
  };

  async function status() {
    const { desktopNotifications } = await chrome.storage.local.get("desktopNotifications");
    let permission = "unavailable";
    try { permission = await chrome.notifications?.getPermissionLevel(); }
    catch { /* Badge and in-panel attention remain available. */ }
    return { enabled: desktopNotifications !== false, permission: permission || "unavailable", error: lastError };
  }

  async function show(id, title, message, interactive = false) {
    try {
      await chrome.notifications.create(id, { type: "basic", iconUrl: chrome.runtime.getURL("assets/notification-icon.png"),
        title, message, priority: interactive ? 2 : 0, requireInteraction: interactive,
        buttons: [{ title: "打开任务" }] });
      lastError = null;
      return true;
    } catch {
      lastError = "桌面提醒创建失败，请检查 Edge 和系统通知设置。";
      return false;
    }
  }

  async function syncTasks(tasks) {
    const settings = await status();
    const { taskNotificationState: stored, taskNotificationStatus: oldStatus } = await chrome.storage.local.get(["taskNotificationState", "taskNotificationStatus"]);
    const previous = stored?.version === 1 ? stored : { version: 1, initialized: false, records: {}, targets: {} };
    const current = { version: 1, initialized: true, records: {}, targets: {} };
    const time = now();
    const canNotify = settings.enabled && settings.permission === "granted";
    const active = tasks.filter(task => !TERMINAL.has(task.status)).sort((a, b) => a.createdAt - b.createdAt);
    const head = active[0];
    const attention = head?.attention && !head.paused ? head : null;
    const pending = tasks.filter(task => task.attention || task.paused).length;
    const badge = `${pending}:${active.length}`;
    if (badge !== badgeSnapshot) {
      await chrome.action.setBadgeText({ text: pending ? String(pending) : active.length ? String(active.length) : "" });
      await chrome.action.setBadgeBackgroundColor({ color: pending ? "#e6bc75" : "#bcf66c" });
      await chrome.action.setTitle({ title: pending ? `LitScout：${pending} 个任务等待人工处理` : active.length ? `LitScout：${active.length} 个全文任务进行中` : "打开 LitScout 文献侧边栏" });
      badgeSnapshot = badge;
    }

    const incoming = [];
    for (const task of tasks) {
      const old = previous.records[task.taskId];
      const recent = task.createdAt * 1000 >= time - 10 * 60 * 1000;
      if (!old && (previous.initialized || recent)) incoming.push(task);
      const key = attention?.taskId === task.taskId ? `${task.attention.itemId}:${task.attention.reason}` : "";
      const entry = { attentionKey: key, remindedAt: old?.remindedAt || 0 };
      const id = "litscout-attention-" + task.taskId;
      if (key) {
        current.targets[id] = { taskId: task.taskId, itemId: task.attention.itemId, tabId: task.attention.tabId };
        if (canNotify && (old?.attentionKey !== key || !entry.remindedAt || time - entry.remindedAt >= REMIND_MS)) {
          const title = incoming.some(value => value.taskId === task.taskId) ? "LitScout：任务已接收 · 需要你协助" : "LitScout：当前文献需要你协助";
          const message = `${text(task.taskName, 80)}\n${text(task.attention.title)}\n${reasons[task.attention.reason] || "请检查当前篇的操作按钮；完成或跳过后继续下一篇。"}`;
          if (await show(id, title, message, true)) entry.remindedAt = time;
        }
      } else if (old?.attentionKey) {
        await chrome.notifications?.clear(id).catch(() => undefined);
      }
      const receivedId = "litscout-received-" + task.taskId;
      if (previous.targets[receivedId]) current.targets[receivedId] = previous.targets[receivedId];
      current.records[task.taskId] = entry;
    }
    // One receipt per observed batch; an immediate hand-off already includes receipt.
    const received = incoming.filter(task => task.taskId !== attention?.taskId);
    if (canNotify && received.length) {
      const task = received[0];
      const id = "litscout-received-" + task.taskId;
      current.targets[id] = { taskId: task.taskId, itemId: null, tabId: null };
      const title = received.length === 1 ? "LitScout：已接收 Agent 全文任务" : `LitScout：已接收 ${received.length} 个全文任务`;
      await show(id, title, `${text(task.taskName, 100)}\n${task.progress?.total || task.items?.length || 0} 篇文献已进入本机队列。点击查看任务和待办。`);
    }
    const result = { ...settings, error: lastError };
    const updates = {};
    if (JSON.stringify(current) !== JSON.stringify(previous)) updates.taskNotificationState = current;
    if (JSON.stringify(result) !== JSON.stringify(oldStatus)) updates.taskNotificationStatus = result;
    if (Object.keys(updates).length) await chrome.storage.local.set(updates);
    return result;
  }

  return {
    sync: tasks => enqueue(() => syncTasks(tasks)),
    status,
    configure: enabled => enqueue(async () => {
      if (typeof enabled !== "boolean") throw new Error("提醒开关参数无效");
      await chrome.storage.local.set({ desktopNotifications: enabled });
      if (enabled) {
        const { taskNotificationState } = await chrome.storage.local.get("taskNotificationState");
        if (taskNotificationState?.version === 1) {
          for (const entry of Object.values(taskNotificationState.records)) entry.remindedAt = 0;
          await chrome.storage.local.set({ taskNotificationState });
        }
      }
      if (!enabled) {
        const { taskNotificationState } = await chrome.storage.local.get("taskNotificationState");
        for (const id of Object.keys(taskNotificationState?.targets || {})) await chrome.notifications?.clear(id).catch(() => undefined);
      }
      return status();
    }),
    async test() {
      const settings = await status();
      if (settings.permission !== "granted") throw new Error("浏览器通知未授权，请允许 Edge 发送通知。角标和侧栏仍可查看待办。");
      if (!settings.enabled) throw new Error("请先开启桌面提醒。");
      if (!await show("litscout-test", "LitScout：提醒已开启", "新任务和人工待办会在这里提醒。若看不到弹窗，请检查系统免打扰与 Edge 通知设置。")) throw new Error(lastError);
      return { ok: true };
    },
    async open(id) {
      const { taskNotificationState } = await chrome.storage.local.get("taskNotificationState");
      const target = taskNotificationState?.targets?.[id];
      if (!target && id !== "litscout-test") return false;
      await openTask(target || null);
      await chrome.notifications.clear(id).catch(() => undefined);
      return true;
    },
  };
}
