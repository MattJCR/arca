"use strict";

const I18N = JSON.parse(document.getElementById("i18n-data").textContent);
const DOWNLOAD_POLL_MS = 3000;
const STATUS_POLL_MS = 15000;
const speedSnapshots = new Map();
const catalogState = { entries: [], sortKey: "size_bytes", sortDir: -1, flavour: "" };

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
}

function formatBytes(value) {
  if (value === null || value === undefined) {
    return "?";
  }
  let amount = Number(value);
  for (const unit of ["B", "KiB", "MiB", "GiB"]) {
    if (amount < 1024 || unit === "GiB") {
      return unit === "B" ? Math.round(amount) + " " + unit : amount.toFixed(1) + " " + unit;
    }
    amount /= 1024;
  }
}

function showMessage(id, text) {
  const element = document.getElementById(id);
  element.textContent = text;
  element.hidden = !text;
}

function cell(text, className) {
  const td = document.createElement("td");
  td.textContent = text;
  if (className) {
    td.className = className;
  }
  return td;
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || response.statusText);
  }
  return payload;
}

async function loadStatus() {
  let payload;
  try {
    payload = await fetchJson("/api/admin/status");
  } catch (error) {
    return;
  }
  const summary = document.getElementById("status-summary");
  summary.replaceChildren();
  const lines = [
    t("admin.status.free", {
      free: formatBytes(payload.free_bytes),
      total: formatBytes(payload.total_bytes),
    }),
    t("admin.status.usage.zim", { size: formatBytes(payload.usage.zim) }),
    t("admin.status.usage.docs", { size: formatBytes(payload.usage.docs) }),
    t("admin.status.usage.models", { size: formatBytes(payload.usage.models) }),
    t("admin.status.usage.vendor", { size: formatBytes(payload.usage.vendor) }),
    t("admin.status.counts", {
      zim: payload.counts.zim,
      shards: payload.counts.shards,
      documents: payload.counts.documents,
      active: payload.counts.active_downloads,
    }),
    t("admin.status.worst." + payload.worst_state),
  ];
  for (const line of lines) {
    const item = document.createElement("li");
    item.textContent = line;
    summary.append(item);
  }
  const eventsBody = document.getElementById("events-body");
  eventsBody.replaceChildren();
  if (!payload.events.length) {
    const row = document.createElement("tr");
    const empty = cell(t("admin.status.no_events"));
    empty.colSpan = 4;
    row.append(empty);
    eventsBody.append(row);
    return;
  }
  for (const event of payload.events) {
    const row = document.createElement("tr");
    row.append(cell(event.ts), cell(event.level), cell(event.area), cell(event.message));
    eventsBody.append(row);
  }
}

async function loadCatalogFilters() {
  let payload;
  try {
    payload = await fetchJson("/api/admin/catalog/filters");
  } catch (error) {
    showMessage("catalog-message", t("admin.catalog.filters_error", { error: error.message }));
    return;
  }
  const languageSelect = document.getElementById("catalog-lang");
  for (const language of payload.languages) {
    const option = document.createElement("option");
    option.value = language.code;
    const count = language.count === null ? "" : " · " + language.count;
    option.textContent = language.name + " (" + language.code + ")" + count;
    languageSelect.append(option);
  }
  const categorySelect = document.getElementById("catalog-category");
  for (const category of payload.categories) {
    const option = document.createElement("option");
    option.value = category;
    option.textContent = category;
    categorySelect.append(option);
  }
}

async function searchCatalog(event) {
  event.preventDefault();
  showMessage("catalog-message", t("admin.catalog.searching"));
  const params = new URLSearchParams();
  const q = document.getElementById("catalog-q").value.trim();
  const lang = document.getElementById("catalog-lang").value;
  const category = document.getElementById("catalog-category").value;
  if (q) params.set("q", q);
  if (lang) params.set("lang", lang);
  if (category) params.set("category", category);
  let payload;
  try {
    payload = await fetchJson("/api/admin/catalog?" + params.toString());
  } catch (error) {
    showMessage("catalog-message", t("admin.catalog.error", { error: error.message }));
    return;
  }
  showMessage("catalog-message", "");
  catalogState.entries = payload.entries;
  catalogState.flavour = "";
  rebuildFlavourOptions();
  renderCatalog();
}

function rebuildFlavourOptions() {
  const select = document.getElementById("catalog-flavour");
  const flavours = [...new Set(catalogState.entries.map((entry) => entry.flavour))]
    .filter((flavour) => flavour)
    .sort();
  select.replaceChildren();
  const any = document.createElement("option");
  any.value = "";
  any.textContent = t("admin.catalog.any_flavour");
  select.append(any);
  for (const flavour of flavours) {
    const option = document.createElement("option");
    option.value = flavour;
    option.textContent = flavour;
    select.append(option);
  }
}

function visibleCatalogEntries() {
  const key = catalogState.sortKey;
  const direction = catalogState.sortDir;
  const numeric = key === "size_bytes" || key === "article_count" || key === "has_fulltext";
  const entries = catalogState.entries.filter(
    (entry) => !catalogState.flavour || entry.flavour === catalogState.flavour
  );
  entries.sort((a, b) => {
    let result;
    if (numeric) {
      result = (Number(a[key]) || 0) - (Number(b[key]) || 0);
    } else {
      result = String(a[key]).localeCompare(String(b[key]), undefined, { sensitivity: "base" });
    }
    return result * direction;
  });
  return entries;
}

