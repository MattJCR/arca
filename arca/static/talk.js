"use strict";

const I18N = JSON.parse(document.getElementById("i18n-data").textContent);
const talkState = {
  conversations: [],
  current: 0,
  messages: [],
  pending: [],
  vision: { available: false, reason: "no_engine" },
  persistent: true,
  busy: false,
};

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

function errorText(family, error) {
  const key = error.code ? family + "." + error.code : "";
  if (key && I18N[key] !== undefined) {
    return t(key);
  }
  return t(family, { error: error.message });
}

function renderEngine(ready) {
  const badge = document.getElementById("talk-state");
  badge.textContent = t(ready ? "talk.state.ok" : "talk.state.unavailable");
  badge.className = "state " + (ready ? "state-ok" : "state-degraded");
}

function renderVision() {
  const note = document.getElementById("talk-vision");
  note.textContent = t("talk.vision." + talkState.vision.reason);
  note.hidden = talkState.vision.available;
  const button = document.getElementById("talk-attach");
  button.hidden = !talkState.vision.available || !talkState.persistent;
}

function renderStore() {
  showMessage("talk-store", talkState.persistent ? "" : t("talk.memory_only"));
}

function threadEntry(conversation) {
  const item = document.createElement("li");
  const button = document.createElement("button");
  button.type = "button";
  button.className = "thread";
  if (conversation.id === talkState.current) {
    button.setAttribute("aria-current", "true");
  }
  const title = document.createElement("span");
  title.className = "thread-title";
  title.append(svgIcon("thread", 12), conversation.title);
  const count = document.createElement("span");
  count.className = "seal-path";
  count.textContent = t("talk.conversations.count", { count: conversation.message_count });
  button.append(title, count);
  button.addEventListener("click", () => openThread(conversation.id));
  item.append(button);
  return item;
}

function renderThreads() {
  const list = document.getElementById("talk-threads");
  list.replaceChildren(...talkState.conversations.map(threadEntry));
  document.getElementById("talk-threads-empty").hidden = talkState.conversations.length > 0;
}

function pictureList(message) {
  const list = document.createElement("ul");
  list.className = "talk-pictures";
  for (const picture of message.attachments) {
    const item = document.createElement("li");
    const image = document.createElement("img");
    image.src = picture.url;
    image.alt = t("talk.image_alt");
    image.loading = "lazy";
    item.append(image);
    list.append(item);
  }
  return list;
}

function messageEntry(message) {
  const item = document.createElement("li");
  const mine = message.role === "user";
  item.className = "chat-entry " + (mine ? "chat-you" : "chat-assistant");
  const who = document.createElement("p");
  who.className = "chat-role";
  if (!mine) {
    who.append(svgIcon("chat", 12));
  }
  who.append(t(mine ? "talk.you" : "talk.assistant"));
  const body = document.createElement("p");
  body.className = "chat-text";
  body.textContent = message.content;
  item.append(who, body);
  if (message.attachments.length) {
    item.append(pictureList(message));
  }
  return item;
}

function renderThread() {
  const log = document.getElementById("talk-log");
  log.replaceChildren(...talkState.messages.map(messageEntry));
  const open = talkState.current > 0;
  document.getElementById("talk-form").hidden = !open;
  document.getElementById("talk-rename").hidden = !open;
  document.getElementById("talk-delete").hidden = !open;
  const invite = document.getElementById("talk-invite");
  invite.textContent = t(open ? "talk.empty_thread" : "talk.pick");
  invite.hidden = open && talkState.messages.length > 0;
  if (talkState.messages.length) {
    log.lastElementChild.scrollIntoView({ block: "nearest" });
  }
}

function renderTray() {
  const tray = document.getElementById("talk-tray");
  tray.replaceChildren();
  for (const picture of talkState.pending) {
    const item = document.createElement("li");
    const image = document.createElement("img");
    image.src = picture.url;
    image.alt = t("talk.image_alt");
    const drop = document.createElement("button");
    drop.type = "button";
    drop.textContent = t("talk.image_remove");
    drop.addEventListener("click", () => {
      talkState.pending = talkState.pending.filter((item) => item.id !== picture.id);
      renderTray();
    });
    item.append(image, drop);
    tray.append(item);
  }
  tray.hidden = talkState.pending.length === 0;
}

