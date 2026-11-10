/* Jarvis-HUD: verbindet Oberfläche, Voice-Core, lokalen Server und die Sprachkerne (Browser / ElevenLabs). */
"use strict";
const $ = s => document.querySelector(s);
const V = window.JarvisVoice;
const S = V.VoiceState;
const chat = $("#chat"), input = $("#input");
const MOCK = new URLSearchParams(location.search).has("mock");

const TOOL_LABELS = {
  memory:"Gedächtnis", merken:"merkt sich etwas", live_kamera:"Live-Kamera", kamera_schliessen:"Kamera zu", web_search:"Websuche", web_suche:"Websuche", web_fetch:"liest Webseite",
  lies_skill:"liest Anleitung", mails_suchen:"durchsucht Gmail", mail_lesen:"liest Mail", mail_senden:"Mail",
  entwurf_erstellen:"Entwurf", mail_archivieren:"archiviert", kalender_termine:"Kalender", termin_erstellen:"Termin",
  drive_suchen:"Drive-Suche", drive_lesen:"liest Dokument", dokument_erstellen:"neues Doc", aufgaben_liste:"Aufgaben",
  aufgabe_hinzufuegen:"neue Aufgabe", aufgabe_erledigen:"Aufgabe erledigt", kontakte_suchen:"Kontakte",
  nachrichten:"Nachrichten", telegram_neuigkeiten:"liest Telegram", telegram_suchen:"durchsucht Telegram",
  seite_oeffnen:"öffnet Seite", programm_oeffnen:"öffnet Programm", zwischenablage_lesen:"liest Zwischenablage", in_zwischenablage:"kopiert Text", drive_uebersicht:"kartiert Drive", drive_ordnen:"baut Drive um", webseite_lesen:"liest Webseite", n8n_workflows:"n8n-Workflows", n8n_automatik:"startet Workflow", n8n_starten:"Workflow (Freigabe)", n8n_status:"prüft Workflow", ablaeufe_status:"Abläufe", auftraege_liste:"Aufträge", auftrag_anlegen:"neuer Auftrag", auftrag_aktualisieren:"Auftrag geändert", hubspot_kontakt_suchen:"HubSpot-Kontakt",
  hubspot_deals_suchen:"HubSpot-Deals", hubspot_deals_zu_kontakt:"Deals zum Kontakt", hubspot_notizen_lesen:"HubSpot-Notizen",
  hubspot_notiz_erstellen:"HubSpot-Notiz", hubspot_aufgabe_erstellen:"HubSpot-Aufgabe",
  hubspot_mail_protokollieren:"protokolliert in HubSpot", hubspot_mail_senden:"Mail + HubSpot"
};
const HINTS = {
  IDLE: "Klick auf den Kern oder Leertaste: Gespräch starten",
  LISTENING: "Sprich einfach · Esc stoppt",
  TRANSCRIBING: "Ich höre mit · kurze Pause = absenden",
  INTERRUPTED: "Gestoppt – ich höre dir zu",
  THINKING: "Esc bricht ab",
  SPEAKING: "Einfach reinreden · Esc stoppt",
  WAITING_FOR_APPROVAL: "Freigeben, Ablehnen – oder sag „Ja“",
  ERROR: "Wiederholen oder Schließen",
};

// ---------- Hilfen ----------
const store = {get(k,d){try{const v=localStorage.getItem(k);return v===null?d:JSON.parse(v)}catch{return d}},
               set(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch{}}};
function el(tag, cls, text){const e=document.createElement(tag); if(cls) e.className=cls; if(text!=null) e.textContent=text; return e;}
function scrollDown(){chat.scrollTop = chat.scrollHeight;}
function api(path, body, signal){
  return fetch(path, body===undefined ? {} : {method:"POST", headers:{"Content-Type":"application/json","X-Jarvis":"1"}, body:JSON.stringify(body), signal});
}
// Schnellere Reaktion (08.11.2026): Standardpause 0,6 s. Wer noch den alten Standard 1,0 hatte, bekommt einmalig 0,6.
if(!store.get("jarvis.pause.v2", false)){ if(store.get("jarvis.pause", 1.0) === 1.0) store.set("jarvis.pause", 0.6); store.set("jarvis.pause.v2", true); }
const prefs = {pause: store.get("jarvis.pause", 0.6), wake: store.get("jarvis.wake", false),
  reduced: store.get("jarvis.reduced", window.matchMedia("(prefers-reduced-motion: reduce)").matches),
  grid: store.get("jarvis.grid", true), talkMode: store.get("jarvis.talkMode", "frei"),
  barge: store.get("jarvis.barge", "smart")};

// ---------- Voice-Core + Zustandsmaschine ----------
const micSrc = new V.MicSource(), outSrc = new V.MediaOutputSource();
const ctrl = new V.VoiceCoreController({canvas: $("#core"), reducedMotion: prefs.reduced});
ctrl.setInput(MOCK ? new V.MockSource() : micSrc);
ctrl.setOutput(MOCK ? new V.MockSource() : outSrc);

let busy = false, playing = false, convo = false, el11 = null, elMode = "listening", toolRunning = false;
let errorMsg = null, pendingApprovals = [], mockState = null, lastAsk = "", lastJob = null;
let userSpeaking = false, interruptedUntil = 0, pttActive = false;
let jobView = null;   // aktueller Job mit Schritten (vom Server) + Messwerte aus Sicht der Oberfläche

function computeState(){
  // Zustand nur aus echten Fakten ableiten – nie "denkt", wenn nichts mehr läuft.
  if(mockState) return mockState;
  if(Date.now() < interruptedUntil) return S.INTERRUPTED;
  if(errorMsg) return S.ERROR;
  if(playing || (el11 && elMode === "speaking")) return S.SPEAKING;
  if(userSpeaking && (convo || pttActive)) return S.TRANSCRIBING;
  if(busy || toolRunning) return S.THINKING;
  if(pttActive) return S.LISTENING;
  if(pendingApprovals.length) return S.WAITING_FOR_APPROVAL;
  if(convo) return S.LISTENING;
  return S.IDLE;
}
function refreshState(){ ctrl.setState(computeState()); paintJobs(); }
ctrl.onChange(paintState);
function paintState(state){
  const box = $("#coreState"); box.dataset.s = state;
  $("#coreLabel").textContent = ctrl.label();
  $("#coreHint").textContent = (state === S.IDLE && window.__hudReady && isPTT()) ? "Leertaste oder Kern gedrückt halten zum Sprechen" : HINTS[state];
  $("#core").title = `Status: ${ctrl.label()} – Klick oder Leertaste: Gespräch ${convo ? "beenden" : "starten"}, Esc: Stopp`;
}
paintState(S.IDLE);

// Mikrofon-Anzeige + Pegel (läuft mit der Bildwiederholrate, blockiert nichts)
const micHist = []; let testPeak = null;
function sampleMic(){
  if(!micSrc.analyser) return;
  const now = performance.now(); micHist.push({t: now, l: micSrc.level()});
  while(micHist.length && now - micHist[0].t > 10000) micHist.shift();
}
function micPeak(ms){ const now = performance.now(); let p = 0; for(const h of micHist) if(now - h.t <= ms && h.l > p) p = h.l; return p; }
function ambientLevel(){ const v = micHist.map(h => h.l).sort((a, b) => a - b); return v.length ? v[Math.floor(v.length * 0.2)] : 0; }
function loudEnough(idle){
  if(testPeak == null && !micSrc.analyser) return true;           // ohne Pegelmessung nicht blockieren
  const base = {aus: 0.3, smart: 0.2, immer: 0.08}[prefs.barge] || 0.2;
  const thr = Math.max(base * (idle ? 0.6 : 1), ambientLevel() * 2.5) * (playing ? 1.4 : 1);
  return (testPeak != null ? testPeak : micPeak(2500)) >= thr;
}

(function meterLoop(){
  sampleMic();
  const live = convo || mockState === S.LISTENING;
  $("#mic").classList.toggle("live", live);
  $("#micText").textContent = MOCK ? "Mock-Pegel" : el11 ? "ElevenLabs-Mikro" : live ? "Mikro an" : "Mikro aus";
  const raw = ctrl.state === S.LISTENING || ctrl.state === S.SPEAKING ? ctrl.smoothed : 0;
  $("#meter").style.width = Math.round(Math.min(1, raw * 1.3) * 100) + "%";
  requestAnimationFrame(meterLoop);
})();