function renderCatalog() {
  const tableWrap = document.getElementById("catalog-table-wrap");
  const body = document.getElementById("catalog-body");
  const entries = visibleCatalogEntries();
  for (const header of document.querySelectorAll("#catalog-table th[data-sort]")) {
    const active = header.dataset.sort === catalogState.sortKey;
    header.classList.toggle("sorted", active);
    if (active) {
      header.setAttribute("aria-sort", catalogState.sortDir === 1 ? "ascending" : "descending");
      header.dataset.arrow = catalogState.sortDir === 1 ? "▲" : "▼";
    } else {
      header.removeAttribute("aria-sort");
      header.dataset.arrow = "";
    }
  }
  body.replaceChildren();
  if (!catalogState.entries.length) {
    tableWrap.hidden = true;
    document.getElementById("catalog-count").hidden = true;
    showMessage("catalog-message", t("admin.catalog.empty"));
    return;
  }
  tableWrap.hidden = false;
  const count = document.getElementById("catalog-count");
  count.textContent = t("admin.catalog.count", {
    shown: entries.length,
    total: catalogState.entries.length,
  });
  count.hidden = false;
  for (const entry of entries) {
    body.append(catalogRow(entry));
  }
}

function catalogRow(entry) {
  const row = document.createElement("tr");
  const title = cell(entry.title);
  if (entry.is_update) {
    const badge = document.createElement("span");
    badge.className = "badge badge-update";
    badge.textContent = t("admin.catalog.update_available");
    title.append(" ", badge);
  } else if (entry.in_library) {
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = t("admin.catalog.in_library");
    title.append(" ", badge);
  }
  row.append(
    title,
    cell(entry.language || "?"),
    cell(entry.flavour || "-"),
    cell(entry.size_human, "num"),
    cell(entry.article_count === null ? "?" : entry.article_count.toLocaleString(), "num"),
    cell(t(entry.has_fulltext ? "admin.catalog.fulltext_yes" : "admin.catalog.fulltext_no"))
  );
  const actions = document.createElement("td");
  const button = document.createElement("button");
  button.textContent = t("admin.catalog.add_button");
  button.addEventListener("click", () => addDownload(entry.uuid, button));
  actions.append(button);
  row.append(actions);
  return row;
}

function sortCatalog(key) {
  if (catalogState.sortKey === key) {
    catalogState.sortDir = -catalogState.sortDir;
  } else {
    catalogState.sortKey = key;
    catalogState.sortDir = key === "size_bytes" || key === "article_count" ? -1 : 1;
  }
  renderCatalog();
}

async function addDownload(uuid, button) {
  button.disabled = true;
  try {
    const payload = await fetchJson("/api/admin/downloads", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ uuid: uuid }),
    });
    showMessage("downloads-message", t("admin.catalog.added", { id: payload.id }));
  } catch (error) {
    showMessage("downloads-message", t("admin.catalog.add_error", { error: error.message }));
    button.disabled = false;
    return;
  }
  loadDownloads();
}

function downloadActions(download) {
  const actions = [];
  if (download.status === "queued" || download.status === "running") {
    actions.push("pause", "cancel");
  } else if (download.status === "paused") {
    actions.push("resume", "cancel");
  } else if (download.status === "failed") {
    actions.push("retry");
  }
  actions.push("delete");
  return actions;
}

function downloadSpeed(download) {
  const now = performance.now();
  const previous = speedSnapshots.get(download.id);
  speedSnapshots.set(download.id, { time: now, bytes: download.bytes_done });
  if (
    download.status !== "running" ||
    !previous ||
    now <= previous.time ||
    download.bytes_done < previous.bytes
  ) {
    return "-";
  }
  const rate = ((download.bytes_done - previous.bytes) * 1000) / (now - previous.time);
  return formatBytes(rate) + "/s";
}

async function loadDownloads() {
  let payload;
  try {
    payload = await fetchJson("/api/admin/downloads");
  } catch (error) {
    return;
  }
  const tableWrap = document.getElementById("downloads-table-wrap");
  const body = document.getElementById("downloads-body");
  body.replaceChildren();
  if (!payload.downloads.length) {
    tableWrap.hidden = true;
    showMessage("downloads-message", t("admin.downloads.empty"));
    return;
  }
  tableWrap.hidden = false;
  for (const download of payload.downloads) {
    const row = document.createElement("tr");
    const percent = download.size_bytes
      ? ((100 * download.bytes_done) / download.size_bytes).toFixed(1) + "%"
      : "?";
    const progress = download.done_human + " / " + download.size_human + " (" + percent + ")";
    row.append(
      cell(download.filename),
      cell(progress, "num"),
      cell(downloadSpeed(download), "num"),
      cell(download.mirror ? new URL(download.mirror).host : "-"),
      cell(t("admin.downloads.status." + download.status))
    );
    const actions = document.createElement("td");
    for (const action of downloadActions(download)) {
      const button = document.createElement("button");
      button.textContent = t("admin.downloads." + action);
      button.addEventListener("click", () => downloadAction(download.id, action));
      actions.append(button);
    }
    row.append(actions);
    body.append(row);
  }
}

async function downloadAction(id, action) {
  if (action === "delete" && !window.confirm(t("admin.downloads.delete_confirm"))) {
    return;
  }
  try {
    await fetchJson("/api/admin/downloads/" + id + "/" + action, { method: "POST" });
  } catch (error) {
    showMessage("downloads-message", error.message);
  }
  loadDownloads();
}

