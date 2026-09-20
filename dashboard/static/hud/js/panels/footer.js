// hud/js/panels/footer.js — footer readout: live reactor state summary.

import { setText } from "../util.js";
import { reactorLabel } from "../labels.js";

export function mount(ctx) {
  const node = document.getElementById("footerStatus");
  let lastLabel = null;

  ctx.store.subscribe((state) => {
    const label = reactorLabel(state.reactor.state);
    if (label !== lastLabel) {
      setText(node, label);
      lastLabel = label;
    }
    if (node) node.dataset.state = state.reactor.state;
  });

  return { root: node || document.createTextNode("") };
}