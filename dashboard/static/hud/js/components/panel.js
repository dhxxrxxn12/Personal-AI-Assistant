// hud/js/components/panel.js — reusable HUD panel frame + row builders.
// Panels are built from these primitives so every surface shares the same
// cut-corner frame, header and typography.

import { h, statusClass, prettyStatus } from "../util.js";

export function createPanel({ title, meta = "", tone = null, className = "" }) {
  const metaNode = h("span", { class: "panel-head__meta", text: meta });
  const body = h("div", { class: "panel-body" });
  const head = h("header", { class: "panel-head" }, [
    h("h2", { text: title }),
    metaNode,
  ]);
  const root = h("section", { class: `panel-frame ${className}`.trim() }, [
    h("div", { class: "panel" }, [head, body]),
  ]);

  return {
    root,
    body,
    setMeta(text) {
      metaNode.textContent = text ?? "";
    },
    setTone(next) {
      if (next) root.dataset.tone = next;
      else delete root.dataset.tone;
    },
  };
}

export function mountPanel(slotName, options) {
  const slot = document.querySelector(`[data-slot="${slotName}"]`);
  const panel = createPanel(options);
  if (slot) slot.append(panel.root);
  return panel;
}

export function metricRow({ label, value = "—", note = "", percent = null, tone = null, tier = 1, na = false }) {
  const row = h("div", { class: "metric", dataset: { tier: String(tier) } }, [
    h("span", { class: "metric__label", text: label }),
    h("span", { class: `metric__value${na ? " is-na" : ""}` }, [
      String(value),
      note ? h("small", { text: note }) : null,
    ]),
  ]);

  if (percent !== null) {
    row.append(
      h("div", { class: "bar", dataset: tone ? { tone } : {} }, [
        h("div", { class: "bar__fill", style: `width:${percent}%` }),
      ])
    );
  }
  return row;
}

export function statusPill(status) {
  return h("span", { class: statusClass(status), text: prettyStatus(status) });
}

export function headRow(labels, gridClass) {
  return h(
    "div",
    { class: `hud-table__head ${gridClass}` },
    labels.map((label) => h("span", { text: label }))
  );
}

export function tableRow(cells, { gridClass, tone = null } = {}) {
  const row = h("div", { class: `hud-table__row ${gridClass}`.trim() });
  if (tone) row.dataset.tone = tone;
  for (const cell of cells) {
    row.append(
      h("span", { class: cell.class || "", text: cell.text ?? "" })
    );
  }
  return row;
}

export function statusLine(label, value, state = null) {
  return h("div", { class: "status-line" }, [
    h("span", { text: label }),
    h("b", { text: value, dataset: state ? { state } : {} }),
  ]);
}

export function coreRow(label, value, state = null) {
  return h("div", { class: "core-row" }, [
    h("span", { text: label }),
    h("b", { text: value, class: state ? `is-${state}` : "" }),
  ]);
}