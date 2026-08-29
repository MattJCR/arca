"use strict";

const I18N = JSON.parse(document.getElementById("i18n-data").textContent);
const DOWNLOAD_POLL_MS = 3000;
const STATUS_POLL_MS = 15000;
const ENGINE_POLL_MS = 4000;
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
  for (const element of document.querySelectorAll("[data-i18n-title]")) {
    element.title = t(element.dataset.i18nTitle);
  }
  for (const element of document.querySelectorAll("[data-i18n-label]")) {
    element.setAttribute("aria-label", t(element.dataset.i18nLabel));
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

function bar(fraction, tone) {
  const track = document.createElement("span");
  track.className = tone ? "bar bar-" + tone : "bar";
  const fill = document.createElement("span");
  fill.style.width = Math.max(0, Math.min(100, fraction * 100)).toFixed(1) + "%";
  track.append(fill);
  return track;
}

function metricCard(tone, iconName, label, value, unit, foot, fraction) {
  const item = document.createElement("li");
  item.className = "metric metric-" + tone;
  const head = document.createElement("p");
  head.className = "metric-label";
  head.append(svgIcon(iconName, 13), label);
  const figure = document.createElement("p");
  figure.className = "metric-value";
  figure.append(value);
  if (unit) {
    const small = document.createElement("small");
    small.textContent = unit;
    figure.append(small);
  }
  item.append(head, figure);
  if (fraction !== null) {
    item.append(bar(fraction, tone === "accent" ? "" : tone));
  }
  const note = document.createElement("p");
  note.className = "metric-foot";
  note.textContent = foot;
  item.append(note);
  return item;
}

function levelCell(level) {
  const td = document.createElement("td");
  const badge = document.createElement("span");
  badge.className = "level level-" + level;
  badge.textContent = level;
  td.append(badge);
  return td;
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok) {
    const failure = new Error(payload.error || response.statusText);
    failure.code = payload.code || "";
    throw failure;
  }
  return payload;
}

function errorDetail(family, error) {
  const key = error.code ? family + "." + error.code : "";
  return key && I18N[key] !== undefined ? t(key) : error.message;
}

function errorText(family, error) {
  const key = error.code ? family + "." + error.code : "";
  if (key && I18N[key] !== undefined) {
    return t(key);
  }
  return t(family, { error: error.message });
}

function usedByArca(usage) {
  return usage.zim + usage.docs + usage.models + usage.vendor;
}

function shareOf(part, whole) {
  return whole > 0 ? part / whole : 0;
}

function renderStatusMetrics(payload) {
  const summary = document.getElementById("status-summary");
  summary.className = "metrics";
  summary.replaceChildren();
  const usage = payload.usage;
  const mine = usedByArca(usage);
  summary.append(
    metricCard(
      "green",
      "disk",
      t("admin.status.metric.free"),
      formatBytes(payload.free_bytes),
      t("admin.status.metric.of_total", { total: formatBytes(payload.total_bytes) }),
      t("admin.status.metric.free_share", {
        percent: Math.round(100 * shareOf(payload.free_bytes, payload.total_bytes)),
      }),
      shareOf(payload.free_bytes, payload.total_bytes)
    ),
    metricCard(
      "accent",
      "db",
      t("admin.status.metric.zim"),
      formatBytes(usage.zim),
      "",
      t("admin.status.metric.zim_foot", { count: payload.counts.zim }),
      shareOf(usage.zim, mine)
    ),
    metricCard(
      "info",
      "doc",
      t("admin.status.metric.docs"),
      formatBytes(usage.docs),
      "",
      t("admin.status.metric.docs_foot", {
        documents: payload.counts.documents,
        shards: payload.counts.shards,
      }),
      shareOf(usage.docs, mine)
    ),
    metricCard(
      "amber",
      "cpu",
      t("admin.status.metric.models"),
      formatBytes(usage.models),
      "",
      t("admin.status.metric.models_foot", { size: formatBytes(usage.vendor) }),
      shareOf(usage.models, mine)
    )
  );
  if (payload.counts.active_downloads) {
    const active = document.createElement("li");
    active.className = "metric metric-accent";
    const head = document.createElement("p");
    head.className = "metric-label";
    head.append(svgIcon("download", 13), t("admin.status.metric.downloads"));
    const figure = document.createElement("p");
    figure.className = "metric-value";
    figure.append(String(payload.counts.active_downloads));
    active.append(head, figure);
    summary.append(active);
  }
}

function renderWorstState(worst) {
  const badge = document.getElementById("health-badge");
  badge.className = "state state-" + worst;
  badge.textContent = t("admin.status.badge." + worst);
  badge.title = t("admin.status.worst." + worst);
  badge.hidden = false;
}

