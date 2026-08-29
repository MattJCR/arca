"use strict";

(function () {
  const KEY = "arca.theme";
  const root = document.documentElement;
  const stored = read();

  function read() {
    try {
      const value = window.localStorage.getItem(KEY);
      return value === "light" || value === "dark" ? value : "";
    } catch (error) {
      return "";
    }
  }

  function remember(theme) {
    try {
      window.localStorage.setItem(KEY, theme);
    } catch (error) {
      return;
    }
  }

  function system() {
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function reflect(theme) {
    for (const button of document.querySelectorAll("[data-theme-choice]")) {
      button.setAttribute("aria-pressed", String(button.dataset.themeChoice === theme));
    }
  }

  function choose(theme) {
    root.dataset.theme = theme;
    remember(theme);
    reflect(theme);
  }

  if (stored) {
    root.dataset.theme = stored;
  }

  function wire() {
    reflect(root.dataset.theme || system());
    for (const button of document.querySelectorAll("[data-theme-choice]")) {
      button.addEventListener("click", () => choose(button.dataset.themeChoice));
    }
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
      if (!root.dataset.theme) {
        reflect(system());
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wire);
  } else {
    wire();
  }
})();
