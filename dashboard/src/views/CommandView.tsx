import { Suspense, lazy, useCallback, useEffect, useMemo, useReducer, useRef, useState, type ReactNode } from "react";
import { api, ApiError, type Approval, type Automations, type CalendarData, type CalendarEvent, type Column, type Job, type Order, type OrderStatus } from "../command/api";
import { boardReducer, dayKey, initialBoard, timeRange } from "../command/model";
import { KanbanPanel } from "../command/KanbanPanel";
import { CallsPanel } from "../command/CallsPanel";
import { ContentPanel } from "../command/ContentPanel";
import { ApprovalsPanel } from "../command/ApprovalsPanel";
import { AgendaPanel } from "../command/AgendaPanel";
import { AutomationsPanel } from "../command/AutomationPanels";
import { AgentCenter, isJob } from "../command/AgentCenter";
import { TodayView } from "../command/TodayView";
import { buildQueue, buildSuggestions, type QueueItem } from "../command/today";
import { MemoryCenter } from "../command/MemoryCenter";
import { ConnectionsPanel } from "../command/ConnectionsPanel";
import { SkillPlatform } from "../command/SkillPlatform";
import { EventDetail } from "../command/EventDetail";
import { CrmPanel } from "../command/CrmPanel";
import { SkillsOverview } from "../command/SkillsOverview";
import { COMMAND_URL, COMMAND_WINDOW, openCommandCenter } from "../command/commandWindow";
import type { Telemetry } from "../command/telemetry";
import { LiveCamera, useBusEvents, type CameraInfo } from "../command/LiveCamera";
import type { RealtimeSession, SaveState } from "../command/realtime";
import { DEFAULT_PREFS, type VoicePrefs } from "../command/SettingsDialog";
import type { AgentCenterData, AgentRole, CallOverview, Connections, ContentOverview, CapabilityRun, Lead, Platform, Settings } from "../command/api";
import { Icon } from "../command/icons";
import type { ContentView } from "../view/intent";
import type { AudioEngine } from "../audio";
import type { Role, Status, Transcript } from "../types";
import "../command/command.css";

const CalendarPanel = lazy(() => import("../command/CalendarPanel"));
const MapboxWorld = lazy(() => import("../world/MapboxWorld"));

export type CommandSection =
  | "uebersicht" | "auftraege" | "kalender" | "termin" | "anrufe" | "content" | "agenten" | "automatisierungen" | "gedaechtnis"
  | "verbindungen" | "welt" | "gespraech" | "freigaben";
type NavItem = { id: CommandSection; label: string; icon: string };
/** Navigation: Lage und Entscheidungen, dann Vertrieb (CRM zuerst), zuletzt das System.
 *  Das Command Center ist kein Abschnitt mehr: es öffnet sich über einen eigenen Link in einem eigenen Fenster. */
