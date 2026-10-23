// JARVIS ENGINEERING — transparent state for the controlled self-engineering
// lifecycle. This panel only calls authenticated Cloud endpoints.

import { h, shortId, shortTime, statusTone } from "../util.js";
import { mountPanel, statusPill } from "../components/panel.js";

function text(value, fallback = "—") {
  return value === undefined || value === null || value === "" ? fallback : String(value);
}

export function mount(ctx) {
  const { store, controller } = ctx;
  const panel = mountPanel("engineering", { title: "JARVIS Engineering", meta: "sandboxed lifecycle" });
  const body = h("div", { class: "engineering-view" });
  panel.body.append(body);

  store.subscribe((state) => {
    const tasks = [...(state.engineeringTasks || [])]
      .sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
    const task = tasks[0];
    if (!task) {
      body.replaceChildren(h("p", { class: "empty-note", text: "no engineering task recorded" }));
      panel.setMeta("sandboxed lifecycle");
      return;
    }

    const tests = Array.isArray(task.test_results) ? task.test_results : [];
    const repairs = Array.isArray(task.repair_attempts) ? task.repair_attempts : [];
    const files = Array.isArray(task.affected_files) ? task.affected_files : [];
    const events = Array.isArray(task.events) ? task.events : [];
    const controls = [];
    if (task.status === "awaiting_approval") {
      controls.push(h("button", {
        class: "btn btn--tiny btn--primary",
        type: "button",
        text: "Approve sandbox",
        onclick: () => controller.approveEngineeringTask(task.task_id).catch(() => {}),
      }));
    }
    if (!["completed", "cancelled", "rolled_back"].includes(task.status)) {
      controls.push(h("button", {
        class: "btn btn--tiny btn--danger",
        type: "button",
        text: "Cancel",
        onclick: () => controller.cancelEngineeringTask(task.task_id).catch(() => {}),
      }));
    }
    if (task.git_checkpoint && task.status === "awaiting_deploy_approval") {
      controls.push(h("button", {
        class: "btn btn--tiny btn--ghost",
        type: "button",
        text: "Rollback sandbox",
        onclick: () => controller.rollbackEngineeringTask(task.task_id).catch(() => {}),
      }));
    }

    body.replaceChildren(
      h("div", { class: "engineering-head" }, [
        h("span", { class: "cell-id", text: shortId(task.task_id) }),
        statusPill(task.status),
        h("span", { class: "engineering-controls" }, controls),
      ]),
      h("p", { class: "engineering-goal", text: text(task.goal), title: task.goal }),
      h("div", { class: "engineering-grid" }, [
        h("span", { text: `PHASE\n${text(task.current_phase)}` }),
        h("span", { text: `FILES\n${files.length}` }),
        h("span", { text: `TESTS\n${tests.filter((item) => item.success).length}/${tests.length}` }),
        h("span", { text: `REPAIR\n${repairs.length}` }),
        h("span", { text: `SANDBOX\n${task.sandbox_id ? "ACTIVE" : "—"}` }),
        h("span", { text: `CHECKPOINT\n${task.git_checkpoint ? shortId(task.git_checkpoint.commit) : "—"}` }),
      ]),
      h("p", { class: "empty-note", text: task.error || task.production_impact === "pending approval" ? `Production: ${text(task.production_impact)}` : `Production: ${text(task.production_impact)}` }),
      h("p", { class: "empty-note", text: `${events.length} lifecycle events · ${shortTime(task.created_at)}` }),
    );
    panel.setMeta(`${task.status} · ${task.required_approval ? "approval required" : "production protected"}`);
    panel.setTone(statusTone(task.status));
  });

  return { root: panel.root };
}
