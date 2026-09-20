// hud/js/panels/header.js — brand, clock, date and the three status glyphs.

import { h, setText, formatClock, formatDate } from "../util.js";
import { selectors } from "../store.js";

export function mount(ctx) {
  const { store } = ctx;
  const timeNode = document.getElementById("clockTime");
  const dateNode = document.getElementById("clockDate");
  const coreTag = document.getElementById("headerCoreTag");
  const iconLink = document.getElementById("iconLink");
  const iconAgent = document.getElementById("iconAgent");
  const iconMic = document.getElementById("iconMic");

  setText(dateNode, formatDate());

  const tick = window.setInterval(() => setText(timeNode, formatClock()), 1000);
  setText(timeNode, formatClock());

  const setIcon = (node, className) => {
    if (!node) return;
    node.classList.remove("is-ok", "is-warn", "is-bad");
    if (className) node.classList.add(className);
  };

  store.subscribe((state) => {
    setText(
      coreTag,
      `CORE // ${state.reactor.state}${state.sseConnected ? " · LIVE FEED" : ""}`
    );

    setIcon(iconLink, state.online ? "is-ok" : "is-bad");
    const agents = selectors.onlineDevices(state).filter((device) => device.device_type === "mac");
    setIcon(iconAgent, agents.length ? "is-ok" : state.devices.length ? "is-warn" : "is-bad");

    const mic = state.voice.mic;
    setIcon(iconMic, mic === "on" ? "is-ok" : mic === "muted" ? "is-warn" : mic === "off" ? null : "is-bad");
  });

  return {
    destroy() {
      window.clearInterval(tick);
    },
    root: h("span"),
  };
}