// Darstellungs-Einstellungen
function applyLook(){ document.body.classList.toggle("reduced", prefs.reduced); document.body.classList.toggle("grid-bg", prefs.grid); ctrl.setReducedMotion(prefs.reduced); }
applyLook();
$("#reducedChk").checked = prefs.reduced; $("#gridChk").checked = prefs.grid;
$("#reducedChk").onchange = e => { prefs.reduced = e.target.checked; store.set("jarvis.reduced", prefs.reduced); applyLook(); };
$("#gridChk").onchange = e => { prefs.grid = e.target.checked; store.set("jarvis.grid", prefs.grid); applyLook(); };

// ---------- Untertitel, Grundlage, Jobs, Fehler ----------
function subtitle(text, user){ const s = $("#subtitle"); s.textContent = text || ""; s.classList.toggle("user", !!user); }
function basisReset(){ $("#basis").innerHTML = ""; }
function basisAdd(name){
  const label = TOOL_LABELS[name] || name;
  if([...$("#basis").children].some(c => c.textContent === label)) return;
  $("#basis").append(el("span","tool",label));
}
const STEP_ICON = {RUNNING:"◌", COMPLETED:"✓", FAILED:"✕", CANCELLED:"–", WAITING_FOR_APPROVAL:"…", PLANNED:"·"};
const JOB_TEXT = {RUNNING:"Läuft", COMPLETED:"Erledigt", FAILED:"Fehlgeschlagen", CANCELLED:"Abgebrochen", WAITING_FOR_APPROVAL:"Wartet auf Freigabe"};
function paintJobs(){
  const box = $("#jobs"); box.innerHTML = "";
  if(jobView){
    const st = (busy || toolRunning) ? "RUNNING" : (jobView.status || "COMPLETED");
    box.append(el("b",null,(JOB_TEXT[st] || st) + " · "), document.createTextNode(`„${jobView.titel}“`));
    if(jobView.schritte && jobView.schritte.length){
      const ul = el("ul","steps");
      jobView.schritte.forEach(x => {
        const li = el("li", x.status); li.append(el("span","ic", STEP_ICON[x.status] || "·"), el("span",null, TOOL_LABELS[x.werkzeug] || x.werkzeug));
        if(x.berechtigung === "freigabe") li.append(el("span","wait"," Freigabe"));
        if(x.ende && x.start) li.append(el("span","ms", ((x.ende - x.start)).toFixed(1).replace(".",",") + " s"));
        if(x.fehler) li.title = x.fehler;
        ul.append(li);
      });
      box.append(ul);
    }
    const lat = [];
    if(jobView.firstMs != null) lat.push("erste Worte " + (jobView.firstMs/1000).toFixed(1).replace(".",",") + " s");
    if(jobView.audioMs != null) lat.push("Stimme " + (jobView.audioMs/1000).toFixed(1).replace(".",",") + " s");
    if(jobView.totalMs != null) lat.push("gesamt " + (jobView.totalMs/1000).toFixed(1).replace(".",",") + " s");
    if(lat.length) box.append(el("div","lat", lat.join(" · ")));
  }
  else if(pendingApprovals.length){ box.append(el("b",null,`${pendingApprovals.length} wartet auf Freigabe`)); }
  else box.textContent = "Keine laufenden Aufgaben.";
}
function setError(msg){
  errorMsg = msg; $("#errorText").textContent = msg; $("#errorBanner").classList.add("show");
  lastJob = {status:"Fehlgeschlagen", text: lastAsk.slice(0,60)}; refreshState();
}
function clearError(){ errorMsg = null; $("#errorBanner").classList.remove("show"); refreshState(); }
$("#errorClose").onclick = clearError;
$("#errorRetry").onclick = () => { clearError(); if(lastAsk) ask(lastAsk); };
function friendlyError(raw){
  const t = String(raw || "");
  if(/401|authentication|x-api-key|lehnt den Schl/i.test(t)) return "Ein Schlüssel wird abgelehnt (Anthropic oder ElevenLabs). Bitte Schlüssel prüfen.";
  if(/429|rate|limit/i.test(t)) return "Zu viele Anfragen gerade. Kurz warten und nochmal versuchen.";
  if(/Failed to fetch|Verbindung|NetworkError|ECONN/i.test(t)) return "Keine Verbindung zum Jarvis-Server. Läuft „jarvis --ui“ noch?";
  if(/workspace/i.test(t)) return "Die Workspace-ID fehlt oder ist falsch (ANTHROPIC_WORKSPACE_ID).";
  return t.length > 160 ? t.slice(0,160) + " …" : t;
}

// ---------- Uhr & Begrüßung ----------
function tick(){
  const d = new Date();
  $("#clock").textContent = d.toLocaleDateString("de-DE",{weekday:"short",day:"2-digit",month:"2-digit"}) + "  " + d.toLocaleTimeString("de-DE",{hour:"2-digit",minute:"2-digit"});
}
tick(); setInterval(tick, 15000);

// ---------- Status & Einstellungen ----------
let status = {};
let core = store.get("jarvis.core", "auto");
async function loadStatus(){
  try{ status = await (await api("/api/status")).json(); }
  catch(e){ const c = $("#chips"); c.innerHTML = ""; const x = el("div","chip err"); x.append(el("i"), document.createTextNode("Server nicht erreichbar")); c.append(x); return; }
  const chips = $("#chips"); chips.innerHTML = "";
  const voiceName = (status.stimmen||[]).find(v => v.id===status.stimme)?.name?.split(" –")[0] || "Stimme";
  const coreName = useEleven() ? "Echtzeit" : "Browser-Sprache";
  [["Google", status.google], ["HubSpot", status.hubspot], [voiceName, true], [coreName, true],
   [{haiku:"Schnell",sonnet:"Ausgewogen",opus:"Klug"}[status.modell_key] || status.modell, true]].forEach(([n, on]) => {
    const c = el("div", "chip" + (on ? " on" : "")); c.append(el("i"), document.createTextNode(n)); chips.append(c);
  });
  if(MOCK){ const m = el("div","chip on"); m.append(el("i"), document.createTextNode("Mock-Modus")); chips.append(m); }
  const sel = $("#voiceSel"); sel.innerHTML = ""; const groups = {};
  (status.stimmen||[]).forEach(v => { if(!groups[v.gruppe]){ groups[v.gruppe]=el("optgroup"); groups[v.gruppe].label=v.gruppe; sel.append(groups[v.gruppe]); }
    const o = el("option", null, v.name); o.value = v.id; groups[v.gruppe].append(o); });
  sel.value = status.stimme;
  $("#speed").value = status.tempo; $("#speedVal").textContent = Number(status.tempo).toFixed(2)+"×";
  document.querySelectorAll("#modelSeg button").forEach(b => b.classList.toggle("sel", b.dataset.m===status.modell_key));
  const w = $("#elevenWarn"); w.hidden = !status.elevenlabs_problem; w.textContent = status.elevenlabs_problem || "";
  $("#ttsModel").value = status.eleven_tts_model || "eleven_flash_v2_5";
  $("#coreSel").value = core;
  $("#coreHintSet").textContent = status.sprachkern_verfuegbar
    ? "ElevenLabs: natürliches Reinreden, Profi-Spracherkennung und Stimme. Kostet ElevenLabs-Minuten."
    : status.sprachkern_problem
      ? "ElevenLabs lehnt deinen Schlüssel ab (401) – bis zu einem neuen Schlüssel läuft der Browser-Modus."
      : "Für ElevenLabs Echtzeit fehlt ELEVENLABS_API_KEY – bis dahin läuft der Browser-Modus.";
}
async function saveSettings(obj){ try{ const r = await api("/api/settings", obj); if(r.ok) status = await r.json(); }catch(e){} loadStatus(); }
$("#settingsBtn").onclick = () => $("#app").classList.add("open");
$("#closeSettings").onclick = $("#drawerBg").onclick = () => $("#app").classList.remove("open");
$("#voiceSel").onchange = e => { saveSettings({stimme: e.target.value}); preview(); };
$("#coreSel").onchange = e => { if(convo) setConvo(false); core = e.target.value; store.set("jarvis.core", core); loadStatus(); };
$("#ttsModel").onchange = e => saveSettings({eleven_tts_model: e.target.value});
$("#speed").oninput = e => $("#speedVal").textContent = Number(e.target.value).toFixed(2)+"×";
$("#speed").onchange = e => saveSettings({tempo: Number(e.target.value)});
document.querySelectorAll("#modelSeg button").forEach(b => b.onclick = () => saveSettings({modell: b.dataset.m}));
$("#pause").value = prefs.pause; $("#pauseVal").textContent = prefs.pause.toFixed(1)+" s";
$("#pause").oninput = e => { prefs.pause = Number(e.target.value); $("#pauseVal").textContent = prefs.pause.toFixed(1)+" s"; store.set("jarvis.pause", prefs.pause); };
$("#wake").checked = prefs.wake;
$("#wake").onchange = e => { prefs.wake = e.target.checked; store.set("jarvis.wake", prefs.wake); };
async function preview(){
  V.unlockAudio(); stopAudio();
  const r = await api("/api/tts", {text:"Guten Tag. So klinge ich jetzt. Sag Bescheid, wenn ich etwas tun soll.", stimme: $("#voiceSel").value});
  if(r.ok) queueBlob(Promise.resolve(await r.blob()), "Probe"); loadStatus();
}
$("#previewBtn").onclick = preview;