function renderSettings(settings) {
  document.getElementById("talk-system").value = settings.system_prompt;
  document.getElementById("talk-temperature").value = settings.sampling.temperature;
  document.getElementById("talk-predict").value = settings.sampling.n_predict;
  document.getElementById("talk-history").value = settings.max_history_tokens;
}

async function loadState() {
  let payload;
  try {
    payload = await fetchJson("/api/talk");
  } catch (error) {
    showMessage("talk-message", errorText("talk.error", error));
    return;
  }
  talkState.conversations = payload.conversations;
  talkState.vision = payload.vision;
  talkState.persistent = payload.persistent;
  renderEngine(payload.ready);
  renderVision();
  renderStore();
  renderSettings(payload.settings);
  renderThreads();
}

async function openThread(conversationId) {
  if (talkState.busy) {
    return;
  }
  let payload;
  try {
    payload = await fetchJson("/api/talk/" + conversationId);
  } catch (error) {
    showMessage("talk-message", errorText("talk.error", error));
    await loadState();
    return;
  }
  talkState.current = payload.conversation.id;
  talkState.messages = payload.messages;
  talkState.pending = [];
  showMessage("talk-message", "");
  renderTray();
  renderThreads();
  renderThread();
}

async function newThread() {
  if (talkState.busy) {
    return;
  }
  let payload;
  try {
    payload = await fetchJson("/api/talk", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: "" }),
    });
  } catch (error) {
    showMessage("talk-message", errorText("talk.error", error));
    return;
  }
  await loadState();
  await openThread(payload.id);
}

async function renameThread() {
  const current = talkState.conversations.find((item) => item.id === talkState.current);
  const title = window.prompt(t("talk.rename_prompt"), current ? current.title : "");
  if (title === null) {
    return;
  }
  try {
    await fetchJson("/api/talk/" + talkState.current, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: title }),
    });
  } catch (error) {
    showMessage("talk-message", errorText("talk.error", error));
    return;
  }
  await loadState();
}

async function deleteThread() {
  if (talkState.busy) {
    return;
  }
  if (!window.confirm(t("talk.delete_confirm"))) {
    return;
  }
  try {
    await fetchJson("/api/talk/" + talkState.current, { method: "DELETE" });
  } catch (error) {
    showMessage("talk-message", errorText("talk.error", error));
    return;
  }
  talkState.current = 0;
  talkState.messages = [];
  talkState.pending = [];
  renderTray();
  renderThread();
  await loadState();
}

async function clearAll() {
  if (talkState.busy) {
    return;
  }
  if (!window.confirm(t("talk.clear_confirm"))) {
    return;
  }
  try {
    await fetchJson("/api/talk", { method: "DELETE" });
  } catch (error) {
    showMessage("talk-message", errorText("talk.error", error));
    return;
  }
  talkState.current = 0;
  talkState.messages = [];
  talkState.pending = [];
  renderTray();
  renderThread();
  await loadState();
}

async function attachPictures(files) {
  for (const file of files) {
    try {
      const picture = await fetchJson("/api/talk/" + talkState.current + "/image", {
        method: "POST",
        headers: { "Content-Type": file.type || "application/octet-stream" },
        body: file,
      });
      talkState.pending.push(picture);
    } catch (error) {
      showMessage("talk-message", errorText("talk.error", error));
    }
  }
  renderTray();
}

function canStream() {
  return typeof window.ReadableStream === "function" && typeof window.TextDecoder === "function";
}

function showStop(active) {
  document.getElementById("talk-stop").hidden = !active;
}

function liveReply() {
  const log = document.getElementById("talk-log");
  const item = messageEntry({ role: "assistant", content: "", attachments: [] });
  log.append(item);
  document.getElementById("talk-invite").hidden = true;
  item.scrollIntoView({ block: "nearest" });
  return item.querySelector(".chat-text");
}

async function readEvents(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const step = await reader.read();
    if (step.done) {
      break;
    }
    buffer += decoder.decode(step.value, { stream: true });
    let cut = buffer.indexOf("\n\n");
    while (cut >= 0) {
      const frame = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      for (const line of frame.split("\n")) {
        if (line.startsWith("data:")) {
          onEvent(JSON.parse(line.slice(5).trim()));
        }
      }
      cut = buffer.indexOf("\n\n");
    }
  }
}

