// A stopped task must not act on a later capture after an in-flight request ends.
export function startTaskMonitor({ item, processCommands, readTasks, syncTasks, renewLease, stopCapture,
  setTimer = setInterval, clearTimer = clearInterval }) {
  let stopped = false;
  let monitoring = false;
  let renewing = false;
  const monitor = setTimer(async () => {
    if (stopped || monitoring) return;
    monitoring = true;
    try {
      await processCommands();
      if (stopped) return;
      const tasks = await readTasks();
      if (stopped) return;
      await syncTasks(tasks);
      if (stopped) return;
      const active = tasks.find(task => task.taskId === item.taskId)?.items?.find(value => value.id === item.itemId);
      if (!active || !["browser_running", "uploading"].includes(active.state)) {
        await stopCapture(new Error("TASK_CAPTURE_STOPPED"));
      }
    } catch { /* Lease heartbeat handles a disconnected companion. */ }
    finally { monitoring = false; }
  }, 2000);
  const heartbeat = setTimer(async () => {
    if (stopped || renewing) return;
    renewing = true;
    try { await renewLease(); }
    catch {
      if (!stopped) {
        try { await stopCapture(new Error("TASK_CAPTURE_STOPPED")); }
        catch { /* Capture cleanup must not reject a timer callback. */ }
      }
    } finally { renewing = false; }
  }, 20000);
  return () => {
    stopped = true;
    clearTimer(monitor);
    clearTimer(heartbeat);
  };
}
