/* WorldLoom frontend — no dependencies. */
"use strict";

const CATEGORIES = [
  "overview", "geography", "history", "peoples", "cultures", "politics",
  "magic", "science", "religion", "economy", "language", "creatures",
  "locations", "characters", "hooks",
];

const SUGGESTIONS = [
  "Help me find the big idea for this world",
  "Sketch the geography and climate",
  "Draft a deep-history timeline",
  "Design a magic or power system with real costs",
  "Invent the peoples and species who live here",
  "Who holds power, and who wants it?",
  "What do ordinary people eat, fear, and celebrate?",
  "Give me three story hooks from the current canon",
];

const state = {
  config: null,
  worlds: [],
  world: null,        // full world object currently open
  filter: "all",
  streaming: false,
};

const $ = (sel) => document.querySelector(sel);

/* ---------------------------------------------------------------- api */

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

/* ------------------------------------------------------- md rendering */

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function inlineMd(s) {
  return s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>");
}

/** Tiny, safe markdown renderer: escape first, then add structure. */
function renderMarkdown(text) {
  const lines = escapeHtml(text).split("\n");
  const out = [];
  let list = null; // "ul" | "ol" | null
  let inCode = false;
  const closeList = () => { if (list) { out.push(`</${list}>`); list = null; } };
  for (const line of lines) {
    if (line.trim().startsWith("```")) {
      closeList();
      out.push(inCode ? "</code></pre>" : "<pre><code>");
      inCode = !inCode;
      continue;
    }
    if (inCode) { out.push(line + "\n"); continue; }
    const h = line.match(/^(#{1,4})\s+(.*)/);
    if (h) { closeList(); out.push(`<h3>${inlineMd(h[2])}</h3>`); continue; }
    const ul = line.match(/^\s*[-*]\s+(.*)/);
    if (ul) {
      if (list !== "ul") { closeList(); out.push("<ul>"); list = "ul"; }
      out.push(`<li>${inlineMd(ul[1])}</li>`);
      continue;
    }
    const ol = line.match(/^\s*\d+[.)]\s+(.*)/);
    if (ol) {
      if (list !== "ol") { closeList(); out.push("<ol>"); list = "ol"; }
      out.push(`<li>${inlineMd(ol[1])}</li>`);
      continue;
    }
    closeList();
    if (line.trim() === "") continue;
    out.push(`<p>${inlineMd(line)}</p>`);
  }
  closeList();
  if (inCode) out.push("</code></pre>");
  return out.join("");
}

/** Split assistant text into displayable text + proposed canon entries. */
function extractCanon(text) {
  const canon = [];
  const stripped = text.replace(/```canon\s*\n([\s\S]*?)```/g, (_, body) => {
    try {
      const obj = JSON.parse(body.trim());
      if (obj && obj.title && obj.content && CATEGORIES.includes(obj.category)) {
        canon.push(obj);
        return "";
      }
    } catch (e) { /* leave malformed blocks visible */ }
    return "```\n" + body + "```";
  });
  return { text: stripped.trim(), canon };
}

/* ------------------------------------------------------------ worlds */

async function refreshWorlds() {
  state.worlds = await api("GET", "/api/worlds");
  renderWorldList();
}

function renderWorldList() {
  const nav = $("#world-list");
  nav.innerHTML = "";
  for (const w of state.worlds) {
    const div = document.createElement("div");
    div.className = "world-item" + (state.world && state.world.id === w.id ? " active" : "");
    div.innerHTML = `<div class="w-name"></div><div class="w-meta">${w.entries} canon entr${w.entries === 1 ? "y" : "ies"}</div>`;
    div.querySelector(".w-name").textContent = w.name;
    div.onclick = () => openWorld(w.id);
    nav.appendChild(div);
  }
}

async function openWorld(id) {
  state.world = await api("GET", "/api/worlds/" + id);
  $("#welcome").classList.add("hidden");
  for (const sel of ["#chat-header", "#messages", "#composer", "#codex"]) {
    $(sel).classList.remove("hidden");
  }
  renderWorldHeader();
  renderWorldList();
  renderChat();
  renderCodex();
  renderSuggestions();
  $("#input").focus();
}

function renderWorldHeader() {
  const w = state.world;
  $("#world-name").textContent = w.name;
  $("#world-meta").textContent = w.premise
    ? w.premise.slice(0, 90) + (w.premise.length > 90 ? "…" : "")
    : "no premise set";
}

function renderSuggestions() {
  const box = $("#suggestions");
  box.innerHTML = "";
  const chat = state.world ? state.world.chat : [];
  if (!state.world || chat.length > 0) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");
  for (const s of SUGGESTIONS) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.textContent = s;
    chip.onclick = () => { $("#input").value = s; sendMessage(); };
    box.appendChild(chip);
  }
}

