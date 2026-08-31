"use strict";

import * as pdfjs from "/static/pdfjs/pdf.min.mjs";

pdfjs.GlobalWorkerOptions.workerSrc = "/static/pdfjs/pdf.worker.min.mjs";

const params = new URLSearchParams(window.location.search);
const docId = params.get("doc") || "";
const canvas = document.getElementById("pdf-canvas");
const seal = document.getElementById("pdf-seal");
const state = { document: null, page: Math.max(1, Number(params.get("page")) || 1) };

function label(text) {
  seal.replaceChildren();
  const kind = document.createElement("span");
  kind.className = "seal-kind";
  kind.textContent = "PDF";
  const path = document.createElement("span");
  path.className = "seal-path";
  path.textContent = text;
  seal.append(kind, path);
}

async function render() {
  if (!state.document) {
    return;
  }
  const total = state.document.numPages;
  state.page = Math.min(Math.max(1, state.page), total);
  const page = await state.document.getPage(state.page);
  const unscaled = page.getViewport({ scale: 1 });
  const stage = canvas.parentElement.clientWidth || unscaled.width;
  const scale = Math.min(2, Math.max(0.4, (stage - 16) / unscaled.width));
  const viewport = page.getViewport({ scale: scale });
  canvas.width = Math.floor(viewport.width);
  canvas.height = Math.floor(viewport.height);
  await page.render({ canvasContext: canvas.getContext("2d"), viewport: viewport }).promise;
  label(docId.slice(0, 12) + " · " + state.page + " / " + total);
}

async function load() {
  if (!docId) {
    label("?");
    return;
  }
  label(docId.slice(0, 12));
  try {
    const source = { url: "/api/doc/" + encodeURIComponent(docId) + "/original" };
    state.document = await pdfjs.getDocument(source).promise;
  } catch (error) {
    label(docId.slice(0, 12) + " · " + error.name);
    return;
  }
  await render();
}

document.getElementById("pdf-prev").addEventListener("click", () => {
  state.page -= 1;
  render();
});
document.getElementById("pdf-next").addEventListener("click", () => {
  state.page += 1;
  render();
});
window.addEventListener("resize", () => render());
load();
