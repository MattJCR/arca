"use strict";

const chatState = { selection: null, busy: false };

function renderChatState(checks) {
  const badge = document.getElementById("chat-state");
  const notice = document.getElementById("chat-unavailable");
  const server = checks.find((check) => check.id === "llama_server");
  const ready = Boolean(server) && server.state === "ok";
  badge.textContent = t(ready ? "home.chat.state.ok" : "home.chat.state.unavailable");
  badge.className = "state " + (ready ? "state-ok" : "state-degraded");
  notice.hidden = ready;
}

function screenState() {
  const payload = { query: state.query || "", results: [] };
  for (const result of (state.results || []).slice(0, 5)) {
    payload.results.push({
      title: result.title,
      source: result.source_title,
      snippet: result.snippet || "",
    });
  }
  if (state.current) {
    payload.open_document = {
      doc_id: state.current.doc_id || "",
      source_kind: state.current.source_kind || "",
      source_id: state.current.source_id || "",
      path: state.current.path || "",
      title: state.current.title || "",
      page: state.current.page || 0,
    };
  }
  if (chatState.selection) {
    payload.selection = chatState.selection;
  }
  return payload;
}

function renderSelection() {
  const strip = document.getElementById("chat-selection");
  strip.replaceChildren();
  if (!chatState.selection) {
    strip.hidden = true;
    return;
  }
  const hint = document.createElement("span");
  hint.className = "seal-kind";
  hint.textContent = t("home.chat.selection_hint");
  const anchor = document.createElement("span");
  anchor.className = "seal-path";
  anchor.textContent = chatState.selection.anchor || "";
  const drop = document.createElement("button");
  drop.type = "button";
  drop.textContent = t("home.chat.selection_clear");
  drop.addEventListener("click", () => {
    chatState.selection = null;
    renderSelection();
  });
  strip.append(hint, anchor, drop);
  strip.hidden = false;
}

function chatEntry(role, text) {
  const item = document.createElement("li");
  item.className = "chat-entry chat-" + role;
  const who = document.createElement("p");
  who.className = "chat-role";
  who.textContent = t(role === "you" ? "home.chat.you" : "home.chat.assistant");
  const body = document.createElement("p");
  body.className = "chat-text";
  body.textContent = text;
  item.append(who, body);
  return item;
}

function evidenceFor(payload, marker) {
  return payload.evidence.find((item) => item.marker === marker) || null;
}

function citedAnswer(payload) {
  const body = document.createElement("p");
  body.className = "chat-text";
  const parts = payload.text.split(/(\[\d{1,2}\])/);
  for (const part of parts) {
    const match = part.match(/^\[(\d{1,2})\]$/);
    const source = match ? evidenceFor(payload, Number(match[1])) : null;
    if (!source) {
      body.append(document.createTextNode(part));
      continue;
    }
    const link = document.createElement("a");
    link.className = "chat-citation";
    link.href = source.url;
    link.textContent = part;
    link.title = source.source_title;
    link.addEventListener("click", (event) => {
      event.preventDefault();
      openResult(asResult(source));
    });
    body.append(link);
  }
  return body;
}

function asResult(source) {
  return {
    source_kind: source.source_kind,
    source_id: source.doc_id,
    source_title: source.source_title,
    title: source.title || source.source_title,
    path: source.anchor,
    url: source.url,
    rank: source.marker - 1,
    doc_id: source.doc_id,
    anchor: source.anchor,
    page: source.page,
    snippet: source.text,
    original_url: source.original_url,
  };
}

function evidenceList(payload) {
  const list = document.createElement("ol");
  list.className = "chat-evidence";
  for (const source of payload.evidence) {
    const item = document.createElement("li");
    const link = document.createElement("a");
    link.href = source.url;
    link.textContent = "[" + source.marker + "] " + source.source_title;
    link.addEventListener("click", (event) => {
      event.preventDefault();
      openResult(asResult(source));
    });
    item.append(link);
    if (source.anchor) {
      const stamp = document.createElement("span");
      stamp.className = "seal-path";
      stamp.textContent = source.anchor;
      item.append(" ", stamp);
    }
    list.append(item);
  }
  return list;
}

function renderReply(payload) {
  const log = document.getElementById("chat-log");
  const item = document.createElement("li");
  item.className = "chat-entry chat-assistant";
  const who = document.createElement("p");
  who.className = "chat-role";
  who.textContent = t("home.chat.assistant");
  item.append(who, citedAnswer(payload));
  if (payload.state === "ungrounded") {
    const note = document.createElement("p");
    note.className = "detail chat-ungrounded";
    note.textContent = t("chat.ungrounded");
    item.append(note);
  }
  if (payload.warning === "risk_without_citation") {
    const warning = document.createElement("p");
    warning.className = "state state-degraded chat-warning";
    warning.textContent = t("home.chat.risk_warning");
    item.append(warning);
  }
  if (payload.evidence.length) {
    const heading = document.createElement("p");
    heading.className = "detail";
    heading.textContent = t("home.chat.evidence_heading");
    item.append(heading, evidenceList(payload));
  }
  if (payload.searches.length) {
    const searched = document.createElement("p");
    searched.className = "detail";
    searched.textContent = t("home.chat.searched", { queries: payload.searches.join(" · ") });
    item.append(searched);
  }
  log.append(item);
  item.scrollIntoView({ block: "nearest" });
}

async function askAssistant(event) {
  event.preventDefault();
  const input = document.getElementById("chat-input");
  const question = input.value.trim();
  if (!question || chatState.busy) {
    return;
  }
  chatState.busy = true;
  document.getElementById("chat-log").append(chatEntry("you", question));
  input.value = "";
  showMessage("chat-message", t("home.chat.thinking"));
  let payload;
  try {
    payload = await fetchJson("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: question, screen: screenState() }),
    });
  } catch (error) {
    showMessage("chat-message", t("home.chat.error", { error: error.message }));
    chatState.busy = false;
    return;
  }
  showMessage("chat-message", "");
  chatState.busy = false;
  renderReply(payload);
  const badge = document.getElementById("chat-state");
  const ready = payload.state !== "unavailable";
  badge.textContent = t(ready ? "home.chat.state.ok" : "home.chat.state.unavailable");
  badge.className = "state " + (ready ? "state-ok" : "state-degraded");
  document.getElementById("chat-unavailable").hidden = ready;
}

window.addEventListener("message", (event) => {
  if (event.origin !== window.location.origin) {
    return;
  }
  const data = event.data;
  if (!data || data.type !== "arca:selection" || !data.detail) {
    return;
  }
  chatState.selection = data.detail;
  renderSelection();
});

document.getElementById("chat-form").addEventListener("submit", askAssistant);
renderSelection();
