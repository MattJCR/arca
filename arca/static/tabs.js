"use strict";

(function () {
  const tabs = [...document.querySelectorAll('nav.tabs [role="tab"]')];
  if (!tabs.length) {
    return;
  }

  function activate(tab) {
    for (const other of tabs) {
      const selected = other === tab;
      other.setAttribute("aria-selected", String(selected));
      const panel = document.getElementById(other.dataset.tab);
      if (panel) {
        panel.hidden = !selected;
      }
    }
  }

  for (const tab of tabs) {
    tab.addEventListener("click", () => activate(tab));
  }
  activate(tabs[0]);
})();
