const item = location.hash.slice(1);
let objectUrl;
try {
  const result = await chrome.runtime.sendMessage({ type: "PREVIEW", itemId: item });
  if (!result.ok) throw new Error(result.error);
  const bytes = Uint8Array.from(atob(result.data.base64), char => char.charCodeAt(0));
  objectUrl = URL.createObjectURL(new Blob([bytes], { type: "application/pdf" }));
  document.getElementById("pdf").src = objectUrl;
} catch (error) { document.getElementById("status").textContent = error.message; }
addEventListener("unload", () => { if (objectUrl) URL.revokeObjectURL(objectUrl); });
