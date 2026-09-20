// hud/js/controller.js — data layer: polls/streams the real Cloud API, derives
// the reactor state from live task state and exposes the only actions the HUD
// is allowed to perform (create task, approve/deny a blocked step, cancel).

import { api, authToken } from "./api.js";
import { store, selectors, finishedAgo } from "./store.js";

const INTERVALS = {
  tasks: 2500,
  devices: 6000,
  status: 4000,
  metrics: 2000,
  activitySafety: 10000,
};

const RECENT_OUTCOME_MS = 8000;
const MAX_EVENTS = 400;

function every(milliseconds, task) {
  let stopped = false;
  let timer = 0;
  const run = async () => {
    try {
      await task();
    } catch (error) {
      console.warn("[HUD] poll failed", error.message);
    }
    if (!stopped) timer = window.setTimeout(run, milliseconds);
  };
  run();
  return () => {
    stopped = true;
    window.clearTimeout(timer);
  };
}

function eventKey(event) {
  return event.key !== undefined ? event.key : event.seq;
}

function deriveReactor(state) {
  if (!state.online) return { state: "OFFLINE", detail: "cloud link lost" };

  const permission = selectors.permissionTask(state);
  if (permission) {
    const request = permission.permission_request || {};
    return {
      state: "PERMISSION",
      detail: `approval required · ${request.action || "blocked step"}`,
    };
  }

  const running = state.tasks.filter((task) => task.status === "running");
  if (running.length) {
    return { state: "EXECUTING", detail: `agent executing · ${running.length} task(s)` };
  }

  const pending = state.tasks.filter((task) =>
    ["planning", "queued", "recovering", "planned"].includes(task.status)
  );
  if (pending.length) {
    const planning = pending.some((task) => task.status === "planning");
    return {
      state: "THINKING",
      detail: planning ? "planner working" : `queued for agent · ${pending.length}`,
    };
  }

  if (state.voice.status === "LISTENING") {
    return { state: "LISTENING", detail: "microphone open" };
  }

  const recent = state.tasks
    .filter((task) => finishedAgo(task) < RECENT_OUTCOME_MS)
    .sort((a, b) => String(b.completed_at).localeCompare(String(a.completed_at)));

  if (recent.length) {
    const latest = recent[0];
    if (latest.status === "completed") return { state: "SUCCESS", detail: "task completed" };
    if (latest.status === "needs_review") return { state: "ERROR", detail: "task needs review" };
    if (latest.status === "failed") return { state: "ERROR", detail: "task failed" };
  }

  return { state: "IDLE", detail: "all systems nominal" };
}