// ---------- Tabs ----------
function showPane(name){
  document.querySelectorAll(".tabs button").forEach(b => b.classList.toggle("sel", b.dataset.pane === name));
  ["chat", "approvals", "jobsPane", "memoryPane"].forEach(id => { $("#" + id).hidden = id !== name; });
  if(name === "jobsPane") loadJobs();
}
document.querySelectorAll(".tabs button").forEach(b => b.onclick = () => showPane(b.dataset.pane));

// ---------- Heute-Übersicht ----------
function fill(id, items, render, emptyText, countId){
  const box = $(id); box.innerHTML = "";
  if(countId) $(countId).textContent = items ? items.length : "";
  if(!items || !items.length){ box.append(el("div","empty",emptyText)); return; }
  items.forEach(it => box.append(render(it)));
}
function hint(id, html){ const b=$(id); b.innerHTML=""; const h=el("div","hint"); h.innerHTML=html; b.append(h); }
const esc = s => String(s).replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
async function loadOverview(){
  let o; try{ o = await (await api("/api/overview")).json(); }catch(e){ return; }
  $("#today").textContent = o.datum || "Heute";
  if(!o.google){ hint("#termine",'Google nicht verbunden: <code>python -m jarvis --google-verbinden</code>'); hint("#mails","–"); }
  else{
    if(o.termine_fehler) hint("#termine", "Fehler: " + esc(o.termine_fehler));
    else fill("#termine", o.termine, t => { const b=el("button","item"); const r=el("div","row"); r.append(el("span","time",t.zeit), el("span","t",t.titel)); b.append(r); if(t.ort) b.append(el("span","m","📍 "+t.ort)); b.onclick=()=>ask(`Was weißt du über meinen Termin „${t.titel}“ heute um ${t.zeit}? Gibt es dazu Mails oder Vorbereitungen?`); return b; }, "Keine Termine heute.", "#nTermine");
    if(o.mails_fehler) hint("#mails", "Fehler: " + esc(o.mails_fehler));
    else fill("#mails", o.mails, m => { const b=el("button","item"); b.append(el("span","t",m.betreff), el("span","m",m.von+" · "+m.auszug)); b.title="Zusammenfassen lassen"; b.onclick=()=>ask(`Lies die Mail mit der id ${m.id} von ${m.von} („${m.betreff}“) und fasse sie kurz zusammen. Braucht sie eine Antwort?`); return b; }, "Keine ungelesenen Mails.", "#nMails");
  }
  if(!o.hubspot) hint("#deals",'HubSpot nicht eingerichtet: <code>HUBSPOT_TOKEN</code> fehlt.');
  else if(o.deals_fehler) hint("#deals","Fehler: " + esc(o.deals_fehler));
  else fill("#deals", o.deals, d => { const b=el("button","item"); b.append(el("span","t",d.name), el("span","m",[d.betrag,d.phase,d.abschluss&&("bis "+d.abschluss.split("-").reverse().join("."))].filter(Boolean).join(" · "))); b.onclick=()=>ask(`Wie ist der Stand beim Deal „${d.name}“ und was ist der nächste sinnvolle Schritt?`); return b; }, "Keine offenen Deals.", "#nDeals");
  if(o.news_fehler) hint("#news","Nachrichten nicht abrufbar.");
  else fill("#news", o.news, n => { const a=el("a","item"); a.href=n.link; a.target="_blank"; a.rel="noopener"; a.append(el("span","t",n.titel), el("span","m",n.quelle+(n.zeit?" · "+n.zeit:""))); return a; }, "Keine Meldungen.");
}
$("#refreshBtn").onclick = loadOverview;

// ---------- Sprachausgabe (Browser-Modus) ----------
let voiceOn = store.get("jarvis.voice", true);
const VOICE_ON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M11 5 6 9H3v6h3l5 4z"/><path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13"/></svg>';
const VOICE_OFF = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M11 5 6 9H3v6h3l5 4z"/><path d="m22 9-6 6M16 9l6 6"/></svg>';
function paintVoice(){ const b=$("#voiceBtn"); b.innerHTML = voiceOn?VOICE_ON:VOICE_OFF; b.classList.toggle("active", voiceOn); b.title = voiceOn?"Vorlesen: an":"Vorlesen: aus"; }
paintVoice();
$("#voiceBtn").onclick = () => { voiceOn = !voiceOn; store.set("jarvis.voice", voiceOn); if(!voiceOn) stopAudio(); paintVoice(); };

let audioQueue = [], currentAudio = null, generation = 0, spokenRecent = [];
// Höchstens 2 Sprachausgaben gleichzeitig anfragen: Der Browser hat nur 6 Verbindungen zu Jarvis,
// sonst blockieren sich Antwort-Stream, Stopp und Sprachausgabe gegenseitig ("hängt sich auf").
const TTS_PARALLEL = 2;
let ttsInFlight = 0;
function fetchTTS(text){
  const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), 20000);
  ttsInFlight++;
  return api("/api/tts", {text}, ctl.signal).then(r => r.ok ? r.blob() : null).catch(() => null)
    .finally(() => { clearTimeout(t); ttsInFlight--; pumpTTS(); });
}
function pumpTTS(){
  for(const item of audioQueue){
    if(ttsInFlight >= TTS_PARALLEL) break;
    if(!item.p && item.gen === generation){ item.p = fetchTTS(item.text); item.resolve(item.p); }
  }
}
function speak(text, job){
  if(!voiceOn){ subtitle(text); return; }
  let resolve; const waiter = new Promise(r => { resolve = r; }).then(p => p);
  audioQueue.push({p: null, wait: waiter, resolve, gen: generation, text, job});
  pumpTTS(); if(!playing) playNext();
}
function queueBlob(p, text, job){ audioQueue.push({p, wait: p, gen: generation, text, job}); if(!playing) playNext(); }
async function playNext(){
  if(!audioQueue.length){ playing=false; refreshState(); return; }
  playing = true; refreshState();
  const item = audioQueue.shift();
  const {gen, text, job} = item;
  pumpTTS();
  const blob = gen === generation ? await Promise.race([item.wait, new Promise(r => setTimeout(() => r(null), 25000))]) : null;
  if(!blob || gen !== generation || (job && job !== jobView)){ return playNext(); }   // nie Audio eines alten Jobs
  if(job && job.audioMs == null){ job.audioMs = performance.now() - job.t0; paintJobs(); if(job.totalMs != null) reportClient(job); }
  spokenRecent.push(text); if(spokenRecent.length > 4) spokenRecent.shift();
  subtitle(text);
  const url = URL.createObjectURL(blob);
  currentAudio = new Audio(url);
  outSrc.attach(currentAudio);
  currentAudio.onended = currentAudio.onerror = () => { URL.revokeObjectURL(url); currentAudio=null; playNext(); };
  currentAudio.play().catch(() => playNext());
}
function stopAudio(){ generation++; audioQueue = []; if(currentAudio){ currentAudio.pause(); currentAudio=null; } playing=false; refreshState(); }

