// hud/js/voice.js — browser microphone, Gemini Live audio, and safe fallback.
// The browser only receives a short-lived WebSocket ticket. Gemini credentials
// remain on Cloud and desktop actions still go through the Mac Agent queue.

const SpeechRecognitionImpl =
  typeof window !== "undefined"
    ? window.SpeechRecognition || window.webkitSpeechRecognition
    : null;

const WAKE_PATTERN = /\b(?:hey\s+)?jarvis\b/i;
const LIVE_INPUT_RATE = 16000;
const LIVE_OUTPUT_RATE = 24000;

export function createVoice(options = {}) {
  const state = {
    mic: "off",              // off | on | muted | denied | error
    wake: "off",             // off | on | unsupported
    speechRecognition: SpeechRecognitionImpl ? "available" : "unsupported",
    realtime: "off",         // off | connecting | connected
    status: "IDLE",          // IDLE | LISTENING | THINKING | SPEAKING | ERROR
    level: 0,
    transcript: "",
    error: "",
  };

  const handlers = new Map();
  const emit = (event, payload) => {
    for (const handler of handlers.get(event) || []) {
      try { handler(payload); } catch (error) {
        console.error(`[HUD] voice handler for '${event}' failed`, error);
      }
    }
  };

  let stream = null;
  let audioContext = null;
  let analyser = null;
  let samples = null;
  let mediaSource = null;
  let realtimeProcessor = null;
  let realtimeSocket = null;
  let reconnectTimer = 0;
  let levelTimer = 0;
  let recognition = null;
  let manualCapture = null;
  let muted = false;
  let realtimeWanted = false;
  let realtimeStartResolve = null;
  let playbackSources = new Set();
  let playbackCursor = 0;

  function publish() {
    emit("state", { ...state });
    if (typeof options.onChange === "function") options.onChange({ ...state });
  }

  function measureLevel() {
    if (!analyser) { state.level = 0; return; }
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
    levelTimer = window.setInterval(() => { measureLevel(); publish(); }, 90);
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
      if (state.wake === "on" && state.realtime === "off") {
        try { recognition.start(); } catch (error) { /* already running */ }
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
      if (audioContext.state === "suspended") await audioContext.resume();
      analyser = audioContext.createAnalyser();
      analyser.fftSize = 1024;
      samples = new Uint8Array(analyser.fftSize);
      mediaSource = audioContext.createMediaStreamSource(stream);
      mediaSource.connect(analyser);
      muted = false;
      state.mic = "on";
      state.error = "";
      startLevelLoop();
      if (state.wake === "on") {
        const engine = ensureRecognition();
        if (engine) { try { engine.start(); } catch (error) { /* already running */ } }
      }
      publish();
      return true;
    } catch (error) {
      state.mic = error && error.name === "NotAllowedError" ? "denied" : "error";
      state.error = (error && error.message) || "Microphone permission failed";
      publish();
      return false;
    }
  }

  function stopPlayback() {
    for (const source of playbackSources) {
      try { source.stop(); } catch (error) { /* already ended */ }
    }
    playbackSources.clear();
    playbackCursor = 0;
  }

  function stopRealtimeCapture() {
    if (!realtimeProcessor) return;
    realtimeProcessor.onaudioprocess = null;
    try { realtimeProcessor.disconnect(); } catch (error) { /* disconnected */ }
    realtimeProcessor = null;
  }

  function downsampleToPcm16(input, inputRate, outputRate = LIVE_INPUT_RATE) {
    if (!input || !input.length) return new ArrayBuffer(0);
    const ratio = inputRate / outputRate;
    const outputLength = Math.max(1, Math.round(input.length / ratio));
    const output = new Int16Array(outputLength);
    for (let index = 0; index < outputLength; index += 1) {
      const start = Math.floor(index * ratio);
      const end = Math.min(input.length, Math.floor((index + 1) * ratio));
      let sum = 0;
      let count = 0;
      for (let sourceIndex = start; sourceIndex < Math.max(start + 1, end); sourceIndex += 1) {
        sum += input[Math.min(sourceIndex, input.length - 1)];
        count += 1;
      }
      const sample = Math.max(-1, Math.min(1, sum / count));
      output[index] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
    }
    return output.buffer;
  }

  function startRealtimeCapture() {
    if (!audioContext || !mediaSource || realtimeProcessor) return;
    realtimeProcessor = audioContext.createScriptProcessor(2048, 1, 1);
    realtimeProcessor.onaudioprocess = (event) => {
      if (muted || !realtimeSocket || realtimeSocket.readyState !== WebSocket.OPEN) return;
      const input = event.inputBuffer.getChannelData(0);
      const pcm = downsampleToPcm16(input, audioContext.sampleRate);
      if (pcm.byteLength) realtimeSocket.send(pcm);
    };
    mediaSource.connect(realtimeProcessor);
    // The processor writes no output, so the destination connection is silent.
    realtimeProcessor.connect(audioContext.destination);
  }

  function playPcm16(buffer) {
    if (!audioContext || !buffer || buffer.byteLength < 2) return;
    const samples16 = new Int16Array(buffer);
    const audioBuffer = audioContext.createBuffer(1, samples16.length, LIVE_OUTPUT_RATE);
    const channel = audioBuffer.getChannelData(0);
    for (let index = 0; index < samples16.length; index += 1) {
      channel[index] = samples16[index] / (samples16[index] < 0 ? 0x8000 : 0x7fff);
    }
    const source = audioContext.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(audioContext.destination);
    const startAt = Math.max(audioContext.currentTime, playbackCursor);
    playbackCursor = startAt + audioBuffer.duration;
    playbackSources.add(source);
    source.onended = () => playbackSources.delete(source);
    source.start(startAt);
  }

  function setRealtimeFailure(detail) {
    realtimeWanted = false;
    state.realtime = "off";
    state.status = "ERROR";
    state.error = detail || "Cloud voice is unavailable";
    stopRealtimeCapture();
    stopPlayback();
    if (realtimeSocket) {
      try { realtimeSocket.close(1011, "voice unavailable"); } catch (error) { /* closed */ }
      realtimeSocket = null;
    }
    publish();
  }

  function handleRealtimeMessage(event) {
    if (event.data instanceof ArrayBuffer || event.data instanceof Blob) {
      if (event.data instanceof Blob) event.data.arrayBuffer().then(playPcm16).catch(() => {});
      else playPcm16(event.data);
      return;
    }
    let payload;
    try { payload = JSON.parse(event.data); } catch (error) { return; }
    if (!payload || typeof payload !== "object") return;
    if (payload.type === "state") {
      if (["LISTENING", "THINKING", "SPEAKING"].includes(payload.state)) state.status = payload.state;
      publish();
      return;
    }
    if (payload.type === "transcript") {
      state.transcript = String(payload.text || "");
      if (payload.speaker === "user") state.status = "THINKING";
      emit("serverTranscript", payload);
      publish();
      return;
    }
    if (payload.type === "error") setRealtimeFailure(String(payload.detail || "Cloud voice is unavailable"));
  }

  function socketUrl(ticket) {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const configured = String(window.JARVIS_CLOUD_URL || "").replace(/^https?/, (value) => value === "https" ? "wss" : "ws");
    const origin = configured || `${protocol}//${window.location.host}`;
    return `${origin}/voice?ticket=${encodeURIComponent(ticket)}`;
  }

  async function startRealtime() {
    realtimeWanted = true;
    if (state.realtime === "connected" || state.realtime === "connecting") return true;
    if (!(await enable())) return false;
    if (typeof options.getSessionTicket !== "function" || typeof WebSocket === "undefined") {
      setRealtimeFailure("Cloud voice is unavailable in this browser");
      return false;
    }
    state.realtime = "connecting";
    state.status = "THINKING";
    state.error = "";
    publish();
    try {
      const ticket = await options.getSessionTicket();
      if (!realtimeWanted) return false;
      await new Promise((resolve, reject) => {
        realtimeStartResolve = { resolve, reject };
        let connectionHadOpened = false;
        const socket = new WebSocket(socketUrl(ticket));
        realtimeSocket = socket;
        socket.binaryType = "arraybuffer";
        socket.onopen = () => {
          connectionHadOpened = true;
          state.realtime = "connected";
          state.status = "LISTENING";
          state.error = "";
          startRealtimeCapture();
          publish();
          if (realtimeStartResolve) realtimeStartResolve.resolve(true);
          realtimeStartResolve = null;
        };
        socket.onmessage = handleRealtimeMessage;
        socket.onerror = () => {
          if (realtimeStartResolve) realtimeStartResolve.reject(new Error("Cloud voice connection failed"));
        };
        socket.onclose = () => {
          stopRealtimeCapture();
          realtimeSocket = null;
          if (realtimeStartResolve) {
            realtimeStartResolve.reject(new Error("Cloud voice connection closed"));
            realtimeStartResolve = null;
          }
          if (realtimeWanted && connectionHadOpened) {
            state.realtime = "off";
            state.status = "ERROR";
            state.error = "Cloud voice connection lost; reconnecting";
            publish();
            reconnectTimer = window.setTimeout(() => startRealtime().catch(() => {}), 2000);
          } else {
            state.realtime = "off";
            state.status = "IDLE";
            publish();
          }
        };
      });
      return true;
    } catch (error) {
      if (realtimeSocket) {
        try { realtimeSocket.close(); } catch (closeError) { /* closed */ }
      }
      realtimeSocket = null;
      setRealtimeFailure((error && error.message) || "Cloud voice connection failed");
      return false;
    }
  }

  function stopRealtime() {
    realtimeWanted = false;
    if (reconnectTimer) window.clearTimeout(reconnectTimer);
    reconnectTimer = 0;
    stopRealtimeCapture();
    stopPlayback();
    if (realtimeStartResolve) {
      realtimeStartResolve.reject(new Error("Voice stopped"));
      realtimeStartResolve = null;
    }
    if (realtimeSocket) {
      try { realtimeSocket.send(JSON.stringify({ type: "stop" })); } catch (error) { /* closed */ }
      try { realtimeSocket.close(1000, "operator stopped"); } catch (error) { /* closed */ }
      realtimeSocket = null;
    }
    state.realtime = "off";
    state.status = "IDLE";
    publish();
  }

  function interrupt() {
    stopPlayback();
    if (realtimeSocket && realtimeSocket.readyState === WebSocket.OPEN) {
      realtimeSocket.send(JSON.stringify({ type: "interrupt" }));
      state.status = "LISTENING";
      publish();
    }
  }

  function disable() {
    stopRealtime();
    stopLevelLoop();
    if (manualCapture) {
      const resolve = manualCapture;
      manualCapture = null;
      resolve("");
    }
    if (recognition) {
      try { recognition.stop(); } catch (error) { /* not running */ }
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
      mediaSource = null;
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
      try { recognition.stop(); } catch (error) { /* not running */ }
    }
    if (enabled && stream && state.realtime === "off") {
      const engine = ensureRecognition();
      if (engine) { try { engine.start(); } catch (error) { /* already running */ } }
    }
    publish();
    return state.wake;
  }

  function listenOnce(timeoutMs = 9000) {
    if (!stream) return Promise.resolve("");
    const engine = ensureRecognition();
    if (!engine) return Promise.resolve("");
    state.status = "LISTENING";
    publish();
    return new Promise((resolve) => {
      manualCapture = (text) => { state.status = "IDLE"; publish(); resolve(text || ""); };
      window.setTimeout(() => {
        if (!manualCapture) return;
        const finish = manualCapture;
        manualCapture = null;
        state.status = "IDLE";
        publish();
        finish("");
      }, timeoutMs);
      try { engine.start(); } catch (error) { /* already running */ }
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
    startRealtime,
    stopRealtime,
    interrupt,
    isRealtime: () => state.realtime === "connected" || state.realtime === "connecting",
    on,
    readLevel: () => { measureLevel(); return state.level; },
    setStatus: (status) => { state.status = status; publish(); },
    setTranscript: (text) => { state.transcript = text; publish(); },
    hasAnalyser: () => Boolean(analyser),
    analyser: () => analyser,
  };
}
