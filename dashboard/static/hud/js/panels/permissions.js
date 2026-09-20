// hud/js/panels/permissions.js — PERMISSION REQUESTS.
//
// The only way an unsafe step can run is through the existing Cloud gate:
// UI → POST /tasks/{id}/permission/approve → exact blocked step → Mac agent.
// Denial keeps the task in needs_review and nothing is executed.

import { h, emptyNote, shortId, shortTime, setText, prettyStatus } from "../util.js";
import { mountPanel } from "../components/panel.js";

// ACTION | DESCRIPTION | TASK ID | REQUESTED
function permField(label, value, tone = null) {
  return h("div", { class: "perm-field" }, [
    h("span", { class: "perm-field__label", text: label }),
    h("span", { class: "perm-field__value", text: value, dataset: tone ? { tone } : {} }),
  ]);
}

export function mount(ctx) {
  const { store, controller } = ctx;
  const panel = mountPanel("permissions", { title: "Permission Requests", meta: "gate enabled" });
  const list = h("div", { class: "panel-body" });
  const note = h("p", { class: "empty-note", text: "" });
  panel.body.append(list, note);

  function permissionBlock(task) {
    const request = task.permission_request || {};
    const parameters = request.parameters || {};
    const critical = /delete|remove|format|shutdown|reboot|purchase|pay|send|install/i.test(
      `${request.action || ""} ${JSON.stringify(parameters)}`
    );

    const approve = h("button", { class: "btn btn--tiny btn--ok", type: "button", text: "✓ Approve" });
    const deny = h("button", { class: "btn btn--tiny btn--danger", type: "button", text: "✕ Deny" });

    approve.addEventListener("click", async () => {
      approve.disabled = true;
      deny.disabled = true;
      try {
        await controller.approvePermission(task, request.action, parameters);
        setText(note, `approved ${request.action} · task requeued for ${task.device}`);
      } catch (error) {
        setText(note, `approval failed: ${error.message}`);
      } finally {
        approve.disabled = false;
        deny.disabled = false;
      }
    });

    deny.addEventListener("click", async () => {
      approve.disabled = true;
      deny.disabled = true;
      try {
        await controller.denyPermission(task);
        setText(note, `denied ${request.action} · task moved to needs_review`);
      } catch (error) {
        setText(note, `denial failed: ${error.message}`);
      } finally {
        approve.disabled = false;
        deny.disabled = false;
      }
    });

    return h("div", { class: "perm-block", dataset: { critical: String(critical) } }, [
      h("div", { class: "perm-fields" }, [
        permField("Action", request.action || "blocked step", "accent"),
        permField("Description", request.description || task.goal || "(no description)"),
        permField("Task ID", shortId(task.id)),
        permField("Requested", shortTime(request.requested_at)),
      ]),
      h("div", {
        class: "perm-block__meta",
        text: `step ${request.step_index ?? "?"} · ${prettyStatus(request.status)} · task ${prettyStatus(task.status)} · device ${task.device || "—"}`,
      }),
      h("div", { class: "perm-block__params", text: JSON.stringify(parameters, null, 2) }),
      h("div", { class: "perm-block__actions" }, [approve, deny]),
    ]);
  }

  store.subscribe((state) => {
    const pending = (state.tasks || []).filter(
      (task) => task.status === "waiting_permission" && task.permission_request && task.permission_request.status === "pending"
    );

    list.replaceChildren(...(pending.length ? pending.map(permissionBlock) : [emptyNote("no pending requests — gate armed")]));

    const denied = (state.tasks || []).filter(
      (task) => task.permission_request && task.permission_request.status === "denied"
    ).length;
    const approved = (state.tasks || []).filter(
      (task) => task.permission_request && task.permission_request.status === "approved"
    ).length;

    if (!pending.length) {
      setText(note, `${approved} approved · ${denied} denied (all time)`);
    }
    panel.setMeta(pending.length ? `${pending.length} pending` : "gate enabled");
    panel.setTone(pending.length ? "warn" : "ok");
  });

  return { root: panel.root };
}