const NAV: { title: string; items: NavItem[] }[] = [
  { title: "Jarvis", items: [
    { id: "uebersicht", label: "Heute", icon: "overview" },
    { id: "freigaben", label: "Freigaben", icon: "approvals" },
    { id: "agenten", label: "Agenten / Jobs", icon: "agents" },
  ] },
  { title: "Vertrieb", items: [
    { id: "anrufe", label: "CRM", icon: "phone" },
    { id: "kalender", label: "Kalender", icon: "calendar" },
    { id: "auftraege", label: "Aufträge", icon: "orders" },
    { id: "content", label: "Content", icon: "news" },
  ] },
  { title: "System", items: [
    { id: "automatisierungen", label: "Skills und Automationen", icon: "automation" },
    { id: "gedaechtnis", label: "Gedächtnis", icon: "memory" },
    { id: "verbindungen", label: "Verbindungen", icon: "link" },
  ] },
];
/** Mobile Leiste: höchstens fünf Ziele (vier + Menü), der Rest liegt im Menü. */
const TABS: NavItem[] = [...NAV[0].items, NAV[1].items[0]];
const TITLES: Record<CommandSection, string> = {
  uebersicht: "Heute", auftraege: "Aufträge", kalender: "Kalender", termin: "Termin", anrufe: "CRM", content: "Content", agenten: "Agenten / Jobs",
  automatisierungen: "Skills und Automationen", gedaechtnis: "Gedächtnis", verbindungen: "Verbindungen und Einrichtung",
  welt: "World View", gespraech: "Command Center", freigaben: "Freigaben",
};
const MORE: { id: ContentView | "welt"; label: string; icon: string }[] = [
  { id: "welt", label: "World View", icon: "world" },
  { id: "news", label: "Nachrichten", icon: "news" },
  { id: "market", label: "Märkte", icon: "market" },
  { id: "cameras", label: "Kameras", icon: "camera" },
  { id: "ops", label: "Systemprotokoll", icon: "system" },
];
const DEFAULT_COLUMNS: Column[] = [
  { id: "eingang", titel: "Eingang" }, { id: "klaerung", titel: "In Klärung" }, { id: "geplant", titel: "Geplant" },
  { id: "in_arbeit", titel: "In Arbeit" }, { id: "freigabe", titel: "Prüfung" }, { id: "erledigt", titel: "Erledigt" },
];
// Spaltennamen im Dashboard (Status-IDs im Backend bleiben unverändert).
const COLUMN_TITLES: Partial<Record<OrderStatus, string>> = { freigabe: "Prüfung" };

const ALIASES: [RegExp, CommandSection][] = [
  [/auftr|kanban|board/, "auftraege"], [/kalender|termin/, "kalender"], [/agenda/, "kalender"], [/anruf|telefon|vertrieb|lead|pipeline|follow|crm|cold/, "anrufe"], [/content|video|hook|tiktok|reel|shorts/, "content"], [/agent|job/, "agenten"],
  [/automat|ablauf|abläuf|workflow|n8n|skill/, "automatisierungen"], [/ged[aä]chtnis|erinner|memory/, "gedaechtnis"],
  [/verbind|einricht|integration|dienst/, "verbindungen"], [/welt|karte|world|map/, "welt"],
  [/gespr|chat|coach|command center|sprach/, "gespraech"], [/freigab/, "freigaben"], [/übersicht|uebersicht|heute|start|home/, "uebersicht"],
];
export function sectionFromQuery(query?: string): CommandSection {
  const q = (query || "").toLowerCase();
  return ALIASES.find(([re]) => re.test(q))?.[1] ?? "uebersicht";
}

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
  transcriptSave?: SaveState;
  onRetryTranscripts?: () => void;
  onStartRealtime?: () => Promise<void>;
  onStopRealtime?: () => Promise<void>;
  getRealtime?: () => RealtimeSession | null;
  voicePrefs?: VoicePrefs;
  onVoicePrefs?: (prefs: VoicePrefs) => void;
  onReset?: () => void;
  /** Raum = Gespräch; nötig für Dateianhänge. */
  room?: string;
  /** Memory v1: je Antwort (stream_id) die mitgeschickten Erinnerungen. */
  memoryUse?: Record<string, { id: string; inhalt: string }[]>;
  /** Geöffnete „Mehr“-Ansicht (Nachrichten, Märkte, Kameras, Systemprotokoll) – läuft in derselben Schiene. */
  more?: { id: ContentView; node: ReactNode } | null;
};

