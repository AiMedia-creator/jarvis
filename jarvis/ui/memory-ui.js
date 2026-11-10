/* Native HUD memory panel. Served as an explicit static asset by the native UI server. */
"use strict";

const pane = document.querySelector("#memoryPane");
const count = document.querySelector("#memoryCount");
const commandCenter = document.querySelector("#commandCenterLink");
const MAX_ITEMS = 50;
const CATEGORIES = ["preference", "project", "note"];
const SECRET_RE = /(?:password|passwort|secret|api[ _-]?key|token|bearer|private[ _-]?key|sk-[a-z0-9]|-----begin)/i;
let snapshot = { facts: [], episodes: [], events: [], stats: { facts: 0, episodes: 0 } };
let query = "", debounce = null, controller = null, poll = null, disposed = false, busy = false, error = "";

function text(value) { return typeof value === "string" ? value : value == null ? "" : String(value); }
function bounded(value) { return Array.isArray(value) ? value.slice(0, MAX_ITEMS) : []; }
function normalize(value) {
  const raw = value && typeof value === "object" ? value : {};
  const facts = bounded(raw.facts || raw.fact).map((x, i) => ({ id: text(x.id || `fact-${i}`), content: text(x.content ?? x.text), category: text(x.category || "note"), updated_at: text(x.updated_at ?? x.updatedAt ?? x.created_at) }));
  const episodes = bounded(raw.episodes || raw.episode).map((x, i) => ({ id: text(x.id || `episode-${i}`), conversation_id: text(x.conversation_id ?? x.conversationId), user_text: text(x.user_text ?? x.userText), assistant_text: text(x.assistant_text ?? x.assistantText), status: text(x.status), created_at: text(x.created_at ?? x.createdAt) }));
  const stats = raw.stats && typeof raw.stats === "object" ? raw.stats : {};
  const n = (v, fallback) => Number.isFinite(Number(v)) && Number(v) >= 0 ? Math.min(MAX_ITEMS, Math.floor(Number(v))) : fallback;
  return { facts, episodes, events: bounded(raw.events || raw.event), stats: { facts: n(stats.facts, facts.length), episodes: n(stats.episodes, episodes.length) } };
}
function el(tag, cls, value) { const node = document.createElement(tag); if (cls) node.className = cls; if (value != null) node.textContent = value; return node; }
function emit() { window.dispatchEvent(new CustomEvent("memorychange")); }
function roomForCommandCenter() {
  let room = "";
  try { room = localStorage.getItem("jarvis-room") || ""; } catch {}
  if (!/^[a-zA-Z0-9_-]{8,64}$/.test(room)) room = "native-haupt";
  try { localStorage.setItem("jarvis-room", room); } catch {}
  return room;
}
if (commandCenter) commandCenter.href = `http://localhost:8080/?room=${encodeURIComponent(roomForCommandCenter())}`;

