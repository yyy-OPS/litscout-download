export async function startBlobDownloadProbe(chromeApi, tabId, nonce) {
  await chromeApi.scripting.executeScript({ target: { tabId }, files: ["src/content/download-probe.js"] });
  await chromeApi.tabs.sendMessage(tabId, { type: "DOWNLOAD_PROBE_START", nonce });
  await chromeApi.scripting.executeScript({ target: { tabId }, world: "MAIN", args: [nonce], func: nonce => {
    const key = "__litscoutBoundBlobHook";
    globalThis[key]?.restore?.();
    const create = URL.createObjectURL, click = HTMLAnchorElement.prototype.click;
    const notify = url => window.postMessage({ type: "LITSCOUT_BOUND_BLOB", nonce, url }, location.origin);
    function createHook(blob) {
      const url = Reflect.apply(create, this, arguments);
      if (blob instanceof Blob && /application\/pdf|application\/octet-stream/i.test(blob.type)) notify(url);
      return url;
    }
    function clickHook() {
      if (this.href?.startsWith("blob:") && /\.pdf$/i.test(this.download || "")) notify(this.href);
      return Reflect.apply(click, this, arguments);
    }
    URL.createObjectURL = createHook; HTMLAnchorElement.prototype.click = clickHook;
    globalThis[key] = { restore() {
      if (URL.createObjectURL === createHook) URL.createObjectURL = create;
      if (HTMLAnchorElement.prototype.click === clickHook) HTMLAnchorElement.prototype.click = click;
      delete globalThis[key];
    } };
  } });
  return async () => {
    await chromeApi.tabs.sendMessage(tabId, { type: "DOWNLOAD_PROBE_STOP" }).catch(() => undefined);
    await chromeApi.scripting.executeScript({ target: { tabId }, world: "MAIN", func: () => globalThis.__litscoutBoundBlobHook?.restore?.() }).catch(() => undefined);
  };
}
