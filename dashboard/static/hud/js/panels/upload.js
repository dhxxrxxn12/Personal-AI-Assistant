// hud/js/panels/upload.js — HUD file drop wired to POST /files/upload.
// The uploaded path is then handed to the normal task system, so the Mac agent
// (and its existing file_processor action) is what actually touches the file.

import { h, setText, formatBytes, emptyNote, agoText } from "../util.js";
import { mountPanel } from "../components/panel.js";

export function mount(ctx) {
  const { store, controller } = ctx;
  const panel = mountPanel("upload", { title: "File Upload", meta: "cloud drop" });

  const input = h("input", { type: "file", style: "display:none" });
  const dropzone = h(
    "div",
    { class: "dropzone", role: "button", tabindex: "0", title: "Drop a file or click to browse" },
    [
      h("div", { class: "dropzone__title", text: "Drop file here or Click to Browse" }),
      h("div", { class: "dropzone__types", text: "Images · Video · Audio · PDF · Docs · Code · Data" }),
    ]
  );
  const status = h("p", { class: "empty-note", text: "No file loaded" });
  const recent = h("div", { class: "panel-body" });
  panel.body.append(dropzone, input, status, recent);

  let current = null;

  function fileChip(file, { onAnalyse, onPrefill }) {
    return h("div", { class: "file-chip" }, [
      h("div", { class: "file-chip__name", text: file.name || file.path || "file" }),
      h("div", {
        class: "file-chip__meta",
        text: [
          file.size !== undefined ? formatBytes(file.size) : "",
          file.modified_at ? agoText((Date.now() - new Date(file.modified_at).getTime()) / 1000) : "",
        ].filter(Boolean).join(" · "),
      }),
      h("div", { class: "file-chip__actions" }, [
        h("button", { class: "btn btn--tiny", type: "button", text: "Analyse", onclick: onAnalyse }),
        h("button", { class: "btn btn--tiny btn--ghost", type: "button", text: "→", title: "Insert path into the command input", onclick: onPrefill }),
      ]),
    ]);
  }

  async function handleFile(file) {
    if (!file) return;
    setText(status, `uploading ${file.name} …`);
    try {
      const result = await controller.uploadFile(file);
      current = result;
      setText(status, `stored · ${result.path}`);
      controller.logLocal("DEV", `Upload ready for processing: ${result.name}`);
    } catch (error) {
      setText(status, `upload failed: ${error.message}`);
      controller.logLocal("ERROR", `Upload failed: ${error.message}`, "error");
    }
  }

  function analyse(path) {
    controller.submitCommand(
      `Process the uploaded file at ${path} with the file_processor action and summarise the result.`
    ).catch(() => {});
  }

  function prefill(path) {
    window.dispatchEvent(new CustomEvent("hud:prefill-command", { detail: { text: path } }));
  }

  dropzone.addEventListener("click", () => input.click());
  dropzone.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") input.click();
  });
  input.addEventListener("change", () => handleFile(input.files && input.files[0]));

  for (const type of ["dragenter", "dragover"]) {
    dropzone.addEventListener(type, (event) => {
      event.preventDefault();
      dropzone.classList.add("is-over");
    });
  }
  for (const type of ["dragleave", "drop"]) {
    dropzone.addEventListener(type, (event) => {
      event.preventDefault();
      dropzone.classList.remove("is-over");
    });
  }
  dropzone.addEventListener("drop", (event) => {
    const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
    handleFile(file);
  });

  // Prevent the browser from navigating away when a file misses the dropzone.
  window.addEventListener("dragover", (event) => event.preventDefault());
  window.addEventListener("drop", (event) => event.preventDefault());

  store.subscribe((state) => {
    const files = state.files || [];

    if (files.length) {
      recent.replaceChildren(
        ...files.slice(0, 3).map((file) =>
          fileChip(file, {
            onAnalyse: () => analyse(file.path),
            onPrefill: () => prefill(file.path),
          })
        ),
        h("div", { class: "file-chip__meta", text: `${files.length} file(s) in cloud drop folder` })
      );
    } else {
      recent.replaceChildren(emptyNote("no cloud uploads yet"));
    }

    if (current) setText(status, `loaded · ${current.path}`);
    panel.setMeta(files.length ? `${files.length} stored` : "cloud drop empty");
  });

  return { root: panel.root };
}