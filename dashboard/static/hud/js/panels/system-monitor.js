// hud/js/panels/system-monitor.js — real host telemetry from /system/metrics.
// Sensors this host cannot report stay "N/A" — nothing here is invented.

import { h, clampPercent, metricTone, formatRate, emptyNote } from "../util.js";
import { mountPanel, metricRow } from "../components/panel.js";

const NA = "N/A";

const percentValue = (block, key) => {
  const raw = block[key];
  return block.available && raw !== null && raw !== undefined ? `${raw}%` : NA;
};

const ROWS = (metrics) => {
  const cpu = metrics.cpu || {};
  const memory = metrics.memory || {};
  const net = metrics.network || {};
  const gpu = metrics.gpu || {};
  const temp = metrics.temperature || {};
  const disk = metrics.disk || {};
  const uptime = metrics.uptime || {};
  const osBlock = metrics.os || {};

  const cpuPercent = clampPercent(cpu.percent);
  const memoryPercent = clampPercent(memory.percent);
  const diskPercent = clampPercent(disk.percent);
  const gpuPercent = clampPercent(gpu.percent);

  const cpuNote = [
    cpu.logical_cores ? `${cpu.logical_cores}c` : "",
    cpu.frequency_mhz ? `${Math.round(cpu.frequency_mhz)}MHz` : "",
  ].filter(Boolean).join(" @ ");

  const processes = metrics.process_count;

  return [
    metricRow({
      label: "CPU",
      value: percentValue(cpu, "percent"),
      na: !cpu.available,
      note: cpuNote,
      percent: cpuPercent,
      tone: metricTone(cpuPercent),
      tier: 1,
    }),
    metricRow({
      label: "MEMORY",
      value: percentValue(memory, "percent"),
      na: !memory.available,
      note: memory.available ? `${memory.used_gb}/${memory.total_gb} GB` : "",
      percent: memoryPercent,
      tone: metricTone(memoryPercent),
      tier: 1,
    }),
    metricRow({
      label: "NETWORK",
      // rx_kbps is null until the host has two samples to diff — show N/A
      // rather than inventing a rate.
      value: net.available && net.rx_kbps !== null && net.rx_kbps !== undefined ? formatRate(net.rx_kbps) : NA,
      na: !net.available || net.rx_kbps === null || net.rx_kbps === undefined,
      note: net.available && net.tx_kbps !== null && net.tx_kbps !== undefined ? `↑ ${formatRate(net.tx_kbps)}` : "",
      tier: 1,
    }),
    metricRow({
      label: "GPU",
      value: percentValue(gpu, "percent"),
      na: !gpu.available,
      note: gpu.available ? "" : "no NVML",
      percent: gpuPercent,
      tone: metricTone(gpuPercent),
      tier: 1,
    }),
    metricRow({
      label: "TEMP",
      value: temp.available && temp.celsius !== null && temp.celsius !== undefined ? `${temp.celsius}°C` : NA,
      na: !temp.available,
      note: temp.available ? "" : "no sensor",
      tier: 1,
    }),
    metricRow({
      label: "UPTIME",
      value: uptime.human || NA,
      na: !uptime.human,
      tier: 2,
    }),
    metricRow({
      label: "PROCESSES",
      value: processes === null || processes === undefined ? NA : processes,
      na: processes === null || processes === undefined,
      tier: 2,
    }),
    metricRow({
      label: "OS",
      value: osBlock.system ? `${osBlock.system} ${osBlock.release || ""}`.trim() : NA,
      na: !osBlock.system,
      note: osBlock.hostname || "",
      tier: 2,
    }),
    metricRow({
      label: "ARCH",
      value: osBlock.architecture || osBlock.machine || NA,
      na: !(osBlock.architecture || osBlock.machine),
      note: osBlock.processor && osBlock.processor !== osBlock.architecture ? osBlock.processor : "",
      tier: 2,
    }),
    metricRow({
      label: "DISK",
      value: percentValue(disk, "percent"),
      na: !disk.available,
      note: disk.available ? `${disk.used_gb}/${disk.total_gb} GB` : "",
      percent: diskPercent,
      tone: metricTone(diskPercent),
      tier: 2,
    }),
    metricRow({
      label: "HOST",
      value: net.hostname || osBlock.hostname || NA,
      na: !(net.hostname || osBlock.hostname),
      note: net.ip && net.ip !== "127.0.0.1" ? net.ip : "",
      tier: 2,
    }),
  ];
};

export function mount(ctx) {
  const panel = mountPanel("system-monitor", { title: "System Monitor", meta: "host telemetry" });
  const list = h("div", { class: "metric-list" });
  panel.body.append(list);

  ctx.store.subscribe((state) => {
    const metrics = state.metrics;
    if (!metrics) {
      list.replaceChildren(emptyNote("awaiting telemetry"));
      panel.setMeta("--");
      return;
    }
    list.replaceChildren(...ROWS(metrics));
    panel.setMeta(
      metrics.available
        ? `sampled ${String(metrics.collected_at || "").slice(11, 19)}z`
        : "host metrics unavailable"
    );
  });

  return { root: panel.root };
}