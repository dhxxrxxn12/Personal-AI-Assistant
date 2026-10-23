// hud/js/api.js — the single place that talks to the JARVIS Cloud API.
// Every endpoint here already exists in cloud/server.py; the HUD never executes
// Mac actions itself, it only creates tasks / approves blocked steps.

const JSON_HEADERS = { "Content-Type": "application/json" };
const AUTH_TOKEN_KEY = "jarvis_token";
const API_BASE = String(window.JARVIS_CLOUD_URL || "").replace(/\/$/, "");

export function authToken() {
  try {
    return window.sessionStorage.getItem(AUTH_TOKEN_KEY) || "";
  } catch (error) {
    return "";
  }
}

export function storeAuthToken(token) {
  window.sessionStorage.setItem(AUTH_TOKEN_KEY, String(token || ""));
}

export async function request(path, options = {}) {
  const token = authToken();
  const headers = new Headers(options.headers || {});
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (!response.ok) {
    let detail = response.statusText || "request failed";
    try {
      const body = await response.json();
      detail = body.detail || JSON.stringify(body);
    } catch (error) {
      /* non-JSON error body — keep the status text */
    }
    if (response.status === 401 && !window.location.pathname.endsWith("/login")) {
      const next = `${window.location.pathname}${window.location.search}`;
      window.location.assign(`/login?next=${encodeURIComponent(next)}`);
    }
    throw new Error(`${response.status} ${detail}`);
  }
  if (response.status === 204) return null;
  return response.json();
}

export const api = {
  health: () => request("/health"),
  actions: () => request("/actions"),
  memoryStatus: () => request("/memory/status"),

  devices: () => request("/devices"),
  device: (id) => request(`/devices/${encodeURIComponent(id)}`),
  registerDevice: (deviceId, deviceType, name, capabilities = [], metadata = {}) =>
    request("/devices/register", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({
        device_id: deviceId,
        device_type: deviceType,
        name: name || deviceId,
        capabilities,
        metadata,
      }),
    }),

  tasks: () => request("/tasks"),
  task: (id) => request(`/tasks/${encodeURIComponent(id)}`),
  taskSummary: () => request("/tasks/status/summary"),
  createTask: (goal, device) =>
    request("/tasks", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ goal, device }),
    }),
  cancelTask: (id) =>
    request(`/tasks/${encodeURIComponent(id)}/cancel`, { method: "POST" }),

  engineeringTasks: () => request("/engineering/tasks"),
  engineeringDiagnostics: () => request("/engineering/diagnostics"),
  engineeringTask: (id) => request(`/engineering/tasks/${encodeURIComponent(id)}`),
  engineeringEvents: (id) => request(`/engineering/tasks/${encodeURIComponent(id)}/events`),
  createEngineeringTask: (goal, candidatePlan) =>
    request("/engineering/tasks", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ goal, candidate_plan: candidatePlan || null }),
    }),
  approveEngineeringTask: (id) =>
    request(`/engineering/tasks/${encodeURIComponent(id)}/approve`, {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ approved: true }),
    }),
  cancelEngineeringTask: (id) =>
    request(`/engineering/tasks/${encodeURIComponent(id)}/cancel`, { method: "POST" }),
  rollbackEngineeringTask: (id) =>
    request(`/engineering/tasks/${encodeURIComponent(id)}/rollback`, { method: "POST" }),

  permission: (id) => request(`/tasks/${encodeURIComponent(id)}/permission`),
  approvePermission: (id, action, parameters) =>
    request(`/tasks/${encodeURIComponent(id)}/permission/approve`, {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ action, parameters }),
    }),
  denyPermission: (id) =>
    request(`/tasks/${encodeURIComponent(id)}/permission/deny`, { method: "POST" }),

  systemMetrics: () => request("/system/metrics"),
  systemStatus: () => request("/system/status"),
  voiceSession: () => request("/voice/session", { method: "POST" }),

  activity: (since = 0, limit = 200) =>
    request(`/activity?since=${encodeURIComponent(since)}&limit=${encodeURIComponent(limit)}`),

  streamUrl: (since = 0) => `/activity/stream?since=${encodeURIComponent(since)}`,

  files: () => request("/files"),
  upload: (file) => {
    const body = new FormData();
    body.append("file", file);
    return request("/files/upload", { method: "POST", body });
  },
};