// ---------- Fragen (Browser-Modus über /api/ask) ----------
let abortCtrl = null, current = null, followUntil = 0, gotSentence = false, lastTyped = {text:"", t:0};
function addMsg(who, cls){
  const m = el("div","msg "+cls); m.append(el("div","who",who)); const b = el("div","body"); m.append(b); chat.append(m); scrollDown(); return b;
}
async function interrupt(){
  const wasActive = busy || playing || toolRunning;
  stopAudio();
  if(wasActive){ interruptedUntil = Date.now() + 900; setTimeout(refreshState, 950); }
  if(busy){
    try{ abortCtrl && abortCtrl.abort(); }catch{}
    api("/api/stop", {}).catch(()=>{});
    if(current) current.closest(".msg").classList.add("cut");
    lastJob = {status:"Abgebrochen", text:lastAsk.slice(0,60)};
  }
  refreshState();
}
// Gehört ein Ereignis zu genau diesem Job? Fremde Ereignisse werden verworfen (nie in die Unterhaltung schreiben).
let discardedEvents = 0;
function belongs(ev, job){
  if(!job) return false;
  if(job.id == null){
    if(ev.typ === "job" && ev.job_id){ job.id = ev.job_id; job.conv = ev.conversation_id; return true; }
    return false;
  }
  const ok = ev.job_id === job.id && (!job.conv || ev.conversation_id === job.conv);
  if(!ok) discardedEvents++;
  return ok;
}
function reportClient(job){
  if(!job || !job.id) return;
  const v = {job_id: job.id};
  if(job.feedbackMs != null) v.ui_feedback_ms = job.feedbackMs;
  if(job.firstMs != null) v.erstes_token_client_ms = job.firstMs;
  if(job.audioMs != null) v.stimme_ms = job.audioMs;
  if(job.totalMs != null) v.gesamt_client_ms = job.totalMs;
  api("/api/telemetry/client", v).catch(() => {});
}

async function ask(text, fromVoice){
  text = (text||"").trim(); if(!text) return;
  V.unlockAudio(); if(errorMsg) clearError(); showPane("chat");
  if(el11){ lastTyped = {text, t: Date.now()}; addMsg("Du","me").textContent = text; subtitle(text, true); lastAsk = text; elJobStart(text); el11.sendUserMessage(text); return; }
  if(busy){
    // Nur wenn du per Stimme direkt weiterredest, bevor er überhaupt etwas gesagt hat, gehört es zur alten Frage.
    if(fromVoice && !gotSentence && !(jobView && jobView.gotTokens)) text = lastAsk + " " + text;
    await interrupt();
    while(busy) await new Promise(r => setTimeout(r, 30));
  }
  stopAudio();
  busy = true; gotSentence = false; lastAsk = text; basisReset(); subtitle(text, true);
  const t0 = performance.now();
  jobView = {id: null, conv: null, titel: text.slice(0, 90), status: "RUNNING", schritte: [], t0, feedbackMs: null, firstMs: null, audioMs: null, totalMs: null, art: "vordergrund"};
  const myJob = jobView;
  refreshState();
  addMsg("Du","me").textContent = text;
  const body = addMsg("Jarvis","jarvis"); current = body;
  const tools = el("div","tools"); const out = el("div"); const dots = el("div","typing"); dots.innerHTML="<span></span><span></span><span></span>";
  body.append(tools, out, dots);
  abortCtrl = new AbortController();
  const myGen = generation; let failed = null;
  try{
    const r = await api("/api/ask", {text}, abortCtrl.signal);
    if(!r.ok || !r.body) throw new Error("Server antwortet nicht ("+r.status+")");
    const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = "";
    while(true){
      const {value, done} = await reader.read(); if(done) break;
      buf += dec.decode(value, {stream:true});
      let i; while((i = buf.indexOf("\n")) >= 0){
        const line = buf.slice(0,i).trim(); buf = buf.slice(i+1); if(!line) continue;
        let ev; try{ ev = JSON.parse(line); }catch{ continue; }
        if(!belongs(ev, myJob)) continue;              // falsche jobId/conversationId: verwerfen
        if(myJob.feedbackMs == null){ myJob.feedbackMs = performance.now() - t0; }
        const isCurrent = myJob === jobView;            // ein neuerer Auftrag hat übernommen? Dann nichts mehr vorlesen
        if(ev.typ === "token"){ myJob.gotTokens = true; if(myJob.firstMs == null){ myJob.firstMs = performance.now() - t0; paintJobs(); } out.textContent += ev.text; if(!voiceOn) subtitle(out.textContent.slice(-220)); }
        else if(ev.typ === "satz"){ gotSentence = true; if(!myJob.gotTokens) out.textContent += (out.textContent ? " " : "") + ev.text; if(generation===myGen && isCurrent) speak(ev.text, myJob); }
        else if(ev.typ === "job"){ paintJobs(); }
        else if(ev.typ === "schritt"){ const i = myJob.schritte.findIndex(x => x.id === ev.schritt.id); if(i >= 0) myJob.schritte[i] = ev.schritt; else myJob.schritte.push(ev.schritt); paintJobs(); }
        else if(ev.typ === "werkzeug"){ tools.append(el("span","tool", TOOL_LABELS[ev.name] || ev.name)); basisAdd(ev.name); $("#coreLabel").textContent = (TOOL_LABELS[ev.name]||"Arbeitet") + " …"; scheduleApprovals(1200); }
        else if(ev.typ === "abgebrochen"){ body.closest(".msg").classList.add("cut"); myJob.status = "CANCELLED"; }
        else if(ev.typ === "fehler"){ failed = ev.text; body.closest(".msg").classList.add("err"); out.textContent += (out.textContent?"\n":"") + "Fehler: " + friendlyError(ev.text); }
        scrollDown();
      }
    }
  }catch(e){ if(e.name !== "AbortError") failed = e.message; }
  dots.remove(); if(!tools.children.length) tools.remove();
  busy = false; abortCtrl = null;
  myJob.totalMs = performance.now() - t0;
  if(myJob.status === "RUNNING") myJob.status = failed ? "FAILED" : (myJob.schritte.some(x => x.status === "WAITING_FOR_APPROVAL") ? "WAITING_FOR_APPROVAL" : "COMPLETED");
  if(body.closest(".msg").classList.contains("cut")) myJob.status = "CANCELLED";
  reportClient(myJob); scheduleJobs(300);
  if(failed){ setError(friendlyError(failed)); if(!out.textContent) out.textContent = "Fehler: " + friendlyError(failed); body.closest(".msg").classList.add("err"); }
  else if(!body.closest(".msg").classList.contains("cut")){ lastJob = {status:"Erledigt", text:text.slice(0,60)}; if(!out.textContent) out.textContent = "(keine Antwort)"; }
  followUntil = Date.now() + 12000;
  refreshState();
  if(/mail|termin|aufgabe|deal|notiz/i.test(text)) setTimeout(loadOverview, 800);
  scheduleApprovals(200);
}

