// hud/js/panels/reactor.js — the central AI CORE: canvas reactor + readout.

import { h, setText } from "../util.js";
import { reactorLabel } from "../labels.js";
import { createReactor } from "../reactor.js";
import { mountPanel } from "../components/panel.js";

export function mount(ctx) {
  const { store, voice } = ctx;

  const canvas = h("canvas", { class: "reactor-canvas" });
  const nameNode = h("div", { class: "reactor-name", text: "JARVIS" });
  // Cloud-link state.  "ONLINE" is only shown when the polls behind
  // controller.refreshAll() actually succeeded — never assumed.
  const onlineNode = h("div", { class: "reactor-online", text: "OFFLINE", dataset: { online: "false" } });
  const stateNode = h("div", { class: "reactor-state", text: "OFFLINE" });
  const detailNode = h("div", { class: "reactor-detail", text: "no cloud link" });
  const telemetryLeft = h("span", { text: "MIC --" });
  const telemetryRight = h("span", { text: "0 ACTIVE" });

  const overlay = h("div", { class: "reactor-overlay" }, [nameNode, onlineNode, stateNode, detailNode]);
  const wrap = h("div", { class: "reactor-wrap", dataset: { state: "OFFLINE" } }, [
    canvas,
    overlay,
    h("div", { class: "reactor-telemetry" }, [telemetryLeft, telemetryRight]),
  ]);

  // The reactor stage is its own surface (no panel header) to keep the core
  // visually dominant, exactly like the reference layout.
  const panel = mountPanel("reactor", { title: "AI Core", meta: "reactor", className: "reactor-panel" });
  // Status phrase framing the core — same vocabulary as the footer readout.
  const band = h("div", { class: "reactor-band", text: "SYSTEM READY", dataset: { state: "OFFLINE" } });
  panel.body.append(h("div", { class: "reactor-stage" }, [band, wrap]));

  const reactor = createReactor(canvas);
  reactor.start();
  window.addEventListener("resize", () => reactor.resize());

  store.subscribe((state) => {
    const next = state.reactor || { state: "OFFLINE", detail: "" };
    reactor.setState(next.state);
    wrap.dataset.state = next.state;
    band.dataset.state = next.state;
    setText(band, reactorLabel(next.state));
    setText(onlineNode, state.online ? "ONLINE" : "OFFLINE");
    onlineNode.dataset.online = String(Boolean(state.online));
    setText(stateNode, next.state);
    // With the link down both lines would read OFFLINE — print it once.
    stateNode.hidden = !state.online;
    setText(detailNode, next.detail || "");

    const mic = state.voice.mic;
    setText(
      telemetryLeft,
      `MIC ${mic === "on" ? "LIVE" : mic === "muted" ? "MUTED" : mic === "off" ? "OFF" : mic.toUpperCase()}`
    );
    const active = Object.entries(state.counts || {})
      .filter(([status]) => ["planning", "queued", "running", "recovering", "waiting_permission"].includes(status))
      .reduce((total, entry) => total + entry[1], 0);
    setText(telemetryRight, `${active} ACTIVE · ${state.sseConnected ? "LIVE" : "POLL"}`);
    reactor.setLevel(state.voice.level || 0);
  });

  // keep the mic level feeding the core even between store updates
  const levelTimer = window.setInterval(() => {
    if (voice && voice.hasAnalyser()) reactor.setLevel(voice.readLevel());
  }, 90);

  return {
    root: panel.root,
    destroy() {
      window.clearInterval(levelTimer);
      reactor.stop();
    },
  };
}