async function verifyLibrary() {
  const button = document.getElementById("verify-button");
  button.disabled = true;
  showMessage("verify-message", t("admin.verify.running"));
  let payload;
  try {
    payload = await fetchJson("/api/admin/verify", { method: "POST" });
  } catch (error) {
    showMessage("verify-message", error.message);
    button.disabled = false;
    return;
  }
  button.disabled = false;
  if (!payload.results.length) {
    showMessage("verify-message", t("admin.verify.none"));
    return;
  }
  const counts = { ok: 0, corrupt: 0, missing: 0, no_hash: 0 };
  for (const result of payload.results) {
    counts[result.result] += 1;
  }
  showMessage("verify-message", t("admin.verify.summary", counts));
}

async function loadZimContent() {
  const button = document.getElementById("content-refresh-button");
  button.disabled = true;
  let payload;
  try {
    payload = await fetchJson("/api/admin/zim");
  } catch (error) {
    showMessage("content-message", error.message);
    button.disabled = false;
    return;
  }
  button.disabled = false;
  const body = document.getElementById("content-body");
  const tableWrap = document.getElementById("content-table-wrap");
  const count = document.getElementById("content-count");
  body.replaceChildren();
  if (!payload.archives.length) {
    tableWrap.hidden = true;
    count.hidden = true;
    showMessage("content-message", t("admin.content.empty"));
  } else {
    showMessage("content-message", "");
    tableWrap.hidden = false;
    count.textContent = t("admin.content.count", {
      count: payload.archives.length,
      size: payload.total_human,
    });
    count.hidden = false;
    for (const archive of payload.archives) {
      body.append(contentRow(archive));
    }
  }
  renderZimFailures(payload.failures);
}

function contentRow(archive) {
  const row = document.createElement("tr");
  const index = cell(t("admin.content.index." + archive.index_mode));
  index.title = t("admin.content.index." + archive.index_mode + "_hint");
  if (archive.index_mode !== "fulltext") {
    index.className = "state state-degraded";
  }
  const search = document.createElement("td");
  const badge = document.createElement("span");
  badge.className = "state " + (archive.enabled ? "state-ok" : "state-degraded");
  badge.textContent = t(archive.enabled ? "admin.content.enabled" : "admin.content.disabled");
  search.append(badge);
  row.append(
    cell(archive.title),
    cell(archive.language || "?"),
    cell(archive.flavour || "-"),
    cell(archive.article_count.toLocaleString(), "num"),
    cell(archive.size_human, "num"),
    index,
    cell(t("admin.content.state." + (archive.state || "unregistered"))),
    search
  );
  const actions = document.createElement("td");
  const button = document.createElement("button");
  button.textContent = t(archive.enabled ? "admin.content.disable" : "admin.content.enable");
  button.addEventListener("click", () => toggleZim(archive, button));
  actions.append(button);
  row.append(actions);
  return row;
}

async function toggleZim(archive, button) {
  button.disabled = true;
  const action = archive.enabled ? "disable" : "enable";
  try {
    await fetchJson("/api/admin/zim/" + encodeURIComponent(archive.uuid) + "/" + action, {
      method: "POST",
    });
  } catch (error) {
    showMessage("content-message", t("admin.content.toggle_error", { error: error.message }));
    button.disabled = false;
    return;
  }
  loadZimContent();
}

function renderZimFailures(failures) {
  const container = document.getElementById("content-failures");
  const list = document.getElementById("content-failures-list");
  list.replaceChildren();
  container.hidden = !failures.length;
  for (const failure of failures) {
    const item = document.createElement("li");
    item.textContent = t("admin.content.failure", failure);
    list.append(item);
  }
}

async function loadVendor(post) {
  let payload;
  try {
    payload = await fetchJson(
      "/api/admin/vendor" + (post ? "/verify" : ""),
      post ? { method: "POST" } : undefined
    );
  } catch (error) {
    showMessage("vendor-message", error.message);
    return;
  }
  if (payload.error) {
    showMessage("vendor-message", payload.error);
    return;
  }
  showMessage("vendor-message", "");
  const vendorBody = document.getElementById("vendor-body");
  vendorBody.replaceChildren();
  for (const entry of payload.entries) {
    const row = document.createElement("tr");
    row.append(
      cell(entry.key),
      cell(entry.version),
      cell(entry.kind),
      cell(entry.license),
      cell(entry.filename || "-"),
      cell(t("admin.vendor.state." + entry.result))
    );
    vendorBody.append(row);
  }
  const driftBody = document.getElementById("drift-body");
  driftBody.replaceChildren();
  for (const status of payload.drift) {
    const row = document.createElement("tr");
    row.append(
      cell(status.package),
      cell(status.installed || "-"),
      cell(status.documented || "-"),
      cell(t("admin.vendor.drift." + status.result))
    );
    driftBody.append(row);
  }
}

applyI18n();
document.getElementById("catalog-form").addEventListener("submit", searchCatalog);
document.getElementById("catalog-flavour").addEventListener("change", (event) => {
  catalogState.flavour = event.target.value;
  renderCatalog();
});
for (const header of document.querySelectorAll("#catalog-table th[data-sort]")) {
  header.tabIndex = 0;
  header.addEventListener("click", () => sortCatalog(header.dataset.sort));
  header.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      sortCatalog(header.dataset.sort);
    }
  });
}
async function downloadVendorPieces() {
  const button = document.getElementById("vendor-download-button");
  button.disabled = true;
  let payload;
  try {
    payload = await fetchJson("/api/admin/vendor/download", { method: "POST" });
  } catch (error) {
    showMessage("vendor-message", error.message);
    button.disabled = false;
    return;
  }
  button.disabled = false;
  showMessage(
    "vendor-message",
    payload.queued
      ? t("admin.vendor.download_queued", { count: payload.queued })
      : t("admin.vendor.nothing_missing")
  );
  loadDownloads();
}

