// hud/js/panels/speech.js — SPEECH panel: real microphone, wake word, status
// and an audio-level waveform drawn from the live analyser.

import { h, setText } from "../util.js";
import { mountPanel } from "../components/panel.js";

function micLabel(state) {
  switch (state) {
    case "on": return "ON";
    case "muted": return "MUTED";
    case "denied": return "DENIED";
    case "error": return "ERROR";
    default: return "OFF";
  }
}

function micTone(state) {
  switch (state) {
    case "on": return "on";
    case "muted": return "muted";
    case "denied":
    case "error": return "unavailable";
    default: return "off";
  }
}

export function mount(ctx) {
  const { store, voice, controller } = ctx;

  const panel = mountPanel("speech", { title: "Speech", meta: "browser audio" });
  const micNode = h("b", { text: "OFF" });
  const wakeNode = h("b", { text: "OFF" });
  const statusNode = h("b", { text: "IDLE" });
  const canvas = h("canvas");
  const waveform = h("div", { class: "waveform" }, [canvas]);
  // Analyser level, measured from the real MediaStream (voice.readLevel).
  const levelFill = h("div", { class: "bar__fill", style: "width:0%" });
  const levelValue = h("span", { class: "speech-level__value", text: "0%" });
  const levelMeter = h("div", { class: "speech-level" }, [
    h("span", { text: "Level" }),
    h("div", { class: "bar" }, [levelFill]),
    levelValue,
  ]);

  const micButton = h("button", { class: "btn btn--tiny", type: "button", text: "Enable Mic" });
  const muteButton = h("button", { class: "btn btn--tiny", type: "button", text: "Mute" });
  const wakeButton = h("button", { class: "btn btn--tiny", type: "button", text: "Wake Word" });
  const listenButton = h("button", { class: "btn btn--tiny btn--primary", type: "button", text: "Start Voice" });
  const interruptButton = h("button", { class: "btn btn--tiny", type: "button", text: "Interrupt" });
  const transcriptNode = h("p", { class: "empty-note", text: "" });
  const note = h("p", { class: "empty-note", text: "" });

  panel.body.append(
    h("div", { class: "status-grid" }, [
      h("div", { class: "status-line" }, [h("span", { text: "Microphone" }), micNode]),
      h("div", { class: "status-line" }, [h("span", { text: "Wake Word" }), wakeNode]),
      h("div", { class: "status-line" }, [h("span", { text: "Status" }), statusNode]),
    ]),
    waveform,
    levelMeter,
    h("div", { class: "speech-actions" }, [micButton, muteButton, wakeButton, listenButton, interruptButton]),
    transcriptNode,
    note
  );

  const drawContext = canvas.getContext("2d");
  const wave = new Uint8Array(512);
  const motionMedia = typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-reduced-motion: reduce)")
    : null;
  let reducedMotion = Boolean(motionMedia && motionMedia.matches);
  let waveformFrame = 0;

  function drawWaveform() {
    const rect = canvas.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    if (canvas.width !== Math.round(rect.width * dpr) || canvas.height !== Math.round(rect.height * dpr)) {
      canvas.width = Math.max(1, Math.round(rect.width * dpr));
      canvas.height = Math.max(1, Math.round(rect.height * dpr));
    }
    drawContext.setTransform(dpr, 0, 0, dpr, 0, 0);
    const width = Math.max(1, rect.width);
    const height = Math.max(1, rect.height);
    drawContext.clearRect(0, 0, width, height);

    const analyser = voice && voice.analyser();
    const level = store.state.voice.level || 0;
    const accent = store.state.voice.status === "LISTENING" ? "#9df3ff" : "#2fe3ff";

    drawContext.strokeStyle = "rgba(47, 227, 255, 0.14)";
    drawContext.lineWidth = 1;
    drawContext.beginPath();
    drawContext.moveTo(0, height / 2);
    drawContext.lineTo(width, height / 2);
    drawContext.stroke();

    const count = analyser ? Math.min(wave.length, analyser.fftSize || wave.length) : wave.length;
    if (analyser) analyser.getByteTimeDomainData(wave);

    drawContext.beginPath();
    drawContext.lineWidth = 1.4;
    drawContext.strokeStyle = accent;
    const amplitude = height * 0.42;
    for (let index = 0; index < count; index += 1) {
      const x = (index / (count - 1)) * width;
      const raw = analyser ? (wave[index] - 128) / 128 : Math.sin(index * 0.22) * level;
      const y = height / 2 + raw * amplitude;
      if (index === 0) drawContext.moveTo(x, y);
      else drawContext.lineTo(x, y);
    }
    drawContext.stroke();
  }

  function animateWaveform() {
    waveformFrame = 0;
    drawWaveform();
    if (!reducedMotion) waveformFrame = window.requestAnimationFrame(animateWaveform);
  }

  function restartWaveform() {
    if (waveformFrame) window.cancelAnimationFrame(waveformFrame);
    waveformFrame = 0;
    drawWaveform();
    if (!reducedMotion) waveformFrame = window.requestAnimationFrame(animateWaveform);
  }

  function onMotionChange(event) {
    reducedMotion = event.matches;
    restartWaveform();
  }

  if (motionMedia) {
    if (motionMedia.addEventListener) motionMedia.addEventListener("change", onMotionChange);
    else motionMedia.addListener(onMotionChange);
  }
  restartWaveform();
  micButton.addEventListener("click", async () => {
    const current = store.state.voice.mic;
    if (current === "off" || current === "denied" || current === "error") {
      const ok = await voice.enable();
      controller.logLocal(
        "SYS",
        ok ? "Microphone opened by operator" : `Microphone unavailable: ${voice.state().error || voice.state().mic}`,
        ok ? "info" : "warn"
      );
    } else {
      voice.disable();
      controller.logLocal("SYS", "Microphone closed by operator");
    }
  });

  muteButton.addEventListener("click", () => {
    voice.toggleMute();
    controller.logLocal("USER", `Microphone ${voice.state().mic.toUpperCase()}`);
  });

  wakeButton.addEventListener("click", () => {
    const enable = store.state.voice.wake !== "on";
    const result = voice.setWake(enable);
    controller.logLocal(
      "SYS",
      result === "unsupported"
        ? "Wake word unsupported — Web Speech API unavailable in this browser"
        : `Wake word ${result.toUpperCase()}`,
      result === "unsupported" ? "warn" : "info"
    );
  });

  listenButton.addEventListener("click", async () => {
    if (voice.isRealtime && voice.isRealtime()) {
      voice.stopRealtime();
      controller.logLocal("SYS", "Cloud voice stopped");
      return;
    }

    const cloudVoiceStarted = voice.startRealtime ? await voice.startRealtime() : false;
    if (cloudVoiceStarted) {
      controller.logLocal("SYS", "Cloud voice connected — speak naturally");
      return;
    }

    // Live is optional: keep the existing browser Web Speech path usable when
    // the provider/model is unavailable or the browser cannot open a socket.
    if (store.state.voice.mic === "off") await voice.enable();
    const mic = voice.state().mic;
    if (mic !== "on" && mic !== "muted") {
      controller.logLocal("ERROR", "Cannot capture speech — microphone unavailable", "warn");
      return;
    }
    if (store.state.voice.speechRecognition === "unsupported") {
      controller.logLocal("ERROR", "Cloud voice unavailable and Web Speech is unsupported", "warn");
      return;
    }
    controller.logLocal("SYS", "Cloud voice unavailable — using browser speech fallback", "warn");
    voice.setStatus("LISTENING");
    const heard = await voice.listenOnce();
    if (heard) controller.submitVoiceCommand(heard);
    else voice.setStatus("IDLE");
  });

  interruptButton.addEventListener("click", () => {
    if (voice.interrupt) voice.interrupt();
    controller.logLocal("USER", "Voice playback interrupted");
  });

  store.subscribe((state) => {
    const voiceState = state.voice;
    setText(micNode, micLabel(voiceState.mic));
    micNode.dataset.state = micTone(voiceState.mic);
    setText(wakeNode, voiceState.wake === "unsupported" ? "UNSUPPORTED" : voiceState.wake.toUpperCase());
    wakeNode.dataset.state = voiceState.wake === "on"
      ? "on"
      : voiceState.wake === "unsupported" ? "unavailable" : "off";
    setText(statusNode, voiceState.status);
    statusNode.dataset.state = ["LISTENING", "THINKING", "SPEAKING"].includes(voiceState.status) ? "on" : "off";

    const closed = ["off", "denied", "error"].includes(voiceState.mic);
    micButton.textContent = closed ? "Enable Mic" : "Disable Mic";
    muteButton.disabled = voiceState.mic !== "on" && voiceState.mic !== "muted";
    muteButton.textContent = voiceState.mic === "muted" ? "Unmute" : "Mute";
    wakeButton.textContent = voiceState.wake === "on" ? "Wake: On" : "Wake: Off";
    wakeButton.disabled = voiceState.wake === "unsupported";
    listenButton.disabled = voiceState.mic === "denied" || voiceState.mic === "error";
    listenButton.textContent = voiceState.realtime === "connecting"
      ? "Connecting…"
      : voiceState.realtime === "connected" ? "Stop Voice" : "Start Voice";
    interruptButton.disabled = voiceState.realtime !== "connected" || voiceState.status !== "SPEAKING";

    const level = Math.round(Math.max(0, Math.min(1, Number(voiceState.level) || 0)) * 100);
    levelFill.style.width = `${level}%`;
    setText(levelValue, `${level}%`);
    setText(
      transcriptNode,
      voiceState.transcript ? `transcript: ${String(voiceState.transcript).slice(0, 180)}` : ""
    );

    setText(
      note,
      voiceState.realtime === "connected"
        ? `engine: Gemini Live · ${voiceState.status.toLowerCase()}`
        : voiceState.error || (voiceState.speechRecognition === "unsupported"
          ? "speech recognition unavailable — use command input"
          : `fallback: web speech api · ${navigator.language}`)
    );
    panel.setMeta(voiceState.realtime === "connected" ? "Gemini Live" : `mic ${micLabel(voiceState.mic).toLowerCase()}`);
    panel.setTone(voiceState.mic === "denied" || voiceState.mic === "error" ? "danger" : null);
  });

  return {
    root: panel.root,
    destroy() {
      if (waveformFrame) window.cancelAnimationFrame(waveformFrame);
      if (motionMedia) {
        if (motionMedia.removeEventListener) motionMedia.removeEventListener("change", onMotionChange);
        else motionMedia.removeListener(onMotionChange);
      }
    },
  };
}
