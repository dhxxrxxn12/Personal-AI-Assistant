// hud/js/panels/ai-core.js — AI CORE status: model, planner, executor, memory.
// Values come from /system/status, /memory/status and /actions — never faked.

import { h, setText } from "../util.js";
import { mountPanel } from "../components/panel.js";

export function mount(ctx) {
  const { store, api } = ctx;
  const panel = mountPanel("ai-core", { title: "AI Core", meta: "subsystems" });
  const rows = h("div", { class: "core-rows" });
  const note = h("p", { class: "empty-note", text: "" });
  panel.body.append(rows, note);

  // Each row keeps a reference to its value node so updates never rebuild the DOM.
  function valueRow(label) {
    const value = h("b", { text: "—" });
    rows.append(h("div", { class: "core-row" }, [h("span", { text: label }), value]));
    return value;
  }

  const llm = valueRow("LLM");
  const planner = valueRow("PLANNER");
  const executor = valueRow("EXECUTOR");
  const memory = valueRow("MEMORY");
  const actions = valueRow("ACTIONS");

  let actionCount = null;
  api.actions()
    .then((payload) => {
      actionCount = payload.count;
      render(store.state);
    })
    .catch(() => {});

  function subsystem(state, key) {
    const status = state.systemStatus || {};
    return (status.subsystems || []).find((entry) => entry.key === key) || null;
  }

  function render(state) {
    const gemini = subsystem(state, "gemini");
    const agent = subsystem(state, "mac_agent");
    const memoryStatus = subsystem(state, "memory");
    const cloud = subsystem(state, "cloud");

    setText(llm, gemini ? gemini.status : "CHECKING");
    llm.className = gemini ? (gemini.ok ? "is-ok" : "is-bad") : "";

    setText(planner, gemini ? (gemini.ok ? "READY" : "DEGRADED") : "CHECKING");
    planner.className = gemini ? (gemini.ok ? "is-ok" : "is-warn") : "";

    setText(executor, agent ? agent.status : "CHECKING");
    executor.className = agent ? (agent.ok ? "is-ok" : "is-bad") : "";

    setText(memory, memoryStatus ? memoryStatus.status : "CHECKING");
    memory.className = memoryStatus ? (memoryStatus.ok ? "is-ok" : "is-warn") : "";

    setText(
      actions,
      actionCount === null ? "LOADING" : `${actionCount} REGISTERED`
    );
    actions.className = actionCount === null ? "" : "is-ok";

    setText(
      note,
      cloud
        ? `${cloud.detail} · queue ${state.counts.queued || 0}q/${state.counts.running || 0}r`
        : "awaiting cloud status"
    );
    panel.setMeta(gemini ? gemini.status.toLowerCase() : "checking");
  }

  store.subscribe(render);
  return { root: panel.root };
}