export function createController({ voice, onEvent } = {}) {
  const state = store.state;
  let localSeq = 0;
  let streamController = null;
  let sseActive = false;
  const stoppables = [];

  function pushEvents(incoming) {
    const list = Array.isArray(incoming) ? incoming : [];
    if (!list.length) return;

    const known = new Set(state.events.map(eventKey));
    const additions = list.filter((event) => !known.has(eventKey(event)));
    if (!additions.length) return;

    additions.sort((a, b) => Number(a.seq || 0) - Number(b.seq || 0));
    const events = [...state.events, ...additions].slice(-MAX_EVENTS);
    const highest = additions.reduce(
      (max, event) => (Number.isFinite(Number(event.seq)) ? Math.max(max, Number(event.seq)) : max),
      state.cursor
    );

    store.set({ events, cursor: highest });
    if (typeof onEvent === "function") additions.forEach(onEvent);
  }

  function logLocal(category, message, level = "info") {
    localSeq += 1;
    pushEvents([
      {
        key: `local-${localSeq}`,
        category,
        level,
        message,
        at: new Date().toISOString(),
        data: { local: true },
      },
    ]);
  }

  function applyDerived() {
    store.set({
      reactor: deriveReactor(state),
      activeTask:
        selectors.activeTasks(state)[0] || selectors.recentTasks(state, 1)[0] || null,
      permissionTask: selectors.permissionTask(state),
    });
  }

  async function loadTasks() {
    const tasks = await api.tasks();
    const counts = tasks.reduce((accumulator, task) => {
      const status = task.status || "unknown";
      accumulator[status] = (accumulator[status] || 0) + 1;
      return accumulator;
    }, {});
    store.set({ tasks, counts, online: true, lastError: null, updatedAt: new Date().toISOString() });
    applyDerived();
  }

  async function loadDevices() {
    const payload = await api.devices();
    store.set({ devices: payload.devices || [] });
    applyDerived();
  }

  async function loadSystemStatus() {
    const payload = await api.systemStatus();
    store.set({ systemStatus: payload });
  }

  async function loadMetrics() {
    const payload = await api.systemMetrics();
    store.set({ metrics: payload });
  }

  async function loadFiles() {
    const payload = await api.files();
    store.set({ files: payload.files || [] });
  }

  async function pollActivity() {
    const payload = await api.activity(state.cursor);
    pushEvents(payload.events || []);
  }

  function startActivityStream() {
    if (typeof fetch === "undefined" || typeof ReadableStream === "undefined") return;
    streamController = new AbortController();
    const token = authToken();
    const headers = token ? { Authorization: `Bearer ${token}` } : {};
    (async () => {
      try {
        const response = await fetch(api.streamUrl(state.cursor), {
          headers,
          signal: streamController.signal,
        });
        if (!response.ok || !response.body) throw new Error(`stream ${response.status}`);
        sseActive = true;
        store.set({ sseConnected: true });
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const frames = buffer.split("\n\n");
          buffer = frames.pop() || "";
          for (const frame of frames) {
            const line = frame.split("\n").find((item) => item.startsWith("data:"));
            if (!line) continue;
            try { pushEvents([JSON.parse(line.slice(5).trim())]); } catch (error) { /* ignore malformed frame */ }
          }
        }
      } catch (error) {
        if (error.name !== "AbortError") console.warn("[HUD] activity stream unavailable", error.message);
      } finally {
        sseActive = false;
        store.set({ sseConnected: false });
      }
    })();
  }

  // ---------------------------------------------------------------- actions

  async function submitCommand(goal, device) {
    const text = String(goal || "").trim();
    if (!text) return null;
    const target = device || selectors.primaryDevice(state);
    try {
      const task = await api.createTask(text, target);
      logLocal("USER", `Task submitted: ${text.slice(0, 90)} → ${task.device}`);
      await loadTasks();
      return task;
    } catch (error) {
      logLocal("ERROR", `Command rejected: ${error.message}`, "error");
      throw error;
    }
  }

  async function approvePermission(task, action, parameters) {
    try {
      await api.approvePermission(task.id, action, parameters);
      logLocal("PERM", `Approved ${action} for task ${String(task.id).slice(0, 8)}`);
      await loadTasks();
      await pollActivity();
    } catch (error) {
      logLocal("ERROR", `Approval failed: ${error.message}`, "error");
      throw error;
    }
  }

  async function denyPermission(task) {
    try {
      await api.denyPermission(task.id);
      logLocal("PERM", `Denied request for task ${String(task.id).slice(0, 8)}`);
      await loadTasks();
      await pollActivity();
    } catch (error) {
      logLocal("ERROR", `Denial failed: ${error.message}`, "error");
      throw error;
    }
  }

  async function cancelTask(taskId) {
    try {
      await api.cancelTask(taskId);
      logLocal("USER", `Task cancelled: ${String(taskId).slice(0, 8)}`);
      await loadTasks();
    } catch (error) {
      logLocal("ERROR", `Cancel failed: ${error.message}`, "error");
      throw error;
    }
  }

  async function interrupt() {
    const active = selectors.activeTasks(state);
    if (!active.length) {
      logLocal("SYS", "Interrupt ignored — no active task");
      return null;
    }
    const target = active[0];
    logLocal("USER", `INTERRUPT — cancelling ${String(target.id).slice(0, 8)}`);
    await cancelTask(target.id);
    return target;
  }

  async function submitVoiceCommand(text) {
    if (voice && voice.setStatus) voice.setStatus("PROCESSING");
    if (voice && voice.setTranscript) voice.setTranscript(text);
    logLocal("USER", `Voice command captured: ${String(text).slice(0, 90)}`);
    try {
      return await submitCommand(text);
    } finally {
      window.setTimeout(() => {
        if (voice && voice.setStatus) voice.setStatus("IDLE");
      }, 1200);
    }
  }

  async function uploadFile(file) {
    const result = await api.upload(file);
    logLocal("DEV", `Upload stored: ${result.name} (${result.size} bytes)`);
    await loadFiles();
    return result;
  }

  async function refreshAll() {
    try {
      await Promise.all([loadTasks(), loadDevices(), loadSystemStatus(), loadMetrics(), loadFiles()]);
      store.set({ online: true });
    } catch (error) {
      store.set({ online: false, lastError: error.message });
      applyDerived();
      throw error;
    }
  }

  function start() {
    stoppables.push(every(INTERVALS.tasks, loadTasks));
    stoppables.push(every(INTERVALS.devices, loadDevices));
    stoppables.push(every(INTERVALS.status, loadSystemStatus));
    stoppables.push(every(INTERVALS.metrics, loadMetrics));
    stoppables.push(every(INTERVALS.activitySafety, pollActivity));
    loadFiles().catch(() => {});
    startActivityStream();
    logLocal("SYS", "Command center HUD connected");
    return stop;
  }

  function stop() {
    stoppables.splice(0).forEach((stopPolling) => stopPolling());
    if (streamController) {
      streamController.abort();
      streamController = null;
    }
    sseActive = false;
  }

  return {
    start,
    stop,
    refreshAll,
    submitCommand,
    submitVoiceCommand,
    approvePermission,
    denyPermission,
    cancelTask,
    interrupt,
    uploadFile,
    loadFiles,
    logLocal,
    isStreaming: () => sseActive,
  };
}
