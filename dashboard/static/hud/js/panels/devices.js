// hud/js/panels/devices.js — connected JARVIS devices from GET /devices, plus a
// real device registration form (POST /devices/register).

import { h, emptyNote, agoText, statusClass, prettyStatus, setText } from "../util.js";
import { mountPanel } from "../components/panel.js";

function deviceCard(device) {
  const online = device.status === "online";
  const capabilities = Array.isArray(device.capabilities) ? device.capabilities : [];
  const metadata = device.metadata || {};
  const platform = [
    metadata.os || device.device_type || "device",
    metadata.architecture || "",
  ].filter(Boolean).join(" • ");
  const age = device.last_seen
    ? agoText((Date.now() - new Date(device.last_seen).getTime()) / 1000)
    : "never seen";
  const name = device.name && device.name !== device.device_id ? ` · ${device.name}` : "";

  return h("article", { class: "device-card", dataset: { online: String(online) } }, [
    h("div", { class: "device-card__top" }, [
      h("div", { class: "device-card__id", text: device.device_id || "unknown" }),
      h("span", { class: statusClass(device.status), text: prettyStatus(device.status) }),
    ]),
    h("div", { class: "device-card__meta", text: `${platform}${name}` }),
    h("div", { class: "device-card__field" }, [
      h("span", { class: "device-card__label", text: "Last seen" }),
      h("span", {
        class: "device-card__meta",
        text: device.last_seen ? `${age}${online ? "" : " · offline"}` : "never seen",
        title: device.last_seen || "",
      }),
    ]),
    h("div", { class: "device-card__field" }, [
      h("span", { class: "device-card__label", text: "Capabilities" }),
      capabilities.length
        ? h(
            "div",
            { class: "device-card__caps" },
            capabilities.map((capability) => h("span", { class: "tag", text: capability }))
          )
        : h("span", { class: "device-card__meta", text: "none reported" }),
    ]),
  ]);
}

export function mount(ctx) {
  const { store, api, controller } = ctx;
  const panel = mountPanel("devices", { title: "Connected Devices", meta: "0 online" });
  const list = h("div", { class: "panel-body" });
  panel.body.append(list);

  const feedback = h("p", { class: "empty-note", text: "" });
  const fields = {
    id: h("input", { class: "command-select", placeholder: "device id", value: "" }),
    type: h("input", { class: "command-select", placeholder: "device type (mac)", value: "mac" }),
    name: h("input", { class: "command-select", placeholder: "display name", value: "" }),
  };
  const registerButton = h("button", { class: "btn btn--tiny", type: "button", text: "+ Register Device" });
  const submitButton = h("button", { class: "btn btn--tiny btn--primary", type: "button", text: "Commit" });
  const form = h("div", { class: "status-grid", style: "display:none;gap:6px" }, [
    fields.id,
    fields.type,
    fields.name,
    submitButton,
  ]);

  registerButton.addEventListener("click", () => {
    const hidden = form.style.display === "none";
    form.style.display = hidden ? "grid" : "none";
  });

  submitButton.addEventListener("click", async () => {
    const deviceId = fields.id.value.trim();
    const deviceType = fields.type.value.trim() || "mac";
    const name = fields.name.value.trim() || deviceId;
    if (!deviceId) {
      setText(feedback, "device id is required");
      return;
    }
    submitButton.disabled = true;
    try {
      await api.registerDevice(deviceId, deviceType, name);
      setText(feedback, `${deviceId} registered — start its agent to receive heartbeats`);
      controller.logLocal("DEV", `Registered device ${deviceId} from the command center`);
      fields.id.value = "";
      fields.name.value = "";
      await controller.refreshAll();
    } catch (error) {
      setText(feedback, `register failed: ${error.message}`);
    } finally {
      submitButton.disabled = false;
    }
  });

  panel.body.append(list, feedback, registerButton, form);

  store.subscribe((state) => {
    const devices = state.devices || [];
    list.replaceChildren(
      ...(devices.length ? devices.map(deviceCard) : [emptyNote("no devices registered")])
    );
    panel.setMeta(`${devices.filter((device) => device.status === "online").length}/${devices.length} online`);
  });

  return { root: panel.root };
}