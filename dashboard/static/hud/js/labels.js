// hud/js/labels.js — shared status vocabulary.
//
// The reactor band, the core readout and the footer all describe the same
// derived reactor state, so they read the wording from one place and can never
// drift apart.  These are labels only: the state itself is always computed by
// controller.js from real backend data.

export const REACTOR_LABELS = {
  IDLE: "SYSTEM READY",
  LISTENING: "LISTENING",
  THINKING: "PLANNING",
  EXECUTING: "EXECUTING",
  PERMISSION: "AWAITING APPROVAL",
  SUCCESS: "TASK COMPLETE",
  ERROR: "ATTENTION REQUIRED",
  OFFLINE: "CLOUD OFFLINE",
};

export function reactorLabel(state) {
  const key = String(state || "").toUpperCase();
  return REACTOR_LABELS[key] || key || REACTOR_LABELS.IDLE;
}
