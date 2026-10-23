// hud/js/main.js — composition root: builds every panel against the shared
// store/controller and starts the polling/streaming loops.

import { api } from "./api.js";
import { store, selectors } from "./store.js";
import { createController } from "./controller.js";
import { createVoice } from "./voice.js";

import * as headerPanel from "./panels/header.js";
import * as systemMonitorPanel from "./panels/system-monitor.js";
import * as devicesPanel from "./panels/devices.js";
import * as quickActionsPanel from "./panels/quick-actions.js";
import * as speechPanel from "./panels/speech.js";
import * as aiCorePanel from "./panels/ai-core.js";
import * as reactorPanel from "./panels/reactor.js";
import * as activityPanel from "./panels/activity.js";
import * as uploadPanel from "./panels/upload.js";
import * as commandPanel from "./panels/command.js";
import * as tasksPanel from "./panels/tasks.js";
import * as engineeringPanel from "./panels/engineering.js";
import * as permissionsPanel from "./panels/permissions.js";
import * as systemStatusPanel from "./panels/system-status.js";
import * as footerPanel from "./panels/footer.js";

const PANELS = [
  headerPanel,
  systemMonitorPanel,
  devicesPanel,
  quickActionsPanel,
  speechPanel,
  aiCorePanel,
  reactorPanel,
  activityPanel,
  uploadPanel,
  commandPanel,
  tasksPanel,
  engineeringPanel,
  permissionsPanel,
  systemStatusPanel,
  footerPanel,
];

const voice = createVoice({
  onChange: (voiceState) => store.set({ voice: voiceState }),
  getSessionTicket: async () => {
    const session = await api.voiceSession();
    return session.ticket;
  },
});

const controller = createController({ voice });
const ctx = { store, api, controller, voice, selectors };
const mountedPanels = [];

for (const panel of PANELS) {
  try {
    const mounted = panel.mount(ctx);
    if (mounted && typeof mounted.destroy === "function") mountedPanels.push(mounted);
  } catch (error) {
    console.error(`[HUD] panel failed to mount`, error);
  }
}

// Wake-word utterances captured by the browser are dispatched as tasks.
voice.on("transcript", (payload) => {
  if (payload && payload.text) controller.submitVoiceCommand(payload.text);
});
voice.on("wake", (payload) => {
  controller.logLocal("USER", `Wake word detected locally: "${payload.phrase}"`);
});

controller.start();

// Let the panels show the first sample immediately.
controller.refreshAll().catch(() => {
  controller.logLocal("ERROR", "Initial sync failed — retrying automatically", "error");
});

window.addEventListener("beforeunload", () => {
  controller.stop();
  mountedPanels.forEach((panel) => panel.destroy());
});

// Debug hook (read-only): inspect the live store from devtools.
window.__jarvisHud = { store, controller, voice, selectors };
