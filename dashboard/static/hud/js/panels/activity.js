// hud/js/panels/activity.js — live activity log fed by the Cloud event bus
// (SSE when available, cursor polling otherwise) plus HUD-local notices.

import { h, emptyNote, shortTime } from "../util.js";
import { mountPanel } from "../components/panel.js";

const FILTERS = {
  ALL: null,
  TASKS: new Set(["PLAN", "TASK", "EXEC", "AGENT", "USER"]),
  SYSTEM: new Set(["SYS", "ERROR"]),
  DEVICES: new Set(["DEV"]),
  PERMISSIONS: new Set(["PERM"]),
};
const MAX_ROWS = 220;

function logRow(event) {
  return h(
    "div",
    {
      class: "log-row",
      dataset: { level: event.level || "info" },
      title: new Date(event.at).toLocaleString(),
    },
    [
      h("time", { text: shortTime(event.at) }),
      h("span", { class: "log-cat", text: event.category || "SYS", dataset: { cat: event.category || "SYS" } }),
      h("span", { class: "log-msg", text: event.message || "" }),
    ]
  );
}

export function mount(ctx) {
  const { store } = ctx;
  const panel = mountPanel("activity", { title: "Activity Log", meta: "live feed" });
  const log = h("div", { class: "log", role: "log", "aria-live": "polite" });
  const legend = h(
    "div",
    { class: "legend", role: "group", "aria-label": "Activity filter" },
    Object.keys(FILTERS).map((filter) => h("button", {
      class: "activity-filter",
      type: "button",
      text: filter,
      "aria-pressed": filter === "ALL" ? "true" : "false",
    }))
  );
  panel.body.append(log, legend);

  let selectedFilter = "ALL";
  let lastSignature = null;
  const keyOf = (event) => (event.key !== undefined ? event.key : event.seq);
  const controls = Array.from(legend.querySelectorAll("button"));

  function filteredEvents(events) {
    const categories = FILTERS[selectedFilter];
    return categories ? events.filter((event) => categories.has(event.category)) : events;
  }

  function updateControls(events) {
    for (const control of controls) {
      const filterKey = control.textContent;
      const categories = FILTERS[filterKey];
      const count = categories
        ? events.filter((event) => categories.has(event.category)).length
        : events.length;
      const active = filterKey === selectedFilter;
      control.classList.toggle("is-active", active);
      control.setAttribute("aria-pressed", String(active));
      // Counts ride in a ::before pseudo-element so button.textContent — which
      // the filter lookup above depends on — stays exactly the tab name.
      control.dataset.count = String(count);
      control.title = `${filterKey} · ${count} event(s)`;
    }
  }

  function render(state) {
    const events = state.events || [];
    const shown = filteredEvents(events).slice(-MAX_ROWS);
    const signature = `${selectedFilter}:${events.length}:${events.length ? keyOf(events[events.length - 1]) : ""}`;

    if (signature !== lastSignature) {
      log.replaceChildren(...(shown.length ? shown.map(logRow) : [emptyNote("no matching events")]));
      log.scrollTop = log.scrollHeight;
      lastSignature = signature;
    }

    updateControls(events);
    panel.setMeta(`${shown.length}/${events.length} events · ${state.sseConnected ? "sse live" : "polling"}`);
    const errors = events.slice(-30).filter((event) => event.category === "ERROR").length;
    panel.setTone(errors ? "danger" : null);
  }

  controls.forEach((control) => control.addEventListener("click", () => {
    selectedFilter = control.textContent;
    lastSignature = null;
    render(store.state);
  }));

  store.subscribe(render);
  return { root: panel.root };
}