async function loadStatus() {
  let payload;
  try {
    payload = await fetchJson("/api/admin/status");
  } catch (error) {
    return;
  }
  renderStatusMetrics(payload);
  renderWorstState(payload.worst_state);
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
    const area = document.createElement("td");
    const tag = document.createElement("span");
    tag.className = "tech";
    tag.textContent = event.area;
    area.append(tag);
    row.append(cell(event.ts, "stamp"), levelCell(event.level), area, cell(event.message));
    eventsBody.append(row);
  }
}

async function loadCatalogFilters() {
  let payload;
  try {
    payload = await fetchJson("/api/admin/catalog/filters");
  } catch (error) {
    showMessage("catalog-message", errorText("admin.catalog.filters_error", error));
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
    showMessage("catalog-message", errorText("admin.catalog.error", error));
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
    showMessage("downloads-message", errorText("admin.catalog.add_error", error));
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
    const fraction = download.size_bytes ? download.bytes_done / download.size_bytes : 0;
    const percent = download.size_bytes ? (100 * fraction).toFixed(1) + "%" : "?";
    const progress = document.createElement("td");
    const gauge = document.createElement("span");
    gauge.className = "progress-cell";
    const tone =
      download.status === "failed" ? "red" : download.status === "done" ? "green" : "running";
    const track = bar(fraction, tone);
    track.title = download.done_human + " / " + download.size_human;
    const reading = document.createElement("span");
    reading.className = "num";
    reading.textContent = percent;
    gauge.append(track, reading);
    progress.append(gauge);
    row.append(
      cell(download.filename),
      progress,
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

async function uploadDocuments(event) {
  event.preventDefault();
  const input = document.getElementById("documents-file");
  const files = Array.from(input.files || []);
  if (!files.length) {
    return;
  }
  const button = event.target.querySelector("button");
  button.disabled = true;
  let added = 0;
  let duplicates = 0;
  const report = [];
  for (const file of files) {
    showMessage("documents-message", t("admin.documents.uploading", { filename: file.name }));
    let payload;
    try {
      payload = await fetchJson(
        "/api/admin/documents/upload?filename=" + encodeURIComponent(file.name),
        { method: "POST", headers: { "Content-Type": "application/pdf" }, body: file }
      );
    } catch (error) {
      showMessage(
        "documents-message",
        t("admin.documents.upload_error", {
          filename: file.name,
          error: errorDetail("admin.documents.upload_error", error),
        })
      );
      button.disabled = false;
      return;
    }
    if (payload.created) {
      added += 1;
      report.push(t("admin.documents.file_added", { filename: file.name, pages: payload.pages }));
    } else {
      duplicates += 1;
      report.push(t("admin.documents.file_duplicate", { filename: file.name }));
    }
  }
  button.disabled = false;
  input.value = "";
  const summary = document.getElementById("documents-uploaded");
  summary.replaceChildren();
  for (const line of report) {
    const item = document.createElement("li");
    item.textContent = line;
    summary.append(item);
  }
  summary.hidden = !report.length;
  await loadDocuments();
  showMessage("documents-message", t("admin.documents.uploaded", { added, duplicates }));
}

async function indexDocuments() {
  const button = document.getElementById("documents-index-button");
  button.disabled = true;
  showMessage("documents-message", t("admin.documents.indexing"));
  let payload;
  try {
    payload = await fetchJson("/api/admin/documents/index", { method: "POST" });
  } catch (error) {
    showMessage("documents-message", error.message);
    button.disabled = false;
    return;
  }
  button.disabled = false;
  await loadDocuments();
  if (payload.sealed) {
    showMessage(
      "documents-message",
      t("admin.documents.indexed", {
        shard: payload.shard_id,
        documents: payload.documents,
        chunks: payload.chunks,
      })
    );
    return;
  }
  const reasons = {
    nothing_to_index: "admin.documents.index_nothing",
    embeddings_down: "admin.documents.index_no_server",
    no_model: "admin.documents.embedding_missing",
    seal_failed: "admin.documents.index_failed",
  };
  showMessage("documents-message", t(reasons[payload.reason] || "admin.documents.index_failed"));
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
  const pending = payload.pending || [];
  const summary = document.getElementById("documents-pending");
  summary.textContent = pending.length
    ? t("admin.documents.pending", { count: pending.length })
    : t("admin.documents.pending_none");
  document.getElementById("documents-index-button").disabled = !pending.length;
  const notes = [];
  if (payload.provisional.length) {
    notes.push(t("admin.documents.provisional", { shards: payload.provisional.join(", ") }));
  }
  if (!payload.embedding_available) {
    const key = payload.embedding_model
      ? "admin.documents.embedding_down"
      : "admin.documents.embedding_missing";
    notes.push(t(key, { url: payload.embedding_server }));
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
document.getElementById("documents-upload").addEventListener("submit", uploadDocuments);
document.getElementById("documents-index-button").addEventListener("click", indexDocuments);
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

function engineDetail(payload) {
  if (payload.state === "ready" && payload.binary) {
    return t("admin.engine.detail.ready", { binary: payload.binary, profile: payload.profile });
  }
  if (payload.candidate) {
    return t("admin.engine.detail.candidate", { candidate: payload.candidate });
  }
  return payload.detail || "";
}

const ENGINE_TONE = {
  ready: "state-ok",
  failed: "state-broken",
  exhausted: "state-broken",
  unsupported: "state-broken",
};

function engineLine(id, key, params, state) {
  const element = document.getElementById(id);
  element.replaceChildren();
  const badge = document.createElement("span");
  badge.className = "state " + (ENGINE_TONE[state] || "state-degraded");
  badge.textContent = t("admin.engine.state." + state);
  element.append(document.createTextNode(t(key, params) + " "), badge);
}

function renderEngine(payload) {
  engineLine("engine-state", "admin.engine.chat_label", {}, payload.state);
  engineLine(
    "engine-embed",
    "admin.engine.embed_label",
    { model: payload.embed_model || t("admin.engine.no_model") },
    payload.embed_state
  );
  const detail = document.getElementById("engine-detail");
  detail.textContent = engineDetail(payload);
  detail.hidden = !detail.textContent;
  document.getElementById("engine-start").disabled = payload.running;
  if (payload.running) {
    window.setTimeout(loadEngine, ENGINE_POLL_MS);
    return;
  }
  if (payload.state === "ready") {
    loadLlama(0);
  }
}

async function loadEngine() {
  let payload;
  try {
    payload = await fetchJson("/api/admin/engine");
  } catch (error) {
    showMessage("llama-message", errorText("admin.llama.error", error));
    return;
  }
  renderEngine(payload);
}

async function startEngine() {
  let payload;
  try {
    payload = await fetchJson("/api/admin/engine/start", { method: "POST" });
  } catch (error) {
    showMessage("llama-message", errorText("admin.llama.error", error));
    return;
  }
  showMessage("llama-message", "");
  renderEngine(payload);
  window.setTimeout(loadEngine, ENGINE_POLL_MS);
}

async function loadLlama(refresh) {
  let payload;
  try {
    payload = await fetchJson("/api/admin/llama" + (refresh ? "?refresh=1" : ""));
  } catch (error) {
    showMessage("llama-message", errorText("admin.llama.error", error));
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
    showMessage("llama-message", errorText("admin.llama.error", error));
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
    showMessage("llama-message", errorText("admin.llama.error", error));
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
    showMessage("llama-message", errorText("admin.llama.error", error));
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
    showMessage("llama-message", errorText("admin.llama.error", error));
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
document.getElementById("engine-start").addEventListener("click", startEngine);
document.getElementById("chat-settings").addEventListener("submit", saveChatSettings);
loadLlamaHelp();
loadLlama(1);
loadEngine();
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

const manualState = { current: "" };

function manualButton(page) {
  const item = document.createElement("li");
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = page.title;
  button.title = page.name + ".md · " + page.size_human;
  button.setAttribute("aria-current", String(page.name === manualState.current));
  button.addEventListener("click", () => openManualPage(page.name));
  item.append(button);
  return item;
}

function openManualPage(name) {
  manualState.current = name;
  document.getElementById("manual-frame").src = "/api/manual/" + encodeURIComponent(name);
  loadManual();
}

async function loadManual() {
  let payload;
  try {
    payload = await fetchJson("/api/manual");
  } catch (error) {
    showMessage("manual-message", error.message);
    return;
  }
  const list = document.getElementById("manual-pages");
  list.replaceChildren();
  if (!payload.pages.length) {
    showMessage("manual-message", t("admin.manual.empty"));
    return;
  }
  showMessage("manual-message", "");
  if (!manualState.current) {
    manualState.current = payload.pages[0].name;
    document.getElementById("manual-frame").src =
      "/api/manual/" + encodeURIComponent(manualState.current);
  }
  for (const page of payload.pages) {
    list.append(manualButton(page));
  }
}

loadManual();

const hubState = { models: [], current: "", files: [] };
const EXPECTED_DIMS = 768;

function buildLabel(file) {
  if (!file.architecture) {
    return t("admin.hub.unknown_build");
  }
  return file.dims
    ? t("admin.hub.build", { architecture: file.architecture, dims: file.dims })
    : file.architecture;
}

function hubKind() {
  return document.getElementById("hub-kind").value;
}

async function searchHub(event) {
  event.preventDefault();
  const query = document.getElementById("hub-q").value.trim();
  if (!query) {
    return;
  }
  showMessage("hub-message", t("admin.hub.searching"));
  let payload;
  try {
    payload = await fetchJson("/api/admin/hub?q=" + encodeURIComponent(query));
  } catch (error) {
    showMessage("hub-message", errorText("admin.hub.error", error));
    document.getElementById("hub-table-wrap").hidden = true;
    return;
  }
  hubState.models = payload.models;
  document.getElementById("hub-detail").hidden = true;
  renderHubModels();
}

function renderHubModels() {
  const body = document.getElementById("hub-body");
  const wrap = document.getElementById("hub-table-wrap");
  body.replaceChildren();
  if (!hubState.models.length) {
    wrap.hidden = true;
    showMessage("hub-message", t("admin.hub.empty"));
    return;
  }
  showMessage("hub-message", "");
  wrap.hidden = false;
  for (const model of hubState.models) {
    const row = document.createElement("tr");
    const name = cell(model.model_id);
    if (model.gated) {
      const badge = document.createElement("span");
      badge.className = "badge badge-update";
      badge.textContent = t("admin.hub.gated");
      name.append(" ", badge);
    }
    const actions = document.createElement("td");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = t("admin.hub.inspect");
    button.addEventListener("click", () => inspectHubModel(model.model_id, button));
    actions.append(button);
    row.append(
      name,
      cell(model.downloads.toLocaleString(), "num"),
      cell(model.likes.toLocaleString(), "num"),
      cell((model.updated_at || "").slice(0, 10)),
      actions
    );
    body.append(row);
  }
}

async function inspectHubModel(modelId, button) {
  button.disabled = true;
  let payload;
  try {
    payload = await fetchJson("/api/admin/hub/" + modelId);
  } catch (error) {
    showMessage("hub-message", errorText("admin.hub.error", error));
    button.disabled = false;
    return;
  }
  button.disabled = false;
  hubState.current = payload.model_id;
  hubState.files = payload.files;
  document.getElementById("hub-detail-title").textContent = payload.model_id;
  document.getElementById("hub-detail-tags").textContent = payload.tags.slice(0, 8).join(" · ");
  const body = document.getElementById("hub-files-body");
  body.replaceChildren();
  if (!payload.files.length) {
    body.append(emptyRow(t("admin.hub.no_files"), 5));
  }
  for (const file of payload.files) {
    body.append(hubFileRow(payload.model_id, file, payload.local));
  }
  document.getElementById("hub-detail").hidden = false;
}

function hubFileRow(modelId, file, local) {
  const row = document.createElement("tr");
  const name = file.path.split("/").pop();
  const quantization = cell(file.quantization || "-");
  if (file.part) {
    quantization.append(" ", document.createTextNode(t("admin.hub.part", { part: file.part })));
  }
  const hash = cell(file.sha256 ? file.sha256.slice(0, 12) : t("admin.hub.no_hash"));
  if (!file.sha256) {
    hash.className = "state state-degraded";
  }
  const actions = document.createElement("td");
  if (local.includes(name)) {
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = t("admin.hub.in_library");
    actions.append(badge);
  } else {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = t("admin.hub.download");
    button.addEventListener("click", () => downloadHubFile(modelId, file.path, button));
    actions.append(button);
  }
  const build = cell(buildLabel(file));
  if (file.dims && file.dims !== EXPECTED_DIMS && hubKind() === "embed") {
    build.className = "state state-degraded";
    build.title = t("admin.hub.dims_mismatch", { dims: file.dims, expected: EXPECTED_DIMS });
  }
  row.append(quantization, cell(name), cell(file.size_human, "num"), build, hash, actions);
  return row;
}

async function downloadHubFile(modelId, path, button) {
  button.disabled = true;
  let payload;
  try {
    payload = await fetchJson("/api/admin/hub/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model_id: modelId, path: path, kind: hubKind() }),
    });
  } catch (error) {
    showMessage("hub-message", errorText("admin.hub.error", error));
    button.disabled = false;
    return;
  }
  showMessage(
    "hub-message",
    payload.hash_missing
      ? t("admin.hub.queued_no_hash", { filename: payload.filename })
      : t("admin.hub.queued", { filename: payload.filename })
  );
  loadDownloads();
}

async function pinLlamaCommit(event) {
  event.preventDefault();
  const field = document.getElementById("llama-commit");
  const commit = field.value.trim();
  if (!commit) {
    return;
  }
  let payload;
  try {
    payload = await fetchJson("/api/admin/vendor/llama", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ commit: commit }),
    });
  } catch (error) {
    showMessage("vendor-message", errorText("admin.vendor.llama_error", error));
    return;
  }
  showMessage(
    "vendor-message",
    t("admin.vendor.llama_pinned", { commit: payload.commit, queued: payload.queued })
  );
  loadVendor(false);
  loadDownloads();
}

document.getElementById("hub-form").addEventListener("submit", searchHub);
document.getElementById("llama-commit-form").addEventListener("submit", pinLlamaCommit);
