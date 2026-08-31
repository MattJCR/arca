"use strict";

const I18N = JSON.parse(document.getElementById("i18n-data").textContent);
const state = { collections: [], current: null, query: "", results: [] };
const VIEWER_SANDBOX = "allow-same-origin allow-scripts";
const READER_KEY = "arca.reader-scale";
const READER_STEPS = [0.85, 0.925, 1, 1.1, 1.2, 1.35, 1.5];

function t(key, params) {
  let template = I18N[key];
  if (template === undefined) {
    return key;
  }
  if (params) {
    for (const [name, value] of Object.entries(params)) {
      template = template.split("{" + name + "}").join(String(value));
    }
  }
  return template;
}

function applyI18n() {
  for (const element of document.querySelectorAll("[data-i18n]")) {
    element.textContent = t(element.dataset.i18n);
  }
  for (const element of document.querySelectorAll("[data-i18n-placeholder]")) {
    element.placeholder = t(element.dataset.i18nPlaceholder);
  }
  for (const element of document.querySelectorAll("[data-i18n-title]")) {
    element.title = t(element.dataset.i18nTitle);
  }
  for (const element of document.querySelectorAll("[data-i18n-label]")) {
    element.setAttribute("aria-label", t(element.dataset.i18nLabel));
  }
}

function showMessage(id, text) {
  const element = document.getElementById(id);
  element.textContent = text;
  element.hidden = !text;
}

function showBusy(id, text) {
  const element = document.getElementById(id);
  const wheel = document.createElement("span");
  wheel.className = "spinner";
  element.replaceChildren(wheel, document.createTextNode(text));
  element.hidden = false;
}

function showSkeletons(count) {
  const results = document.getElementById("search-results");
  results.replaceChildren();
  for (let index = 0; index < count; index += 1) {
    const item = document.createElement("li");
    item.className = "result result-loading";
    for (const width of ["72%", "94%", "48%"]) {
      const line = document.createElement("span");
      line.className = "skeleton";
      line.style.width = width;
      item.append(line);
    }
    results.append(item);
  }
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || response.statusText);
  }
  return payload;
}

function seal(result) {
  const strip = document.createElement("p");
  strip.className = "seal";
  const kind = document.createElement("span");
  kind.className = "seal-kind";
  kind.textContent = t("home.seal.kind." + result.source_kind);
  const source = document.createElement("span");
  source.className = "seal-source";
  source.textContent = result.source_title;
  const path = document.createElement("span");
  path.className = "seal-path";
  path.textContent = result.source_kind === "doc" ? result.anchor : result.path;
  const rank = document.createElement("span");
  rank.className = "seal-rank";
  if (result.followed) {
    rank.textContent = t("home.seal.followed", { rank: result.rank + 1 });
    rank.title = t("home.seal.followed_hint");
  } else if (result.source_kind === "doc" && result.page) {
    rank.textContent = t("home.seal.page", { page: result.page });
    rank.title = t("home.seal.page_hint");
  } else {
    rank.textContent = t("home.seal.rank", { rank: result.rank + 1 });
    rank.title = t("home.seal.rank_hint", { source: result.source_title });
  }
  strip.append(kind, source, path, rank);
  return strip;
}

function resultItem(result) {
  const item = document.createElement("li");
  item.className = "result";
  item.dataset.key = result.url;
  const link = document.createElement("a");
  link.className = "result-title";
  link.href = result.url;
  link.textContent = result.title;
  link.addEventListener("click", (event) => {
    event.preventDefault();
    openResult(result);
  });
  item.append(link, seal(result));
  if (result.snippet) {
    const snippet = document.createElement("p");
    snippet.className = "result-snippet";
    snippet.textContent = result.snippet;
    item.append(snippet);
  }
  if (result.source_kind === "doc") {
    item.append(documentActions(result));
  }
  return item;
}

function documentActions(result) {
  const actions = document.createElement("div");
  actions.className = "result-actions";
  const readable = document.createElement("a");
  readable.href = result.url;
  readable.textContent = t("home.result.open_readable");
  readable.addEventListener("click", (event) => {
    event.preventDefault();
    openResult(result);
  });
  actions.append(readable);
  const original = originalAction(result);
  if (original) {
    actions.append(original);
  }
  return actions;
}

function originalAction(result) {
  if (!result.original_url) {
    return null;
  }
  const link = document.createElement("a");
  if (result.original_download) {
    link.href = result.original_url;
    link.textContent = t("home.result.download_original");
    return link;
  }
  link.href = originalViewerUrl(result);
  link.textContent = t("home.result.open_original", { page: result.page });
  link.addEventListener("click", (event) => {
    event.preventDefault();
    openOriginal(result);
  });
  return link;
}

