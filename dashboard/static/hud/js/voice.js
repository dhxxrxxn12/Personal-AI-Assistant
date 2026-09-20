// hud/js/voice.js — real browser microphone state for the SPEECH panel.
//
// The PyQt app owns its own native mic/wake-word pipeline (core/stt.py,
// core/wake_word.py) which runs in a separate process; this HUD therefore uses
// the browser's own MediaStream + Web Speech API.  Every value it reports is
// measured locally — permission denials, mute state and unsupported APIs are
// surfaced honestly instead of being faked.

const SpeechRecognitionImpl =
  typeof window !== "undefined"
    ? window.SpeechRecognition || window.webkitSpeechRecognition
    : null;

const WAKE_PATTERN = /\b(?:hey\s+)?jarvis\b/i;

export function createVoice(options = {}) {
  const state = {
    mic: "off",              // off | on | muted | denied | error
    wake: "off",             // off | on | unsupported
    speechRecognition: SpeechRecognitionImpl ? "available" : "unsupported",
    status: "IDLE",          // IDLE | LISTENING | PROCESSING
    level: 0,
    transcript: "",
    error: "",
  };

  const handlers = new Map();
  const emit = (event, payload) => {
    for (const handler of handlers.get(event) || []) {
      try {
        handler(payload);
      } catch (error) {
        console.error(`[HUD] voice handler for '${event}' failed`, error);
      }
    }
  };

  let stream = null;
  let audioContext = null;
  let analyser = null;
  let samples = null;
  let recognition = null;
  let levelTimer = 0;
  let manualCapture = null;
  let muted = false;

  function publish() {
    emit("state", { ...state });
    if (typeof options.onChange === "function") options.onChange({ ...state });
  }

  function measureLevel() {
    if (!analyser) {
      state.level = 0;
      return;
    }
    analyser.getByteTimeDomainData(samples);
    let sum = 0;
    for (let index = 0; index < samples.length; index += 1) {
      const centred = (samples[index] - 128) / 128;
      sum += centred * centred;
    }
    state.level = Math.min(1, Math.sqrt(sum / samples.length) * 5.5);
  }

  function startLevelLoop() {
    stopLevelLoop();
    levelTimer = window.setInterval(() => {
      measureLevel();
      publish();
    }, 90);
  }

  function stopLevelLoop() {
    if (levelTimer) window.clearInterval(levelTimer);
    levelTimer = 0;
  }

  function ensureRecognition() {
    if (!SpeechRecognitionImpl) return null;
    if (recognition) return recognition;

    recognition = new SpeechRecognitionImpl();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = navigator.language || "en-US";

    recognition.onresult = (event) => {
      let interim = "";
      let final = "";
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const chunk = event.results[index][0].transcript;
        if (event.results[index].isFinal) final += chunk;
        else interim += chunk;
      }
      const heard = (final || interim).trim();
      if (!heard) return;

      if (state.wake === "on" && WAKE_PATTERN.test(heard)) {
        state.transcript = "";
        state.status = "LISTENING";
        publish();
        emit("wake", { phrase: heard });
        const remainder = heard.replace(WAKE_PATTERN, "").replace(/^[\s,.:;-]+/, "").trim();
        if (final && remainder) emit("transcript", { text: remainder, source: "wake" });
        return;
      }

      if (manualCapture) {
        state.transcript = heard;
        state.status = "LISTENING";
        publish();
        if (final) {
          const resolve = manualCapture;
          manualCapture = null;
          resolve(heard);
        }
      }
    };

    recognition.onerror = (event) => {
      state.error = event.error || "recognition error";
      if (event.error === "not-allowed" || event.error === "service-not-allowed") {
        if (state.wake === "on") state.wake = "unsupported";
      }
      publish();
    };

    recognition.onend = () => {
      // Browsers stop continuous recognition after silence — restart the gate.
      if (state.wake === "on") {
        try {
          recognition.start();
        } catch (error) {
          /* already running */
        }
      }
    };

    return recognition;
  }

  async function enable() {
    if (stream) return true;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      state.mic = "error";
      state.error = "getUserMedia unavailable (HTTPS or localhost required)";
      publish();
      return false;
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      audioContext = new AudioCtx();
      analyser = audioContext.createAnalyser();
      analyser.fftSize = 1024;
      samples = new Uint8Array(analyser.fftSize);
      audioContext.createMediaStreamSource(stream).connect(analyser);
      muted = false;
      state.mic = "on";
      state.error = "";
      startLevelLoop();
      if (state.wake === "on") {
        const engine = ensureRecognition();
        if (engine) {
          try {
            engine.start();
          } catch (error) {
            /* already running */
          }
        }
      }
      publish();
      return true;
    } catch (error) {
      state.mic = error && error.name === "NotAllowedError" ? "denied" : "error";
      state.error = (error && error.message) || String(error);
      publish();
      return false;
    }
  }

  function disable() {
    stopLevelLoop();
    if (manualCapture) {
      const resolve = manualCapture;
      manualCapture = null;
      resolve("");
    }
    if (recognition) {
      try {
        recognition.stop();
      } catch (error) {
        /* not running */
      }
    }
    if (stream) {
      stream.getTracks().forEach((track) => track.stop());
      stream = null;
    }
    if (audioContext) {
      audioContext.close().catch(() => {});
      audioContext = null;
      analyser = null;
      samples = null;
    }
    muted = false;
    state.mic = "off";
    state.level = 0;
    state.status = "IDLE";
    publish();
  }

  function toggleMute() {
    if (!stream) return;
    muted = !muted;
    stream.getAudioTracks().forEach((track) => { track.enabled = !muted; });
    state.mic = muted ? "muted" : "on";
    if (muted) state.level = 0;
    publish();
  }

  function setWake(enabled) {
    if (enabled && !SpeechRecognitionImpl) {
      state.wake = "unsupported";
      state.error = "Web Speech API unavailable in this browser";
      publish();
      return state.wake;
    }
    state.wake = enabled ? "on" : "off";
    if (!enabled && recognition) {
      try {
        recognition.stop();
      } catch (error) {
        /* not running */
      }
    }
    if (enabled && stream) {
      const engine = ensureRecognition();
      if (engine) {
        try {
          engine.start();
        } catch (error) {
          /* already running */
        }
      }
    }
    publish();
    return state.wake;
  }

  // One-shot capture used by the SPEECH panel's LISTEN button.  Resolves with
  // the recognised text, or an empty string when nothing was heard.
  function listenOnce(timeoutMs = 9000) {
    if (!stream) return Promise.resolve("");
    const engine = ensureRecognition();
    if (!engine) return Promise.resolve("");
    state.status = "LISTENING";
    publish();

    return new Promise((resolve) => {
      manualCapture = (text) => {
        state.status = "IDLE";
        publish();
        resolve(text || "");
      };
      window.setTimeout(() => {
        if (!manualCapture) return;
        const finish = manualCapture;
        manualCapture = null;
        state.status = "IDLE";
        publish();
        finish("");
      }, timeoutMs);

      try {
        engine.start();
      } catch (error) {
        /* continuous recognition may already be running */
      }
    });
  }

  function on(event, handler) {
    if (!handlers.has(event)) handlers.set(event, []);
    handlers.get(event).push(handler);
    return () => {
      const list = handlers.get(event) || [];
      const index = list.indexOf(handler);
      if (index >= 0) list.splice(index, 1);
    };
  }

  return {
    state: () => ({ ...state }),
    enable,
    disable,
    toggleMute,
    setWake,
    listenOnce,
    on,
    readLevel: () => {
      measureLevel();
      return state.level;
    },
    setStatus: (status) => {
      state.status = status;
      publish();
    },
    setTranscript: (text) => {
      state.transcript = text;
      publish();
    },
    hasAnalyser: () => Boolean(analyser),
    analyser: () => analyser,
  };
}

