// Downloads events carry metadata, not bytes. The companion reads only PDFs
// within its dedicated Downloads directory after the browser finishes saving.
export function createSavedDownloadBridge(chromeApi, { api, wake, stopCapture,
  background = operation => Promise.resolve().then(operation).catch(() => undefined) }) {
  let mutation = Promise.resolve();
  const edit = fn => {
    mutation = mutation.catch(() => undefined).then(async () => {
      const { pendingDownloads = {} } = await chromeApi.storage.local.get("pendingDownloads");
      await fn(pendingDownloads);
      await chromeApi.storage.local.set({ pendingDownloads });
    });
    return mutation.catch(() => undefined);
  };
  async function completed(downloadId) {
    await mutation;
    const { pendingDownloads = {} } = await chromeApi.storage.local.get("pendingDownloads");
    const entry = pendingDownloads[downloadId];
    if (!entry) return;
    await stopCapture(entry.itemId);
    wake();
  }
  chromeApi.downloads.onChanged.addListener(change => {
    if (change.state?.current === "complete") void background(() => completed(change.id));
    if (change.state?.current === "interrupted") void edit(entries => { delete entries[change.id]; });
  });
  return {
    async register(item, candidate) {
      if (!Number.isSafeInteger(candidate.downloadId) || candidate.downloadId < 0) return;
      await edit(entries => {
        for (const [key, value] of Object.entries(entries)) if (Date.now() - value.created > 86400000) delete entries[key];
        if (Object.keys(entries).length < 100) entries[candidate.downloadId] = {
          itemId: item.itemId, taskId: item.taskId, lease: item.lease, created: Date.now(),
        };
      });
      const [download] = await chromeApi.downloads.search({ id: candidate.downloadId });
      if (download?.state === "complete") await completed(candidate.downloadId);
    },
    async drain() {
      await mutation;
      const { pendingDownloads = {} } = await chromeApi.storage.local.get("pendingDownloads");
      if (!Object.keys(pendingDownloads).length) return false;
      const tasks = await (await api("/v1/browser/tasks")).json();
      for (const [id, entry] of Object.entries(pendingDownloads)) {
        const item = tasks.find(task => task.taskId === entry.taskId)?.items.find(value => value.id === entry.itemId);
        if (!item || item.fileAvailable || ["completed", "cancelled", "failed", "no_entitlement"].includes(item.state)) {
          await edit(entries => { delete entries[id]; });
          continue;
        }
        const [download] = await chromeApi.downloads.search({ id: Number(id) });
        if (!download || download.state === "interrupted") {
          await edit(entries => { delete entries[id]; });
          continue;
        }
        if (download.state !== "complete") continue;
        try {
          await api(`/v1/browser/items/${entry.itemId}/download`, { method: "POST", lease: entry.lease, timeoutMs: 90000,
            json: { downloadId: Number(id), filename: download.filename } });
        } catch (error) {
          // A directory outside the allowed root requires explicit file selection.
          await chromeApi.storage.local.set({ lastError: String(error.message).replace(/https?:\/\/\S+/g, "[URL]").slice(0, 200) });
          if (item.state === "browser_running") {
            await api(`/v1/browser/items/${entry.itemId}/report`, { method: "POST", lease: entry.lease,
              json: { state: "waiting_user", reason: "saved_download_import_failed", tabId: item.tab } }).catch(() => undefined);
          }
        }
        await edit(entries => { delete entries[id]; });
        return true;
      }
      return false;
    },
  };
}