function originalViewerUrl(result) {
  return (
    "/static/pdf.html?doc=" +
    encodeURIComponent(result.doc_id) +
    "&page=" +
    encodeURIComponent(result.page || 1)
  );
}

function renderSources(sources) {
  const list = document.getElementById("search-sources");
  list.replaceChildren();
  const notes = [];
  for (const source of sources) {
    if (source.error) {
      notes.push(t("home.sources.failed", { title: source.title, error: source.error }));
    } else if (source.mode === "title") {
      notes.push(t("home.sources.title_only", { title: source.title }));
    } else if (source.mode === "none") {
      notes.push(t("home.sources.unsearchable", { title: source.title }));
    }
  }
  for (const note of notes) {
    const item = document.createElement("li");
    item.textContent = note;
    list.append(item);
  }
  list.hidden = !notes.length;
}

function renderCollections(sources) {
  state.collections = sources;
  const select = document.getElementById("search-scope");
  const chosen = select.value;
  select.replaceChildren();
  const all = document.createElement("option");
  all.value = "";
  all.textContent = t("home.search.scope_all");
  select.append(all);
  for (const source of sources) {
    const option = document.createElement("option");
    option.value = source.id;
    option.textContent = source.title;
    select.append(option);
  }
  select.value = chosen;
}

function showLibraryState(library) {
  const message = document.getElementById("search-message");
  message.replaceChildren();
  message.append(document.createTextNode(t("home.library." + library.state) + " "));
  const link = document.createElement("a");
  link.href = "/admin";
  link.textContent = t(
    library.state === "empty" ? "home.search.open_catalog" : "home.health.open_dashboard"
  );
  message.append(link);
  message.hidden = false;
  renderUnreadable(library.unreadable);
}

function renderUnreadable(entries) {
  const list = document.getElementById("search-sources");
  for (const entry of entries) {
    const item = document.createElement("li");
    item.textContent = t("home.library.unreadable_file", entry);
    list.append(item);
  }
  if (entries.length) {
    list.hidden = false;
  }
}

async function runSearch(event) {
  if (event) {
    event.preventDefault();
  }
  const query = document.getElementById("search-q").value.trim();
  const scope = document.getElementById("search-scope").value;
  const params = new URLSearchParams();
  if (query) {
    params.set("q", query);
  }
  if (scope) {
    params.set("scope", scope);
  }
  if (query) {
    showBusy("search-message", t("home.search.searching"));
    showSkeletons(4);
  } else {
    showMessage("search-message", "");
  }
  let payload;
  try {
    payload = await fetchJson("/api/search?" + params.toString());
  } catch (error) {
    showMessage("search-message", t("home.search.error", { error: error.message }));
    document.getElementById("search-results").replaceChildren();
    return;
  }
  state.query = query;
  state.results = [...payload.semantic, ...payload.keyword];
  renderCollections(payload.sources);
  const results = document.getElementById("search-results");
  const count = document.getElementById("search-count");
  results.replaceChildren();
  const library = payload.library || { state: "empty", unreadable: [] };
  if (!payload.sources.length) {
    count.hidden = true;
    renderSources([]);
    showLibraryState(library);
    return;
  }
  document.getElementById("semantic-band").hidden = payload.semantic_available;
  renderSources(payload.sources);
  renderUnreadable(library.unreadable);
  if (!query) {
    showMessage("search-message", "");
    count.hidden = true;
    return;
  }
  if (!payload.keyword.length) {
    count.hidden = true;
    showMessage("search-message", t("home.search.empty", { query: query }));
  } else {
    showMessage("search-message", "");
    count.textContent = t("home.search.count", {
      count: payload.keyword.length,
      sources: payload.sources.length,
    });
    count.hidden = false;
    for (const result of payload.keyword) {
      results.append(resultItem(result));
    }
  }
  renderSemantic(payload);
}

function renderSemantic(payload) {
  const band = document.getElementById("semantic-band");
  const list = document.getElementById("semantic-results");
  const note = document.getElementById("semantic-note");
  list.replaceChildren();
  band.hidden = false;
  if (!payload.semantic_available) {
    note.textContent = t("home.semantic.unavailable");
    note.hidden = false;
    return;
  }
  if (payload.semantic_offline) {
    note.textContent = t("home.semantic.offline");
    note.hidden = false;
    return;
  }
  if (!payload.semantic.length) {
    note.textContent = t("home.semantic.empty");
    note.hidden = false;
    return;
  }
  note.hidden = true;
  for (const result of payload.semantic) {
    list.append(resultItem(result));
  }
}