document.getElementById("verify-button").addEventListener("click", verifyLibrary);
document.getElementById("content-refresh-button").addEventListener("click", loadZimContent);
document
  .getElementById("vendor-verify-button")
  .addEventListener("click", () => loadVendor(true));
document
  .getElementById("vendor-download-button")
  .addEventListener("click", downloadVendorPieces);
loadStatus();
loadCatalogFilters();
loadDownloads();
loadZimContent();
loadVendor(false);
setInterval(loadDownloads, DOWNLOAD_POLL_MS);
setInterval(loadStatus, STATUS_POLL_MS);

async function loadDiagnostics() {
  const button = document.getElementById("diagnostics-refresh-button");
  button.disabled = true;
  let payload;
  try {
    payload = await fetchJson("/api/diagnostics");
  } catch (error) {
    button.disabled = false;
    return;
  }
  button.disabled = false;
  const list = document.getElementById("diagnostics-list");
  list.replaceChildren();
  for (const check of payload.checks) {
    const item = document.createElement("li");
    const badge = document.createElement("span");
    badge.className = "state state-" + check.state;
    badge.textContent = check.state_label;
    item.append(badge, document.createTextNode(" " + check.summary));
    for (const aspect of ["impact", "action"]) {
      if (check[aspect]) {
        const detail = document.createElement("p");
        detail.className = "detail";
        detail.textContent = check[aspect];
        item.append(detail);
      }
    }
    list.append(item);
  }
}

document
  .getElementById("diagnostics-refresh-button")
  .addEventListener("click", loadDiagnostics);
loadDiagnostics();

function stateCell(state) {
  const holder = document.createElement("td");
  const badge = document.createElement("span");
  const tone = state === "sealed" ? "ok" : state === "corrupt" ? "broken" : "degraded";
  badge.className = "state state-" + tone;
  badge.textContent = t("admin.shard.state." + state);
  holder.append(badge);
  return holder;
}

function emptyRow(text, columns) {
  const row = document.createElement("tr");
  const holder = cell(text);
  holder.colSpan = columns;
  row.append(holder);
  return row;
}

async function loadDocuments() {
  const button = document.getElementById("documents-refresh-button");
  button.disabled = true;
  let payload;
  try {
    payload = await fetchJson("/api/admin/documents");
  } catch (error) {
    showMessage("documents-message", error.message);
    button.disabled = false;
    return;
  }
  button.disabled = false;
  const notes = [];
  if (payload.provisional.length) {
    notes.push(t("admin.documents.provisional", { shards: payload.provisional.join(", ") }));
  }
  if (!payload.embedding_available) {
    notes.push(t("admin.documents.embedding_down", { url: payload.embedding_server }));
  }
  showMessage("documents-message", notes.join(" "));
  renderDocumentRows(payload.documents);
  renderShardRows(payload.shards);
  renderJobRows(payload.jobs);
}

function renderDocumentRows(documents) {
  const body = document.getElementById("documents-body");
  body.replaceChildren();
  if (!documents.length) {
    body.append(emptyRow(t("admin.documents.empty"), 6));
    return;
  }
  for (const entry of documents) {
    const row = document.createElement("tr");
    const gaps = entry.unindexable_pages.length
      ? t("admin.documents.gaps", { count: entry.unindexable_pages.length })
      : t("admin.documents.no_gaps");
    const gapCell = cell(gaps);
    if (entry.unindexable_pages.length) {
      gapCell.className = "state state-degraded";
    }
    row.append(
      cell(entry.title),
      cell(entry.orig_name),
      cell(String(entry.pages), "num"),
      gapCell,
      cell(entry.shard_id)
    );
    const actions = document.createElement("td");
    const links = [
      [entry.readable_url, "admin.documents.open_readable"],
      [entry.original_url, "admin.documents.open_original"],
    ];
    for (const pair of links) {
      if (!pair[0]) {
        continue;
      }
      const link = document.createElement("a");
      link.href = pair[0];
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = t(pair[1]);
      actions.append(link, document.createTextNode(" "));
    }
    row.append(actions);
    body.append(row);
  }
}

function renderShardRows(shards) {
  const body = document.getElementById("shards-body");
  body.replaceChildren();
  for (const shard of shards) {
    const row = document.createElement("tr");
    row.append(cell(shard.shard_id), cell(shard.kind || "-"), stateCell(shard.state));
    row.append(
      cell(String(shard.doc_count), "num"),
      cell(String(shard.chunk_count), "num"),
      cell(shard.embed_model || "-")
    );
    if (shard.reason) {
      row.title = shard.reason;
    }
    body.append(row);
  }
}

function renderJobRows(jobs) {
  const body = document.getElementById("jobs-body");
  body.replaceChildren();
  if (!jobs.length) {
    body.append(emptyRow(t("admin.documents.no_jobs"), 4));
    return;
  }
  for (const job of jobs) {
    const row = document.createElement("tr");
    row.append(cell(job.source), cell(job.status), cell(job.shard_id), cell(job.error || "-"));
    body.append(row);
  }
}

