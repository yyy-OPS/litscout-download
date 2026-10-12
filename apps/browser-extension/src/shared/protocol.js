export const CACHE = "litscout-download-v1";

export function localEndpoint(value) {
  const url = new URL(value);
  if (url.protocol !== "http:" || url.hostname !== "127.0.0.1" || url.username || url.password
      || !url.port || Number(url.port) < 1024 || Number(url.port) > 65535
      || url.search || url.hash || url.pathname !== "/") throw new Error("本机地址必须为 http://127.0.0.1:端口，端口范围为 1024–65535");
  return url.origin;
}

export function pairingInput(code, endpoint) {
  const value = String(code || "").trim();
  if (!value.startsWith("litscout-download:")) return { code: value, endpoint: localEndpoint(endpoint) };
  const match = /^litscout-download:v1:(\d{4,5}):([A-Za-z0-9_-]{20,80})$/.exec(value);
  if (!match || Number(match[1]) < 1024 || Number(match[1]) > 65535) throw new Error("连接码格式无效，请让 Agent 重新生成");
  return { code: match[2], endpoint: localEndpoint(`http://127.0.0.1:${match[1]}`) };
}

export function articleUrl(value) {
  try {
    const url = new URL(value);
    const host = url.hostname.replace(/^\[|\]$/g, "").toLowerCase();
    if (url.protocol !== "https:" || url.username || url.password || (url.port && url.port !== "443")) return null;
    if (host.includes(":") || /^\d+\./.test(host) || /(^|\.)(localhost|local|internal)$/.test(host)) return null;
    return url.href;
  } catch { return null; }
}

export function landingPage(paper) {
  return [paper.links?.doiUrl, paper.links?.landingPage, paper.links?.arxivUrl,
    ...Object.values(paper.links?.sourceUrls || {}), paper.links?.pdfUrl].map(articleUrl).find(Boolean) || null;
}

export function directPdfUrl(value) {
  const safe = articleUrl(value);
  if (!safe) return false;
  const url = new URL(safe);
  return /\.pdf$/i.test(url.pathname)
    || (url.hostname === "arxiv.org" && /^\/pdf\/(?:\d{4}\.\d{4,5}(?:v\d+)?|[a-z-]+(?:\.[A-Z]{2})?\/\d{7}(?:v\d+)?)$/i.test(url.pathname));
}

export function blockedReason(snapshot) {
  if (snapshot?.access?.captcha) return "publisher_verification";
  if (snapshot?.access?.noEntitlement && !snapshot.candidates?.length) return "no_entitlement";
  if (snapshot?.access?.loginRequired && !snapshot.candidates?.length) return "login_required";
  return null;
}

export function cacheKey(itemId) {
  if (!/^[a-f0-9-]{36}$/.test(itemId)) throw new Error("文献标识无效");
  return `https://cache.litscout.invalid/${itemId}.pdf`;
}