// ---------- Tastatur & Knöpfe ----------
function autosize(){ input.style.height="auto"; input.style.height=Math.min(input.scrollHeight,120)+"px"; }
input.addEventListener("input", autosize);
input.addEventListener("keydown", e => { if(e.key==="Enter" && !e.shiftKey && !e.isComposing){ e.preventDefault(); send(); } });
function send(){ const t = input.value; input.value=""; autosize(); ask(t); }
$("#sendBtn").onclick = send;
function stopAll(){
  // Stopp: Zuhören, Sprechen und laufende Aufgabe beenden. Löst nie eine externe Aktion aus.
  if(el11){ try{ el11.setVolume({volume: 0}); }catch{} interruptedUntil = Date.now() + 900; setTimeout(refreshState, 950); elJobEnd("CANCELLED"); }
  interrupt(); if(convo) setConvo(false); mockState = null; if(mockAuto){ clearInterval(mockAuto); mockAuto = null; paintMockBar(); } refreshState();
}
$("#stopBtn").onclick = stopAll;
document.addEventListener("keydown", e => {
  if(e.key === "Escape"){ if($("#app").classList.contains("open")) $("#app").classList.remove("open"); else stopAll(); }
  if(e.code === "Space" && (document.activeElement === document.body || document.activeElement === $("#core")) && !e.repeat){
    e.preventDefault(); if(isPTT()) pttStart(); else coreAction();
  }
});
document.addEventListener("keyup", e => { if(e.code === "Space" && pttActive){ e.preventDefault(); pttEnd(); } });
$("#resetBtn").onclick = async () => { if(el11) setConvo(false); await interrupt(); await api("/api/reset", {}); chat.innerHTML=""; subtitle(""); basisReset(); welcome(); };
document.querySelectorAll("#suggest button").forEach(b => b.onclick = () => ask(b.dataset.q));
function coreAction(){
  // Klick auf Kern/Gespräch: läuft gerade etwas, wird unterbrochen (Sitzung bleibt offen); sonst Sitzung an/aus.
  // Löst nie eine externe Aktion aus.
  if(!el11 && (playing || busy || toolRunning)){ interrupt(); return; }
  setConvo(!convo);
}
function isPTT(){ return prefs.talkMode === "ptt" && !useEleven() && !MOCK; }
$("#core").onclick = () => { if(!isPTT()) coreAction(); };
$("#talkBtn").onclick = () => { if(!isPTT()) coreAction(); };
["#core", "#talkBtn"].forEach(id => {
  $(id).addEventListener("pointerdown", e => { if(isPTT()){ e.preventDefault(); pttStart(); } });
  ["pointerup", "pointerleave", "pointercancel"].forEach(ev => $(id).addEventListener(ev, () => { if(isPTT()) pttEnd(); }));
});
// Fenster/Tab schließen: laufende Antwort serverseitig abbrechen
window.addEventListener("pagehide", () => { try{ fetch("/api/stop", {method:"POST", keepalive:true, headers:{"Content-Type":"application/json","X-Jarvis":"1"}, body:"{}"}); }catch{} });

// ---------- Push-to-talk (Browser-Sprachkern) ----------
let pttRec = null, pttText = "";
function pttStart(){
  if(pttActive) return;
  if(!SR){ setError("Spracherkennung wird in diesem Browser nicht unterstützt. Bitte Google Chrome verwenden."); return; }
  V.unlockAudio(); interrupt();
  pttActive = true; pttText = ""; userSpeaking = false; micSrc.start().catch(() => {});
  pttRec = new SR(); pttRec.lang = "de-DE"; pttRec.continuous = true; pttRec.interimResults = true;
  pttRec.onresult = e => { let all = ""; for(let i = 0; i < e.results.length; i++) all += e.results[i][0].transcript + " "; pttText = all.trim(); subtitle(pttText, true); if(!userSpeaking){ userSpeaking = true; refreshState(); } };
  pttRec.onerror = e => { if(e.error === "not-allowed"){ setError("Mikrofon blockiert. Bitte für localhost erlauben."); } };
  pttRec.onend = () => { const t = pttText.trim(); pttText = ""; userSpeaking = false; micSrc.stop(); refreshState(); if(t) ask(t); };
  try{ pttRec.start(); }catch{}
  $("#talkLabel").textContent = "Loslassen zum Senden"; refreshState();
}
function pttEnd(){
  if(!pttActive) return;
  pttActive = false; $("#talkLabel").textContent = "Halten zum Sprechen";
  try{ pttRec && pttRec.stop(); }catch{}
  refreshState();
}
$("#bargeSel").value = prefs.barge;
$("#bargeSel").onchange = e => { prefs.barge = e.target.value; store.set("jarvis.barge", prefs.barge); };
$("#talkMode").value = prefs.talkMode;
$("#talkMode").onchange = e => { if(convo) setConvo(false); prefs.talkMode = e.target.value; store.set("jarvis.talkMode", prefs.talkMode); paintTalkLabel(); };
function paintTalkLabel(){ if(!convo) $("#talkLabel").textContent = isPTT() ? "Halten zum Sprechen" : "Gespräch starten"; }

// ---------- Sprachkern: ElevenLabs Echtzeit ----------
function useEleven(){ return !MOCK && status.sprachkern_verfuegbar && core !== "browser" && window.ElevenLabsClient; }
function paintConvo(on){
  convo = on;
  $("#talkBtn").classList.toggle("on", on); $("#talkLabel").textContent = on ? "Gespräch beenden" : "Gespräch starten";
  paintTalkLabel(); refreshState(); paintState(ctrl.state);
}
async function startEleven(){
  paintConvo(true); toolRunning = true; refreshState(); $("#coreLabel").textContent = "Verbinde …";
  let s;
  try{
    const r = await api("/api/eleven/session", {pause: prefs.pause}); s = await r.json();
    if(!r.ok) throw new Error(s.fehler || ("Status " + r.status));
  }catch(e){
    toolRunning = false; paintConvo(false);
    addMsg("System","err").textContent = "Echtzeit-Sprachkern nicht verfügbar: " + friendlyError(e.message) + " – ich nutze den Browser-Modus.";
    core = "browser"; setConvo(true); return;
  }
  const tools = {};
  (s.werkzeuge || []).forEach(n => tools[n] = async (params) => {
    if(!elJob) await elJobStart("Sprachauftrag");
    clearTimeout(elEndTimer);
    const jobAtCall = elJob;
    toolRunning = true; basisAdd(n); refreshState(); $("#coreLabel").textContent = (TOOL_LABELS[n] || "Arbeitet") + " …";
    const m = el("div","msg jarvis"); m.append(el("div","who","")); const t = el("div","tools"); t.append(el("span","tool", TOOL_LABELS[n] || n)); m.append(t); chat.append(m); scrollDown();
    try{
      const r = await api("/api/tool", {name: n, args: params || {}, job_id: jobAtCall && jobAtCall.id}); const j = await r.json();
      if(/mail|termin|aufgabe|notiz|deal/.test(n)) setTimeout(loadOverview, 1000);
      scheduleApprovals(100);
      return j.ergebnis || "Kein Ergebnis.";
    }catch(e){ return "Fehler: Werkzeug nicht erreichbar."; }
    finally{ toolRunning = false; refreshState(); }
  });
  try{
    el11 = await ElevenLabsClient.Conversation.startSession({
      signedUrl: s.signed_url, connectionType: "websocket", clientTools: tools,
      onConnect: () => { elMode = "listening"; toolRunning = false; refreshState(); },
      onDisconnect: () => { el11 = null; elJobEnd("COMPLETED"); ctrl.setInput(micSrc); ctrl.setOutput(outSrc); paintConvo(false); },
      onError: (m) => { setError(friendlyError(m)); },
      onModeChange: ({mode}) => {
        elMode = mode; refreshState();
        clearTimeout(elEndTimer);
        if(mode === "speaking" && elJob && elJob.audioMs == null){ elJob.audioMs = performance.now() - elJob.t0; if(elJob.firstMs == null) elJob.firstMs = elJob.audioMs; paintJobs(); }
        // Auftrag gilt als erledigt, wenn er ausgeredet hat und kurz nichts mehr passiert (kein Werkzeug läuft)
        if(mode === "listening" && elJob) elEndTimer = setTimeout(() => { if(elMode === "listening" && !toolRunning) elJobEnd("COMPLETED"); }, 2500);
      },
      onMessage: (p) => {
        const role = p.role || (p.source === "ai" ? "agent" : "user");
        if(role === "user"){
          lastAsk = p.message; basisReset(); subtitle(p.message, true);
          if(p.message === lastTyped.text && Date.now() - lastTyped.t < 5000) return;
          elJobStart(p.message);
          addMsg("Du","me").textContent = p.message;
        } else { current = addMsg("Jarvis","jarvis"); current.textContent = p.message; subtitle(p.message); lastJob = {status:"Erledigt", text:lastAsk.slice(0,60)}; }
      },
      onInterruption: () => { if(current) current.closest(".msg").classList.add("cut"); elJobEnd("CANCELLED"); },
    });
    ctrl.setInput(new V.FunctionSource("ElevenLabs Mikro", () => el11 ? el11.getInputVolume() : 0));
    ctrl.setOutput(new V.FunctionSource("ElevenLabs Stimme", () => el11 ? el11.getOutputVolume() : 0));
    toolRunning = false; paintConvo(true);
  }catch(e){
    el11 = null; toolRunning = false; paintConvo(false);
    setError("Verbindung zu ElevenLabs fehlgeschlagen: " + friendlyError(e && e.message || e));
  }
}