function scopeCard(source) {
  const card = document.createElement("div");
  card.className = "scope-card";
  const title = document.createElement("h3");
  title.textContent = source.title;
  card.append(title);
  if (source.fixed) {
    const fixed = document.createElement("p");
    fixed.className = "detail";
    fixed.textContent = t("admin.scope.fixed");
    card.append(fixed);
    return card;
  }
  const articles = document.createElement("p");
  articles.className = "detail";
  articles.textContent = t("admin.scope.articles", { count: source.articles });
  const select = document.createElement("select");
  for (const value of ["out", "full", "curated"]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = t("admin.scope." + value);
    option.selected = value === source.scope;
    select.append(option);
  }
  const titles = document.createElement("textarea");
  titles.rows = 4;
  titles.placeholder = t("admin.scope.titles_label");
  titles.hidden = source.scope !== "curated";
  select.addEventListener("change", () => {
    titles.hidden = select.value !== "curated";
  });
  const estimate = document.createElement("p");
  estimate.className = "result-count";
  estimate.textContent = t("admin.scope.estimate", {
    chunks: source.estimate.chunks,
    seconds: source.estimate.seconds,
    size: source.estimate.size_human,
  });
  const save = document.createElement("button");
  save.type = "button";
  save.textContent = t("admin.scope.save");
  save.addEventListener("click", () => saveScope(source, select.value, titles.value, save));
  card.append(articles, select, titles, estimate, save);
  return card;
}

async function saveScope(source, scope, titles, button) {
  button.disabled = true;
  try {
    await fetchJson("/api/admin/scope", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_kind: source.source_kind,
        source_ref: source.source_ref,
        scope: scope,
        article_list: titles,
      }),
    });
  } catch (error) {
    showMessage("scope-message", t("admin.scope.error", { error: error.message }));
    button.disabled = false;
    return;
  }
  showMessage("scope-message", t("admin.scope.saved", { title: source.title }));
  loadScope();
}

async function loadScope() {
  let payload;
  try {
    payload = await fetchJson("/api/admin/scope");
  } catch (error) {
    showMessage("scope-message", error.message);
    return;
  }
  const list = document.getElementById("scope-list");
  list.replaceChildren();
  for (const source of payload.sources) {
    list.append(scopeCard(source));
  }
}

async function loadModels() {
  let payload;
  try {
    payload = await fetchJson("/api/admin/models");
  } catch (error) {
    return;
  }
  document.getElementById("models-active").textContent = t("admin.models.active", {
    model: payload.active_embed_model,
    dims: payload.active_embed_dims,
  });
  const warnings = document.getElementById("models-warnings");
  warnings.replaceChildren();
  for (const shard of payload.mismatched_shards) {
    const item = document.createElement("li");
    item.textContent = t("admin.models.mismatch", {
      shard: shard.shard_id,
      model: shard.embed_model,
      dims: shard.embed_dims,
    });
    warnings.append(item);
  }
  warnings.hidden = !payload.mismatched_shards.length;
  const body = document.getElementById("models-body");
  body.replaceChildren();
  if (!payload.models.length) {
    body.append(emptyRow(t("admin.models.empty"), 6));
    return;
  }
  for (const model of payload.models) {
    const row = document.createElement("tr");
    const architecture = model.error
      ? t("admin.models.unreadable", { error: model.error })
      : model.architecture;
    const architectureCell = cell(architecture);
    if (model.error) {
      architectureCell.className = "state state-degraded";
    }
    row.append(
      cell(model.name),
      cell(model.kind),
      architectureCell,
      cell(model.quantization || "-"),
      cell(model.context_length ? String(model.context_length) : "-", "num"),
      cell(model.size_human, "num")
    );
    body.append(row);
  }
}

document.getElementById("documents-refresh-button").addEventListener("click", loadDocuments);
loadDocuments();
loadScope();
loadModels();

const llamaState = { groups: [], help_source: "", profiles: [], active: "", last_good: "" };

function llamaSummary(payload) {
  const summary = document.getElementById("llama-summary");
  summary.replaceChildren();
  const lines = [];
  if (payload.binary.present) {
    lines.push(
      t("admin.llama.binary", { path: payload.binary.path, source: payload.binary.source })
    );
    if (payload.binary.version) {
      lines.push(t("admin.llama.version", { version: payload.binary.version }));
    }
    if (payload.binary.devices.length) {
      lines.push(t("admin.llama.devices", { devices: payload.binary.devices.join(" · ") }));
    }
    lines.push(t(payload.binary.gpu ? "admin.llama.gpu_yes" : "admin.llama.gpu_no"));
  } else {
    lines.push(t("admin.llama.binary_missing"));
  }
  const running = payload.server.running
    ? t("admin.llama.status.running", { pid: payload.server.pid })
    : t("admin.llama.status.stopped");
  lines.push(running + " · " + t("admin.llama.health." + payload.server.health));
  if (payload.chat_model.present) {
    lines.push(payload.chat_model.name + " · " + payload.chat_model.size_human);
  }
  for (const line of lines) {
    const item = document.createElement("li");
    item.textContent = line;
    summary.append(item);
  }
}

function renderPreflight(notes) {
  const list = document.getElementById("llama-preflight");
  list.replaceChildren();
  for (const note of notes) {
    const item = document.createElement("li");
    item.className = "state state-degraded";
    item.textContent = t("admin.llama.preflight." + note.code, note.params);
    list.append(item);
  }
  list.hidden = !notes.length;
}