export default function CommandView({
  query, section: forced, worldQuery, role, connected, onView, more = null,
  voiceMode = "studio", transcriptSave, onRetryTranscripts = () => undefined,
}: WorkspaceProps) {
  const [section, setSection] = useState<CommandSection>(() => forced ?? sectionFromQuery(query));
  useEffect(() => { if (forced) setSection(forced); }, [forced, worldQuery]);
  useEffect(() => { if (query) setSection(sectionFromQuery(query)); }, [query]);
  // Navigation: eingeklappt nur Punkt und „Jarvis“. Sie öffnet sich, solange die Maus darüber ist, solange der
  // Tastaturfokus darin liegt, oder nach Antippen/Betätigen der Marke (navOpen, bleibt bis zur Auswahl oder Esc).
  const [navOpen, setNavOpen] = useState(false);
  const [navHover, setNavHover] = useState(false);
  const [navKeys, setNavKeys] = useState(false);
  const lastPointer = useRef("");
  const navExpanded = navOpen || navHover || navKeys;
  const closeNav = useCallback(() => { setNavOpen(false); setNavKeys(false); }, []);
  const [moreOpen, setMoreOpen] = useState(false);
  const [ready, setReady] = useState(false);
  const [calTab, setCalTab] = useState<"monat" | "liste">("monat");
  const [search, setSearch] = useState("");
  const [board, dispatch] = useReducer(boardReducer, initialBoard);
  const [columns, setColumns] = useState<Column[]>(DEFAULT_COLUMNS);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [calendar, setCalendar] = useState<CalendarData | null>(null);
  const [range, setRange] = useState<{ von: string; bis: string } | null>(null);
  const [automations, setAutomations] = useState<Automations | null>(null);
  const [automationError, setAutomationError] = useState<string | null>(null);
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
  const [contentData, setContentData] = useState<ContentOverview | null>(null);
  const [contentError, setContentError] = useState<string | null>(null);
  const [capError, setCapError] = useState<string | null>(null);
  const [capBusy, setCapBusy] = useState<string | null>(null);
  const [agentData, setAgentData] = useState<AgentCenterData | null>(null);
  const [agentError, setAgentError] = useState<string | null>(null);
  const [focusJob, setFocusJob] = useState<string | null>(null);
  const [dueLeads, setDueLeads] = useState<Lead[] | null>(null);
  const [focusLead, setFocusLead] = useState<string | null>(null);
  const [connections, setConnections] = useState<Connections | null>(null);
  const [connError, setConnError] = useState<string | null>(null);
  const [connBusy, setConnBusy] = useState(false);
  const alive = useRef(true);
  useEffect(() => () => void (alive.current = false), []);

  const go = useCallback((next: CommandSection) => {
    setNavKeys(false);
    setSection(next);
    setNavOpen(false);
    if (more) onView("conversation"); // „Mehr“-Ansicht schließen, sobald ein Abschnitt gewählt wird
  }, [more, onView]);
  /** Command Center in eigenem Fenster öffnen; mit Text wird der Auftrag dorthin übergeben. */
  const [blocked, setBlocked] = useState(false);
  const openCenter = useCallback((text?: string) => {
    const opened = openCommandCenter(text);
    setBlocked(!opened);
    if (opened && text) setNotice("Auftrag an das Command Center übergeben.");
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
  const [callDays, setCallDays] = useState(7);
  const loadCalls = useCallback(async (days = callDays) => {
    try {
      const data = await api.calls(days);
      if (!alive.current) return;
      if (!data || !Array.isArray(data.projekte) || !data.kennzahlen) throw new Error("Unerwartete Antwort vom Anruf-Speicher.");
      setCalls(data);
      setCallsError(null);
    } catch (error) {
      if (alive.current) setCallsError(error instanceof ApiError ? error.message : error instanceof Error ? error.message : "Anrufe nicht erreichbar.");
    }
  }, [callDays]);
  useEffect(() => { if (section === "anrufe" || section === "uebersicht") void loadCalls(); }, [section, loadCalls]);
  // Fällige Follow-ups für „Heute“ kommen vom Lead selbst (aktueller Stand), nicht aus alten Anruf-Zeilen.
  useEffect(() => {
    if (section !== "uebersicht") return;
    api.leads("follow_up").then((d) => { if (alive.current && Array.isArray(d?.leads)) setDueLeads(d.leads); })
      .catch(() => { /* älterer Server ohne /api/leads: „Heute“ bleibt ohne CRM-Follow-ups */ });
  }, [section]);
  const loadContent = useCallback(async () => {
    try {
      const data = await api.content();
      if (!alive.current) return;
      if (!data || !Array.isArray(data.eintraege) || !Array.isArray(data.spalten)) throw new Error("Unerwartete Antwort vom Content-Speicher.");
      setContentData(data);
      setContentError(null);
    } catch (error) {
      if (alive.current) setContentError(error instanceof Error ? error.message : "Content nicht erreichbar.");
    }
  }, []);
  useEffect(() => { if (section === "content" || section === "uebersicht") void loadContent(); }, [section, loadContent]);
  const moveContent = useCallback(async (id: string, status: string) => {
    try { await api.setContentStatus(id, status); await loadContent(); }
    catch (error) { if (alive.current) setNotice(error instanceof Error ? error.message : "Status nicht geändert."); }
  }, [loadContent]);
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
    const [a] = await Promise.allSettled([api.automations()]);
    if (!alive.current) return;
    if (a.status === "fulfilled" && Array.isArray(a.value?.agenten) && Array.isArray(a.value?.ablaeufe)) {
      setAutomations(a.value);
      setAutomationError(null);
    }
    else setAutomationError(a.status === "rejected" && a.reason instanceof ApiError && a.reason.status === 404
      ? "Der Jarvis-Server ist älter als dieses Dashboard (Endpunkt /api/automations fehlt) – Jarvis neu starten."
      : "Status von Agenten und Automatisierungen nicht erreichbar.");
    try {
      const data = await api.agentCenter();
      if (!alive.current) return;
      if (!Array.isArray(data?.jobs) || !Array.isArray(data?.rollen)) throw new Error("Unerwartete Antwort");
      setAgentData(data);
      setAgentError(null);
    } catch (error) {
      if (alive.current) setAgentError(error instanceof ApiError && error.status === 404
        ? "Der Jarvis-Server ist älter als dieses Dashboard (/api/agenten fehlt) – Jarvis neu starten."
        : "Agenten-Center nicht erreichbar – läuft der Jarvis-Server?");
    }
  }, []);
  const loadConnections = useCallback(async (fresh = false) => {
    setConnBusy(true);
    try {
      const data = await api.connections(fresh);
      if (!alive.current) return;
      if (!Array.isArray(data?.verbindungen)) throw new Error("Unerwartete Antwort");
      setConnections(data);
      setConnError(null);
    } catch (error) {
      if (alive.current) setConnError(error instanceof ApiError && error.status === 404
        ? "Der Jarvis-Server ist älter als dieses Dashboard (/api/verbindungen fehlt) – Jarvis neu starten."
        : "Verbindungen nicht prüfbar – läuft der Jarvis-Server?");
    } finally { if (alive.current) setConnBusy(false); }
  }, []);
  useEffect(() => { if (section === "verbindungen" || section === "uebersicht") void loadConnections(); }, [section, loadConnections]);

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
  /** Rolle/Skill als echten Job starten (Fähigkeit skill_starten): eigenes Gespräch, eigener Werkzeug-Rahmen. */
  const startSkill = useCallback(async (skill: string, task: string) => {
    await api.startCapability("skill_starten", { skill, auftrag: task });
    void loadCapabilities();
    void loadWork();
  }, [loadCapabilities, loadWork]);
  const startRole = useCallback(async (role: AgentRole, task: string) => {
    if (!role.skill) throw new Error("Diese Rolle hat keine startbare Skill.");
    await startSkill(role.skill, task);
  }, [startSkill]);
  const cancelJob = useCallback(async (id: string) => {
    setCapBusy(id);
    try {
      const result = await api.cancelJob(id);
      if (!result.abgebrochen) setNotice("Der Job war bereits beendet – nichts abgebrochen.");
    } catch (error) {
      setNotice(`Abbrechen nicht möglich: ${error instanceof Error ? error.message : "unbekannt"}`);
    } finally { setCapBusy(null); void loadWork(); }
  }, [loadWork]);
  const openJob = useCallback((id?: string) => { setFocusJob(id ?? null); go("agenten"); }, [go]);
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
      void loadWork();
    }
  }, [loadCapabilities, loadWork]);
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
    void Promise.allSettled([loadOrders(), loadApprovals()]).then(() => { if (alive.current) setReady(true); });
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
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        openCenter();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [openCenter]);
  const ask = useCallback((text: string) => openCenter(text), [openCenter]);

  const openApprovals = approvals.filter((a) => a.status === "WAITING_FOR_APPROVAL");
  const tz = calendar?.zeitzone || "Europe/Berlin";
  const today = dayKey(new Date(), tz);
  const runningJobs = (agentData?.jobs ?? []).filter((j) => isJob(j) && (j.status === "RUNNING" || j.status === "WAITING_FOR_APPROVAL"));
  const next = (calendar?.termine ?? []).find((e) => e.status !== "cancelled" && e.beginn && new Date(e.beginn).getTime() > Date.now());
  const calendarOk = calendar?.status === "ok";
  const webcams = (automations?.integrationen.find((i) => i.id === "windy")?.status ?? null) as "bereit" | "nicht_eingerichtet" | null;
  const needsYou = (connections?.verbindungen ?? []).filter((c) => ["abgelaufen", "unvollstaendig", "fehler"].includes(c.zustand));

  // „Heute“: eine priorisierte Liste aus allen Quellen; nicht erreichbare Quellen werden benannt statt verschwiegen.
  const queue = useMemo(() => buildQueue({
    approvals, orders: board.orders, today, calls, leads: dueLeads, content: contentData, calendar, automations, connections, agents: agentData,
  }), [approvals, board.orders, today, calls, dueLeads, contentData, calendar, automations, connections, agentData]);
  const nextEvent = next ? { titel: next.titel, zeit: `${dayKey(new Date(next.beginn!), tz) === today ? "heute" : new Date(next.beginn!).toLocaleDateString("de-DE", { timeZone: tz, weekday: "long" })} ${timeRange(next, tz).split("–")[0]}` } : null;
  const suggestions = useMemo(() => buildSuggestions({ orders: board.orders, today, calls, nextEvent }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [board.orders, today, calls, nextEvent?.titel, nextEvent?.zeit]);
  const unchecked = [
    offline ? "Aufträge" : null, !calls && callsError ? "Vertrieb" : null, !contentData && contentError ? "Content" : null,
    !agentData && agentError ? "Agenten" : null, !automations && automationError ? "Abläufe" : null, !connections && connError ? "Verbindungen" : null,
  ].filter((x): x is string => !!x);
  const openItem = (item: QueueItem) => {
    if (item.orderId) return void openOrder(item.orderId);
    if (item.jobId) return openJob(item.jobId);
    if (item.leadId) setFocusLead(item.leadId);
    go(item.target);
  };

  const badge = (id: CommandSection) =>
    id === "freigaben" && openApprovals.length > 0 ? <span className="cc-badge" aria-label={`${openApprovals.length} offen`}>{openApprovals.length}</span>
    : id === "agenten" && runningJobs.length > 0 ? <span className="cc-badge live" aria-label={`${runningJobs.length} aktiv`}>{runningJobs.length}</span>
    : id === "verbindungen" && needsYou.length > 0 ? <span className="cc-badge" aria-label={`${needsYou.length} brauchen dich`}>{needsYou.length}</span>
    : null;
  const isCurrent = (id: CommandSection) => !more && (section === id || (id === "kalender" && section === "termin"));
  const navButton = (s: NavItem) => (
    <button key={s.id} type="button" className={isCurrent(s.id) ? "active" : ""}
      aria-current={isCurrent(s.id) ? "page" : undefined} onClick={() => go(s.id)}>
      <Icon name={s.icon} /> {s.label}
      {badge(s.id)}
    </button>
  );
  const moreVisible = moreOpen || section === "welt" || !!more;
  const searchable = !more && ["auftraege", "kalender", "freigaben"].includes(section);
  const moreLabel = more ? MORE.find((m) => m.id === more.id)?.label ?? "Mehr" : null;
  const dark = !!more || section === "welt";

  return (
    <section className="cc" aria-label="Jarvis Arbeitsbereich" data-section={more ? more.id : section}>
      <nav className={`cc-nav ${navExpanded ? "open" : ""} ${navOpen ? "pinned" : ""}`} aria-label="Hauptnavigation"
        onPointerEnter={(e) => { if (e.pointerType === "mouse") setNavHover(true); }}
        onPointerLeave={(e) => { if (e.pointerType === "mouse") setNavHover(false); }}
        onFocus={(e) => { if ((e.target as HTMLElement).matches?.(":focus-visible")) setNavKeys(true); }}
        onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setNavKeys(false); }}
        onKeyDown={(e) => { if (e.key === "Escape" && navExpanded) { e.stopPropagation(); closeNav(); setNavHover(false); (document.activeElement as HTMLElement | null)?.blur?.(); } }}>
        <button type="button" className="cc-brand" aria-expanded={navExpanded} aria-controls="cc-nav-inhalt"
          aria-label={navExpanded ? "Jarvis – Navigation einklappen" : "Jarvis – Navigation ausklappen"}
          onPointerDown={(e) => { lastPointer.current = e.pointerType; }}
          onClick={() => {
            // Maus: das Überfahren öffnet und schließt bereits. Touch, Stift und Tastatur schalten um.
            if (lastPointer.current === "mouse") { lastPointer.current = ""; return; }
            lastPointer.current = "";
            if (navExpanded) closeNav(); else setNavOpen(true);
          }}>
          <span className="cc-logo" aria-hidden="true" /><span>Jarvis</span>
        </button>
        <div id="cc-nav-inhalt" className="cc-nav-body">
        {NAV.map((group) => (
          <div key={group.title} className="cc-nav-group" role="group" aria-label={group.title}>
            <div className="cc-nav-more">{group.title}</div>
            {group.items.map(navButton)}
          </div>
        ))}
        <div className="cc-nav-group" role="group" aria-label="Mehr">
          <button type="button" className="cc-nav-toggle" aria-expanded={moreVisible} onClick={() => setMoreOpen((v) => !v)}>
            <span>Mehr</span><span aria-hidden="true">{moreVisible ? "–" : "+"}</span>
          </button>
          {moreVisible && MORE.map((m) => (
            <button key={m.id} type="button" className={(m.id === "welt" ? !more && section === "welt" : more?.id === m.id) ? "active" : ""}
              aria-current={(m.id === "welt" ? !more && section === "welt" : more?.id === m.id) ? "page" : undefined}
              onClick={() => { if (m.id === "welt") go("welt"); else { closeNav(); onView(m.id); } }}><Icon name={m.icon} /> {m.label}</button>
          ))}
        </div>
        <a className="cc-open-cc" href={COMMAND_URL} target={COMMAND_WINDOW} rel="opener"
          onClick={(e) => { e.preventDefault(); closeNav(); openCenter(); }}>
          <span>Command Center <Icon name="external" /></span><small>eigenes Fenster</small>
        </a>
        <div className={`cc-nav-foot ${connected ? "ok" : "warn"}`} role="status">
          <i aria-hidden="true" /><span>{connected ? "Verbunden" : "Keine Verbindung"}</span>
        </div>
        </div>
      </nav>

      {navOpen && <button type="button" className="cc-backdrop" aria-label="Navigation schließen" onClick={closeNav} />}
      <div className="cc-main">
        <header className="cc-top">
          <button type="button" className="cc-menu-btn" aria-label={navOpen ? "Jarvis – Navigation schließen" : "Jarvis – Navigation öffnen"}
            aria-expanded={navOpen} aria-controls="cc-nav-inhalt" onClick={() => setNavOpen((v) => !v)}>
            <span className="cc-logo" aria-hidden="true" /><span>Jarvis</span>
          </button>
          <h1>{moreLabel ?? TITLES[section]}</h1>
          {searchable && (
            <input type="search" className="cc-search" placeholder="Suchen in Aufträgen, Terminen, Freigaben …" aria-label="Suchen"
              value={search} onChange={(e) => setSearch(e.target.value)} />
          )}
          {/* Status nur bei Abweichung: eine ruhige Kopfzeile heißt „alles in Ordnung“. */}
          <div className="cc-pills">
            {!connected && <span className="cc-pill warn">Getrennt</span>}
            {calendar && !calendarOk && calendar.status !== "demo" && <button type="button" className="cc-pill warn" onClick={() => go("kalender")}>Kalender nicht verfügbar</button>}
            {transcriptSave && (voiceMode === "echtzeit" || transcriptSave.ausstehend + transcriptSave.fehlgeschlagen > 0) && (
              transcriptSave.fehlgeschlagen > 0
                ? <button type="button" className="cc-pill warn" title="Äußerungen liegen sicher in diesem Browser und werden erneut übertragen." onClick={onRetryTranscripts}>Transkript: {transcriptSave.fehlgeschlagen} fehlgeschlagen – erneut senden</button>
                : <span className={`cc-pill ${transcriptSave.ausstehend ? "attn" : "ok"}`} title="Jede erkannte Äußerung wird sofort ins Gesprächsarchiv geschrieben (nur Text, kein Audio).">
                    Transkript: {transcriptSave.gespeichert} gespeichert{transcriptSave.ausstehend ? ` · ${transcriptSave.ausstehend} ausstehend` : ""}</span>
            )}
            {openApprovals.length > 0 && section !== "freigaben" && section !== "uebersicht" && (
              <button type="button" className="cc-pill attn" onClick={() => go("freigaben")}>{openApprovals.length} {openApprovals.length === 1 ? "Freigabe wartet" : "Freigaben warten"}</button>
            )}
            <button type="button" className="cc-pill cc-kbd-hint" onClick={() => openCenter()}
              aria-label="Command Center in eigenem Fenster öffnen">Command Center <kbd>⌘K</kbd></button>
          </div>
        </header>
        {offline && <div className="cc-error" role="status">{offline} Angezeigt wird der letzte bekannte Stand.</div>}
        {blocked && (
          <div className="cc-error" role="alert">Der Browser hat das Command-Center-Fenster blockiert.{" "}
            <a className="cc-link" href={COMMAND_URL} target={COMMAND_WINDOW} onClick={() => setBlocked(false)}>Hier klicken, um es zu öffnen</a>. Dein Auftrag wartet dort zwei Minuten.</div>
        )}
        {notice && (
          <div className="cc-notice" role="status">{notice} <button type="button" className="cc-link" onClick={() => setNotice(null)}>OK</button></div>
        )}

        <div className={`cc-work ${dark ? "flush lt-stage" : ""}`} key={more ? more.id : section}>
          {more ? more.node : <>
          {section === "uebersicht" && (
            <TodayView
              dateLabel={new Date().toLocaleDateString("de-DE", { timeZone: tz, weekday: "long", day: "numeric", month: "long", year: "numeric" })}
              queue={queue} ready={ready} unchecked={unchecked} suggestions={suggestions}
              agents={agentData} agentError={agentError} calendar={calendar} nextEvent={nextEvent}
              canAct={canEdit} connected={connected} onAsk={ask} onOpen={openItem} onOpenJob={openJob} onOpenEvent={openEvent}
              onTalk={() => openCenter()} />
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
              <CrmPanel canEdit={canEdit} focusLead={focusLead} onOpenApprovals={() => go("freigaben")}
                auswertung={<CallsPanel data={calls} error={callsError} canAsk={canEdit} onAsk={ask} days={callDays} onDays={(d) => { setCallDays(d); void loadCalls(d); }} onStartSkill={startSkill} />} />
            </>
          )}
          {section === "content" && (
            <>
              <div className="cc-page-head slim"><span className="cc-tag">Nichts wird automatisch veröffentlicht</span>
                <button type="button" className="cc-link" onClick={() => void loadContent()}>Aktualisieren</button></div>
              <ContentPanel data={contentData} error={contentError} canEdit={canEdit} onMove={moveContent} onAsk={ask} onStartSkill={startSkill} />
            </>
          )}
          {section === "agenten" && (
            <>
              <AgentCenter data={agentData} error={agentError} canAct={canEdit} focusJob={focusJob} busyId={capBusy}
                onCancel={(id) => void cancelJob(id)} onRetry={(id) => void capabilityAction(id, "wiederholen")}
                onApprovals={() => go("freigaben")} onOpenOrder={openOrder} onStartRole={startRole} onRefresh={() => void loadWork()} />
            </>
          )}
          {section === "automatisierungen" && (
            <>
              <SkillsOverview platform={platform} platformError={capError} automations={automations} automationError={automationError}
                canEdit={canEdit} busy={capBusy} onStartSkill={startSkill} onToggleSkill={(id, active) => void toggleSkill(id, active)}
                onRunAction={(id, action) => void capabilityAction(id, action)} onOpenApprovals={() => go("freigaben")} onOpenJobs={() => go("agenten")} />
              <details className="so-more so-tools">
                <summary>Bibliothek, GitHub, Integrationen, frühere Aufträge</summary>
                <SkillPlatform platform={platform} error={capError} canEdit={canEdit} busy={capBusy}
                  onAction={(id, action) => void capabilityAction(id, action)} onOpenOrder={openOrder}
                  onStart={startCapability} onToggleSkill={(id, active) => void toggleSkill(id, active)} onOpenApprovals={() => go("freigaben")} />
                <AutomationsPanel data={automations} error={automationError} />
              </details>
            </>
          )}
          {section === "gedaechtnis" && (
            <>
              <MemoryCenter canEdit={canEdit} />
            </>
          )}
          {section === "verbindungen" && (
            <>
              <ConnectionsPanel data={connections} error={connError} loading={connBusy} onRefresh={() => void loadConnections(true)} />
            </>
          )}
          {section === "welt" && (
            <Suspense fallback={<p className="cc-empty" style={{ padding: 24 }}>Karte lädt …</p>}>
              <MapboxWorld query={worldQuery} webcams={webcams} />
            </Suspense>
          )}
          {section === "gespraech" && (
            <div className="crm-start" role="region" aria-label="Command Center">
              <h2>Das Command Center hat ein eigenes Fenster</h2>
              <p>Sprache, Gespräch und Karte laufen dort.</p>
              <button type="button" className="cc-btn brand" onClick={() => openCenter()}>Command Center öffnen</button>
            </div>
          )}
          {section === "freigaben" && <ApprovalsPanel items={approvals} canDecide={canEdit} onDecide={decide} query={search} />}
          </>}
        </div>
      </div>
      <nav className="cc-tabbar" aria-label="Schnellnavigation">
        {TABS.map((t) => (
          <button key={t.id} type="button" className={isCurrent(t.id) ? "active" : ""} aria-current={isCurrent(t.id) ? "page" : undefined} onClick={() => go(t.id)}>
            <Icon name={t.icon} /><span>{t.id === "agenten" ? "Jobs" : t.label}</span>{badge(t.id)}
          </button>
        ))}
        <button type="button" aria-expanded={navOpen} onClick={() => setNavOpen(true)}><Icon name="menu" /><span>Menü</span></button>
      </nav>
      {camera && <LiveCamera camera={camera} onClose={() => setCamera(null)} />}
    </section>
  );
}