// ---------- Jobs im ElevenLabs-Echtzeitmodus ----------
let elJob = null, elEndTimer = null;
async function elJobStart(text){
  await elJobEnd("COMPLETED");               // vorheriger Auftrag ist mit der neuen Frage abgeschlossen
  const view = {id: null, conv: "echtzeit", titel: String(text).slice(0, 90), status: "RUNNING", schritte: [], t0: performance.now(),
                feedbackMs: null, firstMs: null, audioMs: null, totalMs: null, art: "echtzeit"};
  jobView = view; elJob = view; paintJobs();
  try{ const r = await api("/api/jobs/extern", {aktion: "start", titel: view.titel}); const j = await r.json(); view.id = j.job.id; view.feedbackMs = performance.now() - view.t0; }catch{}
  scheduleJobs(100);
  return view;
}
async function elJobEnd(status){
  const j = elJob; if(!j || j.ended) return; j.ended = true; elJob = null; clearTimeout(elEndTimer);
  j.totalMs = performance.now() - j.t0;
  if(j.status === "RUNNING") j.status = j.schritte.some(x => x.status === "WAITING_FOR_APPROVAL") && status === "COMPLETED" ? "WAITING_FOR_APPROVAL" : status;
  paintJobs();
  if(j.id){ reportClient(j); try{ await api("/api/jobs/extern", {aktion: "ende", id: j.id, status}); }catch{} }
  scheduleJobs(150);
}

// ---------- Ereignis-Bus: Status aller Jobs (auch Hintergrund), ohne die Unterhaltung zu berühren ----------
let evSeq = 0;
async function eventLoop(){
  try{ const d = await (await fetch("/api/events?after=0&timeout=0")).json(); evSeq = d.seq; }catch{}
  while(true){
    try{
      const d = await (await fetch(`/api/events?after=${evSeq}&timeout=15`)).json();
      evSeq = d.seq; let touched = false;
      for(const ev of d.events){
        if(ev.typ === "schritt" && jobView && jobView.id && ev.job_id === jobView.id){
          const i = jobView.schritte.findIndex(x => x.id === ev.schritt.id);
          if(i >= 0) jobView.schritte[i] = ev.schritt; else jobView.schritte.push(ev.schritt);
          paintJobs();
        }
        if(ev.typ === "kamera") kameraEvent(ev);
        if(["job", "schritt", "fertig", "abgebrochen", "fehler"].includes(ev.typ)) touched = true;
        if(ev.typ === "schritt" && ev.schritt.status === "WAITING_FOR_APPROVAL") scheduleApprovals(100);
      }
      if(touched) scheduleJobs(150);
    }catch(e){ await new Promise(r => setTimeout(r, 2000)); }
  }
}

