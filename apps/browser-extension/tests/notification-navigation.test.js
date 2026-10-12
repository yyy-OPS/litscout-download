import assert from "node:assert/strict";
import { test } from "node:test";
import { openTaskFromNotification } from "../src/background/notification-navigation.js";

function fixture({ panelDenied = false, closedTab = false, noWindow = false } = {}) {
  const state = {}, opened = [], tabs = [], windows = [];
  const chrome = {
    storage: { local: { async set(values) { Object.assign(state, values); } } },
    runtime: { getURL: path => "chrome-extension://fixture/" + path },
    tabs: {
      async update(id, values) { if (closedTab) throw Error("closed tab"); assert.equal(values.active, true); return { windowId: 9 }; },
      async create(values) { tabs.push(values); },
    },
    windows: {
      async getLastFocused() { if (noWindow) throw Error("no windows"); return { id: 5 }; },
      async update(id, values) { assert.equal(values.focused, true); windows.push(id); },
      async create(values) { windows.push(values); },
    },
    sidePanel: { async open(values) { if (panelDenied) throw Error("gesture not supported"); opened.push(values); } },
  };
  return { chrome, state, opened, tabs, windows };
}
const target = { taskId: "task-1", itemId: "paper-1", tabId: 42 };

test("notification click selects its exact task/article and opens that browser window's side panel", async () => {
  const f = fixture();
  await openTaskFromNotification(f.chrome, target);
  assert.deepEqual(f.state, { selectedTaskId: "task-1", focusItemId: "paper-1", paperFilter: "all" });
  assert.deepEqual(f.opened, [{ windowId: 9 }]);
  assert.equal(f.tabs.length, 0);
});

test("a closed article and restricted gesture fall back to the extension workbench in a normal tab", async () => {
  const f = fixture({ closedTab: true, panelDenied: true });
  await openTaskFromNotification(f.chrome, target);
  assert.deepEqual(f.tabs, [{ windowId: 5, active: true, url: "chrome-extension://fixture/index.html" }]);
  assert.equal(f.state.selectedTaskId, "task-1");
});

test("a user notification click can restore the workbench when no normal browser window remains", async () => {
  const f = fixture({ closedTab: true, noWindow: true });
  await openTaskFromNotification(f.chrome, target);
  assert.deepEqual(f.windows, [{ url: "chrome-extension://fixture/index.html", focused: true }]);
  assert.equal(f.state.focusItemId, "paper-1");
});
