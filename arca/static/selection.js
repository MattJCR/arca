"use strict";

(function () {
  const docId = document.body.dataset.doc || "";
  if (!docId) {
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
    return null;
  }

  document.addEventListener("selectionchange", () => {
    const selection = document.getSelection();
    if (!selection || selection.isCollapsed || selection.rangeCount === 0) {
      return;
    }
    const found = provenance(selection.getRangeAt(0).startContainer);
    if (!found) {
      return;
    }
    const detail = {
      doc_id: docId,
      anchor: found.anchor,
      page: found.page,
      text: selection.toString(),
    };
    window.parent.postMessage({ type: "arca:selection", detail: detail }, window.location.origin);
  });
})();
