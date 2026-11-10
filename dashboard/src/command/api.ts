/** Datenzugriff der Kommandozentrale (über Java -> lokale Python-Brücke). Schreibaufrufe tragen X-Jarvis. */

export type Priority = "niedrig" | "normal" | "hoch" | "kritisch";
export type OrderStatus = "eingang" | "klaerung" | "geplant" | "in_arbeit" | "freigabe" | "erledigt";
export type Order = {
  id: string;
  version: number;
  titel: string;
  kunde: string;
  projekt: string;
  prioritaet: Priority;
  verantwortlich: string;
  frist: string;
  status: OrderStatus;
  position: number;
  checkliste: { text: string; erledigt: boolean }[];
  abhaengigkeiten: string[];
  quelle: string;
  notiz: string;
  jobs: string[];
  runs: string[];
  erstellt: number;
  geaendert: number;
};
export type Column = { id: OrderStatus; titel: string };
export type CalendarEvent = {
  id: string;
  kalender_id: string;
  kalender: string;
  titel: string;
  ganztaegig: boolean;
  zeitzone: string;
  beginn: string | null;
  ende: string | null;
  datum_beginn: string | null;
  datum_ende: string | null;
  teilnehmer: { email: string; name: string; antwort: string; ich: boolean }[];
  ort: string;
  meeting_link: string;
  serie_id: string | null;
  ausnahme: boolean;
  status: string;
  frei: boolean;
  etag: string | null;
  link: string;
  gueltig: boolean;
};
export type CalendarData = {
  status: "ok" | "nicht_verbunden" | "demo" | "fehler" | "offline";
  hinweis?: string;
  zeitzone?: string;
  termine: CalendarEvent[];
  konflikte: { a: string; b: string; titel: [string, string]; ab: string; bis: string }[];
  gekuerzt?: boolean;
};
export type Approval = {
  id: string;
  werkzeug: string;
  vorschau: { titel: string; ziel: string; zeilen: { feld: string; wert: string }[] };
  status: string;
  status_text: string;
  ergebnis: string;
  erstellt: number;
  laeuft_ab: number;
  duplikat?: string | null;
  job: { id: string; status: string; titel: string } | null;
  graph: "wartet" | null;
};

export type Job = {
  id: string;
  conversation: string;
  art: string;
  titel: string;
  status: string;
  schritte: { id: string; werkzeug: string; status: string }[];
  start: number;
  ende: number | null;
  fehler: string | null;
};
export type ReadyState = "bereit" | "nicht_eingerichtet" | "fehler";
export type Automations = {
  stand: number;
  agenten: { id: string; name: string; rolle: string; modell: string; status: ReadyState; hinweis: string }[];
  ablaeufe: {
    id: string; name: string; beschreibung: string; aktiv: boolean; art: string; zeitplan: string;
    letzter_lauf: { status: string; zeit: number; fehler: string } | null;
  }[];
  ablaeufe_schleife: "läuft" | "aus";
  n8n: { status: ReadyState; hinweis: string; workflows: { name: string; beschreibung: string; freigabe: boolean }[] };
  integrationen: { id: string; name: string; status: ReadyState; hinweis: string }[];
  jobs_laufend: number;
  /** Was die Sprachkette tatsächlich nutzt (aus der Konfiguration). Fehlt bei älterem Server. */
  sprache?: { stimme: string; stimme_anbieter: string; stimme_modell: string; stimme_hinweis: string; erkennung: string } | null;
};