async function loadLlama(refresh) {
  let payload;
  try {
    payload = await fetchJson("/api/admin/llama" + (refresh ? "?refresh=1" : ""));
  } catch (error) {
    showMessage("llama-message", t("admin.llama.error", { error: error.message }));
    return;
  }
  llamaSummary(payload);
  document.getElementById("llama-command").textContent = payload.command_line;
  renderPreflight(payload.preflight);
  const log = document.getElementById("llama-log");
  log.textContent = payload.log.length ? payload.log.join("\n") : t("admin.llama.log_empty");
  llamaState.profiles = payload.profiles;
  llamaState.active = payload.active;
  llamaState.last_good = payload.last_good;
  renderProfiles();
}

async function loadLlamaHelp() {
  let payload;
  try {
    payload = await fetchJson("/api/admin/llama/help");
  } catch (error) {
    return;
  }
  llamaState.groups = payload.groups;
  llamaState.help_source = payload.source || "none";
  document.getElementById("llama-help-source").textContent = t(
    "admin.llama.help_source." + llamaState.help_source
  );
  renderProfiles();
}

function optionRow(option, args) {
  const row = document.createElement("div");
  row.className = "option-row";
  const label = document.createElement("label");
  const id = "opt-" + option.flag.replace(/[^\w-]/g, "");
  label.htmlFor = id;
  label.textContent = option.aliases.length
    ? option.flag + " (" + option.aliases.join(", ") + ")"
    : option.flag;
  const input = document.createElement("input");
  input.id = id;
  input.dataset.flag = option.flag;
  if (option.boolean) {
    input.type = "checkbox";
    input.checked = Object.prototype.hasOwnProperty.call(args, option.flag);
  } else {
    input.type = "text";
    input.value = args[option.flag] || "";
    input.placeholder = option.default || option.value;
  }
  const detail = document.createElement("p");
  detail.className = "detail";
  detail.textContent = option.description;
  row.append(label, input, detail);
  return row;
}

function profileForm(profile) {
  const holder = document.createElement("div");
  for (const group of llamaState.groups) {
    if (group.group === "sampling") {
      continue;
    }
    const box = document.createElement("details");
    box.className = "option-group";
    const title = document.createElement("summary");
    title.textContent = t("admin.llama.group." + group.group);
    box.append(title);
    if (group.group === "other") {
      const note = document.createElement("p");
      note.className = "detail";
      note.textContent = t("admin.llama.other_note");
      box.append(note);
    }
    for (const option of group.options) {
      box.append(optionRow(option, profile.args));
    }
    holder.append(box);
  }
  return holder;
}

function collectArgs(card) {
  const args = {};
  for (const input of card.querySelectorAll("input[data-flag]")) {
    if (input.type === "checkbox") {
      if (input.checked) {
        args[input.dataset.flag] = "";
      }
    } else if (input.value.trim()) {
      args[input.dataset.flag] = input.value.trim();
    }
  }
  return args;
}

function profileBadges(profile, title) {
  const badges = [
    [profile.is_default, "admin.llama.active"],
    [profile.last_good, "admin.llama.last_good"],
  ];
  for (const pair of badges) {
    if (!pair[0]) {
      continue;
    }
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = t(pair[1]);
    title.append(" ", badge);
  }
}

function profileCard(profile) {
  const card = document.createElement("div");
  card.className = "profile-card";
  const title = document.createElement("h4");
  title.textContent = profile.name;
  profileBadges(profile, title);
  const mode = document.createElement("select");
  for (const value of ["form", "raw"]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = t("admin.llama.mode." + value);
    option.selected = value === profile.mode;
    mode.append(option);
  }
  const form = profileForm(profile);
  const rawLabel = document.createElement("label");
  rawLabel.textContent = t("admin.llama.raw_label");
  const raw = document.createElement("textarea");
  raw.rows = 3;
  raw.value = profile.raw_cmd;
  const rawNote = document.createElement("p");
  rawNote.className = "detail";
  rawNote.textContent = t("admin.llama.raw_note");
  const rawBox = document.createElement("div");
  rawBox.append(rawLabel, raw, rawNote);
  const command = document.createElement("p");
  command.className = "command-line";
  command.textContent = profile.command_line;
  const notes = document.createElement("ul");
  notes.className = "sources-note";
  for (const note of profile.preflight) {
    const item = document.createElement("li");
    item.className = "state state-degraded";
    item.textContent = t("admin.llama.preflight." + note.code, note.params);
    notes.append(item);
  }
  const applyMode = () => {
    form.hidden = mode.value !== "form";
    rawBox.hidden = mode.value !== "raw";
  };
  mode.addEventListener("change", applyMode);
  applyMode();
  const actions = document.createElement("p");
  const save = document.createElement("button");
  save.type = "button";
  save.textContent = t("admin.llama.save");
  save.addEventListener("click", () =>
    saveProfile(profile.name, mode.value, collectArgs(card), raw.value)
  );
  const test = document.createElement("button");
  test.type = "button";
  test.textContent = t("admin.llama.test");
  test.addEventListener("click", () => testProfile(profile.name, test));
  const remove = document.createElement("button");
  remove.type = "button";
  remove.textContent = t("admin.llama.delete");
  remove.addEventListener("click", () => deleteProfile(profile.name));
  actions.append(save, test, remove);
  card.append(title, mode, form, rawBox, command, notes, actions);
  return card;
}

function renderProfiles() {
  const holder = document.getElementById("llama-profiles");
  holder.replaceChildren();
  if (!llamaState.profiles.length) {
    const empty = document.createElement("p");
    empty.className = "detail";
    empty.textContent = t("admin.llama.profiles_empty");
    holder.append(empty);
    return;
  }
  for (const profile of llamaState.profiles) {
    holder.append(profileCard(profile));
  }
}

