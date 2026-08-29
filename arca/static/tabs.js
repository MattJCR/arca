"use strict";

(function () {
  const tabs = [...document.querySelectorAll('nav.tabs [role="tab"]')];
  const families = [...document.querySelectorAll("nav.families .family")];
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

  function show(family) {
    for (const button of families) {
      button.setAttribute("aria-pressed", String(button.dataset.family === family));
    }
    let first = null;
    for (const tab of tabs) {
      const mine = tab.dataset.family === family;
      tab.hidden = !mine;
      if (mine && first === null) {
        first = tab;
      }
    }
    if (first !== null) {
      activate(first);
    }
  }

  for (const tab of tabs) {
    tab.addEventListener("click", () => activate(tab));
  }
  for (const button of families) {
    button.addEventListener("click", () => show(button.dataset.family));
  }
  if (families.length) {
    show(families[0].dataset.family);
  } else {
    activate(tabs[0]);
  }
})();