/** Sprach-/Modell-Einstellungen (wie früher auf 8765), ohne Zugangsdaten. */
export type Settings = {
  modell: string; modell_key: string; modelle: string[]; stimme: string; tempo: number;
  stimmen: { id: string; name: string; gruppe?: string }[];
  elevenlabs: boolean; elevenlabs_problem: string | null;
  sprachkern_verfuegbar: boolean; sprachkern_problem: string | null;
  eleven_tts_model: string; eleven_tts_modelle: string[]; google: boolean; hubspot: boolean; demo?: boolean;
};
export type CallProject = { id: string; version: number; name: string; crm: string; zielgruppe: string; leitfaden: string; regeln: string; notiz: string; aktiv: boolean };
export type CallRecord = {
  id: string; projekt_id: string; erstellt: number; lead_name: string; firma: string; rolle: string; telefon_status: string;
  kontakt_quelle: string; ergebnis: string; notiz: string; naechster_schritt: string; frist: string; crm_status: string;
  dauer_s: number | null; auftrag_id: string;
};
export type CallStats = {
  zeitraum_tage: number; gespraeche: number; nach_ergebnis: Record<string, number>; termine: number; termine_pro_100: number | null;
  crm_offen: number; followups_faellig: number; mit_naechstem_schritt: number;
  ohne_naechsten_schritt?: number; telefon_ungeklaert?: number; ohne_kontaktquelle?: number; mittlere_dauer_s: number | null; quelle: string; stand: string;
};
export type CallOverview = { projekte: CallProject[]; kennzahlen: CallStats; letzte_anrufe: CallRecord[]; ergebnisse: Record<string, string> };
export type ContentHook = { text: string; belegt_durch?: string; klarheit?: number; relevanz?: number; neugier?: number; belegbarkeit?: number; summe?: number | null };
export type ContentItem = {
  id: string; version: number; status: string; titel: string; thema: string; ziel: string; zielgruppe: string; plattformen: string[];
  hooks: ContentHook[]; skript: string; briefing: string; quellen: string[]; ki_hinweis: string; notiz: string;
  ergebnisse: { plattform: string; erfasst: string; quelle: string; [k: string]: string | number }[];
};
export type ContentOverview = { spalten: { id: string; titel: string }[]; eintraege: ContentItem[]; plattformen: string[]; quelle: string };
export type BusEvent = { seq: number; typ: string; [key: string]: any };
/** Jarvis-Laufzeitfähigkeit (ausführbar, aus jarvis/faehigkeiten.py) – nicht zu verwechseln mit Markdown-Skills. */
export type Capability = {
  name: string; titel: string; beschreibung: string; berechtigung: string; berechtigung_text: string;
  abbrechbar: boolean; eingabe: { properties: Record<string, { description?: string; enum?: string[] }>; required: string[] };
  zeitlimit_s?: number; art?: "ausfuehrbar" | "skill" | "cloud"; version?: string; herkunft?: string; aktiviert?: boolean; sperrgrund?: string;
};
/** Jarvis-Skill (Markdown-Anleitung) mit prüfbarer Definition aus jarvis/skill_laufzeit.py. */
export type SkillDefinition = {
  id: string; name: string; beschreibung: string; version: string; herkunft: "mitgeliefert" | "eigen" | "importiert";
  quelle: string; commit: string; art: string; werkzeuge: string[]; fehlende_werkzeuge: string[];
  rahmen: "frei" | "deklariert" | "nur_lesen"; berechtigungen: Record<string, string[]>; bestaetigung: string;
  zeitlimit_s: number; abbrechbar: boolean; aktiviert: boolean; sperrgrund: string; verdachtsstellen: number;
};
export type Platform = {
  faehigkeiten: Capability[]; laeufe: CapabilityRun[]; skills: SkillDefinition[];
  berechtigungen: Record<string, string>; freigabepflichtig: string[];
  github: { git: boolean; token: boolean; freigegeben: string[]; privat_moeglich: boolean };
  cloud: { erlaubt: string[]; verfuegbar: string[]; sperrgrund: string; anbieter: string };
};
export type GitHubSummary = {
  repo: string; url: string; commit: string; ref: string; privat: boolean; geladen: string; arbeitsbereich: string;
  titel: string; beschreibung: string; readme_auszug: string; readme_datei: string; readme_verdacht: number;
  lizenz: { datei: string; erkannt: string; laut_github: string };
  struktur: { oberste_ebene: string[]; dateien: number; groesse_kb: number; dateitypen: string[] };
  stack: { manifeste: string[]; sprachen: string[]; abhaengigkeiten: string[] };
  skill_dateien: string[]; hinweis: string;
};
export type CapabilityRun = {
  id: string; faehigkeit: string; quelle: string; status: "RUNNING" | "PLANNED" | "COMPLETED" | "FAILED" | "CANCELLED" | "WAITING_FOR_APPROVAL";
  start: number; ende: number | null; eingabe: Record<string, string>;
  ergebnis: { text?: string; auftrag_id?: string; titel?: string; github?: GitHubSummary;
    treffer?: { repo: string; beschreibung: string; sterne: number; sprache: string; lizenz: string; url: string }[]; [k: string]: unknown } | null;
  fehler: string | null; abbrechbar: boolean; fortschritt?: string; schritte?: { werkzeug: string; status: string }[]; wiederholbar?: boolean;
};
export type LogEntry = Record<string, unknown>;