async function saveProfile(name, mode, args, raw) {
  try {
    await fetchJson("/api/admin/llama/profiles/" + encodeURIComponent(name), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: mode, args: args, raw_cmd: raw }),
    });
  } catch (error) {
    showMessage("llama-message", t("admin.llama.error", { error: error.message }));
    return;
  }
  showMessage("llama-message", "");
  loadLlama(0);
}

async function deleteProfile(name) {
  if (!window.confirm(t("admin.llama.delete_confirm"))) {
    return;
  }
  try {
    await fetchJson("/api/admin/llama/profiles/" + encodeURIComponent(name), { method: "DELETE" });
  } catch (error) {
    showMessage("llama-message", t("admin.llama.error", { error: error.message }));
    return;
  }
  loadLlama(0);
}

async function testProfile(name, button) {
  button.disabled = true;
  let payload;
  try {
    payload = await fetchJson("/api/admin/llama/profiles/" + encodeURIComponent(name) + "/test", {
      method: "POST",
    });
  } catch (error) {
    showMessage("llama-message", t("admin.llama.error", { error: error.message }));
    button.disabled = false;
    return;
  }
  button.disabled = false;
  const notes = [];
  if (payload.ok) {
    notes.push(t("admin.llama.trial.ok", { name: name }));
  } else {
    notes.push(
      t("admin.llama.trial.failed", {
        name: name,
        stage: t("admin.llama.stage." + payload.stage),
        error: payload.error,
      })
    );
    notes.push(
      payload.rolled_back_to
        ? t("admin.llama.trial.rolled_back", { name: payload.rolled_back_to })
        : t("admin.llama.trial.no_rollback")
    );
  }
  showMessage("llama-message", notes.join(" "));
  if (payload.log.length) {
    document.getElementById("llama-log").textContent = payload.log.join("\n");
  }
  loadLlama(0);
}

async function llamaAction(action) {
  try {
    await fetchJson("/api/admin/llama/" + action, { method: "POST" });
  } catch (error) {
    showMessage("llama-message", t("admin.llama.error", { error: error.message }));
    return;
  }
  showMessage("llama-message", "");
  loadLlama(0);
}

async function createProfile(event) {
  event.preventDefault();
  const field = document.getElementById("llama-profile-name");
  const name = field.value.trim();
  if (!name) {
    return;
  }
  field.value = "";
  await saveProfile(name, "form", {}, "");
}

async function loadChatSettings() {
  let payload;
  try {
    payload = await fetchJson("/api/admin/settings");
  } catch (error) {
    return;
  }
  const chat = payload.chat;
  document.getElementById("chat-max-chunks").value = chat.max_chunks;
  document.getElementById("chat-max-tokens").value = chat.max_evidence_tokens;
  document.getElementById("chat-max-calls").value = chat.max_tool_calls;
  document.getElementById("chat-prompt").value = chat.system_prompt;
  document.getElementById("chat-risk-topics").value = chat.risk_topics.join(", ");
  document.getElementById("chat-sampling").value = JSON.stringify(chat.sampling, null, 2);
  document.getElementById("chat-limits").textContent = t("admin.chat.limits", {
    chunks: chat.limits.max_chunks,
    tokens: chat.limits.max_evidence_tokens,
    calls: chat.limits.max_tool_calls,
  });
}

async function saveChatSettings(event) {
  event.preventDefault();
  let sampling;
  try {
    sampling = JSON.parse(document.getElementById("chat-sampling").value || "{}");
  } catch (error) {
    showMessage("chat-settings-message", t("admin.chat.invalid_sampling"));
    return;
  }
  const topics = document
    .getElementById("chat-risk-topics")
    .value.split(",")
    .map((item) => item.trim())
    .filter((item) => item);
  const chat = {
    max_chunks: Number(document.getElementById("chat-max-chunks").value),
    max_evidence_tokens: Number(document.getElementById("chat-max-tokens").value),
    max_tool_calls: Number(document.getElementById("chat-max-calls").value),
    system_prompt: document.getElementById("chat-prompt").value,
    risk_topics: topics,
    sampling: sampling,
  };
  try {
    await fetchJson("/api/admin/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat: chat }),
    });
  } catch (error) {
    showMessage("chat-settings-message", t("admin.chat.error", { error: error.message }));
    return;
  }
  showMessage("chat-settings-message", t("admin.chat.saved"));
  loadChatSettings();
}

document.getElementById("llama-start").addEventListener("click", () => llamaAction("start"));
document.getElementById("llama-stop").addEventListener("click", () => llamaAction("stop"));
document.getElementById("llama-restart").addEventListener("click", () => llamaAction("restart"));
document.getElementById("llama-new-profile").addEventListener("submit", createProfile);
document.getElementById("chat-settings").addEventListener("submit", saveChatSettings);
loadLlamaHelp();
loadLlama(1);
loadChatSettings();

async function loadSearchSettings() {
  let payload;
  try {
    payload = await fetchJson("/api/admin/settings");
  } catch (error) {
    return;
  }
  const search = payload.search;
  document.getElementById("search-per-source").value = search.per_source;
  document.getElementById("search-zim-weight").value = search.zim_weight;
  document.getElementById("search-doc-weight").value = search.doc_weight;
  document.getElementById("search-snippet").value = search.snippet_chars;
  document.getElementById("search-chunk-min").value = search.chunk_min_tokens;
  document.getElementById("search-chunk-max").value = search.chunk_max_tokens;
}