// ---------- Live-Kamera (Werkzeug live_kamera / kamera_schliessen) ----------
function kameraEvent(ev){
  const view = $("#kameraView"), frame = $("#kameraFrame");
  if(ev.aktion === "schliessen"){ view.classList.remove("on"); frame.textContent = ""; return; }
  const k = ev.kamera || {}; let src = "";
  if(k.art === "youtube" && /^[\w-]{11}$/.test(k.video || "")){
    src = "https://www.youtube-nocookie.com/embed/" + k.video + "?autoplay=1&mute=1&playsinline=1&rel=0&modestbranding=1";
  } else if(k.art === "windy" && /^https:\/\/([a-z0-9-]+\.)*windy\.com\//.test(k.url || "")){
    src = k.url;
  }
  if(!src && k.art === "bild" && /^https:\/\/([a-z0-9-]+\.)*windy\.com\//.test(k.url || "")){
    frame.textContent = ""; const img = document.createElement("img"); img.src = k.url; img.alt = k.titel || "Webcam";
    img.style.cssText = "position:absolute;inset:0;width:100%;height:100%;object-fit:contain"; frame.appendChild(img);
  } else {
  if(!src) return;
  frame.textContent = "";
  const f = document.createElement("iframe");
  f.src = src; f.allow = "autoplay; encrypted-media; picture-in-picture; fullscreen"; f.allowFullscreen = true;
  f.referrerPolicy = "strict-origin-when-cross-origin"; f.title = k.titel || "Live-Kamera";
  frame.appendChild(f);
  }
  $("#kameraTitel").textContent = (k.stadt ? k.stadt + " – " : "") + (k.titel || "");
  $("#kameraLive").textContent = k.live ? "LIVE" : (k.art === "bild" ? "BILD" : "ZEITRAFFER"); $("#kameraLive").classList.toggle("off", !k.live);
  $("#kameraHinweis").textContent = [k.quelle, k.hinweis].filter(Boolean).join(" · ");
  const link = $("#kameraLink"); if(/^https:\/\//.test(k.link || "")) { link.href = k.link; link.style.display = ""; } else link.style.display = "none";
  view.classList.add("on");
}
function kameraZu(){ $("#kameraView").classList.remove("on"); $("#kameraFrame").textContent = ""; }
$("#kameraClose").onclick = kameraZu;
$("#kameraView").addEventListener("click", e => { if(e.target.id === "kameraView") kameraZu(); });
document.addEventListener("keydown", e => { if(e.key === "Escape" && $("#kameraView").classList.contains("on")){ kameraZu(); e.stopPropagation(); } }, true);

// ---------- Ansicht: Jobs & Leistung ----------
let jobsTimer = null;
function scheduleJobs(delay){ clearTimeout(jobsTimer); jobsTimer = setTimeout(loadJobs, delay || 300); }
const ART = {vordergrund: "Gespräch", hintergrund: "Hintergrund", echtzeit: "Echtzeit"};
const secs = ms => ms == null ? "–" : (ms / 1000).toFixed(1).replace(".", ",") + " s";
async function loadJobs(){
  let jl, tl;
  try{ [jl, tl] = await Promise.all([api("/api/jobs").then(r => r.json()), api("/api/telemetry").then(r => r.json())]); }catch{ return; }
  const running = (jl.jobs || []).filter(j => j.status === "RUNNING").length;
  $("#jobsCount").textContent = running || "";
  const m = tl.mittel || {};
  const perf = $("#perf"); perf.innerHTML = "";
  [["⌀ Erstes Token", secs(m.erstes_token_ms)], ["⌀ LLM fertig", secs(m.llm_ende_ms)], ["⌀ Stimme", secs(m.stimme_ms)],
   ["⌀ UI-Feedback", secs(m.ui_feedback_ms)], ["⌀ Gesamt", secs(m.gesamt_ms)],
   [`Jobs · ${tl.abgebrochen || 0} abgebr. · ${tl.fehler || 0} Fehler`, String(tl.jobs || 0)]]
    .forEach(([k, v]) => { const d = el("div","k"); d.append(el("b", null, v), el("span", null, k)); perf.append(d); });
  const tel = {}; (tl.pro_job || []).forEach(r => tel[r.job] = r);
  const list = $("#jobList"); list.innerHTML = "";
  if(!(jl.jobs || []).length){ list.append(el("div","empty","Noch keine Aufträge.")); return; }
  jl.jobs.forEach(j => {
    const row = el("div","jrow"); row.dataset.id = j.id;
    const top = el("div","top"); top.append(el("span","badge " + j.art, ART[j.art] || j.art), el("span","st " + j.status, JOB_TEXT[j.status] || j.status)); row.append(top);
    row.append(el("div","ti", j.titel));
    const t = tel[j.id] || {};
    const dur = j.ende ? (j.ende - j.start) * 1000 : (Date.now() / 1000 - j.start) * 1000;
    const meta = [`${j.schritte.length} Schritte`, secs(t.gesamt_ms != null ? t.gesamt_ms : dur)];
    if(t.modell) meta.push(String(t.modell).replace("claude-", ""));
    if(t.erstes_token_ms != null) meta.push("Token " + secs(t.erstes_token_ms));
    if(t.llm_ende_ms != null) meta.push("LLM " + secs(t.llm_ende_ms));
    if(t.stimme_ms != null) meta.push("Stimme " + secs(t.stimme_ms));
    if(t.fehlerart) meta.push("Fehler: " + t.fehlerart);
    row.append(el("div","me", meta.join(" · ")));
    const acts = el("div","acts");
    if(j.status === "RUNNING" || j.status === "WAITING_FOR_APPROVAL"){
      const c = el("button","btn","Abbrechen"); c.onclick = async () => { await api("/api/jobs/cancel", {id: j.id}); if(jobView && jobView.id === j.id){ interrupt(); } scheduleJobs(100); }; acts.append(c);
    }
    if(j.status === "WAITING_FOR_APPROVAL"){ const f = el("button","btn primary","Zur Freigabe"); f.onclick = () => showPane("approvals"); acts.append(f); }
    if(j.ergebnis && j.art === "hintergrund"){ const r = el("button","btn ghost","Ergebnis"); const res = el("div","res", j.ergebnis); res.hidden = true; r.onclick = () => { res.hidden = !res.hidden; }; acts.append(r); row.append(acts, res); }
    else if(acts.children.length) row.append(acts);
    list.append(row);
  });
}
$("#stopAllJobs").onclick = async () => { await api("/api/stop", {alle: true}); stopAll(); scheduleJobs(100); };
$("#bgBtn").onclick = async () => {
  const text = input.value.trim();
  if(!text){ input.placeholder = "Erst den Auftrag eintippen, dann ⧉ für Hintergrund …"; input.focus(); return; }
  input.value = ""; autosize();
  try{ await api("/api/jobs", {text}); }catch{}
  showPane("jobsPane"); scheduleJobs(100);
};

// ---------- Sprachkern: Browser (Web Speech + eigenes Reinreden) ----------
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let rec = null, recRunning = false, flushTimer = null, heardNow = "";
const words = t => (t.toLowerCase().normalize("NFKD").replace(/[^\p{L}\p{N}\s]/gu," ").match(/\p{L}{3,}|\p{N}+/gu) || []);
function echoInfo(heard){
  const said = new Set(words(spokenRecent.join(" ") + " " + (current ? current.textContent : "")));
  const w = words(heard); const fresh = w.filter(x => !said.has(x));
  return {total: w.length, fresh: fresh.length, echo: w.length > 0 && fresh.length / w.length < 0.4};
}
const STOP = /^\s*(stopp?|halt|warte|moment|ruhe|still|sei still|danke,? das reicht|genug)\s*[.!]?\s*$/i;
const STOP_START = /^\W*(stopp?|halt|warte|moment|ruhe|sei still|genug)\b[\s,.!]*/i;
const WAKE = /(?:^|\s)(?:hey |hallo |ok(?:ay)? )?(jarvis|dscharvis|jarwis|javis|jervis)\b[\s,.:!?-]*/i;
let heardWhileActive = false, ignoredCount = 0;
const isActive = () => playing || busy || toolRunning;

// Was gehört wurde, entscheidet NICHT sofort über einen Abbruch: Er redet weiter, bis klar ist, dass du ihn meinst.
function onHeard(all){
  if(!all) return;
  const info = echoInfo(all);
  if(isActive() && info.echo){ restartRec(); return; }            // nur sein eigenes Echo
  if(isActive()){
    if(prefs.barge === "immer"){ if(info.fresh >= 2 || STOP_START.test(all)) interrupt(); }
    else {
      if(STOP_START.test(all) && loudEnough()){ interrupt(); }      // "Stopp", "Warte", "Moment" sofort
      else { heardNow = all; heardWhileActive = true; $("#micText").textContent = "hört mit …";
             clearTimeout(flushTimer); flushTimer = setTimeout(flush, prefs.pause * 1000); return; }
    }
  }
  heardNow = all; subtitle(all, true);
  if(!userSpeaking){ userSpeaking = true; refreshState(); }
  clearTimeout(flushTimer); flushTimer = setTimeout(flush, prefs.pause * 1000);
}
function noteIgnored(why){
  ignoredCount++;
  const m = $("#micText"); m.textContent = "ignoriert: " + why; setTimeout(() => { if(m.textContent.startsWith("ignoriert")) m.textContent = convo ? "Mikro an" : "Mikro aus"; }, 2500);
}
async function relevant(text){
  try{
    const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), 3500);
    const r = await api("/api/relevanz", {text, kontext: current ? current.textContent.slice(-400) : ""}, ctl.signal);
    clearTimeout(t); return !!(await r.json()).unterbrechen;
  }catch{ return false; }                                           // im Zweifel weiterreden
}
function setConvo(on){
  V.unlockAudio();
  if(MOCK){ paintConvo(on); mockState = on ? S.LISTENING : null; refreshState(); return; }
  if(useEleven()){
    if(on && !el11) return startEleven();
    if(!on && el11){ const c = el11; el11 = null; c.endSession().catch(()=>{}); ctrl.setInput(micSrc); ctrl.setOutput(outSrc); paintConvo(false); }
    else if(!on) paintConvo(false);
    return;
  }
  if(on && !SR){ setError("Spracherkennung wird in diesem Browser nicht unterstützt. Bitte Google Chrome verwenden."); return; }
  paintConvo(on);
  if(on){ startRec(); micSrc.start().catch(() => {}); }
  else { clearTimeout(flushTimer); try{ rec && rec.abort(); }catch{} micSrc.stop(); }
}
function startRec(){
  if(!convo || recRunning || el11) return;
  rec = new SR(); rec.lang = "de-DE"; rec.continuous = true; rec.interimResults = true; rec.maxAlternatives = 1;
  rec.onstart = () => { recRunning = true; refreshState(); };
  rec.onresult = e => {
    let all = ""; for(let i = 0; i < e.results.length; i++) all += e.results[i][0].transcript + " ";
    onHeard(all.trim());
  };
  rec.onerror = e => { if(e.error === "not-allowed" || e.error === "service-not-allowed"){ setConvo(false); setError("Mikrofon blockiert. Klick in der Adresszeile auf das Symbol links und erlaube das Mikrofon für localhost."); } };
  rec.onend = () => { recRunning = false; if(convo) setTimeout(startRec, 120); };
  try{ rec.start(); }catch{ recRunning = false; }
}
function restartRec(){ heardNow = ""; userSpeaking = false; clearTimeout(flushTimer); try{ rec && rec.abort(); }catch{} refreshState(); }
async function flush(){
  let text = heardNow.trim();
  const wasActive = heardWhileActive; heardWhileActive = false;
  restartRec();
  if(!text) return;
  if(STOP.test(text)){ interrupt(); return; }
  if(isActive() && echoInfo(text).echo) return;
  if(wasActive && isActive()){
    // Er spricht/arbeitet noch: nur unterbrechen, wenn du ihn wirklich meinst.
    if(!loudEnough()){ noteIgnored("Hintergrund (zu leise)"); return; }
    const wake = WAKE.test(text);
    if(prefs.barge === "aus" && !wake){ noteIgnored("nicht an mich gerichtet"); return; }
    if(!wake && !(await relevant(text))){ noteIgnored("nicht an mich gerichtet"); return; }
    await interrupt();
    text = text.replace(WAKE, " ").trim() || "Ja?";
    ask(text, false); return;
  }
  if(!loudEnough(true)){ noteIgnored("Hintergrund (zu leise)"); return; }
  if(prefs.wake && Date.now() > followUntil){
    const m = text.match(WAKE);
    if(!m) return;
    text = text.slice(m.index + m[0].length).trim() || "Ja?";
  }
  text = text.replace(STOP_START, "").trim();
  if(!text) return;
  ask(text, true);
}

