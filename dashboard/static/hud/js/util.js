// hud/js/util.js — tiny DOM + formatting helpers shared by every HUD panel.

export function h(tag, attrs = {}, children = []) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "text") el.textContent = value;
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") {
      el.addEventListener(key.slice(2).toLowerCase(), value);
    } else el.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

export function setText(node, value) {
  if (node) node.textContent = value === null || value === undefined ? "" : String(value);
}

export function clear(node) {
  if (node) node.replaceChildren();
  return node;
}

export function emptyNote(message) {
  return h("p", { class: "empty-note", text: message });
}

export function formatClock(date = new Date()) {
  return date.toLocaleTimeString("en-GB", { hour12: false });
}

export function formatDate(date = new Date()) {
  return date
    .toLocaleDateString("en-GB", { weekday: "short", day: "2-digit", month: "short", year: "numeric" })
    .toUpperCase();
}

export function shortTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "--:--:--";
  return date.toLocaleTimeString("en-GB", { hour12: false });
}

export function shortId(id) {
  return id ? String(id).slice(0, 8) : "—";
}

export function clampPercent(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return null;
  return Math.max(0, Math.min(100, Math.round(number)));
}

export function formatBytes(size) {
  const bytes = Number(size);
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}

export function formatRate(kbps) {
  const value = Number(kbps);
  if (!Number.isFinite(value)) return "—";
  if (value >= 1024) return `${(value / 1024).toFixed(2)} MB/s`;
  return `${value.toFixed(1)} KB/s`;
}

export function metricTone(percent) {
  if (percent === null) return null;
  if (percent >= 90) return "danger";
  if (percent >= 70) return "warn";
  return null;
}

export function agoText(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value)) return "—";
  if (value < 60) return `${Math.round(value)}s ago`;
  if (value < 3600) return `${Math.round(value / 60)}m ago`;
  if (value < 86400) return `${Math.round(value / 3600)}h ago`;
  return `${Math.round(value / 86400)}d ago`;
}

export function statusClass(status) {
  const safe = String(status || "unknown").replace(/[^a-z_]/gi, "").toLowerCase();
  return `pill pill--${safe || "unknown"}`;
}

export function statusTone(status) {
  switch (status) {
    case "completed": return "ok";
    case "running": return "accent";
    case "waiting_permission": return "warn";
    case "failed": return "danger";
    case "needs_review": return "accent";
    default: return null;
  }
}

export function prettyStatus(status) {
  return String(status || "unknown")
    .replace(/_/g, " ")
    .toUpperCase();
}