/* -------------------------------------------------------------- chat */

function messageEl(role, label) {
  const div = document.createElement("div");
  div.className = "msg msg-" + role;
  div.innerHTML = `<div class="msg-label">${label}</div><div class="msg-body"></div>`;
  $("#messages").appendChild(div);
  return div.querySelector(".msg-body");
}

function renderAssistantBody(el, rawText, { live } = {}) {
  const { text, canon } = live ? { text: rawText, canon: [] } : extractCanon(rawText);
  el.innerHTML = renderMarkdown(text) + (live ? '<span class="cursor-blink"></span>' : "");
  for (const c of canon) el.appendChild(canonCard(c));
}

function canonCard(c) {
  const card = document.createElement("div");
  card.className = "canon-card";
  card.innerHTML = `
    <div class="cc-head"><span class="cc-cat"></span><span class="cc-title"></span></div>
    <div class="cc-content"></div>
    <div class="cc-actions"><button class="btn btn-sm btn-primary">Add to Codex</button></div>`;
  card.querySelector(".cc-cat").textContent = c.category;
  card.querySelector(".cc-title").textContent = c.title;
  card.querySelector(".cc-content").textContent = c.content;
  const btn = card.querySelector("button");
  const already = state.world.entries.some(
    (e) => e.title.toLowerCase() === c.title.toLowerCase() && e.category === c.category);
  if (already) {
    card.querySelector(".cc-actions").innerHTML = '<span class="added">&#10003; In codex</span>';
  } else {
    btn.onclick = async () => {
      btn.disabled = true;
      try {
        state.world = await api("POST", `/api/worlds/${state.world.id}/entries`, c);
        card.querySelector(".cc-actions").innerHTML = '<span class="added">&#10003; Added to codex</span>';
        renderCodex();
        refreshWorlds();
      } catch (err) {
        btn.disabled = false;
        alert("Could not add entry: " + err.message);
      }
    };
  }
  return card;
}

function renderChat() {
  $("#messages").innerHTML = "";
  for (const m of state.world.chat) {
    if (m.role === "user") {
      messageEl("user", "You").textContent = m.content;
    } else {
      renderAssistantBody(messageEl("assistant", "Loom"), m.content, { live: false });
    }
  }
  scrollChat();
}

function scrollChat() {
  const box = $("#messages");
  box.scrollTop = box.scrollHeight;
}

