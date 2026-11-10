import { Suspense, lazy, useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { api, ApiError, type Approval, type Automations, type CalendarData, type CalendarEvent, type Column, type Job, type Order, type OrderStatus } from "../command/api";
import { boardReducer, dayKey, initialBoard, matches, timeRange } from "../command/model";
import { KanbanPanel } from "../command/KanbanPanel";
import { CallsPanel } from "../command/CallsPanel";
import { ApprovalsPanel } from "../command/ApprovalsPanel";
import { AgendaPanel } from "../command/AgendaPanel";
import { AgentsPanel, AutomationsPanel, stateLabel } from "../command/AutomationPanels";
import { EventDetail } from "../command/EventDetail";
import { CommandCenter } from "../command/CommandCenter";
import { Telemetry } from "../command/telemetry";
import { LiveCamera, useBusEvents, type CameraInfo } from "../command/LiveCamera";
import type { RealtimeSession } from "../command/realtime";
import { DEFAULT_PREFS, type VoicePrefs } from "../command/SettingsDialog";
import type { CallOverview, CapabilityRun, Platform, Settings } from "../command/api";
import { Icon } from "../command/icons";
import type { ContentView } from "../view/intent";
import type { AudioEngine } from "../audio";
import type { Role, Status, Transcript } from "../types";
import "../command/command.css";

const CalendarPanel = lazy(() => import("../command/CalendarPanel"));
const MapboxWorld = lazy(() => import("../world/MapboxWorld"));

export type CommandSection =
  | "uebersicht" | "auftraege" | "kalender" | "termin" | "anrufe" | "agenten" | "automatisierungen" | "welt" | "gespraech" | "freigaben";
type NavItem = { id: CommandSection; label: string; icon: string };
const SECTIONS: NavItem[] = [
  { id: "uebersicht", label: "Übersicht", icon: "overview" },
  { id: "auftraege", label: "Aufträge", icon: "orders" },
  { id: "kalender", label: "Kalender", icon: "calendar" },
  { id: "anrufe", label: "Anrufe", icon: "phone" },
  { id: "agenten", label: "Agenten", icon: "agents" },
  { id: "automatisierungen", label: "Automatisierungen", icon: "automation" },
  { id: "welt", label: "World View", icon: "world" },
  { id: "gespraech", label: "Command Center", icon: "chat" },
  { id: "freigaben", label: "Freigaben", icon: "approvals" },
];
const TITLES: Record<CommandSection, string> = {
  uebersicht: "Übersicht", auftraege: "Aufträge", kalender: "Kalender", termin: "Termin", anrufe: "Anrufe", agenten: "Agenten",
  automatisierungen: "Automatisierungen", welt: "World View", gespraech: "Command Center", freigaben: "Freigaben",
};
const MORE: { id: ContentView; label: string; icon: string }[] = [
  { id: "news", label: "Nachrichten", icon: "news" },
  { id: "market", label: "Märkte", icon: "market" },
  { id: "cameras", label: "Kameras", icon: "camera" },
  { id: "memory", label: "Gedächtnis", icon: "memory" },
  { id: "ops", label: "System", icon: "system" },
];
const DEFAULT_COLUMNS: Column[] = [
  { id: "eingang", titel: "Eingang" }, { id: "klaerung", titel: "In Klärung" }, { id: "geplant", titel: "Geplant" },
  { id: "in_arbeit", titel: "In Arbeit" }, { id: "freigabe", titel: "Prüfung" }, { id: "erledigt", titel: "Erledigt" },
];
// Spaltennamen im Dashboard (Status-IDs im Backend bleiben unverändert).
const COLUMN_TITLES: Partial<Record<OrderStatus, string>> = { freigabe: "Prüfung" };
const PRIO_ORDER = ["kritisch", "hoch", "normal", "niedrig"];

const ALIASES: [RegExp, CommandSection][] = [
  [/auftr|kanban|board/, "auftraege"], [/kalender|termin/, "kalender"], [/agenda/, "kalender"], [/anruf|telefon|vertrieb|lead/, "anrufe"], [/agent/, "agenten"],
  [/automat|ablauf|abläuf|workflow|n8n/, "automatisierungen"], [/welt|karte|world|map/, "welt"],
  [/gespr|chat|coach|command center|sprach/, "gespraech"], [/freigab/, "freigaben"], [/übersicht|uebersicht|start|home/, "uebersicht"],
];
export function sectionFromQuery(query?: string): CommandSection {
  const q = (query || "").toLowerCase();
  return ALIASES.find(([re]) => re.test(q))?.[1] ?? "uebersicht";
}

const greeting = () => {
  const h = new Date().getHours();
  return h < 11 ? "Guten Morgen" : h < 18 ? "Guten Tag" : "Guten Abend";
};

type Selected = { kind: "order"; order: Order } | null;

export type WorkspaceProps = {
  query?: string;
  section?: CommandSection;
  worldQuery?: string;
  role: Role;
  connected: boolean;
  onAsk: (text: string) => void;
  onView: (view: ContentView) => void;
  onBack?: () => void;
  transcripts?: Transcript[];
  status?: Status;
  audio?: AudioEngine | null;
  micOn?: boolean;
  onMicChange?: (on: boolean) => void;
  onLog?: (m: string, l?: "info" | "warn") => void;
  onStop?: () => void;
  telemetry?: Telemetry;
  muted?: boolean;
  onMutedChange?: (muted: boolean) => void;
  voiceMode?: "studio" | "echtzeit";
  onStartRealtime?: () => Promise<void>;
  onStopRealtime?: () => Promise<void>;
  getRealtime?: () => RealtimeSession | null;
  voicePrefs?: VoicePrefs;
  onVoicePrefs?: (prefs: VoicePrefs) => void;
  onReset?: () => void;
};

export default function CommandView({
  query, section: forced, worldQuery, role, connected, onAsk, onView,
  transcripts = [], audio = null, micOn = false, onMicChange = () => undefined, onLog = () => undefined, onStop = () => undefined,
  telemetry: givenTelemetry, muted = false, onMutedChange = () => undefined,
  voiceMode = "studio", onStartRealtime = async () => undefined, onStopRealtime = async () => undefined, getRealtime = () => null,
  voicePrefs = DEFAULT_PREFS, onVoicePrefs = () => undefined, onReset = () => undefined,
}: WorkspaceProps) {
  const [ownTelemetry] = useState(() => new Telemetry());
  const telemetry = givenTelemetry ?? ownTelemetry;
  const [section, setSection] = useState<CommandSection>(() => forced ?? sectionFromQuery(query));
  useEffect(() => { if (forced) setSection(forced); }, [forced, worldQuery]);
  useEffect(() => { if (query) setSection(sectionFromQuery(query)); }, [query]);
  const [navOpen, setNavOpen] = useState(false);
  const [calTab, setCalTab] = useState<"monat" | "liste">("monat");
  const [search, setSearch] = useState("");
  const [board, dispatch] = useReducer(boardReducer, initialBoard);
  const [columns, setColumns] = useState<Column[]>(DEFAULT_COLUMNS);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [calendar, setCalendar] = useState<CalendarData | null>(null);
  const [range, setRange] = useState<{ von: string; bis: string } | null>(null);
  const [automations, setAutomations] = useState<Automations | null>(null);
  const [automationError, setAutomationError] = useState<string | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [offline, setOffline] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [selected, setSelected] = useState<Selected>(null);
  const [event, setEvent] = useState<CalendarEvent | null>(null);
  const canEdit = role === "owner";
  const [settings, setSettings] = useState<Settings | null>(null);
  const [camera, setCamera] = useState<CameraInfo | null>(null);
  const [platform, setPlatform] = useState<Platform | null>(null);
  const [calls, setCalls] = useState<CallOverview | null>(null);
  const [callsError, setCallsError] = useState<string | null>(null);
  const [capError, setCapError] = useState<string | null>(null);
  const [capBusy, setCapBusy] = useState<string | null>(null);
  const alive = useRef(true);
  useEffect(() => () => void (alive.current = false), []);

  const go = useCallback((next: CommandSection) => {
    setSection(next);
    setNavOpen(false);
  }, []);
  const openEvent = useCallback((e: CalendarEvent) => {
    setEvent(e);
    go("termin");
  }, [go]);

  const loadOrders = useCallback(async () => {
    try {
      const data = await api.orders();
      if (!alive.current) return;
      if (!Array.isArray(data?.auftraege)) throw new Error("Unerwartete Antwort vom Jarvis-Server.");
      const cols = (Array.isArray(data.spalten) && data.spalten.length ? data.spalten : DEFAULT_COLUMNS)
        .map((c) => ({ ...c, titel: COLUMN_TITLES[c.id] ?? c.titel }));
      setColumns(cols);
      dispatch({ type: "load", orders: data.auftraege });
      setOffline(null);
    } catch (error) {
      if (alive.current) setOffline(error instanceof ApiError ? error.message : "Jarvis-Server nicht erreichbar.");
    }
  }, []);
  const loadCalls = useCallback(async () => {
    try {
      const data = await api.calls(7);
      if (!alive.current) return;
      if (!data || !Array.isArray(data.projekte) || !data.kennzahlen) throw new Error("Unerwartete Antwort vom Anruf-Speicher.");
      setCalls(data);
      setCallsError(null);
    } catch (error) {
      if (alive.current) setCallsError(error instanceof ApiError ? error.message : error instanceof Error ? error.message : "Anrufe nicht erreichbar.");
    }
  }, []);
  useEffect(() => { if (section === "anrufe") void loadCalls(); }, [section, loadCalls]);
  const loadApprovals = useCallback(async () => {
    try {
      const data = await api.approvals();
      if (alive.current && Array.isArray(data?.freigaben)) setApprovals(data.freigaben);
    } catch { /* letzter Stand bleibt sichtbar */ }
  }, []);
  const loadCalendar = useCallback(async (von = range?.von ?? "", bis = range?.bis ?? "") => {
    try {
      const data = await api.calendar(von, bis);
      if (!alive.current) return;
      setCalendar(data && Array.isArray(data.termine)
        ? { ...data, konflikte: Array.isArray(data.konflikte) ? data.konflikte : [] }
        : { status: "fehler", hinweis: "Unerwartete Antwort vom Kalender.", termine: [], konflikte: [] });
    } catch (error) {
      if (alive.current)
        setCalendar((old) => old ?? { status: "offline", hinweis: error instanceof Error ? error.message : "offline", termine: [], konflikte: [] });
    }
  }, [range]);
  const loadWork = useCallback(async () => {
    const [a, j] = await Promise.allSettled([api.automations(), api.jobs()]);
    if (!alive.current) return;
    if (a.status === "fulfilled" && Array.isArray(a.value?.agenten) && Array.isArray(a.value?.ablaeufe)) {
      setAutomations(a.value);
      setAutomationError(null);
    }
    else setAutomationError(a.status === "rejected" && a.reason instanceof ApiError && a.reason.status === 404
      ? "Der Jarvis-Server ist älter als dieses Dashboard (Endpunkt /api/automations fehlt) – Jarvis neu starten."
      : "Status von Agenten und Automatisierungen nicht erreichbar.");
    if (j.status === "fulfilled") setJobs(Array.isArray(j.value?.jobs) ? j.value.jobs : []);
  }, []);

  const loadSettings = useCallback(async () => {
    try {
      const data = await api.settings();
      if (alive.current && Array.isArray(data?.stimmen)) setSettings(data);
    } catch { /* Kopfzeile zeigt dann „–“ */ }
  }, []);
  useEffect(() => { void loadSettings(); }, [loadSettings]);
  const loadCapabilities = useCallback(async () => {
    try {
      const data = await api.capabilities();
      if (!alive.current) return;
      if (!Array.isArray(data?.faehigkeiten)) throw new Error("Unerwartete Antwort");
      setPlatform({ ...data, laeufe: data.laeufe ?? [], skills: data.skills ?? [] });
      setCapError(null);
    } catch (error) {
      if (alive.current) setCapError(error instanceof ApiError && error.status === 404
        ? "Der Jarvis-Server ist älter als dieses Dashboard (/api/faehigkeiten fehlt) – Jarvis neu starten."
        : "Fähigkeiten nicht erreichbar – läuft der Jarvis-Server?");
    }
  }, []);
  useEffect(() => { void loadCapabilities(); }, [loadCapabilities]);
  const startCapability = useCallback(async (name: string, args: Record<string, unknown>) => {
    const result = await api.startCapability(name, args);
    if (result?.lauf) setPlatform((old) => old && { ...old, laeufe: [result.lauf, ...old.laeufe.filter((r) => r.id !== result.lauf.id)] });
  }, []);
  const toggleSkill = useCallback(async (id: string, active: boolean) => {
    try {
      await api.setSkillActive(id, active);
    } catch (error) {
      setNotice(`Skill nicht umgeschaltet: ${error instanceof Error ? error.message : "unbekannt"}`);
    }
    void loadCapabilities();
  }, [loadCapabilities]);
  const capabilityAction = useCallback(async (id: string, action: "abbrechen" | "wiederholen") => {
    setCapBusy(id);
    try {
      await api.capabilityAction(id, action);
    } catch (error) {
      setNotice(`${action === "abbrechen" ? "Abbrechen" : "Wiederholen"} nicht möglich: ${error instanceof Error ? error.message : "unbekannt"}`);
    } finally {
      setCapBusy(null);
      void loadCapabilities();
    }
  }, [loadCapabilities]);
  // Ereignis-Bus: Live-Kameras (Werkzeug live_kamera) und Job-Fortschritt, auch aus dem Echtzeit-Modus.
  const jobTimer = useRef<number | undefined>(undefined);
  useBusEvents(connected, (ev) => {
    if (ev.typ === "kamera") setCamera(ev.aktion === "schliessen" ? null : (ev.kamera ?? null));
    else if (ev.typ === "faehigkeit" && ev.lauf && typeof ev.lauf.id === "string") {
      const run = ev.lauf as CapabilityRun;
      setPlatform((old) => old && { ...old, laeufe: [run, ...old.laeufe.filter((r) => r.id !== run.id)].sort((a, b) => b.start - a.start).slice(0, 25) });
      if (ev.phase === "fertig") void loadOrders(); // neuer Auftrag sofort im Kanban
    } else {
      window.clearTimeout(jobTimer.current);
      jobTimer.current = window.setTimeout(() => { void loadWork(); void loadApprovals(); }, 300);
    }
  });

  useEffect(() => {
    void loadOrders();
    void loadApprovals();
    void loadWork();
    const fast = window.setInterval(() => void loadApprovals(), 10_000);
    const slow = window.setInterval(() => { void loadOrders(); void loadWork(); }, 30_000);
    return () => { window.clearInterval(fast); window.clearInterval(slow); };
  }, [loadOrders, loadApprovals, loadWork]);
  useEffect(() => {
    void loadCalendar();
    const t = window.setInterval(() => void loadCalendar(), 120_000);
    return () => window.clearInterval(t);
  }, [loadCalendar]);

  const move = useCallback(async (order: Order, next: OrderStatus, position: number) => {
    if (!canEdit) return;
    dispatch({ type: "move", id: order.id, status: next, position });
    try {
      const result = await api.updateOrder(order, { status: next, position });
      dispatch({ type: "confirm", order: result.auftrag });
    } catch (error) {
      const current = error instanceof ApiError && error.status === 409 ? (error.body?.aktuell as Order) : undefined;
      dispatch({
        type: "rollback", id: order.id, current,
        message: current ? "Der Auftrag wurde inzwischen geändert – aktueller Stand geladen." :
          `Verschieben fehlgeschlagen, zurückgesetzt (${error instanceof Error ? error.message : "unbekannt"}).`,
      });
    }
  }, [canEdit]);
  const create = useCallback(async (fields: Partial<Order>) => {
    try {
      const result = await api.createOrder(fields);
      dispatch({ type: "add", order: result.auftrag });
    } catch (error) {
      setNotice(`Nicht angelegt: ${error instanceof Error ? error.message : "unbekannt"}`);
    }
  }, []);
  const decide = useCallback(async (id: string, action: "freigeben" | "verwerfen") => {
    try {
      const result = await api.decide(id, action);
      setNotice(action === "freigeben" ? `Ausgeführt: ${result.ergebnis}` : "Verworfen.");
    } catch (error) {
      setNotice(`Nicht möglich: ${error instanceof Error ? error.message : "unbekannt"}`);
    }
    void loadApprovals();
    void loadOrders();
  }, [loadApprovals, loadOrders]);
  const openOrder = useCallback(async (id: string) => {
    let order = board.orders.find((o) => o.id === id);
    if (!order) {
      try {
        const data = await api.orders();
        dispatch({ type: "load", orders: data.auftraege });
        order = data.auftraege.find((o) => o.id === id);
      } catch { /* Kanban zeigt dann den letzten Stand */ }
    }
    if (order) setSelected({ kind: "order", order });
    go("auftraege");
  }, [board.orders, go]);
  const ask = useCallback((text: string) => {
    onAsk(text);
    go("gespraech");
  }, [onAsk, go]);

  const openApprovals = approvals.filter((a) => a.status === "WAITING_FOR_APPROVAL");
  const tz = calendar?.zeitzone || "Europe/Berlin";
  const today = dayKey(new Date(), tz);
  const active = board.orders.filter((o) => o.status !== "erledigt");
  const overdue = active.filter((o) => o.frist && o.frist < today);
  const runningJobs = jobs.filter((j) => j.status === "RUNNING" || j.status === "WAITING_FOR_APPROVAL");
  const troubled = (automations?.ablaeufe ?? []).filter((w) => w.aktiv && w.letzter_lauf && ["FAILED", "UNCLEAR"].includes(w.letzter_lauf.status));
  const stats = useMemo(() => ({
    termineHeute: (calendar?.termine ?? []).filter((e) =>
      e.status !== "cancelled" && (e.beginn ? dayKey(new Date(e.beginn), tz) === today : e.datum_beginn === today)).length,
    konflikte: calendar?.konflikte.length ?? 0,
  }), [calendar, today, tz]);
  const next = (calendar?.termine ?? []).find((e) => e.status !== "cancelled" && e.beginn && new Date(e.beginn).getTime() > Date.now());
  const calendarOk = calendar?.status === "ok";
  const webcams = (automations?.integrationen.find((i) => i.id === "windy")?.status ?? null) as "bereit" | "nicht_eingerichtet" | null;

  const focus: { tone: "attn" | "warn"; text: string; action: string; to: CommandSection }[] = [];
  if (openApprovals.length) focus.push({ tone: "attn", text: `${openApprovals.length} ${openApprovals.length === 1 ? "Freigabe wartet" : "Freigaben warten"} auf deine Entscheidung.`, action: "Prüfen", to: "freigaben" });
  if (overdue.length) focus.push({ tone: "warn", text: `${overdue.length} ${overdue.length === 1 ? "Auftrag ist" : "Aufträge sind"} überfällig: ${overdue.slice(0, 2).map((o) => o.titel).join(", ")}${overdue.length > 2 ? " …" : ""}`, action: "Aufträge", to: "auftraege" });
  if (stats.konflikte) focus.push({ tone: "warn", text: `${stats.konflikte} Terminüberschneidung${stats.konflikte === 1 ? "" : "en"} im Kalender.`, action: "Kalender", to: "kalender" });
  if (calendar && !calendarOk && calendar.status !== "demo")
    focus.push({ tone: "warn", text: `Kalender nicht verfügbar: ${calendar.hinweis || "unbekannter Grund"}`, action: "Details", to: "kalender" });
  for (const w of troubled) focus.push({ tone: "warn", text: `Ablauf „${w.name}“: letzter Lauf ${stateLabel(w.letzter_lauf!.status).toLowerCase()}${w.letzter_lauf!.fehler ? ` (${w.letzter_lauf!.fehler})` : ""}.`, action: "Ansehen", to: "automatisierungen" });

  const navButton = (s: NavItem) => (
    <button key={s.id} type="button" className={section === s.id || (s.id === "kalender" && section === "termin") ? "active" : ""}
      aria-current={section === s.id ? "page" : undefined} onClick={() => go(s.id)}>
      <Icon name={s.icon} /> {s.label}
      {s.id === "freigaben" && openApprovals.length > 0 && <span className="cc-badge">{openApprovals.length}</span>}
    </button>
  );

  return (
    <section className="cc" aria-label="Jarvis Arbeitsbereich">
      <nav className={`cc-nav ${navOpen ? "open" : ""}`} aria-label="Hauptnavigation">
        <div className="cc-brand"><span className="cc-logo">J</span><span>Jarvis</span></div>
        {SECTIONS.map(navButton)}
        <div className="cc-nav-more">Weitere Ansichten</div>
        {MORE.map((m) => (
          <button key={m.id} type="button" onClick={() => { setNavOpen(false); onView(m.id); }}><Icon name={m.icon} /> {m.label}</button>
        ))}
        <div className="cc-nav-foot">
          <span>{connected ? "Mit Jarvis verbunden" : "Keine Verbindung zu Jarvis"}</span>
        </div>
      </nav>

      {navOpen && <button type="button" className="cc-backdrop" aria-label="Navigation schließen" onClick={() => setNavOpen(false)} />}
      <div className="cc-main">
        <header className="cc-top">
          <button type="button" className="cc-btn small cc-menu-btn" aria-label="Navigation öffnen" aria-expanded={navOpen} onClick={() => setNavOpen((v) => !v)}>
            <Icon name="menu" />
          </button>
          <h1>{TITLES[section]}</h1>
          {["uebersicht", "auftraege", "kalender", "freigaben"].includes(section) && (
            <input type="search" className="cc-search" placeholder="Suchen in Aufträgen, Terminen, Freigaben …" aria-label="Suchen"
              value={search} onChange={(e) => setSearch(e.target.value)} />
          )}
          <div className="cc-pills">
            <span className={`cc-pill ${connected ? "ok" : "warn"}`}>{connected ? "Verbunden" : "Getrennt"}</span>
            <span className={`cc-pill ${calendarOk ? "ok" : "warn"}`}>Kalender {calendarOk ? "verbunden" : calendar ? "nicht verfügbar" : "lädt"}</span>
            {openApprovals.length > 0 && <button type="button" className="cc-pill attn" onClick={() => go("freigaben")}>{openApprovals.length} Freigaben</button>}
          </div>
        </header>
        {offline && <div className="cc-error" role="status">{offline} Angezeigt wird der letzte bekannte Stand.</div>}
        {notice && (
          <div className="cc-notice" role="status">{notice} <button type="button" className="cc-link" onClick={() => setNotice(null)}>OK</button></div>
        )}

        <div className={`cc-work ${section === "welt" || section === "gespraech" ? "flush" : ""}`} key={section}>
          {section === "uebersicht" && (
            <>
              <div className="cc-page-head">
                <div>
                  <h2>{greeting()}</h2>
                  <p>{new Date().toLocaleDateString("de-DE", { timeZone: tz, weekday: "long", day: "numeric", month: "long", year: "numeric" })}
                    {next && <> · Nächster Termin: <strong style={{ color: "var(--cc-text)" }}>{next.titel}</strong> um {timeRange(next, tz).split("–")[0]}</>}</p>
                </div>
                {canEdit && <button type="button" className="cc-btn accent" onClick={() => ask("Was steht heute an? Gib mir die drei wichtigsten Punkte.")}>Briefing von Jarvis</button>}
              </div>
              <div className="cc-focus" aria-label="Braucht deine Aufmerksamkeit">
                {focus.length === 0 ? (
                  <div className="cc-focus-item"><p>Nichts wartet gerade auf dich – keine offenen Freigaben, nichts überfällig.</p></div>
                ) : focus.map((f, i) => (
                  <div key={i} className={`cc-focus-item ${f.tone}`}><p>{f.text}</p>
                    <button type="button" className="cc-btn small" onClick={() => go(f.to)}>{f.action}</button></div>
                ))}
              </div>
              <div className="cc-kpis">
                <button type="button" onClick={() => go("kalender")}><strong>{calendarOk ? stats.termineHeute : "–"}</strong><span>Termine heute</span></button>
                <button type="button" onClick={() => go("auftraege")}><strong>{active.length}</strong><span>Offene Aufträge</span></button>
                <button type="button" onClick={() => go("auftraege")}><strong>{active.filter((o) => o.status === "in_arbeit").length}</strong><span>In Arbeit</span></button>
                <button type="button" className={openApprovals.length ? "attn" : ""} onClick={() => go("freigaben")}><strong>{openApprovals.length}</strong><span>Freigaben offen</span></button>
                <button type="button" className={overdue.length ? "warn" : ""} onClick={() => go("auftraege")}><strong>{overdue.length}</strong><span>Überfällig</span></button>
                <button type="button" onClick={() => go("agenten")}><strong>{runningJobs.length}</strong><span>Laufende Arbeiten</span></button>
              </div>
              <div className="cc-grid">
                <section className="cc-card-box">
                  <header><h3>Nächste Termine</h3><button type="button" className="cc-link" onClick={() => go("kalender")}>Kalender</button></header>
                  <AgendaPanel data={calendar} query={search} limitDays={2} canAsk={canEdit} onAsk={ask} onSelect={openEvent} />
                </section>
                <section className="cc-card-box">
                  <header><h3>Offene Aufträge</h3><button type="button" className="cc-link" onClick={() => go("auftraege")}>Alle</button></header>
                  <ul className="cc-list">
                    {active.filter((o) => matches(o, search))
                      .sort((a, b) => PRIO_ORDER.indexOf(a.prioritaet) - PRIO_ORDER.indexOf(b.prioritaet) || (a.frist || "9").localeCompare(b.frist || "9"))
                      .slice(0, 6)
                      .map((o) => (
                        <li key={o.id}>
                          <button type="button" className="cc-row" onClick={() => { setSelected({ kind: "order", order: o }); go("auftraege"); }}>
                            <span className="grow"><span className="title">{o.titel}</span>
                              <span className="meta">{columns.find((c) => c.id === o.status)?.titel}{o.kunde ? ` · ${o.kunde}` : ""}{o.frist ? ` · bis ${o.frist}` : ""}</span></span>
                            <span className={`cc-prio prio-${o.prioritaet}`}>{o.prioritaet}</span>
                          </button>
                        </li>
                      ))}
                    {active.length === 0 && <li className="cc-empty">Keine offenen Aufträge. Lege einen unter „Aufträge“ an oder sag es Jarvis.</li>}
                  </ul>
                </section>
                <section className="cc-card-box">
                  <header><h3>Laufende Arbeit</h3><button type="button" className="cc-link" onClick={() => go("agenten")}>Details</button></header>
                  <ul className="cc-list">
                    {jobs.slice(0, 5).map((j) => (
                      <li key={j.id} className="cc-row"><span className="grow"><span className="title">{j.titel || "Auftrag"}</span>
                        <span className="meta">{j.art}</span></span><span className={`cc-state ${j.status}`}>{stateLabel(j.status)}</span></li>
                    ))}
                    {jobs.length === 0 && <li className="cc-empty">Keine Jobs seit dem letzten Start von Jarvis.</li>}
                  </ul>
                  {automations && (
                    <p className="cc-hint">{automations.ablaeufe.filter((w) => w.aktiv).length} von {automations.ablaeufe.length} geplanten Abläufen aktiv ·
                      Zeitplaner {automations.ablaeufe_schleife}</p>
                  )}
                </section>
              </div>
            </>
          )}
          {section === "auftraege" && (
            <div style={{ display: "grid", gridTemplateColumns: selected ? "minmax(0,1fr) 300px" : "1fr", gap: 16 }}>
              <KanbanPanel columns={columns} orders={board.orders} pending={board.pending} query={search} canEdit={canEdit}
                selectedId={selected?.order.id ?? null}
                onSelect={(order) => setSelected({ kind: "order", order })} onMove={(o, s, p) => void move(o, s, p)}
                onCreate={create} error={board.error} />
              {selected && (
                <aside className="cc-detail" style={{ alignSelf: "start" }} aria-label="Auftragsdetails">
                  <h3>{selected.order.titel}</h3>
                  <dl>
                    <dt>Status</dt><dd>{columns.find((c) => c.id === selected.order.status)?.titel}</dd>
                    <dt>Kunde</dt><dd>{selected.order.kunde || "–"}</dd>
                    <dt>Projekt</dt><dd>{selected.order.projekt || "–"}</dd>
                    <dt>Priorität</dt><dd>{selected.order.prioritaet}</dd>
                    <dt>Frist</dt><dd>{selected.order.frist || "–"}</dd>
                    <dt>Verknüpft</dt><dd>{selected.order.jobs.length} Jobs · {selected.order.runs.length} Läufe</dd>
                  </dl>
                  {selected.order.notiz && <p style={{ margin: 0 }}>{selected.order.notiz}</p>}
                  {selected.order.checkliste.length > 0 && (
                    <ul className="cc-list">{selected.order.checkliste.map((c, i) => <li key={i}>{c.erledigt ? "☑" : "☐"} {c.text}</li>)}</ul>
                  )}
                  <div className="cc-actions">
                    {canEdit && <button type="button" className="cc-btn small accent" onClick={() => ask(`Wie gehe ich den Auftrag "${selected.order.titel}" am besten an? Nächster Schritt?`)}>Nächsten Schritt fragen</button>}
                    <button type="button" className="cc-btn small" onClick={() => setSelected(null)}>Schließen</button>
                  </div>
                </aside>
              )}
            </div>
          )}
          {section === "kalender" && (
            <>
              <div className="cc-page-head">
                <div className="cc-tabs" role="group" aria-label="Kalenderansicht">
                  <button type="button" aria-pressed={calTab === "monat"} onClick={() => setCalTab("monat")}>Kalender</button>
                  <button type="button" aria-pressed={calTab === "liste"} onClick={() => setCalTab("liste")}>Agenda</button>
                </div>
              </div>
              {calTab === "monat" ? (
                <Suspense fallback={<p className="cc-empty">Kalender lädt …</p>}>
                  <CalendarPanel data={calendar}
                    onRange={(von, bis) => { if (range?.von === von && range?.bis === bis) return; setRange({ von, bis }); }}
                    onSelect={openEvent} />
                </Suspense>
              ) : (
                <AgendaPanel data={calendar} query={search} canAsk={canEdit} onAsk={ask} onSelect={openEvent} />
              )}
            </>
          )}
          {section === "termin" && (event
            ? <EventDetail event={event} data={calendar} canAsk={canEdit} onAsk={ask} onBack={() => go("kalender")} />
            : <p className="cc-empty">Kein Termin ausgewählt. <button type="button" className="cc-link" onClick={() => go("kalender")}>Zum Kalender</button></p>)}
          {section === "anrufe" && (
            <>
              <div className="cc-page-head"><div><h2>Anrufe</h2><p>Gemessene Zahlen aus den von Jarvis erfassten Gesprächen. Nichts wird ohne Freigabe ins CRM geschrieben.</p></div>
                <button type="button" className="cc-link" onClick={() => void loadCalls()}>Aktualisieren</button></div>
              <CallsPanel data={calls} error={callsError} canAsk={canEdit} onAsk={ask} />
            </>
          )}
          {section === "agenten" && (
            <>
              <div className="cc-page-head"><div><h2>Agenten</h2><p>Welche KI-Rollen Jarvis wirklich bereitstellt und was gerade läuft.</p></div></div>
              <AgentsPanel data={automations} jobs={jobs} error={automationError} canAsk={canEdit} onAsk={ask} />
            </>
          )}
          {section === "automatisierungen" && (
            <>
              <div className="cc-page-head"><div><h2>Automatisierungen</h2><p>Geplante Abläufe, n8n-Workflows und Integrationen – mit tatsächlichem Status.</p></div></div>
              <AutomationsPanel data={automations} error={automationError} />
            </>
          )}
          {section === "welt" && (
            <Suspense fallback={<p className="cc-empty" style={{ padding: 24 }}>Karte lädt …</p>}>
              <MapboxWorld query={worldQuery} webcams={webcams} />
            </Suspense>
          )}
          {section === "gespraech" && (
            <CommandCenter telemetry={telemetry} audio={audio} role={role} connected={connected} transcripts={transcripts}
              micOn={micOn} onMicChange={onMicChange} muted={muted} onMutedChange={onMutedChange} onAsk={onAsk} onStop={onStop} onLog={onLog}
              automations={automations} approvals={approvals} onOpenApprovals={() => go("freigaben")}
              settings={settings} onSettings={setSettings} voiceMode={voiceMode} onStartRealtime={onStartRealtime}
              onStopRealtime={onStopRealtime} getRealtime={getRealtime} prefs={voicePrefs} onPrefs={onVoicePrefs} onReset={onReset}
              platform={platform} capabilityError={capError} capabilityBusy={capBusy}
              onCapabilityAction={(id, action) => void capabilityAction(id, action)} onOpenOrder={openOrder}
              onStartCapability={startCapability} onToggleSkill={(id, active) => void toggleSkill(id, active)} />
          )}
          {section === "freigaben" && <ApprovalsPanel items={approvals} canDecide={canEdit} onDecide={decide} query={search} />}
        </div>
      </div>
      {camera && <LiveCamera camera={camera} onClose={() => setCamera(null)} />}
    </section>
  );
}