// ---------- Freigaben (Vorlagen) ----------
const approvalCards = {};
let approvalTimer = null;
function scheduleApprovals(delay){ clearTimeout(approvalTimer); approvalTimer = setTimeout(refreshApprovals, delay || 300); }
async function refreshApprovals(){
  let d; try{ d = await (await api("/api/approvals")).json(); }catch{ return; }
  const list = (d.vorlagen || []).slice().reverse();
  const before = pendingApprovals.length;
  list.forEach(renderApproval);
  pendingApprovals = list.filter(a => a.status === "WAITING_FOR_APPROVAL");
  $("#tabCount").textContent = pendingApprovals.length || "";
  $("#noApprovals").hidden = Object.keys(approvalCards).length > 0;
  const b = $("#approvalBanner");
  b.classList.toggle("show", pendingApprovals.length > 0);
  if(pendingApprovals.length){
    $("#approvalCount").textContent = pendingApprovals.length === 1 ? "1 Freigabe offen" : pendingApprovals.length + " Freigaben offen";
    const a = pendingApprovals[pendingApprovals.length - 1];
    $("#approvalTitle").textContent = a.vorschau.titel + " · " + a.vorschau.ziel + (a.vorschau.zeilen[0] ? " · " + a.vorschau.zeilen[0].wert : "");
    if(pendingApprovals.length > before) showPane("approvals");
  }
  if(list.some(a => a.status === "WAITING_FOR_APPROVAL" || a.status === "RUNNING")) scheduleApprovals(3000);
  refreshState(); loadAudit();
}
function renderApproval(a){
  let c = approvalCards[a.id];
  if(!c){
    if(a.status !== "WAITING_FOR_APPROVAL" && a.status !== "UNCLEAR") return;  // UNKLAR nach Neustart immer zeigen
    c = buildApproval(a); approvalCards[a.id] = c; $("#approvals").prepend(c.root);
  }
  c.root.classList.toggle("done", a.status === "COMPLETED");
  c.root.classList.toggle("failed", a.status === "FAILED" || a.status === "UNCLEAR");
  c.root.classList.toggle("cancelled", a.status === "CANCELLED");
  const waiting = a.status === "WAITING_FOR_APPROVAL";
  c.send.hidden = c.reject.hidden = !waiting;
  c.st.textContent = waiting ? "Wartet auf deine Freigabe (Klick oder „Ja“)" : a.status_text + (a.ergebnis && a.status !== "CANCELLED" ? " – " + a.ergebnis : "");
}
function buildApproval(a){
  const root = el("div","approval"); root.dataset.id = a.id;
  const h = el("div","h"); h.append(el("span",null,"Vorlage · " + a.vorschau.titel), el("span",null,a.vorschau.ziel)); root.append(h);
  const dl = el("dl"); (a.vorschau.zeilen || []).forEach(r => { const dd = el("dd",null,r.wert); if(r.wert.length > 120) dd.classList.add("long"); dl.append(el("dt",null,r.feld), dd); }); root.append(dl);
  const f = el("div","f"); const st = el("span","st");
  const send = el("button","btn primary","Freigeben"), reject = el("button","btn","Ablehnen"), details = el("button","btn ghost","Details anzeigen");
  details.onclick = () => { const o = root.classList.toggle("open"); details.textContent = o ? "Weniger" : "Details anzeigen"; };
  f.append(st, send, reject, details); root.append(f);
  send.onclick = async () => {
    send.disabled = reject.disabled = true; st.textContent = "Wird ausgeführt …";
    try{
      const r = await api("/api/approvals/approve", {id: a.id}); const j = await r.json();
      (j.vorlagen || []).forEach(renderApproval);
      if(!r.ok) st.textContent = j.fehler || "Fehlgeschlagen";
      else if(el11){ try{ el11.sendContextualUpdate("Der Nutzer hat die Vorlage '" + a.vorschau.titel + "' per Klick freigegeben. Ergebnis: " + j.ergebnis); }catch{} }
      loadOverview();
    }catch(e){ st.textContent = "Verbindung fehlgeschlagen"; }
    send.disabled = reject.disabled = false; scheduleApprovals(50);
  };
  reject.onclick = async () => {
    const r = await api("/api/approvals/reject", {id: a.id}); const j = await r.json();
    (j.vorlagen || []).forEach(renderApproval);
    if(el11){ try{ el11.sendContextualUpdate("Der Nutzer hat die Vorlage '" + a.vorschau.titel + "' verworfen."); }catch{} }
    scheduleApprovals(50);
  };
  return {root, st, send, reject};
}
$("#approvalShow").onclick = () => { showPane("approvals"); const first = $("#approvals .approval:not(.done):not(.cancelled)"); if(first){ first.classList.add("open"); first.scrollIntoView({block:"nearest"}); } };

// ---------- Protokoll ----------
const STATUS_ICON = {ok:["✓","ok"], fehler:["✕","bad"], freigabe_fehlt:["…","wait"], abgebrochen:["–","wait"]};
async function loadAudit(){
  let d; try{ d = await (await api("/api/audit")).json(); }catch{ return; }
  const items = (d.eintraege || []).filter(e => e.werkzeug !== "lies_skill").slice(0, 8);
  const box = $("#audit"); box.innerHTML = "";
  if(!items.length){ box.append(el("div","empty","Noch keine Aktionen.")); return; }
  items.forEach(e => {
    const r = el("div","row"); const [icon, cls] = STATUS_ICON[e.status] || ["·",""];
    const t = new Date(e.zeit); const label = (TOOL_LABELS[e.werkzeug] || e.werkzeug) + (e.extern && e.freigabe ? " (freigegeben)" : e.extern ? " (Vorlage)" : "");
    r.append(el("span","time", isNaN(t) ? "" : t.toLocaleTimeString("de-DE",{hour:"2-digit",minute:"2-digit"})), el("span", cls, icon), el("span","t",label));
    r.title = e.ergebnis || ""; box.append(r);
  });
}

// ---------- Mock-Modus (Vorführen ohne Mikrofon) ----------
let mockAuto = null;
const MOCK_ORDER = [S.IDLE, S.LISTENING, S.TRANSCRIBING, S.THINKING, S.SPEAKING, S.INTERRUPTED, S.WAITING_FOR_APPROVAL, S.ERROR];
const MOCK_TEXT = {IDLE:"", LISTENING:"", TRANSCRIBING:"Wie sieht mein Tag heute aus?", INTERRUPTED:"", THINKING:"", SPEAKING:"Du hast heute drei Termine, der erste um zehn Uhr mit der Muster GmbH.",
  WAITING_FOR_APPROVAL:"Die Mail an Max Muster liegt als Vorlage bereit.", ERROR:""};
function paintMockBar(){ document.querySelectorAll(".mockbar button").forEach(b => b.classList.toggle("sel", b.dataset.s === (mockAuto ? "AUTO" : mockState || "IDLE"))); }
function setMock(s){
  mockState = s;   // im Vorführmodus zählt nur der gewählte Zustand
  if(s === S.ERROR){ errorMsg = null; $("#errorText").textContent = "Beispiel: ElevenLabs lehnt den Schlüssel ab (401)."; $("#errorBanner").classList.add("show"); }
  else $("#errorBanner").classList.remove("show");
  $("#approvalBanner").classList.toggle("show", s === S.WAITING_FOR_APPROVAL);
  if(s === S.WAITING_FOR_APPROVAL){ $("#approvalCount").textContent = "1 Freigabe offen"; $("#approvalTitle").textContent = "E-Mail senden · Gmail + HubSpot · max@muster.de"; }
  subtitle(MOCK_TEXT[s], s === S.TRANSCRIBING);
  basisReset(); if(s === S.THINKING){ basisAdd("kalender_termine"); basisAdd("mails_suchen"); }
  refreshState(); paintMockBar();
}
if(MOCK){
  const bar = el("div","mockbar");
  MOCK_ORDER.concat(["AUTO"]).forEach(s => { const b = el("button", null, s === "AUTO" ? "Auto" : s.replace("WAITING_FOR_APPROVAL","APPROVAL")); b.dataset.s = s;
    b.onclick = () => { if(mockAuto){ clearInterval(mockAuto); mockAuto = null; }
      if(s === "AUTO"){ let i = 0; setMock(MOCK_ORDER[0]); mockAuto = setInterval(() => { i = (i + 1) % MOCK_ORDER.length; setMock(MOCK_ORDER[i]); }, 3500); paintMockBar(); }
      else setMock(s); };
    bar.append(b); });
  $(".center").prepend(bar); paintMockBar();
}

// Für automatische Tests
window.jarvisHUD = { ctrl, computeState, setMock, stopAll, belongs, get state(){ return ctrl.state; },
  heard: async (text, peak) => { testPeak = peak == null ? 1 : peak; onHeard(text); clearTimeout(flushTimer); await flush(); },
  get ignored(){ return ignoredCount; },
  get jobView(){ return jobView; }, get discarded(){ return discardedEvents; } };

function welcome(){ addMsg("Jarvis","jarvis").textContent = "Alle Systeme online. Klick auf den Kern und sprich einfach – du kannst mich jederzeit unterbrechen."; }
window.__hudReady = true;
loadStatus().then(paintTalkLabel); loadOverview(); welcome(); refreshState(); loadAudit(); scheduleApprovals(500); eventLoop(); scheduleJobs(400);
setInterval(loadOverview, 5*60*1000);
