(() => {
  if (globalThis.__litscoutDownloadProbe) return;
  const probe = globalThis.__litscoutDownloadProbe = { nonce: null };
  const notify = url => {
    if (probe.nonce && typeof url === "string" && url.startsWith("blob:")) {
      void chrome.runtime.sendMessage({ type: "BOUND_BLOB_DOWNLOAD", nonce: probe.nonce, url }).catch(() => undefined);
    }
  };
  window.addEventListener("message", event => {
    if (event.source === window && event.data?.type === "LITSCOUT_BOUND_BLOB" && event.data.nonce === probe.nonce) notify(event.data.url);
  });
  document.addEventListener("click", event => {
    const anchor = event.target?.closest?.("a[href]");
    if (anchor && /\.pdf$/i.test(anchor.download || "")) notify(anchor.href);
  }, true);
  chrome.runtime.onMessage.addListener((message, _sender, reply) => {
    if (message.type === "DOWNLOAD_PROBE_START") { probe.nonce = message.nonce; reply({ ok: true }); }
    if (message.type === "DOWNLOAD_PROBE_STOP") { probe.nonce = null; reply({ ok: true }); }
  });
})();