function openResult(result) {
  showViewer(result, result.url);
}

function openOriginal(result) {
  showViewer(result, originalViewerUrl(result));
}

function markOpen(result) {
  for (const item of document.querySelectorAll("li.result")) {
    const open = Boolean(result) && item.dataset.key === result.url;
    if (open) {
      item.setAttribute("aria-current", "true");
    } else {
      item.removeAttribute("aria-current");
    }
  }
}

function showViewer(result, url) {
  state.current = result;
  markOpen(result);
  document.getElementById("viewer-empty").hidden = true;
  document.getElementById("viewer-pane").hidden = false;
  const strip = document.getElementById("viewer-seal");
  strip.replaceChildren(...seal(result).childNodes);
  const frame = document.getElementById("viewer-frame");
  frame.setAttribute("sandbox", VIEWER_SANDBOX);
  document.getElementById("viewer-loading").hidden = false;
  frame.src = url;
  frame.focus();
}

function closeViewer() {
  state.current = null;
  markOpen(null);
  document.getElementById("viewer-pane").hidden = true;
  document.getElementById("viewer-frame").removeAttribute("src");
  document.getElementById("viewer-empty").hidden = false;
  document.getElementById("search-q").focus();
}

function readerScale() {
  let stored;
  try {
    stored = Number(window.localStorage.getItem(READER_KEY));
  } catch (error) {
    stored = 0;
  }
  return READER_STEPS.includes(stored) ? stored : 1;
}

function applyReaderScale(scale) {
  document.getElementById("reader-scale").textContent = Math.round(scale * 100) + " %";
  const frame = document.getElementById("viewer-frame");
  let inner;
  try {
    inner = frame.contentDocument;
  } catch (error) {
    return;
  }
  if (inner && inner.documentElement) {
    inner.documentElement.style.setProperty("--reader-scale", String(scale));
  }
}

function stepReaderScale(direction) {
  const steps = READER_STEPS;
  const index = steps.indexOf(readerScale());
  const next = steps[Math.min(steps.length - 1, Math.max(0, index + direction))];
  try {
    window.localStorage.setItem(READER_KEY, String(next));
  } catch (error) {
    applyReaderScale(next);
    return;
  }
  applyReaderScale(next);
}

function trackViewerNavigation() {
  const frame = document.getElementById("viewer-frame");
  frame.addEventListener("load", () => {
    document.getElementById("viewer-loading").hidden = true;
    applyReaderScale(readerScale());
    if (!state.current) {
      return;
    }
    let path;
    try {
      path = decodeURIComponent(frame.contentWindow.location.pathname);
    } catch (error) {
      return;
    }
    const prefix = "/zim/" + state.current.source_id + "/";
    if (!path.startsWith(prefix)) {
      return;
    }
    const strip = document.getElementById("viewer-seal");
    const reached = path.slice(prefix.length);
    const moved = Object.assign({}, state.current, {
      path: reached,
      followed: reached !== state.current.path,
    });
    strip.replaceChildren(...seal(moved).childNodes);
  });
}

async function loadHealth() {
  let payload;
  try {
    payload = await fetchJson("/api/diagnostics");
  } catch (error) {
    return;
  }
  if (typeof renderChatState === "function") {
    renderChatState(payload.checks);
  }
  const worst = payload.checks.some((check) => check.state === "broken")
    ? "broken"
    : payload.checks.some((check) => check.state === "degraded")
      ? "degraded"
      : "ok";
  if (worst === "ok") {
    const badge = document.getElementById("health-badge");
    badge.textContent = t("home.health.ok");
    badge.hidden = false;
    return;
  }
  const strip = document.getElementById("health-strip");
  strip.replaceChildren(document.createTextNode(t("home.health." + worst) + " "));
  const link = document.createElement("a");
  link.href = "/admin";
  link.textContent = t("home.health.open_dashboard");
  strip.append(link);
  strip.hidden = false;
}

applyI18n();
document.getElementById("search-form").addEventListener("submit", runSearch);
document.getElementById("search-scope").addEventListener("change", () => {
  if (document.getElementById("search-q").value.trim()) {
    runSearch();
  }
});
document.getElementById("viewer-back").addEventListener("click", closeViewer);
document.getElementById("reader-smaller").addEventListener("click", () => stepReaderScale(-1));
document.getElementById("reader-larger").addEventListener("click", () => stepReaderScale(1));
applyReaderScale(readerScale());
trackViewerNavigation();
runSearch();
loadHealth();