async function saveSearchSettings(event) {
  event.preventDefault();
  const search = {
    per_source: Number(document.getElementById("search-per-source").value),
    zim_weight: Number(document.getElementById("search-zim-weight").value),
    doc_weight: Number(document.getElementById("search-doc-weight").value),
    snippet_chars: Number(document.getElementById("search-snippet").value),
    chunk_min_tokens: Number(document.getElementById("search-chunk-min").value),
    chunk_max_tokens: Number(document.getElementById("search-chunk-max").value),
  };
  try {
    await fetchJson("/api/admin/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ search: search }),
    });
  } catch (error) {
    showMessage("search-settings-message", t("admin.search.error", { error: error.message }));
    return;
  }
  showMessage("search-settings-message", t("admin.search.saved"));
  loadSearchSettings();
}

async function loadMaintenance() {
  let payload;
  try {
    payload = await fetchJson("/api/admin/maintenance");
  } catch (error) {
    return;
  }
  const paths = document.getElementById("maintenance-paths");
  paths.replaceChildren();
  for (const key of ["root", "config", "database", "logs", "session"]) {
    const item = document.createElement("li");
    const label = document.createElement("span");
    label.textContent = t("admin.maintenance.path." + key) + ": ";
    const value = document.createElement("span");
    value.className = "seal-path";
    value.textContent = payload.paths[key];
    item.append(label, value);
    paths.append(item);
  }
  document.getElementById("maintenance-rotation").textContent = t("admin.maintenance.rotation", {
    size: formatBytes(payload.rotation.max_bytes),
    backups: payload.rotation.backups,
  });
  const logs = document.getElementById("maintenance-logs");
  logs.replaceChildren();
  if (!payload.logs.length) {
    const empty = document.createElement("li");
    empty.textContent = t("admin.maintenance.logs_empty");
    logs.append(empty);
    return;
  }
  for (const entry of payload.logs) {
    const item = document.createElement("li");
    item.textContent = entry.name + " · " + entry.size_human;
    logs.append(item);
  }
}

async function verifyIntegrity() {
  const button = document.getElementById("integrity-button");
  button.disabled = true;
  showMessage("integrity-message", t("admin.maintenance.verify_running"));
  let payload;
  try {
    payload = await fetchJson("/api/admin/maintenance/verify", { method: "POST" });
  } catch (error) {
    showMessage("integrity-message", error.message);
    button.disabled = false;
    return;
  }
  button.disabled = false;
  const body = document.getElementById("integrity-body");
  body.replaceChildren();
  if (!payload.items.length) {
    showMessage("integrity-message", t("admin.maintenance.verify_empty"));
    return;
  }
  const ok = payload.counts.ok || 0;
  showMessage(
    "integrity-message",
    t("admin.maintenance.verify_summary", {
      total: payload.items.length,
      ok: ok,
      bad: payload.items.length - ok,
    })
  );
  for (const item of payload.items) {
    const row = document.createElement("tr");
    const result = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = "state state-" + (item.result === "ok" ? "ok" : "degraded");
    badge.textContent = t("admin.maintenance.result." + item.result);
    result.append(badge);
    row.append(
      cell(t("admin.maintenance.kind." + item.kind)),
      cell(item.name),
      result,
      cell(item.detail || "-")
    );
    body.append(row);
  }
}

async function exportConfig() {
  let payload;
  try {
    payload = await fetchJson("/api/admin/maintenance/config");
  } catch (error) {
    showMessage("config-message", error.message);
    return;
  }
  document.getElementById("config-text").value = JSON.stringify(payload, null, 2);
  showMessage("config-message", "");
}

async function importConfig() {
  let parsed;
  try {
    parsed = JSON.parse(document.getElementById("config-text").value || "{}");
  } catch (error) {
    showMessage("config-message", t("admin.maintenance.import_error", { error: error.message }));
    return;
  }
  try {
    await fetchJson("/api/admin/maintenance/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ settings: parsed.settings || parsed }),
    });
  } catch (error) {
    showMessage("config-message", t("admin.maintenance.import_error", { error: error.message }));
    return;
  }
  showMessage("config-message", t("admin.maintenance.imported"));
  loadSearchSettings();
  loadChatSettings();
}

async function rebuildVendorBatch() {
  const button = document.getElementById("vendor-index-button");
  button.disabled = true;
  let payload;
  try {
    payload = await fetchJson("/api/admin/vendor/index", { method: "POST" });
  } catch (error) {
    showMessage("vendor-message", error.message);
    button.disabled = false;
    return;
  }
  button.disabled = false;
  if (payload.sealed) {
    showMessage(
      "vendor-message",
      t("admin.vendor.index_sealed", {
        shard: payload.shard_id,
        packages: payload.packages,
        chunks: payload.chunks,
      })
    );
    return;
  }
  const reasons = {
    nothing_to_index: "admin.vendor.index_nothing",
    embeddings_down: "admin.vendor.index_no_server",
    seal_failed: "admin.vendor.index_failed",
  };
  showMessage("vendor-message", t(reasons[payload.reason] || "admin.vendor.index_failed"));
}

document.getElementById("search-settings").addEventListener("submit", saveSearchSettings);
document.getElementById("integrity-button").addEventListener("click", verifyIntegrity);
document.getElementById("config-export").addEventListener("click", exportConfig);
document.getElementById("config-import").addEventListener("click", importConfig);
document.getElementById("vendor-index-button").addEventListener("click", rebuildVendorBatch);
loadSearchSettings();
loadMaintenance();
