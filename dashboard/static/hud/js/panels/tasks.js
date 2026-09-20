// hud/js/panels/tasks.js — RECENT TASKS from GET /tasks.
// Statuses are shown exactly as the Cloud state machine reports them.

import { h, shortId, shortTime, statusTone, emptyNote } from "../util.js";
import { mountPanel, headRow, tableRow, statusPill } from "../components/panel.js";

const GRID = "tasks-grid";
const ACTIVE = ["planning", "queued", "running", "recovering"];

function resultText(result) {
  if (result === null || result === undefined) return "—";
  if (typeof result !== "object") return String(result);

  const steps = Array.isArray(result.results)
    ? result.results
    : Array.isArray(result.steps) ? result.steps : [];
  const finalStep = steps[steps.length - 1];
  const value = finalStep && typeof finalStep === "object"
    ? finalStep.result
    : result.result;

  if (value !== null && value !== undefined) {
    return typeof value === "object" ? JSON.stringify(value) : String(value);
  }
  return JSON.stringify(result);
}

export function mount(ctx) {
  const { store, controller } = ctx;
  const panel = mountPanel("tasks", { title: "Recent Tasks", meta: "task queue" });

  const table = h("div", { class: "hud-table" }, [
    headRow(["ID", "GOAL", "DEVICE", "STATUS", "RESULT", "CREATED AT"], GRID),
  ]);
  const scroll = h("div", { class: "hud-table__scroll" });
  panel.body.append(table, scroll);

  table.querySelector(".hud-table__head").append(h("span", { text: "ACT" }));

  store.subscribe((state) => {
    const tasks = [...(state.tasks || [])]
      .sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")))
      .slice(0, 24);

    if (!tasks.length) {
      scroll.replaceChildren(emptyNote("no tasks recorded"));
    } else {
      scroll.replaceChildren(
        ...tasks.map((task) => {
          const row = tableRow(
            [
              { text: shortId(task.id), class: "cell-id", title: task.id },
              { text: task.goal || "(no goal)", class: "cell-goal", title: `${task.goal || ""}\n${task.recovery_reason || ""}`.trim() },
              { text: task.device || "—", class: "cell-muted cell-device" },
              { text: "", class: "cell-status" },
              { text: resultText(task.result), class: "cell-muted cell-result", title: resultText(task.result) },
              { text: shortTime(task.created_at), class: "cell-muted" },
            ],
            { gridClass: GRID, tone: statusTone(task.status) }
          );

          row.children[3].append(statusPill(task.status));

          const cancel = h("button", {
            class: "btn btn--tiny btn--ghost",
            type: "button",
            text: "✕",
            title: ACTIVE.includes(task.status) ? `Cancel ${shortId(task.id)}` : "Task is not cancellable",
            "aria-label": `Cancel task ${shortId(task.id)}`,
          });
          cancel.disabled = !ACTIVE.includes(task.status);
          cancel.addEventListener("click", () => { controller.cancelTask(task.id).catch(() => {}); });
          row.append(cancel);
          return row;
        })
      );
    }

    panel.setMeta(
      `${state.counts.running || 0} running · ${state.counts.queued || 0} queued · ${state.counts.completed || 0} done`
    );
    panel.setTone(state.counts.failed ? "danger" : state.counts.waiting_permission ? "warn" : null);
  });

  return { root: panel.root };
}
