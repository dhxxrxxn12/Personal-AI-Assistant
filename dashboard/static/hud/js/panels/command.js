// hud/js/panels/command.js — the main JARVIS command input.
// Submitting always creates a task through the existing Cloud API; the HUD
// never executes anything on the Mac itself.

import { h, setText } from "../util.js";
import { mountPanel } from "../components/panel.js";
import { selectors } from "../store.js";

// Example goals shown as chips.  Clicking one only fills the input — the SEND
// button (and therefore the Cloud task API) is still the single dispatch path.
const EXAMPLES = ["Open Safari", "Open Chrome", "Open Finder", "Take a screenshot"];

export function mount(ctx) {
  const { store, controller } = ctx;
  const panel = mountPanel("command", { title: "Command Input", meta: "cloud task api" });

  const textarea = h("textarea", {
    class: "command-input",
    rows: "2",
    placeholder: "Type a command or question...",
    spellcheck: "false",
  });
  const sendButton = h("button", { class: "btn btn--primary", type: "submit", text: "Send" });
  const deviceSelect = h("select", { class: "command-select" });
  const interruptButton = h("button", { class: "btn btn--tiny btn--danger", type: "button", text: "Interrupt [Esc]" });
  const micChip = h("span", { class: "pill", text: "MIC OFF" });
  const hint = h("span", { class: "command-hint", text: "ENTER to send · SHIFT+ENTER newline" });
  const result = h("p", { class: "empty-note", text: "" });

  const examples = h(
    "div",
    { class: "command-examples" },
    EXAMPLES.map((example) =>
      h("button", {
        class: "command-chip",
        type: "button",
        text: example,
        title: `Prefill "${example}" — press SEND to queue it`,
        onclick: () => {
          textarea.value = example;
          textarea.focus();
        },
      })
    )
  );

  const form = h("form", { class: "command-form" }, [
    textarea,
    h("div", { class: "command-row" }, [
      h("label", { class: "command-device" }, [h("span", { text: "Device" }), deviceSelect]),
      sendButton,
    ]),
    h("div", { class: "command-meta" }, [interruptButton, micChip, hint]),
    examples,
    result,
  ]);
  panel.body.append(form);

  // Exactly one execution per command: the flag is set synchronously, so a
  // second ENTER (or a double click) while the POST is in flight is ignored.
  // The Cloud /tasks route is the single dispatch path — the UI never runs a
  // command locally.
  let inFlight = false;

  async function submit() {
    const goal = textarea.value.trim();
    if (!goal || inFlight) return;
    inFlight = true;
    sendButton.disabled = true;
    textarea.value = "";
    try {
      const task = await controller.submitCommand(goal, deviceSelect.value);
      setText(result, task ? `queued ${String(task.id).slice(0, 8)} → ${task.device}` : "");
    } catch (error) {
      setText(result, `rejected: ${error.message}`);
      // hand the text back so the user does not lose it — unless they already
      // started typing something new.
      if (!textarea.value.trim()) textarea.value = goal;
    } finally {
      inFlight = false;
      sendButton.disabled = false;
      textarea.focus();
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    submit();
  });

  textarea.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  });

  interruptButton.addEventListener("click", () => { controller.interrupt(); });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    event.preventDefault();
    controller.interrupt();
  });

  window.addEventListener("hud:prefill-command", (event) => {
    const text = event.detail && event.detail.text;
    if (!text) return;
    textarea.value = textarea.value ? `${textarea.value} ${text}` : text;
    textarea.focus();
  });

  let deviceSignature = null;

  function syncDeviceOptions(state) {
    const devices = state.devices || [];
    const primary = selectors.primaryDevice(state);
    const ids = devices.map((device) => device.device_id);
    const signature = `${ids.join("|")}#${primary}`;
    const currentValue = deviceSelect.value;

    if (signature !== deviceSignature) {
      const options = ids.length
        ? ids.map((id) => h("option", { value: id, text: id === primary ? `${id} (default)` : id }))
        : [h("option", { value: "mac-main", text: "mac-main (default)" })];
      deviceSelect.replaceChildren(...options);
      deviceSignature = signature;
    }

    const wanted = ids.includes(currentValue) ? currentValue : primary;
    if (deviceSelect.value !== wanted) deviceSelect.value = wanted;
  }

  store.subscribe((state) => {
    syncDeviceOptions(state);
    sendButton.disabled = !state.online;

    const mic = state.voice.mic;
    setText(micChip, `MIC ${mic === "on" ? "ACTIVE" : mic === "muted" ? "MUTED" : mic.toUpperCase()}`);
    micChip.className = `pill ${mic === "on" ? "pill--online" : mic === "muted" ? "pill--waiting_permission" : "pill--unknown"}`;

    const active = selectors.activeTasks(state);
    interruptButton.disabled = !active.length;
    interruptButton.classList.toggle("is-dim", !active.length);

    panel.setMeta(
      !state.online
        ? "cloud offline"
        : active.length
          ? `${active.length} task(s) in flight`
          : "idle"
    );
  });

  setTimeout(() => textarea.focus(), 400);
  return { root: panel.root };
}