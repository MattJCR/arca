"use strict";

(function () {
  const own = document.currentScript;
  const source = {
    kind: (own && own.dataset.sourceKind) || (document.body.dataset.doc ? "doc" : ""),
    id: (own && own.dataset.sourceId) || document.body.dataset.doc || "",
    path: (own && own.dataset.path) || "",
  };
  if (!source.id) {
    return;
  }

  function provenance(node) {
    let element = node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
    while (element && element !== document.body) {
      if (element.id && element.id.startsWith("p:")) {
        const section = element.closest("section.page");
        return { anchor: element.id, page: Number(section ? section.dataset.page : 0) || 0 };
      }
      element = element.parentElement;
    }
    return { anchor: "", page: 0 };
  }

  document.addEventListener("selectionchange", () => {
    const selection = document.getSelection();
    if (!selection || selection.isCollapsed || selection.rangeCount === 0) {
      return;
    }
    const found = provenance(selection.getRangeAt(0).startContainer);
    if (source.kind === "doc" && !found.anchor) {
      return;
    }
    const detail = {
      source_kind: source.kind,
      source_id: source.id,
      path: source.path,
      doc_id: source.kind === "doc" ? source.id : "",
      anchor: found.anchor,
      page: found.page,
      text: selection.toString(),
    };
    window.parent.postMessage({ type: "arca:selection", detail: detail }, window.location.origin);
  });
})();