/** Agenten-Center: ein echter Job mit Agent, Schritten und Zeiten (aus jobs.py/SQLite). */
export type JobStep = {
  id: string; werkzeug: string; status: string; berechtigung: string; start: number | null; ende: number | null;
  ergebnis: string | null; fehler: string | null; freigabe_id?: string | null;
};
export type AgentJob = {
  id: string; titel: string; art: string; status: string; agent: { id: string; name: string; art: string }; unteragenten: string[];
  gespraech: string; start: number; ende: number | null; letzte_aktivitaet: number; schritte: JobStep[];
  schritte_gesamt: number; schritte_fertig: number; fortschritt: string; werkzeuge: string[];
  ergebnis: string | null; fehler: string | null; unterbrochen_durch_neustart: boolean; freigaben: string[];
  abbrechbar: boolean; wiederholbar: boolean; faehigkeit: string | null; modell: string | null;
  nutzung: { tokens: Record<string, number>; kosten_usd?: number | null } | null;
  repo: { name: string; commit: string; url: string } | null;
};
export type AgentRole = {
  id: string; name: string; aufgabe: string; skill: string | null; werkzeuge: string[]; fehlende_werkzeuge: string[];
  status: "bereit" | "teilweise" | "fehlt" | "deaktiviert" | "nicht_eingerichtet"; grund: string; umsetzung: string;
  startbar: boolean; vorlage: string; jobs_aktiv: number; jobs_gesamt: number;
  letzter_job: { id: string; titel: string; status: string; start: number } | null;
};
export type AgentCenterData = {
  jobs: AgentJob[]; zahlen: Record<string, number>; stand: number; hinweis: string;
  agenten: Automations["agenten"]; rollen: AgentRole[];
};
export type ConnectionState = "verbunden" | "unvollstaendig" | "abgelaufen" | "nicht_eingerichtet" | "fehler";
export type Connection = {
  id: string; name: string; zweck: string; zustand: ConnectionState; pruefung: "live" | "konfiguration";
  details: string[]; schritte: string[]; hinweis: string; geprueft?: string;
  dienste: Record<string, { name: string; aktiv: boolean }>;
};
export type Connections = { stand: string; verbindungen: Connection[]; braucht_dich: number; demo?: boolean };
export type MemoryFactFull = {
  id: string; content: string; category: string; source: string; client: string; created_at: string; updated_at: string;
  valid_until: string | null; confidence: number; consent: string; confirmed_at: string | null; origin_episode: string;
  conflicts: string[]; gueltig: boolean; versionen: number;
};
export type MemoryArchiveItem = {
  id: string; conversation_id: string; user_text: string; assistant_text: string; status: string; created_at: string;
  kanal: string; updated_at: string | null;
};
export type MemoryCenterData = {
  suche: string; wissen: MemoryFactFull[]; archiv: MemoryArchiveItem[]; stand: string;
  notizdateien?: { name: string; groesse: number; geaendert: string; auszug: string }[];
  zahlen: { facts: number; episodes: number; events: number; path: string; ausstehend: number; fehlgeschlagen: number;
    widersprueche: number; archiv_seit: string | null; archiv_nach_status: Record<string, number>; archiv_nach_kanal: Record<string, number> };
};
export type MemoryHistory = {
  aktuell: MemoryFactFull; versionen: { content: string; category: string; source: string; changed_at: string; reason: string }[];
  ereignisse: { typ: string; zeit: string }[];
};

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public body: any = null,
  ) {
    super(message);
  }
}

async function call<T>(method: string, url: string, body?: unknown): Promise<T> {
  const response = await fetch(url, {
    method,
    headers: body === undefined ? (method === "GET" ? {} : { "X-Jarvis": "1" })
      : { "Content-Type": "application/json", "X-Jarvis": "1" },
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "same-origin",
  });
  let data: any = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }
  if (!response.ok) throw new ApiError(response.status, data?.fehler || data?.hinweis || data?.message || `Fehler ${response.status}`, data);
  return data as T;
}

export const requestId = () => `ui-${crypto.randomUUID().replaceAll("-", "").slice(0, 20)}`;

