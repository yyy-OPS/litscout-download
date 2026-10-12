export function parseSafeHttpUrl(value, { allowRelative = false } = {}) {
  const text = String(value || "").trim();
  if (!text) return null;
  if (allowRelative && text.startsWith("/") && !text.startsWith("//")) return text;
  let parsed;
  try {
    parsed = new URL(text);
  } catch {
    return null;
  }
  if (parsed.username || parsed.password) return null;
  if (!["http:", "https:"].includes(parsed.protocol)) return null;
  return parsed.toString();
}

