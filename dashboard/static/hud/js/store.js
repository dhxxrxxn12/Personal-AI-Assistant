// hud/js/store.js — one observable state container for every HUD panel.
// Panels never fetch directly: the controller fills this store from the real
// Cloud API and each panel renders from the state it receives.

const listeners = new Set();

const initialState = {
  online: false,                // Cloud reachable
  lastError: null,
  updatedAt: null,

  metrics: null,                // GET /system/metrics
  systemStatus: null,           // GET /system/status
  devices: [],                  // GET /devices
  tasks: [],                    // GET /tasks
  engineeringTasks: [],        // GET /engineering/tasks
  counts: {},                   // task status histogram
  files: [],                    // GET /files
  events: [],                   // activity feed (from SSE or GET /activity)
  cursor: 0,
  sseConnected: false,

  voice: {                      // filled by voice.js (mic + Cloud Live state)
    mic: "off",                 // off | on | muted | denied | unsupported
    wake: "off",                // off | on | unsupported
    realtime: "off",            // off | connecting | connected
    status: "IDLE",             // IDLE | LISTENING | THINKING | SPEAKING | ERROR
    level: 0,
    transcript: "",
  },

  reactor: { state: "OFFLINE", detail: "no cloud link" },
  activeTask: null,
  permissionTask: null,         // task currently waiting_permission
};

export const store = {
  state: { ...initialState },

  set(patch) {
    Object.assign(this.state, patch);
    this.updatedAt = Date.now();
    this.emit();
  },

  emit() {
    for (const listener of listeners) {
      try {
        listener(this.state);
      } catch (error) {
        console.error("[HUD] panel update failed", error);
      }
    }
  },

  subscribe(listener) {
    listeners.add(listener);
    try {
      listener(this.state);
    } catch (error) {
      console.error("[HUD] initial panel render failed", error);
    }
    return () => listeners.delete(listener);
  },
};

export const selectors = {
  permissionTask(state) {
    return state.tasks.find(
      (task) => task.status === "waiting_permission" && task.permission_request?.status === "pending"
    ) || null;
  },

  recentTasks(state, limit = 7) {
    return [...state.tasks]
      .sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")))
      .slice(0, limit);
  },

  activeTasks(state) {
    return state.tasks.filter((task) =>
      ["planning", "queued", "running", "recovering", "waiting_permission"].includes(task.status)
    );
  },

  onlineDevices(state) {
    return state.devices.filter((device) => device.status === "online");
  },

  primaryDevice(state) {
    const online = selectors.onlineDevices(state);
    return (online[0] || state.devices[0] || { device_id: "mac-main" }).device_id;
  },
};

// Milliseconds since a task finished — used for short-lived SUCCESS/ERROR states.
export function finishedAgo(task) {
  if (!task || !task.completed_at) return Infinity;
  const when = new Date(task.completed_at).getTime();
  if (Number.isNaN(when)) return Infinity;
  return Date.now() - when;
}