export const api = {
  orders: () => call<{ spalten: Column[]; auftraege: Order[] }>("GET", "/api/orders"),
  createOrder: (felder: Partial<Order>) =>
    call<{ auftrag: Order }>("POST", "/api/orders", { felder, request_id: requestId() }),
  updateOrder: (order: Order, aenderungen: Partial<Order>, id = requestId()) =>
    call<{ auftrag: Order }>("PATCH", `/api/orders/${encodeURIComponent(order.id)}`, {
      version: order.version,
      aenderungen,
      request_id: id,
    }),
  calendar: (von = "", bis = "") =>
    call<CalendarData>("GET", `/api/calendar?von=${encodeURIComponent(von)}&bis=${encodeURIComponent(bis)}`),
  approvals: () => call<{ freigaben: Approval[] }>("GET", "/api/approvals"),
  jobs: () => call<{ jobs: Job[] }>("GET", "/api/jobs"),
  automations: () => call<Automations>("GET", "/api/automations"),
  settings: () => call<Settings>("GET", "/api/settings"),
  saveSettings: (changes: Partial<{ stimme: string; tempo: number; modell: string; eleven_tts_model: string }>) =>
    call<Settings>("POST", "/api/settings", changes),
  ttsPreview: (text: string, stimme?: string) => call<{ mime: string; audio: string }>("POST", "/api/tts/preview", { text, stimme }),
  realtimeSession: (pause: number) => call<{ signed_url: string; agent_id: string; werkzeuge: string[] }>("POST", "/api/realtime/session", { pause }),
  realtimeTool: (name: string, args: unknown, jobId: string | null) =>
    call<{ ergebnis: string }>("POST", "/api/realtime/tool", { name, args: args ?? {}, job_id: jobId }),
  externJob: (body: { aktion: "start"; titel: string } | { aktion: "ende"; id: string; status: "COMPLETED" | "CANCELLED" | "FAILED" }) =>
    call<{ job?: Job; fehler?: string }>("POST", "/api/jobs/extern", body),
  cancelJob: (id: string) => call<{ abgebrochen: boolean }>("POST", "/api/jobs/cancel", { id }),
  stopAllJobs: () => call<{ ok: boolean; abgebrochen: string[] }>("POST", "/api/jobs/stop-all", {}),
  resetConversation: () => call<{ ok: boolean }>("POST", "/api/reset", {}),
  logs: (art: "audit" | "aktionen" | "ereignisse") => call<{ eintraege: LogEntry[] }>("GET", `/api/logs/${art}`),
  events: (after: number, timeout: number, signal?: AbortSignal) =>
    fetch(`/api/events?after=${after}&timeout=${timeout}`, { credentials: "same-origin", signal })
      .then(async (r) => { if (!r.ok) throw new ApiError(r.status, `Ereignisse nicht erreichbar (${r.status})`); return r.json() as Promise<{ events: BusEvent[]; seq: number }>; }),
  content: () => call<ContentOverview>("GET", "/api/content"),
  setContentStatus: (id: string, status: string) => call<{ eintrag: ContentItem }>("POST", "/api/content/status", { id, status }),
  calls: (tage = 7) => call<CallOverview>("GET", `/api/anrufe?tage=${tage}`),
  capabilities: () => call<Platform>("GET", "/api/faehigkeiten"),
  startCapability: (name: string, args: Record<string, unknown>) =>
    call<{ lauf: CapabilityRun }>("POST", "/api/faehigkeiten/starten", { name, args }),
  setSkillActive: (id: string, aktiv: boolean) => call<{ ok: boolean }>("POST", "/api/skills/aktivieren", { id, aktiv }),
  capabilityAction: (id: string, aktion: "abbrechen" | "wiederholen") =>
    call<{ abgebrochen?: boolean; lauf?: CapabilityRun }>("POST", `/api/faehigkeiten/${encodeURIComponent(id)}/${aktion}`, {}),
  agentCenter: () => call<AgentCenterData>("GET", "/api/agenten"),
  connections: (fresh = false) => call<Connections>("GET", `/api/verbindungen${fresh ? "?neu=true" : ""}`),
  memoryCenter: (q = "", limit = 60) => call<MemoryCenterData>("GET", `/api/gedaechtnis?q=${encodeURIComponent(q)}&limit=${limit}`),
  memoryHistory: (id: string) => call<MemoryHistory>("GET", `/api/gedaechtnis/verlauf/${encodeURIComponent(id)}`),
  memoryExport: () => call<{ datei: string; wissen: number; versionen: number; archiv: number; exportiert: string; inhalt?: unknown }>("GET", "/api/gedaechtnis/export"),
  memoryAction: (aktion: "speichern" | "aendern" | "bestaetigen" | "loeschen" | "archiv_loeschen" | "index_neu", body: Record<string, unknown> = {}) =>
    call<Record<string, any>>("POST", `/api/gedaechtnis/${aktion}`, body),
  transcript: (rolle: "user" | "agent", text: string, ref: string) =>
    call<{ id: string | null; status: string; duplicate?: boolean }>("POST", "/api/transkript", { rolle, text, ref }),
  decide: (id: string, aktion: "freigeben" | "verwerfen") =>
    call<{ ergebnis: string }>("POST", `/api/approvals/${encodeURIComponent(id)}/${aktion}`, {}),
};
