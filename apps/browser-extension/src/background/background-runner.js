// Browser lifecycle failures cannot be reported through an invalid worker context.
const messageOf = error => String(error?.message || error || "background_failed")
  .replace(/https?:\/\/\S+/g, "[URL]").slice(0, 300);
const unavailable = error => /^(?:No SW|Extension context invalidated\.?|The extension context was invalidated\.?)$/i
  .test(String(error?.message || error));

export function createBackgroundRunner({ recordError, logError = message => console.error(message) }) {
  return async operation => {
    try { return await operation(); }
    catch (error) {
      if (unavailable(error)) return;
      const message = messageOf(error);
      try { await recordError(message); }
      catch (reportError) {
        // Keep the original failure visible without leaking credentials or
        // creating a second unhandled rejection while the worker is gone.
        if (!unavailable(reportError)) {
          try { logError(message); } catch { /* Logging is best effort. */ }
        }
      }
    }
  };
}