async function sendMessage() {
  const input = $("#input");
  const message = input.value.trim();
  if (!message || state.streaming || !state.world) return;
  input.value = "";
  state.streaming = true;
  $("#btn-send").disabled = true;
  $("#suggestions").classList.add("hidden");

  messageEl("user", "You").textContent = message;
  const body = messageEl("assistant", "Loom");
  body.innerHTML = '<span class="cursor-blink"></span>';
  scrollChat();

  let full = "";
  try {
    const res = await fetch(`/api/worlds/${state.world.id}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    if (!res.ok || !res.body) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.error || res.statusText);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let lastPaint = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop();
      for (const line of lines) {
        if (!line.trim()) continue;
        let obj;
        try { obj = JSON.parse(line); } catch (e) { continue; }
        if (obj.text) full += obj.text;
        if (obj.error) throw new Error(obj.error);
      }
      const now = performance.now();
      if (now - lastPaint > 60) {
        renderAssistantBody(body, full, { live: true });
        scrollChat();
        lastPaint = now;
      }
    }
    renderAssistantBody(body, full, { live: false });
  } catch (err) {
    if (full) renderAssistantBody(body, full, { live: false });
    const errEl = document.createElement("div");
    errEl.className = "msg-error";
    errEl.textContent = err.message;
    body.appendChild(errEl);
  } finally {
    state.streaming = false;
    $("#btn-send").disabled = false;
    scrollChat();
    // refresh world so chat history / entry counts stay in sync with server
    try {
      state.world = await api("GET", "/api/worlds/" + state.world.id);
      refreshWorlds();
    } catch (e) { /* server may be restarting; keep local view */ }
  }
}

/* -------------------------------------------------------------- codex */

function renderCodexFilter() {
  const box = $("#codex-filter");
  box.innerHTML = "";
  const cats = ["all", ...CATEGORIES.filter(
    (c) => state.world.entries.some((e) => e.category === c))];
  if (!cats.includes(state.filter)) state.filter = "all";
  for (const c of cats) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip" + (state.filter === c ? " active" : "");
    chip.textContent = c;
    chip.onclick = () => { state.filter = c; renderCodex(); };
    box.appendChild(chip);
  }
}

function renderCodex() {
  renderCodexFilter();
  const box = $("#codex-entries");
  box.innerHTML = "";
  const entries = state.world.entries.filter(
    (e) => state.filter === "all" || e.category === state.filter);
  if (entries.length === 0) {
    box.innerHTML = '<div class="codex-empty">No canon yet. Accept proposals from the chat, or add entries by hand.</div>';
    return;
  }
  const ordered = [];
  for (const cat of CATEGORIES) {
    for (const e of entries) if (e.category === cat) ordered.push(e);
  }
  for (const e of ordered) box.appendChild(entryEl(e));
}

function entryEl(e) {
  const det = document.createElement("details");
  det.className = "entry";
  det.innerHTML = `
    <summary><span class="e-cat"></span><span class="e-title"></span></summary>
    <div class="e-body"></div>`;
  det.querySelector(".e-cat").textContent = e.category;
  det.querySelector(".e-title").textContent = e.title;
  const bodyEl = det.querySelector(".e-body");

  const renderView = () => {
    bodyEl.innerHTML = "";
    const p = document.createElement("div");
    p.textContent = e.content;
    p.style.whiteSpace = "pre-wrap";
    bodyEl.appendChild(p);
    const actions = document.createElement("div");
    actions.className = "e-actions";
    const editBtn = document.createElement("button");
    editBtn.className = "btn btn-ghost btn-sm";
    editBtn.textContent = "Edit";
    editBtn.onclick = renderEdit;
    const delBtn = document.createElement("button");
    delBtn.className = "btn btn-danger-ghost btn-sm";
    delBtn.textContent = "Delete";
    delBtn.onclick = async () => {
      if (!confirm(`Delete "${e.title}" from the codex?`)) return;
      state.world = await api("DELETE", `/api/worlds/${state.world.id}/entries/${e.id}`);
      renderCodex();
      refreshWorlds();
    };
    actions.append(editBtn, delBtn);
    bodyEl.appendChild(actions);
  };

  const renderEdit = () => {
    bodyEl.innerHTML = "";
    const catSel = document.createElement("select");
    for (const c of CATEGORIES) {
      const opt = document.createElement("option");
      opt.value = c; opt.textContent = c;
      if (c === e.category) opt.selected = true;
      catSel.appendChild(opt);
    }
    const titleIn = document.createElement("input");
    titleIn.value = e.title;
    const contentTa = document.createElement("textarea");
    contentTa.rows = 5;
    contentTa.value = e.content;
    const actions = document.createElement("div");
    actions.className = "e-actions";
    const saveBtn = document.createElement("button");
    saveBtn.className = "btn btn-primary btn-sm";
    saveBtn.textContent = "Save";
    saveBtn.onclick = async () => {
      state.world = await api("PUT", `/api/worlds/${state.world.id}/entries/${e.id}`, {
        category: catSel.value, title: titleIn.value, content: contentTa.value,
      });
      renderCodex();
    };
    const cancelBtn = document.createElement("button");
    cancelBtn.className = "btn btn-ghost btn-sm";
    cancelBtn.textContent = "Cancel";
    cancelBtn.onclick = renderView;
    actions.append(saveBtn, cancelBtn);
    bodyEl.append(catSel, titleIn, contentTa, actions);
  };

  renderView();
  return det;
}

/* ------------------------------------------------------------- modals */

function openModal(id) {
  $("#modal-backdrop").classList.remove("hidden");
  for (const m of document.querySelectorAll(".modal")) m.classList.add("hidden");
  $(id).classList.remove("hidden");
}
function closeModal() {
  $("#modal-backdrop").classList.add("hidden");
}

/* settings */
function fillSettings() {
  const c = state.config;
  $("#cfg-provider").value = c.provider;
  $("#cfg-anthropic-key").value = c.anthropic.api_key;
  $("#cfg-anthropic-model").value = c.anthropic.model;
  $("#cfg-ollama-url").value = c.ollama.base_url;
  $("#cfg-ollama-model").value = c.ollama.model;
  $("#cfg-openai-url").value = c.openai.base_url;
  $("#cfg-openai-key").value = c.openai.api_key;
  $("#cfg-openai-model").value = c.openai.model;
  $("#cfg-max-tokens").value = c.max_tokens;
  toggleProviderBlocks();
  $("#cfg-test-result").classList.add("hidden");
}

function readSettings() {
  return {
    provider: $("#cfg-provider").value,
    max_tokens: parseInt($("#cfg-max-tokens").value, 10) || 16000,
    anthropic: {
      api_key: $("#cfg-anthropic-key").value.trim(),
      model: $("#cfg-anthropic-model").value.trim() || "claude-opus-4-8",
    },
    ollama: {
      base_url: $("#cfg-ollama-url").value.trim() || "http://127.0.0.1:11434",
      model: $("#cfg-ollama-model").value.trim() || "llama3.1",
    },
    openai: {
      base_url: $("#cfg-openai-url").value.trim() || "https://api.openai.com/v1",
      api_key: $("#cfg-openai-key").value.trim(),
      model: $("#cfg-openai-model").value.trim(),
    },
  };
}

function toggleProviderBlocks() {
  const p = $("#cfg-provider").value;
  $("#cfg-anthropic").style.display = p === "anthropic" ? "" : "none";
  $("#cfg-ollama").style.display = p === "ollama" ? "" : "none";
  $("#cfg-openai").style.display = p === "openai" ? "" : "none";
}

function renderProviderPill() {
  const c = state.config;
  const names = {
    anthropic: "Anthropic · " + c.anthropic.model,
    ollama: "Ollama · " + c.ollama.model,
    openai: "OpenAI-compat · " + (c.openai.model || "?"),
    demo: "Demo provider",
  };
  $("#provider-pill").textContent = "● " + (names[c.provider] || c.provider);
}

/* world create/edit */
let editingWorld = false;

function openWorldModal(editing) {
  editingWorld = editing;
  $("#modal-world-title").textContent = editing ? "Edit World" : "New World";
  $("#btn-save-world").textContent = editing ? "Save" : "Create";
  $("#world-name-input").value = editing ? state.world.name : "";
  $("#world-premise-input").value = editing ? (state.world.premise || "") : "";
  openModal("#modal-world");
  $("#world-name-input").focus();
}

async function saveWorldModal() {
  const name = $("#world-name-input").value.trim();
  const premise = $("#world-premise-input").value.trim();
  if (!name) { $("#world-name-input").focus(); return; }
  if (editingWorld) {
    state.world = await api("PUT", "/api/worlds/" + state.world.id, { name, premise });
    renderWorldHeader();
  } else {
    const world = await api("POST", "/api/worlds", { name, premise });
    await refreshWorlds();
    await openWorld(world.id);
  }
  closeModal();
  await refreshWorlds();
}

/* manual entry */
function openEntryModal() {
  const sel = $("#entry-category");
  sel.innerHTML = "";
  for (const c of CATEGORIES) {
    const opt = document.createElement("option");
    opt.value = c; opt.textContent = c;
    sel.appendChild(opt);
  }
  if (state.filter !== "all") sel.value = state.filter;
  $("#entry-title").value = "";
  $("#entry-content").value = "";
  openModal("#modal-entry");
  $("#entry-title").focus();
}

/* ------------------------------------------------------------- wiring */

function wire() {
  $("#btn-new-world").onclick = () => openWorldModal(false);
  $("#btn-welcome-new").onclick = () => openWorldModal(false);
  $("#btn-edit-world").onclick = () => openWorldModal(true);
  $("#btn-save-world").onclick = saveWorldModal;

  $("#btn-settings").onclick = () => { fillSettings(); openModal("#modal-settings"); };
  $("#cfg-provider").onchange = toggleProviderBlocks;
  $("#btn-save-settings").onclick = async () => {
    state.config = await api("PUT", "/api/config", readSettings());
    renderProviderPill();
    closeModal();
  };
  $("#btn-test").onclick = async () => {
    const el = $("#cfg-test-result");
    el.classList.remove("hidden", "ok", "bad");
    el.textContent = "Testing…";
    try {
      const res = await api("POST", "/api/config/test", readSettings());
      el.classList.add(res.ok ? "ok" : "bad");
      el.textContent = res.detail;
    } catch (err) {
      el.classList.add("bad");
      el.textContent = err.message;
    }
  };

  $("#btn-new-entry").onclick = openEntryModal;
  $("#btn-save-entry").onclick = async () => {
    try {
      state.world = await api("POST", `/api/worlds/${state.world.id}/entries`, {
        category: $("#entry-category").value,
        title: $("#entry-title").value,
        content: $("#entry-content").value,
      });
      renderCodex();
      refreshWorlds();
      closeModal();
    } catch (err) {
      alert(err.message);
    }
  };

  $("#btn-export").onclick = () => {
    window.location.href = `/api/worlds/${state.world.id}/export`;
  };
  $("#btn-clear-chat").onclick = async () => {
    if (!confirm("Clear the conversation? Canon entries are kept.")) return;
    state.world = await api("POST", `/api/worlds/${state.world.id}/chat/clear`);
    renderChat();
    renderSuggestions();
  };
  $("#btn-delete-world").onclick = async () => {
    if (!confirm(`Delete the world "${state.world.name}" and all its canon? This cannot be undone.`)) return;
    await api("DELETE", "/api/worlds/" + state.world.id);
    state.world = null;
    await refreshWorlds();
    for (const sel of ["#chat-header", "#messages", "#composer", "#codex", "#suggestions"]) {
      $(sel).classList.add("hidden");
    }
    $("#welcome").classList.remove("hidden");
  };

  $("#composer").onsubmit = (ev) => { ev.preventDefault(); sendMessage(); };
  $("#input").onkeydown = (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey) {
      ev.preventDefault();
      sendMessage();
    }
  };

  $("#modal-backdrop").onclick = (ev) => {
    if (ev.target === $("#modal-backdrop")) closeModal();
  };
  for (const btn of document.querySelectorAll(".modal-close")) btn.onclick = closeModal;
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") closeModal();
  });
}

/* -------------------------------------------------------------- init */

async function init() {
  wire();
  state.config = await api("GET", "/api/config");
  renderProviderPill();
  await refreshWorlds();
  if (state.worlds.length > 0) await openWorld(state.worlds[0].id);
}

init().catch((err) => {
  document.body.innerHTML = `<div style="padding:40px;font-family:sans-serif">
    <h2>WorldLoom failed to start</h2><p>${escapeHtml(err.message)}</p></div>`;
});