async function blockingMessage(message, attachments) {
  const payload = await fetchJson("/api/talk/" + talkState.current + "/message", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: message, attachments: attachments }),
  });
  talkState.messages.push(payload.asked);
  return payload;
}

async function streamMessage(message, attachments) {
  const response = await fetch("/api/talk/" + talkState.current + "/message/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: message, attachments: attachments }),
  });
  if (!response.ok) {
    const refusal = await response.json();
    const failure = new Error(refusal.error || response.statusText);
    failure.code = refusal.code || "";
    throw failure;
  }
  let live = null;
  let finished = null;
  await readEvents(response, (event) => {
    if (event.type === "asked") {
      document.getElementById("talk-input").value = "";
      talkState.messages.push(event.message);
      renderThread();
      live = liveReply();
    } else if (event.type === "delta" && live) {
      live.textContent += event.text;
      live.scrollIntoView({ block: "nearest" });
    } else if (event.type === "done") {
      finished = event;
    }
  });
  return finished;
}

function applyReply(payload) {
  if (payload.answered) {
    talkState.messages.push(payload.answered);
  }
  renderEngine(payload.reply.state !== "unavailable");
  showMessage(
    "talk-message",
    payload.reply.dropped ? t("talk.dropped", { count: payload.reply.dropped }) : ""
  );
  renderThread();
}

async function stopReply() {
  showStop(false);
  showBusy("talk-message", t("talk.stopping"));
  try {
    await fetchJson("/api/talk/" + talkState.current + "/stop", { method: "POST" });
  } catch (error) {
    showMessage("talk-message", errorText("talk.error", error));
  }
}

async function sendMessage(event) {
  event.preventDefault();
  const input = document.getElementById("talk-input");
  const message = input.value.trim();
  if (talkState.busy || (!message && !talkState.pending.length)) {
    return;
  }
  const attachments = talkState.pending.map((picture) => picture.id);
  const streaming = canStream();
  talkState.busy = true;
  showBusy("talk-message", t("talk.thinking"));
  showStop(streaming);
  let payload;
  try {
    payload = streaming
      ? await streamMessage(message, attachments)
      : await blockingMessage(message, attachments);
  } catch (error) {
    talkState.busy = false;
    showStop(false);
    const failed = talkState.current;
    await openThread(failed);
    showMessage("talk-message", errorText("talk.error", error));
    return;
  }
  input.value = "";
  talkState.pending = [];
  talkState.busy = false;
  showStop(false);
  if (payload) {
    applyReply(payload);
  }
  renderTray();
  await loadState();
}

async function saveSettings(event) {
  event.preventDefault();
  const body = {
    system_prompt: document.getElementById("talk-system").value,
    max_history_tokens: Number(document.getElementById("talk-history").value),
    sampling: {
      temperature: Number(document.getElementById("talk-temperature").value),
      top_p: 0.95,
      n_predict: Number(document.getElementById("talk-predict").value),
    },
  };
  let payload;
  try {
    payload = await fetchJson("/api/talk/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (error) {
    showMessage("talk-settings-message", errorText("talk.error", error));
    return;
  }
  renderSettings(payload);
  showMessage("talk-settings-message", t("talk.settings.saved"));
}

applyI18n();
document.getElementById("talk-new").addEventListener("click", newThread);
document.getElementById("talk-clear").addEventListener("click", clearAll);
document.getElementById("talk-rename").addEventListener("click", renameThread);
document.getElementById("talk-delete").addEventListener("click", deleteThread);
document.getElementById("talk-form").addEventListener("submit", sendMessage);
document.getElementById("talk-stop").addEventListener("click", stopReply);
document.getElementById("talk-settings-form").addEventListener("submit", saveSettings);
document.getElementById("talk-attach").addEventListener("click", () => {
  document.getElementById("talk-file").click();
});
document.getElementById("talk-file").addEventListener("change", async (event) => {
  const chosen = Array.from(event.target.files);
  event.target.value = "";
  await attachPictures(chosen);
});
document.getElementById("talk-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    document.getElementById("talk-form").requestSubmit();
  }
});
renderThread();
loadState();