async function load() {
  if (disposed) return;
  controller?.abort(); controller = new AbortController();
  try {
    const response = await fetch("/api/memory", { headers: { "X-Jarvis": "1" }, credentials: "same-origin", signal: controller.signal });
    if (!response.ok) throw new Error(`Memory service unavailable (${response.status})`);
    snapshot = normalize(await response.json()); error = ""; render();
  } catch (reason) {
    if (reason?.name === "AbortError" || disposed) return;
    error = reason instanceof Error ? reason.message : "Memory service unavailable"; render();
  }
}
function startPoll() {
  if (poll !== null || document.hidden) return;
  poll = window.setInterval(() => void load(), 5000);
}
function stopPoll() { if (poll !== null) { window.clearInterval(poll); poll = null; } }
function visibleFacts() {
  const needle = query.trim().toLocaleLowerCase();
  return needle ? snapshot.facts.filter(x => `${x.content} ${x.category}`.toLocaleLowerCase().includes(needle)) : snapshot.facts;
}
async function create(content, category) {
  const value = content.trim();
  if (!value) { error = "Enter a memory note first."; render(); return; }
  if (SECRET_RE.test(value)) { error = "Not saved: memory must not contain passwords, tokens, or API keys."; render(); return; }
  busy = true; error = ""; render();
  try {
    const response = await fetch("/api/memory", { method: "POST", headers: { "Content-Type": "application/json", "X-Jarvis": "1" }, credentials: "same-origin", body: JSON.stringify({ content: value, category }) });
    if (!response.ok) throw new Error(`Memory note was not saved (${response.status})`);
    emit(); await load();
  } catch (reason) { error = reason instanceof Error ? reason.message : "Memory note was not saved"; }
  finally { busy = false; render(); }
}
async function remove(id) {
  if (!window.confirm("Delete this memory note?")) return;
  busy = true; error = ""; render();
  try {
    const response = await fetch(`/api/memory/${encodeURIComponent(id)}`, { method: "DELETE", headers: { "X-Jarvis": "1" }, credentials: "same-origin" });
    if (!response.ok) throw new Error(`Memory note was not deleted (${response.status})`);
    emit(); await load();
  } catch (reason) { error = reason instanceof Error ? reason.message : "Memory note was not deleted"; }
  finally { busy = false; render(); }
}
function render() {
  if (!pane || disposed) return;
  if (count) count.textContent = snapshot.stats.facts ? String(snapshot.stats.facts) : "";
  pane.textContent = "";
  const header = el("div", "side-head"); header.append(el("h2", null, "Memory management"));
  const refresh = el("button", "iconbtn", "↻"); refresh.type = "button"; refresh.title = "Aktualisieren"; refresh.onclick = () => void load(); header.append(refresh); pane.append(header);
  const stats = el("div", "memory-native-stats", `${snapshot.stats.facts} facts · ${snapshot.stats.episodes} episodes · ${snapshot.events.length} events`); pane.append(stats);
  const form = el("form", "memory-native-form");
  const input = document.createElement("input"); input.type = "text"; input.maxLength = 2000; input.placeholder = "Nicht-sensitive Notiz …"; input.setAttribute("aria-label", "Memory note");
  const select = document.createElement("select"); select.setAttribute("aria-label", "Memory category"); CATEGORIES.forEach(category => { const option = el("option", null, category); option.value = category; select.append(option); });
  const add = el("button", "btn primary", "Merken"); add.type = "submit"; add.disabled = busy; form.append(input, select, add); form.onsubmit = event => { event.preventDefault(); void create(input.value, select.value); }; pane.append(form);
  const search = document.createElement("input"); search.type = "search"; search.className = "memory-native-search"; search.value = query; search.placeholder = "Facts durchsuchen …"; search.setAttribute("aria-label", "Search facts"); search.oninput = () => { window.clearTimeout(debounce); debounce = window.setTimeout(() => { query = search.value; render(); }, 250); }; pane.append(search);
  if (error) pane.append(el("div", "memory-native-error", error));
  const list = el("div", "memory-native-list");
  const facts = visibleFacts();
  if (!facts.length) list.append(el("div", "empty", "Keine passenden Facts."));
  facts.forEach(fact => { const row = el("article", "memory-native-fact"); const body = el("div"); body.append(el("span", "memory-native-category", fact.category), el("p", null, fact.content), el("small", null, fact.updated_at || "—")); const del = el("button", "btn", "Löschen"); del.type = "button"; del.disabled = busy; del.onclick = () => void remove(fact.id); row.append(body, del); list.append(row); });
  pane.append(list, el("div", "memory-native-note", "Episodes sind schreibgeschützt. Inhalte werden als reiner Text angezeigt; keine Secrets oder verstecktes Reasoning."));
}
function dispose() { disposed = true; stopPoll(); controller?.abort(); if (debounce !== null) window.clearTimeout(debounce); }
document.addEventListener("visibilitychange", () => { if (document.hidden) stopPoll(); else { void load(); startPoll(); } });
window.addEventListener("memorychange", () => void load());
window.addEventListener("jarvis:memory", () => void load());
window.addEventListener("pagehide", dispose, { once: true });
render(); void load(); startPoll();
window.JarvisMemory = { reload: load, normalize, dispose };
