// hud/js/panels/system-status.js — SYSTEM STATUS rows straight from
// GET /system/status (cloud, Gemini, queue, gate, agent, memory).

import { h, emptyNote } from "../util.js";
import { mountPanel } from "../components/panel.js";

export function mount(ctx) {
  const { store } = ctx;
  const panel = mountPanel("system-status", { title: "System Status", meta: "subsystems" });
  const rows = h("div", { class: "status-rows" });
  panel.body.append(rows);

  store.subscribe((state) => {
    const payload = state.systemStatus;
    const subsystems = (payload && payload.subsystems) || [];

    if (!subsystems.length) {
      rows.replaceChildren(emptyNote(state.online ? "loading subsystem state" : "cloud offline"));
      panel.setMeta("unavailable");
      return;
    }

    rows.replaceChildren(
      ...subsystems.map((entry) =>
        h("div", { class: "status-row", dataset: { key: entry.key || "" } }, [
          h("span", { class: "status-dot", dataset: { ok: String(Boolean(entry.ok)) }, title: entry.detail || "" }),
          h("span", { class: "status-row__label", text: entry.label }),
          h("span", {
            class: "status-row__value",
            text: entry.status,
            dataset: { ok: String(Boolean(entry.ok)) },
          }),
          h("span", { class: "status-row__detail", text: entry.detail || "" }),
        ])
      )
    );

    const degraded = subsystems.filter((entry) => !entry.ok);
    panel.setMeta(degraded.length ? `${degraded.length} degraded` : "nominal");
    panel.setTone(degraded.length ? "warn" : "ok");
  });

  return { root: panel.root };
}