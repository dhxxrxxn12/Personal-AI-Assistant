// hud/js/panels/quick-actions.js — compact HUD buttons that create REAL tasks
// through the existing Cloud task system.  Nothing here executes on the Mac
// directly: UI → Cloud → Task → Mac Agent → Action → Result.

import { h } from "../util.js";
import { mountPanel } from "../components/panel.js";

const ACTIONS = [
  { label: "Open Safari", goal: "open safari" },
  { label: "Open Chrome", goal: "open chrome" },
  { label: "Open Finder", goal: "open finder" },
  { label: "Take Screenshot", goal: "take a screenshot" },
];

export function mount(ctx) {
  const { store, controller } = ctx;
  const panel = mountPanel("quick-actions", { title: "Quick Actions", meta: "task dispatch" });
  const grid = h("div", { class: "quick-actions" });
  // One execution per press: a button stays out of the Set until the POST has
  // settled, so a store tick (polls land every few seconds) cannot re-enable it
  // while the request is still in flight.
  const busy = new Set();
  const buttons = ACTIONS.map((action) => {
    const button = h("button", {
      class: "btn",
      type: "button",
      text: action.label,
      title: `Create task: "${action.goal}"`,
    });
    button.addEventListener("click", async () => {
      if (busy.has(button)) return;
      busy.add(button);
      button.disabled = true;
      try {
        await controller.submitCommand(action.goal);
      } catch (error) {
        /* controller logs the failure into the activity feed */
      } finally {
        window.setTimeout(() => {
          busy.delete(button);
          button.disabled = !store.state.online;
        }, 600);
      }
    });
    grid.append(button);
    return button;
  });

  panel.body.append(grid);

  store.subscribe((state) => {
    buttons.forEach((button) => { button.disabled = !state.online || busy.has(button); });
    const permissionCount = state.counts.waiting_permission || 0;
    panel.setMeta(
      !state.online
        ? "cloud offline"
        : permissionCount
          ? `${permissionCount} awaiting approval`
          : "via cloud planner"
    );
    panel.setTone(permissionCount ? "warn" : null);
  });

  return { root: panel.root };
}